"""Recolour's Warp (user 2026-11-09, PLAN.md; like image editors' Warp): a grid of points over a shape's colours;
dragging a point bends the colours with it like rubber. Notes never move or change length; only which colour each
one has changes, like the Stir and the Liquify.

THE GRID (rc["warp"]): cols x rows cells over the shape's colour box (recolour.unit_xy's 0..1 box: flipped / turned
with the shape, a piece's whole's box), so (cols + 1) x (rows + 1) points, each one moved by an offset [dx, dy] in
that box. Cells across and up are set apart (Custom grid size, user 2026-11-09: long thin shapes). Saved as the
points, never baked (dragged again later; the colours under it can change and they bend the same way). Applied after
every stir, before the Liquify (user 2026-11-09: fixed order Stir -> Warp -> Liquify; "order added" = its own rework
later, planned-features.md).

Saved as {"cols": cells across, "rows": cells up, "pts": [[dx, dy], ...] row by row from the bottom left ((cols + 1)
x (rows + 1) of them), "smooth": False (only for Straight cells), "strength": % (only != 100), "on": False (only when
hidden), "edge": STIR_EDGES (only != "escape"), "edges": "smooth" (only for Colour edges: Smooth), "folds": "under"
(only for Folds: pulled part underneath)}. No point moved = no Warp
(clean_warp: None). Never released before this layout: no older layout is read (user 2026-11-09).

THE BEND: a point moved = the colour that was at its place is now shown where it went (forward, like the editors).
Between the points the offsets are blended, each cell from its own 4 corners only, so a moved point bends just the
4 cells round it: Smooth = eased curved patches (the editors' Warp: the point's surroundings move with it, smooth
across grid lines; user 2026-11-09 after Catmull-Rom's wider reach was "much, much laggier"), Straight cells =
bilinear (folds along the grid lines). Each spot needs where its colour comes FROM, so the bend is turned round
(Newton steps on a fine grid, inverse_map) into a Liquify-style "where it comes from" map, and the Liquify's lookup
(liquify._where / found / _taken / _apart) does the rest: At the edges, colour sets, Per chord / run, two notes on
a key kept apart, the gate flip's shown places alike. EDGES (user 2026-11-09: "make it a setting"): Sharp (default,
user after Smooth lagged) = the nearest note (stair steps on stretched cells) / Smooth = liquify.found's vote
(stretched spots: looks round where it fetches from; squashed: the most common colour over what the note covers).

FOLDS (user 2026-11-09, PLAN C4): a point dragged past its neighbours folds the grid over itself, so a spot there
gets its colour from two (or more) places. Newton alone took whichever it landed on = torn patches. Now, where any
cell folds (_folds: a piece of it turned over), each spot there is solved in EVERY piece covering it and one rule
picks: the PULLED part (the cell whose 4 corner points moved farther, all told; inside one folded cell the piece
that moved farther) on top ("top", default) or underneath ("under"); like folded paper. Never hangs on drag history.
Grids without a fold: unchanged. (Image editors' Warp: the cell drawn later covers; no choice.)
"""

import json

import numpy as np

from files.speed import compiled

MOST_CELLS = 20  # (cells across / up at most: the window's Custom boxes, a file too)
MOST_MOVE = 4.0  # (a point moves at most this many boxes)
MOST_STRENGTH = 400.0
DEFAULTS = {"smooth": True, "strength": 100.0, "on": True, "edge": "escape", "edges": "sharp", "folds": "top"}
EDGES = ("sharp", "smooth")  # (the window's Edges dropdown by place; Sharp the default, user 2026-11-09: Smooth laggy)
FOLDS = ("top", "under")  # (the window's Folds dropdown by place: the pulled part on top / underneath)
PER_CELL = 32  # (the turned-round map's steps per cell...)
MOST_MAP = 257  # (... at most this many a side: 8 cells; more cells share them, 12+ a cell at 20: a drag on a
# 20 x 20 grid took 0.37 s a move at 513, the bend between points stays smooth)
STEPS = 25  # (Newton steps at most; most spots settle in 3-5)
NEWTON_FAST = 64  # (spots: from this many the compiled steps (fastloops.warp_newton), fewer = NumPy's, the same)
_MAPS = {}  # the warp's json -> its turned-round map (read-only); the last few kept


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and x == x and abs(x) != float("inf")


def clean_warp(d):
    """A saved rc["warp"] -> its clean form (see the top), or None (no Warp: nothing moved, or not readable)."""
    if not isinstance(d, dict):
        return None
    c, r, pts = d.get("cols"), d.get("rows"), d.get("pts")
    if not (cells_ok(c) and cells_ok(r)) or not isinstance(pts, list):
        return None
    c, r = int(c), int(r)
    if len(pts) != (c + 1) * (r + 1) or not all(isinstance(p, list) and len(p) == 2 and all(number(v) for v in p)
                                                for p in pts):
        return None
    clean = [[round(min(max(float(v), -MOST_MOVE), MOST_MOVE), 6) + 0.0 for v in p] for p in pts]
    if not any(v for p in clean for v in p):
        return None
    out = {"cols": c, "rows": r, "pts": clean}
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
    if d.get("edges") == "smooth":
        out["edges"] = "smooth"
    if d.get("folds") == "under":
        out["folds"] = "under"
    return out


def cells_ok(v):
    """A count of cells a file / box may hold (whole, 1..MOST_CELLS)."""
    return number(v) and v == int(v) and 1 <= v <= MOST_CELLS


def full(wp):
    """Every setting of a clean Warp filled in."""
    return dict(DEFAULTS, **wp)


def size(wp):
    """(cells across, cells up) of a Warp."""
    return wp["cols"], wp["rows"]


def empty(cols, rows):
    """A grid of cols x rows cells with no point moved (as saved: the window's start)."""
    cols, rows = int(cols), int(rows)
    return {"cols": cols, "rows": rows, "pts": [[0.0, 0.0] for _ in range((cols + 1) * (rows + 1))]}


def offsets(wp, strength=True):
    """A clean Warp's point offsets as an array (rows + 1 up, cols + 1 across, 2), times its strength
    (strength=True)."""
    c, r = size(wp)
    P = np.asarray(wp["pts"], np.float64).reshape(r + 1, c + 1, 2)
    if strength:
        P = P * (full(wp)["strength"] / 100.0)
    return P


def _eased(P, x, y, slopes=False):
    """The offsets P (rows up, columns across, 2) at spots (x, y) of the box (0..1; past it: the edge's) for Smooth:
    each cell its own curved patch from its 4 corner points, eased in and out (3t^2 - 2t^3 each way), so a moved
    point bends ONLY the 4 cells round it (image editors' Warp, user 2026-11-09: Catmull-Rom's 2-cell reach made a
    4 x 4 drag repaint the whole shape). Each point's surroundings move with it as one piece (its handles go along,
    as the editors' do) and the bend flows smoothly across every grid line. slopes: also how they change along x and
    y ((value, d/dx, d/dy); 0 past the box), exact, for Newton's steps."""
    gy, gx = P.shape[:2]

    def parts(v, g):
        v = np.asarray(v, np.float64)
        f = np.clip(v, 0.0, 1.0) * (g - 1)
        i = np.minimum(f.astype(np.int64), g - 2)
        t = f - i
        s = t * t * (3.0 - 2.0 * t)
        if not slopes:
            return i, s, None
        return i, s, 6.0 * t * (1.0 - t) * ((v >= 0.0) & (v <= 1.0)) * float(g - 1)
    i, sx, dx = parts(x, gx)
    j, sy, dy = parts(y, gy)
    sx_, sy_ = sx[..., None], sy[..., None]
    a, b, c, d = P[j, i], P[j, i + 1], P[j + 1, i], P[j + 1, i + 1]
    lo, hi = a + (b - a) * sx_, c + (d - c) * sx_
    out = lo + (hi - lo) * sy_
    if not slopes:
        return out
    ddx = ((b - a) + ((d - c) - (b - a)) * sy_) * dx[..., None]
    ddy = (hi - lo) * dy[..., None]
    return out, ddx, ddy


def bend_at(P, x, y, smooth=True):
    """How far the spots (x, y) of the box move under the point offsets P (forward: where their colour goes)."""
    if smooth:
        return _eased(P, x, y)
    from notes.liquify import sample
    return sample(P, x, y)


def map_size(wp):
    """(across, up) points of a Warp's turned-round map: PER_CELL steps a cell, at most MOST_MAP a side."""
    c, r = size(wp)
    return min(MOST_MAP, PER_CELL * c + 1), min(MOST_MAP, PER_CELL * r + 1)


def inverse_map(wp, quick=False):
    """A clean Warp turned round: (up, across, 2) Liquify-style map, each grid spot p = how far (in box units) the
    colour it shows comes from (s with s + bend(s) = p, so the map = s - p). Read-only, the last few kept. Where the
    bend folds over itself (a point dragged past its neighbours) the nearest settled answer is used.
    quick (a point being dragged, roll_warp): a few points moved since the last quick map of this grid = only the
    spots they can reach worked out again, the rest kept (a 20 x 20 grid's whole map took 0.33 s a move). Never kept
    for the notes themselves: let go works the whole map out, so a shape's colours never hang on how it was edited
    (Newton's last digits can differ near a moved point's reach)."""
    f = full(wp)
    key = json.dumps([size(wp), wp["pts"], f["smooth"], f["strength"], f["folds"]])
    got = _MAPS.get(key)
    if got is not None:
        return got
    P = offsets(wp)
    smooth, top = f["smooth"], f["folds"] == "top"
    fold = _folds(P, smooth, top)  # (None: nothing folds, the plain Newton answers stand)
    gx, gy = map_size(wp)
    last = _LAST.get((size(wp), smooth, top)) if quick else None
    parts = _reach(last[0], P, smooth, gx, gy) if last is not None else None
    if parts is None:
        jj, ii = np.mgrid[0:gy, 0:gx]
        px, py = (ii / (gx - 1)).ravel(), (jj / (gy - 1)).ravel()
        bx, by = _solve(P, px, py, smooth, fold)
        m = np.stack([bx - px, by - py], 1).reshape(gy, gx, 2)
    else:  # (each moved point's own reach: mirrored twins sit far apart, one box round them all = most of the map;
        # their parts may overlap: every spot in any of them solved once, in one go)
        m = np.array(last[1])
        want = np.zeros((gy, gx), bool)
        for part in parts:
            want[part] = True
        jj, ii = np.nonzero(want)
        if len(jj):
            px, py = ii / (gx - 1), jj / (gy - 1)
            bx, by = _solve(P, px, py, smooth, fold)
            m[jj, ii] = np.stack([bx - px, by - py], 1)
    m[np.abs(m) < 1e-12] = 0.0  # (spots the bend never reached fetch from themselves exactly)
    m.setflags(write=False)
    if quick:
        _LAST.clear()
        _LAST[(size(wp), smooth, top)] = (P, m)
        return m
    if len(_MAPS) > 8:
        _MAPS.pop(next(iter(_MAPS)))
    _MAPS[key] = m
    return m


_LAST = {}  # ((cols, rows), smooth) -> (the point offsets with strength, their map) of the last quick map


def _reach(P0, P, smooth, gx, gy):
    """[(rows slice, columns slice), ...] of the map spots that can change when the offsets go from P0 to P (a few
    points moved: one part each, a mirrored drag's twins too), or None = all of them (many moved, another size). A
    moved point bends only the 4 cells round it (Smooth and Straight cells alike); a spot whose colour comes from
    there is at most the largest bend there away. Each spot is worked out on its own, so parts may overlap."""
    if P0.shape != P.shape:
        return None
    moved = np.argwhere((P0 != P).any(2))
    if len(moved) > 4:
        return None
    return [_reach_one(P0, P, j, i, gx, gy) for j, i in moved]


def _reach_one(P0, P, j, i, gx, gy):
    """_reach's part for the one moved point (j, i)."""
    rows, cols = P.shape[0] - 1, P.shape[1] - 1
    j0, i0 = j - 1, i - 1
    j1, i1 = j + 1, i + 1
    # (the bend inside those cells is a blend of their corners, never past them: no overshoot)
    near = (slice(max(j0, 0), j1 + 1), slice(max(i0, 0), i1 + 1))
    both = np.concatenate([P0[near].reshape(-1, 2), P[near].reshape(-1, 2), np.zeros((1, 2))])
    lo, hi = both.min(0), both.max(0)  # (how far a colour from there can land, each way)
    u0, u1 = max(i0, 0) / cols + lo[0], min(i1, cols) / cols + hi[0]
    v0, v1 = max(j0, 0) / rows + lo[1], min(j1, rows) / rows + hi[1]
    if j0 <= 0:  # (past the box's edge the edge's bend holds: spots out there are reached too)
        v0 = -np.inf
    if i0 <= 0:
        u0 = -np.inf
    if j1 >= rows:
        v1 = np.inf
    if i1 >= cols:
        u1 = np.inf

    def span(a, b, g):
        lo = int(np.floor(max(a, 0.0) * (g - 1))) - 1
        hi = int(np.ceil(min(b, 1.0) * (g - 1))) + 2
        return slice(max(lo, 0), min(hi, g))
    return span(v0, v1, gy), span(u0, u1, gx)


def _solve(P, px, py, smooth, fold=None):
    """Where the colour each spot (px, py) shows comes from under the offsets P: s with s + bend(s) = p (Newton
    steps from p - bend(p)). fold (_folds; None = the grid folds nowhere): spots a folded piece covers are solved in
    every piece covering them and the fold rule picks (FOLDS at the top)."""
    d = bend_at(P, px, py, smooth)
    bx, by, err = _newton(P, px, py, smooth, px - d[:, 0], py - d[:, 1])
    # (Newton that never settled (a far stretch next to a fold): left as it was, it took the nearest wrong spot =
    # torn specks beside the fold; those spots are solved piece by piece too)
    lost = err > 1e-7
    if fold is not None or lost.any():
        _fold_pick(P, px, py, smooth, fold or _folds(P, smooth, True, always=True), bx, by, lost)
    return bx, by


FOLD_LOOK = 9  # (spots a cell side where _folds looks whether a piece turned over / how far a cell's colours land)
FOLD_PIECES = 4  # (a folded cell solved in this many pieces a side: inside it, two layers may come from one cell)
FAR = 8.0  # (a box edge's cell reaches this far past the box: the edge's bend holds out there)
GIVE_UP_STEP, GIVE_UP_FAR = 6, 0.02  # (a fold's piece: still this far off after this many steps = not in it)


def _folds(P, smooth, top, always=False):
    """Where the grid of offsets P folds over itself, or None (nowhere: no spot has two sources). Else a dict:
    "units" = pieces (x0, x1, y0, y1) of the box each solved on its own (every cell; a folded cell cut in
    FOLD_PIECES x FOLD_PIECES), "land" = the box (x0, x1, y0, y1) each one's colours land in, "rank" = its place
    in the fold rule (higher = on top), "fixed" = the boxes of the folded pieces' landing (only spots in one get
    the rule)."""
    rows, cols = P.shape[0] - 1, P.shape[1] - 1
    n = FOLD_LOOK
    t = np.linspace(0.0, 1.0, n)
    # (each cell's forward picture on n x n spots: a small quad there turned over (area <= 0) = a fold)
    cu = (np.arange(cols)[:, None] + t[None]) / cols  # (cols, n)
    cv = (np.arange(rows)[:, None] + t[None]) / rows  # (rows, n)
    X = np.broadcast_to(cu[None, None, :, :], (rows, n, cols, n))  # (rows, n up, cols, n across)
    Y = np.broadcast_to(cv[:, :, None, None], (rows, n, cols, n))
    d = bend_at(P, X.ravel(), Y.ravel(), smooth).reshape(rows, n, cols, n, 2)
    fx, fy = X + d[..., 0], Y + d[..., 1]
    ax, ay = fx[:, :-1, :, 1:] - fx[:, :-1, :, :-1], fy[:, :-1, :, 1:] - fy[:, :-1, :, :-1]
    bx, by = fx[:, 1:, :, :-1] - fx[:, :-1, :, :-1], fy[:, 1:, :, :-1] - fy[:, :-1, :, :-1]
    area = ax * by - ay * bx  # (rows, n-1, cols, n-1)
    flipped = (area <= 0).any((1, 3))  # (rows, cols)
    if not flipped.any() and not always:
        return None
    moved = np.hypot(P[..., 0], P[..., 1])
    rank = moved[:-1, :-1] + moved[:-1, 1:] + moved[1:, :-1] + moved[1:, 1:]
    order = np.empty(rows * cols, np.int64)  # (by how far the cell's corners moved; a tie: grid order)
    order[np.lexsort((np.arange(rows * cols), rank.ravel()))] = np.arange(rows * cols)
    k = FOLD_PIECES
    units, land, ranks, fixed = [], [], [], []
    per = (n - 1) // k  # (looks a piece side: n - 1 must split evenly)
    for j in range(rows):
        for i in range(cols):
            base = order[j * cols + i] * (k * k + 1)
            x0, x1, y0, y1 = i / cols, (i + 1) / cols, j / rows, (j + 1) / rows
            if not flipped[j, i]:
                pieces = [(0, n - 1, 0, n - 1, 0)]
            else:  # (pieces ranked inside the cell by how far they moved: the farther one is "more pulled")
                mv = np.hypot(d[j, :, i, :, 0], d[j, :, i, :, 1])
                pieces = []
                for b in range(k):
                    for a in range(k):
                        pieces.append((a * per, (a + 1) * per, b * per, (b + 1) * per,
                                       float(mv[b * per:(b + 1) * per + 1, a * per:(a + 1) * per + 1].mean())))
                inner = np.argsort([p[4] for p in pieces], kind="stable")
                pieces = [pieces[m][:4] + (int(r) + 1,) for r, m in zip(np.argsort(inner), range(len(pieces)))]
            for a0, a1, b0, b1, sub in pieces:
                px_ = fx[j, b0:b1 + 1, i, a0:a1 + 1]
                py_ = fy[j, b0:b1 + 1, i, a0:a1 + 1]
                pad = 0.02 / max(cols, rows)  # (the curved patch between the looks bulges a hair)
                L = [px_.min() - pad, px_.max() + pad, py_.min() - pad, py_.max() + pad]
                u = [x0 + (x1 - x0) * a0 / (n - 1), x0 + (x1 - x0) * a1 / (n - 1),
                     y0 + (y1 - y0) * b0 / (n - 1), y0 + (y1 - y0) * b1 / (n - 1)]
                # (a piece on the box's edge reaches on past it: out there the edge's bend holds, so its colours land
                # out there too)
                if a0 == 0 and i == 0:
                    u[0], L[0] = -FAR, -np.inf
                if a1 == n - 1 and i == cols - 1:
                    u[1], L[1] = 1 + FAR, np.inf
                if b0 == 0 and j == 0:
                    u[2], L[2] = -FAR, -np.inf
                if b1 == n - 1 and j == rows - 1:
                    u[3], L[3] = 1 + FAR, np.inf
                units.append(u)
                land.append(L)
                ranks.append(base + sub)
                if flipped[j, i]:
                    fixed.append(L)
    ranks = np.asarray(ranks, np.int64)
    return {"units": np.asarray(units), "land": np.asarray(land), "rank": ranks if top else -ranks,
            "fixed": np.asarray(fixed)}


def _fold_pick(P, px, py, smooth, fold, bx, by, lost=None):
    """_solve's fold rule: spots a folded piece's colours land on (and lost ones: plain Newton never settled) solved
    in every piece landing there (Newton kept inside it), the highest ranked answer wins (bx, by changed in place).
    A spot no piece answers keeps Newton's."""
    F = fold["fixed"]
    inside = np.zeros(len(px), bool) if lost is None else lost.copy()
    for x0, x1, y0, y1 in F:
        inside |= (px >= x0) & (px <= x1) & (py >= y0) & (py <= y1)
    s = np.flatnonzero(inside)
    if not len(s):
        return
    U, L, R = fold["units"], fold["land"], fold["rank"]
    sp, up = [], []  # (spot, piece) pairs: each piece landing on the spot
    qx, qy = px[s], py[s]
    for k in range(len(U)):
        x0, x1, y0, y1 = L[k]
        m = np.flatnonzero((qx >= x0) & (qx <= x1) & (qy >= y0) & (qy <= y1))
        if len(m):
            sp.append(s[m])
            up.append(np.full(len(m), k))
    if not sp:
        return
    sp, up = np.concatenate(sp), np.concatenate(up)
    # (a spot only one piece can land on has one answer: Newton's stands when it settled; only 2+ pieces / lost
    # spots are solved piece by piece (most of a fold's box: quick while dragging))
    many = np.bincount(sp, minlength=len(px)) >= 2
    if lost is not None:
        many |= lost
    keep = many[sp]
    sp, up = sp[keep], up[keep]
    if not len(sp):
        return
    lo_x, hi_x, lo_y, hi_y = U[up, 0], U[up, 1], U[up, 2], U[up, 3]
    # (start in the piece's middle, its own part of the box; past the box: the box's edge)
    cx = (np.clip(lo_x, 0, 1) + np.clip(hi_x, 0, 1)) / 2
    cy = (np.clip(lo_y, 0, 1) + np.clip(hi_y, 0, 1)) / 2
    sx, sy, err = _newton(P, px[sp], py[sp], smooth, cx, cy, (lo_x, hi_x, lo_y, hi_y))
    ok = (err < 1e-7) & (sx >= lo_x - 1e-9) & (sx <= hi_x + 1e-9) & (sy >= lo_y - 1e-9) & (sy <= hi_y + 1e-9)
    if not ok.any():
        return
    sp, sx, sy, r = sp[ok], sx[ok], sy[ok], R[up[ok]]
    o = np.lexsort((-r, sp))  # (each spot's answers, highest rank first)
    sp, sx, sy = sp[o], sx[o], sy[o]
    first = np.ones(len(sp), bool)
    first[1:] = sp[1:] != sp[:-1]
    bx[sp[first]], by[sp[first]] = sx[first], sy[first]


def _newton(P, px, py, smooth, sx, sy, bounds=None):
    """Newton steps from (sx, sy) toward s + bend(s) = (px, py) under the offsets P: (x, y, how far off) of the
    best answer each spot reached. bounds (x0, x1, y0, y1 arrays): kept inside them (a fold's piece)."""
    sx, sy = np.array(sx, np.float64), np.array(sy, np.float64)
    fast = compiled("warp_newton") if len(px) >= NEWTON_FAST else None
    if fast:  # (the compiled twin: each spot's very same steps, files/fastloops.warp_newton; user 11-09: heavy
        # per-spot work compiled)
        to = np.ascontiguousarray
        n = len(px)
        bnd = to(np.column_stack(bounds), np.float64) if bounds is not None else np.zeros((1, 4))
        bx, by, best = np.empty(n), np.empty(n), np.empty(n)
        fast.warp_newton(to(P, np.float64), to(px, np.float64), to(py, np.float64), bool(smooth), sx, sy, bnd,
                         bounds is not None, STEPS, GIVE_UP_STEP, GIVE_UP_FAR, bx, by, best)
        return bx, by, best
    best = np.full(len(px), np.inf)
    bx, by = sx.copy(), sy.copy()
    live = np.arange(len(px))
    h = 1e-5
    for step in range(STEPS):
        x, y = sx[live], sy[live]
        if smooth:  # (the slopes exact, with the values: one lookup a step)
            d0, dx, dy = _eased(P, x, y, slopes=True)
        else:
            d0 = bend_at(P, x, y, smooth)
        rx, ry = x + d0[:, 0] - px[live], y + d0[:, 1] - py[live]
        err = np.sqrt(rx * rx + ry * ry)  # (not hypot: the compiled twin's sqrt gives the very same bits)
        better = err < best[live]
        k = live[better]
        best[k], bx[k], by[k] = err[better], x[better], y[better]
        go = err > 1e-9
        if bounds is not None and step == GIVE_UP_STEP:  # (a piece the spot isn't in: stuck at its edge, far off)
            go &= err < GIVE_UP_FAR
        if not go.any():
            break
        live, x, y, rx, ry, d0 = live[go], x[go], y[go], rx[go], ry[go], d0[go]
        if smooth:
            dx, dy = dx[go], dy[go]
        else:
            dx = (bend_at(P, x + h, y, smooth) - d0) / h
            dy = (bend_at(P, x, y + h, smooth) - d0) / h
        a, b, c, e = 1 + dx[:, 0], dy[:, 0], dx[:, 1], 1 + dy[:, 1]
        det = a * e - b * c
        ok = np.abs(det) > 1e-6
        safe = np.where(ok, det, 1.0)
        ux = np.where(ok, (e * rx - b * ry) / safe, rx)
        uy = np.where(ok, (a * ry - c * rx) / safe, ry)
        far = np.sqrt(ux * ux + uy * uy)
        cut = np.where(far > 0.2, 0.2 / np.maximum(far, 1e-300), 1.0)
        nx, ny = x - ux * cut, y - uy * cut
        if bounds is not None:
            nx = np.clip(nx, bounds[0][live], bounds[1][live])
            ny = np.clip(ny, bounds[2][live], bounds[3][live])
        sx[live], sy[live] = nx, ny
    return bx, by, best


def warped(notes, at, low, now, rc, which, pals, pick, box, own):
    """The colours now after rc's Warp: each note takes the colour of the note found where the bend fetches from
    (liquify.liquified's way, with the Warp's own edge choice). Hidden (eye off), strength 0 or none: now as it is."""
    from notes import liquify as lq
    from notes.recolour import leaders
    wp = clean_warp(rc.get("warp"))
    if not wp or wp.get("on") is False or not wp.get("strength", 100.0) or not len(at):
        return now
    a = inverse_map(wp)
    shown = lq.shown_places(notes, at, rc)
    f = full(wp)
    w = lq._where(shown, rc, box, own, {"strength": 100.0, "on": True, "edge": f["edge"],
                                       "soft": f["edges"] == "smooth"})
    idx = np.flatnonzero(lq.touched(a, w["x"], w["y"]))
    if not len(idx):
        return now
    src = lq.found(w, a, shown, idx, w["x"][idx], w["y"][idx], rc, box, own, now)
    new = np.array(now)
    new[idx] = lq._taken(now, idx, src, pals, rc)
    if rc.get("by"):
        new = new[leaders(at, rc["by"])]
    return lq._apart(notes, low, new, pals, pick)


GRIDS = ((3, 3), (4, 4), (5, 5))  # (the window's ready Grid size choices, (across, up); then Custom…)


def refit(wp, cols, rows):
    """A clean Warp moved onto a grid of cols x rows cells (the window's Grid size changed; user 2026-11-09: re-fit,
    nothing lost): the new points chosen so their bend matches the old one as closely as it can over the whole box
    (least squares on REFIT_STEPS spots a cell: the bend is linear in the points, so one solve; across and up are
    apart for both Smooth and Straight cells, so it splits into one small solve each way); the other settings
    kept. A bend the new grid can't make (a peak between its points) comes out a little softer. None when nothing is
    left moved."""
    wp = clean_warp(wp)
    if not wp:
        return None
    cols, rows = int(cols), int(rows)
    c0, r0 = size(wp)
    if (cols, rows) == (c0, r0):
        return wp
    smooth = full(wp)["smooth"]
    sx, sy = REFIT_STEPS * max(cols, c0) + 1, REFIT_STEPS * max(rows, r0) + 1
    yy, xx = np.mgrid[0:sy, 0:sx]
    x, y = (xx / (sx - 1)).ravel(), (yy / (sy - 1)).ravel()
    want = bend_at(offsets(wp, strength=False), x, y, smooth)  # (sy * sx, 2)
    # (the bend = sum of points x (weight across) x (weight up): the weights each way found from a 1-D grid, so the
    # solve is Bx' P By = want with B = each way's weights; least squares each way in turn = the same as all at once)
    Bx = _weights(xx[0] / (sx - 1), cols, smooth)  # (sx, cols + 1)
    By = _weights(yy[:, 0] / (sy - 1), rows, smooth)  # (sy, rows + 1)
    W = want.reshape(sy, sx, 2)
    px, py = np.linalg.pinv(Bx), np.linalg.pinv(By)
    P = np.einsum("aj,jkd,bk->abd", py, W, px)
    return clean_warp(dict(wp, cols=cols, rows=rows, pts=P.reshape(-1, 2).tolist()))


def _weights(t, cells, smooth):
    """(len(t), cells + 1): how much each point of a 1-D grid of `cells` cells counts at spots t (0..1), as bend_at
    blends them (eased / straight)."""
    m = cells + 1
    out = np.empty((len(t), m))
    for i in range(m):
        unit = np.zeros((1, m, 2))
        unit[0, i, 0] = 1.0
        out[:, i] = bend_at(np.repeat(unit, 2, 0), t, np.zeros_like(t), smooth)[:, 0]
    return out


REFIT_STEPS = 8  # (spots per cell the re-fit compares at)


def blend(x, y, f):
    """Add between's step f (0 = x's end, 1 = y's) of two Warps (either may be None = no point moved: the other fades
    in / out): the points' offsets blended on the nearer end's grid (the other end's bend read at its points),
    strength blended, the rest the nearer end's. None when nothing is left moved."""
    x, y = clean_warp(x), clean_warp(y)
    if not x and not y:
        return None
    near = (x if f < 0.5 else y) or x or y
    c, r = size(near)
    jj, ii = np.mgrid[0:r + 1, 0:c + 1]
    u, v = (ii / c).ravel(), (jj / r).ravel()

    def on_grid(d):
        if not d:
            return np.zeros((r + 1, c + 1, 2))
        P = offsets(d, strength=False)
        return P if size(d) == (c, r) else bend_at(P, u, v, full(d)["smooth"]).reshape(r + 1, c + 1, 2)
    P = on_grid(x) * (1 - f) + on_grid(y) * f
    sx = (x or y).get("strength", DEFAULTS["strength"])
    sy = (y or x).get("strength", DEFAULTS["strength"])
    return clean_warp(dict(near, pts=P.reshape(-1, 2).tolist(), strength=sx + (sy - sx) * f))
