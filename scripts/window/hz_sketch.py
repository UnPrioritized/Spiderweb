"""The Hz bass window's Sketch tool (S; user, 2026-10-28): a note drawn and shaped as a pitch line by hand, its
points and pieces dragged, a wobble on a piece. Everything it makes is the note's own bend line (tone["bend"], see
hz_bend.py), so the Bend tool, Copy / Paste bend and presets go on working with it. On a note it works like the
Bend tool for now (hz_bend.BEND_TOOLS). On empty space a press starts a new note there (grid; Shift free) and the
drag sets its end (hz_mouse's "new" drag, sketch_new_to); Ctrl+drag = a Select box.

While it's on, each note shows as a thick line where it's heard (its tune and bend; not the Pitch line / vibrato,
those are the red line's), with a dot at its start and end, a small ">" after its end and, when it bends, a faint
dashed line at its own key."""

import numpy as np

from notes.hzbass import note_bend, pitch
from window.hz_bend import BEND

SKETCH_WIDE = 0.6  # the line's thickness: this part of a key's row (between SKETCH_THIN and SKETCH_THICK px)
SKETCH_THIN, SKETCH_THICK = 3, 9


class HzSketch:
    def sketching(self):
        return self.tool.get() == "sketch"

    def sketch_new_to(self, d, n, e):
        """A new note being drawn (drag d, note n): it starts where the press was and ends at the grid line nearest
        the mouse (Shift: any tick), at least one step long. A click without dragging keeps the last length used."""
        if not d.get("moved") and abs(e.x - d["x"]) < 4:
            return
        d["moved"] = True
        n["len"] = max(self.shortest(e), self.snap(self.beat_at(e.x), e) - n["t"])

    def draw_sketch_notes(self, colours, selected):
        """The notes in view as lines (Sketch tool), each in its colours (fill, edge; selected ones in `selected`)."""
        c, s, most, w = self.canvas, self.s, self.bend_most(), self.canvas.winfo_width()
        wide = max(SKETCH_THIN * s, min(SKETCH_THICK * s, SKETCH_WIDE * self.sy))
        r = 3.5 * s
        for i, n in enumerate(self.tones):
            x0, x1 = self.x_of(n["t"]), self.x_of(n["t"] + n["len"])
            if x1 < self.kb_w or x0 > w:
                continue
            fill, edge = selected if i in self.sel else colours
            if n.get("bend"):
                y = self.pitch_y(pitch(n))
                c.create_line(x0, y, x1, y, fill=edge, dash=(2, 4))
                beats = np.union1d(np.linspace(n["t"], n["t"] + n["len"], 49),
                                   [n["t"] + p[0] * n["len"] for p in n["bend"]])
                ys = self.pitch_y(pitch(n) + note_bend(n, beats, most))
                xy = np.column_stack([[self.x_of(b) for b in beats], ys]).ravel().tolist()
            else:
                y = self.pitch_y(pitch(n))
                xy, ys = [x0, y, max(x1, x0 + 1), y], [y, y]
            c.create_line(*xy, fill=edge, width=wide + 2 * max(1, round(s)), capstyle="round", joinstyle="round")
            c.create_line(*xy, fill=fill, width=wide, capstyle="round", joinstyle="round")
            for x, y in ((x0, ys[0]), (x1, ys[-1])):
                c.create_oval(x - r, y - r, x + r, y + r, fill=BEND, outline=BEND)
            x, y, a = x1 + r + 2 * s, ys[-1], 3 * s  # (">": the end, where a drag will make it longer)
            c.create_line(x, y - a, x + a, y, x, y + a, fill=BEND, width=max(1, round(1.5 * s)))
