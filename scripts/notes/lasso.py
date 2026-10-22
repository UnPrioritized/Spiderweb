"""The Lasso tool (user 2026-10-22, dev/data/planned-features.md "Lasso slice"): a closed loop drawn on the piano roll
cuts pasted notes where it goes (holes / bites, it needn't go all the way across): on each key row, every note the
loop crosses is cut there, EVERY SLIVER KEPT (no note lost), and the pieces go to two shapes, inside and outside.
Inside = the loop going round it (a figure-8: both halves inside, user); the row's middle decides, like the Slice
tool's line."""
import heapq
import math

import numpy as np

from notes.bezier import fit, seg_point, split
from notes.slice import EPS, _halves

FIT_PX = 2.0  # a freehand loop smoothed on closing stays this near the drawn line (screen pixels)


def row_spans(loop, rows, ppq):
    """{row: sorted tick edges [a0, b0, a1, b1, ...]} of the stretches of each key row's middle inside the closed
    loop ((beat, key) points, back to the first by itself), ticks rounded; rows it doesn't reach left out."""
    pts = np.asarray(loop, float)
    x0, y0 = pts[:, 0], pts[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    up = y1 > y0
    lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
    out = {}
    for r in rows:
        on = (lo <= r) & (r < hi)  # (half open: a point exactly on a row counts once)
        if not on.any():
            continue
        x = x0[on] + (r - y0[on]) * (x1[on] - x0[on]) / (y1[on] - y0[on])
        turn = np.where(up[on], 1, -1)
        order = np.argsort(x, kind="stable")
        x, turn = x[order], turn[order]
        wind = np.cumsum(turn)  # (round the row's middle from the left: not 0 = inside, so a loop twice = inside)
        edges, was = [], False
        for k in range(len(x)):
            now = wind[k] != 0
            if now != was:
                edges.append(int(round(x[k] * ppq)))
                was = now
        if edges:
            out[int(r)] = edges
    return out


def _box_spans(boxes, r, ppq):
    """The kept Select boxes' stretches of row r as sorted tick edges (joined where they overlap), or None (no
    boxes: the whole row)."""
    if boxes is None:
        return None
    got = sorted((int(round(min(b[0], b[2]) * ppq)), int(round(max(b[0], b[2]) * ppq))) for b in boxes
                 if min(b[1], b[3]) < r < max(b[1], b[3]))
    out = []
    for a, b in got:
        if out and a <= out[-1]:
            out[-1] = max(out[-1], b)
        else:
            out += [a, b]
    return out


def _both(e1, e2):
    """Where two sets of stretches (sorted edges) overlap, as sorted edges."""
    out = []
    i = j = 0
    while i < len(e1) and j < len(e2):
        a, b = max(e1[i], e2[j]), min(e1[i + 1], e2[j + 1])
        if a < b:
            out += [a, b]
        if e1[i + 1] < e2[j + 1]:
            i += 2
        else:
            j += 2
    return out


def lasso_pieces(notes, loop, ppq, boxes=None):
    """notes (start, end, key, ...) ints cut where the loop crosses their row -> (which note each piece is from,
    piece starts, piece ends, inside: True where the piece is inside the loop). boxes: the kept Select boxes
    (b0, p0, b1, p1): only inside them counts as inside."""
    notes = np.asarray(notes, np.int64)
    if not len(notes):
        z = np.zeros(0, np.int64)
        return z, z, z, np.zeros(0, bool)
    keys = notes[:, 2]
    spans = row_spans(loop, np.unique(keys), ppq)
    whose, starts, ends, inside = [], [], [], []
    for r, edges in spans.items():
        if boxes is not None:
            edges = _both(edges, _box_spans(boxes, r, ppq))
            if not edges:
                continue
        at = np.nonzero(keys == r)[0]
        lo, hi = notes[at, 0], notes[at, 1]
        e = np.asarray(edges, np.int64)
        i0 = np.searchsorted(e, lo, "right")
        i1 = np.searchsorted(e, hi, "left")
        cnt = np.maximum(i1 - i0, 0)  # (edges strictly inside each note: it's cut there)
        rep = np.repeat(np.arange(len(at)), cnt + 1)
        k = np.arange(len(rep)) - np.repeat(np.cumsum(cnt + 1) - (cnt + 1), cnt + 1)
        last = len(e) - 1
        s = np.where(k == 0, lo[rep], e[np.clip(i0[rep] + k - 1, 0, last)])
        t = np.where(k == cnt[rep], hi[rep], e[np.clip(i0[rep] + k, 0, last)])
        whose.append(at[rep]); starts.append(s); ends.append(t)
        inside.append(np.searchsorted(e, s, "right") % 2 == 1)
    whose = np.concatenate(whose) if whose else np.zeros(0, np.int64)
    if not len(whose):
        z = np.zeros(0, np.int64)
        return z, z, z, np.zeros(0, bool)
    starts, ends, inside = np.concatenate(starts), np.concatenate(ends), np.concatenate(inside)
    keep = ends > starts  # (two edges on one tick: no empty piece)
    return whose[keep], starts[keep], ends[keep], inside[keep]


def lasso_notes(sh, notes, tracks, loop, ppq, boxes=None):
    """Pasted notes (a custom shape holding notes) cut by the loop -> ([outside, inside] pasted-notes shapes with
    sh's other settings, or None when nothing is inside or nothing outside; how many of its notes are inside or
    cut). notes: its (start, end, key, velocity) notes as they sound now (rows above the top key too), tracks: each
    one's track."""
    notes = np.asarray(notes, np.int64)[:, :4] if len(notes) else np.zeros((0, 4), np.int64)
    tracks = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64)
    whose, s, e, inside = lasso_pieces(notes, loop, ppq, boxes)
    if not inside.any():
        return None, 0
    took = np.zeros(len(notes), bool)
    took[whose] = True
    rest = ~took  # (rows the loop doesn't reach, notes outside every stretch: whole, outside)
    cols = lambda w: (notes[w, 2], notes[w, 3], tracks[w])
    out = np.concatenate([np.column_stack([notes[rest, 0], notes[rest, 1] - notes[rest, 0], *cols(rest)]),
                          np.column_stack([s, e - s, *cols(whose)])[~inside]])
    ins = np.column_stack([s, e - s, *cols(whose)])[inside]
    return _halves(sh, [out, ins], ppq), int(len(np.unique(whose[inside])))


def touches(notes, loop, ppq, boxes=None):
    """True when some of these notes (start, end, key, ...) are inside the loop."""
    return bool(lasso_pieces(notes, loop, ppq, boxes)[3].any())


def inside_point(loop, pt):
    """True when the loop goes round pt (beat, key): nonzero winding, as the notes' rule (a figure-8: both halves)."""
    pts = np.asarray(loop, float)
    x0, y0 = pts[:, 0], pts[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    on = (np.minimum(y0, y1) <= pt[1]) & (pt[1] < np.maximum(y0, y1))
    x = x0[on] + (pt[1] - y0[on]) * (x1[on] - x0[on]) / (y1[on] - y0[on])
    return int(np.sum(np.where(y1[on] > y0[on], 1, -1)[x < pt[0]])) != 0


def path_middle(path):
    """The point half way along a polyline ((N, 2) points)."""
    p = np.asarray(path, float).reshape(-1, 2)
    if len(p) < 2:
        return p[0]
    d = np.hypot(*np.diff(p, axis=0).T)
    run = np.concatenate([[0.0], np.cumsum(d)])
    half = run[-1] / 2
    j = min(int(np.searchsorted(run, half, "right")) - 1, len(d) - 1)
    t = (half - run[j]) / d[j] if d[j] > 0 else 0.0
    return p[j] + (p[j + 1] - p[j]) * t


def seg_controls(pts, hd, i):
    """Piece i of a loop (point i to the next) as its 4 curve points, or None when it's straight. hd: each point's
    handles [in, out, corner] or None (no handles: straight on both sides)."""
    j = (i + 1) % len(pts)
    a, b = pts[i], pts[j]
    c1 = hd[i][1] if hd and hd[i] else a
    c2 = hd[j][0] if hd and hd[j] else b
    if c1 == a and c2 == b:
        return None
    return a, c1, c2, b


def curved_loop(pts, hd, steps):
    """The loop's line as points (curved pieces sampled, steps(i, controls) points each), back to the first by
    itself."""
    out = []
    for i in range(len(pts)):
        out.append(list(pts[i]))
        c = seg_controls(pts, hd, i)
        if c:
            n = steps(i, c)
            out += [list(seg_point(*c, k / n)) for k in range(1, n)]
    return out


def split_piece(pts, hd, i, t):
    """A new point at t (0..1) on piece i; the line stays exactly the same (handles where it's curved). Returns the
    new point's number."""
    j = (i + 1) % len(pts)
    c = seg_controls(pts, hd, i)
    if c is None:
        a, b = pts[i], pts[j]
        pts.insert(i + 1, [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
        hd.insert(i + 1, None)
        return i + 1
    flat = split([list(p) for p in c], 0, t)  # [a, a', d, m, e, c', b]
    hd[i] = [list(hd[i][0]) if hd[i] else list(pts[i]), flat[1], hd[i][2] if hd[i] else True]
    hd[j] = [flat[5], list(hd[j][1]) if hd[j] else list(pts[j]), hd[j][2] if hd[j] else True]
    pts.insert(i + 1, flat[3])
    hd.insert(i + 1, [flat[2], flat[4], False])
    return i + 1


def smooth_loop(xy):
    """A freehand loop's screen points (closed straight back to the first) fitted with curves (like Straighten) ->
    (points, handles [in, out, corner]) in screen pixels, at least 3 points."""
    sharp = []
    c = fit(list(map(tuple, xy)) + [tuple(xy[0])], FIT_PX, sharp)
    while len(c) < 10:  # (fewer than 3 points: the longest piece halved)
        s = max(range(len(c) // 3), key=lambda s: sum(math.dist(c[3 * s + k], c[3 * s + k + 1]) for k in range(3)))
        c = split(c, s, 0.5)
    n = len(c) // 3
    pts = [list(c[3 * k]) for k in range(n)]
    hd = [[list(c[3 * k - 1] if k else c[-2]), list(c[3 * k + 1]), k == 0 or k in sharp] for k in range(n)]
    return pts, hd


# ---------------------------------------------------------------- Magnetic (user 2026-10-22, planned-features.md)
# The loop clings to the notes it can cut: it runs BETWEEN key rows (along notes' tops / bottoms) and up / down only
# on a note's start or end, so it never cuts a note. Found as the cheapest way along that grid, a step along a
# note's edge costing its length on screen, one through empty space OPEN times that (so it hugs the notes).

OPEN = 4.0  # a step through empty space costs this many times one along a note's edge
THROUGH = 1000.0  # (cutting through a note: only when there's no other way in reach)
PAD_PX = 40  # the path may go this far round past its two ends (screen pixels)
MOST_NODES = 60000  # a bigger grid (very many notes in reach) = a straight line instead


def magnet_rows(notes, ppq):
    """The notes the loop clings to, (start, end, key) ticks -> {key: (starts, ends)} sorted, in beats."""
    out = {}
    if not len(notes):
        return out
    notes = np.asarray(notes, np.int64)
    order = np.argsort(notes[:, 2], kind="stable")
    keys = notes[order, 2]
    cut = np.flatnonzero(np.diff(keys)) + 1
    for part in np.split(order, cut):
        s, e = notes[part, 0], notes[part, 1]
        ok = e > s
        out[int(notes[part[0], 2])] = (np.sort(s[ok]) / ppq, np.sort(e[ok]) / ppq)
    return out


def magnet_spot(rows, pt, sx, sy, width):
    """Where the loop goes for the mouse at pt (beat, key): the nearest note corner (a start / end on a row's top or
    bottom) within width screen pixels, else the mouse's time on the nearest line between key rows."""
    level = round(pt[1] - 0.5) + 0.5
    best = None
    dk = width / sy
    for k in range(math.floor(pt[1] - dk - 1), math.ceil(pt[1] + dk + 1) + 1):
        got = rows.get(k)
        if not got:
            continue
        t0, t1 = pt[0] - width / sx, pt[0] + width / sx
        for arr in got:
            for x in arr[np.searchsorted(arr, t0):np.searchsorted(arr, t1, "right")]:
                for y in (k - 0.5, k + 0.5):
                    d = math.hypot((x - pt[0]) * sx, (y - pt[1]) * sy)
                    if d <= width and (best is None or d < best[0]):
                        best = (d, [float(x), y])
    return best[1] if best else [max(0.0, pt[0]), level]


def _row_flags(rows, r, xs):
    """Row r against the columns xs: (each gap between columns covered by a note, each column strictly inside a
    note, each column on a note's start / end)."""
    got = rows.get(r)
    n = len(xs)
    if not got:
        return np.zeros(n - 1, bool), np.zeros(n, bool), np.zeros(n, bool)
    s, e = got
    mid = (xs[:-1] + xs[1:]) / 2
    cover = np.searchsorted(s, mid, "right") - np.searchsorted(e, mid, "right") > 0
    inside = np.searchsorted(s, xs, "left") - np.searchsorted(e, xs, "right") > 0
    on = np.zeros(n, bool)
    for arr in (s, e):
        i = np.minimum(np.searchsorted(arr, xs), len(arr) - 1)
        on |= arr[i] == xs
    return cover, inside, on


def magnetic_path(rows, a, b, sx, sy):
    """The way from a to b (beat, key; keys on lines between rows, k + 0.5) clinging to the notes: points where it
    turns, a first, b last; None when there'd be too much to look through."""
    if a == b:
        return [list(a)]
    lo, hi = math.floor(min(a[1], b[1])) - 3, math.floor(max(a[1], b[1])) + 3
    levels = np.arange(lo, hi + 1) + 0.5
    t0, t1 = min(a[0], b[0]) - PAD_PX / sx, max(a[0], b[0]) + PAD_PX / sx
    cols = [np.array([a[0], b[0], t0, t1])]
    for r in range(lo, hi + 2):
        got = rows.get(r)
        if got:
            for arr in got:
                cols.append(arr[np.searchsorted(arr, t0):np.searchsorted(arr, t1, "right")])
    xs = np.unique(np.concatenate(cols))
    C, L = len(xs), len(levels)
    if C * L > MOST_NODES:
        return None
    flags = {r: _row_flags(rows, r, xs) for r in range(lo, hi + 2)}  # (row r sits between levels r - 0.5, r + 0.5)
    dx = np.diff(xs) * sx
    hcost = [(dx * np.where(flags[lo + j][0] | flags[lo + j + 1][0], 1.0, OPEN)).tolist() for j in range(L)]
    vcost = [(sy * np.where(flags[lo + j + 1][1], THROUGH, np.where(flags[lo + j + 1][2], 1.0, OPEN))).tolist()
             for j in range(L - 1)]
    start = int(np.searchsorted(xs, a[0])) + C * int(round(a[1] - levels[0]))
    goal = int(np.searchsorted(xs, b[0])) + C * int(round(b[1] - levels[0]))
    dist, came = {start: 0.0}, {}
    heap = [(0.0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if node == goal:
            break
        if d > dist[node]:
            continue
        j, i = divmod(node, C)
        steps = []
        if i > 0:
            steps.append((node - 1, hcost[j][i - 1]))
        if i < C - 1:
            steps.append((node + 1, hcost[j][i]))
        if j > 0:
            steps.append((node - C, vcost[j - 1][i]))
        if j < L - 1:
            steps.append((node + C, vcost[j][i]))
        for nxt, c in steps:
            nd = d + c
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                came[nxt] = node
                heapq.heappush(heap, (nd, nxt))
    if goal not in dist:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(came[path[-1]])
    pts = [[float(xs[n % C]), float(levels[n // C])] for n in reversed(path)]
    out = [pts[0]]  # (only where it turns)
    for k in range(1, len(pts) - 1):
        p, q, r = out[-1], pts[k], pts[k + 1]
        if not (p[0] == q[0] == r[0] or p[1] == q[1] == r[1]):
            out.append(q)
    out.append(pts[-1])
    out[0], out[-1] = list(a), list(b)
    return out


def tidy_loop(pts):
    """A loop's points with repeats in a row dropped (and a last point on the first), or None when it closes
    nothing (fewer than 3 points or no area)."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > EPS or abs(p[1] - out[-1][1]) > EPS:
            out.append([float(p[0]), float(p[1])])
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= EPS and abs(out[0][1] - out[-1][1]) <= EPS:
        out.pop()
    if len(out) < 3:
        return None
    a = np.asarray(out)  # (not by area: a figure-8's halves cancel out)
    return out if np.ptp(a[:, 0]) > EPS and np.ptp(a[:, 1]) > EPS else None
