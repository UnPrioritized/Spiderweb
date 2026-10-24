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
HEARD TEMPO LINE (user 2026-10-24; point[7], missing = none): {"line": points like the tempo line's (no Keep
timing, no line of their own), "visual": True = "Keep the visual" picked}. Inside the part the sound follows that
line (beats are the song's, so a part cut in two keeps reading the same line); before its first point the tempo
heard just before the part, as without one.

Saved in the project as "tempo": {"points": [[beat, bpm, curve, formula, "lin" / "exp", keep timing, bend, heard
line?], ...],
"catch": [[beat, shape, formula, start, end, bend], ...], "every": ticks, "bars": Fit bar lines}, beats like the shapes (a PPQ change
moves nothing).

Exact timing (user's rule: tick and microsecond perfect, not "too small to hear"): a MIDI file's time is whole
numbers, ticks x microseconds per beat, so the catch-up is picked tick by tick to make each stretch's sum exactly
the project BPM's. Every value written also survives Domino opening and saving the file unchanged (domino_safe:
measured 2026-10-21, Domino keeps a tempo as a 32-bit BPM, f32(6e7 / us), and saves trunc(6e7 / bpm): half of all
values lost 1 us each save)."""

import bisect
import copy
import functools
import json
import math
import os
import re
import struct

import numpy as np

from files.mathexpr import fmt, formula
from notes.meter import DEFAULT as DEFAULT_SIG, clean_keysigs, clean_sigs

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
    return {"points": [], "catch": [], "every": EVERY, "bars": True, "sigs": [list(DEFAULT_SIG)], "keysigs": []}


def has_tempo(tempo):
    return bool(tempo and tempo.get("points"))


# The app's tempo line as text (the key of every remembered tempo result, and the undo steps' copy): made once per
# change, not 14 times a redraw (13 k tempos from a loaded MIDI = 7-11 ms each). Only the line watch() was given
# is remembered; App.tempo_changed / a new line call changed(). SPIDERWEB_TEMPO_CHECK=1 (tests) = every reuse
# checked against the line itself (an edit that didn't say so raises).
_text = {"tempo": None, "ver": 0, "made": -1, "text": None}
_CHECK = bool(os.environ.get("SPIDERWEB_TEMPO_CHECK"))


def watch(tempo):
    _text["tempo"] = tempo
    changed()


def changed():
    _text["ver"] += 1


def tempo_text(tempo):
    """json.dumps(tempo), remembered for the watched line until it changes (the same string each time, so undo
    steps share it)."""
    if tempo is not _text["tempo"] or tempo is None:
        return json.dumps(tempo)
    if _text["made"] != _text["ver"]:
        text = json.dumps(tempo)
        if text != _text["text"]:  # (the same text = the same string kept: undo steps share it)
            _text["text"] = text
        _text["made"] = _text["ver"]
    elif _CHECK and json.dumps(tempo) != _text["text"]:
        import sys
        import traceback
        sys.stderr.write("TEMPO STALE\n" + "".join(traceback.format_stack(limit=8)[:-1]))
        _text["text"] = json.dumps(tempo)
    return _text["text"]


def clamp_bpm(v):
    return min(MAX_BPM, max(MIN_BPM, float(v)))


def clean_tempo(data):
    """A tempo line from a file / undo step -> a safe one (broken points left out, sorted, at most two points per
    beat: where a curve arrives, then the tempo from there; user 2026-10-22)."""
    out = empty()
    if not isinstance(data, dict):
        return out
    out["points"] = _clean_points(data.get("points"))
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
    out["sigs"] = clean_sigs(data.get("sigs"))
    out["keysigs"] = clean_keysigs(data.get("keysigs"))
    return out


def _clean_points(raw, inner=False):
    """A tempo line's points cleaned (clean_tempo); inner = a heard tempo line's (no Keep timing, no line of
    their own)."""
    pts = []
    seen = {}
    for p in raw if isinstance(raw, list) else []:
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
        # (formulas up to 4000 letters: a part cut by a paste writes its formula again, longer each time)
        pt = [b, clamp_bpm(v), kind, text[:4000], scale, keep and not inner, bend]
        heard = None if inner or len(p) < 8 else clean_heard(p[7])
        if heard:
            pt.append(heard)
        pts.append(pt)
    pts.sort(key=lambda p: p[0])  # (stable: a pair keeps its order)
    for i in range(len(pts) - 1):  # (a pair on exactly one beat, not a hair apart)
        p, q = pts[i:i + 2]
        if round(p[0], 9) == round(q[0], 9):
            q[0] = p[0]
    drop_loose(pts)
    return pts


def clean_heard(h):
    """A part's heard tempo line (point[7]) from a file / undo step -> a safe one, None when it has nothing."""
    if not isinstance(h, dict):
        return None
    out = {"line": _clean_points(h.get("line"), inner=True)}
    if h.get("visual") is True:
        out["visual"] = True
    end = h.get("end")
    if isinstance(end, (int, float)) and not isinstance(end, bool) and math.isfinite(end) and end > 0:
        out["end"] = clamp_bpm(end)
    return out if out["line"] or "visual" in out or "end" in out else None


def heard_of(p):
    """A point's heard tempo line (point[7]: {"line", "visual"?}), None without one."""
    return p[7] if len(p) > 7 and p[7] else None


def shift_heard(p, db, floor=0.0):
    """Point p's heard tempo line moved db beats (in place; it's in song beats: a copied / pasted / moved part takes
    it along, hunt 2026-10-24); never before floor (None: a copy's own beats, may be below 0)."""
    for q in heard_line(p):
        q[0] = q[0] + db if floor is None else max(floor, q[0] + db)


def heard_line(p):
    """A Keep timing part's own heard tempo points ([] = it plays the tempo heard just before it all along)."""
    h = heard_of(p)
    return h["line"] if h else []


def drop_loose(pts):
    """A jump's first point (where a glide arrives) taken out, in place, when no curved part arrives at it any more
    (held / deleted / dragged after a held part) or it is the same BPM as the second: it played nothing and
    couldn't be used (user 2026-10-22). True when one went."""
    gone = False
    i = 0
    while i + 1 < len(pts):
        p, q = pts[i], pts[i + 1]
        if p[0] == q[0]:
            before = pts[i - 1] if i > 0 else None
            glides = before is not None and before[2] != "hold" and before[1] != p[1] and before[0] < p[0]
            if not glides or p[1] == q[1]:
                del pts[i]
                gone = True
                continue
        i += 1
    return gone


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


def set_bend(p, bend):
    """Point p's part bend set (its heard tempo line after it kept)."""
    p.extend([False] * (6 - len(p)) + [None] * (len(p) < 7))
    p[6] = bend


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
    for i in np.unique(idx[has & (idx < len(pts) - 1)]).tolist():  # (only the parts asked about: a song with
        kind = pts[i][2]  # thousands of points read part by part was slow, hunt 2026-10-22)
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


def held_us(bpm):
    """A held part's microseconds per beat: a BPM that is exactly a file's whole microseconds (a loaded MIDI's own
    tempo, loaded.conductor_line) keeps them as they are (user 2026-10-24: the file's values stay); any other the
    nearest Domino-safe value (Spiderweb's own additions follow Domino)."""
    u = round(60_000_000 / bpm)
    if 1 <= u <= 0xFFFFFF and 60_000_000 / u == bpm:
        return u
    return int(nearest_safe([60_000_000 / bpm])[0])


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
            out.append((a, b, base_u if bpm == base else held_us(bpm)))
        else:  # (a new value every "every" ticks from the part's first tick; the first step the start point's
            # BPM, the last the end point's: step_shares)
            every = tempo.get("every", EVERY)
            part = (edges[k], edges[k + 1])
            jump = to_jump(pts, i)

            def read(starts):
                if jump:
                    return part_bpm(tempo, i, step_shares(*part, starts, every))
                return drawn_bpm(tempo, base, np.asarray(starts) / ppq)
            if every >= BLOCKS_FROM:  # (one entry per step, not per tick: long songs at a high PPQ stay quick)
                starts = np.arange(edges[k] + (a - edges[k]) // every * every, b, every)
                bpm = read(starts)
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
            bpm = read(edges[k] + (ticks - edges[k]) // every * every)
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


def _put_all(segs, ra, rb, news):
    """segs with ticks ra..rb replaced by news ([(a, b, us)] covering ra..rb, e.g. a heard tempo line's)."""
    out = []
    for a, b, u in segs:
        for lo, hi in ((a, min(b, ra)), (max(a, rb), b)):
            if hi > lo:
                out.append((lo, hi, u if isinstance(u, int) else u[lo - a:hi - a]))
    return sorted(out + [s for s in news if s[1] > s[0]], key=lambda s: s[0])


def catch_plan(tempo, base, st, ppq, real=None):
    """A stretch's catch-up worked out in the file's own whole numbers (ticks x microseconds), so the pane and the
    export always agree: {"ok", "push", "bpm" (flat: its BPM; else its fastest / slowest), "target" (the catch-up's
    ticks x microseconds), "latest": the last beat the line could sit at (shown when it can't), "empty": nothing
    after the line to end on}. The whole stretch takes as long as at st["base_u"] (the tempo heard just before
    it; the project's BPM when not given). real(a, b) = the time ticks a..b really take (Keep timing parts at
    their heard tempo); the drawn line when not given."""
    out = {"ok": False, "push": None, "bpm": None, "target": None, "latest": None, "empty": st["end"] is None,
           "fit": None, "why": "no_end"}  # (why: what stops it, for the pane's "Not possible" text; see WHY)
    if st["end"] is None:
        return out
    start_u = base_us(base)
    base_u = st.get("base_u") or start_u
    if real is None:
        def real(a, b):
            return _segments(tempo, base, start_u, ppq, a, b)
    s0, ln, e = (beat_tick(st[k], ppq) for k in ("from", "line", "end"))
    if e - ln <= 0:
        out["why"] = "short"
        return out
    total = (e - s0) * base_u
    bpm = 60_000_000 / base_u
    every = tempo.get("every", EVERY)
    why = [None]

    def attempt(line):
        """(push, ticks' values) with the catch-up starting at tick line; None when it can't be written (why[0]
        says what stopped it)."""
        n, target = e - line, total - _us_sum(real(s0, line))
        if not n <= target <= n * SLOWEST:
            why[0] = "too_fast" if target < n else "too_slow"
            return None
        push = _solve_push(st, n / ppq, target / ppq / 1e6, bpm)
        if push is None:
            w = catch_weight(st["shape"], st["text"], (np.arange(512) + 0.5) / 512, st.get("bend"))
            why[0] = "shape" if not np.any(w > 1e-9) or np.any(w < -1e-9) else "peak"
            return None
        t = (np.arange(n) + 0.5) / n
        ideal = 6e7 / np.clip(bpm * 2 ** (push * catch_weight(st["shape"], st["text"], t, st.get("bend"))), MIN_BPM, MAX_BPM)
        fit = _fit_blocks(ideal, target, every)  # (the file's own values: the pane says OK only when it works)
        if fit is None:
            why[0] = "fit"
            return None
        return push, fit, target

    got = attempt(ln)
    out["why"] = why[0]
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


def keep_off(tempo, sts):
    """[(first, end beat)] of the catch-ups' yellow areas a Keep timing part is switched off in (user 2026-10-24: Keep
    timing doesn't work inside a catch-up's yellow area; it played the tempo the catch-up comes back to and could
    undo the catch-up). A catch-up whose line is inside a Keep timing part is that part's own: not these."""
    pts = tempo["points"]
    parts = [(p[0], pts[i + 1][0] if i + 1 < len(pts) else math.inf) for i, p in enumerate(pts) if keeps(p)]
    return [(st["from"], st["line"]) for st in sts if st["end"] is not None and st["from"] < st["line"]
            and not any(a <= st["line"] - 1e-9 < e for a, e in parts)]


def keep_parts(tempo, sts=()):
    """[(point index, first beat, end beat or None = on to the song's end)] of the Keep timing parts, without the
    catch-ups' own areas (line to end: a catch-up never moves notes) and the yellow areas of sts (stretches) they're
    off in (keep_off), so a part may come in pieces."""
    pts = tempo["points"]
    spans = [(c[0], catch_end(tempo, k)) for k, c in enumerate(tempo["catch"])] + keep_off(tempo, sts)
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
    key = (tempo_text(tempo), base, ppq)
    if key in _layouts:
        return _layouts[key]
    tempo = json.loads(key[0])  # (its own copy: what's remembered never changes with the line)
    start_u = base_us(base)
    sts = stretches(tempo, base)
    kps = keep_parts(tempo, sts)
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

    lines = [{"points": heard_line(tempo["points"][i]), "every": tempo.get("every", EVERY)} for i, _, _ in keeps_at]

    def keep_segs(n, lo, hi):
        """Keep timing piece n's heard tempo over ticks lo..hi: its heard line, before that line's first point
        (or without one) the tempo heard just before the piece."""
        u = keep_u[n]
        if not lines[n]["points"]:
            return [(lo, hi, u)] if hi > lo else []
        return _segments(lines[n], 60_000_000 / u, u, ppq, lo, hi)

    def heard(t):
        if t < 0:
            return start_u
        for a, b, u, n in reversed(spans):
            if a <= t and (b is None or t < b):
                if n is not None and lines[n]["points"]:
                    u = keep_segs(n, t, t + 1)[0][2]
                    return u if isinstance(u, int) else int(u[0])
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
                spans.append((beat_tick(sts[n]["from"], ppq), beat_tick(sts[n]["end"], ppq), u, None))
        else:
            keep_u[n] = u
            spans.append((t0, keeps_at[n][2], u, n))
    keeps_out = [(i, a, e, keep_u[n]) for n, (i, a, e) in enumerate(keeps_at)]

    def real(a, b):
        segs = _segments(tempo, base, start_u, ppq, a, b)
        for n, (_, ka, ke, u) in enumerate(keeps_out):
            lo, hi = max(a, ka), min(b, b if ke is None else ke)
            if hi > lo:
                segs = _put_all(segs, lo, hi, keep_segs(n, lo, hi))
        return segs

    worked = []
    for n, st in enumerate(sts):
        st = dict(st, base_u=bases[n], base=60_000_000 / bases[n])
        worked.append((st, catch_plan(tempo, base, st, ppq, real)))
    if len(_layouts) > 8:
        _layouts.clear()
    _layouts[key] = out = {"start_u": start_u, "keeps": keeps_out, "worked": worked, "real": real,
                           "keep_segs": keep_segs, "own_line": [bool(ln["points"]) for ln in lines]}
    return out


def keep_heard(lay, n, lo, hi):
    """layout()'s Keep timing piece n's heard tempo over ticks lo..hi: one whole number (no heard line, or it holds
    there) or an int64 array, one per tick."""
    segs = lay["keep_segs"](n, lo, hi)
    if len(segs) == 1 and isinstance(segs[0][2], int):
        return segs[0][2]
    out = np.empty(hi - lo, np.int64)
    for a, b, u in segs:
        out[a - lo:b - lo] = u
    return out


def keep_runs(tempo):
    """[(first, last point index)] of the Keep timing runs: parts in a row with Keep timing and one heard tempo line
    (a part cut in two keeps reading the same line), each edited as one in the Conductor pane."""
    pts, out = tempo["points"], []
    for i, p in enumerate(pts):
        if not keeps(p):
            continue
        if out and out[-1][1] == i - 1 and json.dumps(heard_of(pts[i - 1])) == json.dumps(heard_of(p)):
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def heard_before(tempo, base, ppq, i, j=None):
    """The BPM heard just before the Keep timing run of points i..j (j: i alone): what its heard line plays before
    its first point."""
    for k, _, _, u in layout(tempo, base, ppq)["keeps"]:
        if i <= k <= (i if j is None else j):
            return 60_000_000 / u
    return float(base)


def keep_end_bpm(tempo, base, ppq, i):
    """The BPM point i's Keep timing part's heard line arrives at on its end (its own value, not the interval's last
    step; None: it runs to the song's end, or a catch-up ends it)."""
    pts = tempo["points"]
    if i + 1 >= len(pts):
        return None
    lay = layout(tempo, base, ppq)
    et = beat_tick(pts[i + 1][0], ppq)
    for k, ta, te, u in lay["keeps"]:
        if k == i and te == et and te > ta:
            hl = copy.deepcopy(heard_line(pts[i]))  # (before its first point: the tempo heard before the piece)
            split_at(hl, pts[i + 1][0], 60_000_000 / u)
            return next(q[1] for q in hl if abs(q[0] - pts[i + 1][0]) < 1e-9)
    return None


def end_point(tempo, base, ppq, i, force=False):
    """Keep timing turned on (or swapped) for point i's part: the tempo after it starts at the BPM heard at its end,
    so the sound goes on smoothly (user 2026-10-24: they put that point by hand; it can be deleted). Nothing when
    the part runs to the song's end, the part after keeps timing too, the line is there already or the drawn line
    jumps there already (a jump the user made stays, user 2026-10-24: their point after it moved; force: anyway).
    True when it changed something."""
    pts = tempo["points"]
    changed()
    v = keep_end_bpm(tempo, base, ppq, i)
    if v is None:
        return False
    v = clamp_bpm(v)
    on = [k for k, q in enumerate(pts) if abs(q[0] - pts[i + 1][0]) < 1e-9]
    q, p = pts[on[-1]], pts[i]
    if keeps(q) or abs(q[1] - v) <= 1e-9 * v:
        return False
    arrive = pts[on[0]][1] if len(on) == 2 or p[2] != "hold" else p[1]
    if not force and abs(arrive - q[1]) > 1e-9 * q[1]:
        return False
    run = [i]  # (the run's parts remember it: later heard edits move it along while it's still this, hunt)
    while run[0] > 0 and keeps(pts[run[0] - 1]) and json.dumps(heard_of(pts[run[0] - 1])) == json.dumps(heard_of(p)):
        run.insert(0, run[0] - 1)
    for k in run:
        m = pts[k]
        if not heard_of(m):
            m.extend([False] * (6 - len(m)) + [list(BEND)] * (len(m) < 7))
            del m[7:]
            m.append({"line": []})
        m[7]["end"] = v
    if len(on) == 1 and p[2] != "hold" and p[1] != q[1]:  # (a glide arrives: it stays, the tempo jumps from there)
        pts.insert(on[0] + 1, [q[0], v] + copy.deepcopy(q[2:]))
    else:
        if len(on) == 1 and p[2] != "hold":  # (a "curve" between two equal BPMs: flat, kept flat)
            p[2] = "hold"
        q[1] = v
    changed()
    return True


def swap_part(tempo, base, ppq, i, j=None):
    """Keep the audio <-> Keep the visual on the Keep timing run of points i..j (j: i alone; user 2026-10-24): its
    drawn line and its heard line swap places (a flat heard line = the tempo heard just before it), the "visual"
    mark flips, then the tempo after it starts where the new heard line ends (end_point). Returns the run's last
    point's index."""
    pts = tempo["points"]
    j = i if j is None else j
    p = pts[i]
    a = p[0]
    e = pts[j + 1][0] if j + 1 < len(pts) else None
    kb = clamp_bpm(heard_before(tempo, base, ppq, i, j))
    was = keep_end_bpm(tempo, base, ppq, j)  # (the sound smooth after it before: smooth after the swap too)
    smooth = was is not None and abs(pts[[k for k, q in enumerate(pts) if abs(q[0] - e) < 1e-9][-1]][1] - was) \
        <= 1e-9 * was
    h = heard_of(p) or {"line": []}
    hl = copy.deepcopy(h["line"])  # (the heard line over the part, cut at its ends: the same line)
    split_at(hl, a, kb)
    if e is not None:
        split_at(hl, e, kb)
    first = [q for q in hl if abs(q[0] - a) < 1e-9][-1]
    inner = [q for q in hl if a + 1e-9 < q[0] and (e is None or q[0] < e - 1e-9)]
    arrive = next((q[1] for q in hl if e is not None and abs(q[0] - e) < 1e-9), None)
    # the drawn line over the run becomes the heard line
    line = [[q[0], q[1], q[2], q[3], q[4], False, list(bend_of(q))] for q in pts[i:j + 1]]
    q = pts[j]
    if e is not None and q[2] != "hold" and q[1] != pts[j + 1][1]:
        line.append([e, pts[j + 1][1], "hold", "", "lin", False, list(BEND)])
    drop_loose(line)
    if len(line) == 1 and p[2] == "hold" and abs(p[1] - kb) <= 1e-6 * kb:
        line = []  # (flat at the tempo heard before: follows it, as without a line)
    new = {"line": line}
    if not h.get("visual"):
        new["visual"] = True
    # the heard line over the part becomes the drawn one
    p[1], p[2:5] = first[1], copy.deepcopy(first[2:5])
    set_bend(p, list(bend_of(first)))
    p[5] = True
    del p[7:]
    if new["line"] or "visual" in new:
        p.append(new)
    mids = [[q[0], q[1], q[2], q[3], q[4], True, list(bend_of(q))] + ([copy.deepcopy(new)] if len(p) > 7 else [])
            for q in inner]
    pts[i + 1:j + 1] = mids
    last = i + len(mids)
    if e is not None:
        on = [k for k, q in enumerate(pts) if abs(q[0] - e) < 1e-9]
        lp = pts[last]
        if lp[2] != "hold" and arrive is not None:  # (it glides into e: arrives at the heard line's BPM there)
            if len(on) == 2:
                pts[on[0]][1] = arrive
            elif abs(pts[on[0]][1] - arrive) > 1e-9:
                pts.insert(on[0], [e, arrive, "hold", "", "lin", False, list(BEND)])
    lp = pts[last]
    drop_loose(pts)  # (an arrival on a beat nothing glides into any more: gone, so the index found again)
    last = next(k for k, q in enumerate(pts) if q is lp)
    end_point(tempo, base, ppq, last, force=smooth)
    return last


def plans(tempo, base, ppq):
    """[(stretch, catch_plan)] for every catch-up line."""
    return layout(tempo, base, ppq)["worked"]


def step_edges(tempo, worked, ppq):
    """[(first tick, end tick, first beat, end beat, ends at a jump)] of every curved stretch: curved parts (their
    points' beats) and curved catch-ups (None, None, False), which change every "every" ticks from their first tick
    (as the file has them). Remembered for the line (a loaded MIDI's thousands of points: every redraw)."""
    pts = tempo["points"]
    key = (tempo_text(tempo), ppq)
    got = _edges.get(key)
    if got is None:
        first = _first_ticks([p[0] for p in pts], ppq)
        got = [(first[i], first[i + 1], pts[i][0], pts[i + 1][0], to_jump(pts, i)) for i in range(len(pts) - 1)
               if pts[i][2] != "hold" and pts[i][1] != pts[i + 1][1] and first[i + 1] > first[i]]
        if len(_edges) > 4:
            _edges.clear()
        _edges[key] = got
    return got + [(beat_tick(st["line"], ppq), beat_tick(st["end"], ppq), None, None, False) for st, plan in worked
                  if plan["ok"] and st["shape"] != "flat"]


_edges = {}


def step_shares(a, b, starts, every):
    """A curved part ending at a jump (ticks a..b; its arrival point is never played): how far along it each step
    starting at tick `starts` reads the line, spread so the last step is the end BPM exactly (user 2026-10-22).
    Other parts read the line at each step's own start (the next point plays the end BPM): two lines in a row stay
    smooth (user: a doubled step at every joint when every part read both its ends)."""
    n = -(-(b - a) // every)
    j = (np.asarray(starts) - a) // every
    return j / (n - 1) if n > 1 else 0.0 * j


def to_jump(pts, i):
    """Point i's part ends where a jump's two points share a beat."""
    return i + 2 < len(pts) and pts[i + 2][0] == pts[i + 1][0]


def split_at(pts, b, base):
    """A point put on beat b (in place) so the tempo line looks exactly the same (user 2026-10-22: a paste / cut
    must leave the tempo around it as it was): the part there cut in two (hold / line / S-curve exact, an S-curve
    becoming its two bent halves; a formula part's formula written again for each piece), before the first point
    the project's BPM held, after the last one its BPM. Nothing when a point is on b already. Returns the point on
    b (the last one there). (Curved parts change every "every" ticks from their own start: each piece starts
    its steps again.)"""
    on = [p for p in pts if abs(p[0] - b) < 1e-9]
    if on:
        return on[-1]
    i = bisect.bisect_left([p[0] for p in pts], b) - 1
    if i < 0:  # (before the first point: the project's BPM)
        new = [float(b), clamp_bpm(base), "hold", "", "lin", False, list(BEND)]
        pts.insert(0, new)
        return new
    p = pts[i]
    if i + 1 >= len(pts):  # (after the last point: it holds)
        new = [float(b), p[1]] + copy.deepcopy(p[2:])
        pts.insert(i + 1, new)
        return new
    if p[2] == "s":  # (its two halves: a bent line, then the same bend mirrored)
        cx, cy = bend_of(p)
        q = pts[i + 1]
        mid = [(p[0] + q[0]) / 2, float(_along(p, q, 0.5)), "line", "", p[4], keeps(p), [1 - cx, 1 - cy]]
        if heard_of(p):
            mid.append(copy.deepcopy(p[7]))
        p[2] = "line"
        set_bend(p, [cx, cy])
        pts.insert(i + 1, mid)
        if abs(mid[0] - b) < 1e-9:
            return mid
        if mid[0] < b:
            i, p = i + 1, mid
    q = pts[i + 1]
    f = (b - p[0]) / (q[0] - p[0])
    new = [float(b), p[1]] + copy.deepcopy(p[2:])
    if p[2] == "line":
        cx, cy = bend_of(p)
        a = 1 - 2 * cx
        s = f if abs(a) < 1e-9 else (-2 * cx + math.sqrt(max(4 * cx * cx + 4 * a * f, 0))) / (2 * a)
        s = min(1.0, max(0.0, s))
        y = 2 * s * (1 - s) * cy + s * s  # (the curve's own place there: B(s) = (f, y))
        new[1] = float(_along(p, q, y))
        lx, ly = s * cx, s * cy  # (de Casteljau: the left piece's control point, then the right one's)
        rx, ry = (1 - s) * cx + s, (1 - s) * cy + s
        set_bend(p, [_unit(lx / f if f > 0 else 0.5), _unit(ly / y if y > 0 else 0.5)])
        new[6] = [_unit((rx - f) / (1 - f) if f < 1 else 0.5), _unit((ry - y) / (1 - y) if y < 1 else 0.5)]
    elif p[2] == "formula":
        ff = float(_apply(formula_fn(p[3]), np.array([f]))[0])
        new[1] = float(_along(p, q, ff))
        x = re.compile(r"\bx\b")
        text = "(" + (p[3] or FORMULA) + ")"
        # (each piece: the same curve read again, scaled to its own start and end BPM: the part's BPM is
        # start + (end - start) * F, so the left piece is F(f x) / F(f), the right (F(f + (1 - f) x) - F(f)) /
        # (1 - F(f)); a piece that can't be scaled (F(f) = 0 / 1) keeps the formula, close)
        if abs(ff) > 1e-12:
            p[3] = f"({x.sub(f'({f!r}*x)', text)}) / ({ff!r})"
        if abs(1 - ff) > 1e-12:
            new[3] = f"(({x.sub(f'({f!r}+{1 - f!r}*x)', text)}) - ({ff!r})) / ({1 - ff!r})"
    pts.insert(i + 1, new)
    return new


def _unit(v):
    return min(1.0, max(0.0, float(v)))


def _along(p, q, c):
    """The BPM c of the way from p's to q's on p's part's BPM change (linear / doubling)."""
    if p[4] == "exp":
        return clamp_bpm(2 ** (math.log2(p[1]) + (math.log2(q[1]) - math.log2(p[1])) * c))
    return clamp_bpm(p[1] + (q[1] - p[1]) * c)


def part_bpm(tempo, i, f):
    """Point i's part's BPM f of the way (0..1) to the next point: its own curve, so the end is the next point's
    BPM even where a jump's second point shares that beat."""
    p, q = tempo["points"][i], tempo["points"][i + 1]
    c = curve(p[2], p[3], np.asarray(f, float), bend_of(p))
    if p[4] == "exp":
        out = 2 ** (math.log2(p[1]) + (math.log2(q[1]) - math.log2(p[1])) * c)
    else:
        out = p[1] + (q[1] - p[1]) * c
    return np.clip(out, MIN_BPM, MAX_BPM)


def stepped(tempo, beats, ppq, edges):
    """beats moved to where their interval step reads the line inside the curved stretches (step_edges): the
    value the file has there."""
    beats = np.asarray(beats, float)
    every = tempo.get("every", EVERY)
    out = beats.copy()
    tick = np.floor(beats * ppq + 1e-9)
    for a, b, b0, b1, jump in edges:
        on = (tick >= a) & (tick < b)
        starts = a + (tick[on] - a) // every * every
        if jump:  # (spread to its end; the last step a hair before its end beat: the jump's second point is ON it)
            out[on] = np.minimum(b0 + (b1 - b0) * step_shares(a, b, starts, every), b1 - 1e-9 * max(1.0, b1))
        elif every > 1:  # (each step reads the line at its own start; a catch-up's too)
            out[on] = starts / ppq
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


_WINDOW = 256  # (one value: candidates checked this many at a time, not one by one; same order, same answer)


def _first_safe(cands):
    """The first Domino-safe value in cands (in 1..0xFFFFFF), or None."""
    cands = cands[(cands >= 1) & (cands <= 0xFFFFFF)]
    ok = np.nonzero(domino_safe(cands))[0]
    return int(cands[ok[0]]) if len(ok) else None


@functools.lru_cache(maxsize=1 << 16)
def _nearest1(x):
    """nearest_safe of one value: k = 0, then the nearer side, then the other, k = 1, 2... (remembered)"""
    u = int(np.rint(x))
    if _safe1(u):  # (k = 0: most often, no array needed)
        return u
    s = 1 if x >= u else -1
    for k in range(1, 33):  # (the next few in plain Python, the same order)
        if _safe1(u + s * k):
            return u + s * k
        if _safe1(u - s * k):
            return u - s * k
    for k0 in range(0, SAFE_REACH, _WINDOW):
        k = np.arange(max(k0, 1), min(k0 + _WINDOW, SAFE_REACH))
        cands = np.column_stack([u + s * k, u - s * k]).ravel()
        if k0 == 0:
            cands = np.concatenate([[u], cands])
        got = _first_safe(cands)
        if got is not None:
            return got
    return u


def _next1(u, way):
    """next_safe of one value."""
    for k0 in range(1, SAFE_REACH, _WINDOW):
        got = _first_safe(u + way * np.arange(k0, min(k0 + _WINDOW, SAFE_REACH)))
        if got is not None:
            return got
    return u


def nearest_safe(x):
    """The Domino-safe whole microseconds nearest x (array of floats), kept in 1..0xFFFFFF."""
    x = np.clip(np.asarray(x, float), 1, 0xFFFFFF)
    if x.ndim == 1 and x.size <= 32:  # (one at a time in _fit_blocks, a short gap's few: thousands of calls)
        return np.array([_nearest1(v) for v in x.tolist()], np.int64)
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
    if len(u) == 1:
        return np.array([_next1(int(u[0]), way)], np.int64)
    if len(u) <= 32:  # (a short gap's few, thousands of calls: one by one in plain Python, the same first one)
        return np.array([_next_small(v, way) for v in u.tolist()], np.int64)
    out = u.copy()
    # (only the ones still looking: one step at a time over the whole array took 15 s on a catch-up, hunt
    # 2026-10-24); most find one within a few steps
    idx = np.arange(len(u))
    for k in range(1, 9):
        if not len(idx):
            return out
        cand = u[idx] + way * k
        ok = (cand >= 1) & (cand <= 0xFFFFFF) & domino_safe(np.clip(cand, 1, 0xFFFFFF))
        out[idx[ok]] = cand[ok]
        idx = idx[~ok]
    if len(idx):  # (slow tempos: safe values far apart; walked by the BPMs a file can store instead)
        out[idx] = _next_by_bpm(u[idx], way)
    return out


@functools.lru_cache(maxsize=1 << 16)
def _next_small(u, way):
    """next_safe of one value in plain Python, _next_by_bpm's walk (remembered: one export asked the same value
    thousands of times, 92 whole numbers from the next safe one)."""
    c = u + way
    while 1 <= c <= 0xFFFFFF and (c - u) * way < SAFE_REACH:
        v = int(6e7 / struct.unpack("f", struct.pack("f", 6e7 / c))[0])
        if v == c:
            return c
        c = v if (v - c) * way > 0 else c + way
    return u


def _next_by_bpm(u, way):
    """next_safe for values whose next safe value is far: a candidate c that isn't safe comes back from Domino as
    v (6e7 / its float32 BPM, cut); no safe value lies between c and v, so when v is further on the next candidate
    is v itself (slow tempos: thousands of whole numbers skipped at once), else c + 1. The same first one as
    trying every whole number. u itself where none is within SAFE_REACH."""
    u = np.asarray(u, np.int64)
    out = u.copy()
    c = u + way
    left = np.ones(len(u), bool)
    while left.any():
        i = np.flatnonzero(left)
        ci = c[i]
        live = (ci >= 1) & (ci <= 0xFFFFFF) & ((ci - u[i]) * way < SAFE_REACH)
        left[i[~live]] = False
        i, ci = i[live], ci[live]
        if not len(i):
            break
        v = np.trunc(6e7 / (6e7 / ci).astype(np.float32).astype(np.float64)).astype(np.int64)
        safe = v == ci
        out[i[safe]] = ci[safe]
        left[i[safe]] = False
        on = ~safe
        c[i[on]] = np.where((v[on] - ci[on]) * way > 0, v[on], ci[on] + way)
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
            if _safe1(a) and _safe1(b):  # (plain Python: thousands of gaps, one try each step)
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
    while len(out) < PAIR_REACH:  # (a window of candidates at a time; a gap of SAFE_REACH or more ends it)
        cands = u + way * np.arange(1, 2 * _WINDOW + 1)
        cands = cands[(cands >= 1) & (cands <= 0xFFFFFF)]
        safe = cands[domino_safe(cands)] if len(cands) else cands
        if not len(safe):
            nxt = _next1(u, way)
            if nxt == u:
                break
            out.append(nxt)
            u = nxt
            continue
        out += safe[:PAIR_REACH - len(out)].tolist()
        u = out[-1]
    return out


def _safe1(u):
    """domino_safe of one whole number, in plain Python (the same float32 rounding; thousands of calls a second)."""
    if not 1 <= u <= 0xFFFFFF:
        return False
    bpm = struct.unpack("f", struct.pack("f", 6e7 / u))[0]
    return int(6e7 / bpm) == u


_FAR = object()  # (_near_pair: safe values too far apart here, the full search decides)


def _near_pair(n, target):
    """_flat_pair's answer found from the nearest safe values out (plain Python, a few each side: the full search
    built PAIR_REACH each side and tried every pair, 62,000 times in one export), stopping once no pair further
    out can be as near; the same pair (same nearness sums, same order on a tie). _FAR when the safe values are
    far apart (the full search then)."""
    q = target / n

    def side(u, way):
        while True:
            for _ in range(64):
                if _safe1(u):
                    yield u
                    break
                u += way
            else:
                raise LookupError
            u += way

    try:
        gd, gu = side(math.floor(q), -1), side(math.ceil(q), 1)
        downs, ups = [next(gd)], [next(gu)]
        best, done = None, set()
        while True:
            for ia, av in enumerate(downs):
                for ib, bv in enumerate(ups):
                    if (ia, ib) in done:
                        continue
                    done.add((ia, ib))
                    if bv == av:
                        continue
                    gap = bv - av
                    k, rest = divmod(n * bv - target, gap)
                    if rest or not 0 <= k <= n:
                        continue
                    far = (q - float(av)) + (float(bv) - q)
                    if best is None or (far, ia, ib) < best[:3]:
                        best = (far, ia, ib, av, bv, k)
            # (a pair with a further value: at least this far; strictly further = can't win, ties included)
            limit = math.inf if best is None else best[0] + 1e-6
            grow_d = len(downs) < PAIR_REACH and ups[0] - (downs[-1] - 1) <= limit
            grow_u = len(ups) < PAIR_REACH and (ups[-1] + 1) - downs[0] <= limit
            if not grow_d and not grow_u:
                break
            if grow_d:
                downs.append(next(gd))
            if grow_u:
                ups.append(next(gu))
    except (LookupError, StopIteration):
        return _FAR
    if best is None:
        return None if len(downs) >= PAIR_REACH and len(ups) >= PAIR_REACH else _FAR
    _, ia, ib, av, bv, k = best  # (a tie: the full search's order = by the down value's place, then the up's)
    return np.concatenate([np.full(k, av, np.int64), np.full(n - k, bv, np.int64)])


def _flat_pair(n, target):
    """n Domino-safe values adding up to exactly target, all one value or a run of A then a run of B (one or two
    tempo changes); the pair nearest the average first. None if no such pair is near."""
    a, r = divmod(int(target), int(n))  # (most often: the two whole numbers round the average, both safe; that's
    if r == 0 and _safe1(a):  # the nearest pair, as the search below finds; hunt 2026-10-24: export speed)
        return np.full(n, a, np.int64)
    if r and _safe1(a) and _safe1(a + 1):
        k = n * (a + 1) - target
        return np.concatenate([np.full(k, a, np.int64), np.full(n - k, a + 1, np.int64)])
    got = _near_pair(n, target)
    if got is not _FAR:
        return got
    q = target / n
    downs, ups = _safe_side(math.floor(q), -1), _safe_side(math.ceil(q), 1)
    if not downs or not ups:
        return None
    # (every pair at once, in the order a then b; n * b < 2^63: values < 2^24, blocks far under 2^39 ticks)
    a = np.repeat(np.array(downs, np.int64), len(ups))
    b = np.tile(np.array(ups, np.int64), len(downs))
    same = b == a
    hit = np.nonzero(same & (a * n == target))[0]
    if len(hit):
        return np.full(n, int(a[hit[0]]), np.int64)
    gap = np.where(same, 1, b - a)
    k, rest = (n * b - target) // gap, (n * b - target) % gap  # (k ticks of a, the rest of b)
    ok = np.nonzero(~same & (rest == 0) & (k >= 0) & (k <= n))[0]
    if not len(ok):
        return None
    far = (q - a[ok].astype(float)) + (b[ok].astype(float) - q)
    i = ok[int(np.argmin(far))]  # (the first of the nearest, as before)
    a, b, k = int(a[i]), int(b[i]), int(k[i])
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


STAIR_STEP = 0.1  # (a held step at most 10 % of the BPM before it = part of stairs, user 2026-10-22)
STAIR_MIN = 3  # (steps in a row that make stairs)


def ruler_marks(tempo, base, ppq, bar=4):
    """The tempo changes the piano roll's ruler marks (user 2026-10-22): [(beat, "point", (before, at, then), until)]
    for each point (before = the BPM just before it, None at beat 0; then = where its part goes, None when it holds)
    and [(beat, "catch", BPM or None when it can't work, until)] for each catch-up line. until = the beat the ruler's
    faint line runs to (a glide's / catch-up's end, math.inf = on to the edge), None for a jump (the tempo just
    stays). STAIRS (user 2026-10-22: a pasted ramp read as jumps) = STAIR_MIN+ held steps in a row going one way,
    each at most STAIR_STEP of the BPM and within a bar of the one before: ONE mark (before, first BPM, last BPM),
    a line to the last step, [(beat, before, at)] of every step as its 5th item (the tip)."""
    if not has_tempo(tempo):
        return []
    key = (tempo_text(tempo), base, ppq, bar)
    if key in _marks:
        return _marks[key]
    worked = plans(tempo, base, ppq)
    pts, lines = tempo["points"], [st["line"] for st, _ in worked]
    out = []
    pb = np.array([p[0] for p in pts], float)
    befores = shown_bpm(tempo, base, pb - 1e-9 * np.maximum(1.0, pb), worked).tolist()  # (all in one go)
    marks = []  # [index, before, at, then, until]
    for i, p in enumerate(pts):
        b = p[0]
        if i + 1 < len(pts) and pts[i + 1][0] == b:  # (where a curve arrives: the next one on its beat says it)
            continue
        before = befores[i] if b > 0 else None
        then = until = None
        if p[2] != "hold" and i + 1 < len(pts) and pts[i + 1][1] != p[1]:
            cut = [x for x in lines if b < x < pts[i + 1][0]]  # (a catch-up line cuts the part short)
            then = float(drawn_bpm(tempo, base, [min(cut)])[0]) if cut else float(pts[i + 1][1])
            until = min(cut) if cut else pts[i + 1][0]
        marks.append([i, before, float(p[1]), then, until])
    k = 0
    while k < len(marks):
        run = _stairs(marks, k, pts, lines, bar)
        i, before, at, then, until = marks[k]
        if len(run) >= STAIR_MIN:
            last = marks[k + len(run) - 1]
            steps = [(pts[m[0]][0], m[1], m[2]) for m in run]
            out.append((pts[i][0], "point", (before, at, last[2]), pts[last[0]][0], steps))
            k += len(run)
            continue
        out.append((pts[i][0], "point", (before, at, then), until))
        k += 1
    out += [(st["line"], "catch", plan["bpm"] if plan["ok"] else None,
             st["end"] if st["end"] is not None else math.inf) for st, plan in worked]
    if len(_marks) > 4:
        _marks.clear()
    _marks[key] = out
    return out


def heard_marks(tempo, base, ppq, bar=4):
    """ruler_marks for each Keep timing run's own heard tempo line (user 2026-10-24: the ruler marks its points too,
    in purple, same rules): only the points inside the run, before its first point = the tempo heard before the run;
    a glide's line stops at the run's end. No catch-ups (a heard line has none)."""
    out, pts = [], tempo["points"]
    for i, j in keep_runs(tempo):
        line = heard_line(pts[i])
        if not line:
            continue
        a, e = pts[i][0], pts[j + 1][0] if j + 1 < len(pts) else math.inf
        inside = [q for q in line if a - 1e-9 <= q[0] <= e + 1e-9]
        if not inside:
            continue
        before = heard_before(tempo, base, ppq, i, j)
        if abs(before - inside[0][1]) <= 1e-4 * before:  # (the file's microseconds: 180.0002 marked "180 → 180")
            before = inside[0][1]
        got = ruler_marks({"points": inside, "catch": [], "every": tempo.get("every", EVERY)}, before, ppq, bar)
        for b, kind, v, until, *steps in got:
            if kind == "point" and b < e - 1e-9:  # (a point on the run's end: the sound goes on from the line after)
                out.append((b, "heard", v, None if until is None else min(until, e), *steps))
    return out


def _stairs(marks, k, pts, lines, bar):
    """The marks from k on that make stairs (held steps in a row, one way, small, within a bar of each other, no
    catch-up line between them); fewer than STAIR_MIN = not stairs."""
    run, way = [], 0
    for m in marks[k:]:
        i, before, at = m[0], m[1], m[2]
        if before is None or pts[i][2] != "hold" or m[3] is not None:
            break
        if i == 0 or pts[i - 1][2] != "hold":  # (a glide arrives here: not a step)
            break
        if abs(at - before) > STAIR_STEP * before:
            break
        d = (at > before) - (at < before)
        if d and way and d != way:
            break
        if run:
            a, b = pts[run[-1][0]][0], pts[i][0]
            if b - a > bar or any(a < x <= b for x in lines):
                break
        way = way or d
        run.append(m)
    while run and run[-1][1] == run[-1][2]:  # (no change at the end: not part of the climb)
        run.pop()
    return run if sum(m[1] != m[2] for m in run) >= STAIR_MIN else run[:0]


_marks = {}


def bpm_at(tempo, base, ppq, tick):
    """The exact BPM the file holds at a tick (Spiderweb's ticks, before Keep timing moves anything: what the pane
    draws there); the project's BPM without a tempo line."""
    if not has_tempo(tempo):
        return float(base)
    key = (tempo_text(tempo), base, ppq, tick)
    if key in _at:  # (the play line resting / redrawn: once)
        return _at[key]
    u = None
    for st, plan in plans(tempo, base, ppq):  # (only that tick worked out, not the whole song: hunt 2026-10-22,
        ra, rb = beat_tick(st["line"], ppq), beat_tick(st["end"], ppq) if st["end"] is not None else None
        if plan["ok"] and ra <= tick < rb:  # thousands of points took seconds per edit)
            u = plan["fit"] if isinstance(plan["fit"], int) else int(plan["fit"][tick - ra])
            break
    if u is None:
        a, _, got = _segments(tempo, base, base_us(base), ppq, tick, tick + 1)[0]
        u = got if isinstance(got, int) else int(got[tick - a])
    _at.clear()
    _at[key] = out = 60_000_000 / u
    return out


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
