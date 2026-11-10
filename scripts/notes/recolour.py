"""Recolour (side panel under Colours, window/panel_recolour.py; asked by a user for their note art, 2026-10-25):
a shape's notes keep their keys, starts and gates, only which colour each one has changes.

Shuffle: the colours the shape already has go round its notes. Amount (0-100 %) = how many notes get a new one,
each picked at random among its set's colours (by their Shares). STEADY: each note's dice come from the note
itself (start, end, key counted from the shape's first tick and lowest key, its colour before) and the shape's seed
(New shuffle = another seed), so drawing again or moving the shape gives the same colours and an edit only changes
the notes it touches (a copy looks the same until its own New shuffle). Each note keeps its Colours round ("Repeated
colours get new channels"). The notes are first settled as they sound with Recolour off (settled: a note cut by
a later one of its colour stays cut), then two notes on one key sounding together (different colours, else they'd
have been settled) never end up on one colour (they'd cut or merge each other); touching end to start is fine.
Only in Multi channel (App.notes_tracks leaves it out otherwise: in one channel it could only change overlaps).

The shape's colours keep the channels they had before (App all_notes: each colour counted as starting where it
started before, render's back), so Amount 0 looks like Off.

Everything is worked out on the colours WITHOUT their Colours round, each note's round put back after: "Repeated
colours get new channels" moves channels, never colours (user 2026-10-25: ticking it shifted the colours).
COLOUR SETS (colour_sets): a shape with more than SET colours (pasted notes' tracks) is cut into sets of SET in
track order (never by Use channel 10 / the rounds tick: both change no colour). Each note is shuffled within its own
set unless "Mix across sets" (rc "mix"). Shares (rc "shares", % by place in the set) = how likely each colour is
for a note that changes; "colours" = how many each set uses, its own ones first, then new ones (new channels; at
most rc "most": 15, 16 with Use channel 10).
SPLIT (works from one colour; Colours not set = 2): Split: Scattered ("scatter") = Amount of the notes torn off their
colour onto another one (never their own, by Shares). Split: Clumps ("clumps") = the shape's box cut into colour
areas (areas: bands / swirl / wedges, Shares = each area's size), Amount = how rough their edges are.
Per chord / Per run (rc "by"): the notes of one chord (one start) / one run (touching end to start on a key) share
one throw of the dice, the first note's, so they change together and take one colour.
"""

import heapq
import json
import math
import random
import weakref
from collections import OrderedDict

import numpy as np

from files.speed import key_sort, loops
from notes.custom import ROUND

MODES = ("shuffle", "scatter", "clumps")
KEEP = "keep"  # (Off with a Stir: the colours as they are, only stirred; the dropdown shows Off)
KEEP_KEYS = ("mode", "seed", "stir", "frame", "whole", "most", "ppq", "turned", "liquify", "warp")  # (what mode KEEP
# looks at)
# ("liquify" = the Liquify brush's bend map, notes/liquify.py: applied after every stir and the Warp; Off with only a
# Liquify = KEEP too)
# ("warp" = the Warp grid, notes/warp.py: after every stir, before the Liquify (user 11-09: fixed order); Off with
# only a Warp = KEEP too)
# ("turned" = Reverse track order on a stirred shape, sweep 11-06: [[colour before, colour now], ...] of the colours
# it swapped (only those moved). The Stir works out the colours from the ones before and the result is swapped the
# same way, so the reverse is EXACTLY the stirred pattern with its tracks turned round: the stir's dice and "two
# notes on a key kept apart" read the colours, so stirring the swapped colours again gave another pattern, even an
# extra track. Its own notes keep the swapped colours too: with the Stir taken off they stay reversed.)
SPLIT = ("scatter", "clumps")  # (Split modes: work from one colour, 2 colours when Colours isn't set)
BY = ("note", "chord", "run")
PATTERNS = ("bands", "swirl", "wedges")  # (Split: Clumps' areas)
TIGHT, MOST_TIGHT = 1.0, 50.0  # Swirl tightness: turns from the centre point to the edge
MOST_PUSH = 8  # Push steps each way (each one = the areas bunched a step more toward that side)
MOST_SEED = 2 ** 31 - 1
# (squeeze: the colours' middle taken to the Centre point, squeezed on one side; off = the point is only where the
# turn / twist happens, BMC tester 11-03)
# (reach: Only inside a circle, its diameter in % of the colour box (an oval on a box that isn't square, as Turn
# counts), None = the whole shape; the turn fades to nothing at its edge, BMC tester 11-03)
# (area: that area's shape round the Centre point (STIR_AREAS), kept only with a reach; user's own idea 11-04)
# (flip_side / flip_up: the colours mirrored at the notes' own outline before this stir bends them, BMC tester 11-04)
# (PROPORTION, user's own idea 11-04, PLAN stage B: "seen" = beats per key the piano roll showed when a "round"
# tick was ticked (a key looked as tall as that many beats are wide; zoom never changes it afterwards); "round" =
# the area measured that way (a circle stays round on a long shape, Size = % of the box's longer side as seen);
# "spin" = Turn / Twist turn the colours that way too; "stretch" = the area's width across in %, with or without
# round (user picked "works either way"); "stretch_up" = its height the same way (user 11-06: Size = the base, both
# stretched from it; old files have none = 100))
STIR_DEFAULTS = {"turn": 0.0, "twist": 0.0, "centre": [50.0, 50.0], "sets": "stay", "edge": "escape",
                 "squeeze": True, "reach": None, "area": "circle", "round": False, "stretch": 100.0,
                 "stretch_up": 100.0,
                 "spin": False, "seen": None, "flip_side": False, "flip_up": False, "spot": None, "shape": None,
                 "tilt": 0.0, "skew": 0.0, "hard": False, "area_turn": True, "area_twist": True,
                 "area_squeeze": True, "reverse": False, "flip_in": "whole", "flip_by": "time", "mirror": "shape",
                 "size_of": "box", "size_mix": None, "flip_of": "before", "smooth": 0.0, "bend_smooth": 0.0,
                 "bulge": 0.0, "area_bulge": True,
                 "polar": 0.0, "ripple": 0.0, "ripple_len": 10.0, "ripple_kind": "side", "kaleido": 0,
                 "kaleido_turn": 0.0, "wring": 0.0, "wring_angle": 0.0, "round_edge": "cut", "area_polar": True,
                 "area_ripple": True, "area_kaleido": True, "area_wring": True}
# (MORE BENDS, user 11-07 after example pictures; each round the Centre point (its Area tick: round the area's own
# middle, only inside it), the order as SEEN: Turn / Twist -> Straight to round -> Twist along a line -> Ripple ->
# Kaleidoscope -> Bulge. "polar" = Straight to round % (angle -> across, distance -> up: stripes -> rays, Turn 90 ->
# rings; inside an Area the distance is the area's own, so a Square area gives square rings); "ripple" = wave height
# % of the box, "ripple_len" = wave length %, "ripple_kind" RIPPLE_KINDS; "kaleido" = slices (0 = off, 2..MOST_SLICES),
# "kaleido_turn" = the slice's turn in degrees; "wring" = Twist along a line, turns at the far end, "wring_angle" = the
# line's angle; "round_edge" = how Straight to round + Kaleidoscope end at an Area's edge: "cut" clean (default: a
# soft fade smeared them, pictures 11-07) / "fade" like the rest (user: "it can be a choice"))
RIPPLE_KINDS = ("side", "up", "rings")
ROUND_EDGES = ("cut", "fade")
MOST_SLICES = 32
MOST_RIPPLE, MOST_RIPPLE_LEN, LEAST_RIPPLE_LEN = 100.0, 200.0, 0.5
MOST_WRING = 50.0
SETTINGS_ONLY = {"smooth", "bend_smooth", "ripple_len", "ripple_kind", "kaleido_turn", "wring_angle"}  # (alone: no stir)
# ("bulge" = Bulge %, BMC tester 11-07 (their "local expansion / collapse", op art's bulging grid): + = the colours
# swell out from the middle like a lens, packed tight at the edge; - = pinched in. Never adds or loses a colour inside
# its reach: each spot looks along its own ray from the middle, the reach's outline stays put. Its reach = the Area
# ("area_bulge", ticked = only inside it, round its own middle) or the whole shape (round the Centre point, out to the
# colour box's farthest corner). A lens over this stir's finished bend: Turn / Twist / Squeeze happen first)
MOST_BULGE = 1000.0  # (typed; the knob covers +-200, like Twist's: past ~300 the packed edge turns to confetti on
# thin stripes, user 11-07 still wanted room for big shapes)
# ("smooth" / "bend_smooth" = Smooth corners, % (Chinese tester 11-05, user picked a setting): the row ends (time
# slices' lowest / highest keys) the colour flips mirror across / Bend with the outline stretches by, averaged over
# this much of the shape's height (width for the upside-down flip), so an outline's corner (a triangle's tip, an area
# cut off by the shape's edge) rounds instead of turning sharply = a straight seam. 0 = the sharp old way (old
# files); a stir made now starts at NEW_SMOOTH (window.stir_window.with_stir). Straight outlines never change)
MOST_SMOOTH, NEW_SMOOTH = 100.0, 20.0  # (20: the least that took the tester's seams away, fuzz/stir_sweep --known)
# ("size_mix": only Add between's steps, 0..1 = how far Size counts from the box to the circle inside (0..-1: to
# the circle around, SIZE_SHARE), so the area
# grows smoothly between an end of each kind, hunt 11-05 6; never set from the window)
MOST_REACH_INSIDE = 2000.0  # (Size of the circle inside may go as far as the box's own 200 %, hunt 11-05 8)
# (Chinese tester's feedback 11-04, PLAN.md, each a second choice with the old way as the default (user):
# "flip_by" = the sideways colour flip by "time" (mirrored across the row's notes' span) / "order" (each key row's
# 1st note <-> its last, 2nd <-> 2nd-last, lengths left out); "mirror" = with flips inside / outside the area, they
# mirror across "shape" (the whole shape's rows / height) / "area" (only that part's notes: it flips in place);
# "size_of" = Size in % of the "box" / of the circle "inside" it (its shorter side as seen, with Round as seen) /
# of the circle "around" it (its corner to corner))
FLIP_BY = ("time", "order")
MIRROR = ("shape", "area")
# ("flip_of" = what the colour flips mirror: "before" = the colours, then this stir bends them (the swirl stays at
# the Centre point) / "after" = this stir's finished pattern (the swirl turns over too, like the normal flips but
# notes stay), Chinese tester 11-05: "flip the canvas", their flips looked like doing nothing on stripes)
FLIP_OF = ("before", "after")
SIZE_OF = ("box", "inside", "around")  # ("around" = the circle round the box, through its corners: user 11-05)
# (each one's place on size_mix's line: box 0, the circle inside 1, the circle around -1, so Add between's steps
# grow smoothly from any kind to any other)
SIZE_SHARE = {"box": 0.0, "inside": 1.0, "around": -1.0}
# (BMC tester's round 3 answers 11-04, all only with a reach: "hard" = the area cut clean at its edge, Turn and
# Twist alike (a lazy susan), else the soft fade; "area_turn" / "area_twist" / "area_squeeze" = what the area
# limits (unticked = that part works on the whole shape); "reverse" = inside the area the colours' (tracks') order
# turned round, first <-> last; "flip_in" = where the colour flips work: "whole" / "inside" / "outside" the area)
FLIP_IN = ("whole", "inside", "outside")
# (tilt / skew: the area's box turned (degrees, counterclockwise as measured) / slanted (its top slid along by
# this much of its height), PLAN C2b; only with a reach)
MOST_SKEW = 10.0
SPOT_LEAST, SPOT_MOST = -1000.0, 1100.0  # (the area's own spot AND the Centre point may sit past the box: an area
# drawn beside the shape, hunt C 7; the largest area (Size 200 % x Stretch 1000 %) reaches 10 boxes from its middle,
# 1050 either side of 50; a far Centre point = gently curved bands, user 2026-11-10)
# (shape: area "drawn" = a library drawing's own copy (PLAN C2, user 11-04): {"name", "loops": [[[u, v], ...], ...]
# its filled outline in its 0..1 box (v up), "aspect": its width / height as drawn}; Size = its box's width)
# (spot: the area's own middle [% across, % up] when it doesn't move with the Centre point (PLAN C1, user 11-04:
# Turn / Twist still go round the Centre point, the area sits here); None = on the Centre point; only with a reach)
MOST_REACH, LEAST_REACH = 200.0, 0.01  # (200 % of the box at most, however Size is counted: user 11-06)
MOST_TURN = 360000.0  # (Stir's Turn: 1000 whole turns, only so a file can't hold nonsense; user 11-04 dropped +-360)
MOST_STRETCH, LEAST_STRETCH = 1000.0, 10.0
STIR_AREAS = ("circle", "square", "diamond")  # (Size = the circle's width / the square's side / the diamond's tip to tip)
DRAWN = "drawn"  # (an area shaped like a library drawing: stir["shape"])
MOST_SHAPE_POINTS = 20000  # (a drawn area's outline points, all loops: more = the shape dropped)
MAKE_SHAPE_POINTS = 2000  # (a new area from a drawing / shape keeps at most this many: every undo step and notes
# lookup carries the outline, 387 KB a shape at 20,000; the fade looks the same, hunt 11-04 T2)
FIELD_STEPS = 200  # (a drawn area's distances worked out on this many spots along its longer side, once per shape)
FIELD_PIECES = 600  # (from at most this many pieces of its outline)
MOST_STIRS = 20  # (stacked stirs on one shape)
STIR_SETS = ("stay", "move")  # (more than one colour set: each note keeps its set / takes the colour found itself)
# (colours stirred past the edge: gone / back in on the other side OF THE NOTES (each key row's own span) / back in
# on the other side of the BOX, the outline cutting it out (the old Wrap) / each key row stretched to the box first,
# so the stir bends with the outline; BMC tester 11-03: Wrap turned into "the box cut out" on round / slanted shapes)
# "repeat" (Repeat the colours, user 2026-11-10): Wrap around, but each key row comes back by a whole number of its
# colours' own repeats (Rows.repeat_of), the key rows by a whole number of the pattern's repeat up / down
# "bounce" / "bounce2" (Bounce back / Bounce back, edge twice, user 2026-11-10): a spot past its key row's notes
# bounces back into the row (Rows.bounce_lines), the keys off the lowest / highest key row: no seam, the colours run
# backwards past the edge. "bounce" turns at the end note (1 2 3 2 1), "bounce2" shows it twice (1 2 3 3 2 1)
STIR_EDGES = ("escape", "wrap", "repeat", "bounce", "bounce2", "cut", "bend")
BOUNCES = ("bounce", "bounce2")
WRAPS = ("wrap", "repeat") + BOUNCES  # (the edges Rows.wrapped brings back: neither tiled nor clipped before)
BLEND_DEFAULTS = {"angle": 0.0, "tight": TIGHT, "centre": [50.0, 50.0], "push": [0, 0],
                  "stir": STIR_DEFAULTS}  # (left out when so: Add between blends from them)
MOST = 15  # colours a set may use (16 with Use channel 10: the shape's "most", App.notes_tracks)
SET = 15  # colours in a colour set (always: the ticks change no colour)


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and x == x


def clean_recolour(r):
    """A saved sh["recolour"] -> {"mode", "amount": 0..100 (3 decimals), "seed": int, and only when set: "shares"
    [% per place], "pins" [places whose share stays when others change: the Shares window], "colours" n (2..16),
    "mix" True, "by" "chord" / "run", "stir" (clean_stir)}, or None (Off). Mode KEEP = Off with a Stir (none
    without one)."""
    if not isinstance(r, dict) or r.get("mode") not in MODES + (KEEP,):
        return None
    stir = clean_stir(r.get("stir"))
    from notes.liquify import clean_liquify
    liquify = clean_liquify(r.get("liquify"))
    from notes.warp import clean_warp
    warp = clean_warp(r.get("warp"))
    if r["mode"] == KEEP and not stir and not liquify and not warp:
        return None
    a = r.get("amount", 100)
    if not number(a):
        a = 100
    s = r.get("seed", 1)
    if not number(s):
        s = 1
    out = {"mode": r["mode"], "amount": round(min(max(float(a), 0.0), 100.0), 3),
           "seed": int(min(max(s, 0), MOST_SEED))}
    sh = r.get("shares")
    if isinstance(sh, list) and 0 < len(sh) <= 16 and all(number(x) and x >= 0 for x in sh) and sum(sh) > 0:
        out["shares"] = [round(min(float(x), 100.0), 3) for x in sh]
    n = r.get("colours")
    if number(n) and n == int(n) and 2 <= n <= 16:
        out["colours"] = int(n)
    pins = r.get("pins")
    if isinstance(pins, list):
        pins = sorted({int(p) for p in pins if number(p) and p == int(p) and 0 <= p < 16})
        if pins:
            out["pins"] = pins
    if r.get("mix") is True:
        out["mix"] = True
    if r.get("by") in BY[1:]:
        out["by"] = r["by"]
    if r.get("pattern") in PATTERNS[1:]:
        out["pattern"] = r["pattern"]
    a = r.get("angle")
    if number(a) and abs(a) <= 360 and round(a, 3):
        out["angle"] = round(float(a), 3)
    t = r.get("tight")
    if number(t) and 0 <= t <= MOST_TIGHT and round(t, 3) != TIGHT:
        out["tight"] = round(float(t), 3)
    c = r.get("centre")
    if isinstance(c, list) and len(c) == 2 and all(number(x) and 0 <= x <= 100 for x in c) and c != [50, 50]:
        out["centre"] = [round(float(x), 3) for x in c]
    p = r.get("push")
    if isinstance(p, list) and len(p) == 2 and all(number(x) and x == int(x) and abs(x) <= MOST_PUSH for x in p) \
            and any(p):
        out["push"] = [int(x) for x in p]
    f = r.get("frame")
    if isinstance(f, list) and all(number(x) for x in f) and tuple(f) in FRAMES and tuple(f) != SAME:
        out["frame"] = [int(x) for x in f]
    f = r.get("cut_frame")  # (a Stir piece: its whole's frame when cut, SAME too: engine.stirred_piece)
    if isinstance(f, list) and all(number(x) for x in f) and tuple(f) in FRAMES:
        out["cut_frame"] = [int(x) for x in f]
    w = r.get("whole")
    if isinstance(w, list) and len(w) == 6 and all(number(x) and x == int(x) and abs(x) < 1 << 40 for x in w) \
            and w[4] >= 0 and w[5] >= 0 and any(w[:4]):
        out["whole"] = [int(x) for x in w]
    if stir:
        out["stir"] = stir
    if liquify:
        out["liquify"] = liquify
    if warp:
        out["warp"] = warp
    tw =clean_turned(r.get("turned"))
    if tw:
        out["turned"] = tw
    return out


def clean_turned(tw):
    """A saved rc["turned"] -> sorted [[before, now], ...] that swap colours one for one (no colour staying), or
    None."""
    if not isinstance(tw, list) or not tw or len(tw) > ROUND:
        return None
    pairs = []
    for p in tw:
        if not (isinstance(p, list) and len(p) == 2 and all(number(x) and x == int(x) and 0 <= x < ROUND for x in p)):
            return None
        if int(p[0]) != int(p[1]):
            pairs.append([int(p[0]), int(p[1])])
    a, b = [p[0] for p in pairs], [p[1] for p in pairs]
    if not pairs or len(set(a)) != len(a) or set(a) != set(b):
        return None
    return sorted(pairs)


def turned_map(tw, back=False):
    """The colours swap of a "turned" list: f(colours) -> colours (back: the other way); colours not in it stay."""
    a = np.array([p[0] for p in tw], np.int64)
    b = np.array([p[1] for p in tw], np.int64)
    if back:
        a, b = b, a
    o = np.argsort(a)
    a, b = a[o], b[o]

    def f(x):
        x = np.asarray(x, np.int64)
        j = np.clip(np.searchsorted(a, x), 0, len(a) - 1)
        return np.where(a[j] == x, b[j], x)
    return f


def turn_again(tw, swap):
    """The "turned" list after one more swap {colour now: new colour} on top of tw (None: none before); None when it
    all comes back to the colours before."""
    before = {now: was for was, now in (tw or [])}  # (colour now -> the one it was before)
    out = {}
    for now, new in swap.items():
        out[before.pop(now, now)] = new
    for now, was in before.items():
        out[was] = now
    return clean_turned([[a, b] for a, b in out.items()])


def clean_stir(s):
    """A saved rc["stir"]: one stir (a dict) or STACKED STIRS (a list, BMC tester 11-03: each stirs the colours the
    one before left, in order; at most MOST_STIRS) -> one clean stir, a list of 2+ (a stir still at its defaults =
    {} keeps its place), or None (no stir)."""
    if isinstance(s, list):
        items = [one_stir(x) or {} for x in s[:MOST_STIRS] if isinstance(x, dict)]
        if not any(items):
            return None
        return items if len(items) > 1 else items[0]
    return one_stir(s)


def stir_stack(s):
    """A saved rc["stir"] -> its stirs in order, each with every setting filled in ([] = no stir)."""
    s = clean_stir(s)
    return [] if not s else [full_stir(x) for x in (s if isinstance(s, list) else [s])]


def one_stir(s):
    """One saved stir -> {"turn": degrees -MOST_TURN..MOST_TURN, "twist": turns -MOST_TIGHT..MOST_TIGHT, "centre": [% across,
    % up], "sets": "move" (STIR_SETS), "edge": "wrap" (STIR_EDGES), "squeeze": False, "reach": % LEAST_REACH..
    MOST_REACH (+ "area": STIR_AREAS, "stretch" / "stretch_up": %, "round": True, only with a reach), "spin": True, "seen":
    beats per key (only with round / spin), "flip_side" / "flip_up": True}, each only when not its default (STIR_DEFAULTS), or None (no
    stir)."""
    if not isinstance(s, dict):
        return None
    out = {}
    a = s.get("turn")
    if number(a) and abs(a) <= MOST_TURN and round(a, 3):
        out["turn"] = round(float(a), 3)
    t = s.get("twist")
    if number(t) and abs(t) <= MOST_TIGHT and round(t, 3):
        out["twist"] = round(float(t), 3)
    b = s.get("bulge")
    if number(b) and abs(b) <= MOST_BULGE and round(b, 3):
        out["bulge"] = round(float(b), 3)
    for k, lo, hi, plain in (("polar", 0.0, 100.0, 0.0), ("ripple", 0.0, MOST_RIPPLE, 0.0),
                             ("ripple_len", LEAST_RIPPLE_LEN, MOST_RIPPLE_LEN, 10.0),
                             ("kaleido_turn", -360.0, 360.0, 0.0), ("wring", -MOST_WRING, MOST_WRING, 0.0),
                             ("wring_angle", -360.0, 360.0, 0.0)):
        v = s.get(k)
        if number(v) and lo <= round(v, 3) <= hi and round(v, 3) != plain:
            out[k] = round(float(v), 3)
    v = s.get("kaleido")
    if number(v) and 2 <= round(v) <= MOST_SLICES:
        out["kaleido"] = int(round(v))
    if s.get("ripple_kind") in RIPPLE_KINDS[1:]:
        out["ripple_kind"] = s["ripple_kind"]
    c = s.get("centre")
    if isinstance(c, list) and len(c) == 2 and all(number(x) and SPOT_LEAST <= x <= SPOT_MOST for x in c) and \
            [round(x, 3) for x in c] != [50, 50]:
        out["centre"] = [round(float(x), 3) for x in c]
    if s.get("sets") == "move":
        out["sets"] = "move"
    if s.get("edge") in STIR_EDGES[1:]:
        out["edge"] = s["edge"]
    if s.get("squeeze") is False:
        out["squeeze"] = False
    for k in ("flip_side", "flip_up"):
        if s.get(k) is True:
            out[k] = True
    if s.get("flip_by") in FLIP_BY[1:]:
        out["flip_by"] = s["flip_by"]
    if s.get("flip_of") in FLIP_OF[1:]:
        out["flip_of"] = s["flip_of"]
    for k in ("smooth", "bend_smooth"):
        v = s.get(k)
        if number(v) and 0 < round(v, 3) <= MOST_SMOOTH:
            out[k] = round(float(v), 3)
    r = s.get("reach")
    mixed = clean_size_mix(s.get("size_mix"))
    if number(r) and LEAST_REACH <= round(r, 3) <= (MOST_REACH_INSIDE if s.get("size_of") == "inside" or
                                                     mixed is not None else MOST_REACH):
        out["reach"] = round(float(r), 3)
        if s.get("area") in STIR_AREAS[1:]:
            out["area"] = s["area"]
        elif s.get("area") == DRAWN:
            shape = clean_area_shape(s.get("shape"))
            if shape:
                out["area"], out["shape"] = DRAWN, shape
        for key in ("stretch", "stretch_up"):
            w = s.get(key)
            if number(w) and LEAST_STRETCH <= round(w, 3) <= MOST_STRETCH and round(w, 3) != 100:
                out[key] = round(float(w), 3)
        p = s.get("spot")
        if isinstance(p, list) and len(p) == 2 and all(number(x) and SPOT_LEAST <= x <= SPOT_MOST for x in p):
            out["spot"] = [round(float(x), 3) for x in p]
        a = s.get("tilt")
        if number(a) and abs(a) <= 360 and round(a, 3) % 360:
            out["tilt"] = round(float(a), 3)
        a = s.get("skew")
        if number(a) and abs(a) <= MOST_SKEW and round(a, 4):
            out["skew"] = round(float(a), 4)
        for k in ("hard", "reverse"):
            if s.get(k) is True:
                out[k] = True
        for k in ("area_turn", "area_twist", "area_squeeze", "area_bulge", "area_polar", "area_ripple",
                  "area_kaleido", "area_wring"):
            if s.get(k) is False:
                out[k] = False
        if s.get("round_edge") in ROUND_EDGES[1:]:
            out["round_edge"] = s["round_edge"]
        if s.get("flip_in") in FLIP_IN[1:]:
            out["flip_in"] = s["flip_in"]
        if s.get("mirror") in MIRROR[1:]:
            out["mirror"] = s["mirror"]
        if s.get("size_of") in SIZE_OF[1:]:
            out["size_of"] = s["size_of"]
        if isinstance(mixed, list) or (mixed is not None and mixed != SIZE_SHARE[out.get("size_of", "box")]):
            out["size_mix"] = mixed
    v = s.get("seen")
    if number(v) and 1e-9 < v < 1e9:
        if s.get("round") is True and "reach" in out:
            out["round"] = True
        if s.get("spin") is True:
            out["spin"] = True
        if out.get("round") or out.get("spin"):
            out["seen"] = float(v)
    if set(out) <= SETTINGS_ONLY:  # (Smooth corners / a bend's second number alone stirs nothing: no stir)
        return None
    return out


_CLEAN_SHAPES = OrderedDict()  # id -> (the saved object, its clean copy): clean_recolour runs on every redraw
_SAME_SHAPES = OrderedDict()  # its content -> the one clean copy (shapes from undo / a file share it: hunt C 8)


def clean_area_shape(d):
    """A saved stir["shape"] -> {"name": str, "loops": [[[u, v], ...] 3+ points each], "aspect": > 0}, or None.
    Equal drawings come back as the SAME object (so the window and the distance grid compare them by identity)."""
    got = _CLEAN_SHAPES.get(id(d))
    if got is not None and got[0] is d:
        _CLEAN_SHAPES.move_to_end(id(d))
        return got[1]
    out = None
    if isinstance(d, dict) and isinstance(d.get("loops"), list) and number(d.get("aspect")) and \
            1e-3 <= d["aspect"] <= 1e3:
        loops = []
        try:
            for lp in d["loops"][:1000]:
                a = np.asarray(lp, np.float64)
                if a.ndim == 2 and a.shape[1] == 2 and len(a) >= 3 and np.isfinite(a).all() and \
                        np.abs(a).max() < 100:
                    loops.append(np.round(a, 5).tolist())
        except (TypeError, ValueError):
            loops = []
        if loops and sum(len(lp) for lp in loops) <= MOST_SHAPE_POINTS:
            out = {"name": str(d.get("name", ""))[:200], "loops": loops, "aspect": round(float(d["aspect"]), 6)}
            key = json.dumps(out)
            out = _SAME_SHAPES.setdefault(key, out)
            _SAME_SHAPES.move_to_end(key)
            while len(_SAME_SHAPES) > 64:
                _SAME_SHAPES.popitem(last=False)
    while len(_CLEAN_SHAPES) > 1024:  # (the oldest go first; never all at once, hunt C 8)
        _CLEAN_SHAPES.popitem(last=False)
    _CLEAN_SHAPES[id(d)] = (d, out)
    if out is not None:
        _CLEAN_SHAPES[id(out)] = (out, out)
    return out


def full_stir(s):
    """One stir with every setting filled in (Add between blends one end's into the other's)."""
    s = one_stir(s) or {}
    return {k: s.get(k, STIR_DEFAULTS[k]) for k in STIR_DEFAULTS}


# WHOLE (rc "whole", a sliced drawn piece: carried): [first start, last end, lowest key, highest key] of the shape it
# was cut from, each counted from the piece's own box (box_of, before any flip), + that box's width (ticks) and
# height (keys). The dice corner and the Clumps areas count in that box, so a piece keeps the colours it had in the
# whole wherever it goes, flipped, turned or no longer a piece.


def carried(whole_rc, whole, piece):
    """A piece's Recolour, cut from a shape whose Recolour is whole_rc (whole: that shape's notes' box, piece: the
    piece's, both box_of without Recolour, not cut at the piano roll's edges): the whole's box remembered (rc
    "whole", counted where the colours are worked out: a flipped shape's places mirrored back). A shape turned since
    its Recolour was set: nothing remembered (Shuffle throws again after a turn anyway)."""
    rc = dict(whole_rc)
    a, b, _, d = rc.get("frame") or SAME
    if b:
        rc.pop("whole", None)
        rc.pop("cut_frame", None)
        return clean_recolour(rc)
    # (the whole's flips when cut: a Stir piece works the whole's colours out flipped like that and mirrors back only
    # the flips made since, engine.stirred_piece; hunt 2026-11-07: a shape flipped before the cut swapped colours)
    rc["cut_frame"] = list(rc.get("frame") or SAME)
    S, P = [int(v) for v in whole], [int(v) for v in piece]
    o = rc.get("whole") or [0, 0, 0, 0]  # (cut from a piece: counted in its own whole's box)
    B = [S[i] + o[i] for i in range(4)]  # (where the whole's colours count, mirrored back like its notes)
    # (a flipped whole's notes are mirrored back in its box, the piece's in its own: the same notes land c apart)
    ct = S[0] + S[1] - P[0] - P[1] if a < 0 else 0
    ck = S[2] + S[3] - P[2] - P[3] if d < 0 else 0
    rc["whole"] = [B[0] - ct - P[0], B[1] - ct - P[1], B[2] - ck - P[2], B[3] - ck - P[3], P[1] - P[0], P[3] - P[2]]
    return clean_recolour(rc)


# FRAME (rc "frame"): the shape flipped / turned since its Recolour was set, as [a, b, c, d]: a place in its box as
# shown (time, keys, each -0.5..0.5 from the middle) -> where it was then: (a * x + b * y, c * x + d * y). The colours
# are worked out there, so they flip and turn with the shape (fx.flip_shape / turn_shape: moved); the Clumps
# settings stay as typed (the Shares window says so).
SAME = (1, 0, 0, 1)
SIDEWAYS, UPSIDE = (-1, 0, 0, 1), (1, 0, 0, -1)  # (each move undone)
CLOCKWISE, ANTI = (0, -1, 1, 0), (0, 1, -1, 0)
FRAMES = {(a, 0, 0, d) for a in (1, -1) for d in (1, -1)} | {(0, b, c, 0) for b in (1, -1) for c in (1, -1)}


def moved(sh, back):
    """sh's Recolour told the shape was flipped / turned (back: that move undone, SIDEWAYS...). Changes sh."""
    r = sh.get("recolour")
    if not isinstance(r, dict):
        return
    a, b, c, d = r.get("frame") or SAME
    e, f, g, h = back
    m = [a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h]
    sh["recolour"] = {k: v for k, v in r.items() if k != "frame"}
    if tuple(m) != SAME:
        sh["recolour"]["frame"] = m


def rule_of(r):
    """clean_recolour + "most": how many colours one set holds (with_most)."""
    rc = clean_recolour(r)
    if rc is not None:
        rc["most"] = 16 if r.get("most") == 16 else MOST
    return rc


def with_most(sh, all16):
    """sh with its Recolour told a set holds 16 colours while Multi channel uses channel 10 (App.notes_tracks)."""
    r = sh.get("recolour")
    return dict(sh, recolour=dict(r, most=16)) if all16 and isinstance(r, dict) else sh


def can_recolour(sh):
    """Every shape but a Hz bass (its tracks are its voices and layers, not colours)."""
    return not sh.get("hz")


def is_recoloured(sh):
    return clean_recolour(sh.get("recolour")) is not None and can_recolour(sh)


def without_recolour(sh):
    return {k: v for k, v in sh.items() if k != "recolour"}


def new_seed():
    return random.randint(1, MOST_SEED)


def _mixed(x):
    """splitmix64 on uint64s: each value -> one that looks random."""
    with np.errstate(over="ignore"):
        x = x + np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def dice(notes, low, seed, salt, corner):
    """One number 0 <= u < 1 per note, from the note itself (counted from corner = the shape's first tick and lowest
    key, so moving the shape keeps it), its colour before (without its Colours round: the project's repeat tick
    changes no dice) and the seed: the same note in the same shape always gets the same one."""
    t0, k0 = corner
    cols = [np.asarray(notes[:, 0], np.int64) - t0, np.asarray(notes[:, 1], np.int64) - t0,
            np.asarray(notes[:, 2], np.int64) - k0, np.asarray(low, np.int64)]
    fast = loops()
    if fast and len(notes):  # (the compiled mix: the very same numbers)
        out = np.empty(len(notes), np.float64)
        fast.dice_mix(np.uint64(seed * 2 + salt), *(np.ascontiguousarray(c).view(np.uint64) for c in cols), out)
        return out
    with np.errstate(over="ignore"):
        x = np.full(len(notes), np.uint64(seed * 2 + salt), np.uint64)
        t = np.empty(len(notes), np.uint64)
        for c in cols:  # (_mixed done in place: half the time on millions of notes)
            x ^= c.view(np.uint64)
            x += np.uint64(0x9E3779B97F4A7C15)
            x ^= np.right_shift(x, np.uint64(30), out=t)
            x *= np.uint64(0xBF58476D1CE4E5B9)
            x ^= np.right_shift(x, np.uint64(27), out=t)
            x *= np.uint64(0x94D049BB133111EB)
            x ^= np.right_shift(x, np.uint64(31), out=t)
        x >>= np.uint64(11)
    return x.astype(np.float64) / float(1 << 53)


def colour_sets(low):
    """(each note's set number, each set's own colours, sorted) of colours low (tracks without their rounds): cut
    every SET colours in order."""
    from notes.engine import unique_inverse
    ids, inv = unique_inverse(np.asarray(low, np.int64))  # (np.unique's, counted: millions of notes)
    set_of = np.arange(len(ids)) // SET
    if len(ids) > SET and len(ids) % SET == 1:  # (a lone last colour joins the set before: 16 colours = one set)
        set_of[-1] -= 1
    return set_of[inv.reshape(-1)], [ids[set_of == s] for s in range(int(set_of.max()) + 1)]


def palettes(owns, n, most=MOST):
    """Each set's colours to shuffle among: its own, then new ones (after every colour of the shape: a new one
    never lands between other tracks) up to n (None = its own only; never more than most)."""
    out, nxt = [], max((int(o[-1]) + 1 for o in owns), default=0)
    for own in owns:
        extra = max(0, min(n or 0, most) - len(own))
        out.append(np.concatenate([own, np.arange(nxt, nxt + extra, dtype=np.int64)]) if extra else own)
        nxt += extra
    return out


def weights(n, shares):
    """The chances of a set's n colours (shares by place, missing ones the average), adding up to 1."""
    if shares:
        w = np.array([shares[i] if i < len(shares) else sum(shares) / len(shares) for i in range(n)], np.float64)
    else:
        w = np.ones(n)
    if w.sum() <= 0:
        w = np.ones(n)
    return w / w.sum()


def picked(w, u):
    """The place each throw u (0 <= u < 1) lands on, colours given chances w (one with 0 never)."""
    return np.minimum(np.searchsorted(np.cumsum(w), u, side="right"), len(w) - 1)


def leaders(notes, by):
    """Each note's group's first note (its index): "chord" = notes starting together (lowest key first), "run" =
    notes on one key each starting where the one before ends."""
    s, e, k = (np.asarray(notes[:, c], np.int64) for c in (0, 1, 2))
    if by == "chord":
        o = np.lexsort((k, s))
        new = np.ones(len(o), bool)
        new[1:] = s[o][1:] != s[o][:-1]
        lead = np.empty(len(o), np.int64)
        lead[o] = o[np.flatnonzero(new)[np.cumsum(new) - 1]]
        return lead
    # a run: each note follows a note on its key ending where it starts (also past notes overlapping in between)
    ends = k * (1 << 40) + e
    o = np.argsort(ends, kind="stable")
    at = np.searchsorted(ends[o], k * (1 << 40) + s)
    found = at < len(o)
    found[found] = ends[o][at[found]] == (k * (1 << 40) + s)[found]
    lead = np.arange(len(s))
    before = o[np.minimum(at, len(o) - 1)]
    ok = found & (s[before] < s)  # (starting earlier: a chain never comes back round)
    lead[ok] = before[ok]
    while True:  # (each note to the first of its chain)
        nxt = lead[lead]
        if (nxt == lead).all():
            return lead
        lead = nxt


def wanted(rc):
    """How many colours a set uses (palettes' n): Colours, else its own ones (Split: at least 2)."""
    return rc.get("colours") or (2 if rc.get("mode") in SPLIT else None)


def plan(tracks, rc):
    """(each note's set, each set's colours) for tracks under rc, or None when there's nothing to shuffle (no set
    with 2 colours, or with Mix across sets fewer than 2 in all)."""
    if tracks is None or not len(tracks):
        return None
    which, owns = colour_sets(np.asarray(tracks, np.int64) % ROUND)
    pals = palettes(owns, wanted(rc), rc.get("most", MOST))
    return (which, pals) if enough(pals, rc.get("mix")) else None


def enough(pals, mix):
    """Something to shuffle: a set with 2 colours, or with Mix across sets (2 sets or more) 2 in all."""
    if not pals:
        return False
    return (sum(len(p) for p in pals) if mix and len(pals) > 1 else max(len(p) for p in pals)) >= 2


def centre_spot(rc, notes, own, c):
    """Where the Centre point c (% of the colours' box) sits on the piano roll, (tick, key), for notes (as shown,
    without Recolour; own: engine.free_box): in the box the areas count in (a piece: its whole's), flipped / turned
    as the shape was since (rc "frame")."""
    S = box_of(notes)
    B, F = colour_box(notes, rc, own)
    a, b, f2, d = rc.get("frame") or SAME
    if not b:
        t, k = B[0] + c[0] / 100 * (B[1] - B[0]), B[2] + c[1] / 100 * (B[3] - B[2])
        return (S[0] + S[1] - t if a < 0 else t), (S[2] + S[3] - k if d < 0 else k)
    (a0, a1), (b0, b1) = whole_shares(rc)
    u, v = a0 + c[0] / 100 * (a1 - a0) - 0.5, b0 + c[1] / 100 * (b1 - b0) - 0.5
    x, y = 0.5 + f2 * v, 0.5 + b * u  # (shown = the turn undone back: rows and columns swapped)
    return F[0] + x * (F[1] - F[0]), F[2] + y * (F[3] - F[2])


def centre_at(rc, notes, own, t, k, one_key=False):
    """The Centre point (% of the colours' box) for a spot (tick, key) on the piano roll (centre_spot's way back).
    one_key: notes all on one key = a box one key tall round them (Liquify's brush: else up / down is lost and a
    one-row shape can't be brushed, hunt 11-08); else every key there is the middle (50)."""
    S = box_of(notes)
    B, F = colour_box(notes, rc, own)
    a, b, f2, d = rc.get("frame") or SAME
    if not b:
        t, k = (S[0] + S[1] - t if a < 0 else t), (S[2] + S[3] - k if d < 0 else k)
        flat = 100 * (k - B[2]) + 50.0 if one_key else 50.0
        return [100 * (t - B[0]) / max(B[1] - B[0], 1), 100 * (k - B[2]) / (B[3] - B[2]) if B[3] > B[2] else flat]
    x, y = (t - F[0]) / max(F[1] - F[0], 1) - 0.5, ((k - F[2]) / (F[3] - F[2]) if F[3] > F[2]
                                                   else (k - F[2] + 0.5 if one_key else 0.5)) - 0.5
    u, v = 0.5 + b * y, 0.5 + f2 * x
    (a0, a1), (b0, b1) = whole_shares(rc)
    return [100 * (u - a0) / max(a1 - a0, 1e-12), 100 * (v - b0) / (b1 - b0) if b1 > b0 else 50.0]


_BOXES = OrderedDict()  # id(notes) -> (weak link to them, their box): big remembered notes asked again and again


def box_of(notes):
    """(first start, last end, lowest key, highest key) of notes. Big arrays' boxes are remembered (the Stir window
    asks at every mouse move: 4 x 1 M notes looked at a move, hunt 11-04 T1); the remembered notes are never changed
    in place, and the weak link never keeps them alive."""
    big = isinstance(notes, np.ndarray) and len(notes) > 20000
    if big:
        got = _BOXES.get(id(notes))
        if got is not None and got[0]() is notes:
            _BOXES.move_to_end(id(notes))
            return got[1]
    box = int(notes[:, 0].min()), int(notes[:, 1].max()), int(notes[:, 2].min()), int(notes[:, 2].max())
    if big:
        try:
            _BOXES[id(notes)] = (weakref.ref(notes), box)
        except TypeError:  # (a view of another array can't be linked weakly: not remembered)
            return box
        if len(_BOXES) > 64:
            _BOXES.popitem(last=False)
    return box


def places_of(notes, rc, gate, box):
    """Where the dice, groups and areas look at each note: where it was before the shape was gate flipped (gate:
    its runs mirrored back, user's rule: every key row exactly reversed, colours too) and flipped (rc "frame"
    without a turn: mirrored back in the box, note for note). A turned shape's notes are new ones: its turn only
    moves the Clumps areas (areas)."""
    if gate:
        from notes.gateflip import mirrored_runs
        notes = mirrored_runs(notes, None, True)[0]
    a, b, _, d = rc.get("frame") or SAME
    if b or (a > 0 and d > 0):
        return notes
    p = np.array(notes, np.int64)
    if a < 0:
        w = box[0] + box[1]
        p[:, 0], p[:, 1] = w - p[:, 1], w - p[:, 0]
    if d < 0:
        p[:, 2] = box[2] + box[3] - p[:, 2]
    return p


def recoloured(notes, tracks, rc, held=False, gate=False, own=None):
    """The shape's notes and tracks after its Recolour rc (rule_of). Nothing to shuffle (plan): unchanged. First
    the notes are settled as their colours before would sound (render's overlap rule on each colour: a note cut
    where a later one of its colour starts on its key, notes starting together made one), so a new colour never
    frees a cut note: the notes stay as heard with Recolour off. held: sustained (overlaps kept, render held): not
    settled, its notes of one colour may still sound together. gate: the shape is gate flipped (places_of). own:
    the notes' box without the piano roll's edges (engine.free_box; None = as they are): the dice and areas count
    from it (and a piece's whole's box, rc "whole": colour_box), so pushing a shape against an edge keeps its
    colours."""
    rc = kept_rule(rc)
    if (rc.get("stir") or rc.get("liquify") or rc.get("warp")) and tracks is not None \
            and len(notes) >= BEFORE_STIR_LEAST:
        got = before_liquify(notes, tracks, rc, held, gate, own)
        if got is None:
            return notes, tracks
        rc, notes, lap, tw, (n, at, low, now, which, pals, pick, box, own) = got
        now = liquified_now(n, at, low, now, rc, which, pals, pick, box, own)
        return notes.copy(), lap + (turned_map(tw)(now) if tw else now)  # (a copy: whoever gets it may change it)
    return _recoloured(notes, tracks, rc, held, gate, own)


def kept_rule(rc):
    """rc as recoloured works with it: Off + a Stir / Warp / Liquify (KEEP) =the shape's own colours only, the same however
    it got there: settings left from another mode (Per chord, Colours, Mix, Shares) wait for it, the seed too (which
    of two notes sounding together gives way: never random here)."""
    if rc["mode"] == KEEP:
        rc = {k: v for k, v in rc.items() if k in KEEP_KEYS}
        rc["seed"] = 1
    return rc


def before_liquify(notes, tracks, rc, held=False, gate=False, own=None):
    """recoloured's work up to the Liquify: (rc, settled notes, each note's round, the "turned" list, all_stirred's
    arguments with the colours after every stir and the Warp), or None = nothing to recolour. Big shapes keep it (the brush's
    strokes and Stir knob turns change nothing before it). Liquify's brush starts a stroke with it too (Live)."""
    rc = kept_rule(rc)
    if gate and (rc.get("liquify") or rc.get("warp")):  # (they work on where notes show: liquify.shown_places)
        rc = dict(rc, _gate=True)
    if tracks is None or len(notes) < BEFORE_STIR_LEAST or not keeping():
        b = _before_stir(notes, tracks, rc, held, gate, own) if tracks is not None or rc["mode"] in SPLIT else None
        if b is None:
            return None
        notes2, lap, tw, (n, at, low, now, which, pals, pick, box, own2) = b
        now = stirs_only(n, at, low, now, rc, which, pals, pick, box, own2)
        now = warped_now(n, at, low, now, rc, which, pals, pick, box, own2)
        return rc, notes2, lap, tw, (n, at, low, now, which, pals, pick, box, own2)
    # (only the Stir changed since last time (a knob turned): the colours before it are kept, user 11-07: 3 M
    # notes took a second a change; the same for Liquify / Warp, and a brush stroke keeps the stirred colours too)
    key = (json.dumps({k: v for k, v in rc.items() if k not in ("stir", "liquify", "warp")}, sort_keys=True), held,
           gate,
           None if own is None else tuple(own))
    got = _BEFORE_STIR.get("got")
    if got is None or got[0]() is not notes or got[1]() is not tracks or got[2] != key:
        # (weak links: the shape's notes gone = the kept work goes too, hunt 11-07: ~150 MB a million notes stayed)
        gone = lambda ref: any(r is ref for r in _BEFORE_STIR.get("got", ())[:2]) and forget_kept()
        got = (weakref.ref(notes, gone), weakref.ref(tracks, gone), key,
               _before_stir(notes, tracks, rc, held, gate, own))
        _BEFORE_STIR["got"] = got
    b = got[3]
    if b is None:
        return None
    notes2, lap, tw, (n, at, low, now, which, pals, pick, box, own2) = b
    skey = json.dumps(rc.get("stir"), sort_keys=True)
    kept = _BEFORE_STIR.get("stirred")
    if kept is not None and kept[0] is b and kept[1] == skey:
        now = kept[2]
    else:
        now = stirs_only(n, at, low, now, rc, which, pals, pick, box, own2)
        if rc.get("liquify") or rc.get("warp"):  # (kept for the next brush stroke / Warp drag: only they change then)
            now = np.array(now)
            now.setflags(write=False)
            _BEFORE_STIR["stirred"] = (b, skey, now)
    if rc.get("warp"):  # (the Warp's colours kept too: a Liquify stroke on a warped shape redoes neither)
        wkey = skey + json.dumps(rc.get("warp"), sort_keys=True)
        kept = _BEFORE_STIR.get("warped")
        if kept is not None and kept[0] is b and kept[1] == wkey:
            now = kept[2]
        else:
            now = warped_now(n, at, low, now, rc, which, pals, pick, box, own2)
            if rc.get("liquify"):
                now = np.array(now)
                now.setflags(write=False)
                _BEFORE_STIR["warped"] = (b, wkey, now)
    return rc, notes2, lap, tw, (n, at, low, now, which, pals, pick, box, own2)


BEFORE_STIR_LEAST = 100_000  # (notes: smaller shapes are quick anyway, no memory kept for them)
_BEFORE_STIR = {}  # "got": (weak notes, weak tracks, key, _before_stir's result) of the last big stirred shape
_KEEP_WHILE = [None]  # a function: True = keep the quick work now (the app: a Stir / Warp / Liquify window open)


def keep_while(fn):
    """fn() True = big shapes' quick Stir / Warp / Liquify work may be kept (before_liquify, _sorted_notes, rows_of);
    False = worked out each time, nothing kept (memory use 2026-11-10: ~210-315 MB at 2 M notes). None = always."""
    _KEEP_WHILE[0] = None if fn is None else weakref.WeakMethod(fn) if hasattr(fn, "__self__") else lambda: fn


def keeping():
    ref = _KEEP_WHILE[0]
    fn = None if ref is None else ref()
    return True if fn is None else bool(fn())


def forget_kept():
    """Let go of everything kept for quick Stir changes (New / Open / the Stir window closed / the notes gone)."""
    _BEFORE_STIR.clear()
    _SORTED.clear()
    _ROWS.clear()


def _before_stir(notes, tracks, rc, held, gate, own):
    """recoloured's work up to the Stir: (settled notes, each note's round, the "turned" list, all_stirred's
    arguments but rc), or None = nothing to recolour. Arrays made read-only: they are kept."""
    out = []
    _recoloured(notes, tracks, rc, held, gate, own, lambda *a: out.append(a))
    if not out:
        return None
    notes2, lap, tw, args = out[0]
    mine = {}  # (an array the caller handed in (held: the notes as they came) is copied: theirs stay theirs)

    def own(a):
        if not isinstance(a, np.ndarray):
            return a
        if id(a) not in mine:
            b = a.copy() if any(np.shares_memory(a, x) for x in (notes, tracks) if isinstance(x, np.ndarray)) else a
            b.setflags(write=False)
            mine[id(a)] = b
        return mine[id(a)]
    return own(notes2), own(lap), tw, tuple(own(x) for x in args)


def _recoloured(notes, tracks, rc, held, gate, own, keep=None):
    """recoloured's own work (keep(notes, lap, tw, all_stirred's args): hands them over instead of stirring)."""
    if tracks is None and len(notes) and rc["mode"] in SPLIT:  # (one colour: Split works from it)
        tracks = np.zeros(len(notes), np.int64)
    if tracks is None or not len(notes) or plan(tracks, rc) is None:
        return notes, tracks
    if gate and (rc.get("liquify") or rc.get("warp")):  # (they work on where notes show: liquify.shown_places)
        rc = dict(rc, _gate=True)
    t = np.asarray(tracks, np.int64)
    if not held:
        notes, t = settled(notes, t)
    tw = rc.get("turned") if rc["mode"] == KEEP else None
    if tw:  # (Reverse track order: stirred from the colours before it, then swapped the same way, KEEP_KEYS)
        t = t - t % ROUND + turned_map(tw, back=True)(t % ROUND)
    which, pals = plan(t, rc)
    low = t % ROUND
    lap = t - low  # (each note's round, put back after)
    box, own = colour_box(notes, rc, own)
    at = places_of(notes, rc, gate, box_of(notes))
    corner = (box[0], box[2])
    pick = dice(at, low, rc["seed"], 1, corner)
    if rc["mode"] == KEEP:
        if keep:
            return keep(notes, lap, tw, (notes, at, low, low, which, pals, pick, box, own))
        if tw:
            return notes, lap + turned_map(tw)(all_stirred(notes, at, low, low, rc, which, pals, pick, box, own))
        return notes, lap + all_stirred(notes, at, low, low, rc, which, pals, pick, box, own)
    roll = dice(at, low, rc["seed"], 0, corner)
    clumps = rc["mode"] == "clumps"
    if clumps:  # (every note takes the colour of the area it lands in: the throw = where it lands)
        n = sum(len(p) for p in pals) if rc.get("mix") and len(pals) > 1 else max(len(p) for p in pals)
        pick, roll = areas(at, rc, pick, n, box, own), np.zeros(len(notes))
    if rc.get("by"):
        lead = leaders(at, rc["by"])
        pick, roll = pick[lead], roll[lead]
    change = roll < (1.0 if clumps else rc["amount"] / 100)
    shares = rc.get("shares")
    take = torn if rc["mode"] == "scatter" else lambda p, w, own, u: p[picked(w, u)]
    if rc.get("mix") and len(pals) > 1:
        pool = np.concatenate(pals)  # (each set weighed by its colours: even shares = every colour alike)
        w = np.concatenate([weights(len(p), shares) * len(p) for p in pals])
        new = np.where(change, take(pool, w / w.sum(), low, pick), low)
        new = kept_apart(notes, low, new, np.unique(pool), pick)
    else:
        new = low.copy()
        for s, p in enumerate(pals):
            m = np.flatnonzero(which == s)
            if len(p) < 2 or not len(m):
                continue
            got = np.where(change[m], take(p, weights(len(p), shares), low[m], pick[m]), low[m])
            new[m] = kept_apart(notes[m], low[m], got, p, pick[m])
    if keep:
        return keep(notes, lap, None, (notes, at, low, new, which, pals, pick, box, own))
    return notes, lap + all_stirred(notes, at, low, new, rc, which, pals, pick, box, own)


def all_stirred(notes, at, low, now, rc, which, pals, pick, box, own):
    """now after every stir of rc in order, then its Warp, then its Liquify (applied last, user 11-07; Warp before
    it, user 11-09)."""
    now = stirs_only(notes, at, low, now, rc, which, pals, pick, box, own)
    now = warped_now(notes, at, low, now, rc, which, pals, pick, box, own)
    return liquified_now(notes, at, low, now, rc, which, pals, pick, box, own)


def warped_now(notes, at, low, now, rc, which, pals, pick, box, own):
    """now after rc's Warp (notes/warp.py), or as it is without one."""
    if not rc.get("warp"):
        return now
    from notes.warp import warped
    return warped(notes, at, low, now, rc, which, pals, pick, box, own)


def liquified_now(notes, at, low, now, rc, which, pals, pick, box, own):
    """now after rc's Liquify (notes/liquify.py), or as it is without one."""
    if not rc.get("liquify"):
        return now
    from notes.liquify import liquified
    return liquified(notes, at, low, now, rc, which, pals, pick, box, own)


def stirs_only(notes, at, low, now, rc, which, pals, pick, box, own):
    """now after every stir of rc in order (stacked stirs: each on the colours the one before left; a stir at its
    defaults skipped)."""
    st = rc.get("stir")
    for one in (st if isinstance(st, list) else [st] if st else []):
        if one:
            now = stirred(notes, at, low, now, dict(rc, stir=one), which, pals, pick, box, own)
    return now


def stirred(notes, at, low, now, rc, which, pals, pick, box, own):
    """Recolour's Stir: the colours now (each note's, without its round) bent round the stir's Centre point: each note
    looks at the note found where it comes from (stir_from, any colour set) and takes its colour; with more than
    one colour set (a user's feedback 2026-11-02: the pattern broke into patches when each note only looked at its
    own set's notes) Stir "sets" decides: "stay" (default) = the colour in the SAME PLACE of the note's own set (the
    pattern bends whole, each note keeps its set's tracks; a colour its set lacks = Move's), "move" = that note's colour itself (notes may go to
    another set). Per chord / run: its group's first note's. No colour is made or thrown: only the ones there go
    round. Two notes on a key sounding together then kept apart (kept_apart, low = the colours before Recolour). at
    = the notes' places (places_of)."""
    st = rc.get("stir")
    if not st or not len(at):
        return now
    full = full_stir(st)
    inside = area_inside(at, rc, box, own) if full["reach"] and (full["reverse"] or full["flip_in"] != "whole") \
        else None
    after = full["flip_of"] == "after" and (full["flip_side"] or full["flip_up"])
    if after:  # (the stirred pattern mirrored: each note looks where its mirrored spot looks; below, the flips work
        # on the notes' own spots instead of where the stir looks)
        mid = (np.asarray(at[:, 0], np.float64) + np.asarray(at[:, 1], np.float64)) / 2
        key = np.asarray(at[:, 2], np.float64)
    else:
        mid, key = stir_from(at, rc, box, own, colours=now)
    there = None
    if inside is not None and full["flip_in"] != "whole":  # (flips only inside / outside the area, tester 11-04)
        there = inside if full["flip_in"] == "inside" else ~inside
    # (Mirror across the area: only that part's notes are the outline the flips mirror at, so it flips in place;
    # sideways by Note order: each key row's notes swapped by their order, PLAN B / C)
    part = there if there is not None and full["mirror"] == "area" and there.any() else None
    order = full["flip_side"] and full["flip_by"] == "order"
    side, up, smooth = full["flip_side"] and not order, full["flip_up"], full["smooth"]
    if part is not None:  # (inside: across where the area and the notes overlap, row by row / time by time: the
        # inside notes' first start / last end jittered the line on notes of different lengths, 11-05; outside: each
        # piece beside the area across itself: across both pieces' outline folded / jumped, sweep 11-05)
        fm, fk = area_mirrored(at, mid, key, rc, box, own, side, up, smooth, full["flip_in"] == "outside")
    else:
        fm, fk = flipped(at, mid, key, side, up, smooth)
    if side or up:  # (a spot the MIRROR pushed past its row's end (or its time's lowest / highest key) bounces back
        # in, as a mirror would: else every note there took the row's last note = a flat patch, Chinese tester
        # 11-05. A spot already past it before the mirror stays: one stir flipping + turning = a flip stir, then a
        # turn stir, still)
        fm, fk = bounced(at, fm, fk, side, up, mid, key)
    if there is not None:
        fm, fk = np.where(there, fm, mid), np.where(there, fk, key)
    mid, key = stir_from(at, rc, box, own, (fm, fk), now) if after else (fm, fk)
    src = nearest(at, mid, key)
    if order:  # (before: the found note's twin's colour; after: the colour the note's twin found)
        twin = row_twins(at, now, part)
        got = src[twin] if after else twin[src]
        src = got if there is None else np.where(there, got, src)
    move = st.get("sets") == "move" or len(pals) < 2
    if move:
        new = now[src]
    else:  # (blocks of the channels' colours, 15 / 16 with channel 10: the note's own block, the found one's colour)
        per = rc.get("most", MOST)
        pool = np.concatenate(pals)
        set_of = np.repeat(np.arange(len(pals)), [len(p) for p in pals])
        o = np.argsort(pool, kind="stable")
        want = now // per * per + now[src] % per
        i = o[np.clip(np.searchsorted(pool[o], want), 0, len(pool) - 1)]
        # (a colour the note's block doesn't have: the found note's colour itself, as Move does; keeping its own
        # left a whole area unstirred, a tester's file 11-03: a 17th colour alone in the 2nd block)
        new = np.where(pool[i] == want, want, now[src])
    if rc.get("by"):
        new = new[leaders(at, rc["by"])]
    if inside is not None and full["reverse"] and inside.any():  # (tester 11-04: inside the area the tracks'
        # order turned round, each colour set on its own: its first colour <-> its last)
        new = new.copy()
        for p in pals:
            p = np.sort(np.asarray(p, np.int64))
            if len(p) < 2:
                continue
            j = np.clip(np.searchsorted(p, new), 0, len(p) - 1)
            m = inside & (p[j] == new)
            new[m] = p[::-1][j[m]]
    if move and len(pals) > 1:
        return kept_apart(notes, low, new, np.unique(np.concatenate(pals)), pick)
    if move:
        return kept_apart(notes, low, new, pals[0], pick) if len(pals[0]) >= 2 else new
    mine = set_of[o[np.clip(np.searchsorted(pool[o], new), 0, len(pool) - 1)]]  # (Per chord: the leader's set)
    for s, p in enumerate(pals):
        m = np.flatnonzero(mine == s)
        if len(p) >= 2 and len(m):
            new[m] = kept_apart(notes[m], low[m], new[m], p, pick[m])
    return new


def from_whole(notes, tracks, wn, wt, held=False, back=None):
    """A stirred piece's notes and tracks: settled as recoloured does (held: not), each note the colour of the
    whole's recoloured note (wn, wt) at its spot (back(notes): where each was before a flip): the whole's note on
    its key covering its middle (the one a cut sliver came from), else the nearest; its own round kept."""
    t = np.asarray(tracks, np.int64)
    if not held:
        notes, t = settled(notes, t)
    if not len(notes) or not len(wn) or wt is None:
        return notes, t
    at = back(notes) if back else notes
    mid = (np.asarray(at[:, 0], np.float64) + np.asarray(at[:, 1], np.float64)) / 2
    key = np.asarray(at[:, 2], np.int64)
    got = nearest(wn, mid, key, own=False)
    # (a sliver of a long note cut near its end: its middle may be nearer the next note's; the covering one wins)
    ws, we, wk = (np.asarray(wn[:, c], np.int64) for c in (0, 1, 2))
    o = np.lexsort((ws, wk))
    line = wk[o].astype(np.float64) * 2.0 ** 42 + ws[o]
    j = np.searchsorted(line, key.astype(np.float64) * 2.0 ** 42 + mid, side="right") - 1
    ok = j >= 0
    c = o[np.maximum(j, 0)]
    ok &= (wk[c] == key) & (we[c] > mid)
    got = np.where(ok, c, got)
    # (stacked notes on one key (Sustain, held notes) all cover the middle: the one the piece was cut from wins, by
    # the same start (a left half / an uncut note), else the same end + velocity (a right half); the covering one
    # starting last took another stacked note's colour, sweep 11-06)
    ps, pe = np.asarray(at[:, 0], np.int64), np.asarray(at[:, 1], np.int64)
    k64 = key.astype(np.int64) << 41
    line = (wk[o] << 41) + ws[o]
    j = np.minimum(np.searchsorted(line, k64 + ps), len(o) - 1)
    c = o[j]
    same_start = (line[j] == k64 + ps) & (we[c] >= pe)
    if np.asarray(wn).shape[1] > 3 and np.asarray(notes).shape[1] > 3:
        wv = np.asarray(wn[:, 3], np.int64)
        oe = np.lexsort((wv, we, wk))
        line_e = (((wk[oe] << 41) + we[oe]) << 7) + wv[oe]
        want = ((k64 + pe) << 7) + np.asarray(notes[:, 3], np.int64)
        je = np.minimum(np.searchsorted(line_e, want), len(oe) - 1)
        ce = oe[je]
        same_end = (line_e[je] == want) & (ws[ce] <= ps)
        got = np.where(same_end & ~same_start, ce, got)
    got = np.where(same_start, c, got)
    return notes, t - t % ROUND + np.asarray(wt, np.int64)[got] % ROUND


def unit_xy(mid, key, rc, box, own):
    """Places (note middle in ticks, key) -> (x, y) 0..1 in the colour box, as areas counts them: flipped / turned
    since (rc "frame") and a piece's whole's box (rc "whole") taken in."""
    f = rc.get("frame")
    turned = bool(f and f[1])
    t0, t1, k0, k1 = own if turned else box
    x = (np.asarray(mid, np.float64) - t0) / max(t1 - t0, 1.0)
    y = (np.asarray(key, np.float64) - k0) / (k1 - k0) if k1 > k0 else np.full(len(x), 0.5)
    if turned:
        x, y = 0.5 + f[1] * (y - 0.5), 0.5 + f[2] * (x - 0.5)
        (a0, a1), (b0, b1) = whole_shares(rc)
        x, y = (x - a0) / max(a1 - a0, 1e-12), (y - b0) / max(b1 - b0, 1e-12) if b1 > b0 else y
    return x, y


def from_unit(x, y, rc, box, own):
    """unit_xy's way back: (x, y) in the colour box -> (middle in ticks, key), both floats."""
    f = rc.get("frame")
    turned = bool(f and f[1])
    t0, t1, k0, k1 = own if turned else box
    if turned:
        (a0, a1), (b0, b1) = whole_shares(rc)
        x, y = a0 + x * (a1 - a0), b0 + y * (b1 - b0) if b1 > b0 else y
        x, y = 0.5 + f[2] * (y - 0.5), 0.5 + f[1] * (x - 0.5)  # (f[1], f[2] are +-1: each its own way back)
    return t0 + x * max(t1 - t0, 1.0), (k0 + y * (k1 - k0) if k1 > k0 else np.full(len(x), float(k0)))


def stir_from(at, rc, box, own, spots=None, colours=None):
    """(middle in ticks, key) each note's colour comes from under rc's Stir: its place in the colour box turned
    back round the Centre point by Turn + Twist (turns, most near the centre: Swirl's way), then (Squeeze on) the
    Centre point taken back to the middle (the colours' middle moved there: squeezed on one side, stretched on the
    other). spots = (middles, keys) worked out instead of the notes' own places (the notes still make the rows).
    colours: each note's colour now (Repeat the colours finds the rows' repeats in them)."""
    st = full_stir(rc.get("stir"))
    if spots is None:
        s, e, k = (np.asarray(at[:, c], np.float64) for c in (0, 1, 2))
        mid = (s + e) / 2
    else:
        mid, k = (np.asarray(v, np.float64) for v in spots)
    edge = st["edge"]
    rep = None
    if edge in WRAPS or edge == "bend":  # (Bend's rows with Smooth corners: an outline's corner rounds, no seam)
        rows = rows_of(at, st["bend_smooth"] if edge == "bend" else 0.0)
        t0, t1 = float(at[:, 0].min()), float(at[:, 1].max())
        if edge == "repeat" and colours is not None:
            rep = rows.repeat_of(at, colours)
        elif edge in BOUNCES and colours is not None and len(at):
            rep = rows.bounce_of(at, colours, edge == "bounce2")
    if edge == "bend":  # (each key row stretched to the notes' whole width: no empty spot left to come from)
        mid = rows.stretched(mid, k, t0, t1)
    x, y = unit_xy(mid, k, rc, box, own)
    cx, cy = (v / 100 for v in st["centre"])
    dx, dy = x - cx, y - cy
    turn = st["turn"] / 360
    reach = st["reach"]
    (kx, ky), (rx, ry) = area_scales(st, lambda u, v: from_unit(u / 100, v / 100, rc, box, own), rc.get("ppq"))
    fast = loops()
    if fast and len(x) and not (reach and st["area"] == DRAWN):  # (the compiled walk: the very same numbers,
        # files/fastloops.stir_spots; user 11-07: 3 M notes)
        fp, ip = stir_numbers(st, (cx, cy), (kx, ky), (rx, ry))
        x, y = np.ascontiguousarray(x, np.float64), np.ascontiguousarray(y, np.float64)
        ox, oy = np.empty_like(x), np.empty_like(y)
        fast.stir_spots(x, y, fp, ip, ox, oy)
        mid, key = from_unit(ox, oy, rc, box, own)
        if edge in WRAPS:
            mid, key = rows.wrapped(mid, key, rep, edge)
        elif edge == "bend":
            mid = rows.unstretched(mid, key, t0, t1)
        return mid, key
    if st["bulge"]:  # (a lens over everything below: each spot looks nearer the middle (bulge) / farther (pinch))
        x, y = bulged(x, y, st, (cx, cy), (kx, ky))
        dx, dy = x - cx, y - cy
    seen_x, seen_y = x, y  # (where the note is seen: the Area's fade for Turn / Twist / Squeeze is measured there)
    if bends(st):  # (Kaleidoscope, Ripple, Twist along a line, Straight to round: seen after Turn / Twist; they
        # send spots far (Straight to round: across the whole box), so the Area must not be measured after them)
        x, y = bent(x, y, st, (cx, cy), (kx, ky), (rx, ry))
        dx, dy = x - cx, y - cy
    whole_twist = st["twist"] and not (reach and st["area_twist"])
    if reach:  # (an Area: Twist winds out to its edge, everything fades smoothly to none there (Hard edge: cut
        # clean, tester 11-04); measured from the area's own spot when it has one. What the area limits is ticked:
        # an unticked part works on the whole shape)
        q = area_spot_q(seen_x, seen_y, st, (kx, ky))
        w = (q < 1).astype(np.float64) if st["hard"] else 1 - q * q * (3 - 2 * q)
        if st["area_turn"]:
            turn = turn * w
        if st["twist"] and st["area_twist"]:
            turn = turn + st["twist"] * np.sqrt(q) * w
    if whole_twist:
        far = max(np.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1))
        turn = turn + st["twist"] * np.sqrt(np.hypot(dx * rx, dy * ry) / max(far, 1e-12))
    a = -2 * np.pi * np.asarray(turn, np.float64)
    ca, sa = np.cos(a), np.sin(a)
    X, Y = dx * rx, dy * ry  # (spin: turned round as seen; else rx = ry = 1, the box's own way)
    x, y = cx + (X * ca - Y * sa) / rx, cy + (X * sa + Y * ca) / ry
    if edge in WRAPS:  # (left as it is: Rows.wrapped brings it back by the notes' own repeat, user 2026-11-10)
        pass
    elif edge != "escape":  # (what went out one side comes back in the other: the colours tiled, user 11-02)
        out = lambda v: (v < -1e-9) | (v > 1 + 1e-9)  # (on the edge, or a hair past it: stays on it)
        x = np.clip(np.where(out(x), np.mod(x, 1.0), x), 0, 1)
        y = np.clip(np.where(out(y), np.mod(y, 1.0), y), 0, 1)
    else:  # (escape: gone, the edge's colours stretch in)
        x, y = np.clip(x, 0, 1), np.clip(y, 0, 1)
    if st["squeeze"]:
        sx = np.where(x < cx, 0.5 * x / max(cx, 1e-12), 0.5 + 0.5 * (x - cx) / max(1 - cx, 1e-12))
        sy = np.where(y < cy, 0.5 * y / max(cy, 1e-12), 0.5 + 0.5 * (y - cy) / max(1 - cy, 1e-12))
        if reach and st["area_squeeze"]:  # (the squeeze fades with the circle too: outside it nothing moves)
            sx, sy = x + (sx - x) * w, y + (sy - y) * w
        x, y = sx, sy
    mid, key = from_unit(x, y, rc, box, own)
    if edge in WRAPS:  # (a spot past the key rows / its key row's notes comes back in on the other side, every
        # whole number of key rows / the row's repeat length: a whole number of colour turns lines up; Bounce back:
        # bounced off the ends instead)
        mid, key = rows.wrapped(mid, key, rep, edge)
    elif edge == "bend":
        mid = rows.unstretched(mid, key, t0, t1)
    return mid, key


def stir_numbers(st, c, k, r):
    """Stir st's numbers for the compiled walk (files/fastloops.stir_spots): (floats, whole numbers) in its order,
    every one worked out here exactly as stir_from / bulged / bent / area_spot_q work it out (the same Python / NumPy
    scalars, so the notes come out the same to the last bit)."""
    (cx, cy), (kx, ky), (rx, ry) = c, k, r
    reach = st["reach"]
    area = reach is not None
    turn = st["turn"] / 360
    whole_twist = bool(st["twist"] and not (reach and st["area_twist"]))
    per_note = bool((reach and st["area_turn"]) or (reach and st["twist"] and st["area_twist"]) or whole_twist)
    a0 = -2 * np.pi * np.asarray(turn, np.float64)
    ca0, sa0 = float(np.cos(a0)), float(np.sin(a0))
    far_twist = float(max(np.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1)))
    am = tuple(v / 100 for v in (st["spot"] or st["centre"]))

    def far(m):
        return max(math.hypot((X - m[0]) * rx, (Y - m[1]) * ry) for X in (0, 1) for Y in (0, 1)) or 1.0
    f = [0.0] * 32
    f[0:9] = [cx, cy, rx, ry, turn, st["twist"], ca0, sa0, far_twist]
    if area:
        tilt = math.radians(st["tilt"]) if st["tilt"] else 0.0
        f[9:19] = [am[0], am[1], kx, ky, reach / 200 * size_scale(st, k), math.cos(tilt), math.sin(tilt),
                   st["skew"], st["stretch"] / 100, st["stretch_up"] / 100]
    m = bulge_size(st["bulge"]) if st["bulge"] else 1.0
    f[19:21] = [m, m - 1]
    if st["kaleido"] >= 2:
        wedge = 2 * np.pi / st["kaleido"]
        f[21:24] = [math.radians(st["kaleido_turn"]), wedge, wedge / 2]
    f[24:26] = [st["ripple"] / 100, st["ripple_len"] / 100]
    wa = math.radians(st["wring_angle"])
    f[26:29] = [math.cos(wa), math.sin(wa), 2 * np.pi * st["wring"]]
    f[29:32] = [st["polar"] / 100, far((cx, cy)), far(am)]
    kinds = {"square": 1, "diamond": 2}
    i = [int(area), int(st["hard"]), kinds.get(st["area"], 0), int(bool(st["tilt"])), int(bool(st["skew"])),
         int(st["area_turn"]), int(st["area_twist"]), int(st["area_squeeze"]), int(st["area_bulge"]),
         int(bool(st["bulge"])), int(st["kaleido"] >= 2),
         ("side", "up", "rings").index(st["ripple_kind"]) if st["ripple"] else -1, int(bool(st["wring"])),
         int(bool(st["polar"])), int(st["area_kaleido"]), int(st["area_ripple"]), int(st["area_wring"]),
         int(st["area_polar"]), int(st["round_edge"] == "cut"), int(whole_twist), int(per_note),
         {"escape": 1, "wrap": 2, "repeat": 2, "bounce": 2, "bounce2": 2}.get(st["edge"], 0),
         int(st["squeeze"])]  # (2: neither tiled nor clipped)
    return np.array(f, np.float64), np.array(i, np.int64)


def bulge_size(b):
    """Bulge % -> how many times bigger the very middle looks: +100 % = 2, +200 % = 3; -100 % = 1 / 2 (pinched);
    0 = 1 (nothing moves)."""
    m = 1 + abs(b) / 100
    return m if b > 0 else 1 / m


def bulged(x, y, st, c, k):
    """Spots (x, y) in the colour box -> where stir st's Bulge (filled in, not 0) has them look: along their own ray
    from the middle, distance q (0..1 of the reach) -> q / (m - (m - 1) q) (a lens: the middle m times as big, the
    edge 1 / m, smooth between), so the reach's outline stays and every colour inside it stays (only squeezed /
    stretched). The Area (ticked "area_bulge"): round its own middle, any area shape, nothing outside moves; else
    the whole shape round the Centre point c, inside the oval touching the colour box's four sides (round like a
    ball; the box's edge squared the look with creases; never a colour from past the box; the corners stay)."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    m = bulge_size(st["bulge"])
    if st["reach"] and st["area_bulge"]:
        mx, my = (v / 100 for v in (st["spot"] or st["centre"]))
        q = area_spot_q(x, y, st, k)
    else:
        mx, my = c
        dx, dy = x - mx, y - my
        q = np.hypot(np.where(dx > 0, dx / max(1 - mx, 1e-12), -dx / max(mx, 1e-12)),
                     np.where(dy > 0, dy / max(1 - my, 1e-12), -dy / max(my, 1e-12)))
    ok = (q < 1) & (q > 1e-12)
    g = np.ones_like(q)
    g[ok] = 1 / (m - (m - 1) * q[ok])  # (the new distance / the old)
    return mx + (x - mx) * g, my + (y - my) * g


def bends(st):
    """Whether stir st (filled in) has one of the more bends on."""
    return bool(st["polar"] or st["ripple"] or st["kaleido"] >= 2 or st["wring"])


def flip_of_matters(st):
    """Whether Flip what ("flip_of") can change stir st (filled in): it has a flip and moves where colours come from
    (stir_from: Turn, Twist, Bulge, a more bend, a Centre point off the middle with Squeeze on), or flips sideways by
    Note order AND upside down (the two flips' order then differs, stirred_colours). Else before = after, so the
    window greys it (user 11-09: a tester switched it on a stir with nothing to bend and saw no change)."""
    if not (st["flip_side"] or st["flip_up"]):
        return False
    return bool(st["turn"] or st["twist"] or st["bulge"] or bends(st)
                or (st["squeeze"] and list(st["centre"]) != [50.0, 50.0])
                or (st["flip_side"] and st["flip_up"] and st["flip_by"] == "order"))


def bent(x, y, st, c, k, r):
    """Spots (x, y) in the colour box -> where stir st's more bends have them look, worked backwards from how they
    are seen (Kaleidoscope on top, then Ripple, Twist along a line, Straight to round). Each goes round the Centre
    point c (its Area tick: round the area's own middle, only inside it: the soft fade, Hard edge cut; Straight to
    round + Kaleidoscope cut clean unless round_edge "fade"). Measured as seen with r = (rx, ry) (Turn and twist
    round as seen), k = the area's (kx, ky)."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    rx, ry = r
    area = st["reach"] is not None
    if area:
        q = area_spot_q(x, y, st, k)
        hard = (q < 1).astype(np.float64)
        soft = hard if st["hard"] else 1 - q * q * (3 - 2 * q)
        am = tuple(v / 100 for v in (st["spot"] or st["centre"]))

    def where(key, rnd=False):
        """(middle, weight: None = everywhere) of one bend."""
        if area and st["area_" + key]:
            return am, (hard if rnd and st["round_edge"] == "cut" else soft)
        return c, None

    def seen(m):
        return (x - m[0]) * rx, (y - m[1]) * ry

    def put(m, X, Y, w, share=1.0):
        nx, ny = m[0] + X / rx, m[1] + Y / ry
        f = share if w is None else w * share
        return x + (nx - x) * f, y + (ny - y) * f

    def far(m):
        return max(math.hypot((X - m[0]) * rx, (Y - m[1]) * ry) for X in (0, 1) for Y in (0, 1)) or 1.0

    if st["kaleido"] >= 2:  # (one slice mirrored round the middle: every other half-slice turned over)
        m, w = where("kaleido", True)
        X, Y = seen(m)
        t = math.radians(st["kaleido_turn"])
        wedge = 2 * np.pi / st["kaleido"]
        a = np.mod(np.arctan2(Y, X) - t, wedge)
        a = np.where(a > wedge / 2, wedge - a, a) + t
        R = np.hypot(X, Y)
        x, y = put(m, R * np.cos(a), R * np.sin(a), w)
    if st["ripple"]:  # (shifted along a wave: sideways stripes wave, up / down, or rings out from the middle)
        m, w = where("ripple")
        X, Y = seen(m)
        h, L = st["ripple"] / 100, st["ripple_len"] / 100
        kind = st["ripple_kind"]
        if kind == "side":
            X = X + h * np.sin(2 * np.pi * Y / L)
        elif kind == "up":
            Y = Y + h * np.sin(2 * np.pi * X / L)
        else:
            R = np.hypot(X, Y)
            g = np.where(R > 1e-12, (R + h * np.sin(2 * np.pi * R / L)) / np.maximum(R, 1e-12), 1.0)
            X, Y = X * g, Y * g
        x, y = put(m, X, Y, w)
    if st["wring"]:  # (a ribbon wrung along a line through the middle: the farther along, the more it turns)
        m, w = where("wring")
        X, Y = seen(m)
        a = math.radians(st["wring_angle"])
        ca, sa = math.cos(a), math.sin(a)
        u, v = X * ca + Y * sa, Y * ca - X * sa
        phi = 2 * np.pi * st["wring"] * u / far(m)
        u, v = u + v * np.sin(phi), v * np.cos(phi)
        x, y = put(m, u * ca - v * sa, u * sa + v * ca, w)
    if st["polar"]:  # (angle -> across the box, distance -> up: straight stripes become rays / rings)
        m, w = where("polar", True)
        X, Y = seen(m)
        across = (np.arctan2(Y, X) + np.pi) / (2 * np.pi)
        up = area_spot_q(x, y, st, k) if w is not None else np.hypot(X, Y) / far(m)
        f = st["polar"] / 100 * (1.0 if w is None else w)
        x, y = x + (across - x) * f, y + (up - y) * f
    return x, y


def area_spot_q(x, y, st, k):
    """area_q of spots (x, y) in the colour box for stir st (filled in, with a reach), from its own spot or Centre
    point: 0 in its middle, 1 on the outline and past it."""
    ax, ay = (v / 100 for v in (st["spot"] or st["centre"]))
    # (rounded: a note right on the outline is out on both sides, not in on one by a float hair, hunt 11-04 2)
    return np.clip(np.round(area_q(*area_local(x - ax, y - ay, st, k), st), 9), 0.0, 1.0)


def area_inside(at, rc, box, own):
    """Which notes of at sit inside rc's Stir area (where they are, before any stir; the area line as the piano
    roll draws it), or None (no area)."""
    st = full_stir(rc.get("stir"))
    if not st["reach"]:
        return None
    s, e, k = (np.asarray(at[:, c], np.float64) for c in (0, 1, 2))
    x, y = unit_xy((s + e) / 2, k, rc, box, own)
    kxy = area_scales(st, lambda u, v: from_unit(u / 100, v / 100, rc, box, own), rc.get("ppq"))[0]
    return area_spot_q(x, y, st, kxy) < 1


def area_dist(dx, dy, area):
    """How far (dx, dy) is from the Centre point, measured the Stir area's way: its outline at distance 1 is a
    circle of radius 1 / a square of half-side 1 / a diamond with its tips 1 away."""
    if area == "square":
        return np.maximum(np.abs(dx), np.abs(dy))
    if area == "diamond":
        return np.abs(dx) + np.abs(dy)
    return np.hypot(dx, dy)


def area_scales(st, spot, ppq):
    """((kx, ky), (rx, ry)) for one stir (filled in): the colour box's across / up distances times these = the
    area's own measure before its Angle / slant / Stretch (area_local), and the turn's (spin). spot(u, v) = (tick, key) of % of
    the colour box (numpy arrays). Without "seen" (or the PPQ): the box's own way, 1 / 1."""
    c = st["seen"]
    if not c or not ppq or not (st["round"] or st["spin"]):
        return (1.0, 1.0), (1.0, 1.0)
    t, k = spot(np.array([0.0, 100.0, 0.0]), np.array([0.0, 0.0, 100.0]))
    lx = math.hypot((t[1] - t[0]) / ppq, (k[1] - k[0]) * c)  # (the box's sides as seen, in beats)
    ly = math.hypot((t[2] - t[0]) / ppq, (k[2] - k[0]) * c)
    big = max(lx, ly, 1e-12)
    lx, ly = max(lx, 1e-9 * big) / big, max(ly, 1e-9 * big) / big  # (a flat box: never 0)
    area = (lx, ly) if st["round"] else (1.0, 1.0)
    return area, ((lx, ly) if st["spin"] else (1.0, 1.0))


def area_local(dx, dy, st, k):
    """(dx, dy) from the area's middle (the colour box 0..1 across / up) -> the area's own (X, Y) (its outline at
    about 1: area_q): k = area_scales' (kx, ky), then the area's Angle (tilt) and slant (skew) undone, Stretch
    along its own width and Stretch up along its height (PLAN C2b; tilt 0 / skew 0 = exactly the old way)."""
    r = st["reach"] / 200 * size_scale(st, k)
    X, Y = np.asarray(dx, np.float64) * k[0] / r, np.asarray(dy, np.float64) * k[1] / r
    if st["tilt"]:
        a = math.radians(st["tilt"])
        X, Y = X * math.cos(a) + Y * math.sin(a), Y * math.cos(a) - X * math.sin(a)
    if st["skew"]:
        X = X - st["skew"] * Y
    return X / (st["stretch"] / 100), Y / (st["stretch_up"] / 100)


def area_global(X, Y, st, k):
    """area_local's way back: the area's own (X, Y) -> (dx, dy) from its middle, the colour box 0..1."""
    X, Y = np.asarray(X, np.float64) * (st["stretch"] / 100), np.asarray(Y, np.float64) * (st["stretch_up"] / 100)
    if st["skew"]:
        X = X + st["skew"] * Y
    if st["tilt"]:
        a = math.radians(st["tilt"])
        X, Y = X * math.cos(a) - Y * math.sin(a), X * math.sin(a) + Y * math.cos(a)
    r = st["reach"] / 200 * size_scale(st, k)
    return X * r / k[0], Y * r / k[1]


MOST_SIZE_PARTS = 8  # (a step's size_mix list: [share, weight] parts, at most this many)


def clean_size_mix(v):
    """A saved size_mix: an old step's share (a number -1 < v < 1) or a step's list of [share -1..1, weight 0..1]
    parts (blend_sizes), or None."""
    if number(v):
        return round(float(v), 6) if -1 < round(v, 6) < 1 else None
    if isinstance(v, list) and 0 < len(v) <= MOST_SIZE_PARTS and all(
            isinstance(p, list) and len(p) == 2 and number(p[0]) and number(p[1]) and -1 <= p[0] <= 1
            and 0 < p[1] <= 1 for p in v):
        return [[round(float(a), 6), round(float(w), 9)] for a, w in v]
    return None


def size_parts(st):
    """Stir st's Size counting as [share, weight] parts (one part [share, 1] for a single kind)."""
    v = st.get("size_mix")
    if isinstance(v, list):
        return [list(p) for p in v]
    return [[SIZE_SHARE.get(st["size_of"], 0.0) if v is None else v, 1.0]]


def blend_sizes(a, b, reach, f):
    """size_mix for an Add between step f of the way from stir a to stir b (both with a reach) whose Size number
    is reach: the area's REAL size (Size x what 100 % is, at the step's own box) blends straight from a's to b's,
    so two ends of the same real size keep it on every step (hunt 11-06: blending the Size number and the counting
    apart swelled the middle steps up to 2.5x). Each end's parts weighted by its share of the real size; equal
    shares added up. A single part of weight 1 = that share as a plain number."""
    got = {}
    for s, w in ((a, (1 - f) * a["reach"] / reach), (b, f * b["reach"] / reach)):
        for share, part in size_parts(s):
            if part * w > 0:
                got[share] = got.get(share, 0.0) + part * w
    total = sum(got.values()) or 1.0
    parts = sorted([sh, w / total] for sh, w in got.items())  # (rounding of the Size number: weights add up to 1)
    if len(parts) == 1:
        return parts[0][0]
    return parts[:MOST_SIZE_PARTS]


def size_scale(st, k):
    """What 100 % Size is, in the area's measure (k = area_scales' (kx, ky)): the box's longer side (1), or with Size
    of "the circle inside" the box's shorter one (tester 11-04: "100 % = the diameter of a circle inside the
    shape"; only differs with Round as seen: without it the box counts as square), or with "the circle around" the
    box's corner to corner (user 11-05; also without Round as seen: the square's diagonal); an Add between step's
    size_mix = part way between two of them (SIZE_SHARE: 0..1 box to inside, 0..-1 box to around)."""
    short = min(k) / max(max(k), 1e-12)
    out = 0.0
    for share, w in size_parts(st):  # (a step's list: each part's counting, weighted: blend_sizes)
        out += w * (1.0 + (short - 1.0) * share if share >= 0 else 1.0 + (math.hypot(1.0, short) - 1.0) * -share)
    return out


def most_reach(st, k):
    """The largest Size for stir st (filled in, k = area_scales' (kx, ky)), the ONE rule for the window and the
    piano roll's drags: 200 % of the box however Size is counted (user 11-06; counting by the circle inside = more
    than 200, up to MOST_REACH_INSIDE); the circle around never under 100 (= through the box's corners, user 11-05:
    the roll's drags stopped at 70.7 %, hunt 11-06)."""
    scale = size_scale(st, k)
    return min(max(MOST_REACH / scale, 100.0), MOST_REACH_INSIDE)


def area_aspect(st):
    """The area's width over its height before Stretch: a drawing's as drawn, else 1."""
    return st["shape"]["aspect"] if st["area"] == DRAWN and st["shape"] else 1.0


def area_corners(st, k):
    """The area's box (filled in), as (dx, dy) from its middle in the colour box 0..1: its corners going round
    from (-, -), (+, -), (+, +), (-, +) of its own (X, Y)."""
    h = 1 / area_aspect(st)
    return area_global(np.array([-1.0, 1.0, 1.0, -1.0]), np.array([-h, -h, h, h]), st, k)


def area_from_box(st, p0, pu, pv, k, keep="size"):
    """An area's settings for a box dragged on the piano roll (PLAN C2b: its corners (-, -), (+, -), (-, +) in % of
    the colour box): {"middle" [% across, % up], "reach", "stretch", "stretch_up", "tilt", "skew"}, or None (a box
    turned inside out / flat). keep "size" = st's Size stays, both Stretches take the box (user 11-06: a side
    dragged = its own stretch); "stretch" = st's Stretch up stays, Size takes the box's height (Ctrl on a corner,
    an area made from a box)."""
    p0, pu, pv = (np.asarray(p, np.float64) / 100 for p in (p0, pu, pv))
    c1, c2 = (pu - p0) / 2 * k, (pv - p0) / 2 * k  # (half its width / height, measured the area's way)
    tilt = math.atan2(c1[1], c1[0])
    cw, sw = math.cos(tilt), math.sin(tilt)
    wx, wy = c2[0] * cw + c2[1] * sw, c2[1] * cw - c2[0] * sw
    h = wy * area_aspect(st)  # (its height, in Size's measure before Stretch up)
    if h <= 1e-9:
        return None
    scale = size_scale(st, k)
    r = st["reach"] / 200 * scale if keep == "size" else h / (st["stretch_up"] / 100)
    middle = (pu + pv) / 2 * 100
    return {"middle": [float(v) for v in middle], "reach": 200 * r / scale,
            "stretch": 100 * math.hypot(*c1) / r, "stretch_up": 100 * h / r,
            "tilt": math.degrees(tilt), "skew": wx / wy}


def fit_reach(got, most):
    """area_from_box's got (Size kept) with Size moved just enough that both Stretches fit LEAST..MOST_STRETCH,
    within LEAST_REACH..most: the area stays the same on screen (hunt 11-06: Round as seen ticked by a turn press on
    a long shape needed Stretch up 5.6 %, clamped to 10 = the area grew at the press). Stretches clamped only when
    no Size can hold them (a box over 100x longer than tall)."""
    r, sx, sy = got["reach"], got["stretch"], got["stretch_up"]
    lo, hi = max(sx, sy) * r / MOST_STRETCH, min(sx, sy) * r / LEAST_STRETCH
    r2 = min(max(min(max(r, lo), hi) if lo <= hi else r, LEAST_REACH), most)
    clamp = lambda v: min(max(v * r / r2, LEAST_STRETCH), MOST_STRETCH)
    return dict(got, reach=r2, stretch=clamp(sx), stretch_up=clamp(sy))


def area_outline(area, n):
    """The Stir area's outline at distance 1 (area_dist), closed, as (dx, dy) arrays: n + 1 points round a circle,
    only the corners for a square / diamond (straight sides)."""
    if area == "square":
        return np.array([1.0, -1.0, -1.0, 1.0, 1.0]), np.array([1.0, 1.0, -1.0, -1.0, 1.0])
    if area == "diamond":
        return np.array([1.0, 0.0, -1.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0, -1.0, 0.0])
    a = np.linspace(0, 2 * np.pi, n + 1)
    return np.cos(a), np.sin(a)


def area_outlines(st, n):
    """area_outline for one stir (filled in), as a list of closed (dx, dy) lines: a drawn area has one per loop."""
    if st["area"] != DRAWN or not st["shape"]:
        return [area_outline(st["area"], n)]
    return [(lp[:, 0].copy(), lp[:, 1].copy()) for lp in drawn_loops(st["shape"], True)]


def area_q(X, Y, st):
    """How far (X, Y) (the area's own measure: its outline at about 1, area_dist's) is from the area's middle out
    to its edge: 0 in the middle, 1 on the outline and past it."""
    if st["area"] == DRAWN and st["shape"]:
        return drawn_q(X, Y, st["shape"])
    return area_dist(X, Y, st["area"])


def drawn_loops(shape, closed=False):
    """A drawn area's loops in its own measure: its box 2 wide (-1..1 across), as tall as drawn, middle at 0, 0;
    closed = each ending on its first point."""
    a = shape["aspect"]
    out = []
    for lp in shape["loops"]:
        p = np.asarray(lp, np.float64)
        p = np.column_stack((2 * p[:, 0] - 1, (2 * p[:, 1] - 1) / a))
        if closed and (p[0] != p[-1]).any():
            p = np.vstack((p, p[:1]))
        out.append(p)
    return out


_FIELDS = {}  # a drawn area's outline (json) -> (grid corner, step, signed distances): worked out once per shape
_FIELD_IDS = {}  # id -> (the clean shape, its field): no json each make


def drawn_field(shape):
    """(lo, step, q): q[j, i] = area_q of the spot lo + (i, j) * step in a drawn area, measured along the ray from
    its middle (like the mock pictures the user picked: a star's arms stir out to their tips), 1 outside (inside =
    odd crossings, like Overlaps cancel out)."""
    got = _FIELD_IDS.get(id(shape))
    if got is not None and got[0] is shape:
        return got[1]
    key = json.dumps([shape["loops"], shape["aspect"]])
    got = _FIELDS.get(key)
    if got is not None:
        _FIELD_IDS[id(shape)] = (shape, got)
        return got
    loops = drawn_loops(shape, True)
    a = np.concatenate([lp[:-1] for lp in loops])
    b = np.concatenate([lp[1:] for lp in loops])
    lo, hi = np.minimum(a.min(0), b.min(0)), np.maximum(a.max(0), b.max(0))
    pad = 0.02 * max(hi - lo) + 1e-6
    lo, hi = lo - pad, hi + pad
    step = max(hi - lo) / (FIELD_STEPS - 1)
    nx, ny = (int(math.ceil((hi[k] - lo[k]) / step)) + 1 for k in (0, 1))
    xs, ys = lo[0] + np.arange(nx) * step, lo[1] + np.arange(ny) * step
    gx, gy = np.meshgrid(xs, ys)
    px, py = gx.ravel(), gy.ravel()
    m = np.hypot(px, py)
    ux, uy = px / np.maximum(m, 1e-12), py / np.maximum(m, 1e-12)
    ux[m < 1e-12] = 1.0  # (the middle itself: any way out)
    s_in = np.zeros(len(px))  # (the outline's last crossing before the spot on the ray from the middle; 0 = none)
    s_out = np.full(len(px), np.inf)  # (its first crossing past the spot)
    beyond = np.zeros(len(px), np.int64)  # (crossings past the spot: odd = inside)
    a, b = field_segments(loops)  # (from fewer pieces: a busy outline's 5,000 points took 5.6 s)
    e = b - a
    for k in range(0, len(a), 64):  # (pieces in chunks: the grid times 64 at a time)
        ax, ay, ex, ey = a[k:k + 64, 0], a[k:k + 64, 1], e[k:k + 64, 0], e[k:k + 64, 1]
        den = ux[:, None] * ey - uy[:, None] * ex
        ok = np.abs(den) > 1e-15
        den = np.where(ok, den, 1.0)
        s = (ax * ey - ay * ex) / den  # (how far along the ray the piece is crossed)
        u = (ax * uy[:, None] - ay * ux[:, None]) / den  # (where on the piece: 0..1, its end left to the next)
        hit = ok & (u >= 0) & (u < 1) & (s > 0)
        past = hit & (s > m[:, None])
        beyond += past.sum(1)
        s_out = np.minimum(s_out, np.where(past, s, np.inf).min(1))
        s_in = np.maximum(s_in, np.where(hit & ~past, s, 0.0).max(1))
    inside = beyond % 2 == 1
    # (the piece of the ray the spot is on: from the middle = 0 there, 1 at the outline, exactly a circle's /
    # square's own measure on such shapes; a piece away from the middle (a frame round a hole) = 0 halfway along
    # it, 1 at both its ends, so no seam at the hole)
    t = (m - s_in) / np.maximum(s_out - s_in, 1e-12)
    q = np.where(s_in > 0, np.abs(2 * t - 1), m / np.maximum(s_out, 1e-12))
    got = (lo, step, np.where(inside & np.isfinite(s_out), np.clip(q, 0.0, 1.0), 1.0).reshape(ny, nx))
    while len(_FIELDS) > 16:
        _FIELDS.pop(next(iter(_FIELDS)))
    _FIELDS[key] = got
    while len(_FIELD_IDS) > 256:
        _FIELD_IDS.pop(next(iter(_FIELD_IDS)))
    _FIELD_IDS[id(shape)] = (shape, got)
    return got


def field_segments(loops):
    """(starts, ends) of a drawn area's outline pieces for its distances: at most FIELD_PIECES in all (each loop
    keeps every k-th point, its share; at least 8 pieces), enough for a fade."""
    total = sum(len(lp) - 1 for lp in loops)
    k = max(1, -(-total // FIELD_PIECES))
    a, b = [], []
    for lp in loops:
        n = len(lp) - 1
        step = min(k, max(1, n // 8))
        p = lp[:-1:step]
        p = np.vstack((p, lp[-1:]))  # (closed again on its first point)
        a.append(p[:-1])
        b.append(p[1:])
    return np.concatenate(a), np.concatenate(b)


def drawn_q(X, Y, shape):
    """area_q of a drawn area (drawn_field, read between its spots): 0 in the middle, 1 on the outline and
    outside."""
    lo, step, qs = drawn_field(shape)
    X, Y = np.asarray(X, np.float64), np.asarray(Y, np.float64)
    ny, nx = qs.shape
    fx, fy = (X - lo[0]) / step, (Y - lo[1]) / step
    inb = (fx >= 0) & (fx <= nx - 1) & (fy >= 0) & (fy <= ny - 1)
    i = np.clip(np.floor(fx), 0, nx - 2).astype(np.int64)
    j = np.clip(np.floor(fy), 0, ny - 2).astype(np.int64)
    tx, ty = np.clip(fx - i, 0.0, 1.0), np.clip(fy - j, 0.0, 1.0)
    q = (qs[j, i] * (1 - tx) * (1 - ty) + qs[j, i + 1] * tx * (1 - ty) + qs[j + 1, i] * (1 - tx) * ty +
         qs[j + 1, i + 1] * tx * ty)
    return np.where(inb, q, 1.0)


def drawn_gauge(X, Y, shape):
    """How big a drawn area must be (its Size over this one) for its outline to pass (X, Y): the spot's distance
    from the middle over the outline's farthest crossing in that direction (the area line's drag)."""
    r = math.hypot(X, Y)
    if r < 1e-12:
        return 0.0
    dx, dy = X / r, Y / r
    best = 0.0
    for p in drawn_loops(shape, True):
        a, b = p[:-1], p[1:]
        ex, ey = b[:, 0] - a[:, 0], b[:, 1] - a[:, 1]
        den = dx * ey - dy * ex
        ok = np.abs(den) > 1e-12
        den = np.where(ok, den, 1.0)
        s = (a[:, 0] * ey - a[:, 1] * ex) / den
        u = (a[:, 0] * dy - a[:, 1] * dx) / den
        hit = ok & (s > 0) & (u >= -1e-9) & (u <= 1 + 1e-9)
        if hit.any():
            best = max(best, float(s[hit].max()))
    if best <= 0:  # (no crossing that way: the farthest point of the outline)
        best = max(float(np.hypot(p[:, 0], p[:, 1]).max()) for p in drawn_loops(shape))
    return r / best


def flipped(at, mid, key, side, up, smooth=0.0):
    """Stir's colour flips: spots (mid, key) mirrored at the notes' own outline, sideways across their key row's
    notes (first start <-> last end), upside down across the notes around their time (lowest <-> highest key), so
    a round or slanted shape flips into itself, never into empty space. Done on where the stir looks = the colours
    flipped first, then bent: the swirl stays at the Centre point. smooth: Smooth corners, %."""
    if side:
        mid = Rows(at, smooth).mirrored(mid, key)
    if up:
        key = Cols(at, smooth).mirrored(mid, key)
    return mid, key


def bounce(v, lo, hi):
    """v folded back into lo..hi at its ends, as a mirror would (lo..hi of no width: lo)."""
    v, lo, hi = (np.asarray(x, np.float64) for x in (v, lo, hi))
    w = np.maximum(hi - lo, 0.0)
    u = np.mod(v - lo, np.maximum(2 * w, 1e-12))
    return np.where(w > 0, lo + np.where(u > w, 2 * w - u, u), lo)


def bounced(at, mid, key, side, up, mid0, key0):
    """Mirrored spots (mid, key) kept on the notes: sideways, a middle past its key row's first / last note's middle
    bounces back in; upside down, a key past the lowest / highest key around its time likewise. Only where the
    spot before the mirror (mid0, key0) was on the notes that way; spots on the notes never move."""
    if side:
        rows = Rows(at)
        g = rows.g  # (its first / last middle, not its ends)
        r0 = rows.row(key0)
        was = (mid0 >= rows.a[r0] + g[r0] - 1e-9) & (mid0 <= rows.b[r0] - g[r0] + 1e-9)
        r = rows.row(key)
        mid = np.where(was, bounce(mid, rows.a[r] + g[r], rows.b[r] - g[r]), mid)
    if up:
        cols = Cols(at)
        s0 = cols.slice(mid0)
        was = (key0 >= cols.lo[s0] - 1e-9) & (key0 <= cols.hi[s0] + 1e-9)
        s = cols.slice(mid)
        key = np.where(was, bounce(key, cols.lo[s], cols.hi[s]), key)
    return mid, key


_CHORDS = OrderedDict()  # (Mirror across only that part: the area's ends on each row / time, kept per area + notes)


def chord_ends(test, a, b, n):
    """For each line j from a[j] to b[j] (time along a key row, or keys at one time): the first and the last spot
    where test(spots, lines) is True, each end found exactly (halved 30 times between the last spot in and the next
    one out; inside right up to a line's end = that end); NaN on a line where it never is. n spots tried a line."""
    m = len(a)
    u = np.linspace(0.0, 1.0, n)
    P = np.asarray(a, np.float64)[:, None] + (np.asarray(b, np.float64) - a)[:, None] * u[None, :]
    ins = test(P.ravel(), np.repeat(np.arange(m), n)).reshape(m, n)
    has = ins.any(axis=1)
    lines = np.arange(m)
    out = []
    for idx, step in ((np.argmax(ins, axis=1), -1), (n - 1 - np.argmax(ins[:, ::-1], axis=1), 1)):
        pin = P[lines, idx]
        pout = P[lines, np.clip(idx + step, 0, n - 1)]  # (at a line's end: the same spot, it stays)
        for _ in range(30):
            mm = (pin + pout) / 2
            got = test(mm, lines)
            pin, pout = np.where(got, mm, pin), np.where(got, pout, mm)
        out.append(np.where(has, pin, np.nan))
    return out


def area_mirrored(at, mid, key, rc, box, own, side, up, smooth=0.0, outside=False):
    """Spots (mid, key) mirrored across where the Stir area and the notes overlap (PLAN C; hunt 11-05): sideways
    along the spot's key row, from where the area starts on that row (never before the row's first note) to where it
    ends (never past its last); upside down along the keys at the spot's time (within the notes there, Cols' slices);
    sideways first. Worked out as the piano roll shows the notes, so a turned shape flips the way it looks; each
    row's own ends, so a turned or slanted area's line is straight. Kept per area + notes: Turn / Twist changing
    don't work it out again. A spot on a row / time the area doesn't reach stays. smooth: Smooth corners, % of the
    height (width upside down): where the shape's edge cuts the area off, the ends turned sharply = a straight seam
    (Chinese tester 11-05). outside: the notes beside the area instead, each piece across itself (a row's piece
    before the area: its first note's start <-> the area's start; after it: the area's end <-> its last note's end;
    a row / time the area doesn't reach: across all of it), the piece picked by which side of the area's middle
    the spot is."""
    st = full_stir(rc.get("stir"))
    kxy = area_scales(st, lambda u, v: from_unit(u / 100, v / 100, rc, box, own), rc.get("ppq"))[0]

    def inside(t, k):
        x, y = unit_xy(np.asarray(t, np.float64), np.asarray(k, np.float64), rc, box, own)
        return area_spot_q(np.asarray(x, np.float64), np.asarray(y, np.float64), st, kxy) < 1

    sig = (st["reach"], st["area"], id(st["shape"]), st["stretch"], st["stretch_up"], st["tilt"], st["skew"],
           tuple(st["spot"] or st["centre"]), st["round"], st["seen"], st["size_of"], json.dumps(st.get("size_mix")),
           tuple(float(v) for v in box), tuple(float(v) for v in own), json.dumps(rc.get("frame")),
           json.dumps(rc.get("whole")), rc.get("ppq"))
    mid, key = np.array(mid, np.float64), np.array(key, np.float64)
    if side:
        rows = Rows(at)
        ks = rows.keys.astype(np.float64)
        lo, hi = _chords(("rows", sig, rows.keys.tobytes(), rows.a.tobytes(), rows.b.tobytes()),
                         lambda: chord_ends(lambda p, j: inside(p, ks[j]), rows.a, rows.b, 256))
        los, his = lo, hi
        if smooth > 0 and len(ks) > 2:
            reach = smooth / 100 * float(ks[-1] - ks[0]) / 2
            los, his = smooth_ends(ks, lo, reach), smooth_ends(ks, hi, reach)
        r = rows.row(key)
        ok = np.isfinite(lo[r])
        if outside:
            whole = Rows(at, smooth)
            mid = pieces_mirrored(mid, (rows.a[r], rows.b[r], lo[r], hi[r]),
                                  (whole.a[r], whole.b[r], los[r], his[r]), ok)
        else:
            mid = np.where(ok, soft_mirror(mid, lo[r], hi[r], los[r], his[r]), mid)
    if up:
        cols = Cols(at)
        tc = cols.t0 + (np.arange(cols.n) + 0.5) * cols.w
        lo, hi = _chords(("cols", sig, cols.t0, cols.w, cols.lo.tobytes(), cols.hi.tobytes()),
                         lambda: chord_ends(lambda p, j: inside(tc[j], p), cols.lo, cols.hi, 64))
        los, his = lo, hi
        if smooth > 0:
            reach = smooth / 100 * cols.n / 2
            los, his = smooth_ends(np.arange(cols.n), lo, reach), smooth_ends(np.arange(cols.n), hi, reach)
        s = cols.slice(mid)
        ok = np.isfinite(lo[s])
        if outside:
            whole = Cols(at, smooth)
            key = pieces_mirrored(key, (cols.lo[s], cols.hi[s], lo[s], hi[s]),
                                  (whole.lo[s], whole.hi[s], los[s], his[s]), ok)
        else:
            key = np.where(ok, soft_mirror(key, lo[s], hi[s], los[s], his[s]), key)
    return mid, key


def soft_mirror(v, lo, hi, los, his):
    """v mirrored across lo..hi, with Smooth corners across the smoothed ends los..his instead: that moves the
    mirrored line by d, so near a corner the end of it would land past the real end (folded back by bounced = a
    crease, sweep 11-05): its last L (a quarter of the line, 2 d at least) squeezed evenly more towards the end into
    L - d (no bend where the squeeze starts), the real end <-> the real end. Spots mirrored past that (off the line
    before): mirrored as they are."""
    v, lo, hi = (np.nan_to_num(np.asarray(x, np.float64)) for x in (v, lo, hi))
    w = np.maximum(hi - lo, 0.0)
    d = np.clip(np.nan_to_num(np.asarray(los, np.float64) + his) - (lo + hi), -w / 4, w / 4)
    x = lo + hi - v + d
    e = np.abs(d)
    L = np.maximum(w / 4, 2 * e)
    sign = np.where(d > 0, 1.0, -1.0)
    start = np.where(d > 0, hi, lo) + sign * (e - L)  # (where the squeeze starts; L further on: the moved end)
    s = (x - start) * sign
    u = np.clip(s, 0.0, L)
    squeezed = start + sign * (u - e * (u / np.maximum(L, 1e-12)) ** 2)
    return np.where((e > 0) & (s > 0), np.where(s <= L, squeezed, x - d), x)


def pieces_mirrored(v, real, soft, ok):
    """Spots v on lines (a key row's time or a time's keys) mirrored across the piece of their line beside the area:
    real / soft = (a, b, lo, hi), the line's notes a..b and the area on it lo..hi, as they are / with Smooth corners
    (ok False where the area doesn't reach): before it = a..lo, after it = hi..b, a line it doesn't reach = a..b
    (soft_mirror). A piece of no width (the area covers that end): the spot stays."""
    a, b, lo, hi = (np.nan_to_num(np.asarray(x, np.float64)) for x in real)
    sa, sb, slo, shi = (np.nan_to_num(np.asarray(x, np.float64)) for x in soft)
    before = v < (lo + hi) / 2
    p0, q0 = np.where(ok, np.where(before, a, hi), a), np.where(ok, np.where(before, sa, shi), sa)
    p1, q1 = np.where(ok, np.where(before, lo, b), b), np.where(ok, np.where(before, slo, sb), sb)
    return np.where(p1 > p0, soft_mirror(v, p0, p1, q0, q1), v)


def _chords(k, make):
    got = _CHORDS.get(k)
    if got is None:
        got = _CHORDS[k] = make()
        while len(_CHORDS) > 32:
            _CHORDS.popitem(last=False)
    else:
        _CHORDS.move_to_end(k)
    return got


def row_twins(at, colours, among=None):
    """Each note's twin for the sideways colour flip by Note order (tester 11-04, PLAN B): on its key row, counted by
    start (then colour), the 1st note <-> the last, the 2nd <-> the 2nd-last and so on, whatever their lengths; a
    row of one note keeps its own. among (a mask): only those notes count, each row's part among them (the rest
    stay their own twin)."""
    n = len(at)
    twin = np.arange(n)
    idx = twin.copy() if among is None else np.flatnonzero(among)
    if not len(idx):
        return twin
    k = np.asarray(at[idx, 2], np.int64)
    o = np.lexsort((np.asarray(colours)[idx], np.asarray(at[idx, 0], np.int64), k))
    ks = k[o]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    sizes = np.diff(np.r_[starts, len(ks)])
    first = np.repeat(starts, sizes)
    last = first + np.repeat(sizes, sizes) - 1
    got = idx[o]
    twin[got] = got[first + last - np.arange(len(ks))]
    return twin


def smooth_ends(pos, vals, r):
    """vals (at sorted places pos) averaged TWICE with the ones within r / sqrt 2 of it (as much smoothing as one
    pass over r), the reach shrunk near either end so it stays even on both sides: a straight line stays exactly
    as it is (evenly spaced places), a corner rounds over about 3 r. Twice (11-08): one pass rounded a corner into
    an arc that met the straight parts with a sudden change of bend, a faint straight line where the rounding ends
    (stir sweep: a triangle's tip flipped upside down, Bend on a diamond / disc). NaN values: left out, stay NaN.
    The reach near the ends EASES into the full reach (11-10, a crease on discs at Smooth corners ~35-50 %: it grew
    one per row, then stopped at once on one row): from half the full reach to 1.5 x it the growth slows smoothly
    (x - (x - 0.5)^2 / 2, in full reaches); never past the end, so straight lines stay exact; places 1.5 full reaches
    or more from the ends as before."""
    vals = np.asarray(vals, np.float64)
    ok = np.isfinite(vals)
    if r <= 0 or ok.sum() < 3:
        return vals
    p, v = np.asarray(pos, np.float64)[ok], vals[ok]
    full = r / np.sqrt(2.0)
    x = np.minimum(p - p[0], p[-1] - p) / full
    reach = full * np.where(x <= 0.5, x, np.where(x >= 1.5, 1.0, x - (x - 0.5) ** 2 / 2))
    lo = np.searchsorted(p, p - reach - 1e-9, side="left")
    hi = np.searchsorted(p, p + reach + 1e-9, side="right")
    for _ in range(2):
        c = np.concatenate([[0.0], np.cumsum(v)])
        v = (c[hi] - c[lo]) / (hi - lo)
    out = vals.copy()
    out[ok] = v
    return out


class Cols:
    """The lowest and highest key of at's notes around each time (notes put in slices by their middle; a slice
    without notes counts as the nearest one with notes), for Stir's upside-down colour flip. smooth: Smooth corners,
    % of the width the lowest / highest keys are averaged over."""

    def __init__(self, at, smooth=0.0):
        mid = (np.asarray(at[:, 0], np.float64) + np.asarray(at[:, 1], np.float64)) / 2
        k = np.asarray(at[:, 2], np.float64)
        self.t0 = float(mid.min())
        self.n = int(max(1, min(1024, len(at) // 8)))  # (about 8 notes a slice)
        self.w = max(float(mid.max()) - self.t0, 1.0) / self.n
        b = self.slice(mid)
        lo, hi = np.full(self.n, np.inf), np.full(self.n, -np.inf)
        np.minimum.at(lo, b, k)
        np.maximum.at(hi, b, k)
        full = np.flatnonzero(np.isfinite(lo))
        i = np.clip(np.searchsorted(full, np.arange(self.n)), 0, len(full) - 1)
        j = np.maximum(i - 1, 0)
        near = full[np.where(np.abs(full[j] - np.arange(self.n)) <= np.abs(full[i] - np.arange(self.n)), j, i)]
        self.lo, self.hi = self.lo0, self.hi0 = lo[near], hi[near]
        if smooth > 0:
            at_ = np.arange(self.n, dtype=np.float64)
            r = smooth / 100 * self.n / 2
            self.lo, self.hi = smooth_ends(at_, self.lo, r), smooth_ends(at_, self.hi, r)
        self.sum = self.lo + self.hi

    def slice(self, mid):
        return np.clip(((np.asarray(mid, np.float64) - self.t0) / self.w).astype(np.int64), 0, self.n - 1)

    def mirrored(self, mid, key):
        s = self.slice(mid)
        return soft_mirror(key, self.lo0[s], self.hi0[s], self.lo[s], self.hi[s])


class Rows:
    """Each key row's notes' span (first start, last end) of at, for Stir's Wrap around, Bend with the outline and
    the sideways colour flip; a key without notes counts as the nearest one with notes (lower on a tie, as nearest
    does). smooth: Smooth corners, % of the height the rows' ends are averaged over (never for Wrap around: it
    wraps at the real notes)."""

    def __init__(self, at, smooth=0.0):
        from notes.engine import unique_inverse
        k = np.asarray(at[:, 2], np.int64)
        self.keys, inv = unique_inverse(k)  # (np.unique's, counted: millions of notes)
        self.a = np.full(len(self.keys), np.inf)
        self.b = np.full(len(self.keys), -np.inf)
        np.minimum.at(self.a, inv, np.asarray(at[:, 0], np.float64))
        np.maximum.at(self.b, inv, np.asarray(at[:, 1], np.float64))
        self.a0, self.b0 = self.a, self.b
        self.at = at  # (kept until bw is first asked for, then let go)
        self.g = np.full(len(self.keys), np.inf)  # (each row's shortest note's half: its first / last middle)
        np.minimum.at(self.g, inv, np.asarray(at[:, 1] - at[:, 0], np.float64) / 2)
        if smooth > 0 and len(self.keys) > 2:
            r = smooth / 100 * float(self.keys[-1] - self.keys[0]) / 2
            self.a, self.b = smooth_ends(self.keys, self.a, r), smooth_ends(self.keys, self.b, r)

    def row(self, key):
        q = np.rint(np.asarray(key, np.float64))
        i = np.clip(np.searchsorted(self.keys, q), 0, len(self.keys) - 1)
        j = np.maximum(i - 1, 0)
        return np.where(np.abs(self.keys[j] - q) <= np.abs(self.keys[i] - q), j, i)

    @property
    def bw(self):
        """Each key row's REPEAT end for Wrap around (user 2026-11-10: colours shifted at the seam): its last note's
        start + the row's usual start-to-start step (the median of its steps, lower one on an even count; steps of 0
        left out), so a row of n evenly spaced notes repeats every n steps and a whole number of colour turns comes
        round exactly. A row with one start (no step) keeps its last end; never before its last end."""
        got = getattr(self, "_bw", None)
        if got is None:
            got, self._bounce = repeat_ends(self.at, self.keys, self.a0, self.b0)
            self._bw = got
            self.at = None
        return got

    def bounce_lines(self, twice=False):
        """Each key row's (lo, hi) Bounce back bounces off (repeat_ends): its first / last note's middle (the end
        note not shown again: 1 2 3 2 1), twice: half a step further out (Bounce back, edge twice: 1 2 3 3 2 1)."""
        self.bw
        return self._bounce[2:] if twice else self._bounce[:2]

    def key_wrapped(self, key, h=None):
        """Keys past the notes' lowest / highest key row come back in on the other side, repeating every whole
        number of key rows (lowest .. highest + 1: a key row past the top = the bottom one); h: every h keys
        instead (Repeat the colours: a whole number of the pattern's repeats up / down)."""
        base = float(self.keys[0]) - 0.5
        if h is None:
            h = float(self.keys[-1] - self.keys[0]) + 1.0
        key = np.asarray(key, np.float64)
        return np.where((key < base) | (key >= base + h), base + np.mod(key - base, h), key)

    def key_bounced(self, key, twice=False, lines=None):
        """Bounce back: keys past the lowest / highest key row bounce back off that row (one past the top = the row
        below it, and so on, as far as it goes); twice: off its outer edge, half the rows' usual gap past it (one past
        the top = the top row again; key_lines). lines: (lowest, highest) to bounce off instead (key_bounces: the
        colours' runs). One key row (not twice): every key past it = it."""
        k0, k1 = key_lines(self.keys, twice) if lines is None else lines
        base, h = float(k0), float(k1) - float(k0)
        key = np.asarray(key, np.float64)
        out = (key < base) | (key > base + h)
        if h <= 0:
            return np.where(out, base, key)
        u = np.mod(key - base, 2 * h)
        return np.where(out, base + np.where(u > h, 2 * h - u, u), key)

    def repeat_of(self, at, colours):
        """Repeat the colours (user 2026-11-10: Wrap around shifted a colour cycle at the seam): (each key row's
        window start, window end, key height) for wrapped: a row whose colours repeat comes back by a whole number of
        its repeats (row_repeats), the key rows by a whole number of the pattern's repeat up / down (key_repeat; None
        = the plain height). Worked out once for these notes + colours (kept: drags ask again every move)."""
        got = getattr(self, "_rep", None)
        c = np.asarray(colours)
        if got is not None and (got[0] is colours or (len(got[1]) == len(c) and np.array_equal(got[1], c))):
            return got[2]
        bw = self.bw
        rep = row_repeats(at, self.keys, self.a, bw, c) + (key_repeat(at, self.keys, self.a0, self.b0, c),)
        self._rep = (colours, c, rep)
        return rep

    def bounce_of(self, at, colours, twice=False):
        """Bounce back's (row starts' lines, row ends' lines, lowest key line, highest key line) on these notes'
        colours (row_bounces, key_bounces); colours None = the plain lines (bounce_lines, key_lines). Worked out once
        for these notes + colours (kept: drags ask again every move)."""
        if colours is None or not len(self.keys):
            return self.bounce_lines(twice) + key_lines(self.keys, twice)
        kept = getattr(self, "_bnc", None)
        if kept is None:
            kept = self._bnc = {}
        got = kept.get(twice)
        c = np.asarray(colours)
        if got is not None and (got[0] is colours or (len(got[1]) == len(c) and np.array_equal(got[1], c))):
            return got[2]
        lines = self.bounce_lines(False) + self.bounce_lines(True)
        out = row_bounces(at, self.keys, c, lines, twice) + key_bounces(at, self.keys, self.a0, self.b0, c, twice)
        kept[twice] = (colours, c, out)
        return out

    def wrapped(self, mid, key, rep=None, edge=None):
        """(middle, key) of spots past the notes (Wrap around): the key round the key rows first (key_wrapped), then
        a middle past its key row's notes back in on that row's other side, every repeat length (bw). rep:
        repeat_of's (window starts, window ends, key height) instead (Repeat the colours). edge "bounce" / "bounce2"
        (Bounce back / edge twice): the key bounced off the key rows (key_bounced), then a middle past its row's
        bounce_lines bounced back off them (as far out as it goes: there and back, again and again), so the colours
        carry on with no seam, backwards."""
        bounce = edge in BOUNCES
        if bounce:  # (rep: bounce_of's lines on the colours; None = the plain ones)
            if rep is None:
                rep = self.bounce_of(None, None, edge == "bounce2")
            key = self.key_bounced(key, edge == "bounce2", rep[2:])
            aw, bw = rep[:2]
        else:
            key = self.key_wrapped(key, None if rep is None else rep[2])
            aw, bw = (self.a, self.bw) if rep is None else rep[:2]
        fast = loops()
        if fast and len(mid):  # (the compiled walk: the very same numbers, files/fastloops.stir_wrap / stir_bounce)
            to = np.ascontiguousarray
            out = np.empty(len(mid), np.float64)
            (fast.stir_bounce if bounce else fast.stir_wrap)(
                to(self.keys, np.int64), to(aw, np.float64), to(bw, np.float64), to(mid, np.float64),
                to(key, np.float64), out)
            return out, key
        r = self.row(key)
        a, b = aw[r], bw[r]
        w = np.maximum(b - a, 1.0)
        if bounce:
            u = np.mod(mid - a, 2 * w)
            return np.where((mid < a) | (mid > b), a + np.where(u > w, 2 * w - u, u), mid), key
        return np.where((mid < a) | (mid > b), a + np.mod(mid - a, w), mid), key

    def mirrored(self, mid, key):
        r = self.row(key)
        g = self.g[r]  # (squeezed up to the first / last note's middle: the bounce's ends)
        return soft_mirror(mid, self.a0[r] + g, self.b0[r] - g, self.a[r] + g, self.b[r] - g)

    def stretched(self, mid, key, t0, t1):
        r = self.row(key)
        a, b = self.a[r], self.b[r]
        return t0 + (mid - a) / np.maximum(b - a, 1.0) * (t1 - t0)

    def unstretched(self, mid, key, t0, t1):
        r = self.row(key)
        a, b = self.a[r], self.b[r]
        return a + (mid - t0) / max(t1 - t0, 1.0) * (b - a)


def repeat_ends(at, keys, starts, ends):
    """(Rows.bw, (lo, hi, lo2, hi2)) for the notes at whose key rows are keys (sorted), first starts starts and
    last ends ends. Bounce back's lines: each row's first / last note's middle (lo, hi: 1 2 3 2 1); edge twice: half
    the row's usual step further out (lo2, hi2: 1 2 3 3 2 1). Middles, not starts / repeat ends: the notes' middles
    mirror onto middles, the same on both sides whatever the gate. A row without a step: its first start .. last
    end (both)."""
    from files.speed import key_sort
    k = np.asarray(at[:, 2], np.int64)
    s = np.asarray(at[:, 0])
    whole = s.dtype == np.int64
    o = key_sort(k, s) if whole else np.lexsort((np.asarray(s, np.float64), k))
    ks, ss = k[o], s[o]
    row = np.searchsorted(keys, ks)  # (each note's key row, in keys' order)
    last = np.full(len(keys), -np.inf)
    np.maximum.at(last, row, np.asarray(ss, np.float64))
    same = ks[1:] == ks[:-1]
    step = (ss[1:] - ss[:-1])[same]
    srow = row[1:][same]
    keep = step > 0
    step, srow = step[keep], srow[keep]
    out = np.asarray(ends, np.float64).copy()
    lo, hi = np.asarray(starts, np.float64).copy(), out.copy()
    lo2, hi2 = lo.copy(), hi.copy()
    if len(step):
        o2 = key_sort(srow.astype(np.int64), step) if whole else np.lexsort((np.asarray(step, np.float64), srow))
        step, srow = np.asarray(step[o2], np.float64), srow[o2]
        count = np.bincount(srow, minlength=len(keys))
        first = np.concatenate([[0], np.cumsum(count)[:-1]])
        has = count > 0
        med = step[(first + (count - 1) // 2)[has]]
        out[has] = np.maximum(out[has], last[has] + med)
        mids = (np.asarray(ss, np.float64) + np.asarray(at[o, 1], np.float64)) / 2
        cut = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])  # (each row's notes in a run, in keys' order)
        m0, m1 = np.minimum.reduceat(mids, cut), np.maximum.reduceat(mids, cut)
        lo[has], hi[has] = m0[has], m1[has]
        lo2[has], hi2[has] = m0[has] - med / 2, m1[has] + med / 2
    return out, (lo, hi, lo2, hi2)


REPEAT_TRIES = 64  # (a row's repeat: at most this many lengths tried; colours a stir mixed never repeat anyway)
REPEAT_LOOK = 256  # (each length tried on this many notes first: most wrong ones fail there at once)
KEY_LOOKS = 512  #(the repeat up / down: each key row's colours looked at in this many spots along the time)


def row_repeats(at, keys, a, bw, colours):
    """Each key row's (window start, window end) for Repeat the colours: a row whose colours (read along it by
    start) repeat EXACTLY every p notes, each p notes later by the same time P, over at least two repeats, comes back
    by whole repeats into a window of the largest whole number of them its notes hold, starting half a step before
    its first note's middle (so every spot in it is nearest the note the cycle has there, the last one too); any
    other row keeps (a, bw) (plain Wrap around). Colours may come in runs (30 notes a colour x 15 colours = 450 notes
    a repeat): the lengths tried are where the colour of the row's first whole run comes again at a run's start."""
    from files.speed import key_sort
    lo = np.asarray(a, np.float64).copy()
    out = np.asarray(bw, np.float64).copy()
    if not len(at):
        return lo, out
    k = np.asarray(at[:, 2], np.int64)
    s = np.asarray(at[:, 0])
    whole = s.dtype == np.int64
    o = key_sort(k, s) if whole else np.lexsort((np.asarray(s, np.float64), k))
    ks, ss, cs = k[o], s[o], np.asarray(colours)[o]
    es = np.asarray(at[:, 1])[o]
    first = np.flatnonzero(np.concatenate([[True], ks[1:] != ks[:-1]]))
    last = np.append(first[1:], len(ks))
    row = np.searchsorted(keys, ks[first])
    for r, i0, i1 in zip(row.tolist(), first.tolist(), last.tolist()):
        c, t = cs[i0:i1], ss[i0:i1]
        p = row_period(c, t, whole)
        if p:
            P = float(t[p] - t[0])
            # (halfway between the first note's middle and the one before it in the cycle: note p - 1, a repeat back)
            m0, mp = (float(t[0]) + float(es[i0])) / 2, (float(t[p - 1]) + float(es[i0 + p - 1])) / 2 - P
            lo[r] = (m0 + mp) / 2
            out[r] = lo[r] + P * (len(c) // p)
    return lo, out


def row_period(c, t, whole):
    """The shortest p (notes) a key row's colours c (read by start t, sorted) repeat EXACTLY by, each p notes later
    by the same time, over at least two repeats; 0 = none (fewer than 4 notes, two starting together, one colour /
    one change). Colours may come in runs: the lengths tried are where the colour of the row's first whole run comes
    again at a run's start."""
    n = len(c)
    if n < 4 or not (t[1:] > t[:-1]).all():  # (two notes starting together: no single order to repeat by)
        return 0
    runs = np.flatnonzero(c[1:] != c[:-1]) + 1
    if len(runs) < 2:  # (one colour (or one change): plain Wrap around comes out the same)
        return 0
    r1 = int(runs[0])  # (the first WHOLE run's start; the row's first run may be cut short)
    tries = runs[1:][c[runs[1:]] == c[r1]] - r1
    for q in tries[tries <= n // 2][:REPEAT_TRIES].tolist():
        m = min(REPEAT_LOOK, n - q)
        P = t[q] - t[0]
        tol = 0 if whole else 1e-6 * max(1.0, abs(float(P)))  # (whole ticks: exact)
        if not (np.array_equal(c[:m], c[q:q + m]) and (np.abs(t[q:q + m] - t[:m] - P) <= tol).all()):
            continue
        if np.array_equal(c[:n - q], c[q:]) and (np.abs(t[q:] - t[:n - q] - P) <= tol).all():
            return q
    return 0


def _run_at(bounds, i, n):
    """(first, last) note of the colour run holding note i; bounds = where runs start (the first left out)."""
    j = int(np.searchsorted(bounds, i, "right"))
    return (int(bounds[j - 1]) if j else 0), (int(bounds[j]) - 1 if j < len(bounds) else n - 1)


def _whole_run(edges):
    """The end run's length as if whole: edges = run edges from the end inward (the end first). The run itself,
    unless it's shorter than the next two, which are alike (a regular pattern): then theirs."""
    e = np.asarray(edges, np.int64)
    if len(e) >= 4:
        l0, l1, l2 = abs(int(e[1] - e[0])), abs(int(e[2] - e[1])), abs(int(e[3] - e[2]))
        if l1 == l2 and l0 < l1:
            return l1
    return abs(int(e[1] - e[0]))


def row_bounces(at, keys, colours, lines, twice):
    """Each key row's (lo, hi) for Bounce back on the colours (user 2026-11-10: with colours in runs the end colour
    showed twice as wide, Bounce back looked like edge twice): Bounce back turns in the MIDDLE of the end colour's
    run (shown once at its own width: ... 2 1 2 ...), edge twice at the run's outer end (... 2 1 1 2 ...). A row
    whose colours repeat (row_period) takes its end runs as if WHOLE (a row starting partway into a run turns as if
    the run were all there: the same band on every row); any other row its end runs as it has them (edge twice:
    the line it had). One note a colour = the lines it had. lines: repeat_ends' (lo, hi, lo2, hi2)."""
    from files.speed import key_sort
    lo, hi = (np.asarray(v, np.float64).copy() for v in (lines[2:] if twice else lines[:2]))
    if not len(at):
        return lo, hi
    k = np.asarray(at[:, 2], np.int64)
    s = np.asarray(at[:, 0])
    whole = s.dtype == np.int64
    o = key_sort(k, s) if whole else np.lexsort((np.asarray(s, np.float64), k))
    ks, ss, cs = k[o], s[o], np.asarray(colours)[o]
    ms = (np.asarray(ss, np.float64) + np.asarray(at[:, 1], np.float64)[o]) / 2
    first = np.flatnonzero(np.concatenate([[True], ks[1:] != ks[:-1]]))
    last = np.append(first[1:], len(ks))
    row = np.searchsorted(keys, ks[first])
    for r, i0, i1 in zip(row.tolist(), first.tolist(), last.tolist()):
        n = i1 - i0
        c, t, m = cs[i0:i1], ss[i0:i1], ms[i0:i1]
        if n < 2 or not (t[1:] > t[:-1]).all():
            continue
        bounds = np.flatnonzero(c[1:] != c[:-1]) + 1
        if not len(bounds):  # (one colour: any line gives the same)
            continue
        p = row_period(c, t, whole)
        if p:
            P = float(t[p] - t[0])

            def mid(i):  # (note i's middle, the row repeated both ways)
                q = i // p
                return float(m[i - q * p]) + P * q
            a0, a1 = _run_at(bounds, p, n)  # (the first colour's run in the 2nd repeat: whole)
            a0, a1 = a0 - p, a1 - p
            b0, b1 = _run_at(bounds, n - 1 - p, n)  # (the last colour's run a repeat back: whole)
            b0, b1 = b0 + p, b1 + p
            if twice:
                L, H = (mid(a0 - 1) + mid(a0)) / 2, (mid(b1) + mid(b1 + 1)) / 2
            else:
                L, H = (mid(a0) + mid(a1)) / 2, (mid(b0) + mid(b1)) / 2
        else:  # (no repeat found: the end runs as the row has them; one cut short next to two whole runs of the
            # same length (a cycle too short to show two repeats) = as if whole, the row's usual step apart)
            d = np.sort(np.diff(np.asarray(t, np.float64)))
            st = float(d[(len(d) - 1) // 2])
            a0, a1, b0, b1 = 0, int(bounds[0]) - 1, int(bounds[-1]), n - 1
            a0, b1 = a1 + 1 - _whole_run(np.r_[0, bounds, n]), b0 - 1 + _whole_run(np.r_[n, bounds[::-1], 0])

            def mid(i):  # (note i's middle; before / after the row: the usual step on)
                return float(m[0]) + i * st if i < 0 else float(m[n - 1]) + (i - n + 1) * st if i >= n else float(m[i])
            if twice:
                L, H = mid(a0) - st / 2, mid(b1) + st / 2
            else:
                L, H = (mid(a0) + mid(a1)) / 2, (mid(b0) + mid(b1)) / 2
        if H > L:
            lo[r], hi[r] = L, H
    return lo, hi


def key_lines(keys, twice):
    """(lowest, highest) key line Bounce back bounces keys off: the lowest / highest key row; edge twice: half the
    key rows' usual gap past them (rows on every key: half a key; rows 2 keys apart: a key, user 2026-11-10: the
    top showed the edge row once). One key row: edge twice half a key each way."""
    k0, k1 = float(keys[0]), float(keys[-1])
    if not twice:
        return k0, k1
    d = np.diff(np.asarray(keys, np.float64))
    g = float(np.sort(d)[(len(d) - 1) // 2]) if len(d) else 1.0  # (the median gap, lower one on an even count)
    return k0 - g / 2, k1 + g / 2


def key_bounces(at, keys, a0, b0, colours, twice):
    """key_lines on the colours (Bounce back up / down, as row_bounces along the rows): the key rows coming in runs
    of one colour pattern (By key colours), looked at in KEY_LOOKS spots along the time; Bounce back turns in the
    middle of the lowest / highest run, edge twice at its outer end. A pattern repeating up / down every v keys
    takes its end runs as if whole; otherwise the runs as they are (edge twice: key_lines)."""
    k0, k1 = key_lines(keys, twice)
    if len(keys) < 2:
        return k0, k1
    got, inside = _key_looks(at, keys, a0, b0, colours)
    nk = len(keys)
    least = max(1, KEY_LOOKS // 16)

    def same(i, j):
        both = inside[i] & inside[j]
        return both.sum() >= least and bool((got[i][both] == got[j][both]).all())
    v = _key_period(_key_grid(keys, got, inside))
    kl = np.asarray(keys)
    gap = (k1 - float(keys[-1])) * 2  # (edge twice's gap; 0 for Bounce back)

    def run(i):  # (first, last) key row of the run of rows alike holding row i
        a = i
        while a > 0 and same(a - 1, i):
            a -= 1
        b = i
        while b < nk - 1 and same(b + 1, i):
            b += 1
        return a, b
    lo, hi = k0, k1
    if v:
        ib, it = np.searchsorted(kl, kl[0] + v), np.searchsorted(kl, kl[-1] - v)
        if ib < nk and kl[ib] == kl[0] + v and kl[it] == kl[-1] - v:
            a, b = run(int(ib))
            if not (a == 0 and b == nk - 1):
                lo = float(kl[a]) - v - gap / 2 if twice else (float(kl[a]) + float(kl[b])) / 2 - v
            a, b = run(int(it))
            if not (a == 0 and b == nk - 1):
                hi = float(kl[b]) + v + gap / 2 if twice else (float(kl[a]) + float(kl[b])) / 2 + v
            return (lo, hi) if hi > lo else (k0, k1)
    # (no repeat found: the end runs as they are; one cut short next to two whole runs alike = as if whole, the
    # rows' usual gap apart)
    g = (float(np.sort(np.diff(kl.astype(np.float64)))[(nk - 2) // 2]))
    edges = [0]
    while edges[-1] < nk and len(edges) < 4:
        edges.append(run(edges[-1])[1] + 1)
    a, b = 0, edges[1] - 1
    if b == nk - 1:  # (all one run: any line gives the same)
        return k0, k1
    first = float(kl[b]) - (_whole_run(edges) - 1) * g
    lo = first - g / 2 if twice else (first + float(kl[b])) / 2
    edges = [nk - 1]
    while edges[-1] >= 0 and len(edges) < 4:
        edges.append(run(edges[-1])[0] - 1)
    a = edges[1] + 1
    top = float(kl[a]) + (_whole_run(edges) - 1) * g
    hi = top + g / 2 if twice else (float(kl[a]) + top) / 2
    return (lo, hi) if hi > lo else (k0, k1)


def key_repeat(at, keys, a0, b0, colours):
    """The key height for Repeat the colours: the smallest v (key rows) where each key row's colours, looked at in
    the same KEY_LOOKS spots along the time, are those of the row v higher wherever both have notes (a pattern
    repeating up / down: By key, a slant), over at least two repeats; the largest whole number of v inside the key
    rows (lowest .. highest + 1). None found = None (plain Wrap around's height)."""
    if len(keys) < 2:
        return None
    v = _key_period(_key_grid(keys, *_key_looks(at, keys, a0, b0, colours)))
    h = int(keys[-1]) - int(keys[0]) + 1
    return float(v * (h // v)) if v else None


def _key_looks(at, keys, a0, b0, colours):
    """(colours, inside): each key row's colours looked at in the same KEY_LOOKS spots along the time, and which
    of those spots lie inside the row's notes."""
    t = np.linspace(float(np.min(a0)), float(np.max(b0)), KEY_LOOKS)
    kk = np.repeat(np.asarray(keys, np.float64), KEY_LOOKS)
    tt = np.tile(t, len(keys))
    got = np.asarray(colours)[nearest(at, tt, kk, own=False)].reshape(len(keys), KEY_LOOKS)
    inside = (t[None, :] >= np.asarray(a0)[:, None]) & (t[None, :] <= np.asarray(b0)[:, None])
    return got, inside


def _key_grid(keys, got, inside):
    """_key_looks' rows on every key lowest .. highest (-1 = no note there)."""
    lo = int(keys[0])
    grid = np.full((int(keys[-1]) - lo + 1, KEY_LOOKS), -1, np.int64)
    grid[np.asarray(keys) - lo] = np.where(inside, got, -1)
    return grid


def _key_period(grid):
    """The smallest v (keys) the grid's rows repeat by wherever both have notes, over at least two repeats; 0 =
    none."""
    h = len(grid)
    for v in range(1, h // 2 + 1):
        x, y = grid[:-v], grid[v:]
        both = (x >= 0) & (y >= 0)
        if both.sum() < KEY_LOOKS // 4:  # (too little side by side to tell)
            continue
        if (x[both] == y[both]).all():
            return v
    return 0


_ROWS = {}  # (smooth) -> (at, its Rows) of the last big kept at


def rows_of(at, smooth=0.0):
    """Rows(at, smooth), kept for the last big at nobody changes (Stir knob turns: the same notes each time)."""
    got = _ROWS.get(smooth)
    if got is not None and got[0] is at:
        return got[1]
    rows = Rows(at, smooth)
    if len(at) >= BEFORE_STIR_LEAST and not at.flags.writeable and keeping():
        if any(g[0] is not at for g in _ROWS.values()):
            _ROWS.clear()
        _ROWS[smooth] = (at, rows)
    return rows


_SORTED = {}  # "got": (at, nearest's sorted notes) of the last big at (kept read-only between Stir changes)


def _sorted_notes(at):
    """nearest's notes sorted by key, then middle, as one long line (each key row its own stretch); kept for the
    last big at (Stir knob turns look up the same notes each time, user 11-07)."""
    got = _SORTED.get("got")
    if got is not None and got[0] is at:
        return got[1]
    n = len(at)
    nk = np.asarray(at[:, 2], np.int64)
    nm = (np.asarray(at[:, 0], np.float64) + np.asarray(at[:, 1], np.float64)) / 2
    a = np.asarray(at)
    if a.dtype == np.int64 and n and np.abs(a[:, :2]).max() < 1 << 52:
        # (whole ticks: by start + end is by middle, the same order; the compiled sort when it's there)
        o = key_sort(nk, a[:, 0] + a[:, 1])
    else:
        o = np.lexsort((nm, nk))
    ks, ms = nk[o], nm[o]
    new = np.ones(n, bool)  # (sorted: each key's first place, as np.unique gives it)
    new[1:] = ks[1:] != ks[:-1]
    first = np.flatnonzero(new)
    keys = ks[first]
    last = np.append(first[1:], n)
    base, top = float(ms.min()), float(ms.max())
    gap = 2 * (top - base) + 2
    line = ms - base + np.repeat(np.arange(len(keys)) * gap, last - first)
    out = (nk, nm, o, ks, ms, keys, first, last, line, base, top, gap)
    if n >= BEFORE_STIR_LEAST and not at.flags.writeable and keeping():  # (only arrays nobody changes: the kept ones)
        _SORTED["got"] = (at, out)
    return out


def nearest(at, mid, key, own=True, kept=None):
    """For each spot (mid, key): the note of at nearest it: on the nearest key holding notes (the key rounded), the
    note there whose middle is nearest in time. own: spot i is note i's own spot moved: the note itself as near as
    the one found wins (no stir = each note its own). kept: _sorted_notes(at) made already (Liquify's brush)."""
    n = len(at)
    nk, nm, o, ks, ms, keys, first, last, line, base, top, gap = kept or _sorted_notes(at)
    fast = loops()
    if fast and len(mid):  # (the compiled search: the very same notes, files/fastloops.stir_nearest)
        to = np.ascontiguousarray
        out = np.empty(len(mid), np.int64)
        fast.stir_nearest(to(keys, np.int64), to(first, np.int64), to(last, np.int64), to(line, np.float64),
                          to(ms, np.float64), to(nk, np.int64), to(nm, np.float64), to(o, np.int64), float(base),
                          float(top), float(gap), to(mid, np.float64), to(key, np.float64), bool(own), out)
        return out
    q = np.rint(np.asarray(key, np.float64))
    i = np.clip(np.searchsorted(keys, q), 0, len(keys) - 1)
    j = np.maximum(i - 1, 0)
    row = np.where(np.abs(keys[j] - q) <= np.abs(keys[i] - q), j, i)  # (the nearer key with notes; lower on a tie)
    lo, hi = first[row], last[row]
    mid = np.asarray(mid, np.float64)
    # (a spot past every note in time looks from the nearest end: the same note is found; the distances below use
    # the real spot)
    p = np.clip(np.searchsorted(line, np.clip(mid, base, top) - base + row * gap), lo, hi - 1)
    p0 = np.maximum(p - 1, lo)
    p = np.where(np.abs(ms[p0] - mid) <= np.abs(ms[p] - mid), p0, p)
    got = o[p]
    if not own:
        return got
    mine = (nk == keys[row]) & (np.abs(nm - mid) <= np.abs(ms[p] - mid) + 1e-9)
    return np.where(mine, np.arange(n), got)


def whole_shares(rc):
    """((start, end) across, (bottom, top) up) of the whole's box (rc "whole") in shares of the piece's own box
    (0, 1 = its own)."""
    w = rc.get("whole")
    if not w:
        return (0.0, 1.0), (0.0, 1.0)
    wd, ht = max(w[4], 1), max(w[5], 1)
    return (w[0] / wd, 1 + w[1] / wd), (w[2] / ht, 1 + w[3] / ht)


def colour_box(notes, rc, own=None):
    """(the box the dice corner and Clumps areas count in, the shape's own box) for notes (as shown, without
    Recolour; own: their box without the piano roll's edges, engine.free_box), both where the colours are worked
    out (a flipped shape's mirrored back in its notes' box, places_of)."""
    S = box_of(notes)
    F = list(own or S)
    a, b, _, d = rc.get("frame") or SAME
    if not b:
        if a < 0:
            F[0], F[1] = S[0] + S[1] - F[1], S[0] + S[1] - F[0]
        if d < 0:
            F[2], F[3] = S[2] + S[3] - F[3], S[2] + S[3] - F[2]
    w = rc.get("whole")
    B = (F[0] + w[0], F[1] + w[1], F[2] + w[2], F[3] + w[3]) if w and not b else tuple(F)
    return B, tuple(F)


def areas(notes, rc, u, n, box=None, own=None):
    """Split: Clumps: where each note's middle lands, 0 <= place < 1 across the colour areas (Shares cut it in
    order), in the shape's box (time and keys each 0..1: the same on any zoom). Bands = stripes across the Angle's
    way (0 = upright, in time order), Wedges = slices around the Centre point, Swirl = wedges twisted more near it
    (Swirl tightness = turns out to the edge). Push bunches the areas toward a side (the box squeezed there).
    Rough edges (Amount) = each place moved by up to about one area: mostly by a smooth noise (ragged clumps), a
    little by the note's own throw u."""
    s, e, k = (np.asarray(notes[:, c], np.float64) for c in (0, 1, 2))
    f = rc.get("frame")
    turned = bool(f and f[1])
    t0, t1, k0, k1 = (own if turned else box) or box_of(notes)  # (box: the colours' box, colour_box)
    x = ((s + e) / 2 - t0) / max(t1 - t0, 1.0)
    y = (k - k0) / (k1 - k0) if k1 > k0 else np.full(len(k), 0.5)
    if turned:  # (turned since: each place turned back in its own box, places_of did the flips; then in the whole's)
        x, y = 0.5 + f[1] * (y - 0.5), 0.5 + f[2] * (x - 0.5)
        (a0, a1), (b0, b1) = whole_shares(rc)
        x, y = (x - a0) / max(a1 - a0, 1e-12), (y - b0) / max(b1 - b0, 1e-12) if b1 > b0 else y
    cx, cy = (v / 100 for v in rc.get("centre", (50, 50)))
    px, py = rc.get("push", (0, 0))
    gx, gy = 2 ** (px / 2), 2 ** (py / 2)  # (pushed right: x ** gx < x, so the cuts land further right, bunched)
    x, cx = np.clip(x, 0, 1) ** gx, cx ** gx
    y, cy = np.clip(y, 0, 1) ** gy, cy ** gy
    turn = rc.get("angle", 0.0) / 360
    pattern = rc.get("pattern", "bands")
    dx, dy = x - cx, y - cy
    if pattern == "bands":
        a = turn * 2 * np.pi
        ca, sa = round(np.cos(a), 12), round(np.sin(a), 12)  # (90 degrees exact: a row on a cut stays one colour)
        q = dx * ca + dy * sa
        ends = [(X - cx) * ca + (Y - cy) * sa for X in (0, 1) for Y in (0, 1)]
        place = (q - min(ends)) / max(max(ends) - min(ends), 1e-12)
    else:
        place = np.arctan2(dy, dx) / (2 * np.pi) - turn
        if pattern == "swirl":
            far = max(np.hypot(X - cx, Y - cy) for X in (0, 1) for Y in (0, 1))
            place = place + rc.get("tight", TIGHT) * np.sqrt(np.hypot(dx, dy) / max(far, 1e-12))
    rough = rc["amount"] / 100
    if rough:
        seed = rc["seed"]
        wobble = 0.65 * _noise(x, y, seed, 6) + 0.35 * _noise(x, y, seed + 1, 17)
        place = place + rough / n * (2.2 * (wobble - 0.5) + 0.4 * (u - 0.5))
    if pattern == "bands":
        return np.clip(place, 0.0, np.nextafter(1.0, 0))
    return np.mod(place, 1.0)


def _noise(x, y, seed, cells):
    """A smooth bumpy value 0..1 over the box (cells bumps across), the same for the same seed."""
    gx, gy = x * cells, y * cells
    ix, iy = np.floor(gx), np.floor(gy)
    fx, fy = gx - ix, gy - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    ix, iy = ix.astype(np.int64), iy.astype(np.int64)
    a, b = np.meshgrid(np.arange(cells + 2, dtype=np.int64), np.arange(cells + 2, dtype=np.int64), indexing="ij")
    with np.errstate(over="ignore"):  # (each corner's value once, looked up by every note: much quicker)
        v = _mixed(_mixed(np.full(a.size, np.uint64(seed * 2 + 7), np.uint64) ^ a.reshape(-1).view(np.uint64))
                   ^ b.reshape(-1).view(np.uint64))
    grid = ((v >> np.uint64(11)).astype(np.float64) / float(1 << 53)).reshape(a.shape)
    ix, iy = np.clip(ix, 0, cells), np.clip(iy, 0, cells)
    top = grid[ix, iy + 1] * (1 - fx) + grid[ix + 1, iy + 1] * fx
    bottom = grid[ix, iy] * (1 - fx) + grid[ix + 1, iy] * fx
    return bottom * (1 - fy) + top * fy


def torn(p, w, own, u):
    """Split: Scattered: each note's colour among p (chances w) but never its own one (own), the others' chances
    kept to each other; no other colour with a chance = its own."""
    order = np.argsort(p)
    at = np.minimum(np.searchsorted(p[order], own), len(p) - 1)
    mine = np.where(p[order][at] == own, order[at], -1)
    wo = np.where(mine >= 0, w[np.maximum(mine, 0)], 0.0)
    before = np.where(mine >= 0, (np.cumsum(w) - w)[np.maximum(mine, 0)], 2.0)
    v = u * (1 - wo)
    v = np.where(v >= before, v + wo, v)  # (stepping over its own colour's stretch)
    got = p[picked(w, np.minimum(v, np.nextafter(1.0, 0)))]
    return np.where((1 - wo > 1e-9) & (got != own), got, own)


def settled(notes, t):
    """notes and their tracks with each track's overlaps on a key fixed as render does (engine.resolve_overlaps,
    the track as the slot); the notes' other columns kept."""
    from notes.engine import resolve_overlaps
    vel = notes[:, 3] if notes.shape[1] > 3 else np.full(len(notes), 100)  # ((start, end, key) only: all alike)
    rows = np.column_stack([np.asarray(notes[:, :3], np.int64), np.asarray(vel, np.int64), t, np.arange(len(notes))])
    got = resolve_overlaps(rows)
    if len(got) == len(rows) and (got[:, 1] == rows[got[:, 5], 1]).all():
        return notes, t  # (nothing overlapped)
    idx = got[:, 5]
    out = np.array(notes[idx])
    out[:, 1] = got[:, 1]
    return out, t[idx]


def kept_apart(notes, old, new, colours, pick):
    """new colours with every clash put right: a note sounding on its key together with earlier ones (overlapping,
    not just touching) gets a colour none of those with ANOTHER colour before has now (its own one first, else a
    free one from its dice), or its own one when all are taken. Notes of one colour before may share (sustained).
    Only bursts of notes overlapping on a key are looked at: pairs all at once, bigger ones note by note."""
    s, e, k = (np.asarray(notes[:, c], np.int64) for c in (0, 1, 2))
    o = key_sort(k, s)  # (np.lexsort((s, k)), compiled when it's there)
    ks, ss, es = k[o], s[o], e[o]
    reach =np.maximum.accumulate(es + ks * (1 << 40))  # (the latest end so far, counted per key: keys far apart)
    over = np.zeros(len(o), bool)
    over[1:] = ss[1:] + ks[1:] * (1 << 40) < reach[:-1]
    over[1:] &= ks[1:] == ks[:-1]
    if not over.any():
        return new
    new = new.copy()
    # (a note whose colour before isn't one of these (Mix across sets + a Stir's "Stay": it came from another set)
    # falls back on its dice's colour here, never its own: the compiled walk read past the colour list = every
    # note gone, and the plain one put it back in the other set, hunt 11-07)
    colours = np.asarray(colours)
    nc = len(colours)
    there = colours[np.clip(np.searchsorted(colours, old), 0, nc - 1)] == old
    if not there.all():
        old = np.where(there, old, colours[np.minimum((np.asarray(pick) * nc).astype(np.int64), nc - 1)])
    burst = np.cumsum(~over) - 1  # (each note's burst: notes on a key each overlapping one before it)
    size = np.bincount(burst)
    pair = over & (size[burst] == 2)  # the second note of a burst of two
    if pair.any():
        b = o[pair]
        a = o[np.flatnonzero(pair) - 1]
        clash = (old[a] != old[b]) & (new[a] == new[b])
        a, b = a[clash], b[clash]
        if len(b):
            n = len(colours)
            other = colours[(np.searchsorted(colours, new[a]) + 1
                             + np.minimum((pick[b] * (n - 1)).astype(np.int64), n - 2)) % n]
            new[b] = np.where(old[b] != new[a], old[b], other)
    starts = np.flatnonzero(~over)
    big = np.flatnonzero(size > 2)
    fast = loops()
    if fast and len(big):  # (the compiled walk: the same colours, files/fastloops.py)
        to = np.ascontiguousarray
        old_c, new_c = np.searchsorted(colours, old), np.searchsorted(colours, new)
        fast.recolour_apart(to(o, np.int64), to(ss), to(e), to(old_c, np.int64), new_c, to(pick, np.float64),
                            to(starts[big], np.int64), to(size[big], np.int64), len(colours))
        return colours[new_c]
    lst = colours.tolist()
    for w in big.tolist():
        sounding = []  # (end, note) of the notes still sounding in this burst
        for j in range(int(starts[w]), int(starts[w]) + int(size[w])):
            i = int(o[j])
            while sounding and sounding[0][0] <= ss[j]:  # (ending where this one starts: touching, fine)
                heapq.heappop(sounding)
            mine = int(old[i])
            taken = {int(new[x]) for _, x in sounding if int(old[x]) != mine}
            if int(new[i]) in taken:
                free = [c for c in lst if c not in taken]
                if not free or mine in free:
                    new[i] = mine
                else:
                    new[i] = free[min(int(pick[i] * len(free)), len(free) - 1)]
            heapq.heappush(sounding, (int(es[j]), i))
    return new


def first_starts(notes, tracks, starts=None):
    """{colour (track % ROUND): the tick it first starts at} of the notes before recolouring (starts: each note's
    start to count by, e.g. a gate flipped shape's unflipped ones)."""
    if tracks is None or not len(notes):
        return {}
    low = np.asarray(tracks, np.int64) % ROUND
    s = np.asarray(notes[:, 0] if starts is None else starts, np.int64)
    from notes.engine import unique_inverse
    ids, which = unique_inverse(low)  # (np.unique's, counted: millions of notes)
    first = np.full(len(ids), np.iinfo(np.int64).max)
    np.minimum.at(first, which.reshape(-1), s)
    return dict(zip(ids.tolist(), first.tolist()))


def counted_starts(notes, tracks, firsts, own=None):
    """Each note's start as render should count it (its colour's first start before recolouring, firsts); a colour
    that had none: the note's own start (own: the starts to count by instead, e.g. a gate flipped shape's unflipped
    ones)."""
    low = np.asarray(tracks, np.int64) % ROUND
    own = np.asarray(notes[:, 0] if own is None else own, np.int64)
    if not firsts:
        return own
    ids = np.array(sorted(firsts), np.int64)
    vals = np.array([firsts[i] for i in ids.tolist()], np.int64)
    at = np.clip(np.searchsorted(ids, low), 0, len(ids) - 1)
    return np.where(ids[at] == low, vals[at], own)
