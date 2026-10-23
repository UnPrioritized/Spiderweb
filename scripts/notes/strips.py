"""Channel per strip (Experimental; right-click pasted notes, window/strips_window.py; asked by a user whose pasted song
cycles 16 colours over 351 strips): each strip of a pasted track gets channels of its own, so each can be edited
alone elsewhere. A guess, so it's put on per shape: one pasted track = one channel stays the default (user
2026-10-20).

RULE = PER KEY ROW (user 2026-10-22; one by note starts across the whole shape failed on a merged copy, whose rows
slid differently): on each key, a track coming back after at least `gap` notes of other tracks played on that key =
its next strip. Each strip = its track + strip x 15 (16 with channel 10), like "Repeated colours get new channels"
(render's rounds, the first strip keeping its channels: all_notes), so no colour changes on screen.
Known misses (Help says so): one colour never splits; two colours taking turns on a key split at every turn unless
the gap is raised past it."""

import numpy as np

MOST_GAP = 1000  # (the "notes of other colours between" box's top)


def clean_strips(s):
    """A saved sh["strips"] -> {"gap": 1..MOST_GAP}, or None (not a dict: off)."""
    if not isinstance(s, dict):
        return None
    g = s.get("gap", 1)
    if isinstance(g, bool) or not isinstance(g, (int, float)) or g != g:
        g = 1
    return {"gap": int(min(max(round(g), 1), MOST_GAP))}


def can_strip(sh):
    """Shapes Channel per strip works on: notes shapes (pasted, and the frozen ones: Merged notes, Turn into plain
    notes, Apply mask copies; user 2026-10-23: the song's merged copy said "1 strip"), not images (their tracks are
    the picture's colours)."""
    return "notes" in sh and "picture" not in sh and "picture_notes" not in sh


def has_strips(sh):
    return isinstance(sh.get("strips"), dict) and can_strip(sh)


def gap_of(sh):
    return (clean_strips(sh.get("strips")) or {"gap": 1})["gap"]


def strip_laps(notes, tracks, gap=1):
    """(start, end, key, ...) notes + each one's track -> each note's strip of its track (0 = the first). On each key,
    in time order, a run of one track's notes coming back after at least gap notes of other tracks = its next
    strip."""
    n = len(notes)
    if not n:
        return np.zeros(0, np.int64)
    tracks = np.asarray(tracks, np.int64)
    key, start = np.asarray(notes[:, 2], np.int64), np.asarray(notes[:, 0], np.int64)
    o = np.lexsort((tracks, start, key))
    k, t = key[o], tracks[o]
    new = np.ones(n, bool)
    new[1:] = (k[1:] != k[:-1]) | (t[1:] != t[:-1])
    run_at = np.flatnonzero(new)  # (each run of one track on a key: where it starts / ends in key, time order)
    run_end = np.append(run_at[1:], n) - 1
    rk, rt = k[run_at], t[run_at]
    r = np.lexsort((run_at, rt, rk))  # (the runs of each track on each key, in time order)
    same = np.zeros(len(r), bool)
    same[1:] = (rk[r][1:] == rk[r][:-1]) & (rt[r][1:] == rt[r][:-1])
    between = np.zeros(len(r), np.int64)  # (the other tracks' notes since its last run on that key)
    between[1:] = run_at[r][1:] - run_end[r][:-1] - 1
    back = np.cumsum(same & (between >= gap))
    first = np.maximum.accumulate(np.where(same, 0, np.arange(len(r))))
    laps = np.empty(len(r), np.int64)
    laps[r] = back - back[first]
    out = np.empty(n, np.int64)
    out[o] = laps[np.cumsum(new) - 1]
    return out


def strip_sizes(rendered, owner):
    """The strips shape number owner sounds as now (Multi channel: each strip its own slot): each strip's note count,
    in the order they start."""
    mine = rendered[rendered[:, 5] == owner]
    if not len(mine):
        return np.zeros(0, np.int64)
    ids, which = np.unique(mine[:, 4], return_inverse=True)
    first = np.full(len(ids), np.iinfo(np.int64).max)
    np.minimum.at(first, which.reshape(-1), mine[:, 0])
    return np.bincount(which.reshape(-1), minlength=len(ids))[np.argsort(first, kind="stable")]


def strip_colours(drawn, owners):
    """The notes as painted while the Channel per strip window shows the strips (drawn: the rendered rows): each strip
    of these shapes in a colour of its own, neighbours far apart round the colours (strips counted in the order they
    start), so one split wrong shows as confetti, one missed as a long block."""
    if not owners or not len(drawn):
        return drawn
    out = None
    for o in owners:
        at = np.flatnonzero(drawn[:, 5] == o)
        if not len(at):
            continue
        ids, which = np.unique(drawn[at, 4], return_inverse=True)
        which = which.reshape(-1)
        first = np.full(len(ids), np.iinfo(np.int64).max)
        np.minimum.at(first, which, drawn[at, 0])
        rank = np.empty(len(ids), np.int64)
        rank[np.argsort(first, kind="stable")] = np.arange(len(ids))
        if out is None:
            out = drawn.copy()
        out[at, 4] = rank[which] * 7 % 15
    return drawn if out is None else out
