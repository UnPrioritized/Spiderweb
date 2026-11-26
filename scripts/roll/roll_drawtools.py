"""The drawing tools' mouse work on the piano roll, each tool's press / drag / let go in its own part (split out of
pianoroll.on_press / on_drag / on_release, which only hand the click on): Line and the other two-point tools,
the box tools (Circle, Polygon...), Polyline, Arc, Funnel (line, then wall), Custom shape (placing its box),
Freehand. Every tool takes a drag OR click-click (a click: the shape follows the mouse until the next click,
pianoroll.follow)."""

import math
from types import SimpleNamespace

from files.lang import tr, trn
from notes.engine import make_shape
from notes.custom import box_frame
from notes.pen import ink_at
from roll.roll_live import BOX_TOOLS
from roll.roll_shared import CTRL, SHIFT, mouse_trail
from window import look


class DrawTools:
    """Mixin for PianoRoll: the drawing tools' press / drag / let go."""

    # ---- Line, Curve, Glissando... (two points) and the box tools: press, drag, let go -------------------------------

    def press_line(self, e, tool, pt):
        self.draft = make_shape(tool, [pt, pt], self.app.new_defaults(tool))
        self.pen_draft()
        self.drag = ("create", pt, e.x, e.y)
        self._draft_time = 0.0
        self.request_redraw()

    def press_box_tool(self, e, tool, pt):
        self.draft = self.box_draft(tool, pt, pt)
        self.drag = ("create", pt, e.x, e.y)
        self.request_redraw()

    def drag_create(self, e):
        kind, pt = self.draft.get("draw") or self.draft["kind"], self.draw_pt(e)
        if kind in BOX_TOOLS:
            start = self.drag[1]
            flat = False
            if not self.app.live.get():  # (its own shape: keys on the lines between keys; live shapes' strokes
                start, pt = self.row_box(start, pt, e.state & SHIFT)  # stay lines, user)
                flat = start[1] == pt[1] and float(pt[1]).is_integer()  # (one key: nothing, Ctrl or not)
            if e.state & CTRL and not flat:  # a perfect circle / polygon on screen
                pt = self.keep_aspect(start, pt, self.box_aspect(kind))
            self.draft = self.box_draft(kind, start, pt)
        else:
            self.draft = make_shape(kind, [self.drag[1], pt], self.app.new_defaults(kind))
            self.pen_draft()
        self.draft_moved()

    def release_create(self, e, still):
        funnel = self.draft["kind"] == "funnel"
        if still:  # clicked twice in the same spot: nothing
            self.cancel_draft()
        elif funnel and self.add_funnel_line():
            pass
        elif funnel:  # the line: the wall comes next (the panel says so)
            self.app.sync_funnel()
        else:
            self.commit_draft()

    # ---- Funnel: its line is drawn like Line's (create), then its wall ---------------------------------------------

    def press_wall(self, e, pt):
        # the funnel's line is drawn, now its wall
        self.draft["pts"] += [list(pt), list(pt)]
        self.drag = ("wall", e.x, e.y)
        self.request_redraw()

    def drag_wall(self, e):
        pt = self.event_pt(e)
        self.draft["pts"][3] = pt
        if e.state & CTRL:  # centred on the line: both ends equally far from it
            mid = self.crossing(self.draft["pts"][:2], (self.draft["pts"][2], pt))
            if mid:
                self.draft["pts"][2] = [2 * mid[0] - pt[0], 2 * mid[1] - pt[1]]
        self.request_redraw()
        text = self.position_text(e) or ""
        n = self.app.note_count(self.draft)
        self.app.show_position(tr("pianoroll.new_funnel_notes", text=text, notes=trn("n.notes", n, n=n)))

    def release_wall(self, e, still):
        if still:
            del self.draft["pts"][2:]  # clicked twice in the same spot: still waiting for the wall
            self.request_redraw()
        elif not self.arrange_funnel(self.draft["pts"]):
            del self.draft["pts"][2:]  # the wall can't run along the line: still waiting for a wall
            self.request_redraw()
            self.app.sync_funnel(note=tr("pianoroll.the_wall_has_to_cross_the"))
        elif self.app.confirm_big([dict(self.draft, pts=self.arrange_funnel(self.draft["pts"]))]):
            self.draft["pts"] = self.arrange_funnel(self.draft["pts"])
            self.commit_draft()
        else:
            self.cancel_draft()

    # ---- Polyline: click its points, or drag each segment ------------------------------------------------------------

    def press_poly(self, e, pt):
        if self.draft is None:
            self.draft = make_shape("poly", [pt, pt], self.app.defaults)
            self.pen_draft()  # (the shared pen: roll_pen)
        elif self.poly_point(pt):
            return
        self.drag = ("segment", e.x, e.y)
        self.request_redraw()

    def drag_segment(self, e):
        """A polyline's next point / an arc's end (arcdrag), at the mouse."""
        self.draft["pts"][-1] = self.draw_pt(e)
        self.request_redraw()

    def release_segment(self, e, still):
        if not still:  # dragged: the point goes where it was let go
            self.poly_point(self.draw_pt(e))

    # ---- Arc: three clicks: start, a point it passes through, end (or drag start -> end, then bend) ------------------

    def press_arc(self, e, pt):
        if self.draft is None:
            self.draft = make_shape("arc", [pt, pt], self.app.defaults)
            self.draft["k"] = self.sy / self.sx  # round as it looks on screen now
            self.pen_draft()
            self.drag = ("arcdrag", e.x, e.y)
        elif self.arc_bend:  # dragged start -> end: this click sets the point it passes through
            self.draft["pts"][1] = pt
            self.arc_bend = False
            self.finish_arc()
        elif len(self.draft["pts"]) == 2:
            self.draft["pts"][-1] = pt
            self.draft["pts"].append(list(pt))
        else:
            self.draft["pts"][-1] = pt
            self.finish_arc()
        self.request_redraw()

    def release_arcdrag(self, e, still):
        if not still:  # dragged start -> end: now it bends with the mouse until a click
            (b0, p0), end = self.draft["pts"]
            self.draft["pts"] = [[b0, p0], [(b0 + end[0]) / 2, (p0 + end[1]) / 2], end]
            self.arc_bend = True
            self.request_redraw()

    # ---- Custom shape: its box placed ---------------------------------------------------------------------------------

    def press_custom(self, e, pt):
        app = self.app
        tpl = app.custom_template(app.custom_shape)
        if not tpl:
            app.status.config(text=tr("pianoroll.pick_a_custom_shape_in_the"))
            return
        self.draft = app.new_custom(tpl[0], *pt, *pt, areas=tpl[2], drawn=tpl[1])
        self.drag = ("place", pt, e.x, e.y, tpl[1])
        self._draft_time = 0.0  # (slow or not: found again for each new shape)
        self.request_redraw()

    def drag_place(self, e):
        _, start, _, _, aspect = self.drag
        start, pt = self.row_box(start, self.event_pt(e), e.state & SHIFT)  # (keys on the lines between keys)
        flat = start[1] == pt[1] and float(pt[1]).is_integer()  # (one key: nothing, Ctrl or not)
        if e.state & CTRL and aspect and not flat:
            pt = self.keep_aspect(start, pt, aspect)
        self.draft["pts"] = box_frame(start[0], start[1], pt[0], pt[1])
        self.draft_moved()
        self.show_draft(self.position_text(e) or "")

    def release_place(self, e, still):
        pts = self.draft["pts"]
        if still or pts[0][1] == pts[2][1]:  # (one key tall: nothing is made, user 2026-11-20)
            self.cancel_draft()
        elif self.keep_draft() or self.app.confirm_big([self.draft]):
            self.commit_draft()
        else:
            self.cancel_draft()

    # ---- Freehand -----------------------------------------------------------------------------------------------------

    def press_free(self, e):
        app = self.app
        self.draft = make_shape("free", [self.draw_pt(e, snap=False)], app.defaults)
        trail = mouse_trail(e.x_root, e.y_root, None)
        self.drag = ("free", e.x, e.y, trail[-1][2] if trail else None)
        # (each point's width from the hand's speed, worked out as it's drawn and never again: the pen's Width
        # from speed, pen.ink_at; where + when the mouse was, Windows' mouse trail when it has one)
        self._ink_way = ([(e.x / self.scale, e.y / self.scale)], [trail[-1][2] if trail else e.time])
        self.draft["ink"] = [1.0]
        if app.free_thick > 1:  # (the wide pen already while drawing: its notes show as they'll be, user)
            self.draft["k"] = self.sy / self.sx
            self.pen_draft()

    def free_drag(self, e):
        """Freehand: every place the mouse passed since the last move (also the ones Windows skipped while the
        program was busy), at least 3 px apart. Only the new bit of line is drawn now; the whole roll (the notes)
        follows a few times a second, so painting doesn't hold up reading the mouse."""
        _, lx, ly, since = self.drag
        trail = mouse_trail(e.x_root, e.y_root, since)
        if trail:
            since = trail[-1][2]
            ox, oy = self.winfo_rootx(), self.winfo_rooty()
            spots = [(x - ox, y - oy, t) for x, y, t in trail]
        else:
            spots = [(e.x, e.y, e.time)]
        pts = self.draft["pts"]
        ink = self.draft.get("ink")
        way = getattr(self, "_ink_way", None)
        for x, y, t in spots:
            if math.hypot(x - lx, y - ly) >= 3:
                pt = self.event_pt(SimpleNamespace(x=x, y=y, state=e.state), snap=False)
                self.create_line(self.t2x(pts[-1][0]), self.p2y(pts[-1][1]), self.t2x(pt[0]), self.p2y(pt[1]),
                                 fill=look.DRAFT_LINE, width=look.LINES + 1)  # (as the draft's redraw)
                pts.append(pt)
                if ink is not None and way is not None:
                    way[0].append((x / self.scale, y / self.scale))
                    way[1].append(t)
                    ink.append(ink_at(*way))
                lx, ly = x, y
        self.drag = ("free", lx, ly, since)
        self.request_redraw(delay=100)

    def release_free(self, e, still):
        pts = self.draft["pts"]
        if self.live_drawing() and len(pts) >= 3 and math.hypot(
                e.x - self.t2x(pts[0][0]), e.y - self.p2y(pts[0][1])) < 12 * self.scale:
            pts.append(list(pts[0]))  # let go near its start: closed
        if len(pts) >= 2:
            # made perfect with the last sensitivity picked (smooth.py), as the roll looks now
            self.draft.update(smooth=self.app.free_smooth, k=self.sy / self.sx)
            if self.app.free_thick > 1:  # (a wide pen: pen.py; into a live shape too, user 2026-11-19; roll_live
                # draft_stroke carries it)
                self.draft["thick"] = self.app.free_thick
            if self.app.free_pen:  # (the pen's shape picked last)
                self.draft["pen"] = dict(self.app.free_pen)
            ink = self.draft.pop("ink", None)
            self._ink_way = None
            if ink and len(ink) == len(pts) - 1:  # (a loop closed at the let go: its end as its last point)
                ink.append(ink[-1])
            if ink and len(ink) == len(pts):
                self.draft["ink"] = ink
            self.commit_draft()
        else:
            self.cancel_draft()
