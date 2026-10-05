"""Chop: every note of a shape cut into pieces by a rhythm, like the chopper of a well-known piano roll (measured from
its output; rhythm only, the keys never change). The shape stays as drawn; sh["chop"] = the settings:

"len": one step of the rhythm, in beats (the "snap" text it was picked as is kept for the window; "off" = 1 tick).
"steps" + "pieces": the rhythm, one repeat "steps" steps long, its pieces [start, length, velocity] in steps.
The rhythm REPEATS along each note (it isn't stretched to fit), from the note's own start (a Fill shape's notes
touching on a key count as one: "runs", run_starts), or with "abs" from the song's start (tick 0), so the first
piece can be cut short. A rest in the rhythm = a hole in the note; a piece running past the note's end is cut there.
A piece's velocity: a % of its note's own (1..200, the default, like the other piano roll), or with "fixed" the
velocity itself (1..127, our own). "vel" (0..100): how much the pieces' velocities count (0 = every piece keeps the
note's own velocity, 100 = the rhythm's fully). "name": the rhythm it was picked as ("" = drawn).

Ctrl+U (quick chop) = the "even" rhythm at the snap's length (100 %: velocities never change)."""

import math

import numpy as np

# our own rhythms (not taken from any other program): id -> (steps, pieces)
RHYTHMS = {
    "even": (1, [[0, 1, 100]]),
    "half": (1, [[0, 0.5, 100]]),
    "swing": (2, [[0, 4 / 3, 100], [4 / 3, 2 / 3, 75]]),
    "offbeat": (2, [[1, 1, 100]]),
    "gallop": (2, [[0, 1, 100], [1, 0.5, 80], [1.5, 0.5, 80]]),
    "triplets": (1, [[0, 1 / 3, 100], [1 / 3, 1 / 3, 80], [2 / 3, 1 / 3, 80]]),
    "accent": (4, [[0, 1, 100], [1, 1, 65], [2, 1, 65], [3, 1, 65]]),
    "stutter": (4, [[0, 0.5, 100], [0.5, 0.5, 80], [1, 0.5, 100], [1.5, 0.5, 80], [2, 2, 100]]),
}
MAX_STEPS = 64
MAX_PIECES = 256
MAX_VEL = 200  # % (a piece can be louder than its note, up to 127)
TOO_MANY = 2 * 10 ** 7  # pieces to work out: past that the notes stay unchopped (20 M notes: ~6 GB in all, measured)
BATCH = 10 ** 6  # pieces worked out at once (apply_chop)
CHOP_DEFAULTS = {"on": True, "name": "even", "steps": 1, "pieces": [[0, 1, 100]], "len": 0.25, "snap": "1/16",
                 "abs": False, "vel": 100.0, "fixed": False}


def top(fixed):
    """The highest velocity a piece can have: 127 (fixed) or MAX_VEL %."""
    return 127 if fixed else MAX_VEL


def switched(pieces, fixed):
    """Pieces turned to fixed velocities (fixed) or back to %: 100 % = 127, about as loud (capped at 127)."""
    k = 1.27 if fixed else 1 / 1.27
    return [[s, n, max(1.0, min(float(top(fixed)), round(v * k)))] for s, n, v in pieces]


def rhythm(name):
    """A built-in rhythm's (steps, pieces), copied."""
    steps, pieces = RHYTHMS[name]
    return steps, [list(p) for p in pieces]


def clean_pieces(pieces, steps, fixed=False):
    """Pieces from a file / the window -> valid ones inside one repeat, sorted, at most MAX_PIECES."""
    out = []
    for p in pieces if isinstance(pieces, list) else ():
        try:
            s, n, v = (float(x) for x in p)
        except (TypeError, ValueError):
            continue
        if not all(map(math.isfinite, (s, n, v))):
            continue
        s, e = max(0.0, s), min(float(steps), s + n)
        if e - s > 1e-9:
            out.append([s, e - s, max(1.0, min(float(top(fixed)), v))])
    return sorted(out)[:MAX_PIECES]


def clean_chop(c):
    """A chop from a file -> valid chop, or None when there is none (switched off, or no pieces)."""
    if not isinstance(c, dict) or c.get("on") is False:
        return None
    out = dict(CHOP_DEFAULTS)
    try:
        out["steps"] = max(1, min(MAX_STEPS, int(c.get("steps", 1))))
        ln = float(c.get("len", out["len"]))
        out["len"] = ln if math.isfinite(ln) and ln > 0 else out["len"]
        v = float(c.get("vel", 100.0))
        out["vel"] = max(0.0, min(100.0, v)) if math.isfinite(v) else 100.0
    except (TypeError, ValueError, OverflowError):
        pass
    out["len"] = min(out["len"], 10 ** 4)
    out["fixed"] = c.get("fixed") is True
    out["pieces"] = clean_pieces(c.get("pieces", out["pieces"]), out["steps"], out["fixed"])
    if not out["pieces"]:
        return None
    out["snap"] = c["snap"] if isinstance(c.get("snap"), str) else ""
    out["name"] = c["name"] if isinstance(c.get("name"), str) else ""
    out["abs"] = c.get("abs") is True
    del out["on"]
    return out


def run_starts(a):
    """Where each note's run starts: notes on one key that touch or overlap count as one long note, so a fill's
    1-tick outline notes and the fill note after them follow one rhythm."""
    order = np.lexsort((a[:, 1], a[:, 0], a[:, 2]))
    s, e, p = a[order, 0], a[order, 1], a[order, 2]
    seg = np.ones(len(order), bool)
    seg[1:] = p[1:] != p[:-1]
    seg_id = np.cumsum(seg) - 1
    t0 = int(s.min())
    span = int(e.max()) - t0 + 2
    # the latest end so far on each key (keys one after another, each lifted above the ones before)
    reach = np.maximum.accumulate(e - t0 + seg_id * span) - seg_id * span + t0
    new = seg.copy()
    new[1:] |= s[1:] > reach[:-1]  # a gap: a new run
    out = np.empty(len(a), np.int64)
    out[order] = s[np.maximum.accumulate(np.where(new, np.arange(len(order)), 0))]
    return out


def step_ticks(chop, ppq):
    """One step of the rhythm in ticks (at least 1): snap "off" = 1 tick at any PPQ (its "len" was worked out at the
    PPQ of the time)."""
    return 1.0 if chop.get("snap") == "off" else max(chop["len"] * ppq, 1.0)


def _repeats(a, chop, ppq):
    """The rhythm's pieces (array), their starts / ends in ticks, one repeat's length, where each note's repeats
    count from, the first repeat that can reach each note and how many do."""
    step = step_ticks(chop, ppq)
    cycle = chop["steps"] * step
    pieces = np.array(chop["pieces"], float)
    ps, pe = pieces[:, 0] * step, (pieces[:, 0] + pieces[:, 1]) * step
    s, e = a[:, 0].astype(float), a[:, 1].astype(float)
    if chop.get("origins") is not None:  # (a sliced Fill piece: the runs as they started in the whole, sliced.py)
        origin = np.asarray(chop["origins"], float)
    else:
        origin = np.zeros(len(a)) if chop["abs"] else run_starts(a).astype(float) if chop.get("runs") else s
    k0 = np.floor((s - origin - pe.max()) / cycle).astype(np.int64)  # the first repeat that can reach the note
    k1 = np.ceil((e - origin - ps.min()) / cycle).astype(np.int64)  # (past the last one that can)
    return pieces, ps, pe, cycle, origin, k0, np.maximum(k1 - k0, 0)


def too_many(a, chop, ppq):
    """Would chopping these notes make so many pieces that they're left unchopped?"""
    if not len(a) or not chop:
        return False
    pieces, *_, reps = _repeats(a, chop, ppq)
    return reps.sum() * len(pieces) > TOO_MANY


def apply_chop(a, chop, ppq):
    """a: notes as an int64 array (start, end, pitch, velocity, then any columns riding along) -> the pieces, note by
    note in order."""
    if not len(a) or not chop:
        return a
    pieces, ps, pe, cycle, origin, k0, reps = _repeats(a, chop, ppq)
    ends = np.cumsum(reps * len(pieces))  # (pieces to work out up to each note)
    if not len(ends) or ends[-1] > TOO_MANY:
        return a  # (left unchopped: the window says why)
    out = np.empty((int(ends[-1]), a.shape[1]), np.int64)
    n = start = 0
    while start < len(a):  # (in batches of notes, so the working out takes little memory besides the pieces)
        done = ends[start - 1] if start else 0
        stop = max(start + 1, int(np.searchsorted(ends, done + BATCH, side="right")))
        part = slice(start, stop)
        got = _pieces(a[part], chop, pieces, ps, pe, cycle, origin[part], k0[part], reps[part])
        out[n:n + len(got)] = got
        n, start = n + len(got), stop
    return out[:n]


def _pieces(a, chop, pieces, ps, pe, cycle, origin, k0, reps):
    """apply_chop for some of the notes."""
    s, e = a[:, 0].astype(float), a[:, 1].astype(float)
    nk = np.repeat(np.arange(len(a)), reps)  # one row per note and repeat of the rhythm
    k = k0[nk] + (np.arange(len(nk)) - np.repeat(np.cumsum(reps) - reps, reps))
    base = np.repeat(origin[nk] + k * cycle, len(pieces))
    note = np.repeat(nk, len(pieces))
    which = np.tile(np.arange(len(pieces)), len(nk))
    lo = np.rint(np.maximum(base + ps[which], s[note])).astype(np.int64)
    hi = np.rint(np.minimum(base + pe[which], e[note])).astype(np.int64)
    keep = hi > lo
    out = a[note[keep]].copy()
    out[:, 0], out[:, 1] = lo[keep], hi[keep]
    amt = chop["vel"] / 100
    if amt and chop.get("fixed"):  # (the drawn velocity itself, mixed with the note's own by amt)
        out[:, 3] = np.clip(np.rint((1 - amt) * out[:, 3] + amt * pieces[which[keep], 2]), 1, 127).astype(np.int64)
    elif amt:
        share = 1 - amt + amt * pieces[which[keep], 2] / 100
        out[:, 3] = np.clip(np.rint(out[:, 3] * share), 1, 127).astype(np.int64)
    return out
