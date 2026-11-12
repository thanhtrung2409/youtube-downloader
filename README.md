# youtube-downloader

**YouTube to MP3** downloads YouTube videos and playlists as the highest-quality MP3: it takes the best available audio stream and encodes it once at 320 kbps, with title, artist, chapters and cover art embedded.

- **One video** is saved straight into the folder you choose.
- **A playlist or channel** gets its own folder, named after it, with numbered tracks.

```text
Music/yt2mp3/
├── Some Song.mp3
└── My Playlist/
    ├── 01 - First song.mp3
    ├── 02 - Second song.mp3
    └── ...
```

Downloading again skips tracks whose MP3 is already there, so re-running a playlist fetches only what was added since.

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

It shows progress per track, lists every file it saved, and can cancel at any time. A cancelled download leaves no partial files behind. Your folder and options are remembered.

## Web app

Open **YouTube to MP3 (web)** from the Start menu, or run:

```bash
py yt2mp3_web.py
```

Your browser opens at `http://127.0.0.1:8780` with the same form: links, a **Browse…** button that opens the Windows folder picker, quality options, live progress and Cancel. Keep its small window open while you use it; closing that window stops it. Reloading the page reconnects to a running download.

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
| `--items 1-10` | Only some playlist items (`1-10`, `1,4,7-9`) |
| `--no-playlist` | For a `watch?v=…&list=…` URL, take only that video (by default the whole playlist is downloaded) |
| `--vbr` | LAME V0 (~245 kbps, smaller, same audible quality) instead of 320 CBR |
| `--keep-source` | Also keep the original Opus/M4A stream, untouched |
| `--cookies-from-browser firefox` | Use your login: Premium 256 kbps audio, age-restricted or members-only videos |

## Files

| File | What it is |
|---|---|
| `yt2mp3.py` | The engine (`download()`) and the command line |
| `job.py` | Background download with progress and cancel, plus saved settings; used by both apps |
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
