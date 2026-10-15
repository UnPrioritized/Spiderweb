"""The Hz bass window's "Hz shape…" window (user, 2026-10-10, was "Wave shape…"; notes/CLAUDE.md "HZ SHAPE WINDOW";
Note length rows under the drawing since 2026-10-13: length_rows): a line drawn
through one wave of the Hz bass (left to right) over its keys (bottom to top); every key row hits wherever the line
crosses it (hzbass.trace_hits). Straight lines from point to point. The line is stretched to all the keys, so the
key rows are drawn between its lowest and highest point, each with a dot where it hits.

Mouse: drag a point; a press on the line = a new point there, dragged; a press elsewhere = a new point at the line's
end; right-click / double-click a point = gone (2 stay). Each piece has a round dot in its middle (user, 2026-10-10):
dragged = the piece bends into a curve through it, right-click / double-click = straight again. Snapped to the grid
(1/16 each way), Shift = free. One undo step of the Hz bass window per change, on release; Ctrl+Z / Esc while held =
back to the press (HzMouse.held_fx). Each shown key row's notes are faint bars from its hits to their ends (the
Note length; wrapping past the right edge; ticks counted at the Hz bass's own key): drag any bar's end = every note
that whole % of a wave (user 2026-10-13), one step on release like the box. Under the drawing: preview strips at a
few pitches (draw_strips). With no Hz bass to hold it yet (nothing made, a spam shape without notes) a
change makes no step there: this window undoes those itself (local_step), so Ctrl+Z never lands on a step of the
piano roll's. The shape is the Hz bass's (its picked layer's) sound setting hz["trace"], kept in the Hz bass
window's `extra`."""

import copy
import json
import math
import os
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

from files.about import USER
from files.lang import tr
from files.mathexpr import calc
from files.safefile import write_text
from notes.hz_settings import clean_trace
from notes.hzbass import (FULL_LENGTH, HZ_DEFAULTS, START_TRACE, TRACE_POINTS, clean_extra, hz_gate,
                          key_range, length_ticks, trace_hits, trace_line)
from roll.roll_shared import SLOT_COLORS
from window import look
from window.hz_gates import LENGTH_UNITS
from window.hz_layers import layer_colour
from window.widgets import Scrub, Tooltip, name_list, no_spaces, remember_place, set_names

GRID = 16  # grid lines each way (snapped to)
PAD = 14  # px round the drawing area (x scale)
GRAB = 7  # px: how near a point / the line a press takes it
ROWS_SHOWN = 64  # more key rows than this: only some drawn (every key still hits)
MAX_BARS = 3000  # ... more note bars than this: fewer rows drawn (hunt: 33000 took 1.4 s to draw)
QUICK_BARS = 600  # ... more than this while a point is dragged: drawn once the mouse rests (rest_draw)
STRIP_H = 110  # px: the preview strips under the drawing (x scale; user 2026-10-13: how it looks low and high)
STRIP_ROWS = 32  # ... more key rows than this: only some drawn
STRIP_GAP = 6  # ... px between two (x scale)
STRIP_WAVES = 48  # ... the most waves the highest shows
STRIP_WAIT = 120  # ms: while the mouse holds something, the strips drawn again once it rests this long
FIXED_STRIPS = (21, 33, 45, 57)  # ... their keys while the Hz bass has no notes (an octave apart; no names shown)
# the starting shapes of the Shapes ▾ menu (my picks): a saw = each key once, the others each key twice a wave
SHAPES = {"straight": [list(p) for p in START_TRACE],  # (as it starts: shown after Reset, hunt)
          "saw": [[0.0, 0.0], [1.0, 1.0]],
          "triangle": [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]],
          "sine": [[i / 32, round(0.5 - 0.5 * math.cos(2 * math.pi * i / 32), 6)] for i in range(33)],
          "square": [[0.0, 0.0], [0.0, 1.0], [0.5, 1.0], [0.5, 0.0], [1.0, 0.0]]}
SHAPES_FILE = os.path.join(USER, "hz_shapes.json")  # the user's saved lines (Save…; in the shapes box after the
# ready-made ones, newest first)


def read_shapes():
    """(the user's saved lines [{"name", "points"}] in saving order, whether the file is there but can't be read).
    A broken entry is left out; a missing file = none."""
    if not os.path.exists(SHAPES_FILE):
        return [], False
    try:
        with open(SHAPES_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        shapes = data.get("shapes") if isinstance(data, dict) else None
        if not isinstance(shapes, list):
            return [], True
    except (OSError, ValueError):
        return [], True
    out = []
    for s in shapes:
        try:
            name, raw = clean_shape_name(str(s["name"])), s["points"]
            pts = clean_trace(raw) or ([list(p) for p in START_TRACE] if raw == [list(p) for p in START_TRACE]
                                       else None)  # (cleaned to [] = as it starts, or nothing usable)
        except (TypeError, KeyError, IndexError):
            continue
        if name and pts:
            out.append({"name": name, "points": pts})
    return out, False


def load_shapes():
    """The user's saved lines in saving order (none when the file can't be read)."""
    return read_shapes()[0]


def save_shapes(items):
    """Writes the saved lines (OSError when it can't be). A file there that can't be read is kept first as
    hz_shapes.json.bad (hunt: saving over it lost every older line); returns True when it was."""
    os.makedirs(os.path.dirname(SHAPES_FILE), exist_ok=True)
    kept = read_shapes()[1]
    if kept:
        os.replace(SHAPES_FILE, SHAPES_FILE + ".bad")
    write_text(SHAPES_FILE, json.dumps({"shapes": items}, indent=1))
    return kept


def clean_shape_name(name):
    from window.drawer import clean_name
    return clean_name(name)


def built_in_name(name):
    """The name is one of the ready-made shapes' (as shown or its id; capitals or not)."""
    low = name.casefold()
    return any(low in (k.casefold(), tr("hz.trace_" + k).casefold()) for k in SHAPES)


def free_shape_name(name, items):
    """name, or "name (2)", "name (3)"... when a saved line already has it (capitals or not); the name cut so the
    whole fits NAME_MAX letters (hunt: a long name came back cut to the one it copied)."""
    from window.drawer import NAME_MAX
    used = {s["name"].casefold() for s in items}
    out, k = name, 2
    while out.casefold() in used:
        end = f" ({k})"
        out, k = name[:NAME_MAX - len(end)].rstrip() + end, k + 1
    return out


def length_box(app, parent, var, apply, label=None):
    """A Note length box ("[100] [% of a wave ▾]"): a frame (not placed) with .entry, .unit (the dropdown:
    LENGTH_UNITS) and .scrub. apply() on Enter, leaving the box, each step of the number and a unit picked."""
    f = ttk.Frame(parent)
    f.entry = ttk.Entry(f, textvariable=var, width=7)
    f.entry.pack(side="left", padx=(0, 4))
    names = [tr("hz.length_" + u) for u in LENGTH_UNITS]
    f.unit = ttk.Combobox(f, values=names, state="readonly", width=max(map(len, names)) + 1)
    f.unit.current(0)
    f.unit.pack(side="left")
    f.unit.bind("<<ComboboxSelected>>", lambda e: apply())
    f.entry.bind("<Return>", lambda e: apply())
    f.entry.bind("<FocusOut>", lambda e: no_spaces(var) or apply())
    f.scrub = Scrub(app, [(f.entry, var, apply)], (1, 10, 1), 1, 100, label=label)
    wave = lambda: LENGTH_UNITS[max(0, f.unit.current())] == "wave"
    f.scrub.read = lambda text: wave_percent(text) if wave() else calc(text)  # (a typed 1/8 steps from 12.5, hunt)

    def step_for(value, n, state):  # (% of a wave: 0.1 steps at 1 % and under, so 0.1..1 % can be reached, hunt)
        step = f.scrub.step_size(state)
        return 0.1 if wave() and (value < 1 - 1e-9 or (value <= 1 + 1e-9 and n < 0)) else step
    f.scrub.step_for = step_for
    for w in (f.entry, f.unit):
        Tooltip(w, tr("hz.length_tip"))
    return f


def wave_percent(text):
    """A "% of a wave" box's text as %: a fraction of at most 1 ("1/8") = that share of a wave (12.5)."""
    v = float(calc(text))
    return v * 100 if "/" in text and 0 < v <= 1 else v


def note_share(length, gap, wave, ppq, whole=False):
    """How long notes last with this Note length (as hzbass.note_lengths makes them): gap = until the next hit on
    their key (as made), wave = one wave of their tone, both ticks (arrays or numbers). whole: in whole ticks, at
    least 1."""
    if "beats" in length:
        new = np.minimum(length_ticks(length, ppq), gap)
    elif "wave" in length:
        new = np.minimum(np.floor(wave * length["wave"] + 0.5) if whole else wave * length["wave"], gap)
    else:
        new = np.floor(gap * length["pct"] / 100.0 + 0.5) if whole else gap * length["pct"] / 100.0
    return np.maximum(new, 1) if whole else new


def row_hits(line, n):
    """Where each of n key rows hits in a wave (0..1, the lowest row first): as the drawing shows them (hits at the same
    moment = one note)."""
    return [sorted({round(x, 9) for x in trace_hits(line, (k + 0.5) / n)}) for k in range(n)]


def gaps(hits):
    """For each hit of a key row (0..1 of a wave): how far (waves) it is to the row's next hit, in the next wave too
    (a hit at the wave's end and one at its start are one note: no gap)."""
    at = sorted({round(x % 1.0, 9) for x in hits})
    to = {x: (at[(i + 1) % len(at)] - x) % 1.0 or 1.0 for i, x in enumerate(at)}
    return [to[round(x % 1.0, 9)] for x in hits]


def row_notes(hits, wave, span, length, ppq):
    """A key row's notes over `span` ticks with a tone whose wave is `wave` ticks (hits: where in each wave it hits,
    0..1): arrays of start and end ticks, each note as long as the Note length makes it (the wave before the stretch
    too: a note still sounding at its start begins before 0)."""
    n = np.arange(-1, int(span / wave) + 3)
    s = np.unique(np.round((n[:, None] + np.asarray(hits, float)[None, :]) * wave).astype(np.int64).ravel())
    s, gap = s[:-1], np.diff(s)
    e = s + note_share(length, gap, wave, ppq, whole=True).astype(np.int64)
    keep = (s < span) & (e > 0)
    return s[keep], e[keep]


def open_trace(hz):
    """Hz bass window → Settings… → Hz shape…: the window shown (it's made with the Hz bass window, hidden)."""
    if hz.trace_win is not None and hz.trace_win.winfo_exists():
        hz.sync()  # (its Note length rows as the Hz bass shown has them)
        hz.trace_win.deiconify()
        hz.trace_win.lift()
    else:
        hz.trace_win = TraceWindow(hz)
    hz.trace_win.refresh()
    return hz.trace_win


def own_trace(pts):
    """The line drawn as kept before a Hz bass holds it: none when it's as it starts."""
    pts = [list(p) for p in pts]
    return None if pts == [list(p) for p in START_TRACE] else pts


def shown_trace(extra, hz=None):
    """The wave shape the layer picked plays: its own (extra), else the Hz bass's main one (hz), else as it starts:
    a list of [x, y]."""
    return [list(p) for p in extra.get("trace") or (hz or {}).get("main_trace") or START_TRACE]


class TraceWindow(tk.Toplevel):
    def __init__(self, hz, hidden=False):
        """hidden: made with the Hz bass window, shown by Hz shape… (open_trace)."""
        super().__init__(hz)
        if hidden:
            self.withdraw()
        self.hz, self.app = hz, hz.app
        s = self.s = hz.app.scale
        self.title(tr("hz.trace_title"))
        self.transient(hz)
        self.geometry(f"{round(460 * s)}x{round(660 * s)}")
        self.minsize(round(360 * s), round(640 * s))  # (the drawing kept usable, ~200 px, under the strips, hunt)
        self.pts = shown_trace(hz.extra, (hz.target() or {}).get("hz"))
        self.drag = None  # {"i": point number, "was": points, "before": fx state, ...} while the mouse holds one
        self.bar = None  # while a note's end is dragged: {"x": press x, "from": its length (waves), "pct": now, ...}
        self.ends = []  # each note's end drawn: (px x, px y, its length in waves) (bar_at)
        self.strips_timer = None  # (draw: the strips waiting for the mouse to rest)
        self.resting = False  # (rest_draw: the mouse rests holding a point, everything drawn)
        self.placed = False  # (the last press put a new point in)
        self.local, self.local_redo = [], []  # the shapes before / after changes no Hz bass held (local_step)
        self.local_for = None  # (what they were made for: waiting_for())
        self.drawn = None  # (what the drawing shows: look_now())
        hint = ttk.Label(self, text=tr("hz.trace_hint"), foreground=look.HINT, padding=(8, 0, 8, 8))
        hint.pack(side="bottom", fill="x")  # (the rows under the drawing first: they always show)
        hint.bind("<Configure>", lambda e: hint.config(wraplength=max(1, e.width - round(16 * s))))
        self.length_rows(hz)
        f = self.own_row(self, "trace", hz.on_trace_all, hz.on_trace_back)
        f.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
        hz.trace_own_row = f.parts
        bar = ttk.Frame(self, padding=(8, 2, 8, 2))
        bar.pack(side="bottom", fill="x")
        b = ttk.Button(bar, text=tr("hz.trace_reset"), command=self.reset, takefocus=False)
        b.pack(side="left")
        Tooltip(b, tr("hz.trace_reset_tip"))
        self.saved = []  # the saved lines as the box lists them (newest first; fill_shapes)
        self.pick = None  # the box's pick: ("ready", id) / ("saved", its place in self.saved) / None (show_pick)
        box = self.shapes_box = name_list(ttk.Combobox(bar, state="readonly", width=14, takefocus=False,
                                                       postcommand=self.fill_shapes))
        box.pack(side="left", padx=(6, 0))
        box.bind("<<ComboboxSelected>>", lambda e: self.on_pick())
        Tooltip(box, tr("hz.trace_shapes_tip"))
        b = ttk.Button(bar, text=tr("hz.trace_save"), command=self.save_shape, takefocus=False)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("hz.trace_save_tip"))
        b = self.delete_btn = ttk.Button(bar, text=tr("hz.trace_delete"), command=self.delete_shape, takefocus=False)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("hz.trace_delete_tip"))
        self.says = ttk.Label(self, text="", foreground=look.INFO, padding=(8, 0, 8, 0))  # (its own line: the bar
        self.says.pack(side="bottom", fill="x")  # is full with the shapes box, Save… and Delete)
        self.fill_shapes()
        st = self.strips = tk.Canvas(self, background=look.CHART_BG, highlightthickness=0, height=round(STRIP_H * s))
        st.pack(side="bottom", fill="x", padx=8, pady=(0, 4))
        st.bind("<Configure>", lambda e: self.draw_strips())
        Tooltip(st, tr("hz.trace_strips_tip"))
        c = self.canvas = tk.Canvas(self, background=look.CHART_BG, highlightthickness=0, takefocus=True)
        c.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        c.bind("<Configure>", lambda e: self.draw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<ButtonPress-3>", self.on_right)
        c.bind("<Motion>", self.on_motion)
        c.bind("<Escape>", lambda e: self.cancel_drag())
        self.bind("<F1>", lambda e: (self.app.open_help("hz_shape"), "break")[1])  # (its page: the hint is short)
        self.protocol("WM_DELETE_WINDOW", self.close)
        remember_place(self, "hz_shape")  # (not "hz_trace": the window grew for the strips, an old size would squash)

    def length_rows(self, hz):
        """Note length (moved here from Settings…, user 2026-10-13): ONE box = what the layer picked plays (2+
        layers: changing it gives that layer its own, user 2026-10-14; a line under it says whose it is, Use for all
        layers / Back to the main length), the last note sticking out. The widgets are the Hz bass window's
        (hz.length_row...; hz_gates show_length / on_length): this window is made with it and only hidden when
        closed, so they're always there. Changes are undo steps of the Hz bass window, like the drawing's."""
        app = self.app
        box = ttk.Frame(self, padding=(8, 0, 8, 6))
        box.pack(side="bottom", fill="x")
        lb = ttk.Label(box, text=tr("hz.length"))
        lb.grid(row=0, column=0, sticky="e", padx=(0, 8), pady=2)
        hz.length_row = length_box(app, box, hz.length_var, hz.on_length, lb)
        hz.length_row.grid(row=0, column=1, sticky="w", pady=2)
        Tooltip(lb, tr("hz.length_tip"))
        box.columnconfigure(1, weight=1)
        f = self.own_row(box, "length", hz.on_length_all, hz.on_length_back)
        f.grid(row=1, column=1, sticky="we", pady=(0, 2))
        hz.length_own_row = f.parts
        hz.stick_box = ttk.Checkbutton(box, text=tr("hz.stick"), variable=hz.stick, command=hz.on_stick,
                                       takefocus=False)
        hz.stick_box.grid(row=2, column=1, sticky="w", pady=2)
        hz.stick_tip = Tooltip(hz.stick_box, tr("hz.stick_tip"))

    def own_row(self, parent, what, every, back):
        """The line under the shapes bar / the Note length box with layers (hz_gates.show_own): whose the setting
        is, Use for all layers, Back to the main ... (what = "length" / "trace"). ALWAYS as tall as a button, also
        empty, so nothing in the window moves when its buttons come and go (user 2026-10-14)."""
        f = ttk.Frame(parent)
        mark = ttk.Label(f, text="")
        Tooltip(mark, tr(f"hz.{what}_layer_tip"))
        b1 = ttk.Button(f, text=tr("hz.use_for_all"), command=every, takefocus=False)
        Tooltip(b1, tr(f"hz.{what}_all_tip"))
        b2 = ttk.Button(f, text=tr(f"hz.{what}_back"), command=back, takefocus=False)
        Tooltip(b2, tr(f"hz.{what}_back_tip"))
        f.config(height=b1.winfo_reqheight())
        f.pack_propagate(False)
        f.parts = (f, mark, b1, b2)
        return f

    # ------------------------------------------------------------ where things are

    def area(self):
        """The drawing area on the canvas: (left, top, width, height) px."""
        pad = PAD * self.s
        w, h = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        return pad, pad, max(1.0, w - 2 * pad), max(1.0, h - 2 * pad)

    def xy(self, p):
        x0, y0, w, h = self.area()
        return x0 + p[0] * w, y0 + (1.0 - p[1]) * h

    def point_at(self, e, snap=True):
        """The spot under the mouse as [x, y] 0..1, snapped to the grid unless Shift is held."""
        x0, y0, w, h = self.area()
        x, y = (e.x - x0) / w, 1.0 - (e.y - y0) / h
        if snap and not e.state & 0x1:
            x, y = round(x * GRID) / GRID, round(y * GRID) / GRID
        return [min(1.0, max(0.0, x)), min(1.0, max(0.0, y))]

    def grabbed(self, e):
        """The point under the mouse (its number), or None."""
        best, near = None, GRAB * self.s
        for i, p in enumerate(self.pts):
            x, y = self.xy(p)
            d = max(abs(x - e.x), abs(y - e.y))
            if d <= near:
                best, near = i, d
        return best

    def middle(self, i):
        """The middle of the piece coming to point i (its round dot): halfway, moved by its bend."""
        a, b = self.pts[i - 1], self.pts[i]
        bx, by = (b[2], b[3]) if len(b) >= 4 else (0.0, 0.0)
        return [(a[0] + b[0]) / 2 + bx, (a[1] + b[1]) / 2 + by]

    def dots(self):
        """The pieces showing a round dot (the numbers of the points they go to): bent ones, and straight ones long
        enough on screen for it not to crowd their ends (a Sine's many short pieces have none)."""
        out = []
        for i in range(1, len(self.pts)):
            (ax, ay), (bx, by) = self.xy(self.pts[i - 1]), self.xy(self.pts[i])
            if len(self.pts[i]) >= 4 or ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 >= 4 * GRAB * self.s:
                out.append(i)
        return out

    def bend_at(self, e):
        """The round dot under the mouse (the number of the point its piece goes to), or None."""
        best, near = None, GRAB * self.s
        for i in self.dots():
            x, y = self.xy(self.middle(i))
            d = ((x - e.x) ** 2 + (y - e.y) ** 2) ** 0.5
            if d <= near:
                best, near = i, d
        return best

    def on_line(self, e):
        """The piece of the line under the mouse: (the number of the point it goes to, how far along it 0..1), or
        None."""
        best, near = None, GRAB * self.s
        for i in range(1, len(self.pts)):
            line = [self.xy(p) for p in trace_line([self.pts[i - 1], self.pts[i]])]
            for k, ((ax, ay), (bx, by)) in enumerate(zip(line, line[1:])):
                dx, dy = bx - ax, by - ay
                t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((e.x - ax) * dx + (e.y - ay) * dy) / (dx * dx + dy * dy)))
                d = ((ax + t * dx - e.x) ** 2 + (ay + t * dy - e.y) ** 2) ** 0.5
                if d <= near:
                    best, near = (i, (k + t) / (len(line) - 1)), d
        return best

    def split(self, i, t, p):
        """A new point p put in the piece coming to point i, t of the way along it: a bent piece stays the same
        curve on both sides (cut at t), only moved to p."""
        a, b = self.pts[i - 1], self.pts[i]
        if len(b) < 4:
            self.pts.insert(i, p)
            return
        cx, cy = 2 * b[2] + (a[0] + b[0]) / 2, 2 * b[3] + (a[1] + b[1]) / 2  # (the curve's pull point)
        l = [a[0] + t * (cx - a[0]), a[1] + t * (cy - a[1])]  # (each half's pull point)
        r = [cx + t * (b[0] - cx), cy + t * (b[1] - cy)]
        self.pts[i] = [b[0], b[1], (r[0] - (p[0] + b[0]) / 2) / 2, (r[1] - (p[1] + b[1]) / 2) / 2]
        self.pts.insert(i, [p[0], p[1], (l[0] - (a[0] + p[0]) / 2) / 2, (l[1] - (a[1] + p[1]) / 2) / 2])
        self.pull_in(i)
        self.pull_in(i + 1)

    def rows(self):
        """How many key rows the Hz bass shown has (its box's keys; the keys it will have before its first note)."""
        sh = self.hz.target()
        if sh is not None and sh["kind"] in ("custom", "funnel") and sh.get("pts"):
            lo, hi = key_range(sh)
        else:
            lo, hi = self.app.hz_defaults["lo"], self.app.hz_defaults["hi"]
        return max(1, hi - lo + 1)

    def length_now(self):
        """The Note length the notes shown have: the layer picked's own, else the Hz bass's (100 % with none); while
        a note's end is dragged, that % of a wave."""
        if self.bar is not None and self.bar["pct"] is not None:
            return {"wave": self.bar["pct"] / 100.0}
        sh = self.hz.length_target()
        if sh is None:
            return FULL_LENGTH
        return self.hz.extra.get("own_length") or sh["hz"].get("own_length") or sh["hz"].get("length") or FULL_LENGTH

    def bpm(self):
        sh = self.hz.target()
        return (sh["hz"]["bpm"] if sh is not None and sh.get("hz") else None) or self.app.current_bpm() or 120.0

    def main_wave(self):
        """One wave of the Hz bass's own tone (its Pitch box's key; ticks): the drawing's notes in ticks are that long
        a wave."""
        sh = self.hz.target()
        hz = sh["hz"] if sh is not None and sh.get("hz") else HZ_DEFAULTS
        return hz_gate(hz, self.bpm()) * self.app.ppq

    def strip_keys(self):
        """The pitches the preview strips show, low to high (keys, not whole): the lowest, middle and highest of the
        layer shown's notes (fewer when they're the same), or FIXED_STRIPS with no notes."""
        ps = sorted({round(t["key"] + t.get("cents", 0.0) / 100.0, 6) for t in self.hz.tones or []})
        if not ps:
            return FIXED_STRIPS
        return tuple(sorted({ps[0], ps[len(ps) // 2], ps[-1]}))

    def note_colour(self):
        """(fill, edge) of the notes in the strips: the layer picked's colour, as in the Hz window."""
        hz = (self.hz.target() or {}).get("hz") or {}
        return layer_colour(hz, hz.get("layer", 0)) if hz.get("layers") else SLOT_COLORS[0]

    # ------------------------------------------------------------ drawing

    def refresh(self):
        """The shape from the Hz bass window (another Hz bass shown, undo...), unless the mouse holds a point. Drawn
        again only when something it shows changed (the Hz bass window redraws often: dragging, scrolling)."""
        if self.drag is None:
            self.pts = shown_trace(self.hz.extra, (self.hz.target() or {}).get("hz"))
        if self.drawn != self.look_now():
            self.draw()
        sh = self.hz.target()
        hz = (sh or {}).get("hz") or {}
        title = (tr("hz.trace_title_layer", layer=self.hz.layers.name_of(hz, hz.get("layer", 0)))
                 if hz.get("layers") else tr("hz.trace_title"))  # (the layer picked, user 2026-10-14)
        if self.title() != title:
            self.title(title)

    def check_local(self):
        """This window's own undo steps are forgotten once the shape they were made for changed: a Hz bass holds
        the line now (its steps count), another shape shown, or the one shown gone."""
        if self.local_for != self.waiting_for() or (self.hz.target() or {}).get("hz"):
            self.local, self.local_redo = [], []

    def look_now(self):
        """What the drawing shows (draw: nothing changed = not drawn again)."""
        return ([list(p) for p in self.pts], self.rows(), self.canvas.winfo_width(), self.canvas.winfo_height(),
                self.length_now(), self.main_wave(), self.app.ppq, self.strip_keys(), self.note_colour())

    def draw(self):
        c, s = self.canvas, self.s
        c.delete("all")
        self.drawn = self.look_now()
        x0, y0, w, h = self.area()
        for i in range(GRID + 1):  # (the grid: quarters stronger)
            colour = look.CHART_GRID_STRONG if i % (GRID // 4) == 0 else look.CHART_GRID
            c.create_line(x0 + i * w / GRID, y0, x0 + i * w / GRID, y0 + h, fill=colour)
            c.create_line(x0, y0 + i * h / GRID, x0 + w, y0 + i * h / GRID, fill=colour)
        line = trace_line(self.pts)
        lo, hi = min(p[1] for p in line), max(p[1] for p in line)
        for a, b in ((hi, 1.0), (0.0, lo)):  # (outside the line's height: no keys there, it's stretched to them)
            if b - a > 1e-9:
                c.create_rectangle(x0, y0 + (1 - b) * h, x0 + w, y0 + (1 - a) * h, fill=look.CHART_BAND, outline="",
                                   stipple="gray50")
        n = self.rows()
        hits_of = row_hits(line, n)  # (hits at the same moment: one note, as made)
        total = sum(map(len, hits_of))
        every = max(1, -(-n // ROWS_SHOWN))
        while every < n and sum(len(x) for x in hits_of[::every]) > MAX_BARS:  # (fewer rows: hunt, 33000 bars
            every += 1  # took 1.4 s a mouse move)
        # a point held over many bars: only the line while the mouse moves, bars and dots once it rests (hunt)
        quick = self.drag is not None and not self.resting and sum(len(x) for x in hits_of[::every]) > QUICK_BARS
        r = 2.5 * s
        length, wave, ppq = self.length_now(), self.main_wave(), self.app.ppq
        bh = max(2.0, min(8 * s, 0.6 * every * (hi - lo) * h / n))  # (a note bar's height: under its row's room)
        self.ends = []
        dots = []
        for k, hits in enumerate(hits_of):
            if k % every or quick:
                continue
            y = (k + 0.5) / n
            py = y0 + (1 - (lo + y * (hi - lo))) * h if hi - lo > 1e-12 else y0 + (1 - lo) * h
            if hi - lo > 1e-12:
                c.create_line(x0, py, x0 + w, py, fill=look.CHART_BAND)
            for x, gap in zip(hits, gaps(hits)):  # (each note faint, from its hit to its end: wraps past the right)
                size = float(note_share(length, gap * wave, wave, ppq)) / wave
                end = x + size
                for a, b in ((x, min(end, 1.0)), (0.0, end - 1.0)):
                    if b - a > 1e-9:
                        c.create_rectangle(x0 + a * w, py - bh / 2, x0 + b * w, py + bh / 2, fill=look.HZ_NOTE_BAR,
                                           outline=look.HZ_NOTE_BAR_EDGE)  # (edged: touching notes stay apart, user)
                self.ends.append((x0 + (end if end <= 1.0 + 1e-9 else end - 1.0) * w, py, size, size >= gap - 1e-9))
                dots.append((x0 + x * w, py))
        for px, py in dots:  # (on top of every bar)
            c.create_oval(px - r, py - r, px + r, py + r, fill=look.HZ_GREEN, outline="")
        c.create_rectangle(x0, y0, x0 + w, y0 + h, outline=look.CHART_FRAME)
        c.create_line(*[v for p in line for v in self.xy(p)], fill=look.CHART_LINE, width=max(1, round(2 * s)))
        q = 3.5 * s
        for i in self.dots():  # (each piece's round dot: drag it to bend the piece)
            x, y = self.xy(self.middle(i))
            c.create_oval(x - q, y - q, x + q, y + q, fill=look.CHART_POINT if len(self.pts[i]) < 4 else look.CHART_LINE,
                          outline=look.CHART_LINE)
        for p in self.pts:
            x, y = self.xy(p)
            c.create_rectangle(x - q, y - q, x + q, y + q, fill=look.CHART_POINT, outline=look.CHART_LINE)
        self.show_pick()
        times = total / n
        if abs(times - 1.0) < 1e-9:
            self.says.config(text=tr("hz.trace_same"), foreground=look.INFO)
        else:
            self.says.config(text=tr("hz.trace_times", times=f"{times:.2f}".rstrip("0").rstrip(".")),
                             foreground=look.WARN if times > 1.0 else look.INFO)
        if self.strips_timer:
            self.after_cancel(self.strips_timer)
            self.strips_timer = None
        if (self.drag is not None or self.bar is not None) and not self.resting:  # (held: drawn once the mouse
            self.strips_timer = self.after(STRIP_WAIT, self.rest_draw if quick else self.draw_strips)  # rests, hunt)
        else:
            self.draw_strips()

    def rest_draw(self):
        """The mouse rests holding a point: the bars and dots drawn too (draw left them out while it moved)."""
        self.strips_timer = None
        if not self.canvas.winfo_exists():
            return
        self.resting = True
        try:
            self.draw()
        finally:
            self.resting = False

    def draw_strips(self):
        """The preview strips (user 2026-10-13): the notes the drawing makes at a few pitches from low to high, drawn
        like the piano roll (one row per key, bottom to top; no pitch names), all over the same stretch of time, so a
        higher pitch shows more waves. Only the drawing and the Note length: not the effects."""
        c, s = self.strips, self.s
        self.strips_timer = None
        if not c.winfo_exists():  # (a wait for the mouse to rest ending after the window went)
            return
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw < 10 or ch < 10:
            return
        keys, ppq, bpm = self.strip_keys(), self.app.ppq, self.bpm()
        waves = [hz_gate({"key": k, "cents": 0.0}, bpm) * ppq for k in keys]
        span = min(4 * waves[0], STRIP_WAVES * waves[-1])  # (4 waves of the lowest, unless the highest gets crowded:
        # then the lowest shows less than one, hunt: notes far apart drew ~1500 waves up high)
        n = self.rows()
        every = max(1, -(-n // STRIP_ROWS))
        rows = row_hits(trace_line(self.pts), n)[::every]
        length = self.length_now()
        fill, edge = self.note_colour()
        gap = STRIP_GAP * s
        sw = (cw - gap * (len(keys) - 1)) / len(keys)
        rh = (ch - 1) / len(rows)
        for i, wave in enumerate(waves):
            left = i * (sw + gap)
            c.create_rectangle(left, 0, left + sw - 1, ch - 1, fill=look.HZ_BG, outline="")
            for j, hits in enumerate(rows):
                top = ch - 1 - (j + 1) * rh
                if rh >= 4 and j:
                    c.create_line(left, top + rh, left + sw - 1, top + rh, fill=look.HZ_ROW_LINE)
                starts, ends = row_notes(hits, wave, span, length, ppq)
                a = np.floor(left + np.maximum(starts, 0) / span * (sw - 1)).astype(int)
                b = np.maximum(a + 1, np.ceil(left + np.minimum(ends, span) / span * (sw - 1)).astype(int))
                if not len(a):
                    continue
                first = np.flatnonzero(np.concatenate([[True], a[1:] != a[:-1]]))  # (each note its own, edge and all,
                a, b = a[first], np.minimum(np.maximum.reduceat(b, first), int(left + sw - 1))  # like the piano roll,
                line = edge if rh >= 3 else ""  # user: touching ones drawn as one looked like a long note; notes in
                for p, q in zip(a.tolist(), b.tolist()):  # one pixel: one)
                    c.create_rectangle(p, top + 1, q, top + rh, fill=fill, outline=line)
            c.create_rectangle(left, 0, left + sw - 1, ch - 1, outline=look.CHART_FRAME)

    # ------------------------------------------------------------ the mouse

    def bar_at(self, e):
        """The note's end under the mouse (its length in waves), or None (none to change: no Hz bass). A note reaching
        its row's next hit ends on the line: the line wins there (at 100 % every end does, hunt)."""
        if self.hz.length_target() is None:
            return None
        best, near = None, GRAB * self.s
        line = None
        for x, y, size, full in self.ends:
            d = max(abs(x - e.x), abs(y - e.y) / 1.5)
            if d <= near and full:
                if line is None:
                    line = self.on_line(e) is not None
                if line:
                    continue
            if d <= near:
                best, near = size, d
        return best

    def box_share(self):
        """The Note length set, as a share of a wave (where a bar drag starts from: a bar cut short by its row's next
        hit would make a small drag jump, hunt). % of the note: that share; ticks: of the Hz bass's own wave."""
        length = self.length_now()
        if "wave" in length:
            return length["wave"]
        if "beats" in length:
            return min(1.0, length_ticks(length, self.app.ppq) / self.main_wave())
        return length["pct"] / 100.0

    def on_motion(self, e):
        """Over a note's end: the sideways arrows (drag it to change every note's length)."""
        want = "sb_h_double_arrow" if self.drag is None and self.grabbed(e) is None and not self.bend_at(e) and (
            self.bar is not None or self.bar_at(e) is not None) else ""
        if str(self.canvas.cget("cursor")) != want:
            self.canvas.config(cursor=want)

    def length_box(self):
        """The Note length box: (variable, row, apply) (one box: on_length knows whose length it is)."""
        hz = self.hz
        return hz.length_var, hz.length_row, hz.on_length

    def on_press(self, e):
        self.canvas.focus_set()
        if self.drag is not None or self.bar is not None:
            return
        was = [list(p) for p in self.pts]
        i = self.grabbed(e)
        bend = i is None and self.bend_at(e)
        if bend:
            i = bend
        if i is None and self.bar_at(e) is not None:  # (a note's end: every note's length follows, % of a wave, user)
            var, row, _ = self.length_box()
            self.bar = {"x": e.x, "from": self.box_share(), "pct": None, "was": (var.get(), row.unit.current())}
            self.placed = True  # (a double click there takes nothing out)
            return
        self.placed = i is None  # (a double click on the point this click puts in doesn't take it out again)
        if i is None:
            if len(self.pts) >= TRACE_POINTS:
                return self.bell()
            got = self.on_line(e)
            if got is None:
                i = len(self.pts)
                self.pts.append(self.point_at(e))
            else:
                i = got[0]
                self.split(i, got[1], self.point_at(e))
        self.drag = {"i": i, "bend": bool(bend), "was": was, "before": self.hz.fx.state(), "at": (e.x, e.y),
                     "moved": False, "start": [list(p) for p in self.pts]}  # (start: as at the press, new point in)
        self.draw()

    def on_drag(self, e):
        if self.bar is not None:
            return self.drag_bar(e)
        d = self.drag
        if d is None:
            return
        if not d["moved"] and max(abs(e.x - d["at"][0]), abs(e.y - d["at"][1])) < 3:
            return  # (under 3 px: still a click, the point stays where it is)
        d["moved"] = True
        i, p = d["i"], self.point_at(e)
        if d["bend"]:  # (the round dot: the piece bends so its middle is there)
            a, b = self.pts[i - 1], self.pts[i]
            bx, by = p[0] - (a[0] + b[0]) / 2, p[1] - (a[1] + b[1]) / 2
            self.pts[i] = b[:2] + ([bx, by] if max(abs(bx), abs(by)) > 1e-9 else [])
        else:  # (a point: the bends on both sides kept as at the press, pulled in so their dots stay inside)
            self.pts = [list(q) for q in d["start"]]
            self.pts[i] = p + self.pts[i][2:]
            for j in (i, i + 1):
                self.pull_in(j)
        self.draw()

    def pull_in(self, i):
        """The bend of the piece coming to point i made smaller if its round dot would be outside the drawing (it
        couldn't be grabbed there)."""
        if not 0 < i < len(self.pts) or len(self.pts[i]) < 4:
            return
        a, b = self.pts[i - 1], self.pts[i]
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        bx, by = min(1.0, max(0.0, mx + b[2])) - mx, min(1.0, max(0.0, my + b[3])) - my
        self.pts[i] = b[:2] + ([bx, by] if max(abs(bx), abs(by)) > 1e-9 else [])

    def drag_bar(self, e):
        """A note's end dragged: every note that share of a wave long (whole %, 1..100), shown in the box at once;
        the notes made on release."""
        b = self.bar
        if abs(e.x - b["x"]) < 3:  # (under 3 px: a click; back there: as before, a typed 1/8 kept, hunt)
            if b["pct"] is not None:
                var, row, _ = self.length_box()
                var.set(b["was"][0])
                row.unit.current(b["was"][1])
                b["pct"] = None
                self.draw()
            return
        pct = min(100, max(1, round((b["from"] + (e.x - b["x"]) / self.area()[2]) * 100)))
        if pct == b["pct"]:
            return
        b["pct"] = pct
        var, row, _ = self.length_box()
        row.unit.current(LENGTH_UNITS.index("wave"))
        var.set(str(pct))
        self.draw()

    def on_release(self, e):
        if self.bar is not None:
            b, self.bar = self.bar, None
            if b["pct"] is not None:
                self.length_box()[2]()  # (one undo step, as typed in the box)
            self.draw()
            return
        d, self.drag = self.drag, None
        if d is None:
            return
        if self.pts != d["was"]:
            self.save(d["before"], d["was"])
        self.draw()

    def on_double(self, e):
        if not self.placed:
            self.remove(e)

    def on_right(self, e):
        if self.drag is None and self.bar is None:
            self.remove(e)

    def remove(self, e):
        """The point under the mouse taken away (the line keeps at least 2; the piece left there is straight), or
        the round dot's piece made straight again."""
        i = self.grabbed(e)
        if i is None:
            j = self.bend_at(e)
            if j is None:  # (or the bent piece itself: its dot may sit on a point, where it can't be grabbed)
                got = self.on_line(e)
                j = got and got[0]
            if not j or len(self.pts[j]) < 4:
                return
            was, before = [list(p) for p in self.pts], self.hz.fx.state()
            self.pts[j] = self.pts[j][:2]
        else:
            if len(self.pts) <= 2:
                return self.bell()
            was, before = [list(p) for p in self.pts], self.hz.fx.state()
            del self.pts[i]
            if i < len(self.pts):  # (the point after it: its piece now comes from further back, straight)
                self.pts[i] = self.pts[i][:2]
        self.save(before, was)
        self.draw()

    def cancel_drag(self):
        """Ctrl+Z / Esc while a point or a note's end is held: back as at the press, no undo step."""
        if self.bar is not None:
            var, row, _ = self.length_box()
            var.set(self.bar["was"][0])
            row.unit.current(self.bar["was"][1])
            self.bar = None
            self.draw()
        if self.drag is not None:
            self.pts = self.drag["was"]
            self.drag = None
            self.draw()

    def held(self):
        """What puts back the point or note's end the mouse holds (HzMouse.held_fx), or None."""
        return self.cancel_drag if self.drag is not None or self.bar is not None else None

    def reset(self):
        """Back to straight up at the wave's start (every key together) and the layer's Note length back to the
        main one (one layer: to 100 %, not sticking out; user 2026-10-14): one undo step. No Hz bass yet: the line."""
        if self.drag is not None or self.bar is not None:
            return
        if not self.hz.reset_shape():
            self.use(START_TRACE)
        self.refresh()

    def use(self, pts):
        """The line replaced by these points (Reset, a starting shape): one undo step (none when it's the same)."""
        if self.drag is not None:
            return
        was, before = [list(p) for p in self.pts], self.hz.fx.state()
        self.pts = [list(p) for p in pts]
        self.save(before, was)
        self.draw()

    # ------------------------------------------------------------ the shapes box (user 2026-10-14)

    def fill_shapes(self):
        """The shapes box's list, read again each time it opens: the ready-made shapes, a divider, the saved lines
        newest first (each remembers its place in the file: "at")."""
        items = load_shapes()
        self.saved = [dict(s, at=k) for k, s in reversed(list(enumerate(items)))]
        set_names(self.shapes_box, [tr("hz.trace_" + k) for k in SHAPES], [s["name"] for s in self.saved])
        self.pick = None if self.pick and self.pick[0] == "saved" else self.pick  # (places may have moved)
        self.show_pick()

    def on_pick(self):
        k, n = self.shapes_box.current(), len(SHAPES)
        if k < 0:
            return
        if k < n:
            key = list(SHAPES)[k]
            self.pick, pts = ("ready", key), SHAPES[key]
        elif self.saved and k > n:
            self.pick, pts = ("saved", k - n - 1), self.saved[k - n - 1]["points"]
        else:
            return
        self.use(pts)
        self.show_pick()

    def show_pick(self):
        """The box shows the shape the line is (the one picked while the line is still it, else the first that is
        the same; empty when it's a line of its own); Delete only works on a saved one."""
        def points(pick):
            return SHAPES[pick[1]] if pick[0] == "ready" else self.saved[pick[1]]["points"]
        now = [list(p) for p in self.pts]
        if self.pick is None or points(self.pick) != now:
            self.pick = next((("ready", k) for k in SHAPES if SHAPES[k] == now), None) or next(
                (("saved", j) for j, s in enumerate(self.saved) if s["points"] == now), None)
        box = self.shapes_box
        if self.pick is None:
            box.set("")
        elif self.pick[0] == "ready":
            box.current(list(SHAPES).index(self.pick[1]))
        else:
            box.current(len(SHAPES) + 1 + self.pick[1])
        box.name_before = box.get()
        self.delete_btn.state(["!disabled"] if self.pick and self.pick[0] == "saved" else ["disabled"])

    def save_shape(self):
        """The line drawn saved under a name (asked: empty or a ready-made shape's name = asked again; a name already
        saved gets " (2)", " (3)"...)."""
        from window.settings_window import ask_name
        if self.drag is not None or self.bar is not None:
            return
        name = ask_name(self, tr("hz.trace_save_title"), tr("hz.trace_save_ask"),
                        check=lambda n: tr("hz.trace_built_in", name=n) if built_in_name(n) else None)
        if name is None:
            return
        items = load_shapes()
        name = free_shape_name(name, items)
        items.append({"name": name, "points": [list(p) for p in self.pts]})
        try:
            kept = save_shapes(items)
        except OSError as e:
            return messagebox.showerror(tr("hz.trace_title"), tr("hz.trace_not_saved", e=e), parent=self)
        self.fill_shapes()
        self.pick = ("saved", 0)  # (the newest: on top)
        self.show_pick()
        self.says.config(text=tr("hz.trace_kept_bad") if kept else tr("hz.trace_saved", name=name),
                         foreground=look.WARN if kept else look.INFO)

    def delete_shape(self):
        """The saved line picked in the box taken out of the file (asked first; only that one, even when another
        has the same name). The line drawn stays."""
        if not self.pick or self.pick[0] != "saved" or self.drag is not None or self.bar is not None:
            return
        s = self.saved[self.pick[1]]
        if not messagebox.askyesno(tr("hz.trace_delete_title"), tr("hz.trace_delete_ask", name=s["name"]),
                                   parent=self):
            return
        items = load_shapes()
        k = s["at"]
        if k >= len(items) or items[k] != {"name": s["name"], "points": s["points"]}:  # (the file changed since)
            k = next((i for i, t in enumerate(items) if t == {"name": s["name"], "points": s["points"]}), None)
        if k is not None:
            try:
                save_shapes(items[:k] + items[k + 1:])
            except OSError as e:
                messagebox.showerror(tr("hz.trace_title"), tr("hz.trace_not_saved", e=e), parent=self)
        self.pick = None
        self.fill_shapes()

    def save(self, before, was):
        """The shape drawn becomes the Hz bass's: one undo step of the Hz bass window (was: the points before).
        Nothing there to hold it yet (no step made): a step of this window's own (local_step)."""
        hz, app = self.hz, self.app
        if hz.length_target() is not None:  # (a Hz bass holds it: its main line or the layer's own, user 2026-10-14)
            self.local, self.local_redo = [], []
            return hz.put_trace([list(p) for p in self.pts])
        hz.extra = clean_extra(dict(hz.extra, trace=own_trace(self.pts)))
        if hz.fx.now() == before:
            return
        top = app.undo_stack[-1] if app.undo_stack else None
        hz.fx.tidy()
        hz.commit(tr("hz.step_wave"), copy.deepcopy(hz.tones), before)
        if (app.undo_stack[-1] if app.undo_stack else None) is not top:
            self.local, self.local_redo = [], []  # (a real step: the Hz bass holds the shape now)
        else:
            if self.local_for != self.waiting_for():
                self.local = []
            self.local.append(was)
            self.local_redo, self.local_for = [], self.waiting_for()

    def waiting_for(self):
        """What changes making no undo step are for: the shape shown (a spam shape without notes), or None (a new
        Hz bass not made yet)."""
        sh = self.hz.target()
        return id(sh) if sh is not None else None

    def local_step(self, redo=False):
        """Ctrl+Z / Ctrl+Y in this window: a change no Hz bass held taken back / done again. False when there's
        none (the main undo goes on)."""
        self.check_local()
        src, dst = (self.local_redo, self.local) if redo else (self.local, self.local_redo)
        if not src or self.drag is not None:
            return False
        dst.append([list(p) for p in self.pts])
        self.pts = src.pop()
        self.hz.extra = clean_extra(dict(self.hz.extra, trace=own_trace(self.pts)))
        self.hz.fx.tidy()
        self.draw()
        return True

    def close(self):
        self.cancel_drag()
        self.withdraw()
