import base64
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest

from contentrium_cut.auth import AuthManager
from contentrium_cut.contract import CutError


BOOTSTRAP = dict(schemaVersion=1, productId='com.contentrium.cut', installationId='install-test',
                 keyId='key-test', authProtocol=1, endpoint='http://127.0.0.1:41737',
                 secret=base64.b64encode(bytes(range(32))).decode())
CONFIG = dict(appVersion='0.1.0', bundleId='bundle-test', protocolVersion=1)


def transcript(request, response):
    return json.dumps([request[k] for k in ['installationId', 'keyId', 'instanceId', 'contextId', 'clientNonce',
                                          'appVersion', 'bundleId', 'protocolVersion']] +
                      [response[k] for k in ['challengeId', 'serverNonce', 'serverBootId', 'expiresAt','serverAppVersion','serverBundleId']],
                      separators=(',', ':'), ensure_ascii=True)


def proof(label, value, key=bytes(range(32))):
    return hmac.new(key, (label + '\n' + value).encode(), hashlib.sha256).hexdigest()


class PrivateAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [100]
        self.auth = AuthManager(self.tmp.name, bootstrap=BOOTSTRAP, config=CONFIG, clock=lambda: self.now[0])
        self.request = dict(installationId='install-test', keyId='key-test', instanceId='panel-test', contextId='cd'*16,
                            clientNonce='ab' * 32, appVersion='0.1.0', bundleId='bundle-test', protocolVersion=1)

    def session(self):
        challenge = self.auth.challenge(self.request)
        text = transcript(self.request, challenge)
        self.assertEqual(challenge['serverProof'], proof('CUT-SERVER-AUTH-1', text))
        response = self.auth.establish_session(dict(challengeId=challenge['challengeId'], clientProof=proof('CUT-PANEL-AUTH-1', text)))
        return response, text

    def test_one_use_challenge_and_stable_owner_without_persisted_keys(self):
        first, text = self.session()
        with self.assertRaises(CutError):
            self.auth.establish_session(dict(challengeId=json.loads(text)[8], clientProof=proof('CUT-PANEL-AUTH-1', text)))
        second, _ = self.session()
        self.assertEqual(self.auth.owner_id(first['sessionId']), self.auth.owner_id(second['sessionId']))
        self.assertNotEqual(first['sessionId'], second['sessionId'])
        self.assertFalse(list(Path(self.tmp.name).rglob('*.json')))
        self.assertNotIn('requestKey', self.auth.authenticate(first['sessionId']))
        with self.assertRaises(CutError): self.auth.issue_code()

    def test_expiry_wrong_metadata_reflection_and_altered_request_fail(self):
        for field, value in [('protocolVersion', 2), ('keyId', 'rogue'), ('extra', 1)]:
            with self.assertRaises(CutError): self.auth.challenge(dict(self.request, **{field: value}))
        challenge = self.auth.challenge(self.request)
        self.now[0] += 31
        with self.assertRaises(CutError): self.auth.establish_session(dict(challengeId=challenge['challengeId'], clientProof=challenge['serverProof']))
        self.now[0] = 100
        response, text = self.session()
        session = response['sessionId']
        request_key = bytes.fromhex(proof('CUT-REQUEST-KEY-1', text + '\n' + session))
        raw = b'{"epoch":0}'
        mac = proof('CUT-REQUEST-1', json.dumps([session, 1, 'POST', '/heartbeat', hashlib.sha256(raw).hexdigest()], separators=(',', ':')), request_key)
        with self.assertRaises(CutError): self.auth.verify_request(session, 1, 'POST', '/heartbeat', b'{"epoch":1}', mac)
        self.auth.verify_request(session, 1, 'POST', '/heartbeat', raw, mac)
        with self.assertRaises(CutError): self.auth.verify_request(session, 1, 'POST', '/heartbeat', raw, mac)
        signed = self.auth.sign_response(session, 1, 409, {'error': {'code': 'UPDATE_IN_PROGRESS'}})
        self.assertEqual(signed['counter'], 1)
        self.assertEqual(signed['status'], 409)
        self.assertEqual(json.loads(signed['body'])['error']['code'], 'UPDATE_IN_PROGRESS')

    def test_failed_proof_consumes_challenge_and_limits_pending_challenges(self):
        challenge = self.auth.challenge(self.request)
        with self.assertRaises(CutError): self.auth.establish_session(dict(challengeId=challenge['challengeId'], clientProof=challenge['serverProof']))
        with self.assertRaises(CutError): self.auth.establish_session(dict(challengeId=challenge['challengeId'], clientProof=proof('CUT-PANEL-AUTH-1', transcript(self.request, challenge))))
        for _ in range(64):
            try: self.auth.challenge(self.request)
            except CutError: break
        with self.assertRaises(CutError): self.auth.challenge(self.request)

    def test_simultaneous_context_rejected_and_displaced_context_cannot_reclaim(self):
        first, _ = self.session()
        self.request['contextId'] = 'ef'*16
        with self.assertRaises(CutError) as error: self.session()
        self.assertEqual(error.exception.code, 'PANEL_CONTEXT_CONFLICT')
        self.now[0] += 31
        second, _ = self.session()
        self.assertEqual(self.auth.owner_id(second['sessionId']),proof('CUT-OWNER-1',json.dumps(['install-test','panel-test'],separators=(',',':'))))
        with self.assertRaises(CutError) as error:self.auth.authenticate(first['sessionId'])
        self.assertEqual(error.exception.code,'PANEL_CONTEXT_CONFLICT')
        self.request['contextId']='cd'*16
        with self.assertRaises(CutError):self.session()

    def test_active_apply_blocks_context_replacement_even_after_lease_expiry(self):
        self.session(); self.auth.context_busy=lambda owner:True
        self.now[0]+=31;self.request['contextId']='ef'*16
        with self.assertRaises(CutError):self.session()

    def test_session_keys_do_not_survive_boot_and_expired_session_rejects(self):
        session,_=self.session()
        restarted=AuthManager(self.tmp.name,bootstrap=BOOTSTRAP,config=CONFIG,clock=lambda:self.now[0])
        with self.assertRaises(CutError):restarted.authenticate(session['sessionId'])
        self.now[0]+=1801
        with self.assertRaises(CutError) as error:self.auth.authenticate(session['sessionId'])
        self.assertEqual(error.exception.code,'SESSION_EXPIRED')


if __name__ == '__main__': unittest.main()
