"""
The Tempo pane under the piano roll (below the velocity pane when both are shown): the project's tempo line
(notes/tempo.py) on a doubling scale, sharing the piano roll's time axis.

Points: double-click empty space adds one, drag moves it (time snaps to the grid, BPM to the BPM snap; Shift free),
double-click a point to type its BPM, right-click deletes it. A click between two points picks that part: its curve
is set in the bar above. Catch-up lines (right-click empty space > Add catch-up line here) are dragged sideways;
picked, the bar shows the catch-up's shape and the BPM it works out (red when it can't). The wheel over the numbers
on the left zooms up / down (Shift scrolls), dragging them scrolls; elsewhere the wheel works as on the piano roll.
"""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.tempo import (CURVES, FORMULA, MAX_BPM, MIN_BPM, SHAPES, catch_plan, clamp_bpm, formula_fn, shown_bpm,
                         stretches)
from roll.roll_shared import CTRL, SHIFT, grab_while_panning
from window import look
from window.widgets import ShortLabel, Tooltip

BPM_SNAPS = (0, 0.1, 1, 5, 10)  # the BPM snap dropdown's steps (0 = off)
NICE = (0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000)  # numbers filled in on zoom
LABEL_GAP = 15  # px between two numbers on the left (times the window's scale)


def bpm_text(v):
    v = float(v)
    return f"{v:,.0f}" if v >= 10000 else fmt(round(v, 3))


class TempoBox(ttk.Frame):
    """The Tempo pane with its bar on top: BPM snap, what the picked point / part / catch-up line has, a hint."""

    def __init__(self, parent, app, scale):
        super().__init__(parent)
        self.app = app
        bar = self.bar = ttk.Frame(self, padding=(2, 2))
        bar.pack(fill="x")
        ttk.Label(bar, text=tr("tempo.tempo")).pack(side="left", padx=(2, 8))
        ttk.Label(bar, text=tr("tempo.bpm_snap")).pack(side="left")
        self.snap = ttk.Combobox(bar, state="readonly", width=5,
                                 values=[tr("tempo.off") if s == 0 else fmt(s) for s in BPM_SNAPS])
        self.snap.current(BPM_SNAPS.index(1))
        self.snap.pack(side="left", padx=(4, 10))
        self.snap.bind("<<ComboboxSelected>>", lambda e: app.roll.focus_set())
        # what's picked: one of these rows shows
        self.point_row = row = ttk.Frame(bar)
        ttk.Label(row, text=tr("tempo.bpm")).pack(side="left")
        self.bpm_var = tk.StringVar()
        self.bpm_box = ttk.Entry(row, textvariable=self.bpm_var, width=10)
        self.bpm_box.pack(side="left", padx=(4, 0))
        self.bpm_box.bind("<Return>", lambda e: self.typed_bpm())
        self.bpm_box.bind("<FocusOut>", lambda e: self.typed_bpm())
        self.part_row = row = ttk.Frame(bar)
        ttk.Label(row, text=tr("tempo.curve")).pack(side="left")
        self.curve = ttk.Combobox(row, state="readonly", width=20, values=[tr("tempo.curve_" + k) for k in CURVES])
        self.curve.pack(side="left", padx=(4, 0))
        self.curve.bind("<<ComboboxSelected>>", lambda e: self.picked_curve())
        self.catch_row = row = ttk.Frame(bar)
        ttk.Label(row, text=tr("tempo.catch")).pack(side="left")
        self.shape = ttk.Combobox(row, state="readonly", width=12, values=[tr("tempo.shape_" + k) for k in SHAPES])
        self.shape.pack(side="left", padx=(4, 0))
        self.shape.bind("<<ComboboxSelected>>", lambda e: self.picked_shape())
        self.formula_var = tk.StringVar()
        self.formula_box = ttk.Entry(bar, textvariable=self.formula_var, width=16)
        self.formula_box.bind("<Return>", lambda e: self.typed_formula())
        self.formula_box.bind("<FocusOut>", lambda e: self.typed_formula())
        self.formula_tip = Tooltip(self.formula_box, "")
        self.result = ttk.Label(bar, text="", font=look.font(9))
        fit = ttk.Button(bar, text=tr("tempo.fit"), command=lambda: (self.pane.fit_view(), app.roll.focus_set()))
        fit.pack(side="right", padx=(6, 2))
        Tooltip(fit, tr("tempo.fit_tip"))
        self.hint = ShortLabel(bar, least=8, text=tr("tempo.hint"), foreground=look.HINT, font=look.font(8))
        self.hint.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.pane = TempoPane(self, app, scale)
        self.pane.pack(fill="both", expand=True)
        self._shown = None

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
                self.bpm_var.set(bpm_text(tempo["points"][pick[1]][1]))
        elif kind == "part":
            p = tempo["points"][pick[1]]
            self.curve.current(CURVES.index(p[2]))
            formula = p[2] == "formula"
            text, tip = p[3] or FORMULA, tr("tempo.formula_tip")
        elif kind == "catch":
            c = tempo["catch"][pick[1]]
            self.shape.current(SHAPES.index(c[1]))
            formula = c[1] == "formula"
            text, tip = c[2] or FORMULA, tr("tempo.catch_formula_tip")
        if formula:
            if self.focus_get() is not self.formula_box:
                self.formula_var.set(text)
            self.formula_tip.set_text(tip)
            rows.append(self.formula_box)
        if kind == "catch":
            res, bad = pane.catch_result(pick[1])
            self.result.config(text=res, foreground=look.ERROR if bad else look.TEMPO_CATCH)
            rows.append(self.result)
        shown = tuple(map(str, rows))
        if shown != self._shown:  # (packed again only when it changes: no flashing while dragging)
            for w in (self.point_row, self.part_row, self.catch_row, self.formula_box, self.result):
                w.pack_forget()
            for w in rows:
                w.pack(side="left", padx=(0, 8), before=self.hint)
            self._shown = shown

    def bpm_snap(self):
        return BPM_SNAPS[max(0, self.snap.current())]

    def typed_bpm(self):
        pick = self.pane.picked()
        if not pick or pick[0] != "point":
            return
        p = self.app.tempo["points"][pick[1]]
        try:
            v = calc(self.bpm_var.get().replace(",", ""))
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ValueError
        except (ValueError, ArithmeticError, TypeError, SyntaxError):
            self.bpm_var.set(bpm_text(p[1]))
            return
        v = clamp_bpm(v)
        if v != p[1]:
            self.app.push_undo(name=tr("tempo.step_bpm"))
            p[1] = v
            self.app.tempo_changed()
        self.bpm_var.set(bpm_text(p[1]))

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
        pick = self.pane.picked()
        if not pick or pick[0] not in ("part", "catch"):
            return
        item = self.app.tempo["points" if pick[0] == "part" else "catch"][pick[1]]
        at = 3 if pick[0] == "part" else 2
        text = self.formula_var.get().strip()
        if text == (item[at] or FORMULA):
            return
        if formula_fn(text) is None:
            self.app.status.config(text=tr("tempo.formula_bad"))
            self.formula_var.set(item[at] or FORMULA)
            return
        self.app.push_undo(name=tr("tempo.step_curve" if pick[0] == "part" else "tempo.step_catch_shape"))
        item[at] = text
        self.app.tempo_changed()


class TempoPane(tk.Canvas):
    def __init__(self, box, app, scale):
        super().__init__(box, bg=look.ROLL_BG, highlightthickness=0, cursor="crosshair")
        self.box, self.app, self.scale = box, app, scale
        self.pad = int(8 * scale)  # room above the top number / below the bottom one
        self.view = None  # (lowest, highest) log2 BPM shown; None = fitted on first draw
        self.pick = None  # ("point" / "part" / "catch", index)
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

    def b2y(self, v):
        lo, hi = self.view
        top, bot = self.area()
        return bot - (np.log2(np.maximum(v, 1e-9)) - lo) / (hi - lo) * (bot - top)

    def y2b(self, y):
        lo, hi = self.view
        top, bot = self.area()
        return clamp_bpm(2 ** (lo + (bot - y) / max(1, bot - top) * (hi - lo)))

    def fit_view(self, redraw=True):
        """Up / down zoom to show the whole tempo line (catch-ups too) with a little room."""
        tempo, base = self.app.tempo, self.base()
        vals = [base] + [p[1] for p in tempo["points"]]
        for st in stretches(tempo, base):
            if st["catch"] is not None:
                plan = catch_plan(tempo, base, st)
                if plan["ok"]:
                    vals.append(plan["bpm"])
        lo, hi = math.log2(min(vals)), math.log2(max(vals))
        if hi - lo < 2:  # (at least two doublings shown: a flat line in the middle)
            mid = (hi + lo) / 2
            lo, hi = mid - 1, mid + 1
        self.view = (lo - 0.35, hi + 0.35)
        if redraw:
            self.request_redraw()

    def picked(self):
        """The pick, if it still points at something."""
        p, tempo = self.pick, self.app.tempo
        if not p:
            return None
        n = len(tempo["points"]) - (1 if p[0] == "part" else 0) if p[0] != "catch" else len(tempo["catch"])
        if 0 <= p[1] < n:
            return p
        self.pick = None
        return None

    def set_pick(self, pick):
        self.pick = pick
        self.box.sync()
        self.request_redraw()

    # ------------------------------------------------------------ finding things under the mouse

    def point_at(self, x, y):
        roll, r = self.app.roll, 7 * self.scale
        best = None
        for i, (b, v, _, _) in enumerate(self.app.tempo["points"]):
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

    def part_at(self, beat):
        pts = self.app.tempo["points"]
        for i in range(len(pts) - 1):
            if pts[i][0] <= beat < pts[i + 1][0]:
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
        roll = self.app.roll
        self.close_typing(True)
        if roll.sx is None or self.view is None:
            return
        roll.focus_set()  # (Space / Ctrl+Z keep working)
        if e.x < roll.kb_w:  # the numbers: drag to scroll up / down
            self.drag = {"kind": "view", "y": e.y, "view": self.view}
            return
        i = self.point_at(e.x, e.y)
        if i is not None:
            self.drag = {"kind": "point", "i": i, "pushed": False, "x": e.x, "y": e.y}
            return self.set_pick(("point", i))
        j = self.catch_at(e.x)
        if j is not None:
            self.drag = {"kind": "catch", "i": j, "pushed": False, "x": e.x}
            return self.set_pick(("catch", j))
        part = self.part_at(roll.x2t(e.x))
        self.set_pick(("part", part) if part is not None else None)

    def on_drag(self, e):
        if e.state & 0x200 and self._pan:  # (the middle button too: pans)
            return self.pan_to(e)
        d = self.drag
        if not d:
            return
        roll, tempo = self.app.roll, self.app.tempo
        if d["kind"] == "view":
            lo, hi = d["view"]
            top, bot = self.area()
            shift = (e.y - d["y"]) / max(1, bot - top) * (hi - lo)
            self.view = (lo + shift, hi + shift)
            return self.request_redraw()
        if not d["pushed"]:
            if abs(e.x - d["x"]) < 3 and abs(e.y - d.get("y", e.y)) < 3:
                return  # (a click, not a drag yet)
            self.app.push_undo(name=tr("tempo.step_move" if d["kind"] == "point" else "tempo.step_catch_move"))
            d["pushed"] = True
        one = 1.0 / max(1, self.app.ppq)
        if d["kind"] == "point":
            pts, i = tempo["points"], d["i"]
            b = self.snap_beat(roll.x2t(e.x), e)
            lo = pts[i - 1][0] + one if i > 0 else 0.0
            hi = pts[i + 1][0] - one if i + 1 < len(pts) else math.inf
            pts[i][0] = min(max(b, lo), hi)
            pts[i][1] = self.snap_bpm(self.y2b(e.y), e)
        else:
            cs, i = tempo["catch"], d["i"]
            b = self.snap_beat(roll.x2t(e.x), e)
            lo = cs[i - 1][0] + one if i > 0 else one
            hi = cs[i + 1][0] - one if i + 1 < len(cs) else math.inf
            cs[i][0] = min(max(b, lo), hi)
        self.app.tempo_changed()
        self.show_position(e)

    def on_release(self, e):
        d, self.drag = self.drag, None
        if d and d.get("pushed"):
            self.app.settle_history()

    def end_drag(self):
        """Ctrl+Z while dragging: let go first (the step made at the first move takes it back)."""
        self.drag = None

    def on_double(self, e):
        roll = self.app.roll
        if roll.sx is None or self.view is None or e.x < roll.kb_w:
            return
        self.drag = None
        i = self.point_at(e.x, e.y)
        if i is not None:
            return self.open_typing(i)
        if self.catch_at(e.x) is not None:
            return
        self.add_point(self.snap_beat(roll.x2t(e.x), e), self.snap_bpm(self.y2b(e.y), e))

    def add_point(self, b, v):
        pts = self.app.tempo["points"]
        if any(abs(p[0] - b) < 1e-9 for p in pts):
            return
        self.app.push_undo(name=tr("tempo.step_add"))
        at = sum(1 for p in pts if p[0] < b)
        kind = pts[at - 1][2] if at > 0 else "hold"
        pts.insert(at, [float(b), clamp_bpm(v), kind, pts[at - 1][3] if at > 0 else ""])
        self.pick = ("point", at)
        self.app.tempo_changed()

    def add_catch(self, b):
        cs = self.app.tempo["catch"]
        b = max(float(b), 1.0 / max(1, self.app.ppq))
        if any(abs(c[0] - b) < 1e-9 for c in cs):
            return
        self.app.push_undo(name=tr("tempo.step_catch_add"))
        at = sum(1 for c in cs if c[0] < b)
        cs.insert(at, [b, "flat", ""])
        self.pick = ("catch", at)
        self.app.tempo_changed()

    def on_right(self, e):
        roll = self.app.roll
        self.close_typing(True)
        if roll.sx is None or self.view is None or e.x < roll.kb_w or self.drag:
            return
        tempo = self.app.tempo
        i = self.point_at(e.x, e.y)
        if i is not None:  # (removed directly, like a path point or a sustain stop)
            self.app.push_undo(name=tr("tempo.step_delete"))
            del tempo["points"][i]
            self.pick = None
            return self.app.tempo_changed()
        j = self.catch_at(e.x)
        if j is not None:
            self.app.push_undo(name=tr("tempo.step_catch_delete"))
            del tempo["catch"][j]
            self.pick = None
            return self.app.tempo_changed()
        b, v = self.snap_beat(roll.x2t(e.x), e), self.snap_bpm(self.y2b(e.y), e)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("tempo.add_point"), command=lambda: self.add_point(b, v))
        menu.add_command(label=tr("tempo.add_catch"), command=lambda: self.add_catch(b))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def on_motion(self, e):
        self.show_position(e)
        roll = self.app.roll
        if roll.sx is None or self.view is None:
            return
        if e.x < roll.kb_w:
            want = "sb_v_double_arrow"
        elif self.point_at(e.x, e.y) is not None:
            want = "fleur"
        elif self.catch_at(e.x) is not None:
            want = "sb_h_double_arrow"
        else:
            want = "crosshair"
        if self["cursor"] != want and not self.drag:
            self.config(cursor=want)

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
        self.view = (lo + shift, hi + shift)
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
                self.view = (lo + step, hi + step)
            else:
                at = math.log2(self.y2b(e.y))
                f = 0.8 if up else 1.25
                if hi - lo > 0.02 or not up:
                    self.view = (at - (at - lo) * f, at + (hi - at) * f)
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
        self.set_pick(("point", i))
        b, v = self.app.tempo["points"][i][:2]
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
        self._typing = (box, var, i)

    def close_typing(self, keep):
        if not self._typing:
            return
        box, var, i = self._typing
        self._typing = None
        if keep and i < len(self.app.tempo["points"]):
            p = self.app.tempo["points"][i]
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
        st = stretches(tempo, base)[j]
        plan = catch_plan(tempo, base, st)
        if plan.get("empty"):
            return tr("tempo.catch_empty"), True
        if not plan["ok"]:
            if plan["earliest"] is not None and plan["earliest"] > st["line"]:
                return tr("tempo.catch_bad", where=self.app.bar_text(plan["earliest"])), True
            return tr("tempo.catch_cant"), True
        key = "tempo.catch_bpm" if st["shape"] == "flat" else (
            "tempo.catch_top" if plan["bpm"] >= base else "tempo.catch_low")
        return tr(key, bpm=bpm_text(plan["bpm"])), False

    # ------------------------------------------------------------ drawing

    def request_redraw(self):
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self.redraw)

    def labels(self):
        """[(bpm, strong)] numbers for the left side: doublings of 15 first, then rounder numbers as they fit."""
        lo, hi = self.view
        top, bot = self.area()
        per = (bot - top) / max(1e-9, hi - lo)  # px per doubling
        gap = LABEL_GAP * self.scale
        placed = []

        def room(v):
            y = math.log2(v) * per
            return all(abs(y - math.log2(p) * per) >= gap for p, _ in placed)

        base = self.base()
        if lo <= math.log2(base) <= hi:
            placed.append((base, True))
        for n in range(math.floor(lo - math.log2(15)), math.ceil(hi - math.log2(15)) + 1):
            v = 15 * 2.0 ** n
            if lo <= math.log2(v) <= hi and MIN_BPM <= v <= MAX_BPM and room(v):
                placed.append((v, False))
        lo_v, hi_v = 2 ** lo, 2 ** hi
        for step in reversed(NICE):
            if step > hi_v:
                continue
            k0, k1 = math.ceil(lo_v / step), math.floor(hi_v / step)
            if k1 - k0 > 400:
                break
            for k in range(max(1, k0), k1 + 1):
                v = k * step
                if room(v):
                    placed.append((v, False))
        return placed

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
        # stretches: the drawn part (amber) and its catch-up (teal; red when it can't work)
        plans = []
        for j, st in enumerate(stretches(tempo, base)):
            plan = catch_plan(tempo, base, st)
            plans.append((st, plan))
            if st["catch"] is None:
                continue
            x0, x1, x2 = (roll.t2x(st[k]) for k in ("from", "catch", "line"))
            first = [p[0] for p in tempo["points"] if st["from"] <= p[0] < st["catch"]]
            if first:
                self.create_rectangle(max(kb, roll.t2x(first[0])), 0, max(kb, x1), h, fill=look.TEMPO_DRAWN_BG,
                                      outline="")
            self.create_rectangle(max(kb, x1), 0, max(kb, x2), h, outline="",
                                  fill=look.TEMPO_CATCH_BG if plan["ok"] else look.TEMPO_BAD_BG)
        for x, c in roll.grid_cols(w):
            if x >= kb:
                self.create_line(x, 0, x, h, fill=c)
        for v, strong in self.labels():
            y = round(float(self.b2y(v)))
            self.create_line(kb, y, w, y, fill=look.CHART_GRID_STRONG if strong else look.CHART_GRID)
        y = float(self.b2y(base))
        self.create_line(kb, y, w, y, fill=look.TEMPO_BASE, dash=(4, 3), width=lw)
        # the line itself
        xs = np.arange(kb, w + 2, 2, dtype=float)
        vals = shown_bpm(tempo, base, np.array([roll.x2t(x) for x in xs]))
        ys = np.clip(self.b2y(vals), -50, h + 50)
        self.create_line(*np.column_stack([xs, ys]).ravel().tolist(), fill=look.TEMPO_LINE, width=lw + 1)
        pick = self.picked()
        if pick and pick[0] == "part":  # the picked part, thicker
            a, b = tempo["points"][pick[1]][0], tempo["points"][pick[1] + 1][0]
            on = (xs >= roll.t2x(a)) & (xs <= roll.t2x(b))
            if on.sum() >= 2:
                self.create_line(*np.column_stack([xs[on], ys[on]]).ravel().tolist(), fill=look.TEMPO_PICKED,
                                 width=lw + 3)
        # catch-up lines and their words
        for j, (st, plan) in enumerate(plans):
            x = roll.t2x(st["line"])
            if x < kb - 5 or x > w + 5:
                continue
            ok = plan["ok"]
            col = look.TEMPO_CATCH if ok else look.ERROR
            picked = pick == ("catch", j)
            self.create_line(x, 0, x, h, fill=col, width=lw * (3 if picked else 2))
            r = 5 * self.scale
            self.create_polygon(x - r, 0, x + r, 0, x, r * 1.4, fill=col, outline=col)
            if st["catch"] is not None:
                text = bpm_text(plan["bpm"]) + " BPM" if ok else tr("tempo.not_possible")
                xm = (max(kb, roll.t2x(st["catch"])) + x) / 2
                self.create_text(xm, top + 8 * self.scale, text=text, fill=col, font=look.font(8, "bold"))
        starts = {st["catch"] for st, _ in plans if st["catch"] is not None}
        r = 4 * self.scale
        for i, (b, v, _, _) in enumerate(tempo["points"]):
            x, y = roll.t2x(b), float(self.b2y(v))
            if x < kb - r or x > w + r:
                continue
            on = pick == ("point", i)
            edge = look.TEMPO_CATCH if b in starts else look.TEMPO_LINE
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

    def draw_playhead(self):
        self.delete("playhead")
        roll = self.app.roll
        if roll.sx is None or not self.winfo_ismapped():
            return
        x = round(roll.t2x(self.app.playhead))
        if roll.kb_w <= x <= self.winfo_width():
            self.create_line(x, 0, x, self.winfo_height(), fill=look.PLAY_LINE, width=max(1, round(self.scale)),
                             tags="playhead")
