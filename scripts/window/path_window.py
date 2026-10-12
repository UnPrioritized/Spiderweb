"""The Copy along a path window (notes/copypath.py): the selected shapes copied along a path through points A (a corner
of their notes' box), B, C, ..., shown live on the piano roll (the shared part: tool_window.py; the path's points on
the piano roll: roll/roll_path.py). Several shapes travel together as one group: each gets the same page, plus where
the group's box is from its own notes ("grp", page_cfg), so corners, sizes and flips work on the whole group.

Path kind (Straight / Curve / Arc), spacing (one copy every [note length] or a number of copies), Speed, Size,
Colours (+ Step), Flip (+ which way), On top, and where the picked point is as bar / beat / tick / key like the piano
roll's status line, typed or dragged on the piano roll; Add point (it follows the mouse until a click places it)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from files.snap import SNAP_LIST, snap_beats, snap_text
from notes.copypath import (COLOURS, COUNT_LIMITS, EVERY_LIMITS, FAR, FLIPS, KINDS, LETTERS, MOST_POINTS,
                            PATH_DEFAULTS, SIZE_LIMITS, SPEEDS, STEP_LIMITS, TOPS, WAYS, all_points, clean_path, peak,
                            shifts)
from roll.roll_shared import note_name
from window import big_ask, look
from window.tool_window import ToolWindow
from window.widgets import Scrub, Tooltip, bad, good, grid_shown

LENGTHS = [s for s, _ in SNAP_LIST if s != "off"]  # "one copy every" choices (the snap list's note lengths)
CHOICES = {"kind": KINDS, "speed": SPEEDS, "colours": COLOURS, "flip": FLIPS, "way": WAYS, "top": TOPS}
GROUP_ONLY = ("grp", "most")  # (page settings that may differ between the shapes of one group)


class PathWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "path", "path_window", "path_pos", PATH_DEFAULTS
    loose = False  # the last point follows the mouse on the piano roll until a click places it
    adding = False  # Add point pressed: the new point comes with the mouse's first move on the piano roll
    idle = False  # another shape selected meanwhile: no new path until the menu / + asks for one (user)
    taking = False  # (take_typing at work)
    est = 0  # notes the shown settings make, about (asked past big_ask.BIG)
    pick = 0  # the point the bar / beat / tick / key boxes show (0 = B)

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
        self.ticks = {}
        for r, key in ((2, "anchor"), (3, "half")):
            var = tk.BooleanVar(value=True)
            tick = ttk.Checkbutton(sp, text=tr(f"path.{key}"), variable=var,
                                   command=lambda k=key, v=var: self.put(k, v.get()))
            tick.grid(row=r, column=0, columnspan=4, sticky="w", pady=(4, 0))
            Tooltip(tick, tr(f"path.tip_{key}"))
            self.ticks[key] = (tick, var)
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
        lb = ttk.Label(colours, text=tr("path.colour_step"), foreground=look.HINT)
        lb.grid(row=0, column=1, sticky="e", padx=(10, 3))
        self.number(colours, 0, 2, "step", tr("path.tip_step"), (1, 5, 1), label=lb, width=4)
        self.colour_note = ttk.Label(box, text="", foreground=look.WARN, wraplength=round(440 * s))
        self.colour_note.grid(row=6, column=1, sticky="w")
        flip = self.choice(box, 7, "flip", tr("path.flip"))
        self.choice(flip, 0, "way", None, column=1)
        self.choice(box, 8, "top", tr("path.top"))

        self.point_label = self.label(box, 9, LETTERS[1])
        Tooltip(self.point_label, tr("path.tip_point"))
        end = ttk.Frame(box)
        end.grid(row=9, column=1, sticky="w", pady=(6, 2))
        for c, key, steps in ((0, "bar", (1, 4, 1)), (2, "beat", (1, 4, 1)), (4, "tick", (1, 10, 1)),
                              (6, "key", (1, 12, 1))):
            lb = ttk.Label(end, text=tr(f"path.{key}"), foreground=look.HINT)
            lb.grid(row=0, column=c, sticky="e", padx=(8 if c else 0, 3))
            self.number(end, 0, c + 1, key, tr(f"path.tip_{key}"), steps, label=lb, width=5)
        self.key_name = ttk.Label(end, text="", foreground=look.HINT, width=6)
        self.key_name.grid(row=0, column=8, sticky="w", padx=(4, 0))
        self.add_button = ttk.Button(end, text=tr("path.add_point"), command=self.add_point)
        self.add_button.grid(row=0, column=9, sticky="w", padx=(6, 0))
        Tooltip(self.add_button, tr("path.tip_add_point"))

        self.info = ttk.Label(box, text="", foreground=look.HINT, wraplength=round(440 * s))  # (the window keeps its size)
        self.info.grid(row=10, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("path.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("path.tip_reset"))
        ttk.Button(row, text=tr("path.accept"), command=self.accept).pack(side="right")

    def label(self, box, r, text):
        lb = ttk.Label(box, text=text)
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=(6, 2))
        return lb

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

    # ---- the group: its box, each shape's page

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

    def frame(self):
        """The group's box as ((beat it starts, lowest key), (beats, keys) it spans), or None (no notes). The points
        are kept from its start, lowest key."""
        g = self.group()
        if g is None:
            return None
        (t0, t1, lo, hi), ppq = g[0], self.app.ppq
        return (t0 / ppq, lo), ((t1 - t0) / ppq, hi - lo)

    def points(self):
        """A, B, C, ... as (beat, key) on the piano roll, or None (B not placed / no notes)."""
        fr, cl = self.frame(), clean_path(self.cfg)
        if fr is None or cl is None:
            return None
        (b0, k0), size = fr
        return [(b0 + b, k0 + p) for b, p in all_points(cl, size)]

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

    # ---- the points

    def placing(self):
        """Is a point still to be clicked on the piano roll (B, or one added)? (Until then it follows the mouse
        there: follow.)"""
        return bool(self.targets) and not self.idle and (not self.cfg.get("pts") or self.loose or self.adding)

    def lists(self):
        """Copies of the points and the middle points (as many)."""
        pts = [list(p) for p in self.cfg.get("pts") or []]
        mids = list(self.cfg.get("mids") or [])
        return pts, [None if m is None else list(m) for m in (mids + [None] * len(pts))[:len(pts)]]

    def from_box(self, beat, key):
        """(beat, key) on the piano roll -> from the group's start, lowest key; or None (no notes)."""
        fr = self.frame()
        return None if fr is None else [round(beat - fr[0][0], 9), round(key - fr[0][1], 9)]

    def take_typing(self):
        """A number typed in a box without Enter counts before a piano roll click / move acts (user)."""
        if self.taking:
            return
        try:
            f = self.focus_get()
        except (KeyError, tk.TclError):
            return
        for key, e in self.entries.items():
            if e is f:
                self.taking = True
                try:
                    self.on_entry(key)
                finally:
                    self.taking = False
                return

    def drop_loose(self):
        """A point never clicked (still following the mouse) goes; B too, and then its path page (user)."""
        loose, self.adding = self.loose, False
        if not loose:
            return
        self.loose = False
        pts, mids = self.lists()
        if len(pts) > 1:
            self.cfg.update(pts=pts[:-1], mids=mids[:-1])
            self.pick = min(self.pick, len(pts) - 2)
            self.preview()
        else:
            self.cfg.update(pts=None, mids=None)
            if self.at is not None:
                ToolWindow.remove_page(self)

    def accept(self):
        self.drop_loose()
        super().accept()

    def sync(self):
        if sorted(self.app.note_tool_sels()) != self.targets:  # (another selection: a point not clicked goes)
            self.drop_loose()
        super().sync()

    def follow(self, beat, key, k):
        """The point still to be placed follows the mouse on the piano roll: the copies show there (no step inside)."""
        self.take_typing()
        pt = self.from_box(beat, key)
        if pt is None:
            return
        pts, mids = self.lists()
        if self.loose:
            pts[-1] = pt
        else:
            pts.append(pt)
            mids.append(None)
        self.loose, self.adding = True, False
        self.pick = len(pts) - 1
        self.cfg.update(pts=pts, mids=mids, k=k)
        self.show()
        self.preview(False)

    def place(self, beat, key, k):
        """A click on the piano roll places the point following the mouse (B, or one added)."""
        self.take_typing()
        pt = self.from_box(beat, key)
        if pt is None:
            return
        pts, mids = self.lists()
        if self.loose:
            pts[-1] = pt
        else:
            pts.append(pt)
            mids.append(None)
        self.loose = self.adding = False
        self.pick = len(pts) - 1
        self.cfg.update(pts=pts, mids=mids, k=k)
        self.show()
        self.preview()
        self.undo.mark()

    def add_point(self):
        """Add point: a new point after the last one follows the mouse on the piano roll until a click."""
        if self.cfg.get("pts") and not self.placing() and len(self.cfg["pts"]) < MOST_POINTS:
            self.adding = True
            self.show()

    def stop_adding(self):
        """Right-click while an added point follows the mouse: it's dropped. True if so (B can't be)."""
        if not self.cfg.get("pts") or not (self.loose or self.adding) or (self.loose and len(self.cfg["pts"]) < 2):
            return False
        if self.loose:
            pts, mids = self.lists()
            self.cfg.update(pts=pts[:-1], mids=mids[:-1])
        self.loose = self.adding = False
        self.pick = min(self.pick, len(self.cfg["pts"]) - 1)
        self.show()
        self.preview()
        return True

    def remove_point(self, i):
        """Point i (0 = B) right-clicked: it goes, its two stretches become one straight one. Not the only one."""
        pts, mids = self.lists()
        if len(pts) < 2 or self.placing():
            return
        del pts[i]
        mids = mids[:i] + ([None] if i < len(pts) else []) + mids[i + 2:]
        self.pick = min(self.pick if self.pick < i else max(0, self.pick - 1), len(pts) - 1)
        self.cfg.update(pts=pts, mids=mids)
        self.show()
        self.preview()
        self.undo.mark()

    def calm(self):
        self.loose = self.adding = self.idle = False
        self.pick = 0

    @classmethod
    def open(cls, app):
        """From the right-click menu: a new path for the selected shapes (the window opened, or brought up)."""
        super().open(app)
        w = getattr(app, cls.ATTR)
        if w and (w.at is not None or w.idle):
            w.new_page()

    def retarget(self):
        """Another selection (user: no new path unless asked for in the menu / with +): its last path shows, or
        nothing waits for a click."""
        self.calm()
        super().retarget()
        self.show_last()

    def remove_page(self):
        """A path taken off (its tab's ×): the last one left shows, or nothing waits for a click (user: no new path
        made by moving the mouse)."""
        if self.at is None:
            return
        super().remove_page()
        self.show_last()

    def show_last(self):
        """The selected shapes' last path page shows; none: nothing waits for a click."""
        self.calm()
        last = [k for k, st in enumerate(self.first_fx()) if st["tool"] == self.KEY]
        if last and self.same():
            self.pick_page(last[-1])
        else:
            self.idle = bool(self.targets)
            self.show()

    def pick_page(self, k):
        self.calm()
        super().pick_page(k)
        if self.cfg.get("pts") is None and self.cfg.get("b") is not None:  # (the first days' pages)
            self.cfg = clean_path(self.cfg) or self.cfg
            self.show()

    def new_page(self):
        self.drop_loose()
        self.calm()
        super().new_page()

    def put_state(self, state):
        self.loose = self.adding = False
        super().put_state(state)

    def reset(self):
        self.calm()
        super().reset()

    def set_point(self, which, beat, key, k, done):
        """A point ("pt", i: 0 = B) or a stretch's middle point ("mid", i) put at (beat, key) on the piano roll
        (k = beats per key on screen now). A middle point on a straight path makes it a curve; a point moved takes
        the bends beside it along by half as much."""
        self.take_typing()
        pt = self.from_box(beat, key)
        if pt is None:
            return
        what, i = which
        pts, mids = self.lists()
        if what == "mid":
            if self.cfg["kind"] == "straight":  # (bends hidden by Straight stay straight: only this one bends)
                self.cfg["kind"] = "curve"
                mids = [None] * len(mids)
            mids[i] = pt
        else:
            dx, dy = pt[0] - pts[i][0], pt[1] - pts[i][1]
            for s in (i, i + 1):  # (the bends kept as they were)
                if s < len(mids) and mids[s] is not None:
                    mids[s] = [mids[s][0] + dx / 2, mids[s][1] + dy / 2]
            pts[i] = pt
            self.pick = i
        self.cfg.update(pts=pts, mids=mids, k=k)
        self.show()
        self.preview(done)
        self.undo.mark(None if done else which)

    # ---- settings

    def put_choice(self, key, value):
        self.put(key, value)  # (Straight keeps the bends, hidden: Curve / Arc bring them back; user)

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
            ok = self.picked() is not None and v == int(v)
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
        beat, p = self.picked()
        bar, b, t = self.bar_beat_tick(beat)
        bar, b, t, p = {"bar": (v, b, t, p), "beat": (bar, v, t, p), "tick": (bar, b, v, p),
                        "key": (bar, b, t, v)}[key]
        beat = ((bar - 1) * self.app.beats + (b - 1)) + t / ppq
        base = self.frame()[0]
        if abs(beat - base[0]) > FAR or abs(p - base[1]) > FAR:
            bad(e)
            return
        if (beat, p) != self.picked():
            self.set_point(("pt", self.pick), beat, p, self.cfg["k"], done)

    def picked(self):
        """The picked point as (beat, key) on the piano roll, or None (not placed)."""
        pts = self.points()
        return pts[min(self.pick, len(pts) - 2) + 1] if pts else None

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
        for key, (tick, var) in self.ticks.items():
            if var.get() != c[key]:
                var.set(c[key])
        config(self.ticks["half"][0], state="normal" if c["speed"] == "even" and c["anchor"] else "disabled")
        config(self.entries["step"], state="normal" if c["colours"] != "keep" else "disabled")
        config(self.combos["way"], state="readonly" if c["flip"] == "each" else "disabled")
        n = len(c.get("pts") or [])
        self.pick = max(0, min(self.pick, n - 1))
        adding = self.adding or (self.loose and n > 1)
        config(self.point_label, text=LETTERS[(n if self.adding else self.pick) + 1])
        end = self.picked() if not self.adding else None
        bar_beat = self.bar_beat_tick(end[0]) if end else None
        for key, v in zip(("bar", "beat", "tick", "key"), (*bar_beat, end[1]) if end else (None,) * 4):
            put_text(key, "" if v is None else fmt(round(v, 6)))
            config(self.entries[key], state="normal" if end else "disabled")
        name = note_name(round(end[1]), self.app.edo()) if end and 0 <= round(end[1]) < 256 else ""
        config(self.key_name, text=name)
        config(self.add_button, state="normal" if n and not self.placing() and n < MOST_POINTS else "disabled")
        multi = self.app.channel_mode.get() == "auto"
        note = "" if c["colours"] == "keep" or multi else tr("path.needs_multi")
        config(self.colour_note, text=note)
        grid_shown(self.colour_note, bool(note))  # (no empty row while there's nothing to say)
        fr = self.frame()
        if not self.targets or fr is None:
            info = ""
        elif self.idle:
            info = tr("path.idle")
        elif adding:
            info = tr("path.click_point", name=LETTERS[n + 1 if self.adding else n])
        elif self.placing():
            info = tr("path.click_b")
        else:
            cl = clean_path(c)
            info = tr("path.n_copies", n=len(shifts(cl, ppq, fr[1]))) if cl else ""
            if cl and cl["flip"] == "mirror" and peak(cl, fr[1]) is None:
                info += "\n" + tr("path.no_peak")
        config(self.info, text=info)
        self.app.roll.request_redraw()

    def preview(self, now=True):
        if self.big_ok(now):
            super().preview(now)
        self.app.roll.request_redraw()

    def big_ok(self, now):
        """Past big_ask.BIG notes (more than the shapes had so far): asked first, as other actions do (hunt).
        While a point follows the mouse / is dragged (not now) the notes wait for the click / let go. No = the change
        is put back."""
        cl, fr = clean_path(self.cfg), self.frame()
        if not cl or fr is None:
            self.est = 0
            return True
        n = len(shifts(cl, self.app.ppq, fr[1])) * sum(len(self.before_notes(i)) for i in self.targets) * max(
            1.0, max(cl["size"]) / 100)
        if n <= big_ask.BIG or n <= self.est:
            self.est = n
            return True
        if not now:
            return False
        if big_ask.ask(self.app, "notes", int(n), parent=self):
            self.est = n
            return True
        self.loose = self.adding = False
        self.put_state(self.undo.states[self.undo.at])
        return False

    def close(self):
        super().close()
        self.app.roll.request_redraw()


open_path = PathWindow.open
