"""Freehand Thickness on the piano roll (notes/pen.py; user 2026-11-19): a circle round the mouse showing the pen's
width while the Freehand tool is on and the pen is wider than a plain line (only round the mouse: the drawn stroke
stays the thin line), and [ / ] = thinner / thicker (Shift: by 5), like paint programs and the Magnetic lasso's
Width. The keys go through the side panel's Thickness box, so selected freehand shapes change with it."""

from files.lang import trn
from files.mathexpr import calc, fmt
from notes.pen import THICK_MAX, THICK_MIN, clean_thick
from roll.roll_shared import SHIFT
from window import look

PEN_KEYS = {"bracketleft": -1, "bracketright": 1, "[": -1, "]": 1,  # (made-up key events name them by the letter)
            "braceleft": -5, "braceright": 5, "{": -5, "}": 5}  # (Shift+[ / ] give { / } on most keyboards)


class PenCircle:
    """Mixed into PianoRoll."""
    pen_mouse = None  # (x, y) of the mouse last seen over the roll while the circle can show

    def pen_on(self):
        """The circle shows: Freehand tool, pen wider than a plain line (into a live shape too)."""
        return self.app.tool.get() == "free" and self.app.free_thick > THICK_MIN

    def pen_motion(self, e):
        if self.pen_on() or self.pen_mouse:
            self.pen_mouse = (e.x, e.y)
            self.draw_pen_circle()

    def pen_leave(self, e=None):
        if self.pen_mouse:
            self.pen_mouse = None
            self.draw_pen_circle()

    def draw_pen_circle(self):
        """The pen's width as a circle round the mouse (black line on a background band, like Liquify's brush)."""
        self.delete("pencircle")
        m = self.pen_mouse
        if not m or not self.pen_on() or self.sy is None or m[0] < self.kb_w or m[1] < self.ruler_h:
            return
        x, y = m
        r = self.app.free_thick / 2 * self.sy  # (keys across, as the roll looks now: new strokes take this zoom)
        line = max(1, round(self.scale))
        band = line + 2 * max(1, round(self.scale))
        for width, col in ((band, look.ROLL_BG), (line, look.LIQUIFY_BRUSH)):
            self.create_oval(x - r, y - r, x + r, y + r, outline=col, width=width, tags="pencircle")
        self.tag_raise("pencircle")
        if not self.__dict__.get("_redrawing") and self.find_withtag("frame"):  # (under the keyboard + ruler)
            self.tag_lower("pencircle", "frame")

    def pen_key(self, e):
        """[ / ] with the Freehand tool: the Thickness one key thinner / thicker (Shift: 5). True = the key was it."""
        step = PEN_KEYS.get(e.keysym)
        app = self.app
        if step is None or app.tool.get() != "free" or self.drag:
            return False
        step *= 5 if e.state & SHIFT and abs(step) == 1 else 1
        app.sync_freehand()  # (from what the box shows: a selected freehand shape's own, else the new-stroke one)
        try:
            now = float(calc(app.thick_var.get()))
        except ValueError:
            now = app.free_thick
        value = clean_thick(min(max(now + step, THICK_MIN), THICK_MAX))
        app.thick_var.set(fmt(value))
        app.on_thick_entry()
        app.status.show(trn("pianoroll.pen_thickness", app.free_thick, n=fmt(app.free_thick)))
        self.draw_pen_circle()
        return True
