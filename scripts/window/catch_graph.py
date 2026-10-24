"""A drawn catch-up's shape (Tempo pane: Catch-up shape Drawn, its "Graph…" button; user 2026-10-24, like Copy along
a path's Speed graph): left to right = the teal area, from the catch-up line to its end; bottom to top = how much of
the catch-up's push each moment gets (0 % = the tempo heard before it). Spiderweb still works out how hard it pushes,
so it always lands on time (like a formula, drawn). Points (the two ends move only up / down) and a round bend handle
in the middle of each stretch (double-click = straight). The window follows the picked catch-up; each change is a
main undo step, the pane shows it as it's dragged, and Ctrl+Z / Ctrl+Y here step through this window's own changes."""

import json
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import fmt
from notes.tempo import DRAWN, DRAWN_BENDS, DRAWN_POINTS, catch_get, catch_set, drawn_at, drawn_from
from window import look
from window.speed_graph import BEND_SNAP, FLAT, SNAP, PointDialog
from window.tempo_pane import ids
from window.widgets import REDO_KEYS, UNDO_KEYS, LocalUndo, chart_edges, remember_place

PRESETS = [(tr("tempo.shape_" + k), k) for k in ("flat", "ease", "rise", "ramp")]


class CatchGraph(tk.Toplevel):
    def __init__(self, box):
        app = box.app
        super().__init__(app)
        self.box, self.app = box, app
        self.title(tr("catch_graph.title"))
        self.transient(app)
        self.resizable(False, False)
        s = self.s = app.scale
        self.w, self.h = int(420 * s), int(260 * s)
        self.ml, self.mb = chart_edges(s, ("100 %",), 46, 22)
        self.mr, self.mt = int(12 * s), int(10 * s)
        self.point_labels = (tr("catch_graph.across"), tr("catch_graph.push"))  # (PointDialog's two boxes)
        self.several = False  # the Select box holds several drawn catch-ups (none edited: click one)
        self.held = None  # the catch-up shown last (kept while it isn't drawn for a moment: undo brings it back)
        self.c = None  # the catch-up edited (its own list in the tempo line), None: no drawn catch-up picked
        self.pts = [list(p) for p in DRAWN]
        self.drag = None  # ("pt" / "bend", i)
        self.job = None
        self.hover = None
        self.session = None  # its points when the window opened / it was picked (Cancel goes back to them)
        self.stepped = False  # this change already has its main undo step
        self._storing = False

        frame = ttk.Frame(self, padding=8)
        frame.pack(fill="both", expand=True)
        self.info = ttk.Label(frame, text="", wraplength=int(480 * s), justify="left")
        self.info.pack(anchor="w")
        self.canvas = tk.Canvas(frame, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                highlightbackground=look.CHART_BORDER, cursor="crosshair")
        self.canvas.pack(pady=(4, 2))
        self.result = ttk.Label(frame, text="", font=look.font(9))
        self.result.pack(anchor="w", pady=(0, 4))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        for i, (name, shape) in enumerate(PRESETS):
            ttk.Button(row, text=name, command=lambda k=shape: self.set_points(drawn_from(k, "", None))).grid(
                row=0, column=i, sticky="ew", padx=(0, 4))
            row.columnconfigure(i, weight=1, uniform="presets")
        ttk.Label(frame, text=tr("catch_graph.hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=int(480 * s)).pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(frame)
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
        for k in UNDO_KEYS + REDO_KEYS:  # (in place of LocalUndo's: a drag held first)
            self.bind(k, lambda e, d=-1 if k in UNDO_KEYS else 1: (self.undo_key(d), "break")[1])
        for seq in ("<Key>", "<Control-Key>", "<Alt-Key>"):  # (the piano roll's shortcuts wait)
            self.bind(seq, lambda e: "break")
        self.bind("<F1>", lambda e: (app.open_help("conductor"), "break")[1])
        self.press_pts = self.press_c = None  # a drag: the points at the press (undo_key)
        self.sync()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = box.winfo_rooty() - self.winfo_reqheight() - int(40 * s)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        remember_place(self, "catch_graph")

    # ------------------------------------------------------------ the picked catch-up

    def target(self):
        """The drawn catch-up to edit (user 2026-10-24, picked A + C): the one clicked, or the only drawn one in the
        Select box (several: "several", click one); else the one shown so far, as long as it's still there and
        drawn (clicking points / empty space keeps it; after an undo: the one on the same beat). None: none."""
        box, lines = self.box, self.app.tempo["catch"]
        if box.mode == "visual":
            pick = box.pane.picked()
            if pick and pick[0] == "catch":
                c = box.pane.ed()["catch"][pick[1]]
                if c[1] == "drawn":
                    return c
            elif box.pane.bands:
                mine = ids(box.pane.sel)
                inside = [c for c in lines if id(c) in mine and c[1] == "drawn"]
                if len(inside) == 1:
                    return inside[0]
                if inside:
                    return "several"
        if self.held is None:
            return None
        same = [c for c in lines if c is self.held] or [c for c in lines if c[0] == self.held[0]]
        return same[0] if same and same[0][1] == "drawn" else None

    def live(self):
        return self.c is not None

    def sync(self):
        """Show the picked catch-up's points (not while something is dragged)."""
        if self.drag:
            return
        c = self.target()
        old = self.held
        if c is None and old is not None and not any(x is old or x[0] == old[0] for x in self.app.tempo["catch"]):
            return self.close()  # (its catch-up deleted)
        self.several = c == "several"
        if self.several:
            c = None
        if c is not None:
            self.held = c
        if c is not self.c:  # (another one picked, or the line came back from an undo: its steps here start again)
            self.c = c
            self.session = None if c is None else [list(p) for p in catch_get(c, 6) or DRAWN]
            self.pts = [list(p) for p in (self.session or DRAWN)]
            self.hist.reset()
        elif c is not None and not self._storing:
            now = [list(p) for p in catch_get(c, 6) or DRAWN]
            if now != self.pts:
                self.pts = now
                self.hist.reset()
        self.draw()

    def store(self):
        """Put the points on the catch-up (while dragging at most once per idle moment); the first put of a change
        makes its main undo step."""
        self.job = None
        if self.c is None or (catch_get(self.c, 6) or DRAWN) == self.pts:
            return
        self._storing = True
        try:
            if not self.stepped:
                self.app.push_undo(name=tr("tempo.step_catch_drawn"))
                self.stepped = True
            catch_set(self.c, 6, self.pts)
            self.app.tempo_changed()
        finally:
            self._storing = False

    def schedule(self):
        if not self.job:
            self.job = self.after_idle(self.store)

    def changed(self):
        """A finished change (not a drag): on the catch-up as one main step, one step here."""
        self.stepped = False
        self.store()
        self.draw()
        self.hist.mark()

    def set_points(self, pts):
        if self.live():
            self.pts = [list(p) for p in pts]
            self.changed()

    def put_state(self, state):
        """Ctrl+Z / Ctrl+Y here: the points as they were then (a main step too)."""
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.drag = None
        if self.live():
            self.pts = json.loads(state)
            self.stepped = False
            self.store()
            self.draw()

    def undo_key(self, d):
        """Ctrl+Z (d -1) / Ctrl+Y (1). While something is dragged, Ctrl+Z puts the points back as they were at the
        press with no step (the mouse still held moves nothing), and Ctrl+Y does nothing."""
        if not self.drag:
            return self.hist.undo() if d < 0 else self.hist.redo()
        if d > 0:
            return
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.drag = None
        self.pts = json.loads(self.press_pts)
        if self.stepped and self.c is not None:  # (its main step: back to nothing changed, then dropped)
            catch_set(self.c, 6, self.press_c)
            self._storing = True
            try:
                self.app.tempo_changed()
            finally:
                self._storing = False
            self.app.settle_history()
        self.stepped = False
        self.draw()

    def ok(self):
        if self.job:
            self.after_cancel(self.job)
            self.store()
        self.close()

    def cancel(self):
        """The points as they were when the window opened (or this catch-up was picked): one more main step."""
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        if self.c is not None and self.target() is self.c and self.session is not None \
                and (catch_get(self.c, 6) or DRAWN) != self.session:
            self.pts = self.session
            self.stepped = False
            self.store()
        self.close()

    def close(self):
        self.box.graph_window = None
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
        """Stretch i's bend handle (u, p) at its middle, or None (a level stretch: nothing to bend)."""
        (u0, p0, m), (u1, p1, _) = self.pts[i], self.pts[i + 1]
        if abs(p1 - p0) <= FLAT or u1 - u0 <= FLAT:
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
        cv.create_rectangle(x0, y0, x1, y1, outline=look.CHART_FRAME)
        cv.create_text(x0, y1 + 4 * s, text=tr("catch_graph.line"), anchor="nw", fill=look.LABEL, font=look.font(7))
        cv.create_text(x1, y1 + 4 * s, text=tr("catch_graph.end"), anchor="ne", fill=look.LABEL, font=look.font(7))
        at_pts = [p[0] for p in self.pts]  # (and a hair before each point: two on one spot = an upright jump)
        u = np.unique(np.clip(np.r_[np.linspace(0.0, 1.0, 241), at_pts, np.array(at_pts) - 1e-9], 0, 1))
        lw = max(1, round(1.5 * s))
        line = [c for a, b in zip(u, drawn_at(self.pts, u)) for c in (self.u2x(a), self.p2y(b))]
        cv.create_line(*line, fill=look.CHART_LINE if self.live() else look.CHART_GRID_STRONG, width=lw)
        if self.live():
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
            text = tr("catch_graph.at", at=fmt(round(at[0] * 100, 1)), p=fmt(round(at[1] * 100, 1)))
            cv.create_text(self.u2x(0) + 6 * s, y0 + 4 * s, text=text, anchor="nw", fill=look.VALUE,
                           font=look.font(8))
        self.info.config(text=tr("catch_graph.info") if self.live() else tr(
            "catch_graph.several" if self.several else "catch_graph.pick"))
        self.show_result()

    def show_result(self):
        """What the catch-up works out (as the bar shows it): its BPM, or why it can't."""
        at = [j for j, c in enumerate(self.app.tempo["catch"]) if c is self.c]
        if not self.live() or not at:
            return self.result.config(text="")
        res, bad = self.box.pane.catch_result(at[0])
        self.result.config(text=res, foreground=look.ERROR if bad else look.TEMPO_CATCH)

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
        if not self.live():
            return
        self.press_pts = json.dumps(self.pts)
        self.press_c = catch_get(self.c, 6)  # (as the catch-up held them: Ctrl+Z mid-drag puts exactly this back)
        self.stepped = False  # (each drag a main step of its own)
        got = self.hit(e.x, e.y)
        if got is None:  # (a new point here, on the stretch it splits; both halves keep its bend)
            if len(self.pts) >= DRAWN_POINTS:
                return
            free = e.state & 0x1
            u = self.x2u(e.x) if free else round(self.x2u(e.x) / SNAP) * SNAP
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            i = next(j for j, p in enumerate(self.pts) if p[0] > u)
            p = self.y2p(e.y) if free else round(self.y2p(e.y) / SNAP) * SNAP
            self.pts.insert(i, [u, p, self.pts[i - 1][2]])
            got = ("pt", i)
            self.schedule()
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
            m = min(DRAWN_BENDS[1], max(DRAWN_BENDS[0], (self.y2p(e.y) - p0) / (p1 - p0)))
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
        self.store()
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
        """Right-click a point: type where it is, or delete it (the ends: only their height); a bend handle:
        straight."""
        got = self.hit(e.x, e.y)
        if got is None or not self.live() or self.drag:
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
        if 0 < i < len(self.pts) - 1 and self.live():
            del self.pts[i]
            self.changed()

    def set_point(self, i, u, p):
        """Point i typed in (PointDialog): kept between its neighbours (an end keeps its place)."""
        pts = self.pts
        if not 0 <= i < len(pts) or not self.live():
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
            self.hover = [u, float(drawn_at(self.pts, [u])[0])]
        self.draw()
