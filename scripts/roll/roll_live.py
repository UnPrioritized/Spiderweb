"""Piano roll: live drawing. With "Live shape" on, lines, polylines, freehand strokes, curves, arcs, circles and
polygons drawn on the roll become strokes of one custom shape (the selected one, or a new one), so outlines that
meet can be filled like a drawer shape. Also: picking one stroke of a custom shape, deleting it, and bending a
curve stroke with its anchors / handles (bezier.py, like the Curve shape). The strokes live in the shape's own box
(custom.py: add_stroke, refit)."""

import copy
import json
import math

from files.lang import tr
from notes.bezier import (add_anchor, can_delete, delete_point, drag_point, half_at, handle_lines, nearest, pen_handles,
                          set_symmetry)
from notes.custom import (add_stroke, box_frame, custom_settings, frame_to_bp, frame_to_uv, map_stroke,
                          new_live_shape, refit, stroke_bp, stroke_ends)
from notes.polygon import polygon_aspect, polygon_strokes
from roll.roll_funnel import seg_dist
from roll.roll_shared import ALT, PICK, cached_strokes, shown_points

STROKE_TOOLS = ("line", "poly", "free", "curve", "arc", "circle", "polygon")
BOX_TOOLS = ("circle", "polygon")  # always make custom shapes (their own, or strokes of the live one)


class LiveDrawing:
    """Mixed into PianoRoll."""

    def live_drawing(self):
        return self.app.live.get() and self.app.tool.get() in STROKE_TOOLS

    def live_target(self):
        """The custom shape new strokes go into (Live shape on and one custom shape selected), or None."""
        app, sh = self.app, self.app.selected()
        if (app.live.get() and sh and sh["kind"] == "custom" and not sh.get("text") and "notes" not in sh
                and len(app.sels) == 1):
            return sh
        return None

    def draw_pt(self, e, snap=True):
        """event_pt, and while drawing live: close (on screen) to a point of the shape drawn into, or to the start
        of the polyline being drawn, it lands exactly on it (so the outline closes)."""
        pt = self.event_pt(e, snap)
        if not self.live_drawing():
            return pt
        near = []
        sh = self.live_target()
        if sh:
            to_bp = frame_to_bp(sh["pts"])
            near += [to_bp(*q) for q in stroke_ends(sh["strokes"])]
        d = self.draft
        if d and d["kind"] == "poly" and len(d["pts"]) >= 3:
            near.append(d["pts"][0])
        best, reach = None, 8 * self.scale
        for q in near:
            dist = math.hypot(self.t2x(q[0]) - e.x, self.p2y(q[1]) - e.y)
            if dist < reach:
                best, reach = q, dist
        return [best[0], best[1]] if best is not None else pt

    def box_aspect(self, tool):
        """Width / height with Ctrl (round as it looks: a circle, an equal-sided polygon / star)."""
        return polygon_aspect(self.app.polygon_defaults) if tool == "polygon" else 1.0

    def box_draft(self, tool, a, b):
        """A circle / polygon being dragged: a custom shape of just that, filling the box from a to b. A polygon
        keeps its settings (polygon.py: the panel's, for new ones)."""
        app = self.app
        extra = {}
        if tool == "polygon":
            extra["polygon"] = dict(app.polygon_defaults)
            strokes = polygon_strokes(extra["polygon"])
        else:
            strokes = [{"kind": "ellipse", "box": [0, 0, 1, 1]}]
        return dict(app.defaults, kind="custom", name=tool.title(), strokes=strokes, **extra,
                    **custom_settings(app.custom_defaults), pts=box_frame(a[0], a[1], b[0], b[1]), draw=tool)

    @staticmethod
    def draft_strokes(sh):
        """A finished draft as strokes in beats / pitch (a crossing star can be several loops)."""
        kind = sh.get("draw") or sh["kind"]
        if kind == "circle":
            (b0, p0), (b1, _), (_, p1) = sh["pts"]
            return [{"kind": "ellipse", "box": [b0, p0, b1, p1]}]
        if kind == "polygon":
            return [stroke_bp(sh, k) for k in range(len(sh["strokes"]))]
        return [LiveDrawing.draft_stroke(sh)]

    @staticmethod
    def draft_stroke(sh):
        """A finished (not box) draft as a stroke in beats / pitch."""
        kind = sh.get("draw") or sh["kind"]
        if kind == "curve":
            return {"kind": "curve", "pts": [list(p) for p in sh["pts"]]}
        if kind == "arc":  # (stays an arc, so its notes are exactly the Arc tool's)
            return {"kind": "arc", "pts": [list(p) for p in sh["pts"]], "k": sh.get("k", 1.0)}
        if kind == "free":  # stays freehand, so it can be made perfect (k: beats per key, add_stroke converts it)
            return {"kind": "poly", "pts": [list(p) for p in sh["pts"]], "free": True, "smooth": sh.get("smooth", 0),
                    "k": sh.get("k", 1.0)}
        return {"kind": "poly", "pts": [list(p) for p in sh["pts"]]}

    def live_commit(self, sh):
        """A finished draft goes into the live shape (or a new one) as a stroke; a circle / polygon drawn without Live
        shape becomes a custom shape of its own. False if it's a normal shape (the caller adds it)."""
        app = self.app
        kind = sh.get("draw") or sh["kind"]
        box = kind in BOX_TOOLS
        if not box and not (app.live.get() and kind in STROKE_TOOLS):
            return False
        sts = self.draft_strokes(sh)
        st = sts[0]
        (b0, p0), (b1, _), (_, p1) = sh["pts"][:3] if box else ((0, 0), (1, 0), (0, 1))
        if b0 == b1 or p0 == p1 or st["kind"] != "ellipse" and all(p == st["pts"][0] for p in st["pts"]):
            self.request_redraw()  # nothing to see: dropped
            return True
        target = self.live_target()
        if target is None and not app.live.get():
            new = dict(sh)
            del new["draw"]
            app.add_shape(new)
            return True
        if target is None:
            target = new_live_shape(app.defaults, app.custom_defaults)
            for st in sts:
                k = add_stroke(target, st)
            app.add_shape(target)
        else:
            app.push_undo(name=tr("roll_live.draw_into_the_live_shape"))
            for st in sts:
                k = add_stroke(target, st)
            app.shapes_changed()
        # a new curve is picked, so its anchors and handles can be bent right away
        app.set_stroke(k if target["strokes"][k]["kind"] == "curve" else None)
        return True

    # ------------------------------------------------------------ one stroke of a custom shape

    def stroke_at(self, sh, x, y, near=PICK):
        """The number of the custom shape's stroke under (x, y) on screen, or None."""
        best = None
        for k, path in enumerate(cached_strokes(sh)):
            pts = [(self.t2x(b), self.p2y(p)) for b, p in path]
            d = min((seg_dist(x, y, a, b) for a, b in zip(pts, pts[1:])), default=math.inf)
            if d <= near and (best is None or d < best[0]):
                best = (d, k)
        return best and best[1]

    def picked_stroke(self, sh):
        """The picked stroke's number if sh is a custom shape and it has one, else None."""
        k = self.app.stroke
        return k if sh and sh["kind"] == "custom" and k is not None and k < len(sh["strokes"]) else None

    def stroke_curve(self, sh):
        """The picked stroke of the custom shape if it's a curve (its anchors and handles show), else None."""
        k = self.picked_stroke(sh)
        return sh["strokes"][k] if k is not None and sh["strokes"][k]["kind"] == "curve" else None

    def stroke_maps(self, sh):
        """(u, v) -> screen and screen -> (u, v) for the custom shape's box."""
        to_bp, to_uv = frame_to_bp(sh["pts"]), frame_to_uv(sh["pts"])
        return (lambda p: self.to_xy(to_bp(*p))), (lambda x, y: list(to_uv(*self.from_xy(x, y))))

    def point_strokes(self, sh):
        """The strokes of the selected custom shape whose points show (and drag with the Select tool): all of them
        with Live shape on (long freehand strokes show some of theirs until picked, see stroke_handles), else just
        the picked one. Not for text or pasted notes."""
        if sh.get("text") or "notes" in sh:
            return []
        k = self.picked_stroke(sh)
        if self.app.live.get() and len(self.app.sels) == 1:
            return list(range(len(sh["strokes"])))
        return [] if k is None else [k]

    @staticmethod
    def stroke_spots(st):
        """[(point number, (u, v))] a stroke can be dragged by: a polyline's points, a curve's two ends (the
        anchors between come with its pen handles), an arc's three points, an ellipse's left / right / bottom /
        top (point numbers 0-3)."""
        if st["kind"] == "ellipse":
            u0, v0, u1, v1 = st["box"]
            cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
            return list(enumerate(((u0, cv), (u1, cv), (cu, v0), (cu, v1))))
        pts = st["pts"]
        if st["kind"] == "curve":
            return [(0, pts[0]), (len(pts) - 1, pts[-1])]
        return list(enumerate(pts))

    def stroke_handles(self, sh):
        """(beat, pitch, hid, any tool) of the selected custom shape: ("pt", stroke, point number) = a stroke's
        points (Select tool, see point_strokes); ("ctrl" / "anchor", point number) = the picked curve stroke's
        anchors and handles (any tool, on top)."""
        to_bp = frame_to_bp(sh["pts"])
        picked = self.picked_stroke(sh)
        out = []
        for k in self.point_strokes(sh):
            spots = self.stroke_spots(sh["strokes"][k])
            out += [(*to_bp(*spots[j][1]), ("pt", k, spots[j][0]), False)
                    for j in shown_points(len(spots), k == picked)]
        st = self.stroke_curve(sh)
        if st:
            out += [(*to_bp(*st["pts"][j]), (kind, j), True) for j, kind in pen_handles(st["pts"]) if kind != "end"]
        return out

    def drag_stroke_point(self, sh, hid, e):
        """Dragging a stroke's point ("pt", stroke, point): snapped to the grid unless Shift, and onto another
        stroke's point within a few pixels (so outlines can be joined). Every stroke point in the same spot moves
        along (joined outlines stay joined); a curve's end brings its handle, an ellipse's side point moves that
        side. Returns the hid to go on with (an ellipse side dragged past the other side becomes that side)."""
        _, k, j = hid
        to_uv = frame_to_uv(sh["pts"])
        if to_uv is None:
            return hid
        st = sh["strokes"][k]
        if st["kind"] == "ellipse":
            u, v = to_uv(*self.event_pt(e))
            box = st["box"]  # [u0, v0, u1, v1]: left, right, bottom, top = box[0], box[2], box[1], box[3]
            box[(0, 2, 1, 3)[j]] = u if j < 2 else v
            flipped = box[0] > box[2] if j < 2 else box[1] > box[3]
            refit(sh)
            return ("pt", k, j ^ 1) if flipped else hid
        old = dict(self.stroke_spots(st))[j]
        joined = [(i, n) for i, other in enumerate(sh["strokes"]) if other["kind"] != "ellipse"
                  for n, p in self.stroke_spots(other) if math.dist(p, old) < 1e-7] or [(k, j)]
        to_bp = frame_to_bp(sh["pts"])
        pt = self.event_pt(e)
        best, reach = None, 8 * self.scale  # onto another stroke's point (not one moving along)
        for i, other in enumerate(sh["strokes"]):
            for n, p in self.stroke_spots(other) if other["kind"] != "ellipse" else ():
                if (i, n) in joined:
                    continue
                b, q = to_bp(*p)
                d = math.hypot(self.t2x(b) - e.x, self.p2y(q) - e.y)
                if d < reach:
                    best, reach = [b, q], d
        new = list(to_uv(*(best or pt)))
        for i, n in joined:
            pts = sh["strokes"][i]["pts"]
            du, dv = new[0] - pts[n][0], new[1] - pts[n][1]
            pts[n] = list(new)
            if sh["strokes"][i]["kind"] == "curve":  # its end's handle comes along
                h = 1 if n == 0 else n - 1
                pts[h] = [pts[h][0] + du, pts[h][1] + dv]
        refit(sh)
        return hid

    def stroke_handle_lines(self, sh):
        st = self.stroke_curve(sh)
        if not st:
            return []
        to_bp = frame_to_bp(sh["pts"])
        return [(to_bp(*a), to_bp(*h)) for a, h in handle_lines(st["pts"])]

    def drag_stroke(self, sh, hid, e):
        """Dragging a stroke's point (drag_stroke_point), or the picked curve stroke's anchor / handle (snapped
        unless Shift; Alt like bezier.drag_point). Returns the hid to go on with."""
        if hid[0] == "pt":
            return self.drag_stroke_point(sh, hid, e)
        st, to_uv = self.stroke_curve(sh), frame_to_uv(sh["pts"])
        if st is None or to_uv is None:
            return hid
        to_xy, from_xy = self.stroke_maps(sh)
        drag_point(st, hid[1], list(to_uv(*self.event_pt(e))), e.state & ALT, to_xy, from_xy)
        refit(sh)
        return hid

    def delete_stroke_handle(self, sh, hid):
        """Right-click on the picked curve stroke's anchor (removed) or handle (pulled back in)."""
        st = self.stroke_curve(sh)
        what = st and can_delete(st, hid[1])
        if what == "middle":
            self.app.status.config(text=tr("roll_live.the_middle_anchor_of_a_symmetric"))
        elif what:
            self.app.push_undo(name=tr("roll_live.remove_a_point"))
            delete_point(st, hid[1], self.stroke_maps(sh)[0])
            refit(sh)
            self.app.shape_edited()

    def stroke_click(self, sh, e, near=None):
        """A new anchor on the picked curve stroke where it's nearest to the mouse, moved to the mouse.
        near: only if it's that close (pixels). True if one was added."""
        st, to_uv = self.stroke_curve(sh), frame_to_uv(sh["pts"])
        if st is None or to_uv is None:
            return False
        to_xy = self.stroke_maps(sh)[0]
        seg, t, d = nearest(st["pts"], to_xy, e.x, e.y)
        if near is not None and d > near:
            return False
        before = json.dumps(self.app.shapes)
        if not add_anchor(st, seg, t, list(to_uv(*self.event_pt(e))), to_xy):
            return False
        refit(sh)
        self.app.push_undo(before, tr("roll_live.add_an_anchor"))
        self.app.shape_edited()
        return True

    def stroke_symmetry(self, sh, mode, at):
        """Symmetric halves for the picked curve stroke (see CurveEditing.set_symmetry)."""
        st = self.stroke_curve(sh)
        if st is None or (st.get("sym") or None) == mode:
            return
        to_xy = self.stroke_maps(sh)[0]
        self.app.push_undo(name=tr("roll_live.symmetric_halves"))
        set_symmetry(st, mode, half_at(st["pts"], to_xy, at.x, at.y), to_xy)
        refit(sh)
        self.app.shape_edited()

    def set_stroke_role(self, sh, k, role):
        """Outline and fill ("both") / outline only ("edge") / fill line ("cut"), custom.ROLES."""
        st = sh["strokes"][k]
        if (st.get("role") or "both") == role:
            return
        self.app.push_undo(name=tr("roll_live.stroke_role"))
        st.pop("role", None)
        if role != "both":
            st["role"] = role
        self.app.shape_edited()

    def set_stroke_colour(self, sh, k, colour):
        """The stroke's outline notes in colour (1 .. 15, like the drawer's areas) or the shape's own (0)."""
        st = sh["strokes"][k]
        if st.get("colour", 0) == colour:
            return
        self.app.push_undo(name=tr("roll_live.stroke_colour"))
        st.pop("colour", None)
        if colour:
            st["colour"] = colour
        self.app.shape_edited()

    def delete_stroke(self, sh, k):
        """Stroke k out of the custom shape (the last one: the whole shape goes)."""
        app = self.app
        if len(sh["strokes"]) <= 1:
            return app.delete_selected()
        app.push_undo(name=tr("roll_live.delete_a_stroke"))
        del sh["strokes"][k]
        refit(sh)
        app.set_stroke(None)
        app.shape_edited()
        app.sync_custom()

    # ------------------------------------------------------------ copy / paste / flip / turn one stroke

    def stroke_host(self):
        """The selected custom shape a pasted stroke goes into (one selected, not text or pasted notes), or None."""
        app, sh = self.app, self.app.selected()
        ok = sh and sh["kind"] == "custom" and not sh.get("text") and "notes" not in sh and len(app.sels) == 1
        return sh if ok else None

    def copy_stroke(self, sh, k):
        app = self.app
        app.stroke_clip, app.clip_kind, app.stroke_pastes = stroke_bp(sh, k), "stroke", 0
        app.status.config(text=tr("roll_live.copied_the_stroke_ctrl_v_pastes"))

    def paste_stroke(self):
        """The copied stroke into the selected custom shape (none selected: a new one), a grid step later and a key
        lower each time, so it doesn't sit exactly on the copy. The pasted stroke gets picked."""
        app = self.app
        app.stroke_pastes += 1
        n = app.stroke_pastes
        db, dp = n * (app.snap_beats() or 0.25), -n
        st = map_stroke(copy.deepcopy(app.stroke_clip), lambda b, p: (b + db, p + dp))
        host = self.stroke_host()
        if host is None:
            host = new_live_shape(app.defaults, app.custom_defaults)
            k = add_stroke(host, st)
            app.add_shape(host, tr("roll_live.paste_a_stroke"))
        else:
            app.push_undo(name=tr("roll_live.paste_a_stroke"))
            k = add_stroke(host, st)
            app.shapes_changed()
        app.set_stroke(k)
        app.sync_custom()

    def change_stroke(self, sh, k, fn, turn=None, name=tr("roll_live.change_a_stroke")):
        """Stroke k moved point by point by fn(beat, pitch) (turn: beats per key on screen, when it's turned 90°)."""
        st = stroke_bp(sh, k)
        new = map_stroke(st, fn)
        if turn and "k" in st:  # still round (arcs, straightened freehand), like turning a shape
            new["k"] = turn * turn / st["k"]
        app = self.app
        app.push_undo(name=name)
        del sh["strokes"][k]
        add_stroke(sh, new, at=k)
        app.shape_edited()
        app.set_stroke(k)

    def stroke_middle(self, sh, k):
        path = cached_strokes(sh)[k]
        bs, ps = [b for b, _ in path], [p for _, p in path]
        return (min(bs) + max(bs)) / 2, (min(ps) + max(ps)) / 2

    def flip_stroke(self, sh, k, sideways):
        cb, cp = self.stroke_middle(sh, k)
        self.change_stroke(sh, k, (lambda b, p: (2 * cb - b, p)) if sideways else (lambda b, p: (b, 2 * cp - p)),
                           name=tr("roll_live.flip_a_stroke"))

    def turn_stroke(self, sh, k, clockwise):
        """Turned 90° around its middle as it looks on screen (like turning a shape)."""
        if self.sx is None:
            return
        cb, cp = self.stroke_middle(sh, k)
        r, sign = self.sy / self.sx, 1 if clockwise else -1
        self.change_stroke(sh, k, lambda b, p: (cb + sign * (p - cp) * r, cp - sign * (b - cb) / r), turn=r,
                           name=tr("roll_live.turn_a_stroke"))

    def draw_picked_stroke(self, sh):
        """The picked stroke of the selected custom shape: thick, under the handles."""
        k = self.picked_stroke(sh)
        if k is None:
            return
        path = cached_strokes(sh)[k]
        if len(path) >= 2:
            self.create_line(*[v for b, p in path for v in (self.t2x(b), self.p2y(p))], fill="#7a1fe0",
                             width=max(3, round(3 * self.scale)), capstyle="round", joinstyle="round")
