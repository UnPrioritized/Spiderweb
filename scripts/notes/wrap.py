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
BINS = 512  # (tilted rows: at most this many strips across the shape; row ends blend between them)


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
    th = math.radians(cfg["angle"])
    if abs(math.sin(th)) > 1e-9:
        out, k = tilted(a, cfg, th)
    else:
        out, k = upright(a, cfg)
        if math.cos(th) < 0:  # (180: the same cuts, slices counted from the other end)
            k = cfg["slices"] - 1 - k
    g = out[:, -1]
    lap, own = g // ROUND, g % ROUND
    out[:, -1] = (own + k % cfg["colours"]) % cfg["most"] + lap * ROUND
    return out


def upright(a, cfg):
    """Angle 0: rows = key rows, cut exactly on ticks -> (the pieces, each one's slice)."""
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
    return out[keep], k[keep]


def tilted(a, cfg, th):
    """Any other angle -> (the pieces, each one's slice). The notes' box is squeezed to a square (x = time, y = keys,
    0..1 each: a circle drawn as an ellipse is a ball), the rows tilted by th: u = along a row, v = across. Rows =
    strips across v (BINS of them, never thinner than a key row's step, so every strip meets each row it crosses);
    each strip's rows' ends = where its notes reach (strip_rows), counted at the strip's middle and blended evenly
    from one strip's middle to the next (held flat, the row ends stepped: slices began again at every strip, a
    sawtooth along flat edges). A note's centre line is cut at the strips' middles; inside such a piece u and the
    row ends all run evenly, so the slice cuts fall exactly."""
    n = cfg["slices"]
    f = shares(n, cfg["round"])
    s0, e0, key = a[:, 0], a[:, 1], a[:, 2]
    t0, w_t = int(s0.min()), max(1, int(e0.max() - s0.min()))
    k0 = int(key.min())
    h = int(key.max()) - k0 + 1
    c, s = math.cos(th), math.sin(th)  # (turned clockwise on screen: v = x s + y c, u = x c - y s)
    c = 0.0 if abs(c) < 1e-9 else c  # (cos 90 is 6e-17, not 0)
    x0, x1 = (s0 - t0) / w_t, (e0 - t0) / w_t
    yc = (key - k0 + 0.5) / h
    v0, v1 = x0 * s + yc * c, x1 * s + yc * c
    vlo, vhi = float(min(v0.min(), v1.min())), float(max(v0.max(), v1.max()))
    w = max((vhi - vlo) / BINS, abs(c) / h, 1e-12)
    nb = int((vhi - vlo) / w) + 1

    def pieces(off):
        """Each note's centre line cut where v crosses vlo + (q + off) w: (note, cell q, x from, x to)."""
        qa = np.floor((v0 - vlo) / w - off).astype(np.int64)
        qb = np.floor((v1 - vlo) / w - off).astype(np.int64)
        step = 1 if s > 0 else -1  # (v grows along a note when s > 0: its cells one after another, left to right)
        reps = np.abs(qb - qa) + 1
        idx = np.repeat(np.arange(len(a)), reps)
        q = qa[idx] + step * (np.arange(len(idx)) - np.repeat(np.cumsum(reps) - reps, reps))
        y = yc[idx]
        xa_, xb_ = (vlo + (q + off) * w - y * c) / s, (vlo + (q + 1 + off) * w - y * c) / s
        return (idx, q, np.clip(np.minimum(xa_, xb_), x0[idx], x1[idx]),
                np.clip(np.maximum(xa_, xb_), x0[idx], x1[idx]))

    # each strip's rows
    sidx, sb, sxa, sxb = pieces(0.0)
    sb = np.clip(sb, 0, nb - 1)
    sy = yc[sidx]
    su_a, su_b = sxa * c - sy * s, sxb * c - sy * s
    rlo, rhi, rstrip = strip_rows(cfg["gaps"], sb, np.minimum(su_a, su_b), np.maximum(su_a, su_b), key[sidx], h, c,
                                  s, w_t)
    lifted = rlo + rstrip * 8.0  # (u is within -2..2: each strip's rows lifted above the ones before)

    def ends_at(q, u):
        """The row ends (lo, hi) at strip q's middle for a point at u (the row reaching it there, else the one just
        before it along the strip, else the first); NaN where strip q has no rows."""
        rs = np.searchsorted(rstrip, q, "left")
        re = np.searchsorted(rstrip, q, "right")
        pos = np.clip(np.searchsorted(lifted, u + q * 8.0, "right") - 1, rs, np.maximum(re - 1, rs))
        ok = (re > rs) & (q >= 0) & (q < nb)
        pos = np.clip(pos, 0, max(0, len(rlo) - 1))
        return np.where(ok, rlo[pos], np.nan), np.where(ok, rhi[pos], np.nan)

    # the pieces cut at the strips' middles: piece in cell q runs from strip q's middle to strip q + 1's
    idx, q, xa, xb = pieces(0.5)
    y = yc[idx]
    um = (xa + xb) / 2 * c - y * s
    la, ha = ends_at(q, um)
    lb, hb = ends_at(q + 1, um)
    la, ha = np.where(np.isnan(la), lb, la), np.where(np.isnan(ha), hb, ha)
    lb, hb = np.where(np.isnan(lb), la, lb), np.where(np.isnan(hb), ha, hb)
    la, ha = np.where(np.isnan(la), um, la), np.where(np.isnan(ha), um, ha)
    lb, hb = np.where(np.isnan(lb), um, lb), np.where(np.isnan(hb), um, hb)
    # along the piece: u = c x - s y, row ends = A + B x (blended by v = s x + c y between the two middles)
    vq = vlo + (q + 0.5) * w
    b_lo, b_hi = (lb - la) * s / w, (hb - ha) * s / w
    a_lo, a_hi = la + (lb - la) * (c * y - vq) / w, ha + (hb - ha) * (c * y - vq) / w

    def share(x):
        lo_, hi_ = a_lo + b_lo * x, a_hi + b_hi * x
        span_ = hi_ - lo_
        return np.where(span_ > 1e-15, (c * x - s * y - lo_) / np.where(span_ > 1e-15, span_, 1.0), 0.5)

    fa, fb = share(xa), share(xb)
    up = fb >= fa  # (slices count up along the piece; else down)
    ka = np.searchsorted(f, np.minimum(fa, fb), "right")  # (the slice at the lower end)
    kb = np.searchsorted(f, np.maximum(fa, fb), "left")  # (the slice just under the upper end)
    kb = np.maximum(kb, ka)  # (a piece lying right on a cut, its share not changing: in the slice after the cut)
    m = kb - ka  # (cuts inside the piece)
    r2 = m + 1
    p = np.repeat(np.arange(len(idx)), r2)
    i = np.arange(len(p)) - np.repeat(np.cumsum(r2) - r2, r2)
    upp = up[p]

    def cut_x(j):
        fk = f[np.clip(np.where(upp, ka[p] + j, kb[p] - 1 - j), 0, max(0, n - 2))]
        coef = c - b_lo[p] - fk * (b_hi[p] - b_lo[p])
        rest = -s * y[p] - a_lo[p] - fk * (a_hi[p] - a_lo[p])
        x = -rest / np.where(np.abs(coef) > 1e-15, coef, 1.0)
        return np.clip(x, xa[p], xb[p])

    left = np.where(i == 0, xa[p], cut_x(i - 1))
    right = np.where(i == m[p], xb[p], cut_x(i))
    left = np.minimum(left, right)
    k = np.where(upp, ka[p] + i, kb[p] - i)
    note = idx[p]
    ta = np.floor(t0 + left * w_t + 0.5).astype(np.int64)
    tb = np.floor(t0 + right * w_t + 0.5).astype(np.int64)
    ta, tb = np.clip(ta, s0[note], e0[note]), np.clip(tb, s0[note], e0[note])
    keep = tb > ta
    note, k, ta, tb = note[keep], k[keep], ta[keep], tb[keep]
    # touching bits of one note in one slice = one note again
    new = np.ones(len(note), bool)
    new[1:] = (note[1:] != note[:-1]) | (k[1:] != k[:-1])
    first = np.flatnonzero(new)
    last = np.append(first[1:], len(note)) - 1
    out = a[note[first]].copy()
    out[:, 0], out[:, 1] = ta[first], tb[last]
    return out, k[first]


def strip_rows(gaps, b, ulo, uhi, key, h, c, s, w_t):
    """The rows of the strips (pieces b = strip, u from ulo to uhi): each row's u ends and strip, in strip order then
    along the strip. "whole": a strip = one row. "each": pieces next to each other along the strip are one row: on
    one key when they touch (no tick between), on keys one apart when no more than one key row's step apart along u
    (1 / (h |s|)); anything else = a gap."""
    if gaps != "each":
        nb = int(b.max()) + 1
        lo, hi = np.full(nb, np.inf), np.full(nb, -np.inf)
        np.minimum.at(lo, b, ulo)
        np.maximum.at(hi, b, uhi)
        has = np.isfinite(lo)
        return lo[has], hi[has], np.flatnonzero(has)
    order = np.lexsort((ulo, b))
    bo, lo_o, hi_o, ko = b[order], ulo[order], uhi[order], key[order]
    seg = np.ones(len(order), bool)
    seg[1:] = bo[1:] != bo[:-1]
    sid = np.cumsum(seg) - 1
    lift = sid * 4.0  # (u is within -2..2: each strip lifted above the ones before)
    reach = np.maximum.accumulate(hi_o + lift) - lift  # (the furthest u so far in the strip)
    step = 1 / (h * abs(s))
    tick = 1 / w_t
    gap = np.zeros(len(order), bool)
    prev_reach = reach[:-1]
    d = lo_o[1:] - prev_reach
    same = ko[1:] == ko[:-1]
    near = np.abs(ko[1:] - ko[:-1]) == 1
    gap[1:] = np.where(same, d > tick * 0.5 * abs(c) + 1e-12, ~near | (d > step * 1.001))
    first = np.flatnonzero(seg | gap)
    return lo_o[first], np.maximum.reduceat(hi_o, first), bo[first]
