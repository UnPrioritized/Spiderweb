"""Recolour's Liquify (user 2026-11-07, PLAN.md; like image editors' Liquify): a brush pushes the colours inside a
shape around. Notes never move or change length; only which colour each one has changes, like the Stir.

THE BEND MAP (rc["liquify"]["map"]): one grid over the shape's colour box (recolour.unit_xy's 0..1 box: flipped /
turned with the shape, a piece's whole's box), each grid point saying how far (in that box) the colour seen there is
fetched from: a spot p shows the colour found at p + strength * map(p) (between grid points: blended). Kept with the
shape, so it is applied again after every edit (never baked): the colours under it (Recolour mode, Stir) can change
and they flow along the same bends. Applied LAST, after every stir (recolour.all_stirred). One map per shape, no list
of strokes (user: like the editors; mistakes are fixed with Restore / undo).

Saved as {"grid": [across, up], "map": text (zlib + base64 of int16 pairs, 1 / SCALE of the box each), "strength":
% (only != 100), "on": False (only when hidden), "edge": STIR_EDGES (only != "escape")}. A map with nothing pushed is
no Liquify (clean_liquify: None).

THE BRUSHES (dab): each mouse step changes the map near the brush, the brush measured on screen (J: the colour box's
units per screen pixel there, so a round brush stays round however the shape is stretched / turned). The map is a
"where it comes from" map, so each dab is put on top of the one before by looking the old map up at the moved spot.
"""

import base64
import zlib

import numpy as np

SCALE = 4096  # (map units per colour box: 1/4096 of the box is the finest push kept)
MOST_PUSH = 8.0  # (a spot looks at most this many boxes away: int16 / SCALE)
GRID_LONG = 256  # (grid points along the shape's longer side on screen when its first stroke is made)
GRID_SHORT = 16  # (at least this many along the other side)
MOST_GRID = 1024
MOST_STRENGTH = 400.0  # (user 2026-11-07: up to 400 %)
DEFAULTS = {"strength": 100.0, "on": True, "edge": "escape"}
TOOLS = ("push", "restore", "smooth", "twirl", "pinch", "bulge")

_DECODED = {}  # map text -> its array (read-only); the last few kept: every making of the notes reads it


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and x == x


def decode(grid, text):
    """The map text -> float64 array (up, across, 2) in colour box units, read-only; None when it doesn't fit."""
    got = _DECODED.get(text)
    if got is not None and got.shape[:2] == (grid[1], grid[0]):
        return got
    try:
        raw = zlib.decompress(base64.b64decode(text.encode("ascii"), validate=True))
    except (ValueError, zlib.error, UnicodeEncodeError):
        return None
    gx, gy = grid
    if len(raw) != gx * gy * 4:
        return None
    a = np.frombuffer(raw, "<i2").astype(np.float64).reshape(gy, gx, 2) / SCALE
    a.setflags(write=False)
    if len(_DECODED) > 16:
        _DECODED.pop(next(iter(_DECODED)))
    _DECODED[text] = a
    return a


def encode(a):
    """A map array (up, across, 2) -> its text (rounded to 1 / SCALE, at most MOST_PUSH boxes)."""
    q = np.rint(np.clip(np.asarray(a, np.float64), -MOST_PUSH, MOST_PUSH) * SCALE).astype("<i2")
    text = base64.b64encode(zlib.compress(q.tobytes(), 6)).decode("ascii")
    return text


def clean_liquify(d):
    """A saved rc["liquify"] -> its clean form (see the top), or None (no Liquify: nothing pushed)."""
    if not isinstance(d, dict):
        return None
    g, text = d.get("grid"), d.get("map")
    if not (isinstance(g, list) and len(g) == 2 and all(number(v) and v == int(v) and 2 <= v <= MOST_GRID for v in g)
            and isinstance(text, str) and text):
        return None
    g = [int(v) for v in g]
    a = decode(g, text)
    if a is None or not a.any():
        return None
    out = {"grid": g, "map": text}
    s = d.get("strength")
    if number(s) and round(float(s), 3) != DEFAULTS["strength"]:
        out["strength"] = round(min(max(float(s), 0.0), MOST_STRENGTH), 3)
    if d.get("on") is False:
        out["on"] = False
    from notes.recolour import STIR_EDGES
    if d.get("edge") in STIR_EDGES and d["edge"] != DEFAULTS["edge"]:
        out["edge"] = d["edge"]
    return out


def full(lq):
    """Every setting of a clean Liquify filled in."""
    return dict(DEFAULTS, **lq)


def new_grid(width_px, height_px):
    """[across, up] grid points for a shape this big on screen (its first stroke)."""
    w, h = max(float(width_px), 1.0), max(float(height_px), 1.0)
    if w >= h:
        return [GRID_LONG, int(min(GRID_LONG, max(GRID_SHORT, round(GRID_LONG * h / w))))]
    return [int(min(GRID_LONG, max(GRID_SHORT, round(GRID_LONG * w / h)))), GRID_LONG]


def empty(grid):
    return np.zeros((grid[1], grid[0], 2), np.float64)


def sample(a, x, y):
    """The map a at spots (x, y) of the colour box (0..1; past it: the edge's), blended between grid points."""
    gy, gx = a.shape[:2]
    fx = np.clip(np.asarray(x, np.float64), 0.0, 1.0) * (gx - 1)
    fy = np.clip(np.asarray(y, np.float64), 0.0, 1.0) * (gy - 1)
    i0 = np.minimum(fx.astype(np.int64), gx - 2)
    j0 = np.minimum(fy.astype(np.int64), gy - 2)
    tx, ty = (fx - i0)[..., None], (fy - j0)[..., None]
    return ((a[j0, i0] * (1 - tx) + a[j0, i0 + 1] * tx) * (1 - ty)
            + (a[j0 + 1, i0] * (1 - tx) + a[j0 + 1, i0 + 1] * tx) * ty)


def touched(a, x, y):
    """Which spots (x, y) sit in a grid cell with something pushed at one of its corners (the rest fetch from
    themselves: left alone, most notes of a big shape)."""
    nz = (a != 0).any(2)
    cell = nz[:-1, :-1] | nz[1:, :-1] | nz[:-1, 1:] | nz[1:, 1:]
    gy, gx = a.shape[:2]
    i = np.minimum((np.clip(x, 0.0, 1.0) * (gx - 1)).astype(np.int64), gx - 2)
    j = np.minimum((np.clip(y, 0.0, 1.0) * (gy - 1)).astype(np.int64), gy - 2)
    return cell[j, i]


def liquify_from(at, rc, box, own):
    """(the notes moved (their places in at), middle in ticks, key) each one's colour comes from under rc's Liquify
    (stir_from's way: the edge choices alike). Notes where nothing was pushed are left out."""
    from notes.recolour import from_unit, rows_of, unit_xy
    lq = full(clean_liquify(rc.get("liquify")))
    a = decode(lq["grid"], lq["map"])
    s, e, k = (np.asarray(at[:, c], np.float64) for c in (0, 1, 2))
    mid = (s + e) / 2
    edge = lq["edge"]
    if edge in ("wrap", "bend"):
        rows = rows_of(at, 0.0)
        t0, t1 = float(at[:, 0].min()), float(at[:, 1].max())
    if edge == "bend":
        mid = rows.stretched(mid, k, t0, t1)
    x, y = unit_xy(mid, k, rc, box, own)
    idx = np.flatnonzero(touched(a, x, y))
    x, y = x[idx], y[idx]
    d = sample(a, x, y) * (lq["strength"] / 100)
    x, y = x + d[:, 0], y + d[:, 1]
    if edge != "escape":
        out = lambda v: (v < -1e-9) | (v > 1 + 1e-9)  # noqa: E731 (a hair past the edge stays on it)
        x = np.clip(np.where(out(x), np.mod(x, 1.0), x), 0, 1)
        y = np.clip(np.where(out(y), np.mod(y, 1.0), y), 0, 1)
    else:
        x, y = np.clip(x, 0, 1), np.clip(y, 0, 1)
    mid, key = from_unit(x, y, rc, box, own)
    if edge == "wrap":
        mid = rows.wrapped(mid, key)
    elif edge == "bend":
        mid = rows.unstretched(mid, key, t0, t1)
    return idx, mid, key


def liquified(notes, at, low, now, rc, which, pals, pick, box, own):
    """The colours now after rc's Liquify: each note takes the colour of the note found where the map fetches from
    (recolour.stirred's way: colour sets "stay", Per chord / run by the group's first note, two notes on a key kept
    apart). Hidden (eye off) or none: now as it is."""
    from notes.recolour import MOST, kept_apart, leaders, nearest
    lq = clean_liquify(rc.get("liquify"))
    if not lq or lq.get("on") is False or not len(at) or not lq.get("strength", 100.0):
        return now
    idx, mid, key = liquify_from(at, rc, box, own)
    if not len(idx):
        return now
    # (own=False: the spots are only some notes'; a spot that didn't move finds its own note anyway, the nearest)
    src = nearest(at, mid, key, own=False)
    new = np.array(now)
    move = len(pals) < 2
    if move:
        new[idx] = now[src]
    else:  # (each note keeps its block of channels and takes the found note's colour in it, as Stir's "stay")
        per = rc.get("most", MOST)
        pool = np.concatenate(pals)
        set_of = np.repeat(np.arange(len(pals)), [len(p) for p in pals])
        o = np.argsort(pool, kind="stable")
        want = now[idx] // per * per + now[src] % per
        i = o[np.clip(np.searchsorted(pool[o], want), 0, len(pool) - 1)]
        new[idx] = np.where(pool[i] == want, want, now[src])
    if rc.get("by"):
        new = new[leaders(at, rc["by"])]
    if move:
        return kept_apart(notes, low, new, pals[0], pick) if len(pals[0]) >= 2 else new
    mine = set_of[o[np.clip(np.searchsorted(pool[o], new), 0, len(pool) - 1)]]
    for s, p in enumerate(pals):
        m = np.flatnonzero(mine == s)
        if len(p) >= 2 and len(m):
            new[m] = kept_apart(notes[m], low[m], new[m], p, pick[m])
    return new


def blend(x, y, f):
    """Add between's step f (0 = x's end, 1 = y's) of two Liquifys (either may be None = nothing pushed: the other
    fades in / out): the maps blended spot by spot (on the nearer end's grid), strength blended, the rest the nearer
    end's. None when nothing is left pushed."""
    x, y = clean_liquify(x), clean_liquify(y)
    if not x and not y:
        return None
    near = (x if f < 0.5 else y) or x or y
    g = near["grid"]
    gx, gy = g
    jj, ii = np.mgrid[0:gy, 0:gx]
    u, v = ii / (gx - 1), jj / (gy - 1)

    def on_grid(d):
        if not d:
            return np.zeros((gy, gx, 2))
        a = decode(d["grid"], d["map"])
        return a if d["grid"] == g else sample(a, u, v)
    a = on_grid(x) * (1 - f) + on_grid(y) * f
    sx = (x or y).get("strength", DEFAULTS["strength"])
    sy = (y or x).get("strength", DEFAULTS["strength"])
    out = dict(near, grid=list(g), map=encode(a), strength=sx + (sy - sx) * f)
    return clean_liquify(out)


# ---- brushes ----

def weight(q, density):
    """Brush strength at q = distance / radius (0 middle, 1 rim): full up to the hard part (density %), then fading
    smoothly to 0 at the rim; nothing past it."""
    hard = min(max(density / 100.0, 0.0), 1.0)
    q = np.asarray(q, np.float64)
    if hard >= 1.0:
        return (q <= 1.0).astype(np.float64)
    f = np.clip((q - hard) / (1.0 - hard), 0.0, 1.0)
    return np.where(q <= 1.0, 1.0 - f * f * (3 - 2 * f), 0.0)


def _reach(a, c, r, J):
    """The grid points a brush of radius r px at c (colour box units) can touch: (rows slice, cols slice), or None."""
    gy, gx = a.shape[:2]
    ex = r * float(np.hypot(J[0][0], J[0][1]))
    ey = r * float(np.hypot(J[1][0], J[1][1]))
    i0, i1 = int(np.floor((c[0] - ex) * (gx - 1))), int(np.ceil((c[0] + ex) * (gx - 1)))
    j0, j1 = int(np.floor((c[1] - ey) * (gy - 1))), int(np.ceil((c[1] + ey) * (gy - 1)))
    i0, j0 = max(i0, 0), max(j0, 0)
    i1, j1 = min(i1, gx - 1), min(j1, gy - 1)
    if i0 > i1 or j0 > j1:
        return None
    return slice(j0, j1 + 1), slice(i0, i1 + 1)


def dab(a, tool, c, r, J, pressure=50.0, density=50.0, vec=(0.0, 0.0), turn=1, frozen=None):
    """The map a after one brush dab (a new array; a itself never changed). tool: TOOLS. c = the brush middle in
    colour box units, r = its radius in screen px, J = [[dx/dpx, dx/dpy], [dy/dpx, dy/dpy]] (colour box units per
    screen pixel there), pressure / density in %, vec = the mouse's move in px since the last dab (Push), turn = +1 /
    -1 (Twirl's way round), frozen = the freeze mask (up, across) 0..1 or None."""
    a = np.array(a, np.float64)
    J = np.asarray(J, np.float64)
    got = _reach(a, c, r, J)
    if got is None:
        return a
    rs, cs = got
    gy, gx = a.shape[:2]
    jj, ii = np.mgrid[rs, cs]
    px, py = ii / (gx - 1), jj / (gy - 1)
    ux, uy = px - c[0], py - c[1]  # (box units from the middle)
    try:
        Ji = np.linalg.inv(J)
    except np.linalg.LinAlgError:
        return a
    sx, sy = Ji[0, 0] * ux + Ji[0, 1] * uy, Ji[1, 0] * ux + Ji[1, 1] * uy  # (screen px from the middle)
    w = weight(np.hypot(sx, sy) / max(r, 1e-9), density)
    if frozen is not None:
        w = w * (1.0 - np.clip(frozen[rs, cs], 0.0, 1.0))
    if not w.any():
        return a
    p = pressure / 100.0
    old = a[rs, cs]
    if tool == "restore":
        a[rs, cs] = old * (1.0 - (w * min(0.25 * p, 1.0))[..., None])
        return a
    if tool == "smooth":
        pad = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
        blur = sum(pad[rs.start + dj:rs.stop + dj, cs.start + di:cs.stop + di] for dj in (0, 1, 2) for di in (0, 1, 2)) / 9
        a[rs, cs] = old + (blur - old) * (w * min(0.5 * p, 1.0))[..., None]
        return a
    if tool == "push":
        vx = J[0, 0] * vec[0] + J[0, 1] * vec[1]
        vy = J[1, 0] * vec[0] + J[1, 1] * vec[1]
        k = w * min(p * 1.0, 1.0)
        qx, qy = px - k * vx, py - k * vy
    elif tool == "twirl":
        ang = turn * w * 0.12 * p  # (radians a dab, most in the middle)
        ca, sa = np.cos(ang), np.sin(ang)
        rx, ry = sx * ca - sy * sa, sx * sa + sy * ca  # (turned on screen, so it stays round)
        qx = c[0] + J[0, 0] * rx + J[0, 1] * ry
        qy = c[1] + J[1, 0] * rx + J[1, 1] * ry
    elif tool in ("pinch", "bulge"):
        f = w * 0.06 * p
        f = 1.0 - f if tool == "bulge" else 1.0 + f  # (bulge: the middle's colours swell out = each spot looks inward)
        qx, qy = c[0] + ux * f, c[1] + uy * f
    else:
        return a
    d = sample(a, qx, qy)  # (the old map at the moved spot: a isn't changed until below)
    a[rs, cs, 0] = qx + d[..., 0] - px
    a[rs, cs, 1] = qy + d[..., 1] - py
    return a


def straight(a, b, spacing):
    """Dab spots from a to b (screen px) every spacing px, a left out (Shift+click: a straight stroke)."""
    n = max(1, int(np.ceil(np.hypot(b[0] - a[0], b[1] - a[1]) / max(spacing, 1e-9))))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)]
