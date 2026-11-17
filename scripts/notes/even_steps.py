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
    snapped to clean lines, hidden "_even" = the grid's start tick for chop."""
    if not even_on(sh) or sh.get("range"):  # (a gate Range: its own rows, stage E)
        return sh
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
    pts = [list(p) for p in sh["pts"]]
    from notes.custom import frame_upright
    if frame_upright(pts):
        g = gate_ticks(sh["gate"], ppq) / ppq
        w = pts[1][0] - pts[0][0]
        whole = max(1, math.floor(abs(w) / g + 0.5)) * g
        if w >= 0:  # (the left edge stays: Auto's grid counts from it)
            pts[1][0] = pts[0][0] + whole
        else:  # (flipped sideways: point 1 is the left edge)
            pts[0][0] = pts[2][0] = pts[1][0] + whole
    ev = even_version(dict(sh, even=True), ppq)
    if ev is sh or "_even" not in ev:
        return pts, sh["strokes"]
    to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(pts)
    out = []
    for old, new in zip(sh["strokes"], ev["strokes"]):
        if new is old or to_uv is None:
            out.append(old)
        else:
            out.append(dict(new, pts=[list(to_uv(*to_bp(u, v))) for u, v in new["pts"]]))
    return pts, out


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
    folds away to nothing. Snapped until nothing moves: the stored even shape snaps to itself."""
    for _ in range(AGAIN):
        got = _snapped_loop(pts)
        if got is None or len(got) == len(pts) and all(_length(p, q) <= 1e-9 for p, q in zip(got, pts)):
            return got
        pts = got
    return pts


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
