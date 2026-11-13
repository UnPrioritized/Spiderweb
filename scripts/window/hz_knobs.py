"""The synth window's Knobs tab (window/hz_synth.py): boxes of knobs, like a synth's (user), each writing the effects'
lines the Hz bass already has, so everything they do is in the MIDI. A line drawn by hand that the knobs can't have
made shows them about where it is (with a note); turning one makes new lines from the knobs.
  Volume: Attack, Decay, Sustain, Release = the Volume line once per note, with its sustain point and fall.
  OSC A (the Wave box): one waveform (or the plain tone) at a Shape amount, and Octave below: those lines, flat; Mode
  (FM, Pulse width, Sync, Growl, Bitcrush) and its knobs (only the picked mode's shown) = hz["mode"] (not lines).
  OSC B (after it): On, Waveform, Plays on (every key / split keys), Shape, Octave, Semi, Fine, Level, its own Mode =
  hz["osc2"] (not lines; there only while on).
  Pitch: Amount (keys) and Time = the Pitch line once per note, from Amount keys off to the note's tone.
  Vibrato (LFO 1): Timing + Rate (hz["lfo"]), Depth = the Vibrato line, Delay = none that long, then Rise = it
  comes in over that long, once per note.
  Tremolo (LFO 2): Timing (hz["lfo"]), Rate = the Tremolo line (its value is how fast), Depth, Delay, Rise
  (hz["lfo"]). Timing other than Free = the Rate moves by note lengths (straight, triplets, dotted).
  Tone: Sweep (on / off) from Start to End in Time = the Sweep line once per note, Key track (hz["lfo"]) = how far
  it follows each note's pitch; Wah = its line, flat.
  Filter (MIDI CC; user 2026-10-21, Serum / Vital put it next to the oscillators): Cutoff, Amount and its own
  Attack, Decay, Sustain, Release = the Cutoff line (CC 74) once per note (Amount 0: flat; Cutoff 50 %: none).
  Character: Slant, Groups, Off pitch, Noisy = their lines, flat.
  Voice: Voices, Detune, Blend, Random start, Voices on (split / same keys), Glide, Curve, Only notes that touch,
  Legato = hz["voice"] (not lines).
  Arpeggio (third row): On, Pattern, Speed, Octaves, Gate, Swing, Chord, Scale, Root = hz["arp"] (not lines; there
  only while on); the Steps pattern's own steps under its picture (window/hz_steps.py).
Each box has a picture: the envelope (with a dot while a key sounds), one wave's hits (the notes), the pitch, the
wobbles over two beats, which keys are loud over time, the keys' notes over four waves, the copies' tones and a glide."""

import math
import tkinter as tk
from tkinter import font as tkfont, ttk

import numpy as np

from files import colours
from files.lang import english_width, tr, trnum
from files.mathexpr import calc, fmt
from notes.hzbass import (ARP, ARP_PATTERNS, BLEND, CHORDS, CRUSH, DETUNE, FAST, GLIDE_CURVE, GROUPS, GROWL, LFO, LOOP,
                          MODES, OFF_BOXES, OFF_PITCH, OSC2, PITCH, RACK, RATE_TOP, SCALES, SOFT, START_COUNT,
                          START_STEPS, MOD_BOXES, MOD_NEED_LINE, STEPS, SUB, TIMINGS, TREMOLO, TREMOLO_DEPTH, VIBRATO_RATE,
                          VOICES, WAH, WAVES, FILTER_START, adsr_line, filter_line,
                          arpeggiated,
                          blend_gains, glide_left, clean_arp, clean_extra, clean_mode, clean_steps, clean_voice, copies,
                          group_count, line_at, osc2_shift, wave_hits, HEIGHT_FROM, HEIGHT_MOST, height_times,
                          height_value)
from roll.roll_shared import NOTE_NAMES
from window.hz_dials import (COUNTS, KINDS, KNOB_BODY, KNOB_FOCUS, KNOB_RIM, KNOB_TRACK, PERCENTS, TIME_KNOB,
                             TIME_MOST, UPDOWN, Dial, Ringed, UpDown, knob_of, paint_knob, shown,
                             value_of)  # (moved)
from window.hz_effects import AMOUNT, FX_COLOR
from window.hz_steps import StepEditor
from window.synth_look import (DIM, EDGE, ENTRY, GRID, MID, PANEL, PIC, TEXT, Box, bright, dark_list, mix)
from window.tool_window import Knob
from window.widgets import Scrub, Tooltip, grid_shown, read_number, unit_word, wheel_ready

PLURAL_UNITS = ("hz.synth_beats", "hz.synth_keys", "hz.synth_cents")  # (their "_one" key: for 1)
# the LFO boxes' Rate knobs that can move by note lengths (Timing dropdown; hzbass.TIMINGS), and how a timing's rates
# are to the plain note lengths' (1/4 = 1 a beat)
TIMED = {"vibrato_rate": "vibrato_timing", "tremolo_rate": "tremolo_timing"}
TIMING_TIMES = {"straight": 1.0, "triplet": 1.5, "dotted": 2 / 3}
NOTE_RATES = tuple(2.0 ** i for i in range(-4, 7))  # (times a beat: 4 bars' notes .. 1/256 notes)
# the Wave box's modes (hzbass.MODES): their knobs (knob = mode_setting) and kinds; only the picked mode's are shown
MODE_KNOBS = {"fm": (("fm_depth", "percent"), ("fm_ratio", "ratio"), ("fm_time", "time")),
              "pulse": (("pulse_width", "width"), ("pulse_rate", "vib_rate")),
              "sync": (("sync_amount", "sync"), ("sync_time", "time")),
              "growl": (("growl_amount", "percent"), ("growl_every", "every")),
              "crush": (("crush_amount", "percent"),)}
MODE_NAMES = ("off",) + tuple(MODE_KNOBS)
OSC2_KNOBS = (("shape", "percent"), ("octave", "osc_octave"), ("semi", "keys"), ("fine", "fine"), ("level", "percent"))
# the boxes and their knobs: (knob, kind, value at the start / a middle-click); rows of boxes
BOXES = {"volume": (("attack", "time", 0.0), ("decay", "time", 0.0), ("sustain", "percent", 1.0),
                    ("release", "time", 0.0), ("volume_boost", "percent", 0.0)),
         "wave": (("shape", "percent", 1.0), ("octave", "percent", 0.0))
         + tuple((key, kind, MODES[m][key.split("_", 1)[1]][2]) for m, knobs in MODE_KNOBS.items() for key, kind in knobs),
         "pitch": (("amount", "pitch", 0.0), ("time", "time", 0.25), ("bend_range", "range", PITCH)),
         # (vibrato_delay is its Rise: it was called Delay first; vibrato_wait is its Delay)
         "vibrato": (("vibrato_rate", "vib_rate", VIBRATO_RATE), ("vibrato_depth", "percent", 0.0),
                     ("vibrato_wait", "time", 0.0), ("vibrato_delay", "time", 0.0)),
         "tremolo": (("tremolo_rate", "trem_rate", 0.0), ("tremolo_depth", "percent", TREMOLO_DEPTH),
                     ("tremolo_wait", "time", 0.0), ("tremolo_rise", "time", 0.0), ("tremolo_boost", "percent", 0.0)),
         "tone": (("sweep_start", "percent", 1.0), ("sweep_end", "percent", 0.0), ("sweep_time", "time", 1.0),
                  ("sweep_track", "bend", 0.0), ("wah", "percent", 0.0)),
         "filter": tuple((k if k == "cutoff" else f"filter_{k}", kind, FILTER_START[k]) for k, kind in (
             ("cutoff", "percent"), ("amount", "bend"), ("attack", "time"), ("decay", "time"), ("sustain", "percent"),
             ("release", "time"))),
         "character": (("slant", "percent", 0.0), ("groups", "groups", 1.0), ("offpitch", "percent", 0.0),
                       ("noisy", "percent", 0.0), ("height", "height", 1.0), ("height_rand", "percent", 0.0)),
         "voice": (("voices", "voices", 1.0), ("detune", "cents", 20.0), ("blend", "percent", BLEND),
                   ("random", "percent", 0.0), ("glide", "time", 0.0), ("curve", "bend", GLIDE_CURVE)),
         "arp": tuple((f"arp_{k}", k, start) for k, (_, _, start) in ARP.items()),
         # (OSC B: its knobs, then its own Mode's, named osc2_ + OSC A's)
         "osc2": tuple((f"osc2_{k}", kind, OSC2[k][2]) for k, kind in OSC2_KNOBS)
         + tuple((f"osc2_{key}", kind, MODES[m][key.split("_", 1)[1]][2]) for m, knobs in MODE_KNOBS.items()
                 for key, kind in knobs)}
ROWS = (("volume", "wave", "osc2", "pitch"), ("vibrato", "tremolo", "tone", "filter", "character"), ("voice", "arp"))
# the Effects tab's effects (hzbass.RACK, window/hz_rack.py): their knobs (knob = effect_setting) and kinds
RACK_KNOBS = {"chorus": (("chorus_depth", "cents"), ("chorus_rate", "vib_rate")),
              "flanger": (("flanger_rate", "vib_rate"), ("flanger_depth", "percent"), ("flanger_mix", "percent")),
              "echo": (("echo_time", "time"), ("echo_repeats", "repeats"), ("echo_fade", "percent")),
              "reverb": (("reverb_length", "time"), ("reverb_scatter", "percent"), ("reverb_level", "percent")),
              "compressor": (("compressor_threshold", "threshold"), ("compressor_ratio", "comp_ratio"),
                             ("compressor_attack", "time"), ("compressor_release", "time"),
                             ("compressor_gain", "gain"))}
KNOBS = {key: (box, kind, start) for box, knobs in BOXES.items() for key, kind, start in knobs}
KNOBS.update({key: (fx, kind, RACK[fx][key.split("_", 1)[1]][2]) for fx, knobs in RACK_KNOBS.items()
              for key, kind in knobs})
COLOURS = {k: bright(v) for k, v in {
    "volume": FX_COLOR["volume"], "wave": FX_COLOR["sine"], "pitch": FX_COLOR["pitch"], "vibrato": FX_COLOR["vibrato"],
    "tremolo": FX_COLOR["tremolo"], "tone": FX_COLOR["sweep"], "character": FX_COLOR["slant"], "voice": "#3a6ee0",
    "arp": "#c0398a", "osc2": "#2f9e6e", "filter": FX_COLOR["cc_cutoff"]}.items()}
# the lines each box writes (its header light is lit while one of them changes the sound) and its own setting
BOX_LINES = {"volume": ("volume",), "wave": tuple(WAVES) + ("octave",), "pitch": ("pitch",), "vibrato": ("vibrato",),
             "tremolo": ("tremolo",), "tone": ("sweep", "wah"), "filter": ("cc_cutoff",),
             "character": ("slant", "offpitch", "noisy", "groups", "height", "height_rand")}
BOX_EXTRA = {"wave": "mode", "voice": "voice", "arp": "arp", "osc2": "osc2"}
# the boxes a click on the light / name switches off and on (Bypass: the lines in hz["off"], the own setting moved to
# hz["bypass"]; the Arpeggio's click is its On instead)
BYPASS = OFF_BOXES
PICTURES = {"volume": (260, 90), "wave": (200, 90), "pitch": (150, 90), "vibrato": (230, 60), "tremolo": (150, 60),
            "tone": (200, 90), "filter": (230, 90), "character": (220, 60), "voice": (230, 60), "arp": (260, 60), "osc2": (200, 90)}
CHARACTER = ("slant", "offpitch", "noisy")  # (the Character box's lines that are just their value; Groups is counted)
WAVE_NAMES = ("none",) + tuple(WAVES)
# every knob and choice where it starts; those that do nothing right now are kept in hz["kept"] (hzbass.clean_kept)
START = dict({key: start for key, (_, _, start) in KNOBS.items()}, wave="none", sweep=False, same=False,
             touching=False, legato=False, mode="off", rack=(), rack_off=(), arp_on=False, arp_pattern="up",
             arp_chord="placed", arp_scale="off", arp_root=0, vibrato_timing="free", tremolo_timing="free",
             osc2_on=False, osc2_wave="none", osc2_split=False, osc2_mode="off", osc2_a_off=False, height_from="middle",
             arp_steps=[dict(s) for s in START_STEPS], arp_count=START_COUNT)
ARP_CHOICES = ("pattern", "chord", "scale", "root")  # (the Arpeggio box's dropdowns)
ARP_KEYS = ARP_CHOICES + tuple(ARP) + ("steps", "count")  # (all it has)
KEEP = (tuple(KNOBS) + ("same", "touching", "osc2_split", "osc2_a_off") + tuple(f"arp_{w}" for w in ARP_CHOICES)
        + ("osc2_wave", "osc2_mode", "arp_steps", "arp_count"))
CHOICES = {"arp_pattern": ARP_PATTERNS, "arp_chord": tuple(CHORDS), "arp_scale": ("off",) + tuple(SCALES),
           "arp_root": tuple(range(12)), "osc2_wave": WAVE_NAMES, "osc2_mode": MODE_NAMES,
           "height_from": tuple(HEIGHT_FROM)}


def text_key(key):
    """The knob whose name and tip a knob shows: OSC B's Mode knobs are OSC A's (osc2_fm_depth: fm_depth)."""
    return key[5:] if key.startswith("osc2_") and key.split("_")[1] in MODE_KNOBS else key


def kept_value(key, v):
    """A kept knob's value checked against its knob (None: no good)."""
    if key == "arp_root":  # (a key name's number: kept as 5.0)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v in range(12) else None
    if key == "arp_steps":
        return clean_steps(v) if isinstance(v, list) and v else None
    if key == "arp_count":  # (a count: kept as 5.0)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v in range(1, STEPS + 1) else None
    if key in CHOICES:
        return v if v in CHOICES[key] else None
    if key in ("same", "touching", "osc2_split", "osc2_a_off"):
        return v if isinstance(v, bool) else None
    if isinstance(v, bool) or not isinstance(v, float):
        return None
    kind = KNOBS[key][1]
    lo, hi = KINDS[kind][1:3]
    return v if lo - 1e-9 <= shown(kind, v) <= hi + 1e-9 else None


def set_range(keys):
    """The Pitch box's Amount knob turns up to `keys` (its Range) each way."""
    unit, _, _, steps, most = KINDS["pitch"]
    KINDS["pitch"] = (unit, -keys, keys, steps, most)


def pitch_line(amount, time, keys=PITCH):
    """The Pitch line from `amount` keys off (up or down) to the note's tone in `time` beats, fast first (like the
    ready-made Drop), with the line's top `keys` up (the Range): (points, length)."""
    start = min(1.0, max(0.0, 0.5 + amount / (2 * keys)))
    if time <= 0:
        return [[0.0, 0.5]], LOOP[0]
    return [[0.0, start, FAST], [time, 0.5]], max(LOOP[0], time)


def written(key):
    """What a knob turned writes again (SynthKnobs.write): its box, or only the Range (the Pitch line kept)."""
    return "bend_range" if key == "bend_range" else KNOBS[key][0]


def same_line(a, b):
    """Two lines' points the same (to a millionth)."""
    def same(x, y):
        return x == y if isinstance(x, str) or isinstance(y, str) else abs(x - y) < 1e-6
    return len(a) == len(b) and all(len(p) == len(q) and all(map(same, p, q)) for p, q in zip(a, b))


def flat(win, name):
    """An effect's line the same all along (no amount line changing it): its value, else None."""
    pts = win.fxl.get(name)
    if not pts or name + AMOUNT in win.fxl or max(p[1] for p in pts) - min(p[1] for p in pts) > 1e-9:
        return None
    return pts[0][1]


def read_volume(win, was):
    """The Volume box for the lines: ({attack, decay, sustain, release}, True) when the knobs could have made them
    (or there's no Volume line: full all along), else (about where they are, False)."""
    plain = {"attack": 0.0, "decay": 0.0, "sustain": 1.0, "release": 0.0}
    pts = win.fxl.get("volume")
    if not pts:
        return plain, True
    every, at = win.loops.get("volume"), win.sustains.get("volume")
    if not every or win.froms.get("volume") != "note" or at is None or "volume" in win.fits:
        return plain, False
    # as the knobs make it: a rise from 0 first = Attack up to the next point; a fall = up to the last point
    attack = pts[1][0] if len(pts) > 1 and pts[0][1] == 0.0 and pts[0][2:] == [-FAST] else 0.0
    attack = min(max(0.0, attack), at)
    got = {"attack": attack, "decay": at - attack, "sustain": float(line_at(pts, at)),
           "release": pts[-1][0] - at if pts[-1][0] > at + 1e-9 else 0.0}
    if same_line(adsr_line(**got)[0], pts) and "volume:amount" not in win.fxl:
        return got, True
    top = max((p for p in pts if p[0] <= at + 1e-9), key=lambda p: p[1], default=pts[0])  # (the first highest)
    got = {"attack": max(0.0, top[0]), "decay": max(0.0, at - top[0]), "sustain": float(line_at(pts, at)),
           "release": max(0.0, every - at)}
    if got["release"] <= LOOP[0] + 1e-9 and pts[-1][0] <= at + 1e-9:  # (no fall)
        got["release"] = 0.0
    return got, same_line(adsr_line(**got)[0], pts) and "volume:amount" not in win.fxl


def read_wave(win, was):
    """The Wave box: ({wave, shape, octave}, made) as read_volume. No waveform = the plain tone (Shape kept as it
    was); several, or one changing, = drawn by hand."""
    names = [n for n in WAVES if n in win.fxl]
    got = {"wave": names[0] if names else "none", "shape": was["shape"], "octave": 0.0}
    made = len(names) <= 1
    if names:
        v = flat(win, names[0])
        made = made and v is not None
        got["shape"] = v if v is not None else max(p[1] for p in win.fxl[names[0]])
    if "octave" in win.fxl:
        v = flat(win, "octave")
        made = made and v is not None
        got["octave"] = v if v is not None else max(p[1] for p in win.fxl["octave"])
    mode = win.extra.get("mode") or win.extra.get("bypass", {}).get("mode")  # (no mode: its knobs kept as they were)
    got["mode"] = mode["kind"] if mode else "off"
    for key, _ in MODE_KNOBS.get(got["mode"], ()):
        got[key] = mode[key.split("_", 1)[1]]
    return got, made


def read_pitch(win, was):
    """The Pitch box: ({amount, time, bend_range}, made) as read_volume (no Pitch line: Time kept as it was, and
    Amount too while Time is 0: no line then either)."""
    keys = win.lfo.get("bend_range", PITCH)
    pts = win.fxl.get("pitch")
    if not pts:
        amount = min(keys, max(-keys, was["amount"])) if was["time"] <= 0 else 0.0
        return {"amount": amount, "time": was["time"], "bend_range": keys}, True
    every = win.loops.get("pitch")
    got = {"amount": (pts[0][1] - 0.5) * 2 * keys, "time": every or was["time"], "bend_range": keys}
    made = (every and win.froms.get("pitch") == "note" and "pitch" not in win.fits and "pitch" not in win.sustains
            and "pitch:amount" not in win.fxl and same_line(pitch_line(got["amount"], got["time"], keys)[0], pts))
    return got, bool(made)


def vibrato_line(depth, rise, wait=0.0):
    """The Vibrato line: (points, length; None = all along): none for `wait` beats from each note's start, then
    coming in over `rise` beats."""
    if rise <= 0 and wait <= 0:
        return [[0.0, depth]], None
    pts = [[0.0, 0.0]] + ([[wait, 0.0]] if wait > 0 else []) + [[wait + rise, depth]]
    return pts, max(LOOP[0], wait + rise)


def coming_in(b, wait, rise):
    """How much of an LFO there is at beats b from a note's start: none for `wait` beats, then up to all of it over
    `rise` beats (as the notes have it: the Vibrato line, hzbass's tremolo)."""
    if rise > 0:
        return np.clip((b - wait) / rise, 0.0, 1.0)
    return (b >= wait - 1e-9).astype(float)


def timed_rates(timing, hi):
    """The rates (times a beat) a Rate knob moves to with a Timing other than Free, up to hi."""
    return [r * TIMING_TIMES[timing] for r in NOTE_RATES if r * TIMING_TIMES[timing] <= hi + 1e-9]


def snap_rate(v, timing, hi):
    """A rate moved to the nearest note length of its Timing (0 = off stays 0; Free: as it is)."""
    if timing == "free" or v <= 0:
        return v
    return min(timed_rates(timing, hi), key=lambda r: abs(math.log(r / v)))


def note_name(v, timing):
    """A rate's note length, as many MIDI programs write it: 1/4 = once a beat, 1/8T a triplet eighth, 1/8D a
    dotted one ("" for Free or one that's no note length)."""
    if timing == "free" or v <= 0:
        return ""
    q = 4 * v / TIMING_TIMES[timing]  # (1/q of a bar)
    if abs(q - round(q)) < 1e-6 and round(q) >= 1:
        name = f"1/{round(q)}"
    elif abs(1 / q - round(1 / q)) < 1e-6:
        name = f"{round(1 / q)}/1"
    else:
        return ""
    return name + {"straight": "", "triplet": "T", "dotted": "D"}[timing]


def read_vibrato(win, was):
    """The Vibrato box: ({vibrato_rate, vibrato_timing, vibrato_depth, vibrato_wait, vibrato_delay (its Rise)},
    made) as read_volume (no Vibrato line: Delay and Rise kept as they were)."""
    rate = win.lfo.get("vibrato_rate", VIBRATO_RATE)
    timing = win.lfo.get("vibrato_timing", "free")
    pts = win.fxl.get("vibrato")
    if not pts:
        return {"vibrato_rate": rate, "vibrato_timing": timing, "vibrato_depth": 0.0,
                "vibrato_wait": was["vibrato_wait"], "vibrato_delay": was["vibrato_delay"]}, True
    every = win.loops.get("vibrato")
    wait = rise = 0.0
    if every and win.froms.get("vibrato") == "note":
        # (from the points, not the length: a line is never shorter than LOOP[0], so a tiny Delay made it longer)
        if len(pts) == 3 and pts[0][1] == 0.0 and pts[1][1] == 0.0:  # (none first, then coming in)
            wait, rise = pts[1][0], pts[2][0] - pts[1][0]
        elif len(pts) == 2:
            rise = pts[1][0]
        else:
            rise = every
    got = {"vibrato_rate": rate, "vibrato_timing": timing, "vibrato_depth": max(p[1] for p in pts),
           "vibrato_wait": wait, "vibrato_delay": rise}
    want, length = vibrato_line(got["vibrato_depth"], rise, wait)
    made = (same_line(want, pts) and "vibrato" not in win.fits and "vibrato" not in win.sustains
            and "vibrato:amount" not in win.fxl and (length is None) == (every is None))
    return got, made


def read_tremolo(win, was):
    """The Tremolo box: ({tremolo_rate, tremolo_timing, tremolo_depth, tremolo_wait, tremolo_rise}, made) as
    read_volume (no Tremolo line: Rate 0, steady)."""
    lfo = win.lfo
    got = {"tremolo_rate": 0.0, "tremolo_timing": lfo.get("tremolo_timing", "free"),
           "tremolo_depth": lfo.get("tremolo_depth", TREMOLO_DEPTH), "tremolo_wait": lfo.get("tremolo_wait", 0.0),
           "tremolo_rise": lfo.get("tremolo_rise", 0.0)}
    if "tremolo" not in win.fxl:
        return got, True
    v = flat(win, "tremolo")
    got["tremolo_rate"] = TREMOLO * (v if v is not None else max(p[1] for p in win.fxl["tremolo"]))
    return got, v is not None


def sweep_line(start, end, time):
    """The Sweep line: (points, length; None = all along): from Start to End in `time` beats from each note's start,
    fast first like the envelopes (or staying at Start)."""
    if time <= 0 or abs(start - end) < 1e-9:
        return [[0.0, start]], None
    return [[0.0, start, FAST], [time, end]], max(LOOP[0], time)


def read_tone(win, was):
    """The Tone box: ({sweep, sweep_start, sweep_end, sweep_time, sweep_track, wah}, made) as read_volume (no Sweep
    line: off, its knobs kept as they were; Key track is hz["lfo"]'s)."""
    got = {"sweep": False, "sweep_start": was["sweep_start"], "sweep_end": was["sweep_end"],
           "sweep_time": was["sweep_time"], "sweep_track": win.lfo.get("sweep_track", was["sweep_track"]), "wah": 0.0}
    made = True  # (Key track: as the sound has it even without a Sweep line, e.g. one deleted on the Draw tab)
    pts = win.fxl.get("sweep")
    if pts:
        got["sweep"], got["sweep_track"] = True, win.lfo.get("sweep_track", 0.0)
        v = flat(win, "sweep")
        if v is not None:
            got["sweep_start"] = got["sweep_end"] = v
        else:
            got.update(sweep_start=pts[0][1], sweep_end=pts[-1][1], sweep_time=pts[-1][0])
            want, every = sweep_line(got["sweep_start"], got["sweep_end"], got["sweep_time"])
            made = (same_line(want, pts) and every is not None and win.loops.get("sweep") == every
                    and win.froms.get("sweep") == "note" and "sweep" not in win.fits and "sweep" not in win.sustains
                    and "sweep:amount" not in win.fxl)
    if "wah" in win.fxl:
        v = flat(win, "wah")
        made = made and v is not None
        got["wah"] = v if v is not None else max(p[1] for p in win.fxl["wah"])
    return got, made


def filter_vals(v):
    """The Filter box's knobs (filter_amount...) as hz["filter"] keeps them (amount...)."""
    return {k: v[k if k == "cutoff" else f"filter_{k}"] for k in FILTER_START}


def read_filter(win, was):
    """The Filter box: ({cutoff, filter_amount, filter_attack...}, made) as read_volume. Its knobs as it wrote the
    Cutoff line (hz["filter"]), else as they were when they could have made it; a flat line = Cutoff alone; none =
    the middle (no CC sent)."""
    keys = [k for k, _, _ in BOXES["filter"]]
    kept = win.extra.get("filter")
    got = {k: was[k] for k in keys} if kept is None else {k if k == "cutoff" else f"filter_{k}": v
                                                          for k, v in kept.items()}
    pts = win.fxl.get("cc_cutoff")
    if not pts:
        return dict(got, cutoff=0.5, filter_amount=0.0), True
    want, at, every = filter_line(**filter_vals(got))
    if (same_line(want, pts) and win.loops.get("cc_cutoff") == every and win.sustains.get("cc_cutoff") == at
            and (every is None or win.froms.get("cc_cutoff") == "note") and "cc_cutoff" not in win.fits
            and "cc_cutoff:amount" not in win.fxl):
        return got, True
    v = flat(win, "cc_cutoff")
    if v is not None and not win.loops.get("cc_cutoff"):
        return dict(got, cutoff=v, filter_amount=0.0), True
    return got, False  # (drawn by hand: the knobs stay as they were)


def read_character(win, was):
    """The Character box: ({slant, groups (how many), offpitch, noisy}, made) as read_volume."""
    got, made = {"slant": 0.0, "groups": 1.0, "offpitch": 0.0, "noisy": 0.0}, True
    for name in CHARACTER + ("groups",):
        if name in win.fxl:
            v = flat(win, name)
            made = made and v is not None
            got[name] = v if v is not None else max(p[1] for p in win.fxl[name])
    got["groups"] = float(group_count(got["groups"])) if "groups" in win.fxl else 1.0
    got["height"], got["height_rand"] = 1.0, 0.0
    for name in ("height", "height_rand"):
        if name in win.fxl:
            v = flat(win, name)
            made = made and v is not None
            v = v if v is not None else max(p[1] for p in win.fxl[name])
            got[name] = float(height_times(v)) if name == "height" else v
    got["height_from"] = win.extra.get("height_from") or "middle"
    return got, made


def read_voice(win, was):
    """The Voice box: ({voices, detune, same, blend, random, glide, curve, touching, legato}, True): its own
    settings, not lines (one voice: Detune, Voices on and Blend kept as they were; no glide: Curve and Only notes
    that touch too)."""
    v = win.extra.get("voice") or win.extra.get("bypass", {}).get("voice", {})
    n = v.get("voices", 1)
    got = {"voices": float(n), "detune": v["detune"] if n > 1 else was["detune"],
           "same": bool(v.get("same")) if n > 1 else was["same"],
           "blend": v.get("blend", BLEND) if n > 1 else was["blend"], "random": v.get("random", 0.0),
           "glide": v.get("glide", 0.0),
           "curve": v.get("curve", GLIDE_CURVE) if v.get("glide") else was["curve"],
           "touching": bool(v.get("touching")) if v.get("glide") else was["touching"],
           "legato": bool(v.get("legato"))}
    return got, True


def read_arp(win, was):
    """The Arpeggio box: ({arp_on, arp_pattern, arp_chord, arp_scale, arp_root, arp_speed, arp_octaves, arp_gate,
    arp_swing, arp_steps}, True) (off: its settings kept as they were; no scale: its Root too; not the Steps
    pattern: its steps too)."""
    arp = win.extra.get("arp")
    if not arp:
        return {"arp_on": False}, True
    return {"arp_on": True, "arp_swing": 0.0, "arp_scale": "off", "arp_root": was["arp_root"],
            "arp_steps": was["arp_steps"], "arp_count": was["arp_count"],
            **{f"arp_{k}": v for k, v in arp.items()}}, True


def read_osc2(win, was):
    """The OSC B box: ({osc2_on, osc2_wave, osc2_split, osc2_mode, its knobs and its mode's}, True) (off: its
    settings kept as they were; no mode: the modes' knobs too)."""
    b = win.extra.get("osc2")
    if not b:
        return {"osc2_on": False}, True
    mode = b.get("mode") or {}
    got = {"osc2_on": True, "osc2_wave": b["wave"], "osc2_split": bool(b.get("split")), "osc2_a_off": bool(b.get("a_off")),
           "osc2_mode": mode.get("kind", "off"), **{f"osc2_{k}": b[k] for k in OSC2}}
    for key, _ in MODE_KNOBS.get(got["osc2_mode"], ()):
        got[f"osc2_{key}"] = mode[key.split("_", 1)[1]]
    return got, True


def read_rack(win, was):
    """The Effects tab: {rack: its effects in order, rack_off: those switched off, each one's knobs} (an effect not
    there: its knobs kept as they were)."""
    rack = win.extra.get("rack", ())
    got = {"rack": tuple(e["kind"] for e in rack), "rack_off": tuple(e["kind"] for e in rack if e.get("off"))}
    for e in rack:
        for key, _ in RACK_KNOBS[e["kind"]]:
            got[key] = e[key.split("_", 1)[1]]
    return got


def read_boost(read, name):
    """A box's reading with its CC boost knob (hz["boost"]: kept as it is, whatever the lines are)."""
    def got(win, was):
        vals, made = read(win, was)
        return dict(vals, **{f"{name}_boost": (win.extra.get("boost") or {}).get(name, 0.0)}), made
    return got


READ = {"volume": read_boost(read_volume, "volume"), "wave": read_wave, "pitch": read_pitch, "vibrato": read_vibrato,
        "tremolo": read_boost(read_tremolo, "tremolo"), "tone": read_tone, "filter": read_filter, "character": read_character,
        "voice": read_voice,
        "arp": read_arp, "osc2": read_osc2}


# (the pictures' mixin: it needs the names above, so it is imported here)
from window.hz_knob_pics import SynthKnobPics  # noqa: E402  (moved)


class SynthKnobs(SynthKnobPics):
    """The Knobs tab of SynthWindow (needs its fx pane, fxl / loops / ..., redraw, commit_fx, live, page)."""

    def build_knobs(self, page):
        s = self.s
        self.turning = None  # while a knob is turned: the lines from before (FxPane.state)
        self.vals = dict(START)  # (rack / rack_off: the Effects tab's effects in order, those switched off)
        self.mode_cells = {}  # OSC A's (wave) / B's box -> mode -> its knobs' cells (only the picked mode's shown)
        self.mode_col, self.mode_picks, self.mode_shown = {}, {}, {}  # (... where they start, the Mode dropdowns)
        self.dials, self.dial_vars, self.dial_boxes, self.box_says, self.pics = {}, {}, {}, {}, {}
        self.scrubs = {}  # (each knob's box's Up / Down / drag)
        self.unit_labels, self.timing_picks = {}, {}  # (each knob's unit beside its box; the LFO boxes' Timing)
        self.pic_for = {}  # what each picture was drawn for
        self.box_text = {}  # what each knob's box was last given to show (different = typed there)
        self.dot_at = None  # where the envelope picture's moving dot is drawn
        self.lines = [ttk.Frame(page, style="Synth.TFrame") for _ in BOXES]  # (made before the boxes, so the boxes
        self.boxes, self.laid = {}, None  # show on top of them)
        for name, knobs in BOXES.items():
            outer = self.boxes[name] = Box(page, s, tr(f"hz.synth_{name}"), COLOURS[name])
            Tooltip(outer.lamp, tr({"arp": "hz.synth_tip_arp_on", "osc2": "hz.synth_tip_osc2_on"}.get(
                name, "hz.synth_tip_lamp")))
            if name in BYPASS:
                for w in (outer.lamp, outer.title):
                    w.config(cursor="hand2")
                    w.bind("<ButtonPress-1>", lambda e, name=name: self.bypass_click(name))
            box = outer.body
            col = 0
            if name == "wave":
                self.wave_names = [tr("hz.synth_wave_none")] + [tr("hz.fx_" + n) for n in WAVES]
                self.wave_var, self.wave_pick = self.choice_cell(box, 0, tr("hz.synth_wave_kind"), self.wave_names,
                                                                 lambda i: self.on_wave(), tr("hz.synth_tip_wave"), 9)
                col = 1
            if name == "arp":
                col = self.arp_cells(outer)
            if name == "osc2":
                col = self.osc2_cells(outer)
            if name == "tone":
                cell = ttk.Frame(box, style="Synth.Box.TFrame")
                cell.grid(row=0, column=0, padx=6, sticky="n")
                ttk.Label(cell, text=tr("hz.synth_sweep"), style="Synth.Box.TLabel").pack()
                self.sweep_var = tk.BooleanVar(value=False)
                cb = ttk.Checkbutton(cell, text=tr("hz.synth_sweep_on"), variable=self.sweep_var,
                                     style="Synth.Box.TCheckbutton",
                                     command=lambda: self.change("tone", "sweep", self.sweep_var.get()))
                cb.pack(pady=(14, 0))
                Tooltip(cb, tr("hz.synth_tip_sweep"))
                col = 1
            if name in ("vibrato", "tremolo"):
                self.timing_cell(box, name)
                col = 1
            for key, kind, start in knobs:
                mode = text_key(key).split("_")[0]
                if name in ("wave", "osc2") and mode in MODE_KNOBS:  # (Mode after Octave below / Level, then the
                    if name not in self.mode_cells:  # picked mode's knobs)
                        self.mode_cell(box, col, name)
                        self.mode_col[name] = col + 1
                    at = self.mode_col[name] + [k for k, _ in MODE_KNOBS[mode]].index(text_key(key))
                    cell = self.dial_cell(box, at, key, kind, start, COLOURS[name])
                    cell.grid_remove()
                    self.mode_cells[name].setdefault(mode, []).append(cell)
                    col = max(col, at + 1)
                    continue
                self.dial_cell(box, col, key, kind, start, COLOURS[name])
                col += 1
                if key in ("random", "curve"):  # (Voices on after the copies' knobs, the tick boxes after Glide's)
                    self.voice_cell(box, col, key)
                    col += 1
                if key == "curve":  # (then the CC lines' channels)
                    self.cc_cell(box, col)
                    col += 1
                if key == "height_rand":  # (Height's Grows from after its knobs)
                    self.grows_cell(box, col)
                    col += 1
            if name == "arp":
                for what in ARP_CHOICES[1:]:  # (Chord, Scale, Root after the knobs)
                    self.arp_choice(box, col, what, CHOICES[f"arp_{what}"])
                    col += 1
            size = PICTURES[name]
            pic = self.pics[name] = tk.Canvas(box, width=round(size[0] * s), height=round(size[1] * s),
                                              background=PIC, highlightthickness=1, highlightbackground=EDGE)
            pic.grid(row=1, column=0, columnspan=col, sticky="ew", pady=(8, 0))
            says = self.box_says[name] = ttk.Label(box, text="", style="Synth.Box.Warn.TLabel",
                                                   wraplength=round(size[0] * s))
            says.grid(row=2, column=0, columnspan=col, sticky="w", pady=(6, 0))
            if name == "arp":  # (the Steps pattern's own steps, under the picture: shown while it's picked)
                self.step_edit = StepEditor(box, s, self.app, COLOURS["arp"], self.on_steps)
                self.step_edit.frame.grid(row=3, column=0, columnspan=col, sticky="w", pady=(8, 0))
                self.step_edit.frame.grid_remove()
            pic.bind("<Configure>", lambda e, says=says: (says.config(wraplength=e.width), self.draw_pics()))
        self.lay_boxes(math.inf)  # (as wide as they need: the window's size is set from that; then as wide as it is)

    def lay_boxes(self, room):
        """The boxes in their rows (ROWS); a row too wide for the window goes on in a new row below (user)."""
        laid = []
        for names in ROWS:
            line, used = [], 0
            for name in names:
                w = self.boxes[name].winfo_reqwidth() + 10
                if line and used + w > room:
                    laid.append(line)
                    line, used = [], 0
                line.append(name)
                used += w
            laid.append(line)
        if laid == self.laid:  # (only when it changes: no flashing)
            return
        self.laid = laid
        for f in self.lines + list(self.boxes.values()):  # (all again: each in its row's order)
            f.pack_forget()
        for i, names in enumerate(laid):
            self.lines[i].pack(fill="x", anchor="w", pady=(0, 8) if i < len(laid) - 1 else 0)
            for name in names:
                self.boxes[name].pack(in_=self.lines[i], side="left", anchor="n", padx=(0, 10))

    def voice_cell(self, box, col, after):
        """The Voice box's Voices on dropdown (after Detune) or its Only notes that touch and Legato tick boxes
        (after Glide and its Curve)."""
        if after == "random":
            self.same_names = [tr("hz.synth_voices_split"), tr("hz.synth_voices_same")]
            self.same_var, _ = self.choice_cell(box, col, tr("hz.synth_voices_on"), self.same_names,
                                                lambda i: self.change("voice", "same", i == 1),
                                                tr("hz.synth_tip_voices_on"), 10)
        else:
            cell = ttk.Frame(box, style="Synth.Box.TFrame")
            cell.grid(row=0, column=col, padx=6, sticky="n")
            ttk.Label(cell, text="", style="Synth.Box.TLabel").pack()
            self.touching_var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(cell, text=tr("hz.synth_touching"), variable=self.touching_var,
                                 style="Synth.Box.TCheckbutton",
                                 command=lambda: self.change("voice", "touching", self.touching_var.get()))
            cb.pack(pady=(14, 0), anchor="w")
            Tooltip(cb, tr("hz.synth_tip_touching"))
            self.legato_var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(cell, text=tr("hz.synth_legato"), variable=self.legato_var,
                                 style="Synth.Box.TCheckbutton",
                                 command=lambda: self.change("voice", "legato", self.legato_var.get()))
            cb.pack(pady=(4, 0), anchor="w")
            Tooltip(cb, tr("hz.synth_tip_legato"))

    def cc_cell(self, box, col):
        """The Voice box's CC dropdown (user 2026-10-21): One channel / Own channel per note (hz["cc_own"], the whole
        Hz bass's, every layer: not a sound a preset carries; hz_cc.voices)."""
        self.cc_names = [tr("hz.synth_cc_one"), tr("hz.synth_cc_own")]
        self.cc_var, _ = self.choice_cell(box, col, tr("hz.synth_cc"), self.cc_names,
                                          lambda i: self.set_cc_own(i == 1), tr("hz.synth_tip_cc"), 18)

    def choice_cell(self, box, col, label, names, picked, tip, width):
        """A dropdown with its name (label) over it in column col of a box's top row (names = its items, the first
        shown; picked(the item's place) when one is picked, then the keyboard back to the window; tip = its tip's
        text): (its StringVar, it)."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6, sticky="n")
        ttk.Label(cell, text=label, style="Synth.Box.TLabel").pack()
        var = tk.StringVar(value=names[0])
        cb = ttk.Combobox(cell, textvariable=var, values=names, state="readonly", width=width, style="Synth.TCombobox")
        dark_list(cb)
        cb.pack(pady=(12, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: (picked(e.widget.current()), self.keyboard_back(e.widget)))
        Tooltip(cb, tip)
        return var, cb

    def set_cc_own(self, on):
        """The CC dropdown picked: one undo step (none when it's as it was)."""
        self.hz.put_lengths(tr("hz.step_cc_own"), cc_own=True if on else None)

    def dial_cell(self, box, col, key, kind, start, colour):
        """A knob with its name over it and its value's box under it."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6)
        ttk.Label(cell, text=tr(f"hz.synth_{text_key(key)}"), style="Synth.Box.TLabel").pack()
        changed = lambda v, done: self.on_dial(key, v, done)
        if kind in UPDOWN:  # (up or down: 0 in the middle)
            k = UpDown(cell, self.s, changed, colour, size=46, start=knob_of(kind, start))
        else:
            k = Dial(cell, self.s, changed, colour, size=46, start=knob_of(kind, start))
        self.dials[key] = k
        k.pack()
        row = ttk.Frame(cell, style="Synth.Box.TFrame")
        row.pack(pady=(2, 0))
        var = self.dial_vars[key] = tk.StringVar()
        e = self.dial_boxes[key] = ttk.Entry(row, textvariable=var, width=5, justify="center", style=ENTRY)
        e.pack(side="left")
        unit, lo, hi, steps, _ = KINDS[kind]
        if unit:
            self.unit_labels[key] = ttk.Label(row, text=tr(unit), style="Synth.Box.Dim.TLabel")
            if unit in PLURAL_UNITS:  # ("1 beat" while the box holds 1; others like "a beat" keep their text)
                unit_word(self.unit_labels[key], var, unit)
            self.unit_labels[key].pack(side="left", padx=(2, 0))
        e.bind("<Return>", lambda ev: (self.on_box(key), self.keyboard_back(e), "break")[2])
        e.bind("<FocusOut>", lambda ev: self.on_box(key))
        self.scrubs[key] = Scrub(self.app, [(e, var, lambda: self.on_box(key, stepped=True))], steps, lo, hi,
                                 drag_box=True)
        tip = tr(f"hz.synth_tip_{text_key(key)}")
        if text_key(key) != key:  # (OSC B's Mode knobs)
            tip = tr("hz.synth_tip_osc2_mode") + "\n" + tip
        for w in (k, e):
            Tooltip(w, tip + "\n" + tr("hz.synth_tip_knob"))
        return cell

    def grows_cell(self, box, col):
        """The Character box's Grows from dropdown: the key row that stays put while Height changes."""
        def make():
            return [tr(f"hz.synth_height_{w}") for w in HEIGHT_FROM]
        names = make()
        var, _ = self.choice_cell(box, col, tr("hz.fx_height_from"), names,
                                  lambda i: self.on_grows(tuple(HEIGHT_FROM)[i]), tr("hz.synth_tip_height_from"),
                                  english_width(make))
        self.grows_pick = (var, names)

    def on_grows(self, where):
        """Grows from picked: only that setting changes, one undo step (the box's lines stay as drawn, user)."""
        before = self.fx.state()
        self.vals["height_from"] = where
        self.set_extra("height_from", where)
        self.keep_vals()
        self.redraw()
        self.show_knobs()
        if self.fx.now() != before:
            self.commit_fx(before)

    def timing_cell(self, box, name):
        """An LFO box's Timing dropdown (column 0): Free, or its Rate only at note lengths (straight, triplets,
        dotted)."""
        def make():
            return [tr(f"hz.synth_timing_{t}") for t in TIMINGS]
        names = make()
        var, _ = self.choice_cell(box, 0, tr("hz.synth_timing"), names, lambda i: self.on_timing(name, TIMINGS[i]),
                                  tr("hz.synth_tip_timing"), english_width(make))
        self.timing_picks[name] = (var, names)

    def on_timing(self, name, timing):
        """An LFO box's Timing picked: its Rate moved to the nearest note length of it, one undo step. Only the
        rate changes: a line drawn by hand stays (the Vibrato's rate isn't in its line; the Tremolo's line is
        written again only when its rate moved)."""
        if timing == self.vals[f"{name}_timing"]:
            return
        before = self.fx.state()
        key = f"{name}_rate"
        rate = snap_rate(self.vals[key], timing, KINDS[KNOBS[key][1]][2])
        moved = abs(rate - self.vals[key]) > 1e-9
        self.vals[f"{name}_timing"], self.vals[key] = timing, rate
        linked = key in self.linked()  # (a macro adds to it: the sum snapped to the timing too, hunt)
        if name == "tremolo" and (moved or linked):
            self.write(name)
        else:
            self.keep_bases()
            if name == "vibrato":
                self.set_lfo("vibrato_rate", self.macro_vals()[key], VIBRATO_RATE)
            self.set_timing(f"{name}_timing", timing)
            self.keep_vals()
            self.redraw()
            self.show_knobs()
        if self.fx.now() != before:
            self.commit_fx(before)

    def mode_cell(self, box, col, name):
        """OSC A's (the Wave box's) or OSC B's Mode dropdown (Off, FM, Pulse width, Sync, Growl, Bitcrush)."""
        names = [tr(f"hz.synth_mode_{m}") for m in MODE_NAMES]
        key = "mode" if name == "wave" else "osc2_mode"
        var, _ = self.choice_cell(box, col, tr("hz.synth_mode"), names,
                                  lambda i: (self.sweep_on(key), self.change(name, key, MODE_NAMES[i])),
                                  (tr("hz.synth_tip_osc2_mode") + "\n" if name == "osc2" else "") + tr("hz.synth_tip_mode"),
                                  11)
        self.mode_cells[name] = {}
        self.mode_picks[name] = (var, names, key)

    def osc2_cells(self, outer):
        """The OSC B box's On (the light on its header, or its name: a click switches it) and, in column 0, its
        Waveform and Plays on dropdowns: the next free column."""
        def switch(e):
            self.change("osc2", "osc2_on", not self.vals["osc2_on"])
        for w in (outer.lamp, outer.title):
            w.config(cursor="hand2")
            w.bind("<ButtonPress-1>", switch)
        cell = ttk.Frame(outer.body, style="Synth.Box.TFrame")
        cell.grid(row=0, column=0, padx=6, sticky="n")
        picks = (("wave_kind", "osc2_wave", self.wave_names, WAVE_NAMES, "hz.synth_tip_osc2_wave"),
                 ("osc2_plays", "osc2_split", [tr("hz.synth_osc2_every"), tr("hz.synth_osc2_split")], (False, True),
                  "hz.synth_tip_osc2_plays"))
        for i, (label, key, names, ids, tip) in enumerate(picks):
            ttk.Label(cell, text=tr(f"hz.synth_{label}"), style="Synth.Box.TLabel").pack(pady=(4 if i else 0, 0))
            var = tk.StringVar(value=names[0])
            cb = ttk.Combobox(cell, textvariable=var, values=names, state="readonly", width=9, style="Synth.TCombobox")
            dark_list(cb)
            cb.pack(pady=(2, 0))
            cb.bind("<<ComboboxSelected>>", lambda e, key=key, names=names, ids=ids, var=var: (
                self.sweep_on(key), self.change("osc2", key, ids[e.widget.current()]),
                self.keyboard_back(e.widget)))
            Tooltip(cb, tr(tip))
            setattr(self, f"{key}_pick", (var, names, ids))
        return 1

    # ------------------------------------------------------------ changes

    def on_dial(self, key, k, done):
        """A knob turned (done: let go / one step of the wheel or the keys = one undo step)."""
        if self.turning is None:
            self.turning, self.turn_vals = self.fx.state(), dict(self.vals)
        self.vals[key] = self.whole_step(key, self.timed(key, value_of(KNOBS[key][1], k), self.dials[key].stepping))
        self.sweep_on(key)
        self.write(written(key))
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

    def on_steps(self, steps, count, done):
        """The Arpeggio's steps (all of them) and how many play set in the step editor (done: let go / one click =
        one undo step); it puts the arpeggio on."""
        if self.turning is None:  # (from the press on: held, so Ctrl+Z calls the drag off, hunt)
            self.turning, self.turn_vals = self.fx.state(), dict(self.vals)
        if steps != self.vals["arp_steps"] or count != self.vals["arp_count"]:
            self.vals["arp_steps"], self.vals["arp_count"] = [dict(s) for s in steps], count
            self.sweep_on("arp_")
            self.write("arp")
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

    def timed(self, key, v, step=0):
        """A Rate knob's value at its box's Timing (the nearest note length; Free: as it is), others as they are.
        step = a wheel / arrow / box step's way (+ / -): at least one note length that way, as a synced Rate moves."""
        timing = self.vals.get(TIMED.get(key), "free")
        if key not in TIMED or timing == "free":
            return v
        hi = KINDS[KNOBS[key][1]][2]
        got, now = snap_rate(v, timing, hi), self.vals[key]
        if step and abs(got - now) < 1e-9:
            rates = timed_rates(timing, hi)
            if step > 0:
                got = next((r for r in rates if r > now + 1e-9), now)
            else:
                got = next((r for r in reversed(rates) if r < now - 1e-9), now)
        return got

    def whole_step(self, key, v):
        """A wheel / arrow step on a knob of whole numbers (Octave, Semi, Groups, Voices...): at least one whole
        number that way (a step of the knob's turn alone can round back to where it was)."""
        step, kind = self.dials[key].stepping, KNOBS[key][1]
        if not step or kind not in COUNTS + ("keys", "pitch") or abs(v - self.vals[key]) > 1e-9:
            return v
        lo, hi = KINDS[kind][1:3]
        return float(min(hi, max(lo, self.vals[key] + (1 if step > 0 else -1))))

    def keyboard_back(self, w):
        """A wave picked / a value typed with Enter: the keyboard back to the window (no blue box left), so the
        letters play the keys again (user)."""
        w.selection_clear()
        self.focus_set()

    def cancel_turn(self):
        """Ctrl+Z while a knob is held: it goes back to where it was at the press, no undo step (the mouse still
        held turns nothing)."""
        hz = self.hz
        hz.fxl, hz.loops, hz.off, hz.froms, hz.fits, hz.sustains, hz.lfo, hz.extra = self.turning
        self.vals, self.turning = self.turn_vals, None
        for dial in list(self.dials.values()) + self.macro_dials:
            dial.drag, dial.pointing, dial.ring_held = None, False, None
        self.step_edit.drag = None
        self.macro_drop()
        self.redraw()
        self.show_knobs()
        return True

    def on_box(self, key, stepped=False):
        """A value typed (or stepped: Up / Down, the wheel, a drag) in the box under a knob."""
        e, var = self.dial_boxes[key], self.dial_vars[key]
        box, kind, _ = KNOBS[key]
        lo, hi = KINDS[kind][1:3]
        if var.get() == self.box_text.get(key):  # (nothing typed: the box shows the sound as it is)
            return e.config(style=ENTRY)
        v = read_number(var.get(), lo, hi)
        if v is None:  # (not a number, or out of range: back to the last good value, user)
            var.set(self.box_text.get(key, ""))
            e.config(style=ENTRY)
            return
        e.config(style=ENTRY)
        self.box_text[key] = var.get()  # (taken: from now on the box shows the sound again)
        v = v / 100 if kind in PERCENTS else float(round(v)) if kind in COUNTS else v
        v = self.timed(key, v, (v > self.vals[key]) - (v < self.vals[key]) if stepped else 0)
        if abs(v - self.vals[key]) > 1e-9:
            self.sweep_on(key)
            self.change(written(key), key, v)
        else:  # (as the knob has it: rounded to whole groups)
            var.set(fmt(shown(kind, v)))
            self.box_text[key] = var.get()

    def sweep_on(self, key):
        """Turning one of Sweep's knobs puts it on (its On box ticked); the Arpeggio's and OSC B's the same."""
        if key.startswith("sweep_"):
            self.vals["sweep"] = True
        if key.startswith("arp_"):
            self.vals["arp_on"] = True
        if key.startswith("osc2_"):
            self.vals["osc2_on"] = True

    def arp_cells(self, outer):
        """The Arpeggio box's On (the light on its header, or its name: a click switches it) and Pattern dropdown
        (column 0): the next free column."""
        self.arp_var = tk.BooleanVar(value=False)

        def switch(e):
            self.arp_var.set(not self.arp_var.get())
            self.change("arp", "arp_on", self.arp_var.get())
        for w in (outer.lamp, outer.title):
            w.config(cursor="hand2")
            w.bind("<ButtonPress-1>", switch)
        self.arp_choice(outer.body, 0, "pattern", ARP_PATTERNS)
        return 1

    def arp_choice(self, box, col, what, ids):
        """One of the Arpeggio box's dropdowns (Pattern / Chord / Scale / Root): picking one puts the arpeggio on."""
        def make():
            return ([NOTE_NAMES[i] for i in ids] if what == "root"  # (C, C#...: as on the piano roll's keys)
                    else [tr(f"hz.synth_arp_{what}_{i}") for i in ids])
        names = make()
        var, _ = self.choice_cell(box, col, tr(f"hz.synth_arp_{what}"), names,
                                  lambda i: (self.sweep_on("arp_"), self.change("arp", f"arp_{what}", ids[i])),
                                  tr(f"hz.synth_tip_arp_{what}"), english_width(make))
        setattr(self, f"arp_{what}_pick", (var, names, ids))

    def on_wave(self):
        self.change("wave", "wave", WAVE_NAMES[self.wave_pick.current()])  # (the place: names alike stay apart)

    def change(self, box, key, value):
        """One knob's value set: one undo step."""
        before = self.fx.state()
        self.vals[key] = value
        self.write(box)
        if self.fx.now() != before:
            self.commit_fx(before)

    def write(self, box, show=True):
        """The lines made from a box's knobs (a box switched off stays off: its knobs turn while it's silent), with
        what the macros add to them; show=False: nothing shown yet (more boxes coming: a macro turned)."""
        self.keep_bases()
        v, fx = self.macro_vals(), self.fx
        was_off = box in BYPASS and self.box_off(box)
        if was_off and box in BOX_EXTRA:  # (its kept setting is written anew below)
            self.set_extra("bypass", {k: s for k, s in self.extra.get("bypass", {}).items() if k != BOX_EXTRA[box]})
        if box == "volume":
            pts, at, every = adsr_line(v["attack"], v["decay"], v["sustain"], v["release"])
            fx.drop("volume")
            self.fxl["volume"] = pts
            self.loops["volume"], self.froms["volume"], self.sustains["volume"] = every, "note", at
            self.set_boost("volume", v["volume_boost"])
        elif box == "wave":
            for name in WAVES:
                fx.drop(name)
            if v["wave"] != "none":
                self.fxl[v["wave"]] = [[0.0, v["shape"]]]
            fx.drop("octave")
            if v["octave"] > 0:
                self.fxl["octave"] = [[0.0, v["octave"]]]
            m = v["mode"]
            self.set_extra("mode", {"kind": m, **{key.split("_", 1)[1]: v[key] for key, _ in MODE_KNOBS.get(m, ())}})
        elif box == "pitch":
            fx.drop("pitch")
            self.set_range(v["bend_range"])
            if v["amount"] and v["time"] > 0:
                pts, every = pitch_line(v["amount"], v["time"], v["bend_range"])
                self.fxl["pitch"] = pts
                self.loops["pitch"], self.froms["pitch"] = every, "note"
        elif box == "bend_range":  # (only the Range: the Pitch line stays, bending that much further, as a synth's)
            self.set_range(v["bend_range"])
        elif box == "vibrato":
            fx.drop("vibrato")
            if v["vibrato_depth"] > 0:
                pts, every = vibrato_line(v["vibrato_depth"], v["vibrato_delay"], v["vibrato_wait"])
                self.fxl["vibrato"] = pts
                if every:
                    self.loops["vibrato"], self.froms["vibrato"] = every, "note"
            self.set_lfo("vibrato_rate", v["vibrato_rate"], VIBRATO_RATE)
            self.set_timing("vibrato_timing", v["vibrato_timing"])
        elif box == "voice":
            self.set_extra("voice", {"voices": int(v["voices"]), "detune": v["detune"], "same": v["same"],
                                      "blend": v["blend"], "random": v["random"], "glide": v["glide"],
                                      "curve": v["curve"], "touching": v["touching"], "legato": v["legato"]})
        elif box == "tremolo":
            fx.drop("tremolo")
            if v["tremolo_rate"] > 0:
                self.fxl["tremolo"] = [[0.0, min(1.0, v["tremolo_rate"] / TREMOLO)]]
            self.set_lfo("tremolo_depth", v["tremolo_depth"], TREMOLO_DEPTH)
            self.set_lfo("tremolo_wait", v["tremolo_wait"], 0.0)
            self.set_lfo("tremolo_rise", v["tremolo_rise"], 0.0)
            self.set_timing("tremolo_timing", v["tremolo_timing"])
            self.set_boost("tremolo", v["tremolo_boost"])
        elif box == "tone":
            fx.drop("sweep")
            if v["sweep"]:
                pts, every = sweep_line(v["sweep_start"], v["sweep_end"], v["sweep_time"])
                self.fxl["sweep"] = pts
                if every:
                    self.loops["sweep"], self.froms["sweep"] = every, "note"
            self.set_lfo("sweep_track", v["sweep_track"] if v["sweep"] else 0.0, 0.0)  # (off: kept in hz["kept"])
            fx.drop("wah")
            if v["wah"] > 0:
                self.fxl["wah"] = [[0.0, v["wah"]]]
        elif box == "filter":
            fx.drop("cc_cutoff")
            pts, at, every = filter_line(**filter_vals(v))
            if every is not None:
                self.fxl["cc_cutoff"] = pts
                self.loops["cc_cutoff"], self.froms["cc_cutoff"], self.sustains["cc_cutoff"] = every, "note", at
            elif abs(pts[0][1] - 0.5) > 1e-9:  # (the middle all along: no CC sent)
                self.fxl["cc_cutoff"] = pts
            # (the knobs kept with it: the MOD tab moves Amount / the envelope from them, hz_modulate.knob_filter)
            self.set_extra("filter", filter_vals(v) if "cc_cutoff" in self.fxl else None)
        elif box == "arp":
            self.set_extra("arp", {k: v[f"arp_{k}"] for k in ARP_KEYS} if v["arp_on"] else None)
        elif box == "osc2":
            m = v["osc2_mode"]
            self.set_extra("osc2", {"wave": v["osc2_wave"], "split": v["osc2_split"], "a_off": v["osc2_a_off"],
                                    **{k: v[f"osc2_{k}"] for k in OSC2},
                                    "mode": {"kind": m, **{key.split("_", 1)[1]: v[f"osc2_{key}"]
                                                           for key, _ in MODE_KNOBS.get(m, ())}}}
                           if v["osc2_on"] else None)
        elif box in RACK:
            self.write_rack()
        else:
            for name in CHARACTER:
                fx.drop(name)
                if v[name] > 0:
                    self.fxl[name] = [[0.0, v[name]]]
            fx.drop("groups")
            if v["groups"] > 1:
                self.fxl["groups"] = [[0.0, (v["groups"] - 1) / (GROUPS - 1)]]
            fx.drop("height")
            if abs(v["height"] - 1.0) > 1e-9:  # (100 %: as drawn, no line)
                self.fxl["height"] = [[0.0, height_value(v["height"])]]
            fx.drop("height_rand")
            if v["height_rand"] > 0:
                self.fxl["height_rand"] = [[0.0, v["height_rand"]]]
            self.set_extra("height_from", v["height_from"])
        if was_off:
            self.switch_box(box, True)
        elif box in self.extra.get("bypass", {}).get("boxes", ()):  # (switched on elsewhere, e.g. its lines on the
            kept = dict(self.extra["bypass"])  # Lines tab: no longer named as off)
            kept["boxes"] = [b for b in kept["boxes"] if b != box]
            self.set_extra("bypass", kept)
        if show:
            self.keep_vals()
            self.redraw()
            self.show_knobs()

    def keep_vals(self):
        """The knobs that do nothing right now (the sound as it is doesn't show them) kept in hz["kept"], so they're
        there again after the window is closed, in a saved project and a preset (user: as in a synth)."""
        got = dict(START)
        for read in READ.values():
            got.update(read(self, START)[0])
        got.update(read_rack(self, START))
        v, linked = self.vals, self.linked()  # (a knob a macro moves: its own value is in hz["macro"])
        self.set_extra("kept", {k: v[k] for k in KEEP if k not in linked and v[k] != got[k]
                                and not (isinstance(v[k], float) and abs(v[k] - got[k]) < 1e-9)})

    def kept_vals(self):
        """Every knob and choice as the sound keeps them: where it starts, or as kept in hz["kept"]."""
        out = dict(START)
        for k, v in self.extra.get("kept", {}).items():
            if k in KEEP and kept_value(k, v) is not None:
                out[k] = kept_value(k, v)
        return out

    def box_parts(self, name):
        """A box's lines that are there (the Volume line full all along doesn't count) and its own setting's name."""
        lines = [n for n in BOX_LINES.get(name, ()) if n in self.fxl]
        if lines == ["volume"] and flat(self, "volume") == 1.0:
            lines = []
        return lines, BOX_EXTRA.get(name)

    def box_linked(self, name):
        """A MOD tab source moves one of the box's effects (it changes the sound, even with its knob at 0)."""
        return any(MOD_BOXES[link["to"]] == name for link in (self.extra.get("mod") or {}).get("links", ())
                   if link["to"] in MOD_BOXES  # (a knob that makes no line: its box has its setting already)
                   and (link["to"] not in MOD_NEED_LINE or link["to"] in self.fxl)
                   or link["to"] == "wave" and any(w in self.fxl for w in WAVES))

    def box_off(self, name):
        """The box is switched off (Bypass): all it has is off, or it has nothing and was switched off (it stays off
        while its knobs do nothing, user)."""
        lines, extra = self.box_parts(name)
        if extra in self.extra or any(n not in self.off for n in lines):
            return False
        kept = self.extra.get("bypass", {})
        return bool(lines) or extra in kept or name in kept.get("boxes", ())

    def switch_box(self, name, off):
        """A box switched off / on: its lines in / out of hz["off"], its own setting moved to / from hz["bypass"],
        its name in / out of the boxes switched off there."""
        lines, extra = self.box_parts(name)
        self.off = [n for n in self.off if n not in lines] + (lines if off else [])
        now, kept = dict(self.extra), dict(self.extra.get("bypass", {}))
        if extra and off and extra in now:
            kept[extra] = now.pop(extra)
        elif extra and not off and extra in kept:
            now[extra] = kept.pop(extra)
        kept["boxes"] = [b for b in kept.get("boxes", ()) if b != name] + ([name] if off else [])
        now["bypass"] = kept
        self.extra = clean_extra(now)

    def bypass_click(self, name):
        """A box's light or name clicked: the box switched off (kept, silent) or back on, one undo step; a box that
        does nothing has nothing to switch (a ding). OSC A's while OSC B is on: OSC A's notes off / on (as a synth's
        oscillator switch)."""
        if name == "wave" and self.vals["osc2_on"]:
            return self.change("osc2", "osc2_a_off", not self.vals["osc2_a_off"])
        off = self.box_off(name)
        lines, extra = self.box_parts(name)
        if not off and not lines and extra not in self.extra and not self.box_linked(name):
            return self.bell()
        before = self.fx.state()
        self.switch_box(name, not off)
        self.redraw()
        self.show_knobs()
        if self.fx.now() != before:
            self.commit_fx(before)

    def set_extra(self, name, value):
        """One of the Hz bass's own settings (hzbass.EXTRAS) set, checked (left out when it does nothing)."""
        extra = dict(self.extra)
        extra[name] = value
        self.extra = clean_extra(extra)

    def set_boost(self, name, value):
        """A box's CC boost knob into hz["boost"] (0 = left out)."""
        self.set_extra("boost", dict(self.extra.get("boost") or {}, **{name: value}))

    def set_lfo(self, key, value, plain):
        """A setting of hz["lfo"] (left out when it's what the Hz bass does without one)."""
        if abs(value - plain) < 1e-9:
            self.lfo.pop(key, None)
        else:
            self.lfo[key] = value

    def set_range(self, keys):
        """The Pitch box's Range in hz["lfo"] (left out at PITCH), the Amount knob and its box turning that far."""
        self.set_lfo("bend_range", keys, PITCH)
        self.show_range()

    def show_range(self):
        """The Amount knob and its box turn as far as the sound's Range."""
        keys = self.lfo.get("bend_range", PITCH)
        set_range(keys)
        self.scrubs["amount"].lo, self.scrubs["amount"].hi = -keys, keys

    def set_timing(self, key, timing):
        """An LFO box's Timing in hz["lfo"] (left out while Free)."""
        if timing == "free":
            self.lfo.pop(key, None)
        else:
            self.lfo[key] = timing

    # ------------------------------------------------------------ showing them

    def show_knobs(self):
        """The knobs, their boxes and the pictures show the lines (not while a knob is turned: it shows what's
        turned)."""
        self.show_range()
        if self.turning is None:
            was = self.kept_vals()  # (the knobs the sound doesn't show: as kept, else where they start)
            was.update(self.bases())  # (... and those a macro moves: their own values, e.g. Pitch Time at 0)
            self.vals.update({k: was[k] for k in KEEP})
            for box, read in READ.items():
                got, made = read(self, was)
                self.vals.update(got)
                says = "" if made else tr("hz.synth_drawn")
                if self.box_says[box].cget("text") != says:
                    self.box_says[box].config(text=says)
            self.vals.update(read_rack(self, was))
            self.vals.update(self.bases_read(self.vals))  # (a knob a macro moves shows its own value, the lines
            # have the sum)
        for key, (box, kind, _) in KNOBS.items():
            v = self.vals[key]
            k = knob_of(kind, v)
            if not self.dials[key].drag and abs(self.dials[key].value - k) > 0.05:
                self.dials[key].set(k)
            text = fmt(shown(kind, v))
            e, var = self.dial_boxes[key], self.dial_vars[key]
            typing = self.focus_get() is e and var.get() != self.box_text.get(key)  # (left as typed)
            if var.get() != text and not typing:
                var.set(text)
                e.config(style=ENTRY)
            if not typing:
                self.box_text[key] = text
        name = self.wave_names[WAVE_NAMES.index(self.vals["wave"])]
        if self.wave_var.get() != name:
            self.wave_var.set(name)
        if self.sweep_var.get() != self.vals["sweep"]:
            self.sweep_var.set(self.vals["sweep"])
        if self.arp_var.get() != self.vals["arp_on"]:
            self.arp_var.set(self.vals["arp_on"])
        for what in ARP_CHOICES:
            var, names, ids = getattr(self, f"arp_{what}_pick")
            name = names[ids.index(self.vals[f"arp_{what}"])]
            if var.get() != name:
                var.set(name)
        frame = self.step_edit.frame
        if bool(frame.winfo_manager()) != (self.vals["arp_pattern"] == "steps"):  # (the box changes height)
            grid_shown(frame, self.vals["arp_pattern"] == "steps")
            self.after_idle(self.fit_knobs)
        self.step_edit.show(self.vals["arp_steps"], self.vals["arp_count"], self.vals["arp_on"])
        for box, (var, names, key) in self.mode_picks.items():
            mode = self.vals[key]
            if var.get() != names[MODE_NAMES.index(mode)]:
                var.set(names[MODE_NAMES.index(mode)])
            if self.mode_shown.get(box) != mode:  # (the box changes width: rows laid again)
                self.mode_shown[box] = mode
                for m, cells in self.mode_cells[box].items():
                    for cell in cells:
                        grid_shown(cell, m == mode)
                self.after_idle(self.fit_knobs)
        for key in ("osc2_wave", "osc2_split"):
            var, names, ids = getattr(self, f"{key}_pick")
            if var.get() != names[ids.index(self.vals[key])]:
                var.set(names[ids.index(self.vals[key])])
        var, names = self.grows_pick
        name = names[tuple(HEIGHT_FROM).index(self.vals["height_from"])]
        if var.get() != name:
            var.set(name)
        for box, (var, names) in self.timing_picks.items():
            timing = self.vals[f"{box}_timing"]
            if var.get() != names[TIMINGS.index(timing)]:
                var.set(names[TIMINGS.index(timing)])
            note = note_name(self.vals[f"{box}_rate"], timing)  # (beside the Rate's box: its note length)
            text = tr("hz.synth_a_beat_note", note=note) if note else tr("hz.synth_a_beat")
            if self.unit_labels[f"{box}_rate"].cget("text") != text:
                self.unit_labels[f"{box}_rate"].config(text=text)
        name = self.same_names[int(self.vals["same"])]
        if self.same_var.get() != name:
            self.same_var.set(name)
        if self.touching_var.get() != self.vals["touching"]:
            self.touching_var.set(self.vals["touching"])
        if self.legato_var.get() != self.vals["legato"]:
            self.legato_var.set(self.vals["legato"])
        sh = self.hz.target()
        name = self.cc_names[int(bool(((sh or {}).get("hz") or {}).get("cc_own")))]
        if self.cc_var.get() != name:
            self.cc_var.set(name)
        self.light_boxes()
        self.draw_pics()
        self.show_macros()
        self.show_rack()
        self.show_preset()
        self.meter_later()

    def light_boxes(self):
        """Each box's header light: lit while the box changes the sound (Arpeggio: while it's on); a box switched off
        has a grey name."""
        for name, outer in self.boxes.items():
            lines = [n for n in BOX_LINES.get(name, ()) if n in self.fxl and n not in self.off]
            if lines == ["volume"] and flat(self, "volume") == 1.0:  # (the Volume line full all along)
                lines = []
            lit = (bool(lines) or BOX_EXTRA.get(name) in self.extra
                   or self.box_linked(name) and not (name in BYPASS and self.box_off(name)))
            if name == "wave" and self.vals["osc2_on"]:  # (OSC A's light = its notes on while OSC B is on)
                lit = not self.vals["osc2_a_off"]
            outer.lamp.light(lit)
            grey = name in BYPASS and self.box_off(name) or name == "wave" and self.a_silent()
            colour = DIM if grey else COLOURS[name]
            if outer.title.cget("foreground") != colour:
                outer.title.config(foreground=colour)

    def a_silent(self):
        """OSC A's notes switched off (OSC B on, alone)."""
        return self.vals["osc2_on"] and self.vals["osc2_a_off"]

    def pic_colour(self, name):
        """A box's colour in its picture: grey while the box is switched off."""
        return MID if name in BYPASS and self.box_off(name) else COLOURS[name]
