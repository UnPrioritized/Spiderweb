"""Fill with tempo jumps' small graphs (user 2026-10-29: drawing the shape in the Conductor pane was hard and
confusing): "My drawn one" (left to right = one gap, from its jump to the next one / its catch-up; bottom to top =
the lower to the higher of the two BPM boxes) and "Strength" (left to right = the whole area; bottom to top = how far
each jump goes from the normal tempo, 0 % = none, 100 % = all of it). Points (the two ends move only up / down) and
a round bend handle in the middle of each stretch (double-click = straight). Each stretch bends like a Conductor
pane part (tempo.bent, control point [0.5, bend]), so a drawn shape becomes tempo parts exactly. Changes show at
once (a Jump areas chain is laid again); Cancel puts back what it had when opened; Ctrl+Z / Ctrl+Y here step through
this window's own changes. Settings of the window, not of the song: no undo step of their own."""

import json
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import fmt
from notes.melody import GRAPH_POINTS, graph_at
from window import look
from window.speed_graph import BEND_SNAP, FLAT, SNAP, PointDialog
from window.widgets import REDO_KEYS, UNDO_KEYS, LocalUndo, chart_edges, remember_place

PRESETS = {"shape": [("up", [[0.0, 0.0, 0.5], [1.0, 1.0, 0.5]]), ("down", [[0.0, 1.0, 0.5], [1.0, 0.0, 0.5]]),
                     ("arch", [[0.0, 0.0, 0.5], [0.5, 1.0, 0.5], [1.0, 0.0, 0.5]]),
                     ("drop", [[0.0, 1.0, 0.0], [1.0, 0.0, 0.5]])],
           "strength": [("full", [[0.0, 1.0, 0.5], [1.0, 1.0, 0.5]]), ("grow", [[0.0, 0.0, 0.5], [1.0, 1.0, 0.5]]),
                        ("fade", [[0.0, 1.0, 0.5], [1.0, 0.0, 0.5]]),
                        ("swell", [[0.0, 0.0, 0.5], [0.5, 1.0, 0.5], [1.0, 0.0, 0.5]])]}
START = {"shape": PRESETS["shape"][0][1], "strength": PRESETS["strength"][0][1]}
MID = 0.25  # (a stretch's middle sits at 0.25 + bend / 2 of its height: tempo.bent with the control point halfway)


def graph_pts(app, kind):
    """The graph kept for kind ("shape" / "strength"), its starting one when none."""
    return [list(p) for p in (getattr(app, "melody_" + kind + "_pts", None) or START[kind])]


class JumpGraph(tk.Toplevel):
    def __init__(self, win, kind):
        app = win.app
        super().__init__(win)
        self.win, self.app, self.kind = win, app, kind
        self.title(tr("jump_graph.title_" + kind))
        self.transient(win)
        self.resizable(False, False)
        s = self.s = app.scale
        self.w, self.h = int(420 * s), int(240 * s)
        self.ml, self.mb = chart_edges(s, ("60,000,000",), 46, 22)
        self.mr, self.mt = int(12 * s), int(10 * s)
        self.point_labels = (tr("jump_graph.across_" + kind), tr("jump_graph.height"))  # (PointDialog's two boxes)
        self.pts = graph_pts(app, kind)
        self.session = [list(p) for p in self.pts]  # (Cancel: back to these)
        self.drag = None  # ("pt" / "bend", i)
        self.hover = None
        self.press_pts = None

        frame = ttk.Frame(self, padding=8)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=tr("jump_graph.info_" + kind), wraplength=int(440 * s), justify="left").pack(anchor="w")
        self.canvas = tk.Canvas(frame, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                highlightbackground=look.CHART_BORDER, cursor="crosshair")
        self.canvas.pack(pady=(4, 4))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        for i, (name, pts) in enumerate(PRESETS[kind]):
            ttk.Button(row, text=tr("jump_graph.%s_%s" % (kind, name)),
                       command=lambda pts=pts: self.set_points(pts)).grid(row=0, column=i, sticky="ew", padx=(0, 4))
            row.columnconfigure(i, weight=1, uniform="presets")
        ttk.Label(frame, text=tr("catch_graph.hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=int(440 * s)).pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(6, 0))
        # (back to how a new window starts: Up / Full; user 2026-10-29)
        ttk.Button(row, text=tr("jump_graph.reset"), command=lambda: self.set_points(START[kind])).pack(side="left")
        ttk.Button(row, text=tr("graph_window.cancel"), command=self.cancel).pack(side="right", padx=(4, 0))
        ttk.Button(row, text=tr("graph_window.ok"), command=self.close).pack(side="right")

        cv = self.canvas
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<Double-Button-1>", self.double)
        cv.bind("<ButtonPress-3>", self.menu)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.hist = LocalUndo(self, lambda: json.dumps(self.pts), self.put_state)
        for k in UNDO_KEYS + REDO_KEYS:  # (in place of LocalUndo's: a drag held first)
            self.bind(k, lambda e, d=-1 if k in UNDO_KEYS else 1: (self.undo_key(d), "break")[1])
        for seq in ("<Key>", "<Control-Key>", "<Alt-Key>"):  # (the piano roll's shortcuts wait)
            self.bind(seq, lambda e: "break")
        self.draw()
        self.update_idletasks()
        self.geometry(f"+{win.winfo_rootx() + win.winfo_width() + int(8 * s)}+{win.winfo_rooty()}")
        remember_place(self, "jump_graph_" + kind)

    # ------------------------------------------------------------ the window's setting

    def store(self):
        """The points to the Fill window (a chain being made is laid again with them)."""
        setattr(self.app, "melody_%s_pts" % self.kind, [list(p) for p in self.pts])
        self.win.seen = None
        self.win.sync()

    def changed(self):
        self.store()
        self.draw()
        self.hist.mark()

    def set_points(self, pts):
        self.pts = [list(p) for p in pts]
        self.changed()

    def put_state(self, state):
        self.drag = None
        self.pts = json.loads(state)
        self.store()
        self.draw()

    def undo_key(self, d):
        """Ctrl+Z (d -1) / Ctrl+Y (1); a drag held: Ctrl+Z = back to the press, no step, Ctrl+Y nothing."""
        if not self.drag:
            return self.hist.undo() if d < 0 else self.hist.redo()
        if d < 0:
            self.drag = None
            self.pts = json.loads(self.press_pts)
            self.store()
            self.draw()

    def cancel(self):
        if self.drag:
            self.undo_key(-1)
        if self.pts != self.session:
            self.pts = self.session
            self.store()
        self.close()

    def close(self):
        if getattr(self.win, "graphs", None) is not None:
            self.win.graphs.pop(self.kind, None)
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
        """Stretch i's bend handle (u, p) on the line at its middle, or None (a level stretch: nothing to bend)."""
        (u0, p0, m), (u1, p1, _) = self.pts[i], self.pts[i + 1]
        if abs(p1 - p0) <= FLAT or u1 - u0 <= FLAT:
            return None
        return (u0 + u1) / 2, p0 + (p1 - p0) * (MID + m / 2)

    def height_text(self, p):
        """A height as the left side shows it: a BPM between the boxes (shape) / a percentage (strength)."""
        if self.kind == "strength":
            return f"{fmt(round(p * 100, 1))} %"
        lo, hi = self.win.graph_bpms()
        if lo is None:
            return f"{fmt(round(p * 100, 1))} %"
        return fmt(round(lo + (hi - lo) * p, 2))

    def draw(self):
        cv, s = self.canvas, self.s
        cv.delete("all")
        x0, x1, y0, y1 = self.u2x(0), self.u2x(1), self.p2y(1), self.p2y(0)
        for k in range(5):
            y = self.p2y(k / 4)
            cv.create_line(x0, y, x1, y, fill=look.CHART_GRID_STRONG if k in (0, 4) else look.CHART_GRID)
            cv.create_text(x0 - 4 * s, y, text=self.height_text(k / 4), anchor="e", fill=look.LABEL,
                           font=look.font(7))
            x = self.u2x(k / 4)
            cv.create_line(x, y0, x, y1, fill=look.CHART_GRID)
        cv.create_rectangle(x0, y0, x1, y1, outline=look.CHART_FRAME)
        cv.create_text(x0, y1 + 4 * s, text=tr("jump_graph.start_" + self.kind), anchor="nw", fill=look.LABEL,
                       font=look.font(7))
        cv.create_text(x1, y1 + 4 * s, text=tr("jump_graph.end_" + self.kind), anchor="ne", fill=look.LABEL,
                       font=look.font(7))
        at_pts = [p[0] for p in self.pts]  # (and a hair before each point: two on one spot = an upright jump)
        u = np.unique(np.clip(np.r_[np.linspace(0.0, 1.0, 241), at_pts, np.array(at_pts) - 1e-9], 0, 1))
        lw = max(1, round(1.5 * s))
        line = [c for a, b in zip(u, graph_at(self.pts, u)) for c in (self.u2x(a), self.p2y(b))]
        cv.create_line(*line, fill=look.CHART_LINE, width=lw)
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
            text = tr("jump_graph.at", at=fmt(round(at[0] * 100, 1)), p=self.height_text(at[1]))
            cv.create_text(x0 + 6 * s, y0 + 4 * s, text=text, anchor="nw", fill=look.VALUE, font=look.font(8))

    def drag_at(self):
        if not self.drag:
            return None
        what, i = self.drag
        return self.pts[i][:2] if what == "pt" else self.handle(i)

    # ------------------------------------------------------------ mouse

    def hit(self, x, y):
        """("pt", i) / ("bend", i) nearest (x, y), or None; the ends only when nothing else is there."""
        r = 8 * self.s
        found = []
        last = len(self.pts) - 1
        for i, (u, p, _) in enumerate(self.pts):
            d = max(abs(self.u2x(u) - x), abs(self.p2y(p) - y))
            if d <= r:
                found.append((i in (0, last), d, 0, ("pt", i)))
        for i in range(last):
            h = self.handle(i)
            if h:
                d = max(abs(self.u2x(h[0]) - x), abs(self.p2y(h[1]) - y))
                if d <= r:
                    found.append((False, d, 1, ("bend", i)))
        return min(found)[3] if found else None

    def press(self, e):
        self.press_pts = json.dumps(self.pts)
        got = self.hit(e.x, e.y)
        if got is None:  # (a new point here, on the stretch it splits; both halves keep its bend)
            if len(self.pts) >= GRAPH_POINTS:
                return
            free = e.state & 0x1
            u = self.x2u(e.x) if free else round(self.x2u(e.x) / SNAP) * SNAP
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            i = next(j for j, p in enumerate(self.pts) if p[0] > u)
            p = self.y2p(e.y) if free else round(self.y2p(e.y) / SNAP) * SNAP
            self.pts.insert(i, [u, p, self.pts[i - 1][2]])
            got = ("pt", i)
            self.store()
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
            if i in (0, len(pts) - 1):  # (the ends: only up / down)
                u = pts[i][0]
            else:
                u = min(max(u, pts[i - 1][0]), pts[i + 1][0])
            if [u, p] == pts[i][:2]:
                return
            pts[i][:2] = [u, p]
        else:
            p0, p1 = pts[i][1], pts[i + 1][1]
            if abs(p1 - p0) <= FLAT:
                return
            m = min(1.0, max(0.0, ((self.y2p(e.y) - p0) / (p1 - p0) - MID) * 2))
            if not free and abs(m - 0.5) < BEND_SNAP * 2:
                m = 0.5
            if m == pts[i][2]:
                return
            pts[i][2] = m
        self.draw()
        self.store()

    def release(self, e):
        if not self.drag:
            return
        self.drag = None
        self.draw()
        self.hist.mark()

    def double(self, e):
        """Double-click a bend handle: that stretch straight again."""
        got = self.hit(e.x, e.y)
        if got and got[0] == "bend":
            self.straight(got[1])

    def straight(self, i):
        if self.pts[i][2] != 0.5:
            self.pts[i][2] = 0.5
            self.changed()

    def menu(self, e):
        """Right-click a point: type where it is, or delete it (the ends: only their height); a bend handle:
        straight."""
        got = self.hit(e.x, e.y)
        if got is None or self.drag:
            return
        what, i = got
        m = tk.Menu(self, tearoff=0)
        if what == "bend":
            m.add_command(label=tr("speed_graph.straight"), command=lambda: self.straight(i))
        else:
            end = i in (0, len(self.pts) - 1)
            m.add_command(label=tr("graph_window.value"), command=lambda: PointDialog(self, i))
            m.add_command(label=tr("graph_window.delete_point"), command=lambda: self.delete_point(i),
                          state="disabled" if end else "normal")
        m.tk_popup(e.x_root, e.y_root)

    def delete_point(self, i):
        if 0 < i < len(self.pts) - 1:
            del self.pts[i]
            self.changed()

    def set_point(self, i, u, p):
        """Point i typed in (PointDialog): kept between its neighbours (an end keeps its place)."""
        pts = self.pts
        if not 0 <= i < len(pts):
            return
        u = pts[i][0] if i in (0, len(pts) - 1) else min(max(u, pts[i - 1][0]), pts[i + 1][0])
        p = min(1.0, max(0.0, p))
        if [u, p] != pts[i][:2]:
            pts[i][:2] = [u, p]
            self.changed()

    def on_hover(self, e):
        self.hover = None
        if e is not None and not self.drag and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            self.hover = [u, float(graph_at(self.pts, [u])[0])]
        self.draw()
