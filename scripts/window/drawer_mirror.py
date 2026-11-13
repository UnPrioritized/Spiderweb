"""The drawer's Mirror maths (moved out of drawer.py): the mirror copies of a point, which points pair up, whether
strokes lie on each other."""

import math

from notes import basics


def mirror_fns(mode):
    """The ways a new stroke is mirrored: [fn(u, v) -> (u, v)] (none when off)."""
    h, v = (lambda u, w: (1 - u, w)), (lambda u, w: (u, 1 - w))
    return {"h": [h], "v": [v], "both": [h, v, lambda u, w: (1 - u, 1 - w)]}.get(mode, [])


def link_fns(link):
    """The ways a Mirror link maps a point: [fn(u, v) -> (u, v)], across the link's own middle "at" (the board's
    middle until its strokes are moved together)."""
    cu, cv = link["at"]
    h, v = (lambda u, w: (2 * cu - u, w)), (lambda u, w: (u, 2 * cv - w))
    return {"h": [h], "v": [v], "both": [h, v, lambda u, w: (2 * cu - u, 2 * cv - w)]}.get(link["ways"], [])


WAY_NAMES = {"h": ["h"], "v": ["v"], "both": ["h", "v", "hv"]}  # (mirror_fns' copies, in order)


def way_fn(way, at):
    """The mirror taking a stroke of one way to one of another (way = the letters they differ in), across at."""
    cu, cv = at
    return lambda u, w: (2 * cu - u if "h" in way else u, 2 * cv - w if "v" in way else w)


def way_apart(a, b):
    return "".join(c for c in "hv" if (c in a) != (c in b))


def point_role(st, j):
    """What point j of a stroke is, so only alike points pair up across the mirror: an end / anchor / corner (0), a
    curve's handle out (1) or in (2), an arc's middle."""
    if st["kind"] == "curve":
        return j % 3
    return "mid" if st["kind"] == "arc" and j == 1 else 0


def spot(p):
    return round(p[0], 6), round(p[1], 6)


def seg_dist(p, a, b):
    """How far point p is from the piece a-b."""
    return basics.seg_dist(p[0], p[1], a[0], a[1], b[0], b[1])


def same_stroke(a, b):
    """The two strokes lie on each other (a stroke the mirror leaves where it is: no copy)."""
    if a["kind"] != b["kind"]:
        return False
    if a["kind"] == "ellipse":
        return all(abs(p - q) < 1e-6 for p, q in zip(a["box"], b["box"]))
    pa, pb = a["pts"], b["pts"]
    if len(pa) != len(pb):
        return False

    def near(p, q):
        return math.dist(p, q) < 1e-6

    def same(side):
        return all(near(p, q) for p, q in zip(pa, side))
    if same(pb) or same(pb[::-1]):
        return True
    if len(pa) < 3 or not (near(pa[0], pa[-1]) and near(pb[0], pb[-1])):
        return False
    if a["kind"] == "arc":  # a whole circle: the same one from the other end of its line across
        return near(pa[0], pb[1]) and near(pa[1], pb[0])
    step = 3 if a["kind"] == "curve" else 1  # closed: the same outline from any of its points (a curve's anchors)
    for side in (pb, pb[::-1]):
        ring = side[:-1]
        for s in range(step, len(ring), step):
            turned = ring[s:] + ring[:s]
            if same(turned + turned[:1]):
                return True
    return False
