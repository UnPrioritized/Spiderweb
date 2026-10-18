"""Inside fill "Ripples" (sh["ripples"]): rings that grow out from shapes drawn inside the outline, each ring a
colour of its own (with Multi channel, like Nested's steps). The shape keeps its Fill / Spam (sh["fill"]).

The drawing is read by its closed loops: a loop no other loop holds is a FRAME (it only cuts the rings off, it makes
none of its own); a loop a frame holds is a SOURCE (a loop inside a source, like a donut's hole, is part of that
source). "Holds" = most of the loop's outline lies inside the other's (so where a stroke starts, a side shared with
the frame or a heart poking out past it don't matter; two crossing hearts are two sources). Each frame is filled
only from its own sources (user: two boxes = two separate pictures). Ring k = every source scaled by size k round
its own centre point (and turned), in the view the shape was first drawn in (nested.view_of: a heart stays a heart
however the box was stretched). Where copies meet, the smaller one wins (rings in order, smallest first, each only
where no smaller one is), which makes the seams. Frame left over past the biggest copy stays empty.

sh["ripples"] = {"steps" (rings, up to MOST_STEPS), "grow" (the biggest copy's size, 1 = as drawn; used when "auto"
is off), "auto" (True = the biggest copy just reaches every spot of its frame), "edge" (True = one ring edge lies
exactly on each drawn source: sizes moved so a copy is 1), "turn" (degrees each ring turns more than the one inside
it; the ring at the drawn outline isn't turned, user), "spacing" (-100..100: + = the small rings closer together),
"colours", "skip" (every other ring empty), "inside" (inside the drawn sources: "rings" / "empty" / "plain" = one
colour), "at" ([[u, v] or None, ...] in the shape's box, one per source in the drawing's order, which flips and
moves don't change; missing = its middle), "turns" ([None (= Turn) / "other" (= -Turn) / degrees, ...] per source,
the same way), "trim" (True = Cut at the outline: rings stop at their frame; off = they grow past it to Grow to)}.
A drawing with no frame (no loop holds another) grows rings out of every loop to Grow to, uncut.
Colours count from the drawn outline: the ring just inside it is colour 0, so Steps / Grow to don't change it."""

import json
import math

import numpy as np

from notes.custom import cut_out, frame_to_bp, frame_to_uv, in_turns, merged_rows, poly_edges, rows_spans
from notes.nested import mirrored, view_loops, view_of
from notes.paths import TOP_KEY, pitch_of

INSIDES = ("rings", "empty", "plain")
RIPPLES_DEFAULTS = {"steps": 60, "grow": 5.0, "auto": True, "edge": True, "turn": 0.0, "spacing": 0, "colours": 4,
                    "skip": False, "inside": "rings", "trim": True}
MOST_STEPS = 120
MOST_COLOURS = 15
MOST_GROW = 50.0  # (5000 %)
RAYS = 720  # directions a source's reach is measured in (Auto)
GRID = 64  # frame spots checked across (Auto), plus its corners
SAMPLES = 96  # points spread round a loop to tell what holds it
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
    for k in ("auto", "edge", "skip", "trim"):
        if k in c:
            out[k] = c[k] is True
    if c.get("inside") in INSIDES:
        out["inside"] = c["inside"]
    at = c.get("at")
    if isinstance(at, list) and any(at):
        out["at"] = [[float(p[0]), float(p[1])] if isinstance(p, (list, tuple)) and len(p) == 2
                     and all(isinstance(x, (int, float)) and math.isfinite(x) for x in p) else None for p in at[:1000]]
    turns = c.get("turns")
    if isinstance(turns, list) and any(t is not None for t in turns):
        out["turns"] = [OTHER if t == OTHER else max(-180.0, min(180.0, float(t)))
                        if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) else None
                        for t in turns[:1000]]
    return out


# ---------------------------------------------------------------- each source by hand

OTHER = "other"  # (a source's turn: the other way round from Turn)
_middles = {}  # source_middles, remembered


def source_turn(cfg, n):
    """Degrees source n's rings turn each: its own, else Turn (or the other way)."""
    turns = cfg.get("turns") or []
    own = turns[n] if n < len(turns) else None
    return cfg["turn"] if own is None else -cfg["turn"] if own == OTHER else own


def own_list(cfg, key, n, value):
    """cfg's per-source list `key` with entry n set to value (None entries past the end; all None = no list)."""
    got = list(cfg.get(key) or [])
    got += [None] * (n + 1 - len(got))
    got[n] = value
    out = {k: v for k, v in cfg.items() if k != key}
    if any(x is not None for x in got):
        out[key] = got
    return out


def set_source(sh, key, n, value):
    """Source n's own centre point ("at": [u, v] in the shape's box) or turn ("turns"); None = back as all."""
    sh["ripples"] = own_list(sh["ripples"], key, n, value)


def recall(cache, key):
    """What remember kept under key (None: nothing), now counted as used last."""
    got = cache.pop(key, None)
    if got is not None:
        cache[key] = got
    return got


def remember(cache, key, value, most=30):
    """value kept in cache under key; past `most` the one used longest ago goes (forgetting them all made a heavy
    shape's rings worked out again whenever it was clicked, hunt)."""
    while len(cache) >= most:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def layout(sh):
    """(each source's middle (u, v) in the shape's box in the drawing's order, a frame drawn round sources): only
    which loops hold which, no rings (the side panel asks at every refresh). Remembered."""
    key = json.dumps([sh["strokes"], sh["pts"], sh.get("round")])
    got = recall(_middles, key)
    if got is not None:
        return got
    view, to_uv = view_of(sh), frame_to_uv(sh["pts"])
    found, framed = {}, False
    if view[1] > 0 and to_uv:
        a, c = view
        for frame, sources in parts(view_loops(sh, view)):
            framed = framed or frame is not None
            for n, src in sources:
                x, y = middle(src[0])
                found[n] = tuple(float(w) for w in to_uv(x - a * y / c, y / c))
    return remember(_middles, key, ([found[n] for n in sorted(found)], framed))


def source_middles(sh):
    """[(u, v), ...]: each source's middle in the shape's box, in the drawing's order."""
    return layout(sh)[0]


def framed(sh):
    """A frame is drawn round the sources (not lone loops growing free): Cut at the outline means something."""
    return layout(sh)[1]


def centre_uv(sh, n):
    """Source n's centre point (u, v) in the shape's box: its own, else its middle."""
    at = sh["ripples"].get("at") or []
    return tuple(at[n]) if n < len(at) and at[n] else source_middles(sh)[n]


def handle_spots(sh):
    """[(beat, pitch), ...]: where each source's centre point shows. A sliced piece: its whole's sources (its notes
    are made from the whole, sliced.source), at the piece's own points."""
    whole = sh
    if sh.get("cut"):
        from notes.sliced import source
        got = source(sh)
        if got and got[0].get("ripples"):
            whole = got[0]
    to_bp = frame_to_bp(whole["pts"])
    return [to_bp(*centre_uv(whole, n)) for n in range(len(source_middles(whole)))]


# ---------------------------------------------------------------- frames and sources

CHUNK = 2_000_000  # (points x sides worked out at once: a 5000-point loop would need GBs in one go)


def chunked(f):
    """f(points, loop, ...) run on the points a chunk at a time."""
    def run(pts, loop, *more):
        step = max(1, CHUNK // max(1, len(loop)))
        if len(pts) <= step:
            return f(pts, loop, *more)
        return np.concatenate([f(pts[i:i + step], loop, *more) for i in range(0, len(pts), step)])
    return run


@chunked
def inside_many(pts, loop):
    """Which of the points lie inside the loop (even-odd)."""
    xa, ya = loop[:, 0], loop[:, 1]
    xb, yb = np.roll(xa, -1), np.roll(ya, -1)
    x, y = pts[:, :1], pts[:, 1:]
    cross = (ya > y) != (yb > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = xa + (y - ya) * (xb - xa) / (yb - ya)
    return (np.count_nonzero(cross & (x < xs), axis=1) % 2).astype(bool)


@chunked
def near_many(pts, loop, eps):
    """Which of the points lie on the loop's outline (within eps)."""
    a, b = loop, np.roll(loop, -1, axis=0)
    d = b - a
    ll = (d ** 2).sum(axis=1)
    rel = pts[:, None, :] - a[None]
    t = np.clip((rel * d[None]).sum(axis=2) / np.where(ll > 0, ll, 1)[None], 0, 1)
    gap = rel - t[..., None] * d[None]
    return (np.hypot(gap[..., 0], gap[..., 1]) <= eps).any(axis=1)


def spread_round(loop, n=SAMPLES):
    """n points evenly along the loop's outline, its corners too."""
    closed = np.concatenate([loop, loop[:1]])
    seg = np.hypot(*np.diff(closed, axis=0).T)
    at = np.concatenate([[0], np.cumsum(seg)])
    if at[-1] <= 0:
        return loop
    t = np.linspace(0, at[-1], n, endpoint=False)
    even = np.column_stack([np.interp(t, at, closed[:, 0]), np.interp(t, at, closed[:, 1])])
    return np.concatenate([even, loop])


def loop_area(p):
    x, y = p[:, 0], p[:, 1]
    return abs(float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum())) / 2


def parts(loops):
    """[(frame loop, [(n, [its outline, the loops inside it...]), ...]), ...]: every frame with its sources, n =
    the source's place in the drawing's order (counted over all frames). No loop holding another: [(None, [every
    loop as a source])] (rings grow out from them with no frame, user)."""
    if not loops:
        return []
    allp = np.concatenate(loops)
    eps = 1e-7 * max(float(np.ptp(allp, axis=0).max()), 1e-12)
    areas = [loop_area(p) for p in loops]
    spots = [spread_round(p) for p in loops]
    holders = []
    for i, p in enumerate(loops):
        got = []
        for j, q in enumerate(loops):
            if j == i or areas[j] <= areas[i]:
                continue
            s = spots[i][~near_many(spots[i], q, eps)]
            if len(s) and np.count_nonzero(inside_many(s, q)) * 2 > len(s):
                got.append(j)
        holders.append(got)
    depth = [len(h) for h in holders]
    frames = {i: [] for i in range(len(loops)) if depth[i] == 0}
    sources = {}
    for i in range(len(loops)):
        if depth[i] == 1:
            sources[i] = [loops[i]]
            frames[holders[i][0]].append(i)
    for i in range(len(loops)):  # (deeper loops: part of the smallest source holding them)
        if depth[i] >= 2:
            mine = [j for j in holders[i] if j in sources]
            if mine:
                sources[min(mine, key=lambda j: areas[j])].append(loops[i])
    # (no loop holds another: each is a source, no frame; an empty frame beside a filled one stays empty, user)
    lone = [] if sources else list(frames)
    for i in lone:
        sources[i] = [loops[i]]
    order = {i: n for n, i in enumerate(sorted(sources))}
    out = [(loops[f], [(order[i], sources[i]) for i in srcs]) for f, srcs in frames.items() if srcs]
    return out + ([(None, [(order[i], sources[i]) for i in lone])] if lone else [])


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
    at = sh["ripples"].get("at") or []
    a, c = view
    to_bp = frame_to_bp(sh["pts"])
    out = []
    for n, src in sources:
        uv = at[n] if n < len(at) else None
        if uv:
            b, p = to_bp(*uv)
            out.append(np.array([b + a * p, c * p]))
        else:
            out.append(middle(src[0]))
    return out


# ---------------------------------------------------------------- ring sizes

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
    (x0, y0), (x1, y1) = frame.min(axis=0), frame.max(axis=0)
    gx, gy = np.meshgrid(np.linspace(x0, x1, GRID), np.linspace(y0, y1, GRID))
    g = np.column_stack([gx.ravel(), gy.ravel()])
    return np.concatenate([g[inside_many(g, frame)], frame])


def auto_grow(frame, sources, mids, turned):
    """The size the biggest copy needs so every spot of the frame lies in some source's copy (turned, per source:
    its copies turn, so it counts as its nearest reach all round)."""
    spots = frame_spots(frame)
    need = np.full(len(spots), np.inf)
    for (_, src), c, turned in zip(sources, mids, turned):
        r = reach(src[0], c)
        d = spots - c
        if turned:
            rr = np.full(len(spots), r[r > 0].min() if (r > 0).any() else 0)
        else:
            rr = r[np.round(np.arctan2(d[:, 1], d[:, 0]) / (2 * math.pi) * RAYS).astype(int) % RAYS]
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


# ---------------------------------------------------------------- notes

def to_bp_loops(loops, view):
    """Loops in the view (no point twice, nested.view_loops) -> closed ones in beats / pitch."""
    a, c = view
    out = []
    for p in loops:
        q = p[:, 1] / c
        b = np.column_stack([p[:, 0] - a * q, q])
        out.append(np.concatenate([b, b[:1]]))
    return out


def spans_of(loops_bp, ppq, wind=False):
    """(start, end, key) of closed loops in beats / pitch, filled even-odd (nested.loop_spans). wind: Overlaps
    cancel out off, every area closed in (where a loop crosses itself and the holes too: nonzero)."""
    if not loops_bp:
        return np.zeros((0, 3), np.int64)
    ps = np.concatenate(loops_bp)[:, 1]
    q0, q1 = max(0, pitch_of(float(ps.min()))), min(TOP_KEY, pitch_of(float(ps.max())))
    found = rows_spans(poly_edges(loops_bp), q0, q1, ppq, wind)
    return np.asarray(found, np.int64).reshape(-1, 3)[:, [1, 2, 0]]


def union_spans(sets, ppq, wind=False):
    """Several sets of closed loops (beats / pitch), each filled even-odd, together (where any of them is): one
    pass over the key rows for them all, each set moved along time into a lane of its own so they can't cancel each
    other out, then moved back (a pass per source was most of the time with many sources)."""
    if len(sets) == 1:
        return spans_of(sets[0], ppq, wind)
    lows = [math.floor(min(float(p[:, 0].min()) for p in s)) for s in sets]
    wide = max(float(max(p[:, 0].max() for p in s)) - lo for s, lo in zip(sets, lows)) + 2
    lane = int(math.ceil(wide))
    shifts = [j * lane - lo for j, lo in enumerate(lows)]
    moved = [p + np.array([sh, 0.0]) for s, sh in zip(sets, shifts) for p in s]
    found = spans_of(moved, ppq, wind)
    if not len(found):
        return found
    j = np.clip(found[:, 0] // (lane * ppq), 0, len(sets) - 1)
    back = np.asarray(shifts, np.int64)[j] * ppq
    found = np.column_stack([found[:, 0] - back, found[:, 1] - back, found[:, 2]])
    return merged_rows(found)


def within(spans, area):
    """Only the parts of the stretches inside the area."""
    if not len(spans) or not len(area):
        return np.zeros((0, 3), np.int64)
    return cut_out(spans, cut_out(spans, area))


def ripple_rings(sh, ppq):
    """[(pieces, k1, drawn, framed), ...] for each frame: pieces[k] = ring k's own stretches (only where no smaller
    ring is, cut to the frame), k1 = the ring at the drawn outline, drawn = the drawn sources' insides (cut to the
    frame), framed = a frame was drawn round them (not the lone ones). Remembered."""
    cfg = sh["ripples"]
    shaped = {k: v for k, v in cfg.items() if k not in RECOLOUR}
    wind = bool(sh.get("union"))  # (Overlaps cancel out off: every area closed in, like Nested)
    key = (json.dumps([sh["strokes"], sh["pts"], shaped, sh.get("round")]), wind, ppq)
    got = recall(_rings, key)
    if got is not None:
        return got
    got = []
    view = view_of(sh)
    if view[1] > 0:
        taken = np.zeros((0, 3), np.int64)  # (frames that overlap: the earlier one keeps the overlap)
        for frame, sources in parts(view_loops(sh, view)):
            cut = frame is not None and cfg["trim"]
            if cut:
                area = merged_rows(spans_of(to_bp_loops([frame], view), ppq, wind))
                area = cut_out(area, taken) if len(taken) and len(area) else area
                keep = (lambda s, area=area: within(s, area))
            else:  # (no frame, or Cut at the outline off: the rings go as far as they grow, user)
                keep = (lambda s, taken=taken: cut_out(s, taken) if len(s) and len(taken) else s)
            pieces, k1, drawn = make_rings(sh, cfg, frame if cut else None, sources, view, keep, ppq)
            if not cut:
                area = merged_rows(np.concatenate([drawn] + pieces))
            taken = merged_rows(np.concatenate([taken, area]))
            got.append((pieces, k1, drawn, frame is not None))
    return remember(_rings, key, got)


def make_rings(sh, cfg, frame, sources, view, keep, ppq):
    """(pieces, k1, drawn) of one frame (ripple_rings), each put through keep (cut to the frame / off what earlier
    frames took). frame None = not cut to one: Grow to, never Auto."""
    wind = bool(sh.get("union"))
    mids = centres(sh, sources, view)
    turns = [source_turn(cfg, n) for n, _ in sources]
    fill = cfg["auto"] and frame is not None
    grow = auto_grow(frame, sources, mids, [bool(t) for t in turns]) if fill else cfg["grow"]
    sizes, k1 = ring_sizes(cfg, grow)
    way = -1 if mirrored(sh) else 1  # (a flipped shape turns the other way: its fill is a mirror image)
    covered = np.zeros((0, 3), np.int64)
    pieces = []
    for k, size in enumerate(sizes):
        sets = []
        for (_, src), c, t in zip(sources, mids, turns):
            a = way * math.radians(t) * (k - k1)
            co, si = math.cos(a) * size, math.sin(a) * size
            moved = [np.column_stack([c[0] + co * (p[:, 0] - c[0]) - si * (p[:, 1] - c[1]),
                                      c[1] + si * (p[:, 0] - c[0]) + co * (p[:, 1] - c[1])]) for p in src]
            sets.append(to_bp_loops(moved, view))
        ring = union_spans(sets, ppq, wind)
        piece = cut_out(ring, covered) if len(covered) and len(ring) else ring
        covered = merged_rows(np.concatenate([covered, ring]))
        pieces.append(keep(piece))
    drawn = keep(union_spans([to_bp_loops(src, view) for _, src in sources], ppq, wind))
    return pieces, k1, drawn


def edge_outline(sh):
    """The loops the outline gate shrinks (custom.edge_loops), in beats / pitch: only the frames the rings are cut
    to (the sources are filled with rings, not holes), and with Inside the sources "empty" the sources too."""
    view = view_of(sh)
    if view[1] <= 0:
        return []
    out = []
    for frame, sources in parts(view_loops(sh, view)):
        loops = [frame] if frame is not None and sh["ripples"]["trim"] else []
        loops += [p for _, src in sources for p in src] if sh["ripples"]["inside"] == "empty" else []
        out += [p.tolist() for p in to_bp_loops(loops, view)]
    return out


def ripple_spans(sh, ppq):
    """(start, end, key, colour) ticks of every stretch filled (like nested.nested_spans)."""
    cfg = sh["ripples"]
    rounds = bool(sh.get("_rounds"))
    every = 2 if cfg["skip"] else 1
    out = []
    for pieces, k1, drawn, _ in ripple_rings(sh, ppq):
        lift = (k1 // every // cfg["colours"] + 1) * cfg["colours"]  # (keeps every turn >= 0, ring k1 colour 0)
        for k, piece in enumerate(pieces):
            d = k1 - k  # (the ring just inside the drawn outline first)
            if d % every or not len(piece):
                continue
            if cfg["inside"] != "rings" and len(drawn):
                piece = cut_out(piece, drawn)
            colour = in_turns((-d) // every + lift, cfg["colours"], rounds)
            out.append(np.column_stack([piece, np.full(len(piece), colour, np.int64)]))
        if cfg["inside"] == "plain" and len(drawn):
            out.append(np.column_stack([drawn, np.full(len(drawn), in_turns(lift, cfg["colours"], rounds),
                                                       np.int64)]))
    out = [o for o in out if len(o)]
    if not out:
        return np.zeros((0, 4), np.int64)
    got = np.concatenate(out)
    return got[np.lexsort((got[:, 0], got[:, 2]))].astype(np.int64)
