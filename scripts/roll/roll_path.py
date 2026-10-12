"""The piano roll's part of Copy along a path (window/path_window.py): while its window is open, the path from A (where
the selected shapes start) to B is drawn, with A, its middle point and B. B not placed yet: it follows the mouse
(the copies too) until a click puts it there. Then B and the middle point can be dragged (the middle point bends a
straight path into a curve). Snapped like drawing (time to the grid, keys to whole rows; Shift = free), and free to
go past the piano roll's edges (notes there are cut off)."""

from notes.copypath import clean_path, middle, path_points
from roll.roll_shared import SHIFT
from window import look

GRAB = 8  # px: how near a point the mouse must be to grab it


class PathEditing:
    def path_win(self):
        """The open Copy along a path window with shapes to work on, or None."""
        w = self.app.path_window
        return w if w and w.targets else None

    def path_pt(self, e):
        """The mouse as (beat, key): time on the grid, whole keys; Shift = exactly there. Not kept inside the roll."""
        b, p = self.x2t(e.x), self.y2p(e.y)
        sb = self.app.snap_beats()
        if sb and not e.state & SHIFT:
            b, p = round(b / sb) * sb, round(p)
        return b, p

    def path_k(self):
        """Beats per key on screen now (how round an arc is, how long the path is)."""
        return self.sy / self.sx

    def path_spots(self, w):
        """{"a" / "mid" / "b": (x, y)} on screen, or None."""
        a, b = w.anchor(), w.end_point()
        if a is None or b is None:
            return None
        m = middle(w.cfg)
        return {"a": self.to_xy(a), "mid": self.to_xy((a[0] + m[0], a[1] + m[1])), "b": self.to_xy(b)}

    def path_hit(self, x, y):
        """The path's point under the mouse ("b" first, then "mid"), or None."""
        w = self.path_win()
        spots = w and self.path_spots(w)
        for which in ("b", "mid"):
            if spots and abs(spots[which][0] - x) <= GRAB * self.scale and abs(spots[which][1] - y) <= GRAB * self.scale:
                return which
        return None

    def path_press(self, e):
        """A left press while the window is open: B placed (still to be placed), or a point grabbed. True if taken."""
        w = self.path_win()
        if w is None:
            return False
        if w.placing():
            w.set_point("b", *self.path_pt(e), self.path_k(), True)
            return True
        which = self.path_hit(e.x, e.y)
        if which is None:
            return False
        self.drag = ("path", which)
        return True

    def path_drag(self, e):
        w = self.path_win()
        if w:
            w.set_point(self.drag[1], *self.path_pt(e), self.path_k(), False)

    def path_release(self, e):
        w = self.path_win()
        if w:
            w.set_point(self.drag[1], *self.path_pt(e), self.path_k(), True)

    def path_motion(self, e):
        """B still to be placed follows the mouse (the copies too). True if so."""
        w = self.path_win()
        if w is None or not w.placing() or e.x < self.kb_w or e.y < self.ruler_h:
            return False
        w.follow(*self.path_pt(e), self.path_k())
        return True

    def draw_copy_path(self):
        """The path, A, its middle point and B (while the window is open)."""
        self.delete("copypath")
        w = self.path_win()
        a = w and w.anchor()
        if a is None:
            return
        s = self.scale
        r = 4 * s
        cl = clean_path(w.cfg) if w.cfg.get("b") is not None else None
        if cl:
            pts = path_points(cl, 256)
            xy = [v for b, p in pts for v in self.to_xy((a[0] + b, a[1] + p))]
            self.create_line(*xy, fill=look.PATH_LINE, width=max(1, round(2 * s)), dash=(6, 3), tags="copypath")
            spots = self.path_spots(w)
            x, y = spots["mid"]
            self.create_oval(x - r, y - r, x + r, y + r, outline=look.PATH_LINE, fill=look.ROLL_BG,
                             width=max(1, round(2 * s)), tags="copypath")
            x, y = spots["b"]
            self.create_rectangle(x - r, y - r, x + r, y + r, outline="", fill=look.PATH_LINE, tags="copypath")
        x, y = self.to_xy(a)
        self.create_oval(x - r, y - r, x + r, y + r, outline="", fill=look.PATH_LINE, tags="copypath")
