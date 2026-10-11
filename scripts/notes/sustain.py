"""
Sustain (right-click "Sustain..."): a shape's notes held on, made for lagging out MIDI players.

sh["sustain"] = {} (the hold gap worked out by itself) or {"gap": beats} (typed). Each key row on its own: notes
whose gaps are no longer than the hold gap make one group, and every note of a group ends where the group's last
note ends (stacked on the same key). Longer gaps are holes in the shape and stay empty, so its look is kept.
Done last, after everything else that makes the shape's notes; only lengths change. A sustained shape keeps its
overlaps in every Channels mode, other shapes still fix their clashes with it (engine.render).
"""

import math

import numpy as np

MOST_GAP = 10 ** 4  # beats a typed hold gap may be


def clean_sustain(s):
    """sh["sustain"] from a file -> valid, or None (not sustained)."""
    if not isinstance(s, dict):
        return None
    try:
        gap = float(s["gap"]) if "gap" in s else None
    except (TypeError, ValueError):
        gap = None
    if gap is None or not math.isfinite(gap):
        return {}
    return {"gap": min(max(gap, 0.0), MOST_GAP)}


def is_sustained(sh):
    """True if sh's notes are held on (a Hz bass never is: holding its repeats would ruin the sound; nor an image:
    its notes are its picture, user)."""
    return isinstance(sh.get("sustain"), dict) and not sh.get("hz") and "picture" not in sh


def row_order(notes, tracks=None):
    """notes (start, end, pitch, ...) -> (the order key row by key row, in time; each note's gap to the notes
    before it on its key: what has sounded until then, -1 for a row's first note). tracks: each note's colour; a
    key row of each colour is a row of its own (user: colours held apart)."""
    if tracks is None:
        order = np.lexsort((notes[:, 0], notes[:, 2]))
        p = notes[order, 2]
    else:
        tracks = np.asarray(tracks, np.int64)
        order = np.lexsort((notes[:, 0], tracks, notes[:, 2]))
        p = notes[order, 2] * (int(tracks.max()) - int(tracks.min()) + 1) + tracks[order] - int(tracks.min())
    s, e = notes[order, 0], notes[order, 1]
    gap = np.full(len(s), -1, np.int64)
    if len(s) > 1:
        same = p[1:] == p[:-1]
        shift = int(e.max() - e.min()) + 1  # (running end per row: each row lifted past the one before)
        row = np.concatenate([[0], np.cumsum(~same)])
        ran = np.maximum.accumulate(e - e.min() + row * shift) - row * shift + e.min()
        gap[1:] = np.where(same, np.maximum(s[1:] - ran[:-1], 0), -1)
    return order, gap


def auto_gap(notes, tracks=None):
    """The hold gap in ticks worked out from the notes: the most common gap between notes on a key (the spacing a
    shape's own notes have), a little more for rounding. Holes are rarer and longer, so they don't count."""
    if len(notes) < 2:
        return 1
    gaps = row_order(notes, tracks)[1]
    gaps = gaps[gaps >= 0]
    if not len(gaps):
        return 1
    vals, counts = np.unique(gaps, return_counts=True)
    most = int(vals[np.argmax(counts)])
    return most + max(1, most // 2)


def hold_gap(sus, notes, ppq, tracks=None):
    """The hold gap in ticks: the typed one, else auto_gap."""
    if "gap" in sus:
        return int(round(sus["gap"] * ppq))
    return auto_gap(notes, tracks)


def sustained(notes, sus, ppq, tracks=None):
    """notes (start, end, pitch, velocity) with each group's notes held to the group's last end (tracks: each
    colour's notes grouped on their own)."""
    if len(notes) < 2:
        return notes
    gap = hold_gap(sus, notes, ppq, tracks)
    order, gaps = row_order(notes, tracks)
    new = (gaps < 0) | (gaps > gap)  # (a new group: a row's first note, or after a hole)
    first = np.flatnonzero(new)
    group = np.cumsum(new) - 1
    ends = np.maximum.reduceat(notes[order, 1], first)
    out = notes.copy()
    out[order, 1] = ends[group]
    return out


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
