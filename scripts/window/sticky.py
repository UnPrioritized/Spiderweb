"""Sticky lines in the drawer: a point being drawn or dragged sticks to another stroke's point, to where two strokes
cross, or anywhere along a stroke's line (in that order, the nearest within REACH pixels). Always on; Shift = free
(Drawer.event_pt). Sticking, not glue: moving that line later leaves the point where it is.

The lines are the strokes' short straight pieces Fill / Spam see (custom.stroke_points), not the perfect curve, and
stuck points aren't rounded: a point stuck to a line is ON it exactly, so it counts as touching (no red dot, no
closing line) in the drawer and on the placed shape at any size."""

import copy

import numpy as np

from notes.custom import CURVE_STEPS, ELLIPSE_STEPS, stroke_points
from notes.pattern import has_formula

REACH = 10  # pixels (times the window's scale)
MOST_NEAR = 300  # crossings are looked for among at most this many pieces near the mouse (nearest first)
BEND = np.cos(np.radians(20))  # a stroke turning more than this between two pieces = a corner (rest_on)
PAST = 1  # pixels: a shape resting on a curve may touch this far past the end of one of its short pieces (where
# the curve bulges toward it, a corner of the curve touches the shape: never exactly on one piece)


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


def hull_idx(pts):
    """The numbers of pts on their outline's convex hull (only those can be a shape's point nearest a line)."""
    p = [tuple(q) for q in np.asarray(pts, float).reshape(-1, 2).tolist()]
    order = sorted(range(len(p)), key=lambda i: p[i])

    def half(seq):
        h = []
        for i in seq:
            while len(h) >= 2:
                (ax, ay), (bx, by), (cx, cy) = p[h[-2]], p[h[-1]], p[i]
                if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0:
                    break
                h.pop()
            h.append(i)
        return h

    lower, upper = half(order), half(order[::-1])
    return np.asarray(lower[:-1] + upper[:-1] or order[:1], int)  # (counter-clockwise)


def furthest(H, m):
    """For a convex outline H ((K, 2), counter-clockwise, as hull_idx) and directions m (M, 2): the number of its
    point furthest along each (found by its sides' angles: quick for big K)."""
    if len(H) <= 8:
        return np.argmax(m @ H.T, 1)
    e = np.roll(H, -1, 0) - H
    ea = np.unwrap(np.arctan2(e[:, 1], e[:, 0]))
    want = np.arctan2(m[:, 1], m[:, 0]) + np.pi / 2  # (the side turning past this angle: its start is furthest)
    i = np.searchsorted(ea, ea[0] + (want - ea[0]) % (2 * np.pi))
    near = (i[:, None] + np.arange(-2, 3)) % len(H)  # (and the next ones, against rounding)
    return near[np.arange(len(m)), np.argmax((H[near] * m[:, None]).sum(2), 1)]


class Targets:
    """What can be stuck to in a drawing: strokes skip left out (the ones being changed), and for skip_pts
    {(stroke, point number)} of polylines only that point and the two pieces beside it."""

    def __init__(self, strokes, skip=frozenset(), skip_pts=frozenset()):
        pts, pt_owner, segs, owner, index = [], [], [], [], []
        for i, st in enumerate(strokes):
            if i in skip:
                continue
            line = np.asarray(stroke_points(st), float).reshape(-1, 2)
            gone = {j for a, j in skip_pts if a == i}
            mine = [p for j, p in key_points(st, line) if j is None or j not in gone]
            pts += mine
            pt_owner += [i] * len(mine)
            if len(line) > 1:
                keep = np.ones(len(line) - 1, bool)
                for j in gone:
                    keep[max(j - 1, 0):j + 1] = False
                segs.append(np.column_stack([line[:-1], line[1:]])[keep])
                owner.append(np.full(int(keep.sum()), i))
                index.append(np.flatnonzero(keep))
        self.pts = np.asarray(pts, float).reshape(-1, 2)
        self.pt_owner = np.asarray(pt_owner, int)
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
        corner): (kind "point" / "line", (u, v) where it touches, pixels away, s, the stroke touched) or None; the
        box from a to a + s * d then touches exactly: its short pieces (as stroke_points makes them) pass through a stroke's point,
        or one of its corners lies on a stroke's line with the whole circle on one side (it rests on it). The
        nearest within reach pixels, counted as how far the mouse's corner would move (a + d to a + s * d): a line
        along the circle's side near a needs it to grow a lot for a small gap. Never a box under 1 pixel (a line
        through a: the circle can't grow onto it)."""
        k = view[0]
        a, d = np.asarray(a, float), np.asarray(d, float)
        if abs(d[0]) * k < 1 or abs(d[1]) * k < 1:
            return None
        ang = 2 * np.pi * np.arange(ELLIPSE_STEPS) / ELLIPSE_STEPS
        w = d / 2 + np.column_stack([-abs(d[0]) / 2 * np.cos(ang), abs(d[1]) / 2 * np.sin(ang)])  # (corner = a + s w)
        lo, hi = np.minimum(a, a + d) - 2 * reach / k, np.maximum(a, a + d) + 2 * reach / k  # (near it only)
        size = np.hypot(*d) * k  # (the mouse's corner moves this many pixels per 1 of s)
        small = 1 / (min(abs(d[0]), abs(d[1])) * k)  # (s below this: a box under 1 pixel)
        best = None
        inside = np.all((self.pts >= lo) & (self.pts <= hi), axis=1) if len(self.pts) else np.zeros(0, bool)
        pts, pt_owner = self.pts[inside], self.pt_owner[inside]
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
            s = near / np.where(far == 0, np.nan, far)
            gap = np.where(ok & (s >= small), np.abs(s - 1) * size, np.inf)
            i, j = np.unravel_index(np.argmin(gap), gap.shape)
            if gap[i, j] <= reach:
                best = ("point", (float(pts[i, 0]), float(pts[i, 1])), float(gap[i, j]), float(s[i, j]),
                        int(pt_owner[i]))
        seg, owner = self.seg, self.owner
        if len(seg):
            keep = np.all((np.maximum(seg[:, :2], seg[:, 2:]) >= lo) & (np.minimum(seg[:, :2], seg[:, 2:]) <= hi), 1)
            seg, owner = seg[keep], owner[keep]
        if not len(seg):
            return best
        p0, r = seg[:, :2], seg[:, 2:] - seg[:, :2]
        ln = np.hypot(*r.T)
        seg, owner, p0, r, ln = seg[ln > 0], owner[ln > 0], p0[ln > 0], r[ln > 0], ln[ln > 0]
        n = np.column_stack([-r[:, 1], r[:, 0]]) / ln[:, None]
        g = ((a - p0) * n).sum(1)  # (the corner's side of each line, and how far)
        m = n @ w.T
        side = np.sign(g + n @ (d / 2))  # (the circle rests on the side its middle is on)
        kk = np.where(side >= 0, np.argmin(m, 1), np.argmax(m, 1))
        mk = m[np.arange(len(m)), kk]
        s = -g / np.where(mk == 0, np.nan, mk)
        touch = a + s[:, None] * w[kk]
        t = ((touch - p0) * r).sum(1) / ln ** 2
        past = PAST / (k * ln)
        gap = np.where((s >= small) & (t >= -past) & (t <= 1 + past) & np.isfinite(s), np.abs(s - 1) * size, np.inf)
        i = int(np.argmin(gap)) if len(gap) else 0
        if not len(gap) or gap[i] > reach or (best and best[2] <= gap[i]):
            return best
        return "line", (float(touch[i, 0]), float(touch[i, 1])), float(gap[i]), float(s[i]), int(owner[i])

    def with_lines(self, lines):
        """A copy with more lines to stick to (point lists, e.g. the mirror lines; owner -1, -2...: no stroke)."""
        if not lines:
            return self
        out = copy.copy(self)
        seg, owner, index = [self.seg], [self.owner], [self.index]
        for n, line in enumerate(lines):
            line = np.asarray(line, float).reshape(-1, 2)
            if len(line) > 1:
                seg.append(np.column_stack([line[:-1], line[1:]]))
                owner.append(np.full(len(line) - 1, -1 - n))
                index.append(np.arange(len(line) - 1))
        out.seg, out.owner, out.index = np.concatenate(seg), np.concatenate(owner), np.concatenate(index)
        return out

    def touch_two(self, a, d, view, reach):
        """A circle being drawn (box from corner a; the mouse at a + d) resting on TWO strokes' lines at once (user):
        its box's width and height both change, so it's an oval as a rule. (corner (u, v) to draw the box to, pixels
        from the mouse, [(u, v) where it touches, the stroke touched] x 2) or None: the nearest within reach pixels.
        Curves count too (rest_on); boxes from 1:5 to 5:1."""
        k = view[0]
        a, d = np.asarray(a, float), np.asarray(d, float)
        if abs(d[0]) * k < 1 or abs(d[1]) * k < 1:
            return None
        b = a + d
        C = np.asarray(stroke_points({"kind": "ellipse", "box": [*np.minimum(a, b), *np.maximum(a, b)]}), float)

        def valid(D):
            nd = d + D
            return bool(np.all(np.sign(nd) == np.sign(d)) and np.all(np.abs(nd) * k >= 1)
                        and 0.2 <= abs(nd[1] / nd[0]) <= 5)

        got = self.rest_on(C, (C - a) / d, view, reach, valid, two=True)  # (its points: a + their share of the box)
        if not got:
            return None
        D, far, touch, _, owner = got
        return (float(b[0] + D[0]), float(b[1] + D[1])), far, [(touch[0], owner[0]), (touch[1], owner[1])]

    def touch_line(self, line, view, reach):
        """A stroke being moved (line = its stroke_points where it is now) whose LINE sticks (user: not only its
        points), each the nearest within reach pixels: [(kind, (u, v) where it touches, pixels away, (du, dv) to move
        it by more)], kind "on" = a stroke's point comes onto its pieces, "rest" = it rests against a stroke's line
        (rest_on: its corner on it with the moved stroke on its middle's side, or a closed one's side on a point)."""
        k = view[0]
        line = np.asarray(line, float).reshape(-1, 2)
        if len(line) < 2:
            return []
        out = []
        lo, hi = line.min(0) - 2 * reach / k, line.max(0) + 2 * reach / k  # (near it only)
        pts = self.pts[np.all((self.pts >= lo) & (self.pts <= hi), 1)] if len(self.pts) else self.pts
        if len(pts):
            a, r = line[:-1], line[1:] - line[:-1]
            ll = (r * r).sum(1)
            q = pts[:, None] - a[None]  # (point, piece, 2)
            t = np.clip((q * r[None]).sum(2) / np.where(ll == 0, 1, ll), 0, 1)
            gap = q - t[..., None] * r[None]  # (from the nearest spot on each piece to the point)
            far = np.hypot(gap[..., 0], gap[..., 1]) * k
            i, j = np.unravel_index(np.argmin(far), far.shape)
            if far[i, j] <= reach:
                out.append(("on", (float(pts[i, 0]), float(pts[i, 1])), float(far[i, j]),
                            (float(gap[i, j, 0]), float(gap[i, j, 1]))))
        got = self.rest_on(line, np.ones_like(line), view, reach)
        if got:
            out.append(("rest", got[2][0], got[1], got[0]))
        return out

    def rest_two(self, line, view, reach):
        """A stroke being moved (line as in touch_line) resting on TWO lines at once (user: between two slanted
        lines it jumped from resting on one to resting on the other): ((du, dv) to move it by more, pixels, [(u, v)
        where it touches] x 2) or None: the smallest move within reach pixels (rest_on)."""
        line = np.asarray(line, float).reshape(-1, 2)
        got = self.rest_on(line, np.ones_like(line), view, reach, two=True)
        return got and got[:3]

    def rest_box(self, a, b, view, reach):
        """A square being drawn (corner a pressed, b at the mouse; its sides straight across / up) resting against a
        stroke's line (user), as a moved stroke rests (rest_on): [(kind "rest" / "two", (du, dv) to move b by,
        pixels, [(u, v) where it touches], [its sides touching or beside a touching corner: 0 = a to (b's u, a's v),
        1, 2, 3 = back to a])], each the nearest: one line within reach pixels, two within half of it. The sides
        through a can't move, so a line through a never counts."""
        k = view[0]
        a, b = np.asarray(a, float), np.asarray(b, float)
        if abs(b[0] - a[0]) * k < 1 or abs(b[1] - a[1]) * k < 1:
            return []
        box = np.array([a, [b[0], a[1]], b, [a[0], b[1]]])
        free = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)  # (which of b's u / v moves each corner)

        def valid(d):  # (the box not turned over or flat)
            nb = b + np.asarray(d)
            return bool(np.all(np.sign(nb - a) == np.sign(b - a)) and np.all(np.abs(nb - a) * k >= 1))

        out = []
        for kind, most in (("rest", reach), ("two", reach / 2)):
            got = self.rest_on(box, free, view, most, valid, two=kind == "two", closed=True)
            if got:
                d, far, touch, where, _ = got
                sides = set()
                for w in where:
                    sides |= {(w[1] - 1) % 4, w[1]} if w[0] == "corner" else {w[1] if (w[2] - w[1]) % 4 == 1 else w[2]}
                out.append((kind, d, far, touch, sorted(sides)))
        return out

    def rest_on(self, C, F, view, reach, valid=None, two=False, closed=None):
        """A shape resting against strokes' lines (user), as it changes with one moving point (D = (du, dv) from
        now): its points C + F * D (C, F: (K, 2); a moved stroke F = 1, a square or circle being drawn each point's
        share of its box). It touches without crossing: by its corner on a stroke's piece (the shape on its middle's
        side), or (closed shapes: closed=None = when C ends where it starts) by its side on a stroke's point (a curve
        bulging toward it). Curves count (user): from each spot along a stroke where it nearly rests, it's followed
        to where it does (Newton's way, a few steps). two: on two lines at once (not about parallel, touching 3
        pixels apart or more). (D, pixels, [(u, v) where it touches], [("corner", point number) / ("side", point
        numbers at its ends)], [strokes]) or None: the smallest D within reach pixels; valid(D): more checks."""
        k = view[0]
        C, F = np.asarray(C, float).reshape(-1, 2), np.asarray(F, float).reshape(-1, 2)
        if closed is None:
            closed = len(C) > 3 and np.array_equal(C[0], C[-1])
            if closed:
                C, F = C[:-1], F[:-1]
        if len(C) < 2 or not len(self.seg):
            return None
        h = hull_idx(C)  # (only its outline can touch from outside; moving with F keeps it the outline)
        if len(h) < 2:
            return None
        H, Fh = C[h], F[h]
        step = np.abs(np.roll(h, -1) - h)
        real = ((step == 1) | (step == len(C) - 1)) if closed else None  # (outline sides that are its own lines)
        lo, hi = H.min(0) - 2 * reach / k, H.max(0) + 2 * reach / k
        seg = self.seg
        keep = np.all((np.maximum(seg[:, :2], seg[:, 2:]) >= lo) & (np.minimum(seg[:, :2], seg[:, 2:]) <= hi), 1)
        keep &= np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]) > 0
        idx = np.flatnonzero(keep)
        if not len(idx):
            return None
        r = seg[idx, 2:] - seg[idx, :2]
        ln = np.hypot(r[:, 0], r[:, 1])
        bend = (r[:-1] * r[1:]).sum(1) < BEND * ln[:-1] * ln[1:]
        cut = np.flatnonzero((np.diff(idx) != 1) | (np.diff(self.owner[idx]) != 0) | (np.diff(self.index[idx]) != 1)
                            | bend)
        starts, ends = np.concatenate([[0], cut + 1]), np.concatenate([cut + 1, [len(idx)]])
        run = np.repeat(np.arange(len(starts)), ends - starts)  # (each stretch: one stroke's pieces one after another,
        arc = np.cumsum(ln)  # up to a sharp corner: a polyline's each piece, a curve's anchor with handles not in line)
        span = 4 * reach / k

        def near(c):  # (the pieces along its stroke within span of piece c)
            r0, r1 = starts[run[c]], ends[run[c]]
            return np.arange(r0 + np.searchsorted(arc[r0:r1], arc[c] - span),
                             r0 + np.searchsorted(arc[r0:r1], arc[c] + span, "right"))

        sep = self.contacts(idx, H, Fh, real, k)[0]
        seeds = []  # (each dip in how near it is along a stroke: where it nearly rests)
        for r0, r1 in zip(starts, ends):
            s = sep[r0:r1]
            o = np.concatenate([[np.inf], s, [np.inf]])
            dip = np.flatnonzero(np.isfinite(s) & (o[1:-1] <= o[:-2]) & (o[1:-1] < o[2:]))
            seeds += [r0 + j for j in dip if abs(s[j]) * k <= 1.5 * reach]
        seeds = sorted(seeds, key=lambda c: abs(sep[c]))[:6]
        tries = [(x, y) for i, x in enumerate(seeds) for y in seeds[i + 1:]] if two else [(x,) for x in seeds]
        best = None
        for start in tries:
            got = self.follow(idx, near, start, H, Fh, real, k, reach)
            if got and (best is None or got[1] < best[1]) and (valid is None or valid(got[0])):
                best = got
        if best is None:
            return None
        D, far, touch, where, owner = best
        where = [("corner", int(h[w])) if w >= 0 else ("side", int(h[-1 - w]), int(h[-w % len(h)])) for w in where]
        return D, far, touch, where, owner

    def contacts(self, P, H, F, real, k):
        """For pieces P (numbers in self.seg) and a shape's convex outline H (counter-clockwise, F as in rest_on;
        real: which of its sides H[m] -> H[m + 1] are its own lines, None = none): how near the shape is to each
        piece (sep, u units; below 0 = crossing; inf = not touching it within the piece / side), A (moving by D
        changes sep by about A . D), where they'd touch and which part of the shape (its corner number, or for a
        side -1 - its first corner). The corner nearest the piece's line touches it, or the piece's start point
        touches a side, whichever is nearer."""
        seg = self.seg[P]
        p0, r = seg[:, :2], seg[:, 2:] - seg[:, :2]
        ln = np.hypot(r[:, 0], r[:, 1])
        n = np.column_stack([-r[:, 1], r[:, 0]]) / ln[:, None]
        n *= np.where((((H.min(0) + H.max(0)) / 2 - p0) * n).sum(1) >= 0, 1.0, -1.0)[:, None]  # (toward its middle)
        kk = furthest(H, -n)
        sep = ((H[kk] - p0) * n).sum(1)
        A = n * F[kk]
        aa = (A * A).sum(1)
        touch = H[kk] - (sep / np.where(aa == 0, np.inf, aa))[:, None] * A * F[kk]  # (after the smallest move)
        t, past = ((touch - p0) * r).sum(1) / ln, PAST / k
        sep = np.where((aa > 0) & (t >= -past) & (t <= ln + past), sep, np.inf)
        where = kk.copy()
        if real is not None:  # (a closed shape: the piece's start on one of its sides near that corner)
            K, rows = len(H), np.arange(len(P))
            e = np.roll(H, -1, 0) - H
            el = np.hypot(e[:, 0], e[:, 1])
            nu = np.column_stack([e[:, 1], -e[:, 0]]) / np.where(el == 0, 1, el)[:, None]  # (outward)
            m = (kk[:, None] + np.arange(-3, 3)) % K
            dm = ((p0[:, None] - H[m]) * nu[m]).sum(2)
            m = m[rows, np.argmax(dm, 1)]
            sb = ((p0 - H[m]) * nu[m]).sum(1)
            tau = ((p0 - H[m]) * e[m]).sum(1) / np.where(el[m] == 0, np.inf, el[m] ** 2)
            slack = past / np.where(el[m] == 0, np.inf, el[m])
            fp = F[m] * (1 - tau)[:, None] + F[(m + 1) % K] * tau[:, None]  # (how its spot on the side moves)
            ab = -nu[m] * fp
            on = real[m] & (el[m] > 0) & (tau >= -slack) & (tau <= 1 + slack) & (sb < sep) & ((ab * ab).sum(1) > 0)
            sep = np.where(on, sb, sep)
            A = np.where(on[:, None], ab, A)
            touch = np.where(on[:, None], p0, touch)
            where = np.where(on, -1 - m, where)
        return sep, A, touch, where

    def follow(self, idx, near, start, H, F, real, k, reach):
        """rest_on from pieces idx[start] (one or two): each step, along each one's stroke the dip in how near the
        shape is (nearest the last one), then the move making it touch (both at once: their two lines); until it
        stays, touching exactly and crossing nothing near. (D, pixels, [touch], [part of the shape], [strokes])."""
        D, cur = np.zeros(2), list(start)
        for _ in range(30):
            Hs, pick, rows = H + F * D, [], []
            for c in cur:
                w = near(c)
                sep, A, _, _ = self.contacts(idx[w], Hs, F, real, k)
                o = np.concatenate([[np.inf], sep, [np.inf]])
                dip = np.flatnonzero(np.isfinite(sep) & (o[1:-1] <= o[:-2]) & (o[1:-1] <= o[2:]))
                if not len(dip):
                    return None
                b = dip[np.argmin(np.abs(w[dip] - c))]
                pick.append(int(w[b]))
                rows.append((sep[b], A[b]))
            if len(rows) == 1:
                (g, ai), = rows
                step = -g * ai / (ai @ ai)
            else:
                (gi, ai), (gj, aj) = rows
                det = ai[0] * aj[1] - ai[1] * aj[0]
                if abs(det) < 0.1 * np.hypot(*ai) * np.hypot(*aj):  # (about parallel: no single spot)
                    return None
                step = np.array([(-gi * aj[1] + gj * ai[1]) / det, (-gj * ai[0] + gi * aj[0]) / det])
            D = D + step
            if np.hypot(*D) * k > 3 * reach:
                return None
            if pick == cur and np.hypot(*step) * k < 1e-7:
                break
            cur = pick
        else:
            return None
        Hs, touch, where = H + F * D, [], []
        for c in cur:
            w = near(c)
            sep, _, tp, wh = self.contacts(idx[w], Hs, F, real, k)
            b = int(np.flatnonzero(w == c)[0])
            if abs(sep[b]) * k > 1e-6 or sep[np.isfinite(sep)].min() * k < -1e-6:
                return None
            touch.append((float(tp[b, 0]), float(tp[b, 1])))
            where.append(int(wh[b]))
        far = float(np.hypot(*D) * k)
        if far > reach or (len(cur) == 2 and np.hypot(touch[0][0] - touch[1][0], touch[0][1] - touch[1][1]) * k < 3):
            return None
        return (float(D[0]), float(D[1])), far, touch, where, [int(self.owner[idx[c]]) for c in cur]

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
