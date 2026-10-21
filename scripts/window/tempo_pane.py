"""
The Tempo pane under the piano roll (below the velocity pane when both are shown): the project's tempo line
(notes/tempo.py) on a doubling scale, sharing the piano roll's time axis.

Tools in the bar (user 2026-10-21): Select (a toggle: off = the pencil), then Linear / Curve / Formula (pencil
kinds; none pressed = plain points). Pencil: a click adds a point (held, it follows the mouse), a drag on a point /
catch-up line moves it; Linear / Curve / Formula drag a part from the press to the let-go (points between are
replaced). Select: click picks a point / catch-up line / part, a box picks many, dragging moves what's picked (time
snaps to the grid, BPM to the BPM snap; Shift free). Double-click a point: type its BPM. Right-click a point / line:
delete it (all picked ones when it's picked); elsewhere a menu: Add catch-up BPM here. A catch-up line starts the
catch-up, which ends at the next point; picked, the bar shows its shape and the BPM it works out (red when it
can't). The wheel over the numbers on the left zooms up / down (Shift scrolls), dragging them scrolls; elsewhere the
wheel works as on the piano roll.
"""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.tempo import (CURVES, EVERY, FORMULA, MAX_BPM, MIN_BPM, SCALES, SHAPES, clamp_bpm, curve, formula_fn,
                         catch_get, catch_set, keeps, layout,
                         plans, shown_bpm, step_edges)
from roll.zoombar import add_zoom_bars
from roll.roll_shared import (BOX_STILL, CTRL, PENCIL_CURSOR, SELECT_CURSOR, SHIFT, draw_boxes, grab_while_panning,
                              set_cursor)
from window import look
from window.widgets import ShortLabel, Tooltip, unit_word

BPM_SNAPS = (0, 0.1, 1, 5, 10)  # the BPM snap dropdown's steps (0 = off)
NICE = (0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000, 5000000,
        10000000)  # numbers filled in on zoom
LABEL_GAP = 15  # px between two numbers on the left (times the window's scale)
DRAWS = {"line": "line", "curve": "s", "formula": "formula"}  # pencil kind -> the curve the drawn part gets


def bpm_text(v):
    v = float(v)
    return f"{v:,.0f}" if v >= 10000 else fmt(round(v, 3))


class TempoBox(ttk.Frame):
    """The Tempo pane with two bar rows on top: tools, BPM snap, Graph; then what's picked + a hint."""

    def __init__(self, parent, app, scale):
        super().__init__(parent)
        self.app = app
        bar = self.bar = ttk.Frame(self, padding=(2, 2))
        bar.pack(fill="x")
        ttk.Label(bar, text=tr("tempo.tempo")).pack(side="left", padx=(2, 8))
        self.tool = tk.StringVar(value="point")  # select / point / line / curve / formula
        self.tool_vars = {}
        for key, label in (("select", tr("app.select")), (None, None), ("line", tr("app.linear")),
                           ("curve", tr("app.curve")), ("formula", tr("app.formula"))):
            if key is None:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=5)
                continue
            var = self.tool_vars[key] = tk.BooleanVar(value=False)
            ttk.Checkbutton(bar, text=label, variable=var, style="Toolbutton", takefocus=False,
                            command=lambda k=key: self.pick_tool(k)).pack(side="left", padx=1)
        lb = ttk.Label(bar, text=tr("tempo.every"))
        lb.pack(side="left", padx=(10, 0))
        self.every_var = tk.StringVar(value=str(EVERY))
        self.every_box = ttk.Entry(bar, textvariable=self.every_var, width=6)
        self.every_box.pack(side="left", padx=(4, 0))
        self.every_box.bind("<Return>", lambda e: (self.typed_every(), app.roll.focus_set()))
        self.every_box.bind("<FocusOut>", lambda e: self.left_box(self.typed_every))
        unit_word(ttk.Label(bar), self.every_var, "unit.ticks").pack(side="left", padx=(2, 0))
        for w in (lb, self.every_box):
            Tooltip(w, tr("tempo.every_tip"))
        ttk.Label(bar, text=tr("tempo.bpm_snap")).pack(side="left", padx=(10, 0))
        self.snap = ttk.Combobox(bar, state="readonly", width=5,
                                 values=[tr("tempo.off") if s == 0 else fmt(s) for s in BPM_SNAPS])
        self.snap.current(BPM_SNAPS.index(1))
        self.snap.pack(side="left", padx=(4, 10))
        self.snap.bind("<<ComboboxSelected>>", lambda e: app.roll.focus_set())
        self.bars_var = tk.BooleanVar(value=True)
        b = ttk.Checkbutton(bar, text=tr("tempo.bars"), variable=self.bars_var, takefocus=False,
                            command=self.picked_bars)
        b.pack(side="left", padx=(0, 10))
        Tooltip(b, tr("tempo.bars_tip"))
        # second row: what's picked (one of these rows shows) + the hint
        row2 = ttk.Frame(self, padding=(2, 0, 2, 2))
        row2.pack(fill="x")
        tall = max(self.every_box.winfo_reqheight(), self.snap.winfo_reqheight())
        ttk.Frame(row2, width=1, height=tall).pack(side="left")  # (keeps its height when nothing's picked)
        self.point_row = row = ttk.Frame(row2)
        ttk.Label(row, text=tr("tempo.bpm")).pack(side="left")
        self.bpm_var = tk.StringVar()
        self.bpm_box = ttk.Entry(row, textvariable=self.bpm_var, width=10)
        self.bpm_box.pack(side="left", padx=(4, 0))
        self.bpm_box.bind("<Return>", lambda e: (self.typed_bpm(), app.roll.focus_set()))
        self.bpm_box.bind("<FocusOut>", lambda e: self.left_box(self.typed_bpm))
        self.bpm_box.bind("<FocusIn>", lambda e: self.typing_for())
        self.part_row = row = ttk.Frame(row2)
        ttk.Label(row, text=tr("tempo.curve")).pack(side="left")
        self.curve = ttk.Combobox(row, state="readonly", width=20, values=[tr("tempo.curve_" + k) for k in CURVES])
        self.curve.pack(side="left", padx=(4, 0))
        self.curve.bind("<<ComboboxSelected>>", lambda e: self.picked_curve())
        self.scale_box = ttk.Combobox(row, state="readonly", width=12, values=[tr("tempo.scale_" + k) for k in SCALES])
        self.scale_box.pack(side="left", padx=(4, 0))
        self.scale_box.bind("<<ComboboxSelected>>", lambda e: self.picked_scale())
        Tooltip(self.scale_box, tr("tempo.scale_tip"))
        self.keep_var = tk.BooleanVar(value=False)
        self.keep_box = ttk.Checkbutton(row, text=tr("tempo.keep"), variable=self.keep_var, takefocus=False,
                                        command=self.picked_keep)
        self.keep_box.pack(side="left", padx=(8, 0))
        Tooltip(self.keep_box, tr("tempo.keep_tip"))
        self.draw_scale = "lin"  # what new drawn parts get (the last one picked)
        self.catch_row = row = ttk.Frame(row2)
        ttk.Label(row, text=tr("tempo.catch")).pack(side="left")
        self.shape = ttk.Combobox(row, state="readonly", width=12, values=[tr("tempo.shape_" + k) for k in SHAPES])
        self.shape.pack(side="left", padx=(4, 0))
        self.shape.bind("<<ComboboxSelected>>", lambda e: self.picked_shape())
        self.formula_var = tk.StringVar()
        self.formula_box = ttk.Entry(row2, textvariable=self.formula_var, width=16)
        self.formula_box.bind("<Return>", lambda e: self.typed_formula())
        self.formula_box.bind("<FocusOut>", lambda e: self.left_box(self.typed_formula))
        self.formula_box.bind("<FocusIn>", lambda e: self.typing_for())
        self._typed_for = None  # (what the BPM / Formula box's typing goes to: what was picked when it was clicked)
        self._committed = False
        self.formula_tip = Tooltip(self.formula_box, "")
        self.draw_formula = FORMULA  # what the Formula pencil draws
        self.result = ttk.Label(row2, text="", font=look.font(9))
        fit = ttk.Button(bar, text=tr("tempo.fit"), command=lambda: (self.pane.fit_view(), app.roll.focus_set()))
        fit.pack(side="right", padx=(6, 2))
        Tooltip(fit, tr("tempo.fit_tip"))
        self.graph = ttk.Combobox(bar, state="readonly", width=11, values=[tr("tempo.scale_" + k) for k in SCALES])
        self.graph.current(0)
        self.graph.pack(side="right")
        self.graph.bind("<<ComboboxSelected>>", lambda e: (self.pane.set_scale(self.graph.current() == 1),
                                                           app.roll.focus_set()))
        lb = ttk.Label(bar, text=tr("tempo.graph"))
        lb.pack(side="right", padx=(6, 4))
        for w in (lb, self.graph):
            Tooltip(w, tr("tempo.graph_tip"))
        self.hint = ShortLabel(row2, least=8, text=tr("tempo.hint_point"), foreground=look.HINT, font=look.font(8))
        self.hint.pack(side="left", fill="x", expand=True, padx=(2, 0))
        holder = tk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.pane = TempoPane(holder, self, app, scale)
        add_zoom_bars(holder, self.pane, axes=(False,), up="bpm")  # (only up / down: time is the piano roll's)
        self._shown = None

    def pick_tool(self, key):
        """A tool button pressed: at most one is down; none down = the pencil's plain points."""
        on = self.tool_vars[key].get()
        for k, var in self.tool_vars.items():
            if k != key:
                var.set(False)
        self.tool.set(key if on else "point")
        self.pane.placing = None  # (adding a catch-up: called off)
        self.pick_tool_hint()
        self.pane.drag = None
        self.sync()
        self.pane.request_redraw()
        self.app.roll.focus_set()

    def pick_tool_hint(self):
        self.hint.config(text=tr("tempo.hint_" + ("draw" if self.tool.get() in DRAWS else self.tool.get())))

    # ------------------------------------------------------------ the bar follows what's picked

    def sync(self):
        """Show the row for what's picked, with its values (called after every change)."""
        pane, tempo = self.pane, self.app.tempo
        pick = pane.picked()
        kind = pick[0] if pick else None
        rows = {"point": [self.point_row], "part": [self.part_row], "catch": [self.catch_row]}.get(kind, [])
        formula = False
        if kind == "point":
            if self.focus_get() is not self.bpm_box:
                vals = {tempo["points"][i][1] for i in pick[1]}
                self.bpm_var.set(bpm_text(vals.pop()) if len(vals) == 1 else "")
        elif kind == "part":
            p = tempo["points"][pick[1]]
            last = pick[1] == len(tempo["points"]) - 1  # (after the last point: it holds on, only Keep timing)
            self.curve.current(CURVES.index(p[2]))
            self.curve.state(["disabled"] if last else ["!disabled"])
            self.scale_box.current(SCALES.index(p[4]))
            self.scale_box.state(["disabled"] if p[2] == "hold" or last else ["!disabled"])
            self.keep_var.set(keeps(p))
            formula = p[2] == "formula"
            text, tip = p[3] or FORMULA, tr("tempo.formula_tip")
        elif kind == "catch":
            c = tempo["catch"][pick[1]]
            self.shape.current(SHAPES.index(c[1]))
            formula = c[1] == "formula"
            text, tip = c[2] or FORMULA, tr("tempo.catch_formula_tip")
        if not formula and kind is None and self.tool.get() == "formula":  # (the Formula pencil's formula)
            formula, text, tip = True, self.draw_formula, tr("tempo.formula_tip")
        if formula:
            if self.focus_get() is not self.formula_box:
                self.formula_var.set(text)
            self.formula_tip.set_text(tip)
            rows.append(self.formula_box)
        if kind == "catch":
            res, bad = pane.catch_result(pick[1])
            self.result.config(text=res, foreground=look.ERROR if bad else look.TEMPO_CATCH)
            rows.append(self.result)
        if self.focus_get() is not self.every_box:
            self.every_var.set(str(tempo.get("every", EVERY)))
        self.bars_var.set(tempo.get("bars", True))
        shown = tuple(map(str, rows))
        if shown != self._shown:  # (packed again only when it changes: no flashing while dragging)
            for w in (self.point_row, self.part_row, self.catch_row, self.formula_box, self.result):
                w.pack_forget()
            for w in rows:
                w.pack(side="left", padx=(0, 8), before=self.hint)
            self._shown = shown

    def bpm_snap(self):
        return BPM_SNAPS[max(0, self.snap.current())]

    def typing_for(self):
        """A box clicked: its typing goes to what's picked now, even if a pane click picks something else first."""
        pick = self.pane.picked()
        tempo = self.app.tempo
        if pick and pick[0] == "point":
            self._typed_for = ("point", [tempo["points"][i] for i in pick[1]])
        elif pick:
            self._typed_for = (pick[0], tempo["points" if pick[0] == "part" else "catch"][pick[1]])
        else:
            self._typed_for = None
        self._committed = False

    def target(self):
        """(kind, item(s)) the typing goes to, still in the line; else what's picked now."""
        tempo = self.app.tempo
        alive = tempo["points"] + tempo["catch"]
        if self._typed_for:
            kind, it = self._typed_for
            if kind == "point":
                pts = [p for p in it if any(p is a for a in alive)]
                if pts:
                    return kind, pts
            elif any(it is a for a in alive):
                return kind, it
        pick = self.pane.picked()
        if not pick:
            return None
        if pick[0] == "point":
            return "point", [tempo["points"][i] for i in pick[1]]
        return pick[0], tempo["points" if pick[0] == "part" else "catch"][pick[1]]

    def commit_typing(self):
        """A press in the pane: a BPM / formula typed without Enter goes to what it was typed for first."""
        focus = self.focus_get()
        for box, done in ((self.bpm_box, self.typed_bpm), (self.formula_box, self.typed_formula),
                          (self.every_box, self.typed_every)):
            if focus is box:
                done()
                self._committed = True

    def left_box(self, done):
        """The box lost the keyboard: what's typed goes in, unless a pane press took it already."""
        if self._committed:
            self._committed = False
            self._typed_for = None
            return self.sync()
        done()
        self._typed_for = None

    def typed_bpm(self):
        """The BPM box: every picked point gets it."""
        got = self.target()
        if not got or got[0] != "point":
            return
        pts = got[1]
        try:
            v = calc(self.bpm_var.get().replace(",", ""))
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ValueError
        except (ValueError, ArithmeticError, TypeError, SyntaxError):
            self.sync_bpm(pts)
            return
        v = clamp_bpm(v)
        if any(p[1] != v for p in pts):
            self.app.push_undo(name=tr("tempo.step_bpm"))
            for p in pts:
                p[1] = v
            self.app.tempo_changed()
        self.sync_bpm(pts)

    def sync_bpm(self, pts):
        vals = {p[1] for p in pts}
        self.bpm_var.set(bpm_text(vals.pop()) if len(vals) == 1 else "")

    def picked_curve(self):
        pick = self.pane.picked()
        if pick and pick[0] == "part":
            p = self.app.tempo["points"][pick[1]]
            kind = CURVES[self.curve.current()]
            if kind != p[2]:
                self.app.push_undo(name=tr("tempo.step_curve"))
                p[2] = kind
                if kind == "formula" and not p[3]:
                    p[3] = FORMULA
                self.app.tempo_changed()
        self.app.roll.focus_set()

    def picked_scale(self):
        pick = self.pane.picked()
        if pick and pick[0] == "part":
            p = self.app.tempo["points"][pick[1]]
            scale = SCALES[self.scale_box.current()]
            self.draw_scale = scale
            if scale != p[4]:
                self.app.push_undo(name=tr("tempo.step_curve"))
                p[4] = scale
                self.app.tempo_changed()
        self.app.roll.focus_set()

    def picked_keep(self):
        pick = self.pane.picked()
        if pick and pick[0] == "part":
            p = self.app.tempo["points"][pick[1]]
            if self.keep_var.get() != keeps(p):
                self.app.push_undo(name=tr("tempo.step_keep"))
                p[5:] = [self.keep_var.get()]
                self.app.tempo_changed()
        self.app.roll.focus_set()

    def picked_bars(self):
        tempo = self.app.tempo
        if self.bars_var.get() != tempo.get("bars", True):
            self.app.push_undo(name=tr("tempo.step_bars"))
            tempo["bars"] = self.bars_var.get()
            self.app.tempo_changed()
        self.app.roll.focus_set()

    def typed_every(self):
        tempo = self.app.tempo
        try:
            v = int(calc(self.every_var.get()))
            if not 1 <= v <= 65535:
                raise ValueError
        except (ValueError, ArithmeticError, TypeError, SyntaxError, OverflowError):
            self.every_var.set(str(tempo.get("every", EVERY)))
            return
        if v != tempo.get("every", EVERY):
            self.app.push_undo(name=tr("tempo.step_every"))
            tempo["every"] = v
            self.app.tempo_changed()

    def picked_shape(self):
        pick = self.pane.picked()
        if pick and pick[0] == "catch":
            c = self.app.tempo["catch"][pick[1]]
            shape = SHAPES[self.shape.current()]
            if shape != c[1]:
                self.app.push_undo(name=tr("tempo.step_catch_shape"))
                c[1] = shape
                if shape == "formula" and not c[2]:
                    c[2] = FORMULA
                self.app.tempo_changed()
        self.app.roll.focus_set()

    def typed_formula(self):
        got = self.target()
        text = self.formula_var.get().strip()
        if not got or got[0] not in ("part", "catch"):
            if self.tool.get() == "formula" and text != self.draw_formula:
                if formula_fn(text) is None:
                    self.app.status.config(text=tr("tempo.formula_bad"))
                    self.formula_var.set(self.draw_formula)
                else:
                    self.draw_formula = text
            return
        pick, item = got
        pick = (pick,)
        at = 3 if pick[0] == "part" else 2
        if text == (item[at] or FORMULA):
            return
        if formula_fn(text) is None:
            self.app.status.config(text=tr("tempo.formula_bad"))
            self.formula_var.set(item[at] or FORMULA)
            return
        self.app.push_undo(name=tr("tempo.step_curve" if pick[0] == "part" else "tempo.step_catch_shape"))
        item[at] = text
        if pick[0] == "part":
            self.draw_formula = text
        self.app.tempo_changed()


class TempoPane(tk.Canvas):
    def __init__(self, parent, box, app, scale):
        super().__init__(parent, bg=look.ROLL_BG, highlightthickness=0)
        self.box, self.app, self.scale = box, app, scale
        self.bars = ()  # (add_zoom_bars: the scrollbar right of it)
        set_cursor(self, PENCIL_CURSOR)
        self.pad = int(8 * scale)  # room above the top number / below the bottom one
        self.log = False  # the up / down scale: linear (False, default) or exponential (True)
        self.view = None  # (lowest, highest) shown, in the scale's units (ax); None = fitted on first draw
        self.sel = []  # the picked points / catch-up lines (their own lists: they keep their place when sorted)
        self.part = None  # a picked part: its first point's list
        self.placing = None  # adding a catch-up: {"step": 1} (drag the yellow area) / {"step": 2, "a", "b", "hover"}
        self.band = None  # the kept Select box (first, last beat): the pane's whole height
        self.drag = None
        self._pan = None
        self._redraw_pending = False
        self._typing = None  # the BPM box over a point (double-click)
        self.bind("<Configure>", lambda e: self.request_redraw())
        self.bind("<Map>", lambda e: self.request_redraw())
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Double-Button-1>", self.on_double)
        self.bind("<Motion>", self.on_motion)
        self.bind("<ButtonPress-3>", self.on_right)
        self.bind("<ButtonPress-2>", self.start_pan)
        self.bind("<B2-Motion>", self.pan_to)
        self.bind("<MouseWheel>", self.on_wheel)
        grab_while_panning(self)

    # ------------------------------------------------------------ coordinates

    def base(self):
        """The project's BPM (the box's number, 120 while it holds no good one)."""
        try:
            v = calc(self.app.pvar["bpm"].get())
            if 4 <= v <= 100000:
                return float(v)
        except (ValueError, ArithmeticError, TypeError, SyntaxError):
            pass
        return 120.0

    def area(self):
        return self.pad, self.winfo_height() - 1 - self.pad

    # The up / down scale (user 2026-10-21): linear by default (what many MIDI programs show, from 0 BPM), or
    # exponential (each step up doubles: huge spikes fit). self.view = (lowest, highest) in its own units: BPM, or
    # log2 BPM.

    def ax(self, v):
        return np.log2(np.maximum(v, 1e-9)) if self.log else np.asarray(v, float)

    def unax(self, u):
        return 2.0 ** u if self.log else u

    def limits(self):
        """(lowest, highest) the view can show: 0 BPM (linear) / the slowest a file holds, up to the fastest."""
        return (math.log2(MIN_BPM), math.log2(MAX_BPM)) if self.log else (0.0, MAX_BPM)

    def least_span(self):
        return 0.02 if self.log else 0.5

    def b2y(self, v):
        lo, hi = self.view
        top, bot = self.area()
        return bot - (self.ax(v) - lo) / (hi - lo) * (bot - top)

    def y2u(self, y):
        lo, hi = self.view
        top, bot = self.area()
        return lo + (bot - y) / max(1, bot - top) * (hi - lo)

    def y2b(self, y):
        return clamp_bpm(self.unax(self.y2u(y)))

    def set_scale(self, log):
        """Linear / exponential picked: the whole line fitted in the new scale."""
        if log != self.log:
            self.log = log
            self.fit_view()

    def set_view(self, lo, hi):
        """The BPMs shown, kept between 0 (exponential: the slowest a file holds) and the fastest (user: stop there,
        like the velocity pane at 0; the pane's padding stays below / above). Zoomed out past both: all of it."""
        floor, roof = self.limits()
        span = min(max(hi - lo, self.least_span()), roof - floor)
        lo = min(max(lo, floor), roof - span)
        self.view = (lo, lo + span)
        for bar in self.bars:
            bar.refresh()

    # (the scrollbar right of the pane, roll/zoombar.py: measured down from the fastest tempo)

    def bar_view(self, across):
        if self.view is None:
            return None
        floor, roof = self.limits()
        return roof - self.view[1], roof - self.view[0], roof - floor

    def bar_move(self, across, a):
        lo, hi = self.view
        roof = self.limits()[1]
        self.set_view(roof - a - (hi - lo), roof - a)
        self.request_redraw()

    def bar_zoom(self, across, a, b, which):
        roof = self.limits()[1]
        if b - a > self.least_span():
            self.set_view(roof - b, roof - a)
            self.request_redraw()

    def zoom_step(self, across, f):
        """The "+" / "-" buttons: zoom around the middle (f > 1 = in)."""
        lo, hi = self.view
        mid, half = (lo + hi) / 2, (hi - lo) / 2 / f
        if 2 * half > self.least_span():
            self.set_view(mid - half, mid + half)
            self.request_redraw()

    def fit_view(self, redraw=True):
        """Up / down zoom to show the whole tempo line (catch-ups too) with a little room."""
        tempo, base = self.app.tempo, self.base()
        vals = [base] + [p[1] for p in tempo["points"]]
        vals += [pl["bpm"] for _, pl in plans(tempo, base, self.app.ppq) if pl["ok"]]
        if self.log:
            lo, hi = math.log2(min(vals)), math.log2(max(vals))
            if hi - lo < 2:  # (at least two doublings shown: a flat line in the middle)
                mid = (hi + lo) / 2
                lo, hi = mid - 1, mid + 1
            self.set_view(lo - 0.35, hi + 0.35)
        else:
            lo, hi = min(vals), max(vals)
            pad = max((hi - lo) * 0.15, hi * 0.1, 1.0)
            self.set_view(max(0.0, lo - pad), hi + pad)
        if redraw:
            self.request_redraw()

    # ------------------------------------------------------------ what's picked

    def is_sel(self, item):
        return any(item is s for s in self.sel)

    def sel_idx(self, key):
        """Indices of the picked items in tempo[key] ("points" / "catch")."""
        return [i for i, it in enumerate(self.app.tempo[key]) if self.is_sel(it)]

    def picked(self):
        """What the bar shows: ("point", [indices]), ("part", i), ("catch", j) or None."""
        tempo = self.app.tempo
        had = len(self.sel)
        self.sel = [s for s in self.sel if any(s is it for it in tempo["points"] + tempo["catch"])]
        if self.band and len(self.sel) < had:  # (undo brought new lists back: the box picks what's in it again)
            self.sel = self.in_span(self.band)
        pts = tempo["points"]
        if self.part is not None:
            at = [i for i, p in enumerate(pts) if p is self.part]
            if at:
                return ("part", at[0])
            self.part = None
        points, lines = self.sel_idx("points"), self.sel_idx("catch")
        if points:
            return ("point", points)
        if len(lines) == 1:
            return ("catch", lines[0])
        return None

    def set_sel(self, items, part=None):
        self.sel, self.part = list(items), part
        self.box.sync()
        self.request_redraw()

    # ------------------------------------------------------------ finding things under the mouse

    def point_at(self, x, y):
        roll, r = self.app.roll, 7 * self.scale
        best = None
        for i, (b, v, *_) in enumerate(self.app.tempo["points"]):
            d = math.hypot(roll.t2x(b) - x, float(self.b2y(v)) - y)
            if d <= r and (best is None or d < best[0]):
                best = (d, i)
        return best and best[1]

    def catch_at(self, x):
        roll = self.app.roll
        for i, c in enumerate(self.app.tempo["catch"]):
            if abs(roll.t2x(c[0]) - x) <= 5 * self.scale:
                return i
        return None

    def start_at(self, x):
        """The catch-up whose yellow area's left edge is at x (its index), or None."""
        roll = self.app.roll
        for j, (st, _) in enumerate(plans(self.app.tempo, self.base(), self.app.ppq)):
            if st["end"] is not None and st["from"] < st["line"] and abs(roll.t2x(st["from"]) - x) <= 4 * self.scale:
                return j
        return None

    def end_at(self, x):
        """The catch-up whose teal area's right edge is at x (its index), or None."""
        roll = self.app.roll
        for j, (st, _) in enumerate(plans(self.app.tempo, self.base(), self.app.ppq)):
            if st["end"] is not None and abs(roll.t2x(st["end"]) - x) <= 4 * self.scale:
                return j
        return None

    # ------------------------------------------------------------ adding a catch-up: two drags (user 2026-10-21)

    def start_placing(self):
        """Add catch-up BPM: drag the yellow area (what it counts), then the teal one (where its BPM plays). A kept
        Select box is the yellow area already."""
        if self.band:  # (the box comes back if it's called off)
            self.placing = {"step": 2, "a": self.band[0], "b": self.band[1], "hover": None, "band": self.band}
            self.band = None
        else:
            self.placing = {"step": 1}
        self.box.hint.config(text=tr("tempo.hint_catch%d" % self.placing["step"]))
        self.app.roll.focus_set()  # (Esc reaches it, whatever box had the keyboard)
        self.request_redraw()

    def cancel_placing(self):
        """Adding a catch-up called off (Esc, right-click, another tool, Ctrl+Z): nothing left drawn, the kept
        Select box it started from back."""
        if self.placing:
            self.band = self.placing.get("band") or self.band
            self.placing = None
            if self.drag and self.drag["kind"] == "catch1":
                self.drag = None
            self.box.pick_tool_hint()
            self.request_redraw()

    def place_catch(self, e):
        """Step 2's press: the catch-up made (line where the yellow area ends), its end following the mouse."""
        p, tempo = self.placing, self.app.tempo
        tick = 1.0 / max(1, self.app.ppq)
        line = max(p["b"], tick)
        if any(abs(c[0] - line) < 1e-9 for c in tempo["catch"]):
            return self.cancel_placing()
        self.app.push_undo(name=tr("tempo.step_catch_add"))
        item = [line, "flat", ""]
        catch_set(item, 3, p["a"] if p["a"] < line else None)
        catch_set(item, 4, max(self.snap_beat(self.app.roll.x2t(e.x), e), line + tick))
        tempo["catch"].insert(sum(1 for c in tempo["catch"] if c[0] < line), item)
        self.placing = None
        self.box.pick_tool_hint()
        self.sel, self.part = [item], None
        self.drag = {"kind": "end", "item": item, "x": e.x, "pushed": True}
        self.app.tempo_changed()

    def part_at(self, beat):
        """The part at beat: its first point's index (after the last point: the last one, for Keep timing)."""
        pts = self.app.tempo["points"]
        for i in range(len(pts)):
            if pts[i][0] <= beat and (i == len(pts) - 1 or beat < pts[i + 1][0]):
                return i
        return None

    def snap_beat(self, b, e):
        if e.state & SHIFT:
            return max(0.0, b)
        sb = self.app.snap_beats()
        return max(0.0, round(b / sb) * sb) if sb else max(0.0, b)

    def snap_bpm(self, v, e):
        step = self.box.bpm_snap()
        if e.state & SHIFT or not step:
            return clamp_bpm(v)
        return clamp_bpm(max(step, round(v / step) * step))

    # ------------------------------------------------------------ mouse

    def on_press(self, e):
        roll, tempo = self.app.roll, self.app.tempo
        self.close_typing(True)
        if roll.sx is None or self.view is None:
            return
        self.box.commit_typing()  # (a BPM / formula typed without Enter: to what it was typed for, first)
        roll.focus_set()  # (Space / Ctrl+Z keep working)
        self.app.key_pane = self  # (Delete: the points picked here)
        if e.x < roll.kb_w:  # the numbers: drag to scroll up / down
            self.drag = {"kind": "view", "y": e.y, "view": self.view}
            return
        if self.placing:  # adding a catch-up: the yellow area dragged, then the teal one
            if self.placing["step"] == 1:
                self.drag = {"kind": "catch1", "x": e.x, "x1": e.x, "b0": roll.x2t(e.x),
                             "shift0": bool(e.state & SHIFT), "span": None}
            else:
                self.place_catch(e)
            return
        tool = self.box.tool.get()
        self.picked()  # (what's picked still there)
        i, j = self.point_at(e.x, e.y), self.catch_at(e.x)
        item = tempo["points"][i] if i is not None else tempo["catch"][j] if j is not None else None
        if item is not None:
            if tool == "select" and e.state & CTRL:
                self.band = None
                if self.is_sel(item):  # (Ctrl+click on a picked one: unpicked)
                    return self.set_sel([s for s in self.sel if s is not item])
                self.set_sel(self.sel + [item])
            elif not (tool == "select" and self.is_sel(item)):
                self.band = None
                self.set_sel([item])
            return self.start_move(e, item, pushed=False)
        for kind, j in (("start", self.start_at(e.x)), ("end", self.end_at(e.x))):
            if j is not None:  # the yellow area's left edge / the teal area's right edge
                self.band = None
                self.set_sel([tempo["catch"][j]])
                self.drag = {"kind": kind, "item": tempo["catch"][j], "x": e.x, "pushed": False}
                return
        b, v = self.snap_beat(roll.x2t(e.x), e), self.snap_bpm(self.y2b(e.y), e)
        if tool == "select":
            beat = roll.x2t(e.x)
            if self.band and self.band[0] <= beat <= self.band[1] and not e.state & CTRL:
                return self.start_move(e, None, pushed=False)  # (inside the box: all it picked, in time)
            self.part, self.band = None, None
            self.drag = {"kind": "box", "x": e.x, "x1": e.x, "b0": beat, "shift0": bool(e.state & SHIFT),
                         "span": None, "before": list(self.sel), "ctrl": bool(e.state & CTRL)}
            self.request_redraw()
            return
        self.band = None
        if tool == "point":
            item = self.add_point(b, v)
            if item is not None:
                self.start_move(e, item, pushed=True)
        else:
            self.drag = {"kind": "draw", "b0": b, "v0": v, "b1": b, "v1": v, "x": e.x, "y": e.y}
            self.request_redraw()

    def start_move(self, e, item, pushed):
        """Hold the picked items, item the one under the mouse (None: the Select box grabbed; pushed: the step is
        made already: a new point). With Select only in time (user, like the ruler); the Select box goes along and
        its left side snaps, else the grabbed one does."""
        band = tuple(self.band) if self.band else None
        self.drag = {"kind": "move", "grab": item, "x": e.x, "y": e.y, "pushed": pushed,
                     "orig": [(s, s[0], s[1] if self.is_point(s) else None) for s in self.sel],
                     # (hand-set catch-up edges go along when many move; one line dragged alone leaves them)
                     "starts": [(s, k, s[k]) for s in self.sel if not self.is_point(s) and (band or len(self.sel) > 1)
                                for k in (3, 4) if catch_get(s, k) is not None],
                     "press": self.app.roll.x2t(e.x), "band": band, "ref": band[0] if band else item[0],
                     "time_only": self.box.tool.get() == "select",
                     "v0": item[1] if item is not None and self.is_point(item) else None, "db": 0.0}

    def is_point(self, item):
        return any(item is p for p in self.app.tempo["points"])

    def on_drag(self, e):
        if e.state & 0x200 and self._pan:  # (the middle button too: pans)
            return self.pan_to(e)
        d = self.drag
        if not d:
            return
        roll = self.app.roll
        if d["kind"] == "view":
            lo, hi = d["view"]
            top, bot = self.area()
            shift = (e.y - d["y"]) / max(1, bot - top) * (hi - lo)
            self.set_view(lo + shift, hi + shift)
            return self.request_redraw()
        if d["kind"] == "box":
            d["x1"] = e.x
            d["span"] = self.box_span(d, e)
            inside = self.in_span(d["span"]) if d["span"] else []
            self.sel = d["before"] * d["ctrl"] + [s for s in inside if not (d["ctrl"] and
                                                                            any(s is o for o in d["before"]))]
            self.box.sync()
            return self.request_redraw()
        if d["kind"] == "draw":
            d["b1"], d["v1"] = self.snap_beat(roll.x2t(e.x), e), self.snap_bpm(self.y2b(e.y), e)
            self.show_position(e)
            return self.request_redraw()
        if d["kind"] == "catch1":
            d["x1"] = e.x
            d["span"] = self.box_span(d, e)
            return self.request_redraw()
        if d["kind"] in ("start", "end"):  # a catch-up's yellow left edge / teal right edge
            if not d["pushed"]:
                if abs(e.x - d["x"]) < 3:
                    return
                self.app.push_undo(name=tr("tempo.step_catch_" + d["kind"]))
                d["pushed"] = True
            c, tick = d["item"], 1.0 / max(1, self.app.ppq)
            b = self.snap_beat(roll.x2t(e.x), e)
            catch_set(c, 3, min(b, c[0] - tick)) if d["kind"] == "start" else catch_set(c, 4, max(b, c[0] + tick))
            self.app.tempo_changed()
            return self.show_position(e)
        if not d["pushed"]:
            if abs(e.x - d["x"]) < 3 and abs(e.y - d["y"]) < 3:
                return  # (a click, not a drag yet)
            one = d["orig"][0][2] is not None if len(d["orig"]) == 1 else None
            self.app.push_undo(name=tr("tempo.step_move_many" if one is None else "tempo.step_move" if one
                                       else "tempo.step_catch_move"))
            d["pushed"] = True
        self.move_to(d, e)
        self.app.tempo_changed()
        self.show_position(e)

    def move_to(self, d, e):
        """The held items moved by the mouse: all by the same beats (and BPM); the grabbed one snaps."""
        roll, tempo = self.app.roll, self.app.tempo
        one = 1.0 / max(1, self.app.ppq)
        db = self.snap_beat(d["ref"] + roll.x2t(e.x) - d["press"], e) - d["ref"]
        db = max([db] + [-b for _, b, _ in d["orig"]] + ([-d["band"][0]] if d["band"] else []))  # (not before 0)
        dv = 0.0 if d["v0"] is None or d["time_only"] else self.snap_bpm(self.y2b(e.y), e) - d["v0"]

        def place(shift):
            for s, b, v in d["orig"]:
                s[0] = max(b + shift, 0.0 if v is not None else one)
                if v is not None:
                    s[1] = clamp_bpm(v + dv) if dv else v
            for s, k, a in d["starts"]:  # (a catch-up's edges set by hand go along)
                s[k] = max(0.0, a + shift)
            for s, b, v in d["orig"]:  # (a line dragged alone past its own hand-set edge: that edge automatic
                # at once, as opening the file makes it, so undo / save + open give the same)
                if v is None and catch_get(s, 3) is not None and s[3] >= s[0]:
                    catch_set(s, 3, None)
                if v is None and catch_get(s, 4) is not None and s[4] <= s[0]:
                    catch_set(s, 4, None)
            for key in ("points", "catch"):
                tempo[key].sort(key=lambda it: it[0])

        place(db)
        for key in ("points", "catch"):  # (two on one beat: the time stays where it last worked)
            beats = [it[0] for it in tempo[key]]
            if any(beats[k + 1] - beats[k] < one / 2 for k in range(len(beats) - 1)):
                place(d["db"])
                break
        else:
            d["db"] = db
        if d["band"]:
            self.band = (d["band"][0] + d["db"], d["band"][1] + d["db"])

    def box_span(self, d, e):
        """The Select box being dragged as (first, last beat): both ends on the NEAREST grid line (like the ruler;
        Shift = whole ticks, the pressed end as Shift was at the press). None while it's still a click."""
        if abs(d["x1"] - d["x"]) < BOX_STILL:
            return None
        tick, step = 1 / max(1, self.app.ppq), self.app.snap_beats()
        s0, s1 = (tick if sh else step or tick for sh in (d["shift0"], e.state & SHIFT))
        b0 = round(d["b0"] / s0) * s0
        b1 = round(self.app.roll.x2t(d["x1"]) / s1) * s1
        lo, hi = sorted((max(0.0, b0), max(0.0, b1)))
        return (lo, hi) if hi - lo > 1e-9 else None

    def in_span(self, span):
        """The points and catch-up lines from span[0] to span[1] (the whole height: every BPM)."""
        lo, hi = span
        tempo = self.app.tempo
        return [it for it in tempo["points"] + tempo["catch"] if lo - 1e-9 <= it[0] <= hi + 1e-9]

    def on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        if d["kind"] == "box":
            if d["span"] is None:  # a click: the part there (or nothing)
                part = self.part_at(self.app.roll.x2t(e.x))
                pts = self.app.tempo["points"]
                self.set_sel(d["before"] if d["ctrl"] else [], None if part is None else pts[part])
            else:
                self.band = d["span"]  # (the box stays, like the piano roll's)
                self.request_redraw()
        elif d["kind"] == "draw":
            self.draw_part(d)
        elif d["kind"] == "catch1":
            if d["span"] is not None:  # (a click does nothing: it waits for a drag)
                self.placing = {"step": 2, "a": d["span"][0], "b": d["span"][1], "hover": None}
                self.box.hint.config(text=tr("tempo.hint_catch2"))
            self.request_redraw()
        elif d["kind"] in ("start", "end"):
            if d["pushed"]:
                self.app.settle_history()
        elif d["kind"] == "move":
            if d["pushed"]:
                self.app.settle_history()
            elif d["time_only"] and d["grab"] is not None and not e.state & CTRL:  # (a click on one of many: it)
                self.band = None
                self.set_sel([d["grab"]])

    def end_drag(self):
        """Ctrl+Z while dragging: let go first (the step made at the first move takes it back). True when it only
        called off something that made no undo step yet (a catch-up being added, a press not moved): Ctrl+Z / Ctrl+Y
        stop there."""
        d, self.drag = self.drag, None
        only = bool(self.placing) or bool(d) and d["kind"] in ("box", "draw", "catch1", "view") or \
            bool(d) and d.get("pushed") is False
        self.cancel_placing()
        if d:
            self.request_redraw()
        return only

    def on_double(self, e):
        """Double-click on a point: type its BPM. Elsewhere: a press like any other."""
        roll = self.app.roll
        if roll.sx is None or self.view is None or e.x < roll.kb_w:
            return
        i = self.point_at(e.x, e.y)
        if i is None:
            return self.on_press(e)
        self.drag = None
        self.open_typing(i)

    def add_point(self, b, v):
        """A new point (picked); its part keeps the curve of the part it was put in. None when one is there."""
        pts = self.app.tempo["points"]
        if any(abs(p[0] - b) < 1e-9 for p in pts):
            return None
        self.app.push_undo(name=tr("tempo.step_add"))
        at = sum(1 for p in pts if p[0] < b)
        item = [float(b), clamp_bpm(v), *(pts[at - 1][2:6] if at > 0 else ("hold", "", self.box.draw_scale, False))]
        pts.insert(at, item)
        self.sel, self.part = [item], None
        self.app.tempo_changed()
        return item

    def draw_part(self, d):
        """Linear / Curve / Formula let go: a part from the press to here (the points between it replaced)."""
        b0, v0, b1, v1 = d["b0"], d["v0"], d["b1"], d["v1"]
        if b1 < b0:
            b0, v0, b1, v1 = b1, v1, b0, v0
        if b1 - b0 < 0.5 / max(1, self.app.ppq):
            self.add_point(b0, v0)
            return
        pts = self.app.tempo["points"]
        self.app.push_undo(name=tr("tempo.step_draw"))
        before = [p for p in pts if p[0] <= b1 + 1e-9]
        # (the part after keeps its curve)
        after_kind = before[-1][2:6] if before else ("hold", "", self.box.draw_scale, False)
        kind = DRAWS[self.box.tool.get()]
        text = self.box.draw_formula if kind == "formula" else ""
        keep = [p for p in pts if p[0] < b0 - 1e-9 or p[0] > b1 + 1e-9]
        held = [p for p in pts if p[0] <= b0 + 1e-9]
        first = [float(b0), clamp_bpm(v0), kind, text, self.box.draw_scale, bool(held) and keeps(held[-1])]
        last = [float(b1), clamp_bpm(v1), *after_kind]
        pts[:] = sorted(keep + [first, last], key=lambda p: p[0])
        self.sel, self.part = [], first
        self.app.tempo_changed()

    def add_catch(self, b):
        cs = self.app.tempo["catch"]
        b = max(float(b), 1.0 / max(1, self.app.ppq))
        if any(abs(c[0] - b) < 1e-9 for c in cs):
            return
        self.app.push_undo(name=tr("tempo.step_catch_add"))
        item = [b, "flat", ""]
        cs.insert(sum(1 for c in cs if c[0] < b), item)
        self.sel, self.part = [item], None
        self.app.tempo_changed()

    def on_right(self, e):
        roll = self.app.roll
        self.close_typing(True)
        if roll.sx is None or self.view is None or e.x < roll.kb_w or self.drag:
            return
        if self.placing:  # (adding a catch-up: right-click calls it off)
            return self.cancel_placing()
        self.app.key_pane = self
        self.picked()
        tempo = self.app.tempo
        i, j = self.point_at(e.x, e.y), self.catch_at(e.x)
        item = tempo["points"][i] if i is not None else tempo["catch"][j] if j is not None else None
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("tempo.add_catch"), command=self.start_placing)
        for k, at, key in ((3, self.start_at, "tempo.catch_auto"), (4, self.end_at, "tempo.catch_end_auto")):
            n = at(e.x) if item is None else None
            if n is not None and catch_get(tempo["catch"][n], k) is not None:  # (an edge set by hand)
                menu.add_separator()
                menu.add_command(label=tr(key), command=lambda c=tempo["catch"][n], k=k: self.auto_edge(c, k))
        if item is not None:  # (user: right-click doesn't delete; Delete picked ones, or this menu)
            gone = self.sel if self.is_sel(item) else [item]
            menu.add_separator()
            menu.add_command(label=tr("tempo.delete"), command=lambda: self.delete_items(gone))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def auto_edge(self, c, k):
        """A catch-up's yellow start (k = 3) / teal end (k = 4) back to where Spiderweb puts it (one undo step)."""
        if catch_get(c, k) is not None:
            self.app.push_undo(name=tr("tempo.step_catch_start" if k == 3 else "tempo.step_catch_end"))
            catch_set(c, k, None)
            self.app.tempo_changed()

    def delete_items(self, gone):
        """These points / catch-up lines go (one undo step)."""
        tempo = self.app.tempo
        gone = [g for g in gone if any(g is it for it in tempo["points"] + tempo["catch"])]
        if not gone:
            return
        if len(gone) > 1:
            name = "tempo.step_delete_many"
        else:
            name = "tempo.step_delete" if self.is_point(gone[0]) else "tempo.step_catch_delete"
        self.app.push_undo(name=tr(name))
        for key in ("points", "catch"):
            tempo[key][:] = [it for it in tempo[key] if not any(it is g for g in gone)]
        self.sel = [s for s in self.sel if not any(s is g for g in gone)]
        self.app.tempo_changed()

    def delete_picked(self):
        """The Delete key while this pane was clicked last: the picked points / catch-up lines go (the Select box
        stays, empty, like the piano roll's). Always takes the key (user's pick in the hunt: never the piano
        roll's shapes, which may be off screen); nothing while the mouse holds something here."""
        self.picked()
        if self.sel and not self.drag:
            self.delete_items(list(self.sel))
        return True

    def forget(self):
        """Hidden / another project opened: the Select box, picks and a catch-up being added go, Delete and Esc
        are the piano roll's again."""
        self.placing, self.drag, self.band, self.sel, self.part = None, None, None, [], None
        self.box.pick_tool_hint()
        if getattr(self.app, "key_pane", None) is self:
            self.app.key_pane = None

    def pick_state(self):
        """What's picked, for an undo step (JSON): the Select box, the picked points / lines and part by place."""
        tempo, part = self.app.tempo, self.picked()
        return json.dumps({"band": list(self.band) if self.band else None, "points": self.sel_idx("points"),
                           "catch": self.sel_idx("catch"), "part": part[1] if part and part[0] == "part" else None})

    def restore_pick(self, text):
        """An undo step's pick_state back (on the tempo line it brought back)."""
        try:
            got = json.loads(text)
            tempo = self.app.tempo
            self.band = tuple(got["band"]) if got.get("band") else None
            self.sel = [tempo[key][i] for key in ("points", "catch") for i in got.get(key, ())
                        if 0 <= i < len(tempo[key])]
            part = got.get("part")
            self.part = tempo["points"][part] if part is not None and 0 <= part < len(tempo["points"]) else None
        except (ValueError, TypeError, KeyError, AttributeError):
            self.band, self.sel, self.part = None, [], None

    def on_motion(self, e):
        self.show_position(e)
        roll = self.app.roll
        if roll.sx is None or self.view is None:
            return
        if self.placing and self.placing["step"] == 2:  # (the teal area shown up to the mouse before the press)
            self.placing["hover"] = self.snap_beat(roll.x2t(e.x), e)
            self.request_redraw()
        if e.x < roll.kb_w:
            want = "sb_v_double_arrow"
        elif self.placing:
            want = "sb_h_double_arrow"
        elif self.point_at(e.x, e.y) is not None:
            want = "fleur"
        elif self.catch_at(e.x) is not None or self.start_at(e.x) is not None or self.end_at(e.x) is not None:
            want = "sb_h_double_arrow"
        elif self.box.tool.get() != "select":
            want = PENCIL_CURSOR
        elif self.band and self.band[0] <= roll.x2t(e.x) <= self.band[1]:
            want = "sb_h_double_arrow"  # (moves what the box picked, in time)
        else:
            want = SELECT_CURSOR
        if not self.drag:
            set_cursor(self, want)

    def show_position(self, e):
        roll = self.app.roll
        text = None
        if roll.sx is not None and self.view is not None and e.x >= roll.kb_w:
            text = roll.time_text(e.x) + tr("tempo.position", bpm=bpm_text(self.y2b(e.y)))
        self.app.show_position(text)

    def start_pan(self, e):
        self._pan = (e.x, e.y, self.app.roll.view_t, self.view)

    def pan_to(self, e):
        roll = self.app.roll
        if not self._pan or roll.sx is None or self.view is None:
            return
        x0, y0, t0, (lo, hi) = self._pan
        roll.view_t = t0 - (e.x - x0) / roll.sx
        roll.clamp_view()
        top, bot = self.area()
        shift = (e.y - y0) / max(1, bot - top) * (hi - lo)
        self.set_view(lo + shift, hi + shift)
        roll.request_redraw()

    def on_wheel(self, e):
        """Over the numbers: zoom the BPMs (Shift: scroll them). Elsewhere: as the piano roll (scroll, Ctrl zooms
        time)."""
        roll = self.app.roll
        if roll.sx is None or self.view is None:
            return
        up = e.delta > 0
        if e.x < roll.kb_w:
            lo, hi = self.view
            if e.state & SHIFT:
                step = (hi - lo) * 0.1 * (1 if up else -1)
                self.set_view(lo + step, hi + step)
            else:
                at = self.y2u(e.y)
                f = 0.8 if up else 1.25
                if hi - lo > self.least_span() or not up:
                    self.set_view(at - (at - lo) * f, at + (hi - at) * f)
            return self.request_redraw()
        if e.state & CTRL:
            b = roll.x2t(e.x)
            roll.sx = min(100000.0, max(0.05, roll.sx * (1.25 if up else 0.8)))
            roll.view_t = b - (e.x - roll.kb_w) / roll.sx
        else:
            roll.view_t += (-1 if up else 1) * 120 / roll.sx
        roll.clamp_view()
        roll.request_redraw()

    # ------------------------------------------------------------ typing a BPM on a point

    def open_typing(self, i):
        self.close_typing(True)
        item = self.app.tempo["points"][i]
        self.set_sel([item])
        b, v = item[:2]
        var = tk.StringVar(value=bpm_text(v))
        box = tk.Entry(self, textvariable=var, width=9, font=look.font(9), relief="solid", bd=1)
        x, y = self.app.roll.t2x(b), float(self.b2y(v))
        box.place(x=min(max(x + 8, 0), max(0, self.winfo_width() - 90)), y=max(0, y - 22))
        box.select_range(0, "end")
        box.focus_set()
        box.bind("<Return>", lambda e: self.close_typing(True))
        box.bind("<KP_Enter>", lambda e: self.close_typing(True))
        box.bind("<Escape>", lambda e: self.close_typing(False))
        box.bind("<FocusOut>", lambda e: self.close_typing(True))
        self._typing = (box, var, item)

    def close_typing(self, keep):
        if not self._typing:
            return
        box, var, p = self._typing
        self._typing = None
        if keep and any(p is q for q in self.app.tempo["points"]):
            try:
                v = calc(var.get().replace(",", ""))
                if isinstance(v, (int, float)) and math.isfinite(v) and v > 0 and clamp_bpm(v) != p[1]:
                    self.app.push_undo(name=tr("tempo.step_bpm"))
                    p[1] = clamp_bpm(v)
                    self.app.tempo_changed()
            except (ValueError, ArithmeticError, TypeError, SyntaxError):
                pass
        box.destroy()
        self.app.roll.focus_set()

    # ------------------------------------------------------------ the catch-up's words in the bar

    def catch_result(self, j):
        """(text, can't) for catch-up line j: its BPM, or why it can't work."""
        tempo, base = self.app.tempo, self.base()
        st, plan = plans(tempo, base, self.app.ppq)[j]
        if plan["empty"]:
            return tr("tempo.catch_empty"), True
        if not plan["ok"]:
            if plan["latest"] is not None and plan["latest"] < st["line"]:
                return tr("tempo.catch_bad", where=self.app.bar_text(plan["latest"])), True
            return tr("tempo.catch_cant"), True
        key = "tempo.catch_bpm" if st["shape"] == "flat" else (
            "tempo.catch_top" if plan["bpm"] >= st["base"] else "tempo.catch_low")
        return tr(key, bpm=bpm_text(plan["bpm"])), False

    # ------------------------------------------------------------ drawing

    def request_redraw(self):
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self.redraw)

    def labels(self):
        """[(bpm, strong)] numbers for the left side: the project's BPM, the file's limits, (exponential: doublings
        of 15), then rounder numbers as they fit."""
        lo, hi = self.view
        top, bot = self.area()
        per = (bot - top) / max(1e-9, hi - lo)  # px per BPM / per doubling
        gap = LABEL_GAP * self.scale
        placed = []

        def a(v):
            return float(self.ax(v))

        def room(v):
            return all(abs(a(v) - a(p)) * per >= gap for p, _ in placed)

        base = self.base()
        if lo <= a(base) <= hi:
            placed.append((base, True))
        if not self.log and lo <= 0 and room(0):  # (linear: 0 BPM at the bottom, like the velocity pane)
            placed.append((0, False))
        for v in (MIN_BPM, MAX_BPM):  # (where the faint red lines are)
            if lo - 1e-9 <= a(v) <= hi + 1e-9 and room(v):
                placed.append((v, False))
        if self.log:
            for n in range(math.floor(lo - math.log2(15)), math.ceil(hi - math.log2(15)) + 1):
                v = 15 * 2.0 ** n
                if lo <= a(v) <= hi and MIN_BPM <= v <= MAX_BPM and room(v):
                    placed.append((v, False))
        lo_v, hi_v = self.unax(lo), self.unax(hi)
        for step in reversed(NICE):
            if step > hi_v:
                continue
            k0, k1 = math.ceil(lo_v / step - 1e-9), math.floor(hi_v / step + 1e-9)
            if k1 - k0 > 400:
                break
            for k in range(max(1 if self.log else 0, k0), k1 + 1):
                v = k * step
                if room(v):
                    placed.append((v, False))
        return placed

    def line_xy(self, worked, w, kb):
        """The tempo line as (xs, beats, ys): every 2 px, plus each jump drawn exactly upright (the beat just
        before and the beat itself at the same x)."""
        roll, tempo = self.app.roll, self.app.tempo
        xs = list(np.arange(kb, w + 2, 2, dtype=float))
        beats = [roll.x2t(x) for x in xs]
        jumps = {p[0] for p in tempo["points"]} | {st["line"] for st, _ in worked}
        jumps |= {st["end"] for st, _ in worked if st["end"] is not None}
        ppq, every = self.app.ppq, tempo.get("every", EVERY)
        if every > 1 and every / ppq * roll.sx >= 3:  # (the interval's steps, upright too, while wide enough to see)
            t0, t1 = roll.x2t(kb) * ppq, roll.x2t(w + 2) * ppq
            for a, b in step_edges(tempo, worked, ppq):
                k0 = max(1, math.ceil((t0 - a) / every))
                for t in range(a + k0 * every, min(b, int(t1) + 1), every):
                    jumps.add(t / ppq)
        for b in jumps:
            x = round(roll.t2x(b))
            if kb <= x <= w + 2:
                xs += [x, x]
                beats += [b - 1e-9 * max(1.0, b), b]
        order = sorted(range(len(xs)), key=lambda k: (xs[k], beats[k]))
        xs, beats = np.array(xs)[order], np.array(beats)[order]
        vals = shown_bpm(tempo, self.base(), beats, worked, ppq)
        return xs, beats, np.clip(self.b2y(vals), -50, self.winfo_height() + 50)

    def redraw(self):
        self._redraw_pending = False
        if not self.winfo_ismapped():
            return
        self.delete("all")
        roll, app = self.app.roll, self.app
        w, h = self.winfo_width(), self.winfo_height()
        kb = int(roll.kb_w)
        if roll.sx is None or w - kb < 2 or h < 2 * self.pad + 10:
            return
        if self.view is None:
            self.fit_view(False)
        tempo, base = app.tempo, self.base()
        top, bot = self.area()
        lw = max(1, round(self.scale))
        worked = plans(tempo, base, self.app.ppq)
        # stretches: the held part (amber) and its catch-up (teal; red when it can't work)
        for st, plan in worked:
            x1 = roll.t2x(st["line"])
            if st["end"] is not None and st["from"] < st["line"]:  # (its left edge drags: where it counts from)
                x0 = roll.t2x(st["from"])
                self.create_rectangle(max(kb, x0), 0, max(kb, x1), h, fill=look.TEMPO_DRAWN_BG, outline="")
                if x0 >= kb:
                    self.create_line(x0, 0, x0, h, fill=look.TEMPO_DRAWN_EDGE, width=lw * (2 if st["manual"] else 1),
                                     dash=() if st["manual"] else (3, 3))
            if st["end"] is not None:
                xe = roll.t2x(st["end"])
                self.create_rectangle(max(kb, x1), 0, max(kb, xe), h, outline="",
                                      fill=look.TEMPO_CATCH_BG if plan["ok"] else look.TEMPO_BAD_BG)
                if st["manual_end"] and xe >= kb:  # (its right edge drags; set by hand: a solid line)
                    self.create_line(xe, 0, xe, h, fill=look.TEMPO_CATCH, width=lw * 2)
        ppq = self.app.ppq
        keep_spans = [(max(kb, roll.t2x(a / ppq)), w if e is None else min(w, roll.t2x(e / ppq)), u)
                      for _, a, e, u in layout(tempo, base, ppq)["keeps"]]
        keep_spans = [s for s in keep_spans if s[1] > s[0]]
        for x0, x1, _ in keep_spans:  # Keep timing parts (purple), their notes keep the tempo heard before
            self.create_rectangle(x0, 0, x1, h, fill=look.TEMPO_KEEP_BG, outline="")
        for x, c in roll.grid_cols(w):
            if x >= kb:
                self.create_line(x, 0, x, h, fill=c)
        for v, strong in self.labels():
            y = round(float(self.b2y(v)))
            self.create_line(kb, y, w, y, fill=look.CHART_GRID_STRONG if strong else look.CHART_GRID)
        for v, past in ((MIN_BPM, h), (MAX_BPM, 0)):  # (the slowest / fastest a file holds: past them faint red)
            y = round(float(self.b2y(v)))
            if 0 <= y <= h:
                self.create_rectangle(kb, y, w, past, fill=look.TEMPO_BAD_BG, outline="")
                self.create_line(kb, y, w, y, fill=look.TEMPO_LIMIT, width=lw)
        if not self.log:  # (0 BPM: the bottom, like the velocity pane's 0)
            y = round(float(self.b2y(0)))
            if 0 <= y <= h:
                self.create_line(kb, y, w, y, fill=look.CHART_GRID_STRONG)
        y = float(self.b2y(base))
        self.create_line(kb, y, w, y, fill=look.TEMPO_BASE, dash=(4, 3), width=lw)
        for x0, x1, u in keep_spans:  # (the tempo its notes keep: dashed, with the words on top)
            y = float(self.b2y(60_000_000 / u))
            self.create_line(x0, y, x1, y, fill=look.TEMPO_KEEP, dash=(6, 3), width=lw + 1)
            if x1 - x0 > 70 * self.scale:
                self.create_text((x0 + x1) / 2, top + 8 * self.scale, text=tr("tempo.keep"), fill=look.TEMPO_KEEP,
                                 font=look.font(8, "bold"))
        # the line itself
        xs, beats, ys = self.line_xy(worked, w, kb)
        self.create_line(*np.column_stack([xs, ys]).ravel().tolist(), fill=look.TEMPO_LINE, width=lw + 1)
        pick = self.picked()
        if pick and pick[0] == "part":  # the picked part, thicker
            pts = tempo["points"]
            a, b = pts[pick[1]][0], pts[pick[1] + 1][0] if pick[1] + 1 < len(pts) else math.inf
            on = (beats >= a) & (beats < b)
            if on.sum() >= 2:
                self.create_line(*np.column_stack([xs[on], ys[on]]).ravel().tolist(), fill=look.TEMPO_PICKED,
                                 width=lw + 3)
        # catch-up lines and their words
        for j, (st, plan) in enumerate(worked):
            ok = plan["ok"]
            col = look.TEMPO_CATCH if ok else look.ERROR
            picked = self.is_sel(tempo["catch"][j])
            if plan["latest"] is not None and (picked or not ok):  # (how far the line can go: dashed)
                xl = roll.t2x(plan["latest"])
                if kb <= xl <= w:
                    self.create_line(xl, 0, xl, h, fill=look.ERROR, dash=(4, 3), width=lw)
            x = roll.t2x(st["line"])
            if x < kb - 5 or x > w + 5:
                continue
            self.create_line(x, 0, x, h, fill=col, width=lw * (3 if picked else 2))
            r = 5 * self.scale
            self.create_polygon(x - r, 0, x + r, 0, x, r * 1.4, fill=col, outline=col)
            text = bpm_text(plan["bpm"]) + " BPM" if ok else tr("tempo.not_possible")
            xe = roll.t2x(st["end"]) if st["end"] is not None else x + 80 * self.scale
            self.create_text((x + min(xe, w)) / 2, top + 8 * self.scale, text=text, fill=col,
                             font=look.font(8, "bold"))
        self.draw_drag(lw)
        ends = {st["end"] for st, _ in worked if st["end"] is not None}
        r = 4 * self.scale
        for p in tempo["points"]:
            b, v = p[0], p[1]
            x, y = roll.t2x(b), float(self.b2y(v))
            if x < kb - r or x > w + r:
                continue
            on = self.is_sel(p)
            edge = look.TEMPO_CATCH if b in ends else look.TEMPO_LINE
            self.create_oval(x - r, y - r, x + r, y + r, fill=look.TEMPO_PICKED if on else look.CHART_POINT,
                             outline=edge, width=lw + 1)
        # the numbers on the left
        self.create_rectangle(0, 0, kb, h, fill=look.RULER_BG, outline="")
        self.create_line(kb - 1, 0, kb - 1, h, fill=look.ROLL_EDGE)
        for v, strong in self.labels():
            y = float(self.b2y(v))
            if 5 * self.scale <= y <= h - 5 * self.scale:
                self.create_text(kb - 5, y, text=bpm_text(v), anchor="e", fill=look.LABEL,
                                 font=look.font(7, "bold") if strong else look.font(7))
        self.draw_playhead()
        for bar in self.bars:
            bar.refresh()

    def draw_drag(self, lw):
        """A select box being dragged, or the part the Linear / Curve / Formula pencil is drawing."""
        d, roll = self.drag, self.app.roll
        h, kb = self.winfo_height(), roll.kb_w
        p = self.placing
        areas = []  # adding a catch-up: the yellow area being dragged, then the teal one up to the mouse
        if d and d["kind"] == "catch1" and d["span"]:
            areas.append((d["span"][0], d["span"][1], look.TEMPO_DRAWN_BG, look.TEMPO_DRAWN_EDGE))
        elif p and p["step"] == 2:
            areas.append((p["a"], p["b"], look.TEMPO_DRAWN_BG, look.TEMPO_DRAWN_EDGE))
            if p["hover"] is not None and p["hover"] > p["b"]:
                areas.append((p["b"], p["hover"], look.TEMPO_CATCH_BG, look.TEMPO_CATCH))
        for a, b, fill, edge in areas:
            x0, x1 = max(kb, roll.t2x(a)), max(kb, roll.t2x(b))
            self.create_rectangle(x0, 0, x1, h, fill=fill, outline="", stipple="gray50")
            for x in (x0, x1):
                self.create_line(x, 0, x, h, fill=edge, width=2, dash=(4, 3))
        span = d["span"] if d and d["kind"] == "box" else self.band
        if span:  # (the whole height, like the box many MIDI programs show in their lanes)
            h = self.winfo_height()
            draw_boxes(self, [(roll.t2x(span[0]), 1, roll.t2x(span[1]), h - 2)], roll.kb_w, 0, self.scale)
        if not d:
            return
        if d["kind"] == "draw" and d["b1"] != d["b0"]:
            b0, v0, b1, v1 = d["b0"], d["v0"], d["b1"], d["v1"]
            if b1 < b0:
                b0, v0, b1, v1 = b1, v1, b0, v0
            kind = DRAWS.get(self.box.tool.get(), "line")
            t = np.linspace(0, 1, 129)
            c = curve(kind, self.box.draw_formula, t)
            if self.box.draw_scale == "exp":
                vals = 2 ** (math.log2(v0) + (math.log2(v1) - math.log2(v0)) * c)
            else:
                vals = np.clip(v0 + (v1 - v0) * c, MIN_BPM, MAX_BPM)
            xs = [roll.t2x(b0 + (b1 - b0) * k) for k in t]
            ys = self.b2y(vals)
            self.create_line(*np.column_stack([xs, ys]).ravel().tolist(), fill=look.TEMPO_PICKED, width=lw + 2)

    def draw_playhead(self):
        self.delete("playhead")
        roll = self.app.roll
        if roll.sx is None or not self.winfo_ismapped():
            return
        x = round(roll.t2x(self.app.playhead))
        if roll.kb_w <= x <= self.winfo_width():
            self.create_line(x, 0, x, self.winfo_height(), fill=look.PLAY_LINE, width=max(1, round(self.scale)),
                             tags="playhead")
