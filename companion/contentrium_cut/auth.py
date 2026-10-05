"""User-paired loopback credentials; only hashes are persisted by the service."""
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

class AuthManager:
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
