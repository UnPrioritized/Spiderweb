"""Change visual time signature (user 2026-11-12; plan: PLAN.md / dev/data/rebar-status.md): a stretch of whole bars
gets a new time signature, each old bar becoming ONE new bar. Its notes stretch in beats and its tempo scales by
the same amount, so it SOUNDS EXACTLY THE SAME; the old signature + old tempo come back after it and everything
after moves along. Only the bar lines change.

Factor of a bar = new bar length / old bar length in quarter notes (4/4 at 100 BPM -> 6/4 = x1.5 = 150 BPM).
Mixed signatures inside = each old bar its own factor (user 2026-11-12: 4/4 x1.5, 3/4 x2 for 6/4).

This file is the maths only (no window): plan() works out the bars and the beat map, tempo_of() the new tempo line
(points, catch-ups, signatures, key signatures), map_beats / map_ticks move times. A catch-up or Keep timing part
that crosses the stretch's edge (or a change of factor inside it) can't keep its sound: refused (user 2026-11-12),
tempo_of returns {"why": code, "at": beat}."""

import bisect
import copy
import math

import numpy as np

from notes import meter, tempo as T

EPS = 1e-9
WHY = ("catch", "keep", "limit", "empty")  # (refusal codes: a catch-up / Keep timing part across an edge, a BPM past
#                                            what a file holds, nothing to change)


def grow(sigs, lo, hi):
    """(a, b) beats: lo..hi grown out to whole bars (a box not on bar lines takes the bars it touches; an empty
    box = the bar it is in)."""
    lo, hi = max(0.0, min(lo, hi)), max(0.0, max(lo, hi))
    a = meter.bar_line(sigs, lo)
    b = meter.bar_line(sigs, hi, after=True)
    if b <= a + EPS:
        b = meter.bar_line(sigs, a + EPS, after=True)
    return a, b


def old_bars(sigs, a, b):
    """[(start, end)] of the old bars from a to b (a short bar a signature cut is its own bar)."""
    starts = [x for x, _ in meter.bar_lines(sigs, a - EPS, b - EPS)]
    starts = [x for x in starts if a - EPS <= x < b - EPS]
    if not starts or abs(starts[0] - a) > EPS:
        starts.insert(0, a)
    return [(s, starts[i + 1] if i + 1 < len(starts) else b) for i, s in enumerate(starts)]


def plan(sigs, lo, hi, top, bottom):
    """The change worked out: {"a", "b": the old stretch (whole bars), "new_b": its new end, "shift": what everything
    after moves (beats), "runs": [(old a, old b, new a, new b, factor)] (bars in a row with the same factor joined),
    "bars": (first, last bar number from 0), "count": bars, "sig": [top, bottom], "factors": every bar's factor}."""
    a, b = grow(sigs, lo, hi)
    n = meter.bar_len(top, bottom)
    bars = old_bars(sigs, a, b)
    runs, at, factors = [], a, []
    for s, e in bars:
        f = n / (e - s)
        factors.append(f)
        if runs and abs(runs[-1][4] - f) <= EPS * f:
            r = runs[-1]
            runs[-1] = (r[0], e, r[2], at + n, r[4])
        else:
            runs.append((s, e, at, at + n, f))
        at += n
    first = meter.where(sigs, a + EPS)[0]
    return {"a": a, "b": b, "new_b": at, "shift": at - b, "runs": runs, "bars": (first, first + len(bars) - 1),
            "count": len(bars), "sig": [int(top), int(bottom)], "factors": factors}


def _edges(pl):
    return [r[0] for r in pl["runs"]] + [pl["b"]]


def factor_at(pl, x, side=1):
    """The factor at beat x (side 1: just after it, -1: just before it); 1 outside the stretch."""
    y = x + side * EPS * 10
    if not pl["a"] <= y < pl["b"]:
        return 1.0
    return pl["runs"][_run(pl, y)][4]


def _run(pl, x):
    """The index of the run beat x is in (by halving: thousands of runs x thousands of points)."""
    if "starts" not in pl:
        pl["starts"] = [r[0] - EPS for r in pl["runs"]]
    return max(0, bisect.bisect_right(pl["starts"], x) - 1)


def map_beat(pl, x):
    """Old beat -> new beat (exact on the runs' edges)."""
    if x < pl["a"] - EPS:
        return x
    if x >= pl["b"] - EPS:
        return pl["new_b"] + (x - pl["b"]) if abs(x - pl["b"]) > EPS else pl["new_b"]
    oa, ob, na, nb, f = pl["runs"][_run(pl, x)]
    return na if abs(x - oa) <= EPS else na + (x - oa) * f


def map_beats(pl, xs):
    """map_beat over an array of beats (NumPy, for many notes)."""
    xs = np.asarray(xs, float)
    out = xs.copy()
    after = xs >= pl["b"] - EPS
    out[after] = xs[after] + pl["shift"]
    on = (xs >= pl["a"] - EPS) & ~after
    if on.any():  # (each beat's run found by halving: never every run over every note)
        oa, na, f = (np.array([r[k] for r in pl["runs"]], float) for k in (0, 2, 4))
        i = np.maximum(np.searchsorted(oa - EPS, xs[on], "right") - 1, 0)
        out[on] = na[i] + (xs[on] - oa[i]) * f[i]
    return out


def map_ticks(pl, ticks, ppq):
    """Old ticks -> new ticks (nearest tick; NumPy int64 for an array). Before the stretch unchanged, after it
    moved by the whole number of ticks the stretch grew."""
    t = np.asarray(ticks, np.int64)
    out = t.copy()
    a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
    after = t >= b_t
    out[after] = t[after] + (round(pl["new_b"] * ppq) - b_t)
    on = (t >= a_t) & ~after
    if on.any():
        ta = np.array([round(r[0] * ppq) for r in pl["runs"]], np.int64)
        tn = np.array([round(r[2] * ppq) for r in pl["runs"]], np.int64)
        f = np.array([r[4] for r in pl["runs"]], float)
        i = np.maximum(np.searchsorted(ta, t[on], "right") - 1, 0)
        out[on] = tn[i] + np.rint((t[on] - ta[i]) * f[i]).astype(np.int64)
    return int(out) if np.ndim(ticks) == 0 else out


def _inside_run(pl, lo, hi):
    """lo..hi lies inside one run (the same factor all along)."""
    if not pl["a"] - EPS <= lo < pl["b"] + EPS:
        return False
    return hi <= pl["runs"][_run(pl, lo)][1] + EPS


def _run_end(pl, x):
    """The end of the run beat x is in."""
    return pl["runs"][_run(pl, x)][1]


def check(tempo, base, pl):
    """None when the change keeps every catch-up and Keep timing part's sound, else {"why", "at": beat}. A
    catch-up's stretch (from its yellow start to its end, and the beat it is paid back to) must be wholly before
    the stretch, wholly after it, or inside one run without reaching its end (the tempo read there is the next
    run's); a Keep timing part likewise, and one inside a run must not start on its first beat (it plays the tempo
    heard just before it)."""
    a, b = pl["a"], pl["b"]
    for st in T.stretches(tempo, base):
        if st["end"] is None:
            continue
        lo, end = st["from"], st["end"]
        read = st["read"] if st["read"] is not None else end
        hi = max(end, read, st["line"])
        if hi < a - EPS or (abs(hi - a) <= EPS and read < a - EPS):
            continue
        if lo >= b - EPS:
            continue
        if _inside_run(pl, lo, hi) and read < _run_end(pl, lo) - EPS:
            continue
        return {"why": "catch", "at": st["line"]}
    pts = tempo["points"]
    for i in T.keep_points(pts):
        lo = pts[i][0]
        hi = pts[i + 1][0] if i + 1 < len(pts) else math.inf
        if hi <= a + EPS or lo > b + EPS:
            continue
        if _inside_run(pl, lo, hi) and not any(abs(lo - oa) <= EPS for oa, *_ in pl["runs"]):
            continue
        return {"why": "keep", "at": lo}
    return None


def _pair(pts, x, beats):
    """At beat x: the arrival point and the point leaving it, a pair (two points on one beat, the tempo line's
    jump), so each side can scale on its own. Returns (arrival, leaving)."""
    on = T.on_beat(pts, x, beats)
    if len(on) >= 2:
        return pts[on[0]], pts[on[-1]]
    p = pts[on[0]]
    arrive = [p[0], p[1], "hold", "", "lin", False, list(T.BEND)]
    pts.insert(on[0], arrive)
    beats.insert(on[0], p[0])
    return arrive, p


def _scale_heard(p, pl):
    h = T.heard_of(p)
    if not h:
        return
    hl = h["line"]
    hb = [q[0] for q in hl]
    for x in _edges(pl):
        if hl and hb[0] < x:  # (cut at the edges, like the line itself; before its first point it reads the part's)
            T.split_at(hl, x, hl[0][1], hb)
    for q in hl:
        q[1] = T.clamp_bpm(q[1] * factor_at(pl, q[0]))
        q[0] = map_beat(pl, q[0])
    if "end" in h:
        h["end"] = T.clamp_bpm(h["end"] * factor_at(pl, p[0]))


def tempo_of(tempo, base, pl):
    """The new tempo line (a copy; the old one untouched) or {"why", "at"} when it can't keep the sound. base =
    the project's BPM (before the first point)."""
    why = check(tempo, base, pl)
    if why:
        return why
    out = copy.deepcopy(tempo)
    pts = out["points"]
    beats = [p[0] for p in pts]
    edges = _edges(pl)
    for x in edges:  # (the line cut at each edge, looking the same: split_at)
        T.split_at(pts, x, base, beats)
    arrivals = []
    for x in edges:
        fl, fr = factor_at(pl, x, -1), factor_at(pl, x, 1)
        if abs(fl - fr) > EPS:
            arrive, leave = _pair(pts, x, beats)
            arrivals.append(arrive)
            arrive[1] *= fl
            leave[1] *= fr
            arrive[0] = leave[0] = x
    on_edge = {id(p) for x in edges for i in T.on_beat(pts, x, beats) for p in (pts[i],)}
    for p in pts:
        if id(p) not in on_edge:
            p[1] *= factor_at(pl, p[0])
    for p in pts:
        if not T.MIN_BPM - 1e-9 <= p[1] <= T.MAX_BPM:
            return {"why": "limit", "at": p[0]}
    for p in pts:
        _scale_heard(p, pl)
        p[0] = map_beat(pl, p[0])
        p[1] = T.clamp_bpm(p[1])
    place = {id(p): i for i, p in enumerate(pts)} if arrivals else {}
    for arrive in arrivals:  # (a curved part arriving at its own BPM is flat: held, so the arrival can go and it
        k = place[id(arrive)]  # never glides on to the leaving point instead)
        prev = pts[k - 1] if k > 0 else None
        if prev is not None and prev[2] != "hold" and abs(prev[1] - arrive[1]) <= 1e-12 * arrive[1]:
            prev[2] = "hold"
    T.drop_loose(pts)
    for c in out["catch"]:
        c[0] = map_beat(pl, c[0])
        for k in (3, 4):
            v = T.catch_get(c, k)
            if v is not None:
                T.catch_set(c, k, map_beat(pl, v))
    out["sigs"] = sigs_of(tempo, pl)
    out["keysigs"] = [[map_beat(pl, m[0]), *m[1:]] for m in tempo.get("keysigs") or []]
    # (safety net: every catch-up's stretch must be the old one moved; the edges' new points can end a catch-up
    # that had nothing after it, or move where one's yellow area starts)
    for o, n in zip(T.stretches(tempo, base), T.stretches(out, base)):
        for k in ("from", "line", "end", "read"):
            if (o[k] is None) != (n[k] is None) or (o[k] is not None and abs(map_beat(pl, o[k]) - n[k]) > 1e-6):
                return {"why": "catch", "at": n["line"]}
    return out


def sigs_of(tempo, pl):
    """The new time signatures: the picked one at the stretch's start, the one in effect at its old end back after
    it (none when it's the same), the ones inside gone, the ones after moved along. No new repeat of the signature
    already in effect (old marks, repeats too, stay)."""
    old = meter.sigs_of(tempo)
    a, b = pl["a"], pl["b"]
    back = meter.at_beat(old, b)
    out = [list(m) for m in old if m[0] < a - EPS]
    if not out or out[-1][1:] != pl["sig"] or a < EPS:
        out.append([a, *pl["sig"]])
    after = [[m[0] + pl["shift"], *m[1:]] for m in old if m[0] >= b - EPS]
    if not (after and abs(after[0][0] - pl["new_b"]) <= EPS) and back[1:] != pl["sig"]:
        out.append([pl["new_b"], *back[1:]])
    out += after
    return out
