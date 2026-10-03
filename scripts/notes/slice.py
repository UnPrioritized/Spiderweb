"""Slice: cutting shapes along a straight line drawn with the Slice tool. Lines, curves and arcs are cut where it
crosses them (the window does that, like Split here); a custom shape is cut in two filled halves when the line goes
all the way across it: each half keeps its side's strokes (as polylines) and gets the cut line's pieces inside the
shape as a new edge, so its fill closes there. Areas coloured by hand stay with the half their spot is in."""

import copy

import numpy as np

from notes.custom import frame_to_uv, refit, stroke_points
from notes.gaterange import part_range

EPS = 1e-9


def crossings(path, a, b, whole_line=False):
    """Where the polyline path ((N, 2) points) crosses the segment a-b, in path order: [(point, s along a-b)].
    whole_line: the infinite line through a and b instead (s may then be outside 0..1)."""
    p = np.asarray(path, float).reshape(-1, 2)
    if len(p) < 2:
        return []
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    q, r = p[:-1], p[1:] - p[:-1]
    den = r[:, 0] * d[1] - r[:, 1] * d[0]
    ok = np.abs(den) > EPS
    den = np.where(ok, den, 1)
    w = a - q
    t = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den  # along the path's piece
    s = (w[:, 0] * r[:, 1] - w[:, 1] * r[:, 0]) / den  # along a-b
    hit = ok & (t >= -EPS) & (t < 1 - EPS) & (whole_line | ((s >= -EPS) & (s <= 1 + EPS)))
    if len(p) >= 2:  # (the very last point counts too)
        hit[-1] |= ok[-1] & (abs(t[-1] - 1) <= EPS) & (whole_line | ((s[-1] >= -EPS) & (s[-1] <= 1 + EPS)))
    out = []
    for j in np.flatnonzero(hit):
        pt = q[j] + r[j] * min(1.0, max(0.0, t[j]))
        if not out or np.hypot(*(pt - out[-1][0])) > 1e-7:
            out.append((pt, float(s[j]), int(j), float(t[j])))
    return [(pt, s) for pt, s, _, _ in out]


def cut_pieces(path, a, b):
    """The polyline cut wherever it crosses the segment a-b: a list of polylines (lists of points)."""
    p = [tuple(map(float, pt)) for pt in path]
    if len(p) < 2:
        return [p]
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    pieces, cur = [], [p[0]]
    for q0, q1 in zip(p, p[1:]):
        q, r = np.asarray(q0), np.asarray(q1) - np.asarray(q0)
        den = r[0] * d[1] - r[1] * d[0]
        cuts = []
        if abs(den) > EPS:
            w = a - q
            t = (w[0] * d[1] - w[1] * d[0]) / den
            s = (w[0] * r[1] - w[1] * r[0]) / den
            if EPS < t < 1 - EPS and -EPS <= s <= 1 + EPS:
                cuts.append(t)
            elif abs(t - 1) <= EPS and -EPS <= s <= 1 + EPS and q1 != p[-1]:
                cuts.append(1.0)  # (right on a point: cut there)
        for t in cuts:
            pt = tuple(q + r * t)
            cur.append(pt)
            if len(cur) >= 2:
                pieces.append(cur)
            cur = [pt]
        if not cuts or cuts[-1] < 1:
            cur.append(q1)
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces


def side(pts, a, b):
    """Which side of the line a-b the points lie on (+1 / -1): the point furthest from it decides; 0 = on it."""
    p = np.asarray(pts, float).reshape(-1, 2)
    d = np.asarray(b, float) - np.asarray(a, float)
    c = d[0] * (p[:, 1] - a[1]) - d[1] * (p[:, 0] - a[0])
    k = int(np.argmax(np.abs(c)))
    return 0 if abs(c[k]) < 1e-9 else (1 if c[k] > 0 else -1)


def inside(polys, pt):
    """Is pt inside the drawing (even-odd: how many times a ray to the right crosses its strokes)?"""
    x, y = pt
    n = 0
    for poly in polys:
        p = np.asarray(poly, float).reshape(-1, 2)
        if len(p) < 2:
            continue
        x0, y0, x1, y1 = p[:-1, 0], p[:-1, 1], p[1:, 0], p[1:, 1]
        c = (y0 > y) != (y1 > y)
        xs = x0[c] + (y - y0[c]) * (x1[c] - x0[c]) / (y1[c] - y0[c])
        n += int((xs > x).sum())
    return n % 2 == 1


def slice_custom(sh, a, b, ppq):
    """A custom shape cut along the segment a-b (beats, keys) -> its two halves, or None when the segment doesn't
    go all the way across it (or misses it). A spam gate Range is shared out: each half gets its part of it. Glue
    boxes are left as they were: the caller moves them (glue.for_part)."""
    to_uv = frame_to_uv(sh["pts"])
    if to_uv is None:
        return None
    a, b = np.array(to_uv(*a)), np.array(to_uv(*b))
    if np.hypot(*(b - a)) < EPS:
        return None
    polys = [stroke_points(st) for st in sh["strokes"]]
    ss = []
    for poly in polys:
        for _, s in crossings(poly, a, b, whole_line=True):
            if s < -1e-6 or s > 1 + 1e-6:
                return None  # (the line crosses the shape past the segment's end: not all the way across)
            ss.append(s)
    if len(ss) < 2:
        return None
    ss = sorted(set(round(s, 9) for s in ss))
    cut_lines = []
    for s0, s1 in zip(ss, ss[1:]):
        mid = a + (b - a) * (s0 + s1) / 2
        if s1 - s0 > 1e-9 and inside(polys, mid):
            cut_lines.append([list(a + (b - a) * s0), list(a + (b - a) * s1)])
    halves = {1: [], -1: []}
    for st, poly in zip(sh["strokes"], polys):
        pieces = cut_pieces(poly, a, b)
        closed = len(poly) > 2 and np.hypot(*(np.asarray(poly[0]) - np.asarray(poly[-1]))) < 1e-9
        if closed and len(pieces) > 1 and side([poly[0]], a, b):  # (closed: its last and first pieces are one)
            pieces = [pieces[-1] + pieces[0][1:]] + pieces[1:-1]
        keep = {k: st[k] for k in ("role", "colour") if k in st}
        for piece in pieces:
            k = side(piece, a, b)
            if k:
                halves[k].append(dict(keep, kind="poly", pts=[list(pt) for pt in piece]))
    if not halves[1] or not halves[-1] or not cut_lines:
        return None
    out = []
    for k in (1, -1):
        new = copy.deepcopy({key: v for key, v in sh.items()
                             if key not in ("strokes", "areas", "polygon", "from", "name")})
        new["name"] = sh.get("name", "")
        new["strokes"] = halves[k] + [{"kind": "poly", "pts": [list(p) for p in line]} for line in cut_lines]
        areas = [list(ar) for ar in sh.get("areas", ()) if side([ar[:2]], a, b) == k]
        if areas:
            new["areas"] = areas
        refit(new)
        if sh.get("range"):
            new["gate"], new["range"] = part_range(sh, new, ppq)
        out.append(new)
    return out


def clip_segment(a, b, box):
    """The part of the segment a-b inside box (b0, p0, b1, p1 in any order), or None."""
    lo = np.array([min(box[0], box[2]), min(box[1], box[3])], float)
    hi = np.array([max(box[0], box[2]), max(box[1], box[3])], float)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    t0, t1 = 0.0, 1.0
    for k in (0, 1):
        if abs(d[k]) < EPS:
            if not lo[k] <= a[k] <= hi[k]:
                return None
            continue
        u0, u1 = sorted(((lo[k] - a[k]) / d[k], (hi[k] - a[k]) / d[k]))
        t0, t1 = max(t0, u0), min(t1, u1)
        if t0 > t1:
            return None
    return tuple(a + d * t0), tuple(a + d * t1)
