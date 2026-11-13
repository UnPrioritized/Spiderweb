"""The synth window's knobs (window/hz_knobs.py, hz_macros.py, hz_mod.py, hz_rack.py): the dark Ringed / Dial /
UpDown knob widgets, how a knob's value maps to where it points (KINDS, knob_of, value_of) and their look."""

import math

from notes.hzbass import (ARP, DETUNE, GROUPS, HEIGHT_MOST, LFO, MODES, OSC2, PITCH, RACK, RATE_TOP, TREMOLO, VOICES,
                          height_times, height_value)
from window.synth_look import DIM, MID, PANEL, PIC
from window.tool_window import Knob
from window.widgets import wheel_ready

KNOB_BODY, KNOB_RIM, KNOB_TRACK, KNOB_FOCUS = "#474d57", "#5c636e", "#1a1d22", "#9aa3ae"
TIME_KNOB = 4.0  # beats a time knob goes to (along a curve: fine near 0)
TIME_MOST = 64.0  # ... and a typed one
# what a knob's value is: (unit text (None: no unit), lowest, highest typed, the box's steps (Shift, Ctrl), where the
# knob goes to along a curve (fine near 0; None: straight, percent / keys / groups))
KINDS = {"time": ("hz.synth_beats", 0.0, TIME_MOST, (0.05, 0.25, 0.01), TIME_KNOB),
         "percent": ("hz.synth_percent", 0.0, 100.0, (1, 10, 0.1), None),
         "keys": ("hz.synth_keys", -PITCH, PITCH, (1, 3, 0.1), None),
         "pitch": ("hz.synth_keys", -PITCH, PITCH, (1, 3, 0.1), None),  # (Amount: up to the Range, set_range)
         "range": ("hz.synth_keys", *LFO["bend_range"], (1, 12, 1), None),
         "vib_rate": ("hz.synth_a_beat", 0.0, 64.0, (0.1, 1, 0.01), RATE_TOP),
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
         "gate": ("hz.synth_percent", 100 * ARP["gate"][0], 100 * ARP["gate"][1], (1, 10, 0.1), None),
         "swing": ("hz.synth_percent", 100 * ARP["swing"][0], 100 * ARP["swing"][1], (1, 10, 0.1), None),
         "bend": ("hz.synth_percent", -100.0, 100.0, (1, 10, 0.1), None),  # (bend: -1..1, 0 in the middle)
         "threshold": ("hz.synth_db", RACK["compressor"]["threshold"][0], RACK["compressor"]["threshold"][1],
                       (1, 6, 0.1), None),
         "comp_ratio": ("hz.synth_to_one", RACK["compressor"]["ratio"][0], RACK["compressor"]["ratio"][1],
                        (0.5, 2, 0.1), RACK["compressor"]["ratio"][1]),
         "gain": ("hz.synth_db", RACK["compressor"]["gain"][0], RACK["compressor"]["gain"][1], (1, 6, 0.1), None),
         "osc_octave": (None, OSC2["octave"][0], OSC2["octave"][1], (1, 1, 1), None),
         "fine": ("hz.synth_cents", OSC2["fine"][0], OSC2["fine"][1], (1, 10, 0.1), None),
         "height": ("hz.synth_percent", 0.0, 100.0 * HEIGHT_MOST, (1, 10, 0.1), None)}  # (100 % in the middle)
PERCENTS = ("percent", "width", "gate", "swing", "bend", "height")  # (kept 0..1 (Height 0..4), shown and typed in %)
UPDOWN = ("keys", "pitch", "bend", "osc_octave", "fine")  # (knobs with 0 in the middle)
COUNTS = ("groups", "voices", "every", "repeats", "octaves", "osc_octave", "range")  # (whole numbers)


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
                  outline=KNOB_FOCUS if wheel_ready(k) else KNOB_RIM)  # (lit = the wheel turns it)
    k.create_line(c + r * 0.25 * math.cos(a), c - r * 0.25 * math.sin(a), c + r * math.cos(a), c - r * math.sin(a),
                  fill="#f2f4f7" if k.enabled else DIM, width=2, capstyle="round")
    if k.hover:  # (a macro dragged over it: let go = linked)
        k.create_oval(1, 1, s - 1, s - 1, outline=k.hover, width=2)
    if k.ring:  # (a macro moves it: the picked macro's reach on the outside, a dot where the sound has it; a MOD
        reach, colour, now = k.ring[:3]  # source's both ways: from its low end)
        low = k.ring[3] if len(k.ring) > 3 and k.ring[3] is not None else k.value
        if reach is not None:
            o = max(1, m - w)
            k.create_arc(o, o, s - o, s - o, start=k.angle(low), extent=k.angle(reach) - k.angle(low),
                         style="arc", width=2, outline=colour)
        a, rr = math.radians(k.angle(now)), (s - 2 * m) / 2
        d = max(2, w)
        k.create_oval(c + rr * math.cos(a) - d, c - rr * math.sin(a) - d, c + rr * math.cos(a) + d,
                      c - rr * math.sin(a) + d, fill=colour, outline=PIC)


class Ringed:
    """A synth knob a macro can move (window/hz_macros.py): ring = (where the picked macro all the way takes it, or
    None; its colour; where the sound has it now), in the knob's own 0..100 (or -100..100). Dragging on its outside
    (past the grey body) while the picked macro moves it = ring_drag(pixels up since the press, fine, done): how far
    the macro moves it. hover = a macro dragged over it (its colour)."""

    ring = ring_drag = ring_held = hover = None

    def ringed(self):
        self.bind("<ButtonPress-1>", self.ring_press)
        self.bind("<B1-Motion>", self.ring_move)
        self.bind("<ButtonRelease-1>", lambda e: self.ring_release())

    def on_ring(self, e):
        s = self.size
        c, r = s / 2, s / 2 - max(3, s // 9) * 1.9
        return bool(self.ring and self.ring[0] is not None and self.ring_drag) and (e.x - c) ** 2 + (e.y - c) ** 2 > r * r

    def ring_press(self, e):
        if self.enabled and self.on_ring(e):
            self.focus_set()
            self.ring_held = e.y
            self.ring_drag(0, False, False)
        else:
            self.press(e)

    def ring_move(self, e):
        if self.ring_held is not None:
            self.ring_drag(self.ring_held - e.y, bool(e.state & 1), False)
        else:
            self.move(e)

    def ring_release(self):
        if self.ring_held is not None:
            self.ring_held = None
            self.ring_drag(None, False, True)
        else:
            self.release()

    def held(self):
        return bool(self.drag or self.pointing or self.ring_held is not None)


class UpDown(Ringed, Knob):
    """The up / down knob (Pitch Amount: 0 in the middle) in the dark look, turning 135 degrees each way like the
    others."""

    TURN = 135

    def __init__(self, parent, scale, changed, color, size=44, start=0.0):
        super().__init__(parent, scale, changed, color=color, size=size)
        self.config(background=PANEL)
        self.start = start  # (a middle-click puts it back there)
        self.stepping = 0  # (while a wheel / arrow step turns it: which way; see SynthKnobs.on_dial)
        self.bind("<ButtonPress-2>", lambda e: self.turn_to(self.start, True))
        self.ringed()

    def angle(self, v):
        return 90 - v / 100 * self.TURN

    def draw(self):
        paint_knob(self, 225, 2 * self.TURN, 90, self.value / 100 * self.TURN)

    def step(self, d):
        """A wheel / arrow step: one turn of its own (one undo step), as the synth's other knobs."""
        if self.enabled:
            self.stepping = d
            v = self.value + d
            self.turn_to(0 if v * self.value < 0 else v, True)  # (stops at 0 on the way past)
            self.stepping = 0


class Dial(Ringed, Knob):
    """A synth's knob: 0 (pointing down left) to 100 (down right), turning 270 degrees. As Knob otherwise, without
    sticking anywhere; a middle-click puts it back to `start`. In the dark look."""

    TURN = 270

    def __init__(self, parent, scale, changed, color, size=44, start=0.0):
        super().__init__(parent, scale, changed, color=color, size=size)
        self.config(background=PANEL)
        self.start = start
        self.stepping = 0  # (while a wheel / arrow step turns it: which way; see SynthKnobs.on_dial)
        self.bind("<ButtonPress-2>", lambda e: self.turn_to(self.start, True))
        self.ringed()

    def angle(self, v):
        return 225 - v / 100 * self.TURN

    def draw(self):
        paint_knob(self, 225, self.TURN, 225, self.value / 100 * self.TURN)

    def step(self, d):
        if self.enabled:
            self.stepping = d
            self.turn_to(self.value + d, True)
            self.stepping = 0

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
    if kind == "height":  # (as its line: the middle = 100 %)
        return max(0.0, min(100.0, 100 * height_value(v)))
    lo, hi = KINDS[kind][1:3]
    if kind in UPDOWN:  # (-hi .. hi)
        return max(-100.0, min(100.0, 100 * shown(kind, v) / hi))
    return max(0.0, min(100.0, 100 * (shown(kind, v) - lo) / (hi - lo)))


def value_of(kind, k):
    """A knob's value where it points (keys: whole keys)."""
    unit, lo, hi, _, most = KINDS[kind]
    if most:
        return max(lo, round(most * (k / 100) ** 2, 3))
    if kind in ("keys", "pitch", "osc_octave"):  # (whole keys / octaves)
        return float(round(hi * k / 100))
    if kind == "fine":
        return round(hi * k / 100, 1)
    if kind in ("percent", "bend"):
        return k / 100
    if kind == "height":  # (whole %; 100 % in the middle)
        return round(float(height_times(k / 100)), 2)
    x = lo + (hi - lo) * k / 100
    if kind in COUNTS:
        return float(round(x))
    return x / 100 if kind in PERCENTS else round(x, 2)


def shown(kind, v):
    return 100 * v if kind in PERCENTS else v
