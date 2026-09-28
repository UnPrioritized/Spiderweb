"""A tumour setting's graph (the "…" next to its box in the tumour window): along the whole line, the box's number is
multiplied by the graph (100 % = as typed). Drag points, click to add one, right-click to remove one; presets and
a formula. Stored as tm["graphs"][setting] (tumour.py)."""

import json
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.mathexpr import formula, fmt
from notes.joined import shown_tumour
from notes.tumour import GRAPH_LIMIT, TUMOUR_DEFAULTS

FLAT = [[0.0, 1.0], [1.0, 1.0]]
PRESETS = [("Flat (off)", FLAT), ("Rise", [[0.0, 0.0], [1.0, 1.0]]), ("Fall", [[0.0, 1.0], [1.0, 0.0]]),
           ("Hill", [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]]), ("Valley", [[0.0, 1.0], [0.5, 0.0], [1.0, 1.0]])]
VIEWS = [(0, 100), (0, 200), (0, 400), (-100, 100), (-200, 200), (-400, 400), (-1000, 1000)]  # shown heights, %
U_SNAP, F_SNAP = 1 / 40, 0.05  # dragging moves points in these steps (Shift = free)
HINT = ("Drag a point to move it (Shift = free). Click anywhere to add a point, right-click a point to remove it.\n"
        "100 % = the number in the box; the bumps change along the line as the graph does.")
FORMULA_HINT = "x = 0 at the line's start, 1 at its end; the result is in %. E.g. 100*x   50+50*sin(x*2*pi)"


def view_text(v):
    return f"{v[0]} to {v[1]} %"


class GraphWindow(tk.Toplevel):
    def __init__(self, tw, key, label, unit):
        super().__init__(tw)
        self.tw, self.app, self.key, self.label, self.unit = tw, tw.app, key, label, unit
        self.title(f"Graph: {label}")
        self.transient(tw)
        self.resizable(False, False)
        s = self.s = self.app.scale
        self.w, self.h = int(440 * s), int(220 * s)
        self.ml, self.mr, self.mt, self.mb = int(46 * s), int(12 * s), int(10 * s), int(22 * s)
        self.pts = [list(p) for p in FLAT]
        self.drag = None  # {"i": point being dragged, "pushed": undo step taken}
        self.job = None
        self.hover = None

        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        self.info = ttk.Label(box, text="")
        self.info.pack(anchor="w")
        self.canvas = tk.Canvas(box, width=self.w, height=self.h, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#a0a0a0", cursor="crosshair")
        self.canvas.pack(pady=(4, 4))
        row = ttk.Frame(box)
        row.pack(fill="x")
        for name, pts in PRESETS:
            ttk.Button(row, text=name, command=lambda pts=pts: self.set_points(pts)).pack(side="left", padx=(0, 4))
        ttk.Label(row, text="Show").pack(side="left", padx=(8, 0))
        self.view = tk.StringVar(value=view_text(VIEWS[1]))
        c = ttk.Combobox(row, textvariable=self.view, state="readonly", width=15, values=[view_text(v) for v in VIEWS])
        c.pack(side="left", padx=(5, 0))
        c.bind("<<ComboboxSelected>>", lambda e: self.draw())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Formula").pack(side="left")
        self.formula = tk.StringVar()
        self.formula_box = ttk.Entry(row, textvariable=self.formula, width=34)
        self.formula_box.pack(side="left", padx=(5, 4))
        self.formula_box.bind("<Return>", lambda e: (self.apply_formula(), "break")[1])
        ttk.Button(row, text="Apply", command=self.apply_formula).pack(side="left")
        self.formula_note = ttk.Label(box, text=FORMULA_HINT, foreground="#777", font=("Segoe UI", 8))
        self.formula_note.pack(anchor="w")
        ttk.Label(box, text=HINT, foreground="#777", font=("Segoe UI", 8), justify="left").pack(anchor="w",
                                                                                                pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(6, 0))
        ttk.Button(row, text="OK", command=self.ok).pack(side="left")
        ttk.Button(row, text="Cancel", command=self.cancel).pack(side="left", padx=(4, 0))
        self.session = None  # the graph as it was when the window opened / these shapes were selected (see begin)

        cv = self.canvas
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<ButtonPress-3>", self.remove)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.sync(fit_view=True)
        # where it was last time, else beside the tumour window
        self.update_idletasks()
        self.geometry(self.app.graph_pos or f"+{tw.winfo_rootx() + tw.winfo_width() + int(8 * s)}+{tw.winfo_rooty()}")
        self.bind("<Configure>", self.remember, add="+")

    # ------------------------------------------------------------ the shapes' graph

    def tm(self):
        tgts = self.app.tumour_targets()
        return dict(TUMOUR_DEFAULTS, **((shown_tumour(tgts[0]) if tgts else None) or {}))

    def sync(self, fit_view=False):
        """Show the first selected line's graph (not while a point is being dragged)."""
        tgts = self.app.tumour_targets()
        old = self.session["tgts"] if self.session else None
        if old is None or len(old) != len(tgts) or any(a is not b for a, b in zip(old, tgts)):
            self.begin()  # (other shapes selected: what was done to the last ones is kept, like OK)
        if self.drag:
            return
        g = (self.tm().get("graphs") or {}).get(self.key)
        self.pts = [list(p) for p in (g or FLAT)]
        if fit_view or not self.fits(self.view_range()):
            self.fit_view()
        self.draw()

    def store(self):
        """Put the graph on the selected lines (while dragging at most once per idle moment)."""
        self.job = None
        self.tw.set_graph(self.key, self.pts)

    def schedule(self):
        if not self.job:
            self.job = self.after_idle(self.store)

    def set_points(self, pts):
        self.push_undo()
        self.pts = [list(p) for p in pts]
        if not self.fits(self.view_range()):
            self.fit_view()
        self.draw()
        self.store()

    def apply_formula(self):
        try:
            f = formula(self.formula.get())
            pts = []
            for i in range(41):
                v = float(f(i / 40)) / 100
                if v != v or abs(v) == float("inf"):
                    raise ValueError("it doesn't give a number everywhere from x = 0 to 1")
                pts.append([i / 40, min(GRAPH_LIMIT, max(-GRAPH_LIMIT, v))])
        except (ValueError, ZeroDivisionError, OverflowError, TypeError) as e:
            msg = str(e) if isinstance(e, ValueError) else "it doesn't give a number everywhere from x = 0 to 1"
            self.formula_box.config(style="Bad.TEntry")
            self.formula_note.config(text=f"Can't use it: {msg}.", foreground="#d00000")
            return
        self.formula_box.config(style="TEntry")
        self.formula_note.config(text=FORMULA_HINT, foreground="#777")
        # (points on a straight stretch aren't needed)
        keep = [pts[0]] + [b for a, b, c in zip(pts, pts[1:], pts[2:])
                           if abs((b[1] - a[1]) - (c[1] - b[1])) > 1e-9] + [pts[-1]]
        self.set_points(keep)

    def begin(self):
        """Start again from the selected shapes' graphs as they are now (Cancel goes back to this)."""
        app = self.app
        tgts = app.tumour_targets()
        self.session = {"tgts": tgts, "shapes": json.dumps(app.shapes), "step": None, "exact": True,
                        "graphs": [json.loads(json.dumps(((shown_tumour(t) or {}).get("graphs") or {}).get(self.key)))
                                   for t in tgts]}

    def ok(self):
        """Keep the graph."""
        if self.job:
            self.after_cancel(self.job)
            self.store()
        self.close()

    def cancel(self):
        """Put the graph back as it was when the window opened (or when these shapes were selected)."""
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        app, ses = self.app, self.session
        if ses and ses["step"] is not None:
            if ses["exact"] and len(app.undo_stack) == ses["step"]:  # nothing else changed: exactly as it was
                app.undo_stack.pop()
                app.shapes = json.loads(ses["shapes"])
                app._edit_key = None
                app.sync_panel()
                app.shapes_changed()
            else:  # other changes since then: just this graph goes back
                if any(t is s for t in ses["tgts"] for s in app.shapes):
                    app.push_undo()
                    for t, g in zip(ses["tgts"], ses["graphs"]):
                        tm = t.get("tumour") if any(t is s for s in app.shapes) else None
                        if not tm:
                            continue
                        graphs = dict(tm.get("graphs") or {})
                        graphs.pop(self.key, None)
                        if g:
                            graphs[self.key] = g
                        if graphs:
                            tm["graphs"] = graphs
                        else:
                            tm.pop("graphs", None)
                    app.shapes_changed()
                    app.sync_tumour()
        self.close()

    def close(self):
        self.tw.graph_window = None
        self.destroy()

    def remember(self, e):
        if e.widget is self:
            self.app.graph_pos = f"+{self.winfo_x()}+{self.winfo_y()}"

    # ------------------------------------------------------------ view

    def view_range(self):
        return next((v for v in VIEWS if view_text(v) == self.view.get()), VIEWS[1])

    def fits(self, v):
        return all(v[0] / 100 - 1e-9 <= f <= v[1] / 100 + 1e-9 for _, f in self.pts)

    def fit_view(self):
        """The smallest view the graph fits in (0 to 200 % when it does)."""
        order = [VIEWS[1], VIEWS[0], VIEWS[2], VIEWS[3], VIEWS[4], VIEWS[5], VIEWS[6]]
        self.view.set(view_text(next((v for v in order if self.fits(v)), VIEWS[-1])))

    def u2x(self, u):
        return self.ml + u * (self.w - self.ml - self.mr)

    def f2y(self, f):
        lo, hi = self.view_range()
        return self.mt + (hi / 100 - f) / ((hi - lo) / 100) * (self.h - self.mt - self.mb)

    def x2u(self, x):
        return min(1.0, max(0.0, (x - self.ml) / (self.w - self.ml - self.mr)))

    def y2f(self, y):
        lo, hi = self.view_range()
        f = hi / 100 - (y - self.mt) / (self.h - self.mt - self.mb) * ((hi - lo) / 100)
        return min(hi / 100, max(lo / 100, f))

    def value_text(self, f):
        """What f (a multiplier) makes of the box's number, as the box shows it."""
        tm = self.tm()
        v = tm[self.key] * f * (self.app.ppq if self.key in ("length", "dist") else 100 if self.key == "slant" else 1)
        return f"{fmt(round(f * 100, 1))} % = {fmt(round(v, 2))} {self.unit}"

    def draw(self):
        cv, s = self.canvas, self.s
        cv.delete("all")
        lo, hi = self.view_range()
        x0, x1 = self.u2x(0), self.u2x(1)
        y_top, y_bot = self.f2y(hi / 100), self.f2y(lo / 100)
        tm = self.tm()
        # outside the tumour range: no bumps there, so greyed out
        r0, r1 = sorted((tm["start"], tm["end"]))
        for a, b in ((0.0, r0), (r1, 1.0)):
            if b - a > 1e-9:
                cv.create_rectangle(self.u2x(a), y_top, self.u2x(b), y_bot, fill="#e4e4e4", outline="")
        step = max(25, (hi - lo) // 8)
        v = lo
        while v <= hi:
            y = self.f2y(v / 100)
            cv.create_line(x0, y, x1, y, fill="#9fb2cf" if v in (0, 100) else "#d3dff0")
            cv.create_text(x0 - 4 * s, y, text=f"{v} %", anchor="e", fill="#333", font=("Segoe UI", 7))
            v += step
        for j in range(1, 4):
            x = self.u2x(j / 4)
            cv.create_line(x, y_top, x, y_bot, fill="#d3dff0")
        cv.create_rectangle(x0, y_top, x1, y_bot, outline="#808080")
        cv.create_text(x0, y_bot + 4 * s, text="line start", anchor="nw", fill="#333", font=("Segoe UI", 7))
        cv.create_text(x1, y_bot + 4 * s, text="line end", anchor="ne", fill="#333", font=("Segoe UI", 7))
        if r0 > 1e-9 or r1 < 1 - 1e-9:
            cv.create_text((self.u2x(r0) + self.u2x(r1)) / 2, y_bot + 4 * s, text="tumour range", anchor="n",
                           fill="#777", font=("Segoe UI", 7))
        lw = max(1, round(1.5 * s))
        cv.create_line(*[c for u, f in self.pts for c in (self.u2x(u), self.f2y(f))], fill="#d00000", width=lw)
        r = 4 * s
        for i, (u, f) in enumerate(self.pts):
            x, y = self.u2x(u), self.f2y(f)
            shape = cv.create_rectangle if i in (0, len(self.pts) - 1) else cv.create_oval
            shape(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#d00000", width=lw)
        # what's under the mouse (or the point being dragged)
        at = self.pts[self.drag["i"]] if self.drag else self.hover
        if at is not None:
            text = f"at {fmt(round(at[0] * 100, 1))} % of the line: {self.value_text(at[1])}"
            cv.create_text(x1 - 4 * s, y_top + 4 * s, text=text, anchor="ne", fill="#0a50e0", font=("Segoe UI", 8))
        name = self.label.lower()
        self.info.config(text=f"{self.label} along the line: 100 % = the box's {self.value_text(1).split('= ')[1]}."
                         if self.app.tumour_targets() else f"Select a line with tumours to give its {name} a graph.")

    # ------------------------------------------------------------ mouse

    def point_at(self, x, y):
        r = 8 * self.s
        best = None
        for i, (u, f) in enumerate(self.pts):
            d = max(abs(self.u2x(u) - x), abs(self.f2y(f) - y))
            if d <= r and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def press(self, e):
        if not self.app.tumour_targets():
            return
        i = self.point_at(e.x, e.y)
        pushed = False
        if i is None:  # a new point here
            u = self.x2u(e.x)
            if not e.state & 0x1:
                u = round(u / U_SNAP) * U_SNAP
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            f = self.y2f(e.y) if e.state & 0x1 else round(self.y2f(e.y) / F_SNAP) * F_SNAP
            i = next(j for j, p in enumerate(self.pts) if p[0] > u)
            self.push_undo()
            pushed = True
            self.pts.insert(i, [u, f])
            self.schedule()
        self.drag = {"i": i, "pushed": pushed}
        self.draw()

    def motion(self, e):
        d = self.drag
        if not d:
            return
        i, free = d["i"], e.state & 0x1
        u, f = self.x2u(e.x), self.y2f(e.y)
        if not free:
            u, f = round(u / U_SNAP) * U_SNAP, round(f / F_SNAP) * F_SNAP
        if i == 0 or i == len(self.pts) - 1:
            u = self.pts[i][0]  # the ends stay at the line's start / end
        else:
            u = min(max(u, self.pts[i - 1][0]), self.pts[i + 1][0])
        if [u, f] == self.pts[i]:
            return
        if not d["pushed"]:
            self.push_undo()
            d["pushed"] = True
        self.pts[i] = [u, f]
        self.draw()
        self.schedule()

    def release(self, e):
        self.drag = None
        self.draw()

    def remove(self, e):
        i = self.point_at(e.x, e.y)
        if i is None or i in (0, len(self.pts) - 1) or not self.app.tumour_targets():
            return
        self.push_undo()
        del self.pts[i]
        self.draw()
        self.store()

    def push_undo(self):
        """One undo step for everything done to the graph until OK (or other shapes are selected)."""
        ses = self.session
        if ses["step"] is None or len(self.app.undo_stack) != ses["step"]:
            if ses["step"] is not None:  # (something else changed in between: Cancel can only put the graph back)
                ses["exact"] = False
            self.app.push_undo()
            ses["step"] = len(self.app.undo_stack)
        self.app._edit_key = None  # (typing in a box afterwards is its own undo step)

    def on_hover(self, e):
        """Under the mouse: the graph's value at that spot along the line."""
        self.hover = None
        if e is not None and not self.drag and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            f = float(np.interp(u, [p[0] for p in self.pts], [p[1] for p in self.pts]))
            self.hover = [u, f]
        self.draw()
