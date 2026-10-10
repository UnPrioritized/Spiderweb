"""Hz bass settings: every limit and starting value, and the checks that clean saved settings (hzbass.py's
docstring says what each setting does)."""

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
# above the Hz bass's own tone, like a filter's key tracking; -1 = down); bend_range = the Pitch box's Range: whole
# keys the "pitch" line goes up at 1 and down at 0 (missing = PITCH; bend_range())
LFO = {"vibrato_rate": (0.0, 64.0), "tremolo_depth": (0.0, 1.0), "tremolo_wait": (0.0, 64.0),
       "tremolo_rise": (0.0, 64.0), "sweep_track": (-1.0, 1.0), "bend_range": (1.0, 48.0)}
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
PITCH = 12.0  # "pitch": keys up at 1 (and down at 0; 0.5 = the tone as placed), unless hz["lfo"]["bend_range"]
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
SLIDE_BEND = 0.95  # a slide's own bend goes this far each way (hz_glide.slide_part; 1 would be a jump)
GLIDE_CURVE = 0.5  # ... its curve when there's none: -1 = slow first, 0 = straight, 1 = fast first (glide_left)
BLEND = 0.5  # ... how loud the middle copies are next to the outer ones when there's none: all the same (blend_gains)
TRACE_POINTS = 256  # hz["trace"]: the most points of the drawn wave shape
START_TRACE = ((0.0, 0.0), (0.0, 1.0))  # ... as it starts: straight up at the wave's start (every key together)


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


def clean_line(pts):
    """A line over the notes checked (the layer's loudness line, hz["loud"]): [[beat, value(, bend)], ...] in
    order, or [] when it's no good."""
    try:
        got = [clean_point(p) for p in pts or ()]
    except (TypeError, ValueError, AttributeError, IndexError):
        return []
    return sorted((p for p in got if p), key=lambda p: p[0])


def clean_vel(pts):
    """A note's loudness line checked (tone["vel"]): [[u, velocity], ...] in order, u = 0..1 along the note (a point
    may sit just outside it), velocity 1..127; None when it's no good or empty."""
    try:
        got = [[float(u), float(v)] for u, v in pts or ()]
    except (TypeError, ValueError):
        return None
    got = [[u, min(127.0, max(1.0, v))] for u, v in got if math.isfinite(u) and math.isfinite(v)]
    return sorted(got, key=lambda p: p[0]) or None


def clean_bend(pts):
    """A note's own bend line checked (tone["bend"]): [[u, keys(, curve)], ...] in order, u = 0..1 along the note,
    keys up (below 0: down) as far as the Range knob goes, curve = how the piece to the next point bends
    (-SLIDE_BEND..SLIDE_BEND, hz_lines.bent_part; missing = straight); None when it's no good or empty."""
    most = LFO["bend_range"][1]
    out = []
    try:
        for p in pts or ():
            u, k = float(p[0]), float(p[1])
            curve = float(p[2]) if len(p) > 2 else 0.0
            if math.isfinite(u) and math.isfinite(k) and math.isfinite(curve):
                got = [min(1.0, max(0.0, u)), min(most, max(-most, k))]
                if curve:
                    got.append(min(SLIDE_BEND, max(-SLIDE_BEND, curve)))
                out.append(got)
    except (TypeError, ValueError, IndexError):
        return None
    return sorted(out, key=lambda p: p[0]) or None


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
            out[key] = min(hi, max(lo, float(round(v)) if key == "bend_range" else v))
    for key in ("vibrato_timing", "tremolo_timing"):
        if isinstance(lfo, dict) and lfo.get(key) in TIMINGS[1:]:
            out[key] = lfo[key]
    return out


def bend_range(hz):
    """Keys the "pitch" line bends up at 1 and down at 0 (the Pitch box's Range)."""
    return (hz.get("lfo") or {}).get("bend_range", PITCH)


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


def clean_extra(hz):
    """The synth window's own settings of a Hz bass (EXTRAS, not lines) checked: {name: its settings} for those that
    do something."""
    out = {}
    for name in EXTRAS:
        got = CLEAN_EXTRA[name](hz.get(name))
        if got:
            out[name] = got
    return out


def clean_trace(trace):
    """The drawn wave shape checked (hz["trace"]): [[x, y], ...] in drawing order, x = where in one wave (0..1), y =
    how high among the keys (0..1); 2..TRACE_POINTS points, or [] when it's as it starts (START_TRACE)."""
    out = []
    for p in trace if isinstance(trace, list) else ():
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            out.append([min(1.0, max(0.0, x)), min(1.0, max(0.0, y))])
    out = out[:TRACE_POINTS]
    if len(out) < 2 or out == [list(p) for p in START_TRACE]:
        return []
    return out


def trace_hits(trace, y):
    """Where in each wave (0..1) a key row whose middle is at y (0..1 among the shape's keys) hits, drawn wave shape
    `trace`: once wherever the line crosses the row, the line stretched so its lowest point is the lowest key and its
    highest the highest (every key gets at least one hit). A point right on the row counts once (each piece from its
    start, not its end; the last one both). A flat line: every key once, at its first point."""
    lo, hi = min(p[1] for p in trace), max(p[1] for p in trace)
    if hi - lo < 1e-12:
        return [trace[0][0]]
    y = lo + y * (hi - lo)
    got = []
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(trace, trace[1:])):
        if y0 == y1:
            continue
        t = (y - y0) / (y1 - y0)
        if 0.0 <= t < 1.0 or (t == 1.0 and i == len(trace) - 2):
            got.append(x0 + t * (x1 - x0))
    return got


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
# (Echo Time: each repeat's echoes as far apart as it says when that repeat plays; Reverb Length: read as each note
# ends)
RACK_MOD = {"chorus": ("depth", "rate"), "flanger": ("depth", "mix", "rate"), "echo": ("fade", "time"),
            "reverb": ("level", "scatter", "length"),
            "compressor": ("threshold", "ratio", "gain", "attack", "release")}
RACK_CURVES = {("compressor", "ratio"): RACK["compressor"]["ratio"][1],  # (curved knobs: their top)
               ("compressor", "attack"): TIME_TOP, ("compressor", "release"): TIME_TOP,
               ("chorus", "rate"): RATE_TOP, ("flanger", "rate"): RATE_TOP, ("echo", "time"): TIME_TOP,
               ("reverb", "length"): TIME_TOP}
MOD_RACK = {f"{kind}_{k}": (kind, k) for kind, ks in RACK_MOD.items() for k in ks}
MOD_SETTINGS.update({name: (*RACK[kind][k][:2], RACK_CURVES.get((kind, k))) for name, (kind, k) in MOD_RACK.items()})
# ... the time knobs (beats; along a curve up to TIME_TOP, typed up to TIME_MOST): a running envelope's stage gets
# shorter or longer as they move (timed_line). The Volume box's Attack / Decay / Release (ADSR_KNOBS) only while
# the Volume line is as its knobs make it (knob_adsr)
ADSR_KNOBS = ("attack", "decay", "release")
MOD_SETTINGS.update({name: (0.0, TIME_MOST, TIME_TOP) for name in ADSR_KNOBS})
TIME_STEP = 1 / 1024  # beats: how finely a moved envelope is worked out (fewer steps on very long notes)
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
               "kept": clean_kept, "macro": clean_macro, "osc2": clean_osc2, "mod": clean_mod, "trace": clean_trace}
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


def clean_off(off, fx):
    """The effects switched off (Bypass: their lines kept, no effect) checked: those in fx, in FX order."""
    off = off if isinstance(off, list) else ()
    return [name for name in FX if name in fx and name in off]


# the envelope-like things whose time knobs the MOD tab moves (timed_line): name -> its knobs, how they're read. The
# lines' (from them, as their knobs make them), then those worked out in KeyGrid: the Tremolo's Delay / Rise (how
# much of it has come in), each Mode's Time (FM's depth falling, Sync's rise: how far through it)
TIMED_NAMES = {"volume": ADSR_KNOBS, "pitch": ("time",), "sweep": ("sweep_time",),
               "vibrato": ("vibrato_wait", "vibrato_delay"), "tremolo_in": ("tremolo_wait", "tremolo_rise"),
               **{f"{osc}{kind}_in": (f"{osc}{kind}_time",) for osc in ("", "osc2_") for kind in ("fm", "sync")}}
TIMED_KNOBS = {k: line for line, knobs in TIMED_NAMES.items() for k in knobs}
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
# ... the tuning: the Voice box's Detune (each repeat's own spread) and Random start (read once, as each note starts),
# OSC B's Octave / Semi (whole octaves / keys, as their knobs) and Fine, bending its tone as the Pitch line does
# (KeyGrid.osc2_keys)
OSC2_TUNE = ("osc2_octave", "osc2_semi", "osc2_fine")
MOD_SETTINGS.update(detune=(0.0, DETUNE, None), random=(0.0, 1.0, None),
                    **{name: (*OSC2[name[5:]][:2], None) for name in OSC2_TUNE})
# ... and the Arpeggio box's Speed (its steps added up, so the run goes faster or slower from where it is), Gate and
# Swing (read as each step starts): arp_moved
ARP_KNOBS = ("arp_speed", "arp_gate", "arp_swing")
MOD_SETTINGS.update(arp_speed=(*ARP["speed"][:2], ARP["speed"][1]), arp_gate=(*ARP["gate"][:2], None),
                    arp_swing=(*ARP["swing"][:2], None))
MOD_TARGETS = MOD_LINES + tuple(MOD_SETTINGS)
# (knobs the engine reads once for a whole note or the whole sound, not for each repeat: KeyGrid.moved leaves them
# out)
NOT_EACH = set(TIMED_KNOBS) | {"glide", "curve", "compressor_attack", "compressor_release", "reverb_length",
                               *LFO_RATES, *OSC2_TUNE, *ARP_KNOBS}


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
    Random start, a wave mode, OSC B, a drawn wave shape or Arpeggio steps quieter than full (as played, or still to
    be played)."""
    arp = hz.get("arp") or {}
    quiet = (any(n.get("level", 1.0) != 1.0 or n.get("vel") for n in hz.get("tones") or ())
             or any(s["level"] != 1.0 for s in (arp.get("steps") or ())[:arp.get("count", STEPS)])
             or bool(hz.get("loud")))  # (loudness lines: each note's, the layer's)
    return ((bool(hz.get("fx")) or len(copies(hz)) > 1 or bool((hz.get("voice") or {}).get("random"))
             or bool(hz.get("mode")) or bool(hz.get("osc2")) or bool(hz.get("trace")) or quiet
             or bool((hz.get("mod") or {}).get("links"))
             or any(not e.get("off") for e in hz.get("rack") or ())) and bool(hz.get("tones")))


def clean_slide(s):
    """A saved slide checked: {"id", "out", "in"} + its own "bend" (-SLIDE_BEND..SLIDE_BEND; missing = untouched,
    it follows the Glide curve) and "kind": "double" (an S; missing = one curve). Raises like float() when broken."""
    out = {"id": int(s["id"]), "out": max(0.0, float(s["out"])), "in": max(0.0, float(s["in"]))}
    try:  # (a broken bend alone is left out: the slide follows the Glide curve, the note stays)
        bend = float(s["bend"]) if s.get("bend") is not None else math.nan
    except (TypeError, ValueError):
        bend = math.nan
    if math.isfinite(bend):
        out["bend"] = min(SLIDE_BEND, max(-SLIDE_BEND, bend))
    if s.get("kind") == "double":
        out["kind"] = "double"
    return out


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
            tone["to"] = [clean_slide(s) for s in n.get("to") or ()]
            vel = clean_vel(n.get("vel")) if n.get("vel") else None
            if vel:
                tone["vel"] = vel
            bend = clean_bend(n.get("bend")) if n.get("bend") else None
            if bend:
                tone["bend"] = bend
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
    loud = clean_line(hz.get("loud")) if isinstance(hz.get("loud"), list) else []
    if any(p[1] != 1.0 for p in loud):  # (at 100 % all along: it changes nothing, left out)
        out["loud"] = loud
    layers = clean_layers(hz, out)
    if layers:
        out["layers"], out["layer"] = layers
    if any(l.get("tones") for l in layers_of(out)):
        for flag in ("grow", "own"):
            if hz.get(flag) is True:
                out[flag] = True
    return out


# ---------------------------------------------------------------- layers

# one layer's own ("tones" and "loud" = its notes and its loudness line: not its sound, which Copy sound to copies)
SOUND = ("tones", "fx", "loop", "off", "amount", "from", "fit", "sustain", "lfo", "loud") + EXTRAS
NOT_SOUND = ("tones", "loud")
LAYERS = 64  # the most layers in one Hz bass (user, 2026-10-09: was 16)
LAYER_NAME = 40  # characters: the longest layer name


def clean_layers(hz, out):
    """hz["layers"] checked: (layers, picked number) or None. A Hz bass with layers keeps the PICKED layer's notes
    and sound where one without has them (hz["tones"], hz["fx"]...: everything that edits them works on it) and
    hz["layers"] = [{"name", "colour" (a number in the piano roll's note colours), "mute": True?, "solo": True?,
    and for every layer but the picked one its own SOUND}, ...] in order; hz["layer"] = the picked one's number.
    out = hz checked so far (the shared settings, the picked layer's sound)."""
    got = hz.get("layers")
    if not isinstance(got, list) or not got:
        return None
    try:
        picked = int(hz.get("layer", 0))
    except (TypeError, ValueError):
        picked = 0
    picked = max(0, min(len(got[:LAYERS]) - 1, picked))
    shared = {k: out[k] for k in ("key", "cents", "bpm")}
    layers = []
    for i, entry in enumerate(got[:LAYERS]):
        entry = entry if isinstance(entry, dict) else {}
        name = entry.get("name")
        try:
            colour = max(0, int(entry.get("colour", i)))
        except (TypeError, ValueError):
            colour = i
        info = {"name": name[:LAYER_NAME] if isinstance(name, str) else "", "colour": colour}
        for flag in ("mute", "solo"):
            if entry.get(flag) is True:
                info[flag] = True
        if i != picked:
            own = clean_hz(dict(shared, **{k: entry[k] for k in SOUND if k in entry}))
            info.update({k: own[k] for k in SOUND if k in own})
        layers.append(info)
    return layers, picked


def layers_of(hz):
    """Every layer of a Hz bass as a Hz bass of its own (no "layers" in it), in order; one without layers: [hz]."""
    got = hz.get("layers")
    if not got:
        return [hz]
    picked = hz.get("layer", 0)
    shared = {k: v for k, v in hz.items() if k not in SOUND and k not in ("layers", "layer", "_memo")}
    return [dict(shared, **{k: src[k] for k in SOUND if k in src}) for src in
            (hz if i == picked else entry for i, entry in enumerate(got))]


def heard_layers(hz):
    """The layers that make notes: those with notes, not muted (any soloed: only those). None has notes: the Hz
    bass as it is (its one tone, no layers)."""
    every = layers_of(hz)
    if not hz.get("layers") or not any(l.get("tones") for l in every):
        return [every[hz.get("layer", 0)] if hz.get("layers") else hz]
    solo = any(e.get("solo") for e in hz["layers"])
    return [l for l, e in zip(every, hz["layers"]) if l.get("tones") and (e.get("solo") if solo else not e.get("mute"))]


def all_tones(hz):
    """The notes of every layer of a Hz bass, one list."""
    return [n for l in layers_of(hz) for n in l.get("tones") or ()]


def with_layers(hz, every, picked=None):
    """hz with its layers' sounds (layers_of's list, each changed) put back, picked (default: as it was) on top."""
    if not hz.get("layers"):
        return every[0]
    picked = hz.get("layer", 0) if picked is None else picked
    out = {k: v for k, v in hz.items() if k not in SOUND and k != "_memo"}
    out.update({k: every[picked][k] for k in SOUND if k in every[picked]})
    out.update({k: v for k, v in every[picked].items() if k not in SOUND and k not in ("layers", "layer")})
    out["layers"] = [{k: v for k, v in e.items() if k not in SOUND} if i == picked else
                     dict({k: v for k, v in e.items() if k not in SOUND}, **{k: every[i][k] for k in SOUND if k in every[i]})
                     for i, e in enumerate(hz["layers"])]
    out["layer"] = picked
    return out


def each_layer(hz, fn):
    """hz with fn(layer's Hz bass) done to every layer (e.g. moved: every layer's notes and lines alike)."""
    return with_layers(hz, [fn(l) for l in layers_of(hz)])
