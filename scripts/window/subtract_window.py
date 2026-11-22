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
import re
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import texts, tr, trn
from files import shape_defaults
from files.mathexpr import amount, fmt
from notes.gaterange import DIRS, STRAIGHT, few_steps, gate_steps, y_gate
from notes.subtract import (MOST, MOST_PCT, box_of, clean_subtract, cut_marks, empty_side, end_side, is_subtracted, marked, part_subtract,
                            side_used, subtracts, ticks_of, touched, touching, with_sides)
from window import look
from window.default_pin import DefaultPin, defaults_changed
from window.range_window import PRESETS
from window.widgets import (LocalUndo, PointGraph, Scrub, Tooltip, chart_edges, remember_place, show_mixed, traced,
                            unit_text)

GRAPH_KEYS = ("to", "graph", "dir", "rows", "join", "count")  # (a side's graph settings, shared by all)
ALONG = ("time", "count")  # "Along a row:" choices (count = by note count)
SIDES = ("end", "start")  # the tabs, Note end first (user 2026-10-20: it doesn't move when notes sound; sh["subtract"] = the end side, its
# "start" = the start side)
SIDE_LISTS = ("use", "bys", "touches", "varies", "owns", "g", "g_all")  # (each tab's, see side_data)
UNITS = ("pct", "ticks")  # "Amount in:" choices ("% + ticks" = only shown as its text, not in the list: user 2026-11-23)
_LAST_NUMBER = re.compile(r"(\d+(?:\.\d+)?)(\s*[%％]?\s*)$")


def value_of(side, key):
    """A stored side's "by" / "to" as the window's [% or None, beats] (0 % with ticks = ticks only)."""
    if side.get("unit") != "pct":
        return [None, float(side[key])]
    p, b = float(side[key]), float(side.get("plus" if key == "by" else "plus_to") or 0.0)
    return [None if p == 0 and b > 0 else p, b]


def kind(v):
    """An amount's kind: "ticks", "pct" or "both"."""
    return "ticks" if v[0] is None else "pct" if not v[1] else "both"


def amount_text(v, ppq):
    """[% or None, beats] as typed: 50% + 10 / 50% / 10."""
    p, b = v
    ticks = ticks_of(b, ppq)
    parts = [] if p is None else [fmt(p) + "%"]
    if ticks or p is None:
        parts.append(fmt(ticks))
    return " + ".join(parts)


def describe(key, saved, ppq):
    """A saved default in a few words (Settings → User set defaults): each side used, its amount (and the second one
    with a graph), "only touching", "same amount"."""
    def side_words(s):
        a = amount_text(value_of(s, "by"), ppq)
        if s.get("vary") and "to" in s:
            a = tr("defaults.from_to", a=a, b=amount_text(value_of(s, "to"), ppq))
        ends = [value_of(s, "by")] + ([value_of(s, "to")] if s.get("vary") and "to" in s else [])
        kinds = {kind(v) for v in ends}
        unit = tr("subtract.pct_word") if kinds == {"pct"} else tr("subtract.ticks") if "pct" not in kinds \
            else tr("subtract.both_word")  # (50% + 10 ticks / 10% of each note / 2 ticks to 50%: both words)
        words = [f"{a} {unit}"]
        if s.get("touch"):
            words.append(tr("defaults.touching"))
        if s.get("same"):
            words.append(tr("defaults.same"))
        return ", ".join(words)
    if key == "extend":
        return side_words(saved)
    parts = []
    for name, s in (("subtract.side_end", end_side(saved)), ("subtract.side_start", saved.get("start"))):
        if side_used(s):
            parts.append(f"{tr(name)}: {side_words(s)}")
    return " · ".join(parts) or tr("defaults.nothing_used")


def step_text(text, delta):
    """A box's text with its LAST number stepped by delta (never under 0): 50% + 10 -> 50% + 11; None when the text
    doesn't end in a number."""
    m = _LAST_NUMBER.search(text)
    if not m:
        return None
    return text[:m.start(1)] + fmt(max(0.0, float(m.group(1)) + delta)) + m.group(2)


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


class SubtractWindow(DefaultPin, PointGraph, tk.Toplevel):
    notes_pass = True  # (its clicks / drags don't wait for notes made in the background: notes_wait.pass_window)
    use, bys, touches, varies, owns, g, g_all = (_side_list(k) for k in SIDE_LISTS)

    KEY = "subtract"  # the shape's key (sh["subtract"])
    T = "subtract"  # texts' first word (subtract.title...), window place, tip / Help topic
    ATTR = "subtract_window"  # App's attribute holding the open one
    SIDES = SIDES
    TOUCH = True  # "Only notes that touch..." shown

    def extra_rows(self, box):
        """More rows above the amount (Gate extend: what the amount is in)."""

    def tx(self, key, **kw):
        """A text of this window: "<T>.<key>" (Gate extend: Gate subtract's where it has none of its own); a key with a
        dot = that one."""
        if "." not in key:
            k = f"{self.T}.{key}"
            key = k if k in texts() else "subtract." + key
        return tr(key, **kw)

    def side_data(self):
        return side_data()

    def has_it(self, sh):
        return is_subtracted(sh)

    def own_part(self, sh):
        return own_part(sh)

    def put_own(self, sh, part):
        put_own(sh, part)

    # ---- TYPED AMOUNTS (user 2026-11-22, PLAN.md stages B + C): the % SIGN decides, never the dropdown: 50 = 50
    # ticks, 50% = % of each note's length, 50% + 10 = both (files/mathexpr.amount). Each amount in the window =
    # [% or None, beats] (None = no % typed). "Amount in" follows what's typed (Ticks / %; both = "% + ticks" shown,
    # not in the list; shapes that differ = Mixed); picking Ticks or % turns every number into that unit (50% + 10
    # -> 60 ticks). Text that can't work = the box red + why under it.

    BOX_WIDTH = 14  # (letters the amount boxes show: "120% + 20" whole)

    def as_shown(self, s):
        """A stored side with its amounts as the window keeps them ([% or None, beats])."""
        return dict(s, by=value_of(s, "by"), **({"to": value_of(s, "to")} if "to" in s else {}))

    def stored(self, side):
        """One side's window settings -> how it's stored (a % in either amount = unit "pct" + "plus" ticks)."""
        side = dict(side)
        by, to = side["by"], side.get("to")
        if any(v is not None and v[0] is not None for v in (by, to)):
            side["unit"] = "pct"
            side["by"] = by[0] or 0.0
            if by[1]:
                side["plus"] = by[1]
            if to is not None:
                side["to"] = to[0] or 0.0
                if to[1]:
                    side["plus_to"] = to[1]
        else:
            side["by"] = by[1]
            if to is not None:
                side["to"] = to[1]
        return side

    def widest_label(self):
        """The widest amount the graph's left edge shows (its margin)."""
        return self.tx("pct_n", n=MOST_PCT) + " + " + trn("n.ticks", 99999, n=99999)

    def first_amount(self):
        """What a shape gets where it has no amount yet: 1 tick."""
        return [None, 1 / self.app.ppq]

    # ---- the user's own default ("use as default" pin, window/default_pin.py; user 2026-11-22/23: saves EVERYTHING the
    # window sets on a shape, from the first shape worked on)

    @classmethod
    def clean_saved(cls, v):
        return clean_subtract(v)

    @classmethod
    def describe_default(cls, v, ppq):
        return describe(cls.KEY, v, ppq)

    @classmethod
    def editor_class(cls):
        return default_editor(cls)

    def can_save_default(self):
        return bool(self.idx) and any(self.ons)

    def default_now(self):
        """The first shape's settings shown here = what shapes get from now on."""
        if not self.idx:
            return None
        self.take_typing()
        i = next((i for i, on in enumerate(self.ons) if on), 0)
        mine = self.made_of(i)
        mine.pop("cuts", None)  # (a piece's own marks: never a default)
        if isinstance(mine.get("start"), dict):
            mine["start"] = {k: v for k, v in mine["start"].items() if k != "cuts"}
        return mine

    def made_of(self, i):
        """Shape i's stored settings from the window's."""
        end, start = self.side_of("end", i), self.side_of("start", i)
        return with_sides(self.stored(end) if end else {"by": 0.0, "off": True}, start and self.stored(start))

    def hook_scrub(self, scrub):
        """A box's Scrub: its LAST number steps (50% + 10 -> 50% + 11)."""
        scrub.step_text = step_text

    def scaled(self, v, k):
        """An amount k times as big (each part at most the most it may be)."""
        return [None if v[0] is None else min(v[0] * k, MOST_PCT), min(v[1] * k, MOST)]

    def shape_values(self):
        """Per shape with an amount on the tab shown: the amounts it uses."""
        d = self.sides[self.side]
        out = []
        for i, b in enumerate(d["bys"]):
            if b is None:
                continue
            g = d["g"] if d["g_all"] or not d["owns"][i] else d["owns"][i]
            out.append([b, g["to"]] if d["varies"][i] and g and "to" in g else [b])
        return out

    def shape_kinds(self):
        """Each shape's kind of amount: "ticks", "pct" or "both" (10 ticks changing to 50% = both)."""
        out = set()
        for vs in self.shape_values():
            ks = {kind(v) for v in vs}
            out.add(ks.pop() if len(ks) == 1 else "both")
        return out

    def unit_row(self, box):
        """"Amount in:" Ticks / % (follows what's typed)."""
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        self.unit_label = ttk.Label(row, text=self.tx("unit"))
        self.unit_label.pack(side="left")
        self.unit_box = ttk.Combobox(row, values=[self.tx("unit_" + u) for u in UNITS], state="readonly", width=24)
        self.unit_box.pack(side="left", padx=4)
        self.unit_box.bind("<<ComboboxSelected>>", lambda e: self.pick_unit(UNITS[self.unit_box.current()]))
        self.unit_tip = Tooltip(self.unit_box, self.tx("unit_tip"))
        Tooltip(self.unit_label, self.tx("unit_tip"))

    def show_unit(self):
        """The dropdown as the tab shown's amounts are."""
        kinds = self.shape_kinds() or {kind(self.first_amount())}  # (nothing yet: what a shape would get)
        both = self.tx("unit_both")
        if both not in getattr(self.unit_box, "fit_also", ()):
            self.unit_box.fit_also = (both, tr("widgets.mixed"))
        if kinds == {"both"}:  # (shown, not a choice: user 2026-11-23)
            self.unit_box.set(both)
        elif len(kinds) == 1:
            self.unit_box.current(UNITS.index(kinds.pop()))
        show_mixed(self.unit_box, len(kinds) > 1, self.unit_tip, self.tx("unit_tip"))

    def pick_unit(self, unit):
        """Ticks or % picked: every number on the tab shown takes that unit (50% + 10 -> 60 ticks, or 60%)."""
        if not self.idx:
            return self.show_controls()
        self.take_typing()
        d = self.sides[self.side]
        ppq = self.app.ppq

        def moved(v):
            if v is None:
                return None
            n = (v[0] or 0) + ticks_of(v[1], ppq)
            return [None, min(n / ppq, MOST)] if unit == "ticks" else [float(min(n, MOST_PCT)), 0.0]
        d["bys"] = [moved(b) for b in d["bys"]]
        for g in [d["g"], *d["owns"]]:
            if g and "to" in g:
                g["to"] = moved(g["to"])
        self.accepted(self.by_box)
        self.show_controls()
        self.store()
        self.hist.mark()

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        setattr(app, self.ATTR, self)
        self.title(self.tx("title"))
        self.transient(app)
        self.resizable(False, False)
        self.closed = False
        s = self.s = app.scale
        self.ppq = app.ppq  # (the PPQ the boxes show ticks of)
        self.idx = []  # the shapes worked on (numbers in app.shapes)
        self.drag, self.hover = None, None
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text=self.tx("nothing"), foreground=look.HINT)
        self.on_var = tk.BooleanVar(value=True)
        self.on_tick = ttk.Checkbutton(box, text=self.tx("on"), variable=self.on_var,
                                       command=lambda: self.put_all("on"))
        self.on_tick.pack(anchor="w")
        Tooltip(self.on_tick, self.tx("on_tip"))
        self.side = "end"
        self.side_var = tk.StringVar(value=self.side)
        tabs = len(self.SIDES) > 1  # (Gate extend: one side, no tabs / "Use this side")
        if tabs:
            row = ttk.Frame(box)
            row.pack(anchor="w", pady=(8, 0))
            for side in self.SIDES:
                b = ttk.Radiobutton(row, text=self.tx("side_" + side), value=side, variable=self.side_var,
                                    style="Toolbutton", command=self.pick_side)
                b.pack(side="left", padx=(0, 2))
                Tooltip(b, self.tx(f"side_{side}_tip"))
            ttk.Separator(box).pack(fill="x", pady=(0, 6))
        self.use_var = tk.BooleanVar(value=True)
        self.use_tick = ttk.Checkbutton(box, variable=self.use_var, command=lambda: self.put_all("use"))
        if tabs:
            self.use_tick.pack(anchor="w")
        self.use_tip = Tooltip(self.use_tick, "")
        self.unit_row(box)
        self.extra_rows(box)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        self.by_label = ttk.Label(row, text=self.tx("by"))
        self.by_label.pack(side="left")
        self.by_var, self.to_var = tk.StringVar(value=""), tk.StringVar(value="")
        self.by_box = self.number_box(row, self.by_var, "by", self.by_label)
        self.to_label = ttk.Label(row, text=self.tx("to"))
        self.to_box = self.number_box(row, self.to_var, "to", self.to_label)
        self.by_unit = ttk.Label(row)
        self.by_unit.pack(side="left", padx=(4, 0))
        for var in (self.by_var, self.to_var):
            traced(self, var, lambda *_: self.put_unit())
        self.hook_scrub(Scrub(app, [(self.by_box, self.by_var, lambda: self.on_box("by"))], lambda: (1, 10, 1), 0,
                              None, label=self.by_unit, no_undo=True))
        self.by_tips = [Tooltip(w, "") for w in (self.by_label, self.by_box, self.by_unit)]
        Tooltip(self.to_label, self.tx("to_tip"))
        self.touch_var = tk.BooleanVar(value=False)
        self.touch_tick = ttk.Checkbutton(box, variable=self.touch_var, command=lambda: self.put_all("touch"))
        if self.TOUCH:
            self.touch_tick.pack(anchor="w", pady=(6, 0))
        self.touch_tip = Tooltip(self.touch_tick, "")
        self.vary_var = tk.BooleanVar(value=False)
        self.vary_tick = ttk.Checkbutton(box, text=self.tx("vary"), variable=self.vary_var,
                                         command=lambda: self.put_all("vary"))
        self.vary_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.vary_tick, self.tx("vary_tip"))
        self.graph_part = self.make_graph(ttk.Frame(box), s)
        self.after_graph = row = ttk.Frame(box)
        row.pack(anchor="e", pady=(10, 0))
        from window.edit_line import EditLine  # ("Hidden while you edit this one: ..." + Preview all, stage C)
        self.edit_line = EditLine(box, app, self.KEY, None, order=False, before=row)
        self.pin_row(row)
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda ev: self.cancel())
        self.bind("<Return>", lambda ev: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.ons, self.sides = [], {side: self.side_data() for side in self.SIDES}  # (ons: "Subtract from gates" per shape)
        self.hist = LocalUndo(self, self.state, self.put_state, main=app.last_step)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.retarget(start=True)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, self.T)
        self.focus_set()

    def number_box(self, row, var, key, label):
        label.pack(side="left", padx=(0 if key == "by" else 6, 0))
        e = ttk.Entry(row, textvariable=var, width=self.BOX_WIDTH)
        e.pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: (self.on_box(key), "break")[1])  # (not OK: the window stays)
        e.bind("<FocusOut>", lambda ev: self.on_box(key))
        if key == "to":
            self.hook_scrub(Scrub(self.app, [(e, var, lambda: self.on_box("to"))], lambda: (1, 10, 1), 0, None,
                                  label=label, no_undo=True))
            self.to_tip = Tooltip(e, self.tx("to_tip"))
        return e

    def make_graph(self, part, s):
        """The part shown while "Change across the shape" is ticked: Across, the row ticks, the graph, presets."""
        self.w, self.h = int(460 * s), int(170 * s)
        self.ml, self.mb = chart_edges(s, (self.widest_label(),), 56, 22)
        self.mr, self.mt = int(12 * s), int(10 * s)
        pad = int(20 * s)
        row = ttk.Frame(part)
        row.pack(anchor="w", padx=(pad, 0), pady=(4, 0))
        ttk.Label(row, text=tr("range_window.across")).pack(side="left")
        self.dir_box = ttk.Combobox(row, values=[tr(f"range_window.{d}") for d in DIRS], state="readonly", width=24)
        self.dir_box.pack(side="left", padx=4)
        self.dir_box.bind("<<ComboboxSelected>>", lambda e: self.put_graph("dir", DIRS[self.dir_box.current()]))
        Tooltip(self.dir_box, self.tx("dir_tip"))
        self.rows_var, self.join_var = tk.BooleanVar(), tk.BooleanVar()
        self.rows_check = ttk.Checkbutton(part, text=tr("range_window.rows"), variable=self.rows_var,
                                          command=lambda: self.put_graph("rows", self.rows_var.get()))
        self.rows_check.pack(anchor="w", padx=(pad, 0), pady=(4, 0))
        Tooltip(self.rows_check, self.tx("rows_tip"))
        self.join_check = ttk.Checkbutton(part, text=tr("range_window.join"), variable=self.join_var,
                                          command=lambda: self.put_graph("join", self.join_var.get()))
        self.join_check.pack(anchor="w", padx=(2 * pad, 0))
        Tooltip(self.join_check, self.tx("join_tip"))
        row = ttk.Frame(part)
        row.pack(anchor="w", padx=(2 * pad, 0), pady=(2, 0))
        self.along_label = ttk.Label(row, text=self.tx("along"))
        self.along_label.pack(side="left")
        self.along_box = ttk.Combobox(row, values=[self.tx(f"along_{a}") for a in ALONG], state="readonly",
                                      width=16)
        self.along_box.pack(side="left", padx=4)
        self.along_box.bind("<<ComboboxSelected>>",
                            lambda e: self.put_graph("count", ALONG[self.along_box.current()] == "count"))
        for w in (self.along_label, self.along_box):
            Tooltip(w, self.tx("along_tip"))
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
            ttk.Button(row, text=self.tx(key.split(".")[1]), command=lambda pts=pts: self.put_graph("graph", [list(p) for p in pts])
                       ).pack(side="left", padx=(0, 4))
        ttk.Label(part, text=self.tx("hint"), foreground=look.HINT, font=look.font(8), justify="left",
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
        return json.dumps([t.get(self.KEY) for t in self.tgts])

    def own_now(self):
        return json.dumps([self.own_part(t) for t in self.tgts])

    def retarget(self, start=False):
        """Work on the selected shapes (start: opened, which puts Gate subtract on)."""
        self.idx = target_indices(self.app)
        self.saved_sel = self.app.sel_state()  # (for the undo step)
        self.before = self.sub_now()
        self.kept = self.own_now()  # (put back by Cancel)
        self.now = self.before  # (store tells a change from this: opening changes nothing = no step edited)
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
        subs = [t.get(self.KEY) if self.has_it(t) else None for t in self.tgts]
        self.ons = [start or s is not None for s in subs]
        mine = self.saved_default()  # (shapes without it yet start from the user's own default, stage D)
        if mine is not None:
            subs = [mine if s is None else s for s in subs]
        for side in self.SIDES:
            # (an empty side = none: one a flip left with 0 ticks gets 1 tick when used, hunt 2026-10-20)
            ss = [s and (end_side(s) if side == "end" else s.get("start")) for s in subs]
            ss = [None if empty_side(s) else self.as_shown(s) for s in ss]
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
            ends = self.sides["end"]["use"]
            starts = self.sides["start"]["use"] if "start" in self.sides else []
            self.side = "start" if any(starts) and not any(ends) else "end"
        self.side_var.set(self.side)
        self.show_controls()

    def use_first(self):
        """Shapes with neither side used: the Note end side used (1 tick where it has no amount)."""
        ends, starts = self.sides["end"], self.sides.get("start")  # (Gate extend: no start side)
        for i in range(len(self.ons)):
            if self.ons[i] and not ends["use"][i] and not (starts and starts["use"][i]):
                ends["use"][i] = True
                if ends["bys"][i] is None:
                    ends["bys"][i] = self.first_amount()

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
        b = next((b for b in self.bys if b is not None), None) or self.first_amount()
        return {"to": self.scaled(b, 4), "graph": [list(p) for p in STRAIGHT], "dir": "time"}

    def show_controls(self):
        """The ticks, boxes and graph as the shapes' settings are: ticked when every shape has it, half-ticked when
        only some do; a box shows the number they share, else stays empty."""
        self.accepted()  # (the boxes show the shapes' amounts again: no red / reason left from before)
        def tick(var, w, flags):
            var.set(bool(flags) and all(flags))
            w.state(["alternate"] if any(flags) and not all(flags) else ["!alternate"])
        tick(self.on_var, self.on_tick, self.ons)
        tick(self.use_var, self.use_tick, self.use)
        start = self.side == "start"
        self.use_tick.config(text=self.tx("use_" + self.side))
        self.use_tip.set_text(self.tx(f"use_{self.side}_tip"))
        self.touch_tick.config(text=self.tx("touch_start" if start else "touch"))
        self.touch_tip.set_text(self.tx("touch_start_tip" if start else "touch_tip"))
        for t in self.by_tips:
            t.set_text(self.tx("by_start_tip" if start else "by_tip"))
        tick(self.touch_var, self.touch_tick, self.touches)
        tick(self.vary_var, self.vary_tick, self.varies)
        got = {self.text_of(b) for b in self.bys if b is not None}
        self.by_var.set(got.pop() if len(got) == 1 and None not in self.bys else "")
        g = self.g or self.new_graph()
        self.to_var.set(self.text_of(g["to"]))
        self.dir_box.current(DIRS.index(g["dir"]))
        self.rows_var.set(bool(g.get("rows")))
        self.join_var.set(bool(g.get("join")))
        self.along_box.current(1 if g.get("count") else 0)
        self.show_unit()

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
                self.put_own(saved[i], sub)

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
        self.app.add_undo_step(json.dumps(saved), self.tx("step"), sel)
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

    # ---- the amounts: [% or None, beats], ticks shown whole

    def text_of(self, v):
        return amount_text(v, self.app.ppq)

    def read(self, text, ppq=None):
        """Typed text -> [% or None, beats] (whole % and whole ticks, each at most its most), or None (self.why = the
        reason to show, "" = none: an empty box). ppq: the one it was typed for (default the project's)."""
        ppq = ppq or self.app.ppq
        self.why = ""
        if not text.strip():
            return None
        try:
            p, t = amount(text)
        except ValueError as e:
            self.why = str(e)
            return None
        if p < -1e-9 or t < -1e-9:  # (a hair under 0 from decimals, 0.3% - 0.1% - 0.2%, counts as 0: hunt)
            self.why = self.tx("below_zero")
            return None
        ticks = math.floor(max(t, 0) + 0.5)
        p = min(math.floor(max(p, 0) + 0.5), MOST_PCT)  # (rounded FIRST: 0.4% + 10 = plain 10 ticks, hunt)
        if "%" not in text and "％" not in text or p == 0 and ticks > 0:
            p = None
        return [None if p is None else float(p), min(ticks / ppq, MOST)]

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
                b = self.first_amount() if b is None else b
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
            return self.refused(box, var)
        self.accepted(box)
        if key == "by":
            self.bys = [beats] * len(self.idx)
        elif self.g:
            self.g["to"] = beats
            self.g_all = True
        self.show_controls()
        self.store()
        self.hist.mark(key)

    def refused(self, box, var):
        """A typed amount that can't be used: left as typed, red, with why under it (user 2026-11-22); an emptied
        box: back to the last good one."""
        if not getattr(self, "why", ""):
            return self.show_controls()
        box.config(style="Bad.TEntry")
        if not hasattr(self, "why_label"):
            row = self.by_box.master
            self.why_label = ttk.Label(row.master, foreground=look.ERROR, wraplength=int(420 * self.s), justify="left")
        self.why_label.config(text=self.why)
        if not self.why_label.winfo_manager():
            self.why_label.pack(anchor="w", after=self.by_box.master, pady=(2, 0))

    def accepted(self, box=None):
        """A typed amount taken, or the boxes refilled from the shapes (a tab, Ctrl+Z, another selection, back to the
        last good number: hunt, the red + why stayed): the red + why gone."""
        for b in (self.by_box, self.to_box):
            if str(b.cget("style")) == "Bad.TEntry":
                b.config(style="TEntry")
        if hasattr(self, "why_label") and self.why_label.winfo_manager():
            self.why_label.pack_forget()

    # ---- showing

    def put_unit(self):
        """The word after the boxes: "of each note" (only %), ticks (a ticks part in every box shown), or both."""
        texts = [self.by_var.get()] + ([self.to_var.get()] if self.vary_var.get() else [])
        kinds, t = set(), 0
        for text in texts:
            try:
                t = amount(text)[1]
            except ValueError:
                t = 0
            kinds.add("pct" if ("%" in text or "％" in text) and not t else "ticks")
        if kinds == {"pct"}:
            self.by_unit.config(text=self.tx("pct_word"))
        elif kinds == {"ticks"}:
            self.by_unit.config(text=unit_text(fmt(t), "subtract.ticks", "subtract.tick"))
        else:
            self.by_unit.config(text=self.tx("both_word"))

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
        self.by_label.config(text=self.tx("from" if vary else "by"))
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

    def ends(self):
        """The first and second amount shown (the first shape's, the graph's)."""
        got = [b for b in self.bys if b is not None]
        g = self.g or self.new_graph()
        return got[0] if got else self.first_amount(), g["to"]

    def gates(self):
        """The first and second amount in whole steps, for the pale steps (None: % and ticks both, none drawn)."""
        a, b = self.ends()
        if kind(a) == kind(b) == "ticks":
            return ticks_of(a[1], self.app.ppq), ticks_of(b[1], self.app.ppq)
        if kind(a) == kind(b) == "pct":
            return int(a[0]), int(b[0])
        return None

    def label_at(self, y):
        """The amount the notes get at height y (0..1) of the graph, as text."""
        a, b = self.ends()
        ppq = self.app.ppq
        parts = []
        if a[0] is not None or b[0] is not None:
            parts.append(self.tx("pct_n", n=y_gate(int(a[0] or 0), int(b[0] or 0), y)))
        ta, tb = ticks_of(a[1], ppq), ticks_of(b[1], ppq)
        if ta or tb or not parts:
            n = y_gate(ta, tb, y)
            parts.append(trn("n.ticks", n, n=n))
        return " + ".join(parts)

    def draw(self):
        """The graph: amounts at 0, 25 ... 100 % up, the whole-tick amounts the notes get (pale steps), the line."""
        cv, s = self.canvas, self.s
        cv.delete("all")
        if not self.g:
            return
        x0, x1, top, bot = self.u2x(0), self.u2x(1), self.y2c(1), self.y2c(0)
        for j in range(1, 4):
            cv.create_line(self.u2x(j / 4), top, self.u2x(j / 4), bot, fill=look.CHART_GRID)
        for k in range(5):
            y = k / 4
            cv.create_line(x0, self.y2c(y), x1, self.y2c(y), fill=look.CHART_GRID)
            cv.create_text(x0 - 4 * s, self.y2c(y), text=self.label_at(y), anchor="e", fill=look.LABEL,
                           font=look.font(7))
        cv.create_rectangle(x0, top, x1, bot, outline=look.CHART_FRAME)
        d = self.g["dir"]
        ends = {"time": ("first", "last"), "keys": ("range_window.low", "range_window.high"),
                "keys_down": ("range_window.high", "range_window.low")}[d]
        if d == "time":
            ends = ("row_first", "row_last") if self.g.get("rows") else ("first", "last")
        cv.create_text(x0, bot + 4 * s, text=self.tx(ends[0]), anchor="nw", fill=look.LABEL, font=look.font(7))
        cv.create_text(x1, bot + 4 * s, text=self.tx(ends[1]), anchor="ne", fill=look.LABEL, font=look.font(7))
        ab = self.gates()
        a, b = ab or (0, 0)
        span = (b - a) or 1
        if ab is not None and few_steps(self.pts, a, b):
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
            cv.create_text(x1 - 4 * s, top + 4 * s, text=self.tx("at", at=fmt(round(at[0] * 100, 1)),
                                                                 ticks=self.label_at(float(at[1]))),
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
        changed = [s for s in self.SIDES if sides[s] != self.sides[s]]
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
        had = [self.has_it(t) for t in self.tgts]
        was = getattr(self, "now", None)
        for i, t in enumerate(self.tgts):
            if not self.ons[i]:
                t.pop(self.KEY, None)
                continue
            t[self.KEY] = self.made_of(i)
        self.now = self.sub_now()
        self.show()
        # (its settings changed on shapes that keep it: the steps after it skipped until the user leaves it, stage C;
        # put on / taken off: whole; nothing changed (the window opening): as it was)
        if self.now != was:
            edited = had and all(had) and all(self.has_it(t) for t in self.tgts)
            self.app.colour_edit_set((self.KEY, 0) if edited else None)
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
            self.app.tips.show(self.T, wait=True)  # (the first time: what it does)

    def cancel(self):
        if self.idx and self.idx[-1] < len(self.app.shapes) and self.own_now() != self.kept:
            self.app.roll.cancel_draft()  # (as it was: an Add between step tried out stays one)
            for i, part in zip(self.idx, json.loads(self.kept)):
                self.put_own(self.app.shapes[i], part)
        self.app.colour_edit_drop(self.KEY)  # (whole again: made just below)
        self.close()
        self.app.shapes_changed()

    def close(self):
        self.closed = True
        setattr(self.app, self.ATTR, None)
        self.app.colour_edit_leave(self.KEY)  # (the steps after it worked out again)
        self.destroy()
        self.app.roll.focus_set()


def default_editor(cls):
    """cls (SubtractWindow / ExtendWindow) changing the user's DEFAULT only (Settings → User set defaults, double-click
    a row; user 2026-11-23): the same window on a stand-in shape no one sees; no shape, selection or undo step
    touched; OK saves it as the default, Cancel changes nothing. done(): called after OK (the list refreshed)."""
    class Editor(cls):
        ATTR = cls.ATTR + "_defaults"
        editing_default = True

        def __init__(self, app, done=None):
            self.fake = {"kind": "line", "pts": [[0, 60], [1, 61]]}
            saved = self.saved_default()
            if saved is not None:
                self.fake[cls.KEY] = saved
            self.done = done
            super().__init__(app)
            self.title(tr("defaults.edit_title", name=self.tx("title")))
            self.tidy_editor()

        @property
        def tgts(self):
            return [self.fake]

        def pin_row(self, row):
            """(no pin here: OK is what saves)"""

        def retarget(self, start=False):
            self.idx = [0]
            self.saved_sel = None
            self.before = self.now = self.sub_now()
            self.kept = self.own_now()
            self.load(True)
            self.store()
            self.hist.reset()

        def store(self):
            for i, t in enumerate(self.tgts):
                if not self.ons[i]:
                    t.pop(self.KEY, None)
                else:
                    t[self.KEY] = self.made_of(i)
            self.now = self.sub_now()
            self.show()

        def sync(self):
            """(the main window's selection doesn't matter here)"""

        def settle(self, also=()):
            """(nothing tried on shapes to keep)"""

        def show(self):
            super().show()
            self.on_tick.state(["disabled"])  # (a default goes only with Reset, never from here: user 2026-11-23)

        def ok(self):
            self.take_typing()
            self.save_default()
            self.close()

        def save_default(self):
            mine = dict(self.fake.get(self.KEY) or {})
            mine.pop("cuts", None)
            try:
                shape_defaults.put(self.KEY, {"settings": mine})
            except OSError:
                self.app.status.config(text=tr("defaults.cant_save"))
            defaults_changed(self.app)  # (the Settings list if it's still open + the open windows' pins: hunt)

        def forget_default(self):
            try:
                shape_defaults.forget(self.KEY)
            except OSError:
                self.app.status.config(text=tr("defaults.cant_save"))
            defaults_changed(self.app)

        def cancel(self):
            self.close()

        def close(self):
            self.closed = True
            setattr(self.app, self.ATTR, None)
            self.destroy()
    Editor.__name__ = cls.__name__ + "Default"
    return Editor


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
