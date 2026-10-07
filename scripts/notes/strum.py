"""Strum: the notes of each chord (notes starting on the same tick) start one after another, like a hand strumming
strings, the way the strum tool of a well-known piano roll does it (measured from its output). The shape stays as
drawn; a page's settings (fx.py) = STRUM_DEFAULTS' keys.

One rule for start times, end times and velocities: one note of the chord stays as it is (the anchor), the k-th
note away from it changes by g + g*r + ... + g*r^(k-1), g = the strength, r from the tension t (-100 .. 100):
1 + t/100 when t >= 0, 1 + t/200 below (100 = each step twice the last, -100 = half).
Start ("start" on): "time" beats per note; + = the lowest note first (it stays, the others start later), - = the
highest first. Whole notes move, unless "preserve" (the ends stay). "ahead": the tension works the other way round
and the strum is moved earlier, so its last note sits on the chord's start (a note it would push before tick 0
starts at 0, its length kept). No note moves more than CAP beats. "vel": velocity taken off per note,
+ = quieter after the first note struck, - = quieter before the last one (never under 1).
End ("end" on): "end_time" beats: the ends come earlier; + = the last note struck keeps its end, - = the first.
"chop": every note is first cut wherever another note starts while it's held, so those pieces strum too.
"alternate": every other chord strums the other way (single notes don't count, and never change).
A note left with no length keeps 1 tick at its start."""

import math

import numpy as np

TIME_KNOB, END_KNOB, VEL_KNOB = 1 / 3, 1 / 8, 127  # how far the knobs go (beats, beats, velocity)
LIMITS = {"time": (-4.0, 4.0), "tension": (-100.0, 100.0), "vel": (-127.0, 127.0), "vel_tension": (-100.0, 100.0),
          "end_time": (-4.0, 4.0), "end_tension": (-100.0, 100.0)}  # (typed numbers can go past the knobs)
FLAGS = ("start", "preserve", "ahead", "end", "chop", "alternate")
STRUM_DEFAULTS = dict({k: 0.0 for k in LIMITS}, start=True, preserve=False, ahead=False, end=True, chop=False,
                      alternate=False)
CAP = 64  # beats: no note moves further from its chord (a steep tension over a big chord would go on forever, user)
CHOP_MAX = 5_000_000  # pieces: more than this and the notes aren't chopped


def clean_strum(c):
    """A strum from a file -> valid strum, or None when there is none or it would change nothing."""
    if not isinstance(c, dict):
        return None
    out = dict(STRUM_DEFAULTS)
    for key, (lo, hi) in LIMITS.items():
        try:
            v = float(c.get(key, 0.0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out[key] = max(lo, min(hi, v))
    for key in FLAGS:
        if isinstance(c.get(key), bool):
            out[key] = c[key]
    if not (out["start"] and (out["time"] or out["vel"]) or out["end"] and out["end_time"]):
        return None
    return out


def steps(k, g, tension, cap):
    """How far the note k steps from the anchor changes: g + g*r + ... (k of them), never past cap."""
    r = 1 + tension / 100 if tension >= 0 else 1 + tension / 200
    k = np.asarray(k, float)
    if r == 1:
        return np.minimum(g * k, cap)
    with np.errstate(over="ignore"):
        return np.minimum(g * (np.power(r, k) - 1) / (r - 1), cap)


def chop(a):
    """Every note cut where another note starts while it's held (start and end ticks in the first two columns)."""
    s, e = a[:, 0], a[:, 1]
    cuts = np.unique(s)
    lo = np.searchsorted(cuts, s, "right")  # the cuts inside a note: cuts[lo:hi]
    n = np.maximum(np.searchsorted(cuts, e, "left") - lo, 0)
    if not n.any() or n.sum() > CHOP_MAX:
        return a
    rep = np.repeat(np.arange(len(a)), n + 1)
    j = np.arange(len(rep)) - np.repeat(np.cumsum(n + 1) - (n + 1), n + 1)  # which piece of its note
    at, last = lo[rep] + j, len(cuts) - 1
    out = a[rep]
    out[:, 0] = np.where(j == 0, s[rep], cuts[np.minimum(at - 1, last)])
    out[:, 1] = np.where(j == n[rep], e[rep], cuts[np.minimum(at, last)])
    return out


def apply_strum(a, st, ppq):
    """a: notes as an int64 array of (start, end, key, velocity, ...) rows (other columns ride along) -> the notes
    strummed."""
    if not len(a) or not st:
        return a
    if st["chop"]:
        a = chop(a)
    a = a[np.lexsort((a[:, 2], a[:, 0]))]  # (a copy) chords in time order, each low to high
    s, e = a[:, 0], a[:, 1]
    new = np.ones(len(a), bool)
    new[1:] = s[1:] != s[:-1]
    firsts = np.flatnonzero(new)
    size = np.diff(np.append(firsts, len(a)))
    chord = np.cumsum(new) - 1
    m = size[chord]
    rank = np.arange(len(a)) - firsts[chord]
    start = st["start"]
    down = np.full(len(firsts), start and st["time"] < 0)  # per chord: the highest note struck first
    if st["alternate"]:
        multi = size > 1
        down ^= multi & (np.cumsum(multi) % 2 == 0)  # (the 2nd, 4th... real chord)
    q = np.where(down[chord], m - 1 - rank, rank)  # place in the strum (0 = struck first)
    ns, ne = s.astype(float), e.astype(float)
    cap = CAP * ppq
    if start and st["time"]:
        g = abs(st["time"]) * ppq
        if st["ahead"]:
            off = steps(q, g, -st["tension"], cap) - steps(m - 1, g, -st["tension"], cap)
        else:
            off = steps(q, g, st["tension"], cap)
        off = np.rint(off)
        late = np.maximum(0, -(ns + off))  # (pushed before tick 0: the note stops at 0, keeping its length)
        ns = ns + off + late
        if not st["preserve"]:
            ne = ne + off + late
    if st["end"] and st["end_time"]:
        k = m - 1 - q if st["end_time"] > 0 else q
        ne = ne - np.rint(steps(k, abs(st["end_time"]) * ppq, st["end_tension"], cap))
    if start and st["vel"]:
        k = q if st["vel"] > 0 else m - 1 - q
        a[:, 3] = np.maximum(1, a[:, 3] - np.rint(steps(k, abs(st["vel"]), st["vel_tension"], VEL_KNOB))).astype(np.int64)
    ns = np.maximum(ns, 0)
    a[:, 0] = ns.astype(np.int64)
    a[:, 1] = np.maximum(ne, ns + 1).astype(np.int64)
    return a
