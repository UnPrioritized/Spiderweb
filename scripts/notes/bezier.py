"""Bézier curves with anchors and handles (a pen tool), as used by Curve shapes, the drawer's curves and the funnel's curves:
sampling, adding / removing anchors, pen tool editing, symmetric curves, and fitting a curve with as few anchors as
possible to a list of points.

A curve is a flat list of points [anchor, handle, handle, anchor, handle, handle, anchor, ...]: every third point
(0, 3, 6, ...) is an anchor the curve goes through, and the two points between two anchors are the handles: the
out handle of the anchor before and the in handle of the anchor after."""

import math


def anchor_count(pts):
    return (len(pts) - 1) // 3 + 1


def seg_point(p0, p1, p2, p3, t):
    mt = 1 - t
    a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def segments(pts):
    return [pts[i:i + 4] for i in range(0, len(pts) - 3, 3)]


def sample(pts, n=48):
    """Points along the curve, n per segment."""
    out = [tuple(pts[0])]
    for seg in segments(pts):
        out += [seg_point(*seg, i / n) for i in range(1, n + 1)]
    return out


def _lerp(a, b, t):
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


def split(pts, s, t):
    """A new anchor at t (0..1) on segment s; the curve's shape stays exactly the same."""
    p0, p1, p2, p3 = pts[3 * s:3 * s + 4]
    a, b, c = _lerp(p0, p1, t), _lerp(p1, p2, t), _lerp(p2, p3, t)
    d, e = _lerp(a, b, t), _lerp(b, c, t)
    return [list(p) for p in pts[:3 * s + 1]] + [a, d, _lerp(d, e, t), e, c] + [list(p) for p in pts[3 * s + 3:]]


def remove_anchor(pts, a):
    """Without anchor a (not the first or last) and its two handles."""
    i = 3 * a
    return [list(p) for p in pts[:i - 1] + pts[i + 2:]]


def handle_anchor(i):
    """The anchor a handle point belongs to (1 -> 0, 2 -> 3, 4 -> 3, ...)."""
    return i - 1 if i % 3 == 1 else i + 1


# ---------------------------------------------------------------- symmetric curves
# A symmetric curve has an odd number of anchors: the middle one sits on the symmetry line (or point), and point i
# of one half belongs with point len-1-i of the other half. "turn" = the second half is the first one turned half
# way round the middle between the ends (an S), "mirror" = mirrored across the line through the middle (an arch).
# The mirror line runs along `axis`: 0 = the time direction, 1 = the pitch direction (on the roll: a mirrored point
# keeps its distance along that axis and its side swaps, so it doesn't depend on the zoom), None = at right angles
# to the line between the ends, an exact mirror (the drawer, whose board is the same scale both ways).

class Symmetry:
    def __init__(self, pts, mode, axis):
        (ax, ay), (bx, by) = pts[0], pts[-1]
        self.mode = mode
        self.m = ((ax + bx) / 2, (ay + by) / 2)
        d = self.d = ((bx - ax) / 2, (by - ay) / 2)
        self.w = (1.0, 0.0) if axis == 0 else (0.0, 1.0) if axis == 1 else (-d[1], d[0])
        self.det = d[0] * self.w[1] - d[1] * self.w[0]
        self.ok = abs(self.det) > 1e-12 if mode == "mirror" else True

    def split(self, v):
        """v = alpha * d + beta * w (w = along the mirror line)."""
        d, w = self.d, self.w
        return (v[0] * w[1] - v[1] * w[0]) / self.det, (d[0] * v[1] - d[1] * v[0]) / self.det

    def join(self, alpha, beta):
        return [alpha * self.d[0] + beta * self.w[0], alpha * self.d[1] + beta * self.w[1]]

    def reflect(self, p):
        m = self.m
        if self.mode == "turn":
            return [2 * m[0] - p[0], 2 * m[1] - p[1]]
        alpha, beta = self.split((p[0] - m[0], p[1] - m[1]))
        v = self.join(-alpha, beta)
        return [m[0] + v[0], m[1] + v[1]]

    def onto_line(self, p):
        """The nearest place for the middle anchor: the middle point (turn) or on the mirror line."""
        if self.mode == "turn":
            return list(self.m)
        _, beta = self.split((p[0] - self.m[0], p[1] - self.m[1]))
        v = self.join(0, beta)
        return [self.m[0] + v[0], self.m[1] + v[1]]

    def flat(self, anchor, h):
        """A smooth middle anchor's handle on a mirrored curve: along the ends' direction (the top of the arch is
        round), keeping how far it reaches that way."""
        alpha, _ = self.split((h[0] - anchor[0], h[1] - anchor[1]))
        v = self.join(alpha, 0)
        return [anchor[0] + v[0], anchor[1] + v[1]]


def symmetric(pts, sharp, mode, axis, source=0):
    """(pts, sharp) with one half (source 0 = from the start, 1 = from the end) copied onto the other half.
    pts needs an odd number of anchors (see make_symmetric); the ends stay where they are."""
    n = len(pts)
    if n < 7 or (n - 1) % 6:
        return pts, sharp
    sym = Symmetry(pts, mode, axis)
    if not sym.ok:
        return pts, sharp
    pts = [list(p) for p in pts]
    c = (n - 1) // 2
    mid = c // 3
    last = anchor_count(pts) - 1
    mine = range(1, c - 1) if source == 0 else range(c + 2, n - 1)
    for i in mine:
        pts[n - 1 - i] = sym.reflect(pts[i])
    # the middle anchor goes onto the symmetry line / point, taking its handles along
    new = sym.onto_line(pts[c])
    h = c - 1 if source == 0 else c + 1
    hp = [pts[h][0] + new[0] - pts[c][0], pts[h][1] + new[1] - pts[c][1]]
    pts[c] = new
    smooth_mid = mode == "turn" or mid not in sharp
    if mode == "mirror" and smooth_mid:
        hp = sym.flat(new, hp)
    pts[h] = hp
    pts[2 * c - h] = sym.reflect(hp)
    side = [a for a in sharp if (a < mid if source == 0 else a > mid)]
    sharp = sorted(set(side + [last - a for a in side] + ([] if smooth_mid else [mid])))
    return pts, sharp


def make_symmetric(pts, sharp, mode, axis, source=0):
    """Like symmetric, first giving the curve a middle anchor if it has none (the middle segment split in two)."""
    segs = len(segments(pts))
    if segs % 2:
        s = segs // 2
        pts = split(pts, s, 0.5)
        sharp = [a + 1 if a > s else a for a in sharp]
    return symmetric(pts, sharp, mode, axis, source)


# ---------------------------------------------------------------- pen tool editing
# For the Curve shape on the roll and curve strokes in the drawer: a curve c is a dict with "pts", and optional
# "sharp" (anchor numbers that are corners) and "sym" ("mirror" / "turn", see above). to_screen(p) -> (x, y) and
# from_screen(x, y) -> p map a curve point to the screen and back. exact: mirror exactly (see Symmetry).
# A joined curve (joined.py) can also have "gaps" (segment numbers that aren't drawn: the curve is in pieces) and
# "splits" (anchor numbers where a piece's next tumour section starts); their anchors stay, like the ends.

def piece_ends(c):
    """Anchor numbers that end a piece: the curve's two ends and the anchors on either side of each gap."""
    last = anchor_count(c["pts"]) - 1
    return {0, last} | {g for g in c.get("gaps", ())} | {g + 1 for g in c.get("gaps", ())}


def fixed_anchors(c):
    """Anchors that can't be removed: piece ends and tumour section starts."""
    return piece_ends(c) | set(c.get("splits", ()))


def shift_marks(c, after, d):
    """Anchor (and gap segment) numbers after `after` moved by d (an anchor added / removed there)."""
    for key in ("gaps", "splits"):
        if c.get(key):
            c[key] = [a + d if a > after else a for a in c[key]]

def set_sharp(c, sharp):
    if sharp:
        c["sharp"] = sorted(set(sharp))
    else:
        c.pop("sharp", None)


def sym_axis(pts, to_screen, exact=False):
    """Which way a mirrored curve's mirror line runs: across the ends' direction as it looks on screen
    (1 = up and down, 0 = left to right), or None = exact."""
    if exact:
        return None
    (ax, ay), (bx, by) = to_screen(pts[0]), to_screen(pts[-1])
    return 1 if abs(bx - ax) >= abs(by - ay) else 0


def keep_symmetric(c, i, to_screen, exact=False):
    """A symmetric curve's other half follows the half point i is in. True if it's symmetric."""
    if not c.get("sym"):
        return False
    source = 1 if i > (len(c["pts"]) - 1) // 2 else 0
    c["pts"], sharp = symmetric(c["pts"], c.get("sharp", []), c["sym"], sym_axis(c["pts"], to_screen, exact),
                                source)
    set_sharp(c, sharp)
    return True


def drag_point(c, i, new, alt, to_screen, from_screen, exact=False):
    """Point i dragged to new:
    an anchor moves with its handles (alt: pulls new handles out of it, both sides alike);
    a handle point moves, and on a smooth anchor the other handle turns with it to keep the curve smooth, keeping
    its length on screen (alt: just this one, the anchor becomes a sharp corner).
    A symmetric curve's other half follows."""
    pts, sharp = c["pts"], c.get("sharp", [])
    n = len(pts)
    if i % 3 == 0:
        d = [new[0] - pts[i][0], new[1] - pts[i][1]]
        if alt:
            for j in (i - 1, i + 1):
                if 0 <= j < n:
                    s = 1 if j > i else -1
                    pts[j] = [pts[i][0] + s * d[0], pts[i][1] + s * d[1]]
            set_sharp(c, [a for a in sharp if a != i // 3])
        else:
            for j in (i - 1, i, i + 1):
                if 0 <= j < n:
                    pts[j] = [pts[j][0] + d[0], pts[j][1] + d[1]]
    else:
        pts[i] = list(new)
        a = handle_anchor(i)
        if 0 < a < n - 1 and a // 3 not in sharp:
            if alt:
                set_sharp(c, sharp + [a // 3])
            else:
                other = 2 * a - i
                (ax, ay), (hx, hy), (ox, oy) = to_screen(pts[a]), to_screen(new), to_screen(pts[other])
                d, length = math.hypot(hx - ax, hy - ay), math.hypot(ox - ax, oy - ay)
                if d > 0 and length > 0:
                    pts[other] = list(from_screen(ax - (hx - ax) / d * length, ay - (hy - ay) / d * length))
    keep_symmetric(c, i, to_screen, exact)


def add_anchor(c, seg, t, new, to_screen, exact=False):
    """A new anchor at t on segment seg, moved to new (so the curve goes through there); a symmetric curve gets one
    on the other half too. Returns False (nothing added) if that's an anchor already."""
    if t in (0, 1):
        return False
    pts, sharp = c["pts"], c.get("sharp", [])
    splits = [(seg, t)]
    if c.get("sym"):  # the same place on the other half
        splits.append((len(segments(pts)) - 1 - seg, 1 - t))
    at = 3 * (seg + 1)
    if seg in c.get("gaps", ()):
        return False  # (a gap between pieces isn't part of the curve)
    for s, tt in sorted(splits, reverse=True):  # the later one first, so the earlier keeps its number
        pts = split(pts, s, tt)
        sharp = [a + 1 if a > s else a for a in sharp]
        shift_marks(c, s, 1)
        if s < seg:
            at += 3
    d = [new[0] - pts[at][0], new[1] - pts[at][1]]
    for j in (at - 1, at, at + 1):
        pts[j] = [pts[j][0] + d[0], pts[j][1] + d[1]]
    c["pts"] = pts
    set_sharp(c, sharp)
    keep_symmetric(c, at, to_screen, exact)
    return True


def can_delete(c, i):
    """What a right-click on point i does: "anchor" (removed), "handle" (pulled back in), "middle" (a symmetric
    curve's middle anchor: stays) or None (the ends and their handles stay, you couldn't grab them again)."""
    n = len(c["pts"])
    a = handle_anchor(i) if i % 3 else i
    if not 0 < a < n - 1 or a // 3 in piece_ends(c) or not i % 3 and a // 3 in fixed_anchors(c):
        return None
    if i % 3:
        return "handle"
    return "middle" if c.get("sym") and a == (n - 1) // 2 else "anchor"


def delete_point(c, i, to_screen, exact=False):
    """Right-click on point i (see can_delete): an anchor between the ends is removed (on a symmetric curve its
    partner too), a handle point is pulled back into its anchor (a sharp corner there)."""
    what = can_delete(c, i)
    pts, sharp = c["pts"], c.get("sharp", [])
    if what == "anchor":
        a = i // 3
        for k in sorted({a} | ({anchor_count(pts) - 1 - a} if c.get("sym") else set()), reverse=True):
            pts = remove_anchor(pts, k)
            sharp = [b - 1 if b > k else b for b in sharp if b != k]
            shift_marks(c, k, -1)
        c["pts"] = pts
        set_sharp(c, sharp)
    elif what == "handle":
        a = handle_anchor(i)
        pts[i] = list(pts[a])
        set_sharp(c, sharp + [a // 3])
    else:
        return
    keep_symmetric(c, i, to_screen, exact)


def set_symmetry(c, mode, source, to_screen, exact=False):
    """Symmetric halves on (mode "mirror" / "turn") or off (None); half `source` (0 = from the start, 1 = from
    the end) keeps its shape."""
    if mode is None:
        c.pop("sym", None)
        return
    pts, sharp = make_symmetric(c["pts"], c.get("sharp", []), mode, sym_axis(c["pts"], to_screen, exact),
                                source)
    c["pts"], c["sym"] = pts, mode
    set_sharp(c, sharp)


def half_at(pts, to_screen, x, y):
    """Which half of the curve (0 = from the start, 1 = from the end) is nearest to (x, y) on screen."""
    seg, t, _ = nearest(pts, to_screen, x, y)
    return 1 if (seg + t) * 2 > len(segments(pts)) else 0


def pen_handles(pts, selected=True, gaps=()):
    """[(point number, "ctrl" / "anchor" / "end")] to show, in drawing order (anchors on top): handle points (the
    ones between anchors only while pulled out of their anchor), anchors, the two ends. Not selected: the ends only.
    gaps: segments between the pieces of a joined curve (their handles aren't shown, the anchors beside them are
    ends)."""
    n = len(pts)
    ends = {0, n - 1} | {3 * g for g in gaps} | {3 * g + 3 for g in gaps}
    if not selected:
        return [(i, "end") for i in sorted(ends)]
    ctrls = [(i, "ctrl") for i in range(n) if i % 3 and (i - 1) // 3 not in gaps and
             (handle_anchor(i) in ends or pts[i] != pts[handle_anchor(i)])]
    return ctrls + [(i, "anchor") for i in range(3, n - 1, 3) if i not in ends] + [(i, "end") for i in sorted(ends)]


def handle_lines(pts, gaps=()):
    """[(anchor, handle point)]: the handle lines to draw."""
    return [(pts[handle_anchor(i)], p) for i, p in enumerate(pts)
            if i % 3 and (i - 1) // 3 not in gaps and p != pts[handle_anchor(i)]]


def nearest(pts, to_screen, x, y, n=64, gaps=()):
    """(segment, t, distance) of the curve point nearest to (x, y) on screen; to_screen maps a curve point.
    gaps: segments to leave out."""
    best = None
    for s, seg in enumerate(segments(pts)):
        if s in gaps:
            continue
        for i in range(n + 1):
            sx, sy = to_screen(seg_point(*seg, i / n))
            d = math.hypot(sx - x, sy - y)
            if best is None or d < best[2]:
                best = (s, i / n, d)
    return best


def resample(points, n=300):
    """The polyline as n+1 points evenly spaced along its length."""
    lens = [0.0]
    for a, b in zip(points, points[1:]):
        lens.append(lens[-1] + math.dist(a, b))
    total = lens[-1]
    if total == 0:
        return [tuple(points[0]), tuple(points[-1])]
    out, j = [], 0
    for i in range(n + 1):
        L = total * i / n
        while j < len(lens) - 2 and lens[j + 1] < L:
            j += 1
        seg = lens[j + 1] - lens[j]
        t = 0.0 if seg == 0 else (L - lens[j]) / seg
        out.append(tuple(_lerp(points[j], points[j + 1], t)))
    return out


def difference(pts_a, pts_b):
    """How different two curves are: the largest distance between them, compared evenly along both."""
    a, b = resample(sample(pts_a), 100), resample(sample(pts_b), 100)
    return max(math.dist(p, q) for p, q in zip(a, b))


# ---------------------------------------------------------------- fitting (Philip Schneider's algorithm)

def _unit(v):
    d = math.hypot(*v)
    return (v[0] / d, v[1] / d) if d else (0.0, 0.0)


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def fit(points, tol=0.003):
    """A curve (flat list, see the top) through the first and last point that stays within tol of all points."""
    pts = [tuple(points[0])]
    for p in points[1:]:
        if math.dist(p, pts[-1]) > 1e-9:
            pts.append(tuple(p))
    if len(pts) < 2:
        p = pts[0]
        return [list(p), list(p), list(p), list(p)]
    pts = resample(pts, 300)
    k = min(3, len(pts) - 1)
    t_left, t_right = _unit(_sub(pts[k], pts[0])), _unit(_sub(pts[-1 - k], pts[-1]))
    out = [list(pts[0])]
    _fit(pts, t_left, t_right, tol, out, 0)
    return out


def _fit(pts, t_left, t_right, tol, out, depth):
    p0, p3 = pts[0], pts[-1]
    if len(pts) == 2:
        d = math.dist(p0, p3) / 3
        out += [[p0[0] + t_left[0] * d, p0[1] + t_left[1] * d], [p3[0] + t_right[0] * d, p3[1] + t_right[1] * d],
                list(p3)]
        return
    u = _chord_params(pts)
    bez = _generate(pts, u, t_left, t_right)
    err, at = _max_error(pts, bez, u)
    if err > tol and err < tol * 16:
        for _ in range(6):
            u = [_newton(bez, p, t) for p, t in zip(pts, u)]
            bez = _generate(pts, u, t_left, t_right)
            err, at = _max_error(pts, bez, u)
            if err <= tol:
                break
    if err <= tol or depth > 10 or len(pts) < 5:
        out += [list(bez[1]), list(bez[2]), list(p3)]
        return
    t_mid = _unit(_sub(pts[at - 1], pts[at + 1]))
    if t_mid == (0.0, 0.0):
        t_mid = _unit(_sub(pts[at - 1], pts[at]))
    _fit(pts[:at + 1], t_left, t_mid, tol, out, depth + 1)
    _fit(pts[at:], (-t_mid[0], -t_mid[1]), t_right, tol, out, depth + 1)


def _chord_params(pts):
    u = [0.0]
    for a, b in zip(pts, pts[1:]):
        u.append(u[-1] + math.dist(a, b))
    return [x / u[-1] for x in u]


def _generate(pts, u, t_left, t_right):
    """The best handles along the given end directions (least squares)."""
    p0, p3 = pts[0], pts[-1]
    c00 = c01 = c11 = x0 = x1 = 0.0
    for p, t in zip(pts, u):
        mt = 1 - t
        b0, b1, b2, b3 = mt ** 3, 3 * mt * mt * t, 3 * mt * t * t, t ** 3
        a0 = (t_left[0] * b1, t_left[1] * b1)
        a1 = (t_right[0] * b2, t_right[1] * b2)
        c00 += a0[0] * a0[0] + a0[1] * a0[1]
        c01 += a0[0] * a1[0] + a0[1] * a1[1]
        c11 += a1[0] * a1[0] + a1[1] * a1[1]
        tmp = (p[0] - (p0[0] * (b0 + b1) + p3[0] * (b2 + b3)), p[1] - (p0[1] * (b0 + b1) + p3[1] * (b2 + b3)))
        x0 += a0[0] * tmp[0] + a0[1] * tmp[1]
        x1 += a1[0] * tmp[0] + a1[1] * tmp[1]
    det = c00 * c11 - c01 * c01
    length = math.dist(p0, p3)
    al = ar = 0.0
    if abs(det) > 1e-12:
        al, ar = (x0 * c11 - x1 * c01) / det, (c00 * x1 - c01 * x0) / det
    if al < 1e-6 * length or ar < 1e-6 * length:
        al = ar = length / 3
    return [p0, (p0[0] + t_left[0] * al, p0[1] + t_left[1] * al), (p3[0] + t_right[0] * ar, p3[1] + t_right[1] * ar),
            p3]


def _max_error(pts, bez, u):
    worst, at = 0.0, len(pts) // 2
    for i in range(1, len(pts) - 1):
        d = math.dist(seg_point(*bez, u[i]), pts[i])
        if d > worst:
            worst, at = d, i
    return worst, at


def _newton(bez, p, t):
    """t moved closer to where the curve is nearest to p."""
    p0, p1, p2, p3 = bez
    q = seg_point(p0, p1, p2, p3, t)
    mt = 1 - t
    d1 = [3 * (mt * mt * (p1[k] - p0[k]) + 2 * mt * t * (p2[k] - p1[k]) + t * t * (p3[k] - p2[k])) for k in (0, 1)]
    d2 = [6 * (mt * (p2[k] - 2 * p1[k] + p0[k]) + t * (p3[k] - 2 * p2[k] + p1[k])) for k in (0, 1)]
    num = (q[0] - p[0]) * d1[0] + (q[1] - p[1]) * d1[1]
    den = d1[0] ** 2 + d1[1] ** 2 + (q[0] - p[0]) * d2[0] + (q[1] - p[1]) * d2[1]
    if den == 0:
        return t
    return min(1.0, max(0.0, t - num / den))
