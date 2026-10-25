"""The piano roll's part of Recolour's Split: Clumps (notes/recolour.py areas, window/shares_window.py): while the
Shares window is open, each selected shape using Swirl or Wedges shows its Centre point as a dot where its areas
turn (in the box they count in: a sliced piece's whole's, flipped / turned with the shape: recolour.centre_spot);
dragging a dot sets the Centre point (in % of that box, like the window's boxes) of every selected Clumps shape.
Whole % (Shift = free); one undo step a drag."""

from files.lang import tr
from notes.engine import free_box
from notes.recolour import centre_at, centre_spot, clean_recolour, without_recolour
from roll.roll_shared import SHIFT
from window import look

GRAB = 8  # px: how near the dot the mouse must be to grab it


class CentreDots:
    def centre_boxes(self):
        """[(shape, (its Recolour, its notes without it, their box past the piano roll's edges), the dot's (tick,
        key))] of the selected Clumps shapes turning around a point, while the Shares window is open."""
        app = self.app
        if not getattr(app, "shares_window", None):
            return []
        out = []
        for sh in app.recolour_targets():
            rc = clean_recolour(sh.get("recolour"))
            if not rc or rc["mode"] != "clumps" or rc.get("pattern", "bands") == "bands":
                continue
            plain = without_recolour(sh)
            notes, _ = app.notes_tracks(plain)
            if not len(notes):
                continue
            own = free_box(plain, notes, app.notes_tracks, app.keys)
            out.append((sh, (rc, notes, own), centre_spot(rc, notes, own, rc.get("centre", [50, 50]))))
        return out

    def centre_xy(self, spot):
        return self.to_xy((spot[0] / self.app.ppq, spot[1]))

    def draw_centre_dots(self):
        self.delete("rccentre")
        s = self.scale
        r = 6 * s
        line = max(1, round(2 * s))
        for _, _, spot in self.centre_boxes():
            x, y = self.centre_xy(spot)
            self.create_oval(x - r, y - r, x + r, y + r, outline=look.PATH_LINE, fill=look.ROLL_BG, width=line,
                             tags="rccentre")
            self.create_line(x - r * 1.8, y, x + r * 1.8, y, fill=look.PATH_LINE, width=line, tags="rccentre")
            self.create_line(x, y - r * 1.8, x, y + r * 1.8, fill=look.PATH_LINE, width=line, tags="rccentre")

    def centre_press(self, e):
        """A left press on a Centre point dot: grabbed (one undo step). True if taken."""
        g = GRAB * self.scale
        for sh, made, spot in reversed(self.centre_boxes()):
            x, y = self.centre_xy(spot)
            if abs(x - e.x) <= g and abs(y - e.y) <= g:
                self.app.push_undo(name=tr("shares.undo_centre"))
                self.drag = ("rccentre", made)
                return True
        return False

    def centre_drag(self, e):
        if not getattr(self.app, "shares_window", None):  # (the window closed mid-drag: let go)
            self.drag = None
            return
        rc, notes, own = self.drag[1]
        b, p = self.from_xy(e.x, e.y)
        c = centre_at(rc, notes, own, b * self.app.ppq, p)  # (the grabbed shape's box: under the mouse)
        c = [min(max(v, 0.0), 100.0) for v in c]
        c = [round(v, 3) if e.state & SHIFT else float(round(v)) for v in c]

        def changed(old, sh):
            if not old or old["mode"] != "clumps":
                return old
            return dict(old, centre=c)
        self.app.change_recolour(changed, None, push=False)
