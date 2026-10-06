"""One bounded authenticated metadata lookup; credentials never leave memory."""
import json
import re
import time
from urllib.request import Request,HTTPRedirectHandler,build_opener
from .contract import CutError


def validate_access(token,terms):
    if terms is not True or not isinstance(token,str) or not 1<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):
        raise CutError('MODEL_NOT_READY','Provider access and explicit acceptance are required.')


def community_revision(token):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    try:
        url='https://huggingface.co/api/models/pyannote/speaker-diarization-community-1/revision/main'
        request=Request(url,headers={'Authorization':'Bearer '+token,'User-Agent':'Contentrium-CUT','Accept':'application/json'})
        deadline=time.monotonic()+15
        with build_opener(NoRedirect()).open(request,timeout=5) as response:
            if response.status!=200 or response.geturl()!=url:raise ValueError()
            raw=bytearray()
            while True:
                block=response.read(8192)
                if time.monotonic()>deadline:raise ValueError()
                if not block:break
                raw.extend(block)
                if len(raw)>512*1024:raise ValueError()
        revision=json.loads(raw)['sha']
        if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError()
        return revision
    except Exception:
        raise CutError('MODEL_NOT_READY','Provider metadata could not be verified.') from None
