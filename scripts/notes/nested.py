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

sh["nested"] = {"way", "steps" (copies, the shape itself counts), "last" (the last copy's size, 0..1), "spacing"
(-100..100: + = the copies closer together toward the last one), "turn" (degrees per step), "colours" (how many
channels the steps take turns over), "skip" (True = every other step left empty), "at" ([u, v] in the shape's box,
only once moved; missing = the middle of the filled part)}."""

import json
import math

import numpy as np

from notes.custom import (cut_out, drawn_view, edge_loops, find_spans, fill_plan, frame_to_bp, frame_to_uv,
                          inside_rows, merged_rows)
from notes.shrink import field, inner_rows, proportion

WAYS = ("scale", "band")
NESTED_DEFAULTS = {"way": "scale", "steps": 8, "last": 0.0, "spacing": 0, "turn": 0.0, "colours": 4, "skip": False}
MOST_STEPS = 60
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
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out["last"]):
        out["last"] = 0.0
    out["skip"] = c.get("skip") is True
    at = c.get("at")
    if isinstance(at, (list, tuple)) and len(at) == 2:
        try:
            u, v = float(at[0]), float(at[1])
            if math.isfinite(u) and math.isfinite(v):
                out["at"] = [u, v]
        except (TypeError, ValueError):
            pass
    return out


def uses_centre(sh):
    """The Nested way uses the centre point (its handle shows): not Even band."""
    return sh["nested"].get("way") != "band"


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
    at = sh["nested"].get("at")
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


def centre_uv(sh, ppq):
    """The centre point in the shape's box (u, v), or None if the box is flat."""
    at = sh["nested"].get("at")
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
    key = (json.dumps([sh["strokes"], sh["pts"], cfg, sh.get("round")]), bool(sh.get("union")), ppq)
    got = _spans.get(key)
    if got is None:
        if len(_spans) > 60:
            _spans.clear()
        got = _spans[key] = find_nested(sh, ppq)
        got.flags.writeable = False
    return got


def find_nested(sh, ppq):
    cfg = sh["nested"]
    base = inside_rows(sh, ppq)
    none = np.zeros((0, 4), np.int64)
    if not len(base):
        return none
    centre, view = centre_bp(sh, ppq), view_of(sh)
    if view[1] <= 0:
        return np.column_stack([base, np.zeros(len(base), np.int64)])
    if cfg["way"] == "band":
        steps = band_steps(sh, ppq, base, view)
    else:
        steps = scaled_steps(sh, ppq, base, centre, view)
    return painted(steps, cfg)


def band_steps(sh, ppq, base, view):
    """Even band: step i = the inside shrunk inward by (1 - its size) of the deepest spot's distance to the outline
    (shrink.inner_rows, exact like the outline gate, in the view it was first drawn in)."""
    loops, walled = edge_loops(sh)
    keys = range(int(base[:, 2].min()), int(base[:, 2].max()) + 1)
    pts = np.concatenate([np.asarray(p, float).reshape(-1, 2) for p in loops if len(p)])
    reach = float(max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1] * view[1]))) or 1.0
    deepest = float(field(loops, walled, reach, view)[3].max())
    steps = [base]
    for size, _ in step_sizes(sh["nested"])[1:]:
        r = (1 - size) * deepest
        steps.append(inner_rows(loops, walled, keys, r, ppq, view) if r > 0 else base)
    return steps


def scaled_steps(sh, ppq, base, centre, view):
    """Scaling: step i = the shape's box made smaller and turned round the centre point (exact, like its own)."""
    steps = []
    for i, (size, turn) in enumerate(step_sizes(sh["nested"])):
        if i == 0:
            steps.append(base)
            continue
        if size < 1e-4:
            steps.append(np.zeros((0, 3), np.int64))
            continue
        copy = dict(sh, pts=moved_frame(sh["pts"], centre, size, turn, view))
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
