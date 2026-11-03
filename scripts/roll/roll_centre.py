"""The piano roll's part of Recolour's Centre points (notes/recolour.py): while the Shares window is open, each
selected shape using Split: Clumps' Swirl or Wedges shows its Centre point as a dot where its areas turn; while the
Stir window is open (window/stir_window.py), each selected shape shows its Stir's Centre point. Dots sit in the box
the colours count in (a sliced piece's whole's, flipped / turned with the shape: recolour.centre_spot); dragging one
sets that Centre point (in % of that box, like the windows' boxes) of every selected shape of its kind. Whole %
(Shift = free); one undo step a drag."""

import math

from files.lang import tr
from notes.engine import free_box
from notes.recolour import LEAST_REACH, MOST_REACH, centre_at, centre_spot, clean_recolour, without_recolour
from roll.roll_gliss import clip_polyline
from roll.roll_shared import SHIFT
from window import look

GRAB = 8  # px: how near the dot (or a Stir's circle line) the mouse must be to grab it
RING_POINTS = 72  # (the circle line's pieces)


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
                c = app.stir_window.shown_stir(rc)["centre"] if kind == "stir" else clumps_centre(rc)
                if c is None:
                    continue
                plain = without_recolour(sh)
                notes, _ = app.notes_tracks(plain)
                if not len(notes):
                    continue
                own = free_box(plain, notes, app.notes_tracks, app.keys)
                out.append((sh, (rc or {}, notes, own, kind), centre_spot(rc or {}, notes, own, c)))
        # (both windows open: the dots of the one used last drawn on top and grabbed first, hunt 2026-11-02)
        out.sort(key=lambda d: d[1][3] == getattr(app, "centre_first", None))
        return out

    def centre_xy(self, spot):
        return self.to_xy((spot[0] / self.app.ppq, spot[1]))

    def reach_ring(self, made):
        """The Stir's Only inside a circle line on the piano roll, [x0, y0, x1, y1, ...] closed (in the colour box,
        so an oval on a box that isn't square; flipped / turned with the shape), or None."""
        rc, notes, own, kind = made
        w = self.app.stir_window
        st = w.shown_stir(rc) if kind == "stir" and w else None
        if not st or not st["reach"]:
            return None
        cx, cy = st["centre"]
        r = st["reach"] / 2
        out = []
        for j in range(RING_POINTS + 1):
            a = 2 * math.pi * j / RING_POINTS
            out += self.centre_xy(centre_spot(rc, notes, own, [cx + r * math.cos(a), cy + r * math.sin(a)]))
        return out

    def ring_near(self, line, x, y):
        """How near (px) the point x, y is to the polyline line."""
        best = math.inf
        for j in range(0, len(line) - 2, 2):
            x0, y0, x1, y1 = line[j:j + 4]
            dx, dy = x1 - x0, y1 - y0
            t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / max(dx * dx + dy * dy, 1e-12)))
            best = min(best, math.hypot(x0 + t * dx - x, y0 + t * dy - y))
        return best

    def draw_centre_dots(self):
        self.delete("rccentre")
        s = self.scale
        r = 6 * s
        line = max(1, round(2 * s))
        room = (-10, -10, self.winfo_width() + 10, self.winfo_height() + 10)
        for _, made, spot in self.centre_boxes():
            x, y = self.centre_xy(spot)
            col = look.PATH_LINE
            ring = self.reach_ring(made)
            if ring:  # (thin, dashed, cut to the view: never one long dashed line far off screen)
                for piece in clip_polyline(ring, room):
                    self.create_line(*piece, fill=col, width=line, dash=(4, 3), tags="rccentre")
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

    def centre_hit(self, e, rings=False):
        """(its shape's Recolour, notes, box, kind) of the Centre point dot under the mouse, else None. rings: also
        a Stir's circle line, as (made, "reach") (dots win)."""
        g = GRAB * self.scale
        dots = list(reversed(self.centre_boxes()))
        for sh, made, spot in dots:
            x, y = self.centre_xy(spot)
            if abs(x - e.x) <= g and abs(y - e.y) <= g:
                return made
        if rings:
            for sh, made, spot in dots:
                ring = self.reach_ring(made)
                if ring and self.ring_near(ring, e.x, e.y) <= g:
                    return made, "reach"
        return None

    def centre_press(self, e):
        """A left press on a Centre point dot (or a Stir's circle line: its size): grabbed (one undo step). True if
        taken."""
        made = self.centre_hit(e, rings=True)
        if made is None:
            return False
        if len(made) == 2:  # ((made, "reach"): the circle line)
            self.app.push_undo(name=tr("stir.undo_reach"))
            self.drag = ("rccentre", made[0], "reach")
            return True
        self.app.push_undo(name=tr("stir.undo_centre" if made[3] == "stir" else "shares.undo_centre"))
        self.drag = ("rccentre", made)
        return True

    def centre_right(self, e):
        """A right-click on a Centre point dot: every selected shape's Centre point of that kind back to the
        middle (like Nested's; one undo step, none when nothing moves). True if taken."""
        made = self.centre_hit(e)
        if made is None:
            return False
        if made[3] == "stir":
            self.set_centre("stir", [50.0, 50.0], tr("stir.undo_centre"))
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
        else:
            def changed(old, sh):
                return dict(old, centre=c) if old and old["mode"] == "clumps" else old
        self.app.change_recolour(changed, name, push)

    def centre_drag(self, e):
        rc, notes, own, kind = self.drag[1]
        if not getattr(self.app, "stir_window" if kind == "stir" else "shares_window", None):
            self.drag = None  # (the window closed mid-drag: let go)
            return
        b, p = self.from_xy(e.x, e.y)
        c = centre_at(rc, notes, own, b * self.app.ppq, p)  # (the grabbed shape's box: under the mouse)
        if len(self.drag) > 2:  # (the circle line: its size = twice the mouse's distance from the Centre point)
            from window.stir_window import with_stir
            w = self.app.stir_window
            m, i = w.shown_stir(rc)["centre"], w.tab
            size = 2 * math.hypot(c[0] - m[0], c[1] - m[1])
            size = round(size, 3) if e.state & SHIFT else float(round(size))
            size = min(max(size, LEAST_REACH if e.state & SHIFT else 1.0), MOST_REACH)
            self.app.change_recolour(lambda old, sh: with_stir(old, i, lambda st: dict(st, reach=size)), None, False)
            return
        c = [min(max(v, 0.0), 100.0) for v in c]
        c = [round(v, 3) if e.state & SHIFT else float(round(v)) for v in c]
        self.set_centre(kind, c, None, push=False)
