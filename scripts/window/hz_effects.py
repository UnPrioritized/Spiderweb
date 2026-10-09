"""The effects pane of the Hz bass window (hzbass.py "Effects"): under the notes, in step with their time.

On the left the effects, each in its own colour. Clicking a name puts that effect on the Hz bass: one line over all
the notes, through points at beats, 0 % at the bottom, 100 % at the top (flat before the first point and after the
last). Clicking a name that's on highlights it: its line is in full colour and the only one that can be grabbed, the
others are faint (a press on a faint line adds a point to the highlighted one); with none highlighted they're all
in full colour and the nearest one is grabbed.
Drag a point to move it, press on the line for a new point, double click a point to delete it (the last point
takes the effect off). Every change is one undo step of the main window, made when the mouse is let go.
Right-click > Repeat every…: a small window to type how long one repeat is (green lines in the pane show where
each one would start while it's open); the effect's line becomes one repeat of that length, shown over and over
(any copy grabbed changes them all; too close together to grab: a band); Shape: ready-made ones for a repeat.
Its Plays dropdown: All the way (from the shape's start) / Once per note / Restart at each note (hz["from"]), and
Stretch to each note (hz["fit"]): such a line is drawn from each note's start, cut where the next note starts
(copies_of; a copy's number is then (note start's number, repeat after it)).
Ctrl+drag (with Select: a plain drag on empty space too) = a box that selects points (Ctrl+click one: in / out); dragging a selected point moves them all, Delete
deletes them (none selected, the pane pressed last: the highlighted effect is taken off), Ctrl+C / Ctrl+V copy them and paste them at the mouse (into the highlighted effect, or the ones they
came from; the points already there are replaced). The small square before a name that's on switches the effect off
and on again (Bypass: its line is kept).
A repeating effect can have an amount line (right-click > Amount line): dotted, over the notes, how strong the
repeat is. The window's lines are keyed "<effect>" and "<effect>:amount" (AMOUNT) in win.fxl.
Pencil tool + an effect highlighted: a drag on empty space draws its line (snap on: a step in every grid cell, like
drawing velocities; Shift or snap off: a smooth line); a plain click there still clears the highlight.
Its top edge drags to make the pane taller or shorter (remembered).
Moving dots (draw_dots, like a synth's envelope view): while the preview plays, a dot on each line where the play
line crosses it; while a live key sounds (hz_live.py), a dot on each line counted from each note, on the first
note in view: from its start, stopping at the sustain point while the key is held, down the fall once let go."""

import copy
import math
from fractions import Fraction
import random
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc
from files.snap import SNAPS, snap_beats, snap_text
from notes.hzbass import (BEND, ENVELOPES, FROM_MODES, FX, FX_START, LOOP_SHAPES, OFF_PITCH, PITCH, TREMOLO, VIBRATO, bend_of, bent_part,
                          chains, group_count, legato_links, line_at,
                          loop_off, loop_on, loop_shape, sound_span, sustained, tones_span)
from roll.roll_shared import BOX_STILL, CTRL, SELECT_CURSOR, SHIFT
from window import look
from window.widgets import Scrub, remember_place

# (not orange, red, green or blue: selected notes, the red line, the exact tone, notes)
REPEAT_MOST = 999  # Repeat every… [n] / [n]: the biggest number in either box
REPEAT_LINE = look.HZ_GREEN  # where each repeat would start while the Repeat every… window is open
PLAY_LINE = look.PLAY_LINE  # the preview's play line (as in the notes above)
FX_COLOR = {"volume": "#9b2d5f", "slant": "#8a3ff0", "groups": "#0a8f8f", "offpitch": "#d0189a", "noisy": "#8a5a14",
            "vibrato": "#00a5d8", "pitch": "#4b0082", "sweep": "#7f8c00", "wah": "#2c3e6b", "tremolo": "#e0607a",
            "octave": "#1d6b3a", "sine": "#b060c0", "square": "#606060", "saw": "#c0a000", "triangle": "#c05a30"}
FX_COLOR = {k: look.readable(v) for k, v in FX_COLOR.items()}  # (lighter in the dark look)


AMOUNT = ":amount"  # (the window's key for an effect's amount line: effect + AMOUNT)


def base(key):
    """The effect a line belongs to ("volume:amount" -> "volume")."""
    return key.split(":")[0]


class _NoKeys:
    state = 0


class _At:
    """A mouse spot for on_drag (x, y, keys held)."""

    def __init__(self, x, y, state):
        self.x, self.y, self.state = x, y, state


def faint(colour, by=0.6):
    """colour mixed with white."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(v + (255 - v) * by) for v in (r, g, b))


class FxPane:
    # the pane's colours (the synth window's dark look has its own: hz_synth.LOOK)
    LOOK = {"bg": look.FX_BG, "grid": look.FX_GRID, "outside": look.FX_OUTSIDE, "notes": look.FX_NOTES,
            "hint": look.HINT, "repeat": look.FX_REPEAT, "names": look.FX_NAMES, "names_dim": 0.5,
            "text": look.FX_TEXT, "edge": look.FX_EDGE, "box": look.FX_BOX, "point": look.FX_POINT}

    def __init__(self, win):
        self.win, self.s = win, win.s
        self.active = None  # the effect highlighted
        self.drag = None
        self.sel = set()  # the points selected: (effect, number)
        self.clip = None  # points copied: {effect: [[beats after the first one, value(, bend)], ...]}
        self.mouse_x = None  # where the mouse is over the pane (for pasting)
        self.pressed = False  # the pane was pressed last (not the notes): Delete with no points selected = the effect
        self.asking = None  # the Repeat every… window, while it's open
        self.trying = None  # the length typed in it (beats), shown as green lines
        self.trying_how = (None, False)  # ... and how it's picked to play there (hz["from"] mode, stretched)
        self.says = ""  # for the window's status line
        self.drawn = {}  # each line's [(x, y)] as last drawn (the moving dots sit on them)
        self.dots = None  # the moving dots as drawn
        self.row_h, self.pad = round(15 * self.s), round(9 * self.s)
        self.edge = round(4 * self.s)  # the top edge: drag it = the pane's height
        start_h = max(round(112 * self.s), round(8 * self.s) + len(FX) * self.row_h)
        c = self.canvas = tk.Canvas(win, background=self.LOOK["bg"], highlightthickness=0,
                                    height=win.app.hz_fx_h or start_h)
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-3>", self.on_menu)
        c.bind("<Motion>", self.on_motion)
        c.bind("<Leave>", lambda e: setattr(self, "mouse_x", None) or self.say(""))
        c.bind("<MouseWheel>", self.on_wheel)

    # ------------------------------------------------------------ what it shows

    def y_of(self, value):
        h = self.canvas.winfo_height()
        return self.pad + (1.0 - value) * (h - 2 * self.pad)

    def value_at(self, y):
        h = self.canvas.winfo_height()
        return min(1.0, max(0.0, 1.0 - (y - self.pad) / max(1, h - 2 * self.pad)))

    def note_spans(self):
        """[(start, end)] beats of each note (chain of slides; chains starting together: the longest), in order."""
        spans = {}
        tones = self.win.tones
        for s, e in chains(tones, legato_links(self.win.extra.get("voice"), tones)).values():
            spans[s] = max(spans.get(s, e), e)
        return sorted(spans.items())

    def note_starts(self, every, fit):
        """[(beat, how many times one repeat is stretched)] where each note (chain of slides) starts, in order, for
        an effect counted from each note (hz["from"]); fit = stretched over each note. No notes: from beat 0."""
        spans = self.note_spans()
        if not spans:
            return [(0.0, 1.0)]
        return [(s, max(e - s, 1e-9) / every if fit else 1.0) for s, e in spans]

    def sustain(self, name):
        """The effect's sustain point (beats into its repeat, hz["sustain"]), or None."""
        return self.win.sustains.get(name) if self.win.froms.get(name) == "note" else None

    def release(self, name, k):
        """For copy k of an effect with a sustain point: (beat its note ends at, beat its fall starts at: the note's
        end, or where it reaches the sustain point if that's later, as it's drawn); None without one."""
        at = self.sustain(name)
        if at is None or not isinstance(k, tuple):
            return None
        spans = self.note_spans()
        if not spans:
            return math.inf, math.inf
        s, e = spans[min(k[0], len(spans) - 1)]
        return e, max(e, s + at)

    def to_beat(self, name, k, p):
        """Where a point p beats into repeat k is drawn: from the copy's start, or (past a sustain point) from where
        its fall starts."""
        o, sc = self.place(name, k)
        rel, at = self.release(name, k), self.sustain(name)
        if rel is not None and p > at + 1e-12:
            return rel[1] + p - at
        return o + p * sc

    def from_beat(self, name, k, beat, fall=None):
        """beat as beats into repeat k (to_beat undone), within the repeat; fall = True / False: on the part after
        the sustain point or before it (None: whichever beat is on; between them: the sustain point)."""
        o, sc = self.place(name, k)
        every, rel, at = self.win.loops[name], self.release(name, k), self.sustain(name)
        if rel is not None:
            if fall is None:
                if o + at < beat < rel[1]:
                    return at
                fall = beat >= rel[1]
            if fall:
                return min(every, at + max(0.0, beat - rel[1]))
        return min(every, max(0.0, (beat - o) / sc))

    def copies_of(self, every, mode=None, fit=False):
        """The repeats in view of a line repeating every `every` beats: [(number of the repeat, beat it starts at,
        times stretched, beat it's cut at)], mode / fit like hz["from"] / hz["fit"] (each note's own repeats,
        starting at each note; the last point stays until the next note). None when they're too close together to
        show one by one (drawn as a band then, not grabbable)."""
        win = self.win
        a, b = win.beat_at(win.kb_w), win.beat_at(self.canvas.winfo_width())
        if not mode:
            if every * win.sx < 4 * self.s:
                return None
            return [(k, k * every, 1.0, (k + 1) * every) for k in range(math.floor(a / every) - 1,
                                                                        math.floor(b / every) + 2)]
        st = self.note_starts(every, fit)
        if min(every * sc for _, sc in st) * win.sx < 4 * self.s:
            return None
        out = []
        for i, (s, sc) in enumerate(st):
            nxt = st[i + 1][0] if i + 1 < len(st) else math.inf
            if nxt < a or s > b:
                continue
            if mode == "note":
                out.append(((i, 0), s, sc, nxt))
                continue
            w = every * sc
            for j in range(max(0, math.floor((a - s) / w)), math.floor((min(b, nxt) - s) / w) + 1):
                if s + j * w < nxt:
                    out.append(((i, j), s + j * w, sc, min(s + (j + 1) * w, nxt)))
            if len(out) > 4000:
                return None
        return out

    def copies(self, name):
        """copies_of the effect as it repeats now, or None when it doesn't repeat (or they're too close together)."""
        every = self.win.loops.get(name)
        if not every:
            return None
        return self.copies_of(every, self.win.froms.get(name), name in self.win.fits)

    def place(self, name, k):
        """(beat, times stretched) where repeat k of a repeating effect starts."""
        every = self.win.loops[name]
        if isinstance(k, tuple):  # (counted from each note: (note start's number, repeat after it))
            st = self.note_starts(every, name in self.win.fits)
            s, sc = st[min(k[0], len(st) - 1)]
            return s + k[1] * every * sc, sc
        return k * every, 1.0

    def copy_at(self, name, beat):
        """The number of the repeat of a repeating effect that beat is in."""
        win, every = self.win, self.win.loops[name]
        mode = win.froms.get(name)
        if not mode:
            return math.floor(beat / every)
        st = self.note_starts(every, name in win.fits)
        i = max(0, sum(1 for s, _ in st if s <= beat + 1e-9) - 1)
        s, sc = st[i]
        return i, (math.floor(max(0.0, beat - s) / (every * sc)) if mode == "restart" else 0)

    def local(self, name, beat, k):
        """beat as beats into repeat k of a repeating effect (its points' beats), within the repeat."""
        return self.from_beat(name, k, beat)

    def wrap(self, name):
        """The `every` for line_at: the repeat's length when the last point leads on to the next repeat's first
        (not once per note: it stays on its last value)."""
        return None if self.win.froms.get(name) == "note" else self.win.loops.get(name)

    def points(self, name):
        """[(x, y, number of the point, number of the repeat)] of an effect's points on screen (a repeating one's in
        every repeat in view, when they're far enough apart to grab)."""
        win, pts = self.win, self.win.fxl[name]
        got = self.copies(name)
        if got is None:
            return [] if name in win.loops else [(win.x_of(p[0]), self.y_of(p[1]), i, 0) for i, p in enumerate(pts)]
        every = win.loops[name]
        out = []
        for k, o, sc, until in got:
            if every * sc * win.sx >= 24 * self.s:
                for i, p in enumerate(pts):
                    b = self.to_beat(name, k, p[0])
                    if b <= until + 1e-9:
                        out.append((win.x_of(b), self.y_of(p[1]), i, k))
        return out

    def segments(self, name):
        """[(from beat, value, to beat, value, bend, number of the point it starts at, number of the repeat)]: the
        lines between an effect's points in view (a repeating one: every repeat's, the last one leading to the next
        repeat's first point; counted from each note: only the whole ones before the next note)."""
        pts = self.win.fxl[name]
        got = self.copies(name)
        if got is None:
            return [(a[0], a[1], b[0], b[1], bend_of(a), i, 0) for i, (a, b) in enumerate(zip(pts, pts[1:]))]
        every, wrap, out = self.win.loops[name], self.wrap(name), []
        for k, o, sc, until in got:
            for i, a in enumerate(pts):
                if i + 1 < len(pts):
                    b, ob = pts[i + 1], o
                elif wrap:
                    b, ob = pts[0], o + every * sc
                else:
                    continue
                b0 = self.to_beat(name, k, a[0])
                b1 = self.to_beat(name, k, b[0]) if ob == o else ob + b[0] * sc
                rel = self.release(name, k)
                if rel is not None and abs(a[0] - self.sustain(name)) < 1e-9:  # (from the sustain point: the
                    b0 = rel[1]  # fall, after the note)
                if b1 <= until + 1e-9 or not self.win.froms.get(name):
                    out.append((b0, a[1], b1, b[1], bend_of(a), i, k))
        return out

    def sampled(self, name, got):
        """[(x, y)] of a line counted from each note (hz["from"]): worked out every few pixels in each repeat (and
        at each point), so it's cut where the next note starts."""
        win, pts, wrap = self.win, self.win.fxl[name], self.wrap(name)
        w, step, xy = self.canvas.winfo_width(), 2 * self.s, []
        for n, (k, o, sc, until) in enumerate(got):
            x0 = win.x_of(o) if n else min(win.kb_w, win.x_of(o))
            x1 = min(w, win.x_of(until)) if until < math.inf else w
            if x1 <= x0:
                continue
            xs = set(np.arange(x0, x1, step).tolist()) | {x1}
            for p in pts:
                x = win.x_of(o + p[0] * sc)
                if x0 <= x <= x1:
                    xs |= {x, max(x0, x - 0.01)}
            rel = self.release(name, k)
            if rel is not None:  # (with a sustain point: as it plays, the note's length known; its points past
                for p in pts:  # the sustain point from where the fall starts)
                    x = win.x_of(self.to_beat(name, k, p[0]))
                    if x0 <= x <= x1:
                        xs |= {x, max(x0, x - 0.01)}
                xs = sorted(xs)
                vs = sustained(pts, win.loops[name], self.sustain(name),
                               np.array([win.beat_at(x) for x in xs]) - o, rel[0] - o)
                xy += [(x, self.y_of(v)) for x, v in zip(xs, vs.tolist())]
                continue
            xs = sorted(xs)
            u = np.maximum(0.0, (np.array([win.beat_at(x) for x in xs]) - o) / sc)
            xy += [(x, self.y_of(v)) for x, v in zip(xs, line_at(pts, np.minimum(u, win.loops[name] - 1e-12)
                                                                 if wrap else u, wrap).tolist())]
        return xy

    def line(self, name):
        """[(x, y)] of an effect's line: through its points, bent where they say, and flat out to both sides of the
        pane (a repeating one: every repeat in view)."""
        win, pts = self.win, self.win.fxl[name]
        if win.froms.get(name):
            got = self.copies(name)
            if got:
                return self.sampled(name, got)
        xy = []
        for b0, v0, b1, v1, bend, _, _ in self.segments(name):
            x0, x1 = win.x_of(b0), win.x_of(b1)
            if math.isnan(bend):  # hold: flat, then a step
                xy += [(x0, self.y_of(v0)), (x1, self.y_of(v0))]
            elif bend and x1 - x0 > 2:
                n = max(4, min(48, int((x1 - x0) / (3 * self.s))))
                u = np.arange(n) / n
                xy += [(x0 + (x1 - x0) * a, self.y_of(v0 + (v1 - v0) * g)) for a, g in zip(u, bent_part(u, bend))]
            else:
                xy.append((x0, self.y_of(v0)))
        if name in win.loops:
            return xy
        xy.append((win.x_of(pts[-1][0]), self.y_of(pts[-1][1])))
        return [(min(win.kb_w, xy[0][0]), xy[0][1])] + xy + [(max(self.canvas.winfo_width(), xy[-1][0]), xy[-1][1])]

    def handles(self, name):
        """[(x, y, number of the point the line starts at, number of the repeat)]: the round handles in the middle of
        the lines that can be bent (wide enough on screen, going up or down, not held)."""
        win, out = self.win, []
        if name in win.loops and not self.points(name):
            return out
        for b0, v0, b1, v1, bend, i, k in self.segments(name):
            if (win.x_of(b1) - win.x_of(b0) >= 16 * self.s and abs(v1 - v0) > 0.005 and not math.isnan(bend)):
                out.append((win.x_of((b0 + b1) / 2), self.y_of(v0 + (v1 - v0) * (1 + bend) / 2), i, k))
        return out

    def now(self):
        """The lines, repeats, effects switched off, those counted from each note, those stretched, their sustain
        points and the synth window's vibrato / tremolo and Voice settings, as they are now."""
        return (self.win.fxl, self.win.loops, self.win.off, self.win.froms, self.win.fits, self.win.sustains,
                self.win.lfo, self.win.extra)

    def hz_now(self):
        """The notes and effects here as a Hz bass's settings (enough for the hzbass functions)."""
        win = self.win
        return {"tones": win.tones, "fx": {k: v for k, v in win.fxl.items() if k in FX}, "loop": win.loops,
                "off": win.off, "from": win.froms, "fit": win.fits, "sustain": win.sustains, "lfo": win.lfo, **win.extra}

    def tidy(self):
        """A sustain point always sits on a point of its line: one put back where it is when its point went (drawn
        over with the pencil)."""
        for name, at in list(self.win.sustains.items()):
            pts = self.win.fxl.get(name)
            if pts and not any(abs(p[0] - at) < 1e-9 for p in pts):
                self.add_point(pts, at)

    def state(self):
        """now(), kept (to go back to)."""
        return copy.deepcopy(self.now())

    def names(self):
        """The lines there are, in FX order (an effect's amount line right after it)."""
        return [k for name in FX for k in (name, name + AMOUNT) if k in self.win.fxl]

    def grabbable(self):
        """The lines that can be grabbed: the highlighted effect's, or all when none is highlighted."""
        return [k for k in self.names() if self.active in (None, base(k))]

    def colour(self, name):
        """An effect's colour on this pane."""
        return FX_COLOR[name]

    def faint(self, colour, by=0.6):
        """A colour paled toward the pane's background."""
        return faint(colour, by)

    def redraw(self):
        c, win, s, pal = self.canvas, self.win, self.s, self.LOOK
        c.delete("all")
        self.drawn = {}
        w, h, kb = c.winfo_width(), c.winfo_height(), win.kb_w
        if w < 50 or h < 20:
            return
        fx = win.fxl
        self.sel = {(n, i) for n, i in self.sel if n in fx and i < len(fx[n])}
        for v in (0.0, 0.5, 1.0):
            c.create_line(kb, self.y_of(v), w, self.y_of(v), fill=pal["grid"])
        if win.tones:  # before and after the notes (and the falls after them): grey
            x0, x1 = max(kb, win.x_of(0.0)), max(kb, win.x_of(sound_span(self.hz_now())))
            for a, b in ((kb, x0), (x1, w)):
                if b > a:
                    c.create_rectangle(a, 0, b, h, fill=pal["outside"], outline="")
        for n in win.tones:  # where the notes are, faintly
            c.create_rectangle(max(kb, win.x_of(n["t"])), h - 3 * s, max(kb, win.x_of(n["t"] + n["len"])), h,
                               fill=pal["notes"], outline="")
        if not fx:
            c.create_text((kb + w) / 2, h / 2, text=tr("hz.fx_hint"), fill=pal["hint"], width=w - kb - 40 * s,
                          justify="center")
        order = sorted(self.names(), key=lambda k: base(k) == self.active)  # (the highlighted one on top)
        trying = self.copies_of(self.trying, *self.trying_how) if self.trying else None
        for _, o, _, _ in trying or ():  # Repeat every… being typed: where each would start
            x = win.x_of(o)
            if x > kb:
                c.create_line(x, 0, x, h, fill=REPEAT_LINE, width=max(1, round(1.5 * s)))
        for _, o, _, _ in (self.copies(self.active) or () if self.active in win.loops else ()):  # where each
            x = win.x_of(o)  # repeat starts
            if x > kb:
                c.create_line(x, 0, x, h, fill=pal["repeat"], dash=(2, 3))
        if self.sustain(self.active) is not None:  # where each note ends: its fall starts there
            for k, _, _, _ in self.copies(self.active) or ():
                x = win.x_of(self.release(self.active, k)[1])
                if kb < x < w:
                    c.create_line(x, 0, x, h, fill=self.faint(self.colour(self.active), 0.3), dash=(4, 3))
        for name in order:
            lit = self.active in (None, base(name))
            colour = self.colour(base(name)) if lit else self.faint(self.colour(base(name)))
            off = base(name) in win.off  # (switched off: dashed; an amount line: dotted)
            if name in win.loops and self.copies(name) is None:  # repeats too close together: a band
                vs = [p[1] for p in fx[name]]
                c.create_rectangle(kb, self.y_of(max(vs)), w, self.y_of(min(vs)) + 1, fill=colour, outline="",
                                   stipple="gray25")
                continue
            xy = self.line(name)
            self.drawn[name] = xy
            c.create_line(*[v for p in xy for v in p], fill=colour, width=max(2, round(2 * s)) if lit else 1,
                          dash=(6, 4) if off else (2, 3) if name.endswith(AMOUNT) else ())
            if lit:
                r = 3.5 * s
                at = self.sustain(name)
                for x, y, i, _ in self.points(name):
                    fill = colour if (name, i) in self.sel else pal["point"]
                    if at is not None and abs(fx[name][i][0] - at) < 1e-9:  # the sustain point: a diamond
                        q = r * 1.5
                        c.create_polygon(x, y - q, x + q, y, x, y + q, x - q, y, fill=fill, outline=colour,
                                         width=max(1, round(1.5 * s)))
                        continue
                    c.create_rectangle(x - r, y - r, x + r, y + r, fill=fill, outline=colour,
                                       width=max(1, round(1.5 * s)))
                r = 3 * s
                for x, y, _, _ in self.handles(name):
                    c.create_oval(x - r, y - r, x + r, y + r, fill=pal["point"], outline=colour, width=1)
        c.create_rectangle(0, 0, kb, h, fill=pal["names"], outline="")  # the effects
        for i, name in enumerate(FX):
            y = 4 * s + i * self.row_h + self.row_h / 2
            lit = self.active in (None, name)
            colour = self.colour(name) if lit else self.faint(self.colour(name), pal["names_dim"])
            r = 3 * s
            on = name in fx and name not in win.off
            c.create_rectangle(4 * s, y - r, 4 * s + 2 * r, y + r, outline=colour, fill=colour if on else "")
            if name in win.off:  # (switched off: crossed out)
                c.create_line(4 * s, y + r, 4 * s + 2 * r, y - r, fill=colour)
            c.create_text(8 * s + 2 * r, y, text=tr("hz.fx_" + name), anchor="w", fill=colour,
                          font=look.font(8, "bold" if self.active == name else "normal"))
        for v in (0.0, 0.5, 1.0):  # what the heights mean
            c.create_text(w - 4 * s, self.y_of(v), text=f"{v * 100:.0f} %", anchor="e", fill=pal["text"],
                          font=look.font(7))
        c.create_line(kb, 0, kb, h, fill=pal["edge"])
        c.create_line(0, 0, w, 0, fill=pal["edge"])
        d = self.drag
        if d and d["kind"] == "box" and "x1" in d:
            c.create_rectangle(d["x0"], d["y0"], d["x1"], d["y1"], outline=pal["box"], dash=(3, 3))
        self.dots = None
        self.draw_dots()

    def y_on(self, name, x):
        """The height of a line as drawn at x (None: not drawn there)."""
        xy = self.drawn.get(name)
        if not xy or not xy[0][0] <= x <= xy[-1][0]:
            return None
        return float(np.interp(x, [p[0] for p in xy], [p[1] for p in xy]))

    def draw_dots(self):
        """The moving dots (see the top): drawn again only when they moved."""
        c, win, s = self.canvas, self.win, self.s
        if not c.winfo_ismapped():
            return
        w, h, kb = c.winfo_width(), c.winfo_height(), win.kb_w
        lit = [k for k in self.names() if self.active in (None, base(k)) and base(k) not in win.off]
        got = []
        x = self.play_x()
        if x is not None and kb <= x <= w:  # the play line, and where it crosses each line
            got.append(("line", round(x)))
            got += [(k, round(x), round(y)) for k in lit for y in [self.y_on(k, x)] if y is not None]
        pos = win.live.position()  # (beats since the live note started, beats it was let go at / None)
        if pos is not None:
            for name in lit:
                at = self.live_spot(name, *pos)
                if at is not None and kb <= at[0] <= w:
                    got.append((name, round(at[0]), round(at[1])))
        if got == self.dots:
            return
        self.dots = got
        c.delete("dot")
        r = 4 * s
        for g in got:
            if g[0] == "line":
                c.create_line(g[1], 0, g[1], h, fill=PLAY_LINE, width=max(1, round(s)), tags="dot")
            else:
                c.create_oval(g[1] - r, g[2] - r, g[1] + r, g[2] + r, fill=self.colour(base(g[0])), outline=self.LOOK["point"],
                              width=max(1, round(1.5 * s)), tags="dot")

    def play_x(self):
        """x of the preview's play line while it plays, else None."""
        p = self.win.preview
        return self.win.x_of(p.play_beat()) if p.playing() else None

    def live_spot(self, name, u, gone):
        """Where the live note is on an effect counted from each note: (x, y) on the first note in view, u beats
        after its start, let go at `gone` beats (None: still held); None for other lines."""
        win = self.win
        every, mode = win.loops.get(name), win.froms.get(name)
        if not every or not mode or name.endswith(AMOUNT):
            return None
        at = self.sustain(name)
        if mode == "restart":
            p = u % every
        elif at is not None and gone is None:  # (held: up to the sustain point, then it waits there)
            p = min(u, at)
        elif at is not None:  # (let go: the fall, from the sustain point)
            p = min(every, at + max(0.0, u - gone))
        else:
            p = min(u, every)
        got = self.copies(name) or []
        first = next(((k, o) for k, o, _, _ in got if win.x_of(o) >= win.kb_w), got[0][:2] if got else None)
        if first is None:
            return None
        k = first[0]
        if at is not None and gone is not None and p > at:  # (on the fall part of that copy)
            x = win.x_of(self.to_beat(name, k, p))
        else:
            o, sc = self.place(name, k)
            x = win.x_of(o + p * sc)
        v = line_at(win.fxl[name], p, every if mode == "restart" else None)
        return x, self.y_of(float(v))

    def say(self, text):
        if text != self.says:
            self.says = text
            self.win.show_status()

    # ------------------------------------------------------------ mouse

    def name_at(self, x, y):
        """The effect whose name is under the mouse (the left column), or None."""
        i = int((y - 4 * self.s) // self.row_h)
        return FX[i] if x < self.win.kb_w and y >= 4 * self.s and 0 <= i < len(FX) else None

    def on_box(self, x):
        """True when x is on the small squares before the names (switch an effect off / on)."""
        return x < 11 * self.s

    def hit(self, x, y):
        """("point", effect, number, number of the repeat) / ("bend", effect, number of the point the line starts at,
        number of the repeat) / ("line", effect) under the mouse, or None."""
        if x < self.win.kb_w:
            return None
        r = 6 * self.s
        names = self.grabbable()[::-1]
        for name in names:
            for px, py, i, k in self.points(name):
                if abs(x - px) <= r and abs(y - py) <= r:
                    return "point", name, i, k
        for name in names:
            for px, py, i, k in self.handles(name):
                if abs(x - px) <= r and abs(y - py) <= r:
                    return "bend", name, i, k
        for name in names:
            if self.on_line(name, x, y):
                return "line", name
        return None

    def on_line(self, name, x, y):
        """True when (x, y) is on the effect's line (never on a repeating one too close together to grab)."""
        if name in self.win.loops and not self.points(name):
            return False
        xy, r = self.line(name), 6 * self.s
        for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
            if x0 <= x <= x1 and abs(y - (y0 if x1 - x0 < 1e-9 else y0 + (y1 - y0) * (x - x0) / (x1 - x0))) <= r:
                return True
        return False

    def on_faint(self, x, y):
        """The highlighted effect when (x, y) is on another effect's faint line (a press there adds a point to the
        highlighted one, user), else None."""
        name = self.active
        if name not in self.win.fxl or x < self.win.kb_w or (name in self.win.loops and not self.points(name)):
            return None
        return name if any(self.on_line(k, x, y) for k in self.names() if base(k) != name) else None

    def on_motion(self, e):
        self.mouse_x = e.x
        if e.y < self.edge:
            self.canvas.config(cursor="sb_v_double_arrow")
            return self.say("")
        name, hit = self.name_at(e.x, e.y), self.hit(e.x, e.y)
        draws = (hit is None and e.x >= self.win.kb_w and self.active in self.win.fxl and
                 self.win.tool.get() == "pencil" and not e.state & CTRL)
        self.canvas.config(cursor="hand2" if name else "fleur" if hit and hit[0] == "point" else
                           "sb_v_double_arrow" if hit and hit[0] == "bend" else "crosshair" if hit else
                           self.win.pencil if draws else
                           SELECT_CURSOR if e.x >= self.win.kb_w and self.win.tool.get() == "select" else "")
        if name:
            every = self.win.loops.get(name)
            self.say(tr("hz.fx_%s_tip" % name) + (" " + self.repeat_text(name, "hz.fx_repeats") if every else "") +
                     (" " + tr("hz.fx_is_off") if name in self.win.off else
                      " " + tr("hz.fx_box_tip") if name in self.win.fxl and self.on_box(e.x) else ""))
        elif hit and hit[0] == "point":
            p, at = self.win.fxl[hit[1]][hit[2]], self.sustain(hit[1])
            self.say(self.value_text(hit[1], p[1], self.pitch_keys()) + (" " + tr("hz.fx_sustain_tip")
                                                       if at is not None and abs(p[0] - at) < 1e-9 else ""))
        elif hit and hit[0] == "bend":
            self.say(tr("hz.fx_bend_tip"))
        else:
            self.say("")

    def pitch_keys(self):
        """Keys the Pitch line bends at its top and bottom (the Pitch box's Range)."""
        return self.win.lfo.get("bend_range", PITCH)

    @staticmethod
    def value_text(name, value, keys=PITCH):
        if name.endswith(AMOUNT):
            return tr("hz.fx_value_amount", name=tr("hz.fx_" + base(name)), value=f"{value * 100:.4g}")
        if name == "groups":
            n = int(group_count(value))
            return tr("hz.fx_value_groups" if n > 1 else "hz.fx_value_together", name=tr("hz.fx_" + name), n=n)
        if name == "tremolo":
            return tr("hz.fx_value_beat", name=tr("hz.fx_" + name), n=f"{value * TREMOLO:.3g}")
        if name == "pitch":
            return tr("hz.fx_value_keys", name=tr("hz.fx_" + name), n=f"{(value - 0.5) * 2 * keys:+.3g}")
        if name in ("offpitch", "vibrato"):  # how far apart the lowest and the highest key's tones are / how far
            most = OFF_PITCH if name == "offpitch" else VIBRATO  # the pitch goes up and down
            return tr("hz.fx_value", name=tr("hz.fx_" + name), value=f"{value * most * 100:.3g}")
        return tr("hz.fx_value", name=tr("hz.fx_" + name), value=f"{value * 100:.4g}")

    def on_press(self, e):
        win = self.win
        win.canvas.focus_set()
        self.drag, self.pressed = None, True
        if e.y < self.edge:
            self.drag = {"kind": "size", "y": e.y_root, "h": self.canvas.winfo_height()}
            return
        name = self.name_at(e.x, e.y)
        if name:
            self.drag = {"kind": "name", "fx": name, "box": self.on_box(e.x)}
            return
        hit = self.hit(e.x, e.y)
        if e.state & CTRL and e.x >= win.kb_w and (hit is None or hit[0] == "line"):  # a box selecting points
            self.drag = {"kind": "box", "x0": e.x, "y0": e.y, "had": set(self.sel)}
            return
        faint = self.on_faint(e.x, e.y) if hit is None else None
        if hit is None and not faint and e.x >= win.kb_w and win.tool.get() == "select":  # Select: a box without
            self.drag = {"kind": "box", "x0": e.x, "y0": e.y, "had": set(), "plain": True}  # Ctrl too (user: missed
            return  # Ctrl+drag); it selects anew, a click without a drag selects / highlights nothing
        if hit is None and e.x >= win.kb_w and self.active in win.fxl and win.tool.get() == "pencil":
            every = win.loops.get(self.active)  # (a drag draws; a click clears the highlight on release, or on a
            self.drag = {"kind": "draw", "fx": self.active, "x0": e.x, "y0": e.y, "moved": False,  # faint line
                         "faint": faint,  # adds a point there)
                         "before": self.state(), "orig": copy.deepcopy(win.fxl[self.active]), "got": {},
                         "k": self.copy_at(self.active, max(0.0, win.beat_at(e.x))) if every else 0}
            return
        if faint:  # another effect's faint line: a new point on the highlighted one, at the mouse
            hit = "line", faint
        if hit is None:
            if e.x >= win.kb_w and (self.active is not None or self.sel):  # a click on nothing: nothing
                self.active, self.sel = None, set()  # highlighted or selected
                win.redraw()
            return
        name = hit[1]
        before = self.state()
        if hit[0] == "point" and e.state & CTRL:  # Ctrl+click: in / out of the selection
            self.sel ^= {(name, hit[2])}
            return win.redraw()
        if hit[0] == "bend":
            self.drag = {"kind": "bend", "fx": name, "i": hit[2], "before": before}
            return
        every = win.loops.get(name)
        if hit[0] == "line":  # a new point where the line was pressed (a repeating one: in that repeat)
            beat = max(0.0, win.beat_at(e.x))
            k = self.copy_at(name, beat) if every else 0
            i = self.add_point(win.fxl[name], self.local(name, beat, k) if every else beat, self.wrap(name))
        else:
            i, k = hit[2], hit[3]
        if hit[0] == "line" or (name, i) not in self.sel:
            self.sel = {(name, i)}
        self.drag = {"kind": "point", "fx": name, "i": i, "k": k, "before": before,
                     "orig": {(n, j): tuple(win.fxl[n][j][:2]) for n, j in self.sel}}
        if hit[0] == "line":
            self.on_drag(e)
            self.canvas.config(cursor="fleur")  # (the new point is under the mouse: it moves)
        win.redraw()

    def click_point(self, name, x, y, e):
        """A new point on the effect at (x, y), one undo step (the pencil's click on a faint line)."""
        win = self.win
        before = self.state()
        every = win.loops.get(name)
        beat = max(0.0, win.beat_at(x))
        k = self.copy_at(name, beat) if every else 0
        i = self.add_point(win.fxl[name], self.local(name, beat, k) if every else beat, self.wrap(name))
        self.sel = {(name, i)}
        self.drag = {"kind": "point", "fx": name, "i": i, "k": k, "before": before,
                     "orig": {(name, i): tuple(win.fxl[name][i][:2])}}
        self.on_drag(_At(x, y, e.state))
        self.drag = None
        self.says = ""
        win.commit_fx(before)

    @staticmethod
    def add_point(pts, beat, every=None):
        """A point put on a line at beat (the line keeps its shape; every = it repeats, beat is in one repeat); its
        number."""
        i = sum(1 for p in pts if p[0] <= beat)
        on = pts[i - 1] if i else pts[-1] if every else pts[0]  # (the point the line it's put on starts at)
        pts.insert(i, [beat, float(line_at(pts, beat, every)), *on[2:]])  # (both halves bent the same)
        return i

    def on_drag(self, e):
        d, win = self.drag, self.win
        if d and d["kind"] == "size":  # the pane's height (the notes keep some room)
            least = round(50 * self.s)
            most = max(least, win.canvas.winfo_height() + self.canvas.winfo_height() - round(120 * self.s))
            win.app.hz_fx_h = min(most, max(least, d["h"] - (e.y_root - d["y"])))
            self.canvas.config(height=win.app.hz_fx_h)
            return
        if d and d["kind"] == "bend":
            return self.drag_bend(d, e)
        if d and d["kind"] == "box":
            d["x1"], d["y1"] = e.x, e.y
            return self.redraw()
        if d and d["kind"] == "draw":
            return self.draw_at(d, e)
        if not d or d["kind"] != "point":
            return
        name, i = d["fx"], d["i"]
        every = win.loops.get(name)
        at = win.snap(win.beat_at(e.x), e)
        d.setdefault("sustains", dict(win.sustains))
        if every:  # (a repeat: all of them move)
            o, sc = self.place(name, d["k"])
            rel, sus = self.release(name, d["k"]), d["sustains"].get(name)
            if rel is not None and d["orig"][(name, i)][0] > sus + 1e-12:  # (past the sustain point: the grid
                at = sus + win.snap(win.beat_at(e.x) - rel[1], e)  # from where the fall starts)
            else:
                if win.froms.get(name):  # (counted from each note: the grid from the note's start)
                    at = win.snap(win.beat_at(e.x) - o, e) + o
                at = (at - o) / sc
        v = self.value_at(e.y)
        if name == "pitch" and not e.state & SHIFT:  # (whole keys; Shift = in between)
            v = 0.5 + round((v - 0.5) * 2 * self.pitch_keys()) / (2 * self.pitch_keys())
        v = round(v, 3 if e.state & SHIFT else 2 if name != "pitch" else 6)
        b0, v0 = d["orig"][(name, i)]
        db, dv = self.limited(d["orig"], at - b0, v - v0)
        for (n, j), (b, u) in d["orig"].items():
            win.fxl[n][j][0], win.fxl[n][j][1] = b + db, round(u + dv, 6)
            was = d["sustains"].get(n)
            if was is not None and abs(b - was) < 1e-9:  # (the sustain point goes with its point)
                win.sustains[n] = b + db
        self.says = self.value_text(name, v0 + dv, self.pitch_keys())
        win.redraw()

    def limited(self, orig, db, dv):
        """A move of the points (orig: {(effect, number): (beat, value)}) by db beats and dv, made smaller where it
        would take one past a point that isn't moving (or past the start / the end of a repeat, or out of 0..1)."""
        win = self.win
        lo, hi = -math.inf, math.inf
        for (n, j), (b, u) in orig.items():
            pts = win.fxl[n]
            a = j - 1
            while (n, a) in orig:
                a -= 1
            z = j + 1
            while (n, z) in orig:
                z += 1
            lo = max(lo, (pts[a][0] if a >= 0 else 0.0) - b)
            hi = min(hi, (pts[z][0] if z < len(pts) else win.loops.get(n) or math.inf) - b)
        vs = [u for _, u in orig.values()]
        return min(hi, max(lo, db)), min(1.0 - max(vs), max(-min(vs), dv))

    def cancel_drag(self):
        """Ctrl+Z while the mouse is held here: what's dragged (points, a bend, a line drawn with the pencil) goes
        back to how it was at the press, no undo step; anything else held: nothing (like the notes' Select box)."""
        d, win = self.drag, self.win
        if not d or "before" not in d:
            return True
        self.drag, self.says = None, ""
        win.fxl, win.loops, win.off, win.froms, win.fits, win.sustains, win.lfo, win.extra = d["before"]
        self.sel = {(n, i) for n, i in self.sel if n in win.fxl and i < len(win.fxl[n])}
        self.canvas.config(cursor="")
        win.redraw()
        return True

    def on_release(self, e):
        d, win = self.drag, self.win
        self.drag = None
        if not d:
            return
        if d["kind"] == "size":
            return win.app.schedule_autosave()
        if d["kind"] == "draw":
            if not d["moved"] and d["faint"]:  # (a click on a faint line: a point there)
                return self.click_point(d["fx"], d["x0"], d["y0"], e)
            if not d["moved"]:  # (a click on nothing: nothing highlighted or selected)
                self.active, self.sel = None, set()
                return win.redraw()
            self.says = ""
            if self.now() != d["before"]:
                win.commit_fx(d["before"])
            return
        if d["kind"] == "box":
            if d.get("plain") and max(abs(e.x - d["x0"]), abs(e.y - d["y0"])) < BOX_STILL:
                self.active, self.sel = None, set()
                return win.redraw()
            x0, x1, y0, y1 = sorted((d["x0"], e.x)) + sorted((d["y0"], e.y))
            self.sel = d["had"] | {(n, i) for n in self.grabbable() for x, y, i, _ in self.points(n)
                                   if x0 <= x <= x1 and y0 <= y <= y1}
            return win.redraw()
        if d["kind"] in ("point", "bend"):
            self.says = ""
            if self.now() != d["before"]:
                win.commit_fx(d["before"])
            return
        name = d["fx"]
        if self.name_at(e.x, e.y) != name:
            return
        if name in win.fxl and d["box"]:  # its small square: switched off / on
            return self.switch(name)
        if name in win.fxl:  # on already: highlighted (or not any more)
            self.active = None if self.active == name else name
            return win.redraw()
        self.put(name)

    def span(self):
        """(from, to) beats a new line goes over: all the notes, or what's in view without notes."""
        win = self.win
        if win.tones:
            return 0.0, tones_span(win.tones)
        return max(0.0, win.beat_at(win.kb_w)), max(0.0, win.beat_at(win.canvas.winfo_width()))

    def put(self, name):
        """An effect put on, its line going over all the notes (or what's in view without notes); it's the one
        highlighted then."""
        before = self.state()
        a, b = self.span()
        self.win.fxl[name] = [[a + u * (b - a), v] for u, v in FX_START[name]]
        self.active = name
        self.win.commit_fx(before)

    def on_double(self, e):
        """A double click on a point deletes it; the last point takes the effect off."""
        hit = self.hit(e.x, e.y)
        if hit and hit[0] == "point":
            self.delete_point(hit[1], hit[2])
        elif hit and hit[0] == "bend":
            self.set_bend(hit[1], hit[2], 0.0)
        else:
            self.on_press(e)

    def delete_point(self, name, i):
        self.delete_points({(name, i)})

    def delete_selected(self):
        self.delete_points(self.sel)

    def delete_key(self):
        """Delete in the window: the points selected, or (none, the pane pressed last) the highlighted effect taken
        off. False when it's not for the pane (the notes' Delete then). While the mouse holds anything but a point
        here (a line being drawn, a box, a bend...): nothing until it's let go."""
        if self.drag and self.drag["kind"] != "point":
            return True
        if self.sel:
            self.delete_selected()
            return True
        if self.pressed and self.active in self.win.fxl and self.canvas.winfo_ismapped():
            self.remove(self.active)
            return True
        return False

    def delete_points(self, which):
        """Points deleted (a set of (effect, number)); an effect left with none is taken off."""
        win = self.win
        before = self.state()
        for n, j in sorted(which, reverse=True):
            if abs(win.fxl[n][j][0] - win.sustains.get(n, math.nan)) < 1e-9:  # (its sustain point goes too)
                del win.sustains[n]
            del win.fxl[n][j]
        for n in {n for n, _ in which}:
            if not win.fxl[n]:
                self.drop(n)
        self.drag, self.sel = None, set()
        win.commit_fx(before)

    def drop(self, name):
        """An effect's line, repeat and switch gone (no undo step)."""
        win = self.win
        gone = {name} if name.endswith(AMOUNT) else {name, name + AMOUNT}
        for k in gone:
            win.fxl.pop(k, None)
        if not name.endswith(AMOUNT):
            win.loops.pop(name, None)
            win.froms.pop(name, None)
            win.sustains.pop(name, None)
            win.fits = [n for n in win.fits if n != name]
        win.off = [n for n in win.off if n != name]
        self.sel = {(n, i) for n, i in self.sel if n not in gone}
        if self.active == name:
            self.active = None

    def remove(self, name):
        """The effect taken off."""
        win = self.win
        before = self.state()
        on = name in win.fxl
        self.drop(name)
        if on:
            win.commit_fx(before)

    def switch(self, name):
        """The effect switched off (Bypass: its line kept, doing nothing) or on again."""
        win = self.win
        before = self.state()
        win.off = [n for n in win.off if n != name] if name in win.off else win.off + [name]
        win.commit_fx(before)

    def amount_line(self, name):
        """A repeating effect's amount line put on (100 % over all the notes) or taken off."""
        win = self.win
        before = self.state()
        if win.fxl.pop(name + AMOUNT, None) is None:
            a, b = self.span()
            win.fxl[name + AMOUNT] = [[a, 1.0], [b, 1.0]]
            self.active = name
        win.commit_fx(before)

    # ------------------------------------------------------------ drawing with the pencil

    def draw_at(self, d, e):
        """The pencil dragged over the pane: the highlighted effect's line becomes what's drawn, from where the
        drawing started to where it is (snap on: one step in every grid cell; Shift or snap off: a smooth line)."""
        win = self.win
        if not d["moved"]:
            if abs(e.x - d["x0"]) < 4 and abs(e.y - d["y0"]) < 4:
                return
            d["moved"] = True
            self.on_drag_draw_point(d, d["x0"], d["y0"], e)
        self.on_drag_draw_point(d, e.x, e.y, e)
        name, every, got = d["fx"], win.loops.get(d["fx"]), d["got"]
        step, hold = d["step"], d["hold"]
        gs = sorted(got)
        lo, hi = gs[0] * step, (gs[-1] + 1) * step if hold else gs[-1] * step
        if every:
            hi = min(hi, every)
        orig = d["orig"]
        kept = [p for p in orig if not lo - 1e-9 <= p[0] <= hi + 1e-9]
        new = [[g * step, got[g], "hold"] if hold else [g * step, got[g]] for g in gs if not every or g * step < every]
        if not new:  # (all past the end of the repeat)
            return
        # the line as it was up to where the drawing starts and on from where it ends (a step at both)
        wrap = self.wrap(name)
        new = [[lo, float(line_at(orig, lo, wrap))]] + new + [[hi, float(line_at(orig, hi, wrap))]]
        win.fxl[name] = sorted(kept + new, key=lambda p: p[0])
        self.says = self.value_text(name, got[gs[-1]], self.pitch_keys())
        win.redraw()

    def on_drag_draw_point(self, d, x, y, e):
        """One spot of the pencil taken in (and the grid cells between it and the last one, in a straight line)."""
        win = self.win
        if "step" not in d:
            snap = win.snap_beats()
            d["hold"] = bool(snap) and not e.state & SHIFT
            d["step"] = snap if d["hold"] else 4.0 / win.sx
        beat = win.beat_at(x)
        if win.loops.get(d["fx"]):
            beat = self.from_beat(d["fx"], d["k"], beat)
        beat = max(0.0, beat)
        g = int(math.floor(beat / d["step"] + (0 if d["hold"] else 0.5)))
        v = self.value_at(y)
        if d["fx"] == "pitch" and not e.state & SHIFT:  # (whole keys, like the points)
            v = 0.5 + round((v - 0.5) * 2 * self.pitch_keys()) / (2 * self.pitch_keys())
        v = round(v, 4)
        last = d.get("last")
        if last is not None and abs(g - last[0]) > 1:  # (a fast mouse: the cells in between too)
            for h in range(min(g, last[0]) + 1, max(g, last[0])):
                d["got"][h] = round(last[1] + (v - last[1]) * (h - last[0]) / (g - last[0]), 4)
        d["got"][g] = v
        d["last"] = (g, v)

    # ------------------------------------------------------------ copy and paste

    def copy_points(self):
        """The points selected copied (False when there are none)."""
        if not self.sel:
            return False
        fx = self.win.fxl
        first = min(fx[n][i][0] for n, i in self.sel)
        self.clip = {}
        for n, i in sorted(self.sel, key=lambda p: (self.names().index(p[0]), p[1])):
            self.clip.setdefault(n, []).append([fx[n][i][0] - first, *fx[n][i][1:]])  # (an amount line's too)
        return True

    def paste_points(self):
        """The points copied put in at the mouse (or the left of the view): into the highlighted effect when they
        came from one, else into the effects they came from; the points already in that stretch are replaced.
        False when nothing was copied."""
        if not self.clip:
            return False
        win = self.win
        before = self.state()
        at = max(0.0, win.snap(win.beat_at(self.mouse_x if self.mouse_x is not None else win.kb_w), _NoKeys))
        clip = self.clip
        if len(clip) == 1 and self.active:
            clip = {self.active: next(iter(clip.values()))}
        self.sel = set()
        for n, pts in clip.items():
            every = win.loops.get(n)
            a = self.local(n, at, self.copy_at(n, at)) if every else at
            new = [[a + p[0], *p[1:]] for p in pts if not every or a + p[0] <= every]
            if not new:
                continue
            b = new[-1][0]
            old = [p for p in win.fxl.get(n, []) if not a <= p[0] <= b]
            win.fxl[n] = sorted(old + new, key=lambda p: p[0])
            self.sel |= {(n, i) for i, p in enumerate(win.fxl[n]) if any(p is q for q in new)}
        if self.now() != before:
            win.commit_fx(before)
        return True

    # ------------------------------------------------------------ bent lines

    def segment_at(self, name, x):
        """The number of the point where the line under x starts (None: before the first / after the last point)."""
        for b0, _, b1, _, _, i, _ in self.segments(name):
            if self.win.x_of(b0) <= x <= self.win.x_of(b1):
                return i
        return None

    def drag_bend(self, d, e):
        """A line's round handle dragged up / down: its middle follows the mouse (sticks to straight near it)."""
        name, i = d["fx"], d["i"]
        pts = self.win.fxl[name]
        a, b = pts[i], (pts[i + 1] if i + 1 < len(pts) else pts[0])
        if abs(b[1] - a[1]) < 1e-9:
            return
        bend = min(BEND, max(-BEND, 2.0 * (self.value_at(e.y) - a[1]) / (b[1] - a[1]) - 1.0))
        bend = 0.0 if abs(bend) < 0.05 and not e.state & SHIFT else round(bend, 3)
        pts[i][2:] = [bend] if bend else []
        self.says = tr("hz.fx_bend_tip")
        self.win.redraw()

    def set_bend(self, name, i, bend):
        """The line from point i: bent (0 = straight) or "hold"."""
        win = self.win
        before = self.state()
        win.fxl[name][i][2:] = [bend] if bend else []
        if self.now() != before:
            win.commit_fx(before)

    # ------------------------------------------------------------ repeating

    def every_text(self, every):
        """How long one repeat is, as a text ("1/4", "2 bars", "1.5 beats")."""
        for label, beats in self.lengths():
            if abs(beats - every) < 1e-9:
                return label
        return tr("hz.fx_beats", n=f"{every:.4g}")

    def lengths(self):
        """The repeat lengths offered: [(text, beats)], longest first."""
        bar = float(self.win.app.beats)
        out = [(tr("hz.fx_bars", n=n), n * bar) for n in (4, 2)]
        return out + [(snap_text(s), snap_beats(s, bar)) for s in SNAPS if s != "off"]

    def repeat_text(self, name, key):
        """A text about how an effect repeats (key "hz.fx_repeat" for the menu, "hz.fx_repeats" for the status
        line), saying whether it counts from each note and is stretched."""
        win = self.win
        mode = win.froms.get(name)
        if mode:
            key += "_" + mode + ("_fit" if name in win.fits else "")
        elif key == "hz.fx_repeat":
            key = "hz.fx_repeat_now"
        sustained = key.startswith("hz.fx_repeats") and self.sustain(name) is not None
        more = " " + tr("hz.fx_repeats_sustain") if sustained else ""
        return tr(key, every=self.every_text(win.loops[name])) + more

    def set_loop(self, name, every, mode=None, fit=False):
        """The effect repeats every `every` beats (None = not any more). Its line is squeezed into one repeat (the
        points keep their shape), stretched back over the notes when it stops, or made longer / shorter.
        mode = None (from the shape's left edge) / "note" / "restart" (hz["from"]); fit = stretched over each note."""
        win = self.win
        before = self.state()
        if name not in win.fxl:
            a, b = self.span()
            win.fxl[name] = [[a + u * (b - a), v] for u, v in FX_START[name]]
        old, pts = win.loops.get(name), win.fxl[name]
        win.froms.pop(name, None)
        win.fits = [n for n in win.fits if n != name]
        at = win.sustains.pop(name, None)
        if at is not None and every and old and mode == "note" and not fit:  # (still once per note: it stays)
            win.sustains[name] = at * every / old
        if every is None:
            if old:
                win.fxl[name] = loop_off(pts, old, *self.span())
                del win.loops[name]
                win.fxl.pop(name + AMOUNT, None)  # (only a repeat has one)
        else:
            win.fxl[name] = [[p[0] * every / old, *p[1:]] for p in pts] if old else loop_on(pts, every)
            win.loops[name] = every
            if mode:
                win.froms[name] = mode
                if fit:
                    win.fits = [n for n in FX if n in win.fits or n == name]
        self.active = name
        if self.now() != before:
            win.commit_fx(before)
        else:
            win.redraw()

    def ask_repeat(self, name, x, y):
        """The Repeat every… window: how it plays (all the way from the shape's start / once per note / restart at
        each note, hz["from"]), how long one repeat is, as a note length [n] / [n] like the snap (1 / 4 = one beat;
        user), and Stretch to each note (then the length boxes are greyed: the note decides). Each number can be
        typed, dragged sideways (its box; the label: the first one), Up / Down, wheel.
        While it's open the pane shows where each repeat would start (green lines). OK / Enter = set_loop, Cancel /
        Escape / the window's X = nothing."""
        if self.asking:
            self.asking.destroy()
        win = self.win
        most = 64 * float(win.app.beats)
        top = self.asking = tk.Toplevel(win)
        top.title(tr("hz.fx_repeat_title", name=tr("hz.fx_" + name)))
        top.transient(win)
        top.resizable(False, False)
        f = ttk.Frame(top, padding=10)
        f.pack()
        modes = (None,) + FROM_MODES
        texts = [tr("hz.fx_from_" + (m or "left")) for m in modes]
        was = win.froms.get(name) if name in win.loops else self.new_from(None)
        how = tk.StringVar(top, value=texts[modes.index(was)])
        fit = tk.BooleanVar(top, value=name in win.fits)
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=(0, 6))
        ttk.Label(row, text=tr("hz.fx_plays")).pack(side="left")
        pick = ttk.Combobox(row, textvariable=how, values=texts, state="readonly",
                            width=max(len(t) for t in texts) + 1)
        pick.pack(side="left", padx=(6, 0))
        now = Fraction((win.loops.get(name) or win.snap_beats() or 1.0) / 4).limit_denominator(REPEAT_MOST)
        num = tk.StringVar(top, value=str(now.numerator))
        den = tk.StringVar(top, value=str(now.denominator))
        start = (num.get(), den.get())
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=(0, 6))
        lb = ttk.Label(row, text=tr("hz.fx_repeat_every"))
        lb.pack(side="left")
        top.entry = ttk.Entry(row, textvariable=num, width=5, justify="center")
        top.entry.pack(side="left", padx=(6, 4))
        ttk.Label(row, text="/").pack(side="left")
        top.under = ttk.Entry(row, textvariable=den, width=5, justify="center")
        top.under.pack(side="left", padx=(4, 0))
        stretch = ttk.Checkbutton(f, text=tr("hz.fx_fit"), variable=fit)
        stretch.pack(anchor="w", pady=(0, 10))
        b = ttk.Frame(f)
        b.pack(anchor="e")

        def mode():
            return modes[texts.index(how.get())] if how.get() in texts else None

        def switched(*_):
            """The boxes follow the choice: Lasts / Repeat every; greyed while stretched; a new envelope starts at
            half a beat (the shortest heard, user) unless something was typed."""
            m = mode()
            if m and name not in win.loops and (num.get(), den.get()) == start:
                num.set("1")
                den.set("8")
            lb.config(text=tr("hz.fx_lasts" if m == "note" else "hz.fx_repeat_every"))
            stretch.config(state="normal" if m else "disabled")
            on = "disabled" if m and fit.get() else "normal"
            for w in (top.entry, top.under):
                w.config(state=on)
            shown()

        def value():
            """The length in beats, or None when the numbers aren't whole numbers 1.. or it's too long."""
            try:
                n, d = int(calc(num.get())), int(calc(den.get()))
            except Exception:  # (anything typed that isn't a number)
                return None
            v = 4.0 * n / d if 1 <= n <= REPEAT_MOST and 1 <= d <= REPEAT_MOST else None
            return v if v and v <= most else None

        def shown(*_):
            if not top.winfo_exists():
                return
            v = self.trying = value()
            self.trying_how = (mode(), fit.get() and mode() is not None)
            ok.config(state="normal" if v else "disabled")
            self.redraw()

        def done(keep):
            v, m, stretched = (value(), mode(), fit.get()) if keep else (None, None, False)
            top.destroy()
            if v:
                self.set_loop(name, v, m, stretched and m is not None)

        def gone(e):
            if e.widget is top:
                self.asking = self.trying = None
                if self.canvas.winfo_exists():  # (not when the Hz bass window itself is closing)
                    self.redraw()

        ok = ttk.Button(b, text=tr("hz.fx_repeat_ok"), command=lambda: done(True))
        ok.pack(side="left", padx=(0, 4))
        ttk.Button(b, text=tr("hz.fx_repeat_cancel"), command=lambda: done(False)).pack(side="left")
        Scrub(win.app, [(top.entry, num, None)], (1, 4, 1), 1, REPEAT_MOST, label=lb, drag_box=True)
        Scrub(win.app, [(top.under, den, None)], (1, 4, 1), 1, REPEAT_MOST, drag_box=True)
        num.trace_add("write", shown)
        den.trace_add("write", shown)
        how.trace_add("write", switched)
        fit.trace_add("write", switched)
        top.bind("<Return>", lambda e: done(True) if value() else None)
        top.bind("<Escape>", lambda e: done(False))
        top.bind("<Destroy>", gone)
        for k in ("z", "Z", "y", "Y"):  # (the main window's undo would change the lines under it)
            top.bind(f"<Control-{k}>", lambda e: "break")
        top.protocol("WM_DELETE_WINDOW", lambda: done(False))
        switched()
        top.geometry(f"+{x}+{y}")
        remember_place(top, "hz_repeat")
        top.entry.focus_set()
        top.entry.select_range(0, "end")

    def set_sustain(self, name, beat):
        """The effect's sustain point put on its point at beat (beats into its repeat), or taken off (None)."""
        win = self.win
        before = self.state()
        if beat is None:
            win.sustains.pop(name, None)
        else:
            win.sustains[name] = beat
        if self.now() != before:
            win.commit_fx(before)

    def new_from(self, kind):
        """How a line that didn't repeat plays once it's given a shape of `kind` (None: Repeat every…): an
        envelope once per note, the rest all the way (hz["from"])."""
        return "note" if kind in ENVELOPES else None

    def set_shape(self, name, kind):
        """A ready-made shape for one repeat of the effect (it starts repeating every beat if it didn't; an envelope:
        once per note, half a beat, unless it repeats already)."""
        win = self.win
        before = self.state()
        every = win.loops.get(name)
        if not every:
            mode = self.new_from(kind)
            if mode:
                win.froms[name] = mode
            if kind in ENVELOPES:
                every = 0.5
        every = every or 1.0
        win.fxl[name] = loop_shape(kind, every, random.randrange(1 << 30), name)
        win.loops[name] = every
        win.sustains.pop(name, None)  # (a new line: its points are others)
        self.active = name
        win.commit_fx(before)

    def on_menu(self, e):
        if self.drag:  # (the left button holds something: nothing, like the notes; the menu would take its let-go)
            return "break"
        name, hit = self.name_at(e.x, e.y), self.hit(e.x, e.y)
        menu = tk.Menu(self.win, tearoff=0)
        if hit and hit[0] == "point":
            menu.add_command(label=tr("hz.fx_delete_point"), command=lambda: self.delete_point(hit[1], hit[2]))
            name_p, beat = hit[1], self.win.fxl[hit[1]][hit[2]][0]
            if name_p in self.win.loops:  # (a sustain point: only once per note, not stretched)
                can = self.win.froms.get(name_p) == "note" and name_p not in self.win.fits
                self.sustained = tk.BooleanVar(self.win, value=can and abs(self.win.sustains.get(name_p, math.nan)
                                                                           - beat) < 1e-9)
                menu.add_checkbutton(label=tr("hz.fx_sustain" if can else "hz.fx_sustain_needs"),
                                     variable=self.sustained, state="normal" if can else "disabled",
                                     command=lambda: self.set_sustain(name_p, beat if self.sustained.get() else None))
        seg = hit[2] if hit and hit[0] == "bend" else self.segment_at(hit[1], e.x) if hit and hit[0] == "line" else None
        if seg is not None:  # the line under the mouse: held (a step) or bent
            p = self.win.fxl[hit[1]][seg]
            self.held = tk.BooleanVar(self.win, value=len(p) > 2 and p[2] == "hold")
            menu.add_checkbutton(label=tr("hz.fx_hold"), variable=self.held,
                                 command=lambda: self.set_bend(hit[1], seg, "hold" if self.held.get() else 0.0))
            menu.add_command(label=tr("hz.fx_straight"), command=lambda: self.set_bend(hit[1], seg, 0.0),
                             state="normal" if len(p) > 2 else "disabled")
            menu.add_separator()
        target = name or (hit and base(hit[1])) or (self.active if e.x >= self.win.kb_w else None)
        if target:  # repeating: how long one repeat is, and ready-made shapes for it
            every = self.win.loops.get(target)
            menu.add_command(label=self.repeat_text(target, "hz.fx_repeat") if every else
                             tr("hz.fx_repeat_ask"), command=lambda: self.ask_repeat(target, e.x_root, e.y_root))
            if every:
                menu.add_command(label=tr("hz.fx_repeat_off"), command=lambda: self.set_loop(target, None))
            shapes = tk.Menu(menu, tearoff=0)
            for kind in LOOP_SHAPES:
                shapes.add_command(label=tr("hz.fx_shape_" + kind), command=lambda k=kind: self.set_shape(target, k))
            shapes.add_separator()
            for kind in ENVELOPES:
                shapes.add_command(label=tr("hz.fx_shape_%s%s" % (kind, "_pitch" if target == "pitch" else "")),
                                   command=lambda k=kind: self.set_shape(target, k))
            menu.add_cascade(label=tr("hz.fx_shape"), menu=shapes)
            if target in self.win.loops:
                self.has_amount = tk.BooleanVar(self.win, value=target + AMOUNT in self.win.fxl)
                menu.add_checkbutton(label=tr("hz.fx_amount"), variable=self.has_amount,
                                     command=lambda: self.amount_line(target))
            if target in self.win.fxl:
                self.is_off = tk.BooleanVar(self.win, value=target in self.win.off)
                menu.add_checkbutton(label=tr("hz.fx_bypass"), variable=self.is_off,
                                     command=lambda: self.switch(target))
        on = [fx for fx in ([target] if target else FX) if fx in self.win.fxl]  # (only the effects that are on; user)
        if on and target:
            menu.add_separator()
        for fx in on:
            menu.add_command(label=tr("hz.fx_remove", name=tr("hz.fx_" + fx)), command=lambda fx=fx: self.remove(fx))
        if menu.index("end") is not None:  # (nothing on and nothing under the mouse: no menu)
            menu.tk_popup(e.x_root, e.y_root)

    def on_wheel(self, e):
        """The time of the notes above: wheel = sideways, Ctrl = zoom around the mouse."""
        win = self.win
        up = e.delta > 0
        if e.state & CTRL:
            b = win.beat_at(e.x)
            win.sx = min(100000.0, max(0.05, win.sx * (1.25 if up else 0.8)))
            win.t0 = b - (e.x - win.kb_w) / win.sx
        else:
            win.t0 += (-1 if up else 1) * 120 / win.sx
        win.clamp_view()
        win.redraw()
