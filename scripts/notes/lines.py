"""The LINES inside each track (a user's "shards", 2026-10-30; shared by Flip each line sideways / upside down and
Split into pieces). One track's notes are one line when they touch: on the same key back to back or overlapping,
or on NEIGHBOURING keys (1 apart) with one starting where the other ends or overlapping it. By tick position, never
by gate. A gap in time, or an empty key between, = separate lines; woven staircases are one line (user's pictures:
dev/data/shard-flip/imageset1 + imageset2)."""

import numpy as np


def note_lines(notes, tracks=None):
    """(start, end, key, ...) notes + each one's track (None = all one track) -> (each note's line, how many). Lines
    are numbered by track (in number order), then first start, then lowest key."""
    n = len(notes)
    if not n:
        return np.zeros(0, np.int64), 0
    start, end, key = (np.asarray(notes[:, c], np.int64) for c in (0, 1, 2))
    tracks = np.zeros(n, np.int64) if tracks is None else np.asarray(tracks, np.int64)
    # each track's notes on each key in time order -> RUNS of touching ones (one covered stretch each)
    _, t_rank = np.unique(tracks, return_inverse=True)
    t_rank = t_rank.reshape(-1)
    k0, t0 = int(key.min()), int(start.min())
    lane = t_rank * (int(key.max()) - k0 + 2) + (key - k0)  # (+2: a track's top key + 1 is an empty lane)
    span = int(max(end.max(), start.max())) - t0 + 2
    o = np.lexsort((start, lane))
    ln, s, e = lane[o], start[o] - t0, end[o] - t0
    reach = np.maximum.accumulate(ln * span + e)  # (furthest end so far, never carried into the next lane)
    new = np.ones(n, bool)
    new[1:] = (ln[1:] != ln[:-1]) | (ln[1:] * span + s[1:] > reach[:-1])
    run = np.cumsum(new) - 1
    at = np.flatnonzero(new)
    r_lane, r_start = ln[at], s[at]
    r_end = np.maximum.reduceat(e, at)
    # runs on the key above that touch each run: those ending at / after its start AND starting at / before its end
    # (runs on one key never touch each other, so both are sorted and the matches are one stretch)
    s_code, e_code = r_lane * span + r_start, r_lane * span + r_end
    lo = np.searchsorted(e_code, (r_lane + 1) * span + r_start, "left")
    hi = np.searchsorted(s_code, (r_lane + 1) * span + r_end, "right")
    many = np.maximum(hi - lo, 0)
    a = np.repeat(np.arange(len(at)), many)
    b = lo[a] + (np.arange(len(a)) - np.repeat(np.cumsum(many) - many, many))
    group = joined(len(at), a, b)
    # numbered by track, first start, lowest key
    g_ids, g = np.unique(group, return_inverse=True)
    g = g.reshape(-1)
    first = np.full(len(g_ids), np.iinfo(np.int64).max)
    np.minimum.at(first, g, r_start)
    low = np.full(len(g_ids), np.iinfo(np.int64).max)
    np.minimum.at(low, g, r_lane)  # (same track: the lowest lane = the lowest key)
    order = np.lexsort((low, first, low // (int(key.max()) - k0 + 2)))
    number = np.empty(len(g_ids), np.int64)
    number[order] = np.arange(len(g_ids))
    out = np.empty(n, np.int64)
    out[o] = number[g[run]]
    return out, len(g_ids)


def joined(n, a, b):
    """The groups n things fall into when each pair (a[i], b[i]) is joined: each thing's group = its lowest member.
    Roots hooked onto the lower root, then every pointer followed to its root, until no pair is split."""
    label = np.arange(n)
    while len(a):
        ra, rb = label[a], label[b]
        split = ra != rb
        if not split.any():
            break
        a, b, ra, rb = a[split], b[split], ra[split], rb[split]
        np.minimum.at(label, np.maximum(ra, rb), np.minimum(ra, rb))
        while True:
            nxt = label[label]
            if np.array_equal(nxt, label):
                break
            label = nxt
    return label


def line_spans(notes, line, count):
    """Each line's (first start, last end, lowest key, highest key) as four arrays."""
    start, end, key = (np.asarray(notes[:, c], np.int64) for c in (0, 1, 2))
    big, small = np.iinfo(np.int64).max, np.iinfo(np.int64).min
    lo, hi, kl, kh = np.full(count, big), np.full(count, small), np.full(count, big), np.full(count, small)
    np.minimum.at(lo, line, start)
    np.maximum.at(hi, line, end)
    np.minimum.at(kl, line, key)
    np.maximum.at(kh, line, key)
    return lo, hi, kl, kh
