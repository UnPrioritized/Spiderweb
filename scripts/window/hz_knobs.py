"""The synth window's Knobs tab (window/hz_synth.py): boxes of knobs, like a synth's (user), each writing the effects'
lines the Hz bass already has, so everything they do is in the MIDI. A line drawn by hand that the knobs can't have
made shows them about where it is (with a note); turning one makes new lines from the knobs.
  Volume: Attack, Decay, Sustain, Release = the Volume line once per note, with its sustain point and fall.
  Wave: one waveform (or the plain tone) at a Shape amount, and Octave below: those lines, flat.
  Pitch: Amount (keys) and Time = the Pitch line once per note, from Amount keys off to the note's tone.
  Vibrato (LFO 1): Rate (hz["lfo"]), Depth = the Vibrato line, Delay = it comes in over that long, once per note.
  Tremolo (LFO 2): Rate = the Tremolo line (its value is how fast), Depth (hz["lfo"]).
  Tone: Sweep (on / off) from Start to End in Time = the Sweep line once per note; Wah = its line, flat.
  Character: Slant, Groups, Off pitch, Noisy = their lines, flat.
Each box has a picture: the envelope (with a dot while a key sounds), one wave's hits (the notes), the pitch, the
wobbles over two beats, which keys are loud over time, the keys' notes over four waves."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.hzbass import (FAST, GROUPS, LOOP, OFF_PITCH, PITCH, SOFT, SUB, TREMOLO, TREMOLO_DEPTH, VIBRATO_RATE, WAH,
                          WAVES, group_count, line_at)
from window.hz_effects import AMOUNT, FX_COLOR
from window.tool_window import Knob
from window.widgets import Scrub, Tooltip

WARN = "#c06000"
TIME_KNOB = 4.0  # beats a time knob goes to (along a curve: fine near 0)
TIME_MOST = 64.0  # ... and a typed one
# what a knob's value is: (unit text (None: no unit), lowest, highest typed, the box's steps (Shift, Ctrl), where the
# knob goes to along a curve (fine near 0; None: straight, percent / keys / groups))
KINDS = {"time": ("hz.synth_beats", 0.0, TIME_MOST, (0.05, 0.25, 0.01), TIME_KNOB),
         "percent": ("hz.synth_percent", 0.0, 100.0, (1, 10, 0.1), None),
         "keys": ("hz.synth_keys", -PITCH, PITCH, (1, 3, 0.1), None),
         "vib_rate": ("hz.synth_a_beat", 0.0, 64.0, (0.1, 1, 0.01), 10.0),
         "trem_rate": ("hz.synth_a_beat", 0.0, TREMOLO, (0.1, 1, 0.01), TREMOLO),
         "groups": (None, 1.0, float(GROUPS), (1, 1, 1), None)}
# the boxes and their knobs: (knob, kind, value at the start / a middle-click); rows of boxes
BOXES = {"volume": (("attack", "time", 0.0), ("decay", "time", 0.0), ("sustain", "percent", 1.0),
                    ("release", "time", 0.0)),
         "wave": (("shape", "percent", 1.0), ("octave", "percent", 0.0)),
         "pitch": (("amount", "keys", 0.0), ("time", "time", 0.25)),
         "vibrato": (("vibrato_rate", "vib_rate", VIBRATO_RATE), ("vibrato_depth", "percent", 0.0),
                     ("vibrato_delay", "time", 0.0)),
         "tremolo": (("tremolo_rate", "trem_rate", 0.0), ("tremolo_depth", "percent", TREMOLO_DEPTH)),
         "tone": (("sweep_start", "percent", 1.0), ("sweep_end", "percent", 0.0), ("sweep_time", "time", 1.0),
                  ("wah", "percent", 0.0)),
         "character": (("slant", "percent", 0.0), ("groups", "groups", 1.0), ("offpitch", "percent", 0.0),
                       ("noisy", "percent", 0.0))}
ROWS = (("volume", "wave", "pitch", "tone"), ("vibrato", "tremolo", "character"))
KNOBS = {key: (box, kind, start) for box, knobs in BOXES.items() for key, kind, start in knobs}
COLOURS = {"volume": FX_COLOR["volume"], "wave": FX_COLOR["sine"], "pitch": FX_COLOR["pitch"],
           "vibrato": FX_COLOR["vibrato"], "tremolo": FX_COLOR["tremolo"], "tone": FX_COLOR["sweep"],
           "character": FX_COLOR["slant"]}
PICTURES = {"volume": (260, 90), "wave": (200, 90), "pitch": (150, 90), "vibrato": (230, 60), "tremolo": (150, 60),
            "tone": (200, 90), "character": (220, 60)}
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
    return got, made


def read_pitch(win, was):
    """The Pitch box: ({amount, time}, made) as read_volume (no Pitch line: Time kept as it was)."""
    pts = win.fxl.get("pitch")
    if not pts:
        return {"amount": 0.0, "time": was["time"]}, True
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


READ = {"volume": read_volume, "wave": read_wave, "pitch": read_pitch, "vibrato": read_vibrato,
        "tremolo": read_tremolo, "tone": read_tone, "character": read_character}


class Dial(Knob):
    """A synth's knob: 0 (pointing down left) to 100 (down right), turning 270 degrees. As Knob otherwise, without
    sticking anywhere; a middle-click puts it back to `start`."""

    TURN = 270

    def __init__(self, parent, scale, changed, color, size=44, start=0.0):
        super().__init__(parent, scale, changed, color=color, size=size)
        self.start = start
        self.bind("<ButtonPress-2>", lambda e: self.turn_to(self.start, True))

    def draw(self):
        self.delete("all")
        s, m = self.size, max(3, self.size // 9)
        w = max(2, m // 2)
        self.create_arc(m, m, s - m, s - m, start=225, extent=-self.TURN, style="arc", width=w,
                        outline="#888" if self.focus_get() is self else "#ccc")
        if self.value:
            self.create_arc(m, m, s - m, s - m, start=225, extent=-self.value / 100 * self.TURN, style="arc",
                            outline=self.color if self.enabled else "#ccc", width=w)
        c, r = s / 2, s / 2 - m * 1.8
        a = math.radians(225 - self.value / 100 * self.TURN)
        self.create_oval(c - r, c - r, c + r, c + r, fill="#555" if self.enabled else "#aaa", outline="")
        self.create_line(c, c, c + r * math.cos(a), c - r * math.sin(a), fill="white", width=2)

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
    if kind == "groups":
        return 100 * (v - 1) / (GROUPS - 1)
    return 100 * v


def value_of(kind, k):
    """A knob's value where it points (keys: whole keys)."""
    most = KINDS[kind][4]
    if most:
        return round(most * (k / 100) ** 2, 3)
    if kind == "keys":
        return float(round(PITCH * k / 100))
    if kind == "groups":
        return float(round(1 + (GROUPS - 1) * k / 100))
    return k / 100


def shown(kind, v):
    return 100 * v if kind == "percent" else v


class SynthKnobs:
    """The Knobs tab of SynthWindow (needs its fx pane, fxl / loops / ..., redraw, commit_fx, live, page)."""

    def build_knobs(self, page):
        s = self.s
        self.turning = None  # while a knob is turned: the lines from before (FxPane.state)
        self.vals = {key: start for key, (_, _, start) in KNOBS.items()}
        self.vals["wave"], self.vals["sweep"] = "none", False
        self.dials, self.dial_vars, self.dial_boxes, self.box_says, self.pics = {}, {}, {}, {}, {}
        self.pic_for = {}  # what each picture was drawn for
        self.dot_at = None  # where the envelope picture's moving dot is drawn
        rows = [ttk.Frame(page) for _ in ROWS]
        for i, row in enumerate(rows):
            row.pack(fill="x", anchor="w", pady=(0, 8) if i < len(rows) - 1 else 0)
        where = {name: rows[i] for i, names in enumerate(ROWS) for name in names}
        for name, knobs in BOXES.items():
            box = ttk.Labelframe(where[name], text=tr(f"hz.synth_{name}"), padding=(10, 4, 10, 8))
            box.pack(side="left", anchor="n", padx=(0, 10))
            col = 0
            if name == "wave":
                cell = ttk.Frame(box)
                cell.grid(row=0, column=0, padx=6, sticky="n")
                ttk.Label(cell, text=tr("hz.synth_wave_kind")).pack()
                self.wave_names = [tr("hz.synth_wave_none")] + [tr("hz.fx_" + n) for n in WAVES]
                self.wave_var = tk.StringVar(value=self.wave_names[0])
                cb = self.wave_pick = ttk.Combobox(cell, textvariable=self.wave_var, values=self.wave_names,
                                                   state="readonly", width=9)
                cb.pack(pady=(12, 0))
                cb.bind("<<ComboboxSelected>>", lambda e: self.on_wave())
                Tooltip(cb, tr("hz.synth_tip_wave"))
                col = 1
            if name == "tone":
                cell = ttk.Frame(box)
                cell.grid(row=0, column=0, padx=6, sticky="n")
                ttk.Label(cell, text=tr("hz.synth_sweep")).pack()
                self.sweep_var = tk.BooleanVar(value=False)
                cb = ttk.Checkbutton(cell, text=tr("hz.synth_sweep_on"), variable=self.sweep_var,
                                     command=lambda: self.change("tone", "sweep", self.sweep_var.get()))
                cb.pack(pady=(14, 0))
                Tooltip(cb, tr("hz.synth_tip_sweep"))
                col = 1
            for key, kind, start in knobs:
                self.dial_cell(box, col, key, kind, start, COLOURS[name])
                col += 1
            size = PICTURES[name]
            pic = self.pics[name] = tk.Canvas(box, width=round(size[0] * s), height=round(size[1] * s),
                                              background="white", highlightthickness=1, highlightbackground="#ccc")
            pic.grid(row=1, column=0, columnspan=col, sticky="ew", pady=(8, 0))
            says = self.box_says[name] = ttk.Label(box, text="", foreground=WARN, wraplength=round(size[0] * s))
            says.grid(row=2, column=0, columnspan=col, sticky="w", pady=(6, 0))
            pic.bind("<Configure>", lambda e, says=says: (says.config(wraplength=e.width), self.draw_pics()))

    def dial_cell(self, box, col, key, kind, start, colour):
        """A knob with its name over it and its value's box under it."""
        cell = ttk.Frame(box)
        cell.grid(row=0, column=col, padx=6)
        ttk.Label(cell, text=tr(f"hz.synth_{key}")).pack()
        changed = lambda v, done: self.on_dial(key, v, done)
        if kind == "keys":  # (up or down: 0 in the middle)
            k = Knob(cell, self.s, changed, color=colour, size=46)
        else:
            k = Dial(cell, self.s, changed, colour, size=46, start=knob_of(kind, start))
        self.dials[key] = k
        k.pack()
        row = ttk.Frame(cell)
        row.pack(pady=(2, 0))
        var = self.dial_vars[key] = tk.StringVar()
        e = self.dial_boxes[key] = ttk.Entry(row, textvariable=var, width=5, justify="center")
        e.pack(side="left")
        unit, lo, hi, steps, _ = KINDS[kind]
        if unit:
            ttk.Label(row, text=tr(unit), foreground="#777").pack(side="left", padx=(2, 0))
        e.bind("<Return>", lambda ev: (self.on_box(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_box(key))
        Scrub(self.app, [(e, var, lambda: self.on_box(key))], steps, lo, hi, drag_box=True)
        for w in (k, e):
            Tooltip(w, tr(f"hz.synth_tip_{key}") + "\n" + tr("hz.synth_tip_knob"))

    # ------------------------------------------------------------ changes

    def on_dial(self, key, k, done):
        """A knob turned (done: let go / one step of the wheel or the keys = one undo step)."""
        if self.turning is None:
            self.turning = self.fx.state()
        self.vals[key] = value_of(KNOBS[key][1], k)
        self.sweep_on(key)
        self.write(KNOBS[key][0])
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

    def on_box(self, key):
        """A value typed (or stepped) in the box under a knob."""
        e, var = self.dial_boxes[key], self.dial_vars[key]
        box, kind, _ = KNOBS[key]
        lo, hi = KINDS[kind][1:3]
        try:
            v = float(calc(var.get()))
            if not lo <= v <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        v = v / 100 if kind == "percent" else float(round(v)) if kind == "groups" else v
        if abs(v - self.vals[key]) > 1e-9:
            self.sweep_on(key)
            self.change(box, key, v)
        else:  # (as the knob has it: rounded to whole groups)
            var.set(fmt(shown(kind, v)))

    def sweep_on(self, key):
        """Turning one of Sweep's knobs puts it on (its On box ticked)."""
        if key.startswith("sweep_"):
            self.vals["sweep"] = True

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
        """The lines made from a box's knobs."""
        v, fx = self.vals, self.fx
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
        else:
            for name in CHARACTER:
                fx.drop(name)
                if v[name] > 0:
                    self.fxl[name] = [[0.0, v[name]]]
            fx.drop("groups")
            if v["groups"] > 1:
                self.fxl["groups"] = [[0.0, (v["groups"] - 1) / (GROUPS - 1)]]
        self.redraw()
        self.show_knobs()

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
        for key, (box, kind, _) in KNOBS.items():
            v = self.vals[key]
            k = knob_of(kind, v)
            if not self.dials[key].drag and abs(self.dials[key].value - k) > 0.05:
                self.dials[key].set(k)
            text = fmt(shown(kind, v))
            e = self.dial_boxes[key]
            if self.dial_vars[key].get() != text and self.focus_get() is not e:
                self.dial_vars[key].set(text)
                e.config(style="TEntry")
        name = self.wave_names[WAVE_NAMES.index(self.vals["wave"])]
        if self.wave_var.get() != name:
            self.wave_var.set(name)
        if self.sweep_var.get() != self.vals["sweep"]:
            self.sweep_var.set(self.vals["sweep"])
        self.draw_pics()

    def draw_pics(self):
        """Each box's picture drawn again when its values (or size) changed."""
        for box, knobs in BOXES.items():
            c = self.pics[box]
            key = (tuple(self.vals[k] for k, _, _ in knobs), self.vals["wave"] if box == "wave" else None,
                   self.vals["sweep"] if box == "tone" else None, c.winfo_width(), c.winfo_height())
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
        c.create_rectangle(x_of(at), 0, x_of(at) + held, h, fill="#eef3fc", outline="")
        font = ("Segoe UI", 7)
        for text, x0, x1 in (("A", x_of(0.0), x_of(self.vals["attack"])), ("D", x_of(self.vals["attack"]), x_of(at)),
                             ("S", x_of(at), x_of(at) + held), ("R", x_of(at) + held, x_of(every, True))):
            if x1 - x0 >= 8 * s:
                c.create_text((x0 + x1) / 2, 2 * s, text=text, anchor="n", fill="#999", font=font)
        x = x_of(at) + held
        c.create_line(x, 0, x, h, fill="#bbb", dash=(3, 3))
        c.create_text(x + 3 * s, h - 2 * s, text=tr("hz.synth_let_go"), anchor="sw", fill="#999", font=font)
        c.create_line(*[v for p in xy for v in p], fill=COLOURS["volume"], width=max(2, round(2 * s)))

    def wave_hits(self):
        """Two waves' hits as the notes come out (hzbass.KeyGrid): [(place 0..2, how hard 0..1)], the soft ones left
        out."""
        v = self.vals
        part = np.arange(SUB) / SUB
        one = (part == 0).astype(float)
        if v["wave"] != "none":
            one = (1.0 - v["shape"]) * one + v["shape"] * WAVES[v["wave"]](part)
        out = []
        for n in (0, 1):  # (every other wave softer: the octave below)
            hard = one * (1.0 - v["octave"] if n % 2 else 1.0)
            out += [(n + p, float(x)) for p, x in zip(part, hard) if x >= SOFT]
        return out

    def draw_wave(self, c):
        """Two waves' notes, one bar each, as tall as it hits (and how many notes a wave takes)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 8 * s
        hits = self.wave_hits()
        bw = (w - 2 * pad) / (2 * SUB)
        colour = FX_COLOR.get(self.vals["wave"], "#777")
        c.create_line(pad + (w - 2 * pad) / 2, pad, pad + (w - 2 * pad) / 2, h - pad, fill="#ddd", dash=(3, 3))
        for p, x in hits:
            x0 = pad + p * SUB * bw
            c.create_rectangle(x0 + bw * 0.15, h - pad - x * (h - 3 * pad), x0 + bw * 0.85, h - pad, fill=colour,
                               outline="")
        c.create_line(pad, h - pad, w - pad, h - pad, fill="#bbb")
        per = len([1 for p, _ in hits if p < 1]), len([1 for p, _ in hits if p >= 1])
        c.create_text(w - 3 * s, 2 * s, text=tr("hz.synth_wave_notes", n=fmt(sum(per) / 2)), anchor="ne",
                      fill="#777", font=("Segoe UI", 7))

    def draw_pitch(self, c):
        """The pitch over the start of a note: from Amount keys off to the tone (the middle line)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 10 * self.s
        v = self.vals
        mid = h / 2
        c.create_line(pad, mid, w - pad, mid, fill="#ddd")
        font = ("Segoe UI", 7)
        c.create_text(w - 3 * s, mid - 2 * s, text=tr("hz.synth_pitch_tone"), anchor="se", fill="#999", font=font)
        for k, y in ((PITCH, pad), (-PITCH, h - pad)):
            c.create_text(3 * s, y, text=f"{k:+.0f}", anchor="w", fill="#bbb", font=font)
        time = max(v["time"], 1e-9)
        total = time * 1.4  # (a bit of the tone after it)
        pts, _ = pitch_line(v["amount"], v["time"]) if v["amount"] else ([[0.0, 0.5]], 0)
        b = np.linspace(0.0, total, 60)
        ys = line_at(pts, b)
        x0 = pad + 14 * s
        xy = [(x0 + u / total * (w - x0 - pad), mid - (y - 0.5) * 2 * (mid - pad)) for u, y in zip(b, ys)]
        c.create_line(*[q for p in xy for q in p], fill=COLOURS["pitch"], width=max(2, round(2 * s)))

    def wobble(self, c, values, colour, mid):
        """A picture's line over two beats: values at evenly spread spots (mid: -1..1 around the middle, else 0..1
        of the height)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        n = len(values)
        top, bottom = pad, h - pad
        if mid:
            y0 = (top + bottom) / 2
            c.create_line(pad, y0, w - pad, y0, fill="#ddd")
            ys = y0 - values * (y0 - top)
        else:
            ys = bottom - values * (bottom - top)
        c.create_line(w / 2, top, w / 2, bottom, fill="#e4e4e4", dash=(3, 3))  # (one beat in)
        xy = [(pad + i / (n - 1) * (w - 2 * pad), y) for i, y in enumerate(ys)]
        c.create_line(*[q for p in xy for q in p], fill=colour, width=max(2, round(2 * s)))

    def draw_vibrato(self, c):
        """The tone going up and down over two beats from a note's start (coming in over Delay)."""
        v = self.vals
        b = np.linspace(0.0, 2.0, 400)
        come = np.clip(b / v["vibrato_delay"], 0.0, 1.0) if v["vibrato_delay"] > 0 else np.ones(len(b))
        self.wobble(c, v["vibrato_depth"] * come * np.sin(2 * np.pi * v["vibrato_rate"] * b), COLOURS["vibrato"],
                    True)

    def draw_tremolo(self, c):
        """The loudness over two beats: down to 1 - Depth, Rate times a beat."""
        v = self.vals
        b = np.linspace(0.0, 2.0, 400)
        d = v["tremolo_depth"] if v["tremolo_rate"] > 0 else 0.0
        self.wobble(c, (1 - d) + d * (1 + np.cos(2 * np.pi * v["tremolo_rate"] * b)) / 2, COLOURS["tremolo"],
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
        rgb = [int(COLOURS["tone"][i:i + 2], 16) for i in (1, 3, 5)]
        cw, rh = (w - 2 * pad) / cols, (h - 2 * pad) / rows
        for r in range(rows):
            y = h - pad - (r + 1) * rh
            for i in range(cols):
                f = loud[r, i]
                colour = "#%02x%02x%02x" % tuple(round(255 + (q - 255) * f) for q in rgb)
                c.create_rectangle(pad + i * cw, y, pad + (i + 1) * cw + 1, y + rh + 1, fill=colour, outline="")
        if v["sweep"] and every:
            xt = pad + v["sweep_time"] / total * (w - 2 * pad)
            c.create_line(xt, pad, xt, h - pad, fill="#999", dash=(3, 3))

    def draw_character(self, c):
        """Eight of the shape's keys (the highest at the top) and their notes over four waves: when each key hits
        (Slant, Groups, Noisy late; Off pitch drifting)."""
        v = self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * self.s
        keys, waves = 8, 4
        sx, rh = (w - 2 * pad) / waves, (h - 2 * pad) / keys
        groups = int(v["groups"])
        for k in range(1, waves):
            c.create_line(pad + k * sx, pad, pad + k * sx, h - pad, fill="#e4e4e4", dash=(3, 3))
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
                                       fill=COLOURS["character"], outline="")

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
            c.create_oval(got[0] - r, got[1] - r, got[0] + r, got[1] + r, fill=COLOURS["volume"], outline="white",
                          width=max(1, round(1.5 * self.s)), tags="dot")
