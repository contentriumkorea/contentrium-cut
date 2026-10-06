"""Signed test-only continuation responses; no HTTP listener or native worker."""
import json
import sys
import tempfile
from contentrium_cut.service import CutService
from contentrium_cut.contract import CutError
from test_private_auth import BOOTSTRAP, CONFIG

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
case = sys.argv[1]
identity = 'c' * 32
state = dict(polls=0, taken=0, canceled=False, released=case != 'hold')
with tempfile.TemporaryDirectory(prefix='cut-continuation-wire-') as directory:
    service = CutService(directory, dict(CONFIG, privateBootstrap=BOOTSTRAP))
    original = service._control

    def control(method, path, token, body):
        if path == '/fixture/work':
            value = dict(id=identity, status='pending', pollAfterMs=50, expiresInMs=3000, epoch=7)
            if case == 'malformed':
                value['id'] = '../another-operation'
            return dict(continuation=value)
        if path == '/fixture/heartbeat':
            state['released'] = True
            return dict(awake=True)
        if path == '/continuations/' + identity:
            state['polls'] += 1
            status = ('canceled' if state['canceled'] else 'consumed' if state['taken'] else
                      'failed' if case == 'failed' else 'ready' if state['released'] else 'pending')
            value = dict(id=identity, status=status)
            if status == 'failed':
                value['error'] = dict(code='SOURCE_CHANGED')
            return value
        if path == '/continuations/' + identity + '/take':
            if method != 'POST' or body != {'epoch': 7}:
                raise CutError('INVALID_REQUEST', 'Fixture take must use its captured epoch.')
            state['taken'] += 1
            return dict(execute=state['taken'] == 1, restored=True, count=state['taken'])
        if path == '/continuations/' + identity + '/cancel':
            state['canceled'] = True
            return dict(canceled=True)
        return original(method, path, token, body)

    service._control = control
    try:
        for line in sys.stdin:
            request = json.loads(line)
            status, body = service.dispatch_authenticated(request['method'], request['path'], request['headers'], request['body'].encode())
            print(json.dumps(dict(status=status, body=body)), flush=True)
    finally:
        service.close()
