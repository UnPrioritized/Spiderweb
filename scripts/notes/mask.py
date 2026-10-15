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
Invert = the other way round: the pattern shows everywhere but there (a hole). While its own notes sound (a border),
the pattern never overlaps them (user), inverted or not. A note across the edge is cut exactly
there and every piece kept, however short (user: dropping short pieces made long notes vanish while short ones showed).
sh["mask"] = {"size": keys, "k": beats per key, "invert": bool, "own": bool (its own notes sound, a border; False =
silent, only its line shows), "show": "inside" / "line" (missing = inside for closed shapes), and in a group (right-click
Combine masks, user): "group": number, "mode": "add" / "subtract" / "intersect", "colour": the group's line colour}.
A group's members are worked through in the Shapes list's order, top first; the first always adds (user, like After
Effects); Apply on any member = the whole group. A group left with one member = a mask on its own."""

import math
import random

import numpy as np

from notes.mould import mould_rows

MASK_DEFAULTS = {"size": 2.0, "k": 0.0625, "invert": False, "own": True}
MOST_SIZE = 127.0  # the thickness box's largest value (keys)
SOLID = ("fill", "spam")  # custom shapes with these insides mask with the whole inside
SHOWS = ("inside", "line")
MODES = ("add", "subtract", "intersect")
GROUP_COLOURS = 6  # (a small set, look.MASK_GROUPS: groups may share one, user)
OFF = 1 << 40  # ticks are kept as row * ROW + tick + OFF: one sorted list for every key row
ROW = 1 << 42


THICKNESS = ("size",)  # (what a new mask takes from the last one set: never Invert / own, user)


def is_mask(sh):
    return bool(sh.get("mask"))


def can_mask(sh):
    """Hz bass boxes never become masks (user)."""
    return not sh.get("hz")


def pinned_slots(sh, mode):
    """Are sh's note tracks its channel slots (render's fixed)? A picture always; Apply mask's notes: copied from a
    picture always (own_slots True), else in Multi channel, wherever it's moved (user, 2026-10-12: colours swapped
    once moved; own_slots = its first point when made, no longer checked)."""
    if "picture" in sh:
        return True
    o = sh.get("own_slots")
    if o is True:
        return True
    return isinstance(o, list) and mode == "auto"


def picture_like(sh):
    """Painted in the pictures' colours: a picture, or Apply mask's notes copied from one."""
    return "picture" in sh or "picture_notes" in sh


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
    g, c = m.get("group"), m.get("colour")
    if isinstance(g, (int, float)) and not isinstance(g, bool) and math.isfinite(g) and g == int(g) and 0 < g < 1e9:
        out["group"] = int(g)
        out["mode"] = m["mode"] if m.get("mode") in MODES else "add"
        ok = isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c == int(c)
        out["colour"] = int(c) % GROUP_COLOURS if ok else 0
    return out


def mask_units(shapes):
    """The masks worked out together: a list of lists of shape numbers, each group's members in the list's order (top
    first), every mask not in a group (or the last one left in it) on its own."""
    units, groups = [], {}
    for i, sh in enumerate(shapes):
        if is_mask(sh):
            g = sh["mask"].get("group")
            if g is None:
                units.append([i])
            elif g in groups:
                groups[g].append(i)
            else:
                groups[g] = [i]
                units.append(groups[g])
    return units


GROUP_KEYS = ("group", "mode", "colour")


def new_group_id(shapes):
    """A group number no mask here uses, picked at random so it's never used again later or in another project (a
    mask pasted / split back joins only its own group, if that is still there)."""
    used = {sh["mask"].get("group") for sh in shapes if is_mask(sh)}
    while True:
        g = random.randrange(1, 10 ** 9)
        if g not in used:
            return g


def tidy_groups(shapes):
    """No hidden group settings (hunt 2026-10-12): a mask left alone in its group loses them (so a duplicate of it
    is a second lone mask), a group's first member is Add (as the panel shows it). True if anything changed."""
    changed = False
    for u in mask_units(shapes):
        m = shapes[u[0]]["mask"]
        if "group" not in m:
            continue
        if len(u) == 1:
            shapes[u[0]]["mask"] = {k: v for k, v in m.items() if k not in GROUP_KEYS}
            changed = True
        elif m.get("mode", "add") != "add":
            shapes[u[0]]["mask"] = dict(m, mode="add")
            changed = True
    return changed


def same_mask(a, b):
    """Two masks alike apart from their groups (Join / merge keep the first one's)."""
    return {k: v for k, v in a.items() if k not in GROUP_KEYS} == {k: v for k, v in b.items() if k not in GROUP_KEYS}


def unit_of(units, i):
    return next((u for u in units if i in u), None)


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


def minus(a, b, c, d):
    """Spans (a, b) without the spans (c, d) (both merged): (starts, ends)."""
    if not len(c) or not len(a):
        return a, b
    pos = np.concatenate([a, b, c, d])
    ones_a, ones_c = np.ones(len(a), np.int64), np.ones(len(c), np.int64)
    ins = np.concatenate([ones_a, -ones_a, ones_c * 0, ones_c * 0])  # (inside the area: +1 at a start, -1 at an end)
    out = np.concatenate([ones_a * 0, ones_a * 0, ones_c, -ones_c])  # (inside the spans taken out)
    order = np.argsort(pos, kind="stable")
    pos, ins, out = pos[order], np.cumsum(ins[order]), np.cumsum(out[order])
    last = np.append(pos[1:] != pos[:-1], True)  # (the count after every change at one spot)
    pos, ins, out = pos[last], ins[last], out[last]
    keep = (ins[:-1] > 0) & (out[:-1] == 0)
    return merged(pos[:-1][keep], pos[1:][keep])


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
_room = [16]  # (how many areas are kept: mask_view makes it room for every mask, hunt: 17 remade them all)


def mask_area(sh, ppq, nkeys, notes_of):
    """A mask shape's area (starts, ends) and its reach (the same before its own notes are taken out: which shapes
    it covers), remembered. notes_of(shape) = its notes."""
    return mask_parts(sh, ppq, nkeys, notes_of)[:4]


def own_area(sh, ppq, nkeys, notes_of):
    """The spans of a mask's own notes the pattern never overlaps (sounding, not Fill / Spam), else empty ones."""
    return mask_parts(sh, ppq, nkeys, notes_of)[4:]


def mask_parts(sh, ppq, nkeys, notes_of):
    from notes.engine import shape_key
    m = sh["mask"]
    src = area_source(sh)
    key = (shape_key(src), ppq, nkeys)  # (big pasted / image notes kept as the strings themselves: quick)
    got = _areas.pop(key, None)
    if got is not None:
        _areas[key] = got  # (used last: kept longest)
    else:
        while len(_areas) > _room[0]:  # (a big mask's area is many MB, but every mask's is wanted at each change)
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
        reach = got
        own = area((), 0, 0, nkeys)
        if m["own"] and not is_solid(sh) and len(notes):
            # its own notes sound (a border): the pattern never overlaps them (user), whatever the thickness / gate
            own = area(notes, 0, 0, nkeys)
            got = joined(got, own) if m["invert"] else minus(*got, *own)
        got = _areas[key] = got + reach + own
    return got


def joined(x, y):
    """Spans in x or y."""
    return merged(np.concatenate([x[0], y[0]]), np.concatenate([x[1], y[1]]))


def common(x, y):
    """Spans in both x and y."""
    return minus(*x, *minus(*x, *y))


def unit_area(shapes, unit, ppq, nkeys, notes_of):
    """The area of masks worked out together (mask_units): (starts, ends, reach starts, reach ends, inverted). Each
    member's area (its Invert = everywhere but there) joins the result by its mode, the first adding (user); the reach
    = every member's (the shapes any of them touches are covered). Sounding own notes stay free of the pattern."""
    first = shapes[unit[0]]
    a, b, ra, rb = mask_area(first, ppq, nkeys, notes_of)
    cur, neg, reach = (a, b), first["mask"]["invert"], (ra, rb)
    if len(unit) == 1:
        return a, b, ra, rb, neg
    own = own_area(first, ppq, nkeys, notes_of)
    for i in unit[1:]:
        sh = shapes[i]
        m = sh["mask"]
        a, b, ra, rb = mask_area(sh, ppq, nkeys, notes_of)
        reach, own = joined(reach, (ra, rb)), joined(own, own_area(sh, ppq, nkeys, notes_of))
        # (kept as spans + "everywhere but these": not x = neg; A - B = A and not B)
        x, nx, mode = (a, b), m["invert"], m.get("mode", "add")
        if mode == "subtract":
            nx, mode = not nx, "intersect"
        if mode == "add":
            if not neg and not nx:
                cur = joined(cur, x)
            elif neg and nx:  # (not a or not b = not (a and b))
                cur = common(cur, x)
            elif neg:  # (not a or b = not (a - b))
                cur = minus(*cur, *x)
            else:  # (a or not b = not (b - a))
                cur, neg = minus(*x, *cur), True
        else:
            if not neg and not nx:
                cur = common(cur, x)
            elif neg and nx:  # (not a and not b = not (a or b))
                cur = joined(cur, x)
            elif neg:  # (not a and b = b - a)
                cur, neg = minus(*x, *cur), False
            else:
                cur = minus(*cur, *x)
    cur = joined(cur, own) if neg else minus(*cur, *own)
    return cur[0], cur[1], reach[0], reach[1], neg


def reach_box(sh, ppq, nkeys, notes_of):
    """The box round a mask's reach: (lowest key, highest key, first tick, last tick), None when empty."""
    ra, rb = mask_area(sh, ppq, nkeys, notes_of)[2:]
    if not len(ra):
        return None
    rows = (ra - OFF) // ROW
    return int(rows.min()), int(rows.max()), int((ra - OFF - rows * ROW).min()), int((rb - OFF - rows * ROW).max())


def free_colour(shapes, members, ppq, nkeys, notes_of):
    """A new group's line colour: the first one no group it overlaps uses (user; fixed after). All taken = the one
    used least there."""
    def boxes(idx):
        return [b for b in (reach_box(shapes[i], ppq, nkeys, notes_of) for i in idx) if b]

    mine, used = boxes(members), []
    for u in mask_units(shapes):
        rest = [i for i in u if i not in members]  # (the group as it stays after its members picked here leave)
        if len(rest) > 1 and any(x[0] <= y[1] and y[0] <= x[1] and x[2] < y[3] and y[2] < x[3]
                                 for x in mine for y in boxes(rest)):
            used.append(shapes[rest[0]]["mask"].get("colour", 0))
    return next((c for c in range(GROUP_COLOURS) if c not in used),
                min(range(GROUP_COLOURS), key=used.count))


def unmasked(rendered, shapes):
    """The rows of rendered a mask can cover (not masks' own: a mask is never masked)."""
    return rendered[~np.isin(rendered[:, 5], [i for i, sh in enumerate(shapes) if is_mask(sh)])]


def unit_shown(notes, shapes, unit, ppq, nkeys, notes_of):
    """What masks worked out together show of notes (shown_in). Apply mask makes exactly these."""
    return shown_in(notes, unit_area(shapes, unit, ppq, nkeys, notes_of))


def shown_in(notes, got):
    """What an area (unit_area's) shows of notes (rendered rows, no masks' own): (the shapes it covers, the rows that
    show, start and end trimmed)."""
    none = np.zeros(0, np.int64), notes[:0]
    a, b, ra, rb, invert = got
    if not len(ra) or not len(notes):
        return none
    # (only the notes near the reach are looked at closely: millions of others cost one quick pass)
    rows = (ra - OFF) // ROW
    lo_t, hi_t = int((ra - OFF - rows * ROW).min()), int((rb - OFF - rows * ROW).max())
    near = np.flatnonzero((notes[:, 2] >= rows.min()) & (notes[:, 2] <= rows.max()) & (notes[:, 1] > lo_t)
                          & (notes[:, 0] < hi_t))
    hit = kept_parts(notes[near], ra, rb, False)[0]  # (the shapes it covers: by its reach, own notes in it)
    if not hit.any():
        return none
    mine = np.unique(notes[near[hit], 5])
    if invert:  # (every note of the shapes it touches: the ones away from the hole show whole)
        near = np.flatnonzero(np.isin(notes[:, 5], mine))
    _, idx, ps, pe = kept_parts(notes[near], a, b, invert)
    got = notes[near[idx]].copy()
    got[:, 0], got[:, 1] = ps, pe
    return mine, got


def mask_view(rendered, shapes, ppq, nkeys, notes_of, pattern=None):
    """The notes as the piano roll draws them with the masks: (rows, covered shapes). Rows of the shapes a mask
    covers come twice: all of them as ghosts (velocity -1, painted faint), then the parts that show over them
    (start, end trimmed; every mask's own, they may overlap), taken from pattern (rendered without the masks' own
    notes; None = rendered). No masks = rendered itself."""
    units = mask_units(shapes)
    _room[0] = max(16, 2 * sum(map(len, units)))
    if not units or not len(rendered):
        return rendered, set()
    owner = rendered[:, 5]
    notes = unmasked(rendered if pattern is None else pattern, shapes)
    covered, parts = set(), []
    for unit in units:
        mine, got = unit_shown(notes, shapes, unit, ppq, nkeys, notes_of)
        if len(mine):
            covered.update(mine.tolist())
            parts.append(got)
    if not covered:
        return rendered, set()
    under = np.isin(owner, list(covered))
    ghosts = rendered[under].copy()
    ghosts[:, 3] = -1
    return np.concatenate([rendered[~under], ghosts] + parts), covered
