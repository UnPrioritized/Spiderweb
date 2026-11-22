"""
Gate extend (right-click "Gate extend..."): every note of a shape ends a little later, so it runs over into the
notes after it like a run played by hand (user 2026-11-02, stage E2 of the glissando plan: "looks better"). The other
side of Gate subtract (subtract.py), built the same way.

sh["extend"] = {"by": the amount, "unit": "pct" (by = % of each note's own length) or none (by = beats, shown in whole
ticks like Gate subtract: a PPQ change keeps the look), "same": True (% only: % of the shape's AVERAGE note length,
every note the same amount), + the graph like Gate subtract's ("vary": True while it's
used; "to", "graph", "dir", "rows", "join", "count" kept while it's off)}.
MIXED (typed "50% + 10", user 2026-11-22): unit "pct" + "plus" = beats added to every note at From, "plus_to" = at
To (each missing = 0; only stored when not 0, so plain % / ticks shapes are saved exactly as before). Each part steps
along the graph by itself; a note gets round(its length x %) + the ticks.
A note never runs into the next note on its own key (every colour together, like Sustain; my pick: two notes on one
key at once is a clash in any MIDI player): it stops where that one starts, or stays as it was if it already reached
it. Notes starting on the same tick on one key don't stop each other.
Done near the end: after Sustain, before Gate subtract (engine.fx_notes), so a shape with both gets its gaps cut
from the longer notes. Not on a Hz bass or an image, like Sustain / Gate subtract. A flip / turn moves its graph only
(the notes still end later: a run played backwards still runs over).
"""

import math

import numpy as np

from files.speed import key_sort

from notes.gaterange import DIRS, STRAIGHT, clean_graph, flipped_range, turned_range
from notes.subtract import EPS, amounts, part_side, ticks_of

MOST = 10 ** 4  # beats a typed tick amount may be
MOST_PCT = 1000  # % a typed amount may be
GRAPH_KEYS = ("to", "graph", "dir", "rows", "join", "count")


def is_pct(ext):
    return isinstance(ext, dict) and ext.get("unit") == "pct"


def unit_ppq(ext, ppq):
    """What one whole step of the amount is: a tick (ppq per beat), or 1 % (amounts kept as plain whole numbers)."""
    return 1 if is_pct(ext) else ppq


def _amount(v, most):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return min(v, most) if math.isfinite(v) and v >= 0 else None


def clean_extend(s):
    """sh["extend"] from a file -> valid, or None (not extended)."""
    if not isinstance(s, dict):
        return None
    most = MOST_PCT if s.get("unit") == "pct" else MOST
    by = _amount(s.get("by"), most)
    if by is None:
        return None
    out: dict = {"by": by}
    if s.get("unit") == "pct":
        out["unit"] = "pct"
        if s.get("same") is True:
            out["same"] = True
        plus = _amount(s.get("plus"), MOST)
        if plus:
            out["plus"] = plus
    to =_amount(s.get("to"), most)
    if to is not None:  # (the graph: kept while it's off too)
        out.update(to=to, graph=clean_graph(s.get("graph") or ()) or [list(p) for p in STRAIGHT],
                   dir=s["dir"] if s.get("dir") in DIRS else "time")
        for key in ("vary", "rows", "join", "count"):
            if s.get(key) is True:
                out[key] = True
        plus_to = _amount(s.get("plus_to"), MOST) if s.get("unit") == "pct" else None
        if plus_to:
            out["plus_to"] = plus_to
    return out


def is_mixed(ext):
    """True if ext adds ticks on top of its % somewhere (typed "50% + 10")."""
    return is_pct(ext) and bool(ext.get("plus") or ext.get("plus_to"))


def ticks_part(ext):
    """A mixed Gate extend's ticks part as a plain ticks one (the same graph), for subtract.amounts."""
    out = {k: v for k, v in ext.items() if k not in ("unit", "same", "plus", "plus_to")}
    out["by"] = ext.get("plus") or 0.0
    if "to" in ext:
        out["to"] = ext.get("plus_to") or 0.0
    return out


def full_form(ext):
    """ext as a % one with both "plus" keys (0 where missing; a ticks one = 0 % + its ticks): two ends of an Add
    between then blend number by number."""
    if is_pct(ext):
        out = dict(ext, plus=ext.get("plus") or 0.0)
        if "to" in ext:
            out["plus_to"] = ext.get("plus_to") or 0.0
        return out
    out = dict(ext, unit="pct", by=0.0, plus=ext["by"])
    if "to" in ext:
        out.update(to=0.0, plus_to=ext["to"])
    return out


def _slim(ext):
    """full_form back to the saved form: "plus" keys only when not 0."""
    return {k: v for k, v in ext.items() if k not in ("plus", "plus_to") or v}


def is_extended(sh):
    """True if sh has Gate extend (never a Hz bass or an image)."""
    return isinstance(sh.get("extend"), dict) and not sh.get("hz") and "picture" not in sh


def flipped_extend(ext, sideways):
    return flipped_range(ext, sideways) if "graph" in ext else ext


def turned_extend(ext, clockwise):
    return turned_range(ext, clockwise) if "graph" in ext else ext


def mixed_extend(x, y, f, mix):
    """An Add between step's Gate extend f of the way from x to y: step by step when both use the same unit, else
    the nearer end's. An end with % + ticks (mixed): both ends blend as % + ticks (a ticks end = 0 % + its ticks)."""
    if is_mixed(x) or is_mixed(y):
        x, y = full_form(x), full_form(y)
        if ("to" in x) != ("to" in y) or x.get("same") != y.get("same"):
            return _slim(x if f < 0.5 else y)
        return _slim(mix(x, y, f))
    if is_pct(x) != is_pct(y):
        return dict(x if f < 0.5 else y)
    return mix(x, y, f)


def part_extend(ext, whole, part, ppq):
    """A piece cut off a shape whose graph runs across it (Slice, Split): its own graph, so each note keeps its amount
    (subtract.part_side). whole / part = their notes before Gate extend. Mixed: the graph is only cut to the piece's
    stretch (heights and both amounts kept), so the % and the ticks part both keep each note's step."""
    if is_mixed(ext):
        return _cut_graph(ext, whole, part, ppq)
    return part_side(ext, whole, part, unit_ppq(ext, ppq))


def _cut_graph(ext, whole, part, ppq):
    """The graph of ext over the piece's stretch only, heights unchanged (see part_extend)."""
    if not ext.get("vary") or "to" not in ext or ext["dir"] == "time" and ext.get("rows") or not len(whole) \
            or not len(part):
        return ext
    from notes.gaterange import keys_up, reversed_graph
    down = ext["dir"] == "keys_down"
    r = keys_up(ext)
    col = 0 if r["dir"] == "time" else 2
    w0, w1 = int(whole[:, col].min()), int(whole[:, col].max())
    span = max(w1 - w0, 1)
    u0, u1 = (min(1.0, max(0.0, (int(f(part[:, col])) - w0) / span)) for f in (np.min, np.max))
    us, ys = [p[0] for p in r["graph"]], [p[1] for p in r["graph"]]
    if int(part[:, col].min()) == int(part[:, col].max()):
        # (every note of the piece in one place along the graph: it has nothing to run along and would get From,
        # so it keeps the amounts it had there, without a graph)
        y = float(np.interp(u0, us, ys))

        def step(a, b):
            n = abs(b - a) + 1
            return a + (1 if b >= a else -1) * min(n - 1, math.floor(y * n + EPS))
        pa = step(ticks_of(ext["by"], 1), ticks_of(ext["to"], 1))
        pt = step(ticks_of(ext.get("plus") or 0.0, ppq), ticks_of(ext.get("plus_to") or 0.0, ppq)) / ppq
        return _slim(dict(ext, by=float(pa), to=float(pa), plus=pt, plus_to=pt))
    pts = [[u0, float(np.interp(u0, us, ys))]] + [[u, y] for u, y in r["graph"] if u0 < u < u1]
    pts.append([u1, float(np.interp(u1, us, ys))])
    width = u1 - u0
    graph = [[(u - u0) / width if width > 1e-12 else float(k > 0), y] for k, (u, y) in enumerate(pts)]
    graph[0][0], graph[-1][0] = 0.0, 1.0
    if down:
        graph = reversed_graph(graph)
    return dict(ext, graph=graph)


def next_starts(notes):
    """Per note: the start of the next note on its key that starts LATER (every colour together), or a huge number."""
    s, p = notes[:, 0].astype(np.int64), notes[:, 2].astype(np.int64)
    big = np.int64(1) << 40
    keys = p * big + s
    order = key_sort(p, s) if int(np.abs(s).max(initial=0)) < big // 2 else np.argsort(keys, kind="stable")  # (the
    # compiled sort: the same order as the keys sorted while starts stay within half of big)
    k = keys[order]
    at = np.searchsorted(k, keys, side="right")
    out = np.full(len(notes), np.iinfo(np.int64).max // 4, np.int64)
    ok = at < len(k)
    nxt = order[np.minimum(at, len(k) - 1)]
    ok &= p[nxt] == p
    out[ok] = s[nxt][ok]
    return out


def extended(notes, ext, ppq):
    """notes (start, end, pitch, velocity, ...) with their ends moved later (engine.fx_notes); never into the next
    note on their key."""
    if not len(notes):
        return notes
    n = np.broadcast_to(np.asarray(amounts(notes, ext, unit_ppq(ext, ppq)), np.int64), (len(notes),))
    out = notes.copy()
    s, e = out[:, 0].astype(np.int64), out[:, 1].astype(np.int64)
    # (% of its own length to the NEAREST tick, halves up: rounded down, notes 3 and 4 ticks long got 1 and 2 at
    # 50 %, twice as uneven as the line's own steps, user 2026-11-06)
    if is_pct(ext) and ext.get("same"):
        # ("Same amount for every note": % of the shape's AVERAGE length, so a slanted line's ends follow its starts
        # exactly, like the ticks unit; user 2026-11-10, idea B)
        avg = float(np.mean(e - s))
        add = np.floor(avg * n / 100 + 0.5).astype(np.int64)
    elif is_pct(ext):
        add = ((e - s) * n * 2 + 100) // 200
    else:
        add = n
    if is_mixed(ext):
        add = add + np.asarray(amounts(notes, ticks_part(ext), ppq), np.int64)
    want = e + add
    out[:, 1] = np.minimum(want, np.maximum(e, next_starts(notes)))
    return out
