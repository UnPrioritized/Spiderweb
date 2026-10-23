"""Gate trim (a user's request, 2026-10-23): on each key, every RUN of notes touching end to end (a gap of even one
tick starts a new run; all colours together) keeps "keep" % of its whole length, counted from its start ("side"
"left") or from its end ("right"). The notes aren't squeezed: the ones inside stay as they are, the note the cut
falls on is cut there and the rest of the run goes (60 + 40 + 70 ticks at 50 % = 85: 60 + 25). A circle at 25 %
from the left = a crescent. A page's settings (fx.py) = TRIM_DEFAULTS' keys."""

import math

import numpy as np

SIDES = ("left", "right")
TRIM_DEFAULTS = {"keep": 100.0, "side": "left"}


def clean_trim(c):
    """A trim from a file -> valid trim, or None when there is none or it would change nothing (100 %)."""
    if not isinstance(c, dict):
        return None
    try:
        keep = float(c.get("keep", 100.0))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(keep) or keep >= 100:
        return None
    return {"keep": max(0.0, keep), "side": c["side"] if c.get("side") in SIDES else "left"}


def runs(a):
    """(order of a by key then time, run number of each note in that order, each run's first start, its last end):
    notes on one key that touch or overlap are one run."""
    order = np.lexsort((a[:, 1], a[:, 0], a[:, 2]))
    s, e, p = a[order, 0], a[order, 1], a[order, 2]
    seg = np.ones(len(order), bool)
    seg[1:] = p[1:] != p[:-1]
    seg_id = np.cumsum(seg) - 1
    t0 = int(s.min())
    span = int(e.max()) - t0 + 2
    # the latest end so far on each key (keys one after another, each lifted above the ones before)
    reach = np.maximum.accumulate(e - t0 + seg_id * span) - seg_id * span + t0
    new = seg.copy()
    new[1:] |= s[1:] > reach[:-1]  # a gap: a new run
    run = np.cumsum(new) - 1
    first = np.flatnonzero(new)
    return order, run, s[first], np.maximum.reduceat(e, first)


def apply_trim(a, tr, ppq=None):
    """a: notes as an int64 array of (start, end, key, velocity, ...) rows (other columns ride along) -> the notes
    trimmed. The length kept = the run's first start to its last end x keep %, rounded to whole ticks."""
    if not len(a) or not tr:
        return a
    order, run, lo, hi = runs(a)
    keep = np.rint((hi - lo) * (tr["keep"] / 100)).astype(np.int64)
    out = a[order]  # (a copy)
    if tr["side"] == "right":
        cut = (hi - keep)[run]
        stay = out[:, 1] > cut
        out, cut = out[stay], cut[stay]
        out[:, 0] = np.maximum(out[:, 0], cut)
    else:
        cut = (lo + keep)[run]
        stay = out[:, 0] < cut
        out, cut = out[stay], cut[stay]
        out[:, 1] = np.minimum(out[:, 1], cut)
    return out
