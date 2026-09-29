"""
Paths (lists of (beat, pitch) points) -> notes: the rules every line, polyline, freehand stroke and curve follows.

Shapes are stored as points of (beat, pitch). A line becomes one note per pitch it passes:
each note starts on the tick where the line reaches that pitch (rounded to the nearest tick)
and lasts until the next note starts, so a line over 100 ticks and 13 pitches gives gates like 8, 7, 8, 8, 7...
With "end_dot" on, the last note starts exactly on the shape's last point instead of ending there
(for polylines: on every dot).

Paths are NumPy arrays of (time, pitch) rows here (a line with tiny tumours has hundreds of thousands of points).
"""

import math

import numpy as np

EDGE = 0.5 - 1e-6  # half a pitch row, just inside the row
KEYS = (128, 256)  # the project's key range: the MIDI standard 0-127, or 0-255 (256-key MIDI)
TOP_KEY = KEYS[-1] - 1  # the highest key a shape can ever make (the project's range filters the rest)


def lerp(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)


# ---------------------------------------------------------------- path -> notes

def pitch_of(y):
    return math.floor(y + 0.5)


def dedupe(path):
    """The path as an (N, 2) array without points repeating the one before."""
    a = np.asarray(path, float).reshape(-1, 2)
    keep = np.ones(len(a), bool)
    keep[1:] = (a[1:] != a[:-1]).any(axis=1)
    return a[keep]


def spans(lo, hi, rev=None):
    """Indices lo..hi of every span one after another (rev: that span backwards, hi..lo)."""
    size = hi - lo + 1
    k = np.arange(int(size.sum())) - np.repeat(np.cumsum(size) - size, size)
    if rev is None:
        return np.repeat(lo, size) + k
    return np.where(np.repeat(rev, size), np.repeat(hi, size) - k, np.repeat(lo, size) + k)


def direction_changes(v):
    """Segments (segment i runs from point i to i + 1) that go the other way (up / down) than the last segment
    that moved at all."""
    d = np.sign(np.diff(v))
    nz = np.nonzero(d)[0]
    dn = d[nz]
    return nz[1:][dn[1:] != dn[:-1]]


def _remap(pts, i, j, move_start, move_end, end_dot=False):
    """
    Stretch pitches of pts[i..j] so its first/last pitch row is covered fully instead of half.
    end_dot: instead, the last pitch is only just reached at the end (so its note starts on the last point).
    """
    ys, ye = float(pts[i, 1]), float(pts[j, 1])
    ps, pe = pitch_of(ys), pitch_of(ye)
    if ps == pe:
        return
    d = 1 if pe > ps else -1
    ns = ps - d * EDGE if move_start else ys
    ne = (pe - d * EDGE if end_dot else pe + d * EDGE) if move_end else ye
    k = (ne - ns) / (ye - ys)
    pts[i:j + 1, 1] = ns + (pts[i:j + 1, 1] - ys) * k


def stretch_ends(path, end_dot=False):
    """
    A line from pitch 60 to 64 visits 5 pitches; give each an equal share of time.
    (Without this, the first and last pitch would only get half a share.)
    Only the start of the path and its end are stretched; turning points already get full shares.
    """
    pts = np.array(path, float).reshape(-1, 2)
    if len(pts) < 2:
        return pts
    turns = [0, *direction_changes(pts[:, 1]).tolist(), len(pts) - 1]
    if len(turns) == 2:
        _remap(pts, 0, len(pts) - 1, True, True, end_dot)
    else:
        _remap(pts, 0, turns[1], True, False)
        _remap(pts, turns[-2], len(pts) - 1, False, True, end_dot)
    return pts


def keep_longest(raw):
    """The same key starting on the same tick twice (where pieces meet): keep the longest (in the order the
    first of them came). Notes ending before tick 0 are dropped."""
    raw = raw[raw[:, 1] >= 0]
    if len(raw) < 2:
        return raw
    order = np.lexsort((np.arange(len(raw)), raw[:, 2], raw[:, 0]))
    b = raw[order]
    new = np.ones(len(b), bool)
    new[1:] = (b[1:, 0] != b[:-1, 0]) | (b[1:, 2] != b[:-1, 2])
    at = np.nonzero(new)[0]
    first = np.argsort(order[at])
    return np.column_stack([b[at, 0], np.maximum.reduceat(b[:, 1], at), b[at, 2]])[first]


def loop_from_left(path):
    """A closed loop (first point = last) restarted at its leftmost point, so it splits into pieces running left to
    right (starting mid-slope would leave a join there)."""
    path = path[:-1]
    i = int(np.lexsort((path[:, 1], path[:, 0]))[0])
    return np.concatenate([path[i:], path[:i + 1]])


def ends_forward(path):
    """Does the path reach its last point moving forward in time? (An arc that bends back past its end reaches
    it moving backwards: then the note there already starts on it.)"""
    dt = np.diff(path[:, 0])
    moved = np.nonzero(np.abs(dt) > 1e-12)[0]
    return not len(moved) or bool(dt[moved[-1]] > 0)


def path_notes(path, end_dot=False):
    """A line / curve / arc path (beat points scaled to ticks, first to last point) -> (start, end, pitch) notes.
    A closed loop (a whole circle) has no ends: nothing is stretched, like custom shape outlines."""
    if len(path) > 3 and (path[0] == path[-1]).all():
        path = loop_from_left(path)
        end_dot = False
    else:
        end_dot = end_dot and ends_forward(path)
        path = stretch_ends(path, end_dot)
    return keep_longest(line_notes(path, end_dot))


def line_notes(pts, tail=False):
    """
    (tick, pitch) points -> (start, end, pitch) notes, before keep_longest. The path is split wherever it turns
    back in time (every piece runs left to right) and straight-up parts become their own parts, so their notes
    stay 1 tick instead of sharing the length of the flat part next to them (the sides of a square).
    tail: the last part of a piece ending on the path's last point has its last note start on it.
    """
    n = len(pts)
    cuts = direction_changes(pts[:, 0])
    lo, hi = np.append(0, cuts), np.append(cuts, n - 1)
    q = pts[spans(lo, hi, pts[hi, 0] < pts[lo, 0])]  # every piece left to right, one after another
    size = hi - lo + 1
    piece_end = np.cumsum(size) - 1
    same = np.ones(len(q) - 1, bool)  # q[i] -> q[i + 1] is a segment (not a jump to the next piece)
    same[piece_end[:-1]] = False
    up = np.abs(q[1:, 0] - q[:-1, 0]) < 1e-9
    split = np.nonzero(same[1:] & same[:-1] & (up[1:] != up[:-1]))[0] + 1
    first = np.sort(np.concatenate([piece_end - size + 1, split]))
    last = np.sort(np.concatenate([split, piece_end]))
    tails = np.zeros(len(first), bool)
    if tail:
        tails = np.isin(last, piece_end) & (q[last] == pts[-1]).all(axis=1)
    size = last - first + 1
    return parts_notes(q[spans(first, last)], np.cumsum(size) - size, tails)


def parts_notes(r, first, tails, counts=False):
    """
    Parts running left to right -> (start, end, pitch) notes, one per pitch crossed. Part k = r[first[k]] up to
    the next part's first point, (tick, pitch) rows.
    tails[k]: part k's last note starts on its last point; it gets the same gate as the note before it.
    counts: also return how many notes each part made.
    """
    t, y = r[:, 0], r[:, 1]
    p = np.floor(y + 0.5).astype(np.int64)
    k_parts = len(first)
    last = np.append(first[1:] - 1, len(r) - 1)
    seg = np.ones(max(0, len(r) - 1), bool)
    seg[last[:-1]] = False
    j = np.nonzero(seg)[0]
    j = j[p[j] != p[j + 1]]
    pa, pb = p[j], p[j + 1]
    step = np.sign(pb - pa)
    c = np.abs(pb - pa)
    at = np.repeat(np.cumsum(c) - c, c)
    jj, st = np.repeat(j, c), np.repeat(step, c)
    qq = np.repeat(pa, c) + st * (np.arange(len(at)) - at + 1)
    yy = qq - 0.5 * st  # the row edge the line crosses to enter pitch qq
    ta, tb, ya, yb = t[jj], t[jj + 1], y[jj], y[jj + 1]
    tt = ta + (tb - ta) * (yy - ya) / (yb - ya)

    # entries: each part's first point, then its crossings in order
    pid = np.searchsorted(first, jj, "right") - 1
    per = np.bincount(pid, minlength=k_parts) + 1
    off = np.cumsum(per) - per
    m = int(per.sum())
    et, ep = np.empty(m), np.empty(m, np.int64)
    et[off], ep[off] = t[first], p[first]
    pos = np.arange(len(tt)) + pid + 1
    et[pos], ep[pos] = tt, qq

    starts = np.floor(et + 0.5).astype(np.int64)
    end_tick = np.floor(t[last] + 0.5).astype(np.int64)
    part = np.repeat(np.arange(k_parts), per)
    e_last = off + per - 1
    # A note lasts until the next note that starts on a later tick.
    # (Very steep lines put several pitches on the same tick: all but the last of them get 1 tick, so a tight
    # turn followed by a flat stretch doesn't stack two long notes into a block.)
    same_part = part[1:] == part[:-1]
    later = np.zeros(m, bool)
    later[:-1] = same_part & (starts[1:] > starts[:-1])
    nxt_at = np.minimum.accumulate(np.where(later, np.arange(m), m)[::-1])[::-1]
    inside = nxt_at < e_last[part]
    nxt = np.where(inside, starts[np.minimum(nxt_at + 1, m - 1)], end_tick[part])
    ends = np.maximum(nxt, starts + 1)
    tied = np.zeros(m, bool)
    tied[:-1] = same_part & (starts[1:] == starts[:-1])
    ends[tied] = starts[tied] + 1
    tl = e_last[tails & (per >= 2)]
    tl = tl[starts[tl] == end_tick[tails & (per >= 2)]]
    ends[tl] = starts[tl] + np.maximum(1, ends[tl - 1] - starts[tl - 1])
    notes = np.column_stack([starts, ends, ep])
    return (notes, per) if counts else notes


def dot_segment_notes(path):
    """
    Polyline with "starts exactly on the last point": every segment works like its own line,
    so the note of every dot starts exactly on that dot's tick.
    """
    a, b = path[:-1], path[1:]
    swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))  # every segment left to right
    a, b = np.where(swap[:, None], b, a), np.where(swap[:, None], a, b)
    # _remap with end_dot on each segment: the first pitch covered fully, the last only just reached
    ys, ye = a[:, 1], b[:, 1]
    ps, pe = np.floor(ys + 0.5), np.floor(ye + 0.5)
    d = np.where(pe > ps, 1, -1)
    ns, ne = ps - d * EDGE, pe - d * EDGE
    moved = ps != pe
    with np.errstate(divide="ignore", invalid="ignore"):
        k = (ne - ns) / (ye - ys)
        y0, y1 = ns + (ys - ys) * k, ns + (ye - ys) * k
    pts = np.stack([np.column_stack([a[:, 0], np.where(moved, y0, ys)]),
                    np.column_stack([b[:, 0], np.where(moved, y1, ye)])], axis=1).reshape(-1, 2)
    n = len(a)
    raw, per = parts_notes(pts, np.arange(n) * 2, np.ones(n, bool), counts=True)
    # A dot shared by two segments: keep the next segment's note, not the previous segment's tail
    # (a segment's tail = its last note when it has two or more; notes are compared by value)
    row = np.unique(raw, axis=0, return_inverse=True)[1].ravel()
    in_tails = np.isin(row, row[(np.cumsum(per) - 1)[per >= 2]])
    at = np.unique(raw[:, [0, 2]], axis=0, return_inverse=True)[1].ravel()
    return raw[~(in_tails & np.isin(at, at[~in_tails]))]
