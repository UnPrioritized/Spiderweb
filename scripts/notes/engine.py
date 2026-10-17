"""
Turns drawn shapes into MIDI notes: what a shape is, its notes, and fixing overlaps / picking channels.

The maths for each kind lives in its own file: paths.py (lines, polylines, freehand, curves), custom.py
(custom shapes), funnel.py (funnels), envelope.py (velocities).
"""

import itertools
import json
import math
import threading

import numpy as np

from files.lang import tr
from files.speed import key_sort, loops
from notes.custom import (ALIGNS, ENDS,CUSTOM_DEFAULTS, CUSTOM_FLAGS, FILLS, BOX_STROKE, block_notes, check_notes,
                          clean_curve, clean_cycle,
                          clean_strokes, custom_notes_groups, custom_strokes, cycle_turns, cycling)
from notes.between import KINDS as BETWEEN_KINDS, clean_between
from notes.envelope import env_values, velocity_env
from notes.joined import clean_joined, is_joined, joined_paths
from notes.hzbass import HZ_KINDS, clean_hz, heard_layers, layers_of, note_lengths, velocity_parts, with_layers
from notes.funnel import clean_funnel, clean_starts, funnel_notes, funnel_strokes, old_funnel
from notes.arc import arc_k, arc_points
from notes.areas import clean_areas
from notes.nested import clean_nested
from notes.tiles import clean_tiles
from notes.chop import apply_chop
from notes.claw import apply_claw
from notes.copypath import apply_path
from notes.fx import (clean_fx, flip_shape, groups, idle, mirrored, notes_box, reverse_velocity, swapped, toggled,
                      turn_notes, turn_pts, turn_shape, velocities)
from notes.gaterange import clean_range
from notes.gateflip import is_flipped as is_gate_flipped, is_pasted_flipped, mirrored_runs, unflipped_starts
from notes.merge import (LAP as merge_lap, clean_merge, recipe_notes, reshaped_notes as merge_reshaped,
                         turned_notes as merge_turned, with_rounds as merge_rounds)
from notes.glue import apply_glue, clean_glue, glue_box
from notes.strum import apply_strum
from notes.sustain import clean_sustain, cut_by, held, is_sustained
from notes.bezier import anchor_count, sample
from notes.paths import dedupe, dot_segment_notes, path_notes
from notes.pattern import FORMULA_KINDS, clean_pattern, clean_shape_formula, formed_paths
from notes.custom import ROUND, frame_upright, notes_resampled
from notes.picture import clean_picture, turned_notes
from notes.polygon import clean_polygon, polygon_strokes
from notes.mask import clean_mask
from notes.mould import clip_to, mould_cut, piece_loops
from notes.sliced import (clean_cut, clip_strokes, cut_through, in_part, knife_cut, moved_by, piece_knives, piece_notes,
                          run_origins, source, spotted_notes, whole_made)
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
LAST10 = np.array(CHANNELS + [9], np.int64)  # ("Use channel 10": the shapes' 16 channels in order, render)
CLASH = 1 << 16  # a track number this or past it: a Hz bass layer's note clashing with another layer's (layered_notes):
#                  its track // CLASH = how many channels past the shape's own (in every Channels mode)


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


def sane(x):
    """True if every number in x (a shape or strokes as read from a file: nested dicts and lists) is a real number
    no further than FAR from 0 (a hand-changed Infinity / NaN / 1e300 broke the drawing)."""
    todo = [x]
    while todo:  # (no recursion: a deeply nested file can't run out of stack)
        x = todo.pop()
        if isinstance(x, dict):
            todo.extend(x.values())
        elif isinstance(x, list):
            todo.extend(x)
        elif isinstance(x, (int, float)) and not -FAR <= x <= FAR:  # (NaN is never between)
            return False
    return True


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
    fx = clean_fx(sh, steps=bool(cut) and whole_made(cut))
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
    bt = clean_between(sh.get("between")) if out["kind"] in BETWEEN_KINDS else None
    if bt:  # one of an Add between group (between.py)
        out["between"] = bt
    tm = sh.get("team")
    if not bt and isinstance(tm, list) and len(tm) == 2 and isinstance(tm[0], str) and type(tm[1]) is int:
        out["team"] = list(tm)  # (its group's colour turn, kept when Turn into plain notes unlinked it: between.turns)
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
                sh = dict(sh, notes=recipe_notes(mg))
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
            if sh.get("made") in ("mask", "leftovers", "path", "plain"):
                out["made"] = sh["made"]
            pic = clean_picture(sh.get("picture"))
            if pic:  # a picture made into notes (picture.py): the notes are its finished colours, track = slot
                out["picture"] = pic
                for k in ("own_vel", "glue", "fx"):  # (no note tools on a picture, user)
                    out.pop(k, None)
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
    return out


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
        knives = piece_knives(sh)  # (a piece: only its side, sliced.py)
        return clip_strokes(funnel_strokes(sh), knives) if knives else funnel_strokes(sh)
    paths = joined_paths(sh, tumour_path) if is_joined(sh) else [shape_path(sh)]  # (joined: one path per piece)
    got = piece_loops(sh) if sh.get("cut") else None  # (cut with a shape: only its side, mould.py)
    return clip_to(paths, *got) if got else paths


_paths = {}
SHAPE_KEYS = ("starts", "tumour", "k", "text", "smooth", "gaps", "splits",
              "tumours", "pattern", "shape")  # what changes how a funnel / tumours / an arc / text /
# a straightened freehand stroke look (besides the points)


def _cached(sh):
    key = (sh["kind"], tuple(map(tuple, sh["pts"])), json.dumps(sh.get("strokes")),
           json.dumps([sh.get(k) for k in SHAPE_KEYS]),
           json.dumps([sh["cut"].get("knife"), sh["cut"]["was"], sh.get("fx"), sh.get("_m"), sh["cut"].get("mould")])
           if sh.get("cut") and (sh["kind"] == "funnel" or sh["cut"].get("mould")) else None)
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
    done = {}

    def get(s):
        key = json.dumps(s, sort_keys=True)
        if key not in done:
            done[key] = fx_notes(s, ppq, keys, get)
        return done[key]
    return get(sh)


BIG_TEXT = 100_000  # characters: a text this long (pasted / image notes, packed) is kept apart by shape_key


def shape_key(sh):
    """A shape as a key of the notes it makes (equal for exactly the same shape): its JSON text, with big texts
    (pasted / image notes, many MB) kept as the strings themselves, so they're never written out again: comparing
    the same string is instant (App.notes_key is asked at every rest of a drag). A Hz bass with another layer
    picked is the same shape (the same notes: picking a layer makes nothing again)."""
    if (sh.get("hz") or {}).get("layer"):
        sh = dict(sh, hz=with_layers(sh["hz"], layers_of(sh["hz"]), 0))
    big = sorted(k for k, v in sh.items() if isinstance(v, str) and len(v) > BIG_TEXT)
    if not big:
        return json.dumps(sh, sort_keys=True)
    return (json.dumps({k: v for k, v in sh.items() if k not in big}, sort_keys=True),
            tuple((k, sh[k]) for k in big))


def from_key(key):
    """A copy of the shape shape_key was made from (big texts shared: a string never changes)."""
    return json.loads(key) if isinstance(key, str) else dict(json.loads(key[0]), **dict(key[1]))


DEEP = 50  # steps: more, and fx_notes makes the notes before each one first (see there)
_warming = threading.local()


def fx_notes(sh, ppq, keys, get):
    """shape_notes_tracks' work: the notes, then its glue, then its note tool pages one after another (fx.py).
    get(shape) = another shape's notes and tracks (remembered by the caller: the steps before the last one, the
    notes flipped back). A hidden "_m" = the notes made flipped along those axes (the steps before a flip)."""
    if "sustain" in sh:  # (held on last, after everything else: sustain.py)
        notes, tracks = get({k: v for k, v in sh.items() if k != "sustain"})
        return held(notes, tracks, sh["sustain"], ppq) if is_sustained(sh) else (notes, tracks)
    if "picture" in sh:  # (no glue / chop / claw / strum on a picture: its notes are its picture, user)
        return kind_notes_tracks(sh, ppq, keys)
    fx = sh.get("fx")
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
        return fx_step(notes, tracks, last, sh, ppq, keys)
    if sh.get("_m"):
        if sh.get("cut"):  # (a sliced piece: its notes as it was, before the flips; sliced.steps_kept)
            return get(unflipped(sh))
        notes, tracks = get(bare_of(sh))
        return mirrored(notes, sh["_m"], notes_box(notes)), tracks
    if is_pasted_flipped(sh) or is_gate_flipped(sh):  # (each run on a key mirrored inside itself, gateflip.py)
        notes, tracks = get({k: v for k, v in sh.items() if k not in ("gate_flip", "glue")})
        return with_glue(*mirrored_runs(notes, tracks, bool(sh.get("own_vel"))), sh, ppq)
    return with_glue(*kind_notes_tracks(sh, ppq, keys), sh, ppq)


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
    if last["tool"] in ("flip", "turn") and idle(fx[:-1]) and not (sh.get("cut") and whole_made(sh["cut"])):
        return rest, None
    if last["tool"] == "flip":
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
        return with_strum(notes, tracks, step["cfg"], ppq)
    if tool == "vel":  # (over the shape's time, like its velocity line)
        t = np.concatenate(cached_arrays(sh))[:, 0] * ppq
        return velocities(notes, step["pts"], float(t.min()), float(t.max())), tracks
    return notes, tracks


def with_glue(notes, tracks, sh, ppq):
    """shape_notes_tracks' notes and tracks after the shape's glue (glue.py; it comes first, before any page)."""
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


def env_velocities(env, starts, t_lo, t_hi):
    """Velocities of notes starting at starts (ticks) from a velocity line env over t_lo .. t_hi."""
    if len({v for _, v in env}) == 1:  # the same velocity everywhere
        return np.full(len(starts), max(1, min(127, round(env[0][1]))), np.int64)
    frac = np.clip((starts - t_lo) / (t_hi - t_lo), 0, 1) if t_hi > t_lo else np.zeros(len(starts))
    return np.clip(np.round(env_values(env, frac)), 1, 127).astype(np.int64)  # (rounds halves to even, like round)


def whole_tools(whole, raw, spots, tracks, ppq):
    """A line piece's whole's notes (raw (start, end, key), each one's spot, tracks) with its velocities, after its
    glue and note tool pages: (start, end, key, velocity) rows, their spots, tracks."""
    t = dedupe(np.concatenate(cached_arrays(whole)))[:, 0] * ppq
    vel = env_velocities(velocity_env(whole), raw[:, 0], float(t.min()), float(t.max()))
    who = tracks if tracks is not None else np.zeros(len(raw), np.int64)
    a = np.column_stack([raw, vel, np.arange(len(raw)), who]).astype(np.int64)  # (spots ride along as row numbers)
    if whole.get("glue") and len(a):
        a = apply_glue(a, whole["glue"], glue_box(np.concatenate(cached_arrays(whole))), ppq, True)
    a = run_steps(a, whole.get("fx") or [], whole, ppq)
    return a[:, :4], spots[a[:, 4]], a[:, 5] if tracks is not None else None


def run_steps(a, fx, sh, ppq, m=(), pre=()):
    """Notes a (start, end, key, velocity, then columns riding along) after the steps fx of sh (as fx_notes does them;
    first a's put back by each of pre in order (("m", flip axes) / ("t", degrees, ticks per key): turned), then
    flipped along the axes m). A turn step turns the notes back, not the drawing (close, not exact). A flip / turn with
    no page switched on before it does nothing (before_step)."""
    up = []  # (the steps, last first: from the end down to the notes, then back up; no recursion: lists can be long)
    while fx:
        last, fx = fx[-1], fx[:-1]
        if last["tool"] in ("flip", "turn") and idle(fx):
            continue
        up.append(last)
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
    # cut by the Slice tool: the whole's notes on its side (sliced.py; a line too when cut from a piece as it was)
    if sh.get("cut") and (sh["kind"] in ("custom", "funnel") or (sh["cut"].get("whole") or {}).get("cut")):
        got = piece_notes(sh, ppq, keys, shape_notes_tracks)  # (the whole's glue / pages done first)
        if got is not None:
            return got
    if sh["kind"] in HZ_KINDS and (sh.get("hz") or {}).get("layers"):
        return layered_notes(sh, ppq, keys)
    end_dot = sh.get("end_dot", False)
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
    if piece:
        raw, spots = spotted_notes(sh, ppq, end_dot)
    elif sh["kind"] == "custom" and "notes" in sh:
        # (a merged shape turned / slanted / stretched: its parts the same way, merged again; else a turned / skewed
        # picture or merged shape: each key row sampled across it, not its notes tilted)
        # ("New channels each Colours round": its parts' rounds, ridden along on the tracks: merge.with_rounds)
        laps = sh.get("_rounds") and "merge" in sh and not cycling(sh)
        raw = merge_reshaped(sh, ppq, keys, bool(laps)) if "merge" in sh else None
        if raw is not None:
            if laps:
                raw = np.column_stack([raw[:, :4], raw[:, 4] + raw[:, 5] * merge_lap])
        else:
            if laps:
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
    elif end_dot and sh["kind"] == "poly" and len(path) > 2 and not (sh.get("shape") or sh.get("pattern")):
        # (with a formula its points are the formula's, like a curve's)
        raw = dot_segment_notes(path)
    else:
        raw = path_notes(path, end_dot)
    t_lo = float(path[:, 0].min())
    t_hi = float(path[:, 0].max())
    env = velocity_env(vel_sh)
    raw = note_array(raw, 3)
    if sh["kind"] in HZ_KINDS and sh.get("hz") and "notes" not in sh and not piece:  # (a Hz bass's Note length)
        raw = note_lengths(sh["hz"], raw, ppq, sh["kind"] != "funnel", sh)
    keep = (raw[:, 2] >= 0) & (raw[:, 2] < keys) & (raw[:, 1] > 0)
    raw = raw[keep]
    raw[:, 0] = np.maximum(raw[:, 0], 0)
    tracks = None
    if spots is not None:  # (Colours count over the whole's notes, then the piece keeps its own)
        spots = spots[keep]
        keep = unique_index(raw)
        raw, spots = raw[keep], spots[keep]
        if cycling(sh) and len(raw):
            tracks = cycle_turns(sh, raw, ppq, float(path[:, 1].min()))
        if sh.get("glue") or sh.get("fx"):  # the whole's glue / note tool pages first, on all its notes (sliced.py)
            raw, spots, tracks = whole_tools(sh, raw, spots, tracks, ppq)
        raw, spots, tracks = cut_through(sh, raw, spots, tracks, part, ppq)
        mine = in_part(spots, part)
        raw, tracks = raw[mine], tracks[mine] if tracks is not None else None
        if knife:
            raw, tracks = knife_cut(raw, tracks, knife, knife_d, ppq)
        if mould:
            raw, tracks = mould_cut(raw, tracks, mould, knife_d, ppq)
        if raw.shape[1] == 4:  # (velocities made by the whole's pages: kept, unless the piece has its own)
            if same_vel:
                return raw, tracks
            raw = raw[:, :3]
    if own is not None:
        own = own[keep]
        if cycling(sh):  # (a merged shape with Colours: turns across it all, not its shapes' own colours)
            own[:, 1] = 0
        if sh.get("own_vel"):  # (the same note in two tracks stays twice: they can go to different channels)
            got = unique_rows(np.column_stack([raw, own]))
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
    vel = env_velocities(env, raw[:, 0], t_lo, t_hi)
    if sh["kind"] in HZ_KINDS and own is None and (sh.get("hz") or {}).get("tones"):  # Hz bass velocity effects
        got = velocity_parts(sh, ppq, raw[:, 0], raw[:, 2])
        if got is not None:  # (a note with a loudness line drawn: its own velocity instead of the shape's)
            factor, mine = got
            vel = np.clip(np.floor(np.where(mine, 127, vel) * factor + 0.5), 1, 127).astype(np.int64)
    return np.column_stack([raw, vel]), tracks


# ---------------------------------------------------------------- overlaps and channels

def assign_slots(note_lists, split="key", apart=(), first=None, taken=None):
    """
    Auto channels: shapes whose notes overlap get different slots, shapes that don't clash reuse the lowest
    free one. Earlier shapes get the lower slots. split="key": only notes on the same key at the same time
    clash; split="time": any notes sounding at the same time clash, whatever their key.
    apart: groups of note list numbers that always get different slots (a custom shape's outline and inside).
    first: per list None, or the tick to count it as starting at instead (a gate aware flip's colours: where they
    start unflipped).
    taken: {list number: its slot}, lists whose slot is set already (the others clashing with them step round it).
    """
    n = len(note_lists)
    busy = range(n)
    group = list(range(n))
    for g in apart:
        for a in g:
            group[a] = min(g)
    if group != list(range(n)):
        # a group's own lists get slots apart anyway (a shape's colours take turns note by note: lots of stretches):
        # only lists whose group, joined, clashes with another group can clash with anything
        whole = {}
        for i, notes in enumerate(note_lists):
            whole.setdefault(group[i], []).append(notes)
        joined = [stretches(np.concatenate(ls), g, split) for g, ls in whole.items()]
        clashing = {g for pair in clash_pairs(np.concatenate(joined)) for g in pair}
        busy = [i for i in range(n) if group[i] in clashing]
    parts = [stretches(note_lists[i], i, split) for i in busy]
    clashes = [set() for _ in range(n)]
    for a, b in clash_pairs(np.concatenate(parts) if parts else np.zeros((0, 4), np.int64)):
        clashes[a].add(b)
        clashes[b].add(a)
    for group in apart:
        for a in group:
            clashes[a] |= set(group) - {a}

    first = [f if f is not None else int(notes[:, 0].min()) if len(notes) else math.inf
             for notes, f in zip(note_lists, first or [None] * n)]
    order = sorted(range(n), key=lambda i: (first[i], i))
    slots, done = [0] * n, set()
    for i, s in (taken or {}).items():
        slots[i] = s
        done.add(i)
    for i in order:
        if i in done:
            continue
        used = {slots[j] for j in clashes[i] if j in done}
        k = 0
        while k in used:
            k += 1
        slots[i] = k
        done.add(i)
    return slots


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
            out, m = fast.note_stretches(notes, split == "key", owner, fast.key_order(key, s, s, s))
        return out if m == len(out) else out[:m].copy()
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


def clash_pairs(items, chunk=1 << 22):
    """(key, start, end, owner) stretches -> the set of (a, b) owner pairs (a < b) that clash: on the same key, one
    starting before the other ends (in start, end, owner order; one starting where the other ends doesn't clash, nor
    does a stretch without a length). Worked out in chunks of about `chunk` pairs at a time."""
    if not len(items):
        return set()
    fast = loops()
    n = int(items[:, 3].max()) + 1
    if fast and n <= 4096 and items[:, 3].min() >= 0:  # (the compiled sweep: the same pairs)
        a, b = np.nonzero(fast.clash_pairs(np.ascontiguousarray(items, np.int64), n))
        return set(zip(a.tolist(), b.tolist()))
    order = np.lexsort((items[:, 3], items[:, 2], items[:, 1], items[:, 0]))
    k, s, e, o = (items[order, c] for c in range(4))
    vals, rank = np.unique(np.concatenate([s, e]), return_inverse=True)  # (ticks as ranks: no overflow)
    rank = rank.ravel()
    row = (k - k.min()) * len(vals)
    ps, pe = row + rank[:len(s)], row + rank[len(s):]
    cnt = np.maximum(np.searchsorted(ps, pe, "left") - np.arange(len(ps)) - 1, 0)  # later ones starting before its end
    total = np.cumsum(cnt)
    n = int(o.max()) + 1
    out, start = set(), 0
    while start < len(ps):
        stop = max(start + 1, int(np.searchsorted(total, (total[start - 1] if start else 0) + chunk, "right")))
        c = cnt[start:stop]
        if c.any():
            i = np.repeat(np.arange(start, stop), c)
            j = i + 1 + np.arange(len(i)) - np.repeat(np.cumsum(c) - c, c)
            a, b = o[i], o[j]
            m = a != b
            code = np.unique(np.minimum(a[m], b[m]) * n + np.maximum(a[m], b[m]))
            out.update(zip((code // n).tolist(), (code % n).tolist()))
        start = stop
    return out


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
        return out if m == len(out) else out[:m].copy()
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


def first_seen(ids):
    """Each id (whole numbers, not negative) as a number: 0 for the id that shows up first, 1 for the next new
    one, and so on."""
    top = int(ids.max()) + 1
    if top > 4 * len(ids) + 65536:  # (too spread out for a table)
        _, first, where = np.unique(ids, return_index=True, return_inverse=True)
        return np.argsort(np.argsort(first))[where]
    first = np.full(top, -1, np.int64)
    first[ids[::-1]] = np.arange(len(ids) - 1, -1, -1)  # (a repeated index keeps the last one written: the first)
    found = np.flatnonzero(first >= 0)
    rank = np.empty(top, np.int64)
    rank[found[np.argsort(first[found])]] = np.arange(len(found))
    return rank[ids]


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
    if use10:  # (0..8, 10..24, 26..40...: never a slot that is channel 10)
        return slots + (slots + 6) // 15
    return slots


def unmapped_slot(s, use10):
    """The assign_slots slot mapped_slots turns into slot s, or None (none does: channel 10 skipped)."""
    for u in range(s + 1):  # (mapped_slots never makes a slot smaller)
        if int(mapped_slots(np.array([u], np.int64), use10)[0]) == s:
            return u
    return None


def pushed_solid(note_lists, pinned, fixed, split, step):
    """render's pinned tracks with each "solid" shape's track that clashes with an earlier "solid" shape's on the same
    slot moved up by step slots (15, 16 with channel 10 used) until it's free: the same channel (colour), another
    track (user, 2026-10-17: a copy of plain notes ate the original's notes)."""
    placed = {}  # slot -> earlier solid tracks' stretches there
    out = list(pinned)
    for i, (lst, pin) in enumerate(zip(note_lists, pinned)):
        if fixed[i] != "solid" or pin is None or not len(lst):
            continue
        ids, which = unique_inverse(pin)
        new = pin.copy()
        for k, s in enumerate(ids.tolist()):
            mine = lst[which == k]
            while s in placed and clash_pairs(np.concatenate(placed[s] + [stretches(mine, 1, split)])):
                s += step
            placed.setdefault(s, []).append(stretches(mine, 0, split))
            new[which == k] = s
        out[i] = new
    return out


def in_rounds(note_lists, slot_of, rounds, step):
    """render's slots with "New channels each Colours round" (user 2026-10-17: each round editable alone in other
    programs): every round of every shape with rounds, in shape order, gets step slots of its own past the ones the
    other shapes use, each note at the channel (colour) it has without the tick. -> (slots, slot count).
    The other shapes' blocks are left free of rounds, the rest are filled in order: a plain-notes shape frozen on
    round channels keeps them and the shapes around it keep theirs. A round needing more than step slots (a
    layered Hz bass's clash tracks) takes as many blocks."""
    taken = set()
    for s, lst, r in zip(slot_of, note_lists, rounds):
        if r is None and len(lst):
            taken.update((np.unique(np.asarray(s, np.int64)) // step).tolist())
    free = (b for b in itertools.count() if b not in taken)
    out = list(slot_of)
    for i, (lst, r) in enumerate(zip(note_lists, rounds)):
        if r is not None and len(lst):
            s = np.asarray(slot_of[i], np.int64)
            q = s // step - int(s.min()) // step  # (the block within a round)
            wide = int(q.max()) + 1
            blocks = np.fromiter(itertools.islice(free, (int(r.max()) + 1) * wide), np.int64)
            out[i] = s % step + step * blocks[r * wide + q]
    return out, max([int(np.max(s)) + 1 for s, lst in zip(out, note_lists) if len(lst)] or [0])


def render(note_lists, mode, split="key", tracks=None, apart=None, fixed=None, use10=False, teams=None, pastes=None,
           held=None, back=None, ordered=None, rounds=None):
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
    its own, the same colours (in_rounds).
    """
    pastes = pastes or [None] * len(note_lists)
    tracks = tracks or [None] * len(note_lists)
    apart = apart or [False] * len(note_lists)
    fixed = fixed or [False] * len(note_lists)
    teams = teams or [None] * len(note_lists)
    back = back or [False] * len(note_lists)
    ordered = ordered or [False] * len(note_lists)
    firsts, paste_back, paste_first = {}, set(), {}  # (unit -> the tick it counts as starting at: assign_slots first)
    made = {}

    def whole_starts(i):
        """Unflipped starts of piece i of a gate flipped paste, its runs counted across all such pieces of the
        paste (sliced after the flip: a run went across the cut)."""
        if i not in made:
            mine = [j for j in range(len(note_lists)) if pastes[j] == pastes[i] and back[j] == "whole"]
            got = unflipped_starts(np.concatenate([note_lists[j] for j in mine]))
            at = np.cumsum([0] + [len(note_lists[j]) for j in mine])
            made.update({j: got[at[n]:at[n + 1]] for n, j in enumerate(mine)})
        return made[i]

    pinned = [np.asarray(tr, np.int64) if fx and tr is not None else None for tr, fx in zip(tracks, fixed)]
    if mode == "auto":
        pinned = pushed_solid(note_lists, pinned, fixed, split, 16 if use10 else 15)
    top = max([int(p.max()) + 1 for p in pinned if p is not None and len(p)] or [0])
    if mode == "auto":
        units, unit_of, forced = [], [], []  # the shapes, pasted notes split up by track; unit_of = each note's unit
        team_unit, team_units = {}, {}  # (group, turn) -> its unit; group -> its units
        paste_unit, paste_units = {}, {}  # (paste, track) -> its unit; paste -> its units
        taken = {}  # (unit -> its slot before channel 10 is skipped: a "solid" pinned shape's tracks)
        solid_teams = []  # (unit, (group, turn)) of those tracks
        for lst, tr, sep, pin, team, pid in zip(note_lists, tracks, apart, pinned, teams, pastes):
            if pin is not None:
                if fixed[len(unit_of)] == "solid" and len(lst):
                    ids, which = unique_inverse(pin)
                    for k, s in enumerate(ids.tolist()):
                        u = unmapped_slot(s, use10)
                        if u is not None:  # (on channel 10: no other shape takes it anyway)
                            taken[len(units)] = u
                            if team is not None:  # (a step turned into plain notes: the other turns step round it)
                                solid_teams.append((len(units), team))
                            units.append(lst[which == k])
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
                starts = whole_starts(len(unit_of)) if b == "whole" else unflipped_starts(lst) if b else lst[:, 0]
                for k, u in enumerate(us):
                    units[u] = np.concatenate([units[u], lst[which == k]])
                    s0 = int(starts[which == k].min())
                    paste_first[u] = min(paste_first.get(u, s0), s0)
                unit_of.append(np.asarray(us, np.int64)[which])
                if b:  # (a gate flipped piece: the whole paste's tracks by where they start unflipped, below)
                    paste_back.add(pid)
                continue
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
            if back[len(unit_of) - 1]:  # (each colour's channel as unflipped: counted from where it starts then)
                starts = unflipped_starts(lst)
                for k in range(len(ids)):
                    firsts[len(units) + k] = int(starts[which == k].min())
            if ordered[len(unit_of) - 1]:  # (one start for all: ties go by unit, so by track)
                s0 = int(lst[:, 0].min())
                for k in range(len(ids)):
                    firsts[len(units) + k] = s0
            units += mine
        for pid in paste_back:
            for u in paste_units[pid]:
                if u in paste_first:
                    firsts[u] = paste_first[u]
        forced += [us for us in team_units.values() if len(us) > 1]
        forced += [(p, u) for p, team in solid_teams for t, u in team_unit.items() if t[0] == team[0] and t != team]
        for p, team in solid_teams:  # (its own turn's shapes: its channel, as when it was one of them)
            if team in team_unit:
                taken.setdefault(team_unit[team], taken[p])
        forced += [us for us in paste_units.values() if len(us) > 1]  # (pasted tracks: always apart, tracks_apart)
        unit_slots = mapped_slots(np.array(assign_slots(units, split, forced, [firsts.get(u) for u in
                                                                              range(len(units))], taken), np.int64),
                                  use10)
        slot_of = [pin if u is None else unit_slots[u] for u, pin in zip(unit_of, pinned)]
        count = int(unit_slots.max()) + 1 if len(units) else 0
        if rounds and any(r is not None for r in rounds):
            slot_of, count = in_rounds(note_lists, slot_of, rounds, 16 if use10 else 15)
    else:  # (a Hz bass layer's clashing notes too: one channel, user)
        slot_of = [0 if pin is None else pin for pin in pinned]
        count = 1 if any(p is None for p in pinned) else 0
    count = max(count, top)
    notes = np.empty((sum(len(lst) for lst in note_lists), 6), np.int64)
    at = 0
    for o, lst in enumerate(note_lists):
        part = notes[at:at + len(lst)]
        part[:, :4], part[:, 4], part[:, 5] = lst, slot_of[o], o
        at += len(lst)
    if mode != "raw":
        if held and any(held):  # (sustained shapes keep their overlaps; the others are cut where theirs start)
            mine = np.asarray(held, bool)[notes[:, 5]]
            kept = notes[mine]
            notes = np.concatenate([cut_by(resolve_overlaps(notes[~mine]), kept), kept])
        else:
            notes = resolve_overlaps(notes)
    return notes, count


def slot_track_channel(slot, use10=False):
    """Slot number -> (track index, MIDI channel 0-15), one channel per track, skipping the drum channel (use10:
    not skipping it: slot k = channel k; render keeps the other shapes off those slots)."""
    return slot, slot % 16 if use10 else CHANNELS[slot % len(CHANNELS)]
