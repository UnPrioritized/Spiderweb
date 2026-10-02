"""
The velocity pane under the piano roll: one bar per note at its start, a cap as long as its gate.
Drag a line (or draw with the pencil) to set velocities. With a shape selected only its notes change,
the others are faded; with nothing selected every note under the drag changes. The Formula tool is a line with a
pattern (pattern.py: Wave, Zigzag, ...) swinging up and down around it (velocity_formula.py: its settings).
"""

import tkinter as tk

import numpy as np

from files.lang import tr
from notes.envelope import env_at, env_values, paint_env, tidy_env, velocity_env
from notes.pattern import loop_points
from roll.roll_shared import (CTRL, DRAFT_COLOR, SHIFT, SELECTED_COLOR, SLOT_COLORS, cached_path, fade,
                              grab_while_panning)

LEVELS = (127, 96, 64, 32, 0)
CURVE_STEPS = 48
FORMULA_POINTS = 4000  # the most points a Formula line has


# Bar colours, lowest first (higher ones are drawn on top): faded slots, normal slots, selected shape, shape being drawn
LAYERS = ([(fade(f), fade(b, 0.55)) for f, b in SLOT_COLORS] + list(SLOT_COLORS) + [SELECTED_COLOR, DRAFT_COLOR])
NORMAL, SELECTED, DRAFT = len(SLOT_COLORS), 2 * len(SLOT_COLORS), 2 * len(SLOT_COLORS) + 1


def segment(a, b, pad):
    """A straight drag from a to b (beat, velocity) as envelope points, reaching pad beats past both ends."""
    if a[0] > b[0]:
        a, b = b, a
    if a[0] == b[0]:
        return [[a[0] - pad, b[1]], [a[0] + pad, b[1]]]
    return [[a[0] - pad, a[1]], [a[0], a[1]], [b[0], b[1]], [b[0] + pad, b[1]]]


def curve_env(a, b, c, pad):
    """A curve from a to b bent towards c (beat, velocity) as envelope points, reaching pad beats past both ends."""
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
                    max(1.0, min(127.0, s * s * a[1] + 2 * s * t * c[1] + t * t * b[1]))])
    return [[a[0] - pad, a[1]]] + pts + [[b[0] + pad, b[1]]]


def formula_env(a, b, pat, pad):
    """The straight line from a to b (beat, velocity) with the pattern pat on it (its sizes in velocity steps, its
    loops between a and b), as envelope points reaching pad beats past both ends. Just the line if pat can't be
    worked out."""
    if a[0] > b[0]:
        a, b = b, a
    if a[0] == b[0]:
        return segment(a, b, pad)
    try:
        u, v = loop_points(pat)
    except ValueError:
        return segment(a, b, pad)
    loops = pat["loops"]
    count = max(1, int(np.ceil(loops - 1e-9)))
    per = max(8, min(len(u) - 1, FORMULA_POINTS // count))
    t = np.linspace(0.0, 1.0, per + 1)
    uu, vv = np.interp(t, np.linspace(0.0, 1.0, len(u)), u), np.interp(t, np.linspace(0.0, 1.0, len(v)), v)
    along = np.concatenate([np.tile(uu[:-1], count) + np.repeat(np.arange(count), per), [count - 1 + uu[-1]]])
    side = np.concatenate([np.tile(vv[:-1], count), [vv[-1]]])
    along = np.maximum.accumulate(np.clip(along / loops, 0.0, None))  # (a loop drawn going back: straight up)
    keep = along <= 1.0
    if not keep.all():  # a part of a loop at the end: up to b exactly
        i = int(np.argmax(~keep))
        f = (1.0 - along[i - 1]) / max(1e-12, along[i] - along[i - 1])
        end = side[i - 1] + (side[i] - side[i - 1]) * f
        along, side = np.append(along[:i], 1.0), np.append(side[:i], end)
    beats = a[0] + (b[0] - a[0]) * along
    vel = np.clip(a[1] + (b[1] - a[1]) * along + side, 1.0, 127.0)
    pts = np.column_stack([beats, vel]).tolist()
    return [[a[0] - pad, pts[0][1]]] + pts + [[b[0] + pad, pts[-1][1]]]


def limit_bend(a, b, c):
    """c with its velocity pulled in just enough that the curve a -> b stays inside 1..127 (same shape, no flat top)."""
    hi = 127 + ((127 - a[1]) * (127 - b[1])) ** 0.5
    lo = 1 - ((a[1] - 1) * (b[1] - 1)) ** 0.5
    return [c[0], min(max(c[1], lo), hi)]


def curve_mid(a, b, c):
    """The point halfway along the curve (where its handle is drawn)."""
    return [(a[0] + 2 * c[0] + b[0]) / 4, (a[1] + 2 * c[1] + b[1]) / 4]


class VelocityPane(tk.Canvas):
    def __init__(self, parent, app, scale):
        super().__init__(parent, bg="#ffffff", highlightthickness=0, cursor="crosshair")
        self.app = app
        self.scale = scale
        self.top = int(7 * scale)  # gap above velocity 127
        self.img = None
        self._pic = None  # what that picture shows (see redraw)
        self.edit = None  # the drag in progress
        self.curve = None  # the last curve drawn, while its handle can still bend it
        app.vel_tool.trace_add("write", lambda *_: self.request_redraw())
        self._pan = None
        self._redraw_pending = False

        self.bind("<Configure>", lambda e: self.request_redraw())
        self.bind("<Map>", lambda e: self.request_redraw())  # shown again: it skipped redraws while hidden
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Motion>", self.on_motion)
        self.bind("<ButtonPress-3>", self.on_right)
        self.bind("<ButtonPress-2>", self.start_pan)
        self.bind("<B2-Motion>", self.pan_to)
        grab_while_panning(self)
        self.bind("<MouseWheel>", self.on_wheel)

    # ------------------------------------------------------------ coordinates

    def bottom(self):
        """Where velocity 0 sits: a gap above the pane's edge, so dragging to the bottom is easy (like 127 at the top)."""
        return self.winfo_height() - 1 - self.top

    def v2y(self, v):
        bot = self.bottom()
        return round(bot - v / 127 * (bot - self.top))

    def y2v(self, y):
        bot = self.bottom()
        return max(1.0, min(127.0, (bot - y) / max(1, bot - self.top) * 127))

    def event_pt(self, e):
        return [self.app.roll.x2t(e.x), self.y2v(e.y)]

    def trail_pt(self, e):
        """The mouse position for the red drag line, kept inside the velocity range."""
        return (e.x, min(max(e.y, self.v2y(127)), self.v2y(1)))

    # ------------------------------------------------------------ editing

    def mark(self, seg, only=None):
        """Show the velocities seg gives the editable rendered notes starting inside it (later marks win) and note
        their shapes."""
        ed, ppq = self.edit, self.app.ppq
        lo, hi = seg[0][0] * ppq, seg[-1][0] * ppq
        first, end = self.app.roll.visible_range(lo, hi)
        notes = self.app.roll.sorted_notes()[first:end]
        owners = only if only is not None else self.app.sels or None
        ok = (notes[:, 0] >= lo) & (notes[:, 0] <= hi)
        if owners is not None:
            ok &= np.isin(notes[:, 5], list(owners))
        rows = np.nonzero(ok)[0]
        if not len(rows):
            return
        if ed["preview"] is None:  # velocity per row of roll.sorted_notes(), -1 = not drawn over
            ed["preview"] = np.full(len(self.app.roll.sorted_notes()), -1, np.int64)
        ed["preview"][rows + first] = np.clip(np.round(env_values(seg, notes[rows, 0] / ppq)), 1, 127)
        shapes = notes[rows, 5]
        _, at = np.unique(shapes, return_index=True)
        for i in shapes[np.sort(at)].tolist():
            ed["owners"].setdefault(i, True)

    def pad(self):
        return 0.5 / self.app.roll.sx  # half a pixel either side of a drag

    def on_press(self, e):
        roll = self.app.roll
        if roll.sx is None or e.x < roll.kb_w:
            return
        roll.cancel_draft()
        roll.focus_set()  # so Space / Ctrl+C etc. keep working
        pt = self.event_pt(e)
        cv = self.live_curve()
        which = self.near_handle(cv, e) if cv else None
        ends = self.snap_spots()
        if which:
            self.edit = {"handle": cv, "which": which, "drawn": None, "preview": None, "owners": {}, "trail": [],
                         "ends": ends}
            return
        self.curve = None
        tool = self.app.vel_tool.get()
        if tool != "pencil":
            pt = [self.snap_time(pt[0], e, ends), pt[1]]
        self.edit = {"start": pt, "last": pt, "drawn": None, "preview": None, "owners": {}, "trail": [self.trail_pt(e)],
                     "kind": tool, "ends": ends}
        if tool == "pencil":
            self.extend(pt)
        else:
            self.draw_curve(pt, pt, pt)

    def on_drag(self, e):
        self.on_motion(e)
        ed = self.edit
        if not ed:
            return
        pt = self.event_pt(e)
        if "handle" in ed:
            cv = ed["handle"]
            a, b, c = cv["a"], cv["b"], cv["c"]
            if ed["which"] == "mid":
                lo, hi = min(a[0], b[0]), max(a[0], b[0])
                mx = min(max(pt[0], (3 * lo + hi) / 4), (lo + 3 * hi) / 4)  # keeps the bend between the two ends
                c = [2 * mx - (a[0] + b[0]) / 2, 2 * pt[1] - (a[1] + b[1]) / 2]
            else:  # an end moved: the bend keeps its place (a line stays straight)
                other = b if ed["which"] == "a" else a
                pt = [self.snap_time(pt[0], e, ed["ends"]), other[1] if e.state & CTRL else pt[1]]
                if abs(pt[0] - other[0]) < 1e-9:
                    return
                a, b = (pt, b) if ed["which"] == "a" else (a, pt)
                if cv["kind"] != "curve":
                    c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
            self.draw_curve(a, b, c, only=cv["done"])
        elif ed["kind"] == "pencil":
            ed["trail"].append(self.trail_pt(e))
            self.extend(pt)
        else:
            a = ed["start"]
            pt = [self.snap_time(pt[0], e, ed["ends"]), pt[1]]
            if ed["kind"] == "curve":
                self.draw_curve(a, pt, [(a[0] + pt[0]) / 2, a[1]])  # starts flat, like an ease-in
            else:
                if e.state & CTRL:  # perfectly flat
                    pt = [pt[0], a[1]]
                self.draw_curve(a, pt, [(a[0] + pt[0]) / 2, (a[1] + pt[1]) / 2])  # a straight line (+ a pattern)

    def snap_spots(self):
        """Times (beats) the drag's ends snap onto: the first and last note of the selected shapes."""
        sels = self.app.sels
        if not sels:
            return []
        notes = self.app.rendered
        starts = notes[np.isin(notes[:, 5], list(sels)), 0]
        return sorted({int(starts.min()), int(starts.max())}) if len(starts) else []

    def snap_time(self, b, e, ends):
        """b snapped: onto the selected shapes' first / last note when it's close on screen, else to the grid.
        Only while Shift is held (free by default)."""
        if not e.state & SHIFT:
            return b
        roll, ppq = self.app.roll, self.app.ppq
        near = [t / ppq for t in ends if abs(roll.t2x(t / ppq) - roll.t2x(b)) <= 10 * self.scale]
        if near:
            return min(near, key=lambda t: abs(t - b))
        sb = self.app.snap_beats()
        return max(0.0, round(b / sb) * sb) if sb else b

    def extend(self, pt):
        """Add the stretch from the last mouse point to pt to the drag (later stretches win)."""
        ed = self.edit
        seg = segment(ed["last"], pt, self.pad())
        ed["drawn"] = seg if ed["drawn"] is None else paint_env(ed["drawn"], seg)
        self.mark(seg)
        ed["last"] = pt
        self.request_redraw()

    def kind(self):
        ed = self.edit
        return ed["handle"]["kind"] if "handle" in ed else ed["kind"]

    def shape_env(self, kind, a, b, c, pad):
        """The envelope of a line / curve / formula line from a to b."""
        if kind == "formula":
            return formula_env(a, b, self.app.vel_pattern, pad)
        return curve_env(a, b, c, pad)

    def draw_curve(self, a, b, c, only=None):
        """Make the drag the curve a -> b bent towards c (a line with a pattern for the Formula tool)."""
        ed = self.edit
        c = limit_bend(a, b, c)
        ed["curve"] = (a, b, c)
        ed["drawn"] = self.shape_env(self.kind(), a, b, c, self.pad())
        ed["preview"], ed["owners"] = None, {}
        self.mark(ed["drawn"], only)
        self.request_redraw()

    def on_release(self, e):
        ed, self.edit = self.edit, None
        if not ed or not ed["drawn"]:
            self.request_redraw()
            return
        if "handle" in ed:
            cv = ed["handle"]
            if self.live_curve() is cv:
                cv["a"], cv["b"], cv["c"] = ed["curve"]
                self.commit(ed["drawn"], cv["done"], cv)  # same undo step as the curve itself
            return
        if not ed["owners"]:
            self.request_redraw()
            return
        self.app.push_undo(name=tr("velocity.draw_velocity"))
        done = self.commit(ed["drawn"], ed["owners"])
        if "curve" in ed and ed["curve"][0][0] != ed["curve"][1][0]:
            a, b, c = ed["curve"]
            # its handles can move its ends (and bend a curve) until something else changes
            self.curve = {"a": a, "b": b, "c": c, "done": done, "kind": ed["kind"]}
            self.request_redraw()

    def commit(self, drawn, owners, cv=None):
        """Write the drawn velocities into each owner shape. Returns {shape: (dict, envelope, before, span)};
        bending a curve again (cv) starts over from the velocities it had before the curve."""
        app = self.app
        bases = cv["done"] if cv else None
        dus = [p[0] for p in drawn]
        done = {}
        for i in owners:
            sh = app.shapes[i]
            base = bases[i][2] if bases else velocity_env(sh)
            bs = [b for b, _ in cached_path(sh)]
            lo, hi = min(bs), max(bs)
            if hi > lo:
                pts = [[(b - lo) / (hi - lo), v] for b, v in drawn]
                env = tidy_env(paint_env(base, pts))
            else:
                env = [[0.0, float(max(1, min(127, round(env_at(drawn, dus, lo)))))]]  # every note is at the start
            sh["vel_env"] = env
            sh.pop("own_vel", None)  # pasted notes: their own velocities are replaced
            us = [u for u, _ in env]
            sh["vel0"], sh["vel1"] = (max(1, min(127, round(env_at(env, us, u)))) for u in (0.0, 1.0))
            done[i] = (sh, env, base, (lo, hi))
        if cv is not None:
            cv["done"] = done
        app.shapes_changed()
        app.sync_panel()
        return done

    def live_curve(self):
        """The last line / curve drawn, if its shapes haven't changed since (then its handles can still change it)."""
        cv = self.curve
        if cv is None or self.app.vel_tool.get() != cv["kind"]:
            return None
        shapes = self.app.shapes
        for i, (sh, env, _, span) in cv["done"].items():
            if i >= len(shapes) or shapes[i] is not sh or sh.get("vel_env") is not env:
                break
            bs = [b for b, _ in cached_path(sh)]
            if (min(bs), max(bs)) != span:
                break
        else:
            return cv
        self.curve = None
        return None

    def formula_changed(self):
        """The Formula tool's settings changed: the last formula line drawn (while it can still be changed) takes
        them, in the same undo step."""
        cv = self.live_curve()
        if cv and cv["kind"] == "formula":
            self.commit(formula_env(cv["a"], cv["b"], self.app.vel_pattern, self.pad()), cv["done"], cv)
            self.request_redraw()

    def confirm(self):
        """Done with the last line / curve: its handles go away. True if there was one."""
        if self.curve is None:
            return False
        self.curve = None
        self.request_redraw()
        return True

    def near_handle(self, cv, e):
        """Which of the curve's handles the mouse is on: "mid" (the bend), "a" / "b" (its ends), or None."""
        roll = self.app.roll
        for which, (b, v) in (("mid", curve_mid(cv["a"], cv["b"], cv["c"])), ("a", cv["a"]), ("b", cv["b"])):
            if which == "mid" and cv["kind"] != "curve":
                continue  # a line stays straight
            if abs(roll.t2x(b) - e.x) <= 7 * self.scale and abs(self.v2y(v) - e.y) <= 7 * self.scale:
                return which
        return None

    def on_motion(self, e):
        roll = self.app.roll
        text = None
        if roll.sx is not None and e.x >= roll.kb_w:
            text = roll.time_text(e.x) + tr("velocity.velocity", y2v=round(self.y2v(e.y)))
        self.app.show_position(text)
        cv = None if self.edit else self.live_curve()
        want = "fleur" if cv and self.near_handle(cv, e) else "crosshair"
        if self["cursor"] != want and not (self.edit and "handle" in self.edit):
            self.config(cursor=want)

    def on_right(self, e):
        self.edit = None
        self.curve = None
        self.app.select(None)

    def start_pan(self, e):
        self._pan = (e.x, self.app.roll.view_t)

    def pan_to(self, e):
        roll = self.app.roll
        if self._pan and roll.sx is not None:
            roll.view_t = self._pan[1] - (e.x - self._pan[0]) / roll.sx
            roll.clamp_view()
            roll.request_redraw()

    def on_wheel(self, e):
        """Wheel scrolls sideways, Ctrl+wheel zooms time (same as the piano roll)."""
        roll = self.app.roll
        if roll.sx is None:
            return
        up = e.delta > 0
        if e.state & CTRL:
            b = roll.x2t(e.x)
            roll.sx = min(100000.0, max(0.05, roll.sx * (1.25 if up else 0.8)))
            roll.view_t = b - (e.x - roll.kb_w) / roll.sx
        else:
            roll.view_t += (-1 if up else 1) * 120 / roll.sx
        roll.clamp_view()
        roll.request_redraw()

    # ------------------------------------------------------------ drawing

    def request_redraw(self):
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self.redraw)

    def redraw(self):
        self._redraw_pending = False
        if not self.winfo_ismapped():
            return
        self.delete("all")
        roll = self.app.roll
        w, h = self.winfo_width(), self.winfo_height()
        kb = int(roll.kb_w)
        if roll.sx is None or w - kb < 2 or h < 2 * self.top + 4:
            return
        # the picture shows exactly this: when nothing here changed (the piano roll moved up or down, a shape is
        # dragged and its notes catch up later), it's shown again as it is
        pic = None if self.edit or roll.draft_notes() is not None else (
            self.app.rendered, (frozenset(self.app.sels), roll.sx, roll.view_t, roll.kb_w, w, h, self.top,
                                tuple(roll.grid_cols(w))))
        if pic and self.img is not None and self._pic and self._pic[0] is pic[0] and self._pic[1] == pic[1]:
            self.create_image(kb, 0, image=self.img, anchor="nw")
        else:
            self.paint(w, h, kb)
            self._pic = pic
        self.create_rectangle(0, 0, kb, h, fill="#f0f0f0", outline="")
        self.create_line(kb - 1, 0, kb - 1, h, fill="#808080")
        for v in LEVELS:
            y = min(max(self.v2y(v), 6 * self.scale), h - 6 * self.scale)
            self.create_text(kb - 5, y, text=str(v), anchor="e", fill="#333", font=("Segoe UI", 7))
        ed = self.edit
        lw = max(1, round(self.scale))
        cv = ed.get("curve") if ed else None
        if cv:
            self.draw_curve_line(*cv, lw, kind=self.kind())
        elif ed and len(ed["trail"]) >= 2:
            self.create_line(*[c for p in ed["trail"] for c in p], fill="#d00000", width=lw)
        cv = None if ed else self.live_curve()
        if cv:
            self.draw_curve_line(cv["a"], cv["b"], cv["c"], lw, kind=cv["kind"])
        self.draw_playhead()

    def draw_playhead(self):
        self.delete("playhead")
        roll = self.app.roll
        if roll.sx is None or not self.winfo_ismapped():
            return
        x = round(roll.t2x(self.app.playhead))
        if roll.kb_w <= x <= self.winfo_width():
            self.create_line(x, 0, x, self.winfo_height(), fill="#0a50e0", width=max(1, round(self.scale)),
                             tags="playhead")

    def draw_curve_line(self, a, b, c, lw, kind="curve"):
        """The red line / curve with square handles on its ends and a curve's round handle in the middle."""
        roll = self.app.roll
        bend = kind == "curve"
        pts = self.shape_env(kind, a, b, c, 0)[1:-1]
        if len(pts) < 2:
            return
        self.create_line(*[q for b_, v in pts for q in (roll.t2x(b_), self.v2y(v))], fill="#d00000", width=lw)
        if a[0] != b[0]:
            r = 4 * self.scale
            if bend:
                m = curve_mid(a, b, c)
                x, y = roll.t2x(m[0]), self.v2y(m[1])
                self.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#d00000", width=lw)
            for b_, v in (a, b):
                x, y = roll.t2x(b_), self.v2y(v)
                self.create_rectangle(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#d00000", width=lw)

    def bars(self, w, kb):
        """Visible notes -> {(x, layer): top y} for the bars and {(y, x0, x1, layer)} for the gate caps."""
        app, roll, ppq = self.app, self.app.roll, self.app.ppq
        iw = w - kb
        ax, bx = roll.sx / ppq, roll.kb_w - roll.view_t * roll.sx - kb  # image x = tick * ax + bx
        sels = app.sels
        preview = self.edit["preview"] if self.edit else None
        ys = np.array([self.v2y(v) for v in range(128)], np.int64)

        t_lo, t_hi = roll.x2t(kb) * ppq, roll.x2t(w) * ppq
        first, end = roll.visible_range(t_lo, t_hi)
        notes = roll.sorted_notes()[first:end]
        vel = notes[:, 3].copy()
        if preview is not None:  # velocities being drawn right now
            drawn = preview[first:end]
            vel = np.where(drawn >= 0, drawn, vel)
        slot = notes[:, 4] % len(SLOT_COLORS)
        if sels:
            layer = np.where(np.isin(notes[:, 5], list(sels)), NORMAL + slot, slot)  # (own colours, others faded)
        else:
            layer = NORMAL + slot
        s, e = notes[:, 0], notes[:, 1]
        d = roll.draft_notes()
        if d is not None:
            s, e = np.concatenate([s, d[:, 0]]), np.concatenate([e, d[:, 1]])
            vel = np.concatenate([vel, d[:, 3]])
            layer = np.concatenate([layer, np.full(len(d), DRAFT)])
        # (the same sums as one note at a time, so the same pixels)
        x0, x1 = np.round(s * ax + bx).astype(np.int64), np.round(e * ax + bx).astype(np.int64)
        on = ~((x1 < 0) | (x0 >= iw))
        x0, x1, y, layer = x0[on], x1[on], ys[vel[on]], layer[on]
        # each bar (column and layer): its highest note
        bar = x0 >= 0
        top = np.full(iw * len(LAYERS), 1 << 30, np.int64)
        np.minimum.at(top, x0[bar] * len(LAYERS) + layer[bar], y[bar])
        found = np.nonzero(top < 1 << 30)[0]
        tops = dict(zip(zip((found // len(LAYERS)).tolist(), (found % len(LAYERS)).tolist()), top[found].tolist()))
        long = x1 - x0 >= 2
        caps = np.unique(np.column_stack([y[long], np.maximum(x0[long], 0), np.minimum(x1[long], iw - 1),
                                          layer[long]]), axis=0)
        return tops, set(map(tuple, caps.tolist()))

    def paint(self, w, h, kb):
        """Grid and bars as one picture, built row by row from the top (bars only ever start, going down)."""
        iw = w - kb
        rgb = {}

        def px(color):
            if color not in rgb:
                rgb[color] = bytes.fromhex(color[1:])
            return rgb[color]

        fills = [px(f) for f, _ in LAYERS]
        borders = [px(b) for _, b in LAYERS]
        row_color = {self.v2y(v): "#d3dff0" for v in (96, 64, 32)}
        row_color[self.v2y(127)] = row_color[self.v2y(0)] = "#9fb2cf"
        row_color[h - 1] = "#808080"
        cols = [(int(x) - kb, px(c)) for x, c in self.app.roll.grid_cols(w) if 0 <= int(x) - kb < iw]

        rows = {}  # row colour -> the current row: that colour, grid lines, and every bar started so far
        for color in {"#ffffff", *row_color.values()}:
            line = bytearray(px(color) * iw)
            if color != "#808080":
                for x, c in cols:
                    line[x * 3:x * 3 + 3] = c
            rows[color] = line

        tops, caps = self.bars(w, kb)
        starts, cap_rows = {}, {}
        for (x, layer), y in tops.items():
            starts.setdefault(max(y, 0), []).append((x, layer))
        for y, x0, x1, layer in sorted(caps, key=lambda c: c[3]):
            cap_rows.setdefault(y, []).append((x0, x1, layer))

        active = [-1] * iw  # the top layer of bar in each column so far
        out = []
        for y in range(h):
            new = starts.get(y, ())
            for x, layer in new:
                if layer > active[x]:
                    active[x] = layer
                    for line in rows.values():
                        line[x * 3:x * 3 + 3] = fills[layer]
            row = bytearray(rows[row_color.get(y, "#ffffff")])
            for x, layer in new:
                if active[x] == layer:
                    row[x * 3:x * 3 + 3] = borders[layer]  # dark top of the bar
            for x0, x1, layer in cap_rows.get(y, ()):
                b = borders[layer]
                if max(active[x0:x1 + 1]) <= layer:
                    row[x0 * 3:x1 * 3 + 3] = b * (x1 - x0 + 1)
                else:
                    for x in range(x0, x1 + 1):
                        if active[x] <= layer:
                            row[x * 3:x * 3 + 3] = b
            out.append(row)

        data = b"P6 %d %d 255\n" % (iw, h) + b"".join(out)
        if self.img is None or (self.img.width(), self.img.height()) != (iw, h):
            self.img = tk.PhotoImage(master=self, width=iw, height=h)
        self.img.configure(data=data, format="ppm")
        self.create_image(kb, 0, image=self.img, anchor="nw")
