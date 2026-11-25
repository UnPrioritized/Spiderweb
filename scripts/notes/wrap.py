"""Wrapped slices (Trim shape's second mode; a tester's "beach ball" request, 2026-11-23, PLAN.md): the shape's notes
are cut into curved slices as if it were wrapped round a ball or a can, each slice its own colour. A page of the
Trim shape window ("tool": "trim" with "mode": "wrap"; trim.py keeps the row trim).

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
SMOOTH = 1  # (tilted: the outline's sides smoothed this many times, each row end with the ones above and below)
TIE = 0.3  # (tilted: every cut this share of a key before its place, so no row lies right on one)
SLIVER_PASSES = 8  # (tilted: slivers folded in this many rounds at most, shortest first)
SLIVER_DRAWN = 0.005  # (the same on a drawn outline: crumbs only)
SLIVER = 0.03  # (tilted: a piece shorter than this share of the shape's length, trapped in another slice, joins it)


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
    # (rounded: cos 90 = 6e-17 made the middle cut 0.4999999999999999, so rows an odd number of ticks long put it a
    # tick to the left, user 2026-11-23: "a few ticks out of place" at 100 %, none at 0 %)
    return np.round(r * (1 - np.cos(np.pi * k / n)) / 2 + (1 - r) * k / n, 12)


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


def apply_wrap(a, cfg, ppq=960, tag=False, outline=None):
    """a: notes as an int64 array of (start, end, key, velocity, ..., colour group) rows (other columns ride along;
    the last one = the colour group, custom.ROUND rounds in it) -> the notes cut into slices and coloured. tag: one
    more last column, each note's slice (0 = the first; "Each slice becomes its own shape"). outline: the drawn
    outline as loops of (tick, key) points (engine.wrap_outline), else the notes' own outline is used."""
    if not len(a) or not cfg:
        return a
    th = math.radians(cfg["angle"])
    if abs(math.sin(th)) > 1e-9:
        out, k = tilted(a, cfg, th, outline)
    else:
        out, k = upright(a, cfg)
        if math.cos(th) < 0:  # (180: the same cuts, slices counted from the other end)
            k = cfg["slices"] - 1 - k
    g = out[:, -1]
    lap, own = g // ROUND, g % ROUND
    if cfg.get("rounds"):  # ("Repeated colours get new channels": each time the slices go round the colours again =
        # a round of its own, paired with the shape's own rounds, user 2026-11-23)
        laps = (cfg["slices"] - 1) // cfg["colours"] + 1
        lap = lap * laps + k // cfg["colours"]
    out[:, -1] = (own + k % cfg["colours"]) % cfg["most"] + lap * ROUND
    return np.column_stack([out, k]).astype(np.int64) if tag else out


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

    def cut(m, at, sp):  # tick of cut m (0-based) in rows starting at `at`, `sp` ticks long
        return at + np.floor(sp * f[np.clip(m, 0, n - 2)] + 0.5).astype(np.int64)

    def cuts_by(t):  # how many cuts are at or before tick t: the search, then checked against the cuts' own ticks
        # (on an exact half-tick tie the share's float rounding disagreed with the tick: one tick got two slices)
        k = np.searchsorted(f, (t - base + 0.5) / safe - 1e-12, "left")
        for _ in range(2):
            k += (k < n - 1) & (cut(k, base, span) <= t)
            k -= (k > 0) & (cut(k - 1, base, span) > t)
        return k

    ka, kb = cuts_by(s), cuts_by(e - 1)
    reps = kb - ka + 1
    idx = np.repeat(np.arange(len(b)), reps)
    j = np.arange(len(idx)) - np.repeat(np.cumsum(reps) - reps, reps)
    k = ka[idx] + j  # each piece's slice
    tick = lambda m: cut(m, base[idx], span[idx])
    start = np.where(j == 0, s[idx], tick(k - 1))
    end = np.where(k == kb[idx], e[idx], tick(k))
    out = b[idx]
    out[:, 0], out[:, 1] = start, end
    keep = end > start
    return out[keep], k[keep]


def drawn_box(outline, t0, w_t, k0, h):
    """(first tick, ticks across, lowest key place, keys across) of the drawn outline (loops of (tick, key) points), or
    None: no outline, or it isn't where the notes are (their box off by more than a key / 3 % of the length)."""
    if not outline:
        return None
    pts = np.concatenate(outline)
    lo_t, hi_t = float(pts[:, 0].min()), float(pts[:, 0].max())
    lo_p, hi_p = float(pts[:, 1].min()), float(pts[:, 1].max())
    room = 0.03 * w_t + 1
    if (hi_t - lo_t < 1 or hi_p - lo_p < 0.5 or abs(lo_t - t0) > room or abs(hi_t - (t0 + w_t)) > room
            or abs(lo_p - (k0 - 0.5)) > 1.01 or abs(hi_p - (k0 + h - 0.5)) > 1.01):
        return None
    return lo_t, hi_t - lo_t, lo_p, hi_p - lo_p


def loop_edges(loops, t0, w_t, p0, ph):
    """The drawn outline's edges (x1, y1, x2, y2, inside normal x, y; in its box, 0..1): each loop closed; inside =
    the filled side (a loop inside an odd number of others is a hole)."""
    polys = []
    for lp in loops:
        x, y = (lp[:, 0] - t0) / w_t, (lp[:, 1] - p0) / ph
        if abs(x[0] - x[-1]) > 1e-12 or abs(y[0] - y[-1]) > 1e-12:
            x, y = np.r_[x, x[0]], np.r_[y, y[0]]
        polys.append((x, y))

    def inside_count(px, py, skip):
        n_ = 0
        for j, (x, y) in enumerate(polys):
            if j == skip:
                continue
            ya, yb, xa, xb = y[:-1], y[1:], x[:-1], x[1:]
            cross = (ya > py) != (yb > py)
            with np.errstate(divide="ignore", invalid="ignore"):
                xs = xa + (py - ya) * (xb - xa) / (yb - ya)
            n_ += int((cross & (xs > px)).sum()) % 2
        return n_
    out = [[], [], [], [], [], []]
    for j, (x, y) in enumerate(polys):
        area = float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]))
        hole = inside_count(x[0], y[0], j) % 2 == 1
        left = (area > 0) != hole  # (inside on the left of the way the loop goes)
        dx, dy = np.diff(x), np.diff(y)
        nx, ny = (-dy, dx) if left else (dy, -dx)
        for lst, v in zip(out, (x[:-1], y[:-1], x[1:], y[1:], nx, ny)):
            lst.append(v)
    return tuple(np.concatenate(v) for v in out)


def tilted(a, cfg, th, outline=None):
    """Any other angle -> (the pieces, each one's slice). The notes' box is squeezed to a square (x = time, y = keys,
    0..1 each: a circle drawn as an ellipse is a ball), the rows tilted by th: u = along a row, v = across. Rows =
    lines across v at BINS strip middles; each one's ends = where it crosses the SMOOTH outline through the key
    rows' ends (outline_rows; taken from the notes on each line they were noisy: rough diagonal borders and slivers
    where a border runs nearly along the keys, user 2026-11-23), blended evenly from one middle to the next (held
    flat they stepped: a sawtooth along flat edges). A note's centre line is cut at the middles; inside such a piece
    u and the row ends all run evenly, so the slice cuts fall exactly."""
    n = cfg["slices"]
    f = shares(n, cfg["round"])
    s0, e0, key = a[:, 0], a[:, 1], a[:, 2]
    t0, w_t = int(s0.min()), max(1, int(e0.max() - s0.min()))
    k0 = int(key.min())
    h = int(key.max()) - k0 + 1
    c, s = math.cos(th), math.sin(th)  # (turned clockwise on screen: v = x s + y c, u = x c - y s)
    c = 0.0 if abs(c) < 1e-9 else c  # (cos 90 is 6e-17, not 0)
    drawn = drawn_box(outline, t0, w_t, k0, h)
    if drawn:  # (the drawing's own box: a drawn ellipse is then exactly a circle, its borders truly straight / mirrored)
        t0f, w_tf, p0, ph = drawn
        x0, x1 = (s0 - t0f) / w_tf, (e0 - t0f) / w_tf
        yc = (key - p0) / ph
    else:
        x0, x1 = (s0 - t0) / w_t, (e0 - t0) / w_t
        yc = (key - k0 + 0.5) / h
    v0, v1 = x0 * s + yc * c, x1 * s + yc * c
    # the outline's corners' v = the lines the row ends are taken on (between two corners the outline is straight,
    # so the row ends there run evenly: blending between them is exact; 512 evenly spaced lines missed the corners by
    # up to half a key: wonky thin outer slices, user 2026-11-23)
    edges = loop_edges(outline, *drawn) if drawn else outline_edges(a, cfg["gaps"], t0, w_t, k0, h)
    ev = [ex * s + ey * c for ex, ey in ((edges[0], edges[1]), (edges[2], edges[3]))]
    knots = np.unique(np.round(np.concatenate(ev), 12))
    nk = len(knots)

    def pieces():
        """Each note's centre line cut where v crosses a knot: (note, cell q (from knot q to knot q + 1), x from,
        x to)."""
        qa = np.searchsorted(knots, v0, "right") - 1
        qb = np.searchsorted(knots, v1, "right") - 1
        step = 1 if s > 0 else -1  # (v grows along a note when s > 0: its cells one after another, left to right)
        reps = np.abs(qb - qa) + 1
        idx = np.repeat(np.arange(len(a)), reps)
        q = qa[idx] + step * (np.arange(len(idx)) - np.repeat(np.cumsum(reps) - reps, reps))
        y = yc[idx]
        ka_, kb_ = knots[np.clip(q, 0, nk - 1)], knots[np.clip(q + 1, 0, nk - 1)]
        ka_ = np.where(q < 0, -1e9, ka_)
        kb_ = np.where(q + 1 >= nk, 1e9, kb_)
        xa_, xb_ = (ka_ - y * c) / s, (kb_ - y * c) / s
        return (idx, q, np.clip(np.minimum(xa_, xb_), x0[idx], x1[idx]),
                np.clip(np.maximum(xa_, xb_), x0[idx], x1[idx]))

    # each knot line's rows: where it crosses the outline
    rlo, rhi, rstrip = knot_rows(edges, cfg["gaps"], c, s, knots)
    lifted = rlo + rstrip * 8.0  # (u is within -2..2: each knot's rows lifted above the ones before)

    has = np.unique(rstrip)  # (knots whose line crosses the outline)

    def ends_at(q, u):
        """The row ends (lo, hi) on knot q's line for a point at u (the row reaching it there, else the one just
        before it along the line, else the first). A knot past the outline (beyond a ball's point: a row's last
        ticks can stick out of a drawn outline there) takes the nearest knot that crosses it (taken as the middle of
        the line, those ticks became bits of the middle slice, user 2026-11-23); NaN when there's none."""
        if len(has):
            i = np.clip(np.searchsorted(has, q), 0, len(has) - 1)
            j = np.clip(i - 1, 0, len(has) - 1)
            q = np.where(np.abs(has[j] - q) < np.abs(has[i] - q), has[j], has[i])
        rs = np.searchsorted(rstrip, q, "left")
        re = np.searchsorted(rstrip, q, "right")
        pos = np.clip(np.searchsorted(lifted, u + q * 8.0, "right") - 1, rs, np.maximum(re - 1, rs))
        ok = (re > rs) & (q >= 0) & (q < nk)
        pos = np.clip(pos, 0, max(0, len(rlo) - 1))
        return np.where(ok, rlo[pos], np.nan), np.where(ok, rhi[pos], np.nan)

    # the pieces: piece in cell q runs from knot q's line to knot q + 1's
    idx, q, xa, xb = pieces()
    y = yc[idx]
    um = (xa + xb) / 2 * c - y * s
    la, ha = ends_at(q, um)
    lb, hb = ends_at(q + 1, um)
    la, ha = np.where(np.isnan(la), lb, la), np.where(np.isnan(ha), hb, ha)
    lb, hb = np.where(np.isnan(lb), la, lb), np.where(np.isnan(hb), ha, hb)
    la, ha = np.where(np.isnan(la), um, la), np.where(np.isnan(ha), um, ha)
    lb, hb = np.where(np.isnan(lb), um, lb), np.where(np.isnan(hb), um, hb)
    # along the piece: u = c x - s y, row ends = A + B x (blended by v = s x + c y between the two knots)
    vq = knots[np.clip(q, 0, nk - 1)]
    w = np.where((q >= 0) & (q + 1 < nk), knots[np.clip(q + 1, 0, nk - 1)] - vq, 1.0)
    w = np.where(w > 1e-15, w, 1.0)
    b_lo, b_hi = (lb - la) * s / w, (hb - ha) * s / w
    a_lo, a_hi = la + (lb - la) * (c * y - vq) / w, ha + (hb - ha) * (c * y - vq) / w

    # (every cut a hair (TIE of a key) before its place: a row lying right on a border, like the middle row of a ball
    # at 90 degrees, sat on it give or take the notes' rounding and flipped from slice to slice all along: user
    # 2026-11-23. Moved off it, the whole row is in one slice. A drawn outline has no rounding: a hair is enough, and a
    # bigger one would put the two halves at 90 degrees out of mirror)
    tie = 1e-9 if drawn else TIE / h
    bt0, bw = (drawn[0], drawn[1]) if drawn else (t0, w_t)  # (back to ticks by the same box x was measured in)

    def share(x):
        lo_, hi_ = a_lo + b_lo * x, a_hi + b_hi * x
        span_ = hi_ - lo_
        return np.where(span_ > 1e-15, (c * x - s * y + tie - lo_) / np.where(span_ > 1e-15, span_, 1.0), 0.5)

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
        rest = -s * y[p] + tie - a_lo[p] - fk * (a_hi[p] - a_lo[p])
        x = -rest / np.where(np.abs(coef) > 1e-15, coef, 1.0)
        return np.clip(x, xa[p], xb[p])

    right = np.where(i == m[p], xb[p], cut_x(i))  # (each cut once: a bit's start = the bit before's end)
    left = np.where(i == 0, xa[p], np.r_[0.0, right[:-1]])
    left = np.minimum(left, right)
    k = np.where(upp, ka[p] + i, kb[p] - i)
    note = idx[p]
    if drawn:  # (a bit of a row sticking out of the drawn outline (past a ball's point, where every slice meets):
        # it takes the slice of its key's nearest bit inside, user 2026-11-23: middle-slice bits at the row ends)
        xm = (left + right) / 2
        lo_m, hi_m = a_lo[p] + b_lo[p] * xm, a_hi[p] + b_hi[p] * xm
        um_ = c * xm - s * y[p]
        out_ = ~(np.isin(q[p], has) | np.isin(q[p] + 1, has)) | (um_ < lo_m - 1e-12) | (um_ > hi_m + 1e-12)
        k, left_out = inner_slices(k, out_, note)  # (the bits are in order along each note: no sorting)
        if left_out.any():  # (a whole note outside, a spam note at a row's end: its key's nearest bit inside)
            sel = np.isin(key[note], np.unique(key[note][left_out]))
            o = np.flatnonzero(sel)[np.lexsort((left[sel], key[note][sel]))]
            k[o] = inner_slices(k[o], left_out[o], key[note][o])[0]
    ta = np.floor(bt0 + left * bw + 0.5).astype(np.int64)
    tb = np.floor(bt0 + right * bw + 0.5).astype(np.int64)
    ta, tb = np.clip(ta, s0[note], e0[note]), np.clip(tb, s0[note], e0[note])
    keep = tb > ta
    note, k, ta, tb = note[keep], k[keep], ta[keep], tb[keep]
    # (a drawn outline: only crumbs folded, SLIVER_DRAWN of the length (1-5 tick strays near the points, hunt
    # 2026-11-23); the full fold there dragged borders near the points and would eat real thin slice tips)
    k = no_slivers(note, k, ta, tb, key[note], (SLIVER_DRAWN if drawn else SLIVER) * w_t)
    # touching bits of one note in one slice = one note again
    new = np.ones(len(note), bool)
    new[1:] = (note[1:] != note[:-1]) | (k[1:] != k[:-1])
    first = np.flatnonzero(new)
    last = np.append(first[1:], len(note)) - 1
    out = a[note[first]].copy()
    out[:, 0], out[:, 1] = ta[first], tb[last]
    return out, k[first]


def inner_slices(k, out_, group):
    """Pieces in order within each group (a note, or a key sorted along time): the ones marked out_ (outside the
    outline) take the slice of the nearest piece inside in their group (the one before, else the one after) ->
    (the slices, the ones left outside: no piece inside in their group)."""
    if not out_.any():
        return k, out_
    pos = np.arange(len(k))
    before = np.maximum.accumulate(np.where(~out_, pos, -1))
    after = np.minimum.accumulate(np.where(~out_, pos, len(k))[::-1])[::-1]
    ok_b = (before >= 0) & (group[np.maximum(before, 0)] == group)
    ok_a = (after < len(k)) & (group[np.minimum(after, len(k) - 1)] == group)
    near_b = np.where(ok_b, pos - before, np.inf)
    near_a = np.where(ok_a, after - pos, np.inf)
    src = np.where(near_b <= near_a, before, after)
    fix = out_ & (ok_b | ok_a)
    out = k.copy()
    out[fix] = k[np.clip(src[fix], 0, len(k) - 1)]
    return out, out_ & ~(ok_b | ok_a)


def no_slivers(note, k, ta, tb, key, most):
    """Each slice's pieces in order (note, then time) -> their slices with SLIVERS folded in: a piece shorter than most
    ticks between two touching pieces of one other slice on its key takes theirs (user 2026-11-23: "sliver
    everywhere"; where a slice border only grazes a key row, less than a key deep, which a key row can't show)."""
    # whole pieces of one note in one slice first (a note's bits are in order, so equal neighbours join)
    grp = np.r_[True, (note[1:] != note[:-1]) | (k[1:] != k[:-1])]
    gid = np.cumsum(grp) - 1
    f_ = np.flatnonzero(grp)
    gs, ge = ta[f_], np.maximum.reduceat(tb, f_)
    gk, gkey = k[f_], key[f_]
    o = np.lexsort((gs, gkey))  # (along each key)
    s_, e_, k_, y_ = gs[o], ge[o], gk[o].copy(), gkey[o]
    # (the shortest first, then the pieces joined and looked at again: folding every trapped piece at once swapped
    # 3 4 3 4 3 into 3 3 4 3 3)
    span = np.arange(len(o))  # (each piece's joined run: its first and last piece)
    for _ in range(SLIVER_PASSES):
        if len(k_) < 3:
            break
        new = np.r_[True, (y_[1:] != y_[:-1]) | (s_[1:] != e_[:-1]) | (k_[1:] != k_[:-1])]
        run = np.cumsum(new) - 1
        f_ = np.flatnonzero(new)
        rs, rk, ry = s_[f_], k_[f_], y_[f_]
        re = np.maximum.reduceat(e_, f_)
        if len(f_) < 3:
            break
        ln = re - rs
        inner = np.zeros(len(f_), bool)
        inner[1:-1] = ((ry[:-2] == ry[1:-1]) & (ry[2:] == ry[1:-1]) & (re[:-2] == rs[1:-1]) & (re[1:-1] == rs[2:])
                       & (rk[:-2] == rk[2:]) & (rk[1:-1] != rk[:-2]) & (ln[1:-1] < most))
        if not inner.any():
            break
        # (only a trapped run with no shorter trapped neighbour: neighbours fold in the next pass)
        left = np.r_[False, inner[:-1]] & (np.r_[np.inf, ln[:-1]] <= ln)
        right = np.r_[inner[1:], False] & (np.r_[ln[1:], np.inf] < ln)
        go = inner & ~left & ~right
        rk = rk.copy()
        idx = np.flatnonzero(go)
        rk[idx] = rk[idx - 1]
        k_ = rk[run]
    gk = gk.copy()
    gk[o] = k_
    return gk[gid]


def outline_edges(a, gaps, t0, w_t, k0, h):
    """The smooth outline's edges (x1, y1, x2, y2, inside normal x, y; x, y in the notes' box, 0..1). The outline:
    each key row's runs ("whole": the row first start .. last end;
    "each": runs of touching notes) as points at their ends, joined to the run they overlap on the next key (each
    end to its nearest: the leftmost / rightmost overlapping run there, both ways); an end with no partner goes
    straight up / down half a key, a run with nothing overlapping above / below gets a flat cap half a key out.
    Each edge knows which side is inside: crossing it going along +u enters (a row's start) or leaves (its end)."""
    order, _, lo, hi = rows_of(a, gaps)
    first = np.flatnonzero(np.r_[True, np.diff(_) != 0]) if len(_) else np.zeros(0, np.int64)
    rk = a[order[first], 2]  # (each run's key; runs come by key, then left to right)
    xs, xe = (lo - t0) / w_t, (hi - t0) / w_t
    y = (rk - k0 + 0.5) / h
    half = 0.5 / h
    m = len(rk)
    lift = (rk - k0).astype(float) * 4.0  # (x is 0..1: each key's runs lifted above the ones before)
    ls, le = xs + lift, xe + lift

    def overlapping(dk):
        """For each run, the first / last run on key + dk overlapping it (-1 / -2 when none)."""
        jl = np.searchsorted(le, xs + lift + dk * 4.0, "right")
        jh = np.searchsorted(ls, xe + lift + dk * 4.0, "left") - 1
        ok = (jl <= jh) & (jl < m) & (jh >= 0)
        jl, jh = np.clip(jl, 0, max(0, m - 1)), np.clip(jh, 0, max(0, m - 1))
        ok &= (rk[jl] == rk + dk) & (rk[jh] == rk + dk)
        return np.where(ok, jl, -1), np.where(ok, jh, -2)

    up_f, up_l = overlapping(1)
    dn_f, dn_l = overlapping(-1)
    ids = np.arange(m)
    lmatch = (up_f >= 0) & (dn_f[np.maximum(up_f, 0)] == ids)  # (left ends joined upward)
    rmatch = (up_l >= 0) & (dn_l[np.maximum(up_l, 0)] == ids)
    lmatched_dn = np.zeros(m, bool)
    lmatched_dn[up_f[lmatch]] = True
    rmatched_dn = np.zeros(m, bool)
    rmatched_dn[up_l[rmatch]] = True
    # each side smoothed along its rows (the notes' whole ticks make row ends zig-zag a little: a slanted line then
    # clipped a corner and a row began in the wrong slice, wonky edges, user 2026-11-23)
    for x_, match, up in ((xs, lmatch, up_f), (xe, rmatch, up_l)):
        above = np.where(match, up, -1)
        below = np.full(m, -1)
        below[up[match]] = ids[match]
        both = (above >= 0) & (below >= 0)
        for _ in range(SMOOTH):
            x_[both] = (x_[below[both]] + 2 * x_[both] + x_[above[both]]) / 4
    ex1, ey1, ex2, ey2, enx, eny = [], [], [], [], [], []

    def add(x1, y1, x2, y2, nx, ny):
        for lst, v in zip((ex1, ey1, ex2, ey2, enx, eny), (x1, y1, x2, y2, nx, ny)):
            lst.append(np.broadcast_to(np.asarray(v, float), np.shape(x1)).astype(float))

    i = ids[lmatch]
    j = up_f[lmatch]
    add(xs[i], y[i], xs[j], y[j], y[j] - y[i], -(xs[j] - xs[i]))  # (left: inside to the right)
    i = ids[rmatch]
    j = up_l[rmatch]
    add(xe[i], y[i], xe[j], y[j], -(y[j] - y[i]), xs[j] * 0 + (xe[j] - xe[i]))  # (right: inside to the left)
    top = up_f < 0
    bot = dn_f < 0
    for side, x_, nx in (("l", xs, 1.0), ("r", xe, -1.0)):  # (an end with no partner, its run going on: straight)
        up_free = ~(lmatch if side == "l" else rmatch) & ~top
        dn_free = ~(lmatched_dn if side == "l" else rmatched_dn) & ~bot
        add(x_[up_free], y[up_free], x_[up_free], y[up_free] + half, nx, 0.0)
        add(x_[dn_free], y[dn_free] - half, x_[dn_free], y[dn_free], nx, 0.0)
    # a run with nothing above / below: a point half a key out over its middle (a flat cap's square corners kinked
    # the borders as they met at the ball's points)
    mid = (xs + xe) / 2
    for sel, dy in ((top, half), (bot, -half)):
        for x_ in (xs, xe):
            ex, ey = mid[sel] - x_[sel], dy + 0 * mid[sel]  # (from the end to the point)
            nx_, ny_ = ey, -ex  # (one of its two sides: the one facing the run's middle at y)
            flip = (nx_ * (mid[sel] - (x_[sel] + mid[sel]) / 2) + ny_ * (-dy / 2)) < 0
            add(x_[sel], y[sel], mid[sel], y[sel] + dy, np.where(flip, -nx_, nx_), np.where(flip, -ny_, ny_))
    return tuple(np.concatenate(v) for v in (ex1, ey1, ex2, ey2, enx, eny))


def knot_rows(edges, gaps, c, s, knots):
    """The rows of each knot's line (v = knots[q]): (each row's u from, u to, its q), in q order then along the line.
    An edge counts the knots from its lower v up to (not at) its upper one, so a corner where the outline goes on is
    crossed once."""
    x1, y1, x2, y2, nx, ny = edges
    v1, v2 = np.round(x1 * s + y1 * c, 12), np.round(x2 * s + y2 * c, 12)
    lo_v, hi_v = np.minimum(v1, v2), np.maximum(v1, v2)
    qa = np.searchsorted(knots, lo_v, "left")
    qb = np.searchsorted(knots, hi_v, "left") - 1
    flat = hi_v - lo_v < 1e-15  # (an edge along the lines: crossed by none)
    reps = np.where(flat, 0, np.maximum(qb - qa + 1, 0))
    e = np.repeat(np.arange(len(x1)), reps)
    q = qa[e] + (np.arange(len(e)) - np.repeat(np.cumsum(reps) - reps, reps))
    t = (knots[q] - v1[e]) / (v2[e] - v1[e])
    px, py = x1[e] + (x2[e] - x1[e]) * t, y1[e] + (y2[e] - y1[e]) * t
    u = px * c - py * s
    enter = (c * nx[e] - s * ny[e]) > 0  # (going along +u crosses into the inside)
    if not len(q):
        return np.zeros(0), np.zeros(0), np.zeros(0, np.int64)
    o = np.lexsort((~enter, u, q))  # (by line, along it; at one place entering first)
    q, u, enter = q[o], u[o], enter[o]
    if gaps != "each":  # (a line's one row: from its first crossing to its last)
        newq = np.r_[True, q[1:] != q[:-1]]
        f_ = np.flatnonzero(newq)
        return u[f_], np.maximum.reduceat(u, f_), q[f_]
    d = np.where(enter, 1, -1)
    newq = np.r_[True, q[1:] != q[:-1]]
    base = np.repeat(np.cumsum(d)[np.flatnonzero(newq)] - d[np.flatnonzero(newq)],
                     np.diff(np.r_[np.flatnonzero(newq), len(q)]))
    depth = np.cumsum(d) - base  # (inside how many runs after each crossing, per line)
    starts = enter & (depth == 1)
    ends = ~enter & (depth == 0)
    si, ei = np.flatnonzero(starts), np.flatnonzero(ends)
    if len(si) != len(ei) or (len(si) and ((ei < si).any() or (q[si] != q[ei]).any())):  # (crossings that don't
        newq_i = np.flatnonzero(newq)  # pair up: each line one row)
        return u[newq_i], np.maximum.reduceat(u, newq_i), q[newq_i]
    return u[si], u[ei], q[si]
