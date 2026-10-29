"""Tempo jumps that follow the melody (PLAN.md, planned feature #12): where the jumps go in an area of the Conductor
pane. Melody notes = the highlighted track's (or only its selected ones); each note start is an ANCHOR (notes starting
together count once). Notes shorter than "shortest" are skipped (fast runs / trills would make wild jumps); a rest
longer than "rest" ends a RUN (the tempo goes back to normal there). Every snap step = anchors on the grid instead,
no melody needed (also with All tracks). All in beats."""

import bisect

import numpy as np

SHORTEST = 0.125  # beats: "Skip notes shorter than" to start with (1/32)
REST = 1.0  # beats: "Back to the normal tempo after a rest longer than" to start with
MOST_ANCHORS = 100_000  # more jumps than this in one area = refused (each is a tempo point or more; a million froze
# for minutes, hunt 2026-10-29)
CATCH_LEAST = 4  # ticks: the shortest gap a catch-up can be fitted into (1-2 ticks never fit, hunt 2026-10-29)


def melody_runs(starts, ends, a, b, shortest=SHORTEST, rest=REST, every=1):
    """[(anchors: sorted unique start beats, run end beat)] of the notes starting in a..b (beats; starts / ends
    arrays), notes shorter than shortest left out; a new run where the time from the latest end so far to the next
    start is more than rest. A run ends where its last note ends (at most b). every: only every n-th anchor of each
    run jumps, its first one too (user 2026-10-29: "Jump every [2] notes", counted again after each rest)."""
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
    every = max(1, int(every))
    for lo, hi in zip(np.r_[0, cut], np.r_[cut, len(s)]):
        out.append((np.unique(s[lo:hi])[::every], min(float(reach[hi - 1]), b)))
    return out


def top_notes(starts, ends, keys):
    """Which notes are the TOP NOTE at their start (user 2026-10-29, "Top note of all tracks"): no higher note is
    sounding then (started at or before it, not ended yet), so of a chord only its highest note counts. Worked out
    one key at a time from the top: each key's notes joined into stretches, the lower notes' starts looked up in
    them (300k notes: one pass per key used)."""
    s, e, k = np.asarray(starts, float), np.asarray(ends, float), np.asarray(keys, np.int64)
    top = np.ones(len(s), bool)
    if len(s) < 2:
        return top
    for key in np.unique(k)[::-1]:
        mine = k == key
        lower = np.flatnonzero((k < key) & top)
        if not len(lower):
            break
        ms, me = s[mine], e[mine]
        o = np.argsort(ms, kind="stable")
        ms, me = ms[o], np.maximum.accumulate(me[o])  # (furthest reach so far: overlapping notes as one stretch)
        q = s[lower]
        j = np.searchsorted(ms, q, "right") - 1  # (the last of this key's notes starting at or before it)
        covered = (j >= 0) & (me[np.maximum(j, 0)] > q)
        top[lower[covered]] = False
    return top


def anchor_keys(starts, ends, keys, runs, shortest=SHORTEST):
    """[keys array per run]: the highest key starting on each anchor (a chord's top note) of melody_runs' runs,
    notes shorter than shortest left out as there."""
    s, k = np.asarray(starts, float), np.asarray(keys, np.int64)
    ok = np.asarray(ends, float) - s >= shortest - 1e-9
    s, k = s[ok], k[ok]
    o = np.lexsort((-k, s))  # (by start, highest key first)
    s, k = s[o], k[o]
    out = []
    for xs, _ in runs:
        j = np.searchsorted(s, np.asarray(xs, float), "left")
        out.append(k[np.minimum(j, len(k) - 1)] if len(k) else np.zeros(len(xs), np.int64))
    return out


def pitch_sizes(run_keys, lowest):
    """[shares per run] for "Higher notes jump further" (user 2026-10-29): the area's highest anchor note jumps the
    whole way (1), its lowest lowest (0..1), the ones between straight by key; all on one key = whole jumps."""
    every = np.concatenate([np.asarray(r, float) for r in run_keys]) if run_keys else np.zeros(0)
    if not len(every) or every.max() == every.min():
        return [np.ones(len(r)) for r in run_keys]
    k0, k1 = every.min(), every.max()
    return [lowest + (1.0 - lowest) * (np.asarray(r, float) - k0) / (k1 - k0) for r in run_keys]


def snap_runs(step, a, b, ppq):
    """Every step (whole ticks, at least 1): one run with an anchor every step FROM THE AREA'S START (user
    2026-10-29: counted from beat 0, a 490-tick step started part way in) up to b, ending at b. Worked out in ticks
    (in beats some landed a tick off: hunt 2026-10-29)."""
    step = int(step)
    t0, t1 = round(a * ppq), round(b * ppq)
    if step < 1 or t1 <= t0:
        return []
    n = (t1 - t0 - 1) // step + 1
    if n > MOST_ANCHORS:
        n = MOST_ANCHORS + 1  # (counted as too many: never made)
    return [((t0 + np.arange(n, dtype=np.int64) * step) / ppq, float(b))]


def step_runs(step0, step1, pts, scale, a, b, ppq):
    """Every step changing over the area (user 2026-10-29): one run from a, each next jump one step later, the step
    read from the graph pts (graph_at) at the jump before: height 0 = step0, 1 = step1 (whole ticks); scale "lin" =
    the length straight between them, "exp" = on the doubling scale (each halving takes the same room). Steps
    rounded to whole ticks, at least 1."""
    step0, step1 = max(1, int(step0)), max(1, int(step1))
    t0, t1 = round(a * ppq), round(b * ppq)
    if t1 <= t0:
        return []
    # every tick's step a chunk at a time (one graph read each), then walked (hunt 2026-10-29: one read per jump
    # froze ~1 s; a chunk keeps a huge area's memory small)
    span, chunk, steps, c0 = t1 - t0, 1 << 20, [], None
    out, t = [], t0
    while t < t1 and len(out) <= MOST_ANCHORS:
        out.append(t)
        k = t - t0
        if c0 is None or not c0 <= k < c0 + len(steps):
            c0 = k
            h = graph_at(pts, np.arange(k, min(span, k + chunk)) / span)
            s = step0 * (step1 / step0) ** h if scale == "exp" else step0 + (step1 - step0) * h
            steps = np.maximum(1, np.floor(s + 0.5)).astype(np.int64).tolist()  # (halves up)
        t += steps[k - c0]
    return [(np.array(out, np.int64) / ppq, float(b))]


def heard_points(tempo, base, ppq, a, b):
    """What was really heard over beats a..b (the file's tempo with catch-ups, Keep timing parts at their heard
    tempo) as a heard tempo line: [[beat, bpm, "hold", ...]] held points, each BPM exactly a whole number of
    microseconds (so it plays back exactly). Keep the audio's runs play it (hunt 2026-10-29: the area's own tempo
    changes were lost, notes after it moved)."""
    from notes.tempo import BEND, _put_many, base_us, beat_tick, file_segments, layout
    ta, tb = beat_tick(a, ppq), max(beat_tick(b, ppq), beat_tick(a, ppq) + 1)
    if not tempo["points"] and not tempo["catch"]:  # (no tempo line: the project's BPM all along)
        return [[ta / ppq, 60_000_000 / base_us(base), "hold", "", "lin", False, list(BEND)]]
    lay = layout(tempo, base, ppq)
    segs, _ = file_segments(tempo, base, ppq, tb)
    puts = []
    for n, (_, ka, ke, _) in enumerate(lay["keeps"]):
        ke = tb if ke is None else min(ke, tb)
        if ke > ka:
            puts.append((ka, ke, lay["keep_segs"](n, ka, ke)))
    segs = _put_many(segs, puts)
    out, last = [], None
    for a0, b0, u in segs:
        s, e = max(a0, ta), min(b0, tb)
        if e <= s:
            continue
        if isinstance(u, (int, np.integer)):
            spots = [(s, int(u))]
        else:  # (one value per tick: a point where it changes)
            vals = np.asarray(u, np.int64)[s - a0:e - a0]
            change = np.flatnonzero(np.diff(vals)) + 1
            spots = [(s, int(vals[0]))] + [(s + int(k), int(vals[k])) for k in change]
        for t, us in spots:
            if us != last:
                out.append([t / ppq, 60_000_000 / us, "hold", "", "lin", False, list(BEND)])
                last = us
    return out


def anchor_count(runs):
    return sum(len(at) for at, _ in runs)


# ---------------------------------------------------------------- the tempo made (group B)

SHAPES = ("burst", "jump", "updown", "random", "drawn")  # between two jumps (lo = the first box, hi = the second):
# lo -> hi (higher first = a reverse burst), lo then back to the normal tempo halfway (catch-up way: held up to the
# catch-up), lo / hi in turns, random between them, the user's drawn piece (drawn_shape) stretched over each gap
HOWS = ("audio", "catch")  # Keep the audio (the area's notes keep their sound time) / a catch-up after each jump
HOLD = 50.0  # %: the catch-up way, how much of each gap the show tempo holds before its catch-up starts
CATCH_SHAPES = ("flat", "ease", "rise", "ramp")  # the catch-ups' shapes offered (the pane's, without drawn / formula)


def gap_values(shape, k, lo, hi, rng, snap=0.0):
    """(BPM leaving the jump, BPM arriving at the next one or None = held) for gap k. snap: the BPM snap, for
    Random's picks only (typed BPMs stay exact: hunt 2026-10-29, 82.5 came out 82)."""
    if shape == "burst":
        return lo, hi
    if shape == "updown":
        return (lo if k % 2 == 0 else hi), None
    if shape == "random":
        v = lo + (hi - lo) * rng.random()
        return (max(snap, round(v / snap) * snap) if snap else v), None
    return lo, None  # (jump: the first box only)


GRAPH_POINTS = 200  # the most points a jump graph (My drawn one / Strength, window/jump_graph.py) keeps


def graph_at(pts, t):
    """A jump graph's height (0..1) at t (0..1, array): points [along, height, bend] joined by stretches bent like a
    Conductor pane part (tempo.bent, control point [0.5, bend]: 0.5 = straight); two points on one spot = a jump
    (the later one from there)."""
    from notes.tempo import bent
    t = np.clip(np.asarray(t, float), 0.0, 1.0)
    out = np.full(t.shape, float(pts[-1][1]))
    for (u0, p0, m), (u1, p1, _) in zip(pts, pts[1:]):
        inside = (t >= u0) & (t < u1) if u1 > u0 else np.zeros(t.shape, bool)
        out[inside] = p0 + (p1 - p0) * bent((t[inside] - u0) / (u1 - u0), [0.5, m])
    return out


def fill_clip(runs, a, b, shape, lo, hi, normal, how="audio", hold=HOLD, catch_shape="flat", rng=None,
              snap=0.0, plain=(), heard=None, drawn=None, strength=None, sizes=None):
    """The tempo the fill lays over beats a..b, as a Conductor pane copy (TempoPane.put_copies replaces what's in
    the area with it): {"length", "points", "catch", "spans"}, beats from a. runs: melody_runs / snap_runs. normal:
    the BPM between runs.
    Keep the audio: the WHOLE AREA one Keep timing run playing heard (heard_points: what was heard there before, in
    song beats), so no note moves, inside or after it (hunt 2026-10-29); normal = the drawn tempo arriving there.
    Catch-up: each gap = the show tempo up to hold % of it, then a catch-up (its yellow area from the jump, so
    back-to-back catch-ups all pay back to the tempo after the run: every jump on time); normal = the tempo heard
    at the area's start. plain: gaps (run, k) left at the normal tempo (their catch-up can't be done). snap: BPM
    snap for Random's values. drawn: My drawn one's graph (graph_at), for shape "drawn": stretched over each gap
    (up to the catch-up), bottom = the lower of lo / hi, top = the higher, each stretch one part. strength: a graph
    from the first jump to the last (None = 100 % all along; past the last: its end value): every BPM made goes only
    that share of the way from the normal
    tempo (straight BPMs, user 2026-10-29: 120 -> 240 at 50 % = 180). sizes: [shares per run] (pitch_sizes), each
    gap's BPMs the same way by its jump's share, on top of Strength (None = whole)."""
    import random
    from notes.tempo import BEND, catch_set, clamp_bpm
    rng = rng or random.Random()
    keep = how == "audio"
    pts, cs, gaps = [], [], []  # (gaps: each catch-up's (run, gap))
    line = [[q[0] - a] + list(q[1:]) for q in heard or ()]

    # Strength runs from the first jump to the last (user 2026-10-29: Grow's last jump stopped at 75 %, it sat
    # three quarters across the area); one jump: across the area
    jumps = [float(x) for xs, _ in runs for x in xs]
    s0, s1 = (min(jumps), max(jumps)) if len(jumps) > 1 and max(jumps) > min(jumps) else (a, b)

    size = [1.0]  # (the gap being made's pitch share)

    def point(x, bpm, curve="hold", formula="", scale=None, bend=BEND):
        scale = scale or ("exp" if curve == "line" else "lin")
        share = size[0]
        if strength is not None and s1 > s0:
            share *= float(graph_at(strength, [(x - s0) / (s1 - s0)])[0])
        if share != 1.0:
            bpm = normal + (bpm - normal) * share
        p = [x - a, clamp_bpm(bpm), curve, formula, scale, keep, list(bend)]
        if keep and line:
            p.append({"line": [list(q) for q in line]})
        pts.append(p)

    if keep and (not runs or runs[0][0][0] > a + 1e-9):  # (from the area's start: its audio kept too)
        point(a, normal)
    for r, (xs, end) in enumerate(runs):
        xs = [float(x) for x in xs]
        for k, x in enumerate(xs):
            nxt = xs[k + 1] if k + 1 < len(xs) else end
            if nxt <= x + 1e-9:
                continue
            if (r, k) in plain:
                point(x, normal)
                continue
            size[0] = float(sizes[r][k]) if sizes is not None else 1.0
            v0, v1 = gap_values(shape, k, lo, hi, rng, snap)
            stop = nxt if keep else x + (nxt - x) * min(max(hold, 1.0), 99.0) / 100.0
            made = len(pts)
            if shape == "drawn" and drawn:
                bl, bh = min(lo, hi), max(lo, hi)
                for j, (u, p, m) in enumerate(drawn):
                    nxt_p = drawn[j + 1][1] if j + 1 < len(drawn) else p
                    point(x + (stop - x) * u, bl + (bh - bl) * p, "line" if nxt_p != p else "hold", "", "lin",
                          [0.5, m])
            elif shape == "jump" and keep:
                point(x, v0)
                point(x + (nxt - x) / 2, normal)
            elif v1 is None:
                point(x, v0)
            else:
                point(x, v0, "line")
                point(stop, v1)  # (arriving: the next jump / the catch-up line leaves from the same beat)
            if not keep and all(p[1] == clamp_bpm(normal) for p in pts[made:]):  # (no jump left at all: 0 %
                del pts[made:]  # Strength / lowest note; no catch-up to pay back, hunt 2026-10-29: it was counted
                point(x, normal)  # "too short" or got one doing nothing)
                continue
            if not keep:
                item = [stop - a, catch_shape, ""]
                catch_set(item, 3, x - a)  # (its yellow area from the jump: back-to-back stretches chain)
                cs.append(item)
                gaps.append((r, k))
        size[0] = 1.0
        point(end, normal)  # (the run's end: back to the normal tempo)
    pts.sort(key=lambda p: p[0])  # (stable: an arriving point stays before the one leaving the same beat)
    held = []  # (a held point the same as the one before: nothing changes there, left out: gaps left plain; never
    lines = sorted(c[0] for c in cs)  # one a catch-up ends on: back-to-back catch-ups chain through those)
    for p in pts:
        q = held[-1] if held else None
        if q is not None and q[2] == "hold" and p[2] == "hold" and q[1] == p[1] and q[0] < p[0] \
                and bisect.bisect_right(lines, q[0]) == bisect.bisect_right(lines, p[0]):
            continue
        held.append(p)
    return {"length": b - a, "points": held, "catch": cs, "spans": [(0.0, b - a)], "gaps": gaps}
