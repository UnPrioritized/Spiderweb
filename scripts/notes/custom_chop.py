"""Custom shapes, the chop block (moved out of custom.py): stretches cut into back-to-back notes of the spam gate
(chop and its Hz bass / Range twins), the outline gate (even band, grown sideways) and Outline spam. custom.py
re-exports every public name from here, and imports this file last (after all its own helpers exist)."""

import json
import math

import numpy as np

from files.speed import key_rows
from notes.gaterange import RangeKeys, RangeRows, range_grid, stretched
from notes.hzbass import KeyGrid, even_squares, off_cents, squares, threshold
from notes.shrink import inner_lines, inner_rows
from notes.custom import (_inner, _inner_room, area_inner, area_inner_lines, area_spans, colour_of, custom_strokes,
                          cut_out, drawn_view, fill_plan, fill_test, fillable, has_areas, has_ripples, inside_spans,
                          merged_by_key, merged_rows, outline_notes, outline_of_lines, walls)


def spam_gate(sh, ppq):
    """The spam gate in ticks: a whole number; for Hz bass the exact one as a float (chop_even), or with placed
    tones the array of its repeats (chop_grid), or a KeyGrid when they have effects (chop_keys)."""
    if sh.get("hz"):
        if sh["hz"].get("tones"):
            return squares(sh, ppq)
        got = even_squares(sh, ppq) if sh["hz"].get("tempo") else None
        if got is not None:  # (made under a changing tempo: the waves follow it)
            return got
        gate = max(1.0, float(sh["gate"] * ppq))
        limit = threshold(sh["hz"])
        whole = sh["hz"].get("fixed") or (limit is not None and off_cents(gate) <= limit + 1e-9)
        return float(math.floor(gate + 0.5)) if whole else gate
    if sh.get("range"):  # the gate goes from one to another across the shape (gaterange.py)
        return range_grid(sh, ppq)
    return gate_ticks(sh["gate"], ppq)


def gate_ticks(beats, ppq):
    """A spam gate in beats -> the whole ticks its notes use (what the boxes show, user: never a fraction)."""
    return max(1, math.floor(beats * ppq + 0.5))


def chop_even(stretches, g, count=False):
    """chop for Hz bass: g = the exact gate in ticks (a float, at least 1). One grid for every stretch, counted
    from tick 0: square n runs from round(n × g) to round((n + 1) × g). A stretch gets the squares whose middle is
    inside it; one too short for any stays one note as it is."""
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    lo = np.ceil(s0 / g - 0.5).astype(np.int64)
    n = np.ceil(e0 / g - 0.5).astype(np.int64) - lo
    short = n <= 0
    n = np.where(short, 1, n)
    if count:
        return n
    k = np.repeat(lo, n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    out = np.column_stack([np.floor(k * g + 0.5).astype(np.int64), np.floor((k + 1) * g + 0.5).astype(np.int64),
                           np.repeat(q, n)])
    if short.any():
        whole = np.repeat(short, n)
        out[whole, 0], out[whole, 1] = s0[short], e0[short]
    return out


def chop_grid(stretches, squares, count=False, fit=False, keep=False):
    """chop for Hz bass with placed tones: squares = its repeats, (start, end) ticks in order (hzbass.squares).
    A stretch gets the squares whose middle is inside it, whole. keep (a gate Range): one too short for any square
    stays one note as it is (a steep line's pieces don't vanish). fit (a gate Range's "fit"): keep, and its first
    note starts and its last ends at the stretch's edges."""
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    mids = squares[:, 0] + squares[:, 1]  # (twice the middle, so it stays whole numbers)
    lo = np.searchsorted(mids, 2 * s0, "left")
    n = np.maximum(np.searchsorted(mids, 2 * e0, "left") - lo, 0)
    short = n == 0
    if fit or keep:
        n = np.where(short, 1, n)
    if count:
        return n
    k = np.repeat(lo, n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    out = np.column_stack([squares[np.minimum(k, len(squares) - 1), 0], squares[np.minimum(k, len(squares) - 1), 1],
                           np.repeat(q, n)])
    if fit and len(out):
        last = np.cumsum(n) - 1
        out[last - n + 1, 0] = s0
        out[last, 1] = e0
    elif keep and short.any():
        whole = np.repeat(short, n)
        out[whole, 0], out[whole, 1] = s0[short], e0[short]
    return out


def chop_keys(stretches, grid, count=False, fit=False, keep=False):
    """chop for Hz bass whose tones have effects: every key has its own repeats (hzbass.KeyGrid). A Height over
    100 % grows past the shape's keys (KeyGrid.reach): its lowest / highest row's stretches copied onto the keys
    below / above, each copy's notes counted as its row's (so the stretches' order and counts still hold)."""
    below, above = grid.reach() if hasattr(grid, "reach") else (0, 0)
    if not (below or above) or not len(stretches):
        return keys_chopped(stretches, grid, count, fit, keep)
    lo, hi = grid.lo, grid.lo + grid.n - 1
    rows, extra = np.arange(len(stretches)), [stretches]
    owner = [rows]
    for edge, keys in ((lo, range(lo - 1, max(-1, lo - below - 1), -1)),
                       (hi, range(hi + 1, min(KEY_TOP, hi + above) + 1))):
        at = rows[stretches[:, 2] == edge]
        for k in keys:
            copy = stretches[at].copy()
            copy[:, 2] = k
            extra.append(copy)
            owner.append(at)
    ext, owner = np.concatenate(extra), np.concatenate(owner)
    n = keys_chopped(ext, grid, True, fit, keep)
    if count:
        return np.bincount(owner, n, len(stretches)).astype(np.int64)
    notes = keys_chopped(ext, grid, False, fit, keep)
    o = np.argsort(owner, kind="stable")  # (each stretch's own notes, then its copies')
    first, m = (np.cumsum(n) - n)[o], n[o]
    return notes[np.repeat(first, m) + np.arange(int(m.sum())) - np.repeat(np.cumsum(m) - m, m)]


KEY_TOP = 127  # the highest key a Height can grow onto: the project's top key (set_key_top)


def set_key_top(keys):
    """The project's key count changed (App): a Height grows only onto keys it has (none made and counted past)."""
    global KEY_TOP
    KEY_TOP = keys - 1


def keys_chopped(stretches, grid, count=False, fit=False, keep=False):
    """chop_keys on the stretches as they are."""
    q = stretches[:, 2]
    n = np.zeros(len(stretches), np.int64)
    parts = []
    for key, rows in key_rows(q):
        sq = grid.squares(int(key))
        n[rows] = chop_grid(stretches[rows], sq, True, fit, keep)
        if not count:
            parts.append((rows, chop_grid(stretches[rows], sq, fit=fit, keep=keep)))
    if count:
        return n
    if not parts:
        return np.zeros((0, 3), np.int64)
    # (each key's notes put straight where they go, in the stretches' order: no sort of every note)
    out = np.empty((int(n.sum()), 3), np.result_type(*(p.dtype for _, p in parts)))
    first = np.cumsum(n) - n
    for rows, part in parts:
        m = n[rows]
        out[np.repeat(first[rows] - (np.cumsum(m) - m), m) + np.arange(len(part))] = part
    return out


def range_spans(stretches, join):
    """Where a Range along each key row runs for each stretch: (t0s, t1s) = its own ends, or join: its key's first
    stretch's start to last one's end (gaps included)."""
    s0, e0 = stretches[:, 0], stretches[:, 1]
    if not join or not len(stretches):
        return s0, e0
    keys, inv = np.unique(stretches[:, 2], return_inverse=True)
    inv = inv.reshape(-1)
    lo, hi = np.full(len(keys), s0.max()), np.full(len(keys), e0.min())
    np.minimum.at(lo, inv, s0)
    np.maximum.at(hi, inv, e0)
    return lo[inv], hi[inv]


def chop_rows(stretches, grid, count=False, fit=False):
    """chop for a gate Range along each key row (gaterange.RangeRows): the whole graph over each stretch's own
    length (join: over its key's stretches together), squares whose middle is inside it, as chop_grid. One too
    short for any square = one note of the first gate from its start (user; with fit: the stretch as it is).
    The squares only depend on the length the graph runs over, so all stretches of one length go at once."""
    if not len(stretches):
        return np.zeros(0, np.int64) if count else np.zeros((0, 3), np.int64)
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    t0, t1 = range_spans(stretches, grid.join)
    n = np.zeros(len(stretches), np.int64)
    parts = []
    for length, rows in _same(t1 - t0):
        sq = grid.squares(0, length)
        rel = np.column_stack([s0[rows] - t0[rows], e0[rows] - t0[rows], rows])  # (from t0; key = the row's number)
        got = chop_grid(rel, sq, True)
        none = got == 0
        n[rows] = np.maximum(got, 1)
        if not count:
            got = chop_grid(rel, sq, fit=fit, keep=True)
            if not fit:
                short = np.repeat(none, n[rows])
                got[short, 1] = got[short, 0] + grid.a
            parts.append(got)
    if count:
        return n
    out = np.concatenate(parts)
    out = out[np.argsort(out[:, 2], kind="stable")]  # (back in the stretches' order)
    owner = out[:, 2].copy()
    out[:, :2] += t0[owner][:, None]
    out[:, 2] = q[owner]
    return out


def _same(values):
    """(value, indices) for each different value, in one sort (not a scan of all of them per value)."""
    vals, inv = np.unique(values, return_inverse=True)
    inv = inv.reshape(-1)
    order = np.argsort(inv, kind="stable")
    return zip(vals.tolist(), np.split(order, np.cumsum(np.bincount(inv, minlength=len(vals)))[:-1]))


def stretch_of(st, notes):
    """Which of the (start, end, key) stretches st each (start, end, key) note was cut from: the one its start is
    inside, else the one its middle is in (a note reaching back over a gap: "Across gaps in a row" cuts its squares
    along the whole key, so one can start before its stretch, user's hunt). Both = the last stretch on its key
    starting at or before that tick; -1 = none on its key."""
    o = np.lexsort((st[:, 0], st[:, 2]))
    big = np.int64(1) << 40
    keyed = st[o, 2].astype(np.int64) * big + st[o, 0]
    key = notes[:, 2].astype(np.int64)

    def last_at(t):
        i = np.searchsorted(keyed, key * big + t, "right") - 1
        j = o[np.maximum(i, 0)]
        return np.where((i >= 0) & (st[j, 2] == key), j, -1)

    j = last_at(notes[:, 0].astype(np.int64))
    inside = (j >= 0) & (notes[:, 0] < st[j, 1])
    return np.where(inside, j, last_at((notes[:, 0].astype(np.int64) + notes[:, 1]) // 2))


def chop(sh, stretches, g, count=False):
    """stretches: NumPy array of (start, end, key) rows in ticks -> each filled with back-to-back notes of gate g,
    as an array of (start, end, key) rows in the same order. Where they start: ALIGNS; what happens to the bit that
    doesn't fit a whole gate: ENDS. count: just how many notes each stretch gets."""
    keep = bool(sh.get("range")) and not sh.get("hz")
    fit = keep and stretched(sh["range"])
    if isinstance(g, RangeRows):
        return chop_rows(stretches, g, count, fit)
    if isinstance(g, (KeyGrid, RangeKeys)):
        return chop_keys(stretches, g, count, fit, keep)
    if isinstance(g, np.ndarray):
        return chop_grid(stretches, g, count, fit, keep)
    if isinstance(g, float):
        return chop_even(stretches, g, count)
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    size = e0 - s0
    ends, align = sh.get("ends", "drop"), sh.get("align", "auto")

    def number(n):  # note number inside its stretch
        return np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)

    if ends in ("round", "stretch"):
        n = np.maximum(1, (2 * size + g) // (2 * g))  # whole gates, rounded (half up)
        if ends == "stretch":
            if count:
                return n
            k, a, m, length = number(n), np.repeat(s0, n), np.repeat(n, n), np.repeat(size, n)
            return np.column_stack([a + length * k // m, a + length * (k + 1) // m, np.repeat(q, n)])
        if align == "aligned":  # the gate grid's squares with at least half a gate of the stretch in them
            lo = -((g - 2 * s0) // (2 * g))
            n = (2 * e0 + g) // (2 * g) - lo
            none = n <= 0  # (none: the square the stretch's middle is in)
            first = np.where(none, (s0 + e0) // 2 // g, lo) * g
            n = np.where(none, 1, n)
        else:
            first = s0 + (size - n * g) // 2 if align == "centred" else s0
        if count:
            return n
        starts = np.repeat(first, n) + number(n) * g
        return np.column_stack([starts, starts + g, np.repeat(q, n)])
    if align == "aligned":
        s = -(-s0 // g) * g  # the first gate line at or after s0
    else:
        # (centred: a stretch shorter than one gate stays one note, user; it was cut in two halves with "keep")
        s = np.where(size < g, s0, s0 + size % g // 2) if align == "centred" else s0
    n = np.maximum(0, (e0 - s) // g)
    if ends == "keep":  # a shorter note before the first whole gate (aligned / centred) and after the last
        head_end = np.minimum(s, e0)
        head = head_end > s0
        tail = np.where(s < e0, s + n * g, e0) < e0
        c = n + head + tail
        if count:
            return c
        k = number(c)
        starts = np.repeat(s, c) + (k - np.repeat(head, c)) * g
        stops = starts + g
        first, last = np.repeat(head, c) & (k == 0), np.repeat(tail, c) & (k == np.repeat(c, c) - 1)
        starts = np.where(first, np.repeat(s0, c), starts)
        stops = np.where(first, np.repeat(head_end, c), np.where(last, np.repeat(e0, c), stops))
        return np.column_stack([starts, stops, np.repeat(q, c)])
    short = n == 0  # too short for even one gate: stays one note as it is ("min": at least a quarter gate)
    n = np.where(short, 1, n)
    if count:
        return n
    starts = np.repeat(s, n) + number(n) * g
    out = np.column_stack([starts, starts + g, np.repeat(q, n)])
    if short.any():
        whole = np.repeat(short, n)
        a, b = s0[short], e0[short]
        if ends == "min":
            least = max(1, -(-g // 4))
            grow = b - a < least
            a = np.where(grow, a + (b - a - least) // 2, a)
            b = np.where(grow, a + least, b)
        out[whole, 0], out[whole, 1] = a, b
    return out


def chop_count(sh, stretches, g):
    """How many notes chop makes of stretches (a list or array of (start, end, key))."""
    st = np.asarray(stretches, np.int64).reshape(-1, 3)
    return int(chop(sh, st, g, count=True).sum()) if len(st) else 0


def stroke_groups(sh):
    """{group: [stroke numbers]} of a custom shape whose strokes came from different shapes (convert.py: each
    shape's strokes make their notes on their own, like the shapes did) or have outline colours of their own
    (colour_of), or None if they're all one."""
    groups = {}
    for k, st in enumerate(sh["strokes"]):
        groups.setdefault((st.get("src", -1), colour_of(st)), []).append(k)
    return groups if len(groups) > 1 else None


def coloured_strokes(sh):
    """Some stroke has an outline colour of its own (its notes then go on a channel of their own)."""
    return not sh.get("text") and "notes" not in sh and any(colour_of(st) for st in sh["strokes"])


def outline_groups(sh, ppq, spam=False):
    """The outline's notes (spam: chopped like Outline spam) and which stroke group each belongs to (None if the
    strokes are all one group, see stroke_groups)."""
    groups = stroke_groups(sh)
    g = edge_gate(sh, ppq)
    spans = inside_rows(sh, ppq) if g else None
    if groups is None:
        notes = thicker(sh, outline_notes(sh, ppq), spans, g, ppq)
        return (chop_outline(sh, notes, ppq) if spam else notes), None
    parts, ids = [], []
    paths = custom_strokes(sh)  # (once for every group: a live shape of 1,000 lines made them 1,000 times, speed
    # hunt 2026-10-29)
    for n, (_, strokes) in enumerate(sorted(groups.items())):
        # (the even band goes with the first group: it's the whole shape's)
        notes = outline_notes(sh, ppq, strokes, paths)
        notes = thicker(sh, notes, spans, g, ppq) if n == 0 or sh.get("edge_mode") == "sideways" else notes
        if spam:
            notes = chop_outline(sh, notes, ppq)
        parts.append(notes)
        ids.append(np.full(len(notes), n, np.int64))
    return np.concatenate(parts), np.concatenate(ids)


def edge_gate(sh, ppq):
    """The shape's smallest outline gate in ticks (sh["edge"], in beats; 0 = off)."""
    return max(1, int(round(sh["edge"] * ppq))) if sh.get("edge") else 0


def inside_rows(sh, ppq):
    """The inside's stretches as (start, end, key) ticks (none when the shape can't be filled)."""
    if not fillable(sh["strokes"]):
        return np.zeros((0, 3), np.int64)
    if has_areas(sh):
        return merged_rows(area_spans(sh, ppq)[:, :3])
    return np.asarray(inside_spans(sh, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]


def span_of(notes, spans):
    """For each (start, end, key) note, the inside stretch on its key that holds its middle: (a, b, found)."""
    n = len(notes)
    if not n or spans is None or not len(spans):
        return np.zeros(n, np.int64), np.zeros(n, np.int64), np.zeros(n, bool)
    lo = int(min(notes[:, 0].min(), spans[:, 0].min()))
    big = int(max(notes[:, 1].max(), spans[:, 1].max())) - lo + 2
    sp = spans[np.lexsort((spans[:, 0], spans[:, 2]))]
    mid = (notes[:, 0] + notes[:, 1]) // 2
    i = np.searchsorted(sp[:, 2] * big + (sp[:, 0] - lo), notes[:, 2] * big + (mid - lo), "right") - 1
    found = i >= 0
    i = np.maximum(i, 0)
    found &= (sp[i, 2] == notes[:, 2]) & (mid <= sp[i, 1])
    return sp[i, 0], sp[i, 1], found


def grow_inward(notes, spans, g):
    """Outline notes shorter than g ticks made g long, growing into the inside (user: the shape's edge stays where
    it is): a note in the left half of its key's inside grows right, one in the right half grows left, never past
    the middle (so a gate too big for the shape is cut to what fits). Notes with no inside beside them (open
    lines) stay as they are."""
    if not g or not len(notes):
        return notes
    a, b, found = span_of(notes, spans)
    s, e = notes[:, 0].copy(), notes[:, 1].copy()
    m = (a + b) // 2
    left = found & (s + e <= a + b)
    right = found & ~left
    e = np.where(left, np.maximum(e, np.minimum(s + g, m)), e)
    s = np.where(right, np.minimum(s, np.maximum(e - g, m)), s)
    return np.column_stack([s, e, notes[:, 2]]).astype(np.int64)


def near_edge(notes, spans, g):
    """Spam "Outline" with a smallest outline gate: the spam notes that start or end within g ticks of their
    key's inside edge (so the outline is at least g thick, all of it when the shape is thinner)."""
    a, b, found = span_of(notes, spans)
    return found & ((notes[:, 0] - a < g) | (b - notes[:, 1] < g))


def edge_loops(sh):
    """The lines the inside is made of (fill_plan's loops; text: its letters), and fill_test (None: even-odd; then
    every line given is part of a loop, else it's walls(sh)). Ripples: only its frames (ripples.edge_outline)."""
    if has_ripples(sh):
        from notes.ripples import edge_outline
        return edge_outline(sh)
    tx = sh.get("text")
    walled = fill_test(sh)
    return (custom_strokes(sh) if tx else walls(sh) if walled else fill_plan(sh)["polys"]), walled


def inner_ticks(sh, ppq, g, spans):
    """The shape shrunk inward by the outline gate g (ticks) as (start, end, key) notes on the keys of its inside
    (shrink.inner_rows: a smaller copy of its own outline). Remembered. An inside filling nothing: none."""
    if not len(spans):
        return np.zeros((0, 3), np.int64)
    keys = sorted(set(spans[:, 2].tolist()))
    key = (json.dumps([sh["strokes"], sh["pts"], sh.get("text")]), bool(sh.get("union")), ppq, g, keys[0], keys[-1],
           drawn_view(sh))
    if has_areas(sh):  # (what the coloured areas fill, shrunk from its own edge)
        key += (json.dumps(sh["areas"]), bool(sh.get("borders")))
    if has_ripples(sh):  # (Ripples: its frames, + its sources with Inside the sources "empty")
        key += ("ripples", json.dumps({k: v for k, v in sh["ripples"].items() if k not in ("colours", "skip")}))
    got = _inner.get(key)
    if got is None:
        _inner_room()
        if has_areas(sh):
            got = _inner[key] = area_inner(sh, ppq, g, keys)
        else:
            loops, walled = edge_loops(sh)
            got = _inner[key] = inner_rows(loops, walled, keys, g / ppq, ppq, drawn_view(sh))
    return got


def with_band(notes, spans, inner):
    """Outline notes and the band between the inside's edge and the inner shape, as notes merged per key."""
    band = cut_out(spans, inner)
    ks, ss, es = merged_by_key(np.concatenate([np.asarray(notes, np.int64).reshape(-1, 3), band]))
    return np.column_stack([ss, es, ks]).astype(np.int64).reshape(-1, 3)


def edge_inner(sh, ppq):
    """How far in sh's outline gate reaches, for the piano roll's preview line: ("lines", pieces (b0, k0, b1, k1)
    of the shrunk shape's outline) for the even band, ("rows", (start, end, key) notes of the inside left) for Grow
    sideways, or None where the outline gate does nothing."""
    if not (sh.get("fill") in ("empty", "outline_spam") or sh.get("apart") and sh.get("fill") in ("fill", "spam")):
        return None
    g = edge_gate(sh, ppq)
    spans = inside_rows(sh, ppq) if g else ()
    if not len(spans):
        return None
    if sh.get("edge_mode") == "sideways":
        return "rows", cut_out(spans, grow_inward(outline_notes(sh, ppq), spans, g))
    loops, walled = edge_loops(sh)
    if not loops:
        return None
    if has_areas(sh):  # (worked out exactly on the notes' lines across each key and joined up: the grid over the
        # whole shape was too coarse for hairline gaps between areas, user saw it zigzag)
        keys = list(range(int(spans[:, 2].min()), int(spans[:, 2].max()) + 1))
        key = ("lines", json.dumps([sh["strokes"], sh["pts"], sh["areas"]]), bool(sh.get("union")),
               bool(sh.get("borders")), ppq, g, drawn_view(sh))
        got = _inner.get(key)
        if got is None:
            _inner_room()
            got = _inner[key] = outline_of_lines(*area_inner_lines(sh, ppq, g, keys), g / ppq)
        return "lines", got
    return "lines", inner_lines(loops, walled, g / ppq, drawn_view(sh))


def thicker(sh, notes, spans, g, ppq):
    """Outline notes with the smallest outline gate: the even band (default: down to the shape shrunk inward) or
    each note grown sideways (the first way, kept as a second option: sh["edge_mode"] = "sideways")."""
    if not g or not len(spans):
        return notes
    if sh.get("edge_mode") == "sideways":
        return grow_inward(notes, spans, g)
    return with_band(notes, spans, inner_ticks(sh, ppq, g, spans))


def chop_outline(sh, notes, ppq):
    """Outline notes chopped into back-to-back notes of the spam gate (spam start and ends like Spam; a note too
    short for even one gate never vanishes, so steep parts of the outline stay)."""
    return chop(sh, np.asarray(notes, np.int64).reshape(-1, 3), spam_gate(sh, ppq))


def outline_spam(sh, ppq):
    """The outline in notes of the spam gate (chop_outline)."""
    return outline_groups(sh, ppq, spam=True)[0]
