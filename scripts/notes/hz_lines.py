"""Hz bass effect lines: a line's value at a beat (bends, holds, repeats), sustain points, the ready-made repeat
shapes and envelopes."""

import math

import numpy as np

from notes.envelope import env_values
from notes.hz_settings import FAST, LOOP, LOOP_SHAPES, WOBBLE_RATE_MOST, WOBBLE_SEED_STEP


def bent_part(u, bend):
    """How far along (0..1) the line from a point to the next has come at u (0..1 of the way) with that bend
    (arrays; nan = hold: none of the way until the next point). Bend b puts the middle at (1 + b) / 2 of the way:
    0 = straight, above 0 = bulging towards the next point's value early, below 0 = late. S_CURVE + b = an S: that
    curve on each half, the second turned round (below 0: slow at both ends, like a slide's "double")."""
    bend = np.asarray(bend, float)
    double = bend >= S_CURVE - 1.0  # (an S: bend_of's number S_CURVE + b, b's curve on each half)
    if np.any(double):
        b = np.where(double, bend - S_CURVE, bend)
        u = np.asarray(u, float)
        one = bent_part(np.where(u < 0.5, 2.0 * u, 2.0 - 2.0 * u), b)  # (the second half turned round)
        return np.where(double, np.where(u < 0.5, 0.5 * one, 1.0 - 0.5 * one), bent_part(u, b))
    m = (1.0 + np.nan_to_num(bend)) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        early = 1.0 - (1.0 - u) ** (np.log(1.0 - m) / np.log(0.5))
        late = u ** (np.log(m) / np.log(0.5))
    out = np.where(bend > 0, early, np.where(bend < 0, late, u))
    return np.where(np.isnan(bend), (u >= 1.0).astype(float), out)


S_CURVE = 4.0  # bend_of: a point whose piece is an S ([u, value, b, "s"]: a note bend's) = S_CURVE + b


def bend_of(p):
    """A point's bend as a number (nan = hold; an S: S_CURVE + its bend, for bent_part)."""
    if len(p) <= 2:
        return 0.0
    if p[2] == "hold":
        return math.nan
    return float(p[2]) + (S_CURVE if len(p) > 3 and p[3] == "s" else 0.0)


def note_vel(n, beat):
    """A note's own velocity at beat (an array, counted like its "t") from its loudness line (tone["vel"]; before
    its start / after its end: as at them), or None when it has none (it takes the Hz bass's velocity)."""
    pts = n.get("vel")
    if not pts:
        return None
    u = np.clip((np.asarray(beat, float) - n["t"]) / max(n["len"], 1e-12), 0.0, 1.0)
    return env_values(pts, u)


def vel_part(n, t, span):
    """The part of note n's loudness line from beat t for span beats, as a line of its own (u 0..1 over that part),
    or None when it has none: for a note made from it (an arpeggio's step, a note cut at the left edge)."""
    pts = n.get("vel")
    if not pts:
        return None
    u0, u1 = (t - n["t"]) / max(n["len"], 1e-12), (t + span - n["t"]) / max(n["len"], 1e-12)
    if u1 - u0 <= 1e-12:
        return [[0.0, float(note_vel(n, t))]]
    a, b = env_values(pts, np.clip([u0, u1], 0.0, 1.0))
    return ([[0.0, float(a)]] + [[(p[0] - u0) / (u1 - u0), p[1]] for p in pts if u0 < p[0] < u1]
            + [[1.0, float(b)]])


def bends(n):
    """True when note n has a bend line or a wobble of its own (note_bend moves it)."""
    return bool(n.get("bend") or n.get("wobble"))


def tone_keys(n):
    """(lowest, highest) pitch (keys) note n reaches with its own bend line and wobbles (its points: pieces never
    overshoot them; each wobble's deepest each way)."""
    k = n["key"] + n.get("cents", 0.0) / 100.0
    vals = [p[1] for p in n.get("bend") or ()] or [0.0]
    deep = max((depth_most(w) for w in n.get("wobble") or ()), default=0.0)
    return k + min(vals) - deep, k + max(vals) + deep


def note_bend(n, beat, most):
    """Keys a note's own bend line (tone["bend"]) and wobbles (tone["wobble"]) move its tone at beat (an array,
    counted like its "t"; before its start / after its end: as at them), no further than `most` (NOTE_BEND_MOST)
    each way; None when it has none. "bend_at" = (start, length) the line is spread over when it's another note's (an
    arpeggio's step: by time)."""
    pts, wob = n.get("bend"), n.get("wobble")
    if not pts and not wob:
        return None
    t, span = n.get("bend_at") or (n["t"], n["len"])
    u = np.clip((np.asarray(beat, float) - t) / max(span, 1e-12), 0.0, 1.0)
    got = line_at(pts, u) if pts else np.zeros(np.shape(u))
    if wob:
        got = got + wobble_keys(wob, u, span)
    return np.clip(got, -most, most)


def random_heights(j):
    """A Random wobble's height (-1..1) at whole wave number j (an int array: its "seed" + the waves gone by), the
    same every time (play, export, red line, Bake); 0 at every whole WOBBLE_SEED_STEP (a new run starts on its line)."""
    j = np.asarray(j, np.int64)
    with np.errstate(over="ignore"):
        x = j.astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15)
        x ^= x >> np.uint64(31)
        x *= np.uint64(0xBF58476D1CE4E5B9)
        x ^= x >> np.uint64(29)
    h = (x >> np.uint64(11)).astype(float) / float(1 << 53) * 2.0 - 1.0
    return np.where(j % WOBBLE_SEED_STEP == 0, 0.0, h)


def wave_of(shape, c, seed=0):
    """A wobble's wave (-1..1) at c cycles from its start (an array): starts at 0 going up (a square: up first),
    WOBBLE_SHAPES. Random (user 2026-11-13: smooth) = an eased glide to a new height (random_heights, from `seed`)
    each wave; Pluck (user 2026-11-13) = each wave kicks to the top, then curves back to the middle."""
    c = np.asarray(c, float)
    f = c % 1.0
    if shape == "triangle":
        return np.where(f < 0.25, 4.0 * f, np.where(f < 0.75, 2.0 - 4.0 * f, 4.0 * f - 4.0))
    if shape == "saw":
        return np.where(f < 0.5, 2.0 * f, 2.0 * f - 2.0)
    if shape == "square":
        return np.where(f < 0.5, 1.0, -1.0)
    if shape == "pluck":
        return (1.0 - f) ** PLUCK_FALL
    if shape == "random":
        k = np.floor(c).astype(np.int64) + int(seed)
        a, b = random_heights(k), random_heights(k + 1)
        return a + (b - a) * (1.0 - np.cos(math.pi * f)) / 2.0
    return np.sin(2.0 * math.pi * f)


PLUCK_FALL = 3.0  # wave_of: a Pluck wave falls back from its top like (1 - x) to this power (fast, then settling)
WAVE_GAP = 1e-9  # wobble_keys: a run's waves read at most this far (waves) before its end
HALF_NEAR = 1e-6  # wave_end: a run's wave count this near a whole half wave IS that half wave (rounding)
POINT_NEAR = 1e-7  # fit_wobbles: a wobble end this near a bend point (parts of the note) is put on it
U_NEAR = 1e-12  # wobble_keys: this near a run's start / end (parts of the note) counts as on it


def wave_start(w):
    """Where a wobble run's wave count starts at its u0: waves gone before ("phase", a cut run going on) + the
    user's Phase nudge ("nudge")."""
    return w.get("phase", 0.0) + w.get("nudge", 0.0)


def wave_end(w, span):
    """Waves a wobble run has gone through at its end (+ its phase); a hair off a whole half wave = that half wave
    (user's pictures 2026-10-28: a free note length made 8 waves 8.0000004, a sliver of a new half drawn as a
    spike)."""
    end = wave_start(w) + w["rate"] * (w["at"][1] - w["at"][0]) * span
    half = round(end * 2.0) / 2.0
    return half if abs(end - half) < HALF_NEAR else end


def depth_pts(w):
    """A wobble run's depth line: its "env" points ([[share 0..1 of the run, keys(, curve)], ...], the depth line's
    own points, stage B 2026-11-13) or a straight line from its "depth" at the start to the one at its end."""
    return w.get("env") or [[0.0, w["depth"][0]], [1.0, w["depth"][1]]]


def depth_at(w, share):
    """A wobble run's depth (keys each way) at share (0..1 of the run, an array)."""
    return line_at(depth_pts(w), share)


def depth_most(w):
    """A wobble run's deepest depth (its points: the curves between them never go past them)."""
    return max(p[1] for p in depth_pts(w))


def set_depth(w, pts):
    """Run w's depth line set to pts ([[share, keys(, curve)], ...], first at 0, last at 1): kept as "env" when
    it has points between its ends or a curve, else as the plain "depth" pair (old saves' way); "depth" always holds
    its two ends."""
    pts = [[round(float(p[0]), 9), round(max(0.0, float(p[1])), 4), *p[2:3]] for p in pts]
    w["depth"] = [pts[0][1], pts[-1][1]]
    if len(pts) > 2 or any(len(p) > 2 for p in pts[:-1]):
        w["env"] = pts
    else:
        w.pop("env", None)
    return w


def depth_cut(w, a, b):
    """Run w's depth line from share a to b (0..1 of the run), laid over that part (0..1): its points inside, a
    curved piece cut through made of short straight ones (as bend_part cuts a bend)."""
    pts = w.get("env")
    if not pts:
        d0, d1 = w["depth"]
        return [[0.0, round(d0 + (d1 - d0) * a, 4)], [1.0, round(d0 + (d1 - d0) * b, 4)]]
    got = bend_part({"bend": pts, "t": 0.0, "len": 1.0}, a, b - a)
    return got or [[0.0, 0.0], [1.0, 0.0]]


def cut_depth(w, a, b):
    """Run w with its depth line cut to share a..b (depth_cut, set_depth), a new dict."""
    run = dict(w)
    run.pop("env", None)
    return set_depth(run, depth_cut(w, a, b))


def wobble_keys(runs, u, span):
    """Keys a note's wobbles (tone["wobble"], hz_settings.clean_wobble) add at u (0..1 along it, an array) when it's
    span beats long: each run's wave from u0 to u1, its depth (keys each way) following its depth line (depth_pts),
    `rate` waves a beat counted from its start (+ "phase" waves: a run cut at its start goes on in step)."""
    u = np.asarray(u, float)
    out = np.zeros(u.shape)
    for w in runs:
        u0, u1 = w["at"]
        if u1 - u0 <= 1e-12:
            continue
        share = np.clip((u - u0) / (u1 - u0), 0.0, 1.0)
        depth = depth_at(w, share)
        # (never quite its end's wave count: a hair before the end must read the last half wave, not the next one
        # rounding makes of it; user's picture 2026-10-28, a square ending in a dip)
        end = wave_end(w, span)
        wave = wave_of(w.get("shape"), np.minimum(wave_start(w) + w["rate"] * (u - u0) * span, end - WAVE_GAP),
                       w.get("seed", 0))
        # (up to its end, not on it: the piece after takes over there, user's picture 2026-10-28: a square's last
        # instant added to the next piece's wobble; the note's own end too: its ">" sits on its end point, user)
        inside = (u >= u0 - U_NEAR) & (u < u1 - U_NEAR)  # (a beat turned into u may land a hair off: rounding)
        out += np.where(inside, depth * wave * (-1.0 if w.get("flip") else 1.0), 0.0)
    return out


def wobble_edges(n, most, lo=-math.inf, hi=math.inf):
    """Beats from lo to hi where note n's wobbles turn or jump (every quarter wave and each run's ends), at most
    `most` of them (more: none), for drawing a wobble exactly: its tops, corners and jumps on the spot."""
    t, span = n.get("bend_at") or (n["t"], n["len"])
    out = []
    for w in n.get("wobble") or ():
        u0, u1 = w["at"]
        b0, b1 = t + u0 * span, t + u1 * span
        phase, rate = wave_start(w), w["rate"]
        out += [b for b in (b0, b1) if lo <= b <= hi]
        a, b = max(b0, lo), min(b1, hi)
        if b <= a:
            continue
        first = math.floor((phase + rate * (a - b0)) * 4) + 1
        last = math.ceil((wave_end(w, span) if b >= b1 else phase + rate * (b - b0)) * 4)
        if len(out) + last - first > most:
            return []
        out += [b0 + (k / 4 - phase) / rate for k in range(first, last)]
    return out


def went_on(w, waves):
    """The "phase" (and a Random run's "seed") of the part of run w that starts `waves` waves after its own start:
    it goes on in step, the same heights too."""
    gone = w.get("phase", 0.0) + waves
    out = {"phase": round(gone % 1.0, 9)}
    if "seed" in w or w.get("shape") == "random":
        out["seed"] = w.get("seed", 0) + int(math.floor(round(gone, 9)))
    return out


def split_wobbles(n, u):
    """A point put at u (0..1) on note n: a wobble across it becomes two, the second going on in step (hunt
    2026-10-28: one wobble over two pieces couldn't be reached)."""
    out = []
    for w in n.get("wobble") or ():
        u0, u1 = w["at"]
        if not u0 + ON_POINT < u < u1 - ON_POINT:
            out.append(w)
            continue
        s = (u - u0) / (u1 - u0)
        first = dict(cut_depth(w, 0.0, s), at=[u0, u])
        second = dict(cut_depth(w, s, 1.0), at=[u, u1], **went_on(w, w["rate"] * (u - u0) * n["len"]))
        out += [first, second]
    if out:
        n["wobble"] = out


def merge_wobbles(n, lo, u, hi):
    """The point at u on note n is gone (its pieces lo..u and u..hi are one now): the first of their wobbles goes
    over the whole piece, the other is dropped (hunt 2026-10-28, user took A)."""
    runs = n.get("wobble") or ()
    mine = [w for w in runs if abs(w["at"][1] - u) < ON_POINT or abs(w["at"][0] - u) < ON_POINT]
    if not mine:
        return
    kept = dict(min(mine, key=lambda w: w["at"][0]), at=[lo, hi])
    n["wobble"] = sorted([w for w in runs if not any(w is m for m in mine)] + [kept], key=lambda w: w["at"])


def fit_wobbles(n):
    """Note n's wobbles each end back on their line (hunt 2026-10-28, user took A): the speed nudged so its piece
    holds whole half waves, as the speed post does; one set freely with Ctrl ("free") is left alone. A wobble going
    on from the one before it (a split one, "phase") starts where that one now ends. An end a hair off a point is put
    on it (user's autosave 2026-10-28: a cut's rounding left one 0.000000001 short, its wobble no longer found)."""
    us = [p[0] for p in n.get("bend") or ([0.0], [1.0])]
    runs = sorted(n.get("wobble") or (), key=lambda w: w["at"])
    for w in runs:
        w["at"] = [min(us, key=lambda p: abs(p - a)) if min(abs(p - a) for p in us) < POINT_NEAR else a
                   for a in w["at"]]
    end = None  # (where the run before ends: (u, its waves there))
    for w in runs:
        beats = (w["at"][1] - w["at"][0]) * n["len"]
        if beats <= 1e-12:
            continue
        # (going on from the run before: a phase, or a Random run's seed off a whole step: one that went on from a
        # whole wave has no phase left)
        going = w.get("phase") or w.get("seed", 0) % WOBBLE_SEED_STEP
        if going and end and abs(end[0] - w["at"][0]) < ON_POINT:
            w.update(went_on(end[2], end[1] - end[2].get("phase", 0.0)))
            if not w["phase"]:
                del w["phase"]
        phase = w.get("phase", 0.0)
        if not w.get("free"):
            waves = max(phase + 0.5, round(2.0 * (phase + w["rate"] * beats)) / 2.0)
            w["rate"] = min(WOBBLE_RATE_MOST, max(0.01, (waves - phase) / beats))  # (not rounded: whole half waves)
        end = (w["at"][1], phase + w["rate"] * beats, w)


def wobble_part(n, t, span):
    """The part of note n's wobbles from beat t for span beats, laid over that part (u 0..1), or None when none is
    left in it: each run cut to it, the depth where it's cut, a run cut at its start going on in step ("phase")."""
    size, off = max(n["len"], 1e-12), t - n["t"]
    out = []
    for w in n.get("wobble") or ():
        a, b = w["at"][0] * size, w["at"][1] * size  # (beats from the note's start)
        lo, hi = max(a, off), min(b, off + span)
        if hi - lo <= 1e-9 or b - a <= 1e-12:
            continue
        run = dict(cut_depth(w, (lo - a) / (b - a), (hi - a) / (b - a)),
                   at=[round((lo - off) / span, 9), round((hi - off) / span, 9)])
        if lo > a + 1e-12:
            run.update(went_on(w, w["rate"] * (lo - a)))
        out.append(run)
    return out or None


ON_POINT = 1e-9  # bend_part: a cut this near a point (in parts of the note) is on it (points are saved to 9 places)


def bend_part(n, t, span):
    """The part of note n's bend line from beat t for span beats, as a line of its own (u 0..1 over that part), or
    None when it has none or it's at 0 all along: for a note cut at the left edge. A curved piece cut through becomes
    short straight ones; a jump (two points at one spot) stays a jump."""
    pts = n.get("bend")
    if not pts:
        return None
    size = max(n["len"], 1e-12)
    u0, u1 = (t - n["t"]) / size, (t + span - n["t"]) / size

    def at(u):
        return float(line_at(pts, min(1.0, max(0.0, u))))
    if u1 - u0 <= 1e-12:
        out = [[0.0, at(u0)]]
    else:
        xs = [p[0] for p in pts]
        # (the piece the cut starts in; -1: before the line. ON_POINT: saved points are rounded, a cut on a third
        # must still count as on it)
        first = int(np.searchsorted(xs, u0 + ON_POINT, side="right")) - 1
        # (the cut's start, every point inside (both of a jump's), its end; each with the curve of the piece after it,
        # that curve as the point has it, and True when it's ON one of the line's points: a piece between two of
        # those is a whole one, its curve kept; a cut on a point = that point, its curve too: a note made longer /
        # shorter from its start keeps its first piece's curve)
        on = 0 <= first < len(pts) - 1 and abs(xs[first] - u0) < ON_POINT
        nodes = [(u0, pts[first][1] if on else at(u0), bend_of(pts[first]) if 0 <= first < len(pts) - 1 else 0.0,
                  on, pts[first][2:] if on else ())]
        inside = [j for j, x in enumerate(xs) if u0 + ON_POINT < x < u1 - ON_POINT]
        for j in inside:
            nodes.append((xs[j], pts[j][1], bend_of(pts[j]) if j < len(pts) - 1 else 0.0, True,
                          pts[j][2:] if j < len(pts) - 1 else ()))
        # (ending ON a point: its pitch; on a jump the first one's, the pitch arriving there, not the one after)
        last = next((p for p in pts if abs(p[0] - u1) < ON_POINT), None)
        nodes.append((u1, at(u1) if last is None else last[1], 0.0, last is not None, ()))
        out = []
        for (a, va, curve, on_a, tail), (b, _, _, on_b, _) in zip(nodes, nodes[1:]):
            if curve and not (on_a and on_b) and b > a:  # (a curve cut through: sampled)
                out.append([a, va])
                out += [[a + (b - a) * i / 8, at(a + (b - a) * i / 8)] for i in range(1, 8)]
            else:
                out.append([a, va] + (list(tail) if curve else []))  # (its own curve, an S too)
        out.append([u1, nodes[-1][1]])
        out = [[(p[0] - u0) / (u1 - u0), *p[1:]] for p in out]
    return out if any(abs(p[1]) > 1e-9 for p in out) else None


def bend_from(n):
    """The fields a note made from note n (an arpeggio's step) takes to play n's bend line (and wobbles) by time."""
    if not bends(n):
        return {}
    return dict({f: n[f] for f in ("bend", "wobble") if n.get(f)},
                bend_at=list(n.get("bend_at") or (n["t"], n["len"])))


def line_arrays(pts, every=None):
    """line_at's points as arrays (beats, values, bends), for a caller asking about the same line many times."""
    if every:
        pts = [[pts[-1][0] - every, *pts[-1][1:]]] + list(pts) + [[pts[0][0] + every, *pts[0][1:]]]
    return (np.array([p[0] for p in pts], float), np.array([p[1] for p in pts], float),
            np.array([bend_of(p) for p in pts]))


def line_at(pts, beat, every=None, arrays=None):
    """A line's value at beat (a number or an array): through its points (each line between two bent as the first
    one says), flat before the first and after the last; every = the points are one repeat of that many beats,
    repeated from beat 0 (the last point leads on to the next repeat's first). arrays = line_arrays(pts, every)
    made already."""
    if every:
        beat = np.mod(np.asarray(beat, float), every)
    xs, vs, bends = line_arrays(pts, every) if arrays is None else arrays
    beat = np.asarray(beat, float)
    if len(xs) == 1:
        return np.full(beat.shape, vs[0])
    j = np.clip(np.searchsorted(xs, beat, side="right") - 1, 0, len(xs) - 2)
    w = xs[j + 1] - xs[j]
    u = np.where(w > 0, np.clip((beat - xs[j]) / np.where(w > 0, w, 1.0), 0.0, 1.0), 1.0)
    return vs[j] + (vs[j + 1] - vs[j]) * bent_part(u, bends[j])


def sustained(pts, every, at, u, held):
    """A line played once per note with a sustain point at `at` beats (hz["sustain"]): its value u beats after the
    note's start (arrays) for a note held `held` beats. Up to `at` as drawn, then staying there until the note ends;
    after that the rest (the fall), from where it was when the note ended (a note ending early: the fall starts from
    there and comes round to the line by the fall's end), then its last value."""
    u = np.maximum(0.0, np.asarray(u, float))
    held = np.broadcast_to(np.asarray(held, float), u.shape)
    d = u - held  # (beats since the note ended)
    fall = every - at
    top = float(line_at(pts, at))
    gone = np.clip(d / fall, 0.0, 1.0) if fall > 1e-12 else np.ones(u.shape)
    after = line_at(pts, at + np.clip(d, 0.0, max(fall, 0.0))) + (line_at(pts, np.minimum(held, at)) - top) * (1 - gone)
    after = np.clip(after, 0.0, 1.0)  # (a fast fall from high above where the note got to would go under 0)
    return np.where(d < 0, line_at(pts, np.minimum(u, at)), after)


def same_points(a, b):
    """Two lines' points the same (to a millionth)."""
    def same(x, y):
        return x == y if isinstance(x, str) or isinstance(y, str) else abs(x - y) < 1e-6
    return len(a) == len(b) and all(len(p) == len(q) and all(map(same, p, q)) for p, q in zip(a, b))


def adsr_line(attack, decay, sustain, release):
    """The line of an ADSR envelope (beats; sustain 0..1): (points, its sustain point, its length). The rise comes
    late and the drops fast first, as the ready-made envelopes do (the Volume box's; ENV 2 / ENV 3 the same)."""
    pts = [[0.0, 0.0, -FAST]] if attack > 0 else []
    if decay > 0:
        pts.append([attack, 1.0, FAST])
    at = attack + decay
    pts.append([at, sustain, FAST] if release > 0 else [at, sustain])
    if release > 0:
        pts.append([at + release, 0.0])
    return pts, at, max(LOOP[0], at + release)


def filter_line(cutoff, amount, attack, decay, sustain, release):
    """The Filter box's Cutoff line (a MIDI CC): (points, sustain point, length; None, None = all along): Cutoff moved
    by Amount x an ADSR envelope from each note's start (adsr_line's, its values cut to 0..1), or flat at Cutoff
    (Amount 0)."""
    if abs(amount) < 1e-9:
        return [[0.0, cutoff]], None, None
    pts, at, every = adsr_line(attack, decay, sustain, release)
    return [[p[0], min(1.0, max(0.0, cutoff + amount * p[1])), *p[2:]] for p in pts], at, every


def env_line(e, x):
    """adsr_line's line at x beats (an array), worked out directly (its bends FAST / -FAST are squares: many times
    faster than line_at for the MOD tab's envelopes, hunt)."""
    a, d, s, r = e["attack"], e["decay"], e["sustain"], e["release"]
    at = a + d
    top = 1.0 if d > 0 else s  # (where the rise goes: no decay = straight to the sustain level)
    rise = top * np.clip(x / a, 0.0, 1.0) ** 2 if a > 0 else np.full(x.shape, top)
    fall = 1.0 + (s - 1.0) * (1.0 - (1.0 - np.clip((x - a) / d, 0.0, 1.0)) ** 2) if d > 0 else rise
    out = np.where(x < a, rise, fall)
    if r > 0:
        out = np.where(x < at, out, s * (1.0 - np.clip((x - at) / r, 0.0, 1.0)) ** 2)
    return out


def env_value(e, u, held):
    """A MOD tab envelope u beats after its note's start for a note held `held` beats: as sustained(adsr_line(...))."""
    u = np.maximum(0.0, u)
    held = np.broadcast_to(np.asarray(held, float), u.shape)
    at = e["attack"] + e["decay"]
    fall = max(LOOP[0], at + e["release"]) - at
    dd = u - held
    gone = np.clip(dd / fall, 0.0, 1.0) if fall > 1e-12 else np.ones(u.shape)
    after = env_line(e, at + np.clip(dd, 0.0, max(fall, 0.0))) + (env_line(e, np.minimum(held, at))
                                                                  - e["sustain"]) * (1 - gone)
    return np.where(dd < 0, env_line(e, np.minimum(u, at)), np.clip(after, 0.0, 1.0))


def loop_on(pts, every):
    """A line turned into one repeat of `every` beats: its points squeezed in, first point at 0, last at the end."""
    a, b = pts[0][0], pts[-1][0]
    if b - a < 1e-9:
        return [[0.0, pts[0][1]], [every, pts[0][1]]]
    return [[(p[0] - a) / (b - a) * every, *p[1:]] for p in pts]


def loop_off(pts, every, a, b):
    """One repeat's points stretched back into a line from beat a to b (what loop_on did, undone)."""
    return [[a + p[0] / every * (b - a), *p[1:]] for p in pts]


def loop_shape(kind, every, seed=None, name=None):
    """A ready-made shape for one repeat of `every` beats (LOOP_SHAPES, or ENVELOPES for the effect `name`): its
    points. Envelopes end where the effect does nothing: pitch from 12 keys up / down to the tone, the others from
    full to 0. Volume rises late (the top half sounds about the same: it reaches the limiter, user)."""
    top, still = (1.0, 0.5) if name == "pitch" else (1.0, 0.0)
    if kind == "drop":
        pts = [(0, top, FAST), (1, still)]
    elif kind == "rise":
        pts = [(0, 0.0, -FAST if name == "volume" else FAST), (1, still if name == "pitch" else top)]
    elif kind == "pluck":
        pts = [(0, still), (0.15, 0.8 if name == "pitch" else top, FAST), (1, still)]
    elif kind == "sine":  # (quarter waves as bent lines: the middle of each is at sin 45 degrees, 0.707 of the way)
        b = math.sqrt(2.0) - 1.0
        pts = [(0, 0.5, b), (0.25, 1, -b), (0.5, 0.5, b), (0.75, 0, -b), (1, 0.5, b)]
    elif kind == "steps":  # (two points at one spot: a step)
        pts = []
        for i, v in enumerate(np.random.default_rng(seed).random(8)):
            pts += [(i / 8, float(v)), ((i + 1) / 8, float(v))]
    else:
        pts = LOOP_SHAPES[kind]
    return [[p[0] * every, round(p[1], 4), *p[2:]] for p in pts]


def shifted_line(pts, d):
    """An effect line's points d beats later; past the left edge (beat 0) it starts with its value there."""
    if not pts:
        return pts
    moved = [[p[0] + d, *p[1:]] for p in pts]
    if moved[0][0] >= 0:
        return moved
    keep = [p for p in moved if p[0] > 0]
    head = [0.0, float(line_at(pts, -d))]
    last = [p for p in moved if p[0] <= 0][-1]  # (the line it cuts goes on as it was bent)
    if keep and len(last) > 2:
        head.append(last[2])
    return [head] + keep


def turned_repeat(pts, d, every):
    """One repeat's points (0..every) for its repeat starting d beats later: the same points, the ones going past
    its end coming round to its start (in the same order, so the line is exactly the same)."""
    s = d % every
    if s < 1e-12 or every - s < 1e-12:
        return pts
    moved = [[p[0] + s, *p[1:]] for p in pts]
    over = [[p[0] - every, *p[1:]] for p in moved if p[0] >= every - 1e-12]
    return [[max(0.0, p[0]), *p[1:]] for p in over] + [p for p in moved if p[0] < every - 1e-12]
