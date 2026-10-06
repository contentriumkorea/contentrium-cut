"""Cancelable media identity for immutable local listening examples."""
import hashlib
from pathlib import Path
from .models import check_cancel
from .contract import CutError

def media_digest(path,cancel=None):
    digest=hashlib.sha256()
    try:
        with Path(path).open('rb') as source:
            while True:
                check_cancel(cancel);block=source.read(1024*1024)
                if not block:break
                digest.update(block)
    except OSError:raise CutError('MISSING_AUDIO','Source media is unavailable.')
    return digest.hexdigest()

def require_media_identity(source,cancel=None):
    if not source.get('sha256') or media_digest(source['path'],cancel)!=source['sha256']:
        raise CutError('SOURCE_CHANGED','Source media changed after the immutable analysis.')
