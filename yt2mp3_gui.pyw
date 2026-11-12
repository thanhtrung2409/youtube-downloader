"""YouTube to MP3, desktop app: paste links, pick a folder, press Download.

Start it with `pyw yt2mp3_gui.pyw` (no console window), or with the Start menu
shortcut that install_shortcuts.ps1 creates.
"""

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job  # noqa: E402
import yt2mp3  # noqa: E402

if sys.stderr is None:  # pythonw has no console; keep this session's tracebacks somewhere findable
    job.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open(job.SETTINGS_FILE.with_name('gui.log'), 'w', encoding='utf-8')

TITLE = 'YouTube to MP3'
COLORS = {'ok': '#1a7f37', 'skip': '#6e7781', 'error': '#cf222e', 'info': '#24292f'}
ICONS = {'ok': '✓', 'skip': '↷', 'error': '✕', 'info': '•'}


def track_line(a: dict) -> str:
    """One line per track being worked on, e.g. "Downloading 45% · Song title"."""
    title = a['title'] if len(a['title']) <= 70 else a['title'][:69] + '…'
    percent = f" {a['percent']:.0f}%" if a['percent'] is not None else ''
    return f"{a['phase']}{percent} · {title}"


class App:
    POLL_MS = 150

    def __init__(self, root: tk.Tk):
        self.root = root
        self.job: job.DownloadJob | None = None
        self.log_next = 0

        s = job.load_settings()
        self.out = tk.StringVar(value=s['out'])
        self.vbr = tk.BooleanVar(value=s['vbr'])
        self.keep_source = tk.BooleanVar(value=s['keep_source'])
        self.no_playlist = tk.BooleanVar(value=s['no_playlist'])
        self.workers = tk.StringVar(value=str(s['workers']))
        self.status = tk.StringVar(value='Paste a YouTube link, then press Download.')
        self.detail = tk.StringVar()

        base = font.nametofont('TkDefaultFont')
        base.configure(size=10)
        font.nametofont('TkTextFont').configure(size=10)
        bold = base.copy()
        bold.configure(weight='bold')
        big = base.copy()
        big.configure(size=14, weight='bold')
        ttk.Style().configure('Big.TButton', padding=(18, 6))

        root.title(TITLE)
        root.minsize(600, 600)
        root.protocol('WM_DELETE_WINDOW', self.close)

        f = ttk.Frame(root, padding=18)
        f.pack(fill='both', expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(99, weight=1)  # the log grows with the window
        row = iter(range(100))

        ttk.Label(f, text=TITLE, font=big).grid(row=next(row), column=0, sticky='w')
        subtitle = ttk.Label(f, text='Best-quality MP3 from YouTube videos and playlists. '
                                     'Each playlist is saved in its own folder.',
                             foreground=COLORS['skip'])
        subtitle.grid(row=next(row), column=0, sticky='ew', pady=(0, 12))
        subtitle.bind('<Configure>', lambda e: subtitle.configure(wraplength=e.width))

        blocking, warnings = yt2mp3.missing_requirements()
        for text in blocking + warnings:
            ttk.Label(f, text=text, wraplength=560,
                      foreground=COLORS['error'] if text in blocking else '#9a6700'
                      ).grid(row=next(row), column=0, sticky='w', pady=(0, 8))

        head = ttk.Frame(f)
        head.grid(row=next(row), column=0, sticky='ew')
        ttk.Label(head, text='YouTube links', font=bold).pack(side='left')
        ttk.Label(head, text='  one per line', foreground=COLORS['skip']).pack(side='left')
        ttk.Button(head, text='Paste', command=self.paste).pack(side='right')
        self.urls = tk.Text(f, height=4, wrap='none', undo=True, relief='solid', borderwidth=1,
                            padx=6, pady=4, font='TkTextFont')
        self.urls.grid(row=next(row), column=0, sticky='ew', pady=(4, 14))
        self.urls.bind('<Control-Return>', lambda e: (self.start(), 'break')[1])
        self.urls.focus_set()

        ttk.Label(f, text='Save to', font=bold).grid(row=next(row), column=0, sticky='w')
        folder = ttk.Frame(f)
        folder.grid(row=next(row), column=0, sticky='ew', pady=(4, 14))
        folder.columnconfigure(0, weight=1)
        ttk.Entry(folder, textvariable=self.out).grid(row=0, column=0, sticky='ew', ipady=2)
        ttk.Button(folder, text='Browse…', command=self.browse).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(folder, text='Open', command=self.open_folder).grid(row=0, column=2, padx=(6, 0))

        quality = ttk.Frame(f)
        quality.grid(row=next(row), column=0, sticky='w')
        ttk.Label(quality, text='Quality', font=bold).pack(side='left', padx=(0, 12))
        ttk.Radiobutton(quality, text='320 kbps (best)', variable=self.vbr, value=False).pack(side='left')
        ttk.Radiobutton(quality, text='V0 VBR (smaller, sounds the same)', variable=self.vbr,
                        value=True).pack(side='left', padx=(12, 0))
        at_once = ttk.Frame(f)
        at_once.grid(row=next(row), column=0, sticky='w', pady=(8, 0))
        ttk.Label(at_once, text='At once', font=bold).pack(side='left', padx=(0, 12))
        ttk.Spinbox(at_once, from_=1, to=yt2mp3.MAX_WORKERS, width=3, textvariable=self.workers,
                    state='readonly').pack(side='left')
        ttk.Label(at_once, text='  tracks at a time (3–4 is a good balance)',
                  foreground=COLORS['skip']).pack(side='left')
        ttk.Checkbutton(f, text='For a video link inside a playlist, download only that video',
                        variable=self.no_playlist).grid(row=next(row), column=0, sticky='w', pady=(8, 0))
        ttk.Checkbutton(f, text='Also keep the original audio file (Opus/M4A, not re-encoded)',
                        variable=self.keep_source).grid(row=next(row), column=0, sticky='w', pady=(4, 0))

        buttons = ttk.Frame(f)
        buttons.grid(row=next(row), column=0, sticky='w', pady=(16, 12))
        self.start_btn = ttk.Button(buttons, text='Download', style='Big.TButton', command=self.start)
        self.start_btn.pack(side='left')
        self.cancel_btn = ttk.Button(buttons, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_btn.pack(side='left', padx=(8, 0))
        ttk.Label(buttons, text='Ctrl+Enter', foreground=COLORS['skip']).pack(side='left', padx=(12, 0))

        ttk.Label(f, textvariable=self.status, font=bold).grid(row=next(row), column=0, sticky='w')
        ttk.Label(f, textvariable=self.detail, foreground=COLORS['skip'],
                  justify='left').grid(row=next(row), column=0, sticky='w')
        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.grid(row=next(row), column=0, sticky='ew', pady=(6, 10))

        logbox = ttk.Frame(f)
        logbox.grid(row=99, column=0, sticky='nsew')
        logbox.columnconfigure(0, weight=1)
        logbox.rowconfigure(0, weight=1)
        self.log = tk.Text(logbox, height=8, wrap='word', state='disabled', relief='solid', borderwidth=1,
                           padx=8, pady=6, font='TkTextFont', background='#f6f8fa', cursor='arrow')
        self.log.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(logbox, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.log['yscrollcommand'] = scroll.set
        for kind, color in COLORS.items():
            self.log.tag_configure(kind, foreground=color)

    # Actions

    def paste(self) -> None:
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        current = self.urls.get('1.0', 'end').strip()
        self.urls.insert('end', ('\n' if current else '') + text)

    def browse(self) -> None:
        current = Path(self.out.get().strip() or yt2mp3.DEFAULT_OUT)
        initial = next((p for p in (current, *current.parents) if p.is_dir()), Path.home())
        path = filedialog.askdirectory(parent=self.root, initialdir=initial, mustexist=False,
                                       title='Choose where to save the MP3s')
        if path:
            self.out.set(str(Path(path)))

    def open_folder(self) -> None:
        path = Path(self.out.get().strip())
        if path.is_dir():
            os.startfile(path)
        else:
            messagebox.showinfo(TITLE, "This folder doesn't exist yet. It is created on the first download.",
                                parent=self.root)

    def settings(self) -> dict:
        return {'out': self.out.get().strip(), 'vbr': self.vbr.get(),
                'keep_source': self.keep_source.get(), 'no_playlist': self.no_playlist.get(),
                'workers': job.clamp_workers(self.workers.get())}

    def start(self) -> None:
        if self.job and self.job.running:
            return
        urls, rejected = job.parse_urls(self.urls.get('1.0', 'end'))
        if not urls:
            messagebox.showwarning(TITLE, 'Paste at least one YouTube link (it starts with https://).',
                                   parent=self.root)
            return
        s = self.settings()
        if not s['out'] or not Path(s['out']).is_absolute():
            messagebox.showwarning(TITLE, 'Choose a folder to save to.', parent=self.root)
            return
        blocking, _ = yt2mp3.missing_requirements()
        if blocking:
            messagebox.showerror(TITLE, '\n\n'.join(blocking), parent=self.root)
            return
        job.save_settings(s)

        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        for word in rejected:
            self.append('error', f'Ignored, not a link: {word}')
        self.job = job.DownloadJob(urls, Path(s['out']), workers=s['workers'], vbr=s['vbr'],
                                   keep_source=s['keep_source'], no_playlist=s['no_playlist']).start()
        self.log_next = 0
        self.start_btn.configure(state='disabled')
        self.cancel_btn.configure(state='normal')
        self.poll()

    def cancel(self) -> None:
        if self.job:
            self.job.cancel()
            self.cancel_btn.configure(state='disabled')

    def close(self) -> None:
        if self.job and self.job.running:
            if not messagebox.askyesno(TITLE, 'A download is still running. Stop it and quit?', parent=self.root):
                return
            self.job.cancel()
        job.save_settings(self.settings())
        self.root.destroy()

    # Progress

    def poll(self) -> None:
        s = self.job.snapshot(self.log_next)
        self.log_next = s['log_next']
        for kind, text in s['log']:
            self.append(kind, text)
        if s['state'] in ('running', 'cancelling'):
            if s['state'] == 'cancelling':
                status = 'Cancelling… (tracks being converted are finished first)'
            elif s['total']:
                status = f"{s['done']} of {s['total']} done" + (' · finding more…' if s['searching'] else '')
            else:
                status = 'Finding videos…'
            self.status.set(status)
            self.detail.set('\n'.join(track_line(a) for a in s['active']))
            self.set_bar(s['percent'])
            self.root.title(f"{s['percent']:.0f}% · {TITLE}" if s['percent'] is not None else TITLE)
            self.root.after(self.POLL_MS, self.poll)
        else:
            self.status.set(s['message'])
            self.detail.set(f"Saved in {s['out']}" if s['new'] else '')
            self.set_bar(100.0 if s['state'] == 'done' else 0.0)
            self.root.title(TITLE)
            self.start_btn.configure(state='normal')
            self.cancel_btn.configure(state='disabled')

    def set_bar(self, percent: float | None) -> None:
        if percent is None:
            if str(self.bar['mode']) != 'indeterminate':
                self.bar.configure(mode='indeterminate')
                self.bar.start(12)
        else:
            if str(self.bar['mode']) != 'determinate':
                self.bar.stop()
                self.bar.configure(mode='determinate')
            self.bar['value'] = percent

    def append(self, kind: str, text: str) -> None:
        self.log.configure(state='normal')
        self.log.insert('end', f'{ICONS[kind]}  {text}\n', kind)
        self.log.configure(state='disabled')
        self.log.see('end')


def main() -> None:
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on high-DPI screens
        except (AttributeError, OSError):
            pass
    yt2mp3.refresh_path()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
