"""The claw machine: changes a shape's notes after they're made (the shape itself stays as drawn), like the claw
machine of a well-known piano roll. sh["claw"] = {"mode", "period", "trash", "dist", "stretch", "short"}.

By time ("time"): the notes' time is cut into periods ("period" beats, counted from the shape's first note), each
period into n slices, and slice k is thrown away ("trash" = [k, n], or None): notes in it go, notes crossing its
edges are cut there, and the gap closes (later notes move earlier). "stretch": the result is stretched back to the
shape's first length (the notes get longer too). "dist" (-100 .. 100) then bends time inside each period-long
stretch of the new timeline: right (+) = long notes first, getting faster; left (-) = the other way round. A
note's start and end are bent each in their own period. "short": notes the claw left shorter than SHORT beats are
removed (notes that were that short already stay)."""

import numpy as np

PERIODS = (1, 2, 4, 8, 16)  # beats
TRASHES = ((1, 4), (2, 4), (3, 4), (4, 4), None, (1, 3), (2, 3), (3, 3), None, (1, 2), (2, 2))  # None = a gap
BEND = np.log(50.0)  # how much the dial bends time when turned all the way
SHORT = 1 / 24  # beats
CLAW_DEFAULTS = {"mode": "time", "period": 1, "trash": None, "dist": 0.0, "stretch": False, "short": False}


def clean_claw(c):
    """A claw from a file -> valid claw, or None when there is none or it would change nothing."""
    if not isinstance(c, dict):
        return None
    out = dict(CLAW_DEFAULTS)
    if c.get("period") in PERIODS:
        out["period"] = c["period"]
    t = c.get("trash")
    if isinstance(t, (list, tuple)) and tuple(t) in TRASHES:
        out["trash"] = list(t)
    try:
        out["dist"] = max(-100.0, min(100.0, float(c.get("dist", 0.0))))
    except (TypeError, ValueError):
        pass
    out["stretch"] = c.get("stretch") is True
    out["short"] = c.get("short") is True
    if out["trash"] is None and out["dist"] == 0:
        return None
    return out


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
    return a
