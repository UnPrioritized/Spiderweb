"""The synth window's Knobs tab (window/hz_synth.py): boxes of knobs, like a synth's (user), each writing the effects'
lines the Hz bass already has, so everything they do is in the MIDI. A line drawn by hand that the knobs can't have
made shows them about where it is (with a note); turning one makes new lines from the knobs.
  Volume: Attack, Decay, Sustain, Release = the Volume line once per note, with its sustain point and fall.
  Wave: one waveform (or the plain tone) at a Shape amount, and Octave below: those lines, flat; Mode (FM, Pulse width,
  Sync, Growl, Bitcrush) and its knobs (only the picked mode's shown) = hz["mode"] (not lines).
  Pitch: Amount (keys) and Time = the Pitch line once per note, from Amount keys off to the note's tone.
  Vibrato (LFO 1): Rate (hz["lfo"]), Depth = the Vibrato line, Delay = it comes in over that long, once per note.
  Tremolo (LFO 2): Rate = the Tremolo line (its value is how fast), Depth (hz["lfo"]).
  Tone: Sweep (on / off) from Start to End in Time = the Sweep line once per note; Wah = its line, flat.
  Character: Slant, Groups, Off pitch, Noisy = their lines, flat.
  Voice: Voices, Detune, Voices on (split / same keys), Glide, Only notes that touch, Legato = hz["voice"] (not
  lines).
  Arpeggio (third row): On, Pattern, Speed, Octaves, Gate, Chord = hz["arp"] (not lines; there only while on).
Each box has a picture: the envelope (with a dot while a key sounds), one wave's hits (the notes), the pitch, the
wobbles over two beats, which keys are loud over time, the keys' notes over four waves, the copies' tones and a glide."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.hzbass import (ARP, ARP_PATTERNS, CHORDS, CRUSH, DETUNE, FAST, GROUPS, GROWL, LOOP, MODES, OFF_PITCH, PITCH,
                          RACK, SOFT, SUB, TREMOLO, TREMOLO_DEPTH, VIBRATO_RATE, VOICES, WAH, WAVES, arpeggiated,
                          clean_arp, clean_extra, clean_mode, clean_voice, copies, group_count, line_at, wave_hits)
from window.hz_effects import AMOUNT, FX_COLOR
from window.synth_look import (DIM, EDGE, ENTRY, GRID, MID, PANEL, PIC, TEXT, Box, bright, dark_list, mix)
from window.tool_window import Knob
from window.widgets import Scrub, Tooltip, grid_shown

KNOB_BODY, KNOB_RIM, KNOB_TRACK, KNOB_FOCUS = "#474d57", "#5c636e", "#1a1d22", "#9aa3ae"
TIME_KNOB = 4.0  # beats a time knob goes to (along a curve: fine near 0)
TIME_MOST = 64.0  # ... and a typed one
# what a knob's value is: (unit text (None: no unit), lowest, highest typed, the box's steps (Shift, Ctrl), where the
# knob goes to along a curve (fine near 0; None: straight, percent / keys / groups))
KINDS = {"time": ("hz.synth_beats", 0.0, TIME_MOST, (0.05, 0.25, 0.01), TIME_KNOB),
         "percent": ("hz.synth_percent", 0.0, 100.0, (1, 10, 0.1), None),
         "keys": ("hz.synth_keys", -PITCH, PITCH, (1, 3, 0.1), None),
         "vib_rate": ("hz.synth_a_beat", 0.0, 64.0, (0.1, 1, 0.01), 10.0),
         "trem_rate": ("hz.synth_a_beat", 0.0, TREMOLO, (0.1, 1, 0.01), TREMOLO),
         "groups": (None, 1.0, float(GROUPS), (1, 1, 1), None),
         "voices": (None, 1.0, float(VOICES), (1, 1, 1), None),
         "cents": ("hz.synth_cents", 0.0, DETUNE, (1, 10, 0.1), None),
         "width": ("hz.synth_percent", 100 * MODES["pulse"]["width"][0], 100 * MODES["pulse"]["width"][1],
                   (1, 10, 0.1), None),
         "ratio": ("hz.synth_times", MODES["fm"]["ratio"][0], MODES["fm"]["ratio"][1], (0.25, 1, 0.01),
                   MODES["fm"]["ratio"][1]),
         "sync": ("hz.synth_times", MODES["sync"]["amount"][0], MODES["sync"]["amount"][1], (0.1, 1, 0.01), None),
         "every": (None, MODES["growl"]["every"][0], MODES["growl"]["every"][1], (1, 1, 1), None),
         "repeats": (None, RACK["echo"]["repeats"][0], RACK["echo"]["repeats"][1], (1, 1, 1), None),
         "speed": ("hz.synth_a_beat", ARP["speed"][0], ARP["speed"][1], (0.25, 1, 0.01), ARP["speed"][1]),
         "octaves": (None, ARP["octaves"][0], ARP["octaves"][1], (1, 1, 1), None),
         "gate": ("hz.synth_percent", 100 * ARP["gate"][0], 100 * ARP["gate"][1], (1, 10, 0.1), None)}
PERCENTS = ("percent", "width", "gate")  # (kept 0..1, shown and typed in %)
COUNTS = ("groups", "voices", "every", "repeats", "octaves")  # (whole numbers)
# the Wave box's modes (hzbass.MODES): their knobs (knob = mode_setting) and kinds; only the picked mode's are shown
MODE_KNOBS = {"fm": (("fm_depth", "percent"), ("fm_ratio", "ratio"), ("fm_time", "time")),
              "pulse": (("pulse_width", "width"), ("pulse_rate", "vib_rate")),
              "sync": (("sync_amount", "sync"), ("sync_time", "time")),
              "growl": (("growl_amount", "percent"), ("growl_every", "every")),
              "crush": (("crush_amount", "percent"),)}
MODE_NAMES = ("off",) + tuple(MODE_KNOBS)
# the boxes and their knobs: (knob, kind, value at the start / a middle-click); rows of boxes
BOXES = {"volume": (("attack", "time", 0.0), ("decay", "time", 0.0), ("sustain", "percent", 1.0),
                    ("release", "time", 0.0)),
         "wave": (("shape", "percent", 1.0), ("octave", "percent", 0.0))
         + tuple((key, kind, MODES[m][key.split("_", 1)[1]][2]) for m, knobs in MODE_KNOBS.items() for key, kind in knobs),
         "pitch": (("amount", "keys", 0.0), ("time", "time", 0.25)),
         "vibrato": (("vibrato_rate", "vib_rate", VIBRATO_RATE), ("vibrato_depth", "percent", 0.0),
                     ("vibrato_delay", "time", 0.0)),
         "tremolo": (("tremolo_rate", "trem_rate", 0.0), ("tremolo_depth", "percent", TREMOLO_DEPTH)),
         "tone": (("sweep_start", "percent", 1.0), ("sweep_end", "percent", 0.0), ("sweep_time", "time", 1.0),
                  ("wah", "percent", 0.0)),
         "character": (("slant", "percent", 0.0), ("groups", "groups", 1.0), ("offpitch", "percent", 0.0),
                       ("noisy", "percent", 0.0)),
         "voice": (("voices", "voices", 1.0), ("detune", "cents", 20.0), ("glide", "time", 0.0)),
         "arp": tuple((f"arp_{k}", k, start) for k, (_, _, start) in ARP.items())}
ROWS = (("volume", "wave", "pitch", "tone"), ("vibrato", "tremolo", "character", "voice"), ("arp",))
# the Effects tab's effects (hzbass.RACK, window/hz_rack.py): their knobs (knob = effect_setting) and kinds
RACK_KNOBS = {"chorus": (("chorus_depth", "cents"), ("chorus_rate", "vib_rate")),
              "echo": (("echo_time", "time"), ("echo_repeats", "repeats"), ("echo_fade", "percent")),
              "reverb": (("reverb_length", "time"), ("reverb_scatter", "percent"), ("reverb_level", "percent"))}
KNOBS = {key: (box, kind, start) for box, knobs in BOXES.items() for key, kind, start in knobs}
KNOBS.update({key: (fx, kind, RACK[fx][key.split("_", 1)[1]][2]) for fx, knobs in RACK_KNOBS.items()
              for key, kind in knobs})
COLOURS = {k: bright(v) for k, v in {
    "volume": FX_COLOR["volume"], "wave": FX_COLOR["sine"], "pitch": FX_COLOR["pitch"], "vibrato": FX_COLOR["vibrato"],
    "tremolo": FX_COLOR["tremolo"], "tone": FX_COLOR["sweep"], "character": FX_COLOR["slant"], "voice": "#3a6ee0",
    "arp": "#c0398a"}.items()}
# the lines each box writes (its header light is lit while one of them changes the sound) and its own setting
BOX_LINES = {"volume": ("volume",), "wave": tuple(WAVES) + ("octave",), "pitch": ("pitch",), "vibrato": ("vibrato",),
             "tremolo": ("tremolo",), "tone": ("sweep", "wah"), "character": ("slant", "offpitch", "noisy", "groups")}
BOX_EXTRA = {"wave": "mode", "voice": "voice", "arp": "arp"}
# the boxes a click on the light / name switches off and on (Bypass: the lines in hz["off"], the own setting moved to
# hz["bypass"]; the Arpeggio's click is its On instead)
BYPASS = ("volume", "wave", "pitch", "vibrato", "tremolo", "tone", "character", "voice")
PICTURES = {"volume": (260, 90), "wave": (200, 90), "pitch": (150, 90), "vibrato": (230, 60), "tremolo": (150, 60),
            "tone": (200, 90), "character": (220, 60), "voice": (230, 60), "arp": (260, 60)}
CHARACTER = ("slant", "offpitch", "noisy")  # (the Character box's lines that are just their value; Groups is counted)
WAVE_NAMES = ("none",) + tuple(WAVES)


def adsr_line(attack, decay, sustain, release):
    """The Volume line of an ADSR envelope (beats; sustain 0..1): (points, its sustain point, its length). The rise
    comes late and the drops fast first, as the ready-made envelopes do (the top half sounds about the same)."""
    pts = [[0.0, 0.0, -FAST]] if attack > 0 else []
    if decay > 0:
        pts.append([attack, 1.0, FAST])
    at = attack + decay
    pts.append([at, sustain, FAST] if release > 0 else [at, sustain])
    if release > 0:
        pts.append([at + release, 0.0])
    return pts, at, max(LOOP[0], at + release)


def pitch_line(amount, time):
    """The Pitch line from `amount` keys off (up or down) to the note's tone in `time` beats, fast first (like the
    ready-made Drop): (points, length)."""
    start = 0.5 + amount / (2 * PITCH)
    if time <= 0:
        return [[0.0, 0.5]], LOOP[0]
    return [[0.0, start, FAST], [time, 0.5]], max(LOOP[0], time)


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
    """The Pitch box: ({amount, time}, made) as read_volume (no Pitch line: Time kept as it was, and Amount too while
    Time is 0: no line then either)."""
    pts = win.fxl.get("pitch")
    if not pts:
        return {"amount": was["amount"] if was["time"] <= 0 else 0.0, "time": was["time"]}, True
    every = win.loops.get("pitch")
    got = {"amount": (pts[0][1] - 0.5) * 2 * PITCH, "time": every or was["time"]}
    made = (every and win.froms.get("pitch") == "note" and "pitch" not in win.fits and "pitch" not in win.sustains
            and "pitch:amount" not in win.fxl and same_line(pitch_line(**got)[0], pts))
    return got, bool(made)


def vibrato_line(depth, delay):
    """The Vibrato line: (points, length; None = all along): coming in over `delay` beats from each note's start."""
    if delay <= 0:
        return [[0.0, depth]], None
    return [[0.0, 0.0], [delay, depth]], max(LOOP[0], delay)


def read_vibrato(win, was):
    """The Vibrato box: ({vibrato_rate, vibrato_depth, vibrato_delay}, made) as read_volume (no Vibrato line: Delay
    kept as it was)."""
    rate = win.lfo.get("vibrato_rate", VIBRATO_RATE)
    pts = win.fxl.get("vibrato")
    if not pts:
        return {"vibrato_rate": rate, "vibrato_depth": 0.0, "vibrato_delay": was["vibrato_delay"]}, True
    every = win.loops.get("vibrato")
    got = {"vibrato_rate": rate, "vibrato_depth": max(p[1] for p in pts),
           "vibrato_delay": every if every and win.froms.get("vibrato") == "note" else 0.0}
    want, length = vibrato_line(got["vibrato_depth"], got["vibrato_delay"])
    made = (same_line(want, pts) and "vibrato" not in win.fits and "vibrato" not in win.sustains
            and "vibrato:amount" not in win.fxl and (length is None) == (every is None))
    return got, made


def read_tremolo(win, was):
    """The Tremolo box: ({tremolo_rate, tremolo_depth}, made) as read_volume (no Tremolo line: Rate 0, steady)."""
    got = {"tremolo_rate": 0.0, "tremolo_depth": win.lfo.get("tremolo_depth", TREMOLO_DEPTH)}
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
    """The Tone box: ({sweep, sweep_start, sweep_end, sweep_time, wah}, made) as read_volume (no Sweep line: off,
    its knobs kept as they were)."""
    got = {"sweep": False, "sweep_start": was["sweep_start"], "sweep_end": was["sweep_end"],
           "sweep_time": was["sweep_time"], "wah": 0.0}
    made = True
    pts = win.fxl.get("sweep")
    if pts:
        got["sweep"] = True
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


def read_character(win, was):
    """The Character box: ({slant, groups (how many), offpitch, noisy}, made) as read_volume."""
    got, made = {"slant": 0.0, "groups": 1.0, "offpitch": 0.0, "noisy": 0.0}, True
    for name in CHARACTER + ("groups",):
        if name in win.fxl:
            v = flat(win, name)
            made = made and v is not None
            got[name] = v if v is not None else max(p[1] for p in win.fxl[name])
    got["groups"] = float(group_count(got["groups"])) if "groups" in win.fxl else 1.0
    return got, made


def read_voice(win, was):
    """The Voice box: ({voices, detune, same, glide, touching, legato}, True): its own settings, not lines (one
    voice: Detune and Voices on kept as they were; no glide: Only notes that touch too)."""
    v = win.extra.get("voice") or win.extra.get("bypass", {}).get("voice", {})
    n = v.get("voices", 1)
    got = {"voices": float(n), "detune": v["detune"] if n > 1 else was["detune"],
           "same": bool(v.get("same")) if n > 1 else was["same"], "glide": v.get("glide", 0.0),
           "touching": bool(v.get("touching")) if v.get("glide") else was["touching"],
           "legato": bool(v.get("legato"))}
    return got, True


def read_arp(win, was):
    """The Arpeggio box: ({arp_on, arp_pattern, arp_chord, arp_speed, arp_octaves, arp_gate}, True) (off: its
    settings kept as they were)."""
    arp = win.extra.get("arp")
    if not arp:
        return {"arp_on": False}, True
    return {"arp_on": True, **{f"arp_{k}": v for k, v in arp.items()}}, True


def read_rack(win, was):
    """The Effects tab: {rack: its effects in order, rack_off: those switched off, each one's knobs} (an effect not
    there: its knobs kept as they were)."""
    rack = win.extra.get("rack", ())
    got = {"rack": tuple(e["kind"] for e in rack), "rack_off": tuple(e["kind"] for e in rack if e.get("off"))}
    for e in rack:
        for key, _ in RACK_KNOBS[e["kind"]]:
            got[key] = e[key.split("_", 1)[1]]
    return got


READ = {"volume": read_volume, "wave": read_wave, "pitch": read_pitch, "vibrato": read_vibrato,
        "tremolo": read_tremolo, "tone": read_tone, "character": read_character, "voice": read_voice,
        "arp": read_arp}


def paint_knob(k, start, turn, arc_from, arc):
    """A knob in the dark look: a dark track round it (`turn` degrees from `start`), the value's arc in its colour
    (`arc` degrees from `arc_from`), a grey body with a light pointer."""
    k.delete("all")
    s, m = k.size, max(3, k.size // 9)
    w = max(2, m // 2 + 1)
    k.create_arc(m, m, s - m, s - m, start=start, extent=-turn, style="arc", width=w, outline=KNOB_TRACK)
    if arc:
        k.create_arc(m, m, s - m, s - m, start=arc_from, extent=-arc, style="arc", width=w,
                     outline=k.color if k.enabled else MID)
    c, r = s / 2, s / 2 - m * 1.9
    a = math.radians(arc_from - arc)
    k.create_oval(c - r, c - r, c + r, c + r, fill=KNOB_BODY, width=max(1, m // 3),
                  outline=KNOB_FOCUS if k.focus_get() is k else KNOB_RIM)
    k.create_line(c + r * 0.25 * math.cos(a), c - r * 0.25 * math.sin(a), c + r * math.cos(a), c - r * math.sin(a),
                  fill="#f2f4f7" if k.enabled else DIM, width=2, capstyle="round")


class UpDown(Knob):
    """The up / down knob (Pitch Amount: 0 in the middle) in the dark look, turning 135 degrees each way like the
    others."""

    TURN = 135

    def __init__(self, parent, scale, changed, color, size=44):
        super().__init__(parent, scale, changed, color=color, size=size)
        self.config(background=PANEL)

    def draw(self):
        paint_knob(self, 225, 2 * self.TURN, 90, self.value / 100 * self.TURN)


class Dial(Knob):
    """A synth's knob: 0 (pointing down left) to 100 (down right), turning 270 degrees. As Knob otherwise, without
    sticking anywhere; a middle-click puts it back to `start`. In the dark look."""

    TURN = 270

    def __init__(self, parent, scale, changed, color, size=44, start=0.0):
        super().__init__(parent, scale, changed, color=color, size=size)
        self.config(background=PANEL)
        self.start = start
        self.bind("<ButtonPress-2>", lambda e: self.turn_to(self.start, True))

    def draw(self):
        paint_knob(self, 225, self.TURN, 225, self.value / 100 * self.TURN)

    def step(self, d):
        if self.enabled:
            self.turn_to(self.value + d, True)

    def point(self, e):
        if not self.enabled:
            return
        self.focus_set()
        c = self.size / 2
        if (e.x - c) ** 2 + (e.y - c) ** 2 > 4:
            a = (225 - math.degrees(math.atan2(c - e.y, e.x - c))) % 360  # (from the left end, clockwise)
            self.turn_to(a / self.TURN * 100 if a <= self.TURN else 100 if a < (self.TURN + 360) / 2 else 0)

    def press(self, e):
        if self.enabled:
            self.focus_set()
            self.drag = (e.y, self.value)

    def move(self, e):
        if self.drag:
            y, r = self.drag
            r = max(0.0, min(100.0, r + (y - e.y) * (0.1 if e.state & 1 else 0.5)))
            self.drag = (e.y, r)
            self.turn_to(r)

    def turn_to(self, value, done=False):
        super().turn_to(max(0.0, min(100.0, value)), done)


def knob_of(kind, v):
    """Where a knob points for a value."""
    most = KINDS[kind][4]
    if most:
        return min(100.0, 100 * math.sqrt(max(0.0, v) / most))
    if kind == "keys":
        return max(-100.0, min(100.0, 100 * v / PITCH))
    lo, hi = KINDS[kind][1:3]
    return max(0.0, min(100.0, 100 * (shown(kind, v) - lo) / (hi - lo)))


def value_of(kind, k):
    """A knob's value where it points (keys: whole keys)."""
    unit, lo, hi, _, most = KINDS[kind]
    if most:
        return max(lo, round(most * (k / 100) ** 2, 3))
    if kind == "keys":
        return float(round(PITCH * k / 100))
    if kind == "percent":
        return k / 100
    x = lo + (hi - lo) * k / 100
    if kind in COUNTS:
        return float(round(x))
    return x / 100 if kind in PERCENTS else round(x, 2)


def shown(kind, v):
    return 100 * v if kind in PERCENTS else v


class SynthKnobs:
    """The Knobs tab of SynthWindow (needs its fx pane, fxl / loops / ..., redraw, commit_fx, live, page)."""

    def build_knobs(self, page):
        s = self.s
        self.turning = None  # while a knob is turned: the lines from before (FxPane.state)
        self.vals = {key: start for key, (_, _, start) in KNOBS.items()}
        self.vals["wave"], self.vals["sweep"] = "none", False
        self.vals["same"], self.vals["touching"], self.vals["legato"], self.vals["mode"] = False, False, False, "off"
        self.vals["rack"], self.vals["rack_off"] = (), ()
        self.vals.update(arp_on=False, arp_pattern="up", arp_chord="placed")  # (the Effects tab's effects in order, those switched off)
        self.mode_cells = {}  # the Wave box's mode -> its knobs' cells (only the picked mode's shown)
        self.dials, self.dial_vars, self.dial_boxes, self.box_says, self.pics = {}, {}, {}, {}, {}
        self.pic_for = {}  # what each picture was drawn for
        self.box_text = {}  # what each knob's box was last given to show (different = typed there)
        self.dot_at = None  # where the envelope picture's moving dot is drawn
        self.lines = [ttk.Frame(page, style="Synth.TFrame") for _ in BOXES]  # (made before the boxes, so the boxes
        self.boxes, self.laid = {}, None  # show on top of them)
        for name, knobs in BOXES.items():
            outer = self.boxes[name] = Box(page, s, tr(f"hz.synth_{name}"), COLOURS[name])
            Tooltip(outer.lamp, tr("hz.synth_tip_arp_on" if name == "arp" else "hz.synth_tip_lamp"))
            if name in BYPASS:
                for w in (outer.lamp, outer.title):
                    w.config(cursor="hand2")
                    w.bind("<ButtonPress-1>", lambda e, name=name: self.bypass_click(name))
            box = outer.body
            col = 0
            if name == "wave":
                cell = ttk.Frame(box, style="Synth.Box.TFrame")
                cell.grid(row=0, column=0, padx=6, sticky="n")
                ttk.Label(cell, text=tr("hz.synth_wave_kind"), style="Synth.Box.TLabel").pack()
                self.wave_names = [tr("hz.synth_wave_none")] + [tr("hz.fx_" + n) for n in WAVES]
                self.wave_var = tk.StringVar(value=self.wave_names[0])
                cb = self.wave_pick = ttk.Combobox(cell, textvariable=self.wave_var, values=self.wave_names,
                                                   state="readonly", width=9, style="Synth.TCombobox")
                dark_list(cb)
                cb.pack(pady=(12, 0))
                cb.bind("<<ComboboxSelected>>", lambda e: (self.on_wave(), self.keyboard_back(e.widget)))
                Tooltip(cb, tr("hz.synth_tip_wave"))
                col = 1
            if name == "arp":
                col = self.arp_cells(outer)
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
            for key, kind, start in knobs:
                mode = key.split("_")[0]
                if name == "wave" and mode in MODE_KNOBS:  # (Mode after Octave below, then the picked mode's knobs)
                    if not self.mode_cells:
                        self.mode_cell(box, col)
                        self.mode_col = col + 1
                    at = self.mode_col + [k for k, _ in MODE_KNOBS[mode]].index(key)
                    cell = self.dial_cell(box, at, key, kind, start, COLOURS[name])
                    cell.grid_remove()
                    self.mode_cells.setdefault(mode, []).append(cell)
                    col = max(col, at + 1)
                    continue
                self.dial_cell(box, col, key, kind, start, COLOURS[name])
                col += 1
                if key in ("detune", "glide"):  # (Voices on after the copies' knobs, the tick box after Glide)
                    self.voice_cell(box, col, key)
                    col += 1
            if name == "arp":
                self.arp_choice(box, col, "chord", tuple(CHORDS))
                col += 1
            size = PICTURES[name]
            pic = self.pics[name] = tk.Canvas(box, width=round(size[0] * s), height=round(size[1] * s),
                                              background=PIC, highlightthickness=1, highlightbackground=EDGE)
            pic.grid(row=1, column=0, columnspan=col, sticky="ew", pady=(8, 0))
            says = self.box_says[name] = ttk.Label(box, text="", style="Synth.Box.Warn.TLabel",
                                                   wraplength=round(size[0] * s))
            says.grid(row=2, column=0, columnspan=col, sticky="w", pady=(6, 0))
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
        (after Glide)."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6, sticky="n")
        if after == "detune":
            ttk.Label(cell, text=tr("hz.synth_voices_on"), style="Synth.Box.TLabel").pack()
            self.same_names = [tr("hz.synth_voices_split"), tr("hz.synth_voices_same")]
            self.same_var = tk.StringVar(value=self.same_names[0])
            cb = ttk.Combobox(cell, textvariable=self.same_var, values=self.same_names, state="readonly", width=10,
                              style="Synth.TCombobox")
            dark_list(cb)
            cb.pack(pady=(12, 0))
            cb.bind("<<ComboboxSelected>>", lambda e: (
                self.change("voice", "same", self.same_names.index(self.same_var.get()) == 1),
                self.keyboard_back(e.widget)))
            Tooltip(cb, tr("hz.synth_tip_voices_on"))
        else:
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

    def dial_cell(self, box, col, key, kind, start, colour):
        """A knob with its name over it and its value's box under it."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6)
        ttk.Label(cell, text=tr(f"hz.synth_{key}"), style="Synth.Box.TLabel").pack()
        changed = lambda v, done: self.on_dial(key, v, done)
        if kind == "keys":  # (up or down: 0 in the middle)
            k = UpDown(cell, self.s, changed, colour, size=46)
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
            ttk.Label(row, text=tr(unit), style="Synth.Box.Dim.TLabel").pack(side="left", padx=(2, 0))
        e.bind("<Return>", lambda ev: (self.on_box(key), self.keyboard_back(e), "break")[2])
        e.bind("<FocusOut>", lambda ev: self.on_box(key))
        Scrub(self.app, [(e, var, lambda: self.on_box(key))], steps, lo, hi, drag_box=True)
        for w in (k, e):
            Tooltip(w, tr(f"hz.synth_tip_{key}") + "\n" + tr("hz.synth_tip_knob"))
        return cell

    def mode_cell(self, box, col):
        """The Wave box's Mode dropdown (Off, FM, Pulse width, Sync, Growl, Bitcrush)."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6, sticky="n")
        ttk.Label(cell, text=tr("hz.synth_mode"), style="Synth.Box.TLabel").pack()
        self.mode_names = [tr(f"hz.synth_mode_{m}") for m in MODE_NAMES]
        self.mode_var = tk.StringVar(value=self.mode_names[0])
        cb = ttk.Combobox(cell, textvariable=self.mode_var, values=self.mode_names, state="readonly", width=11,
                          style="Synth.TCombobox")
        dark_list(cb)
        cb.pack(pady=(12, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: (
            self.change("wave", "mode", MODE_NAMES[self.mode_names.index(self.mode_var.get())]),
            self.keyboard_back(e.widget)))
        Tooltip(cb, tr("hz.synth_tip_mode"))

    # ------------------------------------------------------------ changes

    def on_dial(self, key, k, done):
        """A knob turned (done: let go / one step of the wheel or the keys = one undo step)."""
        if self.turning is None:
            self.turning, self.turn_vals = self.fx.state(), dict(self.vals)
        self.vals[key] = value_of(KNOBS[key][1], k)
        self.sweep_on(key)
        self.write(KNOBS[key][0])
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

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
        for dial in self.dials.values():
            dial.drag = None
        self.redraw()
        self.show_knobs()
        return True

    def on_box(self, key):
        """A value typed (or stepped) in the box under a knob."""
        e, var = self.dial_boxes[key], self.dial_vars[key]
        box, kind, _ = KNOBS[key]
        lo, hi = KINDS[kind][1:3]
        if var.get() == self.box_text.get(key):  # (nothing typed: the box shows the sound as it is)
            return e.config(style=ENTRY)
        try:
            v = float(calc(var.get()))
            if not lo <= v <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):  # (not a number, or out of range: back to the last good value, user)
            var.set(self.box_text.get(key, ""))
            e.config(style=ENTRY)
            return
        e.config(style=ENTRY)
        self.box_text[key] = var.get()  # (taken: from now on the box shows the sound again)
        v = v / 100 if kind in PERCENTS else float(round(v)) if kind in COUNTS else v
        if abs(v - self.vals[key]) > 1e-9:
            self.sweep_on(key)
            self.change(box, key, v)
        else:  # (as the knob has it: rounded to whole groups)
            var.set(fmt(shown(kind, v)))
            self.box_text[key] = var.get()

    def sweep_on(self, key):
        """Turning one of Sweep's knobs puts it on (its On box ticked); the Arpeggio's the same."""
        if key.startswith("sweep_"):
            self.vals["sweep"] = True
        if key.startswith("arp_"):
            self.vals["arp_on"] = True

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
        """One of the Arpeggio box's dropdowns (Pattern / Chord): picking one puts the arpeggio on."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6, sticky="n")
        ttk.Label(cell, text=tr(f"hz.synth_arp_{what}"), style="Synth.Box.TLabel").pack()
        names = [tr(f"hz.synth_arp_{what}_{i}") for i in ids]
        var = tk.StringVar(value=names[0])
        cb = ttk.Combobox(cell, textvariable=var, values=names, state="readonly",
                          width=max(len(n) for n in names) + 1, style="Synth.TCombobox")
        dark_list(cb)
        cb.pack(pady=(12, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: (self.sweep_on("arp_"), self.change(
            "arp", f"arp_{what}", ids[names.index(var.get())]), self.keyboard_back(e.widget)))
        Tooltip(cb, tr(f"hz.synth_tip_arp_{what}"))
        setattr(self, f"arp_{what}_pick", (var, names, ids))

    def on_wave(self):
        self.change("wave", "wave", WAVE_NAMES[self.wave_names.index(self.wave_var.get())])

    def change(self, box, key, value):
        """One knob's value set: one undo step."""
        before = self.fx.state()
        self.vals[key] = value
        self.write(box)
        if self.fx.now() != before:
            self.commit_fx(before)

    def write(self, box):
        """The lines made from a box's knobs (a box switched off stays off: its knobs turn while it's silent)."""
        v, fx = self.vals, self.fx
        was_off = box in BYPASS and self.box_off(box)
        if was_off and box in BOX_EXTRA:  # (its kept setting is written anew below)
            self.set_extra("bypass", {k: s for k, s in self.extra.get("bypass", {}).items() if k != BOX_EXTRA[box]})
        if box == "volume":
            pts, at, every = adsr_line(v["attack"], v["decay"], v["sustain"], v["release"])
            fx.drop("volume")
            self.fxl["volume"] = pts
            self.loops["volume"], self.froms["volume"], self.sustains["volume"] = every, "note", at
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
            if v["amount"] and v["time"] > 0:
                pts, every = pitch_line(v["amount"], v["time"])
                self.fxl["pitch"] = pts
                self.loops["pitch"], self.froms["pitch"] = every, "note"
        elif box == "vibrato":
            fx.drop("vibrato")
            if v["vibrato_depth"] > 0:
                pts, every = vibrato_line(v["vibrato_depth"], v["vibrato_delay"])
                self.fxl["vibrato"] = pts
                if every:
                    self.loops["vibrato"], self.froms["vibrato"] = every, "note"
            self.set_lfo("vibrato_rate", v["vibrato_rate"], VIBRATO_RATE)
        elif box == "voice":
            self.set_extra("voice", {"voices": int(v["voices"]), "detune": v["detune"], "same": v["same"],
                                      "glide": v["glide"], "touching": v["touching"], "legato": v["legato"]})
        elif box == "tremolo":
            fx.drop("tremolo")
            if v["tremolo_rate"] > 0:
                self.fxl["tremolo"] = [[0.0, min(1.0, v["tremolo_rate"] / TREMOLO)]]
            self.set_lfo("tremolo_depth", v["tremolo_depth"], TREMOLO_DEPTH)
        elif box == "tone":
            fx.drop("sweep")
            if v["sweep"]:
                pts, every = sweep_line(v["sweep_start"], v["sweep_end"], v["sweep_time"])
                self.fxl["sweep"] = pts
                if every:
                    self.loops["sweep"], self.froms["sweep"] = every, "note"
            fx.drop("wah")
            if v["wah"] > 0:
                self.fxl["wah"] = [[0.0, v["wah"]]]
        elif box == "arp":
            self.set_extra("arp", {k: v[f"arp_{k}"] for k in ("pattern", "chord", *ARP)} if v["arp_on"] else None)
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
        if was_off:
            self.switch_box(box, True)
        self.redraw()
        self.show_knobs()

    def box_parts(self, name):
        """A box's lines that are there (the Volume line full all along doesn't count) and its own setting's name."""
        lines = [n for n in BOX_LINES.get(name, ()) if n in self.fxl]
        if lines == ["volume"] and flat(self, "volume") == 1.0:
            lines = []
        return lines, BOX_EXTRA.get(name)

    def box_off(self, name):
        """The box is switched off (Bypass): it has something, and all of it is off."""
        lines, extra = self.box_parts(name)
        if extra in self.extra or any(n not in self.off for n in lines):
            return False
        return bool(lines) or extra in self.extra.get("bypass", {})

    def switch_box(self, name, off):
        """A box switched off / on: its lines in / out of hz["off"], its own setting moved to / from hz["bypass"]."""
        lines, extra = self.box_parts(name)
        self.off = [n for n in self.off if n not in lines] + (lines if off else [])
        if extra:
            now, kept = dict(self.extra), dict(self.extra.get("bypass", {}))
            if off and extra in now:
                kept[extra] = now.pop(extra)
            elif not off and extra in kept:
                now[extra] = kept.pop(extra)
            now["bypass"] = kept
            self.extra = clean_extra(now)

    def bypass_click(self, name):
        """A box's light or name clicked: the box switched off (kept, silent) or back on, one undo step; a box that
        does nothing has nothing to switch (a ding)."""
        off = self.box_off(name)
        lines, extra = self.box_parts(name)
        if not off and not lines and extra not in self.extra:
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

    def set_lfo(self, key, value, plain):
        """A setting of hz["lfo"] (left out when it's what the Hz bass does without one)."""
        if abs(value - plain) < 1e-9:
            self.lfo.pop(key, None)
        else:
            self.lfo[key] = value

    # ------------------------------------------------------------ showing them

    def show_knobs(self):
        """The knobs, their boxes and the pictures show the lines (not while a knob is turned: it shows what's
        turned)."""
        if self.turning is None:
            for box, read in READ.items():
                got, made = read(self, self.vals)
                self.vals.update(got)
                says = "" if made else tr("hz.synth_drawn")
                if self.box_says[box].cget("text") != says:
                    self.box_says[box].config(text=says)
            self.vals.update(read_rack(self, self.vals))
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
        for what in ("pattern", "chord"):
            var, names, ids = getattr(self, f"arp_{what}_pick")
            name = names[ids.index(self.vals[f"arp_{what}"])]
            if var.get() != name:
                var.set(name)
        name = self.mode_names[MODE_NAMES.index(self.vals["mode"])]
        if self.mode_var.get() != name:
            self.mode_var.set(name)
        if getattr(self, "mode_shown", None) != self.vals["mode"]:  # (the Wave box changes width: rows laid again)
            self.mode_shown = self.vals["mode"]
            for mode, cells in self.mode_cells.items():
                for cell in cells:
                    grid_shown(cell, mode == self.vals["mode"])
            self.after_idle(self.fit_knobs)
        name = self.same_names[int(self.vals["same"])]
        if self.same_var.get() != name:
            self.same_var.set(name)
        if self.touching_var.get() != self.vals["touching"]:
            self.touching_var.set(self.vals["touching"])
        if self.legato_var.get() != self.vals["legato"]:
            self.legato_var.set(self.vals["legato"])
        self.light_boxes()
        self.draw_pics()
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
            outer.lamp.light(bool(lines) or BOX_EXTRA.get(name) in self.extra)
            colour = DIM if name in BYPASS and self.box_off(name) else COLOURS[name]
            if outer.title.cget("foreground") != colour:
                outer.title.config(foreground=colour)

    def pic_colour(self, name):
        """A box's colour in its picture: grey while the box is switched off."""
        return MID if name in BYPASS and self.box_off(name) else COLOURS[name]

    def draw_pics(self):
        """Each box's picture drawn again when its values (or size) changed."""
        for box, knobs in BOXES.items():
            c = self.pics[box]
            key = (tuple(self.vals[k] for k, _, _ in knobs),
                   (self.vals["wave"], self.vals["mode"]) if box == "wave" else None,
                   self.vals["sweep"] if box == "tone" else None,
                   (self.vals["same"], self.vals["touching"]) if box == "voice" else None,
                   (self.vals["arp_on"], self.vals["arp_pattern"], self.vals["arp_chord"]) if box == "arp" else None,
                   self.pic_colour(box) if box in BYPASS else None,
                   c.winfo_width(),
                   c.winfo_height())
            if c.winfo_width() < 50 or self.pic_for.get(box) == key:
                continue
            self.pic_for[box] = key
            c.delete("all")
            getattr(self, "draw_" + box)(c)
            if box == "volume":
                self.dot_at = None

    def adsr_spots(self):
        """The envelope's picture: (its points, sustain point, length, x of a beat, y of a value, the held part's
        width)."""
        v = self.vals
        pts, at, _ = adsr_line(v["attack"], v["decay"], v["sustain"], v["release"])
        every = at + v["release"]
        c = self.pics["volume"]
        w, h, pad = c.winfo_width(), c.winfo_height(), 10 * self.s
        held = every / 3 if every > 0 else 1.0  # (drawn a third as long as the rest)
        sx = (w - 2 * pad) / (every + held)

        def x_of(b, after=False):  # (after the sustain point: past the held part)
            return pad + (b + (held if after else 0.0)) * sx

        def y_of(value):
            return h - pad - value * (h - 2.5 * pad)
        return pts, at, every, x_of, y_of, held * sx

    def draw_volume(self, c):
        """The envelope: its rise and drop, the part while the key is held (Sustain), the fall after the key is let
        go (Release)."""
        s = self.s
        pts, at, every, x_of, y_of, held = self.adsr_spots()
        h = c.winfo_height()
        top = float(line_at(pts, at))
        before, after = np.linspace(0.0, at, 40), np.linspace(at, every, 40)
        xy = [(x_of(b), y_of(v)) for b, v in zip(before, line_at(pts, before))]
        xy += [(x_of(at) + held, y_of(top))]
        xy += [(x_of(b, True), y_of(v)) for b, v in zip(after, line_at(pts, after))]
        colour = self.pic_colour("volume")
        c.create_rectangle(x_of(at), 0, x_of(at) + held, h, fill=mix(colour, PIC, 0.88), outline="")
        font = ("Segoe UI", 7)
        for text, x0, x1 in (("A", x_of(0.0), x_of(self.vals["attack"])), ("D", x_of(self.vals["attack"]), x_of(at)),
                             ("S", x_of(at), x_of(at) + held), ("R", x_of(at) + held, x_of(every, True))):
            if x1 - x0 >= 8 * s:
                c.create_text((x0 + x1) / 2, 2 * s, text=text, anchor="n", fill=DIM, font=font)
        x = x_of(at) + held
        c.create_line(x, 0, x, h, fill=MID, dash=(3, 3))
        c.create_text(x + 3 * s, h - 2 * s, text=tr("hz.synth_let_go"), anchor="sw", fill=DIM, font=font)
        c.create_line(*[v for p in xy for v in p], fill=colour, width=max(2, round(2 * s)))

    def wave_hits(self):
        """Two waves' hits at a note's start as the notes come out (hzbass.wave_hits, as KeyGrid makes them; Growl
        and Bitcrush for an A1): [(place 0..2, how hard 0..1)], the soft ones left out."""
        v = self.vals
        mode = clean_mode({"kind": v["mode"], **{key.split("_", 1)[1]: v[key] for key, _ in MODE_KNOBS.get(v["mode"], ())}})
        number, since = np.arange(2), np.zeros(2)
        wave = v["wave"] != "none"
        shapes = [(WAVES[v["wave"]], np.full(2, v["shape"]), np.ones(2, bool))] if wave else []
        where, mix = wave_hits(shapes, np.full(2, not wave), number, since, mode)
        mix = mix * np.where(number % 2 == 1, 1.0 - v["octave"], 1.0)[:, None]  # (every other wave softer)
        at = number[:, None] + where
        if mode.get("kind") == "growl":
            at = at + GROWL * mode["amount"] * (number % mode["every"] / (mode["every"] - 1))[:, None]
        if mode.get("kind") == "crush" and mode["amount"] > 0:  # (an A1's wave at the Hz bass's BPM)
            bpm = float(((self.hz.target() or {}).get("hz") or {}).get("bpm", 120.0))
            grid = mode["amount"] ** 2 * CRUSH * 55.0 * 60.0 / bpm
            at = np.floor(at / grid + 0.5) * grid
        keep = (mix >= SOFT) & (at < 2.0)
        return [(float(a), float(x)) for a, x in zip(at[keep], mix[keep])]

    def draw_wave(self, c):
        """Two waves' notes, one bar each, as tall as it hits (and how many notes a wave takes)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 8 * s
        hits = self.wave_hits()
        bw = (w - 2 * pad) / (2 * SUB)
        colour = bright(FX_COLOR[self.vals["wave"]]) if self.vals["wave"] in FX_COLOR else DIM
        if self.box_off("wave"):
            colour = MID
        c.create_line(pad + (w - 2 * pad) / 2, pad, pad + (w - 2 * pad) / 2, h - pad, fill=MID, dash=(3, 3))
        for p, x in hits:
            x0 = pad + p * SUB * bw
            c.create_rectangle(x0 + bw * 0.15, h - pad - x * (h - 3 * pad), x0 + bw * 0.85, h - pad, fill=colour,
                               outline="")
        c.create_line(pad, h - pad, w - pad, h - pad, fill=MID)
        per = len([1 for p, _ in hits if p < 1]), len([1 for p, _ in hits if p >= 1])
        c.create_text(w - 3 * s, 2 * s, text=tr("hz.synth_wave_notes", n=fmt(sum(per) / 2)), anchor="ne",
                      fill=DIM, font=("Segoe UI", 7))

    def draw_pitch(self, c):
        """The pitch over the start of a note: from Amount keys off to the tone (the middle line)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 10 * self.s
        v = self.vals
        mid = h / 2
        c.create_line(pad, mid, w - pad, mid, fill=MID)
        font = ("Segoe UI", 7)
        c.create_text(w - 3 * s, mid - 2 * s, text=tr("hz.synth_pitch_tone"), anchor="se", fill=DIM, font=font)
        for k, y in ((PITCH, pad), (-PITCH, h - pad)):
            c.create_text(3 * s, y, text=f"{k:+.0f}", anchor="w", fill=DIM, font=font)
        time = max(v["time"], 1e-9)
        total = time * 1.4  # (a bit of the tone after it)
        pts, _ = pitch_line(v["amount"], v["time"]) if v["amount"] else ([[0.0, 0.5]], 0)
        b = np.linspace(0.0, total, 60)
        ys = line_at(pts, b)
        x0 = pad + 14 * s
        xy = [(x0 + u / total * (w - x0 - pad), mid - (y - 0.5) * 2 * (mid - pad)) for u, y in zip(b, ys)]
        c.create_line(*[q for p in xy for q in p], fill=self.pic_colour("pitch"), width=max(2, round(2 * s)))

    def wobble(self, c, values, colour, mid):
        """A picture's line over two beats: values at evenly spread spots (mid: -1..1 around the middle, else 0..1
        of the height)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        n = len(values)
        top, bottom = pad, h - pad
        if mid:
            y0 = (top + bottom) / 2
            c.create_line(pad, y0, w - pad, y0, fill=MID)
            ys = y0 - values * (y0 - top)
        else:
            ys = bottom - values * (bottom - top)
        c.create_line(w / 2, top, w / 2, bottom, fill=GRID, dash=(3, 3))  # (one beat in)
        xy = [(pad + i / (n - 1) * (w - 2 * pad), y) for i, y in enumerate(ys)]
        c.create_line(*[q for p in xy for q in p], fill=colour, width=max(2, round(2 * s)))

    def draw_vibrato(self, c):
        """The tone going up and down over two beats from a note's start (coming in over Delay)."""
        v = self.vals
        b = np.linspace(0.0, 2.0, 400)
        come = np.clip(b / v["vibrato_delay"], 0.0, 1.0) if v["vibrato_delay"] > 0 else np.ones(len(b))
        self.wobble(c, v["vibrato_depth"] * come * np.sin(2 * np.pi * v["vibrato_rate"] * b),
                    self.pic_colour("vibrato"), True)

    def draw_tremolo(self, c):
        """The loudness over two beats: down to 1 - Depth, Rate times a beat."""
        v = self.vals
        b = np.linspace(0.0, 2.0, 400)
        d = v["tremolo_depth"] if v["tremolo_rate"] > 0 else 0.0
        self.wobble(c, (1 - d) + d * (1 + np.cos(2 * np.pi * v["tremolo_rate"] * b)) / 2, self.pic_colour("tremolo"),
                    False)

    def draw_tone(self, c):
        """Which of the shape's keys are loud (the darker, the louder; the highest keys at the top) over a note's
        start: the Sweep moving from Start to End (dashed line: Time), Wah's stripes."""
        v = self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * self.s
        pts, every = sweep_line(v["sweep_start"], v["sweep_end"], v["sweep_time"])
        total = max(1.0, 1.5 * v["sweep_time"]) if every else 1.0
        cols, rows = 48, 24
        b = (np.arange(cols) + 0.5) / cols * total
        x = (np.arange(rows) + 0.5) / rows
        loud = np.ones((rows, cols))
        if v["sweep"]:
            where = line_at(pts, b)
            loud = 0.08 + 0.92 * np.clip(np.cos(np.pi * (x[:, None] - where[None, :])), 0.0, 1.0) ** 4
        loud = loud * ((1.0 + np.cos(2.0 * np.pi * WAH * v["wah"] * (x - 0.5))) / 2.0)[:, None]
        rgb = [int(self.pic_colour("tone")[i:i + 2], 16) for i in (1, 3, 5)]
        dark = [int(PIC[i:i + 2], 16) for i in (1, 3, 5)]
        cw, rh = (w - 2 * pad) / cols, (h - 2 * pad) / rows
        for r in range(rows):
            y = h - pad - (r + 1) * rh
            for i in range(cols):
                f = loud[r, i]
                colour = "#%02x%02x%02x" % tuple(round(d + (q - d) * f * 0.7) for q, d in zip(rgb, dark))
                c.create_rectangle(pad + i * cw, y, pad + (i + 1) * cw + 1, y + rh + 1, fill=colour, outline="")
        if v["sweep"] and every:
            xt = pad + v["sweep_time"] / total * (w - 2 * pad)
            c.create_line(xt, pad, xt, h - pad, fill=DIM, dash=(3, 3))

    def draw_character(self, c):
        """Eight of the shape's keys (the highest at the top) and their notes over four waves: when each key hits
        (Slant, Groups, Noisy late; Off pitch drifting)."""
        v = self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * self.s
        keys, waves = 8, 4
        sx, rh = (w - 2 * pad) / waves, (h - 2 * pad) / keys
        colour = self.pic_colour("character")
        groups = int(v["groups"])
        for k in range(1, waves):
            c.create_line(pad + k * sx, pad, pad + k * sx, h - pad, fill=GRID, dash=(3, 3))
        for i in range(keys):
            x = i / keys
            noise = np.random.default_rng(1000 + i)
            stretch = 1.0 + OFF_PITCH * v["offpitch"] * (x - 0.5)
            y = h - pad - (i + 1) * rh
            for n in range(-1, waves + 1):
                late = v["slant"] * x + math.floor(x * groups) / groups + v["noisy"] * noise.random()
                a, b = (n + late) * stretch, (n + 1 + late) * stretch - 0.12
                a, b = max(0.0, a), min(float(waves), b)
                if b > a:
                    c.create_rectangle(pad + a * sx, y + rh * 0.15, pad + b * sx, y + rh * 0.85,
                                       fill=colour, outline="")

    def draw_voice(self, c):
        """Left: eight of the shape's keys (the highest at the top) and the copies they play, each copy at its tone
        (the middle line = the note's own); right: a note gliding in from a lower one before it (Glide)."""
        s, v = self.s, self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        colour, font = self.pic_colour("voice"), ("Segoe UI", 7)
        split = w * 0.45
        n = int(v["voices"])
        cents = copies({"voice": clean_voice({"voices": n, "detune": v["detune"]})})
        keys, rh = 8, (h - 2 * pad) / 8

        def x_of(c_):  # (a copy's tone: the note's own in the middle, half the most Detune each way)
            return pad + (split - 2 * pad) * (0.5 + c_ / DETUNE)
        c.create_line(x_of(0.0), pad, x_of(0.0), h - pad, fill=GRID, dash=(3, 3))
        for c_ in cents:
            c.create_line(x_of(c_), pad, x_of(c_), h - pad, fill=mix(colour, PIC, 0.7))
        r = max(2.0, min(rh * 0.35, 4 * s))
        for i in range(keys):
            y = h - pad - (i + 0.5) * rh
            for c_ in (cents if v["same"] else [cents[i % len(cents)]]):
                c.create_rectangle(x_of(c_) - r, y - r, x_of(c_) + r, y + r, fill=colour, outline="")
        says = (tr("hz.synth_voice_notes", n=n) if v["same"] else tr("hz.synth_voice_no_extra")) if n > 1 else ""
        c.create_text(split - 3 * s, 1 * s, text=says, anchor="ne", fill=DIM, font=font)
        c.create_line(split, pad, split, h - pad, fill=MID)
        # the glide: a note a beat long, then one 5 keys up, touching
        x0, x1 = split + pad, w - pad
        lo, hi = h - pad - (h - 2 * pad) * 0.2, pad + (h - 2 * pad) * 0.2
        mid = (x0 + x1) / 2
        for a, b, y in ((x0, mid, lo), (mid, x1, hi)):
            c.create_rectangle(a + 1, y - 2 * s, b - 1, y + 2 * s, fill="#2f353e", outline="")
        g = min(v["glide"], 1.0)
        u = np.linspace(0.0, 1.0, 40)
        xy = [(x0, lo), (mid, lo)] + [(mid + q * g * (x1 - mid), hi + (lo - hi) * (1 - q) ** 2) for q in u]
        xy.append((x1, hi))
        c.create_line(*[q for p in xy for q in p], fill=colour, width=max(2, round(2 * s)))
        c.create_text(x1, 1 * s, text=tr("hz.synth_glide"), anchor="ne", fill=DIM, font=font)

    def draw_arp(self, c):
        """Two beats of what it plays (a small piano roll): for a chord of A1, C2 and E2 placed together, or (with a
        chord shape) one A1; greyed while it's off."""
        s, v = self.s, self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        arp = clean_arp({k: v[f"arp_{k}"] for k in ("pattern", "chord", *ARP)})
        keys = (33, 36, 40) if arp["chord"] == "placed" else (33,)
        tones = [{"t": 0.0, "len": 2.0, "key": k, "cents": 0.0, "id": i + 1, "to": []} for i, k in enumerate(keys)]
        got = arpeggiated(tones, arp)  # (off: the same run, greyed)
        lo, hi = min(n["key"] for n in got), max(n["key"] for n in got)
        top = 12 * s  # (room for the words at the top)
        rh = (h - pad - top) / max(6, hi - lo + 1)
        colour = COLOURS["arp"] if v["arp_on"] else MID
        c.create_line(w / 2, pad, w / 2, h - pad, fill=GRID, dash=(3, 3))  # (one beat in)
        for n in got:
            x0 = pad + n["t"] / 2 * (w - 2 * pad)
            x1 = max(x0 + 2, pad + (n["t"] + n["len"]) / 2 * (w - 2 * pad) - 1)
            y = h - pad - (n["key"] - lo + 1) * rh
            c.create_rectangle(x0, y + 1, x1, y + rh - 1, fill=colour, outline="")
        c.create_text(w - 3 * s, 2 * s, text=tr("hz.synth_arp_notes", n=fmt(arp["speed"])), anchor="ne",
                      fill=DIM, font=("Segoe UI", 7))

    def draw_adsr_dot(self):
        """While a key sounds: a dot on the envelope's picture, waiting at the sustain point while it's held, down
        the fall after it's let go."""
        pos = self.live.position() if self.live.active() and self.page.get() == "knobs" else None
        c = self.pics["volume"]
        got = None
        if pos is not None and c.winfo_width() >= 50:
            u, gone = pos
            pts, at, every, x_of, y_of, held = self.adsr_spots()
            b = min(u, at) if gone is None else min(every, at + max(0.0, u - gone))
            got = round(x_of(b, gone is not None)), round(y_of(float(line_at(pts, b))))
        if got == self.dot_at:
            return
        self.dot_at = got
        r = 4 * self.s
        c.delete("dot")
        if got:
            c.create_oval(got[0] - r, got[1] - r, got[0] + r, got[1] + r, fill=COLOURS["volume"], outline=TEXT,
                          width=max(1, round(1.5 * self.s)), tags="dot")
