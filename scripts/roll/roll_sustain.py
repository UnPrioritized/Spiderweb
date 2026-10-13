"""The piano roll's part of Sustain's stops put by hand (window/sustain_window.py, notes/sustain.py): while a sustained
shape is selected, each stop it uses is a line across the shape's keys. With the Select tool, drag a line to move
its stop (on the grid, Shift = free; it stays inside the shape unless "Allow past the shape's end" is ticked). With
the Sustain window open, each change is one of its steps (its Ctrl+Z); closed, one undo step."""

from files.lang import tr
from notes.sustain import MOST_PAST, is_sustained, stop_share, stop_tick
from roll.roll_shared import SHIFT
from window import look
from window.sustain_window import bare_notes, target_indices

GRAB = 5  # px either side of a line that grab it


class SustainStops:
    def stop_lines(self):
        """[(shape number, place in its stops' "at", tick, lowest key, highest key)]: the stops put by hand that the
        selected shapes use (a stop past the end only with "past")."""
        app, out = self.app, []
        for i in target_indices(app):
            sh = app.shapes[i]
            st = (sh.get("sustain") or {}).get("stops") if is_sustained(sh) else None
            if not st or st.get("off") or not st.get("at"):
                continue
            notes = bare_notes(app, sh)
            if not len(notes):
                continue
            keys = int(notes[:, 2].min()), int(notes[:, 2].max())
            out += [(i, k, stop_tick(notes, f), *keys) for k, f in enumerate(st["at"]) if f < 1 or st.get("past")]
        return out

    def stop_hit(self, x, y):
        """The stop line under the mouse (Select tool, nothing being drawn): (shape number, place in "at"), or None."""
        if self.app.tool.get() != "select" or self.draft or self.sx is None:
            return None
        g = GRAB * self.scale
        for i, k, tick, k0, k1 in reversed(self.stop_lines()):
            if (abs(self.t2x(tick / self.app.ppq) - x) <= g and
                    self.p2y(k1 + 0.5) - g <= y <= self.p2y(k0 - 0.5) + g):
                return i, k
        return None

    def stop_spot(self, sh, x, state, keep=True):
        """The tick for a stop at screen x on shape sh (on the grid; Shift = free), or None. keep: moved inside what
        the shape allows (after its first start; before its end, or MOST_PAST past it with "past"); else None when
        outside."""
        app = self.app
        notes = bare_notes(app, sh)
        if not len(notes):
            return None
        lo, hi = int(notes[:, 0].min()), int(notes[:, 1].max())
        if hi <= lo:
            return None
        snap = 1 if state & SHIFT else app.snap_ticks()
        tick = round(self.x2t(x) * app.ppq / snap) * snap
        past = bool(((sh.get("sustain") or {}).get("stops") or {}).get("past"))
        top = lo + int(MOST_PAST * (hi - lo)) if past else hi - 1
        if lo < tick <= top:
            return tick
        if not keep:
            return None
        if tick <= lo:  # (the first grid line inside, else a tick in)
            tick = (lo // snap + 1) * snap
            return tick if tick <= top else lo + 1
        tick = top // snap * snap
        return tick if tick > lo else top

    def stop_press(self, e):
        """A left press on a stop line: it's held (one undo step when the Sustain window is closed). True if taken."""
        hit = self.stop_hit(e.x, e.y)
        if hit is None:
            return False
        if not self.app.sustain_window:
            self.app.push_undo(name=tr("sustain.move_stop"))
        self.drag = ("sustop", *hit, object())  # (the last: the moves of this drag are one step of the window's)
        return True

    def stop_drag(self, e):
        _, i, k, gesture = self.drag
        app = self.app
        if i >= len(app.shapes):
            return
        sh = app.shapes[i]
        at = ((sh.get("sustain") or {}).get("stops") or {}).get("at") or []
        tick = self.stop_spot(sh, e.x, e.state) if k < len(at) else None
        if tick is None:
            return
        f = stop_share(bare_notes(app, sh), tick)
        if f is None or f == at[k]:
            return
        at[k] = f
        app.shapes_changed()
        if app.sustain_window:
            app.sustain_window.stops_changed(gesture)

    def stop_release(self, e):
        self.stop_drag(e)
        _, i, k, gesture = self.drag
        app = self.app
        if i < len(app.shapes):
            st = (app.shapes[i].get("sustain") or {}).get("stops")
            if st and st.get("at"):
                st["at"] = sorted(set(st["at"]))  # (in order again; two on one tick = one)
                app.shapes_changed()
                if app.sustain_window:
                    app.sustain_window.stops_changed(gesture)

    def draw_sustain_stops(self):
        """The selected shapes' stops put by hand: a line across each shape's keys."""
        self.delete("sustop")
        if self.sx is None:
            return
        s = self.scale
        width, r = max(1, round(2 * s)), 3 * s
        w, h = self.winfo_width(), self.winfo_height()
        for i, k, tick, k0, k1 in self.stop_lines():
            x = self.t2x(tick / self.app.ppq)
            if not self.kb_w - r <= x <= w + r:
                continue
            y0, y1 = max(self.p2y(k1 + 0.5), self.ruler_h), min(self.p2y(k0 - 0.5), h)
            if y0 >= y1:
                continue
            self.create_line(x, y0, x, y1, fill=look.SUSTAIN_STOP, width=width, tags="sustop")
            for y in (y0, y1):  # (small ends, so it reads as something to grab)
                self.create_rectangle(x - r, y - r, x + r, y + r, outline="", fill=look.SUSTAIN_STOP, tags="sustop")
