"""Serve only the native-DOM regression fixture and its public ES modules on loopback."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).absolute().parent.parent

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/scripts/fixtures/live-shelf-focus.html':
            target = ROOT / 'scripts/fixtures/live-shelf-focus.html'
        elif path.startswith('/static/') and Path(path).name == path.removeprefix('/static/') and path.endswith('.mjs'):
            target = ROOT / 'static' / Path(path).name
        else:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/javascript' if target.suffix == '.mjs' else 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8984)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    print(f'http://127.0.0.1:{args.port}/scripts/fixtures/live-shelf-focus.html', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
