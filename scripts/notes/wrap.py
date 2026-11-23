"""Wrapped slices (Shape trim's second mode; a tester's "beach ball" request, 2026-11-23, PLAN.md): the shape's notes
are cut into curved slices as if it were wrapped round a ball or a can, each slice its own colour. A page of the
Shape trim window ("tool": "trim" with "mode": "wrap"; trim.py keeps the row trim).

Each row (all notes on one key from its first start to its last end: "gaps" "whole"; or each run of touching notes
on its own, as the row trim counts them: "each") is cut into "slices" pieces. Cut k of n sits at share f(k) of the
row: f = r (1 - cos(180 k / n)) / 2 + (1 - r) k / n, r = "round" / 100 (100 % = Round: thin at the ends, widest in
the middle, the way stripes on a ball look; 0 % = Even: equal widths). Notes keep their lengths except where a cut
falls: there they're cut in two. Cut ticks round halves up; a slice thinner than a tick vanishes (the last slice
starting on a tick wins it). Slice k (from 0 at the row's start) = the note's own colour + (k round "colours"), round
"most" (15, 16 with "Use channel 10", as Copy along a path's): slice 1 keeps the shape's colour (user 2026-11-23); a
Colours round of the shape's (custom.ROUND) stays.

"angle" (degrees) = where the two points sit: 0 = top and bottom (rows cut along time). Other angles: stage B."""

import math

import numpy as np

from notes.custom import ROUND

GAPS = ("whole", "each")
WRAP_DEFAULTS = {"slices": 8, "colours": 8, "angle": 0.0, "round": 100.0, "gaps": "whole", "own": False, "most": 15}
MOST_SLICES = 100_000  # (no real limit, user 2026-11-23: only a guard against nonsense)
MOST_COLOURS = 16  # (16 with "Use channel 10"; the window allows areas.most_colours)


def clean_wrap(c):
    """The wrap settings of a page from a file -> valid ones (WRAP_DEFAULTS' keys), or None when it would change
    nothing (1 slice)."""
    def num(key, lo, hi, whole=False):
        try:
            v = float(c.get(key, WRAP_DEFAULTS[key]))
        except (TypeError, ValueError):
            v = math.nan
        if not math.isfinite(v):
            v = float(WRAP_DEFAULTS[key])
        v = max(lo, min(hi, v))
        return int(round(v)) if whole else round(v, 3)

    out = {"slices": num("slices", 1, MOST_SLICES, True), "colours": num("colours", 1, MOST_COLOURS, True),
           "angle": round(((num("angle", -1e9, 1e9) + 180) % 360) - 180, 3), "round": num("round", 0, 100),
           "gaps": c["gaps"] if c.get("gaps") in GAPS else "whole", "own": c.get("own") is True,
           "most": 16 if c.get("most") == 16 else 15}
    out["colours"] = min(out["colours"], out["most"])
    return out if out["slices"] > 1 else None


def shares(n, rnd):
    """The cuts' shares of a row, f(1) .. f(n - 1) (rising): Round blended with Even by rnd (0..100)."""
    k = np.arange(1, n, dtype=float)
    r = rnd / 100
    return r * (1 - np.cos(np.pi * k / n)) / 2 + (1 - r) * k / n


def rows_of(a, gaps):
    """(order of a by key then time, each note's row number in that order, each row's first start, its last end)."""
    if gaps == "each":
        from notes.trim import runs
        return runs(a)
    order = np.lexsort((a[:, 0], a[:, 2]))
    p = a[order, 2]
    new = np.ones(len(order), bool)
    new[1:] = p[1:] != p[:-1]
    row = np.cumsum(new) - 1
    first = np.flatnonzero(new)
    return order, row, np.minimum.reduceat(a[order, 0], first), np.maximum.reduceat(a[order, 1], first)


def apply_wrap(a, cfg, ppq=960):
    """a: notes as an int64 array of (start, end, key, velocity, ..., colour group) rows (other columns ride along;
    the last one = the colour group, custom.ROUND rounds in it) -> the notes cut into slices and coloured."""
    if not len(a) or not cfg:
        return a
    n = cfg["slices"]
    f = shares(n, cfg["round"])
    order, row, lo, hi = rows_of(a, cfg["gaps"])
    b = a[order]
    span = (hi - lo).astype(float)[row]
    base = lo[row]
    s, e = b[:, 0], b[:, 1]
    # cut j (0-based, share f[j]) of a row lands on tick base + floor(span f[j] + 0.5): it is at or before tick t
    # when f[j] < (t - base + 0.5) / span. ka = how many cuts are at or before the note's start (its first piece's
    # slice), kb = at or before its last tick: the cuts ka .. kb - 1 fall inside it.
    safe = np.maximum(span, 1.0)
    ka = np.searchsorted(f, (s - base + 0.5) / safe - 1e-12, "left")
    kb = np.searchsorted(f, (e - 1 - base + 0.5) / safe - 1e-12, "left")
    reps = kb - ka + 1
    idx = np.repeat(np.arange(len(b)), reps)
    j = np.arange(len(idx)) - np.repeat(np.cumsum(reps) - reps, reps)
    k = ka[idx] + j  # each piece's slice
    tick = lambda m: base[idx] + np.floor(span[idx] * f[np.minimum(m, n - 2)] + 0.5).astype(np.int64)
    start = np.where(j == 0, s[idx], tick(k - 1))
    end = np.where(k == kb[idx], e[idx], tick(k))
    out = b[idx]
    out[:, 0], out[:, 1] = start, end
    keep = end > start
    out, k = out[keep], k[keep]
    g = out[:, -1]
    lap, own = g // ROUND, g % ROUND
    out[:, -1] = (own + k % cfg["colours"]) % cfg["most"] + lap * ROUND
    return out
