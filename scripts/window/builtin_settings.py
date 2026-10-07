"""Built-in BASSMIDI's "Settings…" window (the button under MIDI out): soundfont, voice limit, and lowering the voice
limit by itself while the synth is overloaded (off to start). Overload watches the synth while it plays: when it
can't make the sound as fast as it plays (busy spam), part of the sound goes missing (stops, crackles); the red
text next to the Settings button says so. Program settings, not the project's: no undo, kept with the window
settings in the autosave."""

import collections
import os
import threading
import time
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.synth import RATE
from window import look
from window.hz_preview import VOICES
from window.widgets import Scrub, Tooltip


def open_builtin_settings(app):
    w = getattr(app, "builtin_window", None)
    if w is not None and w.winfo_exists():
        w.lift()
    else:
        app.builtin_window = BuiltinSettings(app)


class BuiltinSettings(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("bs.title"))
        self.transient(app)
        self.resizable(False, False)
        s = app.scale
        box = ttk.Frame(self, padding=(12, 10, 12, 12))
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(box, text=tr("ps.font")).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        self.font_name = ttk.Label(box, text="", wraplength=round(250 * s))
        self.font_name.grid(row=r, column=1, sticky="w", pady=3)
        b = ttk.Button(box, text=tr("ps.change"), command=app.pick_soundfont)
        b.grid(row=r, column=2, sticky="w", padx=(8, 0), pady=3)
        Tooltip(b, tr("bs.change_tip"))
        r += 1

        lb = ttk.Label(box, text=tr("ps.voices"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        e = app.voices_entry = ttk.Entry(box, textvariable=app.voices_var, width=8)
        e.grid(row=r, column=1, sticky="w", pady=3)
        e.bind("<Return>", lambda ev: (app.on_play_voices(), "break")[1])
        e.bind("<FocusOut>", lambda ev: app.on_play_voices())
        Scrub(app, [(e, app.voices_var, app.on_play_voices)], (50, 500, 1), *VOICES, label=lb)
        Tooltip(e, tr("app.voices_tip"))
        Tooltip(lb, tr("app.voices_tip"))
        r += 1

        c = ttk.Checkbutton(box, text=tr("bs.guard"), variable=app.play_guard, command=app.on_play_guard,
                            takefocus=False)
        c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(c, tr("bs.guard_tip"))
        r += 1

        self.state = ttk.Label(box, text="", foreground=look.ERROR)
        self.state.grid(row=r, column=1, columnspan=2, sticky="w", pady=(3, 0))

        self.bind("<Escape>", lambda ev: self.destroy())
        self.refresh()
        self.update_idletasks()  # (near the side panel's MIDI out)
        btn = app.builtin_btn
        x = btn.winfo_rootx() - self.winfo_reqwidth() - round(10 * s)
        self.geometry(f"+{max(0, x)}+{max(0, btn.winfo_rooty() - round(40 * s))}")

    def refresh(self):
        if not self.winfo_exists():
            return
        font = self.app.hz_preview["font"]
        name = os.path.basename(font) if font else tr("ps.no_font")
        if self.font_name.cget("text") != name:
            self.font_name.config(text=name)
        text = self.app.overload_text
        if self.state.cget("text") != text:
            self.state.config(text=text)


class Overload:
    """Watches Built-in BASSMIDI while it plays, on its own thread (asking BASS can wait while it's busy making
    sound, and the window mustn't). Missing sound = the time passed minus the sound made (BASS plays the stream
    itself: what it couldn't make in time isn't heard). With guard, the voice limit is lowered while sound goes
    missing and raised slowly back to the user's limit once it's quiet again."""
    EVERY = 0.1  # seconds between looks
    TOO_MUCH = 0.02  # seconds missing within the last second = overloaded (BASS makes sound in pieces: some wobble)
    DROP = 0.85  # the limit is multiplied by this at each look with sound missing...
    DROP_WAIT = 0.3  # ...at most this often (a lower limit takes a moment to show)
    QUIET = 5.0  # seconds with nothing missing before the limit goes back up...
    RISE = 1.05  # ...by this much a second

    def __init__(self):
        self.thread = None
        self._stop = threading.Event()
        self.text_args = None  # None, ("overloaded",) or ("lowered", voices playing)

    def start(self, live, limit, guard):
        self.stop()
        self.live, self.limit, self.guard = live, limit, guard
        self.now_limit = limit
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_limit(self, limit):
        """The user typed another voice limit while playing: it starts again from there."""
        self.limit = self.now_limit = limit

    def set_guard(self, on):
        self.guard = on
        if not on and self.thread is not None and self.now_limit != self.limit:
            self.now_limit = self.limit
            self.live.set_voices(self.limit)

    def stop(self):
        if self.thread is not None:
            self._stop.set()
            self.thread.join()
            self.thread = None
            if self.now_limit != self.limit:
                self.live.set_voices(self.limit)
        self.text_args = None

    def _run(self):
        live, clock = self.live, time.perf_counter
        t0, p0 = clock(), live.position()
        lost = collections.deque()  # (time, seconds missing since the look before)
        voices = collections.deque()  # (time, voices playing)
        last_drop = last_lost = t0
        while not self._stop.wait(self.EVERY):
            t, p = clock(), live.position()
            gone = (t - t0) - (p - p0) / 8 / RATE
            t0, p0 = t, p
            lost.append((t, max(0.0, gone)))
            voices.append((t, live.voices_playing()))
            while lost[0][0] < t - 1:
                lost.popleft()
            while voices[0][0] < t - 1:
                voices.popleft()
            missing = sum(g for _, g in lost)
            if gone > 0.003:
                last_lost = t
            if self.guard:
                if gone > 0.003 and t - last_drop >= self.DROP_WAIT and self.now_limit > VOICES[0]:
                    self.now_limit = max(VOICES[0], int(self.now_limit * self.DROP))
                    live.set_voices(self.now_limit)
                    last_drop = t
                elif self.now_limit < self.limit and t - last_lost >= self.QUIET and t - last_drop >= 1:
                    self.now_limit = min(self.limit, int(self.now_limit * self.RISE) + 1)
                    live.set_voices(self.now_limit)
                    last_drop = t
                if self.now_limit < self.limit or missing > self.TOO_MUCH:
                    self.text_args = ("lowered", round(sum(v for _, v in voices) / len(voices)))
                else:
                    self.text_args = None
            else:
                self.text_args = ("overloaded",) if missing > self.TOO_MUCH else None

    def text(self):
        a = self.text_args
        if a is None:
            return ""
        return tr("app.overloaded") if a[0] == "overloaded" else tr("app.overloaded_n", n=f"{a[1]:,}")
