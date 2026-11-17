"""Symmetrical steps (sh["even"], custom.even_on; PLAN.md "Even steps", a tester's idea, user 2026-11-17): the even
version of a Spam / Outline spam / Nested-with-Spam shape. Every straight side of its drawing becomes the nearest
CLEAN line: each key row moves by the same whole number of gates, or one gate every few rows. The corners land
where those lines meet (so the closing side is corrected too), and every key row's stretch is widened to whole
gates on the gate grid (custom_chop.chop: sh["_even"]), so no row ever has a leftover.

Units here: x in gates from the grid's start (Aligned: tick 0, Auto: the shape's leftmost point), y in key rows
with whole numbers on the lines BETWEEN rows (y = pitch + 0.5): a clean line crosses those lines on gate lines,
so its rows' ends are whole gates and step evenly. Curved strokes stay as drawn (only their rows are widened)."""

import json
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
        sp = range_space(sh, ppq)
        if sp == "rows":  # (by keys, straight sides: the drawing as it is, each row its own whole gates)
            return dict(sh, _even=keys_middle(sh, ppq), _even_rows=True)
        return keys_version(sh, ppq, sp) if sp is not None else sh
    g = gate_ticks(sh["gate"], ppq)
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])
    if to_uv is None:
        return sh
    origin = grid_start(sh, ppq)
    strokes = [snapped_stroke(st, to_bp, to_uv, ppq, g, origin) for st in sh["strokes"]]
    strokes = chains_snapped(sh["strokes"], strokes, to_bp, to_uv, ppq, g, origin)
    return dict(sh, strokes=strokes, _even=origin)


def chains_snapped(old, strokes, to_bp, to_uv, ppq, g, origin):
    """Open straight-sided strokes whose ends meet end to end into a closed outline (a triangle drawn as three
    lines, a line stopping just short of its start; ends within custom.TOUCH_BEATS / TOUCH_KEYS) snapped as one
    loop (hunt 2026-11-17: they got no even sides). Each stroke keeps its own corners when the snap keeps every
    corner; else the chain becomes one closed stroke (the first's settings; only when they all share them)."""
    from notes.custom import TOUCH_BEATS, TOUCH_KEYS
    open_ = [i for i, st in enumerate(old) if plain_poly(st) and len(st.get("pts") or []) >= 2
             and strokes[i] is st]  # (not closed: closed ones snapped already)
    if not open_:
        return strokes
    ends = {i: (to_bp(*old[i]["pts"][0]), to_bp(*old[i]["pts"][-1])) for i in open_}

    def meet(a, b):
        return abs(a[0] - b[0]) <= TOUCH_BEATS and abs(a[1] - b[1]) <= TOUCH_KEYS

    out, used = list(strokes), set()
    for i in open_:
        if i in used:
            continue
        chain, at, start = [(i, False)], ends[i][1], ends[i][0]
        while not meet(at, start):
            nxt = next(((j, rev) for j in open_ if j not in used and j not in (k for k, _ in chain)
                        for rev in (False, True) if meet(at, ends[j][1 if rev else 0])), None)
            if nxt is None:
                break
            chain.append(nxt)
            at = ends[nxt[0]][0 if nxt[1] else 1]
        if not meet(at, start) or len({(old[k].get("role"), old[k].get("colour")) for k, _ in chain}) > 1:
            continue  # (not closed / strokes of different kinds: as drawn)
        used.update(k for k, _ in chain)
        pieces = [old[k]["pts"][::-1] if rev else old[k]["pts"] for k, rev in chain]
        corners = [p for piece in pieces for p in piece[:-1]]
        xy = [((to_bp(u, v)[0] * ppq - origin) / g, to_bp(u, v)[1] + 0.5) for u, v in corners]

        def back(x, y):
            return list(to_uv((origin + x * g) / ppq, y - 0.5))
        got = _same_corners(xy)
        if got is not None:  # (each stroke its own corners, the ends shared)
            at_ = 0
            for (k, rev), piece in zip(chain, pieces):
                m = len(piece) - 1
                new = [back(*got[(at_ + n) % len(got)]) for n in range(m + 1)]
                out[k] = dict(old[k], pts=new[::-1] if rev else new)
                at_ += m
            continue
        loop = even_loop(xy)
        if loop is None:
            continue
        pts = [back(x, y) for x, y in loop]
        out[chain[0][0]] = dict(old[chain[0][0]], pts=pts + [list(pts[0])])
        for k, _ in chain[1:]:
            out[k] = None
    return [st for st in out if st is not None]


def _same_corners(pts):
    """even_loop keeping every corner (None when a corner would go or come: the caller joins the strokes then)."""
    cur = pts
    for _ in range(AGAIN):
        got = _snapped_loop(cur)
        if got is None or len(got) != len(cur):
            return None
        if all(_length(p, q) <= 1e-9 for p, q in zip(got, cur)):
            return got
        cur = got
    return cur


def snapped_shape(sh, ppq):
    """(pts, strokes) of sh stored even (the tick ticked, a gate change, a let go): an upright box made a whole
    number of gates wide from its left edge (so a sideways flip lands on the same grid), the straight sides'
    corners where the even version has them. Curves keep their place in the box (they move by under half a gate)."""
    from notes.custom import frame_upright, map_stroke, stroke_points
    on = dict(sh, even=True)
    if not stores_snap(sh) or "_even" not in even_version(on, ppq):
        return [list(p) for p in sh["pts"]], sh["strokes"]
    strokes = shown_strokes(on, ppq)
    pts = [list(p) for p in sh["pts"]]
    if axis_box(pts) and not sh.get("range") and not sh.get("fit"):
        pts = hugging_box(pts, strokes, gate_ticks(sh["gate"], ppq) / ppq)
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(pts)
    if to_uv is None:
        return [list(p) for p in sh["pts"]], strokes
    (a0, c0), (a1, c1), (a2, c2) = sh["pts"]
    (n0, m0), (n1, m1), (n2, m2) = pts
    lu, lv = math.hypot(n1 - n0, m1 - m0), math.hypot(n2 - n0, m2 - m0)
    su = math.hypot(a1 - a0, c1 - c0) / lu if lu > 1e-12 else 1.0  # (u / v stretched: arcs stay round)
    sv = math.hypot(a2 - a0, c2 - c0) / lv if lv > 1e-12 else 1.0
    return pts, [map_stroke(st, lambda u, v: to_uv(*to_bp(u, v)), su, sv) for st in strokes]


def axis_box(pts):
    """The box's sides run along time and keys: upright, or turned a quarter / half way (hunt 2026-11-17: a shape
    turned 90 degrees was left out of box fitting and whole-gate resizing)."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    return (abs(p1 - p0) < 1e-9 and abs(b2 - b0) < 1e-9) or (abs(b1 - b0) < 1e-9 and abs(p2 - p0) < 1e-9)


def _box_sides(pts):
    """(left, right, low, high) of an axis box's frame points."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    bs, ps = (b0, b1, b2, b1 + b2 - b0), (p0, p1, p2, p1 + p2 - p0)
    return min(bs), max(bs), min(ps), max(ps)


def _moved_sides(pts, old, new):
    """Each frame point's beat / pitch taken from the old box's side it sits on to that side in the new box."""
    l0, r0, d0, u0 = old
    l1, r1, d1, u1 = new
    return [[l1 if abs(b - l0) <= abs(b - r0) else r1, d1 if abs(p - d0) <= abs(p - u0) else u1] for b, p in pts]


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
    old = _box_sides(pts)
    left = old[0]
    lo_b, hi_b = min(x for x, _ in xy), max(x for x, _ in xy)
    lo_p, hi_p = min(p for _, p in xy), max(p for _, p in xy)
    xl = left + math.floor((lo_b - left) / g + NOISE) * g
    xr = xl + max(1, math.ceil((hi_b - xl) / g - NOISE)) * g
    if hi_p - lo_p < 1e-9:
        return pts
    return _moved_sides(pts, old, (xl, xr, lo_p, hi_p))  # (flips / quarter turns kept)


def even_joined(new, olds, ppq):
    """Turn into live shape (Ctrl+R) made new (a live shape) of olds: with Symmetrical steps on it's stored snapped
    at once, and the drawing Split compares against (new["from"]) is that snapped one, so Split still gives the old
    shapes back after a click (hunt 2026-11-17). True = some notes moved: a shape wasn't on the joined shape's gate
    grid (Auto counts from the joined box's left edge) or wasn't ticked (user picked: tell, don't refuse)."""
    if not (even_on(new) and stores_snap(new)) or "from" not in new:
        return False
    g = gate_ticks(new["gate"], ppq)
    o = grid_start(new, ppq)
    moved = any(not (even_on(sh) and gate_ticks(sh["gate"], ppq) == g and (grid_start(sh, ppq) - o) % g == 0)
                for sh in olds if sh.get("kind") == "custom")
    new["pts"], new["strokes"] = snapped_shape(new, ppq)
    new["from"]["pts"] = [list(p) for p in new["pts"]]
    new["from"]["strokes"] = json.loads(json.dumps(new["strokes"]))
    new.pop("even_free", None)  # (the first shape's: it doesn't fit the joined drawing)
    return moved


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
    strokes = time_bent(dict(sh, pts=pts), strokes, ppq)  # (a Range along time set to bend: curved now)
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
    if not (even_on(sh) and axis_box(new) and axis_box(orig)) or sh.get("fit"):
        return new
    g = gate_ticks(sh["gate"], ppq) / ppq
    sides = _box_sides(new)
    lo, hi, d, u = sides
    olo, ohi, _, _ = _box_sides(orig)
    whole = max(1, math.floor((hi - lo) / g + 0.5)) * g
    if abs(lo - olo) < 1e-9:
        hi = lo + whole
    elif abs(hi - ohi) < 1e-9:
        lo = hi - whole
    else:
        return new
    return _moved_sides(new, sides, (lo, hi, d, u))


def gate_stretched(sh, gate, ppq):
    """sh's box stretched sideways for a new gate (beats) so every step stays the same number of gates (user: a
    gate change = the shape stretches), from the box's left edge; then stored even again. -> (pts, strokes)."""
    old, new = gate_ticks(sh["gate"], ppq), gate_ticks(gate, ppq)
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    left = min(b0, b1, b2, b1 + b2 - b0)
    pts = [[left + (b - left) * new / old, p] for b, p in sh["pts"]]
    return snapped_shape(dict(sh, pts=pts, gate=gate), ppq)


# ---- Gate Range (user 2026-11-17). By keys: Straight sides (default: the drawing as it is, each row rounded out to
# whole gates of its own from the box's middle) or Bend with the gates (range "even_bend": every row steps by ITS OWN
# gate, the sides bend along the Range graph). Along time: the drawing as it is, each row rounded out to the Range's
# cut lines. Bend uses a STAND-IN SPACE (RangeSpace) where every row has gate a; the sides snap there as usual, each
# row's stretch is widened to whole a, then taken back (range_chop). The real outline = the straight stand-in sides
# taken back: bent (shown_strokes).

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


class RangeSpace:
    """A gate Range's stand-in space for Symmetrical steps: fwd(tick, pitch) -> x (whole numbers = where that row's
    notes may start / end), back(x, pitch) -> tick. kind "keys" = Bend with the gates (each row's gates from the
    box's middle). Stand-in ticks = origin + x * a (a = the first gate)."""

    def __init__(self, kind, fwd, back, origin, a, grid):
        self.kind, self.fwd, self.back, self.origin, self.a, self.grid = kind, fwd, back, origin, a, grid


def range_space(sh, ppq):
    """sh's RangeSpace, "rows" for a Range by keys with straight sides (no stand-in: each row rounded out to its
    own gates from the middle) or along time (each row rounded out to the cut lines), or None (no Range / one the
    steps can't follow: Each key row by itself)."""
    import numpy as np
    from notes.gaterange import RangeKeys, keys_up, range_grid
    r = sh.get("range")
    if not r:
        return None
    r = keys_up(r)
    if r["dir"] == "keys":
        grid = range_grid(sh, ppq)
        if not isinstance(grid, RangeKeys):
            return None
        if not r.get("even_bend"):
            return "rows"
        gate, mid = row_gates(grid), keys_middle(sh, ppq)
        return RangeSpace("keys", lambda t, p: (t - mid) / gate(_row(p)), lambda x, p: mid + x * gate(_row(p)),
                          mid, grid.a, grid)
    if r.get("rows"):
        return None
    sq = range_grid(sh, ppq)
    if not isinstance(sq, np.ndarray) or not len(sq):
        return None
    if not r.get("even_bend"):
        return "rows"  # (straight sides: the drawing as it is, each row rounded out to the Range's cut lines)
    # Bend with the gates along time (user 2026-11-17: "2 key rows on varying gate will form some kind of log
    # curve"): the stored outline is BENT (time_bent: the drawing laid over the cut squares by its share of the box,
    # so its corners never move; snapping the sides in squares made them jump with every From / To: "feels
    # random"). Stand-in x = cut-square count, where that outline is straight again; never snapped.
    edges = np.r_[sq[:, 0], sq[-1, 1]].astype(float)
    idx = np.arange(len(edges), dtype=float)
    e0, e1, n = edges[0], edges[-1], idx[-1]
    g0, g1 = edges[1] - edges[0], edges[-1] - edges[-2]

    def fwd(t, p):  # (how many cut squares from the first edge; past the ends: the end squares' size)
        if t < e0:
            return (t - e0) / g0
        if t > e1:
            return n + (t - e1) / g1
        return float(np.interp(t, edges, idx))

    def back(x, p):
        if x < 0:
            return e0 + x * g0
        if x > n:
            return e1 + (x - n) * g1
        return float(np.interp(x, idx, edges))
    sp = RangeSpace("time", fwd, back, int(e0), int(g0), edges)
    sp.lin = lambda t: (t - e0) / max(e1 - e0, 1.0) * n  # (a straight drawing's share of the box, in squares)
    sp.lin_back = lambda x: e0 + x / n * (e1 - e0) if n else e0
    return sp


def keys_version(sh, ppq, sp):
    """even_version for a Range with a stand-in space (RangeSpace): strokes in the stand-in space (in the shape's
    own box), "_even" = sp.origin, "_even_space" = its kind, "_even_a" = the stand-in gate a."""
    from notes.custom import stroke_points
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])
    if to_uv is None:
        return sh
    t0, a = sp.origin, sp.a

    def stand_in(u, v):  # (stroke u, v -> stand-in (x in gates of a, y = pitch + 0.5))
        b, p = to_bp(u, v)
        return (sp.fwd(b * ppq, p), p + 0.5)

    def back(x, y):  # (stand-in -> u, v in the box)
        return list(to_uv((t0 + x * a) / ppq, y - 0.5))

    out = []
    for st in sh["strokes"]:
        pts = st.get("pts") or []
        if sp.kind != "time" and plain_poly(st) and len(pts) >= 4 and all(abs(c - d) <= SAME
                                                                          for c, d in zip(pts[0], pts[-1])):
            got = even_loop([stand_in(u, v) for u, v in pts[:-1]])
            if got is not None:
                loop = [back(x, y) for x, y in got]
                out.append(dict(st, pts=loop + [list(loop[0])], _snapped=True))
                continue
        loop = [back(*stand_in(u, v)) for u, v in stroke_points(st)]
        out.append({**{k: v for k, v in st.items() if k in ("role", "colour")}, "kind": "poly", "pts": loop,
                    "free": True})  # (a curve as drawn, in the stand-in space: never snapped)
    return dict(sh, strokes=out, _even=t0, _even_space=sp.kind, _even_a=a)


def range_chop(sh, stretches, g, count=False):
    """custom_chop.chop for a Range with Symmetrical steps (g = range_grid's): None when it isn't one.
    Bend with the gates: stand-in stretches widened to whole a, taken to each row's own gates from the middle.
    Straight sides ("_even_rows"): each row's own stretch rounded out to its own gates from the middle; along time
    (g = the cut squares) rounded out to the cut lines. Then back to back notes (count: how many each)."""
    import numpy as np
    from notes.custom_chop import on_grid
    from notes.gaterange import RangeKeys
    kind = sh.get("_even_space") or ("rows" if sh.get("_even_rows") else None)
    if kind == "rows" and isinstance(g, np.ndarray):
        kind = "cuts"  # (along time)
    elif kind == "time":
        if not isinstance(g, np.ndarray):
            return None
    elif kind is None or not isinstance(g, RangeKeys):
        return None
    t0 = int(sh["_even"])
    none = np.zeros(0, np.int64) if count else np.zeros((0, 3), np.int64)
    if kind == "time":  # (Bend with the gates: stand-in stretches widened to whole a, each a = one cut square)
        a = int(sh["_even_a"])
        st = on_grid(stretches, a, t0)
        if not len(st):
            return none
        j0, j1 = (st[:, 0] - t0) // a, (st[:, 1] - t0) // a
    else:
        st = np.array(stretches, np.int64).reshape(-1, 3)
        if not len(st):
            return none
    if kind in ("cuts", "time"):  # (past the Range's ends: the end squares' size on)
        edges = np.r_[g[:, 0], g[-1, 1]].astype(np.int64)
        e0, e1, last = int(edges[0]), int(edges[-1]), len(edges) - 1
        g0, g1 = max(1, int(edges[1] - edges[0])), max(1, int(edges[-1] - edges[-2]))

        def line(t, up):  # (the cut line at / before tick t, or at / after when up)
            inside = np.searchsorted(edges, t, "left" if up else "right") - (0 if up else 1)
            past = -((e1 - t) // g1) if up else (t - e1) // g1
            before = -((e0 - t) // g0) if up else (t - e0) // g0
            return np.where(t < e0, before, np.where(t > e1, last + past, np.clip(inside, 0, last)))
        if kind == "cuts":  # (straight sides: each row's own ends rounded out to the cut lines)
            j0 = line(st[:, 0], False)
            j1 = np.maximum(line(st[:, 1], True), j0 + 1)
        n = j1 - j0
        if count:
            return n
        k = np.repeat(j0, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)

        def at(j):  # (cut line j)
            return np.where(j < 0, e0 + j * g0, np.where(j > last, e1 + (j - last) * g1,
                                                          edges[np.clip(j, 0, last)]))
        return np.column_stack([at(k), at(k + 1), np.repeat(st[:, 2], n)])
    keys = np.unique(st[:, 2])
    gates = np.array([row_gates(g)(int(q)) for q in keys.tolist()], np.int64)[np.searchsorted(keys, st[:, 2])]
    if kind == "keys":
        a = int(sh["_even_a"])
        st = on_grid(st, a, t0)
        j0, j1 = (st[:, 0] - t0) // a, (st[:, 1] - t0) // a
    else:  # (straight sides: the row's own ends rounded out to its own gates)
        j0 = (st[:, 0] - t0) // gates
        j1 = np.maximum(-((t0 - st[:, 1]) // gates), j0 + 1)
    n = j1 - j0
    if count:
        return n
    k = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    gg = np.repeat(gates, n)
    starts = t0 + (np.repeat(j0, n) + k) * gg
    return np.column_stack([starts, starts + gg, np.repeat(st[:, 2], n)])


def unbent_strokes(sh, ppq):
    """A shape stored bent by its Range (Bend with the gates / along time): its straight-sided strokes again,
    corner to corner (hunt 2026-11-17: the bend stayed after the Range was switched off). Others as they are."""
    sp = range_space(sh, ppq)
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])
    if not isinstance(sp, RangeSpace) or to_uv is None:
        return sh["strokes"]
    out = []
    for st in sh["strokes"]:
        pts = st.get("pts") or []
        if plain_poly(st) and len(pts) >= 4 and all(abs(c - d) <= SAME for c, d in zip(pts[0], pts[-1])):
            xy = [(sp.fwd(to_bp(u, v)[0] * ppq, to_bp(u, v)[1]), to_bp(u, v)[1]) for u, v in pts[:-1]]
            corners = _straight_dropped(xy)  # (the row-middle points of a bent side lie on its stand-in line)
            loop = [list(to_uv((sp.lin_back(x) if sp.kind == "time" else sp.back(x, p)) / ppq, p))
                    for x, p in corners]  # (time: the straight drawing = the squares' share of the box)
            out.append(dict(st, pts=loop + [list(loop[0])]))
        else:
            out.append(st)
    return out


def time_bent(sh, strokes, ppq):
    """Bend with the gates along time: straight closed sides laid over the cut squares by their share of the box
    (a point at every row's middle), so each row steps by the same squares and the sides curve as the gates grow.
    The corners stay where they are. Other strokes / other Ranges: as they are."""
    sp = range_space(dict(sh, even=True), ppq)
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])
    if not isinstance(sp, RangeSpace) or sp.kind != "time" or to_uv is None:
        return strokes

    def real(b, p):
        return list(to_uv(sp.back(sp.lin(b * ppq), p) / ppq, p))
    out = []
    for st in strokes:
        pts = st.get("pts") or []
        if not (plain_poly(st) and len(pts) >= 4 and all(abs(c - d) <= SAME for c, d in zip(pts[0], pts[-1]))):
            out.append(st)
            continue
        bp = [to_bp(u, v) for u, v in pts]
        bent = []
        for (b0, p0), (b1, p1) in zip(bp, bp[1:]):
            bent.append(real(b0, p0))
            lo, hi = sorted((p0, p1))
            fs = sorted((q - p0) / (p1 - p0) for q in range(math.floor(lo), math.ceil(hi) + 1) if lo < q < hi)
            bent += [real(b0 + f * (b1 - b0), p0 + f * (p1 - p0)) for f in fs]  # (each row's middle it crosses)
        bent.append(list(bent[0]))
        out.append(dict(st, pts=bent))
    return out


def range_restored(before, after, ppq):
    """A Range changed on an even shape (Range window): (pts, strokes, even_free) stored for the new Range: a bent
    outline straightened first, then snapped / bent the new way. even_free re-marked when the drawing was only
    ours."""
    mine = (before.get("even_free") or {}).get("mark") == drawing_mark(before["strokes"])
    work = dict(after, strokes=unbent_strokes(before, ppq))
    pts, strokes = snapped_shape(work, ppq)
    strokes = time_bent(dict(work, pts=pts), strokes, ppq)
    free = before.get("even_free")
    return pts, strokes, (dict(free, mark=drawing_mark(strokes)) if mine and free else free)


def shown_strokes(sh, ppq):
    """The strokes where the notes show the shape (the landing line; what a let go stores): even_version's, but a
    Range's straight stand-in sides taken back (a point at every row's middle: the bent edge)."""
    ev = even_version(sh, ppq)
    if ev is sh or "_even_space" not in ev:
        return ev["strokes"]
    sp = range_space(sh, ppq)
    t0, a = ev["_even"], ev["_even_a"]
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])

    def real(b, p):  # (stand-in beats / pitch -> u, v of the real spot)
        return list(to_uv(sp.back((b * ppq - t0) / a, p) / ppq, p))

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
    """(rows, columns, depths, gate, grid start) of every note cell of base's (start, end, key) rows widened to
    whole gates, in row then column order. Only the cells themselves (hunt 2026-11-17: a grid over the whole box
    took 0.45 s and 750 MB for a thin slanted band at PPQ 15360)."""
    import numpy as np
    from notes.custom import merged_rows
    from notes.custom_chop import on_grid
    g, o = gate_ticks(sh["gate"], ppq), int(sh["_even"])
    st = merged_rows(on_grid(base, g, o))
    st = st[np.lexsort((st[:, 0], st[:, 2]))]
    j0s, j1s = (st[:, 0] - o) // g, (st[:, 1] - o) // g
    n = j1s - j0s
    first = np.repeat(np.cumsum(n) - n, n)
    cols = np.repeat(j0s, n) + np.arange(int(n.sum())) - first
    rows = np.repeat(st[:, 2], n)
    across = np.minimum(cols - np.repeat(j0s, n) + 1, np.repeat(j1s, n) - cols)  # (cells to the row's end)
    o2 = np.lexsort((rows, cols))  # (by column, then key: runs of keys one above the other)
    r, c = rows[o2], cols[o2]
    new = np.r_[True, (c[1:] != c[:-1]) | (r[1:] != r[:-1] + 1)]
    run = np.cumsum(new) - 1
    starts = np.flatnonzero(new)
    lo, hi = r[starts][run], r[np.r_[starts[1:], len(r)] - 1][run]
    up = np.empty(len(r), np.int64)
    up[o2] = np.minimum(r - lo + 1, hi - r + 1)  # (cells to the column's end)
    return rows, cols, np.minimum(across, up), g, o


def nested_even(sh, ppq, base, cfg):
    """Nested's steps for an even shape: step i = every cell at least i x band deep, as (start, end, key) rows;
    band = the deepest / Steps, at least one cell (Last size / Spacing / Turn / the centre point: not used). Each
    step looks only at the cells the one before kept (they only get fewer)."""
    import numpy as np
    rows, cols, depth, g, o = nested_cells(sh, ppq, base)
    most = int(depth.max()) if len(depth) else 0
    n = cfg["steps"]
    band = max(1, math.floor(most / n + 0.5))
    out = []
    for i in range(n):
        least = 1 + i * band
        if least > most:  # (deeper than the shape goes: empty)
            out.append(np.zeros((0, 3), np.int64))
            continue
        keep = depth >= least
        rows, cols, depth = rows[keep], cols[keep], depth[keep]
        new = np.r_[True, (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1] + 1)]  # (a run of cells on one key)
        s = np.flatnonzero(new)
        e = np.r_[s[1:], len(rows)] - 1
        out.append(np.column_stack([o + cols[s] * g, o + (cols[e] + 1) * g, rows[s]]).astype(np.int64))
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
    """A stroke of straight sides as drawn: not a curve, freehand, smoothed, with a pattern / Formula along it
    (pattern.has_formula: they stay as drawn, hunt 2026-11-17) or a polygon's twisted sides ("corners")."""
    from notes.pattern import has_formula
    return (st.get("kind") == "poly" and not st.get("smooth") and not st.get("free") and not st.get("corners")
            and not has_formula(st))


def stores_snap(sh):
    """Is the snapped drawing stored in sh (at a tick / let go)? Not for a sliced piece (its notes come from the
    even whole: re-snapping its cut edge broke it) nor a polygon (its own settings remake its lines on open /
    paste; its notes are even anyway). Hunt 2026-11-17."""
    return not sh.get("cut") and not sh.get("polygon")


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
