"""Symmetrical steps (sh["even"], custom.even_on; PLAN.md "Even steps", a tester's idea, user 2026-11-17): the even
version of a Spam / Outline spam / Nested-with-Spam shape. Every straight side of its drawing becomes the nearest
CLEAN line: each key row moves by the same whole number of gates, or one gate every few rows. The corners land
where those lines meet (so the closing side is corrected too), and every key row's stretch is widened to whole
gates on the gate grid (custom_chop.chop: sh["_even"]), so no row ever has a leftover.

Units here: x in gates from the grid's start (Aligned: tick 0, Auto: the shape's leftmost point), y in key rows
with whole numbers on the lines BETWEEN rows (y = pitch + 0.5): a clean line crosses those lines on gate lines,
so its rows' ends are whole gates and step evenly. Curved strokes stay as drawn (only their rows are widened)."""

import math

from notes.custom import even_on, frame_to_bp, frame_to_uv
from notes.custom_chop import gate_ticks

FLAT_ROWS = 0.5  # a side rising less than this many rows = flat (stays on one line between / through rows)
FAR = 2.0  # two snapped sides meeting further than this (rows / gates) and their half-lengths from the corner: a
# short flat step joins them there instead (nearly parallel sides would meet far away)
SAME = 1e-9
NOISE = 1e-6  # float noise of a snapped corner taken to the shape's box and back


def even_version(sh, ppq):
    """sh as its notes are made with Symmetrical steps on (sh itself when off or nothing to do): straight sides
    snapped to clean lines, hidden "_even" = the grid's start tick for chop. A gate Range by keys: keys_version
    (its strokes then lie in the stand-in space, see there); by time: as it is (hidden in its window)."""
    if not even_on(sh):
        return sh
    if sh.get("range"):
        grid = keys_grid(sh, ppq)
        return keys_version(sh, ppq, grid) if grid is not None else sh
    g = gate_ticks(sh["gate"], ppq)
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])
    if to_uv is None:
        return sh
    origin = grid_start(sh, ppq)
    strokes = [snapped_stroke(st, to_bp, to_uv, ppq, g, origin) for st in sh["strokes"]]
    return dict(sh, strokes=strokes, _even=origin)


def snapped_shape(sh, ppq):
    """(pts, strokes) of sh stored even (the tick ticked, a gate change, a let go): an upright box made a whole
    number of gates wide from its left edge (so a sideways flip lands on the same grid), the straight sides'
    corners where the even version has them. Curves keep their place in the box (they move by under half a gate)."""
    from notes.custom import frame_upright, map_stroke, stroke_points
    on = dict(sh, even=True)
    if "_even" not in even_version(on, ppq):
        return [list(p) for p in sh["pts"]], sh["strokes"]
    strokes = shown_strokes(on, ppq)
    pts = [list(p) for p in sh["pts"]]
    if frame_upright(pts) and not sh.get("range") and not sh.get("fit"):
        pts = hugging_box(pts, strokes, gate_ticks(sh["gate"], ppq) / ppq)
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(pts)
    if to_uv is None:
        return [list(p) for p in sh["pts"]], strokes
    (a0, c0), (a1, _), (_, c2) = sh["pts"]
    (n0, m0), (n1, _), (_, m2) = pts
    su = (a1 - a0) / (n1 - n0) if frame_upright(pts) and abs(n1 - n0) > 1e-12 else 1.0  # (u stretched: arcs stay
    sv = (c2 - c0) / (m2 - m0) if frame_upright(pts) and abs(m2 - m0) > 1e-12 else 1.0  # round)
    return pts, [map_stroke(st, lambda u, v: to_uv(*to_bp(u, v)), su, sv) for st in strokes]


def hugging_box(pts, strokes, g):
    """An upright box round the snapped drawing (user 2026-11-17: snapped corners stuck out of the box, so "Copies
    of" another drawing, filling the box, never reached them): its left edge moved by whole gates (the grid Auto
    counts from stays the same), a whole number of gates wide, from the drawing's lowest to highest pitch; flips
    kept."""
    from notes.custom import stroke_points
    to_bp = frame_to_bp(pts)
    xy = [to_bp(u, v) for st in strokes for u, v in stroke_points(st)]
    if not xy:
        return pts
    (b0, p0), (b1, _), (_, p2) = pts
    left = min(b0, b1)
    lo_b, hi_b = min(x for x, _ in xy), max(x for x, _ in xy)
    lo_p, hi_p = min(p for _, p in xy), max(p for _, p in xy)
    xl = left + math.floor((lo_b - left) / g + NOISE) * g
    xr = xl + max(1, math.ceil((hi_b - xl) / g - NOISE)) * g
    if hi_p - lo_p < 1e-9:
        return pts
    bl, br = (xl, xr) if b1 >= b0 else (xr, xl)
    pl, ph = (lo_p, hi_p) if p2 >= p0 else (hi_p, lo_p)
    return [[bl, pl], [br, pl], [bl, ph]]


def drawing_mark(strokes):
    """A short fingerprint of strokes (has the drawing been edited since it was snapped?); numbers to 6 places, so
    saving (numbers trimmed) and opening again keeps it."""
    import hashlib
    import json

    def rounded(x):
        if isinstance(x, float):
            return round(x, 6) + 0.0
        if isinstance(x, (list, tuple)):
            return [rounded(y) for y in x]
        if isinstance(x, dict):
            return {k: rounded(v) for k, v in x.items()}
        return x
    return hashlib.sha1(json.dumps(rounded(strokes), sort_keys=True).encode()).hexdigest()[:16]


def ticked(sh, ppq):
    """The changes ticking Symmetrical steps makes to sh: snapped, and the drawing as it was kept in "even_free"
    (pts / strokes before, "at" = the snapped box, "mark" = the snapped strokes' fingerprint) so unticking gives it
    back (user 2026-11-17: unticking left it snapped, with leftovers)."""
    pts, strokes = snapped_shape(dict(sh, even=True), ppq)
    import copy
    free = {"pts": [list(p) for p in sh["pts"]], "strokes": copy.deepcopy(sh["strokes"]), "at": [list(p) for p in pts],
            "mark": drawing_mark(strokes)}  # (own lists: a move changing the box in place mustn't move these)
    return {"pts": pts, "strokes": strokes, "even_free": free}


def unticked(sh):
    """The drawing ticking replaced, given back on untick: (pts, strokes), or None when there's none or its strokes
    were edited since (those edits win). Moves / resizes / flips / gate stretches made since carry over: the box's
    change since the tick (snapped box -> box now) is laid on the old box too."""
    free = sh.get("even_free")
    if not isinstance(free, dict) or free.get("mark") != drawing_mark(sh["strokes"]):
        return None
    to_uv = frame_to_uv(free["at"])
    if to_uv is None:
        return None
    now, was = frame_to_bp(sh["pts"]), frame_to_bp(free["pts"])
    return [list(now(*to_uv(*was(u, v)))) for u, v in ((0, 0), (1, 0), (0, 1))], free["strokes"]


def remarked(sh, strokes):
    """even_free after a change WE made to the stored strokes (a gate stretch): its fingerprint follows them."""
    free = sh.get("even_free")
    return dict(free, mark=drawing_mark(strokes)) if isinstance(free, dict) else None


def same_drawing(pts, strokes, pts2, strokes2):
    """Two (frame points, strokes) the same within float noise (a snap that moves nothing changes nothing)."""
    def flat(p, st):
        out = [x for q in p for x in q]
        for s in st:
            out += [x for q in s.get("pts", ()) for x in q] + list(s.get("box", ()))
        return out
    a, b = flat(pts, strokes), flat(pts2, strokes2)
    return len(a) == len(b) and all(abs(x - y) <= 1e-9 for x, y in zip(a, b))


def whole_width(new, orig, sh, ppq):
    """Frame points new (a resize of orig) with the moved left / right side put a whole number of gates from the
    side that stayed (Symmetrical steps: resizing jumps in whole steps, user). Turned boxes / both sides moved:
    new as it is."""
    from notes.custom import frame_upright
    if not (even_on(sh) and frame_upright(new) and frame_upright(orig)) or sh.get("fit"):
        return new
    g = gate_ticks(sh["gate"], ppq) / ppq
    lo, hi = sorted((new[0][0], new[1][0]))
    olo, ohi = sorted((orig[0][0], orig[1][0]))
    whole = max(1, math.floor((hi - lo) / g + 0.5)) * g
    if abs(lo - olo) < 1e-9:
        hi = lo + whole
    elif abs(hi - ohi) < 1e-9:
        lo = hi - whole
    else:
        return new
    a, b = (lo, hi) if new[1][0] >= new[0][0] else (hi, lo)
    return [[a, new[0][1]], [b, new[1][1]], [a, new[2][1]]]


def gate_stretched(sh, gate, ppq):
    """sh's box stretched sideways for a new gate (beats) so every step stays the same number of gates (user: a
    gate change = the shape stretches), from the box's left edge; then stored even again. -> (pts, strokes)."""
    old, new = gate_ticks(sh["gate"], ppq), gate_ticks(gate, ppq)
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    left = min(b0, b1, b2, b1 + b2 - b0)
    pts = [[left + (b - left) * new / old, p] for b, p in sh["pts"]]
    return snapped_shape(dict(sh, pts=pts, gate=gate), ppq)


# ---- Gate Range by keys (user 2026-11-17: "smooth bend": each row steps by ITS OWN gate, the sides bend along the
# Range graph, still whole notes, mirror halves exact). The STAND-IN SPACE: every row's ticks from the box's left
# edge t0 scaled by a / that row's gate (a = the first gate), so every row has gate a there; the sides snap there as
# usual, each row's stretch is widened to whole gates of a, then taken back to the row's own gate (chop,
# "_even_keys"). The real outline = those straight sides taken back row by row: bent (shown_strokes).

def keys_grid(sh, ppq):
    """sh's gate Range by keys (gaterange.RangeKeys; keys_down too), else None."""
    from notes.gaterange import RangeKeys, keys_up, range_grid
    if not sh.get("range") or keys_up(sh["range"])["dir"] != "keys":
        return None
    grid = range_grid(sh, ppq)
    return grid if isinstance(grid, RangeKeys) else None


def row_gates(grid):
    """key -> that key row's gate in ticks (remembered per grid)."""
    from notes.gaterange import gate_at
    memo = {}

    def gate(key):
        got = memo.get(key)
        if got is None:
            u = (key - grid.k0) / (grid.k1 - grid.k0) if grid.k1 > grid.k0 else 0.0
            got = memo[key] = gate_at(grid.graph, grid.a, grid.b, min(1.0, max(0.0, u)), grid.halves)
        return got
    return gate


def _row(p):
    return math.floor(p + 0.5)


def keys_middle(sh, ppq):
    """The tick every row's gates count from with a Range by keys: the box's middle (counted from its left edge, a
    drawing mirrored round its middle got lopsided steps: the far side's gates add up faster)."""
    from notes.gaterange import frame_span
    lo, hi, _, _ = frame_span(sh)
    return math.floor((lo + hi) / 2 * ppq + 0.5)


def keys_version(sh, ppq, grid):
    """even_version for a Range by keys: strokes in the stand-in space (in the shape's own box), "_even" = the tick
    the rows count from (keys_middle), "_even_keys" = the stand-in gate a."""
    from notes.custom import stroke_points
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])
    if to_uv is None:
        return sh
    gate, t0, a = row_gates(grid), keys_middle(sh, ppq), grid.a

    def stand_in(u, v):  # (stroke u, v -> stand-in (x in gates of a, y = pitch + 0.5))
        b, p = to_bp(u, v)
        return ((b * ppq - t0) / gate(_row(p)), p + 0.5)

    def back(x, y):  # (stand-in -> u, v in the box)
        return list(to_uv((t0 + x * a) / ppq, y - 0.5))

    out = []
    for st in sh["strokes"]:
        pts = st.get("pts") or []
        if plain_poly(st) and len(pts) >= 4 and all(abs(c - d) <= SAME for c, d in zip(pts[0], pts[-1])):
            got = even_loop([stand_in(u, v) for u, v in pts[:-1]])
            if got is not None:
                loop = [back(x, y) for x, y in got]
                out.append(dict(st, pts=loop + [list(loop[0])], _snapped=True))
                continue
        loop = [back(*stand_in(u, v)) for u, v in stroke_points(st)]
        out.append({**{k: v for k, v in st.items() if k in ("role", "colour")}, "kind": "poly", "pts": loop,
                    "free": True})  # (a curve as drawn, in the stand-in space: never snapped)
    return dict(sh, strokes=out, _even=t0, _even_keys=a)


def keys_chop(stretches, grid, a, t0, count=False):
    """chop for a Range by keys with Symmetrical steps: stand-in stretches widened to whole gates of a from t0,
    taken to each row's own gates (from t0 too), then cut into that row's gates back to back (count: how many
    each)."""
    import numpy as np
    from notes.custom_chop import on_grid
    st = on_grid(stretches, a, t0)
    if not len(st):
        return np.zeros(0, np.int64) if count else np.zeros((0, 3), np.int64)
    keys = np.unique(st[:, 2])
    gates = np.array([row_gates(grid)(int(k)) for k in keys.tolist()], np.int64)[np.searchsorted(keys, st[:, 2])]
    j0, j1 = (st[:, 0] - t0) // a, (st[:, 1] - t0) // a
    n = j1 - j0
    if count:
        return n
    k = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    g = np.repeat(gates, n)
    starts = t0 + (np.repeat(j0, n) + k) * g
    return np.column_stack([starts, starts + g, np.repeat(st[:, 2], n)])


def shown_strokes(sh, ppq):
    """The strokes where the notes show the shape (the landing line; what a let go stores): even_version's, but a
    Range by keys' straight stand-in sides taken back row by row (a point at every row's middle: the bent edge)."""
    ev = even_version(sh, ppq)
    if ev is sh or "_even_keys" not in ev:
        return ev["strokes"]
    grid = keys_grid(sh, ppq)
    gate, t0, a = row_gates(grid), ev["_even"], ev["_even_keys"]
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])

    def real(b, p):  # (stand-in beats / pitch -> u, v of the real spot)
        return list(to_uv((t0 + (b * ppq - t0) * gate(_row(p)) / a) / ppq, p))

    out = []
    for old, st in zip(sh["strokes"], ev["strokes"]):
        if not st.get("_snapped"):
            out.append(old)  # (curves: as drawn)
            continue
        pts = [to_bp(u, v) for u, v in st["pts"]]
        bent = []
        for (b0, p0), (b1, p1) in zip(pts, pts[1:]):
            bent.append(real(b0, p0))
            lo, hi = sorted((p0, p1))
            fs = sorted((q - p0) / (p1 - p0) for q in range(math.floor(lo), math.ceil(hi) + 1) if lo < q < hi)
            bent += [real(b0 + f * (b1 - b0), p0 + f * (p1 - p0)) for f in fs]  # (each row's middle it crosses)
        bent.append(real(*pts[-1]))
        out.append({k: v for k, v in dict(st, pts=bent).items() if k != "_snapped"})
    return out


# ---- Nested with Spam (stage F, a tester's picture dev/data/even_feedback.png: every band one row thick along flat
# sides and one gate thick along the slopes). Each note cell's DEPTH = the fewer of: gates to its row's end, rows
# to its column's end (counted from 1 at the edge); the Steps count spreads whole-number bands over the deepest
# (user: "fill like now", each band the same whole number of cells).

def nested_cells(sh, ppq, base):
    """(depth grid, first row key, first gate column, gate, grid start) of base's (start, end, key) rows widened to
    whole gates; depth 0 = outside."""
    import numpy as np
    from notes.custom import merged_rows
    from notes.custom_chop import on_grid
    g, o = gate_ticks(sh["gate"], ppq), int(sh["_even"])
    st = merged_rows(on_grid(base, g, o))
    k0, k1 = int(st[:, 2].min()), int(st[:, 2].max())
    j0s, j1s = (st[:, 0] - o) // g, (st[:, 1] - o) // g
    c0 = int(j0s.min())
    inside = np.zeros((k1 - k0 + 1, int(j1s.max()) - c0), bool)
    for k, a, b in zip((st[:, 2] - k0).tolist(), (j0s - c0).tolist(), (j1s - c0).tolist()):
        inside[k, a:b] = True
    depth = np.minimum(_edge_dist(inside, 1), _edge_dist(inside, 0))
    return depth, k0, c0, g, o


def _edge_dist(inside, axis):
    """Each inside cell's count of cells to the nearest outside one along axis (either way; 1 = at the edge)."""
    import numpy as np
    a = np.moveaxis(inside, axis, -1)
    n = a.shape[-1]
    idx = np.arange(n)
    last_out = np.maximum.accumulate(np.where(a, -1, idx), axis=-1)  # (the nearest outside cell before, -1 = none)
    nxt = np.minimum.accumulate(np.where(a, n, idx)[..., ::-1], axis=-1)[..., ::-1]  # (after, n = none)
    d = np.minimum(idx - last_out, nxt - idx)
    return np.moveaxis(np.where(a, d, 0), -1, axis)


def nested_even(sh, ppq, base, cfg):
    """Nested's steps for an even shape: step i = every cell at least i x band deep, as (start, end, key) rows;
    band = the deepest / Steps, at least one cell (Last size / Spacing / Turn / the centre point: not used)."""
    import numpy as np
    depth, k0, c0, g, o = nested_cells(sh, ppq, base)
    most = int(depth.max())
    n = cfg["steps"]
    band = max(1, math.floor(most / n + 0.5))
    row_most, col_most = depth.max(axis=1), depth.max(axis=0)
    out = []
    for i in range(n):
        least = 1 + i * band
        if least > most:  # (deeper than the shape goes: empty, no scan)
            out.append(np.zeros((0, 3), np.int64))
            continue
        rs, cs = np.flatnonzero(row_most >= least), np.flatnonzero(col_most >= least)
        r0, c1 = int(rs[0]), int(cs[0])  # (only the rows / columns still that deep: they shrink step by step)
        on = depth[r0:int(rs[-1]) + 1, c1:int(cs[-1]) + 1] >= least
        edge = np.diff(np.pad(on, ((0, 0), (1, 1))).astype(np.int8), axis=1)
        at = np.flatnonzero(edge)  # (row by row, left to right: each run's start then its end)
        w = edge.shape[1]
        a, b = at[0::2], at[1::2]
        out.append(np.column_stack([o + (c0 + c1 + a % w) * g, o + (c0 + c1 + b % w) * g, k0 + r0 + a // w])
                   .astype(np.int64))
    return out


def grid_start(sh, ppq):
    """The tick the gate grid counts from: 0 for Aligned (the song's grid), else the left edge of the shape's box
    (Auto; Centred = Auto here: no leftover to share). The box, not the drawing's leftmost point: snapping moves
    that point, so the grid would move again each time."""
    if sh.get("align") == "aligned":
        return 0
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    return math.floor(min(b0, b1, b2, b1 + b2 - b0) * ppq + 0.5)


def plain_poly(st):
    """A stroke of straight sides as drawn (not a curve, freehand, smoothed or a polygon side with a pattern)."""
    return (st.get("kind") == "poly" and not st.get("smooth") and not st.get("free")
            and not (st.get("sides") and st.get("formula")))


def snapped_stroke(st, to_bp, to_uv, ppq, g, origin):
    """A plain closed poly's corners moved onto clean lines (in the shape's own u / v); anything else as it is."""
    pts = st.get("pts") or []
    if not plain_poly(st) or len(pts) < 4 or any(abs(a - b) > SAME for a, b in zip(pts[0], pts[-1])):
        return st
    xy = []
    for u, v in pts[:-1]:
        b, p = to_bp(u, v)
        xy.append(((b * ppq - origin) / g, p + 0.5))
    got = even_loop(xy)
    if got is None:
        return st
    out = []
    for x, y in got:
        out.append(list(to_uv((origin + x * g) / ppq, y - 0.5)))  # (not rounded: snapping again moved it)
    return dict(st, pts=out + [list(out[0])])


def clean_line(p, q):
    """The clean line nearest the side p -> q: ("flat", y) or (slope, c) meaning x = c + slope * y, slope a whole
    number of gates a row (0 = upright) or +-1/m (one gate every m rows); c on the values that put the line on
    gate lines where it crosses between rows (whole numbers for whole slopes, steps of 1/m otherwise)."""
    (x0, y0), (x1, y1) = p, q
    dy, dx = y1 - y0, x1 - x0
    if abs(dy) < FLAT_ROWS - NOISE:  # (NOISE: a side snapped to exactly half a row stays sloped next time)
        return ("flat", math.floor((y0 + y1) + 0.5) / 2)  # (on a line between rows or through a row's middle)
    s = dx / dy
    m = math.floor(1 / abs(s) + 0.5) if SAME < abs(s) < 1 - NOISE else 0
    if abs(s) >= 1 - NOISE or m == 1:
        slope, step = float(math.floor(s + 0.5)), 1.0
    else:
        if m == 0 or m > abs(dy) + NOISE:  # (less than one gate over the whole side: upright)
            slope, step = 0.0, 1.0
        else:
            slope, step = math.copysign(1.0 / m, s), 1.0 / m
    c = ((x0 - slope * y0) + (x1 - slope * y1)) / 2
    return (slope, math.floor(c / step + 0.5) * step)


def _abc(line):
    """line -> (a, b, c) of a * x + b * y = c."""
    if line[0] == "flat":
        return 0.0, 1.0, line[1]
    slope, c = line
    return 1.0, -slope, c


def _on(line, y=None, x=None):
    """The point of a line at height y (not flat) or at x (flat)."""
    if line[0] == "flat":
        return (x, line[1])
    slope, c = line
    return (c + slope * y, y)


AGAIN = 8  # snapped again until nothing moves (a side squeezed short by its neighbours can turn flat), at most


def even_loop(pts):
    """A closed loop's corners [(x, y)] (not repeated at the end) -> the even loop's corners, or None if it
    folds away to nothing. Snapped until nothing moves: the stored even shape snaps to itself. Points on a straight
    line between their neighbours go first (a Range by keys' stored bent side has one at every row's middle: alone
    each piece is too short to tell its angle)."""
    pts = _straight_dropped(pts)
    for _ in range(AGAIN):
        got = _snapped_loop(pts)
        if got is None or len(got) == len(pts) and all(_length(p, q) <= 1e-9 for p, q in zip(got, pts)):
            return got
        pts = got
    return pts


def _straight_dropped(pts):
    """The loop without corners lying on the straight line between their neighbours (within NOISE)."""
    out = list(pts)
    i = 0
    while len(out) > 3 and i < len(out):
        (ax, ay), (bx, by), (cx, cy) = out[i - 1], out[i], out[(i + 1) % len(out)]
        ln = math.hypot(cx - ax, cy - ay)
        if ln > SAME and abs((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / ln <= NOISE and (
                (bx - ax) * (cx - bx) + (by - ay) * (cy - by)) >= 0:  # (on the line AND between them)
            del out[i]
        else:
            i += 1
    return out


def _snapped_loop(pts):
    n = len(pts)
    if n < 3:
        return None
    lines = [clean_line(pts[i], pts[(i + 1) % n]) for i in range(n)]
    out = []
    for i in range(n):  # corner i: where side i - 1 meets side i
        l1, l2 = lines[i - 1], lines[i]
        (a1, b1, c1), (a2, b2, c2) = _abc(l1), _abc(l2)
        det = a1 * b2 - a2 * b1
        x, y = pts[i]
        cx, cy = x, y
        half = min(_length(pts[i - 1], pts[i]), _length(pts[i], pts[(i + 1) % n])) / 2
        if abs(det) > SAME:
            cx, cy = (c1 * b2 - c2 * b1) / det, (a1 * c2 - a2 * c1) / det
            if _length((cx, cy), (x, y)) <= max(FAR, half) + NOISE:
                out.append((cx, cy))
                continue
        elif l1 == l2:
            continue  # (one straight line: no corner)
        # (parallel, or meeting far away: a short flat step between them at the corner's own row line, an upright
        # one between two flat sides)
        if l1[0] == "flat" and l2[0] == "flat":
            xr = float(math.floor(x + 0.5))
            out += [(xr, l1[1]), (xr, l2[1])]
        elif l1[0] == "flat" or l2[0] == "flat":
            out.append((cx, cy))  # (a flat side and a sloped one always meet near: the slope is steep there)
        else:
            yr = math.floor(2 * y + 0.5) / 2
            out += [_on(l1, y=yr), _on(l2, y=yr)]
    keep = []
    for p in out:  # (corners landing on one spot = one)
        if not keep or _length(p, keep[-1]) > SAME:
            keep.append(p)
    while len(keep) > 1 and _length(keep[0], keep[-1]) <= SAME:
        keep.pop()
    return keep if len(keep) >= 3 else None


def _length(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])
