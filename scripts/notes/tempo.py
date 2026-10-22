"""The project's tempo line (the Tempo pane, window/tempo_pane.py) and the tempo changes it writes into a MIDI file.

The line is made of points, each an exact BPM at a beat; two on one beat (user 2026-10-22) = a jump: the first is
where the part before arrives, the second the tempo from there (the first never reaches the file: it holds for no
tick). Each part between two points has a curve (hold, straight,
S-curve, ease out, ease in, or a formula of x from 0 to 1) and a BPM change: linear (default: a straight part from
60 to 240 passes 150 halfway) or exponential (straight on the pane's doubling scale: passes 120). Before the first
point the tempo is the project's BPM, after the last one it holds the last point's. A curved part writes a new
tempo every "every" ticks (default 24).

A catch-up line starts a catch-up (user 2026-10-21): the drawn tempo holds up to the line, then from the line to the
next point (or catch-up line) Spiderweb works out a tempo so the whole stretch (stretches: from where the line left
the tempo it comes back to) takes exactly as long as at the tempo heard just before it; the drawn line goes on from
that point. The catch-up's shape: flat, fast then slow (settling to the BPM), slow then fast, up and down or a
formula; the two eases bend by a handle (catch[5]). A catch-up never moves notes: everything after it is in time.

Keep timing (a tick per part, point[5]): the part's notes keep the time they have at the tempo heard before it;
the export moves them in ticks (notes/tempo_file.py). The part after the last point goes on to the song's end.

Saved in the project as "tempo": {"points": [[beat, bpm, curve, formula, "lin" / "exp", keep timing], ...],
"catch": [[beat, shape, formula, start, end, bend], ...], "every": ticks, "bars": Fit bar lines}, beats like the shapes (a PPQ change
moves nothing).

Exact timing (user's rule: tick and microsecond perfect, not "too small to hear"): a MIDI file's time is whole
numbers, ticks x microseconds per beat, so the catch-up is picked tick by tick to make each stretch's sum exactly
the project BPM's. Every value written also survives Domino opening and saving the file unchanged (domino_safe:
measured 2026-10-21, Domino keeps a tempo as a 32-bit BPM, f32(6e7 / us), and saves trunc(6e7 / bpm): half of all
values lost 1 us each save)."""

import bisect
import json
import math

import numpy as np

from files.mathexpr import fmt, formula

MIN_BPM = 60_000_000 / 0xFFFFFF  # ~3.58: the slowest a MIDI file holds (24-bit microseconds per beat)
MAX_BPM = 60_000_000.0  # 1 microsecond per beat (faster = 0, which breaks the file; Domino writes that past it)
CURVES = ("hold", "line", "s", "formula")
BEND = [0.5, 0.5]  # a line / S-curve part's bend (its curve's control point, 0..1 each way): straight
OLD_CURVES = {"out": ("line", [0.5, 1.0]), "in": ("line", [0.5, 0.0])}  # (ease out / in before the bend handle)
SHAPES = ("flat", "ease", "rise", "ramp", "formula")
CATCH_BEND = [0.5, 0.0]  # a fast-then-slow / slow-then-fast catch-up's bend (control point: 0..1 along, push)
FORMULA = "x^3"  # a new formula part's / catch-up's starting formula
SCALES = ("lin", "exp")  # a part's BPM change: linear (default, user) / exponential (straight on the doubling scale)
EVERY = 24  # ticks between the tempo changes of a curved part (user: like Domino's default; 1 = every tick)
BLOCKS_FROM = 8  # an interval this long or more: a curved part worked out step by step, not tick by tick
FAR = 1e7  # beats: past this a point is left out (as shapes' points, engine.BEATS_FAR)
SAFE_REACH = 8192  # the farthest a Domino-safe value is from any value (measured: 5212 at the slowest tempos)
SLOWEST = 16_777_211  # the slowest Domino-safe tempo (us per beat; a file holds up to 0xFFFFFF)


def empty():
    return {"points": [], "catch": [], "every": EVERY, "bars": True}


def has_tempo(tempo):
    return bool(tempo and tempo.get("points"))


def clamp_bpm(v):
    return min(MAX_BPM, max(MIN_BPM, float(v)))


def clean_tempo(data):
    """A tempo line from a file / undo step -> a safe one (broken points left out, sorted, at most two points per
    beat: where a curve arrives, then the tempo from there; user 2026-10-22)."""
    out = empty()
    if not isinstance(data, dict):
        return out
    seen = {}
    for p in data.get("points", []) if isinstance(data.get("points"), list) else []:
        if not isinstance(p, list):  # (broken by hand: that point left out, the rest kept)
            continue
        try:
            b, v = float(p[0]), float(p[1])
            kind = p[2] if len(p) > 2 and isinstance(p[2], str) else "hold"
            bend = list(BEND) if kind != "s" else [0.5, 0.0]  # (an S-curve from before: much like it was)
            kind, bend = OLD_CURVES.get(kind, (kind, bend))
            kind = kind if kind in CURVES else "hold"
            text = p[3] if len(p) > 3 and isinstance(p[3], str) else ""
            scale = p[4] if len(p) > 4 and isinstance(p[4], str) and p[4] in SCALES else "lin"
            keep = len(p) > 5 and p[5] is True
            got = p[6] if len(p) > 6 and isinstance(p[6], list) and len(p[6]) == 2 else None
            if got and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                           for x in got):
                bend = [min(1.0, max(0.0, float(x))) for x in got]
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        if not (math.isfinite(b) and math.isfinite(v)) or not 0 <= b <= FAR or v <= 0:
            continue
        key = round(b, 9)
        if seen.get(key, 0) >= 2:
            continue
        seen[key] = seen.get(key, 0) + 1
        out["points"].append([b, clamp_bpm(v), kind, text[:200], scale, keep, bend])
    out["points"].sort(key=lambda p: p[0])  # (stable: a pair keeps its order)
    for i in range(len(out["points"]) - 1):  # (a pair on exactly one beat, not a hair apart)
        p, q = out["points"][i:i + 2]
        if round(p[0], 9) == round(q[0], 9):
            q[0] = p[0]
    every = data.get("every", EVERY)
    out["every"] = every if isinstance(every, int) and not isinstance(every, bool) and 1 <= every <= 65535 else EVERY
    out["bars"] = data.get("bars", True) is not False
    seen = set()
    for c in data.get("catch", []) if isinstance(data.get("catch"), list) else []:
        if not isinstance(c, list):
            continue
        try:
            b = float(c[0])
            shape = c[1] if len(c) > 1 and isinstance(c[1], str) and c[1] in SHAPES else "flat"
            text = c[2] if len(c) > 2 and isinstance(c[2], str) else ""
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        if not math.isfinite(b) or not 0 < b <= FAR or round(b, 9) in seen:
            continue
        seen.add(round(b, 9))

        def edge(k):
            x = c[k] if len(c) > k else None
            try:
                return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) \
                    and math.isfinite(float(x)) else None
            except OverflowError:
                return None
        start, end = edge(3), edge(4)
        item = [b, shape, text[:200]]
        catch_set(item, 3, start if start is not None and 0 <= start < b else None)
        catch_set(item, 4, end if end is not None and b < end <= FAR else None)
        bend = c[5] if len(c) > 5 else None
        if isinstance(bend, list) and len(bend) == 2 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1 for x in bend):
            catch_set(item, 5, [float(x) for x in bend])
        out["catch"].append(item)
    out["catch"].sort(key=lambda c: c[0])
    return out


def catch_set(c, k, v):
    """A catch-up line's start (k = 3, the yellow area's left edge) or end (k = 4, the teal area's right edge) set
    by hand, or None = where Spiderweb puts it; k = 5: its bend [x, y] (None = CATCH_BEND). Kept short: [beat,
    shape, formula] when none is set."""
    while len(c) <= k:
        c.append(None)
    c[k] = None if v is None or v == CATCH_BEND else list(v) if k == 5 else float(v)
    while len(c) > 3 and c[-1] is None:
        c.pop()


def catch_get(c, k):
    return c[k] if len(c) > k else None


_formulas = {}


def formula_fn(text):
    """The formula of x as a function (None if it can't be read). Remembered: the pane draws often."""
    if text not in _formulas:
        try:
            fn = formula(text or FORMULA)
            fn(0.5)
        except (ValueError, ArithmeticError, TypeError):
            fn = None
        if len(_formulas) > 200:
            _formulas.clear()
        _formulas[text] = fn
    return _formulas[text]


def bend_of(p):
    """A point's part bend [x, y] (BEND = straight)."""
    return p[6] if len(p) > 6 and p[6] else BEND


def bent(t, bend):
    """A curve from (0, 0) to (1, 1) bent through its control point bend (0..1 each way, so it never goes past
    either end: user, like the velocity pane's curve), read at times t."""
    cx, cy = bend
    a = 1 - 2 * cx
    if abs(a) < 1e-9:
        s = t
    else:  # (x(s) = a s^2 + 2 cx s: the place along the curve at each time)
        s = (-2 * cx + np.sqrt(np.maximum(4 * cx * cx + 4 * a * t, 0))) / (2 * a)
    s = np.clip(s, 0, 1)
    return 2 * s * (1 - s) * cy + s * s


def curve(kind, text, t, bend=None):
    """How far along (0 at the part's start point, 1 at its end) a part of this curve is at t (0..1, array). Line:
    bent by its handle (bend); S-curve: that bend on the first half, mirrored on the second."""
    t = np.asarray(t, float)
    bend = bend or BEND
    if kind == "line":
        return t if bend == BEND else bent(t, bend)
    if kind == "s":
        lo = t < 0.5
        return np.where(lo, bent(np.minimum(2 * t, 1), bend) / 2, 1 - bent(np.minimum(2 * (1 - t), 1), bend) / 2)
    if kind == "formula":
        return _apply(formula_fn(text), t)
    return np.zeros_like(t)  # hold: the start point's BPM, then a jump


FORMULA_SAMPLES = 4096  # a formula worked out at most this many times per part (in between: read off a straight line)


def _apply(fn, t):
    if fn is None:
        return np.zeros_like(t)
    if t.size > FORMULA_SAMPLES + 1:  # (a long curve changes on every tick: tens of thousands)
        grid = np.linspace(0.0, 1.0, FORMULA_SAMPLES + 1)
        return np.interp(t, grid, _apply(fn, grid))
    out = np.empty_like(t)
    flat = t.ravel()
    res = out.ravel()
    for i, x in enumerate(flat.tolist()):
        try:
            v = fn(x)
            res[i] = v if math.isfinite(v) else 0.0
        except (ValueError, ArithmeticError, TypeError):
            res[i] = 0.0
    return out


def catch_weight(shape, text, t, bend=None):
    """The catch-up's shape at t (0..1): how much of its push each moment gets (0 = the project's BPM). Flat = the
    same all along; ease = fast then slow (all at the start, none at the end); rise = slow then fast; both bent by
    their handle (bend: the control point, x along, y = push; CATCH_BEND: (1 - t)^2 / t^2); ramp = up and back
    down."""
    t = np.asarray(t, float)
    cx, cy = bend or CATCH_BEND
    if shape == "ease":  # (from 1 to 0: bent() upside down)
        return 1 - bent(t, (cx, 1 - cy))
    if shape == "rise":
        return bent(t, (cx, cy))
    if shape == "ramp":
        return np.sin(np.pi * t)
    if shape == "formula":
        return _apply(formula_fn(text), t)
    return np.ones_like(t)


# ------------------------------------------------------------ the drawn line and its stretches

def catch_end(tempo, k):
    """Where catch-up line k's catch-up ends: its teal area's right edge set by hand, else the next point; never
    past the next catch-up line. None when nothing ends it."""
    c, lines = tempo["catch"][k], [x[0] for x in tempo["catch"]]
    after = [p[0] for p in tempo["points"] if p[0] > c[0]] + lines[k + 1:k + 2]
    end = min(after) if after else None
    if catch_get(c, 4) is not None:
        end = min([catch_get(c, 4)] + lines[k + 1:k + 2])
    return end


def stretches(tempo, base):
    """The catch-up lines' stretches: [{"from": beat the stretch (the yellow area + the catch-up) starts, "read":
    the beat whose tempo it's paid back to (= "end"; None when there's no end), "manual": "from" set by hand,
    "line": the line's beat = where the catch-up starts, "end": where it ends (the next point or catch-up line
    after it; None when there's none), "shape", "text"}].

    Paid back to the tempo the line comes back to after the catch-up (user 2026-10-21: the yellow area counts
    everything in it). Where it starts (the yellow area, dragged by its left edge = catch[3]; a song may change
    tempo for real before a jump): by hand = there. Else, walking back from the line, the last point at the BPM
    the line comes back to = where the jump left it; the stretch starts where the line leaves it (that point when
    its part curves away, else the next point). None at that BPM: the first point after the stretch before."""
    pts = tempo["points"]
    pb = [p[0] for p in pts]
    ends = [catch_end(tempo, k) for k in range(len(tempo["catch"]))]
    prevs, prev = [], 0.0
    for end in ends:
        prevs.append(prev)
        prev = prev if end is None else end
    out = [None] * len(ends)
    for k in range(len(ends) - 1, -1, -1):  # (from the last: each may read the next one's)
        c, end, prev = tempo["catch"][k], ends[k], prevs[k]
        b, shape, text = c[:3]
        hand, hand_end = catch_get(c, 3), catch_get(c, 4)
        start, read = prev, None
        if end is not None:  # (user: the yellow area counts everything in it, at the tempo the line comes back to:
            # the next point at or after the end, also when it ends at another catch-up line or by hand mid-stop)
            read = min([x for x in pb if x >= end - 1e-12] or [end])
            nxt = out[k + 1] if k + 1 < len(out) else None
            if nxt and nxt["read"] is not None and nxt["from"] < nxt["line"] and abs(nxt["from"] - read) <= 1e-9:
                read = nxt["read"]  # (ending where the next stop starts: paid back to the tempo that one comes
                #                     back to; user: a stretch pasted right after itself changes nothing)
        if end is not None and hand is not None and hand < b:
            start = max(prev, hand)
        elif end is not None:
            on = float(drawn_bpm(tempo, base, [read])[0])
            cands = [i for i, x in enumerate(pb) if prev <= x < b]
            if cands:
                start = pb[cands[0]]
            for n in range(len(cands) - 1, -1, -1):
                i = cands[n]
                if abs(pts[i][1] - on) <= 1e-9 * on:
                    leaves = pts[i][2] != "hold" and i + 1 < len(pts) and pts[i + 1][1] != pts[i][1]
                    start = pb[i] if leaves else (pb[cands[n + 1]] if n + 1 < len(cands) else b)
                    break
        out[k] = {"from": start, "read": read, "manual": hand is not None and hand < b,
                  "manual_end": hand_end is not None,
                  "line": b, "end": end, "shape": shape, "text": text, "bend": catch_get(c, 5)}
    return out


def drawn_bpm(tempo, base, beats):
    """The drawn line's BPM at beats (array): the points' curves, the project's BPM before the first point. A
    catch-up's part shows the drawn line here (catch_bpm has its own)."""
    beats = np.asarray(beats, float)
    pts = tempo["points"]
    out = np.full(beats.shape, float(base))
    if not pts:
        return out
    pb = np.array([p[0] for p in pts])
    vs = np.array([p[1] for p in pts])
    lv = np.log2(vs)
    idx = np.searchsorted(pb, beats, "right") - 1  # the point at or before each beat (-1: before the first)
    has = idx >= 0
    out[has] = vs[idx[has]]
    for i in range(len(pts) - 1):
        kind = pts[i][2]
        if kind == "hold" or pts[i][1] == pts[i + 1][1] or pb[i + 1] <= pb[i]:
            continue
        on = idx == i
        if on.any():
            c = curve(kind, pts[i][3], (beats[on] - pb[i]) / (pb[i + 1] - pb[i]), bend_of(pts[i]))
            if pts[i][4] == "exp":  # (the same ratio every beat: straight on the pane's doubling scale)
                out[on] = 2 ** (lv[i] + (lv[i + 1] - lv[i]) * c)
            else:  # linear: the same BPM added every beat
                out[on] = vs[i] + (vs[i + 1] - vs[i]) * c
    return np.clip(out, MIN_BPM, MAX_BPM)


def _solve_push(st, beats, seconds, base, n=512):
    """The push A that makes a catch-up of the stretch's shape (BPM = base x 2^(A x weight)) last `seconds` over
    `beats`. None when it can't (a shape with no push anywhere, or past what a MIDI file can hold)."""
    w = catch_weight(st["shape"], st["text"], (np.arange(n) + 0.5) / n, st.get("bend"))
    if not np.any(w > 1e-9) or np.any(w < -1e-9) or seconds <= 0:
        return None
    lo_b, hi_b = math.log2(MIN_BPM / base), math.log2(MAX_BPM / base)

    def took(a):
        return float(np.sum(60.0 / (base * 2 ** np.clip(a * w, lo_b, hi_b)))) * beats / n

    lo, hi = -64.0, 64.0
    if not took(hi) <= seconds <= took(lo):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if took(mid) > seconds:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def beat_tick(b, ppq):
    """The tick a beat starts on in the file: the smallest tick t with t / ppq >= b, the rule the drawn line's
    points use (_first_ticks), so a catch-up ending on a point ends where the point's tempo starts."""
    return _first_ticks([b], ppq)[0]


def base_us(base):
    """The project BPM's microseconds per beat with a tempo line: the nearest Domino-safe value (user's rule: every
    value written survives a Domino save), at most a microsecond a beat off."""
    return int(nearest_safe([60_000_000 / base])[0])


def _first_ticks(pb, ppq):
    """The first tick of each point's part: the smallest tick t with t / ppq >= the point's beat (as drawn_bpm
    reads it)."""
    out = []
    for b in pb:
        c = max(0, math.ceil(b * ppq))
        while c > 0 and (c - 1) / ppq >= b:
            c -= 1
        while c / ppq < b:
            c += 1
        out.append(c)
    return out


def _segments(tempo, base, base_u, ppq, t0, t1):
    """The drawn line's tempo from tick t0 up to t1 (exclusive) as [(a, b, us)]: us one whole number for a held
    part, an array (one per tick) for a curved one (it changes on every tick, like a line drawn in many MIDI
    programs). Never one entry per tick of a held part (a long song would fill the memory)."""
    out = []
    if t1 <= t0:
        return out
    pts = tempo["points"]
    pb = [p[0] for p in pts]
    first = _first_ticks(pb, ppq)
    edges = [0] + first + [max(t1, first[-1] + 1) if first else t1]
    for k in range(len(edges) - 1):  # (k = 0: before the first point; k = i + 1: point i's part)
        a, b = max(edges[k], t0), min(edges[k + 1], t1)
        if b <= a:
            continue
        i = k - 1
        held = i < 0 or i == len(pts) - 1 or pts[i][2] == "hold" or pts[i][1] == pts[i + 1][1]
        if held:
            bpm = float(drawn_bpm(tempo, base, [a / ppq])[0])
            out.append((a, b, base_u if bpm == base else int(nearest_safe([6e7 / bpm])[0])))
        else:  # (a new value every "every" ticks from the part's first tick, at the start of each)
            every = tempo.get("every", EVERY)
            if every >= BLOCKS_FROM:  # (one entry per step, not per tick: long songs at a high PPQ stay quick)
                starts = np.arange(edges[k] + (a - edges[k]) // every * every, b, every)
                bpm = drawn_bpm(tempo, base, starts / ppq)
                us = nearest_safe(6e7 / bpm)
                us[bpm == base] = base_u
                for s, u in zip(starts.tolist(), us.tolist()):
                    lo, hi = max(s, a), min(s + every, b)
                    if out and out[-1][1] == lo and isinstance(out[-1][2], int) and out[-1][2] == u:
                        out[-1] = (out[-1][0], hi, u)
                    else:
                        out.append((lo, hi, u))
                continue
            ticks = np.arange(a, b)
            bpm = drawn_bpm(tempo, base, (edges[k] + (ticks - edges[k]) // every * every) / ppq)
            us = nearest_safe(6e7 / bpm)
            us[bpm == base] = base_u  # (the project's own tempo, exactly as the file starts)
            out.append((a, b, us))
    return out


def _us_sum(segs):
    return sum(u * (b - a) if isinstance(u, int) else int(u.sum()) for a, b, u in segs)


def _latest_tick(segs, total, e):
    """The last tick a catch-up could start at with at least 1 microsecond per beat left for each tick after it
    (the fastest a file holds), the drawn line up to it. None when even the whole stretch drawn leaves time."""
    acc, best = 0, None
    for a, b, u in segs:
        if isinstance(u, int):
            if acc + e - a > total:
                return best
            if u > 1:
                x = (total - acc - e + u * a) // (u - 1)
                if x < b:
                    return x
            best = b
            acc += u * (b - a)
        else:
            f = acc + np.concatenate([[0], np.cumsum(u)]) + e - np.arange(a, b + 1)
            bad = np.flatnonzero(f > total)
            if len(bad):
                return a + int(bad[0]) - 1 if bad[0] > 0 else best
            best = b
            acc += int(u.sum())
    return None


def _put(segs, ra, rb, ru):
    """segs with ticks ra..rb replaced by ru (one whole number, or one per tick), kept in order."""
    out = []
    for a, b, u in segs:
        for lo, hi in ((a, min(b, ra)), (max(a, rb), b)):
            if hi > lo:
                out.append((lo, hi, u if isinstance(u, int) else u[lo - a:hi - a]))
    if rb > ra:
        out.append((ra, rb, ru))
    return sorted(out, key=lambda s: s[0])


def catch_plan(tempo, base, st, ppq, real=None):
    """A stretch's catch-up worked out in the file's own whole numbers (ticks x microseconds), so the pane and the
    export always agree: {"ok", "push", "bpm" (flat: its BPM; else its fastest / slowest), "target" (the catch-up's
    ticks x microseconds), "latest": the last beat the line could sit at (shown when it can't), "empty": nothing
    after the line to end on}. The whole stretch takes as long as at st["base_u"] (the tempo heard just before
    it; the project's BPM when not given). real(a, b) = the time ticks a..b really take (Keep timing parts at
    their heard tempo); the drawn line when not given."""
    out = {"ok": False, "push": None, "bpm": None, "target": None, "latest": None, "empty": st["end"] is None,
           "fit": None}
    if st["end"] is None:
        return out
    start_u = base_us(base)
    base_u = st.get("base_u") or start_u
    if real is None:
        def real(a, b):
            return _segments(tempo, base, start_u, ppq, a, b)
    s0, ln, e = (beat_tick(st[k], ppq) for k in ("from", "line", "end"))
    if e - ln <= 0:
        return out
    total = (e - s0) * base_u
    bpm = 60_000_000 / base_u
    every = tempo.get("every", EVERY)

    def attempt(line):
        """(push, ticks' values) with the catch-up starting at tick line; None when it can't be written."""
        n, target = e - line, total - _us_sum(real(s0, line))
        if not n <= target <= n * SLOWEST:
            return None
        push = _solve_push(st, n / ppq, target / ppq / 1e6, bpm)
        if push is None:
            return None
        t = (np.arange(n) + 0.5) / n
        ideal = 6e7 / np.clip(bpm * 2 ** (push * catch_weight(st["shape"], st["text"], t, st.get("bend"))), MIN_BPM, MAX_BPM)
        fit = _fit_blocks(ideal, target, every)  # (the file's own values: the pane says OK only when it works)
        return None if fit is None else (push, fit, target)

    got = attempt(ln)
    if got is None:
        latest = _latest_tick(real(s0, e), total, e)  # (fastest possible: right for a flat catch-up)
        if latest is not None and latest > s0 and st["shape"] != "flat":  # (others: the last line spot that works)
            lo, hi = s0, min(latest, e - 1)
            if attempt(lo) is None:
                latest = None
            else:
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    lo, hi = (mid, hi) if attempt(mid) is not None else (lo, mid)
                latest = lo
        out["latest"] = None if latest is None else latest / ppq
        return out
    push, fit, target = got
    w = catch_weight(st["shape"], st["text"], np.linspace(0, 1, 257), st.get("bend"))
    top = bpm * 2 ** float((push * w).max() if push > 0 else (push * w).min())
    out.update(ok=True, push=push, bpm=clamp_bpm(top), target=target, fit=fit)
    return out


def catch_bpm(base, st, plan, beats):
    """The catch-up's BPM at beats inside its part (for drawing), round the tempo heard before it."""
    t = (np.asarray(beats, float) - st["line"]) / max(1e-12, st["end"] - st["line"])
    w = catch_weight(st["shape"], st["text"], np.clip(t, 0, 1), st.get("bend"))
    return np.clip(st.get("base", base) * 2 ** (plan["push"] * w), MIN_BPM, MAX_BPM)


def keeps(p):
    """True for a point whose part is Keep timing (its notes keep the time they have at the tempo heard before)."""
    return len(p) > 5 and p[5] is True


def keep_parts(tempo):
    """[(point index, first beat, end beat or None = on to the song's end)] of the Keep timing parts, without the
    catch-ups' own areas (line to end: a catch-up never moves notes), so a part may come in two pieces."""
    pts = tempo["points"]
    spans = [(c[0], catch_end(tempo, k)) for k, c in enumerate(tempo["catch"])]
    out = []
    for i, p in enumerate(pts):
        if not keeps(p):
            continue
        pieces = [(p[0], pts[i + 1][0] if i + 1 < len(pts) else None)]
        for lo, hi in spans:
            cut = []
            for a, e in pieces:
                if (e is not None and lo >= e) or (hi is not None and hi <= a):
                    cut.append((a, e))
                    continue
                if lo > a:
                    cut.append((a, lo))
                if hi is not None and (e is None or hi < e):
                    cut.append((hi, e))
            pieces = cut
        out += [(i, a, e) for a, e in pieces if e is None or e > a]
    return out


_layouts = {}


def layout(tempo, base, ppq):
    """The tempo line worked out in ticks (the last few remembered: the pane asks often): {"start_u": the project
    BPM's microseconds, "keeps": [(point index, first tick, end tick or None, heard us)], "worked": [(stretch,
    catch_plan)]}. Each stretch gets "base_u" / "base" (BPM) = the tempo heard just before it, each Keep timing
    part its heard us the same way: inside a stretch, its tempo; inside a Keep timing part, its; else the line."""
    key = (json.dumps(tempo), base, ppq)
    if key in _layouts:
        return _layouts[key]
    tempo = json.loads(key[0])  # (its own copy: what's remembered never changes with the line)
    start_u = base_us(base)
    sts = stretches(tempo, base)
    kps = keep_parts(tempo)
    spans = []  # (a, b or None, heard us) in the order they're found
    keeps_at = []
    for i, b, end in kps:  # (ticks by the points' rule: a part starts on the first tick at or after its beat)
        a = beat_tick(b, ppq)
        e = None if end is None else beat_tick(end, ppq)
        if e is None or e > a:
            keeps_at.append((i, a, e))

    def in_keep(t):
        return any(a <= t and (e is None or t < e) for _, a, e in keeps_at)

    # a catch-up inside a Keep timing part paid back to the tempo heard in it (the drawn line there is only for
    # looks): worked out once the Keep timing pieces before its line are known
    inner = {n for n, st in enumerate(sts) if st["read"] is not None and in_keep(beat_tick(st["line"], ppq) - 1)
             and in_keep(beat_tick(st["read"], ppq))}
    todo = sorted([(beat_tick(st["line"] if n in inner else st["from"], ppq), 0, n) for n, st in enumerate(sts)]
                  + [(a, 1, n) for n, (_, a, _) in enumerate(keeps_at)])

    def heard(t):
        if t < 0:
            return start_u
        for a, b, u in reversed(spans):
            if a <= t and (b is None or t < b):
                return u
        u = _segments(tempo, base, start_u, ppq, t, t + 1)[0][2]
        return u if isinstance(u, int) else int(u[0])

    bases, keep_u = [start_u] * len(sts), [start_u] * len(keeps_at)
    for t0, kind, n in todo:
        u = heard(t0 - 1)
        if kind == 0 and sts[n]["read"] is not None and n not in inner:  # (the tempo the line comes back to)
            u = heard(beat_tick(sts[n]["read"], ppq))
        if kind == 0:
            bases[n] = u
            if sts[n]["end"] is not None:
                spans.append((beat_tick(sts[n]["from"], ppq), beat_tick(sts[n]["end"], ppq), u))
        else:
            keep_u[n] = u
            spans.append((t0, keeps_at[n][2], u))
    keeps_out = [(i, a, e, keep_u[n]) for n, (i, a, e) in enumerate(keeps_at)]

    def real(a, b):
        segs = _segments(tempo, base, start_u, ppq, a, b)
        for _, ka, ke, u in keeps_out:
            lo, hi = max(a, ka), min(b, b if ke is None else ke)
            if hi > lo:
                segs = _put(segs, lo, hi, u)
        return segs

    worked = []
    for n, st in enumerate(sts):
        st = dict(st, base_u=bases[n], base=60_000_000 / bases[n])
        worked.append((st, catch_plan(tempo, base, st, ppq, real)))
    if len(_layouts) > 8:
        _layouts.clear()
    _layouts[key] = out = {"start_u": start_u, "keeps": keeps_out, "worked": worked, "real": real}
    return out


def plans(tempo, base, ppq):
    """[(stretch, catch_plan)] for every catch-up line."""
    return layout(tempo, base, ppq)["worked"]


def step_edges(tempo, worked, ppq):
    """[(first tick, end tick)] of every curved stretch: curved parts and curved catch-ups, which change every
    "every" ticks from their first tick (as the file has them)."""
    pts = tempo["points"]
    first = _first_ticks([p[0] for p in pts], ppq)
    out = [(first[i], first[i + 1]) for i in range(len(pts) - 1)
           if pts[i][2] != "hold" and pts[i][1] != pts[i + 1][1] and first[i + 1] > first[i]]
    return out + [(beat_tick(st["line"], ppq), beat_tick(st["end"], ppq)) for st, plan in worked
                  if plan["ok"] and st["shape"] != "flat"]


def stepped(tempo, beats, ppq, edges):
    """beats moved back to the start of their interval step inside the curved stretches (step_edges): the value
    the file has there."""
    beats = np.asarray(beats, float)
    every = tempo.get("every", EVERY)
    if every <= 1:
        return beats
    out = beats.copy()
    tick = np.floor(beats * ppq + 1e-9)
    for a, b in edges:
        on = (tick >= a) & (tick < b)
        out[on] = (a + (tick[on] - a) // every * every) / ppq
    return out


def shown_bpm(tempo, base, beats, worked, ppq=None):
    """What the pane draws: the drawn line with each working catch-up (worked = plans()) in its place; with ppq,
    curved stretches in the interval's steps (user: as jagged as the file)."""
    beats = np.asarray(beats, float)
    at = stepped(tempo, beats, ppq, step_edges(tempo, worked, ppq)) if ppq else beats
    out = drawn_bpm(tempo, base, at)
    for st, plan in worked:
        if plan["ok"]:
            on = (beats >= st["line"]) & (beats < st["end"])
            out[on] = catch_bpm(base, st, plan, at[on] + (0.5 / ppq if ppq else 0))
    return out


# ------------------------------------------------------------ exact tempo changes for a file

def domino_safe(u):
    """True where u (microseconds per beat) comes back unchanged after Domino opens and saves the file."""
    u = np.asarray(u, np.int64)
    bpm = (6e7 / np.maximum(u, 1)).astype(np.float32).astype(np.float64)
    return np.trunc(6e7 / bpm).astype(np.int64) == u


def nearest_safe(x):
    """The Domino-safe whole microseconds nearest x (array of floats), kept in 1..0xFFFFFF."""
    x = np.clip(np.asarray(x, float), 1, 0xFFFFFF)
    u = np.rint(x).astype(np.int64)
    out = np.where(domino_safe(u), u, 0)
    left = out == 0
    for k in range(1, SAFE_REACH):
        if not left.any():
            break
        idx = np.nonzero(left)[0]
        base = u[idx]
        # the nearer side first (x may sit just off u)
        first = np.where(x[idx] >= base, base + k, base - k)
        second = np.where(x[idx] >= base, base - k, base + k)
        for cand in (first, second):
            ok = (cand >= 1) & (cand <= 0xFFFFFF) & domino_safe(np.clip(cand, 1, 0xFFFFFF)) & (out[idx] == 0)
            out[idx[ok]] = cand[ok]
        left = out == 0
    return np.where(out == 0, u, out)


def next_safe(u, way):
    """The next Domino-safe value past u (array) going way (+1 / -1); u itself where there's none in reach."""
    u = np.asarray(u, np.int64)
    out = u.copy()
    left = np.ones(len(u), bool)
    for k in range(1, SAFE_REACH):
        if not left.any():
            break
        cand = u + way * k
        ok = left & (cand >= 1) & (cand <= 0xFFFFFF) & domino_safe(np.clip(cand, 1, 0xFFFFFF))
        out[ok] = cand[ok]
        left &= ~ok
    return out


def _fit_exact(ideal, target):
    """Whole, Domino-safe values near ideal (array, one per tick) adding up to exactly target. None if they can't.
    A flat catch-up comes out as a few runs of one value (few tempo changes), not a new value on every tick."""
    n = len(ideal)
    if n == 0 or not n <= target <= n * 0xFFFFFF:
        return None
    if ideal.max() - ideal.min() < 1:  # a flat catch-up: two values at most (user saw 4 that looked alike)
        got = _flat_pair(n, target)
        if got is not None:
            return got
    ideal = ideal * (target / ideal.sum())
    out = nearest_safe(ideal)
    body = out[:max(0, n - 2)]  # (the last two are picked as a pair at the end, to land exactly)
    for _ in range(8):  # rounding left over: the first ticks step to their next safe value until it's used up
        rest = target - int(body.sum()) - int(np.sum(ideal[len(body):]))
        way = 1 if rest > 0 else -1
        nxt = next_safe(body, way)
        step = np.abs(nxt - body)
        take = np.cumsum(step) <= abs(rest)
        if not take.any():
            break
        body[take] = nxt[take]
    out[:len(body)] = body
    rest = target - int(out[:max(0, n - 2)].sum())
    if n == 1:  # (only a Domino-safe value: else it can't, user's rule)
        out[0] = rest
        return out if 1 <= rest <= 0xFFFFFF and domino_safe([rest])[0] else None
    # the last two: a Domino-safe pair adding up to what's left; none near = it can't
    half = rest / 2
    a0 = int(round(half))
    for k in range(SAFE_REACH):
        for a in (a0 - k, a0 + k):
            b = rest - a
            if 1 <= a <= 0xFFFFFF and 1 <= b <= 0xFFFFFF and domino_safe([a, b]).all():
                out[n - 2], out[n - 1] = a, b
                return out
    return None


def _runs(segs):
    """[(a, b, us)] -> [(tick, us)] only where the value changes."""
    out = []
    for a, b, u in segs:
        if isinstance(u, int):
            vals, ticks = [u], [a]
        else:
            keep = np.concatenate([[True], u[1:] != u[:-1]])
            vals, ticks = u[keep].tolist(), np.arange(a, b)[keep].tolist()
        for t, v in zip(ticks, vals):
            if not out or out[-1][1] != v:
                out.append((t, v))
    return out


def _fit_blocks(ideal, target, every):
    """_fit_exact with a new value only every `every` ticks (user: some programs don't like one per tick): each
    block takes the Domino-safe value nearest what keeps the sum on track (what's off carried on to the next), the
    last block lands exactly with one or two values (_flat_pair). A flat catch-up: two values at most anyway."""
    n = len(ideal)
    if n <= every and every > 1:  # (one block, drawn as one step: one or two values like a flat catch-up)
        got = _flat_pair(n, target) if n <= target <= n * 0xFFFFFF else None
        return got if got is not None else _fit_exact(ideal, target)
    if every <= 1 or ideal.max() - ideal.min() < 1:
        return _fit_exact(ideal, target)
    if not n <= target <= n * 0xFFFFFF:
        return None
    starts = np.arange(0, n, every)
    lens = np.diff(np.append(starts, n))
    want = ideal[starts] * lens
    want = want * (target / want.sum())  # (each block's share of the target)
    out = np.empty(n, np.int64)
    done, owed = 0, 0.0
    for k in range(len(starts) - 1):
        u = int(nearest_safe([(want[k] + owed) / lens[k]])[0])
        out[starts[k]:starts[k] + lens[k]] = u
        owed += want[k] - u * lens[k]
        done += u * lens[k]
    last, rest = lens[-1], target - done
    if not last <= rest <= last * 0xFFFFFF:
        return _fit_exact(ideal, target)
    tail = _flat_pair(int(last), int(rest))
    if tail is None:
        tail = _fit_exact(np.full(int(last), rest / last), int(rest))
        if tail is None:
            return _fit_exact(ideal, target)
    out[starts[-1]:] = tail
    return out


PAIR_REACH = 48  # Domino-safe values tried on each side of a flat catch-up's tempo


def _safe_side(start, way):
    """Up to PAIR_REACH Domino-safe values from start (included when safe) going way (+1 / -1)."""
    out, u = [], int(start) - way
    while len(out) < PAIR_REACH:
        nxt = int(next_safe(np.array([u]), way)[0])
        if nxt == u or not 1 <= nxt <= 0xFFFFFF:
            break
        out.append(nxt)
        u = nxt
    return out


def _flat_pair(n, target):
    """n Domino-safe values adding up to exactly target, all one value or a run of A then a run of B (one or two
    tempo changes); the pair nearest the average first. None if no such pair is near."""
    q = target / n
    downs, ups = _safe_side(math.floor(q), -1), _safe_side(math.ceil(q), 1)
    best = None
    for a in downs:
        for b in ups:
            if b == a:
                if a * n == target:
                    return np.full(n, a, np.int64)
                continue
            k, rest = divmod(n * b - target, b - a)  # (k ticks of a, the rest of b)
            if rest == 0 and 0 <= k <= n:
                far = (q - a) + (b - q)
                if best is None or far < best[0]:
                    best = (far, a, b, k)
    if best is None:
        return None
    _, a, b, k = best
    return np.concatenate([np.full(k, a, np.int64), np.full(n - k, b, np.int64)])


def tempo_events(tempo, base, ppq):
    """The tempo changes a MIDI file gets with no notes to keep in time: ([(tick, microseconds per beat)], [beats of
    catch-up lines that can't work]) (notes/tempo_file.py's file_plan, which the export uses)."""
    from notes.tempo_file import file_plan
    plan = file_plan(tempo, base, ppq)
    return plan["events"], plan["bad"]


def file_segments(tempo, base, ppq, end=0):
    """The file's tempo before Keep timing moves anything, as [(a, b, us)] from tick 0 to at least end, and the
    beats of catch-up lines that can't work. Each stretch with a working catch-up adds up exactly to the time of
    the tempo heard before it; one that can't work leaves its stretch as drawn (heard) and is listed."""
    base_u = base_us(base)
    pts = tempo["points"]
    last = max([p[0] for p in pts] + [x for c in tempo["catch"] for x in (c[0], catch_get(c, 4)) if x is not None])
    end = max(end, beat_tick(last, ppq) + 1)
    segs = _segments(tempo, base, base_u, ppq, 0, end)
    bad = []
    for st, plan in plans(tempo, base, ppq):
        if not plan["ok"]:
            bad.append(st["line"])
            continue
        # (the drawn pieces cut where the catch-up goes; its values worked out with the pane's check)
        segs = _put(segs, beat_tick(st["line"], ppq), beat_tick(st["end"], ppq), plan["fit"])
    return segs, bad


def bpm_text(v):
    v = float(v)
    return f"{v:,.0f}" if v >= 10000 else fmt(round(v, 3))


def short_bpm(v):
    """A BPM to one decimal (user: the ruler stays calm): 133.333 -> 133.3, 120.0 -> 120."""
    v = float(v)
    return f"{v:,.0f}" if v >= 10000 else fmt(round(v, 1))


def ruler_marks(tempo, base, ppq):
    """The tempo changes the piano roll's ruler marks (user 2026-10-22): [(beat, "point", (before, at, then))] for
    each point (before = the BPM just before it, None at beat 0; then = where its part goes, None when it holds)
    and [(beat, "catch", BPM or None when it can't work)] for each catch-up line."""
    if not has_tempo(tempo):
        return []
    key = (json.dumps(tempo), base, ppq)
    if key in _marks:
        return _marks[key]
    worked = plans(tempo, base, ppq)
    pts, lines = tempo["points"], [st["line"] for st, _ in worked]
    out = []
    for i, p in enumerate(pts):
        b = p[0]
        if i + 1 < len(pts) and pts[i + 1][0] == b:  # (where a curve arrives: the next one on its beat says it)
            continue
        before =float(shown_bpm(tempo, base, [b - 1e-9 * max(1.0, b)], worked)[0]) if b > 0 else None
        then = None
        if p[2] != "hold" and i + 1 < len(pts) and pts[i + 1][1] != p[1]:
            cut = [x for x in lines if b < x < pts[i + 1][0]]  # (a catch-up line cuts the part short)
            then = float(drawn_bpm(tempo, base, [min(cut)])[0]) if cut else float(pts[i + 1][1])
        out.append((b, "point", (before, float(p[1]), then)))
    out += [(st["line"], "catch", plan["bpm"] if plan["ok"] else None) for st, plan in worked]
    if len(_marks) > 4:
        _marks.clear()
    _marks[key] = out
    return out


_marks = {}


def bpm_at(tempo, base, ppq, tick):
    """The exact BPM the file holds at a tick (Spiderweb's ticks, before Keep timing moves anything: what the pane
    draws there); the project's BPM without a tempo line."""
    if not has_tempo(tempo):
        return float(base)
    key = (json.dumps(tempo), base, ppq)
    got = _at.get(key)
    if got is None:  # (the play line moves every frame while playing: worked out once per tempo line)
        runs = _runs(file_segments(tempo, base, ppq)[0])
        if len(_at) > 4:
            _at.clear()
        got = _at[key] = ([t for t, _ in runs], [u for _, u in runs])
    ticks, us = got
    return 60_000_000 / us[max(0, bisect.bisect_right(ticks, tick) - 1)]


_at = {}


def file_seconds(events, ppq, tick):
    """Seconds from the start to tick with these tempo changes (exact sums, for tests)."""
    total, last_t, last_u = 0, 0, events[0][1]
    for t, u in events:
        if t >= tick:
            break
        total += (t - last_t) * last_u
        last_t, last_u = t, u
    total += (tick - last_t) * last_u
    return total / ppq / 1e6
