"""The piano roll's part of Recolour's Warp (notes/warp.py, window/warp_window.py; user 2026-11-09, PLAN.md stage C):
while the Warp window is open, each selected shape Recolour can change shows its grid over its colour box (flipped /
turned with the shape, like the Stir's Centre point: recolour.centre_spot). Smooth = the bent curves the colours
follow, Straight cells = straight lines between the points. A shape without a Warp shows a flat grid with the
window's picks; its first drag takes them.
Press a point + drag = that point moves (anywhere, past the shape's box too: user 2026-11-09; colours from outside
follow At the edges). The REAL new colours show while dragging, like the Liquify brush: only the notes whose colour
changed are worked out and painted (roll_liquify's Live machinery, the Warp's turned-round map standing in for a
Liquify's map); where that can't follow (a Liquify on the shape too, few notes, Single channel...) every note is made
again at most every LIVE_MS. Let go = ONE undo step (pushed at the first real move: a click alone leaves none). Esc
mid-drag = the point back, no step. Ctrl+Z mid-drag = ends as if let go, then that step is taken back. With the window
open a press on a shape off a point never moves it: an unselected shape gets selected, a selected one stays.
MIRROR (window dropdown, user 2026-11-09, PLAN C3 + C5): each drag move sets the dragged point's twins across the
grid's middle line(s) AS SHOWN ON THE PIANO ROLL (a turned shape: the box's other way; warp_axes), offsets mirrored;
Half turn = the point opposite through the middle, offsets turned round. A point on a mirror line keeps 0 across it
(slides along it only); the middle point in 4-way / Half turn is LOCKED (a cross; a press moves nothing, no step);
twins jump to the mirrored spot at the first move (a lopsided grid stays as it is until then). Look: mirror lines /
Half turn's circle-with-dot ON TOP of the grid in the accent with a dark edge, points over them, the hovered /
dragged point's twins lit in the accent (wp_hover, set by pianoroll.hover_cursor), the dragged point last.
CURVE HANDLES (PLAN.md "Warp curve handles" B, user 2026-11-09; maths in notes/warp.py): the point clicked last
(wp_pick) shows its handles + the facing handle of each neighbour, drawn like the Curve tool's (a line on a white
edge, a solid dot). Dragging one = the point's opposite handle turns along (keeps its length as seen); Alt = this
one alone, the point becomes a corner; right-click = back where it sits untouched (one undo step). The nearer of a
point and a handle is grabbed (a tie: the point). Mirror modes: the twins' handles copy the dragged point's
(warp_mirror_handles), exactly mirrored; on a mirror line the mirror wins over the pairing."""

import time

import numpy as np

from files.lang import tr
from notes import warp as wp_
from notes.recolour import KEEP, box_of, can_recolour, centre_at, centre_spot, clean_recolour, new_seed
from roll.roll_shared import ALT
from window import look

GRAB = 8  # px: how near a grid point the mouse must be to grab it
STEPS = 8  # (a Smooth grid line's pieces per cell)
LIVE_MS = 30  # (every note made again at most this often while dragging when not shown live)
MOST_GRIDS = 8  # (selected shapes showing a grid at most: more = only the first ones)


class WarpGrid:
    def warp_on(self):
        return getattr(self.app, "warp_window", None) is not None

    # ---- where things are

    def warp_grids(self):
        """[{sh, made, wp (every setting filled in), P (raw offsets: rows + 1 up, cols + 1 across, 2)}] of the selected
        shapes Recolour can change (a shape without a Warp: a flat grid with the window's picks). While a point is
        dragged its shape shows the dragged grid."""
        w = self.app.warp_window
        st = getattr(self, "wp", None)
        out = []
        for sh in self.app.recolour_targets()[:MOST_GRIDS]:
            if st and st["sh"] is sh:
                out.append(st["g"])
                continue
            made = self.stir_made(sh, "warp")
            if made is None:
                continue
            got = (made[0] or {}).get("warp")
            if got:
                g = wp_.full(got)
            else:
                g = wp_.full(dict(wp_.empty(w.cols, w.rows), smooth=w.smooth, edge=w.edge, edges=w.edges,
                                     folds=w.folds))
            out.append({"sh": sh, "made": made, "wp": g, "P": wp_.offsets(g, strength=False)})
        return out

    @staticmethod
    def warp_factor(g):
        """The share of a point's offset the colours move by (Strength %; 0 = dragged as if 100 %)."""
        s = g["wp"]["strength"] / 100.0
        return s if s > 0 else 1.0

    def warp_screen(self, made, u, v):
        """Spots (u, v) of a shape's colour box (0..1, past it too) -> screen (x, y) arrays."""
        rc, notes, own, _ = made
        u, v = np.asarray(u, np.float64), np.asarray(v, np.float64)
        B = box_of(notes)
        t, k = centre_spot(rc, notes, own, [100 * u, 100 * v])
        if B[3] == B[2] and not (rc.get("frame") or (1, 0))[1]:  # (one key: a box one key tall, as the brush's)
            k = B[2] - 0.5 + v
        x, y = self.to_xy((np.asarray(t, np.float64) / self.app.ppq, np.asarray(k, np.float64)))
        return np.asarray(x, np.float64), np.asarray(y, np.float64)

    def warp_unit(self, made, x, y):
        """Screen spot -> (u, v) in the shape's colour box."""
        rc, notes, own, _ = made
        b, p = self.from_xy(x, y)
        c = centre_at(rc, notes, own, b * self.app.ppq, p, one_key=True)
        return c[0] / 100.0, c[1] / 100.0

    def warp_points(self, g):
        """Screen (x, y) of every grid point as the colours show it (row by row from the bottom left)."""
        c, r = wp_.size(g["wp"])
        jj, ii = np.mgrid[0:r + 1, 0:c + 1]
        P = g["P"] * self.warp_factor(g) if g["wp"]["strength"] > 0 else g["P"]
        u, v = (ii / c).ravel() + P[..., 0].ravel(), (jj / r).ravel() + P[..., 1].ravel()
        return self.warp_screen(g["made"], u, v)

    def warp_lines(self, g):
        """The grid's lines on screen: [[x0, y0, x1, y1, ...], ...], rows then columns; Smooth = the bent curves
        (each line's spots moved as the colours there are), Straight cells = straight between the points."""
        c, r = wp_.size(g["wp"])
        # (curve handles dragged: the bend read from the control net, curved like Smooth)
        P, smooth = (wp_.shape_of(dict(g["wp"], pts=np.asarray(g["P"]).reshape(-1, 2).tolist()), strength=False)
                     if g["wp"].get("h") else (g["P"], g["wp"]["smooth"]))
        P = P * (self.warp_factor(g) if g["wp"]["strength"] > 0 else 1.0)
        su, sv = (STEPS * c, STEPS * r) if smooth else (c, r)  # (pieces along a row / a column)
        tu, tv = np.linspace(0.0, 1.0, su + 1), np.linspace(0.0, 1.0, sv + 1)
        # (rows: v fixed, u along; columns: u fixed, v along)
        u = np.concatenate([np.tile(tu, r + 1), np.repeat(np.arange(c + 1) / c, sv + 1)])
        v = np.concatenate([np.repeat(np.arange(r + 1) / r, su + 1), np.tile(tv, c + 1)])
        d = wp_.bend_at(P, u, v, smooth)
        x, y = self.warp_screen(g["made"], u + d[:, 0], v + d[:, 1])
        xy = np.empty(2 * len(x))
        xy[0::2], xy[1::2] = x, y
        xy = xy.tolist()
        a, b = 2 * (su + 1), 2 * (sv + 1)
        rows = [xy[i * a:(i + 1) * a] for i in range(r + 1)]
        k = (r + 1) * a
        return rows + [xy[k + i * b:k + (i + 1) * b] for i in range(c + 1)]

    def warp_axes(self, made):
        """True when the colour box's u (across) runs sideways on screen (False: a turned shape, u runs up / down)."""
        x, y = self.warp_screen(made, [0.0, 1.0, 0.5, 0.5], [0.5, 0.5, 0.0, 1.0])
        return abs(x[1] - x[0]) + abs(y[3] - y[2]) >= abs(y[1] - y[0]) + abs(x[3] - x[2])

    def warp_mirror(self, g):
        """(mirror across u = 0.5, mirror across v = 0.5, half turn) for grid g from the window's Mirror pick, the
        lines as shown on the piano roll (a turned shape: swapped). Half turn = round the middle, the same on screen
        whatever the Flip / Turn (a half turn is its own mirror both ways)."""
        w = getattr(self.app, "warp_window", None)
        mode = w.mirrored() if w else "off"
        if mode == "half":
            return False, False, True
        side, up = mode in ("side", "both"), mode in ("up", "both")
        if not (side or up):
            return False, False, False
        return ((side, up) if self.warp_axes(g["made"]) else (up, side)) + (False,)

    @staticmethod
    def warp_locked(c, r, j, i, mu, mv, half):
        """Point (j, i) of a c x r cell grid can't move: the middle point in 4-way / Half turn (its own twin)."""
        return (half or (mu and mv)) and 2 * i == c and 2 * j == r

    @staticmethod
    def warp_twin_spots(c, r, j, i, mu, mv, half):
        """The points (j, i)'s drag moves too: [(j2, i2), ...] (itself left out)."""
        if half:
            out = [(r - j, c - i)]
        else:
            out = ([(j, c - i)] if mu else []) + ([(r - j, i)] if mv else []) + ([(r - j, c - i)] if mu and mv else [])
        return [s for s in dict.fromkeys(out) if s != (j, i)]

    @staticmethod
    def warp_twins(P, j, i, new, mu, mv, half=False):
        """P (rows + 1, cols + 1, 2) with point (j, i) at offsets new and its mirror twins set (mu: across u = 0.5,
        mv: across v = 0.5, half: the point opposite through the middle, moved the opposite way); a point on a
        mirror line keeps 0 across it, the middle in Half turn stays. Changes P in place."""
        r, c = P.shape[0] - 1, P.shape[1] - 1
        dx, dy = new
        if half:
            if 2 * i == c and 2 * j == r:
                dx = dy = 0.0
            P[j, i] = (dx, dy)
            P[r - j, c - i] = (-dx, -dy)
            return P
        if mu and 2 * i == c:
            dx = 0.0
        if mv and 2 * j == r:
            dy = 0.0
        P[j, i] = (dx, dy)
        if mu:
            P[j, c - i] = (-dx, dy)
        if mv:
            P[r - j, i] = (dx, -dy)
        if mu and mv:
            P[r - j, c - i] = (-dx, -dy)
        return P

    def warp_point_locked(self, g, i):
        """Point number i of grid g can't move with the Mirror picked now."""
        c, r = wp_.size(g["wp"])
        j, i = divmod(i, c + 1)
        return self.warp_locked(c, r, j, i, *self.warp_mirror(g))

    # ---- curve handles

    def warp_cur(self, g):
        """Grid g's Warp with its points as they are now."""
        return dict(g["wp"], pts=np.asarray(g["P"], np.float64).reshape(-1, 2).tolist())

    def warp_pick_of(self, g):
        """The point of grid g clicked last (its handles show), or None."""
        p = getattr(self, "wp_pick", None)
        c, r = wp_.size(g["wp"])
        return p[1] if p and p[0] == id(g["sh"]) and p[1] < (c + 1) * (r + 1) else None

    def warp_handle_list(self, g):
        """[(point, side)] of the handles grid g shows: the picked point's own + each neighbour's facing it."""
        k0 = self.warp_pick_of(g)
        if k0 is None:
            return []
        c, r = wp_.size(g["wp"])
        i, j = k0 % (c + 1), k0 // (c + 1)
        out = []
        for s, (di, dj) in enumerate(wp_.SIDES):
            if wp_.side_ok(i, j, s, c, r):
                out.append((k0, s))
                out.append(((j + dj) * (c + 1) + i + di, s ^ 1))
        return out

    def warp_box_spot(self, g, k):
        """Point k of grid g where it shows, in box units."""
        c, r = wp_.size(g["wp"])
        f = self.warp_factor(g)
        p = np.asarray(g["P"], np.float64).reshape(-1, 2)[k]
        return np.array([(k % (c + 1)) / c + p[0] * f, (k // (c + 1)) / r + p[1] * f])

    def warp_handle_spots(self, g):
        """[(point, side, handle x, y, point x, y)] on screen of the handles grid g shows."""
        hl = self.warp_handle_list(g)
        if not hl:
            return []
        c, r = wp_.size(g["wp"])
        V, _ = wp_.handle_vecs(self.warp_cur(g))
        a = np.array([self.warp_box_spot(g, k) for k, s in hl])
        h = a + np.array([V[k // (c + 1), k % (c + 1), s] for k, s in hl])
        x, y = self.warp_screen(g["made"], np.concatenate([h[:, 0], a[:, 0]]), np.concatenate([h[:, 1], a[:, 1]]))
        n = len(hl)
        return [(k, s, float(x[m]), float(y[m]), float(x[n + m]), float(y[n + m])) for m, (k, s) in enumerate(hl)]

    def warp_handle_hit(self, x, y, grids=None):
        """(grid, point, side, distance) of the shown handle nearest the mouse within GRAB px, or None."""
        if not self.warp_on():
            return None
        best = None
        for g in (self.warp_grids() if grids is None else grids):
            for k, s, hx, hy, _, _ in self.warp_handle_spots(g):
                d = float(np.hypot(hx - x, hy - y))
                if d <= GRAB * self.scale and (best is None or d < best[3]):
                    best = (g, k, s, d)
        return best

    def warp_scale(self, made):
        """Screen px of one box unit across / up (a turned shape: its own ways), for a handle's length as seen."""
        x, y = self.warp_screen(made, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
        return float(np.hypot(x[1] - x[0], y[1] - y[0])), float(np.hypot(x[2] - x[0], y[2] - y[0]))

    @staticmethod
    def warp_mirror_handles(wp, k, s, mu, mv, half):
        """wp with point k's handles copied onto its mirror twins (mirrored exactly, corner mark too). s = the handle
        just dragged / put back (None: a point drag): on a mirror line the point is its own twin, so the handle
        across the line becomes s's mirror (the mirror wins over the pairing) and a handle along the line keeps 0
        across it. Every handle's offset kept within MOST_MOVE."""
        c, r = wp_.size(wp)
        i, j = k % (c + 1), k // (c + 1)
        H = {(e[0], e[1]): [float(e[2]), float(e[3])] for e in wp.get("h", ())}
        corners = set(wp.get("corners", ()))
        ways = [(True, True)] if half else ([(True, False)] if mu else []) + ([(False, True)] if mv else []) + (
            [(True, True)] if mu and mv else [])

        def twin(fu, fv):
            return (r - j if fv else j) * (c + 1) + (c - i if fu else i)

        def side(q, fu, fv):
            return q ^ 1 if (fu and q < 2) or (fv and q >= 2) else q

        def flip(d, fu, fv):
            return [-d[0] if fu else d[0], -d[1] if fv else d[1]]
        if s is not None:
            for fu, fv in ways:
                if twin(fu, fv) != k:
                    continue
                for q in range(4):  # (a handle its own twin: 0 across the line)
                    if side(q, fu, fv) == q and (k, q) in H:
                        H[(k, q)] = [0.0 if fu else H[(k, q)][0], 0.0 if fv else H[(k, q)][1]]
            for fu, fv in ways:
                q = side(s, fu, fv)
                if twin(fu, fv) != k or q == s:
                    continue
                if (k, s) in H:
                    H[(k, q)] = flip(H[(k, s)], fu, fv)
                else:
                    H.pop((k, q), None)
        for fu, fv in ways:
            t = twin(fu, fv)
            if t == k:
                continue
            for q in range(4):
                H.pop((t, side(q, fu, fv)), None)
                if (k, q) in H:
                    H[(t, side(q, fu, fv))] = flip(H[(k, q)], fu, fv)
            if k in corners:
                corners.add(t)
            else:
                corners.discard(t)
        m = wp_.MOST_MOVE
        out = {key: v for key, v in wp.items() if key not in ("h", "corners")}
        if H:
            out["h"] = [[a, b] + [min(max(v, -m), m) + 0.0 for v in H[(a, b)]] for a, b in sorted(H)]
            if corners:
                out["corners"] = sorted(corners)
        return out

    def warp_right(self, e):
        """Right-click (Warp window open) on a shown handle: back where it sits untouched (one undo step; its
        twins too). True when it was on a handle."""
        if not self.warp_on():
            return False
        got = self.warp_handle_hit(e.x, e.y)
        if got is None:
            return False
        g, k, s, _ = got
        if not any((h[0], h[1]) == (k, s) for h in g["wp"].get("h", ())):
            return True  # (untouched already: nothing to put back)
        self.app.push_undo(name=tr("warp.undo_handle_back"))
        wp = wp_.reset_handle(self.warp_cur(g), k, s)
        wp = self.warp_mirror_handles(wp, k, s, *self.warp_mirror(g))
        if wp.get("on") is False:
            wp["on"] = True
        self.warp_apply({"sh": g["sh"], "g": dict(g, wp=wp)}, now=True)
        self.app.sync_recolour()
        self.app.schedule_autosave()
        self.redraw()
        return True

    def warp_hover(self, x, y):
        """The mouse moved (no button held, Warp window open): the point under it remembered and the grid drawn
        again when that changed, so its twins light up (a nearer handle: wp_hover_h, the move pointer). Returns
        warp_hit's answer."""
        grids = self.warp_grids() if self.warp_on() else []
        got = self.warp_hit(x, y, grids, dist=True) if grids else None
        hd = self.warp_handle_hit(x, y, grids) if grids else None
        if hd and (got is None or hd[3] < got[2]):
            got = None
        self.wp_hover_h = (id(hd[0]["sh"]), hd[1], hd[2]) if hd and got is None else None
        got = got[:2] if got else None
        key = (id(got[0]["sh"]), got[1]) if got else None
        self.wp_hover_locked = bool(got) and self.warp_point_locked(*got)
        if key != getattr(self, "wp_hover", None):
            self.wp_hover = key
            self.draw_warp_grid()
        return got

    def warp_leave(self, e=None):
        """The mouse left the piano roll: the hovered point forgotten, its lit twins dark again."""
        self.wp_hover_h = None
        if getattr(self, "wp_hover", None) is not None:
            self.wp_hover = None
            self.wp_hover_locked = False
            self.draw_warp_grid()

    def warp_hit(self, x, y, grids=None, dist=False):
        """(grid, point number) of the grid point under the mouse (the nearest within GRAB px), or None (dist: +
        its distance)."""
        if not self.warp_on():
            return None
        best = None
        for g in (self.warp_grids() if grids is None else grids):
            px, py = self.warp_points(g)
            d = np.hypot(px - x, py - y)
            i = int(np.argmin(d))
            if d[i] <= GRAB * self.scale and (best is None or d[i] < best[2]):
                best = (g, i, float(d[i]))
        return best if dist or not best else best[:2]

    def warp_grab(self, x, y, grids=None):
        """What a press at (x, y) grabs: ("point", grid, number), ("handle", grid, point, side) or None. The
        nearer wins; a tie = the point."""
        grids = self.warp_grids() if grids is None else grids
        pt = self.warp_hit(x, y, grids, dist=True)
        hd = self.warp_handle_hit(x, y, grids)
        if hd and (pt is None or hd[3] < pt[2]):
            return ("handle",) + hd[:3]
        return ("point",) + pt[:2] if pt else None

    # ---- drawing

    def draw_warp_grid(self):
        """Each selected shape's grid: lines over a ROLL_BG band (busy colours under it, like the Stir circle) and
        a small square at each point; the dragged point filled."""
        self.delete("wpgrid")
        if not self.warp_on() or self.sx is None:
            return
        from roll.roll_gliss import clip_polyline
        s = self.scale
        line = max(1, round(s))
        band = line + 2 * max(1, round(s))
        room = (-10, -10, self.winfo_width() + 10, self.winfo_height() + 10)
        st = getattr(self, "wp", None)
        hover = getattr(self, "wp_hover", None)
        for g in self.warp_grids():
            pieces = [p for ln in self.warp_lines(g) for p in clip_polyline(ln, room)]
            for width, col in ((band, look.ROLL_BG), (line, look.WARP_GRID)):
                for p in pieces:
                    self.create_line(*p, fill=col, width=width, tags="wpgrid")
            # (MIRROR LOOK, user 2026-11-09 C5: ON TOP of the grid in its own accent with a dark edge, the points
            # drawn over it; order: grid lines -> mirror lines / marker -> points -> lit twins + the dragged point)
            mu, mv, half = self.warp_mirror(g)
            accent = line + 2 * max(1, round(s))
            edge = accent + 2 * max(1, round(s))
            for on, u, v in ((mu, [0.5, 0.5], [0.0, 1.0]), (mv, [0.0, 1.0], [0.5, 0.5])):
                if on:  # (the mirror line: the box's own middle, unbent, solid (Windows draws Tk's dashes 2 px on /
                    # off: they vanished on the grid's own middle line), cut to the view first)
                    x, y = self.warp_screen(g["made"], u, v)
                    for p in clip_polyline([float(x[0]), float(y[0]), float(x[1]), float(y[1])], room):
                        self.create_line(*p, fill=look.WARP_MIRROR_EDGE, width=edge, tags="wpgrid")
                        self.create_line(*p, fill=look.WARP_MIRROR, width=accent, tags="wpgrid")
            if half:  # (Half turn: a small circle with a dot at the box's own middle, no lines)
                x, y = self.warp_screen(g["made"], [0.5], [0.5])
                x, y, rr, d = float(x[0]), float(y[0]), 8 * s, 2.5 * s
                if room[0] < x < room[2] and room[1] < y < room[3]:
                    self.create_oval(x - rr, y - rr, x + rr, y + rr, outline=look.WARP_MIRROR_EDGE, width=edge,
                                     tags="wpgrid")
                    self.create_oval(x - rr, y - rr, x + rr, y + rr, outline=look.WARP_MIRROR, width=accent,
                                     tags="wpgrid")
                    self.create_oval(x - d, y - d, x + d, y + d, fill=look.WARP_MIRROR,
                                     outline=look.WARP_MIRROR_EDGE, width=max(1, round(s)), tags="wpgrid")
            # (curve handles: like the Curve tool's, a line on a white edge under the points, the dots over them)
            hs = [h for h in self.warp_handle_spots(g)
                  if not (h[2] < room[0] and h[4] < room[0] or h[2] > room[2] and h[4] > room[2]
                          or h[3] < room[1] and h[5] < room[1] or h[3] > room[3] and h[5] > room[3])]
            for width, edged in ((max(3, round(3.5 * s)), True), (max(1, round(1.5 * s)), False)):
                for _, _, hx, hy, ax, ay in hs:
                    self.create_line(ax, ay, hx, hy, fill=look.HANDLE_FILL if edged else look.HANDLE, width=width,
                                     tags="wpgrid")
            px, py = self.warp_points(g)
            px, py = px.tolist(), py.tolist()
            c, rows = wp_.size(g["wp"])
            r = 4 * s
            held = st["i"] if st and st["g"] is g else None
            lead = held if held is not None else (hover[1] if hover and hover[0] == id(g["sh"]) else None)
            lit = set()
            if lead is not None and (mu or mv or half):
                j0, i0 = divmod(lead, c + 1)
                lit = {jj * (c + 1) + ii for jj, ii in self.warp_twin_spots(c, rows, j0, i0, mu, mv, half)}
            locked = None
            if (half or (mu and mv)) and c % 2 == 0 and rows % 2 == 0:
                locked = (rows // 2) * (c + 1) + c // 2
            last = []  # (lit twins + the dragged point: drawn after every other point)
            for i, (x, y) in enumerate(zip(px, py)):
                if not (room[0] < x < room[2] and room[1] < y < room[3]):
                    continue
                if i in lit or i == held:
                    last.append(i)
                elif i == locked:  # (can't move: a small cross, never a square to grab)
                    for a, b in ((-1, -1), (-1, 1)):
                        self.create_line(x + a * r, y + b * r, x - a * r, y - b * r, fill=look.ROLL_BG,
                                         width=line + 2 * max(1, round(s)), tags="wpgrid")
                        self.create_line(x + a * r, y + b * r, x - a * r, y - b * r, fill=look.WARP_GRID,
                                         width=line + max(1, round(s)), tags="wpgrid")
                else:
                    self.create_rectangle(x - r, y - r, x + r, y + r, outline=look.WARP_GRID, fill=look.ROLL_BG,
                                          width=line, tags="wpgrid")
            q = r + 0.5 * s
            for _, _, hx, hy, _, _ in hs:
                if room[0] < hx < room[2] and room[1] < hy < room[3]:
                    self.create_oval(hx - q, hy - q, hx + q, hy + q, fill=look.HANDLE, outline=look.HANDLE_FILL,
                                     width=max(1, round(s)), tags="wpgrid")
            for i in sorted(last, key=lambda i: i == held):  # (the dragged point on top of all)
                x, y = px[i], py[i]
                if i == held:
                    self.create_rectangle(x - r, y - r, x + r, y + r, outline=look.WARP_GRID, fill=look.WARP_GRID,
                                          width=line, tags="wpgrid")
                else:
                    self.create_rectangle(x - r, y - r, x + r, y + r, outline=look.WARP_MIRROR_EDGE,
                                          fill=look.WARP_MIRROR, width=line, tags="wpgrid")
        # (where a full redraw leaves it: under the keyboard + ruler, under what's drawn after it there)
        self.tag_raise("wpgrid")
        if self.find_withtag("frame"):
            self.tag_lower("wpgrid", "frame")
            for tag in ("lqbrush", "melodylit", "sustop"):
                if self.find_withtag(tag):
                    self.tag_raise(tag, "wpgrid")

    # ---- the mouse

    def warp_shape_at(self, x, y):
        """The shape a press at (x, y) off the grid points picks (never moves) while the window is open: under the
        mouse, or the selected one's handles / box sides; None = not one Recolour can change."""
        app = self.app
        i = self.shape_at(x, y, prefer_selected=False)
        if i is None and app.sel is not None and (self.hit_handle(x, y, True) is not None or self.custom_hit(x, y)):
            i = app.sel
        return i if i is not None and can_recolour(app.shapes[i]) else None

    def warp_target(self, x, y):
        """A press at (x, y) is the Warp's (a grid point, or a shape it picks)."""
        return self.warp_grab(x, y) is not None or self.warp_shape_at(x, y) is not None

    def warp_press(self, e):
        """A left press while the Warp window is open: on a grid point = it's dragged (True); on a shape Recolour
        can change = picked, never moved (True; the Liquify window open too: left to its brush); elsewhere the piano
        roll as usual (False)."""
        if not self.warp_on():
            return False
        app = self.app
        got = self.warp_grab(e.x, e.y)
        if got is None:
            if getattr(self, "wp_pick", None) is not None:  # (off the grid: the picked point's handles go)
                self.wp_pick = None
                self.draw_warp_grid()
            if getattr(app, "liquify_window", None) is not None:
                return False
            i = self.warp_shape_at(e.x, e.y)
            if i is None:
                return False
            if i not in app.sels:
                app.select(i)
            self.drag = ("warp_none",)  # (held: nothing moves; let go ends it)
            return True
        if got[0] == "handle":
            return self.warp_handle_press(e, *got[1:])
        g, i = got[1:]
        self.wp_pick = (id(g["sh"]), i)  # (its handles show)
        if self.warp_point_locked(g, i):  # (the middle point in 4-way / Half turn: nothing moves, no undo step)
            self.draw_warp_grid()
            self.drag = ("warp_none",)
            if np.any(np.asarray(g["P"]).reshape(-1, 2)[i]):  # (moved before the mirror was picked: back to its
                self.warp_centre_back(g, i)  # place, one undo step; hunt 2026-11-09)
            return True
        u, v = self.warp_unit(g["made"], e.x, e.y)
        c, r = wp_.size(g["wp"])
        k = self.warp_factor(g)
        base = ((i % (c + 1)) / c, (i // (c + 1)) / r)
        P = np.array(g["P"], np.float64)
        here = P.reshape(-1, 2)[i]
        # (where the point shows now minus the mouse: the point stays under the mouse where it was grabbed)
        grab = (base[0] + here[0] * k - u, base[1] + here[1] * k - v)
        g = dict(g, P=P)
        self.wp = {"sh": g["sh"], "g": g, "i": i, "grab": grab, "base": base, "k": k, "pushed": False,
                   "old": clean_recolour(g["sh"].get("recolour")), "job": None, "took": 0.0, "way": None,
                   "shown": 0.0, "jobs": [{"sh": g["sh"], "made": g["made"], "warp": g["wp"], "a": None}]}
        self.drag = ("warp",)
        self.draw_warp_grid()
        return True

    def warp_handle_press(self, e, g, k, s):
        """A press on a shown curve handle (point k, side s): it's dragged (Alt held while moving = a corner)."""
        c, r = wp_.size(g["wp"])
        pt = self.warp_box_spot(g, k)
        V, _ = wp_.handle_vecs(self.warp_cur(g))
        u, v = self.warp_unit(g["made"], e.x, e.y)
        hv = V[k // (c + 1), k % (c + 1), s]
        grab = (pt[0] + hv[0] - u, pt[1] + hv[1] - v)  # (the handle stays under the mouse where it was grabbed)
        g = dict(g, P=np.array(g["P"], np.float64))
        self.wp = {"sh": g["sh"], "g": g, "i": None, "hs": (k, s), "grab": grab, "pt": pt,
                   "scale": self.warp_scale(g["made"]), "pushed": False,
                   "old": clean_recolour(g["sh"].get("recolour")), "job": None, "took": 0.0, "way": None,
                   "shown": 0.0, "jobs": [{"sh": g["sh"], "made": g["made"], "warp": g["wp"], "a": None}]}
        self.drag = ("warp",)
        self.draw_warp_grid()
        return True

    def warp_centre_back(self, g, i):
        """The locked middle point, moved before 4-way / Half turn was picked, put back on its place (one undo
        step; a hidden Warp shown again, as a drag does)."""
        self.app.push_undo(name=tr("warp.undo_drag"))
        P = np.array(g["P"], np.float64)
        P.reshape(-1, 2)[i] = 0.0
        wp = dict(g["wp"])
        if wp.get("on") is False:
            wp["on"] = True
        st = {"sh": g["sh"], "g": dict(g, P=P, wp=wp)}
        self.warp_apply(st, now=True)
        self.app.sync_recolour()
        self.app.schedule_autosave()
        self.redraw()

    def warp_drag(self, e):
        st = getattr(self, "wp", None)
        if not st:
            return
        if not self.warp_on():  # (the window closed mid-drag: let go)
            return self.warp_release(e)
        g = st["g"]
        u, v = self.warp_unit(g["made"], e.x, e.y)
        mu, mv, half = self.warp_mirror(g)
        hw = None
        if st.get("hs") is not None:  # (a curve handle: from its point as shown, box units)
            k, s = st["hs"]
            vec = (u + st["grab"][0] - st["pt"][0], v + st["grab"][1] - st["pt"][1])
            hw = wp_.set_handle(self.warp_cur(g), k, s, vec, alt=bool(e.state & ALT), scale=st["scale"])
            hw = self.warp_mirror_handles(hw, k, s, mu, mv, half)
            if (hw.get("h"), hw.get("corners")) == (g["wp"].get("h"), g["wp"].get("corners")):
                return
        else:
            k = st["k"]
            new = ((u + st["grab"][0] - st["base"][0]) / k, (v + st["grab"][1] - st["base"][1]) / k)
            new = [min(max(float(c), -wp_.MOST_MOVE), wp_.MOST_MOVE) for c in new]
            j, i = divmod(st["i"], wp_.size(g["wp"])[0] + 1)
            P2 = self.warp_twins(np.array(g["P"]), j, i, new, mu, mv, half) if mu or mv or half else None
            if (P2 is None and list(g["P"][j, i]) == new) or (P2 is not None and np.array_equal(P2, g["P"])):
                return
        if not st["pushed"]:
            self.warp_first(st)
        job = st["jobs"][0]
        if st["way"] is None:  # (how it shows, worked out from the grid before this move: Live starts from it)
            job["a"] = self.warp_map(g)
            st["way"] = self.liquify_way(st["jobs"])
        if hw is not None:
            hw["on"] = g["wp"].get("on", True)  # (warp_first may have shown a hidden Warp)
            g["wp"] = job["warp"] = wp_.full(hw)
        elif P2 is None:
            g["P"][j, i] = new
        else:
            g["P"][...] = P2
            if g["wp"].get("h"):  # (the twins' handles mirror the dragged point's too)
                g["wp"] = job["warp"] = self.warp_mirror_handles(g["wp"], st["i"], None, mu, mv, half)
        st["changed"] = True  # (the map is turned round when shown: moves coming quicker than that cost nothing)
        if st["way"] == "later":  # (its first Recolour: the notes made once, then shown live)
            self.warp_again(st)
            st["shown"] = time.perf_counter()
        self.draw_warp_grid()
        if st["way"] == "live":
            if time.perf_counter() - st["shown"] > LIVE_MS / 1000:  # (moves never stopping: shown now)
                if st["job"]:
                    self.after_cancel(st["job"])
                    st["job"] = None
                self.warp_live()
            elif not st["job"]:
                st["job"] = self.after_idle(self.warp_live)
        elif not st["job"]:
            st["job"] = self.after(max(LIVE_MS, int(st["took"] * 1000)), self.warp_live)

    def warp_first(self, st):
        """The drag's first real move: its undo step; a hidden Warp shown again in it (else the drag works blind,
        like Liquify's brush)."""
        self.app.push_undo(name=tr("warp.undo_handle" if st.get("hs") is not None else "warp.undo_drag"))
        st["pushed"] = True
        g = st["g"]
        if g["wp"].get("on") is False:
            g["wp"] = dict(g["wp"], on=True)
            st["jobs"][0]["warp"] = g["wp"]
            self.warp_apply(st, now=True)
            self.redraw()
            st["jobs"][0]["made"] = g["made"] = self.stir_made(st["sh"], "warp") or g["made"]

    @staticmethod
    def warp_map(g):
        """The grid's turned-round map (zeros: no point moved), as warp.warped uses it."""
        wp = wp_.clean_warp(dict(g["wp"], pts=g["P"].reshape(-1, 2).tolist()))
        if wp and wp.get("on") is not False and wp.get("strength", 100.0):
            return wp_.inverse_map(wp, quick=True)  # (let go: the whole map, warp.warped)
        gx, gy = wp_.map_size(g["wp"])
        return np.zeros((gy, gx, 2))

    def warp_live(self):
        st = getattr(self, "wp", None)
        if not st:
            return
        st["job"] = None
        t0 = time.perf_counter()
        if st["way"] == "live" and any(job["live"]["img"] is not self._img for job in st["jobs"]):
            self.warp_again(st)  # (a new picture under it: zoomed / scrolled mid-drag)
        elif st["way"] == "live":
            for job in st["jobs"]:
                job["a"] = self.warp_map(st["g"])
            if all(self.liquify_show(job) for job in st["jobs"]):
                st["shown"] = time.perf_counter()
                st["took"] = st["shown"] - t0
                return
            st["way"] = "full"  # (something it can't follow: every note made from here on)
            self.warp_apply(st)
        else:
            self.warp_apply(st)
        st["took"] = time.perf_counter() - t0

    def warp_again(self, st):
        """The drag's colours so far made into notes now, the piano roll painted from them, then shown live from
        there (a shape's first Recolour; a new picture of the piano roll mid-drag)."""
        self.warp_apply(st, now=True)
        self.redraw()
        for job in st["jobs"]:
            job["made"] = st["g"]["made"] = self.stir_made(job["sh"], "warp") or job["made"]
            job["a"] = self.warp_map(st["g"])  # (Live starts from the grid as it's now made)
        st["way"] = self.liquify_way(st["jobs"])
        if st["way"] == "later":
            st["way"] = "full"

    def warp_apply(self, st, now=False):
        """The dragged shape gets its grid as it is now; its notes made again (now: here, not in the background)."""
        sh, g = st["sh"], st["g"]
        old = clean_recolour(sh.get("recolour"))
        base = dict(old) if old else {"mode": KEEP, "seed": new_seed()}
        got = wp_.clean_warp(dict(g["wp"], pts=g["P"].reshape(-1, 2).tolist()))
        if got:
            base["warp"] = got
        else:
            base.pop("warp", None)
        new = clean_recolour(base)
        if new:
            sh["recolour"] = new
        else:
            sh.pop("recolour", None)
        self.app.shapes_changed(now=now)

    def warp_release(self, e):
        st = getattr(self, "wp", None)
        self.drag = None
        if not st:
            return
        if st["job"]:
            self.after_cancel(st["job"])
            st["job"] = None
            if st["way"] == "live":  # (the last mouse moves shown first)
                self.warp_live()
        if st.get("changed"):
            self.warp_apply(st, now=st["way"] == "live")  # (shown live: the picture stays till the notes are in)
            self.app.sync_recolour()
            self.app.schedule_autosave()
        self.wp = None
        self.draw_warp_grid()

    def warp_cancel(self):
        """A drag going on ends as if let go (Ctrl+Z mid-drag: the drag ends first, then its step is taken back)."""
        if getattr(self, "wp", None):
            self.warp_release(None)
        elif (self.drag or ("",))[0] == "warp_none":
            self.drag = None

    def warp_escape(self):
        """Esc mid-drag: the point back where it was, no undo step. True when a drag was going on."""
        st = getattr(self, "wp", None)
        if not st:
            return False
        if st["job"]:
            self.after_cancel(st["job"])
        self.wp = None
        self.drag = None
        if st["pushed"]:
            sh = st["sh"]
            if st["old"]:
                sh["recolour"] = st["old"]
            else:
                sh.pop("recolour", None)
            from window.history import undo_state
            self.app.drop_empty_step(undo_state(self.app.shapes))
            self.app.shapes_changed(now=True)
            self.app.sync_recolour()
            self.redraw()
        self.draw_warp_grid()
        return True
