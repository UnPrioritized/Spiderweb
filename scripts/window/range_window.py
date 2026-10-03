"""The spam gate Range's graph (the side panel's Graph… button; gaterange.py): across the shape (left to right, or
low to high keys) from the first gate to the second. Drag points, click to add one, right-click one to delete it;
presets. The pale steps behind the line = the whole-tick gates the notes really get. Changes show on the piano roll
at once; OK keeps them as one undo step, Cancel / Esc puts the graph back. Ctrl+Z / Ctrl+Y step through the changes
made in the window."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import fmt
from notes.gaterange import STRAIGHT, gate_steps
from window.widgets import LocalUndo


def _power(k):
    return [[i / 8, (i / 8) ** k] for i in range(9)]


PRESETS = [("range_window.straight", STRAIGHT), ("range_window.short_longer", _power(2)),
           ("range_window.long_longer", _power(0.5)), ("range_window.hill", [[0, 0], [0.5, 1], [1, 0]]),
           ("range_window.valley", [[0, 1], [0.5, 0], [1, 1]])]
U_SNAP, Y_SNAP = 1 / 40, 1 / 20  # dragging moves points in these steps (Shift = free)


class RangeGraph(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("range_window.title"))
        self.transient(app)
        self.resizable(False, False)
        s = self.s = app.scale
        self.w, self.h = int(440 * s), int(220 * s)
        self.ml, self.mr, self.mt, self.mb = int(56 * s), int(12 * s), int(10 * s), int(22 * s)
        self.tgts = [t for t in app.custom_targets() if t.get("range")]
        self.before = json.dumps(app.shapes)
        self.old = [json.loads(json.dumps(t["range"])) for t in self.tgts]
        self.pts = [list(p) for p in self.tgts[0]["range"]["graph"]]
        self.drag, self.hover = None, None
        self.recount()
        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        self.info = ttk.Label(box, text="")
        self.info.pack(anchor="w")
        cv = self.canvas = tk.Canvas(box, width=self.w, height=self.h, bg="#ffffff", highlightthickness=1,
                                     highlightbackground="#a0a0a0", cursor="crosshair")
        cv.pack(pady=4)
        row = ttk.Frame(box)
        row.pack(fill="x")
        for key, pts in PRESETS:
            ttk.Button(row, text=tr(key), command=lambda pts=pts: self.set_points(pts)).pack(side="left", padx=(0, 4))
        ttk.Label(box, text=tr("range_window.hint"), foreground="#777", font=("Segoe UI", 8), justify="left",
                  wraplength=self.w).pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(6, 0))
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<ButtonPress-3>", self.delete_at)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.hist = LocalUndo(self, lambda: json.dumps(self.pts), self.put_state)
        self.draw()
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 120}+{app.winfo_rooty() + 120}")
        self.grab_set()  # (the shapes it works on stay picked while it's open)
        self.focus_set()

    # ---- the shapes

    def gates(self):
        """The first and second gate in ticks (the first target's)."""
        t = self.tgts[0]
        return (max(1, math.floor(t["gate"] * self.app.ppq + 0.5)),
                max(1, math.floor(t["range"]["to"] * self.app.ppq + 0.5)))

    def store(self):
        for t in self.tgts:
            t["range"] = dict(t["range"], graph=[list(p) for p in self.pts])
        self.app.shapes_changed()
        self.recount()

    def set_points(self, pts):
        self.pts = [list(p) for p in pts]
        self.store()
        self.draw()
        self.hist.mark()

    def put_state(self, state):
        self.drag = None
        self.pts = json.loads(state)
        self.store()
        self.draw()

    def ok(self):
        if json.dumps(self.app.shapes) != self.before:
            self.app.add_undo_step(self.before, tr("range_window.step"))
        self.close()

    def cancel(self):
        for t, r in zip(self.tgts, self.old):
            t["range"] = r
        self.app.shapes_changed()
        self.close()

    def close(self):
        self.grab_release()
        self.destroy()
        self.app.sync_custom()

    # ---- drawing

    def u2x(self, u):
        return self.ml + u * (self.w - self.ml - self.mr)

    def y2c(self, y):
        return self.mt + (1 - y) * (self.h - self.mt - self.mb)

    def x2u(self, x):
        return min(1.0, max(0.0, (x - self.ml) / (self.w - self.ml - self.mr)))

    def c2y(self, c):
        return min(1.0, max(0.0, 1 - (c - self.mt) / (self.h - self.mt - self.mb)))

    def draw(self):
        cv, s = self.canvas, self.s
        cv.delete("all")
        a, b = self.gates()
        x0, x1, top, bot = self.u2x(0), self.u2x(1), self.y2c(1), self.y2c(0)
        for j in range(1, 4):
            cv.create_line(self.u2x(j / 4), top, self.u2x(j / 4), bot, fill="#d3dff0")
        n = abs(b - a) + 1
        for k in range(5):  # gate labels at 0, 25 ... 100 % of the way
            y = k / 4
            g = a + (1 if b >= a else -1) * min(n - 1, int(y * n))
            cv.create_line(x0, self.y2c(y), x1, self.y2c(y), fill="#d3dff0")
            cv.create_text(x0 - 4 * s, self.y2c(y), text=tr("range_window.ticks", n=g), anchor="e", fill="#333",
                           font=("Segoe UI", 7))
        cv.create_rectangle(x0, top, x1, bot, outline="#808080")
        keys = self.tgts[0]["range"]["dir"] == "keys"
        cv.create_text(x0, bot + 4 * s, text=tr("range_window.low" if keys else "range_window.left"), anchor="nw",
                       fill="#333", font=("Segoe UI", 7))
        cv.create_text(x1, bot + 4 * s, text=tr("range_window.high" if keys else "range_window.right"), anchor="ne",
                       fill="#333", font=("Segoe UI", 7))
        # the whole-tick gates the notes get (pale steps)
        span = (b - a) or 1
        steps = gate_steps(self.pts, a, b)
        if len(steps) <= 2000:
            line = []
            for u0, u1, g in steps:
                y = self.y2c((g - a) / span if b != a else 0)
                line += [self.u2x(u0), y, self.u2x(u1), y]
            if len(line) >= 4:
                cv.create_line(*line, fill="#f0b0b0", width=max(1, round(2 * s)))
        lw = max(1, round(1.5 * s))
        cv.create_line(*[c for u, y in self.pts for c in (self.u2x(u), self.y2c(y))], fill="#d00000", width=lw)
        r = 4 * s
        for i, (u, y) in enumerate(self.pts):
            x, yy = self.u2x(u), self.y2c(y)
            shape = cv.create_rectangle if i in (0, len(self.pts) - 1) else cv.create_oval
            shape(x - r, yy - r, x + r, yy + r, fill="#ffffff", outline="#d00000", width=lw)
        at = self.pts[self.drag] if self.drag is not None else self.hover
        if at is not None:
            g = a + (1 if b >= a else -1) * min(n - 1, int(float(at[1]) * n))
            cv.create_text(x1 - 4 * s, top + 4 * s, text=tr("range_window.at", at=fmt(round(at[0] * 100, 1)), n=g),
                           anchor="ne", fill="#0a50e0", font=("Segoe UI", 8))
        self.info.config(text=tr("range_window.info", a=a, b=b) +
                         (tr("range_window.notes", n=self.count) if self.count else ""))

    def recount(self):
        self.count = sum(self.app.note_count(t) or 0 for t in self.tgts if t is not self.app.custom_defaults)

    # ---- mouse

    def point_at(self, x, y):
        r = 8 * self.s
        best = None
        for i, (u, v) in enumerate(self.pts):
            d = max(abs(self.u2x(u) - x), abs(self.y2c(v) - y))
            if d <= r and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def snapped(self, e):
        u, y = self.x2u(e.x), self.c2y(e.y)
        if not e.state & 0x1:
            u, y = round(u / U_SNAP) * U_SNAP, round(y / Y_SNAP) * Y_SNAP
        return u, y

    def press(self, e):
        i = self.point_at(e.x, e.y)
        if i is None:
            u, y = self.snapped(e)
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            i = next(j for j, p in enumerate(self.pts) if p[0] > u)
            self.pts.insert(i, [u, y])
            self.store()
        self.drag = i
        self.draw()

    def motion(self, e):
        i = self.drag
        if i is None:
            return
        u, y = self.snapped(e)
        if i in (0, len(self.pts) - 1):
            u = self.pts[i][0]  # (the ends stay at the shape's edges)
        else:
            u = min(max(u, self.pts[i - 1][0]), self.pts[i + 1][0])
        if [u, y] != self.pts[i]:
            self.pts[i] = [u, y]
            self.store()
            self.draw()

    def release(self, e):
        self.drag = None
        self.draw()
        self.hist.mark()

    def delete_at(self, e):
        i = self.point_at(e.x, e.y)
        if i is None or len(self.pts) <= 2:
            return
        del self.pts[i]
        self.pts[0][0], self.pts[-1][0] = 0.0, 1.0
        self.store()
        self.draw()
        self.hist.mark()

    def on_hover(self, e):
        self.hover = None
        if e is not None and self.drag is None and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            self.hover = [u, float(np.interp(u, [p[0] for p in self.pts], [p[1] for p in self.pts]))]
        self.draw()


def open_range_graph(app):
    if any(t.get("range") for t in app.custom_targets()):
        RangeGraph(app)
