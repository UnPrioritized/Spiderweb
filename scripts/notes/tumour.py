"""Tumours: bumps along a line.

A line, polyline, freehand stroke, curve or arc can carry sh["tumour"] = {"on", "shape", "size", "length", "dist",
"side", "wrap", "start", "end", "seed", "mirror", "k"}. Its points stay as drawn (still draggable); the path it makes
notes from gets a bump every `dist` along it, each `length` long and `size` keys high, on the side `side` picks.
`ease` > 0: over that far from each end of the range the bumps grow from nothing, so the line leads into them
smoothly instead of starting with a sudden (e.g. straight-up) side.
`fit`: the distance is stretched a little so a whole number of steps fits the range exactly; round a closed loop
(e.g. a full circle) the bumps then meet up where it starts.
Worked out as it looks on screen: k = beats per key on screen when the settings were last changed (like arcs, see
arc.py), so sizes are in keys, lengths / distances in beats along the line. length 0 = spikes: every bump is one
point pushed sideways and the line zigzags straight from spike to spike."""

import bisect
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
TUMOUR_DEFAULTS = {"on": True, "shape": "triangle", "size": 3.0, "length": 0.125, "dist": 0.125, "side": "alt",
                   "wrap": "simple", "start": 0.0, "end": 1.0, "ease": 0.0, "fit": False, "seed": 1, "mirror": False,
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
                        ("end", 0, 1), ("ease", 0, 1e6), ("k", 1e-9, 1e9)):
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
    return out


def template(shape, length, size):
    """The bump as (x along the line, y sideways) points from (0, 0) to (length, 0)."""
    if shape == "triangle":
        return [(0.0, 0.0), (length / 2, size), (length, 0.0)]
    if shape == "square":
        return [(0.0, 0.0), (0.0, size), (length, size), (length, 0.0)]
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
    lo, hi = min(tm["start"], tm["end"]) * w.total, max(tm["start"], tm["end"]) * w.total
    length = max(0.0, tm["length"] / k)
    dist = max(tm["dist"] / k, (hi - lo) / MAX_TUMOURS, 1e-9)
    # a closed loop (e.g. a full circle) with bumps all the way round: its end is its start again
    loop = w.closed and lo < 1e-9 and hi > w.total - 1e-9
    if tm.get("fit") and hi - lo > 1e-9:
        # a whole number of steps fits the range, so the last one lands right on its end; round a loop with
        # alternating sides, an even number, so the sides keep alternating where it meets up
        n = max(1, round((hi - lo) / dist))
        if loop and tm["side"] == "alt":
            n = max(2, 2 * round((hi - lo) / dist / 2))
        dist = (hi - lo) / n
        starts = [lo + (hi - lo) * i / n for i in range(n + 1)]
    else:
        starts = []
        s = lo
        while s <= hi + 1e-9 and len(starts) < MAX_TUMOURS:
            starts.append(min(s, hi))
            s += dist
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
        h = size * side * grows(s)
        out.block(np.column_stack([x + nx * h, y + ny * h]))
        if not fit_loop:
            out.add(base(starts[-1], w.total) + [w.pts[-1]])
    else:
        shape = template(tm["shape"], length, size)
        pts, per_bump, sizes = [], [], []
        for i, (s, side) in enumerate(zip(starts, sides)):
            room = min(hi, starts[i + 1] if i + 1 < len(starts) else math.inf)
            e = min(s + length, room)
            # (cut only where the next bump or the range's end is in the way: a round bump taller than half its
            # length bulges out past its own ends, and that part is kept when there's room)
            bump = cut(shape, room - s)
            if e - s < 1e-9:  # no room left (it would start right on the end of the range)
                out.add([w.at(s)])
            elif tm["wrap"] == "simple":  # on the straight line from where it starts to where it ends
                a, b = w.at(s), w.at(e)
                ux, uy = b[0] - a[0], b[1] - a[1]
                n = math.hypot(ux, uy)
                ux, uy = (ux / n, uy / n) if n > 1e-12 else w.direction(s)
                stretch = n / (e - s) if e - s > 1e-12 else 1.0
                bump = eased(bump, s)
                pts += bump
                per_bump.append((a[0], a[1], ux, uy, stretch, side))
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
                per_bump.append((s, side))
                sizes.append(len(bump))
                out.block(len(bump))
            nxt = starts[i + 1] if i + 1 < len(starts) else w.total
            out.add(base(e, nxt))
        out.add([w.pts[-1]])
        if per_bump:
            par = np.repeat(np.array(per_bump, float), sizes, axis=0)
            if tm["wrap"] == "simple":
                p = np.array(pts, float)
                x, y = p[:, 0] * par[:, 4], p[:, 1]
                ax, ay, ux, uy, side = par[:, 0], par[:, 1], par[:, 2], par[:, 3], par[:, 5]
                out.fill(np.column_stack([ax + ux * x - uy * y * side, ay + uy * x + ux * y * side]))
            else:
                p = np.array(pts, float)
                s, side = par[:, 0], par[:, 1]
                x = p[:, 0]
                y = p[:, 1] * grows(s + x)
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
