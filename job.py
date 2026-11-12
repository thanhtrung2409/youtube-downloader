"""Background download job and saved settings, shared by the desktop and web apps.

A DownloadJob runs yt2mp3.download() on its own thread and keeps a small progress
state: how many tracks were found and finished, and what each worker is doing
right now. A UI starts the job, polls snapshot() a few times a second, and may
call cancel().
"""

import itertools
import json
import os
import re
import threading
from pathlib import Path

import yt2mp3

SETTINGS_FILE = Path(os.environ.get('APPDATA') or Path.home()) / 'yt2mp3' / 'settings.json'
DEFAULT_SETTINGS = {'out': str(yt2mp3.DEFAULT_OUT), 'vbr': False, 'keep_source': False, 'no_playlist': False,
                    'workers': yt2mp3.DEFAULT_WORKERS}


def clamp_workers(value) -> int:
    try:
        return max(1, min(yt2mp3.MAX_WORKERS, int(value)))
    except (TypeError, ValueError):
        return yt2mp3.DEFAULT_WORKERS


def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    settings = {k: saved[k] if type(saved.get(k)) is type(v) else v for k, v in DEFAULT_SETTINGS.items()}
    settings['workers'] = clamp_workers(settings['workers'])
    return settings


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
# yt-dlp post-processor keys -> what the user sees while it runs.
PHASES = {'ExtractAudio': 'Converting to MP3', 'Metadata': 'Adding tags', 'EmbedThumbnail': 'Adding cover art'}
# How far along a track is, for the overall progress bar: downloading counts for up to
# DOWNLOAD_SHARE of it, and converting/tagging for the rest.
DOWNLOAD_SHARE = 0.6

_ids = itertools.count(1)


class _Logger:
    """yt-dlp logger: keep errors, drop the chatter (progress comes from the hooks)."""

    def __init__(self, job: 'DownloadJob'):
        self.job = job

    def debug(self, msg: str) -> None:
        pass

    info = warning = debug

    def error(self, msg: str) -> None:
        self.job._add('error', ANSI.sub('', msg).removeprefix('ERROR: '))


class DownloadJob(yt2mp3.Reporter):
    def __init__(self, urls: list[str], out: Path, workers: int = yt2mp3.DEFAULT_WORKERS, **options):
        self.id = next(_ids)
        self.urls, self.options = urls, options
        self.out = Path(out).expanduser().resolve()
        self.workers = clamp_workers(workers)
        self.state = 'running'    # running, cancelling, done, cancelled, failed
        self.message = ''         # the final summary
        self.total = self.new = self.skipped = self.failed_count = 0
        self.links_left = len(urls)  # links not yet turned into tracks: more may be coming
        self.active: dict[int, dict] = {}  # worker thread -> {title, phase, percent, order}
        self.playlists: set[str] = set()
        self.log: list[tuple[str, str]] = []  # (kind, text); kind is info, ok, skip or error
        self._order = itertools.count()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
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
                self.state = 'cancelling'
                self._cancel.set()

    def snapshot(self, log_from: int = 0) -> dict:
        """Current state, with log lines from index log_from on."""
        with self._lock:
            done = self.new + self.skipped + self.failed_count
            active = sorted(self.active.values(), key=lambda a: a['order'])
            in_flight = sum(a['share'] for a in active)
            expected = self.total + self.links_left  # each unread link is at least one more track
            return {
                'id': self.id, 'state': self.state, 'message': self.message, 'out': str(self.out),
                'workers': self.workers, 'total': self.total, 'done': done, 'new': self.new,
                'skipped': self.skipped, 'failed': self.failed_count, 'searching': self.links_left > 0,
                'percent': min(100.0, 100 * (done + in_flight) / expected) if self.total else None,
                'active': [{k: a[k] for k in ('title', 'phase', 'percent')} for a in active],
                'log': [list(entry) for entry in self.log[log_from:]], 'log_next': len(self.log),
            }

    # Everything below runs on the job's or the workers' threads.

    def _add(self, kind: str, text: str) -> None:
        with self._lock:
            self.log.append((kind, text))

    def _run(self) -> None:
        self._add('info', f'Saving to {self.out} ({self.workers} at a time)')
        try:
            result = yt2mp3.download(self.urls, self.out, workers=self.workers, reporter=self,
                                     logger=_Logger(self), cancel=self._cancel, **self.options)
        except Exception as e:  # anything unexpected ends the job; show it rather than die silently
            return self._finish('failed', 'error', f'Stopped: {e}')
        parts = [f'{len(result.new)} new']
        if result.skipped:
            parts.append(f'{len(result.skipped)} already downloaded')
        if result.failed:
            parts.append(f'{len(result.failed)} failed')
        if result.cancelled:
            self._finish('cancelled', 'info', 'Cancelled: ' + ', '.join(parts) + '.')
        else:
            self._finish('done', 'error' if result.failed and not result.new else 'info',
                         'Finished: ' + ', '.join(parts) + '.')

    def _finish(self, state: str, kind: str, text: str) -> None:
        with self._lock:
            self.state, self.message = state, text
            self.active.clear()
            self.log.append((kind, text))

    def _mine(self) -> dict | None:
        return self.active.get(threading.get_ident())

    # yt2mp3.Reporter

    def planned(self, title, playlist):
        with self._lock:
            self.total += 1
            if playlist and playlist not in self.playlists:
                self.playlists.add(playlist)
                self.log.append(('info', f'Playlist: {playlist}'))

    def link_done(self):
        with self._lock:
            self.links_left -= 1

    def started(self, title, phase='Reading video info'):
        with self._lock:
            self.active[threading.get_ident()] = {'title': title, 'phase': phase,
                                                  'percent': None, 'share': 0.0, 'order': next(self._order)}

    def progress(self, d):
        with self._lock:
            if not (a := self._mine()):
                return
            a['title'] = (d.get('info_dict') or {}).get('title') or a['title']
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate')
                a['phase'] = 'Downloading'
                a['percent'] = min(100.0, 100 * (d.get('downloaded_bytes') or 0) / total) if total else None
                a['share'] = DOWNLOAD_SHARE * (a['percent'] or 0) / 100
            elif d['status'] == 'finished':
                a['percent'], a['share'] = 100.0, DOWNLOAD_SHARE

    def postprocess(self, d):
        if d['status'] == 'started' and d['postprocessor'] in PHASES:
            with self._lock:
                if a := self._mine():
                    a['phase'], a['percent'] = PHASES[d['postprocessor']], None
                    a['share'] = max(a['share'], DOWNLOAD_SHARE)

    def saved(self, path, is_new):
        rel = os.path.relpath(path, self.out)
        with self._lock:
            if is_new:
                self.new += 1
                self.log.append(('ok', rel))
            else:
                self.skipped += 1
                self.log.append(('skip', f'{rel} (already downloaded)'))
            if a := self._mine():
                a['share'] = 0.0  # now counted in done

    def failed(self, title):
        with self._lock:
            self.failed_count += 1

    def ended(self):
        with self._lock:
            self.active.pop(threading.get_ident(), None)
