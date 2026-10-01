"""A tumour setting's graph (the "…" next to its box in the tumour window): along the whole line, the box's number is
multiplied by the graph (100 % = as typed). Drag points, click to add one, right-click one for its menu (type its
exact value, delete it); presets and a formula; the shown height is a preset or typed. Ctrl+Z / Ctrl+Y step through
the changes made in the window. Stored as tm["graphs"][setting] (tumour.py)."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, formula, fmt
from notes.joined import shown_tumour
from notes.tumour import GRAPH_LIMIT, TUMOUR_DEFAULTS
from window.widgets import LocalUndo, Scrub

FLAT = [[0.0, 1.0], [1.0, 1.0]]
PRESETS = [(tr("graph_window.flat_off"), FLAT), (tr("graph_window.rise"), [[0.0, 0.0], [1.0, 1.0]]),
           (tr("graph_window.fall"), [[0.0, 1.0], [1.0, 0.0]]),
           (tr("graph_window.hill"), [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]]),
           (tr("graph_window.valley"), [[0.0, 1.0], [0.5, 0.0], [1.0, 1.0]])]
VIEWS = [(0, 100), (0, 200), (0, 400), (-100, 100), (-200, 200), (-400, 400), (-1000, 1000)]  # shown heights, %
LIMIT = GRAPH_LIMIT * 100  # the highest (and minus: lowest) value, %
MIN_SPAN = 1  # a typed height is at least this many %
U_SNAP, F_SNAP = 1 / 40, 0.05  # dragging moves points in these steps (Shift = free)
HINT = tr("graph_window.drag_a_point_to_move_it")
FORMULA_HINT = tr("graph_window.x_0_at_the_line_s")


def view_text(v):
    return tr("graph_window.to", v=fmt(v[0]), v2=fmt(v[1]))


def block_undo(win):
    """Ctrl+Z / Ctrl+Y do nothing in win (they'd reach the main window's undo)."""
    for k in "zZyY":
        win.bind(f"<Control-{k}>", lambda e: "break")


class GraphWindow(tk.Toplevel):
    def __init__(self, tw, key, label, unit):
        super().__init__(tw)
        self.tw, self.app, self.key, self.label, self.unit = tw, tw.app, key, label, unit
        self.title(tr("graph_window.graph", label=label))
        self.transient(tw)
        self.resizable(False, False)
        s = self.s = self.app.scale
        self.w, self.h = int(440 * s), int(220 * s)
        self.ml, self.mr, self.mt, self.mb = int(46 * s), int(12 * s), int(10 * s), int(22 * s)
        self.pts = [list(p) for p in FLAT]
        self.drag = None  # {"i": point being dragged}
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
        # the shown height: a preset, or typed in From / to
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        self.lo, self.hi = VIEWS[1]
        ttk.Label(row, text=tr("graph_window.show")).pack(side="left")
        self.view = tk.StringVar(value=view_text(VIEWS[1]))
        c = ttk.Combobox(row, textvariable=self.view, state="readonly", width=15,
                         values=[view_text(v) for v in VIEWS] + [tr("graph_window.custom_view")])
        c.pack(side="left", padx=(5, 0))
        c.bind("<<ComboboxSelected>>", lambda e: self.on_view_pick())
        self.range_vars, self.range_boxes = {}, {}
        for side, text in (("lo", tr("graph_window.from")), ("hi", tr("graph_window.to_box"))):
            lb = ttk.Label(row, text=text)
            lb.pack(side="left", padx=(10 if side == "lo" else 5, 0))
            var = self.range_vars[side] = tk.StringVar()
            e = self.range_boxes[side] = ttk.Entry(row, textvariable=var, width=7)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, sd=side: (self.on_range(sd), "break")[1])
            e.bind("<FocusOut>", lambda ev, sd=side: self.on_range(sd))
            Scrub(self.app, [(e, var, lambda sd=side: self.on_range(sd))], (10, 100, 1), -LIMIT, LIMIT, label=lb)
        ttk.Label(row, text="%").pack(side="left", padx=(3, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text=tr("graph_window.formula")).pack(side="left")
        self.formula = tk.StringVar()
        self.formula_box = ttk.Entry(row, textvariable=self.formula, width=34)
        self.formula_box.pack(side="left", padx=(5, 4))
        self.formula_box.bind("<Return>", lambda e: (self.apply_formula(), "break")[1])
        ttk.Button(row, text=tr("graph_window.apply"), command=self.apply_formula).pack(side="left")
        self.formula_note = ttk.Label(box, text=FORMULA_HINT, foreground="#777", font=("Segoe UI", 8))
        self.formula_note.pack(anchor="w")
        ttk.Label(box, text=HINT, foreground="#777", font=("Segoe UI", 8), justify="left").pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(6, 0))
        ttk.Button(row, text=tr("graph_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("graph_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.session = None  # the graph as it was when the window opened / these shapes were selected (see begin)

        cv = self.canvas
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<ButtonPress-3>", self.point_menu)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._storing = False
        self.hist = LocalUndo(self, lambda: json.dumps(self.pts), self.put_state)
        self.sync(fit_view=True)
        # where it was last time, else beside the tumour window
        self.update_idletasks()
        self.geometry(self.app.graph_pos or f"+{tw.winfo_rootx() + tw.winfo_width() + int(8 * s)}+{tw.winfo_rooty()}")
        self.bind("<Configure>", self.remember, add="+")

    # ------------------------------------------------------------ the shapes' graph

    def tm(self):
        return dict(TUMOUR_DEFAULTS, **(self.app.shown_tumours() or {}))

    def sync(self, fit_view=False):
        """Show the first selected line's graph (not while a point is being dragged)."""
        tgts = self.app.tumour_targets()
        old = self.session["tgts"] if self.session else None
        new = old is None or len(old) != len(tgts) or any(a is not b for a, b in zip(old, tgts))
        if new:
            self.begin()  # (other shapes selected: what was done to the last ones is kept, like OK)
        if self.drag:
            return
        g = (self.tm().get("graphs") or {}).get(self.key)
        self.pts = [list(p) for p in (g or FLAT)]
        # other shapes, or the graph changed from outside (the main window's undo): its steps here start again
        if new or not self._storing and self.hist.get() != self.hist.states[self.hist.at]:
            self.hist.reset()
        if fit_view or new and not self.fits(self.view_range()):
            self.fit_view()
        self.draw()

    def store(self):
        """Put the graph on the selected lines (while dragging at most once per idle moment)."""
        self.job = None
        self._storing = True
        try:
            self.tw.set_graph(self.key, self.pts)
        finally:
            self._storing = False

    def schedule(self):
        if not self.job:
            self.job = self.after_idle(self.store)

    def set_points(self, pts):
        if not self.app.tumour_targets():
            return
        self.pts = [list(p) for p in pts]
        if not self.fits(self.view_range()):
            self.fit_view()
        self.draw()
        self.store()
        self.hist.mark()

    def put_state(self, state):
        """Ctrl+Z / Ctrl+Y in the window: the graph as it was then."""
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.drag = None
        if not self.app.tumour_targets():
            return
        self.pts = json.loads(state)
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
                    raise ValueError(tr("graph_window.it_doesn_t_give_a_number"))
                pts.append([i / 40, min(GRAPH_LIMIT, max(-GRAPH_LIMIT, v))])
        except (ValueError, ZeroDivisionError, OverflowError, TypeError) as e:
            msg = str(e) if isinstance(e, ValueError) else tr("graph_window.it_doesn_t_give_a_number")
            self.formula_box.config(style="Bad.TEntry")
            self.formula_note.config(text=tr("graph_window.can_t_use_it", msg=msg), foreground="#d00000")
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
        self.session = {"tgts": tgts,
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
        # (still part of the tumour window's change: its Accept / X decides whether it's kept)
        app, ses = self.app, self.session
        if ses and any(t is s for t in ses["tgts"] for s in app.shapes):
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
            self.tw.changed(("graph", self.key))
        self.close()

    def close(self):
        self.tw.graph_window = None
        self.destroy()

    def remember(self, e):
        if e.widget is self:
            self.app.graph_pos = f"+{self.winfo_x()}+{self.winfo_y()}"

    # ------------------------------------------------------------ view

    def view_range(self):
        return self.lo, self.hi

    def fits(self, v):
        return all(v[0] / 100 - 1e-9 <= f <= v[1] / 100 + 1e-9 for _, f in self.pts)

    def fit_view(self):
        """The smallest view the graph fits in (0 to 200 % when it does)."""
        order = [VIEWS[1], VIEWS[0], VIEWS[2], VIEWS[3], VIEWS[4], VIEWS[5], VIEWS[6]]
        self.set_view(*next((v for v in order if self.fits(v)), VIEWS[-1]))

    def set_view(self, lo, hi):
        """Show from lo to hi % (the dropdown names the preset, if it's one; the From / to boxes show them)."""
        self.lo, self.hi = lo, hi
        self.view.set(next((view_text(v) for v in VIEWS if v == (lo, hi)), tr("graph_window.custom_view")))
        for side, value in (("lo", lo), ("hi", hi)):
            self.range_vars[side].set(fmt(value))
        self.draw()

    def on_view_pick(self):
        v = next((v for v in VIEWS if view_text(v) == self.view.get()), None)
        if v:
            self.set_view(*v)
        else:  # (Custom: type it in the boxes)
            self.range_boxes["lo"].focus_set()
            self.range_boxes["lo"].select_range(0, "end")

    def on_range(self, side):
        """A number typed in From / to (the other end moves along when they'd cross)."""
        try:
            value = float(calc(self.range_vars[side].get()))
            if not math.isfinite(value):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            return self.set_view(self.lo, self.hi)
        value = min(LIMIT, max(-LIMIT, round(value, 4)))
        lo, hi = (value, self.hi) if side == "lo" else (self.lo, value)
        if hi - lo < MIN_SPAN:
            if side == "lo":
                lo = min(lo, LIMIT - MIN_SPAN)
                hi = lo + MIN_SPAN
            else:
                hi = max(hi, MIN_SPAN - LIMIT)
                lo = hi - MIN_SPAN
        if (lo, hi) != (self.lo, self.hi):
            self.set_view(lo, hi)
        else:
            self.range_vars[side].set(fmt(value))

    def grid_step(self):
        """% between the grid's lines: a round number, about 8 lines (25 % at 0 to 100 %)."""
        span = self.hi - self.lo
        raw = max(span / 8, min(25, span / 4))
        step = 10 ** math.floor(math.log10(raw))
        return step * next(m for m in (1, 2, 2.5, 5, 10) if step * m >= raw - 1e-9)

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

    def box_number(self):
        """The box's number as the box shows it (what 100 % is)."""
        tm = self.tm()
        return tm[self.key] * (self.app.ppq if self.key in ("length", "dist") else 100 if self.key == "slant" else 1)

    def value_text(self, f):
        """What f (a multiplier) makes of the box's number, as the box shows it."""
        return tr("graph_window.text", f=fmt(round(f * 100, 1)), v=fmt(round(self.box_number() * f, 2)),
                  unit=self.unit)

    def f_snap(self):
        """Dragging moves points in steps of this (5 %, finer when the view is small)."""
        return min(F_SNAP, self.grid_step() / 500)

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
        step = self.grid_step()
        for k in range(math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9) + 1):
            v = round(k * step, 6)
            y = self.f2y(v / 100)
            cv.create_line(x0, y, x1, y, fill="#9fb2cf" if v in (0, 100) else "#d3dff0")
            cv.create_text(x0 - 4 * s, y, text=f"{fmt(v)} %", anchor="e", fill="#333", font=("Segoe UI", 7))
        for j in range(1, 4):
            x = self.u2x(j / 4)
            cv.create_line(x, y_top, x, y_bot, fill="#d3dff0")
        cv.create_rectangle(x0, y_top, x1, y_bot, outline="#808080")
        cv.create_text(x0, y_bot + 4 * s, text=tr("graph_window.line_start"), anchor="nw", fill="#333",
                       font=("Segoe UI", 7))
        cv.create_text(x1, y_bot + 4 * s, text=tr("graph_window.line_end"), anchor="ne", fill="#333",
                       font=("Segoe UI", 7))
        if r0 > 1e-9 or r1 < 1 - 1e-9:
            cv.create_text((self.u2x(r0) + self.u2x(r1)) / 2, y_bot + 4 * s, text=tr("graph_window.tumour_range"),
                           anchor="n",
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
            text = tr("graph_window.at_of_the_line", at=fmt(round(at[0] * 100, 1)), value_text=self.value_text(at[1]))
            cv.create_text(x1 - 4 * s, y_top + 4 * s, text=text, anchor="ne", fill="#0a50e0", font=("Segoe UI", 8))
        name = self.label.lower()
        self.info.config(text=tr("graph_window.along_the_line_100_the_box", label=self.label,
                                 split=self.value_text(1).split('= ')[1])
                         if self.app.tumour_targets() else tr("graph_window.select_a_line_with_tumours_to", name=name))

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
        if i is None:  # a new point here
            u = self.x2u(e.x)
            if not e.state & 0x1:
                u = round(u / U_SNAP) * U_SNAP
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            f = self.y2f(e.y) if e.state & 0x1 else self.snap_f(self.y2f(e.y))
            i = next(j for j, p in enumerate(self.pts) if p[0] > u)
            self.pts.insert(i, [u, f])
            self.schedule()
        self.drag = {"i": i}
        self.draw()

    def motion(self, e):
        d = self.drag
        if not d:
            return
        i, free = d["i"], e.state & 0x1
        u, f = self.x2u(e.x), self.y2f(e.y)
        if not free:
            u, f = round(u / U_SNAP) * U_SNAP, self.snap_f(f)
        if i == 0 or i == len(self.pts) - 1:
            u = self.pts[i][0]  # the ends stay at the line's start / end
        else:
            u = min(max(u, self.pts[i - 1][0]), self.pts[i + 1][0])
        if [u, f] == self.pts[i]:
            return
        self.pts[i] = [u, f]
        self.draw()
        self.schedule()

    def snap_f(self, f):
        step = self.f_snap()
        return min(self.hi / 100, max(self.lo / 100, round(round(f / step) * step, 6)))

    def release(self, e):
        self.drag = None
        self.draw()
        self.hist.mark()

    def point_menu(self, e):
        """Right-click a point: type its exact value, or delete it (not when only the two ends are left)."""
        i = self.point_at(e.x, e.y)
        if i is None or not self.app.tumour_targets():
            return
        m = tk.Menu(self, tearoff=0)
        m.add_command(label=tr("graph_window.value"), command=lambda: PointDialog(self, i))
        m.add_command(label=tr("graph_window.delete_point"), command=lambda: self.delete_point(i),
                      state="normal" if len(self.pts) > 2 else "disabled")
        m.tk_popup(e.x_root, e.y_root)

    def delete_point(self, i):
        """An end's neighbour becomes the new end (it moves to the line's start / end)."""
        if len(self.pts) <= 2 or not self.app.tumour_targets():
            return
        del self.pts[i]
        self.pts[0][0], self.pts[-1][0] = 0.0, 1.0
        self.draw()
        self.store()
        self.hist.mark()

    def set_point(self, i, u, f):
        """Point i typed in (PointDialog)."""
        if [u, f] == self.pts[i] or not self.app.tumour_targets():
            return
        self.pts[i] = [u, f]
        if not self.fits(self.view_range()):
            self.fit_view()
        self.draw()
        self.store()
        self.hist.mark()

    def on_hover(self, e):
        """Under the mouse: the graph's value at that spot along the line."""
        self.hover = None
        if e is not None and not self.drag and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            f = float(np.interp(u, [p[0] for p in self.pts], [p[1] for p in self.pts]))
            self.hover = [u, f]
        self.draw()


class PointDialog(tk.Toplevel):
    """Right-click a graph point > Value…: where it is along the line and its value, typed (in %, or as the number
    it makes of the box's number)."""

    def __init__(self, gw, i):
        super().__init__(gw)
        self.gw, self.i = gw, i
        self.title(tr("graph_window.point"))
        self.transient(gw)
        self.resizable(False, False)
        block_undo(self)
        u, f = gw.pts[i]
        self.end = i in (0, len(gw.pts) - 1)
        self.number = gw.box_number()  # (100 %)
        self._linking = False
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.vars, self.boxes = {}, {}
        rows = [("along", tr("graph_window.along"), fmt(round(u * 100, 4)), "%", (1, 10, 0.1), 0, 100),
                ("value", tr("graph_window.value_box"), fmt(round(f * 100, 4)), "%", (5, 25, 1), -LIMIT, LIMIT)]
        if abs(self.number) > 1e-12:
            rows.append(("number", "=", fmt(round(self.number * f, 4)), gw.unit, (1, 10, 0.1), None, None))
        for r, (key, text, value, unit, steps, lo, hi) in enumerate(rows):
            lb = ttk.Label(box, text=text)
            lb.grid(row=r, column=0, sticky="w", pady=2)
            var = self.vars[key] = tk.StringVar(value=value)
            e = self.boxes[key] = ttk.Entry(box, textvariable=var, width=10)
            e.grid(row=r, column=1, sticky="w", padx=(6, 4), pady=2)
            ttk.Label(box, text=unit).grid(row=r, column=2, sticky="w")
            if key == "along" and self.end:
                e.config(state="disabled")  # (the ends stay at the line's start / end)
                continue
            Scrub(gw.app, [(e, var, None)], steps, lo, hi, label=lb)
        if "number" in self.vars:
            self.vars["value"].trace_add("write", lambda *_: self.link("value", "number", self.number / 100))
            self.vars["number"].trace_add("write", lambda *_: self.link("number", "value", 100 / self.number))
        btns = ttk.Frame(box)
        btns.grid(row=len(rows), column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=tr("graph_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(btns, text=tr("graph_window.cancel"), command=self.destroy).pack(side="left", padx=(4, 0))
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{gw.winfo_pointerx() + 10}+{gw.winfo_pointery() + 10}")
        self.grab_set()
        e = self.boxes["value"]
        e.focus_set()
        e.select_range(0, "end")

    def link(self, src, dst, factor):
        """% and the number it makes follow each other."""
        if self._linking:
            return
        try:
            value = float(calc(self.vars[src].get())) * factor
        except (ValueError, ZeroDivisionError):
            return
        self._linking = True
        self.vars[dst].set(fmt(round(value, 4)))
        self._linking = False

    def ok(self):
        gw, i = self.gw, self.i
        try:
            along = float(calc(self.vars["along"].get())) / 100
            value = float(calc(self.vars["value"].get())) / 100
            if not (math.isfinite(along) and math.isfinite(value)):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            return self.bell()
        pts = gw.pts
        if self.end:
            along = pts[i][0]
        else:
            along = min(max(along, pts[i - 1][0]), pts[i + 1][0])
        value = min(GRAPH_LIMIT, max(-GRAPH_LIMIT, value))
        self.destroy()
        gw.set_point(i, along, value)
