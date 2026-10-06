"""Unshipped local server for owned native QA; no authentication bypass in product."""
import json,os
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];PRIVATE=ROOT/'docs/qa/private';MEDIA=PRIVATE/'fixture'
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path!='/fixture':self.send_error(404);return
        value={'paths':[str(MEDIA/name) for name in ['QA60-A.mp4','QA60-B.mp4','QA60-C.mp4','QA60.wav']],'projectPath':str(PRIVATE/'Contentrium CUT 60min QA.prproj'),'exportPath':str(PRIVATE)}
        body=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path!='/proof':self.send_error(404);return
        length=int(self.headers.get('Content-Length',0))
        if length>8000000:self.send_error(413);return
        value=json.loads(self.rfile.read(length));PRIVATE.mkdir(parents=True,exist_ok=True)
        with (PRIVATE/'native-round2-events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
        print(json.dumps({'event':value.get('event'),'error':value.get('error')}),flush=True)
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',41738),Handler).serve_forever()
