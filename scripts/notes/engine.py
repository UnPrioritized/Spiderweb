"""
Turns drawn shapes into MIDI notes: what a shape is, its notes, and fixing overlaps / picking channels.

The maths for each kind lives in its own file: paths.py (lines, polylines, freehand, curves), custom.py
(custom shapes), funnel.py (funnels), envelope.py (velocities).
"""

import itertools
import json
import math
import re
import threading
from contextlib import contextmanager

import numpy as np

from files.lang import tr
from files.own_tracks import FILE0, FILE_MOST, HAND, ID_MOST, INTO_MOST
from files.speed import key_sort, loops
from notes.custom import (ALIGNS, ENDS,CUSTOM_DEFAULTS, CUSTOM_FLAGS, FILLS, BOX_STROKE, block_notes, check_notes,
                          clean_curve, clean_cycle,
                          clean_strokes, custom_notes_groups, custom_strokes, cycle_turns, cycling)
from notes.between import KINDS as BETWEEN_KINDS, clean_between
from notes.envelope import env_values, velocity_env
from notes.loaded import clean_extract
from notes.joined import clean_joined, is_joined, joined_paths
from notes.hzbass import (HZ_KINDS, clean_hz, heard_layers, layers_of, left_edge, note_lengths, own_channels,
                          velocity_parts, with_layers, without_cc)
from notes.hz_cc import cc_fall, ring_of, voice_hz, voices
from notes.funnel import clean_funnel, clean_starts, funnel_notes, funnel_strokes, old_funnel
from notes.arc import arc_k, arc_points
from notes.gliss import bent, clean_gliss, for_new, gliss_notes
from notes.areas import clean_areas
from notes.nested import clean_nested
from notes.tiles import clean_tiles
from notes.ripples import clean_ripples, free_span, grows_free
from notes.chop import apply_chop
from notes.claw import apply_claw
from notes.copypath import apply_path
from notes.fx import (at_step, clean_fx, flip_shape, glue_from, groups, idle, is_page, mirrored, notes_box, pages, reverse_velocity, swapped, toggled,
                      turn_notes, turn_pts, turn_shape, velocities)
from notes.gaterange import clean_range
from notes.gateflip import is_flipped as is_gate_flipped, is_pasted_flipped, mirrored_runs, unflipped_starts
from notes.merge import (LAP as merge_lap, clean_merge, clean_unmerge, recipe_notes,
                         reshaped_notes as merge_reshaped, turned_notes as merge_turned, with_rounds as merge_rounds)
from notes.glue import apply_glue, clean_glue, glue_box
from notes.strum import apply_strum
from notes.trim import apply_trim
from notes.recolour import (KEEP, PAGE, box_of, clean_recolour, is_recoloured, page_split, recoloured, rule_of,
                            without_recolour)
from notes.subtract import clean_subtract, is_subtracted, side_used, subtracted
from notes.extend import clean_extend, extended, is_extended
from notes.sustain import auto_gap, clean_sustain, cut_by, held, is_sustained, sustain_on
from notes.bezier import anchor_count, sample
from notes.paths import SNAP_ENDS, dedupe, dot_segment_notes, path_notes, rows_up_to, snap_steps
from notes.pattern import FORMULA_KINDS, clean_pattern, clean_shape_formula, formed_paths
from notes.custom import ROUND, block_shift, frame_upright, notes_resampled, pack_notes, path_colours, unpack_notes
from notes.picture import clean_picture, turned_notes
from notes.polygon import clean_polygon, polygon_strokes
from notes.mask import clean_mask, is_mask
from notes.passengers import check_cc
from notes.strips import clean_strips
from notes.mould import clip_to, mould_cut, piece_loops
from notes.sliced import (clean_cut, clip_strokes, cut_through, in_part, knife_cut, made_at_cut, moved, moved_by,
                          own_velocities, with_whole_velocity,
                          piece_knives, piece_notes, run_origins, snap_piece, source, spotted_notes, steps_kept,
                          whole_made)
from notes.smooth import clean_level, smooth_path
from notes.steps import RUNNER, expanded, whole_steps
from notes.text import clean_text
from notes.tumour import LINE_KINDS, clean_tumour, tumour_path

KINDS = {"line": tr("engine.line"), "poly": tr("engine.polyline"), "free": tr("engine.freehand"),
         "curve": tr("engine.curve"), "arc": tr("engine.arc"), "custom": tr("engine.custom"),
         "funnel": tr("engine.funnel"), "gliss": tr("engine.glissando")}
POINT_NAMES = {"line": [tr("engine.a"), tr("engine.b")], "gliss": [tr("engine.start"), tr("engine.end")],
               "arc": [tr("engine.start"), tr("engine.through"), tr("engine.end")],
               "funnel": [tr("engine.line_start"), tr("engine.line_end"), tr("engine.wall_1"), tr("engine.wall_2")]}


def point_names(sh):
    """Names for the panel's point boxes, or None if the shape has none."""
    if sh["kind"] == "curve" or bent(sh):  # anchors + handles (bezier.py); a plain curve: Start, Handle 1, Handle 2, End
        pts = sh["pts"]
        if len(pts) == 4:
            return [tr("engine.start"), tr("engine.handle_1"), tr("engine.handle_2"), tr("engine.end")]
        last = anchor_count(pts) - 1
        return [(tr("engine.start") if i == 0 else tr("engine.end") if i // 3 == last else
                 tr("engine.anchor", i=i // 3 + 1)) if i % 3 == 0 else "  " + tr("engine.handle")
                for i in range(len(pts))]
    if sh["kind"] == "funnel":  # extra lines
        return POINT_NAMES["funnel"] + [tr("engine.line_2", k=k, end=end) for k in range(2, len(sh["pts"]) // 2)
                                        for end in ("start", "end")]
    return POINT_NAMES.get(sh["kind"])
SHAPE_DEFAULTS = {"vel0": 127, "vel1": 127, "end_dot": False}

# Every MIDI channel except 10 (drums). Each slot gets its own track (MIDI editors want one channel per track):
# slot 0 = track 1 channel 1, slot 9 = track 10 channel 11, slot 15 = track 16 channel 1 again, ...
CHANNELS = [c for c in range(16) if c != 9]
LAST10 = np.array(CHANNELS + [9], np.int64)  # ("Use channel 10": the shapes' 16 channels in order, render)
# 31-EDO "Overlaps keep their pitch" (user 2026-11-10; App.sync_edo_lanes sets edo_lanes): Multi channel's slots go
# only on the first channel of each 31-EDO group (1, 4, 7, 11, 14: the others play 2 / 4 octaves up, playback.EDO_LANE),
# then the next port's same five. Only the slot -> channel / port mapping changes; slots, colours, tracks stay.
EDO_SAFE = np.array([0, 3, 6, 10, 13], np.int64)
edo_lanes = False


def channel_round(use10=False):
    """Slots in one round of channels (then the next track / port): 15, 16 with channel 10, 5 with edo_lanes."""
    return len(EDO_SAFE) if edo_lanes else 16 if use10 else len(CHANNELS)
CLASH = 1 << 16  # a track number this or past it: a Hz bass layer's note clashing with another layer's (layered_notes):
#                  its track // CLASH = how many channels past the shape's own (in every Channels mode)
OWN0 = 1 << 32  # a slot this or past it: a track added by hand (files/own_tracks.py), OWN0 + its id * 16 + its
#                 channel (so the channel is slot & 15 everywhere); the automatic slots are all below it


def own_slot(track_id, ch):
    return OWN0 + track_id * 16 + ch


def own_id(slot):
    """The id of the track added by hand that slot (>= OWN0) is."""
    return (slot - OWN0) >> 4


def track_ids(slots):
    """The track list's track ids of notes on these slots: the slot itself; on a track added by hand, HAND + its
    id (files/own_tracks.py); in a loaded MIDI's track n (FILE0 + n), -1 - n."""
    slots = np.asarray(slots, np.int64)
    hand = slots >= OWN0
    if not hand.any():
        return slots
    i = (slots - OWN0) >> 4
    return np.where(hand, np.where(i >= FILE0, FILE0 - 1 - i, HAND + i), slots)


def auto_count(slots):
    """How many automatic slots there are up to the last one used (slots: the notes' slot column): own tracks'
    left out."""
    slots = np.asarray(slots)
    auto = slots[slots < OWN0] if len(slots) and slots.max() >= OWN0 else slots
    return int(auto.max()) + 1 if len(auto) else 0


# ---------------------------------------------------------------- shapes

def make_shape(kind, pts, defaults):
    sh = dict(defaults, kind=kind)
    if kind in ("line", "funnel", "gliss"):
        sh["pts"] = [list(pts[0]), list(pts[-1])]
        if kind == "gliss":  # (its own copy of the settings: the defaults' stay as they are)
            sh["gliss"] = for_new(clean_gliss(defaults.get("gliss")))
        if kind == "funnel":
            sh["starts"] = []  # the line only: the wall is drawn next, then the curves are added
    elif kind == "curve":
        a, b = pts[0], pts[-1]
        mid = (a[0] + b[0]) / 2
        sh["pts"] = [list(a), [mid, a[1]], [mid, b[1]], list(b)]  # gentle S-curve to start with
    else:
        sh["pts"] = [list(p) for p in pts]
    return sh


def clean_basics(sh):
    """vel0 / vel1 / end_dot from a file (a shape, or the defaults for new shapes), made valid: a broken one = its
    default."""
    out = dict(SHAPE_DEFAULTS)
    for k in ("vel0", "vel1"):
        v = sh.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out[k] = max(1, min(127, v))
    out["end_dot"] = bool(sh.get("end_dot", False))
    return out


FAR = 1e12  # no number in a shape is ever this big (a beat that far is long past what a MIDI file can reach)
BEATS_FAR = 1e7  # a shape reaching past this beat (months of music) or key is a changed file: its notes alone could
KEYS_FAR = 1e4  # take all the memory there is (keys are 0-255; hz_settings.LONGEST for Hz tones)
ALL_KEYS = 256  # keys a note can be on (0-255): a whole channel taken, split by key (assign_slots extra)


def sane(x):
    """True if every number in x (a shape or strokes as read from a file: nested dicts and lists) is a real number
    no further than FAR from 0 (a hand-changed Infinity / NaN / 1e300 broke the drawing)."""
    todo = [x]
    while todo:  # (no recursion: a deeply nested file can't run out of stack)
        x = todo.pop()
        if isinstance(x, dict):
            todo.extend(x.values())
        elif isinstance(x, list):
            if len(x) >= SANE_AT_ONCE:  # (a long list of only numbers: looked at in one go)
                ok = numbers_in_reach(x)
                if ok is not None:
                    if not ok:
                        return False
                    continue
            todo.extend(x)
        elif isinstance(x, (int, float)) and not -FAR <= x <= FAR:  # (NaN is never between)
            return False
    return True


SANE_AT_ONCE = 16  # (shorter lists: one by one is quicker)


def numbers_in_reach(x):
    """A list (of lists ...) holding ONLY numbers, all the same length at each level: are they all within FAR of 0?
    None: anything else (text, dicts, ragged rows, a huge whole number): sane looks at it one by one. (A piece of
    a drawing sliced 720 times stores ~720 cuts: one by one, opening took seconds, 2026-11-08.)"""
    try:
        a = np.asarray(x)
    except (ValueError, TypeError, OverflowError):
        return None
    if a.dtype.kind not in "fiub" or a.dtype == object:
        return None
    return bool(((a >= -FAR) & (a <= FAR)).all())


def clean_kept_patterns(kept):
    """A saved shape's "patterns_kept" (custom.shelve_patterns: Nested / Tiles / Ripples settings while another
    Inside fill is picked), each cleaned like the live one, no centre points; {} when nothing usable."""
    if not isinstance(kept, dict):
        return {}
    out = {}
    for key, clean in (("nested", clean_nested), ("tiles", clean_tiles), ("ripples", clean_ripples)):
        c = clean(kept.get(key)) if isinstance(kept.get(key), dict) else None
        if c:
            c.pop("at", None)
            out[key] = c
    return out


_unmade = threading.local()


@contextmanager
def merges_unmade():
    """clean_shape inside (this thread): a merged shape saved without its notes gets a one-note stand-in instead of
    its notes made again (they can be millions: asked about first, counted from its parts, App.note_count). Only
    for counting: the shapes are cleaned again to be used."""
    kept = getattr(_unmade, "on", False)
    _unmade.on = True
    try:
        yield
    finally:
        _unmade.on = kept


BY_SNAP_MOST = 10 ** 4  # the longest "Notes by snap" step, beats
BY_SNAP_LEAST = 1 / 65535  # the shortest, beats (a tick at the highest PPQ; notes use at least a tick anyway)


def snap_name(text):
    """A typed note length like "1/32" or " 3 / 16 " -> "3/16" (whole-note fraction, like the Snap list), else None."""
    m = re.fullmatch(r"\s*(\d{1,3})\s*/\s*(\d{1,3})\s*", text) if isinstance(text, str) else None
    # (only what looks like a note: top 1-100, bottom 1-128 like the Snap list's own; "480/2" = a sum of ticks,
    # hunt 2026-10-28)
    if not m or not 1 <= int(m.group(1)) <= 100 or not 1 <= int(m.group(2)) <= 128:
        return None
    return f"{int(m.group(1))}/{int(m.group(2))}"


def snap_name_beats(name):
    """"3/16" -> its length in beats (quarter notes)."""
    a, b = name.split("/")
    return int(a) * 4 / int(b)


def clean_by_snap(v):
    """A "Notes by snap" step length (beats) from a file, or None (off / unusable)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return min(max(v, BY_SNAP_LEAST), BY_SNAP_MOST) if math.isfinite(v) and v > 0 else None


def aligned_ch(sh):
    """{"into_ch", "into_home"} kept one per "into" entry (colours laid again change into's length: App.fit_into):
    cut to it, or filled with -1 (= the track's channel); a shape without into_ch: {}. Without into_home: none."""
    chs, into = sh.get("into_ch"), sh.get("into")
    if not isinstance(chs, list) or not isinstance(into, list):
        return {}
    n = len(into)
    out = {"into_ch": (list(chs) + [-1] * n)[:n]}
    home = sh.get("into_home")
    if isinstance(home, list):
        out["into_home"] = (list(home) + [-1] * n)[:n]
    return out


def rest_ch(sh):
    """(channels, homes) of the colours gone for now (sh["into_rest"], App.fit_into), one per entry like aligned_ch
    (-1 = the track's channel): kept so a colour coming back plays on the channel it had in a folder (hunt C
    2026-10-28). Missing / stale ones (into_rest cleared elsewhere) cut or filled to its length."""
    n = len(sh.get("into_rest") or []) if isinstance(sh.get("into_rest"), list) else 0
    chs, home = sh.get("into_rest_ch"), sh.get("into_rest_home")
    chs = [c if type(c) is int and -1 <= c <= 15 else -1 for c in chs] if isinstance(chs, list) else []
    home = [v if type(v) is int else -1 for v in home] if isinstance(home, list) else []
    return (chs + [-1] * n)[:n], (home + [-1] * n)[:n]


def clean_shape(sh):
    """Shape loaded from a file -> valid shape, or None if it's a kind we no longer support or has a number that
    can't be used."""
    if sh.get("kind") not in KINDS or len(sh.get("pts", [])) < 1 or not sane(sh):
        return None
    out = clean_basics(sh)
    out["kind"] = sh["kind"]
    out["pts"] = [[float(b), float(p)] for b, p in sh["pts"]]
    env = sh.get("vel_env")
    if env:
        out["vel_env"] = [[float(u), max(1.0, min(127.0, float(v)))] for u, v in env]
    gl = clean_glue(sh.get("glue"))
    if gl:  # touching notes on a key made one (glue.py)
        out["glue"] = gl
    cut = clean_cut(sh["cut"]) if isinstance(sh.get("cut"), dict) else None
    if cut:  # a piece cut from another shape, keeping its notes (sliced.py)
        out["cut"] = cut
        if out.get("vel_env"):  # (older files: every earlier cut's point on the line before its start)
            from notes.joined import thin_lead
            out["vel_env"] = thin_lead(out["vel_env"])
    fx = clean_fx(sh, steps=bool(cut) and (whole_made(cut) or bool(sh.get("ripples"))))  # (sliced.steps_kept)
    if fx:  # Chop / Claw machine / Strum pages, flips and velocities after them (fx.py)
        out["fx"] = fx
    cy = clean_cycle(sh.get("cycle"))
    if cy:  # "Colours" (custom.py)
        out["cycle"] = cy
    mk = clean_mask(sh.get("mask"))
    if mk:  # a mask (mask.py)
        out["mask"] = mk
    tm = clean_tumour(sh.get("tumour")) if out["kind"] in LINE_KINDS else None
    if tm:
        out["tumour"] = tm
    st = clean_by_snap(sh.get("by_snap")) if out["kind"] in LINE_KINDS else None
    if st:  # "Notes by snap": every note one step long, in beats (paths.snap_steps)
        out["by_snap"] = st
        name = snap_name(sh.get("by_snap_name"))
        if name and abs(snap_name_beats(name) - st) < 1e-9:  # (typed as a note length: shown so, follows the PPQ)
            out["by_snap_name"] = name
        if sh.get("by_snap_end") in SNAP_ENDS[1:]:  # ("Line's end": missing = the last step cut short)
            out["by_snap_end"] = sh["by_snap_end"]
    bt = clean_between(sh.get("between")) if out["kind"] in BETWEEN_KINDS else None
    if bt:  # one of an Add between group (between.py)
        out["between"] = bt
    tm = sh.get("team")
    if (not bt and isinstance(tm, list) and len(tm) in (2, 3) and isinstance(tm[0], str)
            and all(type(t) is int for t in tm[1:])):
        # (its group's colour turn, kept when Turn into plain notes unlinked it, + its repeat: between.turns)
        out["team"] = list(tm)
    # the tracks it was laid on, one per colour (files/own_tracks.py into_ids: a track added by hand's id, or -1 - an
    # automatic track's slot), the colours' columns still to lay (Domino's paste), gap tracks it keeps, the tracks of
    # colours gone for now (App.fit_into: back on them when they come back)
    # (into / into_rest: a loaded MIDI's track too, FILE0 + its number)
    for key, low, high in (("into", -ID_MOST, FILE_MOST), ("cols", 0, ID_MOST), ("into_gap", 0, ID_MOST),
                           ("into_rest", -ID_MOST, FILE_MOST)):
        got = sh.get(key)
        got = [got] if type(got) is int else got if isinstance(got, list) else []
        got = [v for v in got[:INTO_MOST] if type(v) is int and low <= v < high]
        if got:
            out[key] = got
    # (each colour's own channel in its track, -1 = the track's: an Extract's in the loaded MIDI's track it came
    # from, a channel dragged into a folder, PLAN #11)
    chs = sh.get("into_ch")
    if "into" in out and isinstance(chs, list) and all(type(c) is int and -1 <= c <= 15 for c in chs):
        out.update(aligned_ch({"into": out["into"], "into_ch": chs, "into_home": sh.get("into_home")}))
        home = out.get("into_home")  # (... and that track: on another one the colour takes its channel)
        if home is not None and not all(type(v) is int and -1 <= v < FILE_MOST for v in home):
            out.pop("into_home")
    if "into_rest" in out:  # (... and the colours gone for now: their channels kept, rest_ch)
        rchs, rhome = rest_ch(dict(sh, into_rest=out["into_rest"]))
        if any(c >= 0 for c in rchs):
            out["into_rest_ch"] = rchs
            out["into_rest_home"] = [v if -1 <= v < FILE_MOST else -1 for v in rhome]
    if "into" in out and isinstance(sh.get("slots10"), bool):  # (its automatic tracks' channel rule: mask.slot_rule)
        out["slots10"] = sh["slots10"]
    if "into" in out and sh.get("from_paste") is True:  # (plain copy of a laid Domino paste: keeps its tracks in
        out["from_paste"] = True                       # every Channels mode, App.pasted_notes; hunt 2026-10-29)
    if "into" in out and sh.get("into_new") is True:  # (just drawn / pasted, colours not laid yet: App.fit_into)
        out["into_new"] = True
    if out["kind"] == "custom":
        strokes = clean_strokes(sh.get("strokes"))
        if len(out["pts"]) != 3 or not strokes:
            return None
        out["name"] = str(sh.get("name", ""))
        out["strokes"] = strokes
        areas = clean_areas(sh.get("areas"))
        if areas:  # areas coloured by hand (areas.py)
            out["areas"] = areas
        fit = sh.get("fit")
        try:  # Fit box to drawing: the straight box shown round it, in the frame's u / v (custom.box_pts)
            fit = [[float(u), float(v)] for u, v in fit] if fit else None
        except (TypeError, ValueError):
            fit = None
        if fit and len(fit) == 3 and all(math.isfinite(x) for p in fit for x in p):
            out["fit"] = fit
        out["fill"] = sh.get("fill") if sh.get("fill") in FILLS else "empty"
        out["gate"] = max(1e-6, float(sh.get("gate", CUSTOM_DEFAULTS["gate"])))
        out["align"] = sh.get("align") if sh.get("align") in ALIGNS else "auto"
        out["ends"] = sh.get("ends") if sh.get("ends") in ENDS else "drop"
        out.update({k: True for k in CUSTOM_FLAGS if sh.get(k) is True})
        nested = clean_nested(sh.get("nested")) if out["fill"] in ("fill", "spam") else None
        if nested:  # Inside fill Nested (nested.py)
            out["nested"] = nested
        tiles = clean_tiles(sh.get("tiles")) if out["fill"] in ("fill", "spam") and not nested else None
        if tiles:  # Inside fill Tiles (tiles.py)
            out["tiles"] = tiles
        ripples = (clean_ripples(sh.get("ripples")) if out["fill"] in ("fill", "spam") and not nested and not tiles
                   else None)
        if ripples:  # Inside fill Ripples (ripples.py)
            out["ripples"] = ripples
        kept = clean_kept_patterns(sh.get("patterns_kept"))
        if kept:  # (patterns switched off: their settings come back when picked again, custom.shelve_patterns)
            out["patterns_kept"] = kept
        if sh.get("gate_flip") is True:  # its gates laid from the right (gateflip.py)
            out["gate_flip"] = True
        try:  # the smallest outline gate in beats (custom.grow_inward)
            edge = float(sh.get("edge") or 0)
        except (TypeError, ValueError):
            edge = 0
        if edge > 0:
            out["edge"] = min(edge, 10 ** 4)
        if sh.get("edge_mode") == "sideways":  # (the first way: each note grown sideways; default = even band)
            out["edge_mode"] = "sideways"
        try:  # the view it was first drawn in (custom.drawn_view): a number, or [m, n]
            rd = sh.get("round") or 0
            rd = [float(x) for x in rd] if isinstance(rd, list) else float(rd)
        except (TypeError, ValueError):
            rd = 0
        if isinstance(rd, list):
            if len(rd) == 2 and all(map(math.isfinite, rd)) and rd[1] > 0:
                out["round"] = rd
        elif math.isfinite(rd) and rd > 0:
            out["round"] = rd
        rg = clean_range(sh.get("range"))
        if rg:  # the spam gate going from one to another across the shape (gaterange.py)
            out["range"] = rg
        rg = clean_range(sh.get("range_kept"))
        if rg:  # (one switched off: the Range window brings it back when it's switched on)
            out["range_kept"] = rg
        clean_hz_of(sh, out)
        fr = sh.get("from")  # the shapes it was made of (convert.py)
        if isinstance(fr, dict) and isinstance(fr.get("shapes"), list) and fr["shapes"]:
            olds = [clean_shape(o) if isinstance(o, dict) else None for o in fr["shapes"]]
            try:
                if all(olds) and len(fr["pts"]) == 3:
                    out["from"] = {"shapes": olds, "strokes": clean_strokes(fr["strokes"]),
                                   "pts": [[float(b), float(p)] for b, p in fr["pts"]]}
                    vel = fr.get("vel")  # (its velocities when made: changed since = Split gives them to the shapes)
                    if isinstance(vel, dict):
                        got = {k: v for k, v in clean_basics(vel).items() if k != "end_dot"}
                        if vel.get("vel_env"):
                            got["vel_env"] = [[float(u), max(1.0, min(127.0, float(v)))] for u, v in vel["vel_env"]]
                        out["from"]["vel"] = got
            except (KeyError, TypeError, ValueError):
                pass
        pg = clean_polygon(sh.get("polygon"))
        if pg and "notes" not in sh and not sh.get("text"):  # made by the Polygon tool (polygon.py)
            out["polygon"] = pg
            out["strokes"] = polygon_strokes(pg)
        tx = clean_text(sh["text"]) if isinstance(sh.get("text"), dict) else None
        if tx:  # typed text (text.py): its strokes are the letters, the settings let it be retyped
            out["text"] = tx
        mg = clean_merge(sh["merge"], clean_shape) if isinstance(sh.get("merge"), dict) else None
        if mg:  # Gate sensitive merge (merge.py): two shapes' notes, saved as the recipe
            out["merge"] = mg
            if mg.pop("lift", False):  # (its parts' gate flips moved onto it: notes made again, unflipped parts)
                out["gate_flip"] = True
                sh = {k: v for k, v in sh.items() if k != "notes"}
            if "notes" not in sh:  # (left out of project files: made again)
                sh = dict(sh, notes=pack_notes([[0, 1, 0, 1, 0]]) if getattr(_unmade, "on", False) else
                          recipe_notes(mg))
        if "notes" in sh:  # pasted notes (custom.py): the strokes are just the box
            if not isinstance(sh["notes"], str) or not check_notes(sh["notes"]):
                return None
            out.update(notes=sh["notes"], strokes=[dict(BOX_STROKE)], fill="empty")
            if sh.get("own_vel"):
                out["own_vel"] = True
            if sh.get("flip_colours") is True:  # (sliced from a gate flipped paste: its colours' order kept, gateflip)
                out["flip_colours"] = True
            if isinstance(sh.get("paste"), str):  # pieces sliced from one paste: its tracks share channels (render)
                out["paste"] = sh["paste"]
            if isinstance(sh.get("cc"), str) and check_cc(sh["cc"]):  # its tracks' CCs etc. (passengers.py)
                out["cc"] = sh["cc"]
            st = clean_strips(sh.get("strips"))
            if st:  # Separate strips (strips.py): each strip of a track its own channels
                out["strips"] = st
            um = clean_unmerge(sh["unmerge"], clean_shape) if isinstance(sh.get("unmerge"), dict) else None
            if um:  # a Gate sensitive merge kept as separate shapes: the shape its notes were, for Split (merge.py)
                out["unmerge"] = um
            # Apply mask's notes (mask.py): track = the channel slot it had (True: from a picture, always kept; [beat,
            # key]: Multi channel, while its first point is there), painted in the picture's colours, overlaps kept
            # (copied from a sustained shape), what it is (its name in any language)
            o = sh.get("own_slots")
            if o is True or (isinstance(o, list) and len(o) == 2 and all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in o)):
                out["own_slots"] = o if o is True else [float(v) for v in o]
            if isinstance(sh.get("slots10"), bool):  # (its slots' channel rule when made: mask.slot_rule)
                out["slots10"] = sh["slots10"]
            pn = sh.get("picture_notes")
            if isinstance(pn, dict):  # (the picture's colours, picture.clean_settings' "pal": a list of #rrggbb)
                pal = pn.get("pal")
                ok = isinstance(pal, list) and all(isinstance(h, str) for h in pal)
                out["picture_notes"] = {"pal": list(pal) if ok else None}
            if sh.get("held") is True:
                out["held"] = True
            if sh.get("paste_laps") is True:  # (made from a Domino paste's notes: keeps their overlaps like it, keeps_laps)
                out["paste_laps"] = True
            ex = clean_extract(sh.get("extract"))
            if ex:  # (Load MIDI's Extract: the loaded notes it took stay hidden while it's there, loaded.py)
                out["extract"] = ex
            if sh.get("made") in ("mask", "leftovers", "path", "plain"):
                out["made"] = sh["made"]
            mb = sh.get("made_by")  # (the one-time command that made it + the shape it came from: Properties)
            if isinstance(mb, list) and len(mb) == 2 and all(isinstance(x, str) for x in mb):
                out["made_by"] = [mb[0][:20], mb[1][:200]]
            if isinstance(sh.get("leftover_of"), str):  # (a two-shapes merge's leftovers: its Split takes them back)
                out["leftover_of"] = sh["leftover_of"][:64]
            if sh.get("lapped") is True:  # (merge leftovers: each note's Colours round on its track, + LAP x round)
                out["lapped"] = True
            pic = clean_picture(sh.get("picture"))
            if pic:  # a picture made into notes (picture.py): the notes are its finished colours, track = slot
                out["picture"] = pic
                for k in ("own_vel", "glue", "fx"):  # (no note tools on a picture, user)
                    out.pop(k, None)
    if out["kind"] == "gliss":  # a Glissando run: its line (straight: 2 points, bent: a curve's) + settings (gliss.py)
        n = len(out["pts"])
        out["pts"] = out["pts"][:n - (n - 1) % 3] if n >= 4 else [out["pts"][0], out["pts"][-1]]
        out["gliss"] = clean_gliss(sh.get("gliss"))
    if out["kind"] == "arc":  # start, a point it passes through, end; k = beats per key on screen (arc.py)
        if len(out["pts"]) != 3:
            return None
        out["k"] = arc_k(sh)
    if out["kind"] == "free" and "smooth" in sh:  # made perfect (smooth.py); k = beats per key on screen, like arcs
        out["smooth"] = clean_level(sh["smooth"])
        out["k"] = arc_k(sh)
    if out["kind"] == "curve":  # anchor, handle, handle, anchor, ... (bezier.py)
        n = len(out["pts"])
        if n < 4:
            return None
        c = clean_curve(sh, out["pts"][:n - (n - 1) % 3])
        del c["kind"]
        out.update(c)
        clean_joined(sh, out)  # a joined curve's pieces / tumours (joined.py)
        if is_joined(out):
            out.pop("sym", None)
    if out["kind"] in FORMULA_KINDS:
        pat = clean_pattern(sh.get("pattern"))
        if pat:  # a formula laid along it (pattern.py)
            out["pattern"] = pat
        form = clean_shape_formula(sh.get("shape"))
        if form:  # a formula giving it its shape (pattern.py)
            out["shape"] = form
    if out["kind"] == "funnel":
        try:
            starts = sh.get("starts")
            if len(out["pts"]) == 2 and starts is None:
                out["pts"], starts = old_funnel(dict(sh, pts=out["pts"]))
            if len(out["pts"]) < 4 or len(out["pts"]) % 2:
                return None
            out.update(clean_funnel(sh))
            out["starts"] = clean_starts(starts, len(out["pts"]) // 2 - 1)
        except (TypeError, ValueError, AttributeError):
            return None
        clean_hz_of(sh, out)
    sus = clean_sustain(sh.get("sustain"))
    if sus is not None:  # its notes held on (sustain.py)
        out["sustain"] = sus
    sub = clean_subtract(sh.get("subtract"))
    if sub is not None:  # its gates made shorter (subtract.py)
        out["subtract"] = sub
    ext = clean_extend(sh.get("extend"))
    if ext is not None:  # its gates made longer (extend.py)
        out["extend"] = ext
    rc = clean_recolour(sh.get("recolour"))
    if rc is not None:  # its notes' colours changed (recolour.py)
        out["recolour"] = rc
    from notes.steps import kept_in
    kept_in(out, sh)  # (the order of its steps + the ones switched off: steps.py; saved only when not the default)
    # (again on what was read: a number written as text, "nan" or "1e400", got past sane as text; and where it
    # reaches, its strokes too)
    return out if sane(out) and within_reach(out) else None


def within_reach(sh):
    """True if a shape from a file stays within BEATS_FAR beats / KEYS_FAR keys (a custom shape's strokes too, as
    placed by its frame)."""
    pts = list(sh["pts"])
    if sh["kind"] == "custom" and len(pts) == 3:
        us = [u for st in sh["strokes"] for u in (st["box"][0::2] if "box" in st else [p[0] for p in st["pts"]])]
        vs = [v for st in sh["strokes"] for v in (st["box"][1::2] if "box" in st else [p[1] for p in st["pts"]])]
        (b0, p0), (b1, p1), (b2, p2) = pts
        pts += [(b0 + u * (b1 - b0) + v * (b2 - b0), p0 + u * (p1 - p0) + v * (p2 - p0))
                for u in (min(us), max(us)) for v in (min(vs), max(vs))]
    return all(abs(b) <= BEATS_FAR and abs(p) <= KEYS_FAR for b, p in pts)


def clean_hz_of(sh, out):
    """A custom shape's / funnel's Hz bass from a file into out (the gate is one wave of a tone), with the spam gate
    it had before Hz bass was ticked (back when it's unticked)."""
    hz = clean_hz(sh.get("hz"))
    if not hz:
        return
    out["hz"] = hz
    was = sh.get("before_hz")
    try:
        gate = float(was["gate"])
        if math.isfinite(gate) and gate > 0:
            out["before_hz"] = {"gate": gate, **({"range": True} if was.get("range") else {})}
    except (TypeError, KeyError, ValueError, AttributeError):
        pass


def shape_path(sh):
    """The shape as one polyline of (beat, pitch) points (a custom shape: all its strokes one after another)."""
    if sh["kind"] in ("custom", "funnel"):
        return [p for stroke in shape_strokes(sh) for p in stroke]
    pts = [tuple(p) for p in sh["pts"]]
    if sh["kind"] == "curve" or bent(sh):  # (a bent Glissando run: its line is a curve too, gliss.py)
        pts = sample(pts, 240)
    elif sh["kind"] == "arc":
        pts = arc_points(pts, sh.get("k", 1.0))
    elif sh["kind"] == "free" and sh.get("smooth"):  # straightened / a perfect shape (the drawn points stay)
        pts = smooth_path(pts, sh["smooth"], sh.get("k", 1.0))
    if sh.get("shape") or sh.get("pattern"):  # a shape / pattern formula laid along it (pattern.py)
        pts = formed_paths([pts], sh)[0]
    tm = sh.get("tumour")
    return tumour_path(pts, tm) if tm and tm["on"] else pts


def shape_strokes(sh):
    """The shape as separate polylines (only custom shapes and funnels have more than one)."""
    if sh["kind"] == "custom":
        return custom_strokes(sh)
    if sh["kind"] == "funnel":
        knives = piece_knives(sh)  # (a piece: only its side, sliced.py)
        return clip_strokes(funnel_strokes(sh), knives) if knives else funnel_strokes(sh)
    paths = joined_paths(sh, tumour_path) if is_joined(sh) else [shape_path(sh)]  # (joined: one path per piece)
    got = piece_loops(sh) if sh.get("cut") else None  # (cut with a shape: only its side, mould.py)
    if got:
        return clip_to(paths, *got)
    knives = piece_knives(sh) if sh.get("cut") else None  # (a Notes by snap piece: only its side, sliced.snap_piece)
    return [np.asarray(p, float) for p in clip_strokes(paths, knives)] if knives else paths


_paths = {}
SHAPE_KEYS = ("starts", "tumour", "k", "text", "smooth", "gaps", "splits",
              "tumours", "pattern", "shape")  # what changes how a funnel / tumours / an arc / text /
# a straightened freehand stroke look (besides the points)


_held = threading.local()  # (same_shapes: {id(shape): (shape, its _cached)} while nothing changes the shapes)
PATHS_KEPT = 3000  # _paths holds at least this many shapes' lines
_last_pass = 0  # shapes the last whole same_shapes pass asked for


@contextmanager
def same_shapes():
    """While the shapes can't change (the piano roll's redraw): _cached works each one out once, then knows it by
    the object itself (its key's JSON ~0.06 ms a circle, asked 3 times a shape a redraw: 200 shapes ~36 ms, redraw
    hunt 2026-10-31)."""
    outer = getattr(_held, "seen", None)
    _held.seen = {} if outer is None else outer
    try:
        yield
    finally:
        global _last_pass
        if outer is None:
            _last_pass = len(_held.seen)
        _held.seen = outer


def _cached(sh):
    seen = getattr(_held, "seen", None)
    if seen is not None:
        got = seen.get(id(sh))
        if got is not None and got[0] is sh:
            return got[1]
        seen[id(sh)] = (sh, _keyed(sh))
        return seen[id(sh)][1]
    return _keyed(sh)


def _keyed(sh):
    key = (sh["kind"], tuple(map(tuple, sh["pts"])), json.dumps(sh.get("strokes")),
           json.dumps([sh.get(k) for k in SHAPE_KEYS]),
           json.dumps([sh["cut"].get("knife"), sh["cut"]["was"], sh.get("fx"), sh.get("_m"), sh["cut"].get("mould")])
           if sh.get("cut") and (sh["kind"] == "funnel" or sh["cut"].get("mould") or snap_piece(sh)) else None)
    got = _paths.get(key)
    if got is None:
        seen = getattr(_held, "seen", None)
        if len(_paths) > max(PATHS_KEPT, 2 * _last_pass, 2 * len(seen or ())):
            _paths.clear()  # (room for twice the shapes a redraw uses: a fixed 3,000 emptied it mid-redraw past
            # ~3,000 shapes, so every kept roll line was made again: 4,000 shapes 0.4 s a drag step, hunt 11-06)
        got = _paths[key] = {"strokes": shape_strokes(sh)}
    return got


def cached_strokes(sh):
    """shape_strokes, remembered (curves are 240 points, circles 360; recomputing them every frame adds up)."""
    return _cached(sh)["strokes"]


def cached_arrays(sh):
    """cached_strokes as NumPy (N, 2) arrays, remembered too (a line with tiny tumours has many points)."""
    got = _cached(sh)
    if "arrays" not in got:
        got["arrays"] = [np.asarray(st, float).reshape(-1, 2) for st in got["strokes"]]
    return got["arrays"]


def cached_reach(sh):
    """(first beat, lowest key, highest key, last beat) of cached_arrays, None when nothing's drawn; remembered with
    them (every redraw asks it of every shape: 4,000 shapes ~70 ms a redraw, speed hunt 2026-10-29)."""
    got = _cached(sh)
    if "reach" not in got:
        arrays = [a for a in cached_arrays(sh) if len(a)]
        if arrays:
            a = np.concatenate(arrays)
            got["reach"] = (a[:, 0].min(), a[:, 1].min(), a[:, 1].max(), a[:, 0].max())
        else:
            got["reach"] = None
    return got["reach"]


def cached_path(sh):
    """The shape as one list of (beat, pitch) points (a custom shape: all its strokes one after another)."""
    strokes = cached_strokes(sh)
    return strokes[0] if len(strokes) == 1 else [p for st in strokes for p in st]


NO_NOTES = np.zeros((0, 6), np.int64)


def note_array(notes, columns):
    """A list of note tuples (or an array) -> NumPy int64 array with that many columns."""
    return np.asarray(notes, np.int64).reshape(-1, columns)


def unique_inverse(a):
    """np.unique(a, return_inverse=True) of a 1-D array, the inverse flat. Whole numbers in a small range (tracks,
    keys): counted instead of sorted (millions of a Hz bass's notes: a sort took ~0.1 s)."""
    if a.dtype.kind in "iu" and len(a) and int(a.max()) - int(a.min()) <= max(1 << 20, len(a)):
        lo = int(a.min())
        there = np.bincount(a - lo) > 0
        ids = np.flatnonzero(there)
        at = np.cumsum(there) - 1
        return (ids + lo).astype(a.dtype), at[a - lo]
    ids, which = np.unique(a, return_inverse=True)
    return ids, which.ravel()


def unique_rows(a):
    """a without rows that repeat an earlier row (order kept)."""
    keep = unique_index(a)
    return a if len(keep) == len(a) else a[keep]


def unique_index(a):
    """The rows unique_rows keeps."""
    if len(a) < 2:
        return np.arange(len(a))
    if a.dtype == np.int64 and a.ndim == 2 and a.shape[1] == 5:  # (pasted notes + velocity + track: by key, start,
        # end, track; rows alike in those four (few) sorted again with the velocity. 3 M notes 0.65 s -> ~0.15 s)
        c = a.T
        order = key_sort(c[2], c[0], c[1], c[4])
        same = np.zeros(len(a), bool)
        same[1:] = True
        for col in (2, 0, 1, 4):  # (a column at a time: the whole sorted copy was 2 x all the notes, 381 MB at 5 M)
            x = c[col][order]
            same[1:] &= x[1:] == x[:-1]
            del x
        if not same.any():
            return np.arange(len(a))
        group = same.copy()
        group[:-1] |= same[1:]
        idx = np.sort(order[group])  # (earliest first: the stable sort below keeps it first among repeats)
        sub = a[idx]
        so = np.lexsort(sub.T[::-1])
        sb = sub[so]
        again = np.zeros(len(sb), bool)
        again[1:] = (sb[1:] == sb[:-1]).all(axis=1)
        keep = np.ones(len(a), bool)
        keep[idx[so[again]]] = False
        return np.flatnonzero(keep)
    if a.dtype == np.int64 and a.ndim == 2 and a.shape[1] in (3, 4):  # (notes: by key, then start, end, track;
        c = a.T  # any order bringing repeats together does, and keys sort quickest)
        order = key_sort(c[2], c[0], c[1], c[3] if a.shape[1] == 4 else c[1])
    else:
        order = np.lexsort(a.T[::-1])  # stable: within repeats the earliest comes first
    b = a[order]
    new = np.ones(len(a), bool)
    new[1:] = (b[1:] != b[:-1]).any(axis=1)
    return np.arange(len(a)) if new.all() else np.sort(order[new])


def shape_notes(sh, ppq, keys=128):
    """All notes of one shape as a NumPy array of (start, end, pitch, velocity) rows in ticks, before overlap
    handling. keys: the project's key range (keys 0 .. keys - 1)."""
    return shape_notes_tracks(sh, ppq, keys)[0]


def shape_notes_tracks(sh, ppq, keys=128):
    """shape_notes, and for pasted notes which track each note came from, for a custom shape made of other shapes
    which of them (one number per row; None for every other shape)."""
    sh = made_from(sh)

    def make(s, keys_in):
        done = {}

        def get(s):
            key = json.dumps(s, sort_keys=True)
            if key not in done:
                done[key] = fx_notes(s, ppq, keys_in, get)
            return done[key]
        return get(s)
    got = edge_made(sh, ppq, keys, make)  # (reaching past the keys / before beat 0: made whole, cut at the end)
    return got if got is not None else make(sh, keys)


def whole_notes_tracks(sh, ppq, keys=128, over_top=None):
    """shape_notes_tracks WHOLE: a shape past the edges (past_edges) with its notes past the keys / before beat 0
    kept where they are, not cut off ("Keep notes made there": Gate sensitive merge's parts, Slice on pasted notes).
    Inside the edges: shape_notes_tracks. over_top=True: over the top key whole too whatever "Space past the keys"
    says now (a merge made so must come back the same on any PC / setting, hunt 2026-11-08)."""
    out = past_edges(made_from(sh), keys, ppq, over_top)
    if out is None:
        return shape_notes_tracks(sh, ppq, keys)
    lifted, up, room, later = out

    def make(s, keys_in):
        done = {}

        def get(s):
            key = json.dumps(s, sort_keys=True)
            if key not in done:
                done[key] = fx_notes(s, ppq, keys_in, get)
            return done[key]
        return get(s)
    with rows_up_to(room - 1):
        notes, tracks = make(lifted, room)
    notes = np.array(notes, copy=True)
    if up:
        notes[:, 2] -= up
    if later:
        notes[:, :2] -= round(later * ppq)
    return notes, tracks


SPACE_KEYS = [False]  # Settings → General "Space past the keys" on (App.space_changed): a shape crossing the top key
# is made whole then cut too; off = cut at the top key first, as before this setting (user: off = as before)


def edge_made(sh, ppq, keys, make):
    """sh's (notes, tracks) when it reaches past the edges (past_edges): made lifted (make(shape, keys)), with fills
    making their rows that high (paths.rows_up_to), then put back and cut (cut_back). None: made as always. The one
    way for App.notes_tracks, its background thread and shape_notes_tracks (hunt 2026-11-06: they drifted)."""
    out = past_edges(sh, keys, ppq)
    if out is None:
        return None
    lifted, up, room, later = out
    with rows_up_to(room - 1):
        got = make(lifted, room)
    return cut_back(*got, up, keys, round(later * ppq))


LATER_STEP = 12  # beats: a shape before beat 0 is made this many at a time later (whole bars of 4/4, 3/4, 6/8 and
#                  every usual grid step fit in, so its spam grid / Chop's song grid land the same; past_edges)
LATER_MOST = 10 ** 8  # ticks: a shape's own grids needing a longer shift than this: LATER_STEP alone


def later_unit(sh, ppq):
    """Beats (a Fraction) the time shift of a shape before beat 0 comes in: LATER_STEP and every grid the shape
    counts from the song's start (Spam "Aligned" gate, Colours By time, Chop's song grid), so they land the same
    (hunt 2026-11-06: a 7-tick aligned gate went off the grid)."""
    from fractions import Fraction
    steps = [Fraction(LATER_STEP)]
    try:
        if sh.get("kind") == "custom" and sh.get("align") == "aligned" and isinstance(sh.get("gate"), (int, float)):
            steps.append(Fraction(max(1, round(sh["gate"] * ppq)), ppq))
        c = sh.get("cycle")
        if isinstance(c, dict) and c.get("by") == "time" and isinstance(c.get("every"), (list, tuple)):
            a, b = c["every"][:2]
            steps.append(Fraction(4 * Fraction(a).limit_denominator(1000), Fraction(b).limit_denominator(1000)))
        for page in sh.get("fx") or ():
            cfg = page.get("cfg") or {} if isinstance(page, dict) else {}
            if page.get("tool") == "chop" and cfg.get("abs") and isinstance(cfg.get("len"), (int, float)):
                steps.append(Fraction(cfg["len"]).limit_denominator(ppq))
    except (TypeError, ValueError, ZeroDivisionError, AttributeError):
        return Fraction(LATER_STEP)
    unit = steps[0]
    for s in steps[1:]:
        if s <= 0:
            continue
        num = unit.numerator * s.numerator // math.gcd(unit.numerator, s.numerator)
        unit = Fraction(num, math.gcd(unit.denominator, s.denominator))
        if unit * ppq > LATER_MOST:
            return Fraction(LATER_STEP)
    return unit


def cut_back(notes, tracks, up, keys, later=0):
    """past_edges' notes (made lifted by up keys and later ticks later) put back where the shape is, cut at the keys
    and beat 0: what reaches past them is left out, a note across beat 0 starts on it (keeping its velocity)."""
    notes = np.array(notes, copy=True)
    if up:
        notes[:, 2] -= up
    if later:
        notes[:, :2] -= later
    keep = (notes[:, 2] >= 0) & (notes[:, 2] < keys) & (notes[:, 1] > 0)
    notes = notes[keep]
    notes[:, 0] = np.maximum(notes[:, 0], 0)
    return notes, None if tracks is None else np.asarray(tracks)[keep]


def off_part(notes, tracks, up, keys, later=0):
    """What cut_back leaves out of the same lifted notes, put back where the shape is: notes under key 0, over the
    top key or wholly before beat 0, and the part before beat 0 of a note across it (ending on beat 0). The
    "Keep notes made there" notes (App.offrange_tracks); never part of the shape's real notes."""
    notes = np.array(notes, copy=True)
    if up:
        notes[:, 2] -= up
    if later:
        notes[:, :2] -= later
    out = (notes[:, 2] < 0) | (notes[:, 2] >= keys) | (notes[:, 0] < 0)
    notes = notes[out]
    inside = (notes[:, 2] >= 0) & (notes[:, 2] < keys)  # (in the keys: only its part before beat 0 is off)
    notes[inside, 1] = np.minimum(notes[inside, 1], 0)
    return notes, None if tracks is None else np.asarray(tracks)[out]


def past_edges(sh, keys, ppq=960, over_top=None):
    """A shape whose lines reach before beat 0, under key 0 or over the top key (Space past the edges, Settings →
    General, user 2026-11-06: no notes are made there): (it lifted onto the keys by up whole keys and not cut at the
    edges (_free) and moved later by whole later_units past beat 0 (+ one more: note tools reaching earlier), up,
    the keys to make it on, beats later). Its notes are made WHOLE (velocities by their real starts, note tool pages,
    Sustain, overlaps as if nothing was cut; tools never see a time before 0, where some stop) and only then put back
    and cut where it really is (edge_made / cut_back). A sliced piece by its WHOLE's reach (moved along with it):
    lifted by its points, sliced.made_at_cut then makes it where it was cut (also lifted) and moves it. A Glissando
    goes up by whole octaves, its picked keys along (real piano keys). Over the top key alone only with Space past
    the keys on (SPACE_KEYS; off: as before; over_top True / False overrides it). None: made as always (inside the
    keys, a Hz bass: its tones sit on song beats, Recolour's uncut look)."""
    if over_top is None:
        over_top = SPACE_KEYS[0]
    if sh.get("_free") or sh.get("hz") or "pts" not in sh:
        return None
    try:
        r = cached_reach(sh)
        if sh.get("cut"):  # (a piece: its notes come from the whole's)
            d = moved_by(sh)
            w = cached_reach(moved(sh["cut"]["whole"], d)) if d is not None and sh["cut"].get("whole") else None
            if r is not None and w is not None:
                r = (min(r[0], w[0]), min(r[1], w[1]), max(r[2], w[2]), max(r[3], w[3]))
    except Exception:  # (a shape too broken to measure: made as always)
        return None
    if r is None or r[0] >= 0 and r[1] >= -0.5 and (r[2] <= keys - 0.5 or not over_top):
        return None
    up = max(0, math.ceil(-0.5 - float(r[1]) - 1e-9))
    gliss = sh.get("kind") == "gliss"
    if gliss and up:
        up = -(-up // 12) * 12
    room = max(keys, math.ceil(float(r[2]) + up + 0.5 + 1e-9) + 1)
    if r[0] < 0:
        unit = later_unit(sh, ppq)
        later = float((math.ceil(-float(r[0]) / unit - 1e-9) + 1) * unit)
    else:
        later = 0.0
    lifted = dict(sh, pts=[[b + later, p + up] for b, p in sh["pts"]]) if up or later else dict(sh)
    if gliss and up and isinstance(sh.get("gliss"), dict) and sh["gliss"].get("keys"):
        lifted["gliss"] = dict(sh["gliss"], keys=[k + up for k in sh["gliss"]["keys"]])
    lifted["_free"] = True
    if later:  # (where beat 0 is in it: a note across it reads its velocity there, where it shows: from0_starts)
        lifted["_from0"] = later
    return lifted, up, room, later


def from0_starts(sh, notes, starts, ppq):
    """Where notes (start, end, ... rows) read their velocity: at starts, except a note across beat 0 of a shape made
    later (past_edges "_from0", user 2026-11-08): there, where it shows. Notes wholly before beat 0 keep their own."""
    f = sh.get("_from0")
    if not f or not len(notes):
        return starts
    t0 = round(f * ppq)
    return np.where((starts < t0) & (notes[:, 1] > t0), t0, starts)


def made_from(sh):
    """sh as its notes are made from: a Hz bass without its CC lines (hz_settings.without_cc: they make no notes,
    they're MIDI messages, hz_cc.py). Own channel per note stays only while it has some (own_channels), as
    {"fall": how long its lines go on after a note, "ring": their run-on} (hz_cc.voices: a voice is busy until its
    lines end, so the notes and the CC messages take the same channels). The run-on (hz["ring"]) makes no notes
    either, nor finer gates (hz["fine"]: the export's, hz_fine.py). The same again for what it gives back.
    Liquify's freeze mask makes no notes either (it only guides the brush: liquify.py), so freezing makes nothing
    again."""
    rc = sh.get("recolour")
    if isinstance(rc, dict) and isinstance(rc.get("liquify"), dict) and "frozen" in rc["liquify"]:
        from notes.recolour import clean_recolour, without_recolour
        rc = dict(rc, liquify={k: v for k, v in rc["liquify"].items() if k != "frozen"})
        # (only a mask: no Recolour at all, the very key of the shape without it)
        sh = dict(sh, recolour=rc) if clean_recolour(rc) else without_recolour(sh)
    hz = sh.get("hz")
    if not isinstance(hz, dict):
        return sh
    got = without_cc(hz)
    if "ring" in got or "fine" in got:
        got = {k: v for k, v in got.items() if k not in ("ring", "fine")}
    if hz.get("cc_own") is True:
        got = dict(got, cc_own={"fall": cc_fall(hz), "ring": ring_of(hz)}) if own_channels(hz) else {
            k: v for k, v in got.items() if k != "cc_own"}
    return sh if got is hz else dict(sh, hz=got)


def one_voice(sh):
    """sh with every note on the Hz bass's one channel (Own channel per note left out: outside Multi channel the CC
    goes out as one channel, so the notes are made as for one)."""
    hz = sh.get("hz")
    if not isinstance(hz, dict) or not hz.get("cc_own"):
        return sh
    return dict(sh, hz={k: v for k, v in hz.items() if k != "cc_own"})


BIG_TEXT = 100_000  # characters: a text this long (pasted / image notes, packed) is kept apart by shape_key


def shape_key(sh):
    """A shape as a key of the notes it makes (equal for exactly the same shape): its JSON text, with big texts
    (pasted / image notes, many MB) kept as the strings themselves, so they're never written out again: comparing
    the same string is instant (App.notes_key is asked at every rest of a drag). A Hz bass with another layer
    picked is the same shape (the same notes: picking a layer makes nothing again), and so is one with other CC
    lines (made_from), and one with another Channel per strip (strips.py: only channels change, App.notes_key keeps
    it; hunt 2026-10-23: each gap tried kept another copy of a 2 M-note paste)."""
    sh = made_from(sh)
    if "strips" in sh:
        sh = {k: v for k, v in sh.items() if k != "strips"}
    if (sh.get("hz") or {}).get("layer"):
        sh = dict(sh, hz=with_layers(sh["hz"], layers_of(sh["hz"]), 0))
    texts = []
    out = without_big(sh, texts)
    return json.dumps(out, sort_keys=True) if not texts else (json.dumps(out, sort_keys=True), tuple(texts))


BIG_MARK = "\0big:"  # (a big text's place in shape_key's JSON: no saved text holds this character)


def without_big(sh, texts):
    """sh with every big text (BIG_TEXT; also inside a Gate sensitive merge's shapes, merges of merges too: a big
    paste merged was copied out again at every change) swapped for a mark, the texts added to texts in order."""
    out = {}
    for k, v in sh.items():
        if isinstance(v, str) and len(v) > BIG_TEXT:
            out[k] = BIG_MARK + str(len(texts))
            texts.append(v)
        elif k in ("merge", "unmerge") and isinstance(v, dict) and isinstance(v.get("parts"), list):
            out[k] = dict(v, parts=[without_big(p, texts) if isinstance(p, dict) else p for p in v["parts"]])
        else:
            out[k] = v
    return out


def with_big(sh, texts):
    """without_big's sh with the very texts put back (changes sh)."""
    for k, v in sh.items():
        if isinstance(v, str) and v.startswith(BIG_MARK):
            sh[k] = texts[int(v[len(BIG_MARK):])]
        elif k in ("merge", "unmerge") and isinstance(v, dict) and isinstance(v.get("parts"), list):
            for p in v["parts"]:
                if isinstance(p, dict):
                    with_big(p, texts)
    return sh


def from_key(key):
    """A copy of the shape shape_key was made from (big texts shared: a string never changes)."""
    return json.loads(key) if isinstance(key, str) else with_big(json.loads(key[0]), key[1])


def pasted_notes(sh):
    """Notes pasted from Domino (or pieces of them), or a plain copy made from one laid on tracks (Fill gaps, Remove
    Track: sh["from_paste"], hunt 2026-10-29): not made by Spiderweb (Turn into plain notes, Apply mask, merges,
    pictures...)."""
    if sh.get("from_paste") is True:
        return True
    return "notes" in sh and not sh.get("made") and "merge" not in sh and "picture" not in sh and \
        "picture_notes" not in sh


def heard_way(sh, mode, rule=None):
    """How the Channels mode fixes a notes shape's OWN overlaps on each of its tracks ("stretch" / "split"), or None
    when it keeps them: Single channel; Multi for notes Spiderweb made, or any with Remove overlaps; never As drawn,
    Sustain, held notes, pictures, masks. The notes are made so (sh["_heard"], heard_notes) before every tool works
    on them (user 2026-11-08: Glue, note tool pages, Recolour, flips work on the notes AS SHOWN; a long quiet note
    under a loud one shows loud to the long note's end in Single channel)."""
    if (mode == "raw" or "notes" not in sh or "picture" in sh or "picture_notes" in sh or is_mask(sh)
            or sh.get("held") is True or sustain_on(sh) or sh.get("_sus") is True):
        return None
    rule = rule or {}
    if mode == "auto" and keeps_laps(sh) and not rule.get("across"):
        return None
    return rule.get("way") or "stretch"


def keeps_laps(sh):
    """Notes keeping their own overlaps in Multi channel (without Remove overlaps): a Domino paste (pasted_notes), or
    notes made from one by Apply mask / Gate sensitive merge / Fill gaps (sh["paste_laps"]; overlap hunt 2026-11-08,
    user took A: Multi cut the paste's overlaps once copied, so Apply mask didn't sound like its preview)."""
    return pasted_notes(sh) or sh.get("paste_laps") is True


_FREE = {}  # id(a big stored notes text) -> (it, True): its notes have no overlaps of their own (heard_notes)
OVERLAP_GROUPS_MOST = 1 << 24  # (any_overlap's tables: tracks x keys; more = the sort below)


def self_overlaps(n, who):
    """Do two of these notes (start, end, key... rows) on the same track (who: 0, 1, 2...) and key overlap?
    The compiled loop does it in one pass when each track + key's notes come in start order (a paste mostly
    does; 5 M notes 290 -> ~30 ms); else a sort and each column gathered once (six gathers took 219 of 728 ms)."""
    fast = loops()
    if fast and len(n) > 1:
        k = n[:, 2]
        lo, hi = int(k.min()), int(k.max())
        nk = hi - lo + 1
        groups = (int(who.max()) + 1) * nk
        rows = n
        b = n.base
        if not n.flags.c_contiguous and isinstance(b, np.ndarray) and b.flags.c_contiguous and b.ndim == 2 and \
                b.dtype == np.int64 and len(b) == len(n) and b.ctypes.data == n.ctypes.data and \
                n.strides == b.strides:
            rows = b  # (a paste's notes come as the first columns of a wider array: start / end at the same places)
        if groups <= OVERLAP_GROUPS_MOST and rows.flags.c_contiguous:
            got = fast.any_overlap(rows, who * nk + (k - lo), groups)
            if got in (0, 1):
                return bool(got)
    o = key_sort(who * 256 + n[:, 2], n[:, 0])
    x = who[o]
    over = x[1:] == x[:-1]
    x = n[o, 2]
    over &= x[1:] == x[:-1]
    del x
    s = n[o, 0]
    over &= s[1:] < n[o[:-1], 1]
    return bool(over.any())


def heard_notes(sh, notes, tracks, ppq=None):
    """A notes shape's notes with its own overlaps on each track fixed as sh["_heard"] says (heard_way; Single's
    rule: the later start wins, cut + stretch or split around). None / no overlaps: as they are. A big pasted text
    found without overlaps is remembered (moved / stretched / cut copies of it never get any)."""
    way = sh.get("_heard")
    if not way or len(notes) < 2 or np.ndim(notes) != 2 or np.shape(notes)[1] != 4:
        return notes, tracks
    text = sh.get("notes")
    big = isinstance(text, str) and len(text) > 10_000
    if big:
        got = _FREE.get(id(text))
        if got is not None and got[0] is text:
            return notes, tracks
    n = np.asarray(notes, np.int64)
    ids, who = (None, np.zeros(len(n), np.int64)) if tracks is None else \
        unique_inverse(np.asarray(tracks, np.int64).ravel())
    if not self_overlaps(n, who):
        if big:
            if len(_FREE) > 64:
                _FREE.clear()
            _FREE[id(text)] = (text, True)
        return notes, tracks
    rows = np.zeros((len(n), 6), np.int64)
    rows[:, :4], rows[:, 4] = n[:, :4], who
    rows[:, 5] = np.arange(len(n))  # (each note's row, ridden along: _read_at_shown)
    del who  # (in rows now: 40 MB less at 5 M notes while they're fixed)
    pts = sh.get("pts")
    back = bool(pts) and pts[1][0] < pts[0][0]
    if back:  # (flipped sideways: fixed as the unflipped notes are, then mirrored back = the exact mirror of what showed
        hi = int(n[:, 1].max())  # unflipped; user took B 2026-11-08, the overlaps stay stored; mirrored within 0..hi:
        rows[:, 0], rows[:, 1] = hi - n[:, 1], hi - n[:, 0]  # the compiled loops want no ticks below 0)
    low = min(0, int(rows[:, 0].min()))  # (notes before beat 0: moved up meanwhile, for the same reason)
    if low:
        rows[:, :2] -= low
    rows = split_overlaps(rows) if way == "split" else resolve_overlaps(rows)
    if low:
        rows[:, :2] += low
    if back:
        rows[:, 0], rows[:, 1] = hi - rows[:, 1], hi - rows[:, 0]
        # (READ WHERE THE NOTE SHOWS, user 2026-11-12: mirrored back, a note's start moves (stretched to an earlier
        # note's end, or cut); a shape reading its own line reads it at that shown start, as with Gate subtract;
        # case 392786383)
        if ppq:
            rows = _read_at_shown(sh, rows, n, ppq)
    out = np.ascontiguousarray(rows[:, :4])
    if tracks is None:
        return out, None
    return out, ids[rows[:, 4]]


def _read_at_shown(sh, rows, n, ppq):
    """heard_notes' fixed rows (column 5 = each one's row in n) of a sideways-flipped shape reading its own velocity
    line: a note whose start moved reads the line at its shown start. Only when the line read at the unmoved starts
    gives exactly their velocities (the reading the notes were made with); else as they are."""
    if not reads_line(_unglued_view(sh)):  # (glue runs after: its notes read the line as these do)
        return rows
    env = velocity_env(sh)
    if len({v for _, v in env}) == 1:
        return rows
    path = dedupe(np.concatenate(cached_arrays(sh)))[:, 0] * ppq
    t_lo, t_hi = float(path.min()), float(path.max())
    moved = rows[:, 0] != np.asarray(n)[rows[:, 5], 0]
    if not moved.any():
        return rows
    got = env_velocities(env, from0_starts(sh, rows, rows[:, 0], ppq), t_lo, t_hi)
    same = ~moved & (rows[:, 3] == np.asarray(n)[rows[:, 5], 3])  # (not merged with a louder one)
    if not (got[same] == rows[same, 3]).all():
        return rows
    rows = rows.copy()
    rows[moved, 3] = got[moved]
    return rows


PASTE_KEPT_MOST = 4  # (pasted texts whose repeat check / own-overlap fix are kept: _PASTE_KEPT)
PASTE_KEPT_LEAST = 100_000  # (fewer notes: done each time, it's quick)
_PASTE_KEPT = {}  # packed notes text -> {"stats", "keep", way: the fixed notes as pasted (unmoved) or True: none}


def paste_moved(sh, ppq, keys):
    """A plain pasted-notes shape only MOVED since pasted, none of its notes past the piano roll's edges:
    (text, rows, ticks, keys shifted, its kept work), else None. Its notes = its packed rows shifted, every row
    by the same amount (custom.block_shift): repeats and its own overlaps are the very same as unmoved, moved."""
    text = sh.get("notes")
    if (sh["kind"] != "custom" or not isinstance(text, str) or len(text) < 10_000 or not sh.get("own_vel")
            or sh.get("cut") or "merge" in sh or "picture" in sh or sh.get("lapped") or sh.get("_free")
            or sh.get("hz") or sh.get("cycle")):
        return None
    rows = unpack_notes(text)
    if len(rows) < PASTE_KEPT_LEAST:
        return None
    got = block_shift(sh, ppq, rows)
    if got is None:
        return None
    T0, K0 = got
    kept = _PASTE_KEPT.get(text)
    if kept is None:
        if len(_PASTE_KEPT) >= PASTE_KEPT_MOST:
            _PASTE_KEPT.clear()
        kept = _PASTE_KEPT[text] = {"stats": (int(rows[:, 0].min()), int(rows[:, 1].min()), int(rows[:, 2].min()),
                                              int(rows[:, 2].max()))}
    s0, e0, k0, k1 = kept["stats"]
    if s0 + T0 < 0 or e0 + T0 <= 0 or k0 + K0 < 0 or k1 + K0 >= keys:  # (cut / clipped at an edge: the long way)
        return None
    return text, rows, T0, K0, kept


def moved_rows(rows, keep, T0, K0, cols=5):
    """rows (start, end, key, velocity[, track]) at keep (None: all) shifted by T0 ticks, K0 keys -> a new int64
    array, made column by column (no whole-array copies on the way)."""
    n = len(rows) if keep is None else len(keep)
    out = np.empty((n, cols), np.int64)
    for c, d in zip(range(cols), (T0, T0, K0, 0, 0)):
        col = rows[:, c] if keep is None else rows[keep, c]
        if d:
            np.add(col, d, out=out[:, c], dtype=np.int64)  # (worked out in 64 bits: kept rows may be 32-bit)
        else:
            out[:, c] = col
    return out


def paste_keep(sh, rows, kept):
    """The rows a pasted shape keeps (unique_rows; an Extract keeps its doubles): None = all."""
    if sh.get("extract"):
        return None
    if "keep" not in kept:
        keep = unique_index(rows)
        kept["keep"] = None if len(keep) == len(rows) else keep
    return kept["keep"]


def moved_paste(sh, ppq, keys):
    """kind_notes_tracks of a pasted shape only moved (paste_moved), or None: exactly what the long way gives
    (block_notes, the edge cut, unique_rows), without its copies (a 5 M-note move: 0.46 s, +577 MB before)."""
    got = paste_moved(sh, ppq, keys)
    if got is None:
        return None
    text, rows, T0, K0, kept = got
    out = moved_rows(rows, paste_keep(sh, rows, kept), T0, K0)
    return out[:, :4], out[:, 4]


def heard_kind(sh, ppq, keys):
    """heard_notes(sh, *kind_notes_tracks(...)). A pasted shape only moved: its own overlaps fixed ONCE where it was
    pasted and kept, then shifted (fixing commutes with moving every note by the same ticks and keys: the same
    notes, test overlap_lean)."""
    way = sh.get("_heard")
    got = paste_moved(sh, ppq, keys) if way else None
    if got is None:
        return heard_notes(sh, *kind_notes_tracks(sh, ppq, keys), ppq)
    text, rows, T0, K0, kept = got
    keep = paste_keep(sh, rows, kept)
    fixed = kept.get(("heard", way, keep is None))
    if fixed is None:
        base = moved_rows(rows, keep, 0, 0)
        n, tr = heard_notes(sh, base[:, :4], base[:, 4])
        if n.base is base or n is base:  # (no overlaps of its own: nothing to fix)
            fixed = True
        else:
            small = n.min() >= -(1 << 31) and n.max() < 1 << 31
            fixed = (n.astype(np.int32) if small else n, np.asarray(tr).astype(np.int32))
        kept[("heard", way, keep is None)] = fixed
    if fixed is True:
        out = moved_rows(rows, keep, T0, K0)
        return out[:, :4], out[:, 4]
    n, tr = fixed
    return moved_rows(n, None, T0, K0, 4), tr.astype(np.int64)


def colour_split(sh):
    """recolour.page_split for shape sh (a Recolour on): its colour steps that run before one of its note tool pages,
    or None (all after them: no "page" in its saved order, or every page entry first)."""
    r = sh["recolour"]
    o = r.get("order") if isinstance(r, dict) else None
    if not isinstance(o, list) or PAGE not in o:
        return None
    n = len(pages(sh.get("fx")))
    rc = clean_recolour(r) if n else None
    if rc is None:
        return None
    return page_split(dict(rc, **{k: r[k] for k in ("_upto", "most") if k in r}), n)


def pages_coloured(sh, inner, keep):
    """sh (without its Recolour) with a {"tool": "colours", "rc": ...} step in front of each page j of inner (the
    colour steps running right before it: fx_notes / colour_step) and only its first keep pages (the ones after the
    colour step being edited skipped, with the steps after them)."""
    out, j = [], 0
    for st in sh.get("fx") or ():
        if is_page(st):
            if j >= keep:
                break
            if j in inner:
                out.append({"tool": "colours", "rc": inner[j]})
            j += 1
        out.append(st)
    sh = {k: v for k, v in sh.items() if k != "fx"}
    if out:
        sh["fx"] = out
    return sh


def colour_step(notes, tracks, rc, ppq):
    """The notes after a "colours" step (pages_coloured): their colours only, as Recolour rc makes them (the notes as
    they are then, never settled: the page after it gets the very same notes, only coloured)."""
    rule = rule_of(rc)
    if rule is None or not len(notes):
        return notes, tracks
    if rule.get("stir"):
        rule["ppq"] = ppq
    return recoloured(notes, tracks, rule, True, False, None)


DEEP = 50  # steps: more, and fx_notes makes the notes before each one first (see there)
_warming = threading.local()


def fx_notes(sh, ppq, keys, get):
    """shape_notes_tracks' work: the notes, then its glue, then its note tool pages one after another (fx.py).
    get(shape) = another shape's notes and tracks (remembered by the caller: the steps before the last one, the
    notes flipped back). A hidden "_m" = the notes made flipped along those axes (the steps before a flip)."""
    sh = made_from(sh)  # (a Hz bass's CC lines make no notes: never from them, whoever asks)
    run = expanded(sh)  # (an order of its own / steps switched off: every step one entry, in that order: steps.py)
    if run is not None:
        return get(run)
    if "recolour" in sh:  # (colours only, on the notes as they come out at the very end: recolour.py)
        if not is_recoloured(sh):
            return get(without_recolour(sh))
        # (held: overlaps kept, not settled; a Domino paste keeps its own overlaps too, as with Recolour off
        # (Multi channel, user 2026-10-30; Single channel cuts them on its track afterwards anyway)
        held_ = sustain_on(sh) or sh.get("held") is True or keeps_laps(sh) or sh.get("_sus") is True
        gate = is_pasted_flipped(sh) or is_gate_flipped(sh) or sh.get("_gate") is True
        rule = rule_of(sh["recolour"])
        if rule.get("stir"):  # (a stir round as seen counts beats: recolour.area_scales)
            rule["ppq"] = ppq
        split = colour_split(sh)  # (colour steps before a note tool page, PLAN.md stage F; None = all after them)
        # (Stir, and Per chord / run (a run cut in two got a new first note, hunt 11-07): a piece looks its colours up
        # in the whole)
        if ((rule.get("stir") or rule.get("liquify") or rule.get("warp") or rule.get("by") and rule["mode"] != KEEP)
                and (sh.get("cut") or sh.get("colour_cut"))
                and not (rule.get("frame") or (1, 0))[1]):  # (turned: notes made anew)
            notes, tracks = get(without_recolour(sh))
            got = stirred_piece(sh, notes, tracks, held_, get, gate, rule)
            if got is not None:
                return got
        if split is None:
            notes, tracks = get(without_recolour(sh))
        else:  # (each part of the colour steps in front of its page; the rest at the end as always)
            inner, final, keep = split
            notes, tracks = get(pages_coloured(without_recolour(sh), inner, keep))
            if final is None:
                return notes, tracks
            rule = rule_of(final)
            if rule.get("stir"):
                rule["ppq"] = ppq
        return recoloured(notes, tracks, rule, held_, gate, free_box(sh, notes, get, keys))
    if "subtract" in sh:  # (gates made shorter at the very end, after Sustain too: subtract.py)
        notes, tracks = get({k: v for k, v in sh.items() if k != "subtract"})
        if not is_subtracted(sh):
            return notes, tracks
        return stepped_piece(sh, "subtract", notes, tracks, ppq, get) or (
            shown_subtracted(notes, sh["subtract"], sh, ppq), tracks)
    if "extend" in sh:  # (gates made longer after Sustain, before Gate subtract: extend.py)
        notes, tracks = get({k: v for k, v in sh.items() if k != "extend"})
        if not is_extended(sh):
            return notes, tracks
        return stepped_piece(sh, "extend", notes, tracks, ppq, get) or (extended(notes, sh["extend"], ppq), tracks)
    if "sustain" in sh:  # (held on last, after everything else: sustain.py)
        bare = {k: v for k, v in sh.items() if k != "sustain"}
        if not is_sustained(sh):
            return get(bare)
        # (its own overlaps kept under the hold, as heard_way says: "_sus" keeps the notes below from being fixed as
        # heard; sweep 55660070, a sustained paste in Single had them fixed first, and a velocity line missed)
        notes, tracks = get(dict(bare, _sus=True))
        got = held_piece(sh, notes, tracks, ppq, get)
        return got if got is not None else held(notes, tracks, auto_sustain(sh, get), ppq)
    fx = sh.get("fx")
    if "picture" in sh and not (fx and fx[-1]["tool"] in RUNNER):
        return kind_notes_tracks(sh, ppq, keys)  # (no glue / chop / claw / strum on a picture: its notes are its picture, user)
    if fx and (is_pasted_flipped(sh) or is_gate_flipped(sh)):
        # (note tool pages first, then each key row reversed: a Claw's spacing turns round too, user 2026-11-05)
        # (velocity lines drawn after the last page come AFTER the flip: they were drawn on the notes as shown, so
        # they change only the notes under them there; before it, the flip carried them onto other notes, partial
        # sweep 11-05)
        # (glue places after the last page too: a box is drawn on the notes as shown, so it glues them after the flip,
        # like a shape with no page; before it, it glued other notes and moved other rows' runs, hunt 2026-11-07)
        k = len(fx)
        while k and fx[k - 1]["tool"] in ("vel", "glue"):
            k -= 1
        base = {key: v for key, v in sh.items() if key not in ("gate_flip", "fx", "glue")}
        if k:
            base["fx"] = fx[:k]
        first = glue_from(sh.get("glue"), fx, k) if sh.get("glue") else None  # (boxes glued before the held places)
        if first and pages(fx[:k]):
            base["glue"] = first
            first = None
        notes, tracks = get(base)
        notes, tracks = mirrored_runs(notes, tracks, bool(sh.get("own_vel")))
        if first:  # (no page before: glued after the flip, as with no step at all)
            notes, tracks = with_glue(notes, tracks, dict(sh, glue=first), ppq)
        for j in range(k, len(fx)):
            step = fx[j]
            if step["tool"] == "glue":
                g = glue_level(sh, j + 1)
                notes, tracks = with_glue(notes, tracks, dict(g, glue=at_step(g.get("glue"), step)), ppq)
            else:
                notes, tracks = fx_step(notes, tracks, step, sh, ppq, keys)
        return notes, tracks
    if fx:
        if len(fx) > DEEP and not getattr(_warming, "on", False):
            # (a long list: the notes before each step made first, from the first step up, so making them never goes
            # a step deeper than one: hundreds of steps went past Python's limit)
            chain, s = [], sh
            while s.get("fx"):
                s = before_step(s)[0]
                chain.append(s)
            _warming.on = True
            try:
                for s in reversed(chain):
                    get(s)
            finally:
                _warming.on = False
        rest, last = before_step(sh)
        notes, tracks = get(rest)
        if last is None:
            return notes, tracks
        if last["tool"] == "flip":  # (flipped within their own box: they stay where they were)
            return mirrored(notes, [last["axis"]], notes_box(notes)), tracks
        if last["tool"] == "turn":  # (the steps before it: on the drawing turned back; turned within their own box)
            same = groups(np.column_stack([notes[:, 3:], tracks if tracks is not None else np.zeros(len(notes))]))
            notes, idx = turn_notes(notes, last["deg"], last["r"] * ppq, keys, same)
            return notes, None if tracks is None else np.asarray(tracks)[idx]
        if last["tool"] == "glue":
            g = glue_level(sh)
            return with_glue(notes, tracks, dict(g, glue=at_step(g.get("glue"), last)), ppq)
        if last["tool"] == "colours":  # (colour steps in front of a page: pages_coloured; or anywhere: steps.expanded)
            if last.get("full"):  # (no page after them: coloured as the Recolour at the end colours)
                rule = rule_of(last["rc"])
                if rule.get("stir"):
                    rule["ppq"] = ppq
                held_ = sh.get("_sus") is True or sh.get("held") is True or keeps_laps(sh)
                return recoloured(notes, tracks, rule, held_, bool(last.get("gate")), free_box(rest, notes, get, keys))
            return colour_step(notes, tracks, last["rc"], ppq)
        if last["tool"] in RUNNER:
            return run_step(notes, tracks, last, rest, ppq, get)
        return fx_step(notes, tracks, last, sh, ppq, keys)
    if sh.get("_m"):
        if sh.get("cut"):  # (a sliced piece: its notes as it was, before the flips; sliced.steps_kept)
            return get(unflipped(sh))
        notes, tracks = get(bare_of(sh))
        return mirrored(notes, sh["_m"], notes_box(notes)), tracks
    if is_pasted_flipped(sh) or is_gate_flipped(sh):  # (each run on a key mirrored inside itself, gateflip.py)
        notes, tracks = get({k: v for k, v in sh.items() if k not in ("gate_flip", "glue")})
        got = stepped_piece(sh, "gate_flip", notes, tracks, ppq, get) if is_gate_flipped(sh) else None
        if got is None:
            got = mirrored_runs(notes, tracks, bool(sh.get("own_vel")))
            if not sh.get("own_vel"):  # (READ WHERE THE NOTE SHOWS, user 2026-11-12, A: a shape reading its own line
                # reads it at each note's start after the flip; with the partner's velocity (uneven notes don't land
                # where their partner started) a line set notes outside it, sweep 392761239)
                got = (line_at_shown({k: v for k, v in sh.items() if k != "gate_flip"}, got[0], ppq), got[1])
        return with_glue(*got, sh, ppq)
    # (a notes shape's own overlaps fixed as the Channels mode shows them, before glue and every tool: heard_way)
    if sh.get("_noglue0"):  # (its glue before the pages runs later, or is switched off: steps.expanded)
        return heard_kind(sh, ppq, keys)
    return with_glue(*heard_kind(sh, ppq, keys), sh, ppq)


def shown_subtracted(notes, sub, sh, ppq):
    """subtracted(), and a shape whose notes read its own velocity line: a note Gate subtract's start side moved reads
    it where it shows now (user 2026-11-12: a line drawn over a note's shown start sets it; old projects may change a
    few velocities). Pages, glue, own velocities, pieces, Gate aware flip, Hz bass: as before (velocities made there)."""
    out = subtracted(notes, sub, ppq)
    if not len(out) or not side_used(sub.get("start")) or not reads_line(sh):
        return out
    moved = out[:, 0] != np.asarray(notes)[:, 0]
    if not moved.any():
        return out
    env = velocity_env(sh)
    if len({v for _, v in env}) == 1:
        return out
    path = dedupe(np.concatenate(cached_arrays(sh)))[:, 0] * ppq
    t_lo, t_hi = float(path.min()), float(path.max())
    if grows_free(sh):
        t_lo, t_hi = free_span(sh, ppq) or (t_lo, t_hi)
    out = out.copy()
    out[moved, 3] = env_velocities(env, from0_starts(sh, out[moved], out[moved, 0], ppq), t_lo, t_hi)
    return out


def reads_line(sh):
    """Do sh's notes take their velocities straight off its velocity line, read at each note's start (shown_subtracted)?"""
    # (glue that runs AFTER Gate subtract (the step order: "_noglue0", no "glue0" before it) glues the notes as read
    # here: adding it left the velocities read at the old starts, sweep 75545074)
    glued_first = sh.get("glue") and not (sh.get("_noglue0") and not any(
        s.get("tool") == "glue0" for s in sh.get("fx") or ()))
    if (sh.get("own_vel") or sh.get("merge") or sh.get("cut") or glued_first or sh.get("gate_flip") or sh.get("hz")
            or "picture" in sh or sh["kind"] in HZ_KINDS and (sh.get("hz") or {}).get("tones")):
        return False
    return all(s.get("tool") in ("sustain", "extend", "subtract", "colours") for s in sh.get("fx") or ())


def _unglued_view(sh):
    """sh without its glue (for reads_line: glue that runs AFTER a step doesn't change where that step's notes read
    their line; a glue added made them read it the old way again, sweep 106580335)."""
    if not sh.get("glue") and not any(s.get("tool") in ("glue", "glue0") for s in sh.get("fx") or ()):
        return sh
    out = {k: v for k, v in sh.items() if k not in ("glue", "_noglue0")}
    fx = [s for s in sh.get("fx") or () if s.get("tool") not in ("glue", "glue0")]
    out.pop("fx", None)
    if fx:
        out["fx"] = fx
    return out


def line_at_shown(sh, out, ppq):
    """Gate aware flip's mirrored notes out of a shape reading its own velocity line (reads_line; sh without the
    flip): each note's velocity read off the line where it shows now (READ WHERE THE NOTE SHOWS, user 2026-11-12).
    Anything else: out as it is."""
    if not len(out) or not reads_line(_unglued_view(sh)):  # (glue runs after the flip)
        return out
    env = velocity_env(sh)
    if len({v for _, v in env}) == 1:
        return out
    path = dedupe(np.concatenate(cached_arrays(sh)))[:, 0] * ppq
    t_lo, t_hi = float(path.min()), float(path.max())
    if grows_free(sh):
        t_lo, t_hi = free_span(sh, ppq) or (t_lo, t_hi)
    out = out.copy()
    out[:, 3] = env_velocities(env, from0_starts(sh, out, out[:, 0], ppq), t_lo, t_hi)
    return out


def run_step(notes, tracks, step, sh, ppq, get):
    """The notes after one of the runner's own steps (steps.RUNNER; sh: the shape as it is before it, its notes
    notes / tracks): glue before the pages, Gate aware flip, Sustain, Gate extend, Gate subtract."""
    tool = step["tool"]
    if tool == "glue0":  # (the boxes glued before the pages, on the drawing as it is here: glue_level's way)
        boxes = glue_from(sh.get("glue"), sh.get("fx"))
        if not boxes:
            return notes, tracks
        g = unflipped(sh) if sh.get("_m") else sh
        return with_glue(notes, tracks, dict(g, glue=boxes), ppq)
    if tool == "gate_flip":
        out, tr_ = mirrored_runs(notes, tracks, bool(sh.get("own_vel")))
        # (READ WHERE THE NOTE SHOWS, user 2026-11-12, A: a shape reading its own line reads it at each note's start
        # after the flip; sweep 492773031: Gate extend first, the partner's velocity set a note outside a line)
        if not sh.get("own_vel"):
            out = line_at_shown(sh, out, ppq)
        return out, tr_
    if tool in ("extend", "subtract"):  # (a sliced piece: on its whole's notes, cut where it was: stepped_piece)
        got = stepped_piece(sh, tool, notes, tracks, ppq, get, step["cfg"]) if sh.get("cut") else None
        if got is not None:
            return got
        if tool == "subtract":
            return shown_subtracted(notes, step["cfg"], sh, ppq), tracks
        return extended(notes, step["cfg"], ppq), tracks
    sus = step["cfg"]  # (Sustain)
    got = held_piece(dict(sh, sustain=sus), notes, tracks, ppq, get)
    if got is not None:
        return got
    if "gap" not in sus and sh.get("glue"):  # (Auto: worked out without glue, as auto_sustain does)
        bare = {k: v for k, v in sh.items() if k not in ("glue", "_noglue0")}
        fx = [st for st in sh.get("fx") or () if st["tool"] not in ("glue", "glue0")]
        bare.pop("fx", None)
        if fx:
            bare["fx"] = fx
        sus = dict(sus, _auto=auto_gap(get(dict(bare, _sus=True))[0]))
    return held(notes, tracks, sus, ppq)


_held_wholes = {}  # id(whole's notes) -> (them, (sustain, ppq), held(...)): held_piece's


def unglued(sh):
    """sh without its glue and glue places (None: it has no glue)."""
    if not sh.get("glue"):
        return None
    out = {k: v for k, v in sh.items() if k != "glue"}
    fx = [st for st in sh.get("fx") or () if st["tool"] != "glue"]
    if fx:
        out["fx"] = fx
    else:
        out.pop("fx", None)
    return out


def auto_sustain(sh, get):
    """sh's Sustain as held: an Auto hold gap worked out from the notes WITHOUT glue (sus "_auto" ticks; hunt
    2026-11-07: gluing touching notes in a box changed the shape's most common gap, so rows outside the box were held
    differently). Typed gap / no glue: as it is."""
    sus = sh["sustain"]
    bare = unglued({k: v for k, v in sh.items() if k != "sustain"}) if "gap" not in sus else None
    if bare is None:
        return sus
    notes = get(dict(bare, _sus=True))[0]  # (its own overlaps kept, as fx_notes holds them)
    return dict(sus, _auto=auto_gap(notes))


def piece_ticks(src, part, ppq):
    """A line piece's own stretch: (its first tick or None at the line's start, lo, hi), the ticks its cuts sit on
    (no cut at an end = no limit there). src / part: sliced.source's."""
    from notes.sliced import BIG, beat_on, paths_of
    paths = paths_of(src)

    def tick(c):
        k = int(c // BIG)
        if k >= len(paths) or len(paths[k]) < 1:
            return None
        return int(round(float(beat_on(paths[k], c - k * BIG)) * ppq))
    tl = tick(part[0]) if part[0] else None
    tr = tick(part[1]) if part[1] is not None else None
    return tl, (-1 << 62) if tl is None else tl, (1 << 62) if tr is None else tr


def _clipped(notes, lo, hi):
    out = notes[:, :4].copy()
    out[:, 0] = np.maximum(out[:, 0], lo)
    out[:, 1] = np.minimum(out[:, 1], hi)
    return out


def _without_marks(side):
    return {k: v for k, v in side.items() if k != "cuts"} if isinstance(side, dict) else side


def stepped_piece(sh, step, notes, tracks, ppq, get, cfg=None):
    """A sliced piece of a line (Split here / Slice / Lasso) with Gate aware flip, Gate extend or Gate subtract (step):
    that step done on the WHOLE shape's notes, then cut at the piece's own ends, a knife through the finished notes
    (partial sweep 50337141 / 53101013 / 53101035: done on the piece alone, a note cut in two had its cut end
    shortened / lengthened and its runs mirrored inside the piece moved the cut). notes / tracks: the piece's own
    before the step. A graph across the shape or Gate extend's "Same amount" stay worked out on the piece (its own
    cut graph / average, as it was cut). None: not such a piece, or its notes aren't the whole's cut at its ends
    (made alone, as before). Gate extend / subtract also on a piece cut through its whole's notes (pages / glue, a
    custom shape: partial sweep 68824084 / 69006198): each note found in the whole's (the one it is, or a part of).
    cfg: the step's settings when made by the runner (steps.expanded; sh = the shape before the step there: the
    colour steps before it don't count, the notes' times do)."""
    runner = cfg is not None
    if not sh.get("cut") or not len(notes) or not runner and (sh.get("order") is not None or sh.get("_run")):
        return None
    gate = step == "gate_flip"
    if gate and (runner or sh["kind"] not in LINE_KINDS):
        return None
    if not gate:
        cfg = cfg if runner else sh.get(step)
        if not isinstance(cfg, dict) or cfg.get("vary") or cfg.get("same") or (cfg.get("start") or {}).get("vary"):
            return None
        cfg = dict(_without_marks(cfg), **({"start": _without_marks(cfg["start"])} if cfg.get("start") else {}))
    got = source(sh)
    if got is None:
        return None
    src, part, _ = got
    timed = (sh["kind"] in LINE_KINDS and isinstance(part[0], (int, float)) and not src.get("glue")
             and not src.get("fx"))  # (cut where the line's time reaches the cut: the piece = a stretch of time)
    if gate and not timed:
        return None
    from notes.steps import before_view, order_of
    if runner and (step, 0) in order_of(src):  # (its own order: the whole up to just before this step, not with
        w0, wt0 = get(before_view(src, (step, 0)))  # the steps after it too; sweep 277542201: Sustain after a
    else:                                          # first Gate subtract was held before being shortened)
        w0, wt0 = get({k: v for k, v in src.items() if k != step})
    if not len(w0):
        return None
    if not runner and (tracks is None) != (wt0 is None):
        return None
    made = w0  # (the whole's notes as remembered: the same array for every piece)
    w0 = np.asarray(w0, np.int64)[:, :4]
    mine = np.asarray(notes, np.int64)[:, :4]
    pt = np.zeros(len(mine), np.int64) if tracks is None else np.asarray(tracks, np.int64)
    if runner:  # (matched by the notes alone, each keeps its own colour)
        wt, pick = np.zeros(len(w0), np.int64), np.zeros(len(mine), np.int64)
    else:
        wt, pick = (np.zeros(len(w0), np.int64) if wt0 is None else np.asarray(wt0, np.int64)), pt
    # (where to look: the piece's stretch of time, or its notes' own)
    lo, hi = piece_ticks(src, part, ppq)[1:] if timed else (int(mine[:, 0].min()), int(mine[:, 1].max()))
    rows = _owned_rows(w0, wt, mine, pick, lo, hi)
    if rows is None:
        return None
    w1 = _stepped_whole(made, w0, step, cfg, ppq, bool(sh.get("own_vel")))
    if step == "gate_flip":  # (runs mirrored across the cut: the knife cuts the notes where they are now)
        keep = (w1[:, 0] < hi) & (w1[:, 1] > lo)
        return _clipped(w1[keep], lo, hi), None if tracks is None else wt[keep]
    w1 = w1[rows]
    w = w0[rows]
    out = w1.copy()  # (each of its notes as the whole's finished note, still cut where it was cut)
    out[:, 0] = np.where(mine[:, 0] > w[:, 0], np.maximum(w1[:, 0], mine[:, 0]), w1[:, 0])
    out[:, 1] = np.where(mine[:, 1] < w[:, 1], np.minimum(w1[:, 1], mine[:, 1]), w1[:, 1])
    out[:, 3] = mine[:, 3]  # (its own velocity: a velocity line on the piece keeps it)
    keep = out[:, 1] > out[:, 0]
    return out[keep], None if tracks is None else pt[keep]


_stepped_wholes = {}  # (id(whole's notes), step, settings, ppq, own_vel) -> (them, the step done): stepped_piece's


def _stepped_whole(made, w0, step, cfg, ppq, own_vel):
    """The whole's notes w0 (made: the array they came from, remembered by the caller) after step, worked out once
    for all its pieces (a line cut into 180 pieces did it 180 times)."""
    key = (id(made), step, json.dumps(cfg, sort_keys=True), ppq, own_vel)
    was = _stepped_wholes.get(key)
    if was is not None and was[0] is made:
        return was[1]
    if len(_stepped_wholes) > 16:
        _stepped_wholes.clear()
    if step == "gate_flip":
        w1 = mirrored_runs(w0, None, own_vel)[0]
    else:
        w1 = extended(w0, cfg, ppq) if step == "extend" else subtracted(w0, cfg, ppq)
    w1 = np.asarray(w1, np.int64)
    _stepped_wholes[key] = (made, w1)
    return w1


def _owned_rows(w0, wt, mine, pt, lo, hi):
    """Which of the whole's notes w0 (colours wt) each of a piece's notes mine (colours pt) is, or a part of (same
    key, velocity and colour, inside it), each whole note taken once; None: one of them isn't any (not simply cut
    out of the whole: stepped_piece makes it alone)."""
    near = np.flatnonzero((w0[:, 0] <= hi) & (w0[:, 1] >= lo))
    exact = {}
    for i, row, t in zip(near.tolist(), w0[near].tolist(), wt[near].tolist()):
        exact.setdefault((*row, t), []).append(i)
    rows = np.full(len(mine), -1, np.int64)
    left = []
    for j, (row, t) in enumerate(zip(mine.tolist(), pt.tolist())):
        got = exact.get((*row, t))
        if got:
            rows[j] = got.pop()
        else:
            left.append(j)
    if left:
        used = set(rows[rows >= 0].tolist())
        wn = w0[near]
        for j in left:
            s, e, k, v = mine[j].tolist()
            hit = near[(wn[:, 2] == k) & (wn[:, 0] <= s) & (wn[:, 1] >= e) & (wt[near] == pt[j])]
            # (its own velocity first; a velocity line on the piece since: by place alone, sweep 73407007, the piece
            # was made alone and Gate subtract shortened its cut end again)
            free = sorted((i for i in hit.tolist() if i not in used), key=lambda i: int(w0[i, 3]) != v)
            if not free:
                return None
            rows[j] = free[0]
            used.add(free[0])
    return rows


def held_piece(sh, notes, tracks, ppq, get):
    """A sliced piece of a sustained line (notes, tracks: its own, before Sustain): held as the WHOLE shape holds them,
    cut at the piece's own cuts, and the held ends of earlier notes crossing its start starting again there (partial
    sweep 11-02: held per piece, a note held across the cut ended at its own end). None: not such a piece."""
    from notes.sliced import velocity
    got = source(sh) if sh["kind"] in LINE_KINDS and sh.get("cut") else None
    if got is None or not len(notes):
        return None
    src, part, same_vel = got
    if not isinstance(part[0], (int, float)) or src.get("glue") or src.get("fx"):
        return None
    whole = dict({k: v for k, v in src.items() if k != "sustain"}, **velocity(sh["cut"]["whole"]))
    if sh.get("_run") and sh.get("fx") and all(st.get("tool") in RUNNER for st in sh["fx"]):
        # (made by the runner: the whole gets the steps that ran before this Sustain too (Gate subtract first...),
        # not only the drawing; sweep 277542201: a subtracted note was held again over the cut)
        whole["fx"], whole["_run"] = json.loads(json.dumps(sh["fx"])), True
    w0, wt0 = get(whole)
    if not len(w0):
        return None
    hold = json.dumps(sh["sustain"], sort_keys=True)
    was = _held_wholes.get(id(w0))  # (the whole held once for all its pieces, not again per piece)
    if was is None or was[0] is not w0 or was[1] != (hold, ppq):
        if len(_held_wholes) > 16:
            _held_wholes.clear()
        was = _held_wholes[id(w0)] = (w0, (hold, ppq), held(w0, wt0, sh["sustain"], ppq))
    w1, wt1 = was[2]
    tl, lo, hi = piece_ticks(src, part, ppq)
    w1 = np.asarray(w1, np.int64)
    wt1 = np.zeros(len(w1), np.int64) if wt1 is None else np.asarray(wt1, np.int64)
    ends = {}
    for s, e, k in w1[:, :3].tolist():
        ends[s, k] = max(e, ends.get((s, k), e))
    starts0 = {(s, k) for s, k in w0[:, [0, 2]].tolist()}
    notes = np.asarray(notes, np.int64).copy()
    mine = {(s, k): v for s, k, v in notes[:, [0, 2, 3]].tolist()}
    env = velocity_env(sh)
    span = np.concatenate(cached_arrays(sh))[:, 0] * ppq
    t_lo, t_hi = float(span.min()), float(span.max())

    def vel_of(k, s):  # (a note going on from an earlier one: that one's velocity, as this piece reads it)
        on = w0[(w0[:, 2] == k) & (w0[:, 0] <= s)]
        if not len(on):
            return None
        s0 = int(on[:, 0].max())
        if (s0, k) in mine:
            return mine[s0, k]
        if same_vel:
            return None
        return int(env_velocities(env, np.array([s0]), t_lo, t_hi, min(0.0, float(env[0][0])))[0])
    for i, (s, e, k) in enumerate(notes[:, :3].tolist()):
        if (s, k) in ends:
            e2 = ends[s, k]
        else:  # (the second half of a note cut at the piece's start: the whole's note held over it)
            over = w1[(w1[:, 2] == k) & (w1[:, 0] < s) & (w1[:, 1] > s)]
            e2 = int(over[:, 1].max()) if len(over) else e
        notes[i, 1] = max(min(e2, hi), s + 1) if e2 > s else e
    extra = []
    own_at = {k for s, k in mine if s == tl}
    for j, (s, e, k, v) in enumerate(w1[:, :4].tolist()):
        if s < lo < e and k not in own_at:  # (held over the cut from the piece before)
            row = [lo, min(e, hi), k, vel_of(k, s) or v]
        elif lo <= s < hi and (s, k) not in starts0 and (s, k) not in mine:  # (starting on a Sustain stop)
            row = [s, min(e, hi), k, vel_of(k, s) or v]
        else:
            continue
        if row[1] > row[0]:
            extra.append(row + [int(wt1[j])])
    if extra:
        add = np.asarray(extra, np.int64)
        notes = np.concatenate([notes[:, :4], add[:, :4]])
        tracks = np.concatenate([np.zeros(len(notes) - len(add), np.int64) if tracks is None else
                                 np.asarray(tracks, np.int64), add[:, 4]])
        got = unique_rows(np.column_stack([notes, tracks]))
        return got[:, :4], got[:, 4]
    return notes, tracks


def stirred_piece(sh, notes, tracks, held_, get, gate, rule):
    """A sliced piece of a stirred shape (recolour.py Stir): its notes take the colours the WHOLE shape shows (its
    stir looks across the cut, into the other pieces' notes), each from the whole's note at its spot (from_whole;
    a gate flipped / flipped piece: the spot its note had before, recolour.places_of, so the colours flip with it).
    None: not a piece any more (made as a shape of its own)."""
    from notes.recolour import from_whole, places_of
    from notes.sliced import flipped_back, source
    own = sh["recolour"]
    then = own.get("cut_frame")
    piece = sh
    if not sh.get("cut"):  # (flipped since the cut, a shape of its own: looked up as the piece unflipped, hunt 11-07)
        now = own.get("frame") or (1, 0, 0, 1)
        was = then or (1, 0, 0, 1)
        if now[1] or was[1]:
            return None
        piece = flipped_back(sh, now[0] * was[0] < 0, now[3] * was[3] < 0)
        if piece is None:
            return None
        then = was
    got = source(without_recolour(piece))
    if got is None:
        return None
    if tracks is None:  # (one colour before it, a line: the whole's colours all the same; partial sweep 56456115, a
        # Scattered line's pieces were coloured anew, the note cut in two changed colour)
        tracks = np.zeros(len(notes), np.int64)
    # (the whole as it was cut: its own box, flipped as it was then (rc "cut_frame", recolour.carried; old pieces
    # without it: never flipped); the piece's notes mirrored back only by the flips made since)
    rc = {k: v for k, v in own.items() if k not in ("whole", "frame", "cut_frame")}
    if then and tuple(then) != (1, 0, 0, 1):
        rc["frame"] = then
    if then and not then[1]:
        now = own.get("frame") or (1, 0, 0, 1)
        if not now[1]:
            rule = dict(rule, frame=[now[0] * then[0], 0, 0, now[3] * then[3]])
    whole = {k: v for k, v in without_recolour(got[0]).items() if k != "gate_flip"}
    rounds = bool(sh.get("_rounds"))  # (the whole's rounds too: a piece's notes take the round with the colour)
    if rounds:
        whole["_rounds"] = True
    wn, wt = get(dict(whole, recolour=rc))
    return from_whole(notes, tracks, wn, wt, held_, lambda n: places_of(n, rule, gate, box_of(n)), rounds)


def free_box(sh, notes, get, keys):
    """The box (recolour.box_of) of sh's notes (without Recolour: notes) as they'd be without the piano roll's
    edges cutting them, so a shape pushed partly past the song's start / the keys keeps its colours (Recolour counts
    from it); None when they reach no edge (the notes' own box)."""
    if not len(notes):
        return None
    b = box_of(notes)  # (remembered for big notes: asked at every mouse move with the Stir window open)
    if b[0] > 0 and b[2] > 0 and b[3] < keys - 1:
        return None
    plain = dict(without_recolour(sh), _free=True)
    up = 0
    if notes[:, 2].min() == 0 and "pts" in sh:  # (rows below the lowest key aren't made at all: lifted, counted back)
        up = max(0, math.ceil(-min(p for _, p in cached_path(sh)) + 1))
    free, _ = get(moved(plain, (0, up)) if up else plain)
    if not len(free):
        return None
    b = box_of(free)
    return b[0], b[1], b[2] - up, b[3] - up


def tagged_notes(sh, k, ppq, keys):
    """sh's notes and tracks as shown, with one more last column of the notes: which copy of its path page k each
    note is (0 = the shape itself; copypath.apply_path's tag), carried through every step after the page (flips,
    turns, Claw, Strum, Chop, Sustain...). Not remembered (made once, for Turn into plain shapes)."""
    fx = list(sh["fx"])
    fx[k] = dict(fx[k], tag=True)

    def get(s):
        return fx_notes(s, ppq, keys, get)
    return get(dict(sh, fx=fx))


def unflipped(sh):
    """sh (without its pages) with its drawing flipped back along its hidden "_m" axes, each within its own box."""
    out = json.loads(json.dumps(bare_of(sh)))
    for axis in sh["_m"]:
        k = 0 if axis == "time" else 1
        vals = [pt[k] for pt in cached_path(out)]
        flip_shape(out, axis == "time", min(vals) + max(vals))
    return out


def before_step(sh):
    """(sh without its last step, as the notes that step works on are made: flipped / turned back before a flip /
    turn step; that step, or None when it does nothing: a flip / turn with no page switched on before it leaves the
    drawing's own notes, user 2026-10-07, they came out jagged. Not on a piece whose notes come from its whole's
    pages: sliced.steps_kept)."""
    fx, last = sh["fx"], sh["fx"][-1]
    rest = dict(sh, fx=fx[:-1]) if len(fx) > 1 else {k: v for k, v in sh.items() if k != "fx"}
    if last["tool"] in ("flip", "turn") and idle(fx[:-1]) and not steps_kept(sh):
        return rest, None
    if last["tool"] == "glue":  # (this place's boxes are glued here, not before: glue_level / fx.at_step)
        k = last.get("from", 0)
        if k and isinstance(rest.get("glue"), list):
            rest["glue"] = rest["glue"][:k]
        else:
            rest.pop("glue", None)
    elif last["tool"] == "flip":
        rest["_m"] = toggled(sh.get("_m"), last["axis"])
        if not rest["_m"]:
            del rest["_m"]
    elif last["tool"] == "turn":
        rest = turned_back(rest, last)
    return rest, last


def as_made(sh):
    """sh's drawing as its notes are made from it: before its flip / turn steps (a sliced piece flipped or turned
    as a shape with pages is still that piece: sliced.moved_by(as_made(sh)))."""
    while sh.get("fx"):
        sh = before_step(sh)[0]
    return unflipped(sh) if sh.get("_m") else bare_of(sh)


def turned_back(sh, step):
    """sh's drawing turned back from a turn step (around its own middle: a custom shape's box's, as its corner turns
    it), its flips seen the other way round."""
    back = json.loads(json.dumps(sh))
    if sh["kind"] == "custom":
        (b1, p1), (b2, p2) = sh["pts"][1:]
        cb, cp = (b1 + b2) / 2, (p1 + p2) / 2
    else:
        a = np.concatenate(cached_arrays(sh))
        cb, cp = (a[:, 0].min() + a[:, 0].max()) / 2, (a[:, 1].min() + a[:, 1].max()) / 2
    deg, r = step["deg"], step["r"]
    q = round(deg / 90)
    if abs(deg - q * 90) < 1e-9:  # (quarter turns: everything turns back, as Turn 90 turned it)
        for _ in range(abs(q)):
            turn_shape(back, q < 0, r, cb, cp)
        if back.get("_m") and q % 2:
            if len(back["_m"]) == 1:  # (a sideways flip now undone as an upside-down one: its velocity put back)
                reverse_velocity(back)
            back["_m"] = swapped(back["_m"])
        return back
    if back.get("_m"):  # (flipped after a slanted turn: the drawing flipped back first, velocity / glue / Range too)
        for axis in back.pop("_m"):
            flip_shape(back, axis == "time", 2 * (cb if axis == "time" else cp))
    back["pts"] = turn_pts(back["pts"], -deg, r, cb, cp)
    return back


def bare_of(sh):
    """sh without its note tool pages (its notes and glue only)."""
    return {k: v for k, v in sh.items() if k not in ("fx", "_m")}


def fx_step(notes, tracks, step, sh, ppq, keys=256):
    """The notes after one step of sh["fx"] (fx.py)."""
    if step.get("off"):
        return notes, tracks
    tool = step["tool"]
    if tool == "path":  # (copies past the highest key are cut off: copypath.py)
        if tracks is None and step["cfg"] and step["cfg"]["colours"] != "keep":  # (one colour: group 0, so it turns)
            tracks = np.zeros(len(notes), np.int64)
        if sh.get("_rounds") and step["cfg"]:  # ("New channels each Colours round": copies' rounds too)
            step = dict(step, cfg=dict(step["cfg"], rounds=True))
        if step.get("tag") and step["cfg"]:  # (Turn into plain shapes: each note's copy rides along as one more
            # column of the notes, through the steps after it: tagged_notes)
            who = tracks if tracks is not None else np.zeros(len(notes), np.int64)
            got = apply_path(np.column_stack([notes, who]).astype(np.int64), step["cfg"], ppq, keys, tag=True)
            return np.column_stack([got[:, :-2], got[:, -1]]), None if tracks is None else got[:, -2]
        return _after(lambda a, cfg, q: apply_path(a, cfg, q, keys), notes, tracks, step["cfg"], ppq)
    if tool == "chop":
        return with_chop(notes, tracks, dict(sh, chop=step["cfg"]), ppq)
    if tool == "claw":
        return with_claw(notes, tracks, step["cfg"], ppq)
    if tool == "strum":
        cfg = step["cfg"]
        if cfg and sh.get("_rounds") and tracks is not None and cfg.get("edges") == "wrap":  # ("Repeated colours
            cfg = dict(cfg, rounds=True)  # get new channels": each trip round the block = later rounds, strum.wrapped)
        return with_strum(notes, tracks, cfg, ppq)
    if tool == "trim":
        return _after(apply_trim, notes, tracks, step["cfg"], ppq)
    if tool == "vel":  # (over the shape's time, like its velocity line)
        t = np.concatenate(cached_arrays(sh))[:, 0] * ppq
        return velocities(notes, step["pts"], float(t.min()), float(t.max()),
                          from0_starts(sh, notes, notes[:, 0], ppq)), tracks
    return notes, tracks


def glue_level(sh, n=None):
    """sh as its (last, or n steps long: the one at n - 1) glue step sees it (fx.GLUE): its drawing and glue boxes as
    they were before the flips / turns after that step (its boxes are shares of the drawing's box, flipped / turned
    with it), the later glue places' boxes gone."""
    while sh.get("fx") and (len(sh["fx"]) > n if n is not None else sh["fx"][-1]["tool"] != "glue"):
        sh = before_step(sh)[0]
    return unflipped(sh) if sh.get("_m") else sh


def with_glue(notes, tracks, sh, ppq):
    """shape_notes_tracks' notes and tracks after the shape's glue (glue.py; before any page, unless it has a place
    among them: glue_level)."""
    glue = sh.get("glue")
    if not glue or not len(notes):
        return notes, tracks
    box = glue_box(np.concatenate(cached_arrays(sh)))
    if tracks is None:
        return apply_glue(notes, glue, box, ppq, False), None
    got = apply_glue(np.column_stack([notes, tracks]), glue, box, ppq, True)
    return got[:, :-1], got[:, -1]


def with_chop(notes, tracks, sh, ppq):
    """The same after a Chop page (chop.py; sh["chop"] = its settings). A Fill shape's touching notes on
    a key (its outline notes, colour borders) follow one rhythm; spam notes and others each start their own."""
    chop = sh.get("chop")
    if chop and sh["kind"] == "custom" and sh.get("fill") == "fill" and "notes" not in sh:
        chop = dict(chop, runs=True)
        if sh.get("cut") and len(notes) and not chop.get("abs"):  # (a sliced piece: the rhythm goes on across the cut)
            chop["origins"] = run_origins(sh, notes, ppq)
    return _after(apply_chop, notes, tracks, chop, ppq)


def with_claw(notes, tracks, claw, ppq):
    """shape_notes_tracks' notes and tracks after a Claw page (claw.py; None = none)."""
    return _after(apply_claw, notes, tracks, claw, ppq)


def with_strum(notes, tracks, strum, ppq):
    """The same after a Strum page (strum.py)."""
    return _after(apply_strum, notes, tracks, strum, ppq)


def _after(fn, notes, tracks, settings, ppq):
    if not settings:
        return notes, tracks
    if tracks is None:
        return fn(notes, settings, ppq), None
    got = fn(np.column_stack([notes, tracks]), settings, ppq)
    return got[:, :-1], got[:, -1]


def env_velocities(env, starts, t_lo, t_hi, lo=0.0):
    """Velocities of notes starting at starts (ticks) from a velocity line env over t_lo .. t_hi (lo: read that far
    before its start, as a share of its time; else at its start)."""
    if len({v for _, v in env}) == 1:  # the same velocity everywhere
        return np.full(len(starts), max(1, min(127, round(env[0][1]))), np.int64)
    frac = np.clip((starts - t_lo) / (t_hi - t_lo), lo, 1) if t_hi > t_lo else np.zeros(len(starts))
    # (rounds halves to even, like round; float noise taken off first: a point added to the line elsewhere moved a
    # value sitting on a half a hair either way, 83.5 -> 84 or 83)
    return np.clip(np.round(np.round(env_values(env, frac), 6)), 1, 127).astype(np.int64)


def whole_tools(whole, raw, spots, tracks, ppq):
    """A line piece's whole's notes (raw (start, end, key), each one's spot, tracks) with its velocities, after its
    glue and note tool pages: (start, end, key, velocity) rows, their spots, tracks."""
    t = dedupe(np.concatenate(cached_arrays(whole)))[:, 0] * ppq
    # (a note across beat 0 of a shape made later reads it where it shows, as the shape uncut does: from0_starts;
    # sweep 277533358, its velocity changed when cut)
    vel = env_velocities(velocity_env(whole), from0_starts(whole, raw, raw[:, 0], ppq), float(t.min()),
                         float(t.max()))
    who = tracks if tracks is not None else np.zeros(len(raw), np.int64)
    a = np.column_stack([raw, vel, np.arange(len(raw)), who]).astype(np.int64)  # (spots ride along as row numbers)
    fx, glued_first = whole_steps(whole)  # (its glue before the pages where its order puts it: steps.py)
    first = glue_from(whole.get("glue"), whole.get("fx")) if len(a) and glued_first else None  # (the rest at its
    if first:  # places: run_steps)
        a = apply_glue(a, first, glue_box(np.concatenate(cached_arrays(whole))), ppq, True)
    a = run_steps(a, fx, whole, ppq)
    return a[:, :4], spots[a[:, 4]], a[:, 5] if tracks is not None or path_colours(whole) else None


def run_steps(a, fx, sh, ppq, m=(), pre=()):
    """Notes a (start, end, key, velocity, then columns riding along) after the steps fx of sh (as fx_notes does them;
    first a's put back by each of pre in order (("m", flip axes) / ("t", degrees, ticks per key): turned), then
    flipped along the axes m). A turn step turns the notes back, not the drawing (close, not exact). A flip / turn with
    no page switched on before it does nothing (before_step)."""
    up = []  # (the steps, last first: from the end down to the notes, then back up; no recursion: lists can be long)
    put_in = ("glue0", "sustain", "extend", "subtract")  # (put in by whole_steps: not sh's own fx entries)
    extra = sum(1 for st in fx if st["tool"] in put_in)
    while fx:
        last, fx = fx[-1], fx[:-1]
        if last["tool"] in put_in:
            extra -= 1
        if last["tool"] in ("flip", "turn") and idle(fx):
            continue
        # (a glue's place: which boxes, on the drawing as it is after that many of sh's own steps)
        up.append(dict(last, _at=len(fx) - extra) if last["tool"] in ("glue", "glue0") else last)
        if last["tool"] == "flip":
            m = toggled(m, last["axis"])
        elif last["tool"] == "turn":
            pre = tuple(pre) + ((("m", m),) if m else ()) + (("t", -last["deg"], last["r"] * ppq),)
            m = ()
    for op in pre:
        a = mirrored(a, op[1], notes_box(a)) if op[0] == "m" else turn_notes(a, op[1], op[2], 256, groups(a[:, 3::2]))[0]
    if m:
        a = mirrored(a, m, notes_box(a))
    for last in reversed(up):
        if last["tool"] == "flip":
            a = mirrored(a, [last["axis"]], notes_box(a))
        elif last["tool"] == "turn":  # (velocity, track: not spot)
            a = turn_notes(a, last["deg"], last["r"] * ppq, 256, groups(a[:, 3::2]))[0]
        elif last["tool"] == "glue":  # (the last column = the tracks: never glued together)
            g = glue_level(sh, last["_at"] + 1)
            boxes = at_step(g.get("glue"), last)
            if boxes and len(a):
                a = apply_glue(a, boxes, glue_box(np.concatenate(cached_arrays(g))), ppq, True)
        elif last["tool"] == "glue0":  # (the boxes before the pages, glued where the order puts them)
            g = glue_level(sh, last["_at"])
            boxes = glue_from(sh.get("glue"), sh.get("fx"))
            if boxes and len(a):
                a = apply_glue(a, boxes, glue_box(np.concatenate(cached_arrays(g))), ppq, True)
        elif last["tool"] == "extend":  # (ordered before a page: on the whole's notes there, steps.in_whole)
            a = extended(a, last["cfg"], ppq)
        elif last["tool"] == "subtract":
            a = subtracted(a, last["cfg"], ppq)
        elif last["tool"] == "sustain":  # (the pieces made by a cut keep the note's spot and track columns)
            if len(a):
                a = held(a, None, last["cfg"], ppq)[0]
        elif last["tool"] in ("path", "strum"):  # (the last column = the tracks: the copies' colours go on them,
            # hunt; a Strum wrap's later rounds too, so sliced line pieces keep them)
            notes, tr = fx_step(a[:, :-1], a[:, -1], last, sh, ppq)
            a = np.column_stack([notes, tr])
        else:
            a = fx_step(a, None, last, sh, ppq)[0]
    return a


class LayerPool:
    """A few threads making Hz layers side by side. Daemon threads (closing Spiderweb never waits for a layer still
    being made, as with the background notes thread), unlike ThreadPoolExecutor's."""

    def __init__(self, n):
        import queue
        self.jobs = queue.SimpleQueue()
        for i in range(n):
            threading.Thread(target=self.work, name=f"hz-layer-{i}", daemon=True).start()

    def work(self):
        while True:
            fut, f, x = self.jobs.get()
            try:
                fut.set_result(f(x))
            except BaseException as e:  # (raised again in the caller, as it was)
                fut.set_exception(e)

    def map(self, f, xs):
        """[f(x) for x in xs], made side by side; an error in one is raised here."""
        from concurrent.futures import Future
        futs = [Future() for _ in xs]
        for fut, x in zip(futs, xs):
            self.jobs.put((fut, f, x))
        return [fut.result() for fut in futs]


_layer_pool = []  # (made the first time a Hz bass has 2+ layers; only one, even when two threads ask at once)
_pool_lock = threading.Lock()


def layer_pool():
    with _pool_lock:
        if not _layer_pool:
            import os
            _layer_pool.append(LayerPool(min(8, os.cpu_count() or 1)))
        return _layer_pool[0]


def layered_notes(sh, ppq, keys):
    """kind_notes_tracks of a Hz bass with layers: each layer heard makes its notes as a Hz bass of its own, in order.
    A note on a key an earlier layer sounds at the same time goes on another channel (user): its track + CLASH x its
    clash_levels level. The layers are made side by side in threads (a 5-layer 128-key one: 1.5 s -> 1.2 s; caches
    they share are read once, then filled)."""
    layers = heard_layers(sh["hz"])
    if len(layers) > 1:
        got = layer_pool().map(lambda hz: kind_notes_tracks(dict(sh, hz=hz), ppq, keys), layers)
    else:
        got = [kind_notes_tracks(dict(sh, hz=hz), ppq, keys) for hz in layers]
    if not got:
        return np.zeros((0, 4), np.int64), None
    notes = np.concatenate([n for n, _ in got])
    levels = np.concatenate(clash_levels([n for n, _ in got]))
    if all(t is None for _, t in got) and not levels.any():
        return notes, None
    tracks = np.concatenate([np.zeros(len(n), np.int64) if t is None else np.asarray(t, np.int64) for n, t in got])
    return notes, tracks + levels * CLASH


def voiced_notes(sh, ppq, keys):
    """kind_notes_tracks of a Hz bass with its own channel per note (hz_cc.voices): each voice's notes made as a Hz
    bass of their own (so chord lines don't merge), track = the voice."""
    played, got = voices(sh["hz"], left_edge(sh))
    mine = {}
    for li, v, tones in got:
        mine.setdefault((v, li), []).extend(tones)
    notes, tracks = [np.zeros((0, 4), np.int64)], [np.zeros(0, np.int64)]
    for (v, li), tones in sorted(mine.items(), key=lambda e: e[0]):
        n, _ = kind_notes_tracks(dict(sh, hz=voice_hz(played, li, tones)), ppq, keys)
        notes.append(np.asarray(n, np.int64).reshape(-1, 4))
        tracks.append(np.full(len(notes[-1]), v, np.int64))
    return np.concatenate(notes), np.concatenate(tracks)


def clash_levels(lists):
    """Note lists ((start, end, key, ...) rows), in order -> per list, each note's level: 0 = no note of an earlier
    list on its key at the same time, else the lowest level where none is (notes touching end to start don't clash)."""
    out = [np.zeros(len(n), np.int64) for n in lists]
    if len(lists) < 2:
        return out
    fast = loops()
    lens = [len(n) for n in lists]
    if fast and sum(lens):  # (the compiled loop: the same levels)
        rows = np.concatenate([n[:, :3] for n in lists]).astype(np.int64, copy=False)
        if int(rows[:, 2].max()) - int(rows[:, 2].min()) <= 4 * len(rows) + 1024:  # (else: too spread for a table)
            offs = np.concatenate([[0], np.cumsum(lens)]).astype(np.int64)
            lv = np.zeros(len(rows), np.int64)
            fast.clash_levels(rows, offs, lv)
            return np.split(lv, offs[1:-1])
    span = max([int(n[:, 1].max()) for n in lists if len(n)] or [0]) + 1
    placed = []  # per level: its notes' key * span + start (sorted), and the latest end so far on that key
    for notes, lv in zip(lists, out):
        left = np.arange(len(notes))
        level = 0
        while len(left):
            q = notes[left]
            if level < len(placed):
                at, ends, keys = placed[level]
                i = np.searchsorted(at, q[:, 2] * span + q[:, 1], "left") - 1
                j = np.maximum(i, 0)
                hit = (i >= 0) & (keys[j] == q[:, 2]) & (ends[j] > q[:, 0])
            else:
                hit = np.zeros(len(q), bool)
            free = left[~hit]
            lv[free] = level
            if len(free):
                a = notes[free][:, :3]
                if level < len(placed):  # (its notes back with their latest ends: the running max stays the same)
                    at, ends, keys = placed[level]
                    a = np.concatenate([np.column_stack([at % span, ends, keys]), a])
                a = a[np.lexsort((a[:, 0], a[:, 2]))]
                placed[level:level + 1] = [(a[:, 2] * span + a[:, 0], running_max(a[:, 1], a[:, 2]), a[:, 2])]
            left = left[hit]
            level += 1
    return out


def kind_notes_tracks(sh, ppq, keys):
    got = made_at_cut(sh, ppq, keys, _kind_notes_tracks) if sh.get("cut") else None  # (a moved sliced piece)
    return got if got is not None else _kind_notes_tracks(sh, ppq, keys)


def _kind_notes_tracks(sh, ppq, keys):
    # cut by the Slice tool: the whole's notes on its side (sliced.py; a line too when cut from a piece as it was)
    if sh.get("cut") and (sh["kind"] in ("custom", "funnel") or (sh["cut"].get("whole") or {}).get("cut")):
        got = piece_notes(sh, ppq, keys, shape_notes_tracks)  # (the whole's glue / pages done first)
        if got is not None:
            return got
    if sh["kind"] in HZ_KINDS and isinstance((sh.get("hz") or {}).get("cc_own"), dict):  # (made_from's, with CC)
        return voiced_notes(sh, ppq, keys)
    if sh["kind"] in HZ_KINDS and (sh.get("hz") or {}).get("layers"):
        return layered_notes(sh, ppq, keys)
    got = moved_paste(sh, ppq, keys)  # (a big paste only moved: its notes shifted, without the copies below)
    if got is not None:
        return got
    by_snap = sh.get("by_snap") if sh["kind"] in LINE_KINDS else None  # ("Notes by snap": one step each, paths.py)
    end_dot = sh.get("end_dot", False) and not by_snap
    piece = source(sh) if sh["kind"] in LINE_KINDS and sh.get("cut") else None
    vel_sh = sh  # (whose velocities, over whose time)
    if piece:  # cut from another shape (sliced.py): its notes are made as that one's, then its own part kept
        whole, part, same_vel = piece
        knife, knife_d = sh["cut"].get("knife"), moved_by(sh)  # (cut through its notes by the Slice tool)
        mould = sh["cut"].get("mould")  # (cut with a shape, mould.py)
        if same_vel:
            vel_sh = whole
        sh = whole
    path = dedupe(np.concatenate(cached_arrays(vel_sh)))  # (drawing the line uses the same points)
    if path[-1, 0] < path[0, 0]:
        path = path[::-1]  # drawn right to left: the "last point" is the later end in time, same as left to right
    path = path * [ppq, 1]  # beats -> ticks
    own = None  # pasted notes' own velocities and tracks
    groups = None  # a custom shape made of other shapes (convert.py): which of them each note came from
    spots = None  # a piece's: where on the whole's path each note starts
    came = None  # a piece's stepped notes: the line note each came from (start, end), for its cuts
    if piece:
        raw, spots = spotted_notes(sh, ppq, end_dot)
        if by_snap:  # (the whole's steps, from the whole's start)
            whole_t = np.concatenate(cached_arrays(sh))[:, 0] * ppq
            raw = note_array(raw, 3)
            got, src = snap_steps(raw, by_snap * ppq, int(np.floor(whole_t.min() + 0.5)),
                                  int(np.floor(whole_t.max() + 0.5)), sh.get("by_snap_end", "cut"))
            raw, spots, came = got, spots[src], raw[src, :2]
    elif sh["kind"] == "custom" and "notes" in sh:
        # (a merged shape turned / slanted / stretched: its parts the same way, merged again; else a turned / skewed
        # picture or merged shape: each key row sampled across it, not its notes tilted)
        # ("New channels each Colours round": its parts' rounds, ridden along on the tracks: merge.with_rounds)
        # (a merge's "Merge leftovers" made with rounds ("lapped"): its tracks carry them already, + LAP x round)
        laps = sh.get("_rounds") and ("merge" in sh or sh.get("lapped")) and not cycling(sh)
        raw = merge_reshaped(sh, ppq, keys, bool(laps)) if "merge" in sh else None
        if raw is not None:
            if laps:
                raw = np.column_stack([raw[:, :4], raw[:, 4] + raw[:, 5] * merge_lap])
        else:
            if laps and "merge" in sh:
                got = merge_rounds(sh)
                sh, laps = (sh, False) if got is None else (got, True)
            if not frame_upright(sh["pts"]) and ("picture" in sh or "merge" in sh):
                raw = turned_notes(sh, ppq) if "picture" in sh else merge_turned(sh, ppq)
            elif "picture" not in sh and notes_resampled(sh):  # (plain notes turned / taller: like a turned image too)
                raw = merge_turned(sh, ppq)
            else:
                raw = block_notes(sh, ppq)
        raw, own = raw[:, :3], raw[:, 3:5]
        if laps:
            own = np.column_stack([own[:, 0], own[:, 1] % merge_lap + own[:, 1] // merge_lap * ROUND])
        elif sh.get("lapped"):  # (rounds off: its colours only)
            own = np.column_stack([own[:, 0], own[:, 1] % merge_lap])
    elif sh["kind"] == "custom":
        raw, groups = custom_notes_groups(sh, ppq)
    elif sh["kind"] == "funnel":
        raw = funnel_notes(sh, ppq)
    elif sh["kind"] == "gliss":  # a Glissando run: one note per picked key, a grid step each (gliss.py)
        raw = gliss_notes(sh, ppq)
    elif sh["kind"] in LINE_KINDS and len(cached_arrays(sh)) > 1:  # a joined curve's pieces: each like a line
        pieces = []
        for a in cached_arrays(sh):
            a = dedupe(a)
            if len(a) and a[-1, 0] < a[0, 0]:
                a = a[::-1]
            pieces.append(note_array(path_notes(a * [ppq, 1], end_dot), 3))
        raw = np.concatenate(pieces)
    elif end_dot and sh["kind"] == "poly" and len(path) > 2 and not (sh.get("shape") or sh.get("pattern")):
        # (with a formula its points are the formula's, like a curve's)
        raw = dot_segment_notes(path)
    else:
        raw = path_notes(path, end_dot)
    if by_snap and not piece:
        raw = snap_steps(note_array(raw, 3), by_snap * ppq, int(np.floor(float(path[:, 0].min()) + 0.5)),
                         int(np.floor(float(path[:, 0].max()) + 0.5)), sh.get("by_snap_end", "cut"))[0]
    t_lo = float(path[:, 0].min())
    t_hi = float(path[:, 0].max())
    if grows_free(vel_sh):  # (Ripples rings past the drawing: the velocity line runs over them, velocity.time_span)
        t_lo, t_hi = free_span(vel_sh, ppq) or (t_lo, t_hi)
    env = velocity_env(vel_sh)
    raw = note_array(raw, 3)
    if sh["kind"] in HZ_KINDS and sh.get("hz") and "notes" not in sh and not piece:  # (a Hz bass's Note length)
        raw = note_lengths(sh["hz"], raw, ppq, sh["kind"] != "funnel", sh)
    if sh.get("_free"):  # (not cut at the piano roll's edges: only Recolour's box asks, free_box)
        keep = np.ones(len(raw), bool)
    else:
        keep = (raw[:, 2] >= 0) & (raw[:, 2] < keys) & (raw[:, 1] > 0)
        raw = raw[keep]
        raw[:, 0] = np.maximum(raw[:, 0], 0)
    tracks = made = vel_at = None
    if spots is not None:  # (Colours count over the whole's notes, then the piece keeps its own)
        spots = spots[keep]
        came = came[keep] if came is not None else None
        keep = unique_index(raw)
        raw, spots = raw[keep], spots[keep]
        came = came[keep] if came is not None else None
        if cycling(sh) and len(raw):
            tracks = cycle_turns(sh, raw, ppq, float(path[:, 1].min()))
        def finish(src, raw, spots, tracks, came):
            if src.get("glue") or src.get("fx"):  # the whole's glue / note tool pages first, on all its notes
                raw, spots, tracks = whole_tools(src, raw, spots, tracks, ppq)
                came = None  # (the pages' notes aren't the steps any more)
                raw = np.column_stack([raw, raw[:, 0]])  # (each page note's start rides along through the cuts)
            raw, spots, tracks = cut_through(src, raw, spots, tracks, part, ppq, came)
            mine = in_part(spots, part)
            raw, tracks = raw[mine], tracks[mine] if tracks is not None else None
            if knife:
                raw, tracks = knife_cut(raw, tracks, knife, knife_d, ppq)
            if mould:
                raw, tracks = mould_cut(raw, tracks, mould, knife_d, ppq)
            return raw, tracks
        # (a velocity of its own on a piece of a shape with pages: its notes also as made with the velocities it had
        # when cut, kept where its own line didn't change: sliced.own_velocities)
        was = finish(with_whole_velocity(sh, vel_sh), raw, spots, tracks, came)[0][:, :4] \
            if not same_vel and (sh.get("glue") or sh.get("fx")) else None
        # (each note's own start rides along: a note the cut went through keeps its velocity in both halves, read
        # where the note started, user 2026-11-02; pages carry their velocity column themselves)
        carry = not (sh.get("glue") or sh.get("fx")) and raw.shape[1] == 3
        if carry:
            raw = np.column_stack([raw, raw[:, 0]])
        raw, tracks = finish(sh, raw, spots, tracks, came)
        if carry:
            vel_at, raw = raw[:, 3].copy(), raw[:, :3]
        if raw.shape[1] == 5:  # (velocities made by the whole's pages: kept, unless the piece has its own)
            vel_at, raw = raw[:, 4].copy(), raw[:, :4]
            if same_vel:
                return raw, tracks
            ok = was is not None and was.shape == raw.shape and (was[:, :3] == raw[:, :3]).all()
            made, raw = (was if ok else raw)[:, 3], raw[:, :3]
    if own is not None:
        own = own[keep]
        if cycling(sh):  # (a merged shape with Colours: turns across it all, not its shapes' own colours)
            own[:, 1] = 0
        if sh.get("own_vel"):  # (the same note in two tracks stays twice: they can go to different channels)
            got = np.column_stack([raw, own])
            if not sh.get("extract"):  # (Extract's notes as loaded: doubles kept, plan step 11)
                got = unique_rows(got)
            if cycling(sh) and len(got):
                return got[:, :4], cycle_turns(sh, got[:, :3], ppq)
            return got[:, :4], got[:, 4]
        got = unique_rows(np.column_stack([raw, own[:, 1]]))
        raw, tracks = got[:, :3], got[:, 3]
        if cycling(sh) and len(raw):
            tracks = cycle_turns(sh, raw, ppq)
    elif groups is not None:  # (the same note from two of them stays twice, like two shapes)
        got = unique_rows(np.column_stack([raw, np.asarray(groups, np.int64)[keep]]))
        raw, tracks = got[:, :3], got[:, 3]
    elif spots is None:
        raw = unique_rows(raw)
        if cycling(sh) and len(raw):  # "Colours" on a line / funnel
            tracks = cycle_turns(sh, raw, ppq, float(path[:, 1].min()))
    if made is not None and len(made) == len(raw):  # (a piece of a shape with pages, with a velocity of its own)
        # (read where each note started: a note the cut went through keeps its velocity in both halves, sweep 11-06)
        at = from0_starts(sh, raw, vel_at if vel_at is not None and len(vel_at) == len(raw) else raw[:, 0], ppq)
        frac = np.clip((at - t_lo) / (t_hi - t_lo), 0, 1) if t_hi > t_lo else np.zeros(len(raw))
        vel = own_velocities(vel_sh, made, frac)
    else:
        carried = vel_at is not None and len(vel_at) == len(raw)
        # (a cut note's start before a piece's own time: read on the line kept from before it, joined.piece_velocity)
        vel = env_velocities(env, from0_starts(sh, raw, vel_at if carried else raw[:, 0], ppq), t_lo, t_hi,
                             min(0.0, float(env[0][0])) if carried and not same_vel else 0.0)
    if sh["kind"] in HZ_KINDS and own is None and (sh.get("hz") or {}).get("tones"):  # Hz bass velocity effects
        got = velocity_parts(sh, ppq, raw[:, 0], raw[:, 2])
        if got is not None:  # (a note with a loudness line drawn: its own velocity instead of the shape's)
            factor, mine = got
            vel = np.clip(np.floor(np.where(mine, 127, vel) * factor + 0.5), 1, 127).astype(np.int64)
    return np.column_stack([raw, vel]), tracks


# ---------------------------------------------------------------- overlaps and channels

def assign_slots(note_lists, split="key", apart=(), first=None, taken=None, shape_of=None, step=15, loose=(),
                 extra=None):
    """
    Auto channels: shapes whose notes overlap get different slots, shapes that don't clash reuse the lowest
    free one. Earlier shapes get the lower slots. split="key": only notes on the same key at the same time
    clash; split="time": any notes sounding at the same time clash, whatever their key.
    apart: groups of note list numbers that always get different slots (a custom shape's outline and inside).
    first: per list None, or the tick to count it as starting at instead (a gate aware flip's colours: where they
    start unflipped).
    taken: {list number: its slot}, lists whose slot is set already (the others clashing with them step round it).
    shape_of: per list the shape it belongs to (Settings → General "Overlapping shapes keep their colours"): each shape
    gets its slots as if alone, and one clashing with an earlier shape moves WHOLE by step slots (15, 16 with channel
    10 used) until nothing clashes: the same colours on the next row of channels (user 2026-10-18). A taken list of
    the same shape stays and its colour counts as used. loose: shapes whose slots are moved again after (Colours
    rounds on channels of their own): never moved, nor stepped round.
    extra: {list number: (start, end) ticks} it also counts as sounding then ON EVERY KEY, either split (a Hz bass's
    CC lines change the whole channel: sent before its first note and on after its sound, hz_cc.cc_spans).
    """
    n = len(note_lists)
    extra = {i: span for i, span in (extra or {}).items() if span[1] > span[0]}

    def span_rows(span, owner):
        """An extra span as stretches clashing with anything then: one on each key (split="time": all key 0)."""
        keys = np.arange(ALL_KEYS, dtype=np.int64) if split == "key" else np.zeros(1, np.int64)
        return np.column_stack([keys, np.full(len(keys), span[0]), np.full(len(keys), span[1]),
                                np.full(len(keys), owner)]).astype(np.int64)

    def rows(i):
        """List i's stretches (stretches), its extra ones too."""
        got = stretches(note_lists[i], i, split)
        return np.concatenate([got, span_rows(extra[i], i)]) if i in extra else got

    busy = range(n)
    group = list(range(n))
    for g in apart:
        low = min(g, default=0)  # (once per group: a 4,096-colour shape made this 67 M steps)
        for a in g:
            group[a] = low
    if group != list(range(n)):
        # a group's own lists get slots apart anyway (a shape's colours take turns note by note: lots of stretches):
        # only lists whose group, joined, clashes with another group can clash with anything
        whole = {}
        for i, notes in enumerate(note_lists):
            whole.setdefault(group[i], []).append(notes)
        joined = [stretches(np.concatenate(ls), g, split) for g, ls in whole.items()]
        joined += [span_rows(span, group[i]) for i, span in extra.items()]
        clashing = set(np.concatenate(clash_arrays(np.concatenate(joined))).tolist())
        busy = [i for i in range(n) if group[i] in clashing]
    parts = [rows(i) for i in busy]
    # each list's clashes as one array (near(i)): thousands of shapes on top of each other = millions of pairs
    a, b = clash_arrays(np.concatenate(parts) if parts else np.zeros((0, 4), np.int64))
    starts, ends = np.concatenate([b, a]), np.concatenate([a, b])
    by = np.argsort(starts, kind="stable")
    ends = ends[by]
    at = np.searchsorted(starts[by], np.arange(n + 1)).tolist()
    # apart groups kept as groups, not as every pair in them (a shape with 4,096 colours = 16 M pairs; R:\333.spwb
    # took 5 s here): near(i) = its clashes + its groups' lists (itself too: it never has a slot yet when asked)
    member = {}
    for g in apart:
        g = np.asarray(sorted(set(g)), np.int64)
        if len(g) > 1:
            for a in g.tolist():
                member.setdefault(a, []).append(g)

    def near(i):
        own = member.get(i)
        return np.concatenate([ends[at[i]:at[i + 1]]] + own) if own else ends[at[i]:at[i + 1]]

    def alone(i):  # (clashes with nothing and in no group: most shapes in a big project)
        return at[i] == at[i + 1] and i not in member

    first = [f if f is not None else int(notes[:, 0].min()) if len(notes) else math.inf
             for notes, f in zip(note_lists, first or [None] * n)]
    order = sorted(range(n), key=lambda i: (first[i], i))
    slots, done = np.zeros(n, np.int64), np.zeros(n, bool)
    for i, s in (taken or {}).items():
        slots[i] = s
        done[i] = True
    if shape_of is not None:
        shapes, fixed = {}, {}  # (shape -> its lists, shapes in order of their first note; -> its lists with a slot)
        for i in order:
            if not done[i]:
                shapes.setdefault(shape_of[i], []).append(i)
        for i in np.flatnonzero(done).tolist():  # (an Add between step turned into plain notes: its group's other
            fixed.setdefault(shape_of[i], []).append(i)  # steps step round its colour)
        roomy = np.array([s not in loose for s in shape_of], bool)  # (shapes stepped round)
        own, pinned = np.full(n, -1, np.int64), np.zeros(n, bool)  # (-1: not this shape's)
        for key, mine in shapes.items():
            pins = fixed.get(key)  # (its slots as if no other shape were there)
            if pins:
                pinned[pins] = True
            for i in mine:
                if alone(i):
                    own[i] = 0
                    continue
                j = near(i)
                own[i] = least_free(np.concatenate([own[j], slots[j[pinned[j]]] % step]))
            rows = set()
            if key not in loose:  # (its slots move again anyway: in_rounds)
                for i in mine:
                    if alone(i):
                        continue
                    j = near(i)
                    d = slots[j[done[j] & ~pinned[j] & roomy[j]]] - own[i]
                    rows.update((d[(d >= 0) & (d % step == 0)] // step).tolist())
            # (a shape with a part pinned: from that part's row, so the rest stays with it when nothing clashes
            # any more; sweep 2026-11-06)
            row = min(int(slots[p]) // step for p in pins) if pins else 0
            while row in rows:
                row += 1
            if pins:
                pinned[pins] = False
            for i in mine:  # (one by one: quicker than array steps for the usual few lists a shape)
                slots[i] = own[i] + row * step
                done[i] = True
                own[i] = -1
        return slots.tolist()
    if member:
        # (a group whose lists clash with nothing and are in no other group: each takes the next slot in turn, in
        # order, as the loop below would give them one by one; 337.spwb: 8,192 lists in two groups)
        place = np.empty(n, np.int64)
        place[order] = np.arange(n)
        lone = np.asarray([at[i] == at[i + 1] and not done[i] and len(member[i]) == 1 if i in member else False
                           for i in range(n)], bool)
        for g in apart:
            a = next(iter(g), None)
            g = member[a][0] if a in member and len(member[a]) == 1 else None
            if g is not None and lone[g].all() and not done[g].any():
                slots[g[np.argsort(place[g])]] = np.arange(len(g))
                done[g] = True
    for i in order:
        if not done[i]:
            if not alone(i):  # (else slot 0)
                j = near(i)
                slots[i] = least_free(slots[j[done[j]]])
            done[i] = True
    return slots.tolist()


def least_free(used):
    """The smallest whole number 0, 1, 2... not in `used` (an array; numbers below 0 don't count)."""
    if len(used) < 64:
        used = set(used.tolist())
        k = 0
        while k in used:
            k += 1
        return k
    # (marks, no sort: the answer is at most len(used); 16,384 lists of 4,096 each spent 1.7 s sorting)
    seen = np.zeros(len(used) + 1, bool)
    seen[used[(used >= 0) & (used < len(seen))]] = True
    return int(np.argmin(seen))


def stretches(notes, owner, split="key"):
    """One list's notes as (key, start, end, owner) rows, its notes on a key joined into stretches (back-to-back spam
    = one stretch): anything with a length that overlaps a stretch overlaps one of its notes. Notes without a length
    stay on their own. split="time": every note counts as key 0."""
    fast = loops()
    if fast and split != "key" and len(notes):
        # (by key first: notes mostly come by key and start, so that's quick, and far fewer stretches come out; those
        # joined across the keys are the same stretches, touching ones joining either way)
        notes = stretches(notes, owner, "key")[:, [1, 2, 0]]
    if fast:  # (the compiled loop: the same rows; a spam shape's notes mostly come by key and start already)
        notes = np.ascontiguousarray(notes, np.int64)
        out, m = fast.note_stretches(notes, split == "key", owner, np.zeros(0, np.int64))
        if m < 0:
            key = np.ascontiguousarray(notes[:, 2]) if split == "key" else np.zeros(len(notes), np.int64)
            s = np.ascontiguousarray(notes[:, 0])
            out, m = fast.note_stretches(notes, split == "key", owner, fast.key_order_cores(key, s, s, s))
        return shorter(out, m)
    flat = notes[:, 1] <= notes[:, 0]
    key = notes[:, 2] if split == "key" else np.zeros(len(notes), np.int64)
    out = [np.column_stack([key[flat], notes[flat, 0], notes[flat, 1], np.full(int(flat.sum()), owner, np.int64)])]
    a, key = (notes[~flat], key[~flat]) if flat.any() else (notes, key)
    if len(a):
        s, e, k = a[:, 0], a[:, 1], key
        if not ((k[1:] > k[:-1]) | (k[1:] == k[:-1]) & (s[1:] >= s[:-1])).all():  # (else in that order already)
            order = np.lexsort((s, k))
            s, e, k = s[order], e[order], k[order]
        run = running_max(e, k)
        new = np.ones(len(s), bool)
        new[1:] = (k[1:] != k[:-1]) | (s[1:] > run[:-1])
        at = np.nonzero(new)[0]
        out.append(np.column_stack([k[at], s[at], np.maximum.reduceat(e, at), np.full(len(at), owner, np.int64)]))
    return np.concatenate(out).astype(np.int64).reshape(-1, 4)


CLASH_TABLE = 1 << 28  # (bytes the compiled sweep's table of owner pairs may take: about 46,000 clashing owners)


def clash_pairs(items, chunk=1 << 22):
    """(key, start, end, owner) stretches -> the set of (a, b) owner pairs (a < b) that clash (clash_arrays)."""
    a, b = clash_arrays(items, chunk)
    return set(zip(a.tolist(), b.tolist()))


def clash_arrays(items, chunk=1 << 22):
    """(key, start, end, owner) stretches -> arrays a, b of the owner pairs (a < b) that clash, sorted by a then b:
    on the same key, one starting before the other ends (in start, end, owner order; one starting where the other
    ends doesn't clash, nor does a stretch without a length). Worked out in chunks of about `chunk` pairs at a
    time."""
    if not len(items):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    fast = loops()
    if fast:  # (the compiled sweep: the same pairs; owners numbered 0, 1, 2... among those here, then back)
        names, inv = np.unique(items[:, 3], return_inverse=True)
        rows = np.array(items, np.int64)
        rows[:, 3] = inv.ravel()
        a, b, ok = fast.clash_pairs(rows, len(names), CLASH_TABLE)
        if ok:
            return names[a].astype(np.int64), names[b].astype(np.int64)
    order =np.lexsort((items[:, 3], items[:, 2], items[:, 1], items[:, 0]))
    k, s, e, o = (items[order, c] for c in range(4))
    vals, rank = np.unique(np.concatenate([s, e]), return_inverse=True)  # (ticks as ranks: no overflow)
    rank = rank.ravel()
    row = (k - k.min()) * len(vals)
    ps, pe = row + rank[:len(s)], row + rank[len(s):]
    cnt = np.maximum(np.searchsorted(ps, pe, "left") - np.arange(len(ps)) - 1, 0)  # later ones starting before its end
    total = np.cumsum(cnt)
    n = int(o.max()) + 1
    kept, pile, piled, start = np.zeros(0, np.int64), [], 0, 0
    while start < len(ps):
        stop = max(start + 1, int(np.searchsorted(total, (total[start - 1] if start else 0) + chunk, "right")))
        c = cnt[start:stop]
        if c.any():
            i = np.repeat(np.arange(start, stop), c)
            j = i + 1 + np.arange(len(i)) - np.repeat(np.cumsum(c) - c, c)
            a, b = o[i], o[j]
            m = a != b
            pile.append(np.unique(np.minimum(a[m], b[m]) * n + np.maximum(a[m], b[m])))
            piled += len(pile[-1])
            if piled > max(len(kept), chunk):  # (the same pair on every key: repeats dropped as it goes, not at the
                kept = np.unique(np.concatenate([kept] + pile))  # end: 2,000 stacked squares held 13 GB)
                pile, piled = [], 0
        start = stop
    code = np.unique(np.concatenate([kept] + pile))
    return code // n, code % n


def shorter(out, m):
    """A compiled loop's result rows out[:m]: its first rows as they are (no copy: a 5 M-note overlap fix copied all
    229 MB again when 76 notes went, 2026-11-09), unless most of the room would be wasted, then a copy of them."""
    if m == len(out):
        return out
    return out[:m] if m * 10 >= len(out) * 9 else out[:m].copy()


def running_max(values, groups):
    """The largest value so far, starting over in every group (groups: sorted numbers, each group together)."""
    if not len(values):
        return values
    lo = values.min()
    shift = int(values.max() - lo) + 1
    g = (groups - groups.min()) * shift
    return np.maximum.accumulate(values - lo + g) - g + lo


def resolve_overlaps(notes):
    """
    Notes on the same pitch and slot that overlap: the earlier note is cut where the later one starts,
    and the later one is stretched to where the earlier one would have ended, if that's further.
    Notes starting on the same tick become one note: the highest velocity wins, the longest length is kept.
    notes: array of (start, end, pitch, velocity, slot, owner) rows -> the fixed notes, grouped by slot and key
    (groups in the order they first show up), sorted inside each group.
    """
    if not len(notes):
        return notes
    fast = loops()
    if fast:  # (the compiled loops: the same notes)
        notes = np.ascontiguousarray(notes, np.int64)
        group, order = fast.overlap_order(notes)
        if not len(order):
            group = first_seen(notes[:, 4] * 256 + notes[:, 2])
            order = np.ascontiguousarray(overlap_order(notes, group), np.int64)
        out, m = fast.overlap_sweep(notes, order, group)
        return shorter(out, m)
    group = first_seen(notes[:, 4] * 256 + notes[:, 2])  # groups numbered in the order they first show up
    order = overlap_order(notes, group)
    a, group = np.take(notes, order, axis=0), group[order]  # (take: quicker than notes[order])
    s, e = a[:, 0], a[:, 1]
    run = running_max(e, group)  # everything before in the group sounds until here
    same = np.zeros(len(a), bool)
    same[1:] = group[1:] == group[:-1]
    before = np.concatenate([[0], run[:-1]])
    over = same & (s < before)  # starts while earlier notes still sound:
    end = np.where(over, np.maximum(e, before), e)  # stretched to where they would have ended
    cut = np.zeros(len(a), bool)
    cut[:-1] = over[1:]
    end[cut] = s[1:][over[1:]]  # and the one before is cut here
    last = np.ones(len(a), bool)
    last[:-1] = ~same[1:]
    a[:, 1] = end
    keep = last | (end > s)
    return a if keep.all() else a[keep]


def fixed_already(notes):
    """True when resolve_overlaps(notes) would give back exactly these rows, in this order: each slot + key's notes
    together in one block, in it every start later than the one before, every note but the block's last ending
    after its start and no later than the next one starts (nothing to cut, stretch, merge or reorder). A paste
    whose own overlaps were fixed already (heard_notes) comes to render so: its 5 M notes 0.07 s + 240 MB saved."""
    n = len(notes)
    if n < 2:
        return True
    g = notes[:, 4] * 256
    g += notes[:, 2]
    same = g[1:] == g[:-1]
    heads = g[np.flatnonzero(~same) + 1]
    first = int(g[0])
    del g
    if len(heads) > n // 4 + 64:  # (blocks this small: the sweep is quick anyway)
        return False
    if len(np.unique(np.append(heads, first))) != len(heads) + 1:  # (a slot + key coming back later)
        return False
    s, e = notes[:, 0], notes[:, 1]
    ok = s[1:] > s[:-1]
    ok &= e[:-1] <= s[1:]
    ok &= e[:-1] > s[:-1]
    ok |= ~same
    return bool(ok.all())


OVERLAP_WAYS = ("stretch", "split")  # (render's way: Single's cut + stretch / split around)


def split_overlaps(notes, stretch=False):
    """Single channel's "split around" way: notes on the same pitch and slot that overlap: the one starting later
    wins (as resolve_overlaps), the earlier one is cut where it starts and what's left of it after the winner ends
    comes back as a note of its own (resolve_overlaps stretches the winner over it instead: stretch=True gives
    the same notes). Notes starting on the same tick become one note first, as there. Every piece is kept, however
    short (user 2026-11-06)."""
    if not len(notes):
        return notes
    notes = np.asarray(notes, np.int64)
    group = first_seen(notes[:, 4] * 256 + notes[:, 2])
    order = overlap_order(notes, group)  # (by group, start; the same start: the loudest last, the longest first)
    a, g = notes[order], group[order]
    lead = np.ones(len(a), bool)  # (the first of each group + start: the one row the same start becomes)
    lead[1:] = (g[1:] != g[:-1]) | (a[1:, 0] != a[:-1, 0])
    at = np.flatnonzero(lead)
    if len(at) == len(a):  # (no two share a start: every row is its own)
        return winners(a, g, -a[:, 0], stretch, notes)
    last = np.append(at[1:], len(a)) - 1
    one = a[last].copy()  # (the loudest of the same start, with the longest length)
    one[:, 1] = np.maximum.reduceat(a[:, 1], at)
    g = g[last]
    return winners(one, g, -one[:, 0], stretch, notes)


def across_tracks(notes, rank, stretch=False):
    """Multi channel's Remove overlaps (user 2026-11-06): notes on the same pitch on DIFFERENT slots that overlap:
    the slot with the lower rank wins (rank: per note, its slot's place in the track order), the other note is cut
    where the winner starts and comes back after it ends as a note of its own (stretch: the winner is stretched over
    that instead, like Single channel). Each slot's own overlaps must be fixed already. Every piece is kept, however
    short. rank may also be a function: note places -> their ranks (only their order counts), asked only for the
    notes that overlap."""
    if not len(notes):
        return notes
    notes = np.asarray(notes, np.int64)
    return winners(notes, notes[:, 2], rank if callable(rank) else np.asarray(rank, np.int64), stretch)


def winners(notes, group, rank, stretch=False, like=None):
    """split_overlaps / across_tracks: in each group (a key, or a slot + key), at every moment only the sounding note
    with the lowest rank sounds (no two with one rank overlap): each note becomes the pieces of its time where it
    wins. stretch: a piece of a note coming back after a winner is given to that winner instead (stretched over it).
    -> the notes, grouped as resolve_overlaps' (by slot + key in the order they first show up in like (default
    notes), by start inside each)."""
    s, e = notes[:, 0], notes[:, 1]
    # (only notes overlapping another in their group need the work; the rest stay as they are)
    if ((group[1:] > group[:-1]) | (group[1:] == group[:-1]) & (s[1:] >= s[:-1])).all():
        o = None  # (by group and start already: split_overlaps' notes)
        gs, ss, es = group, s, e
    else:
        o = np.lexsort((s, group))
        gs, ss, es = group[o], s[o], e[o]
    same = np.zeros(len(gs), bool)
    same[1:] = gs[1:] == gs[:-1]
    run = running_max(es, gs)
    before = np.concatenate([[0], run[:-1]])
    over = same & (ss < before) & (es > ss)
    touch = over.copy()
    touch[:-1] |= over[1:]  # (the note it overlaps isn't always the one just before: whole runs below)
    run_id = np.cumsum(~(same & (ss < before)))  # (runs of notes sounding one after another without a gap)
    busy = np.zeros(run_id[-1] + 1, bool)
    busy[run_id[touch]] = True
    if o is None:
        work = busy[run_id] & (es > ss)
    else:
        work = np.zeros(len(notes), bool)
        work[o] = busy[run_id] & (es > ss)
    like = notes if like is None else like
    if not work.any():
        return regrouped(notes, like)
    rest, w = notes[~work], np.flatnonzero(work)
    wg, ws, we, wr = (np.ascontiguousarray(a, np.int64) for a in (group[w], s[w], e[w],
                                                                  rank(w) if callable(rank) else rank[w]))
    fast = loops()
    if fast:  # (the compiled loops: the same pieces, without the stretches in between: 4x the notes, 4x the time)
        order = np.lexsort((ws, wg))
        note, ps, pe, m = fast.winner_pieces(wg, ws, we, wr, order)
        note, ps, pe = note[:m], ps[:m], pe[:m]
    else:
        note, ps, pe = winner_stretches(wg, ws, we, wr)
    out = notes[w[note]].copy()
    out[:, 0], out[:, 1] = ps, pe
    if stretch and len(out) > 1:  # (a note coming back after the winner: the winner stretched over it instead;
        back = out[:, 0] > ws[note]  # such a piece always follows its winner's, touching it)
        keep = np.flatnonzero(~back)
        ends = np.maximum.reduceat(out[:, 1], keep)
        out = out[keep]
        out[:, 1] = ends
    return regrouped(np.concatenate([rest, out]), like)


def winner_stretches(wg, ws, we, wr):
    """winners' pieces the NumPy way (fastloops.winner_pieces' twin): every start and end in a group = the edges of
    the little stretches where one note wins; each stretch's winner, the same note's stretches side by side joined.
    -> (each piece's note (place in wg...), start, end), by group, then time."""
    g0 = int(wg.min())
    gk = (wg - g0).astype(np.int64)
    t0 = int(min(ws.min(), 0))
    span = int(max(we.max(), 0)) - t0 + 1
    if span * (int(gk.max()) + 1) >= 1 << 62:  # (too far apart for one code: group numbers made smaller)
        _, gk = np.unique(gk, return_inverse=True)
        gk = gk.reshape(-1).astype(np.int64)
    # (sorted, repeats dropped: np.unique's hashing took 6x the time for 4x the notes, a sort ~10x less, 4x)
    edges = np.sort(np.concatenate([gk * span + (ws - t0), gk * span + (we - t0)]))
    edges = edges[np.concatenate([[True], edges[1:] != edges[:-1]])]
    i0 = np.searchsorted(edges, gk * span + (ws - t0))
    i1 = np.searchsorted(edges, gk * span + (we - t0))
    best = lowest_over(i0, i1, wr, len(edges))
    piece = np.flatnonzero(best[:-1] >= 0)
    piece = piece[edges[piece] // span == edges[piece + 1] // span]  # (a stretch inside one group)
    note = best[piece]
    first = np.ones(len(piece), bool)  # (stretches of one note next to each other = one piece)
    first[1:] = (note[1:] != note[:-1]) | (piece[1:] != piece[:-1] + 1)
    head = np.flatnonzero(first)
    tail = np.append(head[1:], len(piece)) - 1
    return note[head], edges[piece[head]] % span + t0, edges[piece[tail] + 1] % span + t0


def lowest_over(i0, i1, rank, size):
    """For each of size places: the note (its place in i0...) with the lowest rank among those covering it (note k
    covers places i0[k] .. i1[k] - 1), -1 for none. A tree of ranges (segment tree): each note written into at most
    2 blocks a level, ~20 levels, then the blocks pushed down to the places once; the work goes with the notes, not
    with how many places each covers (spreading every note over its places grew 5-8x for 4x the notes where they
    pile up: hunt 2026-11-06, the user wants the NumPy way within the speed rule without the compiled loops)."""
    out = np.full(size, -1, np.int64)
    if not len(i0) or size <= 0:
        return out
    perm = np.argsort(rank, kind="stable")  # (lowest rank first: each note's place in that order = its value)
    dense = np.empty(len(perm), np.int64)
    dense[perm] = np.arange(len(perm))
    width = 1 << max(0, int(size - 1).bit_length())
    none = np.iinfo(np.int64).max
    tree = np.full(2 * width, none, np.int64)
    back = perm[::-1]  # (highest value first: a block written twice in one level keeps the last = the lowest)
    lo, hi, val = i0[back] + width, i1[back] + width, dense[back]
    while len(lo):
        keep = lo < hi
        if not keep.all():
            lo, hi, val = lo[keep], hi[keep], val[keep]
        left, right = (lo & 1) == 1, (hi & 1) == 1
        tree[lo[left]] = val[left]  # (a block of one level never gets a block of another: plain writes)
        tree[hi[right] - 1] = val[right]
        lo, hi = (lo + left) >> 1, (hi - right) >> 1
    level = 1
    while level < width:  # (each block's lowest pushed down into its two halves, top to bottom)
        parent = tree[level:2 * level]
        tree[2 * level:4 * level:2] = np.minimum(tree[2 * level:4 * level:2], parent)
        tree[2 * level + 1:4 * level:2] = np.minimum(tree[2 * level + 1:4 * level:2], parent)
        level *= 2
    leaf = tree[width:width + size]
    got = leaf != none
    out[got] = perm[leaf[got]]
    return out


def regrouped(notes, like=None):
    """notes ordered as resolve_overlaps gives them: by slot + key in the order they first show up in like (the
    notes before the fix; default notes), then by start."""
    if not len(notes):
        return notes
    code = notes[:, 4] * 256 + notes[:, 2]
    g = None
    if like is None:
        g = first_seen(code)
    else:
        had = like[:, 4] * 256 + like[:, 2]
        top = max(int(had.max()), int(code.max())) + 1
        if top <= 4 * len(had) + 65536:  # (a table, no sort: first_seen's way over like's ids)
            rank = seen_ranks(had, top)
            g = rank[code]
            if int(g.min()) < 0:  # (an id like doesn't have: the sorted way below, as before)
                g = None
        if g is None:
            o = np.argsort(had, kind="stable")  # (np.unique's way, by one stable sort: its hashing grew too fast)
            sh = had[o]
            new = np.concatenate([[True], sh[1:] != sh[:-1]])
            ids, at = sh[new], o[new]  # (each id's first place: stable, so the earliest)
            place = np.empty(len(ids), np.int64)
            place[np.argsort(at, kind="stable")] = np.arange(len(ids))
            g = place[np.searchsorted(ids, code)]
    s = notes[:, 0]
    # (notes mostly come in time inside each group already: then a stable sort by group alone gives the same order
    # as by group + start, quicker; 337.spwb's 2 M notes 140 -> 60 ms; in that order already: a copy)
    if ((g[1:] > g[:-1]) | (g[1:] == g[:-1]) & (s[1:] >= s[:-1])).all():
        return notes.copy()
    o = np.argsort(g, kind="stable")
    gs, ss = g[o], s[o]
    if not ((gs[1:] == gs[:-1]) & (ss[1:] < ss[:-1])).any():
        return np.take(notes, o, axis=0)
    lo, wide = int(s.min()), int(s.max()) - int(s.min()) + 1
    if wide < 1 << 40 and int(g.max()) < 1 << 22:  # (one key, one sort: the same order as the lexsort, quicker)
        return notes[np.argsort((g << 40) | (s - lo), kind="stable")]
    return notes[np.lexsort((s, g))]


def first_seen(ids):
    """Each id (whole numbers, not negative) as a number: 0 for the id that shows up first, 1 for the next new
    one, and so on."""
    top = int(ids.max()) + 1
    if top > 4 * len(ids) + 65536:  # (too spread out for a table)
        _, first, where = np.unique(ids, return_index=True, return_inverse=True)
        return np.argsort(np.argsort(first))[where]
    return seen_ranks(ids, top)[ids]


def seen_ranks(ids, top):
    """A table 0..top-1: each id's number in the order ids first show up (first_seen), -1 for ids not there. No
    sort: an id's first place is where the table of first places points back at itself."""
    first = np.empty(top, np.int64)
    first[ids[::-1]] = np.arange(len(ids) - 1, -1, -1)  # (a repeated index keeps the last one written: the first)
    lead = first[ids] == np.arange(len(ids))  # (ids at their first place, in the order they show up)
    rank = np.full(top, -1, np.int64)
    new = ids[lead]
    rank[new] = np.arange(len(new))
    return rank


def overlap_order(notes, group):
    """The order resolve_overlaps works in: by group, then start; the same start: by velocity (the loudest comes
    last, so it's the one kept), then the longest first. The same as
    np.lexsort((-end, velocity, start, group)), but most notes come in nearly sorted already (each shape's notes
    key by key, in time), which one sort by group and start makes good use of; only notes that share a start need
    the rest."""
    s = notes[:, 0]
    if not len(s) or s.min() < 0 or s.max() >= 1 << 40 or group.max() >= 1 << 22:
        return np.lexsort((-notes[:, 1], notes[:, 3], s, group))
    key = (group << 40) | s
    order = np.argsort(key, kind="stable")
    key = key[order]
    tie = np.zeros(len(key), bool)
    tie[1:] = key[1:] == key[:-1]
    tie[:-1] |= tie[1:]
    at = np.flatnonzero(tie)
    if len(at):
        sub = order[at]
        order[at] = sub[np.lexsort((-notes[sub, 1], notes[sub, 3], key[at]))]
    return order


CHANNEL_MODES = ("raw", "single", "auto")
SPLITS = ("key", "time")


def mapped_slots(slots, use10):
    """assign_slots' slots (array) -> the slots used (render's use10)."""
    if use10 == "all":  # (channel 10 last of each 16: the first 15 as without it, so ticking changes no colour)
        return slots // 16 * 16 + LAST10[slots % 16]
    if use10 == "order":  # ("Channels 1-16 in order": slot k = channel k)
        return slots
    if use10:  # (0..8, 10..24, 26..40...: never a slot that is channel 10)
        return slots + (slots + 6) // 15
    return slots


def unmapped_slot(s, use10):
    """The assign_slots slot mapped_slots turns into slot s, or None (none does: channel 10 skipped). Worked out
    directly: plain-notes shapes on thousands of slots ask for each one (hunt: a search made every edit slow)."""
    block, ch = divmod(s, 16)
    place = ch if ch < 9 else ch - 1  # (its place among CHANNELS)
    if use10 == "all":
        return block * 16 + (15 if ch == 9 else place)
    if use10 == "order" or not use10:
        return s
    return None if ch == 9 else block * 15 + place


def pushed_solid(note_lists, pinned, fixed, split, step, use10=False):
    """render's pinned tracks with each "solid" shape's track that clashes with an earlier "solid" shape's on the same
    slot moved up by step slots (15, 16 with channel 10 used) until it's free: the same channel (colour), another
    track (user, 2026-10-17: a copy of plain notes ate the original's notes). step 1 (Settings → General "Overlapping
    shapes keep their colours" off; user 2026-10-20: 352 path copies made 646 channels, most empty): the next free
    slot in the order the other shapes take them (with "Use channel 10": channel 10 last of each 16, unless "1-16 in
    order"; user 2026-10-20), never one they skip (channel 10, unmapped_slot). A moved track never lands on a slot
    the same shape's other tracks use (hunt 2026-10-20: two colours folded onto one channel). A track first tries
    its slots one by one against that slot's tracks only (a user's 32 stacked 256-key pastes: comparing each of
    3,640 tracks with every placed note took 34 s); after a few clashing slots, every slot it clashes on is found
    in one go (hunt: trying slot after slot, 200 copies x 15 colours took 286 s)."""
    placed = PlacedSpans()
    out = list(pinned)
    for i, (lst, pin) in enumerate(zip(note_lists, pinned)):
        if fixed[i] != "solid" or pin is None or not len(lst):
            continue
        ids, which = unique_inverse(pin)
        now = ids.tolist()  # (the shape's tracks' slots: moved ones' new slots, the rest as they are)
        order = np.argsort(which, kind="stable")  # (each track's notes in their own order, without a mask each)
        at = np.searchsorted(which[order], np.arange(len(now) + 1))
        for k, s in enumerate(ids.tolist()):
            mine = sorted_spans(lst[order[at[k]:at[k + 1]]], split)
            others, bad, tried = set(now) - {s}, None, 0
            while s in others or (placed.clashes(mine, s) if bad is None else s in bad):
                tried += s not in others  # (only real clashes count: stepping over the shape's own slots is free)
                if bad is None and tried >= 8:
                    bad = placed.clashing(mine)
                u = unmapped_slot(s, use10) if step == 1 else None
                if u is not None:
                    s = int(mapped_slots(np.array([u + 1]), use10)[0])
                    continue
                s += step
                while step == 1 and unmapped_slot(s, use10) is None:
                    s += 1
            placed.add(mine, s)
            now[k] = s
        out[i] = np.asarray(now, pin.dtype)[which]
    return out


SPAN_KEY, SPAN_OFF = 1 << 42, 1 << 40  # (sorted_spans: key x SPAN_KEY + tick + SPAN_OFF, ticks never reach 2^40)


def sorted_spans(notes, split):
    """A track's stretches (stretches: what overlaps them overlaps its notes) as sorted, apart (start, end) codes
    with the key in them (split "time": all key 0); ones without a length dropped (they clash with nothing)."""
    st = stretches(notes, 0, split)
    st = st[st[:, 2] > st[:, 1]]
    base = st[:, 0] * SPAN_KEY + SPAN_OFF
    order = np.argsort(base + st[:, 1], kind="stable")
    return (base + st[:, 1])[order], (base + st[:, 2])[order], st[:, 1].min(initial=0), st[:, 2].max(initial=0)


class PlacedSpans:
    """pushed_solid's tracks placed so far. Every slot's spans together (by_slot): on one slot they never overlap
    (placed only where they don't clash), so they stay sorted, apart spans. And all spans in a few runs sorted by
    start (runs: each about half the one before, two alike in size merged), with each run's longest span, so a track
    is only compared with the spans starting near its own (a user's 64 stacked 256-key pastes: comparing every
    placed note for each track took 56 s)."""

    def __init__(self):
        self.by_slot = {}  # slot -> (lo, hi, first tick, last tick)
        self.runs, self.coming = [], []  # (lo sorted, hi, slot, longest span); tracks not in runs yet

    def add(self, mine, slot):
        lo, hi, t0, t1 = mine
        if len(lo):
            self.coming.append((lo, hi, slot))
            had = self.by_slot.get(slot)
            if had is not None:
                lo, hi = np.concatenate([had[0], lo]), np.concatenate([had[1], hi])
                order = np.argsort(lo, kind="stable")
                lo, hi, t0, t1 = lo[order], hi[order], min(t0, had[2]), max(t1, had[3])
            self.by_slot[slot] = (lo, hi, t0, t1)

    def clashes(self, mine, slot):
        """Does mine clash with the tracks placed on slot?"""
        lo, hi = mine[0], mine[1]
        had = self.by_slot.get(slot)
        if had is None or not len(lo):
            return False
        at = had[1].searchsorted(lo, "right")  # (the slot's first span ending after each of mine starts)
        ok = at < len(had[0])
        return bool((had[0][at[ok]] < hi[ok]).any())

    def clashing(self, mine):
        """The slots of placed tracks that clash with mine (same key, overlapping: touching doesn't)."""
        lo, hi = mine[0], mine[1]
        out = set()
        if not len(lo):
            return out
        for plo, phi, slot in self.coming:  # (made only when asked: most tracks find a free slot without it)
            self.runs.append((plo, phi, np.full(len(plo), slot, np.int64), int((phi - plo).max())))
            while len(self.runs) > 1 and len(self.runs[-2][0]) <= 2 * len(self.runs[-1][0]):
                (alo, ahi, aslot, al), (blo, bhi, bslot, bl) = self.runs[-2:]
                rlo = np.concatenate([alo, blo])
                order = np.argsort(rlo, kind="stable")
                self.runs[-2:] = [(rlo[order], np.concatenate([ahi, bhi])[order],
                                   np.concatenate([aslot, bslot])[order], max(al, bl))]
        self.coming = []
        for plo, phi, slot, longest in self.runs:
            i0 = plo.searchsorted(lo - longest, "right")  # (a span starting longest or more before mine ends by then)
            n = np.maximum(plo.searchsorted(hi, "left") - i0, 0)
            total = int(n.sum())
            if total > len(plo):  # (long spans: each of the run's spans looked up in mine instead)
                at = hi.searchsorted(plo, "right")  # (mine's first span ending after each placed one starts)
                hit = at < len(lo)
                hit[hit] = lo[at[hit]] < phi[hit]
                out.update(np.unique(slot[hit]).tolist())
            elif total:
                ends = np.cumsum(n)
                at = np.repeat(i0 - ends + n, n) + np.arange(total)  # (every span starting near each of mine)
                hit = phi[at] > np.repeat(lo, n)
                out.update(np.unique(slot[at[hit]]).tolist())
        return out


def in_rounds(note_lists, slot_of, rounds, step, teams=None, stay=None, together=None):
    """render's slots with "New channels each Colours round" (user 2026-10-17: each round editable alone in other
    programs): every round of every shape with rounds, in shape order, gets step slots of its own past the ones the
    other shapes use, each note at the channel (colour) it has without the tick. -> (slots, slot count).
    The other shapes' blocks are left free of rounds, the rest are filled in order: a plain-notes shape frozen on
    round channels keeps them and the shapes around it keep theirs. A round needing more than step slots (a
    layered Hz bass's clash tracks) takes as many blocks. teams (render's): the steps of an Add between group take
    their rounds together, like one shape (together: the shapes with one key alike). stay: per shape True if its
    first round keeps the slots it has without
    the tick, only the later rounds moving (colours given by hand, custom.paint_rounds: a colour used once never
    moves, hunt)."""
    stay = stay or [False] * len(rounds)
    taken = set()
    for s, lst, r, st in zip(slot_of, note_lists, rounds, stay):
        if (r is None or st) and len(lst):
            s = np.broadcast_to(np.asarray(s, np.int64), len(lst))
            taken.update((np.unique(s if r is None else s[np.asarray(r) == 0]) // step).tolist())
    free = (b for b in itertools.count() if b not in taken)
    owners = {}  # (a shape, or an Add between group -> its shapes)
    for i, (lst, r) in enumerate(zip(note_lists, rounds)):
        if r is not None and len(lst):
            team = teams[i] if teams else None
            own = ("team", team[0]) if team is not None else (
                ("with", together[i]) if together and together[i] is not None else i)
            owners.setdefault(own, []).append(i)
    out = list(slot_of)
    for mine in owners.values():
        s = np.concatenate([np.broadcast_to(np.asarray(slot_of[i], np.int64), len(rounds[i])) for i in mine])
        rr = np.concatenate([rounds[i] for i in mine])
        move = rr != 0 if any(stay[i] for i in mine) else np.ones(len(s), bool)
        got = s.copy()
        if move.any():
            sm = s[move]
            q = sm // step - int(sm.min()) // step  # (the block within a round)
            wide = int(q.max()) + 1
            had, r = np.unique(rr[move], return_inverse=True)  # (only the rounds it has, no gaps: a piece sliced
            # off mid-way keeps the whole shape's round numbers, user 2026-10-18)
            blocks = np.fromiter(itertools.islice(free, len(had) * wide), np.int64)
            got[move] = sm % step + step * blocks[r.reshape(-1) * wide + q]
        at = np.cumsum([0] + [len(rounds[i]) for i in mine])
        for k, i in enumerate(mine):
            out[i] = got[at[k]:at[k + 1]]
    return out, max([int(np.max(s)) + 1 for s, lst in zip(out, note_lists) if len(lst)] or [0])


def render(note_lists, mode, split="key", tracks=None, apart=None, fixed=None, use10=False, teams=None, pastes=None,
           held=None, back=None, ordered=None, rounds=None, keep=False, stay=None, voice_slots=None, busy=None,
           together=None, note_slots=None, kept=None, groups=None, overlaps=None, slots_only=False):
    """
    note_lists: shape_notes() of every shape -> (final notes, number of slots used). The notes are an array of
    (start, end, pitch, velocity, slot, owner) rows, owner = the shape's number.
    mode: "raw" = one channel, notes kept as they are (overlaps allowed), "single" = one channel with overlaps
    fixed, "auto" = overlapping shapes get their own channels (split: see assign_slots).
    tracks: per shape None, or the track of each of its notes (pasted notes, shape_notes_tracks): with "auto" each
    track of the shape gets channels as if it were a shape of its own.
    apart: per shape True if its tracks must get different channels (pasted notes, Fill / Spam "Outline").
    fixed: per shape True if its tracks ARE its slots, whatever the mode (a picture's colours: slot k = the k-th
    colour of the project's picture colours, so its channel never changes). "solid": the same, and with "auto" the
    other shapes clashing with its notes take other slots (Turn into plain notes: it stands for the shape it was).
    use10: the pictures use channel 10 too (slot k = channel k, slot_track_channel): the other shapes' slots skip
    every slot that would be channel 10 (shapes never use it, user). "all" = the project's "Use channel 10": slots
    are channels too, but the shapes take channel 10 only as the 16th (LAST10), so ticking it changes no colour.
    "order" = the same with "Channels 1-16 in order": the shapes take channel 10 as their 10th.
    teams: per shape None, or (group, turn) for an Add between group giving each shape its own colour (between.py):
    with "auto" the group's shapes of one turn share a channel, and every turn of a group gets a different one (its
    shapes' own tracks are left out).
    pastes: per shape None, or the id of the paste it was sliced from (pasted notes, slice.slice_notes): with "auto"
    one track of all the pieces of a paste is ONE unit, so it keeps one channel across them.
    held: per shape True if it's sustained (sustain.py): its notes keep their overlaps in every mode, and other
    shapes' notes end where one of its notes on their key and slot starts.
    back: per shape True if it's gate flipped (gateflip.py colours_back): with "auto" its tracks get channels as if
    they started where they do unflipped (gateflip.unflipped_starts), so each colour keeps its unflipped channel. A
    paste's pieces: the whole paste's tracks counted that way, flipped pieces unflipped.
    ordered: per shape True if its tracks take channels in track order, all counted as starting at its first note (a
    Gate sensitive merge: track k = the slot its colour had before, merge.part_rows).
    rounds: per shape None, or each note's Colours round (custom.ROUND): with "auto" every round goes on channels of
    its own, the same colours (in_rounds). stay: per shape True if its first round keeps its slots (in_rounds). together: per shape None or a key: shapes
    with one key take their rounds together, like one shape (Channel per strip on pieces of one paste, in_rounds).
    keep: with "auto" a shape clashing with an earlier one moves whole to the next row of channels, its colours kept
    (assign_slots shape_of; an Add between group and a paste's pieces move as one shape).
    voice_slots: {shape's number: None} to be told, with "auto", which slot each of its tracks got: filled in as
    {track: slot} (a Hz bass with its own channel per note: each voice's CC lines go on its slot, hz_cc; pasted
    notes with passengers: each track's events on its slot, passengers.py).
    busy: per shape None, or {track (None: every track): (start, end) ticks}: with "auto" its tracks also count as
    sounding then on every key (a Hz bass's CC lines: their whole time, run-on too, hz_cc.cc_spans).
    note_slots: {shape's number: None} filled with each of its notes' slot (with "auto").
    kept: per shape True if its tracks keep a slot each outside "auto" too (a Domino paste: what you copied; "single"
    then fixes overlaps per slot).
    groups: per shape None or a key: with keep, shapes with one key move as one shape (the pieces of one cut shape).
    overlaps: how overlaps are fixed (overlap_fix; None = cut + stretch per slot, as always).
    slots_only: only the slots are wanted (note_slots / voice_slots): (None, number of slots), no notes made.
    """
    pastes = pastes or [None] * len(note_lists)
    tracks = tracks or [None] * len(note_lists)
    apart = apart or [False] * len(note_lists)
    fixed = fixed or [False] * len(note_lists)
    teams = teams or [None] * len(note_lists)
    back = back or [False] * len(note_lists)
    ordered = ordered or [False] * len(note_lists)
    firsts, paste_back, paste_first, paste_ordered = {}, set(), {}, set()  # (unit -> the tick it counts as starting at: assign_slots first)
    made = {}

    def whole_starts(i):
        """Unflipped starts of piece i of a gate flipped paste, its runs counted across all such pieces of the
        paste (sliced after the flip: a run went across the cut)."""
        if i not in made:
            mine = [j for j in range(len(note_lists)) if pastes[j] == pastes[i] and isinstance(back[j], str)
                    and back[j] == "whole"]
            got = unflipped_starts(np.concatenate([note_lists[j] for j in mine]))
            at = np.cumsum([0] + [len(note_lists[j]) for j in mine])
            made.update({j: got[at[n]:at[n + 1]] for n, j in enumerate(mine)})
        return made[i]

    pinned = [np.asarray(tr, np.int64) if fx and tr is not None else None for tr, fx in zip(tracks, fixed)]
    if mode == "auto":
        pinned = pushed_solid(note_lists, pinned, fixed, split, (16 if use10 else 15) if keep else 1, use10)
    top = max([int(p.max()) + 1 for p in pinned if p is not None and len(p)] or [0])
    if mode == "auto":
        units, unit_of, forced = [], [], []  # the shapes, pasted notes split up by track; unit_of = each note's unit
        team_unit, team_units = {}, {}  # (group, turn) -> its unit; group -> its units
        paste_unit, paste_units = {}, {}  # (paste, track) -> its unit; paste -> its units
        taken = {}  # (unit -> its slot before channel 10 is skipped: a "solid" pinned shape's tracks)
        solid_teams = []  # (unit, (group, turn)) of those tracks
        solid_cuts = []  # (unit, cut group) of those tracks: its fellow pieces keep its row (keep colours)
        told = {}  # (shape's number -> its track numbers, its first unit: voice_slots)
        extra = {}  # (unit -> the extra time it sounds: busy)
        for lst, tr, sep, pin, team, pid in zip(note_lists, tracks, apart, pinned, teams, pastes):
            spans = busy[len(unit_of)] if busy else None
            if pin is not None:
                if fixed[len(unit_of)] == "solid" and len(lst):
                    ids, which = unique_inverse(pin)
                    o = np.argsort(which, kind="stable")  # (each track's notes in their order: one sort, no masks)
                    cut, every = np.searchsorted(which[o], np.arange(len(ids) + 1)), lst[o]
                    for k, s in enumerate(ids.tolist()):
                        u = unmapped_slot(s, use10)
                        if u is not None:  # (on channel 10 while shapes skip it: no other shape takes it anyway)
                            taken[len(units)] = u
                            if team is not None:  # (a step turned into plain notes: the other turns step round it)
                                solid_teams.append((len(units), team))
                            elif groups and groups[len(unit_of)] is not None:  # (a sliced piece turned plain)
                                solid_cuts.append((len(units), groups[len(unit_of)]))
                            units.append(every[cut[k]:cut[k + 1]])
                unit_of.append(None)
                continue
            if team is not None:
                u = team_unit.get(team)
                if u is None:
                    u = team_unit[team] = len(units)
                    units.append(lst)
                    team_units.setdefault(team[0], []).append(u)
                else:
                    units[u] = np.concatenate([units[u], lst])
                unit_of.append(u)
                continue
            if tr is None or not len(lst):
                if spans and spans.get(None) and len(lst):
                    extra[len(units)] = spans[None]
                unit_of.append(len(units))
                units.append(lst)
                continue
            ids, which = unique_inverse(np.asarray(tr))
            if pid is not None:  # (a piece of a paste: each track joins that track's unit in the other pieces)
                if pid not in paste_units:  # (all its tracks' units at once, in track order: channels as before the cut)
                    every = np.unique(np.concatenate([np.asarray(t2) for t2, p2 in zip(tracks, pastes)
                                                      if p2 == pid and t2 is not None]))
                    paste_units[pid] = list(range(len(units), len(units) + len(every)))
                    for t2 in every.tolist():
                        paste_unit[pid, t2] = len(units)
                        units.append(lst[:0])
                us = [paste_unit[pid, t] for t in ids.tolist()]
                b = back[len(unit_of)]
                if isinstance(b, np.ndarray):  # (recoloured: each note counted from its colour's first start, an array)
                    starts = b
                else:
                    starts = whole_starts(len(unit_of)) if b == "whole" else unflipped_starts(lst) if b else lst[:, 0]
                o = np.argsort(which, kind="stable")  # (each track's notes in their order: one sort, no masks)
                cut, every = np.searchsorted(which[o], np.arange(len(ids) + 1)), lst[o]
                first = np.minimum.reduceat(np.asarray(starts)[o], cut[:-1])
                for k, u in enumerate(us):
                    units[u] = np.concatenate([units[u], every[cut[k]:cut[k + 1]]])
                    s0 = int(first[k])
                    paste_first[u] = min(paste_first.get(u, s0), s0)
                if ordered[len(unit_of)]:  # (pasted notes: the whole paste's tracks in track order, below)
                    paste_ordered.add(pid)
                if voice_slots is not None and len(unit_of) in voice_slots:  # (its passengers' tracks)
                    told[len(unit_of)] = (ids, us)
                for k, t in enumerate(ids.tolist() if spans else ()):  # (its CCs' time: the whole paste's track)
                    span = spans.get(t)
                    if span:
                        had = extra.get(us[k], span)
                        extra[us[k]] = (min(had[0], span[0]), max(had[1], span[1]))
                unit_of.append(np.asarray(us, np.int64)[which])
                if b is not None and b is not False:  # (gate flipped / recoloured piece: the whole paste's tracks by
                    # where they start unflipped / as counted, below)
                    paste_back.add(pid)
                continue
            if voice_slots is not None and len(unit_of) in voice_slots:
                told[len(unit_of)] = (ids, list(range(len(units), len(units) + len(ids))))
            if spans:
                for k, t in enumerate(ids.tolist()):
                    span = spans.get(t, spans.get(None))
                    if span:
                        extra[len(units) + k] = span
            unit_of.append(len(units) + which)
            if sep:
                forced.append(range(len(units), len(units) + len(ids)))
            if len(ids) > 1:  # (each track's notes in their order: one sort by track, not a pass per track)
                o = key_sort(which.astype(np.int64), np.arange(len(which)))
                cut = np.searchsorted(which[o], np.arange(len(ids) + 1))
                every = lst[o]
                mine = [every[a:b] for a, b in zip(cut[:-1].tolist(), cut[1:].tolist())]
            else:
                mine = [lst]
            b = back[len(unit_of) - 1]
            if b is not None and b is not False:  # (each colour's channel as unflipped: counted from where it
                starts = np.full(len(ids), np.iinfo(np.int64).max)  # starts then; recoloured: as before, an array)
                np.minimum.at(starts, which, b if isinstance(b, np.ndarray) else unflipped_starts(lst))
                for k in range(len(ids)):
                    firsts[len(units) + k] = int(starts[k])
            if ordered[len(unit_of) - 1]:  # (one start for all: ties go by unit, so by track)
                s0 = int(lst[:, 0].min())
                for k in range(len(ids)):
                    firsts[len(units) + k] = s0
            units += mine
        for pid in paste_back:
            for u in paste_units[pid]:
                if u in paste_first:
                    firsts[u] = paste_first[u]
        for pid in paste_ordered:  # (one start for all: ties go by unit, made in track order)
            us = [u for u in paste_units[pid] if u in paste_first]
            s0 = min((paste_first[u] for u in us), default=0)
            for u in us:
                firsts[u] = s0
        forced += [us for us in team_units.values() if len(us) > 1]
        forced += [(p, u) for p, team in solid_teams for t, u in team_unit.items() if t[0] == team[0] and t != team]
        for p, team in solid_teams:  # (its own turn's shapes: its channel, as when it was one of them)
            if team in team_unit:
                taken.setdefault(team_unit[team], taken[p])
        forced += [us for us in paste_units.values() if len(us) > 1]  # (pasted tracks: always apart, tracks_apart)
        shape_of, loose = None, set()
        if keep:  # (each unit's shape: an Add between group / a paste = one shape)
            shape_of = list(range(len(units)))
            for i, u in enumerate(unit_of):
                if u is None:
                    continue
                key = ("team", teams[i][0]) if teams[i] is not None else ("paste", pastes[i]) if (
                    pastes[i] is not None and tracks[i] is not None) else ("cut", groups[i]) if (
                    groups and groups[i] is not None) else ("shape", i)
                for v in np.unique(np.asarray(u)).tolist():
                    shape_of[v] = key
            for p, team in solid_teams:  # (a step turned into plain notes still belongs to its group)
                shape_of[p] = ("team", team[0])
            for p, key in solid_cuts:  # (a sliced piece turned into plain notes still belongs to its whole: the
                shape_of[p] = ("paste", ("cut", key))  # pieces' own key, App's pastes ("cut", group))
            # (a shape whose first round keeps its slots (stay: Channel per strip, colours given by hand) isn't loose:
            # the others step round those, hunt 2026-10-23: a line over a strips song shared its channel)
            loose = {shape_of[v] for i, u in enumerate(unit_of) if u is not None and rounds and rounds[i] is not None
                     and not (stay and stay[i]) for v in np.unique(np.asarray(u)).tolist()}
        unit_slots = mapped_slots(np.array(assign_slots(units, split, forced, [firsts.get(u) for u in
                                                                              range(len(units))], taken, shape_of,
                                                        16 if use10 in ("all", "order") else 15, loose, extra),
                                           np.int64),
                                  use10)
        slot_of = [pin if u is None else unit_slots[u] for u, pin in zip(unit_of, pinned)]
        for i, (ids, us) in told.items():
            voice_slots[i] = dict(zip(ids.tolist(), unit_slots[us].tolist()))
        count = int(unit_slots.max()) + 1 if len(units) else 0
        if rounds and any(r is not None for r in rounds):
            slot_of, count = in_rounds(note_lists, slot_of, rounds, 16 if use10 else 15, teams, stay, together)
        for i in note_slots or ():  # (each note's slot, rounds done: Channel per strip's CCs, App.cc_messages)
            note_slots[i] = np.broadcast_to(np.asarray(slot_of[i], np.int64), len(note_lists[i]))
    else:  # (a Hz bass layer's clashing notes too: one channel, user)
        slot_of = [0 if pin is None else pin for pin in pinned]
        count = 1 if any(p is None for p in pinned) else 0
        # (a Domino paste's tracks take their slots as with "auto", among the other pastes and the pinned shapes,
        # so each keeps a channel and two pastes never share one: user 2026-11-01, "a paste acts like Multi
        # channel"; hunt 2026-11-01: two pastes merged; the other shapes stay on slot 0)
        sub = [i for i, (k, tr) in enumerate(zip(kept or (), tracks))
               if k and tr is not None and pinned[i] is None and len(tr)]
        if sub:
            pick = sorted(sub + [i for i, p in enumerate(pinned) if p is not None])
            got = {k: None for k, i in enumerate(pick) if i in set(sub)}
            told = {k: None for k, i in enumerate(pick) if voice_slots is not None and i in voice_slots}
            render([note_lists[i] for i in pick], "auto", split, [tracks[i] for i in pick],
                   [apart[i] for i in pick], [fixed[i] for i in pick], use10, None, [pastes[i] for i in pick],
                   None, [back[i] for i in pick], [ordered[i] for i in pick], keep=keep, voice_slots=told,
                   busy=[busy[i] for i in pick] if busy else None, note_slots=got, slots_only=True)
            for k, v in told.items():  # (its CC events' tracks: passengers.py)
                voice_slots[pick[k]] = v
            for k, s in got.items():
                if s is not None and len(s):
                    slot_of[pick[k]] = np.asarray(s, np.int64)
                    count = max(count, int(np.max(s)) + 1)
    count = max(count, top)
    if slots_only:  # (the notes themselves aren't wanted: their overlap fixing took 0.3 s on 3 M pasted notes)
        return None, count
    notes = np.empty((sum(len(lst) for lst in note_lists), 6), np.int64)
    at = 0
    for o, lst in enumerate(note_lists):
        part = notes[at:at + len(lst)]
        part[:, :4], part[:, 4], part[:, 5] = lst, slot_of[o], o
        at += len(lst)
    if mode != "raw":
        fix = overlap_fix(mode, overlaps)
        if held and any(held):  # (sustained shapes keep their overlaps; the others are cut where theirs start)
            mine = np.asarray(held, bool)[notes[:, 5]]
            kept = notes[mine]
            notes = np.concatenate([cut_by(fix(notes[~mine]), kept), kept])
        elif not (fix is resolve_overlaps and fixed_already(notes)):  # (nothing to fix: the same rows, no copy)
            notes = fix(notes)
    return notes, count


def remove_across(notes, held, overlaps, ranks):
    """Multi channel's Remove overlaps on the finished notes (App's all_notes, after the shapes laid on your tracks
    are in): every track's own overlaps fixed first (user 2026-11-06), then the track order decides between tracks.
    held: per note True if it keeps its overlaps (Sustain, Fill gaps' Keep both: left as they are, as in render).
    ranks: notes -> each note's place in the track list (earlier = lower). overlaps: overlap_fix's."""
    stretch = overlaps.get("way", "stretch") == "stretch"
    rest = notes[~held] if held.any() else notes
    rest = resolve_overlaps(rest) if stretch else split_overlaps(rest)
    if len(rest):
        rest = np.asarray(rest, np.int64)

        def ranked(w):
            """The ranks of rest's notes w (only those that overlap: their order is all that counts)."""
            # (places worked out from ALL the notes, held ones too: a track holding only Sustain notes still counts,
            # so your tracks after it keep their place, hunt 2026-11-07; then looked up by each note's slot)
            every = np.asarray(ranks(notes), np.int64)
            slots, at = np.unique(notes[:, 4], return_index=True)
            mine = rest[w]
            rank = every[at][np.searchsorted(slots, mine[:, 4])]
            rank = -rank if overlaps.get("later") else rank
            # (one track's channels share a place: between them the later start wins, Single channel's rule inside
            # one track (user 2026-11-06, hunt: the winner was left to chance and differed without the compiled
            # loops); then the lower slot: every note its own rank, so no tie is ever left)
            order = np.lexsort((mine[:, 4], -mine[:, 0], rank))
            rank = np.empty(len(mine), np.int64)
            rank[order] = np.arange(len(mine))
            return rank
        rest = across_tracks(rest, ranked, stretch)
    return np.concatenate([rest, notes[held]]) if held.any() else rest


def overlap_fix(mode, overlaps):
    """render's overlap fixing: overlaps = None (today's way) or {"way": OVERLAP_WAYS, "across": True for Multi
    channel's Remove overlaps, "rank": None or an array slot -> its place in the track order (default: the slot
    number), "later": True when later tracks win}. -> a function notes -> fixed notes."""
    o = overlaps or {}
    stretch = o.get("way", "stretch") == "stretch"
    if not (mode == "auto" and o.get("across")):
        return resolve_overlaps if stretch else split_overlaps

    def fix(notes):
        if not stretch:  # (each track's own first, user)
            notes = split_overlaps(notes)
        elif not fixed_already(notes):
            notes = resolve_overlaps(notes)
        if not len(notes):
            return notes
        table = o.get("rank")
        rank = notes[:, 4] if table is None else np.asarray(table, np.int64)[notes[:, 4]]
        return across_tracks(notes, -rank if o.get("later") else rank, stretch)
    return fix


def slot_track_channel(slot, use10=False):
    """Slot number -> (track index, MIDI channel 0-15), one channel per track, skipping the drum channel (use10:
    not skipping it: slot k = channel k; render keeps the other shapes off those slots). A track added by hand
    (OWN0): its own channel."""
    if slot >= OWN0:
        return slot, slot & 15
    if edo_lanes:
        return slot, int(EDO_SAFE[slot % len(EDO_SAFE)])
    return slot, slot % 16 if use10 else CHANNELS[slot % len(CHANNELS)]


def channels_of(slots, use10=False):
    """slot_track_channel's channels of many slots at once."""
    slots = np.asarray(slots, np.int64)
    if edo_lanes:
        ch = EDO_SAFE[slots % len(EDO_SAFE)]
    else:
        ch = slots % 16 if use10 else np.asarray(CHANNELS, np.int64)[slots % len(CHANNELS)]
    hand = slots >= OWN0
    return np.where(hand, slots & 15, ch) if hand.any() else ch


def ports_of(slots, use10, ports):
    """slot_port's ports of many slots at once, going round ports (a player's)."""
    slots = np.asarray(slots, np.int64)
    return np.where(slots >= OWN0, 0, slots // channel_round(use10) % ports)


PORTS = 16  # MIDI ports players tell apart (measured: port 16 = port 0)


def slot_port(slot, use10=False):
    """The port of a slot's track when ports are written (slot_ports): each round of the channels its own, so slots
    on one channel number never share a channel (16 rounds; a 17th is the 1st again). A track added by hand: port A
    (user 2026-10-26)."""
    if slot >= OWN0:
        return 0
    return slot // channel_round(use10) % PORTS


def slot_ports(slots, cc, use10=False):
    """True when the tracks get ports (MIDI export, .dms): only with CC lines going out (a Hz bass's: hz_cc) and
    more slots than one round of channels, so a CC can't reach another slot's notes on the same channel number (user
    2026-10-21, picked over sharing channels); without CC lines files stay as they always were. edo_lanes: whenever
    there are more than 5 slots (overlapping notes on one key and channel would cut each other)."""
    if edo_lanes:
        return slots > len(EDO_SAFE)
    return bool(cc) and slots > (16 if use10 else len(CHANNELS))
