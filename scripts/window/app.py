"""Main window: toolbar, side panel, shapes.
Its other parts: panel_custom.py / panel_funnel.py / panel_tumour.py (+ tumour_window.py) / panel_text.py /
panel_pattern.py (those panel sections), app_playback.py (playing, MIDI out), app_pictures.py (placed pictures),
history.py (undo and the History panel),
project.py (files, autosave, MIDI export), widgets.py (tooltips), font_dialog.py (picking a font)."""

import collections
import copy
import itertools
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np

from files.lang import size as lang_size, tr, trn
from notes.areas import ALL_COLOURS, most_colours
from notes.custom import (ROUND, CUSTOM_DEFAULTS, capped_colours, custom_note_count, has_pattern, set_key_top,
                          tracks_apart)
from window import big_ask, look, notes_wait
from window.help import Tips, open_help
from window.updates import Updates
from window.help_texts import BY_ID, TOOL_TOPICS
from window.track_channels import tidy_tracks
from notes.engine import (BEATS_FAR, CLASH, aligned_ch, rest_ch, KINDS, NO_NOTES, OWN0, SHAPE_DEFAULTS, as_made, cached_arrays, from_key, fx_notes, merges_unmade, point_names,
                          one_voice, own_slot, remove_across, render, resolve_overlaps, shape_key, slot_track_channel,
                          split_overlaps, heard_way, keeps_laps,
                          track_ids,
                          auto_count, PORTS, pasted_notes, edge_made, past_edges, SPACE_KEYS,
                          off_part)
from notes.paths import rows_up_to
from files.own_tracks import FILE0, HAND, file_order, key_track, free_channel, full_plan, into_ids, new_id, with_port
from notes.loaded import port_numbers
from notes.funnel import FUNNEL_DEFAULTS, funnel_note_count, inside_out, turned_curve
from notes.fx import (flip_shape, flipped as fx_flipped, glue_after, tidy_glue, turn_shape, with_turn as fx_turned,
                      with_velocity)
from notes.gateflip import (all_flipped, colours_back, offered as gate_offered,
                            turn_velocities, unflipped_starts, why_not as gate_why_not)
from notes.glue import added as glue_added, glue_box, to_shares as glue_shares
from notes import engine, hz_cc
from notes.hz_cc import cc_spans
from notes.passengers import spans as passenger_spans
from notes.hzbass import fit_length, has_cc, own_channels
from notes.mask import (MASK_DEFAULTS, as_used, can_mask, is_mask, mask_view, picture_like, pinned_slots, silent,
                        slot_pal, slot_rule, tidy_groups)
from notes.merge import has_colours, merge_parts, velocity_reversed as merge_vrev
from notes.recolour import counted_starts, first_starts, is_recoloured, with_most, without_recolour
from notes.sustain import is_sustained
from notes.strips import gap_of, has_strips, strip_colours, strip_laps, strip_spans
from notes.pattern import moved_formulas
from notes.paths import KEYS
from notes.tumour import LINE_KINDS
from notes.polygon import POLYGON_DEFAULTS
from notes.ripples import grows_free
from notes.sliced import completed, cut_group, fresh_marks, moved_by, steps_kept, turned_piece
from notes.smooth import SMOOTH_DEFAULT
from notes.text import TEXT_DEFAULTS
from files.mathexpr import calc, calc_int, fmt
from window.panel_colours import ColoursPanel
from window.panel_recolour import RecolourPanel
from window.panel_snap import SnapPanel
from window.panel_mask import MaskPanel
from window.panel_custom import CustomPanel
from window.panel_nested import NestedPanel
from window.panel_tiles import TilesPanel
from window.panel_ripples import RipplesPanel
from window.panel_freehand import FreehandPanel
from window.panel_gliss import GlissPanel
from notes.gliss import DEFAULTS as GLISS_DEFAULTS, fit_pts as fit_gliss
from window.panel_lasso import LassoPanel
from window.panel_funnel import FunnelPanel
from window.panel_pattern import PatternPanel
from window.panel_polygon import PolygonPanel
from window.panel_text import TextPanel
from window.claw_window import open_claw
from window.strum_window import open_strum
from window.chop_window import open_chop, quick_chop
from window.hz_preview import clean_settings as clean_preview
from window.builtin_settings import Overload
from files.kestrel import BLOCKS as KESTREL_BLOCKS, KESTREL_DEFAULTS
from window.panel_tumour import TumourPanel
from notes.joined import all_tumours, is_joined
from window.join_split import JoinSplit
from window.between_window import BetweenGroups
from notes.between import (copied as between_copied, keep_pushes, push_sizes, sync_groups, turns as between_turns,
                           whole_groups)
from window.app_pictures import Pictures
from window.app_playback import GROUP_DEFAULT, Playback
from window.history import CH10_OFF, HistoryPanel
from window.menubar import build_menu
from roll.pianoroll import PianoRoll
from roll.roll_draw import GHOST_FADE, STACK_MODES, list_order, painting_order, stack_rule, track_ranks
from files import clipboard, errors, notes_clip, speed, system
from files.about import ICONS, VERSION
from files.playback import AHEAD, BUILTIN, DEFAULT_DEVICE, KESTREL, MidiOut, Player
from files.slow_down import SlowDown
from files.midi_out import PPQ_WARN, track_slots
from files.domino_clip import DOMINO_STARTS, FORMAT as DOMINO_FORMAT, on_clipboard
from files.clipboard import RAW, copy_count, get_text, put_text, put_text_and, registered
from files.share import LONG_LINE, FIND, ShareError, made_by, read_shapes, shapes_line, unpack
from files.project import AUTOSAVE, OUTPUT_DIR, ProjectFiles
from files.snap import DEFAULT_SNAP, snap_beats
from notes.tempo import changed as tempo_edited, drop_loose, empty as empty_tempo, watch as watch_tempo
from notes.meter import DEFAULT as DEFAULT_SIG, first_bar_beats, sigs_of, where
from roll.roll_shared import box_kind, cached_path
from roll.zoombar import add_zoom_bars
from window.snap_picker import SnapPicker
from window import palettes
from window.settings_window import open_settings
from window.tool_picker import ToolPicker
from window.tempo_pane import TempoBox
from window.track_list import TrackList, build_onion, build_strip, sync_onion
from window.load_extract import sync_channels, sync_extract
from window.velocity import VelocityPane
from window.velocity_formula import VelocityFormulaBar
from window.side_panel import ScrollingSide
from window.widgets import (REDO_KEYS, UNDO_KEYS, ListTip, Scrub, StatusLine, Tooltip, bad, click_first,
                            fit_dropdowns, good, grid_shown, mac_buttons, no_focus, set_glide, label_column, remember_good, watch_bad, ShortLabel, WrapRow)


VEL_KEYS = ("vel0", "vel1")
# Common PPQ values. Above 32767 the MIDI header officially means SMPTE timing
PPQS = [2, 4, 8, 16, 24, 48, 96, 120, 144, 192, 240, 384, 480, 768, 960, 1024, 1440, 1920, 2048, 2880,
        3840, 4096, 5760, 7680, 8192, 11520, 12288, 15360, 16384, 23040, 24576, 30720, 32768, 36864,
        46080, 49152, 65535]
TOOLS = [("select", tr("app.select"), "v"), ("slice", tr("app.slice"), "k"), ("lasso", tr("app.lasso"), "j")]
SLICE_MODES = [("line", tr("app.slice_line")), ("shape", tr("app.slice_shape"))]
LASSO_MODES = [("free", tr("app.lasso_free")), ("poly", tr("app.lasso_poly")),
               ("magnet", tr("app.lasso_magnet"))]  # (the Lasso button's ▾)
# in the drawing tools' list (tool_picker.py), in this order
DRAW_TOOLS = [("line", tr("app.line"), "l"), ("poly", tr("app.polyline"), "p"), ("free", tr("app.freehand"), "f"),
              ("curve", tr("app.curve"), "c"), ("arc", tr("app.arc"), "a"), ("gliss", tr("app.gliss"), "r"),
              ("custom", tr("app.custom_shape"), "s"),
              ("circle", tr("app.circle"), "o"), ("polygon", tr("app.polygon"), "q"), ("funnel", tr("app.funnel"), "n"),
              ("text", tr("app.text"), "x"), ("hz", tr("app.hz_bass"), "h"), ("picture", tr("app.picture"), "i")]
LATE_S = 0.15  # making the notes took longer than this (s): after an edit they're made in the background (late_ok)
OLD_NOTES_SHARE = 8  # the notes cache (shapes as they WERE = undo targets) may take 1/8 of the PC's memory...
OLD_NOTES_MOST = 2 ** 30  # (bytes) ...and at most 1 GB (user 2026-11-10: a big limit; undo past it makes notes again)
WINE_NOTES_SHARE = 16  # (Wine-like systems: 1/16, phones)
OLD_NOTES_LEAST = 256 * 2 ** 20  # (bytes; at least this much: forget_old_notes)
_old_notes_most = []


def old_notes_most():
    """Bytes the notes cache may take before the notes of shapes as they WERE go, oldest first (App.forget_old_notes;
    the PC's memory asked once): the smaller of OLD_NOTES_MOST and 1/OLD_NOTES_SHARE of it (unknown = 1 GB), Wine
    1/WINE_NOTES_SHARE if smaller. Undo of a forgotten version makes its notes again (2 M notes: ~1 s, user OK)."""
    if not _old_notes_most:
        total = system.total_memory()
        most = min(OLD_NOTES_MOST, total // OLD_NOTES_SHARE) if total else OLD_NOTES_MOST
        if system.WINE:
            most = min(most, max(OLD_NOTES_LEAST, (total or 16 * 2 ** 30) // WINE_NOTES_SHARE))
        _old_notes_most.append(max(OLD_NOTES_LEAST, most))
    return _old_notes_most[0]


SHARE_LEAST = 100_000  # notes: arrays this long in the notes cache are kept once (shared_arrays)
_SAMPLE = 64  # rows looked at first before two arrays are compared whole


def shared_arrays(got, pool):
    """got (notes, tracks...) as the notes cache keeps it: each big array that's a column of a bigger array nothing
    else holds = its own copy (a tracks column kept a whole 4-column array alive: 60 MB for 15 at 2 M notes), and
    each one EQUAL to an array the pool (a notes cache) holds at the same place = that one (the notes before a
    Recolour and after it are the same notes: kept once). The cache never changes what it hands out, so sharing
    is safe."""
    if not isinstance(got, tuple):
        return got
    out = list(got)
    for i, a in enumerate(got):
        if not isinstance(a, np.ndarray) or len(a) < SHARE_LEAST:
            continue
        b = a.base
        if isinstance(b, np.ndarray) and b.nbytes >= 2 * a.nbytes and sys.getrefcount(b) <= 3:
            a = out[i] = a.copy()  # (refs: a.base, b, the call's own)
        del b
        rows = np.linspace(0, len(a) - 1, _SAMPLE).astype(np.int64)
        look = a[rows]
        for v in pool.values():
            c = v[i] if isinstance(v, tuple) and len(v) > i else None
            if (c is a or not isinstance(c, np.ndarray) or c.shape != a.shape or c.dtype != a.dtype
                    or not np.array_equal(c[rows], look) or not np.array_equal(c, a)):
                continue
            out[i] = c
            break
    return tuple(out)


MAX_POINT_BOXES = 40  # a curve with more points than this lists none in the panel (a line saying so instead)
CHANNEL_CHOICES = [
    ("raw", tr("app.as_drawn"),
     tr("app.keeps_overlaps_every_note_exactly_as")),
    ("single", tr("app.single_channel"),
     tr("app.removes_overlaps_all_on_one_channel")),
    ("auto", tr("app.multi_channel"),
     tr("app.a_channel_per_overlap_shapes_whose")),
]
SPLIT_CHOICES = [
    ("key", tr("app.split_same_key_at_the_same")),
    ("time", tr("app.split_any_notes_at_the_same")),
]
SPLIT_TIP = (
    tr("app.same_key_shapes_only_get_split")
)


class Stopped(Exception):
    """Notes being made in the background (App.notes_rested) aren't wanted any more."""


def can_go_in(sh):
    """True if the shape can be drawn into a track added by hand (Draw into a track group B: pictures, masks, Hz
    basses and Channel per strip shapes keep their own channels for now; pasted notes with CC events go in, their
    events on each colour's track, user 2026-10-29: "you copy from Domino is what you'll get")."""
    return not (picture_like(sh) or is_mask(sh) or "hz" in sh or has_strips(sh))


def recolour_alone(sh):
    """sh as its notes are made outside Multi channel: Recolour left out (in one channel it would only change
    overlaps), except on a Domino paste (its tracks kept in every mode: hunt 2026-11-01)."""
    if "recolour" not in sh:
        return sh
    return with_most(sh, False) if pasted_notes(sh) else without_recolour(sh)


def colours_apart(sh, mode):
    """Does a laid shape keep each colour on its own track in this channels mode? Multi channel: always. Single
    channel / As drawn: only a shape keeping its own channels (an Extract, a picture) or a Domino paste (user
    2026-10-29 / 11-01: what you copied is what you get); other shapes play every colour on their first track."""
    return mode == "auto" or bool(pinned_slots(sh, mode)) or pasted_notes(sh)


def placed_on(sh, own):
    """The tracks the shape was laid on (Draw into a track; own: {id: channel} of the tracks added by hand): its
    into_ids up to the first track added by hand that's gone (from there on: laid again, App.fit_into). Each a
    track added by hand's id (>= 0) or -1 - an automatic track's slot (pinned there, B11). [] = an automatic shape
    (also when its first one is gone, or it can't go in: can_go_in)."""
    out = []
    if not can_go_in(sh):
        return out
    for v in into_ids(sh):
        if v >= 0 and v not in (own or {}):
            break
        out.append(v)
    return out


class Known:
    """placed_on's own for App.fit_into: a track id there = one of yours (by_id: {id: track}, grows as tracks are
    added) or a loaded MIDI's track it knows (files: App.file_tracks)."""

    def __init__(self, by_id, files):
        self.by_id, self.files = by_id, files

    def __contains__(self, v):
        return v in self.by_id or v >= FILE0 and v - FILE0 in self.files

    def __bool__(self):
        return True


def entry_slot(v, own, rule, use10):
    """A placed_on entry's slot now: a track added by hand = engine.own_slot; an automatic track = its slot, as the
    project's channel rule has it now (rule = the shape's "slots10" when laid: mask.as_used)."""
    if v >= 0:
        return own_slot(v, own[v])
    return int(as_used(np.array([-1 - v], np.int64), rule, use10)[0])


def laid_slots_of(sh, ids, own, use10):
    """Each placed_on entry's slot now (entry_slot), in order; a colour keeps its own channel (sh["into_ch"], -1 =
    none: the track's) while it's on the track it was given it on (sh["into_home"]) and that track has no channel
    of its own from Track Properties (own.fixed): an Extract's colours in a loaded MIDI's track (group D), a channel
    dragged into a track of yours or a loaded one (a folder, PLAN #11); else the track's channel (hunt 2026-10-26:
    an Extract laid elsewhere held two channels in one track)."""
    rule = sh.get("slots10") if isinstance(sh.get("slots10"), bool) else None
    chs = sh.get("into_ch") if isinstance(sh.get("into_ch"), list) else []
    home = sh.get("into_home") if isinstance(sh.get("into_home"), list) else None
    fixed = getattr(own, "fixed", ())

    def own_ch(k, v):
        return (v >= 0 and k < len(chs) and chs[k] >= 0 and v not in fixed
                and (home is None or k < len(home) and home[k] == v))
    return [own_slot(v, chs[k]) if own_ch(k, v) else entry_slot(v, own, rule, use10) for k, v in enumerate(ids)]


def own_chs(sh):
    """(channels, homes) one per "into" entry (aligned_ch; -1 = the track's channel; no home given = the track it's
    on: its channel holds there), for fit_into to carry with its colours."""
    into = into_ids(sh)
    got = aligned_ch(dict(sh, into=into)) if "into_ch" in sh else {}
    chs = got.get("into_ch", [-1] * len(into))
    return chs, got.get("into_home", [v if c >= 0 else -1 for v, c in zip(into, chs)])


def put_rest_ch(sh, chs, homes):
    """The colours gone for now's channels (engine.rest_ch) kept on the shape, or dropped when none has one."""
    if any(c >= 0 for c in chs):
        sh["into_rest_ch"], sh["into_rest_home"] = list(chs), list(homes)
    else:
        sh.pop("into_rest_ch", None)
        sh.pop("into_rest_home", None)


def keep_home(sh):
    """Before an Extract's colours are laid on other tracks: the tracks they came from kept (into_home; made before
    it was kept = the tracks it's on now)."""
    if isinstance(sh.get("into_ch"), list) and not isinstance(sh.get("into_home"), list):
        sh["into_home"] = [v for v in into_ids(sh)]


class Channels(dict):
    """App.own_channels' {id: channel}, + fixed = the loaded tracks (FILE0 + n) given a channel in Track
    Properties (laid_slots_of: every colour in them on it)."""
    fixed = frozenset()


def wrap_landing(n, keys):
    """THE wrap rule ("Wrap them around", user 2026-11-08), for kept notes past the keys / before beat 0 (rows
    start, end, key, ...): (the ones that land, put where they land, ok = which rows of n those are). Start cut at
    beat 0 (nothing before it, a note across it starts on it), key mod the project's keys (one over the top = key 0,
    one under key 0 = the top key). Export (wrapped_in) and the piano roll's landing spots (App.wrapped_rows) both
    use this, so what's shown is what's written."""
    n = np.array(n, copy=True)
    n[:, 0] = np.maximum(n[:, 0], 0)
    ok = n[:, 1] > n[:, 0]
    n = n[ok]
    n[:, 2] %= keys
    return n, ok


def landing_clashes(rows, real):
    """(start, end, key) for each landing note (rows) that sits on a real note (real) on its key: from the first
    real note's start it meets (or its own start, when one already sounds there) to the last real end it meets
    (or its own end). One row per clashing landing note; none when nothing meets. Same key, time overlapping (the
    overlap the export sees: Single channel cuts it, Multi moves a colour to another channel)."""
    empty = np.zeros((0, 3), np.int64)
    if not len(rows) or not len(real):
        return empty
    real = np.asarray(real)
    k, s, e = real[:, 2].astype(np.int64), real[:, 0].astype(np.int64), real[:, 1].astype(np.int64)
    order = np.lexsort((s, k))
    k, s, e = k[order], s[order], e[order]
    far = int(max(np.abs(s).max(), np.abs(e).max(), np.abs(rows[:, :2]).max())) + 2
    lift = np.int64(2 * far + 1)  # (each key its own stretch of numbers: searches / running ends never cross keys)
    base = k * lift + far
    place = base + s
    ends = np.maximum.accumulate(base + e)  # (latest end so far, per key: base keeps keys apart)
    rk, rs, re = rows[:, 2].astype(np.int64), rows[:, 0].astype(np.int64), rows[:, 1].astype(np.int64)
    rb = rk * lift + far
    # (latest end of the real notes on this key starting before this one ends; another key's = far below rs)
    upto = np.searchsorted(place, rb + re, "left")
    late = np.where(upto > 0, ends[np.maximum(upto - 1, 0)] - rb, rs - 1)
    hit = late > rs
    if not hit.any():
        return empty
    # (one on this key already sounding at its start = from its start; else from the first one starting after it)
    at = np.searchsorted(place, rb + rs, "left")
    before = np.where(at > 0, ends[np.maximum(at - 1, 0)] - rb, rs - 1)
    first = s[np.minimum(at, len(s) - 1)]
    lo = np.where(before > rs, rs, np.maximum(rs, first))
    hi = np.minimum(re, late)
    return np.column_stack([lo, hi, rk])[hit & (hi > lo)]


def wrapped_in(sh, notes, tracks, ppq, keys, notes_tracks):
    """sh's (notes, tracks) with its notes past the keys added back inside ("Keep notes made there" + "Wrap them
    around", user 2026-11-08): key mod the project's keys (one over the top = key 0, one under key 0 = the top key),
    cut at beat 0 like cut_back (nothing before it, a note across it starts on it). Made from the same remembered
    lifted notes as edge_made / App.offrange_tracks (engine.off_part); added before the channels / overlaps are
    worked out, so they're fixed like any other note in that mode."""
    out = past_edges(sh, keys, ppq)
    if out is None:
        return notes, tracks
    lifted, up, room, later = out
    with rows_up_to(room - 1):
        n, t = notes_tracks(lifted, room)
    if not len(n):
        return notes, tracks
    n, t = off_part(n, t, up, keys, round(later * ppq))
    n, ok = wrap_landing(n, keys)
    if not ok.any():
        return notes, tracks
    if tracks is None and t is None:
        both, tt = np.concatenate([np.asarray(notes).reshape(-1, n.shape[1]), n]), None
    else:
        a = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64).ravel()
        b = np.zeros(len(n), np.int64) if t is None else np.asarray(t, np.int64)[ok]
        both, tt = np.concatenate([np.asarray(notes).reshape(-1, n.shape[1]), n]), np.concatenate([a, b])
    order = np.argsort(both[:, 0], kind="stable")
    return both[order].astype(np.asarray(notes).dtype if len(notes) else both.dtype, copy=False), \
        None if tt is None else tt[order]


def all_notes(shapes, ppq, keys, mode, split, all16, notes_tracks, quiet=False, rounds=False, order=False,
              keep=False, busy=None, own=None, stash=None, overlaps=None, wrap=False):
    """Every shape's notes together, overlaps / channels worked out: (colours each shape asks for, channel 10 used
    (slot k = channel k, slot_track_channel), rendered, slot count, {Hz bass's number: {voice: slot}} for those with
    their own channel per note (render voice_slots)). all16: the project's "Use channel 10" (every
    shape may use it, not only pictures). Touches no Tk: also made in a background thread (App.notes_rested).
    quiet: every mask's own notes left out (what the masks show: App.pattern). rounds: the project's "New channels
    each Colours round" (App.rounds_apart). order: its "Channels 1-16 in order" (App.in_order). keep: Settings →
    General "Overlapping shapes keep their colours" (render keep). busy: per shape the times its CC lines are
    sent (App.cc_busy: render busy). own: {id: channel} of the tracks added by hand. A shape laid on tracks
    (placed_on: drawn / pasted with a track highlighted) has its notes on them (colour k on the k-th, colours in
    its tracks' order; more colours than tracks: the last one's) in every mode, its own overlaps fixed as if alone
    (Single channel; As drawn: kept), other shapes' never (Domino lets two notes share a key in a track); the other
    shapes never step round them (nor take tracks added by hand). Also returns {shape's number: how many colours
    it has} for those (App.fit_into). stash: a dict given = what lay_again needs kept in it. overlaps: how overlaps
    are fixed (App.overlap_rule: Single's split around, Multi's Remove overlaps; render overlaps)."""
    team_laps = []
    across = bool(overlaps and overlaps.get("across")) and mode == "auto"  # (Multi channel's Remove overlaps)
    teams = between_turns(shapes, team_laps)
    # (each Colours shape's notes made with their rounds, then taken off the tracks: render's rounds; a merged shape's
    # parts' rounds too, merge.part_rows; an Add between step = one channel, its round = its group's: in_rounds)
    rounds = rounds and mode == "auto"
    took = [rounds and has_colours(sh) and team is None for sh, team in zip(shapes, teams)]
    wide = all16 and mode == "auto"  # (a Nested / Tiles / Ripples fill with Colours: its pairs go round 16 colours)
    shapes_as = [dict(sh, _most=ALL_COLOURS) if wide and has_pattern(sh) and sh.get("cycle") else sh for sh in shapes]
    asked = [dict(sh, _rounds=True) if r else sh for sh, r in zip(shapes_as, took)]
    got = [notes_tracks(s) for s in asked]
    if wrap:  # ("Wrap them around": the notes past the keys put back inside, App.outgoing)
        got = [wrapped_in(s, n, t, ppq, keys, notes_tracks) for s, (n, t) in zip(asked, got)]
    laps = [np.asarray(t, np.int64) // ROUND if r and t is not None else None for (_, t), r in zip(got, took)]
    got = [(n, t % ROUND) if lap is not None else (n, t) for (n, t), lap in zip(got, laps)]
    if rounds:
        laps = [np.full(len(n), tl, np.int64) if tl is not None else lap
                for (n, _), lap, tl in zip(got, laps, team_laps)]
    # (Channel per strip: each strip of a pasted track a round of its own, the first keeping its channels; strips.py)
    strips = [mode == "auto" and has_strips(sh) and t is not None for sh, (_, t) in zip(shapes, got)]
    laps = [strip_laps(n, t, gap_of(sh)) if s else lap for sh, (n, t), lap, s in zip(shapes, got, laps, strips)]
    # (pieces sliced from one paste with the same setting: their strips worked out together and given their
    # channels as one, so a strip cut in two stays one channel, hunt 2026-10-23)
    together = [(sh["paste"], gap_of(sh)) if s and isinstance(sh.get("paste"), str) else None
                for sh, s in zip(shapes, strips)]
    for key in {k for k in together if k is not None}:
        mine = [i for i, k in enumerate(together) if k == key]
        if len(mine) > 1:
            joined = strip_laps(np.concatenate([got[i][0] for i in mine]),
                                np.concatenate([np.asarray(got[i][1], np.int64) for i in mine]), key[1])
            at = np.cumsum([0] + [len(got[i][0]) for i in mine])
            for k, i in enumerate(mine):
                laps[i] = joined[at[k]:at[k + 1]]
    # (a mask whose own notes don't sound: none, mask.py)
    off = [silent(sh) or quiet and is_mask(sh) for sh in shapes]
    got = [(n[:0], None if t is None else t[:0]) if o else (n, t) for o, (n, t) in zip(off, got)]
    laps = [lap[:0] if o and lap is not None else lap for o, lap in zip(off, laps)]
    # (shapes laid on tracks: taken out of the channel work, put on their tracks below)
    into = [placed_on(sh, own) for sh in shapes]
    # ("New channels each Colours round": each round's colours are colours of their own on the tracks too, laid
    # after the round before; user 2026-10-26: a copy pasted into a track went round the same ones)
    inside = {i: (got[i][0], np.asarray(got[i][1], np.int64) + laps[i] * ROUND) if rounds and laps[i] is not None
              and got[i][1] is not None else got[i] for i, p in enumerate(into) if p}
    got = [(n[:0], None if t is None else t[:0]) if i in inside else (n, t) for i, (n, t) in enumerate(got)]
    laps = [lap[:0] if i in inside and lap is not None else lap for i, lap in enumerate(laps)]
    # a shape never has more than 15 colours (user: a MIDI player shows no more either): the extra ones are
    # merged into the last (pasted notes keep their tracks)
    # (a Hz bass layer's notes clashing with another's: another channel, not another colour asked for)
    # (a Hz bass with its own channel per note: one channel a voice, not a colour asked for)
    wanted = [0 if t is None or "notes" in sh or own_channels(sh.get("hz")) else len(np.unique(np.asarray(t) % CLASH))
              for sh, (_, t) in zip(shapes, got)]
    most = most_colours(all16)
    got = [(n, capped_colours(t % CLASH, most) + t // CLASH * CLASH if w > most else t) for (n, t), w in zip(got, wanted)]
    # (pictures using channel 10 too: one setting for the project, kept with every picture; an Apply mask copy made
    # while channel 10 was used keeps it in use, hunt)
    # (Turn into plain notes / a path's Turn into plain shapes: "solid", other shapes step round its channels)
    pins = [pinned_slots(sh, mode) and ("solid" if sh.get("made") in ("plain", "path") else True) for sh in shapes]
    rules = [slot_rule(sh) if pin else None for sh, pin in zip(shapes, pins)]
    use10 = any(r is True for r in rules) or any(shapes[i].get("slots10") is True for i in inside)
    # (pinned tracks made with channel 10 skipped stay on their channels while it's used: mask.as_used)
    got = [(n, as_used(t, r, use10 or all16)) for (n, t), r in zip(got, rules)]
    # (with all16 the shapes use channel 10 too, but only as their 16th: render's "all"; in order: "order")
    # (merged shapes and Nested / Tiles / Ripples fills take channels by colour number, not by which colour starts
    # first: a flipped or moved copy keeps its colours, user; pasted notes by track: a pasted song's channels stay
    # in their order, user 2026-10-22)
    # (pasted notes' passengers: each track's on its slot; a Domino paste's in every mode, hunt 2026-11-01)
    voice_slots = {i: None for i, sh in enumerate(shapes) if own_channels(sh.get("hz")) or "cc" in sh} \
        if mode == "auto" else {i: None for i, sh in enumerate(shapes) if "cc" in sh and pasted_notes(sh)}
    note_slots = {i: None for i, (sh, s) in enumerate(zip(shapes, strips)) if s and "cc" in sh}
    # (a sliced piece with Colours: by colour number too, so it keeps the whole's colours, user 2026-11-02: a
    # piece starting on the 3rd colour began again from the 1st)
    ordered = [bool(sh.get("merge")) or has_pattern(sh) or "notes" in sh
               or bool(sh.get("cut") or sh.get("cut_of")) and has_colours(sh, turns=True) for sh in shapes]
    # (a recoloured shape's colours: each counted as starting where it did before, so they keep their channels)
    backs = [recolour_back(s, notes_tracks, dict(s_as, _rounds=True) if r else s_as, n, t)
             if mode == "auto" and not o and t is not None and is_recoloured(s) else colours_back(s)
             for s, s_as, r, o, (n, t) in zip(shapes, shapes_as, took, ordered, got)]
    # (a one-colour piece as one track too, so its whole's pieces share it: a tiny Lasso piece's note on its
    # neighbour's pushed the pieces onto another row with keep colours, partial sweep 11-02)
    rendered, slots = render([n for n, _ in got], mode, split,
                             [np.zeros(len(n), np.int64) if t is None and mode == "auto" and len(n) and cut_group(sh)
                              else t for (n, t), sh in zip(got, shapes)],
                             [tracks_apart(sh) or is_recoloured(sh) for sh in shapes],  # (Split: from one colour)
                             pins, ("order" if order else "all") if all16 else use10, teams,
                             # (a cut shape's pieces like a paste's: one channel per colour across them, so a piece
                             # without the first colours keeps the whole's channels, partial sweep 11-02)
                             [sh.get("paste") if "notes" in sh else ("cut", cut_group(sh)) if cut_group(sh) else None
                              for sh in shapes],
                             # (a Domino paste keeps its own overlaps outside Single channel: user 2026-10-30; not
                             # with Remove overlaps ticked, user 2026-11-06)
                             [is_sustained(sh) or sh.get("held") is True or mode == "auto" and keeps_laps(sh)
                              and not across for sh in shapes],
                             backs, ordered, laps, keep,
                             [s or r and not has_colours(sh, turns=True) for sh, r, s in zip(shapes, took, strips)],
                             voice_slots,
                             busy, together, note_slots, [pasted_notes(sh) for sh in shapes],
                             # (keep colours: a cut shape's pieces move as one shape, partial sweep 11-02)
                             groups=[cut_group(sh) for sh in shapes] if keep else None,
                             overlaps=overlaps and {"way": overlaps.get("way", "stretch")})
    way = (overlaps or {}).get("way", "stretch")
    if stash is not None:  # (App.fit_into's laying again: lay_again)
        stash.update(inside=inside, rendered=rendered, slots=slots, base10=any(r is True for r in rules),
                     all16=all16, mode=mode, overlaps=overlaps if across else None, way=way)
    rendered, slots, need, laid = lay_inside(rendered, slots, inside, shapes, own, use10 or all16, mode, way)
    emptied = frozenset()
    if across and len(rendered):  # (Remove overlaps: after the laid shapes are in, so your tracks take part too)
        rendered, emptied = cut_across(rendered, shapes, overlaps)
    voice_slots.update(laid)
    for i, s in note_slots.items():  # (Channel per strip with CCs: each strip's slot + time, App.cc_messages)
        if s is not None and len(s):
            voice_slots[("strips", i)] = strip_spans(got[i][0], got[i][1], s)
    if stash is not None:
        stash["voice_slots"] = voice_slots
    return (wanted, use10 or all16, rendered, slots, {i: s for i, s in voice_slots.items() if s is not None}, need,
            emptied)


def cut_across(rendered, shapes, overlaps):
    """all_notes' Remove overlaps step (engine.remove_across) -> (notes, the automatic tracks it left without notes).
    The order: the track list's as it is BEFORE the cut (user 2026-11-06, hunt: an emptied track no longer counted for
    your tracks' places, so yours moved down and the list no longer showed who won); those emptied tracks keep
    counting as tracks with notes everywhere (App.cut_emptied: list, exports), so nothing moves. The loaded MIDI's
    tracks first, in their listed order (overlaps "files")."""
    held = np.flatnonzero([is_sustained(sh) or sh.get("held") is True for sh in shapes])
    before = np.unique(rendered[:, 4])
    files = overlaps.get("files") or ()
    out = remove_across(rendered, np.isin(rendered[:, 5], held), overlaps,
                        lambda n: track_ranks(n[:, 4], overlaps.get("own") or (), files=files)[0])
    gone = np.setdiff1d(before, np.unique(out[:, 4]) if len(out) else before[:0])
    return out, frozenset(int(s) for s in gone.tolist() if s < OWN0)


def lay_inside(rendered, slots, inside, shapes, own, use10, mode, way="stretch"):
    """all_notes' last part: the shapes laid on tracks ({number: (notes, colours)}) put on them, after the other
    shapes' rendered notes: (rendered, slot count, {number: how many colours it has}, {number: {pasted track: slot}}
    for laid pasted notes with CC events: render's voice_slots, Multi channel only, App.cc_messages)."""
    need, laid = {}, {}
    if not inside:
        return rendered, slots, need, laid
    parts, together = [rendered], []
    for i, (n, t) in inside.items():
        sh = shapes[i]
        colour = (np.unique(np.asarray(t, np.int64), return_inverse=True)[1].ravel() if t is not None and len(n)
                  else np.zeros(len(n), np.int64))
        need[i] = int(colour.max()) + 1 if len(n) else 0
        ids = placed_on(sh, own)
        if not ids:
            continue
        on = np.array(laid_slots_of(sh, ids, own, use10), np.int64)
        # (Single channel / As drawn: every colour on its first track, hunt 2026-10-26; an Extract keeps its channels,
        # pasted notes their tracks: user 2026-10-29, "you copy from Domino is what you'll get")
        kept = colours_apart(sh, mode)
        if not kept:
            colour = np.zeros_like(colour)
        if kept and "cc" in sh and t is not None and len(n):  # (each pasted track's events: its colour's)
            tracks = np.unique(np.asarray(t, np.int64))
            laid[i] = {int(u): int(on[min(k, len(ids) - 1)]) for k, u in enumerate(tracks.tolist())}
        # (a Domino paste keeps its own overlaps in Multi channel too, as copied: user 2026-10-30, round trip)
        fix = mode != "raw" and not is_sustained(sh) and sh.get("held") is not True and not (
            mode == "auto" and keeps_laps(sh))
        if mode == "single" and fix:  # (Single channel: no overlaps between the shapes on a track either, user
            together.append(in_track(n, on[np.minimum(colour, len(ids) - 1)], i, False))  # 2026-10-26)
        else:
            parts.append(in_track(n, on[np.minimum(colour, len(ids) - 1)], i, fix, way))
    if together:  # (pinned on one of Spiderweb's tracks: with the notes already there)
        together = np.concatenate(together)
        there = np.isin(rendered[:, 4], np.unique(together[together[:, 4] < OWN0, 4]))
        if there.any():  # (sustained / held shapes and pictures keep their overlaps, as in render: hunt 10-26)
            keeps = np.array([is_sustained(s) or s.get("held") is True or picture_like(s) for s in shapes] + [True])
            there &= ~keeps[np.minimum(rendered[:, 5], len(shapes))]
        if there.any():
            parts[0] = rendered[~there]
            together = np.concatenate([rendered[there], together])
        parts.append(fix_rows(together, way))
    rendered = np.concatenate(parts)
    return rendered, max(slots, auto_count(rendered[:, 4])), need, laid  # (automatic tracks shapes were pinned on, B11)


def lay_again(made, stash, shapes, own):
    """all_notes' answer again after App.fit_into changed only where laid shapes go (stash: what all_notes kept),
    without making every shape's notes again (hunt 2026-10-26: a paste into a track made the whole song twice); None
    when that isn't enough (a laid shape left / joined the others, or channel 10's use changed: all_notes again)."""
    inside = stash.get("inside")
    if inside is None or {i for i, sh in enumerate(shapes) if placed_on(sh, own)} != set(inside):
        return None
    use10 = stash["base10"] or any(shapes[i].get("slots10") is True for i in inside)
    if (use10 or stash["all16"]) != made[1]:
        return None
    rendered, slots, need, laid = lay_inside(stash["rendered"], stash["slots"], inside, shapes, own, made[1],
                                             stash["mode"], stash.get("way", "stretch"))
    emptied = frozenset()
    if stash.get("overlaps") and len(rendered):  # (Remove overlaps after laying again too: hunt 2026-11-06, the
        rendered, emptied = cut_across(rendered, shapes, stash["overlaps"])  # overlaps stayed till the next edit)
    return made[0], made[1], rendered, slots, {**made[4], **laid}, need, emptied


def in_track(notes, slots, owner, fix, way="stretch"):
    """A shape's notes on tracks added by hand (slots: each note's): rendered rows (fix: its own overlaps on each
    track fixed, fix_rows)."""
    rows = np.zeros((len(notes), 6), np.int64)
    rows[:, :4], rows[:, 5], rows[:, 4] = notes, owner, slots
    return fix_rows(rows, way) if fix else rows


def as_heard(sh, mode, rule):
    """sh as its notes are made in this Channels mode: a notes shape whose own overlaps the mode fixes carries the
    way ("_heard", engine.heard_way / heard_notes), so it's made, kept and looked up apart from the other modes."""
    way = heard_way(sh, mode, rule)
    return dict(sh, _heard=way) if way else sh


def fix_rows(rows, way="stretch"):
    """Rendered rows with their overlaps on each track fixed: cut + stretch (resolve_overlaps), or split around
    (way "split": Single's tick / Multi's Remove overlaps, hunt 2026-11-06: laid shapes were always stretched)."""
    if not len(rows):
        return rows
    rows = rows.copy()
    ids, rows[:, 4] = np.unique(rows[:, 4], return_inverse=True)  # (small numbers while fixing: OWN0's are huge)
    rows = split_overlaps(rows) if way == "split" else resolve_overlaps(rows)
    rows[:, 4] = ids[rows[:, 4]]
    return rows


def recolour_back(sh, notes_tracks, made_as, notes, tracks):
    """render's back for a recoloured shape: each note's start counted as its colour's first start before Recolour
    (unflipped when gate flipped), so Amount 0 gives the channels Off does (recolour.py)."""
    bn, bt = notes_tracks(without_recolour(made_as))
    if bt is None or not len(bn):
        return colours_back(sh)
    back = colours_back(sh)  # (new colours, Split's too: counted by where they start unflipped)
    return counted_starts(notes, tracks, first_starts(bn, bt, unflipped_starts(bn) if back else None),
                          unflipped_starts(notes) if back else None)


def sends_cc(sh):
    """A Hz bass shape sending CC lines."""
    return sh.get("kind") is not None and has_cc(sh.get("hz") or {})


def cc_busy(shapes, ppq, mode):
    """all_notes' busy: per shape None, or {track: (start, end)} the times its CC lines are sent (hz_cc.cc_spans at the
    Hz bass's own BPM, as its notes): Multi channel keeps its channel for them, on every key, so no other shape
    takes it while its lines (run-on too) change it (user 2026-10-21: either split). None otherwise."""
    if mode != "auto":  # (a Domino paste's tracks: as in Multi channel in every mode, hunt 2026-11-01)
        if not any("cc" in sh and pasted_notes(sh) for sh in shapes):
            return None
        return [passenger_spans(sh, ppq) or None if "cc" in sh and pasted_notes(sh) else None for sh in shapes]
    if not any(sends_cc(sh) or "cc" in sh for sh in shapes):
        return None
    # (pasted notes' CCs too: each track's channel held from its first event to its last, hunt 2026-10-22: a volume
    # change after a track's last note changed the shape that took its channel next)
    return [cc_spans(sh, ppq, float(sh["hz"]["bpm"])) or None if sends_cc(sh) else
            passenger_spans(sh, ppq) or None if "cc" in sh else None for sh in shapes]


def cc_texts(shapes, mode):
    """What cc_busy comes from, for App.notes_key (cheap: the times are worked out in the background): Auto's run-on
    and each CC Hz bass's whole text (shape_key leaves its lines out), None when cc_busy gives None."""
    if mode != "auto" or not any(sends_cc(sh) for sh in shapes):
        return None
    return (hz_cc.auto_ring, tuple(json.dumps(dict(sh, hz={k: v for k, v in sh["hz"].items() if k != "_memo"}),
                                              sort_keys=True, default=repr) if sends_cc(sh) else None
                                   for sh in shapes))


class App(ProjectFiles, CustomPanel, NestedPanel, TilesPanel, RipplesPanel, ColoursPanel, RecolourPanel, SnapPanel, MaskPanel, PolygonPanel, FreehandPanel, GlissPanel, LassoPanel, FunnelPanel, TumourPanel, PatternPanel, TextPanel, JoinSplit, BetweenGroups,
          Playback, Pictures, HistoryPanel, ScrollingSide, tk.Tk):
    _bg_notes = False  # the notes are being made in the background after an edit (shapes_changed, notes_wait.py)
    _recolour_owed = False  # the panel's Recolour row waits for those notes (panel_recolour.sync_recolour)
    _bg_serial = None  # (the event that started it: its other bindings don't wait for it)
    _bg_carry = None  # (the selected shapes whose old notes go along meanwhile: roll_draw.carry_from)
    _job_stash = None  # (all_notes' stash from the notes made in the background: notes_made, for lay_again)
    stale_ok = 0  # > 0: the view is being drawn: the notes as they are on screen, not waited for

    def __init__(self, autosave=AUTOSAVE):
        mac_buttons()  # (Mac's Tk 8.6: right / middle button swapped back, before any binding)
        super().__init__()
        errors.install(self)
        notes_wait.install(self)  # (a big project's notes made in the background after an edit: shapes_changed)
        look.apply(self)  # (the dark look, before any widget is made)
        palettes.start()  # (the note colours picked in Settings)
        self.title(tr("app.spiderweb", VERSION=VERSION))
        try:  # title bar + taskbar icon; True = every other window (drawer, Help, ...) gets it too
            self.icons = [tk.PhotoImage(file=os.path.join(ICONS, f"icon-{n}.png")) for n in (16, 24, 32, 48, 64, 256)]
            self.iconphoto(True, *self.icons)
            from files.system import small_title_icon  # (title bars: the two-ring icon, on every window once shown)
            small, big = os.path.join(ICONS, "icon-small.ico"), os.path.join(ICONS, "icon.ico")
            self.bind_class("Toplevel", "<Map>", lambda e: e.widget is e.widget.winfo_toplevel()
                            and small_title_icon(e.widget, small, big), add="+")
            self.bind("<Map>", lambda e: e.widget is self and small_title_icon(self, small, big), add="+")
        except tk.TclError:  # icons missing: Tk's own icon
            pass
        s = self.scale = self.winfo_fpixels("1i") / 96  # match Windows display scaling
        self.geometry(f"{int(1400 * s)}x{int(820 * s)}")
        # (never bigger than the screen: a big Size on a small screen put the window's edges off it)
        self.minsize(min(int(1000 * s), int(self.winfo_screenwidth() * 0.9)),
                     min(int(600 * s), int(self.winfo_screenheight() * 0.85)))
        self.autosave_path = autosave

        self.shapes = []
        self.sel = None     # the shape the panel shows (the last one clicked)
        self.sels = set()   # every selected shape
        self.hidden_shapes = set()  # shapes on tracks the track list hides (track_view): not drawn, not picked
        self.parts = set()  # the selected funnel's highlighted lines / curves (roll_funnel.py)
        self.stroke = None  # the selected custom shape's picked stroke (roll_live.py)
        self.part_main = None  # the highlighted part that was clicked (the others are its linked curves)
        self.clipboard = None
        self.tempo_clip = None  # the Tempo pane's copy (tempo_pane.copy): its own, never the Windows clipboard
        self.seen_clip = None  # the Windows clipboard's copy count at Spiderweb's last copy (shared_clip)
        self.own_domino_copy = None  # ... and at its last Copy to Domino (paste_from_domino)
        self.companion = None  # drawing on Domino's piano roll (window/domino_companion.py) while on
        self.companion_tops = ()  # ... its windows: shortcuts work in them as in the main window
        self.stroke_clip, self.clip_kind, self.stroke_pastes = None, None, 0  # a copied stroke (roll_live.py)
        self.playhead = 0.0  # beat of the play line
        self.out = MidiOut(self.make_live, self.make_kestrel)
        self.font_failed = None  # (make_live: the soundfont that couldn't be read)
        self.player = Player(self.out)
        from files.ahead_play import AheadPlay
        self.ahead_play = AheadPlay(self)  # (Rendered ahead's sound: files/ahead_play.py)
        self.play_voices = 1000  # Built-in BASSMIDI's voice limit (user; with the window settings)
        self.play_guard = tk.BooleanVar(self, value=False)  # lowered by itself while overloaded (user: off to start)
        self.play_limiter = tk.BooleanVar(self, value=False)  # (off to start, as in the driver it copies)
        # 31-EDO: channel 10 as tuned rows in a piano sound (user 2026-11-10; off = drums, the template's way)
        self.edo10_piano = tk.BooleanVar(self, value=False)
        # synths side by side playing a loaded MIDI (user 2026-10-24: all cores would halt the PC; half to start)
        self.loaded_synths = GROUP_DEFAULT
        self.synths_var = tk.StringVar(self, value=str(GROUP_DEFAULT))
        self.synths_entry = None  # (in Settings → Sound)
        self.domino256 = tk.BooleanVar(self, value=False)  # Settings → General: keys above 127 to / from Domino (mod)
        self.copy_domino = tk.BooleanVar(self, value=False)  # Settings → General: Ctrl+C also copies for Domino
        self.copy_notes = tk.BooleanVar(self, value=False)  # ... and as Spiderweb Notes (files/notes_clip.py)
        self.keep_colours = tk.BooleanVar(self, value=False)  # Settings → General: overlapping shapes move whole to
        # the next row of channels (render keep)
        # Settings → Look "Stacked notes": which of the notes on top of each other shows (roll_draw.stack_rule; a
        # loaded MIDI's painter too: "loud" = roll_loaded.loud_on)
        self.stack_order = tk.StringVar(self, value=STACK_MODES[0])
        # Settings → Note colours "Show hidden note starts / ends" (off by default, user 2026-10-31): Spiderweb's own
        # notes shaded where a note under them starts / ends (roll_draw.hidden_shade; loaded MIDI always has it)
        self.hidden_shade = tk.BooleanVar(self, value=False)
        # Settings → General "Space past the keys" (keyboard heights past each end, 0 = off) and "Space before the
        # start" (bars before beat 0, 0 = off): the main piano roll may scroll there (user 2026-11-06; roll limits)
        self.space_keys = tk.IntVar(self, value=0)
        self.space_bars = tk.IntVar(self, value=0)
        # ... "Keep notes made there" (off; user 2026-11-08): shapes keep the notes they make in that space, kept
        # apart from the real notes (App.offrange_tracks), so nothing that leaves Spiderweb ever gets them
        self.keep_offrange = tk.BooleanVar(self, value=False)
        self.offrange_opacity = tk.IntVar(self, value=50)  # (how see-through they're drawn, %: roll_draw.OFF_FADE)
        self.wrap_offrange = tk.BooleanVar(self, value=False)  # ("Wrap them around": App.outgoing)
        # ("Show wrapped notes": settings.WRAP_SHOWN "selected" / "always" / "off"; App.wrapped_rows)
        self.show_wrapped = tk.StringVar(self, value="selected")
        self._wrapped = None  # (App.wrapped_rows' last answer, keyed)
        self.wrap_version = 0  # (+1 each time wrapped_rows' answer changes: the roll's picture key)
        self._outgoing = None  # (App.outgoing's last (key, notes))
        self.off_all = self.off_drawn = NO_NOTES  # (offrange_rows; off_drawn = the ones the piano roll shows)
        # (off_all as the masks' preview shows it: ghosts (velocity -1) + the parts that show; None = no mask over
        # them; off_paint = that, filtered like off_drawn: what roll_draw.off_rects paints; App.masked_offrange)
        self.off_masked = self.off_paint = None
        self.export_held = False  # (W held: the piano roll shows what the export gets, export_view)
        self.off_version = 0  # (+1 each time off_drawn is set: the roll's picture key)
        # (+1 each time rendered / off_all are made again: outgoing / wrapped_rows / scrub_wrapped keys; an array's
        # id alone can be handed to a new one once the old is gone)
        self.made_version = 0
        # Kestrel (GPU)'s own settings (Settings → Sound; files.kestrel.Kestrel's keywords): all but "lower" are used
        # when it opens (a change = opened again at the next Play: fresh_builtin)
        self.kestrel_opts = dict(KESTREL_DEFAULTS)
        self.kestrel_vars = {"voices": tk.StringVar(self, value=f"{KESTREL_DEFAULTS['voices']}"),
                             **{k: tk.BooleanVar(self, value=KESTREL_DEFAULTS[k]) for k in ("lower", "dc_blocker")},
                             # (user 2026-11-02: a list (by the connector's number) + a dB box)
                             "limiter": tk.IntVar(self, value=KESTREL_DEFAULTS["limiter"]),
                             "ceiling_db": tk.StringVar(self, value=f"{KESTREL_DEFAULTS['ceiling_db']:g}"),
                             "block": tk.IntVar(self, value=KESTREL_BLOCKS.index(KESTREL_DEFAULTS["block"])),
                             # (13g: a list by place, a number box as text, the card as cards()' "backend:name")
                             "interpolation": tk.IntVar(self, value=KESTREL_DEFAULTS["interpolation"]),
                             "min_velocity": tk.StringVar(self, value=str(KESTREL_DEFAULTS["min_velocity"])),
                             "card": tk.StringVar(self, value="")}
        self.overload, self.overload_text = Overload(), ""  # (red on the status line while it can't keep up)
        # 13f: slow down to keep the sound going, a tick each (user 2026-10-29: off at first); files/slow_down.py
        self.slow_kestrel = tk.BooleanVar(self, value=False)
        self.slow_builtin = tk.BooleanVar(self, value=False)
        self.slow_down, self.slowed_text = SlowDown(), ""
        # Rendered ahead's settings (files/render_ahead.py; Settings → Sound): engine by its saved name, how far
        # ahead in seconds, Slow down (user 2026-11-02: off = a part not made in time is skipped, on = play waits)
        from files import render_ahead
        self.ahead_engine = tk.StringVar(self, value=render_ahead.engines()[0])
        self.ahead_seconds = render_ahead.DEFAULT_SECONDS
        self.ahead_var = tk.StringVar(self, value=str(render_ahead.DEFAULT_SECONDS))
        self.slow_ahead = tk.BooleanVar(self, value=False)
        # (R1b: its engines' own settings, render_ahead.DEFAULTS; the lists' vars hold their places)
        self.ahead_opts = dict(render_ahead.DEFAULTS)
        self.ahead_vars = {k: tk.IntVar(self) if k in ("block", "interpolation", "limiter") else
                           tk.BooleanVar(self) if k in render_ahead.BOOLS else tk.StringVar(self)
                           for k in render_ahead.DEFAULTS}
        self.ahead_entries = {}
        self.show_ahead_opts()
        self.builtin_window = self.voices_entry = None  # (window/builtin_settings.py)
        self._play_job = None
        self._scrub_held = {}
        self.defaults = dict(SHAPE_DEFAULTS)
        self.custom_defaults = dict(CUSTOM_DEFAULTS)  # inside fill and spam gate for new custom shapes
        self.custom_shape = "Circle"  # the library shape new custom shapes are made of
        self.funnel_defaults = dict(FUNNEL_DEFAULTS)  # settings for new funnels
        self.free_smooth = SMOOTH_DEFAULT  # how much new freehand strokes are made perfect (smooth.py)
        self.text_defaults = dict(TEXT_DEFAULTS)  # settings for new text (the last ones used)
        self.polygon_defaults = dict(POLYGON_DEFAULTS)  # points / kind of new polygons (polygon.py)
        self.gliss_defaults = dict(GLISS_DEFAULTS)  # grids new Glissando runs pick their step from (the last used)
        self._rows = {"last": True, "line_fill": False, "free": False, "gliss": False, "tumour": False,
                      "pattern": False, "text": False, "custom": False, "funnel": False}  # optional panel parts
        self.drawer = None
        self.claw_window = None
        self.claw_pos = ""  # where the claw machine window was last ("+x+y", remembered in the autosave)
        self.strum_window = None
        self.strum_pos = ""  # (the same for the strum window)
        self.trim_window = None
        self.trim_pos = ""  # (and the Gate trim window)
        self.chop_window = None
        self.chop_pos = ""  # (and the chop window)
        self.path_window = None  # Copy along a path (path_window.py)
        self.sustain_window = None  # Sustain… (sustain_window.py)
        self.subtract_window = None  # Gate subtract… (subtract_window.py)
        self.extend_window = None  # Gate extend… (extend_window.py)
        self.merge_window = None  # Gate sensitive merge… (merge_window.py)
        self.shares_window = None  # Recolour's Shares… (shares_window.py)
        self.stir_window = None  # Recolour's Stir… (stir_window.py)
        self.liquify_window = None  # Recolour's Liquify… (liquify_window.py)
        self.warp_window = None  # Recolour's Warp… (warp_window.py)
        # (big shapes' quick Stir / Warp / Liquify work is kept only while one of their windows is open: ~210-315 MB
        # at 2 M notes otherwise held all the time; user 2026-11-10)
        from notes.recolour import keep_while
        keep_while(self.colour_window_open)
        self.stir_open = set()  # the Stir window's parts shown unfolded ("area", "flips"; autosave)
        self.fill_window = None  # Fill gaps… (fill_window.py)
        self.lines_window = None  # Flip each line… (lines_window.py)
        self.melody_window = None  # Fill with tempo jumps… (melody_window.py)
        self.strips_window = None  # Channel per strip… (strips_window.py)
        self.path_pos = ""
        self.hz_window = None  # the Hz bass window (hz_window.py)
        self.image_window = None  # image to notes (image_window.py; opened by the Picture tool)
        self.image_pos = ""  # its size and place
        self.image_last = None  # (picture file, settings) last used there, so it opens with them again
        self.image_rate = 4e-6  # seconds a grid cell takes to make there (measured on each big one: its question)
        self._pictures = {}  # placed pictures' files read: (path, mtime, size) -> picture.Picture (picture_for)
        self.picture_owners, self.picture_pal = np.zeros(0, bool), None  # (shapes_changed: for the piano roll)
        self.picture_use10 = False  # (shapes_changed: pictures use channel 10 too)
        self.hz_clip = None  # notes copied in it (HzWindow.copy_notes)
        self.hz_bend_clip = None  # a note's bend line copied in it (HzBend.copy_bend)
        self.hz_ramp_last = None  # the ready bend shape picked last (R puts it on again: HzBend.quick_ramp)
        self.hz_copied = None  # the Windows clipboard's copy count at its last copy (a later Domino copy wins Ctrl+V)
        self.hz_pos = ""  # its size and place ("WxH+x+y", remembered in the autosave)
        self.hz_view = None  # its zoom + scroll when last closed (HzView.view_state, remembered in the autosave)
        self.hz_fx_h = 0  # its effects pane's height in pixels, dragged by its top edge (0 = as it starts)
        self.hz_loud_h = 0  # ... its loudness pane's (hz_loud.py)
        self.hz_start = None  # the beat picked with the Hz bass tool for a new Hz bass (roll_hz.py)
        self.hz_defaults = {"lo": 48, "hi": 58}  # the keys a new Hz bass repeats
        self.hz_preview = clean_preview({})  # the Hz bass preview's settings (hz_preview.py; with the window's)
        self.synth = None  # the built-in synth (files/synth.py), started when the preview or Built-in BASSMIDI is first used
        self.rendered, self.slot_count, self.voice_slots = NO_NOTES, 0, {}  # (start, end, pitch, velocity, slot, owner) rows
        self.drawn, self.mask_covered = NO_NOTES, set()  # the notes as painted with the masks (mask.mask_view)
        self.drawn_slots = NO_NOTES[:, 4]  # each of them's real track (drawn's own can be strip colours)
        self.pattern = NO_NOTES  # rendered without the masks' own notes: what masks show (= rendered but in Single)
        self.note_counts = []  # notes per shape in rendered
        self.notes_late = False  # the notes are behind the shapes (a drag going on: see shapes_changed)
        self.scrubbing = False  # a number box's label is being dragged (widgets.Scrub): slow notes wait too ("follow":
        # a Shares knob, they follow it in the background)
        self._merge_time, self._merges_owed = 0.0, False  # (remake_merges' last time; put off while scrubbing)
        self.held_scrub = None  # that Scrub (shortcuts / Delete wait, Ctrl+Z cancels it)
        self._trimmed = None  # the oldest steps the 300 limit dropped for the last step (add_undo_step)
        self.rendered_pts = []  # each shape's first point when rendered was made
        self.ppq = 960
        self.undo_stack, self.redo_stack = [], []
        self._redo_kept = None  # (push_undo)
        self._edit_key = None
        self._scrub = None  # the number box being stepped (scrub_step)
        self._loading = False
        self._opening = False  # (load_file filling in the project boxes)
        self._autosave_job = None
        self._autosave_failed = False  # (told once until it works again: ProjectFiles.autosave)
        self._saved_shapes = None  # the shapes as last saved in / opened from a project file (json)
        self.project_file = None  # the project file Save writes into (None: asks), shown in the title
        self._saved_mark = None  # (the newest undo step, file_key()) when last saved / opened; None = unsaved
        self._title = None
        self.big_skip = set()  # big_ask actions ticked "Don't ask again until Spiderweb is closed"
        self._notes_cache = {}
        self._notes_used = {}  # notes cache key -> shapes_gen when last used (forget_old_notes)
        self._notes_top = set()  # notes cache keys asked for by something else than another key's making (kept old)
        self._notes_depth = 0  # notes_tracks calls inside another's making right now
        self._notes_deps = {}  # notes cache key -> the keys asked for while making it (kept with it: forget_old_notes)
        self._notes_asked = []  # (one set per making going on: the keys it asked for)
        self.colours_wanted = []  # per shape, how many colours (tracks) its notes ask for (shapes_changed)
        self.leave_boxes = {}  # side panel box -> what takes its number when it's left (widgets.leave_box)
        self._notes_worked = 0  # shapes whose notes had to be worked out (not remembered)
        self._merge_keys = {}  # merged shapes' parts' settings when last seen (remake_merges)
        self._notes_time = 0.0  # how long that took the last time
        self._make_time = 0.0  # how long the last making of every note took, remembered or not (late_ok)
        self._mask_time = 0.0  # ... and the masks' preview (mask.mask_view)
        self._late_notes = None  # while dragging: notes left until the mouse rests
        self.shapes_gen = 0  # +1 at every shapes_changed (windows working out something slow from the shapes: same?)
        self._coming = None  # notes being made in the background while a drag rests (notes_rested)
        self._made_shapes = []  # app.shapes when the notes were last made (late_ok: the same shapes, maybe more)
        self._position = None
        self._normal_geometry = None

        self.tool = tk.StringVar(value="select")  # (each tool's tip shows when it's picked, see help.py)
        self.slice_mode = tk.StringVar(value="line")  # the Slice tool's ▾: "line" (drag a line) / "shape" (Slice with
        #                                               shape: click the shape to cut with)
        self.lasso_mode = tk.StringVar(value="free")  # the Lasso tool's ▾: "free" (drag) / "poly" (click points) /
        #                                               "magnet" (clings to the notes)
        self.lasso_mode.trace_add("write", lambda *_: hasattr(self, "roll") and self.roll.drop_lasso(unfinished=True))
        self.lasso_mode.trace_add("write", lambda *_: hasattr(self, "lasso_box") and self.sync_lasso())
        self.lasso_width = tk.StringVar(value="10")  # Magnetic: how far from the mouse it looks for a note corner
        #                                              (px; text like every number box: lasso_px reads it)
        # (another mode picked: a loop not closed yet goes, the next press starts one of the new kind; hunt)
        self.tips = Tips(self)
        self.updates = Updates(self)
        self.help_window = None
        self.live = tk.BooleanVar(value=False)  # drawing tools draw into one custom shape (roll_live.py)
        self.mask = tk.BooleanVar(value=False)  # what's drawn becomes a mask (mask.py)
        self.mask_preview = tk.BooleanVar(value=True)  # masks hide what's outside them (off = the pattern drawn whole)
        self.onion = tk.BooleanVar(value=False)  # Multiple tracks: the track list's picked tracks behind the highlighted one
        self.mask_fade_var = tk.StringVar(value="20")  # ... how much of it still shows, % (roll_draw.GHOST_FADE)
        self.mask_defaults = dict(MASK_DEFAULTS)  # a new mask's settings: the last ones set in the panel
        self.draw_tool = "line"  # the drawing tool a double right-click goes back to
        self.snap = tk.StringVar(value=DEFAULT_SNAP)
        self.hz_snap = tk.StringVar(value=DEFAULT_SNAP)  # the Hz bass window's own snap
        self.hz_line = tk.BooleanVar(value=True)  # the Hz bass window shows its red line
        self.hz_fx = tk.BooleanVar(value=False)  # the Hz bass window shows its effects pane
        self.hz_layers = tk.BooleanVar(value=False)  # ... and its layers strip (off to start: plain Hz bass users, user)
        self.hz_tools = {}  # ... its tools button: {"shown": the drawing tool on it, "pins": [...], "tool": in use}
        self.hz_loud = tk.BooleanVar(value=False)  # ... and its loudness pane (off to start, user)
        self.show_lines = tk.BooleanVar(value=True)
        self.show_notes = tk.BooleanVar(value=True)
        self.channel_mode = tk.StringVar(value="single")
        self.channel_split = "key"  # what counts as an overlap for Multi channel (engine.SPLITS)
        self.use_ch10 = tk.BooleanVar(value=False)  # Multi channel uses channel 10 too: 16 colours (all16)
        self.ch10_order = tk.BooleanVar(value=False)  # with Use channel 10: channels 1-16 in order (in_order)
        self.new_rounds = tk.BooleanVar(value=False)  # each Colours round on channels of its own (rounds_apart)
        self.pad_rounds = tk.BooleanVar(value=True)  # with it: empty tracks for a round's unused channels (pad_tracks)
        # (overlap fixing, user 2026-11-06: overlap_rule; saved with the project, no undo step like the ticks above)
        self.single_split = tk.BooleanVar(value=False)  # Single channel: split around instead of cut + stretch
        self.multi_cut = tk.BooleanVar(value=False)  # Multi channel: Remove overlaps between tracks too
        self.multi_later = tk.BooleanVar(value=False)  # ... later tracks win (off: earlier tracks win, the default)
        self.multi_stretch = tk.BooleanVar(value=False)  # ... cut + stretch like Single channel (off: split around)
        self.cut_emptied = frozenset()  # automatic tracks Remove overlaps took every note of (still counted: filled_slots)
        self.channels_folded = tk.BooleanVar(value=False)  # Project's Channels rows folded to one grey line (autosave)
        self._used_tracks = (None, 0)  # (rendered, how many slots have notes: update_status)
        self.keys = 128  # the project's key range: 0 .. keys - 1 (paths.KEYS)
        self.loaded = None  # a loaded MIDI file (Load MIDI: notes.loaded.LoadedMidi), its notes drawn under the shapes
        self.midi_link = None  # the project's MIDI file (notes.loaded.clean_link): kept while it isn't loaded too
        self.loaded_play = None  # the loaded MIDI playing along (files.play_loaded.LoadedPlayer)
        self.loaded_group = None  # ... its synths on Built-in BASSMIDI (files.synth.LiveGroup)
        self._instruments_job = None  # ... its instruments loading before it plays (start_loaded)
        self._kestrel_job = None  # Kestrel (GPU) starting on a thread for a play (kestrel_first)
        self._kestrel_made = None  # ... started, waiting for MidiOut.open: (out, error, soundfonts)
        self._thin_seen = (0, 0.0)  # (its thinned count, when it last grew: "Overloaded")
        self._thin_hist = collections.deque()  # (a loaded MIDI's (time, notes left out, sent): notes_left_text)
        set_key_top(self.keys)
        self.keys_var = tk.StringVar(value=str(KEYS[0]))
        self.edo31 = tk.BooleanVar(value=False)  # 256 keys shown as 31-EDO (the look only: edo())
        self.edo31_audio = tk.BooleanVar(value=False)  # ... and played that way (Built-in BASSMIDI)
        self.edo31_loaded = tk.BooleanVar(value=False)  # ... a loaded MIDI too, forced (App.edo_loaded)
        self.edo31_lanes = tk.BooleanVar(value=True)  # ... Multi channel only on each group's first channel (sync_edo_lanes)
        self.show_history = tk.BooleanVar(value=False)  # the History panel (off on a fresh start, like the velocity pane)
        self.show_velocity = tk.BooleanVar(value=False)  # off on a fresh start: turning it on shows its tip
        self.vel_tool = tk.StringVar(value="line")
        self.midi_device = tk.StringVar(value=DEFAULT_DEVICE)
        self._vel_height = int(170 * s)
        self.tempo = empty_tempo()  # the Tempo pane's line (notes/tempo.py), saved with the project, undo steps too
        self.own_tracks = []  # tracks added by hand (files/own_tracks.py), saved + undone like the tempo line;
        # replaced (never changed in place) on every change, so the track list sees it by identity
        self.own_next = 0  # the next own track's id (never one used before in this project: own_tracks.new_id; saved)
        # a new project's empty Track 1 (user 2026-10-26, like Domino's empty tracks): Spiderweb's first track listed
        # and written while it has no notes, until Remove Track (least_tracks; saved, undone)
        self.start_track = True
        # {n: {"name", "ch"}} of the loaded MIDI's tracks shapes are drawn into, as saved with the project: used
        # while its MIDI isn't loaded (file_tracks)
        self.file_saved = {}
        # Delete loaded notes (group D): [{"boxes", "tracks"}] like an Extract's, the loaded notes in them hidden;
        # saved + undone like the own tracks, replaced (never changed in place) on every change
        self.deleted = []
        # the loaded MIDI's track numbers in the order the track list shows and the export writes them (dragged, D4;
        # None = the file's own); saved + undone like the deleted boxes
        self.file_order = None
        self.file_gone = []  # (its tracks removed in the track list, group E: not listed nor written; saved + undone)
        self.file_props = {}  # (its tracks' new names / channels, Track Properties: {number: {"name", "ch"}}; the same)
        # this project's own mark (a new one at New / Open): Ctrl+C's copies carry it, so a copy laid on tracks keeps
        # them only when pasted back into the same project (put_into_current; hunt 2026-10-26)
        self.project_token = os.urandom(8).hex()
        self.show_tempo = tk.BooleanVar(value=False)
        self._tempo_height = int(150 * s)
        self.pvar = {
            "ppq": tk.StringVar(value="960"),
            "bpm": tk.StringVar(value="120"),
            "output": tk.StringVar(value=os.path.join(OUTPUT_DIR, "spiderweb.mid")),
        }
        self.fvars = {k: tk.StringVar() for k in VEL_KEYS}
        self.end_dot = tk.BooleanVar(value=False)
        self.custom_pick = tk.StringVar()  # the shape box of the custom shape panel
        self.fill_var = tk.StringVar(value="empty")
        self.align_var = tk.StringVar(value="auto")
        self.gate_var = tk.StringVar()
        self.fentries = {}
        self.project_entries = {}  # BPM, Beats per bar
        self.point_rows = []

        ttk.Style(self).configure("Bad.TEntry", foreground=look.ERROR)
        ttk.Style(self).configure("Bad.TCombobox", foreground=look.ERROR)
        remember_good(self)  # (a wrong value typed in a number box goes back to its last good one)
        self._build()
        self.restore_window()
        speed.start()  # (the own compiled loops for lots of notes: loaded in the background)
        speed.wait_own()  # (~0.06 s: the autosave's notes made with them; a Warp's took ~7 s without)
        self.load_autosave()
        self.reattach_file()  # (the project file the work belonged to; the title)
        self.sync_panel()
        self.after(800, lambda: self.tips.show("welcome"))  # the first time Spiderweb starts
        self.updates.start()  # (What's new after an update; asks about / looks for updates)
        from window import app_playback
        if app_playback.READY_AFTER_MS is not None:  # (Built-in BASSMIDI ready before the first Space)
            self.after(app_playback.READY_AFTER_MS, self.ready_builtin)
            self.after(app_playback.READY_AFTER_MS, self.kick_ahead)  # (Rendered ahead: made while stopped, M2)

        for k, v in self.pvar.items():  # (the output file name changes no notes: typing it remade them all, slow)
            v.trace_add("write", lambda *_, k=k: self.schedule_autosave() if k == "output" else self.on_project_change(True))
        for k, v in self.fvars.items():
            v.trace_add("write", lambda *_, k=k: self.on_field(k))
        self.end_dot.trace_add("write", lambda *_: self.on_end_dot())
        self.tool.trace_add("write", lambda *_: self.on_tool_change())
        if self.tool.get() != "select":  # the tool from last time (restore_window)
            self.on_tool_change()
        # (Live shape also shows the selected custom shape's stroke points)
        self.live.trace_add("write", lambda *_: (self.sync_custom(), self.schedule_autosave(),
                                                  self.roll.request_redraw(),
                                                  self.live.get() and self.tips.show("live")))
        self.mask.trace_add("write", lambda *_: (self.schedule_autosave(), self.roll.request_redraw(),
                                                 self.sync_toolbar_parts()))
        self.sync_toolbar_parts()
        self.mask_preview.trace_add("write", lambda *_: (self.schedule_autosave(), self.sync_mask_fade(),
                                                         any(map(is_mask, self.shapes)) and self.shapes_changed()))
        self.snap.trace_add("write", lambda *_: self.roll.request_redraw())
        self.hz_snap.trace_add("write", lambda *_: (self.hz_window and self.hz_window.redraw(),
                                                    self.schedule_autosave()))
        # Tk hands Alt (pressed alone, or with a key) and F10 to the Windows window menu, which then eats the next
        # key or beeps (e.g. after an Alt-drag); only Alt+F4 (close) and Alt+Space (window menu) still go there
        for seq in ("<Key-Alt_L>", "<KeyRelease-Alt_L>", "<Key-Alt_R>", "<KeyRelease-Alt_R>",
                    "<Key-F10>", "<KeyRelease-F10>"):
            self.tk.call("bind", "all", seq, "")
        for seq in ("<Alt-Key>", "<Alt-KeyRelease>"):
            self.tk.call("bind", "all", seq, 'if {"%K" in {F4 space}} {tk::WinMenuKey %W %N}')
        self.bind_all("<F1>",lambda e: None if self.in_drawer(e) else self.open_help())
        # a left / middle button held on the piano roll, its panes, the track list or their scrollbars: playing
        # doesn't turn their pages meanwhile (roll_shared.PlayFollow, user 2026-10-23)
        self.view_held = False
        self.bind_all("<ButtonPress>", self.press_anywhere, add="+")
        self.bind_all("<ButtonRelease>", lambda e: setattr(self, "view_held", False), add="+")
        for keys, fn in ((UNDO_KEYS, lambda e: self.key_undo(e)), (REDO_KEYS, lambda e: self.key_undo(e, True)),
                         (("<Control-s>",), lambda e: self.save_project()),
                         (("<Control-Shift-s>", "<Control-Shift-S>"), lambda e: self.save_project_as())):
            for key in keys:
                self.bind_all(key, lambda e, fn=fn: None if self.in_drawer(e) else fn(e))
        self.bind_all("<space>", self.hotkey(self.toggle_play, main_only=True, while_held=True))  # (pop-ups: not the main playback)
        # Tab: the piano roll <-> the track list (on the main window only: typing boxes keep Tab for the next box)
        self.bind("<Tab>", self.hotkey(self.toggle_tracks, main_only=True))
        for k in ("<Control-x>", "<Control-X>"):  # (main window only: Ctrl+X in Help / pop-ups cut the shapes behind)
            self.bind_all(k, self.hotkey(self.cut_selected, main_only=True))
        # Delete pressed while a toolbar / panel button has the keyboard (Ctrl+A works there): the piano roll's own
        # Delete, with its mid-drag / velocity line rules (the roll handles its own key itself)
        def delete_key(e):
            if e.widget is self.roll:
                return None
            fn = lambda: self.roll.on_key(e)
            fn.in_list = self.track_list.key_delete  # (the track list showing: its Select box's notes, group E)
            return self.hotkey(fn, main_only=True, while_held=True)(e)
        self.bind_all("<Delete>", delete_key)
        for keys, fn in (("Control-n Control-N", self.new_project), ("Control-o Control-O", self.open_project),
                         ("Control-e Control-E", self.generate),  # (the File menu's keys, user 2026-10-22)
                         ("Control-c Control-C", self.copy_selected),
                         ("Control-Shift-c Control-Shift-C", self.copy_to_domino),
                         ("Control-g Control-G", self.join_selected),
                         ("Control-Shift-g Control-Shift-G", self.split_selected),
                         ("Control-r Control-R", self.turn_into_live),
                         ("Alt-w Alt-W", lambda: open_claw(self)), ("Alt-s Alt-S", lambda: open_strum(self)),
                         ("Alt-u Alt-U", lambda: open_chop(self)), ("Control-u Control-U", lambda: quick_chop(self)),
                         ("Control-v Control-V", self.paste), ("Control-h Control-H", lambda: self.flip(True)),
                         ("Control-j Control-J", lambda: self.flip(False)), ("Control-a Control-A", self.select_all),
                         ("Control-Left", lambda: self.rotate(False)), ("Control-Right", lambda: self.rotate(True))):
            for k in keys.split():
                self.bind_all(f"<{k}>", self.hotkey(fn))
        self._device_was = self.midi_device.get()
        self.midi_device.trace_add("write", lambda *_: self.midi_device_picked())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Configure>", self.remember_geometry, add="+")
        # a click anywhere outside the velocity pane = done with its line / curve
        # (the Formula tool's settings change the line just drawn: clicking them keeps it)
        self.bind("<ButtonPress>", lambda e: e.widget is self.vel or str(e.widget).startswith(str(self.vel_formula_bar))
                  or self.vel.confirm(), add="+")
        # (the Tempo pane's line just drawn the same, user 2026-10-22; a press in the pane decides itself)
        self.bind("<ButtonPress>", lambda e: e.widget is self.tempo_pane or self.tempo_pane.confirm(), add="+")

    # ------------------------------------------------------------ layout

    def _build(self):
        # the toolbar: the tools, then the view settings and buttons on the right (on a second row when the window
        # is too narrow for both, see fit_toolbar)
        build_menu(self)
        top = self.toolbar = ttk.Frame(self, padding=(6, 4))
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        bar = self.tool_bar = ttk.Frame(top)
        for key, label, hot in TOOLS:
            b = ttk.Radiobutton(bar, text=f"{label} ({hot.upper()})", value=key, variable=self.tool,
                                style="Toolbutton")
            b.pack(side="left", padx=1)
            Tooltip(b, BY_ID[TOOL_TOPICS[key]]["tip"] if key in TOOL_TOPICS else tr("app.lasso_tip"))
            if key in ("slice", "lasso"):  # (its ▾: Slice / Slice with shape, Lasso / Polygonal...; picking one
                from window.tool_picker import arrow_picture  # picks the tool too)
                if not hasattr(self, "_tool_arrow"):
                    self._tool_arrow = arrow_picture(self.scale)
                mb = ttk.Menubutton(bar, image=self._tool_arrow, style="Toolbutton", takefocus=False)  # (the
                # drawing tools' arrow)
                menu = tk.Menu(mb, tearoff=False)
                modes, var = (SLICE_MODES, self.slice_mode) if key == "slice" else (LASSO_MODES, self.lasso_mode)
                for mode, name in modes:
                    menu.add_radiobutton(label=name, value=mode, variable=var,
                                         command=lambda key=key: self.tool.set(key))
                mb["menu"] = menu
                mb.pack(side="left", padx=(0, 1))
                Tooltip(mb, tr("app.slice_modes_tip") if key == "slice" else tr("app.lasso_tip"))
        line = ttk.Separator(bar, orient="vertical")
        line.pack(side="left", fill="y", padx=(3, 3), pady=2)
        # the drawing tool picked last + the list; its pinned tools' buttons go in the bar too (fit_toolbar)
        self.tool_picker = ToolPicker(self, bar, DRAW_TOOLS, inline=True)
        self.tool_picker.frame.pack(side="left", padx=1)
        self._tools_together = ((line, self.tool_picker.frame),)  # (wrapped: the line stays before the tools)
        self._tools_line = line
        live = ttk.Checkbutton(bar, text=tr("app.live_shape_g"), variable=self.live)
        live.pack(side="left", padx=(12, 0))
        Tooltip(live, BY_ID["live"]["tip"])
        b = ttk.Checkbutton(bar, text=tr("mask.tick"), variable=self.mask,
                            command=lambda: self.mask.get() and self.tips.show("masks"))
        b.pack(side="left", padx=(8, 0))
        Tooltip(b, tr("mask.tick_tip"))
        b = ttk.Checkbutton(bar, text=tr("mask.preview"), variable=self.mask_preview)
        b.pack(side="left", padx=(4, 0))
        Tooltip(b, tr("mask.preview_tip"))
        fade = self.build_mask_fade(bar)
        fade.pack(side="left", padx=(2, 0))
        self._tools_together += ((b, fade),)  # (wrapped: the % box stays by its tick)
        # (shown only while Mask is ticked or there are masks, user 2026-10-23: more room; sync_toolbar_parts)
        self._mask_preview_parts, self._hidden_parts = (b, fade), set()
        onion = self.onion_parts = build_onion(self, bar)
        for w in onion:
            w.pack(side="left", padx=(12 if w is onion[0] else 0, 0))
        self._tools_together += (onion,)
        bar = self.option_bar = ttk.Frame(top)
        self._toolbar_rows = self._toolbar_wrap = None
        top.bind("<Configure>", lambda e: self.fit_toolbar())
        snap = ttk.Label(bar, text=tr("app.snap"))
        snap.pack(side="left", padx=(0, 4))
        self.snap_picker = SnapPicker(self, bar, self.snap)  # (the track list's snap while it shows: toggle_tracks)
        snap_box = self.snap_picker.button
        snap_box.pack(side="left")
        self._toolbar_together = ((snap, snap_box),)  # (wrapped onto more lines: kept side by side)
        redraw = lambda: (self.roll.request_redraw(), self.schedule_autosave())
        ttk.Checkbutton(bar, text=tr("app.show_lines"), variable=self.show_lines,
                        command=redraw).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(bar, text=tr("app.show_notes"), variable=self.show_notes,
                        command=redraw).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(bar, text=tr("app.velocity_pane"), variable=self.show_velocity,
                        command=self.toggle_velocity).pack(side="left", padx=(8, 0))
        b = ttk.Checkbutton(bar, text=tr("app.tempo_pane"), variable=self.show_tempo, command=self.toggle_tempo)
        b.pack(side="left", padx=(8, 0))
        Tooltip(b, tr("app.tempo_pane_tip"))
        b = ttk.Checkbutton(bar, text=tr("app.history"), variable=self.show_history, command=self.toggle_history)
        b.pack(side="left", padx=(8, 0))
        Tooltip(b, tr("app.history_tip"))
        ttk.Button(bar, text=tr("app.fit_view"), command=lambda: self.roll.fit_view()).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("app.undo"), command=self.undo_roll).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("app.redo"), command=lambda: self.undo_roll(True)).pack(side="left", padx=(4, 0))
        self.play_btn = ttk.Button(bar, text=tr("app.play_space"), width=14, command=self.toggle_play, takefocus=False)
        self.play_btn.pack(side="left", padx=(12, 0))
        self.voices_var = tk.StringVar(value=str(self.play_voices))
        # Help + Settings: always at the top right corner, whatever row the buttons are on (user)
        corner = self.corner = ttk.Frame(top)
        corner.grid(row=0, column=2, sticky="ne", padx=(8, 0))  # (at the top: the tools may wrap onto more lines)
        # (Help + Settings buttons: left out while the menu bar's Help menu has them, user 2026-10-22; kept here,
        # and the empty corner, in case they come back)
        # help_btn = ttk.Button(corner, text=tr("app.help_f1"), command=self.open_help, takefocus=False)
        # help_btn.pack(side="left", padx=(0, 4))
        # Tooltip(help_btn, tr("app.every_tip_searchable_opens_at_the"))
        # b = self.gear = ttk.Button(corner, text="⚙", width=3, command=lambda: open_settings(self), takefocus=False)
        # b.pack(side="left")
        # Tooltip(b, tr("settings.button_tip"))
        no_focus(top)

        self.status =StatusLine(self, text="", padding=(6, 2), font=look.font(9))
        self.status.pack(side="bottom", fill="x")
        # (Built-in BASSMIDI's red "Overloaded" while it can't keep up: the status line's right end, placed only
        # then (show_overload), so nothing moves, user 2026-10-18)
        self.overload_label = ttk.Label(self.status, text="", foreground=look.ERROR, font=look.font(9))
        # (13f: orange "Slowed to 62 %" left of it while the song plays slower to keep up)
        self.slowed_label = ttk.Label(self.status, text="", foreground=look.WARN, font=look.font(9))
        # (M3: Rendered ahead's grey "Audio buffer: 12.4 / 60" at the very right end while playing / making stopped)
        self.buffer_label = ttk.Label(self.status, text="", foreground=look.HINT, font=look.font(9))
        self.buffer_text = ""
        # (red "(+1,447 off-range)" right after the note count while notes are kept past the keys / before beat 0,
        # Settings → Piano roll "Keep notes made there"; user 2026-11-08: the count leaves them out, the red part
        # sits right after it (moves with the mouse position: the user's choice), laid over that piece of the
        # line's text: StatusLine.set_red)
        self.offrange_label = ttk.Label(self.status, text="", foreground=look.ERROR, font=look.font(9), padding=0)
        self.offrange_tip = Tooltip(self.offrange_label, tr("app.offrange_tip"))
        self.offrange_text = ""

        side = self._build_side()
        self._build_project(side)
        self._build_history(side)
        self._build_shape_list(side)
        self._build_lasso(side)
        self._build_shape_settings(side)
        self.settings.bind("<Configure>", lambda e: self.after_idle(self.fit_side), add="+")

        s = self.scale
        self.panes = tk.PanedWindow(self, orient="vertical", sashwidth=int(6 * s), sashrelief="raised",
                                    bd=0, bg=look.SASH, opaqueresize=True)
        self.panes.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        roll_box = tk.Frame(self.panes)
        self.roll = PianoRoll(roll_box, self, s)
        add_zoom_bars(roll_box, self.roll)
        self.panes.add(roll_box, stretch="always", minsize=int(120 * s))
        self.vel_box = ttk.Frame(self.panes)
        vbar = ttk.Frame(self.vel_box, padding=(2, 2))
        vbar.pack(fill="x")
        ttk.Label(vbar, text=tr("app.velocity")).pack(side="left", padx=(2, 8))
        b = ttk.Radiobutton(vbar, text=tr("app.select"), value="select", variable=self.vel_tool, style="Toolbutton")
        b.pack(side="left", padx=1)
        Tooltip(b, tr("velocity.select_tip"))
        ttk.Separator(vbar, orient="vertical").pack(side="left", fill="y", padx=5)
        for key, label in (("line", tr("app.linear")), ("curve", tr("app.curve")), ("pencil", tr("app.pencil")),
                           ("formula", tr("app.formula"))):
            b = ttk.Radiobutton(vbar, text=label, value=key, variable=self.vel_tool, style="Toolbutton")
            b.pack(side="left", padx=1)
            if key == "formula":
                Tooltip(b, tr("app.formula_tip"))
        self.vel_formula_bar = VelocityFormulaBar(vbar, self)  # (shown while Formula is the tool)
        # (one line: beside the Formula boxes it ends in "…", the whole hint in a tip; hunt 2026-10-19: wrapped,
        # it was a thin column and the graph jumped)
        self.vel_hint = ShortLabel(vbar, least=8, text=tr("app.ctrl_flat_shift_snap_enter_done"),
                                   foreground=look.HINT, font=look.font(8))
        self.vel_hint.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.vel_tool.trace_add("write", lambda *_: self.show_vel_formula())
        self.vel = VelocityPane(self.vel_box, self, s)
        self.vel.pack(fill="both", expand=True)  # the pane itself is added when it's turned on (off at first)
        self.tempo_box = TempoBox(self.panes, self, s)  # (under the velocity pane when both are on)
        self.tempo_pane = self.tempo_box.pane
        no_focus(self.vel_box)  # (Space plays there too, never ticks a tick)
        no_focus(self.tempo_box)
        # the track list: Tab swaps it with the piano roll and its panes, in the same place (toggle_tracks)
        self.track_box = tk.Frame(self)
        inner = tk.Frame(self.track_box)
        self.track_list = TrackList(inner, self, s)
        build_strip(self, self.track_box, self.track_list).pack(fill="x")
        inner.pack(fill="both", expand=True)
        add_zoom_bars(inner, self.track_list, up="tracks")

    def sync_toolbar_parts(self):
        """Mask preview + its % box only while Mask is ticked or some shape is a mask (user 2026-10-23); the
        Multiple tracks toggle greyed while no track is highlighted."""
        hidden = set() if self.mask.get() or any(map(is_mask, self.shapes)) else set(self._mask_preview_parts)
        sync_onion(self)
        if hidden != self._hidden_parts or hidden and any(w.winfo_manager() for w in hidden):
            self._hidden_parts = hidden
            for w in hidden:
                w.pack_forget()
            if self._toolbar_wrap is not None:
                self._toolbar_rows = None  # (laid out again: the row count may change too)
                self.fit_toolbar()

    def track_view(self, follow=False):
        """What the piano roll paints (self.drawn) with a track highlighted in the track list: its notes, the tracks
        picked behind it faint (velocity -2 - v, owner behind_owner: painted first), every other track hidden (user
        2026-10-23, like Domino). Nothing highlighted: everything. follow: the notes were just made (shapes_changed):
        the highlight follows its music and what was just drawn / pasted / moved (track_list.follow)."""
        if getattr(self, "drawn_all", None) is None:
            return
        # (slots: each note's real track; Channel per strip paints strips in colours of their own, hunt 10-23)
        d, slots = self.drawn_all, self.drawn_slots
        if follow:
            self.track_list.follow(d, slots, self.sels)
        self.drawn, self.behind_owner = self.track_list.filtered(d, slots, len(self.shapes))
        self.picture_owners = self.picture_owners_all if self.behind_owner is None else np.append(
            self.picture_owners_all, False)  # (the tracks behind: their own channel colours)
        # shapes whose notes are all on hidden tracks: their lines hidden too, never picked (user 2026-10-23);
        # shapes without notes stay
        hidden = set()
        if self.track_list.current is not None and len(d):
            shown = list(self.track_list.shown_slots())
            n = len(self.shapes) + 1
            some = np.bincount(d[:, 5], minlength=n) > 0
            # (one channel of it highlighted: the shapes with notes only on its other channels hidden too)
            see = (np.isin(slots, [s for s in shown if s != self.track_list.current])
                   | self.track_list.shown_mask(d, slots))
            seen = np.bincount(d[see, 5], minlength=n) > 0
            hidden = set(np.flatnonzero(some & ~seen).tolist())
        if self.companion is not None:  # (drawing on Domino: only what's drawn / taken there shows, the project's
            # own shapes are hidden till you're back, user 2026-11-06; window/domino_companion.py MARK)
            away = [i for i, sh in enumerate(self.shapes) if not sh.get("domino_send")]
            if away and len(self.drawn):
                self.drawn = self.drawn[~np.isin(self.drawn[:, 5], away)]
            hidden |= set(away)
        def shown_off(off):  # (only the shapes / tracks shown)
            if len(off) and (hidden or self.track_list.current is not None):
                keep = ~np.isin(off[:, 5], list(hidden))
                if self.track_list.current is not None:
                    keep &= np.isin(off[:, 4], list(self.track_list.shown_slots()))
                off = off[keep]
            return off
        off = shown_off(self.off_all)
        masked = self.off_masked
        paint = None if masked is None else shown_off(masked)
        if (off is not self.off_drawn or paint is not self.off_paint) and (
                len(off) or len(self.off_drawn) or paint is not None or self.off_paint is not None):
            self.off_version += 1
        self.off_drawn = off
        self.off_paint = paint  # (as painted under the masks' preview: faded outside them; None = off_drawn)
        if self.export_held:  # (W held: what the export gets; let go = track_view again)
            if self.wraps_offrange():
                self.export_drawn()
            else:
                self.export_held = False
        if hidden != self.hidden_shapes:
            self.hidden_shapes = hidden
            if self.sels & hidden:  # (a hidden shape can't stay picked: nothing changes out of sight)
                keep = self.sels - hidden
                self.select_many(keep, self.sel if self.sel in keep else max(keep, default=None))

    def shown_shapes(self):
        """The shape numbers the piano roll shows: all but those on tracks the track list hides (Slice / Lasso
        cut only these, hunt 2026-10-23: nothing changes out of sight)."""
        return [i for i in range(len(self.shapes)) if i not in self.hidden_shapes]

    def press_anywhere(self, e):
        w = str(e.widget)
        self.view_held = e.num in (1, 2) and any(w == str(p) or w.startswith(str(p) + ".")
                                                  for p in (self.panes, self.track_box))

    def tracks_shown(self):
        return bool(self.track_box.winfo_manager())

    def toggle_tracks(self):
        """Tab: the piano roll <-> the track list (user 2026-10-23, like Domino's)."""
        if self.roll.draft is not None or self.roll.follow is not None:
            return
        if self.tracks_shown() and self.track_list.busy():  # (a drag held in the list: let go first, hunt 10-26)
            return
        place = dict(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        if self.tracks_shown():
            self.track_box.pack_forget()
            self.panes.pack(**place)
            self.snap_picker.use(self.snap, tr("snap.tip"))
            self.roll.focus_set()
            r = self.roll
            r.update_idletasks()  # (just packed: its real width)
            x = r.t2x(self.playhead) if r.sx else None
            if x is not None and (x < r.kb_w or x > r.winfo_width() - 6 * r.scale):  # (off screen here: the piano
                page = max(1, r.winfo_width() - r.kb_w) / r.sx  # roll shows its PAGE, user 2026-10-24 / 26: pages
                r.view_t = round(page * int(self.playhead // page) * r.sx) / r.sx  # from the song's start, like
                # Domino; whole pixels)
                r.clamp_view()
            r.request_redraw()
            if self.track_list.conductor_on:  # (the Conductor row highlighted = into the Conductor pane, like
                if not self.show_tempo.get():  # Domino's Conductor track; turned on when hidden, user 11-05)
                    self.show_tempo.set(True)
                    self.toggle_tempo()
                self.key_pane = self.tempo_pane  # (Delete / Ctrl+C / V: the tempo's, as after clicking the pane)
            elif getattr(self, "key_pane", None) is self.tempo_pane:  # (from a track: that track's piano roll keys)
                self.key_pane = None
        else:
            self.tips.show("tracks")
            # (back from the Conductor pane: its row highlighted again)
            self.track_list.set_conductor(getattr(self, "key_pane", None) is self.tempo_pane)
            self.panes.pack_forget()
            self.track_box.pack(**place)
            self.snap_picker.use(self.track_list.snap, tr("tracks.snap_tip"))
            self.track_list.see_now()  # (the play line / highlighted row off screen there: shown, user 2026-10-26)
            self.track_list.focus_set()
            self.track_list.request_redraw()

    def show_vel_formula(self):
        self.vel_hint.config(text=tr("velocity.select_hint" if self.vel_tool.get() == "select"
                                     else "app.ctrl_flat_shift_snap_enter_done"))
        if self.vel_tool.get() == "formula":
            self.vel_formula_bar.fill_list()
            self.vel_formula_bar.pack(side="left", before=self.vel_hint)
        else:
            self.vel_formula_bar.pack_forget()

    def fit_toolbar(self):
        """The view settings and buttons: right of the tools when both fit in the window's width, else below them;
        either part wraps onto more lines when even its own row is too narrow (pinned tools, wider fonts)."""
        top = self.toolbar
        room = top.winfo_width() - 12
        if room <= 1:
            return
        if self._toolbar_wrap is None:
            again = lambda: self.winfo_exists() and self.fit_toolbar()  # noqa: E731  (a button grew / shrank)
            tp = self.tool_picker

            def tool_groups():  # (the pinned tools' buttons right after the picker, each wrapping on its own; room
                out = []  # kept for the picker's longest tool name and the closed Circle / Polygon group)
                for g in self._toolbar_wrap[0].groups:
                    if any(w in self._hidden_parts for w in g):  # (Mask preview while not needed)
                        continue
                    out.append((g, tp.label_room() if tp.frame in g else 0))
                    if tp.frame in g:
                        out += tp.parts()
                return out
            self._toolbar_wrap = (WrapRow(self.tool_bar, together=self._tools_together, changed=again,
                                          items=tool_groups, drop=(self._tools_line,)),
                                  WrapRow(self.option_bar, together=self._toolbar_together, changed=again))
        tools, options = self._toolbar_wrap
        corner = self.corner.winfo_reqwidth() + int(8 * self.scale)
        rows = 1 if tools.natural() + options.natural() + corner + int(24 * self.scale) <= room else 2
        tools.fit(10 ** 6 if rows == 1 else room - corner)
        options.fit(10 ** 6 if rows == 1 else room)
        if rows == self._toolbar_rows:
            return
        self._toolbar_rows = rows
        self.tool_bar.grid(row=0, column=0, sticky="w")
        # one row: Help + Settings centred like the buttons beside them (the tool buttons make the row 2 px taller:
        # at the top they sat a pixel above Play, user); wrapped: at the top, by the first line of tools
        self.corner.grid(sticky="e" if rows == 1 else "ne")
        if rows == 1:
            # (columnspan / pady set again: grid keeps the two-row ones, which put it over the gear's column)
            self.option_bar.grid(row=0, column=1, columnspan=1, sticky="e", pady=0)
        else:
            # (all 3 columns: the Help + Settings corner sits on row 0 only, so row 1 gets the whole width)
            self.option_bar.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _build_side(self):
        """The side panel: scrolls (scrollbar, mouse wheel) when everything in it doesn't fit; otherwise the help
        text at the bottom takes the room that's left."""
        side = self.make_side(int(lang_size("size.panel_width", 330) * self.scale), "side", shuts=True)  # (a translation
        self._side_wheel_at = 0.0  # may widen it)
        set_glide(self.wheel_gliding)
        click_first(self)  # (dropdowns, spin boxes, sliders: the wheel only after a click)
        fit_dropdowns(self)
        return side

    def wheel_gliding(self, e):
        """A clicked dropdown / number box / knob under the wheel while the side panel or the synth's Knobs / Effects
        tab is being scrolled: it scrolls on instead of changing (widgets.wheel_turns). True = taken."""
        if self.side_gliding(e.widget):
            self.side_wheel(e, dropdown=True)
            return True
        synth = self.hz_window and self.hz_window.synth_win
        return bool(synth and synth.winfo_exists() and synth.gliding(e))

    def side_wheel(self, e, dropdown=False):
        """The mouse wheel over the side panel scrolls it (lists scroll themselves; dropdowns and number boxes
        with the keyboard change, unless the panel is already scrolling: side_gliding)."""
        if self.scroll_side(e, dropdown):
            self._side_wheel_at = time.monotonic()

    SIDE_GLIDE = 0.5  # seconds: a wheel turned within this of the last panel scroll keeps scrolling the panel (user)

    def side_gliding(self, widget):
        """The side panel is being scrolled with the wheel: a dropdown / number box the mouse passes over scrolls
        on with it instead of changing (user: like web pages; still the wheel stops = they change again)."""
        return (str(widget).startswith(str(self.side_canvas)) and self.side_bar.winfo_ismapped()
                and time.monotonic() - self._side_wheel_at < self.SIDE_GLIDE)

    def _build_project(self, side):
        box = ttk.LabelFrame(side, text=tr("app.project"), padding=6)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        r = 0
        boxes = []  # (BPM box gone, user 2026-10-23: the tempo = the Conductor pane's, 120 before its first point;
        # Beats per bar: the pane's time signatures, user 2026-10-22)
        for label, key in ((tr("app.ppq"), "ppq"),):
            lb = ttk.Label(box, text=label)
            lb.grid(row=r, column=0, sticky="w", pady=1)
            if key == "ppq":
                row = ttk.Frame(box)
                row.grid(row=r, column=1, sticky="w", padx=5)
                self.ppq_box = ttk.Combobox(row, textvariable=self.pvar[key], values=PPQS, width=7, height=12)
                self.ppq_box.pack(side="left")
                self.ppq_tip = Tooltip(self.ppq_box, "")  # (a loaded MIDI's PPQ can't change: sync_ppq_lock)
                self.hz_ppq = None  # (box, tip, its own tip): the Hz bass Settings… PPQ box while open
                # a typed PPQ counts on Enter / leaving the box / a pick from the list (user: each key remade the
                # notes and rounded the tool windows' ticks at every half-typed PPQ); a wrong one: back to the last good
                for seq in ("<Return>", "<FocusOut>", "<<ComboboxSelected>>"):
                    self.ppq_box.bind(seq, lambda e: self.on_project_change(True, ppq_done=True) if self.ppq_ok()
                                      else self.pvar["ppq"].set(str(self.ppq)), add="+")
                # Many MIDI programs can't open a file with a PPQ of 32767 or more (above that it's SMPTE timing anyway)
                self.ppq_warning = ttk.Label(row, text=tr("app.many_programs_can_t_open_this"), foreground=look.ERROR,
                                             font=look.font(8))
                Tooltip(self.ppq_warning, tr("app.a_ppq_of_or_more_many", PPQ_WARN=PPQ_WARN))
            else:
                cell = ttk.Frame(box)
                cell.grid(row=r, column=1, sticky="w", padx=5)
                e = self.project_entries[key] = ttk.Entry(cell, textvariable=self.pvar[key], width=6)
                e.pack(fill="both", expand=True)
                watch_bad(e)
                boxes.append(e)
                Scrub(self, [(e, self.pvar[key], None)], (1, 10, 0.1), 4, 100000, label=lb)
            r += 1
        ttk.Label(box, text=tr("app.keys")).grid(row=r, column=0, sticky="w", pady=1)
        kf = ttk.Frame(box)
        kf.grid(row=r, column=1, sticky="w", padx=5)
        b = ttk.Combobox(kf, textvariable=self.keys_var, values=[str(k) for k in KEYS], state="readonly",
                         width=7)
        b.pack(side="left")
        b.bind("<<ComboboxSelected>>", lambda e: self.on_project_change())
        Tooltip(b, tr("app.128_the_standard_keys_0_127"))
        self.edo_ticks = ttk.Frame(kf)  # (shown with 256 keys only)
        t = ttk.Checkbutton(self.edo_ticks, text=tr("app.edo31"), variable=self.edo31, command=self.edo_ticked)
        t.pack(side="left", padx=(6, 0))
        Tooltip(t, tr("app.edo31_tip"))
        self.edo_audio_tick = ttk.Checkbutton(self.edo_ticks, text=tr("app.edo31_audio"), variable=self.edo31_audio,
                                              command=self.edo_ticked)
        self.edo_audio_tick.pack(side="left", padx=(4, 0))
        self.edo_audio_tip = Tooltip(self.edo_audio_tick, tr("app.edo31_audio_tip"))
        ttk.Style().configure("Warn.TCheckbutton", foreground=look.WARN)  # (Audio on, another MIDI out: edo_warn)
        box.update_idletasks()
        for e in boxes:
            e.master.config(width=b.winfo_reqwidth(), height=e.winfo_reqheight())
            e.master.pack_propagate(False)
        r += 1
        # (a line of its own under Keys, shown with the 31-EDO ticks: beside them it widened the panel)
        self.edo_loaded_tick = ttk.Checkbutton(box, text=tr("app.edo31_loaded"), variable=self.edo31_loaded,
                                               command=self.edo_loaded_ticked)
        self.edo_loaded_tick.grid(row=r, column=1, sticky="w", padx=5, pady=1)
        self.edo_loaded_tick.grid_remove()
        Tooltip(self.edo_loaded_tick, tr("app.edo31_loaded_tip"))
        r += 1
        ttk.Label(box, text=tr("app.output_file")).grid(row=r, column=0, sticky="w", pady=1)
        out = ttk.Frame(box)
        out.grid(row=r, column=1, sticky="ew", padx=5)
        ttk.Entry(out, textvariable=self.pvar["output"]).pack(side="left", fill="x", expand=True)
        ttk.Button(out, text="…", width=3, command=self.browse_output).pack(side="left", padx=(3, 0))
        r += 1
        # (user 2026-10-18: the header folds the rows; folded = one grey line of short words for what's on)
        # (the arrow in a spot of its own: ▶ and ▼ differ in width, the word must not jump)
        head = ttk.Frame(box)
        head.grid(row=r, column=0, sticky="nw", pady=(3, 0))
        self.channels_head = ttk.Label(head, width=2, cursor="hand2")
        self.channels_head.pack(side="left")
        name = ttk.Label(head, text=tr("app.channels"), cursor="hand2")
        name.pack(side="left")
        ch = self.channels_rows = ttk.Frame(box)
        ch.grid(row=r, column=1, sticky="w", padx=5, pady=(3, 0))
        self.channels_short = ttk.Label(box, foreground=look.HINT, cursor="hand2")
        self.channels_short.grid(row=r, column=1, sticky="w", padx=5, pady=(3, 0))
        for w in (self.channels_head, name, self.channels_short):
            Tooltip(w, tr("app.channels_fold_tip"))
            w.bind("<Button-1>", lambda e: self.channels_folded.set(not self.channels_folded.get()))
        # (a long line wraps under itself instead of being cut at the panel's edge: a hint line of the side panel,
        # side_panel.fit_hint; the wrap given here only marks it as one)
        self.channels_short.config(wraplength=int(300 * self.scale))
        for var in (self.channel_mode, self.use_ch10, self.ch10_order, self.new_rounds, self.pad_rounds,
                    self.keep_colours, self.channels_folded, self.edo31_lanes):
            var.trace_add("write", lambda *a: self.sync_channels_fold())
        self.mode_buttons = {}
        for value, text, tip in CHANNEL_CHOICES:
            b = self.mode_buttons[value] = ttk.Radiobutton(ch, text=text, value=value, variable=self.channel_mode,
                                                           command=self.on_channel_mode)
            b.pack(anchor="w")
            Tooltip(b, tip)
        # (user 2026-11-06: Single channel's other way, its own row shown only while Single channel is picked)
        self.single_split_tick = ttk.Checkbutton(ch, text=tr("app.single_split"), variable=self.single_split,
                                                 command=self.on_channel_mode)
        Tooltip(self.single_split_tick, tr("app.single_split_tip"))
        # (user 2026-11-10: 31-EDO projects only, under Multi channel, greyed unless it's picked)
        self.edo_lanes_tick = ttk.Checkbutton(ch, text=tr("app.edo31_lanes"), variable=self.edo31_lanes,
                                              command=self.edo_lanes_ticked)
        Tooltip(self.edo_lanes_tick, tr("app.edo31_lanes_tip"))
        # (user 2026-11-06: Multi channel's Remove overlaps; who wins + the way shown only once it's ticked)
        self.multi_rows = ttk.Frame(ch)
        self.multi_cut_tick = ttk.Checkbutton(self.multi_rows, text=tr("app.multi_cut"), variable=self.multi_cut,
                                              command=self.on_channel_mode)
        self.multi_cut_tick.pack(anchor="w")
        Tooltip(self.multi_cut_tick, tr("app.multi_cut_tip"))
        self.multi_more = ttk.Frame(self.multi_rows)
        names = [tr("app.multi_earlier_win"), tr("app.multi_later_win")]
        self.multi_win_box = ttk.Combobox(self.multi_more, state="readonly", values=names, width=max(map(len, names)))
        self.multi_win_box.pack(anchor="w", pady=(1, 0))
        self.multi_win_box.current(0)
        self.multi_win_box.bind("<<ComboboxSelected>>", lambda e: (
            self.multi_later.set(self.multi_win_box.current() == 1), self.on_channel_mode()))
        Tooltip(self.multi_win_box, tr("app.multi_win_tip"))
        self.multi_later.trace_add("write", lambda *a: self.multi_win_box.current(int(self.multi_later.get())))
        self.multi_stretch_tick = ttk.Checkbutton(self.multi_more, text=tr("app.multi_stretch"),
                                                  variable=self.multi_stretch, command=self.on_channel_mode)
        self.multi_stretch_tick.pack(anchor="w", pady=(2, 0))
        Tooltip(self.multi_stretch_tick, tr("app.multi_stretch_tip"))
        for var in (self.single_split, self.multi_cut, self.multi_later, self.multi_stretch):
            var.trace_add("write", lambda *a: self.sync_channels_fold())
        names = [name for _, name in SPLIT_CHOICES]
        self.split_box = ttk.Combobox(ch, state="readonly", values=names, width=max(map(len, names)))
        # (not packed here: shapes_changed shows it under Multi channel; with no autosave it never runs at start)
        self.split_box.bind("<<ComboboxSelected>>", lambda e: (self.shapes_changed(), self.sync_channels_fold()))
        self.split_box.current(0)
        Tooltip(self.split_box, SPLIT_TIP)
        # (shown under Multi channel too, user: most MIDI players play channel 10 as drums, some people only want
        # the 16th colour)
        self.ch10_tick = ttk.Checkbutton(ch, text=tr("app.use_ch10"), variable=self.use_ch10,
                                         command=self.on_use_ch10)
        Tooltip(self.ch10_tick, tr("app.use_ch10_tip"))
        # (user 2026-10-18: shown only while Use channel 10 is ticked; off = channel 10 last, so ticking that changes
        # no colour)
        self.order_tick = ttk.Checkbutton(ch, text=tr("app.ch10_order"), variable=self.ch10_order,
                                          command=self.on_channel_mode)
        Tooltip(self.order_tick, tr("app.ch10_order_tip"))
        # (user 2026-10-17: a Colours round's channels never used again, so each round can be edited alone elsewhere)
        self.rounds_tick = ttk.Checkbutton(ch, text=tr("app.new_rounds"), variable=self.new_rounds,
                                           command=self.on_channel_mode)
        Tooltip(self.rounds_tick, tr("app.new_rounds_tip"))
        # (user 2026-10-18: shown only while it's ticked; off = only tracks with notes are written, channels kept)
        self.pad_tick = ttk.Checkbutton(ch, text=tr("app.pad_rounds"), variable=self.pad_rounds,
                                        command=self.on_pad_rounds)
        Tooltip(self.pad_tick, tr("app.pad_rounds_tip"))
        self.sync_channels_fold()
        self.sync_overlap_rows()  # (Single channel is picked at the start: its row shows without a shapes_changed)
        r += 1
        # (MIDI out: only in Settings → Sound now, user 2026-10-18; "Overloaded" is beside Play)
        names = [name for _, name in DOMINO_STARTS]
        ttk.Label(box, text=tr("app.domino_start")).grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.domino_box = ttk.Combobox(box, state="readonly", values=names)
        # (short fixed choices: keeps its size on a wider panel, user 2026-11-10)
        self.domino_box.grid(row=r, column=1, sticky="w", padx=5, pady=(6, 0))
        self.domino_box.current(0)
        self.domino_box.bind("<<ComboboxSelected>>", lambda e: self.schedule_autosave())
        Tooltip(self.domino_box, tr("app.where_copied_and_pasted_notes_start"))
        domino = ttk.Frame(box)
        domino.grid(row=r + 1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(domino, text=tr("app.domino_row")).pack(side="left")
        for text, tip, command, gap in (("app.domino_export", "app.domino_export_tip", self.export_dms, 0),
                                        ("app.domino_copy", "app.ctrl_shift_c_copies_the_selected",
                                         self.copy_to_domino, 4),  # (the same gap as Open… / Save…)
                                        ("app.domino_paste", "app.domino_paste_tip",
                                         lambda: self.paste_from_domino(), 0)):  # (user: missed it)
            b = ttk.Button(domino, text=tr(text), command=command)
            b.pack(side="right", padx=gap)
            Tooltip(b, tr(tip))
        btns = ttk.Frame(box)
        btns.grid(row=r + 2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        # (New / Open… / Save… are in the File menu: the row got too crammed, user 2026-10-22)
        ttk.Button(btns, text=tr("app.export_midi"), command=self.generate).pack(side="right")  # (under Export)
        if system.WINDOWS:  # (Domino companion: Spiderweb's tools right on Domino's piano roll)
            b = ttk.Button(btns, text=tr("companion.button"), command=self.start_companion)
            b.pack(side="left")
            Tooltip(b, tr("companion.button_tip"))

    def _build_shape_list(self, side):
        box = self.shapes_box = ttk.LabelFrame(side, text=tr("app.shapes"), padding=6)
        box.pack(fill="x", pady=(8, 0))
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.listbox = tk.Listbox(row, height=8, activestyle="none", exportselection=False, font=look.font(9),
                                  selectmode="extended")
        sb = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_list_select)
        ListTip(self.listbox)
        # hand the keys back to the roll, so Space plays instead of picking a list row
        self.listbox.bind("<ButtonRelease-1>", lambda e: self.roll.focus_set(), add="+")
        self.listbox.bind("<Double-Button-1>", self.on_list_double)
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text=tr("app.duplicate"), command=self.duplicate).pack(side="left")
        ttk.Button(btns, text=tr("app.delete"), command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(btns, text=tr("app.delete_all"), command=self.delete_all).pack(side="left")

    def _build_shape_settings(self, side):
        self.settings = ttk.LabelFrame(side, text=tr("app.new_shape_defaults"), padding=6)
        self.settings.pack(fill="x", pady=(8, 0))
        v = ttk.Frame(self.settings)
        v.pack(fill="x")
        lb = ttk.Label(v, text=tr("app.velocity"))
        lb.pack(side="left")
        for i, key in enumerate(("vel0", "vel1")):
            if i:
                ttk.Label(v, text="→").pack(side="left", padx=3)
            self.fentries[key] = ttk.Entry(v, textvariable=self.fvars[key], width=5)
            self.fentries[key].pack(side="left", padx=(5 if not i else 0, 0))
            watch_bad(self.fentries[key])
        # dragging "Velocity" moves both ends together
        Scrub(self, [(self.fentries[k], self.fvars[k], None) for k in ("vel0", "vel1")], (1, 10, 1), 1, 127, label=lb)
        ttk.Label(v, text=tr("app.start_end"), foreground=look.HINT).pack(side="left", padx=(5, 0))
        self.env_note = ttk.Label(self.settings, text=tr("app.velocity_drawn_in_the_velocity_pane"), foreground=look.HINT,
                                  font=look.font(8),
                                  wraplength=int(300 * self.scale), justify="left")
        self._build_colours()
        self._build_recolour()
        self._build_mask()
        last = self.last_row = ttk.Frame(self.settings)
        last.pack(fill="x", pady=(4, 0))
        top = ttk.Frame(last)
        top.pack(fill="x")
        self.last_parts = [ttk.Label(top, text=tr("app.last_note"))]
        self.last_parts[0].pack(side="left", anchor="n")
        opts = ttk.Frame(top)
        opts.pack(side="left", padx=(5, 0))
        for text, value in ((tr("app.ends_on_the_last_point"), False),
                            (tr("app.starts_exactly_on_the_last_point"), True)):
            b = ttk.Radiobutton(opts, text=text, value=value, variable=self.end_dot)
            b.pack(anchor="w")
            self.last_parts.append(b)
        self._build_by_snap(last)
        self._build_line_fill()
        self._build_freehand()
        self._build_gliss()
        self._build_tumour()
        self._build_pattern()
        self._build_text()
        self._build_custom()
        self._build_polygon()
        self._build_funnel()
        self.points_box = ttk.Frame(self.settings)
        self.points_box.pack(fill="x", pady=(6, 0))
        # their boxes start at one place, the funnel's too; Colours' rows start under its dropdown, like Inside's.
        # (Not "Last note": 7 px wider, it pushed Tiles' rows past the panel's edge; it never shows with them)
        widest = label_column([lb, self.colours_word, self.recolour_word, self.shape_word, self.inside_word])
        self.funnel_box.columnconfigure(0, minsize=widest)
        self.cycle_rows_pad = widest + 5  # (panel_colours sync_colours packs the rows there)
        self.cycle_every_row.pack_configure(padx=(self.cycle_rows_pad, 0))
        label_column(self.cycle_words)

    def build_points(self):
        """Tick/pitch boxes for each point of the selected line or curve."""
        for w in self.points_box.winfo_children():
            w.destroy()
        self.point_rows = []
        sh = self.selected()
        names = point_names(sh) if sh and len(self.sels) == 1 else None
        if not names:
            return
        if len(names) > MAX_POINT_BOXES:  # (a box pair per point took ~0.02 s: a fitted curve's hundreds froze it)
            ttk.Label(self.points_box, text=tr("app.too_many_points", n=f"{len(names):,}"), foreground=look.HINT,
                      wraplength=int(300 * self.scale), justify="left").grid(row=0, column=0, sticky="w")
            return
        for col, text in enumerate((tr("app.point"), tr("app.tick"), tr("app.pitch"))):
            ttk.Label(self.points_box, text=text, foreground=look.HINT).grid(row=0, column=col, sticky="w", padx=(0, 5))
        for i, name in enumerate(names):
            tv, pv = tk.StringVar(), tk.StringVar()
            ttk.Label(self.points_box, text=name).grid(row=i + 1, column=0, sticky="w", pady=1, padx=(0, 5))
            te = ttk.Entry(self.points_box, textvariable=tv, width=10)
            te.grid(row=i + 1, column=1, sticky="w", padx=(0, 5))
            pe = ttk.Entry(self.points_box, textvariable=pv, width=7)
            pe.grid(row=i + 1, column=2, sticky="w")
            self.point_rows.append((tv, pv, te, pe))
            watch_bad(te)
            watch_bad(pe)
            # ticks: a snap step (Ctrl = one tick), pitch: a key (Shift = an octave)
            # (Space past the keys / before the start on: as far as the mouse can put a point, hunt 2026-11-06)
            Scrub(self, [(te, tv, None)], lambda: (self.snap_ticks(), 4 * self.snap_ticks(), 1),
                  lambda: self.point_limits()[0])
            Scrub(self, [(pe, pv, None)], (1, 12, 0.1), lambda: self.point_limits()[1],
                  lambda: self.point_limits()[2])
        self.sync_points()
        for i, (tv, pv, _, _) in enumerate(self.point_rows):
            tv.trace_add("write", lambda *_, i=i: self.on_point(i))
            pv.trace_add("write", lambda *_, i=i: self.on_point(i))

    # ------------------------------------------------------------ panel <-> shapes

    def selected(self):
        return self.shapes[self.sel] if self.sel is not None else None

    def target(self):
        """The panel shows the selected shape, or the defaults for new shapes when nothing is selected."""
        return self.selected() or self.defaults

    def targets(self):
        """What the panel's velocity and last-note settings change: every selected shape, or the defaults."""
        return [self.shapes[i] for i in sorted(self.sels)] or [self.defaults]

    def sync_title(self):
        sh = self.selected()
        title = tr("app.shape", sel=self.sel + 1,
                   shape_label=self.shape_label(sh)) if sh else tr("app.new_shape_defaults")
        part = self.between_part(sh) if sh else None
        if part:
            title += part
        if len(self.sels) > 1:
            title += tr("app.more_selected", n=len(self.sels) - 1)
        self.settings.config(text=title)

    def sync_ppq_lock(self):
        """A project with a MIDI file keeps the file's PPQ (its notes are in its ticks): the box greyed, its tip says why."""
        locked = self.midi_link is not None
        for box, tip, free in ((self.ppq_box, self.ppq_tip, ""), self.hz_ppq or (None, None, "")):
            if box is None or not box.winfo_exists():
                continue  # (the Hz bass window's Settings… box too: the same PPQ, hunt 10-27)
            if locked != box.instate(["disabled"]):
                box.state(["disabled" if locked else "!disabled"])
            tip.text = tr("load.ppq_locked") if locked else free

    def typing_ppq(self, ppq_done=False):
        """A PPQ still being typed in the Project box or the Hz bass Settings… one (taken on Enter / leaving it)."""
        hz = self.hz_ppq[0] if self.hz_ppq and self.hz_ppq[0].winfo_exists() else None
        return not ppq_done and (self.typing_in(self.ppq_box) or hz is not None and self.typing_in(hz))

    def sync_panel(self):
        sh = self.selected()
        self.sync_title()
        self.sync_ppq_lock()
        self.sync_edo_loaded()  # (a MIDI loaded / gone)
        self._loading = True
        for key, var in self.fvars.items():
            var.set(fmt(self.target()[key]))
            self.fentries[key].config(style="TEntry")
        self.end_dot.set(bool(self.target().get("end_dot", False)))
        self._loading = False
        if sh and sh.get("vel_env"):
            self.env_note.pack(fill="x", after=self.settings.winfo_children()[0], pady=(2, 0))
        else:
            self.env_note.pack_forget()
        self.build_points()
        self.sync_colours()
        self.sync_mask()
        self.sync_freehand()
        self.sync_gliss()
        self.sync_tumour()
        self.sync_pattern()
        self.sync_text()
        self.sync_custom()
        self.sync_funnel()
        self.sync_list_selection()
        self.sync_line_fill()
        self.sync_by_snap()
        for w in (self.claw_window, self.strum_window, self.chop_window, self.trim_window, self.path_window,
                  self.sustain_window, self.subtract_window, self.extend_window, self.merge_window, self.strips_window,
                  self.fill_window, self.lines_window):
            if w:
                w.sync()
        if self.hz_window:
            self.hz_window.sync()
        if self.sel is not None and self.row_of(self.sel) is not None:
            self.listbox.see(self.row_of(self.sel))
        # the first time one is selected: how it's edited
        kinds = {self.shapes[i]["kind"] for i in self.sels}
        for kind, topic in (("custom", "custom_edit"), ("funnel", "funnel_curves"), ("free", "straighten")):
            if kind in kinds:
                self.tips.show(topic)
                break

    def sync_list_selection(self):
        rows = [r for r, row in enumerate(self.shape_rows()) if self.sels.intersection(row)]
        if list(self.listbox.curselection()) == rows:  # (the same: left as it is, at every drag step, redraw hunt)
            return
        self.listbox.selection_clear(0, "end")
        for r in rows:
            self.listbox.selection_set(r)
        r = None if self.sel is None else self.row_of(self.sel)
        if r is not None:  # (the picked shape's row in view: a refill / new shape's row, hunt 10-26)
            self.listbox.see(r)

    def sync_points(self):
        sh = self.selected()
        if not sh:
            return
        self._loading = True
        for (tv, pv, te, pe), (b, p) in zip(self.point_rows, sh["pts"]):
            tv.set(fmt(b * self.ppq))
            pv.set(fmt(p))
            te.config(style="TEntry")
            pe.config(style="TEntry")
        self._loading = False

    def on_field(self, key):
        if self._loading:
            return
        try:
            value = calc_int(self.fvars[key].get(), 1, 127)
        except ValueError:
            bad(self.fentries[key], typing=True)  # (red; back to its good value on Enter / leaving it)
            return
        good(self.fentries[key])
        tgts = self.targets()
        if all(t[key] == value and not t.get("vel_env") for t in tgts):
            return
        if self.sels:
            self.begin_edit(("field", tuple(sorted(self.sels)), key))
        for t in tgts:
            t[key] = value
            t.pop("vel_env", None)
            t.pop("own_vel", None)  # pasted notes: their own velocities are replaced
            if t.get("fx"):  # (and those of its note tool pages, fx.py)
                t["fx"] = with_velocity(t["fx"], [[0.0, t["vel0"]], [1.0, t["vel1"]]])
        self.env_note.pack_forget()
        self.shapes_changed()

    def on_end_dot(self):
        if self._loading:
            return
        tgts, value = self.targets(), self.end_dot.get()
        if all(t.get("end_dot", False) == value for t in tgts):
            return
        if self.sels:
            self.push_undo(name=tr("app.last_note"))
        for t in tgts:
            t["end_dot"] = value
            if t.get("kind") == "gliss":  # (a Glissando run: its end snaps to the last note's start / end, gliss.py)
                t["pts"] = fit_gliss(t, self.ppq) or t["pts"]
        self.shapes_changed()
        self.sync_points()

    def point_limits(self):
        """(lowest tick, lowest key, highest key) a point box takes: tick 0 and keys 0-255, or out to the Space past
        the keys / before the start (pianoroll.room_edges; hunt 2026-11-06)."""
        b0, lo, hi = self.roll.room_edges()
        return min(0, round(b0 * self.ppq)), min(0, lo), max(KEYS[1] - 1, hi)

    def on_point(self, i):
        if self._loading or not self.selected():
            return
        tv, pv, te, pe = self.point_rows[i]
        values = []
        # (tick 0 on, pitch 0-255 at 128 keys too: user; ticks only as far as a saved file keeps a shape; the Space
        # settings widen it as far as the mouse goes)
        t_lo, p_lo, p_hi = self.point_limits()
        for var, entry, lo, hi in ((tv, te, t_lo, BEATS_FAR * self.ppq), (pv, pe, p_lo, p_hi)):
            try:
                value = float(calc(var.get()))
                if not lo <= value <= hi:
                    raise ValueError
                values.append(value)
                good(entry)
            except ValueError:
                bad(entry, typing=True)
        if len(values) < 2:
            return
        sh = self.selected()
        new = [values[0] / self.ppq, values[1]]
        if sh["pts"][i] != new:
            trial = dict(sh, pts=[new if j == i else p for j, p in enumerate(sh["pts"])])
            if not self.point_ok(sh, trial):
                return bad(te, typing=True)  # (a stray zero: many notes asked first, like the Gate box)
            self.begin_edit(("point", self.sel, i))
            axis = self.roll.held_axis(sh)
            sh["pts"][i] = new
            if self.roll.keep_symmetric(sh, i, axis):
                self.sync_points()  # the other half followed
            self.shapes_changed()

    def point_ok(self, sh, trial):
        """A point typed on a spam custom shape / funnel: past big_ask's 1 M notes = asked first. Counted only when
        the most it could make is past that (counting a funnel 10^9 ticks long took 12 s by itself); far past it
        the most is asked about."""
        if sh["kind"] not in ("custom", "funnel") or sh.get("merge") or trial.get("hz"):
            return True
        beats = [p[0] for p in trial["pts"]]
        keys = [p[1] for p in trial["pts"]]
        gate = min(sh.get("gate", 1), (sh.get("range") or {}).get("to", math.inf))
        most = (int((max(beats) - min(beats)) * self.ppq / max(1, round(gate * self.ppq))) + 3) * (
            int(max(keys) - min(keys)) + 2) * 2
        if most <= big_ask.BIG:
            return True
        if most > 50 * big_ask.BIG:
            return self.ask_notes(most)
        return self.note_count(trial) <= self.note_count(sh) or self.confirm_big([trial])

    def ppq_ok(self):
        try:
            return calc_int(self.pvar["ppq"].get(), 1, 65535) > 0
        except ValueError:
            return False

    def hz_tuned_before_ppq(self, typed, ppq_done):
        """The Hz bass shapes in tune with the tempo under them, when the PPQ box now holds another PPQ (they're made
        for the new one's tempo steps too: a PPQ change keeps the tone by itself)."""
        if typed and self.typing_ppq(ppq_done):
            return []
        try:
            if calc_int(self.pvar["ppq"].get(), 1, 65535) == self.ppq:
                return []
        except ValueError:
            return []
        return [sh for sh in self.shapes if sh.get("hz") and not self.hz_out_of_tune(sh)]

    def on_project_change(self, typed=False, ppq_done=False):
        """typed: a key in the PPQ / BPM / Beats per bar box. The notes are remade only for a new PPQ or Keys (BPM
        and beats per bar change no notes: Hz bass keeps its own BPM; remaking them made typing lag). A PPQ typed
        counts once done (ppq_done: Enter / left / picked from the list)."""
        if self._opening:  # (a project being opened: done once its boxes are all filled in, load_file)
            return
        before = (self.ppq, self.keys)
        tuned = self.hz_tuned_before_ppq(typed, ppq_done)
        for key, lo, hi in (("ppq", 1, 65535),):
            if key == "ppq" and typed and self.typing_ppq(ppq_done):
                continue  # (still being typed: taken on Enter / leaving it)
            try:
                setattr(self, key, calc_int(self.pvar[key].get(), lo, hi))
                if key in self.project_entries:
                    good(self.project_entries[key])
            except ValueError:
                if key in self.project_entries:  # (red; back to its good value on Enter / leaving it)
                    bad(self.project_entries[key], typing=True)
        if "bpm" in self.project_entries:
            try:
                ok = 4 <= calc(self.pvar["bpm"].get()) <= 100000
            except ValueError:
                ok = False
            (good if ok else lambda e: bad(e, typing=True))(self.project_entries["bpm"])
        self.keys = KEYS[1] if self.keys_var.get() == str(KEYS[1]) else KEYS[0]
        if typed and (self.ppq, self.keys) == before:
            self.play_changed()  # (a new BPM heard while playing)
            self.roll.request_redraw()  # (bar lines)
            self.tempo_changed()  # (the Tempo pane's BPM line and catch-ups)
            self.sync_custom()  # (Hz bass "BPM changed" lines)
            self.sync_funnel()
            if self.hz_window:
                self.hz_window.show_stale()
                self.hz_window.show_cc()  # (the CC lines' run-on after the sound is in seconds)
                self.hz_window.redraw()
            self.schedule_autosave()
            return
        if self.ppq != before[0]:
            for sh in tuned:  # (a glide's steps come at other spots at a new PPQ: still in tune, hunt 2026-10-23)
                sh["hz"] = self.hz_retimed(sh, sh["hz"])
        set_key_top(self.keys)
        self.sync_edo()
        self.roll.clamp_view()
        warn = self.ppq >= PPQ_WARN
        self.ppq_box.config(style="Bad.TCombobox" if warn else "TCombobox")
        if warn and not self.ppq_warning.winfo_manager():
            self.ppq_warning.pack(side="left", padx=(5, 0))
        elif not warn:
            self.ppq_warning.pack_forget()
        self.sync_points()
        self.shapes_changed()
        self.sync_custom()  # gates are shown in ticks
        if self.hz_window:
            self.hz_window.show_stale()  # (a BPM change)
        self.sync_funnel()
        self.sync_tumour()
        self.sync_text()
        self.sync_gliss()  # (Step "Auto (1/32)" / grids under a tick and the key count follow the PPQ / Keys)
        for w in (self.claw_window, self.strum_window, self.chop_window, self.trim_window, self.path_window,
                  self.sustain_window, self.subtract_window, self.extend_window, self.merge_window, self.fill_window, self.melody_window):
            if w:
                w.ppq_changed()  # (numbers shown in ticks)
        if self.image_window and (self.ppq, self.keys) != before:
            self.image_window.show_settings()  # (its Keys inside the new key range; One step in the new ticks)
        if self.ppq != before[0] and getattr(self, "tempo_box", None) is not None:
            self.tempo_box.sync()  # (the catch-up result is worked out in ticks, hunt 10-27)
        if self.ppq != before[0] and hasattr(self, "by_snap_entry"):
            self.sync_by_snap()  # (a step typed in ticks follows the PPQ: the box shows the new count, hunt 10-28)

    def all16(self):
        """True while Multi channel uses channel 10 too ("Use channel 10", shown only under Multi channel)."""
        return self.use_ch10.get() and self.channel_mode.get() == "auto"

    def sync_channels_fold(self):
        """Project's Channels header: ▾ = the rows shown, ▸ = folded to a grey line naming what's on."""
        if not hasattr(self, "channels_short"):  # (a trace before the panel is made)
            return
        folded = self.channels_folded.get()
        if self.channels_head.cget("text") != ("▶" if folded else "▼"):  # (every shapes_changed: only changes)
            self.channels_head.config(text="▶" if folded else "▼")
        mode = self.channel_mode.get()
        words = [tr("app.ch_short_" + mode)]
        if mode == "auto":
            words.append(tr("app.ch_short_" + SPLIT_CHOICES[max(self.split_box.current(), 0)][0]))
            if self.edo_lanes_on():
                words.append(tr("app.ch_short_edo_lanes"))
            if self.all16():
                words.append(tr("app.ch_short_ch10"))
            if self.in_order():
                words.append(tr("app.ch_short_order"))
            if self.rounds_apart():
                words.append(tr("app.ch_short_rounds"))
            if self.blocks_apart() and self.pad_rounds.get():
                words.append(tr("app.ch_short_pad"))
            if self.multi_cut.get():
                words.append(tr("app.ch_short_later" if self.multi_later.get() else "app.ch_short_earlier"))
                if self.multi_stretch.get():
                    words.append(tr("app.ch_short_stretch"))
        elif mode == "single" and self.single_split.get():
            words.append(tr("app.ch_short_split"))
        if self.channels_short.cget("text") != ", ".join(words):
            self.channels_short.config(text=", ".join(words))
        grid_shown(self.channels_rows, not folded)
        grid_shown(self.channels_short, folded)

    def overlap_rule(self):
        """How overlaps are fixed now (render overlaps), or None = the usual way: Single channel's "split around"
        tick, Multi channel's Remove overlaps (who wins, cut + stretch or split around; user 2026-11-06)."""
        mode = self.channel_mode.get()
        if mode == "single" and self.single_split.get():
            return {"way": "split"}
        if mode == "auto" and self.multi_cut.get():  # (own / files: the track list's order, track_ranks; not the
            own, files, _ = list_order(self)             # emptied tracks: those are worked out from these notes)
            return {"across": True, "way": "stretch" if self.multi_stretch.get() else "split",
                    "later": bool(self.multi_later.get()), "own": own, "files": files}
        return None

    def heard_rule(self):
        """overlap_rule's way + across only (quick: asked for every notes shape whose notes are made)."""
        mode = self.channel_mode.get()
        if mode == "single":
            return {"way": "split" if self.single_split.get() else "stretch"}
        if mode == "auto" and self.multi_cut.get():
            return {"across": True, "way": "stretch" if self.multi_stretch.get() else "split"}
        return None

    def heard_way_of(self, sh):
        """How the Channels mode fixes sh's own overlaps (engine.heard_way), or None."""
        return heard_way(sh, self.channel_mode.get(), self.heard_rule())

    def sync_overlap_rows(self):
        """Single's split around row only under Single channel; Multi's who wins + way only once Remove overlaps is
        ticked (only on a change: grid_shown's rule)."""
        single = self.channel_mode.get() == "single"
        if single != bool(self.single_split_tick.winfo_manager()):
            if single:
                self.single_split_tick.pack(anchor="w", padx=(18, 0), pady=(1, 0), after=self.mode_buttons["single"])
            else:
                self.single_split_tick.pack_forget()
        more = bool(self.multi_cut.get())
        if more != bool(self.multi_more.winfo_manager()):
            if more:
                self.multi_more.pack(anchor="w", padx=(18, 0))
            else:
                self.multi_more.pack_forget()

    def in_order(self):
        """True while the shapes take channels 1-16 in order ("Channels 1-16 in order", only with all16)."""
        return self.ch10_order.get() and self.all16()

    def rounds_apart(self):
        """True while each Colours round goes on channels of its own ("New channels each Colours round", Multi
        channel only)."""
        return self.new_rounds.get() and self.channel_mode.get() == "auto"

    def colours_used(self):
        """With "Repeated colours get new channels": how many colours each shape's notes use (channels within one
        repeat; the Shapes list's orange goes by these, not by its channels, hunt), else None."""
        r = self.rendered
        if not self.rounds_apart() or not len(r):
            return None
        step = 16 if self.picture_use10 else 15
        pairs = np.unique(r[:, 5] * step + r[:, 4] % step)
        return np.bincount(pairs // step, minlength=len(self.shapes)).tolist()

    def pad_tracks(self):
        """True if the saved MIDI / .dms gets a track for every channel up to the last one used, empty ones too
        (midi_out.track_slots); False only with the rounds tick (or Settings' "Overlapping shapes keep their
        colours", rows_apart) and "Keep empty tracks" off."""
        return not (self.blocks_apart() and not self.pad_rounds.get())

    def least_tracks(self):
        """Spiderweb's tracks listed and written at least (midi_out.track_slots' least): 1 for a new project's empty
        Track 1 (start_track), none with a loaded MIDI (its own tracks instead). A track Remove overlaps emptied
        stays (hunt 2026-11-06: tracks never come and go with the tick)."""
        least = int(self.start_track and self.loaded is None and self.midi_link is None)
        return max([least] + [s + 1 for s in self.cut_emptied])

    def filled_slots(self, notes):
        """The automatic tracks counted as having notes (own_tracks.counted: where your tracks go) for these notes:
        theirs + the ones Remove overlaps emptied, so ticking it never moves your tracks (hunt 2026-11-06)."""
        got = np.unique(np.asarray(notes)[:, 4]) if len(notes) else np.zeros(0, np.int64)
        return np.union1d(got, np.asarray(sorted(self.cut_emptied), np.int64)) if self.cut_emptied else got

    def rows_apart(self):
        """True while overlapping shapes move to the next row of channels (Settings → General, Multi channel only)."""
        return self.keep_colours.get() and self.channel_mode.get() == "auto"

    def blocks_apart(self):
        """True while channels go in blocks that may leave some empty ("Keep empty tracks" shown and used): the
        rounds tick, rows_apart, or a shape with Channel per strip (strips.py; hunt 2026-10-23)."""
        return self.rounds_apart() or self.rows_apart() or (
            self.channel_mode.get() == "auto" and any(has_strips(sh) for sh in self.shapes))

    def edo(self):
        """True while the piano roll shows 31-EDO (256 keys + its tick)."""
        return self.keys == KEYS[1] and self.edo31.get()

    def edo_ticked(self):
        lanes = engine.edo_lanes
        self.sync_edo()
        if engine.edo_lanes != lanes:  # ("Overlaps keep their pitch" came / went: the track list's channels, the
            self.shapes_changed()     # folded Channels line, the export, as its own tick does)
        self.schedule_autosave()
        if self.edo31.get():
            self.tips.show("edo31", wait=True)  # (the first time: what the ticks do)

    def sync_edo(self):
        """The 31-EDO ticks: shown with 256 keys only, Audio greyed until 31-EDO is ticked."""
        big = self.keys == KEYS[1]
        if big and not self.edo_ticks.winfo_manager():
            self.edo_ticks.pack(side="left")
            self.edo_loaded_tick.grid()
        elif not big and self.edo_ticks.winfo_manager():
            self.edo_ticks.pack_forget()
            self.edo_loaded_tick.grid_remove()
        self.edo_audio_tick.state(["!disabled" if self.edo31.get() else "disabled"])
        self.sync_edo_loaded()
        tuned = self.edo() and self.edo31_audio.get()
        if tuned != self.out.edo:  # (sounding notes end first: their note-offs go where their note-ons went)
            playing = self.player.running
            if playing:
                self.stop_play()
            self.scrub_end()
            self.out.edo = tuned
            if playing:
                self.start_play(keep=True)
            elif getattr(self, "ahead_play", None) is not None:  # (Rendered ahead's sound made while stopped: old)
                self.play_changed()
        self.edo_warn()
        self.edo_loaded_tidy()
        self.sync_edo_lanes()
        self.roll.request_redraw()

    def edo_lanes_on(self):
        """True while Multi channel's tracks go only on the first channel of each 31-EDO group (1, 4, 7, 11, 14, then
        the next port's): "Overlaps keep their pitch" (user 2026-11-10), a 31-EDO project with Multi channel."""
        return self.edo() and self.channel_mode.get() == "auto" and self.edo31_lanes.get()

    def sync_edo_lanes(self):
        """The "Overlaps keep their pitch" tick: shown only in a 31-EDO project (Keys 256 + 31-EDO), under Multi
        channel, greyed unless Multi channel is picked; engine.edo_lanes follows it (the export's channels / ports and
        playback). A change while playing = played again from there (note-offs where their note-ons went)."""
        tick = getattr(self, "edo_lanes_tick", None)
        if tick is None:  # (before the side panel is made)
            return
        shown = self.edo()
        if shown != bool(tick.winfo_manager()):
            if shown:
                tick.pack(anchor="w", padx=(18, 0), pady=(1, 0), after=self.mode_buttons["auto"])
            else:
                tick.pack_forget()
        tick.state(["!disabled" if self.channel_mode.get() == "auto" else "disabled"])
        on = self.edo_lanes_on()
        if on == engine.edo_lanes:
            return
        engine.edo_lanes = on
        if self.player.running:
            self.stop_play()
            self.start_play(keep=True)
        elif getattr(self, "ahead_play", None) is not None:
            self.play_changed()

    def edo_lanes_ticked(self):
        self.shapes_changed()  # (the track list's channels, the export; sync_edo_lanes from there)
        self.schedule_autosave()

    def sync_edo_loaded(self):
        """The Loaded MIDI tick greyed until 31-EDO + Audio are ticked and a MIDI is loaded."""
        tick = getattr(self, "edo_loaded_tick", None)
        if tick is not None:  # (built with the side panel)
            on = self.edo31.get() and self.edo31_audio.get() and self.loaded is not None
            tick.state(["!disabled" if on else "disabled"])

    def edo_heard(self, device=None):
        """True when the piano roll's notes are played in 31-EDO on this MIDI out (None: the one picked), opened or
        not yet (out.tuned only knows the open one: before the first Play it said no)."""
        return self.edo() and self.edo31_audio.get() and (device or self.midi_device.get()) in (BUILTIN, KESTREL, AHEAD)

    def edo_loaded(self, device=None):
        """True when the loaded MIDI is played in 31-EDO, forced (user 2026-11-10): its notes go out as rows, like
        Spiderweb's own, on the tuned channels (one piano sound; its bends, instruments and controllers left out).
        device = the MIDI out to plan for (None: the one picked). Past EDO_MOST notes: played as written."""
        return self.edo_loaded_wanted(device) and self.loaded.notes <= self.edo_most()

    def edo_loaded_wanted(self, device=None):
        """edo_loaded before the note count: the tick on, a loaded MIDI still the same, 31-EDO heard on device."""
        return (self.loaded is not None and self.edo31_loaded.get() and self.edo_heard(device)
                and not getattr(self.loaded, "changed", False))

    @staticmethod
    def edo_most():
        from notes.loaded import EDO_MOST
        return EDO_MOST

    def edo_loaded_rows(self, device=None):
        """The loaded MIDI's notes as rows for the player while edo_loaded, else None (notes.loaded.edo_rows)."""
        if not self.edo_loaded(device):
            return None
        from notes.loaded import edo_rows
        try:
            return edo_rows(self.loaded, self.track_list.loaded_mutes(), self.ppq)
        except Exception:  # (a file that changed / went: plays nothing of it, said when drawing / exporting)
            errors.write_log(*sys.exc_info(), "(a loaded MIDI's notes for 31-EDO)")
            return None

    def edo_loaded_tidy(self):
        """The loaded MIDI's 31-EDO copies let go while they can't be used (the tick, 31-EDO or Audio off, or a MIDI
        out without 31-EDO picked): ~0.6 GB at 4 M notes stayed until the MIDI was closed. Made again when needed."""
        if self.loaded is not None and not self.edo_loaded_wanted():
            from notes.loaded import edo_forget
            edo_forget(self.loaded)
            if getattr(self, "ahead_play", None) is not None:
                self.ahead_play._edo_part = None

    def edo_loaded_ticked(self):
        """The Loaded MIDI tick: heard from now on (playing: started again from where it is)."""
        self.edo_loaded_tidy()
        self.schedule_autosave()
        if self.player.running:
            self.stop_play()
            self.start_play(keep=True)
        else:
            self.play_changed()  # (Rendered ahead's making while stopped: made again)

    def edo_warn(self):
        """The Audio tick orange (+ its tip says why) while it's on but the MIDI out can't play 31-EDO."""
        off = self.edo() and self.edo31_audio.get() and self.midi_device.get() not in (BUILTIN, KESTREL, AHEAD)
        self.edo_audio_tick.config(style="Warn.TCheckbutton" if off else "TCheckbutton")
        self.edo_audio_tip.text = tr("app.edo31_audio_needs_builtin" if off else "app.edo31_audio_tip")

    def read_project(self):
        try:
            ppq = calc_int(self.pvar["ppq"].get(), 1, 65535)
        except ValueError:
            raise ValueError(tr("app.ppq_must_be_a_whole_number"))
        try:
            bpm = calc(self.pvar["bpm"].get())
            if not 4 <= bpm <= 100000:
                raise ValueError
        except ValueError:
            raise ValueError(tr("app.bpm_must_be_a_number_at"))
        return ppq, bpm, self.beats

    @property
    def tempo(self):
        """The Conductor pane's line (notes/tempo.py); a new one is watched (tempo.tempo_text remembers its text)."""
        return self._tempo

    @tempo.setter
    def tempo(self, line):
        self._tempo = line
        watch_tempo(line)

    @property
    def beats(self):
        """The first bar's length in beats (what Beats per bar was; bar lines: the time signatures, notes/meter)."""
        v = first_bar_beats(getattr(self, "tempo", None))
        return int(v) if v == int(v) else v

    # ------------------------------------------------------------ shapes

    def notes_of(self, sh):
        return self.notes_tracks(sh)[0]

    def notes_tracks(self, sh, keys=None, edges=True):
        """shape_notes_tracks, remembered. Each note tool page goes on top of the notes remembered without it
        (trying a page's settings doesn't make the notes before it again). Outside Multi channel a Hz bass's notes
        are made as for one channel (Own channel per note needs Multi channel). keys: made on another key range
        (a shape past the edges, made lifted: engine.past_edges). edges=False: the notes before a page, never cut
        at the edges (only the finished shape is: shape_notes_tracks' way; pages_hunt 5, 2026-11-06)."""
        sh, keys, key = self.notes_cache_key(sh, keys, edges)
        cache, asked = self._notes_cache, self._notes_asked
        if asked:
            asked[-1].add(key)
        got = cache.pop(key, None)
        if got is None:
            self._notes_depth += 1
            asked.append(set())
            try:
                # (reaching past the keys / before beat 0: made whole, then cut: engine.edge_made, the thread's too)
                got = edge_made(sh, self.ppq, keys, self.notes_tracks) if edges and keys == self.keys else None
                if got is None:
                    got = fx_notes(sh, self.ppq, keys, lambda s: self.notes_tracks(s, keys, False))
            finally:
                self._notes_depth -= 1
                deps = asked.pop()
            self._notes_deps[key] = deps
            got = shared_arrays(got, cache)
            self._notes_worked += 1
        cache[key] = got  # (last = used most recently: trim_notes forgets the oldest first)
        self._notes_used[key] = self.shapes_gen
        if not self._notes_depth:
            self._notes_top.add(key)
        self.trim_notes()
        return got

    def notes_cache_key(self, sh, keys=None, edges=True):
        """(sh as its notes are made, keys, the notes cache key) for notes_tracks."""
        if self.channel_mode.get() != "auto":  # (Recolour too: in one channel it would only change overlaps; a
            sh = recolour_alone(one_voice(sh))  # Domino paste keeps its tracks, so its Recolour stays)
        elif "recolour" in sh:  # (a colour set holds 16 with channel 10 used)
            sh = with_most(sh, self.all16())
        if "notes" in sh:  # (its own overlaps fixed as shown before any tool works on them: engine.heard_way)
            sh = as_heard(sh, self.channel_mode.get(), self.heard_rule())
        keys = self.keys if keys is None else keys
        key = (shape_key(sh), self.ppq, keys)
        if not edges and keys == self.keys and past_edges(sh, keys, self.ppq) is not None:
            key += ("whole",)  # (uncut: not the finished shape's cut notes)
        return sh, keys, key

    def notes_known(self, sh):
        """notes_tracks(sh) when they're remembered already, else None (nothing made)."""
        return self._notes_cache.get(self.notes_cache_key(sh)[2])

    def trim_notes(self):
        """The notes remembered for at most 500 shapes, or 3 for each shape in the project: past that, the ones
        used longest ago are forgotten (forgetting them all made every edit with over 500 shapes slow)."""
        cache = self._notes_cache
        most = max(500, 3 * len(self.shapes))
        if len(cache) > most:
            for k in list(itertools.islice(cache, len(cache) - most // 2)):
                del cache[k]
                self._notes_used.pop(k, None)

    def forget_old_notes(self):
        """The notes remembered for shapes as they WERE (before a move, an edit...: kept so undo is quick) past
        OLD_NOTES_SHARE of the PC's memory: the oldest forgotten (made again if undo needs them). A few edits of a
        shape with millions of notes kept a copy each: a phone ran out of memory. The notes this making used stay.
        Wine-like systems only (old_notes_most None elsewhere: nothing forgotten). Every system: notes of shapes as
        they WERE that only went into making another's notes (the notes before a Recolour / a page, made lifted for
        the edges...) are forgotten: undo asks for the finished ones (2 M notes: ~240 MB less an edit). The ones the
        notes used NOW were made from stay (_notes_deps): else the next Recolour / page change after an undo or an
        edit of another shape made the whole shape again (2 M notes: +35 %)."""
        cache, used, gen, top = self._notes_cache, self._notes_used, self.shapes_gen, self._notes_top
        deps = self._notes_deps
        for d in (used, deps):  # (the cache emptied elsewhere: their keys hold big texts)
            for k in [k for k in d if k not in cache]:
                del d[k]
        for k in [k for k in top if k not in cache]:
            top.discard(k)
        need, todo = set(), [k for k in cache if used.get(k) == gen]
        while todo:
            for d in deps.get(todo.pop(), ()):
                if d not in need:
                    need.add(d)
                    todo.append(d)
        for k in [k for k in cache if k not in top and used.get(k) != gen and k not in need]:
            del cache[k]
            used.pop(k, None)
            deps.pop(k, None)
        most = old_notes_most()
        if most is None:
            return
        # (arrays shared between entries (shared_arrays) count once; forgetting an entry frees only the arrays no
        # other entry holds)
        held, sizes, holders = {}, {}, {}
        for k, v in cache.items():
            ids = set()
            for a in (v if isinstance(v, (tuple, list)) else (v,)):
                if isinstance(a, np.ndarray):
                    b = a.base if isinstance(a.base, np.ndarray) else a
                    ids.add(id(b))
                    sizes[id(b)] = b.nbytes
            held[k] = ids
            for i in ids:
                holders[i] = holders.get(i, 0) + 1
        total = sum(sizes.values())
        if total <= most:
            return
        for k in [k for k in cache if used.get(k) != gen and k not in need]:  # (oldest first; never what's used now)
            if total <= most:
                break
            for i in held[k]:
                holders[i] -= 1
                if not holders[i]:
                    total -= sizes[i]
            del cache[k]
            used.pop(k, None)
            deps.pop(k, None)
            top.discard(k)

    def shapes_changed(self, now=False, moving=False):
        """Recalculate every note (overlaps depend on all shapes together) and refresh the screen.
        While dragging, if that's slow (lots of notes), only the lines follow the mouse: the notes catch up when the
        mouse rests or is let go (now: catch up). moving: shapes are dragged to another place: the piano roll shows
        their notes going along (roll_draw.paint_carried), and they catch up when the mouse rests (if that's quick)
        or is let go.
        After an edit on a big project (making the notes took > LATE_S, or now="late": a drag let go), the notes are
        made in a background thread: the view keeps working meanwhile, the first other click / key waits for them
        (notes_wait.py, late_ok, finish_notes)."""
        if now and now != "late":
            self._bg_notes = False  # (being made now: what's read on the way is read as it is, not waited for)
        self.shapes_gen += 1
        if self._late_notes:
            self.after_cancel(self._late_notes)
            self._late_notes = None
        if sync_groups(self.shapes):  # Add between: the steps follow their first / last shape (between.py)
            self.after_idle(self.sync_title)  # (a step changed by hand is a key now)
        if tidy_groups(self.shapes):  # (masks: a group of one = on its own, the first member Add; mask.py)
            self.after_idle(self.sync_mask)
        if self.loaded is not None:  # (Extract: the loaded notes the shapes took stay hidden, the rest come back)
            sync_extract(self)
            sync_channels(self)  # (Track Properties' channels: shown and heard on them)
        if not now and self.scrubbing and self._merge_time > 0.03:  # (a big merged shape: made when the mouse rests)
            self._merges_owed = True
        else:
            started = time.perf_counter()
            self.remake_merges()
            self._merge_time, self._merges_owed = time.perf_counter() - started, False
        for sh in self.shapes:  # ("Shape length follows the notes": after any change to its box too, hunt)
            if (sh.get("hz") or {}).get("grow"):
                fit_length(sh)
        busy = self._notes_time + self._mask_time  # (the masks' preview is made after the notes, mask.py)
        slow = busy > 0.15 or self._merges_owed
        # (other drags make the notes AND repaint them all at every step: the repaint counts too)
        slow = slow or not moving and busy + self.roll.paint_time > 0.15
        # (moving: also whenever the piano roll shows its notes as one picture, which it can carry along)
        # (Nested's / Tiles' / Ripples' centre points: the notes are all they show, so they follow in the background,
        # user)
        # (a Shares knob held too: the colours follow it, user 2026-10-25)
        follow = (self.roll.drag and self.roll.drag[0] == "handle" and isinstance(self.roll.drag[1], tuple)
                  and self.roll.drag[1][0] in ("centre", "ripple")) or self.scrubbing == "follow"
        if not now and (self.roll.drag or self.scrubbing) and (
                slow or follow and busy > 0.03 or moving and (busy > 0.03 or self.roll._img is not None)):
            self.notes_late = True
            if follow:  # (one at a time: when it's made, _coming_check shows it and starts the next spot's)
                if not (self._coming and not self._coming["done"]):
                    self._late_notes = self.after_idle(self._make_coming, True, True)
                self.roll.request_redraw()
                return
            # the notes are made in the background once the mouse rests: quick enough to paint = the real notes
            # show then, else they wait for the mouse to be let go (and are ready by then)
            self._late_notes = self.after(120 if moving else 250, self.notes_rested, not (moving and slow))
            self.roll.request_redraw()
            return
        if now in (False, "late") and self.late_ok():
            self.notes_late = self._bg_notes = True
            self._bg_serial, self._bg_carry = notes_wait.last_event, None
            self._make_coming(True)  # (made already at a mid-drag rest: shown at once, shapes_changed(now=True))
            if self._bg_notes:
                self.roll.request_redraw()
                self.update_status()
            return
        self.notes_late = self._bg_notes = False
        self._made_shapes = list(self.shapes)
        if not self.roll.drag:
            self.remake_pictures()
        self.rendered_pts = [list(sh["pts"][0]) for sh in self.shapes]  # (where each shape is in these notes)
        self.roll.copy_src = None  # (Ctrl+drag copies: their own notes now, roll_draw.on_top)
        started, worked = time.perf_counter(), self._notes_worked
        self.channel_split = SPLIT_CHOICES[max(self.split_box.current(), 0)][0]
        multi = self.channel_mode.get() == "auto"
        self.split_box.config(state="readonly" if multi else "disabled")
        if multi != bool(self.split_box.winfo_manager()):  # (only shown under Multi channel, user)
            if multi:
                self.split_box.pack(anchor="w", padx=(18, 0), pady=(1, 0))
                self.ch10_tick.pack(anchor="w", padx=(18, 0), pady=(2, 0))
                self.rounds_tick.pack(anchor="w", padx=(18, 0), pady=(2, 0))
                self.multi_rows.pack(anchor="w", padx=(18, 0), pady=(2, 0), after=self.rounds_tick)
            else:
                self.split_box.pack_forget()
                self.ch10_tick.pack_forget()
                self.rounds_tick.pack_forget()
                self.multi_rows.pack_forget()
        self.sync_overlap_rows()
        self.sync_edo_lanes()
        if self.all16() != bool(self.order_tick.winfo_manager()):  # (only shown with Use channel 10, user)
            if self.all16():
                self.order_tick.pack(anchor="w", padx=(36, 0), pady=(2, 0), before=self.rounds_tick)
            else:
                self.order_tick.pack_forget()
        padding = self.blocks_apart()
        if padding != bool(self.pad_tick.winfo_manager()):  # (only shown with the rounds tick / rows, user)
            if padding:
                self.pad_tick.pack(anchor="w", padx=(36, 0), pady=(2, 0), after=self.rounds_tick)
            else:
                self.pad_tick.pack_forget()
        self.sync_channels_fold()  # (", empty tracks" comes and goes with Channel per strip, hunt 10-27)
        if any("into_ch" in sh for sh in self.shapes):  # (a track's own channel gone with its shapes: it takes one it
            tidy_tracks(self)                           # still holds, PLAN #11 C; found by the track sweep)
        made = self.notes_made(any_spot=now == "follow")
        for sh in self.shapes:  # a piece whose outline was changed is a shape of its own now (sliced.py)
            if "cut" in sh and moved_by(as_made(sh)) is None:
                completed(sh)  # (with the whole's glue / note tool pages)
        stash = (self._job_stash or {}) if made is not None else {}  # (made in the background: its all_notes' stash)
        self._job_stash = None

        def make():
            stash.clear()
            return all_notes(self.shapes, self.ppq, self.keys, self.channel_mode.get(), self.channel_split,
                             self.all16(), self.notes_tracks, rounds=self.rounds_apart(), order=self.in_order(),
                             keep=self.keep_colours.get(),
                             busy=self.cc_busy(self.channel_mode.get()), own=self.own_channels(), stash=stash,
                             overlaps=self.overlap_rule())
        if made is None:
            made = make()
            self._make_time = time.perf_counter() - started  # (every time: late_ok goes by it)
            if self._notes_worked != worked:
                self._notes_time = self._make_time
        if self.fit_into(made):  # (shapes laid on tracks got more colours: laid down the rows, laid again)
            if any("into_ch" in sh for sh in self.shapes):  # (a paste's lanes tidied too, so undo / redo give
                tidy_tracks(self)                           # back the same: found by the track sweep)
            made = lay_again(made, stash, self.shapes, self.own_channels()) or make()
        wanted, self.picture_use10, self.rendered, self.slot_count, self.voice_slots, _, self.cut_emptied = made
        self.forget_old_notes()
        self.off_all = self.offrange_rows()  # (before play_changed: App.outgoing reads them)
        self.made_version += 1
        if getattr(self, "tempo_pane", None) is not None:  # (Keep timing notes too close: looked at again soon)
            self.tempo_pane.dense_now()
        started = time.perf_counter()
        self.pattern = self.rendered
        if self.channel_mode.get() == "single" and any(is_mask(sh) and not silent(sh) for sh in self.shapes):
            # (Single channel: sounding masks' notes cut the pattern's where they start; what a mask shows / Apply
            # makes = the pattern as it sounds without them, user)
            self.pattern = all_notes(self.shapes, self.ppq, self.keys, "single", self.channel_split, self.all16(),
                                     self.notes_tracks, quiet=True, own=self.own_channels(),
                                     overlaps=self.overlap_rule())[2]
        if self.mask_preview.get():
            self.drawn, self.mask_covered = mask_view(self.rendered, self.shapes, self.ppq, self.keys, self.notes_of,
                                                      self.pattern, GHOST_FADE[0] > 0)
            self.off_masked = self.masked_offrange()  # (the kept notes past the keys under the masks too)
        else:  # (toolbar "Preview" off: the pattern drawn whole, only the masks' dashed lines; user)
            self.drawn, self.mask_covered = self.rendered, set()
            self.off_masked = None
        self.drawn_slots = track_ids(self.drawn[:, 4])  # (the real tracks: the track list's one-track view goes
        #                                                  by these; a track added by hand: its HAND id)
        if self.strips_window:  # (its strips each in a colour of its own while it shows them)
            self.drawn = strip_colours(self.drawn, self.strips_window.shown_owners())
        self._mask_time = time.perf_counter() - started
        if wanted != self.colours_wanted:
            self.colours_wanted = wanted
            self.after_idle(self.sync_custom)  # (the panel's warning)
        self.play_changed()
        if self.hz_window:
            self.hz_window.show_cc()  # (its CC lines on other shapes' channels: the notes just made)
        # placed pictures: their notes are drawn in the picture's own colours (the first picture's: they share them)
        self.picture_owners = np.array([picture_like(sh) for sh in self.shapes], bool)
        self.picture_pal = self.slots_pal()
        self.drawn_all, self.picture_owners_all = self.drawn, self.picture_owners
        self.track_view(follow=True)
        counts = self.note_counts = np.bincount(self.rendered[:, 5], minlength=len(self.shapes)).tolist()
        chans = [1] * len(self.shapes)  # how many channels each shape spreads over (every mode: a paste keeps its
        if len(self.rendered):          # tracks in Single too; each track of yours counted, hunt 2026-11-01)
            ids, which = np.unique(self.rendered[:, 4], return_inverse=True)
            pairs = np.unique(self.rendered[:, 5] * len(ids) + which.ravel())
            chans = np.bincount(pairs // len(ids), minlength=len(self.shapes)).tolist()
        # (an Add between group is one row: between_window.py)
        self.fill_shape_list(counts, chans, most_colours(self.all16()), self.colours_used())
        if self.strips_window:  # (its count / Multi channel line from these notes: made late, Channels changed)
            self.strips_window.show()
        if self.fill_window:  # (anything to fill? from these notes: undo / redo, Channels changed, hunt 10-25)
            self.fill_window.sync()
        if self.lines_window:  # (its line count from these notes, hunt 2026-10-30)
            self.lines_window.sync()
        self.sync_list_selection()
        if self._recolour_owed:  # (the panel's Recolour colours waited for these notes: panel_recolour)
            self.sync_recolour()
        self.roll.request_redraw()
        self.track_list.changed()
        self.sync_toolbar_parts()
        self.update_status()
        self.schedule_autosave()
        self.sync_history()

    def slots_pal(self):
        """The picture colours by slot as the notes have them now (the first picture's, else the first copy's)."""
        src = next((sh for sh in self.shapes if "picture" in sh), None) or next(
            (sh for sh in self.shapes if "picture_notes" in sh), None)
        if src is None:
            return None
        pal = src["picture"]["set"].get("pal") if "picture" in src else src["picture_notes"].get("pal")
        return slot_pal(pal, slot_rule(src), self.picture_use10)

    def notes_rested(self, show=True):
        """The mouse rests mid-drag: the notes are made in a background thread (the window keeps answering the mouse;
        moved again = thrown away). show: put on screen once made (else kept for when the mouse is let go)."""
        # (after the mouse moves still waiting to be handled: a slow redraw can make the timer run out first)
        if self._merges_owed:  # (merged shapes put off while a number box label is dragged: made now, hunt)
            self.remake_merges()
            self._merges_owed = False
        self._late_notes = self.after_idle(self._make_coming, show)

    def notes_key(self):
        """Everything the notes are made from (shapes_changed): a background result is used only for exactly this."""
        mode, split = self.channel_mode.get(), SPLIT_CHOICES[max(self.split_box.current(), 0)][0]
        return (tuple(shape_key(sh) for sh in self.shapes), self.ppq, self.keys, mode, split, self.all16(),
                self.rounds_apart(), self.in_order(), self.keep_colours.get(), cc_texts(self.shapes, mode),
                tuple(json.dumps(sh.get("strips")) for sh in self.shapes),  # (Channel per strip: not in shape_key)
                tuple(sorted(self.own_channels().items())), tuple(sorted(self.own_channels().fixed)),
                tuple(sorted((self.overlap_rule() or {}).items())))

    def own_channels(self):
        """{id: channel} of the tracks added by hand (all_notes own), and of the loaded MIDI's tracks shapes are
        drawn into (FILE0 + its number: laid on like yours, group D)."""
        out = Channels((t["id"], t["ch"]) for t in self.own_tracks)
        files = self.file_tracks()
        for n in self.file_into():
            out[FILE0 + n] = files[n]["ch"]
        out.fixed = frozenset(FILE0 + n for n, p in self.file_props.items() if p.get("ch") is not None)
        return out

    def file_tracks(self):
        """{n: {"name", "ch", "port"}} of the loaded MIDI's tracks (its main channel; none = 1; its first FF 21 port,
        none = A). Its MIDI not loaded: as saved with the project (file_saved), so shapes drawn into them keep their
        channel and are listed and exported under the track's name."""
        lm = self.loaded
        if lm is None:
            return self.file_saved
        got = getattr(self, "_file_tracks", None)
        if got is None or got[0] is not lm:
            ports = port_numbers(lm) % PORTS
            got = self._file_tracks = (lm, {n: with_port({"name": t.name or "",  # (port A: none kept, saves as before)
                                                          "ch": t.channel if t.channel is not None else 0}, int(ports[n]))
                                            for n, t in enumerate(lm.tracks)})
        props = self.file_props
        if not props:
            return got[1]
        # (Track Properties' new name / channel / port, group E)
        out = {}
        for n, d in got[1].items():
            p = props.get(n) or {}
            d = {k: v if p.get(k) is None else p[k] for k, v in d.items()}
            out[n] = d if p.get("port") is None else with_port(d, p["port"])
        return out

    def with_file_tracks(self):
        """own_written + the loaded MIDI's tracks shapes are drawn into, each as a track of its own under its name,
        first (for writing WITHOUT the file: Export MIDI while it isn't loaded, Copy / Export to Domino)."""
        files = self.file_tracks()
        return [with_port({"id": FILE0 + n, "name": files[n]["name"], "ch": files[n]["ch"], "after": 0},
                          files[n].get("port", 0)) for n in self.file_into()] + list(self.own_written())

    def file_rows(self):
        """The loaded MIDI's track numbers in the order they're listed (own_tracks.full_plan's files); none while
        it isn't loaded (waiting_rows)."""
        return file_order(self.loaded, self.file_order, self.file_gone, self.file_props)

    def waiting_rows(self):
        """Its MIDI not loaded (not found): the file's track numbers shapes are drawn into, listed first as tracks
        of their own under their names until it's back (yours keep their "file" places meanwhile)."""
        return self.file_into() if self.loaded is None else []

    def file_into(self):
        """The loaded MIDI's track numbers shapes are drawn into (their into / into_rest), smallest first."""
        files = self.file_tracks()
        if not files:
            return []
        return sorted({v - FILE0 for sh in self.shapes for v in into_ids(sh) + list(sh.get("into_rest") or [])
                       if type(v) is int and v >= FILE0 and v - FILE0 in files})

    def own_written(self, as_mode=None):
        """The tracks added by hand the track list shows and the exports write. Multi channel: all of them. Single
        channel / As drawn (hunt 2026-10-26, user: you get what you see): a laid shape plays every colour on its first
        track, so the tracks Spiderweb added only for its other colours (tinted, "auto") are left out until Multi
        channel again. In every mode a tinted track kept only for a colour gone for now (into_rest: e.g. Repeated colours
        unticked) is left out too, until the colour is back. The same list while nothing changes (the track list
        compares it by identity). as_mode: the list that channels mode would show (not kept: fit_into asks what Multi
        channel shows, for a Domino paste)."""
        own, mode = self.own_tracks, as_mode or self.channel_mode.get()
        if not any(t.get("auto") for t in own):
            return own
        chans = self.own_channels()
        # (a Domino paste: as in Multi channel in every mode, its gap tracks too; user 2026-11-01)
        shown = {v for sh in self.shapes if colours_apart(sh, mode) for k in ("into", "into_gap")
                 for v in (sh.get(k) or []) if type(v) is int}
        if mode != "auto":
            shown |= {p[0] for p in (placed_on(sh, chans) for sh in self.shapes if not colours_apart(sh, mode)) if p}
        # (kept by what it shows: a change counter missed shapes laid again within one change, sweep 2026-10-26)
        key = (id(own), mode, frozenset(shown))
        got = getattr(self, "_own_written", None)
        if got is not None and got[0] == key:
            return got[1]
        out = [t for t in own if not t.get("auto") or t["id"] in shown]
        out = own if len(out) == len(own) else out
        if as_mode is None:
            self._own_written = (key, out)
        return out

    def fit_into(self, made):
        """Lays the shapes drawn / pasted with a track highlighted (all_notes' made: their colour counts, made[5])
        like Domino's paste (user 2026-10-26): colour k on the row cols[k] below the highlighted one (a copy's
        columns: copy_columns; else the next row down), WHATEVER track is there (one added by hand = into it, an
        automatic one = pinned on its slot); past the list's end new tracks of the highlighted one's kind (added by
        hand: "auto", tinted, the channel after the one above; automatic: the next slots). Once laid each colour stays
        on its track; more colours later carry on below the last one. Tracks Spiderweb added that no shape lists go.
        Multi channel only (Single channel / As drawn put every colour on the first), except a copy with its columns
        (the track list's Ctrl+C: its rows kept, like Domino's). No undo step of its own: part
        of the change that asked for it. True if anything changed (the notes are made again)."""
        need, rendered, use10 = made[5], made[2], made[1]
        mode = self.channel_mode.get()
        if mode != "auto":  # (the track list's copies keep their rows in every mode: Domino's; a Domino paste is
            need = {i: n for i, n in need.items()  # laid as in Multi channel too, hunt 2026-11-01)
                    if self.shapes[i].get("cols") or colours_apart(self.shapes[i], mode)}
            if not need:
                return False
        own, changed = list(self.own_tracks), False
        by_id = {t["id"]: t for t in own}
        self.cut_emptied = made[6] if len(made) > 6 else frozenset()  # (this answer's, before least / filled)
        slots, filled = track_slots(rendered, self.pad_tracks(), self.least_tracks()), self.filled_slots(rendered)
        # (every row: an automatic slot s as -1 - s, yours by id, a loaded MIDI's track n as FILE0 + n; group D)
        files = self.file_tracks()
        rows = [(-1 - r if r >= 0 else FILE0 + key_track(-1 - r)) if type(r) is int else r["id"]  # (a part: its track)
                for r in full_plan(self.file_rows(), slots, own, filled)]
        def channel(v):
            if v >= FILE0:
                return files[v - FILE0]["ch"] if v - FILE0 in files else 0
            return by_id[v]["ch"] if v >= 0 else slot_track_channel(-1 - v, use10)[1]

        lays = collections.defaultdict(set)  # (your tracks' channels of their own: a folder's)
        for sh in self.shapes:
            for v, c in zip(sh.get("into") or [], sh.get("into_ch") or []):
                if type(v) is int and c >= 0:
                    lays[v].add(c)

        def plays_on(v):
            """Every channel row v plays on (hunt 2026-10-31: a loaded folder's other channels were handed out again;
            a loaded track without notes plays on none)."""
            lm, n = self.loaded, v - FILE0
            if v >= FILE0 and lm is not None and n < len(lm.tracks):
                if not lm.tracks[n].notes:
                    return ()
                moved = lm.rechannel
                if (moved is not None and n < len(moved) and moved[n] >= 0) or (self.file_props or {}).get(
                        n, {}).get("ch") is not None:
                    return (channel(v),)
                return tuple(np.flatnonzero(lm.tracks[n].chn).tolist())
            return (channel(v),) + tuple(lays.get(v, ()))

        # (tracks kept hidden for a colour gone for now / Single channel: not rows to fill, hunt 2026-10-31: a new
        # shape's colour landed on one and its channel was handed out twice; their own shape still gets them back)
        hidden = shown_hidden = {t["id"] for t in own} - {t["id"] for t in self.own_written()}
        # (a Domino paste fills the rows Multi channel shows, in every mode: hunt 2026-11-01, in Single it skipped
        # tracks Single hides and landed elsewhere than in Multi; those it lays on show again)
        multi_hidden = shown_hidden if mode == "auto" else (
            {t["id"] for t in own} - {t["id"] for t in self.own_written("auto")})
        apart_now = [False]

        def port(v):  # (a new tinted track takes the port of the one above)
            if v >= FILE0:
                return files[v - FILE0].get("port", 0) if v - FILE0 in files else 0
            return by_id[v].get("port", 0) if v >= 0 else 0

        taken = []  # (how many shown rows play on each channel: worked out when the first track is added)

        def new_channel(laid, start):
            """A new tinted track's channel for the shape's next colour (laid: its tracks so far, start: its first
            colour's channel; user 2026-10-31): the first one no shown row plays on yet, going up from start; none
            left = the first one the shape doesn't use yet (a full list gave it its own Ch 4 twice). From its colour
            16 on (17 with channel 10): the channel of its colour one repeat earlier, so each colour keeps its
            channel in every repeat (two of its tracks sharing a channel don't shift them)."""
            if not taken:
                taken.append(collections.Counter(c for r in rows if r not in shown_hidden for c in plays_on(r)))
                taken.append(collections.Counter(c for r in rows if r in shown_hidden and r not in multi_hidden
                                                 for c in plays_on(r)))  # (shown in Multi channel only)
            counts, k, step = taken[0], len(laid), 16 if use10 else 15
            if k >= step:
                ch = channel(laid[k - step])
            else:
                ch = free_channel(start, counts + taken[1] if apart_now[0] else counts, use10)
                if ch is None:
                    ch = free_channel(start, collections.Counter(c for v in laid for c in plays_on(v)), use10)
                if ch is None:
                    ch = start
            counts[ch] += 1
            return ch

        def at_end(kind_own, laid, start):
            """A new track at the end of the rows for the next colour of a shape laid on laid, its first colour on
            channel start (kind_own: added by hand, else an automatic slot)."""
            if kind_own:
                v = new_id(own, self.own_next)
                self.own_next = v + 1
                t = with_port({"id": v, "name": "", "ch": new_channel(laid, start), "after": -1, "auto": True},
                              port(rows[-1]) if rows else 0)
                own.append(t)
                by_id[v] = t
            elif rows and 0 <= rows[-1] < FILE0:  # (the list ends with a track of yours: a new automatic one would
                return at_end(True, laid, start)  # be listed above yours, not at the end; user 2026-11-01)
            else:
                v = -1 - (max([-1 - r for r in rows if r < 0] + [-1]) + 1)
            rows.append(v)
            return v

        known = Known(by_id, files)  # (yours, also the ones added below, and the loaded MIDI's)
        for i, n in need.items():
            sh = self.shapes[i]
            apart_now[0] = mode != "auto" and colours_apart(sh, mode)
            hidden = multi_hidden if apart_now[0] else shown_hidden
            sh.pop("into_new", False)  # (just drawn / pasted)
            have, cols = placed_on(sh, known), sh.get("cols")
            if n <= 0 or not have or (len(have) >= n and not cols):
                if have and len(have) > max(n, 1):  # (colours gone for now: their tracks kept aside, hunt 10-26;
                    cut = max(n, 1)                 # their channels in a folder too, hunt C 2026-10-28: rest_ch)
                    chs, homes = own_chs(sh)
                    rchs, rhomes = rest_ch(sh)
                    sh["into_rest"] = have[cut:] + list(sh.get("into_rest") or [])
                    put_rest_ch(sh, chs[cut:len(have)] + rchs, homes[cut:len(have)] + rhomes)
                    sh["into"] = have[:cut]
                    changed = True
                continue
            rest = list(sh.get("into_rest") or []) if not cols else []
            rchs, rhomes = rest_ch(sh) if not cols else ([], [])
            chs, homes = own_chs(sh)
            chs, homes = chs[:len(have)], homes[:len(have)]
            while len(have) < n and rest and (rest[0] < 0 or rest[0] in known) and rest[0] not in have:
                have.append(rest.pop(0))  # (a colour back: on the track it had, while that track is still there,
                chs.append(rchs.pop(0))   # on the channel it had there)
                homes.append(rhomes.pop(0))
            if not cols:
                if rest and len(have) < n:  # (its track went meanwhile: the rest laid as new ones)
                    rest, rchs, rhomes = [], [], []
                if rest:
                    sh["into_rest"] = rest
                else:
                    sh.pop("into_rest", None)
                put_rest_ch(sh, rchs, rhomes)
                if len(have) >= n:
                    sh["into"] = have
                    if any(c >= 0 for c in chs):
                        sh["into_ch"], sh["into_home"] = chs, homes
                    changed = True
                    continue
            if have[0] not in rows:
                continue
            anchor = rows.index(have[0])
            if cols:  # (a copy's columns, or a Domino paste's tracks: from the highlighted row, gaps kept; an open
                # folder's Ch rows are rows too, a shut one = its first channel: user 2026-10-29)
                lanes = [p for p in self.paste_lanes(rows) if p[0] not in hidden or p[0] in have]
                start = (have[0], chs[0] if chs and chs[0] >= 0 else -1)
                at = lanes.index(start) if start in lanes else next(
                    (k for k, (v, _) in enumerate(lanes) if v == have[0]), 0)
                laid, lchs, made_here = [], [], []
                for k in range(n):
                    r = at + cols[k] if k < len(cols) else at + (cols[-1] if cols else 0) + k - len(cols) + 1
                    while r >= len(lanes):
                        v = at_end(laid[0] >= 0 if laid else have[0] >= 0, laid or have[:1],
                                   lanes[at][1] if lanes[at][1] >= 0 else channel(lanes[at][0]))
                        made_here.append(v)
                        lanes.append((v, -1))
                    laid.append(lanes[r][0])
                    lchs.append(lanes[r][1])
                sh["into"] = laid
                sh.pop("cols", None)
                sh.pop("into_rest", None)
                if any(c >= 0 for c in lchs):
                    sh["into_ch"], sh["into_home"] = lchs, list(laid)
                else:
                    sh.pop("into_ch", None)
                    sh.pop("into_home", None)
                gap = [v for v in made_here if v >= 0 and v not in laid]
                if gap:
                    sh["into_gap"] = sorted(set(sh.get("into_gap", [])) | set(gap))
                if any(v < 0 for v in laid):
                    sh["slots10"] = bool(use10) and any(channel(v) == 9 for v in laid if v < 0)
                changed = True
                continue
            made_here = []
            first_ch = chs[0] if chs and chs[0] >= 0 else channel(have[0])  # (on a folder's Ch row: its channel)
            while len(have) < n:  # (colours gained later too, e.g. Repeated colours ticked: down the rows, whatever
                # track is there, user 2026-10-31)
                if have and have[-1] not in rows:  # (its last track isn't listed: nothing to go on from)
                    break
                r = rows.index(have[-1]) + 1 if have else anchor
                while r < len(rows) and (rows[r] in have or rows[r] in hidden):  # (one of its own tracks dragged
                    r += 1                                                       # below / a hidden one: the next)
                while r >= len(rows):
                    made_here.append(at_end(have[0] >= 0 if have else rows[anchor] >= 0, have, first_ch))
                have.append(rows[r])
            sh["into"] = have
            if sh.pop("cols", None) is not None:  # (laid again from its columns: nothing kept aside)
                sh.pop("into_rest", None)
            gap =[v for v in made_here if v >= 0 and v not in have]
            if gap:
                sh["into_gap"] = sorted(set(sh.get("into_gap", [])) | set(gap))
            if any(v < 0 for v in have):  # (its automatic tracks' channel rule, kept when Use channel 10 changes)
                sh["slots10"] = bool(use10) and any(channel(v) == 9 for v in have if v < 0)
            changed = True
        for sh in self.shapes:  # (each colour's own channel kept beside its track: one per "into" entry)
            if "into_ch" in sh:
                sh.update(aligned_ch(sh))
        # (a colour gone for now keeps its tinted track, hidden while empty: own_written; it comes back on it,
        # hunt 2026-10-26: Repeated colours off / on put them into your own track)
        used ={v for sh in self.shapes for key in ("into", "into_gap", "into_rest") for v in (sh.get(key) or [])
                if type(v) is int}
        kept = [t for t in own if not t.get("auto") or t["id"] in used]
        if kept != self.own_tracks:
            self.own_tracks = kept
            changed = True
        return changed

    def copy_columns(self, idx):
        """Ctrl+C's columns (Domino's paste, user 2026-10-26): {shape's number: each colour's row in the track list,
        counted from the first row of everything copied}, so a paste lays them out the same from the highlighted
        track. Multi channel only (else one channel: nothing to keep), except shapes keeping their tracks in every
        mode (a Domino paste: colours_apart, hunt 2026-11-01); shapes with no notes left out."""
        mode = self.channel_mode.get()
        if not len(self.rendered):
            return {}
        idx = [i for i in idx if colours_apart(self.shapes[i], mode)]
        tl, r = self.track_list, self.rendered
        own = self.own_channels()
        rows = {}
        # (the notes sorted by shape once, each shape's read from there: a search through every note per shape froze
        # Ctrl+C for seconds on big projects, hunt 2026-10-26)
        by = np.argsort(r[:, 5], kind="stable")
        owners = r[by, 5]
        for i in idx:
            sh = self.shapes[i]
            a, b = np.searchsorted(owners, [i, i + 1])
            mine = r[by[a:b]]
            if not len(mine):
                continue
            if placed_on(sh, own):  # (laid on tracks: its own list, one per colour)
                ids = track_ids(np.array(self.laid_slots(sh), np.int64)).tolist()
            else:
                ids = track_ids(np.array(self.colour_slots(sh, mine), np.int64)).tolist()
            at_rows = [tl.row_of_id(v) for v in ids]
            if None not in at_rows:
                rows[i] = at_rows
        if not rows:
            return {}
        first = min(min(v) for v in rows.values())
        return {i: [v - first for v in got] for i, got in rows.items()}

    def colour_slots(self, sh, mine):
        """Each colour's slot of a shape not laid on tracks (mine: its rendered notes, some): where most of that
        colour's notes are now, found by start + key; one colour = the slot most of its notes are on (also for a
        colour none of whose notes were found). "Repeated colours get new channels": each repeat's colours are
        colours of their own, in the order all_notes lays them (hunt 2026-10-26: repeats landed on other tracks).
        Notes of several colours on one start + key (stacked pasted tracks) count for each of those slots, shared
        out; a colour whose best slot another colour took goes on its next best one if that's about as good (a
        tester's 32-track paste, 2026-10-31: tracks lying on others were laid onto their tracks and merged)."""
        vals, counts = np.unique(mine[:, 4], return_counts=True)
        most = int(vals[counts.argmax()])
        n, t = self.notes_tracks(dict(sh, _rounds=True) if self.rounds_apart() and has_colours(sh) else sh)
        n = np.asarray(n, np.int64).reshape(-1, 4)
        colour = (np.unique(np.asarray(t, np.int64), return_inverse=True)[1].ravel() if t is not None
                  else np.zeros(len(n), np.int64))
        if not len(colour) or colour.max() == 0:
            return [most]
        have = mine[:, 0] * 1024 + mine[:, 2]
        o = np.argsort(have, kind="stable")
        have, slot = have[o], mine[o, 4]
        want = n[:, 0] * 1024 + n[:, 2]
        lo, hi = np.searchsorted(have, want), np.searchsorted(have, want, side="right")
        many = hi - lo  # (rendered notes on each note's start + key)
        each = np.repeat(lo, many) + np.arange(int(many.sum())) - np.repeat(np.cumsum(many) - many, many)
        colours = int(colour.max()) + 1
        pair = np.repeat(colour, many) * len(vals) + np.searchsorted(vals, slot[each])
        weight = np.bincount(pair, 1.0 / np.repeat(many, many), colours * len(vals)).reshape(colours, len(vals))
        best = weight.max(axis=1)
        out, taken = [most] * colours, set()
        for k in sorted(range(colours), key=lambda k: -best[k]):  # (the surest colours first)
            if best[k] <= 0:
                continue  # (none of its notes found: the slot most notes are on)
            order = np.argsort(-weight[k], kind="stable")
            pick = order[0]
            for j in order:
                if weight[k, j] < best[k] * 0.5:
                    break
                if int(vals[j]) not in taken:
                    pick = j
                    break
            out[k] = int(vals[pick])
            taken.add(out[k])
        return out

    def into_target(self):
        """The track highlighted in the track list new shapes are laid on (placed_on's entry: a track added by
        hand's id, an automatic track = -1 - its slot, a loaded MIDI's track n = FILE0 + n), or None (nothing, or a
        damaged loaded track: the export only copies those as they are)."""
        tl = self.track_list
        s = tl.current
        if s is None:
            return None
        own = tl.own_track(s)
        if own is not None:
            return own["id"]
        if s < 0:
            n, lm = tl.file_part(s)[0], self.loaded  # (a part: its track, PLAN #11 D)
            whole = lm is None or n >= len(lm.mf.tracks) or lm.mf.tracks[n].whole
            return FILE0 + n if whole and n in self.file_tracks() else None
        return -1 - s if s < HAND else None

    def paste_lanes(self, rows):
        """fit_into's rows (placed_on entries) as the places a paste's columns count: (entry, channel) each, an OPEN
        folder's Ch rows one each (in the list's order), any other row once, a shut folder on its first channel
        (user 2026-10-29: "if mixed track is not expanded, paste at their 1ch position"), -1 = the track's own."""
        tl, out = self.track_list, []
        chans = tl.row_chans()
        # (a loaded track with parts is in rows once per row, track or part, in the list's order: each its own row
        # on its own channels, hunt 2026-10-29: every part's column landed on the track's first channel)
        split = {}
        tl.tracks()
        for s in (int(v) for v in tl._all):
            if s < 0 and tl.file_part(s)[1] is not None:
                split.setdefault(tl.file_part(s)[0], []).append(s)
        seen = {}
        for v in rows:
            lid = -1 - (v - FILE0) if v >= FILE0 else HAND + v if v >= 0 else -1 - v
            own_ch = False
            if v >= FILE0 and v - FILE0 in split:
                k = seen[v] = seen.get(v, -1) + 1
                lid, own_ch = split[v - FILE0][min(k, len(split[v - FILE0]) - 1)], True
            row = tl.row_of_id(lid)
            mine = chans[row] if row is not None and row < len(chans) else ()
            if len(mine) > 1 and lid in tl.open:
                out += [(v, int(c)) for c in mine]
            else:
                out.append((v, int(mine[0]) if len(mine) > 1 or (own_ch and mine) else -1))
        return out

    def first_ch(self, target):
        """A mixed track's first channel (its top Ch row) when target (a placed_on entry) is one, else None: where
        a shape drawn / pasted with its track row highlighted goes (user 2026-10-29)."""
        if target is None or target < 0:
            return None
        tl = self.track_list
        row = tl.row_of_id(-1 - (target - FILE0) if target >= FILE0 else HAND + target)
        chans = tl.row_chans()
        mine = chans[row] if row is not None and row < len(chans) else ()
        return int(mine[0]) if len(mine) > 1 else None

    def put_into_current(self, sh, relay=True):
        """A new shape is laid from the highlighted track (Draw into a track; fit_into lays its colours). relay
        False (duplicate) or nothing highlighted: one already laid in this project stays where it is. True if it
        can't go in (can_go_in): it stays automatic."""
        target = self.into_target() if relay else None
        # (a paste copied from another project / before New or Open / a shared line: its tracks aren't these)
        foreign = relay and sh.pop("into_from", None) != self.project_token
        sh.pop("into_from", None)
        if target is None:
            if foreign or not self.in_own_track(sh):
                for key in ("into", "cols", "into_gap", "into_rest", "slots10", "into_new"):
                    sh.pop(key, None)
            sh.pop("cols", None)
            return False
        sh.pop("into_gap", None)
        sh.pop("into_rest", None)
        keep_home(sh)
        if not can_go_in(sh):
            for key in ("into", "cols", "slots10", "into_new"):
                sh.pop(key, None)
            return True
        sh["into"] = [target]
        sh["into_new"] = True  # (its colours go down the rows like Domino's paste: fit_into)
        tl = self.track_list
        ch = tl.shown_ch()  # (a folder's "Ch n" row highlighted: on that channel there, check-in C)
        if ch is None and tl.current is not None and tl.current < 0 and tl.file_part(tl.current)[1] is not None:
            ch = tl.part_channel(tl.current)  # (a loaded track's part: on its channel, PLAN #11 D)
        if ch is None:  # (a mixed track's own row: its first channel, open or shut, user 2026-10-29)
            ch = self.first_ch(target)
        if ch is not None and target >= 0:
            sh["into_ch"], sh["into_home"] = [ch], [target]
        return False

    def laid_slots(self, sh):
        """The slot each colour of a shape laid on tracks has in the notes now (all_notes' entry_slot), in order."""
        own = self.own_channels()
        return laid_slots_of(sh, placed_on(sh, own), own, self.picture_use10)

    def in_own_track(self, sh):
        """True if the shape is laid on tracks (placed_on: drawn / pasted with a track highlighted)."""
        return bool(placed_on(sh, self.own_channels()))

    def cc_busy(self, mode, shapes=None):
        """all_notes' busy for these shapes (cc_busy)."""
        return cc_busy(self.shapes if shapes is None else shapes, self.ppq, mode)

    def _make_coming(self, show, follow=False):
        """follow: shown when made even if the shapes have moved on since (then the next spot's are made)."""
        self.stale_ok += 1  # (what it reads of the notes on screen: as they are, never waited for)
        try:
            self._start_coming(show, follow)
        finally:
            self.stale_ok -= 1

    def _start_coming(self, show, follow):
        self._late_notes = None
        show = show or not (self.roll.drag or self.scrubbing)  # (the drag called off: no let go to wait for)
        job, key = self._coming, self.notes_key()
        if job and job["key"] == key and (job["got"] is not None or not job["done"]):  # (made / being made already)
            if not job["done"]:
                job["show"] = job["show"] or show
            elif show:
                self.shapes_changed(now=True)
            return
        if job:  # (made for another spot: it stops at its next shape, these start at once)
            job["stop"] = True
        shapes = [from_key(s) for s in key[0]]  # (the thread's own copy: the drag goes on with the real ones)
        for sh, st in zip(shapes, key[10]):
            if st != "null":  # (Channel per strip, left out of shape_key)
                sh["strips"] = json.loads(st)
        for sh in shapes:
            if "cut" in sh and moved_by(as_made(sh)) is None:
                completed(sh)
        job = self._coming = {"key": key, "show": show, "done": False, "got": None, "own": {}, "stop": False,
                              "follow": follow, "stash": {}, "used": [], "top": [], "depth": 0, "asked": [],
                              "deps": {}}
        # (stash: all_notes' own, for lay_again; top / depth / asked / deps: as App.notes_tracks' _notes_top /
        # _notes_depth / _notes_asked / _notes_deps)
        known = self._notes_cache  # (only read in the thread; its own new notes go to job["own"])
        sels, fast, rule = frozenset(self.sels), speed.loops(), stack_rule(self)
        voiced, wide = self.channel_mode.get() == "auto", self.all16()
        heard = (self.channel_mode.get(), self.heard_rule())

        def notes_tracks(sh, keys=None, edges=True):
            if job["stop"]:
                raise Stopped
            if not voiced:  # (as App.notes_tracks: Recolour left out too, not a Domino paste's)
                sh = recolour_alone(one_voice(sh))
            elif "recolour" in sh:
                sh = with_most(sh, wide)
            if "notes" in sh:  # (as App.notes_cache_key)
                sh = as_heard(sh, *heard)
            keys = key[2] if keys is None else keys
            k = (shape_key(sh), key[1], keys)
            if not edges and keys == key[2] and past_edges(sh, keys, key[1]) is not None:
                k += ("whole",)  # (as App.notes_tracks: the notes before a page are never cut)
            if job["asked"]:
                job["asked"][-1].add(k)
            got = known.get(k) or job["own"].get(k)
            if got is None:
                job["depth"] += 1
                job["asked"].append(set())
                try:
                    # (past the edges: made whole, then cut, as App.notes_tracks does: engine.edge_made)
                    got = edge_made(sh, key[1], keys, notes_tracks) if edges and keys == key[2] else None
                    if got is None:
                        got = fx_notes(sh, key[1], keys, lambda s: notes_tracks(s, keys, False))
                finally:
                    job["depth"] -= 1
                    deps = job["asked"].pop()
                job["deps"][k] = deps
                got = shared_arrays(got, job["own"])
                job["own"][k] = got
            job["used"].append(k)
            if not job["depth"]:
                job["top"].append(k)
            return got

        def work():
            started = time.perf_counter()
            try:
                busy = None if key[9] is None else cc_busy(  # (worked out here: a long Arpeggio's took 0.2 s)
                    [json.loads(t) if t else {} for t in key[9][1]], key[1], key[4])
                own = Channels(key[11])  # (as own_channels: the loaded tracks given a channel too)
                own.fixed = frozenset(key[12])
                got = all_notes(shapes, *key[1:6], notes_tracks, rounds=key[6], order=key[7], keep=key[8],
                                busy=busy, own=own, overlaps=dict(key[13]) or None, stash=job["stash"])
                # (the same time as shapes_changed's: making the notes only, not the orders below)
                job["time"] = time.perf_counter() - started
                if job["stop"]:
                    raise Stopped
                # (the piano roll's orders for painting them too: roll_draw.start_order / paint_order)
                rendered, order = got[2], None
                start = np.argsort(np.ascontiguousarray(rendered[:, 0]), kind="stable")
                if fast:
                    pics = np.array([picture_like(sh) for sh in shapes], bool)
                    order = ((sels, pics.tobytes(), len(pics), bool(got[1]), None, rule),
                             painting_order(fast, rendered, start, sels, pics, key[2], got[1], None, rule))
                job["drawn"] = (start, order)
                job["got"] = got
            except Exception:  # (MemoryError too: made again the normal way, which tells about it; Stopped)
                job["got"] = None
            job["done"] = True
        job["thread"] = threading.Thread(target=work, daemon=True, name="notes")
        job["thread"].start()
        self.after(15, self._coming_check, job)

    def _coming_check(self, job):
        if job is not self._coming:  # (the notes were made the normal way meanwhile)
            return
        if not job["done"]:
            self.after(15, self._coming_check, job)
            return
        if self._bg_notes:  # (after an edit: taken in; something changed since without shapes_changed: made again)
            self.stale_ok += 1  # (notes_key reads the notes on screen as they are, as _make_coming did)
            try:
                changed = job["got"] is not None and self.notes_key() != job["key"]
            finally:
                self.stale_ok -= 1
            if changed:
                self._make_coming(True)
            else:
                self.finish_notes()
            return
        held = self.roll.drag or self.scrubbing
        if not self.notes_late or not (job["show"] or not held):
            return
        if job["got"] is None:  # (failed: made the normal way, which tells about it; held: done when let go)
            if not held:
                self.shapes_changed(now=True)
        elif self.notes_key() == job["key"]:
            self.shapes_changed(now=True)
        elif job["follow"] and held:  # (the centre point moved on meanwhile: these shown, the new spot's made)
            self.shapes_changed(now="follow")
            self.notes_late = True
            self._make_coming(True, True)

    def notes_for(self, shapes, sels):
        """The notes for a shape list NOT on screen yet (the Fold tracks preview, fold_window) made in a background
        thread, as _make_coming does; returns the job (job["done"] once made). Taken in by putting these very shapes
        in app.shapes and calling shapes_changed(now=True): notes_made finds the job (same notes_key). Never shown
        by itself (nothing is late on screen meanwhile)."""
        old = self._coming
        if old:  # (another spot's: stopped, and never taken for these)
            old["stop"] = True
        self._coming = None
        keep = self.shapes, self.sels
        self.shapes, self.sels = shapes, set(sels)  # (read by notes_key / the painting order, only during this call)
        try:
            self._make_coming(False)
        finally:
            self.shapes, self.sels = keep
        return self._coming

    def notes_made(self, any_spot=False):
        """The notes made in the background for the shapes exactly as they are now (waited for if still being made),
        else None. Called by shapes_changed, which then makes them itself. any_spot: a finished job's notes even if
        the shapes have changed since."""
        job, self._coming = self._coming, None
        if job is None or job["key"] != self.notes_key() and not (any_spot and job["done"]):
            if job:  # (made for another spot: stopped, so it doesn't slow down making these)
                job["stop"] = True
            return None
        job["thread"].join()
        if job["got"] is None:
            return None
        cache = self._notes_cache
        for k, v in job["own"].items():  # (equal to notes remembered before the job: kept once; the thread only
            job["own"][k] = shared_arrays(v, cache)  # compared its own: 2 M notes kept ~60 MB more an edit)
        cache.update(job["own"])
        for k in job["used"]:  # (the notes it used are the shapes' as they are now: kept by forget_old_notes)
            self._notes_used[k] = self.shapes_gen
        self._notes_top.update(job["top"])
        self._notes_deps.update(job["deps"])
        self.trim_notes()
        if job["own"]:
            self._notes_time = job["time"]
        self._make_time = job["time"]
        self.roll.ready_order(job["got"][2], *job["drawn"])
        self._job_stash = job["stash"]
        return job["got"]

    def catch_up_notes(self, late=True):
        """The notes left for later while dragging (shapes_changed): now (late: a drag let go, in the background
        when that's slow, late_ok; else made here, for what reads them next)."""
        if self._bg_notes:
            if not late:
                self.finish_notes()
            return
        if self._late_notes or self.notes_late:
            self.shapes_changed(now="late" if late else True)

    def late_ok(self):
        """The notes can be made in the background (shapes_changed): the last making took longer than LATE_S, and the
        notes on screen still belong to these shapes (the same ones in the same places, maybe more after them)."""
        if self._make_time + self._mask_time <= LATE_S or self._merges_owed:
            return False
        if self.companion is not None or getattr(self, "fold_window", None):  # (Draw on Domino / Fold tracks: their
            return False                                                       # own notes, made at once)
        made = self._made_shapes
        return len(self.shapes) >= len(made) and all(a is b for a, b in zip(self.shapes, made))

    def finish_notes(self):
        """Notes still being made in the background (shapes_changed): waited for and taken in now."""
        if self._bg_notes:
            self._bg_notes = False
            self.shapes_changed(now=True)

    def shape_edited(self, moving=False):
        """The selected shape was changed with the mouse (moving: just dragged to another place)."""
        self.sync_points()
        if self._rows["gliss"]:  # (a Glissando run's step / keys as its ends move)
            self.sync_gliss()
        self.shapes_changed(moving=moving)

    def add_shape(self, sh, name=None):
        if self.mask.get() and not is_mask(sh) and can_mask(sh):  # (drawn with the Mask tick on: a mask, mask.py)
            sh["mask"] = self.new_mask()
        self.push_undo(name=name or tr("app.draw", shape_label=self.shape_label(sh)))
        left_out = self.put_into_current(sh)
        self.shapes.append(sh)
        stir = getattr(self, "stir_window", None)
        if stir and stir.drawing and not stir.drawing["pick"]:  # (a Stir area being drawn: this stroke goes into it)
            stir.took(sh)
            if self.companion is not None:  # (shown on Domino while it's drawn, never sent: Stir uses it up)
                sh["domino_send"] = 1
        elif self.companion is not None:  # (drawn on Domino: goes into Domino once finished, domino_companion.py;
            self.companion.drawn(sh)      # also with the Stir window open, hunt 2026-11-06)
        self.select(len(self.shapes) - 1)
        self.shapes_changed()
        if left_out:
            self.status.config(text=tr("tracks.not_into"))
        if sh["kind"] == "curve":  # the first curve: its anchors and handles
            self.tips.show("curves_pen", wait=True)
        self.tips.show("undo", wait=True)  # the first shape: how to take it back

    def on_use_ch10(self):
        """The "Use channel 10" tick. Turned off: Colours / Add between turns over 16 channels go down to 15 at
        once, as an undo step (user: not kept for when it's ticked again). Drawn colours (drawer areas, a stroke's
        colour) stay: the drawer and panel say so in orange. Ctrl+Z on that step ticks it again (_restore)."""
        if not self.use_ch10.get():
            most = most_colours(False)
            if self.over_15(self.shapes, clamp=False):
                self.push_undo(name=tr("app.use_ch10_off"))
                state, name, sel, hz = self.undo_stack[-1]
                self.undo_stack[-1] = (state, name, tuple(sel) + (CH10_OFF,), hz)  # (the step's mark: _restore)
                self.over_15(self.shapes)
            if (self.defaults.get("cycle") or {}).get("n", 0) > most:
                self.defaults["cycle"] = dict(self.defaults["cycle"], n=most)
        self.on_channel_mode()

    def on_channel_mode(self):
        self.shapes_changed()
        self.sync_custom()
        self.sync_colours()
        self.show_colour_limit()
        if self.hz_window:
            self.hz_window.show_clash(self.hz_window.target())  # (Multi channel only)
        self.path_follow()  # (its "needs Multi channel" note)
        if self.channel_mode.get() == "auto":
            self.tips.show("channels", wait=True)

    def on_pad_rounds(self):
        """"Keep empty tracks" ticked / unticked: the export's tracks change (the title's *, the autosave, the track
        list's rows; hunt 10-27)."""
        self.update_status()
        self.schedule_autosave()
        tl = getattr(self, "track_list", None)
        if tl is not None and tl.winfo_ismapped():
            tl._sheet = tl._all_sheet = None
            tl.request_redraw()

    def show_colour_limit(self):
        """The open drawer and Image window show the colours allowed now (16 with "Use channel 10")."""
        d, im = getattr(self, "drawer", None), self.image_window
        try:
            if d is not None and d.winfo_exists():
                d.draw_swatches()
                d.show_colours_count()
            if im is not None and im.winfo_exists():
                im.show_settings()
        except tk.TclError:
            pass

    def select(self, i, toggle=False):
        """Select shape i only (None = nothing), or with toggle add it to / take it out of the selection."""
        if not toggle:
            self.select_many([] if i is None else [i], i)
        elif i in self.sels:
            rest = self.sels - {i}
            self.select_many(rest, self.sel if self.sel in rest else max(rest, default=None))
        else:
            self.select_many(self.sels | {i}, i)

    def select_many(self, indices, primary):
        self.commit_typing()  # (first: it belongs to the shapes picked until now)
        self._hz_ok = None
        ty = self.roll.typing
        self.sels = set(indices)  # first: ending the typing refreshes the panel, which reads the selection
        self.sel = primary
        if ty and (ty["i"] not in self.sels if ty["i"] is not None else bool(self.sels)):
            self.roll.end_typing()  # another shape picked (or the text deleted): done typing
        self.parts = set()
        self.stroke = self.ripple_pick = None
        self._edit_key = self._scrub = None
        self.sync_panel()
        self.roll.request_redraw()
        self.update_status()

    def set_parts(self, parts, main=None):
        """Highlight these lines / curves of the selected funnel. `main` = the one clicked (its linked curves get
        another colour); None keeps the last one."""
        if main is not None or not parts:
            self.part_main = main
        if set(parts) != self.parts:
            self.parts = set(parts)
            self.sync_funnel()
        self.roll.request_redraw()

    def set_stroke(self, k):
        """Pick stroke k of the selected custom shape (None = none): Del deletes it, a curve shows its handles."""
        self.stroke = k
        self.sync_custom()
        self.sync_freehand()
        self.roll.request_redraw()

    def delete_picked(self):
        """The Edit menu's Delete: the Tempo pane's picks if it was clicked last, else the selected shapes."""
        if getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.delete_picked()
        self.delete_selected()

    def select_all(self):
        """Ctrl+A: every shape; the Tempo pane clicked last: its whole line."""
        if getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.select_all()
        shown = [i for i in range(len(self.shapes)) if i not in self.hidden_shapes]  # (one track shown: its shapes)
        if shown:
            self.select_many(shown, self.sel if self.sel in shown else shown[-1])
            self.picked_all = frozenset(self.sels)  # (a copy for Domino takes the tempo along: copied_whole)

    def on_list_select(self, _):
        rows = self.shape_rows()  # (an Add between group is one row: all its shapes)
        cur = set(self.listbox.curselection())
        shown = {r for r, row in enumerate(rows) if self.sels.intersection(row)}
        if cur != shown:
            added = cur - shown
            picked = {i for r in cur if r < len(rows) for i in rows[r]}
            primary = (rows[max(added)][0] if added else self.sel if self.sel in picked else
                       max(picked, default=None))
            self.select_many(picked, primary)

    def on_list_double(self, e):
        """Double-click a Shapes list row: the piano roll jumps to that shape (user 2026-10-26; the track list showing
        = back to the piano roll first)."""
        rows = self.shape_rows()
        r = self.listbox.nearest(e.y)
        if not 0 <= r < len(rows):
            return
        if self.tracks_shown():
            self.toggle_tracks()
        self.show_shapes(rows[r])

    def show_shapes(self, idxs):
        """The piano roll's view moved (zoom kept) so shapes idxs show: their drawing and notes in the middle, or
        their start at the left edge / their top key at the top when they don't fit."""
        r = self.roll
        if r.sx is None or r.sy is None:
            return
        bs, ps = [], []
        for i in idxs:
            if i < len(self.shapes):
                for b, p in cached_path(self.shapes[i]):
                    bs.append(b)
                    ps.append(p)
        notes = self.rendered
        if len(notes):
            mine = notes[np.isin(notes[:, 5], list(idxs))]
            if len(mine):
                bs += [mine[:, 0].min() / self.ppq, mine[:, 1].max() / self.ppq]
                ps += [mine[:, 2].min(), mine[:, 2].max()]
        if not bs:
            return
        lo, hi, k0, k1 = float(min(bs)), float(max(bs)), float(min(ps)), float(max(ps))
        wide = (r.winfo_width() - r.kb_w) / r.sx
        tall = (r.winfo_height() - r.ruler_h) / r.sy
        r.view_t = (lo + hi - wide) / 2 if hi - lo <= wide * 0.9 else lo - wide * 0.05
        r.view_top = (k0 + k1 + tall) / 2 if k1 - k0 + 1 <= tall else k1 + 1
        r.clamp_view()
        r.request_redraw()

    def delete_selected(self, only=None, name=None):
        """Delete the selected shapes, or `only` these (Delete while dragging them): the rest stay selected."""
        gone = set(self.sels if only is None else only)
        if not gone:
            return
        self.roll.end_typing()  # first: it refreshes the panel, which must still see the old shapes
        self.push_undo(name=name or tr("app.delete"))
        for i in sorted(gone, reverse=True):
            del self.shapes[i]
        rest = [i - sum(j < i for j in gone) for i in sorted(self.sels - gone)]  # (numbers after the gone ones drop)
        self.select_many(rest, max(rest, default=None))
        self.shapes_changed()

    def delete_all(self):
        if not self.shapes:
            return
        if not messagebox.askyesno(tr("app.spiderweb_2"), trn("app.are_you_sure_you_want_to", len(self.shapes), n=len(self.shapes)),
                                   icon="warning", parent=self):
            return
        self.roll.cancel_draft()  # first: it refreshes the panel, which must still see the old shapes
        self.push_undo(name=tr("app.delete_all"))
        self.shapes.clear()
        self.select(None)
        self.shapes_changed()

    def colour_window_open(self):
        """True while the Stir, Warp or Liquify window is open (recolour.keeping: their quick work is kept then)."""
        return bool(self.stir_window or self.warp_window or self.liquify_window)

    def colour_window_closed(self):
        """A Stir / Warp / Liquify window closed: none of them open now = their quick work let go (memory use,
        user 2026-11-10: the first drag after opening one again makes it, ~0.2 s at 2 M notes)."""
        if not self.colour_window_open():
            from notes.recolour import forget_kept
            forget_kept()

    def new_project(self):
        """The Project tab's New: a new MIDI. The shapes and the tempo line go (their Select boxes / picks too); PPQ,
        keys, the output file, channel settings and the Domino start stay; BPM and the time signature go back to
        MIDI's 120 and 4/4 (user). Asked first (unsaved work: save it first?). NOTHING SURVIVES (user 2026-10-25:
        Ctrl+Z after New brought the tempo back without the loaded MIDI): like Open, the undo / redo steps go."""
        from notes.recolour import forget_kept
        forget_kept()  # (quick Stir changes' kept work: hunt 11-07, ~0.45 GB on a 3 M-note shape)
        boxes =(self.pvar["bpm"].get(), self.project_file)
        try:
            usual = calc(boxes[0]) == 120
        except ValueError:
            usual = False
        plain = not any(self.tempo.get(k) for k in ("points", "catch", "keysigs")) and sigs_of(self.tempo) == [DEFAULT_SIG]
        plain = plain and not self.own_tracks and self.start_track
        if not self.shapes and plain and usual and not boxes[1] and self.loaded is None and self.midi_link is None:
            return
        ask = tr("app.new_ask") + ("\n\n" + tr("load.new_closes") if self.midi_link is not None else "")
        if self.saved_key() == self._saved_shapes or not self.shapes and plain:
            if not messagebox.askyesno(tr("app.spiderweb_2"), ask, icon="warning", parent=self):
                return
        else:  # (unsaved work: one question, save it first?)
            answer = messagebox.askyesnocancel(tr("app.spiderweb_2"), ask + "\n\n" + tr("app.new_save_first"),
                                               icon="warning", default="yes", parent=self)
            if answer is None or answer and not self.save_project():
                return
        if self.midi_link is not None:  # (a loaded MIDI goes too)
            self.midi_link = None
            self.drop_finding()
            self.stop_loaded()
            self.drop_read_ahead()
            self.loaded = None
            self.roll.forget_loaded()
        self.roll.cancel_draft()  # first: it refreshes the panel, which must still see the old shapes
        self.roll.drop_lasso()  # (a waiting loop would cut the new one)
        self.stop_play()
        self.undo_stack.clear()  # (nothing of the old project comes back, like Open)
        self.roll.warp_drop_pick()
        self.redo_stack.clear()
        self._redo_kept = self._trimmed = None
        self._saved_shapes = self._saved_mark = None
        self.set_playhead(0.0)
        if hasattr(self, "track_list"):  # (its mutes, highlight and picks belonged to the old song)
            self.track_list.forget()
        self.shapes.clear()
        self.project_file = None  # (Save asks for a name again)
        self.tempo = empty_tempo()
        self.own_tracks, self.own_next = [], 0
        self.start_track = True  # (one empty Track 1, nothing highlighted: user 2026-10-26)
        # (the old song's deleted notes, dragged track order and track names: nothing to do with the new one, hunt)
        self.deleted, self.file_order, self.file_saved, self.midi_missing = [], None, {}, False
        self.file_gone, self.file_props = [], {}
        self.project_token = os.urandom(8).hex()  # (copies from before New: not laid on these tracks)
        self.tempo_pane.forget()
        self.roll.box_kept = None
        self.roll.view_t = 0.0  # (back to bar 1, zoom kept: user)
        self.select(None)
        self.pvar["bpm"].set("120")  # (remakes what hangs on it: on_project_change)
        self.tempo_changed()
        self.shapes_changed()
        self.sync_ppq_lock()  # (no loaded MIDI any more: PPQ can be changed again)
        self.sync_history()
        self.sync_window_title()

    def duplicate(self):
        if not self.sels:
            return
        shift = max(self.snap_beats(), 1 / self.ppq) if self.snap_beats() else 1.0  # (a step under a tick: on top)
        self.add_copies([self.without_cc(dict(self.shapes[i])) for i in sorted(self.sels)], shift,
                        tr("app.duplicate"), relay=False)

    def time_picked(self):
        """Were the selected shapes picked by a ruler band (a time selection)? Then copies take their CC events
        along; picked by clicking / a Select box: notes only (user 2026-10-29, Domino's way)."""
        kept = getattr(self.roll, "box_kept", None)
        areas = kept[0] if kept else []
        return bool(areas) and all(box_kind(a) == "ruler" for a in areas)

    def without_cc(self, sh):
        """sh (a copy about to be made) without its CC events unless the shapes were picked by time (time_picked)."""
        if "cc" in sh and not self.time_picked():
            sh.pop("cc", None)
        return sh

    def add_copies(self, shapes, shift, name=tr("app.paste"), relay=True):
        """Add copies of shapes moved shift beats later, and select them. Returns how many had 16 colours brought
        down to 15 (copied from a project with "Use channel 10" into one without; also said in the status line).
        relay: laid from the highlighted track (put_into_current; False = a duplicate: where they are)."""
        self.push_undo(name=name)
        first = len(self.shapes)
        left_out = 0
        for sh in shapes:
            new = copy.deepcopy(sh)
            new["pts"] = [[b + shift, p] for b, p in new["pts"]]
            # (laid from the highlighted track by its columns, like Domino's paste; a duplicate / nothing highlighted:
            # one laid in this project stays where it is)
            left_out += self.put_into_current(new, relay)
            self.shapes.append(new)
        fresh_marks(self.shapes[first:])
        between_copied(self.shapes, first)  # (a whole Add between group: a new group; parts of one: plain shapes)
        clamped = 0 if self.all16() else len(self.over_15(self.shapes[first:]))
        cut = self.fit_pasted_pictures(first)  # (pictures: the project's one channel-10 setting)
        self.select_many(range(first, len(self.shapes)), len(self.shapes) - 1)
        self.shapes_changed()
        said = [trn("app.pasted_15", clamped, n=clamped)] if clamped else []
        said += [trn("app.pasted_picture_15", cut, n=cut)] if cut else []
        said += [tr("tracks.not_into")] if left_out else []
        if said:
            self.status.config(text="  ".join(said))
        return clamped

    def over_15(self, shapes, clamp=True):
        """The shapes whose Colours / Add between turns go over 15 channels (only allowed with "Use channel 10"),
        brought down to 15 (clamp). Merged shapes' parts count with them."""
        most = most_colours(False)
        found = []
        for sh in shapes:
            over = []
            for t in [sh] + merge_parts(sh):
                if (t.get("cycle") or {}).get("n", 0) > most:
                    over.append((t["cycle"], "n"))
                b = (t.get("between") or {}).get("set")
                if b and b.get("colours", 0) > most:
                    over.append((b, "colours"))
            if over:
                found.append(sh)
            if clamp:
                for c, k in over:
                    c[k] = most
        return found

    def glue_selected(self):
        """Right-click → Glue notes: touching notes on a key in the selected shapes become one (glue.py), inside the
        kept Select boxes if there are any, else all their notes. The shapes remember it."""
        if not self.note_tool_sels():  # (pictures take no note tools)
            return
        areas = self.roll.kept_box()
        shapes = [self.shapes[i] for i in sorted(self.note_tool_sels())]
        before = sum(len(self.notes_of(sh)) for sh in shapes)
        self.push_undo(name=tr("app.glue"))
        for sh in shapes:
            was = copy.deepcopy(sh.get("glue"))
            if not areas:
                sh["glue"] = True
            else:
                box = glue_box(np.concatenate(cached_arrays(sh)))
                for area in areas:
                    shares = glue_shares(area, box)
                    if shares:  # (this Glue's first box: a pass of its own on what earlier glue left)
                        sh["glue"] = glue_added(sh.get("glue"), shares, sh.get("glue") == was)
            # (what this glue added is done after the pages there are now, like the last thing done; earlier glue
            # keeps its place, shapes the box missed stay as they were: hunt 2026-11-07)
            fx = glue_after(sh.get("fx"), sh.get("glue"), was, not areas)
            if was is True and fx == sh.get("fx"):  # (a box on a shape glued all over, done at the same place: none)
                sh["glue"] = True
            if fx:
                sh["fx"] = fx
            else:
                sh.pop("fx", None)
        after = sum(len(self.notes_of(sh)) for sh in shapes)
        self.shapes_changed()
        self.status.config(text=tr("app.glued", notes=trn("n.notes", before, n=before), after=after))

    def unglue_selected(self):
        """Right-click → Remove glue: the selected shapes' notes as they were before any glue."""
        shapes = [self.shapes[i] for i in sorted(self.sels) if self.shapes[i].get("glue")]
        if not shapes:
            return
        self.push_undo(name=tr("app.remove_glue"))
        for sh in shapes:
            del sh["glue"]
            tidy_glue(sh)
        self.shapes_changed()

    def picked(self):
        """(shape, stroke number) of the picked stroke of the selected custom shape, or None."""
        sh = self.selected()
        k = self.roll.picked_stroke(sh) if len(self.sels) == 1 else None
        return None if k is None else (sh, k)

    def copy_selected(self, whole=False, cut=False):
        """Ctrl+C: the selected shapes (or with curves highlighted: the curve's shape; a stroke picked: it). The
        Tempo pane clicked last: its points and catch-ups. Their CC events only when picked by time (without_cc);
        cut: always (cut + paste = a move, the CC events would be gone with the shape)."""
        if not whole and getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.copy()
        if not whole and self.roll.curve_parts():
            return self.roll.copy_curve()
        if not whole and self.picked():
            return self.roll.copy_stroke(*self.picked())
        if self.sels:
            shapes = [sh if cut else self.without_cc(sh)
                      for sh in copy.deepcopy([self.shapes[i] for i in sorted(self.sels)])]
            cols = self.copy_columns(sorted(self.sels))  # (each colour's row: pasted the same way, like Domino)
            for i, sh in zip(sorted(self.sels), shapes):
                sh.pop("cols", None)
                if i in cols:
                    sh["cols"] = cols[i]
            self.put_clipboard(shapes)

    def put_clipboard(self, shapes, loaded=False, tempo=None):
        """shapes (copies, their "cols" set) are what Ctrl+V pastes now (also the track list's Ctrl+C; loaded: its
        boxes held a loaded MIDI's notes, which never go into a copy for other programs: said, hunt 2026-10-29).
        tempo: the Conductor's changes copied with them (the track list's boxes, track_tempo): the tempo copy too,
        pasted along with these shapes (track_list.key_paste)."""
        self.cancel_copy()  # (an older copy's text still being made: this one wins)
        for sh in shapes:
            sh["into_from"] = self.project_token  # (its tracks are this project's: put_into_current)
        self.clipboard = shapes
        self.clip_kind = "shapes"
        self.shapes_stamp = self.copy_stamp()
        self.remember_clip()  # (what the clipboard holds now is older than this copy, also while its text is made)
        if tempo is not None:
            tempo["with"], tempo["n"] = shapes, self.shapes_stamp
            self.tempo_clip = tempo
        msg = trn("app.copied_shape_s_ctrl_v_pastes", len(self.clipboard), n=len(self.clipboard))
        if tempo is not None:
            from window.track_tempo import count
            msg += trn("app.copied_tempo_too", count(tempo), n=count(tempo))
        also, domino, notes, why = [], None, None, ""
        # (what both extras copy, worked out once: a big song's Keep timing took twice as long, hunt 2026-10-29)
        got = self.copy_music() if RAW and (self.copy_domino.get() or self.copy_notes.get()) else False
        if self.copy_domino.get():  # (Settings → General: Copy to Domino's notes beside it, never a question)
            domino = self.domino_copy(quiet=True, got=got)
            if isinstance(domino, tuple):
                also.append((registered(DOMINO_FORMAT), domino[0]))
        if self.copy_notes.get():  # (... and Spiderweb Notes, for other programs: files/notes_clip.py)
            try:
                notes, why = self.notes_copy(got=got)
            except Exception as e:  # (the copy itself still goes on the clipboard)
                errors.write_log(type(e), e, e.__traceback__, "notes_copy")
                notes, why = None, "error"
            if notes:
                also.append((registered(notes_clip.NAME), notes))
            elif loaded and not why:
                why = "loaded"
        # the shared text (also on the Windows clipboard, to share: share.py) made on a thread: ~1 s at millions of
        # pasted notes (copy_wait); done quickly = finished here, as before
        from window.copy_wait import CopyJob
        job = CopyJob(self, lambda: shapes_line(shapes),
                      lambda j: self._copy_made(j, also, msg, domino, notes, why, loaded))
        if not job.quick():
            self._copy_job = job
            self.status.config(text=tr("app.copying_clip"))

    def _copy_made(self, job, also, msg, domino, notes, why, loaded):
        """put_clipboard's shared text is made: onto the Windows clipboard with the extras, unless something else
        went on it since the copy (copied in another program, Copy to Domino...: that's newer, kept)."""
        if getattr(self, "_copy_job", None) is job:
            self._copy_job = None
        if job.error is not None and not isinstance(job.error, MemoryError):
            raise job.error
        line = job.line
        if copy_count() != self.seen_clip:
            return self.status.config(text=msg)
        try:
            shared, extra = put_text_and(line, also) if line is not None else (False, False)
        except MemoryError:
            shared, extra = False, False
        self.remember_clip()
        if shared:
            if len(line) > LONG_LINE:
                msg += tr("app.too_long", chars=len(line))
        else:
            msg += tr("app.couldn_t_share")
        if isinstance(domino, tuple) and extra:
            self.own_domino_copy = self.seen_clip  # (pasted straight back: its tempo isn't asked about)
            msg += tr("app.copied_for_domino_too")
        elif domino:  # (no room on the clipboard / it would have asked or failed)
            msg += tr("app.not_copied_for_domino")
        if notes and extra:
            msg += tr("app.copied_as_notes_too") + (tr("load.domino_short") if loaded else "")
        elif notes:  # (made, but the clipboard had no room for it)
            msg += tr("app.not_copied_as_notes_room")
        elif why:  # (left out: off Windows, too far apart, out of memory, an error (errors.log), loaded notes only)
            msg += tr("app.not_copied_as_notes_" + why)
        self.status.config(text=msg)

    def cut_selected(self, whole=False):
        """Ctrl+X: what Ctrl+C and Delete work on (a funnel's highlighted curves, a picked stroke, else the selected
        shapes) copied, then deleted. whole (the menu's Cut): always the shapes."""
        if not whole and getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.cut()
        if not whole and self.roll.curve_parts():
            self.roll.copy_curve()
            return self.roll.delete_parts()
        if not whole and self.picked():
            self.roll.copy_stroke(*self.picked())
            return self.roll.delete_stroke(*self.picked())
        if self.sels:
            self.copy_selected(whole=True, cut=True)
            self.delete_selected(name=tr("app.cut"))

    def finish_copy(self):
        """A Ctrl+C's shared text still being made (copy_wait): waited for here, so it's on the clipboard (Spiderweb's
        own paste never runs beside half a copy; closing keeps it)."""
        job = getattr(self, "_copy_job", None)
        if job is not None:
            job.finish()

    def cancel_copy(self):
        """... dropped instead: something newer goes on the clipboard."""
        job = getattr(self, "_copy_job", None)
        self._copy_job = None
        if job is not None:
            job.drop()

    def copy_stamp(self):
        """A number for each copy (shapes / tempo), counting up: the track list's Ctrl+V pastes the newer."""
        self._copy_n = getattr(self, "_copy_n", 0) + 1
        return self._copy_n

    def remember_clip(self):
        """Spiderweb copied something: what the Windows clipboard holds now is older than that copy."""
        self.seen_clip = copy_count()

    def remember_own_copy(self):
        """A copy only Spiderweb keeps (a stroke, a curve's shape): with "Ctrl+C also copies…" ticked, the Windows
        clipboard is emptied if it still holds Spiderweb's last copy, so other programs don't paste those older notes
        (user's pick, 2026-10-20); something copied in another program since stays."""
        self.drop_own_copy()
        self.remember_clip()

    def drop_own_copy(self):
        """remember_own_copy's emptying alone (Hz window notes, Conductor pane copies: hunt 2026-10-29); what Ctrl+V
        pastes on the piano roll stays as it was."""
        self.cancel_copy()
        if RAW and (self.copy_domino.get() or self.copy_notes.get()) and copy_count() == self.seen_clip:
            clipboard.put_many([])  # (through the module: the tests' pretend clipboard replaces it there)

    def shared_clip(self, shapes_only=False):
        """The Windows clipboard's text if it holds a shared line put there after Spiderweb's last copy (so it's
        what was copied last; Windows' copy count tells, so the same line copied again counts too), "" if the
        clipboard is busy (another program has it open), else None. shapes_only: only if it's shapes that can
        be pasted."""
        if copy_count() == self.seen_clip:
            return None
        text = get_text()
        if text is None:
            return ""
        if not FIND.search(text):
            return None
        if shapes_only:
            try:
                got = unpack(text)
                if got["kind"] != "shapes":
                    return None
                with merges_unmade():  # (only whether it can be pasted)
                    read_shapes(got)
            except ShareError:
                return None
        return text

    def domino_newer(self):
        """True when the Windows clipboard holds notes copied in Domino, put there after Spiderweb's last copy (so
        they're what was copied last: paste takes them)."""
        return RAW and copy_count() != self.seen_clip and on_clipboard()

    def paste_shared(self, text, at=None):
        """Paste shapes shared as text (share.py), like paste; they become Spiderweb's copied shapes. Shapes left
        out, or a line made by another Spiderweb version: a heads-up after pasting (popup + status line)."""
        try:
            got = unpack(text)
            if got["kind"] == "drawing":
                messagebox.showinfo(tr("app.spiderweb_2"), tr("app.shared_drawing"), parent=self)
                return
            with merges_unmade():  # (merged shapes' notes made only once the count is OK'd: they can be millions)
                shapes, left = read_shapes(got)
        except ShareError as e:
            messagebox.showerror(tr("app.spiderweb_2"), tr("app.shared_newer") if e.why == "newer" else
                                 tr("app.shared_broken"), parent=self)
            return
        except MemoryError:
            messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=self)
            return
        if not big_ask.ask_shapes(self, len(shapes)):
            return
        top = self.last_step()
        try:
            if not self.confirm_big(shapes):
                return
            if any("merge" in sh for sh in shapes):
                try:
                    shapes, left = read_shapes(got)
                except ShareError:
                    messagebox.showerror(tr("app.spiderweb_2"), tr("app.shared_broken"), parent=self)
                    return
            clip = self.clipboard, self.clip_kind, self.seen_clip
            self.remember_clip()
            self.clipboard, self.clip_kind = shapes, "shapes"
            self.shapes_stamp = self.copy_stamp()  # (newer than a tempo copy: the track list's Ctrl+V)
            clamped = self.paste(whole=True, at=at)
        except Exception as e:  # (out of memory, or anything else: the shapes never stay half pasted, user)
            if self.last_step() is not top:  # (the paste's step: taken back, not kept for Ctrl+Y)
                kept = self._redo_kept
                self._restore(self.undo_stack, self.redo_stack)
                self.redo_stack.pop()
                if kept:
                    self.redo_stack[:] = kept[0]
                self.sync_history()
            if self.clipboard is shapes:  # (Ctrl+V again tries the line again, not an older copy)
                self.clipboard, self.clip_kind, self.seen_clip = clip
            if not isinstance(e, MemoryError):
                raise
            messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=self)
            return
        made = made_by(got)
        notes = ([trn("app.shared_left_out", left, n=left)] if left else []) + (
            [tr(f"share.made_{made[0]}", version=made[1])] if made else [])
        msg = trn("app.pasted_shared", len(shapes), n=len(shapes))
        if left:
            msg += tr("app.shared_left_out_short", n=left)
        if made:
            msg += tr("app.shared_made_short", version=made[1])
        if clamped:
            msg += " " + trn("app.pasted_15", clamped, n=clamped)
        self.status.config(text=msg)
        if notes:
            messagebox.showwarning(tr("app.spiderweb_2"), "\n\n".join(notes), parent=self)

    def paste(self, whole=False, at=None):
        """Paste the copied shapes so they start at the play line (snapped to the grid), or at beat `at` (a Select
        double-click: the mouse, snapped already). With curves highlighted and a curve copied: its shape onto them.
        Shapes shared as text or notes copied in Domino, copied after Spiderweb's last copy, come first
        (paste_shared, paste_from_domino). The Tempo pane clicked last: its copied tempo at the play line."""
        self.finish_copy()
        if not whole and at is None and getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.paste()
        if getattr(getattr(self, "stir_window", None), "drawing", None):  # (a Stir area being drawn: only new
            # strokes make it, a paste would be left behind, hunt C 3)
            self.status.config(text=tr("stir.draw_no_paste"))
            return
        text = self.shared_clip()
        if text == "":  # (busy: what's on it can't be told, so nothing older is pasted in its place)
            self.status.config(text=tr("app.clipboard_busy"))
            return
        if text is not None:
            return self.paste_shared(text, at)
        if self.domino_newer():
            return self.paste_from_domino(at)
        if not whole and self.roll.curve_parts() and self.roll.curve_clip:
            return self.roll.paste_curve()
        if not whole and self.clip_kind == "stroke":
            return self.roll.paste_stroke()
        if not self.clipboard:
            return
        start = min(b for sh in self.clipboard for b, _ in cached_path(sh))
        if at is None:
            at, sb = self.playhead, self.snap_beats()
            if sb:
                at = round(at / sb) * sb
        return self.add_copies(self.clipboard, at - start)

    def flip(self, sideways, whole=False):
        """Mirror the selected shapes as a group: sideways (time) or upside down (pitch).
        With a funnel's curves highlighted: those curves (end to end / inside out)."""
        if not whole and self.roll.curve_parts():
            return self.roll.set_curves(turned_curve if sideways else inside_out)
        if not whole and self.picked():
            return self.roll.flip_stroke(*self.picked(), sideways)
        if not self.sels:
            return
        shapes = [self.shapes[i] for i in sorted(self.sels)]
        k = 0 if sideways else 1
        vals = [pt[k] for sh in shapes for pt in cached_path(sh)]
        mid2 = min(vals) + max(vals)  # twice the middle
        even = self.notes_middle(shapes, sideways, mid2)
        self.push_undo(name=tr("app.flip_sideways") if sideways else tr("app.flip_upside_down"))
        sizes = push_sizes(self.shapes, whole_groups(self.shapes, self.sels))  # (Add between pushes flip too)
        for sh in shapes:
            steps = steps_kept(sh)  # (a piece of a shape with pages: a flip step too, it stays a piece)
            frm, was = sh["pts"], sh.get("fx")
            fx = fx_flipped(was, "time" if sideways else "keys", steps) if was or steps else was
            # (the true middle for a shape given a flip step: its pages' notes come from its drawing flipped back in
            # its own box (engine.unflipped / turned_back), that gives back the drawing it had; and for a text:
            # rebuilt, it slides onto its anchor by whole ticks, a box moved part of a tick came back a tick off)
            m2 = mid2 if fx is not was or sh.get("text") else even
            if sh.get("merge"):  # (its parts' velocities: each sideways flip turns them round, merge.py)
                sh["merge"]["vrev"] = merge_vrev(sh) != sideways
            if sh.get("unmerge") and sideways:  # (kept as separate shapes: the shape Split gives back, the same)
                sh["unmerge"]["vrev"] = not sh["unmerge"].get("vrev")
            flip_shape(sh, sideways, m2)
            turned_piece(sh, frm, lambda w: flip_shape(w, sideways, m2))  # (a funnel piece stays one)
            if fx is was and fx:  # (no flip step: flip_shape's own steps kept, a merged shape's velocity mirrored)
                pass
            elif fx:  # (its note tool pages' result is flipped too, fx.py)
                sh["fx"] = fx
            else:
                sh.pop("fx", None)
        keep_pushes(self.shapes, sizes, lambda v: (-v[0], v[1]) if sideways else (v[0], -v[1]))
        self.roll.move_kept_box(lambda b, p: (even - b, p) if sideways else (b, even - p))  # (flips too)
        self.sync_panel()
        self.shapes_changed()

    def notes_middle(self, shapes, sideways, mid2):
        """Twice the middle a flip mirrors shapes around: their drawings' own notes' middle (first start + last end,
        lowest + highest key, as fx.notes_box), so each column / row lands on its partner and the flipped notes are
        the mirror note for note (user 2026-10-18: the drawing's middle between two key rows gave a different
        heart). mid2 (the drawing's) when none has notes of its own (Hz bass, pictures), or with Ripples growing
        free (rings cut off at the piano roll's edges move the notes' middle: flipping twice drifted)."""
        if any(grows_free(sh) for sh in shapes):
            return mid2
        got = [self.whole_notes({k: v for k, v in sh.items()
                                 if k not in ("fx", "_m", "sustain", "range", "subtract", "extend")})
               for sh in shapes if not sh.get("hz") and "picture" not in sh]
        got = [n for n in got if len(n)]
        if not got:
            return mid2
        a = np.concatenate(got)
        if sideways:
            return (int(a[:, 0].min()) + int(a[:, 1].max())) / self.ppq
        return int(a[:, 2].min()) + int(a[:, 2].max())

    def whole_notes(self, sh):
        """sh's notes, a shape past the edges (engine.past_edges) WHOLE: the parts before beat 0 / past the keys
        kept, not cut off (a flip mirrors round the whole shape's middle and stays in place; user 2026-11-06: it
        jumped right, onto the notes left after the cut)."""
        out = past_edges(sh, self.keys, self.ppq)
        if out is None:
            return self.notes_of(sh)[:, :3]
        lifted, up, room, later = out
        with rows_up_to(room - 1):
            n = np.array(self.notes_tracks(lifted, room)[0][:, :3], copy=True)
        n[:, 2] -= up
        n[:, :2] -= round(later * self.ppq)
        return n

    def keeps_offrange(self):
        """Settings "Keep notes made there" on AND some space to keep them in (user 2026-11-08: the tick is greyed
        without Space past the keys / before the start, so then it counts as off)."""
        return bool(self.keep_offrange.get() and (self.space_keys.get() > 0 or self.space_bars.get() > 0))

    def offrange_tracks(self, sh):
        """(notes, tracks) sh makes in the space past the keys / before beat 0 (engine.off_part) while "Keep notes
        made there" is on; empty otherwise. KEPT APART from notes_tracks: the real notes (export, playback, Copy to
        Domino, note count...) never hold them. Made from the same remembered lifted notes as edge_made's, so this
        costs no note making of its own. Nothing to save: they come from the shape's drawing again."""
        empty = np.zeros((0, 4), np.int64), None
        if not self.keeps_offrange() or sh.get("hz") or silent(sh):  # (a mask whose own notes don't sound: none
            return empty                                                  # there either, like all_notes)
        out = past_edges(sh, self.keys, self.ppq)
        if out is None:
            return empty
        lifted, up, room, later = out
        with rows_up_to(room - 1):
            notes, tracks = self.notes_tracks(lifted, room)
        if not len(notes):
            return empty
        return off_part(notes, tracks, up, self.keys, round(later * self.ppq))

    def kept_notes(self, sh):
        """sh's (start, end, key, velocity) notes for the tools: with "Keep notes made there" on, its notes past the
        keys / before beat 0 too (the whole shape, a note across beat 0 whole: engine.whole_notes_tracks' way, from
        the remembered notes); else its notes as made (notes_of)."""
        if not self.keeps_offrange() or sh.get("hz"):
            return self.notes_of(sh)
        out = past_edges(sh, self.keys, self.ppq)
        if out is None:
            return self.notes_of(sh)
        lifted, up, room, later = out
        with rows_up_to(room - 1):
            n = np.array(self.notes_tracks(lifted, room)[0][:, :4], np.int64, copy=True)
        n[:, 2] -= up
        n[:, :2] -= round(later * self.ppq)
        return n

    def offrange_changed(self):
        """Settings → Piano roll "Keep notes made there" / its Opacity / the Space settings changed: the kept notes
        worked out again and painted at the new opacity (no undo step: an app setting)."""
        from roll.roll_draw import OFF_FADE
        OFF_FADE[0] = min(100, max(1, int(self.offrange_opacity.get())))
        self.off_all = self.offrange_rows()
        self.made_version += 1
        self.off_masked = self.masked_offrange() if self.mask_preview.get() else None
        self._outgoing = None
        self.track_view()
        self.roll.request_redraw()
        self.update_status()  # (the red "+n off-range" count)
        self.play_changed()  # (Wrap them around: heard from now on, or no longer)

    def wraps_offrange(self):
        """Settings → Piano roll "Wrap them around in exports and Domino copies" on (and Keep notes working)."""
        return self.keeps_offrange() and bool(self.wrap_offrange.get())

    def outgoing(self):
        """The notes that leave Spiderweb (Export MIDI / to Domino, Copy to Domino, Spiderweb Notes, playback): the
        rendered notes, + with "Wrap them around" on the kept notes past the keys put back inside (all_notes wrap=:
        key mod the project's keys, before beat 0 never; overlaps / channels worked out with them like any other
        note in that mode). Rows like rendered's. Made only when asked (and kept until the notes change), so the
        piano roll and the tools never pay for it; never part of app.rendered (the tools would see them twice)."""
        return self.outgoing_layout()[0]

    def outgoing_layout(self):
        """(App.outgoing's notes, their voice_slots {shape: {track: slot}}): the export's own channel layout, so the
        Hz basses' CC lines and pasted notes' CCs go on the channels their notes are WRITTEN on (hunt 2026-11-08:
        wrapped notes moved a Hz bass to another channel, its Cutoff stayed on the old one). Wrap off / nothing
        wrapped = (rendered, voice_slots) as always."""
        if not self.wraps_offrange() or not len(self.off_all):
            return self.rendered, self.voice_slots
        key = (self.made_version, id(self.rendered), id(self.off_all), self.ppq, self.keys, self.channel_mode.get())
        if self._outgoing is not None and self._outgoing[0] == key:
            return self._outgoing[1]
        mode = self.channel_mode.get()
        got = all_notes(self.shapes, self.ppq, self.keys, mode, self.channel_split, self.all16(), self.notes_tracks,
                        rounds=self.rounds_apart(), order=self.in_order(), keep=self.keep_colours.get(),
                        busy=self.cc_busy(mode), own=self.own_channels(), overlaps=self.overlap_rule(), wrap=True)
        self._outgoing = (key, (got[2], got[4]))
        return self._outgoing[1]

    def wrapped_rows(self):
        """Where the kept notes past the keys LAND with "Wrap them around" on, for the piano roll ("Show wrapped
        notes", user 2026-11-08): (rows, boxes, clash). rows = off_drawn's rows put where they land by wrap_landing
        (the export's own rule: shown = written), only the selected shapes' in "Selected shapes", every shown one in
        "Always", none when "Off". boxes = [(owner, t0, t1, k0, k1)] each landing area (a shape's notes from over the
        top land at the bottom, from under key 0 at the top: one box each), only in "Selected shapes" (the dashed
        outline). clash = (start, end, key) stretches where a landing note sits on a real note on the same key (the
        export will see them overlap): drawn red. Kept until something it reads changes."""
        none = (NO_NOTES, [], np.zeros((0, 3), np.int64))
        mode = self.show_wrapped.get()
        off = self.off_drawn
        if mode == "off" or not len(off) or not self.wraps_offrange():
            if self._wrapped is not None:
                self._wrapped = None
                self.wrap_version += 1
            return none
        sels = frozenset(self.sels) if mode == "selected" else None
        key = (self.made_version, self.off_version, id(off), id(self.rendered), sels, mode, self.keys)
        if self._wrapped is not None and self._wrapped[0] == key:
            return self._wrapped[1]
        if sels is not None:
            off = off[np.isin(off[:, 5], list(sels))] if sels else off[:0]
        rows, ok = wrap_landing(off, self.keys)
        boxes = []
        if mode == "selected" and len(rows):
            below = off[ok, 2] < 0  # (from under key 0: lands at the top)
            for owner in np.unique(rows[:, 5]).tolist():
                mine = rows[:, 5] == owner
                for side in (below, ~below):
                    r = rows[mine & side]
                    if len(r):
                        boxes.append((owner, int(r[:, 0].min()), int(r[:, 1].max()), int(r[:, 2].min()),
                                      int(r[:, 2].max())))
        got = rows, boxes, landing_clashes(rows, self.rendered)
        self._wrapped = (key, got)
        self.wrap_version += 1
        return got

    def export_view(self, on):
        """W held on the piano roll (user 2026-11-08): the piano roll shows exactly what the export gets: drawn =
        App.outgoing (wrapped notes solid where they land, the track list's one-track view applied like track_view),
        the kept notes past the keys, their landing copies, red marks and dashed boxes hidden (roll_draw.note_rects).
        Only with Keep notes + Wrap on; off = back to the normal view."""
        on = bool(on) and self.wraps_offrange()
        if on == self.export_held:
            return
        self.export_held = on
        self.track_view()
        self.roll.request_redraw()

    def export_drawn(self):
        """track_view's last step while W is held: drawn from the export's notes instead of the piano roll's."""
        out = self.outgoing()
        self.drawn, self.behind_owner = self.track_list.filtered(out, track_ids(out[:, 4]), len(self.shapes))
        self.picture_owners = self.picture_owners_all if self.behind_owner is None else np.append(
            self.picture_owners_all, False)

    def scrub_wrapped(self):
        """Right-drag listening's wrapped notes (user 2026-11-08: heard whenever "Wrap them around" is on, like Play,
        whatever "Show wrapped notes" says): every shown kept note (off_drawn: hidden shapes / tracks never) put where
        it lands by wrap_landing, the export's own rule. Rows like rendered's; kept until off_drawn / keys change, so
        a mouse move only picks rows."""
        off = self.off_drawn
        if not len(off) or not self.wraps_offrange():
            return NO_NOTES
        key = (self.made_version, self.off_version, id(off), self.keys)
        got = getattr(self, "_scrub_wrap", None)
        if got is None or got[0] != key:
            got = self._scrub_wrap = (key, wrap_landing(off, self.keys)[0])
        return got[1]

    def offrange_rows(self):
        """Every shape's "Keep notes made there" notes as rendered rows (start, end, key, velocity, slot, owner): the
        piano roll paints them see-through (roll_draw OFF_BASE). Each colour on the slot its real notes are on
        (colour_slots), so they look like the rest of the shape; a shape with no real notes: its colour number
        (Multi) or slot 0. Never part of rendered / drawn: nothing that leaves Spiderweb sees them."""
        if not self.keeps_offrange() or self.companion is not None or not self.shapes:
            return NO_NOTES
        rendered, parts = self.rendered, []
        owners = rendered[:, 5]
        order = np.argsort(owners, kind="stable") if len(rendered) else None

        def heard(i):  # (shape i's real notes as rendered, or None)
            if order is None:
                return None
            lo, hi = np.searchsorted(owners, i, sorter=order), np.searchsorted(owners, i, "right", sorter=order)
            return rendered[order[lo:hi]] if hi > lo else None
        kin = {}  # (pieces of one paste / one cut shape: a piece wholly out there takes its colours from one heard)
        for j, s in enumerate(self.shapes):
            for k in (("paste", s.get("paste")), ("cut", cut_group(s))):
                if k[1] is not None and k not in kin and heard(j) is not None:
                    kin[k] = j
        for i, sh in enumerate(self.shapes):
            notes, tracks = self.offrange_tracks(sh)
            if not len(notes):
                continue
            notes = np.asarray(notes, np.int64).reshape(-1, 4)
            t = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64).ravel()
            slot = t if self.channel_mode.get() == "auto" else np.zeros(len(notes), np.int64)
            src = i if heard(i) is not None else next(
                (kin[k] for k in (("paste", sh.get("paste")), ("cut", cut_group(sh))) if k[1] is not None and k in kin),
                None)
            if src is not None:
                mine, other = heard(src), self.shapes[src]
                got = self.colour_slots(other, mine)
                if len(got) == 1:
                    slot = np.full(len(notes), got[0], np.int64)
                else:
                    real = self.notes_tracks(dict(other, _rounds=True) if self.rounds_apart() and has_colours(other)
                                             else other)[1]
                    u = np.unique(np.asarray(real, np.int64)) if real is not None else np.zeros(1, np.int64)
                    at = np.clip(np.searchsorted(u, t), 0, len(u) - 1)
                    found = (u[at] == t) & (at < len(got))
                    slot = np.where(found, np.asarray(got, np.int64)[np.minimum(at, len(got) - 1)],
                                    int(np.bincount(mine[:, 4] - mine[:, 4].min()).argmax() + mine[:, 4].min()))
            parts.append(np.column_stack([notes, slot, np.full(len(notes), i, np.int64)]))
        return np.concatenate(parts) if parts else NO_NOTES

    def kept_mask_world(self, pattern=None):
        """The masks worked out over the kept notes past the keys (hunt 2026-11-08, user: masks work on them like on
        any note): the masks' areas only reach key 0 .. the top key, so everything is lifted by `up` whole keys onto
        a taller keyboard (nkeys): (shapes with every drawn mask lifted, notes = the real rows of the shapes with kept
        notes + the kept rows, lifted, a 7th column 1 on the kept ones, nkeys, up, notes_of for those shapes: a lifted
        mask's notes made WHOLE there (engine.whole_notes_tracks), others' as made, lifted). None: no mask or no kept
        note. pattern: the rows the masks cover (App.pattern's; None = rendered)."""
        off = self.off_all
        if not len(off) or not any(map(is_mask, self.shapes)):
            return None
        from notes.engine import whole_notes_tracks
        up = self.keys * max(1, int(self.space_keys.get()))
        nkeys = self.keys + 2 * up
        shapes = [dict(sh, pts=[[b, p + up] for b, p in sh["pts"]], _up=up) if is_mask(sh) and "pts" in sh else sh
                  for sh in self.shapes]
        real = self.rendered if pattern is None else pattern
        real = real[np.isin(real[:, 5], np.unique(off[:, 5]))]
        notes = np.concatenate([np.column_stack([real, np.zeros(len(real), np.int64)]),
                                np.column_stack([off, np.ones(len(off), np.int64)])])
        notes[:, 2] += up
        ppq = self.ppq

        def notes_of(s):
            if "_up" in s:  # (fills make their rows that high too: paths.rows_up_to, as past_edges' shapes)
                with rows_up_to(nkeys - 1):
                    return whole_notes_tracks({k: v for k, v in s.items() if k != "_up"}, ppq, nkeys,
                                              over_top=True)[0]
            n = np.array(self.notes_of(s), np.int64, copy=True).reshape(-1, 4)
            n[:, 2] += up
            return n
        return shapes, notes, nkeys, up, notes_of

    def masked_offrange(self):
        """off_all as the masks' preview shows it (mask.mask_view over kept_mask_world): the kept rows of shapes a
        mask covers as ghosts (velocity -1, painted faint) + the parts that show; None = no mask over kept notes."""
        world = self.kept_mask_world()
        if world is None:
            return None
        import notes.mask as mask_mod
        shapes, notes, nkeys, up, notes_of = world
        saved = dict(mask_mod._shown)  # (mask_view's own memory is the real notes' one: Apply mask reuses it)
        try:
            rows, covered = mask_view(notes, shapes, self.ppq, nkeys, notes_of, None, GHOST_FADE[0] > 0)
        finally:
            mask_mod._shown.clear()
            mask_mod._shown.update(saved)
        if not covered:
            return None
        rows = rows[rows[:, 6] == 1][:, :6].copy()
        rows[:, 2] -= up
        return rows

    def masked_kept_shown(self):
        """For Apply mask: {first mask of a unit: the kept rows (rendered's columns) that unit shows}, worked out in
        kept_mask_world like the preview (mask.worked_now)."""
        from notes.mask import unmasked, worked_now
        world = self.kept_mask_world(self.pattern)
        if world is None:
            return {}
        shapes, notes, nkeys, up, notes_of = world
        units, shown = worked_now(shapes, unmasked(notes, shapes), self.ppq, nkeys, notes_of)
        out = {}
        for unit in units:
            got = shown(unit)[1]
            got = got[got[:, 6] == 1][:, :6].copy() if len(got) else got[:, :6]
            if len(got):
                got[:, 2] -= up
                out[tuple(unit)] = got
        return out

    def gate_flip_state(self):
        """The right-click "Gate aware flip" for the selected shapes: (shapes it switches, on: ticked, why: None or
        why it's greyed), or None when no selected shape takes it (Hz bass, funnels, images: gateflip.offered)."""
        sel = [self.shapes[i] for i in sorted(self.sels) if i < len(self.shapes) and gate_offered(self.shapes[i])]
        if not sel:
            return None
        on = [sh for sh in sel if all_flipped(sh)]
        if on:  # (switching off always works, even where it does nothing now)
            return on, True, None
        kept = [sh for sh in sel if sh.get("gate_flip")]
        if kept:  # (flipped, then Fill / Empty picked: it does nothing now, but can still be switched off)
            return kept, True, None
        shapes = [sh for sh in sel if not sh.get("gate_flip")]
        why = [gate_why_not(sh, *self.notes_tracks(sh)) for sh in shapes]
        ok = [sh for sh, w in zip(shapes, why) if w is None]
        if ok:
            return ok, False, None
        return [], False, next((w for w in ("one_gate", "no_gates") if w in why), "no_gates")

    def gate_flip(self):
        """Right-click "Gate aware flip": the selected shapes' gates laid from the other side, or back (gateflip.py).
        Their velocities turn round with it, like a flip."""
        got = self.gate_flip_state()
        if not got or not got[0]:
            return
        shapes, on, _ = got
        self.push_undo(name=tr("app.gate_flip"))
        for sh in shapes:
            if on:
                del sh["gate_flip"]
            else:
                sh["gate_flip"] = True
            turn_velocities(sh)
        self.sync_panel()
        self.shapes_changed()
        if not on:
            self.tips.show("gate_flip", wait=True)  # (the first time: what it does)

    def rotate(self, clockwise, whole=False):
        """Turn the selected shapes 90 degrees as a group, around their middle, as they look on screen
        (so the current zoom decides how many beats one key becomes). A stroke picked: just it."""
        if not whole and self.picked():
            return self.roll.turn_stroke(*self.picked(), clockwise)
        if not self.sels or self.roll.sx is None:
            return
        shapes = [self.shapes[i] for i in sorted(self.sels)]
        path = [pt for sh in shapes for pt in cached_path(sh)]
        bs, ps = [b for b, _ in path], [p for _, p in path]
        cb, cp = (min(bs) + max(bs)) / 2, (min(ps) + max(ps)) / 2
        r = self.roll.sy / self.roll.sx  # beats per key on screen
        sign = 1 if clockwise else -1
        limit = self.roll.limits(shapes)  # (turned past an edge: pushed back inside, user)
        self.push_undo(name=tr("app.turn_90"))
        sizes = push_sizes(self.shapes, whole_groups(self.shapes, self.sels))  # (Add between pushes turn too)
        frms = [sh["pts"] for sh in shapes]
        for sh in shapes:
            steps = steps_kept(sh)  # (a piece of a shape with pages: a turn step too, it stays a piece)
            if sh.get("merge"):  # (a turn leaves its parts' velocities as they are)
                sh["merge"]["vrev"] = merge_vrev(sh)
            turn_shape(sh, clockwise, r, cb, cp)
            if sh.get("fx") or steps:  # (its note tool pages' result is turned too, fx.py)
                fx = fx_turned(sh.get("fx"), 90 if clockwise else -90, r, steps)
                if fx:
                    sh["fx"] = fx
                else:
                    sh.pop("fx", None)
        keep_pushes(self.shapes, sizes, lambda v: (sign * v[1] * r, -sign * v[0] / r))
        db, dp = self.roll.push_in(self.roll.reach(shapes), limit)
        if db or dp:
            for sh in shapes:
                sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]

        def turned(w):
            turn_shape(w, clockwise, r, cb, cp)
            w["pts"] = [[b + db, p + dp] for b, p in w["pts"]]
        for sh, frm in zip(shapes, frms):  # (a funnel piece stays one)
            turned_piece(sh, frm, turned)
        self.roll.move_kept_box(lambda b, p: (cb + sign * (p - cp) * r + db, cp - sign * (b - cb) / r + dp),
                                turned=True)  # (too)
        self.shapes_changed()  # (first: notes made in the background, the panel's Recolour row waits for them)
        self.sync_panel()

    @staticmethod
    def stretched(sh, ab, kx, ap, ky):
        """A copy of shape sh stretched kx times sideways from beat ab and ky times up / down from pitch ap (a
        Select box's side dragged), still looking the same stretched: arcs / freehand as round, its tumours,
        formulas and text along. Not a Hz bass's notes (user, like its box's corners)."""
        new = copy.deepcopy(sh)
        fn = lambda b, p: (ab + (b - ab) * kx, ap + (p - ap) * ky)
        new["pts"] = [[round(v, 9) + 0.0 for v in fn(b, p)] for b, p in sh["pts"]]  # (no float noise: roll_custom.tidy)
        r = abs(kx / ky)  # (k = beats per key where it looks round: see rotate)
        if new["kind"] == "arc" or new["kind"] == "free" and "k" in new:
            new["k"] = new.get("k", 1.0) * r
        if new.get("text"):
            new["text"]["k"] = new["text"].get("k", 1.0) * r
        for tm in all_tumours(new):  # (size in keys, the rest in beats)
            tm["k"] *= r
            tm["size"] *= abs(ky)
            tm["length"] *= abs(kx)
            tm["dist"] *= abs(kx)
            tm["ease"] = tm.get("ease", 0.0) * abs(kx)
        moved_formulas(new, fn)
        return new  # (a Hz bass's notes keep their lengths: its box only limits where they sound, user)

    def shape_label(self, sh):
        label = self.plain_label(sh)
        return tr("mask.label", label=label) if is_mask(sh) else label

    def plain_label(self, sh):
        made = sh.get("made") if "notes" in sh else None  # (named by what it is: the same in every language)
        if made == "mask":
            return tr("mask.applied")
        if made == "leftovers":
            return tr("join_split.merge_leftovers")
        if made in ("path", "plain"):  # (Copy along a path → Turn into plain shapes: "Line copy 2", Turn into plain
            # notes: "Custom: Circle (notes)", named when made)
            return sh.get("name") or "?"
        if ("picture" in sh or sh.get("merge") or sh.get("unmerge") or  # (the picture's file name, user; "Merged notes")
                "notes" in sh and sh.get("name") in (tr("join_split.merge_leftovers"),  # (plain notes, user; made
                                                     tr("mask.applied"))):  # before "made")
            return sh.get("name") or "?"
        if sh.get("extract"):  # (where it was taken from, user 2026-10-26: several Extracts all said "Pasted notes")
            from notes.loaded import extract_groups
            boxes = [b for g in extract_groups(sh["extract"]) for b in g["boxes"]]
            if boxes:
                sigs, ppq = sigs_of(self.tempo), self.ppq
                a = where(sigs, min(b[0] for b in boxes) / ppq)[0] + 1
                z = where(sigs, max(min(b[0] for b in boxes), max(b[1] for b in boxes) - 1) / ppq)[0] + 1
                return tr("app.extracted_bar", bar=a) if a == z else tr("app.extracted_bars", first=a, last=z)
        if "notes" in sh:
            return tr("app.pasted_notes")
        if sh.get("text"):
            return tr("app.text_2", value=sh.get('name') or '?')
        if is_joined(sh):
            pieces = len(sh.get("gaps", [])) + 1
            return (tr("app.joined_pieces", curve=KINDS['curve'], pieces=pieces) if pieces > 1 else
                    tr("app.joined", curve=KINDS['curve']))
        return tr("app.custom", value=sh.get('name') or '?') if sh["kind"] == "custom" else KINDS[sh["kind"]]

    def note_count(self, sh, ppq=None):
        """Notes a shape makes (quick for spam, which can be millions). ppq: another than the project's."""
        ppq = ppq or self.ppq
        n = (None if sh.get("merge") else  # (turned / slanted: made again from its shapes, merge.py)
             custom_note_count(sh, ppq) if sh["kind"] == "custom" else
             funnel_note_count(sh, ppq) if sh["kind"] == "funnel" else None)
        if n is None and sh.get("by_snap") and sh["kind"] in LINE_KINDS:
            # Notes by snap: at most the line's own notes + one per step (never made just to count: a 1-tick step on a
            # long line is millions, hunt 2026-10-28)
            plain = {k: v for k, v in sh.items() if k not in ("by_snap", "by_snap_name", "by_snap_end")}
            ts = np.concatenate(cached_arrays(sh))[:, 0] * ppq
            return len(self.notes_of(plain)) + int((ts.max() - ts.min()) / max(1.0, sh["by_snap"] * ppq)) + 2
        return len(self.notes_of(sh)) if n is None else n

    def notes_about(self, sh, ppq=None):
        """note_count, but a merged shape = what its parts make (quick, never made for it: its own may not be made
        yet, engine.merges_unmade; a little more than its own when rows were left over), for the questions."""
        if sh.get("merge"):
            return sum(self.notes_about(p, ppq) for p in sh["merge"]["parts"])
        return self.note_count(sh, ppq)

    def confirm_big(self, shapes):
        return self.ask_notes(sum(self.notes_about(sh) for sh in shapes))

    def ask_notes(self, n):
        """True = go ahead making n notes (asked past big_ask.BIG)."""
        return big_ask.ask(self, "notes", n)

    def layout_rows(self):
        """Show the panel's optional parts, always in the same order above the point boxes."""
        rows = ((self.last_row, "last"), (self.line_fill_row, "line_fill"), (self.free_box, "free"),
                (self.gliss_box, "gliss"), (self.tumour_box, "tumour"), (self.pattern_box, "pattern"),
                (self.text_box, "text"), (self.custom_box, "custom"), (self.funnel_box, "funnel"))
        shown = [key for _, key in rows if self._rows[key]]
        if shown == getattr(self, "_rows_shown", None):
            return  # unpacking and packing them again anyway makes the panel flash
        self._rows_shown = shown
        for w, _ in rows:
            w.pack_forget()
        for w, key in rows:
            if self._rows[key]:
                w.pack(fill="x", pady=(4, 0), before=self.points_box)

    def start_companion(self):
        """Draw on Domino: Spiderweb's tools right on Domino's piano roll (window/domino_companion.py)."""
        from window.domino_companion import start
        start(self)

    def snap_beats(self):
        return snap_beats(self.snap.get(), self.beats)

    def snap_ticks(self):
        """The snap step in ticks (one tick with snapping off)."""
        sb = self.snap_beats()
        return max(1, round(sb * self.ppq)) if sb else 1

    def tool_hotkey(self, key):
        for tool, _, hot in TOOLS + DRAW_TOOLS:
            if key == hot:
                self.tool.set(tool)

    def toggle_select_tool(self):
        """Double right-click: Select <-> the drawing tool used last."""
        self.tool.set(self.draw_tool if self.tool.get() == "select" else "select")

    def on_tool_change(self):
        if self.tool.get() not in ("select", "slice", "lasso"):  # (Slice / Lasso aren't ones to go back to, user)
            self.draw_tool = self.tool.get()
        if self.tool.get() != "lasso":
            self.roll.drop_lasso()  # (a loop waiting for Enter goes with the tool)
        if self.tool.get() != "hz":
            self.hz_start = None  # (a spot waiting for its first note: the Hz bass window stops offering it)
        if self.hz_window:
            self.hz_window.sync()
        self.roll.cancel_draft()
        self.roll.set_cursor(self.roll.custom_cursor(None))  # (the tool's own pointer; remembered: else the next
        #                                                       pointer it had was skipped)
        self.tool_picker.tool_changed()
        self.sync_freehand()
        self.sync_gliss()
        self.sync_lasso()
        self.sync_text()
        self.sync_custom()
        self.sync_funnel()
        if self.tool.get() == "picture":
            self.after_idle(self.open_image)
        self.tips.show(TOOL_TOPICS.get(self.tool.get()))

    def remake_merges(self):
        """Merged shapes whose parts' settings were changed in the panel (custom_targets), or made at another PPQ /
        key range than the project's now: made again (merge.refit). A recipe seen for the first time (new, loaded,
        undone) is taken as it is."""
        from notes.merge import refit, settings_key
        seen = {}
        for sh in self.shapes:
            m = sh.get("merge")
            if not m:
                continue
            key = settings_key(m)
            if (m["ppq"], m["keys"]) != (self.ppq, self.keys):  # (its gates in whole ticks of the project's PPQ)
                m["ppq"], m["keys"] = self.ppq, self.keys
                refit(sh)
            elif self._merge_keys.get(id(m), key) != key:
                refit(sh)
            seen[id(m)] = key
        self._merge_keys = seen

    def note_tool_sels(self):
        """The selected shapes that take note tools (claw, strum, chop, glue): not pictures (user)."""
        return {i for i in self.sels if i < len(self.shapes) and "picture" not in self.shapes[i]}

    def tool_topic(self):
        return TOOL_TOPICS.get(self.tool.get(), "select")

    def open_help(self, topic_id=None):
        open_help(self, topic_id or self.tool_topic())

    def show_position(self, text):
        if text:
            self._position = text
        self.update_status()

    def show_offrange(self):
        """The red "(+1,447 off-range)" after the status line's note count: notes kept past the keys / before beat 0
        (App.off_all; the note count leaves them out, user 2026-11-08). "" while there are none or the setting is off
        (off_all is empty then). Its tip says whether Wrap them around takes them out."""
        n = len(self.off_all)
        self.offrange_text = tr("app.offrange_count", n=n) if n else ""
        self.offrange_tip.set_text(tr("app.offrange_tip_wrap" if self.wraps_offrange() else "app.offrange_tip"))
        return self.offrange_text

    def update_status(self):
        off = self.show_offrange()
        parts = [self._position] if self._position else []
        if self._bg_notes:  # (the notes are being made in the background: shapes_changed)
            self.status.set_red(self.offrange_label, "")
            self.status.show("     ".join(parts + [tr("app.making_notes")]))
            return
        parts.append(tr("app.shapes_notes", shapes=trn("n.shapes", len(self.shapes), n=len(self.shapes)),
                        notes=trn("n.notes", len(self.rendered), n=len(self.rendered))) + (" " + off if off else ""))
        self.status.red_piece = off  # (drawn red over the line: StatusLine.set_red, placed by show below)
        self.status.red = self.offrange_label
        if self.slot_count:  # (every mode: pastes and your tracks make several in Single too, hunt 2026-11-01)
            if self.pad_tracks():
                tracks = self.slot_count
            else:  # (counted once per notes made: the status line changes with every mouse move)
                if self._used_tracks[0] is not self.rendered:
                    self._used_tracks = (self.rendered, len(track_slots(self.rendered, False)))
                tracks = self._used_tracks[1]
            tracks += len(self.own_written()) + len(self.waiting_rows())
            # (folders: tracks holding several channels, PLAN #11; user 2026-10-28: "one channel each" misled)
            tl = getattr(self, "track_list", None)
            mixed = sum(len(c) > 1 for c in tl.row_chans()) if tl is not None else 0
            parts.append(trn("app.tracks_mixed", mixed, slot_count=tracks, mixed=mixed) if mixed else
                         tr("app.one_track") if tracks == 1 else
                         tr("app.tracks_one_channel_each", slot_count=tracks))
        if self.sels:
            shapes = tr("app.shapes_2", n=len(self.sels)) if len(self.sels) > 1 else ""
            n = sum(self.note_counts[i] for i in self.sels if i < len(self.note_counts))
            parts.append(tr("app.selected_notes", shapes=shapes, notes=trn("n.notes", n, n=n)))
        self.status.show("     ".join(parts))

    def in_drawer(self, e):
        """Keys pressed in the drawer window are the drawer's, not the piano roll's."""
        return bool(self.drawer) and str(e.widget).startswith(str(self.drawer))

    def hotkey(self, fn, main_only=False, while_held=False):
        """A window-wide shortcut that leaves typing boxes alone. main_only: only while the main window has the
        keyboard (not in a pop-up). while_held: it works while the mouse holds a shape on the piano roll too (the
        others do nothing then, user: a flip was undone by the next mouse move, a join broken)."""
        def handler(e):
            w = e.widget
            if self.in_drawer(e):
                return None
            if self.hz_window and str(w).startswith(str(self.hz_window)):
                return None  # (the Hz bass window: its own keys only, nothing done to the main piano roll behind)
            if self.image_window and str(w).startswith(str(self.image_window)):
                return None  # (the image window and its pop-ups: Ctrl+C there copies their text, not the shapes)
            if self.app_only(w):
                return None  # (Settings, Help, the update popup: the shapes behind stay as they are)
            if self.grabbed_elsewhere():
                return None  # (a window like Range… is working on the picked shapes: nothing changes behind it)
            if main_only and not (isinstance(w, tk.Misc) and (w.winfo_toplevel() is self or w.winfo_toplevel()
                                                                in self.companion_tops)):
                return None  # (companion_tops: drawing on Domino, window/domino_companion.py, is the main window)
            if isinstance(w, (tk.Entry, ttk.Entry)) and str(w.cget("state")) != "readonly":
                return None
            if self.tracks_shown() and fn not in (self.toggle_tracks, self.toggle_play, self.generate,
                                                   self.new_project, self.open_project):
                # (the track list shows: shortcuts never change shapes out of sight; the list's own keys work there,
                # group E)
                tl = self.track_list
                own = {self.select_all: tl.select_all, self.copy_selected: tl.key_copy, self.cut_selected: tl.key_cut,
                       self.paste: tl.key_paste, self.copy_to_domino: self.copy_to_domino}.get(fn) or getattr(
                           fn, "in_list", None)
                if own is not None and not self.track_list.busy():
                    own()
                return "break"
            if self.box_drawn() or not while_held and (self.roll.holding() or self.vel.edit or self.held_scrub
                                                       or getattr(self, "tempo_pane", None) is not None
                                                       and self.tempo_pane.drag):
                return "break"  # (a velocity line / something in the Tempo pane held: nothing changes under it)
            fn()
            return "break"
        return handler

    def typing_in(self, box):
        """box has the keyboard (or its dropdown list is open)."""
        try:
            return self.focus_get() is box
        except (KeyError, tk.TclError):  # (a dropdown's list has it)
            return True

    def app_only(self, w):
        """w is in a window about Spiderweb itself (Settings, Help, a tip, the update popup: their class has
        APP_ONLY): keys pressed there never change the shapes behind it."""
        if self.in_settings(w):
            return True
        try:
            return getattr(w.winfo_toplevel(), "APP_ONLY", False)
        except (AttributeError, tk.TclError):  # (Tk's own widgets come as a name: a dropdown's list)
            return False

    def in_settings(self, w):
        """w is in the Settings window (or one of its pop-ups)."""
        sw = getattr(self, "settings_window", None)
        return sw is not None and str(w).startswith(str(sw))

    def grabbed_elsewhere(self):
        """A pop-up holds the mouse and keyboard (grab_set: Range…, formula Custom…, tumour graph, snap picker)."""
        try:
            g = self.grab_current()
        except (KeyError, tk.TclError):  # (a grab on a window tkinter doesn't know, e.g. a message box)
            return True
        return g is not None and g.winfo_toplevel() is not self

    def in_hz(self, widget=None):
        """The Hz bass window (or a window of its own) has the keyboard (widget: the one a key was pressed in)."""
        if not self.hz_window:
            return False
        if widget is None:
            try:
                widget = self.focus_get()
            except (KeyError, tk.TclError):  # (a dropdown's list has it)
                return False
        return widget is not None and str(widget).startswith(str(self.hz_window))

    def box_drawn(self):
        """A Select box being drawn on the main piano roll: shortcuts do nothing until it's let go (user: like many
        MIDI programs)."""
        return bool(self.roll.drag) and self.roll.drag[0] == "box"

    def velocity_height(self):
        if self.show_velocity.get() and self.vel_box.winfo_ismapped() and self.vel_box.winfo_height() > 1:
            self._vel_height = self.vel_box.winfo_height()
        return self._vel_height

    def tempo_height(self):
        if self.show_tempo.get() and self.tempo_box.winfo_ismapped() and self.tempo_box.winfo_height() > 1:
            self._tempo_height = self.tempo_box.winfo_height()
        return self._tempo_height

    def toggle_tempo(self, tip=True):
        shown = list(map(str, self.panes.panes()))
        if self.show_tempo.get():
            if str(self.tempo_box) not in shown:
                self.panes.add(self.tempo_box, stretch="never", height=self._tempo_height,
                               minsize=int(60 * self.scale))
            self.update_idletasks()
            self.tempo_box.sync()
            self.tempo_pane.redraw()
            if tip:
                self.tips.show("conductor")
        else:
            self.tempo_height()
            self.tempo_pane.close_typing(True)
            if self.tempo_pane.placing or getattr(self, "key_pane", None) is self.tempo_pane:
                self.tempo_pane.cancel_placing()  # (Delete / Esc the piano roll's again; picks kept for later)
                self.key_pane = None
            if str(self.tempo_box) in shown:
                self.panes.forget(self.tempo_box)
            self.tempo_pane.delete("all")
        self.schedule_autosave()

    def tempo_changed(self, restoring=False):
        """The tempo line changed (an edit, undo, a project opened): the pane and its bar show it. restoring: an undo
        / redo half way (its own syncing does the Hz bass warnings once everything is back: hunt, a redraw half way
        moved the synth window's preset name)."""
        if hasattr(self, "tempo_pane") and not restoring:
            self.tempo_pane.write_heard()  # (Draw: Heard tempo: the edited line back into its Keep timing part)
        tempo_edited()
        self.tempo_count = getattr(self, "tempo_count", 0) + 1  # (Fill with tempo jumps' preview: made again)
        if not hasattr(self, "tempo_box"):
            return
        if not self.tempo_pane.drag:  # (a jump's first point nothing glides into: gone; a drag: when let go)
            drop_loose(self.tempo["points"])
        self.tempo_box.sync()
        self.tempo_pane.request_redraw()
        self.roll.draw_tempo_marks(self.roll.kept_box() or [])  # (the ruler's marks; shown with the pane hidden too)
        if not restoring and not self.tempo_pane.drag and any(sh.get("hz") for sh in self.shapes):
            # (a Hz bass now under another tempo than it was made for: its "Tempo changed" warning, user 2026-10-23)
            self.sync_hz_panels()
            if self.hz_window:
                self.hz_window.show_stale()
                self.hz_window.redraw()
        self.schedule_autosave()
        if self.player.running:  # (playing: on at the new tempo; while a drag goes on, once the mouse rests)
            if getattr(self, "_tempo_play_job", None):
                self.after_cancel(self._tempo_play_job)
            self._tempo_play_job = self.after(80 if self.tempo_pane.drag else 0, self.play_tempo)
        else:  # (Rendered ahead made while stopped: at the new tempo, once it rests; hunt 2026-11-03)
            self.ahead_play.mark_dirty()
            self.kick_ahead()

    def let_go(self):
        """The mouse let go of a drag on the piano roll or in the Conductor pane: what the side panel and the open
        windows say about the shapes follows (Hz bass "Tempo changed", the path window's B; hunt 10-27)."""
        if any(sh.get("hz") for sh in self.shapes):
            self.sync_hz_panels()
            if self.hz_window:
                self.hz_window.show_stale()
        self.path_follow()

    def path_follow(self):
        """The open Copy along a path window's boxes and notes as the shapes / bars / Channels are now (not the box
        being typed in)."""
        w = self.path_window
        if not w or not w.winfo_exists():
            return
        try:
            typing = self.focus_get() in w.entries.values()
        except (KeyError, tk.TclError):
            typing = True
        if not typing:
            w.show()

    def meter_changed(self):
        """A time / key signature added, changed or removed: the bar lines everywhere follow."""
        self.tempo_changed()
        self.sync_title()  # (an Extract's heading names its bars, hunt 10-27)
        self.path_follow()
        self.roll.request_redraw()
        self.vel.request_redraw()
        if self.hz_window and hasattr(self.hz_window, "redraw"):
            self.hz_window.redraw()
        args = getattr(self, "_list_args", None)  # (Extracts are named by their bars: shape_label, hunt 2026-10-26)
        if args is not None and len(args[0]) == len(self.shapes) and any(sh.get("extract") for sh in self.shapes):
            self.fill_shape_list(*args)
            self.sync_list_selection()

    def bar_text(self, beat):
        """A beat as "bar 3 beat 2" (bar and beat counted from 1 by the time signatures, beat may have a
        fraction; beats are the signature's own: 6/8 counts eighths)."""
        bar, into, _, (top, bottom) = where(sigs_of(self.tempo), beat)
        return tr("tempo.where", bar=bar + 1, beat=fmt(round(into * bottom / 4 + 1, 3)))

    def toggle_velocity(self, tip=True):
        if self.show_velocity.get():
            if str(self.vel_box) not in map(str, self.panes.panes()):
                more = {"before": self.tempo_box} if str(self.tempo_box) in map(str, self.panes.panes()) else {}
                self.panes.add(self.vel_box, stretch="never", height=self._vel_height, minsize=int(50 * self.scale),
                               **more)
            self.update_idletasks()  # lay it out now, so it's drawn fresh before it shows up
            self.vel.redraw()
            if tip:
                self.tips.show("velocity")
        else:
            if self.vel_box.winfo_ismapped() and self.vel_box.winfo_height() > 1:
                self._vel_height = self.vel_box.winfo_height()
            if str(self.vel_box) in map(str, self.panes.panes()):
                self.panes.forget(self.vel_box)
            self.vel.delete("all")  # hidden, it isn't kept up to date: never show an old picture
            self.vel.forget_pictures()  # (and lets go of the notes it last showed: a whole old copy on big projects)
        self.schedule_autosave()

    def remember_geometry(self, e):
        if e.widget is self and self.wm_state() == "normal":
            self._normal_geometry = self.wm_geometry()

    def on_close(self):
        if self.drawer and self.drawer.winfo_exists() and not self.drawer.may_quit():
            return
        if getattr(self, "fold_window", None):  # (Fold tracks' preview never kept: as its Cancel, hunt 11-07)
            self.fold_window.cancel()
        if self.companion is not None:  # (drawing on Domino: back first, so the project's own PPQ is what's saved)
            self.companion.stop(now=True)
        if self.hz_window and self.hz_window.draft is not None:  # (a path drawn there: made, as when the Hz bass
            self.hz_window.make_draft()  # window itself is closed)
        job = getattr(self, "_exporting", None)
        if job:  # (a loaded MIDI's export in the background: asked, stopped, its half-written file deleted; hunt)
            if not messagebox.askyesno(tr("app.spiderweb_2"), tr("load.close_exporting"), icon="warning",
                                       default="no", parent=self):
                return
            job["cancel"] = True
            until = time.perf_counter() + 10
            while not job["finished"] and time.perf_counter() < until:
                self.update()
                time.sleep(0.02)
        if not self.close_autosave():
            return
        self.finish_copy()  # (a Ctrl+C's text still being made: on the clipboard before Spiderweb closes)
        self.stop_play()
        self.out.close()
        self.destroy()


# the notes shapes_changed makes: read while they're still made in the background = finished first (notes_wait.py)
LATE_NOTES = ("rendered", "drawn", "drawn_all", "pattern", "note_counts", "slot_count", "voice_slots", "cut_emptied",
              "mask_covered", "colours_wanted", "picture_use10", "drawn_slots")
for _name in LATE_NOTES:
    setattr(App, _name, notes_wait.late_attr(_name))
