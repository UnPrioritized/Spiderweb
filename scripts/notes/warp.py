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
(only for Folds: pulled part underneath), "h": [[point, side, dx, dy], ...] (only curve handles dragged: each
cell then a bicubic patch, net / _inner), "corners": [point, ...] (its handles move alone), "in": [[cell, slot, dx,
dy], ...] (only after a split that needs them: a cell's inner controls off _inner's rule), "xs" / "ys": the inner
grid lines' places across / up in the box (only when not evenly spaced: Split, user 2026-11-09)}. No point moved
and even lines = no Warp (clean_warp: None). Never released before this layout: no older layout is read (user
2026-11-09).

SPLIT (user 2026-11-09, PLAN.md "Warp Split"): grid lines may sit anywhere (MIN_GAP apart). Every bend works on the
EVEN grid of the same counts: a spot is first moved to its place there (_to_even: each cell stretched to 1/n, the
slopes times that stretch), so even grids keep today's very sums. split() adds a line keeping the bend (each patch
cut in two exactly: de Casteljau), remove_line() takes one away (re-fit, like a size change).

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
DIGITS = 9  # (places a saved number keeps: a split's new points / handles hold the bend to ~1e-9)
MIN_GAP = 0.005  # (grid lines at least this far apart, and from the box's edges (box units))
TINY = 1e-12  # (split: a handle this close to its untouched place isn't saved)
IN_TINY = 1e-8  # (split: nor an inner control this close to _inner's (the saved numbers' DIGITS make ~1e-9 noise))


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and x == x and abs(x) != float("inf")


def _kept(v):
    """A saved offset held within MOST_MOVE, DIGITS places."""
    return round(min(max(float(v), -MOST_MOVE), MOST_MOVE), DIGITS) + 0.0


def clean_warp(d, keep=False):
    """A saved rc["warp"] -> its clean form (see the top), or None (no Warp: nothing moved, or not readable). keep:
    a readable grid with nothing moved is kept too (split / remove_line on a fresh grid)."""
    if not isinstance(d, dict):
        return None
    c, r, pts = d.get("cols"), d.get("rows"), d.get("pts")
    if not (cells_ok(c) and cells_ok(r)) or not isinstance(pts, list):
        return None
    c, r = int(c), int(r)
    if len(pts) != (c + 1) * (r + 1) or not all(isinstance(p, list) and len(p) == 2 and all(number(v) for v in p)
                                                for p in pts):
        return None
    clean = [[_kept(v) for v in p] for p in pts]
    out ={"cols": c, "rows": r, "pts": clean}
    xs, ys = _clean_lines(d.get("xs"), c), _clean_lines(d.get("ys"), r)
    if xs:
        out["xs"] = xs
    if ys:
        out["ys"] = ys
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
    h = _clean_handles(d.get("h"), c, r)
    inner = _clean_inner(d.get("in"), c, r)
    if (not keep and not xs and not ys and not any(v for p in clean for v in p) and not any(e[2] or e[3] for e in h)
            and not inner):
        return None
    if h:
        out["h"] = h
        k = d.get("corners")
        if isinstance(k, list):
            k = sorted({int(v) for v in k if number(v) and v == int(v) and 0 <= v < len(clean)})
            if k:
                out["corners"] = k
    if inner:
        out["in"] = inner
    return out


def _clean_lines(v, n):
    """A saved "xs" / "ys" (the n - 1 inner grid lines' places, box units) -> its clean list, or None (evenly
    spaced, or not readable: even)."""
    if n < 2 or not isinstance(v, list) or len(v) != n - 1 or not all(number(x) for x in v):
        return None
    v = [round(float(x), DIGITS) + 0.0 for x in v]
    ends = [0.0] + v + [1.0]
    if any(b - a < MIN_GAP - 1e-9 for a, b in zip(ends, ends[1:])):
        return None
    if all(abs(x - (k + 1) / n) < 1e-9 for k, x in enumerate(v)):
        return None
    return v


def _clean_inner(v, c, r):
    """A saved "in" -> its clean list [[cell, slot, dx, dy], ...] (cell = row by row from the bottom left, slot 0..3 =
    the cell's inner controls (across + 2 x up)), [] when none / not readable."""
    if not isinstance(v, list):
        return []
    got = {}
    for e in v:
        if not (isinstance(e, list) and len(e) == 4 and all(number(x) for x in e)):
            continue
        k, s = e[0], e[1]
        if k != int(k) or s != int(s) or not (0 <= k < c * r) or not (0 <= s < 4):
            continue
        got[(int(k), int(s))] = [_kept(x) for x in e[2:]]
    return [[k, s] + got[(k, s)] for k, s in sorted(got) if any(got[(k, s)])]


def _clean_handles(h, c, r):
    """A saved "h" -> its clean list [[point, side, dx, dy], ...] (sorted, one per handle, only sides the point has;
    the last one wins), [] when none / not readable."""
    if not isinstance(h, list):
        return []
    got = {}
    for e in h:
        if not (isinstance(e, list) and len(e) == 4 and all(number(v) for v in e)):
            continue
        k, s = e[0], e[1]
        if k != int(k) or s != int(s) or not (0 <= k < (c + 1) * (r + 1)) or not (0 <= s < 4):
            continue
        k, s = int(k), int(s)
        if not side_ok(k % (c + 1), k // (c + 1), s, c, r):
            continue
        got[(k, s)] = [_kept(v) for v in e[2:]]
    return [[k, s] + got[(k, s)] for k, s in sorted(got)]


SIDES = ((-1, 0), (1, 0), (0, -1), (0, 1))  # (a point's handles by side: left, right, down, up (steps across, up))


def side_ok(i, j, s, c, r):
    """Whether point (i across, j up) of a c x r grid has a handle on side s (a neighbour that way)."""
    di, dj = SIDES[s]
    return 0 <= i + di <= c and 0 <= j + dj <= r


def cells_ok(v):
    """A count of cells a file / box may hold (whole, 1..MOST_CELLS)."""
    return number(v) and v == int(v) and 1 <= v <= MOST_CELLS


def full(wp):
    """Every setting of a clean Warp filled in."""
    return dict(DEFAULTS, **wp)


def size(wp):
    """(cells across, cells up) of a Warp."""
    return wp["cols"], wp["rows"]


def _ends(v, n):
    """Every line's place one way (n + 1, box units) from a saved "xs" / "ys" (None = evenly spaced)."""
    return np.arange(n + 1) / n if v is None else np.array([0.0] + list(v) + [1.0])


def lines(wp):
    """(X, Y): every grid line's place across / up in the box (cols + 1 / rows + 1 of them, 0 .. 1)."""
    c, r = size(wp)
    return _ends(wp.get("xs"), c), _ends(wp.get("ys"), r)


def uneven(wp):
    """The lines as the maths takes them: None when both ways are even (today's very sums), else (X or None, Y or
    None) (None = that way even)."""
    c, r = size(wp)
    xs, ys = wp.get("xs"), wp.get("ys")
    if xs is None and ys is None:
        return None
    return (None if xs is None else _ends(xs, c)), (None if ys is None else _ends(ys, r))


def _at(L, k, n):
    """Line k's place of n cells one way (L None = even: k / n, today's sums)."""
    return k / n if L is None else float(L[k])


def _to_even(v, L):
    """Spots v (box units) one way -> (their place on the even grid of the same count, how much a step there is
    stretched: du / dv). Inside the box each cell of the lines L is stretched to 1 / n; past it: as it is (the
    bend's edge holds there, slopes 0). fastloops._even_at is its twin (the same sums)."""
    v = np.asarray(v, np.float64)
    n = len(L) - 1
    i = np.clip(np.searchsorted(L, v, "right") - 1, 0, n - 1)
    w = L[i + 1] - L[i]
    inside = (v >= 0.0) & (v <= 1.0)
    u = np.where(inside, (i + (v - L[i]) / w) / n, v)
    return u, np.where(inside, 1.0 / (n * w), 1.0)


def _cells_seen(L, n):
    """How many even cells' worth of steps a way needs so its thinnest cell gets an even cell's share."""
    if L is None:
        return n
    return max(n, int(np.ceil(1.0 / np.diff(L).min() - 1e-9)))


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


NET = 2  # (the bend's kind for a grid with dragged handles: P = its control net; 1 / True = Smooth, 0 = Straight)


def untouched(P, smooth):
    """(rows + 1, cols + 1, 4, 2): where each handle of the points P sits when never dragged, as an offset from its
    point's (SIDES order): Smooth = carried along with its point (0); Straight cells = on the straight line, a third
    of the way to its neighbour. Sides past the grid's edge: 0 (no handle there)."""
    R = np.zeros(P.shape[:2] + (4, 2))
    if not smooth:
        R[:, 1:, 0] = (P[:, :-1] - P[:, 1:]) / 3.0
        R[:, :-1, 1] = (P[:, 1:] - P[:, :-1]) / 3.0
        R[1:, :, 2] = (P[:-1] - P[1:]) / 3.0
        R[:-1, :, 3] = (P[1:] - P[:-1]) / 3.0
    return R


def handle_rel(wp, strength=False):
    """(rows + 1, cols + 1, 4, 2): each handle's offset from its point's (offset space; SIDES order): a dragged one as
    saved (it stays put when Bend switches, moves along with its point), the rest untouched(). strength: times it."""
    c, r = size(wp)
    R = untouched(offsets(wp, strength=False), full(wp)["smooth"])
    for k, s, dx, dy in wp.get("h", ()):
        R[k // (c + 1), k % (c + 1), s] = (dx, dy)
    if strength:
        R = R * (full(wp)["strength"] / 100.0)
    return R


def net(wp, strength=True):
    """A clean Warp's control net (3 rows + 1, 3 cols + 1, 2): each cell (j, i) a bicubic patch of offsets from the
    16 controls at [3j .. 3j + 3, 3i .. 3i + 3]: its 4 corner points, the 8 handles on its sides (each shared with the
    cell across that side) and 4 inner ones (_inner)."""
    c, r = size(wp)
    P, R = offsets(wp, strength), handle_rel(wp, strength)
    C = np.zeros((3 * r + 1, 3 * c + 1, 2))
    C[::3, ::3] = P
    C[::3, 1::3] = P[:, :-1] + R[:, :-1, 1]
    C[::3, 2::3] = P[:, 1:] + R[:, 1:, 0]
    C[1::3, ::3] = P[:-1] + R[:-1, :, 3]
    C[2::3, ::3] = P[1:] + R[1:, :, 2]
    _inner(C, c, r)
    if wp.get("in"):  # (a split's cells whose inner controls are off the rule: kept as saved)
        f = full(wp)["strength"] / 100.0 if strength else 1.0
        for k, s, dx, dy in wp["in"]:
            j, i = divmod(k, c)
            b, a = divmod(s, 2)
            C[3 * j + 1 + b, 3 * i + 1 + a] += np.array([dx, dy]) * f
    return C


def netted(wp):
    """Whether a Warp's bend is read from its control net (curve handles dragged, or a split's inner controls)."""
    return bool(wp.get("h") or wp.get("in"))


IN_NEAR, IN_FAR = 2.0 / 3.0, 1.0 / 3.0  # (_inner: the side next to an inner control counts 2/3, the one across 1/3)


def _inner(C, c, r):
    """Fill each cell's 4 inner controls of the net C from its sides: the handle each way, read off the side next to
    it and the one across, shared out by distance; the two ways averaged. Untouched handles give back today's bends
    exactly (checked in warp_maths): Smooth (all 16 = the nearest corner) and Straight cells (bilinear thirds)."""
    def q(a, b):
        return C[b:3 * r + b:3, a:3 * c + a:3]
    for b in (1, 2):
        bs, bf = (0, 3) if b == 1 else (3, 0)
        for a in (1, 2):
            as_, af = (0, 3) if a == 1 else (3, 0)
            u = q(as_, b) + (q(a, bs) - q(as_, bs)) * IN_NEAR + (q(a, bf) - q(as_, bf)) * IN_FAR
            v = q(a, bs) + (q(as_, b) - q(as_, bs)) * IN_NEAR + (q(af, b) - q(af, bs)) * IN_FAR
            q(a, b)[...] = (u + v) * 0.5


def _factor(wp):
    """The share of an offset the colours move by (Strength %; 0 = shown / dragged as 100 %, like the points)."""
    s = full(wp)["strength"] / 100.0
    return s if s > 0 else 1.0


def handle_vecs(wp):
    """(rows + 1, cols + 1, 4, 2) every handle as shown, from its point as shown (box units, SIDES order: a third of a
    cell that way + its offset times the strength), and (rows + 1, cols + 1, 4) which handles there are."""
    c, r = size(wp)
    R = handle_rel(wp) * _factor(wp)
    jj, ii = np.mgrid[0:r + 1, 0:c + 1]
    has = np.zeros((r + 1, c + 1, 4), bool)
    for s, (di, dj) in enumerate(SIDES):
        has[:, :, s] = (ii + di >= 0) & (ii + di <= c) & (jj + dj >= 0) & (jj + dj <= r)
    R += _thirds(wp)
    return R, has


def _thirds(wp):
    """(rows + 1, cols + 1, 4, 2): each handle's place from its point before any bend: a third of the cell that way
    (its own cell's width: split grids' cells differ; 0 where there's no cell)."""
    c, r = size(wp)
    T = np.zeros((r + 1, c + 1, 4, 2))
    xs, ys = wp.get("xs"), wp.get("ys")
    if xs is None and ys is None:  # (even: today's sums)
        for s, (di, dj) in enumerate(SIDES):
            T[:, :, s, 0] = di / (3.0 * c)
            T[:, :, s, 1] = dj / (3.0 * r)
        return T
    X, Y = lines(wp)
    wx, wy = np.diff(X) / 3.0, np.diff(Y) / 3.0
    T[:, 1:, 0, 0] = -wx
    T[:, :-1, 1, 0] = wx
    T[1:, :, 2, 1] = -wy[:, None]
    T[:-1, :, 3, 1] = wy[:, None]
    if xs is None:  # (even ways keep today's sums: di / (3c) everywhere, as before)
        T[:, :, 0, 0], T[:, :, 1, 0] = -1 / (3.0 * c), 1 / (3.0 * c)
    if ys is None:
        T[:, :, 2, 1], T[:, :, 3, 1] = -1 / (3.0 * r), 1 / (3.0 * r)
    return T


def set_handle(wp, k, s, vec, alt=False, scale=(1.0, 1.0)):
    """wp with point k's handle on side s dragged to vec (from its point as shown, box units, as handle_vecs). Like
    the program's curves: the point's opposite handle turns with it (smooth through the point), keeping its length
    as seen (scale = screen size of one box unit across / up); alt = this one alone and the point becomes a corner
    (kept: later drags move one handle). A side the point doesn't have: wp as it was. Not cleaned (a drag goes on)."""
    c, r = size(wp)
    k, s = int(k), int(s)
    i, j = k % (c + 1), k // (c + 1)
    if not side_ok(i, j, s, c, r):
        return wp
    V, has = handle_vecs(wp)
    f = _factor(wp)
    T = _thirds(wp)
    base = T[j, i, s]
    got = {(e[0], e[1]): e for e in wp.get("h", ())}
    vec = np.asarray(vec, np.float64)
    got[(k, s)] = [k, s] + ((vec - base) / f).tolist()
    corners = set(wp.get("corners", ()))
    if alt:
        corners.add(k)
    o = s ^ 1  # (left <-> right, down <-> up)
    if k not in corners and has[j, i, o]:
        sc = np.abs(np.asarray(scale, np.float64))
        length = float(np.hypot(*(V[j, i, o] * sc)))
        seen = float(np.hypot(*(vec * sc)))
        if seen > 0 and length > 0:
            ob = T[j, i, o]
            got[(k, o)] = [k, o] + ((-vec * (length / seen) - ob) / f).tolist()
    out = dict(wp, h=[got[key] for key in sorted(got)])
    if corners:
        out["corners"] = sorted(corners)
    return out


def reset_handle(wp, k, s):
    """wp with point k's handle on side s back where it sits untouched (right-click). Its point's last dragged handle
    put back = the point smooth again (no corner mark; hunt 2026-11-09)."""
    h = [e for e in wp.get("h", ()) if (e[0], e[1]) != (k, s)]
    out = {key: v for key, v in wp.items() if key != "h"}
    if h:
        out["h"] = h
    if not any(e[0] == k for e in h):
        out = smooth_point(out, k)
    if not h:
        out.pop("corners", None)
    return out


def smooth_point(wp, k):
    """wp with point k no longer a corner (its opposite handles turn together again on the next drag)."""
    left = [c for c in wp.get("corners", ()) if c != k]
    out = {key: v for key, v in wp.items() if key != "corners"}
    if left:
        out["corners"] = left
    return out


def shape_of(wp, strength=True):
    """(what the bend is read from, its kind): the point offsets and Smooth / Straight, or with dragged handles the
    control net and NET."""
    if netted(wp):
        return net(wp, strength), NET
    return offsets(wp, strength), full(wp)["smooth"]


def _bern(v, n, slopes):
    """Bicubic parts of spots v along n cells (past the box: the edge's): (cell, [4 weights], [4 slopes] or None)."""
    v = np.asarray(v, np.float64)
    f = np.clip(v, 0.0, 1.0) * n
    i = np.minimum(f.astype(np.int64), n - 1)
    t = f - i
    u = 1.0 - t
    w = (u * u * u, 3.0 * t * u * u, 3.0 * t * t * u, t * t * t)
    if not slopes:
        return i, w, None
    m = ((v >= 0.0) & (v <= 1.0)) * float(n)
    return i, w, (-3.0 * u * u * m, 3.0 * u * (u - 2.0 * t) * m, 3.0 * t * (2.0 * u - t) * m, 3.0 * t * t * m)


def _net(C, x, y, slopes=False):
    """The offsets at spots (x, y) of the box from the control net C (NET; sums in fastloops._net_at's order);
    slopes: (value, d/dx, d/dy), exact, 0 past the box."""
    c, r = (C.shape[1] - 1) // 3, (C.shape[0] - 1) // 3
    i, wx, dx = _bern(x, c, slopes)
    j, wy, dy = _bern(y, r, slopes)
    i3, j3 = 3 * i, 3 * j
    val = ddx = ddy = 0.0
    for b in range(4):
        e = [C[j3 + b, i3 + a] for a in range(4)]
        rb = ((e[0] * wx[0][..., None] + e[1] * wx[1][..., None]) + e[2] * wx[2][..., None]) + e[3] * wx[3][..., None]
        val = val + rb * wy[b][..., None]
        if slopes:
            sb = (((e[0] * dx[0][..., None] + e[1] * dx[1][..., None]) + e[2] * dx[2][..., None])
                  + e[3] * dx[3][..., None])
            ddx = ddx + sb * wy[b][..., None]
            ddy = ddy + rb * dy[b][..., None]
    if not slopes:
        return val
    return val, ddx, ddy


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


def _straight(P, x, y):
    """Straight cells' bend at spots (x, y) with its slopes, exact: (value, d/dx, d/dy) (value = liquify.sample's very
    sums; slopes 0 past the box). Newton's slopes were steps of 1e-5 to one side: a mirrored grid's twin spots took
    different steps and a fold picked other layers on each side (user 2026-11-09, crossing.spwb)."""
    gy, gx = P.shape[:2]
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    fx = np.clip(x, 0.0, 1.0) * (gx - 1)
    fy = np.clip(y, 0.0, 1.0) * (gy - 1)
    i0 = np.minimum(fx.astype(np.int64), gx - 2)
    j0 = np.minimum(fy.astype(np.int64), gy - 2)
    tx, ty = (fx - i0)[..., None], (fy - j0)[..., None]
    a, b, c, d = P[j0, i0], P[j0, i0 + 1], P[j0 + 1, i0], P[j0 + 1, i0 + 1]
    lo, hi = a * (1 - tx) + b * tx, c * (1 - tx) + d * tx
    mx = (((x >= 0.0) & (x <= 1.0)) * float(gx - 1))[..., None]
    my = (((y >= 0.0) & (y <= 1.0)) * float(gy - 1))[..., None]
    return lo * (1 - ty) + hi * ty, ((b - a) * (1 - ty) + (d - c) * ty) * mx, (hi - lo) * my


def bend_at(P, x, y, smooth=True, L=None):
    """How far the spots (x, y) of the box move under the point offsets P (forward: where their colour goes). smooth
    = the kind (NET: P is a control net, shape_of). L = uneven(wp) (None: even lines)."""
    if L is not None:
        if L[0] is not None:
            x = _to_even(x, L[0])[0]
        if L[1] is not None:
            y = _to_even(y, L[1])[0]
    if smooth == NET:
        return _net(P, x, y)
    if smooth:
        return _eased(P, x, y)
    from notes.liquify import sample
    return sample(P, x, y)


def _with_slopes(P, x, y, smooth, L=None):
    """(value, d/dx, d/dy) of the bend at spots (x, y), exact (Newton's steps): on the even grid, the slopes times
    each way's stretch (fastloops._newton_one's very sums)."""
    fx = fy = None
    if L is not None:
        if L[0] is not None:
            x, fx = _to_even(x, L[0])
        if L[1] is not None:
            y, fy = _to_even(y, L[1])
    d0, dx, dy = (_net(P, x, y, slopes=True) if smooth == NET else _eased(P, x, y, slopes=True) if smooth
                  else _straight(P, x, y))
    if fx is not None:
        dx = dx * fx[:, None]
    if fy is not None:
        dy = dy * fy[:, None]
    return d0, dx, dy


def map_size(wp):
    """(across, up) points of a Warp's turned-round map: PER_CELL steps a cell (split grids: as many as its thinnest
    cell needs to get an even cell's share), at most MOST_MAP a side."""
    c, r = size(wp)
    L = uneven(wp) or (None, None)
    return (min(MOST_MAP, PER_CELL * _cells_seen(L[0], c) + 1), min(MOST_MAP, PER_CELL * _cells_seen(L[1], r) + 1))


def inverse_map(wp, quick=False):
    """A clean Warp turned round: (up, across, 2) Liquify-style map, each grid spot p = how far (in box units) the
    colour it shows comes from (s with s + bend(s) = p, so the map = s - p). Read-only, the last few kept. Where the
    bend folds over itself (a point dragged past its neighbours) the nearest settled answer is used.
    quick (a point being dragged, roll_warp): a few points moved since the last quick map of this grid = only the
    spots they can reach worked out again, the rest kept (a 20 x 20 grid's whole map took 0.33 s a move). Never kept
    for the notes themselves: let go works the whole map out, so a shape's colours never hang on how it was edited
    (Newton's last digits can differ near a moved point's reach)."""
    f = full(wp)
    key = json.dumps([size(wp), wp["pts"], f["smooth"], f["strength"], f["folds"], wp.get("h"), wp.get("in"),
                      wp.get("xs"), wp.get("ys")])
    got = _MAPS.get(key)
    if got is not None:
        return got
    P, smooth = shape_of(wp)
    L = uneven(wp)
    top = f["folds"] == "top"
    fold = _folds(P, smooth, top, L=L)  # (None: nothing folds, the plain Newton answers stand)
    gx, gy = map_size(wp)
    lkey = (size(wp), smooth, top, tuple(wp.get("xs") or ()), tuple(wp.get("ys") or ()))
    last = _LAST.get(lkey) if quick else None
    # (a fold now or in the last map: the fold rule ranks cells by EVERY corner's move, so a spot far from the moved
    # point can change layer: the whole map, else live != let go; hunt 2026-11-09)
    parts = _reach(last[0], P, smooth, gx, gy, L) if last is not None and fold is None and not last[2] else None
    if parts is None:
        jj, ii = np.mgrid[0:gy, 0:gx]
        px, py = (ii / (gx - 1)).ravel(), (jj / (gy - 1)).ravel()
        bx, by = _solve(P, px, py, smooth, fold, L)
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
            bx, by = _solve(P, px, py, smooth, fold, L)
            m[jj, ii] = np.stack([bx - px, by - py], 1)
    m[np.abs(m) < 1e-12] = 0.0  # (spots the bend never reached fetch from themselves exactly)
    m.setflags(write=False)
    if quick:
        _LAST.clear()
        _LAST[lkey] = (P, m, fold is not None)
        return m
    if len(_MAPS) > 8:
        _MAPS.pop(next(iter(_MAPS)))
    _MAPS[key] = m
    return m


_LAST = {}  # ((cols, rows), smooth) -> (the point offsets with strength, their map) of the last quick map


def _reach(P0, P, smooth, gx, gy, L=None):
    """[(rows slice, columns slice), ...] of the map spots that can change when the offsets go from P0 to P (a few
    points moved: one part each, a mirrored drag's twins too), or None = all of them (many moved, another size). A
    moved point bends only the 4 cells round it (Smooth and Straight cells alike); a spot whose colour comes from
    there is at most the largest bend there away. Each spot is worked out on its own, so parts may overlap."""
    if P0.shape != P.shape:
        return None
    if smooth == NET:
        return _reach_net(P0, P, gx, gy, L)
    moved = np.argwhere((P0 != P).any(2))
    if len(moved) > 4:
        return None
    return [_reach_one(P0, P, j, i, gx, gy, L) for j, i in moved]


def _reach_one(P0, P, j, i, gx, gy, L=None):
    """_reach's part for the one moved point (j, i)."""
    rows, cols = P.shape[0] - 1, P.shape[1] - 1
    X, Y = L or (None, None)
    j0, i0 = j - 1, i - 1
    j1, i1 = j + 1, i + 1
    # (the bend inside those cells is a blend of their corners, never past them: no overshoot)
    near = (slice(max(j0, 0), j1 + 1), slice(max(i0, 0), i1 + 1))
    both = np.concatenate([P0[near].reshape(-1, 2), P[near].reshape(-1, 2), np.zeros((1, 2))])
    lo, hi = both.min(0), both.max(0)  # (how far a colour from there can land, each way)
    u0, u1 = _at(X, max(i0, 0), cols) + lo[0], _at(X, min(i1, cols), cols) + hi[0]
    v0, v1 = _at(Y, max(j0, 0), rows) + lo[1], _at(Y, min(j1, rows), rows) + hi[1]
    if j0 <= 0:  # (past the box's edge the edge's bend holds: spots out there are reached too)
        v0 = -np.inf
    if i0 <= 0:
        u0 = -np.inf
    if j1 >= rows:
        v1 = np.inf
    if i1 >= cols:
        u1 = np.inf

    return _span(v0, v1, gy), _span(u0, u1, gx)


def _span(a, b, g):
    """Map spots a .. b (box units) on a side of g spots, a spot more each way."""
    lo = int(np.floor(max(a, 0.0) * (g - 1))) - 1
    hi = int(np.ceil(min(b, 1.0) * (g - 1))) + 2
    return slice(max(lo, 0), min(hi, g))


MOST_REACH_CELLS = 24  # (_reach_net: more cells changed than this = the whole map)


def _reach_net(P0, P, gx, gy, L=None):
    """_reach for control nets (handles dragged): one part per cell any of whose 16 controls changed (a point = its
    4 cells, a handle = the 2 cells beside its line). A bicubic patch never leaves its controls' hull: a spot whose
    colour comes from there is at most the largest of them away."""
    r, c = (P.shape[0] - 1) // 3, (P.shape[1] - 1) // 3
    X, Y = L or (None, None)
    ch = (P0 != P).any(2)
    cells = np.zeros((r, c), bool)
    for b in range(4):
        for a in range(4):
            cells |= ch[b:3 * r + b:3, a:3 * c + a:3]
    got = np.argwhere(cells)
    if len(got) > MOST_REACH_CELLS:
        return None
    parts = []
    for j, i in got:
        near = (slice(3 * j, 3 * j + 4), slice(3 * i, 3 * i + 4))
        both = np.concatenate([P0[near].reshape(-1, 2), P[near].reshape(-1, 2), np.zeros((1, 2))])
        lo, hi = both.min(0), both.max(0)
        u0 = -np.inf if i == 0 else _at(X, i, c) + lo[0]
        u1 = np.inf if i == c - 1 else _at(X, i + 1, c) + hi[0]
        v0 = -np.inf if j == 0 else _at(Y, j, r) + lo[1]
        v1 = np.inf if j == r - 1 else _at(Y, j + 1, r) + hi[1]
        parts.append((_span(v0, v1, gy), _span(u0, u1, gx)))
    return parts


def _solve(P, px, py, smooth, fold=None, L=None):
    """Where the colour each spot (px, py) shows comes from under the offsets P: s with s + bend(s) = p (Newton
    steps from p - bend(p)). fold (_folds; None = the grid folds nowhere): spots a folded piece covers are solved in
    every piece covering them and the fold rule picks (FOLDS at the top). L = uneven(wp)."""
    d = bend_at(P, px, py, smooth, L)
    bx, by, err = _newton(P, px, py, smooth, px - d[:, 0], py - d[:, 1], L=L)
    # (Newton that never settled (a far stretch next to a fold): left as it was, it took the nearest wrong spot =
    # torn specks beside the fold; those spots are solved piece by piece too)
    lost = err > 1e-7
    if fold is not None or lost.any():
        _fold_pick(P, px, py, smooth, fold or _folds(P, smooth, True, always=True, L=L), bx, by, lost, L)
    return bx, by


FOLD_LOOK = 9  # (spots a cell side where _folds looks whether a piece turned over / how far a cell's colours land)
FOLD_PIECES = 4  # (a folded cell solved in this many pieces a side: inside it, two layers may come from one cell)
FAR = 8.0  # (a box edge's cell reaches this far past the box: the edge's bend holds out there)
BOUND_IN = 1e-12  # (a fold's piece: how far inside its edges Newton is kept)
GIVE_UP_STEP, GIVE_UP_FAR = 6, 0.02  # (a fold's piece: still this far off after this many steps = not in it)


def _folds(P, smooth, top, always=False, L=None):
    """Where the grid of offsets P folds over itself, or None (nowhere: no spot has two sources). Else a dict:
    "units" = pieces (x0, x1, y0, y1) of the box each solved on its own (every cell; a folded cell cut in
    FOLD_PIECES x FOLD_PIECES), "land" = the box (x0, x1, y0, y1) each one's colours land in, "rank" = its place
    in the fold rule (higher = on top), "fixed" = the boxes of the folded pieces' landing (only spots in one get
    the rule)."""
    is_net = smooth == NET
    step = 3 if is_net else 1
    rows, cols = (P.shape[0] - 1) // step, (P.shape[1] - 1) // step
    n = FOLD_LOOK
    t = np.linspace(0.0, 1.0, n)
    LX, LY = L or (None, None)
    # (each cell's forward picture on n x n spots: a small quad there turned over (area <= 0) = a fold)
    cu = ((np.arange(cols)[:, None] + t[None]) / cols if LX is None
          else LX[:-1, None] + t[None] * np.diff(LX)[:, None])  # (cols, n)
    cv = ((np.arange(rows)[:, None] + t[None]) / rows if LY is None
          else LY[:-1, None] + t[None] * np.diff(LY)[:, None])  # (rows, n)
    X = np.broadcast_to(cu[None, None, :, :], (rows, n, cols, n))  # (rows, n up, cols, n across)
    Y = np.broadcast_to(cv[:, :, None, None], (rows, n, cols, n))
    d = bend_at(P, X.ravel(), Y.ravel(), smooth, L).reshape(rows, n, cols, n, 2)
    fx, fy = X + d[..., 0], Y + d[..., 1]
    ax, ay = fx[:, :-1, :, 1:] - fx[:, :-1, :, :-1], fy[:, :-1, :, 1:] - fy[:, :-1, :, :-1]
    bx, by = fx[:, 1:, :, :-1] - fx[:, :-1, :, :-1], fy[:, 1:, :, :-1] - fy[:, :-1, :, :-1]
    area = ax * by - ay * bx  # (rows, n-1, cols, n-1)
    flipped = (area <= 0).any((1, 3))  # (rows, cols)
    if not flipped.any() and not always:
        return None
    pts = P[::step, ::step]
    moved = np.hypot(pts[..., 0], pts[..., 1])
    # (by how far the cell's corners moved, added smallest first: mirrored twin cells get the very same number;
    # cells that tie share a place (user 2026-11-09, a mirrored fold came out lopsided when grid order broke the
    # tie): _fold_pick then takes the answer whose colour travels less)
    c4 = np.sort(np.stack([moved[:-1, :-1], moved[:-1, 1:], moved[1:, :-1], moved[1:, 1:]]), 0)
    rank = (c4[0] + c4[1]) + (c4[2] + c4[3])
    if is_net:  # (cells tying on their corners: the one whose 16 controls moved farther, all told, is more pulled;
        # sorted first so mirrored twins add up the very same)
        h = np.hypot(P[..., 0], P[..., 1])
        sixteen = np.stack([h[b:3 * rows + b:3, a:3 * cols + a:3] for b in range(4) for a in range(4)])
        more = np.sort(sixteen, 0).sum(0)
        key = np.stack([rank.ravel(), more.ravel()], 1)
        order = np.unique(key, axis=0, return_inverse=True)[1].reshape(-1).astype(np.int64)
    else:
        order = np.unique(rank.ravel(), return_inverse=True)[1].reshape(-1).astype(np.int64)
    k = FOLD_PIECES
    units, land, ranks, fixed = [], [], [], []
    per = (n - 1) // k  # (looks a piece side: n - 1 must split evenly)
    for j in range(rows):
        for i in range(cols):
            base = order[j * cols + i] * (k * k + 1)
            x0, x1, y0, y1 = _at(LX, i, cols), _at(LX, i + 1, cols), _at(LY, j, rows), _at(LY, j + 1, rows)
            if not flipped[j, i]:
                pieces = [(0, n - 1, 0, n - 1, 0)]
            else:  # (pieces ranked inside the cell by how far they moved: the farther one is "more pulled")
                mv = np.hypot(d[j, :, i, :, 0], d[j, :, i, :, 1])
                pieces = []
                for b in range(k):
                    for a in range(k):
                        pieces.append((a * per, (a + 1) * per, b * per, (b + 1) * per,
                                       float(mv[b * per:(b + 1) * per + 1, a * per:(a + 1) * per + 1].mean())))
                # (pieces moving the same (to 9 places: a mirrored twin's are off in the last bits) share a place)
                inner = np.unique(np.round([p[4] for p in pieces], 9), return_inverse=True)[1].reshape(-1)
                pieces = [p[:4] + (int(r) + 1,) for p, r in zip(pieces, inner)]
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


def _fold_pick(P, px, py, smooth, fold, bx, by, lost=None, L=None):
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
    U, LAND, R = fold["units"], fold["land"], fold["rank"]
    sp, up = [], []  # (spot, piece) pairs: each piece landing on the spot
    qx, qy = px[s], py[s]
    for k in range(len(U)):
        x0, x1, y0, y1 = LAND[k]
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
    # (kept a hair inside the piece: a spot ON a cell's top / right line counts as the next cell's, so Newton stuck
    # at that edge took the neighbour's slopes on one side of a mirrored grid and not on the other)
    sx, sy, err = _newton(P, px[sp], py[sp], smooth, cx, cy,
                          (lo_x + BOUND_IN, hi_x - BOUND_IN, lo_y + BOUND_IN, hi_y - BOUND_IN), L)
    ok = (err < 1e-7) & (sx >= lo_x - 1e-9) & (sx <= hi_x + 1e-9) & (sy >= lo_y - 1e-9) & (sy <= hi_y + 1e-9)
    if not ok.any():
        return
    sp, sx, sy, r = sp[ok], sx[ok], sy[ok], R[up[ok]]
    # (each spot's answers, highest rank first; a tie (mirrored twins): the colour travelling less, so a mirrored
    # Warp stays mirrored except on the crease itself)
    o = np.lexsort((np.hypot(sx - px[sp], sy - py[sp]), -r, sp))
    sp, sx, sy = sp[o], sx[o], sy[o]
    first = np.ones(len(sp), bool)
    first[1:] = sp[1:] != sp[:-1]
    bx[sp[first]], by[sp[first]] = sx[first], sy[first]


def _kind(smooth):
    """The bend's kind as the compiled loop takes it: 0 Straight cells, 1 Smooth, NET (2) a control net."""
    return NET if smooth == NET else 1 if smooth else 0


def _newton(P, px, py, smooth, sx, sy, bounds=None, L=None):
    """Newton steps from (sx, sy) toward s + bend(s) = (px, py) under the offsets P: (x, y, how far off) of the
    best answer each spot reached. bounds (x0, x1, y0, y1 arrays): kept inside them (a fold's piece). L =
    uneven(wp) (None: even lines)."""
    sx, sy = np.array(sx, np.float64), np.array(sy, np.float64)
    fast = compiled("warp_newton") if len(px) >= NEWTON_FAST else None
    if fast:  # (the compiled twin: each spot's very same steps, files/fastloops.warp_newton; user 11-09: heavy
        # per-spot work compiled)
        to = np.ascontiguousarray
        n = len(px)
        bnd = to(np.column_stack(bounds), np.float64) if bounds is not None else np.zeros((1, 4))
        bx, by, best = np.empty(n), np.empty(n), np.empty(n)
        X, Y = L or (None, None)
        lx = to(X, np.float64) if X is not None else np.zeros(0)  # (empty = that way even)
        ly = to(Y, np.float64) if Y is not None else np.zeros(0)
        fast.warp_newton(to(P, np.float64), to(px, np.float64), to(py, np.float64), _kind(smooth), lx, ly, sx, sy,
                         bnd, bounds is not None, STEPS, GIVE_UP_STEP, GIVE_UP_FAR, bx, by, best)
        return bx, by, best
    best = np.full(len(px), np.inf)
    bx, by = sx.copy(), sy.copy()
    live = np.arange(len(px))
    for step in range(STEPS):
        x, y = sx[live], sy[live]
        # (the slopes exact, with the values: one lookup a step)
        d0, dx, dy = _with_slopes(P, x, y, smooth, L)
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
        dx, dy = dx[go], dy[go]
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
    left moved. The new grid's lines are even (Split: a ready size or typed count evens them out, user 2026-11-09)."""
    wp = clean_warp(wp)
    if not wp:
        return None
    cols, rows = int(cols), int(rows)
    if (cols, rows) == size(wp) and uneven(wp) is None:
        return wp
    return _fit(wp, None, None, cols, rows)


def _fit(wp, X, Y, cols, rows):
    """refit's least squares onto a grid of cols x rows cells with the lines X / Y (full, None = even); handles,
    corners and inner controls left out (the new grid's start untouched). None when nothing is left."""
    c0, r0 = size(wp)
    L0 = uneven(wp) or (None, None)
    smooth = full(wp)["smooth"]
    sx = REFIT_STEPS * max(_cells_seen(X, cols), _cells_seen(L0[0], c0)) + 1
    sy = REFIT_STEPS * max(_cells_seen(Y, rows), _cells_seen(L0[1], r0)) + 1
    yy, xx = np.mgrid[0:sy, 0:sx]
    x, y = (xx / (sx - 1)).ravel(), (yy / (sy - 1)).ravel()
    G, kind = shape_of(wp, strength=False)
    want = bend_at(G, x, y, kind, uneven(wp))  # (sy * sx, 2; handles bend it too: the new grid's start untouched)
    wp = {k: v for k, v in wp.items() if k not in ("h", "corners", "in", "xs", "ys")}
    # (the bend = sum of points x (weight across) x (weight up): the weights each way found from a 1-D grid, so the
    # solve is Bx' P By = want with B = each way's weights; least squares each way in turn = the same as all at once)
    Bx = _weights(xx[0] / (sx - 1), cols, smooth, X)  # (sx, cols + 1)
    By = _weights(yy[:, 0] / (sy - 1), rows, smooth, Y)  # (sy, rows + 1)
    W = want.reshape(sy, sx, 2)
    px, py = np.linalg.pinv(Bx), np.linalg.pinv(By)
    P = np.einsum("aj,jkd,bk->abd", py, W, px)
    out = dict(wp, cols=cols, rows=rows, pts=P.reshape(-1, 2).tolist())
    if X is not None:
        out["xs"] = X[1:-1].tolist()
    if Y is not None:
        out["ys"] = Y[1:-1].tolist()
    return clean_warp(out)


def _weights(t, cells, smooth, L=None):
    """(len(t), cells + 1): how much each point of a 1-D grid of `cells` cells (lines L, None = even) counts at spots
    t (0..1), as bend_at blends them (eased / straight)."""
    m = cells + 1
    out = np.empty((len(t), m))
    for i in range(m):
        unit = np.zeros((1, m, 2))
        unit[0, i, 0] = 1.0
        out[:, i] = bend_at(np.repeat(unit, 2, 0), t, np.zeros_like(t), smooth, None if L is None else (L, None))[:, 0]
    return out


def _cut(C, k, t):
    """The control net C with each row's cubic of columns 3k .. 3k + 3 cut in two at t (de Casteljau: both halves
    together bend exactly as the whole did): 3 columns more."""
    p0, p1, p2, p3 = (C[:, 3 * k + a] for a in range(4))
    a, b, c = p0 + (p1 - p0) * t, p1 + (p2 - p1) * t, p2 + (p3 - p2) * t
    d, e = a + (b - a) * t, b + (c - b) * t
    f = d + (e - d) * t
    mid = np.stack([a, d, f, e, c], 1)
    return np.concatenate([C[:, :3 * k + 1], mid, C[:, 3 * k + 3:]], 1)


def split(wp, way, at):
    """wp with a new grid line at `at` (box units, 0..1): way "x" = a line up the box (one cell more across), "y" =
    a line across it (one more up); the bend kept exactly (user 2026-11-09: nothing moves when splitting): the cut
    cells' patches are each cut in two (_cut), the new points take the handles / inner controls that hold it (saved
    as dragged ones; Straight cells without handles stay plain: their straight bend cuts into straight cells). The
    numbers are kept to DIGITS places (~1e-9). None when it can't: already MOST_CELLS that way, or `at` within
    MIN_GAP of a line or the box's edge. wp may be a fresh grid (nothing moved)."""
    w = clean_warp(wp, keep=True)
    if not w or way not in ("x", "y"):
        return None
    across = way == "x"
    c, r = size(w)
    n = c if across else r
    if n >= MOST_CELLS or not number(at):
        return None
    L = lines(w)[0 if across else 1]
    at = round(float(at), DIGITS) + 0.0
    if np.abs(L - at).min() < MIN_GAP - 1e-12:
        return None
    k = int(np.searchsorted(L, at, "right") - 1)
    t = (at - L[k]) / (L[k + 1] - L[k])
    new_l = [float(v) for v in L[1:k + 1]] + [at] + [float(v) for v in L[k + 1:-1]]
    c2, r2 = (c + 1, r) if across else (c, r + 1)

    def renumber(p):  # (an old point's number on the new grid)
        i, j = p % (c + 1), p // (c + 1)
        if across:
            i += i > k
        else:
            j += j > k
        return j * (c2 + 1) + i
    out = {key: v for key, v in w.items() if key not in ("h", "corners", "in", "pts")}
    out.update(cols=c2, rows=r2, **{("xs" if across else "ys"): new_l})
    if not netted(w) and not full(w)["smooth"]:  # (Straight cells: the new points on the straight line)
        P = offsets(w, strength=False)
        if not across:
            P = P.transpose(1, 0, 2)
        P = np.concatenate([P[:, :k + 1], (P[:, k] * (1 - t) + P[:, k + 1] * t)[:, None], P[:, k + 1:]], 1)
        if not across:
            P = P.transpose(1, 0, 2)
        return clean_warp(dict(out, pts=P.reshape(-1, 2).tolist()), keep=True)
    C = net(w, strength=False)
    C = _cut(C, k, t) if across else _cut(C.transpose(1, 0, 2), k, t).transpose(1, 0, 2)
    P = C[::3, ::3]
    R = np.zeros(P.shape[:2] + (4, 2))  # (each handle from its point, as net() reads them)
    R[:, :-1, 1] = C[::3, 1::3] - P[:, :-1]
    R[:, 1:, 0] = C[::3, 2::3] - P[:, 1:]
    R[:-1, :, 3] = C[1::3, ::3] - P[:-1]
    R[1:, :, 2] = C[2::3, ::3] - P[1:]
    smooth = full(w)["smooth"]
    off = np.abs(R - untouched(P, smooth)).max(3) > TINY
    h = [[int(j * (c2 + 1) + i), int(s), float(R[j, i, s, 0]), float(R[j, i, s, 1])]
         for j, i, s in np.argwhere(off) if side_ok(i, j, s, c2, r2)]
    out["pts"] = P.reshape(-1, 2).tolist()
    if h:
        out["h"] = h
    if w.get("corners"):
        out["corners"] = [renumber(p) for p in w["corners"]]
    got = clean_warp(out, keep=True)
    if got is None:
        return None
    # (the inner controls the split needs that _inner wouldn't make from the new sides: saved as "in")
    B = net(got, strength=False)
    inner = []
    for j in range(r2):
        for i in range(c2):
            for s in range(4):
                b, a = divmod(s, 2)
                d = C[3 * j + 1 + b, 3 * i + 1 + a] - B[3 * j + 1 + b, 3 * i + 1 + a]
                if np.abs(d).max() > IN_TINY:
                    inner.append([j * c2 + i, s, float(d[0]), float(d[1])])
    if inner:
        got = clean_warp(dict(got, **{"in": inner}), keep=True)
    return got


def split_mirrored(wp, way, at):
    """split() at `at` and at its mirror 1 - at (Mirror modes, user 2026-11-09: a split adds the mirrored line too;
    one line when they'd sit within MIN_GAP of each other). None when the first can't be made; the mirrored one
    left out when only it can't."""
    got = split(wp, way, at)
    if got is None:
        return None
    other = round(1.0 - round(float(at), DIGITS), DIGITS) + 0.0
    if abs(other - at) < MIN_GAP:
        return got
    return split(got, way, other) or got


def remove_line(wp, way, k):
    """wp without grid line k (1 .. n - 1, the inner lines counted from the box's left / bottom edge) of way "x"
    (lines up the box) or "y": one cell fewer that way, the bend re-fitted onto the lines left (like a size change:
    handles / inner controls start untouched). None when there's no such line, or nothing's left to keep."""
    w = clean_warp(wp, keep=True)
    if not w or way not in ("x", "y"):
        return None
    across = way == "x"
    c, r = size(w)
    n = c if across else r
    if not number(k) or k != int(k) or not 1 <= k <= n - 1:
        return None
    X, Y = lines(w)
    L = np.delete(X if across else Y, int(k))
    even = np.allclose(L, np.arange(n) / (n - 1), atol=1e-9, rtol=0)
    if across:
        X = None if even else L
        Y = uneven(w)[1] if uneven(w) else None
        cols, rows = c - 1, r
    else:
        Y = None if even else L
        X = uneven(w)[0] if uneven(w) else None
        cols, rows = c, r - 1
    return _fit(w, X, Y, cols, rows)


def remove_mirrored(wp, way, k):
    """remove_line() of line k and its mirror (n - k) when that's another line (Mirror modes)."""
    w = clean_warp(wp, keep=True)
    if not w or way not in ("x", "y"):
        return None
    n = size(w)[0 if way == "x" else 1]
    got = remove_line(w, way, k)
    if got is None or n - k == k:
        return got
    # (the mirror's number on the grid left: past k = one less)
    m = n - k - 1 if n - k > k else n - k
    return remove_line(got, way, m) or got


REFIT_STEPS = 8  # (spots per cell the re-fit compares at)


def shows(d):
    """d when it bends what's shown (on, strength above 0), else None."""
    return d if d and d.get("on") is not False and d.get("strength", DEFAULTS["strength"]) != 0 else None


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
    if uneven(near) is None:
        u, v = (ii / c).ravel(), (jj / r).ravel()
    else:  # (a split grid: its points at its own lines)
        X, Y = lines(near)
        u, v = X[ii].ravel(), Y[jj].ravel()

    def alike(d):  # (the same grid as the nearer end: its points / handles taken as they are)
        return size(d) == (c, r) and d.get("xs") == near.get("xs") and d.get("ys") == near.get("ys")

    def on_grid(d):
        if not d:
            return np.zeros((r + 1, c + 1, 2))
        if alike(d):
            return offsets(d, strength=False)
        G, kind = shape_of(d, strength=False)
        return bend_at(G, u, v, kind, uneven(d)).reshape(r + 1, c + 1, 2)
    # (a hidden or 0 % end bends nothing you see: it counts as unmoved, else its bend showed up halfway; hunt
    # 2026-11-09. Both such ends: blended as they are, the steps hidden / 0 % too)
    xs, ys = shows(x), shows(y)
    if xs or ys:
        x, y = xs, ys
    px, py = on_grid(x), on_grid(y)
    P = px * (1 - f) + py * f
    sx = (x or y).get("strength", DEFAULTS["strength"])
    sy = (y or x).get("strength", DEFAULTS["strength"])
    out = {k: v for k, v in near.items() if k not in ("h", "corners", "in")}
    smooth = full(near)["smooth"]
    if netted(x or {}) or netted(y or {}):  # (handles blended like the points: an end without them, or on another
        # grid, = its handles untouched on this one; a split's inner controls the same way)
        def rel(d, Pd):
            return handle_rel(d) if d and alike(d) else untouched(Pd, smooth)
        R = rel(x, px) * (1 - f) + rel(y, py) * f
        diff = np.abs(R - untouched(P, smooth)).max(3) > 1e-9
        h = [[int(j * (c + 1) + i), int(s), float(R[j, i, s, 0]), float(R[j, i, s, 1])]
             for j, i, s in np.argwhere(diff) if side_ok(i, j, s, c, r)]
        if h:
            out["h"] = h
            if near.get("corners"):
                out["corners"] = near["corners"]

        def ins(d):
            D = np.zeros((r * c, 4, 2))
            for k, s, dx, dy in ((d or {}).get("in", ()) if d and alike(d) else ()):
                D[k, s] = dx, dy
            return D
        D = ins(x) * (1 - f) + ins(y) * f
        inner = [[int(k), int(s), float(D[k, s, 0]), float(D[k, s, 1])] for k, s in np.argwhere(np.abs(D).max(2) > 0)]
        if inner:
            out["in"] = inner
    return clean_warp(dict(out, pts=P.reshape(-1, 2).tolist(), strength=sx + (sy - sx) * f))
