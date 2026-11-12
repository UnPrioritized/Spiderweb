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
