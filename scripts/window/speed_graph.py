"""Copy along a path's Speed curve (Speed: Custom, its "Graph…" button; user 2026-10-16, after a speed graph felt
wrong): left to right = the copies, first to last; bottom to top = where each lands along the path, A (0 %) to the end
(100 %). A straight line from corner to corner = Even. Points (the ends fixed) and a round bend handle in the middle
of each stretch: dragged up / down it bends that stretch (double-click = straight again). The line never goes down, so
copies never walk back. Stored as the page's "curve" (notes/copypath.py curve_at); each change is a step in the path
window's own undo too, and Ctrl+Z / Ctrl+Y in this window step through its own changes."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.copypath import BEND_LIMITS, CURVE_POINTS, SPEED_CURVES, curve_at
from window import look
from window.widgets import REDO_KEYS, UNDO_KEYS, LocalUndo, Scrub, remember_place

PRESETS = [(tr("path.even"), SPEED_CURVES["even"]), (tr("path.up"), SPEED_CURVES["up"]),
           (tr("path.down"), SPEED_CURVES["down"]),
           (tr("speed_graph.slow_fast_slow"), [[0.0, 0.0, 0.25], [0.5, 0.5, 0.75], [1.0, 1.0, 0.5]]),
           (tr("speed_graph.fast_slow_fast"), [[0.0, 0.0, 0.75], [0.5, 0.5, 0.25], [1.0, 1.0, 0.5]]),
           (tr("speed_graph.waves"), [[k / 4, k / 4, 0.8 if k % 2 == 0 else 0.2] for k in range(4)] +
            [[1.0, 1.0, 0.5]])]
SNAP = 1 / 40  # points move in these steps (Shift = free)
BEND_SNAP = 0.03  # a bend this close to straight snaps straight (Shift = free)
FLAT = 1e-9  # a stretch this flat has no bend handle (nothing to bend)


def start_curve(speed):
    """The curve Custom starts from: the Speed picked before it, drawn (the same places)."""
    return [list(p) for p in SPEED_CURVES.get(speed, SPEED_CURVES["even"])]


class SpeedGraph(tk.Toplevel):
    def __init__(self, pw):
        super().__init__(pw)
        self.pw, self.app = pw, pw.app
        self.title(tr("speed_graph.title"))
        self.transient(pw)
        self.resizable(False, False)
        s = self.s = self.app.scale
        self.w, self.h = int(420 * s), int(300 * s)
        self.ml, self.mr, self.mt, self.mb = int(46 * s), int(12 * s), int(10 * s), int(22 * s)
        self.pts = start_curve("even")
        self.drag = None  # ("pt" / "bend", i)
        self.job = None
        self.hover = None
        self.session = None  # the curve as it was when the window opened / this page came (Cancel goes back to it)
        self._storing = False

        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        self.info = ttk.Label(box, text="", wraplength=int(480 * s), justify="left")
        self.info.pack(anchor="w")
        self.canvas = tk.Canvas(box, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                highlightbackground=look.CHART_BORDER, cursor="crosshair")
        self.canvas.pack(pady=(4, 4))
        row = ttk.Frame(box)
        row.pack(fill="x")
        for i, (name, pts) in enumerate(PRESETS):
            ttk.Button(row, text=name, command=lambda pts=pts: self.set_points(pts)).grid(
                row=i // 3, column=i % 3, sticky="ew", padx=(0, 4), pady=(0, 4))
        for c in range(3):
            row.columnconfigure(c, weight=1, uniform="presets")
        ttk.Label(box, text=tr("speed_graph.hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=int(480 * s)).pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(6, 0))
        ttk.Button(row, text=tr("graph_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("graph_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))

        cv = self.canvas
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<Double-Button-1>", self.double)
        cv.bind("<ButtonPress-3>", self.menu)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.hist = LocalUndo(self, lambda: json.dumps(self.pts), self.put_state)
        self.sync()
        self.update_idletasks()
        self.geometry(f"+{pw.winfo_rootx() + pw.winfo_width() + int(8 * s)}+{pw.winfo_rooty()}")
        remember_place(self, "speed_graph")

    # ------------------------------------------------------------ the page's curve

    def live(self):
        """Is there a page to put the curve on (Speed: Custom)?"""
        pw = self.pw
        return bool(pw.targets) and not pw.idle and pw.cfg.get("speed") == "custom"

    def session_key(self):
        pw = self.pw
        return json.dumps([pw.targets, sorted((pw.at or {}).items())])

    def begin(self):
        self.session = {"key": self.session_key(), "curve": json.loads(json.dumps(self.pw.cfg.get("curve")))}

    def sync(self):
        """Show the page's curve (not while something is dragged)."""
        new = self.session is None or self.session["key"] != self.session_key()
        if new:
            self.begin()  # (another page / selection: what was done to the last one is kept, like OK)
        if self.drag:
            return
        self.pts = [list(p) for p in (self.pw.cfg.get("curve") or start_curve("even"))]
        # another page, or the curve changed from outside (the path window's undo): its steps here start again
        if new or not self._storing and self.hist.get() != self.hist.states[self.hist.at]:
            self.hist.reset()
        self.draw()

    def store(self):
        """Put the curve on the page (while dragging at most once per idle moment)."""
        self.job = None
        self._storing = True
        try:
            self.pw.put("curve", [list(p) for p in self.pts], not self.drag)
        finally:
            self._storing = False

    def schedule(self):
        if not self.job:
            self.job = self.after_idle(self.store)

    def changed(self):
        """A finished change (not a drag): on the page, one step here."""
        self.draw()
        self.store()
        self.hist.mark()

    def set_points(self, pts):
        if self.live():
            self.pts = [list(p) for p in pts]
            self.changed()

    def put_state(self, state):
        """Ctrl+Z / Ctrl+Y in the window: the curve as it was then."""
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.drag = None
        if self.live():
            self.pts = json.loads(state)
            self.draw()
            self.store()

    def ok(self):
        if self.job:
            self.after_cancel(self.job)
            self.store()
        self.close()

    def cancel(self):
        """The curve as it was when the window opened (or this page came); still part of the path window's change."""
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        ses = self.session
        if ses and ses["key"] == self.session_key() and self.pw.cfg.get("curve") != ses["curve"]:
            self.pw.put("curve", ses["curve"])
        self.close()

    def close(self):
        self.pw.graph_window = None
        self.destroy()

    # ------------------------------------------------------------ drawing

    def u2x(self, u):
        return self.ml + u * (self.w - self.ml - self.mr)

    def p2y(self, p):
        return self.mt + (1 - p) * (self.h - self.mt - self.mb)

    def x2u(self, x):
        return min(1.0, max(0.0, (x - self.ml) / (self.w - self.ml - self.mr)))

    def y2p(self, y):
        return min(1.0, max(0.0, 1 - (y - self.mt) / (self.h - self.mt - self.mb)))

    def handle(self, i):
        """Stretch i's bend handle (u, p) at its middle, or None (a flat stretch: nothing to bend)."""
        (u0, p0, m), (u1, p1, _) = self.pts[i], self.pts[i + 1]
        if p1 - p0 <= FLAT or u1 - u0 <= FLAT:
            return None
        return (u0 + u1) / 2, p0 + (p1 - p0) * m

    def draw(self):
        cv, s = self.canvas, self.s
        cv.delete("all")
        x0, x1, y0, y1 = self.u2x(0), self.u2x(1), self.p2y(1), self.p2y(0)
        for k in range(5):
            y = self.p2y(k / 4)
            cv.create_line(x0, y, x1, y, fill=look.CHART_GRID_STRONG if k in (0, 4) else look.CHART_GRID)
            cv.create_text(x0 - 4 * s, y, text=f"{k * 25} %", anchor="e", fill=look.LABEL, font=look.font(7))
            x = self.u2x(k / 4)
            cv.create_line(x, y0, x, y1, fill=look.CHART_GRID)
        cv.create_line(x0, y1, x1, y0, fill=look.CHART_GRID_STRONG, dash=(3, 3))  # (Even)
        cv.create_rectangle(x0, y0, x1, y1, outline=look.CHART_FRAME)
        cv.create_text(x0, y1 + 4 * s, text=tr("speed_graph.first"), anchor="nw", fill=look.LABEL, font=look.font(7))
        cv.create_text(x1, y1 + 4 * s, text=tr("speed_graph.last"), anchor="ne", fill=look.LABEL, font=look.font(7))
        u = np.unique(np.r_[np.linspace(0.0, 1.0, 241), [p[0] for p in self.pts]])
        lw = max(1, round(1.5 * s))
        cv.create_line(*[c for a, b in zip(u, curve_at(self.pts, u)) for c in (self.u2x(a), self.p2y(b))],
                       fill=look.CHART_LINE, width=lw)
        r, rb = 4 * s, 3 * s
        for i in range(len(self.pts) - 1):  # (bend handles: small solid dots, unlike the points)
            h = self.handle(i)
            if h:
                x, y = self.u2x(h[0]), self.p2y(h[1])
                cv.create_oval(x - rb, y - rb, x + rb, y + rb, fill=look.CHART_LINE, outline="")
        for i, (a, b, _) in enumerate(self.pts):
            x, y = self.u2x(a), self.p2y(b)
            shape = cv.create_rectangle if i in (0, len(self.pts) - 1) else cv.create_oval
            shape(x - r, y - r, x + r, y + r, fill=look.CHART_POINT, outline=look.CHART_LINE, width=lw)
        at = self.drag_at() or self.hover
        if at is not None:
            text = tr("speed_graph.at", at=fmt(round(at[0] * 100, 1)), p=fmt(round(at[1] * 100, 1)))
            cv.create_text(self.u2x(0) + 6 * s, y0 + 4 * s, text=text, anchor="nw", fill=look.VALUE,
                           font=look.font(8))
        self.info.config(text=tr("speed_graph.info") if self.live() else tr("speed_graph.pick_custom"))

    def drag_at(self):
        if not self.drag:
            return None
        what, i = self.drag
        return self.pts[i][:2] if what == "pt" else self.handle(i)

    # ------------------------------------------------------------ mouse

    def hit(self, x, y):
        """("pt", i) / ("bend", i) under (x, y), points first, or None."""
        r = 8 * self.s
        best = None
        for i, (u, p, _) in enumerate(self.pts):
            d = max(abs(self.u2x(u) - x), abs(self.p2y(p) - y))
            if d <= r and (best is None or d < best[0]):
                best = (d, ("pt", i))
        if best:
            return best[1]
        for i in range(len(self.pts) - 1):
            h = self.handle(i)
            if h:
                d = max(abs(self.u2x(h[0]) - x), abs(self.p2y(h[1]) - y))
                if d <= r and (best is None or d < best[0]):
                    best = (d, ("bend", i))
        return best and best[1]

    def press(self, e):
        if not self.live():
            return
        got = self.hit(e.x, e.y)
        if got is None:  # (a new point here, on the stretch it splits; both halves keep its bend)
            if len(self.pts) >= CURVE_POINTS:
                return
            free = e.state & 0x1
            u = self.x2u(e.x) if free else round(self.x2u(e.x) / SNAP) * SNAP
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            i = next(j for j, p in enumerate(self.pts) if p[0] > u)
            lo, hi = self.pts[i - 1][1], self.pts[i][1]
            p = self.y2p(e.y) if free else round(self.y2p(e.y) / SNAP) * SNAP
            self.pts.insert(i, [u, min(hi, max(lo, p)), self.pts[i - 1][2]])
            got = ("pt", i)
            self.schedule()
        if got[0] == "pt" and got[1] in (0, len(self.pts) - 1):
            return  # (the ends stay put)
        self.drag = got
        self.draw()

    def motion(self, e):
        if not self.drag:
            return
        what, i = self.drag
        free = e.state & 0x1
        pts = self.pts
        if what == "pt":
            u, p = self.x2u(e.x), self.y2p(e.y)
            if not free:
                u, p = round(u / SNAP) * SNAP, round(p / SNAP) * SNAP
            u = min(max(u, pts[i - 1][0]), pts[i + 1][0])
            p = min(max(p, pts[i - 1][1]), pts[i + 1][1])  # (never down: copies don't walk back)
            if [u, p] == pts[i][:2]:
                return
            pts[i][:2] = [u, p]
        else:
            p0, p1 = pts[i][1], pts[i + 1][1]
            if p1 - p0 <= FLAT:
                return
            m = min(BEND_LIMITS[1], max(BEND_LIMITS[0], (self.y2p(e.y) - p0) / (p1 - p0)))
            if not free and abs(m - 0.5) < BEND_SNAP:
                m = 0.5
            if m == pts[i][2]:
                return
            pts[i][2] = m
        self.draw()
        self.schedule()

    def release(self, e):
        if not self.drag:
            return
        self.drag = None
        if self.job:
            self.after_cancel(self.job)
        self.store()  # (the path window's step ends here too)
        self.draw()
        self.hist.mark()

    def double(self, e):
        """Double-click a bend handle: that stretch straight again."""
        got = self.hit(e.x, e.y)
        if got and got[0] == "bend" and self.live():
            self.straight(got[1])

    def straight(self, i):
        if self.pts[i][2] != 0.5:
            self.pts[i][2] = 0.5
            self.changed()

    def menu(self, e):
        """Right-click a point: type where it is, or delete it; a bend handle: straight."""
        got = self.hit(e.x, e.y)
        if got is None or not self.live():
            return
        what, i = got
        m = tk.Menu(self, tearoff=0)
        if what == "bend":
            m.add_command(label=tr("speed_graph.straight"), command=lambda: self.straight(i))
        else:
            end = i in (0, len(self.pts) - 1)
            m.add_command(label=tr("graph_window.value"), command=lambda: PointDialog(self, i),
                          state="disabled" if end else "normal")
            m.add_command(label=tr("graph_window.delete_point"), command=lambda: self.delete_point(i),
                          state="disabled" if end else "normal")
        m.tk_popup(e.x_root, e.y_root)

    def delete_point(self, i):
        if 0 < i < len(self.pts) - 1 and self.live():
            del self.pts[i]
            self.changed()

    def set_point(self, i, u, p):
        """Point i typed in (PointDialog): kept between its neighbours."""
        pts = self.pts
        if not (0 < i < len(pts) - 1) or not self.live():
            return
        u = min(max(u, pts[i - 1][0]), pts[i + 1][0])
        p = min(max(p, pts[i - 1][1]), pts[i + 1][1])
        if [u, p] != pts[i][:2]:
            pts[i][:2] = [u, p]
            self.changed()

    def on_hover(self, e):
        self.hover = None
        if e is not None and not self.drag and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            self.hover = [u, float(curve_at(self.pts, [u])[0])]
        self.draw()


class PointDialog(tk.Toplevel):
    """Right-click a point > Value…: which copy (%) and where it lands on the path (%), typed."""

    def __init__(self, gw, i):
        super().__init__(gw)
        self.gw, self.i = gw, i
        self.title(tr("graph_window.point"))
        self.transient(gw)
        self.resizable(False, False)
        for k in UNDO_KEYS + REDO_KEYS:  # (they'd reach the main window's undo)
            self.bind(k, lambda e: "break")
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.vars, self.boxes = {}, {}
        for r, (key, text, v) in enumerate((("u", tr("speed_graph.copies"), gw.pts[i][0]),
                                            ("p", tr("speed_graph.path"), gw.pts[i][1]))):
            lb = ttk.Label(box, text=text)
            lb.grid(row=r, column=0, sticky="w", pady=2)
            var = self.vars[key] = tk.StringVar(value=fmt(round(v * 100, 4)))
            e = self.boxes[key] = ttk.Entry(box, textvariable=var, width=10)
            e.grid(row=r, column=1, sticky="w", padx=(6, 4), pady=2)
            ttk.Label(box, text=tr("unit.percent")).grid(row=r, column=2, sticky="w")
            Scrub(gw.app, [(e, var, None)], (1, 10, 0.1), 0, 100, label=lb)
        btns = ttk.Frame(box)
        btns.grid(row=2, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=tr("graph_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(btns, text=tr("graph_window.cancel"), command=self.destroy).pack(side="left", padx=(4, 0))
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{gw.winfo_pointerx() + 10}+{gw.winfo_pointery() + 10}")
        remember_place(self, "speed_point")
        self.grab_set()
        self.boxes["p"].focus_set()
        self.boxes["p"].select_range(0, "end")

    def ok(self):
        try:
            u, p = (float(calc(self.vars[k].get())) / 100 for k in ("u", "p"))
            if not (math.isfinite(u) and math.isfinite(p)):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            return self.bell()
        self.destroy()
        self.gw.set_point(self.i, u, p)
