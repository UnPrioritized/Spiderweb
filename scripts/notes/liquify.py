"""Recolour's Liquify (user 2026-11-07, PLAN.md; like image editors' Liquify): a brush pushes the colours inside a
shape around. Notes never move or change length; only which colour each one has changes, like the Stir.

THE BEND MAP (rc["liquify"]["map"]): one grid over the shape's colour box (recolour.unit_xy's 0..1 box: flipped /
turned with the shape, a piece's whole's box), each grid point saying how far (in that box) the colour seen there is
fetched from: a spot p shows the colour found at p + strength * map(p) (between grid points: blended). Kept with the
shape, so it is applied again after every edit (never baked): the colours under it (Recolour mode, Stir, Warp) can
change and they flow along the same bends. Applied LAST, after every stir and the Warp (recolour.all_stirred; the
Warp reuses this file's lookup: notes/warp.py). One map per shape, no list
of strokes (user: like the editors; mistakes are fixed with Restore / undo).

Saved as {"grid": [across, up], "map": text (zlib + base64 of int16 pairs, 1 / SCALE of the box each), "frozen":
text (zlib + base64 of uint8 per grid point, 255 = fully frozen), "strength": % (only != 100), "on": False (only when
hidden), "edge": STIR_EDGES (only != "escape"), "moves": "shape" (only when it bends the shape itself, Deform
shapes stage G; + "bend_colours": False when Colours bend too is off; + "forward": True = its map says where each
point GOES, dab_fwd), "span":[x0, y0, x1, y1] (only when the map
grew past the colour box: where it lies in the box's units, span_of; its pushes stay in the box's units)}. "map" / "frozen" only when something is pushed / frozen; neither = no
Liquify (clean_liquify: None).

THE FREEZE MASK (stage D, image editors' Freeze / Thaw Mask): spots the brushes leave alone (partly frozen = partly
left alone), drawn as a see-through red tint while the Liquify window is open. It never changes a colour by itself:
engine.made_from leaves it out, so freezing / thawing never makes the notes again.

THE BRUSHES (dab): each mouse step changes the map near the brush, the brush measured on screen (J: the colour box's
units per screen pixel there, so a round brush stays round however the shape is stretched / turned). The map is a
"where it comes from" map, so each dab is put on top of the one before by looking the old map up at the moved spot.
"""

import base64
import json
import math
import weakref
import zlib

import numpy as np

from files.speed import compiled
from notes.basics import number

SCALE = 4096  # (map units per colour box: 1/4096 of the box is the finest push kept)
MOST_PUSH = 8.0  # (a spot looks at most this many boxes away: int16 / SCALE)
GRID_LONG = 256  # (grid points along the shape's longer side on screen when its first stroke is made)
GRID_SHORT = 16  # (at least this many along the other side)
MOST_GRID = 1024
SAMPLE_FAST = 2000  # (spots: from this many the map is looked up by the compiled loop)
MOST_STRENGTH = 400.0  # (user 2026-11-07: up to 400 %)
DEFAULTS = {"strength": 100.0, "on": True, "edge": "escape"}
TOOLS = ("push", "restore", "smooth", "twirl", "pinch", "bulge", "freeze", "thaw")
MASK_TOOLS = ("freeze", "thaw")
OPPOSITE = {"pinch": "bulge", "bulge": "pinch", "freeze": "thaw", "thaw": "freeze"}  # (Alt held; Twirl: turns back)

_DECODED = {}  # map text -> its array (read-only); the last few kept: every making of the notes reads it
_MASKS = {}  # frozen text -> its array 0..1 (read-only)


def decode(grid, text):
    """The map text -> float64 array (up, across, 2) in colour box units, read-only; None when it doesn't fit."""
    got = _DECODED.get(text)
    if got is not None and got.shape[:2] == (grid[1], grid[0]):
        return got
    try:
        raw = zlib.decompress(base64.b64decode(text.encode("ascii"), validate=True))
    except (ValueError, zlib.error, UnicodeEncodeError):
        return None
    gx, gy = grid
    if len(raw) == gx * gy * 4:
        kind = "<i2"
    elif len(raw) == gx * gy * 8:  # (a wide map: encode(wide=True), Shape Liquifys)
        kind = "<i4"
    else:
        return None
    a = np.frombuffer(raw, kind).astype(np.float64).reshape(gy, gx, 2) / SCALE
    a.setflags(write=False)
    if len(_DECODED) > 16:
        _DECODED.pop(next(iter(_DECODED)))
    _DECODED[text] = a
    return a


WIDE_MOST = 2 ** 31 - 1  # (a wide map's steps: MOST_PUSH_WIDE boxes)
MOST_PUSH_WIDE = WIDE_MOST / SCALE


def _steps(a, wide=False):
    """A map in whole 1 / SCALE steps, as saved (MOST_PUSH boxes at most: int16; wide: int32)."""
    top = WIDE_MOST if wide else 32767
    return np.clip(np.rint(np.asarray(a, np.float64) * SCALE), -top, top)


def encode(a, level=6, wide=False):
    """A map array (up, across, 2) -> its text (rounded to 1 / SCALE, at most MOST_PUSH boxes). level = zlib's
    (1 while a stroke goes on: a big map's text made every update, user 2026-11-17 "restore is stuttery"). The
    array it stands for is kept for decode (no unpacking it straight back). wide: int32 steps (Shape maps: they
    keep the moves divided by Strength, so 10 % stopped a push at 0.8 box; hunt 2026-11-17). decode tells the two
    apart by length."""
    st = _steps(a, wide)
    text = base64.b64encode(zlib.compress(st.astype("<i4" if wide else "<i2").tobytes(), level)).decode("ascii")
    got = st / SCALE
    got.setflags(write=False)
    if len(_DECODED) > 16:
        _DECODED.pop(next(iter(_DECODED)))
    _DECODED[text] = got
    return text


def rounded(a):
    """The map a as it comes back from its text (decode(encode(a))): the brush's live colours look it up so."""
    return _steps(a) / SCALE


def decode_mask(grid, text):
    """The frozen text -> float64 array (up, across) 0..1, read-only; None when it doesn't fit."""
    got = _MASKS.get(text)
    if got is not None and got.shape == (grid[1], grid[0]):
        return got
    try:
        raw = zlib.decompress(base64.b64decode(text.encode("ascii"), validate=True))
    except (ValueError, zlib.error, UnicodeEncodeError):
        return None
    gx, gy = grid
    if len(raw) != gx * gy:
        return None
    m = np.frombuffer(raw, np.uint8).astype(np.float64).reshape(gy, gx) / 255
    m.setflags(write=False)
    if len(_MASKS) > 16:
        _MASKS.pop(next(iter(_MASKS)))
    _MASKS[text] = m
    return m


def mask_steps(m):
    """A mask as saved: whole 1 / 255 steps."""
    return np.clip(np.rint(np.asarray(m, np.float64) * 255), 0, 255)


def encode_mask(m):
    return base64.b64encode(zlib.compress(mask_steps(m).astype(np.uint8).tobytes(), 6)).decode("ascii")


def map_of(lq):
    """A clean Liquify's map as an array (zeros when nothing is pushed)."""
    if lq.get("map"):
        return decode(lq["grid"], lq["map"])
    return empty(lq["grid"])


_LARGEST = {}  # map text -> its largest push each way (across, up); the last few kept, like _DECODED


def largest_of(lq):
    """A clean Liquify's largest push each way, |map| at most (across, up), Strength left out: the same numbers as
    np.abs(map_of(lq)).reshape(-1, 2).max(0), kept per map (every making of the notes asks; ~20 ms at a 1024 grid)."""
    text = lq.get("map")
    if not text:
        return np.zeros(2)
    key = (text, tuple(lq["grid"]))
    got = _LARGEST.get(key)
    if got is None:
        got = np.abs(map_of(lq)).reshape(-1, 2).max(0)
        got.setflags(write=False)
        if len(_LARGEST) > 16:
            _LARGEST.pop(next(iter(_LARGEST)))
        _LARGEST[key] = got
    return got


def mask_of(lq):
    """A clean Liquify's freeze mask (up, across) 0..1, or None (nothing frozen)."""
    return decode_mask(lq["grid"], lq["frozen"]) if lq and lq.get("frozen") else None


def with_arrays(lq, a, m, level=6):
    """lq (a clean Liquify or {}: its settings kept) with the map a and the mask m (None = nothing frozen) -> clean
    form, or None. level: encode's."""
    keep = {k: v for k, v in (lq or {}).items() if k not in ("grid", "map", "frozen")}
    out = dict(keep, grid=[a.shape[1], a.shape[0]])
    if np.rint(a * SCALE).any():
        out["map"] = encode(a, level, wide=keep.get("moves") == "shape" and keep.get("forward") is True)
    if m is not None and mask_steps(m).any():
        out["frozen"] = encode_mask(m)
    return clean_liquify(out)


def clean_liquify(d):
    """A saved rc["liquify"] -> its clean form (see the top), or None (no Liquify: nothing pushed or frozen)."""
    if not isinstance(d, dict):
        return None
    g, text, fz = d.get("grid"), d.get("map"), d.get("frozen")
    if not (isinstance(g, list) and len(g) == 2 and all(number(v) and v == int(v) and 2 <= v <= MOST_GRID for v in g)):
        return None
    g = [int(v) for v in g]
    out = {"grid": g}
    if isinstance(text, str) and text:
        a = decode(g, text)
        if a is not None and a.any():
            out["map"] = text
    if isinstance(fz, str) and fz:
        m = decode_mask(g, fz)
        if m is not None and m.any():
            out["frozen"] = fz
    if len(out) == 1:
        return None
    s = d.get("strength")
    if number(s) and round(float(s), 3) != DEFAULTS["strength"]:
        out["strength"] = round(min(max(float(s), 0.0), MOST_STRENGTH), 3)
    if d.get("on") is False:
        out["on"] = False
    from notes.recolour import STIR_EDGES
    if d.get("edge") in STIR_EDGES and d["edge"] != DEFAULTS["edge"]:
        out["edge"] = d["edge"]
    if d.get("moves") == "shape":  # (bends the shape itself: notes/shape_bend.py, PLAN.md "Deform shapes" stage G;
        # "bend_colours": False = Colours bend too off, only with Shape)
        out["moves"] = "shape"
        if d.get("bend_colours") is False:
            out["bend_colours"] = False
        if d.get("chop") is False:  # (Cut notes where bent off: the outline bent, its notes laid again; user 11-17)
            out["chop"] = False
        # (a Shape map is a "where each point goes" one, "forward": True (PLAN.md Liquify Shape rebuild); one made
        # before it ("where it comes from", never released) opens unbent: its map dropped, user 2026-11-17)
        if d.get("forward") is True and "map" in out:
            out["forward"] = True
        else:
            out.pop("map", None)
            if "frozen" not in out:  # (nothing pushed or frozen)
                return None
    span = _clean_span(d.get("span"))
    if span:  # (the map grown past the box: span_of)
        out["span"] = span
    return out


SPAN = (0.0, 0.0, 1.0, 1.0)  # (the box the map covers, in the colour box's units: the box itself)
MOST_SPAN = 4.0  # (a grown map reaches at most this many boxes past each side)


def span_of(lq):
    """(x0, y0, x1, y1): where a Liquify's map lies in the colour box's units. The box itself, unless a stroke of a
    Liquify set to Shape reached past it and the map grew (hunt 2026-11-17, user: brush the part pushed out)."""
    s = (lq or {}).get("span")
    return tuple(s) if s else SPAN


def to_map(x, y, span):
    """Spots of the colour box -> the map's own 0..1 (the very same numbers while the map covers the box)."""
    if span == SPAN:
        return x, y
    return (np.asarray(x, np.float64) - span[0]) / (span[2] - span[0]), \
        (np.asarray(y, np.float64) - span[1]) / (span[3] - span[1])


def map_scale(span):
    """How many colour box units one map unit is, each way (the map's PUSHES stay in the box's units: only where a
    grid point sits changes; the brush works in the map's own units, roll_liquify divides / multiplies by this)."""
    return np.array([span[2] - span[0], span[3] - span[1]])


def _clean_span(v):
    """A saved "span" -> 4 numbers (x0 < 0 .. 1 < x1 grown at most MOST_SPAN), or None (the box: not saved)."""
    if not (isinstance(v, list) and len(v) == 4 and all(number(x) for x in v)):
        return None
    x0, y0, x1, y1 = (float(x) for x in v)  # (never rounded: a map grown again keeps its grid points exactly where
    # they were; 9 places moved them a hair and a row end sitting on a half tick flipped)
    x0, y0 = max(min(x0, 0.0), -MOST_SPAN), max(min(y0, 0.0), -MOST_SPAN)
    x1, y1 = min(max(x1, 1.0), 1 + MOST_SPAN), min(max(y1, 1.0), 1 + MOST_SPAN)
    return None if (x0, y0, x1, y1) == SPAN else [x0, y0, x1, y1]


def grown_span(old, want, shape):
    """The span holding want, grown from old by WHOLE steps of the map's grid (shape: (up, across, ...)) each side,
    so the old grid points stay grid points (regrid exact); each way at most MOST_GRID points (then as far as that
    reaches) and MOST_SPAN past the box. -> (span, (left, down, right, up) steps added)."""
    gy, gx = shape[:2]
    sx, sy = (old[2] - old[0]) / (gx - 1), (old[3] - old[1]) / (gy - 1)
    lo_x = max(0, math.ceil((old[0] - max(want[0], -MOST_SPAN)) / sx - 1e-9))
    hi_x = max(0, math.ceil((min(want[2], 1 + MOST_SPAN) - old[2]) / sx - 1e-9))
    lo_y = max(0, math.ceil((old[1] - max(want[1], -MOST_SPAN)) / sy - 1e-9))
    hi_y = max(0, math.ceil((min(want[3], 1 + MOST_SPAN) - old[3]) / sy - 1e-9))
    room_x, room_y = MOST_GRID - gx, MOST_GRID - gy  # (past MOST_GRID: shared out, the far side first cut)
    if lo_x + hi_x > room_x:
        lo_x, hi_x = min(lo_x, room_x - min(hi_x, room_x // 2)), min(hi_x, room_x // 2 if lo_x else room_x)
    if lo_y + hi_y > room_y:
        lo_y, hi_y = min(lo_y, room_y - min(hi_y, room_y // 2)), min(hi_y, room_y // 2 if lo_y else room_y)
    lo_x, hi_x, lo_y, hi_y = (max(0, v) for v in (lo_x, hi_x, lo_y, hi_y))
    span = (old[0] - lo_x * sx, old[1] - lo_y * sy, old[2] + hi_x * sx, old[3] + hi_y * sy)
    return span, (lo_x, lo_y, hi_x, hi_y)


def regrid(a, m, old, new):
    """The map a (and freeze mask m or None) laid on the span new (grown_span's: old plus whole steps) instead of old:
    every old grid point kept as it was, the new ones past the old span = its edge's (the map was read so past its
    edge already), so every spot bends exactly as before. Pushes stay in the colour box's units (the same saved
    numbers). Shape maps (sample_fwd) too."""
    gy, gx = a.shape[:2]
    sx, sy = (old[2] - old[0]) / (gx - 1), (old[3] - old[1]) / (gy - 1)
    lo_x, lo_y = int(round((old[0] - new[0]) / sx)), int(round((old[1] - new[1]) / sy))
    hi_x, hi_y = int(round((new[2] - old[2]) / sx)), int(round((new[3] - old[3]) / sy))
    pad = ((lo_y, hi_y), (lo_x, hi_x))
    b = np.pad(np.asarray(a, np.float64), pad + ((0, 0),), mode="edge")
    mm = None if m is None else np.pad(np.asarray(m, np.float64), pad, mode="edge")
    return b, mm


def full(lq):
    """Every setting of a clean Liquify filled in."""
    return dict(DEFAULTS, **lq)


def new_grid(width_px, height_px):
    """[across, up] grid points for a shape this big on screen (its first stroke)."""
    w, h = max(float(width_px), 1.0), max(float(height_px), 1.0)
    if w >= h:
        return [GRID_LONG, int(min(GRID_LONG, max(GRID_SHORT, round(GRID_LONG * h / w))))]
    return [int(min(GRID_LONG, max(GRID_SHORT, round(GRID_LONG * w / h)))), GRID_LONG]


def empty(grid):
    return np.zeros((grid[1], grid[0], 2), np.float64)


def least_radius(shape, J, r):
    """r px, or more: enough to reach one step of a grid of shape (up, across, ...) both ways (a brush between grid
    points changes nothing)."""
    gy, gx = shape[:2]
    J = np.asarray(J, np.float64)
    ex, ey = float(np.hypot(J[0, 0], J[0, 1])), float(np.hypot(J[1, 0], J[1, 1]))
    need = [1.0 / (e * (g - 1)) for e, g in ((ex, gx), (ey, gy)) if e > 0 and np.isfinite(e)]
    return max([r] + need)


def _runs(idx, gap=3):
    """Sorted grid numbers -> [(first, last), ...] of the runs in them (numbers closer than gap = one run)."""
    idx = [int(v) for v in idx]
    out = []
    for v in idx:
        if out and v - out[-1][1] < gap:
            out[-1][1] = v
        else:
            out.append([v, v])
    return [tuple(r) for r in out]


def finer(a, m, r, J, cells=2.0):
    """The map a and mask m (None: none) on a finer grid when a brush of radius r px (J: the colour box's units per
    screen px at its spot) reaches fewer than `cells` grid steps either way (a long shape zoomed in: the brush did
    nothing, or a blocky bend). Each step is cut into k equal ones (at most MOST_GRID points), so the bends stay as
    they were (a blend between grid points, cut finer, is the same blend). Not needed: a and m themselves."""
    gy, gx = a.shape[:2]
    J = np.asarray(J, np.float64)

    def k_for(g, e):
        if not (e > 0 and np.isfinite(e)):
            return 1
        k = int(np.ceil(cells / e / (g - 1)))
        return max(1, min(k, (MOST_GRID - 1) // (g - 1)))
    kx = k_for(gx, r * float(np.hypot(J[0, 0], J[0, 1])))
    ky = k_for(gy, r * float(np.hypot(J[1, 0], J[1, 1])))
    if kx == 1 and ky == 1:
        return a, m
    nx, ny = (gx - 1) * kx + 1, (gy - 1) * ky + 1
    jj, ii = np.mgrid[0:ny, 0:nx]
    u, v = ii / (nx - 1), jj / (ny - 1)
    a2 = np.array(sample(a, u, v), np.float64)
    m2 = None if m is None else np.array(sample(np.asarray(m, np.float64)[..., None], u, v)[..., 0])
    return a2, m2


def sample(a, x, y):
    """The map a at spots (x, y) of the colour box (0..1; past it: the edge's), blended between grid points."""
    if np.ndim(x) == 1 and len(x) >= SAMPLE_FAST and a.shape[2] == 2:
        from files.speed import loops
        fast = loops()
        if fast:  # (the compiled twin: the same sums in the same order, files/fastloops.liquify_sample)
            out = np.empty((len(x), 2), np.float64)
            fast.liquify_sample(np.ascontiguousarray(a, np.float64), np.ascontiguousarray(x, np.float64),
                                np.ascontiguousarray(y, np.float64), out)
            return out
    gy, gx = a.shape[:2]
    fx = np.clip(np.asarray(x, np.float64), 0.0, 1.0) * (gx - 1)
    fy = np.clip(np.asarray(y, np.float64), 0.0, 1.0) * (gy - 1)
    i0 = np.minimum(fx.astype(np.int64), gx - 2)
    j0 = np.minimum(fy.astype(np.int64), gy - 2)
    tx, ty = (fx - i0)[..., None], (fy - j0)[..., None]
    return ((a[j0, i0] * (1 - tx) + a[j0, i0 + 1] * tx) * (1 - ty)
            + (a[j0 + 1, i0] * (1 - tx) + a[j0 + 1, i0 + 1] * tx) * ty)


def touched(a, x, y):
    """Which spots (x, y) sit in a grid cell with something pushed at one of its corners (the rest fetch from
    themselves: left alone, most notes of a big shape)."""
    nz = (a != 0).any(2)
    cell = nz[:-1, :-1] | nz[1:, :-1] | nz[:-1, 1:] | nz[1:, 1:]
    gy, gx = a.shape[:2]
    i = np.minimum((np.clip(x, 0.0, 1.0) * (gx - 1)).astype(np.int64), gx - 2)
    j = np.minimum((np.clip(y, 0.0, 1.0) * (gy - 1)).astype(np.int64), gy - 2)
    return cell[j, i]


def _where(at, rc, box, own, lq=None, colours=None):
    """liquify_from's first half: rc's Liquify settings (filled in; lq: these instead, the Warp's) and each note's
    place (x, y) in the colour box, with what the edge rule needs later. colours: each note's colour now (Repeat
    the colours finds the rows' repeats in them)."""
    from notes.recolour import BOUNCES, WRAPS, rows_of, unit_xy
    lq = lq or full(clean_liquify(rc.get("liquify")) or {})
    s, e, k = (np.asarray(at[:, c], np.float64) for c in (0, 1, 2))
    mid = (s + e) / 2
    # ("soft": the Warp's Edges: Smooth (found): its lq says so, or rc "_soft" (a Warp drag's Live, roll_liquify))
    w = {"lq": lq, "edge": lq["edge"], "rows": None, "t0": 0.0, "t1": 0.0, "rep": None,
         "soft": bool(lq.get("soft") or rc.get("_soft"))}
    if w["edge"] in WRAPS or w["edge"] == "bend":
        w["rows"] = rows_of(at, 0.0)
        w["t0"], w["t1"] = float(at[:, 0].min()), float(at[:, 1].max())
        if w["edge"] == "repeat" and colours is not None and len(at):
            w["rep"] = w["rows"].repeat_of(at, colours)
        elif w["edge"] in BOUNCES and colours is not None and len(at):  # (Bounce back: lines on the colours' runs)
            w["rep"] = w["rows"].bounce_of(at, colours, w["edge"] == "bounce2")
    if w["edge"] == "bend":
        mid = w["rows"].stretched(mid, k, w["t0"], w["t1"])
    w["x"], w["y"] = unit_xy(mid, k, rc, box, own)
    return w


def _fetched(w, a, x, y, rc, box, own, exact=None):
    """(middle in ticks, key) the spots (x, y) of the colour box fetch their colour from under the map a
    (liquify_from's second half: the edge choices like stir_from's). exact: (ox, oy) a Warp's exact fetches
    (warp.exact_offsets; NaN = the map's)."""
    sp = span_of(w["lq"])
    if sp == SPAN:
        d = sample(a, x, y) * (w["lq"]["strength"] / 100)
    else:  # (a map grown past the box: looked up at its own places; its pushes are in the box's units)
        d = sample(a, *to_map(x, y, sp)) * (w["lq"]["strength"] / 100)
    if exact is not None:
        ox, oy = exact
        got = ~np.isnan(ox)
        d[got, 0], d[got, 1] = ox[got], oy[got]
    return _placed(w, x + d[:, 0], y + d[:, 1], rc, box, own)


def _arrows(w, at, idx, x, y, rc, box, own):
    """(tx, ty, kx, ky, h): what each note idx (at spots (x, y)) covers, as two arrows across the box (its half
    length h ticks along, half a key up) -> fetched at their ends; the box's units per tick / key are the same
    everywhere (unit_xy is straight) except with Bend with the outline."""
    s, e, k = (np.asarray(at[idx, c], np.float64) for c in (0, 1, 2))
    m, h = (s + e) / 2, np.maximum((e - s) / 2, 0.5)
    if w["edge"] == "bend":
        xb, yb = _unit(w, m + h, k, rc, box, own)
        xd, yd = _unit(w, m, k + 0.5, rc, box, own)
        return xb - x, yb - y, xd - x, yd - y, h
    bx, by = _unit(w, np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0]), rc, box, own)
    return (bx[1] - bx[0]) * h, (by[1] - by[0]) * h, (bx[2] - bx[0]) * 0.5, (by[2] - by[0]) * 0.5, h


def _placed(w, x, y, rc, box, own):
    """(middle in ticks, key) of the spots (x, y) of the colour box a map fetches from, after the edge choices."""
    from notes.recolour import WRAPS, from_unit
    edge = w["edge"]
    if edge in WRAPS:  # (left as it is: Rows.wrapped brings it back by the notes' own repeat, user 2026-11-10)
        pass
    elif edge != "escape":
        out = lambda v: (v < -1e-9) | (v > 1 + 1e-9)  # noqa: E731 (a hair past the edge stays on it)
        x = np.clip(np.where(out(x), np.mod(x, 1.0), x), 0, 1)
        y = np.clip(np.where(out(y), np.mod(y, 1.0), y), 0, 1)
    else:
        x, y = np.clip(x, 0, 1), np.clip(y, 0, 1)
    mid, key = from_unit(np.asarray(x, np.float64), np.asarray(y, np.float64), rc, box, own)
    if edge in WRAPS:
        mid, key = w["rows"].wrapped(mid, key, w.get("rep"), edge)
    elif edge == "bend":
        mid = w["rows"].unstretched(mid, key, w["t0"], w["t1"])
    return mid, key


def _unit(w, mid, k, rc, box, own):
    """Places (middle in ticks, key) -> (x, y) of the colour box as _where counts them (Bend with the outline: the
    row stretched first)."""
    from notes.recolour import unit_xy
    if w["edge"] == "bend":
        mid = w["rows"].stretched(mid, k, w["t0"], w["t1"])
    return unit_xy(mid, k, rc, box, own)


SOFT_MOST = 4  # (Edges: Smooth, a squashed spot: at most this many looks each way across what it covers)
SOFT_SQUASHED = 1.25  # (a note covering more than this many notes' worth of the colours' AREA = squashed)
# (Edges: Smooth, a stretched spot: looks round where it fetches from, out to SOFT_REACH keys' height (as far
# across, in the colour box's own proportions), each weighing in; the reach grows from 0 (a cell not stretched =
# exactly the nearest note) to full where a key of the colours shows twice as tall or more)
SOFT_REACH = 1.0
_RING = [(0.0, 0.0, 1.0)] + [(0.5 * np.cos(t), 0.5 * np.sin(t), 0.7) for t in np.arange(6) * np.pi / 3] + \
    [(np.cos(t), np.sin(t), 0.35) for t in np.arange(6) * np.pi / 3 + np.pi / 6]  # (looks: across, up, weight)
# (the compiled pick (fastloops.warp_soft) takes the same looks: look 0 the middle, 1 .. 6 the inner ring
# (fastloops.ROUND_INNER = 7), _covered's looks for every count in one table (SOFT_MOST 4 a side there too))


def _cover_looks(nt, nk):
    """_covered's nt x nk looks (shares along the note, across its key), nearest the middle first."""
    a = (np.arange(nt) + 0.5) / nt - 0.5
    b = (np.arange(nk) + 0.5) / nk - 0.5
    A, B = (v.ravel() for v in np.meshgrid(a, b))
    order = np.argsort(np.hypot(A, B), kind="stable")  # (nearest the middle first)
    return A[order], B[order]


def _cover_table():
    """Every look count's looks in one table for the compiled vote: (start of each (nt - 1) * SOFT_MOST + nk - 1,
    across shares, up shares)."""
    start, ta, tb = [0], [], []
    for nt in range(1, SOFT_MOST + 1):
        for nk in range(1, SOFT_MOST + 1):
            A, B = _cover_looks(nt, nk)
            ta.extend(A.tolist())
            tb.extend(B.tolist())
            start.append(len(ta))
    return np.asarray(start, np.int64), np.asarray(ta, np.float64), np.asarray(tb, np.float64)


def _place_numbers(w, rc, box, own):
    """_placed's numbers as the compiled votes take them (fastloops._unit_back), or None: Bend with the outline (its
    arrows differ note by note: NumPy's way). Wrap around (fp[0] 2): its key rows follow the 14 numbers (1, row count, the
    rows' keys, first starts, repeat ends: Rows.wrapped's; a Warp on Wrap around took the slow NumPy pick, ~2 s a
    move at 2 M notes with Smooth edges)."""
    edge = w["edge"]
    if edge == "bend":
        return None
    from notes.recolour import BOUNCES, WRAPS, whole_shares
    f = rc.get("frame")
    turned = bool(f and f[1])
    t0, t1, k0, k1 = own if turned else box
    (a0, a1), (b0, b1) = whole_shares(rc) if turned else ((0.0, 1.0), (0.0, 1.0))
    fp = np.array([0 if edge == "escape" else 2 if edge in WRAPS else 1, turned, a0, a1 - a0, b0,
                   b1 - b0, b1 > b0, f[1] if turned else 1, f[2] if turned else 1, t0, max(t1 - t0, 1.0), k0,
                   k1 - k0, k1 > k0], np.float64)
    if edge in WRAPS:  # (Repeat the colours: repeat_of's ends + the key height after them; Bounce back: 2.0 /
        # edge twice 3.0 with its bounce lines)
        rows = w["rows"]
        if not len(rows.keys):
            return None
        rep = w.get("rep")
        if edge in BOUNCES:  # (+ the lowest / highest key line after the rows' numbers)
            if rep is None:
                rep = rows.bounce_of(None, None, edge == "bounce2")
            aw, bw = rep[:2]
            more = [[float(rep[2]), float(rep[3])]]
        else:
            aw, bw = (rows.a, rows.bw) if rep is None else rep[:2]
            more = [[rep[2]]] if rep is not None and rep[2] is not None else []
        fp = np.concatenate([fp, [{"bounce": 2.0, "bounce2": 3.0}.get(edge, 1.0), float(len(rows.keys))],
                             np.asarray(rows.keys, np.float64),
                             np.asarray(aw, np.float64), np.asarray(bw, np.float64)] + more)
    return fp


_COVER_LOOKS = _cover_table()


def found(w, a, at, idx, x, y, rc, box, own, now, kept=None):
    """The notes of at (their places) the notes idx (at spots (x, y) of the box) take their colour from under the map
    a. Sharp (the Liquify; the Warp's Edges: Sharp): the note nearest the spot the map fetches from. w["soft"] (the
    Warp's Edges: Smooth, user 2026-11-09: stretched cells came out in big steps, squashed ones noisy; a note has ONE
    colour, never a blend): where the note covers less than a note's worth of the colours (stretched), looks round
    the spot it fetches from vote (_round_vote: a stretched staircase's corners round off, a slanted stripe's edge
    comes out as a slant instead of big steps); where it covers more (squashed), up to SOFT_MOST x SOFT_MOST looks
    across what it covers vote, the most common colour wins (the look nearest the middle on a tie). The note handed
    back is one with the winning colour (colour sets / groups / kept_apart then as usual). Live calls it the same
    way: the brush's live colours == let go's."""
    from notes.recolour import _sorted_notes, nearest
    s100 = w["lq"]["strength"] / 100
    ox = oy = None
    ends = None
    il = None  # (the three exact fetches a spot, interleaved as warp_soft takes them, or None)
    if w.get("exact") is not None:  # (a Warp: each spot's exact fetch where it settles, warp.exact_offsets)
        from notes.warp import exact_offsets, exact3_offsets
        if w.get("soft") and len(idx):
            # (Edges: Smooth judges what each note covers from where its two arrows' ends fetch: those EXACT too, like
            # the middle (Warp Split hunt 2026-11-10: the middle exact but the ends from the map made ~7x the one-note
            # slivers). Spot i's middle at 3i, its arrow ends at 3i + 1 and 3i + 2. Not Bend with the outline (arrows
            # of their own a note): warp.exact3_offsets' quicker way, the compiled side doing the fetches and the vote
            # in one pass (_soft_fused), whatever the count (live hands over a few notes, let go all of them: the
            # same way both times)
            if w["edge"] != "bend":
                got = _soft_fused(w, a, at, idx, x, y, rc, box, own, now, kept, s100)
                if got is not None:
                    return got
            arrows = _arrows(w, at, idx, x, y, rc, box, own)
            tx, ty, kx, ky = arrows[:4]
            if w["edge"] != "bend":
                il = exact3_offsets(w["exact"], a, x, y, tx, ty, kx, ky)
            else:
                X, Y = np.empty(3 * len(x)), np.empty(3 * len(x))
                X[0::3], Y[0::3] = x, y
                np.add(x, tx, out=X[1::3])
                np.add(y, ty, out=Y[1::3])
                np.add(x, kx, out=X[2::3])
                np.add(y, ky, out=Y[2::3])
                il = exact_offsets(w["exact"], a, X, Y)
            ox, oy = il[0][0::3], il[1][0::3]
            ends = arrows + ((il[0][1::3], il[1][1::3]), (il[0][2::3], il[1][2::3]))
        else:
            ox, oy = exact_offsets(w["exact"], a, x, y)
    fp = _place_numbers(w, rc, box, own) if len(idx) else None
    fast = compiled("warp_soft") if fp is not None else None
    if fast:  # (the compiled pick, note by note: the very same sums as below, files/fastloops.warp_soft (Sharp too,
        # C7: the nearest note alone); user 11-09: heavy per-note work compiled)
        kept = kept or _sorted_notes(at)
        to = np.ascontiguousarray
        _, _, o, _, ms, keys, first, last, line, base, top, gap = kept
        bx, by = _unit(w, np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0]), rc, box, own)
        kx, ky = (bx[2] - bx[0]) * 0.5, (by[2] - by[0]) * 0.5
        c = np.array([bx[1] - bx[0], by[1] - by[0], kx, ky, np.hypot(kx, ky) * 2, s100, SOFT_SQUASHED, SOFT_REACH,
                      0.0 if w.get("soft") else 1.0, 0.0 if ends is None else 1.0], np.float64)
        out = np.empty(len(idx), np.int64)
        none = np.zeros(0)
        ox_c, oy_c = il if il is not None else (ox, oy)  # (interleaved: the middle's, the two arrow ends' per spot)
        fast.warp_soft(to(at, np.int64), to(idx, np.int64), to(x, np.float64), to(y, np.float64),
                       to(ox_c, np.float64) if ox_c is not None else none, to(oy_c, np.float64) if oy_c is not None else none,
                       to(a, np.float64), c, fp, np.asarray(_RING, np.float64), *_COVER_LOOKS, to(keys, np.int64), to(first, np.int64),
                       to(last, np.int64), to(line, np.float64), to(ms, np.float64), to(o, np.int64), float(base),
                       float(top), float(gap), to(now, np.int64), out)
        return out
    d = sample(a, x, y) * s100
    if ox is not None:
        got = ~np.isnan(ox)
        d[got, 0], d[got, 1] = ox[got], oy[got]
    sx, sy = x + d[:, 0], y + d[:, 1]  # (where in the box each spot fetches from)
    mid, key = _placed(w, sx, sy, rc, box, own)
    if not w.get("soft") or not len(idx):
        return nearest(at, mid, key, own=False, kept=kept)
    kept = kept or _sorted_notes(at)
    now = np.asarray(now)
    tx, ty, kx, ky, h = (ends or _arrows(w, at, idx, x, y, rc, box, own))[:5]
    if ends is not None:
        m1, k1 = _fetched(w, a, x + tx, y + ty, rc, box, own, ends[5])
        m3, k3 = _fetched(w, a, x + kx, y + ky, rc, box, own, ends[6])
    else:
        m1, k1 = _fetched(w, a, x + tx, y + ty, rc, box, own)
        m3, k3 = _fetched(w, a, x + kx, y + ky, rc, box, own)
    vt = np.stack([m1 - mid, k1 - key], 1) * 2  # (along the note's length, in ticks / keys of the colours)
    vk = np.stack([m3 - mid, k3 - key], 1) * 2  # (across its key)
    # (in notes' worth: time / the note's length, keys as they are. SQUASHED = the note covers more than
    # SOFT_SQUASHED notes' AREA of the colours; a slant alone (a shear: one key up = many notes along in time, on
    # long thin spam) covers no more area and stays with the 4-notes vote: spread looks there only gave noise)
    ut, uk = vt[:, 0] / (2 * h), vt[:, 1]
    wt, wk = vk[:, 0] / (2 * h), vk[:, 1]
    area = np.abs(ut * wk - uk * wt)
    # (how long each arrow is, in notes' worth; sqrt, not hypot: the compiled twin's gives the very same bits)
    rt, rk = np.sqrt(ut * ut + uk * uk), np.sqrt(wt * wt + wk * wk)
    out = np.empty(len(idx), np.int64)
    flat = area <= SOFT_SQUASHED
    # (stretched: how far the looks reach, 0 where a key of the colours shows a key tall or less, SOFT_REACH keys
    # (in the box) where it shows twice as tall or more)
    grow = np.clip(1.0 / np.sqrt(np.maximum(area, 1e-12)) - 1.0, 0.0, 1.0)
    f = np.flatnonzero(flat & (grow < 0.05))
    if len(f):
        out[f] = nearest(at, mid[f], key[f], own=False, kept=kept)
    f = np.flatnonzero(flat & (grow >= 0.05))
    if len(f):
        one = np.hypot(kx, ky) * 2  # (one key's height in the box: the same everywhere, or per note with Bend)
        r = (one[f] if np.ndim(one) else one) * SOFT_REACH * grow[f]
        out[f] = _round_vote(w, at, kept, sx[f], sy[f], r, rc, box, own, now)
    q = np.flatnonzero(~flat)
    if len(q):
        nt = np.clip(np.ceil(rt[q]), 1, SOFT_MOST).astype(np.int64)
        nk = np.clip(np.ceil(rk[q]), 1, SOFT_MOST).astype(np.int64)
        for a_ in range(1, SOFT_MOST + 1):
            for b_ in range(1, SOFT_MOST + 1):
                g = q[(nt == a_) & (nk == b_)]
                if len(g):
                    out[g] = _covered(at, kept, mid[g], key[g], vt[g], vk[g], a_, b_, now)
    return out


def _soft_fused(w, a, at, idx, x, y, rc, box, own, now, kept, s100):
    """found() for a Warp's Edges: Smooth (not Bend) in one compiled pass: each note's three exact fetches
    (warp.exact3_offsets' way) and the vote, note by note (files/fastloops.warp_soft2): no arrows, no three-a-spot
    lists (~180 MB a move at 2 M notes). None: no compiled loop (found() then does the same sums in NumPy)."""
    from notes.recolour import _sorted_notes
    from notes.warp import _kind, _near_cells, STEPS, LAZY
    fp = _place_numbers(w, rc, box, own)
    fast = compiled("warp_soft2") if fp is not None else None
    if not fast:
        return None
    kept = kept or _sorted_notes(at)
    to = np.ascontiguousarray
    _, _, o, _, ms, keys, first, last, line, base, top, gap = kept
    P, smooth, fold, L = w["exact"]
    X, Y = L or (None, None)
    bx, by = _unit(w, np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0]), rc, box, own)
    kx, ky = (bx[2] - bx[0]) * 0.5, (by[2] - by[0]) * 0.5
    c = np.array([bx[1] - bx[0], by[1] - by[0], kx, ky, np.hypot(kx, ky) * 2, s100, SOFT_SQUASHED, SOFT_REACH, 0.0,
                  1.0], np.float64)
    near = _near_cells(fold, a.shape[0], a.shape[1])
    out = np.empty(len(idx), np.int64)
    fast.warp_soft2(to(at, np.int64), to(idx, np.int64), to(x, np.float64), to(y, np.float64), to(a, np.float64), c,
                    fp, np.asarray(_RING, np.float64), *_COVER_LOOKS, to(keys, np.int64), to(first, np.int64),
                    to(last, np.int64), to(line, np.float64), to(ms, np.float64), to(o, np.int64), float(base),
                    float(top), float(gap), to(now, np.int64), to(P, np.float64), _kind(smooth),
                    to(X, np.float64) if X is not None else np.zeros(0),
                    to(Y, np.float64) if Y is not None else np.zeros(0),
                    near.ravel() if near is not None else np.zeros(0, np.uint8), STEPS, LAZY, out)
    return out


def _round_vote(w, at, kept, sx, sy, r, rc, box, own, now):
    """Edges: Smooth on a stretched spot: looks in a round patch (radius r, box units) round where each spot fetches
    from (sx, sy) vote, nearer ones weighing more (_RING); the colour with the most weight wins (a tie: the middle's).
    A stripe's staircase (each key row one step) magnified by a stretch gets its corners rounded about a key wide:
    the edge comes out as a slant. The note handed back = the look nearest the middle with the winning colour.
    QUICK FIRST (C4, user 11-09: "quite laggy"): the middle + the outer ring first; all one colour = that colour (the
    middle's note), the inner ring never looked at (most spots, away from a colour's edge); only the rest vote whole.
    (Rule, not a shortcut: live and let go both go through here.)"""
    from notes.recolour import nearest
    n = len(sx)
    ring = np.asarray(_RING, np.float64)
    r = np.broadcast_to(np.asarray(r, np.float64), (n,))[:, None]

    def looks(rows, which):  # (those spots' looks, all in one go: one placing + one search)
        L = ring[which]
        mid, key = _placed(w, (sx[rows, None] + L[:, 0] * r[rows]).ravel(),
                           (sy[rows, None] + L[:, 1] * r[rows]).ravel(), rc, box, own)
        return nearest(at, mid, key, own=False, kept=kept).reshape(len(rows), len(L))
    outer = np.r_[0, 7:len(ring)]
    first = looks(np.arange(n), outer)
    c = now[first]
    mixed = np.flatnonzero((c != c[:, :1]).any(1))
    out = first[:, 0].copy()
    if not len(mixed):
        return out
    got = np.empty((len(mixed), len(ring)), np.int64)
    got[:, outer] = first[mixed]
    got[:, 1:7] = looks(mixed, slice(1, 7))
    n = len(mixed)
    col = now[got]
    K = int(col.max()) + 1
    rows = np.arange(n)[:, None] * K
    score = np.bincount((rows + col).ravel(), weights=np.broadcast_to(ring[:, 2], col.shape).ravel(),
                        minlength=n * K).reshape(n, K)
    score[np.arange(n), col[:, 0]] += 1e-6  # (a tie: the middle's colour)
    best = np.argmax(score, 1)
    pos = np.argmax(col == best[:, None], 1)  # (the first look (nearest the middle) with that colour)
    out[mixed] = got[np.arange(n), pos]
    return out


def _covered(at, kept, mid, key, vt, vk, nt, nk, now):
    """Edges: Smooth on a squashed spot: nt x nk looks spread over what the note covers (the arrows vt / vk round
    (mid, key)) vote; the most common colour wins (a tie: the one looked at nearest the middle); its look's note."""
    from notes.recolour import nearest
    A, B = _cover_looks(nt, nk)
    n, s = len(mid), len(A)
    pm = mid[:, None] + A[None] * vt[:, None, 0] + B[None] * vk[:, None, 0]
    pk = key[:, None] + A[None] * vt[:, None, 1] + B[None] * vk[:, None, 1]
    got = nearest(at, pm.ravel(), pk.ravel(), own=False, kept=kept).reshape(n, s)
    if s == 1:
        return got[:, 0]
    col = now[got]  # (colours are small whole numbers: counted per note by bincount)
    K = int(col.max()) + 1
    rows = np.arange(n)[:, None] * K
    count = np.bincount((rows + col).ravel(), minlength=n * K).reshape(n, K)
    first = np.zeros((n, K), np.int64)
    for j in range(s - 1, -1, -1):  # (each colour's first look, nearest the middle: written last)
        first[np.arange(n), col[:, j]] = j
    best = np.argmax(count * (s + 1) - first, 1)  # (the most common; a tie: the one looked at nearest the middle)
    return got[np.arange(n), first[np.arange(n), best]]


def liquify_from(at, rc, box, own, colours=None):
    """(the notes moved (their places in at), middle in ticks, key) each one's colour comes from under rc's Liquify
    (stir_from's way: the edge choices alike). Notes where nothing was pushed are left out."""
    lq = full(clean_liquify(rc.get("liquify")))
    a = map_of(lq)
    w = _where(at, rc, box, own, colours=colours)
    idx = np.flatnonzero(touched(a, *to_map(w["x"], w["y"], span_of(lq))))
    mid, key = _fetched(w, a, w["x"][idx], w["y"][idx], rc, box, own)
    return idx, mid, key


def _taken(now, idx, src, pals, rc):
    """The colours notes idx take from the notes src found: theirs, or with 2+ colour sets the same place in the
    note's own block of channels (Stir's "stay"; a colour its block lacks: the found one itself)."""
    if len(pals) < 2:
        return now[src]
    from notes.recolour import MOST
    per = rc.get("most", MOST)
    pool = np.concatenate(pals)
    o = np.argsort(pool, kind="stable")
    want = now[idx] // per * per + now[src] % per
    i = o[np.clip(np.searchsorted(pool[o], want), 0, len(pool) - 1)]
    return np.where(pool[i] == want, want, now[src])


def _apart(notes, low, new, pals, pick):
    """new with two notes on a key sounding together kept apart (recolour.kept_apart), each colour set on its own
    with 2+ sets (a note's set: the one its new colour is in). new itself may be changed."""
    from notes.recolour import kept_apart
    if len(pals) < 2:
        return kept_apart(notes, low, new, pals[0], pick) if len(pals[0]) >= 2 else new
    pool = np.concatenate(pals)
    set_of = np.repeat(np.arange(len(pals)), [len(p) for p in pals])
    o = np.argsort(pool, kind="stable")
    mine = set_of[o[np.clip(np.searchsorted(pool[o], new), 0, len(pool) - 1)]]
    for s, p in enumerate(pals):
        m = np.flatnonzero(mine == s)
        if len(p) >= 2 and len(m):
            new[m] = kept_apart(notes[m], low[m], new[m], p, pick[m])
    return new


def shown_places(notes, at, rc):
    """The places the map is brushed on and fetches from: where each note is SHOWN (flipped back only with the
    shape's flip, as the brush's spot is: roll_liquify.liquify_unit), not where a gate flip had it before (at): a gate
    flipped shape bent on the other side of each key row, user 2026-11-07. rc["_gate"]: recolour sets it."""
    if not rc.get("_gate"):
        return at
    from notes.recolour import box_of, places_of
    return places_of(notes, rc, False, box_of(notes))


def liquified(notes, at, low, now, rc, which, pals, pick, box, own, laps=None):
    """The colours now after rc's Liquify: each note takes the colour of the note found where the map fetches from
    (recolour.stirred's way: colour sets "stay", Per chord / run by the group's first note, two notes on a key kept
    apart), its round too (laps: recolour.laps_of). Hidden (eye off) or none: now as it is. Live does the same for a
    brush stroke, only where it changed: keep both in step."""
    from notes.recolour import carry, leaders, nearest
    lq = clean_liquify(rc.get("liquify"))
    if not lq or not lq.get("map") or lq.get("on") is False or not len(at) or not lq.get("strength", 100.0):
        return now
    lv = _LIVE_KEPT.get("live")
    lv = lv() if lv is not None and laps is None else None  # (Live keeps no rounds: carried ones worked out whole)
    if lv is not None:  # (the brush stroke just let go already worked these colours out: Live == liquified)
        got = lv.done_colours(notes, at, now, rc, box, own, map_of(full(lq)))
        if got is not None:
            return got
    shown = shown_places(notes, at, rc)
    idx, mid, key = liquify_from(shown, rc, box, own, now)
    if not len(idx):
        return now
    # (own=False: the spots are only some notes'; a spot that didn't move finds its own note anyway, the nearest)
    src = nearest(shown, mid, key, own=False)
    new = np.array(now)
    new[idx] = _taken(now, idx, src, pals, rc)
    lead = leaders(at, rc["by"]) if rc.get("by") else None
    if lead is not None:
        new = new[lead]
    carry(laps, src, idx, lead)
    return _apart(notes, low, new, pals, pick)


_LIVE_KEPT = {}  # "got": ((weak notes, at, now), _live_key, Live._parts) of the last shape brushed


def _forget_live(_ref=None):
    _LIVE_KEPT.clear()


def _live_key(rc, box, own, grid):
    """What Live's kept parts depend on besides the notes: the rule (the map itself left out) and the boxes."""
    lq = {k: v for k, v in (rc.get("liquify") or {}).items() if k not in ("map", "frozen", "grid")}
    rest = {k: v for k, v in rc.items() if k != "liquify"}
    return json.dumps([rest, lq], sort_keys=True, default=repr), repr(box), repr(own), tuple(grid)


class Live:
    """A brush stroke's colours while dragging, exactly as liquified makes them, worked out only where the map
    changed (the brush shows the real result, like image editors; user 2026-11-07: a bent picture of the piano roll
    looked nothing like it). Every note's colour before kept_apart is kept: a change of the map changes the notes in
    the grid cells around it, then their Per chord / run groups, then whole bursts of notes sounding together on a
    key (kept_apart looks at nothing else), so the colours given are the very ones the whole shape gets."""

    def __init__(self, notes, at, low, now, rc, which, pals, pick, box, own, a, exact=None):
        self.notes, self.at, self.low, self.now, self.rc = notes, at, low, now, rc
        self.pals, self.pick, self.box, self.own = pals, pick, box, own
        # (what doesn't change while brushing: kept for the shape's next stroke too (its notes before the Liquify are
        # kept by recolour.before_liquify: the very same arrays), stage F: a press on 7.7 M notes took ~1 s)
        key = _live_key(rc, box, own, a.shape[:2])
        got = _LIVE_KEPT.get("got")
        if got is None or any(r() is not x for r, x in zip(got[0], (notes, at, now))) or got[1] != key:
            got = None
            parts = self._parts(notes, at, now, rc, box, own, a.shape[:2])
            try:
                _LIVE_KEPT["got"] = (tuple(weakref.ref(x, _forget_live) for x in (notes, at, now)), key, parts)
            except TypeError:  # (a view of another array can't be linked weakly: not kept)
                _LIVE_KEPT.clear()
        else:
            parts = got[2]
        self.__dict__.update(parts)
        # (a Warp drag: warp.solver of the grid now, its spots fetched exactly like warp.warped's; its own copy of w:
        # the kept parts' is shared with the next stroke)
        self.w = dict(self.w, exact=exact)
        self.at_in, self.key = at, key
        self.out = np.array(now)  # (every note's colour as brushed so far: liquified's very result; let go uses it)
        _LIVE_KEPT["live"] = weakref.ref(self)
        self.a = np.zeros_like(a)
        self.F = np.array(now)  # (each note's colour taken, before groups / kept_apart)
        self.P = self.F if self.lead is None else self.F[self.lead]
        self.ever = np.zeros(len(self.at), bool)
        self.zero = True
        self.update(a, exact)

    @classmethod
    def _parts(cls, notes, at, now, rc, box, own, grid):
        """Live's parts that stay the same while brushing (and from stroke to stroke on the same notes)."""
        from notes.recolour import _sorted_notes, leaders
        p = {}
        groups_at, at = at, shown_places(notes, at, rc)  # (groups by the places, the map by where notes show)
        p["at"] = at
        p["w"] = _where(at, rc, box, own, colours=now)
        lq = p["w"]["lq"]
        p["off"] = lq.get("on") is False or not lq.get("strength") or not len(at)
        # (made once, every piece side by side in memory: nearest's compiled search took a copy of a key column
        # at every update, ~4 ms at 3 M notes)
        p["kept"] = tuple(np.ascontiguousarray(v) if isinstance(v, np.ndarray) else v
                          for v in _sorted_notes(at)) if len(at) else None
        lead = p["lead"] = leaders(groups_at, rc["by"]) if rc.get("by") and len(at) else None
        if lead is not None:
            p["by_lead"] = np.argsort(lead, kind="stable")
            p["lead_sorted"] = lead[p["by_lead"]]
        gy, gx = grid
        cx = np.minimum((np.clip(p["w"]["x"], 0.0, 1.0) * (gx - 1)).astype(np.int64), gx - 2)
        cy = np.minimum((np.clip(p["w"]["y"], 0.0, 1.0) * (gy - 1)).astype(np.int64), gy - 2)
        from files.speed import key_sort
        p["cell_of"] = cy * (gx - 1) + cx  # (each note's cell, as touched finds it)
        p["every_note"] = np.arange(len(cy), dtype=np.int64)  # (every note's place, C7: one list kept)
        p["cells"] = cells = key_sort(cy, cx) if len(cy) else np.zeros(0, np.int64)
        p["cx"] = cx[cells]
        p["row_at"] = np.searchsorted(cy[cells], np.arange(gy))  # (each cell row's first place in cells)
        p["bursts"] = cls._bursts(notes)
        p["grid"] = (gy, gx)
        # (nothing pushed = liquified leaves the colours as they are, kept_apart and groups too: once something is,
        # the notes whose group's colour differs change everywhere)
        p["base"] = np.flatnonzero(now[lead] != now) if lead is not None else np.zeros(0, np.int64)
        return p

    @staticmethod
    def _spread(first, size):
        """The places first[i] .. first[i] + size[i] - 1 of every i, one after another."""
        return np.repeat(first - np.cumsum(size) + size, size) + np.arange(int(size.sum()))

    @staticmethod
    def _bursts(notes):
        """(each note's burst, each burst's first place, its size, notes in key / start order) as kept_apart finds
        them (notes on a key each overlapping one before), or None when no two notes overlap."""
        from files.speed import key_sort
        if len(notes) < 2:
            return None
        s, e, k = (np.asarray(notes[:, c], np.int64) for c in (0, 1, 2))
        o = key_sort(k, s)
        ks, ss, es = k[o], s[o], e[o]
        reach = np.maximum.accumulate(es + ks * (1 << 40))
        over = np.zeros(len(o), bool)
        over[1:] = (ss[1:] + ks[1:] * (1 << 40) < reach[:-1]) & (ks[1:] == ks[:-1])
        if not over.any():
            return None
        burst = np.cumsum(~over) - 1
        of = np.empty(len(o), np.int64)
        of[o] = burst
        return of, np.flatnonzero(~over), np.bincount(burst), o

    def update(self, a, exact=None):
        """The map is a now: (notes whose colour may have changed, their colours) (before their round / turn), or
        None when nothing changed. exact: a Warp drag's warp.solver for this map."""
        self.w["exact"] = exact
        got = self._update(a)
        if got is not None:
            self.out[got[0]] = got[1]
        return got

    def done_colours(self, notes, at, now, rc, box, own, a):
        """liquified's result for these very notes and map when this stroke already has it (out), else None: a let go
        on 7.7 M notes worked the whole Liquify out again (~0.4 s), stage F."""
        if (self.off or self.notes is not notes or self.at_in is not at or self.now is not now
                or a.shape != self.a.shape or self.key != _live_key(rc, box, own, a.shape[:2])
                or not np.array_equal(a, self.a)):
            return None
        return self.out.copy()

    def _update(self, a):
        if self.off:
            self.a = a
            return None
        diff = (a != self.a).any(2)
        self.a = a
        if not diff.any():
            return None
        A = self._changed(a, diff)
        zero = not a.any()
        if zero:  # (nothing pushed any more: every note as it was before the Liquify)
            if self.zero:
                return None
            self.zero = True
            B = np.flatnonzero(self.ever)
            self.ever[:] = False
            return B, self.now[B]
        if self.zero:
            self.zero = False
            A = np.union1d(A, self.base)
        if not len(A):
            return None
        B = A
        if self.bursts is not None:  # (every note of the bursts they're in: kept_apart's colours there change)
            of, first, size, o = self.bursts
            mark = np.zeros(len(first), bool)  # (np.unique(of[A]) without its sort, C7)
            mark[of[A]] = True
            b = np.flatnonzero(mark)
            B = o[self._spread(first[b], size[b])]
        if self.bursts is None:  # (no two notes overlap on a key: kept_apart leaves every colour as it is, C7)
            new = self.P[B]
        else:
            new = _apart(self.notes[B], self.low[B], self.P[B].copy(), self.pals, self.pick[B])
        self.ever[B] = True
        return B, new

    def _changed(self, a, diff):
        """The notes whose colour before kept_apart changed now that the map is a (changed at diff's grid points);
        F and P brought up to date."""
        gy, gx = self.grid
        none = np.zeros(0, np.int64)
        rows, cols = np.flatnonzero(diff.any(1)), np.flatnonzero(diff.any(0))
        # (a cell looks at its 4 corners: the cells around every changed grid point. Changes in separate places (a
        # mirrored Warp drag's twins) = a box round each, not one round them all)
        boxes = [(r0, r1, c0, c1) for r0, r1 in _runs(rows) for c0, c1 in _runs(cols)
                 if diff[r0:r1 + 1, c0:c1 + 1].any()]
        spans = [(max(r0 - 1, 0), min(r1, gy - 2), max(c0 - 1, 0), min(c1, gx - 2)) for r0, r1, c0, c1 in boxes]
        reach = np.zeros((gy - 1, gx - 1), bool)
        for j0, j1, i0, i1 in spans:
            reach[j0:j1 + 1, i0:i1 + 1] = True
        every = bool(reach.all())  # (C7: every cell (a 4-way mirrored Warp drag): the notes as they are, no lists)
        if every:
            D = self.every_note
        else:
            parts = []
            for j0, j1, i0, i1 in spans:
                for j in range(j0, j1 + 1):
                    lo, hi = int(self.row_at[j]), int(self.row_at[j + 1]) if j + 1 < gy else len(self.cells)
                    c = self.cx[lo:hi]
                    parts.append(self.cells[lo + int(np.searchsorted(c, i0)):
                                            lo + int(np.searchsorted(c, i1, "right"))])
            D = np.concatenate(parts) if parts else none
            if len(boxes) > 1:  # (boxes reaching into each other: a note once; marked, then read in order =
                # np.unique's result without its sort, C7)
                mark = np.zeros(len(self.at), bool)
                mark[D] = True
                D = np.flatnonzero(mark)
        if not len(D):
            return none
        X, Y = (self.w["x"], self.w["y"]) if every else (self.w["x"][D], self.w["y"][D])
        if a.shape[:2] == (gy, gx):  # (C7: each note's cell kept from the start (_parts): touched's sums, no gathers)
            nz = (a != 0).any(2)
            cell = (nz[:-1, :-1] | nz[1:, :-1] | nz[:-1, 1:] | nz[1:, 1:]).ravel()
            hot = cell[self.cell_of if every else self.cell_of[D]]
        else:
            hot = touched(a, X, Y)
        if hot.all():  # (every note in a pushed cell: no colour kept as it was)
            src = found(self.w, a, self.at, D, X, Y, self.rc, self.box, self.own, self.now, self.kept)
            got = _taken(self.now, D, src, self.pals, self.rc)
        else:
            got = self.now[D]  # (a copy: D is a list of places)
            if hot.any():
                H = D[hot]
                src = found(self.w, a, self.at, H, X[hot], Y[hot], self.rc, self.box, self.own, self.now, self.kept)
                got[hot] = _taken(self.now, H, src, self.pals, self.rc)
        if every:
            D = np.flatnonzero(got != self.F)
            if not len(D):
                return none
            self.F[D] = got[D]
        else:
            moved = got != self.F[D]
            D = D[moved]
            if not len(D):
                return none
            self.F[D] = got[moved]
        if self.lead is None:
            return D
        heads = D[self.lead[D] == D]  # (a group's colour is its first note's: only first notes changing count)
        if not len(heads):
            return none
        lo = np.searchsorted(self.lead_sorted, heads)
        hi = np.searchsorted(self.lead_sorted, heads, "right")
        A = self.by_lead[self._spread(lo, hi - lo)]
        self.P[A] = self.F[self.lead[A]]
        return A


def blend(x, y, f):
    """Add between's step f (0 = x's end, 1 = y's) of two Liquifys (either may be None = nothing pushed: the other
    fades in / out): the maps blended spot by spot (on the nearer end's grid), strength blended, the rest the nearer
    end's. None when nothing is left pushed."""
    x, y = clean_liquify(x), clean_liquify(y)
    if not x and not y:
        return None
    near = (x if f < 0.5 else y) or x or y
    if x and y and (x.get("moves") == "shape") != (y.get("moves") == "shape"):
        # (a Shape map says where points GO, a Colours one where colours come FROM: never mixed. The end of the
        # other kind counts as nothing pushed, so its bends fade out; hunt 2026-11-17)
        if near is x:
            y = None
        else:
            x = None
    if x and y and span_of(x) != span_of(y):  # (maps grown differently past the box: the nearer end's, whole)
        return near
    g = near["grid"]
    gx, gy = g
    jj, ii = np.mgrid[0:gy, 0:gx]
    u, v = ii / (gx - 1), jj / (gy - 1)

    def on_grid(d):
        if not d:
            return np.zeros((gy, gx, 2))
        a = map_of(d)
        return a if d["grid"] == g else sample(a, u, v)
    # (a hidden or 0 % end bends nothing you see: it counts as unpushed, else its bends showed up halfway; hunt
    # 2026-11-09. Both such ends: blended as they are, the steps hidden / 0 % too)
    def shows(d):
        return d if d and d.get("on") is not False and d.get("strength", DEFAULTS["strength"]) != 0 else None
    xs, ys = shows(x), shows(y)
    if xs or ys:
        x, y = xs, ys
    a = on_grid(x) * (1 - f) + on_grid(y) * f
    sx = (x or y).get("strength", DEFAULTS["strength"])
    sy = (y or x).get("strength", DEFAULTS["strength"])
    # (the freeze mask: the nearer end's, on its own grid = the one used here)
    return with_arrays(dict(near, strength=sx + (sy - sx) * f), a, mask_of(near))


# ---- brushes ----

def weight(q, density):
    """Brush strength at q = distance / radius (0 middle, 1 rim): full up to the hard part (density %), then fading
    smoothly to 0 at the rim; nothing past it."""
    hard = min(max(density / 100.0, 0.0), 1.0)
    q = np.asarray(q, np.float64)
    if hard >= 1.0:
        return (q <= 1.0).astype(np.float64)
    f = np.clip((q - hard) / (1.0 - hard), 0.0, 1.0)
    return np.where(q <= 1.0, 1.0 - f * f * (3 - 2 * f), 0.0)


def _reach(shape, c, r, J):
    """The grid points a brush of radius r px at c (colour box units) can touch: (rows slice, cols slice), or None."""
    gy, gx = shape[:2]
    ex = r * float(np.hypot(J[0][0], J[0][1]))
    ey = r * float(np.hypot(J[1][0], J[1][1]))
    i0, i1 = int(np.floor((c[0] - ex) * (gx - 1))), int(np.ceil((c[0] + ex) * (gx - 1)))
    j0, j1 = int(np.floor((c[1] - ey) * (gy - 1))), int(np.ceil((c[1] + ey) * (gy - 1)))
    i0, j0 = max(i0, 0), max(j0, 0)
    i1, j1 = min(i1, gx - 1), min(j1, gy - 1)
    if i0 > i1 or j0 > j1:
        return None
    return slice(j0, j1 + 1), slice(i0, i1 + 1)


def _under(shape, c, r, J, density):
    """The grid points under a brush of radius r px at c (colour box units) on a grid of shape (up, across, ...):
    (rows slice, cols slice, brush strength there, their spots x / y, box units from the middle x / y, screen px from
    the middle x / y), or None (none under it)."""
    gy, gx = shape[:2]
    got = _reach(shape, c, r, J)
    if got is None:
        return None
    rs, cs = got
    jj, ii = np.mgrid[rs, cs]
    px, py = ii / (gx - 1), jj / (gy - 1)
    ux, uy = px - c[0], py - c[1]
    try:
        Ji = np.linalg.inv(J)
    except np.linalg.LinAlgError:
        return None
    sx, sy = Ji[0, 0] * ux + Ji[0, 1] * uy, Ji[1, 0] * ux + Ji[1, 1] * uy
    w = weight(np.hypot(sx, sy) / max(r, 1e-9), density)
    return rs, cs, w, px, py, ux, uy, sx, sy


def mask_dab(m, c, r, J, pressure=50.0, density=50.0, freeze=True):
    """The freeze mask m (up, across) 0..1 after one Freeze (freeze) / Thaw dab: a new array. Pressure = how much a
    dab adds / takes away (100 % = all at once), like painting the editors' mask."""
    m = np.array(m, np.float64)
    got = _under(m.shape, c, r, np.asarray(J, np.float64), density)
    if got is None:
        return m
    rs, cs, w = got[:3]
    k = w * min(max(pressure / 100.0, 0.0), 1.0)
    old = m[rs, cs]
    m[rs, cs] = old + (1.0 - old) * k if freeze else old * (1.0 - k)
    return m


def dab(a, tool, c, r, J, pressure=50.0, density=50.0, vec=(0.0, 0.0), turn=1, frozen=None):
    """The map a after one brush dab (a new array; a itself never changed). tool: TOOLS. c = the brush middle in
    colour box units, r = its radius in screen px, J = [[dx/dpx, dx/dpy], [dy/dpx, dy/dpy]] (colour box units per
    screen pixel there), pressure / density in %, vec = the mouse's move in px since the last dab (Push), turn = +1 /
    -1 (Twirl's way round: +1 turns the colours anticlockwise on screen), frozen = the freeze mask (up, across) 0..1
    or None (frozen spots move less, fully frozen not at all)."""
    a = np.array(a, np.float64)
    J = np.asarray(J, np.float64)
    got = _under(a.shape, c, r, J, density)
    if got is None:
        return a
    rs, cs, w, px, py, ux, uy, sx, sy = got
    if frozen is not None:
        w = w * (1.0 - np.clip(frozen[rs, cs], 0.0, 1.0))
    if not w.any():
        return a
    p = pressure / 100.0
    old = a[rs, cs]
    if tool == "restore":
        a[rs, cs] = old * (1.0 - (w * min(0.25 * p, 1.0))[..., None])
        return a
    if tool == "smooth":
        pad = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
        blur = sum(pad[rs.start + dj:rs.stop + dj, cs.start + di:cs.stop + di] for dj in (0, 1, 2) for di in (0, 1, 2)) / 9
        a[rs, cs] = old + (blur - old) * (w * min(0.5 * p, 1.0))[..., None]
        return a
    if tool == "push":
        vx = J[0, 0] * vec[0] + J[0, 1] * vec[1]
        vy = J[1, 0] * vec[0] + J[1, 1] * vec[1]
        k = w * min(p * 1.0, 1.0)
        qx, qy = px - k * vx, py - k * vy
    elif tool == "twirl":
        ang = turn * w * 0.12 * p  # (radians a dab, most in the middle)
        ca, sa = np.cos(ang), np.sin(ang)
        rx, ry = sx * ca - sy * sa, sx * sa + sy * ca  # (turned on screen, so it stays round)
        qx = c[0] + J[0, 0] * rx + J[0, 1] * ry
        qy = c[1] + J[1, 0] * rx + J[1, 1] * ry
    elif tool in ("pinch", "bulge"):
        f = w * 0.06 * p
        f = 1.0 - f if tool == "bulge" else 1.0 + f  # (bulge: the middle's colours swell out = each spot looks inward)
        qx, qy = c[0] + ux * f, c[1] + uy * f
    else:
        return a
    d = sample(a, qx, qy)  # (the old map at the moved spot: a isn't changed until below)
    a[rs, cs, 0] = qx + d[..., 0] - px
    a[rs, cs, 1] = qy + d[..., 1] - py
    return a


# ---- the Shape brush: a "where each point goes" map (PLAN.md Liquify Shape rebuild, user 2026-11-17: "what liquify
# does in common image editors reliably"). Grid point p shows at p + s x F(p) (s = Strength); a dab moves the points
# that are UNDER the brush now (where they show), like an image editor's mesh. Nothing is searched for: the outline's
# points just look up where they go (sample_fwd), so the same strokes always give the same shape, and a dab never
# moves a point outside its circle. Between grid points: blended (a cell's inside follows its 4 corners).

FOLD_SAFE = 0.5  # (a Push dab is cut into steps moving a point at most this much of the brush's slope: never a fold)
MOST_SUBSTEPS = 32


def sample_fwd(a, x, y):
    """A "where each point goes" map at spots (x, y) of its span (0..1), blended between grid points; past its span
    as its edge (the outline may sit a little past the notes' box: spam ends short of the line). The edge itself
    never moves: a stroke reaching it grows the map first (roll_liquify.liquify_grow), so past it nothing moves."""
    return np.asarray(sample(a, np.asarray(x, np.float64), np.asarray(y, np.float64)), np.float64)


def _seg_dist(ax, ay, bx, by):
    """Distance from (0, 0) to each segment (ax, ay) - (bx, by)."""
    ex, ey = bx - ax, by - ay
    ee = ex * ex + ey * ey
    t = np.clip(-(ax * ex + ay * ey) / np.where(ee > 1e-300, ee, 1.0), 0.0, 1.0)
    return np.hypot(ax + t * ex, ay + t * ey)


def long_lines(a, J, r, strength=100.0, scale=None):
    """The grid lines a push stretched longer than half a brush of radius r px on screen (at least one end moved):
    (rows a, cols a, rows b, cols b). Restore / Smooth count a point whose such line runs under the brush (a long
    push stretches a cell into a spike with no point in its middle; user 2026-11-17). Found once a stroke
    (roll_liquify): Restore and Smooth only make lines shorter."""
    gy, gx = a.shape[:2]
    J = np.asarray(J, np.float64)
    try:
        Ji = np.linalg.inv(J)
    except np.linalg.LinAlgError:
        z = np.zeros(0, np.int64)
        return z, z, z, z
    S = np.ones(2) if scale is None else np.asarray(scale, np.float64)
    s = strength / 100.0 if strength > 1e-6 else 1.0
    X = np.arange(gx)[None, :] / (gx - 1) + s * a[..., 0] / S[0]
    Y = np.arange(gy)[:, None] / (gy - 1) + s * a[..., 1] / S[1]
    SX, SY = Ji[0, 0] * X + Ji[0, 1] * Y, Ji[1, 0] * X + Ji[1, 1] * Y
    moved = (a[..., 0] != 0) | (a[..., 1] != 0)
    out = [[], [], [], []]
    for sa, sb, dj, di in (((slice(None), slice(None, -1)), (slice(None), slice(1, None)), 0, 1),
                           ((slice(None, -1), slice(None)), (slice(1, None), slice(None)), 1, 0)):
        dx, dy = SX[sb] - SX[sa], SY[sb] - SY[sa]
        jj, ii = np.nonzero((dx * dx + dy * dy > (0.5 * r) ** 2) & (moved[sa] | moved[sb]))
        for k, v in enumerate((jj, ii, jj + dj, ii + di)):
            out[k].append(v)
    return tuple(np.concatenate(v).astype(np.int64) for v in out)


def _fwd_points(a, c, r, J, Ji, s, S, more=(0.0, 0.0), lines=None):
    """The grid points a brush of radius r px at c may touch now: (rows, cols, how far each is from the brush's
    middle in screen px). Only where they SHOW counts: the points drawn near (not moved) and the moved points showing
    near (a far push no longer makes every dab look at the whole grid, user 2026-11-17 "restore brush is
    stuttery"). more = map units a Push may still carry them. lines = long_lines' (Restore / Smooth): a point also
    counts by its stretched grid lines running under the brush."""
    gy, gx = a.shape[:2]
    hx, hy = 1.0 / (gx - 1), 1.0 / (gy - 1)
    ex = r * float(np.hypot(J[0, 0], J[0, 1])) + abs(more[0])
    ey = r * float(np.hypot(J[1, 0], J[1, 1])) + abs(more[1])
    i0, i1 = max(int(np.floor((c[0] - ex) * (gx - 1))), 0), min(int(np.ceil((c[0] + ex) * (gx - 1))), gx - 1)
    j0, j1 = max(int(np.floor((c[1] - ey) * (gy - 1))), 0), min(int(np.ceil((c[1] + ey) * (gy - 1))), gy - 1)
    base = (np.arange(j0, j1 + 1)[:, None] * gx + np.arange(i0, i1 + 1)[None, :]).ravel() \
        if i0 <= i1 and j0 <= j1 else np.zeros(0, np.int64)
    moved = np.flatnonzero((a[..., 0] != 0) | (a[..., 1] != 0))
    mj, mi = np.divmod(moved, gx)

    def screen(j, i):  # (where they show, in screen px from the brush's middle)
        ux = i * hx + s * a[j, i, 0] / S[0] - c[0]
        uy = j * hy + s * a[j, i, 1] / S[1] - c[1]
        return Ji[0, 0] * ux + Ji[0, 1] * uy, Ji[1, 0] * ux + Ji[1, 1] * uy
    reach = r + float(np.hypot(*(Ji @ np.asarray(more, np.float64))))
    if len(moved):
        mx, my = screen(mj, mi)
        moved = moved[np.hypot(mx, my) <= reach]
    idx = np.union1d(base, moved)
    j, i = np.divmod(idx, gx)
    dx, dy = screen(j, i)
    d = np.hypot(dx, dy)
    if lines is not None and len(lines[0]):
        ja, ia, jb, ib = lines
        ax, ay = screen(ja, ia)
        bx, by = screen(jb, ib)
        e = _seg_dist(ax, ay, bx, by)
        ok = e <= reach
        if ok.any():
            add = np.concatenate((ja[ok] * gx + ia[ok], jb[ok] * gx + ib[ok]))
            idx = np.concatenate((idx, add))
            d = np.concatenate((d, e[ok], e[ok]))
            o = np.lexsort((d, idx))  # (each point once, at its nearest: itself or one of its grid lines)
            idx, d = idx[o], d[o]
            first = np.r_[True, idx[1:] != idx[:-1]]
            idx, d = idx[first], d[first]
            j, i = np.divmod(idx, gx)
    keep = d <= reach
    return j[keep], i[keep], d[keep]


def dab_fwd(a, tool, c, r, J, pressure=50.0, density=50.0, vec=(0.0, 0.0), turn=1, frozen=None, strength=100.0,
            scale=None, copy=True, lines=None, info=None):
    """A Shape map a ("where each point goes") after one brush dab. The same brushes as dab (same arguments;
    strength = the Liquify's Strength %: the points move where they SHOW), forwards: Push carries the points under
    the brush along the mouse's move, Twirl turns them round the middle (+1 = anticlockwise on screen, as dab turns
    the colours), Pinch / Bulge draws them in / out, Restore takes them back toward where they were drawn (a point
    whose grid line runs under it too), Smooth evens the moves out; frozen points move less. c, J in the map's own
    units; a's moves in the colour box's units, scale = how many of those one map unit is (map_scale; None = the
    same). Only the points under the brush are written (the rest keep their very numbers). copy False: a itself
    changed (the brush's own copy: no whole-map copy each dab on a big grid) and returned. lines: the stroke's
    long_lines (Restore / Smooth; None = found now). info (a dict): info["changed"] = whether the map changed as
    saved (whole 1 / SCALE steps): nothing to make again otherwise (user 2026-11-17: Restore held in a busy shape's
    middle made every note again 19 times a second)."""
    if info is not None:
        info["changed"] = False
    a = np.array(a, np.float64) if copy else a
    S = np.ones(2) if scale is None else np.asarray(scale, np.float64)
    J = np.asarray(J, np.float64)
    try:
        Ji = np.linalg.inv(J)
    except np.linalg.LinAlgError:
        return a
    s = strength / 100.0 if strength > 1e-6 else 1.0  # (0 %: shows nothing; the brush works as at 100 %)
    if tool not in ("push", "restore", "smooth", "twirl", "pinch", "bulge"):
        return a
    p = pressure / 100.0
    n = 1
    if tool == "push":  # (a long move cut into steps, each re-measuring who is under the brush: a steep brush edge
        # never folds the shape over itself)
        hard = min(max(density / 100.0, 0.0), 1.0)
        if hard < 1.0:
            slope = 1.5 / ((1.0 - hard) * max(r, 1e-9))
            n = int(min(MOST_SUBSTEPS, max(1, math.ceil(math.hypot(*vec) * min(p, 1.0) * slope / FOLD_SAFE))))
    v = J @ np.asarray(vec, np.float64) * min(p, 1.0) if tool == "push" else np.zeros(2)  # (map units, whole dab)
    # (the points it may touch: found once a dab, the whole Push's move taken in)
    if tool in ("restore", "smooth") and lines is None:
        lines = long_lines(a, J, r, strength, S)
    j, i, d = _fwd_points(a, c, r, J, Ji, s, S, more=tuple(v), lines=lines if tool in ("restore", "smooth") else None)
    if not len(j):
        return a
    gy, gx = a.shape[:2]
    px, py = i / (gx - 1), j / (gy - 1)
    free = 1.0 if frozen is None else 1.0 - np.clip(frozen[j, i], 0.0, 1.0)
    before = a[j, i].copy()

    def done():
        if info is not None:
            info["changed"] = bool((np.rint(a[j, i] * SCALE) != np.rint(before * SCALE)).any())
        return a
    if tool in ("restore", "smooth"):
        w = weight(d / max(r, 1e-9), density) * free
        on = w > 0
        if not on.any():
            return a
        j, i, w, before = j[on], i[on], w[on], before[on]
        old = before
        if tool == "restore":  # (what's left under half a saved step = none: Restore really ends)
            new = old * (1.0 - (w * min(0.25 * p, 1.0))[:, None])
            new[np.abs(new) < 0.5 / SCALE] = 0.0
            a[j, i] = new
        else:  # (each point toward the mean of the 3 x 3 round it, the grid's edge repeated)
            blur = sum(a[np.clip(j + dj, 0, gy - 1), np.clip(i + di, 0, gx - 1)]
                       for dj in (-1, 0, 1) for di in (-1, 0, 1)) / 9
            a[j, i] = old + (blur - old) * (w * min(0.5 * p, 1.0))[:, None]
        return done()
    step = v / n
    for _ in range(n):
        old = a[j, i]
        X, Y = px + s * old[:, 0] / S[0], py + s * old[:, 1] / S[1]  # (where they show now, map units)
        ux, uy = X - c[0], Y - c[1]
        sx, sy = Ji[0, 0] * ux + Ji[0, 1] * uy, Ji[1, 0] * ux + Ji[1, 1] * uy
        w = weight(np.hypot(sx, sy) / max(r, 1e-9), density) * free
        on = w > 0  # (only the points under the brush rewritten: the rest keep their very numbers, no round-off)
        if not on.any():
            return done()
        if tool == "push":
            NX, NY = X + w * step[0], Y + w * step[1]
        elif tool == "twirl":  # (dab turns where the colours come FROM by +ang: they show turned by -ang)
            ang = -turn * w * 0.12 * p
            ca, sa = np.cos(ang), np.sin(ang)
            rx, ry = sx * ca - sy * sa, sx * sa + sy * ca  # (turned on screen, so it stays round)
            NX = c[0] + J[0, 0] * rx + J[0, 1] * ry
            NY = c[1] + J[1, 0] * rx + J[1, 1] * ry
        else:  # (dab's Pinch looks (1 + f) further out: the points come in by 1 / (1 + f); Bulge the other way)
            f = w * 0.06 * p
            g = 1.0 / (1.0 + f) if tool == "pinch" else 1.0 / np.maximum(1.0 - f, 0.05)
            NX, NY = c[0] + ux * g, c[1] + uy * g
        a[j[on], i[on], 0] = ((NX - px) / s * S[0])[on]
        a[j[on], i[on], 1] = ((NY - py) / s * S[1])[on]
    return done()


def mask_dab_fwd(m, a, c, r, J, pressure=50.0, density=50.0, freeze=True, strength=100.0, scale=None):
    """mask_dab for a Shape map a: the freeze mask m (one value per grid point: it belongs to the outline's own
    points) painted on the points SHOWING under the brush, so a pushed-out part is frozen where you see it and the
    tint follows it (hunt 2026-11-17: the mask stayed where things were drawn). A new array."""
    m = np.array(m, np.float64)
    J = np.asarray(J, np.float64)
    try:
        Ji = np.linalg.inv(J)
    except np.linalg.LinAlgError:
        return m
    S = np.ones(2) if scale is None else np.asarray(scale, np.float64)
    s = strength / 100.0 if strength > 1e-6 else 1.0
    j, i, d = _fwd_points(a, c, r, J, Ji, s, S)
    if not len(j):
        return m
    k = weight(d / max(r, 1e-9), density) * min(max(pressure / 100.0, 0.0), 1.0)
    old = m[j, i]
    m[j, i] = old + (1.0 - old) * k if freeze else old * (1.0 - k)
    return m


def shown_mask(m, a, u, v, strength=100.0, scale=None):
    """The freeze mask m of a Shape map a as it SHOWS at spots (u, v) of the map's own 0..1: each spot takes the
    mask of the point bent there (looked back through the map, shape_bend.LiquifyBend.from_map)."""
    from notes.shape_bend import LiquifyBend
    S = np.ones(2) if scale is None else np.asarray(scale, np.float64)
    s = strength / 100.0 if strength > 1e-6 else 1.0
    b = LiquifyBend.from_map(np.asarray(a, np.float64) / S * s)
    shape = np.shape(u)
    x, y = b._from(np.ravel(np.asarray(u, np.float64)), np.ravel(np.asarray(v, np.float64)))
    return sample(np.asarray(m, np.float64)[..., None], x, y)[..., 0].reshape(shape)


def straight(a, b, spacing):
    """Dab spots from a to b (screen px) every spacing px, a left out (Shift+click: a straight stroke)."""
    n = max(1, int(np.ceil(np.hypot(b[0] - a[0], b[1] - a[1]) / max(spacing, 1e-9))))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)]
