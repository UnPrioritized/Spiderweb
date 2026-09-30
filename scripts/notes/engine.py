"""
Turns drawn shapes into MIDI notes: what a shape is, its notes, and fixing overlaps / picking channels.

The maths for each kind lives in its own file: paths.py (lines, polylines, freehand, curves), custom.py
(custom shapes), funnel.py (funnels), envelope.py (velocities).
"""

import json
import math

import numpy as np

from files.lang import tr
from notes.custom import (ALIGNS, ENDS,CUSTOM_DEFAULTS, CUSTOM_FLAGS, FILLS, BOX_STROKE, block_notes, check_notes,
                          clean_curve,
                          clean_strokes, custom_notes_groups, custom_strokes)
from notes.envelope import env_values, velocity_env
from notes.joined import clean_joined, is_joined, joined_paths
from notes.funnel import clean_funnel, clean_starts, funnel_notes, funnel_strokes, old_funnel
from notes.arc import arc_k, arc_points
from notes.claw import apply_claw, clean_claw
from notes.bezier import anchor_count, sample
from notes.paths import dedupe, dot_segment_notes, path_notes
from notes.pattern import FORMULA_KINDS, clean_pattern, clean_shape_formula, formed_paths
from notes.polygon import clean_polygon, polygon_strokes
from notes.smooth import clean_level, smooth_path
from notes.text import clean_text
from notes.tumour import LINE_KINDS, clean_tumour, tumour_path

KINDS = {"line": tr("engine.line"), "poly": tr("engine.polyline"), "free": tr("engine.freehand"),
         "curve": tr("engine.curve"), "arc": tr("engine.arc"), "custom": tr("engine.custom"),
         "funnel": tr("engine.funnel")}
POINT_NAMES = {"line": [tr("engine.a"), tr("engine.b")],
               "arc": [tr("engine.start"), tr("engine.through"), tr("engine.end")],
               "funnel": [tr("engine.line_start"), tr("engine.line_end"), tr("engine.wall_1"), tr("engine.wall_2")]}


def point_names(sh):
    """Names for the panel's point boxes, or None if the shape has none."""
    if sh["kind"] == "curve":  # anchors + handles (bezier.py); a plain curve: Start, Handle 1, Handle 2, End
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


# ---------------------------------------------------------------- shapes

def make_shape(kind, pts, defaults):
    sh = dict(defaults, kind=kind)
    if kind in ("line", "funnel"):
        sh["pts"] = [list(pts[0]), list(pts[-1])]
        if kind == "funnel":
            sh["starts"] = []  # the line only: the wall is drawn next, then the curves are added
    elif kind == "curve":
        a, b = pts[0], pts[-1]
        mid = (a[0] + b[0]) / 2
        sh["pts"] = [list(a), [mid, a[1]], [mid, b[1]], list(b)]  # gentle S-curve to start with
    else:
        sh["pts"] = [list(p) for p in pts]
    return sh


def clean_shape(sh):
    """Shape loaded from a file -> valid shape, or None if it's a kind we no longer support."""
    if sh.get("kind") not in KINDS or len(sh.get("pts", [])) < 1:
        return None
    out = dict(SHAPE_DEFAULTS)
    out.update({k: sh[k] for k in SHAPE_DEFAULTS if k in sh})
    out["end_dot"] = bool(out["end_dot"])
    out["kind"] = sh["kind"]
    out["pts"] = [[float(b), float(p)] for b, p in sh["pts"]]
    env = sh.get("vel_env")
    if env:
        out["vel_env"] = [[float(u), max(1.0, min(127.0, float(v)))] for u, v in env]
    cl = clean_claw(sh.get("claw"))
    if cl:  # notes thinned out / moved after they're made (claw.py)
        out["claw"] = cl
    tm = clean_tumour(sh.get("tumour")) if out["kind"] in LINE_KINDS else None
    if tm:
        out["tumour"] = tm
    if out["kind"] == "custom":
        strokes = clean_strokes(sh.get("strokes"))
        if len(out["pts"]) != 3 or not strokes:
            return None
        out["name"] = str(sh.get("name", ""))
        out["strokes"] = strokes
        out["fill"] = sh.get("fill") if sh.get("fill") in FILLS else "empty"
        out["gate"] = max(1e-6, float(sh.get("gate", CUSTOM_DEFAULTS["gate"])))
        out["align"] = sh.get("align") if sh.get("align") in ALIGNS else "auto"
        out["ends"] = sh.get("ends") if sh.get("ends") in ENDS else "drop"
        out.update({k: True for k in CUSTOM_FLAGS if sh.get(k) is True})
        fr = sh.get("from")  # the shapes it was made of (convert.py)
        if isinstance(fr, dict) and isinstance(fr.get("shapes"), list) and fr["shapes"]:
            olds = [clean_shape(o) if isinstance(o, dict) else None for o in fr["shapes"]]
            try:
                if all(olds) and len(fr["pts"]) == 3:
                    out["from"] = {"shapes": olds, "strokes": clean_strokes(fr["strokes"]),
                                   "pts": [[float(b), float(p)] for b, p in fr["pts"]]}
            except (KeyError, TypeError, ValueError):
                pass
        pg = clean_polygon(sh.get("polygon"))
        if pg and "notes" not in sh and not sh.get("text"):  # made by the Polygon tool (polygon.py)
            out["polygon"] = pg
            out["strokes"] = polygon_strokes(pg)
        tx = clean_text(sh["text"]) if isinstance(sh.get("text"), dict) else None
        if tx:  # typed text (text.py): its strokes are the letters, the settings let it be retyped
            out["text"] = tx
        if "notes" in sh:  # pasted notes (custom.py): the strokes are just the box
            if not isinstance(sh["notes"], str) or not check_notes(sh["notes"]):
                return None
            out.update(notes=sh["notes"], strokes=[dict(BOX_STROKE)], fill="empty")
            if sh.get("own_vel"):
                out["own_vel"] = True
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
    return out


def shape_path(sh):
    """The shape as one polyline of (beat, pitch) points (a custom shape: all its strokes one after another)."""
    if sh["kind"] in ("custom", "funnel"):
        return [p for stroke in shape_strokes(sh) for p in stroke]
    pts = [tuple(p) for p in sh["pts"]]
    if sh["kind"] == "curve":
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
        return funnel_strokes(sh)
    if is_joined(sh):  # one path per piece
        return joined_paths(sh, tumour_path)
    return [shape_path(sh)]


_paths = {}
SHAPE_KEYS = ("starts", "tumour", "k", "text", "smooth", "gaps", "splits",
              "tumours", "pattern", "shape")  # what changes how a funnel / tumours / an arc / text /
# a straightened freehand stroke look (besides the points)


def _cached(sh):
    key = (sh["kind"], tuple(map(tuple, sh["pts"])), json.dumps(sh.get("strokes")),
           json.dumps([sh.get(k) for k in SHAPE_KEYS]))
    got = _paths.get(key)
    if got is None:
        if len(_paths) > 3000:
            _paths.clear()
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


def cached_path(sh):
    """The shape as one list of (beat, pitch) points (a custom shape: all its strokes one after another)."""
    strokes = cached_strokes(sh)
    return strokes[0] if len(strokes) == 1 else [p for st in strokes for p in st]


NO_NOTES = np.zeros((0, 6), np.int64)


def note_array(notes, columns):
    """A list of note tuples (or an array) -> NumPy int64 array with that many columns."""
    return np.asarray(notes, np.int64).reshape(-1, columns)


def unique_rows(a):
    """a without rows that repeat an earlier row (order kept)."""
    if len(a) < 2:
        return a
    order = np.lexsort(a.T[::-1])  # stable: within repeats the earliest comes first
    b = a[order]
    new = np.ones(len(a), bool)
    new[1:] = (b[1:] != b[:-1]).any(axis=1)
    if new.all():
        return a
    return a[np.sort(order[new])]


def shape_notes(sh, ppq, keys=128):
    """All notes of one shape as a NumPy array of (start, end, pitch, velocity) rows in ticks, before overlap
    handling. keys: the project's key range (keys 0 .. keys - 1)."""
    return shape_notes_tracks(sh, ppq, keys)[0]


def shape_notes_tracks(sh, ppq, keys=128):
    """shape_notes, and for pasted notes which track each note came from, for a custom shape made of other shapes
    which of them (one number per row; None for every other shape)."""
    notes, tracks = _notes_tracks(sh, ppq, keys)
    return with_claw(notes, tracks, sh.get("claw"), ppq)


def with_claw(notes, tracks, claw, ppq):
    """shape_notes_tracks' notes and tracks after the shape's claw (claw.py; None = none)."""
    if not claw:
        return notes, tracks
    if tracks is None:
        return apply_claw(notes, claw, ppq), None
    got = apply_claw(np.column_stack([notes, tracks]), claw, ppq)
    return got[:, :-1], got[:, -1]


def _notes_tracks(sh, ppq, keys):
    end_dot = sh.get("end_dot", False)
    path = dedupe(np.concatenate(cached_arrays(sh)))  # (drawing the line uses the same points)
    if path[-1, 0] < path[0, 0]:
        path = path[::-1]  # drawn right to left: the "last point" is the later end in time, same as left to right
    path = path * [ppq, 1]  # beats -> ticks
    own = None  # pasted notes' own velocities and tracks
    groups = None  # a custom shape made of other shapes (convert.py): which of them each note came from
    if sh["kind"] == "custom" and "notes" in sh:
        raw = block_notes(sh, ppq)
        raw, own = raw[:, :3], raw[:, 3:5]
    elif sh["kind"] == "custom":
        raw, groups = custom_notes_groups(sh, ppq)
    elif sh["kind"] == "funnel":
        raw = funnel_notes(sh, ppq)
    elif sh["kind"] in LINE_KINDS and len(cached_arrays(sh)) > 1:  # a joined curve's pieces: each like a line
        pieces = []
        for a in cached_arrays(sh):
            a = dedupe(a)
            if len(a) and a[-1, 0] < a[0, 0]:
                a = a[::-1]
            pieces.append(note_array(path_notes(a * [ppq, 1], end_dot), 3))
        raw = np.concatenate(pieces)
    elif end_dot and sh["kind"] == "poly" and len(path) > 2:
        raw = dot_segment_notes(path)
    else:
        raw = path_notes(path, end_dot)
    t_lo = float(path[:, 0].min())
    t_hi = float(path[:, 0].max())
    env = velocity_env(sh)
    raw = note_array(raw, 3)
    keep = (raw[:, 2] >= 0) & (raw[:, 2] < keys) & (raw[:, 1] > 0)
    raw = raw[keep]
    raw[:, 0] = np.maximum(raw[:, 0], 0)
    tracks = None
    if own is not None:
        own = own[keep]
        if sh.get("own_vel"):  # (the same note in two tracks stays twice: they can go to different channels)
            got = unique_rows(np.column_stack([raw, own]))
            return got[:, :4], got[:, 4]
        got = unique_rows(np.column_stack([raw, own[:, 1]]))
        raw, tracks = got[:, :3], got[:, 3]
    elif groups is not None:  # (the same note from two of them stays twice, like two shapes)
        got = unique_rows(np.column_stack([raw, np.asarray(groups, np.int64)[keep]]))
        raw, tracks = got[:, :3], got[:, 3]
    else:
        raw = unique_rows(raw)
    if len({v for _, v in env}) == 1:  # the same velocity everywhere
        vel = np.full(len(raw), max(1, min(127, round(env[0][1]))), np.int64)
    else:
        frac = np.clip((raw[:, 0] - t_lo) / (t_hi - t_lo), 0, 1) if t_hi > t_lo else np.zeros(len(raw))
        vel = np.clip(np.round(env_values(env, frac)), 1, 127).astype(np.int64)  # (rounds halves to even, like round)
    return np.column_stack([raw, vel]), tracks


# ---------------------------------------------------------------- overlaps and channels

def assign_slots(note_lists, split="key", apart=()):
    """
    Auto channels: shapes whose notes overlap get different slots, shapes that don't clash reuse the lowest
    free one. Earlier shapes get the lower slots. split="key": only notes on the same key at the same time
    clash; split="time": any notes sounding at the same time clash, whatever their key.
    apart: groups of note list numbers that always get different slots (a custom shape's outline and inside).
    """
    n = len(note_lists)
    by_pitch = {}
    for owner, notes in enumerate(note_lists):
        # Each shape's notes on a key joined into stretches first (back-to-back spam = one stretch): anything with
        # a length that overlaps a stretch overlaps one of its notes. Notes without a length stay on their own.
        flat = notes[:, 1] <= notes[:, 0]
        for s, e, p in notes[flat, :3].tolist():
            by_pitch.setdefault(p if split == "key" else 0, []).append((s, e, owner))
        a = notes[~flat]
        if not len(a):
            continue
        key = a[:, 2] if split == "key" else np.zeros(len(a), np.int64)
        order = np.lexsort((a[:, 0], key))
        s, e, k = a[order, 0], a[order, 1], key[order]
        run = running_max(e, k)
        new = np.ones(len(s), bool)
        new[1:] = (k[1:] != k[:-1]) | (s[1:] > run[:-1])
        at = np.nonzero(new)[0]
        for p, s0, e0 in zip(k[at].tolist(), s[at].tolist(), np.maximum.reduceat(e, at).tolist()):
            by_pitch.setdefault(p, []).append((s0, e0, owner))
    clashes = [set() for _ in range(n)]
    for items in by_pitch.values():
        items.sort()
        active = []
        for s, e, o in items:
            active = [a for a in active if a[0] > s]
            for _, ao in active:
                if ao != o:
                    clashes[o].add(ao)
                    clashes[ao].add(o)
            active.append((e, o))
    for group in apart:
        for a in group:
            clashes[a] |= set(group) - {a}

    first = [int(notes[:, 0].min()) if len(notes) else math.inf for notes in note_lists]
    order = sorted(range(n), key=lambda i: (first[i], i))
    slots, done = [0] * n, set()
    for i in order:
        used = {slots[j] for j in clashes[i] if j in done}
        k = 0
        while k in used:
            k += 1
        slots[i] = k
        done.add(i)
    return slots


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
    _, first, where = np.unique(notes[:, 4] * 256 + notes[:, 2], return_index=True, return_inverse=True)
    group = np.argsort(np.argsort(first))[where]  # groups numbered in the order they first show up
    # same start: the loudest comes last, so it's the one kept
    order = np.lexsort((-notes[:, 1], notes[:, 3], notes[:, 0], group))
    a, group = notes[order], group[order]
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
    return a[last | (end > s)]


CHANNEL_MODES = ("raw", "single", "auto")
SPLITS = ("key", "time")


def render(note_lists, mode, split="key", tracks=None, apart=None):
    """
    note_lists: shape_notes() of every shape -> (final notes, number of slots used). The notes are an array of
    (start, end, pitch, velocity, slot, owner) rows, owner = the shape's number.
    mode: "raw" = one channel, notes kept as they are (overlaps allowed), "single" = one channel with overlaps
    fixed, "auto" = overlapping shapes get their own channels (split: see assign_slots).
    tracks: per shape None, or the track of each of its notes (pasted notes, shape_notes_tracks): with "auto" each
    track of the shape gets channels as if it were a shape of its own.
    apart: per shape True if its tracks must get different channels (pasted notes, Fill / Spam "Outline").
    """
    tracks = tracks or [None] * len(note_lists)
    apart = apart or [False] * len(note_lists)
    if mode == "auto":
        units, unit_of, forced = [], [], []  # the shapes, pasted notes split up by track; unit_of = each note's unit
        for lst, tr, sep in zip(note_lists, tracks, apart):
            if tr is None or not len(lst):
                unit_of.append(len(units))
                units.append(lst)
                continue
            ids, which = np.unique(tr, return_inverse=True)
            which = which.ravel()
            unit_of.append(len(units) + which)
            if sep:
                forced.append(range(len(units), len(units) + len(ids)))
            units += [lst[which == k] for k in range(len(ids))]
        unit_slots = np.array(assign_slots(units, split, forced), np.int64)
        slot_of = [unit_slots[u] for u in unit_of]
        count = int(unit_slots.max()) + 1 if len(units) else 0
    else:
        slot_of, count = [0] * len(note_lists), 1 if note_lists else 0
    parts = [np.column_stack([lst, np.broadcast_to(slot_of[o], len(lst)), np.full(len(lst), o, np.int64)])
             for o, lst in enumerate(note_lists)]
    notes = np.concatenate(parts).astype(np.int64) if parts else NO_NOTES
    if mode != "raw":
        notes = resolve_overlaps(notes)
    return notes, count


def slot_track_channel(slot):
    """Slot number -> (track index, MIDI channel 0-15), one channel per track, skipping the drum channel."""
    return slot, CHANNELS[slot % len(CHANNELS)]
