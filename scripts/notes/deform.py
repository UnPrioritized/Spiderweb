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
