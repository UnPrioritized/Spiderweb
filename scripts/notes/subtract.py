"""
Gate subtract (right-click "Gate subtract..."): every note of a shape ends a little earlier and / or starts a little
later, so a blank space shows between notes that touched. A note never gets shorter than 1 tick.

sh["subtract"] = the NOTE END side: {"by": beats (kept in beats like every length: a PPQ change keeps the look; whole
ticks, 0 or more: 0 = nothing off, user, so a graph can start from no gap),
"touch": True (only notes the next note on their key starts on or before the end of; default: every note)}
+ the graph ("vary": True while it's used; the rest kept while it's off so ticking again brings it back): "to": beats,
"graph": [[u, y]], "dir": "time" / "keys" / "keys_down", "rows", "join", "count" (as Gate Range's, gaterange.py)
+ "off": True (the end side not used, its settings kept) + "start": the NOTE START side, the same keys (no "start"),
its "touch" = only notes the note before on their key ends on or after the start of (user 2026-10-20: two tabs, either
or both; none = end only, as older files).
The amount goes from "by" to "to" along the graph, every whole tick between an equal stretch of y (y_gate). u, per
note: time = its start from the shape's first note start (0) to its last note start (1); keys = its key from the
lowest (0) to the highest (1). "rows" (time only, "Each key row by itself"): each run of a key row on its own (a run =
notes whose gaps are no longer than the usual gap, as Gate aware flip finds them; "join" = the whole row one run),
its first note 0, its last 1: by start time, or by its place among the run's notes ("count"). A run of one note = 0.
Both sides worked out on the notes before either moved; together more than the note has = both cut by the same share
(user). Done LAST, after everything else that makes the shape's notes, Sustain too (engine.fx_notes), so nothing that
makes them sees it. A sideways flip (and Gate aware flip) swaps the sides, so a flipped shape mirrors note for note.
Not on a Hz bass (its own note lengths) or an image (its notes are its picture), like Sustain.
"""

import math

import numpy as np

from notes.gaterange import DIRS, STRAIGHT, clean_graph, keys_up

MOST = 10 ** 4  # beats a typed amount may be
EPS = 1e-7  # rounding margin when the graph's height picks a whole tick


def _beats(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return min(v, MOST) if math.isfinite(v) and v >= 0 else None  # (0: nothing off there, e.g. a graph's start)


def clean_side(s):
    """One side's settings from a file -> valid, or None."""
    if not isinstance(s, dict):
        return None
    by = _beats(s.get("by"))
    if by is None:
        return None
    out = {"by": by}
    for key in ("touch", "off"):
        if s.get(key) is True:
            out[key] = True
    cuts = s.get("cuts")
    if isinstance(cuts, list):  # (touching across a cut: see cut_marks)
        try:
            cuts = [[float(u), float(v)] for u, v in cuts]
        except (TypeError, ValueError):
            cuts = []
        cuts = [[u, v] for u, v in cuts if 0 <= u <= 1 and 0 <= v <= 1]
        if cuts:
            out["cuts"] = cuts
    to = _beats(s.get("to"))
    if to is not None:  # (the graph: kept while it's off too)
        out.update(to=to, graph=clean_graph(s.get("graph") or ()) or [list(p) for p in STRAIGHT],
                   dir=s["dir"] if s.get("dir") in DIRS else "time")
        for key in ("vary", "rows", "join", "count"):
            if s.get(key) is True:
                out[key] = True
    return out


def clean_subtract(s):
    """sh["subtract"] from a file -> valid, or None (not subtracted)."""
    out = clean_side(s)
    if out is not None:
        out = with_sides(out, clean_side(s.get("start")))
    return out


def is_subtracted(sh):
    """True if sh has Gate subtract settings (never a Hz bass or an image), used or not."""
    return isinstance(sh.get("subtract"), dict) and not sh.get("hz") and "picture" not in sh


def side_used(side):
    return bool(side) and not side.get("off")


def subtracts(sh):
    """True if sh's notes are really made shorter: it has Gate subtract with a side used (the Shapes list's
    "(Subtracted)", the menu tick; hunt 2026-10-20: both sides unticked showed it)."""
    return is_subtracted(sh) and (side_used(sh["subtract"]) or side_used(sh["subtract"].get("start")))


def empty_side(side):
    """An unused side with nothing kept on it (0 ticks, no graph, no ticks): the same as none."""
    return side is None or (side.get("off") and not side.get("by") and "to" not in side and not side.get("touch")
                            and "cuts" not in side)


EMPTY = {"by": 0.0, "off": True}  # (an end side that isn't there: sh["subtract"] is the end side)


def end_side(sub):
    return {k: v for k, v in sub.items() if k != "start"}


def with_sides(end, start):
    """sh["subtract"] from its two sides (start None or empty: none, so a shape flipped back and forth stays as it
    was, hunt 2026-10-20)."""
    out = dict(end) if end is not None else dict(EMPTY)
    if not empty_side(start):
        out["start"] = start
    return out


def _marks_flipped(side, sideways):
    cuts = side.get("cuts")
    if not cuts:
        return side
    return dict(side, cuts=[[1 - u, v] if sideways else [u, 1 - v] for u, v in cuts])


def flipped_subtract(sub, sideways):
    """sh["subtract"] of a shape flipped sideways / upside down: each side's graph as Gate Range's (gaterange
    flipped_range), the cut marks mirrored, and sideways the sides swap (a note's end is now its start)."""
    from notes.gaterange import flipped_range
    end, start = end_side(sub), sub.get("start")
    end = _marks_flipped(flipped_range(end, sideways) if "graph" in end else end, sideways)
    start = start and _marks_flipped(flipped_range(start, sideways) if "graph" in start else start, sideways)
    if sideways:
        end, start = start, None if empty_side(end) else end
    return with_sides(end, start)


def turned_subtract(sub, clockwise):
    """sh["subtract"] of a shape turned 90 degrees: each side's graph turned (gaterange.turned_range); the cut marks
    dropped (the notes are made again across the other way)."""
    from notes.gaterange import turned_range

    def turned(side):
        side = {k: v for k, v in side.items() if k != "cuts"}
        return turned_range(side, clockwise) if "graph" in side else side
    start = sub.get("start")
    return with_sides(turned(end_side(sub)), start and turned(start))


def same_subtract(a, b):
    """Two shapes' Gate subtract alike (Join, Turn into live shape, Gate sensitive merge keep it then)? Empty sides
    and cut marks don't count."""
    return plain_subtract(a) == plain_subtract(b)


def plain_subtract(sub):
    """sub without cut marks or an empty start side (what a shape made of pieces keeps)."""
    if not isinstance(sub, dict):
        return sub
    end = {k: v for k, v in end_side(sub).items() if k != "cuts"}
    start = sub.get("start")
    return with_sides(end, start and {k: v for k, v in start.items() if k != "cuts"})


def mixed_subtract(x, y, f, mix):
    """An Add between step's Gate subtract f of the way from x to y (between.mix): each side by itself, a side one
    end doesn't use = 0 ticks there, so its gap grows / shrinks step by step (hunt 2026-10-20)."""
    def one(a, b):
        a, b = (None if s is None else {k: v for k, v in s.items() if k != "cuts"} for s in (a, b))
        if side_used(a) and not side_used(b):
            b = dict(a, by=0.0, **({"to": 0.0} if "to" in a else {}))
        elif side_used(b) and not side_used(a):
            a = dict(b, by=0.0, **({"to": 0.0} if "to" in b else {}))
        elif a is None or b is None:
            return a if f < 0.5 else b
        return mix(a, b, f)
    x, y = plain_subtract(x), plain_subtract(y)
    return with_sides(one(end_side(x), end_side(y)), one(x.get("start"), y.get("start")))


def ticks_of(beats, ppq):
    """An amount in whole ticks (at least 1 unless it's 0)."""
    return max(1, int(round(beats * ppq))) if beats > 0 else 0


def touching(notes):
    """Per note: True if the next note on its key (every colour together, like Sustain) starts on or before its end."""
    out = np.zeros(len(notes), bool)
    if len(notes) < 2:
        return out
    order = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, p = notes[order, 0], notes[order, 1], notes[order, 2]
    out[order[:-1]] = (p[1:] == p[:-1]) & (s[1:] <= e[:-1])
    return out


def touched(notes):
    """Per note: True if the note before it on its key (every colour together) ends on or after its start."""
    out = np.zeros(len(notes), bool)
    if len(notes) < 2:
        return out
    order = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, p = notes[order, 0], notes[order, 1], notes[order, 2]
    out[order[1:]] = (p[1:] == p[:-1]) & (s[1:] <= e[:-1])
    return out


def _share(x, lo, hi, none=0.0):
    span = hi - lo
    return np.where(span > 0, (x - lo) / np.where(span > 0, span, 1), none)


def places(notes, sub, start=False):
    """Each note's u (0..1) along the graph (see the module text); NaN = nothing to run along (a run of one note, every
    note on one key / tick): it gets From. start: the start side's, across time measured from the notes' ENDS, so a
    sideways flip (which swaps the sides) mirrors exactly (hunt 2026-10-20)."""
    if sub["dir"] != "time":
        p = notes[:, 2].astype(float)
        return _share(p, p.min(), p.max(), np.nan)
    s = notes[:, 1 if start else 0].astype(float)
    if not sub.get("rows"):
        return _share(s, s.min(), s.max(), np.nan)
    from notes.sustain import gap_of, row_order
    order, gaps = row_order(notes)
    new = gaps < 0 if sub.get("join") else (gaps < 0) | (gaps > gap_of(gaps))
    first = np.flatnonzero(new)
    run = np.cumsum(new) - 1
    last = np.r_[first[1:], len(order)] - 1
    if sub.get("count"):
        u = _share(np.arange(len(order), dtype=float), first[run].astype(float), last[run].astype(float), np.nan)
    else:
        st = s[order]
        u = _share(st, st[first][run], st[last][run], np.nan)
    out = np.empty(len(notes))
    out[order] = u
    return out


def amounts(notes, sub, ppq, start=False):
    """Each note's amount in whole ticks: "by" everywhere, or along the graph from "by" to "to" (start: the start
    side's)."""
    a = ticks_of(sub["by"], ppq)
    if not sub.get("vary") or "to" not in sub:
        return a
    r = keys_up(sub)
    b = ticks_of(r["to"], ppq)
    u = places(notes, r, start)
    lone = np.isnan(u)  # (a run of one note = From, whichever way the graph runs: flips mirror, hunt 2026-10-20)
    y = np.interp(np.where(lone, 0.0, u), [p[0] for p in r["graph"]], [p[1] for p in r["graph"]])
    n = abs(b - a) + 1
    # (EPS: a note right where the amount steps up gets the same tick in a sliced piece's cut graph, hunt)
    return np.where(lone, a, a + (1 if b >= a else -1) * np.minimum(n - 1, np.floor(y * n + EPS).astype(np.int64)))


def part_subtract(sub, whole, part, ppq):
    """A piece cut off a shape whose graph runs across it (Slice, Split here, Split into letters): its own graph, so
    each of its notes keeps the amount it had. whole / part = their notes before Gate subtract. Each side by itself."""
    start = sub.get("start")
    return with_sides(part_side(end_side(sub), whole, part, ppq),
                      part_side(start, whole, part, ppq, True) if start else start)


def part_side(sub, whole, part, ppq, start=False):
    """part_subtract for one side: the graph cut over the piece's stretch, the amounts between its lowest and highest
    each the same stretch of y as before (as gaterange.part_range). Each key row by itself, no graph, no notes: as
    it is. start: the start side (across time measured from note ends, see places)."""
    if not sub.get("vary") or "to" not in sub or sub["dir"] == "time" and sub.get("rows") or not len(whole) \
            or not len(part):
        return sub
    down = sub["dir"] == "keys_down"
    r = keys_up(sub)
    col = (1 if start else 0) if r["dir"] == "time" else 2
    w0, w1 = int(whole[:, col].min()), int(whole[:, col].max())
    span = max(w1 - w0, 1)
    u0, u1 = (min(1.0, max(0.0, (int(f(part[:, col])) - w0) / span)) for f in (np.min, np.max))
    a, b = ticks_of(sub["by"], ppq), ticks_of(r["to"], ppq)
    n = abs(b - a) + 1
    us, ys = [p[0] for p in r["graph"]], [p[1] for p in r["graph"]]
    pts = [[u0, float(np.interp(u0, us, ys))]] + [[u, y] for u, y in r["graph"] if u0 < u < u1]
    pts.append([u1, float(np.interp(u1, us, ys))])
    band = [min(n - 1, math.floor(y * n + EPS)) for _, y in pts]
    j0, j1 = min(band), max(band)
    m = j1 - j0 + 1
    width = u1 - u0
    graph = [[(u - u0) / width if width > 1e-12 else float(k > 0), min(1.0, max(0.0, (y * n - j0) / m))]
             for k, (u, y) in enumerate(pts)]
    graph[0][0], graph[-1][0] = 0.0, 1.0
    if down:
        graph = [[1 - u, y] for u, y in reversed(graph)]
    sign = 1 if b >= a else -1
    return dict(sub, by=(a + sign * j0) / ppq, to=(a + sign * j1) / ppq, graph=graph)


def box_of(notes):
    """The notes' box: first start, last end, lowest key, highest key."""
    return int(notes[:, 0].min()), int(notes[:, 1].max()), int(notes[:, 2].min()), int(notes[:, 2].max())


def cut_marks(notes, flags, col):
    """Cut marks for "Only notes that touch" (hunt 2026-10-20): a piece cut off a shape keeps its notes that touched a
    note now in another piece as touching. notes = the piece's (before Gate subtract), flags = which of them; col = the
    side's tick (1 end, 0 start). Each mark = [u, v]: the tick and key as shares of the piece's notes' box, so they
    move / stretch / flip with it."""
    if not flags.any():
        return []
    t0, t1, k0, k1 = box_of(notes)
    sel = notes[flags]
    return [[float(_share(float(t), t0, t1)), float(_share(float(k), k0, k1))] for t, k in zip(sel[:, col], sel[:, 2])]


def marked(notes, cuts, col, box=None):
    """Per note: True where a cut mark is (its key, its end / start tick, rounded to whole ticks). box: the notes'
    box the marks are shares of (default: these notes')."""
    t0, t1, k0, k1 = box or box_of(notes)
    m = np.asarray(cuts, float)
    ticks = np.round(t0 + m[:, 0] * (t1 - t0)).astype(np.int64)
    keys = np.round(k0 + m[:, 1] * (k1 - k0)).astype(np.int64)
    big = np.int64(1) << 40
    return np.isin(notes[:, 2].astype(np.int64) * big + notes[:, col], keys * big + ticks)


def side_amounts(notes, side, ppq, touch, start=False):
    """Each note's ticks off one side (0 where unused); touch(notes) = the notes "Only notes that touch" takes."""
    if not side or side.get("off"):
        return np.zeros(len(notes), np.int64)
    n = np.broadcast_to(np.asarray(amounts(notes, side, ppq, start), np.int64), (len(notes),))
    if not side.get("touch"):
        return n
    takes = touch(notes)
    if side.get("cuts"):
        takes = takes | marked(notes, side["cuts"], 0 if start else 1)
    return np.where(takes, n, 0)


def subtracted(notes, sub, ppq):
    """notes (start, end, pitch, velocity, ...) with their ends moved earlier and / or starts later (engine.fx_notes).
    Never under 1 tick, never longer than they were; both sides wanting more than the note has = each cut by the same
    share."""
    if not len(notes):
        return notes
    ne = side_amounts(notes, sub, ppq, touching)
    ns = side_amounts(notes, sub.get("start"), ppq, touched, True)
    out = notes.copy()
    s, e = out[:, 0], out[:, 1]
    room = np.maximum(e - s - 1, 0)
    total = ne + ns
    over = total > room
    if over.any():
        ns = np.where(over, room * ns // np.maximum(total, 1), ns)
        ne = np.where(over, room - ns, ne)
    out[:, 0] = s + ns
    out[:, 1] = e - ne
    return out
