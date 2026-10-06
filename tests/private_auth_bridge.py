"""Isolated JSON-lines peer for JS/Python protocol interoperability tests."""
import json
import sys
import tempfile
from contentrium_cut.service import CutService
from test_private_auth import BOOTSTRAP, CONFIG

with tempfile.TemporaryDirectory() as directory:
    service = CutService(directory, dict(CONFIG, privateBootstrap=BOOTSTRAP))
    try:
        for line in sys.stdin:
            request = json.loads(line)
            status, body = service.dispatch_authenticated(request['method'], request['path'], request['headers'], request['body'].encode())
            print(json.dumps(dict(status=status, body=body)), flush=True)
    finally: service.close()
