"""Copy along a path (user, 2026-10-12): the shape's notes copied again and again along a path from A to B, a page of
the shape (fx.py) like Claw machine / Strum, so the drawing stays editable and every copy follows it.

A = where the shape starts in time (copy 1 = the shape itself); the path is kept FROM A, so moving the shape moves
the path and all its copies along (several shapes with the same page travel together as one group). A page's
settings (PATH_DEFAULTS' keys):
  "b"      B, [beats, keys] from A (None: not placed yet, the page does nothing)
  "mid"    the path's middle point, [beats, keys] from A, for "curve" / "arc" (None: half way, a straight line)
  "kind"   "straight" / "curve" (a bend through mid) / "arc" (a piece of a circle through mid, round on screen)
  "mode"   "every": a copy each "every" beats from A (lined up with where the shape starts), the last one at or
           before B; "count": "count" copies in all, spread evenly along the path's length
  "k"      beats per key on screen when the path was made (how long it is, and how round an arc is, as seen then)
Each copy = every note moved by the path's time and keys there (keys rounded to whole rows). Notes past the lowest /
highest key or before the song's start are cut off (user)."""

import math

import numpy as np

KINDS = ("straight", "curve", "arc")
MODES = ("every", "count")
PATH_DEFAULTS = {"b": None, "mid": None, "kind": "straight", "mode": "every", "every": 0.25, "count": 8, "k": 0.25}
EVERY_LIMITS = (1e-6, 1024.0)  # beats
COUNT_LIMITS = (2, 10_000)  # copies (the shape itself counts)
MOST_COPIES = 10_000
MOST_NOTES = 20_000_000  # notes with the copies: past it the last copies are left out (memory)
FAR = 1e6  # beats / keys: a path point further than this from A is a broken file
SAMPLES = 1024  # points along a curve / arc


def _pt(v):
    try:
        b, p = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError):
        return None
    return [b, p] if math.isfinite(b) and math.isfinite(p) and abs(b) <= FAR and abs(p) <= FAR else None


def clean_path(c):
    """A page's settings from a file -> valid ones, or None when there is none or it does nothing (no B yet)."""
    if not isinstance(c, dict):
        return None
    out = dict(PATH_DEFAULTS)
    out["b"] = _pt(c.get("b"))
    if out["b"] is None:
        return None
    out["mid"] = _pt(c.get("mid")) if c.get("mid") is not None else None
    if c.get("kind") in KINDS:
        out["kind"] = c["kind"]
    if c.get("mode") in MODES:
        out["mode"] = c["mode"]
    for key, (lo, hi), whole in (("every", EVERY_LIMITS, False), ("count", COUNT_LIMITS, True), ("k", (1e-9, 1e9), False)):
        try:
            v = float(c.get(key, out[key]))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out[key] = int(round(max(lo, min(hi, v)))) if whole else max(lo, min(hi, v))
    return out


def middle(cfg):
    """The path's middle point from A (beats, keys): its own, or half way to B."""
    b = cfg["b"]
    return cfg["mid"] if cfg.get("mid") is not None and cfg["kind"] != "straight" else [b[0] / 2, b[1] / 2]


def path_points(cfg, n=SAMPLES):
    """Points along the path from A (0, 0) to B, as an (n, 2) array of (beats, keys)."""
    b = np.array(cfg["b"], float)
    t = np.linspace(0.0, 1.0, n)[:, None]
    kind = cfg["kind"] if cfg.get("mid") is not None else "straight"
    m = np.array(middle(cfg), float)
    if kind == "curve":  # (a bend whose middle is the middle point: control = 2 m - b / 2)
        c = 2 * m - b / 2
        return 2 * (1 - t) * t * c + t * t * b
    if kind == "arc":
        arc = arc_points(b, m, cfg["k"], n)
        if arc is not None:
            return arc
    return t * b


def arc_points(b, m, k, n):
    """The circle's piece from (0, 0) through m to b, round on screen (k beats per key), or None when they're in a
    line."""
    pts = np.array([[0.0, 0.0], [m[0] / k, m[1]], [b[0] / k, b[1]]])  # (on screen's proportions)
    (ax, ay), (mx, my), (bx, by) = pts
    d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    size = max(np.abs(pts).max(), 1e-9)
    if abs(d) < 1e-9 * size * size:
        return None
    ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay) + (bx * bx + by * by) * (ay - my)) / d
    uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx) + (bx * bx + by * by) * (mx - ax)) / d
    a0, am, a1 = (math.atan2(y - uy, x - ux) for x, y in pts)
    span = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > span:  # (the middle isn't on the way round: the other way)
        span -= 2 * math.pi
    r = math.hypot(ax - ux, ay - uy)
    ang = a0 + np.linspace(0.0, 1.0, n) * span
    return np.column_stack([(ux + r * np.cos(ang)) * k, uy + r * np.sin(ang)])


def shifts(cfg, ppq):
    """Where each copy goes: an (n, 2) int array of (ticks, keys) from the shape, the first (0, 0) = the shape."""
    pts = path_points(cfg)
    if cfg["mode"] == "count":
        n = cfg["count"]
        screen = pts / [cfg["k"], 1.0]
        along = np.r_[0.0, np.cumsum(np.hypot(*np.diff(screen, axis=0).T))]
        at = np.linspace(0.0, along[-1], n)
        got = np.column_stack([np.interp(at, along, pts[:, 0]), np.interp(at, along, pts[:, 1])])
    else:
        got = [pts[0]]
        step = cfg["every"]
        way = 1.0 if cfg["b"][0] >= 0 else -1.0
        t, i = pts[:, 0] * way, 0  # (time along the way to B: the first crossing of each step, in path order)
        while len(got) < MOST_COPIES:
            want = len(got) * step
            past = np.flatnonzero(t[i:] >= want - 1e-9)
            if not len(past):
                break
            j = i + past[0]
            if j == 0:
                got.append(pts[0].copy())
            else:
                f = (want - t[j - 1]) / (t[j] - t[j - 1]) if t[j] != t[j - 1] else 1.0
                got.append(pts[j - 1] + f * (pts[j] - pts[j - 1]))
            got[-1][0] = want * way  # (exactly on the step)
            i = max(j - 1, 0)
        got = np.array(got)
    out = np.column_stack([np.round(got[:, 0] * ppq), np.round(got[:, 1])]).astype(np.int64)
    out[0] = 0
    return out


def apply_path(a, cfg, ppq, keys=256):
    """Notes a (start, end, key, then columns riding along) and their copies along the path, in copy order (the
    shape's own first)."""
    if not len(a) or not cfg:
        return a
    d = shifts(cfg, ppq)[:max(1, MOST_NOTES // len(a))]
    out = np.repeat(a[None], len(d), axis=0)
    out[:, :, 0] += d[:, :1]
    out[:, :, 1] += d[:, :1]
    out[:, :, 2] += d[:, 1:]
    out = out.reshape(-1, a.shape[1])
    keep = (out[:, 2] >= 0) & (out[:, 2] < keys) & (out[:, 1] > 0)
    out = out[keep]
    out[:, 0] = np.maximum(out[:, 0], 0)
    return out
