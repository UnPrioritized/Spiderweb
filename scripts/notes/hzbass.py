"""Hz bass: spam so fast that the repeats sound like a tone.

A custom shape with sh["hz"] (and a spam fill) gets its spam gates from a tone instead of the gate box:
  {"key": the key whose tone is wanted, "cents": pitch adjustment (100 = one key), "bpm": the BPM the gates are
   worked out for, "fixed": True = every gate rounded to a whole tick instead (the tone is a little off; the
   higher PPQ x BPM, the less), "auto": cents (0..AUTO_MOST) = instead of "fixed": a tone held still gets fixed
   gates when those are at most this far off its exact tone, else mixed ones; a slide is always mixed. A placed
   tone may have its own "auto" (see threshold)}
sh["gate"] is then one wave of that tone, gate = BPM / (60 x Hz) beats, NOT rounded to a whole tick: the notes sit
on one grid counted from tick 0 (every key in step), note n starting at round(n x gate), so gates of two sizes are
mixed and the tone comes out exact (custom.chop_even). The tone depends on the BPM, so a changed BPM leaves it off
until it's updated (the panel warns); a changed PPQ keeps the tone.

Placed notes (the Hz bass window): hz["tones"] = [{"t": start in beats from the shape's left edge, "len": beats,
"key": its tone, "cents": its own tune, "auto": its own Auto gates threshold (optional), "id": its number (never reused in the shape), "to": its slides}, ...].
Each tone makes repeats one wave apart for as long as it lasts; tones sounding together are a chord. A slide is
made by the user and belongs to two tones: "to" = [{"id": the tone slid to, "out": lead out, "in": lead in
(beats)}, ...]: the tone glides from `out` before this tone's end to `in` after the start of the other one (which
must start at or after this one's end), the wave getting shorter or longer on the way. A tone can slide to several
tones, and several can slide to one: each slide is a line of its own, next to the tone held (tone_runs). Without a
slide the tone just stops at its end. The red line in the window is what is really heard (heard).
All the repeats together, each lasting until the next one starts, are the squares every key of the shape is chopped
by (custom.chop_grid), so a chord takes one channel. Nothing sounds where no tone is.
hz["grow"] = the shape is kept as long as its tones (fit_length). hz["own"] = made with the Hz bass tool: a box
that is nothing but its tones (it goes when its last tone is deleted; the panel shows the keys it repeats).

Effects: hz["fx"] = {effect: [[beat, value], ...]}: one line through points over all the tones (beat counted like a
tone's "t", from the shape's left edge; flat before the first point and after the last), value 0..1.
hz["loop"] = {effect: beats}: that effect's points are one repeat of so many beats (from 0 to it), repeated from the
shape's left edge on over and over (line_at; like an automation that repeats every beat). They change the colour of the tone, not its pitch, by making the keys hit at different spots of the wave
(how late a key is, in waves, is added up over the effects; a key starts that late, and a repeat pushed past its
own tone's end is left out):
  "slant": every key starts its repeats a bit later than the key below it; the value is how much of one wave the
  shape's keys are spread over (0 = all together).
  "groups": the keys take turns in groups, evenly spread over the wave: 0 = all together, then 2, 3... up to
  GROUPS groups at 1 (group_count).
  "offpitch": every key repeats a little faster or slower than the tone, the lowest key the fastest, the highest
  the slowest: the keys drift apart and meet again by themselves. 1 = OFF_PITCH of the tone between them. (Its
  waves are made that much longer or shorter, one after the other, so a key never skips or doubles a repeat.)
  "noisy": every repeat of every key is late by a random bit, up to the value of one wave.
  "vibrato": the tone itself goes up and down, VIBRATO_RATE times a beat, by value x VIBRATO of its pitch (every
  key the same; its waves made shorter and longer like off pitch's).
With effects every key has its own repeats (KeyGrid, custom.chop_keys); without any, nothing changes.
Four more change how hard the keys hit instead (KeyGrid.factor -> velocity_factor, used by engine._notes_tracks on
top of the shape's own velocity; loudness goes with velocity squared):
  "sweep": a bump of loudness over the keys; the value is where it is, 0 = the lowest key, 1 = the highest.
  "wah": loud and quiet stripes over the keys, more of them the higher the value (0 = every key full).
  "tremolo": every key louder and quieter in turn, value x TREMOLO times a beat (0 = steady).
  "octave": every other repeat softer, down to velocity 1 at 1: the tone an octave below comes in.
Waveforms ("sine", "square", "saw", "triangle"): every key hits SUB times in each wave instead of once, and how
hard each of those hits is follows the shape drawn over one wave (WAVES), which takes overtones out of the tone
(measured: sine leaves almost only the lowest, square and triangle take out every second one, saw tilts them).
The value goes from the plain tone (0: one hit a wave) to the whole shape (1); hits too soft to matter are left
out, so the note count grows with the value, up to SUB times as many."""

import functools
import json
import math

import numpy as np

HZ_DEFAULTS = {"key": 33, "cents": 0.0}
MIN_LEN = 1 / 1024  # beats: a tone is never shorter
TUNE = 50.0  # cents: how far a placed tone's own tune goes, up or down (half a key)
AUTO = 3.0  # cents: Auto gates' threshold to start with
AUTO_MOST = 50.0  # ... and the highest (past half a key, a fixed tone is nearer the next key than its own)
VEL_FX = ("sweep", "wah", "tremolo", "octave")  # the effects that change the velocity
WAVES = {"sine": lambda p: (1.0 + np.cos(2.0 * np.pi * p)) / 2.0,  # the waveforms: loudness over one wave (p 0..1)
         "square": lambda p: (p < 0.5).astype(float),
         "saw": lambda p: 1.0 - p,
         "triangle": lambda p: 1.0 - np.abs(2.0 * p - 1.0)}
FX = ("slant", "groups", "offpitch", "noisy", "vibrato") + VEL_FX + tuple(WAVES)  # the effects a Hz bass can have
FLAT, RAMP = [[0.0, 0.5], [1.0, 0.5]], [[0.0, 0.0], [1.0, 1.0]]
# the line an effect starts with, u = 0..1 over the tones (or what's in view when there are none)
FX_START = dict({name: RAMP for name in FX}, offpitch=FLAT, tremolo=FLAT, vibrato=FLAT)
SUB = 16  # waveforms: hits in one wave
SOFT = 0.003  # ... a hit with less than this much of the full loudness is left out (velocity 7 of 127)
VIBRATO = 0.05  # "vibrato" at 1: the pitch goes this much (x the tone) up and down
VIBRATO_RATE = 2.5  # ... times a beat
WAH = 8.0  # "wah" at 1: this many loud stripes over the keys
TREMOLO = 8.0  # "tremolo" at 1: this many times a beat
LOOP = (1 / 256, 1024.0)  # beats: how short and how long one repeat of a repeating effect can be
# ready-made shapes for one repeat (u 0..1 over it, value); "sine" and "steps" (random) are made in loop_shape
LOOP_SHAPES = {"sine": None, "triangle": [(0, 0), (0.5, 1), (1, 0)], "saw_up": [(0, 0), (1, 1)],
               "saw_down": [(0, 1), (1, 0)], "square": [(0, 1), (0.5, 1), (0.5, 0), (1, 0)],
               "pump": [(0, 0), (0.1, 0.35), (0.35, 0.85), (1, 1)], "steps": None}
GROUPS = 6  # "groups" at 1
OFF_PITCH = 0.02  # "offpitch" at 1: the highest key's tone is this much (x the tone) below the lowest key's


def group_count(value):
    """How many groups the keys take turns in at a "groups" value (an array or a number)."""
    return 1 + np.floor(np.asarray(value) * (GROUPS - 1) + 0.5).astype(np.int64)


def hz_of(key, cents=0.0):
    """The tone of a key in Hz (key 69 = 440 Hz)."""
    return 440.0 * 2.0 ** ((key - 69 + cents / 100.0) / 12.0)


def hz_gate(hz, bpm):
    """The spam gate, in beats, that sounds like hz's tone at this BPM."""
    return max(1e-6, float(f"{bpm / (60.0 * hz_of(hz['key'], hz['cents'])):.12g}"))  # (12 digits: as saved)


def clean_fx(fx):
    """Effects checked: {effect: [[beat, value], ...]} in order, beats from 0, values 0..1. Effects with no points
    are left out."""
    out = {}
    for name in FX:
        try:
            pts = [(float(u), float(v)) for u, v in fx.get(name) or ()]
        except (TypeError, ValueError, AttributeError):
            continue
        pts = sorted(((max(0.0, u), min(1.0, max(0.0, v))) for u, v in pts
                      if math.isfinite(u) and math.isfinite(v)), key=lambda p: p[0])  # (two at one spot: a step)
        if pts:
            out[name] = [list(p) for p in pts]
    return out


def clean_loop(loop, fx):
    """Repeats checked: {effect: beats one repeat lasts} for the effects in fx only; a repeating effect's points
    are put inside one repeat (fx is changed)."""
    out = {}
    for name, every in (loop.items() if isinstance(loop, dict) else ()):
        try:
            every = float(every)
        except (TypeError, ValueError):
            continue
        if name in fx and math.isfinite(every) and LOOP[0] <= every <= LOOP[1]:
            out[name] = every
            fx[name] = [[min(u, every), v] for u, v in fx[name]]
    return out


def line_at(pts, beat, every=None):
    """A line's value at beat (a number or an array): through its points, flat before the first and after the last;
    every = the points are one repeat of that many beats, repeated from beat 0 (the last point leads on to the next
    repeat's first)."""
    xs, vs = [p[0] for p in pts], [p[1] for p in pts]
    beat = np.asarray(beat, float)
    if every:
        beat = np.mod(beat, every)
        xs, vs = [xs[-1] - every] + xs + [xs[0] + every], [vs[-1]] + vs + [vs[0]]
    return np.interp(beat, xs, vs)


def fx_at(hz, name, beat):
    """The value of an effect at beat (an array, from the shape's left edge; 0 when there's no such line).
    Before the first point and after the last one the line stays flat, unless it repeats (hz["loop"])."""
    pts = (hz.get("fx") or {}).get(name)
    if not pts:
        return np.zeros(np.shape(beat))
    return line_at(pts, beat, (hz.get("loop") or {}).get(name))


def loop_on(pts, every):
    """A line turned into one repeat of `every` beats: its points squeezed in, first point at 0, last at the end."""
    a, b = pts[0][0], pts[-1][0]
    if b - a < 1e-9:
        return [[0.0, pts[0][1]], [every, pts[0][1]]]
    return [[(u - a) / (b - a) * every, v] for u, v in pts]


def loop_off(pts, every, a, b):
    """One repeat's points stretched back into a line from beat a to b (what loop_on did, undone)."""
    return [[a + u / every * (b - a), v] for u, v in pts]


def loop_shape(kind, every, seed=None):
    """A ready-made shape for one repeat of `every` beats (LOOP_SHAPES): its points."""
    if kind == "sine":
        pts = [(i / 16, 0.5 + 0.5 * math.sin(2 * math.pi * i / 16)) for i in range(17)]
    elif kind == "steps":  # (two points at one spot: a step)
        pts = []
        for i, v in enumerate(np.random.default_rng(seed).random(8)):
            pts += [(i / 8, float(v)), ((i + 1) / 8, float(v))]
    else:
        pts = LOOP_SHAPES[kind]
    return [[u * every, round(v, 4)] for u, v in pts]


def old_fx(tones):
    """Effects saved on each tone (before they were one line over all of them): their points joined into one
    line per effect, at beats."""
    out = {}
    for n in sorted((n for n in tones if isinstance(n, dict) and isinstance(n.get("fx"), dict)),
                    key=lambda n: n.get("t", 0)):
        try:
            t, span = float(n["t"]), float(n["len"])
            for name, pts in clean_fx(n["fx"]).items():
                out.setdefault(name, []).extend([t + min(1.0, u) * span, v] for u, v in pts)
        except (KeyError, TypeError, ValueError):
            continue
    return clean_fx(out)


def has_fx(hz):
    return bool(hz.get("fx")) and bool(hz.get("tones"))


def clean_tones(tones):
    """Placed tones checked and put in order (by start, then key)."""
    out, old = [], []
    for n in tones if isinstance(tones, list) else ():
        try:
            tone = {"t": max(0.0, float(n["t"])), "len": max(MIN_LEN, float(n["len"])), "key": int(n["key"]),
                    "cents": max(-TUNE, min(TUNE, float(n.get("cents", 0.0))))}
            leads = (max(0.0, float(n.get("in", 0.0))), max(0.0, float(n.get("out", 0.0))))
            tone["id"] = int(n.get("id", 0))
            if n.get("auto") is not None:
                tone["auto"] = max(0.0, min(AUTO_MOST, float(n["auto"])))
            tone["to"] = [{"id": int(s["id"]), "out": max(0.0, float(s["out"])), "in": max(0.0, float(s["in"]))}
                          for s in n.get("to") or ()]
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        ok = all(math.isfinite(v) for v in [tone["t"], tone["len"], tone["cents"], tone.get("auto", 0.0), *leads]
                 + [v for s in tone["to"] for v in (s["out"], s["in"])])
        if 0 <= tone["key"] <= 127 and ok:
            out.append(tone)
            old.append(leads if "id" not in n else None)
    order = sorted(range(len(out)), key=lambda i: (out[i]["t"], out[i]["key"]))
    out, old = [out[i] for i in order], [old[i] for i in order]
    seen = set()
    for n in out:  # every tone its own number
        if n["id"] <= 0 or n["id"] in seen:
            n["id"] = max([m["id"] for m in out] + [0]) + 1
        seen.add(n["id"])
    # saved before slides were their own thing (a lead in / out on each tone): it slid to the next tone on its line
    lines = []
    for n, leads in zip(out, old):
        if leads is None:
            continue
        for line in lines:
            a, a_leads = line[-1]
            if a["t"] + a["len"] <= n["t"] + 1e-9:
                if a_leads[1] > 0 or leads[0] > 0:
                    a["to"].append({"id": n["id"], "out": a_leads[1], "in": leads[0]})
                line.append((n, leads))
                break
        else:
            lines.append([(n, leads)])
    for n in out:  # no slide to a tone that's gone, none twice
        kept = {}
        for s in n["to"]:
            if s["id"] in seen and s["id"] != n["id"]:
                kept.setdefault(s["id"], s)
        n["to"] = list(kept.values())
    return out


def clean_hz(hz):
    """A saved "hz" checked, or None."""
    try:
        out = {"key": int(hz["key"]), "cents": float(hz.get("cents", 0.0)), "bpm": float(hz["bpm"])}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not (0 <= out["key"] <= 255 and abs(out["cents"]) <= 1200 and out["bpm"] > 0):
        return None
    if hz.get("fixed") is True:
        out["fixed"] = True
    else:
        try:
            auto = float(hz["auto"]) if hz.get("auto") is not None else None
        except (TypeError, ValueError):
            auto = None
        if auto is not None and math.isfinite(auto):
            out["auto"] = max(0.0, min(AUTO_MOST, auto))
    tones = clean_tones(hz.get("tones"))
    fx = clean_fx(hz["fx"]) if isinstance(hz.get("fx"), dict) else old_fx(hz.get("tones") or ())
    if fx:
        out["fx"] = fx
        loop = clean_loop(hz.get("loop"), fx)
        if loop:
            out["loop"] = loop
    if tones:
        out["tones"] = tones
        for flag in ("grow", "own"):
            if hz.get(flag) is True:
                out[flag] = True
    return out


def tones_span(tones):
    """How long the tones are together, in beats."""
    return max((n["t"] + n["len"] for n in tones), default=0.0)


def next_id(tones):
    """The number for a new tone."""
    return max((n["id"] for n in tones), default=0) + 1


def pitch(n):
    """A placed tone's pitch in keys: its key moved by its own tune (n["cents"])."""
    return n["key"] + n.get("cents", 0.0) / 100.0


def can_slide(a, b):
    """True when tone a can slide to tone b: b starts at or after a's end."""
    return a is not b and b["t"] >= a["t"] + a["len"] - 1e-9


def links(tones):
    """[(a, b, s)]: the slides that count, tone a to tone b; s = the slide itself (in a["to"]: its leads). A slide
    to a tone that starts before a's end (it was moved there) is kept but does nothing."""
    by = {n["id"]: n for n in tones}
    out = []
    for a in tones:
        for s in a["to"]:
            b = by.get(s["id"])
            if b is not None and can_slide(a, b):
                out.append((a, b, s))
    return out


def glide(a, b, s):
    """A slide as (start beat, end beat, pitch at the start, pitch at the end): it leaves a's tone `out` before
    a's end and reaches b's tone `in` after b's start."""
    return a["t"] + a["len"] - min(s["out"], a["len"]), b["t"] + min(s["in"], b["len"]), pitch(a), pitch(b)


def wave(hz, ppq, key, limit=None):
    """The gate, in ticks, of one wave of key's tone (hz = the shape's settings: cents, bpm, fixed). limit = Auto
    gates' threshold in cents for a tone held still: whole ticks when that's at most this far off."""
    gate = ppq * hz["bpm"] / 60.0 / hz_of(key, hz["cents"])
    whole = hz.get("fixed") or (limit is not None and off_cents(gate) <= limit + 1e-9)
    return max(1.0, math.floor(gate + 0.5) if whole else gate)


def off_cents(gate):
    """How far, in cents, a gate rounded to a whole tick is off the tone of the exact gate (in ticks)."""
    whole = max(1.0, math.floor(gate + 0.5))
    return abs(1200.0 * math.log2(gate / whole))


def threshold(hz, n=None):
    """Auto gates' threshold in cents for tone n (its own, or the Hz bass's), or None when the gates aren't Auto."""
    if hz.get("auto") is None:
        return None
    return (n or {}).get("auto", hz["auto"])


def auto_state(hz, ppq, n):
    """For tone n held still with Auto gates: (how far fixed gates would be off, in cents; the threshold; True when
    it gets fixed gates). None when the gates aren't Auto."""
    limit = threshold(hz, n)
    if limit is None:
        return None
    off = off_cents(ppq * hz["bpm"] / 60.0 / hz_of(pitch(n), hz["cents"]))
    return off, limit, off <= limit + 1e-9


def auto_picks(hz, ppq):
    """Which tones get fixed gates with Auto gates (True / False each; one for a Hz bass without placed tones), or
    None when the gates aren't Auto. A threshold change that leaves these the same leaves the notes the same."""
    if hz.get("auto") is None:
        return None
    if not hz.get("tones"):
        return (off_cents(ppq * hz["bpm"] / 60.0 / hz_of(hz["key"], hz["cents"])) <= hz["auto"] + 1e-9,)
    return tuple(auto_state(hz, ppq, n)[2] for n in hz["tones"])


def tone_runs(hz, left, ppq):
    """The repeats of the placed tones as unbroken stretches of tone: [(start ticks, the ticks their waves are over
    = the next one's start, whose: (tone, None) or (tone slid from, tone slid to))], not rounded. A tone held is
    one stretch, from where the first slide into it arrives to where the last slide out of it leaves; every slide
    is one more (two when there's a gap between its tones: nothing sounds there), its waves in step with the tone
    it leaves."""
    tones = hz["tones"]
    ls = links(tones)
    out, held = [], {}
    for n in tones:
        a = n["t"] + min([min(s["in"], n["len"]) for _, b, s in ls if b is n], default=0.0)
        b = n["t"] + n["len"] - min([min(s["out"], n["len"]) for m, _, s in ls if m is n], default=0.0)
        if b > a:
            s, e, gate = (left + a) * ppq, (left + b) * ppq, wave(hz, ppq, pitch(n), threshold(hz, n))
            starts = s + gate * np.arange(int(math.ceil((e - s) / gate)))
            out.append((starts, starts + gate, (n, None)))
            held[n["id"]] = (s, gate)
    for a, b, link in ls:
        x0, x1, k0, k1 = glide(a, b, link)
        if x1 - x0 < 1e-12:
            continue
        s0, e0 = (left + x0) * ppq, (left + a["t"] + a["len"]) * ppq
        s1, e1 = (left + b["t"]) * ppq, (left + x1) * ppq
        t = s0
        if a["id"] in held and s0 >= held[a["id"]][0]:  # in step with the tone it leaves
            s, gate = held[a["id"]]
            t = s + math.ceil((s0 - s) / gate - 1e-9) * gate
        gap = s1 - e0 > 1e-6
        for end in (e0, e1) if gap else (e1,):
            part, after = [], []
            while t < end:
                part.append(t)
                t += wave(hz, ppq, k0 + (k1 - k0) * (t - s0) / (e1 - s0))
                after.append(t)
            if part:
                out.append((np.array(part), np.array(after), (a, b)))
            t = max(t, s1)
    return out


def _limits(tones, left, ppq, starts):
    """For each repeat (start ticks, not rounded): the tick the sound it's in ends at (the end of the tones that
    touch or overlap around it)."""
    spans = []
    for n in sorted(tones, key=lambda n: n["t"]):
        if spans and n["t"] <= spans[-1][1] + 1e-9:
            spans[-1][1] = max(spans[-1][1], n["t"] + n["len"])
        else:
            spans.append([n["t"], n["t"] + n["len"]])
    at = np.array([(left + a) * ppq for a, _ in spans]) - 1e-6
    ends = np.array([math.floor((left + b) * ppq + 0.5) for _, b in spans], np.int64)
    return ends[np.maximum(np.searchsorted(at, starts, "right") - 1, 0)]


def _whole(v):
    return np.floor(v + 0.5).astype(np.int64)


@functools.lru_cache(maxsize=16)
def _heard(hz_json, left, ppq, bpm):
    hz = json.loads(hz_json)
    out = []
    for starts, nexts, _ in tone_runs(hz, left, ppq):
        limits = _limits(hz["tones"], left, ppq, starts)
        mean = nexts - starts  # (the wave as made: with mixed gates the whole-tick ones come to this on average)
        starts, nexts = _whole(starts), _whole(nexts)
        gates = np.maximum(nexts - starts, 1)  # (whole ticks: what the PPQ lets the wave be)
        keys, mean = (69.0 + 12.0 * np.log2(ppq * bpm / 60.0 / 440.0 / g) - hz["cents"] / 100.0
                      for g in (gates, mean))
        ends = np.minimum(nexts, limits)
        keep = ends > starts
        if keep.any():
            out.append((starts[keep] / ppq - left, ends[keep] / ppq - left, keys[keep], mean[keep]))
    return out


def heard(hz, left, ppq, bpm):
    """What the placed tones really sound like at this PPQ and BPM (the red line of the Hz bass window): for each
    unbroken stretch of tone, arrays (start, end in beats from the left edge, pitch in keys, average pitch) of its
    repeats. A repeat lasts a whole number of ticks, so its pitch is a little off the wanted one: the lower the
    PPQ, the more. Mixed gates take turns so that the average is the wanted tone; fixed gates are all the same, so
    there the average is each repeat's own pitch.
    The pitch is counted from the shape's own tuning (hz["cents"]), so a key's exact tone is that key."""
    return _heard(json.dumps(hz, sort_keys=True), left, ppq, float(bpm))


@functools.lru_cache(maxsize=16)
def _squares(hz_json, left, ppq):
    hz = json.loads(hz_json)
    got = [s for s, _, _ in tone_runs(hz, left, ppq)]
    if not got:
        return np.zeros((0, 2), np.int64)
    starts = np.concatenate(got)
    return _grid(starts, _limits(hz["tones"], left, ppq, starts))[0]


def _grid(starts, limits):
    """Repeats (start ticks, not rounded; the tick each one's sound ends at) -> (start, end) whole ticks in order,
    each lasting until the next one starts; and which repeat each of them is."""
    if not len(starts):
        return np.zeros((0, 2), np.int64), np.zeros(0, np.int64)
    starts = _whole(starts)
    order = np.argsort(starts, kind="stable")
    starts, limits = starts[order], limits[order]
    first = np.concatenate([[True], starts[1:] != starts[:-1]])
    at = np.flatnonzero(first)
    order = order[at]
    starts, limits = starts[at], np.maximum.reduceat(limits, at)  # (repeats of two lines on one tick: one note)
    ends = np.minimum(np.concatenate([starts[1:], limits[-1:]]), limits)
    keep = ends > starts
    out = np.column_stack([starts, ends])[keep]
    out.setflags(write=False)
    return out, order[keep]


class KeyGrid:
    """The repeats of a Hz bass with effects: every key has its own (squares(key)), and how much of
    the shape's velocity each of them gets (factor)."""

    def __init__(self, hz, left, ppq, lo, n):
        self.lo, self.n, self.got = lo, max(1, n), {}
        self.runs = []
        for starts, nexts, whose in tone_runs(hz, left, ppq):
            beat = starts / ppq - left
            run = {"starts": starts, "waves": nexts - starts, "number": np.arange(len(starts)), "beat": beat,
                   "limits": _limits(hz["tones"], left, ppq, starts),
                   # (a repeat moved past its own tone's end is left out: the next tone may touch it)
                   "until": (left + whose[0]["t"] + whose[0]["len"]) * ppq if whose[1] is None else np.inf}
            fx = hz.get("fx") or {}
            for name in FX:
                run[name] = fx_at(hz, name, beat)
            run["swept"] = np.full(len(beat), "sweep" in fx)  # (sweep at 0 = the bump on the lowest key)
            for name in WAVES:  # (a waveform at 0 = the plain tone; without any: the plain tone too)
                run["has_" + name] = np.full(len(beat), name in fx)
            run["groups"] = group_count(run["groups"])
            run["turns"] = np.concatenate([[0.0], np.cumsum(run["tremolo"] * TREMOLO * run["waves"] / ppq)[:-1]])
            self.runs.append(run)
        self.shaped = any(r["has_" + name].any() for r in self.runs for name in WAVES)
        self.loud = self.shaped or any(name in (hz.get("fx") or ()) for name in VEL_FX)

    @staticmethod
    def respaced(run, x):
        """A stretch of tone for the key at x: off pitch and vibrato make its waves longer or shorter one after the
        other (every value of a repeat taken for the one with the same number; past the end, the last one's), so
        it may take more or fewer repeats to fill the stretch. None when neither is on."""
        off, vib = run["offpitch"], run["vibrato"]
        if not (off.any() or vib.any()):
            return None
        stretch = (1.0 + OFF_PITCH * off * (x - 0.5)) * (
            1.0 + VIBRATO * vib * np.sin(2.0 * np.pi * VIBRATO_RATE * (run["beat"] - run["beat"][0])))
        n = len(off)
        more = n // 10 + 3  # (enough: the waves are at most a few % shorter)
        pick = np.minimum(np.arange(n + more), n - 1)
        waves = run["waves"][pick] * stretch[pick]
        out = {k: (v[pick] if isinstance(v, np.ndarray) else v) for k, v in run.items()}
        out["starts"] = run["starts"][0] + np.concatenate([[0.0], np.cumsum(waves)[:-1]])
        out["waves"], out["number"] = waves, np.arange(n + more)
        return out

    def made(self, key):
        """A key's repeats, (start, end) ticks in order, none overlapping, and each one's part of the velocity."""
        got = self.got.get(key)
        if got is not None:
            return got
        x = min(1.0, max(0.0, (key - self.lo) / self.n))  # where the key is among the shape's keys, 0 = the lowest
        xv = min(1.0, max(0.0, (key - self.lo) / max(1, self.n - 1)))  # (for loudness: 1 = the highest key)
        noise = np.random.default_rng(1000 + key)  # (the same every time: the key is the seed)
        all_starts, all_limits, all_factors = [], [], []
        for run in self.runs:
            run = self.respaced(run, x) or run
            late = run["slant"] * x + np.floor(x * run["groups"]) / run["groups"]
            if run["noisy"].any():
                late = late + run["noisy"] * noise.random(len(late))
            starts = run["starts"] + late * run["waves"]
            limits = run["limits"]
            factor = np.ones(len(starts))
            if self.loud:
                loud = np.where(run["swept"], 0.08 + 0.92 * np.clip(np.cos(np.pi * (xv - run["sweep"])), 0.0, 1.0) ** 4,
                                1.0)
                loud = loud * (1.0 + np.cos(2.0 * np.pi * WAH * run["wah"] * (xv - 0.5))) / 2.0
                loud = loud * (0.1 + 0.9 * (1.0 + np.cos(2.0 * np.pi * run["turns"])) / 2.0)
                soft = np.where(run["number"] % 2 == 1, 1.0 - run["octave"], 1.0)  # (on the velocity itself)
                if self.shaped:  # SUB hits in every wave, each as loud as the waveforms say there
                    part = np.arange(SUB) / SUB
                    mix = np.ones((len(starts), SUB))
                    for name in WAVES:  # (several on one note: multiplied)
                        v, on = run[name][:, None], run["has_" + name][:, None]
                        mix *= np.where(on, (1.0 - v) * (part == 0) + v * WAVES[name](part)[None, :], 1.0)
                    plain = ~np.any([run["has_" + name] for name in WAVES], axis=0)
                    mix[plain] = part == 0
                    keep = mix >= SOFT
                    rows = np.nonzero(keep)[0]
                    starts = (starts[:, None] + part[None, :] * run["waves"][:, None])[keep]
                    limits = limits[rows]
                    factor = np.sqrt(loud[rows] * mix[keep]) * soft[rows]
                else:
                    factor = np.sqrt(loud) * soft
            keep = starts < min(run["until"], np.inf) - 1e-6
            all_starts.append(starts[keep])
            all_limits.append(limits[keep])
            all_factors.append(factor[keep])
        if all_starts:
            sq, which = _grid(np.concatenate(all_starts), np.concatenate(all_limits))
            factor = np.concatenate(all_factors)[which]
        else:
            sq, factor = np.zeros((0, 2), np.int64), np.zeros(0)
        factor.setflags(write=False)
        got = self.got[key] = (sq, factor)
        return got

    def squares(self, key):
        """A key's repeats: (start, end) ticks in order, none overlapping."""
        return self.made(key)[0]

    def factor(self, key, starts):
        """What the effects make of the velocity of a key's notes (start ticks): 0..1 for each to multiply it by."""
        sq, factor = self.made(key)
        if not len(sq):
            return np.ones(len(starts))
        at = np.minimum(np.searchsorted(sq[:, 0], starts), len(sq) - 1)
        return np.where(sq[at, 0] == starts, factor[at], 1.0)


@functools.lru_cache(maxsize=16)
def _key_grid(hz_json, left, ppq, lo, n):
    return KeyGrid(json.loads(hz_json), left, ppq, lo, n)


def key_range(sh):
    """The lowest and highest key of a custom shape's box."""
    ps = [p for _, p in sh["pts"]]
    ps.append(ps[1] + ps[2] - ps[0])
    return round(min(ps)), round(max(ps))


def velocity_factor(sh, ppq, starts, keys):
    """What the effects make of the velocity of a shape's notes (arrays: start ticks, keys): a number from 0 to 1
    for each to multiply it by, or None when there's no effect that changes it."""
    if not has_fx(sh["hz"]) or not len(starts):
        return None
    grid = squares(sh, ppq)
    if not grid.loud:
        return None
    out = np.ones(len(starts))
    for key in np.unique(keys):
        rows = np.flatnonzero(keys == key)
        out[rows] = grid.factor(int(key), starts[rows])
    return out


def squares(sh, ppq):
    """The repeats of a shape with placed tones: an array of (start, end) ticks in order, none overlapping. When
    it has effects: a KeyGrid (each key has its own)."""
    hz_json = json.dumps(sh["hz"], sort_keys=True)
    if has_fx(sh["hz"]):
        lo, hi = key_range(sh)
        return _key_grid(hz_json, left_edge(sh), ppq, lo, hi - lo + 1)
    return _squares(hz_json, left_edge(sh), ppq)


def left_edge(sh):
    """The beat a custom shape's box starts at (its tones count from there)."""
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    return min(b0, b1, b2, b1 + b2 - b0)


def fit_length(sh):
    """hz["grow"]: the shape made as long as its tones (stretched from its left edge)."""
    span = max(tones_span(sh["hz"]["tones"]), MIN_LEN)
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    bs = (b0, b1, b2, b1 + b2 - b0)
    left, width = min(bs), max(bs) - min(bs)
    if width > 1e-12:
        for p in sh["pts"]:
            p[0] = left + (p[0] - left) * span / width


def shortest_gate(hz, ppq):
    """The shortest gate, in ticks, a shape's Hz bass uses (its highest tone)."""
    return wave(hz, ppq, max((pitch(n) for n in hz.get("tones") or ()), default=hz["key"]))
