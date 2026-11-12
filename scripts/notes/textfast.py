"""A text's letters -> notes for ALL letters in one NumPy pass (design swarm 2026-11-12, user picked it): the very
same numbers as doing it letter by letter in Python (text.flatten, text.text_polys, custom.paths_outline), only
each step done once for every outline together. Typing on an 800-letter text: ~0.8 s -> ~0.3 s.

Exact by construction: the same float operations in the same order (the curve weights are the same Python
expressions, kept in a table). Two spots where NumPy could round differently from Python (np.hypot vs math.hypot
deciding a curve's step count; rint vs round(x, 9)) are checked: a value a hair from where they'd differ is done the
Python way. Anything unusual (grow, open outlines, a one-point outline) -> None: the caller does it the old way.
text.BATCH = False (or env SPIDERWEB_TEXT_BATCH=0) runs the old way everywhere (test text_batch_same)."""
import math

import numpy as np

from notes.paths import ROW_EDGE, keep_longest, parts_notes, spans

MAX_STEPS = 24  # (text.flatten: at most 24 steps a curve piece)
_W = np.zeros((4, MAX_STEPS + 1, MAX_STEPS + 1))  # [weight, steps, i]: text.flatten's own expressions, once each
for _n in range(1, MAX_STEPS + 1):
    for _i in range(1, _n + 1):
        _t = _i / _n
        _W[:, _n, _i] = (1 - _t) ** 3, 3 * (1 - _t) ** 2 * _t, 3 * (1 - _t) * _t * _t, _t ** 3


def _steps(bend, tol):
    """text.flatten's step count for a curve piece this bent."""
    return 1 if bend < 1e-9 else max(2, min(MAX_STEPS, math.ceil(math.sqrt(bend / tol) * 2)))


def flatten_many(P, off, tol):
    """text.flatten of many curves at once. P (N, 2): all their control points one after another, off (S + 1):
    where each curve starts in P. -> (points (M, 2), how many points each curve gave)."""
    from notes.text import _line_dist
    S = len(off) - 1
    ln = np.diff(off)
    nseg = np.where(ln >= 4, (ln - 1) // 3, 0)  # (bezier.segments: pieces of 4 points sharing their ends)
    tot = int(nseg.sum())
    curve = np.repeat(np.arange(S), nseg)
    j = np.arange(tot) - np.repeat(np.cumsum(nseg) - nseg, nseg)
    i0 = off[:-1][curve] + 3 * j
    p0, p1, p2, p3 = P[i0], P[i0 + 1], P[i0 + 2], P[i0 + 3]
    dx, dy = p3[:, 0] - p0[:, 0], p3[:, 1] - p0[:, 1]
    ll = np.hypot(dx, dy)
    flat_end = ll < 1e-12  # (_line_dist's other case: done the Python way below)
    div = np.where(flat_end, 1.0, ll)
    d1 = np.abs((p1[:, 0] - p0[:, 0]) * dy - (p1[:, 1] - p0[:, 1]) * dx) / div
    d2 = np.abs((p2[:, 0] - p0[:, 0]) * dy - (p2[:, 1] - p0[:, 1]) * dx) / div
    bend = np.maximum(d1, d2)
    v = np.sqrt(bend / tol) * 2
    n = np.clip(np.ceil(v), 2, MAX_STEPS).astype(np.int64)
    n[bend < 1e-9] = 1
    # (np.hypot may differ from math.hypot by a hair: where that could change the step count, ask Python)
    near = flat_end | (np.abs(bend / 1e-9 - 1) < 1e-8) | ((bend >= 1e-9) & (np.abs(v - np.rint(v)) < 1e-7))
    for k in np.flatnonzero(near).tolist():
        a, b, c, d = (tuple(map(float, q[k])) for q in (p0, p1, p2, p3))
        n[k] = _steps(max(_line_dist(b, a, d), _line_dist(c, a, d)), tol)
    seg = np.repeat(np.arange(tot), n)
    i = np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n) + 1
    wa, wb, wc, wd = _W[:, n[seg], i]
    q0, q1, q2, q3 = p0[seg], p1[seg], p2[seg], p3[seg]
    stepped = np.empty((len(seg), 2))
    for ax in (0, 1):  # (a * p0 + b * p1 + c * p2 + d * p3, in that order, as flatten)
        stepped[:, ax] = wa * q0[:, ax] + wb * q1[:, ax] + wc * q2[:, ax] + wd * q3[:, ax]
    per = np.zeros(S, np.int64)
    if tot:
        np.add.at(per, curve, n)
    counts = per + 1
    first = np.cumsum(counts) - counts
    out = np.empty((int(counts.sum()), 2))
    is_first = np.zeros(len(out), bool)
    is_first[first] = True
    out[first] = P[off[:-1]]
    out[~is_first] = stepped
    return out, counts


def points_of(curves):
    """Many curves (lists of points) -> (P, off) for flatten_many."""
    lens = np.fromiter((len(c) for c in curves), np.int64, len(curves))
    off = np.concatenate([[0], np.cumsum(lens)])
    P = np.array([p for c in curves for p in c], float).reshape(-1, 2)
    return P, off


def flatten_curves(curves, tol=0.004):
    """[text.flatten(c, tol) for c in curves], as (all points (M, 2), how many each curve gave)."""
    return flatten_many(*points_of(curves), tol)


def split_points(pts, counts):
    """flatten_curves' result as one list of [x, y] points per curve (equal to text.flatten's tuples)."""
    allp, out, a = pts.tolist(), [], 0
    for b in np.cumsum(counts).tolist():
        out.append(allp[a:b])
        a = b
    return out


def round9(a):
    """round(x, 9) of every value: rint(x * 1e9) / 1e9, the same unless x * 1e9 sits a hair from a half (where the
    multiplication's own rounding could tip it): those the Python way."""
    s = a * 1e9
    r = np.rint(s)
    got = r / 1e9
    tie = np.abs(np.abs(s - r) - 0.5) < np.maximum(1e-3, 8 * np.spacing(np.abs(s)))
    for k in np.flatnonzero(tie).tolist():
        got[k] = round(float(a[k]), 9)
    return got


class Polys(list):
    """text_polys' polygons (lists of (beat, pitch)) with the same points as arrays: .arr = (beats, pitches, how
    many points each polygon has), for outline. Never changed in place (shape caches share them)."""
    __slots__ = ("arr",)


def polys_of(bx, by, counts):
    """Polys from the points' beats and pitches."""
    out = Polys()
    xs, ys, a = bx.tolist(), by.tolist(), 0
    for b in np.cumsum(counts).tolist():
        out.append(list(zip(xs[a:b], ys[a:b])))
        a = b
    out.arr = (bx, by, counts)
    return out


def outline(paths, ppq):
    """custom.paths_outline(join_paths(paths), ppq) of a text's closed letter outlines (join_paths gives closed
    polygons back as they are), for all of them at once. paths: Polys from text_polys. None = something unusual
    (not Polys, an open or one-point outline...): the caller does it the old way."""
    arr = getattr(paths, "arr", None)
    if arr is None or len(arr[2]) != len(paths):
        return None
    bx, by, counts = arr
    S = len(counts)
    if not S or (counts < 3).any():
        return None
    e0 = np.cumsum(counts)
    s0 = e0 - counts
    if not ((bx[s0] == bx[e0 - 1]) & (by[s0] == by[e0 - 1])).all():
        return None
    # dedupe: no point equal to the one before it (in its own outline)
    pid0 = np.repeat(np.arange(S), counts)
    keep = np.ones(len(bx), bool)
    keep[1:] = (bx[1:] != bx[:-1]) | (by[1:] != by[:-1])
    keep[s0] = True
    x, y, pid = bx[keep], by[keep], pid0[keep]
    cnt = np.bincount(pid, minlength=S)
    if (cnt < 4).any():
        return None
    e2 = np.cumsum(cnt)
    s2 = e2 - cnt
    if not ((x[s2] == x[e2 - 1]) & (y[s2] == y[e2 - 1])).all():
        return None  # (the closing point was a repeat: rare, the old way)
    # loop_from_left: each outline restarted at its leftmost point (then lowest; the first such)
    m = cnt - 1
    notlast = np.ones(len(x), bool)
    notlast[e2 - 1] = False
    pos = np.arange(len(x))
    cand = np.flatnonzero(notlast)
    order = cand[np.lexsort((pos[cand], y[cand], x[cand], pid[cand]))]
    first_of = order[np.searchsorted(pid[order], np.arange(S))]
    tot = int(cnt.sum())
    pp = np.repeat(np.arange(S), cnt)  # (each point's outline)
    k = np.arange(tot) - np.repeat(s2, cnt)
    src = s2[pp] + ((first_of - s2)[pp] + k) % m[pp]
    X, Y = x[src] * ppq, y[src] * 1
    ps, pe = s2, e2 - 1  # (each outline's first / last point)
    Y = _edges_settled(Y, pp, ps, pe, S)
    # line_notes: pieces turning back in time, straight-up parts on their own
    d = np.sign(np.diff(X))
    d[pe[:-1]] = 0  # (from one outline to the next isn't a segment)
    nz = np.flatnonzero(d)
    dn = d[nz]
    cuts = nz[1:][(dn[1:] != dn[:-1]) & (pp[nz[1:]] == pp[nz[:-1]])]
    lo = np.sort(np.concatenate([ps, cuts]))
    hi = np.sort(np.concatenate([cuts, pe]))
    iq = spans(lo, hi, X[hi] < X[lo])
    q = np.column_stack([X, Y])[iq]
    size = hi - lo + 1
    piece_end = np.cumsum(size) - 1
    same = np.ones(len(q) - 1, bool)
    same[piece_end[:-1]] = False
    up = np.abs(q[1:, 0] - q[:-1, 0]) < 1e-9
    split = np.flatnonzero(same[1:] & same[:-1] & (up[1:] != up[:-1])) + 1
    fst = np.sort(np.concatenate([piece_end - size + 1, split]))
    lst = np.sort(np.concatenate([split, piece_end]))
    size = lst - fst + 1
    ir = spans(fst, lst)
    firsts = np.cumsum(size) - size
    notes, per = parts_notes(q[ir], firsts, np.zeros(len(firsts), bool), counts=True)
    return keep_longest(_late_fixed(notes, np.repeat(pp[iq][ir][firsts], per), X, ps, S))


def _edges_settled(Y, pp, ps, pe, S):
    """paths.edges_settled of every (closed) outline at once."""
    edge = np.abs(Y + 0.5 - np.round(Y + 0.5)) < ROW_EDGE
    if not edge.any():
        return Y
    tot = len(Y)
    level = np.ones(tot, bool)  # (a new height starts here)
    level[1:] = np.abs(np.diff(Y)) > ROW_EDGE
    level[ps] = True
    run = np.cumsum(level) - 1
    st = np.flatnonzero(level)
    en = np.append(st[1:] - 1, tot - 1)
    prev, nxt = st[run] - 1, en[run] + 1
    wrap = np.bincount(pp[st], minlength=S) > 1  # (closed: round past the ends)
    last_start = st[np.searchsorted(st, pe, "right") - 1]
    first_end = en[run[ps]]
    prev = np.where((run == run[ps][pp]) & wrap[pp], last_start[pp] - 1, prev)
    nxt = np.where((run == run[pe][pp]) & wrap[pp], first_end[pp] + 1, nxt)
    on = np.round(Y + 0.5) - 0.5
    has_p, has_n = prev >= ps[pp], nxt <= pe[pp]
    below = ((~has_p | (Y[np.clip(prev, 0, tot - 1)] < on)) & (~has_n | (Y[np.clip(nxt, 0, tot - 1)] < on))
             & (has_p | has_n))
    Y = Y.copy()
    Y[edge] = np.where(below[edge], on[edge] - 2 * ROW_EDGE, on[edge])
    return Y


def _late_fixed(notes, owner, X, ps, S):
    """paths.line_notes' fix of each outline on its own: notes starting on its last tick end there instead of 1
    tick past it, and none on top of a key whose own note already reaches it."""
    right = np.floor(np.maximum.reduceat(X, ps) + 0.5)
    left = np.floor(np.minimum.reduceat(X, ps) + 0.5)
    rn = right[owner]
    late = notes[:, 0] >= rn
    act = (right > left) & (np.bincount(owner[late], minlength=S) > 0)
    late &= act[owner]
    if not late.any():
        return notes
    notes = notes.copy()
    notes[late, 0] = (rn[late] - 1).astype(np.int64)
    notes[late, 1] = rn[late].astype(np.int64)
    long = ~late & act[owner] & (notes[:, 1] >= rn) & (notes[:, 0] < rn - 1)
    kmin = int(notes[:, 2].min())
    code = owner * (int(notes[:, 2].max()) - kmin + 1) + (notes[:, 2] - kmin)  # (outline + key)
    extra = late & np.isin(code, code[long])
    return notes[~extra] if extra.any() else notes


def grow_many(bx, by, counts, d, k):
    """text.offset of every outline at once (the Grow setting), the very same numbers: closed outlines in beats /
    pitch (bx, by, counts), d = each outline's grow (holes shrink), k = the text's beats per key-width. The lengths
    (math.dist / math.hypot) and each outline's turning way (text.area) are Python's own, the rest the same float
    steps in NumPy. -> (bx, by, counts) grown, not rounded."""
    from notes.text import area
    S = len(counts)
    X = bx / k
    Y = by
    xs, ys = X.tolist(), Y.tolist()
    e0 = np.cumsum(counts)
    s0 = e0 - counts
    # pts: each point but the closing one, dropping one closer than 1e-9 to the next (math.dist, as offset does)
    pair = np.ones(len(X), bool)
    pair[e0 - 1] = False  # (the closing point has no next)
    idx = np.flatnonzero(pair)
    far = np.zeros(len(X), bool)
    far[idx] = [math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])) > 1e-9 for i in idx.tolist()]
    pid = np.repeat(np.arange(S), counts)
    n = np.bincount(pid[far], minlength=S)
    keep_whole = n < 3  # (offset gives such an outline back as it was)
    use = far & ~keep_whole[pid]
    P = np.flatnonzero(use)
    ppid = pid[P]
    px, py = X[P], Y[P]
    m = n * ~keep_whole
    e1 = np.cumsum(m)
    s1 = e1 - m
    j = np.arange(len(P))
    nx_i = np.where(j + 1 == e1[ppid], s1[ppid], j + 1)  # (the next point, round the outline)
    pv_i = np.where(j == s1[ppid], e1[ppid] - 1, j - 1)  # (the one before)
    pxl, pyl = px.tolist(), py.tolist()
    sign = np.ones(S)
    for p in np.flatnonzero(m).tolist():
        a, b = int(s1[p]), int(e1[p])
        ring = list(zip(pxl[a:b], pyl[a:b]))
        sign[p] = 1 if area(ring + [ring[0]]) > 0 else -1
    s = sign[ppid]
    dx, dy = px[nx_i] - px, py[nx_i] - py
    ll = np.array([math.hypot(a, b) for a, b in zip(dx.tolist(), dy.tolist())])
    nx_, ny_ = s * dy / ll, -s * dx / ll
    ax, ay = nx_[pv_i], ny_[pv_i]
    bxn, byn = nx_, ny_
    dd = np.asarray(d, float)[ppid]
    dot = ax * bxn + ay * byn
    one = dot > -0.5
    mm = dd / (1 + dot)
    o1x, o1y = px + (ax + bxn) * mm, py + (ay + byn) * mm  # (one point where the two moved edges meet)
    o2x, o2y = px + ax * dd, py + ay * dd  # (a cut-off corner: two points)
    o3x, o3y = px + bxn * dd, py + byn * dd
    per = np.where(one, 1, 2)
    at = np.cumsum(per) - per
    tot = int(per.sum())
    gx, gy = np.empty(tot), np.empty(tot)
    gx[at] = np.where(one, o1x, o2x)
    gy[at] = np.where(one, o1y, o2y)
    two = np.flatnonzero(~one)
    gx[at[two] + 1], gy[at[two] + 1] = o3x[two], o3y[two]
    gpid = np.repeat(ppid, per)
    # each outline: grown points + its first again; outlines kept whole: as they were
    gc = np.bincount(gpid, minlength=S)
    out_c = np.where(keep_whole, counts, gc + 1)
    oe = np.cumsum(out_c)
    os_ = oe - out_c
    OX, OY = np.empty(int(oe[-1]) if S else 0), np.empty(int(oe[-1]) if S else 0)
    ge = np.cumsum(gc)
    gs = ge - gc
    grown = np.flatnonzero(~keep_whole)
    if len(grown):
        dest = np.repeat(os_[grown], gc[grown]) + (np.arange(int(gc[grown].sum())) - np.repeat(
            np.cumsum(gc[grown]) - gc[grown], gc[grown]))
        src = np.concatenate([np.arange(gs[p], ge[p]) for p in grown.tolist()])
        OX[dest], OY[dest] = gx[src], gy[src]
        OX[oe[grown] - 1], OY[oe[grown] - 1] = gx[gs[grown]], gy[gs[grown]]
    for p in np.flatnonzero(keep_whole).tolist():
        OX[os_[p]:oe[p]], OY[os_[p]:oe[p]] = X[s0[p]:e0[p]], Y[s0[p]:e0[p]]
    return OX * k, OY, out_c


def threshold_rows(polys, q0, q1, threshold, sub_rows):
    """text.threshold_spans for every key q0..q1 -> [spans of q0, ...], the very same numbers: each key's lines
    looked at together (crossings, the nonzero rule, the threshold count) instead of one line at a time."""
    E = [(a[0], a[1], b[0], b[1]) for poly in polys for a, b in zip(poly, poly[1:]) if a[1] != b[1]]
    if not E:
        return [[] for _ in range(q0, q1 + 1)]
    xa, ya, xb, yb = np.array(E, float).T
    ylo, yhi = np.minimum(ya, yb), np.maximum(ya, yb)
    dirs = np.where(yb > ya, 1, -1)
    need = max(1, math.ceil(threshold / 100 * sub_rows - 1e-9))
    out_all = []
    for q in range(q0, q1 + 1):
        lo = q - 0.5
        pick = np.flatnonzero((ylo < lo + 1) & (yhi > lo))
        if not len(pick):
            out_all.append([])
            continue
        ys = np.array([lo + (j + 0.5) / sub_rows for j in range(sub_rows)])
        a_x, a_y, b_x, b_y = xa[pick, None], ya[pick, None], xb[pick, None], yb[pick, None]
        cross = (a_y <= ys) != (b_y <= ys)
        ei, jj = np.nonzero(cross)
        if not len(ei):
            out_all.append([])
            continue
        y = ys[jj]
        x = a_x[ei, 0] + (b_x[ei, 0] - a_x[ei, 0]) * (y - a_y[ei, 0]) / (b_y[ei, 0] - a_y[ei, 0])
        d = dirs[pick][ei]
        o = np.lexsort((d, x, jj))  # (each line's crossings by x, then -1 before +1: Python's tuple sort)
        x, d, jj = x[o], d[o], jj[o]
        w = np.cumsum(d)
        first = np.ones(len(jj), bool)
        first[1:] = jj[1:] != jj[:-1]
        base = np.maximum.accumulate(np.where(first, w - d, np.iinfo(np.int64).min))
        w = w - base
        before = w - d
        st = np.flatnonzero((before == 0) & (w != 0))
        en = np.flatnonzero((before != 0) & (w == 0))
        # (starts and ends take turns on a line; a start left open at a line's end makes nothing)
        ns = np.bincount(jj[st], minlength=sub_rows)
        ne = np.bincount(jj[en], minlength=sub_rows)
        rank = np.arange(len(st)) - np.repeat(np.cumsum(ns) - ns, ns)
        st = st[rank < ne[jj[st]]]
        if not len(st):
            out_all.append([])
            continue
        ev_x = np.concatenate([x[st], x[en]])
        ev_d = np.concatenate([np.ones(len(st), np.int64), -np.ones(len(en), np.int64)])
        o = np.lexsort((ev_d, ev_x))
        ev_x, ev_d = ev_x[o], ev_d[o]
        count = np.cumsum(ev_d)
        before = count - ev_d
        on = (before < need) & (need <= count)
        off = (count < need) & (need <= before)
        out, start = [], None
        for k in np.flatnonzero(on | off).tolist():
            xk = float(ev_x[k])
            if on[k]:
                start = xk
            elif out and start - out[-1][1] < 1e-12:
                out[-1][1] = xk
            elif xk > start:
                out.append([start, xk])
        out_all.append(out)
    return out_all
