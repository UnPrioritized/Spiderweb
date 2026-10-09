"""The Hz bass window's drawing tools (user, 2026-10-09; the tools button like the main toolbar's, ▾ list + pins):
a path drawn over the notes becomes notes joined by slides that follow it (notes/hz_trace.py).

While it's drawn and afterwards, until it's made, the path stays editable (its points can be dragged; not a
freehand one's) and the notes it will make show faded ("ghost" notes). It's made into real notes (one undo step) by
Enter, a right-click, another tool, or starting a new path; Esc / Ctrl+Z throw it away. Like the main piano roll:
Line = a drag or click-click; Polyline = click each point (or drag each piece), double-click ends it; Freehand = a
drag. Points snap to the grid and to whole keys (Shift = free: any tick, any cent); freehand ones never snap.
The path only goes forward in time (a pitch for each moment): a point can't go before the one before it."""

import copy
import math

from files.lang import tr
from notes.hz_trace import TOL, path_tones
from roll.roll_shared import CTRL, SHIFT, fade
from window import look

DRAW = ("line", "poly", "free")  # the drawing tools, in the list's order
HOT = {"line": "l", "poly": "y", "free": "f"}  # (P stays the Pencil's: Polyline = Y, user)
NEAR = 7  # px: a press this near a point of the path being edited grabs it
FREE_STEP = 3  # px: a freehand path takes a point when the mouse has moved this far (like the main piano roll's)
FREE_TOL = 1.5  # px: a freehand path's notes may be this far off it (a hand can't draw closer), at least TOL


class HzDraw:
    def draw_tools(self):
        """[(key, label, hot key)] for the tools button (window/tool_picker.py)."""
        names = {"line": tr("app.line"), "poly": tr("app.polyline"), "free": tr("app.freehand")}
        return [(k, names[k], HOT[k]) for k in DRAW]

    def draw_tips(self):
        return {k: tr(f"hz.tool_{k}_tip") for k in DRAW}

    def drawing(self):
        return self.tool.get() in DRAW

    def path_point(self, e, free=False):
        """[beat, pitch in keys] at the mouse: the grid line nearest it and the middle of a key's row (Shift, or
        free: the nearest tick and cent; free = not even ticks)."""
        k = self.top + 0.5 - (e.y - self.ruler_h) / self.sy  # (pitch_y turned round)
        if free:
            return [max(0.0, self.beat_at(e.x)), min(127.0, max(0.0, round(k, 3)))]
        beat = self.snap(self.beat_at(e.x), e)
        k = round(k, 2) if e.state & SHIFT else float(round(k))
        return [beat, min(127.0, max(0.0, k))]

    def draft_xy(self, pt):
        return self.x_of(pt[0]), self.pitch_y(pt[1])

    def grabbable(self):
        """The points of the path being edited that can be grabbed: [(number, x, y)] (a freehand path's: none; an
        unfinished polyline's: not the one following the mouse)."""
        d = self.draft
        if d is None or d["tool"] == "free" or d.get("waiting"):
            return []
        pts = d["pts"][:-1] if d.get("open") else d["pts"]
        return [(i, *self.draft_xy(p)) for i, p in enumerate(pts)]

    def kept_order(self, i, pt):
        """pt for point i of the path being drawn, kept between its neighbours in time (a line's two ends can
        cross: it's put in order when made)."""
        d = self.draft
        if d["tool"] == "line":
            return pt
        pts = d["pts"]
        lo = pts[i - 1][0] if i > 0 else 0.0
        hi = pts[i + 1][0] if i + 1 < len(pts) and not (d.get("open") and i + 1 == len(pts) - 1) else math.inf
        return [min(max(pt[0], lo), hi), pt[1]]

    def draft_tones(self):
        """The notes the path being drawn would make (worked out once for each shape of it), or None (none yet:
        a freehand path still being drawn)."""
        d = self.draft
        if d is None or d.get("held") == "free":
            return None
        key = tuple(map(tuple, d["pts"])), self.app.ppq, len(self.tones)
        if d.get("made_for") != key:
            pts = sorted(d["pts"], key=lambda p: p[0]) if d["tool"] == "line" else d["pts"]
            d["made_for"], d["made"] = key, path_tones(pts, self.app.ppq, self.tones, d.get("tol", TOL))
        return d["made"]

    # ------------------------------------------------------------ mouse

    def draw_press(self, e):
        """A press with a drawing tool: a point of the path being edited grabbed, the click-click line's end or the
        polyline's next point put down, or a new path started (the one before made first). True when it was the
        drawing tool's."""
        if not self.drawing() or e.state & CTRL or e.x < self.kb_w or e.y < self.ruler_h:
            return False
        d = self.draft
        if d is not None:
            if d.get("waiting"):  # click-click line: this press puts the end down
                d["pts"][-1] = self.path_point(e)
                d["waiting"] = False
                return self.redraw() or True
            if d.get("open"):  # polyline: the point following the mouse is put down, a new one follows (a drag
                i = len(d["pts"]) - 1  # moves it: the piece is dragged)
                d["pts"][i] = self.kept_order(i, self.path_point(e))
                d["pts"].append(list(d["pts"][i]))
                d.update(held=i + 1, x=e.x, y=e.y, moved=False)
                return self.redraw() or True
            near = [(math.hypot(x - e.x, y - e.y), i) for i, x, y in self.grabbable()]
            got = min(near) if near else None
            if got and got[0] <= NEAR * self.s:
                d.update(held=got[1], x=e.x, y=e.y, moved=False)
                return True
            self.make_draft()
        if not self.can_place():
            return True
        self.sel, self.box_kept = set(), None
        tool = self.tool.get()
        if tool == "free":
            self.draft = {"tool": tool, "pts": [self.path_point(e, free=True)], "held": "free", "x": e.x, "y": e.y,
                          "tol": max(TOL, FREE_TOL * self.s / self.sy)}
        else:
            pt = self.path_point(e)
            self.draft = {"tool": tool, "pts": [pt, list(pt)], "held": 1, "x": e.x, "y": e.y, "moved": False,
                          "new": True, "open": tool == "poly"}
        self.redraw()
        return True

    def draw_drag(self, e):
        d = self.draft
        if d is None or d.get("held") is None:
            return False
        if d["held"] == "free":
            return self.free_drag(e)
        if not d["moved"] and abs(e.x - d["x"]) < 4 and abs(e.y - d["y"]) < 4:
            return True
        d["moved"] = True
        d["pts"][d["held"]] = self.kept_order(d["held"], self.path_point(e))
        self.redraw()
        self.show_status(e)
        return True

    def free_drag(self, e):
        """Freehand: a point each FREE_STEP px the mouse moves (none going back in time); only the new bit of the
        path is drawn while the mouse moves, the notes it makes when it's let go."""
        d = self.draft
        if math.hypot(e.x - d["x"], e.y - d["y"]) < FREE_STEP * self.s:
            return True
        pt = self.path_point(e, free=True)
        last = d["pts"][-1]
        if pt[0] < last[0]:  # (back in time: the pitch changes at the same moment)
            pt[0] = last[0]
        d["pts"].append(pt)
        d["x"], d["y"] = e.x, e.y
        self.canvas.create_line(*self.draft_xy(last), *self.draft_xy(pt), fill=look.DRAFT_LINE,
                                width=max(2, round(2 * self.s)))
        return True

    def draw_release(self, e):
        d = self.draft
        if d is None or d.get("held") is None:
            return False
        if d["held"] == "free":
            d["held"] = None
            if len(d["pts"]) < 2:
                self.draft = None
        else:
            if d.pop("new", False) and not d["moved"] and not d.get("open"):  # a click: the line's end follows
                d["waiting"] = True  # the mouse until the next click
            d["held"] = None
        self.redraw()
        return True

    def draw_double(self, e):
        """A double click with the polyline being drawn: it ends there (stays editable until made)."""
        d = self.draft
        if not (self.drawing() and d is not None and d.get("open")):
            return False
        self.finish_poly()
        return True

    def finish_poly(self):
        """The polyline being drawn ends: the point following the mouse goes (and points on top of each other).
        Fewer than 2 points left: it goes."""
        d = self.draft
        pts = []
        for p in d["pts"][:-1]:
            if not pts or p != pts[-1]:
                pts.append(p)
        d.update(pts=pts, open=False, held=None)
        if len(pts) < 2:
            self.draft = None
        self.redraw()

    def draw_motion(self, e):
        """The mouse moving with a drawing tool: the click-click line's end / the polyline's next point follows it;
        the pointer."""
        if not self.drawing():
            return False
        d = self.draft
        if d is not None and (d.get("waiting") or d.get("open")) and d.get("held") is None:
            i = len(d["pts"]) - 1
            d["pts"][i] = self.kept_order(i, self.path_point(e))
            self.redraw()
        inside = e.x >= self.kb_w and e.y >= self.ruler_h
        on_point = any(math.hypot(x - e.x, y - e.y) <= NEAR * self.s for _, x, y in self.grabbable())
        self.canvas.config(cursor="fleur" if on_point and not (d or {}).get("open")
                           else "crosshair" if inside and self.can_place() else "")
        self.show_status(e)
        return True

    # ------------------------------------------------------------ made / thrown away

    def make_draft(self):
        """The path drawn becomes notes (one undo step); nothing when it makes none (too short). True when there
        was a path."""
        d = self.draft
        if d is None:
            return False
        if d.get("open"):  # (a polyline still being drawn: ended first)
            self.finish_poly()
            if self.draft is None:
                return True
        if d.get("held") == "free":
            d["held"] = None
        tones = self.draft_tones()  # (as they were shown)
        self.draft = None
        if tones:
            before = copy.deepcopy(self.tones)
            self.sel_before = (before, self.sel_state())
            self.tones += copy.deepcopy(tones)
            self.sel = set(range(len(before), len(self.tones)))
            self.commit(tr("hz.step_trace"), before)
        else:
            self.redraw()
        return True

    def drop_draft(self):
        """Esc / Ctrl+Z: the path drawn goes, nothing made. True when there was one."""
        if self.draft is None:
            return False
        self.draft = None
        self.redraw()
        return True

    # ------------------------------------------------------------ drawing it

    def draw_draft(self, colour):
        """The notes the path would make, faded, and the path over them with its points."""
        d = self.draft
        if d is None:
            return
        c, s = self.canvas, self.s
        fill, edge = (fade(x, 0.45) for x in colour)
        for n in self.draft_tones() or ():
            x0, x1, y = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            c.create_rectangle(x0, y + 1, max(x1, x0 + 2), y + self.sy - 1, fill=fill, outline=edge, dash=(3, 2))
        if len(d["pts"]) > 1:
            xy = [v for pt in d["pts"] for v in self.draft_xy(pt)]
            c.create_line(*xy, fill=look.DRAFT_LINE, width=max(2, round(2 * s)))
        r = 3.5 * s
        for _, x, y in self.grabbable():
            c.create_rectangle(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.HANDLE,
                               width=max(1, round(1.5 * s)))
