"""The piano roll's part of the Glissando tool (notes/gliss.py, window/panel_gliss.py; PLAN.md stage D): on the one
selected run, TWO DOTTED LINES parallel to the drawn line, one above and one below (user 11-02: in place of a round
handle; a little apart from the run so they're easy to grab), dragged up / down = copies above / below; every note's
RIGHT END (dragged: every note of the run gets that length). And the piano keys while picking: a press sounds the key
and lights it, moving the mouse moves both (note-off for the one left), letting go picks / unpicks that key (user
11-02)."""

import math

from files.lang import tr
from notes.gliss import copies_at, gliss_notes, len_for, plan, shifted
from window import look

GAP = 10  # px between the dotted lines and the outermost note rows they stand for (easy to grab off the run)
END_NEAR = 5  # px from a note's right end that grab it
LINE_NEAR = 6  # px from a dotted line that grab it
SIDES = ("above", "below")


def seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - x0 - t * dx, py - y0 - t * dy)


class GlissEditing:
    """Mixed into PianoRoll. Drags: ("gliss_copies", side, dy) = a dotted line (dy: mouse minus the line where it was
    grabbed, px), ("gliss_len", start tick of the note held), ("keyhold", key, ctrl) = a piano key held."""

    held_key = None  # (the piano key held while picking: lit, roll_draw.draw_keyboard)

    def gliss_run(self):
        """The one selected Glissando run (its dotted lines show), or None."""
        sh = self.point_shape()
        return sh if sh and sh["kind"] == "gliss" else None

    def run_pitch_at(self, sh, beat):
        """The drawn line's pitch at beat (held level past its ends)."""
        (b0, p0), (b1, p1) = sh["pts"][0], sh["pts"][-1]
        if b1 == b0:
            return (p0 + p1) / 2
        f = max(0.0, min(1.0, (beat - b0) / (b1 - b0)))
        return p0 + (p1 - p0) * f

    def copy_line(self, sh, side):
        """(x0, y0, x1, y1) of side's dotted line: the drawn line moved to the outermost copy on that side (by its
        ends' keys), then GAP px further out."""
        g, ppq = sh["gliss"], self.app.ppq
        n = g.get(side, 0) * (1 if side == "above" else -1)
        keys = plan(sh, ppq)[3]
        (b0, p0), (b1, p1) = sh["pts"][0], sh["pts"][-1]
        late_first = b1 < b0  # (keys go from the earlier end)
        out = []
        for (b, p), first in (((b0, p0), not late_first), ((b1, p1), late_first)):
            k = (keys[0] if first else keys[-1]) if keys else None
            to = shifted(g, k, n) if k is not None else None
            p = p + ((to - k) if to is not None else n)
            y0, y1 = self.row_y(math.floor(p + 0.5))
            gap = GAP * self.scale
            edge = min(y0, y1) - gap if side == "above" else max(y0, y1) + gap
            out += [self.t2x(b), edge + (self.p2y(p) - (y0 + y1) / 2)]
        return tuple(out)

    def gliss_hit(self, x, y):
        """("copies", side) (a dotted line), ("end", note start tick) (a note's right end) or None, on the selected
        run."""
        sh = self.gliss_run()
        if sh is None:
            return None
        for side in SIDES:
            if seg_dist(x, y, *self.copy_line(sh, side)) <= LINE_NEAR * self.scale:
                return ("copies", side)
        ppq = self.app.ppq
        notes = gliss_notes(sh, ppq)
        tick = self.x2t(x) * ppq  # (only the notes ending near the mouse are looked at: a thick run has thousands)
        reach = END_NEAR * self.scale / self.sx * ppq + 1
        for s, e, k in notes[abs(notes[:, 1] - tick) <= reach].tolist():
            y0, y1 = self.row_y(k)
            if min(y0, y1) <= y <= max(y0, y1) and abs(self.t2x(e / ppq) - x) <= END_NEAR * self.scale:
                return ("end", s)
        return None

    def gliss_cursor(self, x, y):
        hit = self.gliss_hit(x, y)
        return None if hit is None else "sb_v_double_arrow" if hit[0] == "copies" else "sb_h_double_arrow"

    def gliss_press(self, e):
        """A press on the selected run's dotted line / a note's right end: its drag starts (True)."""
        hit = self.gliss_hit(e.x, e.y)
        if hit is None:
            return False
        sh = self.gliss_run()
        if hit[0] == "copies":
            self.app.push_undo(name=tr("history.gliss_copies"))
            x0, y0, x1, y1 = self.copy_line(sh, hit[1])
            f = 0 if x1 == x0 else max(0, min(1, (e.x - x0) / (x1 - x0)))
            self.drag = ("gliss_copies", hit[1], e.y - (y0 + (y1 - y0) * f))
        else:
            self.app.push_undo(name=tr("history.gliss_len"))
            self.drag = ("gliss_len", hit[1])
        return True

    def gliss_drag(self, e):
        sh = self.gliss_run()
        if sh is None:
            return
        g = sh["gliss"]
        if self.drag[0] == "gliss_copies":
            side, dy = self.drag[1], self.drag[2]
            gap = GAP * self.scale + self.sy / 2  # (the line sits that far past its copy's key row middle)
            y = e.y - dy + (gap if side == "above" else -gap)
            beat = self.x2t(e.x)
            away = self.y2p(y) - self.run_pitch_at(sh, beat)
            new, key = copies_at(sh, away, side, self.app.ppq), side
        else:
            tick = self.event_pt(e, snap=False)[0] * self.app.ppq
            new, key = len_for(sh, tick - self.drag[1], self.app.ppq), "len"
        if g.get(key) != new:
            sh["gliss"] = dict(g, **{key: new})
            self.app.gliss_defaults = dict(self.app.gliss_defaults, **{key: new})
            self.app.shape_edited()
            self.app.sync_gliss()

    def draw_gliss(self, sh):
        """The selected run's two dotted lines (copies above / below)."""
        w = max(1, round(1.5 * self.scale))
        for side in SIDES:
            self.create_line(*self.copy_line(sh, side), fill=look.GLISS_COPY, width=w, dash=(4, 3))

    # ---- the piano keys while picking (pianoroll.key_press starts it)
    def key_hold(self, key, ctrl):
        self.drag = ("keyhold", key, ctrl)
        self.held_key = key
        self.app.tap_key(key)
        self.request_redraw()

    def key_hold_drag(self, e):
        """The held piano key follows the mouse: the old one's note-off, the new one sounds and lights."""
        key = max(0, min(self.app.keys - 1, math.floor(self.y2p(e.y) + 0.5)))
        if key != self.drag[1]:
            self.app.tap_end()
            self.drag = ("keyhold", key, self.drag[2])
            self.held_key = key
            self.app.tap_key(key)
            self.request_redraw()

    def key_hold_end(self):
        """Let go: its note-off, then the key is picked / unpicked (Ctrl at the press: that key only)."""
        _, key, ctrl = self.drag
        self.drag = self.held_key = None
        self.app.tap_end()
        self.app.gliss_key_click(key, ctrl)
        self.request_redraw()
