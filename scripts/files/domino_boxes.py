"""Domino's own select box, worked out the way Domino does it (PLAN.md stage B, measured 2026-11-12 on 1.43 32-bit,
1.45 32-bit and 1.45 dev009 64-bit, all alike: dev/scratch/sb_probe*.py). Draw on Domino watches the boxes the user
drags (files/mouse_watch.py) so a Take can later reach the same places on the tracks onion skin shows.

DOMINO'S BOX (the Select tool, a drag on an empty spot):
- Time edges snap to the QUANTIZE unit (the toolbar's quantize box), whatever the zoom: the place PRESSED snaps to
  the nearest grid line (exactly half way = the later one); the place LET GO takes in its whole grid cell (dragged
  right: the box ends at that cell's end; dragged left: starts at that cell's start). Grid lines run from each bar's
  start (dotted units restart at every bar: a bar's last cell may be shorter).
- A note is inside when its START is in [box start, box end) (where it ends doesn't count) and its key's row is
  one the box touches (any part of the row).
- Ctrl+drag or Shift+drag ADDS a box (never takes notes out); a plain drag starts over; a plain click on an empty
  spot selects nothing; Esc keeps the selection; a drag starting on a note moves the selected notes instead.
"""

import numpy as np

# The quantize box's items, the same order in every Domino measured (English 1.43: Bar, Whole, 3/4, 1/2, 1/3, 3/8, 1/4,
# 1/6, 3/16, 1/8, 1/12, 3/32, 1/16, 1/24, 3/64, 1/32, 1/48, Custom...): the unit in beats (quarter notes). "bar" =
# a whole bar of the time signature there; None = "Custom..." (unknown here).
UNITS = ["bar", 4, 3, 2, 4 / 3, 1.5, 1, 2 / 3, 0.75, 0.5, 1 / 3, 0.375, 0.25, 1 / 6, 0.1875, 0.125, 1 / 12, None]


class Grid:
    """Domino's quantize grid: bars from the time signatures [(tick, top, bottom)] (bottom = 4 for a quarter; none =
    4/4 from tick 0) and a unit (UNITS item) at a PPQ."""

    def __init__(self, ppq, unit, sigs=()):
        self.ppq = ppq
        self.unit = unit
        sigs = sorted((int(t), int(a), int(b)) for t, a, b in sigs if a > 0 and b > 0)
        if not sigs or sigs[0][0] > 0:
            sigs.insert(0, (0, 4, 4))
        self.sigs = sigs

    def bar_at(self, t):
        """(bar start, bar length) of the bar holding tick t (t < 0: the first bar's length backwards)."""
        i = 0
        for j, (st, _, _) in enumerate(self.sigs):
            if st <= t:
                i = j
        st, a, b = self.sigs[i]
        length = self.ppq * 4 * a / b
        n = np.floor((t - st) / length) if length > 0 else 0
        return st + n * length, length

    def step(self, length):
        if self.unit == "bar":
            return length
        return self.unit * self.ppq if self.unit else None

    def nearest(self, t):
        """The grid line nearest tick t (half way = the later one), or t itself when the unit isn't known."""
        b, length = self.bar_at(t)
        q = self.step(length)
        if not q:
            return t
        k = np.floor((t - b) / q)
        lo, hi = b + k * q, min(b + (k + 1) * q, b + length)
        return hi if t - lo >= hi - t else lo

    def cell(self, t):
        """(start, end) of the grid cell holding tick t."""
        b, length = self.bar_at(t)
        q = self.step(length)
        if not q:
            return t, t
        k = np.floor((t - b) / q)
        lo = b + k * q
        return lo, min(lo + q, b + length)


def box(grid, t_press, t_let, key_press, key_let, keys=128):
    """Domino's box from a drag: (start tick, end tick (not in it), lowest key, highest key)."""
    p = grid.nearest(t_press)
    lo, hi = grid.cell(t_let)
    t0, t1 = (p, hi) if t_let >= t_press else (lo, p)
    k0, k1 = sorted((int(key_press), int(key_let)))
    return float(t0), float(t1), max(0, k0), min(keys - 1, k1)


def inside(notes, boxes, ordered=False):
    """notes = rows (tick, gate, key, ...): True where a note is inside any of the boxes. ordered: the rows are in
    tick order (a Domino track's): only each box's stretch is looked at."""
    notes = np.asarray(notes)
    hit = np.zeros(len(notes), bool)
    if not len(notes):
        return hit
    t, k = notes[:, 0], notes[:, 2]
    for t0, t1, k0, k1 in boxes:
        if ordered:
            a, b = np.searchsorted(t, t0, "left"), np.searchsorted(t, t1, "left")
            kk = k[a:b]
            hit[a:b] |= (kk >= k0) & (kk <= k1)
        else:
            hit |= (t >= t0) & (t < t1) & (k >= k0) & (k <= k1)
    return hit


def under(notes, tick, key):
    """Is there a note on that key sounding at that tick (a press there grabs a note, not a box)?"""
    notes = np.asarray(notes)
    if not len(notes):
        return False
    return bool(((notes[:, 2] == key) & (notes[:, 0] <= tick) & (tick < notes[:, 0] + np.maximum(notes[:, 1], 1)))
                .any())


def groups(notes, gap):
    """Fallback when no drawn box is known: the selected notes (rows tick, gate, key, ...) cut into groups lying
    apart (starts more than gap ticks apart, or keys more than one row apart), one box around each group's starts
    and keys: [(start, last start + 1, lowest key, highest key)]."""
    notes = np.asarray(notes)
    if not len(notes):
        return []
    t, k = notes[:, 0].astype(np.int64), notes[:, 2].astype(np.int64)
    gid = np.zeros(len(t), np.int64)
    for col, apart in ((t, max(1, gap)), (k, 1), (t, max(1, gap))):  # (time, keys inside, time again)
        order = np.lexsort((col, gid))
        g, c = gid[order], col[order]
        new = np.ones(len(c), bool)
        new[1:] = (g[1:] != g[:-1]) | (np.diff(c) > apart)
        gid[order] = np.cumsum(new) - 1
    order = np.argsort(gid, kind="stable")
    g = gid[order]
    first = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    ts, ks = t[order], k[order]
    lo_t, hi_t = np.minimum.reduceat(ts, first), np.maximum.reduceat(ts, first)
    lo_k, hi_k = np.minimum.reduceat(ks, first), np.maximum.reduceat(ks, first)
    return sorted(zip(lo_t.tolist(), (hi_t + 1).tolist(), lo_k.tolist(), hi_k.tolist()))


class Boxes:
    """The boxes the user dragged in Domino's piano roll, kept while Domino's selection still is exactly what they
    select (DECIDED 2026-11-12 (user): dropped like Domino's selection). Fed by Companion with mouse presses / lets
    go already turned into ticks + keys; checked against the current track's notes as Domino has them."""

    def __init__(self):
        self.list = []        # [(start, end, lowest key, highest key)]: union = what Domino has selected
        self.track = None     # (Domino pid, current track, its track object) they were dragged on
        self.depth = None     # Domino's undo count then (any edit since = dropped)
        self.how = ""         # (tests / status: why the last change)

    def clear(self, why):
        if self.list or self.track is not None:
            self.how = why
        self.list, self.track, self.depth = [], None, None

    def offer(self, b, adds, notes, track, depth):
        """A drag just ended: b = its box, adds = Ctrl / Shift held; notes = the current track's notes now (rows
        tick, gate, key, velocity, selected). Kept only when Domino's selection is exactly the boxes' notes."""
        boxes = (self.list if adds and self.track == track and self.depth == depth else []) + [b]
        if notes is None or not self.matches(notes, boxes):
            return self.clear("selection not the boxes")
        self.list, self.track, self.depth, self.how = boxes, track, depth, "added" if len(boxes) > 1 else "drawn"

    def matches(self, notes, boxes=None):
        notes = np.asarray(notes)
        if not len(notes):
            return True
        ordered = bool((np.diff(notes[:, 0]) >= 0).all()) if len(notes) > 1 else True  # (Domino's: tick order)
        return bool(np.array_equal(inside(notes, self.list if boxes is None else boxes, ordered), notes[:, 4] != 0))

    def still(self, notes, track, depth):
        """Still Domino's selection (same track, no edit, same notes selected)? Else dropped."""
        if not self.list:
            return False
        if track != self.track or depth != self.depth:
            self.clear("track changed" if track != self.track else "edited")
            return False
        if notes is None or not self.matches(notes):
            self.clear("selection changed")
            return False
        return True
