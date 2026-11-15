"""Polygons and stars: a custom shape made by the Polygon tool keeps its settings in sh["polygon"] and its strokes
are made from them (polygon_strokes), so the point count, the kind and a pattern on the sides can be changed any
time after it's placed.

sh["polygon"] = {"points": how many corners (3 = triangle; 2 = a flat line until a pattern pulls its two sides
apart) or star points, "style": "polygon" / "star" (outer and
inner points take turns: "inner" = how far out the inner points are, in % of the outer ones) / "cross" (a star drawn
by joining every "skip"-th corner, crossing itself: 5 skip 2 = a pentagram; when the two share a divisor it's
several loops, 6 skip 2 = two triangles), maybe "shape" (pattern.py: every side becomes it, from its corner to the
next) and "pattern" laid along every side (along the shape, if there is one): Each piece = every side gets all the
loops, Across all = they run on round the whole outline}.

A polygon sits with a flat side at the bottom (a square is a square, a triangle points up), a star with a point at
the top, and the drawing fills its box (0..1 both ways, like every custom shape). The sides go clockwise, so a
pattern's "to the side" (to the left of the way it goes) points outward."""

import math
import random

from notes.pattern import clean_pattern, clean_shape_formula, formed_paths, pattern_paths, shape_paths

STYLES = ("polygon", "star", "cross")
UNEVEN_HOWS = ("random", "turns")
MIXES = ("top", "turns")  # a second pattern: on top of the first (riding on it) / sides taking turns
POLYGON_DEFAULTS = {"points": 5, "style": "polygon", "inner": 50.0, "skip": 2, "twist": 0.0, "turn": 0.0,
                    "uneven": 0.0, "uneven_how": "random", "seed": 1}
MOST_SEED = 999999
MAX_POINTS = 200
MIN_POINTS = 2  # (2 = a flat line, two sides on top of each other: a pattern on them pulls them apart)
MOST_TWIST = 100.0  # % (100 = each side leaves its corner at a right angle)
MOST_TURN = 180.0  # degrees a star's inner points turn
SIDE_STEPS = 32  # points along a twisted side


def _signed(d, key, most):
    v = float(d.get(key, 0.0))
    return max(-most, min(most, v)) if math.isfinite(v) else 0.0


def clean_polygon(d):
    """Polygon settings from a file (or typed) made valid, or None."""
    if not isinstance(d, dict):
        return None
    try:
        out = {"points": max(MIN_POINTS, min(MAX_POINTS, int(d.get("points", POLYGON_DEFAULTS["points"])))),
               "style": d.get("style") if d.get("style") in STYLES else "polygon",
               "inner": max(0.0, min(1000.0, float(d.get("inner", POLYGON_DEFAULTS["inner"])))),
               "skip": max(1, int(d.get("skip", POLYGON_DEFAULTS["skip"]))),
               "twist": _signed(d, "twist", MOST_TWIST), "turn": _signed(d, "turn", MOST_TURN),
               "uneven": max(0.0, _signed(d, "uneven", 100.0)),
               "uneven_how": d.get("uneven_how") if d.get("uneven_how") in UNEVEN_HOWS else "random",
               "seed": max(1, min(MOST_SEED, int(d.get("seed", 1))))}
    except (TypeError, ValueError, OverflowError):
        return None
    pat, form = clean_pattern(d.get("pattern")), clean_shape_formula(d.get("shape"))
    pat2 = clean_pattern(d.get("pattern2"))
    if pat2 and not pat:  # (a second pattern needs a first: it moves up)
        pat, pat2 = pat2, None
    if pat:
        out["pattern"] = pat
    if pat2:
        out["pattern2"] = pat2
        out["mix"] = d.get("mix") if d.get("mix") in MIXES else "top"
    if form:
        out["shape"] = form
    return out


def uneven_shifts(d, count):
    """How far each of count points round the circle slides along it (in steps between points; 0 = even), for
    "uneven" %: Random (by "seed": the same number = the same shape) or In turns (every other side longer). Never
    past a neighbour: at most 0.45 of a step each way."""
    u = d.get("uneven", 0.0) / 100
    if not u:
        return [0.0] * count
    if d.get("uneven_how") == "turns":
        return [(0.225 if i % 2 else -0.225) * u for i in range(count)]
    rng = random.Random(d.get("seed", 1))
    return [rng.uniform(-0.45, 0.45) * u for _ in range(count)]


def polygon_loops(d):
    """The outline(s) as closed point lists, round (a circle through the corners has radius 1, centre 0, 0; y up),
    clockwise."""
    n = d["points"]
    if d["style"] == "star":
        r, turn = d["inner"] / 100, math.radians(d.get("turn", 0.0))
        shift = uneven_shifts(d, 2 * n)
        pts = []
        for i in range(n):
            for j, rad in ((0, 1.0), (0.5, r)):
                a = (math.pi / 2 - 2 * math.pi * (i + j + shift[2 * i + (j > 0)] / 2) / n
                     - (turn if j else 0.0))  # (+ turn = inner points clockwise)
                pts.append((rad * math.cos(a), rad * math.sin(a)))
        return [pts + [pts[0]]]
    shift = uneven_shifts(d, n)
    if d["style"] == "cross":
        k = d["skip"] % n or 1
        corners = [(math.cos(math.pi / 2 - 2 * math.pi * (i + shift[i]) / n),
                    math.sin(math.pi / 2 - 2 * math.pi * (i + shift[i]) / n)) for i in range(n)]
        loops = []
        for first in range(math.gcd(n, k)):
            loop = [corners[(first + j * k) % n] for j in range(n // math.gcd(n, k))]
            loops.append(loop + [loop[0]])
        return loops
    start = -math.pi / 2 - math.pi / n  # (the bottom side flat)
    pts = [(math.cos(start - 2 * math.pi * (i + shift[i]) / n), math.sin(start - 2 * math.pi * (i + shift[i]) / n))
           for i in range(n)]
    return [pts + [pts[0]]]


def twisted_side(a, b, twist):
    """One side from corner a to corner b bent into an S (twist -1..1): it leaves a turned by twist x 90 degrees
    and comes into b turned the same way, so every side swirls alike (a pinwheel). Points from a to b."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = twist * math.pi / 2
    c, s = math.cos(t), math.sin(t)
    hx, hy = (dx * c - dy * s) / 3, (dx * s + dy * c) / 3  # (both handles: the side's way turned, a third long)
    p1, p2 = (ax + hx, ay + hy), (bx - hx, by - hy)
    out = []
    for k in range(SIDE_STEPS + 1):
        u = k / SIDE_STEPS
        w0, w1, w2, w3 = (1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u * u * (1 - u), u ** 3
        out.append((w0 * ax + w1 * p1[0] + w2 * p2[0] + w3 * bx, w0 * ay + w1 * p1[1] + w2 * p2[1] + w3 * by))
    out[0], out[-1] = a, b
    return out


def drawn_loops(d):
    """polygon_loops with the twist on: each loop's points + where its corners are in them (or None: every point
    is a corner)."""
    loops, twist = polygon_loops(d), d.get("twist", 0.0) / 100
    if not twist:
        return [(loop, None) for loop in loops]
    out = []
    for loop in loops:
        pts, corners = [loop[0]], [0]
        for a, b in zip(loop, loop[1:]):
            pts += twisted_side(a, b, twist)[1:]
            corners.append(len(pts) - 1)
        out.append((pts, corners))
    return out


def polygon_aspect(d):
    """Width / height of the drawing as it looks round (for drawing it with Ctrl)."""
    pts = [p for loop, _ in drawn_loops(d) for p in loop]
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return w / h if w > 1e-9 and h > 1e-9 else 1.0


def polygon_strokes(d):
    """The custom shape strokes for polygon settings d: the loops stretched to fill the 0..1 box, each a closed
    polyline with "sides" and d's shape / pattern (custom.stroke_points lays them along each side). Twisted: each
    side is many points, "corners" = where the corners are among them."""
    loops = drawn_loops(d)
    pts = [p for loop, _ in loops for p in loop]
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0

    def spot(v, lo, size):  # (a flat drawing, 2 points, sits in the middle of its box: a pattern bulges both ways)
        return round((v - lo) / size, 9) if size > 1e-9 else 0.5

    out = []
    for loop, corners in loops:
        st = {"kind": "poly", "pts": [[spot(x, x0, w), spot(y, y0, h)] for x, y in loop], "sides": True}
        st["pts"][-1] = list(st["pts"][0])
        if corners:
            st["corners"] = corners
        for layer in ("shape", "pattern", "pattern2"):
            if d.get(layer):
                st[layer] = d[layer]
        if st.get("pattern2"):
            st["mix"] = d.get("mix", "top")
        out.append(st)
    return out


def update_polygon(sh):
    """A polygon custom shape's strokes made again from its settings (after they changed)."""
    if sh.get("polygon"):
        sh["strokes"] = polygon_strokes(sh["polygon"])
        if sh.get("fit"):  # (a fitted box hugs the new drawing, custom.fit_upright)
            from notes.custom import fit_upright
            fit_upright(sh)


def side_paths(pts, corners=None):
    """A polygon stroke's sides, each [start, end] (or, twisted: corners = where the corners are among pts, each
    side all its points from one corner to the next)."""
    if corners and clean_corners(corners, len(pts)):  # (points edited since in a live shape: every point a corner)
        return [[tuple(p) for p in pts[a:b + 1]] for a, b in zip(corners, corners[1:])]
    return [[tuple(a), tuple(b)] for a, b in zip(pts, pts[1:])]


def formed_sides(st):
    """A polygon stroke's sides with its shape and pattern(s) on them. A second pattern ("pattern2"): "top" = laid
    along the first one's outline (small spikes riding on big bumps), "turns" = sides take turns (1st, 3rd ...
    get the first pattern, 2nd, 4th ... the second)."""
    sides = side_paths(st["pts"], st.get("corners"))
    p2 = st.get("pattern2") if st.get("pattern") else None
    if not p2:
        return formed_paths(sides, st)
    if st.get("mix") == "turns":
        out = [None] * len(sides)
        out[0::2] = formed_paths(sides[0::2], st)
        if len(sides) > 1:
            out[1::2] = formed_paths(sides[1::2], dict(st, pattern=p2))
        return out
    if st.get("shape"):
        sides = shape_paths(sides, st["shape"])
    return pattern_paths(pattern_paths(sides, st["pattern"], arrays=True), p2)


def clean_corners(corners, n):
    """A twisted polygon stroke's corner places from a file, or None (not valid for n points)."""
    if not isinstance(corners, list) or len(corners) < 2 or corners[0] != 0 or corners[-1] != n - 1:
        return None
    if not all(isinstance(c, int) and not isinstance(c, bool) for c in corners):
        return None
    return list(corners) if all(a < b for a, b in zip(corners, corners[1:])) else None
