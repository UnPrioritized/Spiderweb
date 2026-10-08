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
  "sweep": a bump of loudness over the keys; the value is where it is, 0 = the lowest key, 1 = the highest
  (hz["lfo"]["sweep_track"] moves it with each note's pitch: see LFO).
  "wah": loud and quiet stripes over the keys, more of them the higher the value (0 = every key full).
  "tremolo": every key louder and quieter in turn, value x TREMOLO times a beat (0 = steady), down to 1 -
  TREMOLO_DEPTH of its loudness (or 1 - hz["lfo"]["tremolo_depth"], set by the synth window's knobs), coming in
  from each note's start after hz["lfo"]["tremolo_wait"] beats over ["tremolo_rise"] (none: there at once).
  "octave": every other repeat softer, down to velocity 1 at 1: the tone an octave below comes in.
Waveforms ("sine", "square", "saw", "triangle"): every key hits SUB times in each wave instead of once, and how
hard each of those hits is follows the shape drawn over one wave (WAVES), which takes overtones out of the tone
(measured: sine leaves almost only the lowest, square and triangle take out every second one, saw tilts them).
The value goes from the plain tone (0: one hit a wave) to the whole shape (1); hits too soft to matter are left
out, so the note count grows with the value, up to SUB times as many.
hz["voice"] = the synth window's Voice box (not lines; clean_voice): {"voices": copies of the sound (2..VOICES),
"detune": cents between the lowest and the highest copy, their tones spread evenly between, "same": True = every key
plays every copy (as many times the notes) instead of each key one copy in turn (no extra notes), "glide": beats a
note takes to glide in from the tone of the note before it (glides), "curve": how (glide_left), "touching": True = only from a note that ends
where it starts, "legato": True = a note that starts right where another ends carries on its effects (no new attack,
no fall after the first: legato_links)}. Glide only bends the tone: each note still starts its effects over, like a
synth's voices (unless Legato joins them).
hz["mode"] = the Wave box's Mode (MODES, clean_mode): FM, Pulse width or Sync change the hits in each wave
(wave_hits), Growl and Bitcrush when each one lands. hz["osc2"] = the second oscillator, OSC B (OSC2, clean_osc2;
there only while it's on): every note played again as its own set of repeats, `octave` x 12 + `semi` keys + `fine`
cents from it, its own "wave" (one of WAVES at `shape`, or the plain tone) and "mode" (as hz["mode"]), `level` as
loud; "split": True = the key rows take turns playing OSC A or OSC B (no extra notes) instead of each row both
(twice the notes); "a_off": True = OSC A switched off (only OSC B sounds, on every row). With Fine, OSC B's gates
alternate (whole-tick ones would round a few cents away). Everything else (the lines, Voice, the Effects tab) works on both. A synth window box switched off (Bypass) puts its lines in
hz["off"] and moves its own setting (mode / voice) to hz["bypass"], which also names it (clean_bypass). Its knobs
that do nothing right now are kept in hz["kept"] (clean_kept), its macros in hz["macro"] (clean_macro).
hz["mod"] = the MOD tab (the matrix, clean_mod): two more LFOs, two more envelopes, each note's velocity and pitch,
linked to effect lines or knobs that make no line (MOD_SETTINGS), which they move over time while the notes are made
(fx_at, setting_at, mod_value)."""

import bisect
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
# hz["lfo"]: what each can be; tremolo_wait / tremolo_rise = beats from each note's start before the tremolo comes
# in, and how long it then takes to reach its depth (the vibrato's are in its line); sweep_track = the Tone box's
# Key track: how far the Sweep's loud part follows each note's pitch (1 = one key row up for every key the note is
# above the Hz bass's own tone, like a filter's key tracking; -1 = down)
LFO = {"vibrato_rate": (0.0, 64.0), "tremolo_depth": (0.0, 1.0), "tremolo_wait": (0.0, 64.0),
       "tremolo_rise": (0.0, 64.0), "sweep_track": (-1.0, 1.0)}
# ... and how the synth window's Rate knobs move (vibrato_timing / tremolo_timing; not missing = "free"): free, or
# only to note lengths (straight, triplets, dotted). Only the knobs care: the rate itself is still times a beat
TIMINGS = ("free", "straight", "triplet", "dotted")
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
# hz["mode"] (the Wave box's Mode): kind -> its settings (lowest, highest, where its knob starts). fm: the wave's
# place pushed back and forth by a wobble `ratio` x the tone, `depth` (x FM_INDEX) falling to 0 over `time` beats
# from each note's start (0: stays); pulse: only the first `width` of each wave, widening to half and back `rate`
# times a beat; sync: the wave started over `amount` times as fast inside each wave of the tone (rising from 1x over
# `time` beats); growl: every repeat late by turns over `every` waves, up to `amount` x GROWL of a wave (a lower
# tone under it); crush: every note moved onto a grid of `amount`^2 x CRUSH beats (uneven waves, gritty).
MODES = {"fm": {"depth": (0.0, 1.0, 0.4), "ratio": (0.25, 16.0, 1.0), "time": (0.0, 64.0, 0.0)},
         "pulse": {"width": (0.02, 0.5, 0.25), "rate": (0.0, 64.0, 0.0)},
         "sync": {"amount": (1.0, 8.0, 3.0), "time": (0.0, 64.0, 0.0)},
         "growl": {"amount": (0.0, 1.0, 0.5), "every": (2.0, 8.0, 2.0)},
         "crush": {"amount": (0.0, 1.0, 0.5)}}
FM_INDEX = 5.0
GROWL = 0.5
CRUSH = 1 / 40
# hz["osc2"] (OSC B): its settings (lowest, highest, where its knob starts): octave / semi = whole octaves / keys from
# the note, fine = cents, level = how loud (as Volume: 0 = silent), shape = its waveform's amount (as a waveform line)
OSC2 = {"shape": (0.0, 1.0, 1.0), "octave": (-3.0, 3.0, 0.0), "semi": (-12.0, 12.0, 0.0), "fine": (-100.0, 100.0, 0.0),
        "level": (0.0, 1.0, 1.0)}
# hz["rack"] (the Effects tab, in the order added; "off": True = switched off, kept): kind -> its settings (lowest,
# highest, where its knob starts). chorus: every other key's tone goes up to `depth` cents and back `rate` times a
# beat (no extra notes); echo: the whole sound again `repeats` times, `time` beats apart, each `fade` as loud as the
# one before; reverb: each note's tone rings on `length` beats after it ends, starting `level` as loud and fading,
# its waves landing more and more scattered (up to `scatter` x half a wave); flanger: a `mix` share of the keys
# (spread evenly: 0.5 = every other key) hits late by up to `depth` x a wave and back, `rate` times a beat (no extra
# notes); compressor: the whole sound's loudness over `threshold` dB is cut down to 1 / `ratio` of how far it's over,
# the cut reached over `attack` beats as it gets louder and let go over `release` beats as it gets quieter, then
# everything made `gain` dB louder (never past full): the quiet parts come up next to the loud ones
RACK = {"chorus": {"depth": (0.0, 100.0, 15.0), "rate": (0.0, 64.0, 0.5)},
        "flanger": {"rate": (0.0, 64.0, 0.25), "depth": (0.0, 1.0, 0.5), "mix": (0.0, 1.0, 0.5)},
        "echo": {"time": (1 / 64, 64.0, 0.75), "repeats": (1.0, 8.0, 3.0), "fade": (0.0, 1.0, 0.5)},
        "reverb": {"length": (1 / 16, 64.0, 2.0), "scatter": (0.0, 1.0, 0.5), "level": (0.0, 1.0, 0.5)},
        "compressor": {"threshold": (-48.0, 0.0, -12.0), "ratio": (1.0, 20.0, 4.0), "attack": (0.0, 64.0, 0.0),
                       "release": (0.0, 64.0, 0.25), "gain": (0.0, 24.0, 9.0)}}
# hz["arp"] (the Arpeggio box; there only while it's on): every note (or the notes placed together, a chord) becomes a
# fast run through its pitches: `chord` = "placed" (the notes placed together) or a chord's steps in keys on each
# note; `octaves` = the same again 1, 2... octaves up; `pattern` = the order; `speed` = notes a beat, each `gate` of
# its step long (1 = touching); `swing` = the steps between the grid's beats (counted from the Hz bass's start) come
# late, by up to half a step at 1 (the step before it that much longer, its own that much shorter)
ARP = {"speed": (0.25, 32.0, 4.0), "octaves": (1.0, 4.0, 1.0), "gate": (0.05, 1.0, 1.0), "swing": (0.0, 1.0, 0.0)}
ARP_PATTERNS = ("up", "down", "updown", "random", "steps")
# hz["arp"]["steps"] (only with the "steps" pattern): the run's own steps, looped, one every 1 / speed beats; each =
# {"note": which held note (1 = the lowest; more than are held = counted round again; 0 = a rest), "octave": moved
# that many octaves, "level": how loud (0..1), "length": of the step (times Gate), "tie": True = it lasts on through
# the next step (which plays nothing new)}. Octaves = the steps played again 1, 2... octaves up in turn. Always STEPS
# of them; hz["arp"]["count"] = how many play (those past it kept for when it grows, as a synth's, hunt).
STEPS = 16  # ... how many
STEP = {"note": (0, 8, 1), "octave": (-2, 2, 0), "level": (0.0, 1.0, 1.0), "length": (0.05, 1.0, 1.0)}
START_STEPS = tuple({"note": 1 + i % 4, "octave": 0, "level": 1.0, "length": 1.0} for i in range(STEPS))  # (notes
START_COUNT = 8  # 1 to 4 in turn; 8 of them playing)
# hz["arp"]["scale"]: the run's notes moved to the nearest note of the scale (counted from hz["arp"]["root"], 0 = C)
SCALES = {"major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10), "harmonic": (0, 2, 3, 5, 7, 8, 11),
          "dorian": (0, 2, 3, 5, 7, 9, 10), "phrygian": (0, 1, 3, 5, 7, 8, 10), "mixolydian": (0, 2, 4, 5, 7, 9, 10),
          "pentatonic": (0, 2, 4, 7, 9), "minor_pentatonic": (0, 3, 5, 7, 10), "blues": (0, 3, 5, 6, 7, 10)}
CHORDS = {"placed": (0,), "octave": (0, 12), "fifth": (0, 7), "major": (0, 4, 7), "minor": (0, 3, 7),
          "seventh": (0, 4, 7, 10), "sus4": (0, 5, 7)}
OFF_BOXES = ("volume", "wave", "pitch", "vibrato", "tremolo", "tone", "character", "voice")  # boxes that switch off
VOICES = 8  # hz["voice"]: the most copies
DETUNE = 100.0  # ... the most cents between the lowest and the highest copy
GLIDE = 64.0  # ... the longest glide, in beats
GLIDE_CURVE = 0.5  # ... its curve when there's none: -1 = slow first, 0 = straight, 1 = fast first (glide_left)
BLEND = 0.5  # ... how loud the middle copies are next to the outer ones when there's none: all the same (blend_gains)


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
    for key in ("vibrato_timing", "tremolo_timing"):
        if isinstance(lfo, dict) and lfo.get(key) in TIMINGS[1:]:
            out[key] = lfo[key]
    return out


def clean_voice(voice):
    """The Voice box's settings checked (hz["voice"], see the docstring): only what does something (one copy: no
    detune; no glide: no "touching")."""
    voice = voice if isinstance(voice, dict) else {}
    out = {}

    def num(key, plain):  # (one that's no good: as if it weren't there)
        try:
            v = float(voice.get(key, plain))
        except (TypeError, ValueError):
            return plain
        return v if math.isfinite(v) else plain
    n, detune, glide = int(num("voices", 1.0)), num("detune", 0.0), num("glide", 0.0)
    if n >= 2:
        out["voices"] = min(VOICES, n)
        out["detune"] = min(DETUNE, max(0.0, detune))
        if voice.get("same") is True:
            out["same"] = True
        blend = min(1.0, max(0.0, num("blend", BLEND)))
        if abs(blend - BLEND) > 1e-9:
            out["blend"] = blend
    random = min(1.0, max(0.0, num("random", 0.0)))
    if random > 0:
        out["random"] = random
    if glide > 0:
        out["glide"] = min(GLIDE, glide)
        if voice.get("touching") is True:
            out["touching"] = True
        curve = min(1.0, max(-1.0, num("curve", GLIDE_CURVE)))
        if abs(curve - GLIDE_CURVE) > 1e-9:
            out["curve"] = curve
    if voice.get("legato") is True:
        out["legato"] = True
    return out


def clean_mode(mode):
    """The Wave box's mode checked (hz["mode"], see MODES): {"kind", its settings (each within its range; missing =
    where its knob starts)}, or {} (no mode)."""
    if not isinstance(mode, dict) or mode.get("kind") not in MODES:
        return {}
    return {"kind": mode["kind"], **clean_settings(mode, MODES[mode["kind"]])}


def clean_settings(got, table):
    """Settings checked against their (lowest, highest, start) table: each within its range (missing / no good = its
    start); counts whole."""
    out = {}
    for key, (lo, hi, start) in table.items():
        try:
            v = float(got.get(key, start))
        except (TypeError, ValueError):
            v = start
        v = min(hi, max(lo, v if math.isfinite(v) else start))
        out[key] = float(round(v)) if key in ("every", "repeats", "octaves", "octave", "semi") else v
    return out


def clean_osc2(osc):
    """OSC B checked (hz["osc2"], see OSC2): {"wave" (a waveform or "none"), its settings, "split": True (only when
    the key rows take turns), "a_off": True (only when OSC A is switched off: OSC B alone), "mode" (as clean_mode,
    only with one)}, or {} (off)."""
    if not isinstance(osc, dict):
        return {}
    out = {"wave": osc["wave"] if osc.get("wave") in WAVES else "none", **clean_settings(osc, OSC2)}
    for flag in ("split", "a_off"):
        if osc.get(flag) is True:
            out[flag] = True
    mode = clean_mode(osc.get("mode"))
    if mode:
        out["mode"] = mode
    return out


def osc2_shift(osc):
    """OSC B's tone from the note's, in keys."""
    return 12.0 * osc["octave"] + osc["semi"] + osc["fine"] / 100.0


def clean_arp(arp):
    """The Arpeggio box checked (hz["arp"], see ARP): {pattern, chord, speed, octaves, gate, swing (left out at 0),
    scale and root (only with a scale, see SCALES), steps (only with the "steps" pattern, see STEPS)}, or {} (off)."""
    if not isinstance(arp, dict):
        return {}
    out = {"pattern": arp.get("pattern") if arp.get("pattern") in ARP_PATTERNS else "up",
           "chord": arp.get("chord") if arp.get("chord") in CHORDS else "placed", **clean_settings(arp, ARP)}
    if not out["swing"]:
        del out["swing"]
    if arp.get("scale") in SCALES:
        out["scale"] = arp["scale"]
        root = arp.get("root")
        good = isinstance(root, (int, float)) and not isinstance(root, bool) and root in range(12)
        out["root"] = int(root) if good else 0
    if out["pattern"] == "steps":
        out["steps"] = clean_steps(arp.get("steps"))
        out["count"] = clean_count(arp.get("count"), arp.get("steps"))
    return out


def clean_count(count, steps=None):
    """How many of the Arpeggio's steps play (1..STEPS); none: as many as were given (made before the count was
    saved on its own), else START_COUNT."""
    if isinstance(count, (int, float)) and not isinstance(count, bool) and math.isfinite(count):
        return int(min(STEPS, max(1, round(count))))
    given = len([s for s in steps if isinstance(s, dict)]) if isinstance(steps, list) else 0
    return min(STEPS, given) if given else START_COUNT


def clean_steps(steps):
    """The Arpeggio's own steps checked (see STEPS): always STEPS of them (missing ones as START_STEPS)."""
    out = []
    for s in steps if isinstance(steps, list) else ():
        if not isinstance(s, dict) or len(out) >= STEPS:
            continue
        step = {}
        for k, (lo, hi, start) in STEP.items():
            v = s.get(k)
            good = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            v = min(hi, max(lo, v)) if good else start
            step[k] = int(round(v)) if isinstance(lo, int) else float(v)
        if s.get("tie") is True:
            step["tie"] = True
        out.append(step)
    return out + [dict(s) for s in START_STEPS[len(out):]]


def in_scale(key, scale, root):
    """A key moved to the nearest key of the scale (SCALES) counted from root (0 = C); half way: down; one that
    would leave the keys (0..127) goes the other way."""
    steps = SCALES[scale]
    for d in (0, -1, 1, -2, 2, -3, 3, -4, 4):
        if (key + d - root) % 12 in steps and 0 <= key + d <= 127:
            return key + d
    return key


def arpeggiated(tones, arp, left=0.0):
    """The tones as the Arpeggio box plays them: from each start (the notes starting together = one chord) a run of
    short tones, one every 1 / speed beats while any of them is held, through their pitches (with the chord's steps
    and octaves) in the pattern's order; a note let go drops out of the run. Each keeps its note's tune and gates;
    slides made by hand are left out (the run's notes are new ones). The "steps" pattern: the box's own steps
    (STEPS) pick the held notes, low to high, each with its octave, loudness ("level" on the tone) and length."""
    step = 1.0 / arp["speed"]
    late = arp.get("swing", 0.0) * step / 2  # (every second step: that much later, the one before it longer)
    steps = arp["steps"][:arp["count"]] if arp["pattern"] == "steps" else None

    def at(k, t0):
        """Step k from t0: (its start, how long its slot is)."""
        t = t0 + k * step
        # (swing by the beat grid, counted from the Hz bass's start as a synth counts from the song's: the steps
        # between the grid's beats come late, wherever the chord starts; user, 2026-10-08)
        odd = bool(late) and math.floor(t / step + 0.5) % 2 == 1
        return (t + late, step - late) if odd else (t, step + late)
    out = []
    order = sorted(tones, key=lambda n: n["t"])
    i = 0
    while i < len(order):
        t0 = order[i]["t"]
        j = i
        while j < len(order) and order[j]["t"] <= t0 + 1e-9:
            j += 1
        chord, i = order[i:j], j
        pitches = {}  # (key, tune) -> the note it's from (one pitch from two notes: the one held longest)
        for n in sorted(chord, key=lambda n: n["len"]):
            for k in CHORDS[arp["chord"]]:
                for o in range(1 if steps else int(arp["octaves"])):  # (steps: their own octaves, below)
                    key = n["key"] + k + 12 * o
                    if arp.get("scale") and not steps:  # (moved into the scale: two landing on one key = one)
                        key = in_scale(key, arp["scale"], arp.get("root", 0))
                    pitches[(key, n["cents"])] = n
        items = sorted(pitches.items(), key=lambda kv: kv[0][0] + kv[0][1] / 100)
        end = max(n["t"] + n["len"] for n in chord)
        # (Random: drawn from the chord's beat in the song (left = the shape's edge), so a Split or a moved edge
        # leaves it as it was)
        rng = np.random.default_rng(int(round((left + t0) * 1000)) % (2 ** 32))
        k = 0
        while True:
            t, slot = at(k, t0)
            if t >= end - 1e-9:
                break
            held = [(key, n) for key, n in items if n["t"] + n["len"] > t + 1e-9]
            if steps:
                tone = stepped(steps, k, t, held, arp, lambda j: at(j, t0), end)
                if tone:
                    tone["id"] = len(out) + 1
                    out.append(tone)
            elif held:
                seq = held if arp["pattern"] != "down" else held[::-1]
                if arp["pattern"] == "updown" and len(held) > 2:
                    seq = held + held[-2:0:-1]
                pick = seq[int(rng.integers(len(seq)))] if arp["pattern"] == "random" else seq[k % len(seq)]
                (key, cents), n = pick
                if 0 <= key <= 127:
                    tone = {"t": t, "len": max(MIN_LEN, min(slot * arp["gate"], n["t"] + n["len"] - t)), "key": key,
                            "cents": cents, "id": len(out) + 1, "to": []}
                    tone.update({f: n[f] for f in ("auto", "gate") if f in n})
                    out.append(tone)
            k += 1
    return sorted(out, key=lambda n: (n["t"], n["key"]))


def stepped(steps, k, t, held, arp, at, end):
    """The "steps" pattern's tone at step k (at t; held = the pitches still held, low to high; at(j) = step j's
    (start, slot); end = when the last note is let go), or None: a rest, nothing held, one held on by the step before
    (tie)."""
    s, count = steps[k % len(steps)], len(steps)
    if k and steps[(k - 1) % count].get("tie") or not s["note"] or s["level"] <= 0 or not held:
        return None
    (key, cents), n = held[(s["note"] - 1) % len(held)]
    key += 12 * (s["octave"] + (k // count) % int(arp["octaves"]))  # (Octaves: each time round, one higher)
    if arp.get("scale"):
        key = in_scale(key, arp["scale"], arp.get("root", 0))
    if not 0 <= key <= 127:
        return None
    j = k  # (tied: on to the end of the last step it's tied through)
    while steps[j % count].get("tie") and at(j + 1)[0] < end - 1e-9:
        j += 1
    tj, slot = at(j)
    tone = {"t": t, "len": max(MIN_LEN, min(tj - t + slot * steps[j % count]["length"] * arp["gate"],
                                            n["t"] + n["len"] - t)),
            "key": key, "cents": cents, "to": []}
    if s["level"] < 1.0:
        tone["level"] = s["level"]
    tone.update({f: n[f] for f in ("auto", "gate") if f in n})
    return tone


def clean_rack(rack):
    """The Effects tab checked (hz["rack"], see RACK): [{"kind", its settings, "off": True when switched off}] in
    order, each kind once."""
    out, seen = [], set()
    for e in rack if isinstance(rack, list) else ():
        if isinstance(e, dict) and e.get("kind") in RACK and e["kind"] not in seen:
            seen.add(e["kind"])
            out.append({"kind": e["kind"], **clean_settings(e, RACK[e["kind"]]),
                        **({"off": True} if e.get("off") is True else {})})
    return out


def rack_on(hz, kind):
    """The Effects tab's `kind` when it's there and on, else None."""
    return next((e for e in hz.get("rack") or () if e["kind"] == kind and not e.get("off")), None)


def rack_tail(hz):
    """Beats the Effects tab's echo and reverb make the sound go on for after the notes."""
    echo, reverb = rack_on(hz, "echo"), rack_on(hz, "reverb")
    return (echo["repeats"] * echo["time"] if echo else 0.0) + (reverb["length"] if reverb else 0.0)


def compress(loud, comp):
    """A steady loudness (0..1, 1 = full) through the Effects tab's compressor (RACK): what's over its threshold cut
    to 1 / ratio of how far over (in dB), then all of it `gain` dB louder, never past full; silence stays silent.
    (Its picture; the notes get the same, but over time: KeyGrid.comp_curve.)"""
    loud = np.asarray(loud, float)
    db = 20.0 * np.log10(np.maximum(loud, 1e-12))
    cut = np.maximum(0.0, db - comp["threshold"]) * (1.0 - 1.0 / comp["ratio"])
    return np.where(loud > 0, np.minimum(1.0, 10.0 ** ((db - cut + comp["gain"]) / 20.0)), 0.0)


def clean_extra(hz):
    """The synth window's own settings of a Hz bass (EXTRAS, not lines) checked: {name: its settings} for those that
    do something."""
    out = {}
    for name in EXTRAS:
        got = CLEAN_EXTRA[name](hz.get(name))
        if got:
            out[name] = got
    return out


def copies(hz):
    """The tones of the Voice box's copies, in cents from the note's (one copy: [0])."""
    v = hz.get("voice") or {}
    n = v.get("voices", 1)
    if n < 2:
        return [0.0]
    return [v["detune"] * (i / (n - 1) - 0.5) for i in range(n)]


def blend_gains(hz):
    """How loud each Voice copy is (copies' order), as a synth's unison Blend: the middle one (two, with an even
    count) full and the outer ones quieter below BLEND, the other way round above it (0 = only the middle ones,
    1 = only the outer ones); at BLEND (or one copy) all full."""
    v = hz.get("voice") or {}
    n, b = v.get("voices", 1), v.get("blend", BLEND)
    if n < 3:  # (two copies are both "the middle two": nothing to blend)
        return np.ones(n)
    middle = np.isin(np.arange(n), ((n - 1) // 2, n // 2))
    return np.where(middle, min(1.0, 2.0 * (1.0 - b)), min(1.0, 2.0 * b))


def clean_bypass(kept):
    """The settings of the synth window's boxes switched off (Bypass) checked: hz["bypass"] = {"mode": as hz["mode"],
    "voice": as hz["voice"], "boxes": the boxes switched off (OFF_BOXES; so a box stays off while its knobs do
    nothing)}, kept here while off, where nothing that makes the notes reads them."""
    kept = kept if isinstance(kept, dict) else {}
    out = {}
    for name, clean in (("mode", clean_mode), ("voice", clean_voice)):
        got = clean(kept.get(name))
        if got:
            out[name] = got
    boxes = kept.get("boxes") if isinstance(kept.get("boxes"), list) else ()
    boxes = [b for b in OFF_BOXES if b in boxes]
    if boxes:
        out["boxes"] = boxes
    return out


def clean_kept(kept):
    """The synth window's knobs that do nothing right now (an Arpeggio off, Sweep unticked, Detune with one voice...)
    checked: hz["kept"] = {knob: its value (a number, True / False, a short name, or "arp_steps": the Arpeggio's
    steps as in hz["arp"]["steps"])}, so they're there again later, as
    in a synth (user); nothing that makes the notes reads them (the window checks each against its knob)."""
    out = {}
    for k, v in (kept.items() if isinstance(kept, dict) else ()):
        if not isinstance(k, str) or len(k) > 40 or len(out) >= 200:
            continue
        if k == "arp_steps":  # (the Arpeggio's own steps while another Pattern is picked)
            if isinstance(v, list) and v:
                out[k] = clean_steps(v)
        elif isinstance(v, bool) or isinstance(v, str) and len(v) <= 40:
            out[k] = v
        elif isinstance(v, (int, float)) and math.isfinite(v):
            out[k] = float(v)
    return out


MACROS = 4  # the synth window's macro knobs


def clean_macro(macro):
    """The synth window's macros checked: hz["macro"] = {"values": each macro knob 0..1 (MACROS of them), "links":
    [[macro, knob, amount -1..1 of the knob's turn at the macro all the way]], "base": {knob: its own value, the
    macros' turns added to it}}. Only the window reads it: the knobs' lines already hold what the macros do."""
    macro = macro if isinstance(macro, dict) else {}
    vals = macro.get("values") if isinstance(macro.get("values"), list) else []
    vals = [min(1.0, max(0.0, float(v))) if isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) else 0.0 for v in vals[:MACROS]]
    vals += [0.0] * (MACROS - len(vals))
    links, seen = [], set()
    for link in macro.get("links") if isinstance(macro.get("links"), list) else ():
        if (isinstance(link, list) and len(link) == 3 and link[0] in range(MACROS) and not isinstance(link[0], bool)
                and isinstance(link[1], str) and len(link[1]) <= 40 and isinstance(link[2], (int, float))
                and not isinstance(link[2], bool) and math.isfinite(link[2]) and (link[0], link[1]) not in seen
                and len(links) < 200):
            seen.add((link[0], link[1]))
            links.append([int(link[0]), link[1], min(1.0, max(-1.0, float(link[2])))])
    linked = {k for _, k, _ in links}
    base = {k: float(v) for k, v in (macro.get("base").items() if isinstance(macro.get("base"), dict) else ())
            if k in linked and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    if not links and not any(vals):
        return {}
    return {"values": vals, "links": links, "base": base}


# hz["mod"] (the synth window's MOD tab, the matrix): sources that move effect lines over time. LFO 3 / LFO 4 (LFO 1
# and 2 are the Vibrato and the Tremolo): one of LOOP_SHAPES, `rate` times a beat, MOD_LFO_MODES: "free" = counted
# from the shape's left edge (like a repeating line), "restart" = starting over at each note, "once" = one wave from
# each note's start, then staying on its last value. ENV 2 / ENV 3 (ENV 1 is the Volume box): attack / decay /
# release in beats, sustain 0..1, from each note's start (as the Volume knobs' line: adsr_line). "velocity" = each
# note's own loudness (an Arpeggio step's), "note" = its pitch (0 = key 0, 1 = key 127). Each source gives 0..1.
MOD_LFOS, MOD_ENVS = ("lfo3", "lfo4"), ("env2", "env3")
MOD_SOURCES = MOD_LFOS + MOD_ENVS + ("velocity", "note")
MOD_LFO = {"rate": (0.0, 64.0, 1.0)}
MOD_LFO_SHAPES = tuple(LOOP_SHAPES)
MOD_LFO_MODES = ("free", "restart", "once")
MOD_ENV = {"attack": (0.0, 64.0, 0.0), "decay": (0.0, 64.0, 1.0), "sustain": (0.0, 1.0, 0.0),
           "release": (0.0, 64.0, 0.0)}
MOD_LINKS = 64  # links at most
# a link moves its effect's line, or (none: the box does nothing) the effect from where it does nothing (live), but
# these only while their box is on (Sweep ticked, a waveform picked), like a synth's filter / oscillator switched off
MOD_NEED_LINE = ("sweep", "wave") + tuple(WAVES)
MOD_LINES = FX + ("wave",)  # "wave" = whichever waveform's line there is (the Wave box's Shape)
# the knobs that make no line a link can move too (engine rework, user 2026-10-09), each read where the engine uses it
# (setting_at): knob -> (lowest, highest, top of its curve or None = straight). A link adds amount x the source of
# the knob's whole turn to where it points (along its curve), kept within lowest..highest. Each Mode's amount knob
# (MODE_AMOUNTS), OSC A's as "fm_depth", OSC B's as "osc2_fm_depth"
MODE_AMOUNTS = {"fm": "depth", "pulse": "width", "sync": "amount", "growl": "amount", "crush": "amount"}
MOD_SETTINGS = dict({"tremolo_depth": (*LFO["tremolo_depth"], None), "sweep_track": (*LFO["sweep_track"], None),
                     "blend": (0.0, 1.0, None), "osc2_shape": (*OSC2["shape"][:2], None),
                     "osc2_level": (*OSC2["level"][:2], None)},
                    **{f"{osc}{kind}_{k}": (*MODES[kind][k][:2], None) for osc in ("", "osc2_")
                       for kind, k in MODE_AMOUNTS.items()})
# ... the Effects tab's (knob = effect_setting). Its effects work on the whole sound, not on each note (as a synth's
# effects after the voices): sources counted from each note follow the newest note (mod_value with no tone)
TIME_TOP, TIME_MOST = 4.0, 64.0  # (a time knob: beats along a curve up to TIME_TOP, typed up to TIME_MOST)
RATE_TOP = 10.0  # (a Rate knob: times a beat along a curve up to this)
# (Chorus / Flanger Rate: their waves added up over the whole sound, KeyGrid.rack_turns)
RACK_MOD = {"chorus": ("depth", "rate"), "flanger": ("depth", "mix", "rate"), "echo": ("fade",),
            "reverb": ("level", "scatter"), "compressor": ("threshold", "ratio", "gain", "attack", "release")}
RACK_CURVES = {("compressor", "ratio"): RACK["compressor"]["ratio"][1],  # (curved knobs: their top)
               ("compressor", "attack"): TIME_TOP, ("compressor", "release"): TIME_TOP,
               ("chorus", "rate"): RATE_TOP, ("flanger", "rate"): RATE_TOP}
MOD_RACK = {f"{kind}_{k}": (kind, k) for kind, ks in RACK_MOD.items() for k in ks}
MOD_SETTINGS.update({name: (*RACK[kind][k][:2], RACK_CURVES.get((kind, k))) for name, (kind, k) in MOD_RACK.items()})
# ... the time knobs (beats; along a curve up to TIME_TOP, typed up to TIME_MOST): a running envelope's stage gets
# shorter or longer as they move (timed_line). The Volume box's Attack / Decay / Release (ADSR_KNOBS) only while
# the Volume line is as its knobs make it (knob_adsr)
ADSR_KNOBS = ("attack", "decay", "release")
MOD_SETTINGS.update({name: (0.0, TIME_MOST, TIME_TOP) for name in ADSR_KNOBS})
TIME_STEP = 1 / 1024  # beats: how finely a moved envelope is worked out (fewer steps on very long notes)
MOD_TARGETS = MOD_LINES + tuple(MOD_SETTINGS)
# the synth window's box each effect is in: a box switched off (hz["bypass"]["boxes"]) stops its links too
MOD_BOXES = dict({w: "wave" for w in tuple(WAVES) + ("wave", "octave")}, volume="volume", pitch="pitch", vibrato="vibrato",
                 tremolo="tremolo", sweep="tone", wah="tone", slant="character", offpitch="character",
                 noisy="character", groups="character")


def mod_start():
    """hz["mod"] as it starts: every source at its knobs' start, nothing linked."""
    lfo = dict({k: s for k, (_, _, s) in MOD_LFO.items()}, shape="sine", mode="free", timing="free")
    return {"lfo": [dict(lfo) for _ in MOD_LFOS], "env": [{k: s for k, (_, _, s) in MOD_ENV.items()}
                                                          for _ in MOD_ENVS], "links": []}


def clean_mod(mod):
    """The MOD tab checked: hz["mod"] = {"lfo": [LFO 3, LFO 4: {shape, rate, mode, timing (only the knob cares, as
    LFO's)}], "env": [ENV 2, ENV 3: MOD_ENV], "links": [{"from": a MOD_SOURCES, "to": an effect (MOD_TARGETS), "amount": -1..1
    of the line's whole height at the source's top, "bipolar": True = the source swings both ways round the line
    (left out when False), "knob": the synth window's knob it was linked on (only the window reads it)}], "phase":
    beats the Free LFOs count late by (the shape's left edge moved, shifted_hz: they stay in step with the song)},
    or {} when it's all as it starts."""
    mod = mod if isinstance(mod, dict) else {}
    out = mod_start()
    for i, got in enumerate((mod.get("lfo") if isinstance(mod.get("lfo"), list) else [])[:len(MOD_LFOS)]):
        if isinstance(got, dict):
            lfo = out["lfo"][i]
            lfo.update(clean_settings(got, MOD_LFO))
            for key, ok in (("shape", MOD_LFO_SHAPES), ("mode", MOD_LFO_MODES), ("timing", TIMINGS)):
                if got.get(key) in ok:
                    lfo[key] = got[key]
    for i, got in enumerate((mod.get("env") if isinstance(mod.get("env"), list) else [])[:len(MOD_ENVS)]):
        if isinstance(got, dict):
            out["env"][i].update(clean_settings(got, MOD_ENV))
    seen = set()
    for link in mod.get("links") if isinstance(mod.get("links"), list) else ():
        if isinstance(link, dict) and link.get("to") in WAVES:  # (a waveform's own name: the Shape knob's "wave")
            link = dict(link, to="wave")
        if not (isinstance(link, dict) and link.get("from") in MOD_SOURCES and link.get("to") in MOD_TARGETS):
            continue
        if link["to"] == f"{link['from']}_rate":  # (an LFO moving its own Rate: never)
            continue
        a = link.get("amount")
        if (isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a)
                and (link["from"], link["to"]) not in seen and len(out["links"]) < MOD_LINKS):
            seen.add((link["from"], link["to"]))
            out["links"].append(dict({"from": link["from"], "to": link["to"], "amount": min(1.0, max(-1.0, float(a)))},
                                     **({"bipolar": True} if link.get("bipolar") is True else {}),
                                     **({"knob": link["knob"]} if isinstance(link.get("knob"), str)
                                        and len(link["knob"]) <= 40 else {})))
    phase = mod.get("phase")
    if isinstance(phase, (int, float)) and not isinstance(phase, bool) and math.isfinite(phase) and phase:
        out["phase"] = float(phase)
    return {} if {k: v for k, v in out.items() if k != "phase"} == mod_start() else out


CLEAN_EXTRA = {"voice": clean_voice, "mode": clean_mode, "rack": clean_rack, "arp": clean_arp, "bypass": clean_bypass,
               "kept": clean_kept, "macro": clean_macro, "osc2": clean_osc2, "mod": clean_mod}
EXTRAS = tuple(CLEAN_EXTRA)  # the synth window's own settings (not lines), each checked by its CLEAN_EXTRA


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


def live(hz, left=0.0):
    """hz as it's heard: without the effects switched off (Bypass), its tones as the Arpeggio box plays them (left =
    the shape's left edge: the Random pattern counts from the song's start)."""
    if hz.get("arp") and hz.get("tones"):  # (taken out once played, so live of live is the same)
        arp = hz["arp"]
        hz = {k: v for k, v in hz.items() if k not in ("arp", "_memo")}  # (other tones: nothing cached holds)
        hz["tones"] = arpeggiated(hz["tones"], arp, left)
    fx = hz.get("fx") or {}
    boxes_off = (hz.get("bypass") or {}).get("boxes", ())
    if ("volume" not in fx and "volume" not in boxes_off and any(
            link["to"] in ADSR_KNOBS for link in (hz.get("mod") or {}).get("links", ()))):
        # (the Volume knobs' times moved with no Volume line: as the knobs have it, full all along)
        pts, at, every = adsr_line(0.0, 0.0, 1.0, 0.0)
        hz = {k: v for k, v in hz.items() if k != "_memo"}
        hz.update(fx=dict(fx, volume=pts), loop=dict(hz.get("loop") or {}, volume=every),
                  sustain=dict(hz.get("sustain") or {}, volume=at), **{"from": dict(hz.get("from") or {}, volume="note")})
        fx = hz["fx"]
    missing = [link["to"] for link in (hz.get("mod") or {}).get("links", ()) if link["to"] in MOD_BOXES
               and link["to"] not in fx and link["to"] not in MOD_NEED_LINE and MOD_BOXES[link["to"]] not in boxes_off]
    if missing:  # (the MOD tab moves them from where they do nothing)
        hz = dict(hz, fx=dict(fx, **{name: [[0.0, NEUTRAL.get(name, 0.0)]] for name in missing}))
        hz.pop("_memo", None)
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


def cached(hz, key, make):
    """make() worked out once for this hz while its notes are being made (hz["_memo"]: only on the private copies
    the note making works on; without it, every time)."""
    memo = hz.get("_memo")
    if memo is None:
        return make()
    if key not in memo:
        memo[key] = make()
    return memo[key]


def chains(tones, joined=()):
    """{tone id: (beat its chain of slides starts at, beat the chain ends at)}: a tone reached by a slide belongs to
    the chain of the (earliest) tone sliding into it; joined = more (a, b) pairs counted like slides (Legato:
    legato_links)."""
    by = {n["id"]: n for n in tones}
    came = {}
    for a, b in [(a, b) for a, b, _ in links(tones)] + list(joined):
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
    tones = hz.get("tones") or ()
    got = cached(hz, "chains", lambda: chains(tones, legato_links(hz.get("voice"), tones)))
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
    after = np.clip(after, 0.0, 1.0)  # (a fast fall from high above where the note got to would go under 0)
    return np.where(d < 0, line_at(pts, np.minimum(u, at)), after)


def tails(hz):
    """{tone id: beats its sound goes on after its end}: with sustain points (hz["sustain"], effects switched off
    don't count) the falls play after a note ends, so the tones a chain of slides ends with sound on for the
    longest fall, also under the notes after them (like a synth's voices), cut only where a tone of the same pitch
    starts (both there would make their repeats twice as many: a higher tone)."""
    hz = live(hz)
    fall = longest_fall(hz)
    tones = hz.get("tones") or ()
    if fall <= 1e-12 or not tones:
        return {}
    joined = legato_links(hz.get("voice"), tones)
    got = chains(tones, joined)
    starts = {}
    for n in tones:
        starts.setdefault(pitch(n), []).append(n["t"])
    leaving = {a["id"] for a, _, _ in links(tones)} | {a["id"] for a, _ in joined}
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
    """How long the tones sound together, in beats (tones_span and the falls after them, tails; the Effects tab's
    echo and reverb after that, rack_tail)."""
    hz = live(hz)  # (as played: the Arpeggio box's tones)
    got, tones = tails(hz), hz.get("tones") or ()
    end = max((n["t"] + n["len"] + got.get(n["id"], 0.0) for n in tones), default=0.0)
    return end + rack_tail(hz) if tones else end


def fx_at(hz, name, beat, tone=None, sources=None):
    """The value of an effect at beat (an array, from the shape's left edge; 0 when there's no such line).
    Before the first point and after the last one the line stays flat, unless it repeats (hz["loop"]); a repeating
    one is made stronger or weaker by its amount line (hz["amount"]). One counted from each note (hz["from"]):
    tone = the tone it's for (each note has its own), else from the latest note start (note_beats); with a sustain
    point: sustained. The MOD tab's links to it (hz["mod"]) then move it by their sources (mod_value), kept within
    0..1 (one to an effect with no line: live gave it one where it does nothing, see MOD_NEED_LINE). sources = a
    dict keeping each source's values for this beat and tone (the same run's effects share them)."""
    pts = (hz.get("fx") or {}).get(name)
    if not pts:
        return np.zeros(np.shape(beat))
    every = (hz.get("loop") or {}).get(name)
    mode = (hz.get("from") or {}).get(name) if every else None
    at = (hz.get("sustain") or {}).get(name) if mode == "note" else None
    span = note_span(hz, np.asarray(beat, float), tone) if at is not None else None
    timed = timed_line(hz, name, beat, tone)
    if timed is not None:  # (its time knobs moved by the MOD tab)
        v = timed
    elif span is not None:
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
    moved = mod_turn(hz, (name, "wave") if name in WAVES else (name,), beat, tone, sources, v)
    return v if moved is None else np.clip(moved, 0.0, 1.0)


def mod_turn(hz, targets, beat, tone=None, sources=None, start=0.0):
    """start moved by the MOD tab's links to these targets at beat (an array) for tone's note: amount x source
    added on, link by link (both ways: round the middle), or None when none is linked. sources = as fx_at's."""
    turn = None
    for link in (hz.get("mod") or {}).get("links", ()):
        if link["to"] in targets:
            sources = {} if sources is None else sources
            if link["from"] not in sources:
                sources[link["from"]] = mod_value(hz, link["from"], beat, tone)
            s = sources[link["from"]]
            turn = (start if turn is None else turn) + link["amount"] * (2.0 * s - 1.0 if link.get("bipolar") else s)
    return turn


def setting_base(hz, target):
    """Where a knob of MOD_SETTINGS is set (its own value, before the MOD tab moves it), or None while it does
    nothing: no Tremolo / Sweep line (Depth, Key track), under 3 voices (Blend), OSC B off or with no waveform
    (Shape), another Mode or the oscillator off (a Mode's knobs), the Voice box off (Glide, Curve); a time knob
    whose line was drawn otherwise."""
    if target in TIMED_KNOBS:  # (a time knob: while its line is as the knobs make it)
        got = TIMED[TIMED_KNOBS[target]][1](hz)
        return got[target] if got else None
    return plain_base(hz, target)


def plain_base(hz, target):
    """setting_base of a knob read as it's set (not from a line)."""
    fx, lfo, osc = hz.get("fx") or {}, hz.get("lfo") or {}, hz.get("osc2") or {}
    if target in MOD_RACK:  # (an Effects tab effect's: while it's there and on)
        kind, key = MOD_RACK[target]
        e = rack_on(hz, kind)
        return e[key] if e else None
    if target in ("glide", "curve"):  # (a glide is worked out once, at the note's start: tone_runs)
        if "voice" in (hz.get("bypass") or {}).get("boxes", ()):
            return None
        return (hz.get("voice") or {}).get(target, 0.0 if target == "glide" else GLIDE_CURVE)
    if target in ("tremolo_wait", "tremolo_rise"):
        return lfo.get(target, 0.0) if "tremolo" in fx else None
    if target == "tremolo_depth":
        return lfo.get("tremolo_depth", TREMOLO_DEPTH) if "tremolo" in fx else None
    if target == "vibrato_rate":
        return lfo.get("vibrato_rate", VIBRATO_RATE) if "vibrato" in fx else None
    if target in LFO_RATES:
        return (hz.get("mod") or mod_start())["lfo"][MOD_LFOS.index(LFO_RATES[target])]["rate"]
    if target == "sweep_track":
        return lfo.get("sweep_track", 0.0) if "sweep" in fx else None
    if target == "blend":
        voice = hz.get("voice") or {}
        return voice.get("blend", BLEND) if voice.get("voices", 1) >= 3 else None
    if target.startswith("osc2_"):
        key = target[5:]
        if not osc or key == "shape" and osc["wave"] == "none":
            return None
        if key in OSC2:
            return osc[key]
        mode = osc.get("mode") or {}
    else:
        key, mode = target, ({} if osc.get("a_off") else hz.get("mode") or {})
    kind, _, setting = key.partition("_")
    return mode.get(setting) if mode.get("kind") == kind else None


def setting_at(hz, target, base, beat, tone=None, sources=None):
    """A MOD_SETTINGS knob's value at beat (an array) for tone's note: base (setting_base) moved by the links to it,
    along the knob's turn (as the lines: amount 1 = the whole turn), kept within its ends."""
    turn = mod_turn(hz, (target,), beat, tone, sources)
    if turn is None:
        return np.full(np.shape(beat), float(base))
    return turned_setting(target, base, turn)


def turned_setting(target, base, turn):
    """A MOD_SETTINGS knob set at base, turned that much more (0..1 = its whole turn), kept within its ends."""
    lo, hi, most = MOD_SETTINGS[target]
    if most:  # (a curved knob: points at the square root of how far up it is)
        v = most * np.maximum(0.0, math.sqrt(max(0.0, base) / most) + turn) ** 2
    else:
        v = base + turn * (hi - lo)
    return np.clip(v, lo, hi)


def same_points(a, b):
    """Two lines' points the same (to a millionth)."""
    def same(x, y):
        return x == y if isinstance(x, str) or isinstance(y, str) else abs(x - y) < 1e-6
    return len(a) == len(b) and all(len(p) == len(q) and all(map(same, p, q)) for p, q in zip(a, b))


def knob_adsr(hz):
    """The Volume line as the Volume box's knobs make it (adsr_line, once per note with its sustain point):
    {attack, decay, sustain, release}; full all along (or none) = (0, 0, 1, 0); None when it was drawn otherwise."""
    plain = {"attack": 0.0, "decay": 0.0, "sustain": 1.0, "release": 0.0}
    pts = (hz.get("fx") or {}).get("volume")
    if not pts or "volume" not in (hz.get("loop") or {}) and all(p[1] == 1.0 for p in pts):
        return plain if "volume" not in (hz.get("amount") or {}) else None
    at = (hz.get("sustain") or {}).get("volume")
    if (at is None or (hz.get("from") or {}).get("volume") != "note" or "volume" in (hz.get("fit") or ())
            or "volume" in (hz.get("amount") or {})):
        return None
    attack = pts[1][0] if len(pts) > 1 and pts[0][1] == 0.0 and list(pts[0][2:]) == [-FAST] else 0.0
    attack = min(max(0.0, attack), at)
    got = {"attack": attack, "decay": at - attack, "sustain": float(line_at(pts, at)),
           "release": pts[-1][0] - at if pts[-1][0] > at + 1e-9 else 0.0}
    return got if same_points(adsr_line(**got)[0], pts) else None


def stage_sums(length, dt):
    """For a stage whose length (beats) is read at every step of a grid dt apart: how far through a stage of that
    length the time from the grid's start has come (adding dt / length step by step: a length moving while the
    stage runs makes it go faster or slower). A length of 0 = done at once."""
    step = dt / np.maximum(np.asarray(length, float), 1e-12)
    return np.concatenate([[0.0], np.cumsum(step[:-1])])


def stage_end(sums, g, t0):
    """When a stage starting at t0 ends (sums = stage_sums over the grid g), inf when it never does."""
    want = float(np.interp(t0, g, sums)) + 1.0
    return float(np.interp(want, sums, g)) if want <= sums[-1] else math.inf


def knob_bend(hz, name, key):
    """The Pitch / Sweep line as its box's knobs make it (pitch_line, sweep_line: from `start` to `end`, fast first, in
    `key` beats, once per note): {key, start, end}, or None (no such line, drawn otherwise)."""
    pts = (hz.get("fx") or {}).get(name)
    if (not pts or len(pts) != 2 or list(pts[0][2:]) != [FAST] or len(pts[1]) > 2 or pts[0][0] != 0.0
            or (hz.get("from") or {}).get(name) != "note" or name in (hz.get("fit") or ())
            or name in (hz.get("sustain") or {}) or name in (hz.get("amount") or {})
            or abs((hz.get("loop") or {}).get(name, 0.0) - max(LOOP[0], pts[1][0])) > 1e-9):
        return None
    return {key: pts[1][0], "start": pts[0][1], "end": pts[1][1]}


def knob_vibrato(hz):
    """The Vibrato line as its knobs make it (none for Delay = vibrato_wait beats from each note's start, then coming
    in over Rise = vibrato_delay beats, linear): {vibrato_wait, vibrato_delay, depth}, or None (drawn otherwise)."""
    pts = (hz.get("fx") or {}).get("vibrato")
    every = (hz.get("loop") or {}).get("vibrato")
    if (not pts or any(len(p) > 2 for p in pts) or name_bent(hz, "vibrato")
            or (every is not None) == (len(pts) == 1)):
        return None
    if len(pts) == 1:  # (all along)
        return {"vibrato_wait": 0.0, "vibrato_delay": 0.0, "depth": pts[0][1]}
    if ((hz.get("from") or {}).get("vibrato") != "note" or list(pts[0]) != [0.0, 0.0]
            or abs(every - max(LOOP[0], pts[-1][0])) > 1e-9):
        return None
    if len(pts) == 2:
        return {"vibrato_wait": 0.0, "vibrato_delay": pts[1][0], "depth": pts[1][1]}
    if len(pts) == 3 and pts[1][1] == 0.0:
        return {"vibrato_wait": pts[1][0], "vibrato_delay": pts[2][0] - pts[1][0], "depth": pts[2][1]}
    return None


def name_bent(hz, name):
    """An effect's line stretched over each note, with a sustain point or an amount line (no knob makes those)."""
    return (name in (hz.get("fit") or ()) or name in (hz.get("sustain") or {})
            or name in (hz.get("amount") or {}))


def knobs_set(*knobs):
    """A TIMED reader for knobs read as they're set: {knob: value}, or None while one does nothing (plain_base)."""
    def read(hz):
        got = {k: plain_base(hz, k) for k in knobs}
        return None if None in got.values() else got
    return read


# the envelope-like things whose time knobs the MOD tab moves (timed_line): name -> its knobs, how they're read. The
# lines' (from them, as their knobs make them), then those worked out in KeyGrid: the Tremolo's Delay / Rise (how
# much of it has come in), each Mode's Time (FM's depth falling, Sync's rise: how far through it)
TIMED = {"volume": (ADSR_KNOBS, knob_adsr), "pitch": (("time",), lambda hz: knob_bend(hz, "pitch", "time")),
         "sweep": (("sweep_time",), lambda hz: knob_bend(hz, "sweep", "sweep_time")),
         "vibrato": (("vibrato_wait", "vibrato_delay"), knob_vibrato),
         "tremolo_in": (("tremolo_wait", "tremolo_rise"), knobs_set("tremolo_wait", "tremolo_rise")),
         **{f"{osc}{kind}_in": ((f"{osc}{kind}_time",), knobs_set(f"{osc}{kind}_time"))
            for osc in ("", "osc2_") for kind in ("fm", "sync")}}
TIMED_KNOBS = {k: line for line, (knobs, _) in TIMED.items() for k in knobs}
MOD_SETTINGS.update({name: (0.0, TIME_MOST, TIME_TOP) for name in TIMED_KNOBS},
                    glide=(0.0, GLIDE, TIME_TOP), curve=(-1.0, 1.0, None))
# ... the speed knobs: how far through its waves each repeat is gets added up repeat by repeat (KeyGrid "_turns"), so
# a speed moving makes the wave go faster or slower from where it is, never jump. Vibrato Rate (from each stretch's
# start, as before), each Mode's Pulse Rate (from the note's start) and FM Ratio (wave by wave over the stretch)
SPEED_KNOBS = ("vibrato_rate",) + tuple(f"{osc}{k}" for osc in ("", "osc2_") for k in ("pulse_rate", "fm_ratio"))
MOD_SETTINGS.update(vibrato_rate=(*LFO["vibrato_rate"], RATE_TOP),
                    **{f"{osc}pulse_rate": (*MODES["pulse"]["rate"][:2], RATE_TOP) for osc in ("", "osc2_")},
                    **{f"{osc}fm_ratio": (*MODES["fm"]["ratio"][:2], MODES["fm"]["ratio"][1]) for osc in ("", "osc2_")})
# ... and the MOD tab's own LFO 3 / LFO 4 Rate (moved_lfo; never by the LFO itself: clean_mod)
LFO_RATES = {f"{src}_rate": src for src in MOD_LFOS}
MOD_SETTINGS.update({name: (*MOD_LFO["rate"][:2], RATE_TOP) for name in LFO_RATES})
MOD_TARGETS = MOD_LINES + tuple(MOD_SETTINGS)
# (knobs the engine reads once for a whole note or the whole sound, not for each repeat: KeyGrid.moved leaves them
# out)
NOT_EACH = set(TIMED_KNOBS) | {"glide", "curve", "compressor_attack", "compressor_release", *LFO_RATES}


def longest_fall(hz):
    """Beats the sound goes on after a note at most (the falls of the lines with a sustain point; a Volume Release
    the MOD tab moves: as long as it can get)."""
    fall = max((hz["loop"][name] - at for name, at in (hz.get("sustain") or {}).items()), default=0.0)
    if any(link["to"] == "release" for link in (hz.get("mod") or {}).get("links", ())) and knob_adsr(hz):
        fall = max(fall, setting_most(hz, "release", knob_adsr(hz)["release"]))
    return fall


def timed_line(hz, name, beat, tone):
    """An effect's line played once per note whose time knobs the MOD tab moves (TIMED: the Volume box's ADSR, Pitch
    Time, Sweep Time, Vibrato Delay / Rise), at beat (an array) for tone's note, or None (nothing moves them; drawn
    otherwise; no tone: as drawn). Worked out on a grid from the note's start: each stage as long as its knob says at
    every step (as a synth's envelope, a running stage gets shorter or longer)."""
    if tone is None or name not in TIMED:
        return None
    knobs, read = TIMED[name]
    linked = {link["to"] for link in (hz.get("mod") or {}).get("links", ()) if link["to"] in knobs}
    e = read(hz) if linked else None
    if e is None:
        return None
    g, v = cached(hz, ("timed", name, tone.get("id")), lambda: timed_grid(hz, name, e, linked, tone))
    return np.interp(np.asarray(beat, float), g, v)


def timed_grid(hz, name, e, linked, tone):
    """timed_line's (grid beats, the line's value there), from the note's start past its longest fall."""
    s0, end = note_span(hz, 0.0, tone)
    s0, end = float(s0), float(end)
    span = end - s0 + cached(hz, "fall", lambda: longest_fall(hz)) + 2 * TIME_STEP
    dt = max(TIME_STEP, span / 100000)
    g = s0 + np.arange(int(span / dt) + 2) * dt
    sources = {}

    def stage(key, t0, stays=False):
        """A stage of knob `key` starting at t0: (how far through it, 0..1 at each step; when it ends). A length of 0
        = over at once, or (stays: FM's Time) never moving."""
        if e[key] <= 0 and key not in linked or t0 == math.inf:
            return ((g < -math.inf) if stays else (g >= t0)).astype(float), (math.inf if stays else t0)
        length = setting_at(hz, key, e[key], g, tone, sources) if key in linked else np.full(len(g), e[key])
        if stays:
            length = np.where(length > 0, length, math.inf)
        sums = stage_sums(length, dt)
        return np.clip(sums - np.interp(min(t0, g[-1]), g, sums), 0.0, 1.0), stage_end(sums, g, t0)

    if name.endswith("_in"):  # (KeyGrid's: how far through, 0..1)
        if name == "tremolo_in":  # (none for Delay, then coming in over Rise)
            _, tw = stage("tremolo_wait", s0)
            return g, stage("tremolo_rise", tw)[0]
        return g, stage(TIMED[name][0][0], s0, stays=name.endswith("fm_in"))[0]
    if name in ("pitch", "sweep"):  # (from start to end, fast first)
        p, _ = stage(next(iter(TIMED[name][0])), s0)
        return g, e["start"] + (e["end"] - e["start"]) * (1.0 - (1.0 - p) ** 2)
    if name == "vibrato":  # (none, then coming in)
        _, tw = stage("vibrato_wait", s0)
        p, _ = stage("vibrato_delay", tw)
        return g, e["depth"] * p
    s = e["sustain"]  # (the Volume: ADSR)
    top = 1.0 if e["decay"] > 0 or "decay" in linked else s  # (no decay: the rise goes straight to the sustain)
    pa, ta = stage("attack", s0)
    pd, td = stage("decay", ta)
    held = np.where(g < ta, top * pa ** 2, np.where(g < td, 1.0 + (s - 1.0) * (1.0 - (1.0 - pd) ** 2), s))
    left = float(np.interp(end, g, held))  # (where it got when the note ends: the fall starts from there)
    pr, _ = stage("release", end)
    fell = np.clip(s * (1.0 - pr) ** 2 + (left - s) * (1.0 - pr), 0.0, 1.0)
    return g, np.where(g < end, held, fell)


def setting_most(hz, target, base):
    """The highest a MOD_SETTINGS knob set at base can be taken by its links (every source at its fullest)."""
    reach = sum(abs(link["amount"]) for link in (hz.get("mod") or {}).get("links", ()) if link["to"] == target)
    return float(turned_setting(target, base, reach))


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


def mod_value(hz, src, beat, tone=None):
    """A MOD tab source's value at beat (an array, from the shape's left edge), 0..1 (see MOD_SOURCES): those counted
    from each note go by tone's note (chain of slides), or (tone None) the latest one to start (note_span)."""
    beat = np.asarray(beat, float)
    mod = hz["mod"]
    if src in ("velocity", "note"):
        def of(n):
            return (n.get("level", 1.0) if src == "velocity"
                    else min(1.0, max(0.0, n.get("played", pitch(n)) / 127.0)))
        if tone is not None:
            return np.full(beat.shape, float(of(tone)))
        tones = sorted(hz.get("tones") or (), key=lambda n: (n["t"], n["len"]))
        if not tones:
            return np.full(beat.shape, 1.0 if src == "velocity" else hz.get("key", HZ_DEFAULTS["key"]) / 127.0)
        # (no tone: the newest note's, as a synth's effects after its voices; together: the longest)
        i = np.searchsorted(np.array([n["t"] for n in tones]), beat, side="right") - 1
        return np.array([float(of(n)) for n in tones])[np.clip(i, 0, len(tones) - 1)]
    span = note_span(hz, beat, tone)
    s0, end = span if span is not None else (0.0, 0.0)
    if src in MOD_ENVS:
        return env_value(mod["env"][MOD_ENVS.index(src)], beat - s0, np.asarray(end) - s0)
    lfo = mod["lfo"][MOD_LFOS.index(src)]
    if any(link["to"] == f"{src}_rate" for link in mod.get("links", ())):
        return moved_lfo(hz, src, beat, tone, s0)
    if lfo["rate"] <= 0:  # (stopped: where a wave starts)
        return np.full(beat.shape, float(line_at(loop_shape(lfo["shape"], 1.0, seed=MOD_LFOS.index(src)), 0.0)))
    every = 1.0 / lfo["rate"]
    pts = cached(hz, ("mod", src), lambda: loop_shape(lfo["shape"], every, seed=MOD_LFOS.index(src)))
    if lfo["mode"] == "free":  # (counted late by "phase": the shape's left edge moved, the song's beats kept)
        return line_at(pts, beat - mod.get("phase", 0.0), every)
    u = np.maximum(0.0, beat - s0)
    return line_at(pts, u, every) if lfo["mode"] == "restart" else line_at(pts, np.minimum(u, every))


def moved_lfo(hz, src, beat, tone, s0):
    """mod_value of an LFO whose Rate the MOD tab moves: its waves added up step by step from its note's start (s0;
    no tone: from the shape's start), so a Rate moving makes it go faster or slower from where it is, never jump.
    Free: as if it ran at the Rate it starts with until then. The Rate's sources are read with no LFO Rate moved (two
    LFOs can't move each other round and round)."""
    mod = hz["mod"]
    i = MOD_LFOS.index(src)
    lfo = mod["lfo"][i]
    s0 = np.broadcast_to(np.asarray(s0, float), beat.shape)
    start = float(s0.min()) if tone is not None and beat.size else 0.0
    end = max(start, float(beat.max())) if beat.size else start
    dt = max(TIME_STEP, (end - start) / 100000)
    g = start + np.arange(int((end - start) / dt) + 2) * dt
    plain = dict(hz, mod=dict(mod, links=[link for link in mod["links"] if link["to"] not in LFO_RATES]))
    sources = {link["from"]: mod_value(plain, link["from"], g, tone) for link in mod["links"]
               if link["to"] == f"{src}_rate"}
    rate = setting_at(hz, f"{src}_rate", lfo["rate"], g, tone, sources)
    turns = np.concatenate([[0.0], np.cumsum(rate[:-1] * dt)])
    at = np.interp(beat, g, turns)
    if lfo["mode"] == "free":  # (counted late by "phase", as mod_value's)
        at = at + rate[0] * (start - mod.get("phase", 0.0))
    else:
        at = np.maximum(0.0, at - np.interp(s0, g, turns))
    pts = cached(hz, ("mod1", src), lambda: loop_shape(lfo["shape"], 1.0, seed=i))
    return line_at(pts, np.minimum(at, 1.0)) if lfo["mode"] == "once" else line_at(pts, at, 1.0)


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
    """True when every key needs its own repeats (KeyGrid): placed tones with effects, several copies (Voice), a
    Random start, a wave mode, OSC B or Arpeggio steps quieter than full (as played, or still to be played)."""
    arp = hz.get("arp") or {}
    quiet = (any(n.get("level", 1.0) != 1.0 for n in hz.get("tones") or ())
             or any(s["level"] != 1.0 for s in (arp.get("steps") or ())[:arp.get("count", STEPS)]))
    return ((bool(hz.get("fx")) or len(copies(hz)) > 1 or bool((hz.get("voice") or {}).get("random"))
             or bool(hz.get("mode")) or bool(hz.get("osc2")) or quiet or bool((hz.get("mod") or {}).get("links"))
             or any(not e.get("off") for e in hz.get("rack") or ())) and bool(hz.get("tones")))


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
    out.update(clean_extra(hz))
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


def legato_links(voice, tones):
    """[(a, b)] with Legato on (hz["voice"]["legato"], the Voice box): tone b starts right where tone a ends, so b
    carries on a's effects counted from each note (no new attack; a's fall doesn't play), as a slide made by hand
    does, like a synth's legato. Paired as Glide pairs them (one to a chord, several to one, chord to chord none);
    the same pitch too (an arpeggio of one note held: one envelope)."""
    if not (voice or {}).get("legato") or len(tones) < 2:
        return []
    ended = sorted(tones, key=lambda n: n["t"] + n["len"])
    ends = [n["t"] + n["len"] for n in ended]
    out, i = [], 0
    order = sorted(tones, key=lambda n: n["t"])
    while i < len(order):
        t = order[i]["t"]
        j = i
        while j < len(order) and order[j]["t"] <= t + 1e-9:
            j += 1
        chord, i = order[i:j], j
        before = ended[bisect.bisect_left(ends, t - 1e-9):bisect.bisect_right(ends, t + 1e-9)]
        if before and not (len(before) > 1 and len(chord) > 1):
            out += [(a, b) for b in chord for a in before if a is not b]
    return out


def glide_left(u, curve):
    """How much of a glide's way is left at u (0..1 of its time): (1 - u) ^ 4^curve, so curve 0 = straight, 1 = fast
    first, -1 = slow first (GLIDE_CURVE = (1 - u)^2, as glides always were)."""
    return (1.0 - u) ** (4.0 ** curve)


def glides(hz):
    """{tone id: [pitch it glides in from, ...]} with Glide on (hz["voice"]): a note glides in from the note(s)
    that ended last before it starts ("touching": only when they end right where it starts), like the slides made
    by hand: one to a chord, several to one, chord to chord none. Not into a note a slide reaches, nor from the same
    pitch."""
    voice = hz.get("voice") or {}
    tones = hz.get("tones") or ()
    if not (voice.get("glide") or glide_moved(hz)) or len(tones) < 2:
        return {}
    slid = {b["id"] for _, b, _ in links(tones)}
    ended = sorted(tones, key=lambda n: n["t"] + n["len"])
    ends = [n["t"] + n["len"] for n in ended]
    out, i = {}, 0
    order = sorted(tones, key=lambda n: n["t"])
    while i < len(order):
        t = order[i]["t"]
        j = i
        while j < len(order) and order[j]["t"] <= t + 1e-9:
            j += 1
        chord, i = order[i:j], j
        k = bisect.bisect_right(ends, t + 1e-9)
        if not k or (voice.get("touching") and ends[k - 1] < t - 1e-9):
            continue
        last, before = ends[k - 1], []
        while k and ends[k - 1] >= last - 1e-9:
            before.append(ended[k - 1])
            k -= 1
        if len(before) > 1 and len(chord) > 1:
            continue
        for b in chord:
            got = [pitch(a) for a in before if abs(pitch(a) - pitch(b)) > 1e-9]
            if got and b["id"] not in slid:
                out[b["id"]] = got
    return out


def glide_moved(hz):
    """The MOD tab moves the Voice box's Glide (and its box is on): notes may glide even with Glide at 0."""
    return (any(link["to"] == "glide" for link in (hz.get("mod") or {}).get("links", ()))
            and plain_base(hz, "glide") is not None)


def glide_of(hz, n, key):
    """Note n's Glide time / Curve (key "glide" / "curve"): as set, or moved by the MOD tab as at its start (a glide
    is over quickly: read once, as it starts)."""
    base = plain_base(hz, key)
    if base is None:
        return 0.0 if key == "glide" else GLIDE_CURVE
    return float(setting_at(hz, key, base, np.array([n["t"]]), n)[0])


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
    it leaves. A tone a chain ends with goes on for its fall (tails). Glide (glides): a tone's start is one more
    stretch from each tone it glides in from, fast first, then the tone held (whose: (tone, None) for both)."""
    tones = hz["tones"]
    ls = links(tones)
    tail = tails(hz)
    gl = glides(hz)
    out, held = [], {}
    for n in tones:
        a = n["t"] + min([min(s["in"], n["len"]) for _, b, s in ls if b is n], default=0.0)
        took = glide_of(hz, n, "glide") if n["id"] in gl else 0.0
        if took > 0:
            s, e = (left + n["t"]) * ppq, (left + n["t"] + min(took, n["len"])) * ppq
            a = max(a, n["t"] + min(took, n["len"]))
            curve = glide_of(hz, n, "curve")
            for k0 in gl[n["id"]]:
                part, after, t = [], [], s
                while t < e:
                    part.append(t)
                    t += wave(hz, ppq, pitch(n) + (k0 - pitch(n)) * glide_left((t - s) / (e - s), curve))
                    after.append(t)
                out.append((np.array(part), np.array(after), (n, None)))
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
    at, ends = cached(hz, ("limits", left, ppq), lambda: _spans(hz, left, ppq))
    return ends[np.maximum(np.searchsorted(at, starts, "right") - 1, 0)]


def _spans(hz, left, ppq):
    """The stretches the tones sound in together (touching or overlapping, falls included): (start ticks, a hair
    early; end ticks)."""
    spans, tail = [], tails(hz)
    for n in sorted(hz["tones"], key=lambda n: n["t"]):
        end = n["t"] + n["len"] + tail.get(n["id"], 0.0)
        if spans and n["t"] <= spans[-1][1] + 1e-9:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([n["t"], end])
    at = np.array([(left + a) * ppq for a, _ in spans]) - 1e-6
    return at, np.array([math.floor((left + b) * ppq + 0.5) for _, b in spans], np.int64)


def _whole(v):
    return np.floor(v + 0.5).astype(np.int64)


@functools.lru_cache(maxsize=16)
def _heard(hz_json, left, ppq, bpm):
    hz = dict(json.loads(hz_json), _memo={})
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
    PPQ, the more. Alternating ("mixed") gates take turns so that the average is the wanted tone; fixed gates are all the same, so
    there the average is each repeat's own pitch.
    The pitch is counted from the shape's own tuning (hz["cents"]), so a key's exact tone is that key."""
    return _heard(json.dumps(live(hz, left), sort_keys=True), left, ppq, float(bpm))


@functools.lru_cache(maxsize=16)
def _squares(hz_json, left, ppq):
    hz = dict(json.loads(hz_json), _memo={})
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


def silent_beside(starts, limits, quiet, osc):
    """A key row playing both oscillators: which repeats are too quiet while the other oscillator sounds there (its
    latest repeat by then is heard and not over), so they're left out before the notes are laid end to end and don't
    cut the other's notes short (OSC B at Level 0 = as if off). Arrays, one item per repeat."""
    drop = np.zeros(len(starts), bool)
    for o in (0, 1):
        mine, other = np.flatnonzero(quiet & (osc == o)), np.flatnonzero(osc != o)
        if not len(mine) or not len(other):
            continue
        other = other[np.argsort(starts[other], kind="stable")]
        at = np.searchsorted(starts[other], starts[mine], "right") - 1
        got = other[np.maximum(at, 0)]
        drop[mine] = (at >= 0) & ~quiet[got] & (starts[mine] < limits[got])
    return drop


def fall_off(since, time):
    """1 at a note's start, falling to 0 over `time` beats, fast first (time 0: stays 1). since = beats (array)."""
    since = np.asarray(since, float)
    if time <= 0:
        return np.ones(since.shape)
    return (1.0 - np.clip(since / time, 0.0, 1.0)) ** 2


def wave_hits(shapes, plain, number, since, mode):
    """The hits in each wave (repeat) of a stretch with a waveform or a wave mode (FM, Pulse width, Sync): (where,
    0..1 of the wave; loudness 0..1), arrays (repeats x hits). shapes = [(waveform function, its value for each repeat,
    on for each repeat)]; plain = the repeats with no waveform on (one hit a wave; FM makes them a sine, Pulse width
    full hits); number = each repeat's number in its stretch; since = beats from its note's start; mode = hz["mode"]
    ({} = none; its knobs may be arrays, one value each repeat; "progress" = how far through its Time each is;
    "turns" = its speed moved by the MOD tab: Pulse's waves so far, FM's wobbles so far less Ratio x the wave's
    number)."""
    part = np.arange(SUB) / SUB
    rows, kind = len(plain), mode.get("kind")
    p = np.broadcast_to(part, (rows, SUB))
    moved = mode.get("progress")  # (its Time moved by the MOD tab: how far through it each repeat is)
    if kind == "fm":  # (the place in the wave pushed back and forth; counted over the stretch so it runs on)
        depth = FM_INDEX * mode["depth"] * (fall_off(since, mode["time"]) if moved is None else (1.0 - moved) ** 2)
        at = np.asarray(number, float)[:, None] + part[None, :]
        wobble = (mode["ratio"] * at if "turns" not in mode
                  else mode["turns"][:, None] + mode["ratio"][:, None] * at)
        p = np.mod(at + depth[:, None] / (2.0 * np.pi) * np.sin(2.0 * np.pi * wobble), 1.0)
    mix = np.ones((rows, SUB))
    for fn, v, on in shapes:  # (several on one note: multiplied)
        mix *= np.where(on[:, None], (1.0 - v[:, None]) * (part == 0) + v[:, None] * fn(p), 1.0)
    if kind == "fm":
        mix[plain] = WAVES["sine"](p[plain])
    elif kind == "pulse":
        mix[plain] = 1.0
    else:
        mix[plain] = part == 0
    if kind == "pulse":
        turns = mode["turns"] if "turns" in mode else mode["rate"] * since
        width = mode["width"] + (0.5 - mode["width"]) * (1.0 - np.cos(2.0 * np.pi * turns)) / 2.0
        mix = mix * (part[None, :] < width[:, None])
    where = np.broadcast_to(part, (rows, SUB))
    if kind == "sync" and rows:  # (the wave's hits squeezed into 1 / r of it, over and over until the wave ends)
        r = (1.0 + (mode["amount"] - 1.0) * moved if moved is not None else mode["amount"] if mode["time"] <= 0
             else 1.0 + (mode["amount"] - 1.0) * np.clip(since / mode["time"], 0, 1))
        r = np.broadcast_to(np.asarray(r, float), (rows,))
        cols = np.flatnonzero((mix >= SOFT).any(axis=0))
        k = int(math.ceil(r.max() - 1e-9))
        where = (np.arange(k)[None, :, None] + part[cols][None, None, :]) / r[:, None, None]
        mix = mix[:, None, cols] * (where < 1.0 - 1e-9)
        where, mix = where.reshape(rows, -1), mix.reshape(rows, -1)
    return where, mix


class KeyGrid:
    """The repeats of a Hz bass with effects: every key has its own (squares(key)), and how much of
    the shape's velocity each of them gets (factor)."""

    def __init__(self, hz, left, ppq, lo, n):
        hz = dict(hz, _memo={})  # (its own: what's worked out once for all the notes, cached)
        self.lo, self.n, self.got = lo, max(1, n), {}
        self.copies, self.same = copies(hz), bool((hz.get("voice") or {}).get("same"))
        self.gains = blend_gains(hz)  # (each copy's loudness: Blend)
        self.random = (hz.get("voice") or {}).get("random", 0.0)  # (Random start: see starting_points)
        self.left = left
        self.mode, self.ppq = hz.get("mode") or {}, ppq
        self.chorus, self.echo, self.reverb, self.flanger, self.comp = (
            rack_on(hz, k) for k in ("chorus", "echo", "reverb", "flanger", "compressor"))
        self.order = [e["kind"] for e in hz.get("rack") or () if not e.get("off")]  # (the compressor's place counts)
        lfo = hz.get("lfo") or {}
        self.trem_in = (lfo.get("tremolo_wait", 0.0), lfo.get("tremolo_rise", 0.0))  # (beats: see LFO)
        self.track = lfo.get("sweep_track", 0.0)  # (Key track: the Sweep follows each note's pitch)
        self.osc2 = hz.get("osc2") or None  # (OSC B: see the docstring)
        self.modes = [self.mode, (self.osc2 or {}).get("mode") or {}]  # (each oscillator's own Mode)
        self.a_off = bool((self.osc2 or {}).get("a_off"))  # (OSC A switched off: only OSC B sounds)
        # (the knobs that aren't lines the MOD tab moves, each run's values worked out in made_runs; setting_at)
        self.hz = hz
        self.moved = {link["to"] for link in (hz.get("mod") or {}).get("links", ())
                      if link["to"] in MOD_SETTINGS and link["to"] not in NOT_EACH
                      and setting_base(hz, link["to"]) is not None}
        self.turned = sorted(name + "_turns" for name in self.moved if name in SPEED_KNOBS)  # (speeds: added up)
        # (the Tremolo's Delay / Rise and the Modes' Time moved: how far through them each repeat is, timed_line)
        self.linked = linked = {link["to"] for link in (hz.get("mod") or {}).get("links", ())}
        self.timed = sorted(name for name, (knobs, read) in TIMED.items()
                            if name.endswith("_in") and linked & set(knobs) and read(hz) is not None)
        n = len(self.copies)
        self.middle = np.isin(np.arange(n), ((n - 1) // 2, n // 2))  # (Blend: the middle copies)
        self.runs = [] if self.a_off else self.made_runs(hz, left, ppq, 0)
        if self.osc2:  # (its notes moved by its tune: everything counted from each note stays as the notes have it)
            shift = 100.0 * osc2_shift(self.osc2)
            # (played: the note's own pitch, for the MOD tab's Note)
            moved = dict(hz, _memo={}, tones=[dict(n, cents=n.get("cents", 0.0) + shift, played=pitch(n))
                                              for n in hz["tones"]])
            if abs(self.osc2["fine"]) > 1e-9:  # (Fine: alternating gates, else whole-tick ones would round it away)
                moved = {k: v for k, v in moved.items() if k not in ("fixed", "auto")}
                moved["tones"] = [{k: v for k, v in n.items() if k not in ("gate", "auto")} for n in moved["tones"]]
            self.runs += self.made_runs(moved, left, ppq, 1)
        if self.reverb:  # (each oscillator's tail of its own)
            self.runs += [r for o in (0, 1) for r in self.reverb_runs(hz, left, ppq, [r for r in self.runs
                                                                                        if r["osc"] == o])]
        self.shaped = (any(r["has_" + name].any() for r in self.runs for name in WAVES)
                       or any(m.get("kind") in ("fm", "pulse", "sync") for m in self.modes))
        self.loud = (self.shaped or any(name in (hz.get("fx") or ()) for name in VEL_FX)
                     or bool(self.echo or self.reverb or self.comp) or bool((self.gains != 1.0).any())
                     or "blend" in self.moved or any(np.any(r["level"] != 1.0) for r in self.runs))
        self.pack()
        self.rack_turns = {name: self.added_up(name) for name in ("chorus_rate", "flanger_rate") if name in self.moved}
        self.starting = self.starting_points() if self.random else None
        self.squeeze = self.comp_curve() if self.comp else None  # (the compressor's turn-down over time)

    def made_runs(self, hz, left, ppq, osc):
        """One oscillator's runs (OSC A = 0: the notes as placed; OSC B = 1: hz with its tones moved by its tune):
        each stretch of tone's repeats with every value they need."""
        runs = []
        tail = tails(hz)
        home = hz.get("key", HZ_DEFAULTS["key"]) + hz.get("cents", 0.0) / 100.0  # (Key track: from the Hz bass's tone)
        shift = osc2_shift(self.osc2) if osc else 0.0
        b = self.osc2 if osc else None
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
            sources = {}  # (the MOD tab's sources: worked out once for the run)
            for name in FX:  # (each tone counts from its own start, like a synth's voice)
                run[name] = fx_at(hz, name, beat, n0, sources)
            mono = {}  # (the Effects tab's: from the newest note, for the whole sound)
            for name in self.moved:  # (the MOD tab's knobs that aren't lines: from each note too)
                run[name] = (setting_at(hz, name, setting_base(hz, name), beat, None, mono) if name in MOD_RACK
                             else setting_at(hz, name, setting_base(hz, name), beat, n0, sources))
            for name in self.timed:
                run[name] = timed_line(hz, name, beat, n0)
            run["swept"] = np.full(len(beat), "sweep" in fx)  # (sweep at 0 = the bump on the lowest key)
            run["has_volume"] = np.full(len(beat), "volume" in fx)
            for name in WAVES:  # (a waveform at 0 = the plain tone; without any: the plain tone too)
                run["has_" + name] = np.full(len(beat), name in fx if b is None else name == b["wave"])
                if b is not None:  # (OSC B: its own waveform, not the lines' (OSC A's))
                    shape = run["osc2_shape"] if "osc2_shape" in self.moved else np.full(len(beat), b["shape"])
                    run[name] = shape if name == b["wave"] else np.zeros(len(beat))
            if b is not None:
                run["octave"] = np.zeros(len(beat))  # (Octave below is OSC A's)
            run["groups"] = group_count(run["groups"])
            run["turns"] = np.concatenate([[0.0], np.cumsum(run["tremolo"] * TREMOLO * run["waves"] / ppq)[:-1]])
            lfo = hz.get("lfo") or {}
            run["vib_rate"] = lfo.get("vibrato_rate", VIBRATO_RATE)
            depth = lfo.get("tremolo_depth")  # (as it was without one: the very same numbers)
            run["trem"] = (0.1, TREMOLO_DEPTH) if depth is None else (1.0 - depth, depth)
            span = note_span(hz, beat, n0)  # (beats from its note's start, a chain's: the wave modes)
            run["since"] = beat - (n0["t"] if span is None else span[0])
            run["trem_since"] = run["since"]  # (the tremolo's Delay / Rise: a reverb tail keeps it as at the end)
            for k in self.turned:  # (a speed the MOD tab moves: its waves added up repeat by repeat, SPEED_KNOBS)
                r = run[k[:-6]]
                if k.endswith("fm_ratio_turns"):  # (wobbles per wave, less Ratio x the wave's number: wave_hits)
                    run[k] = np.concatenate([[0.0], np.cumsum(r[:-1])]) - r * run["number"]
                else:  # (times a beat: the vibrato from the stretch's start, Pulse from the note's)
                    first = r[0] * run["since"][0] if len(r) and k != "vibrato_rate_turns" else 0.0
                    run[k] = first + np.concatenate([[0.0], np.cumsum(r[:-1] * np.diff(beat))])
            run["tone"], run["held"] = n0, whose[1] is None
            run["track"] = pitch(n0) - shift - home  # (keys the note is above the Hz bass's own tone)
            # (OSC B's Level x the Arpeggio step's loudness)
            level = run["osc2_level"] if b is not None and "osc2_level" in self.moved else (
                b["level"] if b is not None else 1.0)
            run["osc"], run["level"] = osc, level * n0.get("level", 1.0)
            runs.append(run)
        return runs

    def starting_points(self):
        """Random start: where each Voice copy's waves start in each note (runs x VOICES, 0..1 of a wave), as a
        synth's random phase: new at every note, kept through its slides (drawn from the beat in the song its chain
        of slides starts at, so the same every time the notes are made, and after a Split or a moved left edge)."""
        f, at = self.flat, np.minimum(self.each["offsets"], len(self.flat["beat"]) - 1)  # (a run with none: any)
        heads = self.left + f["beat"][at] - f["since"][at] if len(f["beat"]) else np.zeros(len(self.runs))
        drawn = {}
        out = np.zeros((len(self.runs), VOICES))
        for i, h in enumerate(heads):
            seed = int(round(float(h) * 65536)) % (2 ** 32)
            if seed not in drawn:  # (OSC B's copies: the next ones drawn, OSC A's as they always were)
                drawn[seed] = np.random.default_rng([seed, 7]).random(2 * VOICES)
            o = self.runs[i]["osc"]
            out[i] = drawn[seed][o * VOICES:(o + 1) * VOICES]
        return out

    def pack(self):
        """Every run's numbers end to end (self.flat: one array each, each repeat's value), and each run's own in
        self.each, so made() works out all of a key's repeats at once instead of run by run (an arpeggio makes
        hundreds of runs; one by one, 128 keys took seconds)."""
        runs = self.runs
        n = np.array([len(r["starts"]) for r in runs], np.int64)
        names = ["starts", "waves", "beat", "limits", "since", "trem_since", "turns", "swept", "has_volume", "groups",
                 "track", "level", *FX, *sorted(self.moved), *self.timed, *self.turned,
                 *("has_" + name for name in WAVES)]
        self.flat = {k: np.concatenate([np.broadcast_to(np.asarray(r[k]), (len(r["starts"]),)) for r in runs])
                     if runs else np.zeros(0) for k in names}
        self.flat["tail_u"] = np.concatenate([r["tail_u"] if "tail_u" in r else np.zeros(len(r["starts"]))
                                              for r in runs]) if runs else np.zeros(0)
        offsets = np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64)
        last = np.maximum(offsets + n - 1, 0)
        f = self.flat
        self.each = {"n": n, "offsets": offsets,
                     "beat0": f["beat"][offsets] if len(f["beat"]) else np.zeros(len(runs)),
                     "end": (f["starts"][last] + f["waves"][last]) if len(f["starts"]) else np.zeros(len(runs)),
                     "moves": np.array([bool(r["offpitch"].any() or r["vibrato"].any()) for r in runs], bool),
                     "until": np.array([r["until"] for r in runs], float),
                     "tail": np.array(["tail_u" in r for r in runs], bool),
                     "vib_rate": np.array([r["vib_rate"] for r in runs], float),
                     "osc": np.array([r["osc"] for r in runs], np.int64)}

    def reverb_runs(self, hz, left, ppq, runs):
        """The Effects tab's reverb: after each note a chain of slides ends with, its tone goes on for the reverb's
        length (cut where a note of the same pitch starts, as the falls are), with everything as it was when the note
        ended; made quieter and scattered in made() ("tail_u" = 0..1 of the way through it). runs = one
        oscillator's."""
        r, tones = self.reverb, hz["tones"]
        soft = SOFT / self.lift("reverb")  # (a compressor after it may lift the tail's end: it goes on further)
        level = self.most("reverb_level", r["level"])  # (the MOD tab may turn it up: as far as it can go)
        if level ** 2 <= soft:
            return []
        quiet = 1.0 - (soft / level ** 2) ** (1 / 3)  # (0..1 of the way: from here on too soft, left out)
        rack = [name for name in self.moved if name in MOD_RACK]  # (the Effects tab's: on over the tail, in time)
        leaving = {a["id"] for a, _, _ in links(tones)} | {a["id"] for a, _ in legato_links(hz.get("voice"), tones)}
        last, starts = {}, {}
        for run in runs:  # (each tone's held stretch that goes on longest)
            n = run["tone"]
            if run["held"] and len(run["starts"]) and (n["id"] not in last
                                                      or run["starts"][-1] > last[n["id"]]["starts"][-1]):
                last[n["id"]] = run
        for m in tones:
            starts.setdefault(pitch(m), []).append(m["t"])
        for s in starts.values():
            s.sort()
        out = []
        for n in tones:
            src = last.get(n["id"])
            if n["id"] in leaving or src is None:
                continue
            end = n["t"] + n["len"]
            same = starts[pitch(n)]
            k = bisect.bisect_left(same, end - 1e-9)
            nxt = same[k] if k < len(same) else math.inf
            length = min(r["length"], nxt - end)
            if length <= 1e-9:
                continue
            i = max(0, int(np.searchsorted(src["beat"], end, "right")) - 1)
            gate, at = float(src["waves"][i]), (left + end) * ppq
            count = int(math.ceil(quiet * length * ppq / gate))
            run = {k: (np.full(count, v[i]) if isinstance(v, np.ndarray) else v) for k, v in src.items()}
            run["starts"] = at + gate * np.arange(count)
            run["waves"], run["number"] = np.full(count, gate), np.arange(count)
            run["beat"] = run["starts"] / ppq - left
            run["since"] = src["since"][i] + (run["starts"] - src["starts"][i]) / ppq
            run["limits"] = np.full(count, math.floor(at + length * ppq + 0.5), np.int64)
            run["until"], run["held"] = np.inf, False
            run["tail_u"] = np.arange(count) * gate / (length * ppq)
            for k in self.turned:  # (the speeds as at the end, their waves going on from there)
                run[k] = (np.zeros(count) if k.endswith("fm_ratio_turns")
                          else src[k][i] + src[k[:-6]][i] * (run["beat"] - src["beat"][i]))
            mono = {}
            for name in rack:
                run[name] = setting_at(self.hz, name, setting_base(self.hz, name), run["beat"], None, mono)
            out.append(run)
        return out

    def added_up(self, name):
        """An Effects tab Rate the MOD tab moves (the whole sound's: sources from the newest note): (grid beats from
        the shape's start, its waves so far there), added up step by step so a Rate moving never jumps."""
        beats = self.flat["beat"]
        end = float(beats.max()) if len(beats) else 0.0
        dt = max(TIME_STEP, end / 100000)
        g = np.arange(int(end / dt) + 2) * dt
        rate = setting_at(self.hz, name, setting_base(self.hz, name), g, None, {})
        return g, np.concatenate([[0.0], np.cumsum(rate[:-1] * dt)])

    def rack_wave(self, name, plain, beat):
        """How many waves of an Effects tab Rate (plain: as set) have gone by at beat (from the shape's start)."""
        if name in self.rack_turns:
            return np.interp(beat, *self.rack_turns[name])
        return plain * beat

    def most(self, name, plain):
        """The highest a knob that makes no line can be (plain: as set): as far as the MOD tab's links can take it."""
        return setting_most(self.hz, name, plain) if name in self.moved else plain

    def made(self, key):
        """A key's repeats, (start, end) ticks in order, none overlapping, and each one's part of the velocity.
        Every run x Voice copy is a "part" (in that order); all parts are worked out together, in one set of arrays
        (pack)."""
        got = self.got.get(key)
        if got is not None:
            return got
        x = min(1.0, max(0.0, (key - self.lo) / self.n))  # where the key is among the shape's keys, 0 = the lowest
        xv = min(1.0, max(0.0, (key - self.lo) / max(1, self.n - 1)))  # (for loudness: 1 = the highest key)
        noise = np.random.default_rng(1000 + key)  # (the same every time: the key is the seed)
        row = key - self.lo
        oscs = [0, 1] if self.osc2 else [0]  # (the oscillators this key row plays: OSC B's Split keys = turns)
        if self.a_off:
            oscs = [1]
        elif self.osc2 and self.osc2.get("split") and self.n > 1:  # (one key: plays both)
            oscs, row = [row % 2], row // 2  # (the Voice copies' turns: by pairs of rows then)
        which = list(range(len(self.copies))) if self.same else [row % len(self.copies)]  # (Voice copies)
        cents = [self.copies[i] for i in which]
        if not self.same:  # (the chorus and flanger take turns among the keys of the same copy (and oscillator),
            row //= len(self.copies)  # so every copy has some moved)
        chorus = self.chorus if row % 2 == 1 else None  # (every other key)
        f, each = self.flat, self.each
        runs = np.flatnonzero(np.isin(each["osc"], oscs)) if self.osc2 else np.arange(len(self.runs))
        run = np.repeat(runs, len(cents))  # each part's run
        copy = np.tile(np.array(which, np.int64), len(runs))  # ... and copy
        scale = np.tile(np.array([2.0 ** (-c / 1200.0) for c in cents]), len(runs))
        n = each["n"][run]
        # A stretch of tone: off pitch and vibrato make its waves longer or shorter one after the other (every value
        # of a repeat taken for the one with the same number; past the end, the last one's), so it may take more or
        # fewer repeats to fill the stretch; scale = every wave that many times as long (a Voice copy's own tone, or
        # the chorus: none past the stretch's end)
        first = np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64)  # (each part's first repeat as it was)
        part = np.repeat(np.arange(len(run)), n)
        src = each["offsets"][run][part] + np.arange(len(part)) - first[part]
        wide = np.repeat(scale, n)
        if chorus:  # (up to depth cents and back, counted from the shape's start: it runs on over the notes)
            depth = f["chorus_depth"][src] if "chorus_depth" in self.moved else chorus["depth"]
            wide = wide * 2.0 ** (-depth * (1.0 - np.cos(2.0 * np.pi * self.rack_wave("chorus_rate", chorus["rate"],
                                                                                        f["beat"][src])))
                                  / 2.0 / 1200.0)
        scaled = np.bincount(part, wide != 1.0, len(run)) > 0
        moves = (each["moves"][run] | scaled) & (n > 0)
        vib = (f["vibrato_rate_turns"][src] if "vibrato_rate" in self.moved  # (its Rate moved: added up)
               else each["vib_rate"][run][part] * (f["beat"][src] - each["beat0"][run][part]))
        stretch = wide * (1.0 + OFF_PITCH * f["offpitch"][src] * (x - 0.5)) * (
            1.0 + VIBRATO * f["vibrato"][src] * np.sin(2.0 * np.pi * vib))
        size = np.where(moves, n + n // 10 + 3, n)
        start = np.concatenate([[0], np.cumsum(size)[:-1]]).astype(np.int64)
        part = np.repeat(np.arange(len(run)), size)
        number = np.arange(len(part)) - start[part]
        at = np.minimum(number, n[part] - 1)
        src = each["offsets"][run][part] + at
        moved = moves[part]
        waves = np.where(moved, f["waves"][src] * stretch[np.minimum(first[part] + at, len(stretch) - 1)],
                         f["waves"][src]) if len(part) else np.zeros(0)
        starts = f["starts"][src].copy()
        for size_k in np.unique(size[moves]):  # (summed wave by wave, as each part on its own would be)
            rows = np.flatnonzero(moves & (size == size_k))
            idx = start[rows][:, None] + np.arange(size_k)
            sums = np.cumsum(waves[idx], axis=1)
            starts[idx[:, 1:]] = starts[idx[:, :1]] + sums[:, :-1]
        keep = ~(moved & scaled[part]) | (starts < each["end"][run][part] - 1e-6)
        part, number, src, waves, starts = part[keep], number[keep], src[keep], waves[keep], starts[keep]
        late = f["slant"][src] * x + np.floor(x * f["groups"][src]) / f["groups"][src]
        if self.starting is not None:  # (Random start: each copy's waves start that far in, the same all through)
            late = late + self.random * self.starting[run, copy][part]
        # the random numbers each part takes, in order: its Noisy ones (if it has any), then the reverb's
        count = np.bincount(part, minlength=len(run))
        noisy = np.bincount(part, f["noisy"][src] != 0, len(run)) > 0
        tail = each["tail"][run]
        took = count * noisy + count * tail
        base = np.concatenate([[0], np.cumsum(took)[:-1]]).astype(np.int64)
        drawn = noise.random(int(took.sum()))
        place = base[part] + np.arange(len(part)) - np.concatenate([[0], np.cumsum(count)[:-1]]).astype(np.int64)[part]
        if noisy.any():
            on = noisy[part]
            late = np.where(on, late + f["noisy"][src] * drawn[np.where(on, place, 0)], late)
        osc = each["osc"][run][part]  # (each repeat's oscillator: its own Mode)
        grid = np.zeros(len(late))  # (Bitcrush's grid in ticks, 0 = none)
        for o in range(len(self.modes)):
            mine = osc == o
            mode = self.mode_now(o, src)
            if mode.get("kind") == "growl" and mine.any():  # (late by turns: 0 .. amount over `every` waves)
                every = int(mode["every"])
                late = np.where(mine, late + GROWL * mode["amount"] * (number % every) / (every - 1), late)
            if mode.get("kind") == "crush" and np.any(mode["amount"] > 0):
                grid = np.where(mine, mode["amount"] ** 2 * CRUSH * self.ppq, grid)
        # (the Effects tab's lateness: added after Bitcrush when it's on, as a synth's FX come after its oscillator)
        crushed = grid > 0
        fx_late = np.zeros(len(late))
        fl = self.flanger
        i = row  # (as the chorus: among the keys of the same copy and oscillator)
        mix = f["flanger_mix"][src] if "flanger_mix" in self.moved else fl["mix"] if fl else 0.0
        on = np.floor((i + 1) * mix + 1e-9) > np.floor(i * mix + 1e-9)  # (Mix: this key moves (each repeat's own))
        if fl and np.any(on):
            # (late by up to Depth and back, counted from the song's start like Random start: a Split keeps it)
            depth = f["flanger_depth"][src] if "flanger_depth" in self.moved else fl["depth"]
            if "flanger_rate" in self.rack_turns:  # (moved: added up from the shape's start, as set before it)
                turns = fl["rate"] * self.left + self.rack_wave("flanger_rate", fl["rate"], f["beat"][src])
            else:
                turns = fl["rate"] * (self.left + f["beat"][src])
            moved = depth * (1.0 - np.cos(2.0 * np.pi * turns)) / 2.0
            if np.ndim(on):
                moved = np.where(on, moved, 0.0)
            fx_late = np.where(crushed, fx_late + moved, fx_late)
            late = np.where(crushed, late, late + moved)
        tailed = tail[part]
        tail_u = f["tail_u"][src]  # (the reverb's: more and more scattered)
        if tailed.any():
            later = place + (count * noisy)[part]
            spread = f["reverb_scatter"][src] if "reverb_scatter" in self.moved else self.reverb["scatter"]
            scatter = spread * 0.5 * tail_u * drawn[np.where(tailed, later, 0)]
            fx_late = np.where(tailed & crushed, fx_late + scatter, fx_late)
            late = np.where(tailed & ~crushed, late + scatter, late)
        starts = starts + late * waves
        fx_late = fx_late * waves  # (ticks)
        hear = f["beat"][src]  # (the beat the compressor's turn-down is read at)
        if self.comp and tailed.any() and not self.after("reverb"):  # (a tail after it: as at its note's end)
            hear = np.where(tailed, each["beat0"][run][part], hear)
        limits = f["limits"][src]
        until = each["until"][run][part]
        fade = f["echo_fade"][src] if "echo_fade" in self.moved else None  # (the echo's, each repeat's own)
        quiet = np.zeros(len(starts), bool)
        if self.loud:
            trem = self.trem(src)
            where = f["sweep"][src]
            if self.track or "sweep_track" in self.moved:  # (Key track: one key row for every key the note is above
                track = f["sweep_track"][src] if "sweep_track" in self.moved else self.track  # the Hz bass's tone)
                where = where + track * f["track"][src] / max(1, self.n - 1)
            swept = 0.08 + 0.92 * np.clip(np.cos(np.pi * (xv - where)), 0.0, 1.0) ** 4
            loud = np.where(f["swept"][src], swept, 1.0)
            loud = loud * (1.0 + np.cos(2.0 * np.pi * WAH * f["wah"][src] * (xv - 0.5))) / 2.0
            if any(self.trem_in) or "tremolo_in" in self.timed:  # (coming in after Delay over Rise, from each
                come = self.trem_come(src)  # note's start)
                d = trem[1] * come
                loud = loud * (1.0 - d * (1.0 - np.cos(2.0 * np.pi * f["turns"][src])) / 2.0)
            else:
                loud = loud * (trem[0] + trem[1] * (1.0 + np.cos(2.0 * np.pi * f["turns"][src])) / 2.0)
            if "blend" in self.moved:  # (Blend moved by the MOD tab: each repeat's own, as blend_gains)
                b, middle = f["blend"][src], self.middle[copy][part]
                gains = np.where(middle, np.minimum(1.0, 2.0 * (1.0 - b)), np.minimum(1.0, 2.0 * b))
            else:
                gains = self.gains[copy][part]
            vol = np.where(f["has_volume"][src], f["volume"][src], 1.0) * (
                gains * f["level"][src])  # (Blend; OSC B's Level)
            loud = loud * vol
            if tailed.any():  # (the reverb: starting at its level, fading)
                level = f["reverb_level"][src] if "reverb_level" in self.moved else self.reverb["level"]
                loud = np.where(tailed, loud * level ** 2 * (1.0 - tail_u) ** 3, loud)
            soft = np.where(number % 2 == 1, 1.0 - f["octave"][src], 1.0)  # (on the velocity itself)
            if self.shaped:  # SUB hits in every wave, each as loud as the waveforms say there (wave_hits)
                plain = ~np.any([f["has_" + name][src] for name in WAVES], axis=0)
                got = [(np.zeros(0, np.int64), np.zeros(0), np.zeros(0))]
                for o, mode in enumerate(self.modes):  # (each oscillator with its own Mode)
                    sel = np.flatnonzero(osc == o) if self.osc2 else np.arange(len(starts))
                    if not len(sel):
                        continue
                    where, mix = wave_hits([(WAVES[name], f[name][src[sel]], f["has_" + name][src[sel]])
                                            for name in WAVES], plain[sel], number[sel], f["since"][src[sel]],
                                           self.mode_now(o, src[sel]))
                    keep = mix >= SOFT
                    got.append((sel[np.nonzero(keep)[0]], (starts[sel, None] + where * waves[sel, None])[keep],
                                mix[keep]))
                    if not self.osc2:
                        break
                rows = np.concatenate([g[0] for g in got]).astype(np.int64)
                starts = np.concatenate([g[1] for g in got])
                limits, until, hear, fx_late = limits[rows], until[rows], hear[rows], fx_late[rows]
                grid, crushed, osc = grid[rows], crushed[rows], osc[rows]
                fade = fade[rows] if fade is not None else None
                env, mix, soft = loud[rows], np.concatenate([g[2] for g in got]), soft[rows]
                quiet = vol[rows] < SOFT
            else:
                env, mix = loud, np.ones(len(loud))
                quiet = vol < SOFT
        else:
            env, mix, soft = np.ones(len(starts)), np.ones(len(starts)), np.ones(len(starts))
        # (each note's velocity part = sqrt(env x mix) x soft x the echo's gain: env = its loudness, which the
        # compressor turns up and down; mix and soft = the waveform's and Octave below's part, which it leaves alone)
        if crushed.any():  # (onto a coarse grid of ticks; then the Effects tab's lateness)
            grid = np.where(crushed, grid, 1.0)
            starts = np.where(crushed, np.floor(starts / grid + 0.5) * grid + fx_late, starts)
        keep = starts < until - 1e-6
        late_comp = self.echo is not None and self.after("echo")  # (the compressor squeezing the echoes too)
        if self.comp:  # (every key row turned up / down alike: the tone across the keys stays)
            pre = env[keep]
            env[keep] = np.minimum(1.0, pre * self.squeezed(hear[keep]))
        all_starts, all_limits, all_env, all_quiet = [starts[keep]], [limits[keep]], [env[keep]], [quiet[keep]]
        all_gain = [np.ones(int(keep.sum()))]
        if self.echo and len(run):  # (the whole sound again, later and quieter: one more line of notes each)
            made = all_starts[0], all_limits[0], all_env[0], all_quiet[0]
            most = self.lift("echo")  # (the compressor after it may bring a quiet one up)
            fades = self.echo["fade"] if fade is None else fade[keep]
            for i in range(1, int(self.echo["repeats"]) + 1):
                gain = fades ** (i / 2.0)  # (loudness goes with the velocity squared)
                if np.max(gain, initial=0.0) ** 2 * most < SOFT:
                    break
                shift = i * self.echo["time"] * self.ppq
                all_starts.append(made[0] + shift)
                all_limits.append(made[1] + int(math.floor(shift + 0.5)))
                if late_comp:  # (squeezed as heard then, among the other echoes)
                    all_env.append(np.minimum(1.0, pre * gain * gain
                                              * self.squeezed(hear[keep] + i * self.echo["time"])))
                else:
                    all_env.append(made[2])
                all_gain.append(np.full(len(made[0]), 1.0 if late_comp else gain))
                all_quiet.append(made[3])
        if len(run):
            st, li = np.concatenate(all_starts), np.concatenate(all_limits)
            env, qu = np.concatenate(all_env), np.concatenate(all_quiet)
            sets = len(all_starts)  # (the sound and its echoes)
            mixed = env * np.tile(mix[keep], sets)
            fa = np.sqrt(mixed) * np.tile(soft[keep], sets) * np.concatenate(all_gain)
            if self.comp:  # (too quiet even after the compressor: left out, decided after it)
                qu = qu | (mixed * np.concatenate(all_gain) ** 2 < SOFT)
            if len(oscs) > 1 and qu.any():  # (both oscillators: one too quiet while the other sounds never cuts it)
                drop = silent_beside(st, li, qu, np.tile(osc[keep], sets))
                st, li, fa, qu = st[~drop], li[~drop], fa[~drop], qu[~drop]
            # (Blend, OSC B: repeats on one tick = the loudest one, left out only if all are)
            if (self.gains != 1.0).any() or "blend" in self.moved or self.osc2:
                first = np.lexsort((-fa, qu))
                st, li, fa, qu = st[first], li[first], fa[first], qu[first]
            sq, which = _grid(st, li)
            factor = fa[which]
            heard = ~qu[which]  # (volume 0: left out once every repeat has its end)
            sq, factor = sq[heard], factor[heard]
        else:
            sq, factor = np.zeros((0, 2), np.int64), np.zeros(0)
        factor.setflags(write=False)
        got = self.got[key] = (sq, factor)
        return got

    def trem(self, src=slice(None)):
        """The tremolo's (steady part, depth) for these repeats (src: their places in self.flat): the run's, or each
        repeat's own while the MOD tab moves its Depth."""
        if "tremolo_depth" not in self.moved:
            return self.runs[0]["trem"]
        d = self.flat["tremolo_depth"][src]
        return 1.0 - d, d

    def trem_come(self, src=slice(None)):
        """How much of the tremolo has come in (0..1) for these repeats: none for its Delay, then over its Rise, from
        each note's start (moved by the MOD tab: as timed_line works it out)."""
        if "tremolo_in" in self.timed:
            return self.flat["tremolo_in"][src]
        wait, rise = self.trem_in
        since = self.flat["trem_since"][src]
        return np.clip((since - wait) / rise, 0.0, 1.0) if rise > 0 else (since >= wait - 1e-9).astype(float)

    def mode_now(self, o, src):
        """Oscillator o's Mode (OSC A = 0, OSC B = 1) for these repeats: as set, or its amount knob each repeat's
        own while the MOD tab moves it (MODE_AMOUNTS); its Time moved: "progress" = how far through it each is."""
        mode = self.modes[o]
        prefix = "osc2_" if o else ""
        name = prefix + f"{mode.get('kind')}_{MODE_AMOUNTS.get(mode.get('kind'))}"
        if name in self.moved:
            mode = dict(mode, **{MODE_AMOUNTS[mode["kind"]]: self.flat[name][src]})
        if f"{prefix}{mode.get('kind')}_in" in self.timed:
            mode = dict(mode, progress=self.flat[f"{prefix}{mode['kind']}_in"][src])
        speed = {"fm": "ratio", "pulse": "rate"}.get(mode.get("kind"))
        if speed and f"{prefix}{mode['kind']}_{speed}" in self.moved:  # (Ratio / Rate moved: added up, wave_hits)
            name = f"{prefix}{mode['kind']}_{speed}"
            mode = dict(mode, **{speed: self.flat[name][src], "turns": self.flat[name + "_turns"][src]})
        return mode

    def after(self, kind):
        """True when the compressor is on and comes after the Effects tab's `kind` (so it squeezes what that makes)."""
        return bool(self.comp and kind in self.order and self.order.index(kind) < self.order.index("compressor"))

    def lift(self, kind):
        """How many times louder the compressor after `kind` can make something at most (its Gain), else 1: what's
        too quiet to keep is decided after it."""
        return 10.0 ** (self.most("compressor_gain", self.comp["gain"]) / 20.0) if self.after(kind) else 1.0

    def comp_curve(self):
        """The compressor as a real one works: it listens to the whole sound (every note sounding together, how loud
        each is from its Volume line and tremolo, not from which key row it's on: Sweep, Wah and Blend are its tone),
        turns it down by how far it's over the threshold, reaching that over Attack when it gets louder and letting
        go over Release when it gets quieter, then lifts it by Gain. (start beat, step, how many times as loud at
        each step); the sound's tail and echoes count only when it comes after them."""
        f, each, comp = self.flat, self.each, self.comp
        if not len(f["beat"]):
            return None
        lv = np.where(f["has_volume"], f["volume"], 1.0)
        if self.osc2:  # (OSC B as loud as its Level)
            lv = lv * f["level"]
        trem = self.trem()
        if any(self.trem_in) or "tremolo_in" in self.timed:
            lv = lv * (1.0 - trem[1] * self.trem_come() * (1.0 - np.cos(2.0 * np.pi * f["turns"])) / 2.0)
        else:
            lv = lv * (trem[0] + trem[1] * (1.0 + np.cos(2.0 * np.pi * f["turns"])) / 2.0)
        tails = np.repeat(each["tail"], each["n"])
        if tails.any():
            level = f["reverb_level"] if "reverb_level" in self.moved else self.reverb["level"]
            lv = np.where(tails, lv * level ** 2 * (1.0 - f["tail_u"]) ** 3, lv)
        ends = each["end"] / self.ppq - self.left
        t0 = float(each["beat0"].min())
        t1 = float(ends.max())
        echo = self.echo if self.after("echo") else None
        if echo:
            t1 += echo["repeats"] * echo["time"]
        dt = max(1 / 128, (t1 - t0) / 100000)  # (a step: fine enough for the tremolo, never too many)
        size = int((t1 - t0) / dt) + 2
        power = np.zeros(size)
        faded = echo and "echo_fade" in self.moved  # (the echo's Fade moved: each step's, by the notes sounding)
        fades = np.zeros(size) if faded else None
        for r in range(len(self.runs)):  # (each note's loudness at every step it sounds; notes together add up)
            o, n = int(each["offsets"][r]), int(each["n"][r])
            if not n or each["tail"][r] and not self.after("reverb"):
                continue
            b, v = f["beat"][o:o + n], lv[o:o + n]
            k0, k1 = int(math.ceil((b[0] - t0) / dt)), int(math.floor((ends[r] - t0) / dt))
            if k1 >= k0:
                p = np.interp(t0 + np.arange(k0, k1 + 1) * dt, b, v) ** 2
                power[k0:k1 + 1] += p
                if faded:
                    fades[k0:k1 + 1] += p * np.interp(t0 + np.arange(k0, k1 + 1) * dt, b, f["echo_fade"][o:o + n])
        if echo:  # (the echoes after it count too)
            dry = power.copy()
            fade = fades / np.maximum(power, 1e-24) if faded else echo["fade"]
            for i in range(1, int(echo["repeats"]) + 1):
                k = int(round(i * echo["time"] / dt))
                if k < size:
                    power[k:] += (fade[:size - k] if faded else fade) ** (2 * i) * dry[:size - k]
        # (the knobs as the MOD tab moves them, at each step: by the newest note, as a synth's effects)
        at = t0 + np.arange(size) * dt
        thr, ratio, gain = (setting_at(self.hz, f"compressor_{k}", comp[k], at, None) if f"compressor_{k}"
                            in self.moved else comp[k] for k in ("threshold", "ratio", "gain"))
        db = 10.0 * np.log10(np.maximum(power, 1e-24))
        want = np.maximum(0.0, db - thr) * (1.0 - 1.0 / ratio)  # (dB to turn down)
        down = np.empty(size)
        a = 1.0 - math.exp(-dt / comp["attack"]) if comp["attack"] > 0 else 1.0
        rel = 1.0 - math.exp(-dt / comp["release"]) if comp["release"] > 0 else 1.0
        now = 0.0
        if {"compressor_attack", "compressor_release"} & self.linked:  # (moved: each step's own)
            a, rel = (np.ones(size) * v if f"compressor_{k}" not in self.linked else np.where(
                t > 0, 1.0 - np.exp(-dt / np.maximum(t, 1e-12)), 1.0) for k, v, t in (
                ("attack", a, setting_at(self.hz, "compressor_attack", comp["attack"], at, None)),
                ("release", rel, setting_at(self.hz, "compressor_release", comp["release"], at, None))))
            for k, (w, up, back) in enumerate(zip(want.tolist(), a.tolist(), rel.tolist())):
                now += (w - now) * (up if w > now else back)
                down[k] = now
        else:
            for k, w in enumerate(want.tolist()):
                now += (w - now) * (a if w > now else rel)
                down[k] = now
        return t0, dt, 10.0 ** ((gain - down) / 20.0)

    def squeezed(self, beat):
        """How many times as loud the compressor makes the sound at these beats (from the left edge)."""
        if self.squeeze is None:
            return np.full(len(beat), 10.0 ** (self.comp["gain"] / 20.0))
        t0, dt, g = self.squeeze
        return g[np.clip(np.round((np.asarray(beat) - t0) / dt).astype(np.int64), 0, len(g) - 1)]

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
    hz = live(sh["hz"], left_edge(sh))
    hz_json = json.dumps(hz, sort_keys=True)
    if has_fx(hz):
        lo, hi = key_range(sh)
        return _key_grid(hz_json, left_edge(sh), ppq, lo, hi - lo + 1)
    return _squares(hz_json, left_edge(sh), ppq)


def left_edge(sh):
    """The beat a custom shape's box starts at (its tones count from there)."""
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    return min(b0, b1, b2, b1 + b2 - b0)


def shifted_hz(hz, d):
    """hz for the shape's left edge moved d beats to the LEFT (d < 0: to the right), so every tone and effect point
    stays on the same beat in the song. Tones then before the edge are left out (one reaching past it starts at it);
    a repeating effect counted from the edge starts its repeat that much later, so it stays in step."""
    if abs(d) < 1e-12:
        return hz
    hz = json.loads(json.dumps(hz))
    tones = []
    for n in hz.get("tones") or ():
        t, end = n["t"] + d, n["t"] + n["len"] + d
        if end <= MIN_LEN:
            continue
        n["t"], n["len"] = max(0.0, t), max(MIN_LEN, end - max(0.0, t))
        tones.append(n)
    if "tones" in hz:
        ids = {n.get("id") for n in tones}
        for n in tones:
            n["to"] = [s for s in n.get("to") or () if s.get("id") in ids]
        hz["tones"] = tones
    loop, froms = hz.get("loop") or {}, hz.get("from") or {}
    for name, pts in (hz.get("fx") or {}).items():
        if name in froms:  # (counted from each note: the edge doesn't matter)
            continue
        hz["fx"][name] = _turned_repeat(pts, d, loop[name]) if name in loop else _shifted_line(pts, d)
    for name, pts in (hz.get("amount") or {}).items():
        hz["amount"][name] = _shifted_line(pts, d)
    if hz.get("mod"):  # (the Free LFOs: counted that much later, so they stay in step too)
        phase = hz["mod"].get("phase", 0.0) + d
        hz["mod"].pop("phase", None)
        if abs(phase) > 1e-12:
            hz["mod"]["phase"] = phase
    return hz


def hz_up_to(hz, width):
    """hz without the tones starting at or after width beats from the left edge (a part split off a Hz bass: they
    made nothing in it); slides to them go too."""
    tones = [n for n in hz.get("tones") or () if n["t"] < width - 1e-9]
    if len(tones) == len(hz.get("tones") or ()):
        return hz
    ids = {n.get("id") for n in tones}
    for n in tones:
        n["to"] = [s for s in n.get("to") or () if s.get("id") in ids]
    return dict(hz, tones=tones)


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
    played = live(hz)  # (the Arpeggio box's octaves go higher)
    top = max((pitch(n) for n in played.get("tones") or ()), default=hz["key"])
    pts = (played.get("fx") or {}).get("pitch") if hz.get("tones") else None
    if pts:  # (the amount line only ever weakens it; the MOD tab's links push it up as far as they reach)
        push = sum(abs(link["amount"]) if link.get("bipolar") else max(0.0, link["amount"])
                   for link in (played.get("mod") or {}).get("links", ()) if link["to"] == "pitch")
        top += max(0.0, (min(1.0, max(p[1] for p in pts) + push) - 0.5) * 2.0 * PITCH)
    if played.get("osc2") and played.get("tones"):  # (OSC B tuned up)
        top += max(0.0, osc2_shift(played["osc2"]))
    return wave(hz, ppq, top)
