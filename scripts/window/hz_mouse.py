"""The Hz bass window's mouse and keys on its notes: placing, moving, stretching, the Select box, copy / paste,
slides made by middle-clicks, right-click menus, Delete, and Esc / Ctrl+Z while the mouse is held."""

import copy
import math
import tkinter as tk
from types import SimpleNamespace

from files.lang import tr
from files.snap import snap_beats
from files.system import double_click_ms
from notes.hzbass import TUNE, can_slide, left_edge, next_id, pitch
from roll.roll_shared import (BOX_CURSORS, BOX_SCROLL_MS, BOX_STILL, CTRL, SELECT_CURSOR, SHIFT, boxes_side,
                              boxes_upright, grid_span)


TUNE_STICK = 3.0  # cents: a dragged tune this near the exact tone sticks to it (at any zoom; was 5 px, user)
DOUBLE_MS = double_click_ms()  # how quick a second click has to be to make a double click (the system's setting)


class HzMouse:
    def snap(self, beat, e):
        """beat on the snap grid: the nearest line (Shift = off)."""
        sb = self.snap_beats()
        if not sb or e.state & SHIFT:
            sb = 1 / self.app.ppq
        return max(0.0, round(beat / sb) * sb)

    def snap_beats(self):
        return snap_beats(self.app.hz_snap.get(), self.app.beats)

    def box_area(self, d=None):
        """The Select box being dragged (d: this box drag instead) as (beat, key, beat, key) corners, a key being
        the top of its row: out to whole snap steps and whole keys (grid_span; Shift / snap off = whole ticks, keys
        still whole: user), so it's never thinner than one step and one key. None while it's still a click."""
        d = d or self.drag
        (x, y), (cx, cy) = self.box_from(d), d["to"]
        if abs(cx - x) < BOX_STILL and abs(cy - y) < BOX_STILL:
            return None
        tick, step = 1 / self.app.ppq, self.snap_beats()
        b0, b1 = grid_span(self.beat_at(x), self.beat_at(cx), tick if d.get("shift0") else step or tick,
                           tick if d.get("shift") else step or tick)
        k0, k1 = sorted((self.key_at(y), self.key_at(cy)))
        return b0, k1, b1, k0 - 1

    def box_from(self, d):
        """Where the Select box being dragged was pressed, on screen now. It's kept as (beat, key) (d["from"]): the
        wheel, panning and scrolling past the edge leave it where it is in the song (user)."""
        b, k = d["from"]
        return self.x_of(b), self.ruler_h + (self.top - k) * self.sy

    def box_rect(self, area):
        """A box_area on screen (x0, y0, x1, y1), x0 < x1, y0 < y1."""
        (x0, x1), (y0, y1) = (sorted((self.x_of(area[0]), self.x_of(area[2]))),
                              sorted((self.y_of(area[1]), self.y_of(area[3]))))
        return x0, y0, x1, y1

    def box_to(self, d):
        """The Select box's corner goes to the mouse (d["mouse"]), kept inside the piano roll. Nothing is selected
        before it's let go (box_pick; user: like Domino)."""
        x, y, state = d["mouse"]
        d["to"] = (min(max(x, self.kb_w), self.canvas.winfo_width()),
                   min(max(y, self.ruler_h), self.canvas.winfo_height()))
        d["shift"] = bool(state & SHIFT)
        self.redraw()

    def box_pick(self, d):
        """The Select box let go: it selects the notes it touches (Ctrl: added to the ones selected at the press)."""
        x0, y0, x1, y1 = self.box_rect(self.box_area(d))
        self.sel = d["base"] | {i for i, n in enumerate(self.tones)
                    if self.x_of(n["t"]) < x1 and self.x_of(n["t"] + n["len"]) > x0
                    and self.y_of(n["key"]) < y1 and self.y_of(n["key"]) + self.sy > y0}

    def box_scroll(self):
        """A Select box dragged past the edge: the view goes a beat that way (3 keys up / down) at once and then
        every BOX_SCROLL_MS while the mouse stays out there, as on the main piano roll. The box stays where it is in
        the song (left behind as the view moves) until the mouse moves again."""
        self.box_timer = None
        d = self.drag
        if not d or d["kind"] != "box":
            return
        x, y, _ = d["mouse"]
        dx = (x > self.canvas.winfo_width()) - (x < self.kb_w)
        dy = (y > self.canvas.winfo_height()) - (y < self.ruler_h)
        if not dx and not dy:
            return
        t0, top = self.t0, self.top
        self.t0 += dx
        self.top -= dy * 3
        self.clamp_view()
        ex, ey = d["to"]
        d["to"] = (ex - (self.t0 - t0) * self.sx, ey + (self.top - top) * self.sy)
        self.redraw()
        self.box_timer = self.after(BOX_SCROLL_MS, self.box_scroll)

    def stretch_to(self, d, e):
        """The kept Select box's right side dragged: it goes to the mouse (the grid line nearest it, Shift = not
        snapped) and every note it selected gets that much longer / shorter, the same for all (user, like Domino:
        one grid step = one grid step on each note); starts and keys stay."""
        (b0, _, b1, _), orig = d["area"], d["orig"]
        at = max(self.snap(self.beat_at(e.x), e), b0 + self.shortest(e))
        for i in self.sel:
            self.tones[i]["len"] = max(1 / self.app.ppq, orig[i]["len"] + at - b1)
        for i in self.sel:  # (the slides' dots stay where they are, as when one note's end is dragged: user)
            self.keep_leads(i, orig)
        d["box"] = [(a, t, max(a + 1 / self.app.ppq, z + at - b1), u) for a, t, z, u in d["boxes"]]  # (each one's
        self.box_kept = (d["box"], set(self.sel))  # right side the same amount, like the notes)
        self.redraw()
        self.show_status(e)

    def sel_state(self):
        """The selected notes as the main window's undo step keeps them (App.sel_state): (their numbers, the kept
        Select boxes or None, how many notes there are), or None (no Hz bass shown)."""
        if self.pushing:
            return self.pushing
        if self.target() is None:
            return None
        boxes = self.kept_box()
        return sorted(self.sel), boxes and list(boxes), len(self.tones)

    def restore_sel(self, state):
        """Undo / redo put the shapes back (after_restore): the notes selected then and their Select boxes too."""
        if state and self.target() is not None and len(self.tones) == state[2]:
            self.sel = set(state[0])
            self.box_kept = (state[1], set(self.sel)) if state[1] else None
            self.redraw()

    def kept_box(self):
        """The last Select boxes [box_area, ...] (Ctrl+drag adds one), still shown after letting go while what they
        selected is still the selection (a press or any other change of the selection drops them). None = not
        shown."""
        if self.box_kept and self.box_kept[1] == self.sel:
            return self.box_kept[0]
        self.box_kept = None
        return None

    def shortest(self, e):
        sb = self.snap_beats()
        return sb if sb and not e.state & SHIFT else 1 / self.app.ppq

    def hit(self, x, y):
        """What's under the mouse: ("in" / "out", tone, slide) a red dot,("left" / "right", tone) a note's end,
        ("tune", tone) the red line in a note (tall rows only), ("note", tone), or None."""
        r = 6 * self.s
        for dx, dy, i, which, s in self.dots():
            if abs(x - dx) <= r and abs(y - dy) <= r:
                return which, i, s
        if x < self.kb_w or y < self.ruler_h:
            return None
        for i in range(len(self.tones) - 1, -1, -1):
            n = self.tones[i]
            x0, x1, y0 = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            if x0 - 1 <= x <= max(x1, x0 + 2) + 1 and y0 <= y < y0 + self.sy:
                edge = min(5 * self.s, (x1 - x0) / 3)
                on_line = (self.tune_rows() and self.app.hz_line.get()
                           and abs(y - self.pitch_y(pitch(n))) <= 4 * self.s)
                return ("right" if x >= x1 - edge else "left" if x <= x0 + edge else
                        "tune" if on_line else "note"), i
        return None

    def on_kept_box(self, kept, e, hit):
        """Where the mouse is on the kept Select boxes: (1, 0) the right side (its corners too), (0, 0) inside (a
        note there wins), or None. The left side, top and bottom do nothing (user, like Domino); a slide's dot
        wins over it all. With Ctrl only inside counts, notes too (a drag there moves a copy; Select tool only)."""
        if not kept or not self.sel or hit and hit[0] in ("in", "out"):
            return None
        side = boxes_side([self.box_rect(a) for a in kept], e.x, e.y, 5 * self.s)
        if e.state & CTRL:  # (the pencil's Ctrl+drag is its only box: a new one there)
            return (0, 0) if side == (0, 0) and self.tool.get() == "select" else None
        if side and side[0] == 1:
            return 1, 0
        return (0, 0) if side == (0, 0) and not hit else None

    def on_motion(self, e):
        hit = self.hit(e.x, e.y)
        # a pencil where a press places a note (not on the keys or bar numbers, not with Ctrl: that's the box)
        inside = e.x >= self.kb_w and e.y >= self.ruler_h
        empty = (SELECT_CURSOR if inside and self.tool.get() == "select" else
                 self.pencil if inside and self.can_place() and not e.state & CTRL else "")
        on_box = self.on_kept_box(self.kept_box(), e, hit)
        if on_box:
            self.canvas.config(cursor=BOX_CURSORS[on_box])
            return self.show_status(e)
        self.canvas.config(cursor={"in": "sb_h_double_arrow", "out": "sb_h_double_arrow", "left": "sb_h_double_arrow",
                                   "right": "sb_h_double_arrow", "tune": "sb_v_double_arrow",
                                   "note": "fleur"}.get(hit and hit[0], empty))
        self.show_status(e)

    def select(self, indices):
        self.sel = set(indices)
        self.redraw()

    def on_press(self, e, place=False):
        """place: a new note even if there's one under the mouse (see on_double)."""
        self.canvas.focus_set()
        self.placed = None
        self.fx.pressed = False  # (Delete is for the notes now)
        if self.fx.sel:  # (the effect points selected aren't any more)
            self.fx.sel = set()
            self.fx.redraw()
        kept, self.box_kept, sel0 = self.kept_box(), None, set(self.sel)
        self.press_was = (sel0, kept)  # (what Esc / Ctrl+Z while the mouse is held go back to: cancel_drag)
        self.drop_drag()
        hit = None if place else self.hit(e.x, e.y)
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, (sorted(sel0), kept and list(kept), len(before)))  # (as before the press)
        on_box = None if place else self.on_kept_box(kept, e, hit)
        if on_box and on_box != (0, 0):  # the kept Select box's side / corner: its notes stretch
            boxes, area = boxes_upright(kept)
            self.box_kept = (boxes, set(self.sel))
            self.drag = {"kind": "stretch", "side": on_box, "area": area, "boxes": boxes, "box": boxes,
                         "before": before, "orig": copy.deepcopy(self.tones), "name": tr("hz.step_stretch")}
            return self.redraw()
        dup = None
        if on_box and e.state & CTRL:  # Ctrl inside: a drag moves a COPY of all it selected (user); let go without
            dup = {"click": hit[1] if hit and hit[0] in ("note", "tune", "left", "right") else None}  # moving =
        whole = bool(on_box) and hit is None  # (grabbed on the box's empty space: Delete takes all it holds)
        if on_box:  # inside it: all it selected moves, held by the first note                    # a Ctrl+click
            hit = ("note", min(self.sel, key=lambda i: self.tones[i]["t"]))
        if hit is None:
            if e.x >= self.kb_w and e.y < self.ruler_h and self.preview_on.get():  # the bar numbers: the play line
                return self.put_play_line(self.beat_at(e.x))
            if e.x < self.kb_w or e.y < self.ruler_h:
                return
            if e.state & CTRL or self.tool.get() == "select":  # a box that selects the notes it touches
                add = e.state & CTRL and self.tool.get() == "select"  # (Ctrl: added to the selection and the
                base = set(self.sel) if add else set()  # boxes kept)
                self.sel = set(base)
                self.drag = {"kind": "box", "from": (self.beat_at(e.x), self.top - (e.y - self.ruler_h) / self.sy),
                             "to": (e.x, e.y), "base": base, "before": before,
                             "more": list(kept or []) if add else [], "shift0": bool(e.state & SHIFT)}
                return self.redraw()
            if not self.can_place():
                return
            # a new note, there at once: it follows the mouse until the button is let go
            tone = {"t": self.snap(self.beat_at(e.x), e), "len": self.last_len, "key": self.key_at(e.y),
                    "cents": 0.0, "id": next_id(self.tones), "to": []}
            self.tones.append(tone)
            self.sel = {len(self.tones) - 1}
            self.drag = {"kind": "new", "i": len(self.tones) - 1, "before": before, "name": tr("hz.step_place")}
            self.sound(tone["key"])
        else:
            kind, i = hit[:2]
            if kind == "note" and e.state & CTRL and not dup:
                return self.select(self.sel ^ {i})
            if i not in self.sel:
                self.sel = {i}
            if kind == "note":  # (the next new note is as long as the one clicked)
                self.last_len = self.tones[i]["len"]
                self.sound([(self.tones[j]["key"], self.tones[j]["t"], self.tones[j]["len"]) for j in self.sel])
            self.drag = {"kind": kind, "i": i, "before": before, "beat": self.beat_at(e.x), "key": self.key_at(e.y),
                         "orig": copy.deepcopy(self.tones), "x": e.x, "y": e.y, "moved": False,
                         "slide": hit[2] if len(hit) > 2 else None, "whole": whole,
                         "name": {"note": tr("hz.step_move"), "in": tr("hz.step_lead"), "out": tr("hz.step_lead"),
                                  "tune": tr("hz.step_tune")}.get(kind, tr("hz.step_length"))}
            if kind == "note" and kept and i in sel0:  # a note the kept box selected: the box goes along
                boxes = boxes_upright(kept)[0]
                # (inside the box a click without dragging keeps the selection and the box: user)
                in_box = boxes_side([self.box_rect(a) for a in kept], e.x, e.y, 0) == (0, 0)
                self.drag.update(box=boxes, inside=bool(on_box) or in_box)
                self.box_kept = (boxes, set(self.sel))
            elif kept and i in sel0:  # an end / the tune line of a note it selected: the box stays where it is
                self.drag["keep_box"] = kept
                self.box_kept = (kept, set(self.sel))
            if dup:
                self.drag.update(dup=dup, name=tr("hz.step_duplicate"))
        self.redraw()

    def on_double(self, e):
        """A double click on a note deletes it, when the button is let go with nothing changed (so a click and then
        a quick drag still moves it). With Select on empty space it pastes the copied notes there (user, like the
        main piano roll). On the note the first click just placed: one more note (every click places one, user).
        Anywhere else, or with Ctrl, it's a press like any other."""
        hit = self.hit(e.x, e.y)
        if (hit and hit[0] in ("note", "tune", "left", "right") and self.tones[hit[1]]["id"] == self.placed
                and not e.state & CTRL and self.tool.get() == "pencil"):
            return self.on_press(e, place=True)
        if hit and hit[0] == "tune" and not e.state & CTRL:  # the red line (tall rows): its tune back to 0 (user)
            return self.reset_tune(hit[1])
        if (self.tool.get() == "select" and hit is None and self.app.hz_clip and not e.state & CTRL
                and e.x >= self.kb_w and e.y >= self.ruler_h and not self.on_kept_box(self.kept_box(), e, hit)):
            self.drop_drag()
            return self.paste_notes(self.snap(self.beat_at(e.x), e))
        self.on_press(e)
        if self.drag and hit and hit[0] in ("note", "tune", "left", "right") and not e.state & CTRL:
            self.drag["double"] = True

    def on_drag(self, e):
        d = self.drag
        if not d:
            return
        if d["kind"] == "box":
            d["mouse"] = (e.x, e.y, e.state)
            self.box_to(d)
            if self.box_timer is None:
                self.box_scroll()
            return
        if d["kind"] == "stretch":
            return self.stretch_to(d, e)
        n = self.tones[d["i"]]
        beat, short = self.beat_at(e.x), self.shortest(e)
        if d["kind"] == "new":
            n["t"], n["key"] = self.snap(beat, e), self.key_at(e.y)
            self.sound(n["key"])
        elif d["kind"] in ("left", "right"):  # the other end stays; the grid lines short of it, and where the end
            was = d["orig"][d["i"]]  # dragged started (off the grid, or a note shorter than a step: user)
            end = was["t"] + was["len"]
            if d["kind"] == "right":
                at = max(self.snap(beat, e), (math.floor(was["t"] / short + 1e-9) + 1) * short)
                n["len"] = (end if abs(beat - end) <= abs(beat - at) else at) - was["t"]
            else:
                at = min(self.snap(beat, e), (math.ceil(end / short - 1e-9) - 1) * short)
                n["t"] = was["t"] if at < 0 or abs(beat - was["t"]) <= abs(beat - at) else at
                n["len"] = end - n["t"]
            self.keep_leads(d["i"], d["orig"])
        elif d["kind"] == "out":
            d["slide"]["out"] = min(max(0.0, n["t"] + n["len"] - self.snap(beat, e)), n["len"])
        elif d["kind"] == "in":
            d["slide"]["in"] = min(max(0.0, self.snap(beat, e) - n["t"]), n["len"])
        elif d["kind"] == "tune":  # the note's own tune: whole cents, and it sticks to the exact tone within
            cents = d["orig"][d["i"]]["cents"] + (d["y"] - e.y) / self.sy * 100  # TUNE_STICK cents (Shift = free)
            if e.state & SHIFT:
                cents = round(cents, 1)
            else:
                cents = 0.0 if abs(cents) <= TUNE_STICK else float(round(cents))
            n["cents"] = max(-TUNE, min(TUNE, cents))
            # the other selected notes move by as much, each from where it was (user): one that would go past the
            # end stops there, and comes back to its own distance as the drag comes back
            moved = n["cents"] - d["orig"][d["i"]]["cents"]
            for j in self.sel - {d["i"]}:
                self.tones[j]["cents"] = max(-TUNE, min(TUNE, round(d["orig"][j]["cents"] + moved, 6)))
        else:  # move every selected note: the one held goes to the grid line nearest to where it's dragged
            if not d["moved"] and abs(e.x - d["x"]) < 4 and abs(e.y - d["y"]) < 4:
                return
            d["moved"] = True
            if d.get("dup") and not d.get("copied"):
                self.copy_moved(d)
                n = self.tones[d["i"]]
            orig = d["orig"]
            held = orig[d["i"]]
            dt = 0.0 if abs(e.x - d["x"]) < 4 else self.snap(held["t"] + beat - d["beat"], e) - held["t"]
            dk = self.key_at(e.y) - d["key"]
            dt = max(dt, -min(orig[i]["t"] for i in self.sel))
            dk = max(-min(orig[i]["key"] for i in self.sel), min(127 - max(orig[i]["key"] for i in self.sel), dk))
            for i in self.sel:
                self.tones[i]["t"], self.tones[i]["key"] = orig[i]["t"] + dt, orig[i]["key"] + dk
            if d.get("box"):
                self.box_kept = ([(b0 + dt, top + dk, b1 + dt, bottom + dk) for b0, top, b1, bottom in d["box"]],
                                 set(self.sel))
            self.sound([(self.tones[j]["key"], self.tones[j]["t"], self.tones[j]["len"]) for j in self.sel])
        self.redraw()
        self.show_status(e)

    def copy_moved(self, d):
        """Ctrl+drag inside the kept Select box, the first move: copies of the selected notes are added (new ids;
        a slide between two of them is copied too) and selected, and they're what moves (the notes stay)."""
        old = sorted(self.sel)
        ids, first = {}, len(self.tones)
        for k, i in enumerate(old):
            ids[self.tones[i]["id"]] = next_id(self.tones) + k
        for i in old:
            n = copy.deepcopy(self.tones[i])
            n["id"] = ids[n["id"]]
            n["to"] = [dict(s, id=ids[s["id"]]) for s in n["to"] if s["id"] in ids]
            self.tones.append(n)
        d["i"] = first + old.index(d["i"])
        d["orig"] = copy.deepcopy(self.tones)
        d["copied"] = True
        self.sel = set(range(first, len(self.tones)))

    def copy_notes(self):
        """Ctrl+C: the effect points selected, else the notes selected (app.hz_clip, kept for any Hz bass). The
        last one copied is what Ctrl+V pastes."""
        if self.fx.copy_points():
            self.app.hz_clip = None
        elif self.sel:
            self.app.hz_clip = copy.deepcopy([self.tones[i] for i in sorted(self.sel)])
            self.fx.clip = None

    def play_line_beat(self):
        """Where Ctrl+V pastes notes: the preview's play line (preview on), else the main window's play line;
        on the grid line nearest it."""
        p = self.preview
        if self.preview_on.get() and p.ev is not None:
            beat = p.play_beat()
        else:
            sh = self.target()
            beat = self.app.playhead - (left_edge(sh) if sh is not None else self.app.hz_start or 0.0)
        sb = self.snap_beats()
        return max(0.0, round(beat / sb) * sb if sb else beat)

    def paste_notes(self, at):
        """The copied notes added with the first one starting at beat `at` (keys stay; new ids, a slide between
        two of them comes along) and selected: one undo step. False when there's nothing to paste."""
        clip = self.app.hz_clip
        if not clip or not self.can_place():
            return False
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        start, base, first = min(n["t"] for n in clip), next_id(self.tones), len(self.tones)
        ids = {n["id"]: base + k for k, n in enumerate(clip)}
        for n in copy.deepcopy(clip):
            n["id"], n["t"] = ids[n["id"]], n["t"] + at - start
            n["to"] = [dict(s, id=ids[s["id"]]) for s in n["to"] if s["id"] in ids]
            self.tones.append(n)
        self.sel = set(range(first, len(self.tones)))
        self.commit(tr("hz.step_paste"), before)
        return True

    def keep_leads(self, i, orig):
        """Note i's end dragged (orig = the notes before the drag): the dots of its slides stay where they were (as
        far as the note reaches)."""
        n, was = self.tones[i], orig[i]
        for s, s0 in zip(n["to"], was["to"]):  # lead out: counted back from the note's end
            s["out"] = min(max(0.0, s0["out"] + (n["t"] + n["len"]) - (was["t"] + was["len"])), n["len"])
        for m, m0 in zip(self.tones, orig):  # lead in: counted from the note's start
            for s, s0 in zip(m["to"], m0["to"]):
                if s["id"] == n["id"]:
                    s["in"] = min(max(0.0, s0["in"] - (n["t"] - was["t"])), n["len"])

    def on_release(self, e):
        d = self.drag
        self.drop_drag()
        if not d:
            return
        if d["kind"] == "box":
            if self.box_timer:
                self.after_cancel(self.box_timer)
                self.box_timer = None
            if self.box_area(d):
                self.box_pick(d)
            if self.box_area(d) or d["more"] and e.state & CTRL:
                self.box_kept = (d["more"] + [self.box_area(d)] if self.box_area(d) else d["more"], set(self.sel))
            elif (not e.state & CTRL and self.tool.get() == "select"
                    and self.preview_on.get()):  # a click, not a drag: the play line goes there
                self.put_play_line(self.snap(d["from"][0], e))
            return self.redraw()
        if d.get("dup") and not d["moved"] and d["dup"]["click"] is not None:  # a Ctrl+click: in / out
            self.sel = set(self.sel) ^ {d["dup"]["click"]}
            return self.redraw()
        if d.get("double") and self.tones == d["before"]:
            self.sel = {d["i"]}
            return self.delete_selected()
        if d["kind"] == "note" and not d["moved"] and len(self.sel) > 1 and not d.get("inside"):
            self.sel = {d["i"]}  # one of several clicked without dragging: just that one
        if d["kind"] in ("left", "right"):
            self.last_len = self.tones[d["i"]]["len"]
        placed = self.tones[d["i"]]["id"] if d["kind"] == "new" else None
        if d["kind"] == "stretch":  # the box goes back to its size, the notes stay longer / shorter (user)
            box = d["boxes"]
        else:
            box = (self.box_kept or (None,))[0] if d.get("box") and (d.get("inside") or d["moved"]) else None
        if self.tones != d["before"]:
            self.commit(d["name"], d["before"])
        self.placed = placed
        if box:  # (the notes were sorted: the same ones, numbered anew)
            self.box_kept = (box, set(self.sel))
        elif d.get("keep_box"):
            self.box_kept = (d["keep_box"], set(self.sel))
        self.redraw()

    def on_middle(self, e):
        """A middle click (not a drag: that moves the view) on a note marks one end of a slide (a full red dot); a
        second one on another note makes the slide between the two spots: it's theirs alone, whatever other notes
        and slides there are. On a dot of a slide: that slide goes. Anywhere else: the mark goes."""
        x, y = self.pan[:2]
        if abs(e.x - x) >= 4 or abs(e.y - y) >= 4 or not self.app.hz_line.get():
            return
        self.slide_mark(e, self.hit(e.x, e.y))

    def slide_mark(self, e, hit):
        """One end of a slide at e on note hit (on_middle; the right-click menu's Start / End a slide here)."""
        before = copy.deepcopy(self.tones)
        first = next((n for n in self.tones if self.pending and n["id"] == self.pending[0]), None)
        if hit and hit[0] in ("in", "out"):
            for n in self.tones:
                n["to"] = [s for s in n["to"] if s is not hit[2]]
            self.pending = None
        elif hit:
            n = self.tones[hit[1]]
            at = min(max(self.snap(self.beat_at(e.x), e), n["t"]), n["t"] + n["len"])
            (a, xa), (b, xb) = sorted([(first or n, self.mark_beat(first) if first else at), (n, at)],
                                      key=lambda v: v[0]["t"])
            if first is None or first is n or not can_slide(a, b):  # the first spot (or a new first spot)
                self.pending = (n["id"], at - n["t"])
                return self.redraw()
            s = self.link(a, b)
            if s is None:
                s = {"id": b["id"], "out": 0.0, "in": 0.0}
                a["to"].append(s)
            s["out"] = min(max(0.0, a["t"] + a["len"] - xa), a["len"])
            s["in"] = min(max(0.0, xb - b["t"]), b["len"])
            self.pending = None
        else:
            self.pending = None
        if self.tones != before:
            self.commit(tr("hz.step_lead"), before)
        else:
            self.redraw()

    def mark_beat(self, n):
        """Where a slide's first mark (self.pending) on note n is: as far into the note as it was put, or the
        note's end when the note got shorter."""
        return n["t"] + min(self.pending[1], n["len"])

    def toggle_tool(self, e=None):
        """Double right click: Select <-> Pencil."""
        if e is not None and self.drag:  # (the left button held: right clicks do nothing)
            return
        if self.menu_wait:
            self.after_cancel(self.menu_wait)
            self.menu_wait = None
        self.tool.set("pencil" if self.tool.get() == "select" else "select")
        if e is not None:
            self.on_motion(e)

    def on_menu(self, e):
        """Right click: slides between the selected notes (see pairs), or take them away. On a note (its red
        line): its tune, typed. On empty space the menu waits for the double click time first (a double right
        click switches the tool), and there's none when there's nothing to pick. None while the left button is held
        (user: the menu took its let-go, and the window and the piano roll no longer agreed)."""
        if self.drag:
            return
        hit = self.hit(e.x, e.y)
        kept = self.kept_box() if not (hit and hit[0] in ("in", "out")) else None
        # inside the kept Select boxes: the menu for all they selected; just one note: its own menu, anywhere in
        # the boxes (user, like the main piano roll)
        if kept and boxes_side([self.box_rect(a) for a in kept], e.x, e.y, 0) == (0, 0):
            one = ("note", next(iter(self.sel))) if len(self.sel) == 1 else None
            show = (lambda: self.show_menu(e, one)) if one else (lambda: self.show_box_menu(e))
            if hit:
                return show()
            if self.menu_wait:  # (empty space: a double right click still switches the tool)
                self.after_cancel(self.menu_wait)
            self.menu_wait = self.after(DOUBLE_MS, show)
            return
        if kept:  # a right click outside the boxes drops them (user), the selection stays
            self.box_kept = None
            self.redraw()
        if not hit:
            if self.menu_wait:
                self.after_cancel(self.menu_wait)
            self.menu_wait = self.after(DOUBLE_MS, lambda: self.show_menu(e, None)) if self.pairs() else None
            return
        self.show_menu(e, hit)

    def show_box_menu(self, e):
        """Right-click inside the kept Select boxes with several notes selected: only what works on all of them at
        once (user asked): tune, own Auto threshold, slides, delete."""
        self.menu_wait = None
        if len(self.sel) < 2:
            return
        first = min(self.sel)
        hz = (self.target() or {}).get("hz") or {}
        pairs = self.pairs()
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("hz.box_selected", n=len(self.sel)), state="disabled")
        menu.add_separator()
        menu.add_command(label=tr("hz.box_tune"), command=lambda: self.type_tune(first))
        if hz.get("auto") is not None:
            menu.add_command(label=tr("hz.box_auto"), command=lambda: self.type_auto(first))
            if any("auto" in self.tones[j] for j in self.sel):
                menu.add_command(label=tr("hz.auto_shared", cents=f"{hz['auto']:g}"),
                                 command=lambda: self.set_auto(first, None))
        self.gate_items(menu, first)
        if pairs and all(self.link(a, b) for a, b in pairs):
            menu.add_command(label=tr("hz.slide_remove"), command=lambda: self.set_slide(False))
        else:
            menu.add_command(label=tr("hz.slide_add"), command=lambda: self.set_slide(True),
                             state="normal" if pairs else "disabled")
        menu.add_separator()
        menu.add_command(label=tr("hz.box_delete", n=len(self.sel)), accelerator="Del", command=self.delete_selected)
        menu.tk_popup(e.x_root, e.y_root)

    def show_menu(self, e, hit):
        self.menu_wait = None
        pairs = self.pairs()
        menu = tk.Menu(self, tearoff=0)
        if hit and hit[0] not in ("in", "out"):
            menu.add_command(label=tr("hz.tune_type", cents=f"{self.tones[hit[1]]['cents']:+g}"),
                             command=lambda: self.type_tune(hit[1]))
            hz = (self.target() or {}).get("hz") or {}
            if hz.get("auto") is not None:  # Auto gates: the note's own threshold
                n = self.tones[hit[1]]
                menu.add_command(label=tr("hz.auto_own", cents=f"{n.get('auto', hz['auto']):g}"),
                                 command=lambda: self.type_auto(hit[1]))
                picked = self.sel if hit[1] in self.sel else {hit[1]}
                if any("auto" in self.tones[j] for j in picked):
                    menu.add_command(label=tr("hz.auto_shared", cents=f"{hz['auto']:g}"),
                                     command=lambda: self.set_auto(hit[1], None))
            self.gate_items(menu, hit[1])
            if self.app.hz_line.get():  # a slide's dots, like two middle-clicks
                n = self.tones[hit[1]]
                first = next((m for m in self.tones if self.pending and m["id"] == self.pending[0]), None)
                ends = first is not None and first is not n and (can_slide(first, n) or can_slide(n, first))
                menu.add_command(label=tr("hz.slide_end" if ends else "hz.slide_start"),
                                 command=lambda: self.slide_mark(e, hit))
        if hit and hit[0] in ("in", "out"):  # a slide's dot: that slide goes, both its dots
            menu.add_command(label=tr("hz.slide_delete"), command=lambda: self.delete_slide(hit[2]))
        if len(self.sel) >= 2:  # (only with two or more selected, user)
            if menu.index("end") is not None and menu.type("end") != "separator":
                menu.add_separator()
            if pairs and all(self.link(a, b) for a, b in pairs):
                menu.add_command(label=tr("hz.slide_remove"), command=lambda: self.set_slide(False))
            else:
                menu.add_command(label=tr("hz.slide_add"), command=lambda: self.set_slide(True),
                                 state="normal" if pairs else "disabled")
        if menu.index("end") is not None and menu.type("end") == "separator":  # (the gates' group ends with one)
            menu.delete("end")
        if menu.index("end") is not None:
            menu.tk_popup(e.x_root, e.y_root)

    def delete_slide(self, s):
        """Slide s goes (the dots on both its notes)."""
        before = copy.deepcopy(self.tones)
        for n in self.tones:
            n["to"] = [t for t in n["to"] if t is not s]
        if self.tones != before:
            self.commit(tr("hz.step_lead"), before)

    def set_slide(self, on):
        """Slides between the selected notes (see pairs): each a quarter of its two notes long to start with (its
        dots can then be dragged); slides that are there stay as they are. Off = those slides go."""
        before = copy.deepcopy(self.tones)
        for a, b in self.pairs():
            if not on:
                a["to"] = [s for s in a["to"] if s["id"] != b["id"]]
            elif not self.link(a, b):
                a["to"].append({"id": b["id"], "out": a["len"] / 4, "in": b["len"] / 4})
        if self.tones != before:
            self.commit(tr("hz.step_lead"), before)

    def on_delete(self):
        """Delete: what the mouse holds (delete_dragged); else the effect points selected, or with none and the
        effects pane pressed last its highlighted effect; else the selected notes."""
        if self.drag:
            self.delete_dragged()
        elif not self.fx.delete_key():
            self.delete_selected()
        return "break"

    def delete_dragged(self):
        """Delete while the mouse is held (like the main piano roll): the note grabbed goes, or all the selected
        notes when the Select box is grabbed on its empty space or stretched, or copies are moved; a slide's dot:
        that slide. A Select box being drawn: nothing (user, like Domino). The other notes stay as they are now; the drag ends (one undo step) and
        the pointer is the one for where the mouse is. A note still being placed just goes (no step)."""
        d = self.drag
        if d["kind"] == "box":  # (nothing selected by it yet: user, like Domino)
            return
        if d["kind"] == "new":
            return self.cancel_drag()
        self.end_drag()
        if d["kind"] in ("in", "out"):
            for n in self.tones:
                n["to"] = [s for s in n["to"] if s is not d["slide"]]
            name = tr("hz.step_lead")
        else:
            whole = d["kind"] in ("box", "stretch") or d.get("dup") or d.get("whole")
            self.remove_notes(set(self.sel) if whole else {d["i"]})
            name = tr("hz.step_delete")
        if self.tones != d["before"]:
            self.commit(name, d["before"])
        else:
            self.redraw()
        self.point_again()

    def remove_notes(self, gone):
        """The notes numbered `gone` taken out, the slides to them too; the other selected notes stay selected."""
        keep = [n for i, n in enumerate(self.tones) if i in self.sel and i not in gone]
        self.tones = [n for i, n in enumerate(self.tones) if i not in gone]
        left = {n["id"] for n in self.tones}
        for n in self.tones:
            n["to"] = [s for s in n["to"] if s["id"] in left]
        self.sel = {i for i, n in enumerate(self.tones) if any(n is k for k in keep)}

    def delete_selected(self):
        if self.sel:
            before = copy.deepcopy(self.tones)
            self.sel_before = (before, self.sel_state())
            self.remove_notes(set(self.sel))
            self.commit(tr("hz.step_delete"), before)

    def drop_drag(self):
        self.drag = None
        self.sound(None)

    def end_drag(self):
        """The drag ends now, though the mouse is still held (a key pressed): its let-go will do nothing."""
        if self.box_timer:
            self.after_cancel(self.box_timer)
            self.box_timer = None
        self.drop_drag()

    def held_fx(self):
        """(Ctrl+Z) What puts back what the mouse holds in an effects pane (here or the synth window's), on a synth
        knob or in its Effects list; None when nothing is held there."""
        syn = self.synth_win
        if self.layers.held is not None:  # (a layers list row held: let go of, nothing moved)
            return self.layers.drop_held
        for pane in (self.fx, syn.fx if syn else None):
            if pane is not None and pane.drag:
                return pane.cancel_drag
        if syn and syn.turning is not None:
            return syn.cancel_turn
        if syn and syn.macro_held:  # (a macro's name being dragged: the drag ends, nothing linked)
            return syn.macro_cancel
        if syn and syn.list_held:  # (an effect held in the Effects tab's list)
            return syn.cancel_list
        return None

    def cancel_drag(self):
        """Esc / Ctrl+Z while the mouse is held: the drag is called off. The notes, the selection and the Select
        boxes go back to how they were at the press (user); no undo step. False when there's no drag. A Select box
        being drawn: nothing (user, like Domino)."""
        d = self.drag
        if not d:
            return False
        if d["kind"] == "box":
            return True
        self.end_drag()
        self.tones = copy.deepcopy(d["before"])
        sel, kept = self.press_was
        self.sel = set(sel)
        self.box_kept = (kept, set(sel)) if kept else None
        self.sel_before = None
        self.redraw()
        self.point_again()
        return True

    def on_escape(self):
        """Esc: a drag going on is called off (cancel_drag; in the effects pane too, like Ctrl+Z), else nothing is
        selected any more."""
        held = None if self.drag else self.held_fx()
        if held:
            held()
        elif not self.cancel_drag():
            self.select(())
        return "break"

    def point_again(self):
        """The mouse pointer as it should be where the mouse is now (a key ended a drag: no move tells it)."""
        c = self.canvas
        self.on_motion(SimpleNamespace(x=c.winfo_pointerx() - c.winfo_rootx(),
                                       y=c.winfo_pointery() - c.winfo_rooty(), state=0))

    def remember(self, e):
        if e.widget is self:
            self.app.hz_pos = self.geometry()
