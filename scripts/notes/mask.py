"""Masks (user, 2026-10-12): a shape drawn with the toolbar's Mask tick shows a note pattern through it.

A mask is a PREVIEW only: the shapes under it stay whole and keep sounding; the piano roll draws their notes outside
the mask as faint ghost notes. Every shape a mask touches is covered (not other masks: a mask is never masked).
Its area, on each key row, comes from its own notes:
  Fill / Spam custom shapes   the whole inside (the rows its Fill notes cover)
  other closed shapes         "inside" (default): between its own notes, inside its outline (as the cookie cutter:
                              the pattern never overlaps them), plus the band below when the thickness isn't 0
  "line" / open lines         a band round its notes
The band is a round brush `size` keys across, measured in the shape's own proportions (`k` beats per key, the screen's
when it became a mask), so a circle gets an even ring whatever the zoom later (user).
Invert = the other way round: the pattern shows everywhere but there (a hole). A note across the edge is cut exactly
there and every piece kept, however short (user: dropping short pieces made long notes vanish while short ones showed).
sh["mask"] = {"size": keys, "k": beats per key, "invert": bool, "own": bool (its own notes sound, a border; False =
silent, only its line shows), "show": "inside" / "line" (missing = inside for closed shapes)}."""

import math

import numpy as np

from notes.mould import mould_rows

MASK_DEFAULTS = {"size": 2.0, "k": 0.0625, "invert": False, "own": True}
MOST_SIZE = 127.0  # the thickness box's largest value (keys)
SOLID = ("fill", "spam")  # custom shapes with these insides mask with the whole inside
SHOWS = ("inside", "line")
OFF = 1 << 40  # ticks are kept as row * ROW + tick + OFF: one sorted list for every key row
ROW = 1 << 42


THICKNESS = ("size",)  # (what a new mask takes from the last one set: never Invert / own, user)


def is_mask(sh):
    return bool(sh.get("mask"))


def can_mask(sh):
    """Hz bass boxes never become masks (user)."""
    return not sh.get("hz")


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
        if "size" not in m and "keys" in m:  # (the first try's keys + beats sideways: kept as the same reach)
            keys, beats = float(m["keys"]), float(m.get("beats", 0))
            m = dict(m, size=keys, k=beats / keys if keys > 0 and beats > 0 else MASK_DEFAULTS["k"])
        size = float(m.get("size", MASK_DEFAULTS["size"]))
        k = float(m.get("k", MASK_DEFAULTS["k"]))
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(size) and math.isfinite(k)):
        return None
    out = {"size": min(MOST_SIZE, max(0.0, size)), "k": min(1e4, max(1e-6, k)), "invert": m.get("invert") is True,
           "own": m.get("own") is not False}
    if m.get("show") in SHOWS:
        out["show"] = m["show"]
    return out


def loops_of(sh):
    """A closed shape's outline loops (beat, key arrays), [] for an open line (slice.cutter_loops)."""
    from notes.slice import cutter_loops
    return cutter_loops(sh)


def shows_inside(sh):
    """Does this (non Fill / Spam) mask show its inside: a closed shape not set to "line"?"""
    show = sh["mask"].get("show")
    return show != "line" and bool(loops_of(sh))


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


def area(notes, size, tpk, nkeys, band=True, gaps=()):
    """The mask's area made from its notes (start, end, key in ticks): (starts, ends) of the coded spans. band: its
    notes widened by a round brush `size` keys across (radius size / 2; tpk = ticks per key sideways); gaps =
    [[key, from tick, to tick]] added as they are (a closed shape's inside)."""
    notes = np.asarray(notes, np.int64)
    parts_a, parts_b = [], []
    if len(gaps):
        g = np.asarray(gaps, np.int64)
        ok = (g[:, 0] >= 0) & (g[:, 0] < nkeys)
        parts_a.append(g[ok, 0] * ROW + g[ok, 1] + OFF)
        parts_b.append(g[ok, 0] * ROW + g[ok, 2] + OFF)
    if band and len(notes):
        # each key row's notes joined into runs first (a big pasted mask: far fewer spans to widen, the same area)
        a, b = merged(notes[:, 2] * ROW + notes[:, 0] + OFF, notes[:, 2] * ROW + notes[:, 1] + OFF)
        k = (a - OFF) // ROW
        s, e = a - OFF - k * ROW, b - OFF - k * ROW
        r = size / 2
        for dk in range(-int(math.floor(r + 1e-9)), int(math.floor(r + 1e-9)) + 1):
            # a round brush in the shape's own proportions: rows further off reach less far sideways
            reach = int(round(tpk * math.sqrt(max(0.0, r * r - dk * dk))))
            row = k + dk
            ok = (row >= 0) & (row < nkeys)
            parts_a.append(row[ok] * ROW + s[ok] - reach + OFF)
            parts_b.append(row[ok] * ROW + e[ok] + reach + OFF)
    if not parts_a:
        z = np.zeros(0, np.int64)
        return z, z
    return merged(np.concatenate(parts_a), np.concatenate(parts_b))


def kept_parts(notes, a, b, invert):
    """Which notes (rows start, end, key, ...) the area (a, b) touches, and the parts of them that show: (hit, the
    notes' row numbers, part starts, part ends). Every piece is kept, however short (user)."""
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
    keep = pe > ps
    return hit, idx[keep], ps[keep], pe[keep]


_areas = {}


def mask_area(sh, ppq, nkeys, notes_of):
    """A mask shape's area (starts, ends), remembered. notes_of(shape) = its notes."""
    from notes.engine import shape_key
    m = sh["mask"]
    src = area_source(sh)
    key = (shape_key(src), ppq, nkeys)  # (big pasted / image notes kept as the strings themselves: quick)
    got = _areas.get(key)
    if got is None:
        if len(_areas) > 16:  # (a few: a big mask's area is many MB)
            _areas.pop(next(iter(_areas)))
        notes = notes_of(src)
        tpk = m["k"] * ppq
        if is_solid(sh):  # (its Fill notes are the inside)
            got = area(notes, 0, 0, nkeys)
        elif shows_inside(sh):  # (between its notes, inside its outline; widened outwards by the thickness)
            gaps = [[k, round(b0 * ppq), round(b1 * ppq)] for k, b0, b1 in
                    mould_rows(np.asarray(notes)[:, :3], loops_of(sh), ppq)] if len(notes) else []
            got = area(notes, m["size"], tpk, nkeys, band=m["size"] > 0, gaps=gaps)
        else:
            got = area(notes, m["size"], tpk, nkeys)
        _areas[key] = got
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
        if not len(a):
            continue
        invert = shapes[i]["mask"]["invert"]
        # (only the notes near the area are looked at closely: millions of others cost one quick pass)
        rows = (a - OFF) // ROW
        lo_t, hi_t = int((a - OFF - rows * ROW).min()), int((b - OFF - rows * ROW).max())
        near = np.flatnonzero((notes[:, 2] >= rows.min()) & (notes[:, 2] <= rows.max()) & (notes[:, 1] > lo_t)
                              & (notes[:, 0] < hi_t))
        hit, idx, ps, pe = kept_parts(notes[near], a, b, False)
        if not hit.any():
            continue
        mine = np.unique(notes[near[hit], 5])
        if invert:  # (every note of the shapes it touches: the ones away from the hole show whole)
            near = np.flatnonzero(np.isin(notes[:, 5], mine))
            hit, idx, ps, pe = kept_parts(notes[near], a, b, True)
        covered.update(mine.tolist())
        got = notes[near[idx]].copy()
        got[:, 0], got[:, 1] = ps, pe
        parts.append(got)
    if not covered:
        return rendered, set()
    under = np.isin(owner, list(covered))
    ghosts = rendered[under].copy()
    ghosts[:, 3] = -1
    return np.concatenate([rendered[~under], ghosts] + parts), covered
