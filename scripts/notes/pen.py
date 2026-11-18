"""A freehand stroke's Thickness (sh["thick"], keys): a wide pen. Every key the band around the stroke covers gets
ONE note for as long as the band covers it (user: a filled ribbon, not stacked copies of the line). The width runs
ACROSS the stroke as the piano roll looked when the stroke was drawn (sh["k"] = beats per key on screen, like
Straighten / arcs), so up-and-down parts get wider in time too. Round ends; a stroke crossing itself = one band.
1 = the plain line (paths.path_notes), as before."""

import math

import numpy as np

from notes.shrink import near_rows

THICK_MIN, THICK_MAX = 1.0, 128.0


def clean_thick(value):
    """A Thickness from a file / box: 1 .. 128 keys, two decimals; anything broken = 1 (the plain line)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return THICK_MIN
    if not math.isfinite(v):
        return THICK_MIN
    return round(min(max(v, THICK_MIN), THICK_MAX), 2)


def thick_of(sh):
    """The shape's Thickness when it's a wide pen (> 1), else None."""
    if sh.get("kind") != "free":
        return None
    t = sh.get("thick")
    return t if isinstance(t, (int, float)) and t > THICK_MIN else None


def band_notes(path, thick, k, ppq):
    """(tick, key) points of the stroke -> (start, end, key) notes of the band thick keys wide around it.
    k = beats per key as drawn (the band is round there). A key row counts along its middle: a flat stroke on a
    key with Thickness 3 covers that key and one each side."""
    path = np.asarray(path, float).reshape(-1, 2)
    if not len(path):
        return np.zeros((0, 3), np.int64)
    kt = max(float(k), 1e-9) * ppq  # ticks per key on screen
    r = thick / 2
    if len(path) == 1:
        path = np.vstack([path, path])
    seg = np.column_stack([path[:-1, 0], path[:-1, 1] * kt, path[1:, 0], path[1:, 1] * kt])
    lo_key = math.ceil(float(path[:, 1].min()) - r)
    hi_key = math.floor(float(path[:, 1].max()) + r)
    keys = np.arange(lo_key, hi_key + 1)
    rows = near_rows(seg, keys * kt, r * kt)
    out = [(start, max(end, start + 1), key)
           for key, spans in zip(keys.tolist(), rows) for a, b in spans
           for start, end in [(math.floor(a + 0.5), math.floor(b + 0.5))]]
    return np.asarray(out, np.int64).reshape(-1, 3)
