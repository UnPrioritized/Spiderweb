"""Built-in BASSMIDI's settings (Settings → Sound; the "Settings…" button under MIDI out opens it): soundfont, voice
limit, and lowering the voice limit by itself while the synth is overloaded (off to start). Overload watches the
synth while it plays: when it can't make the sound as fast as it plays (busy spam), part of the sound goes missing
(stops, crackles); the red text next to the Settings button says so. Program settings, not the project's: no undo,
saved in user/settings.ini [sound]."""

import collections
import threading
import time
from tkinter import ttk

from files import speed
from files.lang import tr
from files.synth import RATE
from window import look
from window.font_list import FontList
from window.hz_preview import VOICES
from window.widgets import Scrub, Tooltip, keep_width


def open_builtin_settings(app):
    """The "Settings…" button under MIDI out: the Settings window on its Sound tab (user, 2026-10-11)."""
    from window.settings_window import open_settings
    open_settings(app, "sound")


class BuiltinPanel(ttk.Frame):
    """Built-in BASSMIDI's settings in Settings → Sound, shown only while it's the MIDI out (app.builtin_window
    while the Settings window is open)."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        box = self
        box.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(box, text=tr("ps.fonts")).grid(row=r, column=0, sticky="ne", padx=(0, 8), pady=3)
        self.fonts = FontList(box, app)  # (shared with the Hz bass preview)
        self.fonts.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1
        first = set(box.grid_slaves())  # (rows from here on: Built-in BASSMIDI's own)

        lb = ttk.Label(box, text=tr("ps.voices"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        e = app.voices_entry = ttk.Entry(box, textvariable=app.voices_var, width=8)
        e.grid(row=r, column=1, sticky="w", pady=3)
        e.bind("<Return>", lambda ev: (app.on_play_voices(), "break")[1])
        e.bind("<FocusOut>", lambda ev: app.on_play_voices())
        Scrub(app, [(e, app.voices_var, app.on_play_voices)], (50, 500, 1), *VOICES, label=lb, no_undo=True)
        Tooltip(e, tr("app.voices_tip"))
        Tooltip(lb, tr("app.voices_tip"))
        r += 1

        from window.app_playback import GROUP_MOST
        lb = ttk.Label(box, text=tr("bs.synths"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        e = app.synths_entry = ttk.Entry(box, textvariable=app.synths_var, width=8)
        e.grid(row=r, column=1, sticky="w", pady=3)
        e.bind("<Return>", lambda ev: (app.on_loaded_synths(), "break")[1])
        e.bind("<FocusOut>", lambda ev: app.on_loaded_synths())
        Scrub(app, [(e, app.synths_var, app.on_loaded_synths)], (1, 4, 1), 1, GROUP_MOST, label=lb, no_undo=True)
        Tooltip(e, tr("bs.synths_tip"))
        Tooltip(lb, tr("bs.synths_tip"))
        r += 1

        c = ttk.Checkbutton(box, text=tr("bs.guard"), variable=app.play_guard, command=app.on_play_guard,
                            takefocus=False)
        c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(c, tr("bs.guard_tip"))
        r += 1

        c = ttk.Checkbutton(box, text=tr("bs.slow_down"), variable=app.slow_builtin, command=app.on_slow_down,
                            takefocus=False)
        c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(c, tr("bs.slow_down_tip"))
        r += 1

        c = ttk.Checkbutton(box, text=tr("bs.limiter"), variable=app.play_limiter, command=app.on_play_limiter,
                            takefocus=False)
        c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(c, tr("bs.limiter_tip") + (tr("bs.limiter_slow") if speed.without_loops() else ""))
        r += 1
        self.bass_rows = [w for w in box.grid_slaves() if w not in first]
        r = self.kestrel_rows(r)

        self.state = ttk.Label(box, text="", foreground=look.ERROR)
        self.state.grid(row=r, column=1, columnspan=2, sticky="w", pady=(3, 0))

        self.refresh()

    def kestrel_rows(self, r):
        """Kestrel (GPU)'s own rows (shown instead of Built-in BASSMIDI's while it's the MIDI out; the soundfont
        list is shared): Max voices, Lower it by itself, Block size, Limiter, DC blocker, its version."""
        from files import kestrel
        app, box = self.app, self
        vars_ = app.kestrel_vars
        before = set(box.grid_slaves())
        lb = ttk.Label(box, text=tr("ks.voices"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        row = ttk.Frame(box)  # (the box + its Default button side by side)
        row.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        e = app.kestrel_voices_entry = ttk.Entry(row, textvariable=vars_["voices"], width=10)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: (app.on_kestrel_opt("voices"), "break")[1])
        e.bind("<FocusOut>", lambda ev: app.on_kestrel_opt("voices"))
        Scrub(app, [(e, vars_["voices"], lambda: app.on_kestrel_opt("voices"))], (65536, 1048576, 1024),
              *kestrel.VOICES_RANGE, label=lb, no_undo=True)
        Tooltip(e, tr("ks.voices_tip"))
        Tooltip(lb, tr("ks.voices_tip"))

        def default():  # (this box only: user 2026-10-28, never mistaken for resetting every setting)
            vars_["voices"].set(str(kestrel.LIVE_VOICES))
            app.on_kestrel_opt("voices")
        b = ttk.Button(row, text=tr("ks.voices_default"), command=default, takefocus=False)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("ks.voices_default_tip", n=f"{kestrel.LIVE_VOICES:,}"))
        r += 1

        c = ttk.Checkbutton(box, text=tr("ks.lower"), variable=vars_["lower"],
                            command=lambda: app.on_kestrel_opt("lower"), takefocus=False)
        c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(c, tr("ks.lower_tip"))
        r += 1

        c = ttk.Checkbutton(box, text=tr("ks.slow_down"), variable=app.slow_kestrel, command=app.on_slow_down,
                            takefocus=False)
        c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(c, tr("ks.slow_down_tip"))
        r += 1

        lb = ttk.Label(box, text=tr("ks.block"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        names = [tr("ks.block_n", n=b, ms=f"{b / kestrel.RATE * 1000:.0f}") for b in kestrel.BLOCKS]
        cb = ttk.Combobox(box, values=names, state="readonly", width=14)
        cb.current(vars_["block"].get())
        cb.grid(row=r, column=1, sticky="w", pady=3)

        def picked(ev=None):
            vars_["block"].set(cb.current())  # (by place: translated names)
            app.on_kestrel_opt("block")
        cb.bind("<<ComboboxSelected>>", picked)
        vars_["block"].trace_add("write", lambda *a: cb.winfo_exists() and cb.current(vars_["block"].get()))
        Tooltip(cb, tr("ks.block_tip"))
        Tooltip(lb, tr("ks.block_tip"))
        r += 1

        for key, text in (("limiter", "ks.limiter"), ("dc_blocker", "ks.dc")):
            c = ttk.Checkbutton(box, text=tr(text), variable=vars_[key],
                                command=lambda k=key: app.on_kestrel_opt(k), takefocus=False)
            c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
            Tooltip(c, tr(text + "_tip"))
            r += 1

        r = self.card_row(r)
        r = self.choice_row(r, "interpolation", "ks.interp")
        r = self.choice_row(r, "steal", "ks.steal", order=(2, 0, 1))  # (Kestrel's usual first)
        r = self.number_row(r, "release_floor", "ks.floor", (-160, -20), tr("ks.floor_unit"))
        r = self.number_row(r, "min_velocity", "ks.min_vel", (0, 127))

        # (13g, user 2026-10-29: the rarer ones folded, shut at first; the fold is kept while Spiderweb runs)
        fold = ttk.Label(box, cursor="hand2", foreground=look.HINT)
        fold.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 3))
        Tooltip(fold, tr("ks.advanced_tip"))
        r += 1
        start = set(box.grid_slaves())
        r = self.choice_row(r, "admit", "ks.admit")
        r = self.number_row(r, "max_steal", "ks.max_steal", (1, 100), "%")
        r = self.number_row(r, "max_layers", "ks.max_layers", (1, 64))
        for key, text in (("filter", "ks.filter"), ("lfo", "ks.lfo"), ("mod_env", "ks.mod_env")):
            c = ttk.Checkbutton(box, text=tr(text), variable=vars_[key],
                                command=lambda k=key: app.on_kestrel_opt(k), takefocus=False)
            c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
            Tooltip(c, tr(text + "_tip"))
            r += 1
        self.advanced = [w for w in box.grid_slaves() if w not in start]
        self.fold = fold

        def flip(ev=None):
            app.kestrel_advanced = not getattr(app, "kestrel_advanced", False)
            self.sync_fold()
        fold.bind("<Button-1>", flip)

        v = kestrel.version()
        hint = ttk.Label(box, text=tr("ks.next_play") + ("\n" + tr("ks.version", v=v) if v else ""),
                         foreground=look.HINT, font=look.font(8), wraplength=round(320 * app.scale), justify="left")
        hint.grid(row=r, column=1, columnspan=2, sticky="w", pady=(2, 0))
        r += 1
        self.k_rows = [w for w in box.grid_slaves() if w not in before]
        self.kestrel_shown = True
        self.sync_fold()
        return r

    def sync_fold(self):
        """The Advanced rows shown while the fold is open (and Kestrel's rows show)."""
        open_ = getattr(self.app, "kestrel_advanced", False)
        self.fold.config(text=("▾ " if open_ else "▸ ") + tr("ks.advanced"))
        for w in self.advanced:
            w.grid() if open_ and self.kestrel_shown else w.grid_remove()

    def card_row(self, r):
        """Graphics card: Automatic + the cards Kestrel can play on (asked on a thread the first time: up to a
        second); a saved card not found now still shows (Play goes back to Automatic and says so)."""
        from files import kestrel
        app, box = self.app, self
        var = app.kestrel_vars["card"]
        lb = ttk.Label(box, text=tr("ks.card"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        cb = keep_width(ttk.Combobox(box, state="readonly", width=34))
        cb.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(cb, tr("ks.card_tip"))
        Tooltip(lb, tr("ks.card_tip"))
        ids = []

        def fill(found):
            if not cb.winfo_exists():
                return
            ids[:] = [""] + [kestrel.card_id(n, b) for n, b, _ in (found or [])]
            if var.get() not in ids:
                ids.append(var.get())
            cb.config(values=[kestrel.card_name(c) or tr("ks.card_auto") for c in ids])
            cb.current(ids.index(var.get()))

        def picked(ev=None):
            if ids:
                var.set(ids[cb.current()])
                app.on_kestrel_opt("card")
        cb.bind("<<ComboboxSelected>>", picked)
        var.trace_add("write", lambda *a: ids and cb.winfo_exists() and fill(kestrel.seen_cards()))
        if kestrel.seen_cards() is not None:
            fill(kestrel.seen_cards())
        else:
            cb.set(tr("ks.card_looking"))
            got = []
            threading.Thread(target=lambda: got.append(kestrel.cards()), daemon=True).start()

            def wait():
                if got:
                    fill(got[0])
                elif cb.winfo_exists():
                    cb.after(50, wait)
            cb.after(50, wait)
        return r + 1

    def choice_row(self, r, key, text, order=None):
        """A dropdown row of Kestrel's choices (kestrel.CHOICES; texts text_0, text_1...; order = the places shown,
        Kestrel's usual first)."""
        from files.kestrel import CHOICES
        app, box = self.app, self
        var = app.kestrel_vars[key]
        order = list(order or range(CHOICES[key]))
        lb = ttk.Label(box, text=tr(text))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        cb = ttk.Combobox(box, values=[tr(f"{text}_{i}") for i in order], state="readonly", width=18)
        cb.current(order.index(var.get()))
        cb.grid(row=r, column=1, sticky="w", pady=3)

        def picked(ev=None):
            var.set(order[cb.current()])  # (by place: translated names)
            app.on_kestrel_opt(key)
        cb.bind("<<ComboboxSelected>>", picked)
        var.trace_add("write", lambda *a: cb.winfo_exists() and cb.current(order.index(var.get())))
        Tooltip(cb, tr(text + "_tip"))
        Tooltip(lb, tr(text + "_tip"))
        return r + 1

    def number_row(self, r, key, text, span, unit=None):
        """A number box row (whole numbers in span, kestrel.RANGES) with its unit after it."""
        app, box = self.app, self
        var = app.kestrel_vars[key]
        lb = ttk.Label(box, text=tr(text))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        row = ttk.Frame(box)
        row.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        e = ttk.Entry(row, textvariable=var, width=8)
        e.pack(side="left")
        if not hasattr(app, "kestrel_entries"):
            app.kestrel_entries = {}
        app.kestrel_entries[key] = e
        e.bind("<Return>", lambda ev: (app.on_kestrel_opt(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: app.on_kestrel_opt(key))
        Scrub(app, [(e, var, lambda: app.on_kestrel_opt(key))], (1, 10, 1), *span, label=lb, no_undo=True)
        if unit:
            ttk.Label(row, text=unit).pack(side="left", padx=(4, 0))
        Tooltip(e, tr(text + "_tip"))
        Tooltip(lb, tr(text + "_tip"))
        return r + 1

    def show_for(self, kestrel):
        """Kestrel's rows (kestrel True) or Built-in BASSMIDI's; the soundfont list stays."""
        for w in self.bass_rows:
            w.grid_remove() if kestrel else w.grid()
        for w in self.k_rows:
            w.grid() if kestrel else w.grid_remove()
        self.kestrel_shown = kestrel
        self.sync_fold()

    def take_voices(self):
        """Settings closed (Esc, X) or another MIDI out picked: a voice limit typed without Enter is taken, a wrong
        one goes back to the limit in use."""
        app = self.app
        app.on_play_voices()
        if app.voices_var.get() != str(app.play_voices):
            app.voices_var.set(str(app.play_voices))
        app.on_loaded_synths()
        if app.synths_var.get() != str(app.loaded_synths):
            app.synths_var.set(str(app.loaded_synths))
        app.on_kestrel_opt("voices")  # (Kestrel's Max voices the same way)
        v = app.kestrel_vars["voices"]
        if v.get() != str(app.kestrel_opts["voices"]):
            v.set(str(app.kestrel_opts["voices"]))

    def refresh(self):
        if not self.winfo_exists():
            return
        self.fonts.refresh()
        text = self.app.overload_text
        if self.state.cget("text") != text:
            self.state.config(text=text)


class Overload:
    """Watches Built-in BASSMIDI while it plays, on its own thread (asking BASS can wait while it's busy making
    sound, and the window mustn't). Missing sound = the time passed minus the sound made (BASS plays the stream
    itself: what it couldn't make in time isn't heard). With guard, the voice limit is lowered while sound goes
    missing and raised slowly back to the user's limit once it's quiet again."""
    EVERY = 0.1  # seconds between looks
    WARM = 0.5  # sound missing this soon after playing starts doesn't count (hunt 2026-10-25: a synth just opened
    # loses its first moments; at the first play the limit was lowered and notes cut for 5 s)
    TOO_MUCH = 0.02  # seconds missing within the last second = overloaded (BASS makes sound in pieces: some wobble)
    DROP = 0.85  # the limit is multiplied by this while overloaded and still losing sound...
    DROP_WAIT = 0.3  # ...at most this often (a lower limit takes a moment to show)
    LOWEST = 50  # never lowered below this (a stall more voices can't fix: device unplugged, PC hiccup)
    # Climbing back (user: slowly while still in the spam, else it breaks up every few moments; quickly once free)
    FREE = 0.7  # free = the voices sounding stayed under this share of the limit for the last second...
    QUIET = 0.5  # ...and not overloaded for this long: half the way to the user's limit...
    CLIMB_WAIT = 0.2  # ...this often
    SLOW_QUIET = 5.0  # still busy: seconds not overloaded before the limit goes up...
    RISE = 1.05  # ...by this much a second

    def __init__(self):
        self.thread = None
        self._stop = threading.Event()
        self.text_args = None  # None, ("overloaded",) or ("lowered", the limit in use)
        self.kept = None  # (live, user's limit, limit in use) at the last stop, for a restart that keeps it
        self.load = None  # (13f, Slow down: the synth's last CPU reading / 100, None = none yet)
        self.over = False  # (... and sound missing within the last second, as "Overloaded" judges it)

    @property
    def running(self):
        return self.thread is not None

    def start(self, live, limit, guard, keep=False):
        """keep = playback started again at once (play line moved, new PPQ): goes on from the limit in use."""
        self.stop()
        kept, self.kept = self.kept, None
        self.live, self.limit, self.guard = live, limit, guard
        self.now_limit = limit
        if keep and guard and kept is not None and kept[:2] == (live, limit):
            self.now_limit = kept[2]
        self._sent = limit  # (a stopped synth is always at the user's limit)
        self._new_limit = False
        self.load, self.over = None, False
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_limit(self, limit):
        """The user typed another voice limit while playing: it starts again from there. Only the watcher's thread
        sends the limit to the synth while it plays (two senders could leave the wrong one)."""
        self.limit = limit
        self._new_limit = True

    def set_guard(self, on):
        self.guard = on  # (off: the thread puts the user's limit back)

    def stop(self):
        if self.thread is not None:
            self._stop.set()
            self.thread.join()
            self.thread = None
            lowered = self.guard and self.now_limit < self.limit
            self.kept = (self.live, self.limit, self.now_limit) if lowered else None
            if self._sent != self.limit:
                self.live.set_voices(self.limit)
        self.text_args = None

    def _run(self):
        live, clock = self.live, time.perf_counter
        t0, p0 = clock(), live.position()
        began = t0
        lost = collections.deque()  # (time, seconds missing since the look before; negative = sound ran ahead)
        busy = collections.deque()  # (time, voices sounding)
        last_drop = last_climb = last_over = t0
        since_drop = 0.0  # seconds missing since the last drop
        while not self._stop.wait(self.EVERY):
            t, p = clock(), live.position()
            gone = (t - t0) - (p - p0) / 8 / RATE
            if t - began < self.WARM:
                gone = 0.0
            t0, p0 = t, p
            self.load = live.cpu() / 100  # (Slow down's reading: read here, never on the window's thread)
            lost.append((t, gone))  # (with its sign: a device giving its position in steps evens out)
            while lost[0][0] < t - 1:
                lost.popleft()
            missing = max(0.0, sum(g for _, g in lost))
            busy.append((t, live.voices_playing()))
            while busy[0][0] < t - 1:
                busy.popleft()
            free = max(v for _, v in busy) < self.now_limit * self.FREE
            since_drop += gone
            over = self.over = missing > self.TOO_MUCH  # (Slow down's "broke up" too: a device giving its
            if over:  # position in steps swings both ways, only the sum counts, hunt 2026-10-29)
                last_over = t
            limit = self.limit
            if self._new_limit:
                self._new_limit = False
                self.now_limit = limit
            lowest = min(limit, self.LOWEST)
            if not self.guard:
                self.now_limit = limit
            elif over and since_drop > 0.003 and t - last_drop >= self.DROP_WAIT and self.now_limit > lowest:
                self.now_limit = max(lowest, int(self.now_limit * self.DROP))
                last_drop, since_drop = t, 0.0
            elif self.now_limit >= limit:
                pass
            elif free and t - last_over >= self.QUIET and t - last_climb >= self.CLIMB_WAIT:
                self.now_limit = min(limit, self.now_limit + max(1, (limit - self.now_limit + 1) // 2))
                last_climb = t
            elif t - last_over >= self.SLOW_QUIET and t - max(last_climb, last_drop) >= 1:
                self.now_limit = min(limit, int(self.now_limit * self.RISE) + 1)
                last_climb = t
            if self.now_limit != self._sent:
                live.set_voices(self.now_limit)
                self._sent = self.now_limit
            if not self.guard:
                self.text_args = ("overloaded",) if over else None
            elif self.now_limit < limit or over:
                self.text_args = ("lowered", self.now_limit)  # (user: the limit, climbing back once calm)
            else:
                self.text_args = None

    def text(self):
        a = self.text_args
        if a is None:
            return ""
        if a[0] == "overloaded":
            return tr("app.overloaded")
        m = f"{self.limit:,}"  # (of the limit you set, user 2026-10-29)
        return tr("app.overloaded_1", m=m) if a[1] == 1 else tr("app.overloaded_n", n=f"{a[1]:,}", m=m)
