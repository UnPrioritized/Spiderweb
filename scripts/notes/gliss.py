"""
Random glissando runs (the Glissando tool, PLAN.md): a dragged line = a run of short notes on PICKED keys only, one
key per grid step, each note one step long.

sh["kind"] = "gliss", sh["pts"] = [start, end] (beats, keys) like a line; sh["gliss"] = its settings:
  "grids": "halves" (1/1, 1/2 ... as fine as the PPQ allows) or "list" (the typed "list" below) = the grids the step picks from;
  "list": the typed grids, e.g. "1/16, 1/32, 1/48";
  "pcs": the picked keys as notes of the octave (0 = C ... 11 = B), every octave; missing = the white keys.
The step picks itself: the grid in the list nearest one key per step for the line as drawn ("fits one key per
step", user 2026-11-01). The run starts on the line's earlier end; its end snaps to where the last key's note
starts ("Snap the end", user OK'd in the mock-up): fit_pts.
"""

import math
import re

import numpy as np

WHITE = (0, 2, 4, 5, 7, 9, 11)
LIST_DEFAULT = "1/16, 1/32, 1/64"
DEFAULTS = {"grids": "halves", "list": LIST_DEFAULT}
GRID_KINDS = ("halves", "list")


def grid_name(text):
    """One typed grid like "1/32" or " 3 / 16 " -> "3/16", else None (any size: grids under a tick are dropped
    later, by the PPQ)."""
    m = re.fullmatch(r"\s*(\d{1,6})\s*/\s*(\d{1,7})\s*", text) if isinstance(text, str) else None
    if not m or int(m.group(1)) < 1 or int(m.group(2)) < 1:
        return None
    return f"{int(m.group(1))}/{int(m.group(2))}"


def read_list(text):
    """A typed grid list ("1/16, 1/32 1/48") -> its grid names in order, repeats dropped; None if any part isn't a
    grid or there's none."""
    parts = [p for p in re.split(r"[,;\s]+", text or "") if p] if isinstance(text, str) else []
    names = [grid_name(p) for p in parts]
    if not names or None in names:
        return None
    return list(dict.fromkeys(names))


def clean_gliss(g):
    """A run's settings from a file -> valid settings (anything broken = its default)."""
    g = g if isinstance(g, dict) else {}
    out: dict = dict(DEFAULTS)
    if g.get("grids") in GRID_KINDS:
        out["grids"] = g["grids"]
    if isinstance(g.get("list"), str) and read_list(g["list"]):
        out["list"] = g["list"][:200]
    pcs = g.get("pcs")
    if isinstance(pcs, list):
        got = sorted({p for p in pcs if type(p) is int and 0 <= p < 12})
        if got:
            out["pcs"] = got
    return out


def halves(ppq):
    """1/1, 1/2, 1/4 ... down to the finest half that's still at least one tick at this PPQ (no other limit)."""
    out, b = [], 1
    while 4 * ppq / b >= 1:
        out.append(f"1/{b}")
        b *= 2
    return out or ["1/1"]


def grids(g, ppq):
    """The grids the step can be, as names ("1/32"): none under one tick (the PPQ is the only limit, user 11-02)."""
    names = read_list(g.get("list")) if g.get("grids") == "list" else None
    names = [n for n in names or halves(ppq) if grid_ticks(n, ppq) >= 1]
    return names or [f"1/{4 * ppq}"]  # (every typed grid under a tick: one tick)


def grid_ticks(name, ppq):
    a, b = name.split("/")
    return int(a) * 4 * ppq / int(b)


def run_keys(g, lo, hi):
    """The picked keys from key lo up to key hi (both included), low to high."""
    pcs = set(g.get("pcs") or WHITE)
    return [k for k in range(max(0, lo), max(0, hi) + 1) if k % 12 in pcs]


def plan(sh, ppq):
    """(start tick, step in ticks, its grid name, keys in the run's order) of a run; keys empty = no notes."""
    g = sh.get("gliss") or DEFAULTS
    (b0, p0), (b1, p1) = sh["pts"][0], sh["pts"][-1]
    if b1 < b0:  # (drawn right to left: the run still goes forward in time, from the earlier end)
        (b0, p0), (b1, p1) = (b1, p1), (b0, p0)
    k0, k1 = math.floor(p0 + 0.5), math.floor(p1 + 0.5)
    keys = run_keys(g, min(k0, k1), max(k0, k1))
    if k1 < k0:
        keys.reverse()
    start = math.floor(b0 * ppq + 0.5)
    names = grids(g, ppq)
    span = (b1 - b0) * ppq
    if len(keys) < 2 or span <= 0:  # (one key / an upright line: the finest grid)
        name = min(names, key=lambda n: grid_ticks(n, ppq))
    else:  # the grid nearest one key per step (as a ratio: 1/32 vs 1/64 is as far as 1/16 vs 1/32)
        want = span / (len(keys) - 1)
        name = min(names, key=lambda n: abs(math.log(grid_ticks(n, ppq) / want)))
    return start, grid_ticks(name, ppq), name, keys


def gliss_notes(sh, ppq):
    """The run's notes: (start, end, key) rows, one per picked key, each one step long."""
    start, step, _, keys = plan(sh, ppq)
    if not keys:
        return np.zeros((0, 3), np.int64)
    i = np.arange(len(keys) + 1)
    edges = start + np.floor(i * step + 0.5).astype(np.int64)  # (from the start each time: no drift with 1/48 etc.)
    return np.column_stack([edges[:-1], np.maximum(edges[1:], edges[:-1] + 1), np.asarray(keys, np.int64)])


def fit_pts(sh, ppq):
    """The run's line snapped to its notes ("Snap the end"): from the first key's note start to where the last
    key's note starts, on their rows. None when there's nothing to snap to (under two keys)."""
    start, step, _, keys = plan(sh, ppq)
    if len(keys) < 2:
        return None
    a = [start / ppq, float(keys[0])]
    b = [(start + math.floor((len(keys) - 1) * step + 0.5)) / ppq, float(keys[-1])]
    (b0, _), (b1, _) = sh["pts"][0], sh["pts"][-1]
    return [b, a] if b1 < b0 else [a, b]  # (the ends stay the ends they were)
