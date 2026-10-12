"""Copy along a path (user, 2026-10-12): the shape's notes copied again and again along a path from A to B, a page of
the shape (fx.py) like Claw machine / Strum, so the drawing stays editable and every copy follows it.

A = where the selected shapes' notes start in time, at their middle key (copy 1 = the shapes themselves); the path is
kept FROM A, so moving a shape moves the path and all its copies along. Several shapes with the same page travel
together as one group: each page keeps where the group's box is from its own notes ("grp"), so sizes and flips work
on the whole group. A page's settings (PATH_DEFAULTS' keys):
  "b"      B, [beats, keys] from A (None: not placed yet, the page does nothing)
  "mid"    the path's middle point, [beats, keys] from A, for "curve" / "arc" (None: half way, a straight line)
  "kind"   "straight" / "curve" (a bend through mid) / "arc" (a piece of a circle through mid, round on screen)
  "mode"   "every": a copy each "every" beats from A (lined up with where the shape starts), the last one at or
           before B; "count": "count" copies in all, spread evenly along the path's length
  "k"      beats per key on screen when the path was made (how long it is, and how round an arc is, as seen then)
  "speed"  "even" / "up" (bunched at A, further apart towards B) / "down" (the other way)
  "size"   [first copy %, last copy %]: each copy grows / shrinks from the group's start, middle key (key rows
           sampled like a stretched picture: no gaps between rows)
  "colours" "keep" / "shift" (every colour of copy n = n x step channels on, round the "most" colours) / "turn"
           (round the shape's own colours) / "one" (each copy one colour: n x step); "step" (-15 .. 15)
  "flip"   "none" / "each" (copies where the path goes down are flipped within the group's box, "way": "keys" upside
           down / "time" left-right / "both") / "mirror" (at the path's first peak or dip: what comes after it is the
           part before it mirrored left-right, the copy on the line cut in half there)
  "top"    "last" / "first": which copy is drawn on top where copies cover each other (the one under is cut away
           there, note by note)
  "grp"    [beats, beats, keys, keys]: the group's box (first start, last end, lowest, highest key) less the
           shape's own (all 0 for one shape)
Each copy = every note moved by the path's time and keys there (keys rounded to whole rows). Notes past the lowest /
highest key or before the song's start are cut off (user)."""

import math

import numpy as np

KINDS = ("straight", "curve", "arc")
MODES = ("every", "count")
SPEEDS = ("even", "up", "down")
COLOURS = ("keep", "shift", "turn", "one")
FLIPS = ("none", "each", "mirror")
WAYS = ("keys", "time", "both")
TOPS = ("last", "first")
PATH_DEFAULTS = {"b": None, "mid": None, "kind": "straight", "mode": "every", "every": 0.25, "count": 8, "k": 0.25,
                 "speed": "even", "size": [100.0, 100.0], "colours": "keep", "step": 1, "most": 15, "flip": "none",
                 "way": "keys", "top": "last", "grp": [0.0, 0.0, 0.0, 0.0]}
EVERY_LIMITS = (1e-6, 1024.0)  # beats
COUNT_LIMITS = (2, 10_000)  # copies (the shape itself counts)
SIZE_LIMITS = (1.0, 1000.0)  # %
STEP_LIMITS = (-15, 15)
MOST_COPIES = 10_000
MOST_NOTES = 20_000_000  # notes with the copies: past it the last copies are left out (memory)
FAR = 1e6  # beats / keys: a path point further than this from A is a broken file
SAMPLES = 1024  # points along a curve / arc
SPEED_POWER = 2.0  # how bunched up Speed up / Slow down are


def _pt(v):
    try:
        b, p = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError):
        return None
    return [b, p] if math.isfinite(b) and math.isfinite(p) and abs(b) <= FAR and abs(p) <= FAR else None


def _num(v, lo, hi, whole=False):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    v = max(lo, min(hi, v))
    return int(round(v)) if whole else v


def clean_path(c):
    """A page's settings from a file -> valid ones, or None when there is none or it does nothing (no B yet)."""
    if not isinstance(c, dict):
        return None
    out = dict(PATH_DEFAULTS)
    out["b"] = _pt(c.get("b"))
    if out["b"] is None:
        return None
    out["mid"] = _pt(c.get("mid")) if c.get("mid") is not None else None
    for key, choices in (("kind", KINDS), ("mode", MODES), ("speed", SPEEDS), ("colours", COLOURS), ("flip", FLIPS),
                         ("way", WAYS), ("top", TOPS)):
        if c.get(key) in choices:
            out[key] = c[key]
    for key, (lo, hi), whole in (("every", EVERY_LIMITS, False), ("count", COUNT_LIMITS, True),
                                 ("k", (1e-9, 1e9), False), ("step", STEP_LIMITS, True), ("most", (15, 16), True)):
        v = _num(c.get(key, out[key]), lo, hi, whole)
        if v is not None:
            out[key] = v
    size = c.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        got = [_num(v, *SIZE_LIMITS) for v in size]
        if None not in got:
            out["size"] = got
    grp = c.get("grp")
    if isinstance(grp, (list, tuple)) and len(grp) == 4:
        got = [_num(v, -FAR, FAR) for v in grp]
        if None not in got:
            out["grp"] = got
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


def arc_centre(b, m, k):
    """The middle of the circle through (0, 0), m and b, round on screen (k beats per key): (x on screen = beats / k,
    keys), with the three points on screen, or None when they're in a line."""
    pts = np.array([[0.0, 0.0], [m[0] / k, m[1]], [b[0] / k, b[1]]])  # (on screen's proportions)
    (ax, ay), (mx, my), (bx, by) = pts
    d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    size = max(np.abs(pts).max(), 1e-9)
    if abs(d) < 1e-9 * size * size:
        return None
    ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay) + (bx * bx + by * by) * (ay - my)) / d
    uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx) + (bx * bx + by * by) * (mx - ax)) / d
    return ux, uy, pts


def arc_points(b, m, k, n):
    """The circle's piece from (0, 0) through m to b, round on screen (k beats per key), or None when they're in a
    line."""
    got = arc_centre(b, m, k)
    if got is None:
        return None
    ux, uy, pts = got
    (ax, ay) = pts[0]
    a0, am, a1 = (math.atan2(y - uy, x - ux) for x, y in pts)
    span = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > span:  # (the middle isn't on the way round: the other way)
        span -= 2 * math.pi
    r = math.hypot(ax - ux, ay - uy)
    ang = a0 + np.linspace(0.0, 1.0, n) * span
    return np.column_stack([(ux + r * np.cos(ang)) * k, uy + r * np.sin(ang)])


def eased(u, speed):
    """Places 0..1 along the copies' run, bunched up as Speed says."""
    if speed == "up":
        return u ** SPEED_POWER
    if speed == "down":
        return 1 - (1 - u) ** SPEED_POWER
    return u


def _crossings(pts, t, targets):
    """For each time in targets (going up), the path's point where t first reaches it, searching on from the last
    one; stops at the first never reached."""
    got, i = [], 0
    for want in targets:
        past = np.flatnonzero(t[i:] >= want - 1e-9)
        if not len(past):
            break
        j = i + past[0]
        if j == 0:
            got.append(pts[0].copy())
        else:
            f = (want - t[j - 1]) / (t[j] - t[j - 1]) if t[j] != t[j - 1] else 1.0
            got.append(pts[j - 1] + f * (pts[j] - pts[j - 1]))
        i = max(j - 1, 0)
    return got


def places(cfg):
    """Where each copy goes, before rounding: an (n, 2) float array of (beats, keys) from A, the first (0, 0)."""
    pts = path_points(cfg)
    if cfg["mode"] == "count":
        screen = pts / [cfg["k"], 1.0]
        along = np.r_[0.0, np.cumsum(np.hypot(*np.diff(screen, axis=0).T))]
        at = eased(np.linspace(0.0, 1.0, cfg["count"]), cfg["speed"]) * along[-1]
        return np.column_stack([np.interp(at, along, pts[:, 0]), np.interp(at, along, pts[:, 1])])
    way = 1.0 if cfg["b"][0] >= 0 else -1.0
    t = pts[:, 0] * way  # (time along the way to B: the first crossing of each step, in path order)
    step = cfg["every"]
    n = len(_crossings(pts, t, np.arange(min(MOST_COPIES, int(t.max() / step + 1e-9) + 1)) * step))
    targets = np.arange(n) * step
    if cfg["speed"] != "even" and n > 1:
        targets = eased(targets / targets[-1], cfg["speed"]) * targets[-1]
    got = np.array(_crossings(pts, t, targets)).reshape(-1, 2)
    if cfg["speed"] == "even":
        got[:, 0] = targets[:len(got)] * way  # (exactly on the steps)
    return got


def shifts(cfg, ppq):
    """Where each copy goes: an (n, 2) int array of (ticks, keys) from the shape, the first (0, 0) = the shape."""
    got = places(cfg)
    out = np.column_stack([np.round(got[:, 0] * ppq), np.round(got[:, 1])]).astype(np.int64)
    out[0] = 0
    return out


def going_down(got):
    """For each copy (places), does the path go down there (to the next copy; the last: from the one before)?"""
    if len(got) < 2:
        return np.zeros(len(got), bool)
    dk = np.diff(got[:, 1])
    return np.r_[dk, dk[-1]] < -1e-9


def peak(cfg):
    """Beats from A where the path first turns back (a peak or a dip in keys), or None (it never does). Exact for a
    curve and an arc (their top / bottom), else where the points along it turn."""
    kind = cfg["kind"] if cfg.get("mid") is not None else "straight"
    if kind == "straight":
        return None
    b, m = cfg["b"], middle(cfg)
    if kind == "curve":  # (keys 2 (1-t) t c + t^2 b: still where t = c / (2c - b))
        cb, ck = 2 * m[0] - b[0] / 2, 2 * m[1] - b[1] / 2
        if abs(2 * ck - b[1]) > 1e-12:
            t = ck / (2 * ck - b[1])
            if 1e-9 < t < 1 - 1e-9:
                return 2 * (1 - t) * t * cb + t * t * b[0]
        return None
    pts = path_points(cfg)
    dk = np.diff(pts[:, 1])
    moving = np.flatnonzero(np.abs(dk) > 1e-9)
    if len(moving) < 2:
        return None
    sign = np.sign(dk[moving])
    turn = np.flatnonzero(sign[1:] != sign[:-1])
    if not len(turn):
        return None
    got = arc_centre(b, m, cfg["k"])  # (an arc turns at its circle's top / bottom: right above its middle)
    return float(got[0] * cfg["k"]) if got else float(pts[moving[turn[0]] + 1, 0])


def group_box(a, cfg, ppq):
    """The group's box (first start, last end, lowest, highest key: ticks / keys) from this shape's notes a."""
    g = cfg.get("grp") or (0, 0, 0, 0)
    return (int(a[:, 0].min()) + round(g[0] * ppq), int(a[:, 1].max()) + round(g[1] * ppq),
            int(a[:, 2].min()) + round(g[2]), int(a[:, 2].max()) + round(g[3]))


def flipped(x, box, way):
    """Notes x flipped within box (group_box): "keys" upside down, "time" left-right, "both"."""
    x = x.copy()
    if way in ("keys", "both"):
        x[:, 2] = box[2] + box[3] - x[:, 2]
    if way in ("time", "both"):
        s = x[:, 0].copy()
        x[:, 0], x[:, 1] = box[0] + box[1] - x[:, 1], box[0] + box[1] - s
    return x


def scaled(x, s, box, by_key):
    """Notes x (by_key = (x sorted by key, its keys)) grown / shrunk s times from the box's start, middle key. Each key
    row then = the row it came from (nearest), like a stretched picture."""
    xs, keys = by_key
    t0, mid = box[0], (box[2] + box[3]) / 2
    lo, hi = keys[0], keys[-1]
    r0 = math.ceil(mid + (lo - 0.5 - mid) * s - 1e-9)
    r1 = math.floor(mid + (hi + 0.5 - mid) * s - 1e-9)
    rows = np.arange(r0, r1 + 1) if r1 >= r0 else np.array([round(mid + ((lo + hi) / 2 - mid) * s)])
    src = np.clip(np.round(mid + (rows - mid) / s), lo, hi)
    first, last = np.searchsorted(keys, src, "left"), np.searchsorted(keys, src, "right")
    n = last - first
    idx = np.repeat(first - np.cumsum(n) + n, n) + np.arange(n.sum())
    out = xs[idx].copy()
    out[:, 2] = np.repeat(rows, n)
    start = t0 + np.round((out[:, 0] - t0) * s).astype(np.int64)
    out[:, 1] = np.maximum(t0 + np.round((out[:, 1] - t0) * s).astype(np.int64), start + 1)
    out[:, 0] = start
    return out


def coloured(tracks, i, cfg, own):
    """Copy i's tracks (colour groups): Keep / Shift / Turn within its own (own = the shape's, sorted) / One each."""
    how, step, most = cfg["colours"], cfg["step"], cfg["most"]
    if how == "shift":
        return (tracks + i * step) % most
    if how == "turn":
        return own[(np.searchsorted(own, tracks) + i * step) % len(own)]
    if how == "one":
        return np.full(len(tracks), (i * step) % most, tracks.dtype)
    return tracks


def mirrored_after(out, p):
    """The notes before tick p, then the same mirrored left-right after it (a note across p cut there)."""
    out = out[out[:, 0] < p].copy()
    out[:, 1] = np.minimum(out[:, 1], p)
    back = out.copy()
    back[:, 0], back[:, 1] = 2 * p - out[:, 1], 2 * p - out[:, 0]
    return np.concatenate([out, back])


def _merged(starts, ends):
    """Spans (any order, may overlap) -> sorted spans that don't (touching ones joined)."""
    order = np.argsort(starts, kind="stable")
    s, e = starts[order], ends[order]
    reach = np.maximum.accumulate(e)
    new = np.r_[True, s[1:] > reach[:-1]]
    first = np.flatnonzero(new)
    return s[first], np.maximum.reduceat(e, first) if len(first) else e[:0]


def _minus(x, cs, ce, wide):
    """Notes x with the parts inside the spans (cs, ce: sorted, apart; key x wide + tick) taken out: what's left of
    each note, in pieces."""
    if not len(cs) or not len(x):
        return x
    s, e = x[:, 2] * wide + x[:, 0], x[:, 2] * wide + x[:, 1]
    i0 = np.searchsorted(ce, s, "right")
    i1 = np.searchsorted(cs, e, "left")
    m = np.maximum(i1 - i0, 0)
    rep = np.repeat(np.arange(len(x)), m + 1)
    j = np.arange(len(rep)) - np.repeat(np.cumsum(m + 1) - (m + 1), m + 1)
    lo = np.where(j == 0, s[rep], ce[np.minimum(i0[rep] + j - 1, len(ce) - 1)])
    hi = np.where(j == m[rep], e[rep], cs[np.minimum(i0[rep] + j, len(cs) - 1)])
    keep = hi > lo
    out = x[rep[keep]].copy()
    base = out[:, 2] * wide
    out[:, 0], out[:, 1] = lo[keep] - base, hi[keep] - base
    return out


def on_top(parts, wide):
    """parts: each copy's notes, top first -> all of them, a copy's notes cut away where copies above cover them
    (halves at a time: the top half, then the bottom half less what the top half covers)."""
    if len(parts) == 1:
        x = parts[0]
        cs, ce = _merged(x[:, 2] * wide + x[:, 0], x[:, 2] * wide + x[:, 1])
        return x, cs, ce
    half = len(parts) // 2
    top, ts, te = on_top(parts[:half], wide)
    low, ls, le = on_top(parts[half:], wide)
    low = _minus(low, ts, te, wide)
    cs, ce = _merged(np.r_[ts, ls], np.r_[te, le])
    return np.concatenate([top, low]), cs, ce


def apply_path(a, cfg, ppq, keys=256):
    """Notes a (start, end, key, then columns riding along; with 5+ columns the last is the colour group) and their
    copies along the path."""
    if not len(a) or not cfg:
        return a
    got = places(cfg)
    got = got[:max(1, MOST_NOTES // len(a))]
    d = np.column_stack([np.round(got[:, 0] * ppq), np.round(got[:, 1])]).astype(np.int64)
    d[0] = 0
    n = len(d)
    box = group_box(a, cfg, ppq)
    u = np.arange(n) / max(1, n - 1)
    size = (cfg["size"][0] + (cfg["size"][1] - cfg["size"][0]) * u) / 100
    flips = going_down(got) if cfg["flip"] == "each" else np.zeros(n, bool)
    col = a.shape[1] - 1 if a.shape[1] >= 5 and cfg["colours"] != "keep" else None
    own = np.unique(a[:, col]) if col is not None else None
    order = np.argsort(a[:, 2], kind="stable")
    by_key = (a[order], a[order, 2])
    flip_key = None
    parts = []
    for i in range(n):
        x = a
        if flips[i]:
            x = flipped(x, box, cfg["way"])
        if abs(size[i] - 1) > 1e-9:
            if x is a:
                x = scaled(x, size[i], box, by_key)
            else:
                if flip_key is None:
                    o = np.argsort(x[:, 2], kind="stable")
                    flip_key = (x[o], x[o, 2])
                x = scaled(x, size[i], box, flip_key)
        else:
            x = x.copy()
        x[:, :2] += d[i, 0]
        x[:, 2] += d[i, 1]
        if col is not None:
            x[:, col] = coloured(x[:, col], i, cfg, own)
        parts.append(x)
    if cfg["flip"] == "mirror":
        p = peak(cfg)
        if p is not None:
            at = box[0] + round(p * ppq)
            parts = [x for x in (mirrored_after(x, at) for x in parts) if len(x)]
    parts = [x[(x[:, 2] >= 0) & (x[:, 2] < keys) & (x[:, 1] > 0)] for x in parts]
    for x in parts:
        x[:, 0] = np.maximum(x[:, 0], 0)
    parts = [x for x in parts if len(x)]
    if not parts:
        return a[:0]
    if len(parts) > 1:
        wide = int(max(int(x[:, 1].max()) for x in parts)) + 2
        out = on_top(parts[::-1] if cfg["top"] == "last" else parts, wide)[0]
    else:
        out = parts[0]
    return out
