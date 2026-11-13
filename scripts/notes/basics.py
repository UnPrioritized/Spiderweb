"""Small maths shared all over the program (notes/, roll/, window/): how far a point is from a piece of line, and
"is this a real number" checks for values read from files. Imports nothing of the program's own."""

import math

INF = float("inf")


def seg_dist(px, py, ax, ay, bx, by, tiny=0.0):
    """How far (px, py) is from the piece of line a-b. A piece shorter than sqrt(tiny) counts as the point a."""
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = 0 if ll == 0 or ll < tiny else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def number(x, finite=False):
    """x is an int / float (not True / False) and not NaN; finite = also not +-inf."""
    return (not isinstance(x, bool) and isinstance(x, (int, float)) and x == x
            and not (finite and abs(x) == INF))
