"""The piano roll's part of the Glissando tool (notes/gliss.py, window/panel_gliss.py; PLAN.md stage D): on the one
selected run, a round THICKNESS HANDLE (dragged up / down: copies one picked key over each, a faint line shows the
outermost copy) and every note's RIGHT END (dragged: every note of the run gets that length). Its end square
dragged snaps the end on let go (gliss_snap), like drawing it."""

from files.lang import tr
from notes.gliss import fit_pts, gliss_notes, len_for, middle_note, shifted, thick_at, plan
from window import look

GAP = 16  # px the thickness handle sits past the outermost copy's middle note (above, or below for a minus thickness)
END_NEAR = 5  # px from a note's right end that grab it


class GlissEditing:
    """Mixed into PianoRoll. Drags: ("gliss_thick", dy) = the handle (dy: mouse minus the handle's key row, px),
    ("gliss_len", start tick of the note held)."""

    def gliss_run(self):
        """The one selected Glissando run (its handles show), or None."""
        sh = self.point_shape()
        return sh if sh and sh["kind"] == "gliss" else None

    def thick_spot(self, sh):
        """(x, y) of the thickness handle, or None."""
        mid = middle_note(sh, self.app.ppq)
        if mid is None:
            return None
        t, key = mid
        thick = sh["gliss"].get("thick", 0)
        top = shifted(sh["gliss"], key, thick) if thick else key
        if top is None:  # (past the picked keys' ends: as far as it goes)
            top = key
        y0, y1 = self.row_y(top)
        y = (y0 + y1) / 2 + (GAP * self.scale if thick < 0 else -GAP * self.scale)
        return self.t2x(t / self.app.ppq), y

    def gliss_hit(self, x, y):
        """"thick" (the handle), ("end", note start tick) (a note's right end) or None, on the selected run."""
        sh = self.gliss_run()
        if sh is None:
            return None
        spot = self.thick_spot(sh)
        near = max(7, 8 * self.scale)
        if spot and abs(spot[0] - x) <= near and abs(spot[1] - y) <= near:
            return "thick"
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
        return None if hit is None else "sb_v_double_arrow" if hit == "thick" else "sb_h_double_arrow"

    def gliss_press(self, e):
        """A press on the selected run's thickness handle / a note's right end: its drag starts (True)."""
        hit = self.gliss_hit(e.x, e.y)
        if hit is None:
            return False
        sh = self.gliss_run()
        if hit == "thick":
            self.app.push_undo(name=tr("history.gliss_thick"))
            spot = self.thick_spot(sh)
            self.drag = ("gliss_thick", e.y - spot[1])
        else:
            self.app.push_undo(name=tr("history.gliss_len"))
            self.drag = ("gliss_len", hit[1])
        return True

    def gliss_drag(self, e):
        sh = self.gliss_run()
        if sh is None:
            return
        g = sh["gliss"]
        if self.drag[0] == "gliss_thick":
            gap = GAP * self.scale if g.get("thick", 0) >= 0 else -GAP * self.scale
            # (the handle's key row = the mouse less where it was grabbed; the handle sits GAP past it)
            y = e.y - self.drag[1] + gap
            new = thick_at(sh, self.y2p(y), self.app.ppq)
            key = "thick"
        else:
            tick = self.event_pt(e, snap=False)[0] * self.app.ppq
            new = len_for(sh, tick - self.drag[1], self.app.ppq)
            key = "len"
        if g.get(key) != new:
            sh["gliss"] = dict(g, **{key: new})
            self.app.gliss_defaults = dict(self.app.gliss_defaults, **{key: new})
            self.app.shape_edited()
            self.app.sync_gliss()

    def gliss_snap(self):
        """After a run's end square was dragged: the end goes where the last key's note starts (like drawing)."""
        sh = self.app.selected()
        if sh and sh["kind"] == "gliss":
            pts = fit_pts(sh, self.app.ppq)
            if pts and pts != sh["pts"]:
                sh["pts"] = pts
                self.app.shape_edited()
                self.app.sync_points()

    def draw_gliss(self, sh):
        """The selected run's outermost copy line (faint, when thicker) and its thickness handle."""
        ppq, g = self.app.ppq, sh["gliss"]
        thick = g.get("thick", 0)
        s = self.scale
        if thick:
            start, step, _, keys = plan(sh, ppq)
            ends = [(start, keys[0]), (start + round((len(keys) - 1) * step), keys[-1])] if keys else []
            moved = [(t, shifted(g, k, thick)) for t, k in ends]
            if len(moved) == 2 and None not in (moved[0][1], moved[1][1]):
                (t0, k0), (t1, k1) = moved
                self.create_line(self.t2x(t0 / ppq), self.p2y(k0), self.t2x(t1 / ppq), self.p2y(k1),
                                 fill=look.GLISS_COPY, width=max(1, round(1.5 * s)), dash=(4, 3))
        spot = self.thick_spot(sh)
        if spot:
            x, y = spot
            r = 5 * s
            self.create_oval(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.HANDLE, width=2)
