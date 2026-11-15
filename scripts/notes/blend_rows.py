"""Nested Blend worked out exactly on the notes' own key rows (no grid): every ring edge is where the smooth value
crosses its step's line, to the tick.

The smooth value is heat spreading from the centre point to the outline (0 there). The notes only exist on key rows,
so the rows ARE the shape: each stretch of the inside is a strip one key tall, its ends exact (0), a row with no
neighbour above / below is 0 half a row further. Along a row the heat is solved exactly (sums of exponentials);
between rows it flows by the plain neighbour average. Between two "nodes" (where some row's stretch starts or ends)
the same rows are alive, and each run of neighbouring rows has closed-form sine modes; the pieces are joined at the
nodes (value and slope carried on) by one block-tridiagonal solve, kept per outline. The centre point is a point
source on its row(s); the hot disc's level is the heat's mean on a small circle round it.

Evened out like the old grid version (before 2026-11-16): 400 heat levels, each level's average distance from
the centre point (made falling), value = 1 - level / outermost level's; the averages are sums over the exact heat
along the rows (STEP row heights apart), each sample shared between the three nearest levels.

Everything is in the view (a, c) the shape is measured in: (beats b, key p) sits at x = b + a p, y = c p.
"""
import math

import numpy as np

STEP = 1 / 16  # spacing along a row (in row heights) of the samples the evening-out sums over
LEVELS = 400

_outlines = {}  # outline (inside rows, ppq, view) -> Outline
_solved = {}  # (outline key, centre) -> Solved
_modes = {}


def clear():
    """Forget every remembered outline / solution."""
    _outlines.clear()
    _solved.clear()


def sine_modes(m):
    """Orthonormal modes of m neighbouring rows, 0 half a row beyond both ends: sin(k pi (j - 1/2) / m) (columns),
    and each mode's decay per row height, 2 sin(k pi / 2m)."""
    got = _modes.get(m)
    if got is None:
        j = np.arange(1, m + 1) - 0.5
        k = np.arange(1, m + 1)
        q = np.sqrt(2.0 / m) * np.sin(np.pi * np.outer(j, k) / m)
        q[:, -1] *= np.sqrt(0.5)
        got = _modes[m] = (q, 2 * np.sin(k * np.pi / (2 * m)))
    return got


class Outline:
    """The inside's rows as nodes and pieces, with the joined solve's elimination done once (block LU)."""

    def __init__(self, base, ppq, view):
        a, h = view
        s, e, k = base[:, 0], base[:, 1], base[:, 2]
        self.a, self.h, self.ppq = a, h, ppq
        self.k0 = int(k.min())
        n = self.N = int(k.max()) - self.k0 + 1
        row = k - self.k0
        x0 = np.round((s / ppq + a * k) * 1e9) / 1e9  # (rounded: equal ends on different rows meet exactly)
        x1 = np.round((e / ppq + a * k) * 1e9) / 1e9
        self.x0, self.x1, self.row = x0, x1, row
        nodes = self.nodes = np.unique(np.concatenate([x0, x1]))
        pieces = self.E = len(nodes) - 1
        i0, i1 = np.searchsorted(nodes, x0), np.searchsorted(nodes, x1)
        d = np.zeros((n, pieces + 1), np.int32)
        np.add.at(d, (row, i0), 1)
        np.add.at(d, (row, i1), -1)
        alive = self.alive = np.cumsum(d, axis=1)[:, :pieces] > 0
        dl = self.dl = np.diff(nodes)
        self.Qf = np.zeros((pieces, n, n))  # (each piece's modes by row, for reading the heat anywhere)
        self.MU = np.zeros((pieces, n))
        seg = self.seg = []
        for j in range(pieces):
            rows = np.flatnonzero(alive[:, j])
            if not len(rows):
                seg.append(None)
                continue
            m = len(rows)
            qc = np.zeros((m, m))
            mu = np.zeros(m)
            off = 0
            for run in np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1):
                q, decay = sine_modes(len(run))
                qc[off:off + len(run), off:off + len(run)] = q
                mu[off:off + len(run)] = decay / h
                off += len(run)
            z = mu * dl[j]
            em = -np.expm1(-2 * z)
            wc = mu * (2 - em) / em  # (slope at one end from the value there / at the other end, per mode)
            ws = mu * 2 * np.exp(-z) / em
            self.Qf[j][np.ix_(rows, np.arange(m))] = qc
            self.MU[j, :m] = mu
            seg.append(dict(rows=rows, Q=qc, mu=mu, n=m, Kc=(qc * wc) @ qc.T, Ks=(qc * ws) @ qc.T))
        # unknowns at each node: the rows alive on both sides of it (elsewhere a row's end is 0)
        empty = np.zeros(0, np.int64)
        self.U = [empty] + [np.flatnonzero(alive[:, i - 1] & alive[:, i]) for i in range(1, pieces)] + [empty]
        self.Lo, self.W, self.Sinv = [None] * (pieces + 1), [None] * (pieces + 1), [None] * (pieces + 1)
        prev = np.zeros((0, 0))
        for i in range(1, pieces):
            u, um = self.U[i], self.U[i - 1]
            if not len(u):
                self.Lo[i], self.W[i], self.Sinv[i] = np.zeros((0, len(um))), np.zeros((0, len(um))), np.zeros((0, 0))
                prev = self.Sinv[i]
                continue
            left, right = seg[i - 1], seg[i]
            pa, pb = np.searchsorted(left["rows"], u), np.searchsorted(right["rows"], u)
            dm = left["Kc"][np.ix_(pa, pa)] + right["Kc"][np.ix_(pb, pb)]
            lo = -left["Ks"][np.ix_(pa, np.searchsorted(left["rows"], um))] if len(um) else np.zeros((len(u), 0))
            w = lo @ prev
            self.Lo[i], self.W[i], self.Sinv[i] = lo, w, np.linalg.inv(dm - w @ lo.T)
            prev = self.Sinv[i]


def outline_of(base, ppq, view):
    key = (base.tobytes(), ppq, tuple(view))
    got = _outlines.get(key)
    if got is None:
        if len(_outlines) > 3:
            _outlines.clear()
            _solved.clear()
        got = _outlines[key] = Outline(base, ppq, view)
    return got, key


def place(o, centre, view):
    """The centre point in the view, and the rows its heat goes into ({row: share}); a point where no row is
    alive moves to the nearest spot of the inside (half a row in from a stretch's end)."""
    a, h = view
    cx, cy = centre[0] + a * centre[1], h * centre[1]
    if o.nodes[0] < cx < o.nodes[-1]:
        r = cy / h - o.k0
        lo = int(math.floor(r))
        f = r - lo
        j = int(np.clip(np.searchsorted(o.nodes, cx, "right") - 1, 0, o.E - 1))
        w = {rr: ww for rr, ww in ((lo, 1 - f), (lo + 1, f)) if 0 <= rr < o.N and o.alive[rr, j] and ww > 0}
        if w:
            total = sum(w.values())
            return cx, cy, {k: v / total for k, v in w.items()}
    yk = h * (o.row + o.k0)
    m = np.minimum(0.5 * h, (o.x1 - o.x0) / 2)
    px = np.clip(cx, o.x0 + m, o.x1 - m)
    i = int(np.argmin((px - cx) ** 2 + (yk - cy) ** 2))
    return float(px[i]), float(yk[i]), {int(o.row[i]): 1.0}


class Solved:
    """The heat for one centre point: each piece's mode weights at its two ends (WS, WE), plus the point source's
    own part in its piece (j0, q, cx)."""


def solve(o, cx, cy, w):
    s = Solved()
    s.cx, s.cy, s.w = cx, cy, w
    pieces, n = o.E, o.N
    j0 = s.j0 = int(np.clip(np.searchsorted(o.nodes, cx, "right") - 1, 0, pieces - 1))
    sg = o.seg[j0]
    src = np.zeros(sg["n"])
    for r, ww in w.items():
        src[np.searchsorted(sg["rows"], r)] = ww
    q = sg["Q"].T @ src
    mu = sg["mu"]
    xs, xe = o.nodes[j0], o.nodes[j0 + 1]
    es, ee = np.exp(-mu * (cx - xs)), np.exp(-mu * (xe - cx))
    qm = sg["Q"]
    ups, upe = qm @ (q * es / (2 * mu)), qm @ (q * ee / (2 * mu))  # (the source alone, at the piece's ends)
    dps, dpe = qm @ (q * es / 2), -(qm @ (q * ee / 2))
    s.q = np.zeros(n)
    s.q[:sg["n"]] = q
    f = {}
    if 1 <= j0 <= pieces - 1 and len(o.U[j0]):
        p = np.searchsorted(sg["rows"], o.U[j0])
        f[j0] = (dps + sg["Kc"] @ ups - sg["Ks"] @ upe)[p]
    if 1 <= j0 + 1 <= pieces - 1 and len(o.U[j0 + 1]):
        p = np.searchsorted(sg["rows"], o.U[j0 + 1])
        f[j0 + 1] = (-dpe - sg["Ks"] @ ups + sg["Kc"] @ upe)[p]
    first = min(f) if f else pieces
    y = [np.zeros(len(u)) for u in o.U]
    for i in range(first, pieces):
        if len(o.U[i]):
            y[i] = f.get(i, 0.0) - (o.W[i] @ y[i - 1] if i > first and len(y[i - 1]) else 0.0)
    sol = [np.zeros(len(u)) for u in o.U]
    for i in range(pieces - 1, 0, -1):
        if not len(o.U[i]):
            continue
        rhs = y[i]
        if i + 1 <= pieces - 1 and len(o.U[i + 1]):
            rhs = rhs - o.Lo[i + 1].T @ sol[i + 1]
        sol[i] = o.Sinv[i] @ rhs
    at_node = np.zeros((pieces + 1, n))
    for i in range(1, pieces):
        at_node[i, o.U[i]] = sol[i]
    s.WS, s.WE = np.zeros((pieces, n)), np.zeros((pieces, n))
    for j in range(pieces):
        sg = o.seg[j]
        if sg is None:
            continue
        us, ue = at_node[j, sg["rows"]], at_node[j + 1, sg["rows"]]
        if j == j0:
            us, ue = us - ups, ue - upe
        s.WS[j, :sg["n"]] = sg["Q"].T @ us
        s.WE[j, :sg["n"]] = sg["Q"].T @ ue
    return s


def heat_at(o, s, ri, xs):
    """The heat at (row index, x) spots, exactly (0 where the row isn't inside)."""
    out = np.zeros(len(xs))
    if not len(xs):
        return out
    j = np.clip(np.searchsorted(o.nodes, xs, "right") - 1, 0, o.E - 1)
    ok = (xs >= o.nodes[0]) & (xs <= o.nodes[-1]) & (ri >= 0) & (ri < o.N)
    ok[ok] &= o.alive[ri[ok], j[ok]]
    idx = np.flatnonzero(ok)
    for lo in range(0, len(idx), 4000):
        ii = idx[lo:lo + 4000]
        jj, rr, x = j[ii], ri[ii], xs[ii]
        t = (x - o.nodes[jj])[:, None]
        dl = o.dl[jj][:, None]
        q = o.Qf[jj, rr]
        mu = o.MU[jj]
        mu = np.where(mu > 0, mu, 1.0)
        em = -np.expm1(-2 * mu * dl)
        ws = (np.exp(-mu * t) - np.exp(-mu * (2 * dl - t))) / em
        we = (np.exp(-mu * (dl - t)) - np.exp(-mu * (dl + t))) / em
        u = (q * (s.WS[jj] * ws + s.WE[jj] * we)).sum(1)
        m = jj == s.j0
        if m.any():
            u[m] += (q[m] * (s.q * np.exp(-mu[m] * np.abs(x[m] - s.cx)[:, None]) / (2 * mu[m]))).sum(1)
        out[ii] = u
    return out


def circle_mean(o, s, rho):
    """The heat's mean on the circle of radius rho round the centre (where it crosses key rows inside)."""
    h = o.h
    kk = np.arange(int(math.ceil((s.cy - rho) / h)), int(math.floor((s.cy + rho) / h)) + 1)
    kk = kk[np.abs(h * kk - s.cy) <= rho]
    half = np.sqrt(np.maximum(rho ** 2 - (h * kk - s.cy) ** 2, 0))
    ri = np.concatenate([kk - o.k0, kk - o.k0])
    xs = np.concatenate([s.cx - half, s.cx + half])
    g = heat_at(o, s, ri, xs)
    ok = (ri >= 0) & (ri < o.N)
    live = np.zeros(len(xs), bool)
    live[ok] = o.alive[ri[ok], np.clip(np.searchsorted(o.nodes, xs[ok], "right") - 1, 0, o.E - 1)] & (
        (xs[ok] > o.nodes[0]) & (xs[ok] < o.nodes[-1]))
    if live.any() and g[live].mean() > 0:
        return float(g[live].mean())
    return float(max(g.max(), 1e-12))


def samples(o, s):
    """The heat on every alive row at STEP row heights inside every piece (ends included): per piece
    (piece, xs, rows, heat[row, x], spacing)."""
    out = []
    for j in range(o.E):
        sg = o.seg[j]
        if sg is None:
            continue
        dl = o.dl[j]
        ns = max(1, int(math.ceil(dl / (STEP * o.h))))
        t = np.linspace(0, dl, ns + 1)[None, :]
        mu = sg["mu"][:, None]
        em = -np.expm1(-2 * mu * dl)
        ws = (np.exp(-mu * t) - np.exp(-mu * (2 * dl - t))) / em
        we = (np.exp(-mu * (dl - t)) - np.exp(-mu * (dl + t))) / em
        m = sg["n"]
        wh = s.WS[j, :m, None] * ws + s.WE[j, :m, None] * we
        if j == s.j0:
            wh = wh + (s.q[:m, None] * np.exp(-mu * np.abs(o.nodes[j] + t - s.cx)) / (2 * mu))
        out.append((j, o.nodes[j] + t[0], sg["rows"], sg["Q"] @ wh, dl / ns))
    return out


def solved(base, ppq, centre, view):
    """The Solved heat for this inside and centre point, evened out (remembered)."""
    o, okey = outline_of(base, ppq, view)
    cx0, cy0 = centre[0] + view[0] * centre[1], view[1] * centre[1]
    key = (okey, round(cx0, 9), round(cy0, 9))
    got = _solved.get(key)
    if got is not None:
        return got
    if len(_solved) > 6:
        _solved.clear()
    cx, cy, w = place(o, centre, view)
    s = solve(o, cx, cy, w)
    s.o = o
    h = o.h
    longer = max(o.nodes[-1] - o.nodes[0], h * o.N)
    s.hot = circle_mean(o, s, max(longer / 60, 1.5 * h))  # (the hot disc's heat: value 1 from there in)
    s.sm = samples(o, s)
    us, ws, rs = [], [], []
    for j, xs, rows, g, dx in s.sm:
        wt = np.full(len(xs), dx)
        wt[0] *= 0.5
        wt[-1] *= 0.5
        y = h * (rows + o.k0)
        us.append((g / s.hot).ravel())
        ws.append(np.tile(wt, len(rows)))
        rs.append(np.hypot(xs[None, :] - cx, (y - cy)[:, None]).ravel())
    u, wgt, r = np.concatenate(us), np.concatenate(ws), np.concatenate(rs)
    free = u < 1
    pos = u[free] * LEVELS - 0.5  # (each sample shared between the three nearest levels: a smooth histogram)
    d = pos - np.round(pos)
    wf, rf = wgt[free], (wgt * r)[free]
    sums, count = np.zeros(LEVELS + 2), np.zeros(LEVELS + 2)
    for off, kw in ((-1, 0.5 * (0.5 - d) ** 2), (0, 0.75 - d ** 2), (1, 0.5 * (0.5 + d) ** 2)):
        idx = np.clip(np.round(pos).astype(np.int64) + off, 0, LEVELS - 1)
        sums += np.bincount(idx, rf * kw, LEVELS + 2)
        count += np.bincount(idx, wf * kw, LEVELS + 2)
    sums, count = sums[:LEVELS], count[:LEVELS]
    level = np.where(count > 0, sums / np.maximum(count, 1e-300), np.inf)
    level = np.minimum.accumulate(level)
    level[~np.isfinite(level)] = 0.0
    edge = level[0] if level[0] > 0 else max(float(level.max()), 1e-9)
    s.u_at = np.concatenate([(np.arange(LEVELS) + 0.5) / LEVELS, [1.0]])
    s.v_at = np.concatenate([1 - np.minimum(level, edge) / edge, [1.0]])
    _solved[key] = s
    return s


def value_of_heat(s, u):
    """The evened-out value of heat u (as a share of the hot disc's)."""
    return np.where(u >= 1, 1.0, np.interp(u, s.u_at, s.v_at))


def heat_for(s, v):
    """The heat (share of the hot disc's) where the evened-out value reaches v (it rises with the heat)."""
    if v <= 0:
        return 0.0
    i = int(np.searchsorted(s.v_at, v, "left"))
    if i >= len(s.v_at):
        return 1.0
    if i == 0:
        return float(s.u_at[0])
    v0, v1 = s.v_at[i - 1], s.v_at[i]
    f = (v - v0) / (v1 - v0) if v1 > v0 else 1.0
    return float(s.u_at[i - 1] + f * (s.u_at[i] - s.u_at[i - 1]))


def roots(fn, x0, x1, ppq):
    """Where fn(which, x) (bracket numbers, spots) crosses 0 inside each bracket [x0, x1] (opposite signs at its
    ends): regula falsi (Illinois) on the exact value, every 8th step a halving, until the spot moves < 1e-4 tick."""
    every = np.arange(len(x0))
    f0, f1 = fn(every, x0), fn(every, x1)
    tol = 1e-4 / ppq
    x0, x1 = x0.copy(), x1.copy()
    xm = (x0 + x1) / 2
    act = np.arange(len(x0))
    side = np.zeros(len(x0), np.int8)
    for it in range(60):
        a0, a1, g0, g1 = x0[act], x1[act], f0[act], f1[act]
        den = g1 - g0
        den = np.where(den == 0, 1e-300, den)
        x = np.clip((a0 * g1 - a1 * g0) / den, np.minimum(a0, a1), np.maximum(a0, a1))
        if it % 8 == 7:
            x = (a0 + a1) / 2
        fx = fn(act, x)
        left = np.sign(fx) == np.sign(g0)  # (the spot lies right of x: x replaces x0)
        sd = side[act]
        n0, n1 = np.where(left, x, a0), np.where(left, a1, x)
        m0 = np.where(left, fx, np.where(sd == -1, g0 / 2, g0))
        m1 = np.where(left, np.where(sd == 1, g1 / 2, g1), fx)
        side[act] = np.where(left, 1, -1)
        x0[act], x1[act], f0[act], f1[act] = n0, n1, m0, m1
        moved = np.abs(x - xm[act])
        xm[act] = x
        done = ((moved < tol) & (it > 0)) | (np.abs(n1 - n0) < tol)
        act = act[~done]
        if not len(act):
            break
    return xm


def seg_dist(seg, px, py):
    """Each spot's distance to the nearest of the segments (exact, a chunk of spots at a time)."""
    out = np.empty(len(px))
    ax, ay, bx, by = (seg[:, i][None, :] for i in range(4))
    dx, dy = bx - ax, by - ay
    ll = np.maximum(dx * dx + dy * dy, 1e-18)
    for lo in range(0, len(px), 256):
        x, y = px[lo:lo + 256, None], py[lo:lo + 256, None]
        t = np.clip(((x - ax) * dx + (y - ay) * dy) / ll, 0, 1)
        out[lo:lo + 256] = np.sqrt(((x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2).min(axis=1))
    return out


class Mixed:
    """The mix slider's part: value = (1 - mix) x depth / deepest + mix x the evened-out heat, depth = the exact
    distance to the outline (the segments, in the view) like Even band's."""

    def __init__(self, s, mix, seg, deepest):
        self.s, self.mix, self.seg, self.deepest = s, mix, seg, deepest

    def value(self, ri, xs, depth):
        heat = heat_at(self.s.o, self.s, ri, xs) / self.s.hot
        return (1 - self.mix) * depth / self.deepest + self.mix * value_of_heat(self.s, heat)

    def sampled(self):
        """The value at every sample (like s.sm's heat)."""
        from notes.shrink import distances
        o, s = self.s.o, self.s
        out = []
        for j, xs, rows, g, _ in s.sm:
            d = distances(self.seg, xs, o.h * (rows + o.k0), 1.5 * self.deepest + o.h)
            out.append((1 - self.mix) * d / self.deepest + self.mix * value_of_heat(s, g / s.hot))
        return out

    def crossing(self, row, x0, x1, line):
        """fn for roots: the value minus its line, in each bracket (row index, [x0, x1]); only the segments that
        can be nearest somewhere in a bracket are measured (within its middle's distance + its length)."""
        o = self.s.o
        y = o.h * (row + o.k0)
        mx = (x0 + x1) / 2
        seg = self.seg
        ax, ay, bx, by = (seg[:, i][None, :] for i in range(4))
        dx, dy = bx - ax, by - ay
        ll = np.maximum(dx * dx + dy * dy, 1e-18)
        pb, ps = [], []
        for lo in range(0, len(mx), 256):
            x, yy = mx[lo:lo + 256, None], y[lo:lo + 256, None]
            t = np.clip(((x - ax) * dx + (yy - ay) * dy) / ll, 0, 1)
            d = np.sqrt((x - ax - t * dx) ** 2 + (yy - ay - t * dy) ** 2)
            near = d <= d.min(axis=1, keepdims=True) + (x1 - x0)[lo:lo + 256, None] + 1e-12
            b, k = np.nonzero(near)
            pb.append(b + lo)
            ps.append(k)
        pair_b, pair_s = np.concatenate(pb), np.concatenate(ps)  # (bracket, segment) pairs, by bracket

        def fn(which, x):
            pick = np.zeros(len(mx), np.int64) - 1
            pick[which] = np.arange(len(which))
            on = pick[pair_b] >= 0
            b, k = pick[pair_b[on]], pair_s[on]
            px, py = x[b], y[which][b]
            s4 = seg[k]
            ex, ey = s4[:, 2] - s4[:, 0], s4[:, 3] - s4[:, 1]
            t = np.clip(((px - s4[:, 0]) * ex + (py - s4[:, 1]) * ey) / np.maximum(ex * ex + ey * ey, 1e-18), 0, 1)
            d = np.sqrt((px - s4[:, 0] - t * ex) ** 2 + (py - s4[:, 1] - t * ey) ** 2)
            depth = np.full(len(which), np.inf)
            np.minimum.at(depth, b, d)
            return self.value(row[which], x, depth) - line[which]
        return fn


def blend_steps(sizes, ppq, base, centre, view, merged_rows, mix=1.0, seg=None, deepest=None):
    """Each step's (start, end, key) stretches, the inside itself first: step i = every spot whose value is at
    least 1 - sizes[i] (sizes[0] = the inside). merged_rows joins touching stretches (nested's). With mix < 1 the
    value is mixed with the depth (seg = the outline's segments in the view, deepest = Even band's deepest)."""
    base = np.asarray(base, np.int64).reshape(-1, 3)
    if not len(base):
        return [base]
    s = solved(base, ppq, centre, view)
    o = s.o
    a = view[0]
    lines = [1 - sz for sz in sizes[1:]]
    mixed = Mixed(s, mix, seg, deepest) if mix < 1 else None
    if mixed:  # (brackets on the mixed value itself)
        want = np.array(lines)
        sampled = mixed.sampled()
    else:  # (the value rises with the heat: brackets and spots on the heat's own level, as exact and cheaper)
        want = np.array([heat_for(s, t) for t in lines]) * s.hot
        sampled = [g for _, _, _, g, _ in s.sm]
    rows_l, x0_l, x1_l, which_l, up_l = [], [], [], [], []
    for (j, xs, rows, _, _), g in zip(s.sm, sampled):  # (brackets: neighbouring samples on both sides of a line)
        for ti, gt in enumerate(want):
            if lines[ti] <= 0:
                continue
            on = g >= gt
            rr, ii = np.nonzero(on[:, 1:] != on[:, :-1])
            if len(rr):
                rows_l.append(rows[rr])
                x0_l.append(xs[ii])
                x1_l.append(xs[ii + 1])
                which_l.append(np.full(len(rr), ti))
                up_l.append(on[rr, ii + 1])
    steps = [base]
    if not rows_l:
        return steps + [base if t <= 0 else np.zeros((0, 3), np.int64) for t in lines]
    row, x0, x1 = np.concatenate(rows_l), np.concatenate(x0_l), np.concatenate(x1_l)
    which, up = np.concatenate(which_l), np.concatenate(up_l)
    target = want[which]
    if mixed:
        fn = mixed.crossing(row, x0, x1, target)
    else:
        def fn(idx, x):
            return heat_at(o, s, row[idx], x) - target[idx]
    xr = roots(fn, x0, x1, ppq)
    order = np.lexsort((xr, row, which))
    row, xr, which, up = row[order], xr[order], which[order], up[order]
    for ti, t in enumerate(lines):
        if t <= 0:
            steps.append(base)
            continue
        m = which == ti
        rr, xx, uu = row[m], xr[m], up[m]
        i = np.flatnonzero(uu[:-1] & ~uu[1:] & (rr[:-1] == rr[1:]))
        key = rr[i] + o.k0
        st = np.floor((xx[i] - a * key) * ppq + 0.5).astype(np.int64)
        en = np.floor((xx[i + 1] - a * key) * ppq + 0.5).astype(np.int64)
        keep = en > st
        got = np.column_stack([st[keep], en[keep], key[keep]])
        steps.append(merged_rows(got) if len(got) else np.zeros((0, 3), np.int64))
    return steps


def values(base, ppq, centre, view, beats, keys, mix=1.0, seg=None, deepest=None):
    """The value at (beats, key) spots, read on each key row's middle line (checks / tests)."""
    s = solved(np.asarray(base, np.int64).reshape(-1, 3), ppq, centre, view)
    keys = np.asarray(keys)
    xs = np.asarray(beats, float) + view[0] * keys
    ri = np.round(keys).astype(np.int64) - s.o.k0
    if mix < 1:
        return Mixed(s, mix, seg, deepest).value(ri, xs, seg_dist(seg, xs, view[1] * keys))
    return value_of_heat(s, heat_at(s.o, s, ri, xs) / s.hot)
