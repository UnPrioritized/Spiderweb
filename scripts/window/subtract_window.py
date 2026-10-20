"""The Gate subtract window (right-click "Gate subtract..."; subtract.py): the selected shapes' notes end a few ticks
earlier and / or start a few ticks later, so a blank space shows between notes that touched. "Subtract from gates" on
/ off, then two tabs, "Note end" and "Note start" (user 2026-10-20: end first, and the side a shape gets when
opened without one), each with "Use this side", how many ticks, "Only
notes that touch the next one" (start: "...the one before"), and "Change across the shape": the amount goes from the
first number to a second one along a graph (like Gate Range's: across time or keys, each key row by itself + across
gaps, along a row by time or by note count; drag points, click to add one, right-click one to delete it; presets).
The amounts are kept in beats (a PPQ change keeps the look), shown in whole ticks. A side's graph settings are shared
by every shape worked on (the first one's shown; changing any of them gives them to all).

Works like the Sustain window: opening it puts Gate subtract on (the menu item is how it's put on), changes show on
the piano roll at once, the window follows the selection, and before anything else changes there the change being
tried out is kept as its own undo step (settle). OK keeps it (one undo step), Cancel / Esc / X puts back what changed
since. Ctrl+Z / Ctrl+Y step through the changes made in it."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes.gaterange import DIRS, STRAIGHT, few_steps, gate_steps, y_gate
from notes.subtract import (MOST, box_of, cut_marks, empty_side, end_side, is_subtracted, marked, part_subtract,
                            side_used, subtracts, ticks_of, touched, touching, with_sides)
from window import look
from window.range_window import PRESETS
from window.widgets import LocalUndo, PointGraph, Scrub, Tooltip, chart_edges, remember_place, unit_text

GRAPH_KEYS = ("to", "graph", "dir", "rows", "join", "count")  # (a side's graph settings, shared by all)
ALONG = ("time", "count")  # "Along a row:" choices (count = by note count)
SIDES = ("end", "start")  # the tabs, Note end first (user 2026-10-20: it doesn't move when notes sound; sh["subtract"] = the end side, its
# "start" = the start side)
SIDE_LISTS = ("use", "bys", "touches", "varies", "owns", "g", "g_all")  # (each tab's, see side_data)


def side_data():
    """One tab's settings: per shape "use" (Use this side), "bys", "touches", "varies", "owns" (its own graph); "g" =
    the graph shown, "g_all" = a graph control was used (every shape takes g)."""
    return {"use": [], "bys": [], "touches": [], "varies": [], "owns": [], "g": None, "g_all": False, "cuts": []}


def _side_list(key):
    """A tab's setting as an attribute (self.bys...): the tab shown's."""
    return property(lambda self: self.sides[self.side][key],
                    lambda self, v: self.sides[self.side].__setitem__(key, v))


def target_indices(app):
    """The selected shapes Gate subtract works on (their numbers): not a Hz bass (its own note lengths), not an image
    (its notes are its picture), like Sustain."""
    return [i for i in sorted(app.sels)
            if i < len(app.shapes) and not app.shapes[i].get("hz") and "picture" not in app.shapes[i]]


def subtract_targets(app):
    return [app.shapes[i] for i in target_indices(app)]


def bare_notes(app, sh):
    """Shape sh's notes before Gate subtract."""
    return np.asarray(app.notes_of({k: v for k, v in sh.items() if k != "subtract"}), np.int64).reshape(-1, 4)


def carry_subtract(app, whole, pieces):
    """Pieces cut off shape whole (Slice, Split here, Split into letters) that took its Gate subtract: a graph across
    the shape is cut so each note keeps its amount (subtract.part_subtract), and "Only notes that touch" keeps the
    notes that touched one now in another piece (cut marks, subtract.cut_marks; hunt 2026-10-20)."""
    sub = whole.get("subtract")
    if not isinstance(sub, dict):
        return
    sides = [(end_side(sub), "end"), (sub.get("start"), "start")]
    vary = any(s and s.get("vary") for s, _ in sides)
    touch = [(s, k) for s, k in sides if s and s.get("touch")]
    if not vary and not touch:
        return
    pieces = [p for p in pieces if isinstance(p.get("subtract"), dict)]
    own = [bare_notes(app, p) for p in pieces]
    if vary:
        notes = bare_notes(app, whole)
        for p, n in zip(pieces, own):
            p["subtract"] = part_subtract(p["subtract"], notes, n, app.ppq)
    if not touch or not any(len(n) for n in own):
        return
    every = np.concatenate([n for n in own if len(n)])
    whole_notes = bare_notes(app, whole)
    for side, key in touch:
        col = 1 if key == "end" else 0
        fn = touching if key == "end" else touched
        flags = fn(every)
        if side.get("cuts") and len(whole_notes):  # (the whole's own marks, from an earlier cut: on its box)
            flags |= marked(every, side["cuts"], col, box_of(whole_notes))
        at = 0
        for p, n in zip(pieces, own):
            mine = flags[at:at + len(n)] & ~fn(n) if len(n) else np.zeros(0, bool)
            at += len(n)
            s = p["subtract"] if key == "end" else p["subtract"].get("start")
            if not s:
                continue
            s.pop("cuts", None)
            cuts = cut_marks(n, mine, col) if len(n) else []
            if cuts:
                s["cuts"] = cuts


def own_part(sh):
    """What the window changes on a shape: its Gate subtract, and an Add between step's mark for one of its own
    ("-": none), like the Sustain window."""
    return [sh.get("subtract"), (sh.get("between") or {}).get("sub", "-")]


def put_own(sh, part):
    sub, mark = part
    if sub is None:
        sh.pop("subtract", None)
    else:
        sh["subtract"] = sub
    b = sh.get("between")
    if isinstance(b, dict):
        if mark == "-":
            b.pop("sub", None)
        else:
            b["sub"] = mark


class SubtractWindow(PointGraph, tk.Toplevel):
    use, bys, touches, varies, owns, g, g_all = (_side_list(k) for k in SIDE_LISTS)

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        app.subtract_window = self
        self.title(tr("subtract.title"))
        self.transient(app)
        self.resizable(False, False)
        self.closed = False
        s = self.s = app.scale
        self.ppq = app.ppq  # (the PPQ the boxes show ticks of)
        self.idx = []  # the shapes worked on (numbers in app.shapes)
        self.drag, self.hover = None, None
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text=tr("subtract.nothing"), foreground=look.HINT)
        self.on_var = tk.BooleanVar(value=True)
        self.on_tick = ttk.Checkbutton(box, text=tr("subtract.on"), variable=self.on_var,
                                       command=lambda: self.put_all("on"))
        self.on_tick.pack(anchor="w")
        Tooltip(self.on_tick, tr("subtract.on_tip"))
        self.side = "end"
        self.side_var = tk.StringVar(value=self.side)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        for side in SIDES:
            b = ttk.Radiobutton(row, text=tr("subtract.side_" + side), value=side, variable=self.side_var,
                                style="Toolbutton", command=self.pick_side)
            b.pack(side="left", padx=(0, 2))
            Tooltip(b, tr(f"subtract.side_{side}_tip"))
        ttk.Separator(box).pack(fill="x", pady=(0, 6))
        self.use_var = tk.BooleanVar(value=True)
        self.use_tick = ttk.Checkbutton(box, variable=self.use_var, command=lambda: self.put_all("use"))
        self.use_tick.pack(anchor="w")
        self.use_tip = Tooltip(self.use_tick, "")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        self.by_label = ttk.Label(row, text=tr("subtract.by"))
        self.by_label.pack(side="left")
        self.by_var, self.to_var = tk.StringVar(value=""), tk.StringVar(value="")
        self.by_box = self.number_box(row, self.by_var, "by", self.by_label)
        self.to_label = ttk.Label(row, text=tr("subtract.to"))
        self.to_box = self.number_box(row, self.to_var, "to", self.to_label)
        self.by_unit = ttk.Label(row)
        self.by_unit.pack(side="left", padx=(4, 0))
        for var in (self.by_var, self.to_var):
            var.trace_add("write", lambda *_: self.put_unit())
        Scrub(app, [(self.by_box, self.by_var, lambda: self.on_box("by"))], lambda: (1, 10, 1), 0, None,
              label=self.by_unit, drag_box=True, no_undo=True)
        self.by_tips = [Tooltip(w, "") for w in (self.by_label, self.by_box, self.by_unit)]
        Tooltip(self.to_label, tr("subtract.to_tip"))
        self.touch_var = tk.BooleanVar(value=False)
        self.touch_tick = ttk.Checkbutton(box, variable=self.touch_var, command=lambda: self.put_all("touch"))
        self.touch_tick.pack(anchor="w", pady=(6, 0))
        self.touch_tip = Tooltip(self.touch_tick, "")
        self.vary_var = tk.BooleanVar(value=False)
        self.vary_tick = ttk.Checkbutton(box, text=tr("subtract.vary"), variable=self.vary_var,
                                         command=lambda: self.put_all("vary"))
        self.vary_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.vary_tick, tr("subtract.vary_tip"))
        self.graph_part = self.make_graph(ttk.Frame(box), s)
        self.after_graph = row = ttk.Frame(box)
        row.pack(anchor="e", pady=(10, 0))
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda ev: self.cancel())
        self.bind("<Return>", lambda ev: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.ons, self.sides = [], {side: side_data() for side in SIDES}  # (ons: "Subtract from gates" per shape)
        self.hist = LocalUndo(self, self.state, self.put_state, main=app.last_step)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.retarget(start=True)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, "subtract")
        self.focus_set()

    def number_box(self, row, var, key, label):
        label.pack(side="left", padx=(0 if key == "by" else 6, 0))
        e = ttk.Entry(row, textvariable=var, width=7)
        e.pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: (self.on_box(key), "break")[1])  # (not OK: the window stays)
        e.bind("<FocusOut>", lambda ev: self.on_box(key))
        if key == "to":
            Scrub(self.app, [(e, var, lambda: self.on_box("to"))], lambda: (1, 10, 1), 0, None, label=label,
                  drag_box=True, no_undo=True)
            Tooltip(e, tr("subtract.to_tip"))
        return e

    def make_graph(self, part, s):
        """The part shown while "Change across the shape" is ticked: Across, the row ticks, the graph, presets."""
        self.w, self.h = int(460 * s), int(170 * s)
        self.ml, self.mb = chart_edges(s, (trn("n.ticks", 99999, n=99999),), 56, 22)
        self.mr, self.mt = int(12 * s), int(10 * s)
        pad = int(20 * s)
        row = ttk.Frame(part)
        row.pack(anchor="w", padx=(pad, 0), pady=(4, 0))
        ttk.Label(row, text=tr("range_window.across")).pack(side="left")
        self.dir_box = ttk.Combobox(row, values=[tr(f"range_window.{d}") for d in DIRS], state="readonly", width=24)
        self.dir_box.pack(side="left", padx=4)
        self.dir_box.bind("<<ComboboxSelected>>", lambda e: self.put_graph("dir", DIRS[self.dir_box.current()]))
        Tooltip(self.dir_box, tr("subtract.dir_tip"))
        self.rows_var, self.join_var = tk.BooleanVar(), tk.BooleanVar()
        self.rows_check = ttk.Checkbutton(part, text=tr("range_window.rows"), variable=self.rows_var,
                                          command=lambda: self.put_graph("rows", self.rows_var.get()))
        self.rows_check.pack(anchor="w", padx=(pad, 0), pady=(4, 0))
        Tooltip(self.rows_check, tr("subtract.rows_tip"))
        self.join_check = ttk.Checkbutton(part, text=tr("range_window.join"), variable=self.join_var,
                                          command=lambda: self.put_graph("join", self.join_var.get()))
        self.join_check.pack(anchor="w", padx=(2 * pad, 0))
        Tooltip(self.join_check, tr("subtract.join_tip"))
        row = ttk.Frame(part)
        row.pack(anchor="w", padx=(2 * pad, 0), pady=(2, 0))
        self.along_label = ttk.Label(row, text=tr("subtract.along"))
        self.along_label.pack(side="left")
        self.along_box = ttk.Combobox(row, values=[tr(f"subtract.along_{a}") for a in ALONG], state="readonly",
                                      width=16)
        self.along_box.pack(side="left", padx=4)
        self.along_box.bind("<<ComboboxSelected>>",
                            lambda e: self.put_graph("count", ALONG[self.along_box.current()] == "count"))
        for w in (self.along_label, self.along_box):
            Tooltip(w, tr("subtract.along_tip"))
        cv = self.canvas = tk.Canvas(part, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                     highlightbackground=look.CHART_BORDER, cursor="crosshair")
        cv.pack(anchor="w", padx=(pad, 0), pady=(6, 4))
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<ButtonPress-3>", self.delete_at)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        row = ttk.Frame(part)
        row.pack(anchor="w", padx=(pad, 0))
        for key, pts in PRESETS:  # (Gate Range's, named for gaps: subtract.short_longer...)
            ttk.Button(row, text=tr("subtract." + key.split(".")[1]), command=lambda pts=pts: self.put_graph("graph", [list(p) for p in pts])
                       ).pack(side="left", padx=(0, 4))
        ttk.Label(part, text=tr("subtract.hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=self.w).pack(anchor="w", padx=(pad, 0), pady=(6, 0))
        return part

    @property
    def tgts(self):
        return [self.app.shapes[i] for i in self.idx if i < len(self.app.shapes)]

    @property
    def pts(self):
        return self.g["graph"] if self.g else STRAIGHT

    # ---- the shapes worked on

    def sub_now(self):
        return json.dumps([t.get("subtract") for t in self.tgts])

    def own_now(self):
        return json.dumps([own_part(t) for t in self.tgts])

    def retarget(self, start=False):
        """Work on the selected shapes (start: opened, which puts Gate subtract on)."""
        self.idx = target_indices(self.app)
        self.saved_sel = self.app.sel_state()  # (for the undo step)
        self.before = self.sub_now()
        self.kept = self.own_now()  # (put back by Cancel)
        self.load(start)
        if start and self.idx:
            self.store()
        else:
            self.now = self.before  # (as this window last left them)
            self.show()
        self.hist.reset()

    def load(self, start):
        """Each shape's settings from the shapes, then the controls. start (opened from the menu): all on, and a shape
        with no side used gets the Note end side (1 tick where it had none; user 2026-10-20: the first tab). Opened
        on the Note end tab unless only the start side is used."""
        subs = [t.get("subtract") if is_subtracted(t) else None for t in self.tgts]
        self.ons = [start or s is not None for s in subs]
        for side in SIDES:
            # (an empty side = none: one a flip left with 0 ticks gets 1 tick when used, hunt 2026-10-20)
            ss = [s and (end_side(s) if side == "end" else s.get("start")) for s in subs]
            ss = [None if empty_side(s) else s for s in ss]
            d = self.sides[side]
            d["use"] = [side_used(s) for s in ss]
            d["bys"] = [s["by"] if s else None for s in ss]
            d["touches"] = [bool(s and s.get("touch")) for s in ss]
            d["varies"] = [bool(s and s.get("vary")) for s in ss]
            d["cuts"] = [s.get("cuts") if s else None for s in ss]  # (kept as they are: subtract.cut_marks)
            # each shape's own graph (kept until a graph control is used: then the one shown goes to all, hunt)
            d["owns"] = [{k: s[k] for k in GRAPH_KEYS if k in s} if s and "to" in s else None for s in ss]
            d["g"] = json.loads(json.dumps(next((o for o in d["owns"] if o), None)))  # (shown: the first shape's)
            d["g_all"] = False
        if start:
            self.use_first()
            ends, starts = self.sides["end"]["use"], self.sides["start"]["use"]
            self.side = "start" if any(starts) and not any(ends) else "end"
        self.side_var.set(self.side)
        self.show_controls()

    def use_first(self):
        """Shapes with neither side used: the Note end side used (1 tick where it has no amount)."""
        ends, starts = self.sides["end"], self.sides["start"]
        for i in range(len(self.ons)):
            if self.ons[i] and not ends["use"][i] and not starts["use"][i]:
                ends["use"][i] = True
                if ends["bys"][i] is None:
                    ends["bys"][i] = 1 / self.app.ppq

    def pick_side(self):
        """A tab clicked: its settings shown (a number typed on the other one goes to it first)."""
        side = self.side_var.get()
        if side == self.side:
            return
        self.side_var.set(self.side)
        self.take_typing()
        self.side = side
        self.side_var.set(side)
        self.show_controls()
        self.show()

    def new_graph(self):
        """The graph settings a shape gets the first time it changes across: To = 4 times its amount (0: 4 ticks)."""
        b = next((b for b in self.bys if b is not None), 1 / self.app.ppq) or 1 / self.app.ppq
        return {"to": min(b * 4, MOST), "graph": [list(p) for p in STRAIGHT], "dir": "time"}

    def show_controls(self):
        """The ticks, boxes and graph as the shapes' settings are: ticked when every shape has it, half-ticked when
        only some do; a box shows the number they share, else stays empty."""
        def tick(var, w, flags):
            var.set(bool(flags) and all(flags))
            w.state(["alternate"] if any(flags) and not all(flags) else ["!alternate"])
        tick(self.on_var, self.on_tick, self.ons)
        tick(self.use_var, self.use_tick, self.use)
        start = self.side == "start"
        self.use_tick.config(text=tr("subtract.use_" + self.side))
        self.use_tip.set_text(tr(f"subtract.use_{self.side}_tip"))
        self.touch_tick.config(text=tr("subtract.touch_start" if start else "subtract.touch"))
        self.touch_tip.set_text(tr("subtract.touch_start_tip" if start else "subtract.touch_tip"))
        for t in self.by_tips:
            t.set_text(tr("subtract.by_start_tip" if start else "subtract.by_tip"))
        tick(self.touch_var, self.touch_tick, self.touches)
        tick(self.vary_var, self.vary_tick, self.varies)
        got = {b for b in self.bys if b is not None}
        self.by_var.set(self.text_of(got.pop()) if len(got) == 1 and None not in self.bys else "")
        g = self.g or self.new_graph()
        self.to_var.set(self.text_of(g["to"]))
        self.dir_box.current(DIRS.index(g["dir"]))
        self.rows_var.set(bool(g.get("rows")))
        self.join_var.set(bool(g.get("join")))
        self.along_box.current(1 if g.get("count") else 0)

    def sync(self):
        """The main window changed the selection or the shapes (sync_panel)."""
        if self.closed:
            return
        if target_indices(self.app) != self.idx:
            self.settle()
            self.retarget()
        elif self.sub_now() != self.now:  # (undo, a file opened)
            self.retarget()

    def fresh(self):
        """The shapes worked on are still there, as this window last left them."""
        return (not self.closed and bool(self.idx) and self.idx[-1] < len(self.app.shapes)
                and self.sub_now() == self.now)

    def pending(self):
        """Is there a change tried here that settle would keep?"""
        return self.fresh() and self.now != self.before

    def put_back(self, saved):
        """The shapes' Gate subtract as before the change tried here, on saved (a copy of the shapes)."""
        for i, sub in zip(self.idx, json.loads(self.kept)):
            if i < len(saved):
                put_own(saved[i], sub)

    def settle(self, also=()):
        """Something else is about to change in the main window: the change tried so far is kept (its own undo
        step), and from now on Cancel only puts back what changes after this. also: other windows kept next
        (App.settle_windows): this step takes their tries back too."""
        if self.fresh():
            self.hist.reset()
        if not self.pending():
            return
        saved = json.loads(json.dumps(self.app.shapes))  # (the step takes back only what was tried here)
        for w in (self, *also):
            w.put_back(saved)
        sel, self.saved_sel, self.before = self.saved_sel, self.app.sel_state(), self.now
        self.kept = self.own_now()
        self.app.add_undo_step(json.dumps(saved), tr("subtract.step"), sel)
        self.hist.seen()

    def ppq_changed(self):
        """A new PPQ: the boxes show the same lengths in the new ticks (App.on_project_change)."""
        old = self.ppq
        if self.app.ppq == old:
            return
        self.ppq = self.app.ppq
        for var in (self.by_var, self.to_var):
            beats = self.read(var.get(), old)
            if beats is not None:
                var.set(self.text_of(beats))
        self.seen = (self.by_var.get(), self.to_var.get())
        self.draw()

    # ---- the amounts: kept in beats, shown in whole ticks

    def text_of(self, beats):
        return fmt(ticks_of(beats, self.app.ppq))

    def read(self, text, ppq=None):
        """Typed ticks -> beats (whole ticks, 0 or more, at most MOST beats), or None (empty, not a number, under
        0). ppq: the one it was typed for (default the project's)."""
        ppq = ppq or self.app.ppq
        try:
            v = calc(text)
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(v) or v < 0:
            return None
        return min(math.floor(v + 0.5) / ppq, MOST)

    def put_all(self, what):
        """A tick changed ("on", "use", "touch", "vary"): every shape takes it (a half-ticked one: a click = all
        on)."""
        if not self.idx:
            return
        self.take_typing()
        n = len(self.idx)
        if what == "on":
            self.ons = [self.on_var.get()] * n
            self.use_first()
        if what in ("on", "use"):
            if what == "use":
                self.use = [self.use_var.get()] * n
            if any(self.use):  # (shapes on this side with no amount yet: the box's, else 1 tick)
                b = self.read(self.by_var.get())
                b = 1 / self.app.ppq if b is None else b
                self.bys = [v if v is not None or not u else b for v, u in zip(self.bys, self.use)]
        elif what == "touch":
            self.touches = [self.touch_var.get()] * n
        else:
            self.varies = [self.vary_var.get()] * n
            if self.vary_var.get() and not self.g:
                self.g = self.new_graph()
        self.show_controls()
        self.store()
        self.hist.mark()

    def put_graph(self, key, value, mark=True):
        """A graph setting changed: every shape takes the graph settings."""
        if not self.idx or not self.g:
            return
        if self.drag is None:  # (a drag: once, at its press)
            self.take_typing()
        self.g_all = True  # (from now on every shape takes the graph shown)
        self.g[key] = value
        if key in ("rows", "join", "count") and not value:
            del self.g[key]
        self.show_controls()
        self.store()
        if mark:
            self.hist.mark()

    def on_box(self, key):
        """An amount typed / stepped: every shape takes it (To: the graph's)."""
        box, var = (self.by_box, self.by_var) if key == "by" else (self.to_box, self.to_var)
        if self.closed or str(box.cget("state")) == "disabled":
            return
        beats = self.read(var.get())
        if beats is None:  # (a wrong number: back to the last good one, or empty)
            return self.show_controls()
        if key == "by":
            self.bys = [beats] * len(self.idx)
        elif self.g:
            self.g["to"] = beats
            self.g_all = True
        self.show_controls()
        self.store()
        self.hist.mark(key)

    # ---- showing

    def put_unit(self):
        self.by_unit.config(text=unit_text(self.to_var.get() if self.vary_var.get() else self.by_var.get(),
                                           "subtract.ticks", "subtract.tick"))

    def show(self):
        """The window as the settings are: what can be used greyed or not, the graph shown while it's used."""
        self.seen = (self.by_var.get(), self.to_var.get())  # (typed past them without Enter = OK takes it)
        if self.idx:
            self.what.pack_forget()
        elif not self.what.winfo_manager():  # (nothing to work on selected: said so, all greyed)
            self.what.pack(anchor="w", before=self.on_tick, pady=(0, 6))
        self.on_tick.state(["!disabled"] if self.idx else ["disabled"])  # (state(): config(state=) drops a half-tick)
        self.use_tick.state(["!disabled"] if any(self.ons) and self.idx else ["disabled"])
        on = any(self.ons) and any(self.use) and bool(self.idx)  # (this tab's controls)
        vary = on and any(self.varies)
        for w in (self.touch_tick, self.vary_tick):
            w.state(["!disabled"] if on else ["disabled"])
        self.by_box.config(state="normal" if on else "disabled")
        for w in (self.by_label, self.by_unit):
            w.config(foreground="" if on else look.LABEL_OFF)
        self.by_label.config(text=tr("subtract.from" if vary else "subtract.by"))
        # (the second number and the graph only while it's used; each checked by itself: opened with it ticked, the
        # graph stayed hidden)
        if vary and not self.to_box.winfo_manager():
            self.to_label.pack(side="left", padx=(6, 0), before=self.by_unit)
            self.to_box.pack(side="left", padx=(4, 0), before=self.by_unit)
        if vary and not self.graph_part.winfo_manager():
            self.graph_part.pack(anchor="w", fill="x", before=self.after_graph)
        if not vary:
            for w in (self.to_label, self.to_box, self.graph_part):
                if w.winfo_manager():
                    w.pack_forget()
        time = (self.g or {}).get("dir", "time") == "time"  # (each key row by itself: left to right only)
        rows = time and bool((self.g or {}).get("rows"))
        self.rows_check.state(["!disabled"] if time else ["disabled"])
        self.join_check.state(["!disabled"] if rows else ["disabled"])
        self.along_box.config(state="readonly" if rows else "disabled")
        self.along_label.config(foreground="" if rows else look.LABEL_OFF)
        self.put_unit()
        self.draw()

    def gates(self):
        """The first and second amount in ticks."""
        got = [b for b in self.bys if b is not None]
        g = self.g or self.new_graph()
        return ticks_of(got[0] if got else 1 / self.app.ppq, self.app.ppq), ticks_of(g["to"], self.app.ppq)

    def draw(self):
        """The graph: amounts at 0, 25 ... 100 % up, the whole-tick amounts the notes get (pale steps), the line."""
        cv, s = self.canvas, self.s
        cv.delete("all")
        if not self.g:
            return
        a, b = self.gates()
        x0, x1, top, bot = self.u2x(0), self.u2x(1), self.y2c(1), self.y2c(0)
        for j in range(1, 4):
            cv.create_line(self.u2x(j / 4), top, self.u2x(j / 4), bot, fill=look.CHART_GRID)
        for k in range(5):
            y = k / 4
            g = y_gate(a, b, y)
            cv.create_line(x0, self.y2c(y), x1, self.y2c(y), fill=look.CHART_GRID)
            cv.create_text(x0 - 4 * s, self.y2c(y), text=trn("n.ticks", g, n=g), anchor="e", fill=look.LABEL,
                           font=look.font(7))
        cv.create_rectangle(x0, top, x1, bot, outline=look.CHART_FRAME)
        d = self.g["dir"]
        ends = {"time": ("first", "last"), "keys": ("range_window.low", "range_window.high"),
                "keys_down": ("range_window.high", "range_window.low")}[d]
        if d == "time":
            ends = ("subtract.row_first", "subtract.row_last") if self.g.get("rows") else (
                "subtract.first", "subtract.last")
        cv.create_text(x0, bot + 4 * s, text=tr(ends[0]), anchor="nw", fill=look.LABEL, font=look.font(7))
        cv.create_text(x1, bot + 4 * s, text=tr(ends[1]), anchor="ne", fill=look.LABEL, font=look.font(7))
        span = (b - a) or 1
        if few_steps(self.pts, a, b):
            line = []
            for u0, u1, g in gate_steps(self.pts, a, b):
                y = self.y2c((g - a) / span if b != a else 0)
                line += [self.u2x(u0), y, self.u2x(u1), y]
            if len(line) >= 4:
                cv.create_line(*line, fill=look.CHART_LINE_FAINT, width=max(1, round(2 * s)))
        lw = max(1, round(1.5 * s))
        cv.create_line(*[c for u, y in self.pts for c in (self.u2x(u), self.y2c(y))], fill=look.CHART_LINE, width=lw)
        r = 4 * s
        for i, (u, y) in enumerate(self.pts):
            x, yy = self.u2x(u), self.y2c(y)
            shape = cv.create_rectangle if i in (0, len(self.pts) - 1) else cv.create_oval
            shape(x - r, yy - r, x + r, yy + r, fill=look.CHART_POINT, outline=look.CHART_LINE, width=lw)
        at = self.pts[self.drag] if self.drag is not None else self.hover
        if at is not None:
            g = y_gate(a, b, float(at[1]))
            cv.create_text(x1 - 4 * s, top + 4 * s, text=tr("subtract.at", at=fmt(round(at[0] * 100, 1)),
                                                                 ticks=trn("n.ticks", g, n=g)),
                           anchor="ne", fill=look.VALUE, font=look.font(8))

    # ---- the graph's mouse (like Gate Range's)

    def press(self, e):
        if not self.g:
            return
        self.take_typing()
        i = self.point_at(e.x, e.y)
        if i is None:
            u, y = self.snapped(e)
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            pts = [list(p) for p in self.pts]
            i = next(j for j, p in enumerate(pts) if p[0] > u)
            pts.insert(i, [u, y])
            self.drag = i
            self.put_graph("graph", pts, mark=False)
        self.drag = i
        self.draw()

    def motion(self, e):
        i = self.drag
        if i is None:
            return
        u, y = self.snapped(e)
        pts = [list(p) for p in self.pts]
        if i in (0, len(pts) - 1):
            u = pts[i][0]  # (the ends stay at the edges)
        else:
            u = min(max(u, pts[i - 1][0]), pts[i + 1][0])
        if [u, y] != pts[i]:
            pts[i] = [u, y]
            self.put_graph("graph", pts, mark=False)

    def release(self, e):
        if self.drag is None:
            return
        self.drag = None
        self.draw()
        self.hist.mark()

    def delete_at(self, e):
        i = self.point_at(e.x, e.y)
        if not self.g or i is None or len(self.pts) <= 2 or self.drag is not None:
            return
        pts = [list(p) for p in self.pts]
        del pts[i]
        pts[0][0], pts[-1][0] = 0.0, 1.0
        self.put_graph("graph", pts)

    def on_hover(self, e):
        self.hover = None
        if e is not None and self.drag is None and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            self.hover = [u, float(np.interp(u, [p[0] for p in self.pts], [p[1] for p in self.pts]))]
        self.draw()

    # ---- undo inside, storing

    def state(self):
        return json.dumps([self.ons, self.sides])

    def put_state(self, state):
        self.drag = None
        ons, sides = json.loads(state)
        changed = [s for s in SIDES if sides[s] != self.sides[s]]
        if changed and self.side not in changed:  # (Ctrl+Z / Ctrl+Y show the tab they change, hunt 2026-10-20)
            self.side = changed[0]
            self.side_var.set(self.side)
        self.ons, self.sides = ons, sides
        self.show_controls()
        self.store()

    def side_of(self, side, i):
        """Shape i's settings for one side (None: it has none)."""
        d = self.sides[side]
        if d["bys"][i] is None:
            return None
        sub = {"by": d["bys"][i]}
        if d["touches"][i]:
            sub["touch"] = True
            if d["cuts"][i]:  # (a piece's notes that touched another piece: kept)
                sub["cuts"] = d["cuts"][i]
        if not d["use"][i]:  # (kept while unused: using it again brings them back)
            sub["off"] = True
        # (its own graph until a graph control is used; none of its own and ticked: the one shown. Kept while
        # unticked too: ticking again brings it back)
        g = d["g"] if d["g_all"] or (d["varies"][i] and not d["owns"][i]) else d["owns"][i]
        if g:
            sub.update(json.loads(json.dumps(g)))
            if d["varies"][i]:
                sub["vary"] = True
        return sub

    def store(self):
        """Put each shape's settings on it and show them."""
        for i, t in enumerate(self.tgts):
            if not self.ons[i]:
                t.pop("subtract", None)
                continue
            t["subtract"] = with_sides(self.side_of("end", i) or {"by": 0.0, "off": True}, self.side_of("start", i))
        self.now = self.sub_now()
        self.show()
        self.app.shapes_changed()

    # ---- closing

    def take_typing(self):
        """Numbers typed without Enter count too (OK, or a tick / dropdown / the graph used next: clicking the graph
        leaves the box the keyboard, so it never takes its number by itself, hunt)."""
        if not any(self.ons) or not any(self.use):
            return
        if self.by_var.get() != self.seen[0]:
            self.on_box("by")
        if self.to_var.get() != self.seen[1] and self.to_box.winfo_manager():
            self.on_box("to")

    def ok(self):
        self.take_typing()
        self.settle()
        self.close()
        if any(self.ons) and self.idx:
            self.app.tips.show("subtract", wait=True)  # (the first time: what it does)

    def cancel(self):
        if self.idx and self.idx[-1] < len(self.app.shapes) and self.own_now() != self.kept:
            self.app.roll.cancel_draft()  # (as it was: an Add between step tried out stays one)
            for i, part in zip(self.idx, json.loads(self.kept)):
                put_own(self.app.shapes[i], part)
        self.close()
        self.app.shapes_changed()

    def close(self):
        self.closed = True
        self.app.subtract_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_subtract(app):
    w = app.subtract_window
    if w:  # (already open: it follows the selection; the menu item puts Gate subtract on)
        w.sync()
        used = [a or b for a, b in zip(w.sides["end"]["use"], w.sides["start"]["use"])]
        if w.idx and (not all(w.ons) or not all(used)):  # (or a shape with no side used: Note end on)
            w.on_var.set(True)
            w.put_all("on")
        w.lift()
        w.focus_set()
    elif target_indices(app):
        SubtractWindow(app)


def any_subtracted(app):
    return any(subtracts(app.shapes[i]) for i in app.sels if i < len(app.shapes))
