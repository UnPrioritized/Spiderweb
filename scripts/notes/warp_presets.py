"""Warp presets (user 2026-11-10, PLAN.md "Warp several points + presets" B: the image editor's ready-made bends, a
Bend % and a direction): each preset is a bend of the colour box (where the colour at each spot goes, box units),
laid onto the shape's grid AS IT IS (any size, split lines, Smooth / Straight cells): every point takes the bend at
its place and its curve handles the bend's slope there (a third of its cell along), the cells' inner controls its
twist, so a 3 x 3 grid already shows the whole preset. A preset is a starting point (user): dragging anything
afterwards makes it Custom; picking it again lays it again.

Saved in rc["warp"] as "preset": [name, bend %, way] next to the points it made; it counts only while the points /
handles still are what it makes (matches()), so any hand edit shows Custom without every edit having to drop it."""

import numpy as np

from notes import warp as wp_

PRESETS = ("arc", "arc_lower", "arc_upper", "arch", "bulge", "shell_lower", "shell_upper", "flag", "wave", "fish",
           "rise", "fisheye", "inflate", "squeeze", "twist")
NO_WAY = ("fisheye", "inflate", "twist")  # (round ones: no Across / Up)
WAYS = ("across", "up")
MOST_BEND = 100.0
STEP = 1e-5  # (the slopes' small step, box units)


def _field(name, b, u, v):
    """Where the colour at spots (u, v) (box units, across = the preset's own across) goes: (du, dv) offsets, for
    bend b (-1 .. 1)."""
    x, y = 2.0 * u - 1.0, 2.0 * v - 1.0
    z = np.zeros_like(u)
    hump = 1.0 - x * x  # (0 at the sides, 1 in the middle)
    if name == "arc":  # (the box laid on a ring: the middle row keeps its length, the rest fan round it)
        phi = b * np.pi * 0.5  # (100 % = a quarter of a ring: the inner edge stays clear of its middle)
        if phi == 0:
            return z, z
        R = 1.0 / phi
        a = (u - 0.5) * phi
        r = R + (v - 0.5)
        # (r cos a - R written so a slight bend (R huge) stays exact)
        nx = 0.5 + r * np.sin(a)
        ny = 0.5 - 2.0 * R * np.sin(a / 2.0) ** 2 + (v - 0.5) * np.cos(a)
        return nx - u, ny - v
    if name == "arc_lower":
        return z, -b * 0.4 * hump * (1.0 - v)
    if name == "arc_upper":
        return z, b * 0.4 * hump * v
    if name == "arch":
        return z, b * 0.4 * hump
    if name == "bulge":
        return z, b * 0.25 * hump * y
    if name == "shell_lower":
        return -b * 0.12 * x * (1.0 - v), -b * 0.4 * hump * (1.0 - v)
    if name == "shell_upper":
        return -b * 0.12 * x * v, b * 0.4 * hump * v
    if name == "flag":
        return z, b * 0.2 * np.sin(2.0 * np.pi * u)
    if name == "wave":
        return z, b * 0.2 * np.sin(2.0 * np.pi * u - (1.0 - v) * np.pi / 2.0)
    if name == "fish":
        return z, b * 0.25 * y * np.sin(2.0 * np.pi * u)
    if name == "rise":
        return z, -b * 0.25 * np.cos(np.pi * u)
    if name == "squeeze":
        return -b * 0.12 * x * (1.0 - y * y), b * 0.15 * y * hump
    if name == "inflate":
        return b * 0.15 * x * (1.0 - y * y), b * 0.15 * y * hump
    if name == "fisheye":  # (inside the box's oval: the middle spread out (b > 0) or drawn in; the rim stays)
        r = np.hypot(x, y)
        g = np.where(r < 1.0, b * 0.6 * (1.0 - r * r) ** 2, 0.0)  # (f(r) = r (1 + g): smooth at the middle + rim)
        return x * g / 2.0, y * g / 2.0
    if name == "twist":  # (turned most in the middle, not at all from the box's oval out)
        r = np.hypot(x, y)
        t = np.where(r < 1.0, b * np.pi * 0.4 * (1.0 - r * r) ** 2, 0.0)
        c, s = np.cos(t), np.sin(t)
        return (x * c - y * s - x) / 2.0, (x * s + y * c - y) / 2.0
    raise ValueError(name)


def field(name, bend, way, u, v):
    """The preset's offsets (du, dv) at spots (u, v) of the colour box; way "up" = the preset turned to run up."""
    b = max(-MOST_BEND, min(MOST_BEND, float(bend))) / 100.0
    u, v = np.asarray(u, np.float64), np.asarray(v, np.float64)
    if way == "up" and name not in NO_WAY:
        dv, du = _field(name, b, v, u)
        return du, dv
    return _field(name, b, u, v)


def ok(p):
    """A saved "preset" entry [name, bend, way] -> its clean list, or None."""
    if not (isinstance(p, list) and len(p) == 3 and p[0] in PRESETS and wp_.number(p[1]) and p[2] in WAYS):
        return None
    return [p[0], round(max(-MOST_BEND, min(MOST_BEND, float(p[1]))), 3), "across" if p[0] in NO_WAY else p[2]]


def make(base, name, bend, way):
    """base (a Warp, or a flat grid with the window's picks) with the preset laid onto its grid: points, handles and
    inner controls set (dragged handles / corners it had go), its other settings kept. None = nothing moved (0 %)."""
    p = ok([name, bend, way])
    if p is None:
        return None
    name, bend, way = p
    c, r = wp_.size(base)
    X, Y = wp_.lines(base)
    V, U = np.meshgrid(Y, X, indexing="ij")  # ((rows + 1, cols + 1) each)

    def at(du, dv):
        a, b = field(name, bend, way, U + du, V + dv)
        return np.stack([a, b], -1)
    D = at(0.0, 0.0)
    Du = (at(STEP, 0.0) - at(-STEP, 0.0)) / (2 * STEP)
    Dv = (at(0.0, STEP) - at(0.0, -STEP)) / (2 * STEP)
    Duv = (at(STEP, STEP) - at(STEP, -STEP) - at(-STEP, STEP) + at(-STEP, -STEP)) / (4 * STEP * STEP)
    out = {k: v for k, v in base.items() if k not in ("pts", "h", "corners", "in", "preset")}
    out["pts"] = D.reshape(-1, 2).tolist()
    P = np.array([[wp_._kept(v) for v in q] for q in out["pts"]]).reshape(r + 1, c + 1, 2)
    wx, wy = np.diff(X)[None, :, None] / 3.0, np.diff(Y)[:, None, None] / 3.0
    R = np.zeros((r + 1, c + 1, 4, 2))  # (each handle from its point: the slope a third of its cell along)
    R[:, 1:, 0] = -Du[:, 1:] * wx
    R[:, :-1, 1] = Du[:, :-1] * wx
    R[1:, :, 2] = -Dv[1:] * wy
    R[:-1, :, 3] = Dv[:-1] * wy
    # (EVERY handle saved, as if dragged: the preset's bend then stays the same when Cells switches)
    h = [[int(j * (c + 1) + i), int(s), float(R[j, i, s, 0]), float(R[j, i, s, 1])]
         for j in range(r + 1) for i in range(c + 1) for s in range(4) if wp_.side_ok(i, j, s, c, r)]
    if h:
        out["h"] = h
    got = wp_.clean_warp(out)
    if got is None:
        return None
    # (each cell's inner controls from the bend's twist at its corners; saved as "in" where _inner's rule differs)
    B = wp_.net(got, strength=False)
    inner = []
    ex, ey = np.diff(X), np.diff(Y)
    for j in range(r):
        for i in range(c):
            for s in range(4):
                b, a = divmod(s, 2)
                jj, ii = j + b, i + a
                sx, sy = (1.0 if a == 0 else -1.0) * ex[i] / 3.0, (1.0 if b == 0 else -1.0) * ey[j] / 3.0
                want = D[jj, ii] + Du[jj, ii] * sx + Dv[jj, ii] * sy + Duv[jj, ii] * sx * sy
                d = want - B[3 * j + 1 + b, 3 * i + 1 + a]
                if np.abs(d).max() > wp_.IN_TINY:
                    inner.append([j * c + i, s, float(d[0]), float(d[1])])
    if inner:
        got = wp_.clean_warp(dict(got, **{"in": inner}))
    if got is not None:
        got["preset"] = p
    return got


def matches(wp):
    """The preset [name, bend, way] a clean Warp still is (its points / handles what that preset makes on its grid),
    or None (no preset, or edited since: Custom)."""
    p = ok(wp.get("preset")) if wp else None
    if p is None or wp.get("corners"):
        return None
    m = make(wp, *p)
    if m is None:
        return None
    if not np.allclose(wp_.net(wp, strength=False), wp_.net(m, strength=False), atol=1e-7, rtol=0):
        return None
    return p
