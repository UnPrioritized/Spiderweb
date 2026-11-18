"""A freehand stroke's Thickness (sh["thick"], keys): a wide pen. Every key the band around the stroke covers gets
ONE note for as long as the band covers it (user: a filled ribbon, not stacked copies of the line). The width runs
ACROSS the stroke as the piano roll looked when the stroke was drawn (sh["k"] = beats per key on screen, like
Straighten / arcs), so up-and-down parts get wider in time too. A stroke crossing itself = one band.
1 = the plain line (paths.path_notes), as before.

The pen's shape (sh["pen"], user 2026-11-19, stage D): "ends" round (default) / flat (the band stops square across
the stroke at its ends); "taper" 0-50 = % of the stroke's length at each end where it thins to nothing (one number,
both ends alike: user); "speed" True = faster drawing thinner, from sh["times"] (ms since the first point, one per
drawn point: only strokes drawn since this exists have them; without them the tick does nothing).
Round + no taper / speed = shrink.near_rows (compiled); anything else = band_rows here (exact: the band between
two points whose widths differ is the convex hull of their two circles)."""

import math

import numpy as np

from files.speed import loops
from notes.shrink import near_rows

THICK_MIN, THICK_MAX = 1.0, 128.0
ENDS = ("round", "flat")
TAPER_MAX = 50
PEN_DEFAULT = {"ends": "round", "taper": 0, "speed": False}
SPEED_LEAST = 0.25  # the thinnest share of the width a fast part gets
SPEED_SMOOTH = 5  # drawn points each side averaged for the speed (the mouse's own jitter)
FINE = 2000  # a varying band: the stroke cut into at most this many pieces (widths change smoothly along it)


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


def clean_pen(value):
    """sh["pen"] from a file: only what differs from PEN_DEFAULT, None when nothing does."""
    if not isinstance(value, dict):
        return None
    out = {}
    if value.get("ends") in ENDS and value["ends"] != "round":
        out["ends"] = value["ends"]
    try:
        t = float(value.get("taper", 0))
        t = round(min(max(t, 0.0), TAPER_MAX), 2) if math.isfinite(t) else 0
    except (TypeError, ValueError):
        t = 0
    if t:
        out["taper"] = t
    if value.get("speed") is True:
        out["speed"] = True
    return out or None


def pen_of(sh):
    """The pen's whole settings (PEN_DEFAULT filled in)."""
    return dict(PEN_DEFAULT, **(clean_pen(sh.get("pen")) or {}))


def clean_times(value, n):
    """sh["times"] from a file: n whole ms, never going back; else None."""
    if not isinstance(value, list) or len(value) != n or n < 2:
        return None
    try:
        t = [int(round(float(x))) for x in value]
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(0 <= x < 1 << 40 for x in t):
        return None
    return np.maximum.accumulate(np.asarray(t, np.int64)).tolist()


def speed_share(pts, times, k):
    """For drawn points (beats, keys) and their times: (place along the stroke 0..1, width share) where a part drawn
    faster than the stroke's usual speed is thinner (share 1 at the usual speed and slower, sqrt(usual / speed)
    faster, at least SPEED_LEAST). None when there's nothing to go by."""
    p = np.asarray(pts, float).reshape(-1, 2)
    t = np.asarray(times, float)
    if len(p) < 3 or len(t) != len(p):
        return None
    d = np.hypot(np.diff(p[:, 0]) / max(k, 1e-9), np.diff(p[:, 1]))  # (as on screen, keys)
    s = np.r_[0.0, np.cumsum(d)]
    if s[-1] <= 0:
        return None
    w = SPEED_SMOOTH
    v = np.empty(len(p))
    for i in range(len(p)):  # (distance / time over a few points each side)
        a, b = max(0, i - w), min(len(p) - 1, i + w)
        dt = t[b] - t[a]
        v[i] = (s[b] - s[a]) / dt if dt > 0 else np.nan
    if np.isnan(v).all():
        return None
    v = np.where(np.isnan(v), np.nanmax(v), v)
    usual = float(np.median(v[v > 0])) if (v > 0).any() else 0.0
    if usual <= 0:
        return None
    share = np.clip(np.sqrt(usual / np.maximum(v, 1e-12)), SPEED_LEAST, 1.0)
    return s / s[-1], share


def taper_share(f, taper):
    """Width share at places f (0..1 along the stroke) with that much taper (% of the length at each end): a round
    swell from nothing at the tip to the full width (a quarter sine)."""
    if taper <= 0:
        return np.ones_like(f)
    t = taper / 100.0
    up = np.sin(np.pi / 2 * np.clip(f / t, 0, 1))
    down = np.sin(np.pi / 2 * np.clip((1 - f) / t, 0, 1))
    return up * down


def band_notes(path, thick, k, ppq, pen=None, speed=None):
    """(tick, key) points of the stroke -> (start, end, key) notes of the band thick keys wide around it.
    k = beats per key as drawn (the band is round there). A key row counts along its middle: a flat stroke on a
    key with Thickness 3 covers that key and one each side. pen: pen_of's settings; speed: speed_share's (place,
    share) when "speed" is on and the stroke has times."""
    path = np.asarray(path, float).reshape(-1, 2)
    if not len(path):
        return np.zeros((0, 3), np.int64)
    kt = max(float(k), 1e-9) * ppq  # ticks per key on screen
    r = thick / 2
    if len(path) == 1:
        path = np.vstack([path, path])
    pen = pen or PEN_DEFAULT
    lo_key = math.ceil(float(path[:, 1].min()) - r)
    hi_key = math.floor(float(path[:, 1].max()) + r)
    keys = np.arange(lo_key, hi_key + 1)
    xy = np.column_stack([path[:, 0], path[:, 1] * kt])  # (ticks, keys as ticks on screen)
    if pen["ends"] == "round" and not pen["taper"] and speed is None:
        seg = np.column_stack([xy[:-1], xy[1:]])
        rows = near_rows(seg, keys * kt, r * kt)
    else:
        xy = finer(xy)
        s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
        f = s / s[-1] if s[-1] > 0 else np.zeros(len(s))
        share = taper_share(f, pen["taper"])
        if speed is not None:
            share = share * np.interp(f, *speed)
        rows = band_rows(xy, r * kt * share, keys * kt, pen["ends"] == "flat", s)
    out = [(start, max(end, start + 1), key)
           for key, spans in zip(keys.tolist(), rows) for a, b in spans
           for start, end in [(math.floor(a + 0.5), math.floor(b + 0.5))]]
    return np.asarray(out, np.int64).reshape(-1, 3)


def band_of(path, st, k, ppq, k_drawn=None):
    """band_notes for shape / stroke st (its "thick", "pen", "times" on its drawn "pts"): path = its line in
    (tick, key), k = beats per key where it's round. k_drawn: the k its own points are in (a live shape's stroke:
    its u / v), else k."""
    pen = pen_of(st)
    speed = None
    if pen["speed"]:
        times = clean_times(st.get("times"), len(st.get("pts") or ()))
        if times:
            speed = speed_share(st["pts"], times, k if k_drawn is None else k_drawn)
    return band_notes(path, st["thick"], k, ppq, pen, speed)


def finer(xy):
    """The stroke with points added so no piece is longer than 1 / FINE of it (widths change smoothly)."""
    d = np.hypot(*np.diff(xy, axis=0).T)
    total = d.sum()
    if total <= 0:
        return xy
    n = np.maximum(1, np.ceil(d / (total / FINE))).astype(int)
    if (n == 1).all():
        return xy
    parts = [xy[i] + (xy[i + 1] - xy[i]) * (np.arange(m) / m)[:, None] for i, m in enumerate(n.tolist())]
    return np.vstack(parts + [xy[-1:]])


def band_rows(xy, r, ys, flat, s):
    """For each height y in ys: where the band of the stroke xy (points; r = its half width at each point, varying)
    covers the line, [[a, b]] merged. Between two points the band is the convex hull of their circles (a circle
    whose radius changes evenly along the piece sweeps exactly that): on a line, between the farthest crossings
    of the two circles and the two tangent lines joining them. flat: no round ends; the band stops square across
    the stroke at both ends (pieces within a width of an end are cut there). s: each point's length along."""
    out = [[] for _ in range(len(ys))]
    if len(xy) < 2:
        return out
    edges, clips, first, cnt = _pen_edges(xy, r, ys, flat, s)
    if not cnt.sum():
        return out
    ys = np.ascontiguousarray(ys, np.float64)
    fast = getattr(loops(), "pen_rows", None)  # (our own compiled loop, several cores: the very same numbers)
    got = None if fast is None else fast(edges, clips, ys, first, cnt)
    row, lo, hi = got if got is not None else _pen_rows_np(edges, clips, ys, first, cnt)
    cuts = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)] if len(row) else []
    for s0, s1 in zip(cuts[:-1].tolist(), cuts[1:].tolist()) if len(row) else ():
        out[int(row[s0])] = np.column_stack([lo[s0:s1], hi[s0:s1]]).tolist()
    return out


def _pen_edges(xy, r, ys, flat, s):
    """band_rows' pieces, each worked out once: edges (n x 15: ends, half widths, has = both tangent lines exist,
    tangent 1 P -> Q, tangent 2), clips (n x 10: the square cuts a flat end puts on the pieces near it: on, end
    point, the way into the stroke), and each piece's first line + how many lines it reaches."""
    n = len(xy) - 1
    ax, ay, bx, by = xy[:-1, 0], xy[:-1, 1], xy[1:, 0], xy[1:, 1]
    ra, rb = r[:-1], r[1:]
    reach = np.maximum(ra, rb)
    first = np.searchsorted(ys, np.minimum(ay, by) - reach, "left")
    cnt = np.maximum(np.searchsorted(ys, np.maximum(ay, by) + reach, "right") - first, 0)
    dx, dy = bx - ax, by - ay
    length = np.hypot(dx, dy)
    cols = [ax, ay, bx, by, ra, rb]
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.where(length > 0, (ra - rb) / np.where(length > 0, length, 1), 2.0)
        has = np.abs(a) < 1 - 1e-12  # (else one circle holds the other: its crossings are the band's)
        ux, uy = dx / length, dy / length
        b = np.sqrt(np.maximum(1 - a * a, 0))
        cols.append(has.astype(np.float64))
        for sg in (1.0, -1.0):  # (the two tangent lines, P -> Q)
            nx, ny = a * ux - sg * b * uy, a * uy + sg * b * ux
            cols += [ax + ra * nx, ay + ra * ny, bx + rb * nx, by + rb * ny]
    edges = np.column_stack(cols)
    edges[~has, 7:] = 0.0  # (unused: no NaN handed on)
    clips = np.zeros((n, 10))
    if flat:  # (square ends: what's behind the start / past the end cut off, across the stroke there)
        total, far = s[-1], 2 * float(r.max())
        for c, end, near in ((0, 0, s[:-1] <= far), (5, n, s[1:] >= total - far)):
            ox, oy = xy[end]
            step = range(1, n + 1) if end == 0 else range(n - 1, -1, -1)  # (into the stroke from that end)
            j = next((j for j in step if xy[j, 0] != ox or xy[j, 1] != oy), None)
            if j is None:
                continue
            vx, vy = xy[j, 0] - ox, xy[j, 1] - oy
            m = math.hypot(vx, vy)
            clips[near, c:c + 5] = [1.0, ox, oy, vx / m, vy / m]  # (kept: the side where (p - end) . v >= 0)
    return edges, clips, first.astype(np.int64), cnt.astype(np.int64)


def _pen_rows_np(edges, clips, ys, first, cnt):
    """pen_rows' NumPy twin (the compiled loop's sums in the same order): (row, lo, hi) of every line's joined
    stretches, line by line."""
    e = np.repeat(np.arange(len(edges)), cnt)
    row = np.repeat(first, cnt) + np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    y = ys[row]
    p, c = edges[e], clips[e]
    ax, ay, bx, by, ra, rb = (p[:, i] for i in range(6))
    lo, hi = np.full(len(e), np.inf), np.full(len(e), -np.inf)
    for px, py, pr in ((ax, ay, ra), (bx, by, rb)):  # the two circles (a line can cross a tangent and the far
        h = pr * pr - (y - py) * (y - py)  # circle with hull between them)
        w = np.sqrt(np.maximum(h, 0))
        lo = np.where(h >= 0, np.minimum(lo, px - w), lo)
        hi = np.where(h >= 0, np.maximum(hi, px + w), hi)
    has = p[:, 6] != 0
    with np.errstate(divide="ignore", invalid="ignore"):
        for t in (7, 11):  # the two tangent lines
            px, py, qx, qy = p[:, t], p[:, t + 1], p[:, t + 2], p[:, t + 3]
            span = qy - py
            on = has & ((py - y) * (qy - y) <= 0) & (np.abs(span) > 1e-12)
            x = px + (y - py) / np.where(on, span, 1.0) * (qx - px)
            lo = np.where(on, np.minimum(lo, x), lo)
            hi = np.where(on, np.maximum(hi, x), hi)
            level = has & ~on & (np.abs(span) <= 1e-12) & (np.abs(py - y) <= 1e-12)  # (lying on the line)
            lo = np.where(level, np.minimum(lo, np.minimum(px, qx)), lo)
            hi = np.where(level, np.maximum(hi, np.maximum(px, qx)), hi)
        for k in (0, 5):  # the flat ends' square cuts
            cut = c[:, k] != 0
            if not cut.any():
                continue
            ox, oy, vx, vy = c[:, k + 1], c[:, k + 2], c[:, k + 3], c[:, k + 4]
            upright = cut & (np.abs(vx) < 1e-12)
            gone = upright & ((y - oy) * vy < 0)
            lo, hi = np.where(gone, np.inf, lo), np.where(gone, -np.inf, hi)
            edge = ox - (y - oy) * vy / np.where(upright, 1.0, vx)
            lo = np.where(cut & ~upright & (vx > 0), np.maximum(lo, edge), lo)
            hi = np.where(cut & ~upright & (vx < 0), np.minimum(hi, edge), hi)
    got = hi > lo
    row, lo, hi = row[got], lo[got], hi[got]
    if not len(row):
        return row, lo, hi
    order = np.lexsort((hi, lo, row))
    row, lo, hi = row[order], lo[order], hi[order]
    # joined line by line (touching ones too): a stretch starting past the furthest end so far on its line = new
    start = np.r_[True, row[1:] != row[:-1]]
    reach = _running_max(hi, start)
    new = start | (lo > np.r_[-np.inf, reach[:-1]])
    at = np.flatnonzero(new)
    return row[at], lo[at], np.maximum.reduceat(hi, at)


def _running_max(v, start):
    """np.maximum.accumulate of v, starting again at every True in start."""
    out = np.empty_like(v)
    idx = np.flatnonzero(start).tolist() + [len(v)]
    for a, b in zip(idx[:-1], idx[1:]):
        out[a:b] = np.maximum.accumulate(v[a:b])
    return out
