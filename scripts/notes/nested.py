"""Inside fill "Nested" (sh["nested"]): the inside cut into steps, each one a copy of the shape inside the one
before, each step a colour of its own (with Multi channel, like areas coloured by hand). The shape keeps its Fill /
Spam (sh["fill"]): Notes Fill = one note per stretch of a step, Spam = chopped as usual (the whole inside as one,
then each note takes the step where its middle is).

Ways ("way"):
- "scale": the outline made smaller (and turned) toward the centre point, in the view the shape was first drawn in
  (custom.drawn_view), so a circle's copies stay circles however the box was stretched. Each copy = the shape with
  its box moved, so its inside is worked out exactly like the shape's own (custom.find_spans). The smaller copy is
  on top; copies are cut where they reach past the shape.
- "band" (Even band): the inside shrunk inward step by step, every step equally thick all round, like the outline
  gate's even band (shrink.inner_rows, exact, in the drawn view). No centre point and no turn.
- "blend" (Blend toward the centre point): steps that follow the outline near it and round off into the centre
  point (a smooth heat-like value on shrink.field's grid, mixed with the Even band by "mix" 0..1). Its steps' edges
  are as fine as that grid; the shape's own edge stays exact. No turn.

sh["nested"] = {"way", "steps" (copies, the shape itself counts), "last" (the last copy's size, 0..1), "spacing"
(-100..100: + = the copies closer together toward the last one), "turn" (degrees per step), "colours" (how many
channels the steps take turns over), "skip" (True = every other step left empty), "at" ([u, v] in the shape's box,
only once moved; missing = the middle of the filled part), "mix" (Blend: 0 = Even band .. 1 = all rounded), "of"
(Scaling: {"name", "strokes"} = the copies are this library shape, filling the shape's box; missing = itself),
"angle" (Scaling: degrees the copies' drawing is turned first, round the centre point; Turn adds on top), "morph"
(Scaling with "of": True = the steps change from the shape into that drawing bit by bit, morph_plan)}."""

import json
import math

import numpy as np

from files import speed
from notes.custom import (clean_strokes, cut_out, drawn_view, edge_loops, find_spans, fill_plan, frame_to_bp,
                          frame_to_uv, has_nested, has_tiles, inside_rows, merged_rows, poly_edges, rows_spans)
from notes.paths import TOP_KEY, pitch_of
from notes.shrink import field, inner_rows, proportion

WAYS = ("scale", "band", "blend")
NESTED_DEFAULTS = {"way": "scale", "steps": 8, "last": 0.0, "spacing": 0, "turn": 0.0, "colours": 4, "skip": False,
                   "mix": 1.0, "angle": 0.0, "morph": False}
MOST_STEPS = 60
MORPH_POINTS = 200  # points spread round every piece of an outline while morphing (at least)
MOST_POINTS = 2000  # (more than that: spread evenly, corners may round off a little)
RECOLOUR = ("colours", "skip")  # (settings that only change the steps' colours, not their shapes)
MOST_COLOURS = 15
_spans = {}  # nested_spans, remembered


def clean_nested(c):
    """A saved sh["nested"] -> a valid one, or None (off)."""
    if not isinstance(c, dict):
        return None
    out = dict(NESTED_DEFAULTS)
    if c.get("way") in WAYS:
        out["way"] = c["way"]
    try:
        out["steps"] = max(2, min(MOST_STEPS, int(c.get("steps", out["steps"]))))
        out["last"] = max(0.0, min(1.0, float(c.get("last", out["last"]))))
        out["spacing"] = max(-100, min(100, int(round(float(c.get("spacing", 0))))))
        turn = float(c.get("turn", 0))
        out["turn"] = max(-180.0, min(180.0, turn)) if math.isfinite(turn) else 0.0
        angle = float(c.get("angle", 0))
        out["angle"] = max(-180.0, min(180.0, angle)) if math.isfinite(angle) else 0.0
        out["colours"] = max(1, min(MOST_COLOURS, int(c.get("colours", out["colours"]))))
        mix = float(c.get("mix", out["mix"]))
        out["mix"] = max(0.0, min(1.0, mix)) if math.isfinite(mix) else 1.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out["last"]):
        out["last"] = 0.0
    out["skip"] = c.get("skip") is True
    out["morph"] = c.get("morph") is True
    of = c.get("of")
    if isinstance(of, dict):  # Copies of another library shape (Scaling): its own copy of the drawing
        strokes = clean_strokes(of.get("strokes"))
        if strokes:
            out["of"] = {"name": str(of.get("name", ""))[:100], "strokes": strokes}
    at = c.get("at")
    if isinstance(at, (list, tuple)) and len(at) == 2:
        try:
            u, v = float(at[0]), float(at[1])
            if math.isfinite(u) and math.isfinite(v):
                out["at"] = [u, v]
        except (TypeError, ValueError):
            pass
    return out


def centre_key(sh):
    """Which setting holds the shape's centre point: "nested", "tiles" (tiles.py: where one tile sits), or None."""
    if has_nested(sh):
        return "nested"
    return "tiles" if has_tiles(sh) else None


def uses_centre(sh):
    """The shape's fill uses the centre point (its handle shows): Nested but not Even band, and Tiles."""
    key = centre_key(sh)
    return key == "tiles" or key == "nested" and sh["nested"].get("way") != "band"


def set_centre(sh, u, v):
    """The centre point moved to (u, v) in the shape's box."""
    key = centre_key(sh)
    if key:
        sh[key] = dict(sh[key], at=[round(u, 6), round(v, 6)])


def reset_centre(sh):
    """The centre point back where it starts (the middle of the filled part)."""
    key = centre_key(sh)
    if key:
        sh[key] = {k: v for k, v in sh[key].items() if k != "at"}


def step_shares(cfg):
    """How far along each step is, Spacing taken in: 0 = the shape itself .. 1 = the last copy."""
    n = cfg["steps"]
    g = 2.0 ** (-cfg["spacing"] / 50)
    return [(i / (n - 1) if n > 1 else 0.0) ** g for i in range(n)]


def step_sizes(cfg):
    """Each copy's (size, turn in radians), the shape itself first."""
    last = cfg["last"]
    return [(1 + (last - 1) * t, math.radians(cfg["turn"] * i)) for i, t in enumerate(step_shares(cfg))]


def centre_bp(sh, ppq):
    """The centre point in beats / pitch: the one set (u, v in the box), else the middle of the filled part."""
    at = sh[centre_key(sh)].get("at")
    if at:
        return frame_to_bp(sh["pts"])(*at)
    rows = inside_rows(sh, ppq)
    if not len(rows):
        (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
        return (b1 + b2) / 2, (p1 + p2) / 2
    w = (rows[:, 1] - rows[:, 0]).astype(float)
    total = w.sum() or 1.0
    return (float((w * (rows[:, 0] + rows[:, 1]) / 2).sum() / total / ppq),
            float((w * rows[:, 2]).sum() / total))


def handle_bp(sh, ppq):
    """Where the centre handle shows: centre_bp, but a sliced piece's is its whole's (its notes are made from the
    whole, sliced.source)."""
    if sh.get("cut"):
        from notes.sliced import source
        got = source(sh)
        if got and centre_key(got[0]):
            return centre_bp(got[0], ppq)
    return centre_bp(sh, ppq)


def centre_uv(sh, ppq):
    """The centre point in the shape's box (u, v), or None if the box is flat."""
    at = sh[centre_key(sh)].get("at")
    if at:
        return tuple(at)
    to_uv = frame_to_uv(sh["pts"])
    return to_uv(*centre_bp(sh, ppq)) if to_uv else None


def view_of(sh):
    """The view the shape is measured in (shrink.py's (a, c)): as first drawn, else as wide as it's tall."""
    got = drawn_view(sh)
    if got:
        return got
    polys = fill_plan(sh)["polys"]
    return (0.0, proportion(polys)) if polys else (0.0, 1.0)


def moved_frame(pts, centre, size, turn, view):
    """The box's frame points made `size` times as big and turned `turn` radians round centre (beats / pitch), as
    seen in the view (a, c): (b, p) is at (b + a p, c p) there."""
    a, c = view
    cb, cp = centre
    cx, cy = cb + a * cp, c * cp
    co, si = math.cos(turn) * size, math.sin(turn) * size
    out = []
    for b, p in pts:
        x, y = b + a * p - cx, c * p - cy
        x, y = cx + co * x - si * y, cy + si * x + co * y
        p2 = y / c
        out.append([x - a * p2, p2])
    return out


def nested_spans(sh, ppq):
    """(start, end, key, colour) ticks of every stretch filled, colour 0 = the shape's own (like custom.area_spans;
    an empty step left out). Remembered."""
    cfg = sh["nested"]
    shaped = {k: v for k, v in cfg.items() if k not in RECOLOUR}
    key = (json.dumps([sh["strokes"], sh["pts"], shaped, sh.get("round")]), bool(sh.get("union")), ppq)
    got = _spans.get(key)  # (the steps, before their colours: Colours / Every other one empty only recolour)
    if got is None:
        if len(_spans) > 60:
            _spans.clear()
        got = _spans[key] = (nested_steps(sh, ppq), {})
    steps, coloured = got
    paint = tuple(cfg[k] for k in RECOLOUR)
    out = coloured.get(paint)
    if out is None:
        out = coloured[paint] = painted(steps, cfg)
        out.flags.writeable = False
    return out


def find_nested(sh, ppq):
    """nested_spans, worked out again (not remembered)."""
    return painted(nested_steps(sh, ppq), sh["nested"])


def nested_steps(sh, ppq):
    """Each step's (start, end, key) stretches, the shape itself first ([] when there's nothing to fill)."""
    cfg = sh["nested"]
    base = inside_rows(sh, ppq)
    if not len(base):
        return []
    centre, view = centre_bp(sh, ppq), view_of(sh)
    if view[1] <= 0:
        return [base]
    if cfg["way"] == "band":
        return band_steps(sh, ppq, base, view)
    if cfg["way"] == "blend":
        return blend_steps(sh, ppq, base, centre, view)
    return scaled_steps(sh, ppq, base, centre, view)


def band_steps(sh, ppq, base, view):
    """Even band: step i = the inside shrunk inward by (1 - its size) of the deepest spot's distance to the outline
    (shrink.inner_rows, exact like the outline gate, in the view it was first drawn in)."""
    loops, walled = edge_loops(sh)
    deepest = deepest_field(sh, view)[1]
    steps = [base]
    for size, _ in step_sizes(sh["nested"])[1:]:
        r = (1 - size) * deepest
        before = steps[-1]  # (a deeper step lies inside the one before: only its key rows are worked out)
        keys = sorted(set(before[:, 2].tolist())) if len(before) else []
        steps.append(inner_rows(loops, walled, keys, r, ppq, view) if r > 0 and keys else
                     before if r <= 0 else np.zeros((0, 3), np.int64))
    return steps


def deepest_field(sh, view):
    """shrink.field over the whole shape (every distance exact) and its deepest spot's distance to the outline."""
    loops, walled = edge_loops(sh)
    pts = np.concatenate([np.asarray(p, float).reshape(-1, 2) for p in loops if len(p)])
    reach = float(max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1] * view[1]))) or 1.0
    got = field(loops, walled, reach, view)
    return got, max(float(got[3].max()), 1e-9)


def smooth_values(f, step, cx, cy, x0, y0):
    """Blend's smooth value on the field's grid: 0 on the outline (and outside), 1 on a small disc round the centre
    point (cx, cy in the view; the nearest inside spot when it's outside), like heat spreading from it (solved by
    red-black over-relaxation); then evened out so its steps are about equally thick (each level's average
    distance to the outline, rising)."""
    inside = f > 0
    h, w = f.shape
    if not inside.any():
        return np.zeros(f.shape)
    jc, ic = (cy - y0) / step, (cx - x0) / step
    jj, ii = np.mgrid[0:h, 0:w]
    d2 = (jj - jc) ** 2 + (ii - ic) ** 2
    if not inside[min(h - 1, max(0, int(round(jc)))), min(w - 1, max(0, int(round(ic))))]:
        k = np.argmin(np.where(inside, d2, np.inf))
        jc, ic = divmod(k, w)
        d2 = (jj - jc) ** 2 + (ii - ic) ** 2
    hot = inside & (d2 <= max(1.0, max(h, w) / 60) ** 2)
    free = inside & ~hot
    # (worked in place on a copy with a zero border, only on the cells each half changes: the same sums as the whole
    # grid at once, much quicker while the centre point is dragged)
    pad = np.zeros((h + 2, w + 2))
    pad[1:-1, 1:-1] = np.where(hot, 1.0, 0.0)
    flat, wide = pad.reshape(-1), w + 2
    red = free & ((jj + ii) % 2 == 0)
    black = free & ~red
    om = 1.9
    cells = [np.flatnonzero(np.pad(part, 1)) for part in (red, black)]
    fast = speed.loops()
    if fast:  # (the same, as a compiled loop: files/fastloops.py)
        fast.smooth_sor(flat, *cells, wide, om, 4 * max(h, w))
    else:
        for _ in range(4 * max(h, w)):
            for at in cells:
                g = (flat[at - 1] + flat[at + 1] + flat[at - wide] + flat[at + wide]) / 4
                here = flat[at]
                flat[at] = here + om * (g - here)
    u = pad[1:-1, 1:-1].copy()
    # evened out by each level's average distance from the centre point (falling as the level rises): 1 - that
    # over the outline's. (By the distance to the outline instead, every spot deeper in than the centre point got
    # the top value: one big middle step when the point was moved off the middle.)
    bins = 400
    at = np.minimum(bins - 1, (u * bins).astype(np.int64))
    away = np.sqrt(d2)
    sums = np.bincount(at[free], away[free], bins)
    counts = np.bincount(at[free], minlength=bins)
    level = np.where(counts > 0, sums / np.maximum(counts, 1), np.inf)
    level = np.minimum.accumulate(level)
    level[~np.isfinite(level)] = 0.0
    edge = level[0] if level[0] > 0 else max(float(level.max()), 1e-9)
    out = np.where(inside, 1 - np.minimum(level[at], edge) / edge, 0.0)
    out[hot] = 1.0
    return out


def blend_steps(sh, ppq, base, centre, view):
    """Blend toward the centre point: each spot's smooth value (smooth_values) mixed with its Even band depth by
    "mix" (0 = Even band, 1 = all smooth); step i = every spot at least (1 - its size) in. Read along each key row's
    middle line at a fine step and cut exactly to the shape (the steps' edges are as fine as the grid, not exact)."""
    (x0, y0, step, f, _), deepest = deepest_field(sh, view)
    a, c = view
    t = smooth_values(f, step, centre[0] + a * centre[1], c * centre[1], x0, y0)
    mix = sh["nested"].get("mix", 1.0)
    t = (1 - mix) * np.maximum(f, 0) / deepest + mix * t
    # every stretch of the inside cut into cells about a third of a grid square long, each read at its middle
    s, e, k = base[:, 0], base[:, 1], base[:, 2]
    cell = max(1.0, step / 3 * ppq)
    n = np.maximum(1, np.ceil((e - s) / cell).astype(np.int64))
    which = np.repeat(np.arange(len(base)), n)
    j = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    width = (e - s) / n
    c0 = s[which] + j * width[which]
    mid = (c0 + width[which] / 2) / ppq
    p = k[which].astype(float)
    gx, gy = (mid + a * p - x0) / step, (c * p - y0) / step
    h, w = t.shape
    ix, iy = np.clip(np.floor(gx).astype(np.int64), 0, w - 2), np.clip(np.floor(gy).astype(np.int64), 0, h - 2)
    fx, fy = np.clip(gx - ix, 0, 1), np.clip(gy - iy, 0, 1)
    val = ((t[iy, ix] * (1 - fx) + t[iy, ix + 1] * fx) * (1 - fy)
           + (t[iy + 1, ix] * (1 - fx) + t[iy + 1, ix + 1] * fx) * fy)
    starts = np.floor(c0 + 0.5).astype(np.int64)
    ends = np.floor(c0 + width[which] + 0.5).astype(np.int64)
    steps = [base]
    for size, _ in step_sizes(sh["nested"])[1:]:
        on = val >= 1 - size
        steps.append(merged_rows(np.column_stack([starts[on], ends[on], k[which][on]])) if on.any()
                     else np.zeros((0, 3), np.int64))
    return steps


def scaled_steps(sh, ppq, base, centre, view):
    """Scaling: step i = the shape's box made smaller and turned round the centre point (exact, like its own); with
    "of", the copies are another drawing filling the same box (the shape itself stays the first step), turned by
    "angle" first. With "morph" (and "of"), each copy's drawing is the shape's outline part way into the other's."""
    cfg = sh["nested"]
    of = cfg.get("of")
    drawing = dict(sh, strokes=of["strokes"]) if of else sh
    way = -1 if mirrored(sh) else 1  # (a flipped shape's copies turn the other way: its fill is a mirror image)
    angle = way * math.radians(cfg.get("angle", 0.0))
    plan = morph_plan(sh, drawing, centre, view, angle) if of and cfg.get("morph") else None
    steps = []
    for i, ((size, turn), f) in enumerate(zip(step_sizes(cfg), step_shares(cfg))):
        turn *= way
        if i == 0:
            steps.append(base)
            continue
        if size < 1e-4:
            steps.append(np.zeros((0, 3), np.int64))
            continue
        if plan:  # (the angle is in the plan's drawing already)
            frame = moved_frame(sh["pts"], centre, size, turn, view)
            loops = morph_loops(plan, f)
            if sh.get("union"):  # (Overlaps cancel out off: every area closed in, find_spans' own way)
                copy = dict(sh, strokes=[{"kind": "poly", "pts": l.tolist()} for l in loops], pts=frame)
                found = find_spans(copy, ppq)
            else:  # (plain even-odd loops: straight to their rows, no outline work for these one-off copies)
                to_bp = frame_to_bp(frame)
                found = loop_spans([np.column_stack(to_bp(l[:, 0], l[:, 1])) for l in loops], ppq)
        else:
            found = find_spans(dict(drawing, pts=moved_frame(sh["pts"], centre, size, turn + angle, view)), ppq)
        sp = np.asarray(found, np.int64).reshape(-1, 3)[:, [1, 2, 0]]
        steps.append(cut_out(sp, cut_out(sp, base)) if len(sp) else sp)  # (only where the shape is)
    return steps


def mirrored(sh):
    """The shape's box is flipped compared with how it was drawn (u along time, v up the keys; box_frame)."""
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    return (b1 - b0) * (p2 - p0) - (p1 - p0) * (b2 - b0) < 0


def view_loops(sh, view):
    """The closed loops a drawing's inside is made of (fill_plan), as points in the view (a, c), each going the same
    way round, no point twice in a row."""
    a, c = view
    out = []
    for poly in fill_plan(sh)["polys"]:
        p = np.asarray(poly, float).reshape(-1, 2)
        p = np.column_stack([p[:, 0] + a * p[:, 1], c * p[:, 1]])
        p = p[np.r_[True, np.hypot(*np.diff(p, axis=0).T) > 1e-12]]
        if len(p) > 1 and math.dist(p[0], p[-1]) <= 1e-12:
            p = p[:-1]
        if len(p) >= 3 and np.ptp(p, axis=0).max() > 1e-12:
            out.append(p[::-1] if loop_area(p) < 0 else p)
    return out


def spread(p, n):
    """n points round the loop p: its own points (so its corners stay sharp) and the rest shared out along its sides
    by their length; evenly spread when it has more than n points of its own."""
    q = np.vstack([p, p[:1]])
    side = np.hypot(*np.diff(q, axis=0).T)
    total, m = float(side.sum()), len(p)
    if m > n:
        along = np.concatenate([[0.0], np.cumsum(side)])
        d = np.arange(n) * total / n
        return np.column_stack([np.interp(d, along, q[:, 0]), np.interp(d, along, q[:, 1])])
    share = side / total * (n - m)
    k = np.floor(share).astype(np.int64)
    k[np.argsort(k - share)[:n - m - int(k.sum())]] += 1  # (what's left: to the sides that lost the most)
    each = k + 1
    at = np.repeat(np.arange(m), each)
    t = (np.arange(n) - np.repeat(np.cumsum(each) - each, each)) / np.repeat(each, each)
    return q[at] + (q[at + 1] - q[at]) * t[:, None]


def loop_area(p):
    """Signed area of a closed loop of points."""
    x, y = p[:, 0], p[:, 1]
    return float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum() / 2)


def lined_up(p, q):
    """q's points started at the one that makes them travel the shortest way from p's (same count, same way round):
    the start that lines them up best (cross-correlation, worked out for every start at once)."""
    zp, zq = p[:, 0] + 1j * p[:, 1], q[:, 0] + 1j * q[:, 1]
    k = int(np.argmax(np.fft.ifft(np.conj(np.fft.fft(zp)) * np.fft.fft(zq)).real))
    return np.roll(q, -k, axis=0)


_morphs = {}  # morph_plan, remembered (a centre move only remakes it with a Copy angle)


def morph_plan(sh, drawing, centre, view, angle):
    """How the shape's outline turns into the drawing's (turned `angle` round the centre point first), or None when
    either has nothing to fill: {"pairs": [(from, to)] point arrays in the view, "view", "to_uv"}. Each piece of the
    shape goes with the nearest piece of the drawing (where it is + how big; the nearest pairs first); one with no
    partner shrinks to its own middle, one of the drawing's grows from its own middle. Each pair has MORPH_POINTS
    points round it, or more when either piece has more corners than that (up to MOST_POINTS)."""
    key = json.dumps([sh["strokes"], sh["pts"], drawing["strokes"], view, angle, angle and centre])
    got = _morphs.get(key)
    if got is not None or key in _morphs:
        return got
    if len(_morphs) > 16:
        _morphs.clear()
    to_uv = frame_to_uv(sh["pts"])
    A, B = view_loops(sh, view), view_loops(drawing, view)
    if to_uv is None or not A or not B:
        _morphs[key] = None
        return None
    if angle:
        a, c = view
        cx, cy = centre[0] + a * centre[1], c * centre[1]
        co, si = math.cos(angle), math.sin(angle)
        B = [np.column_stack([cx + co * (p[:, 0] - cx) - si * (p[:, 1] - cy),
                              cy + si * (p[:, 0] - cx) + co * (p[:, 1] - cy)]) for p in B]
    ca, cb = np.array([p.mean(axis=0) for p in A]), np.array([p.mean(axis=0) for p in B])
    ra, rb = (np.sqrt(np.abs([loop_area(p) for p in X])) for X in (A, B))
    cost = np.hypot(*(ca[:, None] - cb[None]).transpose(2, 0, 1)) + np.abs(ra[:, None] - rb[None])
    used_a, used_b, pairs = set(), set(), []
    most = min(len(A), len(B))
    for at in np.argsort(cost, axis=None, kind="stable"):  # (the nearest pairs first)
        i, j = divmod(int(at), len(B))
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        n = max(MORPH_POINTS, min(MOST_POINTS, max(len(A[i]), len(B[j]))))
        p = spread(A[i], n)
        pairs.append((p, lined_up(p, spread(B[j], n))))
        if len(used_a) == most:
            break
    for X, used, mid, first in ((A, used_a, ca, True), (B, used_b, cb, False)):
        for i, p in enumerate(X):
            if i not in used:
                p = spread(p, max(MORPH_POINTS, min(MOST_POINTS, len(p))))
                dot = np.repeat(mid[i][None], len(p), axis=0)
                pairs.append((p, dot) if first else (dot, p))
    got = _morphs[key] = {"pairs": pairs, "view": view, "to_uv": to_uv}
    return got


def morph_loops(plan, f):
    """The drawing f of the way (0 = the shape, 1 = the other drawing) as closed loops of (u, v) points in the
    shape's box (the first point again at the end); pieces shrunk to a point left out."""
    a, c = plan["view"]
    out = []
    for p, q in plan["pairs"]:
        x = p + (q - p) * f
        if np.ptp(x[:, 0]) < 1e-9 and np.ptp(x[:, 1]) < 1e-9:
            continue
        pitch = x[:, 1] / c
        u, v = plan["to_uv"](x[:, 0] - a * pitch, pitch)
        out.append(np.column_stack([np.append(u, u[0]), np.append(v, v[0])]))
    return out


def loop_spans(polys, ppq):
    """find_spans for closed loops in beats / pitch filled even-odd (the same rows and ticks), straight from them."""
    if not polys:
        return []
    ps = np.concatenate(polys)[:, 1]
    q0, q1 = max(0, pitch_of(float(ps.min()))), min(TOP_KEY, pitch_of(float(ps.max())))
    return rows_spans(poly_edges(polys), q0, q1, ppq)


def painted(steps, cfg):
    """The steps' stretches, each later (smaller) one over the ones before, with their colours."""
    none = np.zeros((0, 4), np.int64)
    out, covered = [], np.zeros((0, 3), np.int64)
    every = 2 if cfg["skip"] else 1
    for i in range(len(steps) - 1, -1, -1):  # (the later, smaller copy on top)
        sp = steps[i]
        if not len(sp):
            continue
        piece = cut_out(sp, covered) if len(covered) else sp
        covered = merged_rows(np.concatenate([covered, sp]))
        if i % every or not len(piece):
            continue
        colour = (i // every) % cfg["colours"]
        out.append(np.column_stack([piece, np.full(len(piece), colour, np.int64)]))
    if not out:
        return none
    got = np.concatenate(out)
    return got[np.lexsort((got[:, 0], got[:, 2]))].astype(np.int64)
