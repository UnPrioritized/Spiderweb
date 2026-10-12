"""The Copy along a path window (notes/copypath.py): the selected shapes copied along a path from A (where their notes
start in time, at their middle key) to B, shown live on the piano roll (the shared part: tool_window.py; the path's
points on the piano roll: roll/roll_path.py). Several shapes travel together as one group: each gets the same page,
plus where the group's box is from its own notes ("grp", page_cfg), so sizes and flips work on the whole group.

Path kind (Straight / Curve / Arc), spacing (one copy every [note length] or a number of copies), Speed, Size,
Colours (+ Step), Flip (+ which way), On top, and where B is as bar / beat / tick / key like the piano roll's status
line, typed or dragged on the piano roll."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from files.snap import SNAP_LIST, snap_beats, snap_text
from notes.copypath import (COLOURS, COUNT_LIMITS, EVERY_LIMITS, FAR, FLIPS, KINDS, PATH_DEFAULTS, SIZE_LIMITS,
                            SPEEDS, STEP_LIMITS, TOPS, WAYS, clean_path, peak, shifts)
from roll.roll_shared import note_name
from window import look
from window.tool_window import ToolWindow
from window.widgets import Scrub, Tooltip, bad, good, grid_shown

LENGTHS = [s for s, _ in SNAP_LIST if s != "off"]  # "one copy every" choices (the snap list's note lengths)
CHOICES = {"kind": KINDS, "speed": SPEEDS, "colours": COLOURS, "flip": FLIPS, "way": WAYS, "top": TOPS}
GROUP_ONLY = ("grp", "most")  # (page settings that may differ between the shapes of one group)


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
        self.vars, self.entries, self.combos = {}, {}, {}
        box.columnconfigure(0, minsize=round(80 * s))
        self.choice(box, 1, "kind", tr("path.kind"))

        self.label(box, 2, tr("path.spacing"))
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
        self.choice(box, 3, "speed", tr("path.speed"))

        self.label(box, 4, tr("path.size"))
        size = ttk.Frame(box)
        size.grid(row=4, column=1, sticky="w", pady=(6, 2))
        for c, key in ((0, "size0"), (3, "size1")):
            self.number(size, 0, c, key, tr(f"path.tip_{key}"), (1, 10, 0.1))
            ttk.Label(size, text=tr("unit.percent"), foreground=look.HINT).grid(row=0, column=c + 1, sticky="w",
                                                                              padx=(3, 0))
        ttk.Label(size, text="→", foreground=look.HINT).grid(row=0, column=2, padx=6)

        colours = self.choice(box, 5, "colours", tr("path.colours"))
        lb = ttk.Label(colours, text=tr("path.step"), foreground=look.HINT)
        lb.grid(row=0, column=1, sticky="e", padx=(10, 3))
        self.number(colours, 0, 2, "step", tr("path.tip_step"), (1, 5, 1), label=lb, width=4)
        self.colour_note = ttk.Label(box, text="", foreground=look.WARN, wraplength=round(440 * s))
        self.colour_note.grid(row=6, column=1, sticky="w")
        flip = self.choice(box, 7, "flip", tr("path.flip"))
        self.choice(flip, 0, "way", None, column=1)
        self.choice(box, 8, "top", tr("path.top"))

        self.label(box, 9, tr("path.end"))
        end = ttk.Frame(box)
        end.grid(row=9, column=1, sticky="w", pady=(6, 2))
        for c, key, steps in ((0, "bar", (1, 4, 1)), (2, "beat", (1, 4, 1)), (4, "tick", (1, 10, 1)),
                              (6, "key", (1, 12, 1))):
            lb = ttk.Label(end, text=tr(f"path.{key}"), foreground=look.HINT)
            lb.grid(row=0, column=c, sticky="e", padx=(8 if c else 0, 3))
            self.number(end, 0, c + 1, key, tr(f"path.tip_{key}"), steps, label=lb, width=5)
        self.key_name = ttk.Label(end, text="", foreground=look.HINT, width=6)
        self.key_name.grid(row=0, column=8, sticky="w", padx=(4, 0))

        self.info = ttk.Label(box, text="", foreground=look.HINT, wraplength=round(440 * s))  # (the window keeps its size)
        self.info.grid(row=10, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("path.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("path.tip_reset"))
        ttk.Button(row, text=tr("path.accept"), command=self.accept).pack(side="right")

    def label(self, box, r, text):
        ttk.Label(box, text=text).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=(6, 2))

    def choice(self, box, r, key, text, column=0):
        """A row with a dropdown (CHOICES[key], texts path.<choice>); returns its frame (more can go beside it)."""
        if text is not None:
            self.label(box, r, text)
        f = ttk.Frame(box)
        f.grid(row=r, column=column + 1, sticky="w", pady=(6, 2), padx=(10, 0) if column else 0)
        c = self.combos[key] = ttk.Combobox(f, state="readonly", width=16,
                                            values=[tr(f"path.{v}") for v in CHOICES[key]])
        c.grid(row=0, column=0, sticky="w")
        c.bind("<<ComboboxSelected>>", lambda e: self.put_choice(key, CHOICES[key][e.widget.current()]))
        Tooltip(c, tr(f"path.tip_{key}"))
        return f

    def number(self, parent, r, c, key, tip, steps, label=None, width=6):
        var = self.vars[key] = tk.StringVar()
        e = self.entries[key] = ttk.Entry(parent, textvariable=var, width=width, justify="center")
        e.grid(row=r, column=c, sticky="w", pady=(4 if r else 0, 0))
        e.bind("<Return>", lambda ev: (self.on_entry(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_entry(key))
        Scrub(self.app, [(e, var, lambda: self.on_entry(key, False))], steps, label=label, drag_box=label is None)
        Tooltip(e, tip)

    # ---- the group: where A is, each shape's page

    def before_notes(self, i):
        """Shape i's notes before this page (all of them while it's still a new one)."""
        sh = self.app.shapes[i]
        fx = sh.get("fx") or []
        k = self.at.get(i, len(fx)) if self.at else len(fx)
        bare = {key: v for key, v in sh.items() if key != "fx"}
        return self.app.notes_of(dict(bare, fx=fx[:k]) if k else bare)

    def boxes(self):
        """{shape: (first start, last end, lowest key, highest key)} of the selected shapes' notes before this page."""
        out = {}
        for i in self.targets:
            if i < len(self.app.shapes):
                a = self.before_notes(i)
                if len(a):
                    out[i] = (int(a[:, 0].min()), int(a[:, 1].max()), int(a[:, 2].min()), int(a[:, 2].max()))
        return out

    def group(self):
        boxes = self.boxes()
        if not boxes:
            return None
        b = list(boxes.values())
        return (min(x[0] for x in b), max(x[1] for x in b), min(x[2] for x in b), max(x[3] for x in b)), boxes

    def anchor(self):
        """A: (beat where the selected shapes' notes start, their middle key), or None (no notes)."""
        g = self.group()
        return None if g is None else (g[0][0] / self.app.ppq, (g[0][2] + g[0][3]) / 2)

    def page_cfg(self, i, cfg):
        """Shape i's page: where the group's box is from its own notes, and how many colours there are."""
        if cfg is None:
            return None
        g = self.group()
        own = g and g[1].get(i)
        ppq = self.app.ppq
        grp = [(g[0][0] - own[0]) / ppq, (g[0][1] - own[1]) / ppq, g[0][2] - own[2], g[0][3] - own[3]] if own else \
            [0.0, 0.0, 0.0, 0.0]
        return dict(cfg, grp=grp, most=16 if self.app.all16() else 15)

    def alike(self, fx):
        return [dict(st, cfg={k: v for k, v in st["cfg"].items() if k not in GROUP_ONLY})
                if st["tool"] == self.KEY and st.get("cfg") else st for st in fx]

    # ---- where B is

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

    def put_choice(self, key, value):
        if key == "kind" and value == "straight":
            self.cfg["mid"] = None
        self.put(key, value)

    def put_length(self, snap):
        self.cfg["mode"] = "every"
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
        elif key in ("size0", "size1"):
            ok = SIZE_LIMITS[0] <= v <= SIZE_LIMITS[1]
        elif key == "step":
            ok = STEP_LIMITS[0] <= v <= STEP_LIMITS[1] and v == int(v)
            v = int(v)
        else:
            ok = self.end_point() is not None and v == int(v)
        if not ok:
            bad(e)
            return
        good(e)
        if key in ("size0", "size1"):
            size = list(self.cfg["size"])
            size[key == "size1"] = v
            if size != self.cfg["size"]:
                self.put("size", size, done)
            return
        if key in ("every", "count", "step"):
            if v != self.cfg[key]:
                if key in ("every", "count"):
                    self.cfg["mode"] = key
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

        def config(w, **opts):
            if any(str(w.cget(k)) != str(v) for k, v in opts.items()):
                w.config(**opts)

        for key, box in self.combos.items():
            text = tr(f"path.{c[key]}")
            if box.get() != text:
                box.set(text)
        if self.mode.get() != c["mode"]:
            self.mode.set(c["mode"])
        ppq = self.app.ppq
        length = next((snap_text(v) for v in LENGTHS if abs(snap_beats(v, self.app.beats) - c["every"]) < 1e-9), "")
        if self.length_box.get() != length:
            self.length_box.set(length)
        put_text("every", fmt(round(c["every"] * ppq, 6)))
        put_text("count", str(c["count"]))
        put_text("size0", fmt(round(c["size"][0], 6)))
        put_text("size1", fmt(round(c["size"][1], 6)))
        put_text("step", str(c["step"]))
        config(self.entries["step"], state="normal" if c["colours"] != "keep" else "disabled")
        config(self.combos["way"], state="readonly" if c["flip"] == "each" else "disabled")
        end = self.end_point()
        bar_beat = self.bar_beat_tick(end[0]) if end else None
        for key, v in zip(("bar", "beat", "tick", "key"), (*bar_beat, end[1]) if end else (None,) * 4):
            put_text(key, "" if v is None else fmt(round(v, 6)))
            config(self.entries[key], state="normal" if end else "disabled")
        name = note_name(round(end[1]), self.app.edo()) if end and 0 <= round(end[1]) < 256 else ""
        config(self.key_name, text=name)
        multi = self.app.channel_mode.get() == "auto"
        note = "" if c["colours"] == "keep" or multi else tr("path.needs_multi")
        config(self.colour_note, text=note)
        grid_shown(self.colour_note, bool(note))  # (no empty row while there's nothing to say)
        if not self.targets:
            info = ""
        elif self.placing():
            info = tr("path.click_b")
        else:
            cl = clean_path(c)
            info = tr("path.n_copies", n=len(shifts(cl, ppq))) if cl else ""
            if cl and cl["flip"] == "mirror" and peak(cl) is None:
                info += "\n" + tr("path.no_peak")
        config(self.info, text=info)
        self.app.roll.request_redraw()

    def preview(self, now=True):
        super().preview(now)
        self.app.roll.request_redraw()

    def close(self):
        super().close()
        self.app.roll.request_redraw()


open_path = PathWindow.open
