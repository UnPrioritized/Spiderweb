"""The Copy along a path window (notes/copypath.py): the selected shapes copied along a path from A (where they start
in time) to B, shown live on the piano roll (the shared part: tool_window.py; the path's points on the piano roll:
roll/roll_path.py). Several shapes travel together as one group: each gets the same page, so the same moves.

Path kind (Straight / Curve / Arc), spacing (one copy every [note length] or a number of copies) and where B is,
as bar / beat / tick / key like the piano roll's status line, typed or dragged on the piano roll."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from files.snap import SNAP_LIST, snap_beats, snap_text
from notes.copypath import COUNT_LIMITS, EVERY_LIMITS, FAR, KINDS, PATH_DEFAULTS, clean_path, shifts
from roll.roll_shared import note_name
from window import look
from window.tool_window import ToolWindow
from window.widgets import Scrub, Tooltip, bad, good

LENGTHS = [s for s, _ in SNAP_LIST if s != "off"]  # "one copy every" choices (the snap list's note lengths)


class PathWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "path", "path_window", "path_pos", PATH_DEFAULTS
    loose = False  # B follows the mouse on the piano roll until a click places it

    def __init__(self, app):
        super().__init__(app)
        self.bind("<F1>", lambda e: (app.open_help(), "break")[1])  # (its own Help page comes with the last group)

    def clean(self, cfg):
        return clean_path(cfg)

    def build(self, box):
        s = self.app.scale
        self.vars, self.entries = {}, {}
        box.columnconfigure(0, minsize=round(80 * s))
        ttk.Label(box, text=tr("path.kind")).grid(row=1, column=0, sticky="e", padx=(0, 8), pady=2)
        self.kind_box = ttk.Combobox(box, state="readonly", width=12, values=[tr(f"path.{k}") for k in KINDS])
        self.kind_box.grid(row=1, column=1, sticky="w", pady=2)
        self.kind_box.bind("<<ComboboxSelected>>", lambda e: self.put_kind(KINDS[e.widget.current()]))
        Tooltip(self.kind_box, tr("path.tip_kind"))

        ttk.Label(box, text=tr("path.spacing")).grid(row=2, column=0, sticky="ne", padx=(0, 8), pady=(6, 2))
        sp = ttk.Frame(box)
        sp.grid(row=2, column=1, sticky="w", pady=(6, 2))
        self.mode = tk.StringVar(value="every")
        every = ttk.Radiobutton(sp, text=tr("path.every"), variable=self.mode, value="every",
                                command=lambda: self.put("mode", "every"))
        every.grid(row=0, column=0, sticky="w")
        Tooltip(every, tr("path.tip_every"))
        self.length_box = ttk.Combobox(sp, state="readonly", width=8, values=[snap_text(v) for v in LENGTHS])
        self.length_box.grid(row=0, column=1, sticky="w", padx=(4, 4))
        self.length_box.bind("<<ComboboxSelected>>", lambda e: self.put_length(LENGTHS[e.widget.current()]))
        Tooltip(self.length_box, tr("path.tip_every"))
        self.number(sp, 0, 2, "every", tr("path.tip_every_ticks"), (1, 10, 1))
        ttk.Label(sp, text=tr("unit.ticks"), foreground=look.HINT).grid(row=0, column=3, sticky="w", padx=(4, 0))
        count = ttk.Radiobutton(sp, text=tr("path.count"), variable=self.mode, value="count",
                                command=lambda: self.put("mode", "count"))
        count.grid(row=1, column=0, sticky="w", pady=(4, 0))
        Tooltip(count, tr("path.tip_count"))
        self.number(sp, 1, 1, "count", tr("path.tip_count"), (1, 10, 1))

        ttk.Label(box, text=tr("path.end")).grid(row=3, column=0, sticky="e", padx=(0, 8), pady=(6, 2))
        end = ttk.Frame(box)
        end.grid(row=3, column=1, sticky="w", pady=(6, 2))
        for c, key, steps in ((0, "bar", (1, 4, 1)), (2, "beat", (1, 4, 1)), (4, "tick", (1, 10, 1)),
                              (6, "key", (1, 12, 1))):
            lb = ttk.Label(end, text=tr(f"path.{key}"), foreground=look.HINT)
            lb.grid(row=0, column=c, sticky="e", padx=(8 if c else 0, 3))
            self.number(end, 0, c + 1, key, tr(f"path.tip_{key}"), steps, label=lb, width=5)
        self.key_name = ttk.Label(end, text="", foreground=look.HINT, width=6)
        self.key_name.grid(row=0, column=8, sticky="w", padx=(4, 0))

        self.info = ttk.Label(box, text="", foreground=look.HINT, wraplength=round(440 * s))  # (the window keeps its size)
        self.info.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("path.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("path.tip_reset"))
        ttk.Button(row, text=tr("path.accept"), command=self.accept).pack(side="right")

    def number(self, parent, r, c, key, tip, steps, label=None, width=6):
        var = self.vars[key] = tk.StringVar()
        e = self.entries[key] = ttk.Entry(parent, textvariable=var, width=width, justify="center")
        e.grid(row=r, column=c, sticky="w", pady=(4 if r else 0, 0))
        e.bind("<Return>", lambda ev: (self.on_entry(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_entry(key))
        Scrub(self.app, [(e, var, lambda: self.on_entry(key, False))], steps, label=label, drag_box=label is None)
        Tooltip(e, tip)

    # ---- where A and B are

    def anchor(self):
        """A: (beat where the selected shapes start, their middle key), or None (nothing drawn)."""
        r = self.app.roll.reach([self.app.shapes[i] for i in self.targets if i < len(self.app.shapes)])
        return None if r is None else (r[0], (r[1] + r[2]) / 2)

    def end_point(self):
        """B as (beat, key) on the piano roll, or None (not placed)."""
        a, b = self.anchor(), self.cfg.get("b")
        return None if a is None or b is None else (a[0] + b[0], a[1] + b[1])

    def placing(self):
        """Is B still to be clicked on the piano roll? (Until then it follows the mouse there: follow.)"""
        return bool(self.targets) and (self.cfg.get("b") is None or self.loose)

    def follow(self, beat, key, k):
        """B, not placed yet, follows the mouse on the piano roll: the copies show there (no step inside)."""
        a = self.anchor()
        if a is None:
            return
        self.loose = True
        self.cfg["b"], self.cfg["k"] = [round(beat - a[0], 9), round(key - a[1], 9)], k
        self.show()
        self.preview(False)

    def retarget(self):
        self.loose = False
        super().retarget()

    def pick_page(self, k):
        self.loose = False
        super().pick_page(k)

    def new_page(self):
        self.loose = False
        super().new_page()

    def put_state(self, state):
        self.loose = False
        super().put_state(state)

    def reset(self):
        self.loose = False
        super().reset()

    def set_point(self, which, beat, key, k, done):
        """B ("b") or the middle point ("mid") put at (beat, key) on the piano roll (from the roll: k = beats per key
        on screen now). The middle point of a straight path makes it a curve. B clicked while it follows the mouse:
        placed there."""
        a = self.anchor()
        if a is None:
            return
        if which == "b" and self.placing():
            self.loose = False
            self.cfg["b"] = None
        pt = [round(beat - a[0], 9), round(key - a[1], 9)]
        if which == "mid":
            self.cfg["mid"] = pt
            if self.cfg["kind"] == "straight":
                self.cfg["kind"] = "curve"
        else:
            if self.cfg.get("b") is not None and self.cfg.get("mid") is not None:  # (the bend kept as it was)
                old, m = self.cfg["b"], self.cfg["mid"]
                self.cfg["mid"] = [m[0] + (pt[0] - old[0]) / 2, m[1] + (pt[1] - old[1]) / 2]
            self.cfg["b"] = pt
        self.cfg["k"] = k
        self.show()
        self.preview(done)
        self.undo.mark(None if done else which)

    # ---- settings

    def put_kind(self, kind):
        self.cfg["kind"] = kind
        if kind == "straight":
            self.cfg["mid"] = None
        self.put("kind", kind)

    def put_length(self, snap):
        self.put("every", snap_beats(snap, self.app.beats))

    def on_entry(self, key, done=True):
        e, var = self.entries[key], self.vars[key]
        try:
            v = float(calc(var.get()))
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        ppq = self.app.ppq
        if key == "every":
            v /= ppq
            ok = EVERY_LIMITS[0] <= v <= EVERY_LIMITS[1] and v * ppq >= 1
        elif key == "count":
            ok = COUNT_LIMITS[0] <= v <= COUNT_LIMITS[1] and v == int(v)
            v = int(v)
        else:
            ok = self.end_point() is not None and v == int(v)
        if not ok:
            bad(e)
            return
        good(e)
        if key in ("every", "count"):
            if v != self.cfg[key]:
                if key == "every":
                    self.cfg["mode"] = "every"
                elif key == "count":
                    self.cfg["mode"] = "count"
                self.put(key, v, done)
            return
        beat, p = self.end_point()
        bar, b, t = self.bar_beat_tick(beat)
        bar, b, t, p = {"bar": (v, b, t, p), "beat": (bar, v, t, p), "tick": (bar, b, v, p),
                        "key": (bar, b, t, v)}[key]
        beat = ((bar - 1) * self.app.beats + (b - 1)) + t / ppq
        a = self.anchor()
        if abs(beat - a[0]) > FAR or abs(p - a[1]) > FAR:
            bad(e)
            return
        if (beat, p) != self.end_point():
            self.set_point("b", beat, p, self.cfg["k"], done)

    def bar_beat_tick(self, beat):
        """beat -> (bar, beat in it, tick in it) as the status line shows them (bar and beat from 1); before the
        song's start: bar 0 and less."""
        ppq, beats = self.app.ppq, self.app.beats
        ticks = round(beat * ppq)
        bar, rest = divmod(ticks, ppq * beats)
        return bar + 1, rest // ppq + 1, rest % ppq

    def show(self):
        """The window shows self.cfg (only what differs is changed)."""
        c = self.cfg

        def put_text(key, text):
            if self.vars[key].get() != text:
                self.vars[key].set(text)
            if str(self.entries[key].cget("style")) != "TEntry":
                self.entries[key].config(style="TEntry")

        kind = tr(f"path.{c['kind']}")
        if self.kind_box.get() != kind:
            self.kind_box.set(kind)
        if self.mode.get() != c["mode"]:
            self.mode.set(c["mode"])
        ppq = self.app.ppq
        length = next((snap_text(v) for v in LENGTHS if abs(snap_beats(v, self.app.beats) - c["every"]) < 1e-9), "")
        if self.length_box.get() != length:
            self.length_box.set(length)
        put_text("every", fmt(round(c["every"] * ppq, 6)))
        put_text("count", str(c["count"]))
        end = self.end_point()
        bar_beat = self.bar_beat_tick(end[0]) if end else None
        for key, v in zip(("bar", "beat", "tick", "key"), (*bar_beat, end[1]) if end else (None,) * 4):
            put_text(key, "" if v is None else fmt(round(v, 6)))
            state = "normal" if end else "disabled"
            if str(self.entries[key].cget("state")) != state:
                self.entries[key].config(state=state)
        name = note_name(round(end[1]), self.app.edo()) if end and 0 <= round(end[1]) < 256 else ""
        if self.key_name.cget("text") != name:
            self.key_name.config(text=name)
        if not self.targets:
            info = ""
        elif self.placing():
            info = tr("path.click_b")
        else:
            cl = clean_path(c)
            info = tr("path.n_copies", n=len(shifts(cl, ppq))) if cl else ""
        if self.info.cget("text") != info:
            self.info.config(text=info)
        self.app.roll.request_redraw()

    def preview(self, now=True):
        super().preview(now)
        self.app.roll.request_redraw()

    def close(self):
        super().close()
        self.app.roll.request_redraw()


open_path = PathWindow.open
