"""The piano roll's part of Recolour's Centre points (notes/recolour.py): while the Shares window is open, each
selected shape using Split: Clumps' Swirl or Wedges shows its Centre point as a dot where its areas turn; while the
Stir window is open (window/stir_window.py), each selected shape shows its Stir's Centre point. Dots sit in the box
the colours count in (a sliced piece's whole's, flipped / turned with the shape: recolour.centre_spot); dragging one
sets that Centre point (in % of that box, like the windows' boxes) of every selected shape of its kind. Whole %
(Shift = free); one undo step a drag."""

from files.lang import tr
from notes.engine import free_box
from notes.recolour import centre_at, centre_spot, clean_recolour, full_stir, without_recolour
from roll.roll_shared import SHIFT
from window import look

GRAB = 8  # px: how near the dot the mouse must be to grab it


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
                c = full_stir((rc or {}).get("stir"))["centre"] if kind == "stir" else clumps_centre(rc)
                if c is None:
                    continue
                plain = without_recolour(sh)
                notes, _ = app.notes_tracks(plain)
                if not len(notes):
                    continue
                own = free_box(plain, notes, app.notes_tracks, app.keys)
                out.append((sh, (rc or {}, notes, own, kind), centre_spot(rc or {}, notes, own, c)))
        return out

    def centre_xy(self, spot):
        return self.to_xy((spot[0] / self.app.ppq, spot[1]))

    def draw_centre_dots(self):
        self.delete("rccentre")
        s = self.scale
        r = 6 * s
        line = max(1, round(2 * s))
        for _, made, spot in self.centre_boxes():
            x, y = self.centre_xy(spot)
            col = look.PATH_LINE
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

    def centre_hit(self, e):
        """(its shape's Recolour, notes, box, kind) of the Centre point dot under the mouse, else None."""
        g = GRAB * self.scale
        for sh, made, spot in reversed(self.centre_boxes()):
            x, y = self.centre_xy(spot)
            if abs(x - e.x) <= g and abs(y - e.y) <= g:
                return made
        return None

    def centre_press(self, e):
        """A left press on a Centre point dot: grabbed (one undo step). True if taken."""
        made = self.centre_hit(e)
        if made is None:
            return False
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
            from window.stir_window import stirred_rc

            def changed(old, sh):
                return stirred_rc(old, dict(full_stir((old or {}).get("stir")), centre=c))
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
        c = [min(max(v, 0.0), 100.0) for v in c]
        c = [round(v, 3) if e.state & SHIFT else float(round(v)) for v in c]
        self.set_centre(kind, c, None, push=False)
