"""Areas of a custom shape (custom.py): the pieces its lines cut the drawing into (the outline-and-fill loops and
the fill lines). Each area can get a colour of its own (a channel group, like the outline's with "Outline") or be
left empty: sh["areas"] = [[u, v, colour]], a spot in the drawing's box (like the strokes' points, so it moves
with them) and what the area there gets: 0 = empty, 1 .. COLOURS = that colour.

Areas are found on a grid of MAP_CELLS x MAP_CELLS cells over the drawing: a sliver thinner than a cell may take
its neighbour's colour. Which notes are filled when nothing is coloured stays exact (custom.row_pieces)."""

import math

import numpy as np

MAP_CELLS = 1024
COLOURS = 15
REACH = 3  # cells: a fill line's loose ends go this much further, so one ending right on a line still splits
_maps = {}


def clean_areas(areas):
    """A saved list of areas -> [[u, v, colour]] (bad ones left out)."""
    out = []
    for a in areas if isinstance(areas, list) else ():
        try:
            u, v, c = float(a[0]), float(a[1]), int(a[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if 0 <= c <= COLOURS and math.isfinite(u) and math.isfinite(v):
            out.append([u, v, c])
    return out


def area_map(loops, cuts, key=None):
    """The AreaMap of these lines (closed loops and fill lines, (u, v) point lists), remembered by key."""
    got = _maps.get(key) if key is not None else None
    if got is None:
        got = AreaMap(loops, cuts)
        if key is not None:
            if len(_maps) > 20:
                _maps.clear()
            _maps[key] = got
    return got


class AreaMap:
    """Which area each spot of the drawing is in: cells on the lines are walls, the rest is cut into pieces that
    touch side by side (a line crossing a cell's corner still separates)."""

    def __init__(self, loops, cuts):
        paths = [np.asarray(p, float).reshape(-1, 2) for p in loops]
        paths = [p for p in paths if len(p)]
        cut_paths = [np.asarray(p, float).reshape(-1, 2) for p in cuts]
        cut_paths = [p for p in cut_paths if len(p)]
        every = np.concatenate(paths + cut_paths) if paths or cut_paths else np.zeros((1, 2))
        lo, hi = every.min(0), every.max(0)
        n = MAP_CELLS
        self.k = n / np.maximum(hi - lo, 1e-9)  # cells per u, per v
        self.lo = lo - (REACH + 2) / self.k
        self.w = self.h = n + 2 * (REACH + 2) + 1
        for i, p in enumerate(cut_paths):  # loose ends reach a little further
            if len(p) >= 2 and math.dist(p[0], p[-1]) > 1e-9:
                c = (p - self.lo) * self.k
                p = c.copy()
                for at, nxt in ((0, 1), (-1, -2)):
                    d = c[at] - c[nxt]
                    ln = math.hypot(*d)
                    if ln > 1e-12:
                        p[at] = c[at] + d / ln * REACH
                cut_paths[i] = p / self.k + self.lo
        wall = np.zeros((self.h, self.w), bool)
        segs = [np.stack([p[:-1], p[1:]], 1) for p in paths + cut_paths if len(p) >= 2]
        if segs:
            s = (np.concatenate(segs) - self.lo) * self.k  # (n, 2 ends, u / v) in cells
            a, b = s[:, 0], s[:, 1]
            m = np.ceil(np.abs(b - a).max(1) * 2).astype(np.int64) + 1  # steps of half a cell or less
            seg = np.repeat(np.arange(len(s)), m)
            t = (np.arange(int(m.sum())) - np.repeat(np.cumsum(m) - m, m)) / np.repeat(np.maximum(m - 1, 1), m)
            pts = a[seg] + (b[seg] - a[seg]) * t[:, None]
            cx = np.clip(np.floor(pts[:, 0]).astype(np.int64), 0, self.w - 1)
            cy = np.clip(np.floor(pts[:, 1]).astype(np.int64), 0, self.h - 1)
            wall[cy, cx] = True
        for p in paths + cut_paths:  # (a single spot)
            if len(p) == 1:
                c = np.floor((p[0] - self.lo) * self.k).astype(np.int64)
                wall[min(max(c[1], 0), self.h - 1), min(max(c[0], 0), self.w - 1)] = True
        self.labels, self.count, self.spots = self._label(~wall)

    def _label(self, free):
        """Every free cell's area number (walls: -1), how many areas, and one spot (u, v) inside each."""
        h, w = free.shape
        pad = np.zeros((h, w + 2), np.int8)
        pad[:, 1:-1] = free
        d = np.diff(pad, axis=1)
        rows, s = np.nonzero(d == 1)
        _, e = np.nonzero(d == -1)
        m = len(rows)
        if not m:
            return np.full((h, w), -1, np.int32), 0, np.zeros((0, 2))
        wide = w + 1
        ks, ke = rows * wide + s, rows * wide + e
        nxt = (rows + 1) * wide
        first = np.searchsorted(ke, nxt + s, "right")  # runs on the next row that overlap this one
        cnt = np.maximum(np.searchsorted(ks, nxt + e, "left") - first, 0)
        a = np.repeat(np.arange(m), cnt)
        b = np.repeat(first, cnt) + np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        lab = np.arange(m)
        while len(a):  # smallest run number of each group, passed along until nothing changes
            low = np.minimum(lab[a], lab[b])
            new = lab.copy()
            np.minimum.at(new, a, low)
            np.minimum.at(new, b, low)
            new = new[new]
            if np.array_equal(new, lab):
                break
            lab = new
        roots, area = np.unique(lab, return_inverse=True)
        area = area.reshape(-1).astype(np.int32)
        out = np.full((h, w), -1, np.int32)
        ln = e - s
        cols = np.repeat(s, ln) + np.arange(int(ln.sum())) - np.repeat(np.cumsum(ln) - ln, ln)
        out[np.repeat(rows, ln), cols] = np.repeat(area, ln)
        _, one = np.unique(area, return_index=True)  # (the first run of each area: its middle cell)
        spots = np.column_stack([(s[one] + e[one]) / 2, rows[one] + 0.5]) / self.k + self.lo
        return out, len(roots), spots

    def cell(self, u, v, off=-1):
        """The area number at each spot (u, v arrays), -1 on a wall, off off the map."""
        u, v = np.atleast_1d(np.asarray(u, float)), np.atleast_1d(np.asarray(v, float))
        cx = np.floor((u - self.lo[0]) * self.k[0]).astype(np.int64)
        cy = np.floor((v - self.lo[1]) * self.k[1]).astype(np.int64)
        ok = (cx >= 0) & (cx < self.w) & (cy >= 0) & (cy < self.h)
        out = np.full(u.shape, off, np.int32)
        out[ok] = self.labels[cy[ok], cx[ok]]
        return out

    def at(self, u, v):
        """The area number at each spot (u, v arrays); a spot on a wall takes a free cell next to it (-1: none)."""
        u, v = np.atleast_1d(np.asarray(u, float)), np.atleast_1d(np.asarray(v, float))
        fx, fy = (u - self.lo[0]) * self.k[0], (v - self.lo[1]) * self.k[1]
        cx, cy = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        out = np.full(u.shape, -1, np.int32)
        ok = (cx >= 0) & (cx < self.w) & (cy >= 0) & (cy < self.h)
        out[ok] = self.labels[cy[ok], cx[ok]]
        # on a wall: the nearest free cell, sideways first (a stretch of notes runs sideways)
        sx, sy = np.where(fx - cx < 0.5, -1, 1), np.where(fy - cy < 0.5, -1, 1)  # (the nearer side first)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (-2, 0), (0, 2), (0, -2), (1, 1), (-1, 1), (1, -1),
                       (-1, -1)):
            miss = out < 0
            if not miss.any():
                break
            x, y = cx[miss] + dx * sx[miss], cy[miss] + dy * sy[miss]
            good = (x >= 0) & (x < self.w) & (y >= 0) & (y < self.h)
            got = np.full(len(x), -1, np.int32)
            got[good] = self.labels[y[good], x[good]]
            out[np.flatnonzero(miss)] = got
        return out

    def outside(self):
        """The area around the drawing (touching the map's edge)."""
        return int(self.labels[0, 0])


def inside_loops(u, v, loops, union=False):
    """Which spots (u, v arrays) are inside the closed loops (even-odd; union: inside any of them)."""
    u, v = np.asarray(u, float), np.asarray(v, float)
    inside = np.zeros(u.shape, bool)
    count = np.zeros(u.shape, np.int64)
    for loop in loops:
        p = np.asarray(loop, float).reshape(-1, 2)
        if len(p) < 3:
            continue
        xa, ya, xb, yb = p[:-1, 0], p[:-1, 1], p[1:, 0], p[1:, 1]
        hit = np.zeros(u.shape, np.int64)
        step = max(1, 2_000_000 // max(1, u.size))
        for i in range(0, len(xa), step):
            sl = slice(i, i + step)
            a, b, c, d = xa[sl, None], ya[sl, None], xb[sl, None], yb[sl, None]
            cross = (b > v) != (d > v)
            x = a + (c - a) * (v - b) / np.where(d == b, 1, d - b)
            hit += (cross & (x > u)).sum(0)
        if union:
            inside |= hit % 2 == 1
        else:
            count += hit
    return inside if union else count % 2 == 1
