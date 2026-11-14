"""Drawing a loaded MIDI through Change visual time signature (PLAN.md 2026-11-14): where the file's ticks land on
screen. (The shared helpers, chain_of / map_key / shown / first_at: notes/loaded_shown.py.)

The piano roll's geometry (ax, bx: pixel of a SHOWN tick t = floor(t * ax + bx + 0.5)) stays what it was; what changes
is that a note's file tick is first moved by loaded_map.map_ticks (the same moves Export MIDI writes into the file).
The drawing loops read the file's bytes, so they get the map as a table of PIXEL BOUNDS in file ticks:
bounds[c] = the first file tick whose shown tick is on pixel c or later (c = 0 .. W + 1); a file tick's pixel is then
the last c with bounds[c] <= tick (-1 before the first, W + 1 after the last: all the loops need to know there is
"before the view" / "past it"). That gives, tick for tick, the pixel the exported file's notes would get when loaded.
No links = EMPTY bounds = the loops take their old formula untouched."""

import numpy as np

from notes import loaded_map
from notes.loaded_shown import first_at

EMPTY = np.zeros(0, np.int64)
_kept = []  # [(chain, ax, bx, W, result)] the last few views' (kept: scrolling asks again and again)
_KEPT = 8


def _pixel(s, ax, bx):
    """The roll's own pixel of shown ticks (the loops' formula, the same float steps)."""
    return np.floor(np.asarray(s, np.float64) * ax + bx + 0.5).astype(np.int64)


def _first_shown(c, ax, bx):
    """For each pixel c: the smallest shown tick on pixel c or later."""
    s = np.ceil((np.asarray(c, np.float64) - 0.5 - bx) / ax).astype(np.int64)
    for _ in range(4):  # (the division can be a tick off: settled by the loops' own formula)
        back = _pixel(s - 1, ax, bx) >= c
        s = np.where(back, s - 1, s)
        if not back.any():
            break
    for _ in range(4):
        short = _pixel(s, ax, bx) < c
        s = np.where(short, s + 1, s)
        if not short.any():
            break
    return s


def span(chain, ax, bx, W):
    """(t0, t1) of view(), without the pixel bounds (the guesses of how much a picture reads ask often)."""
    t0 = int(np.floor((-0.5 - bx) / ax))
    t1 = int(np.ceil((W + 0.5 - bx) / ax))
    if not chain:
        return t0, t1
    got = first_at(chain, np.array([t0, t1 + 1], np.int64))
    return int(got[0]), int(got[1]) - 1


def view(chain, ax, bx, W):
    """(t0, t1, bounds) of a view of W pixel columns through the chain, in FILE ticks: t0 = the first file tick that
    can show in it (the loops read from the checkpoint before it), t1 = the last (the loops read up to it: exactly
    the notes the exported file's loop reads: its shown ticks up to ceil((W + 0.5 - bx) / ax)); bounds as above.
    Without links: the old numbers and EMPTY."""
    t0 = int(np.floor((-0.5 - bx) / ax))
    t1 = int(np.ceil((W + 0.5 - bx) / ax))
    if not chain:
        return t0, t1, EMPTY
    for c, a, b, w, got in list(_kept):
        if a == ax and b == bx and w == W and len(c) == len(chain) and all(
                x[0] is y[0] and x[1] is y[1] for x, y in zip(c, chain)):
            return got
    s = _first_shown(np.arange(W + 2, dtype=np.int64), ax, bx)
    bounds = first_at(chain, np.concatenate([s, [t0, t1 + 1]]))
    got = (int(bounds[-2]), int(bounds[-1]) - 1, np.maximum.accumulate(bounds[:-2]))
    _kept.append((chain, ax, bx, W, got))
    del _kept[:-_KEPT]
    return got


def float_edges(chain, ax, bx, W):
    """The W + 1 left edges of the pixel columns (the last = the right edge of the last column) as FILE ticks, for the
    pictures made from the loading pass's grid (their buckets are in file ticks and are read by a column's edges, not
    by its whole ticks): the bounds' whole tick less how far the column's edge lay before it. A move that only shifts
    the ticks gives exactly the edges the exported file's columns have."""
    bounds = view(chain, ax, bx, W)[2]
    c = np.arange(W + 1, dtype=np.int64)
    s = _first_shown(c, ax, bx)
    edge = (c - 0.5 - bx) * (1 / ax)  # (the old code's own expression)
    out = bounds[:W + 1].astype(np.float64) - (s - edge)
    before = bounds[:W + 1] == 0  # (before the file's first tick: no tick there; the edge goes on the same way, below 0)
    if before.any():
        out[before] = edge[before] - int(loaded_map.map_ticks(chain, 0))
    return out


def file_edges(chain, edges):
    """Shown tick positions (floats: pixel column edges, a column's first tick) -> the file tick where each begins
    (first_at of the next whole shown tick). No links: the numbers as they are."""
    if not chain:
        return edges
    e = np.asarray(edges, np.float64)
    s = np.ceil(e).astype(np.int64)
    first = first_at(chain, s)
    out = first.astype(np.float64) - (s - e)  # (less how far the edge lay before its whole tick)
    before = first == 0  # (before the file's first tick: the edge goes on the same way, below 0)
    if before.any():
        out[before] = e[before] - int(loaded_map.map_ticks(chain, 0))
    return out
