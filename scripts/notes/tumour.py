"""Tumours: bumps along a line.

A line, polyline, freehand stroke, curve or arc can carry sh["tumour"] = {"on", "shape", "size", "length", "dist",
"side", "wrap", "start", "end", "seed", "mirror", "k"}. Its points stay as drawn (still draggable); the path it makes
notes from gets a bump every `dist` along it, each `length` long and `size` keys high, on the side `side` picks.
`ease` > 0: over that far from each end of the range the bumps grow from nothing, so the line leads into them
smoothly instead of starting with a sudden (e.g. straight-up) side.
`rot` (degrees): each bump tilts, its feet staying on the line: every point's sideways part turns by that much,
positive leaning forward (the way the line runs), so 90 lays it flat along the line and 180 puts it on the other side.
`slant` (-1..1, square only): the square's top is narrowed by that much of its length (1 = a point, like a
triangle; minus = wider than its base).
`graphs` (optional): {setting: [[u, f], ...]} for the settings in GRAPH_KEYS: along the whole line (u = 0 its start,
1 its end, by length on screen) that setting is multiplied by f (1 = 100 %), straight between the points. Size
changes point by point (like easing); length, slant and rotation per bump (at its start, rotation at its middle; a
bump the length graph makes 0 long is a spike there, straight out and back);
distance sets how far apart bumps are wherever they are.
`fit`: the distance is stretched a little so a whole number of steps fits the range exactly; round a closed loop
(e.g. a full circle) the bumps then meet up where it starts.
Worked out as it looks on screen: k = beats per key on screen when the settings were last changed (like arcs, see
arc.py), so sizes are in keys, lengths / distances in beats along the line. length 0 = spikes: every bump is one
point pushed sideways and the line zigzags straight from spike to spike."""

import bisect
import json
import math
import random

import numpy as np

from notes.arc import arc_points
from notes.paths import spans

SHAPES = ("triangle", "square", "circle", "parabola")
SIDES = ("alt", "left", "right", "random")
WRAPS = ("simple", "wrap")
LINE_KINDS = ("line", "poly", "free", "curve", "arc")  # the shapes that can have tumours
MAX_TUMOURS = 20000
GRAPH_KEYS = ("size", "length", "dist", "rot", "slant")  # the settings that can follow a graph
GRAPH_LIMIT = 10.0  # graph values from -1000 % to 1000 %
TUMOUR_DEFAULTS = {"on": True, "shape": "triangle", "size": 3.0, "length": 0.125, "dist": 0.125, "side": "alt",
                   "wrap": "simple", "start": 0.0, "end": 1.0, "ease": 0.0, "rot": 0.0, "slant": 0.0, "fit": False, "seed": 1, "mirror": False,
                   "k": 0.25}


def clean_tumour(tm):
    """Tumour settings from a file (None if there are none)."""
    if not isinstance(tm, dict):
        return None
    out = dict(TUMOUR_DEFAULTS)
    for key, choices in (("shape", SHAPES), ("side", SIDES), ("wrap", WRAPS)):
        if tm.get(key) in choices:
            out[key] = tm[key]
    for key, lo, hi in (("size", -1000, 1000), ("length", 0, 1e6), ("dist", 1e-9, 1e6), ("start", 0, 1),
                        ("end", 0, 1), ("ease", 0, 1e6), ("k", 1e-9, 1e9), ("rot", -180, 180), ("slant", -1, 1)):
        try:
            v = float(tm.get(key, out[key]))
            if math.isfinite(v):
                out[key] = min(hi, max(lo, v))
        except (TypeError, ValueError):
            pass
    try:
        out["seed"] = int(tm.get("seed", 1))
    except (TypeError, ValueError):
        pass
    out["on"] = tm.get("on", True) is not False
    out["mirror"] = tm.get("mirror") is True
    out["fit"] = tm.get("fit") is True
    graphs = {key: g for key in GRAPH_KEYS if (g := clean_graph((tm.get("graphs") or {}).get(key)
                                                               if isinstance(tm.get("graphs"), dict) else None))}
    if graphs:
        out["graphs"] = graphs
    return out


def clean_graph(g):
    """A graph from a file: [[u, f], ...] from u = 0 to u = 1, u never going back (None if it's unusable or flat
    at 100 %)."""
    try:
        pts = [[min(1.0, max(0.0, float(u))), min(GRAPH_LIMIT, max(-GRAPH_LIMIT, float(f)))] for u, f in g]
    except (TypeError, ValueError):
        return None
    if len(pts) < 2 or not all(math.isfinite(a) for p in pts for a in p):
        return None
    pts[0][0], pts[-1][0] = 0.0, 1.0
    for a, b in zip(pts, pts[1:]):
        b[0] = max(b[0], a[0])
    return None if all(abs(f - 1) < 1e-12 for _, f in pts) else pts


def graph_fn(tm, key, total):
    """The setting's graph as a function of distances along the line (array or number in, multipliers out),
    None when it has none."""
    g = (tm.get("graphs") or {}).get(key)
    if not g:
        return None
    u, f = np.array([p[0] for p in g]) * total, np.array([p[1] for p in g], float)
    return lambda d: np.interp(d, u, f)


def graph_starts(dg, lo, hi, dist, fit, even):
    """Where the bumps start when the distance follows the graph dg: counting bumps along the range (1 / distance
    per unit of length), bump i starts where the count reaches i. fit: the count is stretched so a whole number of
    steps fits the range (even: an even number). Also returns how much that stretched the distance (1 = not)."""
    if hi - lo < 1e-9:
        return [lo], 1.0
    d = np.linspace(lo, hi, 4097)
    rate = 1 / np.maximum(dist * dg(d), max((hi - lo) / MAX_TUMOURS, 1e-9))
    count = np.concatenate([[0.0], np.cumsum((rate[1:] + rate[:-1]) / 2 * np.diff(d))])
    total = count[-1]
    stretch = 1.0
    if fit:
        n = max(2, 2 * round(total / 2)) if even else max(1, round(total))
        marks = np.linspace(0.0, total, n + 1)
        stretch = total / n
    else:
        # (a bump within a thousandth of a step of the end still counts: after a split, the half whose Fit was
        # turned off must still get the one Fit put right on the end)
        marks = np.arange(min(math.floor(total + 1e-3), MAX_TUMOURS - 1) + 1, dtype=float)
    return np.interp(marks, count, d).tolist(), stretch


def template(shape, length, size, slant=0.0):
    """The bump as (x along the line, y sideways) points from (0, 0) to (length, 0)."""
    if shape == "triangle":
        return [(0.0, 0.0), (length / 2, size), (length, 0.0)]
    if shape == "square":
        m = length / 2 * slant
        return [(0.0, 0.0), (m, size), (length - m, size), (length, 0.0)]
    if shape == "parabola":
        return [(length * t / 16, size * 4 * (t / 16) * (1 - t / 16)) for t in range(17)]
    if abs(size) < 1e-12:
        return [(0.0, 0.0), (length, 0.0)]
    # circle: a round bump through its top (a point every 10 degrees is plenty for a bump)
    return arc_points([(0.0, 0.0), (length / 2, size), (length, 0.0)], step=math.radians(10))


def cut(pts, x_end):
    """The template's points up to x_end along (a bump cut short where the next one starts / the range ends)."""
    if x_end >= pts[-1][0] - 1e-12 and all(x <= x_end + 1e-12 for x, _ in pts):
        return pts
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        if b[0] <= x_end + 1e-12:
            out.append(b)
            continue
        if a[0] < x_end < b[0]:
            u = (x_end - a[0]) / (b[0] - a[0])
            out.append((x_end, a[1] + (b[1] - a[1]) * u))
        break
    return out


class Walk:
    """A path (on-screen units) measured along its length: the point and direction at any distance."""

    def __init__(self, pts):
        self.pts = pts
        self.cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.total = self.cum[-1]
        self.closed = len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9

    def seg(self, d):
        return min(max(bisect.bisect_right(self.cum, d) - 1, 0), len(self.pts) - 2)

    def at(self, d):
        i = self.seg(d)
        a, b = self.pts[i], self.pts[i + 1]
        n = self.cum[i + 1] - self.cum[i]
        u = 0.0 if n == 0 else min(max((d - self.cum[i]) / n, 0.0), 1.0)
        return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u

    def direction(self, d):
        """Unit direction at distance d (at a corner: half way between its two sides)."""
        i = self.seg(d)
        v, before = self._dir(i), None
        if abs(d - self.cum[i]) < 1e-12:
            before = i - 1 if i > 0 else len(self.pts) - 2 if self.closed else None
        if self.closed and d >= self.total - 1e-12:
            # a closed loop's start / end is a corner too: between its last and first sides
            v, before = self._dir(0), len(self.pts) - 2
        if before is not None:
            w = self._dir(before)
            s = (v[0] + w[0], v[1] + w[1])
            n = math.hypot(*s)
            if n > 1e-9:
                return s[0] / n, s[1] / n
        return v

    def _dir(self, i):
        a, b = self.pts[i], self.pts[i + 1]
        n = math.dist(a, b)
        return ((b[0] - a[0]) / n, (b[1] - a[1]) / n) if n else (1.0, 0.0)

    def _arrays(self):
        if not hasattr(self, "_p"):
            self._p, self._cum = np.array(self.pts, float), np.array(self.cum)
            self._dirs = np.array([self._dir(i) for i in range(len(self.pts) - 1)], float)
        return self._p, self._cum

    def _segs(self, d):
        return np.clip(np.searchsorted(self._arrays()[1], d, "right") - 1, 0, len(self.pts) - 2)

    def at_many(self, d):
        """at for a whole array of distances: (x array, y array)."""
        p, cum = self._arrays()
        i = self._segs(d)
        n = cum[i + 1] - cum[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = np.where(n == 0, 0.0, np.clip((d - cum[i]) / n, 0.0, 1.0))
        a, b = p[i], p[i + 1]
        return a[:, 0] + (b[:, 0] - a[:, 0]) * u, a[:, 1] + (b[:, 1] - a[:, 1]) * u

    def directions(self, d):
        """direction for a whole array of distances: (x array, y array)."""
        cum = self._arrays()[1]
        i = self._segs(d)
        v = self._dirs[i]
        before = i - 1
        corner = (np.abs(d - cum[i]) < 1e-12) & ((i > 0) | self.closed)
        if self.closed:
            before[i == 0] = len(self.pts) - 2
            end = d >= self.total - 1e-12
            v[end] = self._dirs[0]
            before[end] = len(self.pts) - 2
            corner |= end
        j = np.nonzero(corner)[0]
        if len(j):  # half way between the two sides (as in direction)
            s = v[j] + self._dirs[before[j]]
            n = np.hypot(s[:, 0], s[:, 1])
            ok = n > 1e-9
            v[j[ok]] = s[ok] / n[ok, None]
        return v[:, 0], v[:, 1]


def bump_starts(w, tm):
    """Where the bumps start along the Walk w (on-screen units): (range start, range end, whether it's a closed loop
    with bumps all the way round, the starts, how much Fit stretched the distance (1 = not at all))."""
    k = tm["k"]
    lo, hi = min(tm["start"], tm["end"]) * w.total, max(tm["start"], tm["end"]) * w.total
    dist = max(tm["dist"] / k, (hi - lo) / MAX_TUMOURS, 1e-9)
    # a closed loop (e.g. a full circle) with bumps all the way round: its end is its start again
    loop = w.closed and lo < 1e-9 and hi > w.total - 1e-9
    dg = graph_fn(tm, "dist", w.total)
    stretch = 1.0
    if dg is not None:
        starts, stretch = graph_starts(dg, lo, hi, dist, tm.get("fit"), loop and tm["side"] == "alt")
    elif tm.get("fit") and hi - lo > 1e-9:
        # a whole number of steps fits the range, so the last one lands right on its end; round a loop with
        # alternating sides, an even number, so the sides keep alternating where it meets up
        n = max(1, round((hi - lo) / dist))
        if loop and tm["side"] == "alt":
            n = max(2, 2 * round((hi - lo) / dist / 2))
        stretch = (hi - lo) / n / dist
        starts = [lo + (hi - lo) * i / n for i in range(n + 1)]
    else:
        starts = []
        s = lo
        while s <= hi + 1e-9 and len(starts) < MAX_TUMOURS:
            starts.append(min(s, hi))
            s += dist
    return lo, hi, loop, starts, stretch


def _walk(path, k):
    pts = []
    for b, p in path:
        q = (b / k, p)
        if not pts or q != pts[-1]:
            pts.append(q)
    return Walk(pts) if len(pts) > 1 else None


def sub_graph(g, a, b):
    """The part of a graph from u = a to u = b, stretched to 0..1."""
    us, fs = [p[0] for p in g], [p[1] for p in g]
    inner = [[(u - a) / (b - a), f] for u, f in g if a + 1e-12 < u < b - 1e-12]
    return [[0.0, float(np.interp(a, us, fs))]] + inner + [[1.0, float(np.interp(b, us, fs))]]


def split_tumour(tm, left, right):
    """Tumour settings for the two halves of a line cut in two (left, right: their (beat, pitch) points, both with
    the cut point), so the bumps stay where they were: each half gets its own part of the range and graphs, Fit is
    turned off (keeping the distance it had worked out) and the right half starts at the first bump after the cut
    (a bump across the cut is cut off there). Random sides are picked again; Lead in works at each half's ends."""
    if not tm:
        return tm, tm
    copy = json.loads(json.dumps(tm))
    w, wl = _walk(list(left) + list(right)[1:], tm["k"]), _walk(left, tm["k"])
    if w is None or wl is None or w.total - wl.total < 1e-9 * w.total:
        return copy, json.loads(json.dumps(tm))
    lo, hi, _, starts, stretch = bump_starts(w, tm)
    cut = wl.total
    base = dict(copy, fit=False, dist=tm["dist"] * stretch, start=0.0, end=1.0)

    def half(a, b, r0, r1):
        out = json.loads(json.dumps(base))
        length = b - a
        out["start"] = min(1.0, max(0.0, (r0 - a) / length))
        out["end"] = min(1.0, max(0.0, (r1 - a) / length))
        if r1 - r0 < 1e-9:  # (no bumps left on this half)
            out["on"] = False
        graphs = {key: g2 for key, g in (tm.get("graphs") or {}).items()
                  if (g2 := clean_graph(sub_graph(g, a / w.total, b / w.total)))}
        out.pop("graphs", None)
        if graphs:
            out["graphs"] = graphs
        return out
    first = half(0.0, cut, lo, min(hi, cut))
    nxt = next(((i, s) for i, s in enumerate(starts) if s >= cut - 1e-9), None)
    second = half(cut, w.total, nxt[1] if nxt else hi, hi if nxt else hi)
    if nxt and tm["side"] == "alt" and nxt[0] % 2:  # (alternating: it has to start on the other side)
        second["mirror"] = not second.get("mirror", False)
    return first, second


def tumour_path(path, tm):
    """The path (beat, pitch points) with the tumours on it."""
    k = tm["k"]
    pts = []
    for b, p in path:
        q = (b / k, p)
        if not pts or q != pts[-1]:
            pts.append(q)
    size = tm["size"]
    if len(pts) < 2 or abs(size) < 1e-12:
        return path
    w = Walk(pts)
    if w.total < 1e-12:
        return path
    length = max(0.0, tm["length"] / k)
    lo, hi, loop, starts, _ = bump_starts(w, tm)
    zg, lg, rg, sg = (graph_fn(tm, key, w.total) for key in ("size", "length", "rot", "slant"))
    rot = math.radians(tm.get("rot", 0.0))
    lean = abs(rot) > 1e-12
    sin, cos = math.sin(rot), math.cos(rot)
    rnd = random.Random(tm["seed"])
    flip = -1 if tm["mirror"] else 1
    sides = []
    for i in range(len(starts)):
        side = {"left": 1, "right": -1, "alt": 1 if i % 2 == 0 else -1}.get(tm["side"])
        sides.append(flip * (side if side is not None else rnd.choice((1, -1))))
    fit_loop = loop and tm.get("fit")
    if fit_loop:
        sides[-1] = sides[0]  # the last one is the first one again

    def base(d0, d1):
        """The path's own points strictly between distances d0 and d1."""
        return w.pts[bisect.bisect_right(w.cum, d0 + 1e-9):bisect.bisect_left(w.cum, d1 - 1e-9)]

    ease = tm.get("ease", 0.0) / k

    def grow(d):
        """How much of the full size a bump has at distance d (less near the range's ends when easing)."""
        if ease < 1e-12:
            return 1.0
        return max(0.0, min(1.0, (d - lo) / ease, (hi - d) / ease))

    def eased(bump, s):
        """The bump's points with the easing applied (extra points where it's easing, so the change is smooth)."""
        if ease < 1e-12 or not (s < lo + ease or s + bump[-1][0] > hi - ease):
            return bump
        step = ease / 16
        pts = [bump[0]]
        for (xa, ya), (xb, yb) in zip(bump, bump[1:]):
            marks = [(lo + ease - s - xa) / (xb - xa), (hi - ease - s - xa) / (xb - xa)] if abs(xb - xa) > 1e-12 else []
            n = min(64, int(abs(xb - xa) / step))
            us = sorted({j / (n + 1) for j in range(1, n + 1)} | {u for u in marks if 1e-9 < u < 1 - 1e-9})
            pts += [(xa + (xb - xa) * u, ya + (yb - ya) * u) for u in us] + [(xb, yb)]
        # (a round bump folding back past the range's end is flat there: kept inside it, not along the line past it)
        return [(min(max(x, lo - s), hi - s), y * grow(s + x)) for x, y in pts]

    def grows(d):
        """grow for a whole array of distances."""
        if ease < 1e-12:
            return 1.0
        return np.maximum(0.0, np.minimum(np.minimum(1.0, (d - lo) / ease), (hi - d) / ease))

    def normals(d):
        ux, uy = w.directions(d)
        return -uy, ux  # to the left of the way the line runs

    # Points that need working out one by one (few per bump) go in as they are; the many points along the bumps
    # are collected as blocks and worked out on whole arrays at the end.
    out = Out()
    if starts[0] > 1e-9:
        out.add([w.pts[0]] + base(0.0, starts[0]))
    if length < 1e-12:  # spikes: one point pushed sideways each, straight from one to the next
        if not out.n and not fit_loop:  # (round a loop it goes from spike to spike only, first = last)
            out.add([w.pts[0]])
        s, side = np.array(starts), np.array(sides, float)
        (x, y), (nx, ny) = w.at_many(s), normals(s)
        h = size * grows(s)
        if zg is not None:
            h = h * zg(s)
        if lean:  # tilted: part of the push goes along the line
            ux, uy = ny, -nx
            if rg is not None:
                r = rot * rg(s)
                sin, cos = np.sin(r), np.cos(r)
            out.block(np.column_stack([x + (nx * cos * side + ux * sin) * h, y + (ny * cos * side + uy * sin) * h]))
        else:
            h = h * side
            out.block(np.column_stack([x + nx * h, y + ny * h]))
        if not fit_loop:
            out.add(base(starts[-1], w.total) + [w.pts[-1]])
    else:
        slant = tm.get("slant", 0.0)
        shape = template(tm["shape"], length, size, slant) if lg is None and sg is None else None
        pts, per_bump, sizes = [], [], []
        for i, (s, side) in enumerate(zip(starts, sides)):
            room = min(hi, starts[i + 1] if i + 1 < len(starts) else math.inf)
            li = length if lg is None else max(0.0, length * float(lg(s)))
            e = min(s + li, room)
            if li < 1e-9 < room - s:  # the length graph at 0 here: a spike, as a bump this thin would be (user)
                a = w.at(s)
                ux, uy = w.direction(s)
                h = size * grow(s) * (1.0 if zg is None else float(zg(s)))
                r = rot if rg is None else rot * float(rg(s))
                along, out_ = math.sin(r) * h, math.cos(r) * h * side
                out.add([a, (a[0] - uy * out_ + ux * along, a[1] + ux * out_ + uy * along), a])
                out.add(base(s, starts[i + 1] if i + 1 < len(starts) else w.total))
                continue
            if e - s < 1e-9:  # no room left (it would start right on the end of the range)
                out.add([w.at(s)])
                out.add(base(e, starts[i + 1] if i + 1 < len(starts) else w.total))
                continue
            # (cut only where the next bump or the range's end is in the way: a round bump taller than half its
            # length bulges out past its own ends, and that part is kept when there's room)
            bump = cut(shape or template(tm["shape"], li, size, slant if sg is None else
                                         min(1.0, max(-1.0, slant * float(sg(s))))), room - s)
            r = rot if rg is None else rot * float(rg(s + li / 2))
            sn, cs = (sin, cos) if rg is None else (math.sin(r), math.cos(r))
            if zg is not None and tm["wrap"] == "simple":  # (points for the size to change along)
                bump = subdivide(bump, sorted({(e - s) * j / 16 for j in range(17)}))
            if tm["wrap"] == "simple":  # on the straight line from where it starts to where it ends
                a, b = w.at(s), w.at(e)
                ux, uy = b[0] - a[0], b[1] - a[1]
                n = math.hypot(ux, uy)
                ux, uy = (ux / n, uy / n) if n > 1e-12 else w.direction(s)
                stretch = n / (e - s) if e - s > 1e-12 else 1.0
                bump = eased(bump, s)
                pts += bump
                per_bump.append((a[0], a[1], ux, uy, stretch, side, s, sn, cs))
                sizes.append(len(bump))
                out.block(len(bump))
            else:  # bending with the line: every point sideways from where it is along the line, with extra
                # points where the line bends and along long sides, so the bump follows the line's shape
                x0, x1 = s + min(x for x, _ in bump), s + max(x for x, _ in bump)
                xs = sorted({c - s for c in w.cum[bisect.bisect_right(w.cum, x0):bisect.bisect_left(w.cum, x1)]} |
                            # (a circle's outline already has a point every 10 degrees)
                            ({(e - s) * j / 16 for j in range(17)} if tm["shape"] != "circle" else set()) |
                            # (the points easing adds, so the bump grows smoothly)
                            ({x for x, _ in eb} if (eb := eased(bump, s)) is not bump else set()))
                bump = subdivide(bump, xs)
                pts += bump
                per_bump.append((s, side, sn, cs))
                sizes.append(len(bump))
                out.block(len(bump))
            nxt = starts[i + 1] if i + 1 < len(starts) else w.total
            if e >= hi - 1e-12 and nxt > e + 1e-12:  # cut off by the range's end: down onto the line there
                out.add([w.at(e)])
            out.add(base(e, nxt))
        out.add([w.pts[-1]])
        if per_bump:
            par = np.repeat(np.array(per_bump, float), sizes, axis=0)
            if tm["wrap"] == "simple":
                p = np.array(pts, float)
                x, y = p[:, 0] * par[:, 4], p[:, 1]
                if zg is not None:
                    y = y * zg(par[:, 6] + p[:, 0])
                if lean:
                    x, y = x + y * par[:, 7], y * par[:, 8]
                ax, ay, ux, uy, side = par[:, 0], par[:, 1], par[:, 2], par[:, 3], par[:, 5]
                out.fill(np.column_stack([ax + ux * x - uy * y * side, ay + uy * x + ux * y * side]))
            else:
                p = np.array(pts, float)
                s, side = par[:, 0], par[:, 1]
                x = p[:, 0]
                y = p[:, 1] * grows(s + x)
                if zg is not None:
                    y = y * zg(s + x)
                if lean:
                    x, y = x + y * par[:, 2], y * par[:, 3]
                d = s + x
                if w.closed:  # a round bump bulging past a loop's start / end: round the loop
                    d = np.mod(d, w.total)
                dc = np.minimum(np.maximum(d, 0.0), w.total)
                (px, py), (nx, ny) = w.at_many(dc), normals(dc)
                over = d - dc  # past the line's start / end: straight on the way it runs there
                px, py = px + ny * over, py - nx * over
                out.fill(np.column_stack([px + nx * y * side, py + ny * y * side]))
    res = out.result()
    keep = np.ones(len(res), bool)
    keep[1:] = (res[1:] != res[:-1]).any(axis=1)
    res = res[keep]
    return list(zip((res[:, 0] * k).tolist(), res[:, 1].tolist()))


class Out:
    """The tumour path's points in order: points added as they are, and blocks whose points come later as one
    array (fill)."""

    def __init__(self):
        self.lit, self.lit_at, self.blocks, self.arrays, self.n = [], [], [], [], 0

    def add(self, pts):
        self.lit += pts
        self.lit_at += range(self.n, self.n + len(pts))
        self.n += len(pts)

    def block(self, pts):
        """A block of points: an array of them, or how many (their array comes with fill)."""
        n = pts if isinstance(pts, int) else len(pts)
        if not isinstance(pts, int):
            self.arrays.append(pts)
        self.blocks.append((self.n, n))
        self.n += n

    def fill(self, pts):
        self.arrays.append(pts)

    def result(self):
        res = np.empty((self.n, 2))
        if self.lit:
            res[self.lit_at] = self.lit
        if self.blocks:
            at, n = np.array(self.blocks).T
            res[spans(at, at + n - 1)] = np.concatenate(self.arrays)
        return res


def subdivide(bump, xs):
    """The bump's points in order, plus a point wherever one of its sides passes one of xs (sorted), so a bump
    bending with the line follows it. In order, not one height per x, so a round bump that folds back keeps its
    whole outline."""
    out = [bump[0]]
    for (xa, ya), (xb, yb) in zip(bump, bump[1:]):
        if abs(xb - xa) > 1e-12:
            mid = xs[bisect.bisect_right(xs, min(xa, xb) + 1e-12):bisect.bisect_left(xs, max(xa, xb) - 1e-12)]
            out += [(x, ya + (yb - ya) * (x - xa) / (xb - xa)) for x in (mid if xb > xa else mid[::-1])]
        out.append((xb, yb))
    return out
