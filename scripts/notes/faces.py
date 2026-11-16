"""The areas a custom shape's lines cut the drawing into, and how deep each one is: the fewest lines to cross to get
from it to outside the drawing (custom.fill_test). Fill / Spam fill an area when that's odd (lines ending on
other lines: a line splitting a filled area leaves both halves filled, user), or with Overlaps cancel out off
when it's 1 or more (everything closed in). With every line part of a closed loop, odd = the usual even-odd.

Worked out exactly on the lines, no grid: the drawing is cut into slabs at the height of every corner and every
crossing, each slab into pieces between the lines that run through it (no two cross inside a slab). Pieces that
touch across a slab's edge (not where a level line lies) are one area; two pieces side by side are one line
apart. A spot is looked up by its slab and how many lines lie left of it."""

import hashlib

import numpy as np

from files import speed

_cache = {}


def faces(paths):
    """Faces of these lines ((x, y) point lists), remembered (the same lines from two places: made once)."""
    parts = [np.ascontiguousarray(p, float).reshape(-1, 2) for p in paths]
    key = hashlib.sha1(b"|".join(p.tobytes() for p in parts)).digest()
    got = _cache.get(key)
    if got is None:
        got = _apart(parts)
        if len(_cache) > 50:
            _cache.clear()
        _cache[key] = got
    return got


def bunches(box):
    """The outlines (their boxes: x0, y0, x1, y1) bunched where boxes touch or overlap, a chain of them one bunch:
    lists of their numbers, each in order, bunches in order of their first one. Sorted along x, each box only
    against those still reaching it (a line of letters: a few each)."""
    n = len(box)
    head = list(range(n))

    def find(i):
        while head[i] != i:
            head[i] = head[head[i]]
            i = head[i]
        return i

    order = sorted(range(n), key=lambda i: box[i][0])
    live = []  # (boxes still reaching the sweep's x: only those can touch the next one)
    for i in order:
        x0, y0, x1, y1 = box[i]
        live = [j for j in live if box[j][2] >= x0]
        for j in live:
            if box[j][1] <= y1 and y0 <= box[j][3]:
                head[find(j)] = find(i)
        live.append(i)
    out = {}
    for i in range(n):
        out.setdefault(find(i), []).append(i)
    return list(out.values())


def _apart(parts):
    """Faces of these lines, each bunch of lines whose boxes touch worked out on its own (the slabs of one part
    don't cut the lines of a part beside it: 4 copies side by side took 12 x one, not 4 x)."""
    parts = [p for p in parts if len(p) > 1]
    if len(parts) < 2:
        return Faces(parts)
    box = [(*p.min(0).tolist(), *p.max(0).tolist()) for p in parts]
    groups = bunches(box)
    if len(groups) < 2:
        return Faces(parts)
    allp = np.concatenate(parts)
    size = max(float(np.ptp(allp[:, 0])), float(np.ptp(allp[:, 1])))
    return Apart([Faces([parts[i] for i in g], size) for g in groups],
                 [(min(box[i][0] for i in g), min(box[i][1] for i in g), max(box[i][2] for i in g),
                   max(box[i][3] for i in g)) for g in groups])


class Apart:
    """Faces of bunches of lines whose boxes don't touch, as one (the same calls as Faces): area 0 = the outside
    of them all, each bunch's areas numbered after the ones before."""

    def __init__(self, parts, boxes):
        self.parts, self.boxes = parts, boxes
        self.maps, base = [], 1
        depth = [np.zeros(1, np.int64)]
        for f in parts:
            n = len(f.lab)
            m = np.arange(base, base + n)
            m[f.lab[f.out]] = 0
            self.maps.append(m)
            depth.append(np.zeros(n, np.int64) if f.depth is None else f.depth)
            base += n
        self.depth = np.concatenate(depth)
        self.lab, self.out = np.arange(base), 0

    def _inside(self, i, x, y):
        x0, y0, x1, y1 = self.boxes[i]
        return (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)

    def area_at(self, x, y):
        x, y = np.asarray(x, float).ravel(), np.asarray(y, float).ravel()
        out = np.zeros(len(x), np.int64)
        for i, f in enumerate(self.parts):
            k = np.flatnonzero(self._inside(i, x, y))
            if len(k):
                out[k] = self.maps[i][f.area_at(x[k], y[k])]
        return out

    def depth_at(self, x, y):
        return self.depth[self.area_at(x, y)]

    def area_grid(self, x, y):
        x, y = np.asarray(x, float).ravel(), np.asarray(y, float).ravel()
        out = np.zeros((len(y), len(x)), np.int64)
        for i, f in enumerate(self.parts):
            x0, y0, x1, y1 = self.boxes[i]
            c = np.flatnonzero((x >= x0) & (x <= x1))
            r = np.flatnonzero((y >= y0) & (y <= y1))
            if len(c) and len(r):
                out[np.ix_(r, c)] = self.maps[i][f.area_grid(x[c], y[r])]
        return out

    def pieces(self):
        got = [f.pieces() for f in self.parts]
        return (np.concatenate([g[0] for g in got]), np.concatenate([g[1] for g in got]),
                np.concatenate([g[2] for g in got]), np.concatenate([m[g[3]] for m, g in zip(self.maps, got)]))


def _segments(paths):
    parts = [np.asarray(p, float).reshape(-1, 2) for p in paths]
    parts = [p for p in parts if len(p) > 1]
    if not parts:
        return np.zeros((0, 4))
    seg = np.concatenate([np.column_stack([p[:-1], p[1:]]) for p in parts])
    return seg[(seg[:, 0] != seg[:, 2]) | (seg[:, 1] != seg[:, 3])]


HEIGHT_SAME = 1e-12  # (heights closer than this share of the drawing's size count as one: rounding noise)


def _even_heights(seg, size):
    """seg with heights a rounding error apart made one (each the lowest of its bunch): a line level but for ~1e-16
    (cos / sin maths, e.g. a star's bar) made a slab so thin that the crossings in it couldn't get a level of their
    own, its lines stayed out of order and a star's middle came out empty (planned-features "NEARLY-LEVEL EDGE")."""
    if not len(seg) or not size:
        return seg
    ys = np.concatenate([seg[:, 1], seg[:, 3]])
    u, back = np.unique(ys, return_inverse=True)
    if len(u) < 2 or not (np.diff(u) <= size * HEIGHT_SAME).any():
        return seg
    head = np.maximum.accumulate(np.where(np.r_[True, np.diff(u) > size * HEIGHT_SAME], np.arange(len(u)), 0))
    ys = u[head][back.reshape(-1)]
    seg = seg.copy()
    seg[:, 1], seg[:, 3] = ys[:len(seg)], ys[len(seg):]
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
    def __init__(self, paths, size=None):
        """size: the whole drawing's (its line-closeness margin), when these lines are one part of it (Apart)."""
        seg = _segments(paths)
        if size is None and len(seg):
            size = max(np.ptp(seg[:, [0, 2]]), np.ptp(seg[:, [1, 3]]))
        seg = _even_heights(seg, size)
        level = seg[:, 1] == seg[:, 3]
        h, s = seg[level], seg[~level]
        up = s[:, 1] < s[:, 3]
        self.x0, self.y0 = np.where(up, s[:, 0], s[:, 2]), np.where(up, s[:, 1], s[:, 3])  # (0 = the lower end)
        self.x1, self.y1 = np.where(up, s[:, 2], s[:, 0]), np.where(up, s[:, 3], s[:, 1])
        hy, hx0, hx1 = h[:, 1], np.minimum(h[:, 0], h[:, 2]), np.maximum(h[:, 0], h[:, 2])
        self.levels = np.unique(np.concatenate([self.y0, self.y1, hy]))
        if len(self.levels) < 2:
            self.depth, self.lab, self.out = None, np.zeros(1, np.int64), 0
            return
        if size is None:
            size = max(np.ptp(seg[:, [0, 2]]), np.ptp(seg[:, [1, 3]]))
        size = max(size, 1e-12)
        eps = self.eps = size * 1e-9
        fast = speed.loops()
        got = fast.face_build(self.x0, self.y0, self.x1, self.y1, self.levels, np.ascontiguousarray(hy),
                              np.ascontiguousarray(hx0), np.ascontiguousarray(hx1), float(eps)) if fast else None
        if got is not None:  # (the same, as a compiled loop: files/fastloops.py)
            self.levels, e, sl, self.lab, self.depth = got
            self._lines(e, sl)
            return
        pairs = self._pairs()
        for _ in range(500):  # lines crossing inside a slab: a level there too, until none do
            e, sl, xb, xt = pairs
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
            pairs = self._split(pairs, lv)
        e, sl, xb, xt = pairs
        lv = self.levels
        nl = len(lv)
        count = self._lines(e, sl)
        off, out = self.off, self.out
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

    def _lines(self, e, sl):
        """Keep each slab's lines (e, slab: slab by slab, left to right) and their pieces' numbers -> lines a slab."""
        self.edge, self.slab = e, sl
        # (each of those lines as x = ex + es * (y - ey): area_at's halving reads these in that order)
        self.ex, self.ey = self.x0[e], self.y0[e]
        self.es = (self.x1[e] - self.ex) / (self.y1[e] - self.ey)
        count = np.bincount(sl, minlength=len(self.levels) - 1)
        self.start = np.concatenate([[0], np.cumsum(count)])  # (each slab's lines: edge[start[s]:start[s + 1]])
        self.off = np.concatenate([[0], np.cumsum(count + 1)])  # (each slab's pieces: off[s] .. off[s] + count[s])
        self.out = int(self.off[-1])  # (the area around the drawing)
        return count

    def _pairs(self):
        """Every line with every slab it runs through, slab by slab from left to right: (line, slab, x at the
        slab's bottom, x at its top)."""
        lv = self.levels
        a, b = np.searchsorted(lv, self.y0), np.searchsorted(lv, self.y1)
        e, sl = _runs(a, b - a)
        xb, xt = self._x(e, lv[sl]), self._x(e, lv[sl + 1])
        o = np.lexsort(((xb + xt) / 2, sl))
        return e[o], sl[o], xb[o], xt[o]

    def _split(self, pairs, old):
        """_pairs again after new levels went in between the old ones: only the slabs that got cut are worked out
        anew, the rest keep their lines as they were (the same as _pairs, ties too: by line number)."""
        e, sl, xb, xt = pairs
        lv = self.levels
        a, b = np.searchsorted(lv, old[:-1]), np.searchsorted(lv, old[1:])  # (each old slab's new slabs: a .. b - 1)
        cut = (b - a > 1)[sl]
        i, s = _runs(a[sl[cut]], (b - a)[sl[cut]])
        ne = e[cut][i]
        nb, nt = self._x(ne, lv[s]), self._x(ne, lv[s + 1])
        o = np.lexsort((ne, (nb + nt) / 2, s))
        keep = ~cut
        sl = np.concatenate([a[sl[keep]], s[o]])
        o2 = np.argsort(sl, kind="stable")  # (the two lots never share a slab, each in order already)
        return (np.concatenate([e[keep], ne[o]])[o2], sl[o2], np.concatenate([xb[keep], nb[o]])[o2],
                np.concatenate([xt[keep], nt[o]])[o2])

    def _x(self, e, y):
        x0, y0 = self.x0[e], self.y0[e]
        return x0 + (self.x1[e] - x0) * (y - y0) / (self.y1[e] - y0)

    def depth_at(self, x, y):
        """How many lines each spot (x, y arrays) is in from outside (0 = outside)."""
        if self.depth is None:
            return np.zeros(np.size(x), np.int64)
        return self.depth[self.area_at(x, y)]

    def area_at(self, x, y):
        """The area each spot (x, y arrays) is in (its number in self.lab; the outside: self.lab[self.out])."""
        x, y = np.asarray(x, float).ravel(), np.asarray(y, float).ravel()
        out = np.full(len(x), self.lab[self.out], np.int64)
        if self.depth is None or not len(x):
            return out
        fast = speed.loops()
        if fast:  # (the same, as a compiled loop: files/fastloops.py)
            return fast.area_spots(np.ascontiguousarray(x), np.ascontiguousarray(y), self.levels, self.start, self.off,
                                   self.lab, self.out, self.edge, self.ex, self.ey, self.es, self.x0, self.y0,
                                   self.x1, self.y1, float(self.eps))
        s = np.searchsorted(self.levels, y, "right") - 1
        q = np.flatnonzero((s >= 0) & (s < len(self.levels) - 1))
        k, xq, yq = s[q], x[q], y[q]
        # a slab's lines are in order from left to right (none cross inside it): how many are left of each spot,
        # found by halving its slab's lines over and over (all spots at once)
        lo, hi = self.start[k], self.start[k + 1]
        act = np.flatnonzero(lo < hi)
        ya, xa = yq, xq
        while len(act):
            mid = (lo[act] + hi[act]) >> 1
            if len(act) < len(ya):
                ya, xa = yq[act], xq[act]
            left = self.ex[mid] + self.es[mid] * (ya - self.ey[mid]) < xa
            lo[act[left]] = mid[left] + 1
            hi[act[~left]] = mid[~left]
            act = act[lo[act] < hi[act]]
        # a spot on a line, or right by one (lines too close to say which comes first): every line of its slab
        # counted, as before the halving (the halving's x can be a hair off _x's: only right by a line, so here)
        first, end = self.start[k], self.start[k + 1]
        margin = self.eps * (end - first + 1)
        near = np.zeros(len(q), bool)
        for side, has in ((lo - 1, lo > first), (lo, lo < end)):
            near[has] |= np.abs(self._x(self.edge[side[has]], yq[has]) - xq[has]) <= margin[has]
        for i in np.flatnonzero(near).tolist():
            lo[i] = first[i] + int((self._x(self.edge[first[i]:end[i]], yq[i]) < xq[i]).sum())
        out[q] = self.lab[self.off[k] + lo - first]
        return out

    def area_grid(self, x, y):
        """area_at for every spot of the grid x (columns) by y (rows): (len(y), len(x)), a row at a time (quick
        for a picture of them)."""
        x, y = np.asarray(x, float).ravel(), np.asarray(y, float).ravel()
        out = np.full((len(y), len(x)), self.lab[self.out], np.int64)
        if self.depth is None:
            return out
        s = np.searchsorted(self.levels, y, "right") - 1
        for r in np.flatnonzero((s >= 0) & (s < len(self.levels) - 1)).tolist():
            k = int(s[r])
            e = self.edge[self.start[k]:self.start[k + 1]]
            left = np.searchsorted(np.sort(self._x(e, y[r])), x, "left") if len(e) else 0
            out[r] = self.lab[self.off[k] + left]
        return out

    def pieces(self):
        """Every piece of every area but the outside: its middle x, y, its size, its area (arrays)."""
        if self.depth is None:
            return np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0, np.int64)
        lv, e, sl = self.levels, self.edge, self.slab
        ym = (lv[sl] + lv[sl + 1]) / 2
        xm = self._x(e, ym)  # (in order across each slab)
        i = np.flatnonzero(sl[1:] == sl[:-1])  # (the pieces between two lines)
        piece = self.off[sl[i]] + (i - self.start[sl[i]]) + 1
        size = (xm[i + 1] - xm[i]) * (lv[sl[i] + 1] - lv[sl[i]])
        g = self.lab[piece]
        keep = g != self.lab[self.out]
        return (xm[i] + xm[i + 1])[keep] / 2, ym[i][keep], size[keep], g[keep]
