"""The piano roll's part of Sustain's stops put by hand (window/sustain_window.py, notes/sustain.py): while a sustained
shape is selected, each stop it uses is a line across the shape's keys. With the Select tool, drag a line to move
its stop (on the grid, Shift = free; it stays inside the shape unless "Allow past the shape's end" is ticked),
right-click one to remove it; right-click the shape: "Add sustain stop here"; with the Sustain window open, a
double-click on its shape adds one (wins over a text's / Add between group's double-click while it's open). With the
window open, each change is one of its steps (its Ctrl+Z); closed, one undo step."""

from files.lang import tr
from notes.sustain import MOST_PAST, MOST_POINTS, is_sustained
from roll.roll_shared import CTRL, SHIFT
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
            box = self.notes_box(bare_notes(app, sh))
            if box is None:
                continue
            lo, hi, k0, k1 = box
            out += [(i, k, lo + int(round(f * (hi - lo))), k0, k1)  # (as sustain.stop_tick)
                    for k, f in enumerate(st["at"]) if f < 1 or st.get("past")]
        return out

    def notes_box(self, notes):
        """(first start, last end, lowest key, highest key) of a shape's notes, or None; the last few remembered
        (asked at every mouse move over the piano roll)."""
        kept = self.__dict__.setdefault("_stop_boxes", [])
        for arr, box in kept:
            if arr is notes:
                return box
        box = None if not len(notes) else (int(notes[:, 0].min()), int(notes[:, 1].max()),
                                           int(notes[:, 2].min()), int(notes[:, 2].max()))
        kept.append((notes, box))
        del kept[:-8]
        return box

    def stop_hit(self, x, y, state=0):
        """The stop line under the mouse (Select tool, nothing being drawn): (shape number, place in "at"), or None.
        Ctrl held: none (Ctrl+click / Ctrl+drag keep their meaning over the line, hunt)."""
        if self.app.tool.get() != "select" or self.draft or self.sx is None or state & CTRL:
            return None
        g = GRAB * self.scale
        for i, k, tick, k0, k1 in reversed(self.stop_lines()):
            if (abs(self.t2x(tick / self.app.ppq) - x) <= g and
                    self.p2y(k1 + 0.5) - g <= y <= self.p2y(k0 - 0.5) + g):
                return i, k
        return None

    def share_at(self, sh, tick):
        """As sustain.stop_share for shape sh (None: notes with no length)."""
        box = self.notes_box(bare_notes(self.app, sh))
        return None if box is None or box[1] <= box[0] else (tick - box[0]) / (box[1] - box[0])

    def stop_spot(self, sh, x, state, keep=True):
        """The tick for a stop at screen x on shape sh (on the grid; Shift = free), or None. keep: moved inside what
        the shape allows (after its first start; before its end, or MOST_PAST past it with "past"); else None when
        outside."""
        app = self.app
        box = self.notes_box(bare_notes(app, sh))
        if box is None:
            return None
        lo, hi = box[:2]
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
        hit = self.stop_hit(e.x, e.y, e.state)
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
        f = self.share_at(sh, tick)
        if f is None or f == at[k]:
            return
        at[k] = f
        app.shapes_changed()
        if app.sustain_window:
            app.sustain_window.stops_changed(gesture)

    def stop_release(self, e):
        self.stop_drag(e)
        self.stop_tidy()

    def stop_tidy(self):
        """A stop drag ends (let go, Esc, Ctrl+Z, the window closed): the stops in order again, two on one tick =
        one (hunt: Esc left two lines on one spot)."""
        if not (self.drag and self.drag[0] == "sustop"):
            return
        _, i, k, gesture = self.drag
        app = self.app
        if i < len(app.shapes):
            st = (app.shapes[i].get("sustain") or {}).get("stops")
            if st and st.get("at") and st["at"] != sorted(set(st["at"])):
                st["at"] = sorted(set(st["at"]))
                app.shapes_changed()
                if app.sustain_window:
                    app.sustain_window.stops_changed(gesture)

    def stop_edit(self, i, name, fn):
        """fn(shape i's stops) changes its stops put by hand: a step of the Sustain window's while it's open, else
        one undo step."""
        app = self.app
        win = app.sustain_window
        if not win:
            app.push_undo(name=name)
        fn(app.shapes[i]["sustain"].setdefault("stops", {"at": []}))
        app.shapes_changed()
        if win:
            win.stops_changed()

    def stop_right(self, e):
        """A right-click on a stop line: the stop goes. True if taken."""
        hit = self.stop_hit(e.x, e.y, e.state)
        if hit is None:
            return False
        i, k = hit
        self.stop_edit(i, tr("sustain.remove_stop"), lambda st: st["at"].pop(k))
        return True

    def stop_here(self, i, at):
        """The share for a new stop on shape i at at.x (on the grid; at.state Shift = free), or None: not sustained,
        outside what the shape allows, a stop there already, too many."""
        app = self.app
        sh = app.shapes[i]
        if i not in target_indices(app) or not is_sustained(sh):
            return None
        tick = self.stop_spot(sh, at.x, at.state, keep=False)
        if tick is None:
            return None
        f = self.share_at(sh, tick)
        st = sh["sustain"].get("stops") or {}
        have = st.get("at") or []
        return None if f is None or (f in have and not st.get("off")) or len(have) >= MOST_POINTS else f

    def add_stop_here(self, i, f):
        def put(st):
            st.pop("off", None)  # (adding one turns "Stop at my points" on, like Add stop)
            st["at"] = sorted(set(st["at"] + [f]))
        self.stop_edit(i, tr("sustain.add_here"), put)

    def stop_double(self, e):
        """A double-click while the Sustain window is open (Select tool): on one of its shapes = a stop there; on a
        stop line = nothing. True if taken."""
        w = self.app.sustain_window
        if not w or self.app.tool.get() != "select" or self.draft or self.sx is None:
            return False
        if self.stop_hit(e.x, e.y) is not None:
            return True
        i = self.shape_at(e.x, e.y)
        if i is None or i not in w.idx or not is_sustained(self.app.shapes[i]):
            return False
        f = self.stop_here(i, e)
        if f is None:
            self.app.status.show(tr("sustain.not_here"))
        else:
            self.add_stop_here(i, f)
        return True

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
