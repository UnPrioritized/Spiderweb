"""
Gate subtract (right-click "Gate subtract..."): every note of a shape ends a little earlier, so a blank space shows
between notes that touched. Only the end moves (the start is the rhythm); a note never gets shorter than 1 tick.

sh["subtract"] = {"by": beats (kept in beats like every length: a PPQ change keeps the look; whole ticks, at least 1),
"touch": True (only notes the next note on their key starts on or before the end of; default: every note)}
+ the graph ("vary": True while it's used; the rest kept while it's off so ticking again brings it back): "to": beats,
"graph": [[u, y]], "dir": "time" / "keys" / "keys_down", "rows", "join", "count" (as Gate Range's, gaterange.py).
The amount goes from "by" to "to" along the graph, every whole tick between an equal stretch of y (y_gate). u, per
note: time = its start from the shape's first note start (0) to its last note start (1); keys = its key from the
lowest (0) to the highest (1). "rows" (time only, "Each key row by itself"): each run of a key row on its own (a run =
notes whose gaps are no longer than the usual gap, as Gate aware flip finds them; "join" = the whole row one run),
its first note 0, its last 1: by start time, or by its place among the run's notes ("count"). A run of one note = 0.
Done LAST, after everything else that makes the shape's notes, Sustain too (engine.fx_notes), so no start moves.
Not on a Hz bass (its own note lengths) or an image (its notes are its picture), like Sustain.
"""

import math

import numpy as np

from notes.gaterange import DIRS, STRAIGHT, clean_graph, keys_up

MOST = 10 ** 4  # beats a typed amount may be


def _beats(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return min(v, MOST) if math.isfinite(v) and v > 0 else None


def clean_subtract(s):
    """sh["subtract"] from a file -> valid, or None (not subtracted)."""
    if not isinstance(s, dict):
        return None
    by = _beats(s.get("by"))
    if by is None:
        return None
    out = {"by": by}
    if s.get("touch") is True:
        out["touch"] = True
    to = _beats(s.get("to"))
    if to is not None:  # (the graph: kept while it's off too)
        out.update(to=to, graph=clean_graph(s.get("graph") or ()) or [list(p) for p in STRAIGHT],
                   dir=s["dir"] if s.get("dir") in DIRS else "time")
        for key in ("vary", "rows", "join", "count"):
            if s.get(key) is True:
                out[key] = True
    return out


def is_subtracted(sh):
    """True if sh's gates are made shorter (never a Hz bass or an image)."""
    return isinstance(sh.get("subtract"), dict) and not sh.get("hz") and "picture" not in sh


def ticks_of(beats, ppq):
    """An amount in whole ticks (at least 1)."""
    return max(1, int(round(beats * ppq)))


def touching(notes):
    """Per note: True if the next note on its key (every colour together, like Sustain) starts on or before its end."""
    out = np.zeros(len(notes), bool)
    if len(notes) < 2:
        return out
    order = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, p = notes[order, 0], notes[order, 1], notes[order, 2]
    out[order[:-1]] = (p[1:] == p[:-1]) & (s[1:] <= e[:-1])
    return out


def _share(x, lo, hi):
    span = hi - lo
    return np.where(span > 0, (x - lo) / np.where(span > 0, span, 1), 0.0)


def places(notes, sub):
    """Each note's u (0..1) along the graph (see the module text)."""
    if sub["dir"] != "time":
        p = notes[:, 2].astype(float)
        return _share(p, p.min(), p.max())
    s = notes[:, 0].astype(float)
    if not sub.get("rows"):
        return _share(s, s.min(), s.max())
    from notes.sustain import gap_of, row_order
    order, gaps = row_order(notes)
    new = gaps < 0 if sub.get("join") else (gaps < 0) | (gaps > gap_of(gaps))
    first = np.flatnonzero(new)
    run = np.cumsum(new) - 1
    last = np.r_[first[1:], len(order)] - 1
    if sub.get("count"):
        u = _share(np.arange(len(order), dtype=float), first[run].astype(float), last[run].astype(float))
    else:
        st = s[order]
        u = _share(st, st[first][run], st[last][run])
    out = np.empty(len(notes))
    out[order] = u
    return out


def amounts(notes, sub, ppq):
    """Each note's amount in whole ticks: "by" everywhere, or along the graph from "by" to "to"."""
    a = ticks_of(sub["by"], ppq)
    if not sub.get("vary") or "to" not in sub:
        return a
    r = keys_up(sub)
    b = ticks_of(r["to"], ppq)
    y = np.interp(places(notes, r), [p[0] for p in r["graph"]], [p[1] for p in r["graph"]])
    n = abs(b - a) + 1
    return a + (1 if b >= a else -1) * np.minimum(n - 1, (y * n).astype(np.int64))


def subtracted(notes, sub, ppq):
    """notes (start, end, pitch, velocity, ...) with their ends moved earlier (engine.fx_notes). Never under 1 tick,
    never longer than they were."""
    if not len(notes):
        return notes
    n = amounts(notes, sub, ppq)
    out = notes.copy()
    s, e = out[:, 0], out[:, 1]
    new = np.maximum(e - n, np.minimum(e, s + 1))
    if sub.get("touch"):
        new = np.where(touching(notes), new, e)
    out[:, 1] = new
    return out
