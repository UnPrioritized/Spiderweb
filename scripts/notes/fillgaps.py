"""Fill gaps (user, 2026-10-23: a user asked to "remove empty space"): per key row, all colours together, the gaps
between the notes are closed. Right = a note's end moves to the next note's start (the row's last note stays),
left = a note's start moves back to the end before it (the row's first note stays), middle = both meet halfway
(an odd gap: the earlier note gets the extra tick). A partial overlap = the earlier note cut where the later one
starts. Notes starting on one tick of one key: "early" = the earlier colour stays (lower slot = earlier track, then
the shape listed first), "late" = the later one, "both" = all kept (overlaps too: nothing is cut). Notes never change
key, velocity or colour."""

import numpy as np

WAYS = ("right", "left", "middle")
SAME = ("early", "late", "both")
_BIG = 1 << 40  # (key x _BIG + tick: one number per key row's tick; ticks stay far below it)


def needs_fill(rows, way, same):
    """Would fill_gaps change rows? The same answer, quicker (one sort; the Fill gaps window asks on every click)."""
    rows = np.asarray(rows, np.int64).reshape(-1, 6)
    if len(rows) < 2:
        return False
    o = np.argsort(rows[:, 2] * _BIG + rows[:, 0], kind="stable")
    k, s, e = rows[o, 2], rows[o, 0], rows[o, 1]
    on_key = k[1:] == k[:-1]
    at_once = on_key & (s[1:] == s[:-1])
    if same != "both" and at_once.any():  # (one of them goes)
        return True
    later = on_key & ~at_once
    run = np.maximum.accumulate(k * _BIG + e) - k * _BIG  # (the latest end on the key so far)
    if (later & (run[:-1] < s[1:])).any():  # (a gap)
        return True
    if same != "both":
        return bool((later & (run[:-1] > s[1:])).any())  # (an overlap cut, or a note held inside stretched)
    if way != "right":
        return False
    first = np.r_[True, ~on_key | (s[1:] != s[:-1])]  # (Keep both, right: a short note among longer ones reaching on)
    g = np.cumsum(first) - 1
    gk, gs = k[first], s[first]
    nxt = np.where(np.r_[gk[1:] == gk[:-1], False], np.r_[gs[1:], 0], -1)[g]
    return bool(((nxt >= 0) & (e < nxt)).any())


def fill_gaps(rows, way, same):
    """rows: (start, end, key, velocity, slot, owner) notes of every shape picked -> (the notes filled, the same
    columns, in the order given; which of rows they are). owner breaks a tie of slots (lower = earlier)."""
    rows = np.asarray(rows, np.int64).reshape(-1, 6)
    if not len(rows):
        return rows.copy(), np.zeros(0, np.int64)
    o = np.lexsort((rows[:, 5], rows[:, 4], rows[:, 0], rows[:, 2]))  # (by key, start, then colour order)
    k, s, e = rows[o, 2], rows[o, 0].copy(), rows[o, 1].copy()
    if same != "both":  # one note per key and start: the first (earlier colour) or the last of each run
        new = np.ones(len(o), bool)
        new[1:] = (k[1:] != k[:-1]) | (s[1:] != s[:-1])
        keep = new if same == "early" else np.r_[new[1:], True]
        o, k, s, e = o[keep], k[keep], s[keep], e[keep]
    # groups = the notes starting on one tick of one key (one note each unless "both")
    first = np.ones(len(o), bool)
    first[1:] = (k[1:] != k[:-1]) | (s[1:] != s[:-1])
    g = np.cumsum(first) - 1
    gk, gs = k[first], s[first]
    n = len(gs)
    same_next = np.r_[gk[1:] == gk[:-1], False]  # (group g + 1 is on the same key)
    nxt = np.where(same_next, np.r_[gs[1:], 0], -1)  # (the next start on the key, -1 = none)
    if same != "both":  # (a partial overlap: cut where the later note starts)
        # (a note starting while an earlier one still sounds is stretched to where that one would have ended, like
        # Single channel: else a long note holding a short one was cut and the rest left silent, overlap hunt
        # 2026-11-08, user took A)
        gm0 = np.full(n, -1, np.int64)
        np.maximum.at(gm0, g, e)
        run0 = np.maximum.accumulate(gk * _BIG + gm0) - gk * _BIG
        on = np.r_[False, gk[1:] == gk[:-1]]
        before = np.where(on, np.r_[0, run0[:-1]], -1)  # (the latest end before each group on its key)
        held = (before[g] > s) & (e < before[g])
        e[held] = before[g][held]
        cut = (nxt[g] >= 0) & (e > nxt[g])
        e[cut] = nxt[g][cut]
    gm = np.full(n, -1, np.int64)  # (each group's latest end)
    np.maximum.at(gm, g, e)
    run = np.maximum.accumulate(gk * _BIG + gm) - gk * _BIG  # (the latest end on the key up to each group)
    has_prev = np.r_[False, gk[1:] == gk[:-1]]
    prev = np.where(has_prev, np.r_[0, run[:-1]], -1)  # (the latest end before the group, -1 = first on its key)
    gap = has_prev & (prev < gs)  # (empty space right before the group)
    if way == "right":
        to = nxt[g]
        grow = (to >= 0) & (e < to)
        e[grow] = to[grow]
    elif way == "left":
        s = np.where(gap[g], prev[g], s)
    else:
        mid = prev + (gs - prev + 1) // 2
        s = np.where(gap[g], mid[g], s)
        after = np.r_[gap[1:], False]  # (the group after this one has a gap before it)
        to = np.r_[mid[1:], 0]
        grow = after[g] & (e < to[g])
        e[grow] = to[g][grow]
    out = rows[o].copy()
    out[:, 0], out[:, 1] = s, e
    back = np.argsort(o, kind="stable")  # (in the order given)
    return out[back], o[back]
