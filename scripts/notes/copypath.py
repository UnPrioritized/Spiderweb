"""Copy along a path (user, 2026-10-12): the shape's notes copied again and again along a path through points A, B,
C, ..., a page of the shape (fx.py) like Claw machine / Strum, so the drawing stays editable and every copy follows it.

The points sit on the copies' CORNERS (user): going up, A = the shape's bottom-left corner and B = the last copy's
top-right; where the path turns down at B, B = the top-left of the first copy going down and C = the last one's
bottom-right. Each run of the path going one way (up / down, forwards / back in time) slides the corner on the line
from one to the other, so the copies stay between its points. The points are kept from the group's box (its first
start, lowest key), so moving a shape moves the path and all its copies along. Several shapes with the same page
travel together as one group: each page keeps where the group's box is from its own notes ("grp"), so corners, sizes
and flips work on the whole group. A page's settings (PATH_DEFAULTS' keys):
  "pts"    B, C, ...: [beats, keys] from the group's first start, lowest key (None: B not placed yet, the page does
           nothing). A isn't kept: it's the corner the path starts from (corner_a)
  "mids"   each stretch's middle point (A-B, B-C, ...), kept the same way (None: half way, a straight stretch)
  "kind"   "straight" / "curve" (a bend through the middle point) / "arc" (a piece of a circle through it, round on
           screen): how the stretches whose middle point was dragged bend
  "pattern" a formula laid along the whole path (pattern.py, like a curve's pattern along the line: sideways sizes in
           keys, round as the piano roll looked: its k is the page's "k"), or None
  "mode"   "every": one copy per "every" beats the copies' starts span, spread evenly along each run (a straight
           one: right on the steps), the last one at or before the end; "count": "count" copies in all, spread
           evenly along the copies' way
  "anchor" True: copies sit on the points (a copy's corner right on each point, runs spaced on their own); False
           (user): spaced evenly through the points along the whole path, the corner sliding across the whole path
           in time (a copy on top of an arc sits in its middle); no half copies then
  "half"   Speed Even: where the path turns, the copies carry on past the point at their spacing (both ways), only
           what's before / after its time and under / over its key kept, so the turn is as full as the rest (user,
           from a mock-up; True to start). Either way a copy ends right on each turning point. Number of copies with
           it: each run gets as many of the path's steps as fit, evened out to land on its ends
  "k"      beats per key on screen when the path was made (how long it is, and how round an arc is, as seen then)
  "speed"  "even" / "up" (bunched at A, further apart towards the end) / "down" (the other way)
  "size"   [first copy %, last copy %]: each copy grows / shrinks from its corner on the path (key rows sampled like
           a stretched picture: no gaps between rows)
  "colours" "keep" / "shift" (every colour of copy n = n x step channels on, round the "most" colours) / "turn"
           (round the shape's own colours) / "one" (each copy one colour: n x step); "step" (-15 .. 15)
  "flip"   "none" / "each" (copies where the path goes down are flipped within the group's box, "way": "keys" upside
           down / "time" left-right / "both") / "mirror" (at the path's first peak or dip: what comes after it is the
           part before it mirrored left-right, the copy on the line cut in half there)
  "top"    "last" / "first": which copy is drawn on top where copies cover each other (the one under is cut away
           there, note by note)
  "inside" Keep inside: only the notes from key "low" to key "high" are kept, the shape's own too (the piano roll's
           own rows: a fence that stays put when the shape moves); the rest are cut off
  "grp"    [beats, beats, keys, keys]: the group's box (first start, last end, lowest, highest key) less the
           shape's own (all 0 for one shape)
Keys are rows (a corner = the middle of its row, like the points clicked on the piano roll). Notes past the lowest /
highest key or before the song's start are cut off (user)."""

import json
import math

import numpy as np

from notes.pattern import clean_pattern, pattern_paths

KINDS = ("straight", "curve", "arc")
MODES = ("every", "count")
SPEEDS = ("even", "up", "down")
COLOURS = ("keep", "shift", "turn", "one")
FLIPS = ("none", "each", "mirror")
WAYS = ("keys", "time", "both")
TOPS = ("last", "first")
PATH_DEFAULTS = {"pts": None, "mids": None, "kind": "straight", "mode": "every", "every": 0.25, "count": 8,
                 "k": 0.25, "speed": "even", "size": [100.0, 100.0], "colours": "keep", "step": 1, "most": 15,
                 "flip": "none", "way": "keys", "top": "last", "half": True, "anchor": True,
                 "inside": False, "low": 0, "high": 127, "pattern": None, "grp": [0.0, 0.0, 0.0, 0.0]}
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # the points' names
MOST_POINTS = len(LETTERS) - 1  # B .. Z
EVERY_LIMITS = (1e-6, 1024.0)  # beats
COUNT_LIMITS = (2, 10_000)  # copies (the shape itself counts)
SIZE_LIMITS = (1.0, 1000.0)  # %
STEP_LIMITS = (-15, 15)
KEY_LIMITS = (0, 255)  # Keep inside's Lowest / Highest
MOST_COPIES = 10_000
MOST_NOTES = 20_000_000  # notes with the copies: past it the last copies are left out (memory)
FAR = 1e6  # beats / keys: a path point further than this from A is a broken file
SAMPLES = 1024  # points along each stretch
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
    pts, mids = c.get("pts"), c.get("mids")
    if pts is None and c.get("b") is not None:  # (the first days' pages: B and one middle point)
        pts, mids = [c["b"]], [c.get("mid")]
    if not isinstance(pts, (list, tuple)) or not pts:
        return None
    pts = [_pt(p) for p in pts[:MOST_POINTS]]
    if None in pts:
        return None
    mids = list(mids) if isinstance(mids, (list, tuple)) else []
    out["pts"] = pts
    out["mids"] = [None if m is None else _pt(m) for m in (mids + [None] * len(pts))[:len(pts)]]
    for key in ("half", "anchor", "inside"):
        if isinstance(c.get(key), bool):
            out[key] = c[key]
    for key, choices in (("kind", KINDS), ("mode", MODES), ("speed", SPEEDS), ("colours", COLOURS), ("flip", FLIPS),
                         ("way", WAYS), ("top", TOPS)):
        if c.get(key) in choices:
            out[key] = c[key]
    for key, (lo, hi), whole in (("every", EVERY_LIMITS, False), ("count", COUNT_LIMITS, True),
                                 ("k", (1e-9, 1e9), False), ("step", STEP_LIMITS, True), ("most", (15, 16), True),
                                 ("low", KEY_LIMITS, True), ("high", KEY_LIMITS, True)):
        v = _num(c.get(key, out[key]), lo, hi, whole)
        if v is not None:
            out[key] = v
    size = c.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        got = [_num(v, *SIZE_LIMITS) for v in size]
        if None not in got:
            out["size"] = got
    if out["low"] > out["high"]:
        out["low"], out["high"] = out["high"], out["low"]
    if c.get("pattern") is not None:
        out["pattern"] = clean_pattern(c["pattern"])
    grp = c.get("grp")
    if isinstance(grp, (list, tuple)) and len(grp) == 4:
        got = [_num(v, -FAR, FAR) for v in grp]
        if None not in got:
            out["grp"] = got
    return out


def _sign(v):
    return 1 if v > 1e-9 else -1 if v < -1e-9 else 0


def corner_a(cfg, size):
    """A: the corner of the group's box the path starts from (size = its (beats, keys)): bottom-left, top-left when
    B is below the box's middle key, right when B is before its middle in time. From the box's start, lowest key."""
    w, h = size
    b = cfg["pts"][0]
    return [0.0 if b[0] >= w / 2 else float(w), 0.0 if b[1] >= h / 2 else float(h)]


def all_points(cfg, size):
    """A, B, C, ... as [beats, keys] from the group's first start, lowest key."""
    return [corner_a(cfg, size)] + [list(p) for p in cfg["pts"]]


def bend_of(cfg, i):
    """Stretch i's own middle point, or None (straight)."""
    return cfg["mids"][i] if cfg["kind"] != "straight" else None


def mid_of(cfg, size, i, pts=None):
    """Stretch i's middle point (its own, or half way), like the points."""
    pts = pts or all_points(cfg, size)
    m = bend_of(cfg, i)
    return list(m) if m is not None else [(pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2]


def _stretch(b, m, kind, k, n):
    """One stretch's points from (0, 0) to b, bent through m (None: straight): an (n, 2) array."""
    b = np.asarray(b, float)
    t = np.linspace(0.0, 1.0, n)[:, None]
    if m is not None and kind == "curve":  # (a bend whose middle is the middle point: control = 2 m - b / 2)
        c = 2 * np.asarray(m, float) - b / 2
        return 2 * (1 - t) * t * c + t * t * b
    if m is not None and kind == "arc":
        arc = arc_points(b, m, k, n)
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


def runs(pts):
    """The path's runs going one way: [(first stretch, last stretch, time way, key way)] (ways 1 / -1). A stretch
    level in keys (or still in time) goes on with the run before it."""
    out = []
    for i in range(len(pts) - 1):
        dt, dk = _sign(pts[i + 1][0] - pts[i][0]), _sign(pts[i + 1][1] - pts[i][1])
        if out:
            s, _, wt, wk = out[-1]
            if not (dt and wt and dt != wt) and not (dk and wk and dk != wk):
                out[-1] = (s, i, wt or dt, wk or dk)
                continue
        out.append((i, i, dt, dk))
    return [(s, e, wt or 1, wk or 1) for s, e, wt, wk in out]


_KEPT = {}  # the last few track / places results (one change asked for them 4-5 times: window text, size check,
KEEP = 8    # notes, piano roll line; a formula with many loops made each take about 0.2 s, hunt)


def _kept(fn, cfg, *args):
    """fn(cfg, *args), remembered by the settings it depends on (read-only arrays: callers make new ones)."""
    key = (fn.__name__, json.dumps({k: v for k, v in cfg.items() if k not in ("grp", "most")}, sort_keys=True),
           tuple(float(a) if not isinstance(a, tuple) else tuple(map(float, a)) for a in args))
    got = _KEPT.get(key)
    if got is None:
        got = fn(cfg, *args)
        for a in got:
            a.setflags(write=False)
        if len(_KEPT) >= KEEP:
            _KEPT.pop(next(iter(_KEPT)))
        _KEPT[key] = got
    return got


def track(cfg, size, n=SAMPLES):
    """_track, remembered."""
    return _kept(_track, cfg, tuple(size), n)


def places(cfg, size):
    """_places, remembered."""
    return _kept(_places, cfg, tuple(size))


def _track(cfg, size, n=SAMPLES):
    """The path sampled, each stretch n points (its ends twice where two meet): the points (m, 2) from the group's
    first start, lowest key; the corner on the line there as parts of the box (m, 2: 0 = start / lowest key, 1 = end
    / highest); its run (m,); does it go down there (m,)."""
    pts = all_points(cfg, size)
    got_p, got_f, got_r, got_d = [], [], [], []
    whole = not cfg["anchor"]  # (Copies on the points off: in time the corner slides across the whole path)
    t0, t_span = pts[0][0], pts[-1][0] - pts[0][0]
    segs = []
    for i in range(len(pts) - 1):
        m = bend_of(cfg, i)
        segs.append(np.array(pts[i], float) + _stretch(np.subtract(pts[i + 1], pts[i]),
                                                       None if m is None else np.subtract(m, pts[i]), cfg["kind"],
                                                       cfg["k"], n))
    if cfg.get("pattern"):  # (the formula along the whole path, round as the piano roll looked: cfg "k")
        segs = [np.asarray(a, float).reshape(-1, 2) for a in
                pattern_paths(segs, dict(cfg["pattern"], k=cfg["k"], each=False))]
    for r, (s, e, wt, wk) in enumerate(runs(pts)):
        r0, span = np.array(pts[s], float), np.subtract(pts[e + 1], pts[s])
        if whole:
            r0[0], span[0], wt = t0, t_span, (1 if t_span >= 0 else -1)
        for i in range(s, e + 1):
            seg = segs[i]
            f = np.zeros_like(seg)
            for c in (0, 1):  # (how far along the run, in time and in keys)
                if abs(span[c]) > 1e-9:
                    f[:, c] = np.clip((seg[:, c] - r0[c]) / span[c], 0.0, 1.0)
            if wt < 0:
                f[:, 0] = 1 - f[:, 0]
            if wk < 0:
                f[:, 1] = 1 - f[:, 1]
            dk = np.diff(seg[:, 1])
            dk = np.r_[dk, dk[-1:]]
            got_p.append(seg)
            got_f.append(f)
            got_r.append(np.full(len(seg), r))
            got_d.append((dk < -1e-9) | ((np.abs(dk) <= 1e-9) & (wk < 0)))
    return np.concatenate(got_p), np.concatenate(got_f), np.concatenate(got_r), np.concatenate(got_d)


def eased(u, speed):
    """Places 0..1 along the copies' run, bunched up as Speed says."""
    if speed == "up":
        return u ** SPEED_POWER
    if speed == "down":
        return 1 - (1 - u) ** SPEED_POWER
    return u


def unease(u, speed):
    """eased the other way round: a place 0..1 -> where it is in the copies' even steps."""
    if speed == "up":
        return u ** (1 / SPEED_POWER)
    if speed == "down":
        return 1 - (1 - u) ** (1 / SPEED_POWER)
    return u


def _counted(along, spans, count, speed):
    """Number of copies: places along the samples (sample number + part of the way to the next). Each run gets a
    copy on both its ends (user: the last copy going up and the first going down touch at the point), the rest
    shared out by how long the runs are (bunched up as Speed says, over the whole path)."""
    total = along[-1]
    if total <= 0:
        return np.zeros(count)
    if count < 2 * len(spans):  # (too few for both ends of every run: spread over the whole path)
        spans = [(spans[0][0], spans[-1][1])]
    v = [(unease(along[lo] / total, speed), unease(along[hi] / total, speed)) for lo, hi in spans]
    room = np.array([b - a for a, b in v])
    share = room / room.sum() * (count - 2 * len(spans)) if room.sum() > 0 else np.zeros(len(spans))
    n = np.floor(share).astype(int)
    n[np.argsort(n - share)[:count - 2 * len(spans) - n.sum()]] += 1  # (largest remainders)
    pos = []
    for (lo, hi), (a, b), k in zip(spans, v, n + 2):
        pos.extend(_along_pos(along, lo, hi, eased(np.linspace(a, b, k), speed) * total))
    return np.array(pos)


def _spaced(along, spans, count):
    """Number of copies with Half copies at the points (user: the same spacing up and down): each run gets as many
    of the path's steps (its length / (count - 1)) as fit, evened out so a copy sits on both its ends. (Places along
    the samples, each run's step in length.)"""
    step = along[-1] / max(1, count - 1)
    pos, steps = [], []
    for lo, hi in spans:
        room = along[hi] - along[lo]
        n = min(MOST_COPIES, max(1, math.floor(room / step + 0.5 + 1e-6))) if step > 0 else 1  # (halves: up)
        pos.extend(_along_pos(along, lo, hi, np.linspace(along[lo], along[hi], n + 1)))
        steps.append(room / n)
    return pos[:MOST_COPIES], steps


def _every(along, start, spans, every, speed):
    """One copy every: each run gets one copy per step of time its copies' starts span (start: each sample's, in
    beats), spread EVENLY along it (user: a curve's copies bunched up where it flattened out, timed by the grid); a
    straight run's copies land right on the steps. (Places along the samples, each run's spacing in length.)"""
    pos, steps = [], []
    for lo, hi in spans:
        span, room = abs(start[hi] - start[lo]), along[hi] - along[lo]
        n = min(MOST_COPIES, int(span / every + 1e-9))
        step = room * every / span if span > 0 else 0.0
        u = eased(np.arange(n + 1) / n, speed) * n if n else np.zeros(1)
        pos.extend(_along_pos(along, lo, hi, along[lo] + u * step))
        steps.append(step)
    return pos[:MOST_COPIES], steps


def _along_pos(along, lo, hi, at):
    """Lengths along the path (in run lo..hi's) -> places along the samples."""
    seg = along[lo:hi + 1]
    j = np.clip(np.searchsorted(seg, at, "right") - 1, 0, max(0, hi - lo - 1))
    gap = seg[np.minimum(j + 1, hi - lo)] - seg[j]
    f = np.clip(np.where(gap > 0, (at - seg[j]) / np.where(gap > 0, gap, 1.0), 0.0), 0.0, 1.0)
    return (lo + j + f).tolist()


def _places(cfg, size):
    """Each copy (size = the group's box (beats, keys)): how far it moves from the shape ((n, 2) beats, keys; the
    first (0, 0)), its corner on the path (the point it grows / shrinks round: from the group's first start, lowest
    key) and does the path go down there."""
    p, f, run, down = track(cfg, size)
    wh = np.array(size, float)
    moved = p - f * wh
    m = len(p)
    spans = [(int(np.flatnonzero(run == r)[0]), int(np.flatnonzero(run == r)[-1])) for r in range(int(run.max()) + 1)]
    ways = [(wt, wk) for _, _, wt, wk in runs(all_points(cfg, size))]
    halves = cfg["half"] and cfg["speed"] == "even" and cfg["anchor"]
    half = []
    if not cfg["anchor"]:  # (user: evenly spaced through the points, none sitting on them)
        spans = [(0, m - 1)]
    screen = moved / [cfg["k"], 1.0]  # (how far the copies go on screen; nothing across a turn's jump)
    along = np.r_[0.0, np.cumsum(np.where(run[1:] == run[:-1], np.hypot(*np.diff(screen, axis=0).T), 0.0))]
    if cfg["mode"] == "count":
        if halves:
            pos, steps = _spaced(along, spans, cfg["count"])
            half = _halves(p, along, moved, f, down, spans, steps, ways, size)
        else:
            pos = _counted(along, spans, cfg["count"], cfg["speed"])
        pos = np.array(pos, float)
    else:
        pos, steps = _every(along, moved[:, 0], spans, cfg["every"], cfg["speed"])
        pos = sorted(set(pos) | {float(hi) for _, hi in spans[:-1]})  # (user: a copy ends right on each turn)
        if halves:
            half = _halves(p, along, moved, f, down, spans, steps, ways, size)
        pos = np.array(pos, float)[:MOST_COPIES]
    j = np.clip(np.floor(pos).astype(np.int64), 0, m - 2)
    u = (pos - j)[:, None]
    got = moved[j] + u * (moved[j + 1] - moved[j])
    corner = (f[j] + u * (f[j + 1] - f[j])) * wh
    falls = down[np.where(u[:, 0] >= 1, j + 1, j)]
    cut = np.full((len(pos), 5), np.nan)
    if half:
        at, mv, fr, dn, ct = zip(*half)
        order = np.argsort(np.r_[pos, at], kind="stable")[:MOST_COPIES]
        got = np.concatenate([got, np.array(mv)])[order]
        corner = np.concatenate([corner, np.array(fr) * wh])[order]
        falls = np.r_[falls, dn][order]
        cut = np.concatenate([cut, np.array(ct)])[order]
    got[0] = 0.0
    return got, corner, falls, cut


def _halves(p, val, moved, f, down, spans, steps, ways, size):
    """Half copies at the points (user, 2026-10-12, from a mock-up): where the path turns, the copies before the
    point carry on past it at their spacing; of those, only what pokes in on the near side of the point's time and
    on the inside of its key (under a peak, over a dip) is kept, so the turn is as full as the rest; the other side
    gets the same mirrored left-right at the point (user: whatever the Flip, like the shape's own halves).
    val = how far along each sample is (time for One copy every, length for Number of copies), steps = each run's
    spacing in it, ways = runs' (time, key) ways, size = the box's (beats, keys). Only at peaks and dips. [(place to
    sort it in among the samples,
    how far it moves, its corner as parts of the box, does the path go down there, (cut: beats from the group's
    start, 1 = what's before stays / -1 = after, key, 1 = what's at or under it stays / -1 = at or over, 1 =
    mirrored left-right at the point))]."""
    out = []
    wide, tall = size
    for r in range(len(spans) - 1):
        if ways[r][1] == ways[r + 1][1]:  # (a turn back in time only: no peak or dip to fill; hunt)
            continue
        hi, lo2 = spans[r][1], spans[r + 1][0]
        at, key = p[hi]
        under = 1.0 if ways[r][1] > 0 else -1.0
        b, o, step = hi, hi - 1, steps[r]
        if o < 0 or step <= 0 or abs(val[b] - val[o]) <= 1e-12:
            continue
        d = moved[b] - moved[o]  # (one sample further away from the point)
        if abs(d[0]) <= 1e-12:  # (copies not moving on in time: they'd never get past the point)
            continue
        if d[1] * under < -1e-12:  # (a bend overshooting the point comes into it the wrong way: its peak is inside
            continue               # the stretch, not here; carried on they went straight down in a column, user)
        keep = 1.0 if d[0] > 0 else -1.0
        for k in range(1, MOST_COPIES):
            a = k * step / abs(val[b] - val[o])
            mv = moved[b] + a * d
            if (keep > 0 and mv[0] >= at - 1e-9) or (keep < 0 and mv[0] + wide <= at + 1e-9):
                break  # (nothing of it left on the near side)
            if (under > 0 and mv[1] > key + 1e-9) or (under < 0 and mv[1] + tall < key - 1e-9):
                break  # (wholly past the point's key: nothing kept; a tall tower made thousands of empty ones, hunt)
            if abs(mv[0] - moved[b][0]) > wide + 1e-9 or abs(mv[1] - moved[b][1]) > tall + 1e-9:
                break  # (a whole copy away from the last one: it can't fill the turn any more)
            fr = f[b] + a * (f[b] - f[o])
            out.append((hi + 0.1 + 0.3 * k / (k + 1), mv, fr, bool(down[b]), (at, keep, key, under, 0.0)))
            # (the other side of the point: the same, mirrored left-right there; user: like the shape's own halves)
            out.append((lo2 - 0.1 - 0.3 * k / (k + 1), mv, fr, bool(down[b]), (at, keep, key, under, 1.0)))
    return out


def box_size(box, ppq):
    """A group_box's size: (beats, keys)."""
    return (box[1] - box[0]) / ppq, box[3] - box[2]


def shifts(cfg, ppq, size):
    """Where each copy goes: an (n, 2) int array of (ticks, keys) from the shape, the first (0, 0) = the shape."""
    got = places(cfg, size)[0]
    out = np.column_stack([np.round(got[:, 0] * ppq), np.round(got[:, 1])]).astype(np.int64)
    out[0] = 0
    return out


def _stretch_peak(b, m, kind, k):
    """Beats from the stretch's start where it turns back in keys inside it (a curve's / arc's top or bottom), or
    None."""
    if kind == "curve":  # (keys 2 (1-t) t c + t^2 b: still where t = c / (2c - b))
        cb, ck = 2 * m[0] - b[0] / 2, 2 * m[1] - b[1] / 2
        if abs(2 * ck - b[1]) > 1e-12:
            t = ck / (2 * ck - b[1])
            if 1e-9 < t < 1 - 1e-9:
                return 2 * (1 - t) * t * cb + t * t * b[0]
        return None
    pts = arc_points(b, m, k, SAMPLES)
    if pts is None:
        return None
    dk = np.diff(pts[:, 1])
    moving = np.flatnonzero(np.abs(dk) > 1e-9)
    if len(moving) < 2 or not np.any(np.sign(dk[moving])[1:] != np.sign(dk[moving])[:-1]):
        return None
    return float(arc_centre(b, m, k)[0] * k)  # (an arc turns at its circle's top / bottom: right above its middle)


def peak(cfg, size):
    """Beats from the group's first start where the path first turns back in keys (a peak or a dip: a point where
    it turns, or a bent stretch's top / bottom), or None (it never does)."""
    pts = all_points(cfg, size)
    last = 0
    for i in range(len(pts) - 1):
        b = np.subtract(pts[i + 1], pts[i])
        dk = _sign(b[1])
        if dk and last and dk != last:
            return float(pts[i][0])
        m = bend_of(cfg, i)
        if m is not None:
            got = _stretch_peak(b, np.subtract(m, pts[i]), cfg["kind"], cfg["k"])
            if got is not None:
                return pts[i][0] + got
        last = dk or last
    return None


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


def scaled(x, s, at, key, by_key):
    """Notes x (by_key = (x sorted by key, its keys)) grown / shrunk s times round (tick at, key). Each key row then
    = the row it came from (nearest), like a stretched picture."""
    xs, keys = by_key
    lo, hi = keys[0], keys[-1]
    r0 = math.ceil(key + (lo - 0.5 - key) * s - 1e-9)
    r1 = math.floor(key + (hi + 0.5 - key) * s - 1e-9)
    rows = np.arange(r0, r1 + 1) if r1 >= r0 else np.array([round(key + ((lo + hi) / 2 - key) * s)])
    src = np.clip(np.round(key + (rows - key) / s), lo, hi)
    first, last = np.searchsorted(keys, src, "left"), np.searchsorted(keys, src, "right")
    n = last - first
    idx = np.repeat(first - np.cumsum(n) + n, n) + np.arange(n.sum())
    out = xs[idx].copy()
    out[:, 2] = np.repeat(rows, n)
    start = np.round(at + (out[:, 0] - at) * s).astype(np.int64)
    out[:, 1] = np.maximum(np.round(at + (out[:, 1] - at) * s).astype(np.int64), start + 1)
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


def mirrored_after(out, p, tag=0):
    """The notes before tick p, then the same mirrored left-right after it (a note across p cut there). tag: added to
    the mirrored ones' last column (apply_path's copy numbers: each mirrored copy one of its own)."""
    out = out[out[:, 0] < p].copy()
    out[:, 1] = np.minimum(out[:, 1], p)
    back = out.copy()
    back[:, 0], back[:, 1] = 2 * p - out[:, 1], 2 * p - out[:, 0]
    if tag:
        back[:, -1] += tag
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


def covered(parts, wide):
    """on_top for each bunch of copies whose times overlap (copies apart in time can't cover each other: no cutting
    work for them; 2M notes side by side took 0.8 s, hunt). parts top first."""
    t0 = np.array([int(x[:, 0].min()) for x in parts])
    t1 = np.array([int(x[:, 1].max()) for x in parts])
    order = np.argsort(t0, kind="stable")
    reach = np.maximum.accumulate(t1[order])
    starts = np.flatnonzero(np.r_[True, t0[order][1:] >= reach[:-1]])
    out = []
    for g in np.split(order, starts[1:]):
        g = np.sort(g)  # (top first again)
        out.append(parts[g[0]] if len(g) == 1 else on_top([parts[k] for k in g], wide)[0])
    return np.concatenate(out)


def apply_path(a, cfg, ppq, keys=256, tag=False):
    """Notes a (start, end, key, then columns riding along; with 5+ columns the last is the colour group) and their
    copies along the path. tag: one more column at the end, each note's copy (0 = the shape; Turn into plain
    shapes)."""
    if not len(a) or not cfg:
        return a
    box = group_box(a, cfg, ppq)
    size = box_size(box, ppq)
    got, corner, down, cut = places(cfg, size)
    n = min(len(got), max(1, MOST_NOTES // len(a)))
    d = np.column_stack([np.round(got[:n, 0] * ppq), np.round(got[:n, 1])]).astype(np.int64)
    d[0] = 0
    at, key = box[0] + corner[:n, 0] * ppq, box[2] + corner[:n, 1]
    u = np.arange(n) / max(1, n - 1)
    size = (cfg["size"][0] + (cfg["size"][1] - cfg["size"][0]) * u) / 100
    flips = down[:n] if cfg["flip"] == "each" else np.zeros(n, bool)
    col = a.shape[1] - 1 if a.shape[1] >= 5 and cfg["colours"] != "keep" else None
    own = np.unique(a[:, col]) if col is not None else None
    order = np.argsort(a[:, 2], kind="stable")
    by_key = (a[order], a[order, 2])
    flip_key = None
    parts, total = [], 0
    for i in range(n):
        x = a
        if flips[i]:
            x = flipped(x, box, cfg["way"])
        if abs(size[i] - 1) > 1e-9:
            if x is a:
                x = scaled(x, size[i], at[i], key[i], by_key)
            else:
                if flip_key is None:
                    o = np.argsort(x[:, 2], kind="stable")
                    flip_key = (x[o], x[o, 2])
                x = scaled(x, size[i], at[i], key[i], flip_key)
        else:
            x = x.copy()
        x[:, :2] += d[i, 0]
        x[:, 2] += d[i, 1]
        if not np.isnan(cut[i, 0]):  # (a half copy: only what's on the near side of the point)
            c, k = box[0] + round(cut[i, 0] * ppq), box[2] + round(cut[i, 2])
            if cut[i, 1] > 0:
                x = x[x[:, 0] < c]
                x[:, 1] = np.minimum(x[:, 1], c)
            else:
                x = x[x[:, 1] > c]
                x[:, 0] = np.maximum(x[:, 0], c)
            x = x[x[:, 2] <= k] if cut[i, 3] > 0 else x[x[:, 2] >= k]
            if cut[i, 4] > 0:  # (the point's other side: mirrored left-right there)
                s = x[:, 0].copy()
                x[:, 0], x[:, 1] = 2 * c - x[:, 1], 2 * c - s
        if col is not None:
            x[:, col] = coloured(x[:, col], i, cfg, own)
        if tag:
            x = np.column_stack([x, np.full(len(x), i, x.dtype)])
        total += len(x)
        if total > MOST_NOTES and parts:  # (grown copies have more notes than the shape: counted as made; hunt)
            break
        parts.append(x)
    if cfg["flip"] == "mirror":
        p = peak(cfg, box_size(box, ppq))
        if p is not None:
            at = box[0] + round(p * ppq)
            parts = [x for x in (mirrored_after(x, at, n if tag else 0) for x in parts) if len(x)]
    lo, hi = (max(0, cfg["low"]), min(keys - 1, cfg["high"])) if cfg["inside"] else (0, keys - 1)
    parts = [x[(x[:, 2] >= lo) & (x[:, 2] <= hi) & (x[:, 1] > 0)] for x in parts]
    for x in parts:
        x[:, 0] = np.maximum(x[:, 0], 0)
    parts = [x for x in parts if len(x)]
    if not parts:
        return a[:0]
    if len(parts) > 1:
        wide = int(max(int(x[:, 1].max()) for x in parts)) + 2
        out = covered(parts[::-1] if cfg["top"] == "last" else parts, wide)
    else:
        out = parts[0]
    return out
