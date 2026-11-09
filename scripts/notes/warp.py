"""Recolour's Warp (user 2026-11-09, PLAN.md; like image editors' Warp): a grid of points over a shape's colours;
dragging a point bends the colours with it like rubber. Notes never move or change length; only which colour each
one has changes, like the Stir and the Liquify.

THE GRID (rc["warp"]): n x n cells over the shape's colour box (recolour.unit_xy's 0..1 box: flipped / turned with
the shape, a piece's whole's box), so (n + 1) x (n + 1) points, each one moved by an offset [dx, dy] in that box.
Saved as the points, never baked (dragged again later; the colours under it can change and they bend the same way).
Applied after every stir, before the Liquify (user 2026-11-09: fixed order Stir -> Warp -> Liquify; "order added" =
its own rework later, planned-features.md).

Saved as {"n": cells a side, "pts": [[dx, dy], ...] row by row from the bottom left ((n + 1)^2 of them), "smooth":
False (only for Straight cells), "strength": % (only != 100), "on": False (only when hidden), "edge": STIR_EDGES (only
!= "escape")}. No point moved = no Warp (clean_warp: None).

THE BEND: a point moved = the colour that was at its place is now shown where it went (forward, like the editors).
Between the points the offsets are blended: Smooth = Catmull-Rom curves through the points (rubber, the editors'
look), Straight cells = each cell on its own (bilinear: folds along the grid lines). Each spot needs where its colour
comes FROM, so the bend is turned round (Newton steps on a fine grid, inverse_map) into a Liquify-style "where it
comes from" map, and the Liquify's lookup (liquify._where / _fetched / _taken / _apart) does the rest: At the edges,
colour sets, Per chord / run, two notes on a key kept apart, the gate flip's shown places alike.
"""

import json

import numpy as np

MOST_CELLS = 16  # (cells a side a file may hold; the window offers 3 / 4 / 5)
MOST_MOVE = 4.0  # (a point moves at most this many boxes)
MOST_STRENGTH = 400.0
DEFAULTS = {"smooth": True, "strength": 100.0, "on": True, "edge": "escape"}
PER_CELL = 32  # (the turned-round map's steps per cell...)
MOST_MAP = 513  # (... at most this many a side)
STEPS = 25  # (Newton steps at most; most spots settle in 3-5)
_MAPS = {}  # the warp's json -> its turned-round map (read-only); the last few kept


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and x == x and abs(x) != float("inf")


def clean_warp(d):
    """A saved rc["warp"] -> its clean form (see the top), or None (no Warp: nothing moved, or not readable)."""
    if not isinstance(d, dict):
        return None
    n, pts = d.get("n"), d.get("pts")
    if not (number(n) and n == int(n) and 1 <= n <= MOST_CELLS) or not isinstance(pts, list):
        return None
    n = int(n)
    if len(pts) != (n + 1) ** 2 or not all(isinstance(p, list) and len(p) == 2 and all(number(v) for v in p)
                                           for p in pts):
        return None
    clean = [[round(min(max(float(v), -MOST_MOVE), MOST_MOVE), 6) + 0.0 for v in p] for p in pts]
    if not any(v for p in clean for v in p):
        return None
    out = {"n": n, "pts": clean}
    if d.get("smooth") is False:
        out["smooth"] = False
    s = d.get("strength")
    if number(s) and round(float(s), 3) != DEFAULTS["strength"]:
        out["strength"] = round(min(max(float(s), 0.0), MOST_STRENGTH), 3)
    if d.get("on") is False:
        out["on"] = False
    from notes.recolour import STIR_EDGES
    if d.get("edge") in STIR_EDGES and d["edge"] != DEFAULTS["edge"]:
        out["edge"] = d["edge"]
    return out


def full(wp):
    """Every setting of a clean Warp filled in."""
    return dict(DEFAULTS, **wp)


def empty(n):
    """A grid of n x n cells with no point moved (as saved: the window's start)."""
    return {"n": int(n), "pts": [[0.0, 0.0] for _ in range((int(n) + 1) ** 2)]}


def offsets(wp, strength=True):
    """A clean Warp's point offsets as an array (n + 1 up, n + 1 across, 2), times its strength (strength=True)."""
    n = wp["n"]
    P = np.asarray(wp["pts"], np.float64).reshape(n + 1, n + 1, 2)
    if strength:
        P = P * (full(wp)["strength"] / 100.0)
    return P


def _catmull(P, x, y):
    """The offsets P (rows up, columns across, 2) at spots (x, y) of the box (0..1; past it: the edge's), on
    Catmull-Rom curves through the points (the grid padded by straight lines past its ends)."""
    Q = np.concatenate([2 * P[:1] - P[1:2], P, 2 * P[-1:] - P[-2:-1]], 0)
    Q = np.concatenate([2 * Q[:, :1] - Q[:, 1:2], Q, 2 * Q[:, -1:] - Q[:, -2:-1]], 1)
    gy, gx = P.shape[:2]

    def parts(v, g):
        f = np.clip(np.asarray(v, np.float64), 0.0, 1.0) * (g - 1)
        i = np.minimum(f.astype(np.int64), g - 2)
        t = f - i
        t2, t3 = t * t, t * t * t
        w = ((-t3 + 2 * t2 - t) / 2, (3 * t3 - 5 * t2 + 2) / 2, (-3 * t3 + 4 * t2 + t) / 2, (t3 - t2) / 2)
        return i, w
    i, wx = parts(x, gx)
    j, wy = parts(y, gy)
    out = np.zeros(np.shape(i) + (2,), np.float64)
    for a in range(4):
        row = np.zeros_like(out)
        for b in range(4):
            row += Q[j + a, i + b] * wx[b][..., None]
        out += row * wy[a][..., None]
    return out


def bend_at(P, x, y, smooth=True):
    """How far the spots (x, y) of the box move under the point offsets P (forward: where their colour goes)."""
    if smooth:
        return _catmull(P, x, y)
    from notes.liquify import sample
    return sample(P, x, y)


def inverse_map(wp):
    """A clean Warp turned round: (up, across, 2) Liquify-style map, each grid spot p = how far (in box units) the
    colour it shows comes from (s with s + bend(s) = p, so the map = s - p). Read-only, the last few kept. Where the
    bend folds over itself (a point dragged past its neighbours) the nearest settled answer is used."""
    key = json.dumps([wp["n"], wp["pts"], wp.get("smooth", True), wp.get("strength", 100.0)])
    got = _MAPS.get(key)
    if got is not None:
        return got
    P = offsets(wp)
    smooth = full(wp)["smooth"]
    g = min(MOST_MAP, PER_CELL * wp["n"] + 1)
    jj, ii = np.mgrid[0:g, 0:g]
    px, py = (ii / (g - 1)).ravel(), (jj / (g - 1)).ravel()
    d = bend_at(P, px, py, smooth)
    sx, sy = px - d[:, 0], py - d[:, 1]
    best = np.full(len(px), np.inf)
    bx, by = sx.copy(), sy.copy()
    live = np.arange(len(px))
    h = 1e-5
    for _ in range(STEPS):
        x, y = sx[live], sy[live]
        d0 = bend_at(P, x, y, smooth)
        rx, ry = x + d0[:, 0] - px[live], y + d0[:, 1] - py[live]
        err = np.hypot(rx, ry)
        better = err < best[live]
        k = live[better]
        best[k], bx[k], by[k] = err[better], x[better], y[better]
        go = err > 1e-9
        if not go.any():
            break
        live, x, y, rx, ry, d0 = live[go], x[go], y[go], rx[go], ry[go], d0[go]
        dx = (bend_at(P, x + h, y, smooth) - d0) / h
        dy = (bend_at(P, x, y + h, smooth) - d0) / h
        a, b, c, e = 1 + dx[:, 0], dy[:, 0], dx[:, 1], 1 + dy[:, 1]
        det = a * e - b * c
        ok = np.abs(det) > 1e-6
        safe = np.where(ok, det, 1.0)
        ux = np.where(ok, (e * rx - b * ry) / safe, rx)
        uy = np.where(ok, (a * ry - c * rx) / safe, ry)
        step = np.hypot(ux, uy)
        cut = np.where(step > 0.2, 0.2 / np.maximum(step, 1e-300), 1.0)
        sx[live], sy[live] = x - ux * cut, y - uy * cut
    m = np.stack([bx - px, by - py], 1).reshape(g, g, 2)
    m[np.abs(m) < 1e-12] = 0.0  # (spots the bend never reached fetch from themselves exactly)
    m.setflags(write=False)
    if len(_MAPS) > 8:
        _MAPS.pop(next(iter(_MAPS)))
    _MAPS[key] = m
    return m


def warped(notes, at, low, now, rc, which, pals, pick, box, own):
    """The colours now after rc's Warp: each note takes the colour of the note found where the bend fetches from
    (liquify.liquified's way, with the Warp's own edge choice). Hidden (eye off), strength 0 or none: now as it is."""
    from notes import liquify as lq
    from notes.recolour import leaders, nearest
    wp = clean_warp(rc.get("warp"))
    if not wp or wp.get("on") is False or not wp.get("strength", 100.0) or not len(at):
        return now
    a = inverse_map(wp)
    shown = lq.shown_places(notes, at, rc)
    w = lq._where(shown, rc, box, own, {"strength": 100.0, "on": True, "edge": full(wp)["edge"]})
    idx = np.flatnonzero(lq.touched(a, w["x"], w["y"]))
    if not len(idx):
        return now
    mid, key = lq._fetched(w, a, w["x"][idx], w["y"][idx], rc, box, own)
    src = nearest(shown, mid, key, own=False)
    new = np.array(now)
    new[idx] = lq._taken(now, idx, src, pals, rc)
    if rc.get("by"):
        new = new[leaders(at, rc["by"])]
    return lq._apart(notes, low, new, pals, pick)


def blend(x, y, f):
    """Add between's step f (0 = x's end, 1 = y's) of two Warps (either may be None = no point moved: the other fades
    in / out): the points' offsets blended on the nearer end's grid (the other end's bend read at its points),
    strength blended, the rest the nearer end's. None when nothing is left moved."""
    x, y = clean_warp(x), clean_warp(y)
    if not x and not y:
        return None
    near = (x if f < 0.5 else y) or x or y
    n = near["n"]
    jj, ii = np.mgrid[0:n + 1, 0:n + 1]
    u, v = (ii / n).ravel(), (jj / n).ravel()

    def on_grid(d):
        if not d:
            return np.zeros((n + 1, n + 1, 2))
        P = offsets(d, strength=False)
        return P if d["n"] == n else bend_at(P, u, v, full(d)["smooth"]).reshape(n + 1, n + 1, 2)
    P = on_grid(x) * (1 - f) + on_grid(y) * f
    sx = (x or y).get("strength", DEFAULTS["strength"])
    sy = (y or x).get("strength", DEFAULTS["strength"])
    return clean_warp(dict(near, pts=P.reshape(-1, 2).tolist(), strength=sx + (sy - sx) * f))
