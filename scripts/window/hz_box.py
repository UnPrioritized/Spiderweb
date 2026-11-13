"""The Hz window's 8-handle box (Sketch tool only, user 2026-11-13; the plugin's): a dashed box with 8 square
handles round the selected notes when 2+ are selected, or round one note after it was held still (hz_sketch
sketch_grab). Dragging a handle scales the notes in time and pitch from the opposite side / corner, their bends
(and wobble depths) with them: a side handle = time or pitch only, a corner = both. Time follows the snap (Shift:
any tick); pitch: each note lands on a whole row and keeps its own tune (Ctrl: the exact scaled pitch, its tune
made from it). A box one pitch tall (no bends, one row) scales only in time. Smallest: 1/4 beat and 1 key (less
only when it was smaller already). A wobble keeps its number of waves (it stretches with the note); slides' dots
stretch along; loudness lines and wobble speed lines are shares of the note, so they ride along. One undo step on
release; Esc / Ctrl+Z / Delete while held = called off."""

import copy

from files.lang import tr
from files.mathexpr import fmt
from notes.hzbass import NOTE_BEND_MOST, handles, pitch, with_handles
from roll.roll_shared import BOX_CURSORS, CTRL
from window import look

BOX_HANDLE = 4  # px: half a handle square's side (a press this near its middle takes it)
BOX_PAD = 4  # px: the box sits this far outside the notes' lines
BOX_LEAST_BEATS = 0.25  # the plugin's smallest box: 1/4 beat ...
BOX_LEAST_KEYS = 1.0  # ... and 1 key (less only when it was smaller already)
TOP_KEY = 127


class HzBox:
    def box8_ids(self):
        """The ids of the notes the box is round, or None when it isn't shown: Sketch tool, 2+ selected, or the one
        selected note held still last (self.box8_held), never while a note is being moved by a hold-grab."""
        if not self.sketching() or not self.sel or self.target() is None:
            return None
        d = self.drag
        if d and d["kind"] == "note" and d.get("grabbed"):
            return None
        ids = frozenset(self.tones[i]["id"] for i in self.sel if i < len(self.tones))
        if len(ids) >= 2 or ids == self.__dict__.get("box8_held"):
            return ids
        return None

    def box8_area(self, notes=None):
        """(t0, t1, lo, hi): the selected notes' span in beats and pitch as their lines are drawn (bends + wobbles:
        the plugin's box runs through the lowest / highest pitch heard)."""
        notes = [self.tones[i] for i in self.sel] if notes is None else notes
        t0 = min(n["t"] for n in notes)
        t1 = max(n["t"] + n["len"] for n in notes)
        lo, hi = float("inf"), float("-inf")
        for n in notes:
            if n.get("bend") or n.get("wobble"):
                keys = self.bend_line(n, NOTE_BEND_MOST)[1]
                lo, hi = min(lo, float(keys.min())), max(hi, float(keys.max()))
            else:
                lo, hi = min(lo, pitch(n)), max(hi, pitch(n))
        return t0, t1, lo, hi

    def box8_rect(self, area=None):
        """The box on screen (x0, y0, x1, y1) round area (default: the selected notes now)."""
        t0, t1, lo, hi = area or self.box8_area()
        pad = self.sketch_wide() / 2 + BOX_PAD * self.s
        return self.x_of(t0) - pad, self.pitch_y(hi) - pad, self.x_of(t1) + pad, self.pitch_y(lo) + pad

    def box8_handles(self, area=None):
        """[((dx, dy), x, y)]: the 8 handles (dx -1 left / 1 right, dy -1 top / 1 bottom, 0 = the middle). A box
        one pitch tall: only the two side ones in the middle row and the corners (they scale time only)."""
        x0, y0, x1, y1 = self.box8_rect(area)
        xs, ys = {-1: x0, 0: (x0 + x1) / 2, 1: x1}, {-1: y0, 0: (y0 + y1) / 2, 1: y1}
        flat = (area or self.box8_area())[3] - (area or self.box8_area())[2] < 1e-9
        return [((dx, dy), xs[dx], ys[dy]) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dx, dy) != (0, 0) and not (flat and dx == 0)]

    def box8_hit(self, x, y):
        if self.drag or self.box8_ids() is None:
            return None
        h = (BOX_HANDLE + 1) * self.s
        return next((side for side, hx, hy in self.box8_handles() if abs(x - hx) <= h and abs(y - hy) <= h), None)

    def box8_press(self, e):
        side = self.box8_hit(e.x, e.y)
        if side is None:
            return False
        kept = self.kept_box()
        self.press_was = (set(self.sel), kept)
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        area = self.box8_area()
        t0, t1, lo, hi = area
        self.drag = {"kind": "box8", "side": side, "before": before, "orig": copy.deepcopy(self.tones),
                     "area": area, "x": e.x, "y": e.y, "moved": False, "fx": 1.0, "fy": 1.0,
                     # (where the handle was grabbed: the box's side follows the mouse from there)
                     "dt": self.beat_at(e.x) - (t1 if side[0] > 0 else t0),
                     "dp": self.pitch_at(e.y) - (hi if side[1] < 0 else lo)}
        self.redraw()
        self.show_status(e)
        return True

    def box8_drag(self, e):
        d = self.drag
        if not d or d["kind"] != "box8":
            return False
        if not d["moved"] and abs(e.x - d["x"]) < 3 and abs(e.y - d["y"]) < 3:
            return True
        d["moved"] = True
        (dx, dy), (t0, t1, lo, hi) = d["side"], d["area"]
        w, h = t1 - t0, hi - lo
        a_t, a_p, fx, fy = t0, lo, 1.0, 1.0
        if dx and w > 1e-12:
            least = min(BOX_LEAST_BEATS, w)
            edge = self.snap(self.beat_at(e.x) - d["dt"], e)
            if dx > 0:
                edge = max(t0 + least, edge)
                fx = (edge - t0) / w
            else:
                edge = max(0.0, min(t1 - least, edge))
                a_t, fx = t1, (t1 - edge) / w
        if dy and h > 1e-9:
            least = min(BOX_LEAST_KEYS, h)
            edge = self.pitch_at(e.y) - d["dp"]
            if dy < 0:
                edge = max(lo + least, edge)
                fy = (edge - lo) / h
            else:
                edge = min(hi - least, edge)
                a_p, fy = hi, (hi - edge) / h
        d["fx"], d["fy"] = fx, fy
        self.tones = copy.deepcopy(d["orig"])
        self.box8_scale(a_t, fx, a_p, fy, exact=bool(e.state & CTRL))
        self.redraw()
        self.show_status(e)
        return True

    def box8_scale(self, a_t, fx, a_p, fy, exact=False):
        """The selected notes scaled fx times in time from beat a_t, fy times in pitch from a_p (keys)."""
        ppq = self.app.ppq
        tick = 1 / ppq
        ids = {self.tones[i]["id"] for i in self.sel}
        for i in sorted(self.sel):
            n = self.tones[i]
            if abs(fx - 1.0) > 1e-12:
                start = round((a_t + (n["t"] - a_t) * fx) * ppq) / ppq
                end = round((a_t + (n["t"] + n["len"] - a_t) * fx) * ppq) / ppq
                n["t"], n["len"] = max(0.0, start), max(tick, end - start)
                for s in n.get("to") or ():  # (a slide's dot out of this note: beats before its end)
                    s["out"] = min(n["len"], s["out"] * fx)
                for run in n.get("wobble") or ():  # (it keeps its number of waves: it stretches with the note)
                    run["rate"] = run["rate"] / fx
            if abs(fy - 1.0) > 1e-12:
                p = pitch(n)
                to = a_p + (p - a_p) * fy
                if exact:
                    key = max(0, min(TOP_KEY, round(to)))
                    n["key"], n["cents"] = key, round(max(-50.0, min(50.0, (to - key) * 100.0)), 2)
                else:
                    n["key"] = max(0, min(TOP_KEY, round(to - n.get("cents", 0.0) / 100.0)))
                if n.get("bend"):
                    for k, q in enumerate(n["bend"]):
                        q[1] = round(max(-NOTE_BEND_MOST, min(NOTE_BEND_MOST, q[1] * fy)), 6)
                        h = handles(q)
                        if h:  # (Bézier handles' keys scale with it, stage H; their shares are of the piece)
                            n["bend"][k] = with_handles(q, [h[0], h[1] * fy, h[2], h[3] * fy])
                for run in n.get("wobble") or ():  # (its depth in keys scales with the bend)
                    if run.get("depth"):
                        run["depth"] = [v * fy for v in run["depth"]]
                    for q in run.get("env") or ():
                        q[1] = q[1] * fy
        if abs(fx - 1.0) > 1e-12:  # (a slide's dot into a selected note: beats after its start)
            for m in self.tones:
                for s in m.get("to") or ():
                    if s["id"] in ids:
                        n = next(k for k in self.tones if k["id"] == s["id"])
                        s["in"] = min(n["len"], s["in"] * fx)

    def box8_release(self, e):
        d = self.drag
        if not d or d["kind"] != "box8":
            return False
        self.drop_drag()
        if self.tones == d["before"]:
            self.sel = set(self.press_was[0])
            self.sel_before = None
            self.redraw()
        else:
            self.commit(tr("hz.step_box8"), d["before"])
        self.point_again()
        return True

    def box8_motion(self, e):
        side = self.box8_hit(e.x, e.y)
        if side is None:
            return False
        self.canvas.config(cursor=BOX_CURSORS[side])
        self.show_status(e)
        return True

    def box8_status(self, e=None):
        d = self.drag
        if d and d["kind"] == "box8":
            return "     " + tr("hz.box8_held", time=fmt(round(d["fx"], 3)), pitch=fmt(round(d["fy"], 3)))
        if e is not None and self.box8_hit(e.x, e.y) is not None:
            return "     " + tr("hz.box8_hint")
        return ""

    def draw_box8(self, c, s):
        """The box: dashed outline + a square on each handle (the one held filled)."""
        if self.box8_ids() is None and not (self.drag and self.drag["kind"] == "box8"):
            return
        x0, y0, x1, y1 = self.box8_rect()
        c.create_rectangle(x0, y0, x1, y1, outline=look.HZ_BEND, dash=(4, 3))
        held = self.drag["side"] if self.drag and self.drag["kind"] == "box8" else None
        q = BOX_HANDLE * s
        for side, hx, hy in self.box8_handles():
            c.create_rectangle(hx - q, hy - q, hx + q, hy + q, outline=look.HZ_BEND, width=max(1, round(1.5 * s)),
                               fill=look.HZ_BEND if side == held else look.HZ_DOT)
