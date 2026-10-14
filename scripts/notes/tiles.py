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

import bisect
import json
import math

import numpy as np

from notes.custom import box_frame, clean_strokes, find_spans, inside_rows

LAYOUTS = ("grid", "brick")
FLIPS = ("none", "h", "v", "hv")
BY = ("diag", "cols", "rows", "checker", "one")
CIRCLE = [{"kind": "ellipse", "box": [0.0, 0.0, 1.0, 1.0]}]
TILES_DEFAULTS = {"of": {"name": "Circle", "strokes": CIRCLE}, "keys": 12, "length": 1.0, "gap_keys": 2,
                  "gap": 0.25, "layout": "grid", "flip": "none", "by": "diag", "colours": 4, "whole": False}
MOST_KEYS = 128
MOST_COLOURS = 15
MOST_TILES = 200000  # (past this many tiles the fill gives up: a tile of one key in a huge shape)
_spans = {}  # tiles_spans, remembered


def clean_tiles(c):
    """A saved sh["tiles"] -> a valid one, or None (off)."""
    if not isinstance(c, dict):
        return None
    out = dict(TILES_DEFAULTS)
    of = c.get("of")
    if isinstance(of, dict):
        strokes = clean_strokes(of.get("strokes"))
        if strokes:
            out["of"] = {"name": str(of.get("name", ""))[:100], "strokes": strokes}
    try:
        out["keys"] = max(1, min(MOST_KEYS, int(c.get("keys", out["keys"]))))
        out["gap_keys"] = max(0, min(MOST_KEYS, int(c.get("gap_keys", out["gap_keys"]))))
        for k in ("length", "gap"):
            x = float(c.get(k, out[k]))
            out[k] = max(1e-6 if k == "length" else 0.0, min(10 ** 4, x)) if math.isfinite(x) else out[k]
        out["colours"] = max(1, min(MOST_COLOURS, int(c.get("colours", out["colours"]))))
    except (TypeError, ValueError):
        return None
    for k, ok in (("layout", LAYOUTS), ("flip", FLIPS), ("by", BY)):
        if c.get(k) in ok:
            out[k] = c[k]
    out["whole"] = c.get("whole") is True
    at = c.get("at")
    if isinstance(at, (list, tuple)) and len(at) == 2:
        try:
            u, v = float(at[0]), float(at[1])
            if math.isfinite(u) and math.isfinite(v):
                out["at"] = [u, v]
        except (TypeError, ValueError):
            pass
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
           tuple(round(c, 9) for c in centre))
    got = _spans.get(key)
    if got is None:
        if len(_spans) > 60:
            _spans.clear()
        got = _spans[key] = find_tiles(sh, ppq, centre)
        got.flags.writeable = False
    return got


def find_tiles(sh, ppq, centre):
    cfg = sh["tiles"]
    none = np.zeros((0, 4), np.int64)
    base = inside_rows(sh, ppq)
    if not len(base):
        return none
    tw, th = cfg["length"] * ppq, cfg["keys"]  # (ticks, keys)
    sx, sy = tw + cfg["gap"] * ppq, th + cfg["gap_keys"]
    # the tile on the centre point: its box's left edge / bottom row (whole keys)
    x0 = centre[0] * ppq - tw / 2
    y0 = int(math.floor(centre[1] - th / 2 + 0.5))
    lo_t, hi_t = int(base[:, 0].min()), int(base[:, 1].max())
    lo_k, hi_k = int(base[:, 2].min()), int(base[:, 2].max())
    j0, j1 = math.floor((lo_k - y0 - th) / sy), math.ceil((hi_k - y0) / sy)
    i0, i1 = math.floor((lo_t - x0 - tw - sx) / sx), math.ceil((hi_t - x0) / sx)
    if (j1 - j0 + 1) * (i1 - i0 + 1) > MOST_TILES:
        return none
    patterns = {}
    pieces, ids = [], []
    tiles = []  # (i, j) of each tile
    for j in range(j0, j1 + 1):
        shift = sx / 2 if cfg["layout"] == "brick" and j % 2 else 0.0
        for i in range(i0, i1 + 1):
            fh = cfg["flip"] in ("h", "hv") and i % 2 == 1
            fv = (cfg["flip"] == "v" and i % 2 == 1) or (cfg["flip"] == "hv" and j % 2 == 1)
            pat = patterns.get((fh, fv))
            if pat is None:
                pat = patterns[(fh, fv)] = tile_pattern(sh, ppq, fh, fv)
            if not len(pat):
                continue
            dt = int(math.floor(x0 + shift + i * sx + 0.5))
            dk = y0 + j * sy
            if dt + pat[:, 1].max() <= lo_t or dt + pat[:, 0].min() >= hi_t or dk + th <= lo_k or dk > hi_k:
                continue
            pieces.append(pat + [dt, dt, dk])
            ids.append(np.full(len(pat), len(tiles), np.int64))
            tiles.append((i, j))
    if not pieces:
        return none
    spans = np.concatenate(pieces)
    tid = np.concatenate(ids)
    got, gid, cut = clip(spans, tid, base, len(tiles))
    if cfg["whole"]:
        keep = ~cut[gid]
        got, gid = got[keep], gid[keep]
    if not len(got):
        return none
    ij = np.asarray(tiles, np.int64)[gid]
    n = cfg["colours"]
    by = cfg["by"]
    colour = (ij[:, 0] + ij[:, 1] if by == "diag" else ij[:, 0] if by == "cols" else ij[:, 1] if by == "rows"
              else (ij[:, 0] + ij[:, 1]) & 1 if by == "checker" else np.zeros(len(ij), np.int64)) % n
    out = np.column_stack([got, colour]).astype(np.int64)
    return out[np.lexsort((out[:, 0], out[:, 2]))]


def clip(spans, tid, base, count):
    """The tiles' (start, end, key) stretches cut to the shape's inside: (pieces, each piece's tile, cut[tile] =
    some of that tile lay outside the shape)."""
    rows = {}
    for s, e, k in base.tolist():
        rows.setdefault(k, []).append((s, e))
    starts = {k: [a for a, _ in sorted(v)] for k, v in rows.items()}
    ends = {k: [b for _, b in sorted(v)] for k, v in rows.items()}
    out, oid = [], []
    cut = np.zeros(count, bool)
    for (s, e, k), t in zip(spans.tolist(), tid.tolist()):
        st, en = starts.get(k), ends.get(k)
        if not st:
            cut[t] = True
            continue
        n = bisect.bisect_right(en, s)  # (the first stretch of the row ending after s)
        inside = 0
        while n < len(st) and st[n] < e:
            a, b = max(s, st[n]), min(e, en[n])
            if b > a:
                out.append((a, b, k))
                oid.append(t)
                inside += b - a
            n += 1
        if inside < e - s:
            cut[t] = True
    return np.asarray(out, np.int64).reshape(-1, 3), np.asarray(oid, np.int64), cut
