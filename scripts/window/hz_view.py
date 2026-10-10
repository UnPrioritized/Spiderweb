"""The Hz bass window's view: zoom, scrolling, scrollbars, drawing its piano roll (notes, the red line, slide dots,
Auto bands) and the status line."""

import math
import time

import numpy as np

from files.lang import tr
from files.mathexpr import fmt
from notes.hzbass import (HZ_DEFAULTS, all_tones, auto_state, bend_range, can_slide, glide, handle_u, heard, hz_of, layers_of,
                          funnel_box, left_edge, links, pitch, slide_knob, slide_part)
from roll.roll_shared import (ALT, CTRL, MAX_SX, MIN_SX, SELECTED_COLOR, SHIFT, SLOT_COLORS, draw_boxes, fade, note_name)
from window import look
from window.hz_layers import layer_colour
from window.widgets import StatusLine


BLACK = (1, 3, 6, 8, 10)
RED = look.HZ_RED
PLAY_LINE = look.PLAY_LINE  # (the main piano roll's)
GREY = look.HZ_UNMADE  # over what the preview hasn't made yet
ORANGE = look.WARN
FAINT = look.HZ_FAINT  # behind the red line: each repeat's own pitch
GREEN = look.HZ_GREEN  # a note's exact tone (the middle of its row)
# Auto gates: the threshold around a note's tone, (fill, edge) when it gets fixed / mixed gates
BAND_FIXED, BAND_MIXED = look.HZ_BAND_FIXED, look.HZ_BAND_MIXED
TUNE_ROW = 20  # px: rows at least this tall show the exact tone, and the red line can be dragged up / down


def shape_length(sh):
    """How long a custom shape's box is, in beats (a funnel: from its first beat to its last)."""
    if sh["kind"] == "funnel":
        b0, b1, _, _ = funnel_box(sh)
        return b1 - b0
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    bs = (b0, b1, b2, b1 + b2 - b0)
    return max(bs) - min(bs)


class HzView:
    def fit_view(self):
        """The view moved so the notes are in sight (the first time there are any). Not before the canvas has its
        size (just opened): worked out for a 1 pixel canvas, the keys sat high up until the view moved (on_resize)."""
        h = self.canvas.winfo_height()
        if h < 50:
            return
        self.fitted = True
        keys = [n["key"] for n in self.every_tone()]
        rows = max(1.0, (h - self.ruler_h) / self.sy)
        self.top = min(127.0, max(rows - 1, (max(keys) + min(keys)) / 2 + rows / 2))

    def view_state(self):
        """Zoom + scroll as kept on the app between openings and in the autosave (app.hz_view)."""
        return {"sx": self.sx / self.s, "sy": self.sy / self.s, "t0": self.t0, "top": self.top}

    def set_view_state(self, v):
        """The zoom + scroll the window had when last closed (a damaged one: left out, fitted as on a first open)."""
        try:
            v = {k: float(v[k]) for k in ("sx", "sy", "t0", "top")}
        except (TypeError, KeyError, ValueError):
            return
        if not all(map(math.isfinite, v.values())):
            return
        self.sx = min(MAX_SX, max(MIN_SX, v["sx"] * self.s))
        self.sy = min(60.0 * self.s, max(1.0, v["sy"] * self.s))
        self.t0, self.top = v["t0"], v["top"]
        self.fitted = True

    def on_resize(self, e=None):
        """The canvas got its size or changed size: the notes in sight the first time, no empty space past the
        lowest / highest key."""
        if self.every_tone() and not self.fitted:
            self.fit_view()
        self.clamp_view()
        self.redraw()

    def x_of(self, beat):
        return self.kb_w + (beat - self.t0) * self.sx

    def beat_at(self, x):
        return self.t0 + (x - self.kb_w) / self.sx

    def y_of(self, key):
        """The top of a key's row."""
        return self.ruler_h + (self.top - key) * self.sy

    def key_at(self, y):
        return max(0, min(127, math.ceil(self.top - (y - self.ruler_h) / self.sy)))

    def clamp_view(self):
        rows = max(1.0, (self.canvas.winfo_height() - self.ruler_h) / self.sy)
        self.top = min(127.0, max(min(127.0, rows - 1), self.top))
        self.t0 = max(-0.25, self.t0)

    def on_wheel(self, e):
        """The same as on the main piano roll (PianoRoll.on_wheel): wheel = up / down 3 keys, Shift = sideways,
        Ctrl = zoom both ways around the mouse, Ctrl+Shift = time only, Alt = keys only."""
        up = e.delta > 0
        f = 1.25 if up else 0.8
        zoom_time = e.state & CTRL and not e.state & ALT
        zoom_keys = (e.state & CTRL and not e.state & SHIFT) or e.state & ALT
        if zoom_time:
            self.zoom_x(f, e.x)
        if zoom_keys:
            self.zoom_y(f, e.y)
        if not (zoom_time or zoom_keys):
            if e.state & SHIFT:
                self.t0 += (-1 if up else 1) * 120 / self.sx
            else:
                self.top += 3 if up else -3
        self.clamp_view()
        self.redraw()
        self.held_to_mouse(e)

    def held_to_mouse(self, e):
        """The view moved under the mouse (wheel) while a note / path is held: it goes to the mouse at once, as on the
        main piano roll (it stayed on the old spot in the song until the mouse moved, and letting go put it there)."""
        if e.state & 0x100:  # (the left button held)
            self.on_drag(e)
        elif self.draft is not None:  # (a path drawn click by click)
            self.on_motion(e)

    def every_tone(self):
        """The notes of every layer: the picked one's (self.tones) + the others' (shown faded)."""
        hz = (self.target() or {}).get("hz") or {}
        if not hz.get("layers"):
            return self.tones
        picked = hz.get("layer", 0)
        return self.tones + [n for i, l in enumerate(layers_of(hz)) if i != picked for n in l.get("tones") or ()]

    def fit_notes(self):
        """The Fit view button: every note of every layer in sight (no notes: the first bars, the keys as they are)."""
        w, h = self.canvas.winfo_width() - self.kb_w, self.canvas.winfo_height() - self.ruler_h
        if w < 50 or h < 50:
            return
        tones = self.every_tone()
        lo, hi = (0.0, max(n["t"] + n["len"] for n in tones)) if tones else (0.0, 4.0 * self.app.beats)
        span = max(hi - lo, 1.0)
        self.sx, self.t0 = w / (span * 1.06), lo - span * 0.03
        if tones:
            keys = [n["key"] for n in tones]
            rows = max(keys) - min(keys) + 1 + 4  # (two keys of room above and below)
            self.sy = min(12.0 * self.s, max(1.0, h / rows))  # (a few notes: not huge rows)
            self.top = (max(keys) + min(keys)) / 2 + h / self.sy / 2
        self.clamp_view()
        self.redraw()

    def zoom_x(self, f, x):
        """Zoom time by f; the beat at canvas x stays where it is."""
        b = self.beat_at(x)
        self.sx = min(MAX_SX, max(MIN_SX, self.sx * f))
        self.t0 = b - (x - self.kb_w) / self.sx

    def zoom_y(self, f, y):
        """Zoom the keys by f; the key at canvas y stays where it is."""
        k = self.top - (y - self.ruler_h) / self.sy
        self.sy = min(60.0 * self.s, max(1.0, self.sy * f))
        self.top = k + (y - self.ruler_h) / self.sy

    def bar_view(self, across):
        """(start, end, total) of what's seen. The time bar reaches 8 bars past the last note (as on the main piano
        roll); scrolled further with the mouse, the total grows to where the view ends."""
        c = self.canvas
        if across:
            a, span = self.t0 + 0.25, max(1, c.winfo_width() - self.kb_w) / self.sx
            tones = self.every_tone()
            end = (max(n["t"] + n["len"] for n in tones) if tones else 0.0) + 0.25 + 8 * self.app.beats
            return a, a + span, max(end, a + span)
        a, span = 127.0 - self.top, max(1, c.winfo_height() - self.ruler_h) / self.sy
        return a, a + span, max(128.0, a + span)

    def bar_move(self, across, a):
        """A scrollbar was dragged to a (by whole pixels)."""
        if across:
            self.t0 = self.t0 + round((a - 0.25 - self.t0) * self.sx) / self.sx if a > 0 else -0.25
        else:
            self.top = self.top + round((127.0 - a - self.top) * self.sy) / self.sy if a > 0 else 127.0
        self.clamp_view()
        self.redraw()

    def bar_zoom(self, across, a, b, which):
        """A scrollbar's end was dragged (which: "start" / "end"): the view shows a..b, the other end stays."""
        c = self.canvas
        if across:
            px = max(1, c.winfo_width() - self.kb_w)
            self.sx = min(MAX_SX, max(MIN_SX, px / (b - a)))
            self.t0 = (a if which == "end" else b - px / self.sx) - 0.25
        else:
            px = max(1, c.winfo_height() - self.ruler_h)
            self.sy = min(60.0 * self.s, max(1.0, px / (b - a)))
            self.top = 127.0 - (a if which == "end" else b - px / self.sy)
        self.clamp_view()
        self.redraw()

    def zoom_step(self, across, f):
        """The "+" / "-" buttons: zoom around the middle of the view."""
        c = self.canvas
        if across:
            self.zoom_x(f, (self.kb_w + c.winfo_width()) / 2)
        else:
            self.zoom_y(f, (self.ruler_h + c.winfo_height()) / 2)
        self.clamp_view()
        self.redraw()

    def pan_start(self, e):
        self.pan = (e.x, e.y, self.t0, self.top)

    def pan_move(self, e):
        x, y, t0, top = self.pan
        self.t0, self.top = t0 - (e.x - x) / self.sx, top + (e.y - y) / self.sy
        self.clamp_view()
        self.redraw()

    def redraw(self):
        c = self.canvas
        for bar in self.bars:
            bar.refresh()
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        s, kb, rh = self.s, self.kb_w, self.ruler_h
        k_hi, k_lo = self.key_at(rh), self.key_at(h)
        for k in range(k_lo, k_hi + 1):  # rows
            y = self.y_of(k)
            if k % 12 in BLACK:
                c.create_rectangle(kb, y, w, y + self.sy, fill=look.HZ_ROW_BLACK, outline="")
            c.create_line(kb, y + self.sy, w, y + self.sy, fill=look.HZ_OCTAVE_LINE if k % 12 == 0 else look.HZ_ROW_LINE)
        beats, sb = self.app.beats, self.snap_beats()
        bar_step = float(beats)
        while bar_step * self.sx < 8:  # zoomed far out: bars, then every 2nd, 4th... bar
            bar_step *= 2
        lo, hi = max(0.0, self.beat_at(kb)), self.beat_at(w)
        # (columns: the snap's lines, then every beat and bar over them, as on the main piano roll: a 3/16 or 1/6
        # snap left most beat / bar lines out)
        for step, colour in ((sb, look.HZ_GRID), (1.0, look.HZ_GRID_BEAT), (bar_step, look.HZ_GRID_BAR)):
            if not step or step * self.sx < 8:
                continue
            n = math.floor(lo / step)
            while n * step <= hi:
                x = self.x_of(n * step)
                c.create_line(x, rh, x, h, fill=colour)
                n += 1
        sh = self.target()
        if sh is not None and not self.grow.get():  # the shape ends here: what's after it isn't used
            x = max(kb, self.x_of(shape_length(sh)))
            c.create_rectangle(x, rh, w, h, fill=look.HZ_SHADE, outline="", stipple="gray50")
            c.create_line(x, rh, x, h, fill=look.HZ_SHADE_EDGE, dash=(4, 3))
        hz = (sh or {}).get("hz") or {}
        own = SLOT_COLORS[0]
        if hz.get("layers"):  # the other layers' notes, faded (only the picked one's can be changed)
            picked = hz.get("layer", 0)
            own = layer_colour(hz, picked)
            for i, layer in enumerate(layers_of(hz)):
                if i == picked:
                    continue
                fill, edge = (fade(colour) for colour in layer_colour(hz, i))
                for n in layer.get("tones") or ():
                    x0, x1, y = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
                    c.create_rectangle(x0, y + 1, max(x1, x0 + 2), y + self.sy - 1, fill=fill, outline=edge)
        for i, n in enumerate(self.tones):  # notes
            x0, x1, y = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            fill, edge = SELECTED_COLOR if i in self.sel else own
            c.create_rectangle(x0, y + 1, max(x1, x0 + 2), y + self.sy - 1, fill=fill, outline=edge)
        if self.app.hz_line.get():
            self.draw_line(w)
        red = self.slide_red()  # (faded while the Arpeggio leaves the slides out)
        for x, y, *_ in self.dots():
            r = 3.5 * s
            c.create_oval(x - r, y - r, x + r, y + r, fill=look.HZ_DOT, outline=red, width=max(1, round(1.5 * s)))
        for x, y, _, sl, *_ in self.handles():  # a slide's bend: hollow while it follows the Glide curve
            r = 3 * s
            c.create_rectangle(x - r, y - r, x + r, y + r, fill=red if "bend" in sl else "", outline=red,
                               width=max(1, round(1.5 * s)))
        self.draw_bends()  # (the Bend tool: each note's own bend line and points, over the slides', hz_bend.py)
        self.draw_draft(own)  # (a path being drawn, and the notes it would make: hz_draw.py)
        d = self.drag  # the Select box (with the ones kept when Ctrl+drag adds it), or the last ones (kept_box)
        boxes = d["more"] + [b for b in (self.box_area(),) if b] if d and d["kind"] == "box" else self.kept_box() or []
        draw_boxes(c, [self.box_rect(b) for b in boxes], kb, rh, s)
        c.create_rectangle(0, 0, kb, h, fill=look.HZ_KEYS, outline="", tags="frame")  # keys (the preview's grey
        # goes under this: draw_preview)
        for k in range(k_lo, k_hi + 1):
            y = self.y_of(k)
            if k % 12 in BLACK:
                c.create_rectangle(0, y, kb * 0.6, y + self.sy, fill=look.HZ_KEY_BLACK, outline="")
            if self.sy >= 4 * s:
                c.create_line(0, y + self.sy, kb, y + self.sy, fill=look.HZ_KEY_LINE)
        for k in range(k_lo, k_hi + 1):  # (the names after the keys, so small rows don't cover them)
            y = self.y_of(k)
            if k % 12 == 0 or (self.sy >= 15 * s and k % 12 not in BLACK):
                c.create_text(kb - 3, y + self.sy / 2, text=note_name(k), anchor="e", fill=look.HZ_KEY_TEXT,
                              font=look.font(7, "bold" if k % 12 == 0 else "normal"))
        c.create_line(kb, 0, kb, h, fill=look.HZ_EDGE)
        c.create_rectangle(0, 0, w, rh, fill=look.HZ_RULER, outline="")  # bar numbers
        every = 1  # (zoomed far out: every 2nd, 4th... bar number, so they don't run into each other)
        while every * beats * self.sx < 40 * s:
            every *= 2
        n = max(0, math.floor(self.beat_at(kb) / beats / every) * every)
        while n * beats <= self.beat_at(w):
            x = self.x_of(n * beats)
            if x >= kb:
                c.create_text(x + 3, rh / 2, text=str(n + 1), anchor="w", fill=look.LABEL, font=look.font(8))
            n += every
        c.create_line(0, rh, w, rh, fill=look.HZ_EDGE)
        if not self.can_place():
            c.create_text((kb + w) / 2, (rh + h) / 2, text=tr("hz.hint_none"), fill=look.HINT,
                          width=w - kb - 40 * s, justify="center")
        self.show_status()
        self.layers.redraw()
        self.fx.redraw()
        self.loudness.redraw()
        if self.synth_win:  # (the same lines there)
            self.synth_win.refresh()
        self.preview.shown = None
        self.draw_preview()

    def tune_rows(self):
        """True when the rows are tall enough to see and change a note's tune."""
        return self.sy >= TUNE_ROW * self.s

    def pitch_y(self, key):
        """Where a pitch (in keys, not whole) is: a key's exact tone is the middle of its row."""
        return self.ruler_h + (self.top - key + 0.5) * self.sy

    def draw_line(self, w):
        """The red line: the tone that's really heard. Each repeat lasts a whole number of ticks, so the line sits
        a little off the middle of the row (the exact tone), more at a low PPQ. It stops dead at a note's end; only
        a slide goes on to the next note."""
        c, app, kb = self.canvas, self.app, self.kb_w
        width = max(2, round(2 * self.s))
        sh = self.target()
        bpm = float(app.current_bpm() or 120)
        hz = self.line_hz()
        knobs = dict(hz, _memo={})  # (each slide's Glide curve: what the MOD tab needs worked out once)
        red = self.slide_red()
        most = bend_range(hz)
        for a, b, s in links(self.tones):  # a slide over a gap between two notes: no sound there
            x0, x1 = a["t"] + a["len"], b["t"]
            f0, f1, k0, k1 = glide(a, b, s)
            k0, k1 = self.bent_pitch(a, f0, most), self.bent_pitch(b, f1, most)  # (from / to the bent pitches)
            if x1 - x0 > 1e-9:
                knob = slide_knob(knobs, a)
                beats = np.linspace(x0, x1, 17)
                pts = [(self.x_of(t), self.pitch_y(k0 + (k1 - k0) * slide_part((t - f0) / (f1 - f0), s, knob)))
                       for t in beats]
                c.create_line(*[v for p in pts for v in p], fill=red, dash=(3, 3))
        left = left_edge(sh) if sh is not None else app.hz_start or 0.0
        lo, hi = self.beat_at(kb), self.beat_at(w)
        for n in self.tones:  # Auto gates: the threshold around each note's tone, green = fixed, orange = mixed (a
            got = auto_state(hz, app.ppq, n)  # note's own gates: the thinnest band)
            if got is None or n["t"] > hi or n["t"] + n["len"] < lo:
                continue
            y, half = self.pitch_y(pitch(n)), max(2.5 * self.s, (got[1] or 0.0) / 100.0 * self.sy)  # (always seen)
            fill, edge = BAND_FIXED if got[2] else BAND_MIXED
            c.create_rectangle(self.x_of(n["t"]), y - half, self.x_of(n["t"] + n["len"]), y + half, fill=fill,
                               outline=edge if half >= 3 * self.s else "")
        runs = []
        for a, b, keys, mean in heard(hz, left, app.ppq, bpm):
            see = (b >= lo) & (a <= hi)
            if see.any():
                runs.append((self.x_of(a[see]), self.x_of(b[see]), self.pitch_y(keys[see]), self.pitch_y(mean[see])))
        # faint: every repeat's own pitch (its whole-tick gate); over it, red: the average = the tone heard
        for colour, wide, which in ((FAINT, 1, 2), (RED, width, 3)):
            for run in runs:
                x0, x1, y = run[0], run[1], run[which]
                if (x1[-1] - x0[0]) >= 2 * len(x0):  # every repeat can be seen: steps
                    c.create_line(*np.column_stack([x0, y, x1, y]).ravel().tolist(), fill=colour, width=wide)
                    continue
                # too many to draw each: a band from the lowest to the highest repeat at every pixel
                col = np.floor(x0)
                at = np.flatnonzero(np.concatenate([[True], col[1:] != col[:-1]]))
                top, bottom = np.minimum.reduceat(y, at), np.maximum.reduceat(y, at)
                xs = np.append(col[at], x1[-1])
                top, bottom = np.append(top, top[-1]), np.append(bottom, bottom[-1])
                pts = np.concatenate([np.column_stack([xs, top]), np.column_stack([xs, bottom])[::-1]])
                c.create_polygon(*pts.ravel().tolist(), fill=colour, outline=colour, width=wide)
        if self.pending:  # the first middle click of a slide: where it will start
            n = next((n for n in self.tones if n["id"] == self.pending[0]), None)
            if n is not None:
                x, y, r = self.x_of(self.mark_beat(n)), self.pitch_y(pitch(n)), 3.5 * self.s
                c.create_oval(x - r, y - r, x + r, y + r, fill=RED, outline=RED)
        if self.tune_rows():  # the exact tone of each note, over the red line: a line right on it shows green
            for n in self.tones:
                y = self.pitch_y(n["key"])
                c.create_line(self.x_of(n["t"]), y, self.x_of(n["t"] + n["len"]), y, fill=GREEN)

    def dots(self):
        """[(x, y, tone number, "in" / "out", slide)]: the red line's dots, two for each slide made: "out" on the
        note it leaves, "in" on the note it goes to (tone number = the note the dot is on). A dot with no lead sits
        just outside its note's end (so the end itself stays free for changing the note's length). None while the
        red line is hidden."""
        out = []
        if not self.app.hz_line.get():
            return out
        index = {id(n): i for i, n in enumerate(self.tones)}
        off, most = 6 * self.s, self.bend_most()
        for a, b, s in links(self.tones):  # (on the notes' bent pitches, where the slide leaves / arrives)
            x0, x1, _, _ = glide(a, b, s)
            out.append((self.x_of(x0) + (off if x0 >= a["t"] + a["len"] else 0),
                        self.pitch_y(self.bent_pitch(a, x0, most)), index[id(a)], "out", s))
            out.append((self.x_of(x1) - (off if x1 <= b["t"] else 0), self.pitch_y(self.bent_pitch(b, x1, most)),
                        index[id(b)], "in", s))
        return out

    def line_hz(self):
        """The Hz bass the red line is worked out from: the shown one's settings with the window's notes."""
        sh = self.target()
        return dict((sh or {}).get("hz") or self.new_hz(float(self.app.current_bpm() or 120)), tones=self.tones)

    def slides_off(self):
        """True while the Arpeggio box is on: it plays new notes, leaving the slides made by hand out (like a
        synth's arpeggiator); they're drawn faded and still edited (user)."""
        return bool(self.line_hz().get("arp"))

    def slide_red(self):
        return fade(RED) if self.slides_off() else RED

    def handles(self):
        """[(x, y, tone number, slide, pitch at its start, at its end, the Glide curve it follows or None)]: each
        slide's bend handle, on its curve at hz_glide.handle_u (tone number = the note it leaves). None while the
        red line is hidden, on a slide between two notes of the same tone (nothing to bend), or one too short to grab
        beside its dots (a dot's grab area reaching it)."""
        out = []
        if not self.app.hz_line.get():
            return out
        hz, index = None, {id(n): i for i, n in enumerate(self.tones)}
        near = 12 * self.s
        ends = {}
        for x, y, _, _, s in self.dots():
            ends.setdefault(id(s), []).append((x, y))
        most = self.bend_most()
        for a, b, s in links(self.tones):
            f0, f1, k0, k1 = glide(a, b, s)
            k0, k1 = self.bent_pitch(a, f0, most), self.bent_pitch(b, f1, most)  # (from / to the bent pitches)
            if abs(k1 - k0) < 1e-6 or self.x_of(f1) - self.x_of(f0) < 20 * self.s:
                continue
            hz = hz or dict(self.line_hz(), _memo={})  # (what the MOD tab needs worked out once for all)
            u = handle_u(s)
            knob = slide_knob(hz, a)
            x, y = self.x_of(f0 + (f1 - f0) * u), self.pitch_y(k0 + (k1 - k0) * slide_part(u, s, knob))
            if any(abs(x - dx) <= near and abs(y - dy) <= near for dx, dy in ends.get(id(s), ())):
                continue
            out.append((x, y, index[id(a)], s, k0, k1, knob))
        return out

    def pairs(self):
        """[(a, b)]: the slides that can go between the selected notes: each one to the next of them, a chain (user:
        not the first to all the others). The next = the selected notes starting first at or after its end, all of
        them when they start together (one to several); several can lead to the same one. Several to several (a
        chord to a chord): none there, it would cross every note with every note."""
        if len(self.sel) < 2 or max(self.sel) >= len(self.tones):
            return []
        ns = sorted((self.tones[i] for i in self.sel), key=lambda n: (n["t"], n["key"]))
        nexts = []
        for a in ns:
            after = [b for b in ns if can_slide(a, b)]
            if after:
                first = min(b["t"] for b in after)
                nexts.append((a, [b for b in after if b["t"] <= first + 1e-9]))
        leads = {}  # (how many notes lead to each next group)
        for _, bs in nexts:
            key = tuple(id(b) for b in bs)
            leads[key] = leads.get(key, 0) + 1
        return [(a, b) for a, bs in nexts if len(bs) == 1 or leads[tuple(id(b) for b in bs)] == 1 for b in bs]

    @staticmethod
    def link(a, b):
        """The slide from tone a to tone b, or None."""
        return next((s for s in a["to"] if s["id"] == b["id"]), None)

    def say(self, text):
        """A message in the status line, kept for a few seconds (the mouse moving would write over it at once)."""
        self.said_until = time.perf_counter() + StatusLine.HOLD_MS / 1000
        self.status.config(text=text)

    def show_status(self, e=None):
        if time.perf_counter() < self.said_until:
            return
        n = len(self.tones)
        text = tr("hz.one_note") if n == 1 else tr("hz.n_notes", n=n)
        sh = self.target()
        if sh is not None and all_tones(sh.get("hz") or {}):  # (every layer's: what the piano roll gets)
            text += "     " + tr("hz.repeats", n=f"{self.app.note_count(sh):,}")
        if e is not None and e.x >= self.kb_w and e.y >= self.ruler_h:
            k = self.key_at(e.y)
            cents = ((sh or {}).get("hz") or HZ_DEFAULTS)["cents"]
            text += "     " + tr("hz.position", beat=fmt(max(0.0, self.beat_at(e.x)) + 1), key=note_name(k),
                                 hz=f"{hz_of(k, cents):.2f}")
        if self.drag and self.drag["kind"] == "tune":
            text += "     " + tr("hz.tune", cents=f"{self.tones[self.drag['i']]['cents']:+g}")
        text += self.bend_status()
        hit = self.hit(e.x, e.y) if e is not None and not self.drag else None
        held = self.drag["kind"] if self.drag else hit and hit[0]
        if held in ("in", "out", "bend") and self.slides_off():
            text += "     " + tr("hz.arp_slides")
        got = (auto_state(sh["hz"], self.app.ppq, self.tones[hit[1]])
               if hit and hit[0] not in ("in", "out", "bend") and sh is not None and sh.get("hz") else None)
        if got and got[1] is None:  # the note's own gates
            text += "     " + tr("hz.own_fixed" if got[2] else "hz.own_mixed", off=f"{got[0]:.2f}")
        elif got:  # Auto gates: what this note gets, and why
            text += "     " + tr("hz.auto_fixed" if got[2] else "hz.auto_mixed", off=f"{got[0]:.2f}",
                                 limit=f"{got[1]:g}")
        if self.fx.says or self.loudness.says:
            text = self.fx.says or self.loudness.says
        self.status.config(text=text)
