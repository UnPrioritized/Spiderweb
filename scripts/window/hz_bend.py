"""The Hz bass window's Bend tool (B; user, 2026-10-10): each note's own bend line (tone["bend"] = [[u, keys], ...],
u 0..1 along the note, so it stretches with it; heard on top of everything else, any distance (not the Range knob):
notes/hz_lines.note_bend), edited right on the note.

A click on a note (or on its bend line, wherever it has gone) adds a point there and holds it; a point dragged moves
(between its neighbours; the end points only up / down). Points snap to the grid and to whole keys (Shift = free:
any tick, any cent). Right-click a point (or Delete while holding it) = it goes; a line back at 0 everywhere = no
bend. Each change is one undo step. Empty space works exactly like the Select tool (user, 2026-10-10: box,
Ctrl+box adds, a click unselects, double-click pastes; hz_mouse checks the tool for "select" / "bend"). A
right-click on a note's bend line or curve square = that note's menu (on a point: the point goes). A note's menu
(any tool) has a Bend submenu: Copy bend, Paste bend onto it / the selected notes, Clear bend, and ready shapes
(Scoop up, Fall off, Wobble: shaped) that replace the bend.
A small square sits half way along each piece between two points that differ: dragged up / down it curves the
piece (like a slide's square: sticks to straight within BEND_STICK px, Shift = free; hollow = straight), stored
as the first point's third number (hz_lines.bent_part); double-click it = straight again."""

import copy
import tkinter as tk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import fmt
from notes.hzbass import (NOTE_BEND_MOST, ON_POINT, SLIDE_BEND, bend_of, bends, line_at, merge_wobbles, note_bend, pitch,
                          depth_most, wobble_edges, wobble_points)
from roll.roll_shared import SHIFT
from window import look

NEAR = 6  # px: a press this near a bend point grabs it
WOBBLE_SPOTS = 4000  # bend_line: spots along a wobbling note at most
EDGE_GAP = 1e-9  # beats: bend_line reads a wobble's jump this far before / after it
DOT_GAP = 4  # px: bend_points leaves out a point this near the one shown before (zoom in to see it)
LINE_NEAR = 4  # px: ... this near a note's bend line adds a point to it
BEND_STICK = 5  # px: a curve square this near straight sticks to it (Shift = free), as a slide's
SQUARE_ROOM = 20  # px: a piece narrower than this gets no square (its points' grab areas would cover it)
BEND = look.HZ_BEND
BEND_TOOLS = ("bend", "sketch")  # the tools that show and edit the bend points (Sketch: hz_sketch.py)
# the drags bend_drag / bend_release take (sketch* + wobble*: the Sketch tool's, hz_sketch.py)
BEND_DRAGS = ("bendpt", "bendcurve", "sketchpiece", "sketchend", "wobbledepth", "wobblerate", "wobblespeed",
              "wobbleend")
WOBBLE_DRAGS = ("wobbledepth", "wobblerate", "wobblespeed")  # (several selected notes: theirs change alike, stage D)


class HzBend:
    def bending(self):
        return self.tool.get() in BEND_TOOLS

    def bend_most(self):
        """Keys a note's bend goes at most each way (not the Pitch box's Range: that's the Pitch line's, user
        2026-10-28)."""
        return NOTE_BEND_MOST

    def bent_pitch(self, n, beat, most=None):
        """Note n's pitch at beat with its own bend line (keys)."""
        got = note_bend(n, np.array([beat], float), self.bend_most() if most is None else most)
        return pitch(n) + (0.0 if got is None else float(got[0]))

    def bend_line(self, n, most):
        """(beats, keys) along note n's bend line for drawing: evenly spread spots plus every point, a jump (two
        points at one spot) drawn straight up / down (one spot per point, each with its own value). With a wobble
        the line is drawn EXACTLY (user 2026-10-28: it's the shape asked for, the red line is how near the sound gets):
        a spot every 2 px (WOBBLE_SPOTS at most) + a spot on each side of every top / corner / jump (wobble_edges), so
        tops stay round, corners sharp, jumps upright. Only the part in view is worked out that finely (hunt
        2026-10-28: a long fast one zoomed in was rough); each note's line is remembered until it or the view changes
        (many wobbling notes lagged)."""
        pts = n.get("bend") or ()
        lo, hi, count = n["t"], n["t"] + n["len"], 49
        # (a curved piece too, user 2026-11-13: 49 spots over a long / zoomed-in note drew its curve as a few straight
        # strokes off the smooth red line; straight pieces need only their points)
        if n.get("wobble") or any(bend_of(p) for p in pts[:-1]):
            pad = 4 / max(self.sx, 1e-9)
            lo = max(lo, self.beat_at(self.kb_w) - pad)
            hi = min(hi, self.beat_at(self.canvas.winfo_width()) + pad)
            if hi <= lo:
                lo, hi = n["t"], n["t"] + n["len"]
            count = int(min(WOBBLE_SPOTS, max(49, (hi - lo) * self.sx / 2)))
        key = (n["t"], n["len"], n["key"], n.get("cents"), repr(pts), repr(n.get("wobble")), repr(n.get("bend_at")),
               most, lo, hi, count)
        cache = self.__dict__.setdefault("_bend_lines", {})
        if key in cache:
            return cache[key]
        at = np.array([n["t"] + p[0] * n["len"] for p in pts], float)
        edges = np.array(wobble_edges(n, WOBBLE_SPOTS, lo, hi) if n.get("wobble") else [], float)
        even = np.linspace(lo, hi, count)
        fixed = np.sort(np.concatenate([at, edges]))  # (spots a hair from a point / edge left out: it's drawn exactly)
        if len(fixed):
            k = np.clip(np.searchsorted(fixed, even), 1, len(fixed)) - 1
            near = np.minimum(np.abs(even - fixed[k]), np.abs(even - fixed[np.minimum(k + 1, len(fixed) - 1)]))
            even = even[near > 1e-7]
        # (beat, order, point number): on one beat the line arriving, then the points in order, then the line leaving
        beats = np.concatenate([even, at, edges, edges])
        order = np.concatenate([np.ones(len(even)), np.ones(len(at)), np.zeros(len(edges)), np.full(len(edges), 2.0)])
        number = np.concatenate([np.full(len(even), -1.0), np.arange(len(at), dtype=float),
                                 np.full(2 * len(edges), -1.0)])
        keys = np.concatenate([note_bend(n, even, most) if len(even) else [],
                               np.clip([p[1] for p in pts], -most, most) if pts else [],
                               note_bend(n, edges - EDGE_GAP, most) if len(edges) else [],
                               note_bend(n, edges + EDGE_GAP, most) if len(edges) else []])
        sort = np.lexsort((number, order, beats))
        if len(cache) > 1000:
            cache.clear()
        got = cache[key] = (beats[sort], pitch(n) + keys[sort])
        return got

    def bend_points(self):
        """[(x, y, tone number, point number)]: the bend points of the notes in view (Bend tool only). Points closer
        sideways than DOT_GAP px to the one shown before are left out (shown when zoomed in; hunt 2026-10-28: a baked
        fast wobble's thousands of dots lagged), never a note's ends, the point held or the other half of a jump
        shown."""
        if not self.bending():
            return []
        most, w, out = self.bend_most(), self.canvas.winfo_width(), []
        gap = DOT_GAP * self.s
        held = self.drag and self.drag.get("kind") == "bendpt" and (self.drag["i"], self.drag["j"])
        for i, n in enumerate(self.tones):
            # (Sketch: a plain note's ends too, bend_press makes its line when one is grabbed)
            pts = n.get("bend") or (self.sketching() and ([0.0, 0.0], [1.0, 0.0]))
            if not pts or self.x_of(n["t"] + n["len"]) < self.kb_w or self.x_of(n["t"]) > w:
                continue
            xs = self.x_of(n["t"] + np.array([p[0] for p in pts]) * n["len"])
            ys = self.pitch_y(pitch(n) + np.clip([p[1] for p in pts], -most, most))
            last = None  # (the point shown before: its number, x)
            for j, (x, y) in enumerate(zip(xs.tolist(), ys.tolist())):
                end = j in (0, len(pts) - 1) or held == (i, j)
                jump = last is not None and last[0] == j - 1 and pts[j][0] == pts[j - 1][0]  # (its partner's shown)
                if not end and not jump and (last is not None and abs(x - last[1]) < gap
                                             or not self.kb_w - gap <= x <= w + gap):
                    continue
                out.append((x, y, i, j))
                last = (j, x)
        return out

    def bend_squares(self):
        """[(x, y, tone number, piece number, keys at its start, at its end)]: the curve squares of the notes in view,
        half way along each piece whose two points differ (an S: a quarter of the way). None on a piece bend_most
        cuts (a point past it: the square would sit where the clipped line isn't, and a nudge would bend it a lot).
        Sketch tool (user 2026-10-28): only on curved pieces, on every piece while Alt is held (sketch_alt)."""
        if not self.bending():
            return []
        out = []
        for i, j in {(i, j) for _, _, i, j in self.bend_points()}:
            pts = self.tones[i].get("bend") or ()
            if self.sketching() and not self.sketch_alt and not (j + 1 < len(pts) and len(pts[j]) > 2):
                continue
            q = self.piece_square(i, j)
            if q:
                out.append(q)
        return out

    def piece_square(self, i, j, room=True):
        """(x, y, i, j, keys at its start, at its end): where piece j of note i's bend line has its curve square, or
        None when it has none (no such piece, its two points alike, too narrow (room=False: any width, Sketch's
        Alt+drag, hunt 2026-10-28), past bend_most)."""
        n = self.tones[i]
        pts = n.get("bend") or ()
        if j + 1 >= len(pts):
            return None
        (u0, k0), (u1, k1) = pts[j][:2], pts[j + 1][:2]
        x0, x1 = self.x_of(n["t"] + u0 * n["len"]), self.x_of(n["t"] + u1 * n["len"])
        most = self.bend_most()
        if (abs(k1 - k0) < 1e-9 or room and x1 - x0 < SQUARE_ROOM * self.s or u1 <= u0
                or max(abs(k0), abs(k1)) > most + 1e-9):
            return None
        at = 0.25 if len(pts[j]) > 3 and pts[j][3] == "s" else 0.5  # (an S: its first half's middle, like a slide)
        k = float(line_at(pts, u0 + (u1 - u0) * at))
        return x0 + (x1 - x0) * at, self.pitch_y(pitch(n) + k), i, j, k0, k1

    def bend_hit(self, x, y):
        """What the Bend tool would take at (x, y): ("point", tone, point number), ("curve", tone, piece number) a
        curve square, ("slide",) a slide's dot or square (those win over a note: the usual tools take them), ("note",
        tone) on a note or on its bend line, ("end", tone) Sketch's ">" after a note's end (only where nothing
        else is: the next note's start, a slide's dot just after the end come first; hunt 2026-10-28), ("wobble", tone,
        "a" / "b" / "line") Sketch's depth line over the picked piece (first: it sits above the notes), or None."""
        which = self.depth_hit(x, y) if self.sketching() else None
        if which:
            return "wobble", self.picked_piece()[0], which
        r = NEAR * self.s
        slides = [max(abs(x - dx), abs(y - dy)) for dx, dy, *_ in self.dots() + self.handles()]
        for px, py, i, j in reversed(self.bend_points()):
            if abs(x - px) <= r and abs(y - py) <= r:  # (a slide's dot nearer still wins: one just after a note's
                if min(slides, default=r + 1) < max(abs(x - px), abs(y - py)):  # end, hunt 2026-10-28)
                    return ("slide",)
                return "point", i, j
        for sx, sy, i, j, *_ in self.bend_squares():
            if abs(x - sx) <= r and abs(y - sy) <= r:
                return "curve", i, j
        r = 6 * self.s  # (as hz_mouse.hit grabs them)
        if any(abs(x - dx) <= r and abs(y - dy) <= r for dx, dy, *_ in self.dots() + self.handles()):
            return ("slide",)
        if x < self.kb_w or y < self.ruler_h:
            return None
        most, beat = self.bend_most(), self.beat_at(x)
        reach = self.sketch_reach()
        for i in range(len(self.tones) - 1, -1, -1):
            n = self.tones[i]
            x0, x1, y0 = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            if self.sketching():  # (only the line counts, as drawn: a jump's upright stroke too; the row isn't shown)
                if x0 - reach <= x <= max(x1, x0 + 2) + reach and self.sketch_near(n, x, y, reach, most):
                    return "note", i
                continue
            if not x0 - 1 <= x <= max(x1, x0 + 2) + 1:
                continue
            if y0 <= y < y0 + self.sy or bends(n) and abs(y - self.pitch_y(self.bent_pitch(n, beat, most))) <= \
                    LINE_NEAR * self.s:
                return "note", i
        i = self.sketch_end_hit(x, y) if self.sketching() else None
        return None if i is None else ("end", i)

    def bend_spot(self, n, e):
        """[u, keys] at the mouse for note n: the grid line nearest it (in the note) and whole keys (Shift: any
        tick, keys to the cent), no further than bend_most."""
        u = (self.snap(self.beat_at(e.x), e) - n["t"]) / max(n["len"], 1e-12)
        k = self.top + 0.5 - (e.y - self.ruler_h) / self.sy - pitch(n)  # (pitch_y turned round)
        k = round(k, 2) if e.state & SHIFT else float(round(k))
        most = self.bend_most()
        return [min(1.0, max(0.0, u)), max(-most, min(most, k))]

    def bend_press(self, e):
        """A press with the Bend tool: on a point it's held; on a note a new point is put there and held (a note
        without a bend gets a line at 0 from end to end first). False on empty space (a Select box) and on a slide's
        dot / square (they're grabbed as with the other tools)."""
        if not self.bending():
            return False
        hit = self.bend_hit(e.x, e.y)
        if hit is None or hit[0] == "slide":
            return False
        kept = self.kept_box()
        self.press_was = (set(self.sel), kept)  # (what Esc / Ctrl+Z while held go back to: cancel_drag)
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, (sorted(self.sel), kept and list(kept), len(before), self.sketch_pick))
        i, n = hit[1], self.tones[hit[1]]
        if not (self.sketching() and hit[0] in ("wobble", "note") and i in self.sel):  # (Sketch: one of several
            self.sel, self.box_kept = {i}, None  # selected notes pressed: they stay selected, their wobbles change
            # alike, stage D 2026-11-13)
        if hit[0] == "wobble":  # (Sketch: the picked piece's wobble depth, sketch_depth_to)
            return self.sketch_depth(e, i, hit[2], before)
        if hit[0] == "note" and self.sketching():  # (Sketch: the piece is held, sketch_piece, Alt: curved,
            return self.sketch_piece(e, i, before)  # sketch_curve; a point is added by a double-click, sketch_double)
        if hit[0] == "end":  # (Sketch's ">": the note's length, sketch_end_to)
            self.drag = {"kind": "sketchend", "i": i, "before": before, "orig": copy.deepcopy(self.tones), "x": e.x,
                         "y": e.y, "moved": False, "name": tr("hz.step_length")}
            self.redraw()
            return True
        if hit[0] == "point" and not n.get("bend"):  # (Sketch: a plain note's end: its line made, at 0)
            n["bend"] = [[0.0, 0.0], [1.0, 0.0]]
        if hit[0] == "curve":  # (where the square was grabbed: it moves as far as the mouse from there)
            square = next(q for q in self.bend_squares() if q[2:4] == (i, hit[2]))
            self.drag = {"kind": "bendcurve", "i": i, "j": hit[2], "before": before, "x": e.x, "y": e.y,
                         "moved": False, "square": square, "name": tr("hz.step_note_bend_curve")}
            self.redraw()
            return True
        if hit[0] == "point":
            j = hit[2]
        else:
            u, k = self.bend_spot(n, e)
            pts = [list(p) for p in n.get("bend") or ([0.0, 0.0], [1.0, 0.0])]
            j = next((j for j, p in enumerate(pts) if abs(p[0] - u) < 1e-9), None)
            if j is None:  # (after the points at the same spot: the new one is the last there)
                j = sum(p[0] <= u for p in pts)
                pts.insert(j, [u, k])  # (a wobble across it rides it: one wobble, stage D 2026-11-13)
            else:
                pts[j][1] = k
            n["bend"] = pts
        p = n["bend"][j]
        self.drag = {"kind": "bendpt", "i": i, "j": j, "before": before, "x": e.x, "y": e.y,
                     "moved": hit[0] == "note", "pinned": p[0] in (0.0, 1.0), "name": tr("hz.step_note_bend"),
                     "wob": copy.deepcopy(n.get("wobble") or []), "old": p[0]}
        self.redraw()
        self.show_status(e)
        return True

    def bend_drag(self, e):
        d = self.drag
        if not d or d["kind"] not in BEND_DRAGS:
            return False
        if not d["moved"] and abs(e.x - d["x"]) < 4 and abs(e.y - d["y"]) < 4:
            return True  # (a click with a wobble changes nothing)
        d["moved"] = True
        n = self.tones[d["i"]]
        if d["kind"] == "sketchpiece":
            return self.sketch_piece_to(d, n, e)
        if d["kind"] == "sketchend":
            return self.sketch_end_to(d, n, e)
        if d["kind"] == "wobbledepth":
            return self.sketch_depth_to(d, n, e)
        if d["kind"] == "wobblerate":
            return self.sketch_rate_to(d, n, e)
        if d["kind"] == "wobblespeed":
            return self.sketch_speed_to(d, n, e)
        if d["kind"] == "wobbleend":
            return self.sketch_post_to(d, n, e)
        if d["kind"] == "bendcurve":  # how far from the piece's first point to its second the square is dragged
            _, sy, _, j, k0, k1 = d["square"]
            y = sy + e.y - d["y"]
            part = (self.top + 0.5 - (y - self.ruler_h) / self.sy - pitch(n) - k0) / (k1 - k0)  # (pitch_y turned round)
            p = n["bend"][j]
            s = d.setdefault("s", len(p) > 3 and p[3] == "s")  # (an S stays one: its square a quarter of the way,
            at = 0.25 if s else 0.5                              # where it's (1 + b) / 4 up)
            straight = self.pitch_y(pitch(n) + k0 + (k1 - k0) * at)
            del p[2:]
            if abs(y - straight) > BEND_STICK * self.s or e.state & SHIFT:  # (else it sticks to straight)
                b = min(SLIDE_BEND, max(-SLIDE_BEND, (4.0 if s else 2.0) * part - 1.0))
                if b:
                    p += [b, "s"] if s else [b]
            self.redraw()
            return True
        pts, j = n["bend"], d["j"]
        u, k = self.bend_spot(n, e)
        if not d["pinned"]:  # (between its neighbours; two at one spot = a jump)
            lo = pts[j - 1][0] if j > 0 else 0.0
            hi = pts[j + 1][0] if j + 1 < len(pts) else 1.0
            pts[j][0] = min(hi, max(lo, u))
            if "wob" in d:  # (a wobble end on it follows: only its own two pieces', hunt 2026-10-28)
                follow_wobble(n, d["wob"], j, d["old"])
        pts[j][1] = k
        self.redraw()
        self.show_status(e)
        return True

    def bend_release(self, e):
        d = self.drag
        if not d or d["kind"] not in BEND_DRAGS:
            return False
        self.drop_drag()
        if d["kind"] == "sketchpiece" and not d["moved"]:  # (Sketch: a click picks the piece: its depth line shows;
            pts = d["orig"] or ([0.0, 0.0], [1.0, 0.0])  # the whole wobble over it when it has one; Shift: the
            j = d["j"] or 0                                 # picked stretch grows to it, stage D)
            if self.sketch_widen(d, pts[j][0], pts[j + 1][0], e.state & SHIFT):
                return True
        if d["kind"] == "wobblerate" and not d["moved"]:  # (Sketch: the speed post clicked: its speed strip)
            self.speed_toggle()
        if d["kind"] in WOBBLE_DRAGS + ("wobbleend",):
            if self.tones[d["i"]] == d["before"][d["i"]]:  # (back where it started: the other selected notes too, no
                self.tones[:] = copy.deepcopy(d["before"])  # undo step; hunt 2026-11-13: theirs were replaced)
            elif d["kind"] != "wobbleend":
                self.spread_pick(d["i"], d["at"])  # (several selected notes: theirs the same)
            else:  # (an end post: the other selected notes' same point moved too, sketch_post_to)
                for k in self.sel:
                    if k != d["i"] and k < len(self.tones) and self.tones[k] != d["before"][k]:
                        tidy_bend(self.tones[k], True)
        self.bend_done(d)
        return True

    def bend_done(self, d, name=None):
        """The bend held / changed in drag d is done: tidied, one undo step when anything changed."""
        tidy_bend(self.tones[d["i"]], self.sketching())
        if self.tones != d["before"]:
            self.commit(name or d["name"], d["before"])
        else:
            self.redraw()

    def bend_delete_dragged(self):
        """Delete while a point is held: that point goes; a curve square: its piece straight again (one undo step
        with what the drag did)."""
        d = self.drag
        self.end_drag()
        if d["kind"] == "wobblespeed":  # (Sketch's speed line: even speed again)
            run = self.wobble_run(self.tones[d["i"]], *d["at"])
            if run:
                run.pop("speed", None)
                self.spread_pick(d["i"], d["at"])  # (the other selected notes too, hunt 2026-11-13)
            self.bend_done(d, tr("hz.step_wobble_speed_line"))
            return
        if d["kind"] == "wobbleend":  # (an end post: the points it moved back (on every selected note), then the
            self.tones[:] = copy.deepcopy(d["before"])  # wobble goes; hunt 2026-11-13: Delete took the whole note)
            d["at"] = list(d["at0"])
            self.sketch_pick, self.sketch_speed = d["pick"]
        if d["kind"] in ("wobbledepth", "wobblerate", "wobbleend"):  # (Sketch's depth line / speed post / an end
            self.sketch_depth_gone(d)                                     # post: the wobble goes)
            self.spread_pick(d["i"], d["at"], gone=True)
            self.bend_done(d, tr("hz.step_wobble_delete"))
            return
        pts = self.tones[d["i"]]["bend"]
        if d["kind"] == "bendcurve":
            del pts[d["j"]][2:]
        else:
            drop_point(self.tones[d["i"]], d["j"])  # (its wobbles merged, hunt 2026-10-28)
        self.bend_done(d, tr("hz.step_note_bend_delete") if d["kind"] == "bendpt" else None)
        self.point_again()

    def bend_double(self, e):
        """A double-click with the Bend tool: on a curve square its piece is straight again (one step); on a note /
        point a second press like the first (no note deleted). False when the tool isn't on or it's on empty
        space (that works as with the Select tool: pastes) or a slide's dot / square (as with the other tools)."""
        if not self.bending():
            return False
        hit = self.bend_hit(e.x, e.y)
        if not hit or hit[0] == "slide":
            return False
        if hit[0] == "wobble" and hit[2][0] == "line":  # (Sketch's depth line: a depth point added there, held;
            return self.sketch_depth_double(e, hit[1], hit[2])  # its points / post / buttons: a press again)
        if hit[0] == "wobble" and hit[2][0] == "sline":  # (its speed strip's line: a speed point, held)
            return self.sketch_speed_double(e, hit[1], hit[2])
        if self.sketching() and hit[0] in ("note", "point", "curve"):  # (hz_sketch.py: a point added / taken away;
            return self.sketch_double(e, hit[:2] if hit[0] == "curve" else hit)  # on a square too: hunt 2026-10-28,
            # Straight is in its right-click menu)
        if hit[0] != "curve":
            self.on_press(e)
            return True
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        del self.tones[hit[1]]["bend"][hit[2]][2:]
        self.bend_done({"i": hit[1], "before": before}, tr("hz.step_note_bend_curve"))
        return True

    def bend_menu(self, e):
        """Right-click with the Bend tool on a point: it goes. False anywhere else (the usual menu)."""
        if not self.bending():
            return False
        hit = self.bend_hit(e.x, e.y)
        if hit and hit[0] == "wobble":  # (Sketch's depth line: a depth point goes; elsewhere the wobble's shape,
            return self.sketch_depth_drop(hit[1], hit[2]) or self.sketch_wobble_menu(e, hit[1])  # Bake, Remove...)
        if hit and hit[0] == "curve" and self.sketching():  # (Sketch: Single curve / S-curve / Straight)
            self.sketch_curve_menu(e, hit[1], hit[2])
            return True
        if not hit or hit[0] != "point":
            return False
        if self.sketching() and hit[2] in (0, len(self.tones[hit[1]].get("bend") or "..") - 1):  # (Sketch: its start
            return False                                                              # and end stay: its menu)
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        drop_point(self.tones[hit[1]], hit[2])
        self.bend_done({"i": hit[1], "before": before}, tr("hz.step_note_bend_delete"))
        self.point_again()
        return True

    def bend_motion(self, e):
        """The pointer with the Bend tool: a hand on a point, up / down arrows on a curve square, the pencil on a
        note. False elsewhere: the usual pointers (a slide's dot, the kept Select box's sides, the Select tool's on
        empty space)."""
        if not self.bending():
            return False
        self.sketch_alt_to(self.curving(e.state))
        hit = self.bend_hit(e.x, e.y)
        if not hit or hit[0] == "slide":
            return False
        inner = hit[0] == "wobble" and (hit[2][0] == "pt" and 0 < hit[2][1] < len(self.depth_shape()[0]) - 1
                                        or hit[2][0] == "spt" and 0 < hit[2][1] < len(self.speed_shape()[0]) - 1)
        cursor = ("fleur" if hit[0] == "point" or inner else
                  "hand2" if hit[0] == "wobble" and hit[2][0] in ("preset", "lock") else
                  "arrow" if hit[0] == "wobble" and hit[2][0] == "strip" else
                  "sb_h_double_arrow" if hit[0] == "end" or hit[0] == "wobble" and (hit[2] == "speed"
                                                                                      or hit[2][0] == "post") else
                  "sb_v_double_arrow" if hit[0] == "curve" or self.sketching() else
                  self.pencil)  # (Sketch: a press on the line holds its piece, up / down)
        self.canvas.config(cursor=cursor)
        self.show_status(e)
        return True

    def bend_status(self):
        """The status line's part for a point held: how far it bends."""
        d = self.drag
        if not d or d["kind"] != "bendpt":
            return ""
        k = self.tones[d["i"]]["bend"][d["j"]][1]
        return "     " + trn("hz.bend_keys", abs(float(fmt(k))), keys=("+" if k > 0 else "") + fmt(k))

    def bend_items(self, menu, i):
        """The right-click menu's Bend submenu for note i (and the other selected notes when it's one of them): Copy
        bend (note i's, or with none the first of those that has one), Paste bend (the one copied last, in any Hz
        bass; it stretches to each note), Clear bend."""
        sub = tk.Menu(menu, tearoff=0)
        picked = sorted(self.sel) if i in self.sel else [i]
        own = next((j for j in [i] + picked if bends(self.tones[j])), None)
        sub.add_command(label=tr("hz.bend_copy"), command=lambda: self.copy_bend(own),
                        state="normal" if own is not None else "disabled")
        sub.add_command(label=tr("hz.bend_paste"), command=lambda: self.set_bends(picked, self.app.hz_bend_clip,
                                                                                   tr("hz.step_note_bend_paste")),
                        state="normal" if self.app.hz_bend_clip else "disabled")
        sub.add_command(label=tr("hz.bend_clear"), command=lambda: self.set_bends(picked, None,
                                                                                   tr("hz.step_note_bend_clear")),
                        state="normal" if any(bends(self.tones[j]) for j in picked) else "disabled")
        sub.add_command(label=tr("hz.simplify"), command=lambda: self.simplify_bends(i),  # (hz_simplify.py)
                        state="normal" if any(self.simplifies(self.tones[j]) for j in picked) else "disabled")
        sub.add_separator()
        for kind in SHAPES:
            sub.add_command(label=tr(f"hz.bend_{kind}"),
                            command=lambda kind=kind: self.set_bends(picked, kind, tr("hz.step_note_bend_shape")))
        menu.add_cascade(label=tr("hz.bend_menu"), menu=sub)
        menu.bend_sub = sub  # (kept while the menu shows)

    def copy_bend(self, i):
        """Note i's bend line and wobbles kept in app.hz_bend_clip ({"bend", "wobble"}: both stretch to a note
        pasted on, the wobbles' speed stays per beat)."""
        self.app.hz_bend_clip = copy.deepcopy({f: self.tones[i][f] for f in ("bend", "wobble") if self.tones[i].get(f)})
        self.say(tr("hz.bend_copied"))

    def set_bends(self, picked, pts, name):
        """The notes numbered `picked` get bend line pts (None: none; a SHAPES name: that shape, made for each note's
        length; a copy_bend clip: its line and wobbles), one undo step. Their wobbles go with the old line."""
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        if isinstance(pts, str) and pts in SHAPES:  # (R puts it on again: quick_ramp)
            self.app.hz_ramp_last = pts
        for j in picked:
            n = self.tones[j]
            n.pop("wobble", None)
            if isinstance(pts, list):  # (a clip from before wobbles: the line alone)
                pts = {"bend": pts}
            if pts in SHAPES:
                n["bend"] = shaped(pts, n["len"])
            elif pts:
                n.pop("bend", None)
                n.update(copy.deepcopy(pts))
            else:
                n.pop("bend", None)
            tidy_bend(n, self.sketching())
        if self.tones != before:
            self.commit(name, before)

    def quick_ramp(self):
        """R (Quick Ramp, like the plugin's): the ready bend shape picked last (Scoop up at first) on the selected
        notes, one undo step; nothing selected = said."""
        if self.drag or self.target() is None:
            return
        if not self.sel:
            return self.say(tr("hz.ramp_none"))
        kind = self.app.hz_ramp_last if self.app.hz_ramp_last in SHAPES else "scoop"
        self.set_bends(sorted(self.sel), kind, tr("hz.step_note_bend_shape"))
        self.say(tr("hz.ramp_done", shape=tr(f"hz.bend_{kind}")))

    def draw_bends(self):
        """With the Bend tool: each note's bend line in view (as it bends the note, dashed) and its points."""
        if not self.bending():
            return
        c, s, most, w = self.paint, self.s, self.bend_most(), self.canvas.winfo_width()
        for n in self.tones if not self.sketching() else ():  # (Sketch: the note's own line shows it)
            if not n.get("bend") or self.x_of(n["t"] + n["len"]) < self.kb_w or self.x_of(n["t"]) > w:
                continue
            beats, keys = self.bend_line(n, most)
            xy = np.column_stack([self.x_of(beats), self.pitch_y(keys)]).ravel().tolist()
            c.create_line(*xy, fill=BEND, width=max(1, round(1.5 * s)), dash=(4, 2))
        r = 3 * s
        for x, y, i, j, *_ in self.bend_squares():  # (filled = curved)
            curved = len(self.tones[i]["bend"][j]) > 2
            c.create_rectangle(x - r, y - r, x + r, y + r, fill=BEND if curved else "", outline=BEND,
                               width=max(1, round(1.5 * s)))
        r = 3.5 * s
        for x, y, i, j in self.bend_points():
            held = self.drag and self.drag["kind"] == "bendpt" and (self.drag["i"], self.drag["j"]) == (i, j)
            c.create_oval(x - r, y - r, x + r, y + r, fill=look.HZ_DOT if held else BEND, outline=BEND,
                          width=max(1, round(1.5 * s)))


def shaped(kind, length):
    """A ready bend line (SHAPES) for a note `length` beats long, its times worked out in beats (a scoop stays as
    short on a long note), then kept as parts of the note like any bend (so it stretches with it afterwards)."""
    if kind == "scoop":  # (2 keys low, up onto the note: fast first)
        return [[0.0, -SCOOP[1], 0.6], [min(0.5, SCOOP[0] / length), 0.0]]
    if kind == "fall":  # (from the note, down at the end: slow first)
        return [[max(0.5, 1.0 - FALL[0] / length), 0.0, -0.6], [1.0, -FALL[1]]]
    # Quick Ramp shapes (user 2026-11-05): RAMP keys over RAMP_PART of the note (not beats: the user's pick)
    start = {"drop": RAMP[1], "up_both": -RAMP[1], "down_both": RAMP[1]}.get(kind)
    end = {"rise": RAMP[1], "up_both": RAMP[1], "down_both": -RAMP[1]}.get(kind)
    if start is not None or end is not None:
        pts = [[0.0, start, 0.6], [RAMP[0], 0.0]] if start is not None else [[0.0, 0.0]]
        pts += [[1.0 - RAMP[0], 0.0, -0.6], [1.0, end]] if end is not None else [[1.0, 0.0]]
        return pts
    rate, depth = WOBBLE  # wobble: quarter waves, each piece curved like a sine's (fast out of 0, slow at the tops)
    count = min(WOBBLE_MOST, max(2, round(4 * rate * length)))  # (a note under a wave long: part of one)
    return [[k / count, (0.0, depth, 0.0, -depth)[k % 4], 0.6 if k % 2 == 0 else -0.6] for k in range(count + 1)]


SHAPES = ("scoop", "fall", "rise", "drop", "up_both", "down_both", "wobble")
RAMP = (0.25, 2.0)  # share of the note, keys: the Quick Ramp shapes (rise ... down_both)
SCOOP = (0.125, 2.0)  # beats, keys below: Scoop up
FALL = (0.25, 5.0)  # ... keys down at the end: Fall off
WOBBLE = (4.0, 0.5)  # waves a beat, keys each way
WOBBLE_MOST = 4096  # quarter waves at most on one note (256 beats at 4 a beat)


def follow_wobble(n, wob, j, old):
    """Note n's wobbles after its bend point j moved sideways from `old` (wob = its wobbles when it was pressed):
    a wobble ending / starting on it follows it (over one piece or several, stage D 2026-11-13); one on the far side
    of a jump (another point on the same spot) stays (hunt 2026-10-28): the one arriving there ends on the jump's
    first point, the one leaving starts on its second."""
    pts, out = n["bend"], []
    first = not (j > 0 and abs(pts[j - 1][0] - old) < ON_POINT)  # (no point before it on that spot)
    last = not (j + 1 < len(pts) and abs(pts[j + 1][0] - old) < ON_POINT)
    for w in copy.deepcopy(wob):
        u0, u1 = w["at"]
        if first and abs(u1 - old) < ON_POINT:
            w["at"] = [u0, pts[j][0]]
        elif last and abs(u0 - old) < ON_POINT:
            w["at"] = [pts[j][0], u1]
        out.append(w)
    if out:
        n["wobble"] = out


def drop_point(n, j):
    """Note n's bend point j taken away: the two pieces it parted are one, so are their wobbles (merge_wobbles;
    hunt 2026-10-28). One of a jump (another point on its spot) parts nothing."""
    pts = n["bend"]
    u = pts[j][0]
    lo = pts[j - 1][0] if j > 0 else None
    hi = pts[j + 1][0] if j + 1 < len(pts) else None
    del pts[j]
    if lo is not None and hi is not None and not any(abs(p[0] - u) < ON_POINT for p in pts):
        merge_wobbles(n, lo, u, hi)


def tidy_bend(n, pieces=False):
    """A note's bend line after a change: rounded; none left when every point is back at 0 (pieces: the Sketch
    tool's, a flat line keeps the points between its ends, they're its pieces). Wobbles with no length left go."""
    if "wobble" in n:
        n["wobble"] = [w for w in n["wobble"] if w["at"][1] - w["at"][0] > 1e-9 and depth_most(w) > 0]
        if not n["wobble"]:
            del n["wobble"]
        else:
            wobble_points(n)  # (each wobble's ends on points: it can be picked; hunt 2026-11-13, Scoop / Fall)
    pts = n.get("bend")
    if pts is None:
        return
    pts[:] = [[round(p[0], 9), round(p[1], 4), *p[2:]] for p in pts]
    # two points on one spot at one pitch do nothing: one (user 2026-10-28), the later kept (its curve is the
    # piece after); at different pitches they're a jump and stay
    pts[:] = [p for p, q in zip(pts, pts[1:] + [None]) if not (q and q[0] == p[0] and q[1] == p[1])]
    if pts and all(abs(p[1]) < 1e-9 for p in pts) and not pieces and n.get("wobble"):
        # (flat, Bend tool: the points a wobble starts / ends on stay, its piece with them; hunt 2026-10-28)
        ends = {u for w in n["wobble"] for u in w["at"]} | {0.0, 1.0}
        pts[:] = [p for p in pts if any(abs(p[0] - u) < ON_POINT for u in ends)]
        pieces = True
    if not pts or all(abs(p[1]) < 1e-9 for p in pts) and not (pieces and len(pts) > 2):
        n.pop("bend")
