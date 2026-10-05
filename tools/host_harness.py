"""Development-only fixed fixture service. Never shipped in the installer."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'docs' / 'qa' / 'private'
MEDIA = PRIVATE / 'fixture'

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/fixture','/linked-fixture'):
            self.send_error(404); return
        payload = {'paths': [str(MEDIA / name) for name in ['CA.mp4', 'CB.mp4', 'CC.mp4', 'audio.wav']],
                   'projectPath': str(PRIVATE / 'Contentrium CUT integration.prproj'),'exportPath':str(PRIVATE)}
        if self.path=='/linked-fixture':
            payload['paths']=[str(MEDIA / name) for name in ['CA-linked.mp4','CB-linked.mp4','CC-linked.mp4','audio.wav']]
            payload['projectPath']=str(PRIVATE/'Contentrium CUT linked integration.prproj')
        body = json.dumps(payload).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if self.path != '/diagnostics':
            self.send_error(404); return
        body = self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 4_000_000))
        value = json.loads(body)
        PRIVATE.mkdir(parents=True, exist_ok=True)
        with (PRIVATE / 'host-events.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(value, ensure_ascii=False) + '\n')
        print(json.dumps(value, ensure_ascii=True), flush=True)
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 41737), Handler).serve_forever()
