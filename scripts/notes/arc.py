"""Arcs: a piece of a perfect circle through three points (start, a point it passes through, end).
Used by the Arc shape on the roll and arc strokes in the drawer.

A circle only looks round when both directions have the same scale, and the roll's beats and keys don't: so an arc
remembers k = how many beats one key was on screen when it was drawn (on screen, x = beats / k and y = keys have
the same scale). In the drawer (same scale both ways) k is 1. Turning a shape 90 degrees with r beats per key on
screen makes it k' = r * r / k; stretching the x direction sx times and y sy times makes it k * sx / sy."""

import math

STEP = math.radians(1.5)  # one sample point per 1.5 degrees of the circle


def circle(a, b, c):
    """(centre, radius) of the circle through a, b and c, or None if they're in a straight line / two are the same."""
    a_sq = (b[0] - c[0]) ** 2 + (b[1] - c[1]) ** 2
    b_sq = (a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2
    c_sq = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
    size = max(a_sq, b_sq, c_sq)
    if size == 0 or min(a_sq, b_sq, c_sq) < 1e-12 * size:
        return None
    s = a_sq * (b_sq + c_sq - a_sq)
    t = b_sq * (a_sq + c_sq - b_sq)
    u = c_sq * (a_sq + b_sq - c_sq)
    total = s + t + u
    if abs(total) < 1e-9 * size * size:
        return None
    centre = ((s * a[0] + t * b[0] + u * c[0]) / total, (s * a[1] + t * b[1] + u * c[1]) / total)
    return centre, math.dist(a, centre)


def _angles(a, b, c, centre):
    """Start angle and how far round (signed) the arc goes from a through b to c."""
    t0 = math.atan2(a[1] - centre[1], a[0] - centre[0])
    t1 = math.atan2(c[1] - centre[1], c[0] - centre[0])
    span = (t1 - t0) % (2 * math.pi)
    # which way round: the way that passes b (b is on the left or right of the line a -> c)
    if (c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0]) > 0:
        span -= 2 * math.pi
    return t0, span


def full_circle(a, b, c):
    """End on the start (and the middle point elsewhere): the whole circle with a -> b across it, going round
    anticlockwise as the roll looks. (centre, radius, start angle, 2 pi) or None."""
    if a != c or a == b:
        return None
    centre = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    return centre, math.dist(a, centre), math.atan2(a[1] - centre[1], a[0] - centre[0]), 2 * math.pi


def _arc(m):
    """(centre, radius, start angle, signed span) of the arc through the three points, or None if there's none."""
    whole = full_circle(*m)
    if whole:
        return whole
    got = circle(*m)
    if got is None:
        return None
    return (*got, *_angles(*m, got[0]))


def arc_points(pts, k=1.0, step=STEP):
    """The arc as a list of points from pts[0] through pts[1] to pts[2] (a straight line if they're in a line; the
    whole circle if pts[2] is pts[0]). Starts and ends exactly on the end points (so outlines that meet there stay
    joined)."""
    (a, b, c) = [tuple(p) for p in pts[:3]] if len(pts) >= 3 else (tuple(pts[0]), None, tuple(pts[-1]))
    if b is None:
        return [a, c]
    m = [(p[0] / k, p[1]) for p in (a, b, c)]
    got = _arc(m)
    if got is None:
        return [a, b, c] if b not in (a, c) else [a, c]
    centre, r, t0, span = got
    n = max(2, math.ceil(abs(span) / step))
    out = [a]
    for i in range(1, n):
        t = t0 + span * i / n
        out.append(((centre[0] + r * math.cos(t)) * k, centre[1] + r * math.sin(t)))
    return out + [c]


def arc_bezier(pts, k=1.0):
    """The arc as Bézier curve points (bezier.py: anchor, handle, handle, anchor, ...), at most a quarter circle per
    piece, so it's still round to a tiny fraction of a key; a straight line if the points are in a line."""
    a, b, c = [tuple(p) for p in pts[:3]]
    m = [(p[0] / k, p[1]) for p in (a, b, c)]
    got = _arc(m)
    if got is None:
        return line_bezier(a, c)
    centre, r, t0, span = got
    n = max(1, math.ceil(abs(span) / (math.pi / 2) - 1e-9))
    step = span / n
    h = 4 / 3 * math.tan(step / 4) * r  # handle length for a piece of a circle

    def on(t):
        return centre[0] + r * math.cos(t), centre[1] + r * math.sin(t)

    out = [list(a)]
    for i in range(n):
        ta, tb = t0 + step * i, t0 + step * (i + 1)
        pa, pb = on(ta), on(tb)
        h1 = (pa[0] - h * math.sin(ta), pa[1] + h * math.cos(ta))
        h2 = (pb[0] + h * math.sin(tb), pb[1] - h * math.cos(tb))
        out += [[h1[0] * k, h1[1]], [h2[0] * k, h2[1]], [pb[0] * k, pb[1]]]
    out[-1] = list(c)
    return out


def ellipse_bezier(box):
    """An ellipse filling box (x0, y0, x1, y1) as a closed Bézier curve of 4 quarters, starting at its left end
    (like custom.stroke_points' ellipses)."""
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    h = 4 / 3 * math.tan(math.pi / 8)
    out = [[cx - rx, cy]]
    for q in range(4):  # left -> top -> right -> bottom -> left
        a0, a1 = math.pi - q * math.pi / 2, math.pi / 2 - q * math.pi / 2
        p0 = (math.cos(a0), math.sin(a0))
        p1 = (math.cos(a1), math.sin(a1))
        h1 = (p0[0] + h * math.sin(a0), p0[1] - h * math.cos(a0))
        h2 = (p1[0] - h * math.sin(a1), p1[1] + h * math.cos(a1))
        out += [[cx + rx * x, cy + ry * y] for x, y in (h1, h2, p1)]
    out[-1] = list(out[0])
    return out


def line_bezier(a, c):
    return [list(a), [a[0] + (c[0] - a[0]) / 3, a[1] + (c[1] - a[1]) / 3],
            [a[0] + (c[0] - a[0]) * 2 / 3, a[1] + (c[1] - a[1]) * 2 / 3], list(c)]


def arc_k(st):
    """A shape's / stroke's k from a file (1 if it's missing or broken)."""
    try:
        k = float(st.get("k", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return k if 1e-9 < k < 1e9 and math.isfinite(k) else 1.0
