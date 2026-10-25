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
import random

import numpy as np

from files.speed import loops
from notes.custom import ROUND

MODES = ("shuffle", "scatter", "clumps")
SPLIT = ("scatter", "clumps")  # (Split modes: work from one colour, 2 colours when Colours isn't set)
BY = ("note", "chord", "run")
PATTERNS = ("bands", "swirl", "wedges")  # (Split: Clumps' areas)
TIGHT, MOST_TIGHT = 1.0, 50.0  # Swirl tightness: turns from the centre point to the edge
MOST_PUSH = 8  # Push steps each way (each one = the areas bunched a step more toward that side)
MOST_SEED = 2 ** 31 - 1
MOST = 15  # colours a set may use (16 with Use channel 10: the shape's "most", App.notes_tracks)
SET = 15  # colours in a colour set (always: the ticks change no colour)


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and x == x


def clean_recolour(r):
    """A saved sh["recolour"] -> {"mode", "amount": 0..100 (3 decimals), "seed": int, and only when set: "shares"
    [% per place], "pins" [places whose share stays when others change: the Shares window], "colours" n (2..16),
    "mix" True, "by" "chord" / "run"}, or None (Off)."""
    if not isinstance(r, dict) or r.get("mode") not in MODES:
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
    return out


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


def framed_centre(rc, c, back=False):
    """A Centre point c (% of the box) as shown on the flipped / turned shape (rc "frame"); back: a shown one ->
    the setting."""
    a, b, cc, d = rc.get("frame") or SAME
    if not back:  # (shown = the frame undone: its rows and columns swapped)
        b, cc = cc, b
    x, y = c[0] - 50, c[1] - 50
    return [50 + a * x + b * y, 50 + cc * x + d * y]


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


def recoloured(notes, tracks, rc, held=False, gate=False, box=None):
    """The shape's notes and tracks after its Recolour rc (rule_of). Nothing to shuffle (plan): unchanged. First
    the notes are settled as their colours before would sound (render's overlap rule on each colour: a note cut
    where a later one of its colour starts on its key, notes starting together made one), so a new colour never
    frees a cut note: the notes stay as heard with Recolour off. held: sustained (overlaps kept, render held): not
    settled, its notes of one colour may still sound together. gate: the shape is gate flipped (places_of). box:
    the box (box_of) the dice and areas count in, e.g. a sliced piece's whole shape's (so a piece keeps the colours
    it had in the whole); None = the notes' own."""
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
    box = box or box_of(notes)
    at = places_of(notes, rc, gate, box)
    corner = (box[0], box[2])
    pick = dice(at, low, rc["seed"], 1, corner)
    roll = dice(at, low, rc["seed"], 0, corner)
    clumps = rc["mode"] == "clumps"
    if clumps:  # (every note takes the colour of the area it lands in: the throw = where it lands)
        n = sum(len(p) for p in pals) if rc.get("mix") and len(pals) > 1 else max(len(p) for p in pals)
        pick, roll = areas(at, rc, pick, n, box), np.zeros(len(notes))
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
        return notes, lap + kept_apart(notes, low, new, np.unique(pool), pick)
    new = low.copy()
    for s, p in enumerate(pals):
        m = np.flatnonzero(which == s)
        if len(p) < 2 or not len(m):
            continue
        got = np.where(change[m], take(p, weights(len(p), shares), low[m], pick[m]), low[m])
        new[m] = kept_apart(notes[m], low[m], got, p, pick[m])
    return notes, lap + new


def areas(notes, rc, u, n, box=None):
    """Split: Clumps: where each note's middle lands, 0 <= place < 1 across the colour areas (Shares cut it in
    order), in the shape's box (time and keys each 0..1: the same on any zoom). Bands = stripes across the Angle's
    way (0 = upright, in time order), Wedges = slices around the Centre point, Swirl = wedges twisted more near it
    (Swirl tightness = turns out to the edge). Push bunches the areas toward a side (the box squeezed there).
    Rough edges (Amount) = each place moved by up to about one area: mostly by a smooth noise (ragged clumps), a
    little by the note's own throw u."""
    s, e, k = (np.asarray(notes[:, c], np.float64) for c in (0, 1, 2))
    t0, t1, k0, k1 = box or box_of(notes)  # (box: counted in another box, recoloured)
    x = ((s + e) / 2 - t0) / max(t1 - t0, 1.0)
    y = (k - k0) / (k1 - k0) if k1 > k0 else np.full(len(k), 0.5)
    f = rc.get("frame")
    if f and f[1]:  # (turned since: each place turned back, places_of did the flips)
        x, y = 0.5 + f[1] * (y - 0.5), 0.5 + f[2] * (x - 0.5)
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
        q = dx * np.cos(a) + dy * np.sin(a)
        ends = [(X - cx) * np.cos(a) + (Y - cy) * np.sin(a) for X in (0, 1) for Y in (0, 1)]
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
