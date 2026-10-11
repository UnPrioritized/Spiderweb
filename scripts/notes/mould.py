"""Cut with this shape (cookie cutter, user 2026-10-12): a shape with an inside cuts others BY NOTES, row by row.

On each key row the cutter's notes are the walls; the gaps between them that lie inside its outline are the inside
(user's (a): the art never overlaps the cutter's notes). Each cut shape becomes two pieces (sliced.py) that keep the
whole's notes: the inside piece the parts in the gaps, the outside one the rest. A note across a wall is trimmed to
fit; a part shorter than SHORT of the note is dropped (user: no slivers, the trade-off being the pieces together miss
it). Kept in the piece as cut["mould"]:
  rows   [[key, from beat, to beat], ...]: the inside gaps, where they were when cut
  side   1 = the inside piece, -1 = the outside one
  loops  the cutter's outline loops [[[beat, key], ...], ...]: the piece's drawing shows only its side
Moving the piece moves them along (sliced.moved_by)."""

import numpy as np

from notes.slice import EPS, inside

SHORT = 0.25  # a part of a cut note shorter than this share of the note is dropped


def mould_rows(walls, loops, ppq):
    """The inside gaps [[key, from beat, to beat]] between the cutter's notes walls (start, end, key in ticks) on
    each key row, kept where the gap's middle is inside its loops."""
    walls = np.asarray(walls, np.int64)
    out = []
    for k in np.unique(walls[:, 2]) if len(walls) else ():
        row = walls[walls[:, 2] == k]
        row = row[np.argsort(row[:, 0], kind="stable")]
        end = None
        for s, e in row[:, :2]:
            if end is not None and s > end and inside(loops, ((s + end) / 2 / ppq, float(k))):
                out.append([int(k), end / ppq, s / ppq])
            end = e if end is None else max(end, e)
    return out


def mould_cut(notes, tracks, m, d, ppq):
    """Notes (start, end, key, any other columns) and tracks: the parts on the mould's side (moved by d since),
    each note trimmed at the walls, parts shorter than SHORT of their note dropped."""
    dk = int(round(d[1]))
    gaps = {}
    for k, b0, b1 in m["rows"]:
        gaps.setdefault(int(k) + dk, []).append((int(round((b0 + d[0]) * ppq)), int(round((b1 + d[0]) * ppq))))
    for g in gaps.values():
        g.sort()
    notes = np.asarray(notes)
    if not len(notes):
        return notes, tracks
    hit = np.isin(notes[:, 2], list(gaps)) if gaps else np.zeros(len(notes), bool)
    rows, kept = [], []
    if m["side"] < 0:  # (rows without gaps: all outside, whole)
        rows.append(notes[~hit])
        kept.append(np.flatnonzero(~hit))
    for i in np.flatnonzero(hit):
        n = notes[i]
        lo, hi = int(n[0]), int(n[1])
        least = SHORT * (hi - lo)
        g = gaps[int(n[2])]
        if m["side"] > 0:
            parts = [(max(lo, a), min(hi, b)) for a, b in g]
        else:
            parts, at = [], lo
            for a, b in g:
                parts.append((at, min(hi, a)))
                at = max(at, b)
            parts.append((at, hi))
        for s, e in parts:
            if e > s and e - s >= least:
                r = n.copy()
                r[0], r[1] = s, e
                rows.append(r[None])
                kept.append([i])
    if not rows:
        return notes[:0], None if tracks is None else np.asarray(tracks)[:0]
    idx = np.concatenate([np.asarray(k, np.int64) for k in kept])
    out = np.concatenate(rows)
    order = np.lexsort((out[:, 0], idx))  # (in the notes' order)
    return out[order], None if tracks is None else np.asarray(tracks)[idx][order]


def moved_mould(m, d):
    """The mould moved by d (beats, keys): for a piece of a mould piece (Split here / Slice on it)."""
    dk = int(round(d[1]))
    return {"rows": [[k + dk, b0 + d[0], b1 + d[0]] for k, b0, b1 in m["rows"]], "side": m["side"],
            "loops": [[[b + d[0], k + d[1]] for b, k in lp] for lp in m["loops"]]}


def piece_loops(sh):
    """A mould piece's (loops where they are now, side), or None (not one, or its outline changed)."""
    from notes.sliced import moved_by
    cut = sh.get("cut")
    m = cut.get("mould") if cut else None
    if not m:
        return None
    d = moved_by(sh)
    if d is None:
        return None
    return [np.asarray(lp, float).reshape(-1, 2) + [d[0], d[1]] for lp in m["loops"]], m["side"]


def clip_to(strokes, loops, side):
    """(beat, key) polylines with the parts on the other side of the loops left out (side 1: inside kept).
    Nothing left = the strokes as they were (the piece still has to be seen and picked)."""
    a = np.concatenate([lp[:-1] for lp in loops])
    e = np.concatenate([lp[1:] - lp[:-1] for lp in loops])
    out = []
    for st in strokes:
        p = np.asarray(st, float).reshape(-1, 2)
        if len(p) < 2:
            continue
        pieces, cur = [], [tuple(p[0])]
        for q0, q1 in zip(p[:-1], p[1:]):
            r = q1 - q0
            den = r[0] * e[:, 1] - r[1] * e[:, 0]
            ok = np.abs(den) > EPS
            den = np.where(ok, den, 1)
            w = a - q0
            t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
            s = (w[:, 0] * r[1] - w[:, 1] * r[0]) / den
            for x in np.sort(t[ok & (t > EPS) & (t < 1 - EPS) & (s >= -EPS) & (s <= 1 + EPS)]):
                pt = tuple(q0 + r * x)
                cur.append(pt)
                pieces.append(cur)
                cur = [pt]
            cur.append(tuple(q1))
        pieces.append(cur)
        for pc in pieces:
            j = (len(pc) - 1) // 2
            mid = ((pc[j][0] + pc[j + 1][0]) / 2, (pc[j][1] + pc[j + 1][1]) / 2)
            if inside(loops, mid) == (side > 0):
                out.append([list(q) for q in pc])
    return out or strokes


def clean_mould(m):
    """cut["mould"] from a file, made valid (raises on anything wrong: the piece becomes a shape of its own)."""
    rows = [[int(k), float(b0), float(b1)] for k, b0, b1 in m["rows"]]
    loops = [[[float(b), float(k)] for b, k in lp] for lp in m["loops"]]
    if not loops or any(len(lp) < 3 for lp in loops):
        raise ValueError
    return {"rows": rows, "side": 1 if m["side"] > 0 else -1, "loops": loops}
