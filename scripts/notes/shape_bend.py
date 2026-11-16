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

from notes import recolour as rc

EPS = 1e-10  # (unit box: a point counts as found when look() brings it back this near)
STEPS = 40  # (Newton steps at most)
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
    if not any(is_shape(x) for x in items):
        return r
    keep, gone = [], []
    for i, x in enumerate(items):
        (gone if is_shape(x) else keep).append(i if is_shape(x) else x)
    out = {k: v for k, v in r.items() if k not in ("stir", "order")}
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
            new.append(k)
        out["order"] = new
    u = r.get("_upto")  # (a step being edited, later ones skipped: App.edit_shapes. Counted again without the
    # Shape tabs; editing a Shape tab = every colour step after it skipped: they all run after the Shape tabs)
    if isinstance(u, int) and not isinstance(u, bool):
        n, gone_at = -1, []
        for j, k in enumerate(rc.order_of(rc.clean_recolour(r) or {})):
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
    if not tabs:
        return None
    out = {"mode": "keep", "seed": 1, "stir": tabs if len(tabs) > 1 else tabs[0]}
    for k in ("frame", "ppq"):
        if k in r:
            out[k] = r[k]
    return out


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
            if reach and st["area_bulge"]:
                mx, my = (v / 100 for v in (st["spot"] or st["centre"]))
                q = rc.area_spot_q(x, y, st, k)
            else:
                mx, my = cx, cy
                ex, ey = x - mx, y - my
                q = np.hypot(np.where(ex > 0, ex / max(1 - mx, 1e-12), -ex / max(mx, 1e-12)),
                             np.where(ey > 0, ey / max(1 - my, 1e-12), -ey / max(my, 1e-12)))
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


LINE_DROP = ("k", "smooth", "shape", "pattern", "tumour", "tumours", "sharp", "end_dot")  # (a bent line = its drawn
# path as points: what made the path from its points (arc, perfect freehand, formulas, tumours) is in them already)
TEXT_DROP = ("text", "polygon")


PULL = "pull"  # (bent_shape: the lines can't show this bend (a fold), pull the notes instead)


def bent_shape(sh, tabs, r, box, own, ppq):
    """sh (its Recolour already without the Shape tabs) with its lines bent by Stir tabs (filled in, Shape, in tab
    order), None when it can't be bent this way (left as it is), or PULL when a tab folds the shape (Straight to
    round, Kaleidoscope, or a point found with no single place to go). r: the Recolour (frame / ppq); box, own: the
    colour box of its notes before the bend (recolour.colour_box). Custom shapes: each stroke bent on its own board
    (curves stay curves, deform.bent_stroke); text and polygons become their strokes. Lines: the drawn path."""
    if any(st["polar"] or st["kaleido"] >= 2 for st in tabs):
        return PULL
    try:
        return _bent_shape(sh, tabs, r, box, own, ppq)
    except Folded:
        return PULL


def _bent_shape(sh, tabs, r, box, own, ppq):
    from notes import deform
    benders = [StirBend(st, r, box, own, ppq) for st in tabs]
    waves = [st["ripple_len"] / 100 for st in tabs if st["ripple"]]
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
        out["strokes"] = [deform.bent_stroke(st, board) for st in strokes]
        if sh.get("areas"):  # (the drawer's coloured areas: each one's spot bent along, so it stays in its area)
            a = np.asarray([p[:2] for p in sh["areas"]], np.float64)
            got = board.bend2(a)
            out["areas"] = [[float(u), float(v), p[2]] for (u, v), p in zip(got, sh["areas"])]
        return out
    if kind in ("line", "poly", "free", "curve", "arc") and not sh.get("cut") and not sh.get("join"):
        from notes.engine import shape_path
        from notes.joined import is_joined
        if is_joined(sh):
            return None
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
    out_n = []
    for ri in np.nonzero(seen.any(1))[0]:
        hit = np.nonzero(seen[ri])[0]
        a = int(math.floor(cols[max(hit[0] - 1, 0)]))
        b = int(math.ceil(cols[min(hit[-1] + 1, len(cols) - 1)])) + 1
        xs = np.arange(a, b + step, step, dtype=np.int64)
        key = float(rows[ri])
        ids = which(*look_tk(xs.astype(np.float64) + 0.5, np.full(len(xs), key)))
        ch = np.nonzero(ids[1:] != ids[:-1])[0]
        if not len(ch) and ids[0] < 0:
            continue
        # (each change found to the tick: halving between the two spots)
        lo_, hi_ = xs[ch].copy(), xs[ch + 1].copy()
        left = ids[ch]
        while True:
            open_ = hi_ - lo_ > 1
            if not open_.any():
                break
            mid = (lo_ + hi_) // 2
            got = which(*look_tk(mid.astype(np.float64) + 0.5, np.full(len(mid), key)))
            same = got == left
            lo_ = np.where(open_ & same, mid, lo_)
            hi_ = np.where(open_ & ~same, mid, hi_)
        cuts = np.r_[xs[0], hi_, xs[-1] + step]
        seg = np.r_[ids[0], ids[ch + 1]]
        for j in range(len(seg)):
            if seg[j] < 0 or cuts[j + 1] <= cuts[j]:
                continue
            out_n.append((cuts[j], cuts[j + 1], int(rows[ri]), seg[j]))
    if not out_n:
        return notes[:0], (tracks[:0] if tracks is not None else None)
    o = np.asarray(out_n, np.int64)
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
    g = s[1:] - np.maximum.accumulate(e)[:-1]
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
