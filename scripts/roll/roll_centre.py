"""The piano roll's part of Recolour's Centre points (notes/recolour.py): while the Shares window is open, each
selected shape using Split: Clumps' Swirl or Wedges shows its Centre point as a dot where its areas turn; while the
Stir window is open (window/stir_window.py), each selected shape shows its Stir's Centre point. Dots sit in the box
the colours count in (a sliced piece's whole's, flipped / turned with the shape: recolour.centre_spot); dragging one
sets that Centre point (in % of that box, like the windows' boxes) of every selected shape of its kind. Whole %
(Shift = free); one undo step a drag."""

import math

import numpy as np

from files.lang import tr
from notes.engine import free_box
from notes.recolour import (DRAWN, LEAST_REACH, LEAST_STRETCH, MOST_REACH, MOST_SKEW, MOST_STRETCH, area_corners,
                            area_dist, area_from_box, area_global, area_local, area_outlines, area_scales, centre_at,
                            centre_spot, clean_recolour, drawn_gauge, without_recolour)
from roll.roll_shared import CTRL
from roll.roll_gliss import clip_polyline
from roll.roll_shared import SHIFT
from window import look

GRAB = 8  # px: how near the dot (or a Stir's circle line) the mouse must be to grab it
RING_POINTS = 72  # (the circle line's pieces)
BOX_LEAST = 40  # px: an area box with a side shorter than this on screen isn't shown (its handles would crowd)


def clumps_centre(rc):
    """A Clumps shape's Centre point that turns its areas (Swirl / Wedges), else None."""
    if not rc or rc["mode"] != "clumps" or rc.get("pattern", "bands") == "bands":
        return None
    return rc.get("centre", [50, 50])


class CentreDots:
    def centre_boxes(self):
        """[(shape, (its Recolour, its notes without it, their box past the piano roll's edges, kind), the dot's
        (tick, key))] of the selected shapes' dots: kind "stir" while the Stir window is open, "clumps" (turning
        Clumps shapes) while the Shares window is."""
        app = self.app
        kinds = [k for k, w in (("clumps", "shares_window"), ("stir", "stir_window")) if getattr(app, w, None)]
        if not kinds:
            return []
        out = []
        for sh in app.recolour_targets():
            rc = clean_recolour(sh.get("recolour"))
            for kind in kinds:
                st = app.stir_window.shown_stir(rc) if kind == "stir" else None
                c = st["centre"] if st else clumps_centre(rc)
                if c is None:
                    continue
                plain = without_recolour(sh)
                notes, _ = app.notes_tracks(plain)
                if not len(notes):
                    continue
                own = free_box(plain, notes, app.notes_tracks, app.keys)
                out.append((sh, (rc or {}, notes, own, kind), centre_spot(rc or {}, notes, own, c)))
                if st and st["reach"] is not None and st["spot"]:  # (the area's own spot: a square, PLAN C1)
                    out.append((sh, (rc or {}, notes, own, "spot"), centre_spot(rc or {}, notes, own, st["spot"])))
        # (both windows open: the dots of the one used last drawn on top and grabbed first, hunt 2026-11-02)
        first = getattr(app, "centre_first", None) == "clumps"
        out.sort(key=lambda d: (d[1][3] == "clumps") == first)
        return out

    def centre_xy(self, spot):
        return self.to_xy((spot[0] / self.app.ppq, spot[1]))

    def reach_ring(self, made):
        """The Stir's area line on the piano roll (its first loop: reach_rings), or None."""
        rings = self.reach_rings(made)
        return rings[0] if rings else None

    def reach_rings(self, made):
        """The Stir's area lines (circle / square / diamond: one; a drawn area: one per loop) on the piano roll, each
        [x0, y0, x1, y1, ...] closed (in the colour box, so an oval on a box that isn't square; flipped / turned
        with the shape); [] = none."""
        rc, notes, own, kind = made
        w = self.app.stir_window
        st = w.shown_stir(rc) if kind == "stir" and w else None
        if not st or not st["reach"]:
            return []
        cx, cy = st["spot"] or st["centre"]
        lines = area_outlines(st, RING_POINTS)
        dx, dy = area_global(np.concatenate([d for d, _ in lines]), np.concatenate([e for _, e in lines]), st,
                             self.area_k(made, st))
        # (all points at once: the box is measured once, hunt 11-03)
        t, k = centre_spot(rc, notes, own, [cx + 100 * dx, cy + 100 * dy])
        x, y = self.to_xy((np.asarray(t, np.float64) / self.app.ppq, np.asarray(k, np.float64)))
        out = np.empty(2 * len(dx))
        out[0::2], out[1::2] = x, y
        out, at = out.tolist(), 0
        rings = []
        for d, _ in lines:
            rings.append(out[2 * at:2 * (at + len(d))])
            at += len(d)
        return rings

    def area_k(self, made, st):
        """The Stir area's (kx, ky) for this shape (recolour.area_scales: Stretch, Round as seen)."""
        rc, notes, own, _ = made
        return area_scales(st, lambda u, v: centre_spot(rc, notes, own, [u, v]), self.app.ppq)[0]

    def ring_near(self, line, x, y):
        """How near (px) the point x, y is to the polyline line."""
        p = np.asarray(line, np.float64).reshape(-1, 2)
        if len(p) < 2:
            return math.inf
        a, d = p[:-1], p[1:] - p[:-1]  # (all pieces at once: a drawn area's line can have thousands)
        t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / np.maximum((d * d).sum(1), 1e-12), 0, 1)
        return float(np.hypot(a[:, 0] + t * d[:, 0] - x, a[:, 1] + t * d[:, 1] - y).min())

    def draw_centre_dots(self):
        self.delete("rccentre")
        s = self.scale
        r = 6 * s
        line = max(1, round(2 * s))
        room = (-10, -10, self.winfo_width() + 10, self.winfo_height() + 10)
        for _, made, spot in self.centre_boxes():
            x, y = self.centre_xy(spot)
            col = look.PATH_LINE
            rings = self.reach_rings(made)
            if rings:  # (cut to the view: never one long dashed line far off screen; a solid band in the background
                # colour under the dashes so it shows over busy colours, user 11-03)
                pieces = [piece for ring in rings for piece in clip_polyline(ring, room)]
                for piece in pieces:
                    self.create_line(*piece, fill=look.ROLL_BG, width=line + 2 * max(1, round(s)),
                                     tags="rccentre")
                for piece in pieces:
                    self.create_line(*piece, fill=look.STIR_RING, width=line, dash=(6, 4), tags="rccentre")
            box = self.area_box(made) if made[3] == "stir" and self.app.tool.get() == "select" else None
            if box:  # (its box: dotted, round handles, apart from a shape's dashed box with squares, user 11-04)
                c = box[1]
                for piece in clip_polyline([v for p in c + c[:1] for v in p], room):
                    self.create_line(*piece, fill=look.STIR_RING, width=1, dash=(2, 3), tags="rccentre")
                mids = [((c[i][0] + c[(i + 1) % 4][0]) / 2, (c[i][1] + c[(i + 1) % 4][1]) / 2) for i in range(4)]
                for (hx, hy), hr in [(p, 4.5 * s) for p in c] + [(p, 3.5 * s) for p in mids]:
                    if -20 < hx < room[2] + 10 and -20 < hy < room[3] + 10:
                        self.create_oval(hx - hr, hy - hr, hx + hr, hy + hr, outline=look.STIR_RING,
                                         fill=look.ROLL_BG, width=line, tags="rccentre")
            if made[3] == "spot":  # (the area's own spot: a square, apart from the Centre point's ring)
                q = r * 0.85
                self.create_rectangle(x - q, y - q, x + q, y + q, outline=col, fill=look.ROLL_BG, width=line,
                                      tags="rccentre")
                q = r * 0.3
                self.create_rectangle(x - q, y - q, x + q, y + q, outline="", fill=col, tags="rccentre")
                continue
            if made[3] == "stir":  # (the Stir's: a ring with a dot, apart from Clumps' cross)
                self.create_oval(x - r, y - r, x + r, y + r, outline=col, fill=look.ROLL_BG, width=line,
                                 tags="rccentre")
                q = r * 0.4
                self.create_oval(x - q, y - q, x + q, y + q, outline="", fill=col, tags="rccentre")
                continue
            self.create_oval(x - r, y - r, x + r, y + r, outline=col, fill=look.ROLL_BG, width=line,
                             tags="rccentre")
            self.create_line(x - r * 1.8, y, x + r * 1.8, y, fill=col, width=line, tags="rccentre")
            self.create_line(x, y - r * 1.8, x, y + r * 1.8, fill=col, width=line, tags="rccentre")

    def centre_hit(self, e, dots=None):
        """(its shape's Recolour, notes, box, kind) of the Centre point dot under the mouse, else None."""
        g = GRAB * self.scale
        for sh, made, spot in reversed(self.centre_boxes() if dots is None else dots):
            x, y = self.centre_xy(spot)
            if abs(x - e.x) <= g and abs(y - e.y) <= g:
                return made
        return None

    def ring_hit(self, e, dots=None):
        """(made, px away) of the nearest Stir circle line within reach of the mouse (Select tool only), else None."""
        if self.app.tool.get() != "select":
            return None
        g, best = GRAB * self.scale, None
        for sh, made, spot in reversed(self.centre_boxes() if dots is None else dots):
            d = min([self.ring_near(ring, e.x, e.y) for ring in self.reach_rings(made)] + [math.inf])
            if d <= g and (best is None or d < best[1]):
                best = (made, d)
        return best

    def centre_press(self, e):
        """A left press on a Centre point dot (any tool): grabbed (one undo step). True if taken. A Stir circle
        line NEARER than the dot is left to ring_press (a small circle's line sits by its dot, hunt 11-03)."""
        dots = self.centre_boxes()
        made = self.centre_hit(e, dots)
        if made is None:
            return False
        ring = self.ring_hit(e, dots)
        if ring:
            x, y = self.centre_xy(next(spot for _, m, spot in dots if m is made))
            if ring[1] < math.hypot(x - e.x, y - e.y):
                return False
        self.app.push_undo(name=tr({"stir": "stir.undo_centre", "spot": "stir.undo_spot"}.get(
            made[3], "shares.undo_centre")))
        self.drag = ("rccentre", made)
        return True

    # ---- the area's box (PLAN C2b, user 11-04: worked like a custom shape's box, looking different from it)

    def area_box(self, made):
        """The shown stir's area box for made (a "stir" dot): (its frame [corner (-, -), (+, -), (-, +)] in beats /
        keys, its four corners on screen going round), or None (no area)."""
        rc, notes, own, kind = made
        w = self.app.stir_window
        st = w.shown_stir(rc) if kind == "stir" and w else None
        if not st or not st["reach"]:
            return None
        m = st["spot"] or st["centre"]
        dx, dy = area_corners(st, self.area_k(made, st))
        t, k = centre_spot(rc, notes, own, [m[0] + 100 * dx, m[1] + 100 * dy])
        b, k = np.asarray(t, np.float64) / self.app.ppq, np.asarray(k, np.float64)
        x, y = (np.asarray(v, np.float64) for v in self.to_xy((b, k)))
        if min(math.hypot(x[1] - x[0], y[1] - y[0]), math.hypot(x[3] - x[0], y[3] - y[0])) < BOX_LEAST * self.scale:
            return None  # (a small area: its handles would crowd its line and dot; the line still sizes it)
        return ([[float(b[0]), float(k[0])], [float(b[1]), float(k[1])], [float(b[3]), float(k[3])]],
                list(zip(x.tolist(), y.tolist())))

    def area_box_hit(self, e, dots=None):
        """(shape, made, kind, k, frame, corners) of the area box handle under the mouse (Select tool only): kind
        "corner" k / "side" k (its middle handle only: the rest of the line is the area line's) / "turn" (just
        outside a corner) / "skew" (just outside a side's middle), as on a custom shape's box; else None."""
        if self.app.tool.get() != "select":
            return None
        s = self.scale
        for sh, made, _ in reversed(self.centre_boxes() if dots is None else dots):
            got = self.area_box(made) if made[3] == "stir" else None
            if not got:
                continue
            frame, c = got
            dist = lambda p: math.hypot(p[0] - e.x, p[1] - e.y)
            mids = [((c[i][0] + c[(i + 1) % 4][0]) / 2, (c[i][1] + c[(i + 1) % 4][1]) / 2) for i in range(4)]
            k = min(range(4), key=lambda i: dist(c[i]))
            m = min(range(4), key=lambda i: dist(mids[i]))
            hit = None
            if dist(c[k]) <= 8 * s:
                hit = ("corner", k)
            elif dist(mids[m]) <= 7 * s:
                hit = ("side", m)
            elif not self.inside_box(c, e.x, e.y):
                if dist(mids[m]) <= 20 * s and dist(mids[m]) < dist(c[k]):
                    hit = ("skew", m)
                elif dist(c[k]) <= 22 * s:
                    hit = ("turn", k)
            if hit:
                return (sh, made) + hit + (frame, c)
        return None

    def area_cursor(self, hit):
        """The pointer over an area box handle (as custom_cursor does for a shape's box)."""
        kind, k, c = hit[2], hit[3], hit[5]
        if kind == "turn":
            return "exchange"
        if kind == "corner":
            dx = dy = 0
            for n in (c[(k - 1) % 4], c[(k + 1) % 4]):
                ll = math.hypot(c[k][0] - n[0], c[k][1] - n[1]) or 1
                dx, dy = dx + (c[k][0] - n[0]) / ll, dy + (c[k][1] - n[1]) / ll
            return self.arrow_cursor(dx, dy)
        (ax, ay), (bx, by) = (c[k], c[(k + 1) % 4]) if kind == "skew" else (c[(k + 1) % 4], c[(k + 2) % 4])
        return self.arrow_cursor(bx - ax, by - ay)

    def target_stir(self, target):
        from window.stir_window import stir_at
        return stir_at(clean_recolour(target.get("recolour")), self.app.stir_window.tab)

    def round_for_box(self, target, made, frame):
        """The grabbed shape's area made Round as seen (the piano roll's look now) when it isn't, its box staying
        exactly where it is on screen (Size / Stretch / Angle / slant worked out again)."""
        from window.stir_window import with_stir
        st = self.target_stir(target)
        if st["round"]:
            return
        rc, notes, own, _ = made
        st2 = dict(st, round=True, seen=self.app.stir_window.seen_now())
        pcts = [centre_at(rc, notes, own, b * self.app.ppq, p) for b, p in frame]
        got = area_from_box(st2, *pcts, self.area_k(made, st2))
        if got is None:
            return
        put = {"round": True, "seen": st2["seen"], "reach": got["reach"], "stretch": got["stretch"],
               "tilt": got["tilt"], "skew": got["skew"]}
        i = self.app.stir_window.tab
        self.app.change_recolour(lambda old, sh: with_stir(old, i, lambda s: dict(s, **put)) if sh is target else old,
                                 None, False)

    def area_box_drag(self, e):
        """The area box handle held: the grabbed shape's area resized / turned / slanted like a custom shape's box
        (Ctrl on a corner keeps its proportions, Shift turns freely, else in 15° steps), the box turned back into
        its middle, Size, Stretch, Angle and slant (recolour.area_from_box)."""
        from window.stir_window import with_stir
        _, made, _, kind, k, orig, start, a0, target = self.drag
        rc, notes, own, _ = made
        w = self.app.stir_window
        if kind in ("corner", "side"):
            new = self.resize_custom(orig, k, self.event_pt(e, snap=False), bool(e.state & CTRL), side=kind == "side")
        elif kind == "turn":
            a = self.screen_angle(orig, e.x, e.y) - a0
            if not e.state & SHIFT:
                a = round(a / (math.pi / 12)) * (math.pi / 12)
            new = self.turn_custom(orig, a)
        else:
            pt = self.event_pt(e, snap=False)
            new = self.skew_custom(orig, k, pt[0] - start[0], pt[1] - start[1])
        st = self.target_stir(target)  # (as it is now: the press may have made it round as seen)
        pcts = [centre_at(rc, notes, own, b * self.app.ppq, p) for b, p in new]
        got = area_from_box(st, *pcts, self.area_k(made, st))
        if got is None:  # (turned inside out / flat: stays as it was last)
            return
        middle = [round(min(max(v, 0.0), 100.0), 3) for v in got["middle"]]
        put = {"reach": round(min(max(got["reach"], LEAST_REACH), MOST_REACH), 3),
               "stretch": round(min(max(got["stretch"], LEAST_STRETCH), MOST_STRETCH), 3),
               "tilt": round(got["tilt"], 3), "skew": round(min(max(got["skew"], -MOST_SKEW), MOST_SKEW), 4)}
        i = w.tab

        def changed(old, sh):
            if sh is not target:
                return old
            return with_stir(old, i, lambda st: dict(st, **put, **({"spot": middle} if st["spot"] else
                                                                  {"centre": middle})))
        self.app.change_recolour(changed, None, False)

    def ring_press(self, e):
        """A left press on a Stir's Only inside a circle line, Select tool only, after the shape's own handles and
        box sides had their turn (hunt 11-03: it took presses meant for them): its size grabbed (one undo step).
        The area box's handles come first (PLAN C2b)."""
        hit = self.area_box_hit(e)
        if hit:
            sh, made, kind, k, frame, _ = hit
            self.app.push_undo(name=tr("stir.undo_area_box"))
            if kind in ("turn", "skew"):  # (turning / slanting goes as seen on screen, like a custom shape's box:
                # Round as seen ticked first, the area kept exactly where it is)
                self.round_for_box(sh, made, frame)
            self.drag = ("rccentre", made, "box", kind, k, frame, self.event_pt(e, snap=False),
                         self.screen_angle(frame, e.x, e.y), sh)
            return True
        ring = self.ring_hit(e)
        if ring is None:
            return False
        self.app.push_undo(name=tr("stir.undo_reach"))
        self.drag = ("rccentre", ring[0], "reach")
        return True

    def centre_right(self, e):
        """A right-click on a Centre point dot: every selected shape's Centre point of that kind back to the
        middle (like Nested's; one undo step, none when nothing moves). True if taken."""
        made = self.centre_hit(e)
        if made is None:
            return False
        if made[3] in ("stir", "spot"):  # (the area's own spot: back to the middle too)
            self.set_centre(made[3], [50.0, 50.0], tr("stir.undo_" + ("centre" if made[3] == "stir" else "spot")))
        else:
            self.app.change_recolour(lambda old, sh: {k: v for k, v in old.items() if k != "centre"}
                                     if old and old["mode"] == "clumps" else old, tr("shares.undo_centre"))
        return True

    def set_centre(self, kind, c, name, push=True):
        if kind == "stir":
            from window.stir_window import with_stir
            i = self.app.stir_window.tab

            def changed(old, sh):
                return with_stir(old, i, lambda st: dict(st, centre=c))
        elif kind == "spot":  # (only stirs whose area has its own spot: the others stay as they are)
            from window.stir_window import stir_at, with_stir
            i = self.app.stir_window.tab

            def changed(old, sh):
                st = stir_at(old, i)
                return with_stir(old, i, lambda st: dict(st, spot=c)) if st["reach"] is not None and st["spot"] \
                    else old
        else:
            def changed(old, sh):
                return dict(old, centre=c) if old and old["mode"] == "clumps" else old
        self.app.change_recolour(changed, name, push)

    def centre_drag(self, e):
        rc, notes, own, kind = self.drag[1]
        if not getattr(self.app, "shares_window" if kind == "clumps" else "stir_window", None):
            self.drag = None  # (the window closed mid-drag: let go)
            return
        if len(self.drag) > 2 and self.drag[2] == "box":
            return self.area_box_drag(e)
        b, p = self.from_xy(e.x, e.y)
        c = centre_at(rc, notes, own, b * self.app.ppq, p)  # (the grabbed shape's box: under the mouse)
        if len(self.drag) > 2:  # (the area line: its size = twice the mouse's distance from the Centre point,
            # measured the area's way, so the line stays under the mouse on a square / diamond too)
            from window.stir_window import stir_at, with_stir
            w = self.app.stir_window
            st, i = w.shown_stir(rc), w.tab
            m = st["spot"] or st["centre"]
            X, Y = (float(v) for v in area_local((c[0] - m[0]) / 100, (c[1] - m[1]) / 100, st,
                                                 self.area_k(self.drag[1], st)))
            size = st["reach"] * (drawn_gauge(X, Y, st["shape"]) if st["area"] == DRAWN and st["shape"] else
                                  float(area_dist(X, Y, st["area"])))
            size = round(size, 3) if e.state & SHIFT else float(round(size))
            size = min(max(size, LEAST_REACH if e.state & SHIFT else 1.0), MOST_REACH)
            # (only shapes whose shown stir has a circle: a whole-shape stir stays one, hunt 11-03)
            self.app.change_recolour(lambda old, sh: with_stir(old, i, lambda st: dict(st, reach=size))
                                     if stir_at(old, i)["reach"] else old, None, False)
            return
        c = [min(max(v, 0.0), 100.0) for v in c]
        c = [round(v, 3) if e.state & SHIFT else float(round(v)) for v in c]
        self.set_centre(kind, c, None, push=False)
