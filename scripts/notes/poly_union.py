"""The outline of closed polygons laid over each other, filled by the nonzero rule (Turn into custom shape on joined
letters, text.to_custom): where they overlap is inside once, holes stay holes, so the even-odd fill of the result
fills exactly what the polygons filled together. Small groups (one joined word) at a time: every edge is checked
against every other edge of the group."""

import numpy as np

CHUNK = 256  # (edges checked against the rest at a time: keeps the working arrays small)


def _closed(p):
    p = np.asarray(p, float).reshape(-1, 2)
    keep = np.r_[True, np.any(p[1:] != p[:-1], axis=1)]
    p = p[keep]
    if len(p) and np.any(p[0] != p[-1]):
        p = np.vstack([p, p[:1]])
    return p


def _winding(segs, pts):
    """Nonzero winding number of every point (n, 2) for the closed segments (m, 4) (x0, y0, x1, y1)."""
    out = np.zeros(len(pts), np.int64)
    for s in range(0, len(pts), CHUNK):
        x, y = pts[s:s + CHUNK, 0:1], pts[s:s + CHUNK, 1:2]
        xa, ya, xb, yb = segs[None, :, 0], segs[None, :, 1], segs[None, :, 2], segs[None, :, 3]
        cross = (ya <= y) != (yb <= y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xc = xa + (xb - xa) * (y - ya) / (yb - ya)
        hit = cross & (x < xc)
        out[s:s + CHUNK] = np.where(hit, np.where(yb > ya, 1, -1), 0).sum(1)
    return out


def union(polys):
    """The outline loops (closed point lists) of polys (closed point lists) filled by the nonzero rule."""
    polys = [p for p in (_closed(p) for p in polys) if len(p) >= 4]
    if len(polys) < 2:
        return [p.tolist() for p in polys]
    segs = np.concatenate([np.column_stack([p[:-1], p[1:]]) for p in polys])
    owner = np.concatenate([np.full(len(p) - 1, i) for i, p in enumerate(polys)])
    allp = segs.reshape(-1, 2)
    size = max(float(np.ptp(allp[:, 0])), float(np.ptp(allp[:, 1])), 1e-12)
    eps = size * 1e-9
    cuts = [[] for _ in range(len(segs))]  # (each edge's split spots: (t along it, x, y))
    lo, hi = np.minimum(segs[:, :2], segs[:, 2:]), np.maximum(segs[:, :2], segs[:, 2:])
    for s in range(0, len(segs), CHUNK):
        a = segs[s:s + CHUNK]
        near = ((hi[None, :, 0] >= lo[s:s + CHUNK, None, 0] - eps) & (lo[None, :, 0] <= hi[s:s + CHUNK, None, 0] + eps)
                & (hi[None, :, 1] >= lo[s:s + CHUNK, None, 1] - eps) & (lo[None, :, 1] <= hi[s:s + CHUNK, None, 1] + eps)
                & (owner[None, :] != owner[s:s + CHUNK, None]))
        ia, ib = np.nonzero(near)
        if not len(ia):
            continue
        p, r = a[ia, :2], a[ia, 2:] - a[ia, :2]
        q, d = segs[ib, :2], segs[ib, 2:] - segs[ib, :2]
        den = r[:, 0] * d[:, 1] - r[:, 1] * d[:, 0]
        qp = q - p
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (qp[:, 0] * d[:, 1] - qp[:, 1] * d[:, 0]) / den
            u = (qp[:, 0] * r[:, 1] - qp[:, 1] * r[:, 0]) / den
        cross = (np.abs(den) > 1e-300) & (t > 0) & (t < 1) & (u >= 0) & (u <= 1)
        for k in np.flatnonzero(cross).tolist():  # (crossing: a split on this edge; the other edge gets its own
            x, y = p[k] + t[k] * r[k]  # turn when it comes up as `a`)
            cuts[s + int(ia[k])].append((float(t[k]), float(x), float(y)))
        # (an end of the other edge lying on this one: lines on top of each other split at the same spots)
        for end in (q, q + d):
            rr = (r * r).sum(1)
            with np.errstate(divide="ignore", invalid="ignore"):
                tt = ((end - p) * r).sum(1) / rr
            foot = p + tt[:, None] * r
            on = (rr > 0) & (tt > 1e-12) & (tt < 1 - 1e-12) & (np.abs(foot - end).max(1) <= eps)
            for k in np.flatnonzero(on).tolist():
                cuts[s + int(ia[k])].append((float(tt[k]), float(end[k, 0]), float(end[k, 1])))
    pieces = []  # (sub-edges: x0, y0, x1, y1)
    for i, (x0, y0, x1, y1) in enumerate(segs.tolist()):
        pts = [(x0, y0)] + [(x, y) for _, x, y in sorted(cuts[i])] + [(x1, y1)]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if abs(ax - bx) > eps or abs(ay - by) > eps:
                pieces.append((ax, ay, bx, by))
    pc = np.array(pieces, float)
    mid = (pc[:, :2] + pc[:, 2:]) / 2
    dv = pc[:, 2:] - pc[:, :2]
    ln = np.hypot(dv[:, 0], dv[:, 1])[:, None]
    nrm = np.column_stack([-dv[:, 1], dv[:, 0]]) / ln  # (its left)
    step = size * 1e-7
    wl = _winding(segs, mid + nrm * step) != 0
    wr = _winding(segs, mid - nrm * step) != 0
    keep = wl != wr
    edges = {}  # (start spot -> [end spots], filled on the left)
    seen = set()
    key = lambda x, y: (round(x / eps), round(y / eps))
    for (ax, ay, bx, by), left in zip(pc[keep].tolist(), wl[keep].tolist()):
        if not left:
            ax, ay, bx, by = bx, by, ax, ay
        k0, k1 = key(ax, ay), key(bx, by)
        if (k0, k1) in seen or k0 == k1:  # (the same piece of line from two polygons: once)
            continue
        seen.add((k0, k1))
        edges.setdefault(k0, []).append((k1, (ax, ay), (bx, by)))
    loops = []
    while edges:
        k0 = next(iter(edges))
        start = k0
        loop = []
        while k0 in edges:
            k1, a, b = edges[k0].pop()
            if not edges[k0]:
                del edges[k0]
            if not loop:
                loop.append(list(a))
            loop.append(list(b))
            k0 = k1
            if k0 == start:
                break
        if len(loop) >= 4:
            loop[-1] = list(loop[0])
            loops.append(loop)
    return loops
