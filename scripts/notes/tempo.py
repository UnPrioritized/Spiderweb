"""The project's tempo line (the Tempo pane, window/tempo_pane.py) and the tempo changes it writes into a MIDI file.

The line is made of points, each an exact BPM at a beat. Each part between two points has a curve (hold, straight,
S-curve, ease out, ease in, or a formula of x from 0 to 1); curves run on the doubling scale the pane shows, so a
straight part from 60 to 240 passes 120 halfway. Before the first point the tempo is the project's BPM, after the
last one it holds the last point's.

A catch-up line starts a catch-up (user 2026-10-21): the drawn tempo holds up to the line, then from the line to the
next point (or catch-up line) Spiderweb works out a tempo so the whole stretch (from where the catch-up before
ended, or the song's start) takes exactly as long as at the project's BPM; the drawn line goes on from that point.
The catch-up's shape: flat, ease back (fast first, settling to the BPM), ramp (up and back down) or a formula. The
notes are never moved: everything after a catch-up is in time.

Saved in the project as "tempo": {"points": [[beat, bpm, curve, formula], ...], "catch": [[beat, shape, formula],
...]}, beats like the shapes (a PPQ change moves nothing).

Exact timing (user's rule: tick and microsecond perfect, not "too small to hear"): a MIDI file's time is whole
numbers, ticks x microseconds per beat, so the catch-up is picked tick by tick to make each stretch's sum exactly
the project BPM's. Every value written also survives Domino opening and saving the file unchanged (domino_safe:
measured 2026-10-21, Domino keeps a tempo as a 32-bit BPM, f32(6e7 / us), and saves trunc(6e7 / bpm): half of all
values lost 1 us each save)."""

import json
import math

import numpy as np

from files.mathexpr import formula

MIN_BPM = 60_000_000 / 0xFFFFFF  # ~3.58: the slowest a MIDI file holds (24-bit microseconds per beat)
MAX_BPM = 60_000_000.0  # 1 microsecond per beat (faster = 0, which breaks the file; Domino writes that past it)
CURVES = ("hold", "line", "s", "out", "in", "formula")
SHAPES = ("flat", "ease", "ramp", "formula")
FORMULA = "x^3"  # a new formula part's / catch-up's starting formula
EMPTY = {"points": [], "catch": []}
FAR = 1e7  # beats: past this a point is left out (as shapes' points, engine.BEATS_FAR)
SAFE_REACH = 8192  # the farthest a Domino-safe value is from any value (measured: 5212 at the slowest tempos)


def empty():
    return {"points": [], "catch": []}


def has_tempo(tempo):
    return bool(tempo and tempo.get("points"))


def clamp_bpm(v):
    return min(MAX_BPM, max(MIN_BPM, float(v)))


def clean_tempo(data):
    """A tempo line from a file / undo step -> a safe one (broken points left out, sorted, one point per beat)."""
    out = empty()
    if not isinstance(data, dict):
        return out
    seen = set()
    for p in data.get("points", []) if isinstance(data.get("points"), list) else []:
        try:
            b, v = float(p[0]), float(p[1])
            kind = p[2] if len(p) > 2 and p[2] in CURVES else "hold"
            text = p[3] if len(p) > 3 and isinstance(p[3], str) else ""
        except (TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(b) and math.isfinite(v)) or not 0 <= b <= FAR or v <= 0:
            continue
        key = round(b, 9)
        if key in seen:
            continue
        seen.add(key)
        out["points"].append([b, clamp_bpm(v), kind, text[:200]])
    out["points"].sort(key=lambda p: p[0])
    seen = set()
    for c in data.get("catch", []) if isinstance(data.get("catch"), list) else []:
        try:
            b = float(c[0])
            shape = c[1] if len(c) > 1 and c[1] in SHAPES else "flat"
            text = c[2] if len(c) > 2 and isinstance(c[2], str) else ""
        except (TypeError, ValueError, IndexError):
            continue
        if not math.isfinite(b) or not 0 < b <= FAR or round(b, 9) in seen:
            continue
        seen.add(round(b, 9))
        out["catch"].append([b, shape, text[:200]])
    out["catch"].sort(key=lambda c: c[0])
    return out


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


def curve(kind, text, t):
    """How far along (0 at the part's start point, 1 at its end) a part of this curve is at t (0..1, array)."""
    t = np.asarray(t, float)
    if kind == "line":
        return t
    if kind == "s":
        return t * t * (3 - 2 * t)
    if kind == "out":
        return 1 - (1 - t) ** 2
    if kind == "in":
        return t * t
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


def catch_weight(shape, text, t):
    """The catch-up's shape at t (0..1): how much of its push each moment gets (0 = the project's BPM). Flat = the
    same all along; ease back = most at the start, none at the end; ramp = up and back down."""
    t = np.asarray(t, float)
    if shape == "ease":
        return (1 - t) ** 2
    if shape == "ramp":
        return np.sin(np.pi * t)
    if shape == "formula":
        return _apply(formula_fn(text), t)
    return np.ones_like(t)


# ------------------------------------------------------------ the drawn line and its stretches

def stretches(tempo, base):
    """The catch-up lines' stretches: [{"from": beat the stretch starts (where the one before ended, or 0), "line":
    the line's beat = where the catch-up starts, "end": where it ends (the next point or catch-up line after it;
    None when there's none), "shape", "text"}]."""
    out, prev = [], 0.0
    pts = [p[0] for p in tempo["points"]]
    lines = [c[0] for c in tempo["catch"]]
    for k, (b, shape, text) in enumerate(tempo["catch"]):
        after = [x for x in pts if x > b] + lines[k + 1:k + 2]
        end = min(after) if after else None
        out.append({"from": prev, "line": b, "end": end, "shape": shape, "text": text})
        if end is not None:
            prev = end
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
    lv = np.log2(np.array([p[1] for p in pts]))
    idx = np.searchsorted(pb, beats, "right") - 1  # the point at or before each beat (-1: before the first)
    has = idx >= 0
    out[has] = 2 ** lv[idx[has]]
    for i in range(len(pts) - 1):
        kind = pts[i][2]
        if kind == "hold" or pts[i][1] == pts[i + 1][1] or pb[i + 1] <= pb[i]:
            continue
        on = idx == i
        if on.any():
            t = (beats[on] - pb[i]) / (pb[i + 1] - pb[i])
            out[on] = 2 ** (lv[i] + (lv[i + 1] - lv[i]) * curve(kind, pts[i][3], t))
    return np.clip(out, MIN_BPM, MAX_BPM)


def _solve_push(shape, text, beats, seconds, base, n=512):
    """The push A that makes a catch-up of this shape (BPM = base x 2^(A x weight)) last `seconds` over `beats`.
    None when it can't (a shape with no push anywhere, or past what a MIDI file can hold)."""
    w = catch_weight(shape, text, (np.arange(n) + 0.5) / n)
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
        else:
            bpm = drawn_bpm(tempo, base, np.arange(a, b) / ppq)
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


def catch_plan(tempo, base, st, ppq):
    """A stretch's catch-up worked out in the file's own whole numbers (ticks x microseconds), so the pane and the
    export always agree: {"ok", "push", "bpm" (flat: its BPM; else its fastest / slowest), "target" (the catch-up's
    ticks x microseconds), "latest": the last beat the line could sit at (shown when it can't), "empty": nothing
    after the line to end on}."""
    out = {"ok": False, "push": None, "bpm": None, "target": None, "latest": None, "empty": st["end"] is None}
    if st["end"] is None:
        return out
    base_u = round(60_000_000 / base)
    s0, ln, e = (int(round(st[k] * ppq)) for k in ("from", "line", "end"))
    n = e - ln
    if n <= 0:
        return out
    total = (e - s0) * base_u
    target = total - _us_sum(_segments(tempo, base, base_u, ppq, s0, ln))
    latest = _latest_tick(_segments(tempo, base, base_u, ppq, s0, e), total, e)
    out["latest"] = None if latest is None else latest / ppq
    push = None
    if n <= target <= n * 0xFFFFFF:
        push = _solve_push(st["shape"], st["text"], n / ppq, target / ppq / 1e6, base)
    if push is None:
        return out
    w = catch_weight(st["shape"], st["text"], np.linspace(0, 1, 257))
    top = base * 2 ** float((push * w).max() if push > 0 else (push * w).min())
    out.update(ok=True, push=push, bpm=clamp_bpm(top), target=target)
    return out


def catch_bpm(base, st, plan, beats):
    """The catch-up's BPM at beats inside its part (for drawing)."""
    t = (np.asarray(beats, float) - st["line"]) / max(1e-12, st["end"] - st["line"])
    w = catch_weight(st["shape"], st["text"], np.clip(t, 0, 1))
    return np.clip(base * 2 ** (plan["push"] * w), MIN_BPM, MAX_BPM)


_plans = {}


def plans(tempo, base, ppq):
    """[(stretch, catch_plan)] for every catch-up line (the last few remembered: the pane asks often)."""
    key = (json.dumps(tempo), base, ppq)
    if key not in _plans:
        if len(_plans) > 8:
            _plans.clear()
        _plans[key] = [(st, catch_plan(tempo, base, st, ppq)) for st in stretches(tempo, base)]
    return _plans[key]


def shown_bpm(tempo, base, beats, worked):
    """What the pane draws: the drawn line with each working catch-up (worked = plans()) in its place."""
    beats = np.asarray(beats, float)
    out = drawn_bpm(tempo, base, beats)
    for st, plan in worked:
        if plan["ok"]:
            on = (beats >= st["line"]) & (beats < st["end"])
            out[on] = catch_bpm(base, st, plan, beats[on])
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
    if n == 1:
        out[0] = rest
        return out if 1 <= rest <= 0xFFFFFF else None
    # the last two: a Domino-safe pair adding up to what's left (exact first; a value that isn't safe only if no
    # safe pair is near)
    half = rest / 2
    a0 = int(round(half))
    for k in range(SAFE_REACH):
        for a in (a0 - k, a0 + k):
            b = rest - a
            if 1 <= a <= 0xFFFFFF and 1 <= b <= 0xFFFFFF and domino_safe([a, b]).all():
                out[n - 2], out[n - 1] = a, b
                return out
    a = max(1, min(0xFFFFFF, a0))
    b = rest - a
    if not 1 <= b <= 0xFFFFFF:
        return None
    out[n - 2], out[n - 1] = a, b
    return out


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


def tempo_events(tempo, base, ppq):
    """The tempo changes a MIDI file gets: ([(tick, microseconds per beat)], [beats of catch-up lines that can't
    work]). Tick 0 is always the project's BPM (as files without a tempo line). Each stretch with a working
    catch-up adds up exactly to the project BPM's time; a catch-up that can't work leaves its stretch as drawn
    (heard) and is listed."""
    base_u = round(60_000_000 / base)
    if not has_tempo(tempo):
        return [(0, base_u)], []
    pts = tempo["points"]
    last = max([p[0] for p in pts] + [c[0] for c in tempo["catch"]])
    end = int(round(last * ppq)) + 1
    segs = _segments(tempo, base, base_u, ppq, 0, end)
    bad = []
    for st, plan in plans(tempo, base, ppq):
        if not plan["ok"]:
            bad.append(st["line"])
            continue
        ln, e = int(round(st["line"] * ppq)), int(round(st["end"] * ppq))
        n = e - ln
        t = (np.arange(n) + 0.5) / n
        ideal = 6e7 / np.clip(base * 2 ** (plan["push"] * catch_weight(st["shape"], st["text"], t)),
                              MIN_BPM, MAX_BPM)
        got = _fit_exact(ideal, plan["target"])
        if got is None:
            bad.append(st["line"])
            continue
        # (the drawn pieces cut where the catch-up goes)
        cut = []
        for a, b, u in segs:
            for lo, hi in ((a, min(b, ln)), (max(a, e), b)):
                if hi > lo:
                    cut.append((lo, hi, u if isinstance(u, int) else u[lo - a:hi - a]))
        segs = sorted(cut + [(ln, e, got)], key=lambda s: s[0])
    events = _runs(segs)
    if events[0][0] != 0:
        events.insert(0, (0, base_u))
    return events, bad


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
