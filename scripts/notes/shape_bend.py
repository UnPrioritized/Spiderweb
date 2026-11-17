"""Deform shapes (PLAN.md "Deform shapes", user 2026-11-16 / 11-17): a Stir tab set to Shape bends the SHAPE itself,
not its colours. The same maths as the colours (recolour.stir_from), so the outline goes where Stir shows a colour
stripe going; notes are then made from the bent shape as usual (Fill, Spam, patterns, its colours all from the bent
lines).

Stir works BACKWARDS: a spot of the colour box "looks at" another spot (look). Lines move FORWARDS, so each point of
the outline is put where look() brings it back to where it was: worked out (Newton's way) from a first guess that
undoes each move in turn (exact for the plain Turn / Twist / Bulge, near for the rest). Lines are cut into finer
pieces only where the bend curves them (notes/deform.py, the drawer's Deform…, does that part).

THE BOX STAYS PUT (user): the colour box is the shape's own notes' box before the bend; every Shape tab measures in
it (tab 2 bends what tab 1 left). Notes may go past it.
At the edges, Squeeze and the colour settings don't count here (the window greys them, user 2026-11-16)."""
import math

import numpy as np

from files.speed import compiled
from notes import recolour as rc

EPS = 1e-10  # (unit box: a point counts as found when look() brings it back this near)
STEPS = 40  # (Newton steps at most)
FAST_LEAST = 2048  # (points: fewer = the NumPy look, no compiled call set-up)
FOLD = 1e-5  # (a point that stays farther from home than this: the bend folds there, two places look at one spot or
# none does; the lines can't show that, so the notes are pulled() instead)


class Folded(Exception):
    """A Shape tab folds the shape over itself here (StirBend.forward_unit): bent_shape gives up on the lines."""


def is_shape(st):
    """Whether a stir (saved or filled in) bends the shape, not the colours."""
    return isinstance(st, dict) and st.get("moves") == "shape"


def shape_places(r):
    """Which Stir tabs (0 = the first) of a saved Recolour are set to Shape."""
    s = (r or {}).get("stir")
    items = s if isinstance(s, list) else ([s] if isinstance(s, dict) else [])
    return [i for i, x in enumerate(items) if is_shape(x)]


def off_without(off, gone):
    """A shape's switched-off steps (sh["off"], Properties: [[kind, n], ...]) once Stir tabs gone are taken out of
    its Recolour: theirs dropped, the later tabs' numbers moved down."""
    out = []
    for e in off or []:
        if isinstance(e, list) and len(e) == 2 and e[0] == "stir" and isinstance(e[1], int):
            if e[1] in gone:
                continue
            e = ["stir", e[1] - sum(1 for g in gone if g < e[1])]
        out.append(e)
    return out


def shape_tabs(r, off=()):
    """A saved Recolour's Stir tabs set to Shape, in tab order, each filled in (none: []). off: the shape's
    switched-off steps (Properties): those tabs left out."""
    if not isinstance(r, dict):
        return []
    stack = rc.stir_stack(r.get("stir"))
    stack = [{} if ["stir", i] in [list(e) for e in off or ()] else s for i, s in enumerate(stack)]
    u = r.get("_upto")  # (a Shape tab being edited: the Shape tabs after it skipped too, like later colour steps)
    if isinstance(u, int) and not isinstance(u, bool):
        n = -1
        for j, k in enumerate(rc.order_of(rc.clean_recolour(r) or {})):
            if k == "stir":
                n += 1
                if j == u and n < len(stack) and is_shape(stack[n]):
                    stack = stack[:n + 1]
                    break
    return [s for s in stack if is_shape(s) and _moves(s)]


def is_shape_warp(r):
    """Whether a saved Recolour's Warp bends the shape (Warp set to Shape, stage F), however flat."""
    return isinstance(r, dict) and isinstance(r.get("warp"), dict) and r["warp"].get("moves") == "shape"


def can_bend(sh):
    """Whether a shape's outline / notes can be bent by a Shape step: not a picture (user: Shape greyed for them;
    hunt 2026-11-17: a window's Shape pick made a picture's first Warp / Liquify bend nothing)."""
    return not ("picture" in sh or "picture_notes" in sh or sh.get("hz"))


def is_shape_liquify(r):
    """Whether a saved Recolour's Liquify bends the shape (Liquify set to Shape, stage G)."""
    return isinstance(r, dict) and isinstance(r.get("liquify"), dict) and r["liquify"].get("moves") == "shape"


def has_shape(r):
    """Whether a saved Recolour holds a Shape step (a Stir tab, the Warp or the Liquify set to Shape)."""
    return bool(shape_places(r)) or is_shape_warp(r) or is_shape_liquify(r)


SHAPE_KINDS = ("stir", "warp", "liquify")  # (the order Shape steps run in: every Stir tab, the Warp, the Liquify)


def shape_step_tags(r):
    """A saved Recolour's Shape steps as step tags (kind, n) in the order they run (Stir tabs, Warp, Liquify): they
    run before every other step, whatever order they were added in (Properties lists them so)."""
    if not isinstance(r, dict):
        return []
    out = [("stir", i) for i in shape_places(r)]
    if is_shape_warp(r):
        out.append(("warp", 0))
    if is_shape_liquify(r):
        out.append(("liquify", 0))
    return out


def _skipped(r, kind):
    """Whether r's Shape step of kind ("warp" / "liquify") is skipped: a Shape step before it (in SHAPE_KINDS order)
    is being edited (rc "_upto"), so the ones after it are hidden like later colour steps."""
    u = r.get("_upto")
    if not isinstance(u, int) or isinstance(u, bool):
        return False
    order = rc.order_of(rc.clean_recolour(r) or {})
    if not 0 <= u < len(order):
        return False
    k = order[u]
    if k == "stir":
        stack = rc.stir_stack(r.get("stir"))
        n = order[:u + 1].count("stir") - 1
        return n < len(stack) and is_shape(stack[n])
    return k == "warp" and kind == "liquify" and is_shape_warp(r)


def warp_shape(r, off=()):
    """A saved Recolour's Warp when it is set to Shape and bends something now (clean), else None: switched off in
    Properties, hidden, flat, or skipped while a Shape Stir tab is edited (Shape steps run Stir tabs -> Warp)."""
    if not is_shape_warp(r) or ["warp", 0] in [list(e) for e in off or ()]:
        return None
    from notes.warp import clean_warp, shows
    wp = shows(clean_warp(r["warp"]))
    if wp is None or _skipped(r, "warp"):
        return None
    return wp


def liquify_shape(r, off=()):
    """A saved Recolour's Liquify when it is set to Shape and bends something now (clean), else None: switched off,
    hidden, Strength 0, nothing pushed (a freeze mask alone), or skipped while an earlier Shape step is edited."""
    if not is_shape_liquify(r) or ["liquify", 0] in [list(e) for e in off or ()]:
        return None
    from notes.liquify import clean_liquify
    lq = clean_liquify(r["liquify"])
    if not lq or not lq.get("map") or lq.get("on") is False or not lq.get("strength", 100.0) or _skipped(r, "liquify"):
        return None
    return lq


def always_cut(sh):
    """Whether shape sh bends note by note whatever "Cut notes where bent" says: pasted / plain notes, and kinds whose
    lines can't be bent (bent_shape's AS_NOTES: funnel, Glissando, joined curves, a polyline with Last note per dot).
    The windows show their tick ticked + greyed (hunt 2026-11-17 #5)."""
    if "picture" in sh or "picture_notes" in sh:
        return False
    if "notes" in sh:
        return True
    kind = sh.get("kind")
    if kind in ("funnel", "gliss"):
        return True
    if kind in ("line", "poly", "free", "curve", "arc") and not sh.get("cut") and not sh.get("join"):
        from notes.joined import is_joined
        if is_joined(sh):
            return True
        return bool(kind == "poly" and sh.get("end_dot") and not sh.get("by_snap") and len(sh.get("pts") or ()) > 2
                    and not (sh.get("shape") or sh.get("pattern")))
    return False


def cut_before(r, sh, kind, n=0):
    """Whether a Shape step running BEFORE this one (kind "stir" tab n / "warp" / "liquify") cuts the notes: then this
    step cuts them too, whatever its own tick (engine.shape_tabs_done: every step after the first ticked one). The
    windows show its tick ticked + greyed (hunt 2026-11-17 #3)."""
    if not isinstance(r, dict):
        return False
    off = [list(e) for e in sh.get("off") or ()]
    stack = rc.stir_stack(r.get("stir"))
    upto = n if kind == "stir" else len(stack)
    for i, s in enumerate(stack[:upto]):
        if ["stir", i] not in off and is_shape(s) and _moves(s) and s.get("chop", True):
            return True
    if kind == "liquify":
        wp = warp_shape(r, sh.get("off"))
        if wp is not None and wp.get("chop", True):
            return True
    return False


def cuts_notes(r, sh):
    """Whether shape sh with saved Recolour r bends its NOTES (each cut where bent) rather than its outline: pasted /
    plain notes, or a Shape step (Stir tab / Warp / Liquify) with Cut notes where bent on (engine.shape_tabs_done)."""
    if "notes" in sh and "picture" not in sh and "picture_notes" not in sh:
        return True
    off = sh.get("off")
    steps = shape_tabs(r, off) + [x for x in (warp_shape(r, off), liquify_shape(r, off)) if x is not None]
    return any(x.get("chop", True) for x in steps)


def _moves(st):
    """Whether a filled-in Shape stir moves anything at all."""
    return bool(st["turn"] % 360 or st["twist"] or st["bulge"] or st["polar"] or st["ripple"] or st["wring"]
                or st["kaleido"] >= 2 or (st["turn"] and st["reach"]))


def colour_part(r):
    """A saved Recolour without its Shape tabs (their places in the order taken out too), or None when nothing is
    left. Tabs held at their defaults keep theirs."""
    if not isinstance(r, dict):
        return r
    s = r.get("stir")
    items = s if isinstance(s, list) else ([s] if isinstance(s, dict) else [])
    # (the Warp / Liquify set to Shape go too, stages F / G)
    whole = {k for k, yes in (("warp", is_shape_warp(r)), ("liquify", is_shape_liquify(r))) if yes}
    if not any(is_shape(x) for x in items) and not whole:
        return r
    keep, gone = [], []
    for i, x in enumerate(items):
        (gone if is_shape(x) else keep).append(i if is_shape(x) else x)
    out = {k: v for k, v in r.items() if k not in ("stir", "order") and k not in whole}
    if keep:
        out["stir"] = keep if len(keep) > 1 else keep[0]
    o = r.get("order")
    if isinstance(o, list):  # (the n-th "stir" entry = tab n: the gone tabs' entries dropped)
        n, new = -1, []
        for k in o:
            if k == "stir":
                n += 1
                if n in gone:
                    continue
            if k in whole:
                continue
            new.append(k)
        out["order"] = new
    u = r.get("_upto")  # (a step being edited, later ones skipped: App.edit_shapes. Counted again without the
    # Shape tabs; editing a Shape tab = every colour step after it skipped: they all run after the Shape tabs)
    if isinstance(u, int) and not isinstance(u, bool):
        n, gone_at = -1, []
        for j, k in enumerate(rc.order_of(rc.clean_recolour(r) or {})):
            if k in whole:
                gone_at.append(j)
            if k == "stir":
                n += 1
                if n in gone:
                    gone_at.append(j)
        if u in gone_at:
            return None
        out["_upto"] = u - sum(1 for j in gone_at if j < u)
    if rc.clean_recolour(out) is None:
        return None
    return out


def shape_only(r):
    """A saved Recolour with ONLY its Shape tabs (the colour steps left out), or None without any: outside Multi
    channel the colours don't count, but a bent shape is bent in every mode."""
    if not isinstance(r, dict):
        return None
    s = r.get("stir")
    tabs = [x for x in (s if isinstance(s, list) else [s]) if is_shape(x)]
    warp, liq = is_shape_warp(r), is_shape_liquify(r)
    if not tabs and not warp and not liq:
        return None
    out = {"mode": "keep", "seed": 1}
    if tabs:
        out["stir"] = tabs if len(tabs) > 1 else tabs[0]
    if warp:
        out["warp"] = r["warp"]
    if liq:
        out["liquify"] = r["liquify"]
    for k in ("frame", "ppq"):
        if k in r:
            out[k] = r[k]
    u = r.get("_upto")  # (a Shape tab being edited: the same tab marked among the Shape tabs kept (hunt 2026-11-17);
    # a colour step being edited hides no Shape tab: they all run first. Kept order: the tabs, the Warp, the Liquify)
    if isinstance(u, int) and not isinstance(u, bool):
        n, m = -1, -1
        for j, k in enumerate(rc.order_of(rc.clean_recolour(r) or {})):
            if k == "warp" and warp and j == u:
                out["_upto"] = len(tabs)
                break
            if k == "liquify" and liq and j == u:
                out["_upto"] = len(tabs) + warp
                break
            if k == "stir":
                n += 1
                items = s if isinstance(s, list) else [s]
                if n < len(items) and is_shape(items[n]):
                    m += 1
                    if j == u:
                        out["_upto"] = m
                        break
    return out


def grown_reach(sh, r):
    """A shape's drawn reach r (first beat, lowest key, highest key, last beat) grown to where its Shape tabs may take
    notes (engine.past_edges: notes bent past an edge are kept / wrapped like any): every bend stays inside the
    circle round its Centre point through the box's farthest corner (StirBend.within), a ripple's height wider.
    Measured on the drawn box (the notes' box is about it). Turned round as seen (spin): the circle as seen
    (recolour.area_scales' sides: the box's width in beats, its height in keys x beats per key), reaching the same far
    both ways (a turned frame swaps the sides; hunt 2026-11-17: a flat box turned 90 lost notes past the edge)."""
    if r is None or not isinstance(sh.get("recolour"), dict):
        return r
    tabs = shape_tabs(sh["recolour"], sh.get("off"))
    wp = warp_shape(sh["recolour"], sh.get("off"))
    lq = liquify_shape(sh["recolour"], sh.get("off"))
    if not tabs and wp is None and lq is None:
        return r
    w, h = max(r[3] - r[0], 1e-9), max(r[2] - r[1], 1e-9)
    x0, x1, y0, y1 = 0.0, 1.0, 0.0, 1.0
    for st in tabs:
        cx, cy = (v / 100 for v in st["centre"])
        rx = ry = 1.0
        if st["spin"] and st["seen"]:
            lx, ly = w, h * st["seen"]
            big = max(lx, ly)
            rx, ry = max(lx / big, 1e-9), max(ly / big, 1e-9)
        far = max(math.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1))
        far = (far + abs(st["ripple"]) / 100 * max(rx, ry)) / min(rx, ry)
        x0, x1, y0, y1 = min(x0, cx - far), max(x1, cx + far), min(y0, cy - far), max(y1, cy + far)
    # (the Warp / Liquify after the tabs: each moves a spot at most its biggest offset each way)
    for P in ([np.abs(WarpBend.offsets(wp))] if wp is not None else []) + \
             ([np.abs(LiquifyBend.offsets(lq))] if lq is not None else []):
        x0, x1, y0, y1 = x0 - P[0], x1 + P[0], y0 - P[1], y1 + P[1]
    return (min(r[0], r[0] + x0 * w), min(r[1], r[1] + y0 * h), max(r[2], r[1] + y1 * h), max(r[3], r[0] + x1 * w))


class StirBend:
    """One Shape tab, forwards: points (tick, key) -> where the bend puts them (floats).
    st: the tab (filled in); r: the Recolour (its frame / ppq); box, own: recolour.colour_box's, of the notes before
    the bend."""

    def __init__(self, st, r, box, own, ppq):
        self.st = st
        self.r = dict(r or {}, ppq=ppq)
        self.box, self.own = box, own
        f = self.r.get("frame") or rc.SAME
        self.turned = bool(f[1])
        self.flip_x = not self.turned and f[0] < 0
        self.flip_y = not self.turned and f[3] < 0
        self.c = tuple(v / 100 for v in st["centre"])
        self.k, self.rr = rc.area_scales(st, lambda u, v: rc.from_unit(u / 100, v / 100, self.r, box, own), ppq)
        self.kaleido = st["kaleido"] >= 2
        self.plain = dict(st, kaleido=0)  # (the Kaleidoscope's slices are repeated afterwards: kaleido_strokes)
        self._seed = None
        self.worst = 0.0
        self._numbers = {}  # (id(stir) -> (it, stir_numbers) for the compiled look)

    # -- the colour box both ways (a flipped shape's colours count mirrored back: recolour.places_of)
    def to_unit(self, t, k):
        x, y = rc.unit_xy(np.asarray(t, np.float64), np.asarray(k, np.float64), self.r, self.box, self.own)
        return (1 - x if self.flip_x else x), (1 - y if self.flip_y else y)

    def from_unit(self, x, y):
        x, y = (1 - x if self.flip_x else x), (1 - y if self.flip_y else y)
        return rc.from_unit(np.asarray(x, np.float64), np.asarray(y, np.float64), self.r, self.box, self.own)

    def within(self, t, k):
        """Which spots (tick, key) a pulled bend may put notes on: inside the circle round the Centre point through
        the box's farthest corner (as Turn / Twist measure it: everything a turn sweeps), a ripple's height wider.
        Past it a bend like Twist along a line keeps finding the shape again (its ribbon edge-on): no notes there."""
        x, y = self.to_unit(t, k)
        (cx, cy), (rx, ry) = self.c, self.rr
        far = max(math.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1))
        grow = abs(self.st["ripple"]) / 100 * max(rx, ry)
        return np.hypot((x - cx) * rx, (y - cy) * ry) <= far * (1 + 1e-9) + grow + 1e-9

    def look_tk(self, t, k):
        """Spots (tick, key) -> where the bend brings them from (floats), Kaleidoscope too (pulled())."""
        x, y = self.to_unit(t, k)
        x, y = self.look(x, y, self.st)
        return self.from_unit(x, y)

    # -- Stir's own maths, backwards (stir_from without At the edges / Squeeze)
    def look(self, x, y, st=None):
        st, (cx, cy), k, (rx, ry) = st or self.plain, self.c, self.k, self.rr
        fast = rc.loops() if np.size(x) >= FAST_LEAST else None
        if fast and not (st["reach"] and st["area"] == rc.DRAWN) and not rc.bulge_out_on(st):
            # (Stir's own compiled walk, files/fastloops.stir_spots: the very same numbers; "wrap" = neither
            # tiled nor clipped, no squeeze = this look)
            got = self._numbers.get(id(st))
            if got is None or got[0] is not st:
                got = self._numbers[id(st)] = (st, rc.stir_numbers(dict(st, edge="wrap", squeeze=False), self.c, k,
                                                                    self.rr))
            fp, ip = got[1]
            shape = np.shape(x)
            x = np.ascontiguousarray(np.ravel(x), np.float64)
            y = np.ascontiguousarray(np.ravel(y), np.float64)
            ox, oy = np.empty_like(x), np.empty_like(y)
            fast.stir_spots(x, y, fp, ip, ox, oy)
            return ox.reshape(shape), oy.reshape(shape)
        reach = st["reach"]
        if st["bulge"]:
            x, y = rc.bulged(x, y, st, (cx, cy), k)
        sx, sy = x, y
        if rc.bends(st):
            x, y = rc.bent(x, y, st, (cx, cy), k, (rx, ry))
        dx, dy = x - cx, y - cy
        turn = st["turn"] / 360
        if reach:
            q = rc.area_spot_q(sx, sy, st, k)
            w = (q < 1).astype(np.float64) if st["hard"] else 1 - q * q * (3 - 2 * q)
            if st["area_turn"]:
                turn = turn * w
            if st["twist"] and st["area_twist"]:
                turn = turn + st["twist"] * np.sqrt(q) * w
        if st["twist"] and not (reach and st["area_twist"]):
            far = max(math.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1))
            turn = turn + st["twist"] * np.sqrt(np.hypot(dx * rx, dy * ry) / max(far, 1e-12))
        a = -2 * np.pi * np.asarray(turn, np.float64)
        ca, sa = np.cos(a), np.sin(a)
        X, Y = dx * rx, dy * ry
        return cx + (X * ca - Y * sa) / rx, cy + (X * sa + Y * ca) / ry

    def guess(self, x, y):
        """A first guess of where look() comes back from (x, y): each move undone in turn, last one first."""
        st, (cx, cy), k, (rx, ry) = self.plain, self.c, self.k, self.rr
        reach = st["reach"]
        dx, dy = x - cx, y - cy
        turn = st["turn"] / 360  # (Turn / Twist: a turn keeps the distance, so its own amount is known here)
        if reach:
            q = rc.area_spot_q(x, y, st, k)
            w = (q < 1).astype(np.float64) if st["hard"] else 1 - q * q * (3 - 2 * q)
            if st["area_turn"]:
                turn = turn * w
            if st["twist"] and st["area_twist"]:
                turn = turn + st["twist"] * np.sqrt(q) * w
        if st["twist"] and not (reach and st["area_twist"]):
            far = max(math.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1))
            turn = turn + st["twist"] * np.sqrt(np.hypot(dx * rx, dy * ry) / max(far, 1e-12))
        a = 2 * np.pi * np.asarray(turn, np.float64)
        ca, sa = np.cos(a), np.sin(a)
        X, Y = dx * rx, dy * ry
        x, y = cx + (X * ca - Y * sa) / rx, cy + (X * sa + Y * ca) / ry
        if st["polar"] >= 100 and not (reach and st["area_polar"]):  # (Straight to round, all the way: exact)
            far = max(math.hypot((X - cx) * rx, (Y - cy) * ry) for X in (0, 1) for Y in (0, 1)) or 1.0
            ang = x * 2 * np.pi - np.pi
            R = y * far
            x, y = cx + R * np.cos(ang) / rx, cy + R * np.sin(ang) / ry
        if st["ripple"] and not (reach and st["area_ripple"]):
            X, Y = (x - cx) * rx, (y - cy) * ry
            h, L = st["ripple"] / 100, st["ripple_len"] / 100
            if st["ripple_kind"] == "side":
                X = X - h * np.sin(2 * np.pi * Y / L)
            elif st["ripple_kind"] == "up":
                Y = Y - h * np.sin(2 * np.pi * X / L)
            else:
                R = np.hypot(X, Y)
                g = np.where(R > 1e-12, (R - h * np.sin(2 * np.pi * R / L)) / np.maximum(R, 1e-12), 1.0)
                X, Y = X * g, Y * g
            x, y = cx + X / rx, cy + Y / ry
        if st["bulge"]:  # (the lens backwards: a spot q of the way out came from m q / (1 + (m - 1) q))
            m = rc.bulge_size(st["bulge"])
            mx, my, q = rc.bulge_q(x, y, st, (cx, cy), k)  # (Bulge reaches past the box too)
            ok = (q < 1) & (q > 1e-12)
            g = np.ones_like(q)
            g[ok] = m / (1 + (m - 1) * q[ok])
            x, y = mx + (x - mx) * g, my + (y - my) * g
        return x, y

    def forward_unit(self, qx, qy):
        """Unit-box points -> the points look() brings back to them (Newton's way from guess)."""
        qx, qy = np.array(qx, np.float64, ndmin=1), np.array(qy, np.float64, ndmin=1)
        x, y = self.guess(qx, qy)
        if self._seed is None:
            self._seed = searcher(self.look)
        x, y = solve(self.look, qx, qy, x, y, self._seed)
        if len(x):  # (how far the worst point stayed from home: a fold = the lines can't show it, pulled() can)
            lx, ly = self.look(x, y)
            self.worst = max(self.worst, float(np.hypot(lx - qx, ly - qy).max()))
            if self.worst > FOLD:
                raise Folded
        return x, y

    def bend2(self, p):
        """(n, 2) points (tick, key) -> (n, 2) bent."""
        p = np.asarray(p, np.float64).reshape(-1, 2)
        if not len(p):
            return p.copy()
        x, y = self.to_unit(p[:, 0], p[:, 1])
        x, y = self.forward_unit(x, y)
        t, k = self.from_unit(x, y)
        return np.column_stack((t, k))


class WarpBend(StirBend):
    """The Warp set to Shape (stage F), forwards: a spot goes where the grid's bend takes it (warp.bend_at: the
    colour at a point's place shows where it went = the outline at a point's place goes where it went). Measured in
    the same colour box as the Stir tabs (the box stays put). Backwards (look: holes between bent notes, Colours bend
    too) = the Warp's own turned-round map + its exact Newton fetch, as the colours use (warp.warped)."""

    def __init__(self, wp, r, box, own, ppq):
        from notes import warp
        self.wp, self.r = wp, dict(r or {}, ppq=ppq)
        self.box, self.own = box, own
        f = self.r.get("frame") or rc.SAME
        self.turned = bool(f[1])
        self.flip_x = not self.turned and f[0] < 0
        self.flip_y = not self.turned and f[3] < 0
        self.P, self.kind = warp.shape_of(wp)
        self.L = warp.uneven(wp)
        self.worst = 0.0
        self.st = {"ripple": 0.0, "ripple_len": 100.0}
        self._map = None

    def forward_unit(self, qx, qy):
        from notes.warp import bend_at
        qx, qy = np.array(qx, np.float64, ndmin=1), np.array(qy, np.float64, ndmin=1)
        if not len(qx):
            return qx, qy
        fast = compiled("warp_bend") if len(qx) >= FAST_LEAST else None
        if fast:  # (files/fastloops.warp_bend: bend_at's very sums, on half the cores)
            to = np.ascontiguousarray
            X, Y = self.L or (None, None)
            ox, oy = np.empty_like(qx), np.empty_like(qy)
            fast.warp_bend(to(self.P, np.float64), int(self.kind), to(X, np.float64) if X is not None else np.zeros(0),
                           to(Y, np.float64) if Y is not None else np.zeros(0), to(qx), to(qy), ox, oy)
            return qx + ox, qy + oy
        d = np.asarray(bend_at(self.P, qx, qy, self.kind, self.L), np.float64).reshape(-1, 2)
        return qx + d[:, 0], qy + d[:, 1]

    def look(self, x, y, st=None):
        from notes import warp
        from notes.liquify import sample
        x, y = np.array(x, np.float64, ndmin=1), np.array(y, np.float64, ndmin=1)
        shape = x.shape
        x, y = x.ravel(), y.ravel()
        if self._map is None:
            self._map = warp.inverse_map(self.wp)
        a = self._map
        d = sample(a, x, y) if len(x) else np.zeros((0, 2))
        ox, oy = warp.exact_offsets(warp.solver(self.wp), a, x, y)
        ox = np.where(np.isfinite(ox), ox, d[:, 0])
        oy = np.where(np.isfinite(oy), oy, d[:, 1])
        return (x + ox).reshape(shape), (y + oy).reshape(shape)

    def look_tk(self, t, k):
        x, y = self.to_unit(t, k)
        x, y = self.look(x, y)
        return self.from_unit(x, y)

    @staticmethod
    def offsets(wp):
        """The farthest a spot moves each way (box units): a Warp's bend blends its points / handles."""
        from notes.warp import shape_of
        return np.abs(shape_of(wp)[0]).reshape(-1, 2).max(0)

    def within(self, t, k):
        x, y = self.to_unit(t, k)
        P = np.abs(self.P).reshape(-1, 2).max(0)
        return (x >= -P[0] - 1e-9) & (x <= 1 + P[0] + 1e-9) & (y >= -P[1] - 1e-9) & (y <= 1 + P[1] + 1e-9)


class LiquifyBend(StirBend):
    """The Liquify set to Shape (stage G; rebuilt so, PLAN.md "Liquify Shape rebuild", user 2026-11-17: "what liquify
    does in common image editors reliably"). Its map says where each point GOES (liquify.dab_fwd: the brush moves the
    grid's points like an image editor's mesh): forwards = p + Strength x map(p), looked up, nothing searched for, so
    the outline is exact, the same strokes give the same shape, and nothing outside a stroke's circle moves. It never
    folds by itself (a crossing outline is just lines crossing). Backwards (look: Colours bend too, holes between
    bent notes, a fold of an earlier Shape step's pulled way) = Newton's steps on the map (the Warp's, compiled), from
    where the map there points back: near enough for colours."""

    def __init__(self, lq, r, box, own, ppq):
        from notes import liquify as L
        self.lq, self.r = lq, dict(r or {}, ppq=ppq)
        self.box, self.own = box, own
        f = self.r.get("frame") or rc.SAME
        self.turned = bool(f[1])
        self.flip_x = not self.turned and f[0] < 0
        self.flip_y = not self.turned and f[3] < 0
        # (the map in its own units over its span (the colour box, or grown past it: liquify.span_of); every sum below
        # works in those, so a map that never grew gives the very same numbers)
        self.span = L.span_of(lq)
        self.S = np.array([self.span[2] - self.span[0], self.span[3] - self.span[1]])
        # (moves in the colour box's units; in the map's own units for the sums here: the same while not grown)
        A = L.map_of(L.full(lq)) * (L.full(lq)["strength"] / 100.0)
        self.A = np.ascontiguousarray(A if self.span == L.SPAN else A / self.S, np.float64)
        self.worst = 0.0
        self.st = {"ripple": 0.0, "ripple_len": 100.0}

    @classmethod
    def from_map(cls, A):
        """A bender for a Shape map A alone (its own units, Strength taken in), for looking back through it (the
        freeze tint: liquify.shown_mask): _from / _go only."""
        b = cls.__new__(cls)
        b.A = np.ascontiguousarray(A, np.float64)
        b.span, b.S = (0.0, 0.0, 1.0, 1.0), np.ones(2)
        b._cells = None
        return b

    @staticmethod
    def offsets(lq):
        """The farthest a spot moves each way, in the colour box's units, and how far the map reaches past the box."""
        from notes import liquify as L
        sp = L.span_of(lq)
        P = np.abs(L.map_of(L.full(lq))).reshape(-1, 2).max(0) * (L.full(lq)["strength"] / 100.0)
        return P + np.array([max(-sp[0], sp[2] - 1, 0.0), max(-sp[1], sp[3] - 1, 0.0)])

    def _to(self, x, y):
        return (x - self.span[0]) / self.S[0], (y - self.span[1]) / self.S[1]

    def _back(self, mx, my):
        return self.span[0] + mx * self.S[0], self.span[1] + my * self.S[1]

    def _map_units(self, f, x, y):
        """f (map units -> map units) on colour box spots."""
        x, y = np.array(x, np.float64, ndmin=1), np.array(y, np.float64, ndmin=1)
        shape = x.shape
        x, y = x.ravel(), y.ravel()
        if not len(x):
            return x.reshape(shape), y.reshape(shape)
        if self.span == (0.0, 0.0, 1.0, 1.0):
            ox, oy = f(x, y)
        else:
            ox, oy = self._back(*f(*self._to(x, y)))
        return ox.reshape(shape), oy.reshape(shape)

    def _go(self, mx, my):
        from notes.liquify import sample_fwd
        d = sample_fwd(self.A, mx, my)
        return mx + d[:, 0], my + d[:, 1]

    def _from(self, mx, my):
        """Where each spot's point came from (map units): p + A(p) = spot, Newton's steps from spot - A(spot); past the
        span (nothing moves there) or not settled: that first guess."""
        from notes.liquify import sample_fwd
        from notes.warp import _newton
        d = sample_fwd(self.A, mx, my)
        gx, gy = mx - d[:, 0], my - d[:, 1]
        x, y, err = _newton(self.A, mx, my, False, gx, gy)
        lx, ly = self._go(x, y)  # (the steps read the map's edge past its span; checked with nothing moving there)
        ok = np.isfinite(x) & np.isfinite(y) & (np.hypot(lx - mx, ly - my) <= FOLD)
        bad = np.flatnonzero(~ok)
        if len(bad):  # (a strong twirl: the first guess too far off: the cells covering the spot)
            sx, sy, good = self._seeded(mx[bad], my[bad], gx[bad], gy[bad])
            k = bad[good]
            x[k], y[k], ok[k] = sx[good], sy[good], True
        return np.where(ok, x, gx), np.where(ok, y, gy)

    def _seeded(self, mx, my, gx0, gy0):
        """Spots' homes (map units) the sure way: the moved grid cells whose shown corners' box covers the spot (sorted
        once into buckets of one grid step by where they show), each cell's blend turned round exactly (_in_cell).
        Two homes (a crease of a very strong twirl): the one nearest the first guess (gx0, gy0). -> (x, y, found)."""
        gy, gx = self.A.shape[:2]
        if getattr(self, "_cells", None) is None:
            few = self._seeded_few(mx, my, gx0, gy0)  # (a handful of spots: only the cells that can reach each)
            if few is not None:
                return few
            self._cells = None
            moved = (self.A != 0).any(2)
            cm = moved[:-1, :-1] | moved[1:, :-1] | moved[:-1, 1:] | moved[1:, 1:]
            cj, ci = np.nonzero(cm)
            hx, hy = 1.0 / (gx - 1), 1.0 / (gy - 1)
            xs = [(ci + di) * hx + self.A[cj + dj, ci + di, 0] for dj in (0, 1) for di in (0, 1)]
            ys = [(cj + dj) * hy + self.A[cj + dj, ci + di, 1] for dj in (0, 1) for di in (0, 1)]
            if len(cj):
                b = min(hx, hy)
                X0, X1 = np.min(xs, 0), np.max(xs, 0)
                Y0, Y1 = np.min(ys, 0), np.max(ys, 0)
                x0, y0 = float(X0.min()), float(Y0.min())
                bx0, bx1 = np.floor((X0 - x0) / b).astype(np.int64), np.floor((X1 - x0) / b).astype(np.int64)
                by0, by1 = np.floor((Y0 - y0) / b).astype(np.int64), np.floor((Y1 - y0) / b).astype(np.int64)
                W = int(bx1.max()) + 2
                nx, ny = bx1 - bx0 + 1, by1 - by0 + 1
                cnt = nx * ny
                if cnt.sum() <= CELLS_MOST:
                    idx = np.repeat(np.arange(len(cj)), cnt)
                    k = np.arange(len(idx)) - np.repeat(np.cumsum(cnt) - cnt, cnt)
                    key = (by0[idx] + k // nx[idx]) * W + bx0[idx] + k % nx[idx]
                    o = np.argsort(key, kind="stable")
                    self._cells = cj[idx[o]], ci[idx[o]], key[o], b, x0, y0, W
        n = len(mx)
        ox, oy, found = mx.copy(), my.copy(), np.zeros(n, bool)
        if self._cells is None:
            return ox, oy, found
        CJ, CI, keys, b, x0, y0, W = self._cells
        bx = np.floor((mx - x0) / b).astype(np.int64)
        by = np.floor((my - y0) / b).astype(np.int64)
        inb = (bx >= 0) & (bx < W) & (by >= 0)
        k = np.where(inb, by * W + bx, -1)
        lo, hi = np.searchsorted(keys, k, "left"), np.searchsorted(keys, k, "right")
        c = np.where(inb, hi - lo, 0)
        if not c.any():
            return ox, oy, found
        qi = np.repeat(np.arange(n), c)
        gi = np.arange(c.sum()) - np.repeat(np.cumsum(c) - c - lo, c)
        sx, sy, ok = self._in_cell(CJ[gi], CI[gi], mx[qi], my[qi])
        o = np.flatnonzero(ok)
        far = (sx[o] - gx0[qi[o]]) ** 2 + (sy[o] - gy0[qi[o]]) ** 2
        o = o[np.lexsort((far, qi[o]))]
        first = o[np.r_[True, qi[o][1:] != qi[o][:-1]]] if len(o) else o
        w = qi[first]
        ox[w], oy[w], found[w] = sx[first], sy[first], True
        return ox, oy, found

    def _seeded_few(self, mx, my, gx0, gy0):
        """_seeded for a handful of spots, the same answers without sorting every moved cell (user 2026-11-17: a long
        shape zoomed far in, Cut notes where bent: 7 spots bucketed 770k cells, 0.43 s an update): each spot looks
        only at the cells whose home is within the map's largest offset of it. None = too many spots for this."""
        A = self.A
        gy, gx = A.shape[:2]
        hx, hy = 1.0 / (gx - 1), 1.0 / (gy - 1)
        if not hasattr(self, "_reach"):
            self._reach = float(np.abs(A).max()) if A.size else 0.0
        d = self._reach
        wi, wj = min(int(d / hx) + 3, gx - 1), min(int(d / hy) + 3, gy - 1)
        # (looking through a window is far cheaper a cell than sorting cells into buckets: up to twice the map's cells)
        if len(mx) * (2 * wi + 1) * (2 * wj + 1) > 2 * (gx - 1) * (gy - 1):
            return None
        n = len(mx)
        ox, oy, found = mx.copy(), my.copy(), np.zeros(n, bool)
        for q in range(n):
            i0 = max(int(np.floor((mx[q] - d) / hx)) - 1, 0)
            i1 = min(int(np.ceil((mx[q] + d) / hx)) + 1, gx - 1)
            j0 = max(int(np.floor((my[q] - d) / hy)) - 1, 0)
            j1 = min(int(np.ceil((my[q] + d) / hy)) + 1, gy - 1)
            if i1 <= i0 or j1 <= j0:
                continue
            w = A[j0:j1 + 1, i0:i1 + 1]
            X = np.arange(i0, i1 + 1)[None, :] * hx + w[:, :, 0]
            Y = np.arange(j0, j1 + 1)[:, None] * hy + w[:, :, 1]
            cx = (X[:-1, :-1], X[:-1, 1:], X[1:, :-1], X[1:, 1:])
            cy = (Y[:-1, :-1], Y[:-1, 1:], Y[1:, :-1], Y[1:, 1:])
            mv = (w != 0).any(2)  # (moved cells only, as the buckets hold)
            e = 1e-7  # (a box a hair larger: never drops a cell _in_cell would take)
            hit = ((mv[:-1, :-1] | mv[:-1, 1:] | mv[1:, :-1] | mv[1:, 1:])
                   & (np.minimum(np.minimum(cx[0], cx[1]), np.minimum(cx[2], cx[3])) <= mx[q] + e)
                   & (np.maximum(np.maximum(cx[0], cx[1]), np.maximum(cx[2], cx[3])) >= mx[q] - e)
                   & (np.minimum(np.minimum(cy[0], cy[1]), np.minimum(cy[2], cy[3])) <= my[q] + e)
                   & (np.maximum(np.maximum(cy[0], cy[1]), np.maximum(cy[2], cy[3])) >= my[q] - e))
            cj, ci = np.nonzero(hit)
            if not len(cj):
                continue
            cj, ci = cj + j0, ci + i0
            sx, sy, ok = self._in_cell(cj, ci, np.full(len(cj), mx[q]), np.full(len(cj), my[q]))
            if not ok.any():
                continue
            o = np.flatnonzero(ok)
            # (two homes, a crease: the one nearest the first guess; ties = the first in bucket order (row, column))
            best = o[np.argmin((sx[o] - gx0[q]) ** 2 + (sy[o] - gy0[q]) ** 2)]
            ox[q], oy[q], found[q] = sx[best], sy[best], True
        return ox, oy, found

    def _in_cell(self, cj, ci, qx, qy):
        """Grid cells (row cj, column ci) turned round exactly: the spot (u, v) inside each whose blended corners show
        at (qx, qy). -> (x, y in map units, inside)."""
        A = self.A
        gy, gx = A.shape[:2]
        hx, hy = 1.0 / (gx - 1), 1.0 / (gy - 1)

        def at(j, i):
            return i * hx + A[j, i, 0], j * hy + A[j, i, 1]
        ax, ay = at(cj, ci)
        bx, by = at(cj, ci + 1)
        cx, cy = at(cj + 1, ci + 1)
        dx, dy = at(cj + 1, ci)
        ex, ey = bx - ax, by - ay
        fx, fy = dx - ax, dy - ay
        gx_, gy_ = ax - bx + cx - dx, ay - by + cy - dy
        px, py = qx - ax, qy - ay
        k2 = gx_ * fy - gy_ * fx
        k1 = ex * fy - ey * fx + px * gy_ - py * gx_
        k0 = px * ey - py * ex
        best_u, best_v = np.full(len(qx), np.nan), np.full(len(qx), np.nan)
        lin = np.abs(k2) < 1e-14
        disc = np.sqrt(np.maximum(k1 * k1 - 4 * k0 * k2, 0.0))
        safe2 = np.where(lin, 1.0, 2 * k2)
        safe1 = np.where(np.abs(k1) > 1e-300, k1, 1e-300)
        roots = [np.where(lin, -k0 / safe1, (-k1 - disc) / safe2), np.where(lin, -k0 / safe1, (-k1 + disc) / safe2)]
        tol = 1e-9
        for v in roots:
            den_x, den_y = ex + gx_ * v, ey + gy_ * v
            use_x = np.abs(den_x) >= np.abs(den_y)
            den = np.where(use_x, den_x, den_y)
            u = np.where(use_x, px - fx * v, py - fy * v) / np.where(np.abs(den) > 1e-300, den, 1e-300)
            rx = ax + ex * u + fx * v + gx_ * u * v - qx
            ry = ay + ey * u + fy * v + gy_ * u * v - qy
            ok = ((u >= -tol) & (u <= 1 + tol) & (v >= -tol) & (v <= 1 + tol) & (np.hypot(rx, ry) <= FOLD)
                  & np.isnan(best_u))
            best_u, best_v = np.where(ok, u, best_u), np.where(ok, v, best_v)
        inside = ~np.isnan(best_u)
        u, v = np.where(inside, best_u, 0.0), np.where(inside, best_v, 0.0)
        return (ci + u) * hx, (cj + v) * hy, inside

    def forward_unit(self, qx, qy):
        return self._map_units(self._go, qx, qy)

    def look(self, x, y, st=None):
        return self._map_units(self._from, x, y)

    def look_tk(self, t, k):
        x, y = self.to_unit(t, k)
        x, y = self.look(x, y)
        return self.from_unit(x, y)

    def within(self, t, k):
        x, y = self.to_unit(t, k)
        P = self.offsets(self.lq)
        return (x >= -P[0] - 1e-9) & (x <= 1 + P[0] + 1e-9) & (y >= -P[1] - 1e-9) & (y <= 1 + P[1] + 1e-9)


class Board(dict):
    """A bender for notes/deform.py's line work: its own board (u, v) both ways to (tick, key), every Shape tab's
    bend in turn between. "ripple" / "ripple_len": the shortest wave of the tabs, in % of the board (deform checks a
    line finer for it)."""

    def __init__(self, benders, to_tk, from_tk, wave):
        super().__init__(ripple=100.0 if wave else 0.0, ripple_len=(wave or 0.1) * 100)
        self.benders, self.to_tk, self.from_tk = benders, to_tk, from_tk

    def bend2(self, p):
        p = np.asarray(p, np.float64).reshape(-1, 2)
        if not len(p):
            return p.copy()
        q = self.to_tk(p)
        for b in self.benders:
            q = b.bend2(q)
        return self.from_tk(q)


LINE_DROP = ("k", "smooth", "shape", "pattern", "tumour", "tumours", "sharp")  # (a bent line = its drawn path as
# points: what made the path from its points (arc, perfect freehand, formulas, tumours) is in them already; its Last
# note setting stays, hunt 2026-11-17)
TEXT_DROP = ("text", "polygon")
TEXT_FILLS = ("nested", "tiles", "ripples", "areas")  # (custom.has_nested / has_tiles / has_ripples / has_areas:
# never on text)


PULL = "pull"  # (bent_shape: the lines can't show this bend (a fold), pull the notes instead)
AS_NOTES = "notes"  # (bent_shape: lines this way can't be bent (funnel, Glissando, joined curves, a polyline with
# Last note per dot): its notes bent like pasted notes, hunt 2026-11-17)


def bent_shape(sh, tabs, r, box, own, ppq, wp=None, lq=None):
    """sh (its Recolour already without the Shape tabs) with its lines bent by Stir tabs (filled in, Shape, in tab
    order), then the Warp wp and the Liquify lq set to Shape (clean, or None), None when it can't be bent this way
    (left as it is), or PULL when a step folds the shape (Straight to round, Kaleidoscope, or a point found with no
    single place to go). r: the Recolour (frame / ppq); box, own: the colour box of its notes before the bend
    (recolour.colour_box). Custom shapes: each stroke bent on its own board (curves stay curves, deform.bent_stroke);
    text and polygons become their strokes. Lines: the drawn path. (A folded Warp grid only makes lines cross.)"""
    if any(st["polar"] or st["kaleido"] >= 2 for st in tabs):
        return PULL
    try:
        return _bent_shape(sh, tabs, r, box, own, ppq, wp, lq)
    except Folded:
        return PULL


def benders_of(tabs, wp, r, box, own, ppq, lq=None):
    """Every Shape step's bender in the order they run: the Stir tabs, the Warp, the Liquify."""
    out = [StirBend(st, r, box, own, ppq) for st in tabs]
    if wp is not None:
        out.append(WarpBend(wp, r, box, own, ppq))
    if lq is not None:
        out.append(LiquifyBend(lq, r, box, own, ppq))
    return out


def _bent_shape(sh, tabs, r, box, own, ppq, wp=None, lq=None):
    from notes import deform
    benders = benders_of(tabs, wp, r, box, own, ppq, lq)
    waves = [st["ripple_len"] / 100 for st in tabs if st["ripple"]]
    if wp is not None:  # (lines looked at finer than a grid cell: deform cuts them where the bend curves them)
        waves.append(0.5 / max(wp["cols"], wp["rows"]))
    if lq is not None:  # (a brush reaches a few map steps at least)
        waves.append(4.0 / max(lq["grid"]))
    wave = min(waves) if waves else None
    kind = sh.get("kind")
    if kind == "custom":
        if "notes" in sh or "picture" in sh or sh.get("hz") or sh.get("cut"):
            return None
        from notes.custom import custom_strokes, frame_to_bp, frame_to_uv
        to_uv = frame_to_uv(sh["pts"])
        if to_uv is None:
            return None
        to_bp = frame_to_bp(sh["pts"])

        def to_tk(p):
            b, k = to_bp(p[:, 0], p[:, 1])
            return np.column_stack((b * ppq, k))

        def from_tk(q):
            u, v = to_uv(q[:, 0] / ppq, q[:, 1])
            return np.column_stack((u, v))
        board = Board(benders, to_tk, from_tk, wave)
        if sh.get("text"):  # (its letters' outlines as drawn: plain closed lines on its board)
            strokes = [{"kind": "poly", "pts": from_tk(np.asarray(p, np.float64).reshape(-1, 2) * [ppq, 1]).tolist()}
                       for p in custom_strokes(sh) if len(p) >= 2]
        else:
            strokes = sh["strokes"]
        out = {k: v for k, v in sh.items() if k not in TEXT_DROP}
        if sh.get("text"):  # (inside fills text never takes: its bent strokes must not start using them; heavy
            # sweep 2026-11-17: a Nested 480 left on a text took 36 s here, and made other notes than the text)
            for k in TEXT_FILLS:
                out.pop(k, None)
        out["strokes"] = [deform.bent_stroke(st, board) for st in strokes]
        if sh.get("areas") and not sh.get("text"):  # (the drawer's coloured areas: each one's spot bent along, so it stays in its area)
            a = np.asarray([p[:2] for p in sh["areas"]], np.float64)
            got = board.bend2(a)
            out["areas"] = [[float(u), float(v), p[2]] for (u, v), p in zip(got, sh["areas"])]
        return out
    if kind in ("funnel", "gliss"):
        return AS_NOTES
    if kind in ("line", "poly", "free", "curve", "arc") and not sh.get("cut") and not sh.get("join"):
        from notes.engine import shape_path
        from notes.joined import is_joined
        if is_joined(sh):
            return AS_NOTES
        if kind == "poly" and sh.get("end_dot") and not sh.get("by_snap") and len(sh.get("pts") or ()) > 2 \
                and not (sh.get("shape") or sh.get("pattern")):  # (Last note per dot: each piece its own notes)
            return AS_NOTES
        path = np.asarray(shape_path(sh), np.float64).reshape(-1, 2)
        if len(path) < 2:
            return None
        t0, t1, k0, k1 = box
        w, h = max(t1 - t0, 1.0) / ppq, max(k1 - k0, 1.0)

        def to_tk(p):
            return np.column_stack(((t0 / ppq + p[:, 0] * w) * ppq, k0 + p[:, 1] * h))

        def from_tk(q):
            return np.column_stack(((q[:, 0] / ppq - t0 / ppq) / w, (q[:, 1] - k0) / h))
        board = Board(benders, to_tk, from_tk, wave)
        b0 = np.column_stack(((path[:, 0] - t0 / ppq) / w, (path[:, 1] - k0) / h))
        got = deform._line_pieces(b0, board, deform.TOL)
        pts = np.column_stack((t0 / ppq + got[:, 0] * w, k0 + got[:, 1] * h))
        out = {k: v for k, v in sh.items() if k not in LINE_DROP}
        out.update(kind="free", pts=[[float(a), float(b)] for a, b in pts])
        return out
    return None


# BENT NOTES (stage D: pasted / plain notes; planned-features "EACH NOTE A TINY BOX", user 2026-11-16): each note is a
# box [start, end] x [key - 1/2, key + 1/2]. Its middle line is chopped where the bend curves it (a piece's bent
# middle off the straight between its bent ends by more than CHOP_KEYS of a key or CHOP_PART of a beat = cut in
# two, each half checked again; a note the bend doesn't curve stays whole). Each piece's 4 corners are bent; every
# key row's middle line crossing the bent box = that note's new piece on that row (own velocity / colour / track).
# Touching notes share corners = bend the same = no new gaps or overlaps. Pieces under 1 tick dropped.
CHOP_KEYS = 0.25  # (my pick: a quarter key off the straight)
CHOP_PART = 32  # (or 1/32 beat sideways)
CHOP_DEPTH = 24  # (halvings at most: 2^24 pieces of a note; pieces under 1 tick stop sooner)


KEEP_LEAST = 20000  # (notes: fewer = bent whole every time, nothing kept)
_LQ_KEPT = [None]  # (the last Liquify-only bend's pieces: see _lq_kept)


def _lq_kept(tagged, notes, tracks, bend_tk, ppq, lb):
    """_note_boxes_bent for a Liquify set to Shape alone (lb), keeping its pieces between updates while brushing: a
    note whose box covers no map cell the brush changed is bent exactly as before (the map's look-up there reads the
    very same numbers), so only the notes under changed cells are bent again (leftover 2026-11-17: every note was
    bent again at each update, ~0.3 s of 0.75 at 1 M pasted notes). The very same pieces in the very same order."""
    if lb is None or len(notes) < KEEP_LEAST or not rc.keeping():
        _LQ_KEPT[0] = None
        return _note_boxes_bent(tagged, tracks, bend_tk, ppq)
    gy, gx = lb.A.shape[:2]
    # (where three spots go: the notes' place on the map is a straight-line (affine) one, so these pin it down)
    ref = np.array(lb._to(*lb.to_unit(np.array([0.0, 1000.0, 0.0]), np.array([0.0, 0.0, 10.0]))), np.float64)
    old = _LQ_KEPT[0]
    same = (old is not None and old["ppq"] == ppq and old["A"].shape == lb.A.shape and np.array_equal(old["ref"], ref)
            and old["notes"].shape == notes.shape and np.array_equal(old["notes"], notes)  # (own copy: never the
            # caller's array, which may change)
            and ((tracks is None and old["tracks"] is None) or (tracks is not None and old["tracks"] is not None
                 and np.array_equal(np.asarray(old["tracks"]), np.asarray(tracks)))))
    if same:
        changed = (old["A"] != lb.A).any(2)
        if not changed.any():
            return old["out"], old["tr"]
        # (cells with a changed corner, as sums from the corner: a note's span of cells is asked in one go)
        cell = changed[:-1, :-1] | changed[1:, :-1] | changed[:-1, 1:] | changed[1:, 1:]
        sums = np.zeros((gy, gx), np.int64)
        sums[1:, 1:] = cell.astype(np.int64).cumsum(0).cumsum(1)
        i0, i1, j0, j1 = old["cells"]
        hit = (sums[j1, i1] - sums[j0, i1] - sums[j1, i0] + sums[j0, i0]) > 0
        sub = np.flatnonzero(hit)
        if len(sub) * 2 < len(notes):  # (most notes kept: else bending them all is as quick)
            o2, t2 = _note_boxes_bent(tagged[sub], None if tracks is None else np.asarray(tracks)[sub], bend_tk,
                                      ppq)
            prev = old["out"]
            keep = ~hit[prev[:, -1]]
            rows = prev[keep]
            # (both in note order (_pieces_made sorts by note, row, start) and each note's pieces all from one side:
            # the new ones go in by note number, the very order bending them all gives)
            at = np.searchsorted(rows[:, -1], o2[:, -1], "left")
            out = np.insert(rows, at, o2, axis=0)
            tr = None if tracks is None else np.insert(old["tr"][keep], at, np.asarray(t2))
            _LQ_KEPT[0] = dict(old, A=lb.A.copy(), out=out, tr=tr)
            return out, tr
    out, tr = _note_boxes_bent(tagged, tracks, bend_tk, ppq)
    tr = np.asarray(tr) if tr is not None else None
    _LQ_KEPT[0] = {"notes": notes.copy(), "tracks": None if tracks is None else np.asarray(tracks).copy(), "ppq": ppq,
                   "ref": ref, "A": lb.A.copy(), "out": out, "tr": tr, "cells": _note_cells(notes, lb, gx, gy)}
    return out, tr


def _note_cells(notes, lb, gx, gy):
    """Each note's span of map cells (i0, i1, j0, j1: corners for _lq_kept's sums) its bend's look-ups can read:
    its box's corners on the map, the cells liquify.sample picks there, one more each way (round-off); a note
    nowhere (not finite) = every cell."""
    s, e = notes[:, 0].astype(np.float64), notes[:, 1].astype(np.float64)
    k = notes[:, 2].astype(np.float64)
    mx, my = lb._to(*lb.to_unit(np.concatenate((s, e, e, s)), np.concatenate((k - 0.5, k - 0.5, k + 0.5, k + 0.5))))
    n = len(notes)
    mx, my = np.asarray(mx).reshape(4, n), np.asarray(my).reshape(4, n)
    bad = ~(np.isfinite(mx).all(0) & np.isfinite(my).all(0))

    def cell(v, g):
        return np.minimum((np.clip(np.nan_to_num(v), 0.0, 1.0) * (g - 1)).astype(np.int64), g - 2)

    i0 = np.where(bad, 0, np.maximum(cell(mx.min(0), gx) - 1, 0))
    i1 = np.where(bad, gx - 1, np.minimum(cell(mx.max(0), gx) + 1, gx - 2) + 1)
    j0 = np.where(bad, 0, np.maximum(cell(my.min(0), gy) - 1, 0))
    j1 = np.where(bad, gy - 1, np.minimum(cell(my.max(0), gy) + 1, gy - 2) + 1)
    return tuple(v.astype(np.int32) for v in (i0, i1, j0, j1))  # (half the memory: a grid is never that big)


def note_boxes_bent(notes, tracks, bend_tk, ppq, look_back=None, lengths=False, raw=False, kept=None):
    """notes (n, 4+: start, end, key, velocity, ...) + tracks (or None) bent forwards by bend_tk(t, k) -> (t, k)
    (floats; Folded when it can't): the new notes (sorted by start, key) and their tracks. look_back(t, k): the bend
    backwards; a hole inside a key row whose middle comes from inside a note is filled from that note (two rows'
    boxes chopped at different places leave a sliver between their bent sides): a neighbour from that very note is
    stretched over it instead (one cut, not two: user 2026-11-17, test27's Warp made 648 slivers of 1-15 ticks). Two
    pieces from different key rows overlapping by at most CHOP_PART of a beat (the same seam, cut at different places
    each side) meet halfway instead. lengths: also each new note's own note's length as drawn (third item; the edge
    step tells a note the cutting shortened from one drawn short, hunt 2026-11-17). raw (with lengths): left unsorted
    (edge_from_rows / _by_start sort them: one whole sort less). kept: the LiquifyBend when it is the only bend (its
    pieces kept between brush updates: _lq_kept)."""
    notes = np.asarray(notes, np.int64)
    if not len(notes):
        got = _note_boxes_bent(notes, tracks, bend_tk, ppq)
        return (*got, np.zeros(0, np.int64)) if lengths else got
    # (each note's own key + number carried along as two last columns through every copy, dropped at the end)
    tagged = np.column_stack((notes, notes[:, 2], np.arange(len(notes))))
    out, tr = _lq_kept(tagged, notes, tracks, bend_tk, ppq, kept)
    by_row = look_back is not None and len(out) >= 2
    if kept is not None and not by_row:  # (the kept pieces are shared: the steps below may change them in place;
        # _holes_filled starts with a sorted copy)
        out, tr = out.copy(), (tr.copy() if tr is not None else None)
    if by_row:
        out, tr = _holes_filled(tagged, tracks, out, tr, look_back)
    most = max(int(ppq) // CHOP_PART, 1)
    out, tr = _seams_met(out, tr, most, by_row)
    out, tr = _slivers_joined(out, tr, notes[:, 1] - notes[:, 0], most)
    if not lengths:
        return _by_start(out[:, :-2], tr)
    if raw:  # (edge_from_rows sorts them by row itself: the same notes as sorted by start first)
        return out[:, :-2], (np.asarray(tr) if tr is not None else None), (notes[:, 1] - notes[:, 0])[out[:, -1]]
    o = _order(out[:, 2], out[:, 0])
    return out[o, :-2], (np.asarray(tr)[o] if tr is not None else None), (notes[:, 1] - notes[:, 0])[out[o, -1]]


def _slivers_joined(out, tr, full, most):
    """out (sorted by row + start; last column = each piece's note number) with every piece the CUTTING made shorter
    than `most` ticks (shorter than its note too: a short note as drawn stays) joined into the piece it touches on its
    row (the one before, else the one after); a run of such slivers touching nothing longer = one piece, a lone one
    dropped (user 2026-11-17, test28: slivers along the edge). full: each note's own length."""
    n = len(out)
    if n < 2:
        return out, tr
    fast = compiled("sliver_hosts") if n >= FAST_LEAST else None
    if fast is not None:  # (the compiled join, files/fastloops.sliver_hosts: the very same pieces)
        s, e = out[:, 0].copy(), out[:, 1].copy()
        fp = np.ascontiguousarray(np.asarray(full, np.int64)[out[:, -1]])
        keep = np.empty(n, np.int64)
        m = fast.sliver_hosts(s, e, np.ascontiguousarray(out[:, 2]), fp, int(most), np.empty(n, np.int64),
                              np.empty(n, np.int64), np.empty(n, np.int64), keep)
        if m == n:
            return out, tr
        k = keep[:m]
        out = out[k]
        out[:, 0], out[:, 1] = s[k], e[k]
        return out, (np.asarray(tr)[k] if tr is not None else None)
    L = out[:, 1] - out[:, 0]
    short = (L < most) & (L < full[out[:, -1]])
    if not short.any():
        return out, tr
    run = _running_max(out[:, 1], out[:, 2])
    link = np.r_[False, (out[1:, 2] == out[:-1, 2]) & (out[1:, 0] <= run[:-1])]  # (touching / overlapping the one before)
    grp = np.cumsum(~link)
    idx = np.arange(n)
    before = np.maximum.accumulate(np.where(~short, idx, -1))  # (the last long piece so far)
    after = np.minimum.accumulate(np.where(~short, idx, n)[::-1])[::-1]  # (the next long piece)
    host = idx.copy()
    ok_b = short & (before >= 0) & (grp[np.maximum(before, 0)] == grp)
    host[ok_b] = before[ok_b]
    rest = short & ~ok_b
    ok_a = rest & (after < n) & (grp[np.minimum(after, n - 1)] == grp)
    host[ok_a] = after[ok_a]
    alone = rest & ~ok_a  # (a run of slivers touching no longer piece: joined into its first one)
    if alone.any():
        first = np.r_[0, np.flatnonzero(np.diff(grp)) + 1]
        gfirst = first[grp - 1]
        host[alone] = gfirst[alone]
        size = np.bincount(grp)[grp]
        host[alone & (size == 1)] = -1  # (lone: dropped)
    keep_rows = np.flatnonzero(host >= 0)
    h = host[keep_rows]
    s = out[:, 0].copy()
    e = out[:, 1].copy()
    if len(h) and (np.diff(h) >= 0).all():  # (each host's pieces lie together: one reduce per run, not ufunc.at)
        at = np.flatnonzero(np.r_[True, h[1:] != h[:-1]])
        hh = h[at]
        s[hh] = np.minimum.reduceat(out[keep_rows, 0], at)
        e[hh] = np.maximum.reduceat(out[keep_rows, 1], at)
    else:
        np.minimum.at(s, h, out[keep_rows, 0])
        np.maximum.at(e, h, out[keep_rows, 1])
    keep = host == idx
    out = out[keep].copy()
    out[:, 0], out[:, 1] = s[keep], e[keep]
    return out, (np.asarray(tr)[keep] if tr is not None else None)


def _holes_filled(notes, tracks, out, tr, look_back):
    """out (its last column = each piece's note number) sorted by row + start, holes inside a row whose middle looks
    back into a note filled: by stretching the piece beside it when it's of that note, else a new piece."""
    o = _order(out[:, 0], out[:, 2])
    out = out[o]
    tr = np.asarray(tr)[o] if tr is not None else None
    run = _running_max(out[:, 1], out[:, 2])
    hole = np.nonzero((out[1:, 2] == out[:-1, 2]) & (out[1:, 0] > run[:-1]))[0]
    if not len(hole):
        return out, tr
    a, b = run[hole], out[hole + 1, 0]
    t, k = look_back((a + b) / 2, out[hole, 2].astype(np.float64))
    src = _finder(notes)(t, k)
    fill = src >= 0
    if not fill.any():
        return out, tr
    hole, a, b, j = hole[fill], a[fill], b[fill], src[fill]
    out = out.copy()
    left = (out[hole, -1] == j) & (out[hole, 1] == a)  # (the piece before: of that note, ending at the hole)
    right = ~left & (out[hole + 1, -1] == j)  # (the piece after: of that note)
    out[hole[left], 1] = b[left]
    out[hole[right] + 1, 0] = a[right]
    new = ~left & ~right
    if new.any():
        add = notes[j[new]].copy()
        add[:, 0], add[:, 1], add[:, 2] = a[new], b[new], out[hole[new], 2]
        out = np.concatenate((out, add))
        if tr is not None:
            tr = np.concatenate((tr, np.asarray(tracks)[j[new]]))
    o = _order(out[:, 0], out[:, 2])
    return out[o], (tr[o] if tr is not None else None)


def edge_from_rows(made, tracks, line, most=1, stretch=True, full=None):
    """Cut notes `made` (+ tracks) given each key row's ends of `line` (the same shape made the outline way): every
    row's merged spans of line's notes; pieces clipped to them, the first piece of a span starting at its start, the
    last ending at its end, no gap inside a span, an end piece shorter than `most` joined into its neighbour; pieces
    outside every span dropped; a span no piece reaches = one note from the row's nearest piece (whole span). Rows line
    doesn't have are dropped (stray pieces past the outline). Whole-array work (a Twist 50 outline has thousands of
    spans); _edge_from_rows_plain = the same rules one span at a time. stretch False (Spam, outlines, patterns: their
    row ends are real notes; user 2026-11-17 picked "clip to the outline" from test29's previews): only clipped, pieces
    past the outline gone, nothing stretched, no gap filled, no span given a note. full: each piece's own note's length
    as drawn (note_boxes_bent lengths=): an end note is dropped as short only when it was SHORTENED (an Outline fill's
    one-tick walls are drawn that short: hunt 2026-11-17 #1). made may come unsorted (note_boxes_bent raw)."""
    if not len(made) or not len(line):
        return _by_start(made, np.asarray(tracks) if tracks is not None else None) if len(made) else (made, tracks)
    tr = np.asarray(tracks) if tracks is not None else None
    o = _order(made[:, 0], made[:, 2])
    made, tr = made[o], (tr[o] if tr is not None else None)
    full = np.asarray(full)[o] if full is not None else None
    line = line[_order(line[:, 0], line[:, 2])]
    lr, ls = line[:, 2], line[:, 0]
    le = _running_max(line[:, 1], lr)
    at = np.flatnonzero(np.r_[True, (lr[1:] != lr[:-1]) | (ls[1:] > le[:-1])])
    sp_row, sp_a, sp_b = lr[at], ls[at], np.maximum.reduceat(line[:, 1], at)
    t0 = int(min(made[:, 0].min(), sp_a.min()))
    W = int(max(made[:, 1].max(), sp_b.max())) - t0 + 2
    r0 = int(min(made[:, 2].min(), sp_row.min()))
    if (int(max(made[:, 2].max(), sp_row.max())) - r0 + 1) * W >= 1 << 62:
        return _edge_from_rows_plain(made, tr, line, most, stretch, full)
    rk = (made[:, 2] - r0) * W
    fast = compiled("edge_rows") if len(made) >= FAST_LEAST else None
    if fast is not None:  # (the compiled pieces x spans, files/fastloops.edge_rows: the very same notes)
        q, qt, n_in = _edge_fast(fast, made, tr, full, sp_row, sp_a, sp_b, most, stretch)
        if not stretch:
            return _by_start(q, qt)
    else:
        q, qt, n_in = _edge_pairs(made, tr, full, sp_row, sp_a, sp_b, most, stretch, rk, r0, t0, W)
        if not stretch:
            return q, qt
    empty = np.flatnonzero(n_in == 0)
    if len(empty):  # (a span no piece reaches: one note there from the row's nearest piece)
        mid = (sp_a[empty] + sp_b[empty]) // 2
        j = np.searchsorted(rk + made[:, 0] - t0, (sp_row[empty] - r0) * W + mid - t0)
        cands = np.stack((np.clip(j - 1, 0, len(made) - 1), np.clip(j, 0, len(made) - 1)), 1)
        same = made[cands, 2] == sp_row[empty][:, None]
        dist = np.where(same, np.abs((made[cands, 0] + made[cands, 1]) // 2 - mid[:, None]), np.iinfo(np.int64).max)
        best = cands[np.arange(len(empty)), np.argmin(dist, 1)]
        ok = same.any(1)
        if not ok.all():  # (a row the outline has but no piece reached (an edge row half a key off: a turn round a
            # corner, hunt 2026-11-17 #2): from the nearest piece in the rows next to it)
            for i in np.flatnonzero(~ok):
                for d in (1, -1, 2, -2):
                    r = sp_row[empty[i]] + d
                    lo_i = np.searchsorted(made[:, 2], r, "left")
                    hi_i = np.searchsorted(made[:, 2], r, "right")
                    if hi_i > lo_i:
                        seg = made[lo_i:hi_i]
                        best[i] = lo_i + int(np.argmin(np.abs((seg[:, 0] + seg[:, 1]) // 2 - mid[i])))
                        ok[i] = True
                        break
        add = made[best[ok]].copy()
        add[:, 0], add[:, 1], add[:, 2] = sp_a[empty[ok]], sp_b[empty[ok]], sp_row[empty[ok]]
        q = np.concatenate((q, add))
        if qt is not None:
            qt = np.concatenate((qt, tr[best[ok]]))
    return _by_start(q, qt)


def _edge_fast(fast, made, tr, full, sp_row, sp_a, sp_b, most, stretch):
    """edge_from_rows' pieces x spans by the compiled edge_rows (made sorted by row, then start) -> (pieces, their
    tracks, each span's piece count before drops)."""
    n_in = np.zeros(len(sp_a), np.int64)
    least = int(np.median(made[:, 1] - made[:, 0])) // 2 if not stretch else 0
    fl = np.ascontiguousarray(full, np.int64) if full is not None else np.zeros(0, np.int64)
    ms, me, mr = (np.ascontiguousarray(made[:, c]) for c in (0, 1, 2))
    run = np.empty(len(made), np.int64)
    sp = [np.ascontiguousarray(x, np.int64) for x in (sp_row, sp_a, sp_b)]
    out = np.empty((3, len(made) + len(sp_a) + 16), np.int64)
    m = fast.edge_rows(ms, me, mr, run, fl, *sp, int(most), least, bool(stretch), n_in, out)
    if m >= 1 << 40:  # (more pairs than room: once more with the room it asked for)
        out = np.empty((3, m - (1 << 40)), np.int64)
        m = fast.edge_rows(ms, me, mr, run, fl, *sp, int(most), least, bool(stretch), n_in, out)
    pi = out[0, :m]
    q = made[pi]
    q[:, 0], q[:, 1] = out[1, :m], out[2, :m]
    return q, (tr[pi] if tr is not None else None), n_in


def _edge_pairs(made, tr, full, sp_row, sp_a, sp_b, most, stretch, rk, r0, t0, W):
    """edge_from_rows' pieces x spans, whole-array NumPy (edge_rows' twin) -> (pieces, tracks, each span's piece
    count before drops); clip mode (stretch False): the finished notes sorted by start."""
    ka = (sp_row - r0) * W + (sp_a - t0)
    kb = (sp_row - r0) * W + (sp_b - t0)
    first = np.searchsorted(kb, rk + made[:, 0] - t0, "right")  # (the first span ending after the piece starts)
    last = np.searchsorted(ka, rk + made[:, 1] - t0, "left") - 1  # (the last span starting before it ends)
    cnt = np.maximum(last - first + 1, 0)
    total = int(cnt.sum())
    pi = np.repeat(np.arange(len(made)), cnt)
    si = np.repeat(first, cnt) + (np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt))
    if len(si) > 1 and not (np.diff(si) >= 0).all():  # (by span, then start; already so unless a piece spans two)
        o = _order(pi, si)
        pi, si = pi[o], si[o]
    q = made[pi]
    qt = tr[pi] if tr is not None else None
    q[:, 0] = np.maximum(q[:, 0], sp_a[si])
    q[:, 1] = np.minimum(q[:, 1], sp_b[si])
    if not stretch:  # (a note the outline trims below half its own length: dropped, no slivers at the edge; user
        # 2026-11-17 after trying the clip alone)
        # (+ a row's first / last note shorter than half a usual note of the shape (the median: Spam's gate) dropped,
        # pieces the bend cut short at the edge too; inside they stay, they're the bend's own cuts)
        L = q[:, 1] - q[:, 0]
        keep = 2 * L >= made[pi, 1] - made[pi, 0]
        if len(q):
            least = int(np.median(made[:, 1] - made[:, 0])) // 2
            end = np.r_[True, si[1:] != si[:-1]] | np.r_[si[1:] != si[:-1], True]
            short = L < least
            if full is not None:
                short &= L < full[pi]  # (only notes the cutting / trimming shortened)
            keep &= ~end | ~short
        return (*_by_start(q[keep], qt[keep] if qt is not None else None), None)
    if len(q):
        fs = np.r_[True, si[1:] != si[:-1]]
        lst = np.r_[si[1:] != si[:-1], True]
        q[fs, 0] = sp_a[si[fs]]
        q[lst, 1] = sp_b[si[lst]]
        nl = np.flatnonzero(~lst)  # (a solid Fill row: no gaps inside a span)
        q[nl, 1] = np.maximum(q[nl, 1], q[nl + 1, 0])
        n_in = np.bincount(si, minlength=len(sp_a))
        L = q[:, 1] - q[:, 0]
        dropf = fs & (L < most) & (n_in[si] > 1)  # (an end piece shorter than `most`: into its neighbour)
        f = np.flatnonzero(dropf)
        q[f + 1, 0] = q[f, 0]
        had = np.zeros(len(sp_a), bool)
        had[si[f]] = True
        L = q[:, 1] - q[:, 0]
        dropl = lst & ~dropf & (L < most) & ((n_in - had)[si] > 1)
        g = np.flatnonzero(dropl)
        q[g - 1, 1] = q[g, 1]
        keep = ~(dropf | dropl)
        q = q[keep]
        qt = qt[keep] if qt is not None else None
    else:
        n_in = np.zeros(len(sp_a), np.int64)
    return q, qt, n_in


def _edge_from_rows_plain(made, tracks, line, most=1, stretch=True, full=None):
    """edge_from_rows one row / span at a time (when the ticks are too far apart for one sort key)."""
    if not len(made) or not len(line):
        return made, tracks
    tr = np.asarray(tracks) if tracks is not None else None
    o = _order(made[:, 0], made[:, 2])
    made, tr = made[o], (tr[o] if tr is not None else None)
    full = np.asarray(full)[o] if full is not None else None
    lo_ = _order(line[:, 0], line[:, 2])
    line = line[lo_]
    rows_m, at_m = np.unique(made[:, 2], return_index=True)
    end_m = np.r_[at_m[1:], len(made)]
    where = dict(zip(rows_m.tolist(), zip(at_m.tolist(), end_m.tolist())))
    rows_l, at_l = np.unique(line[:, 2], return_index=True)
    end_l = np.r_[at_l[1:], len(line)]
    outs, trs = [], []
    for k, a0, a1 in zip(rows_l.tolist(), at_l.tolist(), end_l.tolist()):
        if k not in where:
            continue
        m0, m1 = where[k]
        P = made[m0:m1]
        T = tr[m0:m1] if tr is not None else None
        # (the outline's spans on this row, touching / overlapping notes merged)
        s, e = line[a0:a1, 0], np.maximum.accumulate(line[a0:a1, 1])
        new = np.r_[True, s[1:] > e[:-1]]
        starts = s[new]
        ends = np.maximum.reduceat(line[a0:a1, 1], np.flatnonzero(new))
        for a, b in zip(starts.tolist(), ends.tolist()):
            hit = np.flatnonzero((P[:, 1] > a) & (P[:, 0] < b))
            if not stretch:  # (only clipped; trimmed below half its length = dropped)
                q = P[hit].copy()
                q[:, 0] = np.maximum(q[:, 0], a)
                q[:, 1] = np.minimum(q[:, 1], b)
                L = q[:, 1] - q[:, 0]
                keep = 2 * L >= P[hit, 1] - P[hit, 0]
                if len(q):  # (the span's first / last note: at least half a usual note, as edge_from_rows)
                    least = int(np.median(made[:, 1] - made[:, 0])) // 2
                    short = L < least
                    if full is not None:
                        short &= L < full[m0:m1][hit]
                    keep[[0, -1]] &= ~short[[0, -1]]
                outs.append(q[keep])
                if T is not None:
                    trs.append(T[hit][keep])
                continue
            if not len(hit):
                j = int(np.argmin(np.abs((P[:, 0] + P[:, 1]) / 2 - (a + b) / 2)))
                piece = P[j:j + 1].copy()
                piece[0, 0], piece[0, 1] = a, b
                outs.append(piece)
                if T is not None:
                    trs.append(T[j:j + 1])
                continue
            q = P[hit].copy()
            qt = T[hit] if T is not None else None
            q[:, 0] = np.maximum(q[:, 0], a)
            q[:, 1] = np.minimum(q[:, 1], b)
            q[0, 0] = a
            q[-1, 1] = b
            if len(q) > 1:  # (a solid Fill row: no gaps inside a span, each piece reaches the next one)
                q[:-1, 1] = np.maximum(q[:-1, 1], q[1:, 0])
            keep = q[:, 1] > q[:, 0]
            q = q[keep]
            qt = qt[keep] if qt is not None else None
            # (an end piece clipped shorter than `most`: joined into its neighbour, no stray sliver at the edge)
            if len(q) > 1 and q[0, 1] - q[0, 0] < most:
                q[1, 0] = q[0, 0]
                q, qt = q[1:], (qt[1:] if qt is not None else None)
            if len(q) > 1 and q[-1, 1] - q[-1, 0] < most:
                q[-2, 1] = q[-1, 1]
                q, qt = q[:-1], (qt[:-1] if qt is not None else None)
            outs.append(q)
            if qt is not None:
                trs.append(qt)
    out = np.concatenate(outs) if outs else made[:0]
    tro = (np.concatenate(trs) if trs else tr[:0]) if tr is not None else None
    return _by_start(out, tro)


def _seams_met(out, tr, most, by_row=False):
    """Two pieces next to each other on a row, from notes of DIFFERENT key rows, overlapping by 1..most ticks (one
    seam between two bent rows, cut at different places on each side) meet halfway: one cut, no overlap. Real
    overlaps (more than `most`, or notes of one key row, as a paste may have) stay. by_row: out is sorted by row +
    start already."""
    if len(out) < 2:
        return out, tr
    if by_row:
        out = out.copy()
    else:
        o = _order(out[:, 0], out[:, 2])
        out = out[o].copy()
        tr = np.asarray(tr)[o] if tr is not None else None
    over = out[:-1, 1] - out[1:, 0]
    seam = np.flatnonzero((out[1:, 2] == out[:-1, 2]) & (out[1:, -2] != out[:-1, -2]) & (over > 0) & (over <= most)
                          & (out[:-1, 0] < out[1:, 0]) & (out[:-1, 1] < out[1:, 1]))
    if len(seam):
        # (a piece with seams on both sides: each side moves only its own end)
        mid = (out[seam, 1] + out[seam + 1, 0]) // 2
        out[seam, 1] = mid
        out[seam + 1, 0] = mid
        ok = out[:, 1] > out[:, 0]
        if not ok.all():
            out = out[ok]
            tr = tr[ok] if tr is not None else None
    return out, tr


def _order(*keys):
    """np.lexsort(keys) (the LAST key first), the very same order, but as one stable sort of a single int64 key when
    the keys' ranges fit (Cut notes where bent: a strong Twist's 2.6 M pieces sorted 3 times, 375 -> 110 ms each;
    speed check 2026-11-17). Integer keys only."""
    n = len(keys[0])
    if n < 2:
        return np.arange(n)
    key = None
    width = 1
    for k in keys:  # (the first key changes fastest, as lexsort's)
        lo, hi = int(k.min()), int(k.max())
        span = hi - lo + 1
        if width * span >= 1 << 62:
            return np.lexsort(keys)
        part = (k - lo).astype(np.int64)
        key = part if key is None else key + part * width
        width *= span
    if n >= RADIX_LEAST and not (key[1:] < key[:-1]).any():  # (in order already: a stable sort keeps it)
        return np.arange(n)
    if n >= RADIX_LEAST:
        fast = compiled("radix_order")
        if fast is not None:  # (the compiled stable radix sort: the very same order, ~4x quicker at 3 M)
            return fast.radix_order(key, max(int(width - 1).bit_length(), 1))  # (key: our own copy, used up)
    return np.argsort(key, kind="stable")


RADIX_LEAST = 4096  # (pieces: fewer = NumPy's sort, no call cost)


def _by_start(out, tr):
    o = _order(out[:, 2], out[:, 0])
    return out[o], (tr[o] if tr is not None else None)


def _note_boxes_bent(notes, tracks, bend_tk, ppq):
    notes = np.asarray(notes, np.int64)
    if not len(notes):
        return notes, tracks
    src = np.arange(len(notes))
    s = notes[:, 0].astype(np.float64)
    e = notes[:, 1].astype(np.float64)
    tol_t = max(ppq / CHOP_PART, 1.0)
    done = []
    for depth in range(CHOP_DEPTH + 1):
        if not len(src):
            break
        k = notes[src, 2].astype(np.float64)
        m = (s + e) / 2
        n = len(src)
        bt, bk = bend_tk(np.concatenate((s, m, e)), np.concatenate((k, k, k)))
        dt = bt[n:2 * n] - (bt[:n] + bt[2 * n:]) / 2
        dk = bk[n:2 * n] - (bk[:n] + bk[2 * n:]) / 2
        cut = ((np.abs(dk) > CHOP_KEYS) | (np.abs(dt) > tol_t)) & (e - s >= 2) & (depth < CHOP_DEPTH)
        keep = ~cut
        done.append((src[keep], s[keep], e[keep]))
        src = np.concatenate((src[cut], src[cut]))
        s, e = np.concatenate((s[cut], m[cut])), np.concatenate((m[cut], e[cut]))
    src = np.concatenate([d[0] for d in done])
    s = np.concatenate([d[1] for d in done])
    e = np.concatenate([d[2] for d in done])
    k = notes[src, 2].astype(np.float64)
    n = len(src)
    # (the 4 corners, in order round the box: start-low, end-low, end-high, start-high)
    ct = np.concatenate((s, e, e, s))
    ck = np.concatenate((k - 0.5, k - 0.5, k + 0.5, k + 0.5))
    bt, bk = bend_tk(ct, ck)
    X = bt.reshape(4, n).T
    Y = bk.reshape(4, n).T
    return _boxes_rows(notes, tracks, src, X, Y)


def _boxes_rows(notes, tracks, src, X, Y):
    """The bent boxes (corners X, Y: (n, 4) in order round each) cut by every key row's middle line (key r at y = r;
    a row counts for an edge from its low end up to, not at, its high end: shared edges give one piece, never two).
    Each crossing pair = one note piece."""
    lo = np.ceil(Y.min(1))
    hi = np.ceil(Y.max(1))
    fine = np.isfinite(lo) & np.isfinite(hi) & np.isfinite(X).all(1)
    lo = np.where(fine, lo, 0.0)
    cnt = np.where(fine, np.maximum(hi - lo, 0), 0).astype(np.int64)
    if not cnt.sum():
        return notes[:0], (np.asarray(tracks)[:0] if tracks is not None else None)
    fast = compiled("box_rows")
    if fast is not None:  # (the compiled cut, files/fastloops.box_rows: the very same pieces)
        total = int(cnt.sum())
        got = np.empty((4, 2 * total), np.int64)
        m = fast.box_rows(np.ascontiguousarray(X, np.float64).ravel(), np.ascontiguousarray(Y, np.float64).ravel(),
                          lo.astype(np.int64), cnt, got)
        if m >= 0:
            na, nb = m >> 32, m & 0xFFFFFFFF
            got = np.concatenate((got[:, :na], got[:, total:total + nb]), 1)
            return _pieces_made(notes, tracks, src, got[0], got[1], got[2], got[3])
    box = np.repeat(np.arange(len(src)), cnt)
    first = np.repeat(np.cumsum(cnt) - cnt, cnt)
    r = lo[box] + (np.arange(len(box)) - first)
    xs = np.full((len(box), 4), np.nan)
    for i in range(4):
        j = (i + 1) % 4
        y0, y1 = Y[box, i], Y[box, j]
        x0, x1 = X[box, i], X[box, j]
        hit = ((y0 <= r) & (r < y1)) | ((y1 <= r) & (r < y0))
        with np.errstate(invalid="ignore", divide="ignore"):
            x = x0 + (r - y0) * (x1 - x0) / (y1 - y0)
        xs[:, i] = np.where(hit, x, np.nan)
    xs.sort(1)  # (NaN last: 0, 2 or 4 crossings)
    pieces = []
    for a in (0, 2):
        ok = np.isfinite(xs[:, a]) & np.isfinite(xs[:, a + 1])
        t0 = np.rint(xs[ok, a]).astype(np.int64)
        t1 = np.rint(xs[ok, a + 1]).astype(np.int64)
        pieces.append((box[ok], r[ok].astype(np.int64), t0, t1))
    b = np.concatenate([p[0] for p in pieces])
    rr = np.concatenate([p[1] for p in pieces])
    t0 = np.concatenate([p[2] for p in pieces])
    t1 = np.concatenate([p[3] for p in pieces])
    good = t1 - t0 >= 1  # (pieces under 1 tick dropped)
    return _pieces_made(notes, tracks, src, b[good], rr[good], t0[good], t1[good])


def _pieces_made(notes, tracks, src, b, rr, t0, t1):
    """The cut pieces (box b, row rr, ticks t0 .. t1) as notes of their own note (src[b]), as cut (not sorted:
    note_boxes_bent sorts once). Pieces of one note that land on one row touching each other = one piece again: a
    cut only where the note really goes to another row (Liquify Shape rebuild: halving left cuts where nothing bent,
    user 2026-11-17 "i expected it to get chopped at that area")."""
    srcs = src[b]
    if len(srcs) > 1:
        o = _order(t0, rr, srcs)
        srcs, rr, t0, t1 = srcs[o], rr[o], t0[o], t1[o]
        new = np.r_[True, (srcs[1:] != srcs[:-1]) | (rr[1:] != rr[:-1]) | (t0[1:] != t1[:-1])]
        if not new.all():
            at = np.flatnonzero(new)
            t1 = np.maximum.reduceat(t1, at)
            srcs, rr, t0 = srcs[at], rr[at], t0[at]
    out = notes[srcs].copy()
    out[:, 0], out[:, 1], out[:, 2] = t0, t1, rr
    return out, (np.asarray(tracks)[srcs] if tracks is not None else None)


# PULLED NOTES (folding bends: Straight to round, Kaleidoscope, a strong Twist along a line / Ripple...): every key
# row of the piano roll looks back, spot by spot, at where the bend brings it from (Stir's own way, like the colours)
# and takes the note there. A run of spots coming from one note = one new note on that row with its velocity and
# colour; joined = a drawn shape's touching runs of one colour and velocity become one note (as its lines would make
# them), else (pasted notes, stage D) each note's pieces stay its own.
FIND_COLS = 1441  # (the first look for where the notes land: spots across the search box per key row)
LEAST_STEP = 1  # (ticks)


def _finder(notes):
    """which(t, k) -> the note (index into notes) covering each spot (tick, key as floats), -1 for none."""
    key = np.floor(notes[:, 2]).astype(np.int64)
    order = np.lexsort((notes[:, 0], key))
    s, e, kk = notes[order, 0], notes[order, 1], key[order]
    lo = int(s.min()) - 1
    span = int(e.max()) - lo + 2
    comb = (kk - int(kk.min())) * span + (s - lo)
    kmin, kmax = int(kk.min()), int(kk.max())
    # (each note's end, with earlier notes' ends carried along: a long note hidden under a later short one still found)
    run_end = _running_max(e, kk)

    def which(t, k):
        t, k = np.asarray(t, np.float64), np.asarray(k, np.float64)
        fine = np.isfinite(t) & np.isfinite(k) & (np.abs(t) < 1e15) & (np.abs(k) < 1e9)
        t, k = np.where(fine, t, 0.0), np.where(fine, k, -1e9)
        r = np.floor(k + 0.5).astype(np.int64)
        tt = np.floor(t).astype(np.int64)
        ok = fine & (r >= kmin) & (r <= kmax) & (tt >= lo) & (tt - lo < span)
        q = np.where(ok, (r - kmin) * span + (tt - lo), 0)
        j = np.searchsorted(comb, q, "right") - 1
        jj = np.clip(j, 0, len(comb) - 1)
        hit = ok & (j >= 0) & (kk[jj] == r) & (run_end[jj] > tt)
        # (the latest note started at or before the spot; if it ended already, an earlier longer one may cover it)
        good = hit & (e[jj] > tt)
        out = np.where(good, order[jj], -1)
        back = np.nonzero(hit & ~good)[0]
        for i in back:  # (rare: notes overlapping on one key)
            a = int(jj[i])
            while a >= 0 and kk[a] == r[i]:
                if s[a] <= tt[i] < e[a]:
                    out[i] = order[a]
                    break
                a -= 1
        return out
    return which


def _running_max(e, kk):
    """Each note's end, the most of it and every earlier note's on its key row (rows sorted by start)."""
    out = e.copy()
    if not len(e):
        return out
    starts = np.r_[0, np.nonzero(np.diff(kk))[0] + 1]
    for a, b in zip(starts, np.r_[starts[1:], len(e)]):
        out[a:b] = np.maximum.accumulate(e[a:b])
    return out


def pulled(notes, tracks, look_back, box, ppq, joined=True, within=None):
    """The notes (and tracks) a bend makes of notes (n, 4+ columns: start, end, key, velocity, ...): look_tk(t, k)
    -> (t, k) where each spot of the piano roll comes from. Every key row that can see a note is looked at every
    `step` ticks (finer than the shortest note or gap), each change of note found to the tick. joined: touching runs
    of one track + velocity on a row become one note."""
    notes = np.asarray(notes, np.int64)
    if not len(notes):
        return notes, tracks
    which = _finder(notes)

    def look_tk(t, k):  # (spots past the reach look at nowhere: NaN finds no note)
        if within is None:
            return look_back(t, k)
        ok = within(t, k)
        a, b = look_back(t, k)
        return np.where(ok, a, np.nan), np.where(ok, b, np.nan)
    t0, t1, k0, k1 = box
    W, H = max(t1 - t0, 1), max(k1 - k0, 1)
    # (where can the notes be seen? a coarse look over the box 1 box out each way, key row by key row)
    rows = np.arange(int(math.floor(k0 - H - 1)), int(math.ceil(k1 + H + 1)) + 1)
    cols = np.linspace(t0 - W, t1 + W, FIND_COLS)
    T, K = np.meshgrid(cols, rows)
    st, sk = look_tk(T.ravel(), K.ravel().astype(np.float64))
    seen = (which(st, sk) >= 0).reshape(T.shape)
    lens = np.r_[notes[:, 1] - notes[:, 0]]
    gaps = _row_gaps(notes)
    small = int(min(lens.min(), gaps.min() if len(gaps) else lens.min()))
    step = max(LEAST_STEP, min(int(ppq) // 32, small // 2))
    # (every row that sees a note scanned in ONE look, each change then halved to the tick, all rows together: the
    # same spots looked at as row by row, ~600-2,000 small looks a change before, speed leftover 2026-11-17)
    live = np.nonzero(seen.any(1))[0]
    if not len(live):
        return notes[:0], (tracks[:0] if tracks is not None else None)
    sv = seen[live]
    first = sv.argmax(1)
    last = sv.shape[1] - 1 - sv[:, ::-1].argmax(1)
    a = np.floor(cols[np.maximum(first - 1, 0)]).astype(np.int64)
    b = np.ceil(cols[np.minimum(last + 1, len(cols) - 1)]).astype(np.int64) + 1
    cnt = (b + step - a + step - 1) // step  # (np.arange(a, b + step, step)'s length)
    row_of = np.repeat(np.arange(len(live)), cnt)
    start_at = np.cumsum(cnt) - cnt  # (each row's first spot)
    xs = a[row_of] + (np.arange(len(row_of)) - start_at[row_of]) * step
    keys_f = rows[live].astype(np.float64)
    ids = which(*look_tk(xs.astype(np.float64) + 0.5, keys_f[row_of]))
    ch = np.nonzero((ids[1:] != ids[:-1]) & (row_of[1:] == row_of[:-1]))[0]
    # (each change found to the tick: halving between the two spots)
    lo_, hi_ = xs[ch].copy(), xs[ch + 1].copy()
    left = ids[ch]
    ch_key = keys_f[row_of[ch]]
    while True:
        open_ = hi_ - lo_ > 1
        if not open_.any():
            break
        mid = (lo_ + hi_) // 2
        got = which(*look_tk(mid.astype(np.float64) + 0.5, ch_key))
        same = got == left
        lo_ = np.where(open_ & same, mid, lo_)
        hi_ = np.where(open_ & ~same, mid, hi_)
    # (the runs: each row's first spot, then each change; a run ends where the next one of its row starts, the last
    # one a step past the row's last spot)
    pos = np.concatenate((start_at, ch + 1))
    beg = np.concatenate((xs[start_at], hi_))
    o = np.argsort(pos, kind="stable")
    pos, beg = pos[o], beg[o]
    r_of = row_of[pos]
    row_end = xs[start_at + cnt - 1] + step
    nxt = np.r_[beg[1:], 0]
    same_row = np.r_[r_of[1:] == r_of[:-1], False]
    end = np.where(same_row, nxt, row_end[r_of])
    seg = ids[pos]
    ok = (seg >= 0) & (end > beg)
    if not ok.any():
        return notes[:0], (tracks[:0] if tracks is not None else None)
    o = np.column_stack((beg[ok], end[ok], rows[live][r_of[ok]].astype(np.int64), seg[ok])).astype(np.int64)
    src = o[:, 3]
    res = notes[src].copy()
    res[:, 0], res[:, 1], res[:, 2] = o[:, 0], o[:, 1], o[:, 2]
    tr = np.asarray(tracks)[src] if tracks is not None else None
    if joined and len(res) > 1:  # (touching pieces of one row, one track and one velocity: one note; only pieces
        # of notes from DIFFERENT key rows (where the bend crosses rows): a spam row's own touching notes stay apart)
        rows_ = notes[src, 2]
        same = ((res[1:, 2] == res[:-1, 2]) & (res[1:, 0] == res[:-1, 1]) & (res[1:, 3] == res[:-1, 3])
                & (rows_[1:] != rows_[:-1]))
        if tr is not None:
            same &= tr[1:] == tr[:-1]
        if res.shape[1] > 4:
            same &= (res[1:, 4:] == res[:-1, 4:]).all(1)
        first = np.r_[True, ~same]
        grp = np.cumsum(first) - 1
        ends = np.zeros(int(grp[-1]) + 1, np.int64)
        np.maximum.at(ends, grp, res[:, 1])
        res = res[first]
        res[:, 1] = ends
        tr = tr[first] if tr is not None else None
    return res, tr


def _row_gaps(notes):
    """The empty stretches between notes on each key row (ticks), > 0 only."""
    o = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, k = notes[o, 0], notes[o, 1], notes[o, 2]
    g = s[1:] - _running_max(e, k)[:-1]  # (each row's own ends: an earlier row's never counts)
    g = g[(k[1:] == k[:-1]) & (g > 0)]
    return g


def solve(look, qx, qy, x, y, seed=None):
    """Points (x, y) moved until look(x, y) = (qx, qy): Newton's steps (the slopes measured a hair either way),
    each step halved while it makes things worse. A point no step brings near enough keeps its best try; points
    that stay far from home (a fold: two places look at the same spot) then try their found neighbours' answers."""
    x, y = x.copy(), y.copy()
    fx, fy = look(x, y)
    err = np.hypot(fx - qx, fy - qy)
    for _ in range(STEPS):
        todo = np.nonzero(err > EPS)[0]
        if not len(todo):
            break
        xs, ys, gx, gy = x[todo], y[todo], fx[todo], fy[todo]
        h = 1e-7 * np.maximum(1.0, np.hypot(xs, ys))
        ax, ay = look(xs + h, ys)
        bx, by = look(xs, ys + h)
        j11, j21, j12, j22 = (ax - gx) / h, (ay - gy) / h, (bx - gx) / h, (by - gy) / h
        det = j11 * j22 - j12 * j21
        det = np.where(np.abs(det) < 1e-14, 1e-14, det)
        rx, ry = gx - qx[todo], gy - qy[todo]
        sx, sy = -(j22 * rx - j12 * ry) / det, -(j11 * ry - j21 * rx) / det
        lam = np.ones(len(todo))
        left = np.arange(len(todo))
        for _ in range(10):
            nx, ny = xs[left] + lam[left] * sx[left], ys[left] + lam[left] * sy[left]
            tx, ty = look(nx, ny)
            e = np.hypot(tx - qx[todo[left]], ty - qy[todo[left]])
            ok = e < err[todo[left]]
            got = todo[left[ok]]
            x[got], y[got], fx[got], fy[got], err[got] = nx[ok], ny[ok], tx[ok], ty[ok], e[ok]
            left = left[~ok]
            if not len(left):
                break
            lam[left] *= 0.5
    bad = np.nonzero(err > 1e-6)[0]
    if len(bad) and len(bad) < len(x):  # (from a found neighbour: along a line, the next point is near)
        good = np.nonzero(err <= 1e-6)[0]
        for _ in range(2):
            near = good[np.clip(np.searchsorted(good, bad), 0, len(good) - 1)]
            gx, gy = x[near] + (qx[bad] - qx[near]), y[near] + (qy[bad] - qy[near])
            sx_, sy_ = solve_plain(look, qx[bad], qy[bad], gx, gy)
            tx, ty = look(sx_, sy_)
            e = np.hypot(tx - qx[bad], ty - qy[bad])
            ok = e < err[bad]
            x[bad[ok]], y[bad[ok]], err[bad[ok]] = sx_[ok], sy_[ok], e[ok]
            bad = bad[err[bad] > 1e-6]
            if not len(bad):
                break
            good = np.nonzero(err <= 1e-6)[0]
    if len(bad) and seed is not None:  # (still lost: started again from the nearest spot a fine search found)
        gx, gy = seed(qx[bad], qy[bad])
        sx_, sy_ = solve_plain(look, qx[bad], qy[bad], gx, gy)
        tx, ty = look(sx_, sy_)
        e = np.hypot(tx - qx[bad], ty - qy[bad])
        ok = e < err[bad]
        x[bad[ok]], y[bad[ok]] = sx_[ok], sy_[ok]
    return x, y


SEARCH = 241  # (the fine search's spots each way, over SEARCH_SPAN of the box)
SEARCH_SPAN = (-1.0, 2.0)
CELLS_MOST = 8_000_000  # (LiquifyBend._seeded: bucket entries of the moved cells at most (memory); past it the
# first guess)


def searcher(look):
    """seed(qx, qy) for solve: for each point, the spot of a fine grid whose look() lands nearest it (worked out once,
    on the first point that needs it)."""
    held = {}

    def seed(qx, qy):
        if "grid" not in held:
            g = np.linspace(*SEARCH_SPAN, SEARCH)
            GX, GY = (a.ravel() for a in np.meshgrid(g, g))
            LX, LY = look(GX, GY)
            held["grid"] = GX, GY, LX, LY
        GX, GY, LX, LY = held["grid"]
        out_x, out_y = np.empty(len(qx)), np.empty(len(qx))
        step = max(1, 4_000_000 // len(GX))
        for a in range(0, len(qx), step):
            d = (LX[None, :] - qx[a:a + step, None]) ** 2 + (LY[None, :] - qy[a:a + step, None]) ** 2
            j = d.argmin(1)
            out_x[a:a + step], out_y[a:a + step] = GX[j], GY[j]
        return out_x, out_y
    return seed


def solve_plain(look, qx, qy, x, y):
    """solve's Newton steps alone (no neighbours' help)."""
    x, y = x.copy(), y.copy()
    for _ in range(STEPS):
        fx, fy = look(x, y)
        rx, ry = fx - qx, fy - qy
        if not len(x) or np.hypot(rx, ry).max() <= EPS:
            break
        h = 1e-7 * np.maximum(1.0, np.hypot(x, y))
        ax, ay = look(x + h, y)
        bx, by = look(x, y + h)
        j11, j21, j12, j22 = (ax - fx) / h, (ay - fy) / h, (bx - fx) / h, (by - fy) / h
        det = j11 * j22 - j12 * j21
        det = np.where(np.abs(det) < 1e-14, 1e-14, det)
        x, y = x - (j22 * rx - j12 * ry) / det, y - (j11 * ry - j21 * rx) / det
    return x, y
