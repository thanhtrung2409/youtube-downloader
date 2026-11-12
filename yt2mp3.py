#!/usr/bin/env python3
"""yt2mp3: download YouTube audio and convert it to the best-quality MP3.

It takes the best audio stream YouTube offers (usually Opus ~130-160 kbps,
or 256 kbps for Premium accounts with cookies), encodes it once with LAME at
320 kbps CBR (or V0 VBR with --vbr), and embeds the title, artist, chapters
and cover art. Several tracks are downloaded at the same time (--workers).

A single video is saved straight into the output folder. A playlist (or a
channel) gets its own sub-folder named after it:

  ~/Music/yt2mp3/Song.mp3
  ~/Music/yt2mp3/My Playlist/First song.mp3

Usage:
  py yt2mp3.py URL [URL ...]          videos, playlists or channels
  py yt2mp3.py -a urls.txt            one URL per line (# comments allowed)
  py yt2mp3.py URL -o D:\\Music        choose the output folder
  py yt2mp3.py URL -w 5               5 tracks at a time (default 3, 1 = one by one)
  py yt2mp3.py URL --items 1-10       only part of a playlist
  py yt2mp3.py URL --vbr              LAME V0 (~245 kbps) instead of 320 CBR
  py yt2mp3.py URL --keep-source      also keep the original Opus/M4A file
  py yt2mp3.py URL --cookies-from-browser firefox
                                      use your login (Premium 256 kbps audio,
                                      age-restricted or members-only videos)

Re-running is safe: tracks whose MP3 already exists are skipped untouched, so
running a playlist again only downloads what was added since.

Requires: yt-dlp (`py -m pip install -r requirements.txt`), ffmpeg on PATH,
and a JavaScript runtime (Deno recommended) for YouTube's signature checks.

The desktop app (yt2mp3_gui.pyw) and the local web app (yt2mp3_web.py) are
front-ends for download() below.
"""

import argparse
import gc
import glob
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yt_dlp
except ImportError:  # reported by missing_requirements()
    yt_dlp = None

DEFAULT_OUT = Path.home() / 'Music' / 'yt2mp3'
DEFAULT_WORKERS = 3
MAX_WORKERS = 8  # more mostly gets you rate-limited by YouTube

# Playlist items go to "<playlist title>/<title>"; a single video has no
# playlist_title, so the folder falls back to "." (the output folder itself).
TEMPLATE = '%(playlist_title|.)s/%(title)s.%(ext)s'
THUMBNAIL_EXTS = ('.webp', '.jpg', '.png')


def refresh_path() -> None:
    """Add PATH entries saved in the registry since this process started.

    A tool installed with winget a minute ago is otherwise invisible until the
    terminal (or Explorer, for a shortcut) is restarted.
    """
    if sys.platform != 'win32':
        return
    import winreg

    current = [p for p in os.environ.get('PATH', '').split(os.pathsep) if p]
    seen = {p.rstrip('\\').lower() for p in current}
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                      (winreg.HKEY_CURRENT_USER, 'Environment')):
        try:
            with winreg.OpenKey(root, key) as k:
                value = winreg.QueryValueEx(k, 'Path')[0]
        except OSError:
            continue
        for p in os.path.expandvars(value).split(os.pathsep):
            if p and p.rstrip('\\').lower() not in seen:
                seen.add(p.rstrip('\\').lower())
                current.append(p)
    os.environ['PATH'] = os.pathsep.join(current)


def missing_requirements() -> tuple[list[str], list[str]]:
    """Return (blocking problems, warnings) about the tools yt2mp3 needs."""
    blocking, warnings = [], []
    if yt_dlp is None:
        blocking.append('yt-dlp is not installed. Run: py -m pip install -r requirements.txt')
    if not shutil.which('ffmpeg'):
        blocking.append('FFmpeg was not found. Install it with: winget install Gyan.FFmpeg')
    if not shutil.which('deno'):
        warnings.append('Deno was not found, so some YouTube videos may fail. '
                        'Install it with: winget install DenoLand.Deno')
    return blocking, warnings


def build_argv(urls: list[str], out: Path, *, vbr: bool = False, keep_source: bool = False,
               no_playlist: bool = False, items: str | None = None,
               cookies_from_browser: str | None = None, batch_file: str | None = None) -> list[str]:
    argv = [
        # Best audio-only stream; fall back to the best muxed file if a video has none.
        '--format', 'bestaudio/best',
        '--extract-audio', '--audio-format', 'mp3',
        '--audio-quality', '0' if vbr else '320K',
        '--embed-metadata', '--embed-thumbnail', '--convert-thumbnails', 'jpg',
        '--paths', str(out),
        '--output', TEMPLATE,
        '--output', f'thumbnail:{TEMPLATE}',
        # A playlist titled "." or ".." would otherwise write outside the output folder.
        '--replace-in-metadata', 'playlist_title', r'^\.+$', '_',
        '--ignore-errors', '--no-overwrites', '--windows-filenames',
        '--no-playlist' if no_playlist else '--yes-playlist',
    ]
    if items:
        argv += ['--playlist-items', items]
    if keep_source:
        argv.append('--keep-video')
    if cookies_from_browser:
        argv += ['--cookies-from-browser', cookies_from_browser]
    if batch_file:
        argv += ['--batch-file', batch_file]
    return argv + list(urls)


@dataclass
class Result:
    new: list[str] = field(default_factory=list)      # MP3s created by this run
    skipped: list[str] = field(default_factory=list)  # MP3s that were already there
    failed: list[str] = field(default_factory=list)   # titles (or links) that could not be downloaded
    cancelled: bool = False

    @property
    def code(self) -> int:
        return 1 if self.failed else 0


class Reporter:
    """What download() tells its caller, one track at a time. Override what you need.

    Methods are called from several worker threads at once. started(), progress(),
    postprocess() and ended() for a track all happen on the thread working on it.
    """

    def planned(self, title: str, playlist: str | None) -> None:
        """A track was found (playlist is the title of the playlist it is in)."""

    def link_done(self) -> None:
        """One of the links has been turned into tracks (or failed)."""

    def started(self, title: str, phase: str = 'Reading video info') -> None:
        """This thread starts working on a track (or, with phase 'Reading link', on a link)."""

    def progress(self, d: dict) -> None:
        """yt-dlp download progress for this thread's track."""

    def postprocess(self, d: dict) -> None:
        """yt-dlp post-processing (converting, tagging) for this thread's track."""

    def saved(self, path: str, is_new: bool) -> None:
        """A track's MP3 is ready: just made (is_new), or already there and left alone."""

    def failed(self, title: str) -> None:
        """A track or link could not be downloaded; yt-dlp has reported why."""

    def ended(self) -> None:
        """This thread is done with its track."""


def _is_nested_playlist(entry: dict) -> bool:
    # A channel lists its tabs (Videos, Shorts, ...) as playlists of their own.
    return entry.get('_type') == 'playlist' or entry.get('ie_key') == 'YoutubeTab'


class _Relay:
    """The yt-dlp logger download() installs.

    It passes messages on (to the caller's logger, or else the console), and calls
    check() first, which stops a track that is still being prepared once cancelled.
    After a cancel, warnings and errors are dropped: they are only yt-dlp noticing it.
    """

    def __init__(self, target, quiet: bool, check, cancel: threading.Event):
        self.target, self.quiet, self.check, self.cancel = target, quiet, check, cancel

    def debug(self, msg: str) -> None:
        self.check()
        if self.target:
            self.target.debug(msg)
        elif not self.quiet:
            print(msg, flush=True)

    info = debug

    def warning(self, msg: str) -> None:
        if self.cancel.is_set():
            return
        if self.target:
            self.target.warning(msg)
        elif not self.quiet:
            print(f'WARNING: {msg}', file=sys.stderr, flush=True)

    def error(self, msg: str) -> None:
        if self.cancel.is_set():
            return
        if self.target:
            self.target.error(msg)
        else:
            print(msg, file=sys.stderr, flush=True)


def download(urls: list[str], out: Path, *, workers: int = DEFAULT_WORKERS, reporter: Reporter | None = None,
             logger=None, quiet: bool = False, cancel: threading.Event | None = None, **options) -> Result:
    """Download urls (plus any options['batch_file']) as MP3 into out, `workers` tracks at a time.

    First every link is expanded into tracks (a playlist from its listing alone), and
    tracks whose MP3 is already there are skipped without being opened. Then a pool of
    workers downloads the rest, each with its own yt-dlp instance.

    options are build_argv()'s keyword options. logger receives yt-dlp's messages
    (without one they go to the console, unless quiet). Setting cancel stops every
    track at its next step, except one already being converted, which is finished;
    partial files are deleted and the result has cancelled=True.
    """
    if yt_dlp is None:
        raise RuntimeError('yt-dlp is not installed')
    workers = max(1, min(MAX_WORKERS, workers))
    reporter = reporter or Reporter()
    cancel = cancel or threading.Event()
    out = out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    parsed = yt_dlp.parse_options(build_argv(urls, out, **options))

    result = Result()
    lock = threading.Lock()
    # Per thread: its YoutubeDL; what its track is doing (phase: 'prepare' until the
    # download starts, then 'download', then 'convert'); the track's file name without
    # extension (base); and whether it ended with an MP3 (got_file).
    local = threading.local()
    instances: list = []
    stopped: set[str] = set()  # file names (without extension) of tracks stopped part-way

    def stop_if_preparing() -> None:
        # Called on every message yt-dlp logs, so a cancel also stops a track while it is
        # still reading video info or fetching its cover, not only once it downloads.
        if cancel.is_set() and getattr(local, 'phase', None) == 'prepare':
            if base := getattr(local, 'base', None):
                with lock:
                    stopped.add(base)
            raise yt_dlp.utils.DownloadCancelled('Cancelled')

    params = dict(parsed.ydl_opts)
    params['logger'] = _Relay(logger, quiet, stop_if_preparing, cancel)
    params['noprogress'] = True  # progress comes from the hooks; bars from several threads would garble
    namer = yt_dlp.YoutubeDL(dict(params))  # only works out file names

    def before_download(info, *, incomplete: bool) -> str | None:
        # yt-dlp's match filter: runs before anything is downloaded or written. Returning
        # a reason skips the track, so an MP3 that is already there is left exactly as it
        # is (tags you edited included).
        if not info.get('title') or (incomplete and info.get('playlist_index') is None):
            # yt-dlp also peeks at playlist entries before it attaches the playlist (and so
            # the folder) to them; those are checked again once it has.
            return None
        path = namer.prepare_filename({**info, 'ext': 'mp3'})
        if os.path.exists(path):
            with lock:
                result.skipped.append(path)
            local.got_file = True
            reporter.saved(path, False)
            return f'Already downloaded: {os.path.relpath(path, out)}'
        if not incomplete:
            local.base = os.path.splitext(path)[0]
            # YouTube lists ~40 cover image URLs, most of them guesses that don't exist for
            # older videos; yt-dlp tries them one by one, ~0.5 s per miss. The ones that came
            # with a size are real, so keep the largest of those.
            real = [t for t in info.get('thumbnails') or () if t.get('width')]
            if real:
                info['thumbnails'] = [max(real, key=lambda t: (t['width'], t.get('preference') or 0))]
        return None

    params['match_filter'] = before_download

    def stop_if_cancelled(d: dict) -> None:
        # Progress hook. A track whose download is complete is converted rather than
        # stopped, so its downloaded audio doesn't stay behind.
        if d['status'] == 'finished':
            local.phase = 'convert'
            return
        local.phase = 'download'
        if cancel.is_set():
            if name := d.get('filename'):
                with lock:
                    stopped.add(os.path.splitext(name)[0])
            raise yt_dlp.utils.DownloadCancelled('Cancelled')

    def mark_converting(d: dict) -> None:
        # Past the download, a track is finished rather than stopped: no half-made MP3s.
        # (MetadataParser and ThumbnailsConvertor run before the download.)
        if d['postprocessor'] not in ('MetadataParser', 'ThumbnailsConvertor'):
            local.phase = 'convert'

    def on_saved(path: str) -> None:
        with lock:
            result.new.append(path)
        local.got_file = True
        # --keep-video also stops yt-dlp deleting the raw thumbnail; it is already embedded.
        for ext in THUMBNAIL_EXTS:
            Path(path).with_suffix(ext).unlink(missing_ok=True)
        reporter.saved(path, True)

    def ydl():
        """This thread's YoutubeDL (they are not thread-safe, so each worker has its own)."""
        if not hasattr(local, 'ydl'):
            y = yt_dlp.YoutubeDL(dict(params))
            y.add_progress_hook(stop_if_cancelled)
            y.add_progress_hook(reporter.progress)
            y.add_postprocessor_hook(mark_converting)
            y.add_postprocessor_hook(reporter.postprocess)
            y.add_post_hook(on_saved)  # called with the final path after all post-processing
            with lock:
                instances.append(y)
            local.ydl = y
        return local.ydl

    def fail(title: str) -> None:
        with lock:
            result.failed.append(title)
        reporter.failed(title)

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='yt2mp3')
    outstanding = 0
    idle = threading.Event()
    idle.set()

    def submit(fn, *args) -> None:
        nonlocal outstanding
        with lock:
            outstanding += 1
            idle.clear()

        def run():
            nonlocal outstanding
            try:
                fn(*args)
            finally:
                with lock:
                    outstanding -= 1
                    if not outstanding:
                        idle.set()

        pool.submit(run)

    def fetch(info: dict, extra: dict) -> None:
        """Worker: download and convert one track."""
        if cancel.is_set():
            return
        title = info.get('title') or info.get('url') or info.get('id') or '?'
        local.got_file, local.base, local.phase = False, None, 'prepare'
        reporter.started(title)
        try:
            ydl().process_ie_result(dict(info), download=True, extra_info=dict(extra))
        except yt_dlp.utils.DownloadCancelled:
            return
        except Exception as e:  # one broken track must not stop the others
            ydl().report_error(f'{title}: {e}')
        finally:
            local.phase = None
            reporter.ended()
        if not local.got_file and not cancel.is_set():
            fail(title)

    def queue(entry: dict, extra: dict, playlist: str | None) -> None:
        reporter.planned(entry.get('title') or entry.get('url') or '?', playlist)
        if not before_download({**entry, **extra}, incomplete=True):
            submit(fetch, entry, extra)

    def expand(y, info: dict) -> None:
        kind = info.get('_type', 'video')
        if kind == 'playlist':
            playlist = info.get('title') or info.get('id')
            base = {'playlist': playlist, 'playlist_title': playlist, 'playlist_id': info.get('id'),
                    'playlist_count': info.get('playlist_count')}
            for index, entry in yt_dlp.utils.PlaylistEntries(y, info).get_requested_items():
                if cancel.is_set():
                    return
                if not entry:
                    continue
                if _is_nested_playlist(entry):
                    resolve(y, entry)
                else:
                    queue(entry, {**base, 'playlist_index': index}, playlist)
        elif kind in ('url', 'url_transparent'):
            resolve(y, info)
        else:  # a single video, already extracted: the worker reuses it
            queue(info, {}, None)

    def resolve(y, ref: dict) -> None:
        info = y.extract_info(ref['url'], download=False, process=False, ie_key=ref.get('ie_key'))
        if info is None:  # yt-dlp has reported why
            fail(ref.get('title') or ref['url'])
        else:
            expand(y, info)

    def plan(url: str) -> None:
        """Turn a link into tracks, from the playlist listing alone where there is one."""
        if cancel.is_set():
            return
        local.base, local.phase = None, 'prepare'
        reporter.started(url, 'Reading link')
        try:
            resolve(ydl(), {'url': url})
        except yt_dlp.utils.DownloadCancelled:
            pass
        finally:
            local.phase = None
            reporter.ended()
            reporter.link_done()

    try:
        for url in parsed.urls:
            submit(plan, url)
        try:
            while not idle.wait(0.5):  # a timed wait lets Ctrl+C through on Windows
                pass
        except KeyboardInterrupt:
            cancel.set()
            while not idle.wait(0.5):  # each worker stops at its next download step
                pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        for y in instances + [namer]:
            y.close()
    if stopped:
        # A stopped download's file can stay open until its traceback is garbage-collected,
        # and Windows won't delete an open file.
        gc.collect()
        for base in stopped:
            for f in glob.glob(glob.escape(base) + '.*'):
                # Only what yt-dlp leaves mid-way (never an audio file): partial downloads and the cover.
                if f.endswith(('.part', '.ytdl')) or '.part-Frag' in f or f.lower().endswith(THUMBNAIL_EXTS):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
    result.cancelled = cancel.is_set()
    return result


class _CliReporter(Reporter):
    def __init__(self, out: Path):
        self.out, self.lock, self.playlists = out, threading.Lock(), set()

    def say(self, text: str) -> None:
        with self.lock:
            print(text, flush=True)

    def planned(self, title, playlist):
        if playlist and playlist not in self.playlists:
            self.playlists.add(playlist)
            self.say(f'Playlist: {playlist}')

    def saved(self, path, is_new):
        rel = os.path.relpath(path, self.out)
        self.say(f'  ✓ {rel}' if is_new else f'  ↷ {rel} (already downloaded)')

    def failed(self, title):
        self.say(f'  ✕ {title}')


def main() -> int:
    # Titles are often non-ASCII (e.g. Vietnamese); don't crash when output is piped.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    p = argparse.ArgumentParser(
        prog='yt2mp3',
        description='Download YouTube audio as the highest-quality MP3. '
                    'Playlists are saved into their own folder.',
    )
    p.add_argument('urls', nargs='*', metavar='URL', help='video, playlist or channel URL')
    p.add_argument('-a', '--batch-file', metavar='FILE', help='text file with one URL per line')
    p.add_argument('-o', '--out', type=Path, default=DEFAULT_OUT,
                   help=f'output folder (default: {DEFAULT_OUT})')
    p.add_argument('-w', '--workers', type=int, default=DEFAULT_WORKERS, metavar='N',
                   help=f'tracks to download at the same time, 1-{MAX_WORKERS} (default {DEFAULT_WORKERS})')
    p.add_argument('--vbr', action='store_true',
                   help='encode LAME V0 VBR (~245 kbps, smaller) instead of 320 kbps CBR')
    p.add_argument('--keep-source', action='store_true',
                   help='also keep the original downloaded audio (Opus/M4A, no re-encode)')
    p.add_argument('--no-playlist', action='store_true',
                   help='for a watch?v=...&list=... URL, download only that video')
    p.add_argument('--items', metavar='SPEC',
                   help='only these playlist items, e.g. "1-10" or "1,4,7-9"')
    p.add_argument('--cookies-from-browser', metavar='BROWSER',
                   help='use cookies from a browser (firefox, edge, chrome, ...)')
    p.add_argument('-v', '--verbose', action='store_true', help="also show yt-dlp's own output")
    a = p.parse_args()

    if not a.urls and not a.batch_file:
        p.error('give at least one URL or --batch-file')
    if not 1 <= a.workers <= MAX_WORKERS:
        p.error(f'--workers must be between 1 and {MAX_WORKERS}')
    refresh_path()
    blocking, _ = missing_requirements()  # yt-dlp prints its own warning when Deno is missing
    if blocking:
        sys.exit('\n'.join(blocking))

    out = a.out.expanduser().resolve()
    print(f'Saving to {out}  ({a.workers} at a time)', flush=True)
    result = download(a.urls, out, workers=a.workers, reporter=_CliReporter(out), quiet=not a.verbose,
                      vbr=a.vbr, keep_source=a.keep_source, no_playlist=a.no_playlist, items=a.items,
                      cookies_from_browser=a.cookies_from_browser, batch_file=a.batch_file)

    print()
    parts = [f'{len(result.new)} new']
    if result.skipped:
        parts.append(f'{len(result.skipped)} already downloaded')
    if result.failed:
        parts.append(f'{len(result.failed)} failed')
    print(('Cancelled. ' if result.cancelled else 'Finished: ') + ', '.join(parts) + '.')
    return 130 if result.cancelled else result.code


if __name__ == '__main__':
    sys.exit(main())
