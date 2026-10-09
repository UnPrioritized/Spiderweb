"""The loudness pane of the Hz bass window: under the notes (and the effects pane), in step with their time. Two
lines, multiplied like a DAW's (user):
- Notes: each note's own loudness line in velocity 1..127 (tone["vel"], u 0..1 along the note). It REPLACES the Hz
  bass's velocity (side panel) for that note (user); a note without one takes that velocity, drawn faint and flat.
- Layer: the picked layer's loudness line (hz["loud"], 0..100 %, beats like the effect lines), the last thing on
  the velocity, like a mixer's fader (0 = silence); faint and flat at 100 % until drawn.
The two buttons above pick which one is drawn (full colour, the other faint). Tools like the main velocity pane's
(user): Linear (Ctrl = flat), Curve (its round handle bends it), Pencil; Shift = snap to the grid. Every note under
the line drawn (only the selected ones when some are) takes its part of it. The last line / curve's handles can move
its ends (and bend a curve) until something else changes; Enter, a right-click or a click elsewhere = done. One
undo step per line, made when the mouse is let go. Its top edge drags its height (remembered)."""

import copy
import json
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.engine import cached_path
from notes.envelope import env_at, env_values, paint_env, tidy_env, velocity_env
from notes.hzbass import clean_line, left_edge
from roll.roll_shared import CTRL, SELECTED_COLOR, SHIFT, SLOT_COLORS, fade
from window import look
from window.hz_layers import layer_colour
from window.velocity import CURVE_STEPS, curve_mid, segment
from window.widgets import Tooltip

TOP = 127.0  # the pane counts both lines in velocity steps: the layer's 100 % = 127
NOTE_LEVELS = (127, 96, 64, 32)
LAYER_LEVELS = (1.0, 0.75, 0.5, 0.25, 0.0)


def curve_line(a, b, c, pad, lo):
    """The curve a -> b bent towards c (beat, value) as points, values kept in lo..TOP, reaching pad beats past both
    ends (velocity.curve_env, with the layer's 0 allowed)."""
    if a[0] > b[0]:
        a, b = b, a
    if a[0] == b[0]:
        return segment(a, b, pad)
    cx = min(max(c[0], a[0]), b[0])
    pts = []
    for i in range(CURVE_STEPS + 1):
        t = i / CURVE_STEPS
        s = 1 - t
        pts.append([s * s * a[0] + 2 * s * t * cx + t * t * b[0],
                    max(lo, min(TOP, s * s * a[1] + 2 * s * t * c[1] + t * t * b[1]))])
    return [[a[0] - pad, a[1]]] + pts + [[b[0] + pad, b[1]]]


def bend_limit(a, b, c, lo):
    """c with its value pulled in just enough that the curve a -> b stays inside lo..TOP (velocity.limit_bend)."""
    hi = TOP + ((TOP - a[1]) * (TOP - b[1])) ** 0.5
    low = lo - ((a[1] - lo) * (b[1] - lo)) ** 0.5
    return [c[0], min(max(c[1], low), hi)]


def tidy_line(pts):
    """A line over beats without repeats or points in the middle of a straight stretch (tidy_env, any beats)."""
    if not pts:
        return pts
    lo, hi = pts[0][0], pts[-1][0]
    if hi <= lo:
        return [list(pts[-1])]
    span = hi - lo
    return [[lo + u * span, v] for u, v in tidy_env([[(p[0] - lo) / span, p[1]] for p in pts])]


class LoudPane:
    def __init__(self, win):
        self.win, self.app, s = win, win.app, win.s
        self.s = s
        self.edit = None  # the line being drawn / the handle being dragged
        self.curve = None  # the last line / curve drawn, while its handles can still change it
        self.says = ""
        self.pad, self.edge = round(7 * s), round(4 * s)
        self.which = tk.StringVar(value="notes")  # the line drawn: "notes" / "layer"
        self.tool = tk.StringVar(value="line")
        box = self.box = ttk.Frame(win)
        bar = ttk.Frame(box, padding=(4, 2))
        bar.pack(fill="x")
        for key in ("notes", "layer"):
            b = ttk.Radiobutton(bar, text=tr("hz.loud_" + key), value=key, variable=self.which, style="Toolbutton",
                                takefocus=False, command=self.picked)
            b.pack(side="left", padx=1)
            Tooltip(b, tr(f"hz.loud_{key}_tip"))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        for key, label in (("line", "app.linear"), ("curve", "app.curve"), ("pencil", "app.pencil")):
            ttk.Radiobutton(bar, text=tr(label), value=key, variable=self.tool, style="Toolbutton", takefocus=False,
                            command=self.picked).pack(side="left", padx=1)
        ttk.Label(bar, text=tr("hz.loud_hint"), foreground=look.HINT, font=look.font(8)).pack(side="left", padx=(10, 0))
        c = self.canvas = tk.Canvas(box, background=look.FX_BG, highlightthickness=0, cursor="crosshair",
                                    height=self.app.hz_loud_h or round(110 * s))
        c.pack(fill="both", expand=True)
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-3>", self.on_menu)
        c.bind("<Motion>", self.on_motion)
        c.bind("<Leave>", lambda e: self.say(""))
        c.bind("<MouseWheel>", lambda e: self.win.fx.on_wheel(e))  # (the notes' time, as in the effects pane)

    # ------------------------------------------------------------ what it shows

    def lowest(self):
        return 0.0 if self.which.get() == "layer" else 1.0

    def bottom(self):
        return self.canvas.winfo_height() - 1 - self.pad

    def y_of(self, v):
        bot = self.bottom()
        return bot - v / TOP * (bot - self.pad)

    def value_at(self, y):
        bot = self.bottom()
        return max(self.lowest(), min(TOP, (bot - y) / max(1, bot - self.pad) * TOP))

    def base_vel(self, n):
        """The velocity a note without its own line gets: the Hz bass's (side panel) where it starts."""
        sh = self.win.target()
        if sh is None:
            return float(self.app.defaults.get("vel0", 100))
        bs = [b for b, _ in cached_path(sh)]
        lo, hi = min(bs), max(bs)
        u = (left_edge(sh) + n["t"] - lo) / (hi - lo) if hi > lo else 0.0
        return float(env_values(velocity_env(sh), [min(1.0, max(0.0, u))])[0])

    def targets(self, tones, lo, hi):
        """The notes a line from beat lo to hi changes: those it reaches, only the selected ones when some are."""
        sel = self.win.sel
        return [i for i, n in enumerate(tones) if (not sel or i in sel) and n["t"] < hi and n["t"] + n["len"] > lo]

    def applied(self, drawn, tones, loud):
        """(tones, layer line) with the line drawn (beat, value in velocity steps) put on: the notes it reaches
        take their part of it, or the layer's line (0..1) takes it. Copies; tones / loud stay as they are."""
        lo, hi = drawn[0][0], drawn[-1][0]
        if self.which.get() == "layer":
            pts = [[b, v / TOP] for b, v in drawn]
            line = paint_env([list(p[:2]) for p in loud] if loud else [[0.0, 1.0]], pts)
            if line[0][0] < 0:  # (before the left edge: none; starts with its value there)
                first = env_at(line, [p[0] for p in line], 0.0)
                line = [[0.0, first]] + [p for p in line if p[0] > 0]
            return tones, clean_line(tidy_line(line))
        tones = copy.deepcopy(tones)
        for i in self.targets(tones, lo, hi):
            n = tones[i]
            span = max(n["len"], 1e-12)
            base = n.get("vel") or [[0.0, self.base_vel(n)], [1.0, self.base_vel(n)]]
            env = tidy_env(paint_env(base, [[(b - n["t"]) / span, v] for b, v in drawn]))
            n["vel"] = [[round(u, 9), round(min(TOP, max(1.0, v)), 3)] for u, v in env]
        return tones, loud

    def shown(self):
        """(tones, layer line) as they'd be with the line being drawn now (or as they are)."""
        win, ed = self.win, self.edit
        if ed and ed.get("drawn"):
            base = ed["base"]
            return self.applied(ed["drawn"], base[0], base[1])
        return win.tones, win.loud

    def colours(self):
        hz = (self.win.target() or {}).get("hz") or {}
        return layer_colour(hz, hz.get("layer", 0)) if hz.get("layers") else SLOT_COLORS[0]

    def redraw(self):
        c, win, s = self.canvas, self.win, self.s
        c.delete("all")
        w, h, kb = c.winfo_width(), c.winfo_height(), win.kb_w
        if w < 50 or h < 2 * self.pad + 10 or not self.box.winfo_manager():
            return
        layer = self.which.get() == "layer"
        lw = max(1, round(s))
        for v in (LAYER_LEVELS if layer else NOTE_LEVELS):
            c.create_line(kb, self.y_of(v * TOP if layer else v), w, self.y_of(v * TOP if layer else v),
                          fill=look.FX_GRID)
        tones, loud = self.shown()
        edge = self.colours()[1]
        for i, n in enumerate(tones):  # each note's line, from a stem at its start (like velocity bars)
            colour = SELECTED_COLOR[1] if i in win.sel else edge
            if layer:
                colour = fade(colour)
            x0, x1 = win.x_of(n["t"]), win.x_of(n["t"] + n["len"])
            if x1 < kb or x0 > w:
                continue
            pts = n.get("vel")
            if pts:
                vs = env_values(pts, [0.0, 1.0])
                xy = ([(x0, vs[0])] + [(x0 + u * (x1 - x0), v) for u, v in pts if 0 < u < 1] + [(x1, vs[1])])
            else:
                v = self.base_vel(n)
                xy = [(x0, v), (x1, v)]
            c.create_line(x0, self.y_of(0), x0, self.y_of(xy[0][1]), fill=colour, width=lw)
            c.create_line(*[q for x, v in xy for q in (x, self.y_of(v))], fill=colour, width=2 * lw if pts else lw,
                          dash=() if pts else (3, 3))
        pts = [(win.x_of(p[0]), p[1] * TOP) for p in loud] if loud else []  # the layer's line, over everything
        xy = ([(kb, pts[0][1])] + pts + [(w, pts[-1][1])]) if pts else [(kb, TOP), (w, TOP)]
        colour = look.CHART_LINE if layer else look.CHART_LINE_FAINT
        c.create_line(*[q for x, v in xy for q in (max(kb, x), self.y_of(v))], fill=colour,
                      width=2 * lw if loud and layer else lw, dash=() if loud else (3, 3))
        ed = self.edit
        if ed and ed.get("curve"):
            self.draw_handles(*ed["curve"], ed["kind"], lw)
        elif ed and len(ed.get("trail", ())) >= 2:
            c.create_line(*[q for b, v in ed["trail"] for q in (win.x_of(b), self.y_of(v))], fill=look.CHART_LINE,
                          width=lw)
        cv = None if ed else self.live_curve()
        if cv:
            self.draw_handles(cv["a"], cv["b"], cv["c"], cv["kind"], lw)
        c.create_rectangle(0, 0, kb, h, fill=look.FX_NAMES, outline="")
        for v in (LAYER_LEVELS if layer else NOTE_LEVELS):
            y = min(max(self.y_of(v * TOP if layer else v), 6 * s), h - 6 * s)
            c.create_text(kb - 5, y, text=f"{round(v * 100)} %" if layer else str(v), anchor="e", fill=look.LABEL,
                          font=look.font(7))
        c.create_line(kb, 0, kb, h, fill=look.FX_EDGE)
        c.create_line(0, 0, w, 0, fill=look.FX_EDGE)

    def draw_handles(self, a, b, c, kind, lw):
        """The red line / curve with square handles on its ends and a curve's round handle in the middle."""
        cv, win, r = self.canvas, self.win, 4 * self.s
        pts = curve_line(a, b, c, 0, self.lowest())[1:-1]
        if len(pts) >= 2:
            cv.create_line(*[q for x, v in pts for q in (win.x_of(x), self.y_of(v))], fill=look.CHART_LINE, width=lw)
        if a[0] == b[0]:
            return
        if kind == "curve":
            m = curve_mid(a, b, c)
            x, y = win.x_of(m[0]), self.y_of(m[1])
            cv.create_oval(x - r, y - r, x + r, y + r, fill=look.CHART_POINT, outline=look.CHART_LINE, width=lw)
        for p in (a, b):
            x, y = win.x_of(p[0]), self.y_of(p[1])
            cv.create_rectangle(x - r, y - r, x + r, y + r, fill=look.CHART_POINT, outline=look.CHART_LINE, width=lw)

    def say(self, text):
        if text != self.says:
            self.says = text
            self.win.show_status()

    # ------------------------------------------------------------ mouse

    def spot(self, e):
        """(beat, value) under the mouse; Shift = the beat on the grid."""
        b = self.win.beat_at(e.x)
        sb = self.win.snap_beats()
        if e.state & SHIFT and sb:
            b = round(b / sb) * sb
        return [b, self.value_at(e.y)]

    def state_now(self):
        """What the notes' and the layer's lines are now (to see whether they changed since a line was drawn)."""
        return json.dumps([self.win.tones, self.win.loud], sort_keys=True)

    def live_curve(self):
        """The last line / curve drawn, if nothing changed since (then its handles can still change it)."""
        cv = self.curve
        if cv is None or cv["kind"] != self.tool.get() or cv["which"] != self.which.get() or \
                cv["made"] != self.state_now():
            self.curve = None
            return None
        return cv

    def near_handle(self, cv, e):
        win, r = self.win, 7 * self.s
        for which, p in (("mid", curve_mid(cv["a"], cv["b"], cv["c"])), ("a", cv["a"]), ("b", cv["b"])):
            if which == "mid" and cv["kind"] != "curve":
                continue
            if abs(win.x_of(p[0]) - e.x) <= r and abs(self.y_of(p[1]) - e.y) <= r:
                return which
        return None

    def on_press(self, e):
        win = self.win
        win.canvas.focus_set()  # (the window's keys: Space, Ctrl+Z...)
        win.fx.pressed = False
        self.edit = None
        if e.y < self.edge:
            self.edit = {"kind": "size", "y": e.y_root, "h": self.canvas.winfo_height()}
            return
        if e.x < win.kb_w:
            return
        cv = self.live_curve()
        which = self.near_handle(cv, e) if cv else None
        if which:
            self.edit = {"kind": cv["kind"], "handle": which, "curve": (cv["a"], cv["b"], cv["c"]),
                         "base": cv["before"], "drawn": None}
            return
        self.curve = None
        pt = self.spot(e)
        tool = self.tool.get()
        self.edit = {"kind": tool, "start": pt, "last": pt, "drawn": None, "trail": [pt],
                     "base": (copy.deepcopy(win.tones), copy.deepcopy(win.loud))}
        if tool == "pencil":
            self.extend(pt)
        else:
            self.bend(pt, pt, pt)

    def bend(self, a, b, c):
        """The line drawn = the curve a -> b bent towards c (a straight line for Linear)."""
        ed = self.edit
        c = bend_limit(a, b, c, self.lowest())
        ed["curve"] = (a, b, c)
        ed["drawn"] = curve_line(a, b, c, self.pad_beats(), self.lowest())
        self.redraw()

    def pad_beats(self):
        return 0.5 / self.win.sx  # half a pixel either side

    def extend(self, pt):
        """Pencil: the stretch from the last mouse point to pt added to the line (later stretches win)."""
        ed = self.edit
        seg = segment(ed["last"], pt, self.pad_beats())
        ed["drawn"] = seg if ed["drawn"] is None else paint_env(ed["drawn"], seg)
        ed["last"] = pt
        ed["trail"].append(pt)
        self.redraw()

    def on_drag(self, e):
        ed, win = self.edit, self.win
        self.on_motion(e)
        if not ed:
            return
        if ed["kind"] == "size":  # the pane's height (the notes keep some room)
            least = round(50 * self.s)
            most = max(least, win.canvas.winfo_height() + self.canvas.winfo_height() - round(120 * self.s))
            self.app.hz_loud_h = min(most, max(least, ed["h"] - (e.y_root - ed["y"])))
            self.canvas.config(height=self.app.hz_loud_h)
            return
        pt = self.spot(e)
        ed["moved"] = True
        if "handle" in ed:
            a, b, c = ed["curve"]
            if ed["handle"] == "mid":
                lo, hi = min(a[0], b[0]), max(a[0], b[0])
                mx = min(max(pt[0], (3 * lo + hi) / 4), (lo + 3 * hi) / 4)  # keeps the bend between the ends
                c = [2 * mx - (a[0] + b[0]) / 2, 2 * pt[1] - (a[1] + b[1]) / 2]
            else:  # an end moved: the bend keeps its place (a line stays straight)
                other = b if ed["handle"] == "a" else a
                if e.state & CTRL:
                    pt = [pt[0], other[1]]
                if abs(pt[0] - other[0]) < 1e-9:
                    return
                a, b = (pt, b) if ed["handle"] == "a" else (a, pt)
                if ed["kind"] != "curve":
                    c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
            self.bend(a, b, c)
        elif ed["kind"] == "pencil":
            self.extend(pt)
        else:
            a = ed["start"]
            if ed["kind"] == "curve":
                self.bend(a, pt, [(a[0] + pt[0]) / 2, a[1]])  # starts flat, like an ease-in
            else:
                if e.state & CTRL:  # perfectly flat
                    pt = [pt[0], a[1]]
                self.bend(a, pt, [(a[0] + pt[0]) / 2, (a[1] + pt[1]) / 2])

    def on_release(self, e):
        ed, self.edit = self.edit, None
        if not ed or ed["kind"] == "size" or not ed["drawn"]:
            return self.redraw()
        win = self.win
        if not ed.get("moved"):
            return self.redraw()  # (a click: no line)
        before = ed["base"]
        tones, loud = self.applied(ed["drawn"], *before)
        if json.dumps([tones, loud], sort_keys=True) == json.dumps(list(before), sort_keys=True):
            self.curve = None
            return self.redraw()
        win.tones[:] = tones  # (the same list: the selection keeps its numbers)
        win.loud = loud
        win.commit(tr("hz.step_loud"), copy.deepcopy(before[0]), push="handle" not in ed)
        if ed.get("curve") and ed["curve"][0][0] != ed["curve"][1][0]:
            a, b, c = ed["curve"]
            self.curve = {"a": a, "b": b, "c": c, "kind": ed["kind"], "which": self.which.get(), "before": before,
                          "made": self.state_now()}
        self.redraw()

    def drop(self):
        """Ctrl+Z / Esc while the mouse holds a line: thrown away, nothing changes (a handle: back to the press)."""
        if self.edit and self.edit["kind"] == "size":
            return
        self.edit = None
        self.redraw()

    def confirm(self):
        """Done with the last line / curve: its handles go. True if there was one."""
        if self.live_curve() is None:
            return False
        self.curve = None
        self.redraw()
        return True

    def picked(self):
        self.curve = None
        self.redraw()

    def on_motion(self, e):
        win = self.win
        if e.y < self.edge and not self.edit:
            self.canvas.config(cursor="sb_v_double_arrow")
            return self.say("")
        cv = None if self.edit else self.live_curve()
        want = "fleur" if cv and self.near_handle(cv, e) else "crosshair"
        if self.canvas["cursor"] != want and not (self.edit and "handle" in self.edit):
            self.canvas.config(cursor=want)
        if e.x < win.kb_w:
            return self.say("")
        v = self.value_at(e.y)
        self.say(tr("hz.loud_pct", v=round(v / TOP * 100)) if self.which.get() == "layer"
                 else tr("hz.loud_vel", v=round(v)))

    def on_menu(self, e):
        """Right-click: done with the last line's handles; a menu to take the lines drawn away."""
        self.confirm()
        win = self.win
        menu = tk.Menu(self.canvas, tearoff=0)
        if self.which.get() == "layer":
            if win.loud:
                menu.add_command(label=tr("hz.loud_clear_layer"), command=self.clear_layer)
        elif any(n.get("vel") for i, n in enumerate(win.tones) if not win.sel or i in win.sel):
            menu.add_command(label=tr("hz.loud_clear_notes"), command=self.clear_notes)
        if menu.index("end") is not None:
            menu.tk_popup(e.x_root, e.y_root)

    def clear_notes(self):
        """The notes' own lines taken away (the selected ones when some are): the Hz bass's velocity again."""
        win = self.win
        before = copy.deepcopy(win.tones)
        for i, n in enumerate(win.tones):
            if not win.sel or i in win.sel:
                n.pop("vel", None)
        win.commit(tr("hz.step_loud"), before)
        self.redraw()

    def clear_layer(self):
        win = self.win
        win.loud = []
        win.commit(tr("hz.step_loud"), copy.deepcopy(win.tones))
        self.redraw()
