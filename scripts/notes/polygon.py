"""Polygons and stars: a custom shape made by the Polygon tool keeps its settings in sh["polygon"] and its strokes
are made from them (polygon_strokes), so the point count, the kind and a pattern on the sides can be changed any
time after it's placed.

sh["polygon"] = {"points": how many corners (3 = triangle) or star points, "style": "polygon" / "star" (outer and
inner points take turns: "inner" = how far out the inner points are, in % of the outer ones) / "cross" (a star drawn
by joining every "skip"-th corner, crossing itself: 5 skip 2 = a pentagram; when the two share a divisor it's
several loops, 6 skip 2 = two triangles), maybe "pattern" (pattern.py) laid along every side: Each piece = every
side gets all the loops, Across all = they run on round the whole outline}.

A polygon sits with a flat side at the bottom (a square is a square, a triangle points up), a star with a point at
the top, and the drawing fills its box (0..1 both ways, like every custom shape). The sides go clockwise, so a
pattern's "to the side" (to the left of the way it goes) points outward."""

import math

from notes.pattern import clean_pattern

STYLES = ("polygon", "star", "cross")
POLYGON_DEFAULTS = {"points": 5, "style": "polygon", "inner": 50.0, "skip": 2}
MAX_POINTS = 200


def clean_polygon(d):
    """Polygon settings from a file (or typed) made valid, or None."""
    if not isinstance(d, dict):
        return None
    try:
        out = {"points": max(3, min(MAX_POINTS, int(d.get("points", POLYGON_DEFAULTS["points"])))),
               "style": d.get("style") if d.get("style") in STYLES else "polygon",
               "inner": max(0.0, min(1000.0, float(d.get("inner", POLYGON_DEFAULTS["inner"])))),
               "skip": max(1, int(d.get("skip", POLYGON_DEFAULTS["skip"])))}
    except (TypeError, ValueError):
        return None
    pat = clean_pattern(d.get("pattern"))
    if pat:
        out["pattern"] = pat
    return out


def polygon_loops(d):
    """The outline(s) as closed point lists, round (a circle through the corners has radius 1, centre 0, 0; y up),
    clockwise."""
    n = d["points"]
    if d["style"] == "star":
        r = d["inner"] / 100
        pts = []
        for i in range(n):
            for j, rad in ((0, 1.0), (0.5, r)):
                a = math.pi / 2 - 2 * math.pi * (i + j) / n
                pts.append((rad * math.cos(a), rad * math.sin(a)))
        return [pts + [pts[0]]]
    if d["style"] == "cross":
        k = d["skip"] % n or 1
        corners = [(math.cos(math.pi / 2 - 2 * math.pi * i / n), math.sin(math.pi / 2 - 2 * math.pi * i / n))
                   for i in range(n)]
        loops = []
        for first in range(math.gcd(n, k)):
            loop = [corners[(first + j * k) % n] for j in range(n // math.gcd(n, k))]
            loops.append(loop + [loop[0]])
        return loops
    start = -math.pi / 2 - math.pi / n  # (the bottom side flat)
    pts = [(math.cos(start - 2 * math.pi * i / n), math.sin(start - 2 * math.pi * i / n)) for i in range(n)]
    return [pts + [pts[0]]]


def polygon_aspect(d):
    """Width / height of the drawing as it looks round (for drawing it with Ctrl)."""
    pts = [p for loop in polygon_loops(d) for p in loop]
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return w / h if w > 1e-9 and h > 1e-9 else 1.0


def polygon_strokes(d):
    """The custom shape strokes for polygon settings d: the loops stretched to fill the 0..1 box, each a closed
    polyline with "sides" and d's pattern (custom.stroke_points lays it along each side)."""
    loops = polygon_loops(d)
    pts = [p for loop in loops for p in loop]
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    x0, y0 = min(xs), min(ys)
    w, h = max(max(xs) - x0, 1e-9), max(max(ys) - y0, 1e-9)
    out = []
    for loop in loops:
        st = {"kind": "poly", "pts": [[round((x - x0) / w, 9), round((y - y0) / h, 9)] for x, y in loop],
              "sides": True}
        st["pts"][-1] = list(st["pts"][0])
        if d.get("pattern"):
            st["pattern"] = d["pattern"]
        out.append(st)
    return out


def update_polygon(sh):
    """A polygon custom shape's strokes made again from its settings (after they changed)."""
    if sh.get("polygon"):
        sh["strokes"] = polygon_strokes(sh["polygon"])


def side_paths(pts):
    """A polygon stroke's sides, each [start, end]."""
    return [[tuple(a), tuple(b)] for a, b in zip(pts, pts[1:])]
