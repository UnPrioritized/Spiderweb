"""The Lasso tool on the piano roll (user 2026-10-22, planned-features.md "Lasso slice"; cutting: notes/lasso.py,
JoinSplit.lasso_cut). Like a pen tool's lasso: Lasso = drag, let go closes it straight back to the start (Alt held =
straight lines) and smooths it into curves; Polygonal = click points, close on the first point / double-click / Enter
(Backspace = last point off, Shift = 45°, Alt-drag = freehand). A closed loop WAITS: Enter cuts, Esc throws it away,
a click elsewhere does nothing; its points drag, middle-click on it adds one, right-click one takes its handles off,
then removes it. Curves like the Curve tool: Alt-drag a point = two handles pulled out of it, a handle dragged = the
other turns with it, Alt-drag a handle = a corner. Drawing / holding at or past the edge scrolls the view."""
import math

from files.lang import tr
from notes.bezier import seg_point
from notes.lasso import (curved_loop, magnet_rows, magnet_spot, magnetic_path, seg_controls, smooth_loop, split_piece,
                         tidy_loop)
from roll.roll_shared import ALT, CTRL, LONG_STROKE, SHIFT, shown_points
from window import look

CLOSE_PX = 8  # a Polygonal click this near the first point closes the loop
POINT_PX = 6  # a waiting loop's point / handle grabbed this near
FREE_PX = 3  # a freehand point every this many pixels
CURVE_PX = 2  # a curved piece drawn / cut as straight bits about this long on screen
PULL_PX = 2  # handles pulled out less than this = none
FASTEN_PX = 120  # a Magnetic loop's line longer than this behind the mouse is fastened by itself
MOST_WIDTH = 256  # Magnetic Width (pixels), like a pen tool's


class LassoDrawing:
    lasso_mouse = None  # (x, y) of the mouse last seen with the Magnetic lasso: its Width circle
    lasso = None  # {"pts": [[beat, key], ...], "mode": "free" / "poly", "closed", "hover": [beat, key] or None,
    #                "hd": each point's handles [in, out, corner] or None, once closed}

    def lasso_pt(self, e):
        """The mouse as (beat, key), free (no grid: the cut goes exactly along it), down to under key 0; past the
        piano roll's edge = on the edge."""
        x = min(max(e.x, self.kb_w), self.winfo_width())
        y = min(max(e.y, self.ruler_h), self.winfo_height())
        return [max(0.0, self.x2t(x)), min(max(self.y2p(y), -0.5), self.app.keys - 0.5)]

    # (what the drawer's lasso does its own way: window/drawer_lasso.py overrides these)

    def lasso_canvas(self):
        return self

    def lasso_back(self, x, y):
        """A screen spot as a loop point."""
        return [self.x2t(x), self.y2p(y)]

    def lasso_mode_now(self):
        return self.app.lasso_mode.get()

    def lasso_magnet_on(self):
        """The Magnetic lasso picked (its Width circle, [ ] keys)."""
        return self.app.tool.get() == "lasso" and self.app.lasso_mode.get() == "magnet"

    def lasso_say(self, kind):
        """The status line's hint: kind = draw / poly / magnet / ready."""
        self.app.status.show(tr("pianoroll.lasso_" + kind))

    def lasso_do_cut(self, line):
        """The loop cuts (JoinSplit.lasso_cut): None = it stays (a question answered No)."""
        return self.app.lasso_cut(line)

    def lasso_synced(self):
        self.app.sync_lasso()  # (the panel's Cut / Cancel)

    def lasso_hd(self):
        """The loop's handles, one per point (a loop set from outside without them gets none)."""
        las = self.lasso
        if len(las.get("hd") or ()) != len(las["pts"]):
            las["hd"] = [None] * len(las["pts"])
        return las["hd"]

    def lasso_line(self, las=None):
        """The loop as points along its line, curved pieces sampled (what's drawn and what cuts)."""
        las = las or self.lasso

        def steps(i, c):
            xy = [self.to_xy(p) for p in c]
            length = sum(math.dist(xy[k], xy[k + 1]) for k in range(3))
            keys = max(p[1] for p in c) - min(p[1] for p in c)
            return int(min(2000, max(8, length / (CURVE_PX * self.scale), 4 * keys)))
        if not las["closed"] or not any(las.get("hd") or ()):
            return [list(p) for p in las["pts"]]
        return curved_loop(las["pts"], las["hd"], steps)

    def lasso_snap(self, pt, e, angle=True):
        """A clicked / dragged point (not freehand): its time on the snap grid (user 2026-10-23, B), Ctrl = free,
        Shift = 45° from the last point (angle; the angle wins over the grid). Keys stay free (only where it crosses
        a row's middle counts)."""
        if angle and e.state & SHIFT:
            return self.lasso_angle(pt, e)
        sb = self.app.snap_beats()
        if e.state & CTRL or not sb:
            return pt
        return [max(0.0, round(pt[0] / sb) * sb), pt[1]]

    def lasso_angle(self, pt, e):
        """Shift held: pt moved onto the nearest 45° line from the last point, on screen."""
        if not e.state & SHIFT or not self.lasso or not self.lasso["pts"]:
            return pt
        x0, y0 = self.to_xy(self.lasso["pts"][-1])
        x, y = self.to_xy(pt)
        a = round(math.atan2(y - y0, x - x0) / (math.pi / 4)) * math.pi / 4
        d = math.hypot(x - x0, y - y0)
        x, y = x0 + d * math.cos(a), y0 + d * math.sin(a)
        return self.lasso_back(x, y)

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

    def lasso_handle_at(self, x, y):
        """The waiting loop's handle (point number, 0 = in / 1 = out) near the mouse, or None."""
        best = None
        for i, h in enumerate(self.lasso_hd()):
            for side in (0, 1):
                if h and h[side] != self.lasso["pts"][i]:
                    hx, hy = self.to_xy(h[side])
                    d = math.hypot(hx - x, hy - y)
                    if d <= POINT_PX * self.scale and (best is None or d < best[0]):
                        best = (d, (i, side))
        return None if best is None else best[1]

    def lasso_saved(self):
        """The loop's points and handles as they are (put back by Ctrl+Z while one is held)."""
        return ([list(p) for p in self.lasso["pts"]],
                [None if h is None else [list(h[0]), list(h[1]), h[2]] for h in self.lasso_hd()])

    def lasso_press(self, e, kept):
        """A press with the Lasso tool."""
        if kept:  # (the kept Select boxes stay: only what's in them is cut, like Slice)
            self.box_kept = (kept, set(self.app.sels))
        las, pt = self.lasso, self.lasso_pt(e)
        if las and las["closed"]:
            hit = self.lasso_handle_at(e.x, e.y)
            i = None if hit else self.lasso_point_at(e.x, e.y)
            if hit:  # (Alt: that handle alone from now on, a corner)
                self.drag = ("lasso_point", hit[0], self.lasso_saved(), ("handle", hit[1]), bool(e.state & ALT))
            elif i is not None:  # (Alt: new handles pulled out of it)
                self.drag = ("lasso_point", i, self.lasso_saved(), "pull" if e.state & ALT else "move", False)
            return  # (a click elsewhere: nothing, user)
        if las and las["mode"] == "magnet":
            if len(las["pts"]) >= 3 and math.dist(self.to_xy(las["pts"][0]), (e.x, e.y)) <= CLOSE_PX * self.scale:
                return self.magnet_close()
            self.lasso_follow(e)
            self.fasten(las["live"])  # (a click = a point by hand, where the line now goes)
            self.drag = ("lasso_magnet",)
        elif las and las["mode"] == "poly":
            if len(las["pts"]) >= 3 and math.dist(self.to_xy(las["pts"][0]), (e.x, e.y)) <= CLOSE_PX * self.scale:
                return self.lasso_close()
            las["pts"].append(self.lasso_snap(pt, e))
            self.drag = ("lasso_poly", e.x, e.y)
        else:
            mode = self.lasso_mode_now()
            mode = mode if mode in ("poly", "magnet") else "free"
            first = self.lasso_snap(pt, e, angle=False) if mode == "poly" else pt
            self.lasso = {"pts": [first], "mode": mode, "closed": False, "hover": None}
            self.drag = ("lasso_poly", e.x, e.y) if mode == "poly" else ("lasso_free",)
            if mode == "magnet":
                rows = magnet_rows(self.app.lasso_magnet_notes(), self.app.ppq)
                self.lasso.update(pts=[magnet_spot(rows, pt, self.sx, self.sy, self.magnet_width())], rows=rows,
                                  live=[], marks=[1])
                self.drag = ("lasso_magnet",)
            self.lasso_say({"poly": "poly", "magnet": "magnet"}.get(mode, "draw"))
        self.draw_lasso()

    def magnet_width(self):
        """The Magnetic lasso's Width on screen: how far from the mouse it looks for a note corner."""
        return self.app.lasso_px() * self.scale

    def lasso_follow(self, e):
        """A Magnetic loop following the mouse: the line from its last point to where the mouse points, clinging to
        the notes (notes/lasso.py); the part far behind the mouse is fastened by itself (like a pen tool's)."""
        las = self.lasso
        self.lasso_mouse = (e.x, e.y)
        spot = magnet_spot(las["rows"], self.lasso_pt(e), self.sx, self.sy, self.magnet_width())
        path = magnetic_path(las["rows"], las["pts"][-1], spot, self.sx, self.sy) or [las["pts"][-1], spot]
        xy = [self.to_xy(p) for p in path]
        run = [0.0]
        for k in range(1, len(xy)):
            run.append(run[-1] + math.dist(xy[k - 1], xy[k]))
        keep = run[-1] - FASTEN_PX * self.scale / 2
        if run[-1] > FASTEN_PX * self.scale:
            k = max(k for k in range(len(path) - 1) if run[k] <= keep)
            a, b = path[k], path[k + 1]
            if a[1] == b[1] and run[k] < keep:  # (part way along a stretch between rows: a point there)
                t = (keep - run[k]) / (run[k + 1] - run[k])
                p = [a[0] + t * (b[0] - a[0]), a[1]]
                self.fasten(path[1:k + 1] + [p])
                path = [p] + path[k + 1:]
            elif k:
                self.fasten(path[1:k + 1])
                path = path[k:]
        las["live"] = path[1:]

    def fasten(self, pts):
        """Points added to a Magnetic loop for good (Backspace takes them off together)."""
        las = self.lasso
        if pts:
            las["pts"] += [list(p) for p in pts]
            las["marks"].append(len(las["pts"]))
            las["live"] = []

    def magnet_close(self):
        """A Magnetic loop closed: the clinging line from its last point back to the first."""
        las = self.lasso
        path = magnetic_path(las["rows"], las["pts"][-1], las["pts"][0], self.sx, self.sy) or []
        las["pts"] += [list(p) for p in path[1:-1]]
        las["live"] = []
        self.lasso_close()

    def lasso_held(self, e):
        """A waiting loop's point or handle dragged."""
        _, i, _, what, alt = self.drag
        pts, hd = self.lasso["pts"], self.lasso_hd()
        pt = self.lasso_pt(e)
        if what == "move":  # (its handles go along)
            new = self.lasso_snap(pt, e, angle=False)
            d = [new[0] - pts[i][0], new[1] - pts[i][1]]
            pts[i] = new
            if hd[i]:
                hd[i][:2] = [[h[0] + d[0], h[1] + d[1]] for h in hd[i][:2]]
        elif what == "pull":  # (both sides alike, like the Curve tool)
            a = pts[i]
            hd[i] = [[2 * a[0] - pt[0], 2 * a[1] - pt[1]], pt, False]
        else:
            side, h = what[1], hd[i]
            h[side] = pt
            if alt or e.state & ALT:
                h[2] = True
            elif not h[2]:  # (a smooth point: the other handle turns with it, keeping its length on screen)
                (ax, ay), (hx, hy), (ox, oy) = self.to_xy(pts[i]), self.to_xy(pt), self.to_xy(h[1 - side])
                d, length = math.hypot(hx - ax, hy - ay), math.hypot(ox - ax, oy - ay)
                if d > 0 and length > 0:
                    h[1 - side] = self.lasso_back(ax - (hx - ax) / d * length, ay - (hy - ay) / d * length)

    def lasso_drag(self, e):
        las, kind = self.lasso, self.drag[0]
        pt = self.lasso_pt(e)
        if kind == "lasso_point":
            self.lasso_held(e)
        elif kind == "lasso_magnet":  # (held down: the same as moving)
            self.lasso_follow(e)
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
            las["hover"] = self.lasso_snap(pt, e)
        self.draw_lasso()

    def lasso_release(self, e):
        drag, self.drag = self.drag, None
        if drag[0] == "lasso_free":
            if self.lasso["hover"]:
                self.lasso["pts"].append(self.lasso["hover"])
            self.lasso_close()
        elif drag[0] == "lasso_point":
            i = drag[1]
            h = self.lasso_hd()[i]
            if drag[3] == "pull" and h and math.dist(self.to_xy(h[1]),
                                                     self.to_xy(self.lasso["pts"][i])) < PULL_PX * self.scale:
                self.lasso_hd()[i] = None  # (pulled out barely: no handles)
            if self.lasso_saved() != drag[2]:
                self.lasso_edited(drag[2])
        self.draw_lasso()

    def lasso_edited(self, saved):
        """A waiting loop changed (saved = how it was before): one step of its own undo (user 2026-10-23: Ctrl+Z
        took the whole loop away, handles too)."""
        self.lasso.setdefault("past", []).append(saved)
        self.lasso["future"] = []

    def lasso_redo(self):
        """Ctrl+Y while there's a loop: a waiting loop's edit taken back by Ctrl+Z comes back. True when one did."""
        las = self.lasso
        if not las or not las["closed"] or self.drag or not las.get("future"):
            return False
        las.setdefault("past", []).append(self.lasso_saved())
        las["pts"], las["hd"] = las["future"].pop()
        self.draw_lasso()
        return True

    def lasso_motion(self, e):
        """The mouse moving with no button: a Polygonal loop's next line follows it, a Magnetic one's clinging line.
        True when it's the lasso's."""
        las = self.lasso
        if self.lasso_magnet_on():
            self.lasso_mouse = (e.x, e.y)  # (the Width circle follows the mouse)
            if not las:
                self.draw_lasso()
        if not las or las["closed"] or las["mode"] == "free":
            return False
        if las["mode"] == "magnet":
            self.lasso_follow(e)
        else:
            las["hover"] = self.lasso_snap(self.lasso_pt(e), e)
        self.draw_lasso()
        return True

    def lasso_close(self):
        """The loop closed straight back to its start: it waits for Enter / Esc. Too small = gone. A freehand Lasso
        loop is smoothed into curves (like Straighten; user 2026-10-22)."""
        las = self.lasso
        pts = tidy_loop(las["pts"])
        if pts is None:
            self.lasso = None
        else:
            hd = [None] * len(pts)
            if las["mode"] == "free" and len(pts) > 3:
                xy, hd = smooth_loop([self.to_xy(p) for p in pts])
                back = lambda p: self.lasso_back(*p)
                pts = [back(p) for p in xy]
                hd = [[back(h[0]), back(h[1]), h[2]] for h in hd]
            las.update(pts=pts, hd=hd, closed=True, hover=None)
            self.lasso_say("ready")
        self.drag = None
        self.draw_lasso()

    def lasso_key(self, e):
        """Enter / Esc / Backspace while there's a loop. True when the key was the lasso's."""
        las = self.lasso
        k = e.keysym.lower()
        if k in ("bracketleft", "bracketright") and self.lasso_magnet_on():  # (Magnetic Width smaller / bigger,
            #                                                                         like a pen tool's)
            self.app.lasso_width.set(str(min(MOST_WIDTH, max(1, self.app.lasso_px() + (1 if k == "bracketright"
                                                                                       else -1)))))
            self.draw_lasso()
            return True
        if not las:
            return False
        if k == "escape":
            self.drop_lasso()
        elif k in ("return", "kp_enter"):
            self.lasso_enter()
        elif k == "backspace" and not las["closed"] and las["mode"] != "free" and not self.drag:
            if las["mode"] == "magnet":  # (back to the point fastened before)
                las["marks"].pop()
                del las["pts"][las["marks"][-1] if las["marks"] else 0:]
                las["live"] = []
            else:
                las["pts"].pop()
            if not las["pts"]:
                self.lasso = None
            self.draw_lasso()
        else:
            return False
        return True

    def lasso_enter(self):
        """Enter (or the panel's Cut): a waiting loop cuts, an open Polygonal / Magnetic one closes."""
        las = self.lasso
        if not las or self.drag:  # (the loop stays shown while it asks about plain notes, user; No = it stays)
            return
        if las["closed"]:
            if self.lasso_do_cut(self.lasso_line()) is not None:
                self.drop_lasso()
        elif las["mode"] == "magnet":
            self.magnet_close()
        elif las["mode"] == "poly":
            self.lasso_close()

    def lasso_double(self):
        """A double-click closes a Polygonal / Magnetic loop (its first click added a point). True when it did."""
        las = self.lasso
        if not las or las["closed"] or las["mode"] == "free":
            return False
        self.drag = None
        self.magnet_close() if las["mode"] == "magnet" else self.lasso_close()
        return True

    def lasso_undo(self, whole=False):
        """Ctrl+Z / Undo / a History click while there's a loop: it acts on the loop, nothing else is undone (like a
        shape half drawn; hunt 2026-10-23, user saw an earlier change undone and half a loop left). A waiting loop's
        point / handle held = back where it was pressed; a waiting loop's last edit (points, handles) taken back
        one at a time (user 2026-10-23), with none left (or whole: a History click) = gone; a loop being drawn /
        open = gone. True when it did."""
        las = self.lasso
        if not las:
            return False
        if self.drag and self.drag[0] == "lasso_point":
            las["pts"], las["hd"] = self.drag[2]
            self.drag = None
            self.draw_lasso()
        elif not whole and las["closed"] and las.get("past"):
            las.setdefault("future", []).append(self.lasso_saved())
            las["pts"], las["hd"] = las["past"].pop()
            self.draw_lasso()
        else:
            self.drop_lasso()
        return True

    def lasso_holding(self):
        """The left button holds the lasso (drawing it or one of its points)."""
        return bool(self.drag) and self.drag[0].startswith("lasso")

    def lasso_right(self, e):
        """Right-click: an open Polygonal loop closes (like a polyline; too few points = gone); on a waiting loop's
        point its handles go first, then the point (3 points stay at least); on a handle its point's handles go.
        True when it was the lasso's (no menu)."""
        las = self.lasso
        if las and not las["closed"] and not self.drag:
            if len(las["pts"]) >= 3:
                self.magnet_close() if las["mode"] == "magnet" else self.lasso_close()
            else:
                self.drop_lasso()
            return True
        if not las or not las["closed"] or self.drag:
            return False
        hd, hit = self.lasso_hd(), self.lasso_handle_at(e.x, e.y)
        i = hit[0] if hit else self.lasso_point_at(e.x, e.y)
        if i is None:
            return False
        if hd[i] or len(las["pts"]) > 3:
            self.lasso_edited(self.lasso_saved())
        if hd[i]:
            hd[i] = None
        elif len(las["pts"]) > 3:
            del las["pts"][i]
            del hd[i]
        self.draw_lasso()
        return True

    def lasso_middle(self, e):
        """A middle click (no drag) on a waiting loop's line adds a point there (on a curve: the curve stays as it
        is). True when it was on the line."""
        las = self.lasso
        if not las or not las["closed"]:
            return False
        pts, hd = las["pts"], self.lasso_hd()
        best = None
        for i in range(len(pts)):
            c = seg_controls(pts, hd, i)
            n = 64 if c else 1
            line = ([self.to_xy(seg_point(*c, k / n)) for k in range(n + 1)] if c else
                    [self.to_xy(pts[i]), self.to_xy(pts[(i + 1) % len(pts)])])
            for k in range(n):
                (ax, ay), (bx, by) = line[k], line[k + 1]
                L = (bx - ax) ** 2 + (by - ay) ** 2
                u = 0.0 if L == 0 else min(1.0, max(0.0, ((e.x - ax) * (bx - ax) + (e.y - ay) * (by - ay)) / L))
                d = math.hypot(ax + u * (bx - ax) - e.x, ay + u * (by - ay) - e.y)
                if best is None or d < best[0]:
                    best = (d, i, (k + u) / n, bool(c))
        if best[0] > POINT_PX * self.scale:
            return False
        _, i, t, curved = best
        self.lasso_edited(self.lasso_saved())
        if curved:
            split_piece(pts, hd, i, min(max(t, 1e-6), 1 - 1e-6))
        else:
            pts.insert(i + 1, self.lasso_snap(self.lasso_pt(e), e, angle=False))
            hd.insert(i + 1, None)
        self.draw_lasso()
        return True

    def drop_lasso(self, unfinished=False):
        """The loop gone (unfinished: only one not closed yet; a waiting one stays, a point held going back)."""
        if unfinished and self.lasso and self.lasso["closed"]:
            if self.drag and self.drag[0] == "lasso_point":
                self.lasso_undo()
            return
        if self.lasso is not None:
            self.lasso = None
            if self.drag and self.drag[0].startswith("lasso"):
                self.drag = None
            self.draw_lasso()

    def draw_lasso(self):
        """The loop: dashed in the cut colour; a waiting one with its points and handles."""
        c = self.lasso_canvas()
        c.delete("lasso")
        self.lasso_synced()
        las = self.lasso
        s = self.scale
        if self.lasso_mouse and self.lasso_magnet_on() and not (las and las["closed"]):  # (Magnetic: its Width
            #                                                                                 as a circle round the mouse)
            (x, y), w = self.lasso_mouse, self.magnet_width()
            c.create_oval(x - w, y - w, x + w, y + w, outline=look.CUT, width=max(1, round(s)), tags="lasso")
        if not las:
            return
        pts = self.lasso_line() + ([las["hover"]] if las["hover"] else []) + las.get("live", [])
        xy = [v for p in pts for v in self.to_xy(p)]
        if las["closed"]:
            xy += xy[:2]
        if len(xy) >= 4:  # (on a white edge, like the handles: it shows on notes and in the dark look; user 10-23)
            c.create_line(*xy, fill=look.HANDLE_FILL, width=max(3, round(4 * s)), tags="lasso")
            c.create_line(*xy, fill=look.CUT, width=max(1, round(2 * s)), dash=(6, 3), tags="lasso")
        r = 3 * s
        hd = self.lasso_hd() if las["closed"] else []
        lines = [(self.to_xy(las["pts"][i]), self.to_xy(h[side])) for i, h in enumerate(hd) if h
                 for side in (0, 1) if h[side] != las["pts"][i]]
        # (handles like the Curve tool's / funnel's: blue lines on a white edge, solid dots, round points)
        for width, edge in ((max(3, round(3.5 * s)), True), (max(1, round(1.5 * s)), False)):
            for (ax, ay), (hx, hy) in lines:
                c.create_line(ax, ay, hx, hy, fill=look.HANDLE_FILL if edge else look.HANDLE, width=width,
                                 tags="lasso")
        q = 4.5 * s
        for _, (hx, hy) in lines:
            c.create_oval(hx - q, hy - q, hx + q, hy + q, fill=look.HANDLE, outline=look.HANDLE_FILL,
                             width=max(1, round(s)), tags="lasso")
        shown = (shown_points(len(las["pts"]), len(las["pts"]) <= LONG_STROKE) if las["closed"]
                 else range(len(las["pts"])) if las["mode"] == "poly"
                 else [0] + [m - 1 for m in las["marks"][1:]] if las["mode"] == "magnet" else (0,))
        for i in shown:
            x, y = self.to_xy(las["pts"][i])
            if hd and hd[i]:  # (a point with handles: round, like a curve's anchor)
                q = 5.5 * s
                c.create_oval(x - q, y - q, x + q, y + q, fill=look.HANDLE_FILL, outline=look.CUT,
                                 width=max(2, round(2 * s)), tags="lasso")
                continue
            c.create_rectangle(x - r, y - r, x + r, y + r, fill=look.CUT if las["closed"] else "",
                                  outline=look.CUT, tags="lasso")
