"""The track list (user 2026-10-23, like Domino's): Tab swaps the piano roll <-> this view in the same place of the
main window. One row per track the MIDI export / Export to Domino would write (midi_out.track_slots: the same
tracks in the same order, both exports alike), each with a tiny picture of its notes in its channel's colour, a
Conductor row on top. View only: Spiderweb picks the tracks. Zoomed out, the ruler and its lines count several
bars per column (meter.bar_lines), like Domino's.

Picture: the rows' stripes, bar lines and notes painted into one NumPy picture (speed.Photo) per redraw; names,
ruler and play line are canvas items. Each track's notes fill its row from its own lowest to highest key."""

import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc
from files.midi_out import track_slots
from files.snap import clean_snap, snap_beats
from files.speed import Photo
from notes.engine import slot_track_channel
from notes.meter import bar_line, bar_lines, key_name, sig_text, sigs_of, where
from roll.roll_draw import TRACK_FADE
from roll.roll_shared import MAX_SX, MIN_SX, SLOT_COLORS, grab_while_panning, slot_colours
from window import look
from window.snap_picker import SnapPicker
from window.widgets import Scrub, Tooltip, bad, good, remember_place

NAME_W = 150    # the names' column (x scale)
RULER_H = 28    # the ruler: signatures on top, bar numbers under them
ROW_H = 26      # a row's height at first; zoomed up / down between ROW_LEAST and ROW_MOST
ROW_LEAST, ROW_MOST = 8, 160
PAD = 3         # free pixels above / below a row's notes
COLUMN = 28     # bar numbers closer than this many pixels: several bars per column (2, 4, 8...)
TIP_SHAPES = 8  # shapes named in a row's tip at most
FAINT = 25      # % the tracks shown behind the current one are drawn at, at first (0 = hidden)


def hex_rgb(colour):
    colour = colour.lstrip("#")
    if len(colour) == 3:
        colour = "".join(c * 2 for c in colour)
    return [int(colour[i:i + 2], 16) for i in (0, 2, 4)]


class TrackList(tk.Canvas):
    def __init__(self, parent, app, scale):
        super().__init__(parent, bg=look.TRACK_ROW, highlightthickness=0, takefocus=True)
        self.app, self.scale = app, scale
        self.name_w, self.ruler_h = round(NAME_W * scale), round(RULER_H * scale)
        self.row_h = round(ROW_H * scale)
        self.view_t, self.sx = 0.0, None  # beat at the picture's left edge, pixels per beat (None: fitted when shown)
        self.top = 0       # pixels the track rows are scrolled up
        self.current = None  # the highlighted track's slot (None: the Conductor row / nothing)
        self.behind = set()  # slots shown faintly behind it on the piano roll ("Show multiple tracks")
        self.muted = set()   # slots not played (the speaker in their row; playback only, never the files)
        self.faint = FAINT
        self.faint_var = tk.StringVar(value=str(FAINT))
        self.snap = tk.StringVar(value="bar")  # the track list's own snap (like Domino's; Bar at first)
        self.picker = None  # the "Show multiple tracks" window while open
        self.bars = ()     # its scrollbars (zoombar.add_zoom_bars)
        self._made = None  # (rendered, pad, use10) the tracks below were worked out for
        self._slots = np.zeros(0, np.int64)  # the tracks: their slots, in order
        self._row_of = np.zeros(0, np.int64)  # slot -> row (-1: none)
        self._lo = self._hi = np.zeros(0, np.int64)  # each slot's lowest / highest key
        self._photo = None
        self._pan = None
        self._job = None
        self.tip = Tooltip(self, "")
        self.bind("<Configure>", lambda e: self.request_redraw())
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<Motion>", self.on_motion)
        self.bind("<MouseWheel>", self.on_wheel)
        self.bind("<ButtonPress-2>", self.start_pan)
        self.bind("<B2-Motion>", self.pan_to)
        self.bind("<ButtonRelease-2>", lambda e: setattr(self, "_pan", None))
        grab_while_panning(self)

    # ------------------------------------------------------------ the tracks

    def tracks(self):
        """The slots written as tracks, in order (worked out again only when the notes change)."""
        app = self.app
        n, pad, use10 = app.rendered, app.pad_tracks(), app.picture_use10
        made = self._made
        if made is None or made[0] is not n or made[1:] != (pad, use10):
            slots = track_slots(n, pad).astype(np.int64)
            size = int(slots.max()) + 1 if len(slots) else 0
            row_of = np.full(size, -1, np.int64)
            row_of[slots] = np.arange(len(slots))
            lo, hi = np.full(size, 1 << 30, np.int64), np.full(size, -1, np.int64)
            if len(n):
                np.minimum.at(lo, n[:, 4], n[:, 2])
                np.maximum.at(hi, n[:, 4], n[:, 2])
            self._slots, self._row_of, self._lo, self._hi = slots, row_of, lo, hi
            self._made = (n, pad, use10)
            if self.current is not None and (self.current >= size or row_of[self.current] < 0):
                self.current = None  # (its track is gone)
            self.behind = {s for s in self.behind if s < size and row_of[s] >= 0}
            self.muted = {s for s in self.muted if s < size and row_of[s] >= 0}
        return self._slots

    def unmuted(self, notes):
        """notes without the muted tracks' (App.play_apart: playback only)."""
        if not self.muted or not len(notes):
            return notes
        return notes[~np.isin(notes[:, 4], list(self.muted))]

    def toggle_mute(self, slot):
        self.muted ^= {slot}
        self.request_redraw()
        self.app.play_changed()

    # ------------------------------------------------------------ settings (autosaved, no undo step)

    def state(self):
        return {"faint": self.faint, "snap": self.snap.get()}

    def set_state(self, data):
        if not isinstance(data, dict):
            return
        if type(data.get("faint")) is int and 0 <= data["faint"] <= 100:
            self.faint = TRACK_FADE[0] = data["faint"]
            self.faint_var.set(str(self.faint))
        if isinstance(data.get("snap"), str):
            self.snap.set(clean_snap(data["snap"]))

    def on_faint(self, e):
        """The Faint % box typed / stepped: the tracks behind drawn again that faint (0 = not drawn)."""
        try:
            v = int(round(float(calc(self.faint_var.get()))))
            if not 0 <= v <= 100:
                raise ValueError
        except (ValueError, ZeroDivisionError, OverflowError):
            return bad(e)
        if str(v) != self.faint_var.get():
            self.faint_var.set(str(v))
        good(e)
        if v != self.faint:
            was, self.faint = self.faint, v
            TRACK_FADE[0] = v
            self.app.schedule_autosave()
            if (was == 0) != (v == 0):  # (0: the tracks behind left out)
                self.app.track_view()
            roll = self.app.roll
            roll._note_pic = roll._carry = roll._draft_base = None  # (pictures painted with the old colours)
            roll.request_redraw()

    def snapped(self, beat):
        """beat on the track list's snap: Bar = the nearest bar line (by the time signatures), a note length = its
        steps from the bar's start, Off = as it is."""
        snap, sigs = self.snap.get(), sigs_of(self.app.tempo)
        if snap == "off":
            return beat
        if snap == "bar":
            return min(bar_line(sigs, beat), bar_line(sigs, beat, after=True), key=lambda b: abs(b - beat))
        step = snap_beats(snap, self.app.beats)
        if not step:
            return beat
        at = where(sigs, beat)[2]
        return max(0.0, at + round((beat - at) / step) * step)

    def toggle_behind(self, slot):
        self.behind ^= {slot}
        self.shown_changed()

    def shown_changed(self):
        """Which tracks show changed: the list, the picker and the piano roll (when it shows one track) again."""
        self.request_redraw()
        if self.picker:
            self.picker.fill()
        self.app.track_view()
        self.app.roll.request_redraw()

    def filtered(self, drawn, back):
        """(the notes the piano roll paints, the owner number given to the faint ones or None): with a track
        highlighted only its notes, the tracks picked behind it as faint copies (velocity -2, owner back), the
        rest left out; nothing highlighted = drawn as it is."""
        self.tracks()
        if self.current is None or not len(drawn):
            return drawn, None
        slot = drawn[:, 4]
        out = [drawn[slot == self.current]]
        behind = sorted(self.behind - {self.current}) if self.faint > 0 else []
        if behind:
            faint = drawn[np.isin(slot, behind)].copy()
            if len(faint):
                faint[:, 3], faint[:, 5] = -2, back
                out.insert(0, faint)
                return np.concatenate(out), back
        return out[0], None

    def open_picker(self):
        if self.picker:
            self.picker.lift()
            return
        self.picker = BehindWindow(self)

    def name_of(self, row):
        return tr("tracks.name", n=row + 1)

    def channel_of(self, slot):
        return tr("tracks.channel", ch=slot_track_channel(slot, self.app.picture_use10)[1] + 1)

    def colour_of(self, slot):
        """The track's notes' colour: its channel's, as on the piano roll (the light look's darker edge colour shows
        better on thin lines; the dark look's fill colour)."""
        table = slot_colours(self.app.picture_use10)
        fill, edge = SLOT_COLORS[table[slot % len(table)]]
        return fill if look.DARK else edge

    # ------------------------------------------------------------ view

    def area(self):
        """(left, top) of the notes' part: right of the names, under the ruler and the Conductor row."""
        return self.name_w, self.ruler_h + self.row_h

    def t2x(self, beat):
        return self.name_w + (beat - self.view_t) * self.sx

    def x2t(self, x):
        return self.view_t + (x - self.name_w) / self.sx

    def song_end(self):
        """The last note's end in beats (at least 4 bars)."""
        n = self.app.rendered
        end = int(n[:, 1].max()) / self.app.ppq if len(n) else 0.0
        return max(end, 16.0)

    def fit(self):
        """The whole song across."""
        w = max(1, self.winfo_width() - self.name_w)
        self.view_t = 0.0
        self.sx = min(MAX_SX, max(MIN_SX, w / (self.song_end() * 1.02)))

    def rows_height(self):
        return len(self.tracks()) * self.row_h

    def clamp(self):
        h = self.winfo_height() - self.area()[1]
        self.top = max(0, min(self.top, self.rows_height() - max(h, self.row_h)))
        self.view_t = max(0.0, self.view_t)

    def bar_view(self, across):
        """zoombar's: (start, end, total) of what's seen."""
        if across:
            if self.sx is None:
                return None
            w = self.winfo_width() - self.name_w
            a, b = self.view_t, self.view_t + w / self.sx
            return a, b, max(b, self.song_end() + 32)
        h = self.winfo_height() - self.area()[1]
        total = max(self.rows_height(), h)
        return self.top, self.top + h, total

    def bar_move(self, across, a):
        if across:
            self.view_t = max(0.0, round(a * self.sx) / self.sx)  # (whole pixels)
        else:
            self.top = round(a)
        self.clamp()
        self.request_redraw()

    def bar_zoom(self, across, a, b, which):
        if across:
            w = self.winfo_width() - self.name_w
            self.sx = min(MAX_SX, max(MIN_SX, w / max(b - a, 1e-9)))
            self.view_t = a if which == "end" else b - w / self.sx
        else:  # (h pixels now show b - a of the rows' old pixels)
            h = self.winfo_height() - self.area()[1]
            f = h / max(b - a, 1)
            self.row_h = round(min(ROW_MOST * self.scale, max(ROW_LEAST * self.scale, self.row_h * f)))
            self.top = round(a * f)
        self.clamp()
        self.request_redraw()

    def zoom_step(self, across, f, x=None):
        """Zoom in (f > 1) / out around x (time) or the middle."""
        if across:
            if self.sx is None:
                return
            x = (self.name_w + self.winfo_width()) / 2 if x is None else x
            t = self.x2t(x)
            self.sx = min(MAX_SX, max(MIN_SX, self.sx * f))
            self.view_t = t - (x - self.name_w) / self.sx
        else:
            mid = self.top + (self.winfo_height() - self.area()[1]) / 2
            row = mid / self.row_h
            self.row_h = round(min(ROW_MOST * self.scale, max(ROW_LEAST * self.scale, self.row_h * f)))
            self.top = round(row * self.row_h - (self.winfo_height() - self.area()[1]) / 2)
        self.clamp()
        self.request_redraw()

    def on_wheel(self, e):
        notch = 1 if e.delta > 0 else -1
        if e.state & 0x4:  # Ctrl: zoom across (+ Shift: the rows' height)
            self.zoom_step(not e.state & 0x1, 1.25 if notch > 0 else 0.8, e.x)
        elif e.state & 0x1:  # Shift: sideways
            self.bar_move(True, self.view_t - notch * 60 * self.scale / self.sx)
        else:
            self.bar_move(False, self.top - notch * self.row_h)

    def start_pan(self, e):
        self._pan = (e.x, e.y, self.view_t, self.top)

    def pan_to(self, e):
        if self._pan is None or self.sx is None:
            return
        x, y, t, top = self._pan
        self.view_t = max(0.0, t - round(e.x - x) / self.sx)
        self.top = top - (e.y - y)
        self.clamp()
        self.request_redraw()

    # ------------------------------------------------------------ mouse

    def row_at(self, y):
        """The row under y: -1 = the Conductor row, None = the ruler / below the last track."""
        if y < self.ruler_h:
            return None
        if y < self.area()[1]:
            return -1
        row = int((y - self.area()[1] + self.top) // self.row_h)
        return row if row < len(self.tracks()) else None

    def on_press(self, e):
        self.focus_set()
        self.tip.use()
        if e.y < self.ruler_h:  # the play line, on the track list's snap (Shift: free)
            if e.x >= self.name_w and self.sx:
                beat = max(0.0, self.x2t(e.x))
                self.app.set_playhead(beat if e.state & 0x1 else self.snapped(beat))
            return
        row = self.row_at(e.y)
        if row is None:
            return
        if row >= 0 and self.on_speaker(e.x):  # its speaker: muted / heard
            return self.toggle_mute(int(self.tracks()[row]))
        if e.state & 0x4 and row >= 0:  # Ctrl+click: shown behind the current track or not
            return self.toggle_behind(int(self.tracks()[row]))
        self.current = None if row < 0 else int(self.tracks()[row])
        self.shown_changed()

    def speaker_x(self):
        return self.name_w - 13 * self.scale

    def on_speaker(self, x):
        return abs(x - self.speaker_x()) <= 9 * self.scale

    def on_motion(self, e):
        row = self.row_at(e.y)
        if row is not None and row >= 0 and self.on_speaker(e.x):
            slot = int(self.tracks()[row])
            text = tr("tracks.unmute_tip" if slot in self.muted else "tracks.mute_tip")
        else:
            text = self.row_tip(row) if row is not None and e.x < self.name_w else ""
        if text != self.tip.text:
            self.tip.hide()
            self.tip.text = text
            if text and not self.tip.used and not e.state & self.tip.BUTTONS:
                self.tip.schedule()

    def row_tip(self, row):
        """A row's whole name, channel, note count and the shapes whose notes are on it."""
        if row < 0:
            return tr("tracks.conductor_tip")
        slot = int(self.tracks()[row])
        n = self.app.rendered
        mine = n[n[:, 4] == slot] if len(n) else n
        lines = [f"{self.name_of(row)}  ·  {self.channel_of(slot)}",
                 trn("tracks.notes", len(mine), n=f"{len(mine):,}")]
        owners = np.unique(mine[:, 5]).tolist() if len(mine) else []
        names = [self.app.shape_label(self.app.shapes[i]) for i in owners[:TIP_SHAPES] if i < len(self.app.shapes)]
        if names:
            lines.append(tr("tracks.shapes", names=", ".join(names)))
        if len(owners) > TIP_SHAPES:
            lines.append(trn("tracks.more_shapes", len(owners) - TIP_SHAPES, n=len(owners) - TIP_SHAPES))
        return "\n".join(lines)

    # ------------------------------------------------------------ drawing

    def changed(self):
        """The notes changed (App.shapes_changed): drawn again while shown."""
        if self.winfo_ismapped():
            self.request_redraw()
        if self.picker:
            self.picker.fill()

    def request_redraw(self):
        if self._job is None:
            self._job = self.after_idle(self.redraw)

    def redraw(self):
        self._job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2 or not self.winfo_ismapped():
            return
        if self.sx is None:
            self.fit()
        self.tracks()
        self.clamp()
        self.delete("all")
        lines = self.shown_bars(w)
        self.paint(w, h, lines)
        self.draw_ruler(w, lines)
        self.draw_conductor(w)
        self.draw_names(h)
        self.draw_playhead()
        for bar in self.bars:
            bar.refresh()

    def shown_bars(self, w):
        """[(x, bar number from 0)] of the bar lines shown: zoomed out only every 2nd, 4th... bar (Domino's
        columns of several bars)."""
        sigs = sigs_of(self.app.tempo)
        lo, hi = max(0.0, self.x2t(self.name_w)), self.x2t(w)
        return [(self.t2x(b), n) for b, n in bar_lines(sigs, lo, hi, COLUMN * self.scale / self.sx)]

    def paint(self, w, h, lines):
        """The rows (stripes, the highlighted one, bar lines) and every track's notes as one picture."""
        x0, y0 = self.area()
        aw, ah = w - x0, h - y0
        if aw < 1 or ah < 1:
            return
        rh, slots = self.row_h, self._slots
        rows = (np.arange(ah) + self.top) // rh  # (each pixel row's track row)
        base = np.array([hex_rgb(look.TRACK_ROW), hex_rgb(look.TRACK_ROW_ALT), hex_rgb(look.TRACK_PICKED),
                         hex_rgb(look.TRACK_EMPTY)], np.uint8)
        kind = np.where(rows >= len(slots), 3, rows % 2)
        if self.current is not None and self.current < len(self._row_of) and self._row_of[self.current] >= 0:
            kind[rows == self._row_of[self.current]] = 2
        img = np.empty((ah, aw, 3), np.uint8)
        img[:] = base[kind][:, None, :]
        edge = ((np.arange(ah) + self.top) % rh == rh - 1) & (rows < len(slots))  # (a faint line under each row)
        img[edge] = hex_rgb(look.TRACK_LINE)
        xs = np.array([round(x) - x0 for x, _ in lines], np.int64)
        xs = xs[(xs >= 0) & (xs < aw)]
        img[:, xs] = hex_rgb(look.TRACK_BAR)
        img[rows >= len(slots)] = base[3]
        self.paint_notes(img, rows)
        self._photo = Photo(self, aw, ah)
        self._photo.put(img)
        self.create_image(x0, y0, image=self._photo.photo, anchor="nw")

    def paint_notes(self, img, rows):
        """Each track's notes as lines in its row (its own lowest .. highest key over the row's height), in its
        channel's colour; a note shorter than a pixel still shows one."""
        n, ppq = self.app.rendered, self.app.ppq
        ah, aw = img.shape[:2]
        if not len(n) or not len(self._slots):
            return
        first, last = self.top // self.row_h, (self.top + ah) // self.row_h
        lo_t, hi_t = self.view_t * ppq, (self.view_t + aw / self.sx) * ppq
        row = self._row_of[n[:, 4]]
        keep = (row >= first) & (row <= last) & (n[:, 1] > lo_t) & (n[:, 0] < hi_t)
        if not keep.any():
            return
        n, row = n[keep], row[keep]
        slot, key = n[:, 4], n[:, 2]
        lo, hi = self._lo[slot], self._hi[slot]
        inner = max(1, self.row_h - 2 * round(PAD * self.scale) - 1)
        span = hi - lo + 1
        thick = np.maximum(1, inner // span)  # (few keys: thicker lines)
        used = np.minimum(inner, span * thick)
        y = row * self.row_h - self.top + round(PAD * self.scale) + (inner - used) // 2 + (hi - key) * used // span
        xa = np.floor((n[:, 0] / ppq - self.view_t) * self.sx).astype(np.int64)
        xb = np.ceil((n[:, 1] / ppq - self.view_t) * self.sx).astype(np.int64) - 1
        xa, xb = np.clip(xa, 0, aw - 1), np.clip(np.maximum(xa, xb), 0, aw - 1)
        cover = np.zeros((ah, aw + 1), np.int32)
        for k in range(int(thick.max())):
            yk = y + k
            on = (thick > k) & (yk >= 0) & (yk < ah)
            if not on.any():
                continue
            flat = cover.reshape(-1)
            size = flat.size
            flat += np.bincount(yk[on] * (aw + 1) + xa[on], minlength=size).astype(np.int32)
            flat -= np.bincount(yk[on] * (aw + 1) + xb[on] + 1, minlength=size).astype(np.int32)
        hit = np.cumsum(cover, axis=1)[:, :aw] > 0
        colours = np.array([hex_rgb(look.TRACK_MUTED if int(s) in self.muted else self.colour_of(int(s)))
                            for s in self._slots], np.uint8)  # (muted: grey)
        ok = rows < len(self._slots)
        per_row = np.zeros((ah, 3), np.uint8)
        per_row[ok] = colours[rows[ok]]
        img[hit] = np.broadcast_to(per_row[:, None, :], img.shape)[hit]

    def draw_ruler(self, w, lines):
        s, top = self.scale, self.ruler_h
        self.create_rectangle(0, 0, w, top, fill=look.RULER_BG, outline="")
        font = look.font(8, fit=True)
        measure = tkfont.Font(font=font).measure
        end = -math.inf
        for x, n in lines:  # bar numbers in the bottom half
            if x < self.name_w or x < end:
                continue
            self.create_line(x, top - 6 * s, x, top, fill=look.RULER_TICK)
            self.create_text(x + 3, top - 9 * s, text=str(n + 1), anchor="w", fill=look.RULER_TEXT, font=font)
            end = x + 3 + measure(str(n + 1)) + 4
        tempo = self.app.tempo
        marks = {}  # beat -> texts: time / key signatures in the top half
        for sg in sigs_of(tempo):
            marks.setdefault(round(sg[0], 6), []).append(sig_text(sg))
        for k in tempo.get("keysigs") or []:
            marks.setdefault(round(k[0], 6), []).append(key_name(k[1], k[2]))
        end = -math.inf
        small = look.font(7, fit=True)
        for beat in sorted(marks):
            x = self.t2x(beat)
            text = f"({', '.join(marks[beat])})"
            if x < self.name_w - 1 or x < end or x > w:
                continue
            self.create_text(x + 1, 7 * s, text=text, anchor="w", fill=look.RULER_TEXT, font=small)
            end = x + measure(text)
        self.create_line(0, top, w, top, fill=look.ROLL_EDGE)
        self.create_rectangle(0, 0, self.name_w, top, fill=look.RULER_BG, outline="")

    def draw_conductor(self, w):
        """The Conductor row: a short line at each tempo point and time / key signature."""
        s, y0 = self.scale, self.ruler_h
        y1 = y0 + self.row_h
        self.create_rectangle(self.name_w, y0, w, y1, fill=look.TRACK_ROW, outline="")
        tempo = self.app.tempo
        beats = {p[0] for p in tempo.get("points") or []} | {sg[0] for sg in sigs_of(tempo)[1:]} \
            | {k[0] for k in tempo.get("keysigs") or []}
        for b in beats:
            x = round(self.t2x(b))
            if self.name_w <= x <= w:
                self.create_line(x, y0 + 4 * s, x, y1 - 4 * s, fill=look.TRACK_CONDUCTOR, width=max(1, round(s)))
        self.create_line(0, y1, w, y1, fill=look.ROLL_EDGE)

    def draw_names(self, h):
        """The names' column: Conductor, then each track's name (cut with "…" when too long: the tip has it whole)
        and channel."""
        s, x1 = self.scale, self.name_w
        y0 = self.ruler_h
        font = look.font(9)
        measure = tkfont.Font(font=font).measure
        self.create_rectangle(0, y0, x1, h, fill=look.TRACK_NAME_BG, outline="")
        top = self.area()[1]
        first = self.top // self.row_h
        slots = self._slots
        for row in range(first, len(slots)):
            ya = top + row * self.row_h - self.top
            if ya > h:
                break
            yb = ya + self.row_h
            slot = int(slots[row])
            picked = slot == self.current
            if picked:
                self.create_rectangle(0, max(ya, top), x1, yb, fill=look.TRACK_PICK, outline="")
            if self.row_h >= 12 * s:
                colour = look.TRACK_PICK_TEXT if picked else look.TRACK_TEXT
                ch = self.channel_of(slot)
                room = x1 - 44 * s - measure(ch) - 8 * s
                name = cut_text(self.name_of(row), room, measure)
                mid = (ya + yb) / 2
                if mid > top:
                    if slot in self.behind:  # (shown behind the current track: a dot in its colour)
                        r = 3.5 * s
                        self.create_oval(10 * s - r, mid - r, 10 * s + r, mid + r, fill=self.colour_of(slot),
                                         outline=colour)
                    self.create_text(18 * s, mid, text=name, anchor="w", fill=colour, font=font)
                    self.create_text(x1 - 26 * s, mid, text=ch, anchor="e",
                                     fill=look.TRACK_PICK_TEXT if picked else look.HINT, font=font)
                    self.draw_speaker(self.speaker_x(), mid, slot in self.muted, picked)
            self.create_line(0, yb - 1, x1, yb - 1, fill=look.TRACK_LINE)
        self.create_rectangle(0, y0, x1, top, fill=look.TRACK_NAME_BG, outline="")  # (rows scrolled under it)
        self.create_text(18 * s, y0 + self.row_h / 2, text=tr("tracks.conductor"), anchor="w", fill=look.TRACK_TEXT,
                         font=font)
        self.create_line(0, top, x1, top, fill=look.ROLL_EDGE)
        self.create_line(x1, 0, x1, h, fill=look.ROLL_EDGE)

    def draw_speaker(self, cx, cy, muted, picked):
        """A small speaker: sound waves beside it while heard, a red cross while muted."""
        s = self.scale
        colour = look.TRACK_PICK_TEXT if picked else look.TRACK_TEXT
        if muted:
            colour = look.HINT
        x = cx - 4 * s
        self.create_polygon(x - 3 * s, cy - 2 * s, x, cy - 2 * s, x + 3 * s, cy - 5 * s, x + 3 * s, cy + 5 * s,
                            x, cy + 2 * s, x - 3 * s, cy + 2 * s, fill=colour, outline="")
        if muted:
            a, b = x + 5 * s, x + 10 * s
            for y0, y1 in ((cy - 2.5 * s, cy + 2.5 * s), (cy + 2.5 * s, cy - 2.5 * s)):
                self.create_line(a, y0, b, y1, fill=look.ERROR, width=max(1, round(1.4 * s)))
        else:
            for r in (4 * s, 7 * s):
                self.create_arc(x + 3 * s - r, cy - r, x + 3 * s + r, cy + r, start=-40, extent=80, style="arc",
                                outline=colour, width=max(1, round(s)))

    def show_playhead(self):
        """The play line moved: while playing, the page turns near the right edge (like the piano roll)."""
        if self.sx is None or not self.winfo_ismapped():
            return
        x = self.t2x(self.app.playhead)
        if self.app.player.running and (x > self.winfo_width() - 6 * self.scale or x < self.name_w):
            self.view_t = self.app.playhead
            self.request_redraw()
            return
        self.draw_playhead()

    def draw_playhead(self):
        self.delete("playhead")
        if self.sx is None or not self.winfo_ismapped():
            return
        x = round(self.t2x(self.app.playhead))
        if self.name_w <= x <= self.winfo_width():
            self.create_line(x, 0, x, self.winfo_height(), fill=look.PLAY_LINE, width=max(1, round(self.scale)),
                             tags="playhead")


def build_strip(app, parent, tl):
    """The strip above the track list: Show multiple tracks… + Faint [n] %, the track list's own snap."""
    strip = ttk.Frame(parent, padding=(2, 2))
    b = ttk.Button(strip, text=tr("tracks.show_multiple"), command=tl.open_picker)
    b.pack(side="left")
    Tooltip(b, tr("tracks.show_multiple_tip"))
    lb = ttk.Label(strip, text=tr("tracks.faint"))
    lb.pack(side="left", padx=(10, 4))
    e = ttk.Entry(strip, textvariable=tl.faint_var, width=4, justify="center")
    e.pack(side="left")
    pc = ttk.Label(strip, text="%")
    pc.pack(side="left", padx=(2, 0))
    e.bind("<Return>", lambda ev: (tl.on_faint(e), "break")[1])
    e.bind("<FocusOut>", lambda ev: tl.on_faint(e))
    Scrub(app, [(e, tl.faint_var, lambda: tl.on_faint(e))], (5, 20, 1), 0, 100, label=lb, drag_box=True,
          no_undo=True)
    for w in (lb, e, pc):
        Tooltip(w, tr("tracks.faint_tip"))
    good(e)
    ttk.Separator(strip, orient="vertical").pack(side="left", fill="y", padx=10)
    ttk.Label(strip, text=tr("app.snap")).pack(side="left", padx=(0, 4))
    SnapPicker(app, strip, tl.snap, tip=tr("tracks.snap_tip")).button.pack(side="left")
    tl.snap.trace_add("write", lambda *_: app.schedule_autosave())
    return strip


class BehindWindow(tk.Toplevel):
    """Show multiple tracks: which tracks the piano roll shows faintly behind the current one (Ctrl+click a row in
    the track list does the same). Not modal: changes show at once."""

    def __init__(self, tl):
        app = tl.app
        super().__init__(app)
        self.tl = tl
        self.title(tr("tracks.picker_title"))
        self.transient(app)
        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("tracks.picker_hint"), foreground=look.HINT, wraplength=round(260 * app.scale),
                  justify="left").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(box)
        row.pack(fill="both", expand=True)
        self.list = tk.Listbox(row, selectmode="multiple", exportselection=False, activestyle="none", height=14,
                               width=26)
        sb = ttk.Scrollbar(row, orient="vertical", command=self.list.yview)
        self.list.config(yscrollcommand=sb.set)
        self.list.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.list.bind("<<ListboxSelect>>", lambda e: self.picked())
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text=tr("tracks.picker_all"), command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(btns, text=tr("tracks.picker_none"), command=lambda: self.set_all(False)).pack(side="left",
                                                                                                 padx=(4, 0))
        ttk.Button(btns, text=tr("tracks.picker_close"), command=self.close).pack(side="right")
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.slots = []
        self.fill()
        remember_place(self, "tracks_picker")

    def fill(self):
        """The rows again (the tracks / the current one changed), the picked ones selected."""
        tl, lb = self.tl, self.list
        slots = [int(s) for s in tl.tracks()]
        top = lb.yview()[0]
        lb.delete(0, "end")
        for row, slot in enumerate(slots):
            text = f"{tl.name_of(row)}  ·  {tl.channel_of(slot)}"
            lb.insert("end", tr("tracks.picker_current", name=text) if slot == tl.current else text)
            if slot == tl.current:
                lb.itemconfig(row, foreground=look.HINT)
            if slot in tl.behind:
                lb.selection_set(row)
        self.slots = slots
        lb.yview_moveto(top)

    def picked(self):
        self.tl.behind = {self.slots[i] for i in self.list.curselection() if i < len(self.slots)}
        self.tl.shown_changed()

    def set_all(self, on):
        self.tl.behind = set(self.slots) if on else set()
        self.tl.shown_changed()

    def close(self):
        self.tl.picker = None
        self.destroy()


def cut_text(text, room, measure):
    """text, or as much of it as fits in room pixels followed by "…"."""
    if measure(text) <= room:
        return text
    while text and measure(text + "…") > room:
        text = text[:-1]
    return text + "…"
