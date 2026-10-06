"""Read-only visual QA server. Does not load the host adapter or local service."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    '/style.css': ROOT / 'plugin/style.css',
    '/view.js': ROOT / 'plugin/view.js',
    '/preview.js': ROOT / 'tools/panel_preview.js',
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/index.html']:
            html = (ROOT / 'plugin/index.html').read_text(encoding='utf-8')
            html = html.replace('<script src="vendor/sha256.js"></script>', '')
            html = html.replace('<script src="host.js"></script><script src="main.js"></script>',
                                '<script src="preview.js"></script>')
            html = html.replace('<head>', '<head><title>Contentrium CUT · UI 검토용 예시</title>')
            body, mime = html.encode('utf-8'), 'text/html'
        elif self.path in ASSETS:
            body = ASSETS[self.path].read_bytes()
            mime = 'text/css' if self.path.endswith('.css') else 'text/javascript'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    print('UI-only preview: http://127.0.0.1:41739', flush=True)
    HTTPServer(('127.0.0.1', 41739), Handler).serve_forever()
