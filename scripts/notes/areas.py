"""Areas of a custom shape (custom.py): the pieces its lines cut the drawing into (the outline-and-fill loops and
the fill lines). Each area can get a colour of its own (a channel group, like the outline's with "Outline") or be
left empty: sh["areas"] = [[u, v, colour]], a spot in the drawing's box (like the strokes' points, so it moves
with them) and what the area there gets: 0 = empty, 1 .. COLOURS = that colour.

Areas are found on a grid of MAP_CELLS x MAP_CELLS cells over the drawing: a sliver thinner than a cell may take
its neighbour's colour. Which notes are filled when nothing is coloured stays exact (custom.row_pieces). The drawer
draws them exact on the lines (AreaMap.exact: faces.py, each face given the cells' area number)."""

import math

import numpy as np

from notes.faces import faces

MAP_CELLS = 1024
COLOURS = 15
REACH = 3  # cells: a fill line's loose ends go this much further, so one ending right on a line still splits (and
# for drawing the areas exact, a point this near a line is joined to it, see landed)
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
        self.lines = landed([p for p in paths + cut_paths if len(p) >= 2], REACH / self.k)
        self._exact = None
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

    def exact(self):
        """The same areas found exactly on the lines, for drawing them smooth (the cells only say which area is
        which): (faces.Faces of the lines, each of its areas' number here, a spot inside each: x, y, its area).
        Each one takes the number most of it is in (by its pieces' middles; one spot right by a line may be in
        the cells' next area)."""
        if self._exact is None:
            fc = faces(self.lines)
            x, y, size, g = fc.pieces()
            lab = np.full(len(fc.lab), self.outside(), np.int64)
            c = self.cell(x, y).astype(np.int64)
            ok = c >= 0
            pair, at = np.unique(g[ok] * (self.count + 1) + c[ok], return_inverse=True)
            w = np.bincount(at.ravel(), size[ok], len(pair))
            pg, pc = pair // (self.count + 1), pair % (self.count + 1)
            o = np.lexsort((-w, pg))
            o = o[np.r_[True, pg[o][1:] != pg[o][:-1]]] if len(o) else o
            lab[pg[o]] = pc[o]
            o = np.lexsort((-size, g))  # (only on walls: as the biggest piece's spot says)
            big = o[np.r_[True, g[o][1:] != g[o][:-1]]] if len(o) else o
            lost = big[np.isin(g[big], pg, invert=True)]
            got = self.at(x[lost], y[lost])
            lab[g[lost]] = np.where(got >= 0, got, self.outside())
            # a spot in each: its biggest piece the cells say is in its area
            o = np.lexsort((-size, lab[g] != c, g))
            spot = o[np.r_[True, g[o][1:] != g[o][:-1]]] if len(o) else o
            self._exact = (fc, lab, x[spot], y[spot], g[spot])
        return self._exact

    def fine_at(self, u, v):
        """The area number at each spot (u, v arrays), exact on the lines."""
        fc, lab = self.exact()[:2]
        return lab[fc.area_at(u, v)]

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


def landed(lines, reach):
    """The lines ((u, v) arrays) plus a short line from every point that sits just off another line (at most reach
    away: u, v) to the nearest spot on it, so the exact areas are cut where the cells cut them (two lines passing
    within a hair of each other, or a line ending just short of one, let two exact areas run together, user). Two
    lines that come that close without crossing are nearest at a point of one of them, so the points are enough.
    Points are rounded to a millionth of reach first (a line ending at 0.02 on a corner at 0.020000000000000018 left
    a sliver between them that joined the areas on both sides)."""
    k = 1 / np.asarray(reach, float)  # (counted in reaches)
    lines = [np.round(p * k * 1e6) / (k * 1e6) for p in lines if len(p) >= 2]
    if not lines:
        return lines
    pts = [p * k for p in lines]
    along = [np.concatenate([[0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))]) for p in pts]
    closed = np.array([math.dist(p[0], p[-1]) <= 1e-12 for p in lines])
    total = np.array([a[-1] for a in along])
    # the points (a closed line's last one is its first), and the segments with where along their line they lie
    n = np.array([len(p) - c for p, c in zip(pts, closed)])
    pl, pa = np.repeat(np.arange(len(pts)), n), np.concatenate([a[:m] for a, m in zip(along, n)])
    pp = np.concatenate([p[:m] for p, m in zip(pts, n)])
    seg = np.concatenate([np.column_stack([p[:-1], p[1:]]) for p in pts])
    sl = np.repeat(np.arange(len(pts)), [len(p) - 1 for p in pts])
    s0, s1 = np.concatenate([a[:-1] for a in along]), np.concatenate([a[1:] for a in along])
    # each segment in the squares (2 reaches wide) it passes; a spot within 1 of a point is in the 3 x 3 round it
    a, b = seg[:, :2], seg[:, 2:]
    m = np.ceil(np.abs(b - a).max(1) * 2).astype(np.int64) + 1  # (steps of half a reach or less)
    si = np.repeat(np.arange(len(seg)), m)
    t = (np.arange(int(m.sum())) - np.repeat(np.cumsum(m) - m, m)) / np.repeat(np.maximum(m - 1, 1), m)
    sq = np.floor((a[si] + (b[si] - a[si]) * t[:, None]) / 2).astype(np.int64)
    sq0 = sq.min(0) - 1
    wide = int(sq[:, 1].max() - sq0[1]) + 2
    pair = np.unique(((sq[:, 0] - sq0[0]) * wide + sq[:, 1] - sq0[1]) * len(seg) + si)
    key, sid = pair // len(seg), pair % len(seg)
    pq = np.floor(pp / 2).astype(np.int64) - sq0
    vi, vs = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            want = (pq[:, 0] + dx) * wide + pq[:, 1] + dy
            lo, hi = np.searchsorted(key, want, "left"), np.searchsorted(key, want, "right")
            cnt = hi - lo
            vi.append(np.repeat(np.arange(len(pp)), cnt))
            vs.append(sid[np.repeat(lo, cnt) + np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)])
    vi, vs = np.concatenate(vi), np.concatenate(vs)
    # not its own line right by the point (along it, round a closed one)
    own = sl[vs] == pl[vi]
    x, lo_, hi_, tot = pa[vi], s0[vs], s1[vs], total[pl[vi]]
    gap = np.maximum(0, np.maximum(lo_ - x, x - hi_))
    for shift in (tot, -tot):
        gap = np.where(closed[pl[vi]], np.minimum(gap, np.maximum(0, np.maximum(lo_ - x - shift, x + shift - hi_))),
                       gap)
    keep = ~own | (gap >= 2)
    vi, vs = vi[keep], vs[keep]
    a, d, q = seg[vs, :2], seg[vs, 2:] - seg[vs, :2], pp[vi]
    ll = (d * d).sum(1)
    t = np.clip(((q - a) * d).sum(1) / np.where(ll == 0, 1, ll), 0, 1)
    at = a + t[:, None] * d
    dist = np.hypot(*(q - at).T)
    keep = dist <= 1
    vi, at, dist, line = vi[keep], at[keep], dist[keep], sl[vs[keep]]
    o = np.lexsort((dist, line, vi))  # (the nearest spot on each line near a point)
    o = o[np.r_[True, (vi[o][1:] != vi[o][:-1]) | (line[o][1:] != line[o][:-1])]] if len(o) else o
    if not len(o):
        return lines
    vi, at, dist, line = vi[o], at[o], dist[o], line[o]
    # only where the lines come closest: a point nearer that line than the points either side of it (joining every
    # point along a stretch where two lines run close cut slivers off the areas there, coloured wrong, user)
    nl = len(lines)
    pair = vi * nl + line  # (in order)
    st = np.concatenate([[0], np.cumsum(n)])[pl[vi]]
    end = st + n[pl[vi]] - 1
    shut = closed[pl[vi]]
    keep = dist > 1e-9  # (not one already on it)
    off = (pp[vi] - at) / np.maximum(dist, 1e-12)[:, None]  # (which way is away from that line)
    for nb in (np.where(vi > st, vi - 1, np.where(shut, end, -1)), np.where(vi < end, vi + 1, np.where(shut, st, -1))):
        want = nb * nl + line
        j = np.minimum(np.searchsorted(pair, want), len(pair) - 1)
        step = pp[np.maximum(nb, 0)] - pp[vi]
        near = np.hypot(*step.T) <= 1  # (a far one is a different place)
        keep &= ~((nb >= 0) & near & (pair[j] == want) & (dist[j] < dist))
        # nor where a step towards the next point comes nearer still (the thin tip where two lines meet: cut into
        # slices, each its own colour, user)
        keep &= ~((nb >= 0) & ((step * off).sum(1) < -1e-6 * np.hypot(*step.T)))
    if not keep.any():
        return lines
    first = np.concatenate([p[:m] for p, m in zip(lines, n)])  # (the points as they are, so the links start on them)
    link = np.unique(np.column_stack([first[vi[keep]], at[keep] / k]), axis=0)
    return lines + [r.reshape(2, 2) for r in link]


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
