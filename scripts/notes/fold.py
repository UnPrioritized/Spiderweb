"""Fold tracks (BMC tester 2026-11-07; window/fold_window.py): many tracks folded down to fewer. The notes' tracks
(their slots as laid in Multi channel) are counted in order and put together:
- "row" (In a row): tracks 1..g -> the first new track, g+1..2g -> the second... (neighbour stripes become one
  wide band of one colour);
- "loop" (Looping): track k -> new track k mod n (1, n+1, 2n+1... -> the first).
Without "keep colours" new track j is slot j, so it takes the j-th colour. With it every new track holds notes of ONE
colour (one channel a track: Domino can open it): each colour's tracks fold among themselves, the new tracks shared
out between the colours by how many tracks each had (each at least one)."""

import numpy as np

from notes.engine import channels_of as channel_of, resolve_overlaps


def block(use10):
    return 16 if use10 else 15


def free_rounds(rows, others, b, most=4096):
    """How far (a whole number of rounds of b channels, so every colour stays) the folded rows' slots must move so no
    other shape's note (others: rendered rows) sounds on one of them while it plays (each slot's own first start ..
    last end): 0 = they're free where they are."""
    if not len(rows) or not len(others):
        return 0
    slots, at = np.unique(rows[:, 4], return_inverse=True)
    at = at.ravel()
    t0 = np.full(len(slots), np.iinfo(np.int64).max)
    t1 = np.full(len(slots), np.iinfo(np.int64).min)
    np.minimum.at(t0, at, rows[:, 0])
    np.maximum.at(t1, at, rows[:, 1])
    near = others[(others[:, 0] < t1.max()) & (others[:, 1] > t0.min())]
    if not len(near):
        return 0
    for k in range(most):
        moved = slots + k * b
        j = np.searchsorted(moved, near[:, 4])
        j = np.minimum(j, len(moved) - 1)
        hit = (moved[j] == near[:, 4]) & (near[:, 0] < t1[j]) & (near[:, 1] > t0[j])
        if not hit.any():
            return k * b
    return 0


def chunks(count, n, how, even):
    """count tracks in order -> each one's new place 0..n-1 (fewer when the groups come out short): how "row" / "loop";
    even: In a row's groups as even as can be (else each ceil(count / n), the last one short)."""
    k = np.arange(count, dtype=np.int64)
    n = max(1, min(n, count))
    if how == "loop":
        return k % n
    if even:
        return k * n // count
    return k // -(-count // n)


def shares(sizes, n):
    """n new tracks shared out between colours with sizes tracks each: by size (largest remainders), each at least 1
    and at most its own count."""
    sizes = np.asarray(sizes, np.int64)
    n = int(min(max(n, len(sizes)), sizes.sum()))
    got = np.ones(len(sizes), np.int64)
    left = n - len(sizes)
    while left > 0:
        room = sizes - got
        want = np.where(room > 0, sizes * n / sizes.sum() - got, -np.inf)
        j = int(np.argmax(want))
        if room[j] <= 0:
            break
        got[j] += 1
        left -= 1
    return got


SEQS = {"plain": np.arange(16, dtype=np.int64),  # (slot k = the k-th track: channel 10 skipped, or 1-16 in order)
        "last10": np.array([c for c in range(16) if c != 9] + [9], np.int64),  # ("Use channel 10": ch 10 the 16th)
        "skip10": np.array([c for c in range(16) if c != 9], np.int64)}  # (a picture's 16: shapes never take ch 10)


def nth_slot(j, seq):
    """The j-th track the shapes take -> its slot (seq: SEQS' name, render's slot rule)."""
    s = SEQS[seq]
    j = np.asarray(j, np.int64)
    return j // len(s) * 16 + s[j % len(s)] if seq != "plain" else j


def in_order(slots, seq):
    """Slots sorted as the shapes take them (channel 10's slot last in its 16 with "last10")."""
    slots = np.asarray(slots, np.int64)
    if seq == "plain":
        return np.sort(slots)
    rank = np.full(16, 15, np.int64)
    rank[SEQS[seq]] = np.arange(len(SEQS[seq]))
    return slots[np.argsort(slots // 16 * 16 + rank[slots % 16], kind="stable")]


def fold_map(slots, n, how="row", even=False, keep=False, use10=False, seq="plain"):
    """The notes' slots (each once, any order) -> {old slot: new slot}. seq: the order the shapes take slots in
    (SEQS; with use10)."""
    if not use10:
        seq = "plain"
    olds = in_order(np.unique(np.asarray(slots, np.int64)), seq)
    if not len(olds):
        return {}
    if not keep:
        new = nth_slot(chunks(len(olds), n, how, even), seq)
        return dict(zip(olds.tolist(), new.tolist()))
    ch = channel_of(olds, use10)
    colours = np.unique(ch)
    sizes = np.array([(ch == c).sum() for c in colours], np.int64)
    each = shares(sizes, n)
    b = block(use10)
    out = {}
    for c, m in zip(colours.tolist(), each.tolist()):
        mine = olds[ch == c]
        place = chunks(len(mine), m, how, even)
        first = int(mine[0] % b)  # (the colour's place in a block: its first track's)
        out.update(zip(mine.tolist(), (first + place * b).tolist()))
    return out


def colour_count(slots, use10):
    return len(np.unique(channel_of(np.unique(np.asarray(slots, np.int64)), use10)))


def folded(rows, fold, cut=True):
    """rows (start, end, key, velocity, slot, owner) -> (the rows on their new slots, how many notes the overlap fix
    changed or merged away). cut: same-key overlaps in a new track fixed like Single channel (later start wins, same
    start = one note)."""
    rows = np.array(rows, np.int64, copy=True)
    if not len(rows):
        return rows, 0
    olds, back = np.unique(rows[:, 4], return_inverse=True)
    rows[:, 4] = np.array([fold[int(s)] for s in olds.tolist()], np.int64)[back.ravel()]
    if not cut:
        return rows, 0
    keep = rows[:, 5].copy()
    rows[:, 5] = np.arange(len(rows))  # (each note's own number: which ones came out changed)
    # (notes kept past the keys, "Keep notes made there": keys under 0 / past 255 — resolve_overlaps counts keys
    # 0-255 only, so each block of 256 keys is fixed on its own; overlaps only ever meet on one key)
    block = rows[:, 2] // 256
    if block.min() == block.max() == 0:
        out = resolve_overlaps(rows)
    else:
        parts = []
        for b in np.unique(block).tolist():
            part = rows[block == b]
            part[:, 2] -= b * 256
            got = resolve_overlaps(part)
            got[:, 2] += b * 256
            parts.append(got)
        out = np.concatenate(parts)
    same = (out[:, 0] == rows[out[:, 5], 0]) & (out[:, 1] == rows[out[:, 5], 1])
    changed = len(rows) - int(same.sum())
    out = out[np.lexsort((out[:, 2], out[:, 0]))]
    out[:, 5] = keep[out[:, 5]]
    return out, changed
