"""The areas a custom shape's lines cut the drawing into, and how deep each one is: the fewest lines to cross to get
from it to outside the drawing (custom.fill_test). Fill / Spam fill an area when that's odd (lines ending on
other lines: a line splitting a filled area leaves both halves filled, user), or with Overlaps cancel out off
when it's 1 or more (everything closed in). With every line part of a closed loop, odd = the usual even-odd.

Worked out exactly on the lines, no grid: the drawing is cut into slabs at the height of every corner and every
crossing, each slab into pieces between the lines that run through it (no two cross inside a slab). Pieces that
touch across a slab's edge (not where a level line lies) are one area; two pieces side by side are one line
apart. A spot is looked up by its slab and how many lines lie left of it."""

import numpy as np

_cache = {}


def faces(paths, key=None):
    """Faces of these lines ((x, y) point lists), remembered by key."""
    got = _cache.get(key) if key is not None else None
    if got is None:
        got = Faces(paths)
        if key is not None:
            if len(_cache) > 50:
                _cache.clear()
            _cache[key] = got
    return got


def _segments(paths):
    parts = [np.asarray(p, float).reshape(-1, 2) for p in paths]
    parts = [p for p in parts if len(p) > 1]
    if not parts:
        return np.zeros((0, 4))
    seg = np.concatenate([np.column_stack([p[:-1], p[1:]]) for p in parts])
    return seg[(seg[:, 0] != seg[:, 2]) | (seg[:, 1] != seg[:, 3])]


def _runs(first, n):
    """For counts n starting at first: (which one, first + 0 .. n - 1) for all of them."""
    i = np.repeat(np.arange(len(n)), n)
    return i, np.repeat(first, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)


def _join(n, a, b):
    """Groups of 0 .. n - 1 joined by the pairs (a, b): each one's smallest member."""
    lab = np.arange(n)
    while True:  # each group's larger head hooked under the smaller one, then every member pointed at its head
        ra, rb = lab[a], lab[b]
        diff = ra != rb
        if not diff.any():
            return lab
        np.minimum.at(lab, np.maximum(ra[diff], rb[diff]), np.minimum(ra[diff], rb[diff]))
        while True:
            nxt = lab[lab]
            if np.array_equal(nxt, lab):
                break
            lab = nxt


class Faces:
    def __init__(self, paths):
        seg = _segments(paths)
        level = seg[:, 1] == seg[:, 3]
        h, s = seg[level], seg[~level]
        up = s[:, 1] < s[:, 3]
        self.x0, self.y0 = np.where(up, s[:, 0], s[:, 2]), np.where(up, s[:, 1], s[:, 3])  # (0 = the lower end)
        self.x1, self.y1 = np.where(up, s[:, 2], s[:, 0]), np.where(up, s[:, 3], s[:, 1])
        hy, hx0, hx1 = h[:, 1], np.minimum(h[:, 0], h[:, 2]), np.maximum(h[:, 0], h[:, 2])
        self.levels = np.unique(np.concatenate([self.y0, self.y1, hy]))
        if len(self.levels) < 2:
            self.depth = None
            return
        size = max(np.ptp(seg[:, [0, 2]]), np.ptp(seg[:, [1, 3]]), 1e-12)
        eps = self.eps = size * 1e-9
        for _ in range(500):  # lines crossing inside a slab: a level there too, until none do
            e, sl, xb, xt = self._pairs()
            same = sl[1:] == sl[:-1]
            db, dt = xb[:-1] - xb[1:], xt[:-1] - xt[1:]
            bad = same & ((db > eps) | (dt > eps))
            if not bad.any():
                break
            i = np.flatnonzero(bad)
            t = db[i] / (db[i] - dt[i])
            lv = self.levels
            y = lv[sl[i]] + t * (lv[sl[i] + 1] - lv[sl[i]])
            y = y[(t > 0) & (t < 1)]
            new = np.unique(np.concatenate([lv, y]))
            if len(new) == len(lv):
                break
            self.levels = new
        e, sl, xb, xt = self._pairs()
        lv = self.levels
        nl = len(lv)
        self.edge, self.slab = e, sl  # (slab by slab, left to right)
        count = np.bincount(sl, minlength=nl - 1)
        self.start = np.concatenate([[0], np.cumsum(count)])  # (each slab's lines: edge[start[s]:start[s + 1]])
        off = np.concatenate([[0], np.cumsum(count + 1)])  # (each slab's pieces: off[s] .. off[s] + count[s])
        self.off = off
        out = int(off[-1])  # (the area around the drawing)
        n = out + 1
        rank = np.arange(len(e)) - self.start[sl]
        ends = np.concatenate([off[:-1], off[:-1] + count])  # (each slab's leftmost and rightmost piece: outside)
        ja, jb = [ends], [np.full(len(ends), out)]
        da, db_, dw = [off[sl] + rank], [off[sl] + rank + 1], [np.ones(len(e), np.int64)]  # (one line apart)
        # where the slabs meet: the lines' spots on each level (below / above), the level lines' ends
        kinds = [(sl + 1, xt, 0), (sl, xb, 1), (np.searchsorted(lv, hy), hx0, 2), (np.searchsorted(lv, hy), hx1, 3)]
        lev = np.concatenate([k for k, _, _ in kinds])
        xs = np.concatenate([x for _, x, _ in kinds])
        kind = np.concatenate([np.full(len(x), c) for _, x, c in kinds])
        o = np.lexsort((xs, lev))
        lev, xs, kind = lev[o], xs[o], kind[o]
        first = np.searchsorted(lev, lev)  # (where each level's spots start)

        def upto(mask):  # how many of these up to and with each spot, on its own level
            c = np.cumsum(mask)
            return c - (c[first] - mask[first])

        below, above = upto(kind == 0), upto(kind == 1)
        cover = upto(kind == 2) - upto(kind == 3)
        gap = np.flatnonzero((lev[1:] == lev[:-1]) & (xs[1:] - xs[:-1] > eps))  # (stretches between two spots)
        k = lev[gap]
        pb = np.where(k > 0, off[np.maximum(k - 1, 0)] + below[gap], out)
        pa = np.where(k < nl - 1, off[np.minimum(k, nl - 2)] + above[gap], out)
        c = cover[gap]
        ja.append(pb[c == 0])
        jb.append(pa[c == 0])
        da.append(pb[c > 0])
        db_.append(pa[c > 0])
        dw.append(c[c > 0])
        lab = _join(n, np.concatenate(ja), np.concatenate(jb))
        a, b, w = lab[np.concatenate(da)], lab[np.concatenate(db_)], np.concatenate(dw)
        keep = a != b
        a, b, w = a[keep], b[keep], w[keep]
        depth = np.full(n, np.iinfo(np.int64).max // 4, np.int64)
        depth[lab[out]] = 0
        while True:  # (fewest lines to cross: grown out from the outside)
            new = depth.copy()
            np.minimum.at(new, b, depth[a] + w)
            np.minimum.at(new, a, depth[b] + w)
            if np.array_equal(new, depth):
                break
            depth = new
        depth[depth > n * 4] = 0  # (cut off from everything: shouldn't happen)
        self.lab, self.depth = lab, depth

    def _pairs(self):
        """Every line with every slab it runs through, slab by slab from left to right: (line, slab, x at the
        slab's bottom, x at its top)."""
        lv = self.levels
        a, b = np.searchsorted(lv, self.y0), np.searchsorted(lv, self.y1)
        e, sl = _runs(a, b - a)
        xb, xt = self._x(e, lv[sl]), self._x(e, lv[sl + 1])
        o = np.lexsort(((xb + xt) / 2, sl))
        return e[o], sl[o], xb[o], xt[o]

    def _x(self, e, y):
        x0, y0 = self.x0[e], self.y0[e]
        return x0 + (self.x1[e] - x0) * (y - y0) / (self.y1[e] - y0)

    def depth_at(self, x, y):
        """How many lines each spot (x, y arrays) is in from outside (0 = outside)."""
        x, y = np.asarray(x, float).ravel(), np.asarray(y, float).ravel()
        out = np.zeros(len(x), np.int64)
        if self.depth is None or not len(x):
            return out
        s = np.searchsorted(self.levels, y, "right") - 1
        ok = (s >= 0) & (s < len(self.levels) - 1)
        for k in np.unique(s[ok]).tolist():
            q = np.flatnonzero(s == k)
            e = self.edge[self.start[k]:self.start[k + 1]]
            if len(e):
                left = (self._x(e[None, :], y[q, None]) < x[q, None]).sum(1)
            else:
                left = np.zeros(len(q), np.int64)
            out[q] = self.depth[self.lab[self.off[k] + left]]
        return out
