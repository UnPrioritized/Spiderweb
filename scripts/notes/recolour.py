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
import math
import random

import numpy as np

from files.speed import loops
from notes.custom import ROUND

MODES = ("shuffle", "scatter", "clumps")
KEEP = "keep"  # (Off with a Stir: the colours as they are, only stirred; the dropdown shows Off)
KEEP_KEYS = ("mode", "seed", "stir", "frame", "whole", "most", "ppq")  # (what mode KEEP looks at: recoloured)
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
# round (user picked "works either way"))
STIR_DEFAULTS = {"turn": 0.0, "twist": 0.0, "centre": [50.0, 50.0], "sets": "stay", "edge": "escape",
                 "squeeze": True, "reach": None, "area": "circle", "round": False, "stretch": 100.0,
                 "spin": False, "seen": None, "flip_side": False, "flip_up": False}
MOST_REACH, LEAST_REACH = 100.0, 0.01
MOST_TURN = 360000.0  # (Stir's Turn: 1000 whole turns, only so a file can't hold nonsense; user 11-04 dropped +-360)
MOST_STRETCH, LEAST_STRETCH = 1000.0, 10.0
STIR_AREAS = ("circle", "square", "diamond")  # (Size = the circle's width / the square's side / the diamond's tip to tip)
MOST_STIRS = 20  # (stacked stirs on one shape)
STIR_SETS = ("stay", "move")  # (more than one colour set: each note keeps its set / takes the colour found itself)
# (colours stirred past the edge: gone / back in on the other side OF THE NOTES (each key row's own span) / back in
# on the other side of the BOX, the outline cutting it out (the old Wrap) / each key row stretched to the box first,
# so the stir bends with the outline; BMC tester 11-03: Wrap turned into "the box cut out" on round / slanted shapes)
STIR_EDGES = ("escape", "wrap", "cut", "bend")
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
    if r["mode"] == KEEP and not stir:
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
    w = r.get("whole")
    if isinstance(w, list) and len(w) == 6 and all(number(x) and x == int(x) and abs(x) < 1 << 40 for x in w) \
            and w[4] >= 0 and w[5] >= 0 and any(w[:4]):
        out["whole"] = [int(x) for x in w]
    if stir:
        out["stir"] = stir
    return out


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
    MOST_REACH (+ "area": STIR_AREAS, "stretch": %, "round": True, only with a reach), "spin": True, "seen":
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
    c = s.get("centre")
    if isinstance(c, list) and len(c) == 2 and all(number(x) and 0 <= x <= 100 for x in c) and \
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
    r = s.get("reach")
    if number(r) and LEAST_REACH <= round(r, 3) <= MOST_REACH:
        out["reach"] = round(float(r), 3)
        if s.get("area") in STIR_AREAS[1:]:
            out["area"] = s["area"]
        w = s.get("stretch")
        if number(w) and LEAST_STRETCH <= round(w, 3) <= MOST_STRETCH and round(w, 3) != 100:
            out["stretch"] = round(float(w), 3)
    v = s.get("seen")
    if number(v) and 1e-9 < v < 1e9:
        if s.get("round") is True and "reach" in out:
            out["round"] = True
        if s.get("spin") is True:
            out["spin"] = True
        if out.get("round") or out.get("spin"):
            out["seen"] = float(v)
    return out or None


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
        return clean_recolour(rc)
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
    with np.errstate(over="ignore"):
        x = np.full(len(notes), np.uint64(seed * 2 + salt), np.uint64)
        for c in cols:
            x = _mixed(x ^ c.view(np.uint64))
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def colour_sets(low):
    """(each note's set number, each set's own colours, sorted) of colours low (tracks without their rounds): cut
    every SET colours in order."""
    ids, inv = np.unique(low, return_inverse=True)
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


def centre_at(rc, notes, own, t, k):
    """The Centre point (% of the colours' box) for a spot (tick, key) on the piano roll (centre_spot's way back)."""
    S = box_of(notes)
    B, F = colour_box(notes, rc, own)
    a, b, f2, d = rc.get("frame") or SAME
    if not b:
        t, k = (S[0] + S[1] - t if a < 0 else t), (S[2] + S[3] - k if d < 0 else k)
        return [100 * (t - B[0]) / max(B[1] - B[0], 1), 100 * (k - B[2]) / (B[3] - B[2]) if B[3] > B[2] else 50.0]
    x, y = (t - F[0]) / max(F[1] - F[0], 1) - 0.5, ((k - F[2]) / (F[3] - F[2]) if F[3] > F[2] else 0.5) - 0.5
    u, v = 0.5 + b * y, 0.5 + f2 * x
    (a0, a1), (b0, b1) = whole_shares(rc)
    return [100 * (u - a0) / max(a1 - a0, 1e-12), 100 * (v - b0) / (b1 - b0) if b1 > b0 else 50.0]


def box_of(notes):
    """(first start, last end, lowest key, highest key) of notes."""
    return int(notes[:, 0].min()), int(notes[:, 1].max()), int(notes[:, 2].min()), int(notes[:, 2].max())


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
    if rc["mode"] == KEEP:  # (Off + a Stir: the shape's own colours only, the same however it got there: settings
        # left from another mode (Per chord, Colours, Mix, Shares) wait for it, the seed too (which of two notes
        # sounding together gives way: never random here)
        rc = {k: v for k, v in rc.items() if k in KEEP_KEYS}
        rc["seed"] = 1
    if tracks is None and len(notes) and rc["mode"] in SPLIT:  # (one colour: Split works from it)
        tracks = np.zeros(len(notes), np.int64)
    if tracks is None or not len(notes) or plan(tracks, rc) is None:
        return notes, tracks
    t = np.asarray(tracks, np.int64)
    if not held:
        notes, t = settled(notes, t)
    which, pals = plan(t, rc)
    low = t % ROUND
    lap = t - low  # (each note's round, put back after)
    box, own = colour_box(notes, rc, own)
    at = places_of(notes, rc, gate, box_of(notes))
    corner = (box[0], box[2])
    pick = dice(at, low, rc["seed"], 1, corner)
    if rc["mode"] == KEEP:
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
    return notes, lap + all_stirred(notes, at, low, new, rc, which, pals, pick, box, own)


def all_stirred(notes, at, low, now, rc, which, pals, pick, box, own):
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
    mid, key = stir_from(at, rc, box, own)
    mid, key = flipped(at, mid, key, st.get("flip_side"), st.get("flip_up"))
    src = nearest(at, mid, key)
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


def stir_from(at, rc, box, own):
    """(middle in ticks, key) each note's colour comes from under rc's Stir: its place in the colour box turned
    back round the Centre point by Turn + Twist (turns, most near the centre: Swirl's way), then (Squeeze on) the
    Centre point taken back to the middle (the colours' middle moved there: squeezed on one side, stretched on the
    other)."""
    st = full_stir(rc.get("stir"))
    s, e, k = (np.asarray(at[:, c], np.float64) for c in (0, 1, 2))
    mid = (s + e) / 2
    edge = st["edge"]
    if edge in ("wrap", "bend"):
        rows = Rows(at)
        t0, t1 = float(at[:, 0].min()), float(at[:, 1].max())
    if edge == "bend":  # (each key row stretched to the notes' whole width: no empty spot left to come from)
        mid = rows.stretched(mid, k, t0, t1)
    x, y = unit_xy(mid, k, rc, box, own)
    cx, cy = (v / 100 for v in st["centre"])
    dx, dy = x - cx, y - cy
    turn = st["turn"] / 360
    reach = st["reach"]
    (kx, ky), (rx, ry) = area_scales(st, lambda u, v: from_unit(u / 100, v / 100, rc, box, own), rc.get("ppq"))
    if reach:  # (an Area: Twist winds out to its edge, everything fades smoothly to none there)
        q = np.clip(area_dist(dx * kx, dy * ky, st["area"]) / (reach / 200), 0.0, 1.0)
        w = 1 - q * q * (3 - 2 * q)
        if st["twist"]:
            turn = turn + st["twist"] * np.sqrt(q)
        turn = turn * w
    elif st["twist"]:
        far = max(np.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1))
        turn = turn + st["twist"] * np.sqrt(np.hypot(dx * rx, dy * ry) / max(far, 1e-12))
    a = -2 * np.pi * np.asarray(turn, np.float64)
    ca, sa = np.cos(a), np.sin(a)
    X, Y = dx * rx, dy * ry  # (spin: turned round as seen; else rx = ry = 1, the box's own way)
    x, y = cx + (X * ca - Y * sa) / rx, cy + (X * sa + Y * ca) / ry
    if edge != "escape":  # (what went out one side comes back in the other: the colours tiled, user 11-02)
        out = lambda v: (v < -1e-9) | (v > 1 + 1e-9)  # (on the edge, or a hair past it: stays on it)
        x = np.clip(np.where(out(x), np.mod(x, 1.0), x), 0, 1)
        y = np.clip(np.where(out(y), np.mod(y, 1.0), y), 0, 1)
    else:  # (escape: gone, the edge's colours stretch in)
        x, y = np.clip(x, 0, 1), np.clip(y, 0, 1)
    if st["squeeze"]:
        sx = np.where(x < cx, 0.5 * x / max(cx, 1e-12), 0.5 + 0.5 * (x - cx) / max(1 - cx, 1e-12))
        sy = np.where(y < cy, 0.5 * y / max(cy, 1e-12), 0.5 + 0.5 * (y - cy) / max(1 - cy, 1e-12))
        if reach:  # (the squeeze fades with the circle too: outside it nothing moves)
            sx, sy = x + (sx - x) * w, y + (sy - y) * w
        x, y = sx, sy
    mid, key = from_unit(x, y, rc, box, own)
    if edge == "wrap":  # (a spot past its key row's notes comes back in on that row's other side: a box's row
        # spans the whole box, so a rectangle is the box's tiling exactly)
        mid = rows.wrapped(mid, key)
    elif edge == "bend":
        mid = rows.unstretched(mid, key, t0, t1)
    return mid, key


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
    area's own measure (area_dist; kx also takes Stretch), and the turn's (spin). spot(u, v) = (tick, key) of % of
    the colour box (numpy arrays). Without "seen" (or the PPQ): the box's own way, 1 / 1."""
    s = st["stretch"] / 100
    c = st["seen"]
    if not c or not ppq or not (st["round"] or st["spin"]):
        return (1 / s, 1.0), (1.0, 1.0)
    t, k = spot(np.array([0.0, 100.0, 0.0]), np.array([0.0, 0.0, 100.0]))
    lx = math.hypot((t[1] - t[0]) / ppq, (k[1] - k[0]) * c)  # (the box's sides as seen, in beats)
    ly = math.hypot((t[2] - t[0]) / ppq, (k[2] - k[0]) * c)
    big = max(lx, ly, 1e-12)
    lx, ly = max(lx, 1e-9 * big) / big, max(ly, 1e-9 * big) / big  # (a flat box: never 0)
    area = (lx / s, ly) if st["round"] else (1 / s, 1.0)
    return area, ((lx, ly) if st["spin"] else (1.0, 1.0))


def area_outline(area, n):
    """The Stir area's outline at distance 1 (area_dist), closed, as (dx, dy) arrays: n + 1 points round a circle,
    only the corners for a square / diamond (straight sides)."""
    if area == "square":
        return np.array([1.0, -1.0, -1.0, 1.0, 1.0]), np.array([1.0, 1.0, -1.0, -1.0, 1.0])
    if area == "diamond":
        return np.array([1.0, 0.0, -1.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0, -1.0, 0.0])
    a = np.linspace(0, 2 * np.pi, n + 1)
    return np.cos(a), np.sin(a)


def flipped(at, mid, key, side, up):
    """Stir's colour flips: spots (mid, key) mirrored at the notes' own outline, sideways across their key row's
    notes (first start <-> last end), upside down across the notes around their time (lowest <-> highest key), so
    a round or slanted shape flips into itself, never into empty space. Done on where the stir looks = the colours
    flipped first, then bent: the swirl stays at the Centre point."""
    if side:
        mid = Rows(at).mirrored(mid, key)
    if up:
        key = Cols(at).mirrored(mid, key)
    return mid, key


class Cols:
    """The lowest and highest key of at's notes around each time (notes put in slices by their middle; a slice
    without notes counts as the nearest one with notes), for Stir's upside-down colour flip."""

    def __init__(self, at):
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
        self.sum = lo[near] + hi[near]

    def slice(self, mid):
        return np.clip(((np.asarray(mid, np.float64) - self.t0) / self.w).astype(np.int64), 0, self.n - 1)

    def mirrored(self, mid, key):
        return self.sum[self.slice(mid)] - np.asarray(key, np.float64)


class Rows:
    """Each key row's notes' span (first start, last end) of at, for Stir's Wrap around, Bend with the outline and
    the sideways colour flip; a key without notes counts as the nearest one with notes (lower on a tie, as nearest
    does)."""

    def __init__(self, at):
        k = np.asarray(at[:, 2], np.int64)
        self.keys, inv = np.unique(k, return_inverse=True)
        self.a = np.full(len(self.keys), np.inf)
        self.b = np.full(len(self.keys), -np.inf)
        np.minimum.at(self.a, inv, np.asarray(at[:, 0], np.float64))
        np.maximum.at(self.b, inv, np.asarray(at[:, 1], np.float64))

    def row(self, key):
        q = np.rint(np.asarray(key, np.float64))
        i = np.clip(np.searchsorted(self.keys, q), 0, len(self.keys) - 1)
        j = np.maximum(i - 1, 0)
        return np.where(np.abs(self.keys[j] - q) <= np.abs(self.keys[i] - q), j, i)

    def wrapped(self, mid, key):
        r = self.row(key)
        a, b = self.a[r], self.b[r]
        w = np.maximum(b - a, 1.0)
        return np.where((mid < a) | (mid > b), a + np.mod(mid - a, w), mid)

    def mirrored(self, mid, key):
        r = self.row(key)
        return self.a[r] + self.b[r] - np.asarray(mid, np.float64)

    def stretched(self, mid, key, t0, t1):
        r = self.row(key)
        a, b = self.a[r], self.b[r]
        return t0 + (mid - a) / np.maximum(b - a, 1.0) * (t1 - t0)

    def unstretched(self, mid, key, t0, t1):
        r = self.row(key)
        a, b = self.a[r], self.b[r]
        return a + (mid - t0) / max(t1 - t0, 1.0) * (b - a)


def nearest(at, mid, key, own=True):
    """For each spot (mid, key): the note of at nearest it: on the nearest key holding notes (the key rounded), the
    note there whose middle is nearest in time. own: spot i is note i's own spot moved: the note itself as near as
    the one found wins (no stir = each note its own)."""
    n = len(at)
    nk = np.asarray(at[:, 2], np.int64)
    nm = (np.asarray(at[:, 0], np.float64) + np.asarray(at[:, 1], np.float64)) / 2
    o = np.lexsort((nm, nk))
    ks, ms = nk[o], nm[o]
    keys, first = np.unique(ks, return_index=True)
    last = np.append(first[1:], n)
    q = np.rint(np.asarray(key, np.float64))
    i = np.clip(np.searchsorted(keys, q), 0, len(keys) - 1)
    j = np.maximum(i - 1, 0)
    row = np.where(np.abs(keys[j] - q) <= np.abs(keys[i] - q), j, i)  # (the nearer key with notes; lower on a tie)
    lo, hi = first[row], last[row]
    mid = np.asarray(mid, np.float64)
    base = min(ms.min(), mid.min())
    gap = 2 * (max(ms.max(), mid.max()) - base) + 2  # (each key row its own stretch of one long sorted line)
    line = ms - base + np.repeat(np.arange(len(keys)) * gap, last - first)
    p = np.clip(np.searchsorted(line, mid - base + row * gap), lo, hi - 1)
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
    o = np.lexsort((s, k))
    ks, ss, es = k[o], s[o], e[o]
    reach = np.maximum.accumulate(es + ks * (1 << 40))  # (the latest end so far, counted per key: keys far apart)
    over = np.zeros(len(o), bool)
    over[1:] = ss[1:] + ks[1:] * (1 << 40) < reach[:-1]
    over[1:] &= ks[1:] == ks[:-1]
    if not over.any():
        return new
    new = new.copy()
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
    ids, which = np.unique(low, return_inverse=True)
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
