"""The Lasso tool (user 2026-10-22, dev/data/planned-features.md "Lasso slice"): a closed loop drawn on the piano roll
cuts pasted notes where it goes (holes / bites, it needn't go all the way across): on each key row, every note the
loop crosses is cut there, EVERY SLIVER KEPT (no note lost), and the pieces go to two shapes, inside and outside.
Inside = the loop going round it (a figure-8: both halves inside, user); the row's middle decides, like the Slice
tool's line."""
import numpy as np

from notes.slice import EPS, _halves


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
