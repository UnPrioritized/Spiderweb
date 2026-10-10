"""The Hz bass window's "Wave shape…" window (user, 2026-10-10; notes/CLAUDE.md "DRAWN WAVE SHAPE"): a line drawn
through one wave of the Hz bass (left to right) over its keys (bottom to top); every key row hits wherever the line
crosses it (hzbass.trace_hits). Straight lines from point to point. The line is stretched to all the keys, so the
key rows are drawn between its lowest and highest point, each with a dot where it hits.

Mouse: drag a point; a press on the line = a new point there, dragged; a press elsewhere = a new point at the line's
end; right-click / double-click a point = gone (2 stay). Snapped to the grid (1/16 each way), Shift = free. One undo
step of the Hz bass window per change, on release; Ctrl+Z / Esc while held = back to the press (HzMouse.held_fx).
The shape is the Hz bass's (its picked layer's) sound setting hz["trace"], kept in the Hz bass window's `extra`."""

import copy
import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.hzbass import START_TRACE, TRACE_POINTS, clean_extra, key_range, trace_hits
from window import look
from window.widgets import Tooltip, remember_place

GRID = 16  # grid lines each way (snapped to)
PAD = 14  # px round the drawing area (x scale)
GRAB = 7  # px: how near a point / the line a press takes it
ROWS_SHOWN = 64  # more key rows than this: only some drawn (every key still hits)
# the starting shapes of the Shapes ▾ menu (my picks): a saw = each key once, the others each key twice a wave
SHAPES = {"saw": [[0.0, 0.0], [1.0, 1.0]],
          "triangle": [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]],
          "sine": [[i / 32, round(0.5 - 0.5 * math.cos(2 * math.pi * i / 32), 6)] for i in range(33)],
          "square": [[0.0, 0.0], [0.0, 1.0], [0.5, 1.0], [0.5, 0.0], [1.0, 0.0]]}


def open_trace(hz):
    """Hz bass window → Wave shape…: the window opened (or brought up)."""
    if hz.trace_win is not None and hz.trace_win.winfo_exists():
        hz.trace_win.deiconify()
        hz.trace_win.lift()
    else:
        hz.trace_win = TraceWindow(hz)
    hz.trace_win.refresh()
    return hz.trace_win


def shown_trace(extra):
    """The wave shape a Hz bass's settings have (as it starts when there's none): a list of [x, y]."""
    return [list(p) for p in extra.get("trace") or START_TRACE]


class TraceWindow(tk.Toplevel):
    def __init__(self, hz):
        super().__init__(hz)
        self.hz, self.app = hz, hz.app
        s = self.s = hz.app.scale
        self.title(tr("hz.trace_title"))
        self.transient(hz)
        self.geometry(f"{round(420 * s)}x{round(400 * s)}")
        self.minsize(round(260 * s), round(240 * s))
        self.pts = shown_trace(hz.extra)
        self.drag = None  # {"i": point number, "was": points, "before": fx state, ...} while the mouse holds one
        self.placed = False  # (the last press put a new point in)
        hint = ttk.Label(self, text=tr("hz.trace_hint"), foreground=look.HINT, padding=(8, 0, 8, 8))
        hint.pack(side="bottom", fill="x")  # (the rows under the drawing first: they always show)
        hint.bind("<Configure>", lambda e: hint.config(wraplength=max(1, e.width - round(16 * s))))
        bar = ttk.Frame(self, padding=(8, 2, 8, 6))
        bar.pack(side="bottom", fill="x")
        b = ttk.Button(bar, text=tr("hz.trace_reset"), command=self.reset, takefocus=False)
        b.pack(side="left")
        Tooltip(b, tr("hz.trace_reset_tip"))
        m = ttk.Menubutton(bar, text=tr("hz.trace_shapes"), takefocus=False)
        m.pack(side="left", padx=(6, 0))
        m["menu"] = menu = tk.Menu(m, tearoff=False)
        for name in SHAPES:
            menu.add_command(label=tr("hz.trace_" + name), command=lambda name=name: self.use(SHAPES[name]))
        Tooltip(m, tr("hz.trace_shapes_tip"))
        self.says = ttk.Label(bar, text="", foreground=look.INFO)
        self.says.pack(side="left", padx=(10, 0))
        c = self.canvas = tk.Canvas(self, background=look.CHART_BG, highlightthickness=0, takefocus=True)
        c.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        c.bind("<Configure>", lambda e: self.draw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<ButtonPress-3>", self.on_right)
        c.bind("<Escape>", lambda e: self.cancel_drag())
        self.protocol("WM_DELETE_WINDOW", self.close)
        remember_place(self, "hz_trace")

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

    def on_line(self, e):
        """The piece of the line under the mouse: the number of the point it goes to, or None."""
        best, near = None, GRAB * self.s
        for i in range(1, len(self.pts)):
            (ax, ay), (bx, by) = self.xy(self.pts[i - 1]), self.xy(self.pts[i])
            dx, dy = bx - ax, by - ay
            t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((e.x - ax) * dx + (e.y - ay) * dy) / (dx * dx + dy * dy)))
            d = ((ax + t * dx - e.x) ** 2 + (ay + t * dy - e.y) ** 2) ** 0.5
            if d <= near:
                best, near = i, d
        return best

    def rows(self):
        """How many key rows the Hz bass shown has (its box's keys; the keys it will have before its first note)."""
        sh = self.hz.target()
        if sh is not None and sh["kind"] in ("custom", "funnel") and sh.get("pts"):
            lo, hi = key_range(sh)
        else:
            lo, hi = self.app.hz_defaults["lo"], self.app.hz_defaults["hi"]
        return max(1, hi - lo + 1)

    # ------------------------------------------------------------ drawing

    def refresh(self):
        """The shape from the Hz bass window (another Hz bass shown, undo...), unless the mouse holds a point."""
        if self.drag is None:
            self.pts = shown_trace(self.hz.extra)
        self.draw()

    def draw(self):
        c, s = self.canvas, self.s
        c.delete("all")
        x0, y0, w, h = self.area()
        for i in range(GRID + 1):  # (the grid: quarters stronger)
            colour = look.CHART_GRID_STRONG if i % (GRID // 4) == 0 else look.CHART_GRID
            c.create_line(x0 + i * w / GRID, y0, x0 + i * w / GRID, y0 + h, fill=colour)
            c.create_line(x0, y0 + i * h / GRID, x0 + w, y0 + i * h / GRID, fill=colour)
        lo, hi = min(p[1] for p in self.pts), max(p[1] for p in self.pts)
        for a, b in ((hi, 1.0), (0.0, lo)):  # (outside the line's height: no keys there, it's stretched to them)
            if b - a > 1e-9:
                c.create_rectangle(x0, y0 + (1 - b) * h, x0 + w, y0 + (1 - a) * h, fill=look.CHART_BAND, outline="",
                                   stipple="gray50")
        n = self.rows()
        every = max(1, -(-n // ROWS_SHOWN))
        r = 2.5 * s
        total = 0
        for k in range(n):
            y = (k + 0.5) / n
            hits = trace_hits(self.pts, y)
            total += len(hits)
            if k % every:
                continue
            py = y0 + (1 - (lo + y * (hi - lo))) * h if hi - lo > 1e-12 else y0 + (1 - lo) * h
            if hi - lo > 1e-12:
                c.create_line(x0, py, x0 + w, py, fill=look.CHART_BAND)
            for x in hits:
                px = x0 + x * w
                c.create_oval(px - r, py - r, px + r, py + r, fill=look.HZ_GREEN, outline="")
        c.create_rectangle(x0, y0, x0 + w, y0 + h, outline=look.CHART_FRAME)
        c.create_line(*[v for p in self.pts for v in self.xy(p)], fill=look.CHART_LINE, width=max(1, round(2 * s)))
        q = 3.5 * s
        for p in self.pts:
            x, y = self.xy(p)
            c.create_rectangle(x - q, y - q, x + q, y + q, fill=look.CHART_POINT, outline=look.CHART_LINE)
        times = total / n
        if abs(times - 1.0) < 1e-9:
            self.says.config(text=tr("hz.trace_same"), foreground=look.INFO)
        else:
            self.says.config(text=tr("hz.trace_times", times=f"{times:.2f}".rstrip("0").rstrip(".")),
                             foreground=look.WARN if times > 1.0 else look.INFO)

    # ------------------------------------------------------------ the mouse

    def on_press(self, e):
        self.canvas.focus_set()
        if self.drag is not None:
            return
        was = [list(p) for p in self.pts]
        i = self.grabbed(e)
        self.placed = i is None  # (a double click on the point this click puts in doesn't take it out again)
        if i is None:
            if len(self.pts) >= TRACE_POINTS:
                return self.bell()
            j = self.on_line(e)
            i = j if j is not None else len(self.pts)
            self.pts.insert(i, self.point_at(e))
        self.drag = {"i": i, "was": was, "before": self.hz.fx.state(), "at": (e.x, e.y), "moved": False}
        self.draw()

    def on_drag(self, e):
        d = self.drag
        if d is None:
            return
        if not d["moved"] and max(abs(e.x - d["at"][0]), abs(e.y - d["at"][1])) < 3:
            return  # (under 3 px: still a click, the point stays where it is)
        d["moved"] = True
        self.pts[d["i"]] = self.point_at(e)
        self.draw()

    def on_release(self, e):
        d, self.drag = self.drag, None
        if d is None:
            return
        if self.pts != d["was"]:
            self.save(d["before"])
        self.draw()

    def on_double(self, e):
        if not self.placed:
            self.remove(e)

    def on_right(self, e):
        if self.drag is None:
            self.remove(e)

    def remove(self, e):
        """The point under the mouse taken away (the line keeps at least 2)."""
        i = self.grabbed(e)
        if i is None:
            return
        if len(self.pts) <= 2:
            return self.bell()
        before = self.hz.fx.state()
        del self.pts[i]
        self.save(before)
        self.draw()

    def cancel_drag(self):
        """Ctrl+Z / Esc while a point is held: back as at the press, no undo step."""
        if self.drag is not None:
            self.pts = self.drag["was"]
            self.drag = None
            self.draw()

    def held(self):
        """What puts back the point the mouse holds (HzMouse.held_fx), or None."""
        return self.cancel_drag if self.drag is not None else None

    def reset(self):
        """Back to straight up at the wave's start (every key together): one undo step."""
        self.use(START_TRACE)

    def use(self, pts):
        """The line replaced by these points (Reset, a starting shape): one undo step (none when it's the same)."""
        if self.drag is not None:
            return
        before = self.hz.fx.state()
        self.pts = [list(p) for p in pts]
        self.save(before)
        self.draw()

    def save(self, before):
        """The shape drawn becomes the Hz bass's: one undo step of the Hz bass window."""
        hz = self.hz
        hz.extra = clean_extra(dict(hz.extra, trace=[list(p) for p in self.pts]))
        if hz.fx.now() != before:
            hz.fx.tidy()
            hz.commit(tr("hz.step_trace"), copy.deepcopy(hz.tones), before)

    def close(self):
        self.cancel_drag()
        self.withdraw()
