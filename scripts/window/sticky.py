"""Sticky lines in the drawer: a point being drawn or dragged sticks to another stroke's point, to where two strokes
cross, or anywhere along a stroke's line (in that order, the nearest within REACH pixels). Always on; Shift = free
(Drawer.event_pt). Sticking, not glue: moving that line later leaves the point where it is.

The lines are the strokes' short straight pieces Fill / Spam see (custom.stroke_points), not the perfect curve, and
stuck points aren't rounded: a point stuck to a line is ON it exactly, so it counts as touching (no red dot, no
closing line) in the drawer and on the placed shape at any size."""

import numpy as np

from notes.custom import CURVE_STEPS, ELLIPSE_STEPS, stroke_points
from notes.pattern import has_formula

REACH = 10  # pixels (times the window's scale)
MOST_NEAR = 300  # crossings are looked for among at most this many pieces near the mouse (nearest first)


def key_points(st, line):
    """The points a stroke offers to stick to, each (its point number to leave out while it's dragged, or None;
    (u, v)): its ends, a polyline's corners, a curve's anchors, an ellipse's left / right / top / bottom. All of
    them are points of line (= stroke_points(st)) exactly."""
    if not len(line):
        return []
    if st["kind"] == "ellipse":
        return [(None, tuple(line[i * ELLIPSE_STEPS // 4])) for i in range(4)]
    if has_formula(st) or st.get("free") or st.get("smooth"):  # (only the ends are sure to be where they look)
        return [(0, tuple(line[0])), (len(st["pts"]) - 1, tuple(line[-1]))]
    if st["kind"] == "curve":
        return [(j, tuple(line[j // 3 * CURVE_STEPS])) for j in range(0, len(st["pts"]), 3)]
    if st["kind"] == "arc":  # (its middle point is on the perfect circle, a hair off the short pieces)
        return [(0, tuple(line[0])), (2, tuple(line[-1]))]
    return [(j, tuple(p)) for j, p in enumerate(line)]


class Targets:
    """What can be stuck to in a drawing: strokes skip left out (the ones being changed), and for skip_pts
    {(stroke, point number)} of polylines only that point and the two pieces beside it."""

    def __init__(self, strokes, skip=frozenset(), skip_pts=frozenset()):
        pts, segs, owner, index = [], [], [], []
        for i, st in enumerate(strokes):
            if i in skip:
                continue
            line = np.asarray(stroke_points(st), float).reshape(-1, 2)
            gone = {j for a, j in skip_pts if a == i}
            pts += [p for j, p in key_points(st, line) if j is None or j not in gone]
            if len(line) > 1:
                keep = np.ones(len(line) - 1, bool)
                for j in gone:
                    keep[max(j - 1, 0):j + 1] = False
                segs.append(np.column_stack([line[:-1], line[1:]])[keep])
                owner.append(np.full(int(keep.sum()), i))
                index.append(np.flatnonzero(keep))
        self.pts = np.asarray(pts, float).reshape(-1, 2)
        self.seg = np.concatenate(segs) if segs else np.zeros((0, 4))
        self.owner = np.concatenate(owner) if owner else np.zeros(0, int)
        self.index = np.concatenate(index) if index else np.zeros(0, int)

    def find(self, x, y, view, reach, extra_pts=(), extra_lines=()):
        """Where screen spot (x, y) sticks: (kind "point" / "cross" / "line", (u, v), pixels away) or None.
        view = (k, ox, oy): on screen x = ox + u * k, y = oy - v * k. extra_pts / extra_lines: the stroke being
        drawn's own (polylines as point lists)."""
        k, ox, oy = view
        pts = np.concatenate([self.pts, np.asarray(extra_pts, float).reshape(-1, 2)])
        if len(pts):
            d = np.hypot(ox + pts[:, 0] * k - x, oy - pts[:, 1] * k - y)
            i = int(np.argmin(d))
            if d[i] <= reach:
                return "point", (float(pts[i, 0]), float(pts[i, 1])), float(d[i])
        seg, owner, index = [self.seg], [self.owner], [self.index]
        for n, line in enumerate(extra_lines):
            line = np.asarray(line, float).reshape(-1, 2)
            if len(line) > 1:
                seg.append(np.column_stack([line[:-1], line[1:]]))
                owner.append(np.full(len(line) - 1, -1 - n))
                index.append(np.arange(len(line) - 1))
        seg, owner, index = np.concatenate(seg), np.concatenate(owner), np.concatenate(index)
        if not len(seg):
            return None
        ax, ay = ox + seg[:, 0] * k, oy - seg[:, 1] * k
        dx, dy = (seg[:, 2] - seg[:, 0]) * k, -(seg[:, 3] - seg[:, 1]) * k
        ll = dx * dx + dy * dy
        t = np.clip(((x - ax) * dx + (y - ay) * dy) / np.where(ll == 0, 1, ll), 0, 1)
        dist = np.hypot(ax + t * dx - x, ay + t * dy - y)
        near = np.flatnonzero(dist <= reach)
        if not len(near):
            return None
        near = near[np.argsort(dist[near])][:MOST_NEAR]
        got = self.crossing(seg[near], owner[near], index[near], x, y, view, reach)
        if got:
            return got
        i = near[0]
        a, b = seg[i, :2], seg[i, 2:]
        u, v = a + t[i] * (b - a)  # (the same share along as on screen: the board's scale is the same both ways)
        return "line", (float(u), float(v)), float(dist[i])

    def touch_circle(self, a, d, view, reach):
        """A circle being drawn (its box from corner a to a + d, u / v) whose LINE sticks (user: not the box's
        corner): (kind "point" / "line", (u, v) where it touches, pixels away, s) or None; the box from a to
        a + s * d then touches exactly: its short pieces (as stroke_points makes them) pass through a stroke's point,
        or one of its corners lies on a stroke's line with the whole circle on one side (it rests on it). Points
        first, then lines; the nearest within reach pixels."""
        k = view[0]
        a, d = np.asarray(a, float), np.asarray(d, float)
        if abs(d[0]) * k < 1 or abs(d[1]) * k < 1:
            return None
        ang = 2 * np.pi * np.arange(ELLIPSE_STEPS) / ELLIPSE_STEPS
        w = d / 2 + np.column_stack([-abs(d[0]) / 2 * np.cos(ang), abs(d[1]) / 2 * np.sin(ang)])  # (corner = a + s w)
        lo, hi = np.minimum(a, a + d) - 2 * reach / k, np.maximum(a, a + d) + 2 * reach / k  # (near it only)
        pts = self.pts[np.all((self.pts >= lo) & (self.pts <= hi), axis=1)] if len(self.pts) else self.pts
        if len(pts):  # a point on the ray from a: on the circle where the ray crosses its pieces, scaled to reach it
            q = pts - a
            e = np.roll(w, -1, axis=0) - w
            cw = q[:, :1] * w[None, :, 1] - q[:, 1:] * w[None, :, 0]
            ce = q[:, :1] * e[None, :, 1] - q[:, 1:] * e[None, :, 0]
            t = -cw / np.where(ce == 0, np.nan, ce)
            hit = w[None] + t[..., None] * e[None]
            ok = (t >= 0) & (t <= 1) & ((hit * q[:, None]).sum(2) > 0)
            far = np.hypot(*hit.transpose(2, 0, 1))
            near = np.hypot(*q.T)[:, None]
            gap = np.where(ok, np.abs(near - far) * k, np.inf)
            i, j = np.unravel_index(np.argmin(gap), gap.shape)
            if gap[i, j] <= reach:
                return "point", (float(pts[i, 0]), float(pts[i, 1])), float(gap[i, j]), float(near[i, 0] / far[i, j])
        seg = self.seg
        if len(seg):
            keep = np.all((np.maximum(seg[:, :2], seg[:, 2:]) >= lo) & (np.minimum(seg[:, :2], seg[:, 2:]) <= hi), 1)
            seg = seg[keep]
        if not len(seg):
            return None
        p0, r = seg[:, :2], seg[:, 2:] - seg[:, :2]
        ln = np.hypot(*r.T)
        seg, p0, r, ln = seg[ln > 0], p0[ln > 0], r[ln > 0], ln[ln > 0]
        n = np.column_stack([-r[:, 1], r[:, 0]]) / ln[:, None]
        g = ((a - p0) * n).sum(1)  # (the corner's side of each line, and how far)
        m = n @ w.T
        side = np.sign(g + n @ (d / 2))  # (the circle rests on the side its middle is on)
        kk = np.where(side >= 0, np.argmin(m, 1), np.argmax(m, 1))
        mk = m[np.arange(len(m)), kk]
        s = -g / np.where(mk == 0, np.nan, mk)
        touch = a + s[:, None] * w[kk]
        t = ((touch - p0) * r).sum(1) / ln ** 2
        gap = np.abs(g + mk) * k  # (how far its nearest corner is from the line now)
        gap = np.where((s > 0) & (t >= 0) & (t <= 1) & np.isfinite(s), gap, np.inf)
        i = int(np.argmin(gap))
        if gap[i] > reach:
            return None
        return "line", (float(touch[i, 0]), float(touch[i, 1])), float(gap[i]), float(s[i])

    @staticmethod
    def crossing(seg, owner, index, x, y, view, reach):
        """The nearest spot within reach where two of these pieces cross (pieces of different strokes, or of one
        stroke crossing itself, not two pieces next to each other)."""
        if len(seg) < 2:
            return None
        k, ox, oy = view
        i, j = np.triu_indices(len(seg), 1)
        ok = (owner[i] != owner[j]) | (np.abs(index[i] - index[j]) > 1)
        i, j = i[ok], j[ok]
        a, r = seg[i, :2], seg[i, 2:] - seg[i, :2]
        c, s = seg[j, :2], seg[j, 2:] - seg[j, :2]
        den = r[:, 0] * s[:, 1] - r[:, 1] * s[:, 0]
        q = c - a
        safe = np.where(den == 0, 1, den)
        t = (q[:, 0] * s[:, 1] - q[:, 1] * s[:, 0]) / safe
        w = (q[:, 0] * r[:, 1] - q[:, 1] * r[:, 0]) / safe
        hit = (den != 0) & (t >= 0) & (t <= 1) & (w >= 0) & (w <= 1)
        if not hit.any():
            return None
        p = a[hit] + t[hit, None] * r[hit]
        d = np.hypot(ox + p[:, 0] * k - x, oy - p[:, 1] * k - y)
        m = int(np.argmin(d))
        if d[m] > reach:
            return None
        return "cross", (float(p[m, 0]), float(p[m, 1])), float(d[m])
