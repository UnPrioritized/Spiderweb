"""Recolour (side panel under Colours, window/panel_recolour.py; asked by a user for their note art, 2026-10-25):
a shape's notes keep their keys, starts and gates, only which colour each one has changes.

Shuffle: the colours the shape already has go round its notes. Amount (0-100 %) = how many notes get a new one,
each picked at random among the shape's colours (an equal share each). STEADY: each note's dice come from the note
itself (start, end, key counted from the shape's first tick and lowest key, its colour before) and the shape's seed
(New shuffle = another seed), so drawing again or moving the shape gives the same colours and an edit only changes
the notes it touches (a copy looks the same until its own New shuffle). Each note keeps its Colours round ("Repeated
colours get new channels"). The notes are first settled as they sound with Recolour off (settled: a note cut by
a later one of its colour stays cut), then two notes on one key sounding together (different colours, else they'd
have been settled) never end up on one colour (they'd cut or merge each other); touching end to start is fine.
Only in Multi channel (App.notes_tracks leaves it out otherwise: in one channel it could only change overlaps).

The shape's colours keep the channels they had before (App all_notes: each colour counted as starting where it
started before, render's back), so Amount 0 looks like Off.
"""

import heapq
import random

import numpy as np

from files.speed import loops
from notes.custom import ROUND

MODES = ("shuffle",)
MOST_SEED = 2 ** 31 - 1


def clean_recolour(r):
    """A saved sh["recolour"] -> {"mode", "amount": 0..100 (3 decimals), "seed": int}, or None (Off)."""
    if not isinstance(r, dict) or r.get("mode") not in MODES:
        return None
    a = r.get("amount", 100)
    if isinstance(a, bool) or not isinstance(a, (int, float)) or a != a:
        a = 100
    s = r.get("seed", 1)
    if isinstance(s, bool) or not isinstance(s, (int, float)) or s != s:
        s = 1
    return {"mode": r["mode"], "amount": round(min(max(float(a), 0.0), 100.0), 3),
            "seed": int(min(max(s, 0), MOST_SEED))}


def can_recolour(sh):
    """Every shape but a Hz bass (its tracks are its voices and layers, not colours)."""
    return not sh.get("hz")


def is_recoloured(sh):
    return clean_recolour(sh.get("recolour")) is not None and can_recolour(sh)


def without_recolour(sh):
    return {k: v for k, v in sh.items() if k != "recolour"}


def new_seed():
    return random.randint(1, MOST_SEED)


def _mixed(x):
    """splitmix64 on uint64s: each value -> one that looks random."""
    with np.errstate(over="ignore"):
        x = x + np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def dice(notes, low, seed, salt, corner):
    """One number 0 <= u < 1 per note, from the note itself (counted from corner = the shape's first tick and lowest
    key, so moving the shape keeps it), its colour before (without its Colours round: the project's repeat tick
    changes no dice) and the seed: the same note in the same shape always gets the same one."""
    t0, k0 = corner
    cols = [np.asarray(notes[:, 0], np.int64) - t0, np.asarray(notes[:, 1], np.int64) - t0,
            np.asarray(notes[:, 2], np.int64) - k0, np.asarray(low, np.int64)]
    with np.errstate(over="ignore"):
        x = np.full(len(notes), np.uint64(seed * 2 + salt), np.uint64)
        for c in cols:
            x = _mixed(x ^ c.view(np.uint64))
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def recoloured(notes, tracks, rc, held=False):
    """The shape's notes and tracks after its Recolour rc (clean_recolour). One colour (tracks None or all one):
    unchanged. First the notes are settled as their colours before would sound (render's overlap rule on each
    colour: a note cut where a later one of its colour starts on its key, notes starting together made one), so a
    new colour never frees a cut note: the notes stay as heard with Recolour off. held: sustained (overlaps kept,
    render held): not settled, its notes of one colour may still sound together."""
    if tracks is None or not len(notes):
        return notes, tracks
    t = np.asarray(tracks, np.int64)
    if len(np.unique(t % ROUND)) < 2:
        return notes, tracks
    if not held:
        notes, t = settled(notes, t)
    low = t % ROUND
    colours = np.unique(low)
    corner = (int(notes[:, 0].min()), int(notes[:, 2].min()))
    pick = dice(notes, low, rc["seed"], 1, corner)
    change = dice(notes, low, rc["seed"], 0, corner) < rc["amount"] / 100
    new = np.where(change, colours[np.minimum((pick * len(colours)).astype(np.int64), len(colours) - 1)], low)
    new = kept_apart(notes, low, new, colours, pick)
    return notes, t - low + new


def settled(notes, t):
    """notes and their tracks with each track's overlaps on a key fixed as render does (engine.resolve_overlaps,
    the track as the slot); the notes' other columns kept."""
    from notes.engine import resolve_overlaps
    vel = notes[:, 3] if notes.shape[1] > 3 else np.full(len(notes), 100)  # ((start, end, key) only: all alike)
    rows = np.column_stack([np.asarray(notes[:, :3], np.int64), np.asarray(vel, np.int64), t, np.arange(len(notes))])
    got = resolve_overlaps(rows)
    if len(got) == len(rows) and (got[:, 1] == rows[got[:, 5], 1]).all():
        return notes, t  # (nothing overlapped)
    idx = got[:, 5]
    out = np.array(notes[idx])
    out[:, 1] = got[:, 1]
    return out, t[idx]


def kept_apart(notes, old, new, colours, pick):
    """new colours with every clash put right: a note sounding on its key together with earlier ones (overlapping,
    not just touching) gets a colour none of those with ANOTHER colour before has now (its own one first, else a
    free one from its dice), or its own one when all are taken. Notes of one colour before may share (sustained).
    Only bursts of notes overlapping on a key are looked at: pairs all at once, bigger ones note by note."""
    s, e, k = (np.asarray(notes[:, c], np.int64) for c in (0, 1, 2))
    o = np.lexsort((s, k))
    ks, ss, es = k[o], s[o], e[o]
    reach = np.maximum.accumulate(es + ks * (1 << 40))  # (the latest end so far, counted per key: keys far apart)
    over = np.zeros(len(o), bool)
    over[1:] = ss[1:] + ks[1:] * (1 << 40) < reach[:-1]
    over[1:] &= ks[1:] == ks[:-1]
    if not over.any():
        return new
    new = new.copy()
    burst = np.cumsum(~over) - 1  # (each note's burst: notes on a key each overlapping one before it)
    size = np.bincount(burst)
    pair = over & (size[burst] == 2)  # the second note of a burst of two
    if pair.any():
        b = o[pair]
        a = o[np.flatnonzero(pair) - 1]
        clash = (old[a] != old[b]) & (new[a] == new[b])
        a, b = a[clash], b[clash]
        if len(b):
            n = len(colours)
            other = colours[(np.searchsorted(colours, new[a]) + 1
                             + np.minimum((pick[b] * (n - 1)).astype(np.int64), n - 2)) % n]
            new[b] = np.where(old[b] != new[a], old[b], other)
    starts = np.flatnonzero(~over)
    big = np.flatnonzero(size > 2)
    fast = loops()
    if fast and len(big):  # (the compiled walk: the same colours, files/fastloops.py)
        to = np.ascontiguousarray
        old_c, new_c = np.searchsorted(colours, old), np.searchsorted(colours, new)
        fast.recolour_apart(to(o, np.int64), to(ss), to(e), to(old_c, np.int64), new_c, to(pick, np.float64),
                            to(starts[big], np.int64), to(size[big], np.int64), len(colours))
        return colours[new_c]
    lst = colours.tolist()
    for w in big.tolist():
        sounding = []  # (end, note) of the notes still sounding in this burst
        for j in range(int(starts[w]), int(starts[w]) + int(size[w])):
            i = int(o[j])
            while sounding and sounding[0][0] <= ss[j]:  # (ending where this one starts: touching, fine)
                heapq.heappop(sounding)
            mine = int(old[i])
            taken = {int(new[x]) for _, x in sounding if int(old[x]) != mine}
            if int(new[i]) in taken:
                free = [c for c in lst if c not in taken]
                if not free or mine in free:
                    new[i] = mine
                else:
                    new[i] = free[min(int(pick[i] * len(free)), len(free) - 1)]
            heapq.heappush(sounding, (int(es[j]), i))
    return new


def first_starts(notes, tracks, starts=None):
    """{colour (track % ROUND): the tick it first starts at} of the notes before recolouring (starts: each note's
    start to count by, e.g. a gate flipped shape's unflipped ones)."""
    if tracks is None or not len(notes):
        return {}
    low = np.asarray(tracks, np.int64) % ROUND
    s = np.asarray(notes[:, 0] if starts is None else starts, np.int64)
    ids, which = np.unique(low, return_inverse=True)
    first = np.full(len(ids), np.iinfo(np.int64).max)
    np.minimum.at(first, which.reshape(-1), s)
    return dict(zip(ids.tolist(), first.tolist()))


def counted_starts(notes, tracks, firsts):
    """Each note's start as render should count it (its colour's first start before recolouring, firsts); a colour
    that had none: the note's own start."""
    low = np.asarray(tracks, np.int64) % ROUND
    own = np.asarray(notes[:, 0], np.int64)
    if not firsts:
        return own
    ids = np.array(sorted(firsts), np.int64)
    vals = np.array([firsts[i] for i in ids.tolist()], np.int64)
    at = np.clip(np.searchsorted(ids, low), 0, len(ids) - 1)
    return np.where(ids[at] == low, vals[at], own)
