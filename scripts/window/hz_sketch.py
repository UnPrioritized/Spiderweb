"""The Hz bass window's Sketch tool (S; user, 2026-10-28): a note drawn and shaped as a pitch line by hand, its
points and pieces dragged, a wobble on a piece. Everything it makes is the note's own bend line (tone["bend"], see
hz_bend.py), so the Bend tool, Copy / Paste bend and presets go on working with it. Points and curve squares
work like the Bend tool's (hz_bend.BEND_TOOLS), but a press on a note's line only picks the note: a DOUBLE-click
there adds a point (sketch_double), a double-click on a point takes it away. On empty space a press starts a new note there (grid; Shift free) and the
drag sets its end (hz_mouse's "new" drag, sketch_new_to); Ctrl+drag = a Select box.

While it's on, each note shows as a thick line where it's heard (its tune and bend; not the Pitch line / vibrato,
those are the red line's), with a dot at its start and end, a small ">" after its end and, when it bends, a faint
dashed line at its own key."""

import copy

import numpy as np

from files.lang import tr
from notes.hzbass import bend_part, line_at, pitch
from roll.roll_shared import SHIFT, note_name
from window import look
from window.hz_bend import BEND, LINE_NEAR, NEAR

SKETCH_WIDE = 0.6  # the line's thickness: this part of a key's row (between SKETCH_THIN and SKETCH_THICK px)
SKETCH_THIN, SKETCH_THICK = 3, 9
END_GAP = 10  # px: the ">" this far after a note's end (its middle)
HOLD_MS = 300  # a press on a line held still this long grabs the whole note (sketch_grab)


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

    def sketch_wide(self):
        """How thick a note's line is (px)."""
        return max(SKETCH_THIN * self.s, min(SKETCH_THICK * self.s, SKETCH_WIDE * self.sy))

    def sketch_reach(self):
        """How near a note's line (px, up / down) a press takes the note."""
        return max(LINE_NEAR * self.s, self.sketch_wide() / 2 + 2 * self.s)

    def sketch_double(self, e, hit):
        """A double-click on a note's line ("note", tone): a point there (on the grid, on the line as it is, so
        nothing changes yet), held like a pressed one; on a point ("point", tone, point): it goes (never the
        first / last: the note's start and end). One undo step each."""
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        i, n = hit[1], self.tones[hit[1]]
        self.sel, self.box_kept = {i}, None
        if hit[0] == "point":
            if 0 < hit[2] < len(n.get("bend") or "..") - 1:
                del n["bend"][hit[2]]
                self.bend_done({"i": i, "before": before}, tr("hz.step_note_bend_delete"))
                self.point_again()
            else:
                self.redraw()
            return True
        u = self.bend_spot(n, e)[0]
        pts = [list(p) for p in n.get("bend") or ([0.0, 0.0], [1.0, 0.0])]
        if any(abs(p[0] - u) < 1e-9 for p in pts):  # (one there already)
            self.redraw()
            return True
        j = sum(p[0] <= u for p in pts)
        pts.insert(j, [u, round(float(line_at(pts, u)), 2)])
        n["bend"] = pts
        self.drag = {"kind": "bendpt", "i": i, "j": j, "before": before, "x": e.x, "y": e.y, "moved": False,
                     "pinned": False, "name": tr("hz.step_note_bend")}
        self.redraw()
        self.show_status(e)
        return True

    def sketch_piece(self, e, i, before):
        """A press on note i's line between two points: that piece is held (sketch_piece_to). A line with no points
        between its ends is all one piece: the whole note goes up / down (its key)."""
        n = self.tones[i]
        pts = n.get("bend")
        u = min(1.0, max(0.0, (self.beat_at(e.x) - n["t"]) / max(n["len"], 1e-12)))
        j = None if not pts or len(pts) <= 2 else min(len(pts) - 2, max(0, sum(p[0] <= u for p in pts) - 1))
        d = self.drag = {"kind": "sketchpiece", "i": i, "j": j, "before": before, "x": e.x, "y": e.y, "moved": False,
                         "orig": copy.deepcopy(pts), "key": n["key"],
                         "name": tr("hz.step_move") if j is None else tr("hz.step_note_bend")}
        self.after(HOLD_MS, lambda: self.sketch_grab(d))
        self.redraw()
        self.show_status(e)
        return True

    def sketch_grab(self, d):
        """The piece pressed in drag d held still for HOLD_MS: the whole note is grabbed instead, it moves like the
        Pencil's (hz_mouse's "note" drag: in time and key, on the grid)."""
        if self.drag is not d or d["moved"] or not self.winfo_exists():
            return
        self.drag = {"kind": "note", "i": d["i"], "before": d["before"], "beat": self.beat_at(d["x"]),
                     "key": self.key_at(d["y"]), "orig": copy.deepcopy(self.tones), "x": d["x"], "y": d["y"],
                     "moved": False, "slide": None, "whole": False, "name": tr("hz.step_move"), "grabbed": True}
        self.canvas.config(cursor="fleur")
        self.redraw()

    def sketch_piece_to(self, d, n, e):
        """The piece held in drag d up / down: both its points by as many keys as the mouse moved (whole keys;
        Shift: to the cent), no further than the Range; the whole note: its key."""
        k = (d["y"] - e.y) / self.sy
        if d["j"] is None:
            n["key"] = max(0, d["key"] + round(k))
        else:
            k = round(k, 2) if e.state & SHIFT else float(round(k))
            most = self.bend_most()
            for j in (d["j"], d["j"] + 1):
                n["bend"][j][1] = max(-most, min(most, d["orig"][j][1] + k))
        self.redraw()
        self.show_status(e)
        return True

    def sketch_end_hit(self, x, y):
        """The note whose ">" (just after its end) is at (x, y), or None. It wins over the end dot right of the
        dot's middle (they're close: the dot is grabbed from its left half)."""
        near, most = NEAR * self.s, self.bend_most()
        for i in range(len(self.tones) - 1, -1, -1):
            n = self.tones[i]
            end = n["t"] + n["len"]
            x1 = self.x_of(end)
            ex, ey = x1 + END_GAP * self.s, self.pitch_y(self.bent_pitch(n, end, most))
            if x1 < x <= ex + near and abs(y - ey) <= near:
                return i
        return None

    def sketch_end_to(self, d, n, e):
        """The ">" held in drag d dragged sideways: the note ends at the grid line nearest the mouse (Shift: any
        tick), at least one step long. Its points stay on their beats: longer = the old end stays a point and a flat
        piece goes on from it; shorter = the line is cut there."""
        was = d["orig"][d["i"]]
        n["len"] = max(self.shortest(e), self.snap(self.beat_at(e.x), e) - was["t"])
        pts = was.get("bend")
        if pts and abs(n["len"] - was["len"]) > 1e-12:
            cut = bend_part(was, was["t"], n["len"])
            if cut is None:  # (flat all along: its pieces kept, on their beats)
                cut = [[p[0] * was["len"] / n["len"], 0.0] for p in pts if p[0] * was["len"] < n["len"] - 1e-9]
                cut.append([1.0, 0.0])
            n["bend"] = cut
        elif pts:
            n["bend"] = copy.deepcopy(pts)
        self.keep_leads(d["i"], d["orig"])
        self.redraw()
        self.show_status(e)
        return True

    def sketch_label(self):
        """(x, y, text) of the label beside what's held: the key it's at + how far from the note's own ("A2 +2.0
        st"); None when no point / piece is held."""
        d = self.drag
        if not d or d["kind"] not in ("bendpt", "sketchpiece") or not d.get("moved"):
            return None
        n = self.tones[d["i"]]
        if d["kind"] == "sketchpiece" and d["j"] is None:
            k, u = n["key"] - d["key"], 0.5
        else:
            j = d["j"]
            pts = n["bend"]
            k = pts[j][1]
            u = pts[j][0] if d["kind"] == "bendpt" else (pts[j][0] + pts[j + 1][0]) / 2
        at = pitch(n) + (k if d["kind"] == "bendpt" or d["j"] is not None else 0.0)
        text = tr("hz.sketch_label", key=note_name(round(at)), keys=("+" if k > 0 else "") + f"{k:.1f}")
        return self.x_of(n["t"] + u * n["len"]), self.pitch_y(at) - 14 * self.s, text

    def draw_sketch_notes(self, colours, selected):
        """The notes in view as lines (Sketch tool), each in its colours (fill, edge; selected ones in `selected`)."""
        c, s, most, w = self.canvas, self.s, self.bend_most(), self.canvas.winfo_width()
        wide = self.sketch_wide()
        grabbed = self.drag and self.drag.get("grabbed")
        r = 3.5 * s
        for i, n in enumerate(self.tones):
            x0, x1 = self.x_of(n["t"]), self.x_of(n["t"] + n["len"])
            if x1 < self.kb_w or x0 > w:
                continue
            fill, edge = selected if i in self.sel else colours
            if n.get("bend"):
                y = self.pitch_y(pitch(n))
                c.create_line(x0, y, x1, y, fill=edge, dash=(2, 4))
                beats, keys = self.bend_line(n, most)
                ys = self.pitch_y(keys)
                xy = np.column_stack([[self.x_of(b) for b in beats], ys]).ravel().tolist()
            else:
                y = self.pitch_y(pitch(n))
                xy, ys = [x0, y, max(x1, x0 + 1), y], [y, y]
            c.create_line(*xy, fill=edge, width=wide + 2 * max(1, round(s)), capstyle="round", joinstyle="round")
            c.create_line(*xy, fill=fill, width=wide, capstyle="round", joinstyle="round")
            if grabbed and i in self.sel:  # (held still: the whole note moves, a dashed box round it)
                pad = wide / 2 + 4 * s
                c.create_rectangle(x0 - pad, min(ys) - pad, x1 + pad, max(ys) + pad, outline=BEND, dash=(4, 3))
            for x, y in ((x0, ys[0]), (x1, ys[-1])):
                c.create_oval(x - r, y - r, x + r, y + r, fill=BEND, outline=BEND)
            x, y, a = x1 + END_GAP * s, ys[-1], 3 * s  # (">": the end, where a drag makes it longer / shorter)
            c.create_line(x - a / 2, y - a, x + a / 2, y, x - a / 2, y + a, fill=BEND, width=max(1, round(1.5 * s)))
        label = self.sketch_label()
        if label:
            x, y, text = label
            t = c.create_text(x, y, text=text, anchor="s", fill=look.LABEL, font=look.font(8, "bold"))
            box = c.bbox(t)
            c.create_rectangle(box[0] - 3 * s, box[1] - s, box[2] + 3 * s, box[3] + s, fill=look.HZ_DOT, outline=BEND)
            c.tag_raise(t)
