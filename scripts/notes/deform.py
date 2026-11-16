"""Drawer deformation (Deform… in the custom shape drawer): the bending maths. A point of the drawing -> where it
goes under Turn, Twist, Ripple and Bulge / Pinch, round the Centre mark, inside an area shaped like Stir's.

Stir works BACKWARDS (where a colour comes from); here the points themselves move, so each bend is worked FORWARDS:
what Stir shows a colour stripe doing, the drawn line does. The same order as Stir's look: Turn / Twist first, then
Ripple, then Bulge (a lens over the rest). The board is the drawing's box (0..1 across and up, up = up).

Settings (a dict; clean() fills it in):
  turn        degrees, + = anticlockwise
  twist       turns at the edge (Stir's Twist: most near the edge, none at the Centre)
  bulge       % (+ = swells out from the middle like a lens, - = pinched in; Stir's bulge_size)
  ripple      wave height, % of the board; ripple_len = wave length %; ripple_kind side / up / rings
  centre      [u, v] on the board (the Centre mark)
  area        None = the whole drawing, or {"kind": circle / square / diamond / drawn, "shape": a library drawing's
              area shape (recolour.clean_area_shape), "middle": [u, v], "size": % of the board (Stir's Size),
              "stretch" / "stretch_up": %, "tilt": degrees, "skew": slant, "hard": True = cut clean}
With an area: Turn, Twist and Ripple fade out to nothing at its outline (Hard edge: full inside, none outside),
Bulge is a lens filling the area round its own middle (its outline stays put). Without one: Twist winds out to the
board's farthest corner, Bulge fills the oval touching the board's four sides round the Centre (Stir's way)."""
import math

import numpy as np

from notes import recolour as rc

DEFAULTS = {"turn": 0.0, "twist": 0.0, "bulge": 0.0, "ripple": 0.0, "ripple_len": 10.0, "ripple_kind": "side",
            "centre": [0.5, 0.5], "area": None}
AREA_DEFAULTS = {"kind": "circle", "shape": None, "middle": [0.5, 0.5], "size": 50.0, "stretch": 100.0,
                 "stretch_up": 100.0, "tilt": 0.0, "skew": 0.0, "hard": False}
AREA_KINDS = rc.STIR_AREAS + (rc.DRAWN,)
RIPPLE_KINDS = rc.RIPPLE_KINDS


def _num(v, lo, hi, plain):
    return float(min(max(v, lo), hi)) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else plain


def clean(d):
    """Deform settings -> every one filled in and kept in its range (Stir's ranges)."""
    d = d if isinstance(d, dict) else {}
    out = {"turn": _num(d.get("turn"), -rc.MOST_TURN, rc.MOST_TURN, 0.0),
           "twist": _num(d.get("twist"), -rc.MOST_TIGHT, rc.MOST_TIGHT, 0.0),
           "bulge": _num(d.get("bulge"), -rc.MOST_BULGE, rc.MOST_BULGE, 0.0),
           "ripple": _num(d.get("ripple"), 0.0, rc.MOST_RIPPLE, 0.0),
           "ripple_len": _num(d.get("ripple_len"), rc.LEAST_RIPPLE_LEN, rc.MOST_RIPPLE_LEN, 10.0),
           "ripple_kind": d.get("ripple_kind") if d.get("ripple_kind") in RIPPLE_KINDS else "side",
           "centre": _spot(d.get("centre")), "area": None}
    a = d.get("area")
    if isinstance(a, dict):
        kind = a.get("kind") if a.get("kind") in AREA_KINDS else "circle"
        shape = rc.clean_area_shape(a.get("shape")) if kind == rc.DRAWN else None
        if kind == rc.DRAWN and not shape:
            kind = "circle"
        out["area"] = {"kind": kind, "shape": shape, "middle": _spot(a.get("middle")),
                       "size": _num(a.get("size"), rc.LEAST_REACH, rc.MOST_REACH, 50.0),
                       "stretch": _num(a.get("stretch"), rc.LEAST_STRETCH, rc.MOST_STRETCH, 100.0),
                       "stretch_up": _num(a.get("stretch_up"), rc.LEAST_STRETCH, rc.MOST_STRETCH, 100.0),
                       "tilt": _num(a.get("tilt"), -360.0, 360.0, 0.0),
                       "skew": _num(a.get("skew"), -rc.MOST_SKEW, rc.MOST_SKEW, 0.0),
                       "hard": a.get("hard") is True}
    return out


def _spot(p):
    if isinstance(p, (list, tuple)) and len(p) == 2:
        return [_num(p[0], -10.0, 11.0, 0.5), _num(p[1], -10.0, 11.0, 0.5)]
    return [0.5, 0.5]


def moves(d):
    """Whether settings d (cleaned) move anything."""
    return bool(d["turn"] % 360 or d["twist"] or d["bulge"] or d["ripple"])


def as_stir(area):
    """A cleaned area -> a filled-in stir holding it, for Stir's own area maths (area_spot_q, area_outlines,
    area_corners): Size / Stretch / Angle / Slant counted the board's way (square, Size of the box)."""
    return dict(rc.STIR_DEFAULTS, reach=area["size"], area=area["kind"], shape=area["shape"],
                stretch=area["stretch"], stretch_up=area["stretch_up"], tilt=area["tilt"], skew=area["skew"],
                hard=area["hard"], spot=[v * 100 for v in area["middle"]], centre=[v * 100 for v in area["middle"]])


ONE = (1.0, 1.0)  # (the board is square: Stir's area_scales without "Round as seen")


def area_q(x, y, area):
    """How far points (x, y) sit from the area's middle out to its outline: 0 in the middle, 1 on it and past it."""
    return rc.area_spot_q(np.asarray(x, np.float64), np.asarray(y, np.float64), as_stir(area), ONE)


def weight(x, y, area):
    """How much the area lets Turn / Twist / Ripple move points (x, y): 1 everywhere without an area; inside one
    1 in the middle fading smoothly to 0 at its outline (Stir's fade), Hard edge = 1 inside, 0 outside."""
    if area is None:
        return np.ones(np.shape(x))
    q = area_q(x, y, area)
    return (q < 1).astype(np.float64) if area["hard"] else 1 - q * q * (3 - 2 * q)


def bend(x, y, d):
    """Points (x, y) on the board -> where settings d (cleaned) put them, as two float arrays."""
    x, y = np.array(x, np.float64, ndmin=1), np.array(y, np.float64, ndmin=1)
    area = d["area"]
    cx, cy = d["centre"]
    w = weight(x, y, area) if area is not None and (d["turn"] or d["twist"] or d["ripple"]) else None
    x0, y0 = x, y
    if d["turn"] or d["twist"]:
        x, y = _turned(x, y, d, cx, cy, w)
    if d["ripple"]:
        x, y = _rippled(x, y, d, cx, cy, w)
    if w is not None:  # (a point the area leaves alone keeps its EXACT place: one stuck on a line stays on it)
        x, y = np.where(w > 0, x, x0), np.where(w > 0, y, y0)
    if d["bulge"]:
        x, y = _bulged(x, y, d, cx, cy)
    return x, y


def _turned(x, y, d, cx, cy, w):
    """Turn + Twist round the Centre (Stir's stir_from forwards): Twist grows with the square root of the distance,
    out to the board's farthest corner (an area: to its outline)."""
    dx, dy = x - cx, y - cy
    turns = d["turn"] / 360
    if w is not None:
        turns = turns * w
        if d["twist"]:
            turns = turns + d["twist"] * np.sqrt(area_q(x, y, d["area"])) * w
    elif d["twist"]:
        far = max(math.hypot(X - cx, Y - cy) for X in (0, 1) for Y in (0, 1)) or 1.0
        turns = turns + d["twist"] * np.sqrt(np.hypot(dx, dy) / far)
    a = 2 * np.pi * np.asarray(turns, np.float64)
    ca, sa = np.cos(a), np.sin(a)
    return cx + dx * ca - dy * sa, cy + dx * sa + dy * ca


def _rippled(x, y, d, cx, cy, w):
    """Ripple: points pushed along a wave measured from the Centre (Stir's way round: the drawn line waves the way
    Stir's stripes do). Sideways = pushed across by the height up the board, Up and down = pushed up by the place
    across, Rings = pushed in / out by the distance."""
    X, Y = x - cx, y - cy
    h, L = d["ripple"] / 100, d["ripple_len"] / 100
    f = 1.0 if w is None else w
    kind = d["ripple_kind"]
    if kind == "side":
        X = X - h * np.sin(2 * np.pi * Y / L) * f
    elif kind == "up":
        Y = Y - h * np.sin(2 * np.pi * X / L) * f
    else:
        R = np.hypot(X, Y)
        g = np.where(R > 1e-12, (R - h * np.sin(2 * np.pi * R / L) * f) / np.maximum(R, 1e-12), 1.0)
        X, Y = X * g, Y * g
    return cx + X, cy + Y


def _bulged(x, y, d, cx, cy):
    """Bulge / Pinch: a lens (Stir's bulge forwards: a point q of the way out goes to m q / (1 + (m - 1) q), the
    middle m times as big, the edge 1 / m). An area: inside it round its own middle, its outline stays. Else round
    the Centre inside the oval touching the board's four sides."""
    m = rc.bulge_size(d["bulge"])
    area = d["area"]
    if area is not None:
        mx, my = area["middle"]
        q = area_q(x, y, area)
    else:
        mx, my = cx, cy
        dx, dy = x - mx, y - my
        q = np.hypot(np.where(dx > 0, dx / max(1 - mx, 1e-12), -dx / max(mx, 1e-12)),
                     np.where(dy > 0, dy / max(1 - my, 1e-12), -dy / max(my, 1e-12)))
    ok = (q < 1) & (q > 1e-12)
    x, y = x.copy(), y.copy()
    g = m / (1 + (m - 1) * q[ok])  # (the new distance / the old; the rest keep their exact place)
    x[ok], y[ok] = mx + (x[ok] - mx) * g, my + (y[ok] - my) * g
    return x, y


def bend_points(pts, d):
    """A list of [u, v] points -> the bent list (d cleaned)."""
    if not len(pts):
        return []
    p = np.asarray(pts, np.float64)
    x, y = bend(p[:, 0], p[:, 1], d)
    return np.column_stack((x, y)).tolist()


def _bend2(p, d):
    """bend on an (n, 2) array -> (n, 2)."""
    x, y = bend(p[:, 0], p[:, 1], d)
    return np.column_stack((x, y))


# STROKES MADE BENDABLE (stage B). A bent straight piece is no longer straight: points are added along it only
# where the bent line would stray more than TOL from the straight piece between them (a piece the bend leaves
# alone or only turns / moves stays 2 points). Curves stay curves: each piece's handles follow the bend's own
# stretch at its anchor (smooth anchors stay smooth), split in two where that strays more than TOL.
TOL = 0.0005  # (board units: under half a pixel on a 1000 px board)
MOST_DEPTH = 12  # (each piece split in two at most this many times: 4096 pieces; a Hard edge never settles)
CHECK_STEP = 0.02  # (a piece is checked at least every this much of its length, and 8 times a ripple's wave)
PLAIN_KEEP = ("role", "colour", "layer", "src")


def _checks(longest, d):
    """How many spots inside a piece of this length are checked."""
    step = CHECK_STEP
    if d["ripple"]:
        step = min(step, d["ripple_len"] / 100 / 8)
    return int(min(max(math.ceil(longest / step), 3), 64))


def _line_pieces(p, d, tol):
    """Points p (n, 2) of a polyline -> its bent points with points added where needed (every point of p kept,
    bent exactly as bend does it)."""
    a, b = p[:-1], p[1:]
    seg = np.arange(len(a))
    t0, t1 = np.zeros(len(a)), np.ones(len(a))
    end_bent = _bend2(p, d)
    done = []
    for depth in range(MOST_DEPTH + 1):
        if not len(seg):
            break
        ab = b[seg] - a[seg]
        P0 = a[seg] + ab * t0[:, None]
        P1 = np.where((t1 == 1)[:, None], b[seg], a[seg] + ab * t1[:, None])
        B0 = np.where((t0 == 0)[:, None], end_bent[seg], _bend2(P0, d))
        B1 = np.where((t1 == 1)[:, None], end_bent[seg + 1], _bend2(P1, d))
        k = _checks(float(np.hypot(*(P1 - P0).T).max()), d)
        ts = np.arange(1, k + 1) / (k + 1)
        S = _bend2((P0[:, None, :] + (P1 - P0)[:, None, :] * ts[None, :, None]).reshape(-1, 2), d).reshape(-1, k, 2)
        e = B1 - B0
        ee = np.maximum((e * e).sum(1), 1e-300)
        s = np.clip(((S - B0[:, None, :]) * e[:, None, :]).sum(2) / ee[:, None], 0, 1)
        off = np.hypot(*(B0[:, None, :] + s[..., None] * e[:, None, :] - S).transpose(2, 0, 1)).max(1)
        ok = (off <= tol) | (depth == MOST_DEPTH)
        done.append((seg[ok], t0[ok], t1[ok]))
        bad = ~ok
        mid = (t0[bad] + t1[bad]) / 2
        seg = np.concatenate((seg[bad], seg[bad]))
        t0, t1 = np.concatenate((t0[bad], mid)), np.concatenate((mid, t1[bad]))
    seg, t0, t1 = (np.concatenate([x[k] for x in done]) for k in range(3))
    order = np.lexsort((t0, seg))
    seg, t1 = seg[order], t1[order]
    inner = t1 < 1
    out = np.empty((len(seg), 2))
    out[~inner] = end_bent[seg[~inner] + 1]
    was = b[seg].copy()
    if inner.any():
        was[inner] = a[seg[inner]] + (b - a)[seg[inner]] * t1[inner, None]
        out[inner] = _bend2(was[inner], d)
    out, was = np.vstack((end_bent[:1], out)), np.vstack((p[:1], was))
    # (halving puts some added points where nothing bends, e.g. a line whose middle crosses the area: an added
    # point that didn't move, between two that didn't either, lies on the old straight piece: dropped)
    still = (out == was).all(1)
    added = np.concatenate(([False], inner, ))
    drop = added.copy()
    drop[1:-1] &= still[1:-1] & still[:-2] & still[2:]
    drop[[0, -1]] = False
    return out[~drop]


def _stretch(p, d, h=1e-6):
    """The bend's stretch at points p (n, 2): (n, 2, 2), column 0 = where a step across goes, 1 = a step up."""
    out = np.empty((len(p), 2, 2))
    for k in (0, 1):
        e = np.zeros(2)
        e[k] = h
        out[:, :, k] = (_bend2(p + e, d) - _bend2(p - e, d)) / (2 * h)
    return out


def _bez(c, t):
    """Points at t (k,) on cubic pieces c (n, 4, 2) -> (n, k, 2)."""
    t = t[None, :, None]
    mt = 1 - t
    p0, p1, p2, p3 = (c[:, j][:, None, :] for j in range(4))
    return mt ** 3 * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t ** 3 * p3


def _halves(c):
    """Cubic pieces c (n, 4, 2) split at t = 0.5 -> (left, right), each (n, 4, 2), the same curve exactly."""
    p0, p1, p2, p3 = (c[:, j] for j in range(4))
    a, b, e = (p0 + p1) / 2, (p1 + p2) / 2, (p2 + p3) / 2
    f, g = (a + b) / 2, (b + e) / 2
    m = (f + g) / 2
    return np.stack((p0, a, f, m), 1), np.stack((m, g, e, p3), 1)


def _bent_cubics(c, d):
    """Cubic pieces c (n, 4, 2) -> bent pieces: anchors bent exactly, handles moved by the bend's stretch at
    their anchor (a smooth anchor's two handles stay in one line)."""
    A = _bend2(c[:, [0, 3]].reshape(-1, 2), d).reshape(-1, 2, 2)
    J = _stretch(c[:, [0, 3]].reshape(-1, 2), d).reshape(-1, 2, 2, 2)
    h1 = A[:, 0] + np.einsum("nij,nj->ni", J[:, 0], c[:, 1] - c[:, 0])
    h2 = A[:, 1] + np.einsum("nij,nj->ni", J[:, 1], c[:, 2] - c[:, 3])
    # (an anchor the bend leaves alone keeps its handles EXACTLY: the stretch there is 1 only up to float noise)
    still = (A == c[:, [0, 3]]).all(2) & (np.abs(J - np.eye(2)).max((2, 3)) < 1e-7)
    h1 = np.where(still[:, :1], c[:, 1], h1)
    h2 = np.where(still[:, 1:], c[:, 2], h2)
    return np.stack((A[:, 0], h1, h2, A[:, 1]), 1)


def _curve_pieces(pts, sharp, d, tol):
    """A curve's points (bezier.py list) -> (bent points, sharp anchors): pieces split in two where the bent
    handles stray more than tol from the bent curve."""
    p = np.asarray(pts, np.float64)
    n = (len(p) - 1) // 3
    cs = np.stack([p[3 * i:3 * i + 4] for i in range(n)])
    seg = np.arange(n)
    t0 = np.zeros(n)
    w = np.ones(n)
    done = []
    for depth in range(MOST_DEPTH + 1):
        if not len(seg):
            break
        bc = _bent_cubics(cs, d)
        size = float(np.hypot(*(cs[:, 3] - cs[:, 0]).T).max() + np.hypot(*(cs[:, 1] - cs[:, 0]).T).max())
        k = _checks(size, d)
        ts = np.arange(1, k + 1) / (k + 1)
        S = _bend2(_bez(cs, ts).reshape(-1, 2), d).reshape(-1, k, 2)
        off = np.hypot(*(_bez(bc, ts) - S).transpose(2, 0, 1)).max(1)
        ok = (off <= tol) | (depth == MOST_DEPTH)
        done.append((seg[ok], t0[ok], bc[ok]))
        bad = ~ok
        left, right = _halves(cs[bad])
        cs = np.concatenate((left, right))
        seg = np.concatenate((seg[bad], seg[bad]))
        t0 = np.concatenate((t0[bad], t0[bad] + w[bad] / 2))
        w = np.concatenate((w[bad] / 2, w[bad] / 2))
    seg, t0, bc = (np.concatenate([x[k] for x in done]) for k in range(3))
    order = np.lexsort((t0, seg))
    seg, bc = seg[order], bc[order]
    out = [bc[0, 0]]
    for c in bc:
        out += [c[1], c[2], c[3]]
    first = {int(s): i for i, s in reversed(list(enumerate(seg)))}  # (each old piece's first new piece)
    return [[float(u), float(v)] for u, v in out], [first[a] for a in sharp if a in first]


def _plain_line(st):
    """Whether a stroke is bent as its own points (a line / polyline / freehand without formulas or smoothing)."""
    from notes.custom import has_formula
    return st["kind"] == "poly" and not st.get("smooth") and not has_formula(st) and not st.get("sides")


def bent_stroke(st, d, tol=TOL):
    """One drawer stroke bent by settings d (cleaned) -> the new stroke (the same dict back when nothing of it
    moves). Curves stay curves (extra anchors where needed; symmetric halves dropped); lines, polylines and
    freehand keep every point and get more where the bend curves them; ellipses, arcs, freehand made perfect and
    strokes with formulas become polylines (like Slice). Role, outline colour and layer kept."""
    from notes.custom import has_formula, stroke_points
    keep = {k: st[k] for k in PLAIN_KEEP if k in st}
    if not moves(d):
        return st
    if st["kind"] == "curve" and not has_formula(st):  # (unchanged = nothing split, every point where it was)
        pts, sharp = _curve_pieces(st["pts"], st.get("sharp", ()), d, tol)
        if pts == [[float(u), float(v)] for u, v in st["pts"]]:
            return st
        out = dict(keep, kind="curve", pts=pts)
        if sharp:
            out["sharp"] = sharp
        return out
    plain = _plain_line(st)
    p = np.asarray(st["pts"] if plain else stroke_points(st), np.float64)
    if len(p) < 2:
        return st
    got = _line_pieces(p, d, tol)
    if len(got) == len(p) and not (got != p).any():
        return st
    out = dict(keep, kind="poly", pts=[[float(u), float(v)] for u, v in got])
    if plain and st.get("free"):
        out.update(free=True, k=st.get("k", 1.0))
    return out


def preview_points(st, d, step=0.004):
    """A stroke's look bent by d, quickly (the live preview: no fitting): its drawn points, each piece cut to at
    most step long (shorter for short ripples), all bent."""
    from notes.custom import stroke_points
    p = np.asarray(stroke_points(st), np.float64)
    if len(p) < 2:
        return p
    if d["ripple"]:
        step = min(step, d["ripple_len"] / 100 / 8)
    n = np.maximum(np.ceil(np.hypot(*(p[1:] - p[:-1]).T) / step), 1).astype(np.int64)
    if (n > 1).any():
        idx = np.repeat(np.arange(len(n)), n)
        f = np.arange(len(idx)) - np.repeat(np.cumsum(n) - n, n)
        p = np.vstack((p[:-1][idx] + (p[1:] - p[:-1])[idx] * (f / n[idx])[:, None], p[-1:]))
    return _bend2(p, d)
