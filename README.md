# youtube-downloader

`yt2mp3` downloads YouTube videos and playlists as the highest-quality MP3: it takes the best available audio stream and encodes it once at 320 kbps, with title, artist, chapters and cover art embedded.

- **One video** is saved straight into the output folder.
- **A playlist or channel** gets its own folder, named after it, with numbered tracks.

```text
~/Music/yt2mp3/
├── Some Song.mp3
└── My Playlist/
    ├── 01 - First song.mp3
    ├── 02 - Second song.mp3
    └── ...
```

## Setup (once, Windows)

```bash
py -m pip install -r requirements.txt
winget install Gyan.FFmpeg DenoLand.Deno
```

FFmpeg does the MP3 encoding. Deno is the JavaScript runtime yt-dlp uses to solve YouTube's challenges; without it, formats go missing. Open a new terminal after installing so both are on `PATH`.

## Use

```bash
py yt2mp3.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

```bash
py yt2mp3.py "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```

Put the URL in quotes: `&` in a URL means something else to the shell. `yt2mp3.cmd` does the same as `py yt2mp3.py`; add this folder to `PATH` to run `yt2mp3 URL` from anywhere.

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

Re-running is safe. Tracks whose MP3 already exists are skipped, so running a playlist again downloads only what was added since.

## About "highest quality"

YouTube's audio is already lossy: usually Opus at ~130–160 kbps or AAC at 128 kbps. Some videos offer 384 kbps 5.1 AAC (downmixed to stereo, since MP3 is stereo only), and Premium accounts get 256 kbps Opus. The tool always picks the best stream and encodes it once at the top MP3 setting, but no converter can add detail the source doesn't have. For a bit-exact copy of what YouTube sends, use `--keep-source`.

Only download content you have the rights to use.
