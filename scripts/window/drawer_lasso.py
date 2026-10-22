"""Mixed into Drawer: the Lasso tool on the board (user 2026-10-22, planned-features.md "Lasso slice": Lasso +
Polygonal + curves, no Magnetic: points stick like drawing). The piano roll's lasso (roll/roll_lasso.py) with the
drawer's own points, words and cut: every stroke the loop crosses is cut there (notes/lasso.py lasso_stroke, closed
ones too), the pieces inside picked, so Delete takes them away."""

from files.lang import tr
from notes.custom import stroke_points
from notes.lasso import inside_point, lasso_stroke, path_middle
from roll.roll_lasso import LassoDrawing
from window import look


class DrawerLasso(LassoDrawing):
    """Mixed into Drawer."""

    def lasso_canvas(self):
        return self.canvas

    def lasso_back(self, x, y):
        return self.from_xy(x, y)

    def lasso_pt(self, e):
        """The mouse on the board, free (freehand)."""
        return self.from_xy(e.x, e.y)

    def lasso_snap(self, pt, e, angle=True):
        """A clicked / dragged point: it sticks to points and lines, else the grid, like drawing (Shift = free)."""
        return self.event_pt(e)

    def lasso_mode_now(self):
        return self.lasso_kind.get()

    def lasso_magnet_on(self):
        return False

    def lasso_say(self, kind):
        self.pos_label.config(text=tr("drawer.lasso_" + kind))

    def lasso_do_cut(self, line):
        return self.lasso_cut(line)

    def lasso_synced(self):
        pass

    def lasso_loops(self, loop):
        """The loop and its Mirror copies."""
        from window.drawer import mirror_fns
        return [loop] + [[list(fn(*p)) for p in loop] for fn in mirror_fns(self.mirror_mode())]

    def draw_lasso(self):
        super().draw_lasso()
        if self.lasso:  # (its Mirror copies cut too: thin dashed, like the Slice line's)
            s = self.scale
            for lp in self.lasso_loops(self.lasso_line())[1:]:
                xy = self.screen_points(lp + ([lp[0]] if self.lasso["closed"] else []))
                if len(xy) >= 4:
                    self.canvas.create_line(*xy, fill=look.CUT, width=max(1, round(s)), dash=(6, 4), tags="lasso")

    def lasso_cut(self, loop):
        """The loop (and its Mirror copies) cuts the strokes it crosses: the picked ones if any, else every one the
        board can change (like Slice). The pieces inside it end up picked, with any stroke wholly inside. True."""
        loops = self.lasso_loops(loop)
        picked = self.movable()
        idx = set(picked or [i for i in range(len(self.strokes)) if self.pickable(i)])
        slack = 1.5 * self.scale / self.px()
        whole = {i for i in idx
                 if any(inside_point(lp, path_middle(stroke_points(self.strokes[i]))) for lp in loops)}
        self.cut_strokes(None, picked, "drawer.lasso_nothing",
                         cutter=lambda i, st: lasso_stroke(st, loops, slack) if i in idx else None, whole=whole)
        return True
