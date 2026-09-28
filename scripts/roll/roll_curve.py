"""Piano roll: editing a Curve shape with anchors and handles, and symmetric curves (the pen tool editing itself is
in bezier.py, shared with the drawer)."""

import json

from files.lang import tr
from notes.bezier import (add_anchor, can_delete, delete_point, drag_point, half_at, handle_lines, keep_symmetric,
                          nearest, pen_handles, set_symmetry)
from roll.roll_shared import ALT


class CurveEditing:
    """Mixed into PianoRoll."""

    def to_xy(self, p):
        return self.t2x(p[0]), self.p2y(p[1])

    def from_xy(self, x, y):
        return [self.x2t(x), self.y2p(y)]

    @staticmethod
    def curve_handles(sh):
        """(beat, pitch, point number, draggable with any tool) of a curve: handle points, anchors on top. The two
        ends need the Select tool (like a line's ends), so the Curve tool can start a new curve there."""
        pts = sh["pts"]
        return [(*pts[i], i, kind != "end") for i, kind in pen_handles(pts, gaps=sh.get("gaps", ()))]

    @staticmethod
    def curve_handle_lines(sh):
        return handle_lines(sh["pts"], sh.get("gaps", ()))

    def keep_symmetric(self, sh, i=0):
        """A symmetric curve's other half follows the half point i is in. True if it's symmetric."""
        return sh["kind"] == "curve" and self.sx is not None and keep_symmetric(sh, i, self.to_xy)

    def drag_curve(self, sh, i, e):
        """Dragging a curve's point (snapped unless Shift), see bezier.drag_point (Alt = sharp / new handles)."""
        drag_point(sh, i, self.event_pt(e), e.state & ALT, self.to_xy, self.from_xy)

    def curve_click(self, sh, e, near=None):
        """A new anchor on the curve where it's nearest to the mouse, moved to the mouse (snapped unless Shift).
        near: only if the curve is that close (pixels). True if one was added."""
        seg, t, d = nearest(sh["pts"], self.to_xy, e.x, e.y, gaps=sh.get("gaps", ()))
        if near is not None and d > near:
            return False
        before = json.dumps(self.app.shapes)
        if not add_anchor(sh, seg, t, self.event_pt(e), self.to_xy):
            return False
        self.app.push_undo(before, tr("roll_curve.add_an_anchor"))
        self.app.shape_edited()
        self.app.build_points()
        return True

    def delete_curve_handle(self, sh, i):
        """Right-click on an anchor between the ends (removed) or a handle point of one (pulled back in).
        True if it did something."""
        what = can_delete(sh, i)
        if what is None:
            return False
        if what == "middle":
            self.app.status.config(text=tr("roll_curve.the_middle_anchor_of_a_symmetric"))
            return True
        self.app.push_undo(name=tr("roll_curve.remove_a_point"))
        delete_point(sh, i, self.to_xy)
        self.app.shape_edited()
        self.app.build_points()
        return True

    def set_symmetry(self, sh, mode, at):
        """Symmetric halves on (mode "mirror" / "turn") or off (None). The half nearest to `at` (where it was
        right-clicked) keeps its shape, the other half follows it."""
        if (sh.get("sym") or None) == mode:
            return
        self.app.push_undo(name=tr("roll_curve.symmetric_halves"))
        set_symmetry(sh, mode, half_at(sh["pts"], self.to_xy, at.x, at.y), self.to_xy)
        self.app.shape_edited()
        self.app.build_points()
