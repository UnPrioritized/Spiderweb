"""Tempo jumps that follow the melody (PLAN.md, planned feature #12): where the jumps go in an area of the Conductor
pane. Melody notes = the highlighted track's (or only its selected ones); each note start is an ANCHOR (notes starting
together count once). Notes shorter than "shortest" are skipped (fast runs / trills would make wild jumps); a rest
longer than "rest" ends a RUN (the tempo goes back to normal there). Every snap step = anchors on the grid instead,
no melody needed (also with All tracks). All in beats."""

import numpy as np

SHORTEST = 0.125  # beats: "Skip notes shorter than" to start with (1/32)
REST = 1.0  # beats: "Back to the normal tempo after a rest longer than" to start with
MOST_ANCHORS = 1_000_000  # more jumps than this in one area = refused (each is a tempo point or more)


def melody_runs(starts, ends, a, b, shortest=SHORTEST, rest=REST):
    """[(anchors: sorted unique start beats, run end beat)] of the notes starting in a..b (beats; starts / ends
    arrays), notes shorter than shortest left out; a new run where the time from the latest end so far to the next
    start is more than rest. A run ends where its last note ends (at most b)."""
    starts, ends = np.asarray(starts, float), np.asarray(ends, float)
    keep = (starts >= a) & (starts < b) & (ends - starts >= shortest - 1e-9)
    s, e = starts[keep], ends[keep]
    if not len(s):
        return []
    o = np.lexsort((e, s))
    s, e = s[o], e[o]
    reach = np.maximum.accumulate(e)
    cut = np.flatnonzero(s[1:] - reach[:-1] > rest + 1e-9) + 1  # (the first note of each new run)
    out = []
    for lo, hi in zip(np.r_[0, cut], np.r_[cut, len(s)]):
        out.append((np.unique(s[lo:hi]), min(float(reach[hi - 1]), b)))
    return out


def snap_runs(step, a, b):
    """Every snap step: one run with an anchor on each grid line (from beat 0) in a..b, ending at b."""
    if not step or step <= 0 or b <= a:
        return []
    first = np.ceil(a / step - 1e-9)
    n = int(np.floor((b - 1e-9) / step) - first) + 1
    if n <= 0:
        return []
    if n > MOST_ANCHORS:
        n = MOST_ANCHORS + 1  # (counted as too many: never made)
    return [((first + np.arange(n)) * step, float(b))]


def anchor_count(runs):
    return sum(len(at) for at, _ in runs)
