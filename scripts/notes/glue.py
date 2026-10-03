"""Glue: notes touching or overlapping on the same key become one long note, like the glue of a well-known piano
roll: fewer notes, the shape looks the same. The shape stays as drawn; sh["glue"] = True (all its notes) or a list
of boxes [u0, v0, u1, v1] as shares of the shape's own box (glue_box: 0..1 across its time and its key rows), so
they move, stretch and flip with it.

A note is glued when its middle is in a box and its key row too. Notes with a gap between them (even 1 tick) stay
apart; the glued note keeps the first note's velocity. Notes of different colours (tracks) never glue together."""

import math

import numpy as np


def clean_glue(g):
    """Glue from a file -> True, a list of boxes, or None (none)."""
    if g is True:
        return True
    if not isinstance(g, list):
        return None
    out = []
    for b in g:
        try:
            u0, v0, u1, v1 = (float(x) for x in b)
        except (TypeError, ValueError):
            continue
        if all(map(math.isfinite, (u0, v0, u1, v1))) and u0 != u1 and v0 != v1:
            out.append([min(u0, u1), min(v0, v1), max(u0, u1), max(v0, v1)])
    return out or None


def glue_box(path):
    """The box glue boxes are shares of: (first beat, lowest key row's edge, last beat, highest key row's edge)
    of a shape's points (path: (N, 2) beats / keys)."""
    b0, p0 = path.min(axis=0)
    b1, p1 = path.max(axis=0)
    return float(b0), float(p0) - 0.5, max(float(b1), float(b0) + 1e-9), float(p1) + 0.5


def to_shares(area, box):
    """A box in beats / keys (any corner order) -> shares of box (glue_box), or None if they don't meet."""
    b0, p0, b1, p1 = box
    lo_b, hi_b = sorted((area[0], area[2]))
    lo_p, hi_p = sorted((area[1], area[3]))
    lo_b, hi_b, lo_p, hi_p = max(lo_b, b0), min(hi_b, b1), max(lo_p, p0), min(hi_p, p1)
    if hi_b <= lo_b or hi_p <= lo_p:
        return None
    w, h = b1 - b0, p1 - p0
    return [(lo_b - b0) / w, (lo_p - p0) / h, (hi_b - b0) / w, (hi_p - p0) / h]


def added(glue, shares):
    """The shape's glue with one more box (True stays True: it already glues everything)."""
    if glue is True or shares is True:
        return True
    return (glue or []) + [shares]


def flipped(glue, sideways):
    if not isinstance(glue, list):
        return glue
    if sideways:
        return [[1 - u1, v0, 1 - u0, v1] for u0, v0, u1, v1 in glue]
    return [[u0, 1 - v1, u1, 1 - v0] for u0, v0, u1, v1 in glue]


def turned(glue, clockwise):
    """Turned 90 degrees with the shape (clockwise: higher keys become later beats)."""
    if not isinstance(glue, list):
        return glue
    if clockwise:
        return [[v0, 1 - u1, v1, 1 - u0] for u0, v0, u1, v1 in glue]
    return [[1 - v1, u0, 1 - v0, u1] for u0, v0, u1, v1 in glue]


def apply_glue(a, glue, box, ppq, tracks):
    """a: notes as an int64 array (start, end, pitch, velocity, then maybe a track column: tracks = True) -> the
    notes after the glue, in the same order (a glued note where its first note was)."""
    if not len(a) or not glue:
        return a
    if glue is True:
        inside = np.ones(len(a), bool)
    else:
        b0, p0, b1, p1 = box
        mid = (a[:, 0] + a[:, 1]) / 2 / ppq
        inside = np.zeros(len(a), bool)
        for u0, v0, u1, v1 in glue:
            t0, t1 = b0 + u0 * (b1 - b0), b0 + u1 * (b1 - b0)
            k0, k1 = p0 + v0 * (p1 - p0), p0 + v1 * (p1 - p0)
            inside |= (mid >= t0 - 1e-9) & (mid <= t1 + 1e-9) & (a[:, 2] >= k0 - 1e-6) & (a[:, 2] <= k1 + 1e-6)
    idx = np.flatnonzero(inside)
    if len(idx) < 2:
        return a
    sel = a[idx]
    who = sel[:, -1] if tracks else np.zeros(len(sel), np.int64)
    order = np.lexsort((sel[:, 1], sel[:, 0], sel[:, 2], who))
    s, e = sel[order, 0], sel[order, 1]
    seg = np.ones(len(order), bool)
    seg[1:] = (sel[order[1:], 2] != sel[order[:-1], 2]) | (who[order[1:]] != who[order[:-1]])
    seg_id = np.cumsum(seg) - 1
    t0 = int(s.min())
    span = int(e.max()) - t0 + 2
    # the latest end so far on each key (segments one after another, each lifted above the ones before)
    reach = np.maximum.accumulate(e - t0 + seg_id * span) - seg_id * span + t0
    new = seg.copy()
    new[1:] |= s[1:] > reach[:-1]  # a gap: a new note
    if new.all():
        return a
    first = np.flatnonzero(new)
    last = np.append(first[1:], len(order)) - 1
    glued = sel[order[first]].copy()
    glued[:, 1] = reach[last]
    keep = np.ones(len(a), bool)
    keep[idx] = False
    out = np.concatenate([a[keep], glued])
    where = np.concatenate([np.flatnonzero(keep), idx[order[first]]])
    return out[np.argsort(where, kind="stable")]
