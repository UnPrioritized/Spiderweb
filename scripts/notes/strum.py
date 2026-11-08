"""Strum: the notes of each chord (notes starting on the same tick) start one after another, like a hand strumming
strings, the way the strum tool of a well-known piano roll does it (measured from its output). The shape stays as
drawn; a page's settings (fx.py) = STRUM_DEFAULTS' keys.

One rule for start times, end times and velocities: one note of the chord stays as it is (the anchor), the k-th
note away from it changes by g + g*r + ... + g*r^(k-1), g = the strength, r from the tension t (-100 .. 100):
1 + t/100 when t >= 0, 1 + t/200 below (100 = each step twice the last, -100 = half).
Start ("start" on): "time" beats per note; + = the lowest note first (it stays, the others start later), - = the
highest first. Whole notes move, unless "preserve" (the ends stay). "ahead": the tension works the other way round
and the strum is moved earlier, so its last note sits on the chord's start (a note it would push before tick 0
starts at 0, its length kept). No note moves more than CAP beats (the ones that would keep their strum order there,
a tick apart). "vel": velocity taken off per note,
+ = quieter after the first note struck, - = quieter before the last one (never under 1).
End ("end" on): "end_time" beats: the ends come earlier; + = the last note struck keeps its end, - = the first.
"chop": every note is first cut wherever another note starts while it's held, so those pieces strum too.
"alternate": every other chord strums the other way (single notes don't count, and never change).
A note left with no length keeps 1 tick at its start.
"keys" .. "keys_to": how many notes of a chord share each step (start, end and velocity alike), counted from the
note struck first. One number (keys_to = keys): groups of that many (1 = every note its own step). A range: the group
sizes change evenly along the chord, from keys at the note struck first towards keys_to at the last (smaller
keys_to = shrinking): a step beginning at place x (0 = struck first) holds
round(keys + (keys_to - keys) * min(1, x / (m - keys_to))) notes, m = the chord's notes (so a step beginning keys_to
from the end holds keys_to); the last step holds what is left (as with one number).
"edges": "off" = as above; "cut" / "wrap": the shape's own time (its first start .. its last end, before the strum)
is a block. "cut" drops what the strum moves past its edges (a note across an edge keeps its part inside); "wrap"
brings it back in at the other end (a note across an edge is cut in two; one as long as the block fills it). Inside
a block, tick 0 doesn't stop a note: the block's edges do.
"block" (beats, only with cut / wrap; none = the whole shape, user 2026-11-08): the shape's time is cut into blocks
that long, counted from its first start (the last one shorter when it doesn't fit), and the edges work inside each
block on its own: a note across a block's edge is cut in two first, each note stays in the block it started in."""

import functools
import math

import numpy as np

TIME_KNOB, END_KNOB, VEL_KNOB = 1 / 3, 1 / 8, 127  # how far the knobs go (beats, beats, velocity)
LIMITS = {"time": (-4.0, 4.0), "tension": (-100.0, 100.0), "vel": (-127.0, 127.0), "vel_tension": (-100.0, 100.0),
          "end_time": (-4.0, 4.0), "end_tension": (-100.0, 100.0)}  # (typed numbers can go past the knobs)
FLAGS = ("start", "preserve", "ahead", "end", "chop", "alternate")
KEYS_MAX = 100_000  # (notes per step: more than any chord holds)
STRUM_DEFAULTS = dict({k: 0.0 for k in LIMITS}, start=True, preserve=False, ahead=False, end=True, chop=False,
                      alternate=False, keys=1, keys_to=1, edges="off")
EDGES = ("off", "cut", "wrap")
# beats: no note moves further from its chord (a steep tension over a big chord would go on forever, user; 1000 bars
# of 4/4, user 2026-10-17: FL's own goes on until it breaks past ~1300 bars, leaving the rest of the chord behind)
CAP = 4000
CHOP_MAX = 5_000_000  # pieces: more than this and the notes aren't chopped
BLOCK_MOST = 100_000.0  # beats: the longest block (longer = the whole shape anyway)


def whole(v, default):
    """A notes-per-step count from a file: 1 .. KEYS_MAX, else default."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return max(1, min(KEYS_MAX, int(v)))
    return default


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
    out["keys"] = whole(c.get("keys"), 1)
    out["keys_to"] = whole(c.get("keys_to"), out["keys"])  # (none: one number)
    if c.get("edges") in EDGES:
        out["edges"] = c["edges"]
    b = c.get("block")  # (saved only when set: old files and the whole shape unchanged)
    if isinstance(b, (int, float)) and not isinstance(b, bool) and math.isfinite(b) and 0 < b <= BLOCK_MOST:
        out["block"] = float(b)
    if not (out["start"] and (out["time"] or out["vel"]) or out["end"] and out["end_time"]):
        return None
    return out


def steps(k, g, tension, cap, kept=False):
    """How far the note k steps from the anchor changes: g + g*r + ... (k of them), never past cap. kept: the notes
    past cap keep their order there, one more each (ticks: one tick apart, not all on one spot)."""
    r = 1 + tension / 100 if tension >= 0 else 1 + tension / 200
    k = np.asarray(k, float)
    with np.errstate(over="ignore", invalid="ignore"):
        out = g * k if r == 1 else g * (np.power(r, k) - 1) / (r - 1)
    over = ~(out < cap)  # (an overflow is past it too)
    if not kept or not over.any():
        return np.where(over, cap, out)
    first = np.flatnonzero(~(steps(np.arange(int(k.max()) + 1), g, tension, cap) < cap))[0]  # (the first step past
    return np.where(over, cap + (k - first), out)  # cap: the same for every chord, steps only grow with k)


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


def groups(q, m, st):
    """The step of the note q places from the one struck first, in a chord of m notes ("keys" .. "keys_to" notes a
    step: the docstring at the top)."""
    a, b = st.get("keys", 1), st.get("keys_to", st.get("keys", 1))
    q = np.asarray(q)
    if a == b:
        return q // a
    sizes, which = np.unique(np.broadcast_to(m, q.shape), return_inverse=True)
    tables = [step_of(a, b, int(size)) for size in sizes]  # (each chord size's steps worked out once)
    at = np.cumsum([0] + [len(t) for t in tables[:-1]])
    return np.concatenate(tables)[at[which.reshape(q.shape)] + q]


@functools.lru_cache(maxsize=1024)
def step_of(a, b, m):
    """Each place's step in a chord of m notes (a keys range)."""
    starts = range_starts(a, b, m)
    return np.repeat(np.arange(len(starts)), np.diff(np.append(starts, m)))


@functools.lru_cache(maxsize=256)
def range_starts(a, b, m):
    """Where each step of a keys range begins in a chord of m notes (places from the note struck first)."""
    starts, x = [], 0
    while x < m:
        starts.append(x)
        x += max(1, round(a + (b - a) * min(1.0, x / max(m - b, 1))))  # (the last whole step: keys_to)
    return np.array(starts, np.int64)


def apply_strum(a, st, ppq):
    """a: notes as an int64 array of (start, end, key, velocity, ...) rows (other columns ride along) -> the notes
    strummed."""
    if not len(a) or not st:
        return a
    edges = st.get("edges", "off")
    block = (int(a[:, 0].min()), int(a[:, 1].max())) if edges != "off" else None  # (the shape's own time)
    blen = max(1, round(st["block"] * ppq)) if block and st.get("block") else 0  # (blocks inside it: ticks)
    if blen and blen >= block[1] - block[0]:
        blen = 0  # (one block holds it all: the whole shape)
    if blen:
        a = split_blocks(a, block[0], blen)
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
    q, last = groups(q, m, st), groups(m - 1, m, st)  # (its step, the chord's last step)
    if blen:  # (each note's own block, by where it starts: after the split none crosses an edge)
        lo = block[0] + (s - block[0]) // blen * blen
        bounds = lo, np.minimum(lo + blen, block[1])
    elif block:
        bounds = block
    ns, ne = s.astype(float), e.astype(float)
    cap = CAP * ppq
    if start and st["time"]:
        g = abs(st["time"]) * ppq
        if st["ahead"]:
            off = steps(q, g, -st["tension"], cap, True) - steps(last, g, -st["tension"], cap, True)
        else:
            off = steps(q, g, st["tension"], cap, True)
        off = np.rint(off)
        late = np.maximum(0, -(ns + off)) if block is None else 0  # (pushed before tick 0: the note stops at 0,
        # keeping its length; inside a block: cut / wrap at its edges instead)
        ns = ns + off + late
        if not st["preserve"]:
            ne = ne + off + late
    if st["end"] and st["end_time"]:
        k = last - q if st["end_time"] > 0 else q
        ne = ne - np.rint(steps(k, abs(st["end_time"]) * ppq, st["end_tension"], cap, True))
    if start and st["vel"]:
        k = q if st["vel"] > 0 else last - q
        a[:, 3] = np.maximum(1, a[:, 3] - np.rint(steps(k, abs(st["vel"]), st["vel_tension"], VEL_KNOB))).astype(np.int64)
    if block is None:
        ns = np.maximum(ns, 0)
    a[:, 0] = ns.astype(np.int64)
    a[:, 1] = np.maximum(ne, ns + 1).astype(np.int64)
    if block is None:
        return a
    return wrapped(a, *bounds, st.get("rounds", False)) if edges == "wrap" else cut(a, *bounds)


def split_blocks(a, lo, size):
    """Every note cut in two (or more) where it crosses a block's edge (lo + k x size, k >= 1)."""
    s, e = a[:, 0], a[:, 1]
    k0 = (s - lo) // size + 1  # (the first edge after the start)
    n = np.maximum(-((lo - e) // size) - k0, 0)  # (edges strictly inside: k0 .. ceil((e - lo) / size) - 1)
    if not n.any():
        return a
    rep = np.repeat(np.arange(len(a)), n + 1)
    j = np.arange(len(rep)) - np.repeat(np.cumsum(n + 1) - (n + 1), n + 1)  # (which piece of its note)
    out = a[rep]
    edge = lo + (k0[rep] + j) * size
    out[:, 0] = np.where(j == 0, s[rep], edge - size)
    out[:, 1] = np.where(j == n[rep], e[rep], edge)
    return out


def cut(a, lo, hi):
    """The notes kept inside lo .. hi (a note across an edge keeps its part inside; lo / hi: one block, or each note's
    own)."""
    keep = (a[:, 1] > lo) & (a[:, 0] < hi)
    lo, hi = (x[keep] if np.ndim(x) else x for x in (lo, hi))
    a = a[keep]
    a[:, 0] = np.maximum(a[:, 0], lo)
    a[:, 1] = np.minimum(a[:, 1], hi)
    return a


def wrapped(a, lo, hi, rounds=False):
    """The notes brought back inside lo .. hi (one block, or each note's own): what is past one edge comes in at the
    other (a note across an edge is cut in two; one as long as the block or longer fills it). rounds ("Repeated
    colours get new channels", the last column = colour + ROUND x its round, custom.ROUND): as if the block went on
    forever, a note brought in from j blocks later is j x (the block's rounds) rounds later; its colour stays."""
    lo, hi = (np.broadcast_to(np.asarray(x, np.int64), (len(a),)) for x in (lo, hi))
    span = hi - lo
    length = a[:, 1] - a[:, 0]
    trips = (a[:, 0] - lo) // span  # (blocks away: -1 = came from before the start)
    s = a[:, 0] - trips * span
    full = length >= span
    s = np.where(full, lo, s)
    e = np.where(full, hi, s + length)
    over = e > hi  # (across the end: the rest comes in at the start, from the block before)
    out = np.concatenate([a, a[over]])
    out[:len(a), 0], out[:len(a), 1] = s, np.minimum(e, hi)
    out[len(a):, 0], out[len(a):, 1] = lo[over], lo[over] + (e[over] - hi[over])
    if rounds and len(a):
        from notes.custom import ROUND
        lap = a[:, -1] // ROUND
        _, which = np.unique(lo, return_inverse=True)  # (each block's own rounds)
        most, least = np.full(which.max() + 1, -(1 << 62)), np.full(which.max() + 1, 1 << 62)
        np.maximum.at(most, which, lap)
        np.minimum.at(least, which, lap)
        laps = (most - least + 1)[which]  # (the rounds one block holds)
        later = np.concatenate([-trips, -trips[over] - 1])
        out[:, -1] += later * np.concatenate([laps, laps[over]]) * ROUND
    return out
