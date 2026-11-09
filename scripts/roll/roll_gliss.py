"""The piano roll's part of the Glissando tool (notes/gliss.py, window/panel_gliss.py; PLAN.md stage D): on the one
selected run, TWO DOTTED LINES parallel to the drawn line, one above and one below (user 11-02: in place of a round
handle; a little apart from the run so they're easy to grab), dragged up / down = copies above / below; every note's
RIGHT END (dragged: every note of the run gets that length). And the piano keys while picking: a press sounds the key
and lights it, moving the mouse moves both (note-off for the one left), letting go picks / unpicks that key (user
11-02)."""

import math

import numpy as np

from files.lang import tr
from notes.gliss import bent, copies_at, for_new, gliss_notes, len_for, plan, run_path, shifted
from window import look

GAP = 10  # px between the dotted lines and the outermost note rows they stand for (easy to grab off the run)
DRAW_EVERY = 8  # a bent run's dotted lines: every 8th point of its line (30 a curve piece)
END_NEAR = 5  # px from a note's right end that grab it
LINE_NEAR = 6  # px from a dotted line that grab it
SIDES = ("above", "below")


def seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - x0 - t * dx, py - y0 - t * dy)


def clip_segment(x0, y0, x1, y1, box):
    """The part of a segment inside box = (left, top, right, bottom), or None (Liang-Barsky)."""
    left, top, right, bottom = box
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - left), (dx, right - x0), (-dy, y0 - top), (dy, bottom - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return x0 + t0 * dx, y0 + t0 * dy, x0 + t1 * dx, y0 + t1 * dy


def clip_polyline(line, box):
    """A polyline [x0, y0, x1, y1, ...] cut to box: the pieces inside, each [x, y, ...] with 2+ points."""
    pieces, cur = [], None
    for j in range(0, len(line) - 2, 2):
        got = clip_segment(*line[j:j + 4], box)
        if got is None:
            cur = None
            continue
        a, b = list(got[:2]), list(got[2:])
        if cur is not None and cur[-2:] == a:
            cur += b
        else:
            cur = a + b
            pieces.append(cur)
    return pieces


def clip_polyline_many(line, box):
    """clip_polyline for a LONG line, the very same pieces: segments wholly past one side of box (clip_segment gives
    None for those) are skipped at once with NumPy, only the rest go through clip_segment (a zoomed-in Warp grid:
    thousands of pieces a line, a few on screen; Warp Split hunt 2026-11-10)."""
    import numpy as np
    a = np.asarray(line, np.float64)
    if len(a) < 4:
        return []
    x, y = a[0::2], a[1::2]
    left, top, right, bottom = box
    x0, x1, y0, y1 = x[:-1], x[1:], y[:-1], y[1:]
    near = ~((np.maximum(x0, x1) < left) | (np.minimum(x0, x1) > right)
             | (np.maximum(y0, y1) < top) | (np.minimum(y0, y1) > bottom))
    pieces, cur, last = [], None, -2
    for j in np.flatnonzero(near).tolist():
        if j != last + 1:
            cur = None
        last = j
        got = clip_segment(line[2 * j], line[2 * j + 1], line[2 * j + 2], line[2 * j + 3], box)
        if got is None:
            cur = None
            continue
        p, q = list(got[:2]), list(got[2:])
        if cur is not None and cur[-2:] == p:
            cur += q
        else:
            cur = p + q
            pieces.append(cur)
    return pieces


class GlissEditing:
    """Mixed into PianoRoll. Drags: ("gliss_copies", side, dy) = a dotted line (dy: mouse minus the line where it was
    grabbed, px), ("gliss_len", start tick of the note held), ("keyhold", key, ctrl) = a piano key held."""

    held_key = None  # (the piano key held while picking: lit, roll_draw.draw_keyboard)

    def gliss_run(self):
        """The one selected Glissando run (its dotted lines show), or None."""
        sh = self.point_shape()
        return sh if sh and sh["kind"] == "gliss" else None

    def run_pitch_at(self, sh, beat):
        """The drawn line's pitch at beat (a curve: its point nearest that beat; held level past its ends)."""
        a = run_path(sh)
        if len(a) == 2:
            (b0, p0), (b1, p1) = a
            if b1 == b0:
                return (p0 + p1) / 2
            f = max(0.0, min(1.0, (beat - b0) / (b1 - b0)))
            return p0 + (p1 - p0) * f
        return float(a[int(np.argmin(abs(a[:, 0] - beat))), 1])

    def copy_line(self, sh, side):
        """[x0, y0, x1, y1, ...] of side's dotted line: the drawn line (a curve: as drawn, bends too) moved to the
        outermost copy on that side (by its ends' keys, in between shared out along it), then GAP px further out."""
        g, ppq = sh["gliss"], self.app.ppq
        n = g.get(side, 0) * (1 if side == "above" else -1)
        keys = plan(sh, ppq)[3]
        a = run_path(sh)[::DRAW_EVERY] if bent(sh) else run_path(sh)
        if bent(sh) and len(a) and a[-1].tolist() != run_path(sh)[-1].tolist():
            a = np.vstack([a, run_path(sh)[-1:]])
        offs = []
        for k in ((keys[0], keys[-1]) if keys else (None, None)):
            to = shifted(g, k, n) if k is not None else None
            offs.append((to - k) if to is not None else n)
        f = np.linspace(0.0, 1.0, len(a))
        p = a[:, 1] + offs[0] + (offs[1] - offs[0]) * f
        out = (GAP * self.scale + self.sy / 2) * (-1 if side == "above" else 1)
        xs, ys = self.t2x(a[:, 0]), self.p2y(p) + out
        return np.column_stack([xs, ys]).ravel().tolist()

    def line_near(self, line, x, y):
        """(distance, the line's y at the nearest place) from (x, y) to a polyline [x0, y0, x1, y1, ...]."""
        best = (math.inf, y)
        for j in range(0, len(line) - 2, 2):
            x0, y0, x1, y1 = line[j:j + 4]
            d = seg_dist(x, y, x0, y0, x1, y1)
            if d < best[0]:
                f = 0 if x1 == x0 else max(0, min(1, (x - x0) / (x1 - x0)))
                best = (d, y0 + (y1 - y0) * f)
        return best

    def gliss_hit(self, x, y):
        """("copies", side) (a dotted line), ("end", note start tick) (a note's right end) or None, on the selected
        run. Note ends win over the dotted lines (the lower line can sit over the notes' ends)."""
        sh = self.gliss_run()
        if sh is None:
            return None
        ppq = self.app.ppq
        notes = gliss_notes(sh, ppq)
        tick = self.x2t(x) * ppq  # (only the notes ending near the mouse are looked at: a thick run has thousands)
        reach = END_NEAR * self.scale / self.sx * ppq + 1
        for s, e, k in notes[abs(notes[:, 1] - tick) <= reach].tolist():
            y0, y1 = self.row_y(k)
            if min(y0, y1) <= y <= max(y0, y1) and abs(self.t2x(e / ppq) - x) <= END_NEAR * self.scale:
                return ("end", s)
        for side in SIDES:
            if self.line_near(self.copy_line(sh, side), x, y)[0] <= LINE_NEAR * self.scale:
                return ("copies", side)
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
            self.drag = ("gliss_copies", hit[1], e.y - self.line_near(self.copy_line(sh, hit[1]), e.x, e.y)[1])
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
            new, key = len_for(sh, tick - self.drag[1], self.app.ppq, self.drag[1]), "len"
        if g.get(key) != new:
            sh["gliss"] = dict(g, **{key: new})
            self.app.gliss_defaults = for_new(dict(self.app.gliss_defaults, **{key: new}))
            self.app.shape_edited()
            self.app.sync_gliss()

    def draw_gliss(self, sh):
        """The selected run's two dotted lines (copies above / below). Cut to the view first and drawn as thin lines
        side by side, never one thick dashed line (roll/CLAUDE.md: a thick dashed line reaching far off screen froze
        the PC)."""
        w = max(1, round(1.5 * self.scale))
        view = (self.kb_w - 20, self.ruler_h - 20, self.winfo_width() + 20, self.winfo_height() + 20)
        shifts = [0] + [k for k in range(-((w - 1) // 2), w // 2 + 1) if k]
        for side in SIDES:
            for piece in clip_polyline(self.copy_line(sh, side), view):
                for k in shifts:
                    self.create_line(*[v + k * (n % 2) for n, v in enumerate(piece)], fill=look.GLISS_COPY, width=1,
                                     dash=(4, 3))

    # ---- bending (stage E): a run gets the Curve tool's anchors and handles
    def gliss_bend(self, sh, e, near=None):
        """Middle-click / "Add anchor here" on a run: a new anchor there, moved to the mouse (a straight run becomes a
        curve first: the same line, so the same notes). True if one was added."""
        if bent(sh):
            return self.curve_click(sh, e, near)
        old = sh["pts"]
        sh["pts"] = [list(old[0]), list(old[0]), list(old[-1]), list(old[-1])]
        if self.curve_click(sh, e, near):
            return True
        sh["pts"] = old
        return False

    def gliss_straight(self, sh):
        """A bent run straight again: from its start to its end (right-click "Make the run straight")."""
        if not bent(sh):
            return
        self.app.push_undo(name=tr("history.gliss_straight"))
        sh["pts"] = [list(sh["pts"][0]), list(sh["pts"][-1])]
        self.app.shape_edited()
        self.app.build_points()

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
