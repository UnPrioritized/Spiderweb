"""Editing a held shape on the piano roll, its press / drag / let go together (split out of pianoroll.on_press /
on_drag / on_release, which only hand the click on): a shape's points and handles (line ends, polyline points,
curve anchors, funnel walls, a custom shape's stroke points), the selected custom shape's box (corner / side =
resize, just outside = turn / skew), the kept Select box's sides (stretch what it selected)."""

import copy
import math

from files.lang import tr
from notes.custom import box_pts, even_on, frame_in_box
from notes.even_steps import whole_width
from notes.fx import copied as fx_copied, with_turn as fx_turned
from notes.gliss import pen_shape
from notes.sliced import steps_kept
from roll.roll_funnel import FLAT
from roll.roll_shared import CTRL, SHIFT, boxes_upright
from window.big_ask import BIG


class ShapeEdits:
    """Mixin for PianoRoll: points / handles, the custom shape's box, the kept Select box's sides."""

    # ---- points and handles -------------------------------------------------------------------------------------------

    def press_point(self, e, kept, tool):
        """A point / handle pressed (any tool: a picked curve stroke's handles can be near its custom shape's box),
        or the selected Glissando run's thickness handle / a note's right end. True when it was one."""
        app = self.app
        i = self.hit_handle(e.x, e.y, tool == "select")
        if i is not None:
            if kept:  # (editing the one shape: the Select box stays where it is, user)
                self.box_kept = (kept, set(app.sels))
            app.push_undo(name=tr("pianoroll.drag_a_point"))
            self.drag = ("handle", i)
            sh = app.selected()  # (a funnel: how many notes it could make now, for the let go's question)
            self.held_big = ({"before": copy.deepcopy(sh), "most": app.notes_most(sh), "count": None}
                             if sh and sh["kind"] == "funnel" else None)
            if isinstance(i, tuple) and i[0] == "ripple":  # (a Ripples source's centre point: picked too)
                app.pick_source(i[1])
            return True
        if self.gliss_press(e):  # (the selected Glissando run's thickness handle / a note's right end)
            if kept:
                self.box_kept = (kept, set(app.sels))
            return True
        return False

    def drag_handle(self, e):
        step = self.held_step([self.app.sel])
        if isinstance(self.drag[1], tuple) and self.app.selected()["kind"] == "custom":
            self.drag = ("handle", self.drag_stroke(self.app.selected(), self.drag[1], e))
        elif isinstance(self.drag[1], tuple):
            self.drag_funnel(self.app.selected(), self.drag[1], e)
        elif pen_shape(self.app.selected()):
            self.drag_curve(self.app.selected(), self.drag[1], e)
        else:
            sh, i = self.app.selected(), self.drag[1]
            funnel = sh["kind"] == "funnel"
            was, flat = ([list(p) for p in sh["pts"]], self.flattest(sh)) if funnel else (None, None)
            sh["pts"][i] = pt = self.event_pt(e)
            if funnel and i in (2, 3) and e.state & CTRL:  # the wall centred on the line
                mid = self.crossing(sh["pts"][:2], (sh["pts"][5 - i], pt))
                if mid:
                    sh["pts"][5 - i] = [2 * mid[0] - pt[0], 2 * mid[1] - pt[1]]
            if funnel and self.flattest(sh) < min(FLAT, flat):  # (no flatter than placing allows: it stops
                sh["pts"][:] = was                               # there; one already flatter may only open)
        if self.unmoved(step):
            return
        if self.stayed_inside(step):
            held = getattr(self, "held_big", None)
            if held and self.app.too_many_to_drag(self.app.selected(), held):
                self.app.lines_only()  # (past 1 M notes: made only once the let go's question says yes)
            else:
                self.app.shape_edited()

    def release_handle_first(self, e):
        """Before the notes catch up: a funnel past 1 M notes asks first (big_drag_asked)."""
        if getattr(self, "held_big", None):
            self.big_drag_asked()

    def big_drag_asked(self):
        """A funnel's handle / point let go: past 1 M notes (more than before the drag) it's asked first, like a typed
        point (App.point_ok; hunt 2026-11-14: one handle drag made 80 M+ notes); No = the funnel as it was."""
        held, self.held_big = self.held_big, None
        sh = self.app.selected()
        if sh is None or sh["kind"] != "funnel" or self.app.notes_most(sh) <= max(BIG, held["most"]):
            return
        if not self.app.point_ok(held["before"], sh):
            sh.clear()
            sh.update(copy.deepcopy(held["before"]))
            self.app.sync_points()
            self.app.notes_late = True  # (made again below, catch_up_notes)

    def release_handle(self, e, still):
        if self.app.selected()["kind"] == "custom":
            self.app.sync_custom()  # (a stroke's point dragged: its gaps may have closed)

    # ---- the selected custom shape's box: resize (corner / side), turn, skew (just outside) ------------------------

    def press_box_edit(self, e, kept, hit):
        app = self.app
        orig = copy.deepcopy(box_pts(app.selected()))  # (the box shown: a fitted one moves its frame along)
        self.inside_fit = frame_in_box(app.selected())
        if kept:  # (the Select box stays where it is, user: like changing a note's length in a box)
            self.box_kept = (kept, set(app.sels))
        app.push_undo(name=tr({"turn": "pianoroll.turn", "skew": "pianoroll.skew"}.get(hit[0], "pianoroll.resize")))
        if hit[0] == "turn":
            self.drag = ("turn", orig, self.screen_angle(orig, e.x, e.y), fx_copied(app.selected().get("fx")),
                         steps_kept(app.selected()))  # (a piece of a shape with pages: a turn step too)
        elif hit[0] == "skew":
            self.drag = ("skew", hit[1], orig, self.event_pt(e, snap=False))
        else:  # (a box shape: its top / bottom on the lines between keys, roll_custom.row_lines)
            orig, self.resize_rows = self.row_lines(app.selected(), orig)
            self.drag = ("resize", hit[1], orig, hit[0] == "side", self.event_pt(e, snap=False))

    def drag_resize(self, e):
        _, k, orig, side, start = self.drag
        pt = self.resize_point(orig, k, side, start, e)
        step = self.held_step([self.app.sel])
        later = self.grid_later  # (a step stopped at the edge: its snap for let go is dropped too)
        sh = self.app.selected()
        box = self.resize_custom(orig, k, pt, e.state & CTRL, side)
        rows_keep = bool(e.state & CTRL and not side and self.resize_rows and not e.state & SHIFT)
        if rows_keep:  # (Ctrl on a box on the lines between keys: whole keys tall, width following, hunt)
            box = self.keep_rows(orig, k, box)
        sh["pts"] = self.frame_for_box(box)
        even = even_on(sh)
        if even:  # (Even steps: the moved side whole gates from the other, even_steps.py)
            sh["pts"] = whole_width(sh["pts"], orig, sh, self.app.ppq)
        if not e.state & SHIFT and not even and not rows_keep:
            keep = e.state & CTRL
            self.notes_onto_grid(
                self.app.selected(), lambda b: self.frame_for_box(self.resize_custom(orig, k, [b, pt[1]], keep, side)),
                pt[0])
        else:  # (free: a snap saved for let go from an earlier step is off, user)
            self.grid_later = None
        if self.unmoved(step):
            return
        if self.stayed_inside(step):
            self.app.shape_edited()
        else:
            self.grid_later = later

    def release_resize_first(self, e):
        """Before the notes catch up: too slow to snap the notes while dragging = done now, so they're made once,
        at the snapped spot."""
        if self.grid_later and not e.state & SHIFT:
            if self.grid_soon(self.app.selected(), *self.grid_later):  # (the tries in the background: the notes
                self.grid_later = None                                    # are made once it's snapped)
            else:
                step = self.held_step([self.app.sel])
                self.notes_onto_grid(self.app.selected(), *self.grid_later, late=False)
                self.grid_later = None
                if self.stayed_inside(step):  # (the snap would pass an edge: it stays where it stopped)
                    self.app.shape_edited()

    def drag_skew(self, e):
        """Slides in whole grid steps / keys, wherever it was grabbed."""
        _, k, orig, start = self.drag
        step = self.held_step([self.app.sel])
        self.app.selected()["pts"] = self.frame_for_box(self.skew_custom(orig, k, *self.drag_steps(start, e)))
        if self.unmoved(step):
            return
        if self.stayed_inside(step):
            self.app.shape_edited()

    def drag_turn(self, e):
        _, orig, a0, fx0, steps = self.drag
        angle = self.screen_angle(orig, e.x, e.y) - a0
        angle = (angle + math.pi) % (2 * math.pi) - math.pi
        if not e.state & SHIFT:
            step = math.radians(15)
            angle = round(angle / step) * step
        sh = self.app.selected()
        if self.limit is None:
            self.limit = self.limits([sh])
        was = sh["pts"]
        sh["pts"] = self.frame_for_box(self.turn_custom(orig, angle))
        if fx0 or steps:  # (its note tool pages' result is turned too, fx.py)
            fx = fx_turned(fx_copied(fx0), math.degrees(angle), self.sy / self.sx, steps)
            if fx:
                sh["fx"] = fx
            else:
                sh.pop("fx", None)
        db, dp = self.push_in(self.reach([sh]), self.limit)  # (pushed back inside the piano roll, user)
        if db or dp:
            sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]
        if sh["pts"] != was:  # (snapped to the same angle: nothing to redo)
            self.app.shape_edited()
        self.app.show_position(tr("pianoroll.turned", degrees=math.degrees(angle)))

    def release_box_edit(self, e, still):
        """resize / turn / skew let go."""
        self.app.sync_custom()

    # ---- the kept Select box's sides / corners: what it selected stretches ------------------------------------------

    def press_stretch(self, kept, on_box):
        app = self.app
        self.box_kept = (kept, set(app.sels))  # (kept with the undo step: undo puts the box back)
        app.push_undo(name=tr("pianoroll.stretch"))
        boxes, around = boxes_upright(kept)
        self.box_kept = (boxes, set(app.sels))
        self.drag = ("stretch", on_box, around, {j: copy.deepcopy(app.shapes[j]) for j in app.sels}, boxes)

    def release_stretch(self, e, still):
        """The box goes back to its size, the shapes stay stretched (user)."""
        self.box_kept = (self.drag[4], set(self.app.sels))
        self.app.sync_panel()
