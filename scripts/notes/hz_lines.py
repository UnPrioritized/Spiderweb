"""Hz bass effect lines: a line's value at a beat (bends, holds, repeats), sustain points, the ready-made repeat
shapes and envelopes."""

import math

import numpy as np

from notes.envelope import env_values
from notes.hz_settings import FAST, LOOP, LOOP_SHAPES


def bent_part(u, bend):
    """How far along (0..1) the line from a point to the next has come at u (0..1 of the way) with that bend
    (arrays; nan = hold: none of the way until the next point). Bend b puts the middle at (1 + b) / 2 of the way:
    0 = straight, above 0 = bulging towards the next point's value early, below 0 = late."""
    m = (1.0 + np.nan_to_num(bend)) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        early = 1.0 - (1.0 - u) ** (np.log(1.0 - m) / np.log(0.5))
        late = u ** (np.log(m) / np.log(0.5))
    out = np.where(bend > 0, early, np.where(bend < 0, late, u))
    return np.where(np.isnan(bend), (u >= 1.0).astype(float), out)


def bend_of(p):
    """A point's bend as a number (nan = hold)."""
    return (math.nan if p[2] == "hold" else float(p[2])) if len(p) > 2 else 0.0


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


def line_at(pts, beat, every=None):
    """A line's value at beat (a number or an array): through its points (each line between two bent as the first
    one says), flat before the first and after the last; every = the points are one repeat of that many beats,
    repeated from beat 0 (the last point leads on to the next repeat's first)."""
    if every:
        pts = [[pts[-1][0] - every, *pts[-1][1:]]] + list(pts) + [[pts[0][0] + every, *pts[0][1:]]]
        beat = np.mod(np.asarray(beat, float), every)
    xs, vs = np.array([p[0] for p in pts], float), np.array([p[1] for p in pts], float)
    beat = np.asarray(beat, float)
    if len(xs) == 1:
        return np.full(beat.shape, vs[0])
    bends = np.array([bend_of(p) for p in pts])
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


def _shifted_line(pts, d):
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


def _turned_repeat(pts, d, every):
    """One repeat's points (0..every) for its repeat starting d beats later: the same points, the ones going past
    its end coming round to its start (in the same order, so the line is exactly the same)."""
    s = d % every
    if s < 1e-12 or every - s < 1e-12:
        return pts
    moved = [[p[0] + s, *p[1:]] for p in pts]
    over = [[p[0] - every, *p[1:]] for p in moved if p[0] >= every - 1e-12]
    return [[max(0.0, p[0]), *p[1:]] for p in over] + [p for p in moved if p[0] < every - 1e-12]
