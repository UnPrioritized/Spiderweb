"""The Hz bass window's drawing tools (user, 2026-10-09; the tools button like the main toolbar's, ▾ list + pins):
a path drawn over the notes becomes notes joined by slides that follow it (notes/hz_trace.py).

While it's drawn and afterwards, until it's made, the path stays editable (its points can be dragged) and the notes
it will make show faded ("ghost" notes). It's made into real notes (one undo step) by Enter, a right-click, another
tool, or starting a new path; Esc / Ctrl+Z throw it away. Like the main piano roll, every tool takes a drag or
click-click; points snap to the grid and to whole keys (Shift = free: any tick, any cent)."""

import copy
import math

from files.lang import tr
from notes.hz_trace import path_tones
from roll.roll_shared import CTRL, SHIFT, fade
from window import look

DRAW = ("line",)  # the drawing tools, in the list's order
HOT = {"line": "l"}
NEAR = 7  # px: a press this near a point of the path being edited grabs it


class HzDraw:
    def draw_tools(self):
        """[(key, label, hot key)] for the tools button (window/tool_picker.py)."""
        names = {"line": tr("app.line")}
        return [(k, names[k], HOT[k]) for k in DRAW]

    def draw_tips(self):
        return {k: tr(f"hz.tool_{k}_tip") for k in DRAW}

    def drawing(self):
        return self.tool.get() in DRAW

    def path_point(self, e):
        """[beat, pitch in keys] at the mouse: the grid line nearest it and the middle of a key's row (Shift: the
        nearest tick and cent)."""
        beat = self.snap(self.beat_at(e.x), e)
        k = self.top + 0.5 - (e.y - self.ruler_h) / self.sy  # (pitch_y turned round)
        k = round(k, 2) if e.state & SHIFT else float(round(k))
        return [beat, min(127.0, max(0.0, k))]

    def draft_xy(self, pt):
        return self.x_of(pt[0]), self.pitch_y(pt[1])

    def draft_tones(self):
        """The notes the path being drawn would make (worked out once for each shape of it), or None."""
        d = self.draft
        if d is None:
            return None
        key = tuple(map(tuple, d["pts"])), self.app.ppq, len(self.tones)
        if d.get("made_for") != key:
            pts = sorted(d["pts"], key=lambda p: p[0]) if d["tool"] == "line" else d["pts"]
            d["made_for"], d["made"] = key, path_tones(pts, self.app.ppq, self.tones)
        return d["made"]

    # ------------------------------------------------------------ mouse

    def draw_press(self, e):
        """A press with a drawing tool: a point of the path being edited grabbed, the click-click path's end put
        down, or a new path started (the one before made first). True when it was the drawing tool's."""
        if not self.drawing() or e.state & CTRL or e.x < self.kb_w or e.y < self.ruler_h:
            return False
        d = self.draft
        if d is not None:
            if d.get("waiting"):  # click-click: this press puts the end down
                d["pts"][-1] = self.path_point(e)
                d["waiting"] = False
                return self.redraw() or True
            near = [(math.hypot(x - e.x, y - e.y), i) for i, (x, y) in enumerate(map(self.draft_xy, d["pts"]))]
            got = min(near) if near else None
            if got and got[0] <= NEAR * self.s:
                d.update(held=got[1], x=e.x, y=e.y, moved=False, was=copy.deepcopy(d["pts"]))
                return True
            self.make_draft()
        if not self.can_place():
            return True
        self.sel, self.box_kept = set(), None
        pt = self.path_point(e)
        self.draft = {"tool": self.tool.get(), "pts": [pt, list(pt)], "held": 1, "x": e.x, "y": e.y, "moved": False,
                      "new": True}
        self.redraw()
        return True

    def draw_drag(self, e):
        d = self.draft
        if d is None or d.get("held") is None:
            return False
        if not d["moved"] and abs(e.x - d["x"]) < 4 and abs(e.y - d["y"]) < 4:
            return True
        d["moved"] = True
        d["pts"][d["held"]] = self.path_point(e)
        self.redraw()
        self.show_status(e)
        return True

    def draw_release(self, e):
        d = self.draft
        if d is None or d.get("held") is None:
            return False
        if d.pop("new", False) and not d["moved"]:  # a click: the end follows the mouse until the next click
            d["waiting"] = True
        d["held"] = None
        self.redraw()
        return True

    def draw_motion(self, e):
        """The mouse moving with a drawing tool: the click-click path's end follows it; the pointer."""
        if not self.drawing():
            return False
        d = self.draft
        if d is not None and d.get("waiting"):
            d["pts"][-1] = self.path_point(e)
            self.redraw()
        inside = e.x >= self.kb_w and e.y >= self.ruler_h
        on_point = d is not None and not d.get("waiting") and any(
            math.hypot(x - e.x, y - e.y) <= NEAR * self.s for x, y in map(self.draft_xy, d["pts"]))
        self.canvas.config(cursor="fleur" if on_point else "crosshair" if inside and self.can_place() else "")
        self.show_status(e)
        return True

    # ------------------------------------------------------------ made / thrown away

    def make_draft(self):
        """The path drawn becomes notes (one undo step); nothing when it makes none (too short). True when there
        was a path."""
        if self.draft is None:
            return False
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
        xy = [v for pt in d["pts"] for v in self.draft_xy(pt)]
        c.create_line(*xy, fill=look.DRAFT_LINE, width=max(2, round(2 * s)))
        r = 3.5 * s
        for x, y in map(self.draft_xy, d["pts"]):
            c.create_rectangle(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.HANDLE,
                               width=max(1, round(1.5 * s)))


