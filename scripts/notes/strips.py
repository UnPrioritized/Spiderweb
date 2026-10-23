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
    """(start, end, key, ...) notes + each one's track -> each note's strip of its track (0 = the first, in the order
    they start). On each key, in time order, a run of one track's notes coming back after at least gap notes of other
    tracks = its next piece (key_laps). Going up the keys, a track's pieces keep the numbering of the key below while
    both keys have as many (a merged copy whose rows slid apart: only counting works there, user's song); where the
    count changes (a strip starting / ending on that key: strips climbing the keys, hunt 2026-10-23) each piece is
    matched to the nearest one in time on the key below and the shift most of them agree on is carried up."""
    n = len(notes)
    if not n:
        return np.zeros(0, np.int64)
    tracks = np.asarray(tracks, np.int64)
    key, start, end = (np.asarray(notes[:, c], np.int64) for c in (2, 0, 1))
    piece, which = rows_unique(tracks, key, key_laps(notes, tracks, gap))  # (by track, key, piece = time on a key)
    p0, p1 = np.full(len(piece), np.iinfo(np.int64).max), np.full(len(piece), np.iinfo(np.int64).min)
    np.minimum.at(p0, which, start)
    np.maximum.at(p1, which, end)
    mid = (p0 + p1) / 2
    pt, pk, pl = piece[:, 0], piece[:, 1], piece[:, 2]
    cut = np.flatnonzero((pt[1:] != pt[:-1]) | (pk[1:] != pk[:-1])) + 1
    bounds = np.concatenate([[0], cut, [len(piece)]]).tolist()
    shift = np.zeros(len(piece), np.int64)
    prev = None  # (track, key, first piece, end, its shift) of the group before
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        t, k, s = int(pt[lo]), int(pk[lo]), 0
        if prev is not None and prev[0] == t and prev[1] == k - 1:
            plo, phi, s = prev[2:]
            if phi - plo != hi - lo:  # (a strip starts or ends here: line up by time)
                m, here = mid[plo:phi], mid[lo:hi]
                if len(m) > 1:
                    j = np.clip(np.searchsorted(m, here), 1, len(m) - 1)
                    j = np.where(here - m[j - 1] <= m[j] - here, j - 1, j)
                else:
                    j = np.zeros(len(here), np.int64)
                # (only pieces nearer their match than half the way to their neighbours vote: a strip starting
                # here has none below; none near = the nearest one alone)
                dist = np.abs(here - m[j])
                gaps = np.diff(here)
                room = np.minimum(np.append(gaps, np.inf), np.insert(gaps, 0, np.inf))
                ok = dist < room / 2
                if not ok.any():
                    ok = dist == dist.min()
                vals, cnt = np.unique((pl[plo:phi][j] + s - pl[lo:hi])[ok], return_counts=True)
                s = int(vals[cnt.argmax()])
        shift[lo:hi] = s
        prev = (t, k, lo, hi, s)
    # a track's strips numbered by their first start
    comp, cw = rows_unique(pt, pl + shift)
    c0 = np.full(len(comp), np.iinfo(np.int64).max)
    np.minimum.at(c0, cw, p0)
    ct = comp[:, 0]
    o = np.lexsort((comp[:, 1], c0, ct))
    first = np.ones(len(o), bool)
    first[1:] = ct[o][1:] != ct[o][:-1]
    rank = np.arange(len(o)) - np.maximum.accumulate(np.where(first, np.arange(len(o)), 0))
    lap = np.empty(len(comp), np.int64)
    lap[o] = rank
    return lap[cw][which]


def strip_spans(notes, tracks, slots):
    """{first strip's slot: [(slot, first start, last end)]}: the slot each strip of each track sounds on and when, in
    the order they start, under the slot its first strip keeps (the track's usual one: App.cc_messages gives later
    strips their track's CCs, hunt 2026-10-23)."""
    pairs, which = rows_unique(tracks, slots)
    s0 = np.full(len(pairs), np.iinfo(np.int64).max)
    s1 = np.full(len(pairs), np.iinfo(np.int64).min)
    np.minimum.at(s0, which, np.asarray(notes[:, 0], np.int64))
    np.maximum.at(s1, which, np.asarray(notes[:, 1], np.int64))
    out = {}
    for (t, s), a, b in sorted(zip(map(tuple, pairs.tolist()), s0.tolist(), s1.tolist()), key=lambda e: e[1]):
        out.setdefault(t, []).append((s, a, b))
    return {v[0][0]: v for v in out.values()}


def rows_unique(*cols):
    """The different rows of these whole-number columns (sorted) + each row's place among them: one number per row
    (much quicker than np.unique over rows)."""
    cols = [np.asarray(c, np.int64) for c in cols]
    code = np.zeros(len(cols[0]), np.int64)
    for c in cols:
        lo = int(c.min())
        code = code * (int(c.max()) - lo + 1) + (c - lo)
    u, which = np.unique(code, return_inverse=True)
    out = np.empty((len(u), len(cols)), np.int64)
    rest = u.copy()
    for i in range(len(cols) - 1, -1, -1):
        lo = int(cols[i].min())
        size = int(cols[i].max()) - lo + 1
        out[:, i] = rest % size + lo
        rest //= size
    return out, which.reshape(-1)


def key_laps(notes, tracks, gap=1):
    """strip_laps' first step: each note's piece of its track on its key (0, 1, 2... in time order): a run of one
    track's notes coming back after at least gap notes of other tracks on that key = its next piece."""
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
