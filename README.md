# youtube-downloader

**YouTube to MP3** downloads YouTube videos and playlists as the highest-quality MP3: it takes the best available audio stream and encodes it once at 320 kbps, with title, artist, chapters and cover art embedded.

- **One video** is saved straight into the folder you choose.
- **A playlist or channel** gets its own folder, named after it.

```text
Music/yt2mp3/
├── Some Song.mp3
└── My Playlist/
    ├── First song.mp3
    ├── Second song.mp3
    └── ...
```

Downloading again skips tracks whose MP3 is already in that folder, and leaves those files exactly as they are (tags you edited stay). Re-running a playlist is quick and fetches only what was added since; delete an MP3 to get it again.

Several tracks download at the same time: 3 by default, up to 8. Most of the time per track is waiting (opening the video, solving YouTube's challenge, converting), so 3 at a time is about 2.5× faster than one by one. Going much higher gains little and can get you rate-limited by YouTube.

There are three ways to use it, all with the same engine: a **desktop app**, a **web app** that runs on your own PC, and a **command line**.

## Setup (once, Windows)

```bash
py -m pip install -r requirements.txt
```

```bash
winget install Gyan.FFmpeg DenoLand.Deno
```

FFmpeg does the MP3 encoding. Deno is the JavaScript runtime yt-dlp uses to solve YouTube's challenges; without it, some videos fail. The apps pick both up right after installing, without a restart.

Then add Start menu shortcuts (add `-Desktop` for desktop shortcuts too):

```bash
powershell -ExecutionPolicy Bypass -File install_shortcuts.ps1
```

## Desktop app

Open **YouTube to MP3** from the Start menu, or run `pyw yt2mp3_gui.pyw`.

1. Paste one or more YouTube links, one per line (videos or playlists).
2. Pick the folder to save to with **Browse…**.
3. Press **Download** (or Ctrl+Enter).

**At once** sets how many tracks download together. It shows overall progress plus a line for each track being worked on, lists every file it saved, and can cancel at any time: tracks already being converted are finished, the rest stop, and no partial files are left behind. Your folder and options are remembered.

## Web app

Open **YouTube to MP3 (web)** from the Start menu, or run:

```bash
py yt2mp3_web.py
```

Your browser opens at `http://127.0.0.1:8780` with the same form: links, a **Browse…** button that opens the Windows folder picker, quality, how many at once, live progress per track and Cancel. Keep its small window open while you use it; closing that window stops it. Reloading the page reconnects to a running download.

It listens only on your own computer (`127.0.0.1`) and ignores requests from other websites. Use `--port 9000` if 8780 is taken, or `--no-browser` to skip opening the browser.

## Command line

```bash
py yt2mp3.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

```bash
py yt2mp3.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" -o D:\Music
```

Put the URL in quotes: `&` in a URL means something else to the shell.

| Option | What it does |
|---|---|
| `URL [URL ...]` | Videos, playlists or channels |
| `-a urls.txt` | One URL per line (`#` comments allowed) |
| `-o FOLDER` | Output folder (default `~/Music/yt2mp3`) |
| `-w 5` | Tracks to download at the same time, 1–8 (default 3; `-w 1` is one by one) |
| `--items 1-10` | Only some playlist items (`1-10`, `1,4,7-9`) |
| `--no-playlist` | For a `watch?v=…&list=…` URL, take only that video (by default the whole playlist is downloaded) |
| `--vbr` | LAME V0 (~245 kbps, smaller, same audible quality) instead of 320 CBR |
| `--keep-source` | Also keep the original Opus/M4A stream, untouched |
| `--cookies-from-browser firefox` | Use your login: Premium 256 kbps audio, age-restricted or members-only videos |
| `-v` | Also show yt-dlp's own messages |

Ctrl+C cancels: tracks being converted finish, the others stop, and partial files are removed.

## Files

| File | What it is |
|---|---|
| `yt2mp3.py` | The engine (`download()`: expands links into tracks, then downloads them with a pool of workers) and the command line |
| `job.py` | Background download with per-track progress and cancel, plus saved settings; used by both apps |
| `yt2mp3_gui.pyw` | Desktop app (tkinter) |
| `yt2mp3_web.py`, `web/index.html` | Local web app (Python standard library only) |
| `install_shortcuts.ps1` | Start menu / desktop shortcuts |

Settings are saved in `%APPDATA%\yt2mp3\settings.json`.

## When downloads start failing

YouTube changes often, and yt-dlp follows within days. Update it:

```bash
py -m pip install -U "yt-dlp[default]"
```

## About "highest quality"

YouTube's audio is already lossy: usually Opus at ~130–160 kbps or AAC at 128 kbps. Some videos offer 384 kbps 5.1 AAC (downmixed to stereo, since MP3 is stereo only), and Premium accounts get 256 kbps Opus. The tool always picks the best stream and encodes it once at the top MP3 setting, but no converter can add detail the source doesn't have. For a bit-exact copy of what YouTube sends, use `--keep-source` (or tick "Also keep the original audio file").

Only download content you have the rights to use.
