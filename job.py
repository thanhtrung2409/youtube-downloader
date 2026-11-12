"""Background download job and saved settings, shared by the desktop and web apps.

A DownloadJob runs yt2mp3.download() on its own thread and turns yt-dlp's
messages and hooks into a small progress state. A UI starts the job, polls
snapshot() a few times a second, and may call cancel().
"""

import itertools
import json
import os
import re
import threading
from pathlib import Path

import yt2mp3

SETTINGS_FILE = Path(os.environ.get('APPDATA') or Path.home()) / 'yt2mp3' / 'settings.json'
DEFAULT_SETTINGS = {'out': str(yt2mp3.DEFAULT_OUT), 'vbr': False, 'keep_source': False, 'no_playlist': False}


def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    return {k: saved[k] if isinstance(saved.get(k), type(v)) else v for k, v in DEFAULT_SETTINGS.items()}


def save_settings(settings: dict) -> None:
    data = {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    except OSError:
        pass  # remembering the folder is a convenience, never a reason to fail


def parse_urls(text: str) -> tuple[list[str], list[str]]:
    """Split pasted text into (http(s) URLs, everything else)."""
    words = text.split()
    urls = [w for w in words if w.lower().startswith(('http://', 'https://'))]
    return urls, [w for w in words if w not in urls]


ANSI = re.compile(r'\x1b\[[0-9;]*m')
PLAYLIST = re.compile(r'^\[download\] Downloading playlist: (.+)$')
ITEM = re.compile(r'^\[download\] Downloading item (\d+) of (\d+)$')
# yt-dlp post-processor keys -> what the user sees while it runs.
PHASES = {'ExtractAudio': 'Converting to MP3', 'Metadata': 'Adding tags', 'EmbedThumbnail': 'Adding cover art'}

_ids = itertools.count(1)


class _Logger:
    """yt-dlp logger that feeds a job. yt-dlp sends normal output to debug()."""

    def __init__(self, job: 'DownloadJob'):
        self.job = job

    def debug(self, msg: str) -> None:
        if not msg.startswith('[debug] '):
            self.job._on_message(ANSI.sub('', msg))

    info = debug

    def warning(self, msg: str) -> None:
        pass  # mostly format noise; missing tools are checked up front instead

    def error(self, msg: str) -> None:
        self.job._on_error(ANSI.sub('', msg).removeprefix('ERROR: '))


class DownloadJob:
    def __init__(self, urls: list[str], out: Path, **options):
        self.id = next(_ids)
        self.urls, self.out, self.options = urls, Path(out).expanduser().resolve(), options
        self.state = 'running'    # running, cancelling, done, cancelled, failed
        self.phase = 'Starting'
        self.title = ''
        self.playlist = ''
        self.item = self.count = 0
        self.percent: float | None = None  # None while the current step can't be measured
        self.log: list[tuple[str, str]] = []  # (kind, text); kind is info, ok, skip or error
        self.new = self.skipped = self.errors = 0
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._partial: list[str] = []  # files of the track that was cancelled mid-download
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> 'DownloadJob':
        self._thread.start()
        return self

    @property
    def running(self) -> bool:
        return self.state in ('running', 'cancelling')

    def cancel(self) -> None:
        with self._lock:
            if self.state == 'running':
                self.state, self.phase = 'cancelling', 'Cancelling…'
                self._cancel.set()

    def snapshot(self, log_from: int = 0) -> dict:
        """Current state, with log lines from index log_from on."""
        with self._lock:
            return {
                'id': self.id, 'state': self.state, 'phase': self.phase, 'title': self.title,
                'playlist': self.playlist, 'item': self.item, 'count': self.count,
                'percent': self.percent, 'new': self.new, 'skipped': self.skipped,
                'errors': self.errors, 'out': str(self.out),
                'log': [list(entry) for entry in self.log[log_from:]], 'log_next': len(self.log),
            }

    # Everything below runs on the job's thread.

    def _add(self, kind: str, text: str) -> None:
        with self._lock:
            self.log.append((kind, text))

    def _run(self) -> None:
        self._add('info', f'Saving to {self.out}')
        cancelled = False
        try:
            yt2mp3.download(self.urls, self.out, logger=_Logger(self), progress_hook=self._on_progress,
                            postprocessor_hook=self._on_postprocess, on_file=self._on_file, **self.options)
        except yt2mp3.yt_dlp.utils.DownloadCancelled:
            cancelled = True
        except Exception as e:  # anything else ends the job; show it rather than die silently
            self._finish('failed', 'error', f'Stopped: {e}')
        else:
            parts = [f'{self.new} new']
            if self.skipped:
                parts.append(f'{self.skipped} already downloaded')
            if self.errors:
                parts.append(f'{self.errors} failed')
            self._finish('done', 'error' if self.errors and not self.new else 'info',
                         'Finished: ' + ', '.join(parts) + '.')
        if cancelled:
            # Only now is yt-dlp's handle on the partial file closed, so Windows lets us delete it.
            for f in self._partial:
                try:
                    Path(f).unlink(missing_ok=True)
                except OSError:
                    pass
            self._finish('cancelled', 'info', 'Cancelled.')

    def _finish(self, state: str, kind: str, text: str) -> None:
        with self._lock:
            self.state, self.phase, self.percent = state, text, None
            self.log.append((kind, text))

    def _on_message(self, msg: str) -> None:
        if m := PLAYLIST.match(msg):
            with self._lock:
                self.playlist = m[1]
            self._add('info', f'Playlist: {m[1]}')
        elif m := ITEM.match(msg):
            with self._lock:
                self.item, self.count, self.title = int(m[1]), int(m[2]), ''
                if not self._cancel.is_set():
                    self.phase, self.percent = 'Reading video info', None
        elif msg.startswith('[youtube] Extracting URL'):
            with self._lock:
                if not self._cancel.is_set():
                    self.phase, self.percent = 'Reading video info', None

    def _on_error(self, msg: str) -> None:
        with self._lock:
            self.errors += 1
            self.log.append(('error', msg))

    def _on_progress(self, d: dict) -> None:
        if self._cancel.is_set():
            # Stop here, while the track is still downloading, and remember what to delete.
            # Never the final MP3: an already-downloaded track also reports progress.
            if name := d.get('filename'):
                stem = Path(name)
                self._partial = [d.get('tmpfilename') or name + '.part', name + '.ytdl',
                                 *(str(stem.with_suffix(ext)) for ext in ('.webp', '.jpg', '.png'))]
                self._partial = [f for f in self._partial if not f.lower().endswith('.mp3')]
            raise yt2mp3.yt_dlp.utils.DownloadCancelled('Cancelled by user')
        with self._lock:
            self.title = (d.get('info_dict') or {}).get('title') or self.title
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate')
                self.phase = 'Downloading'
                self.percent = min(100.0, 100 * (d.get('downloaded_bytes') or 0) / total) if total else None
            elif d['status'] == 'finished':
                self.percent = 100.0

    def _on_postprocess(self, d: dict) -> None:
        # A cancel during conversion lets this track finish (no half-tagged MP3s);
        # it takes effect when the next track starts downloading.
        if d['status'] == 'started' and d['postprocessor'] in PHASES and not self._cancel.is_set():
            with self._lock:
                self.phase, self.percent = PHASES[d['postprocessor']], None

    def _on_file(self, path: str, is_new: bool) -> None:
        rel = os.path.relpath(path, self.out)
        with self._lock:
            if is_new:
                self.new += 1
                self.log.append(('ok', rel))
            else:
                self.skipped += 1
                self.log.append(('skip', f'{rel} (already downloaded)'))
