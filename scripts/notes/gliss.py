"""
Random glissando runs (the Glissando tool, PLAN.md): a dragged line = a run of short notes on PICKED keys only, one
key per grid step, each note one step long.

sh["kind"] = "gliss", sh["pts"] = [start, end] (beats, keys) like a line; sh["gliss"] = its settings:
  "grids": "halves" (1/1, 1/2 ... as fine as the PPQ allows) or "list" (the typed "list" below) = the grids the step picks from;
  "list": the typed grids, e.g. "1/16, 1/32, 1/48";
  "keys": the picked keys (0 ... 255), sorted; missing / empty = the white keys (user 11-02: a hint says so);
  "step": "auto" (picks itself, below) or a fixed grid ("1/64": 2x faster than 1/32; the run's length follows);
  "above" / "below": copies of the run one picked key over each, above / below it, at the same times (user 11-02:
    one dotted line above the run and one below set them apart);
  "len" + "unit": each note's length, "pct" = % of the step (default 100: up to the next note), "ticks";
  "timing": "grid" (one note per step, default) or "line" (UNSNAPPED, stage E: each note where the line crosses its
    key's row; "pct" = % of the gap up to the next note, user 11-02).
A BENT RUN (stage E, bent()): sh["pts"] = a pen-tool curve (anchor, handle, handle, anchor ..., bezier.py) instead of
[start, end]; its keys = the picked keys in the order the line crosses their rows (crossings), so an arch goes up
and back down.
sh["end_dot"] = the shared Last note choice (like Line): True = the last note STARTS on the line's end, else it ENDS
there. Picking (pick_key, the piano keys while the tool is in use): a click = that note in every octave (user:
chords rarely skip a key in an octave), Ctrl+click = that one key.
The step picks itself: the grid in the list nearest one key per step for the line as drawn ("fits one key per
step", user 2026-11-01). The run starts on the line's earlier end. THE DRAWN LINE STAYS AS DRAWN (user 11-02); only
editing Grids / My list / Step / copies / Note length / Last note snaps its end onto the notes (fit_pts).
"""

import bisect
import math
import re

import numpy as np

from notes.bezier import sample

WHITE = (0, 2, 4, 5, 7, 9, 11)
TOP_KEY = 255  # (a click picks the note up to here: the most keys a project has)
LIST_DEFAULT = "1/16, 1/32, 1/64"
DEFAULTS = {"grids": "halves", "list": LIST_DEFAULT, "step": "auto", "above": 0, "below": 0, "len": 100,
            "unit": "pct", "timing": "grid"}
GRID_KINDS = ("halves", "list")
UNITS = ("pct", "ticks")
TIMINGS = ("grid", "line")
SAMPLES = 240  # points per curve piece the crossings are found on (like engine.shape_path)
MOST_COPIES = 127  # copies each way
MOST_LEN = {"pct": 100000, "ticks": 10 ** 7}
LEAST_PCT = 0.0001  # (the smallest % Note length: a tiny one never rounds to 0, which a file would drop)


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
    for side in ("above", "below"):
        if type(g.get(side)) is int and 0 <= g[side] <= MOST_COPIES:
            out[side] = g[side]
    if g.get("unit") in UNITS:
        out["unit"] = g["unit"]
    if g.get("timing") in TIMINGS:
        out["timing"] = g["timing"]
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


def for_new(g):
    """The settings a NEW run starts with: the last used ones, but Note length always 100 % (user 11-02: a length
    changed on one run never carries over to the next)."""
    return dict(g, len=DEFAULTS["len"], unit=DEFAULTS["unit"])


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


def bent(sh):
    """True for a run whose line is a curve (pen-tool points), False for a straight [start, end] one."""
    return sh.get("kind") == "gliss" and len(sh["pts"]) >= 4


def pen_shape(sh):
    """A shape edited with the pen tool's anchors and handles: a Curve or a bent run."""
    return sh["kind"] == "curve" or bent(sh)


def run_path(sh):
    """The drawn line as (beat, pitch) rows, the EARLIER end first (drawn right to left: the run still goes forward
    in time)."""
    pts = [tuple(p) for p in sh["pts"]]
    a = np.asarray(sample(pts, SAMPLES) if len(pts) >= 4 else [pts[0], pts[-1]], float)
    return a[::-1] if a[-1, 0] < a[0, 0] else a


_crossed = {}


def crossings(sh):
    """[(key, beat)]: the picked keys in the order the line crosses their rows' middles, with where it does. The
    two ends count their own (nearest) row; a key never twice in a row (a line wobbling round one row). A straight
    line = every picked key between its ends' rows, as before."""
    g = sh.get("gliss") or DEFAULTS
    k = (json_key(sh["pts"]), tuple(g.get("keys") or ()))
    got = _crossed.get(k)
    if got is not None:
        return got
    keys = picked(g)
    a = run_path(sh)
    b, p = a[:, 0], a[:, 1]
    out = []

    def add(key, t):
        if 0 <= key <= TOP_KEY and (key in keys if keys else key % 12 in WHITE) and (not out or out[-1][0] != key):
            out.append((key, float(t)))

    add(math.floor(p[0] + 0.5), b[0])
    pa, pb = p[:-1], p[1:]
    moves = np.nonzero((np.floor(pb) > np.floor(pa)) | (np.ceil(pb) < np.ceil(pa)))[0]
    for j in moves.tolist():  # (only the pieces passing a row's middle)
        x0, x1, t0, t1 = p[j], p[j + 1], b[j], b[j + 1]
        rows = (range(math.floor(x0) + 1, math.floor(x1) + 1) if x1 > x0 else
                range(math.ceil(x0) - 1, math.ceil(x1) - 1, -1))
        for key in rows:
            add(key, t0 + (t1 - t0) * (key - x0) / (x1 - x0))
    add(math.floor(p[-1] + 0.5), b[-1])
    if len(_crossed) > 256:
        _crossed.clear()
    _crossed[k] = out
    return out


def json_key(pts):
    return tuple(tuple(q) for q in pts)


def plan(sh, ppq):
    """(start tick, step in ticks, its grid name, keys in the run's order) of a run; keys empty = no notes."""
    g = sh.get("gliss") or DEFAULTS
    a = run_path(sh)
    b0, b1 = a[0, 0], a[-1, 0]
    keys = [k for k, _ in crossings(sh)]
    start = math.floor(b0 * ppq + 0.5)
    fixed = g.get("step", "auto")
    if fixed != "auto" and grid_ticks(fixed, ppq) >= 1:  # (a fixed step: the run's length follows from it)
        return start, grid_ticks(fixed, ppq), fixed, keys
    names = grids(g, ppq)
    span = (b1 - b0) * ppq
    if len(keys) < 2 or span <= 0:  # (one key / an upright line: the finest grid)
        name = min(names, key=lambda n: grid_ticks(n, ppq))
    else:  # the grid nearest one key per step (as a ratio: 1/32 vs 1/64 is as far as 1/16 vs 1/32); Last note
        want = span / (len(keys) - (1 if sh.get("end_dot") else 0))  # ending on the line's end = one step more
        name = min(names, key=lambda n: abs(math.log(grid_ticks(n, ppq) / want)))
    return start, grid_ticks(name, ppq), name, keys


def auto_name(sh, ppq):
    """The grid the step would pick itself (the Step dropdown's "Auto (1/32)")."""
    return plan(dict(sh, gliss=dict(sh.get("gliss") or DEFAULTS, step="auto")), ppq)[2]


def unsnapped(sh):
    return (sh.get("gliss") or DEFAULTS).get("timing") == "line"


def line_starts(sh, ppq):
    """(note starts, gaps up to the next note) in ticks, as floats, of an unsnapped run: each note where the line
    crosses its key's row (never before the note before: a line folding back in time). Last note "ends on the last
    point" with its key's row at the line's end: the starts are squeezed towards the start by one gap's room (like
    a grid run's one step more), so it has room to end there; its gap = up to the line's end. "Starts exactly on
    the last point": its gap = the one before it."""
    got = crossings(sh)
    end = run_path(sh)[-1, 0] * ppq
    t = np.maximum.accumulate(np.asarray([b for _, b in got], float) * ppq)
    n = len(t)
    if not sh.get("end_dot") and n > 1 and t[-1] >= end - 1e-6:
        t = t[0] + (t - t[0]) * (n - 1) / n
    nxt = np.append(t[1:], end if not sh.get("end_dot") or n < 2 else 2 * t[-1] - t[-2])
    return t, nxt - t


def run_notes(sh, ppq):
    """The run's own notes (no copies): (starts, ends, keys) int arrays."""
    g = sh.get("gliss") or DEFAULTS
    start, step, _, keys = plan(sh, ppq)
    n = g.get("len", 100)
    i = np.arange(len(keys))
    if unsnapped(sh) and len(keys) > 1:
        t, gap = line_starts(sh, ppq)
        gap = np.where(gap > 0, gap, step)  # (a note with no room: one grid step)
        starts = np.floor(t + 0.5).astype(np.int64)
        ends = np.floor(t + gap * n / 100 + 0.5).astype(np.int64)
    else:
        starts = start + np.floor(i * step + 0.5).astype(np.int64)  # (from the start each time: no drift, 1/48 etc.)
        ends = start + np.floor((i + n / 100) * step + 0.5).astype(np.int64)  # (100 % = exactly up to the next)
    if g.get("unit") == "ticks":
        ends = starts + max(1, math.floor(n + 0.5))
    return starts, np.maximum(ends, starts + 1), np.asarray(keys, np.int64)


def base_gap(sh, ppq, start=None):
    """What 100 % of Note length is in ticks: the step, or (unsnapped) the gap after the note starting at tick
    start (None: the gaps on average)."""
    step = plan(sh, ppq)[1]
    if not unsnapped(sh) or len(crossings(sh)) < 2:
        return step
    t, gap = line_starts(sh, ppq)
    if start is not None:
        j = int(np.argmin(abs(np.floor(t + 0.5) - start)))
        return gap[j] if gap[j] > 0 else step
    return max(1.0, float(gap.mean()))


def gliss_notes(sh, ppq):
    """The run's notes: (start, end, key) rows, one per picked key (+ the copies above / below), each "len" long."""
    g = sh.get("gliss") or DEFAULTS
    starts, ends, keys = run_notes(sh, ppq)
    if not len(keys):
        return np.zeros((0, 3), np.int64)
    keys = keys.tolist()
    rows = [np.column_stack([starts, ends, np.asarray(keys, np.int64)])]
    up, down = g.get("above", 0), g.get("below", 0)
    if up or down:  # copies, one picked key over each (counted among every picked key, not only the run's)
        every = run_keys(g, 0, TOP_KEY)
        place = {k: j for j, k in enumerate(every)}
        at = np.asarray([place[k] for k in keys], np.int64)
        every = np.asarray(every, np.int64)
        for c in list(range(1, up + 1)) + list(range(-1, -down - 1, -1)):
            j = at + c
            ok = (j >= 0) & (j < len(every))
            rows.append(np.column_stack([starts[ok], ends[ok], every[j[ok]]]))
    return np.concatenate(rows)


def shifted(g, key, c):
    """key moved c picked keys up (down when c < 0), as far as the picked keys go (None: key not picked)."""
    every = run_keys(g, 0, TOP_KEY)
    j = bisect.bisect_left(every, key)
    if j >= len(every) or every[j] != key:
        return None
    return every[max(0, min(len(every) - 1, j + c))]


def middle_note(sh, ppq):
    """(start tick, key) of the run's middle note, or None without notes."""
    starts, _, keys = run_notes(sh, ppq)
    if not len(keys):
        return None
    i = len(keys) // 2
    return int(starts[i]), int(keys[i])


def copies_at(sh, keys_away, side, ppq):
    """Copies above / below (side) for a dotted line dragged keys_away keys from the run (its middle note's key): the
    picked keys passed on that side, 0 or more (the other side never: user 11-02, nothing flips)."""
    g = sh.get("gliss") or DEFAULTS
    mid = middle_note(sh, ppq)
    if mid is None:
        return 0
    every = run_keys(g, 0, TOP_KEY)
    to = mid[1] + keys_away
    near = min(range(len(every)), key=lambda j: abs(every[j] - to))
    n = near - every.index(mid[1])
    return max(0, min(MOST_COPIES, n if side == "above" else -n))


def len_for(sh, ticks, ppq, start=None):
    """The run's "len" value (in its own unit) for notes ticks long (unsnapped: the note starting at tick start)."""
    g = sh.get("gliss") or DEFAULTS
    ticks = max(1, round(ticks))
    if g.get("unit") == "ticks":
        return min(ticks, MOST_LEN["ticks"])
    return min(max(LEAST_PCT, round(ticks / base_gap(sh, ppq, start) * 100, 4)), MOST_LEN["pct"])


def fit_pts(sh, ppq):
    """The run's line snapped to its notes: from the first key's note start to where the last key's note starts
    (Last note: starts exactly on it) or ends, on their rows. A bent run: its two end anchors move there, their
    handles along. None when there's nothing to snap to (under two keys, or unsnapped: its notes follow the line
    already)."""
    start, step, _, keys = plan(sh, ppq)
    if len(keys) < 2 or unsnapped(sh):
        return None
    a = [start / ppq, float(keys[0])]
    # where the last step starts (Last note starts exactly) or ends: never the last NOTE's end, so Note length and
    # copies never change the line (an Auto step would pick a new grid from it)
    end = start + math.floor((len(keys) - (1 if sh.get("end_dot") else 0)) * step + 0.5)
    b = [end / ppq, float(keys[-1])]
    pts = sh["pts"]
    (b0, _), (b1, _) = pts[0], pts[-1]
    first, last = (b, a) if b1 < b0 else (a, b)  # (the ends stay the ends they were)
    if len(pts) < 4:
        return [first, last]
    out = [list(p) for p in pts]
    for i, h, to in ((0, 1, first), (len(out) - 1, len(out) - 2, last)):
        d = (to[0] - out[i][0], to[1] - out[i][1])
        out[i] = list(to)
        out[h] = [out[h][0] + d[0], out[h][1] + d[1]]
    return out
