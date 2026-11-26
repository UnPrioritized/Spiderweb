"""The project's tempo line (the Tempo pane, window/tempo_pane.py) and the tempo changes it writes into a MIDI file.

The line is made of points, each an exact BPM at a beat; two on one beat (user 2026-10-22) = a jump: the first is
where the part before arrives, the second the tempo from there (the first never reaches the file: it holds for no
tick). Each part between two points has a curve (hold, straight,
S-curve, ease out, ease in, or a formula of x from 0 to 1) and a BPM change: linear (default: a straight part from
60 to 240 passes 150 halfway) or exponential (straight on the pane's doubling scale: passes 120). Before the first
point the tempo is the project's BPM, after the last one it holds the last point's. A curved part writes a new
tempo every "every" ticks (default 24).

A catch-up line starts a catch-up (user 2026-10-21): the drawn tempo holds up to the line, then from the line to the
next point (or catch-up line) Spiderweb works out a tempo so the whole stretch (stretches: from where the line left
the tempo it comes back to) takes exactly as long as at the tempo heard just before it; the drawn line goes on from
that point. The catch-up's shape: flat, fast then slow (settling to the BPM), slow then fast, up and down or a
formula; the two eases bend by a handle (catch[5]); drawn (catch[6]: points drawn by hand in its own window,
window/catch_graph.py). A catch-up never moves notes: everything after it is in time.

Keep timing (a tick per part, point[5]): the part's notes keep the time they have at the tempo heard before it;
the export moves them in ticks (notes/tempo_file.py). The part after the last point goes on to the song's end.
HEARD TEMPO LINE (user 2026-10-24; point[7], missing = none): {"line": points like the tempo line's (no Keep
timing, no line of their own), "visual": True = "Keep the visual" picked}. Inside the part the sound follows that
line (beats are the song's, so a part cut in two keeps reading the same line); before its first point the tempo
heard just before the part, as without one.

Saved in the project as "tempo": {"points": [[beat, bpm, curve, formula, "lin" / "exp", keep timing, bend, heard
line?, own Interval?], ...],
"catch": [[beat, shape, formula, start, end, bend, drawn, own Interval?], ...], "every": ticks (the Interval box: new
ramps' and those without their own), "bars": Fit bar lines}, beats like the shapes (a PPQ change moves nothing).

Exact timing (user's rule: tick and microsecond perfect, not "too small to hear"): a MIDI file's time is whole
numbers, ticks x microseconds per beat, so the catch-up is picked tick by tick to make each stretch's sum exactly
the project BPM's. Every value written also survives Domino opening and saving the file unchanged (domino_safe:
measured 2026-10-21, Domino keeps a tempo as a 32-bit BPM, f32(6e7 / us), and saves trunc(6e7 / bpm): half of all
values lost 1 us each save). EXCEPT a typed held BPM: what Domino itself writes for it (held_us; user 2026-11-01),
so Domino shows the BPM typed."""

import bisect
import copy
import functools
import json
import hashlib
import itertools
import marshal
import math
import os
import re
import struct
import threading

import numpy as np

from files.mathexpr import fmt, formula
from files.midi_scan import MOST_TICK
from notes.meter import DEFAULT as DEFAULT_SIG, clean_keysigs, clean_sigs
from notes import tempo_ramp
from notes.tempo_ramp import Ramp, RampRule, fixed_ramp
from notes.tempo_safe import fit_block_runs, flat_pair_runs
from notes.tempo_safe import (PAIR_REACH, SAFE_REACH, _FAR, _WINDOW, _fit_blocks, _fit_exact, _first_safe, _flat_pair,  # (moved)
                              _flat_pair_search, _near_pair, _nearest1, _next1, _next_by_bpm, _next_small, _pairs, _runs,
                              _safe1, _safe_side, PAIRS_KEPT, domino_safe, nearest_safe, next_safe)

MIN_BPM = 60_000_000 / 0xFFFFFF  # ~3.58: the slowest a MIDI file holds (24-bit microseconds per beat)
MAX_BPM = 60_000_000.0  # 1 microsecond per beat (faster = 0, which breaks the file; Domino writes that past it)
CURVES = ("hold", "line", "s", "formula")
BEND = [0.5, 0.5]  # a line / S-curve part's bend (its curve's control point, 0..1 each way): straight
OLD_CURVES = {"out": ("line", [0.5, 1.0]), "in": ("line", [0.5, 0.0])}  # (ease out / in before the bend handle)
SHAPES = ("flat", "ease", "rise", "ramp", "drawn", "formula")
CATCH_BEND = [0.5, 0.0]  # a fast-then-slow / slow-then-fast catch-up's bend (control point: 0..1 along, push)
DRAWN = [[0.0, 1.0, 0.5], [1.0, 1.0, 0.5]]  # a drawn catch-up's points [along, push share, bend]: flat
DRAWN_POINTS = 200  # the most points a drawn catch-up keeps
DRAWN_BENDS = (0.02, 0.98)  # a stretch's bend handle height in it (0.5: straight)
FORMULA = "x^3"  # a new formula part's / catch-up's starting formula
SCALES = ("lin", "exp")  # a part's BPM change: linear (default, user) / exponential (straight on the doubling scale)
EVERY = 24  # ticks between the tempo changes of a curved part (user: like Domino's default; 1 = every tick)
BLOCKS_FROM = 8  # an interval this long or more: a curved part worked out step by step, not tick by tick
FAR = float(1 << 46)  # beats: past this a point is left out (a loaded MIDI's farthest tick, MOST_TICK, at PPQ 4 and
# up: huge-tick MIDIs at 60 M BPM pass 10 M beats in 10 s, their tempo changes after it were dropped; x PPQ 65535
# stays inside 64 bits: at 2^48 beats the pane's ticks overflowed, hunt 2026-11-26). Typed places: far_beat.


def far_beat(ppq):
    """The farthest beat a point may be typed / placed at in a project of this PPQ: the farthest tick a MIDI file holds
    (typed past it, Export wrote a damaged file or gigabytes of waits; hunt 2026-11-26)."""
    return min(FAR, MOST_TICK / max(1, ppq))
SLOWEST = 16_777_211  # the slowest Domino-safe tempo (us per beat; a file holds up to 0xFFFFFF)


def empty():
    return {"points": [], "catch": [], "every": EVERY, "bars": True, "sigs": [list(DEFAULT_SIG)], "keysigs": []}


def has_tempo(tempo):
    return bool(tempo and tempo.get("points"))


# The app's tempo line as text (the key of every remembered tempo result, and the undo steps' copy): made once per
# change, not 14 times a redraw (13 k tempos from a loaded MIDI = 7-11 ms each). Only the line watch() was given
# is remembered; App.tempo_changed / a new line call changed(). SPIDERWEB_TEMPO_CHECK=1 (tests) = every reuse
# checked against the line itself (an edit that didn't say so raises).
_text = {"tempo": None, "ver": 0, "made": -1, "text": None, "kmade": -1, "key": None}
_CHECK = bool(os.environ.get("SPIDERWEB_TEMPO_CHECK"))


def watch(tempo):
    _text["tempo"] = tempo
    changed()


def changed():
    _text["ver"] += 1


def version():
    """A number that goes up with every tempo change (changed())."""
    return _text["ver"]


_held = {"tempo": None, "text": None, "n": 0}


def hold(tempo):
    """tempo (a drag's preview line, made new each redraw) won't change until hold(None): its text is worked out
    once, and the caches (layout, step_edges, ...) know it by a token instead of its text (35,000 tempo points:
    ~30 ms to write the text, ~35 ms to read it back, every drag step)."""
    _held["tempo"], _held["text"] = tempo, None
    _held["n"] += 1


def held_copy(tempo):
    """A copy of the held line as its text would read back (marshal: many times quicker than json; a number
    marshal can't write, e.g. NumPy's, through json as before)."""
    try:
        return marshal.loads(marshal.dumps(tempo))
    except ValueError:
        return json.loads(json.dumps(tempo))


def tempo_key(tempo):
    """What the caches know tempo by: a token for the held line (never the same as another's), else its marshal
    bytes (2026-11-14: ~10x quicker than its json text; 144,000 short BPM changes = 11 ms, json 114 ms), made once
    per change for the watched line. Equal lines = equal bytes (a json round trip too); NOT the saved / undo text
    (tempo_text): only for remembering results."""
    if tempo is not None and tempo is _held["tempo"]:
        return ("held", _held["n"])
    known = getattr(_known_t, "pair", None)
    if tempo is not None and known is not None and tempo is known[0]:
        return known[1]
    if tempo is None or tempo is not _text["tempo"]:
        return _mkey(tempo)
    if _text["kmade"] != _text["ver"]:
        _text["key"], _text["kmade"] = _VKey(_text["ver"], _mkey(tempo) if _CHECK else None), _text["ver"]
    elif _CHECK and _mkey(tempo) != _text["key"].check:
        _stale("TEMPO STALE")
        _text["key"] = _VKey(_text["ver"], _mkey(tempo))
        _text["key"].ver = ("stale", id(_text["key"]))  # (never the same key as what was remembered before)
    return _text["key"]


class _VKey:
    """The watched line's cache key: its change number (2026-11-14: its bytes took ~30 ms per change at 190,000
    points). check = its bytes in tests (SPIDERWEB_TEMPO_CHECK) to catch an edit that didn't say so."""
    __slots__ = ("ver", "check")

    def __init__(self, ver, check):
        self.ver, self.check = ver, check

    def __eq__(self, other):
        return isinstance(other, _VKey) and other.ver == self.ver

    def __hash__(self):
        return hash(("tempo line", self.ver))


_known_t = threading.local()  # (per thread: a background read never sees another thread's line)


class known_key:
    """with known_key(tempo): its key worked out once for every cache asked inside (a line that isn't the watched
    one, e.g. a change's trial line: ~30 ms a key at 190,000 points, asked 4-5 times a read). The line must not
    change inside."""

    def __init__(self, tempo):
        self.tempo = tempo

    def __enter__(self):
        self.was = getattr(_known_t, "pair", None)
        if self.tempo is not None and (self.was is None or self.was[0] is not self.tempo):
            _known_t.pair = (self.tempo, tempo_key(self.tempo))
        return self

    def __exit__(self, *exc):
        _known_t.pair = self.was
        return False


def _mkey(tempo):
    try:
        return marshal.dumps(tempo, 2)  # (version 2: no object references, so the same line = the same bytes)
    except ValueError:  # (a number marshal can't write, e.g. NumPy's: its text, as before)
        return json.dumps(tempo)


def key_copy(key, tempo):
    """A copy of tempo as its key (tempo_key) reads back: what's remembered never changes with the line."""
    if isinstance(key, bytes):
        return marshal.loads(key)
    return json.loads(key) if isinstance(key, str) else held_copy(tempo)  # (the watched / held line: now)


def tempo_text(tempo):
    """json.dumps(tempo), remembered for the watched line until it changes (the same string each time, so undo
    steps share it) and for the held one (hold)."""
    if tempo is not None and tempo is _held["tempo"]:
        if _held["text"] is None:
            _held["text"] = json.dumps(tempo)
        return _held["text"]
    if tempo is not _text["tempo"] or tempo is None:
        return json.dumps(tempo)
    if _text["made"] != _text["ver"]:
        snap = _snap_of(tempo)
        text = snap.text() if snap is not None else json.dumps(tempo)
        if _CHECK and snap is not None and text != json.dumps(tempo):
            _stale("TEMPO PIECES WRONG")
            text = json.dumps(tempo)
        if text != _text["text"]:  # (the same text = the same string kept: undo steps share it)
            _text["text"] = text
        _text["made"] = _text["ver"]
    elif _CHECK and json.dumps(tempo) != _text["text"]:
        _stale("TEMPO STALE")
        _text["text"] = json.dumps(tempo)
    return _text["text"]


def _stale(what):
    """SPIDERWEB_TEMPO_CHECK (tests): a remembered tempo result found wrong; =1 says it on stderr, a file path
    adds it to that file."""
    import sys
    import traceback
    text = what + "\n" + "".join(traceback.format_stack(limit=8)[:-2])
    where = os.environ.get("SPIDERWEB_TEMPO_CHECK", "")
    if where not in ("", "1"):
        with open(where, "a", encoding="utf-8") as f:
            f.write(text)
    sys.stderr.write(text)


# A BIG watched line's text in PIECES of PIECE points (2026-11-14, 190,000 short BPM changes: its 12.7 MB json took
# ~200 ms for every undo step, save and "save first?" check, and every step kept a whole new copy): a piece whose
# points are as they were keeps its text (one BPM changed = one piece written again); undo steps keep the pieces
# (TempoSnap), shared with the steps before.
PIECE = 4096
_pieces = {"line": None, "ver": -1, "parts": [], "snap": None}


class TempoSnap:
    """An undo step's copy of a big tempo line: its json text as pieces (head = (key, its json or None for the
    points), texts = the points' pieces, shared string objects). Equal to another copy / its text when the texts
    are."""
    __slots__ = ("head", "texts")

    def __init__(self, head, texts):
        self.head, self.texts = head, texts

    def text(self):
        """The line's json text, exactly json.dumps(line)."""
        pts = "[" + ", ".join(self.texts) + "]"
        return "{" + ", ".join(f"{json.dumps(k)}: {pts if v is None else v}" for k, v in self.head) + "}"

    def line(self, known=None):
        """(The line read back = json.loads(text()), each piece's points): a piece whose text is one of known's
        {id(text): (its points as json reads them, text)} is THOSE points, not read again: only for clean_tempo,
        which makes its own (never change them)."""
        out, pieces = {}, []
        for k, v in self.head:
            out[k] = v if v is None else json.loads(v)
        if "points" in out:
            pts = []
            for t in self.texts:
                got = known.get(id(t)) if known else None
                c = got[0] if got is not None and got[1] is t else json.loads("[" + t + "]")
                pieces.append(c)
                pts += c
            out["points"] = pts
        return out, pieces

    def __eq__(self, other):
        if isinstance(other, TempoSnap):
            return self.head == other.head and self.texts == other.texts
        if isinstance(other, str):
            return self.text() == other
        return NotImplemented

    __hash__ = None


def _snap_of(tempo):
    """The watched line as a TempoSnap (made once per change, pieces as they were kept), None for a small line /
    one these pieces can't hold (then plain json)."""
    if _pieces["line"] is tempo and _pieces["ver"] == _text["ver"]:
        return _pieces["snap"]
    pts = tempo.get("points") if isinstance(tempo, dict) else None
    if not isinstance(pts, list) or len(pts) < BIG_LINE or not all(type(k) is str for k in tempo):
        return None
    old = _pieces["parts"] if _pieces["line"] is tempo else []
    parts = []
    try:
        for j, c in enumerate(range(0, len(pts), PIECE)):
            sl = pts[c:c + PIECE]
            if j < len(old) and old[j][0] == sl:
                parts.append(old[j])
            else:  # (its text + the points as json reads them back: to tell later whether it changed, and undo
                text = json.dumps(sl)[1:-1]  # copies them instead of reading the text)
                parts.append((json.loads("[" + text + "]"), text))
    except (ValueError, TypeError):  # (a number json can't write)
        return None
    head = tuple((k, None if k == "points" else json.dumps(v)) for k, v in tempo.items())
    snap = TempoSnap(head, tuple(t for _, t in parts))
    was = _pieces["snap"]
    if was is not None and _pieces["line"] is tempo and was == snap:
        snap = was  # (the same line: the same copy, steps share it)
    _pieces.update(line=tempo, ver=_text["ver"], parts=parts, snap=snap)
    return snap


def tempo_snap(tempo):
    """What an undo step keeps of the tempo line: tempo_text, or for a big watched line its TempoSnap (pieces shared
    with the steps before)."""
    if tempo is not None and tempo is _text["tempo"] and tempo is not _held["tempo"]:
        snap = _snap_of(tempo)
        if snap is not None:
            if _CHECK and snap.text() != json.dumps(tempo):
                _stale("TEMPO PIECES WRONG")
                return json.dumps(tempo)
            return snap
    return tempo_text(tempo)


def snap_text(snap):
    """An undo step's tempo copy (tempo_snap) as json text."""
    return snap.text() if isinstance(snap, TempoSnap) else snap


def snap_line(snap):
    """An undo step's tempo copy (tempo_snap: text or TempoSnap) read back, cleaned: clean_tempo(json.loads(text)).
    A TempoSnap's pieces the line now has are copied, not read; when cleaning changed nothing, the new line starts
    with the copy's pieces (its next undo step writes only what changed)."""
    if not isinstance(snap, TempoSnap):
        return clean_tempo(json.loads(snap))
    known = {id(t): (c, t) for c, t in _pieces["parts"]}
    data, pieces = snap.line(known)
    line = clean_tempo(data)  # (new points: the pieces stay as they were)
    if line.get("points") == data.get("points"):
        _pieces.update(line=line, ver=-1, parts=list(zip(pieces, snap.texts)), snap=snap)
    return line


def clamp_bpm(v):
    return min(MAX_BPM, max(MIN_BPM, float(v)))


def clean_tempo(data):
    """A tempo line from a file / undo step -> a safe one (broken points left out, sorted, at most two points per
    beat: where a curve arrives, then the tempo from there; user 2026-10-22)."""
    out = empty()
    if not isinstance(data, dict):
        return out
    out["points"] = _clean_points(data.get("points"))
    every = data.get("every", EVERY)
    out["every"] = every if isinstance(every, int) and not isinstance(every, bool) and 1 <= every <= 65535 else EVERY
    out["bars"] = data.get("bars", True) is not False
    seen = set()
    for c in data.get("catch", []) if isinstance(data.get("catch"), list) else []:
        if not isinstance(c, list):
            continue
        try:
            b = float(c[0])
            shape = c[1] if len(c) > 1 and isinstance(c[1], str) and c[1] in SHAPES else "flat"
            text = c[2] if len(c) > 2 and isinstance(c[2], str) else ""
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        if not math.isfinite(b) or not 0 < b <= FAR or round(b, 9) in seen:
            continue
        seen.add(round(b, 9))

        def edge(k):
            x = c[k] if len(c) > k else None
            try:
                return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) \
                    and math.isfinite(float(x)) else None
            except OverflowError:
                return None
        start, end = edge(3), edge(4)
        item = [b, shape, text[:200]]
        catch_set(item, 3, start if start is not None and 0 <= start <= FAR else None)  # (past the line: a pin
        catch_set(item, 4, end if end is not None and 0 < end <= FAR else None)          # waiting, stretches)
        bend = c[5] if len(c) > 5 else None
        if isinstance(bend, list) and len(bend) == 2 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1 for x in bend):
            catch_set(item, 5, [float(x) for x in bend])
        catch_set(item, 6, clean_drawn(c[6] if len(c) > 6 else None))
        own = _clean_step(c[7]) if len(c) > 7 else None  # (its own Interval: a plain number only)
        catch_set(item, 7, own if isinstance(own, int) else None)
        out["catch"].append(item)
    out["catch"].sort(key=lambda c: c[0])
    out["sigs"] = clean_sigs(data.get("sigs"))
    out["keysigs"] = clean_keysigs(data.get("keysigs"))
    return out


def _clean_plain(raw, inner):
    """_clean_points for a big list where EVERY point is an ordinary held 7-field point with a straight bend (what
    190,000 short BPM changes are): the checks done on whole columns (the 7 columns taken apart once). Gives the
    same list the point-by-point way does; None the moment anything is not ordinary (a curve, a text, a point out of
    order / a hair from the next / on one beat three times, a number out of range...): then _clean_mixed."""
    if set(map(type, raw)) != {list} or set(map(len, raw)) != {7}:
        return None
    cb, cv, ck, ct, cs, cp, cd = zip(*raw)
    num = {int, float}
    if not set(map(type, cb)) <= num or not set(map(type, cv)) <= num or set(ck) != {"hold"} or set(ct) != {""} \
            or not set(cs) <= set(SCALES) or set(map(type, cp)) != {bool} or set(map(tuple, cd)) != {tuple(BEND)}:
        return None
    try:
        b, v = np.array(cb, float), np.array(cv, float)
    except (OverflowError, ValueError, TypeError):
        return None
    if not (np.isfinite(b).all() and np.isfinite(v).all()) or b.min() < 0 or b.max() > FAR or v.min() <= 0:
        return None
    d = np.diff(b)
    if not bool(((d == 0) | (d > 2e-9)).all()) or (len(d) > 1 and bool(((d[1:] == 0) & (d[:-1] == 0)).any())):
        return None  # (out of order, a pair a hair apart, or three on a beat: the careful way)
    vv = np.clip(v, MIN_BPM, MAX_BPM).tolist()
    keep = [False] * len(cp) if inner else list(cp)
    # (the collector left on: switched off while making 190,000 lists, it paused ~0.2 s at some later moment)
    pts = [[x, y, "hold", "", s, k, [0.5, 0.5]] for x, y, s, k in zip(b.tolist(), vv, cs, keep)]
    if bool((d == 0).any()):  # (a jump's two points: drop_loose looks at them)
        drop_loose(pts)
    return pts


_NUMS = (int, float)


def _plain_row(p):
    """An ordinary held 7-field point with a straight bend and plain numbers (_clean_mixed reads these in bulk)."""
    return (type(p) is list and len(p) == 7 and type(p[0]) in _NUMS and type(p[1]) in _NUMS and p[2] == "hold"
            and p[3] == "" and p[4] in SCALES and p[6] == BEND and type(p[6][0]) is float and type(p[6][1]) is float)


def _clean_mixed(raw, inner):
    """_clean_points for a big list with SOME unusual points (a few ramps, Keep timing parts, a text, points out of
    order or on one beat): the ordinary held ones read in bulk, only the others point by point (_clean_one), then the
    same rules as the point-by-point way (at most two a beat in the file's order, sorted keeping that order, a pair
    put on exactly one beat, drop_loose): the same list. None = can't read the numbers in bulk (point by point)."""
    n = len(raw)
    flags = list(map(_plain_row, raw))
    odd = [i for i, f in enumerate(flags) if not f]
    if not odd:
        rows = raw
        idx = np.arange(n)
    else:
        idx = np.flatnonzero(np.fromiter(flags, bool, n))
        rows = [raw[i] for i in idx.tolist()]
    try:
        b = np.fromiter((p[0] for p in rows), float, len(rows))
        v = np.fromiter((p[1] for p in rows), float, len(rows))
    except (OverflowError, ValueError, TypeError):
        return None
    ok = np.isfinite(b) & np.isfinite(v) & (b >= 0) & (b <= FAR) & (v > 0)
    made = {}  # (an unusual point's file index -> its cleaned point)
    for i in odd:
        pt = _clean_one(raw[i], inner)
        if pt is not None:
            made[i] = pt
    at = np.concatenate([idx[ok], np.array(sorted(made), np.int64)])  # (every kept point's place in the file)
    beats = np.concatenate([b[ok], np.array([made[i][0] for i in sorted(made)], float)])
    vals = np.concatenate([np.clip(v[ok], MIN_BPM, MAX_BPM), np.zeros(len(made))])  # (the plain ones' BPMs)
    order = np.lexsort((at, beats))  # (by beat, then the file's order: what a stable sort of the file's list gives)
    at, beats, vals = at[order], beats[order], vals[order]
    near = np.flatnonzero(np.diff(beats) <= 2e-9)  # (only points this close can share round(beat, 9))
    drop, snap = set(), {}
    if len(near):
        groups, cur = [], [int(near[0]), int(near[0]) + 1]
        for k in near[1:].tolist():
            if k <= cur[-1]:
                cur.append(k + 1)
            else:
                groups.append(cur)
                cur = [k, k + 1]
        groups.append(cur)
        for g in groups:
            by = {}
            for k in g:
                by.setdefault(round(float(beats[k]), 9), []).append(k)
            for ks in by.values():
                ks.sort(key=lambda k: at[k])  # (the file's first two on a beat stay)
                drop.update(ks[2:])
            left = [k for k in g if k not in drop]
            for k0, k1 in zip(left, left[1:]):  # (a pair on exactly one beat, not a hair apart)
                if round(float(beats[k0]), 9) == round(float(beats[k1]), 9):
                    snap[k1] = k0
    keep_rows = [k for k in range(len(at)) if k not in drop] if drop else range(len(at))
    bl, al, vl = beats.tolist(), at.tolist(), vals.tolist()
    for k1, k0 in sorted(snap.items()):
        bl[k1] = bl[k0]
    pts = []
    for k in keep_rows:
        i = al[k]
        pt = made.get(i)
        if pt is None:
            p = raw[i]
            pt = [bl[k], vl[k], "hold", "", p[4], p[5] is True and not inner, [0.5, 0.5]]
        else:
            pt[0] = bl[k]
        pts.append(pt)
    if len(pts) > 1:
        kb = np.fromiter((p[0] for p in pts), float, len(pts))
        if bool((kb[1:] == kb[:-1]).any()):  # (a jump's two points: drop_loose looks at them)
            drop_loose(pts)
    return pts


def _clean_one(p, inner):
    """One point from a file / undo step cleaned, None when it's broken (left out: the rest kept)."""
    if not isinstance(p, list):
        return None
    try:
        b, v = float(p[0]), float(p[1])
        kind = p[2] if len(p) > 2 and isinstance(p[2], str) else "hold"
        bend = list(BEND) if kind != "s" else [0.5, 0.0]  # (an S-curve from before: much like it was)
        kind, bend = OLD_CURVES.get(kind, (kind, bend))
        kind = kind if kind in CURVES else "hold"
        text = p[3] if len(p) > 3 and isinstance(p[3], str) else ""
        scale = p[4] if len(p) > 4 and isinstance(p[4], str) and p[4] in SCALES else "lin"
        keep = len(p) > 5 and p[5] is True
        got = p[6] if len(p) > 6 and isinstance(p[6], list) and len(p[6]) == 2 else None
        if got and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                       for x in got):
            bend = [min(1.0, max(0.0, float(x))) for x in got]
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if not (math.isfinite(b) and math.isfinite(v)) or not 0 <= b <= FAR or v <= 0:
        return None
    # (formulas up to 4000 letters: a part cut by a paste writes its formula again, longer each time)
    pt = [b, clamp_bpm(v), kind, text[:4000], scale, keep and not inner, bend]
    heard = None if inner or len(p) < 8 else clean_heard(p[7])
    step = _clean_step(p[8]) if len(p) > 8 else None  # (its own Interval)
    if heard or step is not None:
        pt.append(heard or None)
    if step is not None:
        pt.append(step)
    return pt


def _clean_points(raw, inner=False):
    """A tempo line's points cleaned (clean_tempo); inner = a heard tempo line's (no Keep timing, no line of
    their own). Big lists: in bulk (_clean_plain, _clean_mixed), the same list."""
    if isinstance(raw, list) and len(raw) >= BIG_LINE:
        for way in (_clean_plain, _clean_mixed):
            try:  # (a value a bulk way can't sort out, e.g. a list where a word goes: the next way, last point by
                got = way(raw, inner)  # point, which drops that point like before)
            except (TypeError, ValueError, IndexError, KeyError, AttributeError, OverflowError):
                got = None
            if got is not None:
                return got
    pts = []
    seen = {}
    for p in raw if isinstance(raw, list) else []:
        pt = _clean_one(p, inner)  # (broken by hand: that point left out, the rest kept)
        if pt is None:
            continue
        key = round(pt[0], 9)
        if seen.get(key, 0) >= 2:
            continue
        seen[key] = seen.get(key, 0) + 1
        pts.append(pt)
    pts.sort(key=lambda p: p[0])  # (stable: a pair keeps its order)
    for i in range(len(pts) - 1):  # (a pair on exactly one beat, not a hair apart)
        p, q = pts[i:i + 2]
        if round(p[0], 9) == round(q[0], 9):
            q[0] = p[0]
    drop_loose(pts)
    return pts


def clean_heard(h):
    """A part's heard tempo line (point[7]) from a file / undo step -> a safe one, None when it has nothing."""
    if not isinstance(h, dict):
        return None
    out = {"line": _clean_points(h.get("line"), inner=True)}
    if h.get("visual") is True:
        out["visual"] = True
    end = h.get("end")
    if isinstance(end, (int, float)) and not isinstance(end, bool) and math.isfinite(end) and end > 0:
        out["end"] = clamp_bpm(end)
    return out if out["line"] or "visual" in out or "end" in out else None


def heard_of(p):
    """A point's heard tempo line (point[7]: {"line", "visual"?}), None without one."""
    return p[7] if len(p) > 7 and p[7] else None


def shift_heard(p, db, floor=0.0):
    """Point p's heard tempo line moved db beats (in place; it's in song beats: a copied / pasted / moved part takes
    it along, hunt 2026-10-24); never before floor (None: a copy's own beats, may be below 0)."""
    for q in heard_line(p):
        q[0] = q[0] + db if floor is None else max(floor, q[0] + db)


def heard_line(p):
    """A Keep timing part's own heard tempo points ([] = it plays the tempo heard just before it all along)."""
    h = heard_of(p)
    return h["line"] if h else []


def drop_loose(pts):
    """A jump's first point (where a glide arrives) taken out, in place, when no curved part arrives at it any more
    (held / deleted / dragged after a held part) or it is the same BPM as the second: it played nothing and
    couldn't be used (user 2026-10-22). True when one went."""
    if len(pts) > 64:  # (only the pairs looked at: 190,000 points one by one took ~30 ms each change, and every
        return _drop_loose_many(pts)  # point taken out of a big list moved all after it)
    gone = False
    i = 0
    while i + 1 < len(pts):
        p, q = pts[i], pts[i + 1]
        if p[0] == q[0]:
            before = pts[i - 1] if i > 0 else None
            glides = before is not None and before[2] != "hold" and before[1] != p[1] and before[0] < p[0]
            if not glides or p[1] == q[1]:
                del pts[i]
                gone = True
                continue
        i += 1
    return gone


def _drop_loose_many(pts):
    """drop_loose for a long list, the same points taken out: only the beats two points share are looked at (the
    loop above keeps every other point and moves on), the list made again once."""
    ce = col_entry(pts) if len(pts) >= BIG_LINE else None  # (a big line's beats: read once per change)
    beats = ce[3] if ce is not None else np.fromiter((p[0] for p in pts), float, len(pts))
    pairs = np.flatnonzero(beats[1:] == beats[:-1]).tolist()
    if not pairs:
        return False
    gone = []
    for i in pairs:  # (point i against point i + 1; what's before i = the nearest point before it still there)
        p, q = pts[i], pts[i + 1]
        k, g = i - 1, len(gone) - 1
        while k >= 0 and g >= 0 and gone[g] == k:  # (the points taken out just before it: a run ending at i - 1)
            k -= 1
            g -= 1
        before = pts[k] if k >= 0 else None
        glides = before is not None and before[2] != "hold" and before[1] != p[1] and before[0] < p[0]
        if not glides or p[1] == q[1]:
            gone.append(i)
    if not gone:
        return False
    if len(gone) > 16:
        out = set(gone)
        pts[:] = [p for k, p in enumerate(pts) if k not in out]
    else:
        for i in reversed(gone):
            del pts[i]
    return True


def catch_set(c, k, v):
    """A catch-up line's start (k = 3, the yellow area's left edge) or end (k = 4, the teal area's right edge) set
    by hand, or None = where Spiderweb puts it; k = 5: its bend [x, y] (None = CATCH_BEND); k = 6: its drawn points
    (None: none drawn yet); k = 7: its own Interval in ticks (None: tempo["every"]). Kept short: [beat, shape,
    formula] when none is set."""
    while len(c) <= k:
        c.append(None)
    c[k] = None if v is None or k == 5 and v == CATCH_BEND else list(v) if k == 5 else \
        [list(p) for p in v] if k == 6 else int(v) if k == 7 else float(v)
    while len(c) > 3 and c[-1] is None:
        c.pop()


def catch_get(c, k):
    return c[k] if len(c) > k else None


_formulas = {}


def formula_fn(text):
    """The formula of x as a function (None if it can't be read). Remembered: the pane draws often."""
    if text not in _formulas:
        try:
            fn = formula(text or FORMULA)
            fn(0.5)
        except (ValueError, ArithmeticError, TypeError):
            fn = None
        if len(_formulas) > 200:
            _formulas.clear()
        _formulas[text] = fn
    return _formulas[text]


def bend_of(p):
    """A point's part bend [x, y] (BEND = straight)."""
    return p[6] if len(p) > 6 and p[6] else BEND


def set_bend(p, bend):
    """Point p's part bend set (its heard tempo line after it kept)."""
    p.extend([False] * (6 - len(p)) + [None] * (len(p) < 7))
    p[6] = bend


def set_heard(p, h):
    """Point p's heard tempo line (point[7]) set, None = none; its own Interval (point[8]) kept."""
    step = p[8] if len(p) > 8 else None
    p.extend([False] * (6 - len(p)) + [list(BEND)] * (len(p) < 7))
    del p[7:]
    if h or step is not None:
        p.append(h or None)
    if step is not None:
        p.append(step)


# OWN INTERVAL (user 2026-11-12, like Domino's "snapshot": a ramp keeps the Interval it was drawn with; changing the
# Interval box later never steps older ramps again; with ramps picked, the box shows / sets theirs): point[8] =
# its part's Interval in ticks, or [Interval, offset] (its steps counted from offset ticks after its first tick: a
# piece of a ramp Change visual time signature stretched keeps the old staircase); none = tempo["every"], which the
# Interval box sets for new ramps (it stamps the old value on every ramp without one first). A curved catch-up:
# catch[7].

def own_step(p):
    """Point p's own Interval: (ticks, offset) or None (follows tempo["every"])."""
    v = p[8] if len(p) > 8 else None
    if isinstance(v, list):
        return int(v[0]), int(v[1])
    return None if v is None else (int(v), 0)


def step_of(p, every):
    """(Interval, offset) point p's part steps by: its own, else every (tempo["every"]) from its first tick."""
    return own_step(p) or (int(every), 0)


def set_step(p, every, off=0):
    """Point p's own Interval set (None: follows tempo["every"] again); off = where its steps start after its first
    tick (kept within one Interval)."""
    if every is None:
        if len(p) > 8:
            del p[8:]
            while len(p) > 7 and p[-1] is None:
                p.pop()
        return
    every = int(every)
    off = int(off) % every
    p.extend([False] * (6 - len(p)) + [list(BEND)] * (len(p) < 7))
    if len(p) < 8:
        p.append(None)
    del p[8:]
    p.append([every, off] if off else every)


def copy_step(p, src, offset=True):
    """src's own Interval onto point p (offset False: only the Interval, its steps from p's own first tick)."""
    got = own_step(src)
    if got is not None:
        set_step(p, got[0], got[1] if offset else 0)


def catch_step(c, every):
    """A curved catch-up's Interval: its own (catch[7]), else every (tempo["every"])."""
    v = catch_get(c, 7)
    return int(v) if v is not None else int(every)


def _clean_step(v):
    """point[8] / catch[7] from a file / undo step -> a safe one, None when broken."""
    def whole(x, lo, hi):
        return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi
    if whole(v, 1, 65535):
        return v
    if isinstance(v, list) and len(v) == 2 and whole(v[0], 1, 65535) and whole(v[1], 0, 65534) and v[1] < v[0]:
        return list(v) if v[1] else v[0]
    return None


def stamp_steps(tempo, every):
    """Every curved part and curved catch-up (heard tempo lines' parts too) without an Interval of its own gets
    every (the Interval box's value before it changes: user 2026-11-12, older ramps keep theirs). True when any
    did."""
    did = False

    def stamp(pts):
        nonlocal did
        for i, p in enumerate(pts[:-1]):
            if p[2] != "hold" and own_step(p) is None:
                set_step(p, every)
                did = True
    stamp(tempo["points"])
    for p in tempo["points"]:
        h = heard_of(p)
        if h and h.get("line"):
            stamp(h["line"])
    for c in tempo.get("catch", []):
        if c[1] != "flat" and catch_get(c, 7) is None:
            catch_set(c, 7, every)
            did = True
    return did


def bent(t, bend):
    """A curve from (0, 0) to (1, 1) bent through its control point bend (0..1 each way, so it never goes past
    either end: user, like the velocity pane's curve), read at times t."""
    cx, cy = bend
    a = 1 - 2 * cx
    if abs(a) < 1e-9:
        s = t
    else:  # (x(s) = a s^2 + 2 cx s: the place along the curve at each time)
        s = (-2 * cx + np.sqrt(np.maximum(4 * cx * cx + 4 * a * t, 0))) / (2 * a)
    s = np.clip(s, 0, 1)
    return 2 * s * (1 - s) * cy + s * s


def curve(kind, text, t, bend=None):
    """How far along (0 at the part's start point, 1 at its end) a part of this curve is at t (0..1, array). Line:
    bent by its handle (bend); S-curve: that bend on the first half, mirrored on the second."""
    t = np.asarray(t, float)
    bend = bend or BEND
    if kind == "line":
        return t if bend == BEND else bent(t, bend)
    if kind == "s":
        lo = t < 0.5
        return np.where(lo, bent(np.minimum(2 * t, 1), bend) / 2, 1 - bent(np.minimum(2 * (1 - t), 1), bend) / 2)
    if kind == "formula":
        return _apply(formula_fn(text), t)
    return np.zeros_like(t)  # hold: the start point's BPM, then a jump


FORMULA_SAMPLES = 4096  # a formula worked out at most this many times per part (in between: read off a straight line)
_samples = {}  # formula function -> (grid, its values there): _apply's samples, the same every time


def _apply(fn, t):
    if fn is None:
        return np.zeros_like(t)
    if t.size > FORMULA_SAMPLES + 1:  # (a long curve changes on every tick: tens of thousands)
        got = _samples.get(fn)
        if got is None:  # (worked out once per formula: a long ramp read in thousands of pieces worked them out
            if len(_samples) > 200:  # again for every piece, 95 % of its time)
                _samples.clear()
            grid = np.linspace(0.0, 1.0, FORMULA_SAMPLES + 1)
            got = _samples[fn] = (grid, _apply(fn, grid))
        return np.interp(t, *got)
    out = np.empty_like(t)
    flat = t.ravel()
    res = out.ravel()
    for i, x in enumerate(flat.tolist()):
        try:
            v = fn(x)
            res[i] = v if math.isfinite(v) else 0.0
        except (ValueError, ArithmeticError, TypeError):
            res[i] = 0.0
    return out


def catch_weight(shape, text, t, bend=None):
    """The catch-up's shape at t (0..1): how much of its push each moment gets (0 = the project's BPM). Flat = the
    same all along; ease = fast then slow (all at the start, none at the end); rise = slow then fast; both bent by
    their handle (bend: the control point, x along, y = push; CATCH_BEND: (1 - t)^2 / t^2); ramp = up and back
    down."""
    t = np.asarray(t, float)
    if shape == "drawn":  # (bend: its drawn points)
        return drawn_at(bend or DRAWN, t)
    cx, cy = bend or CATCH_BEND
    if shape == "ease":  # (from 1 to 0: bent() upside down)
        return 1 - bent(t, (cx, 1 - cy))
    if shape == "rise":
        return bent(t, (cx, cy))
    if shape == "ramp":
        return np.sin(np.pi * t)
    if shape == "formula":
        return _apply(formula_fn(text), t)
    return np.ones_like(t)


def weight(st, t):
    """A stretch's catch-up shape at t (catch_weight with its own bend / drawn points)."""
    return catch_weight(st["shape"], st["text"], t, st.get("drawn") if st["shape"] == "drawn" else st.get("bend"))


def _bent(t, m):
    """A stretch 0..1 bent so its middle is at height m (0.5: straight), as Copy along a path's Speed graph."""
    if abs(m - 0.5) < 1e-9:
        return t
    if m < 0.5:
        return t ** (math.log(m) / math.log(0.5))
    return 1 - (1 - t) ** (math.log(1 - m) / math.log(0.5))


def drawn_at(pts, t):
    """A drawn catch-up's push share (0..1) at t (0..1, array): its points joined by bent stretches."""
    t = np.clip(np.asarray(t, float), 0.0, 1.0)
    out = np.full(t.shape, float(pts[-1][1]))
    for (u0, p0, m), (u1, p1, _) in zip(pts, pts[1:]):
        inside = (t >= u0) & (t < u1) if u1 > u0 else np.zeros(t.shape, bool)
        out[inside] = p0 + (p1 - p0) * _bent((t[inside] - u0) / (u1 - u0), m)
    return out


def clean_drawn(g):
    """Drawn points from a file -> valid ones (from along 0 to 1, along never back, shares 0..1, bends in
    DRAWN_BENDS), or None."""
    if not isinstance(g, list) or len(g) < 2:
        return None
    out = []
    for p in g[:DRAWN_POINTS]:
        if not (isinstance(p, list) and len(p) == 3 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in p)):
            return None
        u, v, m = min(1.0, max(0.0, float(p[0]))), min(1.0, max(0.0, float(p[1]))), float(p[2])
        out.append([max(u, out[-1][0]) if out else u, v, min(DRAWN_BENDS[1], max(DRAWN_BENDS[0], m))])
    out[0][0], out[-1][0] = 0.0, 1.0
    return out


def drawn_from(shape, text, bend):
    """The points a drawn catch-up starts from (user: the shape picked before it, drawn): a few points read off
    that shape (highest = 100 %), each stretch's bend through the shape's middle there."""
    at = {"flat": [0.0, 1.0], "ease": [0.0, 1.0], "rise": [0.0, 1.0], "ramp": [0.0, 0.5, 1.0]}.get(
        shape, [k / 8 for k in range(9)])
    w = catch_weight(shape, text, at, bend)
    mids = catch_weight(shape, text, [(a + b) / 2 for a, b in zip(at, at[1:])], bend)
    top = max(float(np.max(w)), float(np.max(mids)))
    if top <= 1e-9:
        return [list(p) for p in DRAWN]
    w, mids = np.clip(w / top, 0, 1), np.clip(mids / top, 0, 1)
    out = []
    for i, u in enumerate(at):
        m = 0.5
        if i + 1 < len(at) and abs(w[i + 1] - w[i]) > 1e-6:
            m = min(DRAWN_BENDS[1], max(DRAWN_BENDS[0], float((mids[i] - w[i]) / (w[i + 1] - w[i]))))
        out.append([float(u), round(float(w[i]), 6), round(m, 6)])
    return out


# ------------------------------------------------------------ the drawn line and its stretches

def catch_end(tempo, k):
    """Where catch-up line k's catch-up ends: its teal area's right edge set by hand, else the next point; never
    past the next catch-up line. None when nothing ends it."""
    c, lines = tempo["catch"][k], [x[0] for x in tempo["catch"]]
    after = [p[0] for p in tempo["points"] if p[0] > c[0]] + lines[k + 1:k + 2]
    end = min(after) if after else None
    if catch_get(c, 4) is not None and catch_get(c, 4) > c[0]:  # (a pin the line went past: waits, stretches)
        end = min([catch_get(c, 4)] + lines[k + 1:k + 2])
    return end


def catch_ends(tempo):
    """catch_end of every catch-up line, in order. Points in order, as they nearly always are: found by bisect, the
    same answers as catch_end's walk (thousands of catch-ups walked every point each, PLAN #12 B; keep_parts too,
    2026-10-29)."""
    pb = [p[0] for p in tempo["points"]]
    cs = tempo["catch"]
    if not all(x <= y for x, y in zip(pb, pb[1:])):
        return [catch_end(tempo, k) for k in range(len(cs))]
    ends = []
    for k, c in enumerate(cs):
        j = bisect.bisect_right(pb, c[0])
        after = pb[j:j + 1] + [x[0] for x in cs[k + 1:k + 2]]
        end = min(after) if after else None
        if catch_get(c, 4) is not None and catch_get(c, 4) > c[0]:
            end = min([catch_get(c, 4)] + [x[0] for x in cs[k + 1:k + 2]])
        ends.append(end)
    return ends


def stretches(tempo, base):
    """The catch-up lines' stretches: [{"from": beat the stretch (the yellow area + the catch-up) starts, "read":
    the beat whose tempo it's paid back to (= "end"; None when there's no end), "manual": "from" set by hand,
    "line": the line's beat = where the catch-up starts, "end": where it ends (the next point or catch-up line
    after it; None when there's none), "shape", "text"}].

    Paid back to the tempo the line comes back to after the catch-up (user 2026-10-21: the yellow area counts
    everything in it). Where it starts (the yellow area, dragged by its left edge = catch[3]; a song may change
    tempo for real before a jump): by hand = there. Else, walking back from the line, the last point at the BPM
    the line comes back to = where the jump left it; the stretch starts where the line leaves it (that point when
    its part curves away, else the next point). None at that BPM: the first point after the stretch before."""
    pts = tempo["points"]
    pb = [p[0] for p in pts]
    ordered = all(x <= y for x, y in zip(pb, pb[1:]))
    cs = tempo["catch"]
    ends = catch_ends(tempo)
    prevs, prev = [], 0.0
    for end in ends:
        prevs.append(prev)
        prev = prev if end is None else end
    out, arrays = [None] * len(ends), None
    for k in range(len(ends) - 1, -1, -1):  # (from the last: each may read the next one's)
        c, end, prev = tempo["catch"][k], ends[k], prevs[k]
        b, shape, text = c[:3]
        hand, hand_end = catch_get(c, 3), catch_get(c, 4)
        start, read = prev, None
        if end is not None:  # (user: the yellow area counts everything in it, at the tempo the line comes back to:
            # the next point at or after the end, also when it ends at another catch-up line or by hand mid-stop)
            if ordered:
                j = bisect.bisect_left(pb, end - 1e-12)
                read = pb[j] if j < len(pb) else end
            else:
                read = min([x for x in pb if x >= end - 1e-12] or [end])
            nxt = out[k + 1] if k + 1 < len(out) else None
            if nxt and nxt["read"] is not None and nxt["from"] < nxt["line"] and abs(nxt["from"] - read) <= 1e-9:
                read = nxt["read"]  # (ending where the next stop starts: paid back to the tempo that one comes
                #                     back to; user: a stretch pasted right after itself changes nothing)
        if end is not None and hand is not None and hand < b:
            start = max(prev, hand)
        elif end is not None:
            if arrays is None:  # (made once for every catch-up: 200 catch-ups x 8,000 points, speed hunt 2026-10-29)
                arrays = point_arrays(pts)
            on = float(drawn_bpm(tempo, base, [read], arrays)[0])
            if ordered:
                cands = list(range(bisect.bisect_left(pb, prev), bisect.bisect_left(pb, b)))
            else:
                cands = [i for i, x in enumerate(pb) if prev <= x < b]
            if cands:
                start = pb[cands[0]]
            for n in range(len(cands) - 1, -1, -1):
                i = cands[n]
                if abs(pts[i][1] - on) <= 1e-9 * on:
                    leaves = pts[i][2] != "hold" and i + 1 < len(pts) and pts[i + 1][1] != pts[i][1]
                    start = pb[i] if leaves else (pb[cands[n + 1]] if n + 1 < len(cands) else b)
                    break
        # (an edge set by hand the line was dragged past STAYS, only not used: the line back = the area back,
        # user 2026-10-24; shown as a thick line, "pin" / "pin_end")
        out[k] = {"from": start, "read": read, "manual": hand is not None and hand < b,
                  "manual_end": hand_end is not None and hand_end > b,
                  "pin": hand if hand is not None and hand >= b else None,
                  "pin_end": hand_end if hand_end is not None and hand_end <= b else None,
                  "line": b, "end": end, "shape": shape, "text": text, "bend": catch_get(c, 5),
                  "drawn": catch_get(c, 6), "every": catch_step(c, tempo.get("every", EVERY))}
    return out


BIG_LINE = 2000  # (ds5: from this many points the shared column view below is used)
_COLS = []  # (ds5) [[points list, version, n, beats, BPMs, log2 BPMs or None, plain]] the last few lists looked at
_ig0, _ig1, _ig2, _ig5 = (__import__("operator").itemgetter(k) for k in (0, 1, 2, 5))


def _col_owner(pts):
    """Whose change number a points list's column read may be remembered by: the watched line's (changed()) or the
    held drag preview's (hold()); None = any other list (a change's working copy, edited in place while it is read
    again: read fresh every time, never remembered, as before the column read)."""
    for line, ver in ((_text["tempo"], ("watched", _text["ver"])), (_held["tempo"], ("held", _held["n"]))):
        if isinstance(line, dict) and line.get("points") is pts:
            return ver
    return None


def col_entry(pts):
    """(ds5) ONE shared read of a big points list (~12 ms for 192,000 points, made once per tempo change = per
    watch / changed() version): its beats + BPMs as arrays, and `plain` = every point an ordinary held 7-field point
    (hold, no Keep timing, no heard line / own Interval) so the quick lanes below may skip per-point checks. None for
    a small list. Remembered ONLY for the watched / held line (_col_owner): another list is read fresh each call."""
    n = len(pts)
    if n < BIG_LINE:
        return None
    ver = _col_owner(pts)
    for e in _COLS if ver is not None else ():
        if e[0] is pts and e[1] == ver and e[2] == n:
            if _CHECK and not (np.array_equal(e[3], np.fromiter(map(_ig0, pts), float, n))
                               and np.array_equal(e[4], np.fromiter(map(_ig1, pts), float, n))
                               and e[10] == [i for i, p in enumerate(pts) if len(p) > 5 and p[5] is True]):
                _stale("TEMPO COLUMNS STALE")  # (the watched line changed in place without changed())
                raise RuntimeError("TEMPO COLUMNS STALE: the tempo line changed without tempo.changed()")
            return e
    beats = np.fromiter(map(_ig0, pts), float, n)
    vs = np.fromiter(map(_ig1, pts), float, n)
    lens = set(map(len, pts))
    if min(lens) < 7:  # (not a cleaned line: the careful ways)
        return None
    kinds = set(map(_ig2, pts))
    hold = np.ones(n, bool) if kinds == {"hold"} else np.fromiter((k == "hold" for k in map(_ig2, pts)), bool, n)
    nokeep = max(lens) == 7 and set(map(_ig5, pts)) <= {False}  # (no Keep timing, no heard line / own Interval)
    plain = nokeep and bool(hold.all())
    cand = np.zeros(n, bool)  # (point i's part is a curve: not held, next BPM differs, goes on in beats)
    cand[:-1] = ~hold[:-1] & (vs[1:] != vs[:-1]) & (beats[1:] > beats[:-1])
    keeps = [] if nokeep else [i for i, p in enumerate(pts) if p[5] is True]  # (keep_points' answer)
    e = [pts, ver, n, beats, vs, None, plain, hold, nokeep, cand, keeps]
    if ver is not None:
        _COLS.insert(0, e)
        del _COLS[3:]
    return e


def point_arrays(pts):
    """(beats, BPMs, log2 BPMs) of the points as arrays: drawn_bpm's, made once for many reads (2026-10-29: a Fill's
    2,000 catch-ups made them again for every curved part, 4,000 points x 4,000 parts)."""
    e = col_entry(pts)
    if e is not None:
        if e[5] is None:
            e[5] = np.log2(e[4])
        return e[3], e[4], e[5]
    vs = np.array([p[1] for p in pts])
    return np.array([p[0] for p in pts]), vs, np.log2(vs)


def drawn_bpm(tempo, base, beats, arrays=None):
    """The drawn line's BPM at beats (array): the points' curves, the project's BPM before the first point. A
    catch-up's part shows the drawn line here (catch_bpm has its own). arrays: point_arrays(tempo["points"])."""
    beats = np.asarray(beats, float)
    pts = tempo["points"]
    out = np.full(beats.shape, float(base))
    if not pts:
        return out
    pb, vs, lv = arrays or point_arrays(pts)
    idx = np.searchsorted(pb, beats, "right") - 1  # the point at or before each beat (-1: before the first)
    has = idx >= 0
    out[has] = vs[idx[has]]
    asked = idx[has & (idx < len(pts) - 1)]
    ce = col_entry(pts) if len(asked) > 64 else None  # (a few beats: the loop below is quicker than a column read
    if ce is not None:  # of a list that isn't remembered) (only the curved parts among them: the loop `continue`s
        asked = asked[ce[9][asked]]  # on all the others)
    if len(asked) > 64 and asked[0] == asked[-1] and bool((asked == asked[0]).all()):
        asked = asked[:1]  # (one part asked about, e.g. a long ramp read in pieces: np.unique's answer, quicker)
    else:
        asked = np.unique(asked)
    for i in asked.tolist():  # (only the parts asked about: a song with
        kind = pts[i][2]  # thousands of points read part by part was slow, hunt 2026-10-22)
        if kind == "hold" or pts[i][1] == pts[i + 1][1] or pb[i + 1] <= pb[i]:
            continue
        on = idx == i
        if on.any():
            c = curve(kind, pts[i][3], (beats[on] - pb[i]) / (pb[i + 1] - pb[i]), bend_of(pts[i]))
            if pts[i][4] == "exp":  # (the same ratio every beat: straight on the pane's doubling scale)
                out[on] = 2 ** (lv[i] + (lv[i + 1] - lv[i]) * c)
            else:  # linear: the same BPM added every beat
                out[on] = vs[i] + (vs[i + 1] - vs[i]) * c
    return np.clip(out, MIN_BPM, MAX_BPM)


def _solve_push(st, beats, seconds, base, n=512):
    """The push A that makes a catch-up of the stretch's shape (BPM = base x 2^(A x weight)) last `seconds` over
    `beats`. None when it can't (a shape with no push anywhere, or past what a MIDI file can hold). Remembered by
    its inputs (2026-10-29: after any edit the pane worked out all 2,000 catch-ups of a Fill again, 1.3 ms each)."""
    w = weight(st, (np.arange(n) + 0.5) / n)
    key = (hashlib.blake2b(w.tobytes(), digest_size=16).digest(), beats, seconds, base, n)  # (the shape's fingerprint)
    if key in _pushes:
        return _pushes[key]
    if len(_pushes) > PUSHES_KEPT:
        _pushes.clear()
    got = _pushes[key] = _push_of(w, beats, seconds, base, n)
    return got


PUSHES_KEPT = 100_000  # _solve_push's answers remembered (~200 bytes each)
_pushes = {}


def _push_of(w, beats, seconds, base, n):
    if not np.any(w > 1e-9) or np.any(w < -1e-9) or seconds <= 0:
        return None
    lo_b, hi_b = math.log2(MIN_BPM / base), math.log2(MAX_BPM / base)

    def took(a):
        return float(np.sum(60.0 / (base * 2 ** np.clip(a * w, lo_b, hi_b)))) * beats / n

    lo, hi = -64.0, 64.0
    if not took(hi) <= seconds <= took(lo):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if took(mid) > seconds:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def beat_tick(b, ppq):
    """The tick a beat starts on in the file: the smallest tick t with t / ppq >= b, the rule the drawn line's
    points use (_first_ticks), so a catch-up ending on a point ends where the point's tempo starts."""
    return _first_ticks([b], ppq)[0]


def base_us(base):
    """The project BPM's microseconds per beat with a tempo line: the nearest Domino-safe value (user's rule: every
    value written survives a Domino save), at most a microsecond a beat off."""
    return int(nearest_safe([60_000_000 / base])[0])


def held_us(bpm):
    """A held part's microseconds per beat: a BPM that is exactly a file's whole microseconds (a loaded MIDI's own
    tempo, loaded.conductor_line) keeps them as they are (user 2026-10-24: the file's values stay); any other =
    what Domino itself writes for that BPM (its float32, 60e6 / it cut to a whole number; user 2026-11-01: 252 came
    out 251.99 in Domino. A MIDI re-saved in Domino may slip a microsecond there, as Domino's own files do: checked
    on the user's 252 song)."""
    u = round(60_000_000 / bpm)
    if 1 <= u <= 0xFFFFFF and 60_000_000 / u == bpm:
        return u
    u = int(60_000_000 / float(np.float32(bpm)))
    if 1 <= u <= 0xFFFFFF:
        return u
    return int(nearest_safe([60_000_000 / bpm])[0])


def _held_us_many(bpm, base, base_u):
    """held_us of each BPM (array) as an int64 array, the project's own BPM = base_u: the same numbers as one at a
    time (Python's round and NumPy's rint both round halves to even; int() of a positive = trunc)."""
    bpm = np.asarray(bpm, float)
    out = np.zeros(len(bpm), np.int64)
    if not len(bpm):
        return out
    with np.errstate(all="ignore"):
        u1 = np.rint(60_000_000 / bpm)
        ok1 = (u1 >= 1) & (u1 <= 0xFFFFFF) & (60_000_000 / u1 == bpm)
        u2 = np.trunc(60_000_000 / bpm.astype(np.float32).astype(float))
        ok2 = ~ok1 & (u2 >= 1) & (u2 <= 0xFFFFFF)
    out[ok1] = u1[ok1].astype(np.int64)
    out[ok2] = u2[ok2].astype(np.int64)
    for k in np.flatnonzero(~(ok1 | ok2)).tolist():
        try:
            out[k] = held_us(float(bpm[k]))
        except (ValueError, OverflowError):  # (a curved part's own reading: never used as held)
            out[k] = 0
    out[bpm == base] = base_u
    return out


def typed_bpms(tempo):
    """{us: the f32 BPM typed}: each point's BPM by the microseconds held_us gives it, so a Domino copy / .dms
    writes the BPM as typed (252, not another f32 that also gives 238,095 us; user 2026-11-01)."""
    out = {}
    pts = tempo.get("points") or ()
    ce = col_entry(pts) if len(pts) >= BIG_LINE else None
    if ce is not None:  # (ds5: once per DISTINCT BPM, in the order each first shows: the same answer; 7 of 192,000)
        vs, first = np.unique(ce[4], return_index=True)
        vals = vs[np.argsort(first, kind="stable")].tolist()
    else:
        vals = [p[1] for p in pts]
    for v in vals:
        try:
            out.setdefault(held_us(v), float(np.float32(v)))
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            pass
    return out


def _first_ticks_np(pb, ppq):
    """_first_ticks' all-at-once steps as an int64 array (None when they can't be used: the one-by-one way)."""
    a = np.asarray(pb, float)
    if a.ndim == 1 and np.isfinite(a).all() and (np.abs(a) * ppq).max() < 2.0 ** 52:
        c = np.maximum(0, np.ceil(a * ppq)).astype(np.int64)
        on = np.ones(len(c), bool)
        for _ in range(4):
            down = on & (c > 0) & ((c - 1) / ppq >= a)
            up = on & ~down & (c / ppq < a)
            c[down] -= 1
            c[up] += 1
            on = down | up
            if not on.any():
                break
        return c
    return None


class Segs(list):
    """(ds5) _segments' pieces [(a, b, us)] of a big all-held line, with the same pieces as arrays (.arr = (starts,
    us)) so _runs / _seg_events needn't read 190,000 tuples back."""
    arr = None


def _first_ticks(pb, ppq):
    """The first tick of each point's part: the smallest tick t with t / ppq >= the point's beat (as drawn_bpm
    reads it)."""
    if len(pb) > 64:  # (all at once, the same steps: a loaded MIDI's 35,000 points took 15 ms every drag step)
        c = _first_ticks_np(pb, ppq)
        if c is not None:
            return c.tolist()
    out = []
    for b in pb:
        c = max(0, math.ceil(b * ppq))
        for _ in range(4):  # (a step or two at most; huge beats stop changing per tick and looped for good, hunt
            if c > 0 and (c - 1) / ppq >= b:  # 2026-10-25)
                c -= 1
            elif c / ppq < b:
                c += 1
            else:
                break
        out.append(c)
    return out


def _segments(tempo, base, base_u, ppq, t0, t1, ordered=None):
    """The drawn line's tempo from tick t0 up to t1 (exclusive) as [(a, b, us)]: us one whole number for a held
    part, an array (one per tick) for a curved one (it changes on every tick, like a line drawn in many MIDI
    programs). Never one entry per tick of a held part (a long song would fill the memory). ordered: the points
    are in beat order (None = checked here; layout checks once for its many calls)."""
    out = []
    if t1 <= t0:
        return out
    pts = tempo["points"]
    # only the points whose parts can reach ticks t0..t1 (+ one each side; a tick's margin for rounding): a catch-up
    # per jump read the whole line each time, thousands of jumps took minutes (PLAN #12 B)
    if ordered is None:
        whole = col_entry(pts) if len(pts) >= BIG_LINE else None  # (a big line: its beats read once per change)
        ordered = bool((whole[3][1:] >= whole[3][:-1]).all()) if whole is not None else all(
            p[0] <= q[0] for p, q in zip(pts, pts[1:]))
    p_lo, p_hi = 0, len(pts)  # (out of order, e.g. half way through a change: every point, as before)
    if ordered:
        p_lo = max(0, bisect.bisect_right(pts, (t0 - 1) / ppq, key=lambda p: p[0]) - 1)
        p_hi = min(len(pts), bisect.bisect_left(pts, (t1 + 1) / ppq, key=lambda p: p[0]) + 1)
    # (ds5: the whole big line = its shared column read (col_entry), not a copy + point by point reads)
    ce = col_entry(pts) if p_lo == 0 and p_hi == len(pts) else None
    near = {"points": pts if ce is not None else pts[p_lo:p_hi]}  # (drawn_bpm reads only these: the beats asked are all inside their parts)
    fnp = _first_ticks_np(ce[3], ppq) if ce is not None else None
    k0 = 0 if p_lo == 0 else p_lo + 1
    arrays = point_arrays(near["points"]) if near["points"] else None
    if fnp is not None:  # (ds5: the whole big line: every step on arrays; the tick list only if a part isn't held)
        n_p = len(pts)
        e_arr = np.concatenate([[0], fnp, [max(t1, int(fnp[-1]) + 1)]]).astype(np.int64)
        edges = None
        n_e = len(e_arr)
        held_bpm = drawn_bpm(near, base, np.maximum(e_arr[:-1], t0) / ppq, arrays)
        a_arr, b_arr = np.maximum(e_arr[:-1], t0), np.minimum(e_arr[1:], t1)
        held_arr = np.ones(n_p + 1, bool)  # (part 0: before the first point; part j: point j - 1's)
        held_arr[1:n_p] = ce[7][:-1] | (arrays[1][:-1] == arrays[1][1:])
    else:
        first = _first_ticks([p[0] for p in near["points"]], ppq)
        # edges[k - k0] for parts k = p_lo .. p_hi (k = 0: before the first point; k = i + 1: point i's part)
        edges = ([0] if p_lo == 0 else []) + first + ([max(t1, first[-1] + 1) if first else t1] if p_hi == len(pts) else [])
        n_e = len(edges)
        # (every held part's BPM read at once: one read per part made a loaded MIDI with 35,000 tempo changes hang)
        held_bpm = drawn_bpm(near, base, np.maximum(edges[:-1], t0) / ppq, arrays)
        # (every part's ends + a held part's microseconds at once, 2026-11-14: 190,000 short BPM changes one by one
        # took ~0.3 s a read; the same numbers as held_us one at a time)
        e_arr = np.asarray(edges, np.int64)
        a_arr, b_arr = np.maximum(e_arr[:-1], t0), np.minimum(e_arr[1:], t1)
        ii = np.arange(len(edges) - 1) + k0 - 1  # (the point each part starts at; -1 = before the first)
        nxt = np.minimum(ii + 1, len(pts) - 1) - p_lo  # (the next point, as near's index)
        cur = np.maximum(ii, 0) - p_lo
        near_pts = near["points"]
        if near_pts:
            vs = arrays[1]
            holds = np.fromiter((p[2] == "hold" for p in near_pts), bool, len(near_pts))
            same = vs[np.clip(cur, 0, len(near_pts) - 1)] == vs[np.clip(nxt, 0, len(near_pts) - 1)]
            held_arr = (ii < 0) | (ii == len(pts) - 1) | holds[np.clip(cur, 0, len(near_pts) - 1)] | same
        else:
            held_arr = np.ones(len(ii), bool)
    held_u = _held_us_many(held_bpm, base, base_u)
    if bool(held_arr[a_arr < b_arr].all()):  # (all held: no per-part steps to work out)
        on = a_arr < b_arr
        got = Segs(zip(a_arr[on].tolist(), b_arr[on].tolist(), held_u[on].tolist()))
        got.arr = (a_arr[on], held_u[on])  # (ds5: and the same as arrays: _runs / _seg_events read these)
        return got
    held_arr_np, held_u_np = held_arr, held_u
    held_u = held_u.tolist()
    every_all = tempo.get("every", EVERY)
    sparse = None
    if n_e >= BIG_LINE:  # (ds5: only the parts that are NOT held are visited; the held ones between in bulk)
        on = a_arr < b_arr
        sparse = np.flatnonzero(on & ~held_arr_np).tolist()
    if edges is None:
        edges = e_arr.tolist()
    todo, prev = (sparse + [None]) if sparse is not None else range(len(edges) - 1), 0
    for jn in todo:
        if sparse is not None:
            stop = len(edges) - 1 if jn is None else jn
            sl = on[prev:stop]
            out.extend(zip(a_arr[prev:stop][sl].tolist(), b_arr[prev:stop][sl].tolist(), held_u_np[prev:stop][sl].tolist()))
            if jn is None:
                break
            prev = jn + 1
        j = jn
        k = k0 + j
        a, b = max(edges[j], t0), min(edges[j + 1], t1)
        if b <= a:
            continue
        i = k - 1
        held = i < 0 or i == len(pts) - 1 or pts[i][2] == "hold" or pts[i][1] == pts[i + 1][1]
        if held:
            out.append((a, b, held_u[j]))
        else:  # (a new value every "every" ticks from the part's first tick; the first step the start point's
            # BPM, the last the end point's: step_shares; a part's own Interval: point[8])
            every, off = step_of(pts[i], every_all)
            origin = edges[j] + off
            part = (edges[j], edges[j + 1])
            jump = to_jump(pts, i)
            if (-(-(b - a) // every) if every >= BLOCKS_FROM else b - a) > RAMP_LAZY:  # (worked out when read)
                out.append((a, b, _lazy_ramp(tempo, near, arrays, i, part, every, origin, jump, base, base_u,
                                             a, b, ppq, pts[i][2] in ONE_WAY)))
                continue

            def read(starts):
                if jump:
                    return part_bpm(tempo, i, step_shares(*part, starts, every))
                return drawn_bpm(near, base, np.asarray(starts) / ppq, arrays)
            if every >= BLOCKS_FROM:  # (one entry per step, not per tick: long songs at a high PPQ stay quick)
                starts = np.arange(origin + (a - origin) // every * every, b, every)
                bpm = read(starts)
                us = nearest_safe(6e7 / bpm)
                us[bpm == base] = base_u
                for s, u in zip(starts.tolist(), us.tolist()):
                    lo, hi = max(s, a), min(s + every, b)
                    if out and out[-1][1] == lo and isinstance(out[-1][2], int) and out[-1][2] == u:
                        out[-1] = (out[-1][0], hi, u)
                    else:
                        out.append((lo, hi, u))
                continue
            ticks = np.arange(a, b)
            bpm = read(origin + (ticks - origin) // every * every)
            us = nearest_safe(6e7 / bpm)
            us[bpm == base] = base_u  # (the project's own tempo, exactly as the file starts)
            out.append((a, b, us))
    return out


RAMP_LAZY = 1 << 16  # a gliding piece with more steps than this (ticks, for an Interval under BLOCKS_FROM) comes as a
# tempo_ramp.Ramp: worked out when read, never every step kept (PLAN "Tempo ramps step by step")
ONE_WAY = ("line", "s")  # part curves that only go one way (bent lines + S-curves too: bent never passes an end):
# a long ramp's runs found by halving; a formula can turn back (read step by step)


def _lazy_ramp(tempo, near, arrays, i, part, every, origin, jump, base, base_u, a, b, ppq, one_way):
    """_segments' gliding piece a..b of point i's part as a Ramp: the same values its step list would have, read
    from its OWN copy of the few points it reads (the line edited later never changes it). The rule covers the
    whole part (every piece / window of it shares its runs) and is remembered by what it reads (_RULES)."""
    if jump:
        own = {"points": copy.deepcopy(tempo["points"][i:i + 2])}
    else:
        pb = arrays[0]
        lo = origin + (part[0] - origin) // every * every  # (the earliest step start the part reads)
        k_lo = max(0, int(np.searchsorted(pb, lo / ppq, "right")) - 1)
        k_hi = min(len(pb), int(np.searchsorted(pb, part[1] / ppq, "right")) + 1)
        own = {"points": copy.deepcopy(near["points"][k_lo:k_hi])}
    key = repr((own["points"], jump, part, every, origin, base, base_u, ppq, one_way))
    rule = _RULES.pop(key, None)
    if rule is not None:
        _RULES[key] = rule  # (the newest used last: the least used goes first when full)
    else:
        if jump:
            def fn(starts):
                return part_bpm(own, 0, step_shares(*part, starts, every))
        else:
            own_arr = point_arrays(own["points"])

            def fn(starts):
                return drawn_bpm(own, base, np.asarray(starts) / ppq, own_arr)

        def read(starts):
            bpm = fn(starts)
            us = nearest_safe(6e7 / bpm)
            us[bpm == base] = base_u
            return us
        while len(_RULES) >= RULES_KEPT:  # (only the least used one goes: clearing all made a line with 33+ long
            _RULES.pop(next(iter(_RULES)))  # ramps work every one out again on each read, hunt 2026-11-26)
        rule = _RULES[key] = RampRule(read, origin, every, part[0], part[1], one_way)
    return Ramp(a, b, rule)


RULES_KEPT = 256  # long ramps' rules remembered (their runs: a giant ramp's are worked out once)
_RULES = {}


CATCH_TICKS_MOST = 1 << 24  # a catch-up's stretch up to this is worked out on every tick; longer = _catch_long by runs
# (2^27 left stretches just under it needing GBs, hunt 2026-11-26, user took A)


def _us_sum(segs):
    return sum(u * (b - a) if isinstance(u, int) else int(u.sum()) for a, b, u in segs)


def _latest_tick(segs, total, e):
    """The last tick a catch-up could start at with at least 1 microsecond per beat left for each tick after it
    (the fastest a file holds), the drawn line up to it. None when even the whole stretch drawn leaves time."""
    acc, best = 0, None
    if any(isinstance(u, Ramp) for _, _, u in segs):  # (a long ramp: its runs as held pieces, never a tick each)
        flat = []
        for a, b, u in segs:
            if isinstance(u, Ramp):
                ra, rb, ru = u.spans()
                off = a - u.a
                flat += zip((ra + off).tolist(), (rb + off).tolist(), ru.tolist())
            else:
                flat.append((a, b, u))
        segs = flat
    for a, b, u in segs:
        if isinstance(u, int):
            if acc + e - a > total:
                return best
            if u > 1:
                x = (total - acc - e + u * a) // (u - 1)
                if x < b:
                    return x
            best = b
            acc += u * (b - a)
        else:
            f = acc + np.concatenate([[0], np.cumsum(u)]) + e - np.arange(a, b + 1)
            bad = np.flatnonzero(f > total)
            if len(bad):
                return a + int(bad[0]) - 1 if bad[0] > 0 else best
            best = b
            acc += int(u.sum())
    return None


def _put(segs, ra, rb, ru):
    """segs with ticks ra..rb replaced by ru (one whole number, or one per tick), kept in order."""
    out = []
    for a, b, u in segs:
        for lo, hi in ((a, min(b, ra)), (max(a, rb), b)):
            if hi > lo:
                out.append((lo, hi, u if isinstance(u, int) else u[lo - a:hi - a]))
    if rb > ra:
        out.append((ra, rb, ru))
    return sorted(out, key=lambda s: s[0])


def _put_all(segs, ra, rb, news):
    """segs with ticks ra..rb replaced by news ([(a, b, us)] covering ra..rb, e.g. a heard tempo line's)."""
    out = []
    for a, b, u in segs:
        for lo, hi in ((a, min(b, ra)), (max(a, rb), b)):
            if hi > lo:
                out.append((lo, hi, u if isinstance(u, int) else u[lo - a:hi - a]))
    return sorted(out + [s for s in news if s[1] > s[0]], key=lambda s: s[0])


def _put_many(segs, puts):
    """_put_all for each (ra, rb, news) of puts in turn, in ONE pass when the puts come in order without
    overlapping and segs are in order without overlapping (2026-10-29: 6,500 Keep timing parts each went through
    every piece = a minute before Play); anything else = one at a time, as before. The same answer."""
    def sane(spans):
        return all(b > a for a, b in spans) and all(b0 <= a1 for (_, b0), (a1, _) in zip(spans, spans[1:]))
    if not puts:  # (nothing to put: the pieces as they are; a sane list is in order already, as sorting gave)
        if isinstance(segs, Segs):  # (ds5: a copy that keeps its arrays)
            got = Segs(segs)
            got.arr = segs.arr
            return got
        return list(segs)
    if not sane([(ra, rb) for ra, rb, _ in puts]) or not sane([(a, b) for a, b, _ in segs]):
        for ra, rb, news in puts:
            segs = _put_all(segs, ra, rb, news)
        return segs
    # (2026-11-14: only the pieces a put touches are cut; the runs between are copied as they are, not piece by
    # piece: 190,000 short BPM changes with a few catch-ups / Keep timing parts. The same pieces in the same order)
    out, at_seg = [], 0
    ends = [b for _, b, _ in segs]
    starts = [a for a, _, _ in segs]
    cut = {}  # (piece index -> where it's cut: the parts of it left, in order)
    for ra, rb, _ in puts:
        lo = bisect.bisect_right(ends, ra)  # (the first piece ending after ra)
        hi = bisect.bisect_left(starts, rb)  # (pieces from here on start at / after rb)
        for k in range(lo, hi):
            a, b, u = segs[k]
            have = cut.get(k, [(a, b)])
            left = []
            for x, y in have:
                for lo2, hi2 in ((x, min(y, ra)), (max(x, rb), y)):
                    if hi2 > lo2:
                        left.append((lo2, hi2))
            cut[k] = left
    for k in sorted(cut):
        out += segs[at_seg:k]
        a, b, u = segs[k]
        out += [(x, y, u if isinstance(u, int) else u[x - a:y - a]) for x, y in cut[k]]
        at_seg = k + 1
    out += segs[at_seg:]
    out += [s for _, _, news in puts for s in news if s[1] > s[0]]
    return sorted(out, key=_first)


def _first(s):
    return s[0]


def catch_plan(tempo, base, st, ppq, real=None):
    """A stretch's catch-up worked out in the file's own whole numbers (ticks x microseconds), so the pane and the
    export always agree: {"ok", "push", "bpm" (flat: its BPM; else its fastest / slowest), "target" (the catch-up's
    ticks x microseconds), "latest": the last beat the line could sit at (shown when it can't), "empty": nothing
    after the line to end on}. The whole stretch takes as long as at st["base_u"] (the tempo heard just before
    it; the project's BPM when not given). real(a, b) = the time ticks a..b really take (Keep timing parts at
    their heard tempo); the drawn line when not given."""
    out = {"ok": False, "push": None, "bpm": None, "target": None, "latest": None, "empty": st["end"] is None,
           "fit": None, "why": "no_end"}  # (why: what stops it, for the pane's "Not possible" text; see WHY)
    if st["end"] is None:
        return out
    start_u = base_us(base)
    base_u = st.get("base_u") or start_u
    if real is None:
        def real(a, b):
            return _segments(tempo, base, start_u, ppq, a, b)
    s0, ln, e = (beat_tick(st[k], ppq) for k in ("from", "line", "end"))
    if e - ln <= 0:
        out["why"] = "short"
        return out
    if e - s0 > CATCH_TICKS_MOST:  # (tick by tick took gigabytes on a huge-tick song: by runs, PLAN stage D)
        return _catch_long(tempo, st, ppq, real, out, s0, ln, e, base_u)
    total = (e - s0) * base_u
    bpm = 60_000_000 / base_u
    every = st.get("every") or tempo.get("every", EVERY)  # (its own Interval, stretches)
    why = [None]
    w = weight(st, (np.arange(512) + 0.5) / 512)
    if not np.any(w > 1e-9) or np.any(w < -1e-9):  # (a shape that never works: said first, not the BPM limits
        out["why"] = "shape"                       # its advice can't fix, hunt 2026-10-24)
        return out

    def attempt(line):
        """(push, ticks' values) with the catch-up starting at tick line; None when it can't be written (why[0]
        says what stopped it)."""
        n, target = e - line, total - _us_sum(real(s0, line))
        if not n <= target <= n * SLOWEST:
            why[0] = "too_fast" if target < n else "too_slow"
            return None
        push = _solve_push(st, n / ppq, target / ppq / 1e6, bpm)
        if push is None:
            why[0] = "peak"
            return None
        t = (np.arange(n) + 0.5) / n
        ideal = 6e7 / np.clip(bpm * 2 ** (push * weight(st, t)), MIN_BPM, MAX_BPM)
        fit = _fit_blocks(ideal, target, every)  # (the file's own values: the pane says OK only when it works)
        if fit is None:
            why[0] = "fit"
            return None
        return push, fit, target

    got = attempt(ln)
    out["why"] = why[0]
    if got is None:
        latest = _latest_tick(real(s0, e), total, e)  # (fastest possible: right for a flat catch-up)
        if latest is not None and latest > s0 and st["shape"] != "flat":  # (others: the last line spot that works)
            lo, hi = s0, min(latest, e - 1)
            if attempt(lo) is None:
                latest = None
            else:
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    lo, hi = (mid, hi) if attempt(mid) is not None else (lo, mid)
                latest = lo
        out["latest"] = None if latest is None else latest / ppq
        return out
    push, fit, target = got
    w = weight(st, np.linspace(0, 1, 257))
    top = bpm * 2 ** float((push * w).max() if push > 0 else (push * w).min())
    out.update(ok=True, push=push, bpm=clamp_bpm(top), target=target, fit=fit)
    return out


CATCH_STEPS_MOST = 1 << 22  # a long curved catch-up's Interval steps at most: past it they're made longer (user
# 2026-11-26: same shape, still lands exactly; the export says so: "coarse"). ~7 s at the most (fit_block_runs'
# Domino-safe loop), said in the status line past CATCH_SAY_FROM steps
CATCH_SAY_FROM = 1 << 19


def _catch_long(tempo, st, ppq, real, out, s0, ln, e, base_u):
    """catch_plan for a stretch over CATCH_TICKS_MOST ticks (a huge-tick song), never one value a tick: a flat
    catch-up = its flat pair (two values, exact at any length); a curved one = _fit_blocks' way on its Interval
    steps (made longer past CATCH_STEPS_MOST), the last landing exactly. "fit" = a Ramp of runs. When it can't, the
    latest spot is the flat one's (no halving over attempts: each is seconds long)."""
    total = (e - s0) * base_u
    bpm = 60_000_000 / base_u
    every = int(st.get("every") or tempo.get("every", EVERY))
    w = weight(st, (np.arange(512) + 0.5) / 512)
    if not np.any(w > 1e-9) or np.any(w < -1e-9):
        out["why"] = "shape"
        return out
    why = [None]

    def attempt(line):
        n, target = e - line, total - _us_sum(real(s0, line))
        if not n <= target <= n * SLOWEST:
            why[0] = "too_fast" if target < n else "too_slow"
            return None
        push = _solve_push(st, n / ppq, target / ppq / 1e6, bpm)
        if push is None:
            why[0] = "peak"
            return None
        steps = -(-n // max(1, every))
        ev = max(1, every) if steps <= CATCH_STEPS_MOST else max(1, every) * -(-steps // CATCH_STEPS_MOST)
        starts = np.arange(0, n, ev, dtype=np.int64)
        ideal = 6e7 / np.clip(bpm * 2 ** (push * weight(st, (starts + 0.5) / n)), MIN_BPM, MAX_BPM)
        if st["shape"] == "flat" or len(starts) == 1 or ideal.max() - ideal.min() < 1:
            pair = flat_pair_runs(n, target)
            got = None if pair is None else ([], pair)
        else:
            hook = tempo_ramp.slow_hook if len(starts) > CATCH_SAY_FROM else None
            if hook:
                hook(True, "catch")
            try:
                got = fit_block_runs(ideal, np.diff(np.append(starts, n)), target)
            finally:
                if hook:
                    hook(False)
        if got is None:
            why[0] = "fit"
            return None
        vals, (a, b, k) = got
        last = int(starts[len(vals)])  # (the flat pair's first tick: after the blocks given values)
        ks, vs = starts[:len(vals)].tolist(), list(vals)
        if k > 0:  # (the last block: k ticks of A, then B)
            ks.append(last)
            vs.append(a)
        if k < n - last:
            ks.append(last + k)
            vs.append(b)
        return push, fixed_ramp(line, e, ks, vs), target, bool(vals) and ev != max(1, every)  # (flat: no steps)

    got = attempt(ln)
    out["why"] = why[0]
    if got is None:
        latest = _latest_tick(real(s0, e), total, e)
        out["latest"] = None if latest is None or latest <= s0 else latest / ppq
        return out
    push, fit, target, coarse = got
    w = weight(st, np.linspace(0, 1, 257))
    top = bpm * 2 ** float((push * w).max() if push > 0 else (push * w).min())
    out.update(ok=True, push=push, bpm=clamp_bpm(top), target=target, fit=fit, coarse=coarse)
    return out


def catch_bpm(base, st, plan, beats):
    """The catch-up's BPM at beats inside its part (for drawing), round the tempo heard before it."""
    t = (np.asarray(beats, float) - st["line"]) / max(1e-12, st["end"] - st["line"])
    w = weight(st, np.clip(t, 0, 1))
    return np.clip(st.get("base", base) * 2 ** (plan["push"] * w), MIN_BPM, MAX_BPM)


def keeps(p):
    """True for a point whose part is Keep timing (its notes keep the time they have at the tempo heard before)."""
    return len(p) > 5 and p[5] is True


def keep_points(pts):
    """The indexes of the points keeps() is True for, in order (one quick pass: a loaded MIDI's 35,000 points
    asked keeps() one by one several times every redraw). A big line: once per change (col_entry)."""
    ce = col_entry(pts) if len(pts) >= BIG_LINE else None
    if ce is not None:
        return list(ce[10])
    return [i for i, p in enumerate(pts) if len(p) > 5 and p[5] is True]


def keep_off(tempo, sts):
    """[(first, end beat)] of the catch-ups' yellow areas a Keep timing part is switched off in (user 2026-10-24: Keep
    timing doesn't work inside a catch-up's yellow area; it played the tempo the catch-up comes back to and could
    undo the catch-up). A catch-up whose line is inside a Keep timing part is that part's own: not these."""
    pts = tempo["points"]
    parts = [(pts[i][0], pts[i + 1][0] if i + 1 < len(pts) else math.inf) for i in keep_points(pts)]
    return [(st["from"], st["line"]) for st in sts if st["end"] is not None and st["from"] < st["line"]
            and not any(a <= st["line"] - 1e-9 < e for a, e in parts)]


def keep_parts(tempo, sts=()):
    """[(point index, first beat, end beat or None = on to the song's end)] of the Keep timing parts, without the
    catch-ups' own areas (line to end: a catch-up never moves notes) and the yellow areas of sts (stretches) they're
    off in (keep_off), so a part may come in pieces."""
    pts = tempo["points"]
    spans = [(c[0], end) for c, end in zip(tempo["catch"], catch_ends(tempo))] + keep_off(tempo, sts)
    out = []
    for i in keep_points(pts):
        p = pts[i]
        pieces = [(p[0], pts[i + 1][0] if i + 1 < len(pts) else None)]
        for lo, hi in spans:
            cut = []
            for a, e in pieces:
                if (e is not None and lo >= e) or (hi is not None and hi <= a):
                    cut.append((a, e))
                    continue
                if lo > a:
                    cut.append((a, lo))
                if hi is not None and (e is None or hi < e):
                    cut.append((hi, e))
            pieces = cut
        out += [(i, a, e) for a, e in pieces if e is None or e > a]
    return out


_layouts = {}
PLANS_KEPT = 50_000  # catch_plan answers remembered by everything they read (_plan_key)
_plans = {}


def _plan_key(st, pts, ppq, base, every, keeps_out, lines):
    """What a catch-up's catch_plan reads in layout (2026-10-29: one point moved in a Fill of 2,000 catch-ups worked
    every one out again): the stretch, the points whose parts its ticks can reach (_segments' own pick, a few
    more each side), the Keep timing pieces over it with their heard lines. None when it has no end (nothing to
    work out)."""
    if st["end"] is None:
        return None
    t0, t1 = beat_tick(st["from"], ppq), max(beat_tick(st["end"], ppq), beat_tick(st["line"], ppq))
    lo = max(0, bisect.bisect_right(pts, (t0 - 1) / ppq, key=lambda p: p[0]) - 3)
    hi = min(len(pts), bisect.bisect_left(pts, (t1 + 1) / ppq, key=lambda p: p[0]) + 4)
    over = [(ka, ke, u, lines[k]["points"]) for k, (_, ka, ke, u) in enumerate(keeps_out)
            if (ke is None or ke > t0) and ka < t1]
    text = json.dumps([st, pts[lo:hi], lo == 0, hi == len(pts), ppq, base, every, over], sort_keys=True,
                      default=repr)
    return hashlib.blake2b(text.encode(), digest_size=16).digest()


def layout(tempo, base, ppq):
    """The tempo line worked out in ticks (the last few remembered: the pane asks often): {"start_u": the project
    BPM's microseconds, "keeps": [(point index, first tick, end tick or None, heard us)], "worked": [(stretch,
    catch_plan)]}. Each stretch gets "base_u" / "base" (BPM) = the tempo heard just before it, each Keep timing
    part its heard us the same way: inside a stretch, its tempo; inside a Keep timing part, its; else the line."""
    key = (tempo_key(tempo), base, ppq)
    if key in _layouts:
        return _layouts[key]
    ce = col_entry(tempo["points"]) if len(tempo["points"]) >= BIG_LINE and not tempo["catch"] else None
    if ce is not None and ce[8]:  # (ds5: a big line with no catch-ups, no Keep timing: nothing to work out)
        orig, copy_of = tempo, []
        start_u = base_us(base)

        if isinstance(key[0], _VKey):  # (the watched line: no text to read it back from later)
            if ce[6]:  # (all held: its beats + BPMs say it all, as they were now)
                pb, vs, every = ce[3], ce[4], tempo.get("every", EVERY)
                copy_of.append(lambda: {"points": [[b, v, "hold", "", "lin", False, [0.5, 0.5]] for b, v in
                                                   zip(pb.tolist(), vs.tolist())], "catch": [], "every": every})
            else:
                c = held_copy(tempo)
                copy_of.append(lambda: c)

        def real(a, b):
            if not copy_of or callable(copy_of[0]):  # (its own copy as the line was, made only if asked)
                c = copy_of.pop()() if copy_of else key_copy(key[0], orig)
                copy_of.append((c, all(p[0] <= q[0] for p, q in zip(c["points"], c["points"][1:]))))
            c, ordered = copy_of[0]
            return _segments(c, base, start_u, ppq, a, b, ordered)

        def keep_segs(n, lo, hi):
            raise IndexError(n)
        real.__qualname__, keep_segs.__qualname__ = "layout.<locals>.real", "layout.<locals>.keep_segs"
        if len(_layouts) > 8:
            _layouts.clear()
        _layouts[key] = out = {"start_u": start_u, "keeps": [], "worked": [], "real": real,
                               "keep_segs": keep_segs, "own_line": []}
        return out
    # (its own copy: what's remembered never changes with the line; the held line copied straight: no text)
    tempo = key_copy(key[0], tempo)
    ordered = all(p[0] <= q[0] for p, q in zip(tempo["points"], tempo["points"][1:]))  # (once: _segments' many reads)
    start_u = base_us(base)
    sts = stretches(tempo, base)
    kps = keep_parts(tempo, sts)
    spans = []  # (a, b or None, heard us) in the order they're found
    keeps_at = []
    for i, b, end in kps:  # (ticks by the points' rule: a part starts on the first tick at or after its beat)
        a = beat_tick(b, ppq)
        e = None if end is None else beat_tick(end, ppq)
        if e is None or e > a:
            keeps_at.append((i, a, e))

    def in_keep(t):
        return any(a <= t and (e is None or t < e) for _, a, e in keeps_at)

    # a catch-up inside a Keep timing part paid back to the tempo heard in it (the drawn line there is only for
    # looks): worked out once the Keep timing pieces before its line are known
    inner = {n for n, st in enumerate(sts) if st["read"] is not None and in_keep(beat_tick(st["line"], ppq) - 1)
             and in_keep(beat_tick(st["read"], ppq))}
    todo = sorted([(beat_tick(st["line"] if n in inner else st["from"], ppq), 0, n) for n, st in enumerate(sts)]
                  + [(a, 1, n) for n, (_, a, _) in enumerate(keeps_at)])

    lines = [{"points": heard_line(tempo["points"][i]), "every": tempo.get("every", EVERY)} for i, _, _ in keeps_at]

    def keep_segs(n, lo, hi):
        """Keep timing piece n's heard tempo over ticks lo..hi: its heard line, before that line's first point
        (or without one) the tempo heard just before the piece."""
        u = keep_u[n]
        if not lines[n]["points"]:
            return [(lo, hi, u)] if hi > lo else []
        return _segments(lines[n], 60_000_000 / u, u, ppq, lo, hi)

    def heard(t):
        if t < 0:
            return start_u
        for a, b, u, n in reversed(spans):
            if a <= t and (b is None or t < b):
                if n is not None and lines[n]["points"]:
                    u = keep_segs(n, t, t + 1)[0][2]
                    return u if isinstance(u, int) else int(u[0])
                return u
        u = _segments(tempo, base, start_u, ppq, t, t + 1, ordered)[0][2]
        return u if isinstance(u, int) else int(u[0])

    # (2026-11-06: heard() walked every span found so far = all-against-all with 2,000 catch-ups. A span ending at
    # or before every tick still to be asked can't hold any of them: dropped as the loop goes, same answers)
    asks = [min(t0 - 1, beat_tick(sts[n]["read"], ppq)) if kind == 0 and sts[n]["read"] is not None
            and n not in inner else t0 - 1 for t0, kind, n in todo]
    for k in range(len(asks) - 2, -1, -1):
        asks[k] = min(asks[k], asks[k + 1])
    bases, keep_u = [start_u] * len(sts), [start_u] * len(keeps_at)
    for k, (t0, kind, n) in enumerate(todo):
        if len(spans) > 8:
            spans[:] = [s for s in spans if s[1] is None or s[1] > asks[k]]
        u = heard(t0 - 1)
        if kind == 0 and sts[n]["read"] is not None and n not in inner:  # (the tempo the line comes back to)
            u = heard(beat_tick(sts[n]["read"], ppq))
        if kind == 0:
            bases[n] = u
            if sts[n]["end"] is not None:
                spans.append((beat_tick(sts[n]["from"], ppq), beat_tick(sts[n]["end"], ppq), u, None))
        else:
            keep_u[n] = u
            spans.append((t0, keeps_at[n][2], u, n))
    keeps_out = [(i, a, e, keep_u[n]) for n, (i, a, e) in enumerate(keeps_at)]

    def real(a, b):
        segs = _segments(tempo, base, start_u, ppq, a, b, ordered)
        puts = []
        for n, (_, ka, ke, u) in enumerate(keeps_out):
            lo, hi = max(a, ka), min(b, b if ke is None else ke)
            if hi > lo:
                puts.append((lo, hi, keep_segs(n, lo, hi)))
        return _put_many(segs, puts)

    worked = []
    pts, every = tempo["points"], tempo.get("every", EVERY)  # (own Intervals: in the points / st themselves)
    for n, st in enumerate(sts):
        st = dict(st, base_u=bases[n], base=60_000_000 / bases[n])
        pk = _plan_key(st, pts, ppq, base, every, keeps_out, lines) if ordered else None
        plan = _plans.get(pk) if pk else None
        if plan is None:
            plan = catch_plan(tempo, base, st, ppq, real)
            if pk:
                if len(_plans) > PLANS_KEPT:
                    _plans.clear()
                _plans[pk] = plan
        worked.append((st, plan))
    if len(_layouts) > 8:
        _layouts.clear()
    _layouts[key] = out = {"start_u": start_u, "keeps": keeps_out, "worked": worked, "real": real,
                           "keep_segs": keep_segs, "own_line": [bool(ln["points"]) for ln in lines]}
    return out


def keep_heard(lay, n, lo, hi):
    """layout()'s Keep timing piece n's heard tempo over ticks lo..hi: one whole number (no heard line, or it holds
    there) or an int64 array, one per tick."""
    segs = lay["keep_segs"](n, lo, hi)
    if len(segs) == 1 and isinstance(segs[0][2], int):
        return segs[0][2]
    out = np.empty(hi - lo, np.int64)
    for a, b, u in segs:
        out[a - lo:b - lo] = u
    return out


def keep_runs(tempo):
    """[(first, last point index)] of the Keep timing runs: parts in a row with Keep timing and one heard tempo line
    (a part cut in two keeps reading the same line), each edited as one in the Conductor pane."""
    pts, out = tempo["points"], []
    for i in keep_points(pts):
        p = pts[i]
        if out and out[-1][1] == i - 1 and json.dumps(heard_of(pts[i - 1])) == json.dumps(heard_of(p)):
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def heard_before(tempo, base, ppq, i, j=None):
    """The BPM heard just before the Keep timing run of points i..j (j: i alone): what its heard line plays before
    its first point."""
    for k, _, _, u in layout(tempo, base, ppq)["keeps"]:
        if i <= k <= (i if j is None else j):
            return 60_000_000 / u
    return float(base)


def keep_end_bpm(tempo, base, ppq, i):
    """The BPM point i's Keep timing part's heard line arrives at on its end (its own value, not the interval's last
    step; None: it runs to the song's end, or a catch-up ends it)."""
    pts = tempo["points"]
    if i + 1 >= len(pts):
        return None
    lay = layout(tempo, base, ppq)
    et = beat_tick(pts[i + 1][0], ppq)
    for k, ta, te, u in lay["keeps"]:
        if k == i and te == et and te > ta:
            hl = copy.deepcopy(heard_line(pts[i]))  # (before its first point: the tempo heard before the piece)
            split_at(hl, pts[i + 1][0], 60_000_000 / u)
            return next(q[1] for q in hl if abs(q[0] - pts[i + 1][0]) < 1e-9)
    return None


def end_point(tempo, base, ppq, i, force=False):
    """Keep timing turned on (or swapped) for point i's part: the tempo after it starts at the BPM heard at its end,
    so the sound goes on smoothly (user 2026-10-24: they put that point by hand; it can be deleted). Nothing when
    the part runs to the song's end, the part after keeps timing too, the line is there already or the drawn line
    jumps there already (a jump the user made stays, user 2026-10-24: their point after it moved; force: anyway).
    True when it changed something."""
    pts = tempo["points"]
    changed()
    v = keep_end_bpm(tempo, base, ppq, i)
    if v is None:
        return False
    v = clamp_bpm(v)
    on = [k for k, q in enumerate(pts) if abs(q[0] - pts[i + 1][0]) < 1e-9]
    q, p = pts[on[-1]], pts[i]
    if keeps(q) or abs(q[1] - v) <= 1e-9 * v:
        return False
    arrive = pts[on[0]][1] if len(on) == 2 or p[2] != "hold" else p[1]
    if not force and abs(arrive - q[1]) > 1e-9 * q[1]:
        return False
    run = [i]  # (the run's parts remember it: later heard edits move it along while it's still this, hunt)
    while run[0] > 0 and keeps(pts[run[0] - 1]) and json.dumps(heard_of(pts[run[0] - 1])) == json.dumps(heard_of(p)):
        run.insert(0, run[0] - 1)
    for k in run:
        m = pts[k]
        if not heard_of(m):
            set_heard(m, {"line": []})
        m[7]["end"] = v
    if len(on) == 1 and p[2] != "hold" and p[1] != q[1]:  # (a glide arrives: it stays, the tempo jumps from there)
        pts.insert(on[0] + 1, [q[0], v] + copy.deepcopy(q[2:]))
    else:
        if len(on) == 1 and p[2] != "hold":  # (a "curve" between two equal BPMs: flat, kept flat)
            p[2] = "hold"
        q[1] = v
    changed()
    return True


def swap_part(tempo, base, ppq, i, j=None):
    """Keep the audio <-> Keep the visual on the Keep timing run of points i..j (j: i alone; user 2026-10-24): its
    drawn line and its heard line swap places (a flat heard line = the tempo heard just before it), the "visual"
    mark flips, then the tempo after it starts where the new heard line ends (end_point). Returns the run's last
    point's index."""
    pts = tempo["points"]
    j = i if j is None else j
    p = pts[i]
    a = p[0]
    e = pts[j + 1][0] if j + 1 < len(pts) else None
    kb = clamp_bpm(heard_before(tempo, base, ppq, i, j))
    was = keep_end_bpm(tempo, base, ppq, j)  # (the sound smooth after it before: smooth after the swap too)
    smooth = was is not None and abs(pts[[k for k, q in enumerate(pts) if abs(q[0] - e) < 1e-9][-1]][1] - was) \
        <= 1e-9 * was
    h = heard_of(p) or {"line": []}
    hl = copy.deepcopy(h["line"])  # (the heard line over the part, cut at its ends: the same line)
    split_at(hl, a, kb)
    if e is not None:
        split_at(hl, e, kb)
    first = [q for q in hl if abs(q[0] - a) < 1e-9][-1]
    inner = [q for q in hl if a + 1e-9 < q[0] and (e is None or q[0] < e - 1e-9)]
    arrive = next((q[1] for q in hl if e is not None and abs(q[0] - e) < 1e-9), None)
    # the drawn line over the run becomes the heard line (each part's own Interval with it)
    line = []
    for q in pts[i:j + 1]:
        line.append([q[0], q[1], q[2], q[3], q[4], False, list(bend_of(q))])
        copy_step(line[-1], q)
    q = pts[j]
    if e is not None and q[2] != "hold" and q[1] != pts[j + 1][1]:
        line.append([e, pts[j + 1][1], "hold", "", "lin", False, list(BEND)])
    drop_loose(line)
    if len(line) == 1 and p[2] == "hold" and abs(p[1] - kb) <= 1e-6 * kb:
        line = []  # (flat at the tempo heard before: follows it, as without a line)
    new = {"line": line}
    if not h.get("visual"):
        new["visual"] = True
    # the heard line over the part becomes the drawn one
    p[1], p[2:5] = first[1], copy.deepcopy(first[2:5])
    set_bend(p, list(bend_of(first)))
    p[5] = True
    set_heard(p, new if new["line"] or "visual" in new else None)
    set_step(p, None)  # (the heard line's first part's own Interval comes with it)
    copy_step(p, first)
    mids = []
    for q in inner:
        m = [q[0], q[1], q[2], q[3], q[4], True, list(bend_of(q))]
        set_heard(m, copy.deepcopy(new) if heard_of(p) else None)
        copy_step(m, q)
        mids.append(m)
    pts[i + 1:j + 1] = mids
    last = i + len(mids)
    if e is not None:
        on = [k for k, q in enumerate(pts) if abs(q[0] - e) < 1e-9]
        lp = pts[last]
        if lp[2] != "hold" and arrive is not None:  # (it glides into e: arrives at the heard line's BPM there)
            if len(on) == 2:
                pts[on[0]][1] = arrive
            elif abs(pts[on[0]][1] - arrive) > 1e-9:
                pts.insert(on[0], [e, arrive, "hold", "", "lin", False, list(BEND)])
    lp = pts[last]
    drop_loose(pts)  # (an arrival on a beat nothing glides into any more: gone, so the index found again)
    last = next(k for k, q in enumerate(pts) if q is lp)
    end_point(tempo, base, ppq, last, force=smooth)
    return last


def plans(tempo, base, ppq):
    """[(stretch, catch_plan)] for every catch-up line."""
    return layout(tempo, base, ppq)["worked"]


def step_edges(tempo, worked, ppq):
    """[(first tick, end tick, first beat, end beat, ends at a jump)] of every curved stretch: curved parts (their
    points' beats) and curved catch-ups (None, None, False), which change every "every" ticks from their first tick
    (as the file has them). Remembered for the line (a loaded MIDI's thousands of points: every redraw)."""
    pts = tempo["points"]
    key = (tempo_key(tempo), ppq)
    got = _edges.get(key)
    every = tempo.get("every", EVERY)
    if got is None:
        ce = col_entry(pts) if len(pts) >= BIG_LINE else None
        if ce is not None:  # (a big line: only its curved parts looked at; none = no ticks worked out)
            look = np.flatnonzero(ce[9]).tolist()
            first = _first_ticks(ce[3].tolist(), ppq) if look else []
        else:
            first = _first_ticks([p[0] for p in pts], ppq)
            look = range(len(pts) - 1)
        got = []
        for i in look:
            if pts[i][2] != "hold" and pts[i][1] != pts[i + 1][1] and first[i + 1] > first[i]:
                e, off = step_of(pts[i], every)  # (its own Interval, point[8])
                got.append((first[i], first[i + 1], pts[i][0], pts[i + 1][0], to_jump(pts, i), e, first[i] + off))
        if len(_edges) > 4:
            _edges.clear()
        _edges[key] = got
    return got + [(beat_tick(st["line"], ppq), beat_tick(st["end"], ppq), None, None, False,
                   st.get("every") or every, None) for st, plan in worked if plan["ok"] and st["shape"] != "flat"]


_edges = {}


def step_shares(a, b, starts, every):
    """A curved part ending at a jump (ticks a..b; its arrival point is never played): how far along it each step
    starting at tick `starts` reads the line, spread so the last step is the end BPM exactly (user 2026-10-22).
    Other parts read the line at each step's own start (the next point plays the end BPM): two lines in a row stay
    smooth (user: a doubled step at every joint when every part read both its ends)."""
    n = -(-(b - a) // every)
    j = (np.asarray(starts) - a) // every
    return j / (n - 1) if n > 1 else 0.0 * j


def to_jump(pts, i):
    """Point i's part ends where a jump's two points share a beat."""
    return i + 2 < len(pts) and pts[i + 2][0] == pts[i + 1][0]


def on_beat(pts, b, beats=None):
    """The indexes of the points within 1e-9 of beat b, in order. beats: the points' beats, sorted (kept in step
    by the caller): only the few around b looked at (many pastes into thousands of points, speed hunt 2026-10-29)."""
    if beats is None:
        return [i for i, p in enumerate(pts) if abs(p[0] - b) < 1e-9]
    lo, hi = bisect.bisect_left(beats, b - 2e-9), bisect.bisect_right(beats, b + 2e-9)
    return [i for i in range(lo, hi) if abs(pts[i][0] - b) < 1e-9]


def split_at(pts, b, base, beats=None):
    """A point put on beat b (in place) so the tempo line looks exactly the same (user 2026-10-22: a paste / cut
    must leave the tempo around it as it was): the part there cut in two (hold / line / S-curve exact, an S-curve
    becoming its two bent halves; a formula part's formula written again for each piece), before the first point
    the project's BPM held, after the last one its BPM. Nothing when a point is on b already. Returns the point on
    b (the last one there). (Curved parts change every "every" ticks from their own start: each piece starts
    its steps again.) beats: the points' beats in order, kept in step here (on_beat)."""
    on = on_beat(pts, b, beats)
    if on:
        return pts[on[-1]]

    def put(k, new):
        pts.insert(k, new)
        if beats is not None:
            beats.insert(k, new[0])

    i = bisect.bisect_left(beats if beats is not None else [p[0] for p in pts], b) - 1
    if i < 0:  # (before the first point: the project's BPM)
        new = [float(b), clamp_bpm(base), "hold", "", "lin", False, list(BEND)]
        put(0, new)
        return new
    p = pts[i]
    if i + 1 >= len(pts):  # (after the last point: it holds)
        new = [float(b), p[1]] + copy.deepcopy(p[2:])
        copy_step(new, p, offset=False)
        put(i + 1, new)
        return new
    if p[2] == "s":  # (its two halves: a bent line, then the same bend mirrored)
        cx, cy = bend_of(p)
        q = pts[i + 1]
        mid = [(p[0] + q[0]) / 2, float(_along(p, q, 0.5)), "line", "", p[4], keeps(p), [1 - cx, 1 - cy]]
        if heard_of(p):
            mid.append(copy.deepcopy(p[7]))
        copy_step(mid, p, offset=False)  # (each piece keeps the ramp's own Interval, its steps from its start)
        p[2] = "line"
        set_bend(p, [cx, cy])
        put(i + 1, mid)
        if abs(mid[0] - b) < 1e-9:
            return mid
        if mid[0] < b:
            i, p = i + 1, mid
    q = pts[i + 1]
    f = (b - p[0]) / (q[0] - p[0])
    new = [float(b), p[1]] + copy.deepcopy(p[2:])
    copy_step(new, p, offset=False)
    if p[2] == "line":
        cx, cy = bend_of(p)
        a = 1 - 2 * cx
        s = f if abs(a) < 1e-9 else (-2 * cx + math.sqrt(max(4 * cx * cx + 4 * a * f, 0))) / (2 * a)
        s = min(1.0, max(0.0, s))
        y = 2 * s * (1 - s) * cy + s * s  # (the curve's own place there: B(s) = (f, y))
        new[1] = float(_along(p, q, y))
        lx, ly = s * cx, s * cy  # (de Casteljau: the left piece's control point, then the right one's)
        rx, ry = (1 - s) * cx + s, (1 - s) * cy + s
        set_bend(p, [_unit(lx / f if f > 0 else 0.5), _unit(ly / y if y > 0 else 0.5)])
        new[6] = [_unit((rx - f) / (1 - f) if f < 1 else 0.5), _unit((ry - y) / (1 - y) if y < 1 else 0.5)]
    elif p[2] == "formula":
        ff = float(_apply(formula_fn(p[3]), np.array([f]))[0])
        new[1] = float(_along(p, q, ff))
        x = re.compile(r"\bx\b")
        text = "(" + (p[3] or FORMULA) + ")"
        # (each piece: the same curve read again, scaled to its own start and end BPM: the part's BPM is
        # start + (end - start) * F, so the left piece is F(f x) / F(f), the right (F(f + (1 - f) x) - F(f)) /
        # (1 - F(f)); a piece that can't be scaled (F(f) = 0 / 1) keeps the formula, close)
        if abs(ff) > 1e-12:
            p[3] = f"({x.sub(f'({f!r}*x)', text)}) / ({ff!r})"
        if abs(1 - ff) > 1e-12:
            new[3] = f"(({x.sub(f'({f!r}+{1 - f!r}*x)', text)}) - ({ff!r})) / ({1 - ff!r})"
    put(i + 1, new)
    return new


def _unit(v):
    return min(1.0, max(0.0, float(v)))


def _along(p, q, c):
    """The BPM c of the way from p's to q's on p's part's BPM change (linear / doubling)."""
    if p[4] == "exp":
        return clamp_bpm(2 ** (math.log2(p[1]) + (math.log2(q[1]) - math.log2(p[1])) * c))
    return clamp_bpm(p[1] + (q[1] - p[1]) * c)


def part_bpm(tempo, i, f):
    """Point i's part's BPM f of the way (0..1) to the next point: its own curve, so the end is the next point's
    BPM even where a jump's second point shares that beat."""
    p, q = tempo["points"][i], tempo["points"][i + 1]
    c = curve(p[2], p[3], np.asarray(f, float), bend_of(p))
    if p[4] == "exp":
        out = 2 ** (math.log2(p[1]) + (math.log2(q[1]) - math.log2(p[1])) * c)
    else:
        out = p[1] + (q[1] - p[1]) * c
    return np.clip(out, MIN_BPM, MAX_BPM)


def stepped(tempo, beats, ppq, edges):
    """beats moved to where their interval step reads the line inside the curved stretches (step_edges): the
    value the file has there."""
    beats = np.asarray(beats, float)
    out = beats.copy()
    tick = np.floor(beats * ppq + 1e-9)
    for a, b, b0, b1, jump, every, origin in edges:
        on = (tick >= a) & (tick < b)
        o = a if origin is None else origin
        starts = o + (tick[on] - o) // every * every
        if jump:  # (spread to its end; the last step a hair before its end beat: the jump's second point is ON it)
            out[on] = np.minimum(b0 + (b1 - b0) * step_shares(a, b, starts, every), b1 - 1e-9 * max(1.0, b1))
        elif every > 1:  # (each step reads the line at its own start; a catch-up's too)
            out[on] = starts / ppq
    return out


def shown_bpm(tempo, base, beats, worked, ppq=None):
    """What the pane draws: the drawn line with each working catch-up (worked = plans()) in its place; with ppq,
    curved stretches in the interval's steps (user: as jagged as the file)."""
    beats = np.asarray(beats, float)
    at = stepped(tempo, beats, ppq, step_edges(tempo, worked, ppq)) if ppq else beats
    out = drawn_bpm(tempo, base, at)
    for st, plan in worked:
        if plan["ok"]:
            on = (beats >= st["line"]) & (beats < st["end"])
            out[on] = catch_bpm(base, st, plan, at[on] + (0.5 / ppq if ppq else 0))
    return out


def tempo_events(tempo, base, ppq):
    """The tempo changes a MIDI file gets with no notes to keep in time: ([(tick, microseconds per beat)], [beats of
    catch-up lines that can't work]) (notes/tempo_file.py's file_plan, which the export uses)."""
    from notes.tempo_file import file_plan
    plan = file_plan(tempo, base, ppq)
    return plan["events"], plan["bad"]


def segs_end(tempo, ppq, end):
    """The tick file_segments' pieces run to: end, or past the last point / catch-up end when that is later (asked
    with any end up to there = the same pieces: the caches know them by this one)."""
    pts = tempo["points"]
    last = max([p[0] for p in pts] + [x for c in tempo["catch"] for x in (c[0], catch_get(c, 4)) if x is not None])
    return max(end, beat_tick(last, ppq) + 1)


def file_segments(tempo, base, ppq, end=0):
    """The file's tempo before Keep timing moves anything, as [(a, b, us)] from tick 0 to at least end, and the
    beats of catch-up lines that can't work. Each stretch with a working catch-up adds up exactly to the time of
    the tempo heard before it; one that can't work leaves its stretch as drawn (heard) and is listed."""
    base_u = base_us(base)
    end = segs_end(tempo, ppq, end)
    segs = _segments(tempo, base, base_u, ppq, 0, end)
    bad, puts = [], []
    for st, plan in plans(tempo, base, ppq):
        if not plan["ok"]:
            bad.append(st["line"])
            continue
        # (the drawn pieces cut where the catch-up goes; its values worked out with the pane's check; _put's
        # answer, all in one pass)
        ra, rb = beat_tick(st["line"], ppq), beat_tick(st["end"], ppq)
        puts.append((ra, rb, [(ra, rb, plan["fit"])] if rb > ra else []))
    return _put_many(segs, puts), bad


def bpm_text(v):
    v = float(v)
    return f"{v:,.0f}" if v >= 10000 else fmt(round(v, 3))


def short_bpm(v):
    """A BPM to one decimal (user: the ruler stays calm): 133.333 -> 133.3, 120.0 -> 120."""
    v = float(v)
    return f"{v:,.0f}" if v >= 10000 else fmt(round(v, 1))


STAIR_STEP = 0.1  # (a held step at most 10 % of the BPM before it = part of stairs, user 2026-10-22)
STAIR_MIN = 3  # (steps in a row that make stairs)


def ruler_marks(tempo, base, ppq, bar=4):
    """The tempo changes the piano roll's ruler marks (user 2026-10-22): [(beat, "point", (before, at, then), until)]
    for each point (before = the BPM just before it, None at beat 0; then = where its part goes, None when it holds)
    and [(beat, "catch", BPM or None when it can't work, until)] for each catch-up line. until = the beat the ruler's
    faint line runs to (a glide's / catch-up's end, math.inf = on to the edge), None for a jump (the tempo just
    stays). STAIRS (user 2026-10-22: a pasted ramp read as jumps) = STAIR_MIN+ held steps in a row going one way,
    each at most STAIR_STEP of the BPM and within a bar of the one before: ONE mark (before, first BPM, last BPM),
    a line to the last step, [(beat, before, at)] of every step as its 5th item (the tip)."""
    if not has_tempo(tempo):
        return []
    key = (tempo_key(tempo), base, ppq, bar)
    if key in _marks:
        return _marks[key]
    worked = plans(tempo, base, ppq)
    pts = tempo["points"]
    lines = sorted(st["line"] for st, _ in worked)  # (sorted: the line after a beat by bisect, not a look at all)
    out = []
    pb = np.array([p[0] for p in pts], float)
    befores = shown_bpm(tempo, base, pb - 1e-9 * np.maximum(1.0, pb), worked).tolist()  # (all in one go)
    # (2026-11-14: every point's mark + the stairs found with arrays, not one by one: 190,000 short BPM changes
    # took ~0.5 s a change. The same marks as before: dev/scratch/i_marks.py checks them against the old way)
    n_p = len(pts)
    vals = np.array([p[1] for p in pts], float)
    hold = np.fromiter((p[2] == "hold" for p in pts), bool, n_p)
    shown = np.ones(n_p, bool)
    shown[:-1] = pb[1:] != pb[:-1]  # (where a curve arrives: the next one on its beat says it)
    glides = np.zeros(n_p, bool)
    glides[:-1] = ~hold[:-1] & (vals[1:] != vals[:-1])
    idx = np.flatnonzero(shown)
    m_before = [v if p else None for v, p in zip(np.asarray(befores, float)[idx].tolist(), (pb[idx] > 0).tolist())]
    m_at = vals[idx].tolist()
    m_then, m_until = [None] * len(idx), [None] * len(idx)
    for k in np.flatnonzero(glides[idx]).tolist():  # (a part that moves: where it goes, cut short by a catch-up)
        i = int(idx[k])
        b = pts[i][0]
        n = bisect.bisect_right(lines, b)  # (a catch-up line cuts the part short: the first one after b)
        cut = lines[n] if n < len(lines) and lines[n] < pts[i + 1][0] else None
        m_then[k] = float(drawn_bpm(tempo, base, [cut])[0]) if cut is not None else float(pts[i + 1][1])
        m_until[k] = cut if cut is not None else pts[i + 1][0]
    beats = [pts[i][0] for i in idx.tolist()]
    singles = list(zip(beats, itertools.repeat("point"), zip(m_before, m_at, m_then), m_until))
    k = 0
    for s, e in _stair_runs(idx, m_before, m_at, m_then, hold, pb, lines, bar):
        out += singles[k:s]
        steps = [(beats[x], m_before[x], m_at[x]) for x in range(s, e)]
        out.append((beats[s], "point", (m_before[s], m_at[s], m_at[e - 1]), beats[e - 1], steps))
        k = e
    out += singles[k:]
    out += [(st["line"], "catch", plan["bpm"] if plan["ok"] else None,
             st["end"] if st["end"] is not None else math.inf) for st, plan in worked]
    if len(_marks) > 4:
        _marks.clear()
    _marks[key] = out
    return out


_cols_marks = {}


def ruler_cols(tempo, base, ppq, bar=4):
    """(ds5) ruler_marks of a big PLAIN line (every point an ordinary held point, no catch-ups, no stairs) as arrays
    {"beat", "before" (NaN = None: at beat 0), "at"} of the marks, one per point where the next isn't on its beat; the
    marks themselves = (beat, "point", (before, at, None), None). None when the line is not like that (ruler_marks
    then). No per-mark tuples: the ruler's layout hops over them (roll_draw.shown_from_cols)."""
    pts = tempo["points"]
    ce = col_entry(pts) if tempo.get("catch") == [] else None
    if ce is None or not ce[6]:
        return None
    key = (tempo_key(tempo), base, ppq, bar)
    if key in _cols_marks:
        return _cols_marks[key]
    pb, vals = ce[3], ce[4]
    n = len(pb)
    befores = drawn_bpm(tempo, base, pb - 1e-9 * np.maximum(1.0, pb), (pb, vals, None))
    shown = np.ones(n, bool)
    shown[:-1] = pb[1:] != pb[:-1]
    idx = np.flatnonzero(shown)
    bef, at = befores[idx], vals[idx]
    first = pb[idx] > 0
    out = None
    with np.errstate(invalid="ignore"):
        small = first & ~(np.abs(at - bef) > STAIR_STEP * bef)
    if int(np.count_nonzero(small)) < STAIR_MIN:  # (stairs need STAIR_MIN small steps: none possible)
        stairs = []
    else:
        stairs = _stair_core(idx, first, np.where(first, bef, np.nan), at, np.ones(len(idx), bool), np.ones(n, bool),
                             pb, [], bar)
    if not stairs:
        out = {"beat": pb[idx], "before": np.where(first, bef, np.nan), "at": at}
    if len(_cols_marks) > 4:
        _cols_marks.clear()
    _cols_marks[key] = out
    return out


def cols_as_marks(c):
    """(ds5) ruler_cols as ruler_marks' list (for checks)."""
    b, a = c["before"].tolist(), c["at"].tolist()
    return [(x, "point", (None if y != y else y, z, None), None) for x, y, z in zip(c["beat"].tolist(), b, a)]


def heard_marks(tempo, base, ppq, bar=4):
    """ruler_marks for each Keep timing run's own heard tempo line (user 2026-10-24: the ruler marks its points too,
    in purple, same rules): only the points inside the run, before its first point = the tempo heard before the run;
    a glide's line stops at the run's end. No catch-ups (a heard line has none)."""
    out, pts = [], tempo["points"]
    for i, j in keep_runs(tempo):
        line = heard_line(pts[i])
        if not line:
            continue
        a, e = pts[i][0], pts[j + 1][0] if j + 1 < len(pts) else math.inf
        inside = [q for q in line if a - 1e-9 <= q[0] <= e + 1e-9]
        if not inside:
            continue
        before = heard_before(tempo, base, ppq, i, j)
        if abs(before - inside[0][1]) <= 1e-4 * before:  # (the file's microseconds: 180.0002 marked "180 → 180")
            before = inside[0][1]
        got = ruler_marks({"points": inside, "catch": [], "every": tempo.get("every", EVERY)}, before, ppq, bar)
        for b, kind, v, until, *steps in got:
            if kind == "point" and b < e - 1e-9:  # (a point on the run's end: the sound goes on from the line after)
                out.append((b, "heard", v, None if until is None else min(until, e), *steps))
    return out


def kept_bpms(tempo, base, ppq, beats):
    """The BPM heard at each beat inside a Keep timing part, None outside one (or where a catch-up's yellow area
    switches it off): the ruler's purple brackets after a visual mark (user 2026-10-29, "120 → 60 (120)")."""
    lay, out = layout(tempo, base, ppq), []
    keeps = lay["keeps"]
    if not keeps:  # (no Keep timing part: none anywhere; the ruler asks for every point's beat)
        return [None] * len(beats)
    starts = [a for _, a, _, _ in keeps]  # (in the points' order = by tick)
    for t in _first_ticks(beats, ppq):  # (beat_tick of each, all at once)
        n = bisect.bisect_right(starts, t) - 1
        if n < 0 or (keeps[n][2] is not None and t >= keeps[n][2]):
            out.append(None)
            continue
        u = keep_heard(lay, n, t, t + 1)
        out.append(60_000_000 / (u if isinstance(u, int) else int(u[0])))
    return out


def _stair_runs(idx, before, at, then, hold, pb, lines, bar):
    """[(k, e)]: the marks k..e-1 (places in idx, the points marked) that make STAIRS, looked for from the first
    mark on (a run taken = the next look starts after it), all at once with arrays (2026-11-14; was one look per
    mark). A run from k: held steps (a held point after a held part, not past STAIR_STEP of the BPM), one way (the
    first change sets it), each within a bar of the one before with no catch-up line between (lines sorted);
    steps with no change at its end left off; STAIR_MIN+ changes = stairs."""
    if len(idx) < STAIR_MIN:
        return []
    has = np.array([b is not None for b in before], bool)
    bef = np.array([b if b is not None else np.nan for b in before], float)
    no_then = np.array([t is None for t in then], bool)
    return _stair_core(idx, has, bef, np.asarray(at, float), no_then, hold, pb, lines, bar)


def _stair_core(idx, has, bef, at, no_then, hold, pb, lines, bar):
    """_stair_runs with its marks as arrays (has = a BPM before, bef = it, no_then = no purple heard BPM)."""
    n = len(idx)
    if n < STAIR_MIN:
        return []
    prev_hold = (idx > 0) & hold[np.maximum(idx - 1, 0)]
    with np.errstate(invalid="ignore"):
        ent = has & hold[idx] & no_then & prev_hold & ~(np.abs(at - bef) > STAIR_STEP * bef)
        d = np.where(ent, (at > bef).astype(np.int8) - (at < bef).astype(np.int8), 0)
    beats = pb[idx]
    lines_a = np.asarray(lines, float)
    link = np.zeros(n, bool)  # (mark x can follow mark x - 1: within a bar, no catch-up line between)
    link[1:] = (beats[1:] - beats[:-1] <= bar) & (np.searchsorted(lines_a, beats[1:], "right")
                                                    == np.searchsorted(lines_a, beats[:-1], "right"))
    brk = np.flatnonzero(~(ent & link))  # (a run from k stops at the first of these after k)
    nz, pos, neg = np.flatnonzero(d != 0), np.flatnonzero(d > 0), np.flatnonzero(d < 0)
    cnz = np.concatenate([[0], np.cumsum(d != 0)])

    def after(arr, x):  # (first of arr at / after x, else n)
        j = np.searchsorted(arr, x)
        return np.where(j < len(arr), arr[np.minimum(j, len(arr) - 1)] if len(arr) else n, n)
    ks = np.flatnonzero(ent)
    if not len(ks):
        return []
    blk = after(brk, ks + 1)
    f = after(nz, ks)
    df = d[np.minimum(f, n - 1)]
    g = np.where(df > 0, after(neg, f + 1), after(pos, f + 1))
    end = np.where(f < blk, np.minimum(blk, g), blk)
    ok = (f < end) & (cnz[end] - cnz[ks] >= STAIR_MIN)
    if not ok.any():
        return []
    tend = nz[np.searchsorted(nz, end) - 1] + 1  # (no change at the end: not part of the climb)
    acc, acc_end = ks[ok].tolist(), tend[ok].tolist()
    out, j = [], 0
    while j < len(acc):
        out.append((acc[j], acc_end[j]))
        j = bisect.bisect_left(acc, acc_end[j], j + 1)
    return out


_marks = {}


def bpm_at(tempo, base, ppq, tick):
    """The exact BPM the file holds at a tick (Spiderweb's ticks, before Keep timing moves anything: what the pane
    draws there); the project's BPM without a tempo line."""
    if not has_tempo(tempo):
        return float(base)
    key = (tempo_key(tempo), base, ppq, tick)
    if key in _at:  # (the play line resting / redrawn: once)
        return _at[key]
    u = None
    for st, plan in plans(tempo, base, ppq):  # (only that tick worked out, not the whole song: hunt 2026-10-22,
        ra, rb = beat_tick(st["line"], ppq), beat_tick(st["end"], ppq) if st["end"] is not None else None
        if plan["ok"] and ra <= tick < rb:  # thousands of points took seconds per edit)
            u = plan["fit"] if isinstance(plan["fit"], int) else int(plan["fit"][tick - ra])
            break
    if u is None:
        a, _, got = _segments(tempo, base, base_us(base), ppq, tick, tick + 1)[0]
        u = got if isinstance(got, int) else int(got[tick - a])
    _at.clear()
    _at[key] = out = 60_000_000 / u
    return out


_at = {}


def file_seconds(events, ppq, tick):
    """Seconds from the start to tick with these tempo changes (exact sums, for tests)."""
    total, last_t, last_u = 0, 0, events[0][1]
    for t, u in events:
        if t >= tick:
            break
        total += (t - last_t) * last_u
        last_t, last_u = t, u
    total += (tick - last_t) * last_u
    return total / ppq / 1e6
