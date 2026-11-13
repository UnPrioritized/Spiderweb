"""LFO… (stage I of the Hz bass Sketch extras, 2026-11-13; the plugin's LFO dialog): a window to design one wobble on
a note (the picked Sketch stretch, else the whole note; the other selected notes get it too, Same cycles / Same
speed): its wave shape (the six ready ones, or one DRAWN in the wave editor and kept in the shared shape bank,
user/hz_wobbles.json, also listed in the Sketch wobble's right-click menu), Upside down, depth + cycles, the depth
and speed lines (points, Alt curves, the depth presets, the speed lock) and the phase. Every change shows on the
notes at once (one undo step in all, like the Simplify window). Apply = a LIVE wobble (still editable in Sketch);
Bake = into bend points; Delete = the wobble there goes, the pitch line as it was; Cancel = as before, no step.
From the note's right-click menu (LFO…), the Sketch wobble's menu, or Ctrl+L on the Hz window."""

import copy
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

from files.about import USER
from files.lang import tr, trn
from files.mathexpr import fmt
from files.safefile import write_text
from notes.hz_settings import WAVE_DRAWN, clean_wave
from notes.hzbass import (NOTE_BEND_MOST, ON_POINT, WOBBLE_RATE, WOBBLE_RATE_MOST, WOBBLE_SHAPES, bend_of, bent_part,
                          clean_speed, depth_most, depth_pts, fit_wobbles, set_depth, speed_mean, wobble_points)
from roll.roll_shared import ALT, CTRL, SHIFT
from window import look
from window.widgets import REDO_KEYS, UNDO_KEYS, Tooltip, name_list, remember_place, set_names

BANK_FILE = os.path.join(USER, "hz_wobbles.json")  # the shape bank: drawn waves saved by name ({"shapes": [...]})
GRAB = 7  # px: how near a point a press takes it
CURVE_PX = 40  # px: Alt+dragging a piece this far up / down = a full curve
PAD = 10  # px round an editor's drawing area
WAVE_SNAP = (1 / 16, 1 / 8)  # a wave point snaps to these steps (x, y); Shift = free, like the whole Hz window
LINE_SNAP = (1 / 16, 0.05)  # ... a depth / speed line's point (user 2026-11-13: not the plugin's Shift = snap)
PHASE_STEPS = {"plain": 1 / 8, "shift": 1 / 64, "ctrl": 1 / 4}  # the phase ◀ ▶ buttons (the plugin's)
LOCKS = ("none", "start", "end", "both")  # the speed lock's choices (hz_settings.SPEED_LOCKS + none)
DEPTH_MOST = 48.0  # keys: the Depth box's top (the plugin's); a deeper wobble made on the piano roll is shown as it is
NEW_DEPTH = 1.0  # keys: a new wobble's depth
EDITOR_W, WAVE_H, LINE_H = 300, 120, 54  # px at 100 %: the editors' sizes


# ------------------------------------------------------------ the shape bank

def read_bank():
    """(the saved drawn waves [{"name", "points"}] in saving order, whether the file is there but can't be read)."""
    from window.hz_trace import read_named
    return read_named(BANK_FILE, clean_wave)


def load_bank():
    """The saved drawn waves, newest LAST (saving order); none when the file can't be read."""
    return read_bank()[0]


def save_bank(items):
    """Writes the bank (OSError when it can't). A file there that can't be read is kept as hz_wobbles.json.bad
    first; True when it was."""
    from window.hz_trace import save_named
    return save_named(BANK_FILE, items, clean_wave)


def ready_name(name):
    """True when name is one of the ready shapes' (as shown or its id; capitals or not)."""
    low = name.casefold()
    return any(low in (k.casefold(), tr("hz.wobble_" + k).casefold()) for k in WOBBLE_SHAPES)


def wave_points(run):
    """Run's wave as drawn points (for the wave editor): a drawn one's own, a ready shape's corners (sine, pluck: a
    few points with the curves nearest it; random: shown as a sine, its heights change every wave)."""
    shape = run.get("shape", "sine")
    if shape == WAVE_DRAWN and run.get("wave"):
        return [list(p) for p in run["wave"]]
    if shape == "triangle":
        return [[0.0, 0.0], [0.25, 1.0], [0.75, -1.0], [1.0, 0.0]]
    if shape == "saw":
        return [[0.0, 0.0], [0.5, 1.0], [0.5, -1.0], [1.0, 0.0]]
    if shape == "square":
        return [[0.0, 1.0], [0.5, 1.0], [0.5, -1.0], [1.0, -1.0]]
    return [list(p) for p in _CURVED["pluck" if shape == "pluck" else "sine"]()]


def _fitted(xs, f):
    """Points at xs on wave f (an array function), each piece the single curve nearest f over it (sine, pluck: few
    points to drag, close to the real shape)."""
    from window.hz_sketch import nearest_bend
    s = np.linspace(0.0, 1.0, 33)
    out = []
    for a, b in zip(xs, xs[1:]):
        va, vb = float(f(np.array([a]))[0]), float(f(np.array([b]))[0])
        p = [a, round(va, 6)]
        if abs(vb - va) > 1e-9:
            c = nearest_bend((f(a + (b - a) * s) - va) / (vb - va), s)
            if c:
                p.append(c)
        out.append(p)
    return out + [[xs[-1], round(float(f(np.array([xs[-1]]))[0]), 6)]]


_CURVED = {"sine": lambda: _fitted([0.0, 0.25, 0.5, 0.75, 1.0], lambda x: np.sin(2 * np.pi * x)),
           "pluck": lambda: _fitted([0.0, 1.0], lambda x: (1.0 - x) ** 3)}


# ------------------------------------------------------------ a line drawn with the mouse

class LineEdit:
    """A small canvas where a line of points [[x 0..1, y(, curve)], ...] (first at 0, last at 1) is drawn: a press on
    empty space = a new point there (held: drag it), a press on a point = drag it (the ends only up / down), right-click
    an inner point = gone, Alt+drag a piece (Ctrl+Shift too) = curve it; points snap to `snap` (x, y), Shift = free.
    y from lo to hi (centre: a faint line there). get() = the points now; put(points) on every change; before() at a
    press, after() at its let go (a drag's start / end: the window keeps its numbers steady meanwhile)."""

    def __init__(self, canvas, s, lo, hi, snap, colour, get, put, before=None, centre=None, after=None):
        self.c, self.s, self.lo, self.hi, self.snap, self.colour = canvas, s, lo, hi, snap, colour
        self.get, self.put, self.before, self.centre, self.after = get, put, before, centre, after
        self.drag = None
        canvas.bind("<Configure>", lambda e: self.draw())
        canvas.bind("<ButtonPress-1>", self.press)
        canvas.bind("<B1-Motion>", self.motion)
        canvas.bind("<ButtonRelease-1>", self.release)
        canvas.bind("<ButtonPress-3>", self.right)

    def box(self):
        p = PAD * self.s
        return p, p, max(p + 10, self.c.winfo_width() - p), max(p + 10, self.c.winfo_height() - p)

    def xy(self, x, y):
        x0, y0, x1, y1 = self.box()
        return x0 + x * (x1 - x0), y1 - (y - self.lo) / (self.hi - self.lo) * (y1 - y0)

    def at(self, px, py):
        x0, y0, x1, y1 = self.box()
        return (px - x0) / (x1 - x0), self.lo + (y1 - py) / (y1 - y0) * (self.hi - self.lo)

    def hit(self, px, py):
        """The point under (px, py) (the nearest within GRAB px; the later of a jump's two when on both), or None."""
        best, far = None, (GRAB * self.s) ** 2
        for j, p in enumerate(self.get()):
            x, y = self.xy(p[0], p[1])
            d = (x - px) ** 2 + (y - py) ** 2
            if d <= far:
                best, far = j, d
        return best

    def piece(self, px):
        """The piece (its first point's number) under x px, or None."""
        pts = self.get()
        u = self.at(px, 0)[0]
        return next((j for j in range(len(pts) - 1) if pts[j][0] <= u <= pts[j + 1][0]
                     and pts[j + 1][0] > pts[j][0]), None)

    def press(self, e):
        self.c.focus_set()
        if self.before:
            self.before()
        pts = [list(p) for p in self.get()]
        if bool(e.state & ALT) or e.state & (CTRL | SHIFT) == CTRL | SHIFT:  # (curve a piece)
            j = self.piece(e.x)
            if j is not None and abs(pts[j + 1][1] - pts[j][1]) > 1e-9:
                self.drag = {"curve": j, "y0": e.y, "b0": bend_of(pts[j]) or 0.0,
                             "up": 1.0 if pts[j + 1][1] > pts[j][1] else -1.0}
            return
        j = self.hit(e.x, e.y)
        if j is None:  # (a new point there, held)
            u, v = self.place(e)
            u = min(1.0, max(0.0, u))
            j = next((k for k in range(1, len(pts)) if pts[k][0] > u), len(pts) - 1)
            if j == 0 or not 0.0 < u < 1.0 or len(pts) >= 128:
                return
            pts.insert(j, [u, v])
            self.put(pts)
        self.drag = {"j": j}

    def place(self, e):
        u, v = self.at(e.x, e.y)
        if not e.state & SHIFT:
            u = round(u / self.snap[0]) * self.snap[0]
            v = round(v / self.snap[1]) * self.snap[1]
        return round(u, 6), round(min(self.hi, max(self.lo, v)), 6)

    def motion(self, e):
        d = self.drag
        if not d:
            return
        pts = [list(p) for p in self.get()]
        if "curve" in d:
            j = d["curve"]
            b = d["b0"] + (d["y0"] - e.y) / (CURVE_PX * self.s) * d["up"]
            from window.hz_sketch import curve_to
            curve_to(pts, j, b, False)
            return self.put(pts)
        j = d["j"]
        if j >= len(pts):
            return
        u, v = self.place(e)
        if 0 < j < len(pts) - 1:  # (inner points: between their neighbours; ends stay at 0 / 1)
            pts[j][0] = min(pts[j + 1][0], max(pts[j - 1][0], u))
        pts[j][1] = v
        self.put(pts)

    def release(self, e):
        held, self.drag = self.drag, None
        if self.after:
            self.after(held)

    def right(self, e):
        if self.drag:  # (a point held: right-clicks do nothing, the Hz window's rule; hunt 2026-11-13: one taken out
            return     # under the held one made the next point follow the mouse)
        pts = [list(p) for p in self.get()]
        j = self.hit(e.x, e.y)
        if j is not None and 0 < j < len(pts) - 1:
            if self.before:
                self.before()
            pts = [list(p) for p in self.get()]
            del pts[j]
            self.put(pts)

    def draw(self):
        c = self.c
        c.delete("all")
        x0, y0, x1, y1 = self.box()
        c.create_rectangle(x0, y0, x1, y1, outline=look.CHART_FRAME)
        for k in range(1, 4):  # (a faint grid: quarters)
            x = x0 + (x1 - x0) * k / 4
            c.create_line(x, y0, x, y1, fill=look.CHART_GRID)
        if self.centre is not None:
            y = self.xy(0, self.centre)[1]
            c.create_line(x0, y, x1, y, fill=look.CHART_GRID_STRONG)
        pts = self.get()
        line = []
        for a, b in zip(pts, pts[1:]):
            if b[0] - a[0] <= 1e-12:  # (a jump: straight up / down)
                line += [self.xy(*a[:2]), self.xy(*b[:2])]
                continue
            t = np.linspace(0.0, 1.0, 25 if bend_of(a) else 2)
            g = bent_part(t, np.full(t.shape, bend_of(a) or 0.0))
            line += [self.xy(a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * w) for q, w in zip(t, g)]
        if len(line) >= 2:
            c.create_line(*[v for p in line for v in p], fill=self.colour, width=max(1, round(2 * self.s)))
        r = 3 * self.s
        for p in pts:
            x, y = self.xy(p[0], p[1])
            c.create_rectangle(x - r, y - r, x + r, y + r, fill=look.CHART_POINT, outline=self.colour)


# ------------------------------------------------------------ the window

class LfoWindow(tk.Toplevel):
    """The LFO window for one wobble (`run`, a copy: changed here). beats = the stretch's length (cycles shown);
    notes = how many notes change; apply(run) on every change (the live preview). .result = "apply" / "bake" /
    "delete" / None (Cancel / Escape / closed)."""

    def __init__(self, hz, run, beats, notes, apply):
        super().__init__(hz)
        self.hz, self.run, self.beats, self.apply_to = hz, run, beats, apply
        self.app = hz.app  # (the name box asks it for the sizes: hunt 2026-11-13, Save… did nothing without it)
        self.result = None
        self.depth_scale = None  # (a depth line point held: the Depth it's shown as shares of, kept till let go)
        self.speed_waves = None  # (a speed line point held: the waves to keep, from the press)
        self.kept_waves = None  # (the waves the last time the speed line wasn't stopped: a stopped line keeps them)
        s = self.s = hz.s
        self.title(tr("hz.lfo_title"))
        self.transient(hz)
        self.resizable(False, False)
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        r = 0
        # shape row: the six ready shapes + "Drawn" (on while the wave is one drawn here), Upside down
        ttk.Label(f, text=tr("hz.lfo_shape")).grid(row=r, column=0, sticky="nw", pady=(2, 0))
        row = ttk.Frame(f)
        row.grid(row=r, column=1, sticky="w")
        self.shape = tk.StringVar(self, value=run.get("shape", "sine"))
        self.shape_btns = {}
        for kind in WOBBLE_SHAPES + (WAVE_DRAWN,):
            b = ttk.Radiobutton(row, text=tr(f"hz.wobble_{kind}"), value=kind, variable=self.shape,
                                style="Toolbutton", command=lambda kind=kind: self.pick_shape(kind), takefocus=False)
            b.pack(side="left", padx=(0, 2))
            self.shape_btns[kind] = b
        Tooltip(self.shape_btns[WAVE_DRAWN], tr("hz.lfo_drawn_tip"))
        self.flip = tk.BooleanVar(self, value=bool(run.get("flip")))
        ttk.Checkbutton(row, text=tr("hz.wobble_flip"), variable=self.flip, command=self.changed,
                        takefocus=False).pack(side="left", padx=(8, 0))
        r += 1
        # the wave editor: one wave
        c = self.wave_canvas = tk.Canvas(f, width=round(EDITOR_W * s), height=round(WAVE_H * s),
                                         background=look.CHART_BG, highlightthickness=0, takefocus=True)
        c.grid(row=r, column=1, sticky="w", pady=(6, 0))
        ttk.Label(f, text=tr("hz.lfo_wave")).grid(row=r, column=0, sticky="nw", pady=(6, 0))
        self.wave_edit = LineEdit(c, s, -1.0, 1.0, WAVE_SNAP, look.HZ_WOBBLE, lambda: wave_points(self.run),
                                  self.put_wave, centre=0.0)
        Tooltip(c, tr("hz.lfo_wave_tip"))
        r += 1
        # the shape bank: saved drawn waves
        bank = ttk.Frame(f)
        bank.grid(row=r, column=1, sticky="w", pady=(4, 0))
        self.bank_box = name_list(ttk.Combobox(bank, state="readonly", width=18, takefocus=False,
                                               postcommand=self.fill_bank))
        self.bank_box.pack(side="left")
        self.bank_box.bind("<<ComboboxSelected>>", lambda e: self.pick_bank())
        Tooltip(self.bank_box, tr("hz.lfo_bank_tip"))
        b = ttk.Button(bank, text=tr("hz.lfo_save"), command=self.save_wave, takefocus=False)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("hz.lfo_save_tip"))
        self.delete_btn = ttk.Button(bank, text=tr("hz.lfo_bank_delete"), command=self.delete_wave, takefocus=False)
        self.delete_btn.pack(side="left", padx=(6, 0))
        self.bank, self.bank_pick = [], None
        r += 1
        # depth + cycles
        nums = ttk.Frame(f)
        nums.grid(row=r, column=1, sticky="w", pady=(8, 0))
        ttk.Label(f, text=tr("hz.lfo_depth")).grid(row=r, column=0, sticky="w", pady=(8, 0))
        self.depth = tk.StringVar(self, value=fmt(round(depth_most(run), 2)))
        box = ttk.Spinbox(nums, from_=0.0, to=DEPTH_MOST, increment=0.1, width=6, textvariable=self.depth,
                          command=self.put_depth)
        box.pack(side="left")
        box.bind("<Return>", lambda e: self.put_depth())
        box.bind("<FocusOut>", lambda e: self.put_depth())
        self.depth_box = box
        ttk.Label(nums, text=tr("hz.lfo_keys"), foreground=look.HINT).pack(side="left", padx=(4, 14))
        ttk.Label(nums, text=tr("hz.lfo_cycles")).pack(side="left")
        self.cycles = tk.StringVar(self, value=fmt(round(self.waves(), 2)))
        box = ttk.Spinbox(nums, from_=0.5, to=WOBBLE_RATE_MOST * max(beats, 1e-9), increment=0.5, width=6,
                          textvariable=self.cycles, command=self.put_cycles)
        box.pack(side="left", padx=(4, 0))
        box.bind("<Return>", lambda e: self.put_cycles())
        box.bind("<FocusOut>", lambda e: self.put_cycles())
        self.cycles_box = box
        self.per_beat = ttk.Label(nums, text="", foreground=look.HINT)
        self.per_beat.pack(side="left", padx=(4, 0))
        r += 1
        if notes > 1:  # (several notes: the plugin's SAME CYCLES / SAME SPEED; shared with the Sketch menu's tick)
            same = ttk.Frame(f)
            same.grid(row=r, column=1, sticky="w", pady=(4, 0))
            self.same = tk.StringVar(self, value=hz.wobble_same)
            for k in ("cycles", "speed"):
                ttk.Radiobutton(same, text=tr(f"hz.lfo_same_{k}"), value=k, variable=self.same,
                                command=self.put_same, takefocus=False).pack(side="left", padx=(0, 10))
            ttk.Label(same, text=trn("n.notes", notes, n=notes), foreground=look.HINT).pack(side="left")
            r += 1
        # the depth line: shares of the depth box's depth
        ttk.Label(f, text=tr("hz.lfo_depth_line")).grid(row=r, column=0, sticky="nw", pady=(8, 0))
        c = self.depth_canvas = tk.Canvas(f, width=round(EDITOR_W * s), height=round(LINE_H * s),
                                          background=look.CHART_BG, highlightthickness=0, takefocus=True)
        c.grid(row=r, column=1, sticky="w", pady=(8, 0))
        self.depth_edit = LineEdit(c, s, 0.0, 1.0, LINE_SNAP, look.HZ_WOBBLE, self.depth_shape, self.put_depth_shape,
                                   before=self.depth_press, after=self.depth_release)
        Tooltip(c, tr("hz.lfo_line_tip"))
        r += 1
        presets = ttk.Frame(f)
        presets.grid(row=r, column=1, sticky="w", pady=(2, 0))
        from window.hz_sketch import DEPTH_PRESETS
        for k in DEPTH_PRESETS:
            ttk.Button(presets, text=tr(f"hz.depth_{k}"), style="Toolbutton", takefocus=False,
                       command=lambda k=k: self.put_preset(k)).pack(side="left", padx=(0, 2))
        r += 1
        # the speed line + its lock
        ttk.Label(f, text=tr("hz.lfo_speed_line")).grid(row=r, column=0, sticky="nw", pady=(8, 0))
        c = self.speed_canvas = tk.Canvas(f, width=round(EDITOR_W * s), height=round(LINE_H * s),
                                          background=look.CHART_BG, highlightthickness=0, takefocus=True)
        c.grid(row=r, column=1, sticky="w", pady=(8, 0))
        self.speed_edit = LineEdit(c, s, 0.0, 1.0, LINE_SNAP, look.HZ_SPEED,
                                   lambda: self.run.get("speed") or [[0.0, 1.0], [1.0, 1.0]], self.put_speed,
                                   before=self.speed_press, after=self.speed_release)
        Tooltip(c, tr("hz.lfo_speed_tip"))
        r += 1
        lock = ttk.Frame(f)
        lock.grid(row=r, column=1, sticky="w", pady=(2, 0))
        ttk.Label(lock, text=tr("hz.lfo_lock")).pack(side="left")
        names = [tr(f"hz.lfo_lock_{k}") for k in LOCKS]
        self.lock = ttk.Combobox(lock, values=names, state="readonly", width=max(map(len, names)) + 1, takefocus=False)
        self.lock.current(LOCKS.index(run.get("lock", "none")))
        self.lock.pack(side="left", padx=(4, 0))
        self.lock.bind("<<ComboboxSelected>>", lambda e: self.put_lock())
        Tooltip(self.lock, tr("hz.lfo_lock_tip"))
        ttk.Button(lock, text=tr("hz.wobble_even"), command=self.even_speed, takefocus=False).pack(side="left",
                                                                                                    padx=(8, 0))
        r += 1
        # phase
        ttk.Label(f, text=tr("hz.lfo_phase")).grid(row=r, column=0, sticky="w", pady=(8, 0))
        ph = ttk.Frame(f)
        ph.grid(row=r, column=1, sticky="w", pady=(8, 0))
        for text, sign in (("◀", -1), ("▶", 1)):
            b = ttk.Button(ph, text=text, width=3, takefocus=False)
            b.pack(side="left", padx=(0, 2))
            # (only when let go over the button, like any button: hunt 2026-11-13)
            b.bind("<ButtonRelease-1>", lambda e, sign=sign: self.nudge(sign, e.state)
                   if 0 <= e.x < e.widget.winfo_width() and 0 <= e.y < e.widget.winfo_height() else None)
            Tooltip(b, tr("hz.lfo_phase_tip"))
        self.phase_says = ttk.Label(ph, text="", foreground=look.HINT)
        self.phase_says.pack(side="left", padx=(6, 0))
        r += 1
        self.says = ttk.Label(f, text="", foreground=look.INFO)
        self.says.grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))
        r += 1
        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=2, sticky="e", pady=(8, 0))
        for key, what, tip in (("hz.lfo_apply", "apply", "hz.lfo_apply_tip"), ("hz.lfo_bake", "bake", "hz.lfo_bake_tip"),
                               ("hz.lfo_delete", "delete", "hz.lfo_delete_tip")):
            b = ttk.Button(btns, text=tr(key), command=lambda what=what: self.done(what))
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, tr(tip))
        ttk.Button(btns, text=tr("hz.live_cancel"), command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: None if isinstance(e.widget, ttk.Spinbox) else self.done("apply"))
        self.bind("<Escape>", lambda e: self.destroy())
        for k in UNDO_KEYS + REDO_KEYS:  # (the main undo would change the notes under the window)
            self.bind(k, lambda e: "break")
        self.fill_bank()
        self.show()
        self.geometry(f"+{hz.winfo_pointerx() - 60}+{hz.winfo_pointery() - 60}")
        remember_place(self, "hz_lfo")

    # ---- reading the run

    def waves(self):
        """The run's waves over its stretch (its phase not counted)."""
        return self.run["rate"] * self.beats * speed_mean(self.run)

    def depth_shape(self):
        """The depth line as shares (0..1) of its deepest depth (while a point is held: of the Depth at the press, so
        the held point stays under the mouse; hunt 2026-11-13: worked out anew each move, the wobble melted away)."""
        most = self.depth_scale or depth_most(self.run)
        if most <= 0:
            return [[0.0, 1.0], [1.0, 1.0]]
        return [[p[0], round(p[1] / most, 6), *p[2:3]] for p in depth_pts(self.run)]

    def show(self):
        """Everything shown again from the run."""
        run = self.run
        self.shape.set(run.get("shape", "sine"))
        self.shape_btns[WAVE_DRAWN].state(["!disabled"] if run.get("shape") == WAVE_DRAWN else ["disabled"])
        self.flip.set(bool(run.get("flip")))
        if self.focused() is not self.depth_box:
            self.depth.set(fmt(round(self.depth_scale or depth_most(run), 2)))
        if speed_mean(run) > 1e-6 and self.speed_waves is None:
            self.kept_waves = self.waves()
        if self.focused() is not self.cycles_box:
            self.cycles.set(fmt(round(self.waves(), 2)))
        self.per_beat.config(text=tr("hz.lfo_per_beat", rate=fmt(round(run["rate"], 2))))
        self.lock.current(LOCKS.index(run.get("lock", "none")))
        nudge = run.get("nudge", 0.0)
        self.phase_says.config(text=tr("hz.lfo_phase_now", pct=fmt(round(nudge * 100, 1))) if nudge else
                               tr("hz.lfo_phase_none"))
        for ed in (self.wave_edit, self.depth_edit, self.speed_edit):
            ed.draw()
        self.show_bank()

    def changed(self):
        """A control changed: the run from the controls, then shown on the notes."""
        if self.flip.get():
            self.run["flip"] = True
        else:
            self.run.pop("flip", None)
        self.live()

    def live(self):
        if self.result is not None or not self.winfo_exists():  # (a box left while the window closes)
            return
        got = self.apply_to(self.run)
        if got:  # (as made: whole half waves; the Cycles box shows what's heard, hunt 2026-11-13)
            for k in ("rate",) if self.speed_waves is not None else ("rate", "speed"):  # (the line held: stays put)
                if k in got:
                    self.run[k] = copy.deepcopy(got[k])
                else:
                    self.run.pop(k, None)
        self.show()

    # ---- the controls

    def pick_shape(self, kind):
        run = self.run
        if kind == WAVE_DRAWN:  # (only on while drawn: a click on it changes nothing)
            return self.show()
        run.pop("wave", None)
        if kind == "sine":
            run.pop("shape", None)
        else:
            run["shape"] = kind
        if kind == "random" and "seed" not in run:
            import random
            from notes.hzbass import WOBBLE_SEED_STEP
            run["seed"] = random.randrange(1, 1 << 14) * WOBBLE_SEED_STEP
        self.live()

    def put_wave(self, pts):
        """The wave editor changed: the run's wave is the drawn one now."""
        got = clean_wave(pts)
        if got:
            self.run.update(shape=WAVE_DRAWN, wave=got)
            self.live()

    def put_depth(self):
        try:
            v = float(self.depth.get().replace(",", "."))
        except ValueError:
            return self.show()
        v = min(NOTE_BEND_MOST, max(0.0, v))
        shape = self.depth_shape()
        set_depth(self.run, [[p[0], round(p[1] * v, 4), *p[2:]] for p in shape])
        self.live()

    def depth_press(self):
        self.depth_scale = depth_most(self.run) or NEW_DEPTH

    def depth_release(self, held):
        if self.depth_scale is not None:
            self.depth_scale = None
            self.show()  # (the Depth box = the line's deepest now; the line's top at it again)

    def speed_press(self):
        self.speed_waves = self.waves() if speed_mean(self.run) > 1e-6 else self.kept_waves

    def speed_release(self, held):
        self.speed_waves = None

    def put_depth_shape(self, pts):
        most = self.depth_scale or depth_most(self.run) or NEW_DEPTH
        pts = [[min(1.0, max(0.0, p[0])), min(1.0, max(0.0, p[1])), *p[2:3]] for p in pts]
        set_depth(self.run, [[p[0], round(p[1] * most, 4), *p[2:]] for p in pts])
        self.live()

    def put_preset(self, k):
        from window.hz_sketch import DEPTH_PRESETS
        most = depth_most(self.run) or NEW_DEPTH
        set_depth(self.run, [[p[0], round(p[1] * most, 4), *p[2:]] for p in DEPTH_PRESETS[k]])
        self.live()

    def put_cycles(self):
        try:
            v = float(self.cycles.get().replace(",", "."))
        except ValueError:
            return self.show()
        if v <= 0 or self.beats <= 1e-12 or speed_mean(self.run) <= 1e-6:  # (a stopped speed line: no waves to set,
            return self.show()                                               # hunt 2026-11-13: divided by zero)
        self.run["rate"] = round(min(WOBBLE_RATE_MOST, max(0.01, v / (self.beats * speed_mean(self.run)))), 9)
        self.live()

    def put_same(self):
        self.hz.wobble_same = self.same.get()
        self.live()

    def put_speed(self, pts):
        # (the waves from the press (a drag), else from before (a stopped line keeps the last ones): hunt 2026-11-13,
        # down to the floor and back up lost them)
        waves = self.speed_waves if self.speed_waves is not None else (
            self.waves() if speed_mean(self.run) > 1e-6 else self.kept_waves)
        got = clean_speed(pts)
        if got:
            self.run["speed"] = got
        else:
            self.run.pop("speed", None)
        if speed_mean(self.run) > 1e-6 and waves:  # (the same waves over the stretch, like the Sketch strip's points)
            self.run["rate"] = round(min(WOBBLE_RATE_MOST, max(0.01, waves / (self.beats * speed_mean(self.run)))), 9)
        self.live()

    def even_speed(self):
        self.put_speed([[0.0, 1.0], [1.0, 1.0]])

    def put_lock(self):
        k = LOCKS[self.lock.current()]
        if k == "none":
            self.run.pop("lock", None)
        else:
            self.run["lock"] = k
        self.live()

    def nudge(self, sign, state):
        step = PHASE_STEPS["ctrl" if state & CTRL else "shift" if state & SHIFT else "plain"]
        n = round((self.run.get("nudge", 0.0) + sign * step) % 1.0, 9) % 1.0
        if n:
            self.run["nudge"] = n
        else:
            self.run.pop("nudge", None)
        self.live()

    # ---- the shape bank

    def fill_bank(self):
        """The bank's list, read again each time it opens (newest first; each remembers its place in the file)."""
        items = load_bank()
        self.bank = [dict(t, at=k) for k, t in reversed(list(enumerate(items)))]
        set_names(self.bank_box, [], [t["name"] for t in self.bank])
        self.show_bank()

    def show_bank(self):
        """The box shows the saved wave the run's drawn wave is (empty when none); Delete only on a picked saved one."""
        now = self.run.get("wave") if self.run.get("shape") == WAVE_DRAWN else None
        k = next((j for j, t in enumerate(self.bank) if now and t["points"] == now), None)
        self.bank_pick = k
        if k is None:
            self.bank_box.set("")
        else:
            self.bank_box.current(k)
        self.bank_box.name_before = self.bank_box.get()
        self.delete_btn.state(["!disabled"] if k is not None else ["disabled"])

    def pick_bank(self):
        k = self.bank_box.current()
        if not 0 <= k < len(self.bank):
            return
        self.run.update(shape=WAVE_DRAWN, wave=copy.deepcopy(self.bank[k]["points"]))
        self.live()

    def save_wave(self):
        """The wave as it is (a ready shape too: as drawn points) saved in the bank under a name."""
        from window.hz_trace import free_shape_name
        from window.settings_window import ask_name
        name = ask_name(self, tr("hz.lfo_save_title"), tr("hz.lfo_save_ask"),
                        check=lambda n: tr("hz.lfo_ready_name", name=n) if ready_name(n) else None)
        if name is None:
            return
        pts = clean_wave(wave_points(self.run))
        items = load_bank()
        name = free_shape_name(name, items)
        items.append({"name": name, "points": pts})
        try:
            bad = save_bank(items)
        except OSError as e:
            return messagebox.showerror(tr("hz.lfo_title"), tr("hz.lfo_not_saved", e=e), parent=self)
        self.run.update(shape=WAVE_DRAWN, wave=pts)
        self.fill_bank()
        self.live()
        self.says.config(text=tr("hz.lfo_kept_bad") if bad else tr("hz.lfo_saved", name=name),
                         foreground=look.WARN if bad else look.INFO)

    def delete_wave(self):
        """The picked saved wave taken out of the bank (asked first); the wobble keeps its wave."""
        if self.bank_pick is None:
            return
        t = self.bank[self.bank_pick]
        if not messagebox.askyesno(tr("hz.lfo_delete_title"), tr("hz.lfo_delete_ask", name=t["name"]), parent=self):
            return
        items = load_bank()
        k = t["at"]
        if k >= len(items) or items[k] != {"name": t["name"], "points": t["points"]}:  # (the file changed since)
            k = next((j for j, s in enumerate(items) if s == {"name": t["name"], "points": t["points"]}), None)
        if k is not None:
            try:
                save_bank(items[:k] + items[k + 1:])
            except OSError as e:
                messagebox.showerror(tr("hz.lfo_title"), tr("hz.lfo_not_saved", e=e), parent=self)
        self.fill_bank()

    def focused(self):
        try:
            return self.focus_get()
        except (KeyError, tk.TclError):  # (a dropdown's own list has the keyboard)
            return None

    def done(self, what):
        if self.focused() is self.depth_box:  # (a number typed, Enter not pressed)
            self.put_depth()
        elif self.focused() is self.cycles_box:
            self.put_cycles()
        self.result = what
        self.destroy()


# ------------------------------------------------------------ the Hz window's part

class HzLfo:
    lfo_top = None

    def open_lfo(self, i=None):
        """The LFO window for note i (None: the picked Sketch stretch's note, else the first selected note): on the
        picked stretch when it's that note's, else on the whole note; the other selected notes too when i is one of
        them. Live on the notes while open; one undo step (Apply / Bake / Delete), none on Cancel."""
        if self.lfo_top is not None or self.drag:
            return
        pick = self.picked_piece() if self.sketching() else None
        if i is None:
            i = pick[0] if pick else (min(self.sel) if self.sel else None)
        if i is None or i >= len(self.tones):
            self.bell()
            return self.say(tr("hz.lfo_none"))
        n = self.tones[i]
        u0, u1 = (pick[2], pick[3]) if pick and pick[0] == i else (0.0, 1.0)
        nid, many = n["id"], i in self.sel and len(self.sel) > 1
        got = self.wobble_run(n, u0, u1)  # (none there: a new one; shorter ones inside it are replaced on Apply)
        if not got:  # (a shorter one inside: the window starts from ITS settings, on the whole stretch; hunt
            # 2026-11-13: a fresh sine replaced it the moment the window opened)
            inside = [w for w in n.get("wobble") or () if w["at"][0] < u1 - ON_POINT and w["at"][1] > u0 + ON_POINT]
            if inside:
                got = {k: v for k, v in max(inside, key=lambda w: w["at"][1] - w["at"][0]).items()
                       if k not in ("at", "phase")}
        run = copy.deepcopy(got) if got else {"depth": [NEW_DEPTH, NEW_DEPTH], "rate": WOBBLE_RATE}
        run["at"] = [u0, u1]
        beats = (u1 - u0) * n["len"]
        before, redo = copy.deepcopy(self.tones), self.app.redo_stack[:]
        self.sel_before = (before, self.sel_state())
        state = {"pushed": False}
        sel0 = set(self.sel)

        def put(what, r):
            """The notes from `before` with run r put on (what: "live" / "apply" / "bake" / "delete")."""
            from window.hz_bend import tidy_bend
            from window.hz_sketch import bake_wobble, put_run
            self.tones[:] = copy.deepcopy(before)  # (the same list: the selection keeps its numbers)
            self.sel = set(sel0)
            k = next(j for j, m in enumerate(self.tones) if m.get("id") == nid)
            m = self.tones[k]
            if what == "delete":
                m["wobble"] = [w for w in m.get("wobble") or ()
                               if not (w["at"][0] < u1 - ON_POINT and w["at"][1] > u0 + ON_POINT)]
                spread = self.spread_pick(k, (u0, u1), gone=True) if many else []
            else:
                put_run(m, (u0, u1), dict(copy.deepcopy(r), at=[u0, u1]))
                wobble_points(m)
                spread = self.spread_pick(k, (u0, u1)) if many else []
            if what == "bake":
                for j, a, b in [(k, u0, u1)] + spread:
                    t = self.tones[j]
                    fit_wobbles(t)  # (as it would be once made: whole half waves)
                    w = self.wobble_run(t, a, b)
                    if w:
                        t["wobble"] = [x for x in t["wobble"] if x is not w]
                        t["bend"] = bake_wobble(t, w)
            for j in {k} | {j for j, _, _ in spread}:
                tidy_bend(self.tones[j], True)
            return k

        def apply(r, what="live"):
            """The notes with run r (what as in put); the run as made back (whole half waves) or None."""
            was = copy.deepcopy(self.tones)
            put(what, r)
            if self.tones != was or not state["pushed"] and self.tones != before:
                top = self.app.last_step()
                self.sel_before = (before, self.sel_state())
                self.commit(tr("hz.step_lfo"), copy.deepcopy(before), push=not state["pushed"])
                if self.app.last_step() is not top:
                    state["pushed"], state["step"] = True, self.app.last_step()
            else:
                self.redraw()
            m = next((t for t in self.tones if t.get("id") == nid), None)
            return self.wobble_run(m, u0, u1) if m is not None and what == "live" else None

        def drop():
            """Cancel: the notes as before, the window's step taken out, but ONLY while it's still the newest (a
            safety net, hunt 2026-11-13: an undo behind the window, then Cancel took out an older step); else only the
            notes put back, no step taken out or made."""
            if self.app.last_step() is state.get("step"):
                return self.live_drop(before, redo)
            if self.tones != before:
                self.tones[:] = copy.deepcopy(before)
                self.sel = set(sel0)
                self.sel_before = (before, self.sel_state())
                self.commit(tr("hz.step_lfo"), copy.deepcopy(before), push=False)

        win = self.lfo_top = LfoWindow(self, run, beats, len(self.sel) if many else 1, apply)
        win.grab_set()
        win.live()  # (shown on the notes at once: a new wobble there too; the window shows it as made)
        win.wait_window()
        self.lfo_top = None
        what = win.result
        if what is None:  # (Cancel: as before, no step)
            if state["pushed"]:
                drop()
            return
        apply(win.run, what)
        if self.tones == before and state["pushed"]:  # (nothing changed after all)
            drop()
            return
        if what == "apply" and self.sketching():  # (its depth line shows on the stretch)
            self.sketch_pick = (nid, u0, u1)
        else:
            self.sketch_pick = None
        self.redraw()
