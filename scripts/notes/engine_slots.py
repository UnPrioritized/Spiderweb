"""
Auto channels: which slot (track + channel) each shape's notes go on, and fixing overlaps between notes.

assign_slots / in_rounds / PlacedSpans pick the slots; resolve_overlaps / split_overlaps / winners fix the notes
that overlap. Split out of engine.py, which imports every name here again (engine.assign_slots etc. still work).
"""

import itertools
import math

import numpy as np

from files.speed import loops

# Every MIDI channel except 10 (drums). Each slot gets its own track (MIDI editors want one channel per track):
# slot 0 = track 1 channel 1, slot 9 = track 10 channel 11, slot 15 = track 16 channel 1 again, ...
CHANNELS = [c for c in range(16) if c != 9]
LAST10 = np.array(CHANNELS + [9], np.int64)  # ("Use channel 10": the shapes' 16 channels in order, render)
ALL_KEYS = 256  # keys a note can be on (0-255): a whole channel taken, split by key (assign_slots extra)


def unique_inverse(a):
    """np.unique(a, return_inverse=True) of a 1-D array, the inverse flat. Whole numbers in a small range (tracks,
    keys): counted instead of sorted (millions of a Hz bass's notes: a sort took ~0.1 s)."""
    if a.dtype.kind in "iu" and len(a) and int(a.max()) - int(a.min()) <= max(1 << 20, len(a)):
        lo = int(a.min())
        there = np.bincount(a - lo) > 0
        ids = np.flatnonzero(there)
        at = np.cumsum(there) - 1
        return (ids + lo).astype(a.dtype), at[a - lo]
    ids, which = np.unique(a, return_inverse=True)
    return ids, which.ravel()


def assign_slots(note_lists, split="key", apart=(), first=None, taken=None, shape_of=None, step=15, loose=(),
                 extra=None):
    """
    Auto channels: shapes whose notes overlap get different slots, shapes that don't clash reuse the lowest
    free one. Earlier shapes get the lower slots. split="key": only notes on the same key at the same time
    clash; split="time": any notes sounding at the same time clash, whatever their key.
    apart: groups of note list numbers that always get different slots (a custom shape's outline and inside).
    first: per list None, or the tick to count it as starting at instead (a gate aware flip's colours: where they
    start unflipped).
    taken: {list number: its slot}, lists whose slot is set already (the others clashing with them step round it).
    shape_of: per list the shape it belongs to (Settings → General "Overlapping shapes keep their colours"): each shape
    gets its slots as if alone, and one clashing with an earlier shape moves WHOLE by step slots (15, 16 with channel
    10 used) until nothing clashes: the same colours on the next row of channels (user 2026-10-18). A taken list of
    the same shape stays and its colour counts as used. loose: shapes whose slots are moved again after (Colours
    rounds on channels of their own): never moved, nor stepped round.
    extra: {list number: (start, end) ticks} it also counts as sounding then ON EVERY KEY, either split (a Hz bass's
    CC lines change the whole channel: sent before its first note and on after its sound, hz_cc.cc_spans).
    """
    n = len(note_lists)
    extra = {i: span for i, span in (extra or {}).items() if span[1] > span[0]}

    def span_rows(span, owner):
        """An extra span as stretches clashing with anything then: one on each key (split="time": all key 0)."""
        keys = np.arange(ALL_KEYS, dtype=np.int64) if split == "key" else np.zeros(1, np.int64)
        return np.column_stack([keys, np.full(len(keys), span[0]), np.full(len(keys), span[1]),
                                np.full(len(keys), owner)]).astype(np.int64)

    def rows(i):
        """List i's stretches (stretches), its extra ones too."""
        got = stretches(note_lists[i], i, split)
        return np.concatenate([got, span_rows(extra[i], i)]) if i in extra else got

    busy = range(n)
    group = list(range(n))
    for g in apart:
        low = min(g, default=0)  # (once per group: a 4,096-colour shape made this 67 M steps)
        for a in g:
            group[a] = low
    if group != list(range(n)):
        # a group's own lists get slots apart anyway (a shape's colours take turns note by note: lots of stretches):
        # only lists whose group, joined, clashes with another group can clash with anything
        whole = {}
        for i, notes in enumerate(note_lists):
            whole.setdefault(group[i], []).append(notes)
        joined = [stretches(np.concatenate(ls), g, split) for g, ls in whole.items()]
        joined += [span_rows(span, group[i]) for i, span in extra.items()]
        clashing = set(np.concatenate(clash_arrays(np.concatenate(joined))).tolist())
        busy = [i for i in range(n) if group[i] in clashing]
    parts = [rows(i) for i in busy]
    # each list's clashes as one array (near(i)): thousands of shapes on top of each other = millions of pairs
    a, b = clash_arrays(np.concatenate(parts) if parts else np.zeros((0, 4), np.int64))
    starts, ends = np.concatenate([b, a]), np.concatenate([a, b])
    by = np.argsort(starts, kind="stable")
    ends = ends[by]
    at = np.searchsorted(starts[by], np.arange(n + 1)).tolist()
    # apart groups kept as groups, not as every pair in them (a shape with 4,096 colours = 16 M pairs; R:\333.spwb
    # took 5 s here): near(i) = its clashes + its groups' lists (itself too: it never has a slot yet when asked)
    member = {}
    for g in apart:
        g = np.asarray(sorted(set(g)), np.int64)
        if len(g) > 1:
            for a in g.tolist():
                member.setdefault(a, []).append(g)

    def near(i):
        own = member.get(i)
        return np.concatenate([ends[at[i]:at[i + 1]]] + own) if own else ends[at[i]:at[i + 1]]

    def alone(i):  # (clashes with nothing and in no group: most shapes in a big project)
        return at[i] == at[i + 1] and i not in member

    first = [f if f is not None else int(notes[:, 0].min()) if len(notes) else math.inf
             for notes, f in zip(note_lists, first or [None] * n)]
    order = sorted(range(n), key=lambda i: (first[i], i))
    slots, done = np.zeros(n, np.int64), np.zeros(n, bool)
    for i, s in (taken or {}).items():
        slots[i] = s
        done[i] = True
    if shape_of is not None:
        shapes, fixed = {}, {}  # (shape -> its lists, shapes in order of their first note; -> its lists with a slot)
        for i in order:
            if not done[i]:
                shapes.setdefault(shape_of[i], []).append(i)
        for i in np.flatnonzero(done).tolist():  # (an Add between step turned into plain notes: its group's other
            fixed.setdefault(shape_of[i], []).append(i)  # steps step round its colour)
        roomy = np.array([s not in loose for s in shape_of], bool)  # (shapes stepped round)
        own, pinned = np.full(n, -1, np.int64), np.zeros(n, bool)  # (-1: not this shape's)
        for key, mine in shapes.items():
            pins = fixed.get(key)  # (its slots as if no other shape were there)
            if pins:
                pinned[pins] = True
            for i in mine:
                if alone(i):
                    own[i] = 0
                    continue
                j = near(i)
                own[i] = least_free(np.concatenate([own[j], slots[j[pinned[j]]] % step]))
            rows = set()
            if key not in loose:  # (its slots move again anyway: in_rounds)
                for i in mine:
                    if alone(i):
                        continue
                    j = near(i)
                    d = slots[j[done[j] & ~pinned[j] & roomy[j]]] - own[i]
                    rows.update((d[(d >= 0) & (d % step == 0)] // step).tolist())
            # (a shape with a part pinned: from that part's row, so the rest stays with it when nothing clashes
            # any more; sweep 2026-11-06)
            row = min(int(slots[p]) // step for p in pins) if pins else 0
            while row in rows:
                row += 1
            if pins:
                pinned[pins] = False
            for i in mine:  # (one by one: quicker than array steps for the usual few lists a shape)
                slots[i] = own[i] + row * step
                done[i] = True
                own[i] = -1
        return slots.tolist()
    if member:
        # (a group whose lists clash with nothing and are in no other group: each takes the next slot in turn, in
        # order, as the loop below would give them one by one; 337.spwb: 8,192 lists in two groups)
        place = np.empty(n, np.int64)
        place[order] = np.arange(n)
        lone = np.asarray([at[i] == at[i + 1] and not done[i] and len(member[i]) == 1 if i in member else False
                           for i in range(n)], bool)
        for g in apart:
            a = next(iter(g), None)
            g = member[a][0] if a in member and len(member[a]) == 1 else None
            if g is not None and lone[g].all() and not done[g].any():
                slots[g[np.argsort(place[g])]] = np.arange(len(g))
                done[g] = True
    for i in order:
        if not done[i]:
            if not alone(i):  # (else slot 0)
                j = near(i)
                slots[i] = least_free(slots[j[done[j]]])
            done[i] = True
    return slots.tolist()


def least_free(used):
    """The smallest whole number 0, 1, 2... not in `used` (an array; numbers below 0 don't count)."""
    if len(used) < 64:
        used = set(used.tolist())
        k = 0
        while k in used:
            k += 1
        return k
    # (marks, no sort: the answer is at most len(used); 16,384 lists of 4,096 each spent 1.7 s sorting)
    seen = np.zeros(len(used) + 1, bool)
    seen[used[(used >= 0) & (used < len(seen))]] = True
    return int(np.argmin(seen))


def stretches(notes, owner, split="key"):
    """One list's notes as (key, start, end, owner) rows, its notes on a key joined into stretches (back-to-back spam
    = one stretch): anything with a length that overlaps a stretch overlaps one of its notes. Notes without a length
    stay on their own. split="time": every note counts as key 0."""
    fast = loops()
    if fast and split != "key" and len(notes):
        # (by key first: notes mostly come by key and start, so that's quick, and far fewer stretches come out; those
        # joined across the keys are the same stretches, touching ones joining either way)
        notes = stretches(notes, owner, "key")[:, [1, 2, 0]]
    if fast:  # (the compiled loop: the same rows; a spam shape's notes mostly come by key and start already)
        notes = np.ascontiguousarray(notes, np.int64)
        out, m = fast.note_stretches(notes, split == "key", owner, np.zeros(0, np.int64))
        if m < 0:
            key = np.ascontiguousarray(notes[:, 2]) if split == "key" else np.zeros(len(notes), np.int64)
            s = np.ascontiguousarray(notes[:, 0])
            out, m = fast.note_stretches(notes, split == "key", owner, fast.key_order_cores(key, s, s, s))
        return shorter(out, m)
    flat = notes[:, 1] <= notes[:, 0]
    key = notes[:, 2] if split == "key" else np.zeros(len(notes), np.int64)
    out = [np.column_stack([key[flat], notes[flat, 0], notes[flat, 1], np.full(int(flat.sum()), owner, np.int64)])]
    a, key = (notes[~flat], key[~flat]) if flat.any() else (notes, key)
    if len(a):
        s, e, k = a[:, 0], a[:, 1], key
        if not ((k[1:] > k[:-1]) | (k[1:] == k[:-1]) & (s[1:] >= s[:-1])).all():  # (else in that order already)
            order = np.lexsort((s, k))
            s, e, k = s[order], e[order], k[order]
        run = running_max(e, k)
        new = np.ones(len(s), bool)
        new[1:] = (k[1:] != k[:-1]) | (s[1:] > run[:-1])
        at = np.nonzero(new)[0]
        out.append(np.column_stack([k[at], s[at], np.maximum.reduceat(e, at), np.full(len(at), owner, np.int64)]))
    return np.concatenate(out).astype(np.int64).reshape(-1, 4)


CLASH_TABLE = 1 << 28  # (bytes the compiled sweep's table of owner pairs may take: about 46,000 clashing owners)


def clash_pairs(items, chunk=1 << 22):
    """(key, start, end, owner) stretches -> the set of (a, b) owner pairs (a < b) that clash (clash_arrays)."""
    a, b = clash_arrays(items, chunk)
    return set(zip(a.tolist(), b.tolist()))


def clash_arrays(items, chunk=1 << 22):
    """(key, start, end, owner) stretches -> arrays a, b of the owner pairs (a < b) that clash, sorted by a then b:
    on the same key, one starting before the other ends (in start, end, owner order; one starting where the other
    ends doesn't clash, nor does a stretch without a length). Worked out in chunks of about `chunk` pairs at a
    time."""
    if not len(items):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    fast = loops()
    if fast:  # (the compiled sweep: the same pairs; owners numbered 0, 1, 2... among those here, then back)
        names, inv = np.unique(items[:, 3], return_inverse=True)
        rows = np.array(items, np.int64)
        rows[:, 3] = inv.ravel()
        a, b, ok = fast.clash_pairs(rows, len(names), CLASH_TABLE)
        if ok:
            return names[a].astype(np.int64), names[b].astype(np.int64)
    order =np.lexsort((items[:, 3], items[:, 2], items[:, 1], items[:, 0]))
    k, s, e, o = (items[order, c] for c in range(4))
    vals, rank = np.unique(np.concatenate([s, e]), return_inverse=True)  # (ticks as ranks: no overflow)
    rank = rank.ravel()
    row = (k - k.min()) * len(vals)
    ps, pe = row + rank[:len(s)], row + rank[len(s):]
    cnt = np.maximum(np.searchsorted(ps, pe, "left") - np.arange(len(ps)) - 1, 0)  # later ones starting before its end
    total = np.cumsum(cnt)
    n = int(o.max()) + 1
    kept, pile, piled, start = np.zeros(0, np.int64), [], 0, 0
    while start < len(ps):
        stop = max(start + 1, int(np.searchsorted(total, (total[start - 1] if start else 0) + chunk, "right")))
        c = cnt[start:stop]
        if c.any():
            i = np.repeat(np.arange(start, stop), c)
            j = i + 1 + np.arange(len(i)) - np.repeat(np.cumsum(c) - c, c)
            a, b = o[i], o[j]
            m = a != b
            pile.append(np.unique(np.minimum(a[m], b[m]) * n + np.maximum(a[m], b[m])))
            piled += len(pile[-1])
            if piled > max(len(kept), chunk):  # (the same pair on every key: repeats dropped as it goes, not at the
                kept = np.unique(np.concatenate([kept] + pile))  # end: 2,000 stacked squares held 13 GB)
                pile, piled = [], 0
        start = stop
    code = np.unique(np.concatenate([kept] + pile))
    return code // n, code % n


def shorter(out, m):
    """A compiled loop's result rows out[:m]: its first rows as they are (no copy: a 5 M-note overlap fix copied all
    229 MB again when 76 notes went, 2026-11-09), unless most of the room would be wasted, then a copy of them."""
    if m == len(out):
        return out
    return out[:m] if m * 10 >= len(out) * 9 else out[:m].copy()


def running_max(values, groups):
    """The largest value so far, starting over in every group (groups: sorted numbers, each group together)."""
    if not len(values):
        return values
    lo = values.min()
    shift = int(values.max() - lo) + 1
    g = (groups - groups.min()) * shift
    return np.maximum.accumulate(values - lo + g) - g + lo


def resolve_overlaps(notes):
    """
    Notes on the same pitch and slot that overlap: the earlier note is cut where the later one starts,
    and the later one is stretched to where the earlier one would have ended, if that's further.
    Notes starting on the same tick become one note: the highest velocity wins, the longest length is kept.
    notes: array of (start, end, pitch, velocity, slot, owner) rows -> the fixed notes, grouped by slot and key
    (groups in the order they first show up), sorted inside each group.
    """
    if not len(notes):
        return notes
    fast = loops()
    if fast:  # (the compiled loops: the same notes)
        notes = np.ascontiguousarray(notes, np.int64)
        group, order = fast.overlap_order(notes)
        if not len(order):
            group = first_seen(notes[:, 4] * 256 + notes[:, 2])
            order = np.ascontiguousarray(overlap_order(notes, group), np.int64)
        out, m = fast.overlap_sweep(notes, order, group)
        return shorter(out, m)
    group = first_seen(notes[:, 4] * 256 + notes[:, 2])  # groups numbered in the order they first show up
    order = overlap_order(notes, group)
    a, group = np.take(notes, order, axis=0), group[order]  # (take: quicker than notes[order])
    s, e = a[:, 0], a[:, 1]
    run = running_max(e, group)  # everything before in the group sounds until here
    same = np.zeros(len(a), bool)
    same[1:] = group[1:] == group[:-1]
    before = np.concatenate([[0], run[:-1]])
    over = same & (s < before)  # starts while earlier notes still sound:
    end = np.where(over, np.maximum(e, before), e)  # stretched to where they would have ended
    cut = np.zeros(len(a), bool)
    cut[:-1] = over[1:]
    end[cut] = s[1:][over[1:]]  # and the one before is cut here
    last = np.ones(len(a), bool)
    last[:-1] = ~same[1:]
    a[:, 1] = end
    keep = last | (end > s)
    return a if keep.all() else a[keep]


def fixed_already(notes):
    """True when resolve_overlaps(notes) would give back exactly these rows, in this order: each slot + key's notes
    together in one block, in it every start later than the one before, every note but the block's last ending
    after its start and no later than the next one starts (nothing to cut, stretch, merge or reorder). A paste
    whose own overlaps were fixed already (heard_notes) comes to render so: its 5 M notes 0.07 s + 240 MB saved."""
    n = len(notes)
    if n < 2:
        return True
    g = notes[:, 4] * 256
    g += notes[:, 2]
    same = g[1:] == g[:-1]
    heads = g[np.flatnonzero(~same) + 1]
    first = int(g[0])
    del g
    if len(heads) > n // 4 + 64:  # (blocks this small: the sweep is quick anyway)
        return False
    if len(np.unique(np.append(heads, first))) != len(heads) + 1:  # (a slot + key coming back later)
        return False
    s, e = notes[:, 0], notes[:, 1]
    ok = s[1:] > s[:-1]
    ok &= e[:-1] <= s[1:]
    ok &= e[:-1] > s[:-1]
    ok |= ~same
    return bool(ok.all())


OVERLAP_WAYS = ("stretch", "split")  # (render's way: Single's cut + stretch / split around)


def split_overlaps(notes, stretch=False):
    """Single channel's "split around" way: notes on the same pitch and slot that overlap: the one starting later
    wins (as resolve_overlaps), the earlier one is cut where it starts and what's left of it after the winner ends
    comes back as a note of its own (resolve_overlaps stretches the winner over it instead: stretch=True gives
    the same notes). Notes starting on the same tick become one note first, as there. Every piece is kept, however
    short (user 2026-11-06)."""
    if not len(notes):
        return notes
    notes = np.asarray(notes, np.int64)
    group = first_seen(notes[:, 4] * 256 + notes[:, 2])
    order = overlap_order(notes, group)  # (by group, start; the same start: the loudest last, the longest first)
    a, g = notes[order], group[order]
    lead = np.ones(len(a), bool)  # (the first of each group + start: the one row the same start becomes)
    lead[1:] = (g[1:] != g[:-1]) | (a[1:, 0] != a[:-1, 0])
    at = np.flatnonzero(lead)
    if len(at) == len(a):  # (no two share a start: every row is its own)
        return winners(a, g, -a[:, 0], stretch, notes)
    last = np.append(at[1:], len(a)) - 1
    one = a[last].copy()  # (the loudest of the same start, with the longest length)
    one[:, 1] = np.maximum.reduceat(a[:, 1], at)
    g = g[last]
    return winners(one, g, -one[:, 0], stretch, notes)


def across_tracks(notes, rank, stretch=False):
    """Multi channel's Remove overlaps (user 2026-11-06): notes on the same pitch on DIFFERENT slots that overlap:
    the slot with the lower rank wins (rank: per note, its slot's place in the track order), the other note is cut
    where the winner starts and comes back after it ends as a note of its own (stretch: the winner is stretched over
    that instead, like Single channel). Each slot's own overlaps must be fixed already. Every piece is kept, however
    short. rank may also be a function: note places -> their ranks (only their order counts), asked only for the
    notes that overlap."""
    if not len(notes):
        return notes
    notes = np.asarray(notes, np.int64)
    return winners(notes, notes[:, 2], rank if callable(rank) else np.asarray(rank, np.int64), stretch)


def winners(notes, group, rank, stretch=False, like=None):
    """split_overlaps / across_tracks: in each group (a key, or a slot + key), at every moment only the sounding note
    with the lowest rank sounds (no two with one rank overlap): each note becomes the pieces of its time where it
    wins. stretch: a piece of a note coming back after a winner is given to that winner instead (stretched over it).
    -> the notes, grouped as resolve_overlaps' (by slot + key in the order they first show up in like (default
    notes), by start inside each)."""
    s, e = notes[:, 0], notes[:, 1]
    # (only notes overlapping another in their group need the work; the rest stay as they are)
    if ((group[1:] > group[:-1]) | (group[1:] == group[:-1]) & (s[1:] >= s[:-1])).all():
        o = None  # (by group and start already: split_overlaps' notes)
        gs, ss, es = group, s, e
    else:
        o = np.lexsort((s, group))
        gs, ss, es = group[o], s[o], e[o]
    same = np.zeros(len(gs), bool)
    same[1:] = gs[1:] == gs[:-1]
    run = running_max(es, gs)
    before = np.concatenate([[0], run[:-1]])
    over = same & (ss < before) & (es > ss)
    touch = over.copy()
    touch[:-1] |= over[1:]  # (the note it overlaps isn't always the one just before: whole runs below)
    run_id = np.cumsum(~(same & (ss < before)))  # (runs of notes sounding one after another without a gap)
    busy = np.zeros(run_id[-1] + 1, bool)
    busy[run_id[touch]] = True
    if o is None:
        work = busy[run_id] & (es > ss)
    else:
        work = np.zeros(len(notes), bool)
        work[o] = busy[run_id] & (es > ss)
    like = notes if like is None else like
    if not work.any():
        return regrouped(notes, like)
    rest, w = notes[~work], np.flatnonzero(work)
    wg, ws, we, wr = (np.ascontiguousarray(a, np.int64) for a in (group[w], s[w], e[w],
                                                                  rank(w) if callable(rank) else rank[w]))
    fast = loops()
    if fast:  # (the compiled loops: the same pieces, without the stretches in between: 4x the notes, 4x the time)
        order = np.lexsort((ws, wg))
        note, ps, pe, m = fast.winner_pieces(wg, ws, we, wr, order)
        note, ps, pe = note[:m], ps[:m], pe[:m]
    else:
        note, ps, pe = winner_stretches(wg, ws, we, wr)
    out = notes[w[note]].copy()
    out[:, 0], out[:, 1] = ps, pe
    if stretch and len(out) > 1:  # (a note coming back after the winner: the winner stretched over it instead;
        back = out[:, 0] > ws[note]  # such a piece always follows its winner's, touching it)
        keep = np.flatnonzero(~back)
        ends = np.maximum.reduceat(out[:, 1], keep)
        out = out[keep]
        out[:, 1] = ends
    return regrouped(np.concatenate([rest, out]), like)


def winner_stretches(wg, ws, we, wr):
    """winners' pieces the NumPy way (fastloops.winner_pieces' twin): every start and end in a group = the edges of
    the little stretches where one note wins; each stretch's winner, the same note's stretches side by side joined.
    -> (each piece's note (place in wg...), start, end), by group, then time."""
    g0 = int(wg.min())
    gk = (wg - g0).astype(np.int64)
    t0 = int(min(ws.min(), 0))
    span = int(max(we.max(), 0)) - t0 + 1
    if span * (int(gk.max()) + 1) >= 1 << 62:  # (too far apart for one code: group numbers made smaller)
        _, gk = np.unique(gk, return_inverse=True)
        gk = gk.reshape(-1).astype(np.int64)
    # (sorted, repeats dropped: np.unique's hashing took 6x the time for 4x the notes, a sort ~10x less, 4x)
    edges = np.sort(np.concatenate([gk * span + (ws - t0), gk * span + (we - t0)]))
    edges = edges[np.concatenate([[True], edges[1:] != edges[:-1]])]
    i0 = np.searchsorted(edges, gk * span + (ws - t0))
    i1 = np.searchsorted(edges, gk * span + (we - t0))
    best = lowest_over(i0, i1, wr, len(edges))
    piece = np.flatnonzero(best[:-1] >= 0)
    piece = piece[edges[piece] // span == edges[piece + 1] // span]  # (a stretch inside one group)
    note = best[piece]
    first = np.ones(len(piece), bool)  # (stretches of one note next to each other = one piece)
    first[1:] = (note[1:] != note[:-1]) | (piece[1:] != piece[:-1] + 1)
    head = np.flatnonzero(first)
    tail = np.append(head[1:], len(piece)) - 1
    return note[head], edges[piece[head]] % span + t0, edges[piece[tail] + 1] % span + t0


def lowest_over(i0, i1, rank, size):
    """For each of size places: the note (its place in i0...) with the lowest rank among those covering it (note k
    covers places i0[k] .. i1[k] - 1), -1 for none. A tree of ranges (segment tree): each note written into at most
    2 blocks a level, ~20 levels, then the blocks pushed down to the places once; the work goes with the notes, not
    with how many places each covers (spreading every note over its places grew 5-8x for 4x the notes where they
    pile up: hunt 2026-11-06, the user wants the NumPy way within the speed rule without the compiled loops)."""
    out = np.full(size, -1, np.int64)
    if not len(i0) or size <= 0:
        return out
    perm = np.argsort(rank, kind="stable")  # (lowest rank first: each note's place in that order = its value)
    dense = np.empty(len(perm), np.int64)
    dense[perm] = np.arange(len(perm))
    width = 1 << max(0, int(size - 1).bit_length())
    none = np.iinfo(np.int64).max
    tree = np.full(2 * width, none, np.int64)
    back = perm[::-1]  # (highest value first: a block written twice in one level keeps the last = the lowest)
    lo, hi, val = i0[back] + width, i1[back] + width, dense[back]
    while len(lo):
        keep = lo < hi
        if not keep.all():
            lo, hi, val = lo[keep], hi[keep], val[keep]
        left, right = (lo & 1) == 1, (hi & 1) == 1
        tree[lo[left]] = val[left]  # (a block of one level never gets a block of another: plain writes)
        tree[hi[right] - 1] = val[right]
        lo, hi = (lo + left) >> 1, (hi - right) >> 1
    level = 1
    while level < width:  # (each block's lowest pushed down into its two halves, top to bottom)
        parent = tree[level:2 * level]
        tree[2 * level:4 * level:2] = np.minimum(tree[2 * level:4 * level:2], parent)
        tree[2 * level + 1:4 * level:2] = np.minimum(tree[2 * level + 1:4 * level:2], parent)
        level *= 2
    leaf = tree[width:width + size]
    got = leaf != none
    out[got] = perm[leaf[got]]
    return out


def regrouped(notes, like=None):
    """notes ordered as resolve_overlaps gives them: by slot + key in the order they first show up in like (the
    notes before the fix; default notes), then by start."""
    if not len(notes):
        return notes
    code = notes[:, 4] * 256 + notes[:, 2]
    g = None
    if like is None:
        g = first_seen(code)
    else:
        had = like[:, 4] * 256 + like[:, 2]
        top = max(int(had.max()), int(code.max())) + 1
        if top <= 4 * len(had) + 65536:  # (a table, no sort: first_seen's way over like's ids)
            rank = seen_ranks(had, top)
            g = rank[code]
            if int(g.min()) < 0:  # (an id like doesn't have: the sorted way below, as before)
                g = None
        if g is None:
            o = np.argsort(had, kind="stable")  # (np.unique's way, by one stable sort: its hashing grew too fast)
            sh = had[o]
            new = np.concatenate([[True], sh[1:] != sh[:-1]])
            ids, at = sh[new], o[new]  # (each id's first place: stable, so the earliest)
            place = np.empty(len(ids), np.int64)
            place[np.argsort(at, kind="stable")] = np.arange(len(ids))
            g = place[np.searchsorted(ids, code)]
    s = notes[:, 0]
    # (notes mostly come in time inside each group already: then a stable sort by group alone gives the same order
    # as by group + start, quicker; 337.spwb's 2 M notes 140 -> 60 ms; in that order already: a copy)
    if ((g[1:] > g[:-1]) | (g[1:] == g[:-1]) & (s[1:] >= s[:-1])).all():
        return notes.copy()
    o = np.argsort(g, kind="stable")
    gs, ss = g[o], s[o]
    if not ((gs[1:] == gs[:-1]) & (ss[1:] < ss[:-1])).any():
        return np.take(notes, o, axis=0)
    lo, wide = int(s.min()), int(s.max()) - int(s.min()) + 1
    if wide < 1 << 40 and int(g.max()) < 1 << 22:  # (one key, one sort: the same order as the lexsort, quicker)
        return notes[np.argsort((g << 40) | (s - lo), kind="stable")]
    return notes[np.lexsort((s, g))]


def first_seen(ids):
    """Each id (whole numbers, not negative) as a number: 0 for the id that shows up first, 1 for the next new
    one, and so on."""
    top = int(ids.max()) + 1
    if top > 4 * len(ids) + 65536:  # (too spread out for a table)
        _, first, where = np.unique(ids, return_index=True, return_inverse=True)
        return np.argsort(np.argsort(first))[where]
    return seen_ranks(ids, top)[ids]


def seen_ranks(ids, top):
    """A table 0..top-1: each id's number in the order ids first show up (first_seen), -1 for ids not there. No
    sort: an id's first place is where the table of first places points back at itself."""
    first = np.empty(top, np.int64)
    first[ids[::-1]] = np.arange(len(ids) - 1, -1, -1)  # (a repeated index keeps the last one written: the first)
    lead = first[ids] == np.arange(len(ids))  # (ids at their first place, in the order they show up)
    rank = np.full(top, -1, np.int64)
    new = ids[lead]
    rank[new] = np.arange(len(new))
    return rank


def overlap_order(notes, group):
    """The order resolve_overlaps works in: by group, then start; the same start: by velocity (the loudest comes
    last, so it's the one kept), then the longest first. The same as
    np.lexsort((-end, velocity, start, group)), but most notes come in nearly sorted already (each shape's notes
    key by key, in time), which one sort by group and start makes good use of; only notes that share a start need
    the rest."""
    s = notes[:, 0]
    if not len(s) or s.min() < 0 or s.max() >= 1 << 40 or group.max() >= 1 << 22:
        return np.lexsort((-notes[:, 1], notes[:, 3], s, group))
    key = (group << 40) | s
    order = np.argsort(key, kind="stable")
    key = key[order]
    tie = np.zeros(len(key), bool)
    tie[1:] = key[1:] == key[:-1]
    tie[:-1] |= tie[1:]
    at = np.flatnonzero(tie)
    if len(at):
        sub = order[at]
        order[at] = sub[np.lexsort((-notes[sub, 1], notes[sub, 3], key[at]))]
    return order


CHANNEL_MODES = ("raw", "single", "auto")
SPLITS = ("key", "time")


def mapped_slots(slots, use10):
    """assign_slots' slots (array) -> the slots used (render's use10)."""
    if use10 == "all":  # (channel 10 last of each 16: the first 15 as without it, so ticking changes no colour)
        return slots // 16 * 16 + LAST10[slots % 16]
    if use10 == "order":  # ("Channels 1-16 in order": slot k = channel k)
        return slots
    if use10:  # (0..8, 10..24, 26..40...: never a slot that is channel 10)
        return slots + (slots + 6) // 15
    return slots


def unmapped_slot(s, use10):
    """The assign_slots slot mapped_slots turns into slot s, or None (none does: channel 10 skipped). Worked out
    directly: plain-notes shapes on thousands of slots ask for each one (hunt: a search made every edit slow)."""
    block, ch = divmod(s, 16)
    place = ch if ch < 9 else ch - 1  # (its place among CHANNELS)
    if use10 == "all":
        return block * 16 + (15 if ch == 9 else place)
    if use10 == "order" or not use10:
        return s
    return None if ch == 9 else block * 15 + place


def pushed_solid(note_lists, pinned, fixed, split, step, use10=False):
    """render's pinned tracks with each "solid" shape's track that clashes with an earlier "solid" shape's on the same
    slot moved up by step slots (15, 16 with channel 10 used) until it's free: the same channel (colour), another
    track (user, 2026-10-17: a copy of plain notes ate the original's notes). step 1 (Settings → General "Overlapping
    shapes keep their colours" off; user 2026-10-20: 352 path copies made 646 channels, most empty): the next free
    slot in the order the other shapes take them (with "Use channel 10": channel 10 last of each 16, unless "1-16 in
    order"; user 2026-10-20), never one they skip (channel 10, unmapped_slot). A moved track never lands on a slot
    the same shape's other tracks use (hunt 2026-10-20: two colours folded onto one channel). A track first tries
    its slots one by one against that slot's tracks only (a user's 32 stacked 256-key pastes: comparing each of
    3,640 tracks with every placed note took 34 s); after a few clashing slots, every slot it clashes on is found
    in one go (hunt: trying slot after slot, 200 copies x 15 colours took 286 s)."""
    placed = PlacedSpans()
    out = list(pinned)
    for i, (lst, pin) in enumerate(zip(note_lists, pinned)):
        if fixed[i] != "solid" or pin is None or not len(lst):
            continue
        ids, which = unique_inverse(pin)
        now = ids.tolist()  # (the shape's tracks' slots: moved ones' new slots, the rest as they are)
        order = np.argsort(which, kind="stable")  # (each track's notes in their own order, without a mask each)
        at = np.searchsorted(which[order], np.arange(len(now) + 1))
        for k, s in enumerate(ids.tolist()):
            mine = sorted_spans(lst[order[at[k]:at[k + 1]]], split)
            others, bad, tried = set(now) - {s}, None, 0
            while s in others or (placed.clashes(mine, s) if bad is None else s in bad):
                tried += s not in others  # (only real clashes count: stepping over the shape's own slots is free)
                if bad is None and tried >= 8:
                    bad = placed.clashing(mine)
                u = unmapped_slot(s, use10) if step == 1 else None
                if u is not None:
                    s = int(mapped_slots(np.array([u + 1]), use10)[0])
                    continue
                s += step
                while step == 1 and unmapped_slot(s, use10) is None:
                    s += 1
            placed.add(mine, s)
            now[k] = s
        out[i] = np.asarray(now, pin.dtype)[which]
    return out


SPAN_KEY, SPAN_OFF = 1 << 42, 1 << 40  # (sorted_spans: key x SPAN_KEY + tick + SPAN_OFF, ticks never reach 2^40)


def sorted_spans(notes, split):
    """A track's stretches (stretches: what overlaps them overlaps its notes) as sorted, apart (start, end) codes
    with the key in them (split "time": all key 0); ones without a length dropped (they clash with nothing)."""
    st = stretches(notes, 0, split)
    st = st[st[:, 2] > st[:, 1]]
    base = st[:, 0] * SPAN_KEY + SPAN_OFF
    order = np.argsort(base + st[:, 1], kind="stable")
    return (base + st[:, 1])[order], (base + st[:, 2])[order], st[:, 1].min(initial=0), st[:, 2].max(initial=0)


class PlacedSpans:
    """pushed_solid's tracks placed so far. Every slot's spans together (by_slot): on one slot they never overlap
    (placed only where they don't clash), so they stay sorted, apart spans. And all spans in a few runs sorted by
    start (runs: each about half the one before, two alike in size merged), with each run's longest span, so a track
    is only compared with the spans starting near its own (a user's 64 stacked 256-key pastes: comparing every
    placed note for each track took 56 s)."""

    def __init__(self):
        self.by_slot = {}  # slot -> (lo, hi, first tick, last tick)
        self.runs, self.coming = [], []  # (lo sorted, hi, slot, longest span); tracks not in runs yet

    def add(self, mine, slot):
        lo, hi, t0, t1 = mine
        if len(lo):
            self.coming.append((lo, hi, slot))
            had = self.by_slot.get(slot)
            if had is not None:
                lo, hi = np.concatenate([had[0], lo]), np.concatenate([had[1], hi])
                order = np.argsort(lo, kind="stable")
                lo, hi, t0, t1 = lo[order], hi[order], min(t0, had[2]), max(t1, had[3])
            self.by_slot[slot] = (lo, hi, t0, t1)

    def clashes(self, mine, slot):
        """Does mine clash with the tracks placed on slot?"""
        lo, hi = mine[0], mine[1]
        had = self.by_slot.get(slot)
        if had is None or not len(lo):
            return False
        at = had[1].searchsorted(lo, "right")  # (the slot's first span ending after each of mine starts)
        ok = at < len(had[0])
        return bool((had[0][at[ok]] < hi[ok]).any())

    def clashing(self, mine):
        """The slots of placed tracks that clash with mine (same key, overlapping: touching doesn't)."""
        lo, hi = mine[0], mine[1]
        out = set()
        if not len(lo):
            return out
        for plo, phi, slot in self.coming:  # (made only when asked: most tracks find a free slot without it)
            self.runs.append((plo, phi, np.full(len(plo), slot, np.int64), int((phi - plo).max())))
            while len(self.runs) > 1 and len(self.runs[-2][0]) <= 2 * len(self.runs[-1][0]):
                (alo, ahi, aslot, al), (blo, bhi, bslot, bl) = self.runs[-2:]
                rlo = np.concatenate([alo, blo])
                order = np.argsort(rlo, kind="stable")
                self.runs[-2:] = [(rlo[order], np.concatenate([ahi, bhi])[order],
                                   np.concatenate([aslot, bslot])[order], max(al, bl))]
        self.coming = []
        for plo, phi, slot, longest in self.runs:
            i0 = plo.searchsorted(lo - longest, "right")  # (a span starting longest or more before mine ends by then)
            n = np.maximum(plo.searchsorted(hi, "left") - i0, 0)
            total = int(n.sum())
            if total > len(plo):  # (long spans: each of the run's spans looked up in mine instead)
                at = hi.searchsorted(plo, "right")  # (mine's first span ending after each placed one starts)
                hit = at < len(lo)
                hit[hit] = lo[at[hit]] < phi[hit]
                out.update(np.unique(slot[hit]).tolist())
            elif total:
                ends = np.cumsum(n)
                at = np.repeat(i0 - ends + n, n) + np.arange(total)  # (every span starting near each of mine)
                hit = phi[at] > np.repeat(lo, n)
                out.update(np.unique(slot[at[hit]]).tolist())
        return out


def in_rounds(note_lists, slot_of, rounds, step, teams=None, stay=None, together=None):
    """render's slots with "New channels each Colours round" (user 2026-10-17: each round editable alone in other
    programs): every round of every shape with rounds, in shape order, gets step slots of its own past the ones the
    other shapes use, each note at the channel (colour) it has without the tick. -> (slots, slot count).
    The other shapes' blocks are left free of rounds, the rest are filled in order: a plain-notes shape frozen on
    round channels keeps them and the shapes around it keep theirs. A round needing more than step slots (a
    layered Hz bass's clash tracks) takes as many blocks. teams (render's): the steps of an Add between group take
    their rounds together, like one shape (together: the shapes with one key alike). stay: per shape True if its
    first round keeps the slots it has without
    the tick, only the later rounds moving (colours given by hand, custom.paint_rounds: a colour used once never
    moves, hunt)."""
    stay = stay or [False] * len(rounds)
    taken = set()
    for s, lst, r, st in zip(slot_of, note_lists, rounds, stay):
        if (r is None or st) and len(lst):
            s = np.broadcast_to(np.asarray(s, np.int64), len(lst))
            taken.update((np.unique(s if r is None else s[np.asarray(r) == 0]) // step).tolist())
    free = (b for b in itertools.count() if b not in taken)
    owners = {}  # (a shape, or an Add between group -> its shapes)
    for i, (lst, r) in enumerate(zip(note_lists, rounds)):
        if r is not None and len(lst):
            team = teams[i] if teams else None
            own = ("team", team[0]) if team is not None else (
                ("with", together[i]) if together and together[i] is not None else i)
            owners.setdefault(own, []).append(i)
    out = list(slot_of)
    for mine in owners.values():
        s = np.concatenate([np.broadcast_to(np.asarray(slot_of[i], np.int64), len(rounds[i])) for i in mine])
        rr = np.concatenate([rounds[i] for i in mine])
        move = rr != 0 if any(stay[i] for i in mine) else np.ones(len(s), bool)
        got = s.copy()
        if move.any():
            sm = s[move]
            q = sm // step - int(sm.min()) // step  # (the block within a round)
            wide = int(q.max()) + 1
            had, r = np.unique(rr[move], return_inverse=True)  # (only the rounds it has, no gaps: a piece sliced
            # off mid-way keeps the whole shape's round numbers, user 2026-10-18)
            blocks = np.fromiter(itertools.islice(free, len(had) * wide), np.int64)
            got[move] = sm % step + step * blocks[r.reshape(-1) * wide + q]
        at = np.cumsum([0] + [len(rounds[i]) for i in mine])
        for k, i in enumerate(mine):
            out[i] = got[at[k]:at[k + 1]]
    return out, max([int(np.max(s)) + 1 for s, lst in zip(out, note_lists) if len(lst)] or [0])
