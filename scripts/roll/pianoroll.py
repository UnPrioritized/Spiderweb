"""The piano roll canvas: view (scroll/zoom), hit testing and mouse editing.
Its other parts: roll_draw.py (painting), roll_custom.py (custom shape box), roll_curve.py (curve editing),
roll_funnel.py (funnel editing), roll_live.py (live drawing, custom shape strokes), roll_menu.py (right-click menu), roll_text.py (the Text tool),
roll_hz.py (the Hz bass tool),
roll_shared.py (colours, keys, caches)."""

import copy
import math
import tkinter as tk
from types import SimpleNamespace

import numpy as np

from files.lang import tr
from notes.custom import box_frame, fill_plan
from notes.engine import make_shape
from notes.joined import all_tumours
from notes.funnel import funnel_contains, funnel_handles, funnel_origins
from roll.roll_curve import CurveEditing
from roll.roll_custom import CustomBox
from roll.roll_draw import RollDrawing
from roll.roll_funnel import FunnelEditing
from roll.roll_hz import HzStart
from roll.roll_live import BOX_TOOLS, LiveDrawing
from roll.roll_menu import ShapeMenu
from roll.roll_shared import ALT, CTRL, PICK, SHIFT, cached_path, cached_strokes, mouse_trail, note_name
from roll.roll_text import TextTyping


class PianoRoll(RollDrawing, CustomBox, CurveEditing, FunnelEditing, LiveDrawing, ShapeMenu, TextTyping, HzStart,
                tk.Canvas):
    def __init__(self, parent, app, scale):
        super().__init__(parent, bg="#ffffff", highlightthickness=0, cursor="crosshair")
        self.app = app
        self.scale = scale
        self.kb_w, self.ruler_h = 56 * scale, 20 * scale
        # view: beat at left edge, pitch at top edge, pixels per beat, pixels per pitch row
        self.view_t, self.view_top, self.sx, self.sy = 0.0, 127.5, None, None
        self.draft = None  # shape being drawn
        self.drag = None   # what the left mouse button is doing
        self._pan = None
        self._saved_view = None
        self.note_img = None     # grid + notes as one picture when there are too many notes for canvas items
        self._note_pic = None    # what that picture shows (see redraw)
        self._note_index = None  # rendered notes sorted by start, to find the visible ones quickly
        self._redraw_pending = False
        self._late_redraw = None  # request_redraw(delay)'s timer
        # a shape started with a click (no drag): the drag it would have been, following the mouse until the next
        # click finishes it
        self.follow = None
        self.arc_bend = False  # an arc dragged start -> end: its middle point follows the mouse until a click
        self.curve_clip = None   # a funnel curve's shape, copied with Ctrl+C
        self.typing = None       # the text being typed with the Text tool (roll_text.py)
        self._caret_job = None

        self.bind("<Configure>", self.on_configure)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Double-Button-1>", self.on_double)
        self.bind("<Motion>", self.on_motion)
        self.bind("<ButtonPress-3>", self.on_right)
        self.bind("<B3-Motion>", self.on_right_drag)
        self.bind("<ButtonRelease-3>", self.on_right_release)
        self.bind("<Double-Button-3>", lambda e: self.app.toggle_select_tool())
        self._scrub = None  # right-drag listening
        self.bind("<ButtonPress-2>", self.start_pan)
        self.bind("<B2-Motion>", self.pan_to)
        self.bind("<ButtonRelease-2>", self.on_middle_release)
        self.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Key>", self.on_key)

    # ------------------------------------------------------------ coordinates

    def t2x(self, b):
        return self.kb_w + (b - self.view_t) * self.sx

    def x2t(self, x):
        return (x - self.kb_w) / self.sx + self.view_t

    def p2y(self, p):
        return self.ruler_h + (self.view_top - p) * self.sy

    def y2p(self, y):
        return self.view_top - (y - self.ruler_h) / self.sy

    def event_pt(self, e, snap=True):
        """The mouse as (beat, pitch), kept inside the visible roll and the MIDI key range (dragging past the
        edge of the window stays on the edge, like the velocity pane)."""
        x = min(max(e.x, self.kb_w), self.winfo_width())
        y = min(max(e.y, self.ruler_h), self.winfo_height())
        b, p = self.x2t(x), self.y2p(y)
        sb = self.app.snap_beats()
        if snap and sb and not e.state & SHIFT:
            b, p = round(b / sb) * sb, round(p)
        return [max(0.0, b), min(max(p, 0), self.app.keys - 1)]

    def clamp_view(self):
        if self.sy is None:
            return
        rows, top = (self.winfo_height() - self.ruler_h) / self.sy, self.app.keys - 0.5
        self.view_top = top if rows >= self.app.keys else min(top, max(rows - 0.5, self.view_top))
        self.view_t = max(0.0, self.view_t)

    def fit_view(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50:
            return
        bs = [b for sh in self.app.shapes for b, _ in cached_path(sh)]
        lo, hi = (min(bs), max(bs)) if bs else (0, 4 * self.app.beats)
        span = max(hi - lo, 1)
        self.sx = (w - self.kb_w) / (span * 1.06)
        self.view_t = max(0.0, lo - span * 0.03)
        self.sy = (h - self.ruler_h) / self.app.keys
        self.view_top = self.app.keys - 0.5
        self.request_redraw()

    def view_state(self):
        if self.sx is None:
            return self._saved_view
        return {"t": self.view_t, "top": self.view_top, "sx": self.sx / self.scale, "sy": self.sy / self.scale}

    def set_view(self, v):
        """Zoom and scroll position from a saved project (applied once the canvas has a size)."""
        try:
            v = {k: float(v[k]) for k in ("t", "top", "sx", "sy")}
        except (TypeError, KeyError, ValueError):
            v = None
        self._saved_view = v
        if self.sx is not None:
            self.apply_saved_view() if v else self.fit_view()

    def apply_saved_view(self):
        v, self._saved_view = self._saved_view, None
        self.sx = min(100000.0, max(0.05, v["sx"] * self.scale))
        self.sy = min(60 * self.scale, max(1.0, v["sy"] * self.scale))
        self.view_t, self.view_top = v["t"], v["top"]
        self.clamp_view()
        self.request_redraw()

    def on_configure(self, _):
        if self.sx is None and self.winfo_width() >= 50 and self._saved_view:
            self.apply_saved_view()
        elif self.sx is None:
            self.fit_view()
        else:
            self.clamp_view()
            self.request_redraw()

    # ------------------------------------------------------------ hit testing

    def handles(self, sh):
        """(beat, pitch, point index, draggable with any tool)"""
        pts = sh["pts"]
        if sh["kind"] == "curve":
            return self.curve_handles(sh)
        if sh["kind"] == "custom":  # its box has its own corners (custom_hit); a picked curve stroke: its handles
            return self.stroke_handles(sh)
        if sh["kind"] == "free" or len(pts) > 300:
            return []
        out = [(b, p, i, sh["kind"] == "arc" and i == 1) for i, (b, p) in enumerate(pts)]  # an arc's middle: any tool
        if sh["kind"] == "funnel":  # curve starts, anchors, handles: ("start", k) / ("anchor" or "ctrl", k, end, i)
            out += [(b, p, hid, True) for b, p, hid in funnel_handles(sh)]
        return out

    def hit_handle(self, x, y, any_handle):
        sh = self.app.selected()
        if not sh:
            return None
        near = max(9, 10 * self.scale)
        for b, p, i, free in reversed(self.handles(sh)):
            if (free or any_handle) and abs(self.t2x(b) - x) <= near and abs(self.p2y(p) - y) <= near:
                return i
        return None

    def shape_at(self, x, y, prefer_selected=True):
        """The shape a click at x, y is for, or None. A selected shape under the mouse comes first (so a selected
        shape can be dragged from where another one lies over it); then lines (drawn over every note), the one on
        top first; then insides / notes, again the one on top (drawn later = on top)."""
        sels = sorted(self.app.sels, reverse=True) if prefer_selected else []
        for among in ([sels] if sels else []) + [None]:
            i = self.hit_shape(x, y, among)
            if i is None:
                i = self.note_owner(x, y, among)
            if i is not None:
                return i
        return None

    def hit_shape(self, x, y, among=None):
        """The shape whose line is near x, y (the one on top first), else the one on top whose inside it is.
        among: just these shape numbers (top first)."""
        order = range(len(self.app.shapes) - 1, -1, -1) if among is None else among
        for i in order:
            sh = self.app.shapes[i]
            strokes = cached_strokes(sh)
            # the faint dashed line as drawn (under tumours / a formula) counts too
            if sh.get("pattern") or sh.get("shape") or any(tm["on"] for tm in all_tumours(sh)):
                strokes = strokes + cached_strokes(dict(sh, tumour=None, tumours=None, pattern=None, shape=None))
            if sh["kind"] == "funnel":  # its curves' too
                strokes = strokes + funnel_origins(sh)
            for stroke in strokes:
                pts = [(self.t2x(b), self.p2y(p)) for b, p in stroke]
                if len(pts) == 1 and math.hypot(pts[0][0] - x, pts[0][1] - y) < PICK:
                    return i
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                    dx, dy = bx - ax, by - ay
                    ll = dx * dx + dy * dy
                    u = 0 if ll == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll))
                    if math.hypot(x - ax - u * dx, y - ay - u * dy) < PICK:
                        return i
        for i in order:
            sh = self.app.shapes[i]
            if "notes" in sh and self.inside_strokes(cached_strokes(sh), self.x2t(x), self.y2p(y)):
                return i  # pasted notes: anywhere in their box
            if sh["kind"] == "custom" and sh["fill"] in ("fill", "spam"):
                polys = cached_strokes(sh) if sh.get("text") else fill_plan(sh)["polys"]
                b, p = self.x2t(x), self.y2p(y)
                if (any(self.inside_strokes([poly], b, p) for poly in polys) if sh.get("union") else
                        self.inside_strokes(polys, b, p)):
                    return i  # filled shapes can be clicked anywhere inside
            if sh["kind"] == "funnel" and funnel_contains(sh, self.x2t(x), self.y2p(y)):
                return i
        return None

    def note_owner(self, x, y, among=None):
        """The shape whose note is under the mouse (a few pixels either side count for short notes), or None.
        Only while the notes are shown. among: just these shapes."""
        if not self.app.show_notes.get() or not len(self.app.rendered):
            return None
        ppq, near = self.app.ppq, 5 * self.scale
        t, t_lo, t_hi = (self.x2t(v) * ppq for v in (x, x - near, x + near))
        ns = self.visible_notes(t_lo, t_hi)
        ns = ns[(ns[:, 2] == math.floor(self.y2p(y) + 0.5)) & (ns[:, 0] <= t_hi) & (ns[:, 1] >= t_lo)]
        if among is not None:
            ns = ns[np.isin(ns[:, 5], list(among))]
        if not len(ns):
            return None
        on = ns[(ns[:, 0] <= t) & (ns[:, 1] > t)]  # right on a note first, else the nearest
        if len(on):
            return int(on[:, 5].max())  # (the shape drawn on top)
        gap = np.maximum(ns[:, 0] - t, t - ns[:, 1])
        return int(ns[gap == gap.min(), 5].max())

    # ------------------------------------------------------------ mouse

    def on_press(self, e):
        self.focus_set()
        if self.follow and self.sx is not None:  # a shape started with a click: this click finishes it
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.on_release(e, second=True)
            return
        if e.x < self.kb_w or self.sx is None:
            return
        app = self.app
        if app.tool.get() == "select":  # Select is the tool at the start, so its tip comes on first use
            app.tips.show("select", wait=True)
        if e.y < self.ruler_h:  # the bar numbers: move the play line
            playing = app.player.running
            app.stop_play()
            app.set_playhead(self.event_pt(e)[0])
            self.drag = ("seek", playing)
            return
        tool = app.tool.get()
        pt = self.draw_pt(e)

        if not self.draft:  # first: a picked curve stroke's handles can be near its custom shape's box
            i = self.hit_handle(e.x, e.y, tool == "select")
            if i is not None:
                app.push_undo(name=tr("pianoroll.drag_a_point"))
                self.drag = ("handle", i)
                return

        hit = self.custom_hit(e.x, e.y)
        if hit and hit[0] != "inside":  # the selected custom shape's corner / side: resize, just outside: turn / skew
            orig = copy.deepcopy(app.selected()["pts"])
            app.push_undo(name={"turn": tr("pianoroll.turn"), "skew": tr("pianoroll.skew")}.get(hit[0], "Resize"))
            if hit[0] == "turn":
                self.drag = ("turn", orig, self.screen_angle(orig, e.x, e.y))
            elif hit[0] == "skew":
                self.drag = ("skew", hit[1], orig, self.event_pt(e, snap=False))
            else:
                self.drag = ("resize", hit[1], orig, hit[0] == "side", self.event_pt(e, snap=False))
            return

        if tool == "text":
            self.text_click(e)
            return
        if tool == "hz":
            self.hz_click(e)
            return
        if tool == "funnel" and self.draft and len(self.draft["pts"]) == 2:
            # the funnel's line is drawn, now its wall
            self.draft["pts"] += [list(pt), list(pt)]
            self.drag = ("wall", e.x, e.y)
            self.request_redraw()
            return
        if tool == "select":
            # (its notes count too; a selected shape under the mouse first, except Ctrl+click: adds the one on top)
            i = self.shape_at(e.x, e.y, prefer_selected=not e.state & CTRL)
            if i is None and hit and not e.state & CTRL:
                i = app.sel  # anywhere inside the selected custom shape's box moves it
            # clicking the one selected funnel again: its line / curve under the mouse gets highlighted;
            # the one selected custom shape again: its stroke under the mouse gets picked (none: unpicked)
            again = i is not None and app.sels == {i} and app.shapes[i]["kind"] == "funnel"
            part = self.part_at(app.shapes[i], e.x, e.y) if again else None
            if (i is not None and app.sels == {i} and app.shapes[i]["kind"] == "custom" and not app.shapes[i].get("text")
                    and "notes" not in app.shapes[i] and not e.state & CTRL):
                again, part = True, ("stroke", self.stroke_at(app.shapes[i], e.x, e.y))
            if part and e.state & CTRL:  # Ctrl+click: highlight just this one too (or not any more)
                app.set_parts(app.parts ^ {part})
                return
            if e.state & CTRL:  # Ctrl+click adds or removes a shape
                if i is not None:
                    app.select(i, toggle=True)
                if i is None or i not in app.sels:
                    self.start_pan(e)
                    self.drag = ("pan",)
                    return
            elif i is None:
                app.select(None)
                self.start_pan(e)
                self.drag = ("pan", e.x, e.y)  # a click without dragging moves the play line here
                return
            elif i not in app.sels:
                app.select(i)
            elif i != app.sel:
                app.select_many(app.sels, i)
            app.push_undo(name=tr("pianoroll.move"))
            orig = {j: copy.deepcopy(app.shapes[j]["pts"]) for j in app.sels}
            # clicking one shape of several without dragging selects just that one; clicking the selected funnel
            # without dragging highlights the part under the mouse (none: clears it)
            self.drag = ("move", pt, orig, None if e.state & CTRL else i, False, part if again else False)
            return
        if tool == "poly":  # click its points, or drag each segment
            if self.draft is None:
                self.draft = make_shape("poly", [pt, pt], app.defaults)
            elif self.poly_point(pt):
                return
            self.drag = ("segment", e.x, e.y)
            self.request_redraw()
            return
        if tool in BOX_TOOLS:
            self.draft = self.box_draft(tool, pt, pt)
            self.drag = ("create", pt, e.x, e.y)
            self.request_redraw()
            return
        if tool == "arc":  # three clicks: start, a point it passes through, end (or drag start -> end, then bend)
            if self.draft is None:
                self.draft = make_shape("arc", [pt, pt], app.defaults)
                self.draft["k"] = self.sy / self.sx  # round as it looks on screen now
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
            return
        if tool == "custom":
            tpl = app.custom_template(app.custom_shape)
            if not tpl:
                app.status.config(text=tr("pianoroll.pick_a_custom_shape_in_the"))
                return
            self.draft = app.new_custom(tpl[0], *pt, *pt)
            self.drag = ("place", pt, e.x, e.y, tpl[1])
            self.request_redraw()
            return
        if tool == "free":
            self.draft = make_shape("free", [self.draw_pt(e, snap=False)], app.defaults)
            trail = mouse_trail(e.x_root, e.y_root, None)
            self.drag = ("free", e.x, e.y, trail[-1][2] if trail else None)
            return
        self.draft = make_shape(tool, [pt, pt], app.new_defaults(tool))
        self.drag = ("create", pt, e.x, e.y)
        self.request_redraw()

    def on_drag(self, e):
        self.app.show_position(self.position_text(e))
        if not self.drag:
            return
        kind = self.drag[0]
        if kind == "pan":
            self.pan_to(e)
        elif kind == "textsel":
            self.text_drag(e)
        elif kind == "seek":
            self.app.set_playhead(self.event_pt(e)[0])
        elif kind == "handle":
            if isinstance(self.drag[1], tuple) and self.app.selected()["kind"] == "custom":
                self.drag = ("handle", self.drag_stroke(self.app.selected(), self.drag[1], e))
            elif isinstance(self.drag[1], tuple):
                self.drag_funnel(self.app.selected(), self.drag[1], e)
            elif self.app.selected()["kind"] == "curve":
                self.drag_curve(self.app.selected(), self.drag[1], e)
            else:
                sh, i = self.app.selected(), self.drag[1]
                sh["pts"][i] = pt = self.event_pt(e)
                if sh["kind"] == "funnel" and i in (2, 3) and e.state & CTRL:  # the wall centred on the line
                    mid = self.crossing(sh["pts"][:2], (sh["pts"][5 - i], pt))
                    if mid:
                        sh["pts"][5 - i] = [2 * mid[0] - pt[0], 2 * mid[1] - pt[1]]
            self.app.shape_edited()
        elif kind == "move":
            pt = self.event_pt(e)
            _, start, orig, one, _, part = self.drag
            db, dp = pt[0] - start[0], pt[1] - start[1]
            if not (db or dp) and not self.drag[4]:
                return
            self.drag = ("move", start, orig, one, True, part)
            for j, pts in orig.items():
                self.app.shapes[j]["pts"] = [[b + db, p + dp] for b, p in pts]
            self.app.shape_edited()
        elif kind == "free":
            self.free_drag(e)
        elif kind in ("segment", "arcdrag"):  # a polyline's next point / an arc's end, at the mouse
            self.draft["pts"][-1] = self.draw_pt(e)
            self.request_redraw()
        elif kind == "create":
            kind, pt = self.draft.get("draw") or self.draft["kind"], self.draw_pt(e)
            if kind in BOX_TOOLS:
                if e.state & CTRL:  # a perfect circle / polygon on screen
                    pt = self.keep_aspect(self.drag[1], pt, self.box_aspect(kind))
                self.draft = self.box_draft(kind, self.drag[1], pt)
            else:
                self.draft = make_shape(kind, [self.drag[1], pt], self.app.new_defaults(kind))
            self.request_redraw()
        elif kind == "wall":
            pt = self.event_pt(e)
            self.draft["pts"][3] = pt
            if e.state & CTRL:  # centred on the line: both ends equally far from it
                mid = self.crossing(self.draft["pts"][:2], (self.draft["pts"][2], pt))
                if mid:
                    self.draft["pts"][2] = [2 * mid[0] - pt[0], 2 * mid[1] - pt[1]]
            self.request_redraw()
            text = self.position_text(e) or ""
            self.app.show_position(tr("pianoroll.new_funnel_notes", text=text,
                                      note_count=self.app.note_count(self.draft)))
        elif kind == "resize":
            _, k, orig, side, start = self.drag
            pt = self.resize_point(orig, k, side, start, e)
            self.app.selected()["pts"] = self.resize_custom(orig, k, pt, e.state & CTRL, side)
            self.app.shape_edited()
        elif kind == "skew":  # slides in whole grid steps / keys, wherever it was grabbed
            _, k, orig, start = self.drag
            self.app.selected()["pts"] = self.skew_custom(orig, k, *self.drag_steps(start, e))
            self.app.shape_edited()
        elif kind == "turn":
            _, orig, a0 = self.drag
            angle = self.screen_angle(orig, e.x, e.y) - a0
            angle = (angle + math.pi) % (2 * math.pi) - math.pi
            if not e.state & SHIFT:
                step = math.radians(15)
                angle = round(angle / step) * step
            self.app.selected()["pts"] = self.turn_custom(orig, angle)
            self.app.shape_edited()
            self.app.show_position(tr("pianoroll.turned", degrees=math.degrees(angle)))
        elif kind == "place":
            _, start, _, _, aspect = self.drag
            pt = self.event_pt(e)
            if e.state & CTRL and aspect:
                pt = self.keep_aspect(start, pt, aspect)
            self.draft["pts"] = box_frame(start[0], start[1], pt[0], pt[1])
            self.request_redraw()
            text = self.position_text(e) or ""
            self.app.show_position(tr("pianoroll.new_shape_notes", text=text,
                                      note_count=self.app.note_count(self.draft)))

    def free_drag(self, e):
        """Freehand: every place the mouse passed since the last move (also the ones Windows skipped while the
        program was busy), at least 3 px apart. Only the new bit of line is drawn now; the whole roll (the notes)
        follows a few times a second, so painting doesn't hold up reading the mouse."""
        _, lx, ly, since = self.drag
        trail = mouse_trail(e.x_root, e.y_root, since)
        if trail:
            since = trail[-1][2]
            ox, oy = self.winfo_rootx(), self.winfo_rooty()
            spots = [(x - ox, y - oy) for x, y, _ in trail]
        else:
            spots = [(e.x, e.y)]
        pts = self.draft["pts"]
        for x, y in spots:
            if math.hypot(x - lx, y - ly) >= 3:
                pt = self.event_pt(SimpleNamespace(x=x, y=y, state=e.state), snap=False)
                self.create_line(self.t2x(pts[-1][0]), self.p2y(pts[-1][1]), self.t2x(pt[0]), self.p2y(pt[1]),
                                 fill="#0a8f0a", width=2)
                pts.append(pt)
                lx, ly = x, y
        self.drag = ("free", lx, ly, since)
        self.request_redraw(delay=100)

    def on_release(self, e, second=False):
        """second: the click that finishes a shape started with a click (see follow)."""
        if not self.drag:
            return
        self.after_idle(self.app.settle_history)  # (a click that changed nothing isn't a step)
        kind = self.drag[0]
        self.app.catch_up_notes()
        still = False  # let go where it was pressed
        if kind in ("create", "place", "wall", "segment", "arcdrag"):
            x, y = self.drag[2:4] if kind == "place" else self.drag[-2:]
            still = abs(e.x - x) < 4 and abs(e.y - y) < 4
        if still and not second and kind in ("create", "place", "wall"):
            # a click, not a drag: the shape follows the mouse until the next click
            self.follow, self.drag = self.drag, None
            if kind == "wall":
                self.app.sync_funnel()
            return
        if kind == "seek" and self.drag[1]:
            self.app.start_play()  # it was playing: carry on from the new spot
        elif kind == "pan" and len(self.drag) > 1 and abs(e.x - self.drag[1]) < 4 and abs(e.y - self.drag[2]) < 4:
            playing = self.app.player.running
            self.app.stop_play()
            self.app.set_playhead(self.event_pt(e)[0])
            if playing:
                self.app.start_play()
        elif kind == "move" and not self.drag[4] and self.drag[3] is not None and len(self.app.sels) > 1:
            self.app.select(self.drag[3])
        elif kind == "move" and not self.drag[4] and self.drag[5] is not False:
            part = self.drag[5]
            if part and part[0] == "stroke":
                self.app.set_stroke(part[1])
            else:
                self.app.set_parts(self.part_group(self.app.selected(), part) if part else (), main=part)
        elif kind == "create":
            funnel = self.draft["kind"] == "funnel"
            if still:  # clicked twice in the same spot: nothing
                self.cancel_draft()
            elif funnel and self.add_funnel_line():
                pass
            elif funnel:  # the line: the wall comes next (the panel says so)
                self.app.sync_funnel()
            else:
                self.commit_draft()
        elif kind == "wall":
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
        elif kind == "free":
            pts = self.draft["pts"]
            if self.live_drawing() and len(pts) >= 3 and math.hypot(
                    e.x - self.t2x(pts[0][0]), e.y - self.p2y(pts[0][1])) < 12 * self.scale:
                pts.append(list(pts[0]))  # let go near its start: closed
            if len(pts) >= 2:
                # made perfect with the last sensitivity picked (smooth.py), as the roll looks now
                self.draft.update(smooth=self.app.free_smooth, k=self.sy / self.sx)
                self.commit_draft()
            else:
                self.cancel_draft()
        elif kind == "segment" and not still:  # dragged: the point goes where it was let go
            self.poly_point(self.draw_pt(e))
        elif kind == "arcdrag" and not still:  # dragged start -> end: now it bends with the mouse until a click
            (b0, p0), end = self.draft["pts"]
            self.draft["pts"] = [[b0, p0], [(b0 + end[0]) / 2, (p0 + end[1]) / 2], end]
            self.arc_bend = True
            self.request_redraw()
        elif kind == "place":
            if still:
                self.cancel_draft()
            elif self.app.confirm_big([self.draft]):
                self.commit_draft()
            else:
                self.cancel_draft()
        elif kind in ("resize", "turn", "skew") or kind == "handle" and self.app.selected()["kind"] == "custom":
            self.app.sync_custom()  # (a stroke's point dragged: its gaps may have closed)
        if kind in ("handle", "move"):
            self.app.sync_funnel()  # its note count
        self.drag = None

    def on_double(self, e):
        # Tk turns a quick second click into a double-click; only polylines use it, everything else gets a normal click
        if self.draft and self.draft["kind"] == "poly":
            self.finish_poly()
        elif self.app.tool.get() == "select" and self.text_at(e.x, e.y) is not None:
            self.edit_text(e)  # double-click a text: type in it
        elif self.app.tool.get() == "text":
            self.text_double(e)  # the word there gets selected
        else:
            self.on_press(e)

    def on_motion(self, e):
        self.app.show_position(self.position_text(e))
        if not self.drag:
            over = not self.draft and self.hit_handle(e.x, e.y, self.app.tool.get() == "select") is not None
            self.set_cursor("fleur" if over else self.custom_cursor(self.custom_hit(e.x, e.y)))
        if self.follow:  # a shape started with a click follows the mouse
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.follow, self.drag = self.drag, None
        elif self.draft and self.draft["kind"] in ("poly", "arc"):
            self.draft["pts"][1 if self.arc_bend else -1] = self.draw_pt(e)
            self.request_redraw()

    def poly_point(self, pt):
        """The polyline being drawn gets its next point at pt (a new one then follows the mouse). True if that
        finished it (live drawing: back on its first point = closed)."""
        pts = self.draft["pts"]
        pts[-1] = pt
        pts.append(list(pt))
        if self.live_drawing() and len(pts) >= 4 and pt == pts[0]:
            self.finish_poly()
            return True
        self.request_redraw()
        return False

    def finish_arc(self):
        """The arc's third click: done (the end on the start = a whole circle), unless all three are the same point."""
        a, b, c = self.draft["pts"]
        if a == c == b:
            self.draft["pts"].pop()  # still waiting for the end
        else:
            self.commit_draft()

    def finish_poly(self):
        if not (self.draft and self.draft["kind"] == "poly"):
            return
        pts = []
        for p in self.draft["pts"][:-1]:  # the last point is the one following the mouse
            if not pts or p != pts[-1]:
                pts.append(p)
        if len(pts) >= 2:
            self.draft["pts"] = pts
            self.commit_draft()
        else:
            self.cancel_draft()

    def on_right(self, e):
        """Right-click: finish a polyline being drawn, otherwise deselect (any tool).
        A right-drag listens to the notes under the mouse instead (any tool)."""
        finished = bool(self.draft and self.draft["kind"] in ("poly", "arc"))
        if self.follow:  # a shape started with a click, not finished: dropped
            self.cancel_draft()
            finished = True
        elif finished:
            self.finish_poly()
            if self.draft and self.draft["kind"] == "arc":
                self.cancel_draft()  # an arc needs all three points
        hid = None if self.draft or self.sx is None else self.hit_handle(e.x, e.y, True)
        if isinstance(hid, tuple) and hid[0] == "pt":
            hid = None  # a stroke's point: right-click works as on the stroke (its menu)
        if isinstance(hid, tuple) and self.app.selected()["kind"] == "custom":  # a picked curve stroke's point
            self.delete_stroke_handle(self.app.selected(), hid)
            return
        if isinstance(hid, tuple):  # a funnel's curve start, anchor or handle: remove it
            self.delete_funnel_handle(self.app.selected(), hid, e)
            return
        if hid is not None and self.app.selected()["kind"] == "curve" and self.delete_curve_handle(
                self.app.selected(), hid):  # a curve's anchor (removed) or handle point (pulled back in)
            return
        if self.sx is not None and e.x >= self.kb_w:
            # deselects on release if it wasn't dragged (unless it just finished a polyline)
            self._scrub = {"x": e.x, "y": e.y, "tick": None, "deselect": not finished}
        elif not finished:
            self.cancel_draft()
            self.app.select(None)

    def scrub_tick(self, e):
        return max(0.0, self.x2t(max(e.x, self.kb_w))) * self.app.ppq

    def on_right_drag(self, e):
        self.app.show_position(self.position_text(e))
        sc = self._scrub
        if not sc:
            return
        if sc["tick"] is None:
            if abs(e.x - sc["x"]) < 4 and abs(e.y - sc["y"]) < 4:
                return
            self.app.stop_play()
            sc["tick"] = self.scrub_tick(e)
        tick = self.scrub_tick(e)
        if not self.app.scrub(sc["tick"], tick):
            self._scrub = None
            return
        sc["tick"] = tick

    def on_right_release(self, e):
        sc, self._scrub = self._scrub, None
        if not sc:
            return
        if sc["tick"] is None:
            i = None if self.draft else self.shape_at(sc["x"], sc["y"])  # (its notes count too)
            if i is not None and sc["deselect"]:  # near a shape (or on its notes): its menu
                self.show_menu(e, i)
            elif sc["deselect"]:
                self.cancel_draft()
                self.app.select(None)
        else:
            self.app.scrub_end()

    def commit_draft(self):
        sh, self.draft = self.draft, None
        if not self.live_commit(sh):  # a stroke of a live shape / a circle or polygon: done there
            self.app.add_shape(sh)

    def cancel_draft(self):
        self.end_typing()
        funnel = bool(self.draft and self.draft["kind"] == "funnel")
        self.draft = None
        self.drag = None
        self.follow = None
        self.arc_bend = False
        self.request_redraw()
        if funnel:
            self.app.sync_funnel()  # no longer waiting for a wall

    def on_middle_release(self, e):
        """A middle click (without dragging) on a selected polyline adds a point there, snapped.
        On a selected funnel: a curve start (on its line) or an anchor, like a click with the Funnel tool.
        On a selected curve: an anchor there, like a click with the Curve tool."""
        sh = self.app.selected()
        if (not self._pan or abs(e.x - self._pan[0]) > 3 or abs(e.y - self._pan[1]) > 3 or self.draft
                or not sh or sh["kind"] not in ("poly", "funnel", "curve", "custom") or e.x < self.kb_w
                or e.y < self.ruler_h):
            return
        if sh["kind"] == "custom":  # its picked curve stroke: an anchor there
            self.stroke_click(sh, e, near=12 * self.scale)
            return
        if sh["kind"] == "funnel":
            self.funnel_click(sh, e)
            return
        if sh["kind"] == "curve":
            self.curve_click(sh, e, near=12 * self.scale)
            return
        self.insert_poly_point(sh, e)

    def insert_poly_point(self, sh, e):
        """A snapped point at the mouse, put in the polyline where it adds the least length."""
        pt = self.event_pt(e)
        pts = sh["pts"]
        if pt in pts:
            return
        screen = [(self.t2x(b), self.p2y(p)) for b, p in pts]
        q = (self.t2x(pt[0]), self.p2y(pt[1]))

        def extra_length(i):
            """How much longer the polyline gets if the point goes in before pts[i]."""
            if i == 0:
                return math.dist(q, screen[0])
            if i == len(pts):
                return math.dist(screen[-1], q)
            a, b = screen[i - 1], screen[i]
            return math.dist(a, q) + math.dist(q, b) - math.dist(a, b)

        i = min(range(len(pts) + 1), key=extra_length)
        self.app.push_undo(name=tr("pianoroll.add_a_point"))
        pts.insert(i, pt)
        self.app.shape_edited()

    def start_pan(self, e):
        self._pan = (e.x, e.y, self.view_t, self.view_top)

    def pan_to(self, e):
        if not self._pan or self.sx is None:
            return
        x, y, t, top = self._pan
        self.view_t = t - (e.x - x) / self.sx
        self.view_top = top + (e.y - y) / self.sy
        self.clamp_view()
        self.request_redraw()

    def on_wheel(self, e):
        if self.sx is None:
            return
        up = e.delta > 0
        f = 1.25 if up else 0.8
        zoom_time = e.state & CTRL and not e.state & ALT
        zoom_pitch = (e.state & CTRL and not e.state & SHIFT) or e.state & ALT
        if zoom_time:
            b = self.x2t(e.x)
            self.sx = min(100000.0, max(0.05, self.sx * f))
            self.view_t = b - (e.x - self.kb_w) / self.sx
        if zoom_pitch:
            p = self.y2p(e.y)
            self.sy = min(60 * self.scale, max(1.0, self.sy * f))
            self.view_top = p + (e.y - self.ruler_h) / self.sy
        if not (zoom_time or zoom_pitch):
            if e.state & SHIFT:
                self.view_t += (-1 if up else 1) * 120 / self.sx
            else:
                self.view_top += 3 if up else -3
        self.clamp_view()
        self.request_redraw()

    def on_key(self, e):
        if self.typing:
            return self.type_key(e)
        k = e.keysym.lower()
        if k == "escape":
            self.cancel_draft()
            self.app.set_parts(())
            self.app.set_stroke(None)
        elif k in ("return", "kp_enter"):
            if not self.app.vel.confirm():  # Enter = done with a velocity line / curve
                self.finish_poly()
        elif k == "delete":
            sh = self.app.selected()
            if self.funnel_parts():
                self.delete_parts()
            elif self.picked_stroke(sh) is not None and len(self.app.sels) == 1:
                self.delete_stroke(sh, self.app.stroke)
            else:
                self.app.delete_selected()
        elif k == "g" and not e.state & CTRL:
            self.app.live.set(not self.app.live.get())
        elif k == "d" and e.state & CTRL:
            self.app.duplicate()
        elif not e.state & CTRL:
            self.app.tool_hotkey(k)

    def time_text(self, x):
        """bar:beat:tick at canvas x (the velocity pane shares the roll's x axis)"""
        ppq, beats = self.app.ppq, self.app.beats
        ticks = max(0, self.x2t(x) * ppq)
        bar, rest = int(ticks // (ppq * beats)) + 1, ticks % (ppq * beats)
        return tr("pianoroll.tick", bar=bar, rest=int(rest // ppq) + 1, rest2=int(rest % ppq), ticks=int(ticks))

    def position_text(self, e):
        if self.sx is None or e.x < self.kb_w or e.y < self.ruler_h:
            return None
        text = self.time_text(e.x)
        p = round(self.y2p(e.y))
        if 0 <= p < self.app.keys:
            text += f"     {note_name(p)} ({p})"
        return text
