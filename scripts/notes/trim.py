"""Gate trim (a user's request, 2026-10-23): on each key, every RUN of notes touching end to end (a gap of even one
tick starts a new run; all colours together) keeps "keep" % of its whole length (up to 3 decimals), or with "by"
"ticks" a fixed "length" (beats, whole ticks at the PPQ; a run shorter stays whole), counted from its start ("side"
"left") or from its end ("right"). The notes aren't squeezed: the ones inside stay as they are, the note the cut
falls on is cut there and the rest of the run goes (60 + 40 + 70 ticks at 50 % = 85: 60 + 25). A circle at 25 %
from the left = a crescent. A page's settings (fx.py) = TRIM_DEFAULTS' keys ("length" only with "by" "ticks")."""

import math

import numpy as np

SIDES = ("left", "right")
BY = ("pct", "ticks")
TRIM_DEFAULTS = {"keep": 100.0, "side": "left", "by": "pct", "length": 1.0}
MAX_LENGTH = 1e9  # beats (no real limit, user 2026-10-23: only a guard against nonsense)


def clean_trim(c):
    """A trim from a file -> valid trim, or None when there is none or it would change nothing (100 %)."""
    if not isinstance(c, dict):
        return None
    try:
        keep = float(c.get("keep", 100.0))
    except (TypeError, ValueError):
        keep = math.nan
    keep = round(max(0.0, min(100.0, keep)), 3) if math.isfinite(keep) else 100.0
    out = {"keep": keep, "side": c["side"] if c.get("side") in SIDES else "left"}
    if c.get("by") == "ticks":
        try:
            length = float(c.get("length", 1.0))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(length) or length <= 0:
            return None
        return dict(out, by="ticks", length=min(length, MAX_LENGTH))
    return out if keep < 100 else None


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


def kept_ticks(lo, hi, tr, ppq):
    """How many ticks of each run (first start lo .. last end hi) stay: keep % rounded, halves up (user 2026-10-23:
    7 ticks kept 4 but 5 kept 2), or the length in ticks (at least 1)."""
    if tr.get("by") == "ticks":
        return np.minimum(hi - lo, max(1, math.floor(tr["length"] * ppq + 0.5)))
    return np.floor((hi - lo) * (tr["keep"] / 100) + 0.5 + 1e-9).astype(np.int64)


def apply_trim(a, tr, ppq=960):
    """a: notes as an int64 array of (start, end, key, velocity, ...) rows (other columns ride along) -> the notes
    trimmed."""
    if not len(a) or not tr:
        return a
    order, run, lo, hi = runs(a)
    keep = kept_ticks(lo, hi, tr, ppq)
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
