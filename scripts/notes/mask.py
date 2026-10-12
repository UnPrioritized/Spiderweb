"""Masks (user, 2026-10-12): a shape drawn with the toolbar's Mask tick shows a note pattern through it.

A mask is a PREVIEW only: the shapes under it stay whole and keep sounding; the piano roll draws their notes outside
the mask as faint ghost notes. Every shape a mask touches is covered (not other masks: a mask is never masked).
Its area, on each key row, comes from its own notes:
  Fill / Spam custom shapes   the whole inside (the rows its Fill notes cover)
  anything else               a band round its notes, `keys` up / down and `beats` sideways (a round brush)
Invert = the other way round: the pattern shows everywhere but there (a hole). A note across the edge is trimmed; a
part shorter than mould.SHORT of its note is left out (user: no slivers, the preview = what Apply mask makes).
sh["mask"] = {"keys": int, "beats": float, "lock": beats per key (False = not locked), "invert": bool,
"own": bool (its own notes sound, a border; False = silent, only its line shows)}."""

import json
import math

import numpy as np

from notes.mould import SHORT

MASK_DEFAULTS = {"keys": 2, "beats": 0.125, "lock": 0.0625, "invert": False, "own": True}
MOST_KEYS, MOST_BEATS = 127, 64.0  # the thickness boxes' largest values
SOLID = ("fill", "spam")  # custom shapes with these insides mask with the whole inside
OFF = 1 << 40  # ticks are kept as row * ROW + tick + OFF: one sorted list for every key row
ROW = 1 << 42


def is_mask(sh):
    return bool(sh.get("mask"))


def is_solid(sh):
    """Does the mask show its whole inside (Fill / Spam), not a band round its notes?"""
    return sh["kind"] == "custom" and sh.get("fill") in SOLID and "notes" not in sh


def silent(sh):
    """A mask whose own notes don't sound (a pure cutter)."""
    return is_mask(sh) and not sh["mask"].get("own", True)


def clean_mask(m):
    """sh["mask"] from a file, made valid; None = not a mask."""
    if not isinstance(m, dict):
        return None
    try:
        keys = int(min(MOST_KEYS, max(0, int(m.get("keys", MASK_DEFAULTS["keys"])))))
        beats = float(min(MOST_BEATS, max(0.0, float(m.get("beats", MASK_DEFAULTS["beats"])))))
        lock = m.get("lock", False)
        lock = float(lock) if lock is not False and lock is not None and math.isfinite(float(lock)) and float(lock) > 0 \
            else False
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(beats):
        return None
    return {"keys": keys, "beats": beats, "lock": lock, "invert": m.get("invert") is True,
            "own": m.get("own") is not False}


def area_source(sh):
    """The shape whose notes make the mask's area: a Fill / Spam one as plain Fill (its notes cover the inside; no
    note tool pages, gate or colours), else the mask itself."""
    if not is_solid(sh):
        return sh
    out = {k: v for k, v in sh.items() if k not in ("fx", "glue", "sustain", "cycle", "edge", "areas")}
    out["fill"] = "fill"
    return out


def merged(starts, ends):
    """Spans (coded row * ROW + tick + OFF) sorted and joined where they touch or overlap: (starts, ends)."""
    if not len(starts):
        return starts, ends
    order = np.argsort(starts, kind="stable")
    a, b = starts[order], ends[order]
    reach = np.maximum.accumulate(b)
    new = np.ones(len(a), bool)
    new[1:] = a[1:] > reach[:-1]
    first = np.flatnonzero(new)
    last = np.append(first[1:], len(a)) - 1
    return a[first], reach[last]


def area(notes, keys, ticks, nkeys, solid):
    """The mask's area made from its notes (start, end, key in ticks): (starts, ends) of the coded spans."""
    notes = np.asarray(notes, np.int64)
    if not len(notes):
        z = np.zeros(0, np.int64)
        return z, z
    s, e, k = notes[:, 0], notes[:, 1], notes[:, 2]
    if solid:
        keys, ticks = 0, 0
    parts_a, parts_b = [], []
    for dk in range(-keys, keys + 1):
        # a round brush: rows further from the note reach less far sideways
        reach = int(round(ticks * math.sqrt(max(0.0, 1 - (dk / (keys + 0.5)) ** 2))))
        row = k + dk
        ok = (row >= 0) & (row < nkeys)
        parts_a.append(row[ok] * ROW + s[ok] - reach + OFF)
        parts_b.append(row[ok] * ROW + e[ok] + reach + OFF)
    return merged(np.concatenate(parts_a), np.concatenate(parts_b))


def kept_parts(notes, a, b, invert):
    """Which notes (rows start, end, key, ...) the area (a, b) touches, and the parts of them that show: (hit, the
    notes' row numbers, part starts, part ends). Parts shorter than SHORT of their note are left out."""
    notes = np.asarray(notes)
    n = len(notes)
    if not n or not len(a):
        z = np.zeros(0, np.int64)
        return np.zeros(n, bool), z, z, z
    s, e, k = notes[:, 0].astype(np.int64), notes[:, 1].astype(np.int64), notes[:, 2].astype(np.int64)
    base = k * ROW + OFF
    lo = np.searchsorted(b, base + s, "right")  # the first span ending after the note starts
    hi = np.searchsorted(a, base + e, "left")  # past the last one starting before it ends
    c = np.maximum(hi - lo, 0)
    hit = c > 0
    per = c + 1 if invert else c  # (inverted: the pieces between the spans, one more than them)
    idx = np.repeat(np.arange(n), per)
    j = np.arange(int(per.sum())) - np.repeat(np.cumsum(per) - per, per)
    at = lo[idx] + j
    if invert:
        start = np.where(j == 0, s[idx], b[np.clip(at - 1, 0, len(b) - 1)] - base[idx])
        end = np.where(j == c[idx], e[idx], a[np.clip(at, 0, len(a) - 1)] - base[idx])
    else:
        start, end = a[at] - base[idx], b[at] - base[idx]
    ps, pe = np.maximum(start, s[idx]), np.minimum(end, e[idx])
    keep = (pe > ps) & (pe - ps >= SHORT * (e - s)[idx])
    return hit, idx[keep], ps[keep], pe[keep]


_areas = {}


def mask_area(sh, ppq, nkeys, notes_of):
    """A mask shape's area (starts, ends), remembered. notes_of(shape) = its notes."""
    m = sh["mask"]
    src = area_source(sh)
    key = (json.dumps(src, sort_keys=True, default=str), ppq, nkeys)
    got = _areas.get(key)
    if got is None:
        if len(_areas) > 200:
            _areas.clear()
        got = _areas[key] = area(notes_of(src), m["keys"], int(round(m["beats"] * ppq)), nkeys, is_solid(sh))
    return got


def mask_view(rendered, shapes, ppq, nkeys, notes_of):
    """The notes as the piano roll draws them with the masks: (rows, covered shapes). Rows of the shapes a mask
    covers come twice: all of them as ghosts (velocity -1, painted faint), then the parts that show over them
    (start, end trimmed; every mask's own, they may overlap). No masks = rendered itself."""
    masks = [i for i, sh in enumerate(shapes) if is_mask(sh)]
    if not masks or not len(rendered):
        return rendered, set()
    owner = rendered[:, 5]
    free = np.flatnonzero(~np.isin(owner, masks))  # (a mask is never masked)
    notes = rendered[free]
    covered, parts = set(), []
    for i in masks:
        a, b = mask_area(shapes[i], ppq, nkeys, notes_of)
        invert = shapes[i]["mask"]["invert"]
        hit, idx, ps, pe = kept_parts(notes, a, b, invert)
        if not hit.any():
            continue
        mine = np.unique(notes[hit, 5])
        covered.update(mine.tolist())
        if invert:  # (only the shapes it touches: the others aren't under it)
            ok = np.isin(notes[idx, 5], mine)
            idx, ps, pe = idx[ok], ps[ok], pe[ok]
        got = notes[idx].copy()
        got[:, 0], got[:, 1] = ps, pe
        parts.append(got)
    if not covered:
        return rendered, set()
    under = np.isin(owner, list(covered))
    ghosts = rendered[under].copy()
    ghosts[:, 3] = -1
    return np.concatenate([rendered[~under], ghosts] + parts), covered
