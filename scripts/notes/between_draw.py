"""Add between for drawn shapes (custom shapes, and a drawn shape with a line / freehand at the other end): each
piece (stroke) of one drawing turns into the nearest piece of the other.

Pieces are matched once (DrawPair), in the shapes' view (nested.view_of: beats along time, keys scaled the way the
shape was drawn, so a circle stays round): the nearest pairs first by where they are + how big (like Nested's "Morph
into it"); ANY piece can go with any other (user 2026-11-25: a line opens up into a closed area and back); a piece
with no partner shrinks to a dot at its own middle (or grows from one). Each pair is spread to the same number of
points, lined up so they travel the least, and every step moves them part of the way.

A step is a custom shape: its box (three frame points) goes from the first shape's to the last's, its strokes are
polylines in that box. A stroke's own colour / role come from the nearer end (switch halfway), its Thickness blends
(user 2026-11-25). The fill settings follow Add between's rules: the same kind of setting at both ends blends
number by number, anything else switches halfway (user 2026-11-25); areas coloured by hand and the centre points of
Nested / Tiles / Ripples come from the nearer end, put where they are on the roll."""

import json
import math

import numpy as np

POINTS = 200  # points spread round / along every pair (at least; nested.MORPH_POINTS)
MOST = 2000  # (at most: more corners than that are spread evenly)
SAME = 1e-9

# a custom shape's keys that are its drawing / made from it, not settings to blend
DRAWING_KEYS = ("kind", "pts", "strokes", "areas", "round", "fit", "name", "between", "even_free", "from", "notes",
                "text", "patterns_kept", "range_kept", "team")
# the keys Pair.at sets on any step (velocity, pages, colours...): left to it
SHARED_KEYS = ("vel0", "vel1", "vel_env", "end_dot", "glue", "fx", "cycle", "sustain", "subtract", "extend",
               "recolour", "order", "off")
STROKE_SHAPE_KEYS = ("kind", "pts", "box", "k", "pk", "thick", "pen", "ink", "free", "smooth", "sides", "corners",
                     "shape", "pattern", "pattern2", "mix", "rev", "sharp", "sym", "src")


def drawn(sh):
    return sh.get("kind") == "custom"


def morphable(sh):
    """A custom shape Add between can use: a drawing (not text, pasted notes, a picture or a Hz bass)."""
    return drawn(sh) and bool(sh.get("strokes")) and not unmorphable(sh)


def unmorphable(sh):
    """A shape Add between leaves out (user 2026-11-25; the menu item greyed with why): text, pasted notes, a
    picture, a Hz bass."""
    return bool(sh.get("text") or "notes" in sh or "picture" in sh or sh.get("hz"))


# ---------------------------------------------------------------- the pieces of a shape

def _view(sh):
    if drawn(sh):
        from notes.nested import view_of
        try:
            return tuple(view_of(sh))
        except (ValueError, ZeroDivisionError, IndexError):
            return 0.0, 1.0
    return None


def _frame(sh):
    """The shape's box (three frame points, beats / pitch): a custom shape's own; a line's = the box round it."""
    if drawn(sh):
        return np.asarray(sh["pts"], float)
    from notes.engine import cached_path
    a = np.asarray(cached_path(sh), float).reshape(-1, 2)
    lo, hi = a.min(axis=0), a.max(axis=0)
    lo, hi = lo - (hi - lo < 1e-9) * 0.5, np.maximum(hi, lo + 1e-9) + (hi - lo < 1e-9) * 0.5
    return np.array([[lo[0], lo[1]], [hi[0], lo[1]], [lo[0], hi[1]]], float)


def _pieces(sh):
    """[(points in beats / pitch (N x 2, no repeated last point when closed), closed, the stroke's look)]: a custom
    shape's strokes as they look (formulas in), a line shape = one open piece."""
    from notes.custom import custom_strokes, path_closed
    from notes.engine import cached_path
    from notes.pen import THICK_MIN, pen_k
    out = []
    if drawn(sh):
        from notes.custom import bp_k, join_paths
        groups = {}  # (strokes that look alike, in drawing order: strokes meeting end to end = one piece, like
        # Fill sees them; user 2026-11-25: an area drawn as two curves came out as two pieces, one growing from a dot)
        for st, path in zip(sh["strokes"], custom_strokes(sh)):
            if not len(path):
                continue
            look = {k: v for k, v in st.items() if k not in STROKE_SHAPE_KEYS}
            if (st.get("thick") or 0) > THICK_MIN:  # (the pen's zoom as beats per key: the boxes differ)
                look.update(thick=st["thick"], pk=bp_k(sh["pts"], pen_k(st)))
                if st.get("pen"):
                    look["pen"] = st["pen"]
            path = [tuple(map(float, q)) for q in path]
            if st["kind"] == "ellipse" and math.dist(path[0], path[-1]) >= 1e-6:
                path.append(path[0])
            groups.setdefault(json.dumps(look, sort_keys=True), (look, []))[1].append(path)
        for look, paths in groups.values():
            for path in join_paths(paths) if len(paths) > 1 else paths:
                p = np.asarray(path, float).reshape(-1, 2)
                closed = path_closed([tuple(q) for q in p])
                if closed and len(p) > 1 and math.dist(p[0], p[-1]) < 1e-6:
                    p = p[:-1]
                out.append((p, closed, look))
        return out
    p = np.asarray(cached_path(sh), float).reshape(-1, 2)
    look = {}
    if (sh.get("thick") or 0) > THICK_MIN:
        look.update(thick=sh["thick"], pk=pen_k(sh))
        if sh.get("pen"):
            look["pen"] = sh["pen"]
    closed = len(p) > 2 and math.dist(p[0], p[-1]) < 1e-6
    out.append((p[:-1] if closed else p, closed, look))
    return out


def _dedupe(p, closed):
    keep = np.r_[True, np.hypot(*np.diff(p, axis=0).T) > SAME]
    p = p[keep]
    if closed and len(p) > 1 and math.dist(p[0], p[-1]) <= SAME:
        p = p[:-1]
    return p


def _length(p, closed):
    q = np.vstack([p, p[:1]]) if closed else p
    return float(np.hypot(*np.diff(q, axis=0).T).sum()) if len(q) > 1 else 0.0


def _along(p, n, closed):
    """n points along p, its own points kept (corners stay sharp), the rest shared out by length; evenly spread
    when it has more than n. Closed: round the loop (no repeated end); open: from its start to its end."""
    if len(p) == 1:
        return np.repeat(p, n, axis=0)
    if closed:
        from notes.nested import spread
        return spread(p, n)
    side = np.hypot(*np.diff(p, axis=0).T)
    total = float(side.sum())
    if total <= SAME:
        return np.repeat(p[:1], n, axis=0)
    m = len(p)
    if m > n:
        along = np.concatenate([[0.0], np.cumsum(side)])
        d = np.linspace(0.0, total, n)
        return np.column_stack([np.interp(d, along, p[:, 0]), np.interp(d, along, p[:, 1])])
    extra = n - m  # (points to add between its own)
    share = side / total * extra
    k = np.floor(share).astype(np.int64)
    k[np.argsort(k - share)[:extra - int(k.sum())]] += 1
    each = k + 1
    at = np.repeat(np.arange(m - 1), each)
    t = (np.arange(n - 1) - np.repeat(np.cumsum(each) - each, each)) / np.repeat(each, each)
    return np.vstack([p[at] + (p[at + 1] - p[at]) * t[:, None], p[-1:]])


def _loop_of(p):
    """An open piece as a flat loop: along it and back (so it can turn into a closed area)."""
    return np.vstack([p, p[-2:0:-1]]) if len(p) > 2 else p


def _area(p):
    x, y = p[:, 0], p[:, 1]
    return float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum() / 2)


def _travel(p, q):
    return float(np.hypot(*(q - p).T).sum())


class _Piece:
    def __init__(self, p, closed, look, view):
        a, c = view
        p = _dedupe(p, closed)
        self.closed, self.look = closed and len(p) >= 3, look
        self.v = np.column_stack([p[:, 0] + a * p[:, 1], c * p[:, 1]])  # (in the view)
        self.size = _length(self.v, self.closed) / 4  # (a square's side with that outline: open and closed alike)
        n = max(16, min(256, len(self.v)))
        self.mid = _along(self.v, n, self.closed).mean(axis=0) if len(self.v) > 1 else self.v[0]


class DrawPair:
    """Two shapes (one or both drawn) matched once; geometry(f) makes the step f of the way."""

    def __init__(self, a, b, turn=None):
        """turn: degrees the steps turn the shape by on the way (+ = clockwise, like Turn by…; WAY C, user
        2026-11-24: the first shape turned by it is matched with the last, the steps turn while the rest blends);
        None = the angle found by itself (found: an exact copy moved / resized / turned, else 0)."""
        self.a, self.b = a, b
        views = [v for v in (_view(a), _view(b)) if v]
        self.view = tuple(np.mean(views, axis=0)) if views else (0.0, 1.0)
        self.views = (_view(a) or self.view, _view(b) or self.view)
        self.fa, self.fb = _frame(a), _frame(b)
        A = [_Piece(*x, self.view) for x in _pieces(a)]
        B = [_Piece(*x, self.view) for x in _pieces(b)]
        self.found = self._found_turn()
        self.turn = self.found if turn is None else float(turn)
        self.theta = -math.radians(self.turn)  # (in the view, counter-clockwise)
        ma = np.array([x.mid for x in A]).reshape(-1, 2)
        self.ca = ma.mean(axis=0) if len(ma) else self._view_pts(self.fa).mean(axis=0)
        if self.theta:  # (the first shape turned the whole way first: the pieces matched as they'll end up)
            for x in A:
                x.v, x.mid = _turned(x.v, self.theta, self.ca), _turned(x.mid[None], self.theta, self.ca)[0]
        self.fa_turned = self._bp_pts(_turned(self._view_pts(self.fa), self.theta, self.ca))
        self.pairs = []  # (from points, to points (view), closed, look a, look b, which end it belongs to)
        ma = np.array([x.mid for x in A]).reshape(-1, 2)
        mb = np.array([x.mid for x in B]).reshape(-1, 2)
        sa, sb = np.array([x.size for x in A]), np.array([x.size for x in B])
        used_a, used_b = set(), set()
        if A and B:
            cost = np.hypot(*(ma[:, None] - mb[None]).transpose(2, 0, 1)) + np.abs(sa[:, None] - sb[None])
            for at in np.argsort(cost, axis=None, kind="stable"):  # (the nearest pairs first)
                i, j = divmod(int(at), len(B))
                if i in used_a or j in used_b:
                    continue
                used_a.add(i)
                used_b.add(j)
                self.pairs.append(self._matched(A[i], B[j]))
                if len(used_a) == min(len(A), len(B)):
                    break
        for X, used, first in ((A, used_a, True), (B, used_b, False)):
            for i, x in enumerate(X):
                if i in used:
                    continue
                n = max(POINTS, min(MOST, len(x.v)))
                p = _along(x.v, n, x.closed)
                # (the dot = its middle's place in the OTHER shape's box, so it travels with the steps; at its own
                # spot, the end shape's extra lines grew in a pile where the last shape is: user 2026-11-25)
                own, other = (self.fa_turned, self.fb) if first else (self.fb, self.fa_turned)
                dot = np.repeat(self._moved(x.mid, own, other)[None], len(p), axis=0)
                self.pairs.append((p, dot, x.closed, x.look, x.look) if first else (dot, p, x.closed, x.look, x.look))
        self.cb = mb.mean(axis=0) if len(mb) else self.ca
        # (the move of the whole shape, beats / pitch: Steps' pushes; shaped like Pair.d)
        a_, c_ = self.view
        d = self.cb - self.ca
        self.d = np.array([[d[0] - a_ * d[1] / c_, d[1] / c_]])

    def _view_pts(self, bp):
        a, c = self.view
        bp = np.asarray(bp, float).reshape(-1, 2)
        return np.column_stack([bp[:, 0] + a * bp[:, 1], c * bp[:, 1]])

    def _bp_pts(self, v):
        a, c = self.view
        pitch = v[:, 1] / c
        return np.column_stack([v[:, 0] - a * pitch, pitch])

    def _found_turn(self):
        """The angle (degrees, + = clockwise) the last shape is turned by when it's an exact copy of the first (the
        same drawing, moved / resized / turned, not mirrored), else 0 (WAY C: found by itself, user 2026-11-24)."""
        a, b = self.a, self.b
        if not (drawn(a) and drawn(b)) or json.dumps(a["strokes"]) != json.dumps(b["strokes"]):
            return 0.0
        if (_det(self.fa) < 0) != (_det(self.fb) < 0):
            return 0.0
        ea, eb = (np.diff(self._view_pts(fr[:2]), axis=0)[0] for fr in (self.fa, self.fb))
        ang = math.degrees(math.atan2(ea[0] * eb[1] - ea[1] * eb[0], ea @ eb))
        return round(-ang, 6) + 0.0

    def _moved(self, v, own, other):
        """A point in the view at the same place in box `other` as it is in box `own` (itself if a box is flat)."""
        from notes.custom import frame_to_bp, frame_to_uv
        a, c = self.view
        pitch = v[1] / c
        to_uv = frame_to_uv(own.tolist())
        if to_uv is None or frame_to_uv(other.tolist()) is None:
            return v
        b, p = frame_to_bp(other.tolist())(*to_uv(v[0] - a * pitch, pitch))
        return np.array([b + a * p, c * p])

    @staticmethod
    def _matched(x, y):
        """(x's points, y's points lined up with them, closed, x's look, y's look)."""
        n = max(POINTS, min(MOST, max(len(x.v), len(y.v))))
        if not x.closed and not y.closed:  # (two open pieces: end to end, the way round that travels less)
            p, q = _along(x.v, n, False), _along(y.v, n, False)
            if _travel(p, q[::-1]) < _travel(p, q):
                q = q[::-1]
            return p, q, False, x.look, y.look
        from notes.nested import lined_up
        vx = x.v if x.closed else _loop_of(x.v)
        vy = y.v if y.closed else _loop_of(y.v)
        p = _along(vx, n, True)
        q = _along(vy, n, True)
        if x.closed and y.closed:  # (both the same way round: no turning inside out)
            if (_area(p) < 0) != (_area(q) < 0):
                q = q[::-1]
            q = lined_up(p, q)
        else:  # (a line opening into a loop: whichever way round travels less)
            q1, q2 = lined_up(p, q), lined_up(p, q[::-1].copy())
            q = q1 if _travel(p, q1) <= _travel(p, q2) else q2
        return p, q, True, x.look, y.look

    # ---- a step

    def frame(self, f):
        """The step's box: the first shape's going to the last's; a flat one (a flip on the way) = the box round
        the step's drawing (None)."""
        fr = self.fa_turned + (self.fb - self.fa_turned) * f
        if self.theta:  # (turned back the part of the way still to go, round the step's middle)
            fr = self._bp_pts(_turned(self._view_pts(fr), -self.theta * (1 - f), self.ca + (self.cb - self.ca) * f))
        (b0, p0), (b1, p1), (b2, p2) = fr
        det = (b1 - b0) * (p2 - p0) - (p1 - p0) * (b2 - b0)
        ends = max(abs(_det(self.fa)), abs(_det(self.fb)), 1e-9)
        return fr if abs(det) > 1e-3 * ends else None

    def strokes_bp(self, f):
        """[(points beats / pitch, closed, look)] of the step f of the way (pieces shrunk to a dot left out)."""
        a, c = self.view
        out = []
        for p, q, closed, la, lb in self.pairs:
            x = p + (q - p) * f
            if np.ptp(x[:, 0]) < 1e-9 and np.ptp(x[:, 1]) < 1e-9:
                continue
            if self.theta:
                x = _turned(x, -self.theta * (1 - f), self.ca + (self.cb - self.ca) * f)
            pitch = x[:, 1] / c
            bp = np.column_stack([x[:, 0] - a * pitch, pitch])
            look = dict(la if f < 0.5 else lb)
            for key in ("thick", "pk", "pen"):
                look.pop(key, None)
            from notes.pen import pen_between
            look.update(pen_between(dict(la, kind="poly"), dict(lb, kind="poly"), f))
            out.append((bp, closed, look))
        return out

    def geometry(self, f):
        """The step's kind, box, strokes and its custom shape settings (the nearer end's, blended where alike)."""
        from notes.custom import box_frame, frame_to_bp, frame_to_uv, moved_at, uv_k, CENTRE_KEYS
        got = self.strokes_bp(f)
        fr = self.frame(f)
        if fr is None or not got:
            allp = np.concatenate([g[0] for g in got]) if got else self.fa
            lo, hi = allp.min(axis=0), allp.max(axis=0)
            fr = np.asarray(box_frame(lo[0], lo[1], max(hi[0], lo[0] + 1e-6), max(hi[1], lo[1] + 1e-6)), float)
        if got:  # (the box fitted round the drawing at once, its turn / slant kept: custom.refit's work, in NumPy)
            to_uv = frame_to_uv(fr.tolist())
            assert to_uv is not None  # (frame() never gives a flat box, the fallback box isn't flat)
            allp = np.concatenate([g[0] for g in got])
            u, v = to_uv(allp[:, 0], allp[:, 1])
            ul, vl = float(u.min()), float(v.min())
            w, h = float(u.max()) - ul, float(v.max()) - vl
            if w < 1e-9:
                ul, w = ul - 0.5, 1.0
            if h < 1e-9:
                vl, h = vl - 0.5, 1.0
            to_bp = frame_to_bp(fr.tolist())
            fr = np.array([to_bp(ul, vl), to_bp(ul + w, vl), to_bp(ul, vl + h)], float)
        to_uv = frame_to_uv(fr.tolist())
        assert to_uv is not None
        strokes = []
        for bp, closed, look in got:
            u, v = to_uv(bp[:, 0], bp[:, 1])
            pts = np.column_stack([u, v])
            if closed:
                pts = np.vstack([pts, pts[:1]])
            st = {"kind": "poly", "pts": np.round(pts, 7).tolist()}
            st.update({k: v for k, v in look.items() if k not in ("pk",)})
            if "pk" in look:
                st["pk"] = uv_k(fr.tolist(), look["pk"])
            strokes.append(st)
        near, far = (self.a, self.b) if f < 0.5 else (self.b, self.a)
        nd = near if drawn(near) else far  # (a line end has no fill settings: the drawn one's)
        out = {"kind": "custom", "pts": fr.tolist(), "strokes": strokes}
        out.update(_settings(self.a, self.b, f, nd))
        if drawn(nd):  # (areas + centre points: where they are on the roll at that end)
            to_bp = frame_to_bp(nd["pts"])

            def fn(u, v):
                return tuple(float(x) for x in to_uv(*to_bp(u, v)))
            if nd.get("areas"):
                out["areas"] = [[*fn(u, v), c] for u, v, c in nd["areas"]]
            for key in CENTRE_KEYS:
                if out.get(key):
                    out[key] = moved_at(out[key], fn)
        rd = _round_for(fr, _mixed_view(self.views, f))
        if rd:
            out["round"] = rd
        return out


def _turned(v, ang, centre):
    """Points (N x 2, the view) turned ang radians counter-clockwise round centre."""
    if not ang:
        return v
    co, si = math.cos(ang), math.sin(ang)
    d = v - centre
    return np.column_stack([centre[0] + co * d[:, 0] - si * d[:, 1], centre[1] + si * d[:, 0] + co * d[:, 1]])


def _det(fr):
    (b0, p0), (b1, p1), (b2, p2) = fr
    return (b1 - b0) * (p2 - p0) - (p1 - p0) * (b2 - b0)


def _mixed_view(views, f):
    (a0, c0), (a1, c1) = views
    return a0 + (a1 - a0) * f, c0 + (c1 - c0) * f


def _round_for(fr, view):
    """sh["round"] for a box fr so the shape is measured in `view` (custom.drawn_view the other way round)."""
    a, c = view
    (b0, p0), (b1, p1), (b2, p2) = fr
    eu = np.array([(b1 - b0) + a * (p1 - p0), c * (p1 - p0)])  # (one u and one v in the view)
    ev = np.array([(b2 - b0) + a * (p2 - p0), c * (p2 - p0)])
    s2 = float(eu @ eu)
    if s2 <= 1e-18 or c <= 0:
        return None
    m, n = float(eu @ ev) / s2, abs(float(eu[0] * ev[1] - eu[1] * ev[0])) / s2
    if n <= 1e-12:
        return None
    return round(n, 12) if abs(m) < 1e-12 else [round(m, 12), round(n, 12)]


def _settings(a, b, f, near):
    """The custom shape settings of a step: a setting both drawn ends have in the same form blends number by
    number, anything else is the nearer drawn end's (Add between's rules, user 2026-11-25)."""
    from notes.between import alike, mix
    far = b if near is a else a
    out = {}
    skip = set(DRAWING_KEYS) | set(SHARED_KEYS)
    for key in near:
        if key in skip or key.startswith("_"):
            continue
        x = near[key]
        y = far.get(key) if drawn(far) else None
        if y is not None and alike(x, y) and not isinstance(x, (str, bool)):
            xa, yb = (x, y) if near is a else (y, x)
            out[key] = mix(xa, yb, f)
            if isinstance(x, dict) and "at" in x:  # (a centre point: the nearer end's, moved onto the step later)
                out[key]["at"] = json.loads(json.dumps(x["at"]))
        else:
            out[key] = json.loads(json.dumps(x))
    return out
