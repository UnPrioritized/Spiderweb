"""Spam gate Range: the gate goes from the spam gate (sh["gate"]) to a second one across the shape, instead of one
gate for every note. sh["range"] = {"to": the second gate in beats, "graph": [[u, y], ...], "dir": "time" / "keys" /
"keys_down", "fit": bool}.

The graph: u = 0..1 across the shape (left to right in time, bottom to top in keys, top to bottom in keys_down), y =
0..1 from the first gate
to the second, straight lines between the points. Every whole-tick gate between the two gets an equal stretch of y
(the gate distribution rule: with the straight default graph each gate gets an equal share of the time, so short
gates make most of the notes).

Time: one row of back-to-back notes for the whole shape, from its left edge (every key cut at the same places),
each note as long as the gate where it starts. Keys: each key row has its own gate, even all along the row.
Fit: each stretch's first note starts and its last note ends right at the stretch's edges (stretched over the blank
left there, or trimmed where it sticks out; custom.chop_grid)."""

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
            "dir": r["dir"] if r.get("dir") in DIRS else "time", "fit": bool(r.get("fit"))}


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


def gate_at(graph, a, b, u):
    """The whole-tick gate at u (0..1 across the shape): a .. b ticks, each an equal stretch of the graph's y."""
    n = abs(b - a) + 1
    y = float(np.interp(u, [p[0] for p in graph], [p[1] for p in graph]))
    return a + (1 if b >= a else -1) * min(n - 1, int(y * n))


def gate_steps(graph, a, b):
    """[(u0, u1, gate)]: where along the shape each gate holds (in order, next to each other)."""
    n = abs(b - a) + 1
    cuts = {0.0, 1.0}
    for (u0, y0), (u1, y1) in zip(graph, graph[1:]):
        cuts.add(u0)
        if y1 != y0 and u1 > u0:
            lo, hi = sorted((y0, y1))
            for k in range(math.ceil(lo * n), math.floor(hi * n) + 1):
                u = u0 + (k / n - y0) * (u1 - u0) / (y1 - y0)
                if u0 < u < u1:
                    cuts.add(u)
    cuts = sorted(cuts)
    out = []
    for p, q in zip(cuts, cuts[1:]):
        if q - p < 1e-12:
            continue
        g = gate_at(graph, a, b, (p + q) / 2)
        if out and out[-1][2] == g:
            out[-1] = (out[-1][0], q, g)
        else:
            out.append((p, q, g))
    return out


def range_squares(t0, t1, a, b, graph):
    """The notes' (start, end) ticks from t0 past t1, back to back, each as long as the gate where it starts."""
    span = max(t1 - t0, 1)
    t, parts = t0, []
    steps = gate_steps(graph, a, b)
    for u0, u1, g in steps:
        tb = t0 + u1 * span
        if t >= tb:
            continue
        n = math.ceil((tb - t) / g)
        parts.append(t + g * np.arange(n + 1, dtype=np.int64))
        t += n * g
    g = steps[-1][2]
    parts.append(t + g * np.arange(1, 3, dtype=np.int64))  # (a little past the end)
    edges = np.unique(np.concatenate(parts))
    return np.column_stack([edges[:-1], edges[1:]])


class RangeKeys:
    """Range by keys: every key row its own gate, notes back to back from the shape's left edge (custom.chop_keys
    asks it for each key's squares)."""

    def __init__(self, t0, t1, k0, k1, a, b, graph):
        self.t0, self.t1, self.k0, self.k1, self.a, self.b, self.graph = t0, t1, k0, k1, a, b, graph

    def squares(self, key):
        u = (key - self.k0) / (self.k1 - self.k0) if self.k1 > self.k0 else 0.0
        g = gate_at(self.graph, self.a, self.b, min(1.0, max(0.0, u)))
        n = math.ceil((self.t1 - self.t0) / g) + 2
        edges = self.t0 + g * np.arange(n + 1, dtype=np.int64)
        return np.column_stack([edges[:-1], edges[1:]])


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
    highest, each the same stretch of y as before)."""
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
    sign = 1 if b >= a else -1
    if down:
        graph = reversed_graph(graph)
    return (a + sign * j0) / ppq, dict(r, to=(a + sign * j1) / ppq, graph=graph, dir=whole["range"]["dir"])


def range_grid(sh, ppq):
    """What custom.chop cuts the stretches with for a shape with a Range: squares (time) or a RangeKeys (keys)."""
    r = keys_up(sh["range"])
    a = max(1, math.floor(sh["gate"] * ppq + 0.5))
    b = max(1, math.floor(r["to"] * ppq + 0.5))
    lo, hi, k0, k1 = frame_span(sh)
    t0, t1 = math.floor(lo * ppq), math.ceil(hi * ppq)
    if r["dir"] == "keys":
        return RangeKeys(t0, t1, round(k0), round(k1), a, b, r["graph"])
    return range_squares(t0, t1, a, b, r["graph"])
