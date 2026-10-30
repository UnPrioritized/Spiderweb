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
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from files.system import double_click_ms
from notes.tempo import (BEND, CATCH_BEND, CURVES, EVERY, FORMULA, MAX_BPM, MIN_BPM, SCALES, SHAPES, bend_of, bpm_at,
                         bpm_text, clamp_bpm, curve, drawn_bpm, drawn_from, drop_loose, end_point, formula_fn,
                         catch_get, catch_set, heard_before, heard_line, heard_of, keep_off, keep_runs, keeps, on_beat,
                         layout, plans, set_bend, shift_heard, shown_bpm, split_at, step_edges, stretches,
                         swap_part, tempo_text)
from notes.tempo import changed as tempo_edited, keep_heard, FAR
from notes.loaded import file_tempos
from notes.meter import bar_beat_tick, from_bar_beat_tick, sigs_of
from notes.tempo_file import FileTempo, limit_spots, loaded_part, sorted_union
from roll.roll_draw import screen_lines
from roll.zoombar import add_zoom_bars
from roll.roll_shared import (ALT, BOX_STILL, CTRL, PENCIL_CURSOR, SELECT_CURSOR, SHIFT, bind_double_right, draw_boxes,
                              grab_while_panning,
                              set_cursor)
from window import look
from window.conductor_marks import ROW, ConductorMarks
from window.melody_window import open_melody
from window.widgets import Scrub, ShortLabel, Tooltip, unit_word

BPM_SNAPS = (0, 0.1, 1, 5, 10)  # the BPM snap dropdown's steps (0 = off)
NICE = (0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000, 5000000,
        10000000)  # numbers filled in on zoom
LABEL_GAP = 15  # px between two numbers on the left (times the window's scale)
DOUBLE_MS = double_click_ms()  # how quick a second click has to be to make a double click (the system's setting)
MENU_CLICK_S = 0.25  # a press this soon after the right-click menu closed = the click that closed it (Windows)
OFF_CLICK_S = 0.5  # a press this soon on the spot of an out-of-view arrow just clicked = its double-click's second
AREA_SHADE = 0.08  # how far a tick line on a coloured area is from that colour (at full strength)
KEEP_SHADE = 0.14  # on the Keep timing parts' purple (user 2026-10-24: 0.08 still too faint there)


def area_shade(colour, by):
    """A tick line's colour on a coloured area (user 2026-10-24): the area's colour, darker (dark look: lighter,
    like the grid there), by = how strong the tick lines show."""
    to = 255 if look.DARK else 0
    k = (KEEP_SHADE if colour == look.TEMPO_KEEP_BG else AREA_SHADE) * by
    return "#%02x%02x%02x" % tuple(round(c + (to - c) * k) for c in (int(colour[i:i + 2], 16) for i in (1, 3, 5)))


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
    formula, BPM change, Keep timing, bend, heard tempo line (its own copies)."""
    if p is None:
        return ["hold", "", scale, False, list(BEND)]
    return [p[2], p[3], p[4], keeps(p), list(bend_of(p))] + ([copy.deepcopy(p[7])] if heard_of(p) else [])


KEEP_HOW = ("off", "audio", "visual")  # a part's Keep timing dropdown (user's names: Keep the audio / the visual)


def keep_how(p):
    if not keeps(p):
        return "off"
    h = heard_of(p)
    return "visual" if h and h.get("visual") else "audio"


class TempoBox(ttk.Frame):
    """The Tempo pane with two bar rows on top: tools, BPM snap, Graph; then what's picked + a hint."""

    def __init__(self, parent, app, scale):
        super().__init__(parent)
        self.app = app
        bar = self.bar = ttk.Frame(self, padding=(2, 2))
        bar.pack(fill="x")
        ttk.Label(bar, text=tr("tempo.tempo")).pack(side="left", padx=(2, 4))
        # the exact BPM at the play line (user 2026-10-22), then the tempo heard there in purple brackets, only where
        # it differs: inside a Keep timing part (user 2026-10-24: right after the number). Both in a box with room
        # for the longest, "60,000,000.00 (60,000,000.00)": playing never pushes the tools after it about
        nums = self.play_box = ttk.Frame(bar)
        nums.pack(side="left", padx=(0, 4))
        self.play_bpm = ttk.Label(nums, text="", foreground=look.INFO)
        self.play_bpm.pack(side="left")
        Tooltip(self.play_bpm, tr("tempo.play_bpm_tip"))
        self.play_heard = ttk.Label(nums, text="", foreground=look.TEMPO_KEEP)
        self.play_heard.pack(side="left", padx=(4, 0))
        Tooltip(self.play_heard, tr("tempo.play_heard_tip"))
        most = f"{60_000_000:,.2f}"
        font = tkfont.Font(root=self, font=ttk.Style(self).lookup("TLabel", "font") or "TkDefaultFont")
        nums.configure(width=font.measure(f"{most} ({most})") + 8, height=self.play_bpm.winfo_reqheight())
        nums.pack_propagate(False)
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
        # which line the tools draw (user 2026-10-24: two buttons only; no key, Tab is the track list's)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=5)
        ttk.Label(bar, text=tr("tempo.draw")).pack(side="left", padx=(0, 4))
        self.mode = "visual"  # visual / heard
        self.mode_vars, self.mode_btns = {}, {}
        for key in ("visual", "heard"):
            var = self.mode_vars[key] = tk.BooleanVar(value=key == "visual")
            b = self.mode_btns[key] = ttk.Checkbutton(bar, text=tr("tempo.draw_" + key), variable=var,
                                                      style="Toolbutton", takefocus=False,
                                                      command=lambda k=key: self.set_mode(k))
            b.pack(side="left", padx=1)
            Tooltip(b, tr("tempo.draw_%s_tip" % key))
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
        self.tall = ttk.Frame(row2, width=1, height=tall)  # (keeps its height when nothing's picked)
        self.tall.pack(side="left")
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
        # several points picked: Keep timing for every part they start (user 2026-10-24)
        self.keeps_label = ttk.Label(row, text=tr("tempo.keep"))
        self.keeps_box = ttk.Combobox(row, state="readonly", width=15,
                                      values=[tr("tempo.keep_" + k) for k in KEEP_HOW])
        self.keeps_box.bind("<<ComboboxSelected>>", lambda e: self.picked_keeps())
        for w in (self.keeps_label, self.keeps_box):
            Tooltip(w, tr("tempo.keeps_tip"))
        self.part_row = row = ttk.Frame(row2)
        ttk.Label(row, text=tr("tempo.curve")).pack(side="left")
        self.curve = ttk.Combobox(row, state="readonly", width=20, values=[tr("tempo.curve_" + k) for k in CURVES])
        self.curve.pack(side="left", padx=(4, 0))
        self.curve.bind("<<ComboboxSelected>>", lambda e: self.picked_curve())
        self.scale_box = ttk.Combobox(row, state="readonly", width=12, values=[tr("tempo.scale_" + k) for k in SCALES])
        self.scale_box.pack(side="left", padx=(4, 0))
        self.scale_box.bind("<<ComboboxSelected>>", lambda e: self.picked_scale())
        Tooltip(self.scale_box, tr("tempo.scale_tip"))
        # Keep timing: Off / Keep the audio / Keep the visual (user 2026-10-24; was a tick); switching swaps the lines
        self.keep_label = ttk.Label(row, text=tr("tempo.keep"))
        self.keep_label.pack(side="left", padx=(8, 0))
        self.keep_box = ttk.Combobox(row, state="readonly", width=15,
                                     values=[tr("tempo.keep_" + k) for k in KEEP_HOW])
        self.keep_box.pack(side="left", padx=(4, 0))
        self.keep_box.bind("<<ComboboxSelected>>", lambda e: self.picked_keep())
        for w in (self.keep_label, self.keep_box):
            Tooltip(w, tr("tempo.keep_tip"))
        self.draw_scale = "lin"  # what new drawn parts get (the last one picked)
        self.catch_row = row = ttk.Frame(row2)
        ttk.Label(row, text=tr("tempo.catch")).pack(side="left")
        self.shape = ttk.Combobox(row, state="readonly", width=12, values=[tr("tempo.shape_" + k) for k in SHAPES])
        self.shape.pack(side="left", padx=(4, 0))
        self.shape.bind("<<ComboboxSelected>>", lambda e: self.picked_shape())
        self.wheeled = False  # (Drawn picked with the mouse wheel: its window doesn't open by itself, as Speed: Custom)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):  # (before the dropdown's own wheel step)
            self.shape.bind(seq, lambda e: self.wheel_pick(), add="+")
        self.graph_button = ttk.Button(row, text=tr("path.graph"), command=self.open_graph)  # (Drawn only)
        self.graph_window = None  # the drawn catch-up's window (catch_graph.py), while open
        self.fit_tall()
        self.formula_var = tk.StringVar()
        self.formula_box = ttk.Entry(row2, textvariable=self.formula_var, width=16)
        self.formula_box.bind("<Return>", lambda e: self.typed_formula())
        self.formula_box.bind("<FocusOut>", lambda e: self.left_box(self.typed_formula))
        self.formula_box.bind("<FocusIn>", lambda e: self.typing_for(formula=True))
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
        pane, tempo = self.pane, self.pane.ed()
        pick = pane.picked()
        kind = pick[0] if pick else None
        rows = {"point": [self.point_row], "part": [self.part_row]}.get(kind, [])
        j = pane.bar_catch()  # (the catch-up row: picked, in the Select box beside the points, or at the play line)
        formula = False
        mine = self.focus_get() if not getattr(self, "_rewrite", False) else None  # (the box being typed in)
        self._rewrite = False
        if kind == "point":
            if mine is not self.bpm_box:
                self.sync_bpm([tempo["points"][i] for i in pick[1]])
            many = self.mode == "visual" and len(pick[1]) > 1
            if many != bool(self.keeps_box.winfo_manager()):
                for w in (self.keeps_label, self.keeps_box):
                    w.pack(side="left", padx=(8 if w is self.keeps_label else 4, 0)) if many else w.pack_forget()
            if many:
                hows = {keep_how(tempo["points"][i]) for i in pick[1][:-1]}
                self.keeps_box.set(tr("tempo.keep_" + hows.pop()) if len(hows) == 1 else "")
        elif kind == "part":
            p = tempo["points"][pick[1]]
            last = pick[1] == len(tempo["points"]) - 1  # (after the last point: it holds on, only Keep timing)
            self.curve.current(CURVES.index(p[2]))
            self.curve.state(["disabled"] if last else ["!disabled"])
            self.scale_box.current(SCALES.index(p[4]))
            self.scale_box.state(["disabled"] if p[2] == "hold" or last else ["!disabled"])
            heard = self.mode == "heard"  # (the heard line's parts: no Keep timing of their own)
            if heard != (not self.keep_box.winfo_manager()):
                for w in (self.keep_label, self.keep_box):
                    w.pack_forget() if heard else w.pack(side="left", padx=(8 if w is self.keep_label else 4, 0))
            if not heard:
                self.keep_box.current(KEEP_HOW.index(keep_how(p)))
            formula = p[2] == "formula"
            text, tip = p[3] or FORMULA, tr("tempo.formula_tip")
        if j is not None:
            rows.append(self.catch_row)
            c = tempo["catch"][j]
            self.shape.current(SHAPES.index(c[1]))
            drawn = c[1] == "drawn"
            if drawn != bool(self.graph_button.winfo_manager()):
                self.graph_button.pack(side="left", padx=(4, 0)) if drawn else self.graph_button.pack_forget()
            formula = c[1] == "formula" and pick is not None  # (the play line's row: never the box, hunt)
            text, tip = c[2] or FORMULA, tr("tempo.catch_formula_tip")
        if not formula and kind is None and self.tool.get() == "formula":  # (the Formula pencil's formula)
            formula, text, tip = True, self.draw_formula, tr("tempo.formula_tip")
        if formula:
            if mine is not self.formula_box:
                self.formula_var.set(text)
            self.formula_tip.set_text(tip)
            rows.append(self.formula_box)
        if j is not None:
            res, bad = pane.catch_result(j)
            self.result.config(text=res, foreground=look.ERROR if bad else look.TEMPO_CATCH)
            rows.append(self.result)
        if mine is not self.every_box:
            self.every_var.set(str(self.app.tempo.get("every", EVERY)))
        self.bars_var.set(self.app.tempo.get("bars", True))
        can = bool(keep_runs(self.app.tempo))  # (Heard tempo: only while some part keeps timing)
        self.mode_btns["heard"].state(["!disabled"] if can else ["disabled"])
        if self.mode == "heard" and not can:
            return self.set_mode("visual")
        if self.graph_window:
            self.graph_window.sync()
        shown = tuple(map(str, rows))
        if shown != self._shown:  # (packed again only when it changes: no flashing while dragging)
            self.fit_tall()
            for w in (self.point_row, self.part_row, self.catch_row, self.formula_box, self.result):
                w.pack_forget()
            for w in rows:
                w.pack(side="left", padx=(0, 8), before=self.hint)
            self._shown = shown

    def forget_typing(self):
        """Undo / redo / a History jump: the boxes show the line as it is now, even the one being typed in, and
        what was typed there is dropped (leaving the box changes nothing; hunt 10-27: it came back as a new step)."""
        self._typed_for = None
        self._committed = False
        self._rewrite = True

    def fit_tall(self):
        """The second row as tall as its tallest box / button always (user 2026-10-24: the Graph… button is taller
        than the boxes, so the pane jumped as it came and went); measured again each time its rows change, so
        another look / font size still fits."""
        h = max(w.winfo_reqheight() for w in (self.every_box, self.snap, self.bpm_box, self.keeps_box, self.curve,
                                              self.shape, self.graph_button))
        if int(self.tall.cget("height")) != h:
            self.tall.configure(height=h)

    def bpm_snap(self):
        return BPM_SNAPS[max(0, self.snap.current())]

    def typing_for(self, formula=False):
        """A box clicked: its typing goes to what's picked now, even if a pane click picks something else first.
        formula: the Formula box (not a part's: the catch-up the bar shows, bar_catch)."""
        pick = self.pane.picked()
        tempo = self.pane.ed()
        j = self.pane.bar_catch() if formula and pick and pick[0] == "point" else None
        if j is not None:
            self._typed_for = ("catch", tempo["catch"][j])
        elif pick and pick[0] == "point":
            self._typed_for = ("point", [tempo["points"][i] for i in pick[1]])
        elif pick:
            self._typed_for = (pick[0], tempo["points" if pick[0] == "part" else "catch"][pick[1]])
        else:
            self._typed_for = None
        self._committed = False

    def target(self):
        """(kind, item(s)) the typing goes to, still in the line; else what's picked now."""
        tempo = self.pane.ed()
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
            p = self.pane.ed()["points"][pick[1]]
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
            p = self.pane.ed()["points"][pick[1]]
            scale = SCALES[self.scale_box.current()]
            self.draw_scale = scale
            if scale != p[4]:
                self.app.push_undo(name=tr("tempo.step_curve"))
                p[4] = scale
                self.app.tempo_changed()
        self.app.roll.focus_set()

    def picked_keep(self):
        pick = self.pane.picked()
        if pick and pick[0] == "part" and self.mode == "visual":
            i = pick[1]  # (a part of a Keep timing run: the whole run, user 2026-10-24: they're one part; but Off =
            # only the picked part, user 2026-10-30: the parts before / after keep it, reading the same heard line)
            want = KEEP_HOW[max(0, self.keep_box.current())]
            s, t = next((r for r in keep_runs(self.app.tempo) if r[0] <= i <= r[1]), (i, i))
            self.set_keep([i] if want == "off" else list(range(s, t + 1)), want)
        self.app.roll.focus_set()

    def picked_keeps(self):
        """Several points picked: Keep timing for the parts between them (the last one's part leaves the picked
        stretch: not it)."""
        pick = self.pane.picked()
        if pick and pick[0] == "point" and len(pick[1]) > 1 and self.mode == "visual" and \
                self.keeps_box.current() >= 0:
            self.set_keep(pick[1][:-1], KEEP_HOW[self.keeps_box.current()])
        self.app.roll.focus_set()

    def set_keep(self, idx, want):
        """Keep timing Off / Keep the audio / Keep the visual for the parts of points idx (one undo step): turned on
        = the tempo after goes on from where the heard line ends (end_point); audio <-> visual = each run's two
        lines swap (swap_part)."""
        tempo = self.app.tempo
        pts = tempo["points"]
        if not idx or all(keep_how(pts[i]) == want for i in idx):
            return
        base, ppq = self.pane.base(), self.app.ppq
        if want != "off":  # (not inside a catch-up's yellow area, user 2026-10-24: refused, the status says why)
            was = [keeps(pts[i]) for i in idx]
            for i in idx:
                pts[i].extend([False] * (6 - len(pts[i])))
                pts[i][5] = True
            offs = keep_off(tempo, stretches(tempo, base))
            for i, w in zip(idx, was):
                pts[i][5] = w
            ends = [pts[i + 1][0] if i + 1 < len(pts) else math.inf for i in idx]
            if any(a < e2 - 1e-9 and pts[i][0] < b - 1e-9 for i, e2 in zip(idx, ends) for a, b in offs):
                self.app.status.config(text=tr("tempo.keep_in_catch_refused"))
                return self.sync()
        self.app.push_undo(name=tr("tempo.step_keep"))
        picked = [pts[i] for i in idx]
        newly = set()
        for p in picked:
            p.extend([False] * (6 - len(p)))
            if want != "off" and not keeps(p):
                newly.add(id(p))
            p[5] = want != "off"  # (only the tick: the bend and heard line after it stay, hunt 2026-10-22)
        tempo_edited()
        if want != "off":
            mine = ids(picked)
            for s, t in reversed(keep_runs(tempo)):  # (from the end: the indices before stay right)
                if not any(id(q) in mine for q in pts[s:t + 1]):
                    continue
                if keep_how(pts[s]) != want:
                    swap_part(tempo, base, ppq, s, t)
                elif any(id(q) in newly for q in pts[s:t + 1]):
                    end_point(tempo, base, ppq, t)
                tempo_edited()
        self.app.tempo_changed()

    def set_mode(self, key):
        """Draw: Visual tempo / Heard tempo (user 2026-10-24): which line the tools edit; the other fades and can't
        be clicked."""
        for k, var in self.mode_vars.items():
            var.set(k == key)
        if key == "heard" and not keep_runs(self.app.tempo):
            key = "visual"
            self.mode_vars["visual"].set(True)
            self.mode_vars["heard"].set(False)
        if key != self.mode:
            self.pane.confirm()
            self.pane.cancel_placing()
            self.pane.drag, self.pane.bands, self.pane.sel, self.pane.part = None, [], [], None
            self.mode = key
            self.pane._ed = None  # (the parts worked out again: the visual line may have changed, hunt 2026-10-24)
            pane = self.pane  # (the run at the play line to start with, else the first)
            run = pane.run_at(self.app.playhead) or (pane.runs() or [None])[0] if key == "heard" else None
            pane.run = run and run[0]
        self.sync()
        self.pick_tool_hint()
        self.pane.request_redraw()
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
        j = self.pane.bar_catch()  # (picked, in the Select box or at the play line)
        if j is not None:
            c = self.pane.ed()["catch"][j]
            shape = SHAPES[self.shape.current()]
            if shape != c[1]:
                self.app.push_undo(name=tr("tempo.step_catch_shape"))
                if shape == "drawn" and catch_get(c, 6) is None:  # (starts from the shape picked before; one
                    catch_set(c, 6, drawn_from(c[1], c[2], catch_get(c, 5)))  # drawn earlier is kept)
                c[1] = shape
                if shape == "formula" and not c[2]:
                    c[2] = FORMULA
                self.app.tempo_changed()
            wheeled, self.wheeled = self.wheeled, False
            if shape == "drawn" and not wheeled:
                return self.open_graph()
        self.app.roll.focus_set()

    def wheel_pick(self):
        """The wheel is about to step the Catch-up shape dropdown (its own wheel comes after this)."""
        self.wheeled = True
        self.after_idle(lambda: setattr(self, "wheeled", False))

    def open_graph(self):
        """A drawn catch-up's window (catch_graph.py)."""
        from window.catch_graph import CatchGraph
        if not self.graph_window:
            self.graph_window = CatchGraph(self)
        self.graph_window.lift()
        self.graph_window.focus_set()

    def typed_formula(self):
        got = self.target()
        text = self.formula_var.get().strip()
        if got and got[0] == "point" and self.pane.bar_catch() is not None:  # (the catch-up row beside them)
            got = ("catch", self.pane.ed()["catch"][self.pane.bar_catch()])
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
        self.fill_preview = None  # Fill with tempo jumps' faint preview: {"tempo", "worked", "span"} (melody_window)
        self._dense = (None, [])  # (what it was worked out for, beats where Keep timing notes are too close)
        self._limits = {"dense": [], "gave": [], "unfit": [], "moved": []}  # (the limit texts' spots, tempo_file.limit_spots)
        self._dense_job = None
        self.drag = None
        self._pan = None
        self._redraw_pending = False
        self._typing = None  # the BPM box over a point (double-click)
        self.run = None  # Draw: Heard tempo: the Keep timing run whose heard line is edited (its first point)
        self._ed = None  # (first point, its line, the line as a tempo line, the run's points) being edited
        self._runs = (None, [])
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
        self.handle_tip = Tooltip(self, "")  # (on a bend handle while the mouse rests there: handle_hover)
        self.handle_tip.spot = lambda: (self.winfo_pointerx() + 12, self.winfo_pointery() + 20)

    # ------------------------------------------------------------ the line the tools edit (user 2026-10-24)

    # Draw: Visual tempo = the project's tempo line. Draw: Heard tempo = one Keep timing run's heard line (the run
    # under the press), handed to every tool as a tempo line of its own ({"points": the line itself, "catch": []}),
    # so they all work the same on it; app.tempo_changed -> write_heard puts it back into the run.

    def runs(self):
        """[(first point, the run's points, first beat, end beat or None)] of the Keep timing runs."""
        tempo = self.app.tempo
        key = tempo_text(tempo)
        if self._runs[0] is not key:
            pts = tempo["points"]
            self._runs = (key, [(pts[i], pts[i:j + 1], pts[i][0], pts[j + 1][0] if j + 1 < len(pts) else None)
                                for i, j in keep_runs(tempo)])
        return self._runs[1]

    def run_at(self, b):
        """The Keep timing run at beat b (its end beat counts: a line can end there), or None."""
        return next((r for r in self.runs() if r[2] - 1e-9 <= b and (r[3] is None or b <= r[3] + 1e-9)), None)

    def ed(self):
        """The tempo line the tools edit: the project's, or (Draw: Heard tempo) the picked run's heard line; no run
        picked = an empty one."""
        if self.box.mode != "heard":
            return self.app.tempo
        got = self._ed
        p = self.run
        if got is not None and got[0] is p and p is not None:
            h = heard_of(p)
            if h is None or h["line"] is got[1]:
                return got[2]
        run = next((r for r in self.runs() if r[0] is p), None) if p is not None else None
        if run is None:
            self.run = self._ed = None
            return {"points": [], "catch": [], "every": self.app.tempo.get("every", EVERY)}
        line = heard_of(p)["line"] if heard_of(p) else []
        # (points past its ends stay as they are until the line is really edited: cut off then, in that edit's undo
        # step, hunt 2026-10-24: opening it changed the saved line)
        self._ed = (p, line, {"points": line, "catch": [], "every": self.app.tempo.get("every", EVERY)},
                    list(run[1]))
        return self._ed[2]

    def ed_base(self):
        """What the edited line plays before its first point: the project's BPM / the tempo heard before the run."""
        got = self._ed if self.box.mode == "heard" else None
        if got is None:
            return self.base()
        tempo_edited()  # (its line may have changed in place just now)
        pts = self.app.tempo["points"]
        at = {id(q): k for k, q in enumerate(pts)}
        ks = [at[id(q)] for q in got[3] if id(q) in at]
        return heard_before(self.app.tempo, self.base(), self.app.ppq, ks[0], ks[-1]) if ks else self.base()

    def run_span(self):
        """(first, end beat; inf = the song's end) of the run being edited, None outside Heard tempo."""
        got = self._ed if self.box.mode == "heard" else None
        if got is None:
            return None
        pts = self.app.tempo["points"]
        at = {id(q): k for k, q in enumerate(pts)}
        ks = [at[id(q)] for q in got[3] if id(q) in at and keeps(q)]
        if not ks or id(got[0]) not in at:
            return None
        return got[0][0], pts[ks[-1] + 1][0] if ks[-1] + 1 < len(pts) else math.inf

    def in_run(self, b):
        """b kept inside the run being edited (Heard tempo; else as it is)."""
        span = self.run_span()
        return b if span is None else min(max(b, span[0]), span[1])

    def write_heard(self):
        """Draw: Heard tempo: the edited heard line put back into its run: points past its ends cut off there (the
        line the same inside), each part of the run its own copy, the "visual" mark kept (app.tempo_changed)."""
        got = self._ed if self.box.mode == "heard" else None
        span = self.run_span()
        if got is None or span is None:
            return
        p, line, _, members = got
        tempo_edited()
        a, e = span
        base = self.ed_base()
        for q in line:  # (pasted from the drawn line: no Keep timing / line of their own)
            q.extend([False] * (6 - len(q)))
            q[5] = False
            del q[7:]
        if any(q[0] < a - 1e-9 for q in line):
            split_at(line, a, base)
            line[:] = [q for q in line if q[0] >= a - 1e-9]
        if e != math.inf and any(q[0] > e + 1e-9 for q in line):
            split_at(line, e, base)
            line[:] = [q for q in line if q[0] <= e + 1e-9]
        if not self.drag:  # (a drag: when let go, as the drawn line's)
            drop_loose(line)
        h = heard_of(p)
        if h is not None and h.get("end") is not None and e != math.inf:
            # the point after the part, put there by Keep timing: follows where the heard line now ends, until the
            # user changes it (hunt 2026-10-24, user picked; the dotted line shows a jump they made)
            at = [q for q in self.app.tempo["points"] if abs(q[0] - e) < 1e-9]
            if not at or abs(at[-1][1] - h["end"]) > 1e-9 * h["end"]:
                del h["end"]
            else:
                hl = copy.deepcopy(line)
                split_at(hl, e, base)
                v = clamp_bpm(next(q[1] for q in hl if abs(q[0] - e) < 1e-9))
                at[-1][1] = h["end"] = v
        if h is None and line:
            p.extend([False] * (6 - len(p)) + [list(BEND)] * (len(p) < 7))
            del p[7:]
            p.append(h := {"line": line})
        elif h is not None and not line and not h.get("visual") and h.get("end") is None:
            del p[7:]
            h = None
        alive = ids(self.app.tempo["points"])
        for q in members:
            if q is not p and id(q) in alive and keeps(q):
                del q[7:]
                if h is not None:
                    q.extend([False] * (6 - len(q)) + [list(BEND)] * (len(q) < 7))
                    q.append(copy.deepcopy(h))

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

    def view_bpm(self):
        """The bold number + its faint line (user 2026-10-30; it showed the old BPM box's 120): the tempo coming
        into view at the piano roll's left edge: the drawn line's (the file's whole microseconds made 90 read
        89.99995), 2 decimals like the bar's "Tempo"."""
        b = max(0.0, self.app.roll.x2t(self.app.roll.kb_w))
        return round(float(drawn_bpm(self.app.tempo, self.base(), [b])[0]), 2)

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
        vals = [base] + [p[1] for p in tempo["points"]] + [q[1] for p in tempo["points"] for q in heard_line(p)]
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
        if not self.sel:  # (a loaded MIDI's thousands of points, nothing picked: no walk through them)
            return []
        self.is_sel(None)
        got = self._sel_ids[2]
        return [i for i, it in enumerate(self.ed()[key]) if id(it) in got]

    def picked(self):
        """What the bar shows: ("point", [indices]), ("part", i), ("catch", j) or None."""
        tempo = self.ed()
        had = len(self.sel)
        if self.sel:
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

    def bar_catch(self):
        """The catch-up line whose row the bar shows (user 2026-10-24: only clicking its line was hard to find): the
        one picked; the only one in the Select box (beside the points' BPM box); with nothing picked, the one whose
        yellow / teal area the play line is in, while not playing (playing, the row would pop in and out) and the
        mouse isn't held in the pane. None:
        none (the visual tempo's only)."""
        if self.box.mode != "visual":
            return None
        pick = self.picked()
        if pick and pick[0] == "catch":
            return pick[1]
        if pick and pick[0] == "point":
            lines = self.sel_idx("catch")
            return lines[0] if self.bands and len(lines) == 1 else None
        if pick or self.app.player.running or self.drag or self.placing:  # (not while the mouse is held, user)
            return None
        at = self.app.playhead
        for j, (st, _) in enumerate(plans(self.app.tempo, self.base(), self.app.ppq)):
            if st["end"] is not None and min(st["from"], st["line"]) <= at < st["end"]:
                return j
        return None

    def select_all(self):
        """Ctrl+A while this pane was clicked last: every point and catch-up line, in one Select box over them
        (user 2026-10-22: Delete then clears the whole line)."""
        tempo = self.ed()
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
        """The point under (x, y), or None; none while the dots are too crowded to be drawn (draw_all: more than
        one per 3 px; hunt 2026-10-29: hidden dots were grabbed and moved)."""
        roll, r = self.app.roll, 7 * self.scale
        pts = self.ed()["points"]
        kb, w = roll.kb_w, self.winfo_width()
        i0, i1 = self.shown_points(kb - 4 * self.scale, w + 4 * self.scale, pts)
        if (i1 - i0) * 3 > w - kb:
            return None
        best = None
        j0, j1 = self.shown_points(x - r - 2, x + r + 2, pts)  # (only those near enough sideways: every point per
        # mouse move was ~28 ms at 8,000 points, speed hunt 2026-10-29)
        for i in range(j0, j1):
            b, v = pts[i][0], pts[i][1]
            d = math.hypot(roll.t2x(b) - x, float(self.b2y(v)) - y)
            if d <= r and (best is None or d < best[0]):
                best = (d, i)
        return best and best[1]

    def catch_at(self, x):
        roll = self.app.roll
        for i, c in enumerate(self.ed()["catch"]):
            if abs(roll.t2x(c[0]) - x) <= 5 * self.scale:
                return i
        return None

    def start_at(self, x):
        """The catch-up whose yellow area's left edge is at x (its index), or None."""
        roll = self.app.roll
        for j, (st, _) in enumerate(plans(self.ed(), self.base(), self.app.ppq)):
            if st["end"] is not None and st["from"] < st["line"] and abs(roll.t2x(st["from"]) - x) <= 4 * self.scale:
                return j
        return None

    def zone_at(self, x):
        """The catch-up whose yellow or teal area is at x (its index; the later one where two meet), or None."""
        b, got = self.app.roll.x2t(x), None
        for j, (st, _) in enumerate(plans(self.ed(), self.base(), self.app.ppq)):
            if st["end"] is not None and min(st["from"], st["line"]) <= b < st["end"]:
                got = j
        return got

    def bad_why(self, x):
        """Why the catch-up whose area (or "Not possible" text, with nothing to end on) is at x can't work
        (tempo.catch_plan's "why"), None when it works or nothing's there."""
        roll, b = self.app.roll, self.app.roll.x2t(x)
        got = None
        for st, plan in plans(self.ed(), self.base(), self.app.ppq):
            if plan["ok"] or not plan.get("why"):
                continue
            if st["end"] is not None and min(st["from"], st["line"]) <= b < st["end"] or \
                    st["end"] is None and 0 <= x - roll.t2x(st["line"]) <= 80 * self.scale:
                got = plan["why"]
        return got

    def zones(self, skip=None):
        """[(first, end beat)] of the catch-ups' yellow + teal areas (skip: leave out catch-up line skip's own)."""
        return [(min(st["from"], st["line"]), st["end"]) for j, (st, _) in
                enumerate(plans(self.ed(), self.base(), self.app.ppq))
                if st["end"] is not None and (skip is None or self.ed()["catch"][j] is not skip)]

    def room(self, b, skip=None):
        """(first, last beat) free of other catch-ups' areas around beat b (user 2026-10-24: a catch-up being
        made / dragged stops dead at another one's edge), None when b is inside one."""
        lo, hi = 0.0, math.inf
        for a, e in self.zones(skip):
            if a - 1e-9 <= b < e - 1e-9 and not abs(b - a) < 1e-9:
                return None
            if e <= b + 1e-9:
                lo = max(lo, e)
            elif a >= b - 1e-9:
                hi = min(hi, a)
        return lo, hi

    def edge_at(self, x):
        """("start", j) / ("end", j): the catch-up edge under the mouse. Where one's teal end touches another's
        yellow start, the area the mouse is over decides (user 2026-10-24: never a tie)."""
        s, n = self.start_at(x), self.end_at(x)
        if s is not None and n is not None and s != n:
            st = plans(self.ed(), self.base(), self.app.ppq)[s][0]
            return ("start", s) if x >= self.app.roll.t2x(st["from"]) else ("end", n)
        return ("start", s) if s is not None else ("end", n) if n is not None else None

    def pins(self, tempo=None, worked=None):
        """[(beat, 3 = yellow start / 4 = teal end, catch-up line)] of the thick lines shown: an edge set by hand the
        line went past, or with no area to show it (where the area comes back to); ALWAYS while its line is
        dragged (user 2026-10-24)."""
        tempo = self.ed() if tempo is None else tempo
        worked = plans(tempo, self.base(), self.app.ppq) if worked is None else worked
        d = self.drag if isinstance(self.drag, dict) else {}
        held = {id(s) for s, _, v in d.get("orig", ()) if v is None}  # (catch-up lines being dragged)
        out = []
        for c, (st, _) in zip(tempo["catch"], worked):
            always = id(c) in held or st["end"] is None
            for k, waiting in ((3, st["pin"]), (4, st["pin_end"])):
                at = catch_get(c, k) if always else waiting
                if at is not None:
                    out.append((at, k, c))
        return out

    def pin_at(self, x):
        """(catch-up line, 3 / 4) of the thick line under the mouse, or None."""
        roll = self.app.roll
        return next(((c, k) for at, k, c in self.pins() if abs(roll.t2x(at) - x) <= 4 * self.scale), None)

    def forget_pin(self, c, k):
        """A thick line's edge forgotten: back to automatic (one undo step; hunt 2026-10-24)."""
        self.app.push_undo(name=tr("tempo.step_catch_start" if k == 3 else "tempo.step_catch_end"))
        catch_set(c, k, None)
        self.app.tempo_changed()

    def end_at(self, x):
        """The catch-up whose teal area's right edge is at x (its index), or None."""
        roll = self.app.roll
        for j, (st, _) in enumerate(plans(self.ed(), self.base(), self.app.ppq)):
            if st["end"] is not None and abs(roll.t2x(st["end"]) - x) <= 4 * self.scale:
                return j
        return None

    # ------------------------------------------------------------ adding a catch-up: two drags (user 2026-10-21)

    def start_placing(self):
        """Add catch-up BPM: drag the yellow area (what it counts), then the teal one (where its BPM plays). A kept
        Select box is the yellow area already (several: from the first one's start to the last one's end)."""
        if self.bands:  # (the boxes come back if it's called off)
            a, b = self.bands_reach()
            if not self.catch_free(a, b):
                return
            self.placing = {"step": 2, "a": a, "b": b, "hover": None, "bands": self.bands}
            self.bands = []
        else:
            self.placing = {"step": 1}
        self.box.hint.config(text=tr("tempo.hint_catch%d" % self.placing["step"]))
        self.app.roll.focus_set()  # (Esc reaches it, whatever box had the keyboard)
        self.request_redraw()

    def start_fill(self):
        """Fill with tempo jumps…: its window (window/melody_window.py) over the kept Select box; none = drag one
        first (the window opens when it's let go). Jump areas picked last time: no box needed."""
        if self.bands or getattr(self.app, "melody_at", None) == "clicks":
            return open_melody(self.app)
        self.placing = {"step": 1, "fill": True}
        self.box.hint.config(text=tr("melody.hint_area"))
        self.app.roll.focus_set()
        self.request_redraw()

    def catch_free(self, a, b):
        """True when beats a..b touch no catch-up's yellow / teal area (user 2026-10-24: one catch-up per area, a
        second one inside it is refused); else the status line says so."""
        for st, _ in plans(self.ed(), self.base(), self.app.ppq):
            if st["end"] is not None and min(st["from"], st["line"]) < b and a < st["end"]:
                self.app.status.config(text=tr("tempo.catch_overlap"))
                return False
        return True

    def cancel_placing(self):
        """Adding a catch-up called off (Esc, right-click, another tool, Ctrl+Z): nothing left drawn, the kept
        Select box it started from back."""
        if self.placing:
            self.bands = self.placing.get("bands") or self.bands
            self.placing = None
            if self.drag and self.drag["kind"] in ("catch1", "fill1"):
                self.drag = None
            self.box.pick_tool_hint()
            self.request_redraw()

    def place_catch(self, e):
        """Step 2's press: the catch-up made (line where the yellow area ends), its end following the mouse."""
        p, tempo = self.placing, self.ed()
        tick = 1.0 / max(1, self.app.ppq)
        line = max(p["b"], tick)
        if any(abs(c[0] - line) < 1e-9 for c in tempo["catch"]) or not self.catch_free(min(p["a"], line),
                                                                                         line + tick):
            return self.cancel_placing()
        self.app.push_undo(name=tr("tempo.step_catch_add"))
        item = [line, "flat", ""]
        catch_set(item, 3, p["a"] if p["a"] < line else None)
        end = min(self.snap_beat(self.app.roll.x2t(e.x), e), (self.room(line) or (0, math.inf))[1])
        catch_set(item, 4, max(end, line + tick))  # (stops at the next catch-up's area, user 2026-10-24)
        tempo["catch"].insert(sum(1 for c in tempo["catch"] if c[0] < line), item)
        self.placing = None
        self.box.pick_tool_hint()
        self.sel, self.part = [item], None
        self.drag = {"kind": "end", "item": item, "x": e.x, "pushed": True}
        self.app.tempo_changed()

    def part_at(self, beat):
        """The part at beat: its first point's index (after the last point: the last one, for Keep timing)."""
        pts = self.ed()["points"]
        for i in range(len(pts)):
            if pts[i][0] <= beat and (i == len(pts) - 1 or beat < pts[i + 1][0]):
                return i
        return None

    # ------------------------------------------------------------ a line part's bend handle (user 2026-10-21)

    def handle_pos(self, i):
        """(beat, BPM) of part i's round bend handle (the curve's middle; an S-curve: its first half's), None when
        it has none (held / formula / the last part / both ends one BPM) or a working catch-up hides the line
        there (user: it was left hanging in the air)."""
        pts = self.ed()["points"]
        if (i is None or i + 1 >= len(pts) or pts[i][2] not in ("line", "s") or pts[i][1] == pts[i + 1][1]
                or pts[i + 1][0] <= pts[i][0]):  # (where a glide arrives: no part of its own)
            return None
        p, q = pts[i], pts[i + 1]
        cx, cy = bend_of(p)
        x, y = (2 * cx + 1) / 4, (2 * cy + 1) / 4
        if p[2] == "s":
            x, y = x / 2, y / 2
        b = p[0] + (q[0] - p[0]) * x
        if any(plan["ok"] and st["line"] <= b < st["end"] for st, plan in plans(self.ed(), self.base(),
                                                                                 self.app.ppq)):
            return None
        return b, self.along_bpm(p, q, y)

    def catch_handle_pos(self, j):
        """(beat, BPM) of catch-up j's round bend handle (fast then slow / slow then fast: its curve's middle), None
        when it has none (another shape, or it can't work)."""
        st, plan = plans(self.ed(), self.base(), self.app.ppq)[j]
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
            catch_set(self.ed()["catch"][i], 5, [min(1.0, max(0.0, 2 * fx - 0.5)),
                                                      min(1.0, max(0.0, 2 * fy - 0.5))])
            return
        pts = self.ed()["points"]
        p, q = pts[i], pts[i + 1]
        fx = (self.app.roll.x2t(e.x) - p[0]) / max(1e-12, q[0] - p[0])
        v = self.y2b(e.y)
        if p[4] == "exp":
            fy = (math.log2(v) - math.log2(p[1])) / (math.log2(q[1]) - math.log2(p[1]))
        else:
            fy = (v - p[1]) / (q[1] - p[1])
        if p[2] == "s":
            fx, fy = fx * 2, fy * 2
        set_bend(p, [min(1.0, max(0.0, 2 * fx - 0.5)), min(1.0, max(0.0, 2 * fy - 0.5))])

    def toggle_s(self, i):
        """The handle's S-curve: the part an S-curve (the same bend, mirrored on its second half) / back to a line
        (one undo step)."""
        p = self.ed()["points"][i]
        self.app.push_undo(name=tr("tempo.step_s"))
        p[2] = "line" if p[2] == "s" else "s"
        self.app.tempo_changed()

    def unbend(self, n, line=False):
        """Handle n's part straight again (line: an S-curve a line too) / catch-up n its own curve again (one undo
        step; nothing when it already is)."""
        if n[0] == "catch":
            c = self.ed()["catch"][n[1]]
            if catch_get(c, 5) is not None:
                self.app.push_undo(name=tr("tempo.step_catch_bend"))
                catch_set(c, 5, None)
                self.app.tempo_changed()
            return
        p = self.ed()["points"][n[1]]
        if bend_of(p) != BEND or line and p[2] == "s":
            self.app.push_undo(name=tr("tempo.step_bend"))
            set_bend(p, list(BEND))
            if line:
                p[2] = "line"
            self.app.tempo_changed()

    def handle_menu(self, e, n):
        """Right-click on a bend handle (user 2026-10-24, was an instant S-curve switch): Straight line / S-curve
        (ticked when on); a catch-up's: Its own curve again / Delete catch-up."""
        tempo = self.ed()
        menu = tk.Menu(self, tearoff=0)
        if n[0] == "part":
            p = tempo["points"][n[1]]
            menu.add_command(label=tr("tempo.handle_straight"), command=lambda: self.unbend(n, True),
                             state="normal" if bend_of(p) != BEND or p[2] == "s" else "disabled")
            self._s_var = tk.BooleanVar(self, value=p[2] == "s")
            menu.add_checkbutton(label=tr("tempo.handle_s"), variable=self._s_var,
                                 command=lambda: self.toggle_s(n[1]))
        else:
            c = tempo["catch"][n[1]]
            menu.add_command(label=tr("tempo.handle_own"), command=lambda: self.unbend(n),
                             state="disabled" if catch_get(c, 5) is None else "normal")
            menu.add_separator()
            menu.add_command(label=tr("tempo.delete_catch"), command=lambda: self.delete_items([c]))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            self.menu_shut = time.perf_counter()  # (see right_menu)

    def handle_hover(self, handle, e):
        """A tip box on the bend handle while the mouse rests on it (user 2026-10-24: double-click was hard to
        find)."""
        tip = self.handle_tip
        text = "" if handle is None or self.drag else tr("tempo.handle_tip" if handle[0] == "part"
                                                         else "tempo.catch_handle_tip")
        if text != tip.text:
            tip.hide()
            tip.text = text
            if text and not e.state & tip.BUTTONS:  # (not "used" like other tips: the handle only shows after a
                tip.schedule()                       # click on its part, hunt 2026-10-24)

    def snap_beat(self, b, e):
        """On the grid (Shift: free): the Select box's move, the catch-up being added, a click's play line."""
        if e.state & SHIFT:
            return max(0.0, b)
        sb = self.app.snap_beats()
        return max(0.0, round(b / sb) * sb) if sb else max(0.0, b)

    def tick_beat(self, b, skip=(), free=False):
        """b on a whole tick: where drawing puts things (user 2026-10-22: no grid, like many MIDI programs); within
        6 px of a tempo point (not one in skip), but never further than one Interval step (zoomed out, 6 px reached
        over several steps: a click there landed on the point, user 2026-10-24): its exact beat, so a jump's second
        point can be put there. free: not kept inside the run being edited (Heard tempo drawing: what lands outside
        is cut off afterwards)."""
        ppq, roll = max(1, self.app.ppq), self.app.roll
        if roll.sx:
            r = min(6 * self.scale / roll.sx, max(1, int(self.ed().get("every", EVERY))) / ppq)
            near = [p[0] for p in self.ed()["points"] if abs(p[0] - b) <= r and not any(p is s for s in skip)]
            ends = [x for _, _, a, e in self.runs() for x in (a, e) if x is not None] if free and \
                self.box.mode == "heard" else self.run_span() or ()
            near += [x for x in ends if abs(x - b) <= r]  # (Heard tempo: the parts' ends)
            if near:
                return min(near, key=lambda pb: abs(pb - b))
        b = max(0.0, round(b * ppq) / ppq)
        return b if free else self.in_run(b)

    def move_beat(self, b, e, skip=(), beats=None):
        """A point / line / catch-up edge moved afterwards: onto the nearest note start or end within 10 px (user
        2026-10-22: tempo changes belong there) or tempo point (not one in skip, the ones moving; beats: these
        beats instead), else a whole tick; Shift = ticks only."""
        roll, ppq = self.app.roll, max(1, self.app.ppq)
        if e.state & SHIFT or roll.sx is None:
            return max(0.0, round(b * ppq) / ppq)
        if beats is None and self.run_span() is not None:  # (Heard tempo: the part's ends too)
            beats = [p[0] for p in self.ed()["points"] if not any(p is s for s in skip)] + [
                x for x in self.run_span() if x != math.inf]
        r = 10 * self.scale / roll.sx
        lo, hi = (b - r) * ppq, (b + r) * ppq
        first, end = roll.visible_range(lo, hi)
        rows = roll.sorted_notes()[first:end]
        rows = rows[rows[:, 5] < len(self.app.shapes)]  # (the track list's faint tracks: only shown)
        near = np.concatenate([rows[:, 0], rows[:, 1]]).astype(float)
        near = near[(near >= lo) & (near <= hi)] / ppq
        if beats is None:
            beats = [p[0] for p in self.ed()["points"] if not any(p is s for s in skip)]
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
        roll, tempo = self.app.roll, self.ed()
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
        got = self.off_mark_at(e.x, e.y)
        if got is not None:  # an arrow to the line out of view: scrolled there
            self._off_click = (time.perf_counter(), e.x, e.y)
            return self.scroll_to_off(got)
        got = self.limit_arrow_at(e.x, e.y)
        if got is not None:  # an arrow to a Keep timing limit spot out of view: scrolled there
            self._off_click = (time.perf_counter(), e.x, e.y)
            return self.scroll_to_limit(got)
        if self.after_off_click(e):  # (its second click: nothing under it now, hunt 2026-10-29)
            return
        w = self.edges_on()
        if w is not None:  # Jump areas: a press = an edge (or the one near it grabbed), held = it follows
            self.cancel_placing()
            if self.live_line() is not None:
                self.live = None
            self.drag = {"kind": "edge"}
            w.edge_press(e)
            return
        if self.placing:  # adding a catch-up: the yellow area dragged, then the teal one
            if self.placing["step"] == 1:
                kind = "fill1" if self.placing.get("fill") else "catch1"  # (Fill with tempo jumps' area)
                self.drag = {"kind": kind, "x": e.x, "x1": e.x, "b0": roll.x2t(e.x),
                             "shift0": bool(e.state & SHIFT), "span": None}
            else:
                self.place_catch(e)
            return
        live_end = self.live_end_at(e.x, e.y)  # (a line just drawn may end outside the parts)
        if live_end is None and self.point_at(e.x, e.y) is None and self.handle_at(e.x, e.y) is None \
                and self.sig_mark_at(e.x, e.y) is not None:  # (a signature's mark: a double-click opens its box,
            self.drag = None                                    # so the first click draws / moves nothing)
            return
        if self.box.mode == "heard" and live_end is None:  # (Heard tempo: inside the Keep timing parts; a press
            run = self.run_at(roll.x2t(e.x))                # picks the run)
            if run is None and self.box.tool.get() in DRAWS:  # (drawing may start anywhere: only what lands in
                self.confirm()                                 # the parts is kept, user 2026-10-24)
                self.bands = []
                b, v = self.tick_beat(roll.x2t(e.x), free=True), self.snap_bpm(self.y2b(e.y), e)
                self.drag = {"kind": "draw", "b0": b, "v0": v, "b1": b, "v1": v, "x": e.x, "y": e.y}
                return
            if run is None:
                return
            if run[0] is not self.run:
                self.confirm()
                self.run, self.sel, self.part, self.bands = run[0], [], None, []
                self.box.sync()
                self.request_redraw()
        tool = self.box.tool.get()
        self.picked()  # (what's picked still there)
        if live_end is not None:  # the live line's end square: moves that end
            self.drag = {"kind": "live_end", "end": live_end}
            return
        if self.handle_at(e.x, e.y) is None:  # (its bend handle keeps it live; anything else: done with it)
            self.confirm()
        if tool == "select" and e.state & CTRL and self.in_band(e.x):
            self.start_move(e, None, pushed=False)  # (Ctrl+drag inside the Select box: copies, like Domino)
            self.drag["copy"] = True
            return
        if tool == "select" and self.in_band(e.x):  # (inside a kept Select box: the box moves, in time only, even
            return self.start_move(e, None, pushed=False)  # pressed on a point: hunt 2026-10-29, BPMs moved)
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
        edge = self.edge_at(e.x)
        if edge is not None:  # the yellow area's left edge / the teal area's right edge
            kind, j = edge
            self.bands = []
            self.set_sel([tempo["catch"][j]])
            self.drag = {"kind": kind, "item": tempo["catch"][j], "x": e.x, "pushed": False}
            return
        b, v = self.tick_beat(roll.x2t(e.x), free=tool != "select"), self.snap_bpm(self.y2b(e.y), e)
        if tool == "select":
            beat = roll.x2t(e.x)
            if self.in_band(e.x) and not e.state & CTRL:
                return self.start_move(e, None, pushed=False)  # (inside a box: all they picked, in time)
            ctrl = bool(e.state & CTRL)  # (Ctrl: another box, added to the ones kept, user 2026-10-22)
            was = (list(self.bands), self.part)  # (Ctrl+Z while the box is dragged: back to these, end_drag)
            self.part, more, self.bands = None, self.bands if ctrl else [], []
            self.drag = {"kind": "box", "x": e.x, "x1": e.x, "b0": beat, "shift0": bool(e.state & SHIFT),
                         "span": None, "before": list(self.sel), "ctrl": ctrl, "more": more, "was": was}
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
        pts = self.ed()["points"]
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
        # (every catch-up's area as it is now: a move stops before one runs into another's or loses its yellow
        # area, user / hunt 2026-10-24)
        cs = self.ed()["catch"]
        self.drag["zones"] = {id(cs[j]): (min(st["from"], st["line"]), st["line"], st["end"]) for j, (st, _) in
                              enumerate(plans(self.ed(), self.base(), self.app.ppq)) if st["end"] is not None}
        # (a Select box's stretch moved: the heard tempo lines of its points go along, hunt 2026-10-24; only when it
        # holds the whole Keep timing part, else the heard line stays on its beats: one point moved inside a part
        # shifted that point's copy only, splitting the part, hunt 2026-10-24)
        moving = [s for s, _, v in self.drag["orig"] if v is not None] + [m for m, _ in self.drag["mates"]]
        got = ids(moving)
        whole = {id(q) for _, members, _, _ in self.runs() if all(id(q) in got for q in members) for q in members}
        self.drag["heards"] = [(s, [q[0] for q in heard_line(s)]) for s in moving
                               if id(s) in whole and heard_line(s)] if band else []

    def copy_drag(self, d):
        """A Ctrl+drag's first move: copies of what the Select box holds are what moves (the originals stay)."""
        self.app.push_undo(name=tr("tempo.step_copy_drag"))
        d["pushed"] = True
        # (let go: laid in from this like a paste, hunt 2026-10-22; the copies below only show it while dragged)
        d["clip"] = self.clip_of(spans=self.bands) if self.bands else None
        tempo, pairs = self.ed(), []
        for s, b, v in d["orig"]:
            c = copy.deepcopy(s)
            tempo["points" if v is not None else "catch"].append(c)
            pairs.append((s, c))
        swap = {id(s): c for s, c in pairs}
        d["orig"] = [(swap[id(s)], b, v) for s, b, v in d["orig"]]
        d["starts"] = [(swap[id(s)], k, a) for s, k, a in d["starts"]]
        d["heards"] = [(swap[id(s)], bs) for s, bs in d["heards"] if id(s) in swap]
        d["copies"] = [c for _, c in pairs]
        self.sel = list(d["copies"])
        for key in ("points", "catch"):
            tempo[key].sort(key=lambda it: it[0])

    def copies_at(self, d):
        """A Ctrl+drag let go now: (its clip, BPM moved along; the beat it's laid from), or None (not moved)."""
        dv = next((s[1] - v for s, _, v in d["orig"] if v is not None), 0.0)
        clip = d.get("clip")
        if not (self.bands and clip and (abs(d["db"]) > 1e-12 or abs(dv) > 1e-12)):
            return None
        clip = dict(clip, points=[[p[0], clamp_bpm(p[1] + dv)] + p[2:] for p in clip["points"]])
        return clip, joined(d["band"])[0][0] + d["db"]

    def copies_preview(self):
        """While a Ctrl+drag is held: the line as letting go gives it (hunt 2026-10-24: the copies drawn over the
        old points looked like catch-ups merging, then jumped when let go): (that line, the copies in it), else
        None."""
        d = self.drag
        if not d or d["kind"] != "move" or d.get("copies") is None:
            return None
        laid = self.copies_at(d)
        if laid is None:  # (back on its start: no copies, the box drawn where it is, hunt 2026-10-24)
            d.pop("laid", None)
            return None
        tempo, cop = self.ed(), ids(d["copies"])
        work = dict(tempo, **{key: [copy.deepcopy(it) for it in tempo[key] if id(it) not in cop]
                              for key in ("points", "catch")})
        new_p, new_c, spans, _ = self.lay_copies(work, *laid, self.ed_base())
        d["laid"] = spans  # (the box drawn there: moved past a teal area as letting go moves it, draw_drag)
        return work, new_p + new_c

    def is_point(self, item):
        return any(item is p for p in self.ed()["points"])

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
        if d["kind"] == "edge":
            w = self.edges_on()
            if w is not None:
                w.edge_drag(e)
            return self.show_position(e)
        if d["kind"] == "live_end":
            if self.live_line() is not None:
                self.live_end_to(d["end"], e)
            return self.show_position(e)
        if d["kind"] == "draw":
            d["b1"], d["v1"] = self.tick_beat(roll.x2t(e.x), free=True), self.snap_bpm(self.y2b(e.y), e)
            d["moved"] = d.get("moved") or abs(e.x - d["x"]) >= 3 or abs(e.y - d["y"]) >= 3  # (less: a click)
            self.show_position(e)
            return self.request_redraw()
        if d["kind"] == "catch1":
            d["x1"] = e.x
            d["span"] = self.box_span(d, e)
            room = self.room(d["b0"]) if d["span"] else None  # (stops dead at another catch-up's edge, user)
            if d["span"] and room is not None:
                lo, hi = max(d["span"][0], room[0]), min(d["span"][1], room[1])
                d["span"] = (lo, hi) if hi - lo > 1e-9 else None
            elif d["span"]:
                d["span"] = None
            return self.request_redraw()
        if d["kind"] == "fill1":  # (Fill with tempo jumps' area: anywhere, catch-ups too: they're replaced)
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
            if live is not None and kind == "part" and self.ed()["points"][i] is live["first"]:
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
            room = self.room(c[0], skip=c)  # (not into another catch-up's area: stops at its edge, user)
            if room is not None:
                b = min(max(b, room[0]), room[1])
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
        roll, tempo = self.app.roll, self.ed()
        one = 1.0 / max(1, self.app.ppq)
        # (a grabbed point / line goes to the mouse itself, user 2026-10-22: with Shift pressed later it sat off
        # the pencil by where the snapped press was; the box keeps its distance)
        at = roll.x2t(e.x) if d["grab"] is not None else d["ref"] + roll.x2t(e.x) - d["press"]
        held = [s for s, _, _ in d["orig"]] + [s for s, _ in d.get("mates", ())]
        if "to" in d:  # (a place typed in the double-click box: exactly there, BPM kept)
            db = d["to"] - d["ref"]
        else:
            db = (self.move_beat(at, e, held) if d["grab"] is not None else self.snap_beat(at, e)) - d["ref"]
        db = max([db] + [-b for _, b, _ in d["orig"]] + [-lo for lo, _ in d["band"] or ()])  # (not before 0)
        # (a grabbed point / line: BPM too, Select as well, user 2026-10-22; the Select box: time only, like Domino,
        # user 2026-10-24)
        if "to" in d:
            dv = 0.0
        elif d["v0"] is not None:
            dv = self.snap_bpm(self.y2b(e.y), e) - d["v0"]
        elif d["band"]:
            dv = 0.0
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
            for s, bs in d.get("heards", ()):  # (heard tempo lines along with a Select box)
                for q, b in zip(heard_line(s), bs):
                    q[0] = max(0.0, b + shift)
            # (a line dragged past its own hand-set edge: the edge stays where it was, unused until the line is
            # back; user 2026-10-24: it went automatic for good, a Keep timing part before it fell into the area)
            order = d.setdefault("order", {})  # (two on one beat: in the order they last had where it worked, so
            for key in ("points", "catch"):    # going back there gives the same line, hunt 2026-10-24)
                tempo[key].sort(key=lambda it: (it[0], order.get(id(it), 0)))

        moved ={id(s) for s, _, _ in d["orig"]} | {id(s) for s, _ in d.get("mates", ())}

        def clash():
            """Two on one beat where something moved: the time stays where it last worked. Points: a jump's two
            may share exactly one beat, never three; one moved onto another (from the left: it arrives there, the
            list's order the sort keeps) lands exactly on its beat. Points not moved are never touched (hunt
            2026-10-22: two a hair apart got joined, three stopped every drag). Heard tempo: never out of the part."""
            span = self.run_span()
            if span and any(id(p) in moved and not span[0] - 1e-9 <= p[0] <= span[1] + 1e-9 for p in tempo["points"]):
                return True
            zs0 = d.get("zones")
            for c in tempo["catch"] if zs0 else ():  # (a moved line inside another's area as it was at the press,
                # with or without an end of its own: it took that one's end over, hunt 2026-10-24)
                if id(c) in moved and any(oid != id(c) and oid not in moved and oa + 1e-9 < c[0] < oe - 1e-9
                                          for oid, (oa, _, oe) in zs0.items()):
                    return True
            if zs0:  # (a catch-up's area running into another's as they were at the press, or one losing its
                tempo_edited()  # yellow area: it stops where it last fitted, user / hunt 2026-10-24; points too)
                after = dict(tempo, points=[list(p) for p in tempo["points"]])  # (as it'll be when let go: a
                drop_loose(after["points"])                                       # point left on another tidied)
                for j, (st, _) in enumerate(plans(after, self.base(), self.app.ppq)):
                    if st["end"] is None:
                        continue
                    cid, a, e = id(tempo["catch"][j]), min(st["from"], st["line"]), st["end"]
                    was = zs0.get(cid)
                    if was and was[0] < was[1] - 1e-9 and a >= st["line"] - 1e-9 and cid not in moved:
                        return True
                    for oid, (oa, _, oe) in zs0.items():
                        if oid != cid and a < oe - 1e-9 and oa < e - 1e-9 and not (
                                was and was[0] < oe - 1e-9 and oa < was[2] - 1e-9):  # (overlapping before: as is)
                            return True
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
            d["order"] = {id(it): k for key in ("points", "catch") for k, it in enumerate(tempo[key])}
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
        tempo = self.ed()
        return [it for it in tempo["points"] + tempo["catch"] if lo - 1e-9 <= it[0] <= hi + 1e-9]

    def in_spans(self, spans):
        """The points and catch-up lines in any of spans, each once, in the lists' order."""
        tempo = self.ed()
        return [it for it in tempo["points"] + tempo["catch"]
                if any(lo - 1e-9 <= it[0] <= hi + 1e-9 for lo, hi in spans)]

    def bands_reach(self):
        """(first, last beat) the kept Select boxes cover together."""
        return min(lo for lo, _ in self.bands), max(hi for _, hi in self.bands)

    def on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        roll = self.app.roll  # (the ruler's tempo marks, kept as they were while a big tempo map was dragged)
        self.after_idle(lambda: roll.draw_tempo_marks(roll.kept_box() or []))
        live = self.live_line()
        if drop_loose(self.ed()["points"]):  # (a jump's first point the drag left with nothing gliding in)
            self.app.tempo_changed()
        if live is not None and d["kind"] in ("live_end", "bend"):
            self.live_seen()
        if d["kind"] == "edge":
            w = self.edges_on()
            if w is not None:
                w.edge_release()
        elif d["kind"] == "box":
            if d["span"] is None:  # a click: the part there (or nothing), the play line there (user 2026-10-21)
                part = self.part_at(self.app.roll.x2t(e.x))
                pts = self.ed()["points"]
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
            if d["span"] is not None and not self.catch_free(*d["span"]):  # (over another catch-up: called off)
                self.cancel_placing()
            elif d["span"] is not None:  # (a click does nothing: it waits for a drag)
                self.placing = {"step": 2, "a": d["span"][0], "b": d["span"][1], "hover": None}
                self.box.hint.config(text=tr("tempo.hint_catch2"))
            self.request_redraw()
        elif d["kind"] == "fill1":
            if d["span"] is not None:  # (the area = a kept Select box; a click waits for a drag)
                self.placing, self.bands, self.sel, self.part = None, [d["span"]], [], None
                self.box.pick_tool_hint()
                self.request_redraw()
                open_melody(self.app)
        elif d["kind"] in ("start", "end", "bend"):
            if d["pushed"]:
                self.app.settle_history()
        elif d["kind"] == "move":
            if any(v is None for _, _, v in d["orig"]):  # (a catch-up line let go: its edges' thick lines go)
                self.request_redraw()
            if d.get("copies") is not None:  # (Ctrl+drag let go: the copies replace what was where they landed,
                # as a paste does; not moved = no copies)
                tempo, cop = self.ed(), ids(d["copies"])
                laid = self.copies_at(d)
                for key in ("points", "catch"):  # (the copies only showed it: laid in like a paste, hunt 2026-10-22)
                    tempo[key][:] = [it for it in tempo[key] if id(it) not in cop]
                if laid is not None:
                    self.put_copies(*laid, None, push=False)
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
        if d["kind"] not in ("box", "view", "catch1", "fill1"):
            self.app.let_go()  # (Hz bass "Tempo changed" in the side panel / Hz window, hunt 10-27)

    def move_line(self, e):
        """A Select click: the play line to the click (snapped like the piano roll's); playing: it stays (user
        2026-10-23)."""
        app = self.app
        if not app.player.running:
            app.set_playhead(self.snap_beat(app.roll.x2t(e.x), e))

    def end_drag(self):
        """Ctrl+Z while dragging: let go first (the step made at the first move takes it back). True when it only
        called off something that made no undo step yet (a catch-up being added, a press not moved): Ctrl+Z / Ctrl+Y
        stop there."""
        d, self.drag = self.drag, None
        only = bool(self.placing) or bool(d) and d["kind"] in ("box", "draw", "catch1", "fill1", "view") or \
            bool(d) and d.get("pushed") is False
        self.cancel_placing()
        if d and d["kind"] == "edge" and self.edges_on() is not None:  # (Ctrl+Z on: the chain's last edge goes;
            ch = self.edges_on().live_chain()  # an edge only pressed, nothing added / moved: just let go, hunt)
            only = only or ch is not None and not ch.moved
            self.edges_on().edge_release()
        if d and d["kind"] == "box":  # (the picks and boxes from before the press, hunt 10-27)
            self.sel = list(d["before"])
            self.bands, self.part = d["was"]
            self.box.sync()
        if d:
            self.request_redraw()
        return only

    def on_double(self, e):
        """Double-click on a point: type its BPM. Elsewhere: a press like any other."""
        roll = self.app.roll
        if roll.sx is None or self.view is None or e.x < roll.kb_w:
            return
        if self.off_mark_at(e.x, e.y) is not None or self.limit_arrow_at(e.x, e.y) is not None or \
                self.after_off_click(e):  # (a double-click on an out-of-view
            return  # arrow only scrolls: the 2nd click drew a point / pasted the copy there, hunt 2026-10-29)
        if self.edges_on() is not None:  # (Jump areas: a press like any other, never a point's box)
            return self.on_press(e)
        i = self.point_at(e.x, e.y)
        n = self.handle_at(e.x, e.y) if i is None else None
        if n is not None:  # (the bend handle: straight / the catch-up's own curve again)
            self.drag = None
            return self.unbend(n)
        mark = self.sig_mark_at(e.x, e.y) if i is None else None
        if mark is not None:  # (a time / key signature: its box, user 2026-10-26)
            self.drag = None
            return self.edit_time_sig(mark[0][0]) if mark[1] == 0 else self.edit_key_sig(mark[0][0], mark[0])
        if i is None and self.box.tool.get() == "select" and getattr(self.app, "tempo_clip", None) \
                and self.catch_at(e.x) is None:  # (Select double-click on empty space: the copy there, like the
            self.drag = None                     # piano roll's; snapped like it, Shift free; user 2026-10-22)
            self.app.key_pane = self
            return self.paste(self.snap_beat(roll.x2t(e.x), e))
        if i is None:
            return self.on_press(e)
        self.drag = None
        self.open_typing(i)

    def glide_end(self, b, pts=None):
        """The lone point on beat b where a curved part arrives (a second point may go on its beat: a jump), or
        None. pts: this line (else the one edited)."""
        pts = self.ed()["points"] if pts is None else pts
        on = [i for i, p in enumerate(pts) if abs(p[0] - b) < 1e-9]
        if len(on) != 1 or on[0] == 0:
            return None
        p, q = pts[on[0] - 1], pts[on[0]]
        return q if p[2] != "hold" and p[1] != q[1] else None

    def add_point(self, b, v):
        """A new point (picked), like a Domino click (user 2026-10-24, its pictures): a HELD step that changes only
        its own bit. Inside a ramp / curve: the part stays exactly as it was around it, the click lasts up to the
        part's next Interval step (one held value, as Domino's one event among a ramp's many), then the part goes
        on as before; over a held tempo (or past the last point): it holds up to the next point. Its Keep timing /
        heard line go along. On the beat of a point where a curve arrives: a second point there, the tempo from
        there on (user 2026-10-22: a jump right where a glide ends). On another point's beat: that point (a jump's
        leaving one) takes the clicked BPM, its ramp kept (user 2026-10-24, like Domino setting the event on that
        tick). None when nothing changes."""
        pts = self.ed()["points"]
        end = self.glide_end(b)
        if end is not None and abs(end[1] - clamp_bpm(v)) < 1e-9:
            return None
        if end is None and any(abs(p[0] - b) < 1e-9 for p in pts):
            item = [p for p in pts if abs(p[0] - b) < 1e-9][-1]
            if abs(item[1] - clamp_bpm(v)) < 1e-9:
                return None
            self.app.push_undo(name=tr("tempo.step_bpm"))
            item[1] = clamp_bpm(v)
            drop_loose(pts)
            self.sel = [item] if any(p is item for p in pts) else []
            self.part = None
            self.app.tempo_changed()
            return item
        self.app.push_undo(name=tr("tempo.step_add"))
        before = [p for p in pts if p[0] < b]
        after = [p for p in pts if p[0] > b]
        if end is None and before and after:  # (inside a part: it stays exactly as it was around the click)
            owner, nxt = before[-1], after[0]
            if owner[2] != "hold" and owner[1] != nxt[1]:  # (a ramp: the click lasts up to its next step only)
                ppq, every = max(1, self.app.ppq), max(1, int(self.ed().get("every", EVERY)))
                a, t = math.ceil(owner[0] * ppq - 1e-9), round(b * ppq)
                back = (a + ((t - a) // every + 1) * every) / ppq
                if back < nxt[0] - 1e-9:
                    split_at(pts, back, self.ed_base())
            split_at(pts, b, self.ed_base())
        at = sum(1 for p in pts if p[0] < b + 1e-9)
        kind = part_kind(pts[at - 1] if at > 0 else None, self.box.draw_scale)
        kind[0], kind[1], kind[4] = "hold", "", list(BEND)
        if end is not None:  # (a jump: the ramp leaving there goes on from the new BPM, user 2026-10-24)
            kind = copy.deepcopy(end[2:])
            end[2], end[3], end[6] = "hold", "", list(BEND)
        item = [float(end[0] if end is not None else b), clamp_bpm(v), *kind]
        pts.insert(at, item)
        drop_loose(pts)  # (the cut point is an arrival only where a ramp comes in)
        self.sel, self.part = [item], None
        self.app.tempo_changed()
        return item

    def draw_part(self, d):
        """Linear / Curve / Formula let go: a part from the press to here (the points between it replaced)."""
        b0, v0, b1, v1 = d["b0"], d["v0"], d["b1"], d["v1"]
        if b1 < b0:
            b0, v0, b1, v1 = b1, v1, b0, v0
        kind = DRAWS[self.box.tool.get()]
        text = self.box.draw_formula if kind == "formula" else ""
        if not d.get("moved") or b1 - b0 < 0.5 / max(1, self.app.ppq):  # (a click: a point, user 2026-10-22)
            b0, v0 = d["b0"], d["v0"]
            if self.box.mode != "heard" or self.run_at(b0) is not None:  # (Heard tempo: inside the parts only)
                self.add_point(b0, v0)
            return
        if self.box.mode == "heard":  # (drawn anywhere: each Keep timing part it crosses keeps its own stretch,
            # cut exactly where the part starts / ends by write_heard; the rest is dropped, user 2026-10-24)
            got = [r[0] for r in self.runs() if r[2] < b1 - 1e-9 and (r[3] is None or r[3] > b0 + 1e-9)]
            if not got:
                return self.request_redraw()
            if len(got) > 1:  # (several parts: laid in each at once, in one undo step; not live)
                self.app.push_undo(name=tr("tempo.step_draw"))
                for p in got:
                    self.run, self.sel, self.part, self._ed = p, [], None, None
                    self.lay_part(b0, v0, b1, v1, kind, text)
                self.box.sync()
                return
            if got[0] is not self.run:
                self.run, self.sel, self.part, self.bands = got[0], [], None, []
                self.box.sync()
        pts = self.ed()["points"]
        self.app.push_undo(name=tr("tempo.step_draw"))
        base = copy.deepcopy(pts)
        first = self.lay_part(b0, v0, b1, v1, kind, text)
        # LIVE until Enter / Esc / a click elsewhere (user 2026-10-22, like the velocity pane): its end squares
        # move it, laid again over the points as they were before it (shrunk: the covered ones come back)
        self.live = {"base": base, "ends": [[b0, v0], [b1, v1]], "first": first}
        self.live_seen()

    def lay_part(self, b0, v0, b1, v1, kind, text, like=None):
        """A part from (b0, v0) to (b1, v1) over the points there (they're replaced), picked; like: a first point
        whose curve / formula / scale / keep / bend it takes. Returns its first point."""
        pts = self.ed()["points"]
        first = self.laid(pts, b0, v0, b1, v1, kind, text, self.ed_base(), like, True)[1]
        self.sel, self.part = [], first
        self.app.tempo_changed()
        return first

    def laid(self, pts, b0, v0, b1, v1, kind, text, base, like=None, inplace=False):
        """lay_part's points: (the line pts with the part laid in, its first point); a copy unless inplace (a drag
        shows what letting go will give, user 2026-10-24). base: the line's BPM before its first point.
        THE PART OVERRIDES ONLY ITS OWN STRETCH, like Domino (user 2026-10-24: the parts before / after were bent
        to meet it): the line before it stays exactly (a jump at its start where they differ); after it an old
        ramp / curve goes on from where it was (a jump back), an old held tempo is replaced by its end value up to
        the next point (Domino's steps do that). Drawn from / up to a jump: its arrival / its leaving tempo stays
        (lay_points' edges)."""
        if b1 < b0:
            b0, v0, b1, v1 = b1, v1, b0, v0
        work = pts if inplace else copy.deepcopy(pts)
        held = [p for p in work if p[0] <= b0 + 1e-9]
        first = [float(b0), clamp_bpm(v0), kind, text, self.box.draw_scale, bool(held) and keeps(held[-1]),
                 list(BEND)]
        if first[5] and heard_of(held[-1]):  # (inside a Keep timing run: its heard line along)
            first.append(copy.deepcopy(held[-1][7]))
        if like is not None:
            first[2:] = copy.deepcopy(like[2:])
        under = [p for p in work if p[0] <= b1 + 1e-9]  # (the part the line's end lands in)
        owner = under[-1] if under else None
        on_end = owner is not None and abs(owner[0] - b1) < 1e-9  # (a point of its own there: it plays on)
        steady = owner is None or owner[2] == "hold" or not any(p[0] > b1 + 1e-9 for p in work)
        kind_after = part_kind(owner, self.box.draw_scale)
        kind_after[0], kind_after[1], kind_after[4] = "hold", "", list(BEND)
        last = [float(b1), clamp_bpm(v1), *kind_after]
        # (where Keep timing starts / ends / changes heard line under it: a point stays there, on the line, with
        # what was there; a line across a part's end made the part reach the line's end, hunt 2026-10-24)
        was, edges = (first[5], heard_line(first)), []
        for p in work:
            if b0 + 1e-9 < p[0] < b1 - 1e-9 and (keeps(p), heard_line(p)) != was:
                was = keeps(p), heard_line(p)
                edges.append((p[0], keeps(p), copy.deepcopy(p[7]) if keeps(p) and heard_of(p) else None))
        had = ids(work)
        self.lay_points(work, [first, last], [(b0, b1)], base)
        if steady and not on_end and any(p is last for p in work):  # (an old held tempo: the end value holds on
            # to the next point; the same value as there = last already gone, nothing to do)
            work[:] = [p for p in work if not (abs(p[0] - b1) < 1e-9 and id(p) not in had and p is not last)]
            drop_loose(work)
        for x, keep, heard in edges:
            p = split_at(work, x, base)
            p[5] = keep
            del p[7:]
            if heard is not None:
                p.append(heard)
        return work, first

    # ------------------------------------------------------------ the line just drawn, live (user 2026-10-22)

    def live_line(self):
        """The line just drawn while it can still change (nothing else changed the tempo since), else None."""
        lv = self.live
        if lv is not None and tempo_text(self.ed()) != lv["seen"]:
            self.live = lv = None
        return lv

    def live_seen(self):
        """The live line's own change done: the tempo as it is now is still its."""
        if self.live is not None:
            tempo_edited()  # (its own change may not have said so yet)
            self.live["seen"] = tempo_text(self.ed())

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
        # (Heard tempo: may go past the part's ends, cut off there afterwards, as drawn)
        b = self.move_beat(self.app.roll.x2t(e.x), e, beats=[p[0] for p in lv["base"]] + [
            x for x in self.run_span() or () if x != math.inf])
        ends = [list(p) for p in lv["ends"]]
        ends[k] = [b, self.snap_bpm(self.y2b(e.y), e)]
        if abs(ends[0][0] - ends[1][0]) < 0.5 / max(1, self.app.ppq):
            return
        lv["ends"] = ends
        old = lv["first"]
        self.ed()["points"][:] = copy.deepcopy(lv["base"])
        (b0, v0), (b1, v1) = ends
        lv["first"] = self.lay_part(b0, v0, b1, v1, old[2], old[3], like=old)
        self.live_seen()
        self.app.changed_in_step()  # (the line's own step: after a save, unsaved now, hunt 10-27)

    def dense_key(self):
        app = self.app
        return tempo_text(app.tempo), self.base(), app.ppq, app.beats, id(app.rendered), id(app.loaded)

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
        lm, loose = app.loaded, None
        if lm is not None and any(len(p) > 5 and p[5] is True for p in app.tempo["points"]):
            if lm._anchors is None:  # (a loaded MIDI's note ticks: one read of the file, on a thread)
                self.read_anchors(lm)
                return
            # (its export writes the file's notes and CC messages + the shapes' (Load MIDI step 10), as there)
            cc = app.cc_messages(app.ppq, self.base())
            ticks = sorted_union([lm._anchors[0], ticks.astype(np.int64)])  # (= np.union1d, quicker when sorted)
            loose = sorted_union([lm._anchors[1], np.array([m[0] for ms in cc.values() for m in ms], np.int64)])
        limits = {"dense": [], "gave": [], "unfit": [], "moved": []}
        if any(len(p) > 5 and p[5] is True for p in app.tempo["points"]):
            try:
                heard = file_tempos(lm) if lm is not None else None  # (a loaded MIDI: its notes keep their file time)
                if loose is None:  # (CC messages: they give way a tick, said in orange)
                    cc = app.cc_messages(app.ppq, self.base())
                    loose = np.array([m[0] for ms in cc.values() for m in ms], np.int64)
                limits = limit_spots(app.tempo, self.base(), app.ppq, app.beats, ticks, heard, loose)
            except MemoryError:
                pass
        self._limits = {k: sorted(set(v)) for k, v in limits.items()}
        self._dense = (key, self._limits["dense"])
        self.request_redraw()

    def read_anchors(self, lm):
        """The loaded MIDI's note ticks read on a thread (Keep timing's "notes too close" lines need them); the
        lines are worked out once they're in."""
        if getattr(lm, "_anchor_job", None) is None:
            job = lm._anchor_job = {"done": False}

            def run():
                try:
                    lm.anchors()
                except Exception:  # (file changed / gone: no lines, the export says why)
                    pass
                job["done"] = True
            threading.Thread(target=run, daemon=True, name="loaded note ticks").start()
        if lm._anchor_job["done"]:
            lm._anchor_job = None
            if lm._anchors is None:
                return
            self._dense_job = self.after_idle(self.work_dense)
        else:
            self._dense_job = self.after(100, lambda: self.read_anchors(lm))

    def edges_on(self):
        """The Fill with tempo jumps window while "Where I click" is picked (Jump areas: clicks here make edges),
        else None."""
        w = getattr(self.app, "melody_window", None)
        return w if w is not None and w.edge_window() else None

    def confirm(self):
        """Done with the live line (Enter, Esc, a click elsewhere): its squares go; a Jump areas chain too. True if
        there was one."""
        w = self.edges_on()
        if w is not None and w.finish_chain():
            return True
        if self.live_line() is None:
            self.live = None
            return False
        self.live = None
        self.request_redraw()
        return True

    def add_catch(self, b):
        cs = self.ed()["catch"]
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
        w = self.edges_on()
        if w is not None and w.finish_chain():  # (Jump areas: right-click = the chain done)
            return
        self.app.key_pane = self
        if self.box.mode == "heard":  # (another part's heard line: that one picked first)
            run = self.run_at(roll.x2t(e.x))
            if run is not None and run[0] is not self.run:
                self.confirm()
                self.run, self.sel, self.part, self.bands = run[0], [], None, []
                self.box.sync()
        self.picked()
        n = self.handle_at(e.x, e.y)
        if n is not None:  # (the bend handle: its own menu)
            return self.handle_menu(e, n)
        tempo = self.ed()
        i, j = self.point_at(e.x, e.y), self.catch_at(e.x)
        item = tempo["points"][i] if i is not None else tempo["catch"][j] if j is not None else None
        pin = self.pin_at(e.x) if item is None else None
        if pin is not None:  # (a thick line: forget it, hunt 2026-10-24)
            return self.pin_menu(e, *pin)
        on_edge = item is None and self.edge_at(e.x) is not None
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
        tempo = self.ed()
        menu = tk.Menu(self, tearoff=0)
        def sep():  # (a line between groups, never on top: user 2026-10-24 saw a stray one)
            if menu.index("end") is not None:
                menu.add_separator()
        on_mark = item is None and self.sig_menu_items(menu, e)  # (time / key signatures: Add here, or the mark's)
        zone = self.zone_at(e.x) if item is None and not on_mark else None
        if not on_mark and self.box.mode == "visual" and zone is None:  # (catch-ups: the visual tempo's only; never
            sep()                                                       # inside another one's area, user)
            menu.add_command(label=tr("tempo.add_catch"), command=self.start_placing)
        if not on_mark and item is None and self.box.mode == "visual":  # (tempo jumps that follow the melody,
            menu.add_command(label=tr("melody.menu"), command=self.start_fill)  # PLAN #12)
        edge =self.edge_at(e.x) if item is None else None
        worked = plans(tempo, self.base(), self.app.ppq) if edge else []
        for k, kind, key in ((3, "start", "tempo.catch_auto"), (4, "end", "tempo.catch_end_auto")):
            n = edge[1] if edge and edge[0] == kind else None
            # (an edge set by hand and used: a waiting one has its own thick line's menu, hunt 2026-10-24)
            if n is not None and worked[n][0]["manual" if k == 3 else "manual_end"]:
                sep()
                menu.add_command(label=tr(key), command=lambda c=tempo["catch"][n], k=k: self.auto_edge(c, k))
        if zone is not None:  # (anywhere in its yellow / teal area, user 2026-10-24: the line was hard to hit)
            sep()
            menu.add_command(label=tr("tempo.delete_catch"),
                             command=lambda c=tempo["catch"][zone]: self.delete_items([c]))
        if item is not None:  # (user: right-click doesn't delete; Delete picked ones, or this menu)
            gone = self.sel if self.is_sel(item) else [item]
            sep()
            menu.add_command(label=tr("tempo.delete"), command=lambda: self.delete_items(gone))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            # (Windows: the menu waits here until it closes; the click outside that closed it then comes to the
            # pane too: it only closes the menu, adds no point, user 2026-10-23)
            self.menu_shut = time.perf_counter()

    def pin_menu(self, e, c, k):
        """Right-click on a thick line (an edge set by hand, waiting): Forget this edge."""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("tempo.forget_pin"), command=lambda: self.forget_pin(c, k))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            self.menu_shut = time.perf_counter()  # (see right_menu)

    def auto_edge(self, c, k):
        """A catch-up's yellow start (k = 3) / teal end (k = 4) back to where Spiderweb puts it (one undo step)."""
        if catch_get(c, k) is not None:
            self.app.push_undo(name=tr("tempo.step_catch_start" if k == 3 else "tempo.step_catch_end"))
            catch_set(c, k, None)
            self.app.tempo_changed()

    def delete_items(self, gone, name=None):
        """These points / catch-up lines go (one undo step, name: its name instead of Delete's)."""
        tempo = self.ed()
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
        tempo = self.ed()
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
    # start the tempo leaving it, at the end the tempo arriving. A paste changes ONLY the stretch inside: the
    # target is cut at the edges too, the point arriving at the start and the one the part after the end leaves
    # from stay; a pasted part leaves from / arrives at them (a jump when they differ). Cut / Delete with a box
    # only take the points out (empty_spans, user 2026-10-26). Touching / overlapping boxes count as one.

    def clip_of(self, spans=None, items=None):
        """What Ctrl+C keeps: {"length", "points", "catch", "spans"}, beats from the copy's start. spans: boxes
        (the line cut at their edges, every catch-up inside); items: picked points / catch-up lines (their own
        first .. last beat). None when nothing's there."""
        tempo, base = self.ed(), self.ed_base()
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
            for k in (5, 6):
                catch_set(item, k, catch_get(c, k))
            clip_c.append(item)
        out = [[p[0] - lo] + copy.deepcopy(p[1:]) for p in pts]
        for p in out:  # (heard tempo lines along, from the copy's start too)
            shift_heard(p, -lo, None)
        return {"length": spans[-1][1] - lo, "points": out, "catch": clip_c,
                "spans": [(a - lo, b - lo) for a, b in spans]}

    def copy(self):
        """Ctrl+C after clicking the pane: the Select boxes (or what's picked, or the part picked: its two
        points), each kept as its distance from the copy's start, so a paste keeps the gaps."""
        pts, pick = self.ed()["points"], self.picked()
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
        self.app.drop_own_copy()  # (Spiderweb's older copy off the clipboard, like a stroke copy: hunt 2026-10-29)
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
        """Everything inside the boxes goes (one undo step), like deleting tempo events in Domino (user 2026-10-26:
        a point came back at the box's end): the tempo arriving at each box's start holds on to the next point
        after it. A curved part running into the box is cut on its start first, so the line before stays as it
        was (Ctrl+X / Delete with a box)."""
        tempo, base = self.ed(), self.ed_base()
        spans = [(lo, hi) for lo, hi in joined(spans) if hi - lo > 1e-9]
        if not spans:
            return
        self.app.push_undo(name=name)
        tempo["catch"][:] = [c for c in tempo["catch"] if not any(lo - 1e-9 <= c[0] < hi - 1e-9 for lo, hi in spans)]
        pts = tempo["points"]
        for lo, hi in spans:
            if not any(lo - 1e-9 <= p[0] < hi - 1e-9 for p in pts):
                continue
            keep = None
            before = [p for p in pts if p[0] < lo - 1e-9]
            if before and before[-1][2] != "hold":  # (a curve arriving at / crossing the start: held from there)
                on = [p for p in pts if abs(p[0] - lo) < 1e-9]
                keep = on[0] if on else split_at(pts, lo, base)
                keep[2:4] = ["hold", ""]
            pts[:] = [p for p in pts if p is keep or not lo - 1e-9 <= p[0] < hi - 1e-9]
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
        self.put_copies(clip, self.tick_beat(at, skip=self.ed()["points"]), tr("tempo.step_paste"))

    def put_copies(self, clip, at, name, push=True, stay=False, teal=True):
        """clip's items placed from beat at, replacing what's in its boxes there (push: its own undo step). A clip
        of catch-up lines only leaves the points alone. stay: its first point always stays (lay_points). teal:
        never starting inside a catch-up's teal area (lay_copies)."""
        if push:
            self.app.push_undo(name=name)
        new_p, new_c, spans, note = self.lay_copies(self.ed(), clip, at, self.ed_base(), stay, teal)
        if note:
            self.app.status.config(text=tr(note))
        self.bands, self.sel, self.part = [(lo, hi) for lo, hi in spans if hi > lo], new_p + new_c, None
        self.app.tempo_changed()

    def lay_copies(self, tempo, clip, at, base, stay=False, teal=True):
        """put_copies' work on the line tempo (in place; a throwaway copy shows a Ctrl+drag as letting go gives it,
        hunt 2026-10-24): (the new points kept, the new catch-ups, the boxes' stretches, a status text key or
        None). base: the line's BPM before its first point. teal: a start inside a catch-up's teal area moves to
        that area's end (user 2026-10-24: the copy replaced its end point, the catch-up ran on to the copy's line;
        never for a Domino paste, which stays with its notes)."""
        one, note = 1.0 / max(1, self.app.ppq), None
        for _ in range(len(tempo["catch"]) + 1) if teal else ():
            hit = next(((st, c) for st, c in zip(stretches(tempo, self.base()), tempo["catch"])
                        if st["end"] is not None and st["line"] + 1e-9 < at <= st["end"] + 1e-9), None)
            if hit is None:
                break
            st, c = hit
            if at < st["end"] - 1e-9:
                at, note = st["end"], "tempo.paste_after_catch"
            if catch_get(c, 4) is None and clip["points"]:  # (it ended on the point the copy replaces: its end
                # kept by hand; a copy of catch-up lines only replaces no point, hunt 2026-10-24)
                catch_set(c, 4, st["end"])
            if at >= st["end"] - 1e-9:
                break
        spans = joined([(at + a, at + b) for a, b in clip.get("spans") or [(0.0, clip["length"])]])
        new_p = [[max(0.0, p[0] + at)] + copy.deepcopy(p[1:]) for p in clip["points"]]
        for p in new_p:  # (their heard tempo lines along)
            shift_heard(p, at)
        new_c = []
        cs = clip["catch"]
        if cs and self.box.mode == "heard":  # (catch-ups: the visual tempo's only; left out, hunt 2026-10-24)
            cs, note = [], "tempo.paste_no_catch"
        for c in cs:
            item = [max(one, c[0] + at)] + list(c[1:3])
            for k in (3, 4):
                v = catch_get(c, k)
                catch_set(item, k, None if v is None else max(0.0, v + at))
            for k in (5, 6):
                catch_set(item, k, catch_get(c, k))
            new_c.append(item)
        inside = lambda x: any(lo - 1e-9 <= x <= hi + 1e-9 for lo, hi in spans)
        areas = [(min(st["from"], st["line"]), st["end"]) for st, c in zip(stretches(tempo, self.base()),
                                                                          tempo["catch"])
                 if st["end"] is not None and not inside(c[0])]
        over = [it for it in new_c if any(lo - 1e-9 <= it[0] < hi - 1e-9 for lo, hi in areas)]
        if over:  # (one catch-up per area, as adding one: a copy landing in another's took its job over, user's
            new_c = [it for it in new_c if not any(it is o for o in over)]  # hunt 2026-10-24)
            note = "tempo.paste_catch_overlap"
        kept = [it for it in tempo["catch"] if not inside(it[0])]
        tempo["catch"][:] = sorted(kept + new_c, key=lambda it: it[0])
        if new_p:
            new_p = self.lay_points(tempo["points"], new_p, spans, base, stay)
        return new_p, new_c, spans, note

    def put_domino(self, tempos, at, length, how, push):
        """Tempo changes pasted from Domino ([(beat from the paste's start, BPM)], each held to the next, as in
        Domino) from beat at (project.paste_from_domino). how: "replace" = like a paste of the stretch at ..
        at + length (the tempo round it as it was: back on its end, like Copy to Domino's own copy), "add" = put
        in among the points, each held to the next point (what was there leaving that beat replaced, what
        arrives kept). push: its own undo step (no notes pasted with it). Always into the visual tempo (user
        2026-10-24: the heard line stays as it is)."""
        if self.box.mode != "visual":
            self.box.set_mode("visual")
        new = [[b, bpm, "hold", "", "lin", False, list(BEND)] for b, bpm in tempos]
        if how == "replace":
            clip = {"length": length, "points": new, "catch": [], "spans": [(0.0, length)]}
            self.put_copies(clip, at, tr("project.paste_from_domino"), push, stay=True, teal=False)
            # (nothing picked, no Select box: a whole song pasted looked all selected, user 2026-10-23; the pasted
            # notes are what's selected)
            self.bands, self.sel, self.part = [], [], None
            self.request_redraw()
            return
        if push:
            self.app.push_undo(name=tr("project.paste_from_domino"))
        pts, base = self.ed()["points"], self.base()
        beats = [q[0] for q in pts]  # (in order: only the points round each beat looked at, on_beat)
        if any(x > y for x, y in zip(beats, beats[1:])):
            beats = None
        for p in new:
            p[0] += at
            split_at(pts, p[0], base, beats)
            on = on_beat(pts, p[0], beats)
            if len(on) == 2:
                del pts[on[1]]
                if beats is not None:
                    del beats[on[1]]
            p[0] = pts[on[0]][0]  # (on that beat exactly: a pair)
            pts.insert(on[0] + 1, p)
            if beats is not None:
                beats.insert(on[0] + 1, p[0])
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
                    (prev is not None and prev[2] == "hold" and prev[1] == p[1] and keeps(prev) == keeps(p)
                     and heard_of(prev) == heard_of(p)):
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
        part = self.picked()
        run = None  # (Heard tempo: the run being edited, by its first point's place)
        if self.box.mode == "heard" and self.run is not None:
            run = next((k for k, p in enumerate(self.app.tempo["points"]) if p is self.run), None)
        return json.dumps({"bands": [list(s) for s in self.bands], "points": self.sel_idx("points"),
                           "catch": self.sel_idx("catch"), "part": part[1] if part and part[0] == "part" else None,
                           "heard": run})

    def restore_pick(self, text):
        """An undo step's pick_state back (on the tempo line it brought back; picks made on the other line than
        the one drawn now: nothing picked)."""
        try:
            got = json.loads(text)
            run, pts = got.get("heard"), self.app.tempo["points"]
            if (run is not None) != (self.box.mode == "heard"):
                got = {}
            elif run is not None:
                self.run = pts[run] if 0 <= run < len(pts) and keeps(pts[run]) else None
            tempo = self.ed()
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
            stop = (self.room(self.placing["b"]) or (0, math.inf))[1]  # (up to the next catch-up's area)
            self.placing["hover"] = min(self.snap_beat(roll.x2t(e.x), e), stop)
            self.request_redraw()
        # the pointer + the hint line: only what can be done right here (user 2026-10-22: the whole list was a wall
        # of text)
        hint = None
        handle = self.handle_at(e.x, e.y)
        self.handle_hover(None if self.point_at(e.x, e.y) is not None or self.placing else handle, e)
        w = self.edges_on()
        if e.x < roll.kb_w:
            want, hint = "sb_v_double_arrow", "numbers"
        elif self.off_mark_at(e.x, e.y) is not None:
            want, hint = "hand2", "off_view"
        elif self.limit_arrow_at(e.x, e.y) is not None:
            want, hint = "hand2", "limit_arrow_" + self.limit_arrow_at(e.x, e.y)[1]
        elif w is not None:  # (Jump areas: clicks make edges, near one = drag it)
            ch = w.live_chain()
            want, hint = ("sb_h_double_arrow", "on_area_edge") if ch and ch.edge_near(e.x) is not None else \
                (PENCIL_CURSOR, "areas")
        elif self.placing:
            want = "sb_h_double_arrow"  # (the catch-up being added keeps its own hint)
        elif self.live_end_at(e.x, e.y) is not None:
            want, hint = "fleur", "on_live_end"
        elif self.box.mode == "heard" and self.run_at(roll.x2t(e.x)) is None:
            if self.box.tool.get() in DRAWS:  # (drawing starts anywhere: only the parts keep it)
                want, hint = PENCIL_CURSOR, "heard_out_draw"
            else:
                want, hint = "arrow", "heard_out"  # (faded: nothing to click outside the Keep timing parts)
        elif self.point_at(e.x, e.y) is not None:
            want, hint = "fleur", "on_point"
        elif handle is not None:
            want, hint = "fleur", "on_handle" if handle[0] == "part" else "on_catch_handle"
        elif self.sig_mark_at(e.x, e.y) is not None:  # (a time / key signature, user 2026-10-26: wasn't obvious)
            mark, row = self.sig_mark_at(e.x, e.y)
            want, hint = "hand2", "on_key_sig" if row else "on_time_sig" if mark[0] > 0 else "on_first_sig"
        elif self.catch_at(e.x) is not None:
            want, hint = "sb_h_double_arrow", "on_catch"
        elif self.pin_at(e.x) is not None:  # (a thick line: what it is, hunt 2026-10-24)
            want, hint = "arrow", "on_pin"
        elif self.edge_at(e.x) is not None:  # (which one: the hint says, user 2026-10-24)
            want, hint = "sb_h_double_arrow", "on_" + self.edge_at(e.x)[0]
        elif self.bad_why(e.x) is not None:  # (a catch-up that can't work: why, user 2026-10-24)
            want, hint = PENCIL_CURSOR if self.box.tool.get() != "select" else SELECT_CURSOR, \
                "why_" + self.bad_why(e.x)
        elif any(a <= roll.x2t(e.x) < b for a, b in self.keep_offs()):  # (why the part's Keep timing is off)
            want, hint = PENCIL_CURSOR if self.box.tool.get() != "select" else SELECT_CURSOR, "on_keep_off"
        elif self.limit_at(e.x, e.y) is not None:  # (a limit text / mark: what it means)
            want, hint = PENCIL_CURSOR if self.box.tool.get() != "select" else SELECT_CURSOR, \
                "on_limit_" + self.limit_at(e.x, e.y)
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
        if e.state & CTRL and not e.state & (SHIFT | ALT):  # (the next / previous track, as the piano roll)
            return self.app.track_list.step_track(-1 if up else 1)
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
        """The BPM box over a point, and under it its place as bar:beat:tick (user 2026-10-25: typed too, moves the
        point like a drag would). Enter = both go in, Esc = neither, Tab between them."""
        self.close_typing(True)
        item = self.ed()["points"][i]
        self.set_sel([item])
        b, v = item[:2]
        var, place = tk.StringVar(value=bpm_text(v)), tk.StringVar(value=self.place_text(b))
        frame = tk.Frame(self)
        boxes = [tk.Entry(frame, textvariable=w, width=12, font=look.font(9), relief="solid", bd=1)
                 for w in (var, place)]
        for box in boxes:
            box.pack(fill="x")
            box.bind("<Return>", lambda e: self.close_typing(True))
            box.bind("<KP_Enter>", lambda e: self.close_typing(True))
            box.bind("<Escape>", lambda e: self.close_typing(False))
            box.bind("<FocusOut>", lambda e: self.after_idle(self.typing_left))
        for box, other in zip(boxes, boxes[::-1]):  # (Tab / Shift+Tab only go round the two rows)
            for key in ("<Tab>", "<Shift-Tab>", "<ISO_Left_Tab>"):
                box.bind(key, lambda e, o=other: (o.focus_set(), o.select_range(0, "end"), "break")[-1])
        Tooltip(boxes[1], tr("tempo.place_tip"))
        frame.update_idletasks()
        x, y = self.app.roll.t2x(b), float(self.b2y(v))
        w, h = frame.winfo_reqwidth(), frame.winfo_reqheight()
        frame.place(x=min(max(x + 8, 0), max(0, self.winfo_width() - w)),
                    y=max(0, min(y - h - 6, self.winfo_height() - h)))
        boxes[0].select_range(0, "end")
        boxes[0].focus_set()
        self._typing = (frame, var, item, place, place.get())

    def place_text(self, b):
        """A beat as bar:beat:tick, "5:3:240" (bars and beats from 1, ticks from 0; the time signature's beats)."""
        ppq = max(1, self.app.ppq)
        return "%d:%d:%d" % bar_beat_tick(sigs_of(self.app.tempo), round(b * ppq), ppq)

    def typed_place(self, text):
        """bar:beat:tick (or bar:beat / bar) typed -> beats, or None when it can't be read; "far" past the
        farthest beat a tempo point is kept at (tempo.FAR: undo / reopening dropped it, hunt 2026-10-25)."""
        try:
            nums = [int(s) for s in text.replace(" ", "").split(":")]
        except ValueError:
            return None
        if not 1 <= len(nums) <= 3:
            return None
        bar, beat, tick = nums + [1, 0][len(nums) - 1:]
        if bar < 1 or beat < 1 or tick < 0:
            return None
        ppq = max(1, self.app.ppq)
        if max(bar, beat, tick / ppq) > FAR:  # (before any maths: huge numbers froze the pane / overflowed)
            return "far"
        b = round(from_bar_beat_tick(sigs_of(self.app.tempo), bar, beat, tick, ppq) * ppq) / ppq
        return "far" if b > FAR else b

    def typing_left(self):
        """The keyboard left a box: Tab to the other one keeps them open, anywhere else = done."""
        if self._typing and str(self.focus_get() or "").startswith(str(self._typing[0])):
            return
        self.close_typing(True)

    def close_typing(self, keep):
        if not self._typing:
            return
        frame, var, p, place, was = self._typing
        self._typing = None
        try:
            if keep and any(p is q for q in self.ed()["points"]):
                self.take_typed(p, var.get(), place.get(), was)
        finally:  # (the box always goes, hunt 2026-10-25: an error left it stuck on the pane)
            frame.destroy()
            self.app.roll.focus_set()

    def take_typed(self, p, bpm, place, was):
        """The double-click box's BPM and place go to point p: one step, named for what really changed; none when
        nothing did (hunt 2026-10-25)."""
        v = None
        try:
            v = calc(bpm.replace(",", ""))
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0 and clamp_bpm(v) != p[1]):
                v = None
        except (ValueError, ArithmeticError, TypeError, SyntaxError):
            pass
        b = self.typed_place(place) if place.strip() != was else None
        status = None
        if b is None and place.strip() != was:
            status = "tempo.place_bad"
        elif b == "far":
            b, status = None, "tempo.place_refused"
        elif b is not None and abs(b - p[0]) < 1e-9:
            b = None
        if v is not None or b is not None:
            app = self.app
            app.push_undo(name=tr("tempo.step_bpm"))
            if v is not None:
                p[1] = clamp_bpm(v)
            if b is not None:
                if self.move_typed(p, b):
                    st = app.undo_stack[-1]
                    app.undo_stack[-1] = (st[0], tr("tempo.step_move")) + tuple(st[2:])
                else:
                    status = "tempo.place_refused"
            app.tempo_changed()
            app.settle_history()  # (a refused move with no BPM change: the empty step goes, Redo kept)
        if status:
            self.app.status.config(text=tr(status))

    def move_typed(self, p, b):
        """Point p moved to beat b by the drag's own rules (its jump partner goes along; where a drag couldn't go,
        it stays). False when it didn't move."""
        x = self.app.roll.t2x(p[0])
        e = type("Typed", (), {"x": x, "y": float(self.b2y(p[1])), "state": 0})()
        self.set_sel([p])
        self.start_move(e, p, pushed=True)
        d, self.drag = self.drag, None
        d["to"] = b
        start = p[0]
        self.move_to(d, e)
        drop_loose(self.ed()["points"])
        return abs(p[0] - start) > 1e-9

    # ------------------------------------------------------------ the catch-up's words in the bar

    def catch_result(self, j, tempo=None):
        """(text, can't) for catch-up line j: its BPM, or why it can't work."""
        tempo, base = tempo or self.ed(), self.base()
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
        """[(bpm, strong)] numbers for the left side: the tempo coming into view (bold, view_bpm), the file's limits, (exponential: doublings
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

        base = self._view_bpm if getattr(self, "_view_bpm", None) is not None else self.view_bpm()
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

    def heard_xy(self, heard, x0, x1):
        """A loaded MIDI's own tempo [(tick, us)] from x0 to x1 as a stepped line's points."""
        roll, ppq = self.app.roll, self.app.ppq
        t0, t1 = roll.x2t(x0) * ppq, roll.x2t(x1) * ppq
        i = max(0, bisect.bisect_right(heard, t0, key=lambda h: h[0]) - 1)
        u = heard[i][1] if heard and heard[i][0] <= t0 else 500000
        pts, x = [], x0
        for t, v in heard[i:]:
            if t > t1:
                break
            if t > t0:
                xt = roll.t2x(t / ppq)
                y = float(self.b2y(60_000_000 / u))
                pts += [x, y, xt, y]
                x = xt
            u = v
        y = float(self.b2y(60_000_000 / u))
        return pts + [x, y, x1, y]

    def shown_points(self, x0, x1, pts=None):
        """(first, end) indices of the points (pts: these; else the project's) between x0 and x1 (they're in beat
        order: a loaded MIDI brings thousands, only the ones in view are worked through)."""
        pts, roll = self.app.tempo["points"] if pts is None else pts, self.app.roll
        b0, b1 = roll.x2t(x0), roll.x2t(x1)
        return (bisect.bisect_left(pts, b0 - 1e-9, key=lambda p: p[0]),
                bisect.bisect_right(pts, b1 + 1e-9, key=lambda p: p[0]))

    def draw_off_marks(self, lines, w, kb, h, base):
        """A small arrow at the top / bottom edge where the tempo line (or Fill's faint preview) goes out of view
        above / below (user 2026-10-29: scrolled up, the preview looked missing; like the piano roll's arrows for
        shapes above its top key): one per stretch out of view, only where there is one. Clicking it scrolls there
        (off_mark_at). lines: [(xs, beats, ys, tempo, worked, preview)]."""
        s, ppq = self.scale, self.app.ppq
        marks = []
        for up in (True, False):
            spots = []  # (x, beat, line number)
            for n, (xs, beats, ys, *_) in enumerate(lines):
                off = np.flatnonzero(ys < 0 if up else ys > h)
                spots += [(float(xs[k]), float(beats[k]), n) for k in off.tolist()]
            spots.sort()
            group = []
            for spot in spots + [None]:
                if group and (spot is None or spot[0] - group[-1][0] > 30 * s):
                    us = []
                    for n in {g[2] for g in group}:  # (how far out it goes: the BPMs themselves, ys stop at the edge)
                        _, _, _, tempo, worked, _ = lines[n]
                        bs = [g[1] for g in group if g[2] == n]
                        us += self.ax(shown_bpm(tempo, base, bs, worked, ppq)).tolist()
                    x = min(max((group[0][0] + group[-1][0]) / 2, kb + 8 * s), w - 8 * s)
                    marks.append((x, up, max(us) if up else min(us), all(g[2] and lines[g[2]][5] for g in group)))
                    group = []
                if spot is not None:
                    group.append(spot)
        self._off_marks = marks
        for x, up, _, faint in marks:
            y0, y1 = (2 * s, 11 * s) if up else (h - 2 * s, h - 11 * s)
            self.create_polygon(x, y0, x + 6 * s, y1, x - 6 * s, y1, outline=look.ABOVE_EDGE, tags="off_mark",
                                fill=look.MELODY_PREVIEW if faint else look.TEMPO_LINE)

    def off_mark_at(self, x, y):
        """(up, the farthest BPM out on the pane's scale) of the arrow (draw_off_marks) at x, y; None."""
        s, h = self.scale, self.winfo_height()
        for mx, up, u, _ in getattr(self, "_off_marks", ()):
            if abs(mx - x) <= 8 * s and (0 <= y <= 14 * s if up else h - 14 * s <= y <= h):
                return up, u
        return None

    def after_off_click(self, e):
        """e is the second click of a double-click on an out-of-view arrow (same spot, soon after): ignored."""
        at = getattr(self, "_off_click", None)
        s = 8 * self.scale
        if at is not None and time.perf_counter() - at[0] < OFF_CLICK_S and abs(e.x - at[1]) <= s \
                and abs(e.y - at[2]) <= s:
            self._off_click = None  # (once: a later click there is a click again)
            return True
        return False

    def scroll_to_off(self, got):
        """An arrow clicked: the view moved up / down (same zoom) so the farthest part of that stretch shows, a
        little room past it."""
        up, u = got
        lo, hi = self.view
        span = hi - lo
        top, bot = self.area()
        room = span * 12 * self.scale / max(1, bot - top) + span * 0.05
        if up:
            self.set_view(u + room - span, u + room)
        else:
            self.set_view(u - room, u - room + span)
        self.request_redraw()

    def draw_limit_arrows(self, words, kb, w, h):
        """A small arrow at a Keep timing part's edge in view when the part has limit spots (draw_limits' texts)
        out of view that way (user 2026-10-30: they showed only when the spot itself was in view; everywhere
        would clutter). Its colour = the nearest spot's text's; clicking it scrolls to that spot (scroll_to_limit).
        words: draw_all's [x0, x1, too close, run, end beat, start beat] of the parts in view."""
        roll, s = self.app.roll, self.scale
        vl, vr = roll.x2t(kb), roll.x2t(w)
        arrows = []
        for x0, x1, _, _, b1, b0 in words:
            near = {}  # left / right: (beat, kind) of the nearest spot out of view
            for kind in ("dense", "unfit", "gave", "moved"):
                spots = self._limits.get(kind) or []
                i = bisect.bisect_left(spots, vl - 1e-9)  # (left: in the part, before the view)
                if i > 0 and spots[i - 1] >= b0 - 1e-9 and (near.get("left") is None or spots[i - 1] > near["left"][0]):
                    near["left"] = (spots[i - 1], kind)
                j = bisect.bisect_right(spots, vr + 1e-9)  # (right: in the part, past the view)
                if j < len(spots) and spots[j] <= b1 + 1e-9 and (near.get("right") is None or
                                                                spots[j] < near["right"][0]):
                    near["right"] = (spots[j], kind)
            for side, (b, kind) in near.items():
                x = x0 + 9 * s if side == "left" else x1 - 9 * s
                arrows.append((x, h - 10 * s, side == "left", b, kind))
        self._limit_arrows = arrows
        for x, y, left, _, kind in arrows:
            d = -1 if left else 1
            self.create_polygon(x + 6 * s * d, y, x - 4 * s * d, y - 6 * s, x - 4 * s * d, y + 6 * s,
                                fill=look.ERROR if kind in ("dense", "unfit") else look.WARN, outline=look.ROLL_BG,
                                tags="limit_arrow")

    def limit_arrow_at(self, x, y):
        """(beat, kind) of the arrow (draw_limit_arrows) at x, y; None."""
        r = 9 * self.scale
        for ax, ay, _, b, kind in getattr(self, "_limit_arrows", ()):
            if abs(ax - x) <= r and abs(ay - y) <= r:
                return b, kind
        return None

    def scroll_to_limit(self, got):
        """A limit arrow clicked: the piano roll's time moved (same zoom) so that spot is in the middle."""
        roll = self.app.roll
        roll.view_t = got[0] - (self.winfo_width() - roll.kb_w) / 2 / roll.sx
        roll.clamp_view()
        roll.request_redraw()

    def line_xy(self, worked, w, kb, tempo=None, base=None):
        """The tempo line (tempo: this one, base its BPM before the first point; else the project's) from x = kb
        to w as (xs, beats, ys): every 2 px, plus each jump drawn exactly upright (the beat just before and the
        beat itself at the same x)."""
        roll = self.app.roll
        tempo, base = (self.app.tempo, self.base()) if tempo is None else (tempo, base)
        xs = list(np.arange(kb, w + 2, 2, dtype=float))
        beats = [roll.x2t(x) for x in xs]
        i0, i1 = self.shown_points(kb - 2, w + 4, tempo["points"])
        jumps = {p[0] for p in tempo["points"][i0:i1]} | {st["line"] for st, _ in worked}
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
        vals = shown_bpm(tempo, base, beats, worked, ppq)
        return xs, beats, np.clip(self.b2y(vals), -50, self.winfo_height() + 50)

    def keep_offs(self, worked=None):
        """[(first, end beat)] where a Keep timing part is off because a catch-up's yellow area covers it."""
        tempo = self.app.tempo
        worked = plans(tempo, self.base(), self.app.ppq) if worked is None else worked
        offs = keep_off(tempo, [st for st, _ in worked])
        pts, out = tempo["points"], []
        for i, p in enumerate(pts):
            if keeps(p):
                a, e = p[0], pts[i + 1][0] if i + 1 < len(pts) else math.inf
                out += [(max(a, lo), min(e, hi)) for lo, hi in offs if max(a, lo) < min(e, hi) - 1e-9]
        return out

    def drag_preview(self):
        """While a Line / Formula drag is drawn: what letting go gives (user 2026-10-24: not a line dangling over
        the old one): {"tempo": the project's tempo line with it} (Draw: Visual tempo) / {"heard": {id(a run's
        point): its heard line with it}} (Draw: Heard tempo), else None."""
        d, ppq = self.drag, max(1, self.app.ppq)
        got = self.copies_preview() if self.box.mode == "heard" and self._ed is not None else None
        if got is not None:  # (a Ctrl+drag held, Heard tempo: its line as letting go gives it)
            return {"heard": {id(m): got[0]["points"] for m in self._ed[3]}}
        if not d or d["kind"] != "draw" or not d.get("moved") or abs(d["b1"] - d["b0"]) < 0.5 / ppq:
            return None
        kind = DRAWS.get(self.box.tool.get(), "line")
        text = self.box.draw_formula if kind == "formula" else ""
        args = (d["b0"], d["v0"], d["b1"], d["v1"], kind, text)
        if self.box.mode != "heard":
            return {"tempo": dict(self.app.tempo, points=self.laid(self.app.tempo["points"], *args, self.base())[0])}
        lo, hi = sorted((d["b0"], d["b1"]))
        out, tempo, moved = {}, self.app.tempo, {}
        at = {id(q): k for k, q in enumerate(tempo["points"])}
        for p, members, a, e in self.runs():
            if a < hi - 1e-9 and (e is None or e > lo + 1e-9):
                line = self.ed()["points"] if p is self.run else heard_line(p)
                base = heard_before(tempo, self.base(), self.app.ppq, at[id(members[0])], at[id(members[-1])])
                new = self.laid(line, *args, base)[0]
                out.update({id(m): new for m in members})
                h = heard_of(p)  # (the point after the part following the heard line's end: it moves too, as
                if h and h.get("end") is not None and e is not None:  # write_heard does when let go, hunt 2026-10-24)
                    on = [q for q in tempo["points"] if abs(q[0] - e) < 1e-9]
                    if on and abs(on[-1][1] - h["end"]) <= 1e-9 * h["end"]:
                        hl = copy.deepcopy(new)
                        split_at(hl, e, base)
                        moved[id(on[-1])] = clamp_bpm(next(q[1] for q in hl if abs(q[0] - e) < 1e-9))
        if not moved:
            return {"heard": out}
        pts = [[q[0], moved[id(q)]] + q[2:] if id(q) in moved else q for q in tempo["points"]]
        return {"heard": out, "tempo": dict(tempo, points=pts)}

    def heard_lines(self, keep_spans, w, kb, preview=None):
        """Each Keep timing piece's heard tempo as (xs, beats, ys, piece's first point index), cut to the piece:
        its heard line (as the export plays it), or flat at the tempo heard before it. preview: {id(point): heard
        line} drawn instead (a drag)."""
        out, pts = [], self.app.tempo["points"]
        every = self.app.tempo.get("every", EVERY)
        for x0, x1, u, b0, b1, i in keep_spans:
            got = (preview or {}).get(id(pts[i]))
            line = {"points": heard_line(pts[i]) if got is None else got, "catch": [], "every": every}
            xa, xb = max(kb, math.floor(x0)), min(w, math.ceil(x1))
            if xb - xa < 1:
                continue
            xs, beats, ys = self.line_xy([], xb, xa, line, 60_000_000 / u)
            on = (beats >= b0 - 1e-9) & (beats < b1)
            out.append((xs[on], beats[on], ys[on], i))
        return out

    def redraw(self):
        got = self.copies_preview() if self.box.mode != "heard" else None
        if got is None:
            return self.draw_all()
        keep = self.app.tempo, self.sel  # (a Ctrl+drag held: everything drawn as letting go gives it)
        self.app.tempo, self.sel = got
        try:
            self.draw_all()
        finally:
            self.app.tempo, self.sel = keep

    def draw_all(self):
        self._redraw_pending = False
        if not self.winfo_ismapped():
            return
        self.delete("all")
        self._off_marks = []  # (draw_off_marks: none until drawn)
        self._limit_arrows = []  # (draw_limit_arrows: the same)
        roll, app = self.app.roll, self.app
        w, h = self.winfo_width(), self.winfo_height()
        kb = int(roll.kb_w)
        if roll.sx is None or w - kb < 2 or h < 2 * self.pad + 10:
            return
        self._view_bpm = self.view_bpm()  # (the bold number + its line: worked out once a drawing)
        if self.view is None:
            self.fit_view(False)
        tempo, base = app.tempo, self.base()
        top, bot = self.area()
        lw = max(1, round(self.scale))
        worked = plans(tempo, base, self.app.ppq)
        areas = []  # (x0, x1, colour) of each coloured area, later ones on top: the faint grid lines' shade
        # stretches: the held part (amber) and its catch-up (teal; red when it can't work)
        for st, plan in worked:
            x1 = roll.t2x(st["line"])
            if st["end"] is not None and st["from"] < st["line"]:  # (its left edge drags: where it counts from)
                x0 = roll.t2x(st["from"])
                self.create_rectangle(max(kb, x0), 0, max(kb, x1), h, fill=look.TEMPO_DRAWN_BG, outline="")
                areas.append((x0, x1, look.TEMPO_DRAWN_BG))
                if x0 >= kb:
                    self.create_line(x0, 0, x0, h, fill=look.TEMPO_DRAWN_EDGE, width=lw * (2 if st["manual"] else 1),
                                     dash=() if st["manual"] else (3, 3))
            if st["end"] is not None:
                xe = roll.t2x(st["end"])
                fill = look.TEMPO_CATCH_BG if plan["ok"] else look.TEMPO_BAD_BG
                self.create_rectangle(max(kb, x1), 0, max(kb, xe), h, outline="", fill=fill)
                areas.append((x1, xe, fill))
                if st["manual_end"] and xe >= kb:  # (its right edge drags; set by hand: a solid line)
                    self.create_line(xe, 0, xe, h, fill=look.TEMPO_CATCH, width=lw * 2)
        zone_starts = {round(min(st["from"], st["line"]), 9) for st, _ in worked if st["end"] is not None}
        for st, _ in worked:  # (two catch-ups' areas touching: a thin gap between, user 2026-10-24)
            if st["end"] is not None and round(st["end"], 9) in zone_starts:
                x = roll.t2x(st["end"])
                if kb <= x <= w:
                    self.create_rectangle(x - 1.5 * lw, 0, x + 1.5 * lw, h, fill=look.ROLL_BG, outline="",
                                          tags="catch_gap")
                    areas.append((x - 1.5 * lw, x + 1.5 * lw, None))
        ppq = self.app.ppq
        heard_mode = self.box.mode == "heard"  # (Draw: Heard tempo: the heard lines full, the drawn one faded)
        keep_spans = [(max(kb, roll.t2x(a / ppq)), w if e is None else min(w, roll.t2x(e / ppq)), u, a / ppq,
                       math.inf if e is None else e / ppq, i) for i, a, e, u in layout(tempo, base, ppq)["keeps"]]
        keep_spans = [s for s in keep_spans if s[1] > s[0]]
        run_of = {k: n for n, (s, t) in enumerate(keep_runs(tempo)) for k in range(s, t + 1)}
        if app.loaded is None and not self.drag:  # (parts side by side in one run (one heard line: keep_runs) and
            # heard at one tempo before it: drawn as one, the same picture; thousands of jumps drew a box + line +
            # word check each, user 2026-10-29)
            joined_spans = []
            for s in keep_spans:
                p = joined_spans[-1] if joined_spans else None
                if p is not None and run_of.get(p[5]) is not None and run_of.get(p[5]) == run_of.get(s[5]) \
                        and abs(p[4] - s[3]) < 1e-9 and p[2] == s[2]:
                    joined_spans[-1] = (p[0], s[1], p[2], p[3], s[4], p[5])
                else:
                    joined_spans.append(s)
            keep_spans = joined_spans
        for x0, x1, *_ in keep_spans:  # Keep timing parts (purple), their notes keep the tempo heard before
            self.create_rectangle(x0, 0, x1, h, fill=look.TEMPO_KEEP_BG, outline="")
            areas.append((x0, x1, look.TEMPO_KEEP_BG))
        offs = self.keep_offs(worked)  # (Keep timing inside a catch-up's yellow area: off, user 2026-10-24)
        for a, b in offs:
            x0, x1 = max(kb, roll.t2x(a)), min(w, roll.t2x(b))
            for x in (x0, x1):
                if kb <= x <= w:
                    self.create_line(x, 0, x, h, fill=look.TEMPO_KEEP, dash=(4, 3), width=lw)
            if x1 - x0 > 150 * self.scale:
                self.create_text((x0 + x1) / 2, top + 8 * self.scale, text=tr("tempo.keep_in_catch"),
                                 fill=look.ERROR, font=look.font(8, "bold"), tags="keep_off")
        if heard_mode:  # (outside the Keep timing parts: faded, nothing to click there)
            x = kb
            for _, _, a, e in self.runs() + [(None, None, math.inf, None)]:
                xa = min(w, roll.t2x(a)) if a != math.inf else w
                if xa > x:
                    self.create_rectangle(x, 0, xa, h, fill=look.TEMPO_OUT_BG, outline="", tags="heard_out")
                    areas.append((x, xa, look.TEMPO_OUT_BG))
                x = max(x, w if e is None else roll.t2x(e))
                if x >= w:
                    break
        by = roll.tick_by() or 1.0
        for x, c in roll.grid_cols(w):
            if x >= kb:
                if c not in (look.GRID_BEAT, look.GRID_BAR):  # (tick / snap lines: a shade of the area they're on)
                    c = next((area_shade(f, by) if f else c for x0, x1, f in reversed(areas) if x0 <= x < x1), c)
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
        y = float(self.b2y(self._view_bpm))
        self.create_line(kb, y, w, y, fill=look.TEMPO_BASE, dash=(4, 3), width=lw)
        dense = self.dense_now()
        words = []  # [x0, x1, too close, run, end beat, start beat]: a run's pieces side by side = one part, one
        # word (user)
        for x0, x1, u, b0, b1, i in keep_spans:
            # notes too close for its tempo (user 2026-10-22: shown while editing, not only after the export): a
            # dashed orange line on each spot (one per pixel)
            bad = bisect.bisect_left(dense, b0 - 1e-9) < bisect.bisect_right(dense, b1 + 1e-9)
            shown = dense[bisect.bisect_left(dense, max(b0, roll.x2t(x0)) - 1e-9):
                          bisect.bisect_right(dense, min(b1, roll.x2t(x1)) + 1e-9)]
            for x in np.unique(np.round(roll.t2x(np.asarray(shown, float)))).tolist() if shown else ():
                self.create_line(x, 0, x, h, fill=look.WARN, dash=(3, 3), width=lw)
            if words and words[-1][3] == run_of.get(i) and abs(words[-1][4] - b0) < 1e-9:
                words[-1][1], words[-1][2], words[-1][4] = x1, words[-1][2] or bad, b1
            else:
                words.append([x0, x1, bad, run_of.get(i), b1, b0])
        for x0, x1, bad, *_ in words:  # (the words on top)
            text, colour = (tr("tempo.keep_dense"), look.WARN) if bad else (tr("tempo.keep"), look.TEMPO_KEEP)
            if x1 - x0 > 70 * self.scale:
                self.create_text((x0 + x1) / 2, top + 8 * self.scale, text=text, fill=colour,
                                 font=look.font(8, "bold"))
        self.draw_limit_arrows(words, kb, w, h)
        # the line itself (faded while the heard line is drawn: user 2026-10-24)
        preview = self.drag_preview()  # (a line being drawn: the tempo as letting go gives it)
        if preview and "tempo" in preview:
            pt = preview["tempo"]
            line_of = (pt, plans(pt, base, self.app.ppq))
            xs, beats, ys = self.line_xy(line_of[1], w, kb, pt, base)
        else:
            line_of = (tempo, worked)
            xs, beats, ys = self.line_xy(worked, w, kb)
        # (only what shows, a pixel column's first / lowest / highest / last point: Windows paints a thick line of
        # tens of thousands of points very slowly, ~1.2 s a redraw at 8,000 points, speed hunt 2026-10-29)
        for coords, _ in screen_lines(np.column_stack([xs, ys]), 1.0, 0.0, 1.0, 0.0, (-10, -10, w + 10, h + 10)):
            self.create_line(*coords, width=lw + 1, fill=look.TEMPO_FADED if heard_mode else look.TEMPO_LINE)
        fp = self.fill_preview
        lines = [(xs, beats, ys, *line_of, False)]  # (for the arrows to parts out of view)
        if fp is not None and not heard_mode:  # (what Fill with tempo jumps would lay: faint, over its area only)
            px, pb, py = self.line_xy(fp["worked"], w, kb, fp["tempo"], base)
            on = (pb >= fp["span"][0] - 1e-9) & (pb <= fp["span"][1] + 1e-9)
            if on.sum() >= 2:
                self.create_line(*np.column_stack([px[on], py[on]]).ravel().tolist(), fill=look.MELODY_PREVIEW,
                                 width=lw + 1, tags="fill_preview")
                lines.append((px[on], pb[on], py[on], fp["tempo"], fp["worked"], True))
        self.draw_off_marks(lines, w, kb, h, base)
        # the tempo the Keep timing notes are heard at (dashed; drawn with Heard tempo: solid)
        dash = () if heard_mode else (6, 3)
        loaded = file_tempos(app.loaded) if app.loaded is not None and keep_spans else None
        heard = self.heard_lines(keep_spans, w, kb, preview and preview.get("heard"))
        lay = layout(tempo, base, ppq) if loaded is not None else None
        known = FileTempo(loaded, lay) if loaded is not None else None  # (one reading for every part shown)
        by_point = {}  # (each point's pieces, in order: looking through all of them per part shown was parts x
        # pieces a redraw, 6,500 on the giant MIDI, speed hunt 2026-10-29)
        for n, (j, a, e, _) in enumerate(lay["keeps"] if lay is not None else ()):
            by_point.setdefault(j, []).append((n, a, e))
        for hx, hb, hy, i in heard:
            if loaded is not None and not heard_line(tempo["points"][i]) and len(hb):
                # (a loaded MIDI: the file's own tempo, from the tempo before the part when that was changed)
                n = next((n for n, a, e in by_point.get(i, ()) if a / ppq <= hb[0] + 1e-9
                          and (e is None or hb[0] < e / ppq)), None)
                if n is None:
                    continue
                e = lay["keeps"][n][2]
                part = loaded_part(loaded, lay, n, 2 ** 62 if e is None else e, known)
                events = [(0, part[0][2])] + [(x, u) for x, _, u in part[1:]] if part else loaded
                x0, x1 = max(kb, hx[0]), hx[-1]
                self.create_line(*self.heard_xy(events, x0, x1), fill=look.TEMPO_KEEP, dash=dash, width=lw + 1)
            elif len(hx) >= 2:
                self.create_line(*np.column_stack([hx, hy]).ravel().tolist(), fill=look.TEMPO_KEEP, dash=dash,
                                 width=lw + 1)
        starts = {round(b0, 9): k for k, (_, _, _, b0, _, _) in enumerate(keep_spans)}
        catches = {round(st["line"], 9) for st, _ in worked}
        for k, (hx, hb, hy, i) in enumerate(heard):  # where the sound jumps at a part's end: a dotted upright line
            b1 = keep_spans[k][4]
            x = roll.t2x(b1)
            if not len(hy) or b1 == math.inf or not kb <= x <= w or round(b1, 9) in catches:
                continue
            after = starts.get(round(b1, 9))
            if after is not None:
                nxt = next((g for g in heard if g[3] == keep_spans[after][5] and len(g[2])), None)
                y1 = nxt[2][0] if nxt is not None else None
            else:
                y1 = float(self.b2y(shown_bpm(tempo, base, [b1], worked, ppq)[0]))
            if y1 is not None and abs(y1 - hy[-1]) >= 1:
                self.create_line(x, hy[-1], x, y1, fill=look.TEMPO_KEEP, dash=(2, 2), width=lw)
        pick = self.picked()
        if pick and pick[0] == "part":  # the picked part, thicker
            pts = self.ed()["points"]
            a, b = pts[pick[1]][0], pts[pick[1] + 1][0] if pick[1] + 1 < len(pts) else math.inf
            if heard_mode:  # (on the run's heard line)
                span = self.run_span() or (0, 0)
                a, b = max(a, span[0]), min(b, span[1])
                got = [g for g in heard if len(g[1]) and span[0] - 1e-9 <= g[1][0] < span[1]]
                px = np.concatenate([g[0] for g in got]) if got else np.zeros(0)
                pb = np.concatenate([g[1] for g in got]) if got else np.zeros(0)
                py = np.concatenate([g[2] for g in got]) if got else np.zeros(0)
            else:
                px, pb, py = xs, beats, ys
            on = (pb >= a) & (pb < b)
            if on.sum() >= 2:
                self.create_line(*np.column_stack([px[on], py[on]]).ravel().tolist(), fill=look.TEMPO_PICKED,
                                 width=lw + 3)
        for at, k, _ in self.pins(tempo, worked):
            if kb <= roll.t2x(at) <= w:
                self.create_line(roll.t2x(at), 0, roll.t2x(at), h, width=lw * 4, tags="catch_pin",
                                 fill=look.TEMPO_DRAWN_EDGE if k == 3 else look.TEMPO_CATCH)
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
            text = bpm_text(plan["bpm"]) + " BPM" if ok else tr("tempo.not_possible") + (
                tr("tempo.why_" + plan["why"]).join((": ", "")) if plan.get("why") else "")  # (user 2026-10-24)
            xe = roll.t2x(st["end"]) if st["end"] is not None else x + 80 * self.scale
            item = self.create_text((x + min(xe, w)) / 2, top + 8 * self.scale, text=text, fill=col,
                                    font=look.font(8, "bold"))
            box = self.bbox(item)
            if box and box[0] < x + 2 * self.scale:  # (wider than its area: starts just right of the line)
                self.itemconfig(item, anchor="w")
                self.coords(item, x + 4 * self.scale, top + 8 * self.scale)
                box = self.bbox(item)
            if box and box[2] > w - 2:  # (past the pane's right edge: left of the
                self.itemconfig(item, anchor="e")                     # line instead, hunt 2026-10-24)
                self.coords(item, x - 4 * self.scale, top + 8 * self.scale)
        self.draw_drag(lw)
        mw = self.edges_on()
        if mw is not None and mw.live_chain() is not None:  # (Jump areas: the edges + "Area 1, 2…")
            mw.chain.draw(self, lw)
        ends = {st["end"] for st, _ in worked if st["end"] is not None}
        r = 4 * self.scale
        if heard_mode:  # (every run's heard points; the run being edited's can be picked)
            span = self.run_span() or (0.0, -1.0)  # (points left past its ends: not shown, cut on the next edit)
            shown = (preview or {}).get("heard") or {}  # (a line being drawn: the points as letting go gives them)
            mine = [q for q in shown.get(id(self.run), self.ed()["points"]) if span[0] - 1e-9 <= q[0] <= span[1] + 1e-9]
            groups = [(mine, look.TEMPO_KEEP)] + [
                ([p for p in shown.get(id(q), heard_line(q)) if a - 1e-9 <= p[0] and (e is None or p[0] <= e + 1e-9)],
                 look.TEMPO_KEEP) for q, _, a, e in self.runs() if q is not self.run]
        else:
            groups = [(preview["tempo"]["points"] if preview and "tempo" in preview else tempo["points"], None)]
        for pts, col in groups:
            i0, i1 = self.shown_points(kb - r, w + r, pts)
            drawn = set()  # (dots landing on the same pixel look the same: drawn once, thousands of jumps)
            # more dots than one per 3 px: none until zoomed in (like crowded curves on the piano roll; the line
            # and a Select box still show them; every 1 tick jumps drew thousands each redraw, user 2026-10-29)
            if (i1 - i0) * 3 > w - kb:
                continue
            for p in pts[i0:i1]:
                b, v = p[0], p[1]
                x, y = roll.t2x(b), float(self.b2y(v))
                if x < kb - r or x > w + r:
                    continue
                on = self.is_sel(p)
                edge = col or (look.TEMPO_CATCH if b in ends else look.TEMPO_LINE)
                spot = (round(x), round(y), on, edge)
                if spot in drawn:
                    continue
                drawn.add(spot)
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
        lines = [(xs, ys)] + [(hx, hy) for hx, _, hy, _ in heard]
        self.draw_limits(lines, kb, w, h, lw)
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

    def draw_limits(self, lines, kb, w, h, lw):
        """Short texts naming what the file can't do where Keep timing asks for it (user 2026-10-24): notes under a
        tick apart / a bar no time signature fits (red), CC messages moved a tick / bar lines written a little off
        (orange). One text where a stretch
        of such spots starts in view (the next one once past its end), a short mark on every spot; at the bottom,
        at the top when a line runs through that place (below the signature marks when one is there)."""
        roll, s = self.app.roll, self.scale
        font = look.font(8, "bold")
        th = 13 * s  # (a text's height)
        sig_x = [roll.t2x(b) for b, _, _, _ in self.sig_marks()]
        taken = []  # (x0, x1, y0, y1) of the texts put
        for kind, colour in (("dense", look.ERROR), ("unfit", look.ERROR), ("gave", look.WARN),
                             ("moved", look.WARN)):  # (bar lines written a little off: BAR CARRY-OVER, user)
            spots = self._limits.get(kind) or []
            lo = bisect.bisect_left(spots, roll.x2t(kb))
            hi = bisect.bisect_right(spots, roll.x2t(w))
            free_from = -math.inf
            text = tr("tempo.limit_" + kind)
            tw = tkfont.Font(font=font).measure(text)
            for b in spots[lo:hi]:
                x = roll.t2x(b)
                if kind != "dense":  # (dense spots have their orange dashed lines already)
                    self.create_line(x, h - 8 * s, x, h, fill=colour, width=lw * 2, tags=("limit", "limit_" + kind))
                if x < free_from:
                    continue
                x0, x1 = x + 3 * s, x + 3 * s + tw
                free_from = x1 + 20 * s
                low = any(self.crosses(lx, ly, x0, x1, h - th - 4 * s, h) for lx, ly in lines)
                # (at the top: under the "Keep timing" words, lower still under a signature mark)
                y = (2 * ROW * s + 12 * s if any(x0 - 4 * s <= sx <= x1 for sx in sig_x) else 24 * s) if low \
                    else h - th - 2 * s
                while any(a < x1 and x0 < b2 and abs(y - y0) < th for a, b2, y0, _ in taken):  # (texts don't overlap)
                    y = y + th if low else y - th
                taken.append((x0, x1, y, y + th))
                self.create_rectangle(x0 - 2 * s, y, x1 + 2 * s, y + th, fill=look.ROLL_BG, outline="",
                                      tags=("limit", "limit_" + kind))
                self.create_text(x0, y + th / 2, text=text, anchor="w", fill=colour, font=font,
                                 tags=("limit", "limit_" + kind))

    @staticmethod
    def crosses(lx, ly, x0, x1, y0, y1):
        """True when the line through points (lx, ly) passes through the box x0..x1, y0..y1 (each piece's ends'
        box: a steep piece crossing it counts too)."""
        if len(lx) < 2:
            return False
        ax, bx, ay, by = lx[:-1], lx[1:], ly[:-1], ly[1:]
        on = (np.maximum(ax, bx) >= x0) & (np.minimum(ax, bx) <= x1)
        return bool(np.any(on & (np.maximum(ay, by) >= y0) & (np.minimum(ay, by) <= y1)))

    def limit_at(self, x, y):
        """The limit text kind ("dense" / "unfit" / "gave") under the mouse, or None."""
        for item in self.find_overlapping(x - 1, y - 1, x + 1, y + 1)[::-1]:
            for tag in self.gettags(item):
                if tag.startswith("limit_"):
                    return tag[6:]
        return None

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
        if d and d["kind"] == "fill1":  # (Fill with tempo jumps' area: drawn as the Select box it becomes)
            spans = [d["span"]] if d["span"] else []
        if d and d["kind"] == "move" and d.get("copies") is not None and d.get("laid"):
            spans = [(a, b) for a, b in d["laid"] if b > a]
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
            if self.box.mode == "heard":  # (what lands outside the Keep timing parts: faded, it's dropped)
                bs = b0 + (b1 - b0) * t
                inside = np.zeros(len(t), bool)
                for _, _, a, e in self.runs():
                    inside |= (bs >= a) & (bs <= (math.inf if e is None else e))
                k = 0
                while k < len(t):
                    if inside[k]:
                        k += 1
                        continue
                    j = k
                    while j + 1 < len(t) and not inside[j + 1]:
                        j += 1
                    lo, hi = max(0, k - 1), min(len(t) - 1, j + 1)  # (to the edge samples: no gap)
                    seg = np.column_stack([xs[lo:hi + 1], ys[lo:hi + 1]]).ravel().tolist()
                    if len(seg) >= 4:
                        self.create_line(*seg, fill=look.TEMPO_FADED, width=lw + 2)
                    k = j + 1

    def show_play_bpm(self):
        app = self.app
        if not self.box.winfo_ismapped():  # (playing: every frame; worked out only while it shows)
            return
        t = math.floor(app.playhead * app.ppq + 1e-9)
        v = bpm_at(app.tempo, self.base(), app.ppq, t)
        # (the play line moved into / out of a catch-up's areas: only asked when stopped with nothing picked, the
        # only time it counts; each frame with a big tempo line and a point picked cost 14 ms, hunt 2026-10-24)
        idle = not (app.player.running or self.sel or self.part is not None or self.drag or self.placing)
        bar = (app.player.running, self.bar_catch() if idle else None)
        if bar != getattr(self, "_bar_catch", bar):
            self.box.sync()
        self._bar_catch = bar
        text = f"{v:,.2f}"  # (no "BPM": Domino shows the bare number, user 2026-10-22)
        if self.box.play_bpm.cget("text") != text:
            self.box.play_bpm.config(text=text)
        heard = self.heard_bpm_at(t)
        heard = f"({heard:,.2f})" if heard is not None and f"{heard:,.2f}" != text else ""
        if self.box.play_heard.cget("text") != heard:
            self.box.play_heard.config(text=heard)

    def heard_bpm_at(self, t):
        """The tempo heard at tick t when it's inside a Keep timing part (its heard line, or the tempo heard before
        it), else None."""
        app = self.app
        lay = layout(app.tempo, self.base(), app.ppq)
        for n, (_, a, e, _) in enumerate(lay["keeps"]):
            if a <= t and (e is None or t < e):
                u = np.asarray(keep_heard(lay, n, t, t + 1)).ravel()[0]
                return 60_000_000 / float(u)
        return None

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
