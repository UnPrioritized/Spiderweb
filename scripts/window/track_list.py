"""The track list (user 2026-10-23, like Domino's): Tab swaps the piano roll <-> this view in the same place of the
main window. One row per track the MIDI export / Export to Domino would write (midi_out.track_slots: the same
tracks in the same order, both exports alike), each with a tiny picture of its notes in its channel's colour, a
Conductor row on top. View only: Spiderweb picks the tracks. Zoomed out, the ruler and its lines count several
bars per column (meter.bar_lines), like Domino's.

Picture: the rows' stripes, bar lines and notes painted into one NumPy picture (speed.Photo) per redraw; names,
ruler and play line are canvas items. Each track's notes fill its row from its own lowest to highest key."""

import json
import math
import threading
from collections import OrderedDict, deque
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc
from files.midi_out import track_slots
from files.own_tracks import HAND, add_tracks, from_rows, into_ids, move_rows, new_id, plan
from files.snap import clean_snap, snap_beats
from files.speed import Photo
from notes.engine import BEATS_FAR, OWN0, slot_track_channel, track_ids
from notes.mask import pinned_slots
from notes.meter import bar_line, bar_lines, key_name, sig_text, sigs_of, where
from notes.tempo import bpm_text, tempo_text
from notes.tempo_file import heard_tempo
from roll import roll_loaded as RL
from roll.roll_draw import TRACK_FADE
from roll.roll_shared import (COLOURS_16, MAX_SX, MIN_SX, SELECT_CURSOR, SLOT_COLORS, PlayFollow, grab_while_panning,
                              slot_colours)
from window import look
from window.track_windows import AddTracksBox, PropertiesBox
from window.widgets import Scrub, Tooltip, bad, good, remember_place

NAME_W = 150    # the names' column (x scale)
RULER_H = 28    # the ruler: signatures on top, bar numbers under them
ROW_H = 26      # a row's height at first; zoomed up / down between ROW_LEAST and ROW_MOST
ROW_LEAST, ROW_MOST = 8, 160
ALL_H = 40      # the All tracks row's height at least (taller than a track, user 2026-10-26: not read as one more)
ALL_ROW, CONDUCTOR_ROW = -2, -1  # row_at's answers for the two rows on top
PAD = 3         # free pixels above / below a row's notes
COLUMN = 28     # bar numbers closer than this many pixels: several bars per column (2, 4, 8...)
TIP_SHAPES = 8  # shapes named in a row's tip at most
FAINT = 25      # % the tracks shown behind the current one are drawn at, at first (0 = hidden)
ROWS_INLINE_S = 0.12  # a loaded MIDI's rows read exactly in the redraw itself for this long at most (the picture is
# made again only after zooming or scrolling far: a slower one then beats rows changing look)
TILE = 512  # px: a loaded MIDI's rows are read in blocks this wide along the song, kept while the zoom stays, and
# the next ones read ahead in the background (user 2026-10-24, Shinki: each new picture read every row again,
# ~100 ms stops while scrolling)
TILES_BYTES = 128 << 20  # blocks kept at most (the oldest used go first)
MENU_CLICK_S = 0.25  # a press this soon after the right-click menu closed = the click that closed it (Windows)
MOVE_PX = 5  # a press on a track's name moved this far (x scale) = dragging the tracks to another place
EDGE_MS = 40  # how often the list scrolls while tracks are dragged past its top / bottom


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
        self.owners = set()  # id() of the shapes with notes on it (follow: it keeps showing them when channels change)
        self.behind = set()  # slots shown faintly behind it on the piano roll ("Show multiple tracks")
        self.onion = app.onion  # ... while this is on (the main toolbar's toggle; off = one track)
        self.onion.trace_add("write", lambda *_: self.shown_changed())
        self.selected = set()  # tracks Ctrl+clicked along with the highlighted one (like Domino's; the right-click
        # menu works on them all), never the highlighted one itself
        self.anchor = None  # where a Shift+click's range starts: (the last plain / Ctrl+clicked track, the
        # highlighted one then)
        self._sweep = None  # a drag from a speaker over the rows: (muted or not, the last row it reached)
        # a press on a track's name, dragged = the tracks moved (group C): {"y", "on" (dragging), "gap" (the row the
        # line is above), "keep" (pressed on one of several selected: a click without a drag picks it alone),
        # "s", "job" (scrolling at an edge)}
        self._move = None
        self.muted = set()   # slots not played (the speaker in their row; playback only, never the files)
        self.faint = FAINT
        self.faint_var = tk.StringVar(value=str(FAINT))
        # the track list's own snap (like Domino's; Bar at first), shown by the toolbar's Snap while the list shows
        self.snap = tk.StringVar(value="bar")
        self.snap.trace_add("write", lambda *_: app.schedule_autosave())
        self.picker = None  # the "Show multiple tracks" window while open
        self.bars = ()     # its scrollbars (zoombar.add_zoom_bars)
        self._made = None  # (rendered, pad, use10) the tracks below were worked out for
        self._slots = np.zeros(0, np.int64)  # the tracks: their slots, in order
        self._filled = self._slots  # the slots with notes (own_tracks.counted: a track of yours counts only these)
        self._all = self._slots  # every row's track: a loaded MIDI's (-1 - its number) first, then the slots
        self.lead = 0  # rows of a loaded MIDI's tracks (Load MIDI) before Spiderweb's own
        self._hand = {}  # id -> the own track (added by hand, files/own_tracks.py) of each such row
        self.menu_shut = 0.0  # when the right-click menu last closed (perf_counter)
        self._row_of = np.zeros(0, np.int64)  # slot -> row (-1: none)
        self._ranges = None  # (each slot's lowest keys, highest keys): key_ranges, worked out when drawn
        self._ids = []  # id() of the shapes when the tracks were worked out (their notes' owner numbers)
        self._sheet = None  # paint's picture: {"key", "x", "y" (its first pixel), "w", "h", "photo"}
        self._tempo_sheet = None  # (key, photo) of the Conductor row's, as wide
        self._all_sheet = None  # (key, photo) of the All tracks row's, as wide
        # a loaded MIDI's exact rows: the zoom they're for, {(row, block): keys x TILE mask, False = unreadable}
        # (oldest used first), their bytes, the background reading
        self._row_place, self._tiles, self._tile_bytes, self._row_job = None, OrderedDict(), 0, None
        self._heard = None  # (key, heard()'s answer) for the tempo line it was worked out for
        self._tip_memo = (None, "")  # (row, notes, shape count) -> row_tip's text
        self._fonts = {}  # font -> (tk font, its measuring function: measurer)
        self._pan = None
        self.play_follow = PlayFollow(app)
        self._job = None
        self.tip = Tooltip(self, "")
        self.bind("<Configure>", lambda e: self.request_redraw())
        # (shown again by Tab at the same size: no <Configure>, and the redraw asked for before it was mapped did
        # nothing: the old picture stayed, user 2026-10-23)
        self.bind("<Map>", lambda e: self.request_redraw())
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<ButtonPress-3>", self.on_right)
        self.bind("<Double-Button-1>", self.on_double)
        self.bind("<Escape>", self.on_escape)
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
        n, pad, use10, lm, own = app.rendered, app.pad_tracks(), app.picture_use10, app.loaded, app.own_written()
        made = self._made
        if made is None or made[0] is not n or made[1:] != (pad, use10, id(lm), own):
            slots = track_slots(n, pad).astype(np.int64)
            size = int(slots.max()) + 1 if len(slots) else 0
            # (the tracks added by hand among them: own_tracks.plan, the exports' order)
            self._filled = np.unique(n[:, 4]) if len(n) else n[:0, 4]  # (tracks with notes: own_tracks.counted)
            rows = plan(slots, own, self._filled)
            self._hand = {HAND + t["id"]: t for t in own}
            mine = np.array([r if type(r) is int else HAND + r["id"] for r in rows], np.int64)
            row_of = np.full(size, -1, np.int64)
            row_of[slots] = np.flatnonzero(mine < HAND)
            # muted / picked tracks keep their music when the tracks are handed out again (like the highlight:
            # follow; hunt 10-23, user picked A), else they're let go, never moved onto other music
            shapes = self.app.shapes
            if made is not None and (self.muted or self.behind):
                old, ids = made[0], self._ids

                def keep(s):
                    if s < 0 or s >= HAND:  # (a loaded MIDI's / an own track: never handed out again)
                        return s
                    had = {ids[i] for i in np.unique(old[old[:, 4] == s, 5]).tolist() if i < len(ids)}
                    if not any(id(sh) in had for sh in shapes):  # (an empty track, or shapes made anew by undo:
                        return s                                   # kept on its number, like the highlight)
                    return moved_to(shapes, n[:, 5], track_ids(n[:, 4]), had, s)
                self.muted = {s for s in map(keep, self.muted) if s is not None}
                self.behind = {s for s in map(keep, self.behind) if s is not None}
            self._ids = [id(sh) for sh in shapes]
            self._slots, self._row_of, self._ranges, self._rows_of_notes = slots, row_of, None, None
            self._mine = mine
            # a loaded MIDI's tracks first (Load MIDI: their numbers -1, -2, ...), Spiderweb's own after them (the
            # export adds them as new tracks after the file's)
            self.lead = len(lm.tracks) if lm is not None else 0
            self._all = np.concatenate([-1 - np.arange(self.lead, dtype=np.int64), mine])
            self._made = (n, pad, use10, id(lm), own)

            def there(s):
                if s >= HAND:
                    return s in self._hand
                return -s - 1 < self.lead if s < 0 else s < size and row_of[s] >= 0
            if self.current is not None and not there(self.current):
                self.current = None  # (its track is gone)
            self.behind = {s for s in self.behind if there(s)}
            self.muted = {s for s in self.muted if there(s)}
            self.selected = {s for s in self.selected if there(s)}
        return self._all

    def row_of_id(self, s):
        """The row of track s (a slot, or a loaded MIDI's track as -1 - its number); None = not listed."""
        self.tracks()
        if s is None:
            return None
        if s < 0:
            return -s - 1 if -s - 1 < self.lead else None
        if s >= HAND:
            at = np.flatnonzero(self._all == s)
            return int(at[0]) if len(at) else None
        return self.lead + int(self._row_of[s]) if s < len(self._row_of) and self._row_of[s] >= 0 else None

    def note_rows(self):
        """Each note's row among Spiderweb's own tracks (lead not counted; -1: none), worked out when asked."""
        self.tracks()
        if self._rows_of_notes is None:
            n, mine = self._made[0], self._mine
            tid = track_ids(n[:, 4])
            out = np.full(len(n), -1, np.int64)
            auto = tid < HAND
            ok = auto & (tid < len(self._row_of))
            out[ok] = self._row_of[tid[ok]]
            if not auto.all():  # (a track added by hand: its row)
                hand_rows = np.flatnonzero(mine >= HAND)
                o = np.argsort(mine[hand_rows])
                ids = mine[hand_rows][o]
                at = np.searchsorted(ids, tid[~auto])
                found = (at < len(ids)) & (ids[np.minimum(at, len(ids) - 1)] == tid[~auto])
                out[~auto] = np.where(found, hand_rows[o][np.minimum(at, len(ids) - 1)], -1)
            self._rows_of_notes = out
        return self._rows_of_notes

    def key_ranges(self):
        """(lowest, highest) key of each row's notes (Spiderweb's own rows; -1 / 1 << 30 for none), only when the
        list is drawn (hunt 10-23: every edit of a 2M-note song paid for it)."""
        self.tracks()
        if self._ranges is None:
            n, size = self._made[0], len(self._mine)
            lo, hi = np.full(size, 1 << 30, np.int64), np.full(size, -1, np.int64)
            if len(n):
                row = self.note_rows()
                ok = row >= 0
                np.minimum.at(lo, row[ok], n[ok, 2])
                np.maximum.at(hi, row[ok], n[ok, 2])
            self._ranges = (lo, hi)
        return self._ranges

    def forget(self):
        """Another project opened: nothing muted, highlighted or picked behind, back at its start; the zoom stays
        (user 2026-10-23: remembered)."""
        self.current, self.behind, self.muted, self.owners, self.selected = None, set(), set(), set(), set()
        self.view_t, self.top, self._sheet = 0.0, 0, None
        sync_onion(self.app)
        self.request_redraw()

    def unmuted(self, notes):
        """notes without the muted tracks' (App.play_apart: playback only)."""
        if self.muted:
            self.tracks()  # (the notes were made again: the mutes follow their music)
        if not self.muted or not len(notes):
            return notes
        return notes[~np.isin(track_ids(notes[:, 4]), list(self.muted))]

    def toggle_mute(self, slot):
        self.muted ^= {slot}
        self.request_redraw()
        self.app.play_changed()

    # ------------------------------------------------------------ settings (autosaved, no undo step)

    def state(self):
        """Kept in the autosave's window settings: Faint, the snap and the view (user 2026-10-23: zoom remembered,
        like Domino's; never fitted again once there is one)."""
        out = {"faint": self.faint, "snap": self.snap.get(), "row_h": round(self.row_h / self.scale, 3)}
        if self.sx is not None:
            out.update(sx=self.sx, t=self.view_t, top=round(self.top / self.scale))
        return out

    def set_state(self, data):
        if not isinstance(data, dict):
            return
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # noqa: E731
        if type(data.get("faint")) is int and 0 <= data["faint"] <= 100:
            self.faint = TRACK_FADE[0] = data["faint"]
            self.faint_var.set(str(self.faint))
        if isinstance(data.get("snap"), str):
            self.snap.set(clean_snap(data["snap"]))
        if num(data.get("row_h")):
            self.row_h = round(min(ROW_MOST, max(ROW_LEAST, data["row_h"])) * self.scale)
        if num(data.get("sx")) and MIN_SX <= data["sx"] <= MAX_SX:
            self.sx = float(data["sx"])
            # (past the piano roll's far limit = a damaged file: the bar lines' loop never ended there, hunt 10-23)
            self.view_t = min(BEATS_FAR, max(0.0, float(data["t"]))) if num(data.get("t")) else 0.0
            self.top = min(10 ** 9, max(0, round(data["top"] * self.scale))) if num(data.get("top")) else 0

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

    def set_behind(self, slots):
        """The tracks shown behind the highlighted one; picking some switches the main toolbar's toggle on."""
        grew = bool(set(slots) - self.behind)
        self.behind = set(slots)
        if grew and not self.onion.get():
            self.onion.set(True)  # (its trace redraws)
        else:
            self.shown_changed()

    def behind_near(self, step):
        """Show multiple tracks ▾ Previous / Next track: the track before / after the highlighted one."""
        slots = [int(s) for s in self.tracks()]
        if self.current not in slots:
            return
        at = slots.index(self.current) + step
        if 0 <= at < len(slots):
            self.set_behind({slots[at]})

    def behind_alike(self):
        """... Tracks of the same colour: every track whose notes have the highlighted one's colour (channel)."""
        if self.current is None:
            return
        colour = self.colour_of(self.current)
        self.set_behind({int(s) for s in self.tracks() if int(s) != self.current and self.colour_of(int(s)) == colour})

    def shown_changed(self):
        """Which tracks show changed: the list, the picker and the piano roll (when it shows one track) again."""
        self.request_redraw()
        if self.picker:
            self.picker.fill()
        sync_onion(self.app)
        self.app.track_view()
        self.app.roll.request_redraw()

    def follow(self, drawn, slots, sels):
        """After the notes were made again, with a track highlighted (hunt 2026-10-23, user picked A + C):
        - channels handed out again (an earlier shape drawn over others): the highlight goes to the track holding
          most of the shapes it showed, so it keeps showing the same music;
        - selected shapes (just drawn, pasted, moved) whose notes are all on hidden tracks: the highlight goes to
          their track; spread over several tracks = nothing highlighted (every track shows), never a shape vanishing
          under the mouse.
        slots: each note's real track (drawn's own can be Channel per strip's colours)."""
        if self.current is None or not len(drawn):
            return
        was = self.current
        got = (moved_to(self.app.shapes, drawn[:, 5], slots, self.owners, self.current) if 0 <= self.current < HAND
               else None)  # (a loaded MIDI's / an own track keeps its place)
        if got is not None:
            self.current = got
        if sels:
            at = np.isin(drawn[:, 5], list(sels))
            owner, slot = drawn[at, 5], slots[at]
            seen = np.isin(slot, list(self.shown_slots()))
            lost = np.setdiff1d(np.unique(owner), np.unique(owner[seen]))  # (none of their notes shown)
            if len(lost):
                where = np.unique(slot[np.isin(owner, lost)])
                self.current = int(where[0]) if len(where) == 1 else None
        if self.current != was:
            self.request_redraw()
            sync_onion(self.app)

    def shown_slots(self):
        """The tracks the piano roll shows: the highlighted one + the ones behind it while Multiple tracks is on."""
        if self.current is None:
            return set()
        return {self.current} | (self.behind if self.onion.get() and self.faint > 0 else set())

    def filtered(self, drawn, slot, back):
        """(the notes the piano roll paints, the owner number given to the faint ones or None): with a track
        highlighted only its notes, the tracks picked behind it as faint copies (velocity -2 - v, owner back), the
        rest left out; nothing highlighted = drawn as it is. slot: each note's real track."""
        if self.current is None or not len(drawn):  # (nothing to work out: no cost on big songs, hunt 10-23)
            self.owners = set()
            return drawn, None
        self.tracks()
        if self.current is None:  # (its track is gone)
            self.owners = set()
            return drawn, None
        # (the shapes it shows now: follow keeps showing them when channels are handed out again)
        self.owners = shape_ids(self.app.shapes, drawn[slot == self.current, 5])
        out = [drawn[slot == self.current]]
        behind = sorted(self.behind - {self.current}) if self.faint > 0 and self.onion.get() else []
        if behind:
            faint = drawn[np.isin(slot, behind)].copy()
            if len(faint):
                # (-2 - velocity: painted faint in its velocity's shade; a mask's ghost (-1) stays one)
                faint[:, 3] = np.where(faint[:, 3] > 0, -2 - faint[:, 3], faint[:, 3])
                faint[:, 5] = back
                out.insert(0, faint)
                return np.concatenate(out), back
        return out[0], None

    def open_picker(self):
        if self.picker:
            self.picker.lift()
            return
        self.picker = BehindWindow(self)

    def name_of(self, row):
        """A loaded MIDI's track: the name it has in the file, else "Track n" (like Spiderweb's own)."""
        if row < self.lead:
            name = self.app.loaded.tracks[row].name
            if name:
                return name
        else:
            own = self.own_track(int(self.tracks()[row]))
            if own is not None and own["name"]:
                return own["name"]
        return tr("tracks.name", n=row + 1)

    def own_track(self, s):
        """The own track (added by hand) of track id s, or None."""
        if s is None or s < HAND:
            return None
        self.tracks()  # (the tracks as they are now: undo / redo changes them while the list isn't drawn)
        return self._hand.get(s)

    def channel_of(self, slot):
        if slot < 0:  # (a loaded MIDI's track: its main channel; none without notes)
            ch = self.app.loaded.tracks[-slot - 1].channel
            return "" if ch is None else tr("tracks.channel", ch=ch + 1)
        if slot >= HAND:
            return tr("tracks.channel", ch=self._hand[slot]["ch"] + 1)
        return tr("tracks.channel", ch=slot_track_channel(slot, self.app.picture_use10)[1] + 1)

    def colour_of(self, slot):
        """The track's notes' colour: its channel's, as on the piano roll (the light look's darker edge colour shows
        better on thin lines; the dark look's fill colour)."""
        if slot < 0 or slot >= HAND:
            ch = self.app.loaded.tracks[-slot - 1].channel if slot < 0 else self._hand[slot]["ch"]
            fill, edge = SLOT_COLORS[COLOURS_16[ch or 0]]
        else:
            table = slot_colours(self.app.picture_use10)
            fill, edge = SLOT_COLORS[table[slot % len(table)]]
        return fill if look.DARK else edge

    # ------------------------------------------------------------ view

    def area(self):
        """(left, top) of the notes' part: right of the names, under the ruler, the All tracks and Conductor rows."""
        return self.name_w, self.conductor_y() + self.row_h

    def all_h(self):
        """The All tracks row's height: a bit taller than the tracks' (ALL_H at least, twice it at most)."""
        s = self.scale
        return max(round(ALL_H * s), min(self.row_h + round(12 * s), round(2 * ALL_H * s)))

    def conductor_y(self):
        """The Conductor row's top (under the All tracks row)."""
        return self.ruler_h + self.all_h()

    def t2x(self, beat):
        return self.name_w + (beat - self.view_t) * self.sx

    def x2t(self, x):
        return self.view_t + (x - self.name_w) / self.sx

    def song_end(self):
        """The last note's end in beats (at least 4 bars)."""
        n = self.app.rendered
        end = int(n[:, 1].max()) / self.app.ppq if len(n) else 0.0
        lm = self.app.loaded
        if lm is not None:
            end = max(end, lm.end / lm.ppq)
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
        if self.sx:  # (whole pixels: the kept picture moves by whole pixels, paint)
            self.view_t = max(0, round(min(self.view_t, BEATS_FAR) * self.sx)) / self.sx

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
        self.app.schedule_autosave()  # (the zoom is remembered: state)

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
        self.app.schedule_autosave()

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
        """The row under y: ALL_ROW / CONDUCTOR_ROW = the rows on top, None = the ruler / below the last track."""
        if y < self.ruler_h:
            return None
        if y < self.conductor_y():
            return ALL_ROW
        if y < self.area()[1]:
            return CONDUCTOR_ROW
        row = int((y - self.area()[1] + self.top) // self.row_h)
        return row if row < len(self.tracks()) else None

    def on_press(self, e):
        self.focus_set()
        self.tip.use()
        if time.perf_counter() - self.menu_shut < MENU_CLICK_S:  # (the click that closed the right-click menu:
            return                                                # Windows hands it on, like the Tempo pane's)
        playing = self.app.player.running  # (playing: the play line stays, user 2026-10-23)
        if e.y < self.ruler_h:  # the play line, on the track list's snap (Shift: free)
            if e.x >= self.name_w and self.sx and not playing:
                beat = max(0.0, self.x2t(e.x))
                self.app.set_playhead(beat if e.state & 0x1 else self.snapped(beat))
            return
        row = self.row_at(e.y)
        self._sweep = None
        self.end_move()
        if row is None:
            return
        if row >= 0 and self.on_speaker(e.x):  # its speaker: muted / heard; dragged on: every speaker crossed the
            slot = int(self.tracks()[row])     # same (user 2026-10-26)
            self.toggle_mute(slot)
            self._sweep = (slot in self.muted, row)
            return
        if row >= 0 and e.state & 0x5 and self.current is not None:  # Ctrl / Shift: several tracks (select)
            return self.select_click(row, e.state & 0x4, e.state & 0x1)
        if row >= 0 and e.x < self.name_w:  # (a track's name: dragged = the tracks moved, group C)
            s, group = int(self.tracks()[row]), self.picked_tracks()
            # (on one of several selected: they stay selected for the drag; let go without one = that track alone,
            # like a file list)
            self._move = {"y": e.y, "on": False, "gap": None, "keep": s in group and len(group) > 1, "s": s,
                          "job": None}
            if self._move["keep"]:
                return
        if e.x >= self.name_w and self.sx and not playing:  # (like many MIDI programs: the play line there, snapped)
            beat = max(0.0, self.x2t(e.x))
            self.app.set_playhead(self.snapped(beat))
        if row == CONDUCTOR_ROW:  # (the tempo's row: the highlight stays; All tracks is the row above it)
            return
        self.current = None if row < 0 else int(self.tracks()[row])
        self.selected, self.anchor = set(), (self.current, self.current)
        self.shown_changed()

    def select_click(self, row, ctrl, shift):
        """Ctrl / Shift clicks on a track while one is highlighted, like a file list (user 2026-10-26; the
        highlighted track keeps its deep blue, the others selected fainter):
        - Ctrl+click: that track in or out of the selection, the next Shift range starts there (anchor);
        - Shift+click: the tracks from the anchor to it, the rest dropped;
        - Ctrl+Shift+click: the same tracks added to the selection.
        Leaving out the highlighted track gives its deep blue to the first selected one left (never none)."""
        rows = [int(s) for s in self.tracks()]
        slot = rows[row]
        group = set(self.picked_tracks())
        if shift:
            # (the anchor counts while the highlight is the one it was set with: moved by Ctrl+wheel, a menu... =
            # from the highlighted track)
            anchor, was = self.anchor if self.anchor is not None else (None, None)
            at = rows.index(anchor if was == self.current and anchor in rows else self.current)
            a, b = sorted((at, row))
            span = set(rows[a:b + 1])
            group = group | span if ctrl else span
        else:
            if slot in group and len(group) == 1:
                return  # (the only one: stays highlighted)
            group ^= {slot}
        current = self.current if self.current in group else next(s for s in rows if s in group)
        self.selected = group - {current}
        if not shift:
            self.anchor = (slot, current)
        if current != self.current:
            self.current = current
            self.shown_changed()
        else:
            self.request_redraw()

    def step_track(self, way):
        """Ctrl+wheel on the piano roll / its panes (like Domino, user 2026-10-24): the next (way 1, wheel down) or
        previous track highlighted, empty ones too; the All tracks row (nothing highlighted) above the first one; stops
        at the top / last. The status line names it."""
        rows = self.tracks()
        if not len(rows):
            return
        slots = [None] + [int(s) for s in rows]
        at = slots.index(self.current) if self.current in slots else 0
        row = min(max(at + way, 0), len(slots) - 1)
        if slots[row] != self.current:
            self.current, self.selected = slots[row], set()
            self.shown_changed()
        if row == 0:
            return self.app.status.config(text=tr("tracks.all"))
        ch = self.channel_of(slots[row])
        name = self.name_of(row - 1)
        self.app.status.config(text=tr("tracks.stepped", name=name, ch=ch) if ch else name)

    def show_all(self):
        """The All tracks row highlighted (current None): the piano roll shows every track and Spiderweb picks new
        shapes' channels (Esc, a click on the row, All tracks in the ▾ list)."""
        self.selected = set()
        if self.current is not None:
            self.current = None
            self.shown_changed()
        else:
            self.request_redraw()

    def picked_tracks(self):
        """The highlighted track and the ones selected with it, in the list's order (the right-click menu's)."""
        group = self.selected | ({self.current} if self.current is not None else set())
        return [int(s) for s in self.tracks() if int(s) in group]

    def on_drag(self, e):
        """A drag from a speaker: every track the mouse crosses gets what the pressed one got (muted or heard). From
        a track's name: the tracks moved (move_drag)."""
        if self._move is not None:
            return self.move_drag(e.y)
        if self._sweep is None:
            return
        on, last = self._sweep
        row = self.row_at(min(max(e.y, self.area()[1]), self.winfo_height() - 1))
        if row is None or row < 0:
            row = len(self.tracks()) - 1 if e.y >= self.area()[1] else 0
        a, b = sorted((last, row))
        slots = {int(s) for s in self.tracks()[a:b + 1]}
        new = self.muted | slots if on else self.muted - slots
        self._sweep = (on, row)
        if new != self.muted:
            self.muted = new
            self.request_redraw()
            self.app.play_changed()

    def on_release(self, e):
        self._sweep = None
        m = self._move
        if m is None:
            return
        self.end_move()
        if m["on"]:
            if m["gap"] is not None:
                self.move_tracks(m["gap"])
        elif m["keep"]:  # (a click on one of several selected tracks: that one alone, like a file list)
            self.current, self.selected, self.anchor = m["s"], set(), (m["s"], m["s"])
            self.shown_changed()

    def on_escape(self, e):
        """Esc: tracks being dragged stay where they were; else the All tracks row (user 2026-10-23: the Conductor
        click alone was hidden)."""
        if self._move is not None and self._move["on"] and e.state & 0x100:  # (still held: a release missed leaves
            return self.end_move()                                          # no drag behind)
        self.end_move()
        self.show_all()

    def moving(self):
        """The tracks a drag from a name moves: the highlighted one and the ones selected with it (a loaded MIDI's
        stay: group D)."""
        return [s for s in self.picked_tracks() if s >= 0]

    def move_drag(self, y):
        """Tracks dragged by a name (group C, like Domino's): a line between two rows shows where they land; past
        the list's top / bottom it scrolls (move_edge)."""
        m = self._move
        if not m["on"]:
            if abs(y - m["y"]) < MOVE_PX * self.scale:
                return
            if not self.moving():
                self._move = None
                return
            m["on"] = True
            self.tip.hide()
        m["last"] = y
        top, h = self.area()[1], self.winfo_height()
        gap = round((y - top + self.top) / self.row_h)
        m["gap"] = min(max(gap, self.lead), len(self.tracks()))  # (never above a loaded MIDI's tracks: group D)
        if (y < top or y > h) and m["job"] is None:
            m["job"] = self.after(EDGE_MS, self.move_edge)
        self.request_redraw()

    def move_edge(self):
        m = self._move
        if m is None:
            return
        m["job"] = None
        y, top, h = m["last"], self.area()[1], self.winfo_height()
        way = -1 if y < top else 1 if y > h else 0
        old = self.top
        self.top += way * max(1, self.row_h // 2)
        self.clamp()
        if self.top != old:
            self.move_drag(y)

    def end_move(self):
        """The drag of tracks over (or called off): its line goes."""
        m, self._move = self._move, None
        if m is not None:
            if m["job"] is not None:
                self.after_cancel(m["job"])
            if m["on"]:
                self.request_redraw()

    def move_tracks(self, gap):
        """The dragged tracks (moving) put together above row gap, in their order: one undo step. Spiderweb's
        automatic tracks among them become yours first (take_tracks). Nothing moves when they're there already."""
        app = self.app
        group = self.moving()
        full = plan(self._slots, app.own_tracks, self._filled)  # (every track, also tinted ones left out of the list)
        ids = [r if type(r) is int else HAND + r["id"] for r in full]
        mine = [int(s) for s in self._mine]
        at = ids.index(mine[gap - self.lead]) if gap - self.lead < len(mine) else len(full)
        rows = move_rows(full, {ids.index(s) for s in group if s in ids}, at)
        if [r if type(r) is int else HAND + r["id"] for r in rows] == ids:
            return
        from window.app import can_go_in, placed_on
        autos = [s for s in group if s < HAND]
        r = app.rendered
        owners = []
        if autos:
            # (EVERY automatic track is taken, empty ones too, with every shape on them: nothing pinned, no track
            # split in two, nothing handed out again, only the order changes, like Domino's; user 2026-10-26, after
            # pinned colours kept empty tracks between them and the music left behind got new channels / gap tracks)
            autos = [int(s) for s in self._slots]
            owners = set(np.unique(r[r[:, 4] < OWN0, 5]).tolist()) if len(r) else set()
            # (a shape with no notes of its own now, e.g. all merged into others' in Single channel: it'd come
            # back on a track of Spiderweb's later)
            noted = set(np.unique(r[:, 5]).tolist()) if len(r) else set()
            chans = app.own_channels()
            owners |= {i for i, sh in enumerate(app.shapes)
                       if i not in noted and can_go_in(sh) and not placed_on(sh, chans)}
            owners |= {i for i, sh in enumerate(app.shapes) if any(v < 0 for v in placed_on(sh, chans))}  # (pinned)
            owners = sorted(owners)
        if any(not can_go_in(app.shapes[i]) for i in owners if i < len(app.shapes)):
            return app.status.config(text=tr("tracks.cant_move"))
        app.roll.end_typing()
        app.push_undo(name=tr("tracks.move_step"))
        taken = self.take_tracks(autos, owners) if autos else {}
        if taken:
            rows = [{"id": taken[x]} if type(x) is int and x in taken else x for x in rows]

            def mine_now(s):  # (an automatic track taken: its new id)
                return HAND + taken[s] if s in taken else s
            self.current = None if self.current is None else mine_now(self.current)
            self.selected = set(map(mine_now, self.selected))
            self.muted, self.behind = set(map(mine_now, self.muted)), set(map(mine_now, self.behind))
            app.shapes_changed()  # (its shapes on the new tracks: the automatic tracks as they are after)
            self.tracks()
        moved = {s - HAND for s in group if s >= HAND} | set(taken.values())
        # (a tinted track dragged = yours, like Properties)
        app.own_tracks = [{k: v for k, v in t.items() if k != "auto"} if t["id"] in moved else t
                          for t in from_rows(rows, self._slots, app.own_tracks, self._filled)]
        self.own_changed()
        if taken:
            app.status.config(text=trn("tracks.made_yours", len(taken), n=len(taken)))

    def take_tracks(self, autos, owners):
        """Spiderweb's automatic tracks autos (slots) made yours (group C, user 2026-10-26: touching a track makes it
        yours): a track of yours on each one's channel, and every shape with notes there (owners) laid on it, its
        colours on other automatic tracks pinned there (B11). {slot: the new track's id}."""
        from window.app import entry_slot, placed_on
        app = self.app
        use10, r, chans = app.picture_use10, app.rendered, app.own_channels()
        own, taken = list(app.own_tracks), {}
        for s in autos:
            v = new_id(own, app.own_next)
            app.own_next = v + 1
            own.append({"id": v, "name": "", "ch": slot_track_channel(s, use10)[1], "after": -1})
            taken[s] = v
        multi = app.channel_mode.get() == "auto"
        # (the notes sorted by shape once, each shape's read from there: a search through every note per shape took
        # 2 s with 1000 shapes, hunt 2026-10-26)
        by = np.argsort(r[:, 5], kind="stable")
        whose = r[by, 5]
        for i in owners:
            sh = app.shapes[i]
            if placed_on(sh, chans):  # (laid already: its entries on these tracks now go to the new ones)
                rule = sh.get("slots10") if isinstance(sh.get("slots10"), bool) else None
                for key in ("into", "into_rest"):
                    if sh.get(key) is not None:
                        sh[key] = [taken.get(entry_slot(v, chans, rule, use10), v) if v < 0 else v
                                   for v in into_ids({"into": sh[key]})]
                continue
            a, b = np.searchsorted(whose, [i, i + 1])
            mine = r[by[a:b]]
            slots = app.colour_slots(sh, mine) if len(mine) else [autos[0]]  # (no notes now: the first one)
            # (Single channel / As drawn: one track, all colours on it; a shape keeping its own channels in every
            # mode, e.g. an Extract, keeps every track, hunt 2026-10-26)
            entries = [taken.get(s, -1 - s) for s in (slots if multi or pinned_slots(sh, "single") else slots[:1])]
            for key in ("cols", "into_gap", "into_rest", "slots10"):
                sh.pop(key, None)
            sh["into"] = entries
            if any(v < 0 for v in entries):  # (fit_into's channel rule for automatic tracks it's pinned on)
                sh["slots10"] = bool(use10) and any(slot_track_channel(-1 - v, use10)[1] == 9
                                                     for v in entries if v < 0)
        app.own_tracks = own
        return taken

    def on_right(self, e):
        """Right-click a speaker: only this track plays (solo), again: every track (like many MIDI programs).
        Anywhere else under the ruler: the tracks' menu (track_menu)."""
        row = self.row_at(e.y)
        if e.y >= self.ruler_h and (row is None or row < 0 or not self.on_speaker(e.x)):
            return self.track_menu(e, row)
        if row is None or row < 0:
            return
        slot, every = int(self.tracks()[row]), {int(s) for s in self.tracks()}
        self.muted = set() if self.muted == every - {slot} else every - {slot}
        self.tip.hide()
        self.request_redraw()
        self.app.play_changed()

    # ------------------------------------------------------------ own tracks (added by hand: files/own_tracks.py)

    def track_menu(self, e, row):
        """Right-click on a row / below them (like Domino's): the row is highlighted (All tracks: none), then Add
        Track, Add Tracks…, Remove Track, Properties…. row: the track list row clicked (ALL_ROW / CONDUCTOR_ROW: new
        tracks before them all; the Conductor row and None = below the tracks: the highlight stays). The play line
        stays put."""
        self.tip.hide()
        s = int(self.tracks()[row]) if row is not None and row >= 0 else None
        # (on one of several selected tracks: they stay selected and the menu works on them all, like Domino's;
        # else highlighted first, like a click; user 2026-10-26)
        if row not in (None, CONDUCTOR_ROW) and s not in self.picked_tracks():
            self.current, self.selected = s, set()
            self.shown_changed()
            self.update_idletasks()
        group = self.picked_tracks() if s in self.picked_tracks() else [s]
        own = [t for t in group if self.own_track(t) is not None]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("tracks.add_track"), command=lambda: self.add_own(row, [self.next_channel(row)]))
        menu.add_command(label=tr("tracks.add_tracks"), command=lambda: self.ask_add(row))
        menu.add_separator()
        menu.add_command(label=tr("tracks.remove_track"), command=lambda: self.remove_own(own),
                         state="normal" if own else "disabled")
        menu.add_command(label=tr("tracks.properties"), command=lambda: self.ask_properties(s),
                         state="normal" if self.own_track(s) is not None and len(group) == 1 else "disabled")
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            self.menu_shut = time.perf_counter()  # (the click that closed it does nothing: on_press)

    def own_row(self, row):
        """A track list row as own_tracks.add_tracks' row: among Spiderweb's own tracks (-1 = before them all:
        the Conductor row, a loaded MIDI's track; None = after them all)."""
        if row is None:
            return None
        if row < self.lead:
            return -1
        own = self.app.own_tracks
        if len(self._hand) == len(own):
            return row - self.lead
        # (tinted tracks left out of the list, App.own_written: the row among all of them)
        s = int(self._mine[row - self.lead])
        full = [r if type(r) is int else HAND + r["id"] for r in plan(self._slots, own, self._filled)]
        return full.index(s) if s in full else row - self.lead

    def row_channel(self, row):
        """The channel (0-15) of the track in row, None for none (the Conductor row, a loaded track without notes)."""
        if row is None or row < 0 or row >= len(self.tracks()):
            return None
        s = int(self.tracks()[row])
        if s < 0:
            return self.app.loaded.tracks[-s - 1].channel
        if s >= HAND:
            return self._hand[s]["ch"]
        return slot_track_channel(s, self.app.picture_use10)[1]

    def next_channel(self, row=None):
        """Add Track's channel, like Domino's: the lowest channel no track uses yet (Spiderweb's own, the ones added
        by hand, a loaded MIDI's); all used: the one fewest tracks use (lowest first: 1, 2, 3... again, on port A,
        user 2026-10-26: not port B). Channel 10 skipped while "Use channel 10" is off."""
        counts = [0] * 16
        for r in range(len(self.tracks())):
            ch = self.row_channel(r)
            if ch is not None:
                counts[ch] += 1
        chs = [c for c in range(16) if c != 9 or self.app.picture_use10]
        return min(chs, key=lambda c: (counts[c], c))

    def set_own(self, new, name):
        """The own tracks replaced by new (one undo step called name)."""
        app = self.app
        app.push_undo(name=name)
        app.own_tracks = new
        self.own_changed()

    def own_changed(self):
        """The own tracks changed (set_own): the list, its picker and the notes again (a channel changed: the shapes
        drawn into the track take it; shapes_changed does the status line, title and autosave)."""
        self.tracks()
        self.shown_changed()
        self.app.shapes_changed()

    def add_own(self, row, chs):
        """One track per channel in chs, after row (own_row), the first of them highlighted."""
        app = self.app
        new, ids = add_tracks(app.own_tracks, self._slots, self.own_row(row), chs, app.own_next, self._filled)
        app.own_next = max(ids) + 1
        self.set_own(new, tr("tracks.add_step" if len(chs) == 1 else "tracks.add_many_step"))
        self.current = HAND + ids[0]
        self.shown_changed()
        at = self.row_of_id(self.current)
        if at is not None:  # (in view)
            y = at * self.row_h
            h = self.winfo_height() - self.area()[1]
            if y < self.top or y + self.row_h > self.top + h:
                self.top = y - h // 2
                self.clamp()
                self.request_redraw()

    def ask_add(self, row):
        """Add Tracks…: one track per channel ticked in its window, after row."""
        chs = AddTracksBox(self, self.app.picture_use10).run()
        if chs:
            self.add_own(row, chs)

    def ask_properties(self, s):
        """Properties… (an own track): its name and channel in a small window."""
        own = self.own_track(s)
        if own is None:
            return
        got = PropertiesBox(self, own["name"], own["ch"]).run()
        if got is not None:
            self.set_properties(s, *got)

    def set_properties(self, s, name, ch):
        own = self.own_track(s)
        if own is None or (own["name"], own["ch"]) == (name, ch):
            return
        # (a track Spiderweb added for a shape's colours: changed by hand = yours, kept even when unused)
        mine = {k: v for k, v in own.items() if k != "auto"}
        self.set_own([dict(mine, name=name, ch=ch) if t is own else t for t in self.app.own_tracks],
                     tr("tracks.properties_step"))

    def on_double(self, e):
        """Double-click an own track's name: its Properties (like Domino's). Any other second click = a click (Tk
        gives a double-click binding the press instead of <ButtonPress-1>)."""
        self.on_press(e)
        row = self.row_at(e.y)
        if row is not None and row >= 0 and e.x < self.name_w and not self.on_speaker(e.x):
            s = int(self.tracks()[row])
            if self.own_track(s) is not None:
                self.ask_properties(s)

    def remove_own(self, ss):
        """Remove Track on the tracks ss (own tracks only, for now; several = the selected ones): the shapes drawn
        into them go with them (like Domino), in the same undo step. A shape with only SOME of its colours on them
        (hunt 2026-10-26, user's pick: like Domino, only those notes go) becomes plain notes of its other colours,
        each still on its track; the status line counts both."""
        from window.app import placed_on
        owns = [t for t in map(self.own_track, ss) if t is not None]
        if not owns:
            return
        app = self.app
        ids = {t["id"] for t in owns}
        if self.current in ss:
            self.current = None
        self.selected -= set(ss)
        app.roll.end_typing()  # (first: it refreshes the panel, which must still see the old shapes)
        own = app.own_channels()
        laids = [placed_on(sh, own) for sh in app.shapes]
        hit = {i: laid for i, laid in enumerate(laids) if any(v in ids for v in laid)}
        gone = {i for i, laid in hit.items() if all(v in ids for v in laid)}
        part = sorted(set(hit) - gone)

        def drop(rows_of):  # (plain_copies' rows: the 5th column = each note's colour's place in its list)
            return {i: rows[~np.isin(np.minimum(rows[:, 4], len(hit[i]) - 1),
                                     [k for k, v in enumerate(hit[i]) if v in ids])] for i, rows in rows_of.items()}
        plain = app.plain_copies(part, together=drop) if part else {}
        for i, sh in plain.items():  # (its colours left, in order: each on the track it was on)
            sh["into"] = [v for v in hit[i] if v not in ids]
        gone |= set(part) - set(plain)
        app.push_undo(name=tr("tracks.remove_step" if len(owns) == 1 else "tracks.remove_many_step"))
        app.put_plain(plain)
        for i in sorted(gone, reverse=True):
            del app.shapes[i]
        rest = [i - sum(j < i for j in gone) for i in sorted(app.sels - gone)]
        app.select_many(rest, max(rest, default=None))
        app.own_tracks = [t for t in app.own_tracks if t["id"] not in ids]
        self.own_changed()
        said = []
        if gone:
            said.append(trn("tracks.removed_shapes", len(gone), n=len(gone)))
        if plain:
            said.append(trn("tracks.removed_plain", len(plain), n=len(plain)))
        if said:
            app.status.config(text=" ".join(said))

    def speaker_x(self):
        return self.name_w - 13 * self.scale

    def on_speaker(self, x):
        return abs(x - self.speaker_x()) <= 9 * self.scale

    def on_motion(self, e):
        row = self.row_at(e.y)
        cursor = SELECT_CURSOR if row is not None and e.x >= self.name_w else "arrow"  # (the Select tool's, like
        if getattr(self, "_cursor", None) != cursor:                                    # many MIDI programs)
            self._cursor = cursor
            self.config(cursor=cursor)
        if row is not None and row >= 0 and self.on_speaker(e.x):
            slot = int(self.tracks()[row])
            text = tr("tracks.unmute_tip" if slot in self.muted else "tracks.mute_tip")
        elif row == CONDUCTOR_ROW and e.x >= self.name_w and self.sx:
            text = self.bpm_tip(e.x)
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
            return tr("tracks.all_tip" if row == ALL_ROW else "tracks.conductor_tip")
        key = (row, self._made and id(self._made[0]), len(self.app.shapes), id(self.app.own_tracks))
        if self._tip_memo[0] == key:  # (worked out once per row: every mouse move took 60 ms on huge tracks)
            return self._tip_memo[1]
        text = self.row_tip_text(row)
        self._tip_memo = (key, text)
        return text

    def row_tip_text(self, row):
        slot = int(self.tracks()[row])
        if slot < 0:  # (a loaded MIDI's track)
            t = self.app.loaded.tracks[-slot - 1]
            lines = ["  ·  ".join(x for x in (self.name_of(row), self.channel_of(slot)) if x),
                     trn("tracks.notes", t.notes, n=f"{t.notes:,}")]
            used = int((t.chn > 0).sum())
            if used > 1:
                lines.append(trn("tracks.channels", used, n=used))
            lines.append(tr("tracks.from_file"))
            return "\n".join(lines)
        n = self.app.rendered
        mine = n[self.note_rows() == row - self.lead] if len(n) else n
        lines = [f"{self.name_of(row)}  ·  {self.channel_of(slot)}",
                 trn("tracks.notes", len(mine), n=f"{len(mine):,}")]
        if slot >= HAND:
            lines.insert(1, tr("tracks.added_tip" if self.own_track(slot).get("auto") else "tracks.own_tip"))
        if self.app.channel_mode.get() != "auto":  # (laid shapes' other colours: on this track until Multi channel)
            from window.app import placed_on
            entry, chans, more = slot - HAND if slot >= HAND else -1 - slot, self.app.own_channels(), set()
            for sh in self.app.shapes:
                laid = placed_on(sh, chans)
                if laid and laid[0] == entry:
                    more |= set(laid[1:]) - {entry}
            if more:
                lines.append(trn("tracks.waiting", len(more), n=len(more)))
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
        self.paint(w, h)
        self.draw_ruler(w, lines)
        self.draw_all(w)
        self.draw_conductor(w)
        self.draw_names(h)
        m = self._move
        if m is not None and m["on"] and m["gap"] is not None:  # (where dragged tracks land, Domino's black line)
            y = self.area()[1] + m["gap"] * self.row_h - self.top
            self.create_line(0, y, w, y, fill=look.TRACK_TEXT, width=max(2, round(2 * self.scale)))
        self.draw_playhead()
        for bar in self.bars:
            bar.refresh()

    def measurer(self, font):
        """font's text width in pixels, each text measured once (asking Tk every redraw was a third of it)."""
        got = self._fonts.get(font)
        if got is None:
            f, widths = tkfont.Font(font=font), {}

            def measure(text):
                w = widths.get(text)
                if w is None:
                    w = widths[text] = f.measure(text)
                return w
            got = self._fonts[font] = (f, measure)
        return got[1]

    def shown_bars(self, w):
        """[(x, bar number from 0)] of the bar lines shown: zoomed out only every 2nd, 4th... bar (Domino's
        columns of several bars)."""
        sigs = sigs_of(self.app.tempo)
        lo, hi = max(0.0, self.x2t(self.name_w)), self.x2t(w)
        return [(self.t2x(b), n) for b, n in bar_lines(sigs, lo, hi, COLUMN * self.scale / self.sx)]

    def left_px(self):
        """The view's left edge in whole pixels from the song's start (clamp keeps it whole)."""
        return int(round(self.view_t * self.sx))

    def paint(self, w, h):
        """The rows (stripes, the highlighted one, bar lines) and every track's notes as one picture, made twice
        the view's size around it and only moved while scrolling (user 2026-10-23: dragging the view lagged at 100k
        notes); made again when the view nears its edge or anything in it changes."""
        x0, y0 = self.area()
        aw, ah = w - x0, h - y0
        if aw < 1 or ah < 1:
            return
        ox = self.left_px()
        key = (self.sx, self.row_h, id(self._made[0]) if self._made else None, id(self._all), self.current,
               frozenset(self.muted), frozenset(self.selected), json.dumps(sigs_of(self.app.tempo)), aw, ah,
               self.app.ppq)
        sh = self._sheet
        if sh is None or sh["key"] != key or not (sh["x"] <= ox and ox + aw <= sh["x"] + sh["w"]
                                                   and sh["y"] <= self.top and self.top + ah <= sh["y"] + sh["h"]):
            cx = max(0, ox - aw // 2)
            if self.rows_height() <= ah:
                cy, ch = 0, ah
            else:
                cy, ch = max(0, self.top - ah // 2), 2 * ah
            img = self.paint_sheet(cx, cy, 2 * aw, ch)
            photo = sh["photo"] if sh is not None and sh["photo"].size == (2 * aw, ch) else Photo(self, 2 * aw, ch)
            photo.put(img)
            sh = self._sheet = {"key": key, "x": cx, "y": cy, "w": 2 * aw, "h": ch, "photo": photo}
        self.create_image(x0 - (ox - sh["x"]), y0 - (self.top - sh["y"]), image=sh["photo"].photo, anchor="nw")

    def paint_sheet(self, cx, cy, cw, ch):
        """paint's picture: cw x ch pixels from pixel cx of the song (sideways) and cy of the rows (down)."""
        rh, slots = self.row_h, self._all
        rows = (np.arange(ch) + cy) // rh  # (each pixel row's track row)
        base = np.array([hex_rgb(look.TRACK_ROW), hex_rgb(look.TRACK_ROW_ALT), hex_rgb(look.TRACK_PICKED),
                         hex_rgb(look.TRACK_EMPTY), hex_rgb(look.TRACK_SELECTED)], np.uint8)
        kind = np.where(rows >= len(slots), 3, rows % 2)
        for s in self.selected:  # (Ctrl+clicked along with the highlighted one: fainter)
            got = self.row_of_id(s)
            if got is not None:
                kind[rows == got] = 4
        at = self.row_of_id(self.current)
        if at is not None:
            kind[rows == at] = 2
        img = np.empty((ch, cw, 3), np.uint8)
        img[:] = base[kind][:, None, :]
        edge = ((np.arange(ch) + cy) % rh == rh - 1) & (rows < len(slots))  # (a faint line under each row)
        img[edge] = hex_rgb(look.TRACK_LINE)
        sigs = sigs_of(self.app.tempo)
        xs = np.array([round(b * self.sx) - cx for b, _ in bar_lines(sigs, cx / self.sx, (cx + cw) / self.sx,
                                                                     COLUMN * self.scale / self.sx)], np.int64)
        xs = xs[(xs >= 0) & (xs < cw)]
        img[:, xs] = hex_rgb(look.TRACK_BAR)
        img[rows >= len(slots)] = base[3]
        self.paint_loaded_rows(img, rows, cx, cy)
        self.paint_notes(img, rows, cx, cy)
        return img

    def paint_notes(self, img, rows, cx, cy):
        """Each track's notes as lines in its row (its own lowest .. highest key over the row's height), in its
        channel's colour; a note shorter than a pixel still shows one. img starts at pixel cx of the song, cy of
        the rows."""
        n, ppq = self.app.rendered, self.app.ppq
        ah, aw = img.shape[:2]
        if not len(n) or not len(self._mine):  # (every track one of yours: no automatic ones, still drawn)
            return
        first, last = cy // self.row_h, (cy + ah) // self.row_h
        lo_t, hi_t = cx / self.sx * ppq, (cx + aw) / self.sx * ppq
        own_row = self.note_rows()
        row = own_row + self.lead
        keep = (own_row >= 0) & (row >= first) & (row <= last) & (n[:, 1] > lo_t) & (n[:, 0] < hi_t)
        if not keep.any():
            return
        n, row = n[keep], row[keep]
        key = n[:, 2]
        lows, highs = self.key_ranges()
        lo, hi = lows[row - self.lead], highs[row - self.lead]
        inner = max(1, self.row_h - 2 * round(PAD * self.scale) - 1)
        span = hi - lo + 1
        thick = np.maximum(1, inner // span)  # (few keys: thicker lines)
        used = np.minimum(inner, span * thick)
        y = row * self.row_h - cy + round(PAD * self.scale) + (inner - used) // 2 + (hi - key) * used // span
        xa = np.floor(n[:, 0] / ppq * self.sx).astype(np.int64) - cx
        xb = np.ceil(n[:, 1] / ppq * self.sx).astype(np.int64) - 1 - cx
        xa, xb = np.clip(xa, 0, aw - 1), np.clip(np.maximum(xa, xb), 0, aw - 1)
        # each note's top pixel row painted once, in a picture `pad` rows taller on top (a thick line starting above
        # the picture still reaches into it); thick lines are then grown downwards row by row (hunt 2026-10-23: a
        # copy of every note per pixel of thickness took seconds and GBs for big one-key tracks)
        pad = int(thick.max()) - 1
        hh = ah + pad
        y = y + pad
        on = (y >= 0) & (y < hh)
        y, xa, xb = y[on], xa[on], xb[on]
        size = hh * (aw + 1)
        # (+1 where a line starts, -1 just past its end, added up along each pixel row: covered where > 0)
        cover = np.bincount(y * (aw + 1) + xa, minlength=size).astype(np.int32)
        cover -= np.bincount(y * (aw + 1) + xb + 1, minlength=size).astype(np.int32)
        hit = np.cumsum(cover.reshape(hh, aw + 1), axis=1, dtype=np.int32)[:, :aw] > 0
        if pad:  # covered if a line starts at most its thickness - 1 rows up, within the same track row
            at = np.arange(hh) - pad + cy  # (each picture row's pixel in the rows)
            track_row = at // self.row_h
            # (each track row's; a loaded MIDI's: 1)
            span = np.ones(len(self._all), np.int64)
            span[self.lead:] = np.maximum(1, highs - lows + 1)
            row_thick = np.maximum(1, inner // span)
            tk = np.ones(hh, np.int64)
            inside = (track_row >= 0) & (track_row < len(self._all))
            tk[inside] = row_thick[track_row[inside]]
            start = np.maximum(np.arange(hh) - tk + 1, track_row * self.row_h - cy + pad)  # (first row that counts)
            ups = np.cumsum(hit, axis=0, dtype=np.int32)
            below = np.where(start[:, None] > 0, ups[np.maximum(start - 1, 0)], 0)
            hit = (ups - below) > 0
        hit = hit[pad:]
        colours = np.array([hex_rgb(look.TRACK_MUTED if int(s) in self.muted else self.colour_of(int(s)))
                            for s in self._all], np.uint8)  # (muted: grey)
        ok = rows < len(self._all)
        per_row = np.zeros((ah, 3), np.uint8)
        per_row[ok] = colours[rows[ok]]
        img[hit] = np.broadcast_to(per_row[:, None, :], img.shape)[hit]

    def paint_loaded_rows(self, img, rows, cx, cy):
        """A loaded MIDI's tracks' rows, laid out like paint_notes' (the track's own lowest .. highest key over the
        row's height). EXACT (user 2026-10-24: the small pictures looked blocky): each row read from the file like
        the piano roll's exact pictures (roll_loaded.exact_tab, this track alone) in TILE-wide blocks along the song,
        kept while the zoom stays: at once while that's quick (ROWS_INLINE_S), the rest on a background thread that
        then reads the blocks around the picture ahead (the sheet painted again once the shown ones come); meanwhile
        the loading pass's small picture (keys x up to loaded.PIC_COLS columns)."""
        lm = self.app.loaded
        if lm is None or not self.lead:
            return
        ah, aw = img.shape[:2]
        first, last = max(0, cy // self.row_h), min(self.lead - 1, (cy + ah) // self.row_h)
        pad = round(PAD * self.scale)
        inner = max(1, self.row_h - 2 * pad - 1)
        ax = self.sx / lm.ppq  # (pixel c of the song = tick t * ax)
        place = (id(lm), lm.hide_ver, self.sx)  # (notes extracted / back: read again)
        if self._row_place != place:
            self._row_place = place
            self._tiles.clear()
            self._tile_bytes = 0
            self.stop_rows()
        self.take_rows()
        j0, j1 = cx // TILE, (cx + aw - 1) // TILE
        end_j = int(lm.end * ax) // TILE + 1  # (blocks from here on are past the song: empty, never read nor kept)
        # (the picture's own blocks: never pushed out by reading ahead, hunt 2026-10-25)
        self._shown_tiles = {(r, j) for r in range(first, last + 1) if lm.tracks[r].notes
                             for j in range(j0, min(j1 + 1, end_j))}
        rate = RL.rate_of(lm)
        until = time.perf_counter() + (ROWS_INLINE_S if RL.speed.loops() else 0)
        waiting = []
        for r in range(first, last + 1):
            t = lm.tracks[r]
            if not t.notes:
                continue
            parts = []
            for j in range(j0, j1 + 1):
                if j >= end_j:
                    parts.append(np.zeros((t.khi - t.klo + 1, TILE), bool))
                    continue
                got = self._tiles.get((r, j))
                if got is None:
                    if time.perf_counter() + RL.view_bytes(lm, (r,), ax, -float(j * TILE), TILE) / rate <= until:
                        got = row_mask(lm, r, ax, -float(j * TILE), TILE)
                        self.keep_tile((r, j), got)
                    else:
                        waiting.append((r, j))
                else:
                    self._tiles.move_to_end((r, j))
                parts.append(got)
            if all(isinstance(p, np.ndarray) for p in parts):
                got = np.concatenate(parts, axis=1)[:, cx - j0 * TILE:cx - j0 * TILE + aw]
            else:  # (blocks not read yet / unreadable (False): the small picture there)
                got = small_mask(lm, t, cx, aw, self.sx)
                if got is None:
                    if not any(isinstance(p, np.ndarray) for p in parts):
                        continue
                    got = np.zeros((t.khi - t.klo + 1, aw), bool)
                for j, p in enumerate(parts, j0):
                    if isinstance(p, np.ndarray):
                        a, b = max(cx, j * TILE), min(cx + aw, (j + 1) * TILE)
                        got[:, a - cx:b - cx] = p[:, a - j * TILE:b - j * TILE]
            span = t.khi - t.klo + 1
            thick = max(1, inner // span)
            used = min(inner, span * thick)
            y0 = r * self.row_h - cy + pad + (inner - used) // 2  # (the row's top key's first pixel row)
            ys = np.arange(max(0, y0), min(ah, y0 + used))
            if not len(ys):
                continue
            # (each pixel row = the keys it covers, top key first: any of them sounding)
            top = got[::-1][:-(-(ys[-1] + 1 - y0) * span // used)]  # (keys up to the last pixel row's, rounded up)
            k0 = (ys - y0) * span // used
            mask = np.logical_or.reduceat(top, k0, axis=0) if span > used else top[k0]
            colour = hex_rgb(look.TRACK_MUTED if -1 - r in self.muted else self.colour_of(-1 - r))
            img[ys[0]:ys[-1] + 1][mask] = colour
        if RL.changed(self, lm):  # (the file changed / went: said once, the small pictures stay)
            return
        # the background reading: the blocks shown blocky first, then the ones around the picture (half its width
        # each way along the song, half its height up and down) so scrolling finds them read
        half = (last - first) // 2
        ahead = [(r, j) for r in range(first, last + 1) if lm.tracks[r].notes
                 for j in range(max(0, (cx - aw // 2) // TILE), min((cx + aw + aw // 2) // TILE + 1, end_j))]
        ahead += [(r, j) for r in [*range(max(0, first - half), first), *range(last + 1, min(self.lead, last + half + 1))]
                  if lm.tracks[r].notes for j in range(j0, min(j1 + 1, end_j))]
        todo = list(dict.fromkeys(waiting + [k for k in ahead if k not in self._tiles]))
        job = self._row_job
        if todo and (job is None or waiting):  # (blocks shown blocky: read those first, now)
            self.stop_rows()
            job = self._row_job = RowJob(lm, place, todo, ax, set(waiting))
            threading.Thread(target=job.run, daemon=True, name="track rows").start()
            self.after(RL.POLL_MS, self._poll_rows)

    def keep_tile(self, key, mask):
        """mask kept as block key; past TILES_BYTES the oldest used go, never the picture's own (_shown_tiles).
        Returns False when there's no room for it (a block read ahead: not kept), else True."""
        def size(m):
            return m.nbytes if isinstance(m, np.ndarray) else 0
        old = self._tiles.pop(key, None)
        self._tile_bytes -= size(old)
        self._tiles[key] = mask
        self._tile_bytes += size(mask)
        if self._tile_bytes > TILES_BYTES:
            shown = getattr(self, "_shown_tiles", set())
            for k in list(self._tiles):
                if self._tile_bytes <= TILES_BYTES:
                    break
                if k != key and k not in shown:
                    self._tile_bytes -= size(self._tiles.pop(k))
            if self._tile_bytes > TILES_BYTES and key not in shown:  # (only shown ones left: no room for it)
                self._tile_bytes -= size(self._tiles.pop(key))
                return False
        return True

    def take_rows(self):
        """The background reading's blocks read so far, kept (no room left = the reading ahead stops). True when a
        block shown blocky came."""
        job, came = self._row_job, False
        if job is None or job.place != self._row_place:
            return False
        while job.out:
            key, mask = job.out.popleft()
            if not self.keep_tile(key, mask):
                job.stop = True
            came = came or key in job.shown
        return came

    def stop_rows(self):
        if self._row_job is not None:
            self._row_job.stop = True
            self._row_job = None

    def _poll_rows(self):
        job = self._row_job
        if job is None:
            return
        self.take_rows()
        if job.shown and all(k in self._tiles for k in job.shown):  # (the blocky ones all read: painted again
            job.shown = set()  # once, the reading ahead goes on)
            self._sheet = None
            self.request_redraw()
        if not job.done:
            self.after(RL.POLL_MS, self._poll_rows)
            return
        self._row_job = None
        RL.changed(self, job.lm)

    def draw_ruler(self, w, lines):
        s, top = self.scale, self.ruler_h
        self.create_rectangle(0, 0, w, top, fill=look.RULER_BG, outline="")
        font = look.font(8, fit=True)
        measure = self.measurer(font)
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

    def heard(self):
        """(ticks, BPMs) of the tempo as it plays (tempo_file.heard_tempo: catch-ups, Keep timing in place), the
        song's slowest and fastest; None without a tempo line (the project's BPM all through)."""
        app = self.app
        pane = getattr(app, "tempo_pane", None)
        base = pane.base() if pane is not None else 120.0
        n = app.rendered
        end = int(n[:, 1].max()) + 1 if len(n) else 0
        key = (tempo_text(app.tempo), base, app.ppq, end)
        if self._heard is not None and self._heard[0] == key:
            return self._heard[1]
        got = heard_tempo(app.tempo, base, app.ppq, end)
        if got:
            ticks = np.array([t for t, _ in got], np.int64)
            bpm = 60_000_000 / np.array([u for _, u in got], float)
            got = (ticks, bpm, float(bpm.min()), float(bpm.max()))
        else:
            got = (np.zeros(1, np.int64), np.array([float(base)]), float(base), float(base))
        self._heard = (key, got)
        return got

    def bpm_heights(self, xs, inner, cx=None):
        """The line height (pixels) at each pixel column xs of the notes' part: a line only where the tempo changes
        (like Domino's Conductor track, user 2026-10-23: no blocks between), the fastest of the changes in that
        column, scaled from the song's slowest (1 px) to its fastest (inner); on the doubling scale when the fastest
        is more than 4 x the slowest (a jump to 2000 BPM would flatten the rest). 0 = no change there. xs count from
        pixel cx of the song (None: the view's left edge)."""
        ticks, bpm, lo, hi = self.heard()
        cx = self.left_px() if cx is None else cx
        col = np.floor(ticks / self.app.ppq * self.sx).astype(np.int64) - cx
        on = (col >= 0) & (col < len(xs))
        val = np.full(len(xs), -np.inf)
        np.maximum.at(val, col[on], bpm[on])
        has = np.isfinite(val)
        if hi <= lo:
            share = np.full(len(xs), 0.5)
        elif hi > 4 * lo:
            share = np.log2(np.where(has, val, lo) / lo) / np.log2(hi / lo)
        else:
            share = (np.where(has, val, lo) - lo) / (hi - lo)
        return np.where(has, np.maximum(1, np.round(1 + share * (inner - 1))), 0).astype(np.int64)

    def draw_all(self, w):
        """The All tracks row (user 2026-10-26): every track's notes together, each in its track's colour, from the
        song's lowest to highest key; highlighted like a track while no track is (current None)."""
        y0, rh = self.ruler_h, self.all_h()
        sh = self._sheet
        if sh is not None and rh > 4:
            key = (sh["key"], sh["x"], sh["w"], rh)  # (the sheet's key holds the notes, zoom, highlight, mutes)
            got = self._all_sheet
            if got is None or got[0] != key:
                img = self.paint_all(sh["x"], sh["w"], rh - 1)
                photo = got[1] if got is not None and got[1].size == (sh["w"], rh - 1) else Photo(self, sh["w"], rh - 1)
                photo.put(img)
                got = self._all_sheet = (key, photo)
            self.create_image(self.name_w - (self.left_px() - sh["x"]), y0, image=got[1].photo, anchor="nw")
        self.create_line(0, y0 + rh - 1, w, y0 + rh - 1, fill=look.ROLL_EDGE)

    def paint_all(self, cx, cw, h):
        """draw_all's picture: cw x h pixels from pixel cx of the song. The tracks painted in order, a later one over
        an earlier one; Spiderweb's own over a loaded MIDI's (as in the export). A loaded MIDI's tracks come from
        the loading pass's small pictures (small_mask)."""
        img = np.empty((h, cw, 3), np.uint8)
        img[:] = hex_rgb(look.TRACK_PICKED if self.current is None else look.TRACK_ROW)
        sigs = sigs_of(self.app.tempo)
        xs = np.array([round(b * self.sx) - cx for b, _ in bar_lines(sigs, cx / self.sx, (cx + cw) / self.sx,
                                                                     COLUMN * self.scale / self.sx)], np.int64)
        img[:, xs[(xs >= 0) & (xs < cw)]] = hex_rgb(look.TRACK_BAR)
        n, ppq, lm = self.app.rendered, self.app.ppq, self.app.loaded
        lo_t, hi_t = cx / self.sx * ppq, (cx + cw) / self.sx * ppq
        if len(n):
            row = self.note_rows()
            keep = (row >= 0) & (n[:, 1] > lo_t) & (n[:, 0] < hi_t)
            n, row = n[keep], row[keep]
        loaded = [r for r in range(self.lead) if lm.tracks[r].notes] if self.lead else []
        keys = [int(n[:, 2].min()), int(n[:, 2].max())] if len(n) else []
        for r in loaded:
            keys += [lm.tracks[r].klo, lm.tracks[r].khi]
        if not keys:
            return img
        k_lo, k_hi = min(keys), max(keys)
        span = k_hi - k_lo + 1
        colours, ids = [], {}  # the colours in painting order; colour -> its number there

        def colour_id(slot):
            c = look.TRACK_MUTED if slot in self.muted else self.colour_of(slot)
            if c not in ids:
                ids[c] = len(colours)
                colours.append(c)
            return ids[c]
        top = np.full((span, cw), -1, np.int64)  # (each key x pixel: the colour painted last there, -1 none)
        for r in loaded:
            t = lm.tracks[r]
            got = small_mask(lm, t, cx, cw, self.sx)
            if got is not None:
                top[t.klo - k_lo:t.khi - k_lo + 1][got] = colour_id(-1 - r)
        if len(n):
            xa = np.clip(np.floor(n[:, 0] / ppq * self.sx).astype(np.int64) - cx, 0, cw - 1)
            xb = np.clip(np.maximum(xa, np.ceil(n[:, 1] / ppq * self.sx).astype(np.int64) - 1 - cx), 0, cw - 1)
            k = n[:, 2] - k_lo
            slots = self._all[row + self.lead]
            order = []  # (each colour once, in the order of the first track using it: later tracks on top)
            for s in self._all[np.unique(row) + self.lead].tolist():
                c = colour_id(int(s))
                if c not in order:
                    order.append(c)
            # (each track's colour number once, then looked up for every note at once: a lookup per note was most
            # of a redraw at 2 M notes, hunt 2026-10-26)
            uniq, back = np.unique(slots, return_inverse=True)
            cid = np.array([colour_id(int(s)) for s in uniq.tolist()], np.int64)[back.ravel()]
            for c in order:  # (+1 where a line starts, -1 just past its end, added up: covered where > 0)
                on = cid == c
                cover = np.bincount(k[on] * (cw + 1) + xa[on], minlength=span * (cw + 1)).astype(np.int32)
                cover -= np.bincount(k[on] * (cw + 1) + xb[on] + 1, minlength=span * (cw + 1)).astype(np.int32)
                top[np.cumsum(cover.reshape(span, cw + 1), axis=1)[:, :cw] > 0] = c
        # keys -> pixel rows (top key first), like paint_notes': several keys a pixel = the one painted last
        pad = round(PAD * self.scale)
        inner = max(1, h - 2 * pad)
        top = top[::-1]
        if span > inner:
            used = inner
            pix = np.maximum.reduceat(top, np.arange(used) * span // used, axis=0)
        else:
            thick = inner // span
            used = span * thick
            pix = np.repeat(top, thick, axis=0)
        y0 = pad + (inner - used) // 2
        hit = pix >= 0
        img[y0:y0 + used][hit] = np.array([hex_rgb(c) for c in colours], np.uint8)[pix[hit]]
        return img

    def draw_conductor(self, w):
        """The Conductor row: a line at each tempo change, as tall as its BPM (the song's own slowest .. fastest,
        user 2026-10-23: Domino's 1-512 BPM wouldn't fit a MIDI file's range) and a short line at each time / key
        signature."""
        s, y0 = self.scale, self.conductor_y()
        y1 = y0 + self.row_h
        x0 = self.name_w
        sh = self._sheet
        if sh is not None and self.row_h > 4:  # (as wide as the rows' picture, moved with it)
            h, cx, cw = self.row_h - 1, sh["x"], sh["w"]
            lo_hi = self.heard()[2:]
            key = (self._heard[0], lo_hi, self.sx, h, cx, cw)
            got = self._tempo_sheet
            if got is None or got[0] != key:
                inner = self.row_h - 2 * round(PAD * s) - 1
                tall = self.bpm_heights(np.arange(cw), inner, cx)
                img = np.empty((h, cw, 3), np.uint8)
                img[:] = hex_rgb(look.TRACK_ROW)
                ys = np.arange(h)[:, None]
                bottom = h - round(PAD * s)
                img[(ys >= bottom - tall[None, :]) & (ys < bottom)] = hex_rgb(look.TRACK_TEMPO)
                photo = got[1] if got is not None and got[1].size == (cw, h) else Photo(self, cw, h)
                photo.put(img)
                got = self._tempo_sheet = (key, photo)
            self.create_image(x0 - (self.left_px() - cx), y0, image=got[1].photo, anchor="nw")
        tempo = self.app.tempo
        beats = {sg[0] for sg in sigs_of(tempo)[1:]} | {k[0] for k in tempo.get("keysigs") or []}
        for b in beats:
            x = round(self.t2x(b))
            if self.name_w <= x <= w:
                self.create_line(x, y0 + 2 * s, x, y0 + 7 * s, fill=look.TRACK_CONDUCTOR, width=max(1, round(s)))
        self.create_line(0, y1, w, y1, fill=look.ROLL_EDGE)

    def bpm_tip(self, x):
        """The Conductor row's tip at x: the BPM playing there, the song's slowest and fastest."""
        ticks, bpm, lo, hi = self.heard()
        tick = max(0.0, self.x2t(x)) * self.app.ppq
        now = bpm[max(0, int(np.searchsorted(ticks, tick, "right")) - 1)]
        return tr("tracks.bpm_tip", bpm=bpm_text(now), lo=bpm_text(lo), hi=bpm_text(hi))

    def draw_names(self, h):
        """The names' column: Conductor, then each track's name (cut with "…" when too long: the tip has it whole)
        and channel."""
        s, x1 = self.scale, self.name_w
        y0 = self.ruler_h
        font = look.font(9)
        measure = self.measurer(font)
        self.create_rectangle(0, y0, x1, h, fill=look.TRACK_NAME_BG, outline="")
        top = self.area()[1]
        first = self.top // self.row_h
        slots = self._all
        for row in range(first, len(slots)):
            ya = top + row * self.row_h - self.top
            if ya > h:
                break
            yb = ya + self.row_h
            slot = int(slots[row])
            picked = slot == self.current
            chosen = not picked and slot in self.selected  # (selected along with it: a fainter blue, like Domino)
            if picked or chosen:
                self.create_rectangle(0, max(ya, top), x1, yb, fill=look.TRACK_PICK if picked else look.TRACK_SELECT,
                                      outline="")
            elif (self.own_track(slot) or {}).get("auto"):  # (added by Spiderweb for a shape's colours, user)
                self.create_rectangle(0, max(ya, top), x1, yb, fill=look.TRACK_ADDED, outline="")
            if self.row_h >= 12 * s:
                colour = (look.TRACK_PICK_TEXT if picked else look.TRACK_SELECT_TEXT if chosen else look.TRACK_TEXT)
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
        ya = self.conductor_y()
        picked = self.current is None  # (the All tracks row: highlighted like a track)
        if picked:
            self.create_rectangle(0, y0, x1, ya - 1, fill=look.TRACK_PICK, outline="")
        self.create_text(18 * s, (y0 + ya) / 2, text=tr("tracks.all"), anchor="w",
                         fill=look.TRACK_PICK_TEXT if picked else look.TRACK_TEXT, font=look.font(9, "bold"))
        self.create_line(0, ya - 1, x1, ya - 1, fill=look.ROLL_EDGE)
        self.create_text(18 * s, ya + self.row_h / 2, text=tr("tracks.conductor"), anchor="w", fill=look.TRACK_TEXT,
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

    def show_playhead(self, start=False):
        """The play line moved: while playing, the page turns near the right edge (like the piano roll) or back to
        a line left of the view, never while the user scrolls / zooms (PlayFollow, user 2026-10-23: it fought the
        mouse; once they stop it snaps back to the line, like Domino)."""
        if self.sx is None or not self.winfo_ismapped():
            return
        x = self.t2x(self.app.playhead)
        free = self.play_follow.free((self.view_t, self.sx), start)
        if self.app.player.running and free and (x > self.winfo_width() - 6 * self.scale or x < self.name_w):
            self.view_t = self.app.playhead
            self.clamp()
            self.play_follow.turned((self.view_t, self.sx))
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


def row_mask(lm, r, ax, bx, W):
    """A loaded track's notes exactly: its keys klo..khi x W pixels, True where a note sounds (False: unreadable)."""
    t = lm.tracks[r]
    try:
        tab, _ = RL.exact_tab(lm, ax, bx, W, t.klo, t.khi - t.klo + 1, tracks=(r,))
    except Exception:  # (a file that changed / went: the small picture stays)
        RL.write_log_now()
        return False
    return False if tab is None else tab >= 0


def small_mask(lm, t, cx, aw, sx):
    """The same from the loading pass's small picture (keys klo.. x up to PIC_COLS columns), or None."""
    if not t.pic_cols:
        return None
    tpp = lm.ppq / sx  # (ticks a pixel)
    ta = (np.arange(aw) + cx) * tpp
    step = lm.G * t.pic_pf
    c0 = np.floor((ta - t.pic_glo * lm.G) / step).astype(np.int64)
    c1 = np.maximum(np.ceil((ta + tpp - t.pic_glo * lm.G) / step).astype(np.int64), c0 + 1)
    on = np.flatnonzero((c1 > 0) & (c0 < t.pic_cols))
    if not len(on):
        return None
    bits = np.unpackbits(t.pic, axis=1, count=t.pic_cols).astype(bool)
    a = np.clip(c0[on], 0, t.pic_cols - 1)
    got = (np.logical_or.reduceat(bits[:, :int(np.clip(c1[on[-1]], 1, t.pic_cols))], a, axis=1)
           if tpp >= step else bits[:, a])
    out = np.zeros((t.khi - t.klo + 1, aw), bool)
    out[t.pic_klo - t.klo:t.pic_klo - t.klo + len(got), on] = got
    return out


class RowJob:
    """A loaded MIDI's rows' blocks [(row, block)] read exactly on a background thread, in order (stop = give up);
    shown = the ones on screen blocky."""

    def __init__(self, lm, place, todo, ax, shown):
        self.lm, self.place, self.todo, self.ax, self.shown = lm, place, todo, ax, shown
        self.stop = self.done = False
        self.out = deque()  # ((row, block), mask) as read (TrackList.take_rows)

    def run(self):
        for r, j in self.todo:
            if self.stop:
                break
            self.out.append(((r, j), row_mask(self.lm, r, self.ax, -float(j * TILE), TILE)))
        self.done = True


def shape_ids(shapes, owners):
    """id() of the shapes numbered in owners (the shapes themselves: their numbers shift on deleting)."""
    return {id(shapes[i]) for i in np.unique(owners).tolist() if 0 <= i < len(shapes)}


def moved_to(shapes, owners, slots, ids, slot):
    """The track (slot) now holding most notes of the shapes with these ids (shape_ids): slot itself while it holds
    any of them (user 2026-10-26: a 15-colour shape's highlighted track holding fewer than its others made the
    highlight jump at every edit), None when none of them has notes. owners / slots: each note's shape / track."""
    known = [i for i, sh in enumerate(shapes) if id(sh) in ids]
    on = slots[np.isin(owners, known)] if known else slots[:0]
    if not len(on):
        return None
    if (on == slot).any():
        return slot
    where, counts = np.unique(on, return_counts=True)
    return int(where[counts.argmax()])


def build_onion(app, parent):
    """The main toolbar's Multiple tracks toggle + its ▾ list (user 2026-10-23, like Domino's button: on = the
    picked tracks show faintly behind the highlighted one, off = that track alone). Greyed while no track is
    highlighted (sync_onion)."""
    b = ttk.Checkbutton(parent, text=tr("tracks.onion"), variable=app.onion, style="Toolbutton")
    Tooltip(b, tr("tracks.onion_tip"))
    from window.tool_picker import arrow_picture  # (the drawing tools' drawn arrow: the "▾" letter sat left / low)
    pics = arrow_picture(app.scale), arrow_picture(app.scale, look.ICON_OFF)
    more = ttk.Menubutton(parent, image=(pics[0], "disabled", pics[1]), style="Toolbutton")
    more.pics = pics  # (kept, or Tk forgets the pictures)
    menu = tk.Menu(more, tearoff=False)
    tl = lambda: app.track_list  # noqa: E731  (made after the toolbar)
    # (All tracks first: the list's All tracks row, which does the same, is at its top too; user 2026-10-26)
    menu.add_command(label=tr("tracks.onion_all"), command=lambda: tl().show_all())
    menu.add_separator()
    menu.add_command(label=tr("tracks.onion_previous"), command=lambda: tl().behind_near(-1))
    menu.add_command(label=tr("tracks.onion_next"), command=lambda: tl().behind_near(1))
    menu.add_command(label=tr("tracks.onion_pick"), command=lambda: tl().open_picker())
    menu.add_command(label=tr("tracks.onion_alike"), command=lambda: tl().behind_alike())
    more.config(menu=menu)
    Tooltip(more, tr("tracks.onion_more_tip"))
    return b, more


def sync_onion(app):
    on = getattr(app, "track_list", None) is not None and app.track_list.current is not None
    for w in getattr(app, "onion_parts", ()):
        w.state(["!disabled"] if on else ["disabled"])


def build_strip(app, parent, tl):
    """The strip above the track list: Show multiple tracks… + Faint [n] % (its snap is the toolbar's, user
    2026-10-23: SnapPicker.use)."""
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
    return strip


class BehindWindow(tk.Toplevel):
    """Show multiple tracks: which tracks the piano roll shows faintly behind the current one. Not modal: changes
    show at once."""

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
        self._drag = None
        self.list.bind("<ButtonPress-1>", self.press)
        self.list.bind("<B1-Motion>", self.drag)
        self.list.bind("<ButtonRelease-1>", lambda e: (setattr(self, "_drag", None), "break")[1])
        self.list.bind("<Double-Button-1>", self.press)  # (a quick second click toggles again, not a double-click)
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

    def press(self, e):
        """A click toggles its row; dragging on gives every row the mouse crosses the same (user 2026-10-23:
        clicking each one was tedious)."""
        lb = self.list
        lb.focus_set()
        i = lb.nearest(e.y)
        if i < 0 or i >= len(self.slots):
            return "break"
        on = not lb.selection_includes(i)
        (lb.selection_set if on else lb.selection_clear)(i)
        self._drag = (on, i)
        self.picked()
        return "break"

    def drag(self, e):
        if self._drag is None:
            return "break"
        on, last = self._drag
        lb = self.list
        if e.y < 0:  # (past the top / bottom: the list scrolls along)
            lb.yview_scroll(-1, "units")
        elif e.y > lb.winfo_height():
            lb.yview_scroll(1, "units")
        i = lb.nearest(e.y)
        a, b = sorted((last, i))
        for k in range(a, b + 1):
            (lb.selection_set if on else lb.selection_clear)(k)
        self._drag = (on, i)
        self.picked()
        return "break"

    def picked(self):
        self.tl.set_behind({self.slots[i] for i in self.list.curselection() if i < len(self.slots)})

    def set_all(self, on):
        self.tl.set_behind(set(self.slots) if on else set())

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
