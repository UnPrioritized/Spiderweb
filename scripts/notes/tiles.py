"""Inside fill "Tiles" (sh["tiles"]): another library shape repeated like wallpaper inside the shape, cut at its
outline, each tile a colour of its own by where it is (with Multi channel, like areas coloured by hand). Like
Nested (nested.py) the shape keeps its Fill / Spam (sh["fill"]) and its notes are made like areas' (custom
.coloured_spans).

Tiles lie level on the piano roll (notes always do), on whole keys; one tile sits on the centre point (the handle
shared with Nested: "at", u, v in the shape's box, so it moves with the shape; missing = the middle of the filled
part). Every tile is the same drawing, so its notes are made once (find_spans; once per flip) and copied to each
tile's spot.

sh["tiles"] = {"of": {"name", "strokes"} (the tile's drawing, filling its box), "keys" (a tile's height), "length"
(its length in beats), "gap_keys", "gap" (beats), "layout" ("grid" / "brick" = every other row moved half a tile),
"flip" ("none" / "h" every other one side to side / "v" every other one upside down / "hv" every other row upside
down and every other column side to side), "by" (tile colours: "diag" / "cols" / "rows" / "checker" / "one"),
"colours" (how many channels), "whole" (True = only tiles the outline doesn't cut), "at"}."""

import json
import math

import numpy as np

from notes.custom import box_frame, in_turns, clean_of_at, find_spans, inside_rows

LAYOUTS = ("grid", "brick")
FLIPS = ("none", "h", "v", "hv")
BY = ("diag", "cols", "rows", "checker", "one")
CIRCLE = [{"kind": "ellipse", "box": [0.0, 0.0, 1.0, 1.0]}]
TILES_DEFAULTS = {"of": {"name": "Circle", "strokes": CIRCLE}, "keys": 12, "length": 1.0, "gap_keys": 2,
                  "gap": 0.25, "layout": "grid", "flip": "none", "by": "diag", "colours": 4, "whole": False}
MOST_KEYS = 128
MOST_COLOURS = 15
MOST_BEATS = 10 ** 4  # (the longest tile length / gap kept)
MOST_TILES = 1000000  # (past this many tiles, or MOST_NOTES of their notes, the fill is left empty: the panel says so)
MOST_NOTES = 8000000
KEPT_BYTES = 300 * 2 ** 20  # (tiles_spans remembered: dropped past this much memory)
_spans = {}  # tiles_spans, remembered


def clean_tiles(c):
    """A saved sh["tiles"] -> a valid one, or None (off)."""
    if not isinstance(c, dict):
        return None
    out = dict(TILES_DEFAULTS)
    try:
        out["keys"] = max(1, min(MOST_KEYS, int(c.get("keys", out["keys"]))))
        out["gap_keys"] = max(0, min(MOST_KEYS, int(c.get("gap_keys", out["gap_keys"]))))
        for k in ("length", "gap"):
            x = float(c.get(k, out[k]))
            out[k] = max(1e-6 if k == "length" else 0.0, min(MOST_BEATS, x)) if math.isfinite(x) else out[k]
        out["colours"] = max(1, min(MOST_COLOURS, int(c.get("colours", out["colours"]))))
    except (TypeError, ValueError):
        return None
    for k, ok in (("layout", LAYOUTS), ("flip", FLIPS), ("by", BY)):
        if c.get(k) in ok:
            out[k] = c[k]
    out["whole"] = c.get("whole") is True
    clean_of_at(c, out)
    return out


def tile_pattern(sh, ppq, flip_h, flip_v):
    """One tile's (start, end, key) notes with its box's left edge at tick 0 and bottom row at key 0."""
    cfg = sh["tiles"]
    w, h = cfg["length"], cfg["keys"]
    tile = {k: v for k, v in sh.items() if k not in ("tiles", "nested", "areas", "text", "notes", "hz", "round")}
    tile.update(strokes=cfg["of"]["strokes"], pts=box_frame(0.0, 63.5, w, 63.5 + h))  # (rows 64 .. 64 + h - 1)
    got = np.asarray(find_spans(tile, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]
    got[:, 2] -= 64
    if flip_h:  # (mirrored inside its own box)
        wt = math.floor(w * ppq + 0.5)
        got[:, 0], got[:, 1] = wt - got[:, 1], wt - got[:, 0]
    if flip_v:
        got[:, 2] = h - 1 - got[:, 2]
    return got


def tiles_spans(sh, ppq, centre):
    """(start, end, key, colour) ticks of every stretch filled (like custom.area_spans). Remembered."""
    cfg = sh["tiles"]
    key = (json.dumps([sh["strokes"], sh["pts"], cfg, sh.get("round")]), bool(sh.get("union")), ppq,
           tuple(round(c, 9) for c in centre), bool(sh.get("_rounds")))
    got = _spans.get(key)
    if got is None:
        if len(_spans) > 60 or sum(a.nbytes for a in _spans.values()) > KEPT_BYTES:
            _spans.clear()
        got = _spans[key] = find_tiles(sh, ppq, centre)
        got.flags.writeable = False
    return got


def too_many(sh, ppq):
    """The Tiles fill is left empty: too many tiles (or their notes) for this shape (the panel says so)."""
    from notes.nested import centre_bp
    base = inside_rows(sh, ppq)
    return bool(len(base)) and tile_grid(sh, ppq, base, centre_bp(sh, ppq), count=True) is None


def tile_grid(sh, ppq, base, centre, count=False):
    """Every tile that could touch the inside's key rows: (i, j, their notes' (start, end, key), each note's
    tile), or None when they're more than MOST_TILES or their notes more than MOST_NOTES. count: only whether
    they're too many (None) or not (True), without making their notes."""
    cfg = sh["tiles"]
    tw, th = cfg["length"] * ppq, cfg["keys"]  # (ticks, keys)
    sx, sy = tw + cfg["gap"] * ppq, th + cfg["gap_keys"]
    x0 = centre[0] * ppq - tw / 2  # (the tile on the centre point: its box's left edge, bottom row on a whole key)
    y0 = int(math.floor(centre[1] - th / 2 + 0.5))
    keys = base[:, 2]
    lo_k, hi_k = int(keys.min()), int(keys.max())
    first = np.full(hi_k - lo_k + 1, np.inf)
    last = np.full(hi_k - lo_k + 1, -np.inf)
    np.minimum.at(first, keys - lo_k, base[:, 0])
    np.maximum.at(last, keys - lo_k, base[:, 1])
    brick = cfg["layout"] == "brick"
    i_all, j_all, total = [], [], 0
    for j in range(math.floor((lo_k - y0 - th) / sy), math.ceil((hi_k - y0) / sy) + 1):
        k0, k1 = max(lo_k, y0 + j * sy), min(hi_k, y0 + j * sy + th - 1)  # (the keys this row of tiles covers)
        if k0 > k1:
            continue
        a, b = first[k0 - lo_k:k1 - lo_k + 1].min(), last[k0 - lo_k:k1 - lo_k + 1].max()
        if not a < b:
            continue
        shift = sx / 2 if brick and j % 2 else 0.0
        i0, i1 = math.floor((a - x0 - shift - tw) / sx), math.ceil((b - x0 - shift) / sx)
        total += i1 - i0 + 1
        if total > MOST_TILES:
            return None
        i_all.append(np.arange(i0, i1 + 1, dtype=np.int64))
        j_all.append(np.full(i1 - i0 + 1, j, np.int64))
    if not i_all:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    ii, jj = np.concatenate(i_all), np.concatenate(j_all)
    shift = np.where(brick & (jj % 2 == 1), sx / 2, 0.0)
    dt = np.floor(x0 + shift + ii * sx + 0.5).astype(np.int64)
    dk = y0 + jj * sy
    odd_i, odd_j = ii % 2 == 1, jj % 2 == 1
    fh = odd_i & (cfg["flip"] in ("h", "hv"))
    fv = odd_i & (cfg["flip"] == "v") | odd_j & (cfg["flip"] == "hv")
    spans, tids, notes = [], [], 0
    for h in (False, True):
        for v in (False, True):
            at = np.flatnonzero((fh == h) & (fv == v))
            if not len(at):
                continue
            pat = tile_pattern(sh, ppq, h, v)
            notes += len(pat) * len(at)
            if notes > MOST_NOTES:
                return None
            if len(pat) and not count:
                off = np.column_stack([dt[at], dt[at], dk[at]])
                spans.append((off[:, None, :] + pat[None, :, :]).reshape(-1, 3))
                tids.append(np.repeat(at, len(pat)))
    if count:
        return True
    if not spans:
        return ii, jj, np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    return ii, jj, np.concatenate(spans), np.concatenate(tids)


def find_tiles(sh, ppq, centre):
    cfg = sh["tiles"]
    none = np.zeros((0, 4), np.int64)
    base = inside_rows(sh, ppq)
    if not len(base):
        return none
    grid = tile_grid(sh, ppq, base, centre)
    if grid is None:  # (too many: left empty, the panel says why)
        return none
    ii, jj, spans, tid = grid
    if not len(spans):
        return none
    got, gid, cut = clip(spans, tid, base, len(ii))
    if cfg["whole"]:
        keep = ~cut[gid]
        got, gid = got[keep], gid[keep]
    if not len(got):
        return none
    i, j = ii[gid], jj[gid]
    by = cfg["by"]
    turn = (i + j if by == "diag" else i if by == "cols" else j if by == "rows"
            else (i + j) & 1 if by == "checker" else np.zeros(len(i), np.int64))
    colour = in_turns(turn, cfg["colours"], sh.get("_rounds"))
    out = np.column_stack([got, colour]).astype(np.int64)
    return out[np.lexsort((out[:, 0], out[:, 2]))]


def clip(spans, tid, base, count):
    """The tiles' (start, end, key) stretches cut to the shape's inside (its stretches don't overlap on a key):
    (pieces, each piece's tile, cut[tile] = some of that tile lay outside the shape)."""
    lo = int(min(spans[:, 0].min(), base[:, 0].min()))
    big = int(max(spans[:, 1].max(), base[:, 1].max())) - lo + 1
    b = base[np.lexsort((base[:, 0], base[:, 2]))]
    b_start, b_end = b[:, 2] * big + (b[:, 0] - lo), b[:, 2] * big + (b[:, 1] - lo)
    first = np.searchsorted(b_end, spans[:, 2] * big + (spans[:, 0] - lo), "right")  # (the first ending after it starts)
    n = np.maximum(np.searchsorted(b_start, spans[:, 2] * big + (spans[:, 1] - lo), "left") - first, 0)
    which = np.repeat(np.arange(len(spans)), n)
    k = np.repeat(first, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    s, e = np.maximum(spans[which, 0], b[k, 0]), np.minimum(spans[which, 1], b[k, 1])
    ok = e > s
    pieces = np.column_stack([s[ok], e[ok], spans[which[ok], 2]]).astype(np.int64).reshape(-1, 3)
    inside = np.bincount(which[ok], (e - s)[ok], len(spans))
    cut = np.zeros(count, bool)
    cut[tid[inside < spans[:, 1] - spans[:, 0]]] = True
    return pieces, tid[which[ok]], cut

