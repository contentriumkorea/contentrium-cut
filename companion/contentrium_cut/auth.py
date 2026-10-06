"""Mutual local authentication. Legacy pairing is a trusted test adapter only."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time
from .contract import CutError
from .jobs import atomic_json

class _LegacyAuth:
    def __init__(self,root,clock=time.time):
        self.path=Path(root)/'paired-sessions.json';self.clock=clock or time.time;self.lock=threading.RLock()
        self.sessions={};self.code=None;self.code_expiry=0;self.failed=0
        if self.path.is_file():
            try:self.sessions=json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError,OSError):self.sessions={}
    def issue_code(self):
        with self.lock:
            self.code=f'{secrets.randbelow(1000000):06d}';self.code_expiry=self.clock()+120;self.failed=0
            return self.code
    def pair(self,code,instance):
        with self.lock:
            if not isinstance(instance,str) or not re.fullmatch(r'[A-Za-z0-9._-]{3,100}',instance):raise CutError('PAIRING_FAILED','Invalid panel identity')
            if self.failed>=5 or self.clock()>=self.code_expiry or self.code is None or not isinstance(code,str) or not hmac.compare_digest(code,self.code):
                self.failed+=1;raise CutError('PAIRING_FAILED','The connection code is invalid or expired.')
            self.code=None;token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest()
            self.sessions[digest]={'instanceId':instance,'projectRef':None,'issuedAt':self.clock()};atomic_json(self.path,self.sessions)
            return {'token':token,'instanceId':instance}
    def authenticate(self,token):
        if not isinstance(token,str) or not token or len(token)>256:raise CutError('AUTH_REQUIRED','Connect the local Companion first.')
        with self.lock:
            digest=hashlib.sha256(token.encode()).hexdigest()
            session=self.sessions.get(digest)
            if not session:raise CutError('AUTH_REQUIRED','Connect the local Companion first.')
            return dict(session)
    def bind_project(self,token,project):
        with self.lock:
            self.authenticate(token)
            if not isinstance(project,str) or not project:raise CutError('PROJECT_REQUIRED','A project identity is required.')
            self.sessions[hashlib.sha256(token.encode()).hexdigest()]['projectRef']=project;atomic_json(self.path,self.sessions)
    def require_project(self,token,project):
        if self.authenticate(token)['projectRef']!=project:raise CutError('PROJECT_SCOPE','This panel is not bound to that project.')
    def revoke(self,token):
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(),None);atomic_json(self.path,self.sessions)
    def validate_origin(self,origin):
        # UXP native fetch does not emit a web Origin. No browser origin is allowed.
        if origin is not None:raise CutError('ORIGIN_DENIED','Browser requests cannot control the Premiere plugin.')


BOOTSTRAP_FIELDS = {'schemaVersion', 'productId', 'installationId', 'keyId', 'authProtocol', 'endpoint', 'secret'}
CHALLENGE_FIELDS = {'installationId', 'keyId', 'instanceId', 'contextId', 'clientNonce', 'appVersion', 'bundleId', 'protocolVersion'}


def validate_bootstrap(value):
    try:
        if (not isinstance(value, dict) or set(value) != BOOTSTRAP_FIELDS or
                type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or
                value['productId'] != 'com.contentrium.cut' or type(value['authProtocol']) is not int or
                value['authProtocol'] != 1 or value['endpoint'] != 'http://127.0.0.1:41737'):
            raise ValueError()
        for key in ('installationId', 'keyId'):
            if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,100}', value[key]): raise ValueError()
        key = base64.b64decode(value['secret'], validate=True)
        if len(key) != 32 or base64.b64encode(key).decode() != value['secret']: raise ValueError()
        return dict(value), key
    except (ValueError, TypeError, KeyError):
        raise CutError('BOOTSTRAP_INVALID', 'Private installation bootstrap is invalid.') from None


def canonical(value):
    # Only ordered arrays of bounded ASCII protocol fields enter transcripts.
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def mac(key, domain, value):
    return hmac.new(key, (domain + '\n' + value).encode('utf-8'), hashlib.sha256).hexdigest()


class AuthManager(_LegacyAuth):
    def __init__(self, root, clock=time.time, *, bootstrap=None, config=None):
        self.private = bootstrap is not None
        self.config = dict(config or {})
        if not self.private:
            super().__init__(root, clock)
            return
        self.bootstrap, self.key = validate_bootstrap(bootstrap)
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.path = Path(root) / 'private' / 'auth-bootstrap.json'
        self.boot_id = secrets.token_hex(16)
        self.sessions, self.challenges = {}, {}
        self.contexts = {}
        self.context_busy = lambda owner: False
        self.failed_proofs = []

    def issue_code(self):
        if self.private: raise CutError('AUTH_REQUIRED', 'Private installation authentication is required.')
        return super().issue_code()

    def pair(self, code, instance):
        if self.private: raise CutError('AUTH_REQUIRED', 'Private installation authentication is required.')
        return super().pair(code, instance)

    def challenge(self, body):
        if not self.private: raise CutError('BOOTSTRAP_MISSING', 'Private installation bootstrap is required.')
        with self.lock:
            now = self.clock()
            self.challenges = {k: v for k, v in self.challenges.items() if v['response']['expiresAt'] > now}
            self.failed_proofs = [stamp for stamp in self.failed_proofs if stamp > now - 30]
            if len(self.challenges) >= 64 or len(self.failed_proofs) >= 8:
                raise CutError('AUTH_RATE_LIMIT', 'Authentication is temporarily unavailable.')
            if (not isinstance(body, dict) or set(body) != CHALLENGE_FIELDS or
                    body.get('installationId') != self.bootstrap['installationId'] or body.get('keyId') != self.bootstrap['keyId'] or
                    type(body.get('protocolVersion')) is not int or body['protocolVersion'] != 1):
                raise CutError('AUTH_REQUIRED', 'Installation components do not match.')
            for field in ('instanceId', 'appVersion', 'bundleId'):
                if not isinstance(body[field], str) or not re.fullmatch(r'[A-Za-z0-9._-]{3,100}', body[field]):
                    raise CutError('AUTH_REQUIRED', 'Authentication metadata is invalid.')
            if not isinstance(body['clientNonce'], str) or not re.fullmatch('[0-9a-f]{64}', body['clientNonce']):
                raise CutError('AUTH_REQUIRED', 'Authentication nonce is invalid.')
            if not isinstance(body['contextId'],str) or not re.fullmatch('[0-9a-f]{32}',body['contextId']):
                raise CutError('AUTH_REQUIRED','Authentication context is invalid.')
            for field in ['appVersion','bundleId']:
                if not isinstance(self.config.get(field),str) or not re.fullmatch(r'[A-Za-z0-9._-]{3,100}',self.config[field]):
                    raise CutError('AUTH_REQUIRED','Server component identity is invalid.')
            response = dict(challengeId=secrets.token_hex(16), serverNonce=secrets.token_hex(32),
                            serverBootId=self.boot_id, expiresAt=int(now) + 30,
                            serverAppVersion=self.config['appVersion'],serverBundleId=self.config['bundleId'])
            text = canonical([body[k] for k in ['installationId', 'keyId', 'instanceId', 'contextId', 'clientNonce', 'appVersion', 'bundleId', 'protocolVersion']] +
                             [response[k] for k in ['challengeId', 'serverNonce', 'serverBootId', 'expiresAt','serverAppVersion','serverBundleId']])
            response['serverProof'] = mac(self.key, 'CUT-SERVER-AUTH-1', text)
            self.challenges[response['challengeId']] = dict(request=dict(body), response=response, transcript=text)
            return dict(response)

    def establish_session(self, body):
        if not self.private: raise CutError('BOOTSTRAP_MISSING', 'Private installation bootstrap is required.')
        with self.lock:
            if not isinstance(body, dict) or set(body) != {'challengeId', 'clientProof'}:
                raise CutError('AUTH_REQUIRED', 'Authentication proof is invalid.')
            challenge = self.challenges.pop(body.get('challengeId'), None) if isinstance(body.get('challengeId'), str) else None
            if (not challenge or challenge['response']['expiresAt'] <= self.clock() or
                    not isinstance(body['clientProof'], str) or not re.fullmatch('[0-9a-f]{64}', body['clientProof']) or
                    not hmac.compare_digest(body['clientProof'], mac(self.key, 'CUT-PANEL-AUTH-1', challenge['transcript']))):
                self.failed_proofs.append(self.clock())
                self.failed_proofs = self.failed_proofs[-64:]
                raise CutError('AUTH_REQUIRED', 'Authentication proof is invalid or expired.')
            self.sessions = {k: v for k, v in self.sessions.items() if v['expiresAt'] > self.clock() or v['pending']}
            active_owners={value['ownerId'] for value in self.sessions.values()}
            self.contexts={owner:value for owner,value in self.contexts.items() if owner in active_owners or self.context_busy(owner)}
            if len(self.sessions) >= 64: raise CutError('AUTH_RATE_LIMIT', 'Authentication is temporarily unavailable.')
            session_id = secrets.token_hex(32)
            text = challenge['transcript'] + '\n' + session_id
            owner = mac(self.key, 'CUT-OWNER-1', canonical([self.bootstrap['installationId'], challenge['request']['instanceId']]))
            context = self.contexts.get(owner)
            if context is None and len(self.contexts)>=64:
                raise CutError('AUTH_RATE_LIMIT','Authentication is temporarily unavailable.')
            context_id = challenge['request']['contextId']
            if context and context['contextId'] != context_id and (context['touchedAt'] > self.clock()-30 or
                    any(value['ownerId']==owner and value['pending'] for value in self.sessions.values()) or self.context_busy(owner)):
                raise CutError('PANEL_CONTEXT_CONFLICT','Another panel context owns this connection.')
            self.contexts[owner] = dict(contextId=context_id,touchedAt=self.clock(),sessionId=session_id)
            diagnostic = (challenge['request']['appVersion'] != self.config['appVersion'] or
                          challenge['request']['bundleId'] != self.config['bundleId'])
            response = dict(sessionId=session_id, expiresAt=int(self.clock()) + 1800, serverBootId=self.boot_id,
                            instanceId=challenge['request']['instanceId'],diagnosticOnly=diagnostic)
            self.sessions[session_id] = dict(challenge['request'], ownerId=owner, projectRef=None, counter=0,
                                            serverBootId=self.boot_id, expiresAt=response['expiresAt'],pending=0,diagnosticOnly=diagnostic,
                                            requestKey=bytes.fromhex(mac(self.key, 'CUT-REQUEST-KEY-1', text)),
                                            responseKey=bytes.fromhex(mac(self.key, 'CUT-RESPONSE-KEY-1', text)))
            response['sessionProof'] = mac(self.key, 'CUT-SESSION-1', canonical([response[k] for k in ['sessionId', 'expiresAt', 'serverBootId', 'instanceId','diagnosticOnly']]) + '\n' + challenge['transcript'])
            return response

    def authenticate(self, token):
        if not self.private: return super().authenticate(token)
        with self.lock:
            if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token) or token not in self.sessions:
                raise CutError('AUTH_REQUIRED', 'Private authentication is required.')
            value = self.sessions[token]
            if value['expiresAt'] <= self.clock(): raise CutError('SESSION_EXPIRED', 'Private authentication has expired.')
            if self.contexts[value['ownerId']]['contextId'] != value['contextId']:
                raise CutError('PANEL_CONTEXT_CONFLICT','Another panel context owns this connection.')
            return {k: value[k] for k in ['instanceId', 'contextId', 'projectRef', 'ownerId', 'appVersion', 'bundleId', 'protocolVersion', 'serverBootId','diagnosticOnly']}

    def owner_id(self, token):
        if not self.private:
            self.authenticate(token)
            return hashlib.sha256(token.encode()).hexdigest()
        return self.authenticate(token)['ownerId']

    def activation_session(self, owner, generation):
        """Only the newest live authenticated transport may supply activation evidence."""
        if not self.private: return None
        with self.lock:
            token = self.contexts.get(owner, {}).get('sessionId')
            if not token or hashlib.sha256(token.encode()).hexdigest() != generation: return None
            try: return self.authenticate(token)
            except CutError: return None

    def bind_project(self, token, project):
        if not self.private: return super().bind_project(token, project)
        with self.lock:
            self.authenticate(token)
            if not isinstance(project, str) or not project: raise CutError('PROJECT_REQUIRED', 'A project identity is required.')
            self.sessions[token]['projectRef'] = project

    def revoke(self, token):
        if not self.private: return super().revoke(token)
        with self.lock: self.sessions.pop(token, None)

    def verify_request(self, session_id, counter, method, path, raw, supplied_mac):
        if not self.private: raise CutError('AUTH_REQUIRED', 'Private authentication is required.')
        with self.lock:
            self.authenticate(session_id)
            value = self.sessions[session_id]
            if type(counter) is not int or counter != value['counter'] + 1 or counter > 9007199254740991:
                raise CutError('AUTH_REQUIRED', 'Request sequence is invalid.')
            text = canonical([session_id, counter, method, path, hashlib.sha256(raw).hexdigest()])
            if not isinstance(supplied_mac, str) or not re.fullmatch('[0-9a-f]{64}', supplied_mac) or not hmac.compare_digest(supplied_mac, mac(value['requestKey'], 'CUT-REQUEST-1', text)):
                raise CutError('AUTH_REQUIRED', 'Request authentication is invalid.')
            value['counter'] = counter
            value['pending'] += 1
            self.contexts[value['ownerId']]['touchedAt'] = self.clock()

    def sign_response(self, session_id, counter, status, body):
        with self.lock:
            value = self.sessions[session_id]
            # The wire body is a JSON string: MAC hashes exactly these UTF-8 bytes.
            raw = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            text = canonical([session_id, counter, status, hashlib.sha256(raw.encode()).hexdigest()])
            response = dict(sessionId=session_id, counter=counter, status=status, body=raw,
                            responseMac=mac(value['responseKey'], 'CUT-RESPONSE-1', text))
            value['pending'] = max(0,value['pending']-1)
            self.contexts[value['ownerId']]['touchedAt'] = self.clock()
            return response
