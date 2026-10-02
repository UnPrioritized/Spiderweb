"""The outline gate's even band (custom.thicker): the shape shrunk inward, a smaller copy of its own outline (user:
"a shape shrinking", one with several insides shrinks each). The inside that's left is every spot inside the shape
at least r away from its outline, measured round in the shape's own proportions (its width counted the same as
its height, so a circle shrinks into a circle and a square into a square). The outline band is the inside minus
that.

Worked out exactly on lines across each key row (inner_rows), and on a grid for the piano roll's preview line
(inner_lines: marching squares on the distance to the outline)."""

import math

import numpy as np

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


def crossings(seg, y):
    """Where the edges cross the line at height y, in order."""
    ay, by = seg[:, 1], seg[:, 3]
    s = seg[(ay <= y) != (by <= y)]
    return np.sort(s[:, 0] + (s[:, 2] - s[:, 0]) * (y - s[:, 1]) / (s[:, 3] - s[:, 1]))


def walled_pieces(x, y, walled, k):
    """Which pieces between the crossings x (on the line at height y, in the shape's proportions) are filled
    (walled = custom.fill_test, in beats / keys)."""
    if len(x) < 2:
        return np.zeros(0, bool)
    a, b = x[:-1], x[1:]
    spots = np.column_stack([a + (b - a) * f for f in (0.5, 0.2, 0.8)])
    return walled(spots, np.full(spots.shape, y / k))


def inside_at(seg, y, walled=None, k=1.0):
    """Where the line at height y is inside: even-odd, or with walled (custom.fill_test) every piece it fills.
    [(a, b)] sorted, merged."""
    x = crossings(seg, y)
    if walled is None:
        return merge(list(zip(x[0::2].tolist(), x[1::2].tolist())))
    on = walled_pieces(x, y, walled, k)
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
    e = np.repeat(np.arange(len(seg)), n)
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
    for k, a, b in zip(row.tolist(), lo.tolist(), hi.tolist()):  # (merged like merge: touching ones join)
        o = out[k]
        if o and a <= o[-1][1]:
            o[-1][1] = max(o[-1][1], b)
        else:
            o.append([a, b])
    return out


def minus(spans, cuts):
    out = []
    for a, b in spans:
        for c, d in cuts:
            if d <= a or c >= b:
                continue
            if c > a:
                out.append((a, c))
            a = max(a, d)
            if a >= b:
                break
        if a < b:
            out.append((a, b))
    return out


def inner_rows(polys, walled, keys, r, ppq):
    """The shrunk inside on each of these key rows as (start, end, key) ticks; r in beats. walled: see inside_at."""
    k = proportion(polys)
    seg = segments(polys) * [1, k, 1, k]
    keys = list(keys)
    ys = [(q - 0.5 + (j + 0.5) / SAMPLES) * k for q in keys for j in range(SAMPLES)]
    near = near_rows(seg, ys, r)
    out = []
    for n, q in enumerate(keys):
        got = []
        for j in range(SAMPLES):
            y = ys[n * SAMPLES + j]
            got += minus(inside_at(seg, y, walled, k), near[n * SAMPLES + j])
        for a, b in merge(got):
            s = math.floor(a * ppq + 0.5)
            e = math.floor(b * ppq + 0.5)
            if e > s:
                out.append((s, e, q))
    return np.asarray(out, np.int64).reshape(-1, 3)


def field(polys, walled, reach):
    """The distance to the outline on a grid over the shape (in the shape's proportions), minus outside: (x0, y0,
    step, values[row, column], k). Only distances up to `reach` are exact (further ones count as 2 x reach), so
    each block of the grid looks only at the edges near it. Remembered (the same for every outline gate tried up
    to reach). walled: see inside_at."""
    key = (repr(polys), getattr(walled, "mode", None), reach)
    got = _fields.get(key)
    if got is not None:
        return got
    if len(_fields) > 30:
        _fields.clear()
    k = proportion(polys)
    seg = segments(polys) * [1, k, 1, k]
    x_lo, y_lo = seg[:, [0, 2]].min(), seg[:, [1, 3]].min()
    x_hi, y_hi = seg[:, [0, 2]].max(), seg[:, [1, 3]].max()
    step = max(x_hi - x_lo, y_hi - y_lo) / GRID or 1.0
    xs = np.arange(x_lo - step, x_hi + 2 * step, step)
    ys = np.arange(y_lo - step, y_hi + 2 * step, step)
    dist = np.full((len(ys), len(xs)), 2.0 * reach)
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
            gx, gy = np.meshgrid(x, y)
            px, py = gx.ravel()[:, None], gy.ravel()[:, None]
            ax, ay, bx, by = (near[:, i][None, :] for i in range(4))
            dx, dy = bx - ax, by - ay
            t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.maximum(dx * dx + dy * dy, 1e-18), 0, 1)
            d = np.sqrt(((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2).min(axis=1)).reshape(len(y), len(x))
            dist[j0:j0 + BLOCK, i0:i0 + BLOCK] = np.minimum(d, 2.0 * reach)
    inside = np.zeros(dist.shape, bool)
    for j, y in enumerate(ys):  # a grid row at a time: the edges it crosses, then how many lie left of each spot
        xc = crossings(seg, y)
        if not len(xc):
            continue
        at = np.searchsorted(xc, xs)
        if walled is None:
            inside[j] = at % 2 == 1
        else:  # (the piece each spot is in: walled in or not)
            inside[j] = np.concatenate([[False], walled_pieces(xc, y, walled, k), [False]])[at]
    got = _fields[key] = (xs[0], ys[0], step, np.where(inside, dist, -dist), k)
    return got


def inner_lines(polys, walled, r):
    """The shrunk inside's outline for the preview: straight pieces (b0, k0, b1, k1, bi, ki) in beats / keys,
    (bi, ki) a spot on its inner side."""
    x0, y0, step, f, k = field(polys, walled,2.0 ** math.ceil(math.log2(max(r, 1e-6) * 1.25)))
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
    return np.column_stack([x0 + s[:, 0] * step, (y0 + s[:, 1] * step) / k,
                            x0 + s[:, 2] * step, (y0 + s[:, 3] * step) / k,
                            x0 + s[:, 4] * step, (y0 + s[:, 5] * step) / k])
