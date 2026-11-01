"""
Random glissando runs (the Glissando tool, PLAN.md): a dragged line = a run of short notes on PICKED keys only, one
key per grid step, each note one step long.

sh["kind"] = "gliss", sh["pts"] = [start, end] (beats, keys) like a line; sh["gliss"] = its settings:
  "grids": "halves" (1/1, 1/2 ... as fine as the PPQ allows) or "list" (the typed "list" below) = the grids the step picks from;
  "list": the typed grids, e.g. "1/16, 1/32, 1/48";
  "keys": the picked keys (0 ... 255), sorted; missing / empty = the white keys (user 11-02: a hint says so);
  "step": "auto" (picks itself, below) or a fixed grid ("1/64": 2x faster than 1/32; the run's length follows);
  "thick": copies of the run one picked key over each, above (> 0) or below (< 0), at the same times;
  "len" + "unit": each note's length, "pct" = % of the step (default 100: up to the next note), "ticks".
Picking (pick_key, the piano keys while the tool is in use): a click = that note in every octave (user: chords rarely
skip a key in an octave), Ctrl+click = that one key.
The step picks itself: the grid in the list nearest one key per step for the line as drawn ("fits one key per
step", user 2026-11-01). The run starts on the line's earlier end; its end snaps to where the last key's note
starts ("Snap the end", user OK'd in the mock-up): fit_pts.
"""

import bisect
import math
import re

import numpy as np

WHITE = (0, 2, 4, 5, 7, 9, 11)
TOP_KEY = 255  # (a click picks the note up to here: the most keys a project has)
LIST_DEFAULT = "1/16, 1/32, 1/64"
DEFAULTS = {"grids": "halves", "list": LIST_DEFAULT, "step": "auto", "thick": 0, "len": 100, "unit": "pct"}
GRID_KINDS = ("halves", "list")
UNITS = ("pct", "ticks")
MOST_THICK = 127  # copies each way
MOST_LEN = {"pct": 100000, "ticks": 10 ** 7}


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
    if g.get("step") != "auto" and grid_name(g.get("step")):
        out["step"] = grid_name(g["step"])
    if type(g.get("thick")) is int and abs(g["thick"]) <= MOST_THICK:
        out["thick"] = g["thick"]
    if g.get("unit") in UNITS:
        out["unit"] = g["unit"]
    n = g.get("len")
    if type(n) in (int, float) and math.isfinite(n) and 0 < n <= MOST_LEN[out["unit"]]:
        out["len"] = n
    elif out["unit"] == "ticks":  # (a broken length in ticks: one step)
        out["unit"] = "pct"
    keys = g.get("keys")
    if isinstance(keys, list):
        got = sorted({k for k in keys if type(k) is int and 0 <= k <= TOP_KEY})
        if got:
            out["keys"] = got
    return out


def picked(g):
    """The picked keys as a set (empty = none picked: runs use the white keys)."""
    return set(g.get("keys") or ())


def pick_key(g, key, one=False):
    """The picked keys after clicking key on the piano keys: one (Ctrl+click) = that key on / off; else that note in
    every octave, on when the clicked key was off, off when it was on."""
    keys = picked(g)
    if one:
        keys ^= {key}
    else:
        row = set(range(key % 12, TOP_KEY + 1, 12))
        keys = keys - row if key in keys else keys | row
    return sorted(keys)


def pick_parts(keys, top):
    """(notes picked in every octave up to key top, as 0-11, the other picked keys up to top) for the panel's line."""
    full = [n for n in range(12) if all(k in keys for k in range(n, top + 1, 12))]
    return full, [k for k in sorted(keys) if k <= top and k % 12 not in full]


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
    keys = picked(g)
    return [k for k in range(max(0, lo), max(0, hi) + 1) if (k in keys if keys else k % 12 in WHITE)]


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
    fixed = g.get("step", "auto")
    if fixed != "auto" and grid_ticks(fixed, ppq) >= 1:  # (a fixed step: the run's length follows from it)
        return start, grid_ticks(fixed, ppq), fixed, keys
    names = grids(g, ppq)
    span = (b1 - b0) * ppq
    if len(keys) < 2 or span <= 0:  # (one key / an upright line: the finest grid)
        name = min(names, key=lambda n: grid_ticks(n, ppq))
    else:  # the grid nearest one key per step (as a ratio: 1/32 vs 1/64 is as far as 1/16 vs 1/32)
        want = span / (len(keys) - 1)
        name = min(names, key=lambda n: abs(math.log(grid_ticks(n, ppq) / want)))
    return start, grid_ticks(name, ppq), name, keys


def auto_name(sh, ppq):
    """The grid the step would pick itself (the Step dropdown's "Auto (1/32)")."""
    return plan(dict(sh, gliss=dict(sh.get("gliss") or DEFAULTS, step="auto")), ppq)[2]


def gliss_notes(sh, ppq):
    """The run's notes: (start, end, key) rows, one per picked key (+ the thickness' copies), each "len" long."""
    g = sh.get("gliss") or DEFAULTS
    start, step, _, keys = plan(sh, ppq)
    if not keys:
        return np.zeros((0, 3), np.int64)
    i = np.arange(len(keys))
    starts = start + np.floor(i * step + 0.5).astype(np.int64)  # (from the start each time: no drift with 1/48 etc.)
    n = g.get("len", 100)
    if g.get("unit") == "ticks":
        ends = starts + max(1, math.floor(n + 0.5))
    else:  # (% of the step, also from the start: 100 % = exactly up to the next note)
        ends = start + np.floor((i + n / 100) * step + 0.5).astype(np.int64)
    ends = np.maximum(ends, starts + 1)
    rows = [np.column_stack([starts, ends, np.asarray(keys, np.int64)])]
    thick = g.get("thick", 0)
    if thick:  # copies, one picked key over each (counted among every picked key, not only the run's)
        every = run_keys(g, 0, TOP_KEY)
        place = {k: j for j, k in enumerate(every)}
        at = np.asarray([place[k] for k in keys], np.int64)
        every = np.asarray(every, np.int64)
        for c in range(1, abs(thick) + 1):
            j = at + (c if thick > 0 else -c)
            ok = (j >= 0) & (j < len(every))
            rows.append(np.column_stack([starts[ok], ends[ok], every[j[ok]]]))
    return np.concatenate(rows)


def shifted(g, key, c):
    """key moved c picked keys up (down when c < 0), or None past the picked keys' ends / key not picked."""
    every = run_keys(g, 0, TOP_KEY)
    j = bisect.bisect_left(every, key)
    if j >= len(every) or every[j] != key or not 0 <= j + c < len(every):
        return None
    return every[j + c]


def middle_note(sh, ppq):
    """(start tick, key) of the run's middle note (where its thickness handle sits), or None without notes."""
    start, step, _, keys = plan(sh, ppq)
    if not keys:
        return None
    i = len(keys) // 2
    return start + math.floor(i * step + 0.5), keys[i]


def thick_at(sh, key, ppq):
    """The thickness for the handle dragged to key: picked keys from the run's middle note to the one nearest key."""
    g = sh.get("gliss") or DEFAULTS
    mid = middle_note(sh, ppq)
    if mid is None:
        return 0
    every = run_keys(g, 0, TOP_KEY)
    near = min(range(len(every)), key=lambda j: abs(every[j] - key))
    return max(-MOST_THICK, min(MOST_THICK, near - every.index(mid[1])))


def len_for(sh, ticks, ppq):
    """The run's "len" value (in its own unit) for notes ticks long."""
    g = sh.get("gliss") or DEFAULTS
    ticks = max(1, round(ticks))
    if g.get("unit") == "ticks":
        return min(ticks, MOST_LEN["ticks"])
    return min(round(ticks / plan(sh, ppq)[1] * 100, 1), MOST_LEN["pct"])


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
