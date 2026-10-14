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
(Scaling: {"name", "strokes"} = the copies are this library shape, filling the shape's box; missing = itself)}."""

import json
import math

import numpy as np

from notes.custom import (clean_strokes, cut_out, drawn_view, edge_loops, find_spans, fill_plan, frame_to_bp,
                          frame_to_uv, has_nested, has_tiles, inside_rows, merged_rows)
from notes.shrink import field, inner_rows, proportion

WAYS = ("scale", "band", "blend")
NESTED_DEFAULTS = {"way": "scale", "steps": 8, "last": 0.0, "spacing": 0, "turn": 0.0, "colours": 4, "skip": False,
                   "mix": 1.0}
MOST_STEPS = 60
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
        out["colours"] = max(1, min(MOST_COLOURS, int(c.get("colours", out["colours"]))))
        mix = float(c.get("mix", out["mix"]))
        out["mix"] = max(0.0, min(1.0, mix)) if math.isfinite(mix) else 1.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out["last"]):
        out["last"] = 0.0
    out["skip"] = c.get("skip") is True
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


def step_sizes(cfg):
    """Each copy's (size, turn in radians), the shape itself first."""
    n, last = cfg["steps"], cfg["last"]
    g = 2.0 ** (-cfg["spacing"] / 50)
    out = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        out.append((1 + (last - 1) * t ** g, math.radians(cfg["turn"] * i)))
    return out


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
    u = np.where(hot, 1.0, 0.0)
    red = free & ((jj + ii) % 2 == 0)
    black = free & ~red
    om = 1.9
    for _ in range(4 * max(h, w)):
        for part in (red, black):
            p = np.pad(u, 1)
            g = (p[1:-1, :-2] + p[1:-1, 2:] + p[:-2, 1:-1] + p[2:, 1:-1]) / 4
            u = np.where(part, u + om * (g - u), u)
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
    "of", the copies are another drawing filling the same box (the shape itself stays the first step)."""
    of = sh["nested"].get("of")
    drawing = dict(sh, strokes=of["strokes"]) if of else sh
    steps = []
    for i, (size, turn) in enumerate(step_sizes(sh["nested"])):
        if i == 0:
            steps.append(base)
            continue
        if size < 1e-4:
            steps.append(np.zeros((0, 3), np.int64))
            continue
        copy = dict(drawing, pts=moved_frame(sh["pts"], centre, size, turn, view))
        sp = np.asarray(find_spans(copy, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]
        steps.append(cut_out(sp, cut_out(sp, base)) if len(sp) else sp)  # (only where the shape is)
    return steps


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
