#!/usr/bin/env python3
"""yt2mp3: download YouTube audio and convert it to the best-quality MP3.

It takes the best audio stream YouTube offers (usually Opus ~130-160 kbps,
or 256 kbps for Premium accounts with cookies), encodes it once with LAME at
320 kbps CBR (or V0 VBR with --vbr), and embeds the title, artist, chapters
and cover art.

A single video is saved straight into the output folder. A playlist (or a
channel) gets its own sub-folder named after it, with numbered tracks:

  ~/Music/yt2mp3/Song.mp3
  ~/Music/yt2mp3/My Playlist/01 - First song.mp3

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
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    sys.exit('yt-dlp is not installed. Run: py -m pip install -r requirements.txt')

DEFAULT_OUT = Path.home() / 'Music' / 'yt2mp3'

# Playlist items go to "<playlist title>/<index> - <title>"; a single video has no
# playlist_title, so the folder falls back to "." (the output folder itself).
TEMPLATE = '%(playlist_title|.)s/%(playlist_index&{:02d} - |)s%(title)s.%(ext)s'


def build_argv(a: argparse.Namespace, out: Path) -> list[str]:
    argv = [
        # Best audio-only stream; fall back to the best muxed file if a video has none.
        '--format', 'bestaudio/best',
        '--extract-audio', '--audio-format', 'mp3',
        '--audio-quality', '0' if a.vbr else '320K',
        '--embed-metadata', '--embed-thumbnail', '--convert-thumbnails', 'jpg',
        '--paths', str(out),
        '--output', TEMPLATE,
        '--output', f'thumbnail:{TEMPLATE}',
        # A playlist titled "." or ".." would otherwise write outside the output folder.
        '--replace-in-metadata', 'playlist_title', r'^\.+$', '_',
        '--ignore-errors', '--no-overwrites', '--windows-filenames',
        '--no-playlist' if a.no_playlist else '--yes-playlist',
    ]
    if a.items:
        argv += ['--playlist-items', a.items]
    if a.keep_source:
        argv.append('--keep-video')
    if a.cookies_from_browser:
        argv += ['--cookies-from-browser', a.cookies_from_browser]
    if a.batch_file:
        argv += ['--batch-file', a.batch_file]
    return argv + a.urls


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
    if not shutil.which('ffmpeg'):
        sys.exit('ffmpeg was not found on PATH. Install it with: winget install Gyan.FFmpeg')

    out = a.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    parsed = yt_dlp.parse_options(build_argv(a, out))
    existed = {str(f) for f in out.rglob('*.mp3')}
    saved: list[str] = []

    def on_saved(path: str) -> None:
        saved.append(path)
        # --keep-video also stops yt-dlp deleting the raw thumbnail; it is already embedded.
        for ext in ('.webp', '.jpg', '.png'):
            Path(path).with_suffix(ext).unlink(missing_ok=True)

    try:
        with yt_dlp.YoutubeDL(parsed.ydl_opts) as ydl:
            ydl.add_post_hook(on_saved)  # called with the final path after all post-processing
            code = ydl.download(parsed.urls)
    except KeyboardInterrupt:
        print('\nCancelled.')
        return 130

    # The post hook also fires for tracks skipped because their MP3 was already there.
    new = [f for f in saved if f not in existed]
    skipped = len(saved) - len(new)
    print()
    if new:
        print(f'Saved {len(new)} new file(s) to {out}:')
        for f in new:
            print(f'  {os.path.relpath(f, out)}')
    else:
        print('Nothing new was saved.')
    if skipped:
        print(f'Skipped {skipped} already downloaded.')
    if code:
        print('Some items failed; see the errors above.')
    return code


if __name__ == '__main__':
    sys.exit(main())
