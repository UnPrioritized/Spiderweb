"""Simplify pitch points (stage E of the Hz bass Sketch extras, 2026-11-13; the plugin's "Simplify"): a line's points
cut down to a number, the biggest moves kept first. Works on a note's bend line ([[u, keys(, curve(, "s"))]]) and on
loudness lines ([[u or beat, value]], straight pieces).

The points are ranked once (rank): the line's ends and the points that must stay (a wobble's run ends on them) first,
then always the point furthest from the line through the points kept so far (Ramer-Douglas-Peucker in time order),
so flat holds and turning points survive and few points = only the biggest moves. simplified() takes the first
`count` of that order; a piece that now spans dropped points gets the curve that follows the old line best (a glide
drawn by many points becomes one curve), a piece between two points that were already neighbours keeps its own. A
kept point's Bézier handles stay (over a wider piece: the same reach in time), and with handles the ranking reads the
real curve (hunt 2026-11-13)."""

import heapq

import numpy as np

from notes.hz_lines import S_CURVE, bent_part, handles, line_arrays, line_at, side_handles, with_handles
from notes.hz_settings import keep_forward

ON_SPOT = 1e-9  # (two points this close in time: a jump)
CURVES = np.linspace(-0.9, 0.9, 19)  # the curves tried on a piece that spans dropped points (a single, then an S)
SAMPLES = 24  # spots a piece is compared at (inside it)
BETTER = 1e-6  # (a curve must beat a straight piece by more than this to be taken: straight stays straight)


def _between(xs, vs, a, b, j):
    """The line from point a to point b at point j's place (by time; a jump with nothing between in time: by count)."""
    w = xs[b] - xs[a]
    f = (xs[j] - xs[a]) / w if w > ON_SPOT else (j - a) / (b - a)
    return vs[a] + (vs[b] - vs[a]) * f


def _worst(xs, vs, a, b):
    """(how far, which) the point between a and b furthest from the straight line a -> b; (-1, None) with none."""
    if b - a < 2:
        return -1.0, None
    js = np.arange(a + 1, b)
    w = xs[b] - xs[a]
    f = (xs[js] - xs[a]) / w if w > ON_SPOT else (js - a) / (b - a)
    off = np.abs(vs[js] - (vs[a] + (vs[b] - vs[a]) * f))
    k = int(np.argmax(off))
    return float(off[k]), int(js[k])


class _Curve:
    """A line with Bézier handles read finely once (hunt 2026-11-13: ranking by the points' own heights dropped a big
    bump made by handles first): spots = every point + CURVE_SPOTS inside each piece, in time order."""

    def __init__(self, pts, xs):
        inner = [xs[j] + (xs[j + 1] - xs[j]) * _U for j in range(len(xs) - 1) if xs[j + 1] - xs[j] > ON_SPOT]
        self.x = np.sort(np.concatenate([xs] + inner))
        self.v = line_at(pts, self.x, arrays=line_arrays(pts))

    def worst(self, xs, vs, a, b):
        """_worst, but how far the real curve strays from a -> b: (how far, the point inside nearest that spot)."""
        off, j = _worst(xs, vs, a, b)
        if j is None or xs[b] - xs[a] <= ON_SPOT:
            return off, j
        lo, hi = np.searchsorted(self.x, xs[a], "right"), np.searchsorted(self.x, xs[b], "left")
        if hi <= lo:
            return off, j
        x, v = self.x[lo:hi], self.v[lo:hi]
        dev = np.abs(v - (vs[a] + (vs[b] - vs[a]) * (x - xs[a]) / (xs[b] - xs[a])))
        k = int(np.argmax(dev))
        if dev[k] <= off + 1e-9:
            return off, j
        inner = np.arange(a + 1, b)
        return float(dev[k]), int(inner[int(np.argmin(np.abs(xs[inner] - x[k])))])


def rank(pts, must=()):
    """The points' numbers in the order simplified() keeps them: the first and last, those in `must` (point
    numbers), then the furthest from the line kept so far, one at a time (ties: the earlier point). A line with
    Bézier handles: how far its REAL curve strays counts (_Curve: a bump made by handles is a big move too)."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    xs = np.array([p[0] for p in pts], float)
    vs = np.array([p[1] for p in pts], float)
    worst = _Curve(pts, xs).worst if any(handles(p) for p in pts) else _worst
    first = sorted({0, n - 1} | {j for j in must if 0 <= j < n})
    order = list(first)
    heap = []
    for a, b in zip(first, first[1:]):
        off, j = worst(xs, vs, a, b)
        if j is not None:
            heap.append((-off, j, a, b))
    heapq.heapify(heap)
    while heap:
        _, j, a, b = heapq.heappop(heap)
        order.append(j)
        for lo, hi in ((a, j), (j, b)):
            off, k = worst(xs, vs, lo, hi)
            if k is not None:
                heapq.heappush(heap, (-off, k, lo, hi))
    return order


_U = np.linspace(0.0, 1.0, SAMPLES + 2)[1:-1]
_TRIED = [(round(float(c), 2), add) for add in (0.0, S_CURVE) for c in CURVES if abs(c) > 1e-9]
_SHAPES = np.array([bent_part(_U, c + add) for c, add in _TRIED])  # (each tried curve's way, made once)


def fitted(pts, a, b, arrays=None):
    """The curve (the extras after a point's [u, value]: [] straight, [c] or [c, "s"]) that makes the piece from
    point a to point b follow the old line between them most closely. arrays = line_arrays(pts) made once."""
    u0, u1, v0, v1 = pts[a][0], pts[b][0], pts[a][1], pts[b][1]
    if u1 - u0 <= ON_SPOT or abs(v1 - v0) < 1e-9:
        return []  # (a jump, or level ends: a curve can't bulge, straight)
    want = (line_at(pts, u0 + _U * (u1 - u0), arrays=arrays) - v0) / (v1 - v0)
    errs = np.sum((_SHAPES - want) ** 2, axis=1)
    k = int(np.argmin(errs))
    if errs[k] >= float(np.sum((_U - want) ** 2)) - BETTER:
        return []
    c, add = _TRIED[k]
    return [c, "s"] if add else [c]


def simplified(pts, count, order=None, semitones=False, curves=True):
    """The line pts with only `count` of its points (the first `count` of rank's order; never fewer than the ones it
    must keep): the kept points as they were, each piece over dropped points re-curved to follow the old line
    (curves=False: straight, a loudness line). semitones = values rounded to whole keys."""
    order = rank(pts) if order is None else order
    keep = sorted(order[:max(2, count)])
    arrays = line_arrays(pts) if curves and len(keep) < len(pts) else None
    out = []
    prev = None  # (the kept point before this one)
    for a, b in zip(keep, keep[1:] + [None]):
        p = pts[a]
        q = [p[0], p[1]]
        h = handles(p)  # (Bézier handles, stage H)
        if b is not None:
            if b == a + 1:
                q += [v for v in p[2:] if not isinstance(v, dict)]  # (the same piece as before)
            elif curves:
                q += fitted(pts, a, b, arrays)
        elif len(p) > 2 and p[2] == "hold":
            q += list(p[2:])
        if h:
            # (a side over a piece that now spans dropped points keeps its reach in time, as a share of the wider
            # piece: a bump made by a kept point's handles survives; hunt 2026-11-13)
            h = list(h)
            if prev is not None and prev != a - 1 and p[0] - pts[prev][0] > ON_SPOT:
                h[0] *= (p[0] - pts[a - 1][0]) / (p[0] - pts[prev][0])
            if b is not None and b != a + 1 and pts[b][0] - p[0] > ON_SPOT:
                h[2] *= (pts[a + 1][0] - p[0]) / (pts[b][0] - p[0])
            q = side_handles(with_handles(q, h), prev is not None, b is not None)
        out.append(q)
        prev = a
    if semitones:
        for q in out:
            q[1] = float(round(q[1]))
    return keep_forward(out)


def wobble_ends(n):
    """Point numbers of note n's bend line that a wobble's run starts or ends on (kept: the wobble stays pickable)."""
    pts = n.get("bend") or ()
    ends = [u for w in n.get("wobble") or () for u in w["at"]]
    return {j for j, p in enumerate(pts) if any(abs(p[0] - u) < 1e-6 for u in ends)}


def least(pts, must=()):
    """The fewest points the line can be cut to (its ends + the ones it must keep)."""
    return len({0, len(pts) - 1} | set(must)) if pts else 0


def share_count(count, most, pts, must=()):
    """The points a line with len(pts) points keeps when the picked one keeps `count` of its `most`: the same share,
    rounded, never below what it must keep nor above what it has."""
    if most <= 0:
        return len(pts)
    return max(least(pts, must), min(len(pts), round(len(pts) * count / most)))
