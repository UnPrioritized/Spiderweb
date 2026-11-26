"""The Slice tool's mouse work on the piano roll, its press / drag / let go together (split out of pianoroll.on_press /
on_drag / on_release, which only hand the click on): Slice (drag a line: what it crosses is cut on let go) and Slice
with shape (a click: the shape under the mouse cuts with its notes)."""

import math

from files.lang import tr
from notes.mask import is_mask
from roll.roll_shared import SHIFT


class SliceTool:
    """Mixin for PianoRoll: the Slice tool's press / drag / let go."""

    def press_slice(self, e, kept):
        app = self.app
        if app.slice_mode.get() == "shape":  # (Slice with shape: the click's shape cuts on let go;
            if kept:  # the Select boxes go with the cut's undo step, then away: hunt 10-26)
                self.box_kept = (kept, set(app.sels))
            self.drag = ("knife", e.x, e.y)
            return
        # (the kept Select boxes stay: only what's in them is cut)
        if kept:
            self.box_kept = (kept, set(app.sels))
        pt = self.slice_pt(e)
        self.drag = ("slice", pt, pt, e.x, e.y)
        self.draw_slice()

    def slice_pt(self, e):
        """The Slice tool's line end at the mouse: time on the snap grid, keys on the lines between key rows (so a
        flat cut runs between two keys); Shift = exactly there."""
        b, _ = self.event_pt(e, snap=False)
        # (keys from the mouse itself: event_pt stops at key 0's middle, so the line couldn't reach under the
        # bottom key)
        p = self.y2p(min(max(e.y, self.ruler_h), self.winfo_height()))
        sb = self.app.snap_beats()
        if not e.state & SHIFT:
            b, p = (round(b / sb) * sb if sb else b), math.floor(p) + 0.5
        _, lo, hi = self.room_edges()
        return [b, min(max(p, lo - 0.5), hi + 0.5)]

    def drag_slice(self, e):
        self.drag = ("slice", self.drag[1], self.slice_pt(e), *self.drag[3:])
        self.draw_slice()

    def release_slice(self, e):
        """(a click without dragging cuts nothing)"""
        _, a, b, x, y = self.drag
        self.drag = None
        self.draw_slice()
        if abs(e.x - x) >= 4 or abs(e.y - y) >= 4:
            self.app.slice_along(a, b)

    def release_knife(self, e):
        """(a drag cuts nothing)"""
        _, x, y = self.drag
        self.drag = None
        if abs(e.x - x) < 4 and abs(e.y - y) < 4:
            self.knife_click(x, y)
        if self.box_kept:
            self.box_kept = None
            self.request_redraw()

    def knife_click(self, x, y):
        """Slice with shape: the top shape under x, y (masks never cut: looked through) cuts with its notes, when it
        can (App.can_knife); a line over pasted notes = the line (can't cut), never the notes under it."""
        app = self.app
        i = self.knife_at(x, y)
        if i is not None and app.can_knife(app.shapes[i]):
            app.knife_cut(i)
        else:
            app.status.config(text=tr("join_split.knife_cant" if i is not None else "join_split.knife_click"))

    def knife_at(self, x, y, near=None):
        """The shape a Slice with shape click at x, y is for: the first of shapes_at that isn't a mask or hidden,
        or None. near (near_shapes, for the pointer on every mouse move): only those are tried by their lines."""
        app = self.app
        order = [i for i in range(len(app.shapes) - 1, -1, -1)
                 if i not in app.hidden_shapes and not is_mask(app.shapes[i])]
        mine = set(order)
        for i in order if near is None else [i for i in near if i in mine]:
            if self.hit_shape(x, y, [i]) is not None:
                return i
        owners = self.note_owners(x, y)
        return next((i for i in order if i in owners), None)
