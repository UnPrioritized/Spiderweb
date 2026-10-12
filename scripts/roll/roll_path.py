"""The piano roll's part of Copy along a path (window/path_window.py): while its window is open, the path through A (a
corner of the selected shapes), B, C, ... is drawn, with each stretch's middle point. A point not placed yet (B, or
one from Add point) follows the mouse (the copies too) until a click puts it there. Then the points and the middle
points can be dragged (a middle point bends a straight path into a curve); right-click a point to remove it, or the
one following the mouse to stop adding. Snapped like drawing (time to the grid, keys to whole rows; Shift = free),
and free to go past the piano roll's edges (notes there are cut off)."""

from notes.copypath import LETTERS, all_points, clean_path, mid_of, track
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
        """{("pt", i) / ("mid", i): (x, y)} on screen (i = 0: B / the stretch A-B), with "a", or None."""
        fr, cl = w.frame(), clean_path(w.cfg)
        if fr is None or cl is None:
            return None
        (b0, k0), size = fr
        pts = all_points(cl, size)
        out = {"a": self.to_xy((b0 + pts[0][0], k0 + pts[0][1]))}
        for i in range(len(pts) - 1):
            m = mid_of(cl, size, i, pts)
            out[("mid", i)] = self.to_xy((b0 + m[0], k0 + m[1]))
            out[("pt", i)] = self.to_xy((b0 + pts[i + 1][0], k0 + pts[i + 1][1]))
        return out

    def path_hit(self, x, y):
        """The path's point under the mouse (points first, the last one on top, then middle points), or None."""
        w = self.path_win()
        spots = w and self.path_spots(w)
        if not spots:
            return None
        n = len(w.cfg["pts"])
        g = GRAB * self.scale
        for which in [("pt", i) for i in reversed(range(n))] + [("mid", i) for i in reversed(range(n))]:
            sx, sy = spots[which]
            if abs(sx - x) <= g and abs(sy - y) <= g:
                return which
        return None

    def path_press(self, e):
        """A left press while the window is open: the point following the mouse placed, or a point grabbed. True if
        taken."""
        w = self.path_win()
        if w is None:
            return False
        if w.placing():
            w.place(*self.path_pt(e), self.path_k())
            return True
        which = self.path_hit(e.x, e.y)
        if which is None:
            return False
        if which[0] == "pt":
            w.pick = which[1]
            w.show()
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
        """A point still to be placed follows the mouse (the copies too). True if so."""
        w = self.path_win()
        if w is None or not w.placing() or e.x < self.kb_w or e.y < self.ruler_h:
            return False
        w.follow(*self.path_pt(e), self.path_k())
        return True

    def path_right(self, e):
        """A right-click while the window is open: the added point following the mouse dropped, or the point under
        the mouse removed. True if taken."""
        w = self.path_win()
        if w is None:
            return False
        if w.stop_adding():
            return True
        which = self.path_hit(e.x, e.y)
        if which is None or which[0] != "pt":
            return False
        w.remove_point(which[1])
        return True

    def draw_copy_path(self):
        """The path, A, its points and middle points (while the window is open)."""
        self.delete("copypath")
        w = self.path_win()
        fr = w and w.frame()
        if not fr:
            return
        s = self.scale
        r = 4 * s
        cl = clean_path(w.cfg)
        if cl is None:
            x, y = self.to_xy(fr[0])
            self.create_oval(x - r, y - r, x + r, y + r, outline="", fill=look.PATH_LINE, tags="copypath")
            return
        (b0, k0), size = fr
        pts = track(cl, size, 128)[0]
        xy = [v for b, p in pts for v in self.to_xy((b0 + b, k0 + p))]
        line = max(1, round(2 * s))
        self.create_line(*xy, fill=look.PATH_LINE, width=line, dash=(6, 3), tags="copypath")
        spots = self.path_spots(w)
        n = len(cl["pts"])
        font = look.font(9, "bold", family=look.TK)
        for i in range(n):
            x, y = spots[("mid", i)]
            self.create_oval(x - r, y - r, x + r, y + r, outline=look.PATH_LINE, fill=look.ROLL_BG, width=line,
                             tags="copypath")
        for i in range(n):
            x, y = spots[("pt", i)]
            big = r * 1.5 if i == w.pick and n > 1 else r  # (the point the window's boxes show)
            self.create_rectangle(x - big, y - big, x + big, y + big, outline="", fill=look.PATH_LINE,
                                  tags="copypath")
            self.create_text(x + r + 3 * s, y - r - 2 * s, text=LETTERS[i + 1], anchor="sw", fill=look.PATH_LINE,
                             font=font, tags="copypath")
        x, y = spots["a"]
        self.create_oval(x - r, y - r, x + r, y + r, outline="", fill=look.PATH_LINE, tags="copypath")
        self.create_text(x + r + 3 * s, y - r - 2 * s, text=LETTERS[0], anchor="sw", fill=look.PATH_LINE, font=font,
                         tags="copypath")
