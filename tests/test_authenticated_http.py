import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from contentrium_cut.service import CutService
from contentrium_cut.contract import CutError
from test_private_auth import BOOTSTRAP, CONFIG, transcript, proof


class AuthenticatedHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.service = CutService(self.tmp.name, dict(CONFIG, privateBootstrap=BOOTSTRAP))
        self.addCleanup(self.service.close)

    def test_public_health_missing_bootstrap_and_bearer_cannot_authenticate_http(self):
        headers = {'Host': '127.0.0.1:41737', 'Content-Type': 'application/json'}
        status, body = self.service.dispatch_authenticated('GET', '/health', headers)
        self.assertEqual(status, 200); self.assertEqual(set(body), {'appVersion', 'protocolVersion'})
        status, _ = self.service.dispatch_authenticated('POST', '/pair', headers, b'{}')
        self.assertNotEqual(status, 200)
        status, _ = self.service.dispatch_authenticated('GET', '/state', dict(headers, Authorization='Bearer abc'))
        self.assertEqual(status, 401)

    def test_production_handler_signs_rejects_replay_and_does_not_allow_second_bind(self):
        request = dict(installationId='install-test', keyId='key-test', instanceId='panel-test', contextId='cd'*16,clientNonce='ab'*32,
                       appVersion='0.1.0', bundleId='bundle-test', protocolVersion=1)
        headers = {'Host': '127.0.0.1:41737', 'Content-Type': 'application/json'}
        _, challenge = self.service.dispatch_authenticated('POST', '/auth/challenge', headers, json.dumps(request).encode())
        text = transcript(request, challenge)
        _, session = self.service.dispatch_authenticated('POST', '/auth/session', headers, json.dumps(dict(challengeId=challenge['challengeId'], clientProof=proof('CUT-PANEL-AUTH-1', text))).encode())
        sid = session['sessionId']; key = bytes.fromhex(proof('CUT-REQUEST-KEY-1', text+'\n'+sid))
        raw = b''
        headers.update({'X-Cut-Session': sid, 'X-Cut-Counter': '1', 'X-Cut-Mac': proof('CUT-REQUEST-1', json.dumps([sid,1,'GET','/state',hashlib.sha256(raw).hexdigest()],separators=(',',':')), key)})
        server = self.service.http_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2); self.addCleanup(conn.close)
        conn.request('GET', '/state', headers=headers)
        response = conn.getresponse(); body = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(body['counter'], 1)
        self.assertEqual(json.loads(body['body'])['bundleId'], 'bundle-test')
        conn.request('GET', '/state', headers=headers)
        response = conn.getresponse(); response.read(); self.assertEqual(response.status, 401)
        with self.assertRaises(OSError): self.service.http_server(port=server.server_port)

    def test_original_host_exit_allows_diagnostic_takeover_with_new_host_present(self):
        self.service.applies['uncertain']={'owner':'logical-owner','active':True,'hostIdentity':{'pid':17,'createTime':'123','imagePath':'C:/old/Adobe Premiere Pro.exe'}}
        self.service.installation=type('Installed',(),{'host_exited':lambda _:False,'host_identity_exited':lambda _,identity:identity['pid']==17})()
        self.assertFalse(self.service._context_busy('logical-owner'))
        self.service.installation.host_identity_exited=lambda identity:False
        self.assertTrue(self.service._context_busy('logical-owner'))


class ActivationHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.host = dict(pid=17, createTime='123', alive=True, processName='Adobe Premiere Pro.exe')
        self.service = CutService(self.tmp.name, dict(CONFIG, privateBootstrap=BOOTSTRAP),
                                 process_probe=lambda instance, claim: dict(self.host))
        self.addCleanup(self.service.close)
        self.service.closed.set(); self.service.manager.join(2)
        self.activations = []
        self.service.updater = type('Updater', (), {
            'gate_open': False,
            '_checking': threading.Lock(), '_operation': threading.Lock(),
            'state': lambda _: dict(updateState='PENDING_ACTIVATION', newVersion=CONFIG['appVersion'],
                                    bundleId=CONFIG['bundleId'], updateEpoch=0),
            'activate': lambda _, receipt: self.activations.append(receipt)})()
        self.headers = {'Host': '127.0.0.1:41737', 'Content-Type': 'application/json'}

    def session(self, *, version=CONFIG['appVersion'], bundle=CONFIG['bundleId'], context='cd'*16):
        request = dict(installationId='install-test', keyId='key-test', instanceId='panel-test',
                       contextId=context, clientNonce='ab'*32, appVersion=version, bundleId=bundle, protocolVersion=1)
        status, challenge = self.service.dispatch_authenticated('POST', '/auth/challenge', self.headers, json.dumps(request).encode())
        self.assertEqual(status, 200, challenge)
        text = transcript(request, challenge)
        status, response = self.service.dispatch_authenticated('POST', '/auth/session', self.headers,
            json.dumps(dict(challengeId=challenge['challengeId'], clientProof=proof('CUT-PANEL-AUTH-1', text))).encode())
        self.assertEqual(status, 200, response)
        sid = response['sessionId']
        return dict(id=sid, key=bytes.fromhex(proof('CUT-REQUEST-KEY-1', text+'\n'+sid)), counter=0)

    def heartbeat(self, session, *, version=CONFIG['appVersion'], bundle=CONFIG['bundleId']):
        body = dict(hostIdentity=self.host, epoch=0, batchRunning=False, quiescent=True, appVersion=version, bundleId=bundle, protocolVersion=1)
        return self.signed_request(session, 'POST', '/heartbeat', body)

    def signed_request(self, session, method, path, body=None):
        raw = json.dumps(body).encode() if body is not None else b''
        session['counter'] += 1
        headers = dict(self.headers, **{'X-Cut-Session': session['id'], 'X-Cut-Counter': str(session['counter']),
            'X-Cut-Mac': proof('CUT-REQUEST-1', json.dumps([session['id'], session['counter'], method, path,
                hashlib.sha256(raw).hexdigest()], separators=(',', ':')), session['key'])})
        status, response = self.service.dispatch_authenticated(method, path, headers, raw)
        return status, json.loads(response['body']) if 'body' in response else response

    def assert_activation_denied(self):
        with self.assertRaises(CutError) as error: self.service.finalize_activation()
        self.assertEqual(error.exception.code, 'UPDATE_ACTIVATION')
        self.assertFalse(self.activations)

    def test_diagnostic_heartbeat_cannot_spoof_current_components_for_activation(self):
        session = self.session(version='0.0.9', bundle='old-bundle')
        status, body = self.heartbeat(session)
        self.assertEqual(status, 409, body)
        self.assertEqual(body['error']['code'], 'COMPONENT_MISMATCH')
        self.assert_activation_denied()
        # Truthful diagnostic heartbeat remains available, but grants no activation.
        self.assertEqual(self.heartbeat(session, version='0.0.9', bundle='old-bundle')[0], 200)
        self.assert_activation_denied()
        self.assertFalse(self.service.activation_heartbeat(CONFIG['appVersion'], CONFIG['bundleId']))

    def test_matching_authenticated_heartbeat_qualifies_for_native_activation(self):
        self.assertEqual(self.heartbeat(self.session())[0], 200)
        self.service.finalize_activation()
        self.assertEqual(len(self.activations), 1)
        self.assertEqual(self.activations[0]['panelVersion'], CONFIG['appVersion'])
        self.assertTrue(self.service.activation_heartbeat(CONFIG['appVersion'], CONFIG['bundleId']))

    def test_reconnect_invalidates_previous_heartbeat_before_first_new_request(self):
        previous = self.session(); self.assertEqual(self.heartbeat(previous)[0], 200)
        current = self.session()
        self.assert_activation_denied()
        self.assertFalse(self.service.activation_heartbeat(CONFIG['appVersion'], CONFIG['bundleId']))
        # An old session still belonging to the same context cannot restore its generation.
        self.assertEqual(self.heartbeat(previous)[0], 200)
        self.assert_activation_denied()
        self.assertEqual(self.heartbeat(current)[0], 200)
        self.service.finalize_activation(); self.assertEqual(len(self.activations), 1)

    def test_displaced_or_expired_session_cannot_supply_activation_evidence(self):
        previous = self.session(); self.assertEqual(self.heartbeat(previous)[0], 200)
        owner = self.service.auth.owner_id(previous['id'])
        self.service.auth.contexts[owner]['touchedAt'] = 0
        current = self.session(context='ef'*16)
        self.assert_activation_denied()
        self.assertEqual(self.heartbeat(current)[0], 200)
        self.service.auth.sessions[current['id']]['expiresAt'] = 0
        self.assert_activation_denied()
        self.assertFalse(self.service.activation_heartbeat(CONFIG['appVersion'], CONFIG['bundleId']))

    def test_reconnect_state_cannot_promote_inflight_old_heartbeat_generation(self):
        previous = self.session()
        entered = threading.Event(); release = threading.Event(); results = []
        worker = threading.Thread(target=lambda: results.append(self.heartbeat(previous)), daemon=True)
        def probe(instance, claim):
            if threading.current_thread() is worker:
                entered.set()
                if not release.wait(3): raise RuntimeError('Fixture host barrier timed out.')
            return dict(self.host)
        self.service.process_probe = probe
        worker.start()
        try:
            self.assertTrue(entered.wait(2), 'Old heartbeat did not enter the native host barrier.')
            current = self.session()
            self.assertEqual(self.signed_request(current, 'GET', '/state')[0], 200)
            before = self.service.activation_heartbeat(CONFIG['appVersion'], CONFIG['bundleId'])
            self.assertFalse(before)
            self.assert_activation_denied()
            release.set(); worker.join(2)
            self.assertFalse(worker.is_alive(), 'Old heartbeat did not leave the barrier.')
            self.assertEqual(len(results), 1)
            after = self.service.activation_heartbeat(CONFIG['appVersion'], CONFIG['bundleId'])
            denied = False
            try: self.service.finalize_activation()
            except CutError as error:
                self.assertEqual(error.code, 'UPDATE_ACTIVATION'); denied = True
            evidence = dict(oldHeartbeatStatus=results[0][0], qualifiedBeforeOldReturns=before,
                            qualifiedAfterOldReturns=after, activationCalls=len(self.activations))
            self.assertFalse(after, evidence)
            self.assertTrue(denied, evidence)
            self.assertFalse(self.activations, evidence)
            self.assertEqual(results[0][0], 401)
            self.assertEqual(results[0][1]['error']['code'], 'SESSION_EXPIRED')
            self.assertEqual(self.heartbeat(current)[0], 200)
            self.assertTrue(self.service.activation_heartbeat(CONFIG['appVersion'], CONFIG['bundleId']))
            self.service.finalize_activation(); self.assertEqual(len(self.activations), 1)
        finally:
            release.set(); worker.join(3)


if __name__ == '__main__': unittest.main()
