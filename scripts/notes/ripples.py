"""Inside fill "Ripples" (sh["ripples"]): rings that grow out from shapes drawn inside the outline, each ring a
colour of its own (with Multi channel, like Nested's steps). The shape keeps its Fill / Spam (sh["fill"]).

The drawing is read by its closed loops: the outermost ones are the FRAME (it only cuts the rings off, it makes
none of its own); loops right inside the frame are the SOURCES (a loop inside a source, like a donut's hole, is part
of that source). Ring k = every source scaled by size k round its own centre point (and turned), in the view the
shape was first drawn in (nested.view_of: a heart stays a heart however the box was stretched). Where copies meet,
the smaller one wins (rings in order, smallest first, each only where no smaller one is), which makes the seams.
Copies are cut to the frame; frame left over past the biggest copy stays empty.

sh["ripples"] = {"steps" (rings, up to MOST_STEPS), "grow" (the biggest copy's size, 1 = as drawn; used when "auto"
is off), "auto" (True = the biggest copy just reaches every spot of the frame), "edge" (True = one ring edge lies
exactly on each drawn source: sizes moved so a copy is 1), "turn" (degrees each ring turns more than the one inside
it; the ring at the drawn outline isn't turned, user), "spacing" (-100..100: + = the small rings closer together),
"colours", "skip" (every other ring empty), "inside" (inside the drawn sources: "rings" / "empty" / "plain" = one
colour), "at" ([[u, v] or None, ...] in the shape's box, one per source left to right; missing = its middle)}.
Colours count from the drawn outline: the ring just inside it is colour 0, so Steps / Grow to don't change it."""

import json
import math

import numpy as np

from notes.custom import cut_out, fill_plan, frame_to_bp, in_turns, merged_rows, poly_edges, rows_spans
from notes.nested import mirrored, view_loops, view_of
from notes.paths import TOP_KEY, pitch_of

INSIDES = ("rings", "empty", "plain")
RIPPLES_DEFAULTS = {"steps": 60, "grow": 5.0, "auto": True, "edge": True, "turn": 0.0, "spacing": 0, "colours": 4,
                    "skip": False, "inside": "rings"}
MOST_STEPS = 120
MOST_COLOURS = 15
MOST_GROW = 50.0  # (5000 %)
RAYS = 720  # directions a source's reach is measured in (Auto)
GRID = 64  # frame spots checked across (Auto), plus its corners
RECOLOUR = ("colours", "skip", "inside")  # (settings that only change the rings' colours, not their shapes)
_rings = {}  # ripple_rings, remembered


def clean_ripples(c):
    """A saved sh["ripples"] -> a valid one, or None (off)."""
    if not isinstance(c, dict):
        return None
    out = dict(RIPPLES_DEFAULTS)
    try:
        out["steps"] = max(2, min(MOST_STEPS, int(c.get("steps", out["steps"]))))
        grow = float(c.get("grow", out["grow"]))
        out["grow"] = max(1.0, min(MOST_GROW, grow)) if math.isfinite(grow) else RIPPLES_DEFAULTS["grow"]
        turn = float(c.get("turn", 0))
        out["turn"] = max(-180.0, min(180.0, turn)) if math.isfinite(turn) else 0.0
        out["spacing"] = max(-100, min(100, int(round(float(c.get("spacing", 0))))))
        out["colours"] = max(1, min(MOST_COLOURS, int(c.get("colours", out["colours"]))))
    except (TypeError, ValueError, OverflowError):
        return None
    for k in ("auto", "edge", "skip"):
        if k in c:
            out[k] = c[k] is True
    if c.get("inside") in INSIDES:
        out["inside"] = c["inside"]
    at = c.get("at")
    if isinstance(at, list) and any(at):
        out["at"] = [[float(p[0]), float(p[1])] if isinstance(p, (list, tuple)) and len(p) == 2
                     and all(isinstance(x, (int, float)) and math.isfinite(x) for x in p) else None for p in at[:1000]]
    return out


def inside_point(loop, others):
    """How many of the other loops hold this loop (tested at the middle of its first side)."""
    x, y = (loop[0] + loop[1]) / 2
    n = 0
    for o in others:
        xa, ya = o[:, 0], o[:, 1]
        xb, yb = np.roll(xa, -1), np.roll(ya, -1)
        cross = (ya > y) != (yb > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = xa + (y - ya) * (xb - xa) / (yb - ya)
        n += int(np.count_nonzero(cross & (x < xs)) % 2)
    return n


def parts(loops):
    """(frame loops, sources): each source = [its outline, the loops inside it...], left to right (by its middle)."""
    depth = [inside_point(p, loops[:i] + loops[i + 1:]) for i, p in enumerate(loops)]
    frame = [p for p, d in zip(loops, depth) if d == 0]
    outer = [p for p, d in zip(loops, depth) if d == 1]
    sources = [[p] for p in outer]
    for p, d in zip(loops, depth):
        if d >= 2:
            for src in sources:
                if inside_point(p, [src[0]]):
                    src.append(p)
                    break
    sources.sort(key=lambda s: tuple(np.round(middle(s[0]), 9)))
    return frame, sources


def middle(p):
    """A loop's middle (the centre of its area; its points' average when it has none)."""
    x, y = p[:, 0], p[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    c = x * y1 - x1 * y
    a = c.sum() / 2
    if abs(a) < 1e-12:
        return np.array([x.mean(), y.mean()])
    return np.array([((x + x1) * c).sum() / (6 * a), ((y + y1) * c).sum() / (6 * a)])


def centres(sh, sources, view):
    """Each source's centre point in the view: its "at" (u, v in the shape's box), else its middle."""
    at = (sh["ripples"].get("at") or [])
    a, c = view
    to_bp = frame_to_bp(sh["pts"])
    out = []
    for i, src in enumerate(sources):
        uv = at[i] if i < len(at) else None
        if uv:
            b, p = to_bp(*uv)
            out.append(np.array([b + a * p, c * p]))
        else:
            out.append(middle(src[0]))
    return out


def reach(outline, centre):
    """How far the outline is from the centre in RAYS directions (the farthest crossing; 0 where none)."""
    t = np.linspace(0, 2 * math.pi, RAYS, endpoint=False)
    dx, dy = np.cos(t)[:, None], np.sin(t)[:, None]
    a = outline - centre
    e = np.roll(a, -1, axis=0) - a
    den = dx * e[:, 1] - dy * e[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (a[:, 0] * e[:, 1] - a[:, 1] * e[:, 0]) / den
        u = (a[:, 0] * dy - a[:, 1] * dx) / den
    ok = (np.abs(den) > 1e-15) & (r > 0) & (u >= 0) & (u <= 1)
    return np.where(ok, r, 0).max(axis=1)


def frame_spots(frame):
    """Spots across the frame (a grid, only those inside it) and its corners, for Auto."""
    pts = np.concatenate(frame)
    (x0, y0), (x1, y1) = pts.min(axis=0), pts.max(axis=0)
    gx, gy = np.meshgrid(np.linspace(x0, x1, GRID), np.linspace(y0, y1, GRID))
    g = np.column_stack([gx.ravel(), gy.ravel()])
    inside = np.zeros(len(g), bool)
    for p in frame:
        xa, ya = p[:, 0], p[:, 1]
        xb, yb = np.roll(xa, -1), np.roll(ya, -1)
        y = g[:, 1:2]
        cross = (ya > y) != (yb > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = xa + (y - ya) * (xb - xa) / (yb - ya)
        inside ^= (np.count_nonzero(cross & (g[:, :1] < xs), axis=1) % 2).astype(bool)
    return np.concatenate([g[inside], pts])


def auto_grow(frame, sources, mids, turned):
    """The size the biggest copy needs so every frame spot lies in some source's copy (turned: copies turn, so each
    source counts as its nearest reach all round)."""
    spots = frame_spots(frame)
    need = np.full(len(spots), np.inf)
    for src, c in zip(sources, mids):
        r = reach(src[0], c)
        d = spots - c
        if turned:
            least = r[r > 0].min() if (r > 0).any() else 0
            rr = np.full(len(spots), least)
        else:
            i = np.round(np.arctan2(d[:, 1], d[:, 0]) / (2 * math.pi) * RAYS).astype(int) % RAYS
            rr = r[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            need = np.minimum(need, np.where(rr > 0, np.hypot(d[:, 0], d[:, 1]) / rr, np.inf))
    worst = need[np.isfinite(need)]
    return float(worst.max()) * 1.02 if len(worst) else 1.0


def ring_sizes(cfg, grow):
    """Each ring's copy size, smallest first, and k1 = the ring at the drawn outline ("edge": its size exactly 1,
    the sizes only ever made bigger, so Auto still fills; every ring bigger than the drawing: left as they are)."""
    n = cfg["steps"]
    p = 2.0 ** (cfg["spacing"] / 50)
    s = [grow * ((k + 1) / n) ** p for k in range(n)]
    if cfg["edge"] and s[0] <= 1:
        k1 = max(k for k in range(n) if s[k] <= 1)
        f = 1 / s[k1]
        return [x * f for x in s], k1
    return s, min(range(n), key=lambda k: abs(s[k] - 1))


def to_bp_loops(loops, view):
    """Loops in the view (no point twice, nested.view_loops) -> closed ones in beats / pitch."""
    a, c = view
    out = []
    for p in loops:
        q = p[:, 1] / c
        b = np.column_stack([p[:, 0] - a * q, q])
        out.append(np.concatenate([b, b[:1]]))
    return out


def spans_of(loops_bp, ppq):
    """(start, end, key) of closed loops in beats / pitch, filled even-odd (nested.loop_spans)."""
    if not loops_bp:
        return np.zeros((0, 3), np.int64)
    ps = np.concatenate(loops_bp)[:, 1]
    q0, q1 = max(0, pitch_of(float(ps.min()))), min(TOP_KEY, pitch_of(float(ps.max())))
    found = rows_spans(poly_edges(loops_bp), q0, q1, ppq)
    return np.asarray(found, np.int64).reshape(-1, 3)[:, [1, 2, 0]]


def within(spans, area):
    """Only the parts of the stretches inside the area."""
    if not len(spans) or not len(area):
        return np.zeros((0, 3), np.int64)
    return cut_out(spans, cut_out(spans, area))


def ripple_rings(sh, ppq):
    """(pieces, k1, drawn): pieces[k] = ring k's own stretches (only where no smaller ring is, cut to the frame),
    k1 = the ring at the drawn outline, drawn = the drawn sources' insides (cut to the frame). Remembered."""
    cfg = sh["ripples"]
    shaped = {k: v for k, v in cfg.items() if k not in RECOLOUR}
    key = (json.dumps([sh["strokes"], sh["pts"], shaped, sh.get("round")]), ppq)
    got = _rings.get(key)
    if got is not None:
        return got
    if len(_rings) > 30:
        _rings.clear()
    none = ([], 0, np.zeros((0, 3), np.int64))
    view = view_of(sh)
    if view[1] <= 0:
        got = none
    else:
        frame, sources = parts(view_loops(sh, view))
        got = make_rings(sh, cfg, frame, sources, view, ppq) if frame and sources else none
    _rings[key] = got
    return got


def make_rings(sh, cfg, frame, sources, view, ppq):
    mids = centres(sh, sources, view)
    turned = bool(cfg["turn"])
    grow = auto_grow(frame, sources, mids, turned) if cfg["auto"] else cfg["grow"]
    sizes, k1 = ring_sizes(cfg, grow)
    way = -1 if mirrored(sh) else 1  # (a flipped shape turns the other way: its fill is a mirror image)
    area = merged_rows(spans_of(to_bp_loops(frame, view), ppq))
    covered = np.zeros((0, 3), np.int64)
    pieces = []
    for k, size in enumerate(sizes):
        a = way * math.radians(cfg["turn"]) * (k - k1)
        co, si = math.cos(a) * size, math.sin(a) * size
        found = []
        for src, c in zip(sources, mids):
            moved = []
            for p in src:
                x, y = p[:, 0] - c[0], p[:, 1] - c[1]
                moved.append(np.column_stack([c[0] + co * x - si * y, c[1] + si * x + co * y]))
            found.append(spans_of(to_bp_loops(moved, view), ppq))
        ring = merged_rows(np.concatenate(found)) if found else np.zeros((0, 3), np.int64)
        piece = cut_out(ring, covered) if len(covered) and len(ring) else ring
        covered = merged_rows(np.concatenate([covered, ring]))
        pieces.append(within(piece, area))
    drawn = within(merged_rows(np.concatenate([spans_of(to_bp_loops(s, view), ppq) for s in sources])), area)
    return pieces, k1, drawn


def ripple_spans(sh, ppq):
    """(start, end, key, colour) ticks of every stretch filled (like nested.nested_spans)."""
    cfg = sh["ripples"]
    pieces, k1, drawn = ripple_rings(sh, ppq)
    rounds = bool(sh.get("_rounds"))
    every = 2 if cfg["skip"] else 1
    lift = (k1 // every // cfg["colours"] + 1) * cfg["colours"]  # (keeps every turn >= 0, ring k1 still colour 0)
    out = []
    for k, piece in enumerate(pieces):
        d = k1 - k  # (the ring just inside the drawn outline first)
        if d % every or not len(piece):
            continue
        if cfg["inside"] != "rings" and len(drawn):
            piece = cut_out(piece, drawn)
        colour = in_turns((-d) // every + lift, cfg["colours"], rounds)
        out.append(np.column_stack([piece, np.full(len(piece), colour, np.int64)]))
    if cfg["inside"] == "plain" and len(drawn):
        out.append(np.column_stack([drawn, np.full(len(drawn), in_turns(lift, cfg["colours"], rounds), np.int64)]))
    out = [o for o in out if len(o)]
    if not out:
        return np.zeros((0, 4), np.int64)
    got = np.concatenate(out)
    return got[np.lexsort((got[:, 0], got[:, 2]))].astype(np.int64)
