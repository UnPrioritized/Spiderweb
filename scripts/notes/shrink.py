"""The outline gate's even band (custom.thicker): the shape shrunk inward, a smaller copy of its own outline (user:
"a shape shrinking", one with several insides shrinks each). The inside that's left is every spot inside the shape
at least r away from its outline, measured round in the VIEW the shape was first drawn in (custom.drawn_view: so a
circle shrinks into a smaller circle and an arc into a smaller arc, however it was turned, skewed or stretched since
and however the screen is zoomed now; an old shape without it: its width counted the same as its height). The
outline band is the inside minus that.

A view (a, c): spot (b, p) on the roll is at (b + a p, c p) there (beats; a = how slanted, c = beats per key), so a
line across a key row stays a straight line, one beat long per beat (warp).

Worked out exactly on lines across each key row (inner_rows), and on a grid for the piano roll's preview line
(inner_lines: marching squares on the distance to the outline)."""

import math

import numpy as np

from files.speed import compiled

SAMPLES = 8  # lines across each key row: a row counts where the shrunk inside touches it anywhere (like the inside)
GRID = 160  # the preview's grid: this many cells across the shape's longer side
BLOCK = 24  # the grid is worked out in blocks this many points across
_fields = {}


def segments(polys):
    """The loops' edges as (ax, ay, bx, by) arrays (beats, keys)."""
    parts = [np.asarray(p, float).reshape(-1, 2) for p in polys]
    parts = [p for p in parts if len(p) > 1]
    if not parts:
        return np.zeros((0, 4))
    return np.concatenate([np.column_stack([p[:-1], p[1:]]) for p in parts])


def proportion(polys):
    """Beats per key that make the shape as wide as it's tall (1 when it's flat)."""
    pts = np.concatenate([np.asarray(p, float).reshape(-1, 2) for p in polys if len(p)])
    w, h = np.ptp(pts[:, 0]), np.ptp(pts[:, 1])
    return w / h if w > 0 and h > 0 else 1.0


def warp(seg, view):
    """Edges (ax, ay, bx, by in beats / keys) as they are in the view (a, c)."""
    a, c = view
    seg = np.asarray(seg, float).reshape(-1, 4)
    return np.column_stack([seg[:, 0] + a * seg[:, 1], seg[:, 1] * c, seg[:, 2] + a * seg[:, 3], seg[:, 3] * c])


def near_lines(seg, ys, r, view):
    """near_rows for the lines at these keys ys, edges seg and stretches in beats / keys."""
    a, c = view
    near = near_rows(warp(seg, view), [y * c for y in ys], r)
    return [[[lo - a * y, hi - a * y] for lo, hi in spans] for spans, y in zip(near, ys)]


def crossings(seg, y):
    """Where the edges cross the line at height y, in order."""
    ay, by = seg[:, 1], seg[:, 3]
    s = seg[(ay <= y) != (by <= y)]
    return np.sort(s[:, 0] + (s[:, 2] - s[:, 0]) * (y - s[:, 1]) / (s[:, 3] - s[:, 1]))


def crossings_all(seg, ys):
    """crossings(seg, y) for each y in ys, all at once: the same numbers (each edge only meets the lines between
    its ends: one line at a time looked at every edge, most of an Even band's time once its loops were compiled)."""
    ys = np.asarray(ys, float)
    out = [np.zeros(0) for _ in range(len(ys))]
    if not len(seg) or not len(ys):
        return out
    order = np.argsort(ys, kind="stable")
    sy = ys[order]
    lo, hi = np.minimum(seg[:, 1], seg[:, 3]), np.maximum(seg[:, 1], seg[:, 3])  # (crossed: lo <= y < hi)
    first = np.searchsorted(sy, lo, "left")
    n = np.maximum(np.searchsorted(sy, hi, "left") - first, 0)
    if not n.sum():
        return out
    e = np.repeat(np.arange(len(seg)), n)
    line = order[np.repeat(first, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)]
    s, y = seg[e], ys[line]
    x = s[:, 0] + (s[:, 2] - s[:, 0]) * (y - s[:, 1]) / (s[:, 3] - s[:, 1])
    o = np.lexsort((x, line))
    line, x = line[o], x[o]
    cuts = np.r_[0, np.flatnonzero(line[1:] != line[:-1]) + 1, len(line)]
    for a, b in zip(cuts[:-1].tolist(), cuts[1:].tolist()):
        out[int(line[a])] = x[a:b]
    return out


def walled_pieces(x, y, walled, view):
    """Which pieces between the crossings x (on the line at height y, in the view) are filled (walled =
    custom.fill_test, in beats / keys)."""
    if len(x) < 2:
        return np.zeros(0, bool)
    a, b = x[:-1], x[1:]
    spots = np.column_stack([a + (b - a) * f for f in (0.5, 0.2, 0.8)])
    p = y / view[1]
    return walled(spots - view[0] * p, np.full(spots.shape, p))


def inside_at(seg, y, walled=None, view=(0.0, 1.0), x=None):
    """Where the line at height y (seg and y in the view) is inside: even-odd, or with walled (custom.fill_test)
    every piece it fills. [(a, b)] sorted, merged. x: its crossings when already known (crossings_all)."""
    x = crossings(seg, y) if x is None else x
    if walled is None:
        return merge(list(zip(x[0::2].tolist(), x[1::2].tolist())))
    on = walled_pieces(x, y, walled, view)
    return merge(list(zip(x[:-1][on].tolist(), x[1:][on].tolist())))


def merge(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def near_rows(seg, ys, r):
    """For each height y in ys (in order, all in the shape's proportions): the stretches of the line at y closer
    than r to an edge, [(a, b)] merged; each edge's round-ended band crosses it in one stretch. Only the lines each
    edge reaches are worked out (every edge with every line was most of an outline gate step's time; that plain way is kept in
    dev/tests/many_notes.py to check against)."""
    ys = np.asarray(ys, float)
    out = [[] for _ in range(len(ys))]
    if not len(seg) or not len(ys):
        return out
    s_lo, s_hi = np.minimum(seg[:, 1], seg[:, 3]) - r, np.maximum(seg[:, 1], seg[:, 3]) + r
    first = np.searchsorted(ys, s_lo, "right")  # (lines strictly inside lo .. hi)
    n = np.maximum(np.searchsorted(ys, s_hi, "left") - first, 0)
    fast = compiled("band_rows")
    if fast is not None and n.sum():  # (the same, as a compiled loop: files/fastloops.py)
        total = int(n.sum())
        ax, ay, bx, by = (np.ascontiguousarray(seg[:, i], float) for i in range(4))
        rows, los, his = np.empty(total, np.int64), np.empty(total), np.empty(total)
        m = fast.band_rows(ax, ay, bx, by, np.hypot(bx - ax, by - ay), np.ascontiguousarray(ys),
                           first.astype(np.int64), n.astype(np.int64), float(r), rows, los, his)
        return _by_line(out, rows[:m], los[:m], his[:m])
    e = np.repeat(np.arange(len(seg)), n)  # (only here: the compiled loop above doesn't need it, 60 ms a big band)
    row = np.repeat(first, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    if not len(e):
        return out
    y = ys[row]
    ax, ay, bx, by = (seg[e, i] for i in range(4))
    lo, hi = np.full(len(e), np.inf), np.full(len(e), -np.inf)
    for px, py in ((ax, ay), (bx, by)):  # the round ends
        d = r * r - (y - py) ** 2
        w = np.sqrt(np.maximum(d, 0))
        lo = np.where(d > 0, np.minimum(lo, px - w), lo)
        hi = np.where(d > 0, np.maximum(hi, px + w), hi)
    dx, dy = bx - ax, by - ay
    length = np.hypot(dx, dy)
    with np.errstate(divide="ignore", invalid="ignore"):
        flat = np.abs(dy) < 1e-12
        c = ax + dx * (y - ay) / dy
        w = r * length / np.abs(dy)
        b_lo, b_hi = np.where(flat, np.minimum(ax, bx), c - w), np.where(flat, np.maximum(ax, bx), c + w)
        ok = np.where(flat, np.abs(y - ay) < r, True)
        upright = np.abs(dx) < 1e-12
        t = (y - ay) * dy / np.where(length > 0, length * length, 1)
        x0 = ax - (y - ay) * dy / dx
        x1 = x0 + length * length / dx
        p_lo = np.where(upright, -np.inf, np.minimum(x0, x1))
        p_hi = np.where(upright, np.inf, np.maximum(x0, x1))
        ok &= np.where(upright, (t >= 0) & (t <= 1), True)
        b_lo, b_hi = np.maximum(b_lo, p_lo), np.minimum(b_hi, p_hi)
        ok &= (b_hi > b_lo) & (length > 0)
    lo = np.where(ok, np.minimum(lo, b_lo), lo)
    hi = np.where(ok, np.maximum(hi, b_hi), hi)
    got = hi > lo
    row, lo, hi = row[got], lo[got], hi[got]
    order = np.lexsort((hi, lo, row))
    row, lo, hi = row[order], lo[order], hi[order]
    # merged like merge, line by line (touching ones join: a stretch starting at or before the furthest end so far
    # goes on the one before; a Python loop over every stretch was most of a deep Nested Even band's time)
    cuts = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)]
    for s, e in zip(cuts[:-1].tolist(), cuts[1:].tolist()):
        a, b = lo[s:e], hi[s:e]
        new = np.r_[True, a[1:] > np.maximum.accumulate(b)[:-1]]
        at = np.flatnonzero(new)
        out[int(row[s])] = np.column_stack([a[at], np.maximum.reduceat(b, at)]).tolist()
    return out


def _by_line(out, row, lo, hi):
    """Joined stretches in line order (row, lo, hi) put into out, a list per line."""
    if not len(row):
        return out
    cuts = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)]
    for s, e in zip(cuts[:-1].tolist(), cuts[1:].tolist()):
        out[int(row[s])] = np.column_stack([lo[s:e], hi[s:e]]).tolist()
    return out


def minus(spans, cuts):
    """spans with the cuts taken out. Both sorted + merged (inside_at, near_rows), so each span only looks at the
    cuts from the first one still reaching it up to the first past its end (every span with every cut grew with
    the square: a 400-letter text line with an outline gate took 1.2 s)."""
    out = []
    k, n = 0, len(cuts)
    for a, b in spans:
        while k < n and cuts[k][1] <= a:  # (left of this span, so of every later one too)
            k += 1
        for j in range(k, n):
            c, d = cuts[j]
            if c >= b:  # (and every later cut)
                break
            if d <= a:
                continue
            if c > a:
                out.append((a, c))
            a = max(a, d)
            if a >= b:
                break
        if a < b:
            out.append((a, b))
    return out


def both(spans, keep):
    """Where spans and keep (both sorted, not overlapping) overlap: [(a, b)], each end one of theirs."""
    out, k = [], 0
    for a, b in spans:
        while k < len(keep) and keep[k][1] <= a:
            k += 1
        j = k
        while j < len(keep) and keep[j][0] < b:
            lo, hi = max(a, keep[j][0]), min(b, keep[j][1])
            if hi > lo:
                out.append((lo, hi))
            j += 1
    return out


def inner_rows(polys, walled, keys, r, ppq, view=None, memo=None):
    """The shrunk inside on each of these key rows as (start, end, key) ticks; r in beats. walled: see inside_at.
    view: the one the shape was drawn in (custom.drawn_view; None: as wide as it's tall, proportion).
    memo: a dict shared by the steps of one shape shrunk deeper and deeper (Nested Even band): each line's inside
    is worked out once, and a step at least as deep as the one before keeps only what that one kept, so lines it
    left empty are skipped and edges that can't reach what it kept are left out (the same numbers: a band only
    grows with r, also as rounded; 28 steps on a patterned polygon redid everything 28 times, heavy sweep 11-16)."""
    va, vc = view = view or (0.0, proportion(polys))
    memo = {} if memo is None else memo
    seg = memo.get("seg")
    if seg is None:
        seg = memo["seg"] = warp(segments(polys), view)
    keys = list(keys)
    ys = [(q - 0.5 + (j + 0.5) / SAMPLES) * vc for q in keys for j in range(SAMPLES)]
    prev = memo.get("kept") if memo.get("r", np.inf) <= r else None  # (y -> what the step before kept there)
    live = [y for y in ys if prev.get(y)] if prev is not None else ys  # (a line prev lacks was empty before it)
    inside = memo.setdefault("inside", {})
    new = [y for y in live if y not in inside]
    for y, x in zip(new, crossings_all(seg, new)):
        inside[y] = inside_at(seg, y, walled, view, x)
    near_seg = seg
    if prev is not None and live and len(seg):
        xa = min(prev[y][0][0] for y in live)
        xb = max(prev[y][-1][1] for y in live)
        reach = r * (1 + 1e-9) + 1e-9  # (an edge's band reaches at most r past its ends, plus rounding)
        lo_x, hi_x = np.minimum(seg[:, 0], seg[:, 2]), np.maximum(seg[:, 0], seg[:, 2])
        lo_y, hi_y = np.minimum(seg[:, 1], seg[:, 3]), np.maximum(seg[:, 1], seg[:, 3])
        near_seg = seg[(lo_x - reach <= xb) & (hi_x + reach >= xa) & (lo_y - reach <= live[-1])
                       & (hi_y + reach >= live[0])]
    near = near_rows(near_seg, live, r)
    kept = {}
    for k, y in enumerate(live):
        got = minus(inside[y], near[k])
        kept[y] = got if prev is None else both(got, prev[y])  # (got lies inside prev[y] already: kept as is,
        # only the stretches the left-out edges would have cut away outside it are dropped)
    memo["kept"], memo["r"] = kept, r
    out = []
    for n, q in enumerate(keys):
        got = []
        for j in range(SAMPLES):
            y = ys[n * SAMPLES + j]
            shift = va * y / vc  # (the view's line back in beats)
            got += [(s - shift, e - shift) for s, e in kept.get(y, ())]
        for a, b in merge(got):
            s = math.floor(a * ppq + 0.5)
            e = math.floor(b * ppq + 0.5)
            if e > s:
                out.append((s, e, q))
    return np.asarray(out, np.int64).reshape(-1, 3)


def band_all_rows(polys, walled, keys, rs, ppq, view):
    """inner_rows with one memo for each depth in rs (rising, > 0) on these keys, a deeper one only on the keys the
    one before had notes on (Nested Even band's steps: nested.band_steps), all at once in a compiled loop
    (fastloops.band_all; 480 steps on a tall patterned shape took 18 s): the very same rows, a list per depth.
    None = no compiled loop (inner_rows step by step then)."""
    fast = compiled("band_all")
    if fast is None:
        return None
    va, vc = view
    seg = warp(segments(polys), view)
    keys = np.unique(np.asarray(list(keys), np.int64))
    ys = ((keys[:, None] - 0.5 + (np.arange(SAMPLES) + 0.5) / SAMPLES) * vc).ravel()
    spans = [inside_at(seg, y, walled, view, x) for y, x in zip(ys.tolist(), crossings_all(seg, ys.tolist()))]
    off = np.r_[0, np.cumsum([len(s) for s in spans])].astype(np.int64)
    flat = np.asarray([p for s in spans for p in s], float).reshape(-1, 2)
    ax, ay, bx, by = (np.ascontiguousarray(seg[:, i], float) for i in range(4))
    a, b, k, step = fast.band_all(ax, ay, bx, by, np.hypot(bx - ax, by - ay), ys, va * ys / vc,
                                  np.repeat(keys, SAMPLES), off, np.ascontiguousarray(flat[:, 0]),
                                  np.ascontiguousarray(flat[:, 1]), np.ascontiguousarray(rs, float), int(ppq),
                                  SAMPLES)
    o = np.argsort(step, kind="stable")
    rows = np.column_stack([a[o], b[o], k[o]]).astype(np.int64).reshape(-1, 3)
    at = np.searchsorted(step[o], np.arange(len(rs) + 1)).tolist()
    return [rows[at[i]:at[i + 1]] for i in range(len(rs))]


def _could_be_nearest(seg, x0, x1, y0, y1):
    """The edges that can be the nearest one to some spot in the box x0..x1, y0..y1: an edge whose box is further
    from the whole box than the nearest edge is from the box's middle plus half its diagonal can't be (so the
    distances come out exactly the same, edges far away left out: a patterned polygon's thousands of little edges
    made every block look at all of them, 1.6 s a change)."""
    if len(seg) < 64:
        return seg
    ax, ay, bx, by = seg[:, 0], seg[:, 1], seg[:, 2], seg[:, 3]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = bx - ax, by - ay
    t = np.clip(((cx - ax) * dx + (cy - ay) * dy) / np.maximum(dx * dx + dy * dy, 1e-18), 0, 1)
    most = np.sqrt(((cx - ax - t * dx) ** 2 + (cy - ay - t * dy) ** 2).min()) + math.hypot(x1 - x0, y1 - y0) / 2
    gx = np.maximum(0.0, np.maximum(np.minimum(ax, bx) - x1, x0 - np.maximum(ax, bx)))
    gy = np.maximum(0.0, np.maximum(np.minimum(ay, by) - y1, y0 - np.maximum(ay, by)))
    return seg[np.hypot(gx, gy) <= most * (1 + 1e-9) + 1e-12]


def field(polys, walled, reach, view=None):
    """The distance to the outline on a grid over the shape (in the view, see inner_rows), minus outside: (x0, y0,
    step, values[row, column], view). Only distances up to `reach` are exact (further ones count as 2 x reach), so
    each block of the grid looks only at the edges near it. Remembered (the same for every outline gate tried up
    to reach). walled: see inside_at."""
    key = (repr(polys), getattr(walled, "mode", None), reach, view)
    got = _fields.get(key)
    if got is not None:
        return got
    if len(_fields) > 30:
        _fields.clear()
    view = view or (0.0, proportion(polys))
    seg = warp(segments(polys), view)
    x_lo, y_lo = seg[:, [0, 2]].min(), seg[:, [1, 3]].min()
    x_hi, y_hi = seg[:, [0, 2]].max(), seg[:, [1, 3]].max()
    step = max(x_hi - x_lo, y_hi - y_lo) / GRID or 1.0
    xs = np.arange(x_lo - step, x_hi + 2 * step, step)
    ys = np.arange(y_lo - step, y_hi + 2 * step, step)
    return _field_end(key, seg, xs, ys, step, distances(seg, xs, ys, reach), walled, view)


def distances(seg, xs, ys, reach):
    """Each grid spot's distance to the nearest of the segments (rows = ys, columns = xs, both rising), exact up to
    `reach` (further ones count as 2 x reach): each block of the grid looks only at the segments near it."""
    dist = np.full((len(ys), len(xs)), 2.0 * reach)
    fast = compiled("field_dist")
    if fast is not None:  # (the same, as a compiled loop: files/fastloops.py)
        fast.field_dist(*(np.ascontiguousarray(seg[:, i], float) for i in range(4)), np.ascontiguousarray(xs, float),
                        np.ascontiguousarray(ys, float), float(reach), BLOCK, dist)
        return dist
    s_xlo, s_xhi = np.minimum(seg[:, 0], seg[:, 2]), np.maximum(seg[:, 0], seg[:, 2])
    s_ylo, s_yhi = np.minimum(seg[:, 1], seg[:, 3]), np.maximum(seg[:, 1], seg[:, 3])
    for j0 in range(0, len(ys), BLOCK):
        y = ys[j0:j0 + BLOCK]
        rows = (s_yhi >= y[0] - reach) & (s_ylo <= y[-1] + reach)
        for i0 in range(0, len(xs), BLOCK):
            x = xs[i0:i0 + BLOCK]
            near = seg[rows & (s_xhi >= x[0] - reach) & (s_xlo <= x[-1] + reach)]
            if not len(near):
                continue
            near = _could_be_nearest(near, x[0], x[-1], y[0], y[-1])
            gx, gy = np.meshgrid(x, y)
            px, py = gx.ravel()[:, None], gy.ravel()[:, None]
            ax, ay, bx, by = (near[:, i][None, :] for i in range(4))
            dx, dy = bx - ax, by - ay
            t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.maximum(dx * dx + dy * dy, 1e-18), 0, 1)
            d = np.sqrt(((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2).min(axis=1)).reshape(len(y), len(x))
            dist[j0:j0 + BLOCK, i0:i0 + BLOCK] = np.minimum(d, 2.0 * reach)
    return dist


def _field_end(key, seg, xs, ys, step, dist, walled, view):
    """field's inside / outside signs on the distances, remembered under key."""
    inside = np.zeros(dist.shape, bool)
    for j, (y, xc) in enumerate(zip(ys, crossings_all(seg, ys))):  # a grid row at a time: the edges it crosses,
        # then how many lie left of each spot
        if not len(xc):
            continue
        at = np.searchsorted(xc, xs)
        if walled is None:
            inside[j] = at % 2 == 1
        else:  # (the piece each spot is in: walled in or not)
            inside[j] = np.concatenate([[False], walled_pieces(xc, y, walled, view), [False]])[at]
    got = _fields[key] = (xs[0], ys[0], step, np.where(inside, dist, -dist), view)
    return got


def inner_lines(polys, walled, r, view=None):
    """The shrunk inside's outline for the preview: straight pieces (b0, k0, b1, k1, bi, ki) in beats / keys,
    (bi, ki) a spot on its inner side."""
    x0, y0, step, f, (va, vc) = field(polys, walled, 2.0 ** math.ceil(math.log2(max(r, 1e-6) * 1.25)), view)
    on = f >= r
    pieces = []
    # where the level is crossed on each grid line, by the values on either side
    with np.errstate(divide="ignore", invalid="ignore"):
        th = (r - f[:, :-1]) / (f[:, 1:] - f[:, :-1])
        tv = (r - f[:-1, :]) / (f[1:, :] - f[:-1, :])
    hx = np.where(on[:, :-1] != on[:, 1:], th, np.nan)  # across: (row, column) -> fraction to the right
    vy = np.where(on[:-1, :] != on[1:, :], tv, np.nan)  # up: (row, column) -> fraction up
    ny, nx = f.shape
    j, i = np.meshgrid(np.arange(ny - 1), np.arange(nx - 1), indexing="ij")
    # each cell's crossings: bottom, right, top, left (x, y in grid steps)
    cand = np.stack([
        np.stack([i + hx[:-1, :], j + 0.0 * hx[:-1, :]], -1),
        np.stack([i + 1 + 0.0 * vy[:, 1:], j + vy[:, 1:]], -1),
        np.stack([i + hx[1:, :], j + 1 + 0.0 * hx[1:, :]], -1),
        np.stack([i + 0.0 * vy[:, :-1], j + vy[:, :-1]], -1)], 2)
    ok = ~np.isnan(cand).any(-1)
    cand, ok = cand.reshape(-1, 4, 2), ok.reshape(-1, 4)
    # the middle of each cell's corners that are inside the shrunk shape: which side of the piece is in
    corners = np.stack([on[:-1, :-1], on[:-1, 1:], on[1:, 1:], on[1:, :-1]], -1).reshape(-1, 4)
    cx = np.array([0, 1, 1, 0])[None, :]
    cy = np.array([0, 0, 1, 1])[None, :]
    count = np.maximum(corners.sum(1), 1)
    mid = np.column_stack([(corners * cx).sum(1) / count + i.ravel(), (corners * cy).sum(1) / count + j.ravel()])
    n = ok.sum(1)
    for want in (2, 4):
        sel = n == want
        c, o, m = cand[sel], ok[sel], mid[sel]
        if not len(c):
            continue
        idx = np.argsort(~o, axis=1, kind="stable")[:, :want]
        p = np.take_along_axis(c, idx[:, :, None], 1)
        for a, b in ((0, 1), (2, 3))[:want // 2]:
            pieces.append(np.column_stack([p[:, a], p[:, b], m]))
    if not pieces:
        return np.zeros((0, 6))
    s = np.concatenate(pieces)
    out = []
    for i in (0, 2, 4):  # (back from the view: keys = y / c, beats = x - a keys)
        p = (y0 + s[:, i + 1] * step) / vc
        out += [x0 + s[:, i] * step - va * p, p]
    return np.column_stack(out)
