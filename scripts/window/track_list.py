"""The track list (user 2026-10-23, like Domino's): Tab swaps the piano roll <-> this view in the same place of the
main window. One row per track the MIDI export / Export to Domino would write (midi_out.track_slots: the same
tracks in the same order, both exports alike), each with a tiny picture of its notes in its channel's colour, a
Conductor row on top. View only: Spiderweb picks the tracks. Zoomed out, the ruler and its lines count several
bars per column (meter.bar_lines), like Domino's.

Picture: the rows' stripes, bar lines and notes painted into one NumPy picture (speed.Photo) per redraw; names,
ruler and play line are canvas items. Each track's notes fill its row from its own lowest to highest key."""

import contextlib
import json
import math
import threading
from collections import OrderedDict, deque
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import numpy as np

from files.colours import rgb_loose as hex_rgb
from files.lang import tr, trn
from files.mathexpr import calc
from files.midi_out import track_slots
from files.own_tracks import (COUNT_MOST, HAND, ID_MOST, PART, add_tracks, key_part, key_track, part_key, part_keys, among_file, clean_file_order, clean_name, file_order, from_rows, full_plan, into_ids, move_rows, ordered,
                              new_id, plan, port_letter, with_port)
from files.snap import clean_snap, snap_beats
from files.speed import Photo
from notes.engine import BEATS_FAR, OWN0, channels_of, pasted_notes, slot_track_channel, track_ids
from notes.loaded import KEY_SIG, TEMPO, TIME_SIG, tempo_tracks, track_port as file_port
from notes.mask import pinned_slots
from notes.meter import bar_line, bar_lines, key_name, sig_text, sigs_of, where
from notes.tempo import bpm_text, tempo_text
from notes.tempo_file import heard_tempo, kept_drawn
from roll import roll_loaded as RL
from roll.roll_draw import TRACK_FADE
from roll.roll_shared import (BOX_STILL, COLOURS_16, MAX_SX, MIN_SX, SELECT_CURSOR, SLOT_COLORS, PlayFollow,
                              draw_boxes as outline_boxes, grab_while_panning, slot_colours)
from roll.tiles import see_through
from window import look
from window.notes_wait import stale_ok
from window.track_windows import AddTracksBox, PropertiesBox
from window.widgets import Scrub, Tooltip, bad, good, remember_place

NAME_W = 150    # the names' column (x scale)
RULER_H = 28    # the ruler: signatures on top, bar numbers under them
ROW_H = 26      # a row's height at first; zoomed up / down between ROW_LEAST and ROW_MOST
ROW_LEAST, ROW_MOST = 8, 160
ALL_H = 40      # the All tracks row's height at least (taller than a track, user 2026-10-26: not read as one more)
ALL_ROW, CONDUCTOR_ROW = -2, -1  # row_at's answers for the two rows on top
PAD = 3         # free pixels above / below a row's notes
WHEEL_ROWS = 4  # tracks a wheel notch scrolls (Domino's)
COLUMN = 28     # bar numbers closer than this many pixels: several bars per column (2, 4, 8...)
TIP_SHAPES = 8  # shapes named in a row's tip at most
FAINT = 25      # % the tracks shown behind the current one are drawn at, at first (0 = hidden)
ROWS_INLINE_S = 0.12  # a loaded MIDI's rows read exactly in the redraw itself for this long at most (the picture is
# made again only after zooming or scrolling far: a slower one then beats rows changing look)
TILE = 512  # px: a loaded MIDI's rows are read in blocks this wide along the song, kept while the zoom stays, and
# the next ones read ahead in the background (user 2026-10-24, Shinki: each new picture read every row again,
# ~100 ms stops while scrolling)
TILES_BYTES = 128 << 20  # blocks kept at most (the oldest used go first)
ALL_BLOCKS = 96  # the All tracks row's blocks of a loaded MIDI kept at most (keys x SQUARE each, the oldest used go)
SQUARE = 256  # px: the rows, All tracks and Conductor rows are painted in squares this wide (paint, tiled)
INDENT = 12  # px (x scale): a folder's sub-rows indented by this in the names (and its name after its ▾)
FOLD_W = 14  # px (x scale): a press this near the names' left edge on a folder = opened / shut
OPEN_MOST = 65534  # open folders read back from a saved view at most (a MIDI file's most tracks)
# redraw's parts, bottom to top (TrackList.layer's tags; each name row is its own part in "names")
# (never "all" / "current": Tk's own tags, "all" = every item: deleting it blanked the whole list)
# (boxes over the Conductor row: boxes on it, user 2026-11-05; they never reach above it)
LAYER_ORDER = ("pic", "ruler", "allrow", "cond", "boxes", "names_bg", "names", "names_top", "move", "playhead")
SQUARES_KEPT = 96 # squares of each picture kept at least (twice the ones in use when more)
MENU_CLICK_S = 0.25  # a press this soon after the right-click menu closed = the click that closed it (Windows)
MOVE_PX = 5  # a press on a track's name moved this far (x scale) = dragging the tracks to another place
EDGE_MS = 40  # how often the list scrolls while tracks are dragged past its top / bottom
HOVER_MS = 500  # a dragged channel / track rested this long on a row's middle = lit: dropped INTO that track (user
#                 2026-10-28: passing over never makes a mixed track by accident; 0.3 s was too short)
INTO_UP = 3  # px (x scale): the line INTO an open folder sits this much higher than the line between tracks
#              (check-in C: at a folder's end the two must look apart)
FILL_TILE = 256  # px: a Select box's see-through fill is put on in squares this big (one small picture per size)


MARK_NOTES = 32  # notes a mute remembers its music by across an undo / redo (TrackList.mute_marks)


def note_keys(notes):
    """One number per note (its shape, start, key) to find it again after the notes are made anew."""
    return notes[:, 5] * (1 << 44) + (notes[:, 2] & 0xFFF) * (1 << 32) + (notes[:, 0] & 0xFFFFFFFF)


def lone_keys(notes, keys, at):
    """Which of the notes at (indexes) have a note_keys number no other note has: a shape laid on two tracks (a
    layered paste) has the same note on both, and following such a note moved a mute onto the other track too
    (speed hunt 2026-10-29, undo muted a track). Counted among the notes of those notes' shapes only."""
    if not len(at):
        return np.zeros(0, bool)
    mine = keys[np.isin(notes[:, 5], np.unique(notes[at, 5]))]
    srt = np.sort(mine)
    want = keys[at]
    return np.searchsorted(srt, want, "right") - np.searchsorted(srt, want, "left") == 1


class TrackList(tk.Canvas):
    def __init__(self, parent, app, scale):
        super().__init__(parent, bg=look.TRACK_ROW, highlightthickness=0, takefocus=True)
        self.app, self.scale = app, scale
        self.name_w, self.ruler_h = round(NAME_W * scale), round(RULER_H * scale)
        self.row_h = round(ROW_H * scale)
        self.view_t, self.sx = 0.0, None  # beat at the picture's left edge, pixels per beat (None: fitted when shown)
        self.top = 0       # pixels the track rows are scrolled up
        self.current = None  # the highlighted track's slot (None: the Conductor row / nothing)
        self.current_ch = None  # ... only this channel of it (a folder's sub-row clicked), None = the whole track
        # the Conductor row highlighted (user 11-05, like Domino): the piano roll keeps showing current's notes, Tab
        # goes to the Conductor pane; any track / All tracks highlighted again turns it off (set_conductor)
        self.conductor_on = False
        # folders (PLAN #11 B, user 2026-10-28): tracks holding notes on 2+ channels whose sub-rows show (track ids);
        # each row's channels (row_chans) and the lines shown (lines) kept while what they come from stays
        self.open = set()
        self._chans = self._lines = None
        self.owners = set()  # id() of the shapes with notes on it (follow: it keeps showing them when channels change)
        # slots shown faintly behind each track on the piano roll ("Show multiple tracks"; per track, user
        # 2026-10-26: {track: its set}; behind = the highlighted one's. One shared set = other editors' way)
        self._behind = {}
        self.onion = app.onion  # ... while this is on (the main toolbar's toggle; off = one track)
        self.onion.trace_add("write", lambda *_: self.shown_changed())
        self.selected = set()  # tracks Ctrl+clicked along with the highlighted one (like Domino's; the right-click
        # menu works on them all), never the highlighted one itself
        self.anchor = None  # where a Shift+click's range starts: (the last plain / Ctrl+clicked track, the
        # highlighted one then)
        # folders' sub-rows Ctrl / Shift clicked along with the highlighted one (current + current_ch): {(track,
        # channel)}, dragged together (PLAN #11 check-in C); a new highlight drops them (current's setter)
        self.sel_ch = set()
        self._sub_anchor = None  # (track, channel) a Shift+click's sub-row range starts from
        self._sweep = None  # a drag from a speaker over the rows: (muted or not, the last row it reached)
        # a press on a track's name, dragged = the tracks moved (group C): {"y", "on" (dragging), "gap" (the row the
        # line is above), "keep" (pressed on one of several selected: a click without a drag picks it alone),
        # "s", "job" (scrolling at an edge)}
        self._move = None
        # the Select boxes kept in the picture (group E, like Domino's): [{"t0", "t1" (beats), "rows": (first, last)
        # row, or None = a ruler band over every row}]; the one being dragged: {"x", "y", "t" (pressed beat), "row",
        # "band", "ctrl", "shift" (at the press), "pick" (Ctrl / Shift click on a row: tracks picked if no drag),
        # "on" (dragged far enough), "box" (as it'd be kept now)}
        self.boxes, self._box = [], None
        self._fills = OrderedDict()  # (w, h) -> the boxes' see-through fill picture of that size
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
        self._all = self._slots  # every row's track: a loaded MIDI's (-1 - its number), yours (HAND + id), slots
        self.lead = 0  # rows before Spiderweb's own part (a loaded MIDI's tracks and yours among them)
        self._file_row = np.zeros(0, np.int64)  # a loaded MIDI's track number -> its row
        self._part_row = {}  # a loaded track's part (its file_order entry, PLAN #11 D) -> its row
        self._hand = {}  # id -> the own track (added by hand, files/own_tracks.py) of each such row
        self.menu_shut = 0.0  # when the right-click menu last closed (perf_counter)
        self._row_of = np.zeros(0, np.int64)  # slot -> row (-1: none)
        self._ids = []  # id() of the shapes when the tracks were worked out (their notes' owner numbers)
        self._sheet = None  # paint's squares: {"key", "tiles" {(column, row): Photo}} (tiled; None = paint again)
        self._tempo_sheet = None  # the same of the Conductor row's
        self._all_sheet = None  # the same of the All tracks row's
        self._all_place, self._all_blocks = None, OrderedDict()  # loaded_tops' blocks and what they were made for
        self._pics = None  # (LoadedMidi, small_pics' arrays)
        # a loaded MIDI's exact rows: the zoom they're for, {(row, block): keys x TILE mask, False = unreadable}
        # (oldest used first), their bytes, the background reading
        self._row_place, self._tiles, self._tile_bytes, self._row_job = None, OrderedDict(), 0, None
        self._heard = None  # (key, heard()'s answer) for the tempo line it was worked out for
        self._tip_memo = (None, "")  # (row, notes, shape count) -> row_tip's text
        self._fonts = {}  # font -> (tk font, its measuring function: measurer)
        self._pan = None
        self.play_follow = PlayFollow(app)
        self._job = None
        # redraw's parts (layer): name -> what was drawn last time; while one is drawn, its items are written down
        # here instead of sent (_rec), the names drawn this redraw (_drawn)
        self._layers, self._rec, self._drawn, self._moved = {}, None, set(), False
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

    @property
    def current(self):
        return self._current

    @current.setter
    def current(self, s):  # (another highlight: the whole track; a folder's sub-row click sets current_ch after)
        self._current, self.current_ch, self.sel_ch = s, None, set()

    def picked_chans(self):
        """The folders' sub-rows picked: the highlighted one (a sub-row clicked) + the ones selected with it."""
        out = set(self.sel_ch)
        if self.current is not None and self.current_ch is not None:
            out.add((self.current, self.current_ch))
        return out

    def shown_pairs(self):
        """The Ch rows the piano roll shows when Ch rows are picked (user 2026-10-28, E3: every picked one while its
        folder is open): {(track id, channel)}, None = no Ch row highlighted."""
        if self.shown_ch() is None:
            return None
        return {p for p in self.picked_chans() if self.line_of(*p) is not None}

    def sub_click(self, row, c, ctrl, shift):
        """Ctrl / Shift click on a folder's sub-row, like select_click for tracks (check-in C 2026-10-28: several
        channels picked and dragged together): Ctrl = that one in / out, Shift = the sub-rows from the last one
        clicked to it (any folders between), Ctrl+Shift = added. The highlight stays on a picked one."""
        pair = (int(self.tracks()[row]), c)
        if self.current_ch is None:  # (tracks picked: tracks OR Ch rows, never both: a new pick, hunt C B)
            self.current, self.selected, self.anchor = pair[0], set(), None
            self.current_ch, self._sub_anchor = c, pair
            return self.shown_changed()
        group = self.picked_chans()
        anchor = self._sub_anchor
        if anchor is None or self.line_of(*anchor) is None:  # (none yet: from the highlighted sub-row)
            anchor = (self.current, self.current_ch) if self.current_ch is not None else None
        if shift and anchor is not None and self.line_of(*anchor) is not None:
            a, b = sorted((self.line_of(*anchor), self.line_of(*pair)))
            rows, chs = self.lines()[:2]
            span = {(int(self._all[rows[k]]), int(chs[k])) for k in range(a, b + 1) if chs[k] >= 0}
            group = group | span if ctrl else span
        else:
            if pair in group and len(group) == 1:
                return
            group ^= {pair}
        if not group:
            return
        now = (self.current, self.current_ch)
        keep = now if now in group else min(group, key=lambda p: self.line_of(*p))
        self.current = keep[0]
        self.current_ch = keep[1]
        self.sel_ch = group - {keep}
        self.selected = set()
        if not shift:
            self._sub_anchor = pair
        self.shown_changed()

    def shown_ch(self):
        """The one channel of the highlighted track the piano roll shows (its sub-row clicked and still listed), None
        = the whole track."""
        if self.current is None or self.current_ch is None or self.line_of(self.current, self.current_ch) is None:
            return None
        return self.current_ch

    # ------------------------------------------------------------ the tracks

    def tracks(self):
        """The slots written as tracks, in order (worked out again only when the notes change)."""
        app = self.app
        n, pad, use10, lm, own = app.rendered, app.pad_tracks(), app.picture_use10, app.loaded, app.own_written()
        waiting, order = app.waiting_rows(), (app.file_order, tuple(app.file_gone), app.least_tracks(), app.file_props)
        made = self._made
        if made is None or made[0] is not n or made[1:] != (pad, use10, id(lm), own, waiting, order):
            slots = track_slots(n, pad, order[2]).astype(np.int64)  # (a new project's empty Track 1 too)
            size = int(slots.max()) + 1 if len(slots) else 0
            # (a loaded MIDI's tracks first (their numbers -1, -2, ...), the tracks added by hand among them and among
            # Spiderweb's: own_tracks.full_plan, the exports' order)
            self._filled = app.filled_slots(n)  # (tracks with notes: own_tracks.counted; Remove overlaps' emptied too)
            files = app.file_rows()
            rows = [-1 - k for k in waiting] + full_plan(files, slots, own, self._filled)
            self._hand = {HAND + t["id"]: t for t in own}
            every = np.array([r if type(r) is int else HAND + r["id"] for r in rows], np.int64)
            row_of = np.full(size, -1, np.int64)
            row_of[slots] = np.flatnonzero((every >= 0) & (every < HAND))
            file_row = np.full(len(lm.tracks) if lm is not None else max(waiting, default=-1) + 1, -1, np.int64)
            plain = (every < 0) & (every > -1 - PART)  # (a part's row: _part_row, PLAN #11 D)
            file_row[-1 - every[plain]] = np.flatnonzero(plain)
            self._part_row = {int(-1 - every[r]): int(r) for r in np.flatnonzero(every <= -1 - PART).tolist()}
            # muted / picked tracks keep their music when the tracks are handed out again (like the highlight:
            # follow; hunt 10-23, user picked A), else they're let go, never moved onto other music
            shapes = self.app.shapes
            if made is not None and (self.muted or self._behind):
                old, ids = made[0], self._ids

                def keep(s):
                    if s < 0 or s >= HAND:  # (a loaded MIDI's / an own track: never handed out again)
                        return s
                    had = {ids[i] for i in np.unique(old[old[:, 4] == s, 5]).tolist() if i < len(ids)}
                    if not any(id(sh) in had for sh in shapes):  # (an empty track, or shapes made anew by undo:
                        return s                                   # kept on its number, like the highlight)
                    return moved_to(shapes, n[:, 5], track_ids(n[:, 4]), had, s)
                self.map_muted(keep)
                self.map_behind(keep)
            self._ids = [id(sh) for sh in shapes]
            self._slots, self._row_of, self._file_row, self._rows_of_notes = slots, row_of, file_row, None
            # (rows before Spiderweb's own part: a loaded MIDI's tracks and yours among them)
            self.lead = len(rows) - len(plan(slots, among_file(own, len(files))[1] if files else own, self._filled))
            self._all = every
            self._made = (n, pad, use10, id(lm), own, waiting, order)

            def there(s):
                if s >= HAND:
                    return s in self._hand
                if s <= -1 - PART:
                    return -1 - s in self._part_row
                return -s - 1 < len(file_row) and file_row[-s - 1] >= 0 if s < 0 else s < size and row_of[s] >= 0
            if self.current is not None and not there(self.current):
                self.current = None  # (its track is gone)
            self.map_behind(lambda s: s if there(s) else None)
            self.map_muted(lambda s: s if there(s) else None)
            self.selected = {s for s in self.selected if there(s)}
            last = len(every) - 1  # (rows gone: the boxes kept on the rows left)
            self.boxes = [b if b["rows"] is None else dict(b, rows=(b["rows"][0], min(b["rows"][1], last)))
                          for b in self.boxes if b["rows"] is None or b["rows"][0] <= last]
        return self._all

    def row_of_id(self, s):
        """The row of track s (a slot, or a loaded MIDI's track as -1 - its number); None = not listed."""
        self.tracks()
        if s is None:
            return None
        if s <= -1 - PART:  # (a loaded track's part, PLAN #11 D)
            return self._part_row.get(-1 - s)
        if s < 0:  # (-1: not listed, e.g. removed)
            return int(self._file_row[-s - 1]) if -s - 1 < len(self._file_row) and self._file_row[-s - 1] >= 0 \
                else None
        if s >= HAND:
            at = np.flatnonzero(self._all == s)
            return int(at[0]) if len(at) else None
        return int(self._row_of[s]) if s < len(self._row_of) and self._row_of[s] >= 0 else None

    def row_chans(self):
        """Each row's channels with notes, as tuples: a loaded MIDI's track from the loading pass's counts
        (Track Properties' channel: that one) + Spiderweb's notes drawn into it, Spiderweb's tracks from their notes.
        2 or more = a mixed track (a folder). Sorted, but a track of yours in its "order" (sub-rows dragged, PLAN
        #11: own_tracks.ordered)."""
        self.tracks()
        lm, use10 = self.app.loaded, self.app.picture_use10
        key = (id(self._made[0]), id(self._all), id(lm), lm.hide_ver if lm is not None else None, use10)
        got = self._chans
        if got is not None and got[0] == key:
            return got[1]
        sets = [set() for _ in range(len(self._all))]
        n = self._made[0]
        if len(n):  # (a note's row and channel go by its slot alone: worked out once per slot, not per note)
            slots = np.unique(n[:, 4])
            rows = self.rows_of(slots)
            ok = rows >= 0
            for p in np.unique(rows[ok] * 16 + channels_of(slots[ok], use10)).tolist():
                sets[p >> 4].add(p & 15)
        if lm is not None:
            moved = lm.rechannel
            for row in np.flatnonzero(self._all < 0).tolist():
                k, only = self.file_part(int(self._all[row]))  # (a part / a track with parts: their channels)
                if k >= len(lm.tracks) or not lm.tracks[k].notes:
                    continue
                if moved is not None and k < len(moved) and moved[k] >= 0:
                    got = {int(moved[k])}
                else:
                    got = set(np.flatnonzero(lm.tracks[k].chn).tolist())
                sets[row] |= got if only is None else got & only
        # (a track of yours: its order's channels listed even without notes, empty "Ch n" rows: check-in C)
        orders = [self._hand.get(int(s), {}).get("order") or () if s >= 0 else self.loaded_order(int(s))
                  for s in self._all.tolist()]
        # (a loaded row's order: only the channels it holds, never listed empty)
        out = [tuple(ordered(o, x | set(o) if s >= 0 else x)) for o, x, s in zip(orders, sets, self._all.tolist())]
        self._chans = (key, out)
        return out

    def mixed(self, row):
        """Does the track in row hold notes on 2+ channels?"""
        chans = self.row_chans()
        return 0 <= row < len(chans) and len(chans[row]) > 1

    def lines(self):
        """The lines shown, top down: (each line's row, its channel (-1 = the track's own line), each row's first
        line + the line count at the end, the open sub-rows' keys row * 16 + channel (sorted) and their lines).
        An open folder's sub-rows (one per channel, in channel order) follow its track's line."""
        chans = self.row_chans()
        key = (chans, frozenset(self.open))
        got = self._lines
        if got is None or got[0][0] is not chans or got[0][1] != key[1]:
            rows, chs, first = [], [], []
            for row, s in enumerate(self._all.tolist()):
                first.append(len(rows))
                rows.append(row)
                chs.append(-1)
                if s in self.open and len(chans[row]) > 1:
                    rows += [row] * len(chans[row])
                    chs += list(chans[row])
            rows, chs = np.array(rows, np.int64), np.array(chs, np.int64)
            sub = np.flatnonzero(chs >= 0)
            keys = rows[sub] * 16 + chs[sub]
            by = np.argsort(keys, kind="stable")  # (sorted for searchsorted: a folder's own order isn't, C1)
            got = self._lines = (key, (rows, chs, np.array(first + [len(rows)], np.int64), keys[by], sub[by]))
        return got[1]

    def line_of(self, s, ch=None):
        """The line of track s (ch: its sub-row of that channel, None when that folder is shut); None = not listed."""
        row = self.row_of_id(s)
        if row is None:
            return None
        rows, chs, first, keys, sub = self.lines()
        if ch is None:
            return int(first[row])
        at = int(np.searchsorted(keys, row * 16 + ch))
        return int(sub[at]) if at < len(keys) and keys[at] == row * 16 + ch else None

    def toggle_open(self, s):
        """A folder opened / shut (its ▾); kept with the view (state)."""
        self.open ^= {s}
        if s not in self.open:  # (its Ch rows can't show: unpicked, E3 hunt; picks stay tracks OR Ch rows)
            if self.current == s and self.current_ch is not None:
                self.current = s  # (the track alone: the other picked Ch rows go too)
                self.shown_changed()
            elif any(p[0] == s for p in self.sel_ch):
                self.sel_ch = {p for p in self.sel_ch if p[0] != s}
                self.shown_changed()
        self.request_redraw()
        self.app.schedule_autosave()

    def file_at(self, row):
        """The loaded MIDI's track number in row (a row number), else None."""
        s = int(self._all[row]) if 0 <= row < len(self._all) else 0
        return key_track(-1 - s) if s < 0 else None  # (a part's: its track's, PLAN #11 D)

    def note_rows(self):
        """Each of Spiderweb's notes' row (-1: none), worked out when asked."""
        self.tracks()
        if self._rows_of_notes is None:
            self._rows_of_notes = self.rows_of(self._made[0][:, 4])
        return self._rows_of_notes

    def rows_of(self, slots):
        """The row of notes on these slots (-1: none)."""
        self.tracks()
        mine = self._all
        slots = np.asarray(slots, np.int64)
        tid = track_ids(slots)
        out = np.full(len(slots), -1, np.int64)
        auto = tid < HAND
        ok = auto & (tid >= 0) & (tid < len(self._row_of))
        out[ok] = self._row_of[tid[ok]]
        inside = (tid < 0) & (-1 - tid < len(self._file_row))  # (drawn into a loaded MIDI's track: its row)
        out[inside] = self._file_row[-1 - tid[inside]]
        if self._part_row and inside.any():  # (on a part's channel: the part's row, PLAN #11 D)
            ch = channels_of(slots, self.app.picture_use10)
            for k, row in self._part_row.items():
                _, chs = self.file_part(-1 - k)
                out[inside & (tid == -1 - key_track(k)) & np.isin(ch, list(chs))] = row
        hand_rows = np.flatnonzero(mine >= HAND)
        if not auto.all() and len(hand_rows):  # (a track added by hand: its row)
            o = np.argsort(mine[hand_rows])
            ids = mine[hand_rows][o]
            at = np.searchsorted(ids, tid[~auto])
            found = (at < len(ids)) & (ids[np.minimum(at, len(ids) - 1)] == tid[~auto])
            out[~auto] = np.where(found, hand_rows[o][np.minimum(at, len(ids) - 1)], -1)
        return out

    def forget(self):
        """Another project opened: nothing muted, highlighted or picked behind, back at its start; the zoom stays
        (user 2026-10-23: remembered)."""
        self.current, self._behind, self.muted, self.owners, self.selected = None, {}, set(), set(), set()
        self.boxes, self._box, self.open = [], None, set()
        self.view_t, self.top, self._sheet = 0.0, 0, None
        sync_onion(self.app)
        self.request_redraw()

    def unmuted(self, notes):
        """notes without the muted tracks' / channels' (App.play_apart: playback only)."""
        if self.muted:
            self.tracks()  # (the notes were made again: the mutes follow their music)
        if not self.muted or not len(notes):
            return notes
        tid = track_ids(notes[:, 4])
        whole, pairs = self.mutes_by_track()
        out = np.isin(tid, list(whole))
        if pairs:  # (a folder's sub-row muted: that channel of the track)
            ch = channels_of(notes[:, 4], self.app.picture_use10)
            out |= np.isin(tid * 16 + ch, [s * 16 + c for s, c in pairs])
        return notes[~out]

    def mute_marks(self):
        """Before an undo / redo: each mute with the music it covers (user 2026-10-28, E3 hunt: like Domino, undo never
        mutes or un-mutes, but a mute goes along with its music when a step moves it back): a loaded row = its file
        track's channels, else up to MARK_NOTES of its notes (shape number, start, key); None = nothing to follow."""
        if not self.muted:
            return []
        self.tracks()
        r, rows, chans = self.app.rendered, self.note_rows(), self.row_chans()
        ch = channels_of(r[:, 4], self.app.picture_use10) if len(r) else r[:0, 0]
        keys = note_keys(r) if len(r) else np.zeros(0, np.int64)
        out = []
        for m in self.muted:
            s, c = m if isinstance(m, tuple) else (m, None)
            row = self.row_of_id(s)
            if row is None:
                out.append((m, None))
            elif s < 0 and self.app.loaded is not None:
                out.append((m, ("file", self.file_part(s)[0], chans[row] if c is None else (c,))))
            else:
                pick = rows == row if len(r) else np.zeros(0, bool)
                if c is not None and len(r):
                    pick &= ch == c
                at = np.flatnonzero(pick)
                got = at[:MARK_NOTES * 4]  # (only notes found on this row alone: lone_keys)
                got = got[lone_keys(r, keys, got)][:MARK_NOTES]
                if not len(got) and len(at) > MARK_NOTES * 4:
                    got = at[lone_keys(r, keys, at)][:MARK_NOTES]
                out.append((m, ("notes", keys[got]) if len(got) else None))
        return out

    def remute(self, marks):
        """After an undo / redo: mute_marks' mutes put on wherever their music is now (a track whose every channel
        ends up muted = the whole track); a mute with nothing to follow stays on its track."""
        self.tracks()
        r, rows, chans = self.app.rendered, self.note_rows(), self.row_chans()
        keys = note_keys(r) if len(r) else np.zeros(0, np.int64)
        ch = channels_of(r[:, 4], self.app.picture_use10) if len(r) else r[:0, 0]
        whole, pairs = set(), set()
        for m, key in marks:
            if key is None:
                (pairs.add(m) if isinstance(m, tuple) else whole.add(m))
            elif key[0] == "file":
                pairs |= {(int(s), c) for row, s in enumerate(self._all.tolist()) if s < 0
                          and self.file_part(s)[0] == key[1] for c in key[2] if c in chans[row]}
            else:
                at = np.flatnonzero(np.isin(keys, key[1]) & (rows >= 0))
                at = at[lone_keys(r, keys, at)]  # (a note now on two tracks shows neither: lone_keys)
                pairs |= {(int(self._all[rows[i]]), int(ch[i])) for i in at.tolist()}
        for s in {s for s, _ in pairs}:  # (every channel of a track muted: the track's own mute)
            got = {c for x, c in pairs if x == s}
            row = self.row_of_id(s)
            if row is not None and chans[row] and got >= set(chans[row]):
                whole.add(s)
        self.muted = whole | {p for p in pairs if p[0] not in whole}
        self.request_redraw()

    def mutes_by_track(self):
        """(whole tracks muted, {(track id, channel)}) with a loaded track's PARTS (PLAN #11 D) as the channels of its
        track (-1 - its number) they hold."""
        whole, pairs = set(), set()
        for m in self.muted:
            s, c = m if isinstance(m, tuple) else (m, None)
            n, chs = self.file_part(s) if s < 0 else (None, None)
            if chs is None:
                (whole.add(s) if c is None else pairs.add((s, c))) if n is None else \
                    (whole.add(-1 - n) if c is None else pairs.add((-1 - n, c)))
            else:
                pairs |= {(-1 - n, x) for x in (chs if c is None else {c} & chs)}
        return whole, pairs

    def loaded_mutes(self):
        """LoadedPlayer's muted(): the loaded MIDI's track numbers muted, (track number, channel) for one channel."""
        whole, pairs = self.mutes_by_track()
        return [-1 - s for s in whole if s < 0] + [(-1 - s, c) for s, c in pairs if s < 0]

    def loaded_order(self, s):
        """A loaded row's Ch rows' order as dragged (a part: its channels' list; the track's own row: its "order")."""
        n, j = self.file_part(s)[0], key_part(-1 - s)
        p = self.app.file_props.get(n) or {}
        if j >= 0:
            return next((q["chs"] for q in p.get("parts", ()) if q["id"] == j), ())
        return p.get("order") or ()

    def shown_file(self):
        """(track number, its channels shown: a sorted list, [] = every one) of the highlighted loaded row: a part,
        a track with parts, one Ch row (PLAN #11 D hunt: Extract / Delete take only what shows); None = none."""
        if self.current is None or self.current >= 0:
            return None
        n, chs = self.file_part(self.current)
        ch = self.shown_ch()
        if ch is not None:
            chs = frozenset({ch})
        return n, sorted(chs) if chs is not None and len(chs) < 16 else []

    def shown_files(self):
        """{track number: its channels shown (sorted, [] = every one)} of the loaded tracks the piano roll shows in
        full: every picked Ch row's (E3 hunt), else the highlighted row's (shown_file); {} = none."""
        pairs = self.shown_pairs()
        if pairs:
            out = {}
            for s, c in pairs:
                if s < 0:
                    out.setdefault(self.file_part(s)[0], set()).add(c)
            return {n: sorted(cs) for n, cs in out.items()}
        got = self.shown_file()
        return {got[0]: got[1]} if got is not None else {}

    def rows_chans(self, rows):
        """{track number: its channels in rows (a sorted list, [] = every one)} of the loaded tracks there."""
        out = {}
        for k in rows:
            s = int(self._all[k])
            if s >= 0:
                continue
            n, chs = self.file_part(s)
            had = out.get(n, set())
            out[n] = None if had is None or chs is None else had | chs
        return {n: sorted(c) if c is not None and len(c) < 16 else [] for n, c in out.items()}

    def row_only(self, row):
        """The channels a loaded row's picture reads: None = its whole track, a part / a track with parts (PLAN #11
        D) its channels (one = that channel, so it shares a sub-row's read blocks)."""
        _, chs = self.file_part(int(self._all[row]))
        if chs is None:
            return None
        return next(iter(chs)) if len(chs) == 1 else chs

    def file_part(self, s):
        """(track number in the file, the channels its row shows: None = all) of loaded track list id s: a PART
        (PLAN #11 D) its channels, a track with parts the channels left to it."""
        k = -1 - s
        n, j = key_track(k), key_part(k)
        parts = (self.app.file_props.get(n) or {}).get("parts") or ()
        if j >= 0:
            return n, frozenset(next((p["chs"] for p in parts if p["id"] == j), ()))
        if parts:
            return n, frozenset(range(16)) - {c for p in parts for c in p["chs"]}
        return n, None

    def muted_tracks(self):
        """The whole tracks muted (track ids)."""
        return {m for m in self.muted if not isinstance(m, tuple)}

    def muted_chans(self):
        """The single channels muted: {(track id, channel)} (a folder's sub-row's speaker)."""
        return {m for m in self.muted if isinstance(m, tuple)}

    def map_muted(self, fn):
        """The mutes' tracks through fn (None = let go), a channel's mute keeping its channel."""
        out = set()
        for m in self.muted:
            s = fn(m[0] if isinstance(m, tuple) else m)
            if s is not None:
                out.add((s, m[1]) if isinstance(m, tuple) else s)
        self.muted = out

    def toggle_mute(self, slot):
        """A speaker clicked: slot = a track id, or (track id, channel) for a folder's sub-row. A folder's own
        speaker is its channels' MASTER (user 2026-10-28): anything heard = all muted, all muted = all heard; a
        channel heard again in a muted folder = the others stay muted."""
        if isinstance(slot, tuple):
            s, c = slot
            chans = self.chans_of(s)
            if s in self.muted:  # (the whole track muted: every other channel stays muted on its own)
                self.muted = (self.muted - {s}) | {(s, x) for x in chans if x != c}
            else:
                self.muted ^= {slot}
                if chans and all((s, x) in self.muted for x in chans):  # (every channel muted = the track)
                    self.muted = {m for m in self.muted if not (isinstance(m, tuple) and m[0] == s)} | {s}
        else:
            was = self.mute_state(slot)
            self.muted = {m for m in self.muted if not (isinstance(m, tuple) and m[0] == slot)} - {slot}
            if was != 2:
                self.muted.add(slot)
        self.request_redraw()
        self.app.play_changed()

    def chans_of(self, s):
        """Track s's channels with notes (row_chans)."""
        row = self.row_of_id(s)
        return () if row is None else self.row_chans()[row]

    def mute_state(self, s):
        """Track s's speaker: 0 = heard, 1 = some of its channels muted (a folder), 2 = muted."""
        if s in self.muted:
            return 2
        chans = self.chans_of(s)
        n = sum((s, x) in self.muted for x in chans)
        return 0 if n == 0 else 2 if n == len(chans) else 1

    # ------------------------------------------------------------ settings (autosaved, no undo step)

    def state(self):
        """Kept in the autosave's window settings: Faint, the snap and the view (user 2026-10-23: zoom remembered,
        like Domino's; never fitted again once there is one)."""
        out = {"faint": self.faint, "snap": self.snap.get(), "row_h": round(self.row_h / self.scale, 3)}
        if self.sx is not None:
            out.update(sx=self.sx, t=self.view_t, top=round(self.top / self.scale))
        if self.open:  # (folders opened: track ids, a loaded MIDI's -1 - n, yours HAND + id)
            out["open"] = sorted(self.open)
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
        if isinstance(data.get("open"), list):
            self.open = {v for v in data["open"][:OPEN_MOST]  # (parts' ids too, E3 hunt)
                         if type(v) is int and -HAND < v < HAND + ID_MOST}

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

    @property
    def behind(self):
        """The tracks shown behind the highlighted one (each track keeps its own)."""
        return self._behind.get(self.current, set())

    @behind.setter
    def behind(self, slots):
        slots = set(slots)
        if slots:
            self._behind[self.current] = slots
        else:
            self._behind.pop(self.current, None)

    def map_behind(self, fn):
        """Every track's picks (and the track itself) through fn: a track's new id, None = gone."""
        out = {}
        for k, v in self._behind.items():
            gone = k is not None and fn(k) is None
            k = None if k is None else fn(k)  # (None: picked while no track was highlighted)
            got = {s for s in map(fn, v) if s is not None}
            if not gone and got:
                out.setdefault(k, set()).update(got)
        self._behind = out

    def set_behind(self, slots):
        """The tracks shown behind the highlighted one; picking some switches the main toolbar's toggle on. All
        tracks highlighted: nothing (picks belong to a track)."""
        if self.current is None:
            return
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
        mine = self.shown_mask(drawn, slot)
        self.owners = shape_ids(self.app.shapes, drawn[mine, 5])
        out = [drawn[mine]]
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

    def shown_mask(self, drawn, slot):
        """Which of drawn's notes the highlighted track shows in full: its own, only the channel picked (a folder's
        sub-row clicked, user 2026-10-28). slot: each note's real track."""
        pairs = self.shown_pairs()
        if pairs:  # (Ch rows picked: each of them, also other tracks' ones, E3)
            ch = channels_of(drawn[:, 4], self.app.picture_use10)
            mine = np.zeros(len(drawn), bool)
            for s, c in pairs:
                mine |= (slot == (-1 - self.file_part(s)[0] if s < 0 else s)) & (ch == c)
            return mine
        n, chs = self.file_part(self.current) if self.current < 0 else (None, None)
        if chs is not None:  # (a loaded track's part / a track with parts: its channels of the track, PLAN #11 D)
            return (slot == -1 - n) & np.isin(channels_of(drawn[:, 4], self.app.picture_use10), list(chs))
        return slot == self.current

    def open_picker(self):
        if self.picker:
            self.picker.lift()
            return
        self.picker = BehindWindow(self)

    def name_of(self, row):
        """A loaded MIDI's track: the name it has in the file, else "Track n" (like Spiderweb's own)."""
        self.tracks()
        if self.file_at(row) is not None:
            name = self.app.file_tracks().get(self.file_at(row), {}).get("name")
            if name:
                return name
            # (unnamed: its number in the file, wherever it's dragged; user 2026-10-28: place numbers shifted)
            return tr("tracks.name", n=self.file_at(row) + 1)
        else:
            own = self.own_track(int(self.tracks()[row]))
            if own is not None and own["name"]:
                return own["name"]
        return tr("tracks.name", n=row + 1)

    def mixed_written(self, own_only=False):
        """(names, any order dragged) of the tracks the export writes holding notes on 2+ channels BY YOUR DOING
        (PLAN #11 E1): yours, a loaded track's part / a loaded track with parts or channels you added; a loaded
        track left as the file had it isn't named (its MIDI was like that). Order dragged = a folder's Ch rows not
        in channel order (a MIDI keeps none inside a track). own_only: yours alone (Domino exports: no loaded notes)."""
        lm, names, moved = self.app.loaded, [], False
        for row, (s, chs) in enumerate(zip(self.tracks().tolist(), self.row_chans())):
            if len(chs) < 2 or (own_only and s < HAND):
                continue
            if s < 0 and lm is not None:
                n, only = self.file_part(s)
                t = lm.tracks[n] if n < len(lm.tracks) else None
                re = lm.rechannel
                had = set() if t is None else {int(re[n])} if re is not None and n < len(re) and re[n] >= 0 \
                    else set(np.flatnonzero(t.chn).tolist())
                if only is None and set(chs) <= had:  # (as the file had it: only its order may be yours, E3 hunt)
                    moved |= list(chs) != sorted(chs)
                    continue
            names.append(self.name_of(row))
            moved |= list(chs) != sorted(chs)
        return names, moved

    def mixed_note(self, domino=False):
        """The export message's lines about mixed tracks (mixed_written), "" for none."""
        names, moved = self.mixed_written(own_only=domino)
        shown = ", ".join(names[:10]) + (", …" if len(names) > 10 else "")
        return ((tr("tracks.mixed_dms" if domino else "tracks.mixed_export", names=shown) if names else "")
                + (tr("tracks.mixed_order") if moved and not domino else ""))

    def own_track(self, s):
        """The own track (added by hand) of track id s, or None."""
        if s is None or s < HAND:
            return None
        self.tracks()  # (the tracks as they are now: undo / redo changes them while the list isn't drawn)
        return self._hand.get(s)

    def file_channel(self, n):
        """A loaded MIDI's track n's main channel (None: no notes); its MIDI not loaded: as saved (App.file_tracks)."""
        lm = self.app.loaded
        if lm is not None:
            new = (self.app.file_props.get(n) or {}).get("ch")  # (Track Properties' channel, group E)
            return new if new is not None else lm.tracks[n].channel if n < len(lm.tracks) else None
        return self.app.file_tracks().get(n, {}).get("ch")

    def part_channel(self, s):
        """A loaded row's channel: file_channel; a part / a track with parts (PLAN #11 D): its first one with notes."""
        n, chs = self.file_part(s)
        if chs is None:
            return self.file_channel(n)
        row = self.row_of_id(s)
        got = self.row_chans()[row] if row is not None else ()
        return got[0] if got else min(chs, default=None)

    def port_of(self, slot):
        """A track's port (0 = A): yours as set in Track Properties, a loaded one's (its parts': their track's) as
        the file or Track Properties has it; Spiderweb's: A."""
        if slot < 0:
            return self.app.file_tracks().get(self.file_part(slot)[0], {}).get("port", 0)
        return self._hand[slot].get("port", 0) if slot >= HAND else 0

    def channel_of(self, slot, long=False):
        """"Ch 3", or "B 3" on a port other than A (user 2026-10-30; long: "Port B, channel 3", for tips)."""
        if slot < 0:  # (a loaded MIDI's track: its main channel; none without notes; a part's: its first)
            ch = self.part_channel(slot)
            if ch is None:
                return ""
        elif slot >= HAND:
            ch = self._hand[slot]["ch"]
        else:
            ch = slot_track_channel(slot, self.app.picture_use10)[1]
        port = self.port_of(slot)
        if port:
            return tr("tracks.port_channel_long" if long else "tracks.port_channel", port=port_letter(port), ch=ch + 1)
        return tr("tracks.channel", ch=ch + 1)

    def colour_of(self, slot):
        """The track's notes' colour: its channel's, as on the piano roll (the light look's darker edge colour shows
        better on thin lines; the dark look's fill colour)."""
        if slot < 0 or slot >= HAND:
            ch = self.file_channel(-1 - slot) if slot < 0 else self._hand[slot]["ch"]
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
        return int(self.lines()[2][-1]) * self.row_h

    def row_y(self, row):
        """Pixel (in the rows, from their top) of row's first line; row = the row count: below the last."""
        return int(self.lines()[2][row]) * self.row_h

    def gap_at(self, y):
        """The track row a line between rows nearest y (pixels in the rows) is above (a folder's sub-rows belong
        to the row above them: never split)."""
        first = self.lines()[2]
        return int(np.searchsorted(first[:-1], round(y / self.row_h)))

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
        else:  # (4 tracks a notch, like Domino's track 1 -> 5; arrow keys still one, user 2026-11-07)
            self.bar_move(False, self.top - notch * WHEEL_ROWS * self.row_h)

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
        line = self.line_at(y)
        return None if line is None else int(self.lines()[0][line])

    def line_at(self, y):
        """The line under y (a track's own or a folder's sub-row), None = above the rows / below the last."""
        if y < self.area()[1]:
            return None
        line = int((y - self.area()[1] + self.top) // self.row_h)
        return line if line < len(self.lines()[0]) else None

    def on_press(self, e):
        self.focus_set()
        self.tip.use()
        if time.perf_counter() - self.menu_shut < MENU_CLICK_S:  # (the click that closed the right-click menu:
            return                                                # Windows hands it on, like the Tempo pane's)
        playing = self.app.player.running  # (playing: the play line stays, user 2026-10-23)
        self._box = None
        if e.y < self.ruler_h:  # the play line, on the track list's snap (Shift: free); dragged: a ruler band
            if e.x >= self.name_w and self.sx:
                beat = max(0.0, self.x2t(e.x))
                if not playing:  # (moved on the press also when it becomes a band, like the piano roll's ruler)
                    self.app.set_playhead(beat if e.state & 0x1 else self.snapped(beat))
                self.box_press(e, beat, None, band=True)
            return
        row = self.row_at(e.y)
        self._sweep = None
        self.end_move()
        if row is None:
            return
        c = -1  # (a folder's sub-row: its channel)
        if row >= 0:
            line = self.line_at(e.y)
            c = int(self.lines()[1][line]) if line is not None else -1
            if c < 0 and e.x < self.name_w and self.on_fold(e.x) and self.mixed(row):  # (a folder's ▸ / ▾)
                return self.toggle_open(int(self.tracks()[row]))
            if c >= 0 and self.on_speaker(e.x):  # (a sub-row's speaker: that channel muted / heard)
                return self.toggle_mute((int(self.tracks()[row]), c))
        if row >= 0 and self.on_speaker(e.x):  # its speaker: muted / heard; dragged on: every speaker crossed the
            slot = int(self.tracks()[row])     # same (user 2026-10-26)
            self.toggle_mute(slot)
            self._sweep = (slot in self.muted, row)
            return
        picture = row >= 0 and e.x >= self.name_w and self.sx
        if c >= 0 and e.state & 0x5 and not picture:  # Ctrl / Shift on a sub-row's name: several channels
            return self.sub_click(row, c, e.state & 0x4, e.state & 0x1)
        if row >= 0 and e.state & 0x5 and self.current is not None:  # Ctrl / Shift: several tracks (select)
            if picture:  # (in the picture: dragged = a Select box, Ctrl adds it; let go without a drag = the click)
                return self.box_press(e, max(0.0, self.x2t(e.x)), row, pick=True)
            return self.select_click(row, e.state & 0x4, e.state & 0x1)
        if picture and self.box_press(e, max(0.0, self.x2t(e.x)), row):  # (a drag on the picture: a Select box;
            return  # a click: the play line + the track, as before; inside the boxes: box_release)
        if row == CONDUCTOR_ROW and e.x >= self.name_w and self.sx and \
                self.box_press(e, max(0.0, self.x2t(e.x)), row):  # (the Conductor's picture too, user 11-05)
            return
        if row >= 0 and e.x < self.name_w and c < 0:  # (a track's name: dragged = the tracks moved, group C)
            s, group = int(self.tracks()[row]), self.picked_tracks()
            # (on one of several selected: they stay selected for the drag; let go without one = that track alone,
            # like a file list)
            self._move = {"y": e.y, "on": False, "gap": None, "keep": s in group and len(group) > 1, "s": s,
                          "job": None, "ch": None, "into": None, "mid": None}
            if self._move["keep"]:
                return
        if row >= 0 and e.x < self.name_w and c >= 0 and (int(self.tracks()[row]) >= HAND or (
                int(self.tracks()[row]) < 0 and self.app.loaded is not None)):
            # (a sub-row of your track: that channel (+ the others picked with it) dragged into another track / out on
            # its own, PLAN #11 C2; a loaded track's: into its own track's rows / out as a part, D; pressed on one of
            # several picked: a click without a drag picks it alone)
            s, group = int(self.tracks()[row]), self.picked_chans()
            self._move = {"y": e.y, "on": False, "gap": None, "keep": (s, c) in group and len(group) > 1, "s": s,
                          "job": None, "ch": c, "into": None, "mid": None}
            if self._move["keep"]:
                return
        if e.x >= self.name_w and self.sx and not playing:  # (like many MIDI programs: the play line there, snapped)
            beat = max(0.0, self.x2t(e.x))  # (Shift: free, as the Select box's corner)
            self.app.set_playhead(self.ticked(beat) if e.state & 0x1 else self.snapped(beat))
        if row == CONDUCTOR_ROW:  # (the tempo's row highlighted; the piano roll keeps what it showed)
            return self.set_conductor(True)
        self.set_conductor(False)
        self.current = None if row < 0 else int(self.tracks()[row])
        if c >= 0:  # (a sub-row: only that channel shown, user 2026-10-28; a Shift+click's range starts here)
            self.current_ch = c
            self._sub_anchor = (self.current, c)
        else:  # (a track: a Ch rows' Shift range starts again from a Ch row clicked next, hunt C)
            self._sub_anchor = None
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
        self.set_conductor(False)
        if self.current_ch is not None:  # (Ch rows picked: tracks OR Ch rows, never both: a new pick, hunt C B)
            self.current, self.selected, self.anchor, self._sub_anchor = slot, set(), (slot, slot), None
            return self.shown_changed()
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

    # ------------------------------------------------------------ Select boxes (group E)

    def box_press(self, e, beat, row, band=False, pick=False):
        """A press on the picture (row) or the ruler (band): dragged = a Select box / a ruler band (box_drag). A
        press outside the kept boxes drops them (Ctrl: kept, the new one added)."""
        ctrl = bool(e.state & 0x4)
        inside = self.box_at(e.x, e.y)
        if not ctrl and not inside:
            self.drop_boxes()
        # (inside the kept boxes: dragged = what they hold moved, Ctrl = copied; a plain click = nothing)
        self._box = {"x": e.x, "y": e.y, "t": beat, "row": row, "band": band, "ctrl": ctrl,
                     "shift": bool(e.state & 0x1), "pick": pick, "on": False, "box": None, "move": inside,
                     "dt": 0.0, "drow": 0}
        return inside

    def box_at(self, x, y):
        """True if (x, y) is inside a kept box (on the ruler: a ruler band)."""
        if not self.boxes or self.sx is None or x < self.name_w:
            return False
        t = self.x2t(x)
        row = self.row_at(y)
        if y < self.ruler_h:
            return any(b["rows"] is None and b["t0"] <= t <= b["t1"] for b in self.boxes)
        if row is None or row < CONDUCTOR_ROW:
            return False
        # (a ruler band shows on the ruler only, user 2026-10-26: its notes are dragged from there)
        return any(b["rows"] is not None and b["t0"] <= t <= b["t1"] and b["rows"][0] <= row <= b["rows"][1]
                   for b in self.boxes)

    def box_drag(self, e):
        """The box being dragged: both corners on the NEAREST grid line (box_span: where the press put the play
        line), whole rows; a ruler band's ends the same (the piano roll's ruler). Shift: whole ticks (each corner as
        Shift was when it moved)."""
        b = self._box
        if not b["on"]:
            still = BOX_STILL * self.scale
            if abs(e.x - b["x"]) < still and (b["band"] or abs(e.y - b["y"]) < still):
                return
            b["on"] = True
            self.tip.hide()
        t, shift = max(0.0, self.x2t(e.x)), bool(e.state & 0x1)
        if b["move"]:
            return self.move_box_drag(b, e, t, shift)
        if b["band"]:
            lo, hi = sorted(self.ticked(x) if s else self.snapped(x) for x, s in ((b["t"], b["shift"]), (t, shift)))
            b["box"] = {"t0": lo, "t1": hi, "rows": None} if hi > lo else None
        else:
            top = self.area()[1]
            y = min(max(e.y, top), self.winfo_height() - 1)
            lines = self.lines()[0]
            if e.y < top or not len(lines):  # (above the tracks: the Conductor row, user 11-05)
                row = CONDUCTOR_ROW
            else:
                row = int(lines[min(max(int((y - top + self.top) // self.row_h), 0), len(lines) - 1)])
            lo, hi = self.box_span(b["t"], t, b["shift"], shift)
            b["box"] = {"t0": lo, "t1": hi, "rows": tuple(sorted((b["row"], row)))}
        self.request_redraw()

    def move_box_drag(self, b, e, t, shift):
        """The kept boxes dragged by their inside: dt = their left side onto the NEAREST grid line (Shift: ticks;
        the piano roll's box moves), never before beat 0; drow = rows, the boxes kept inside the list (a ruler band:
        time only)."""
        left = min(x["t0"] for x in self.boxes)
        want = left + t - b["t"]
        b["dt"] = max(0.0, self.ticked(want) if shift else self.snapped(want)) - left
        if any(x["rows"] is None or x["rows"][0] < 0 for x in self.boxes):  # (the Conductor's: time only)
            b["drow"] = 0
        else:
            top = self.area()[1]
            lines = self.lines()[0]
            line = int((e.y - top + self.top) // self.row_h)
            row = int(lines[min(max(line, 0), len(lines) - 1)]) + (line - len(lines) + 1 if line >= len(lines) else
                                                                   line if line < 0 else 0)
            lo = min(x["rows"][0] for x in self.boxes)
            hi = max(x["rows"][1] for x in self.boxes)
            b["drow"] = min(max(row - b["row"], -lo), len(self.tracks()) - 1 - hi)
        self.request_redraw()

    def box_release(self):
        """Let go: the box kept (Ctrl: with the others); boxes and ruler bands are never kept together (the piano
        roll's rule). No drag: a Ctrl / Shift click picks tracks (select_click)."""
        b, self._box = self._box, None
        if b["move"]:
            if b["on"] and (b["dt"] or b["drow"] or b["ctrl"]):
                self.box_work("copy" if b["ctrl"] else "move", b["dt"], b["drow"])
            elif not b["on"] and b["pick"]:
                self.select_click(b["row"], b["ctrl"], b["shift"])
            # (a plain click inside: nothing, the play line and the highlight stay; user 2026-10-26)
            self.request_redraw()
            return
        if b["on"]:
            new = b["box"]
            if new is not None:
                band = new["rows"] is None
                self.boxes = [x for x in (self.boxes if b["ctrl"] else []) if (x["rows"] is None) == band] + [new]
            self.request_redraw()
        elif b["pick"]:
            self.select_click(b["row"], b["ctrl"], b["shift"])

    def box_tempo(self, kind):
        """{"spans", "clip"} of the Conductor's changes in the boxes (a box from its row, a ruler band; user
        2026-11-05), None when no box holds the Conductor row. Ctrl+C / X: the clip is the tempo copy from now."""
        from window import track_tempo
        spans = track_tempo.tempo_spans(self.boxes)
        if not spans:
            return None
        # (no change inside = nothing, like Domino: a held stretch pasted with notes would change the tempo there)
        # (kept as the tempo copy only once the copy worked: tempo_only / App.put_clipboard; a refused copy left the
        # old copies alone, hunt 2026-11-05)
        clip = track_tempo.clip_of(self.app, spans) if track_tempo.changes(self.app, spans) else None
        return {"spans": spans, "clip": clip}

    def box_work(self, kind, dt=0.0, drow=0):
        """Ctrl+C ("clip") / Ctrl+X ("cut") / Delete ("delete") / a drag of the boxes ("move" / "copy"): their
        notes (track_moves.box_job) and the Conductor's changes they hold (track_tempo), one undo step."""
        from window.track_moves import box_job, has_notes, tempo_only
        from window.track_tempo import note_boxes
        if not self.boxes:
            return
        tempo = self.box_tempo(kind)
        if tempo is not None and tempo["clip"] is not None and kind in ("move", "copy"):
            from window.track_tempo import sig_dt  # (a time signature moved lands on a bar line, all with it)
            dt = sig_dt(self.app, tempo["spans"], dt, kind == "copy")
        notes = note_boxes(self.boxes)
        if tempo is not None and not has_notes(self.app, notes):
            return tempo_only(self.app, kind, tempo, dt)
        return box_job(self.app, notes, kind, dt, drow, tempo=tempo)

    def key_copy(self):
        """Ctrl+C in the list: the Select boxes' notes (track_moves.clip_boxed) + the Conductor's changes."""
        self.box_work("clip")

    def key_cut(self):
        self.box_work("cut")

    def key_delete(self):
        self.box_work("delete")

    def key_paste(self, conductor=False):
        """Ctrl+V in the list (Domino's): what was copied, at the play line, laid from the highlighted track down
        (App.paste); a Select box round what came in. The Conductor row highlighted: the copied tempo there; a copy
        holding notes and the Conductor's changes: both, the tempo the same distance from the notes as copied (user
        2026-11-05)."""
        app = self.app
        app.finish_copy()  # (a Ctrl+C's text still being made: waited for first)
        clip = getattr(app, "tempo_clip", None)
        # (a copy made in Domino / a shared line copied since Spiderweb's last copy comes in first, also with the
        # Conductor row lit: the ONE PASTE rule, hunt 2026-11-05)
        own = app.shared_clip() is None and not app.domino_newer()
        if own and (self.conductor_on or conductor or clip is not None and
                    clip.get("n", 0) > getattr(app, "shapes_stamp", 0)):
            return self.paste_tempo(clip)  # (the Conductor row lit, or the tempo copied last)
        both = (own and clip is not None and clip.get("with") is not None and clip["with"] is app.clipboard
                and app.clip_kind == "shapes")
        before = len(app.shapes)
        top = app.last_step()
        app.paste()
        new = list(range(before, len(app.shapes)))
        tempo_at = None
        if both and new and app.last_step() is not top:  # (in the paste's undo step)
            from notes.engine import cached_path
            start = min(b for sh in app.clipboard for b, _ in cached_path(sh))
            moved = min(b for i in new for b, _ in cached_path(app.shapes[i])) - start
            tempo_at = self.put_tempo(clip, clip["from"] + moved, tr("tempo.step_paste"))
        r = app.rendered
        mine = np.isin(r[:, 5], new) if new and len(r) else np.zeros(len(r), bool)
        rows = self.note_rows()[mine]
        rows = rows[rows >= 0]
        if not len(rows):
            return
        t0, t1 = int(r[mine, 0].min()) / app.ppq, int(r[mine, 1].max()) / app.ppq
        lo = int(rows.min())
        if tempo_at is not None:  # (the tempo came along: the box holds it too, so a drag / Delete takes both, hunt)
            t0, t1, lo = min(t0, tempo_at), max(t1, tempo_at + clip["length"]), CONDUCTOR_ROW
        self.boxes = [{"t0": t0, "t1": t1, "rows": (lo, int(rows.max()))}]
        self.request_redraw()

    def paste_tempo(self, clip):
        """The copied tempo (a Conductor row box's or the Conductor pane's) at the play line, one undo step; a box on
        the Conductor row round it."""
        app = self.app
        if not clip:
            app.status.config(text=tr("tempo.paste_nothing"))
            return
        name = tr("tempo.step_paste")
        app.push_undo(name=name)
        at = self.put_tempo(clip, app.playhead, name)
        self.boxes = [{"t0": at, "t1": at + clip["length"], "rows": (CONDUCTOR_ROW, CONDUCTOR_ROW)}] \
            if clip["length"] > 0 else []
        self.request_redraw()

    def put_tempo(self, clip, at, name):
        """clip laid from beat at (on a tick; in the caller's undo step) -> where it went."""
        from window import track_tempo
        pane = track_tempo.visual(self.app)  # (never kept inside a Keep timing part: Draw: Heard tempo, hunt)
        at = pane.tick_beat(max(0.0, at), skip=pane.ed()["points"])
        track_tempo.put(self.app, clip, at, name)
        return at

    def busy(self):
        """A drag going on in the list (tracks, a box, speakers): its keys wait."""
        return self._move is not None or self._box is not None or self._sweep is not None

    def drop_boxes(self):
        if self.boxes:
            self.boxes = []
            self.request_redraw()

    def ticked(self, beat):
        """beat on whole ticks."""
        ppq = self.app.ppq
        return max(0.0, round(beat * ppq) / ppq)

    def grid_edge(self, beat, after):
        """The grid line of the track list's snap at or before beat (after: at or after it)."""
        snap, sigs = self.snap.get(), sigs_of(self.app.tempo)
        if snap == "off":
            return self.ticked(beat)
        if snap == "bar":
            return bar_line(sigs, beat, after=after)
        step = snap_beats(snap, self.app.beats)
        if not step:
            return self.ticked(beat)
        at = where(sigs, beat)[2]
        k = (beat - at) / step
        if after:  # (a bar's last step ends on the next bar line)
            return min(at + math.ceil(k - 1e-9) * step, bar_line(sigs, beat, after=True))
        return at + math.floor(k + 1e-9) * step

    def box_span(self, a, b, shift_a, shift_b):
        """(lo, hi) beats of a box pressed at a, dragged to b: both corners on the NEAREST grid line, like the play
        line the press puts there (user 2026-10-26: the play line = the box's start or end); never thinner than a
        grid step (Shift: a tick)."""
        tick = 1 / self.app.ppq
        a = self.ticked(a if shift_a else self.snapped(a))
        if b >= a:
            hi = self.ticked(b if shift_b else self.snapped(b))
            if hi <= a + 1e-9:
                hi = a + tick if shift_b else self.grid_edge(a + tick, True)
            return a, hi
        lo = self.ticked(b if shift_b else self.snapped(b))
        if lo >= a - 1e-9:
            lo = a - tick if shift_b else self.grid_edge(a - tick, False)
        return max(0.0, lo), a

    def select_all(self):
        """Ctrl+A in the track list (like Domino's): a ruler band from the song's start to the last note's end over
        every track; the tracks' names stay as they are (user 2026-10-26: blue names = the TRACKS picked)."""
        end = self.last_end()
        self._box = None
        self.boxes = [{"t0": 0.0, "t1": end, "rows": None}] if end > 0 else []
        self.all_band = self.boxes[0] if self.boxes else None  # (a copy for Domino takes the tempo: copied_whole)
        self.request_redraw()

    def last_end(self):
        """The last note's end in beats, any track (a loaded MIDI's too); 0 = no notes."""
        n, ppq = self.app.rendered, self.app.ppq
        end = int(n[:, 1].max()) / ppq if len(n) else 0.0
        lm = self.app.loaded
        if lm is not None:
            end = max(end, lm.end / lm.ppq)
        return end

    def box_rects(self):
        """The kept boxes + the one being dragged on screen: [(x0, y0, x1, y1)] (a ruler band: on the ruler only,
        bands_x; user 2026-10-26, like Domino's)."""
        top, rh = self.area()[1], self.row_h
        shown = self.boxes + ([self._box["box"]] if self._box is not None and self._box["box"] is not None else [])
        m = self._box if self._box is not None and self._box["move"] and self._box["on"] else None
        if m is not None:  # (being moved: where they'd land)
            shown = [dict(b, t0=b["t0"] + m["dt"], t1=b["t1"] + m["dt"], rows=None if b["rows"] is None else
                          (b["rows"][0] + m["drow"], b["rows"][1] + m["drow"])) for b in self.boxes]
        out = []
        first = self.lines()[2]
        for b in shown:
            if b["rows"] is None:
                continue
            r0, r1 = b["rows"]  # (a row's open sub-rows inside its box too)
            # (the Conductor row (-1) stays put above the scrolled tracks: a box from it reaches down from there)
            y0 = self.conductor_y() if r0 < 0 else max(top, top + first[r0] * rh - self.top)
            y1 = top if r1 < 0 else max(top, top + first[r1 + 1] * rh - self.top)
            if y1 > y0:  # (scrolled away above: not drawn)
                out.append((self.t2x(b["t0"]), y0, self.t2x(b["t1"]), y1))
        return out

    def draw_boxes(self, w, h):
        """The boxes over the picture: a see-through fill in tiles (one picture per size, kept) + the Select box's
        outline (user 2026-10-26: outline + a light see-through fill)."""
        rects = self.box_rects()
        if not rects:
            return
        left, top = self.area()
        top = self.conductor_y()  # (box_rects keeps track boxes under the Conductor row)
        tile = FILL_TILE
        for x0, y0, x1, y1 in rects:
            a, b = max(round(x0), left), min(round(x1), w)
            c, d = max(round(y0), top), min(round(y1), h)
            for y in range(c, d, tile):
                for x in range(a, b, tile):
                    self.create_image(x, y, image=self.fill_picture(min(tile, b - x), min(tile, d - y)), anchor="nw")
        outline_boxes(self, rects, left, top, self.scale)

    def fill_picture(self, w, h):
        got = self._fills.get((w, h))
        if got is None:
            rgba = np.empty((h, w, 4), np.uint8)
            rgba[..., :3] = hex_rgb(look.TRACK_BOX_FILL)
            rgba[..., 3] = look.TRACK_BOX_ALPHA
            got = self._fills[(w, h)] = see_through(self, rgba)
            while len(self._fills) > 16:
                self._fills.popitem(last=False)
        else:
            self._fills.move_to_end((w, h))
        return got

    def bands_x(self):
        """[(x0, x1)] of the ruler bands on screen (the ruler black there, like the piano roll's)."""
        shown = self.boxes + ([self._box["box"]] if self._box is not None and self._box["box"] is not None else [])
        m = self._box if self._box is not None and self._box["move"] and self._box["on"] else None
        dt = m["dt"] if m is not None else 0.0
        return [(self.t2x(b["t0"] + dt), self.t2x(b["t1"] + dt)) for b in shown if b["rows"] is None]

    def step_track(self, way):
        """Ctrl+wheel on the piano roll / its panes (like Domino, user 2026-10-24): the next (way 1, wheel down) or
        previous track highlighted, empty ones too; the All tracks row (nothing highlighted) above the first one; stops
        at the top / last. The status line names it."""
        rows = self.tracks()
        if not len(rows):
            return
        # (an open folder's Ch rows are steps too, user 2026-10-28 E3; a shut one = the track as one)
        line_row, line_ch = self.lines()[:2]
        steps = [(None, None)] + [(int(rows[r]), None if c < 0 else int(c)) for r, c in zip(line_row.tolist(),
                                                                                           line_ch.tolist())]
        now = (self.current, self.current_ch)
        at = steps.index(now) if now in steps else (steps.index((self.current, None))
                                                    if (self.current, None) in steps else 0)
        k = min(max(at + way, 0), len(steps) - 1)
        self.set_conductor(False)
        if steps[k] != now:
            self.current, self.selected = steps[k][0], set()
            self.current_ch = steps[k][1]
            self.shown_changed()
        if k == 0:
            return self.app.status.config(text=tr("tracks.all"))
        s, c = steps[k]
        name = self.name_of(self.row_of_id(s))
        if c is not None:
            return self.app.status.config(text=tr("tracks.mark_ch", name=name, ch=c + 1))
        ch = self.channel_of(s)
        self.app.status.config(text=tr("tracks.stepped", name=name, ch=ch) if ch else name)

    def show_all(self):
        """The All tracks row highlighted (current None): the piano roll shows every track and Spiderweb picks new
        shapes' channels (Esc, a click on the row, All tracks in the ▾ list)."""
        self.selected = set()
        self.set_conductor(False)
        if self.current is not None:
            self.current = None
            self.shown_changed()
        else:
            self.request_redraw()

    def set_conductor(self, on):
        """The Conductor row highlighted (on) or not; the tracks' highlight shows again when it goes, and the
        Conductor pane gives the keys back (hunt 2026-11-05: after a Tab round trip a track's Ctrl+V pasted tempo)."""
        app = self.app
        if not on and getattr(app, "key_pane", None) is getattr(app, "tempo_pane", None) is not None:
            app.key_pane = None
        if self.conductor_on != on:
            self.conductor_on = on
            self.request_redraw()

    def picked_tracks(self):
        """The highlighted track and the ones selected with it, in the list's order (the right-click menu's). Ch rows
        picked (a folder's sub-row highlighted) = no track: picks are tracks OR Ch rows, never both (hunt C,
        user 2026-10-28 picked B: a lit "Ch 2" never takes its whole folder along)."""
        if self.current_ch is not None:
            return [int(s) for s in self.tracks() if int(s) in self.selected]
        group = self.selected | ({self.current} if self.current is not None else set())
        return [int(s) for s in self.tracks() if int(s) in group]

    def on_drag(self, e):
        """A drag from a speaker: every track the mouse crosses gets what the pressed one got (muted or heard). From
        a track's name: the tracks moved (move_drag)."""
        if self._move is not None:
            return self.move_drag(e.y)
        if self._box is not None:
            return self.box_drag(e)
        if self._sweep is None:
            return
        on, last = self._sweep
        row = self.row_at(min(max(e.y, self.area()[1]), self.winfo_height() - 1))
        if row is None or row < 0:
            row = len(self.tracks()) - 1 if e.y >= self.area()[1] else 0
        a, b = sorted((last, row))
        slots = {int(s) for s in self.tracks()[a:b + 1]}
        # (a folder's speaker is its channels' master: all of them go along)
        new = {m for m in self.muted if not (isinstance(m, tuple) and m[0] in slots)}
        new = new | slots if on else new - slots
        self._sweep = (on, row)
        if new != self.muted:
            self.muted = new
            self.request_redraw()
            self.app.play_changed()

    def on_release(self, e):
        self._sweep = None
        if self._box is not None:
            return self.box_release()
        m = self._move
        if m is None:
            return
        self.end_move()
        if m["on"]:
            if m["into"] is not None:
                self.drop_into(m)
            elif m["gap"] is not None:
                if m["ch"] is not None and self.loaded_drag(m):  # (a part row whole: moved like a track, hunt D)
                    self.parts_out(self.loaded_drag(m), m["gap"])
                elif m["ch"] is not None:
                    self.channels_out(self.drag_parts(m), m["gap"])
                else:
                    self.move_tracks(m["gap"])
        elif m["keep"]:  # (a click on one of several selected tracks: that one alone, like a file list)
            self.current, self.selected, self.anchor = m["s"], set(), (m["s"], m["s"])
            self.current_ch = m["ch"]  # (... one of several sub-rows: that channel alone)
            self.shown_changed()

    def on_escape(self, e):
        """Esc: tracks being dragged stay where they were; else the All tracks row (user 2026-10-23: the Conductor
        click alone was hidden)."""
        if self._move is not None and self._move["on"] and e.state & 0x100:  # (still held: a release missed leaves
            return self.end_move()                                          # no drag behind)
        self.end_move()
        if self._box is not None and e.state & 0x100:  # (a box being dragged: called off)
            self._box = None
            return self.request_redraw()
        self._box = None
        if self.boxes:  # (the kept Select boxes go first, like the piano roll's)
            return self.drop_boxes()
        self.show_all()

    def moving(self):
        """The tracks a drag from a name moves: the highlighted one and the ones selected with it, a loaded MIDI's
        too (D4; not while it waits for its MIDI: those rows stay first)."""
        loaded = self.app.loaded is not None
        return [s for s in self.picked_tracks() if s >= 0 or loaded]

    def move_drag(self, y):
        """Tracks dragged by a name (group C, like Domino's): a line between two rows shows where they land; past
        the list's top / bottom it scrolls (move_edge)."""
        m = self._move
        if not m["on"]:
            if abs(y - m["y"]) < MOVE_PX * self.scale:
                return
            if m["ch"] is None and not self.moving():
                self._move = None
                return
            m["on"] = True
            self.tip.hide()
        m["last"] = y
        top, h = self.area()[1], self.winfo_height()
        self.drop_spot(m, y - top + self.top)
        if (y < top or y > h) and m["job"] is None:
            m["job"] = self.after(EDGE_MS, self.move_edge)
        self.request_redraw()

    def drag_parts(self, m):
        """What m drags, as [(track id, [its channels])] top down: picked sub-rows (several: the ones picked with
        the pressed one, check-in C) or the dragged tracks' every channel (an empty track of yours: its own one, it
        comes in as an empty "Ch n" row, user 2026-10-28). Your channels only: a loaded MIDI's stay (stage D)."""
        chans = self.row_chans()
        if m["ch"] is not None:
            group = self.picked_chans()
            pairs = group if (m["s"], m["ch"]) in group and len(group) > 1 else {(m["s"], m["ch"])}
            out = {}
            for s, c in sorted((p for p in pairs if p[0] >= HAND and self.line_of(*p) is not None),
                               key=lambda p: self.line_of(*p)):
                out.setdefault(s, []).append(c)
            return list(out.items())
        group = self.moving()
        group = group if m["s"] in group else [m["s"]]
        out = []
        for s in sorted(group, key=lambda s: self.row_of_id(s) if self.row_of_id(s) is not None else 0):
            row = self.row_of_id(s)
            if row is None or s < 0:
                return []  # (a loaded MIDI's track among them: its channels don't move in C)
            own = self.own_track(s)
            got = list(chans[row]) or ([own["ch"]] if own is not None else
                                       [slot_track_channel(s, self.app.picture_use10)[1]] if s < HAND else [])
            out.append((s, got))
        return out

    def can_take(self, m, row):
        """May what m drags go INTO the track in row (PLAN #11 C2)? Your channels (picked sub-rows, or tracks of yours
        / Spiderweb's: not a loaded MIDI's, its channels move in stage D) into any other track; sub-rows into their own
        folder = a new place among its channels. An empty track only into one that keeps its channel list (not a
        loaded one)."""
        if not 0 <= row < len(self._all):
            return False
        dst = int(self._all[row])
        got = self.loaded_drag(m)
        if got:  # (a loaded track's channels: only among its own track's rows, PLAN #11 D)
            n = got[0][1]
            return dst < 0 and self.file_part(dst)[0] == n and not (m["ch"] is None and dst == m["s"])                 and (any(s != dst for s, _, _ in got) or self.mixed(row))  # (its own folder's: a new order)
        parts = self.drag_parts(m)
        if not parts:
            return False
        if m["ch"] is None and dst in [s for s, _ in parts]:
            return False
        chans = self.row_chans()
        return dst >= 0 or all(chans[self.row_of_id(s)] for s, _ in parts)

    def drop_spot(self, m, ly):
        """Where a drag at ly (pixels in the rows) lands: m["gap"] = a row it goes above as a track (Domino's line),
        or m["into"] = (row, place among its sub-rows or None = the end) = INTO that track. An open folder: between
        its Ch rows / the upper half at its end = into (decision 7); its track line's lower half = its first place.
        Any other track: the middle of its row rested on HOVER_MS = into (lit), so passing over never mixes one by
        accident (user 2026-10-28); its edges = the line above / below."""
        rows, chs, first = self.lines()[:3]
        li = int(ly // self.row_h)
        f = ly / self.row_h - li
        m["into"], mid = None, None
        m["gap"] = min(max(self.gap_at(ly), 0), len(self.tracks()))
        if 0 <= li < len(rows) and self.can_take(m, int(rows[li])):
            row, c = int(rows[li]), int(chs[li])
            folder = first[row + 1] - first[row] > 1  # (open, sub-rows listed)
            if c >= 0:  # (between an open folder's Ch rows: the line there, not the one under the folder)
                m["into"], m["gap"] = (row, li - int(first[row]) - 1 + (f >= 0.5)), None
            elif folder and f >= 0.5:
                m["into"], m["gap"] = (row, 0), None
            elif not folder and 0.25 <= f <= 0.75:
                mid = row
                m["gap"] = row + (f >= 0.5)
        if mid is None or m["mid"] is None or m["mid"][0] != mid:
            if m["mid"] is not None:
                self.after_cancel(m["mid"][1])
            m["mid"] = None if mid is None else [mid, self.after(HOVER_MS, self.light_row), False]
        elif m["mid"][2]:
            m["into"], m["gap"] = (mid, None), None

    def light_row(self):
        """The row rested on long enough while dragging: lit = the drop goes into it."""
        m = self._move
        if m is None or m["mid"] is None:
            return
        m["mid"][2] = True
        m["into"], m["gap"] = (m["mid"][0], None), None
        self.request_redraw()

    def drop_into(self, m, parts=None, step=None):
        """m's channels (drag_parts: picked sub-rows, or the dragged tracks' every channel) put into m["into"]'s
        track in their top-down order (C2; check-in C: several at once): one undo step, ONE same-channel question
        for every clash (decision 10). Automatic tracks touched = all made yours first (like a drag). Whole tracks
        of yours moved in are emptied: they go."""
        from window.track_channels import (ask_clash, entry_of, forget_channels, free_channels, move_channels,
                                           set_order, tidy_tracks)
        app = self.app
        row, pos = m["into"]
        dst = int(self._all[row])
        if parts is None and self.loaded_drag(m):
            got = self.loaded_drag(m)
            if all(s == dst for s, _, _ in got):  # (Ch rows moved among their own folder's: only the order)
                return self.reorder_loaded(dst, [c for _, _, cs in got for c in cs], pos)
            return self.parts_into(got, dst)
        parts = self.drag_parts(m) if parts is None else parts  # (Join: given)
        now = list(self.row_chans()[row])
        whole = m["ch"] is None
        if not parts:
            return
        if all(s == dst for s, _ in parts):  # (sub-rows moved among their folder's: only the order)
            return self.reorder(dst, [c for _, cs in parts for c in cs], pos, now)
        # (into a loaded track's PART / a track with parts, hunt D: the file's track takes them, each channel shown
        # on the row holding it; the whole track's channels count for the same-channel question)
        target, every = dst, set(now)
        if dst < 0 and self.file_part(dst)[1] is not None:
            n = self.file_part(dst)[0]
            dst = -1 - n
            every = {c for r, x in enumerate(self.row_chans()) if int(self._all[r]) < 0
                     and self.file_part(int(self._all[r]))[0] == n for c in x}
        seq = [(s, c) for s, cs in parts for c in cs]
        held, clash = set(every), []
        for s, c in seq:  # (its own folder's channels only take a place)
            if s != dst:
                if c in held:
                    clash.append((s, c))
                held.add(c)
        was = {s: self.mute_state(s) == 2 for s, _ in parts}
        autos, owners = [], []
        if any(0 <= s < HAND for s, _ in parts) or 0 <= dst < HAND:
            got = self.snapshot()
            if got is None:
                return
            autos, owners = got
        to = {}
        if clash:  # (those channels are there already: Merge / Move / Cancel, C3)
            mine = {entry_of(s) for s, _ in parts if s >= HAND}
            # (Merge is pressed by Enter when nothing would sound different: into a track of yours or Spiderweb's
            # (made yours on the way), no CC lines; a loaded track's channel may play another instrument)
            calm = dst >= 0 and not any("cc" in sh or "hz" in sh for sh in app.shapes if mine & set(into_ids(sh)))
            free = free_channels(app, held, len(clash))
            got = ask_clash(app, self.name_of(row), sorted({c for _, c in clash}), free, calm, need=len(clash))
            if got == "cancel":
                return
            if got == "move":
                to = dict(zip(clash, free))
        app.roll.end_typing()
        app.push_undo(name=step or tr("tracks.into_step"))
        app.own_tracks = self.named()  # (names stick: "Track n" as they show before tracks go)
        if not autos and whole:  # (whole tracks going: the ones after them would shift, keep_places)
            self.keep_places(min(self.row_of_id(s) for s, _ in parts))
        if autos:
            rows = full_plan(app.file_rows(), self._slots, app.own_tracks, self._filled)
            taken = self.take_named(autos, owners, rows)[0]
            mine_now = lambda s: HAND + taken[s] if s in taken else s  # noqa: E731
            parts = [(mine_now(s), cs) for s, cs in parts]
            to = {(mine_now(s), c): n for (s, c), n in to.items()}
            was = {mine_now(s): w for s, w in was.items()}
            seq = [(mine_now(s), c) for s, c in seq]
            dst, target = mine_now(dst), mine_now(target)  # (Spiderweb's track made yours: its new id, E3 hunt)
        if target in self.muted:  # (a muted track taking channels: its own stay muted, the ones coming keep theirs)
            self.muted = (self.muted - {target}) | {(target, c) for c in now}
        srcs = [s for s, _ in parts]
        # (a tinted track touched = yours, like a drag)
        app.own_tracks = [{k: v for k, v in t.items() if k != "auto"} if HAND + t["id"] in srcs + [dst] else t
                          for t in app.own_tracks]
        for s, cs in parts:
            if s != dst:
                move_channels(app, s, set(cs), dst, {c: n for (x, c), n in to.items() if x == s})
                forget_channels(app, s, cs)
        # (in their order where dropped; a Moved clash only takes another number, a Merged one is the sub-row there)
        arrived = []
        for s, c in seq:
            n = to.get((s, c), c)
            if (s == dst or (s, c) in to or c not in every) and n not in arrived:
                arrived.append(n)
        set_order(app, dst, arrived, pos, now)
        if target != dst and key_part(-1 - target) >= 0 and arrived:  # (a part: the new channels shown on it)
            k, j = self.file_part(target)[0], key_part(-1 - target)
            p, props = app.file_props[k], dict(app.file_props)
            props[k] = dict(p, parts=[dict(q, chs=q["chs"] + [c for c in arrived if c not in q["chs"]])
                                      if q["id"] == j else q for q in p["parts"]])
            app.file_props = props
        if whole:  # (whole tracks of yours moved in: empty now, gone like Domino's merge)
            app.own_tracks = [t for t in app.own_tracks if HAND + t["id"] not in srcs or HAND + t["id"] == dst]
        tidy_tracks(app)
        # (mutes go along: each channel muted there stays muted where it went)
        moved = {(target, to.get((s, c), c)) for s, c in seq if s != dst and (was.get(s) or (s, c) in self.muted)}
        self.muted = {x for x in self.muted if not (whole and x in srcs)
                      and not (isinstance(x, tuple) and (x[0], x[1]) in set(seq) and x[0] != dst)} | moved
        if whole and self.current in srcs:
            self.current = target
        if target in self.open or len(set(now) | set(arrived)) > 1:
            self.open.add(target)  # (so you see where it went)
        self.own_changed()

    def channel_out(self, src, ch, gap):
        """Channel ch of your track src dragged out to above row gap (channels_out)."""
        self.channels_out([(src, [ch])], gap)

    def channels_out(self, parts, gap, step=None):
        """Sub-rows of your tracks (parts: [(track, [channels])] top down) dragged out to above row gap: ONE TRACK
        EACH there, side by side in that order (check-in C, user picked), its colours moved in (C2); the first
        highlighted. One undo step."""
        from window.track_channels import forget_channels, move_channels, tidy_tracks
        app = self.app
        seq = [(s, c) for s, cs in parts for c in cs
               if self.row_of_id(s) is not None and c in self.row_chans()[self.row_of_id(s)]]
        if not seq:
            return
        app.roll.end_typing()
        app.push_undo(name=step or tr("tracks.out_step"))
        self.keep_places(gap)  # (Spiderweb's tracks from there on would shift: made yours when they can be)
        new, ids = add_tracks(self.named(), self._slots, self.own_row(gap - 1) if gap > 0 else -1,
                              [c for _, c in seq], app.own_next, self._filled, app.file_rows())
        app.own_next = max(ids) + 1
        app.own_tracks = new
        for (s, c), v in zip(seq, ids):
            move_channels(app, s, {c}, HAND + v)
            forget_channels(app, s, [c])
            if (s, c) in self.muted or s in self.muted:  # (its mute goes along)
                self.muted = (self.muted - {(s, c)}) | {HAND + v}
        tidy_tracks(app)
        self.current = HAND + ids[0]
        self.own_changed()
        self.name_new(ids)

    # ------------------------------------------------------------ Split / Join (PLAN #11 D)

    def split_track(self, s):
        """Split into tracks: a mixed track = one track per channel where it was, in its sub-rows' order (the first
        stays this track, the rest right after it); a loaded one's = parts (the file's notes stay). One undo step."""
        row = self.row_of_id(s)
        if row is None or not self.mixed(row):
            return
        rest = list(self.row_chans()[row][1:])
        if s < 0:
            return self.parts_out([(s, self.file_part(s)[0], rest)], row + 1, tr("tracks.split_step"))
        self.channels_out([(s, rest)], row + 1, tr("tracks.split_step"))
        self.current = s

    def join_why(self, group):
        """Join into a mixed track on group: None = it can, else the menu's label saying why not."""
        if len(group) < 2 or any(self.row_of_id(s) is None for s in group):
            return tr("tracks.join")
        top, *rest = sorted(group, key=self.row_of_id)
        loaded = [s for s in group if s < 0]
        if loaded and self.app.loaded is None:
            return tr("tracks.join")
        if top >= 0 and loaded:
            return tr("tracks.join_into_yours")
        if top < 0 and len({self.file_part(s)[0] for s in loaded}) > 1:
            return tr("tracks.join_loaded")
        return None

    def join_tracks(self, group):
        """Join into a mixed track: the picked tracks' channels into the topmost, in the list's top-down order (a
        mixed one's sub-rows together), ONE same-channel question for all (drop_into); a loaded track's rows = its
        parts joined (parts_into). One undo step."""
        if self.join_why(group) is not None:
            return
        top, *rest = sorted(group, key=self.row_of_id)
        row = self.row_of_id(top)
        if all(s < 0 for s in group):
            n = self.file_part(top)[0]
            return self.parts_into([(s, n, list(self.row_chans()[self.row_of_id(s)])) for s in rest], top,
                                   tr("tracks.join_step"))
        chans = self.row_chans()
        parts = []
        for s in rest:
            own = self.own_track(s)
            got = list(chans[self.row_of_id(s)]) or ([own["ch"]] if own is not None else
                                                     [slot_track_channel(s, self.app.picture_use10)[1]])
            parts.append((s, got))
        m = {"into": (row, None), "ch": None, "s": rest[0]}
        self.drop_into(m, parts, tr("tracks.join_step"))
        self.selected = set()

    # ------------------------------------------------------------ a loaded track's parts (PLAN #11 D)

    def loaded_drag(self, m):
        """What m drags when it's a loaded MIDI's channels (user 2026-10-28: they keep the file's notes): [(track
        list id, track number, [channels])] top down: picked sub-rows of loaded tracks, or dragged PART rows whole;
        [] = anything else (yours, a loaded track's own row: moved as tracks)."""
        if self.app.loaded is None:
            return []
        chans = self.row_chans()
        if m["ch"] is not None:
            group = self.picked_chans()
            pairs = group if (m["s"], m["ch"]) in group and len(group) > 1 else {(m["s"], m["ch"])}
            if any(s >= 0 or self.line_of(s, c) is None for s, c in pairs):
                return []
            out = {}
            for s, c in sorted(pairs, key=lambda p: self.line_of(*p)):
                out.setdefault(s, []).append(c)
            return [(s, self.file_part(s)[0], cs) for s, cs in out.items()]
        group = self.moving()
        group = group if m["s"] in group else [m["s"]]
        if not group or any(s > -1 - PART or self.row_of_id(s) is None for s in group):
            return []
        group = sorted(group, key=self.row_of_id)
        return [(s, self.file_part(s)[0], list(chans[self.row_of_id(s)])) for s in group]

    def parts_out(self, got, gap, step=None):
        """A loaded track's channels (loaded_drag's) dragged out above row gap: each a PART row of its own there, in
        that order (one track each, like yours), the file's notes staying the file's; one undo step."""
        app = self.app
        props = {n: dict(p, parts=[dict(q, chs=list(q["chs"])) for q in p.get("parts", ())])
                 for n, p in app.file_props.items()}
        new, mutes = [], set()
        for s, n, cs in got:
            p = props.setdefault(n, {"name": None, "ch": None})
            parts = p.setdefault("parts", [])
            for q in parts:  # (out of the part it was in)
                q["chs"] = [c for c in q["chs"] if c not in cs]
            parts[:] = [q for q in parts if q["chs"]]
            for c in cs:
                j = max((q["id"] for q in parts), default=-1) + 1
                parts.append({"id": j, "chs": [c]})
                new.append(part_key(n, j))
                if s in self.muted or (s, c) in self.muted:  # (its mute goes along)
                    mutes.add(-1 - part_key(n, j))
        if self.set_parts(props, new, gap, step or tr("tracks.out_step")):
            self.muted = {x for x in self.muted if not (isinstance(x, tuple) and any(
                x[0] == s and x[1] in cs for s, _, cs in got))} | mutes
            self.current = -1 - new[0]

    def parts_into(self, got, dst, step=None):
        """A loaded track's channels (loaded_drag's) dropped INTO another row of the same track: into that part, or
        back onto the track's own row; parts left empty go. One undo step."""
        app = self.app
        n, j = self.file_part(dst)[0], key_part(-1 - dst)
        p = app.file_props.get(n) or {}
        parts = [dict(q, chs=list(q["chs"])) for q in p.get("parts", ())]
        moving = [c for s, _, cs in got if s != dst for c in cs]
        for q in parts:
            q["chs"] = [c for c in q["chs"] if c not in moving] + (moving if q["id"] == j else [])
        props = dict(app.file_props)
        props[n] = dict(p, parts=[q for q in parts if q["chs"]])
        if not props[n]["parts"]:
            props[n].pop("parts")
            if props[n].get("name") is None and props[n].get("ch") is None and not props[n].get("order"):
                props.pop(n)  # (its Ch rows' order stays, hunt D)
        muted = {(s, c) for s, _, cs in got for c in cs if s in self.muted or (s, c) in self.muted}
        if dst in self.muted:  # (a muted row taking channels: its own stay muted, the ones coming keep theirs)
            self.muted = (self.muted - {dst}) | {(dst, c) for c in self.row_chans()[self.row_of_id(dst)]}
        if self.set_parts(props, [], None, step or tr("tracks.into_step")):
            self.muted = {x for x in self.muted if not (isinstance(x, tuple) and (x[0], x[1]) in muted)} | {
                (dst, c) for _, c in muted}
            if self.current in [s for s, _, _ in got] and self.row_of_id(self.current) is None:
                self.current = dst
            if len(self.row_chans()[self.row_of_id(dst)]) > 1:
                self.open.add(dst)  # (so you see where they went)
            self.request_redraw()

    def reorder_loaded(self, s, chans, pos):
        """A loaded folder's Ch rows dragged among themselves (chans in their order, to place pos; None = the end):
        a new order kept in the project only (a MIDI has none inside a track; channels and notes unchanged). One
        undo step; dropped where they were = nothing."""
        app = self.app
        now = list(self.row_chans()[self.row_of_id(s)])
        rest = [c for c in now if c not in chans]
        at = len(rest) if pos is None else sum(1 for c in now[:pos] if c not in chans)
        order = rest[:at] + list(chans) + rest[at:]
        if order == now:
            return
        n, j = self.file_part(s)[0], key_part(-1 - s)
        props = dict(app.file_props)
        p = dict(props.get(n) or {"name": None, "ch": None})
        if j >= 0:
            p["parts"] = [dict(q, chs=order + [c for c in q["chs"] if c not in order]) if q["id"] == j else q
                          for q in p.get("parts", ())]
        else:
            p["order"] = order
        props[n] = p
        self.set_parts(props, [], None, tr("tracks.order_step"))

    def set_parts(self, props, new, gap, step):
        """app.file_props = props (parts changed) as one undo step: the new parts' rows (their file_order entries)
        put above row gap (None: right after their track), parts gone left out; your tracks keep their places
        among the file's rows (from_rows). False = nothing changed."""
        app = self.app
        if props == app.file_props:
            return False
        full = full_plan(app.file_rows(), self._slots, app.own_tracks, self._filled)
        ids = [r if type(r) is int else HAND + r["id"] for r in full]
        at = None if gap is None else (ids.index(int(self._all[gap])) if gap < len(self._all)
                                       and int(self._all[gap]) in ids else len(full))
        live = set(part_keys(props, len(app.loaded.tracks)))
        gone = [r for r in (self.row_of_id(-1 - k) for k in self._part_row if k not in live) if r is not None]
        first = gap if new else min(gone) if gone else None  # (the first row whose place changes, if any)
        app.roll.end_typing()
        app.push_undo(name=step)
        self.keep_places(first)  # (rows after it shift: Spiderweb's made yours when they can)
        rows = full_plan(app.file_rows(), self._slots, app.own_tracks, self._filled)
        if at is not None:
            rows = rows[:at] + [-1 - k for k in new] + rows[at:]
        rows = [r for r in rows if not (type(r) is int and r <= -1 - PART and -1 - r not in live)]
        app.file_props = props
        app.file_order = clean_file_order([-1 - r for r in rows if type(r) is int and r < 0] + app.file_gone)
        app.own_tracks = from_rows(rows, self._slots, app.own_tracks, self._filled)
        self.own_changed()
        return True

    def reorder(self, s, chans, pos, now):
        """Sub-rows dragged among their own folder's (your track s; chans in their order; now = the folder's order):
        a new place, kept in the save only (PLAN #11 decision 8). One undo step; dropped where they were =
        nothing."""
        from window.track_channels import set_order
        app = self.app
        if s < HAND:
            return
        old = list(app.own_tracks)
        set_order(app, s, chans, pos, now)
        new, app.own_tracks = app.own_tracks, old
        if next((t.get("order") for t in new if HAND + t["id"] == s), None) == now:
            return
        app.roll.end_typing()
        self.set_own(new, tr("tracks.order_step"))

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
            if m.get("mid") is not None:
                self.after_cancel(m["mid"][1])
            if m["on"]:
                self.request_redraw()

    def move_tracks(self, gap):
        """The dragged tracks (moving) put together above row gap, in their order: one undo step. Spiderweb's
        automatic tracks among them become yours first (take_tracks). A loaded MIDI's tracks moved = app.file_order
        (the list and the export follow it; the piano roll keeps drawing in the file's order, user 2026-10-26 B).
        Nothing moves when they're there already."""
        app = self.app
        group = self.moving()
        # (every track, also tinted ones left out of the list)
        full = full_plan(app.file_rows(), self._slots, app.own_tracks, self._filled)
        ids = [r if type(r) is int else HAND + r["id"] for r in full]
        # (above a row waiting for its MIDI, listed first: the top)
        at = (ids.index(int(self._all[gap])) if int(self._all[gap]) in ids else 0) if gap < len(self._all) \
            else len(full)
        rows = move_rows(full, {ids.index(s) for s in group if s in ids}, at)
        if [r if type(r) is int else HAND + r["id"] for r in rows] == ids:
            return
        autos, owners = [], []
        if any(0 <= s < HAND for s in group):
            got = self.snapshot()
            if got is None:
                return
            autos, owners = got
        app.roll.end_typing()
        app.push_undo(name=tr("tracks.move_step"))
        # (names stick: "Track n" as they show before the move; Spiderweb's tracks made yours when they can be)
        if autos:
            taken, rows = self.take_named(autos, owners, rows)
        else:  # (from the first row whose place changes)
            taken = self.keep_places(min([gap] + [self.row_of_id(s) for s in group if self.row_of_id(s) is not None]))
            rows = [{"id": taken[x]} if type(x) is int and x in taken else x for x in rows]
        moved = {s - HAND for s in group if s >= HAND} | set(taken.values())
        # (a tinted track dragged = yours, like Properties)
        app.own_tracks = [{k: v for k, v in t.items() if k != "auto"} if t["id"] in moved else t
                          for t in from_rows(rows, self._slots, app.own_tracks, self._filled)]
        if app.loaded is not None:
            # (the removed ones after them: the order holds every track of the file)
            app.file_order = clean_file_order([-1 - r for r in rows if type(r) is int and r < 0] + app.file_gone)
        self.own_changed()
        if taken:
            app.status.config(text=trn("tracks.made_yours", len(taken), n=len(taken)))

    def snapshot(self, quiet=False):
        """Touching an automatic track makes them ALL yours (dragged, Properties): (every automatic slot, the shapes on
        them), or None when one can't go in (status line; quiet: none, keep_places)."""
        from window.app import can_go_in, placed_on
        app = self.app
        r = app.rendered
        # (EVERY automatic track is taken, empty ones too, with every shape on them: nothing pinned, no track split in
        # two, nothing handed out again, only the order changes, like Domino's; user 2026-10-26, after pinned colours
        # kept empty tracks between them and the music left behind got new channels / gap tracks)
        autos = [int(s) for s in self._slots]
        owners = set(np.unique(r[r[:, 4] < OWN0, 5]).tolist()) if len(r) else set()
        # (a shape with no notes of its own now, e.g. all merged into others' in Single channel: it'd come back on a
        # track of Spiderweb's later)
        noted = set(np.unique(r[:, 5]).tolist()) if len(r) else set()
        chans = app.own_channels()
        owners |= {i for i, sh in enumerate(app.shapes)
                   if i not in noted and can_go_in(sh) and not placed_on(sh, chans)}
        owners |= {i for i, sh in enumerate(app.shapes) if any(v < 0 for v in placed_on(sh, chans))}  # (pinned)
        owners = sorted(owners)
        if any(not can_go_in(app.shapes[i]) for i in owners if i < len(app.shapes)):
            if not quiet:
                app.status.config(text=tr("tracks.cant_move"))
            return None
        return autos, owners

    def keep_places(self, first=0):
        """Before the list's order changes from row `first` on (a move, a drag in / out, Add / Remove Track; in the
        undo step made already): when one of Spiderweb's automatic tracks is at or after it (its place number would
        shift), they're made yours, named "Track n" as they show (user 2026-10-28: names stick; untouched order =
        as before). Only when every shape on them can go in (an image / mask / Hz bass can't): else they all stay
        Spiderweb's, counted by place, and nothing is refused. Yours are named either way. {slot: new id}."""
        app = self.app
        if first is None or not any(0 <= int(s) < HAND for s in self.tracks()[max(first, 0):]):
            app.own_tracks = self.named()
            return {}
        got = self.snapshot(quiet=True)
        if got is None:
            app.own_tracks = self.named()
            return {}
        rows = full_plan(app.file_rows(), self._slots, app.own_tracks, self._filled)
        taken, rows = self.take_named(*got, rows)
        app.own_tracks = from_rows(rows, self._slots, app.own_tracks, self._filled)  # (in their places)
        self.tracks()
        if taken:
            app.status.config(text=trn("tracks.made_yours", len(taken), n=len(taken)))
        return taken

    def take_all(self, autos, owners, rows):
        """snapshot's tracks made yours (in the undo step made already): ({slot: its new id}, rows (full_plan's) with
        them in place); the highlight, picks, mutes go along."""
        app = self.app
        taken = self.take_tracks(autos, owners)
        if taken:
            app.start_track = False  # (an empty Track 1 is yours now: not listed twice)
            rows = [{"id": taken[x]} if type(x) is int and x in taken else x for x in rows]

            def mine_now(s):  # (an automatic track taken: its new id)
                return HAND + taken[s] if s in taken else s
            self.current = None if self.current is None else mine_now(self.current)
            self.selected = set(map(mine_now, self.selected))
            self.map_muted(mine_now)
            self.map_behind(mine_now)
            app.shapes_changed()  # (its shapes on the new tracks: the automatic tracks as they are after)
            self.tracks()
        return taken, rows

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
            # mode, e.g. an Extract, keeps every track, hunt 2026-10-26; a Domino paste too, user 2026-11-01)
            entries = [taken.get(s, -1 - s) for s in (slots if multi or pinned_slots(sh, "single") or pasted_notes(sh)
                                                      else slots[:1])]
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
        The names (and below them): the tracks' menu (track_menu); the notes' picture / a ruler band: the edit menu
        (edit_menu; user 2026-10-26, Domino's). Nothing while a drag is held (the piano roll's rule, hunt 10-26)."""
        if self.busy() or getattr(e, "state", 0) & 0x100:
            return
        row = self.row_at(e.y)
        if e.x >= self.name_w and self.sx and (e.y >= self.ruler_h or self.box_at(e.x, e.y)):
            return self.edit_menu(e)
        if e.y >= self.ruler_h and (row is None or row < 0 or not self.on_speaker(e.x)):
            return self.track_menu(e, row)
        if row is None or row < 0:
            return
        slot, every = int(self.tracks()[row]), {int(s) for s in self.tracks()}
        line = self.line_at(e.y)
        c = int(self.lines()[1][line]) if line is not None else -1
        alone = every - {slot}
        if c >= 0:  # (a folder's Ch row: only that channel heard, its folder's other channels muted too; hunt C)
            alone |= {(slot, x) for x in self.row_chans()[row] if x != c}
        self.muted = set() if self.muted == alone else alone
        self.tip.hide()
        self.request_redraw()
        self.app.play_changed()

    def edit_menu(self, e):
        """Right-click on the notes' picture: Cut / Copy / Paste / Delete, Copy to Domino, Select all, each what its
        key does in the list. Outside the boxes: they're dropped first (like a click), the items needing one greyed.
        The play line and the highlight stay."""
        self.tip.hide()
        if self.busy():
            return
        if not self.box_at(e.x, e.y):
            self.drop_boxes()
        has = "normal" if self.boxes else "disabled"
        on_cond = self.row_at(e.y) == CONDUCTOR_ROW  # (Paste there = the tempo, as with the row highlighted)
        menu = tk.Menu(self, tearoff=0)
        for item in ((tr("menu.cut"), tr("roll_menu.ctrl_x"), self.key_cut, has),
                     (tr("menu.copy"), tr("roll_menu.ctrl_c"), self.key_copy, has),
                     (tr("menu.paste"), tr("roll_menu.ctrl_v"), lambda: self.key_paste(on_cond), "normal"),
                     (tr("app.delete"), tr("roll_menu.del"), self.key_delete, has), None,
                     (tr("menu.domino_copy"), tr("menu.ctrl_shift_c"), self.app.copy_to_domino, has), None,
                     (tr("menu.select_all"), tr("menu.ctrl_a"), self.select_all, "normal")):
            if item is None:
                menu.add_separator()
            else:
                label, key, fn, state = item
                menu.add_command(label=label, accelerator=key, command=fn, state=state)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            self.menu_shut = time.perf_counter()  # (the click that closed it does nothing: on_press)

    # ------------------------------------------------------------ own tracks (added by hand: files/own_tracks.py)

    def track_menu(self, e, row):
        """Right-click on a row / below them (like Domino's): the row is highlighted (All tracks: none), then Add
        Track, Add Tracks…, Remove Track, Properties…. row: the track list row clicked (ALL_ROW / CONDUCTOR_ROW: new
        tracks before them all; the Conductor row and None = below the tracks: the highlight stays). The play line
        stays put."""
        self.tip.hide()
        s = int(self.tracks()[row]) if row is not None and row >= 0 else None
        line = self.line_at(e.y) if getattr(e, "y", None) is not None and s is not None else None
        if line is not None and int(self.lines()[1][line]) >= 0:  # (a folder's Ch row: the channels' menu)
            return self.ch_menu(e, row, int(self.lines()[1][line]))
        # (on one of several selected tracks: they stay selected and the menu works on them all, like Domino's;
        # else highlighted first, like a click; user 2026-10-26)
        if row not in (None, CONDUCTOR_ROW) and s not in self.picked_tracks():
            self.current, self.selected = s, set()
            self.shown_changed()
            self.update_idletasks()
        group = self.picked_tracks() if s in self.picked_tracks() else [s]
        gone = [t for t in group if t is not None and self.removable(t)]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("tracks.add_track"), command=lambda: self.add_own(row, [self.next_channel(row)]))
        menu.add_command(label=tr("tracks.add_tracks"), command=lambda: self.ask_add(row))
        menu.add_separator()
        # (PLAN #11 D: Split on a folder alone, Join on 2+ picked)
        menu.add_command(label=tr("tracks.split"), command=lambda: self.split_track(s),
                         state="normal" if len(group) == 1 and row is not None and row >= 0 and self.mixed(row)
                         else "disabled")
        why = self.join_why([t for t in group if t is not None])
        menu.add_command(label=why or tr("tracks.join"), command=lambda: self.join_tracks(group),
                         state="disabled" if why else "normal")
        menu.add_separator()
        menu.add_command(label=tr("tracks.remove_track"), command=lambda: self.remove_tracks(gone),
                         state="normal" if gone else "disabled")
        # (a folder: one channel for the track would put every channel's notes on it, user 2026-10-28)
        mixed = row is not None and row >= 0 and self.mixed(row)
        menu.add_command(label=tr("tracks.properties_mixed" if mixed else "tracks.properties"),
                         command=lambda: self.ask_properties(s),
                         state="normal" if self.has_properties(s) and len(group) == 1 and not mixed else "disabled")
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            self.menu_shut = time.perf_counter()  # (the click that closed it does nothing: on_press)

    def ch_menu(self, e, row, c):
        """Right-click on a folder's Ch row (hunt C, user 2026-10-28): picked like a click unless it's one of the Ch rows
        picked; Add Track / Add Tracks… (after its track), Remove channel(s) = the picked Ch rows of your tracks
        (remove_chans; a loaded MIDI's own channels move in stage D), Properties greyed (a channel isn't a track)."""
        pair = (int(self.tracks()[row]), c)
        if pair not in self.picked_chans():
            self.current, self.selected = pair[0], set()
            self.current_ch, self._sub_anchor = c, pair
            self.shown_changed()
            self.update_idletasks()
        pairs = [p for p in self.picked_chans() if p[0] >= HAND]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("tracks.add_track"), command=lambda: self.add_own(row, [self.next_channel(row)]))
        menu.add_command(label=tr("tracks.add_tracks"), command=lambda: self.ask_add(row))
        menu.add_separator()
        menu.add_command(label=trn("tracks.remove_chans", max(len(pairs), 1), n=len(pairs)),  # (yours only)
                         command=lambda: self.remove_chans(pairs), state="normal" if pairs else "disabled")
        menu.add_command(label=tr("tracks.properties"), state="disabled")
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            self.menu_shut = time.perf_counter()

    def remove_chans(self, pairs):
        """Remove channel(s): the picked Ch rows (pairs (your track, channel)) gone with your notes on them, like
        Remove Track for a track (remove_own): a shape wholly on them goes, one with other colours elsewhere keeps
        those as plain notes on their tracks; an empty Ch row just goes. The tracks stay. One undo step."""
        from window.app import colours_apart, placed_on
        from window.track_channels import forget_channels, laid_channels, tidy_tracks
        app = self.app
        want = {(s - HAND, c) for s, c in pairs if s >= HAND}
        if not want:
            return
        own, multi = app.own_channels(), app.channel_mode.get() == "auto"
        hit = {}
        for i, sh in enumerate(app.shapes):
            ids = placed_on(sh, own)
            if not ids:
                continue
            drop = [k for k, vc in enumerate(zip(ids, laid_channels(app, sh, ids, own))) if vc in want]
            if not multi and not colours_apart(sh, "single"):  # (Single channel / As drawn: every colour on the
                drop = list(range(len(ids))) if 0 in drop else []        # first one; a Domino paste keeps its tracks)
            if drop:
                hit[i] = (ids, drop)
        gone = {i for i, (ids, drop) in hit.items() if len(drop) == len(ids)}
        part = sorted(set(hit) - gone)

        def keep_rows(rows_of):  # (plain_copies' rows: the 5th column = each note's colour's place in its list)
            return {i: rows[~np.isin(np.minimum(rows[:, 4], len(hit[i][0]) - 1), hit[i][1])]
                    for i, rows in rows_of.items()}
        app.roll.end_typing()
        plain = app.plain_copies(part, together=keep_rows) if part else {}
        for i, sh in plain.items():  # (its colours left, in order: each on the track + channel it was on)
            ids, drop = hit[i]
            old = app.shapes[i]
            laid = laid_channels(app, old, ids, own)
            kept = [k for k in range(len(ids)) if k not in drop]
            sh["into"] = [ids[k] for k in kept]
            sh["into_ch"], sh["into_home"] = [laid[k] for k in kept], [ids[k] for k in kept]
        gone |= set(part) - set(plain)
        app.push_undo(name=trn("tracks.remove_chans_step", len(want), n=len(want)))
        app.put_plain(plain)
        for i in sorted(gone, reverse=True):
            del app.shapes[i]
        rest = [i - sum(j < i for j in gone) for i in sorted(app.sels - gone)]
        app.select_many(rest, max(rest, default=None))
        for s, c in pairs:
            forget_channels(app, s, [c])
        tidy_tracks(app)
        self.current_ch, self.sel_ch = None, set()
        self.own_changed()
        if gone or plain:
            said = ([trn("tracks.removed_ch_shapes", len(gone), n=len(gone))] if gone else []) + (
                [trn("tracks.removed_ch_plain", len(plain), n=len(plain))] if plain else [])
            app.status.config(text=" ".join(said))

    def own_row(self, row):
        """A track list row as own_tracks.add_tracks' row (-1 = before them all: the Conductor / All tracks row;
        None = after them all)."""
        if row is None:
            return None
        if row < 0:
            return -1
        own = self.app.own_tracks
        if len(self._hand) == len(own) and not self.app.waiting_rows():
            return row
        # (tinted tracks left out of the list, App.own_written: the row among all of them; a row waiting for its
        # MIDI, listed first: before them all)
        s = int(self._all[row])
        full = [r if type(r) is int else HAND + r["id"]
                for r in full_plan(self.app.file_rows(), self._slots, own, self._filled)]
        return full.index(s) if s in full else -1

    def row_channel(self, row):
        """The channel (0-15) of the track in row, None for none (the Conductor row, a loaded track without notes)."""
        if row is None or row < 0 or row >= len(self.tracks()):
            return None
        s = int(self.tracks()[row])
        if s < 0:
            return self.part_channel(s)
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
        drawn into the track take it; shapes_changed does the status line, title and autosave). The notes first:
        the old ones may belong to shapes Remove Track just deleted."""
        self.app.shapes_changed()
        self.tracks()
        self.shown_changed()

    def named(self, own=None, ids=None):
        """own (default: the app's) with the unnamed tracks of yours listed now named "Track n" by their place now
        (ids: only those). NAMES STICK (user 2026-10-28: place numbers shifted when tracks moved): done as a track is
        made, and before anything moves them (older projects, tinted tracks); written into the MIDI like a
        Properties name."""
        own = self.app.own_tracks if own is None else own
        rows = {int(s): k for k, s in enumerate(self.tracks())}
        return [dict(t, name=clean_name(tr("tracks.name", n=rows[HAND + t["id"]] + 1)))
                if not t["name"] and HAND + t["id"] in rows and (ids is None or t["id"] in ids) else t for t in own]

    def take_named(self, autos, owners, rows):
        """take_all with names that stick (user 2026-10-28): every unnamed track of yours named by its place first,
        then Spiderweb's automatic tracks made yours named by the place they had (taking them lists them last for a
        moment). In the undo step made already."""
        app = self.app
        app.own_tracks = self.named()
        was = {int(s): k for k, s in enumerate(self.tracks())}
        taken, rows = self.take_all(autos, owners, rows)
        names = {v: clean_name(tr("tracks.name", n=was[s] + 1)) for s, v in taken.items() if s in was}
        app.own_tracks = [dict(t, name=names[t["id"]]) if t["id"] in names and not t["name"] else t
                          for t in app.own_tracks]
        return taken, rows

    def name_new(self, ids):
        """The new tracks of yours ids named by where they show (named), in the undo step that made them."""
        got = self.named(ids=set(ids))
        if got != self.app.own_tracks:
            self.app.own_tracks = got
            self.tracks()
            self.request_redraw()

    def add_own(self, row, chs, port=0):
        """One track per channel in chs (all on port), after row (own_row), the first of them highlighted; named where
        they show (the others named first: none shifts)."""
        app = self.app
        app.push_undo(name=tr("tracks.add_step" if len(chs) == 1 else "tracks.add_many_step"))
        # (Spiderweb's tracks after it would shift: made yours when they can be; at the end nothing shifts)
        self.keep_places(None if row is None else max(row, -1) + 1)
        new, ids = add_tracks(self.named(), self._slots, self.own_row(row), chs, app.own_next, self._filled,
                              app.file_rows(), port)
        app.own_next = max(ids) + 1
        app.own_tracks = new
        self.own_changed()
        self.name_new(ids)
        self.current = HAND + ids[0]
        self.shown_changed()
        at = self.row_of_id(self.current)
        if at is not None:  # (in view)
            y = self.row_y(at)
            h = self.winfo_height() - self.area()[1]
            if y < self.top or y + self.row_h > self.top + h:
                self.top = y - h // 2
                self.clamp()
                self.request_redraw()

    def ask_add(self, row):
        """Add Tracks…: one track per channel ticked in its window, after row."""
        got = AddTracksBox(self, self.app.picture_use10).run()
        if got:
            self.add_own(row, *got)

    def has_properties(self, s):
        """Properties… works on it: yours, Spiderweb's (made yours first), a loaded MIDI's (loaded, not damaged)."""
        if s is None:
            return False
        if s >= 0:
            return True
        lm, n = self.app.loaded, -1 - s
        if self.file_part(s)[1] is not None:  # (a part / a track with parts: their channels are fixed, PLAN #11 D)
            return False
        return lm is not None and n < len(lm.mf.tracks) and lm.mf.tracks[n].whole

    def ask_properties(self, s):
        """Properties…: its name, channel and port in a small window. Yours: changed as it is; a loaded MIDI's: kept
        as the file's track's new name / channel / port (app.file_props, written again on export, group E);
        Spiderweb's: every automatic track made yours first (dragging's rule), then it's changed."""
        if not self.has_properties(s):
            return
        own = self.own_track(s)
        if own is not None:
            got = PropertiesBox(self, own["name"], own["ch"], own.get("port", 0)).run()
            if got is not None:
                self.set_properties(s, *got)
            return
        if s < 0:
            now = self.app.file_tracks().get(-1 - s, {"name": "", "ch": 0})
            got = PropertiesBox(self, now["name"], now["ch"], now.get("port", 0)).run()
            if got is not None:
                self.set_file_props(-1 - s, *got)
            return
        ch = slot_track_channel(s, self.app.picture_use10)[1]
        got = PropertiesBox(self, "", ch).run()
        if got is not None and got != ("", ch, 0):
            self.set_auto_properties(s, *got)

    def set_file_props(self, n, name, ch, port=None):
        """A loaded MIDI's track n given a new name / channel / port (the file's own = none kept): one undo step."""
        t = self.app.loaded.tracks[n]
        # (its own channel = nothing to change, unless its notes use several: OK puts them all on it, hunt 10-26)
        alone = np.count_nonzero(t.chn) <= 1
        had = self.app.file_props.get(n) or {}
        mine = {"name": None if name == (t.name or "") else name,
                "ch": None if alone and ch == (t.channel if t.channel is not None else 0) else ch}
        if port is not None and port != file_port(t):
            mine["port"] = port
        new = dict(self.app.file_props)
        extra = {k: v for k, v in had.items() if k in ("parts", "order")}  # (kept as they were)
        if mine["name"] is None and mine["ch"] is None and "port" not in mine and not extra:
            new.pop(n, None)
        else:
            new[n] = {**mine, **extra}
        if new == self.app.file_props:
            return
        self.app.roll.end_typing()
        self.app.push_undo(name=tr("tracks.properties_step"))
        self.app.file_props = new
        self.own_changed()

    def set_auto_properties(self, s, name, ch, port=0):
        """One of Spiderweb's tracks given a name / channel / port: every automatic track made yours (snapshot), then
        this one changed, one undo step."""
        app = self.app
        got = self.snapshot()
        if got is None:
            return
        full = full_plan(app.file_rows(), self._slots, app.own_tracks, self._filled)
        app.roll.end_typing()
        app.push_undo(name=tr("tracks.properties_step"))
        taken, rows = self.take_all(*got, full)
        app.own_tracks = [with_port(dict(t, name=name, ch=ch), port) if t["id"] == taken.get(s) else t
                          for t in from_rows(rows, self._slots, app.own_tracks, self._filled)]
        self.own_changed()
        if taken:
            app.status.config(text=trn("tracks.made_yours", len(taken), n=len(taken)))

    def set_properties(self, s, name, ch, port=0):
        own = self.own_track(s)
        if own is None or (own["name"], own["ch"], own.get("port", 0)) == (name, ch, port):
            return
        # (a track Spiderweb added for a shape's colours: changed by hand = yours, kept even when unused)
        mine = {k: v for k, v in own.items() if k != "auto"}
        self.set_own([with_port(dict(mine, name=name, ch=ch), port) if t is own else t for t in self.app.own_tracks],
                     tr("tracks.properties_step"))

    def on_double(self, e):
        """Double-click a track's name: its Properties (like Domino's). Any other second click = a click (Tk gives a
        double-click binding the press instead of <ButtonPress-1>)."""
        self.on_press(e)
        row = self.row_at(e.y)
        if row is not None and row >= 0 and e.x < self.name_w and not self.on_speaker(e.x) and e.x > FOLD_W * self.scale:
            if self.mixed(row):  # (a folder / its Ch rows: as the menu's greyed item says, hunt C)
                return self.app.status.config(text=tr("tracks.properties_mixed_why"))
            self.ask_properties(int(self.tracks()[row]))

    def removable(self, s):
        """Remove Track works on it (group E: every track but a loaded MIDI's holding the song's tempo / time / key
        signatures (the Conductor) or a damaged one: those are only ever copied as they are)."""
        if s >= 0:
            return True
        lm, n = self.app.loaded, -1 - s
        if lm is None:  # (waiting for its MIDI: shapes drawn into it)
            return True
        if self.file_part(s)[1] is not None:  # (a part / a track with parts: dragged back first, PLAN #11 D)
            return False
        return (n < len(lm.mf.tracks) and lm.mf.tracks[n].whole
                and n not in tempo_tracks(lm, (TEMPO, TIME_SIG, KEY_SIG)))

    def remove_tracks(self, ss):
        """Remove Track: yours alone = remove_own; with Spiderweb's / a loaded MIDI's = every note on them deleted
        (track_moves: shapes wholly on them go, the rest of others stays as plain notes, a loaded MIDI's = deleted
        notes) and the tracks with them, one undo step (Spiderweb's go once empty, a loaded MIDI's are left out of
        the list and the export: app.file_gone)."""
        if all(self.own_track(s) is not None for s in ss):
            return self.remove_own(ss)
        from window.track_moves import box_job
        rows = sorted(r for r in map(self.row_of_id, ss) if r is not None)
        if not rows:
            return
        boxes = [{"t0": 0.0, "t1": float(BEATS_FAR), "rows": (r, r)} for r in rows]
        own = {self.own_track(s)["id"] for s in ss if self.own_track(s) is not None}
        files = [-1 - s for s in ss if s < 0]
        box_job(self.app, boxes, "remove", extra={"ids": list(ss), "own": own, "files": files})

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
        # (Spiderweb's tracks after them would shift: made yours when they can be)
        self.keep_places(min((r for r in map(self.row_of_id, ss) if r is not None), default=None))
        app.put_plain(plain)
        for i in sorted(gone, reverse=True):
            del app.shapes[i]
        rest = [i - sum(j < i for j in gone) for i in sorted(app.sels - gone)]
        app.select_many(rest, max(rest, default=None))
        # (names stick: the ones after them keep the "Track n" they showed)
        app.own_tracks = [t for t in self.named() if t["id"] not in ids]
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
        if self.box_at(e.x, e.y):                                                       # many MIDI programs)
            # (a ruler band: drag what it holds sideways; a box: moved, the piano roll's)
            cursor = "sb_h_double_arrow" if e.y < self.ruler_h else "fleur"
        if getattr(self, "_cursor", None) != cursor:
            self._cursor = cursor
            self.config(cursor=cursor)
        line = self.line_at(e.y) if row is not None and row >= 0 else None
        c = int(self.lines()[1][line]) if line is not None else -1
        if c >= 0 and e.x < self.name_w:  # (a folder's sub-row)
            slot = int(self.tracks()[row])
            if self.on_speaker(e.x):
                off = slot in self.muted or (slot, c) in self.muted
                text = tr("tracks.unmute_ch_tip" if off else "tracks.mute_ch_tip")
            else:
                text = self.sub_tip(row, c)
        elif row is not None and row >= 0 and self.on_speaker(e.x):
            slot = int(self.tracks()[row])
            state = self.mute_state(slot)
            text = tr("tracks.unmute_tip" if state == 2 else "tracks.mute_all_tip" if state == 1 else "tracks.mute_tip")
        elif row is not None and row >= 0 and self.on_fold(e.x) and self.mixed(row):
            slot = int(self.tracks()[row])
            text = (tr("tracks.unfold_tip") if slot in self.open else
                    tr("tracks.fold_tip", n=len(self.row_chans()[row])))
        elif row == CONDUCTOR_ROW and e.x >= self.name_w and self.sx:
            text = self.bpm_tip(e.x)
        else:
            text = self.row_tip(row) if row is not None and e.x < self.name_w else ""
        if text != self.tip.text:
            self.tip.hide()
            self.tip.text = text
            if not e.state & self.tip.BUTTONS:  # (another row / spot: its tip may show again after a click or key;
                self.tip.used = False            # used stayed on till the mouse left the whole list)
            if text and not self.tip.used and not e.state & self.tip.BUTTONS:
                self.tip.schedule()

    def sub_tip(self, row, ch):
        """A folder's sub-row: its channel, track name and note count (the loaded MIDI's + Spiderweb's on it)."""
        slot, n = int(self.tracks()[row]), self.app.rendered
        count = 0
        if len(n):
            mine = self.note_rows() == row
            count += int((channels_of(n[mine, 4], self.app.picture_use10) == ch).sum())
        lm = self.app.loaded
        if slot < 0 and lm is not None and self.file_part(slot)[0] < len(lm.tracks):
            moved = lm.rechannel
            k = self.file_part(slot)[0]
            if moved is not None and k < len(moved) and moved[k] >= 0:
                count += lm.tracks[k].notes if moved[k] == ch else 0
            else:
                count += int(lm.tracks[k].chn[ch])
        text = tr("tracks.sub_tip", ch=ch + 1, name=self.name_of(row), notes=trn("n.notes", count, n=count))
        cc = self.cc_of(row, ch)
        if cc:  # (its CC events by name, user 2026-10-29)
            from notes.cc_names import kinds_text
            text += "\n" + tr("tracks.cc_line", kinds=kinds_text(cc))
        return text + "\n" + tr("tracks.new_notes_here", ch=ch + 1) if self.aimed_ch(row) == ch else text

    def cc_rows(self):
        """{(row, channel): {kind: how many}} of the CC events Spiderweb sends (App.shown_cc: pasted notes' and Hz
        basses' lines) and a loaded MIDI's own (its loading read's counts; Track Properties' channel, a part's
        channels), for the "CC" marks and tips (user 2026-10-29: never silent)."""
        got, lm = self.app.shown_cc(), self.app.loaded
        self.tracks()
        key = (got, self._all, lm)
        ver = (lm.hide_ver, id(lm.rechannel)) if lm is not None else None  # (Track Properties: hide_ver bumped)
        memo = getattr(self, "_cc_rows", None)
        if memo is not None and all(a is b for a, b in zip(memo[0], key)) and memo[1] == ver:
            return memo[2]
        from notes.cc_names import file_kind, kind_of
        out = {}
        if lm is not None:
            from files.midi_index import CC_KINDS
            moved = lm.rechannel
            for row in np.flatnonzero(self._all < 0).tolist():
                k, only = self.file_part(int(self._all[row]))
                if k >= len(lm.tracks):
                    continue
                to = int(moved[k]) if moved is not None and k < len(moved) and moved[k] >= 0 else None
                for i, n in lm.tracks[k].c_kinds.tolist():
                    ch = i // CC_KINDS
                    if only is not None and ch not in only:
                        continue
                    counts = out.setdefault((row, ch if to is None else to), {})
                    kind = file_kind(i % CC_KINDS)
                    counts[kind] = counts.get(kind, 0) + n
        slots = [s for s, ms in got.items() if ms]
        if slots:
            ids = track_ids(slots).tolist()
            chs = channels_of(slots, self.app.picture_use10).tolist()
            for s, lid, c in zip(slots, ids, chs):
                row = self.row_of_id(int(lid))
                if row is None:
                    continue
                counts = out.setdefault((row, int(c)), {})
                for m in got[s]:
                    k = kind_of(m[1], m[2]) if m[1] is not None else None
                    if k is not None:
                        counts[k] = counts.get(k, 0) + 1
        out = {k: v for k, v in out.items() if v}
        self._cc_rows = (key, ver, out)
        return out

    def cc_of(self, row, ch=None):
        """{kind: how many} of row's CC events (ch: that channel's only)."""
        got = {}
        for (r, c), counts in self.cc_rows().items():
            if r == row and (ch is None or c == ch):
                for k, n in counts.items():
                    got[k] = got.get(k, 0) + n
        return got

    def cc_tip_line(self, row):
        """[the tip's line naming row's CC events], or [] with none."""
        cc = self.cc_of(row)
        if not cc:
            return []
        from notes.cc_names import kinds_text
        return [tr("tracks.cc_line", kinds=kinds_text(cc))]

    def draw_cc_mark(self, x, mid, font, picked):
        """The small "CC" word ending at x (right-aligned): CC events ride on this row. Returns its width."""
        text = tr("cc.mark")
        self.create_text(x, mid, text=text, anchor="e", font=font,
                         fill=look.TRACK_PICK_TEXT if picked else look.CC_MARK)
        return self.measurer(font)(text)

    def aimed_ch(self, row):
        """The channel shapes drawn / pasted now go to in row's folder when its TRACK row is highlighted (its first
        channel: App.first_ch, user 2026-10-29), else None."""
        if self.current is None or self.current_ch is not None or row >= len(self._all):
            return None
        chans = self.row_chans()
        return chans[row][0] if int(self._all[row]) == self.current and len(chans[row]) > 1 else None

    def row_tip(self, row):
        """A row's whole name, channel, note count and the shapes whose notes are on it."""
        if row < 0:
            return tr("tracks.all_tip" if row == ALL_ROW else "tracks.conductor_tip")
        key = (row, self._made and id(self._made[0]), len(self.app.shapes), id(self.app.own_tracks), id(self._all))
        if self._tip_memo[0] == key:  # (worked out once per row: every mouse move took 60 ms on huge tracks)
            return self._tip_memo[1]
        text = self.row_tip_text(row)
        self._tip_memo = (key, text)
        return text

    def row_tip_text(self, row):
        slot = int(self.tracks()[row])
        n = self.app.rendered
        mine = n[self.note_rows() == row] if len(n) else n
        if slot < 0:  # (a loaded MIDI's track; shapes drawn into it, group D)
            lines = ["  ·  ".join(x for x in (self.name_of(row), self.channel_of(slot, long=True)) if x)]
            lm = self.app.loaded
            if lm is not None:
                k, chs = self.file_part(slot)  # (a part / a track with parts: its channels' notes, PLAN #11 D)
                t = lm.tracks[k]
                count = t.notes if chs is None else int(sum(int(t.chn[c]) for c in chs))
                lines.append(trn("tracks.notes", count, n=f"{count:,}"))
                used = int((t.chn > 0).sum()) if chs is None else len(self.row_chans()[row])
                if used > 1:
                    lines.append(trn("tracks.channels", used, n=used))
                lines.append(tr("tracks.from_file"))
                chans = self.row_chans()[row]
                if len(chans) > 1:  # (a folder: where its track row's drawing / pasting goes, user 2026-10-29)
                    lines.append(tr("tracks.row_goes_to", ch=chans[0] + 1))
                lines += self.cc_tip_line(row)
            else:
                lines.append(tr("tracks.waiting_midi"))
            if len(mine):
                lines.append(trn("tracks.yours_inside", len(mine), n=f"{len(mine):,}"))
                owners = np.unique(mine[:, 5]).tolist()
                names = [self.app.shape_label(self.app.shapes[i]) for i in owners[:TIP_SHAPES]
                         if i < len(self.app.shapes)]
                lines.append(tr("tracks.shapes", names=", ".join(names)))
                if len(owners) > TIP_SHAPES:
                    lines.append(trn("tracks.more_shapes", len(owners) - TIP_SHAPES, n=len(owners) - TIP_SHAPES))
            return "\n".join(lines)
        lines = [f"{self.name_of(row)}  ·  {self.channel_of(slot, long=True)}",
                 trn("tracks.notes", len(mine), n=f"{len(mine):,}")]
        chans = self.row_chans()[row]
        if len(chans) > 1:  # (a folder: where its track row's drawing / pasting goes, user 2026-10-29)
            lines.append(tr("tracks.row_goes_to", ch=chans[0] + 1))
        lines += self.cc_tip_line(row)
        if slot >= HAND:
            lines.insert(1, tr("tracks.added_tip" if self.own_track(slot).get("auto") else "tracks.own_tip"))
        if self.app.channel_mode.get() != "auto":  # (laid shapes' other colours: on this track until Multi channel)
            from window.app import placed_on
            entry, chans, more = slot - HAND if slot >= HAND else -1 - slot, self.app.own_channels(), set()
            for sh in self.app.shapes:
                laid = placed_on(sh, chans)
                if laid and laid[0] == entry and not pasted_notes(sh):  # (a Domino paste: on its own tracks)
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

    @stale_ok  # (notes being made in the background: the ones on screen, notes_wait.py)
    def redraw(self):
        self._job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2 or not self.winfo_ismapped():
            return
        if self.sx is None:
            self.fit()
        self.tracks()
        self.clamp()
        # (each part's items kept when they come out the same: redraw 2026-10-31, every click remade all ~300)
        self._drawn, self._moved = set(), False
        lines = self.shown_bars(w)
        with self.layer("pic"):
            self.paint(w, h)
        with self.layer("ruler"):
            self.draw_ruler(w, lines)
        with self.layer("allrow"):
            self.draw_all(w)
        with self.layer("cond"):
            self.draw_conductor(w)
        with self.layer("boxes"):  # (over the Conductor row too: boxes on it, user 11-05; never above it)
            self.draw_boxes(w, h)
        self.draw_names(h)
        m = self._move
        thick = max(2, round(2 * self.scale))
        with self.layer("move"):
            if m is not None and m["on"] and m["gap"] is not None:  # (where dragged tracks land, Domino's line)
                y = self.area()[1] + self.row_y(m["gap"]) - self.top
                self.create_line(0, y, w, y, fill=look.TRACK_TEXT, width=thick)
            elif m is not None and m["on"] and m["into"] is not None:  # (INTO a track, PLAN #11 C2)
                row, pos = m["into"]
                y = self.area()[1] + self.row_y(row) - self.top
                if pos is None:  # (the row rested on: lit all along)
                    self.create_rectangle(thick // 2, y + thick // 2, w - thick // 2, y + self.row_h - thick // 2,
                                          outline=look.TRACK_TEXT, width=thick)
                else:  # (between an open folder's Ch rows: short and indented, like them; decision 7)
                    y += (pos + 1) * self.row_h - round(INTO_UP * self.scale)
                    self.create_line(round(INDENT * 2 * self.scale), y, w, y, fill=look.TRACK_TEXT, width=thick)
        for name in [n for n in self._layers if n not in self._drawn]:  # (rows scrolled away...)
            self.delete(name)
            del self._layers[name]
        if self._moved:  # (a part drawn again lands on top: every part back in drawing order)
            for tag in LAYER_ORDER:
                self.tag_raise(tag)
        self.draw_playhead()
        for bar in self.bars:
            bar.refresh()

    def _create(self, kind, args, kw):
        if self._rec is None:
            return super()._create(kind, args, kw)
        self._rec.append((kind, args, kw))
        return 0

    @contextlib.contextmanager
    def layer(self, name, group=None):
        """One part of redraw: the items made inside are only written down; the same as last time = the canvas keeps
        its items, else they're replaced (tagged name and group: LAYER_ORDER)."""
        self._rec = rec = []
        try:
            yield
        finally:
            self._rec = None
        self._drawn.add(name)
        if self._layers.get(name) == rec:
            return
        self.delete(name)
        tags = (name, group or name)
        for kind, args, kw in rec:
            super()._create(kind, args, dict(kw, tags=tags))
        self._layers[name] = rec
        self._moved = True

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
        """The rows (stripes, the highlighted one, bar lines) and every track's notes, in SQUARE x SQUARE squares
        kept while nothing in them changes: scrolling only paints the squares coming into view (user 2026-10-28:
        scrolling fast lagged; one picture twice the view's size was painted again at each of its edges)."""
        x0, y0 = self.area()
        aw, ah = w - x0, h - y0
        if aw < 1 or ah < 1:
            return
        ox, top = self.left_px(), max(0, self.top)
        # (the highlight and picks aren't in the key: a click only recolours their rows, remark; user 2026-10-31:
        # every click painted every note again)
        # (nor the mutes: a speaker click paints again only the squares of the rows it greyed, mute_squares; redraw
        # hunt 2026-10-31: every square was painted again, ~0.2 s a click)
        key = (self.sx, self.row_h, id(self._made[0]) if self._made else None, id(self._all),
               frozenset(self.open), json.dumps(sigs_of(self.app.tempo)),
               self.app.ppq, self.app.keys,
               # (a loaded MIDI's notes hidden / back, read on a thread: shown at once, hunt 2026-10-26)
               self.app.loaded.hide_ver if self.app.loaded is not None else None)
        (xs, xw), (ys, yw) = squares(ox, aw), squares(top, ah)
        marks = self.marked_lines()
        st = self._sheet
        if st is not None and st["key"] == key:
            if st.get("muted") != self.muted:
                self.mute_squares(st, st.get("muted") or set())
            if st["marks"] != marks:
                self.remark(st, marks)
        got = self.tiled("_sheet", key, xw, yw, SQUARE, self.sheet_with_bg)
        self._sheet["marks"] = marks
        self._sheet["muted"] = set(self.muted)
        for j in ys:
            for i in xs:
                self.create_image(x0 + i * SQUARE - ox, y0 + j * SQUARE - self.top, image=got[(i, j)].photo,
                                  anchor="nw")

    def tiled(self, name, key, xs, ys, th, paint):
        """The kept squares of picture name (self.<name> = {"key", "tiles", "pix", "marks"}; None = all painted
        again): SQUARE wide, th high, columns xs x rows ys. The missing ones painted together in one go (paint(cx, cy,
        cw, ch) -> image, or (image, background mask): then each square's pixels kept in "pix" for remark), all of
        them again when key changes. Returns {(column, row): Photo}."""
        st = getattr(self, name)
        if st is None or st["key"] != key:
            st = {"key": key, "tiles": OrderedDict(), "pix": {}, "marks": None}
            setattr(self, name, st)
        kept, pix = st["tiles"], st["pix"]
        want = [(i, j) for j in ys for i in xs]
        miss = [p for p in want if p not in kept]
        if miss:
            i0, i1 = min(p[0] for p in miss), max(p[0] for p in miss)
            j0, j1 = min(p[1] for p in miss), max(p[1] for p in miss)
            img = paint(i0 * SQUARE, j0 * th, (i1 - i0 + 1) * SQUARE, (j1 - j0 + 1) * th)
            img, bg = img if isinstance(img, tuple) else (img, None)
            for i, j in miss:
                cut = (slice((j - j0) * th, (j - j0 + 1) * th), slice((i - i0) * SQUARE, (i - i0 + 1) * SQUARE))
                part = np.ascontiguousarray(img[cut])
                photo = Photo(self, SQUARE, th)
                photo.put(part)
                kept[(i, j)] = photo
                if bg is not None:
                    pix[(i, j)] = (part, bg[cut].copy())
        for p in want:
            kept.move_to_end(p)
        while len(kept) > max(SQUARES_KEPT, 2 * len(want)):  # (the longest unseen go)
            pix.pop(kept.popitem(last=False)[0], None)
        return kept

    def marked_lines(self):
        """{line: kind} of the rows painted apart (paint_sheet's base colours): picked ones 4, the highlighted 2."""
        out = {}
        for s in self.selected:  # (Ctrl+clicked along with the highlighted one: fainter)
            got = self.line_of(s)
            if got is not None:
                out[got] = 4
        for s, c in self.sel_ch:  # (a folder's sub-rows picked with it)
            got = self.line_of(s, c)
            if got is not None:
                out[got] = 4
        at = (self.line_of(self.current, self.current_ch) if self.current is not None and not self.conductor_on
              else None)  # (the Conductor row highlighted: no track is)
        if at is not None:
            out[at] = 2
        return out

    @staticmethod
    def row_colours():
        """paint_sheet's row colours by kind: plain, plain (every other), highlighted, past the rows, picked."""
        return np.array([hex_rgb(look.TRACK_ROW), hex_rgb(look.TRACK_ROW_ALT), hex_rgb(look.TRACK_PICKED),
                         hex_rgb(look.TRACK_EMPTY), hex_rgb(look.TRACK_SELECTED)], np.uint8)

    def mute_squares(self, st, old):
        """The mutes changed from old: the kept squares of the lines whose track's mutes changed forgotten (tiled
        paints them again, greyed / coloured)."""
        def of(m, s):
            return {x for x in m if x == s or (isinstance(x, tuple) and x[0] == s)}
        changed = {x if not isinstance(x, tuple) else x[0] for x in old ^ self.muted}
        changed = {s for s in changed if of(old, s) != of(self.muted, s)}
        rows = self.lines()[0]
        slots = self._all
        lines = [line for line in range(len(rows)) if int(slots[int(rows[line])]) in changed]
        self.forget_squares(st, lines)

    def forget_squares(self, st, lines):
        """The kept squares (+ their kept pixels) holding any of lines dropped: painted again when shown."""
        rh, js = self.row_h, set()
        for line in lines:
            js |= set(range(line * rh // SQUARE, ((line + 1) * rh - 1) // SQUARE + 1))
        for p in [p for p in st["tiles"] if p[1] in js]:
            del st["tiles"][p]
            st.get("pix", {}).pop(p, None)

    def remark(self, st, marks):
        """The highlight / picks changed, nothing else: the kept squares' background in the rows whose kind changed
        recoloured (notes, lines and bars stay), only those squares put again."""
        old = st["marks"] or {}
        changed = {line for line in set(old) | set(marks) if old.get(line) != marks.get(line)}
        rh, base, count = self.row_h, self.row_colours(), len(self.lines()[0])
        for (i, j), photo in st["tiles"].items():
            got = st["pix"].get((i, j))
            if got is None:
                continue
            img, bg = got
            y0 = j * SQUARE
            hit = [line for line in range(y0 // rh, (y0 + SQUARE - 1) // rh + 1) if line in changed]
            for line in hit:
                a, b = max(line * rh - y0, 0), min((line + 1) * rh - y0, SQUARE)
                kind = marks.get(line, line % 2 if line < count else 3)
                img[a:b][bg[a:b]] = base[kind]
            if hit:
                photo.put(img)
        st["marks"] = marks

    def paint_sheet(self, cx, cy, cw, ch):
        """paint's picture: cw x ch pixels from pixel cx of the song (sideways) and cy of the rows (down)."""
        rh = self.row_h
        count = len(self.lines()[0])
        rows = (np.arange(ch) + cy) // rh  # (each pixel row's line: a track's own or a folder's sub-row)
        base = self.row_colours()
        kind = np.where(rows >= count, 3, rows % 2)
        for line, k in self.marked_lines().items():
            kind[rows == line] = k
        img = np.empty((ch, cw, 3), np.uint8)
        img[:] = base[kind][:, None, :]
        edge = ((np.arange(ch) + cy) % rh == rh - 1) & (rows < count)  # (a faint line under each line)
        img[edge] = hex_rgb(look.TRACK_LINE)
        sigs = sigs_of(self.app.tempo)
        xs = np.array([round(b * self.sx) - cx for b, _ in bar_lines(sigs, cx / self.sx, (cx + cw) / self.sx,
                                                                     COLUMN * self.scale / self.sx)], np.int64)
        xs = xs[(xs >= 0) & (xs < cw)]
        img[:, xs] = hex_rgb(look.TRACK_BAR)
        img[rows >= count] = base[3]
        self.paint_loaded_rows(img, rows, cx, cy)
        self.paint_notes(img, rows, cx, cy)
        self._kind = (base, kind, rows < count)
        return img

    def sheet_with_bg(self, cx, cy, cw, ch):
        """paint_sheet's picture + the mask of its pixels showing their row's background (remark recolours those)."""
        img = self.paint_sheet(cx, cy, cw, ch)
        base, kind, listed = self._kind
        return img, (img == base[kind][:, None, :]).all(axis=2) & listed[:, None]

    def paint_notes(self, img, rows, cx, cy):
        """Each track's notes as lines in its row (the project's whole key range over the row's height, like
        Domino's, user 2026-10-26: a key sits at the same height in every row), in its channel's colour; a note shorter than a pixel still shows one. img starts at pixel cx of the song, cy of
        the rows."""
        n, ppq = self.app.rendered, self.app.ppq
        ah, aw = img.shape[:2]
        if not len(n):
            return
        first_line, last_line = cy // self.row_h, (cy + ah) // self.row_h
        lo_t, hi_t = cx / self.sx * ppq, (cx + aw) / self.sx * ppq
        row = self.note_rows()
        _, _, first, keys, sub = self.lines()
        # (only the notes of the rows these lines show: scrolling a 4,000-track list read every note for each new
        # strip, user 2026-11-07; the same notes reach the painting below, so the picture is the same)
        order, by_row = self.notes_by_row(row)
        r_lo = max(0, int(np.searchsorted(first, first_line, "right")) - 1)
        r_hi = int(np.searchsorted(first, last_line, "right")) - 1
        part = order[np.searchsorted(by_row, r_lo, "left"):np.searchsorted(by_row, r_hi, "right")]
        n, row = n[part], row[part]
        keep = (row >= 0) & (n[:, 1] > lo_t) & (n[:, 0] < hi_t)
        if not keep.any():
            return
        n, row = n[keep], row[keep]
        ch = channels_of(n[:, 4], self.app.picture_use10)
        line = first[row]
        if len(keys):  # (an open folder: each note on its channel's sub-row too)
            k = row * 16 + ch
            at = np.minimum(np.searchsorted(keys, k), len(keys) - 1)
            there = keys[at] == k
            n, row, ch = np.concatenate([n, n[there]]), np.concatenate([row, row[there]]), np.concatenate([ch,
                                                                                                         ch[there]])
            line = np.concatenate([line, sub[at[there]]])
        keep = (line >= first_line) & (line <= last_line)
        if not keep.any():
            return
        n, row, ch, line = n[keep], row[keep], ch[keep], line[keep]
        key = np.clip(n[:, 2], 0, self.app.keys - 1)
        hi = self.app.keys - 1
        inner = max(1, self.row_h - 2 * round(PAD * self.scale) - 1)
        span = self.app.keys
        thick = max(1, inner // span)
        used = min(inner, span * thick)
        y = line * self.row_h - cy + round(PAD * self.scale) + (inner - used) // 2 + (hi - key) * used // span
        xa = np.floor(n[:, 0] / ppq * self.sx).astype(np.int64) - cx
        xb = np.ceil(n[:, 1] / ppq * self.sx).astype(np.int64) - 1 - cx
        xa, xb = np.clip(xa, 0, aw - 1), np.clip(np.maximum(xa, xb), 0, aw - 1)
        # each note in its channel's colour (user 2026-10-28: a mixed track's channels as on the piano roll); a
        # track muted / one of its channels: grey
        pairs, back = np.unique(row * 16 + ch, return_inverse=True)
        colours, ids = [], {}
        cid = np.empty(len(pairs), np.int64)
        for i, p in enumerate(pairs.tolist()):
            c = self.note_colour(int(self._all[p >> 4]), p & 15)
            cid[i] = ids.setdefault(c, len(ids))
            if cid[i] == len(colours):
                colours.append(c)
        cid = cid[back.ravel()]
        # each note's top pixel row painted once, in a picture `pad` rows taller on top (a thick line starting above
        # the picture still reaches into it); thick lines are then grown downwards row by row (hunt 2026-10-23: a
        # copy of every note per pixel of thickness took seconds and GBs for big one-key tracks)
        pad = thick - 1
        hh = ah + pad
        y = y + pad
        on = (y >= 0) & (y < hh)
        y, xa, xb, cid = y[on], xa[on], xb[on], cid[on]
        if pad:  # covered if a line starts at most its thickness - 1 rows up, within the same line
            at = np.arange(hh) - pad + cy  # (each picture row's pixel in the rows)
            track_row = at // self.row_h
            tk = np.ones(hh, np.int64)
            inside = (track_row >= 0) & (track_row < len(self.lines()[0]))
            tk[inside] = thick
            start = np.maximum(np.arange(hh) - tk + 1, track_row * self.row_h - cy + pad)  # (first row that counts)
        for c in range(len(colours)):  # (one colour at a time: a later one over an earlier one)
            mine = cid == c
            ym = y[mine]
            if not len(ym):
                continue
            # (only the pixel rows its lines reach: a colour is mostly one track's, user 2026-10-31: every colour
            # over the whole picture made each repaint slow)
            if not pad:  # (thin lines: only the pixel rows it uses, not every row between its top and bottom; a
                # colour is spread over many tracks in a 4,000-track list, user 2026-11-07)
                uy, yi = np.unique(ym, return_inverse=True)
                size = len(uy) * (aw + 1)
                cover = np.bincount(yi * (aw + 1) + xa[mine], minlength=size).astype(np.int32)
                cover -= np.bincount(yi * (aw + 1) + xb[mine] + 1, minlength=size).astype(np.int32)
                hit = np.cumsum(cover.reshape(len(uy), aw + 1), axis=1, dtype=np.int32)[:, :aw] > 0
                part = img[uy]
                part[hit] = hex_rgb(colours[c])
                img[uy] = part
                continue
            ya, yb = int(ym.min()), min(hh, int(ym.max()) + 1 + pad)
            size = (yb - ya) * (aw + 1)
            # (+1 where a line starts, -1 just past its end, added up along each pixel row: covered where > 0)
            cover = np.bincount((ym - ya) * (aw + 1) + xa[mine], minlength=size).astype(np.int32)
            cover -= np.bincount((ym - ya) * (aw + 1) + xb[mine] + 1, minlength=size).astype(np.int32)
            hit = np.cumsum(cover.reshape(yb - ya, aw + 1), axis=1, dtype=np.int32)[:, :aw] > 0
            if pad:
                ups = np.cumsum(hit, axis=0, dtype=np.int32)
                back = start[ya:yb] - 1 - ya  # (rows above ya hold none of its lines: counted 0 there)
                below = np.where(back[:, None] >= 0, ups[np.maximum(back, 0)], 0)
                hit = (ups - below) > 0
            lo = max(ya, pad)
            img[lo - pad:yb - pad][hit[lo - ya:]] = hex_rgb(colours[c])

    def notes_by_row(self, row):
        """(the notes' order by row, kept in their own order within a row; the rows in that order) for note_rows'
        array row: worked out once per array (a new one comes with new notes / tracks)."""
        got = getattr(self, "_by_row", None)
        if got is None or got[0] is not row:
            small = row.astype(np.int16) if len(row) and int(row.max()) < 32767 else row  # (16-bit: radix sort)
            order = np.argsort(small, kind="stable")
            got = self._by_row = (row, order, row[order])
        return got[1], got[2]

    def chan_colour(self, ch):
        """Channel ch's notes' colour (colour_of's look)."""
        fill, edge = SLOT_COLORS[COLOURS_16[ch]]
        return fill if look.DARK else edge

    def note_colour(self, s, ch):
        """The colour of track s's notes on channel ch in the list: muted grey, else the channel's (Spiderweb's
        automatic tracks: colour_of, one channel each)."""
        if s in self.muted or (s, ch) in self.muted:
            return look.TRACK_MUTED
        return self.colour_of(s) if 0 <= s < HAND else self.chan_colour(ch)

    def paint_loaded_rows(self, img, rows, cx, cy):
        """A loaded MIDI's tracks' lines, laid out like paint_notes' (the project's whole key range over the row's
        height), each note in its channel's colour. EXACT (user 2026-10-24: the small pictures looked blocky): each
        line read from the file like the piano roll's exact pictures (roll_loaded.exact_tab, this track alone; an
        open folder's sub-row: that channel alone, user 2026-10-28) in TILE-wide blocks along the song, kept while
        the zoom stays (closing a folder keeps its sub-rows' too): at once while that's quick (ROWS_INLINE_S), the
        rest on a background thread that then reads the blocks around the picture ahead (the sheet painted again
        once the shown ones come); meanwhile the loading pass's small picture (keys x up to loaded.PIC_COLS columns;
        a sub-row: nothing, the small picture holds every channel)."""
        lm = self.app.loaded
        if lm is None or not len(self._file_row):
            return
        ah, aw = img.shape[:2]
        line_row, line_ch, _, _, _ = self.lines()
        first = max(0, cy // self.row_h)
        last = min(len(line_row) - 1, (cy + ah) // self.row_h)

        def files_in(a, b):  # (the loaded MIDI's tracks in rows a..b-1 with notes: [(row, track number)])
            return [(row, self.file_at(row)) for row in range(max(0, a), min(b, len(self._all)))
                    if self.file_at(row) is not None and lm.tracks[self.file_at(row)].notes]
        # (the lines shown: (line, track number, channel or None = the whole track))
        shown = []
        for line in range(first, last + 1):
            r = self.file_at(int(line_row[line]))
            if r is not None and lm.tracks[r].notes:
                shown.append((line, r, int(line_ch[line]) if line_ch[line] >= 0 else self.row_only(int(line_row[line]))))
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
        self._shown_tiles = {(r, j, c) for _, r, c in shown for j in range(j0, min(j1 + 1, end_j))}
        rate = RL.rate_of(lm)
        until = time.perf_counter() + (ROWS_INLINE_S if RL.speed.loops() else 0)
        waiting = []
        span = self.app.keys  # (the project's whole key range, like Domino: keys keep their height in every row)
        channel_of = np.argsort(COLOURS_16)  # (a colour group's channel)
        for line, r, c in shown:  # (r: the track's number in the file; blocks kept by it + the channel)
            t = lm.tracks[r]
            parts = []
            for j in range(j0, j1 + 1):
                if j >= end_j:
                    parts.append(np.full((t.khi - t.klo + 1, TILE), -1, np.int8))
                    continue
                got = self._tiles.get((r, j, c))
                if got is None:
                    if time.perf_counter() + RL.view_bytes(lm, (r,), ax, -float(j * TILE), TILE) / rate <= until:
                        got = row_mask(lm, r, ax, -float(j * TILE), TILE, c)
                        self.keep_tile((r, j, c), got)
                    else:
                        waiting.append((r, j, c))
                else:
                    self._tiles.move_to_end((r, j, c))
                parts.append(got)
            if all(isinstance(p, np.ndarray) for p in parts):
                got = np.concatenate(parts, axis=1)[:, cx - j0 * TILE:cx - j0 * TILE + aw]
            else:  # (blocks not read yet / unreadable (False): the small picture there, a sub-row nothing)
                small = small_mask(lm, t, cx, aw, self.sx) if c is None else None
                if c is not None:  # (a Ch row / part still being read: grey there meanwhile, user 2026-10-28)
                    band = img[max(0, line * self.row_h - cy + pad):max(0, line * self.row_h - cy + pad + inner)]
                    for j, p in enumerate(parts, j0):
                        if p is None:
                            a, b = max(cx, j * TILE), min(cx + aw, (j + 1) * TILE)
                            band[:, a - cx:b - cx] = hex_rgb(look.TRACK_LOADING)
                if small is None:
                    if not any(isinstance(p, np.ndarray) for p in parts):
                        continue
                    got = np.full((t.khi - t.klo + 1, aw), -1, np.int8)
                else:
                    got = np.where(small, COLOURS_16[RL.track_channel(lm, r)], -1).astype(np.int8)
                for j, p in enumerate(parts, j0):
                    if isinstance(p, np.ndarray):
                        a, b = max(cx, j * TILE), min(cx + aw, (j + 1) * TILE)
                        got[:, a - cx:b - cx] = p[:, a - j * TILE:b - j * TILE]
            full = np.full((span, aw), -1, np.int8)
            k_lo, k_hi = max(t.klo, 0), min(t.khi, span - 1)
            if k_hi >= k_lo:
                full[k_lo:k_hi + 1] = got[k_lo - t.klo:k_hi - t.klo + 1]
            got = full
            thick = max(1, inner // span)
            used = min(inner, span * thick)
            y0 = line * self.row_h - cy + pad + (inner - used) // 2  # (the row's top key's first pixel row)
            ys = np.arange(max(0, y0), min(ah, y0 + used))
            if not len(ys):
                continue
            # (each pixel row = the keys it covers, top key first: one of them sounding, its channel's colour)
            top = got[::-1][:-(-(ys[-1] + 1 - y0) * span // used)]  # (keys up to the last pixel row's, rounded up)
            k0 = (ys - y0) * span // used
            group = np.maximum.reduceat(top, k0, axis=0) if span > used else top[k0]
            on = group >= 0
            lut = np.array([hex_rgb(self.note_colour(-1 - r, int(channel_of[g]))) for g in range(16)], np.uint8)
            img[ys[0]:ys[-1] + 1][on] = lut[group[on]]
        # (the All tracks row: a track with notes taken out (moved, deleted, extracted) read exactly too, rows off
        # screen as well: the loading pass's small picture still has them)
        for r in self.cut_tracks(lm):
            for j in range(j0, min(j1 + 1, end_j)):
                self._shown_tiles.add((r, j, None))
                if (r, j, None) in self._tiles:
                    self._tiles.move_to_end((r, j, None))
                elif time.perf_counter() + RL.view_bytes(lm, (r,), ax, -float(j * TILE), TILE) / rate <= until:
                    self.keep_tile((r, j, None), row_mask(lm, r, ax, -float(j * TILE), TILE))
                else:
                    waiting.append((r, j, None))
        if RL.changed(self, lm):  # (the file changed / went: said once, the small pictures stay)
            return
        # the background reading: the blocks shown blocky first, then the ones around the picture (half its width
        # each way along the song, half its height up and down) so scrolling finds them read
        # (a square painted ahead below the last line: no rows of its own)
        rows_shown = sorted({int(line_row[line]) for line, _, _ in shown}) or (
            [int(line_row[first])] if first < len(line_row) else [])
        ahead = [(r, j, c) for _, r, c in shown
                 for j in range(max(0, (cx - aw // 2) // TILE), min((cx + aw + aw // 2) // TILE + 1, end_j))]
        if rows_shown:
            a, b = rows_shown[0], rows_shown[-1]
            half = (b - a) // 2
            ahead += [(r, j, None) for _, r in files_in(a - half, a) + files_in(b + 1, b + half + 1)
                      for j in range(j0, min(j1 + 1, end_j))]
        todo = list(dict.fromkeys(waiting + [k for k in ahead if k not in self._tiles]))
        job = self._row_job
        if todo and (job is None or waiting):  # (blocks shown blocky: read those first, now)
            self.stop_rows()
            job = self._row_job = RowJob(lm, place, todo, ax, set(waiting))
            threading.Thread(target=job.run, daemon=True, name="track rows").start()
            self.after(RL.POLL_MS, self._poll_rows)

    def cut_tracks(self, lm):
        """The loaded MIDI's listed tracks with notes hidden (an Extract's, deleted ones)."""
        fr = self._file_row
        return [r for r in np.unique(lm.h_trk).tolist() if r < len(fr) and fr[r] >= 0 and lm.tracks[r].notes]

    def tile_mask(self, r, cx, cw):
        """Loaded track r's exact keys x cw pixels from pixel cx of the song, from its read blocks; None while one
        isn't read (or can't be)."""
        lm, parts = self.app.loaded, []
        end_j = int(lm.end * self.sx / lm.ppq) // TILE + 1  # (paint_loaded_rows': past the song = empty)
        for j in range(cx // TILE, (cx + cw - 1) // TILE + 1):
            got = self._tiles.get((r, j, None))
            if j >= end_j:
                got = np.full((lm.tracks[r].khi - lm.tracks[r].klo + 1, TILE), -1, np.int8)
            if not isinstance(got, np.ndarray):
                return None
            parts.append(got)
        return np.concatenate(parts, axis=1)[:, cx - cx // TILE * TILE:][:, :cw] >= 0

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

    def repaint_read(self, done):
        """Rows just read from the file (done = {(track number, channel / channels / None)}): only the squares of
        their lines painted again (user 2026-10-28: the whole list repainted at once flashed); the All tracks row
        too when a whole track came."""
        if any(c is None for _, c in done):
            self._all_sheet = None
        st = self._sheet
        if st is None:
            return
        rows, chs = self.lines()[:2]
        lines = []
        for line in range(len(rows)):
            row = int(rows[line])
            r = self.file_at(row)
            if r is None:
                continue
            c = int(chs[line]) if chs[line] >= 0 else self.row_only(row)
            if (r, c) in done:
                lines.append(line)
        self.forget_squares(st, lines)  # (their kept pixels too, hunt 2026-10-31)

    def _poll_rows(self):
        job = self._row_job
        if job is None:
            return
        self.take_rows()
        if job.shown:  # (a row's blocky / grey blocks all read: that row painted again, the reading goes on)
            groups = {}
            for k in job.shown:
                groups.setdefault((k[0], k[2]), []).append(k)
            done = {g for g, ks in groups.items() if all(k in self._tiles for k in ks)}
            if done:
                job.shown = {k for k in job.shown if (k[0], k[2]) not in done}
                self.repaint_read(done)
                self.request_redraw()
        if not job.done:
            self.after(RL.POLL_MS, self._poll_rows)
            return
        self._row_job = None
        RL.changed(self, job.lm)

    def draw_ruler(self, w, lines):
        s, top = self.scale, self.ruler_h
        self.create_rectangle(0, 0, w, top, fill=look.RULER_BG, outline="")
        bands = self.bands_x()
        for x0, x1 in bands:  # (black over a ruler band, like the piano roll's ruler)
            self.create_rectangle(max(x0, self.name_w), 0, x1, top, fill=look.RULER_PICK, outline="")

        def ink(x, plain):  # (text starting in a band: white)
            return look.RULER_PICK_TEXT if any(x0 <= x < x1 for x0, x1 in bands) else plain
        font = look.font(8, fit=True)
        measure = self.measurer(font)
        end = -math.inf
        for x, n in lines:  # bar numbers in the bottom half
            if x < self.name_w or x < end:
                continue
            self.create_line(x, top - 6 * s, x, top, fill=ink(x, look.RULER_TICK))
            self.create_text(x + 3, top - 9 * s, text=str(n + 1), anchor="w", fill=ink(x + 3, look.RULER_TEXT),
                             font=font)
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
            self.create_text(x + 1, 7 * s, text=text, anchor="w", fill=ink(x + 1, look.RULER_TEXT), font=small)
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
        kept = kept_drawn(app.tempo, base, app.ppq, end)  # (Keep timing parts' drawn tempo: purple, user 11-05)
        k_ticks = np.array([t for t, _ in kept], np.int64)
        k_bpm = 60_000_000 / np.array([u for _, u in kept] or [1], float)[:len(kept)]
        if got:
            ticks = np.array([t for t, _ in got], np.int64)
            bpm = 60_000_000 / np.array([u for _, u in got], float)
        else:
            ticks, bpm = np.zeros(1, np.int64), np.array([float(base)])
        both = np.concatenate([bpm, k_bpm])
        got = (ticks, bpm, float(both.min()), float(both.max()))
        self._heard = (key, got)
        self._kept_line = (k_ticks, k_bpm)
        return got

    def kept_line(self):
        """(ticks, BPMs) of the Keep timing parts' drawn tempo (tempo_file.kept_drawn), worked out with heard()."""
        self.heard()
        return self._kept_line

    def bpm_heights(self, xs, inner, cx=None, line=None):
        """The line height (pixels) at each pixel column xs of the notes' part: a line only where the tempo changes
        (like Domino's Conductor track, user 2026-10-23: no blocks between), the fastest of the changes in that
        column, scaled from the song's slowest (1 px) to its fastest (inner); on the doubling scale when the fastest
        is more than 4 x the slowest (a jump to 2000 BPM would flatten the rest). 0 = no change there. xs count from
        pixel cx of the song (None: the view's left edge). line: other (ticks, BPMs) on the same scale (kept_line)."""
        ticks, bpm, lo, hi = self.heard()
        if line is not None:
            ticks, bpm = line
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
        if self.sx and rh > 4 and w > self.name_w:
            lm = self.app.loaded
            # (only what it shows: a click / Ctrl+click on a track left it as it was but painted it again, ~0.6 s
            # with 2788 tracks, Shinki 2026-10-28)
            key = (id(self._made[0]) if self._made else None, id(self._all), self.current is None and not self.conductor_on,
                   frozenset(self.muted), json.dumps(sigs_of(self.app.tempo)), self.sx, self.app.ppq, self.app.keys,
                   rh, lm.hide_ver if lm is not None else None)
            ox = self.left_px()
            xs, xw = squares(ox, w - self.name_w)
            got = self.tiled("_all_sheet", key, xw, (0,), rh - 1, lambda cx, cy, cw, ch: self.paint_all(cx, cw, ch))
            for i in xs:
                self.create_image(self.name_w + i * SQUARE - ox, y0, image=got[(i, 0)].photo, anchor="nw")
        self.create_line(0, y0 + rh - 1, w, y0 + rh - 1, fill=look.ROLL_EDGE)

    def paint_all(self, cx, cw, h):
        """draw_all's picture: cw x h pixels from pixel cx of the song. The tracks painted in order, a later one over
        an earlier one; Spiderweb's own over a loaded MIDI's (as in the export). A loaded MIDI's tracks come from
        the loading pass's small pictures (small_mask)."""
        img = np.empty((h, cw, 3), np.uint8)
        img[:] = hex_rgb(look.TRACK_PICKED if self.current is None and not self.conductor_on else look.TRACK_ROW)
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
        # (the listed ones only: a removed track's notes go with its row, hunt 2026-10-26)
        loaded = [r for r in range(len(self._file_row)) if self._file_row[r] >= 0 and lm.tracks[r].notes] \
            if lm is not None else []
        if not len(n) and not loaded:
            return img
        k_lo, span = 0, self.app.keys  # (the whole key range, like the rows)
        if len(n):
            keep = (n[:, 2] >= 0) & (n[:, 2] < span)
            n, row = n[keep], row[keep]
        colours, ids = [], {}  # the colours in painting order; colour -> its number there

        def colour_id(slot):
            c = look.TRACK_MUTED if slot in self.muted else self.colour_of(slot)
            if c not in ids:
                ids[c] = len(colours)
                colours.append(c)
            return ids[c]
        if loaded:  # (each key x pixel: the colour painted last there, -1 none)
            lut = np.full(len(lm.tracks) + 1, -1, np.int64)  # (track -> its colour; the last = none)
            for r in loaded:
                lut[r] = colour_id(-1 - r)
            top = lut[self.loaded_tops(lm, loaded, cx, cw, span)]
        else:
            top = np.full((span, cw), -1, np.int64)
        if len(n):
            xa = np.clip(np.floor(n[:, 0] / ppq * self.sx).astype(np.int64) - cx, 0, cw - 1)
            xb = np.clip(np.maximum(xa, np.ceil(n[:, 1] / ppq * self.sx).astype(np.int64) - 1 - cx), 0, cw - 1)
            k = n[:, 2] - k_lo
            slots = self._all[row]
            order = []  # (each colour once, in the order of the first track using it: later tracks on top)
            for s in self._all[np.unique(row)].tolist():
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
        if not colours:  # (nothing in view)
            return img
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

    def loaded_tops(self, lm, loaded, cx, cw, span):
        """paint_all's loaded tracks: each key x pixel of cw from pixel cx, the track painted last there (-1 none).
        Made in SQUARE-wide blocks along the song, kept while what they show stays (Shinki 2026-10-28: a mute or a
        scroll to the picture's edge painted all 2788 tracks again, ~0.4-0.6 s)."""
        cut = set(self.cut_tracks(lm))  # (notes taken out: read exactly from the rows' blocks)
        place = (id(lm), lm.hide_ver, self.sx, span, tuple(loaded), frozenset(cut))
        if self._all_place != place:
            self._all_place, self._all_blocks = place, OrderedDict()
        blocks, parts = self._all_blocks, []
        tpp = lm.ppq / self.sx  # (ticks a pixel)
        for j in range(cx // SQUARE, (cx + cw - 1) // SQUARE + 1):
            got = blocks.get(j)
            if got is None:
                got, whole = np.full((span, SQUARE), -1, np.int32), True
                t0, t1 = j * SQUARE * tpp, (j * SQUARE + SQUARE + 2) * tpp  # (+ the one pixel more, rounding room)
                fast, run = RL.speed.loops(), []
                if fast is not None:  # (compiled: ~0.2 ms a track a block the plain way, user 2026-10-28 fast scrolls)
                    pics = self.small_pics(lm)
                for r in loaded + [None]:
                    if fast is not None:  # (tracks in a row without notes taken out: one compiled call, in order)
                        if r is not None and r not in cut:
                            run.append(r)
                            continue
                        if run:
                            fast.all_tops(got, *pics, np.array(run, np.int64), j * SQUARE, tpp, lm.G)
                            run = []
                    if r is None:
                        break
                    t = lm.tracks[r]
                    if t.klo >= span:
                        continue
                    m = self.tile_mask(r, j * SQUARE, SQUARE) if r in cut else None
                    if m is None:
                        if r in cut:
                            whole = False  # (its blocks not read yet: blocky for now, not kept)
                        elif t.pic_cols and not (t.pic_glo * lm.G < t1 and t0 < (t.pic_glo + t.pic_cols * t.pic_pf)
                                                 * lm.G + tpp):
                            continue  # (nothing of it in this block)
                        # (one pixel more: the block's last pixel reaches as far as in one wide picture)
                        m = small_mask(lm, t, j * SQUARE, SQUARE + 1, self.sx)
                    if m is not None:
                        got[t.klo:t.khi + 1][m[:span - t.klo, :SQUARE]] = r
                if whole:
                    blocks[j] = got
                    while len(blocks) > ALL_BLOCKS:
                        blocks.popitem(last=False)
            else:
                blocks.move_to_end(j)
            parts.append(got)
        return np.concatenate(parts, axis=1)[:, cx % SQUARE:cx % SQUARE + cw]

    def small_pics(self, lm):
        """Every track's loading-pass small picture packed end to end for fastloops.all_tops: (data, offsets, rows,
        bytes a row, first key, first column, columns per picture column, columns, columns with a note, their
        offsets), kept for this MIDI."""
        got = self._pics
        if got is None or got[0] is not lm:
            ts = lm.tracks
            sizes = [t.pic.size if t.pic_cols else 0 for t in ts]
            data = np.concatenate([t.pic.ravel() for t, n in zip(ts, sizes) if n] or [np.zeros(1, np.uint8)])
            used = [np.unpackbits(np.bitwise_or.reduce(t.pic, axis=0), count=t.pic_cols).astype(bool)
                    for t in ts if t.pic_cols and t.pic.shape[0]]
            ncols = [t.pic_cols if t.pic_cols and t.pic.shape[0] else 0 for t in ts]
            arr = lambda f: np.array([f(t) for t in ts] or [0], np.int64)  # noqa: E731
            starts = lambda n: np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64) if ts \
                else np.zeros(1, np.int64)  # noqa: E731
            got = self._pics = (lm, (data, starts(sizes), arr(lambda t: t.pic.shape[0] if t.pic_cols else 0),
                                     arr(lambda t: t.pic.shape[1] if t.pic_cols else 0), arr(lambda t: t.pic_klo),
                                     arr(lambda t: t.pic_glo), arr(lambda t: t.pic_pf), arr(lambda t: t.pic_cols),
                                     np.concatenate(used or [np.zeros(1, bool)]), starts(ncols)))
        return got[1]

    def draw_conductor(self, w):
        """The Conductor row: a line at each tempo change, as tall as its BPM (the song's own slowest .. fastest,
        user 2026-10-23: Domino's 1-512 BPM wouldn't fit a MIDI file's range) and a short line at each time / key
        signature."""
        s, y0 = self.scale, self.conductor_y()
        y1 = y0 + self.row_h
        x0 = self.name_w
        if self.sx and self.row_h > 4 and w > x0:  # (in squares like the rows' picture)
            h = self.row_h - 1
            lo_hi = self.heard()[2:]
            picked = self.conductor_on
            key = (self._heard[0], lo_hi, self.sx, h, picked)

            def paint(cx, cy, cw, ch):
                inner = self.row_h - 2 * round(PAD * s) - 1
                ys = np.arange(ch)[:, None]
                bottom = ch - round(PAD * s)
                img = np.empty((ch, cw, 3), np.uint8)
                # (highlighted like a track; else the colour before Track 1's, so the rows alternate, user 11-05)
                img[:] = hex_rgb(look.TRACK_PICKED if picked else look.TRACK_ROW_ALT)
                for line, colour in ((None, look.TRACK_TEMPO), (self.kept_line(), look.TEMPO_KEEP)):
                    if line is not None and not len(line[0]):
                        continue
                    tall = self.bpm_heights(np.arange(cw), inner, cx, line)
                    img[(ys >= bottom - tall[None, :]) & (ys < bottom)] = hex_rgb(colour)
                return img
            ox = self.left_px()
            xs, xw = squares(ox, w - x0)
            got = self.tiled("_tempo_sheet", key, xw, (0,), h, paint)
            for i in xs:
                self.create_image(x0 + i * SQUARE - ox, y0, image=got[(i, 0)].photo, anchor="nw")
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
        with self.layer("names_bg"):
            self.create_rectangle(0, y0, x1, h, fill=look.TRACK_NAME_BG, outline="")
        top = self.area()[1]
        slots = self._all
        line_row, line_ch = self.lines()[:2]
        chans = self.row_chans()
        ind = round(INDENT * s)
        for line in range(max(0, self.top // self.row_h), len(line_row)):
            ya = top + line * self.row_h - self.top
            if ya > h:
                break
            yb = ya + self.row_h
            with self.layer(f"n{line}", "names"):  # (each row kept while it looks the same)
                row, c = int(line_row[line]), int(line_ch[line])
                slot = int(slots[row])
                if c >= 0:  # (an open folder's sub-row: indented "Ch n" in its colour, its own speaker)
                    picked = slot == self.current and self.current_ch == c and not self.conductor_on
                    chosen = (slot, c) in self.sel_ch  # (picked along with it: fainter, as tracks)
                    if picked or chosen:
                        self.create_rectangle(0, max(ya, top), x1, yb, fill=look.TRACK_PICK if picked else look.TRACK_SELECT,
                                              outline="")
                    elif self.aimed_ch(row) == c:  # (its track row highlighted: new notes go here, user 2026-10-29)
                        w = max(1, round(2 * s))
                        self.create_rectangle(w / 2, max(ya, top) + w / 2, x1 - w / 2, yb - w / 2, outline=look.TRACK_PICK,
                                              width=w)
                    mid = (ya + yb) / 2
                    if self.row_h >= 12 * s and mid > top:
                        self.create_text(18 * s + ind, mid, text=tr("tracks.channel", ch=c + 1), anchor="w", font=font,
                                         fill=look.TRACK_PICK_TEXT if picked else look.TRACK_SELECT_TEXT if chosen
                                         else self.chan_colour(c))
                        if (row, c) in self.cc_rows():  # (its CC events, user 2026-10-29: never silent)
                            self.draw_cc_mark(x1 - 26 * s, mid, font, picked)
                        self.draw_speaker(self.speaker_x(), mid, slot in self.muted or (slot, c) in self.muted, picked)
                    self.create_line(ind, yb - 1, x1, yb - 1, fill=look.TRACK_LINE)
                    continue
                folder = len(chans[row]) > 1
                picked = slot == self.current and self.current_ch is None and not self.conductor_on
                chosen = not picked and slot in self.selected  # (selected along with it: a fainter blue, like Domino)
                if picked or chosen:
                    self.create_rectangle(0, max(ya, top), x1, yb, fill=look.TRACK_PICK if picked else look.TRACK_SELECT,
                                          outline="")
                elif (self.own_track(slot) or {}).get("auto"):  # (added by Spiderweb for a shape's colours, user)
                    self.create_rectangle(0, max(ya, top), x1, yb, fill=look.TRACK_ADDED, outline="")
                if self.row_h >= 12 * s:
                    colour = (look.TRACK_PICK_TEXT if picked else look.TRACK_SELECT_TEXT if chosen else look.TRACK_TEXT)
                    ch = tr("tracks.folder_ch", n=len(chans[row])) if folder else self.channel_of(slot)
                    left = 18 * s + (ind if folder else 0)  # (a folder: its ▸ / ▾ first)
                    cc = any(r == row for r, _ in self.cc_rows())  # (CC events ride on it: a "CC" mark)
                    mark = measure(tr("cc.mark")) + 6 * s if cc else 0
                    room = x1 - 26 * s - left - measure(ch) - 8 * s - mark
                    name = cut_text(self.name_of(row), room, measure)
                    mid = (ya + yb) / 2
                    if mid > top:
                        dot = 10 * s + (ind if folder else 0)
                        if slot in self.behind:  # (shown behind the current track: a dot in its colour)
                            r = 3.5 * s
                            self.create_oval(dot - r, mid - r, dot + r, mid + r, fill=self.colour_of(slot),
                                             outline=colour)
                        if folder:
                            self.draw_fold(6 * s, mid, slot in self.open, colour)
                        self.create_text(left, mid, text=name, anchor="w", fill=colour, font=font)
                        self.create_text(x1 - 26 * s, mid, text=ch, anchor="e",
                                         fill=look.TRACK_PICK_TEXT if picked else look.HINT, font=font)
                        if cc:
                            self.draw_cc_mark(x1 - 26 * s - measure(ch) - 6 * s, mid, font, picked)
                        state = self.mute_state(slot) if folder else 2 if slot in self.muted else 0
                        self.draw_speaker(self.speaker_x(), mid, state == 2, picked, part=state == 1)
                self.create_line(0, yb - 1, x1, yb - 1, fill=look.TRACK_LINE)
        with self.layer("names_top"):
            self.create_rectangle(0, y0, x1, top, fill=look.TRACK_NAME_BG, outline="")  # (rows scrolled under it)
            ya = self.conductor_y()
            picked = self.current is None and not self.conductor_on  # (the All tracks row: highlighted like a track)
            if picked:
                self.create_rectangle(0, y0, x1, ya - 1, fill=look.TRACK_PICK, outline="")
            self.create_text(18 * s, (y0 + ya) / 2, text=tr("tracks.all"), anchor="w",
                             fill=look.TRACK_PICK_TEXT if picked else look.TRACK_TEXT, font=look.font(9, "bold"))
            if self.cc_rows():  # (some track sends CC events: user 2026-10-29)
                self.draw_cc_mark(x1 - 26 * s, (y0 + ya) / 2, font, picked)
            self.create_line(0, ya - 1, x1, ya - 1, fill=look.ROLL_EDGE)
            if self.conductor_on:  # (highlighted like a track, user 11-05)
                self.create_rectangle(0, ya, x1, top, fill=look.TRACK_PICK, outline="")
            self.create_text(18 * s, ya + self.row_h / 2, text=tr("tracks.conductor"), anchor="w",
                             fill=look.TRACK_PICK_TEXT if self.conductor_on else look.TRACK_TEXT, font=font)
            self.create_line(0, top, x1, top, fill=look.ROLL_EDGE)
            self.create_line(x1, 0, x1, h, fill=look.ROLL_EDGE)

    def draw_fold(self, cx, cy, opened, colour):
        """A folder's ▾ (open) / ▸ (shut)."""
        s = self.scale
        if opened:
            pts = (cx - 4 * s, cy - 2 * s, cx + 4 * s, cy - 2 * s, cx, cy + 3 * s)
        else:
            pts = (cx - 2 * s, cy - 4 * s, cx + 3 * s, cy, cx - 2 * s, cy + 4 * s)
        self.create_polygon(*pts, fill=colour, outline="")

    def on_fold(self, x):
        """Is x on a folder's ▾ / ▸ (the names' left edge)?"""
        return x < FOLD_W * self.scale

    def draw_speaker(self, cx, cy, muted, picked, part=False):
        """A small speaker: sound waves beside it while heard, a red cross while muted; part (a folder with some of
        its channels muted): one wave and a small red cross."""
        s = self.scale
        colour = look.TRACK_PICK_TEXT if picked else look.TRACK_TEXT
        if muted:
            colour = look.HINT
        x = cx - 4 * s
        self.create_polygon(x - 3 * s, cy - 2 * s, x, cy - 2 * s, x + 3 * s, cy - 5 * s, x + 3 * s, cy + 5 * s,
                            x, cy + 2 * s, x - 3 * s, cy + 2 * s, fill=colour, outline="")
        if part:
            r = 4 * s
            self.create_arc(x + 3 * s - r, cy - r, x + 3 * s + r, cy + r, start=-40, extent=80, style="arc",
                            outline=colour, width=max(1, round(s)))
            a, b = x + 8 * s, x + 11.5 * s
            for y0, y1 in ((cy - 1.75 * s, cy + 1.75 * s), (cy + 1.75 * s, cy - 1.75 * s)):
                self.create_line(a, y0, b, y1, fill=look.ERROR, width=max(1, round(1.2 * s)))
        elif muted:
            a, b = x + 5 * s, x + 10 * s
            for y0, y1 in ((cy - 2.5 * s, cy + 2.5 * s), (cy + 2.5 * s, cy - 2.5 * s)):
                self.create_line(a, y0, b, y1, fill=look.ERROR, width=max(1, round(1.4 * s)))
        else:
            for r in (4 * s, 7 * s):
                self.create_arc(x + 3 * s - r, cy - r, x + 3 * s + r, cy + r, start=-40, extent=80, style="arc",
                                outline=colour, width=max(1, round(s)))

    def see_now(self):
        """Tab into the list (user 2026-10-26): the play line out of view = the view goes to the PAGE holding it
        (pages of the view's width from the song's start, like Domino: on the first page = bar 1 stays), the
        highlighted track's row out of view = scrolled to the middle."""
        if self.sx is None:
            return
        self.update_idletasks()  # (just packed: its real width, else the first Tab always jumped, user 2026-10-26)
        x = self.t2x(self.app.playhead)
        if x < self.name_w or x > self.winfo_width() - 6 * self.scale:
            page = max(1, self.winfo_width() - self.name_w) / self.sx
            self.view_t = page * int(self.app.playhead // page)
        line = self.line_of(self.current, self.current_ch) if self.current is not None else None
        if line is not None:
            h = self.winfo_height() - self.area()[1]
            y = line * self.row_h - self.top
            if y < 0 or y + self.row_h > h:
                self.top = round(line * self.row_h - (h - self.row_h) / 2)
        self.clamp()

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
        w, h = self.winfo_width(), self.winfo_height()
        ahead = getattr(self.app, "ahead_play", None)
        if ahead is not None and w > self.name_w:  # (M4: Rendered ahead's faint grey, as on the piano roll)
            for a, b in ahead.grey_beats(self.x2t(self.name_w), self.x2t(w)):
                a, b = max(self.name_w, round(self.t2x(a))), min(w, round(self.t2x(b)))
                if b > a:
                    self.create_rectangle(a, self.ruler_h, b, h, fill=look.AHEAD_UNMADE, outline="",
                                          stipple="gray12", tags=("playhead", "ahead"))
        x = round(self.t2x(self.app.playhead))
        if self.name_w <= x <= w:
            self.create_line(x, 0, x, h, fill=look.PLAY_LINE, width=max(1, round(self.scale)), tags="playhead")


def row_mask(lm, r, ax, bx, W, ch=None):
    """A loaded track's notes exactly: its keys klo..khi x W pixels, the colour group (COLOURS_16) of the channel
    sounding there, -1 none (False: unreadable). ch: that channel alone (a folder's sub-row)."""
    t = lm.tracks[r]
    try:
        tab, _ = RL.exact_tab(lm, ax, bx, W, t.klo, t.khi - t.klo + 1, tracks=(r,),
                              only=None if ch is None else {r: ch})  # (ch: a channel or a part's set)
    except Exception:  # (a file that changed / went: the small picture stays)
        RL.write_log_now()
        return False
    return False if tab is None else np.where(tab >= 0, tab // 32, -1).astype(np.int8)


def squares(o, n):
    """The squares (SQUARE px) showing pixels o .. o + n - 1, and those with one more on each side (painted ahead
    so scrolling finds them ready)."""
    a, b = max(0, o) // SQUARE, max(0, o + n - 1) // SQUARE + 1
    return range(a, b), range(max(0, a - 1), b + 1)


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
    a = np.clip(c0[on], 0, t.pic_cols - 1)
    joined = tpp >= step
    end = int(np.clip(c1[on[-1]], 1, t.pic_cols)) if joined else int(a[-1]) + 1
    # (only the columns shown unpacked: the whole track's every redraw was most of the All tracks row's time at
    # 2788 tracks, Shinki 2026-10-28)
    a0 = int(a[0])
    b0 = a0 >> 3
    bits = np.unpackbits(t.pic[:, b0:(end + 7) >> 3], axis=1)[:, a0 - (b0 << 3):end - (b0 << 3)].astype(bool)
    a = a - a0
    got = np.logical_or.reduceat(bits, a, axis=1) if joined else bits[:, a]
    out = np.zeros((t.khi - t.klo + 1, aw), bool)
    out[t.pic_klo - t.klo:t.pic_klo - t.klo + len(got), on] = got
    return out


class RowJob:
    """A loaded MIDI's rows' blocks [(track, block, channel or None)] read exactly on a background thread, in order
    (stop = give up); shown = the ones on screen blocky."""

    def __init__(self, lm, place, todo, ax, shown):
        self.lm, self.place, self.todo, self.ax, self.shown = lm, place, todo, ax, shown
        self.stop = self.done = False
        self.out = deque()  # ((track, block, channel), mask) as read (TrackList.take_rows)

    def run(self):
        for r, j, c in self.todo:
            if self.stop:
                break
            self.out.append(((r, j, c), row_mask(self.lm, r, self.ax, -float(j * TILE), TILE, c)))
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
    # (the strip's Show multiple tracks… too: picks belong to a track, hunt 2026-10-26)
    for w in tuple(getattr(app, "onion_parts", ())) + tuple(getattr(app, "strip_parts", ())):
        w.state(["!disabled"] if on else ["disabled"])
    tl = getattr(app, "track_list", None)
    if tl is not None and tl.picker:
        tl.picker.fill()


def build_strip(app, parent, tl):
    """The strip above the track list: Show multiple tracks… + Faint [n] % (its snap is the toolbar's, user
    2026-10-23: SnapPicker.use)."""
    strip = ttk.Frame(parent, padding=(2, 2))
    b = ttk.Button(strip, text=tr("tracks.show_multiple"), command=tl.open_picker)
    b.pack(side="left")
    Tooltip(b, tr("tracks.show_multiple_tip"))
    app.strip_parts = (b,)
    sync_onion(app)
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
        # Ctrl+A = All (user 2026-10-31: Tk's own list key only highlighted the rows, the piano roll took nothing
        # until the next click); Ctrl+/ too (Tk's), Ctrl+\ = None. "break": not the main window's Select all
        for keys, on in (("<Control-a> <Control-A> <Control-slash>", True), ("<Control-backslash>", False)):
            for k in keys.split():
                self.bind(k, lambda e, on=on: (self.set_all(on), "break")[1])
                self.list.bind(k, lambda e, on=on: (self.set_all(on), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.slots = []
        self.fill()
        remember_place(self, "tracks_picker")

    def fill(self):
        """The rows again (the tracks / the current one changed), the picked ones selected."""
        tl, lb = self.tl, self.list
        slots = [int(s) for s in tl.tracks()]
        top = lb.yview()[0]
        lb.config(state="normal")
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
        lb.config(state="normal" if tl.current is not None else "disabled")  # (All tracks: nothing to pick for)

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
