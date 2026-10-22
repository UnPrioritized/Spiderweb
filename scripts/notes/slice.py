"""Slice: cutting shapes along a straight line drawn with the Slice tool. Lines, curves and arcs are cut where it
crosses them (the window does that, like Split here); a custom shape is cut in two filled halves when the line goes
all the way across it: each half keeps its side's strokes (curves, arcs and polylines cut as themselves; with
formulas, ellipses, freehand made perfect: as polylines) and gets the cut line's pieces inside the
shape as a new edge, so its fill closes there. Areas coloured by hand stay with the half their spot is in."""

import copy
import uuid

import numpy as np

from notes.bezier import anchor_count, sample, seg_point, segments, split
from notes.custom import (CURVE_STEPS, filled_uv, frame_to_uv, has_areas, notes_shape, refit, shape_areas, split_areas,
                          stroke_points)
from notes.gaterange import part_range
from notes.pattern import has_formula

EPS = 1e-9


def crossings(path, a, b, whole_line=False):
    """Where the polyline path ((N, 2) points) crosses the segment a-b, in path order: [(point, s along a-b)].
    whole_line: the infinite line through a and b instead (s may then be outside 0..1)."""
    return [(pt, s) for pt, s, _, _ in _crossings(path, a, b, whole_line)]


def _crossings(path, a, b, whole_line):
    """crossings, each with the piece of the path it's on and how far along that piece: (point, s, piece, t)."""
    p = np.asarray(path, float).reshape(-1, 2)
    if len(p) < 2:
        return []
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    q, r = p[:-1], p[1:] - p[:-1]
    den = r[:, 0] * d[1] - r[:, 1] * d[0]
    ok = np.abs(den) > EPS
    den = np.where(ok, den, 1)
    w = a - q
    t = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den  # along the path's piece
    s = (w[:, 0] * r[:, 1] - w[:, 1] * r[:, 0]) / den  # along a-b
    hit = ok & (t >= -EPS) & (t < 1 - EPS) & (whole_line | ((s >= -EPS) & (s <= 1 + EPS)))
    if len(p) >= 2:  # (the very last point counts too)
        hit[-1] |= ok[-1] & (abs(t[-1] - 1) <= EPS) & (whole_line | ((s[-1] >= -EPS) & (s[-1] <= 1 + EPS)))
    out = []
    for j in np.flatnonzero(hit):
        pt = q[j] + r[j] * min(1.0, max(0.0, t[j]))
        if not out or np.hypot(*(pt - out[-1][0])) > 1e-7:
            out.append((pt, float(s[j]), int(j), float(t[j])))
    return out


def cut_pieces(path, a, b):
    """The polyline cut wherever it crosses the segment a-b: a list of polylines (lists of points)."""
    p = [tuple(map(float, pt)) for pt in path]
    if len(p) < 2:
        return [p]
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    pieces, cur = [], [p[0]]
    for q0, q1 in zip(p, p[1:]):
        q, r = np.asarray(q0), np.asarray(q1) - np.asarray(q0)
        den = r[0] * d[1] - r[1] * d[0]
        cuts = []
        if abs(den) > EPS:
            w = a - q
            t = (w[0] * d[1] - w[1] * d[0]) / den
            s = (w[0] * r[1] - w[1] * r[0]) / den
            if EPS < t < 1 - EPS and -EPS <= s <= 1 + EPS:
                cuts.append(t)
            elif abs(t - 1) <= EPS and -EPS <= s <= 1 + EPS and q1 != p[-1]:
                cuts.append(1.0)  # (right on a point: cut there)
        for t in cuts:
            pt = tuple(q + r * t)
            cur.append(pt)
            if len(cur) >= 2:
                pieces.append(cur)
            cur = [pt]
        if not cuts or cuts[-1] < 1:
            cur.append(q1)
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces


def exact(st):
    """Is the stroke cut as itself (a curve into curves, an arc into arcs, a polyline into polylines) rather than
    as the points it's drawn with? Not with formulas (they're laid along the whole stroke), not an ellipse or a
    freehand stroke made perfect."""
    return st["kind"] in ("curve", "arc", "poly") and not has_formula(st) and not st.get("smooth")


def curve_cuts(pts, a, b):
    """Where a curve stroke (bezier.py points) crosses the line through a-b, found on the points it's drawn with,
    then exactly on the curve: [(u = segment + how far along it, point, s along a-b)]."""
    d = np.asarray(b, float) - np.asarray(a, float)
    a = np.asarray(a, float)
    segs = segments(pts)

    def f(sg, t):  # (which side of the line, and how far)
        x, y = seg_point(*segs[sg], t)
        return d[0] * (y - a[1]) - d[1] * (x - a[0])
    out = []
    for _, _, j, t in _crossings(sample(pts, CURVE_STEPS), a, a + d, True):
        sg, i = divmod(j, CURVE_STEPS)
        lo, hi = i / CURVE_STEPS, (i + 1) / CURVE_STEPS
        flo, fhi = f(sg, lo), f(sg, hi)
        if flo == 0 or fhi == 0 or flo * fhi > 0:  # (right on a drawn point, or only touching it): there
            lo = hi = lo if flo == 0 else hi if fhi == 0 else lo + (hi - lo) * min(1.0, max(0.0, t))
        for _ in range(60):
            if hi - lo < 1e-14:
                break
            mid = (lo + hi) / 2
            if (f(sg, mid) > 0) == (flo > 0):
                lo = mid
            else:
                hi = mid
        u = (lo + hi) / 2
        pt = np.asarray(seg_point(*segs[sg], u))
        out.append((sg + u, pt, float(np.dot(pt - a, d) / np.dot(d, d))))
    return out


def curve_pieces(st, us):
    """A curve stroke cut at these spots along it (u = segment + how far along it) -> its pieces, each a curve of
    the same shape (bezier.split), corners kept."""
    nseg = len(segments(st["pts"]))
    us = sorted({round(u, 12) for u in us if 1e-9 < u < nseg - 1e-9})
    at_anchor = [u for u in us if abs(u - round(u)) < 1e-9]
    splits = [u for u in us if u not in at_anchor]
    pts = [list(p) for p in st["pts"]]
    done = {}
    for u in reversed(splits):  # (the last first: the earlier segments stay as they were)
        sg = int(u)
        pts = split(pts, sg, (u - sg) / done.get(sg, 1.0))
        done[sg] = u - sg

    def anchor(u):  # (an anchor's number once split: one there already, or the new one)
        return round(u) + sum(x < u for x in splits) if u not in splits else int(u) + 1 + sum(x < u for x in splits)
    ends = [0] + [anchor(u) for u in us] + [anchor_count(pts) - 1]
    sharp = [anchor(m) for m in st.get("sharp", ())]
    out = []
    for x, y in zip(ends, ends[1:]):
        piece = {"kind": "curve", "pts": pts[3 * x:3 * y + 1]}
        if any(x < m < y for m in sharp):
            piece["sharp"] = [m - x for m in sharp if x < m < y]
        out.append(piece)
    return out


def joined_curve(c0, c1):
    """Two curve pieces where c0 ends and c1 starts (a closed curve's last and first pieces) made one; that anchor a
    corner unless its handles lie on a line through it."""
    n = anchor_count(c0["pts"]) - 1
    pts = c0["pts"] + c1["pts"][1:]
    p, h0, h1 = (np.asarray(pts[k], float) for k in (3 * n, 3 * n - 1, 3 * n + 1))
    a, b = h0 - p, h1 - p
    smooth = abs(float(a[0] * b[1] - a[1] * b[0])) <= 1e-9 * (np.hypot(*a) * np.hypot(*b) or 1) and np.dot(a, b) <= 0
    sharp = c0.get("sharp", []) + ([] if smooth else [n]) + [m + n for m in c1.get("sharp", [])]
    return dict({"kind": "curve", "pts": pts}, **({"sharp": sharp} if sharp else {}))


def side(pts, a, b):
    """Which side of the line a-b the points lie on (+1 / -1): the point furthest from it decides; 0 = on it."""
    p = np.asarray(pts, float).reshape(-1, 2)
    d = np.asarray(b, float) - np.asarray(a, float)
    c = d[0] * (p[:, 1] - a[1]) - d[1] * (p[:, 0] - a[0])
    k = int(np.argmax(np.abs(c)))
    return 0 if abs(c[k]) < 1e-9 else (1 if c[k] > 0 else -1)


def inside(polys, pt):
    """Is pt inside the drawing (even-odd: how many times a ray to the right crosses its strokes)?"""
    x, y = pt
    n = 0
    for poly in polys:
        p = np.asarray(poly, float).reshape(-1, 2)
        if len(p) < 2:
            continue
        x0, y0, x1, y1 = p[:-1, 0], p[:-1, 1], p[1:, 0], p[1:, 1]
        c = (y0 > y) != (y1 > y)
        xs = x0[c] + (y - y0[c]) * (x1[c] - x0[c]) / (y1[c] - y0[c])
        n += int((xs > x).sum())
    return n % 2 == 1


def is_closed(poly):
    return len(poly) > 2 and np.hypot(*(np.asarray(poly[0], float) - np.asarray(poly[-1], float))) < 1e-9


def stroke_pieces(st, poly, a, b, us=None):
    """One stroke (poly = its stroke_points) cut where it crosses the segment a-b -> its pieces (strokes without
    role / colour). us: a curve cut as itself, at these spots (curve_cuts' u). Closed: its last and first pieces
    are one."""
    joins = is_closed(poly) and side([poly[0]], a, b)
    if us is not None:
        pieces = curve_pieces(st, us)
        if joins and len(pieces) > 1:
            pieces = [joined_curve(pieces[-1], pieces[0])] + pieces[1:-1]
        return pieces
    pieces = cut_pieces(st["pts"] if st["kind"] == "poly" and exact(st) else poly, a, b)
    if joins and len(pieces) > 1:
        pieces = [pieces[-1] + pieces[0][1:]] + pieces[1:-1]
    if st["kind"] == "arc" and exact(st):  # (an arc's piece is an arc: its ends and a point between)
        return [{"kind": "arc", "pts": [list(p[0]), list(p[len(p) // 2]), list(p[-1])], "k": st.get("k", 1.0)}
                if len(p) > 2 else {"kind": "poly", "pts": [list(pt) for pt in p]} for p in pieces]
    keep = {k: st[k] for k in ("free", "k") if k in st and st["kind"] == "poly" and exact(st)}
    return [dict(keep, kind="poly", pts=[list(pt) for pt in p]) for p in pieces]


def slice_stroke(st, a, b, slack=0.0):
    """The drawer's Slice: one stroke cut along the segment a-b -> its pieces (keeping its role, colour and layer),
    or None when it isn't cut. A closed stroke (circle, square...) is cut only when the segment goes all the way
    across it (user, like the piano roll); an open one wherever the segment crosses it (not at its own ends).
    slack: how far past its ends (board units) the segment still counts, for a curve (an end stuck onto the
    points it's drawn with lies a hair off the curve itself)."""
    poly = stroke_points(st)
    if len(poly) < 2:
        return None
    a, b = np.asarray(a, float), np.asarray(b, float)
    length = np.hypot(*(b - a))
    if length < EPS:
        return None
    far = 1e-6 + slack / length  # (in s along a-b)
    closed = is_closed(poly)
    if closed:
        got = crossings(poly, a, b, whole_line=True)
        if len(got) < 2 or any(s < -far or s > 1 + far for _, s in got):
            return None
    us = None
    if st["kind"] == "curve" and exact(st):
        found = [(u, s) for u, _, s in curve_cuts(st["pts"], a, b)]
        if not closed:
            found = [(u, s) for u, s in found if -far <= s <= 1 + far]
        us = [u for u, _ in found]
    pieces = stroke_pieces(st, poly, a, b, us)
    pieces = [p for p in pieces if len(p["pts"]) >= 2
              and np.ptp(np.asarray(stroke_points(p), float).reshape(-1, 2), axis=0).max() > 1e-9]
    if len(pieces) < 2:
        return None
    keep = {k: copy.deepcopy(st[k]) for k in ("role", "colour", "layer") if k in st}
    for p in pieces:  # (plain numbers, like drawn points)
        p["pts"] = [[float(u), float(v)] for u, v in p["pts"]]
    return [dict(keep, **p) for p in pieces]


def slice_custom(sh, a, b, ppq):
    """A custom shape cut along the segment a-b (beats, keys) -> its two halves, or None when the segment doesn't
    go all the way across it (or misses it). A spam gate Range is shared out: each half gets its part of it. Glue
    boxes are left as they were: the caller moves them (glue.for_part)."""
    to_uv = frame_to_uv(sh["pts"])
    if to_uv is None:
        return None
    a, b = np.array(to_uv(*a)), np.array(to_uv(*b))
    if np.hypot(*(b - a)) < EPS:
        return None
    polys = [stroke_points(st) for st in sh["strokes"]]
    each, cuts, at = {}, {}, {}
    for n, (st, poly) in enumerate(zip(sh["strokes"], polys)):
        got = crossings(poly, a, b, whole_line=True)
        if any(s < -1e-6 or s > 1 + 1e-6 for _, s in got):
            return None  # (the line crosses the shape past the segment's end: not all the way across)
        if st["kind"] == "curve" and exact(st):  # (where it really crosses: the halves keep the curve)
            cuts[n] = curve_cuts(st["pts"], a, b)
            got = [(pt, s) for _, pt, s in cuts[n]]
        each[n] = [s for _, s in got]
        for pt, s in got:  # (the cut lines end exactly where the pieces do: a hair's gap left Overlaps cancel out
            at.setdefault(round(s, 9), [float(x) for x in pt])  # off filling nothing, its loops open)
    if sum(map(len, each.values())) < 2:
        return None
    amap = shape_areas(sh)
    groups = [list(range(len(polys)))]
    if sh.get("union") and sh.get("fill") in ("fill", "spam") and not has_areas(sh):
        # (Overlaps cancel out off: each closed stroke closed along the cut by lines of its own, so its pieces stay
        # a loop like the whole's; one lid for all left the inner ones lines ending on a line = notes of their own)
        groups = [[n] for n, p in enumerate(polys) if is_closed(p) and "role" not in sh["strokes"][n]]
        rest = [n for n in range(len(polys)) if [n] not in groups]
        groups += [rest] if rest else []
    cut_lines = []
    ss = sorted(set(round(s, 9) for got in each.values() for s in got))
    for group in groups:
        mine, last = [polys[n] for n in group], None
        for s0, s1 in zip(ss, ss[1:]):
            mid = a + (b - a) * (s0 + s1) / 2
            if s1 - s0 <= 1e-9:
                continue
            line = {"kind": "poly", "pts": [list(at[s0]), list(at[s1])]}
            if inside(mine, mid) or (len(groups) == 1 and sh.get("union")
                                     and filled_uv(sh, [mid[0]], [mid[1]])[0]):
                # (Overlaps cancel out off: the bands even-odd leaves out are filled too, so they need their edge)
                pass
            elif len(groups) == 1 and amap is not None and int(amap.cell([mid[0]], [mid[1]])[0]) not in (-1, amap.outside()):
                # (a closed-in area even-odd leaves out: closed by a fill line, so its colour stays in it, user
                # 2026-10-17; a plain line there doubled notes where the cut lines' ends meet)
                line["role"] = "cut"
            else:
                last = None
                continue
            if last is not None and last.get("role") == line.get("role"):
                # (one line on from the last: ends meeting doubled notes; a point at every crossing it passes, or
                # the faces where other lines end on it got lost)
                last["pts"].append(line["pts"][-1])
            else:
                cut_lines.append(line)
                last = line
    halves = {1: [], -1: []}
    for n, (st, poly) in enumerate(zip(sh["strokes"], polys)):
        pieces = stroke_pieces(st, poly, a, b, [u for u, _, _ in cuts[n]] if n in cuts else None)
        keep = {k: st[k] for k in ("role", "colour") if k in st}
        ends = np.array(list(at.values()), float).reshape(-1, 2)
        for piece in pieces:
            for i in (0, -1):  # (a curve split at a crossing ends a hair off the point found: onto it)
                d = np.hypot(*(ends - piece["pts"][i]).T)
                if len(d) and d.min() < 1e-6:
                    piece["pts"][i] = list(ends[int(np.argmin(d))])
            k = side(stroke_points(piece), a, b)
            if k:
                halves[k].append(dict(keep, **piece))
    if not halves[1] or not halves[-1] or not any("role" not in line for line in cut_lines):
        return None
    out = []
    for k in (1, -1):
        new = copy.deepcopy({key: v for key, v in sh.items()
                             if key not in ("strokes", "areas", "polygon", "from", "name")})
        new["name"] = sh.get("name", "")
        new["strokes"] = halves[k] + copy.deepcopy(cut_lines)
        areas = split_areas(sh, new)
        if areas:
            new["areas"] = areas
        refit(new)
        if sh.get("range"):
            new["gate"], new["range"] = part_range(sh, new, ppq)
        out.append(new)
    return out


def clip_segment(a, b, box):
    """The part of the segment a-b inside box (b0, p0, b1, p1 in any order), or None."""
    lo = np.array([min(box[0], box[2]), min(box[1], box[3])], float)
    hi = np.array([max(box[0], box[2]), max(box[1], box[3])], float)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    t0, t1 = 0.0, 1.0
    for k in (0, 1):
        if abs(d[k]) < EPS:
            if not lo[k] <= a[k] <= hi[k]:
                return None
            continue
        u0, u1 = sorted(((lo[k] - a[k]) / d[k], (hi[k] - a[k]) / d[k]))
        t0, t1 = max(t0, u0), min(t1, u1)
        if t0 > t1:
            return None
    return tuple(a + d * t0), tuple(a + d * t1)


def open_end(notes, end, ppq):
    """True if the Slice tool's line end (beat, key) is in empty space: on the key rows it touches, it's before the
    row's first note or after its last end (or the row is empty)."""
    t, k = end[0] * ppq, end[1]
    for r in range(int(np.floor(k - 0.5 + EPS)), int(np.floor(k + 0.5 + EPS)) + 1):
        if abs(r - k) > 0.5 + EPS:
            continue
        row = notes[notes[:, 2] == r]
        if len(row) and row[:, 0].min() < t < row[:, 1].max():
            return False
    return True


def slice_notes(sh, notes, tracks, a, b, ppq, box=None, seen=None):
    """Pasted notes (a custom shape holding notes, custom.py) cut by the Slice tool along a-b (beats, keys), by time
    (user, 2026-10-10: it needn't go all the way across): on each key row the line crosses, the notes before the
    spot it crosses at go to a new shape (user), a note sounding there cut in two at that tick; rows it doesn't cross
    stay whole with the notes after it. notes: its (start, end, key, velocity) notes as they sound now (rows above
    the top key too), tracks: each one's track. -> (the two halves, each a pasted-notes shape around its own notes
    with sh's other settings, or None: the line touches no note's row between its first note and last end, or one
    side would be empty; crossed: the line went over the notes' box, for the status line). A flat line cuts by key
    instead: flat_cut (box, seen: its kept Select box and the piano roll's key count)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    dk = b[1] - a[1]
    if not len(notes):
        return None, False
    notes = np.asarray(notes, np.int64)[:, :4]
    if abs(dk) < EPS:  # (a flat line runs between key rows: it crosses none)
        return flat_cut(sh, notes, tracks, a, b, ppq, box, seen)
    lo, hi, keys = notes[:, 0], notes[:, 1], notes[:, 2]
    t0, t1, k0, k1 = int(lo.min()), int(hi.max()), int(keys.min()), int(keys.max())
    if (max(a[0], b[0]) * ppq <= t0 or min(a[0], b[0]) * ppq >= t1 or max(a[1], b[1]) < k0
            or min(a[1], b[1]) > k1):  # (nowhere near them: quick, a paste can be millions of notes)
        return None, False
    rows = np.arange(k1 + 1)
    s = (rows - a[1]) / dk  # (where along a-b it crosses each row's middle)
    row_on = (s >= -EPS) & (s <= 1 + EPS)
    row_x = np.round((a[0] + (b[0] - a[0]) * s) * ppq).astype(np.int64)
    inside = row_on[k0:] & (row_x[k0:] > t0) & (row_x[k0:] < t1)
    crossed = bool(inside.any())
    on, x = row_on[keys], row_x[keys]
    # (it has to go through a note or between a row's notes somewhere: a note starting before it on the row, and one
    # ending after it)
    early = np.bincount(keys[on & (lo < x)], minlength=k1 + 1) > 0
    late = np.bincount(keys[on & (hi > x)], minlength=k1 + 1) > 0
    if not (early & late).any():
        return None, crossed
    tracks = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64)
    # rows past an end in empty space (user, 2026-10-12): each note goes to the side of the line carried on, by its
    # middle, never cut. Past an end inside the notes (cutting a corner off): they stay whole after it.
    sk = s[keys]
    past = ~on & (((sk < 0) & open_end(notes, a, ppq)) | ((sk > 1) & open_end(notes, b, ppq)))
    side = past & (lo + hi < 2 * x)
    rest, before = (~on & ~side) | (on & (hi > x)), (on & (lo < x)) | side
    cols = notes[:, 3], tracks
    return _halves(sh, [np.column_stack([s0, s1 - s0, keys, *cols])[keep] for keep, s0, s1 in
                        ((rest, np.where(on, np.maximum(lo, x), lo), hi), (before, lo, np.minimum(hi, x)))],
                   ppq), crossed


def flat_cut(sh, notes, tracks, a, b, ppq, box=None, seen=None):
    """slice_notes with a flat line (user, 2026-10-18): the notes above it, only within its length, go to a new shape;
    the rest (left, right and below) stays one piece; a note across one of its ends is cut in two at that tick.
    Only cut with notes on both sides of it there: rows past the top key (seen) don't count, a kept Select box
    (b0, p0, b1, p1) = only the rows inside it. notes: int (start, end, key, velocity). -> (halves or None,
    crossed: it touched the notes' box)."""
    tracks = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64)
    lo, hi, keys = notes[:, 0], notes[:, 1], notes[:, 2]
    ta, tb = sorted((int(round(a[0] * ppq)), int(round(b[0] * ppq))))
    if ta >= tb:  # (a drag that snapped to one spot: no line)
        return None, False
    k = a[1]
    crossed = bool(ta < hi.max() and tb > lo.min() and keys.min() - 0.5 - EPS <= k <= keys.max() + 0.5 + EPS)
    along = (lo < tb) & (hi > ta)  # (sounding somewhere along it)
    if box is not None:
        along &= (keys > min(box[1], box[3])) & (keys < max(box[1], box[3]))
    up = along & (keys > k + EPS)
    shown = keys < seen if seen else True
    if not (up & shown).any() or not (along & ~up & shown).any():
        return None, crossed  # (nothing to see on one side of it there; nothing left behind: _halves)
    cols = keys, notes[:, 3], tracks
    stay = [np.column_stack([lo, hi - lo, *cols])[~up]]
    for keep, s0, s1 in ((up & (lo < ta), lo, np.minimum(hi, ta)), (up & (hi > tb), np.maximum(lo, tb), hi)):
        stay.append(np.column_stack([s0, s1 - s0, *cols])[keep])  # (the bits of notes sticking out past its ends)
    s0, s1 = np.maximum(lo, ta), np.minimum(hi, tb)
    return _halves(sh, [np.concatenate(stay), np.column_stack([s0, s1 - s0, *cols])[up]], ppq), crossed


def _halves(sh, parts, ppq):
    """slice_notes' two pieces: each (start, length, key, velocity, track) array -> a pasted-notes shape with sh's
    other settings, sharing its paste id. None if one is empty."""
    if any(not len(n) for n in parts):
        return None
    paste = sh.get("paste") or uuid.uuid4().hex[:12]  # (its tracks keep their channels across the pieces: render)
    from notes.gateflip import colours_back
    mirrored = bool(colours_back(sh))  # (a gate flipped paste: the pieces keep its colours' channels)
    from notes.passengers import shared_out
    rides = shared_out(sh, parts, ppq) if "cc" in sh else [None] * len(parts)  # (its CCs by time per track)
    out = []
    for n, cc in zip(parts, rides):
        half = {k: copy.deepcopy(v) for k, v in sh.items()
                if k not in ("notes", "pts", "vel_env", "fx", "glue", "between", "gate_flip", "flip_colours", "unmerge",
                              "leftover_of", "cc")}
        # (pages / glue / a velocity line / a gate aware flip: in its notes; a merge's notes cut: no Split back)
        half.update(notes_shape(n, ppq, sh.get("name")), paste=paste)
        if cc:
            half["cc"] = cc
        if "unmerge" in sh:  # (no Split back any more: not called "Merged notes", hunt)
            from files.lang import tr
            half["name"] = tr("join_split.plain_name", name=sh.get("name") or "")
        if mirrored:
            half["flip_colours"] = True
        if "name" not in sh:
            half.pop("name")
        out.append(half)
    return out


# ---------------------------------------------------------------- cookie cutter (user, 2026-10-12)

def cutter_loops(sh):
    """The closed loops (beat, key arrays) that make a shape's inside, for "Cut with this shape": a custom shape's
    filled loops (as Fill sees them), a closed polyline / curve / arc. [] = no inside (an open line, text, a picture,
    pasted notes)."""
    from notes.between import closed
    from notes.custom import fill_plan
    from notes.engine import cached_path
    if sh["kind"] == "custom":
        if "notes" in sh or "picture" in sh or sh.get("text") or not sh.get("strokes"):
            return []
        return [np.asarray(p, float).reshape(-1, 2) for p in fill_plan(sh)["polys"] if len(p) > 3]
    if sh["kind"] in ("poly", "curve", "arc") and closed(sh):
        return [np.asarray(cached_path(sh), float).reshape(-1, 2)]
    return []

