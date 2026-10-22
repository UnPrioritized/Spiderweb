"""The Lasso tool on the piano roll (user 2026-10-22, planned-features.md "Lasso slice"; cutting: notes/lasso.py,
JoinSplit.lasso_cut). Like a pen tool's lasso: Lasso = drag, let go closes it straight back to the start (Alt held =
straight lines); Polygonal = click points, close on the first point / double-click / Enter (Backspace = last point
off, Shift = 45°, Alt-drag = freehand). A closed loop WAITS: Enter cuts, Esc throws it away, a click elsewhere does
nothing; its points drag, middle-click on it adds one, right-click one removes it."""
import math

from files.lang import tr
from notes.lasso import tidy_loop
from roll.roll_shared import ALT, LONG_STROKE, SHIFT, shown_points
from window import look

CLOSE_PX = 8  # a Polygonal click this near the first point closes the loop
POINT_PX = 6  # a waiting loop's point grabbed this near
FREE_PX = 3  # a freehand point every this many pixels


class LassoDrawing:
    lasso = None  # {"pts": [[beat, key], ...], "mode": "free" / "poly", "closed", "hover": [beat, key] or None}

    def lasso_pt(self, e):
        """The mouse as (beat, key), free (no grid: the cut goes exactly along it), down to under key 0."""
        y = min(max(e.y, self.ruler_h), self.winfo_height())
        return [max(0.0, self.x2t(e.x)), min(max(self.y2p(y), -0.5), self.app.keys - 0.5)]

    def lasso_angle(self, pt, e):
        """Shift held: pt moved onto the nearest 45° line from the last point, on screen."""
        if not e.state & SHIFT or not self.lasso or not self.lasso["pts"]:
            return pt
        x0, y0 = self.to_xy(self.lasso["pts"][-1])
        x, y = self.to_xy(pt)
        a = round(math.atan2(y - y0, x - x0) / (math.pi / 4)) * math.pi / 4
        d = math.hypot(x - x0, y - y0)
        x, y = x0 + d * math.cos(a), y0 + d * math.sin(a)
        return [max(0.0, self.x2t(x)), self.y2p(y)]

    def lasso_point_at(self, x, y):
        """The waiting loop's point (number) near the mouse, or None."""
        pts = self.lasso["pts"]
        best = None
        for i in shown_points(len(pts), len(pts) <= LONG_STROKE):
            px, py = self.to_xy(pts[i])
            d = math.hypot(px - x, py - y)
            if d <= POINT_PX * self.scale and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def lasso_press(self, e, kept):
        """A press with the Lasso tool."""
        if kept:  # (the kept Select boxes stay: only what's in them is cut, like Slice)
            self.box_kept = (kept, set(self.app.sels))
        las, pt = self.lasso, self.lasso_pt(e)
        if las and las["closed"]:
            i = self.lasso_point_at(e.x, e.y)
            if i is not None:
                self.drag = ("lasso_point", i)
            return  # (a click elsewhere: nothing, user)
        if las and las["mode"] == "poly":
            if len(las["pts"]) >= 3 and math.dist(self.to_xy(las["pts"][0]), (e.x, e.y)) <= CLOSE_PX * self.scale:
                return self.lasso_close()
            las["pts"].append(self.lasso_angle(pt, e))
            self.drag = ("lasso_poly", e.x, e.y)
        else:
            mode = "poly" if self.app.lasso_mode.get() == "poly" else "free"
            self.lasso = {"pts": [pt], "mode": mode, "closed": False, "hover": None}
            self.drag = ("lasso_poly", e.x, e.y) if mode == "poly" else ("lasso_free",)
            self.app.status.show(tr("pianoroll.lasso_" + ("poly" if mode == "poly" else "draw")))
        self.draw_lasso()

    def lasso_drag(self, e):
        las, kind = self.lasso, self.drag[0]
        pt = self.lasso_pt(e)
        if kind == "lasso_point":
            las["pts"][self.drag[1]] = pt
        elif kind == "lasso_free" and e.state & ALT:  # (Alt: a straight line to the mouse)
            las["hover"] = pt
        elif kind == "lasso_free" or kind == "lasso_poly" and e.state & ALT:  # (Polygonal: Alt-drag = freehand)
            if las["hover"] and kind == "lasso_free":  # (Alt let go: the straight line's end stays)
                las["pts"].append(las["hover"])
            las["hover"] = None
            x, y = self.to_xy(las["pts"][-1])
            if math.hypot(e.x - x, e.y - y) >= FREE_PX * self.scale:
                las["pts"].append(pt)
        else:
            las["hover"] = self.lasso_angle(pt, e)
        self.draw_lasso()

    def lasso_release(self, e):
        kind, self.drag = self.drag[0], None
        if kind == "lasso_free":
            if self.lasso["hover"]:
                self.lasso["pts"].append(self.lasso["hover"])
            self.lasso_close()
        self.draw_lasso()

    def lasso_motion(self, e):
        """The mouse moving with no button: a Polygonal loop's next line follows it. True when it's the lasso's."""
        las = self.lasso
        if not las or las["closed"] or las["mode"] != "poly":
            return False
        las["hover"] = self.lasso_angle(self.lasso_pt(e), e)
        self.draw_lasso()
        return True

    def lasso_close(self):
        """The loop closed straight back to its start: it waits for Enter / Esc. Too small = gone."""
        las = self.lasso
        pts = tidy_loop(las["pts"])
        if pts is None:
            self.lasso = None
        else:
            las.update(pts=pts, closed=True, hover=None)
            self.app.status.show(tr("pianoroll.lasso_ready"))
        self.drag = None
        self.draw_lasso()

    def lasso_key(self, e):
        """Enter / Esc / Backspace while there's a loop. True when the key was the lasso's."""
        las = self.lasso
        if not las:
            return False
        k = e.keysym.lower()
        if k == "escape":
            self.drop_lasso()
        elif k in ("return", "kp_enter"):
            if las["closed"]:
                if not self.drag:
                    loop = las["pts"]
                    self.drop_lasso()
                    self.app.lasso_cut(loop)
            elif las["mode"] == "poly":
                self.lasso_close()
        elif k == "backspace" and not las["closed"] and las["mode"] == "poly" and not self.drag:
            las["pts"].pop()
            if not las["pts"]:
                self.lasso = None
            self.draw_lasso()
        else:
            return False
        return True

    def lasso_double(self):
        """A double-click closes a Polygonal loop (its first click added a point). True when it did."""
        las = self.lasso
        if not las or las["closed"] or las["mode"] != "poly":
            return False
        self.drag = None
        self.lasso_close()
        return True

    def lasso_right(self, e):
        """Right-click on a waiting loop's point removes it (3 points stay at least). True when it was on one."""
        las = self.lasso
        if not las or not las["closed"] or self.drag:
            return False
        i = self.lasso_point_at(e.x, e.y)
        if i is None:
            return False
        if len(las["pts"]) > 3:
            del las["pts"][i]
            self.draw_lasso()
        return True

    def lasso_middle(self, e):
        """A middle click (no drag) on a waiting loop's line adds a point there. True when it was on the line."""
        las = self.lasso
        if not las or not las["closed"]:
            return False
        pts = las["pts"]
        best = None
        for i in range(len(pts)):
            (ax, ay), (bx, by) = self.to_xy(pts[i]), self.to_xy(pts[(i + 1) % len(pts)])
            L = (bx - ax) ** 2 + (by - ay) ** 2
            u = 0.0 if L == 0 else min(1.0, max(0.0, ((e.x - ax) * (bx - ax) + (e.y - ay) * (by - ay)) / L))
            d = math.hypot(ax + u * (bx - ax) - e.x, ay + u * (by - ay) - e.y)
            if best is None or d < best[0]:
                best = (d, i)
        if best[0] > POINT_PX * self.scale:
            return False
        pts.insert(best[1] + 1, self.lasso_pt(e))
        self.draw_lasso()
        return True

    def drop_lasso(self):
        if self.lasso is not None:
            self.lasso = None
            if self.drag and self.drag[0].startswith("lasso"):
                self.drag = None
            self.draw_lasso()

    def draw_lasso(self):
        """The loop: dashed in the cut colour; a waiting one with its points."""
        self.delete("lasso")
        las = self.lasso
        if not las:
            return
        pts = las["pts"] + ([las["hover"]] if las["hover"] else [])
        xy = [v for p in pts for v in self.to_xy(p)]
        if las["closed"]:
            xy += xy[:2]
        s = self.scale
        if len(xy) >= 4:
            self.create_line(*xy, fill=look.CUT, width=max(1, round(2 * s)), dash=(6, 3), tags="lasso")
        r = 3 * s
        shown = (shown_points(len(las["pts"]), len(las["pts"]) <= LONG_STROKE) if las["closed"]
                 else range(len(las["pts"])) if las["mode"] == "poly" else (0,))
        for i in shown:
            x, y = self.to_xy(las["pts"][i])
            self.create_rectangle(x - r, y - r, x + r, y + r, fill=look.CUT if las["closed"] else "",
                                  outline=look.CUT, tags="lasso")
