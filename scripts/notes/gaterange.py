"""Spam gate Range: the gate goes from the spam gate (sh["gate"]) to a second one across the shape, instead of one
gate for every note. sh["range"] = {"to": the second gate in beats, "graph": [[u, y], ...], "dir": "time" / "keys" /
"keys_down", "fit": bool, "rows": bool, "join": bool, "halves": bool (Steps), "follow": "curve" (funnels only)}.

The graph: u = 0..1 across the shape (left to right in time, bottom to top in keys, top to bottom in keys_down), y =
0..1 from the first gate
to the second, straight lines between the points. Every whole-tick gate between the two gets an equal stretch of y
(the gate distribution rule: with the straight default graph each gate gets an equal share of the time, so short
gates make most of the notes).

Time: one row of back-to-back notes for the whole shape, from its left edge (every key cut at the same places),
each note as long as the gate where it starts. Rows (time only, "Each key row by itself"): the whole graph over each
stretch of a row on its own (join: over each key's first to last stretch, gaps included; custom.chop_rows); one too
short for any note = one note of the first gate (user). Keys: each key row has its own gate, even all along the row.
Fit: each stretch's first note starts and its last note ends right at the stretch's edges (stretched over the blank
left there, or trimmed where it sticks out; custom.chop_grid).
Steps ("halves"): the gate only halves or doubles from the first one (first, half of it, a quarter...; never past
the second): each gate the graph gives is snapped to the nearest of those (halved).
Funnels (funnel.py) have a range too: always from the line start to the wall (no keys / rows / fit); "follow"
"curve" = along how far the funnel has opened instead of evenly."""

import functools
import math

import numpy as np

DIRS = ("time", "keys", "keys_down")
STRAIGHT = [[0.0, 0.0], [1.0, 1.0]]


def clean_graph(g):
    """A graph from a file -> points with u from 0 to 1 (ends at 0 and 1, in order), y 0..1; None if broken."""
    try:
        pts = [[min(1.0, max(0.0, float(u))), min(1.0, max(0.0, float(y)))] for u, y in g]
    except (TypeError, ValueError):
        return None
    if len(pts) < 2 or not all(map(math.isfinite, (c for p in pts for c in p))):
        return None
    pts.sort(key=lambda p: p[0])
    pts[0][0], pts[-1][0] = 0.0, 1.0
    return pts


def clean_range(r):
    """A range from a file -> valid range, or None (none)."""
    if not isinstance(r, dict):
        return None
    try:
        to = float(r["to"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(to) or to <= 0:
        return None
    return {"to": min(to, 10 ** 4), "graph": clean_graph(r.get("graph")) or [list(p) for p in STRAIGHT],
            "dir": r["dir"] if r.get("dir") in DIRS else "time", "fit": bool(r.get("fit")), "rows": bool(r.get("rows")),
            "join": bool(r.get("join")), "halves": bool(r.get("halves")),
            **({"exact": True} if r.get("exact") is True and not r.get("fit") else {}),
            **({"follow": "curve"} if r.get("follow") == "curve" else {})}


ENDS = ("exact", "leave", "stretch")  # the window's Ends dropdown, in its order (Exact on top: user)


def ends_of(r):
    """Ends of a range: "leave" (whole notes, the last one as it falls), "stretch" ("fit": the first and last note
    reach the edges) or "exact" (the gates share the length out exactly, exact_squares)."""
    return "stretch" if r.get("fit") else "exact" if r.get("exact") else "leave"


def with_ends(r, ends):
    out = {k: v for k, v in r.items() if k != "exact"}
    out["fit"] = ends == "stretch"
    if ends == "exact":
        out["exact"] = True
    return out


def stretched(r):
    """Are a stretch's first and last note stretched / trimmed to its edges? Stretch, or Exact by keys (one gate a
    key row: no sharing out there)."""
    return bool(r.get("fit")) or (bool(r.get("exact")) and r.get("dir") != "time")


def exact_ok(r, a, b):
    """Can Exact share the length out with real gates? Not with one gate, Steps (only halvings) or by keys (one gate
    a key row): there the last note is stretched / trimmed to the edge instead."""
    return a != b and not r.get("halves") and keys_up(r)["dir"] == "time"


def reversed_graph(graph):
    return [[1 - u, y] for u, y in reversed(graph)]


def keys_up(r):
    """keys_down as keys with the graph the other way (the same gates), so only time / keys need handling."""
    return dict(r, dir="keys", graph=reversed_graph(r["graph"])) if r["dir"] == "keys_down" else r


def flipped_range(r, sideways):
    """The range of a shape flipped sideways / upside down: the graph runs the other way when it's across that."""
    if r and (r["dir"] == "time") == sideways:
        return dict(r, graph=reversed_graph(r["graph"]))
    return r


def turned_range(r, clockwise):
    """Turned 90 degrees with the shape: time becomes keys and keys time (clockwise: higher keys = later)."""
    if not r:
        return r
    r = keys_up(r)
    to_keys = r["dir"] == "time"
    back = to_keys == clockwise  # (clockwise: time runs top to bottom, keys left to right)
    return dict(r, dir="keys" if to_keys else "time", graph=reversed_graph(r["graph"]) if back else r["graph"])


def halved(g, a, b):
    """Steps: gates (ticks) snapped to the nearest halving / doubling of a, never past b (only real ones: a To that
    isn't one is never reached, hunt 2026-10-10)."""
    if a == b:
        return g
    k = np.round(np.log2(np.asarray(g, float) / a))
    lo, hi = sorted((0, steps_to(a, b)))
    return np.rint(a * 2.0 ** np.clip(k, lo, hi)).astype(np.int64)


def steps_to(a, b):
    """How many halvings (< 0) / doublings (> 0) of a fit between a and b (Steps; 0 = none: one gate)."""
    if a <= 0 or b <= 0 or a == b:
        return 0
    x = math.log2(b / a)
    return math.floor(x + 1e-9) if x > 0 else math.ceil(x - 1e-9)


def y_gate(a, b, y, halves=False):
    """The whole-tick gate where the graph is at y (0..1): a .. b ticks, each an equal stretch of y."""
    n = abs(b - a) + 1
    g = a + (1 if b >= a else -1) * min(n - 1, int(y * n))
    return int(halved(g, a, b)) if halves else g


def gate_at(graph, a, b, u, halves=False):
    """The whole-tick gate at u (0..1 across the shape)."""
    return y_gate(a, b, float(np.interp(u, [p[0] for p in graph], [p[1] for p in graph])), halves)


@functools.lru_cache(maxsize=16)
def _steps(graph, a, b, halves=False):
    """gate_steps as arrays (u0s, u1s, gates), worked out all at once: a To of millions of ticks has millions of
    steps (one loop turn each took seconds). graph: a tuple of (u, y) pairs (kept: the window redraws often)."""
    n = abs(b - a) + 1
    cuts = [np.array([0.0, 1.0])]
    for (u0, y0), (u1, y1) in zip(graph, graph[1:]):
        cuts.append(np.array([u0]))
        if y1 != y0 and u1 > u0:
            lo, hi = sorted((y0, y1))
            k = np.arange(math.ceil(lo * n), math.floor(hi * n) + 1)
            u = u0 + (k / n - y0) * (u1 - u0) / (y1 - y0)
            cuts.append(u[(u0 < u) & (u < u1)])
    cuts = np.unique(np.concatenate(cuts))
    p, q = cuts[:-1], cuts[1:]
    wide = q - p >= 1e-12
    p, q = p[wide], q[wide]
    y = np.interp((p + q) / 2, [pt[0] for pt in graph], [pt[1] for pt in graph])
    g = a + (1 if b >= a else -1) * np.minimum(n - 1, (y * n).astype(np.int64))
    if halves:
        g = halved(g, a, b)
    first = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])  # (next to each other with the same gate = one step)
    last = np.r_[first[1:], len(g)] - 1
    return p[first], q[last], g[first]


def steps_of(graph, a, b, halves=False):
    """gate_steps as arrays (u0s, u1s, gates)."""
    return _steps(tuple(map(tuple, graph)), a, b, bool(halves))


def few_steps(graph, a, b, halves=False, most=2000):
    """Are there at most `most` steps? Worked out only when the graph can't have far more (millions took a second
    each time a point moved, hunt): each straight piece crosses at most that many whole gates."""
    n = abs(b - a) + 1
    cuts = sum(abs(y1 - y0) * n + 2 for (_, y0), (_, y1) in zip(graph, graph[1:]))
    if cuts <= most:
        return True
    return cuts <= 100 * most and len(steps_of(graph, a, b, halves)[0]) <= most


def gate_steps(graph, a, b, halves=False):
    """[(u0, u1, gate)]: where along the shape each gate holds (in order, next to each other)."""
    u0, u1, g = steps_of(graph, a, b, halves)
    return list(zip(u0.tolist(), u1.tolist(), g.tolist()))


def range_squares(t0, t1, a, b, graph, halves=False):
    """The notes' (start, end) ticks from t0 past t1, back to back, each as long as the gate where it starts."""
    span = max(t1 - t0, 1)
    t, parts = t0, []
    _, u1s, gs = steps_of(graph, a, b, halves)
    ends = t0 + u1s * span
    i = 0
    while True:  # (each turn: the step the next note starts in; steps it jumps over cost nothing)
        i += int(np.searchsorted(ends[i:], t, "right"))
        if i >= len(ends):
            break
        g = int(gs[i])
        n = math.ceil((float(ends[i]) - t) / g)
        parts.append(t + g * np.arange(n + 1, dtype=np.int64))
        t += n * g
    g = int(gs[-1])
    parts.append(t + g * np.arange(1, 3, dtype=np.int64))  # (a little past the end)
    edges = np.unique(np.concatenate(parts))
    return np.column_stack([edges[:-1], edges[1:]])


def exact_squares(t0, t1, a, b, graph, halves=False):
    """range_squares that end exactly at t1 (Exact, tools/gate-distribute's rule): the notes the graph gives, the last
    one kept or dropped (whichever is nearer), then the ticks left over / too many shared out as +-1 tick on notes
    spread evenly along (each stays a gate between the two, so most notes stay as they were). When that can't fit
    (one gate, Steps, a stretch too short) the last note is stretched / trimmed to t1."""
    span = max(t1 - t0, 1)
    sq = range_squares(0, span, a, b, graph, halves)
    sq = sq[sq[:, 0] < span]
    g = (sq[:, 1] - sq[:, 0]).astype(np.int64)
    over = int(g.sum()) - span  # (>= 0: the last note reaches the end or past it)
    if len(g) > 1 and over > 0 and span - (int(g.sum()) - int(g[-1])) < over:
        g = g[:-1]
    lo, hi = sorted((a, b))
    if a != b and not halves:  # (a note count whose gates can add up to span: the nearest one, notes at the end
        least, most = -(-span // hi), span // lo  # dropped / copies of the last added)
        if least <= most and not least <= len(g) <= most:
            n = min(max(len(g), least), most)
            g = g[:n] if n < len(g) else np.r_[g, np.full(n - len(g), g[-1], np.int64)]
    diff = span - int(g.sum())  # (> 0: ticks to add, < 0: to take off)
    if diff and a != b and not halves:
        room = (hi - g) if diff > 0 else (g - lo)
        if int(room.sum()) >= abs(diff):
            g = _in_order(g, g + np.sign(diff) * _share(room, abs(diff)))
            diff = 0
    if diff:
        g[-1] = max(1, int(g[-1]) + diff)
        if int(g.sum()) != span:  # (a last note too short to take it all: one note over the whole span)
            g = np.array([span], np.int64)
    edges = t0 + np.r_[0, np.cumsum(g)]
    return np.column_stack([edges[:-1], edges[1:]])


def _in_order(was, now):
    """The shared-out gates (now) sorted inside each run of equal gates they came from (was), rising when the next run
    is longer, falling when it's shorter (the last run: as the one before it), so the gates still rise or fall
    steadily (10, 10, 11, 11, not 10, 11, 10, 11; the user's gate tool keeps them in order, hunt 2026-11-01)."""
    first = np.flatnonzero(np.r_[True, was[1:] != was[:-1]])
    vals = was[first]
    up = np.r_[vals[1:] > vals[:-1], True] if len(vals) > 1 else np.array([True])
    if len(vals) > 1:
        up[-1] = vals[-1] > vals[-2]
    run = np.repeat(np.arange(len(first)), np.diff(np.r_[first, len(was)]))
    order = np.lexsort((np.where(up[run], now, -now), run))
    return now[order]


def _share(room, d):
    """d ticks shared out over notes that can each take room[i] more, as evenly as can be: every note up to a level,
    the rest one tick each on notes spread evenly along."""
    lo, hi = 0, int(room.max())
    while lo < hi:  # (the lowest level that takes all d)
        mid = (lo + hi) // 2
        if int(np.minimum(room, mid).sum()) >= d:
            hi = mid
        else:
            lo = mid + 1
    add = np.minimum(room, max(lo - 1, 0))
    rest = d - int(add.sum())
    if rest:
        can = np.flatnonzero(room > add)
        pick = can[((np.arange(rest) + 0.5) * len(can) / rest).astype(np.int64)]
        add[pick] += 1
    return add


class RangeKeys:
    """Range by keys: every key row its own gate, notes back to back from the shape's left edge (custom.chop_keys
    asks it for each key's squares)."""

    def __init__(self, t0, t1, k0, k1, a, b, graph, halves=False):
        self.t0, self.t1, self.k0, self.k1, self.a, self.b, self.graph = t0, t1, k0, k1, a, b, graph
        self.halves = halves

    def squares(self, key):
        u = (key - self.k0) / (self.k1 - self.k0) if self.k1 > self.k0 else 0.0
        g = gate_at(self.graph, self.a, self.b, min(1.0, max(0.0, u)), self.halves)
        n = math.ceil((self.t1 - self.t0) / g) + 2
        edges = self.t0 + g * np.arange(n + 1, dtype=np.int64)
        return np.column_stack([edges[:-1], edges[1:]])


class RangeRows:
    """Range along each key row: the whole graph over every stretch's own length (join: each key's stretches
    together; custom.chop_rows asks it for the squares over (t0, t1))."""

    def __init__(self, a, b, graph, join, halves=False, exact=False):
        self.a, self.b, self.graph, self.join, self.halves = a, b, tuple(map(tuple, graph)), join, bool(halves)
        self.exact = bool(exact)

    def squares(self, t0, t1):
        return t0 + _row_squares(max(t1 - t0, 1), self.a, self.b, self.graph, self.halves, self.exact)


@functools.lru_cache(maxsize=4096)
def _row_squares(span, a, b, graph, halves, exact=False):
    """range_squares / exact_squares from 0 (kept: many stretches share a length)."""
    return (exact_squares if exact else range_squares)(0, span, a, b, graph, halves)


def is_rows(r):
    return bool(r) and r["dir"] == "time" and r.get("rows")


def frame_span(sh):
    """A custom shape's frame: (first beat, last beat, lowest key, highest key)."""
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    bs, ps = (b0, b1, b2, b1 + b2 - b0), (p0, p1, p2, p1 + p2 - p0)
    return min(bs), max(bs), min(ps), max(ps)


def _span(sh, r, ppq):
    """Where the range runs over (the same ends range_grid uses): ticks for time, keys for keys."""
    lo, hi, k0, k1 = frame_span(sh)
    return (round(k0), round(k1)) if r["dir"] != "time" else (math.floor(lo * ppq), math.ceil(hi * ppq))


def part_range(whole, part, ppq):
    """A piece cut off a ranged shape (Slice) -> its (gate in beats, range): the part of the whole's range over the
    piece's own stretch, so each spot keeps the gate it had (the graph cut there; the gates between its lowest and
    highest, each the same stretch of y as before; Steps: the whole's two gates kept, only the graph cut, so the
    halvings still count from the same gate). Along each key row: the whole's range as it is (each row of the piece
    runs it all)."""
    if is_rows(whole["range"]):
        return whole["gate"], dict(whole["range"])
    down = whole["range"]["dir"] == "keys_down"
    r = keys_up(whole["range"])
    a = max(1, math.floor(whole["gate"] * ppq + 0.5))
    b = max(1, math.floor(r["to"] * ppq + 0.5))
    n = abs(b - a) + 1
    w0, w1 = _span(whole, r, ppq)
    p0, p1 = _span(part, r, ppq)
    span = max(w1 - w0, 1)
    u0, u1 = (min(1.0, max(0.0, (p - w0) / span)) for p in (p0, p1))
    us, ys = [p[0] for p in r["graph"]], [p[1] for p in r["graph"]]
    pts = [[u0, float(np.interp(u0, us, ys))]] + [[u, y] for u, y in r["graph"] if u0 < u < u1]
    pts.append([u1, float(np.interp(u1, us, ys))])
    band = [min(n - 1, int(y * n)) for _, y in pts]  # (the gates at the graph's points, counted from a)
    j0, j1 = min(band), max(band)
    m = j1 - j0 + 1
    width = u1 - u0
    graph = [[(u - u0) / width if width > 1e-12 else (0.0 if k == 0 else 1.0),
              min(1.0, max(0.0, (y * n - j0) / m))] for k, (u, y) in enumerate(pts)]
    graph[0][0], graph[-1][0] = 0.0, 1.0
    if r.get("halves"):
        graph = [[g[0], y] for g, (_, y) in zip(graph, pts)]
    sign = 1 if b >= a else -1
    if down:
        graph = reversed_graph(graph)
    if r.get("halves"):
        return whole["gate"], dict(r, graph=graph, dir=whole["range"]["dir"])
    return (a + sign * j0) / ppq, dict(r, to=(a + sign * j1) / ppq, graph=graph, dir=whole["range"]["dir"])


def range_grid(sh, ppq):
    """What custom.chop cuts the stretches with for a shape with a Range: squares (time), a RangeRows (time, each key
    row by itself) or a RangeKeys (keys)."""
    r = keys_up(sh["range"])
    a = max(1, math.floor(sh["gate"] * ppq + 0.5))
    b = max(1, math.floor(r["to"] * ppq + 0.5))
    exact = bool(r.get("exact"))
    if is_rows(r):
        return RangeRows(a, b, r["graph"], bool(r.get("join")), r.get("halves"), exact)
    lo, hi, k0, k1 = frame_span(sh)
    t0, t1 = math.floor(lo * ppq), math.ceil(hi * ppq)
    if exact and r["dir"] == "time":  # (rounded as the rows are, custom.inside_spans: else a frame between ticks
        t0, t1 = math.floor(lo * ppq + 0.5), math.floor(hi * ppq + 0.5)  # ran a tick past each edge, hunt)
    if r["dir"] == "keys":
        return RangeKeys(t0, t1, round(k0), round(k1), a, b, r["graph"], r.get("halves"))
    return (exact_squares if exact else range_squares)(t0, t1, a, b, r["graph"], r.get("halves"))
