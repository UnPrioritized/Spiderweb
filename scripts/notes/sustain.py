"""
Sustain (right-click "Sustain..."): a shape's notes held on, made for lagging out MIDI players.

sh["sustain"] = {} (the hold gap worked out by itself), + "gap": beats (typed), + "most": beats (each note at most
this long). Each key row on its own: notes whose gaps are no longer than the hold gap make one group, and every note
of a group ends where the group's last note ends (stacked on the same key), or at its start + "most" if that comes
first (never shorter than it was). Longer gaps are holes in the shape and stay empty, so its look is kept.
Stops ("every": beats from the first note's start, + "from"; "stops": {"at": [share of the notes' time, from the
first start to the last end before Sustain, so they stretch with the shape], "past"?, "off"? (kept while unticked)}):
every note sounding across one ends there and the sustain starts again on the same tick, one note per key (no gap:
the look is kept). "past": a stop put past the last end holds each key row's last group on to it.
Done last, after everything else that makes the shape's notes; only lengths change. A sustained shape keeps its
overlaps in every Channels mode, other shapes still fix their clashes with it (engine.render).
"""

import math

import numpy as np

MOST_GAP = 10 ** 4  # beats a typed hold gap may be
MOST_STOPS = 10 ** 6  # stops made by "every"
MOST_PIECES = 20 * 10 ** 6  # notes the stops may cut the notes into (like Chop's limit)
MOST_POINTS = 10 ** 4  # stops put by hand
MOST_PAST = 1000  # how far past the end (in the notes' lengths) a stop put by hand may be


def clean_sustain(s):
    """sh["sustain"] from a file -> valid, or None (not sustained)."""
    if not isinstance(s, dict):
        return None
    out = {}
    gap = _beats(s.get("gap"))
    if gap is not None:
        out["gap"] = gap
    most = _beats(s.get("most"))
    if most:  # (0 = held no longer than the notes are: nothing)
        out["most"] = most
    every = _beats(s.get("every"))
    if every:
        out["every"] = every
        at = _beats(s.get("from"))
        if at:
            out["from"] = at
    stops = s.get("stops")
    if isinstance(stops, dict):
        at, raw = [], stops.get("at")
        for f in raw if isinstance(raw, list) else []:
            try:
                f = float(f)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f) and 0 < f <= MOST_PAST:
                at.append(f)
        out["stops"] = {"at": sorted(set(at))[:MOST_POINTS]}
        for key in ("past", "off"):
            if stops.get(key) is True:
                out["stops"][key] = True
    return out


def _beats(v):
    """A length in beats from a file (0..MOST_GAP), or None."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return min(max(v, 0.0), MOST_GAP) if math.isfinite(v) else None


def is_sustained(sh):
    """True if sh's notes are held on (a Hz bass never is: holding its repeats would ruin the sound; nor an image:
    its notes are its picture, user)."""
    return isinstance(sh.get("sustain"), dict) and not sh.get("hz") and "picture" not in sh


def row_order(notes):
    """notes (start, end, pitch, ...) -> (the order key row by key row, in time; each note's gap to the notes
    before it on its key: what has sounded until then, -1 for a row's first note). Every colour together (user: a
    row is held whole, colours stacking on each other)."""
    order = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, p = notes[order, 0], notes[order, 1], notes[order, 2]
    gap = np.full(len(s), -1, np.int64)
    if len(s) > 1:
        same = p[1:] == p[:-1]
        shift = int(e.max() - e.min()) + 1  # (running end per row: each row lifted past the one before)
        row = np.concatenate([[0], np.cumsum(~same)])
        ran = np.maximum.accumulate(e - e.min() + row * shift) - row * shift + e.min()
        gap[1:] = np.where(same, np.maximum(s[1:] - ran[:-1], 0), -1)
    return order, gap


def auto_gap(notes):
    """The hold gap in ticks worked out from the notes: the most common gap between notes on a key (the spacing a
    shape's own notes have), a little more for rounding. Holes are rarer and longer, so they don't count."""
    if len(notes) < 2:
        return 1
    return gap_of(row_order(notes)[1])


def gap_of(gaps):
    """auto_gap from row_order's gaps."""
    gaps = gaps[gaps >= 0]
    if not len(gaps):
        return 1
    vals, counts = np.unique(gaps, return_counts=True)
    most = int(vals[np.argmax(counts)])
    return most + max(1, most // 2)


def hold_gap(sus, notes, ppq):
    """The hold gap in ticks: the typed one, else auto_gap."""
    if "gap" in sus:
        return int(round(sus["gap"] * ppq))
    return auto_gap(notes)


def sustained(notes, sus, ppq, reach=None):
    """notes (start, end, pitch, velocity) with each group's notes held to the group's last end (or to their start +
    "most" if that comes first; never shorter). reach: each key row's last group held on to this tick (a stop past
    the shape's end)."""
    if len(notes) < (1 if reach is not None else 2):
        return notes
    gap = hold_gap(sus, notes, ppq)
    order, gaps = row_order(notes)
    new = (gaps < 0) | (gaps > gap)  # (a new group: a row's first note, or after a hole)
    first = np.flatnonzero(new)
    group = np.cumsum(new) - 1
    ends = np.maximum.reduceat(notes[order, 1], first)[group]
    if reach is not None:
        p = notes[order, 2]
        last = np.flatnonzero(np.append(p[1:] != p[:-1], True))  # (each row's last note)
        row = np.cumsum(np.append(0, p[1:] != p[:-1]))
        ends = np.where(group == group[last][row], np.maximum(ends, reach), ends)
    if "most" in sus:
        ends = np.maximum(notes[order, 1], np.minimum(ends, notes[order, 0] + max(1, round(sus["most"] * ppq))))
    out = notes.copy()
    out[order, 1] = ends
    return out


def held(notes, tracks, sus, ppq):
    """A sustained shape's notes and tracks (engine.fx_notes): sustained, then cut at its stops."""
    stops = stop_ticks(notes, sus, ppq)
    reach = int(stops[-1]) if len(stops) and stops[-1] > notes[:, 1].max() else None
    notes = sustained(notes, sus, ppq, reach)
    return cut_at(notes, tracks, stops) if len(stops) else (notes, tracks)


def stop_ticks(notes, sus, ppq):
    """Where the sustain stops (sorted ticks inside the notes' time, from the notes before Sustain; past the last
    end too with "past"): "every" beats from the first note's start (+ "from"), and the ones put by hand."""
    if not len(notes):
        return np.zeros(0, np.int64)
    lo, hi = int(notes[:, 0].min()), int(notes[:, 1].max())
    got = [np.zeros(0, np.int64)]
    if "every" in sus:
        step = max(1, round(sus["every"] * ppq))
        first = lo + round(sus.get("from", 0) * ppq)
        k0 = max(0, (lo - first) // step + 1)  # (the first stop past the start)
        k1 = min((hi - 1 - first) // step + 1, k0 + MOST_STOPS)
        got.append(first + step * np.arange(k0, max(k0, k1), dtype=np.int64))
    stops = sus.get("stops")
    if stops and not stops.get("off") and stops["at"]:
        at = lo + np.round(np.asarray(stops["at"], float) * (hi - lo)).astype(np.int64)
        got.append(at[(at > lo) & ((at < hi) | bool(stops.get("past")))])
    return np.unique(np.concatenate(got))


def stop_tick(notes, f):
    """A stop put by hand (share f) as a tick, for these notes (before Sustain)."""
    lo, hi = int(notes[:, 0].min()), int(notes[:, 1].max())
    return lo + int(round(f * (hi - lo)))


def stop_share(notes, tick):
    """stop_tick turned back: the share for a stop at this tick (None: notes with no length)."""
    lo, hi = int(notes[:, 0].min()), int(notes[:, 1].max())
    return (tick - lo) / (hi - lo) if hi > lo else None


def cut_at(notes, tracks, stops):
    """Every note sounding across a stop ends there and a new one starts on the same tick (no gap); the notes
    starting on a stop on the same key make ONE note there (the sustain starts again: the longest, the loudest
    velocity). Too many pieces = left whole."""
    s, e = notes[:, 0], notes[:, 1]
    a = np.searchsorted(stops, s, side="right")
    k = np.searchsorted(stops, e, side="left") - a  # (stops inside each note)
    k = np.maximum(k, 0)
    if not k.any() or len(notes) + int(k.sum()) > MOST_PIECES:
        return notes, tracks
    rep = np.repeat(np.arange(len(notes)), k + 1)
    j = np.arange(len(rep)) - np.repeat(np.cumsum(k + 1) - (k + 1), k + 1)  # (piece number in its note)
    at = stops[np.clip(a[rep] + j - 1, 0, len(stops) - 1)]
    till = stops[np.clip(a[rep] + j, 0, len(stops) - 1)]
    out = notes[rep]
    out[:, 0] = np.where(j == 0, s[rep], at)
    out[:, 1] = np.where(j == k[rep], e[rep], till)
    tr = None if tracks is None else np.asarray(tracks)[rep]
    on = np.flatnonzero(np.isin(out[:, 0], stops))
    if len(on) > 1:
        o = on[np.lexsort((-out[on, 3], -out[on, 1], out[on, 0], out[on, 2]))]
        new = np.ones(len(o), bool)
        new[1:] = (out[o[1:], 2] != out[o[:-1], 2]) | (out[o[1:], 0] != out[o[:-1], 0])
        first = np.flatnonzero(new)
        out[o[first], 3] = np.maximum.reduceat(out[o, 3], first)
        keep = np.ones(len(out), bool)
        keep[o[~new]] = False
        out = out[keep]
        tr = None if tr is None else tr[keep]
    return out, tr


def cut_by(normal, held):
    """normal notes (start, end, pitch, velocity, slot, owner) ending where a held (sustained) note on the same key
    and slot starts inside them; held notes are never cut, a normal note starting inside one is left as it is."""
    if not len(normal) or not len(held):
        return normal
    lo = int(min(normal[:, 0].min(), held[:, 0].min()))
    hk = (held[:, 4] * 256 + held[:, 2]) << 40 | (held[:, 0] - lo)
    hk = np.unique(hk)
    nk = (normal[:, 4] * 256 + normal[:, 2]) << 40 | (normal[:, 0] - lo)
    at = np.searchsorted(hk, nk, side="right")
    ok = at < len(hk)
    nxt = hk[np.minimum(at, len(hk) - 1)]
    start = (nxt & ((1 << 40) - 1)) + lo
    cut = ok & (nxt >> 40 == nk >> 40) & (start < normal[:, 1])
    if not cut.any():
        return normal
    out = normal.copy()
    out[cut, 1] = start[cut]
    return out
