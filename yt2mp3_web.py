#!/usr/bin/env python3
"""YouTube to MP3, local web app: http://127.0.0.1:8780

Run `py yt2mp3_web.py` and it opens in your browser. It listens only on this
computer, and stops when you close its window or press Ctrl+C. Running it
again while it is already running just opens the browser.

  py yt2mp3_web.py --port 9000      use another port
  py yt2mp3_web.py --no-browser     don't open the browser
"""

import argparse
import json
import os
import sys
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job  # noqa: E402
import yt2mp3  # noqa: E402

PAGE = Path(__file__).resolve().parent / 'web' / 'index.html'
DEFAULT_PORT = 8780
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; frame-ancestors 'none'")


def pick_folder(initial: str) -> str:
    """Show the Windows folder picker. The server runs on the user's own PC, so it appears there."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)  # in front of the browser
    try:
        start = Path(initial) if initial.strip() else yt2mp3.DEFAULT_OUT
        start_dir = next((p for p in (start, *start.parents) if p.is_dir()), Path.home())
        path = filedialog.askdirectory(parent=root, initialdir=start_dir, mustexist=False,
                                       title='Choose where to save the MP3s')
        return str(Path(path)) if path else ''
    finally:
        root.destroy()


class App:
    """What all requests share: the current (or last) download job."""

    def __init__(self):
        self.job: job.DownloadJob | None = None
        self.lock = threading.Lock()
        self.dialog = threading.Lock()

    def state(self, job_id: int, log_from: int) -> dict:
        blocking, warnings = yt2mp3.missing_requirements()
        with self.lock:
            current = self.job
        snapshot = current.snapshot(log_from if current.id == job_id else 0) if current else None
        return {'settings': job.load_settings(), 'blocking': blocking, 'warnings': warnings, 'job': snapshot}

    def start(self, body: dict) -> tuple[int, dict]:
        urls, rejected = job.parse_urls(str(body.get('urls', '')))
        if not urls:
            return 400, {'error': 'Paste at least one YouTube link (it starts with https://).'}
        out = str(body.get('out', '')).strip()
        if not out or not Path(out).is_absolute():
            return 400, {'error': 'Choose a folder to save to.'}
        blocking, _ = yt2mp3.missing_requirements()
        if blocking:
            return 400, {'error': ' '.join(blocking)}
        settings = {'out': out, 'vbr': body.get('vbr') is True, 'keep_source': body.get('keep_source') is True,
                    'no_playlist': body.get('no_playlist') is True}
        with self.lock:
            if self.job and self.job.running:
                return 409, {'error': 'A download is already running.'}
            job.save_settings(settings)
            self.job = job.DownloadJob(urls, Path(out), vbr=settings['vbr'], keep_source=settings['keep_source'],
                                       no_playlist=settings['no_playlist']).start()
            return 200, {'id': self.job.id, 'rejected': rejected}

    def cancel(self, body: dict) -> tuple[int, dict]:
        with self.lock:
            if self.job:
                self.job.cancel()
        return 200, {}

    def pick_folder(self, body: dict) -> tuple[int, dict]:
        if not self.dialog.acquire(blocking=False):
            return 409, {'error': 'The folder window is already open.'}
        try:
            return 200, {'path': pick_folder(str(body.get('initial', '')))}
        finally:
            self.dialog.release()

    def open_folder(self, body: dict) -> tuple[int, dict]:
        path = Path(str(body.get('path', '')).strip())
        if not path.is_absolute() or not path.is_dir():  # only ever open folders, never run a file
            return 404, {'error': "This folder doesn't exist yet. It is created on the first download."}
        os.startfile(path)
        return 200, {}


class Handler(BaseHTTPRequestHandler):
    app: App  # set in main()

    def log_message(self, format, *args):
        pass  # keep the console for the status line

    def _trusted(self) -> bool:
        """Only this app's own page may call it: not other websites (via DNS rebinding or cross-site posts)."""
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (origin is None or origin in {f'http://{h}' for h in hosts})

    def _send(self, status: int, body, content_type: str = 'application/json; charset=utf-8') -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', CSP)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._trusted():
            return self._send(403, {'error': 'Forbidden'})
        url = urlsplit(self.path)
        if url.path == '/':
            return self._send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/api/state':
            q = parse_qs(url.query)
            try:
                job_id, log_from = int(q.get('job', ['0'])[0]), int(q.get('log_from', ['0'])[0])
            except ValueError:
                job_id = log_from = 0
            return self._send(200, self.app.state(job_id, max(0, log_from)))
        self._send(404, {'error': 'Not found'})

    def do_POST(self):
        # Requiring JSON forces a CORS preflight for any cross-site request, which this server never approves.
        if not self._trusted() or self.headers.get_content_type() != 'application/json':
            return self._send(403, {'error': 'Forbidden'})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {'error': 'Bad request'})
        route = {'/api/download': self.app.start, '/api/cancel': self.app.cancel,
                 '/api/pick-folder': self.app.pick_folder, '/api/open-folder': self.app.open_folder,
                 }.get(urlsplit(self.path).path)
        if not route:
            return self._send(404, {'error': 'Not found'})
        self._send(*route(body))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let two servers share the port; fail instead.
    allow_reuse_address = sys.platform != 'win32'


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + 'api/state', timeout=2) as r:
            return 'settings' in json.load(r)
    except (OSError, ValueError):
        return False


def main() -> int:
    p = argparse.ArgumentParser(description='YouTube to MP3 as a local web app.')
    p.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'port to listen on (default {DEFAULT_PORT})')
    p.add_argument('--no-browser', action='store_true', help="don't open the browser")
    a = p.parse_args()

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp folder picker on high-DPI screens
        except (AttributeError, OSError):
            pass
    yt2mp3.refresh_path()

    url = f'http://127.0.0.1:{a.port}/'
    try:
        server = Server(('127.0.0.1', a.port), Handler)
    except OSError:
        if already_running(url):
            print(f'YouTube to MP3 is already running at {url}')
            if not a.no_browser:
                webbrowser.open(url)
            return 0
        sys.exit(f'Port {a.port} is used by another program. Try: py yt2mp3_web.py --port {a.port + 1}')

    Handler.app = App()
    print(f'YouTube to MP3 is running at {url}')
    print('Keep this window open while you use it. Close it, or press Ctrl+C, to stop.')
    if not a.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
