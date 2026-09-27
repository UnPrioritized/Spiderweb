"""Freehand strokes made perfect. With a sensitivity (0-100) a wobbly stroke becomes straight lines and smooth
curves, and a stroke that ends where it started becomes a perfect shape: a circle or ellipse, a square or rectangle,
or a straight-sided polygon (triangle, pentagon, hexagon). The sensitivity sets how far the result may stray from the
drawing (up to a fifth of its size), and the simplest shape that stays that close wins, so turning it up makes things
simpler: a slightly oval loop becomes an ellipse, then a circle; a rectangle becomes a square. A loop keeps its kind
(a square never becomes a circle). Tilt is kept (nothing is turned upright). Only the setting is stored, so the
stroke as drawn can always come back (0 = as drawn).

It's all worked out in screen proportions, like arcs: x = beats / k (k = beats per key on screen when the setting
was picked), y = pitch; custom shape strokes use u / k, v (k = how many u one v is on screen)."""

import functools
import math

from notes.bezier import fit, segments, seg_point

SMOOTH_DEFAULT = 0
CORNER = math.radians(40)   # a stroke turning more than this within a short stretch has a corner there
MAX_POLYGON = 6             # a loop with more corners than this is no shape, just lines and curves
LOOP = 0.1                  # a stroke ending this close to its start (part of its size) is a loop
SHAPE_SAMPLES = 120         # points along a loop when fitting shapes to it
PIECE_SAMPLES = 600         # points along a stroke when finding its corners and fitting lines / curves
CORNER_COST = 0.012         # how much worse (part of the loop's size) a fit may be per extra corner / setting it saves


def tolerance(level, size):
    """How far the result may stray from the drawing, for a stroke of that size (its bounding box's diagonal)."""
    return 0.2 * size * (max(0.0, min(100.0, level)) / 100) ** 2.5


def clean_level(value):
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return SMOOTH_DEFAULT


def smooth_path(pts, level, k=1.0):
    """The stroke (x, y points) made perfect at this sensitivity (see the top); 0 = as drawn."""
    pts = [tuple(p) for p in pts]
    if level <= 0 or len(pts) < 3 or k <= 0:
        return pts
    q = _dedupe([(x / k, y) for x, y in pts])
    if len(q) < 3:
        return pts
    xs, ys = [x for x, _ in q], [y for _, y in q]
    size = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    if size < 1e-9:
        return pts
    tol = tolerance(level, size)
    gap = math.dist(q[0], q[-1])
    out = None
    if gap <= LOOP * size:
        out = next((shape for shape, dev in _shape_ladder(tuple(q)) if dev <= tol), None)
    if out is None:
        out = _pieces(q, tol, gap <= tol)
    return [(x * k, y) for x, y in out]


# ---------------------------------------------------------------- perfect shapes (loops)

@functools.lru_cache(maxsize=64)
def _shape_ladder(q):
    """[(shape, how far it strays)] for a loop, simplest first, all of the loop's kind: round (circle, ellipse),
    four corners (square, rectangle, the four-cornered polygon) or another polygon. The kind is whichever fits best,
    counting a little against every extra corner or setting. Doesn't depend on the sensitivity, so it's remembered
    (moving the number only picks from it)."""
    r = _resample(list(q) + [q[0]], SHAPE_SAMPLES)
    size = max(math.dist(a, b) for a in r[::6] for b in r[::6])
    kinds = {}
    ellipse = _ellipse(r)
    if ellipse:
        kinds["round"] = (_dev(r, ellipse), 5)
    box = _box(r)
    if box:
        kinds["box"] = (_dev(r, _box_points(*box)), 5)
    polygons = {}
    for n in range(3, MAX_POLYGON + 1):
        poly = _best_polygon(r, n)
        if poly and len(poly) - 1 == n:
            polygons[n] = poly
            kinds[n] = (_dev(r, poly), 2 * n)
    if not kinds:
        return []
    kind = min(kinds, key=lambda k: kinds[k][0] + CORNER_COST * size * kinds[k][1])
    if kind == "round":
        shapes = [_circle(r), ellipse]
    elif kind == "box" or kind == 4:
        shapes = [_box_points(box[0], box[1], (box[2] + box[3]) / 2, (box[2] + box[3]) / 2) if box else None,
                  _box_points(*box) if box else None, polygons.get(4)]
    else:
        shapes = [polygons[kind]]
    return [(s, _dev(r, s)) for s in shapes if s]


def _circle(r):
    cx, cy = _mean(r)
    rad = sum(math.dist(p, (cx, cy)) for p in r) / len(r)
    return _ellipse_points(cx, cy, rad, rad, 0.0)


def _ellipse(r):
    """Tilted ellipse from the loop's spread (points evenly along it): its axes are the main directions, scaled so
    the loop sits on it on average."""
    cx, cy = _mean(r)
    sxx = sum((x - cx) ** 2 for x, _ in r) / len(r)
    syy = sum((y - cy) ** 2 for _, y in r) / len(r)
    sxy = sum((x - cx) * (y - cy) for x, y in r) / len(r)
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    half = math.hypot((sxx - syy) / 2, sxy)
    l1, l2 = (sxx + syy) / 2 + half, max((sxx + syy) / 2 - half, 0.0)
    a, b = math.sqrt(2 * l1), math.sqrt(2 * l2)
    if b < 1e-9:
        return None
    ca, sa = math.cos(angle), math.sin(angle)
    rho = sum(math.hypot(((x - cx) * ca + (y - cy) * sa) / a, (-(x - cx) * sa + (y - cy) * ca) / b)
              for x, y in r) / len(r)
    return _ellipse_points(cx, cy, a * rho, b * rho, angle)


def _ellipse_points(cx, cy, a, b, angle, n=96):
    """Closed, starting at its leftmost point (like the other closed shapes)."""
    ca, sa = math.cos(angle), math.sin(angle)
    pts = [(cx + a * math.cos(t) * ca - b * math.sin(t) * sa, cy + a * math.cos(t) * sa + b * math.sin(t) * ca)
           for t in (2 * math.pi * i / n for i in range(n))]
    return _closed_from_left(pts)


def _box(r):
    """The loop's rectangle: turned like its smallest bounding box, each side where the loop's points near it are
    on average. (centre, direction, half width, half height), or None."""
    hull = _hull(r)
    best = None
    for a, b in zip(hull, hull[1:] + hull[:1]):
        d = math.dist(a, b)
        if d < 1e-12:
            continue
        ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
        us = [x * ux + y * uy for x, y in hull]
        vs = [-x * uy + y * ux for x, y in hull]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            best = area, (ux, uy), [min(us), max(us), min(vs), max(vs)]
    if best is None:
        return None
    _, (ux, uy), edges = best
    sides = [[], [], [], []]  # left, right, bottom, top: the points nearest each
    for x, y in r:
        u, v = x * ux + y * uy, -x * uy + y * ux
        d = [u - edges[0], edges[1] - u, v - edges[2], edges[3] - v]
        i = d.index(min(d))
        sides[i].append(u if i < 2 else v)
    u0, u1, v0, v1 = [sum(s) / len(s) if s else e for s, e in zip(sides, edges)]
    if u1 - u0 < 1e-9 or v1 - v0 < 1e-9:
        return None
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    return (cu * ux - cv * uy, cu * uy + cv * ux), (ux, uy), (u1 - u0) / 2, (v1 - v0) / 2


def _box_points(c, d, hw, hh):
    (cx, cy), (ux, uy) = c, d
    return _closed_from_left([(cx + su * hw * ux - sv * hh * uy, cy + su * hw * uy + sv * hh * ux)
                              for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))])


def _best_polygon(r, n):
    """The polygon of at most n corners that follows the loop most closely (the smallest tolerance for which
    Ramer-Douglas-Peucker keeps no more than n of its points)."""
    loop = r[:-1]
    c = _mean(loop)
    i = max(range(len(loop)), key=lambda j: math.dist(loop[j], c))  # always a corner: start there
    loop = loop[i:] + loop[:i] + [loop[i]]
    lo, hi, best = 0.0, max(math.dist(loop[0], p) for p in loop), None
    for _ in range(16):
        mid = (lo + hi) / 2
        idx = _rdp(loop, mid)[:-1]
        if len(idx) > n:
            lo = mid
        else:
            hi = mid
            if len(idx) >= 3:
                best = [loop[j] for j in idx]
    return _closed_from_left(best) if best else None


def _dev(r, shape):
    """How far the shape strays from the loop, both ways (the furthest loop point from the shape, the furthest
    part of the shape from the loop)."""
    return max(max(_dist_to_path(p, shape) for p in r), max(_dist_to_path(p, r) for p in _resample(shape, 60)))


def _closed_from_left(pts):
    i = min(range(len(pts)), key=lambda j: (pts[j][0], pts[j][1]))
    pts = pts[i:] + pts[:i]
    return pts + [pts[0]]


# ---------------------------------------------------------------- lines and curves

def _pieces(q, tol, closed):
    """Straight lines and smooth curves: split at the corners (sharp turns), each part a straight line if it stays
    within tol of one, else curves within tol. closed: it ends where it starts."""
    if closed and math.dist(q[0], q[-1]) > 1e-12:
        q = q + [q[0]]
    r = _resample(q, PIECE_SAMPLES)
    corners = _corners(r, tol, closed)
    if closed and corners:  # start the loop at a corner, so it has no bend where it joins up
        c = corners[0]
        r = r[c:] + r[1:c + 1]
        corners = [(i - c) % PIECE_SAMPLES for i in corners]
    cut = sorted(set([0] + corners + [PIECE_SAMPLES]))
    step = math.dist(r[0], r[1])
    out = [r[0]]
    for a, b in zip(cut, cut[1:]):
        part = r[a:b + 1]
        if len(part) < 3 or max(_seg_dist(p, part[0], part[-1]) for p in part) <= tol:
            out.append(part[-1])
            continue
        for seg in segments(fit(part, tol)):  # each curve piece: about as many points as the stroke had there
            n = max(2, min(24, round(_path_len(seg) / max(step * 4, tol))))
            out += [tuple(seg_point(*seg, i / n)) for i in range(1, n + 1)]
    if closed:
        out[-1] = out[0]
    return out


def _corners(r, tol, closed):
    """Where the path (evenly spaced points) turns sharply within a short stretch: point numbers. The stretch grows
    with the tolerance, so wobbles smaller than it don't count. A corner does most of its turning right at the
    point (over a third of the stretch); a smooth bend (a wave's peak) turns gradually, so it isn't one."""
    n = len(r) - 1
    step = math.dist(r[0], r[1]) or 1e-12
    m = max(3, round(max(4 * tol, 0.03 * step * n) / step))
    if not closed and 2 * m >= n:
        return []
    idx = list(range(n)) if closed else list(range(m, n - m + 1))

    def at(i):
        return r[i % n] if closed else r[i]
    turn = {i: _turn(at(i - m), at(i), at(i + m)) for i in idx}
    out = []
    for i in idx:
        if turn[i] <= CORNER or _turn(at(i - m // 3), at(i), at(i + m // 3)) < 0.6 * turn[i]:
            continue
        near = [((j % n) if closed else j) for j in range(i - m, i + m + 1) if j != i]
        if all(turn[i] > turn.get(j, 0.0) or (turn[i] == turn.get(j) and i < j) for j in near):
            out.append(i)
    return out


# ---------------------------------------------------------------- helpers

def _dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) > 1e-9:
            out.append(p)
    return out


def _mean(pts):
    return sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)


def _path_len(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _resample(pts, n):
    """n + 1 points evenly along the path (its first and last point included)."""
    lens = [0.0]
    for a, b in zip(pts, pts[1:]):
        lens.append(lens[-1] + math.dist(a, b))
    total = lens[-1]
    if total < 1e-12:
        return [tuple(pts[0])] * (n + 1)
    out, j = [], 0
    for i in range(n + 1):
        s = total * i / n
        while j < len(pts) - 2 and lens[j + 1] < s:
            j += 1
        seg = lens[j + 1] - lens[j]
        t = 0.0 if seg < 1e-12 else (s - lens[j]) / seg
        (ax, ay), (bx, by) = pts[j], pts[j + 1]
        out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return out


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    t = 0.0 if ll < 1e-24 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _dist_to_path(p, path):
    return min(_seg_dist(p, a, b) for a, b in zip(path, path[1:]))


def _turn(a, b, c):
    """How sharply the path turns at b (0 = straight on, pi = straight back)."""
    d1, d2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
    l1, l2 = math.hypot(*d1), math.hypot(*d2)
    if l1 < 1e-12 or l2 < 1e-12:
        return 0.0
    return math.acos(max(-1.0, min(1.0, (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2))))


def _rdp(pts, tol):
    """Ramer-Douglas-Peucker: the point numbers that keep the path within tol (first and last always)."""
    keep = {0, len(pts) - 1}
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        worst, at = -1.0, None
        for i in range(a + 1, b):
            d = _seg_dist(pts[i], pts[a], pts[b])
            if d > worst:
                worst, at = d, i
        if at is not None and worst > tol:
            keep.add(at)
            stack += [(a, at), (at, b)]
    return sorted(keep)


def _hull(pts):
    """Convex hull (Andrew's monotone chain), anticlockwise."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]
