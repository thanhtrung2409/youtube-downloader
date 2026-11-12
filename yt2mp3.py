#!/usr/bin/env python3
"""yt2mp3: download YouTube audio and convert it to the best-quality MP3.

It takes the best audio stream YouTube offers (usually Opus ~130-160 kbps,
or 256 kbps for Premium accounts with cookies), encodes it once with LAME at
320 kbps CBR (or V0 VBR with --vbr), and embeds the title, artist, chapters
and cover art.

A single video is saved straight into the output folder. A playlist (or a
channel) gets its own sub-folder named after it:

  ~/Music/yt2mp3/Song.mp3
  ~/Music/yt2mp3/My Playlist/First song.mp3

Usage:
  py yt2mp3.py URL [URL ...]          videos, playlists or channels
  py yt2mp3.py -a urls.txt            one URL per line (# comments allowed)
  py yt2mp3.py URL -o D:\\Music        choose the output folder
  py yt2mp3.py URL --items 1-10       only part of a playlist
  py yt2mp3.py URL --vbr              LAME V0 (~245 kbps) instead of 320 CBR
  py yt2mp3.py URL --keep-source      also keep the original Opus/M4A file
  py yt2mp3.py URL --cookies-from-browser firefox
                                      use your login (Premium 256 kbps audio,
                                      age-restricted or members-only videos)

Re-running is safe: tracks whose MP3 already exists are skipped, so running a
playlist again only downloads what was added since.

Requires: yt-dlp (`py -m pip install -r requirements.txt`), ffmpeg on PATH,
and a JavaScript runtime (Deno recommended) for YouTube's signature checks.

The desktop app (yt2mp3_gui.pyw) and the local web app (yt2mp3_web.py) are
front-ends for download() below.
"""

import argparse
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yt_dlp
except ImportError:  # reported by missing_requirements()
    yt_dlp = None

DEFAULT_OUT = Path.home() / 'Music' / 'yt2mp3'

# Playlist items go to "<playlist title>/<title>"; a single video has no
# playlist_title, so the folder falls back to "." (the output folder itself).
TEMPLATE = '%(playlist_title|.)s/%(title)s.%(ext)s'


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
    code: int = 0                                     # yt-dlp's exit code; 1 if any item failed


def download(urls: list[str], out: Path, *, logger=None,
             progress_hook: Callable[[dict], None] | None = None,
             postprocessor_hook: Callable[[dict], None] | None = None,
             on_file: Callable[[str, bool], None] | None = None,
             **options) -> Result:
    """Download urls (plus any options['batch_file']) as MP3 into out.

    options are build_argv()'s keyword options. logger and the hooks are passed
    to yt-dlp; a hook may raise yt_dlp.utils.DownloadCancelled to stop.
    on_file(path, is_new) is called for every MP3, whether just saved (True)
    or skipped because it was already there (False).
    """
    if yt_dlp is None:
        raise RuntimeError('yt-dlp is not installed')
    out = out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    parsed = yt_dlp.parse_options(build_argv(urls, out, **options))
    params = dict(parsed.ydl_opts)
    if logger:
        params.update(logger=logger, noprogress=True)
    result = Result()

    def skip_existing(info, *, incomplete: bool) -> str | None:
        # Runs before anything is downloaded or written, so an MP3 that is already there
        # is left exactly as it is (tags you edited included). Playlist entries are first
        # checked from the playlist listing alone (incomplete=True), so those videos are
        # not even opened.
        if not info.get('title') or (incomplete and info.get('playlist_index') is None):
            # yt-dlp also peeks at playlist entries before it attaches the playlist (and so
            # the folder) to them; those are checked again once it has.
            return None
        path = ydl.prepare_filename({**info, 'ext': 'mp3'})
        if not os.path.exists(path):
            return None
        result.skipped.append(path)
        if on_file:
            on_file(path, False)
        return f'Already downloaded: {os.path.relpath(path, out)}'

    params['match_filter'] = skip_existing

    def on_saved(path: str) -> None:
        result.new.append(path)
        # --keep-video also stops yt-dlp deleting the raw thumbnail; it is already embedded.
        for ext in ('.webp', '.jpg', '.png'):
            Path(path).with_suffix(ext).unlink(missing_ok=True)
        if on_file:
            on_file(path, True)

    with yt_dlp.YoutubeDL(params) as ydl:
        ydl.add_post_hook(on_saved)  # called with the final path after all post-processing
        if progress_hook:
            ydl.add_progress_hook(progress_hook)
        if postprocessor_hook:
            ydl.add_postprocessor_hook(postprocessor_hook)
        result.code = ydl.download(parsed.urls)
    return result


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
    a = p.parse_args()

    if not a.urls and not a.batch_file:
        p.error('give at least one URL or --batch-file')
    refresh_path()
    blocking, _ = missing_requirements()  # yt-dlp prints its own warning when Deno is missing
    if blocking:
        sys.exit('\n'.join(blocking))

    out = a.out.expanduser().resolve()
    try:
        result = download(a.urls, out, vbr=a.vbr, keep_source=a.keep_source,
                          no_playlist=a.no_playlist, items=a.items,
                          cookies_from_browser=a.cookies_from_browser, batch_file=a.batch_file)
    except KeyboardInterrupt:
        print('\nCancelled.')
        return 130

    print()
    if result.new:
        print(f'Saved {len(result.new)} new file(s) to {out}:')
        for f in result.new:
            print(f'  {os.path.relpath(f, out)}')
    else:
        print('Nothing new was saved.')
    if result.skipped:
        print(f'Skipped {len(result.skipped)} already downloaded.')
    if result.code:
        print('Some items failed; see the errors above.')
    return result.code


if __name__ == '__main__':
    sys.exit(main())
