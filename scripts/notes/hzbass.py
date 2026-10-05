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
"key": its tone, "cents": its own tune, "auto": its own Auto gates threshold (optional), "gate": "fixed" / "mixed" =
its own gates while held, whatever the Hz bass's (optional, see own_gate), "id": its number (never reused in the shape), "to": its slides}, ...].
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
shape's left edge on over and over (line_at; like an automation that repeats every beat). hz["amount"] = {effect:
[[beat, value(, bend)], ...]}: for a repeating effect, a line over the notes (beats like "fx") saying how strong the
repeat is: 1 = as drawn, 0 = as if the effect were off (NEUTRAL). hz["from"] = {effect: "note" / "restart"}: a
repeating effect counted from each note's start instead of the shape's left edge (like an envelope): "note" = its
one repeat plays once from each note's start, then stays on its last value; "restart" = it repeats, starting over
at each note. Each note has its own, like the voices of a synth: counted from its own start (a note starting
while others sound starts over alone; KeyGrid works it out for each tone's repeats). A
note reached by a slide never starts it over (note_beats). hz["fit"] = [effects]: such an effect's repeat is
stretched over each note (and the notes it slides on to) instead of lasting its own beats. hz["sustain"] = {effect:
beat in its repeat}: for one played once per note (not stretched), like a synth's envelope: the line plays up to
that beat, stays there while the note (its chain of slides) lasts, and the rest of it (the fall) plays after the
note ends (a note ending before the sustain point falls from where it got to; sustained). The sound of the tones a
chain ends with then goes on that long after them, the longest fall of all, under the notes after it (both sound
there, like a chord), cut only where a note of the same tone starts (tails). hz["off"] = [effects]
switched off (Bypass): their lines are kept but do nothing (live). They change the colour of the tone, not its pitch, by making the keys hit at different spots of the wave
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
  "vibrato": the tone itself goes up and down, VIBRATO_RATE times a beat (or hz["lfo"]["vibrato_rate"], set by the
  synth window's knobs), by value x VIBRATO of its pitch (every key the same; its waves made shorter and longer like
  off pitch's).
  "pitch": unlike the others it DOES change the pitch: the tone bent up or down by the line, 0.5 = as placed, 1 / 0 =
  PITCH keys up / down (bent, in tone_runs, so the red line shows it and every key is in step).
With effects every key has its own repeats (KeyGrid, custom.chop_keys); without any, nothing changes.
Five more change how hard the keys hit instead (KeyGrid.factor -> velocity_factor, used by engine._notes_tracks on
top of the shape's own velocity; loudness goes with velocity squared):
  "volume": how loud, 1 = as the shape's velocity, 0 = silence (repeats softer than SOFT are left out, and the one
  before them still ends where it would have).
  "sweep": a bump of loudness over the keys; the value is where it is, 0 = the lowest key, 1 = the highest.
  "wah": loud and quiet stripes over the keys, more of them the higher the value (0 = every key full).
  "tremolo": every key louder and quieter in turn, value x TREMOLO times a beat (0 = steady), down to 1 -
  TREMOLO_DEPTH of its loudness (or 1 - hz["lfo"]["tremolo_depth"], set by the synth window's knobs).
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
MIN_LEN = 1 / 65536  # beats: a tone is never shorter (under a tick at the highest PPQ: one tick is always possible)
TUNE = 50.0  # cents: how far a placed tone's own tune goes, up or down (half a key)
AUTO = 3.0  # cents: Auto gates' threshold to start with
AUTO_MOST = 50.0  # ... and the highest (past half a key, a fixed tone is nearer the next key than its own)
VEL_FX = ("volume", "sweep", "wah", "tremolo", "octave")  # the effects that change the velocity
WAVES = {"sine": lambda p: (1.0 + np.cos(2.0 * np.pi * p)) / 2.0,  # the waveforms: loudness over one wave (p 0..1)
         "square": lambda p: (p < 0.5).astype(float),
         "saw": lambda p: 1.0 - p,
         "triangle": lambda p: 1.0 - np.abs(2.0 * p - 1.0)}
# the effects a Hz bass can have
FX = ("slant", "groups", "offpitch", "noisy", "vibrato", "pitch") + VEL_FX + tuple(WAVES)
NEUTRAL = {"volume": 1.0, "pitch": 0.5}  # the value where an effect does nothing (0 for the others)
FLAT, RAMP, FULL = [[0.0, 0.5], [1.0, 0.5]], [[0.0, 0.0], [1.0, 1.0]], [[0.0, 1.0], [1.0, 1.0]]
# the line an effect starts with, u = 0..1 over the tones (or what's in view when there are none)
FX_START = dict({name: RAMP for name in FX}, offpitch=FLAT, tremolo=FLAT, vibrato=FLAT, volume=FULL, pitch=FLAT)
SUB = 16  # waveforms: hits in one wave
SOFT = 0.003  # ... a hit with less than this much of the full loudness is left out (velocity 7 of 127)
VIBRATO = 0.05  # "vibrato" at 1: the pitch goes this much (x the tone) up and down
VIBRATO_RATE = 2.5  # ... times a beat (unless hz["lfo"]["vibrato_rate"])
WAH = 8.0  # "wah" at 1: this many loud stripes over the keys
TREMOLO = 8.0  # "tremolo" at 1: this many times a beat
TREMOLO_DEPTH = 0.9  # ... how much quieter it gets (unless hz["lfo"]["tremolo_depth"])
LFO = {"vibrato_rate": (0.0, 64.0), "tremolo_depth": (0.0, 1.0)}  # hz["lfo"]: what each can be
BEND = 0.98  # how far a line between two points can be bent (1 = a step)
LOOP = (1 / 256, 1024.0)  # beats: how short and how long one repeat of a repeating effect can be
FROM_MODES = ("note", "restart")  # hz["from"]: once from each note's start / repeating, starting over at each note
# ready-made shapes for one repeat (u 0..1 over it, value); "sine" and "steps" (random) are made in loop_shape
LOOP_SHAPES = {"sine": None, "triangle": [(0, 0), (0.5, 1), (1, 0)], "saw_up": [(0, 0), (1, 1)],
               "saw_down": [(0, 1), (1, 0)], "square": [(0, 1), (0.5, 1), (0.5, 0), (1, 0)],
               "pump": [(0, 0), (0.1, 0.35), (0.35, 0.85), (1, 1)], "steps": None}
# ... and for one per note (envelopes, made in loop_shape: they depend on the effect); picking one plays it once per note
ENVELOPES = ("drop", "rise", "pluck")
FAST = 0.5  # their bend: fast first, then settling ((1 - u)^2, the drop the user heard best)
GROUPS = 6  # "groups" at 1
PITCH = 12.0  # "pitch": keys up at 1 (and down at 0; 0.5 = the tone as placed)
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


def clean_point(p):
    """A line's point checked: [beat, value] or [beat, value, bend of the line from it to the next] (bend -BEND..BEND,
    or "hold"), None if it's no good."""
    u, v = float(p[0]), float(p[1])
    if not (math.isfinite(u) and math.isfinite(v)):
        return None
    out = [max(0.0, u), min(1.0, max(0.0, v))]
    if len(p) > 2:
        if p[2] == "hold":
            out.append("hold")
        elif math.isfinite(float(p[2])) and abs(float(p[2])) > 1e-9:
            out.append(min(BEND, max(-BEND, float(p[2]))))
    return out


def clean_fx(fx):
    """Effects checked: {effect: [[beat, value(, bend)], ...]} in order, beats from 0, values 0..1. Effects with no
    points are left out."""
    out = {}
    for name in FX:
        try:
            pts = [clean_point(p) for p in fx.get(name) or ()]
        except (TypeError, ValueError, AttributeError, IndexError):
            continue
        pts = sorted((p for p in pts if p), key=lambda p: p[0])  # (two at one spot: a step)
        if pts:
            out[name] = pts
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
            fx[name] = [[min(p[0], every), *p[1:]] for p in fx[name]]
    return out


def clean_from(froms, loop):
    """Effects counted from each note checked: {effect: "note" / "restart"} for repeating effects only."""
    froms = froms if isinstance(froms, dict) else {}
    return {name: froms[name] for name in FX if name in loop and froms.get(name) in FROM_MODES}


def clean_fit(fit, froms):
    """Effects stretched over each note checked: those counted from each note, in FX order."""
    fit = fit if isinstance(fit, list) else ()
    return [name for name in FX if name in froms and name in fit]


def clean_lfo(lfo):
    """The synth window's settings for the wobbling effects checked (hz["lfo"], see LFO): the good ones."""
    out = {}
    for key, (lo, hi) in LFO.items():
        try:
            v = float(lfo[key])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(v):
            out[key] = min(hi, max(lo, v))
    return out


def clean_sustain(sustain, loop, froms, fit):
    """Sustain points checked: {effect: beat in its repeat (0..its length)} for effects played once per note, not
    stretched."""
    out = {}
    for name, b in (sustain.items() if isinstance(sustain, dict) else ()):
        try:
            b = float(b)
        except (TypeError, ValueError):
            continue
        if froms.get(name) == "note" and name not in fit and math.isfinite(b):
            out[name] = min(loop[name], max(0.0, b))
    return {name: out[name] for name in FX if name in out}


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


def clean_off(off, fx):
    """The effects switched off (Bypass: their lines kept, no effect) checked: those in fx, in FX order."""
    off = off if isinstance(off, list) else ()
    return [name for name in FX if name in fx and name in off]


def live(hz):
    """hz as it's heard: without the effects switched off (Bypass)."""
    off = hz.get("off")
    if not off:
        return hz
    out = {k: v for k, v in hz.items() if k not in ("fx", "loop", "off", "amount", "from", "fit", "sustain")}
    fx = {k: v for k, v in (hz.get("fx") or {}).items() if k not in off}
    loop = {k: v for k, v in (hz.get("loop") or {}).items() if k in fx}
    amount = {k: v for k, v in (hz.get("amount") or {}).items() if k in loop}
    froms = {k: v for k, v in (hz.get("from") or {}).items() if k in loop}
    fit = [k for k in hz.get("fit") or () if k in froms]
    sustain = {k: v for k, v in (hz.get("sustain") or {}).items() if k in froms}
    return dict(out, **({"fx": fx} if fx else {}), **({"loop": loop} if loop else {}),
                **({"amount": amount} if amount else {}), **({"from": froms} if froms else {}),
                **({"fit": fit} if fit else {}), **({"sustain": sustain} if sustain else {}))


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


def chains(tones):
    """{tone id: (beat its chain of slides starts at, beat the chain ends at)}: a tone reached by a slide belongs to
    the chain of the (earliest) tone sliding into it."""
    by = {n["id"]: n for n in tones}
    came = {}
    for a, b, _ in links(tones):
        if b["id"] not in came or a["t"] < came[b["id"]]["t"]:
            came[b["id"]] = a
    heads = {}

    def head(n):
        seen = set()
        while n["id"] in came and n["id"] not in seen:  # (slides only go forward in time: no loops, but be safe)
            seen.add(n["id"])
            n = came[n["id"]]
        return n

    for n in tones:
        heads[n["id"]] = head(n)
    ends = {}
    for n in tones:
        h = heads[n["id"]]["id"]
        ends[h] = max(ends.get(h, 0.0), n["t"] + n["len"])
    return {i: (by[h["id"]]["t"], ends[h["id"]]) for i, h in heads.items()}


def note_span(hz, beat, tone=None):
    """(start, end) beats of the note (chain of slides) an effect counted from each note is in at beat (an array,
    from the shape's left edge): tone's chain, or (tone None) the latest chain to start (chains starting together:
    the longest). None when there are no tones."""
    got = chains(hz.get("tones") or ())
    if not got:
        return None
    if tone is not None and tone.get("id") in got:
        return got[tone["id"]]
    spans = {}
    for s, e in got.values():
        spans[s] = max(spans.get(s, e), e)
    starts = np.array(sorted(spans))
    ends = np.array([spans[s] for s in starts])
    i = np.clip(np.searchsorted(starts, beat, side="right") - 1, 0, len(starts) - 1)
    return starts[i], ends[i]


def note_beats(hz, name, beat, tone=None):
    """Beats into an effect counted from each note (hz["from"]) at beat (an array, from the shape's left edge):
    from the start of its note (note_span); stretched so a chain lasts one repeat when the effect is in hz["fit"]."""
    beat = np.asarray(beat, float)
    span = note_span(hz, beat, tone)
    if span is None:
        return beat
    s0, end = span
    u = np.maximum(0.0, beat - s0)
    if name in (hz.get("fit") or ()):
        u = u * hz["loop"][name] / np.maximum(end - s0, MIN_LEN)
    return u


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
    return np.where(d < 0, line_at(pts, np.minimum(u, at)), after)


def tails(hz):
    """{tone id: beats its sound goes on after its end}: with sustain points (hz["sustain"], effects switched off
    don't count) the falls play after a note ends, so the tones a chain of slides ends with sound on for the
    longest fall, also under the notes after them (like a synth's voices), cut only where a tone of the same pitch
    starts (both there would make their repeats twice as many: a higher tone)."""
    hz = live(hz)
    fall = max((hz["loop"][name] - at for name, at in (hz.get("sustain") or {}).items()), default=0.0)
    tones = hz.get("tones") or ()
    if fall <= 1e-12 or not tones:
        return {}
    got = chains(tones)
    starts = {}
    for n in tones:
        starts.setdefault(pitch(n), []).append(n["t"])
    leaving = {a["id"] for a, _, _ in links(tones)}
    out = {}
    for n in tones:
        end = n["t"] + n["len"]
        if n["id"] in leaving or end < got[n["id"]][1] - 1e-9:  # (not where its chain ends)
            continue
        nxt = min((s for s in starts[pitch(n)] if s >= end - 1e-9), default=math.inf)
        if min(fall, nxt - end) > 1e-9:
            out[n["id"]] = min(fall, nxt - end)
    return out


def sound_span(hz):
    """How long the tones sound together, in beats (tones_span and the falls after them, tails)."""
    got = tails(hz)
    return max((n["t"] + n["len"] + got.get(n["id"], 0.0) for n in hz.get("tones") or ()), default=0.0)


def fx_at(hz, name, beat, tone=None):
    """The value of an effect at beat (an array, from the shape's left edge; 0 when there's no such line).
    Before the first point and after the last one the line stays flat, unless it repeats (hz["loop"]); a repeating
    one is made stronger or weaker by its amount line (hz["amount"]). One counted from each note (hz["from"]):
    tone = the tone it's for (each note has its own), else from the latest note start (note_beats); with a sustain
    point: sustained."""
    pts = (hz.get("fx") or {}).get(name)
    if not pts:
        return np.zeros(np.shape(beat))
    every = (hz.get("loop") or {}).get(name)
    mode = (hz.get("from") or {}).get(name) if every else None
    at = (hz.get("sustain") or {}).get(name) if mode == "note" else None
    span = note_span(hz, np.asarray(beat, float), tone) if at is not None else None
    if span is not None:
        s0, end = span
        v = sustained(pts, every, at, np.asarray(beat, float) - s0, np.asarray(end) - s0)
    elif mode:
        v = line_at(pts, note_beats(hz, name, beat, tone), every if mode == "restart" else None)
    else:
        v = line_at(pts, beat, every)
    amount = (hz.get("amount") or {}).get(name) if every else None
    if amount:
        still = NEUTRAL.get(name, 0.0)
        v = still + (v - still) * line_at(amount, beat)
    return v


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


def old_fx(tones):
    """Effects saved on each tone (before they were one line over all of them): their points joined into one
    line per effect, at beats."""
    out = {}
    for n in sorted((n for n in tones if isinstance(n, dict) and isinstance(n.get("fx"), dict)),
                    key=lambda n: n.get("t", 0)):
        try:
            t, span = float(n["t"]), float(n["len"])
            for name, pts in clean_fx(n["fx"]).items():
                out.setdefault(name, []).extend([t + min(1.0, p[0]) * span, *p[1:]] for p in pts)
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
            if n.get("gate") in ("fixed", "mixed"):
                tone["gate"] = n["gate"]
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
        off = clean_off(hz.get("off"), fx)
        if off:
            out["off"] = off
        amount = clean_fx(hz["amount"]) if isinstance(hz.get("amount"), dict) else {}
        amount = {k: v for k, v in amount.items() if k in loop}
        if amount:
            out["amount"] = amount
        froms = clean_from(hz.get("from"), loop)
        if froms:
            out["from"] = froms
        fit = clean_fit(hz.get("fit"), froms)
        if fit:
            out["fit"] = fit
        sustain = clean_sustain(hz.get("sustain"), loop, froms, fit)
        if sustain:
            out["sustain"] = sustain
    lfo = clean_lfo(hz.get("lfo") or {}) if isinstance(hz.get("lfo") or {}, dict) else {}
    if lfo:
        out["lfo"] = lfo
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


def wave(hz, ppq, key, limit=None, whole=None):
    """The gate, in ticks, of one wave of key's tone (hz = the shape's settings: cents, bpm, fixed). limit = Auto
    gates' threshold in cents for a tone held still: whole ticks when that's at most this far off. whole = True /
    False: whole ticks or not, whatever the rest says (a tone's own gates, own_gate)."""
    gate = ppq * hz["bpm"] / 60.0 / hz_of(key, hz["cents"])
    if whole is None:
        whole = hz.get("fixed") or (limit is not None and off_cents(gate) <= limit + 1e-9)
    return max(1.0, math.floor(gate + 0.5) if whole else gate)


def off_cents(gate):
    """How far, in cents, a gate rounded to a whole tick is off the tone of the exact gate (in ticks)."""
    whole = max(1.0, math.floor(gate + 0.5))
    return abs(1200.0 * math.log2(gate / whole))


def own_gate(n):
    """A placed tone's own gates while held (n["gate"], set in the Hz bass window): True = fixed, False = mixed,
    None = the Hz bass's."""
    return {"fixed": True, "mixed": False}.get((n or {}).get("gate"))


def held_fixed(hz, ppq, n):
    """True when tone n held still gets fixed gates (its own, the Hz bass's Fixed, or Auto's pick)."""
    own = own_gate(n)
    if own is not None:
        return own
    got = auto_state(hz, ppq, n)
    return bool(hz.get("fixed")) if got is None else got[2]


def threshold(hz, n=None):
    """Auto gates' threshold in cents for tone n (its own, or the Hz bass's), or None when the gates aren't Auto."""
    if hz.get("auto") is None:
        return None
    return (n or {}).get("auto", hz["auto"])


def auto_state(hz, ppq, n):
    """For tone n held still with Auto gates or gates of its own: (how far fixed gates would be off, in cents; the
    threshold, None for its own gates; True when it gets fixed gates). None when neither."""
    limit, own = threshold(hz, n), own_gate(n)
    if own is not None:
        limit = None
    elif limit is None:
        return None
    off = off_cents(ppq * hz["bpm"] / 60.0 / hz_of(pitch(n), hz["cents"]))
    return off, limit, own if own is not None else off <= limit + 1e-9


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
    it leaves. A tone a chain ends with goes on for its fall (tails)."""
    tones = hz["tones"]
    ls = links(tones)
    tail = tails(hz)
    out, held = [], {}
    for n in tones:
        a = n["t"] + min([min(s["in"], n["len"]) for _, b, s in ls if b is n], default=0.0)
        b = n["t"] + n["len"] - min([min(s["out"], n["len"]) for m, _, s in ls if m is n], default=0.0)
        b += tail.get(n["id"], 0.0)
        if b > a:
            s, e, gate = (left + a) * ppq, (left + b) * ppq, wave(hz, ppq, pitch(n), threshold(hz, n), own_gate(n))
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
    if "pitch" in (hz.get("fx") or {}):
        out = [bent(hz, left, ppq, starts, nexts, whose[0]) + (whose,) for starts, nexts, whose in out]
    return out


def bent(hz, left, ppq, starts, nexts, tone=None):
    """A stretch of tone's repeats (start ticks, next ones' starts) moved by the "pitch" effect: the tone goes up or
    down by the line (PITCH keys at 1 and 0), its waves shorter or longer. The repeats are spaced by adding up the
    tone over time, so a big bend keeps its timing (a repeat is where the waves so far come to a whole number).
    tone = the tone the stretch belongs to (a slide: the one it leaves), for a line counted from each note."""
    t = np.append(starts, nexts[-1])
    f = 2.0 ** ((fx_at(hz, "pitch", t / ppq - left, tone) - 0.5) * 2.0 * PITCH / 12.0)  # (how many times the tone)
    phase = np.concatenate([[0.0], np.cumsum((f[:-1] + f[1:]) / 2.0)])  # (one wave as placed = 1 at f = 1)
    n = max(1, int(math.ceil(phase[-1] - 1e-9)))
    at = np.interp(np.arange(n + 1, dtype=float), phase, t)
    past = np.arange(n + 1) > phase[-1]  # (the last one's end, past what was placed: its wave carries on)
    at[past] = t[-1] + (np.arange(n + 1)[past] - phase[-1]) * (nexts[-1] - starts[-1]) / f[-1]
    return at[:-1], at[1:]


def _limits(hz, left, ppq, starts):
    """For each repeat (start ticks, not rounded): the tick the sound it's in ends at (the end of the tones that
    touch or overlap around it, their falls included: tails)."""
    spans, tail = [], tails(hz)
    for n in sorted(hz["tones"], key=lambda n: n["t"]):
        end = n["t"] + n["len"] + tail.get(n["id"], 0.0)
        if spans and n["t"] <= spans[-1][1] + 1e-9:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([n["t"], end])
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
        limits = _limits(hz, left, ppq, starts)
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
    return _heard(json.dumps(live(hz), sort_keys=True), left, ppq, float(bpm))


@functools.lru_cache(maxsize=16)
def _squares(hz_json, left, ppq):
    hz = json.loads(hz_json)
    got = [s for s, _, _ in tone_runs(hz, left, ppq)]
    if not got:
        return np.zeros((0, 2), np.int64)
    starts = np.concatenate(got)
    return _grid(starts, _limits(hz, left, ppq, starts))[0]


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
        tail = tails(hz)
        for starts, nexts, whose in tone_runs(hz, left, ppq):
            beat = starts / ppq - left
            n0 = whose[0]
            run = {"starts": starts, "waves": nexts - starts, "number": np.arange(len(starts)), "beat": beat,
                   "limits": _limits(hz, left, ppq, starts),
                   # (a repeat moved past its own tone's end, its fall included, is left out: the next tone may
                   # touch it)
                   "until": (left + n0["t"] + n0["len"] + tail.get(n0["id"], 0.0)) * ppq if whose[1] is None
                   else np.inf}
            fx = hz.get("fx") or {}
            for name in FX:
                run[name] = fx_at(hz, name, beat, n0)  # (each tone counts from its own start, like a synth's voice)
            run["swept"] = np.full(len(beat), "sweep" in fx)  # (sweep at 0 = the bump on the lowest key)
            run["has_volume"] = np.full(len(beat), "volume" in fx)
            for name in WAVES:  # (a waveform at 0 = the plain tone; without any: the plain tone too)
                run["has_" + name] = np.full(len(beat), name in fx)
            run["groups"] = group_count(run["groups"])
            run["turns"] = np.concatenate([[0.0], np.cumsum(run["tremolo"] * TREMOLO * run["waves"] / ppq)[:-1]])
            lfo = hz.get("lfo") or {}
            run["vib_rate"] = lfo.get("vibrato_rate", VIBRATO_RATE)
            depth = lfo.get("tremolo_depth")  # (as it was without one: the very same numbers)
            run["trem"] = (0.1, TREMOLO_DEPTH) if depth is None else (1.0 - depth, depth)
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
            1.0 + VIBRATO * vib * np.sin(2.0 * np.pi * run["vib_rate"] * (run["beat"] - run["beat"][0])))
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
        all_starts, all_limits, all_factors, all_quiet = [], [], [], []
        for run in self.runs:
            run = self.respaced(run, x) or run
            late = run["slant"] * x + np.floor(x * run["groups"]) / run["groups"]
            if run["noisy"].any():
                late = late + run["noisy"] * noise.random(len(late))
            starts = run["starts"] + late * run["waves"]
            limits = run["limits"]
            factor, quiet = np.ones(len(starts)), np.zeros(len(starts), bool)
            if self.loud:
                loud = np.where(run["swept"], 0.08 + 0.92 * np.clip(np.cos(np.pi * (xv - run["sweep"])), 0.0, 1.0) ** 4,
                                1.0)
                loud = loud * (1.0 + np.cos(2.0 * np.pi * WAH * run["wah"] * (xv - 0.5))) / 2.0
                loud = loud * (run["trem"][0] + run["trem"][1] * (1.0 + np.cos(2.0 * np.pi * run["turns"])) / 2.0)
                vol = np.where(run["has_volume"], run["volume"], 1.0)
                loud = loud * vol
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
                    quiet = vol[rows] < SOFT
                else:
                    factor = np.sqrt(loud) * soft
                    quiet = vol < SOFT
            keep = starts < min(run["until"], np.inf) - 1e-6
            all_starts.append(starts[keep])
            all_limits.append(limits[keep])
            all_factors.append(factor[keep])
            all_quiet.append(quiet[keep])
        if all_starts:
            sq, which = _grid(np.concatenate(all_starts), np.concatenate(all_limits))
            factor = np.concatenate(all_factors)[which]
            heard = ~np.concatenate(all_quiet)[which]  # (volume 0: left out once every repeat has its end)
            sq, factor = sq[heard], factor[heard]
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
    if not has_fx(live(sh["hz"])) or not len(starts):
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
    hz = live(sh["hz"])
    hz_json = json.dumps(hz, sort_keys=True)
    if has_fx(hz):
        lo, hi = key_range(sh)
        return _key_grid(hz_json, left_edge(sh), ppq, lo, hi - lo + 1)
    return _squares(hz_json, left_edge(sh), ppq)


def left_edge(sh):
    """The beat a custom shape's box starts at (its tones count from there)."""
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    return min(b0, b1, b2, b1 + b2 - b0)


def fit_length(sh):
    """hz["grow"]: the shape made as long as its tones and the falls after them (stretched from its left edge)."""
    span = max(sound_span(sh["hz"]), MIN_LEN)
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    bs = (b0, b1, b2, b1 + b2 - b0)
    left, width = min(bs), max(bs) - min(bs)
    if width > 1e-12:
        for p in sh["pts"]:
            p[0] = left + (p[0] - left) * span / width


def shortest_gate(hz, ppq):
    """The shortest gate, in ticks, a shape's Hz bass uses (its highest tone, bent up by the "pitch" effect's
    highest point)."""
    top = max((pitch(n) for n in hz.get("tones") or ()), default=hz["key"])
    pts = (live(hz).get("fx") or {}).get("pitch") if hz.get("tones") else None
    if pts:  # (the amount line only ever weakens it)
        top += max(0.0, (max(p[1] for p in pts) - 0.5) * 2.0 * PITCH)
    return wave(hz, ppq, top)
