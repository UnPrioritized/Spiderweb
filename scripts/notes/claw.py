"""The claw machine: changes a shape's notes after they're made (the shape itself stays as drawn), like the claw
machine of a well-known piano roll. A page's settings (fx.py) = {"mode", and that mode's settings}.

By time ("time"): the notes' time is cut into periods ("period" beats, counted from the shape's first note), each
period into n slices, and slice k is thrown away ("trash" = [k, n], or None): notes in it go, notes crossing its
edges are cut there, and the gap closes (later notes move earlier). "stretch": the result is stretched back to the
shape's first length (the notes get longer too). "dist" (-100 .. 100) then bends time inside each period-long
stretch of the new timeline: right (+) = long notes first, getting faster; left (-) = the other way round. A
note's start and end are bent each in their own period. Notes squeezed onto one key and tick: only the longest
stays. "short": notes the claw left shorter than SHORT beats are removed (notes that were that short already stay).

Counting modes: "notes" (in time order, low to high within a chord), "keys" (counted up from the shape's lowest
key, so a key with no notes still counts), "chords" (notes starting together = one): keep "keep", throw away
"skip", keep "keep", ... "random": keeps about "pct" % of the notes, picked by "seed" (the same pick every time).
"shorten" (these modes): the notes picked to go stay, "cut" % of their length long (at least 1 tick)."""

import math

import numpy as np

PERIODS = (1, 2, 4, 8, 16)  # beats (the list's choices; any other period can be typed)
TRASHES = ((1, 4), (2, 4), (3, 4), (4, 4), None, (1, 3), (2, 3), (3, 3), None, (1, 2), (2, 2))  # None = a gap
MAX_PERIOD = 1000  # beats (a typed period)
MAX_SLICES = 1000  # (a typed Trash every k/n: n at most this)
BEND = np.log(50.0)  # how much the dial bends time when turned all the way
SHORT = 1 / 24  # beats
MODES = ("time", "notes", "keys", "chords", "random")
COUNTS = ("notes", "keys", "chords")
CLAW_DEFAULTS = {"mode": "time", "period": 1, "trash": None, "dist": 0.0, "stretch": False, "short": False,
                 "keep": 1, "skip": 1, "pct": 50.0, "seed": 1, "shorten": False, "cut": 25.0}
SETTINGS = {"time": ("period", "trash", "dist", "stretch", "short"),
            "notes": ("keep", "skip", "shorten", "cut"), "keys": ("keep", "skip", "shorten", "cut"),
            "chords": ("keep", "skip", "shorten", "cut"), "random": ("pct", "seed", "shorten", "cut")}
MAX_COUNT = 1000


def clean_claw(c):
    """A claw from a file -> valid claw, or None when there is none or it would change nothing."""
    if not isinstance(c, dict):
        return None
    out = dict(CLAW_DEFAULTS)
    if c.get("mode") in MODES:
        out["mode"] = c["mode"]
    for key, lo, hi in (("keep", 1, MAX_COUNT), ("skip", 0, MAX_COUNT), ("pct", 0, 100), ("seed", 0, 10 ** 9),
                         ("cut", 1, 99)):
        try:
            v = float(c.get(key, out[key]))
            if math.isfinite(v):  # (not a number / endless: the default)
                out[key] = max(lo, min(hi, v if key in ("pct", "cut") else int(v)))
        except (TypeError, ValueError, OverflowError):
            pass
    out["period"] = clean_period(c.get("period")) or out["period"]
    out["trash"] = clean_trash(c.get("trash"))
    try:
        v = float(c.get("dist", 0.0))
        if math.isfinite(v):
            out["dist"] = max(-100.0, min(100.0, v))
    except (TypeError, ValueError):
        pass
    out["stretch"] = c.get("stretch") is True
    out["short"] = c.get("short") is True
    out["shorten"] = c.get("shorten") is True
    mode = out["mode"]
    if (mode == "time" and out["trash"] is None and out["dist"] == 0 or mode in COUNTS and out["skip"] == 0 or
            mode == "random" and out["pct"] == 100):
        return None
    return {k: out[k] for k in ("mode",) + SETTINGS[mode]}


def clean_period(p):
    """A period in beats (any number above 0 up to MAX_PERIOD; a whole one as an int) or None."""
    if isinstance(p, bool):
        return None
    try:
        p = float(p)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(p) and 1e-6 <= p <= MAX_PERIOD):
        return None
    return int(p) if p == int(p) else p


def clean_trash(t):
    """[k, n] (whole numbers, 1 <= k <= n <= MAX_SLICES) or None."""
    if not isinstance(t, (list, tuple)) or len(t) != 2 or any(isinstance(x, bool) for x in t):
        return None
    try:
        k, n = (float(x) for x in t)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (1 <= k <= n <= MAX_SLICES and k == int(k) and n == int(n)):
        return None
    return [int(k), int(n)]


def _bend(y, period, dist):
    """Bend time y (ticks from the first note) inside each period."""
    a = BEND * dist / 100
    w = np.floor(y / period)
    x = y / period - w
    return (w + (1 - np.exp(-a * x)) / (1 - np.exp(-a))) * period


def apply_claw(a, claw, ppq):
    """a: notes as an int64 array whose first two columns are start and end ticks (the other columns ride along)
    -> the notes after the claw."""
    if not len(a) or not claw:
        return a
    if claw["mode"] != "time":
        keep = _picked(a, claw)
        if not claw["shorten"]:
            return a[keep]
        a = a.copy()
        go = ~keep
        a[go, 1] = a[go, 0] + np.maximum(1, np.rint((a[go, 1] - a[go, 0]) * claw["cut"] / 100)).astype(np.int64)
        return a
    s, e = a[:, 0].astype(float), a[:, 1].astype(float)
    t0 = s.min()
    period = claw["period"] * ppq
    xs, xe = s - t0, e - t0
    if claw["trash"]:
        k, n = claw["trash"]
        cut = period / n
        lo = (k - 1) * cut

        def close(x):  # time with every thrown-away slice taken out
            return x - cut * np.floor(x / period) - np.clip(np.mod(x, period) - lo, 0, cut)

        xs, xe = close(xs), close(xe)
        if claw["stretch"]:
            full = float((e - t0).max())
            left = float(close(np.array([full]))[0])
            if left > 0:
                xs, xe = xs * (full / left), xe * (full / left)
    keep = xe - xs > 1e-6
    if claw["dist"]:
        xs, xe = _bend(xs, period, claw["dist"]), _bend(xe, period, claw["dist"])
    if claw["short"]:
        short = SHORT * ppq
        keep &= (xe - xs >= short - 1e-6) | (e - s < short)
    a = a[keep].copy()
    starts = np.rint(xs[keep] + t0).astype(np.int64)
    a[:, 0] = starts
    a[:, 1] = np.maximum(np.rint(xe[keep] + t0).astype(np.int64), starts + 1)
    return unstacked(a)


def unstacked(a):
    """Notes squeezed onto the same key and start tick -> only the longest of them (FL keeps one, user 2026-10-18);
    the rest keep their order."""
    order = np.lexsort((a[:, 0] - a[:, 1], a[:, 0], a[:, 2]))
    k, s = a[order, 2], a[order, 0]
    first = np.ones(len(a), bool)
    first[1:] = (k[1:] != k[:-1]) | (s[1:] != s[:-1])
    if first.all():
        return a
    return a[np.sort(order[first])]


def _picked(a, claw):
    """Which notes a counting / random claw keeps."""
    mode = claw["mode"]
    if mode == "random":
        order = np.lexsort((a[:, 2], a[:, 0]))  # (the same notes get the same pick whatever order they came in)
        keep = np.empty(len(a), bool)
        keep[order] = np.random.default_rng(claw["seed"]).random(len(a)) < claw["pct"] / 100
        return keep
    if mode == "notes":
        n = np.empty(len(a), np.int64)
        n[np.lexsort((a[:, 2], a[:, 0]))] = np.arange(len(a))
    elif mode == "keys":
        n = a[:, 2] - a[:, 2].min()
    else:
        n = np.unique(a[:, 0], return_inverse=True)[1].reshape(-1)
    return n % (claw["keep"] + claw["skip"]) < claw["keep"]
