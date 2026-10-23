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

import bisect
import copy
import json
import math
import time
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from files.system import double_click_ms
from notes.tempo import (BEND, CATCH_BEND, CURVES, EVERY, FORMULA, MAX_BPM, MIN_BPM, SCALES, SHAPES, bend_of, bpm_at,
                         bpm_text, clamp_bpm, curve, drop_loose, formula_fn,
                         catch_get, catch_set, keeps, layout,
                         plans, shown_bpm, split_at, step_edges)
from notes.tempo_file import dense_spots
from roll.zoombar import add_zoom_bars
from roll.roll_shared import (BOX_STILL, CTRL, PENCIL_CURSOR, SELECT_CURSOR, SHIFT, bind_double_right, draw_boxes,
                              grab_while_panning,
                              set_cursor)
from window import look
from window.conductor_marks import ConductorMarks
from window.widgets import Scrub, ShortLabel, Tooltip, unit_word

BPM_SNAPS = (0, 0.1, 1, 5, 10)  # the BPM snap dropdown's steps (0 = off)
NICE = (0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000, 5000000,
        10000000)  # numbers filled in on zoom
LABEL_GAP = 15  # px between two numbers on the left (times the window's scale)
DOUBLE_MS = double_click_ms()  # how quick a second click has to be to make a double click (the system's setting)
MENU_CLICK_S = 0.25  # a press this soon after the right-click menu closed = the click that closed it (Windows)
def ids(items):
    """The ids of items: "is it one of these" without going through them all each time."""
    return {id(it) for it in items}


def ask_domino(parent, n, notes, marks=False):
    """Paste from Domino with tempo changes while the Tempo pane has points: "replace" (that stretch), "add" (among
    the points), "notes" (notes only; offered only when notes come too) or None (nothing pasted). marks: asked
    about time / key signatures too (the pane holds the user's own)."""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(tr("tempo.domino_title"))
    win.resizable(False, False)
    win.transient(parent.winfo_toplevel())
    body = ttk.Frame(win, padding=(14, 14, 14, 8))
    body.pack(fill="both", expand=True)
    ttk.Label(body, image="::tk::icons::question").grid(row=0, column=0, sticky="n", padx=(0, 12))
    text = (tr("tempo.domino_ask_marks") if marks else trn("tempo.domino_ask", n, n=n)) + "\n\n" + tr(
        "tempo.domino_how") + (
        "\n" + tr("tempo.domino_how_notes") if notes else "")
    ttk.Label(body, text=text, wraplength=440, justify="left").grid(row=0, column=1, sticky="w")
    btns = ttk.Frame(win, padding=(14, 4, 14, 12))
    btns.pack(fill="x")
    answer = [None]

    def close(how):
        answer[0] = how
        win.destroy()
    ttk.Button(btns, text=tr("tempo.domino_cancel"), command=lambda: close(None)).pack(side="right")
    if notes:
        ttk.Button(btns, text=tr("tempo.domino_notes"), command=lambda: close("notes")).pack(side="right", padx=(0, 6))
    ttk.Button(btns, text=tr("tempo.domino_add"), command=lambda: close("add")).pack(side="right", padx=(0, 6))
    first = ttk.Button(btns, text=tr("tempo.domino_replace"), command=lambda: close("replace"))
    first.pack(side="right", padx=(0, 6))
    win.bind("<Return>", lambda e: e.widget.invoke() if isinstance(e.widget, ttk.Button) else close("replace"))
    win.bind("<Escape>", lambda e: close(None))
    win.protocol("WM_DELETE_WINDOW", lambda: close(None))
    win.update_idletasks()
    top = parent.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - win.winfo_reqwidth()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    from window.widgets import remember_place
    remember_place(win, "domino_tempo")
    win.deiconify()
    win.grab_set()
    first.focus_set()
    win.wait_window()
    return answer[0]


def joined(spans):
    """Select boxes (first, last beat) sorted, the ones touching or overlapping joined into one (a jump on the
    edge two boxes share was lost, hunt 2026-10-22)."""
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


DENSE_WAIT_MS = 300  # Keep timing notes too close: worked out this long after the tempo / notes stop changing
DRAWS = {"line": "line", "formula": "formula"}  # pencil kind -> the curve the drawn part gets (bent by its handle)


def part_kind(p, scale):
    """What a new point's part takes from the part it was put in (p; None: before the first point): curve,
    formula, BPM change, Keep timing, bend (its own copy)."""
    if p is None:
        return ["hold", "", scale, False, list(BEND)]
    return [p[2], p[3], p[4], keeps(p), list(bend_of(p))]


class TempoBox(ttk.Frame):
    """The Tempo pane with two bar rows on top: tools, BPM snap, Graph; then what's picked + a hint."""

    def __init__(self, parent, app, scale):
        super().__init__(parent)
        self.app = app
        bar = self.bar = ttk.Frame(self, padding=(2, 2))
        bar.pack(fill="x")
        ttk.Label(bar, text=tr("tempo.tempo")).pack(side="left", padx=(2, 4))
        # the exact BPM at the play line (user 2026-10-22; a set width: playing doesn't push the tools about)
        # (always 2 decimals, room for the fastest a file holds: "60,000,000.00"; the tools after it never move)
        self.play_bpm = ttk.Label(bar, text="", width=len(f"{60_000_000:,.2f}") + 1, foreground=look.INFO)
        self.play_bpm.pack(side="left", padx=(0, 4))
        Tooltip(self.play_bpm, tr("tempo.play_bpm_tip"))
        # (one always down, user 2026-10-22, like many MIDI programs: Line's click = a point, no pencil-only mode)
        self.tool = tk.StringVar(value="line")  # select / line / formula
        self.last_pencil = "line"  # (double right-click: Select <-> this)
        self.tool_vars = {}
        for key, label in (("select", tr("app.select")), (None, None), ("line", tr("tempo.line_tool")),
                           ("formula", tr("app.formula"))):
            if key is None:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=5)
                continue
            var = self.tool_vars[key] = tk.BooleanVar(value=key == "line")
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
        # drag the word / Up, Down / the wheel (user 2026-10-22), like every number box
        Scrub(app, [(self.every_box, self.every_var, self.typed_every)], (1, 10, 1), 1, 65535, label=lb)
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
        lb = ttk.Label(row, text=tr("tempo.bpm"))
        lb.pack(side="left")
        self.bpm_var = tk.StringVar()
        self.bpm_box = ttk.Entry(row, textvariable=self.bpm_var, width=10)
        self.bpm_box.pack(side="left", padx=(4, 0))
        self.bpm_box.bind("<Return>", lambda e: (self.typed_bpm(), app.roll.focus_set()))
        self.bpm_box.bind("<FocusOut>", lambda e: self.left_box(self.typed_bpm))
        self.bpm_box.bind("<FocusIn>", lambda e: self.typing_for())
        # drag the word / Up, Down / the wheel (user 2026-10-22), like every number box; several BPMs: from the first
        sc = Scrub(app, [(self.bpm_box, self.bpm_var, self.scrubbed_bpm)], (1, 10, 0.01), MIN_BPM, MAX_BPM, label=lb)
        sc.read = lambda text: calc(str(text).replace(",", ""))  # (blank: the first one's number itself)
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
        self.hint = ShortLabel(row2, least=8, text=tr("tempo.hint_draw"), foreground=look.HINT, font=look.font(8))
        self.hint.pack(side="left", fill="x", expand=True, padx=(2, 0))
        holder = tk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.pane = TempoPane(holder, self, app, scale)
        add_zoom_bars(holder, self.pane, axes=(False,), up="bpm")  # (only up / down: time is the piano roll's)
        self._shown = None

    def pick_tool(self, key):
        """A tool button pressed: it's the one down (pressed again: stays down)."""
        for k, var in self.tool_vars.items():
            var.set(k == key)
        self.tool.set(key)
        if self.tool.get() != "select":
            self.last_pencil = self.tool.get()
        self.pane.confirm()  # (the line just drawn: done)
        self.pane.placing = None  # (adding a catch-up: called off)
        self.pick_tool_hint()
        self.pane.drag = None
        self.sync()
        self.pane.request_redraw()
        self.app.roll.focus_set()

    def toggle_select(self):
        """Double right-click on the pane: Select <-> the pencil used last (Line or Formula)."""
        self.pick_tool(self.last_pencil if self.tool.get() == "select" else "select")

    def tool_hint_key(self):
        return "tempo.hint_" + ("draw" if self.tool.get() in DRAWS else self.tool.get())

    def pick_tool_hint(self, spot=None):
        """The hint line: what can be done where the mouse is (spot: "on_point", "numbers"...), else on empty
        space with the tool picked."""
        key = "tempo.hint_" + spot if spot else self.tool_hint_key()
        if spot is None or getattr(self, "_hint_key", None) != key:  # (each mouse move: only when it changes)
            self._hint_key = key
            self.hint.config(text=tr(key))

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
                self.sync_bpm([tempo["points"][i] for i in pick[1]])
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
        alive = ids(tempo["points"] + tempo["catch"])
        if self._typed_for:
            kind, it = self._typed_for
            if kind == "point":
                pts = [p for p in it if id(p) in alive]
                if pts:
                    return kind, pts
            elif id(it) in alive:
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

    def scrubbed_bpm(self):
        """The BPM word dragged (or Up / Down / the wheel): to what's picked now, unless the box is being typed in."""
        if self.focus_get() is not self.bpm_box:
            self._typed_for = None
        self.typed_bpm()

    def sync_bpm(self, pts):
        vals = {p[1] for p in pts}
        self.bpm_box.blank_from = pts[0][1] if len(vals) > 1 else None  # (dragging the word: from the first one's)
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
                p.extend([False] * (6 - len(p)))
                p[5] = self.keep_var.get()  # (only the tick: the bend after it stays, hunt 2026-10-22)
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


class TempoPane(ConductorMarks, tk.Canvas):
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
        self.bands = []  # the kept Select boxes [(first, last beat)]: the pane's whole height (Ctrl+drag adds one)
        self.live = None  # the line just drawn, its ends still movable: {"base", "ends", "first", "seen"}
        self._dense = (None, [])  # (what it was worked out for, beats where Keep timing notes are too close)
        self._dense_job = None
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
        self.bind("<Leave>", lambda e: self.drag or self.placing or self.box.pick_tool_hint())
        bind_double_right(self, self.on_right, self.on_double_right)
        self.menu_wait = None  # a right-click on empty space: its menu, waiting to see if it's a double click
        self.menu_shut = 0.0  # when the right-click menu last closed (perf_counter)
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
        """item is picked. (By id, the set kept while self.sel is the same list: thousands of points picked made
        every check go through them all, hunt 2026-10-22: a 4000-point paste froze for 5 s.)"""
        got = getattr(self, "_sel_ids", None)
        if got is None or got[0] is not self.sel or got[1] != len(self.sel):
            got = self._sel_ids = (self.sel, len(self.sel), {id(s) for s in self.sel})
        return id(item) in got[2]

    def sel_idx(self, key):
        """Indices of the picked items in tempo[key] ("points" / "catch")."""
        return [i for i, it in enumerate(self.app.tempo[key]) if self.is_sel(it)]

    def picked(self):
        """What the bar shows: ("point", [indices]), ("part", i), ("catch", j) or None."""
        tempo = self.app.tempo
        had = len(self.sel)
        alive = ids(tempo["points"] + tempo["catch"])
        if any(id(s) not in alive for s in self.sel):
            self.sel = [s for s in self.sel if id(s) in alive]
        if self.bands and len(self.sel) < had:  # (undo brought new lists back: the boxes pick what's in them again)
            self.sel = self.in_spans(self.bands)
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

    def select_all(self):
        """Ctrl+A while this pane was clicked last: every point and catch-up line, in one Select box over them
        (user 2026-10-22: Delete then clears the whole line)."""
        tempo = self.app.tempo
        items = tempo["points"] + tempo["catch"]
        if self.drag or self.placing:
            return
        lo, hi = min((it[0] for it in items), default=0), max((it[0] for it in items), default=0)
        self.bands = [(lo, hi)] if hi - lo > 1e-9 else []
        self.set_sel(items)

    def drop_picks(self):
        """Esc while this pane was clicked last: nothing picked, the Select boxes gone (user 2026-10-22)."""
        if self.drag:
            return
        self.bands = []
        self.set_sel([])

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
        Select box is the yellow area already (several: from the first one's start to the last one's end)."""
        if self.bands:  # (the boxes come back if it's called off)
            a, b = self.bands_reach()
            self.placing = {"step": 2, "a": a, "b": b, "hover": None, "bands": self.bands}
            self.bands = []
        else:
            self.placing = {"step": 1}
        self.box.hint.config(text=tr("tempo.hint_catch%d" % self.placing["step"]))
        self.app.roll.focus_set()  # (Esc reaches it, whatever box had the keyboard)
        self.request_redraw()

    def cancel_placing(self):
        """Adding a catch-up called off (Esc, right-click, another tool, Ctrl+Z): nothing left drawn, the kept
        Select box it started from back."""
        if self.placing:
            self.bands = self.placing.get("bands") or self.bands
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

    # ------------------------------------------------------------ a line part's bend handle (user 2026-10-21)

    def handle_pos(self, i):
        """(beat, BPM) of part i's round bend handle (the curve's middle; an S-curve: its first half's), None when
        it has none (held / formula / the last part / both ends one BPM) or a working catch-up hides the line
        there (user: it was left hanging in the air)."""
        pts = self.app.tempo["points"]
        if (i is None or i + 1 >= len(pts) or pts[i][2] not in ("line", "s") or pts[i][1] == pts[i + 1][1]
                or pts[i + 1][0] <= pts[i][0]):  # (where a glide arrives: no part of its own)
            return None
        p, q = pts[i], pts[i + 1]
        cx, cy = bend_of(p)
        x, y = (2 * cx + 1) / 4, (2 * cy + 1) / 4
        if p[2] == "s":
            x, y = x / 2, y / 2
        b = p[0] + (q[0] - p[0]) * x
        if any(plan["ok"] and st["line"] <= b < st["end"] for st, plan in plans(self.app.tempo, self.base(),
                                                                                 self.app.ppq)):
            return None
        return b, self.along_bpm(p, q, y)

    def catch_handle_pos(self, j):
        """(beat, BPM) of catch-up j's round bend handle (fast then slow / slow then fast: its curve's middle), None
        when it has none (another shape, or it can't work)."""
        st, plan = plans(self.app.tempo, self.base(), self.app.ppq)[j]
        if st["shape"] not in ("ease", "rise") or not plan["ok"]:
            return None
        cx, cy = st["bend"] or CATCH_BEND
        return (st["line"] + (st["end"] - st["line"]) * (2 * cx + 1) / 4,
                clamp_bpm(st["base"] * 2 ** (plan["push"] * (2 * cy + 1) / 4)))

    def handles(self):
        """The picked part's / catch-up's bend handle: (("part", i) / ("catch", j), (beat, BPM)), or None."""
        pick = self.picked()
        if pick and pick[0] in ("part", "catch"):
            at = (self.handle_pos if pick[0] == "part" else self.catch_handle_pos)(pick[1])
            if at is not None:
                return pick, at
        return None

    def along_bpm(self, p, q, f):
        """The BPM f of the way from p's to q's (on its part's BPM change: linear / doubling)."""
        if p[4] == "exp":
            return 2 ** (math.log2(p[1]) + (math.log2(q[1]) - math.log2(p[1])) * f)
        return p[1] + (q[1] - p[1]) * f

    def handle_at(self, x, y):
        """("part", i) / ("catch", j) when the mouse is on the picked one's bend handle."""
        got = self.handles()
        if got is None:
            return None
        hx, hy = self.app.roll.t2x(got[1][0]), float(self.b2y(got[1][1]))
        return got[0] if math.hypot(hx - x, hy - y) <= 7 * self.scale else None

    def bend_to(self, d, e):
        """The handle dragged to the mouse: the bend, kept between its two ends (never past A or B). A catch-up's
        BPM read with its push at the press (its BPM changes with the bend)."""
        kind, i = d["handle"]
        if kind == "catch":
            st = d["st"]
            fx = (self.app.roll.x2t(e.x) - st["line"]) / max(1e-12, st["end"] - st["line"])
            fy = math.log2(max(MIN_BPM, self.y2b(e.y)) / st["base"]) / d["push"] if d["push"] else 0.5
            catch_set(self.app.tempo["catch"][i], 5, [min(1.0, max(0.0, 2 * fx - 0.5)),
                                                      min(1.0, max(0.0, 2 * fy - 0.5))])
            return
        pts = self.app.tempo["points"]
        p, q = pts[i], pts[i + 1]
        fx = (self.app.roll.x2t(e.x) - p[0]) / max(1e-12, q[0] - p[0])
        v = self.y2b(e.y)
        if p[4] == "exp":
            fy = (math.log2(v) - math.log2(p[1])) / (math.log2(q[1]) - math.log2(p[1]))
        else:
            fy = (v - p[1]) / (q[1] - p[1])
        if p[2] == "s":
            fx, fy = fx * 2, fy * 2
        p[6:] = [[min(1.0, max(0.0, 2 * fx - 0.5)), min(1.0, max(0.0, 2 * fy - 0.5))]]

    def toggle_s(self, i):
        """Right-click on the handle: the part an S-curve (the same bend, mirrored on its second half) / back to a
        line (one undo step)."""
        p = self.app.tempo["points"][i]
        self.app.push_undo(name=tr("tempo.step_s"))
        p[2] = "line" if p[2] == "s" else "s"
        self.app.tempo_changed()

    def snap_beat(self, b, e):
        """On the grid (Shift: free): the Select box's move, the catch-up being added, a click's play line."""
        if e.state & SHIFT:
            return max(0.0, b)
        sb = self.app.snap_beats()
        return max(0.0, round(b / sb) * sb) if sb else max(0.0, b)

    def tick_beat(self, b, skip=()):
        """b on a whole tick: where drawing puts things (user 2026-10-22: no grid, like many MIDI programs); within
        6 px of a tempo point (not one in skip): its exact beat, so a jump's second point can be put there."""
        ppq, roll = max(1, self.app.ppq), self.app.roll
        if roll.sx:
            r = 6 * self.scale / roll.sx
            near = [p[0] for p in self.app.tempo["points"] if abs(p[0] - b) <= r and not any(p is s for s in skip)]
            if near:
                return min(near, key=lambda pb: abs(pb - b))
        return max(0.0, round(b * ppq) / ppq)

    def move_beat(self, b, e, skip=(), beats=None):
        """A point / line / catch-up edge moved afterwards: onto the nearest note start or end within 10 px (user
        2026-10-22: tempo changes belong there) or tempo point (not one in skip, the ones moving; beats: these
        beats instead), else a whole tick; Shift = ticks only."""
        roll, ppq = self.app.roll, max(1, self.app.ppq)
        if e.state & SHIFT or roll.sx is None:
            return max(0.0, round(b * ppq) / ppq)
        r = 10 * self.scale / roll.sx
        lo, hi = (b - r) * ppq, (b + r) * ppq
        first, end = roll.visible_range(lo, hi)
        rows = roll.sorted_notes()[first:end]
        rows = rows[rows[:, 5] < len(self.app.shapes)]  # (the track list's faint tracks: only shown)
        near = np.concatenate([rows[:, 0], rows[:, 1]]).astype(float)
        near = near[(near >= lo) & (near <= hi)] / ppq
        if beats is None:
            beats = [p[0] for p in self.app.tempo["points"] if not any(p is s for s in skip)]
        near = np.concatenate([near, np.array([pb for pb in beats if abs(pb - b) <= r], float)])
        if not len(near):
            return max(0.0, round(b * ppq) / ppq)
        return float(near[np.abs(near - b).argmin()])

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
        if time.perf_counter() - self.menu_shut < MENU_CLICK_S:  # (the click that closed the right-click menu)
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
        k = self.live_end_at(e.x, e.y)
        if k is not None:  # the live line's end square: moves that end
            self.drag = {"kind": "live_end", "end": k}
            return
        if self.handle_at(e.x, e.y) is None:  # (its bend handle keeps it live; anything else: done with it)
            self.confirm()
        if tool == "select" and e.state & CTRL and self.in_band(e.x):
            self.start_move(e, None, pushed=False)  # (Ctrl+drag inside the Select box: copies, like Domino)
            self.drag["copy"] = True
            return
        i, j = self.point_at(e.x, e.y), self.catch_at(e.x)
        item = tempo["points"][i] if i is not None else tempo["catch"][j] if j is not None else None
        n = self.handle_at(e.x, e.y) if item is None else None
        if n is not None:  # the picked part's / catch-up's bend handle
            self.drag = {"kind": "bend", "handle": n, "x": e.x, "y": e.y, "pushed": False}
            if n[0] == "catch":
                self.drag["st"], plan = plans(tempo, self.base(), self.app.ppq)[n[1]]
                self.drag["push"] = plan["push"]
            return
        if item is not None:
            if tool == "select" and e.state & CTRL:
                self.bands = []
                if self.is_sel(item):  # (Ctrl+click on a picked one: unpicked)
                    return self.set_sel([s for s in self.sel if s is not item])
                self.set_sel(self.sel + [item])
            elif not (tool == "select" and self.is_sel(item)):
                self.bands = []
                self.set_sel([item])
            return self.start_move(e, item, pushed=False)
        for kind, j in (("start", self.start_at(e.x)), ("end", self.end_at(e.x))):
            if j is not None:  # the yellow area's left edge / the teal area's right edge
                self.bands = []
                self.set_sel([tempo["catch"][j]])
                self.drag = {"kind": kind, "item": tempo["catch"][j], "x": e.x, "pushed": False}
                return
        b, v = self.tick_beat(roll.x2t(e.x)), self.snap_bpm(self.y2b(e.y), e)
        if tool == "select":
            beat = roll.x2t(e.x)
            if self.in_band(e.x) and not e.state & CTRL:
                return self.start_move(e, None, pushed=False)  # (inside a box: all they picked, in time)
            ctrl = bool(e.state & CTRL)  # (Ctrl: another box, added to the ones kept, user 2026-10-22)
            self.part, more, self.bands = None, self.bands if ctrl else [], []
            self.drag = {"kind": "box", "x": e.x, "x1": e.x, "b0": beat, "shift0": bool(e.state & SHIFT),
                         "span": None, "before": list(self.sel), "ctrl": ctrl, "more": more}
            self.request_redraw()
            return
        self.bands = []  # (Line / Formula: a click = a point, a drag = a part; let go: draw_part)
        self.drag = {"kind": "draw", "b0": b, "v0": v, "b1": b, "v1": v, "x": e.x, "y": e.y}
        self.request_redraw()

    def start_move(self, e, item, pushed):
        """Hold the picked items, item the one under the mouse (None: the Select box grabbed; pushed: the step is
        made already: a new point). Time and BPM, Select too (user 2026-10-22); the Select box goes along and
        first one's left side snaps, else the grabbed one does."""
        band = list(self.bands) or None
        pts = self.app.tempo["points"]
        pid = ids(pts)
        held = [s for s in self.sel if id(s) in pid]
        held_beats = {s[0] for s in held}
        self.drag = {"kind": "move", "grab": item, "x": e.x, "y": e.y, "pushed": pushed,
                     "orig": [(s, s[0], s[1] if id(s) in pid else None) for s in self.sel],
                     # (the other point of a jump goes along in time, keeping its BPM: user 2026-10-22)
                     "mates": [(p, p[0]) for p in pts if not self.is_sel(p) and p[0] in held_beats],
                     # (hand-set catch-up edges go along when many move; one line dragged alone leaves them)
                     "starts": [(s, k, s[k]) for s in self.sel if id(s) not in pid and (band or len(self.sel) > 1)
                                for k in (3, 4) if catch_get(s, k) is not None],
                     "press": self.app.roll.x2t(e.x), "band": band, "ref": item[0] if item is not None else self.bands_reach()[0],
                     "time_only": self.box.tool.get() == "select",
                     "v0": item[1] if item is not None and self.is_point(item) else None, "db": 0.0}

    def copy_drag(self, d):
        """A Ctrl+drag's first move: copies of what the Select box holds are what moves (the originals stay)."""
        self.app.push_undo(name=tr("tempo.step_copy_drag"))
        d["pushed"] = True
        # (let go: laid in from this like a paste, hunt 2026-10-22; the copies below only show it while dragged)
        d["clip"] = self.clip_of(spans=self.bands) if self.bands else None
        tempo, pairs = self.app.tempo, []
        for s, b, v in d["orig"]:
            c = copy.deepcopy(s)
            tempo["points" if v is not None else "catch"].append(c)
            pairs.append((s, c))
        swap = {id(s): c for s, c in pairs}
        d["orig"] = [(swap[id(s)], b, v) for s, b, v in d["orig"]]
        d["starts"] = [(swap[id(s)], k, a) for s, k, a in d["starts"]]
        d["copies"] = [c for _, c in pairs]
        self.sel = list(d["copies"])
        for key in ("points", "catch"):
            tempo[key].sort(key=lambda it: it[0])

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
            if d["span"] and roll.box_kept:  # (one place's boxes at a time: the piano roll's / velocity pane's
                roll.box_kept = None         # go, user 2026-10-22)
                roll.request_redraw()
            inside = self.in_span(d["span"]) if d["span"] else []
            self.sel = d["before"] * d["ctrl"] + [s for s in inside if not (d["ctrl"] and
                                                                            any(s is o for o in d["before"]))]
            self.box.sync()
            return self.request_redraw()
        if d["kind"] == "live_end":
            if self.live_line() is not None:
                self.live_end_to(d["end"], e)
            return self.show_position(e)
        if d["kind"] == "draw":
            d["b1"], d["v1"] = self.tick_beat(roll.x2t(e.x)), self.snap_bpm(self.y2b(e.y), e)
            d["moved"] = d.get("moved") or abs(e.x - d["x"]) >= 3 or abs(e.y - d["y"]) >= 3  # (less: a click)
            self.show_position(e)
            return self.request_redraw()
        if d["kind"] == "catch1":
            d["x1"] = e.x
            d["span"] = self.box_span(d, e)
            return self.request_redraw()
        if d["kind"] == "bend":
            if not d["pushed"]:
                if abs(e.x - d["x"]) < 3 and abs(e.y - d["y"]) < 3:
                    return
                self.app.push_undo(name=tr("tempo.step_bend" if d["handle"][0] == "part" else "tempo.step_catch_bend"))
                d["pushed"] = True
            live = self.live_line()
            self.bend_to(d, e)
            kind, i = d["handle"]
            if live is not None and kind == "part" and self.app.tempo["points"][i] is live["first"]:
                self.live_seen()  # (the live line's own bend: it stays live)
            self.app.tempo_changed()
            return self.show_position(e)
        if d["kind"] in ("start", "end"):  # a catch-up's yellow left edge / teal right edge
            if not d["pushed"]:
                if abs(e.x - d["x"]) < 3:
                    return
                self.app.push_undo(name=tr("tempo.step_catch_" + d["kind"]))
                d["pushed"] = True
            c, tick = d["item"], 1.0 / max(1, self.app.ppq)
            b = self.move_beat(roll.x2t(e.x), e)
            catch_set(c, 3, min(b, c[0] - tick)) if d["kind"] == "start" else catch_set(c, 4, max(b, c[0] + tick))
            self.app.tempo_changed()
            return self.show_position(e)
        if not d["pushed"] and d.get("copy"):
            if abs(e.x - d["x"]) < 3:
                return
            self.copy_drag(d)
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
        # (a grabbed point / line goes to the mouse itself, user 2026-10-22: with Shift pressed later it sat off
        # the pencil by where the snapped press was; the box keeps its distance)
        at = roll.x2t(e.x) if d["grab"] is not None else d["ref"] + roll.x2t(e.x) - d["press"]
        held = [s for s, _, _ in d["orig"]] + [s for s, _ in d.get("mates", ())]
        db = (self.move_beat(at, e, held) if d["grab"] is not None else self.snap_beat(at, e)) - d["ref"]
        db = max([db] + [-b for _, b, _ in d["orig"]] + [-lo for lo, _ in d["band"] or ()])  # (not before 0)
        # (BPM too, Select as well, user 2026-10-22: was time only; the box: by the BPM moved under the mouse)
        if d["v0"] is not None:
            dv = self.snap_bpm(self.y2b(e.y), e) - d["v0"]
        else:
            dv = self.snap_bpm(self.y2b(e.y), e) - self.snap_bpm(self.y2b(d["y"]), e)
        vs = [v for _, _, v in d["orig"] if v is not None]
        if vs and dv:  # (stops at the slowest / fastest a file holds: the gaps between them kept)
            dv = min(max(dv, MIN_BPM - min(vs)), MAX_BPM - max(vs))

        def place(shift):
            for s, b, v in d["orig"]:
                s[0] = max(b + shift, 0.0 if v is not None else one)
                if v is not None:
                    s[1] = clamp_bpm(v + dv) if dv else v
            for s, b in d.get("mates", ()):
                s[0] = max(b + shift, 0.0)
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

        moved = {id(s) for s, _, _ in d["orig"]} | {id(s) for s, _ in d.get("mates", ())}

        def clash():
            """Two on one beat where something moved: the time stays where it last worked. Points: a jump's two
            may share exactly one beat, never three; one moved onto another (from the left: it arrives there, the
            list's order the sort keeps) lands exactly on its beat. Points not moved are never touched (hunt
            2026-10-22: two a hair apart got joined, three stopped every drag)."""
            for key in ("points", "catch"):
                its = tempo[key]
                k = 0
                while k < len(its):  # (each run of items within half a tick of the one before)
                    m = k
                    while m + 1 < len(its) and its[m + 1][0] - its[m][0] < one / 2:
                        m += 1
                    run = its[k:m + 1]
                    k = m + 1
                    if len(run) < 2 or not any(id(it) in moved for it in run):
                        continue
                    if key == "catch" or len(run) > 2:
                        return True
                    a, b = run
                    if id(b) in moved and id(a) not in moved:
                        b[0] = a[0]
                    elif id(a) in moved and id(b) not in moved:
                        a[0] = b[0]
            return False

        place(db)
        if d.get("copies") is not None:  # (copies replace what they land on when let go: never stopped, hunt)
            d["db"] = db
        elif clash():
            place(d["db"])
            clash()  # (the old spot's own exact beat again)
        else:
            d["db"] = db
        if d["band"]:
            self.bands = [(lo + d["db"], hi + d["db"]) for lo, hi in d["band"]]

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

    def in_spans(self, spans):
        """The points and catch-up lines in any of spans, each once, in the lists' order."""
        tempo = self.app.tempo
        return [it for it in tempo["points"] + tempo["catch"]
                if any(lo - 1e-9 <= it[0] <= hi + 1e-9 for lo, hi in spans)]

    def bands_reach(self):
        """(first, last beat) the kept Select boxes cover together."""
        return min(lo for lo, _ in self.bands), max(hi for _, hi in self.bands)

    def on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        live = self.live_line()
        if drop_loose(self.app.tempo["points"]):  # (a jump's first point the drag left with nothing gliding in)
            self.app.tempo_changed()
        if live is not None and d["kind"] in ("live_end", "bend"):
            self.live_seen()
        if d["kind"] == "box":
            if d["span"] is None:  # a click: the part there (or nothing), the play line there (user 2026-10-21)
                part = self.part_at(self.app.roll.x2t(e.x))
                pts = self.app.tempo["points"]
                self.bands = d["more"]  # (a Ctrl+click changes nothing)
                self.set_sel(d["before"] if d["ctrl"] else [], None if part is None else pts[part])
                if not d["ctrl"]:
                    self.move_line(e)
            else:
                self.bands = d["more"] + [d["span"]]  # (the box stays, like the piano roll's)
                self.request_redraw()
        elif d["kind"] == "draw":
            self.draw_part(d)
        elif d["kind"] == "catch1":
            if d["span"] is not None:  # (a click does nothing: it waits for a drag)
                self.placing = {"step": 2, "a": d["span"][0], "b": d["span"][1], "hover": None}
                self.box.hint.config(text=tr("tempo.hint_catch2"))
            self.request_redraw()
        elif d["kind"] in ("start", "end", "bend"):
            if d["pushed"]:
                self.app.settle_history()
        elif d["kind"] == "move":
            if d.get("copies") is not None:  # (Ctrl+drag let go: the copies replace what was where they landed,
                # as a paste does; not moved = no copies)
                tempo, cop = self.app.tempo, ids(d["copies"])
                dv = next((s[1] - v for s, _, v in d["orig"] if v is not None), 0.0)
                moved = bool(self.bands) and (abs(d["db"]) > 1e-12 or abs(dv) > 1e-12)
                for key in ("points", "catch"):  # (the copies only showed it: laid in like a paste, hunt 2026-10-22)
                    tempo[key][:] = [it for it in tempo[key] if id(it) not in cop]
                clip = d.get("clip")
                if moved and clip:
                    clip = dict(clip, points=[[p[0], clamp_bpm(p[1] + dv)] + p[2:] for p in clip["points"]])
                    self.put_copies(clip, joined(d["band"])[0][0] + d["db"], None, push=False)
                else:
                    self.bands = list(d["band"] or [])
                    self.sel = self.in_spans(self.bands)
                    self.app.tempo_changed()
            if d["pushed"]:
                self.app.settle_history()
            elif d["time_only"] and not e.state & CTRL:  # (a Select click: the play line there too)
                if d["grab"] is not None:  # (a click on one of many: it)
                    self.bands = []
                    self.set_sel([d["grab"]])
                self.move_line(e)

    def move_line(self, e):
        """A Select click: the play line to the click (snapped like the piano roll's), playing on from there."""
        app = self.app
        playing = app.player.running
        app.stop_play()
        app.set_playhead(self.snap_beat(app.roll.x2t(e.x), e))
        if playing:
            app.start_play(keep=True)

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
        n = self.handle_at(e.x, e.y) if i is None else None
        if n is not None:  # (the bend handle: straight / the catch-up's own curve again)
            self.drag = None
            if n[0] == "catch":
                c = self.app.tempo["catch"][n[1]]
                if catch_get(c, 5) is not None:
                    self.app.push_undo(name=tr("tempo.step_catch_bend"))
                    catch_set(c, 5, None)
                    self.app.tempo_changed()
                return
            p = self.app.tempo["points"][n[1]]
            if bend_of(p) != BEND:
                self.app.push_undo(name=tr("tempo.step_bend"))
                p[6:] = [list(BEND)]
                self.app.tempo_changed()
            return
        if i is None and self.box.tool.get() == "select" and getattr(self.app, "tempo_clip", None) \
                and self.catch_at(e.x) is None:  # (Select double-click on empty space: the copy there, like the
            self.drag = None                     # piano roll's; snapped like it, Shift free; user 2026-10-22)
            self.app.key_pane = self
            return self.paste(self.snap_beat(roll.x2t(e.x), e))
        if i is None:
            return self.on_press(e)
        self.drag = None
        self.open_typing(i)

    def glide_end(self, b):
        """The lone point on beat b where a curved part arrives (a second point may go on its beat: a jump), or
        None."""
        pts = self.app.tempo["points"]
        on = [i for i, p in enumerate(pts) if abs(p[0] - b) < 1e-9]
        if len(on) != 1 or on[0] == 0:
            return None
        p, q = pts[on[0] - 1], pts[on[0]]
        return q if p[2] != "hold" and p[1] != q[1] else None

    def add_point(self, b, v):
        """A new point (picked); its part keeps the curve of the part it was put in. On the beat of a point where a
        curve arrives: a second point there, the tempo from there on (user 2026-10-22: a jump right where a glide
        ends). None when one is there."""
        pts = self.app.tempo["points"]
        end = self.glide_end(b)
        if end is not None and abs(end[1] - clamp_bpm(v)) < 1e-9:
            return None
        if end is None and any(abs(p[0] - b) < 1e-9 for p in pts):
            return None
        self.app.push_undo(name=tr("tempo.step_add"))
        at = sum(1 for p in pts if p[0] < b + (1e-9 if end is not None else 0))
        item = [float(end[0] if end is not None else b), clamp_bpm(v),
                *part_kind(pts[at - 1] if at > 0 else None, self.box.draw_scale)]
        pts.insert(at, item)
        self.sel, self.part = [item], None
        self.app.tempo_changed()
        return item

    def draw_part(self, d):
        """Linear / Curve / Formula let go: a part from the press to here (the points between it replaced)."""
        b0, v0, b1, v1 = d["b0"], d["v0"], d["b1"], d["v1"]
        if b1 < b0:
            b0, v0, b1, v1 = b1, v1, b0, v0
        if not d.get("moved") or b1 - b0 < 0.5 / max(1, self.app.ppq):  # (a click: a point, user 2026-10-22)
            b0, v0 = d["b0"], d["v0"]
            self.add_point(b0, v0)
            return
        pts = self.app.tempo["points"]
        self.app.push_undo(name=tr("tempo.step_draw"))
        base = copy.deepcopy(pts)
        kind = DRAWS[self.box.tool.get()]
        first = self.lay_part(b0, v0, b1, v1, kind, self.box.draw_formula if kind == "formula" else "")
        # LIVE until Enter / Esc / a click elsewhere (user 2026-10-22, like the velocity pane): its end squares
        # move it, laid again over the points as they were before it (shrunk: the covered ones come back)
        self.live = {"base": base, "ends": [[b0, v0], [b1, v1]], "first": first}
        self.live_seen()

    def lay_part(self, b0, v0, b1, v1, kind, text, like=None):
        """A part from (b0, v0) to (b1, v1) over the points there (they're replaced), picked; like: a first point
        whose curve / formula / scale / keep / bend it takes. Returns its first point."""
        if b1 < b0:
            b0, v0, b1, v1 = b1, v1, b0, v0
        pts = self.app.tempo["points"]
        before = [p for p in pts if p[0] <= b1 + 1e-9]
        # (the part after keeps its curve)
        after_kind = part_kind(before[-1] if before else None, self.box.draw_scale)
        held = [p for p in pts if p[0] <= b0 + 1e-9]
        first = [float(b0), clamp_bpm(v0), kind, text, self.box.draw_scale, bool(held) and keeps(held[-1]),
                 list(BEND)]
        if like is not None:
            first[2:] = copy.deepcopy(like[2:])
        last = [float(b1), clamp_bpm(v1), *after_kind]
        # (user 2026-10-22: drawn from where a glide arrives = a jump there, that point kept; drawn up to a jump =
        # it arrives there, the tempo after the jump kept)
        end = self.glide_end(b0)
        at_b0 = [p for p in pts if abs(p[0] - b0) < 1e-9]
        if len(at_b0) == 2:  # (drawn from a jump: its arrival stays, the line replaces its second point; hunt)
            end = at_b0[0]
        arrive = [end] if end is not None and abs(end[1] - first[1]) > 1e-9 else []
        if arrive:
            first[0] = end[0]
        at_b1 = [p for p in pts if abs(p[0] - b1) < 1e-9]
        leave = at_b1[1:] if len(at_b1) == 2 else []
        if leave:
            last[0] = leave[0][0]
        pts[:] = ([p for p in pts if p[0] < b0 - 1e-9] + arrive + [first, last] + leave +
                  [p for p in pts if p[0] > b1 + 1e-9])
        self.sel, self.part = [], first
        self.app.tempo_changed()
        return first

    # ------------------------------------------------------------ the line just drawn, live (user 2026-10-22)

    def live_line(self):
        """The line just drawn while it can still change (nothing else changed the tempo since), else None."""
        lv = self.live
        if lv is not None and json.dumps(self.app.tempo) != lv["seen"]:
            self.live = lv = None
        return lv

    def live_seen(self):
        """The live line's own change done: the tempo as it is now is still its."""
        if self.live is not None:
            self.live["seen"] = json.dumps(self.app.tempo)

    def live_end_at(self, x, y):
        """0 / 1 when the mouse is on the live line's start / end square, else None."""
        lv = self.live_line()
        if lv is None:
            return None
        roll, r = self.app.roll, 7 * self.scale
        for k, (b, v) in enumerate(lv["ends"]):
            if abs(roll.t2x(b) - x) <= r and abs(float(self.b2y(v)) - y) <= r:
                return k
        return None

    def live_end_to(self, k, e):
        """The live line's end k dragged to the mouse: onto a note's start / end or a tempo point near it (Shift:
        whole ticks), BPM snapped; laid again over the points as they were before it, in the same undo step."""
        lv = self.live
        b = self.move_beat(self.app.roll.x2t(e.x), e, beats=[p[0] for p in lv["base"]])
        ends = [list(p) for p in lv["ends"]]
        ends[k] = [b, self.snap_bpm(self.y2b(e.y), e)]
        if abs(ends[0][0] - ends[1][0]) < 0.5 / max(1, self.app.ppq):
            return
        lv["ends"] = ends
        old = lv["first"]
        self.app.tempo["points"][:] = copy.deepcopy(lv["base"])
        (b0, v0), (b1, v1) = ends
        lv["first"] = self.lay_part(b0, v0, b1, v1, old[2], old[3], like=old)
        self.live_seen()

    def dense_key(self):
        app = self.app
        return json.dumps(app.tempo), self.base(), app.ppq, app.beats, id(app.rendered)

    def dense_now(self):
        """Beats where notes in a Keep timing part are too close for its tempo (tempo_file.dense_spots), as last
        worked out; out of date = worked out again a moment after the changes stop (a big song takes a while)."""
        if self._dense[0] != self.dense_key() and self._dense_job is None:
            self._dense_job = self.after(DENSE_WAIT_MS, self.work_dense)
        return self._dense[1]

    def dense_at(self, x):
        """True when x is on (within 3 px of) a "notes too close" line."""
        dense, roll, r = self._dense[1], self.app.roll, 3 * self.scale
        if not dense or roll.sx is None:
            return False
        i = bisect.bisect_left(dense, roll.x2t(x - r))
        return i < len(dense) and roll.t2x(dense[i]) <= x + r

    def work_dense(self):
        self._dense_job = None
        if self.drag or getattr(self.app, "notes_late", False):  # (still being dragged: later)
            self._dense_job = self.after(DENSE_WAIT_MS, self.work_dense)
            return
        key, app = self.dense_key(), self.app
        if key == self._dense[0] or not self.winfo_ismapped():  # (hidden: its next drawing asks again)
            return
        notes = app.rendered
        # (notes only: CC messages too close give way in the export, never keep the tempo faster)
        ticks = np.concatenate([notes[:, 0], notes[:, 1]]) if len(notes) else np.zeros(0, np.int64)
        try:
            spots = sorted(dense_spots(app.tempo, self.base(), app.ppq, app.beats, ticks))
        except MemoryError:
            spots = []
        self._dense = (key, spots)
        self.request_redraw()

    def confirm(self):
        """Done with the live line (Enter, Esc, a click elsewhere): its squares go. True if there was one."""
        if self.live_line() is None:
            self.live = None
            return False
        self.live = None
        self.request_redraw()
        return True

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
        n = self.handle_at(e.x, e.y)
        if n is not None and n[0] == "part":  # (the bend handle: an S-curve / a line again, user)
            return self.toggle_s(n[1])
        tempo = self.app.tempo
        i, j = self.point_at(e.x, e.y), self.catch_at(e.x)
        item = tempo["points"][i] if i is not None else tempo["catch"][j] if j is not None else None
        on_edge = item is None and (self.start_at(e.x) is not None or self.end_at(e.x) is not None)
        on_edge = on_edge or item is None and self.sig_mark_at(e.x, e.y) is not None  # (a signature's mark)
        if item is None and not on_edge and not self.in_band(e.x):  # (empty space: the menu waits to see if it's
            self.drop_menu_wait()                                    # a double right-click, user)
            self.menu_wait = self.after(DOUBLE_MS, lambda: self.right_menu(e, None))
            return
        self.right_menu(e, item)

    def in_band(self, x):
        beat = self.app.roll.x2t(x)
        return any(lo <= beat <= hi for lo, hi in self.bands)

    def drop_menu_wait(self):
        if self.menu_wait:
            self.after_cancel(self.menu_wait)
            self.menu_wait = None

    def on_double_right(self, e):
        """Double right-click: Select <-> the pencil used last (user, like the piano roll); not inside the Select
        box, nor while the left button is held."""
        if self.drag or e.state & 0x100:
            return
        if self.placing or self.in_band(e.x) or e.x < self.app.roll.kb_w:
            return self.on_right(e)
        self.drop_menu_wait()
        self.box.toggle_select()
        self.on_motion(e)

    def right_menu(self, e, item):
        """The right-click menu: Add catch-up BPM here, an edge back to automatic, Delete for item."""
        self.menu_wait = None
        tempo = self.app.tempo
        menu = tk.Menu(self, tearoff=0)
        on_mark = item is None and self.sig_menu_items(menu, e)  # (time / key signatures: Add here, or the mark's)
        if not on_mark:
            menu.add_separator()
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
            # (Windows: the menu waits here until it closes; the click outside that closed it then comes to the
            # pane too: it only closes the menu, adds no point, user 2026-10-23)
            self.menu_shut = time.perf_counter()

    def auto_edge(self, c, k):
        """A catch-up's yellow start (k = 3) / teal end (k = 4) back to where Spiderweb puts it (one undo step)."""
        if catch_get(c, k) is not None:
            self.app.push_undo(name=tr("tempo.step_catch_start" if k == 3 else "tempo.step_catch_end"))
            catch_set(c, k, None)
            self.app.tempo_changed()

    def delete_items(self, gone, name=None):
        """These points / catch-up lines go (one undo step, name: its name instead of Delete's)."""
        tempo = self.app.tempo
        alive = ids(tempo["points"] + tempo["catch"])
        gone = [g for g in gone if id(g) in alive]
        if not gone:
            return
        if name is None and len(gone) > 1:
            name = tr("tempo.step_delete_many")
        elif name is None:
            name = tr("tempo.step_delete" if self.is_point(gone[0]) else "tempo.step_catch_delete")
        self.app.push_undo(name=name)
        out = ids(gone)
        for key in ("points", "catch"):
            tempo[key][:] = [it for it in tempo[key] if id(it) not in out]
        self.sel = [s for s in self.sel if id(s) not in out]
        self.app.tempo_changed()

    def delete_picked(self):
        """The Delete key while this pane was clicked last: the picked points / catch-up lines go, and the Select
        box with them (user 2026-10-22: it stayed, empty; Ctrl+Z brings both back). Always takes the key (user's
        pick in the hunt: never the piano roll's shapes, which may be off screen); nothing while the mouse holds
        something here."""
        self.picked()
        if self.drag:
            return True
        tempo = self.app.tempo
        if self.sel and len(self.sel) == len(tempo["points"]) + len(tempo["catch"]):  # (all, e.g. Ctrl+A: nothing
            self.delete_items(list(self.sel), tr("tempo.step_delete_many"))           # left round them to keep)
        elif self.bands and self.sel:  # (the boxes' stretch emptied, the tempo round it as it was: like a cut)
            self.empty_spans(self.bands, tr("tempo.step_delete_many"))
        elif self.sel:
            self.delete_items(list(self.sel))
        if self.bands:
            self.bands = []
            self.request_redraw()
        return True

    # ------------------------------------------------------------ copy / paste (user 2026-10-21, like Domino)

    # HOW THE EDGES WORK (user 2026-10-22, after a hunt: pastes bent / flattened / changed the tempo round them):
    # a copy holds EXACTLY what its boxes show: the tempo line cut at each box edge (tempo.split_at), at the
    # start the tempo leaving it, at the end the tempo arriving. A paste / cut / Delete with a box changes ONLY
    # the stretch inside: the target is cut at the edges too, the point arriving at the start and the one the
    # part after the end leaves from stay; a pasted part leaves from / arrives at them (a jump when they differ).
    # Touching / overlapping boxes count as one.

    def clip_of(self, spans=None, items=None):
        """What Ctrl+C keeps: {"length", "points", "catch", "spans"}, beats from the copy's start. spans: boxes
        (the line cut at their edges, every catch-up inside); items: picked points / catch-up lines (their own
        first .. last beat). None when nothing's there."""
        tempo, base = self.app.tempo, self.base()
        if spans:
            spans = joined(spans)
            src = copy.deepcopy(tempo["points"])
            if src:
                for lo, hi in spans:
                    split_at(src, lo, base)
                    split_at(src, hi, base)
            pts = []
            for lo, hi in spans:
                at = [p for p in src if lo - 1e-9 <= p[0] <= hi + 1e-9]
                first = [p for p in at if abs(p[0] - lo) < 1e-9]
                last = [p for p in at if abs(p[0] - hi) < 1e-9] if hi - lo > 1e-9 else []
                pts += first[-1:] + [p for p in at if lo + 1e-9 < p[0] < hi - 1e-9] + last[:1]
            cs = self.in_spans(spans)
        else:
            alive = ids(items)
            pts = [copy.deepcopy(p) for p in tempo["points"] if id(p) in alive]
            cs = [c for c in tempo["catch"] if id(c) in alive]
            beats = [it[0] for it in pts + cs]
            if not beats:
                return None
            lo, hi = min(beats), max(beats)
            if hi - lo > 1e-9:  # (picked edge pairs: only the tempo leaving the start / arriving at the end)
                at_lo = [p for p in pts if abs(p[0] - lo) < 1e-9]
                at_hi = [p for p in pts if abs(p[0] - hi) < 1e-9]
                gone = ids(at_lo[:-1] + at_hi[1:])
                pts = [p for p in pts if id(p) not in gone]
            spans = [(lo, hi)]
        pid = ids(tempo["points"])
        cs = [c for c in cs if id(c) not in pid]
        if not pts and not cs:
            return None
        lo = spans[0][0]
        clip_c = []
        for c in cs:
            item = [c[0] - lo] + list(c[1:3])
            for k in (3, 4):
                catch_set(item, k, None if catch_get(c, k) is None else catch_get(c, k) - lo)
            catch_set(item, 5, catch_get(c, 5))
            clip_c.append(item)
        return {"length": spans[-1][1] - lo, "points": [[p[0] - lo] + copy.deepcopy(p[1:]) for p in pts],
                "catch": clip_c, "spans": [(a - lo, b - lo) for a, b in spans]}

    def copy(self):
        """Ctrl+C after clicking the pane: the Select boxes (or what's picked, or the part picked: its two
        points), each kept as its distance from the copy's start, so a paste keeps the gaps."""
        pts, pick = self.app.tempo["points"], self.picked()
        if self.bands:
            clip = self.clip_of(spans=self.bands)
        elif self.sel:
            clip = self.clip_of(items=self.sel)
        elif pick and pick[0] == "part" and pick[1] + 1 < len(pts):  # (a part picked: from its start to its end)
            clip = self.clip_of(spans=[(pts[pick[1]][0], pts[pick[1] + 1][0])])
        else:
            clip = None
        if clip is None:
            self.app.status.config(text=tr("tempo.copy_nothing"))
            return False
        self.app.tempo_clip = clip
        n = len(clip["points"]) + len(clip["catch"])
        self.app.status.config(text=trn("tempo.copied", n, n=n))
        return True

    def cut(self):
        """Ctrl+X: a copy, then the boxes' stretch emptied (or the picked ones deleted), one undo step."""
        if not self.copy():
            return
        if self.bands:
            self.empty_spans(self.bands, tr("tempo.step_cut"))
        else:
            self.delete_items(list(self.sel), tr("tempo.step_cut"))

    def empty_spans(self, spans, name):
        """Everything inside the boxes goes (one undo step); the tempo round them stays as it was: the line is cut
        at the edges, the point arriving at each start holds its BPM to the end, where the part after leaves
        from its own point (Ctrl+X / Delete with a box; cut + paste back = the same tempo)."""
        tempo, base = self.app.tempo, self.base()
        spans = [(lo, hi) for lo, hi in joined(spans) if hi - lo > 1e-9]
        if not spans:
            return
        self.app.push_undo(name=name)
        tempo["catch"][:] = [c for c in tempo["catch"] if not any(lo - 1e-9 <= c[0] <= hi + 1e-9 for lo, hi in spans)]
        pts = tempo["points"]
        if pts:
            self.lay_points(pts, [], spans, base)
            for lo, _ in spans:  # (the emptied stretch holds the BPM arriving at its start)
                at = [p for p in pts if abs(p[0] - lo) < 1e-9]
                if len(at) == 1:
                    at[0][2:4] = ["hold", ""]
        alive = ids(pts + tempo["catch"])
        self.sel = [s for s in self.sel if id(s) in alive]
        self.app.tempo_changed()

    def paste(self, at=None):
        """Ctrl+V after clicking the pane: the copied tempo at the play line on the grid (like the piano roll's
        paste; at: a Select double-click's beat), gaps kept; what was in its boxes' stretch is replaced (one undo
        step). The pasted ones are picked, in Select boxes."""
        clip = getattr(self.app, "tempo_clip", None)
        if not clip:
            self.app.status.config(text=tr("tempo.paste_nothing"))
            return
        if at is None:
            at, sb = self.app.playhead, self.app.snap_beats()
            if sb:
                at = round(at / sb) * sb
        self.put_copies(clip, self.tick_beat(at, skip=self.app.tempo["points"]), tr("tempo.step_paste"))

    def put_copies(self, clip, at, name, push=True, stay=False):
        """clip's items placed from beat at, replacing what's in its boxes there (push: its own undo step). A clip
        of catch-up lines only leaves the points alone. stay: its first point always stays (lay_points)."""
        tempo = self.app.tempo
        spans = joined([(at + a, at + b) for a, b in clip.get("spans") or [(0.0, clip["length"])]])
        one = 1.0 / max(1, self.app.ppq)
        if push:
            self.app.push_undo(name=name)
        new_p = [[max(0.0, p[0] + at)] + copy.deepcopy(p[1:]) for p in clip["points"]]
        new_c = []
        for c in clip["catch"]:
            item = [max(one, c[0] + at)] + list(c[1:3])
            for k in (3, 4):
                v = catch_get(c, k)
                catch_set(item, k, None if v is None else max(0.0, v + at))
            catch_set(item, 5, catch_get(c, 5))
            new_c.append(item)
        kept = [it for it in tempo["catch"] if not any(lo - 1e-9 <= it[0] <= hi + 1e-9 for lo, hi in spans)]
        tempo["catch"][:] = sorted(kept + new_c, key=lambda it: it[0])
        if new_p:
            new_p = self.lay_points(tempo["points"], new_p, spans, self.base(), stay)
        self.bands, self.sel, self.part = [(lo, hi) for lo, hi in spans if hi > lo], new_p + new_c, None
        self.app.tempo_changed()

    def put_domino(self, tempos, at, length, how, push):
        """Tempo changes pasted from Domino ([(beat from the paste's start, BPM)], each held to the next, as in
        Domino) from beat at (project.paste_from_domino). how: "replace" = like a paste of the stretch at ..
        at + length (the tempo round it as it was: back on its end, like Copy to Domino's own copy), "add" = put
        in among the points, each held to the next point (what was there leaving that beat replaced, what
        arrives kept). push: its own undo step (no notes pasted with it)."""
        new = [[b, bpm, "hold", "", "lin", False, list(BEND)] for b, bpm in tempos]
        if how == "replace":
            clip = {"length": length, "points": new, "catch": [], "spans": [(0.0, length)]}
            self.put_copies(clip, at, tr("project.paste_from_domino"), push, stay=True)
            # (nothing picked, no Select box: a whole song pasted looked all selected, user 2026-10-23; the pasted
            # notes are what's selected)
            self.bands, self.sel, self.part = [], [], None
            self.request_redraw()
            return
        if push:
            self.app.push_undo(name=tr("project.paste_from_domino"))
        pts, base = self.app.tempo["points"], self.base()
        for p in new:
            p[0] += at
            split_at(pts, p[0], base)
            on = [i for i, q in enumerate(pts) if abs(q[0] - p[0]) < 1e-9]
            if len(on) == 2:
                del pts[on[1]]
            p[0] = pts[on[0]][0]  # (on that beat exactly: a pair)
            pts.insert(on[0] + 1, p)
        drop_loose(pts)
        self.bands, self.sel, self.part = [], [], None  # (nothing picked: the pasted notes are what's selected)
        self.app.tempo_changed()

    @staticmethod
    def lay_points(pts, new, spans, base, stay=False):
        """new put among pts (in place) over the stretches spans, replacing what's inside: pts cut at each edge
        (split_at), the first point on a start (what arrives there) and the last on an end (what the part after
        leaves from) stay, new's parts leave from / arrive at them. A one-beat span: new's points replace the
        tempo leaving that beat (a jump's arriving point stays). Same-BPM / loose arriving points go (drop_loose).
        stay: new's first point stays even when it's the first of all and the same as the BPM box (a Domino paste's
        start tempo: else a later BPM box change took it over, user 2026-10-22). Returns the new points kept."""
        had = ids(pts)
        if pts:
            for lo, hi in spans:
                if hi - lo > 1e-9:
                    split_at(pts, lo, base)
                    split_at(pts, hi, base)
        edges = [p for p in pts if id(p) not in had]  # (made here: gone again if they change nothing)
        for lo, hi in spans:
            on_lo = [p for p in new if abs(p[0] - lo) < 1e-9]
            on_hi = [p for p in new if abs(p[0] - hi) < 1e-9] if hi - lo > 1e-9 else []
            edges += on_lo[:1] + on_hi[-1:]
        first, last = {}, {}  # (beat -> the first / last point there)
        for p in pts:
            k = round(p[0], 9)
            first.setdefault(k, p)
            last[k] = p
        rank, keep = {}, []
        starts = sorted(spans)
        for p in pts:
            j = bisect.bisect_right(starts, (p[0] + 1e-9, math.inf)) - 1
            lo, hi = starts[j] if j >= 0 else (math.inf, math.inf)
            if not lo - 1e-9 <= p[0] <= hi + 1e-9:
                keep.append(p)
            elif hi - lo <= 1e-9:  # (one beat: the tempo leaving it replaced, an arrival kept)
                if p is not last[round(p[0], 9)] or not new:
                    keep.append(p)
                    rank[id(p)] = 0
            elif abs(p[0] - lo) < 1e-9 and p is first[round(lo, 9)]:
                keep.append(p)
                rank[id(p)] = 0
            elif abs(p[0] - hi) < 1e-9 and p is last[round(hi, 9)]:
                keep.append(p)
                rank[id(p)] = 2
        pts[:] = sorted(keep + new, key=lambda it: (it[0], rank.get(id(it), 1)))
        drop_loose(pts)
        for p in edges:  # (an edge point holding what was held there already: no point needed)
            i = next((k for k, q in enumerate(pts) if q is p), None)
            if i is None or p[2] != "hold" or (i + 1 < len(pts) and pts[i + 1][0] == p[0]) \
                    or (i > 0 and pts[i - 1][0] == p[0]):
                continue
            prev = pts[i - 1] if i > 0 else None
            if (prev is None and p[1] == clamp_bpm(base) and not keeps(p) and not (stay and p is new[0])) or \
                    (prev is not None and prev[2] == "hold" and prev[1] == p[1] and keeps(prev) == keeps(p)):
                del pts[i]
        alive = ids(pts)
        return [p for p in new if id(p) in alive]

    def forget(self):
        """Hidden / another project opened: the Select box, picks and a catch-up being added go, Delete and Esc
        are the piano roll's again."""
        self.placing, self.drag, self.bands, self.sel, self.part, self.live = None, None, [], [], None, None
        self.box.pick_tool_hint()
        if getattr(self.app, "key_pane", None) is self:
            self.app.key_pane = None

    def pick_state(self):
        """What's picked, for an undo step (JSON): the Select box, the picked points / lines and part by place."""
        tempo, part = self.app.tempo, self.picked()
        return json.dumps({"bands": [list(s) for s in self.bands], "points": self.sel_idx("points"),
                           "catch": self.sel_idx("catch"), "part": part[1] if part and part[0] == "part" else None})

    def restore_pick(self, text):
        """An undo step's pick_state back (on the tempo line it brought back)."""
        try:
            got = json.loads(text)
            tempo = self.app.tempo
            self.bands = [tuple(s) for s in got.get("bands") or ([got["band"]] if got.get("band") else [])]
            self.sel = [tempo[key][i] for key in ("points", "catch") for i in got.get(key, ())
                        if 0 <= i < len(tempo[key])]
            part = got.get("part")
            self.part = tempo["points"][part] if part is not None and 0 <= part < len(tempo["points"]) else None
        except (ValueError, TypeError, KeyError, AttributeError):
            self.bands, self.sel, self.part = [], [], None

    def on_motion(self, e):
        self.show_position(e)
        roll = self.app.roll
        if roll.sx is None or self.view is None:
            return
        if self.placing and self.placing["step"] == 2:  # (the teal area shown up to the mouse before the press)
            self.placing["hover"] = self.snap_beat(roll.x2t(e.x), e)
            self.request_redraw()
        # the pointer + the hint line: only what can be done right here (user 2026-10-22: the whole list was a wall
        # of text)
        hint = None
        handle = self.handle_at(e.x, e.y)
        if e.x < roll.kb_w:
            want, hint = "sb_v_double_arrow", "numbers"
        elif self.placing:
            want = "sb_h_double_arrow"  # (the catch-up being added keeps its own hint)
        elif self.live_end_at(e.x, e.y) is not None:
            want, hint = "fleur", "on_live_end"
        elif self.point_at(e.x, e.y) is not None:
            want, hint = "fleur", "on_point"
        elif handle is not None:
            want, hint = "fleur", "on_handle" if handle[0] == "part" else "on_catch_handle"
        elif self.catch_at(e.x) is not None:
            want, hint = "sb_h_double_arrow", "on_catch"
        elif self.start_at(e.x) is not None or self.end_at(e.x) is not None:
            want, hint = "sb_h_double_arrow", "on_edge"
        elif self.dense_at(e.x):  # (an orange line: what it means)
            want, hint = PENCIL_CURSOR if self.box.tool.get() != "select" else SELECT_CURSOR, "on_dense"
        elif self.box.tool.get() != "select":
            want = PENCIL_CURSOR
        elif self.in_band(e.x):
            want, hint = "fleur", "on_band"  # (moves what the box picked: time and BPM)
        else:
            want = SELECT_CURSOR
        if hint is None and not self.placing and self.live_line() is not None:
            hint = "live"  # (the line just drawn: how to change / finish it)
        if not self.drag:
            set_cursor(self, want)
            if not self.placing and (hint or getattr(self.box, "_hint_key", None) != self.box.tool_hint_key()):
                self.box.pick_tool_hint(hint)

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
            for a, b, *_ in step_edges(tempo, worked, ppq):
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
        keep_spans = [(max(kb, roll.t2x(a / ppq)), w if e is None else min(w, roll.t2x(e / ppq)), u, a / ppq,
                       math.inf if e is None else e / ppq) for _, a, e, u in layout(tempo, base, ppq)["keeps"]]
        keep_spans = [s for s in keep_spans if s[1] > s[0]]
        for x0, x1, *_ in keep_spans:  # Keep timing parts (purple), their notes keep the tempo heard before
            self.create_rectangle(x0, 0, x1, h, fill=look.TEMPO_KEEP_BG, outline="")
        for x, c in roll.grid_cols(w):
            if x >= kb:
                self.create_line(x, 0, x, h, fill=c)
        for v, strong in self.labels():
            y = round(float(self.b2y(v)))
            self.create_line(kb, y, w, y, fill=look.CHART_GRID_STRONG if strong else look.CHART_GRID)
        self.draw_sig_marks()
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
        dense = self.dense_now()
        for x0, x1, u, b0, b1 in keep_spans:  # (the tempo its notes keep: dashed, with the words on top)
            y = float(self.b2y(60_000_000 / u))
            self.create_line(x0, y, x1, y, fill=look.TEMPO_KEEP, dash=(6, 3), width=lw + 1)
            # notes too close for its tempo (user 2026-10-22: shown while editing, not only after the export): a
            # dashed orange line on each spot (one per pixel)
            bad = bisect.bisect_left(dense, b0 - 1e-9) < bisect.bisect_right(dense, b1 + 1e-9)
            shown = dense[bisect.bisect_left(dense, max(b0, roll.x2t(x0)) - 1e-9):
                          bisect.bisect_right(dense, min(b1, roll.x2t(x1)) + 1e-9)]
            for x in np.unique(np.round(roll.t2x(np.asarray(shown, float)))).tolist() if shown else ():
                self.create_line(x, 0, x, h, fill=look.WARN, dash=(3, 3), width=lw)
            text, colour = (tr("tempo.keep_dense"), look.WARN) if bad else (tr("tempo.keep"), look.TEMPO_KEEP)
            if x1 - x0 > 70 * self.scale:
                self.create_text((x0 + x1) / 2, top + 8 * self.scale, text=text, fill=colour,
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
        got = self.handles()
        if got is not None:  # the picked line's / catch-up's bend handle: round, like the velocity pane's curve
            at = got[1]
            x, y, r = roll.t2x(at[0]), float(self.b2y(at[1])), 5 * self.scale
            self.create_oval(x - r, y - r, x + r, y + r, fill=look.CHART_POINT, outline=look.TEMPO_PICKED,
                             width=lw + 1)
        live = self.live_line()
        for b, v in live["ends"] if live is not None else ():  # the live line's end squares (velocity pane's look)
            x, y, r = roll.t2x(b), float(self.b2y(v)), 5 * self.scale
            self.create_rectangle(x - r, y - r, x + r, y + r, fill=look.CHART_POINT, outline=look.TEMPO_PICKED,
                                  width=lw + 1)
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
        spans = d["more"] + [d["span"]] * bool(d["span"]) if d and d["kind"] == "box" else self.bands
        if spans:  # (the whole height, like the box many MIDI programs show in their lanes)
            h = self.winfo_height()
            draw_boxes(self, [(roll.t2x(a), 1, roll.t2x(b), h - 2) for a, b in spans], roll.kb_w, 0, self.scale)
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

    def show_play_bpm(self):
        app = self.app
        if not self.box.winfo_ismapped():  # (playing: every frame; worked out only while it shows)
            return
        v = bpm_at(app.tempo, self.base(), app.ppq, math.floor(app.playhead * app.ppq + 1e-9))
        text = f"{v:,.2f}"  # (no "BPM": Domino shows the bare number, user 2026-10-22)
        if self.box.play_bpm.cget("text") != text:
            self.box.play_bpm.config(text=text)

    def draw_playhead(self):
        self.show_play_bpm()
        self.delete("playhead")
        roll = self.app.roll
        if roll.sx is None or not self.winfo_ismapped():
            return
        x = round(roll.t2x(self.app.playhead))
        if roll.kb_w <= x <= self.winfo_width():
            self.create_line(x, 0, x, self.winfo_height(), fill=look.PLAY_LINE, width=max(1, round(self.scale)),
                             tags="playhead")
