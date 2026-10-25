"""The piano roll's part of Recolour's Split: Clumps (notes/recolour.py areas, window/shares_window.py): while the
Shares window is open, each selected shape using Swirl or Wedges shows its Centre point as a dot on its notes' box;
dragging a dot sets the Centre point (in % of the box, like the window's boxes) of every selected Clumps shape. Whole
% (Shift = free); one undo step a drag."""

from files.lang import tr
from notes.recolour import clean_recolour, framed_centre, without_recolour
from roll.roll_shared import SHIFT
from window import look

GRAB = 8  # px: how near the dot the mouse must be to grab it


class CentreDots:
    def centre_boxes(self):
        """[(shape, (first tick, last tick, lowest key, highest key) of its notes, centre %)] of the selected Clumps
        shapes turning around a point, while the Shares window is open."""
        app = self.app
        if not getattr(app, "shares_window", None):
            return []
        out = []
        for sh in app.recolour_targets():
            rc = clean_recolour(sh.get("recolour"))
            if not rc or rc["mode"] != "clumps" or rc.get("pattern", "bands") == "bands":
                continue
            notes, _ = app.notes_tracks(without_recolour(sh))
            if not len(notes):
                continue
            box = (int(notes[:, 0].min()), int(notes[:, 1].max()), int(notes[:, 2].min()), int(notes[:, 2].max()))
            out.append((sh, box, framed_centre(rc, rc.get("centre", [50, 50]))))  # (flipped / turned since: there)
        return out

    def centre_xy(self, box, c):
        t0, t1, k0, k1 = box
        return self.to_xy(((t0 + c[0] / 100 * (t1 - t0)) / self.app.ppq, k0 + c[1] / 100 * (k1 - k0)))

    def draw_centre_dots(self):
        self.delete("rccentre")
        s = self.scale
        r = 6 * s
        line = max(1, round(2 * s))
        for _, box, c in self.centre_boxes():
            x, y = self.centre_xy(box, c)
            self.create_oval(x - r, y - r, x + r, y + r, outline=look.PATH_LINE, fill=look.ROLL_BG, width=line,
                             tags="rccentre")
            self.create_line(x - r * 1.8, y, x + r * 1.8, y, fill=look.PATH_LINE, width=line, tags="rccentre")
            self.create_line(x, y - r * 1.8, x, y + r * 1.8, fill=look.PATH_LINE, width=line, tags="rccentre")

    def centre_press(self, e):
        """A left press on a Centre point dot: grabbed (one undo step). True if taken."""
        g = GRAB * self.scale
        for sh, box, c in reversed(self.centre_boxes()):
            x, y = self.centre_xy(box, c)
            if abs(x - e.x) <= g and abs(y - e.y) <= g:
                self.app.push_undo(name=tr("shares.undo_centre"))
                self.drag = ("rccentre", box)
                return True
        return False

    def centre_drag(self, e):
        if not getattr(self.app, "shares_window", None):  # (the window closed mid-drag: let go)
            self.drag = None
            return
        t0, t1, k0, k1 = self.drag[1]
        b, p = self.from_xy(e.x, e.y)
        c = [100 * (b * self.app.ppq - t0) / max(t1 - t0, 1), 100 * (p - k0) / (k1 - k0) if k1 > k0 else 50.0]
        c = [min(max(v, 0.0), 100.0) for v in c]
        c = [round(v, 3) if e.state & SHIFT else float(round(v)) for v in c]

        def changed(old, sh):
            if not old or old["mode"] != "clumps":
                return old
            return dict(old, centre=[round(v, 3) for v in framed_centre(old, c, back=True)])
        self.app.change_recolour(changed, None, push=False)
