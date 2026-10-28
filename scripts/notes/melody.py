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


# ---------------------------------------------------------------- the tempo made (group B)

SHAPES = ("burst", "reverse", "jump", "updown", "random")  # between two jumps: slow -> fast, fast -> slow, jump
# to High and back to the normal tempo halfway (catch-up way: held up to the catch-up), High / Low in turns, random
HOWS = ("audio", "catch")  # Keep the audio (the area's notes keep their sound time) / a catch-up after each jump
HOLD = 50.0  # %: the catch-up way, how much of each gap the show tempo holds before its catch-up starts
CATCH_SHAPES = ("flat", "ease", "rise", "ramp")  # the catch-ups' shapes offered (the pane's, without drawn / formula)


def gap_values(shape, k, lo, hi, rng):
    """(BPM leaving the jump, BPM arriving at the next one or None = held) for gap k."""
    if shape == "burst":
        return lo, hi
    if shape == "reverse":
        return hi, lo
    if shape == "updown":
        return (hi if k % 2 == 0 else lo), None
    if shape == "random":
        return lo + (hi - lo) * rng.random(), None
    return hi, None  # (jump)


def fill_clip(runs, a, b, shape, lo, hi, normal, how="audio", hold=HOLD, catch_shape="flat", rng=None,
              snap=0.0, plain=()):
    """The tempo the fill lays over beats a..b, as a Conductor pane copy (TempoPane.put_copies replaces what's in
    the area with it): {"length", "points", "catch", "spans"}, beats from a. runs: melody_runs / snap_runs. normal:
    the BPM back between runs (the tempo arriving at the area). Keep the audio: every part of a run Keep timing (no
    heard line: the tempo heard before the run). Catch-up: each gap = the show tempo up to hold % of it, then a
    catch-up (its yellow area from the jump, so back-to-back catch-ups all pay back to the tempo after the run:
    every jump on time). plain: gaps (run, k) left at the normal tempo (their catch-up can't be done). snap: BPM
    snap for random values."""
    import random
    from notes.tempo import BEND, catch_set, clamp_bpm
    rng = rng or random.Random()
    keep = how == "audio"
    pts, cs, gaps = [], [], []  # (gaps: each catch-up's (run, gap))

    heard = [None]  # (Keep the audio: the run's own heard tempo line = the normal tempo all along)

    def point(x, bpm, curve="hold", kept=keep):
        if snap and curve == "hold":
            bpm = max(snap, round(bpm / snap) * snap)
        p = [x - a, clamp_bpm(bpm), curve, "", "exp" if curve == "line" else "lin", kept, list(BEND)]
        if kept:
            p.append({"line": [list(q) for q in heard[0]]})
        pts.append(p)

    for r, (xs, end) in enumerate(runs):
        xs = [float(x) for x in xs]
        # (heard at the normal tempo by its own line, not "the tempo heard before": a run from the song's start
        # was heard at the project's first BPM there, not the line's)
        heard[0] = [[xs[0] - a, clamp_bpm(normal), "hold", "", "lin", False, list(BEND)]]
        for k, x in enumerate(xs):
            nxt = xs[k + 1] if k + 1 < len(xs) else end
            if nxt <= x + 1e-9:
                continue
            if (r, k) in plain:
                point(x, normal, kept=False)
                continue
            v0, v1 = gap_values(shape, k, lo, hi, rng)
            stop = nxt if keep else x + (nxt - x) * min(max(hold, 1.0), 99.0) / 100.0
            if shape == "jump" and keep:
                point(x, v0)
                point(x + (nxt - x) / 2, normal)
            elif v1 is None:
                point(x, v0)
            else:
                point(x, v0, "line")
                point(stop, v1)  # (arriving: the next jump / the catch-up line leaves from the same beat)
            if not keep:
                item = [stop - a, catch_shape, ""]
                catch_set(item, 3, x - a)  # (its yellow area from the jump: back-to-back stretches chain)
                cs.append(item)
                gaps.append((r, k))
        point(end, normal, kept=False)  # (the run's end: back to the normal tempo)
    pts.sort(key=lambda p: p[0])  # (stable: an arriving point stays before the one leaving the same beat)
    return {"length": b - a, "points": pts, "catch": cs, "spans": [(0.0, b - a)], "gaps": gaps}
