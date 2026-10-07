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


def rest_scales(a, dirs, seg):
    """For circles drawn from corner a with boxes a + s * dirs[n]: the s at which each rests on each line piece
    (as Targets.touch_circle: its short pieces' nearest corner on the line, the circle on its middle's side, the
    touch within the piece), NaN where it can't; and where it touches. seg: (M, 4), or (N, M, 4) = each box its
    own pieces. Shapes (N, M), (N, M, 2)."""
    dirs = np.asarray(dirs, float)
    seg = np.broadcast_to(seg, (len(dirs),) + np.shape(seg)[-2:])
    ang = 2 * np.pi * np.arange(ELLIPSE_STEPS) / ELLIPSE_STEPS
    w = dirs[:, None] / 2 + np.stack([-np.abs(dirs[:, :1]) / 2 * np.cos(ang),
                                      np.abs(dirs[:, 1:]) / 2 * np.sin(ang)], 2)  # (N, K, 2)
    p0, r = seg[..., :2], seg[..., 2:] - seg[..., :2]
    ln = np.hypot(r[..., 0], r[..., 1])
    nrm = np.stack([-r[..., 1], r[..., 0]], -1) / ln[..., None]
    g = ((a - p0) * nrm).sum(-1)  # (N, M)
    m = np.einsum("nmc,nkc->nmk", nrm, w)
    side = np.sign(g + (dirs[:, None] * nrm).sum(-1) / 2)
    kk = np.where(side >= 0, np.argmin(m, 2), np.argmax(m, 2))
    mk = np.take_along_axis(m, kk[..., None], 2)[..., 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = -g / mk
    touch = a + s[..., None] * w[np.arange(len(w))[:, None], kk]
    t = ((touch - p0) * r).sum(-1) / ln ** 2
    ok = np.isfinite(s) & (s > 0) & (t >= 0) & (t <= 1)
    return np.where(ok, s, np.nan), touch


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
        or one of its corners lies on a stroke's line with the whole circle on one side (it rests on it). Points
        first, then lines; the nearest within reach pixels."""
        k = view[0]
        a, d = np.asarray(a, float), np.asarray(d, float)
        if abs(d[0]) * k < 1 or abs(d[1]) * k < 1:
            return None
        ang = 2 * np.pi * np.arange(ELLIPSE_STEPS) / ELLIPSE_STEPS
        w = d / 2 + np.column_stack([-abs(d[0]) / 2 * np.cos(ang), abs(d[1]) / 2 * np.sin(ang)])  # (corner = a + s w)
        lo, hi = np.minimum(a, a + d) - 2 * reach / k, np.maximum(a, a + d) + 2 * reach / k  # (near it only)
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
            gap = np.where(ok, np.abs(near - far) * k, np.inf)
            i, j = np.unravel_index(np.argmin(gap), gap.shape)
            if gap[i, j] <= reach:
                return ("point", (float(pts[i, 0]), float(pts[i, 1])), float(gap[i, j]), float(near[i, 0] / far[i, j]),
                        int(pt_owner[i]))
        seg, owner = self.seg, self.owner
        if len(seg):
            keep = np.all((np.maximum(seg[:, :2], seg[:, 2:]) >= lo) & (np.minimum(seg[:, :2], seg[:, 2:]) <= hi), 1)
            seg, owner = seg[keep], owner[keep]
        if not len(seg):
            return None
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
        gap = np.abs(g + mk) * k  # (how far its nearest corner is from the line now)
        gap = np.where((s > 0) & (t >= 0) & (t <= 1) & np.isfinite(s), gap, np.inf)
        i = int(np.argmin(gap))
        if gap[i] > reach:
            return None
        return "line", (float(touch[i, 0]), float(touch[i, 1])), float(gap[i]), float(s[i]), int(owner[i])

    def touch_two(self, a, d, view, reach):
        """A circle being drawn (box from corner a; the mouse at a + d) resting on TWO strokes' lines at once (user):
        its box's width and height both change, so it's an oval as a rule. (corner (u, v) to draw the box to, pixels
        from the mouse, [(u, v) where it touches, the stroke touched] x 2) or None: the nearest within reach pixels.
        Lines near the circle only, two that aren't parallel; boxes from 1:5 to 5:1."""
        k = view[0]
        a, d = np.asarray(a, float), np.asarray(d, float)
        size = np.hypot(*d)
        if abs(d[0]) * k < 1 or abs(d[1]) * k < 1 or not len(self.seg):
            return None
        lo, hi = np.minimum(a, a + d) - 3 * reach / k, np.maximum(a, a + d) + 3 * reach / k
        seg = self.seg
        keep = np.all((np.maximum(seg[:, :2], seg[:, 2:]) >= lo) & (np.minimum(seg[:, :2], seg[:, 2:]) <= hi), 1)
        keep &= np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]) > 0
        seg, owner = seg[keep], self.owner[keep]
        if len(seg) < 2:
            return None
        sign = np.sign(d)
        ray = lambda th: size * np.column_stack([sign[0] * np.cos(th), sign[1] * np.sin(th)])
        s, _ = rest_scales(a, d[None], seg)  # (now: the lines near sticking first, at most 12)
        near = np.flatnonzero(np.isfinite(s[0]) & (np.abs(s[0] - 1) * size * k <= 4 * reach))
        near = near[np.argsort(np.abs(s[0, near] - 1))][:12]
        if len(near) < 2:
            return None
        seg, owner = seg[near], owner[near]
        th = np.linspace(np.arctan(0.2), np.arctan(5), 120)
        s, _ = rest_scales(a, ray(th), seg)
        r = seg[:, 2:] - seg[:, :2]
        r = r / np.hypot(*r.T)[:, None]
        pi, pj = np.triu_indices(len(seg), 1)
        keep = np.abs(r[pi, 0] * r[pj, 1] - r[pi, 1] * r[pj, 0]) >= 0.1  # (about parallel: no single spot)
        pi, pj = pi[keep], pj[keep]
        f = s[:, pi] - s[:, pj]  # (angle, pair)
        n, p = np.nonzero(np.sign(f[:-1]) * np.sign(f[1:]) < 0)  # (where it changes sign: a spot between)
        if not len(n):
            return None
        pi, pj, lo_t, hi_t, f_lo = pi[p], pj[p], th[n], th[n + 1], f[n, p]
        pair = np.stack([seg[pi], seg[pj]], 1)  # (each spot its own two lines)
        for _ in range(40):  # (halving, all at once: the same s for both lines, as exactly as floats go)
            mid = (lo_t + hi_t) / 2
            sm, _ = rest_scales(a, ray(mid), pair)
            fm = sm[:, 0] - sm[:, 1]
            same = np.sign(fm) == np.sign(f_lo)
            lo_t, f_lo = np.where(same, mid, lo_t), np.where(same, fm, f_lo)
            hi_t = np.where(same, hi_t, mid)
        dd = ray(lo_t)
        sm, touch = rest_scales(a, dd, pair)
        si, sj, ti, tj = sm[:, 0], sm[:, 1], touch[:, 0], touch[:, 1]
        ok = np.isfinite(si) & np.isfinite(sj) & (np.abs(si - sj) <= 1e-9 * np.maximum(si, 1))
        ok &= np.hypot(*(ti - tj).T) * k >= 3  # (not one spot where the two lines meet)
        corner = a + si[:, None] * dd
        far = np.where(ok, np.hypot(*(corner - (a + d)).T) * k, np.inf)
        b = int(np.argmin(far))
        if far[b] > reach:
            return None
        return (tuple(map(float, corner[b])), float(far[b]),
                [(tuple(map(float, ti[b])), int(owner[pi[b]])), (tuple(map(float, tj[b])), int(owner[pj[b]]))])

    def touch_line(self, line, view, reach):
        """A stroke being moved (line = its stroke_points where it is now) whose LINE sticks (user: not only its
        points), each the nearest within reach pixels: [(kind, (u, v) where it touches, pixels away, (du, dv) to move
        it by more)], kind "on" = a stroke's point comes onto its pieces, "rest" = its corner nearest a stroke's line
        comes onto it with the moved stroke on one side (the side its middle is on: it rests on the line)."""
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
        p0, r, ln, n, kk, gk, _ = self.rests(line, lo, hi)
        if len(p0):
            touch = line[kk] - gk[:, None] * n
            t = ((touch - p0) * r).sum(1) / ln ** 2
            far = np.where((t >= 0) & (t <= 1), np.abs(gk) * k, np.inf)
            i = int(np.argmin(far))
            if far[i] <= reach:
                out.append(("rest", (float(touch[i, 0]), float(touch[i, 1])), float(far[i]),
                            (float(-gk[i] * n[i, 0]), float(-gk[i] * n[i, 1]))))
        return out

    def rests(self, line, lo, hi):
        """For a stroke being moved (line as in touch_line), each line piece in the box lo..hi: the piece (p0, r,
        its length, normal n), the moved stroke's corner nearest it with the stroke on the side its middle is on (kk),
        how far that corner is along n (gk: moving it by -gk n rests it on the line) and the piece's stroke."""
        seg, owner = self.seg, self.owner
        if len(seg):
            keep = np.all((np.maximum(seg[:, :2], seg[:, 2:]) >= lo) & (np.minimum(seg[:, :2], seg[:, 2:]) <= hi), 1)
            keep &= np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]) > 0
            seg, owner = seg[keep], owner[keep]
        p0, r = seg[:, :2], seg[:, 2:] - seg[:, :2]
        ln = np.hypot(r[:, 0], r[:, 1])
        n = np.column_stack([-r[:, 1], r[:, 0]]) / np.where(ln == 0, 1, ln)[:, None]
        g = np.einsum("mkc,mc->mk", line[None] - p0[:, None], n)  # (each corner's side of each line, how far)
        side = np.sign((((line.min(0) + line.max(0)) / 2 - p0) * n).sum(1))  # (its middle's side of each line)
        kk = np.where(side >= 0, np.argmin(g, 1), np.argmax(g, 1)) if len(g) else np.zeros(0, int)
        gk = g[np.arange(len(g)), kk]
        return p0, r, ln, n, kk, gk, owner

    def rest_two(self, line, view, reach):
        """A stroke being moved (line as in touch_line) resting on TWO lines at once (user: between two slanted
        lines it jumped from resting on one to resting on the other): ((du, dv) to move it by more, pixels, [(u, v)
        where it touches] x 2) or None; the smallest move within reach pixels. Two pieces not about parallel (of one
        stroke: 30 degrees apart or more, so a curve's next pieces don't count), touching 3 pixels apart or more."""
        k = view[0]
        line = np.asarray(line, float).reshape(-1, 2)
        if len(line) < 2:
            return None
        lo, hi = line.min(0) - 2 * reach / k, line.max(0) + 2 * reach / k
        p0, r, ln, n, kk, gk, owner = self.rests(line, lo, hi)
        near = np.flatnonzero(np.abs(gk) * k <= reach)  # (the move along n is gk: never more than the whole move)
        near = near[np.argsort(np.abs(gk[near]))][:12]
        if len(near) < 2:
            return None
        p0, r, ln, n, kk, gk, owner = (x[near] for x in (p0, r, ln, n, kk, gk, owner))
        i, j = np.triu_indices(len(near), 1)
        det = n[i, 0] * n[j, 1] - n[i, 1] * n[j, 0]
        ok = np.abs(det) >= np.where(owner[i] == owner[j], 0.5, 0.1)
        i, j, det = i[ok], j[ok], det[ok]
        if not len(i):
            return None
        bi, bj = -gk[i], -gk[j]  # (the move: D . n_i = -gk_i and D . n_j = -gk_j)
        d = np.column_stack([(bi * n[j, 1] - bj * n[i, 1]) / det, (bj * n[i, 0] - bi * n[j, 0]) / det])
        ti, tj = line[kk[i]] + d, line[kk[j]] + d
        a = ((ti - p0[i]) * r[i]).sum(1) / ln[i] ** 2
        b = ((tj - p0[j]) * r[j]).sum(1) / ln[j] ** 2
        far = np.hypot(d[:, 0], d[:, 1]) * k
        ok = (a >= 0) & (a <= 1) & (b >= 0) & (b <= 1) & (np.hypot(*(ti - tj).T) * k >= 3) & (far <= reach)
        if not ok.any():
            return None
        m = int(np.argmin(np.where(ok, far, np.inf)))
        return ((float(d[m, 0]), float(d[m, 1])), float(far[m]),
                [(float(ti[m, 0]), float(ti[m, 1])), (float(tj[m, 0]), float(tj[m, 1]))])

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
