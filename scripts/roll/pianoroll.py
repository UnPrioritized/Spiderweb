"""The piano roll canvas: view (scroll/zoom), hit testing and mouse editing.
Its other parts: roll_draw.py (painting), roll_custom.py (custom shape box), roll_curve.py (curve editing),
roll_funnel.py (funnel editing), roll_live.py (live drawing, custom shape strokes), roll_menu.py (right-click menu), roll_text.py (the Text tool),
roll_hz.py (the Hz bass tool), zoombar.py (its scrollbars and zoom buttons),
roll_shared.py (colours, keys, caches)."""

import copy
import math
import tkinter as tk
from types import SimpleNamespace

import numpy as np

from files.lang import tr
from notes.between import copied as between_copied, group_of
from notes.custom import box_frame, fill_plan, fill_test
from notes.engine import cached_arrays, make_shape
from notes.joined import all_tumours
from notes.paths import KEYS
from notes.funnel import funnel_contains, funnel_handles, funnel_origins
from notes.fx import copied as fx_copied, with_turn as fx_turned
from notes.sliced import steps_kept
from roll.roll_curve import CurveEditing
from roll.roll_custom import CustomBox
from roll.roll_draw import RollDrawing
from roll.roll_funnel import FunnelEditing
from roll.roll_hz import HzStart
from window import look
from window.hz_window import open_hz
from roll.roll_live import BOX_TOOLS, LiveDrawing
from roll.roll_menu import ShapeMenu
from roll.roll_shared import (ALT, BOX_CURSORS, BOX_SCROLL_MS, BOX_STILL, CTRL, PICK, SHIFT, boxes_side,
                              boxes_upright, cached_path, cached_strokes, grab_while_panning, grid_span, line_touches_box,
                              mouse_trail, note_name)
from roll.roll_text import TextTyping


class PianoRoll(RollDrawing, CustomBox, CurveEditing, FunnelEditing, LiveDrawing, ShapeMenu, TextTyping, HzStart,
                tk.Canvas):
    def __init__(self, parent, app, scale):
        super().__init__(parent, bg=look.ROLL_BG, highlightthickness=0, cursor="crosshair")
        self.app = app
        self.scale = scale
        self.kb_w, self.ruler_h = 56 * scale, 20 * scale
        # view: beat at left edge, pitch at top edge, pixels per beat, pixels per pitch row
        self.view_t, self.view_top, self.sx, self.sy = 0.0, 127.5, None, None
        self.draft = None  # shape being drawn
        self.drag = None   # what the left mouse button is doing
        self.box_shift = False   # the Select box was last moved with Shift (its mouse corner not snapped)
        self.box_shift0 = False  # ... pressed with Shift (its first corner not snapped)
        self.box_kept = None     # ([box_area, ...], selection) of the last Select boxes, shown after letting go
        self.box_more = []       # the boxes kept when a Ctrl+drag started a new one (it's added to them)
        self.box_mouse = None    # (x, y, state) of the mouse while a Select box is dragged (box_scroll)
        self.box_timer = None
        self.box_moving = None   # the kept Select boxes (box_upright) when a move started: they go along
        self.box_in = False      # ... and that move was pressed inside them
        self.dup = None          # Ctrl+press in the kept boxes: {"click": shape under the mouse}; the copies are
        #                          made at the first move ("done" then)
        self.grabbed = None      # the shape a move drag was started on (None: all selected, the kept boxes moved)
        self.room = None         # how far each shape being moved can go: {shape number: move_room}
        self.limit = None        # how far the shapes being edited may reach (limits)
        self.hz_held = None      # a Hz bass's tones as the drag of one of its stroke points started (drag_stroke)
        self._pan = None
        self._panned = False     # the middle button moved further than a click's 3 px (pan_to)
        self._saved_view = None
        self._tiles = None       # grid + notes as one picture when there are too many notes for canvas items
        self._note_pic = None    # what that picture shows (see redraw)
        self._note_index = None  # rendered notes sorted by start, to find the visible ones quickly
        self._start_order = None  # their row numbers in that order
        self._paint_order = None  # the compiled loops' notes in painting order (roll_draw.paint_order)
        self._img = None         # that picture's pixels (NumPy), to move along when the view moves
        self._exact = None       # timer: the picture painted whole again after it was moved along
        self._shown = None       # the pixels the picture on screen has now (show_image sends only what differs)
        self._ring = None       # the selected notes' outline, worked out (draw_ring)
        self._carry = None     # the picture's parts while shapes are dragged (paint_carried)
        self._draft_base = None  # the other notes' picture while a shape is drawn / placed (paint_drafted)
        self.paint_time = 0.0    # seconds the last whole repaint of the notes took (app.shapes_changed: slow?)
        self._redraw_pending = False
        self._late_redraw = None  # request_redraw(delay)'s timer
        self._draft_made = None  # the shape being drawn / placed: (notes_tracks key, its notes and tracks)
        self._draft_time = 0.0   # seconds making them took the last time
        self.draft_moving = False  # slow ones: only its lines follow the mouse (draft_moved)
        self._draft_rest = None  # timer: the mouse rested, its notes show
        self._draft_text = ""    # the status line's mouse spot while placing (show_draft)
        # a shape started with a click (no drag): the drag it would have been, following the mouse until the next
        # click finishes it
        self.follow = None
        self.arc_bend = False  # an arc dragged start -> end: its middle point follows the mouse until a click
        self.curve_clip = None   # a funnel curve's shape, copied with Ctrl+C
        self.typing = None       # the text being typed with the Text tool (roll_text.py)
        self._caret_job = None
        self.bars = ()           # its scrollbars (zoombar.py)
        self._end_of = self._end = None  # the last note's end, and the notes it was worked out for

        self.bind("<Configure>", self.on_configure)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Double-Button-1>", self.on_double)
        self.bind("<Motion>", self.on_motion)
        self.bind("<ButtonPress-3>", self.on_right)
        self.bind("<B3-Motion>", self.on_right_drag)
        self.bind("<ButtonRelease-3>", self.on_right_release)
        self.bind("<Double-Button-3>", lambda e: self.app.toggle_select_tool())
        self._scrub = None  # right-drag listening
        self.bind("<ButtonPress-2>", self.start_pan)
        self.bind("<B2-Motion>", self.pan_to)
        self.bind("<ButtonRelease-2>", self.on_middle_release)
        grab_while_panning(self)
        self.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Key>", self.on_key)

    # ------------------------------------------------------------ coordinates

    def t2x(self, b):
        return self.kb_w + (b - self.view_t) * self.sx

    def x2t(self, x):
        return (x - self.kb_w) / self.sx + self.view_t

    def p2y(self, p):
        return self.ruler_h + (self.view_top - p) * self.sy

    def y2p(self, y):
        return self.view_top - (y - self.ruler_h) / self.sy

    def event_pt(self, e, snap=True, above=False):
        """The mouse as (beat, pitch), kept inside the visible roll and the MIDI key range (dragging past the
        edge of the window stays on the edge, like the velocity pane). above: shapes being moved: past the top of
        the piano roll (the view as high as it goes) the keys go on up to the 256 keys' highest, also at 128 keys
        (user: shapes can be put away up there, an arrow shows them: above_marks)."""
        x = min(max(e.x, self.kb_w), self.winfo_width())
        y = min(max(e.y, self.ruler_h), self.winfo_height())
        if above and e.y < self.ruler_h and self.view_top >= self.app.keys - 0.5 - 1e-9:
            y = e.y
        b, p = self.x2t(x), self.y2p(y)
        sb = self.app.snap_beats()
        if snap and sb and not e.state & SHIFT:
            b, p = round(b / sb) * sb, round(p)
        return [max(0.0, b), min(max(p, 0), (KEYS[1] if above else self.app.keys) - 1)]

    def slice_pt(self, e):
        """The Slice tool's line end at the mouse: time on the snap grid, keys on the lines between key rows (so a
        flat cut runs between two keys); Shift = exactly there."""
        b, p = self.event_pt(e, snap=False)
        sb = self.app.snap_beats()
        if not e.state & SHIFT:
            b, p = (round(b / sb) * sb if sb else b), math.floor(p) + 0.5
        return [b, p]

    def box_area(self):
        """The Select box being dragged as (beat, pitch, beat, pitch) corners: out to whole snap steps and whole
        keys (grid_span; Shift / snap off = whole ticks, keys still whole: user), so it's never thinner than one
        step and one key. None while it's still a click."""
        x, y = self.box_from()
        cx, cy = self.drag[3:5]
        if abs(cx - x) < BOX_STILL and abs(cy - y) < BOX_STILL:
            return None
        tick, step = 1 / self.app.ppq, self.app.snap_beats()
        b0, b1 = grid_span(self.x2t(x), self.x2t(cx), tick if self.box_shift0 else step or tick,
                           tick if self.box_shift else step or tick)
        top = self.app.keys - 1
        p0, p1 = sorted(min(max(round(self.y2p(v)), 0), top) for v in (y, cy))
        return b0, p1 + 0.5, b1, p0 - 0.5

    def box_from(self):
        """Where the Select box being dragged was pressed, on screen now. It's kept as (beat, pitch): the wheel,
        panning, page turns and scrolling past the edge leave it where it is in the song (user)."""
        return self.t2x(self.drag[1]), self.p2y(self.drag[2])

    def box_rect(self, area):
        """A box_area on screen (x0, y0, x1, y1), x0 < x1, y0 < y1."""
        (x0, x1), (y0, y1) = sorted((self.t2x(area[0]), self.t2x(area[2]))), sorted((self.p2y(area[1]),
                                                                                      self.p2y(area[3])))
        return x0, y0, x1, y1

    def box_to(self):
        """The Select box's corner goes to the mouse (box_mouse), kept inside the piano roll. Nothing is selected
        before it's let go (box_pick; user: like Domino)."""
        _, b, p, _, _, base, primary = self.drag
        mx, my, state = self.box_mouse
        self.drag = ("box", b, p, min(max(mx, self.kb_w), self.winfo_width()),
                     min(max(my, self.ruler_h), self.winfo_height()), base, primary)
        self.box_shift = bool(state & SHIFT)
        self.draw_select_box()

    def box_pick(self):
        """The Select box let go: it selects the shapes it touches (Ctrl: added to the ones selected at the
        press)."""
        _, _, _, _, _, base, primary = self.drag
        box = self.box_area()
        found = self.shapes_in_box(*self.box_rect(box)) - base if box else set()
        if base | found != self.app.sels:
            self.app.select_many(base | found, max(found) if found else primary)

    def box_scroll(self):
        """A Select box dragged past the piano roll's edge: the view goes a beat that way (3 keys up / down) at
        once and then every BOX_SCROLL_MS while the mouse stays out there. The box stays where it is in the song
        (left behind as the view moves) until the mouse moves again."""
        self.box_timer = None
        if not self.drag or self.drag[0] != "box" or self.sx is None:
            return
        mx, my, _ = self.box_mouse
        dx = (mx > self.winfo_width()) - (mx < self.kb_w)
        dy = (my > self.winfo_height()) - (my < self.ruler_h)
        if not dx and not dy:
            return
        t, top = self.view_t, self.view_top
        # (whole pixels: the picture of the notes can then be moved along, see roll_draw)
        self.view_t += dx * max(1, round(self.sx)) / self.sx
        self.view_top -= dy * max(1, round(3 * self.sy)) / self.sy
        self.clamp_view()
        mx, my = (self.view_t - t) * self.sx, (self.view_top - top) * self.sy
        _, b, p, cx, cy, *rest = self.drag
        self.drag = ("box", b, p, cx - mx, cy + my, *rest)
        self.request_redraw()
        self.box_timer = self.after(BOX_SCROLL_MS, self.box_scroll)

    def on_kept_box(self, kept, e):
        """Where the mouse is on the kept Select boxes (boxes_side), or None. With Ctrl only inside counts (a drag
        there moves a copy; on a side Ctrl starts another box)."""
        if not kept or not self.app.sels:
            return None
        side = boxes_side([self.box_rect(a) for a in kept], e.x, e.y, 5 * self.scale)
        return None if side and side != (0, 0) and e.state & CTRL else side

    def stretch_to(self, e):
        """The kept Select box's side / corner dragged: its side goes to the mouse (the grid line / key row
        nearest it; Shift / snap off = the tick nearest it, keys still whole: user) and the shapes it selected
        stretch with it, from the other side."""
        app = self.app
        _, (sx, sy), area, orig, boxes = self.drag
        b0, top, b1, bottom = area
        at_b, at_p = self.event_pt(e, snap=False)
        ab, kx, ap, ky, new = b0, 1.0, bottom, 1.0, list(area)
        if sx:
            ab, edge = (b0, b1) if sx > 0 else (b1, b0)
            step = (None if e.state & SHIFT else app.snap_beats()) or 1 / app.ppq
            at_b = round(at_b / step) * step
            at_b = max(at_b, ab + step) if sx > 0 else min(at_b, ab - step)
            kx = (at_b - ab) / (edge - ab)
            new[2 if sx > 0 else 0] = at_b
        if sy:
            ap, edge = (bottom, top) if sy < 0 else (top, bottom)
            at_p = round(at_p) + (0.5 if sy < 0 else -0.5)  # (the sides run between key rows: the row under the
            at_p = max(at_p, ap + 1.0) if sy < 0 else min(at_p, ap - 1.0)  # mouse is in)
            ky = (at_p - ap) / (edge - ap)
            new[1 if sy < 0 else 3] = at_p
        step = self.held_step(orig)
        for j, sh in orig.items():
            app.shapes[j].clear()
            app.shapes[j].update(app.stretched(sh, ab, kx, ap, ky))
        if len(boxes) > 1:  # (several boxes: each one stretched the same way)
            new = [(ab + (a[0] - ab) * kx, ap + (a[1] - ap) * ky, ab + (a[2] - ab) * kx, ap + (a[3] - ap) * ky)
                   for a in boxes]
        else:
            new = [tuple(new)]
        self.box_kept = (new, set(app.sels))
        if self.unmoved(step):
            return
        if self.stayed_inside(step):
            app.shape_edited()

    def holding(self):
        """The left button holds a shape, its point / handle / box, or the kept Select box's side: shortcuts wait
        (user)."""
        return bool(self.drag) and self.drag[0] in ("move", "handle", "resize", "skew", "turn", "stretch")

    @staticmethod
    def reach(shapes):
        """(first beat, lowest key, highest key, last beat) the shapes' lines reach, or None (nothing drawn)."""
        arrays = [a for sh in shapes for a in cached_arrays(sh) if len(a)]
        if not arrays:
            return None
        a = np.concatenate(arrays)
        return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 1].max()), float(a[:, 0].max())

    def limits(self, shapes, top=None):
        """How far these shapes may go: beat 0 and the lowest / highest key (top: another highest), or as far past
        them as they already are (user: a shape past an edge isn't pushed back; it just can't go further). None:
        no limits (nothing drawn)."""
        r = self.reach(shapes)
        return r and (min(0.0, r[0]), min(0.0, r[1]), max(self.app.keys - 1.0 if top is None else top, r[2]))

    def move_room(self, sh):
        """How far a shape can be moved (beats left, keys down, keys up) before it passes the limits: up to the
        256 keys' highest even at 128 keys (user: put away up there; it isn't played or saved in the MIDI)."""
        r, lim = self.reach([sh]), self.limits([sh], KEYS[1] - 1)
        return r and (r[0] - lim[0], r[1] - lim[1], lim[2] - r[2])

    def in_room(self, db, dp, e, room):
        """A move by (db beats, dp keys) cut short where a shape would pass the limits (room: move_room): in whole
        grid steps / keys like the move, unless Shift is held."""
        if not room:
            return db, dp
        left, down, up = room
        sb = self.app.snap_beats()
        if sb and not e.state & SHIFT:
            left, down, up = math.floor(left / sb + 1e-9) * sb, math.floor(down + 1e-9), math.floor(up + 1e-9)
        return max(db, -left), min(max(dp, -down), up)

    def held_step(self, idx):
        """Before a drag step changes shapes idx (a point / handle, the box's resize / skew, the kept box's side):
        what's needed to take it back (stayed_inside). The limits are the ones at the drag's first step."""
        before = {j: copy.deepcopy(self.app.shapes[j]) for j in idx}
        if self.limit is None:
            self.limit = self.limits(before.values())
        return before, self.drag, self.box_kept

    def unmoved(self, step):
        """After a drag step (held_step): True if it changed nothing (the mouse moved, but the point snapped to
        where it already was): then there's nothing to make again or repaint (user: that made dragging choppy)."""
        before, drag, kept = step
        if self.drag != drag or self.box_kept != kept:
            return False
        try:
            return all(self.app.shapes[j] == sh for j, sh in before.items())
        except ValueError:  # (a NumPy array inside can't say)
            return False

    def stayed_inside(self, step):
        """After a drag step: True if its shapes keep to the limits, else the step is taken back (the shape stops
        at the piano roll's edge, user)."""
        before, drag, kept = step
        r, lim = self.reach([self.app.shapes[j] for j in before]), self.limit
        if not r or not lim or r[0] >= lim[0] - 1e-9 and r[1] >= lim[1] - 1e-9 and r[2] <= lim[2] + 1e-9:
            return True
        for j, sh in before.items():
            self.app.shapes[j].clear()
            self.app.shapes[j].update(sh)
        self.drag, self.box_kept = drag, kept
        return False

    @staticmethod
    def push_in(r, lim):
        """(beats, keys) that bring lines reaching r back inside the limits lim (turning, user: "rubberband back";
        too tall: the bottom inside)."""
        if not r or not lim:
            return 0.0, 0.0
        db = max(0.0, lim[0] - r[0])
        dp = lim[1] - r[1] if r[1] < lim[1] else lim[2] - r[2] if r[2] > lim[2] else 0.0
        return db, dp

    def kept_box(self):
        """The last Select boxes [box_area, ...] (Ctrl+drag adds one), still shown after letting go while what they
        selected is still the selection (a press on the piano roll or any other change of the selection drops
        them). None = not shown."""
        if self.box_kept and self.box_kept[1] == self.app.sels:
            return self.box_kept[0]
        self.box_kept = None
        return None

    def move_kept_box(self, fn):
        """The shapes in the kept Select boxes were turned / flipped: the boxes go along, each corner by fn(beat,
        key) -> (beat, key), the keys out to whole rows."""
        kept = self.kept_box()
        if not kept:
            return
        new = []
        for b0, top, b1, bottom in kept:
            pts = [fn(b, p) for b in (b0, b1) for p in (top, bottom)]
            bs, ps = [b for b, _ in pts], [p for _, p in pts]
            new.append((min(bs), math.ceil(max(ps) - 0.5 - 1e-9) + 0.5, max(bs), math.floor(min(ps) + 0.5 + 1e-9) - 0.5))
        self.box_kept = (new, set(self.app.sels))

    def clamp_view(self):
        if self.sy is None:
            return
        rows, top = (self.winfo_height() - self.ruler_h) / self.sy, self.app.keys - 0.5
        self.view_top = top if rows >= self.app.keys else min(top, max(rows - 0.5, self.view_top))
        self.view_t = max(0.0, self.view_t)

    def fit_view(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50:
            return
        bs = [b for sh in self.app.shapes for b, _ in cached_path(sh)]
        notes = self.app.rendered
        if len(notes):  # (and the notes: pages / gates can put them far past the drawings, user)
            bs += [notes[:, 0].min() / self.app.ppq, notes[:, 1].max() / self.app.ppq]
        lo, hi = (float(min(bs)), float(max(bs))) if bs else (0, 4 * self.app.beats)
        span = max(hi - lo, 1)
        self.sx = (w - self.kb_w) / (span * 1.06)
        self.view_t = max(0.0, lo - span * 0.03)
        self.sy = (h - self.ruler_h) / self.app.keys
        self.view_top = self.app.keys - 0.5
        self.request_redraw()

    def view_state(self):
        if self.sx is None:
            return self._saved_view
        return {"t": self.view_t, "top": self.view_top, "sx": self.sx / self.scale, "sy": self.sy / self.scale}

    def set_view(self, v):
        """Zoom and scroll position from a saved project (applied once the canvas has a size)."""
        try:
            v = {k: float(v[k]) for k in ("t", "top", "sx", "sy")}
            if not all(map(math.isfinite, v.values())):
                v = None
        except (TypeError, KeyError, ValueError):
            v = None
        self._saved_view = v
        if self.sx is not None:
            self.apply_saved_view() if v else self.fit_view()

    def apply_saved_view(self):
        v, self._saved_view = self._saved_view, None
        self.sx = min(100000.0, max(0.05, v["sx"] * self.scale))
        self.sy = min(60 * self.scale, max(1.0, v["sy"] * self.scale))
        self.view_t, self.view_top = v["t"], v["top"]
        self.clamp_view()
        self.request_redraw()

    def on_configure(self, _):
        if self.sx is None and self.winfo_width() >= 50 and self._saved_view:
            self.apply_saved_view()
        elif self.sx is None:
            self.fit_view()
        else:
            self.clamp_view()
            self.request_redraw()

    # ------------------------------------------------------------ scrollbars and zoom buttons (zoombar.py)

    def end_beat(self):
        """Where the scrollbar under the roll ends: 8 bars after the last note (user's choice)."""
        notes = self.app.rendered
        if self._end_of is not notes:
            self._end_of, self._end = notes, (float(notes[:, 1].max()) if len(notes) else 0.0)
        return self._end / self.app.ppq + 8 * self.app.beats

    def bar_view(self, across):
        """(start, end, total) of what's seen, for a scrollbar: beats across, keys counted from the top down.
        Scrolled past the end (mouse), the total grows to where the view ends."""
        if self.sx is None:
            return None
        if across:
            a, span = self.view_t, max(1, self.winfo_width() - self.kb_w) / self.sx
            return a, a + span, max(self.end_beat(), a + span)
        a, span = self.app.keys - 0.5 - self.view_top, max(1, self.winfo_height() - self.ruler_h) / self.sy
        return a, a + span, max(self.app.keys, a + span)

    def bar_move(self, across, a):
        """A scrollbar was dragged to a. (By whole pixels: the picture of the notes can then be moved along.)"""
        if across:
            self.view_t = self.view_t + round((a - self.view_t) * self.sx) / self.sx if a > 0 else 0.0
        elif a > 0:
            self.view_top += round((self.app.keys - 0.5 - a - self.view_top) * self.sy) / self.sy
        else:
            self.view_top = self.app.keys - 0.5
        self.clamp_view()
        self.request_redraw()

    def bar_zoom(self, across, a, b, which):
        """A scrollbar's end was dragged (which: "start" / "end"): the view shows a..b, the other end stays."""
        if across:
            px = max(1, self.winfo_width() - self.kb_w)
            self.sx = min(100000.0, max(0.05, px / (b - a)))
            self.view_t = a if which == "end" else b - px / self.sx
        else:
            px = max(1, self.winfo_height() - self.ruler_h)
            self.sy = min(60 * self.scale, max(1.0, px / (b - a)))
            self.view_top = self.app.keys - 0.5 - (a if which == "end" else b - px / self.sy)
        self.clamp_view()
        self.request_redraw()

    def zoom_x(self, f, x):
        """Zoom time by f; the beat at canvas x stays where it is."""
        b = self.x2t(x)
        self.sx = min(100000.0, max(0.05, self.sx * f))
        self.view_t = b - (x - self.kb_w) / self.sx

    def zoom_y(self, f, y):
        """Zoom the keys by f; the key at canvas y stays where it is."""
        p = self.y2p(y)
        self.sy = min(60 * self.scale, max(1.0, self.sy * f))
        self.view_top = p + (y - self.ruler_h) / self.sy

    def zoom_step(self, across, f):
        """The "+" / "-" buttons: zoom around the middle of the view."""
        if self.sx is None:
            return
        if across:
            self.zoom_x(f, (self.kb_w + self.winfo_width()) / 2)
        else:
            self.zoom_y(f, (self.ruler_h + self.winfo_height()) / 2)
        self.clamp_view()
        self.request_redraw()

    # ------------------------------------------------------------ hit testing

    def handles(self, sh):
        """(beat, pitch, point index, draggable with any tool)"""
        pts = sh["pts"]
        if sh["kind"] == "curve":
            return self.curve_handles(sh)
        if sh["kind"] == "custom":  # its box has its own corners (custom_hit); a picked curve stroke: its handles
            return self.stroke_handles(sh)
        if sh["kind"] == "free" or len(pts) > 300:
            return []
        out = [(b, p, i, sh["kind"] == "arc" and i == 1) for i, (b, p) in enumerate(pts)]  # an arc's middle: any tool
        if sh["kind"] == "funnel":  # curve starts, anchors, handles: ("start", k) / ("anchor" or "ctrl", k, end, i)
            out += [(b, p, hid, True) for b, p, hid in funnel_handles(sh)]
        return out

    def point_shape(self):
        """The shape whose points / box show and can be dragged: the selected one, none with several selected
        (user, 2026-10-02: the last drawn one's points showed)."""
        return self.app.selected() if len(self.app.sels) == 1 else None

    def hit_handle(self, x, y, any_handle):
        sh = self.point_shape()
        if not sh:
            return None
        near = max(9, 10 * self.scale)
        for b, p, i, free in reversed(self.handles(sh)):
            if (free or any_handle) and abs(self.t2x(b) - x) <= near and abs(self.p2y(p) - y) <= near:
                return i
        return None

    def shape_at(self, x, y, prefer_selected=True):
        """The shape a click at x, y is for, or None. A selected shape under the mouse comes first (so a selected
        shape can be dragged from where another one lies over it); then lines (drawn over every note), the one on
        top first; then insides / notes, again the one on top (drawn later = on top)."""
        sels = sorted(self.app.sels, reverse=True) if prefer_selected else []
        mark = self.above_mark_at(x, y)
        if mark is not None:
            return mark
        for among in ([sels] if sels else []) + [None]:
            i = self.hit_shape(x, y, among)
            if i is None:
                i = self.note_owner(x, y, among)
            if i is not None:
                return i
        return None

    def above_marks(self):
        """[(shape number, x)]: shapes put away above the piano roll's highest key (128 keys: up to 255, user) get
        a small arrow at the top of the piano roll, in the middle of their time, to see and grab them by."""
        top, w = self.app.keys - 0.5, self.winfo_width()
        out = []
        for i, sh in enumerate(self.app.shapes):
            r = self.reach([sh])
            if r and r[1] > top:
                x = self.t2x((r[0] + r[3]) / 2)
                if self.kb_w <= x <= w:
                    out.append((i, x))
        return out

    def above_mark_at(self, x, y):
        """The shape whose arrow (above_marks) is at x, y, the one on top first; None."""
        if y > self.ruler_h + 16 * self.scale:
            return None
        for i, mx in reversed(self.above_marks()):
            if abs(mx - x) <= 8 * self.scale:
                return i
        return None

    @staticmethod
    def pick_strokes(sh):
        """The lines a shape can be picked by: its own, the faint dashed line as drawn (under tumours / a formula)
        and a funnel's curves."""
        strokes = cached_strokes(sh)
        if sh.get("pattern") or sh.get("shape") or any(tm["on"] for tm in all_tumours(sh)):
            strokes = strokes + cached_strokes(dict(sh, tumour=None, tumours=None, pattern=None, shape=None))
        if sh["kind"] == "funnel":
            strokes = strokes + funnel_origins(sh)
        elif sh["kind"] == "custom":  # (its strokes' under a formula)
            strokes = strokes + [p for _, p in PianoRoll.origin_strokes(sh)]
        return strokes

    def shapes_in_box(self, x0, y0, x1, y1):
        """The shapes a box on screen (x0 < x1, y0 < y1) touches: a piece of their line, or one of their notes
        while the notes are shown. A line has to go into the box: one that only reaches its edge doesn't count (user:
        a shape ending on the grid line where the box starts isn't picked). A note too: one ending where the box starts
        (or starting where it ends) doesn't count, nor one on the key row just past its top / bottom (user)."""
        found = set()
        e = 1e-6  # (a line ending on the edge is exactly there on screen too)
        for i, sh in enumerate(self.app.shapes):
            for stroke in self.pick_strokes(sh):
                pts = np.asarray(stroke, float).reshape(-1, 2)
                if line_touches_box(self.t2x(pts[:, 0]), self.p2y(pts[:, 1]), x0 + e, y0 + e, x1 - e, y1 - e):
                    found.add(i)
                    break
        if self.app.show_notes.get() and len(self.app.rendered):
            ppq = self.app.ppq
            t0, t1 = self.x2t(x0) * ppq, self.x2t(x1) * ppq
            ns = self.visible_notes(t0, t1)
            # (the keys whose row's middle is in the box; e: screen pixels turned back into ticks aren't exact)
            k0, k1 = math.ceil(self.y2p(y1) - e), math.floor(self.y2p(y0) + e)
            ns = ns[(ns[:, 2] >= k0) & (ns[:, 2] <= k1) & (ns[:, 0] < t1 - e) & (ns[:, 1] > t0 + e)]
            found.update(int(v) for v in np.unique(ns[:, 5]) if 0 <= v < len(self.app.shapes))
        return found

    def hit_shape(self, x, y, among=None):
        """The shape whose line is near x, y (the one on top first), else the one on top whose inside it is.
        among: just these shape numbers (top first)."""
        order = range(len(self.app.shapes) - 1, -1, -1) if among is None else among
        for i in order:
            for stroke in self.pick_strokes(self.app.shapes[i]):  # (every piece at once: a formula line has thousands)
                pts = np.asarray(stroke, float).reshape(-1, 2)
                px, py = self.t2x(pts[:, 0]), self.p2y(pts[:, 1])
                if len(pts) == 1 and math.hypot(px[0] - x, py[0] - y) < PICK:
                    return i
                ax, ay, dx, dy = px[:-1], py[:-1], np.diff(px), np.diff(py)
                ll = dx * dx + dy * dy
                u = np.clip(((x - ax) * dx + (y - ay) * dy) / np.where(ll == 0, 1, ll), 0, 1)
                if (np.hypot(x - ax - u * dx, y - ay - u * dy) < PICK).any():
                    return i
        for i in order:
            sh = self.app.shapes[i]
            if "notes" in sh and self.inside_strokes(cached_strokes(sh), self.x2t(x), self.y2p(y)):
                return i  # pasted notes: anywhere in their box
            if sh["kind"] == "custom" and sh["fill"] in ("fill", "spam"):
                b, p = self.x2t(x), self.y2p(y)
                test = fill_test(sh)
                if (test([b], [p])[0] if test else
                        self.inside_strokes(cached_strokes(sh) if sh.get("text") else fill_plan(sh)["polys"], b, p)):
                    return i  # filled shapes can be clicked anywhere inside
            if sh["kind"] == "funnel" and funnel_contains(sh, self.x2t(x), self.y2p(y)):
                return i
        return None

    def note_owner(self, x, y, among=None):
        """The shape whose note is under the mouse (a few pixels either side count for short notes), or None.
        Only while the notes are shown. among: just these shapes."""
        if not self.app.show_notes.get() or not len(self.app.rendered):
            return None
        ppq, near = self.app.ppq, 5 * self.scale
        t, t_lo, t_hi = (self.x2t(v) * ppq for v in (x, x - near, x + near))
        ns = self.visible_notes(t_lo, t_hi)
        ns = ns[(ns[:, 2] == math.floor(self.y2p(y) + 0.5)) & (ns[:, 0] <= t_hi) & (ns[:, 1] >= t_lo)]
        if among is not None:
            ns = ns[np.isin(ns[:, 5], list(among))]
        if not len(ns):
            return None
        on = ns[(ns[:, 0] <= t) & (ns[:, 1] > t)]  # right on a note first, else the nearest
        if len(on):
            return int(on[:, 5].max())  # (the shape drawn on top)
        gap = np.maximum(ns[:, 0] - t, t - ns[:, 1])
        return int(ns[gap == gap.min(), 5].max())

    # ------------------------------------------------------------ mouse

    def on_press(self, e):
        self.app.commit_typing()  # (a number typed in a panel box goes to the shapes picked now, before this click)
        self.focus_set()
        kept, self.box_kept, self.box_moving, self.dup = self.kept_box(), None, None, None
        self.grabbed = None
        self.box_in = False
        self.room = self.limit = self.hz_held = None
        if self.follow and self.sx is not None:  # a shape started with a click: this click finishes it
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.on_release(e, second=True)
            return
        if e.x < self.kb_w or self.sx is None:
            return
        app = self.app
        if app.tool.get() == "select":  # Select is the tool at the start, so its tip comes on first use
            app.tips.show("select", wait=True)
        if e.y < self.ruler_h:  # the bar numbers: move the play line
            playing = app.player.running
            app.stop_play()
            app.set_playhead(self.event_pt(e)[0])
            self.drag = ("seek", playing)
            return
        tool = app.tool.get()
        pt = self.draw_pt(e)

        if not self.draft:  # first: a picked curve stroke's handles can be near its custom shape's box
            i = self.hit_handle(e.x, e.y, tool == "select")
            if i is not None:
                if kept:  # (editing the one shape: the Select box stays where it is, user)
                    self.box_kept = (kept, set(app.sels))
                app.push_undo(name=tr("pianoroll.drag_a_point"))
                self.drag = ("handle", i)
                return

        hit = self.custom_hit(e.x, e.y)
        if hit and hit[0] != "inside":  # the selected custom shape's corner / side: resize, just outside: turn / skew
            orig = copy.deepcopy(app.selected()["pts"])
            if kept:  # (the Select box stays where it is, user: like changing a note's length in a box)
                self.box_kept = (kept, set(app.sels))
            app.push_undo(name=tr({"turn": "pianoroll.turn", "skew": "pianoroll.skew"}.get(hit[0], "pianoroll.resize")))
            if hit[0] == "turn":
                self.drag = ("turn", orig, self.screen_angle(orig, e.x, e.y), fx_copied(app.selected().get("fx")),
                             steps_kept(app.selected()))  # (a piece of a shape with pages: a turn step too)
            elif hit[0] == "skew":
                self.drag = ("skew", hit[1], orig, self.event_pt(e, snap=False))
            else:
                self.drag = ("resize", hit[1], orig, hit[0] == "side", self.event_pt(e, snap=False))
            return

        on_box = self.on_kept_box(kept, e) if tool == "select" and not self.draft else None
        if on_box and on_box != (0, 0):  # the kept Select box's side / corner: what's in it stretches
            self.box_kept = (kept, set(app.sels))  # (kept with the undo step: undo puts the box back)
            app.push_undo(name=tr("pianoroll.stretch"))
            boxes, around = boxes_upright(kept)
            self.box_kept = (boxes, set(app.sels))
            self.drag = ("stretch", on_box, around, {j: copy.deepcopy(app.shapes[j]) for j in app.sels}, boxes)
            return
        if on_box:  # inside it: moving the shapes takes it along
            self.box_in = True
            self.box_moving = boxes_upright(kept)[0]
            self.box_kept = (self.box_moving, set(app.sels))

        if tool == "slice":  # (the kept Select boxes stay: only what's in them is cut)
            if kept:
                self.box_kept = (kept, set(app.sels))
            pt = self.slice_pt(e)
            self.drag = ("slice", pt, pt, e.x, e.y)
            self.draw_slice()
            return
        if tool == "text":
            self.text_click(e)
            return
        if tool == "hz":
            self.hz_click(e)
            return
        if tool == "picture":  # (only the image window's handle places it, user: a click placed it by mistake)
            app.open_image()
            return
        if tool == "funnel" and self.draft and len(self.draft["pts"]) == 2:
            # the funnel's line is drawn, now its wall
            self.draft["pts"] += [list(pt), list(pt)]
            self.drag = ("wall", e.x, e.y)
            self.request_redraw()
            return
        if tool == "select":
            # (its notes count too; a selected shape under the mouse first, except Ctrl+click: adds the one on top)
            i = self.shape_at(e.x, e.y, prefer_selected=not e.state & CTRL)
            if i is None and hit and not e.state & CTRL:
                i = app.sel  # anywhere inside the selected custom shape's box moves it
            if i is not None and i not in app.sels and not e.state & CTRL:
                self.box_moving = None  # a shape it didn't select, grabbed inside the box: the box goes (user)
            elif kept and not on_box and i in app.sels and not e.state & CTRL:
                self.box_moving = boxes_upright(kept)[0]  # one it selected, grabbed outside it: it goes along too
                self.box_kept = (self.box_moving, set(app.sels))  # (user; a click there still picks just that one)
            if on_box and e.state & CTRL:  # Ctrl inside the kept Select box: a drag moves a COPY of all it selected
                app.push_undo(name=tr("app.duplicate"))  # (user; let go without moving = a Ctrl+click)
                self.dup = {"click": i}
                self.drag = ("move", pt, {j: copy.deepcopy(app.shapes[j]["pts"]) for j in app.sels}, None, False,
                             False)
                return
            if i is None and on_box:  # inside the kept Select box: all it selected moves
                app.push_undo(name=tr("pianoroll.move"))
                self.drag = ("move", pt, {j: copy.deepcopy(app.shapes[j]["pts"]) for j in app.sels}, None, False,
                             False)
                return
            # clicking the one selected funnel again: its line / curve under the mouse gets highlighted;
            # the one selected custom shape again: its stroke under the mouse gets picked (none: unpicked)
            again = i is not None and app.sels == {i} and app.shapes[i]["kind"] == "funnel"
            part = self.part_at(app.shapes[i], e.x, e.y) if again else None
            if (i is not None and app.sels == {i} and app.shapes[i]["kind"] == "custom" and not app.shapes[i].get("text")
                    and "notes" not in app.shapes[i] and not e.state & CTRL):
                again, part = True, ("stroke", self.stroke_at(app.shapes[i], e.x, e.y))
            if part and e.state & CTRL:  # Ctrl+click: highlight just this one too (or not any more)
                app.set_parts(app.parts ^ {part})
                return
            if i is None:  # a box that selects the shapes it touches (Ctrl: adds them to the ones selected, and
                self.box_more = list(kept or []) if e.state & CTRL else []  # the box to the boxes kept);
                if not e.state & CTRL:  # a click without dragging moves the play line here
                    app.select(None)
                self.drag = ("box", self.x2t(e.x), self.y2p(e.y), e.x, e.y, set(app.sels), app.sel)
                self.box_shift0 = bool(e.state & SHIFT)
                return
            if e.state & CTRL:  # Ctrl+click adds or removes a shape
                app.select(i, toggle=True)
                if i not in app.sels:
                    return
            elif i not in app.sels:
                app.select(i)
            elif i != app.sel:
                app.select_many(app.sels, i)
            app.push_undo(name=tr("pianoroll.move"))
            self.grabbed = i
            orig = {j: copy.deepcopy(app.shapes[j]["pts"]) for j in app.sels}
            # clicking one shape of several without dragging selects just that one; clicking the selected funnel
            # without dragging highlights the part under the mouse (none: clears it)
            self.drag = ("move", pt, orig, None if e.state & CTRL else i, False, part if again else False)
            return
        if tool == "poly":  # click its points, or drag each segment
            if self.draft is None:
                self.draft = make_shape("poly", [pt, pt], app.defaults)
            elif self.poly_point(pt):
                return
            self.drag = ("segment", e.x, e.y)
            self.request_redraw()
            return
        if tool in BOX_TOOLS:
            self.draft = self.box_draft(tool, pt, pt)
            self.drag = ("create", pt, e.x, e.y)
            self.request_redraw()
            return
        if tool == "arc":  # three clicks: start, a point it passes through, end (or drag start -> end, then bend)
            if self.draft is None:
                self.draft = make_shape("arc", [pt, pt], app.defaults)
                self.draft["k"] = self.sy / self.sx  # round as it looks on screen now
                self.drag = ("arcdrag", e.x, e.y)
            elif self.arc_bend:  # dragged start -> end: this click sets the point it passes through
                self.draft["pts"][1] = pt
                self.arc_bend = False
                self.finish_arc()
            elif len(self.draft["pts"]) == 2:
                self.draft["pts"][-1] = pt
                self.draft["pts"].append(list(pt))
            else:
                self.draft["pts"][-1] = pt
                self.finish_arc()
            self.request_redraw()
            return
        if tool == "custom":
            tpl = app.custom_template(app.custom_shape)
            if not tpl:
                app.status.config(text=tr("pianoroll.pick_a_custom_shape_in_the"))
                return
            self.draft = app.new_custom(tpl[0], *pt, *pt, areas=tpl[2], drawn=tpl[1])
            self.drag = ("place", pt, e.x, e.y, tpl[1])
            self._draft_time = 0.0  # (slow or not: found again for each new shape)
            self.request_redraw()
            return
        if tool == "free":
            self.draft = make_shape("free", [self.draw_pt(e, snap=False)], app.defaults)
            trail = mouse_trail(e.x_root, e.y_root, None)
            self.drag = ("free", e.x, e.y, trail[-1][2] if trail else None)
            return
        self.draft = make_shape(tool, [pt, pt], app.new_defaults(tool))
        self.drag = ("create", pt, e.x, e.y)
        self._draft_time = 0.0
        self.request_redraw()

    def on_drag(self, e):
        self.app.show_position(self.position_text(e))
        if not self.drag:
            return
        kind = self.drag[0]
        if e.state & 0x200 and kind != "pan":  # (the middle button held too: Tk gives the moves to the left button's
            self.pan_view(e)                     # drag, so the view moves here; what's held follows the mouse)
        if kind == "pan":
            self.pan_to(e)
        elif kind == "box":
            self.box_mouse = (e.x, e.y, e.state)
            self.box_to()
            if self.box_timer is None:
                self.box_scroll()
        elif kind == "slice":
            self.drag = ("slice", self.drag[1], self.slice_pt(e), *self.drag[3:])
            self.draw_slice()
        elif kind == "textsel":
            self.text_drag(e)
        elif kind == "seek":
            self.app.set_playhead(self.event_pt(e)[0])
        elif kind == "handle":
            step = self.held_step([self.app.sel])
            if isinstance(self.drag[1], tuple) and self.app.selected()["kind"] == "custom":
                self.drag = ("handle", self.drag_stroke(self.app.selected(), self.drag[1], e))
            elif isinstance(self.drag[1], tuple):
                self.drag_funnel(self.app.selected(), self.drag[1], e)
            elif self.app.selected()["kind"] == "curve":
                self.drag_curve(self.app.selected(), self.drag[1], e)
            else:
                sh, i = self.app.selected(), self.drag[1]
                sh["pts"][i] = pt = self.event_pt(e)
                if sh["kind"] == "funnel" and i in (2, 3) and e.state & CTRL:  # the wall centred on the line
                    mid = self.crossing(sh["pts"][:2], (sh["pts"][5 - i], pt))
                    if mid:
                        sh["pts"][5 - i] = [2 * mid[0] - pt[0], 2 * mid[1] - pt[1]]
            if self.unmoved(step):
                return
            if self.stayed_inside(step):
                self.app.shape_edited()
        elif kind == "move":
            pt = self.event_pt(e, above=True)
            _, start, orig, one, _, part = self.drag
            db, dp = pt[0] - start[0], pt[1] - start[1]
            if not (db or dp) and not self.drag[4]:
                return
            if self.dup and self.dup != "done":
                orig = self.copy_moved(orig)
            if self.room is None:  # (each shape stops at beat 0 and the lowest / highest key on its own, the Select
                self.room = {j: self.move_room(self.app.shapes[j]) for j in orig}  # box and the rest go on: user)
            self.drag = ("move", start, orig, one, True, part)
            kept = self.box_kept
            moved = False
            for j, pts in orig.items():
                bj, pj = self.in_room(db, dp, e, self.room[j])
                new = [[b + bj, p + pj] for b, p in pts]
                moved = moved or new != self.app.shapes[j]["pts"]
                self.app.shapes[j]["pts"] = new
            if self.box_moving:
                self.box_kept = ([(b0 + db, top + dp, b1 + db, bottom + dp) for b0, top, b1, bottom in self.box_moving],
                                 set(self.app.sels))
            if moved or self.box_kept != kept:  # (snapped to where they already were: nothing to redo)
                self.app.shape_edited(moving=True)
        elif kind == "stretch":
            self.stretch_to(e)
        elif kind == "free":
            self.free_drag(e)
        elif kind in ("segment", "arcdrag"):  # a polyline's next point / an arc's end, at the mouse
            self.draft["pts"][-1] = self.draw_pt(e)
            self.request_redraw()
        elif kind == "create":
            kind, pt = self.draft.get("draw") or self.draft["kind"], self.draw_pt(e)
            if kind in BOX_TOOLS:
                if e.state & CTRL:  # a perfect circle / polygon on screen
                    pt = self.keep_aspect(self.drag[1], pt, self.box_aspect(kind))
                self.draft = self.box_draft(kind, self.drag[1], pt)
            else:
                self.draft = make_shape(kind, [self.drag[1], pt], self.app.new_defaults(kind))
            self.draft_moved()
        elif kind == "wall":
            pt = self.event_pt(e)
            self.draft["pts"][3] = pt
            if e.state & CTRL:  # centred on the line: both ends equally far from it
                mid = self.crossing(self.draft["pts"][:2], (self.draft["pts"][2], pt))
                if mid:
                    self.draft["pts"][2] = [2 * mid[0] - pt[0], 2 * mid[1] - pt[1]]
            self.request_redraw()
            text = self.position_text(e) or ""
            self.app.show_position(tr("pianoroll.new_funnel_notes", text=text,
                                      note_count=self.app.note_count(self.draft)))
        elif kind == "resize":
            _, k, orig, side, start = self.drag
            pt = self.resize_point(orig, k, side, start, e)
            step = self.held_step([self.app.sel])
            self.app.selected()["pts"] = self.resize_custom(orig, k, pt, e.state & CTRL, side)
            if self.unmoved(step):
                return
            if self.stayed_inside(step):
                self.app.shape_edited()
        elif kind == "skew":  # slides in whole grid steps / keys, wherever it was grabbed
            _, k, orig, start = self.drag
            step = self.held_step([self.app.sel])
            self.app.selected()["pts"] = self.skew_custom(orig, k, *self.drag_steps(start, e))
            if self.unmoved(step):
                return
            if self.stayed_inside(step):
                self.app.shape_edited()
        elif kind == "turn":
            _, orig, a0, fx0, steps = self.drag
            angle = self.screen_angle(orig, e.x, e.y) - a0
            angle = (angle + math.pi) % (2 * math.pi) - math.pi
            if not e.state & SHIFT:
                step = math.radians(15)
                angle = round(angle / step) * step
            sh = self.app.selected()
            if self.limit is None:
                self.limit = self.limits([sh])
            was = sh["pts"]
            sh["pts"] = self.turn_custom(orig, angle)
            if fx0 or steps:  # (its note tool pages' result is turned too, fx.py)
                fx = fx_turned(fx_copied(fx0), math.degrees(angle), self.sy / self.sx, steps)
                if fx:
                    sh["fx"] = fx
                else:
                    sh.pop("fx", None)
            db, dp = self.push_in(self.reach([sh]), self.limit)  # (pushed back inside the piano roll, user)
            if db or dp:
                sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]
            if sh["pts"] != was:  # (snapped to the same angle: nothing to redo)
                self.app.shape_edited()
            self.app.show_position(tr("pianoroll.turned", degrees=math.degrees(angle)))
        elif kind == "hzkeys":
            self.hz_drag(e)
        elif kind == "place":
            _, start, _, _, aspect = self.drag
            pt = self.event_pt(e)
            if e.state & CTRL and aspect:
                pt = self.keep_aspect(start, pt, aspect)
            self.draft["pts"] = box_frame(start[0], start[1], pt[0], pt[1])
            self.draft_moved()
            self.show_draft(self.position_text(e) or "")

    def copy_moved(self, orig):
        """Ctrl+drag inside the kept Select box, the first move: copies of the shapes are added and selected, and
        they're what moves (the shapes stay). orig for the copies."""
        app = self.app
        first = len(app.shapes)
        app.shapes += [copy.deepcopy(app.shapes[j]) for j in sorted(orig)]
        between_copied(app.shapes, first)  # (a whole Add between group: a new group; parts of one: plain shapes)
        new = {first + k: orig[j] for k, j in enumerate(sorted(orig))}
        if len(app.shapes) > first + len(orig):  # (a group copied without its steps: they were added)
            new.update({j: copy.deepcopy(app.shapes[j]["pts"]) for j in range(first + len(orig), len(app.shapes))})
        app.select_many(new, max(new))
        app.shapes_changed(now=True)  # (the copies' notes are there before they're carried along)
        self.dup = "done"
        return new

    def free_drag(self, e):
        """Freehand: every place the mouse passed since the last move (also the ones Windows skipped while the
        program was busy), at least 3 px apart. Only the new bit of line is drawn now; the whole roll (the notes)
        follows a few times a second, so painting doesn't hold up reading the mouse."""
        _, lx, ly, since = self.drag
        trail = mouse_trail(e.x_root, e.y_root, since)
        if trail:
            since = trail[-1][2]
            ox, oy = self.winfo_rootx(), self.winfo_rooty()
            spots = [(x - ox, y - oy) for x, y, _ in trail]
        else:
            spots = [(e.x, e.y)]
        pts = self.draft["pts"]
        for x, y in spots:
            if math.hypot(x - lx, y - ly) >= 3:
                pt = self.event_pt(SimpleNamespace(x=x, y=y, state=e.state), snap=False)
                self.create_line(self.t2x(pts[-1][0]), self.p2y(pts[-1][1]), self.t2x(pt[0]), self.p2y(pt[1]),
                                 fill=look.DRAFT_LINE, width=2)
                pts.append(pt)
                lx, ly = x, y
        self.drag = ("free", lx, ly, since)
        self.request_redraw(delay=100)

    def on_release(self, e, second=False):
        """second: the click that finishes a shape started with a click (see follow)."""
        if not self.drag:
            return
        self.after_idle(self.app.settle_history)  # (a click that changed nothing isn't a step)
        kind = self.drag[0]
        self.app.catch_up_notes()
        if kind == "slice":  # (a click without dragging cuts nothing)
            _, a, b, x, y = self.drag
            self.drag = None
            self.draw_slice()
            if abs(e.x - x) >= 4 or abs(e.y - y) >= 4:
                self.app.slice_along(a, b)
            return
        still = False  # let go where it was pressed
        if kind in ("create", "place", "wall", "segment", "arcdrag", "hzkeys"):
            x, y = self.drag[2:4] if kind == "place" else self.drag[-2:]
            still = abs(e.x - x) < 4 and abs(e.y - y) < 4
        if still and not second and kind in ("create", "place", "wall", "hzkeys"):
            # a click, not a drag: the shape follows the mouse until the next click
            self.follow, self.drag = self.drag, None
            if kind == "wall":
                self.app.sync_funnel()
            return
        if kind == "box" and self.box_timer:
            self.after_cancel(self.box_timer)
            self.box_timer = None
        if kind == "box" and (self.box_area() or self.box_more and e.state & CTRL):
            self.box_pick()
            self.box_kept = (self.box_more + [self.box_area()] if self.box_area() else self.box_more,
                             set(self.app.sels))
        elif kind == "seek" and self.drag[1]:
            self.app.start_play(keep=True)  # it was playing: carry on from the new spot
        elif kind == "box" and not self.box_area() and not e.state & CTRL:
            playing = self.app.player.running
            self.app.stop_play()
            self.app.set_playhead(self.event_pt(e)[0])
            if playing:
                self.app.start_play(keep=True)
        elif kind == "move" and not self.drag[4] and self.dup:  # Ctrl+click in the box: adds / takes out one
            if self.dup["click"] is not None:
                self.app.select(self.dup["click"], toggle=True)
        elif (kind == "move" and not self.drag[4] and self.drag[3] is not None and len(self.app.sels) > 1
              and not (self.box_moving and self.box_in)):  # (inside the kept Select box a click keeps the
            #                                                selection and box: user)
            self.app.select(self.drag[3])
        elif kind == "move" and not self.drag[4] and self.drag[5] is not False:
            part = self.drag[5]
            if part and part[0] == "stroke":
                self.app.set_stroke(part[1])
            else:
                self.app.set_parts(self.part_group(self.app.selected(), part) if part else (), main=part)
        elif kind == "hzkeys":
            open_hz(self.app)
        elif kind == "create":
            funnel = self.draft["kind"] == "funnel"
            if still:  # clicked twice in the same spot: nothing
                self.cancel_draft()
            elif funnel and self.add_funnel_line():
                pass
            elif funnel:  # the line: the wall comes next (the panel says so)
                self.app.sync_funnel()
            else:
                self.commit_draft()
        elif kind == "wall":
            if still:
                del self.draft["pts"][2:]  # clicked twice in the same spot: still waiting for the wall
                self.request_redraw()
            elif not self.arrange_funnel(self.draft["pts"]):
                del self.draft["pts"][2:]  # the wall can't run along the line: still waiting for a wall
                self.request_redraw()
                self.app.sync_funnel(note=tr("pianoroll.the_wall_has_to_cross_the"))
            elif self.app.confirm_big([dict(self.draft, pts=self.arrange_funnel(self.draft["pts"]))]):
                self.draft["pts"] = self.arrange_funnel(self.draft["pts"])
                self.commit_draft()
            else:
                self.cancel_draft()
        elif kind == "free":
            pts = self.draft["pts"]
            if self.live_drawing() and len(pts) >= 3 and math.hypot(
                    e.x - self.t2x(pts[0][0]), e.y - self.p2y(pts[0][1])) < 12 * self.scale:
                pts.append(list(pts[0]))  # let go near its start: closed
            if len(pts) >= 2:
                # made perfect with the last sensitivity picked (smooth.py), as the roll looks now
                self.draft.update(smooth=self.app.free_smooth, k=self.sy / self.sx)
                self.commit_draft()
            else:
                self.cancel_draft()
        elif kind == "segment" and not still:  # dragged: the point goes where it was let go
            self.poly_point(self.draw_pt(e))
        elif kind == "arcdrag" and not still:  # dragged start -> end: now it bends with the mouse until a click
            (b0, p0), end = self.draft["pts"]
            self.draft["pts"] = [[b0, p0], [(b0 + end[0]) / 2, (p0 + end[1]) / 2], end]
            self.arc_bend = True
            self.request_redraw()
        elif kind == "place":
            if still:
                self.cancel_draft()
            elif self.keep_draft() or self.app.confirm_big([self.draft]):
                self.commit_draft()
            else:
                self.cancel_draft()
        elif kind in ("resize", "turn", "skew") or kind == "handle" and self.app.selected()["kind"] == "custom":
            self.app.sync_custom()  # (a stroke's point dragged: its gaps may have closed)
        elif kind == "stretch":  # the box goes back to its size, the shapes stay stretched (user)
            self.box_kept = (self.drag[4], set(self.app.sels))
            self.app.sync_panel()
        if kind in ("handle", "move", "stretch"):
            self.app.sync_funnel()  # its note count
        self.drag = None
        self.draw_select_box()  # (the box stays, see kept_box)

    def on_double(self, e):
        # Tk turns a quick second click into a double-click; only polylines use it, everything else gets a normal click
        if self.draft and self.draft["kind"] == "poly":
            self.finish_poly()
        elif self.app.tool.get() == "select" and self.text_at(e.x, e.y) is not None:
            self.edit_text(e)  # double-click a text: type in it
        elif self.app.tool.get() == "select" and self.picture_at(e) is not None:
            self.app.edit_picture(self.picture_at(e))  # double-click a picture: its look in the image window
        elif self.app.tool.get() == "select" and self.group_at(e) is not None:
            self.app.edit_between(self.group_at(e))  # double-click an Add between group: its window
        elif self.paste_spot(e):  # (user, like Domino: Select double-click on empty space pastes there)
            self.app.paste(whole=True, at=self.event_pt(e)[0])
        elif self.app.tool.get() == "text":
            self.text_double(e)  # the word there gets selected
        else:
            self.on_press(e)

    def picture_at(self, e):
        """The placed picture under the mouse (its number), or None."""
        i = self.shape_at(e.x, e.y, prefer_selected=True)
        return i if i is not None and "picture" in self.app.shapes[i] else None

    def group_at(self, e):
        """The shape of an Add between group under the mouse (its number), or None."""
        i = self.shape_at(e.x, e.y, prefer_selected=True)
        return i if i is not None and group_of(self.app.shapes[i]) else None

    def paste_spot(self, e):
        """A Select double-click here pastes the copied shapes (their start at the mouse's beat, snapped; keys
        stay): copied shapes (or shapes shared as text), empty space under the bar numbers, nothing being drawn."""
        app = self.app
        return (app.tool.get() == "select" and not self.draft
                and e.x >= self.kb_w and e.y >= self.ruler_h and self.shape_at(e.x, e.y) is None
                and not self.custom_hit(e.x, e.y) and not self.on_kept_box(self.kept_box(), e)
                and (app.clipboard and app.clip_kind != "stroke" or app.shared_clip(shapes_only=True) is not None))

    def on_motion(self, e):
        self.app.show_position(self.position_text(e))
        if not self.drag:
            over = not self.draft and self.hit_handle(e.x, e.y, self.app.tool.get() == "select") is not None
            # (the arrow of a shape put away above the top key can be grabbed: the move pointer shows it, user)
            over = over or (not self.draft and self.app.tool.get() == "select"
                            and self.above_mark_at(e.x, e.y) is not None)
            hit = None if over else self.custom_hit(e.x, e.y)
            on_box = (self.on_kept_box(self.kept_box(), e) if not over and not (hit and hit[0] != "inside")
                      and self.app.tool.get() == "select" and not self.draft else None)
            self.set_cursor("fleur" if over else BOX_CURSORS[on_box] if on_box else self.custom_cursor(hit))
        if self.follow:  # a shape started with a click follows the mouse
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.follow, self.drag = self.drag, None
        elif self.draft and self.draft["kind"] in ("poly", "arc"):
            self.draft["pts"][1 if self.arc_bend else -1] = self.draw_pt(e)
            self.request_redraw()

    def poly_point(self, pt):
        """The polyline being drawn gets its next point at pt (a new one then follows the mouse). True if that
        finished it (live drawing: back on its first point = closed)."""
        pts = self.draft["pts"]
        pts[-1] = pt
        pts.append(list(pt))
        if self.live_drawing() and len(pts) >= 4 and pt == pts[0]:
            self.finish_poly()
            return True
        self.request_redraw()
        return False

    def finish_arc(self):
        """The arc's third click: done (the end on the start = a whole circle), unless all three are the same point."""
        a, b, c = self.draft["pts"]
        if a == c == b:
            self.draft["pts"].pop()  # still waiting for the end
        else:
            self.commit_draft()

    def finish_poly(self):
        if not (self.draft and self.draft["kind"] == "poly"):
            return
        if self.drag and self.drag[0] == "segment":  # (right-click / Enter while a point is dragged: the drag ends
            self.drag = None                          # too, letting go does nothing)
        pts = []
        for p in self.draft["pts"][:-1]:  # the last point is the one following the mouse
            if not pts or p != pts[-1]:
                pts.append(p)
        if len(pts) >= 2:
            self.draft["pts"] = pts
            self.commit_draft()
        else:
            self.cancel_draft()

    def on_right(self, e):
        """Right-click: finish a polyline being drawn, otherwise deselect (any tool).
        A right-drag listens to the notes under the mouse instead (any tool). Nothing while the left button holds
        something (a point removed under a held one: wrong point / error)."""
        if self.holding():
            return
        finished = bool(self.draft and self.draft["kind"] in ("poly", "arc"))
        if self.follow:  # a shape started with a click, not finished: dropped
            self.drop_hz_keys()  # a new Hz bass's start mark goes too
            self.cancel_draft()
            finished = True
        elif finished:
            self.finish_poly()
            if self.draft and self.draft["kind"] == "arc":
                self.cancel_draft()  # an arc needs all three points
        # (a click that finished / dropped a shape being drawn does only that: not a point of the shape behind it)
        hid = None if self.draft or finished or self.sx is None else self.hit_handle(e.x, e.y, True)
        if isinstance(hid, tuple) and hid[0] == "pt":
            hid = None  # a stroke's point: right-click works as on the stroke (its menu)
        if isinstance(hid, tuple) and self.app.selected()["kind"] == "custom":  # a picked curve stroke's point
            self.delete_stroke_handle(self.app.selected(), hid)
            return
        if isinstance(hid, tuple):  # a funnel's curve start, anchor or handle: remove it
            self.delete_funnel_handle(self.app.selected(), hid, e)
            return
        if hid is not None and self.app.selected()["kind"] == "curve" and self.delete_curve_handle(
                self.app.selected(), hid):  # a curve's anchor (removed) or handle point (pulled back in)
            return
        if self.sx is not None and e.x >= self.kb_w:
            # deselects on release if it wasn't dragged (unless it just finished a polyline)
            self._scrub = {"x": e.x, "y": e.y, "tick": None, "deselect": not finished}
        elif not finished:
            self.cancel_draft()
            self.app.select(None)

    def scrub_tick(self, e):
        return max(0.0, self.x2t(max(e.x, self.kb_w))) * self.app.ppq

    def on_right_drag(self, e):
        self.app.show_position(self.position_text(e))
        sc = self._scrub
        if not sc:
            return
        if sc["tick"] is None:
            if abs(e.x - sc["x"]) < 4 and abs(e.y - sc["y"]) < 4:
                return
            self.app.stop_play()
            sc["tick"] = self.scrub_tick(e)
        tick = self.scrub_tick(e)
        if not self.app.scrub(sc["tick"], tick):
            self._scrub = None
            return
        sc["tick"] = tick

    def on_right_release(self, e):
        sc, self._scrub = self._scrub, None
        if not sc:
            return
        if sc["tick"] is None:
            i = None if self.draft else self.shape_at(sc["x"], sc["y"])  # (its notes count too)
            kept = None if self.draft or not self.app.sels else self.kept_box()
            if kept and sc["deselect"] and boxes_side([self.box_rect(a) for a in kept], sc["x"], sc["y"], 0) == (0, 0):
                # inside the kept Select boxes: the menu for all they selected, or (just one shape) that shape's own
                # menu, anywhere in the boxes (user)
                if len(self.app.sels) > 1:
                    self.show_box_menu(e)
                else:
                    self.show_menu(e, next(iter(self.app.sels)))
            elif i is not None and sc["deselect"]:  # near a shape (or on its notes): its menu
                self.show_menu(e, i)
            elif sc["deselect"]:
                self.cancel_draft()
                self.app.select(None)
        else:
            self.app.scrub_end()

    def commit_draft(self):
        self.keep_draft()
        sh, self.draft = self.draft, None
        if not self.live_commit(sh):  # a stroke of a live shape / a circle or polygon: done there
            self.app.add_shape(sh)

    def cancel_draft(self):
        self.end_typing()
        funnel = bool(self.draft and self.draft["kind"] == "funnel")
        self.draft = None
        self.drag = None
        self.follow = None
        self.arc_bend = False
        self.request_redraw()
        if funnel:
            self.app.sync_funnel()  # no longer waiting for a wall

    def on_middle_release(self, e):
        """A middle click (without dragging) on a selected polyline adds a point there, snapped.
        On a selected funnel: a curve start (on its line) or an anchor, like a click with the Funnel tool.
        On a selected curve: an anchor there, like a click with the Curve tool. Not while the left button holds
        something (a held point's number would change)."""
        sh = self.point_shape()
        if (not self._pan or self._panned or abs(e.x - self._pan[0]) > 3 or abs(e.y - self._pan[1]) > 3 or self.draft
                or self.drag or not sh or sh["kind"] not in ("poly", "funnel", "curve", "custom")
                or e.x < self.kb_w or e.y < self.ruler_h):
            return
        if sh["kind"] == "custom":  # its picked curve stroke: an anchor there
            self.stroke_click(sh, e, near=12 * self.scale)
            return
        if sh["kind"] == "funnel":
            self.funnel_click(sh, e)
            return
        if sh["kind"] == "curve":
            self.curve_click(sh, e, near=12 * self.scale)
            return
        self.insert_poly_point(sh, e)

    def insert_poly_point(self, sh, e):
        """A snapped point at the mouse, put in the polyline where it adds the least length."""
        pt = self.event_pt(e)
        pts = sh["pts"]
        if pt in pts:
            return
        screen = [(self.t2x(b), self.p2y(p)) for b, p in pts]
        q = (self.t2x(pt[0]), self.p2y(pt[1]))

        def extra_length(i):
            """How much longer the polyline gets if the point goes in before pts[i]."""
            if i == 0:
                return math.dist(q, screen[0])
            if i == len(pts):
                return math.dist(screen[-1], q)
            a, b = screen[i - 1], screen[i]
            return math.dist(a, q) + math.dist(q, b) - math.dist(a, b)

        i = min(range(len(pts) + 1), key=extra_length)
        self.app.push_undo(name=tr("pianoroll.add_a_point"))
        pts.insert(i, pt)
        self.app.shape_edited()

    def start_pan(self, e):
        self._pan = (e.x, e.y, self.view_t, self.view_top)
        self._panned = False

    def pan_to(self, e):
        if self.pan_view(e):
            self.draft_to_mouse(e)

    def pan_view(self, e):
        """The middle button dragged: the view moves with the mouse. False if it isn't panning."""
        if not self._pan or self.sx is None:
            return False
        x, y, t, top = self._pan
        if abs(e.x - x) > 3 or abs(e.y - y) > 3:
            self._panned = True  # (once it went further than 3 px it's a pan, not a click, even if it comes back)
        self.view_t = t - (e.x - x) / self.sx
        self.view_top = top + (e.y - y) / self.sy
        self.clamp_view()
        self.request_redraw()
        return True

    def draft_to_mouse(self, e):
        """The view moved under the mouse (wheel, middle-drag) while a new shape is drawn or a shape / point is held:
        it goes to the mouse at once (it stayed on the old spot in the song until the mouse moved, and letting go put
        it there)."""
        if self.app.vel.edit:  # (a velocity line held: its end goes to the mouse)
            return self.app.vel.follow_mouse()
        if self.sx is not None and self.holding():
            return self.on_drag(e)
        if self.sx is None or not (self.draft or self.follow):
            return
        if self.drag and self.drag[0] in ("create", "place", "segment", "arcdrag", "wall"):
            self.on_drag(e)
        elif not self.drag:
            self.on_motion(e)

    def on_wheel(self, e):
        if self.sx is None:
            return
        up = e.delta > 0
        f = 1.25 if up else 0.8
        zoom_time = e.state & CTRL and not e.state & ALT
        zoom_pitch = (e.state & CTRL and not e.state & SHIFT) or e.state & ALT
        if zoom_time:
            self.zoom_x(f, e.x)
        if zoom_pitch:
            self.zoom_y(f, e.y)
        if not (zoom_time or zoom_pitch):
            if e.state & SHIFT:
                self.view_t += (-1 if up else 1) * 120 / self.sx
            else:
                # (3 keys, as whole pixels: the picture of the notes can then be moved along, see roll_draw)
                self.view_top += (1 if up else -1) * max(1, round(3 * self.sy)) / self.sy
        self.clamp_view()
        self.request_redraw()
        self.draft_to_mouse(e)

    def on_key(self, e):
        if self.app.vel.edit:  # a velocity line held: Esc throws it away, other keys wait (its shapes stay put)
            if e.keysym.lower() == "escape":
                self.app.vel.drop()
            return None if e.state & CTRL else "break"  # (Ctrl+Z / Ctrl+Y and shortcuts: their own rules)
        if self.typing:
            return self.type_key(e)
        if self.app.box_drawn():  # (Esc, tool keys, Delete...: nothing until the Select box is let go, user)
            return "break"
        k = e.keysym.lower()
        if k == "escape":
            if self.app.vel.confirm():  # (done with a velocity line / curve, like Enter)
                return
            self.drop_hz_keys()
            self.cancel_draft()
            self.app.set_parts(())
            self.app.set_stroke(None)
        elif k in ("return", "kp_enter"):
            if not self.app.vel.confirm():  # Enter = done with a velocity line / curve
                self.finish_poly()
        elif k == "delete":
            sh = self.app.selected()
            if self.drag:
                self.delete_dragged()
            elif self.funnel_parts():
                self.delete_parts()
            elif self.picked_stroke(sh) is not None and len(self.app.sels) == 1:
                self.delete_stroke(sh, self.app.stroke)
            else:
                self.app.delete_selected()
        elif k == "g" and not e.state & CTRL:
            self.app.live.set(not self.app.live.get())
        elif k == "d" and e.state & CTRL:
            if not self.holding():
                self.app.duplicate()
        elif not e.state & CTRL:
            self.app.tool_hotkey(k)

    def delete_dragged(self):
        """Delete while the mouse is dragging: only the shape being dragged goes (user: the one grabbed, even when
        others move along; all the kept Select boxes hold when they were grabbed on empty space / stretched), the
        rest of the selection stays, and the drag ends. A drag that holds no shape (Select box, play line, Slice
        line, a shape being drawn) ignores it."""
        app, kind = self.app, self.drag[0]
        if kind == "move":
            gone = list(self.drag[2]) if self.grabbed is None or self.dup else [self.grabbed]
        elif kind == "stretch":
            gone = list(self.drag[3])
        elif kind in ("handle", "resize", "turn", "skew"):
            gone = [app.sel]
        else:
            return
        self.drag = self.box_moving = self.dup = None
        app.delete_selected(only=gone)

    def time_text(self, x):
        """bar:beat:tick at canvas x (the velocity pane shares the roll's x axis)"""
        ppq, beats = self.app.ppq, self.app.beats
        ticks = max(0, self.x2t(x) * ppq)
        bar, rest = int(ticks // (ppq * beats)) + 1, ticks % (ppq * beats)
        return tr("pianoroll.tick", bar=bar, rest=int(rest // ppq) + 1, rest2=int(rest % ppq), ticks=int(ticks))

    def position_text(self, e):
        if self.sx is None or e.x < self.kb_w or e.y < self.ruler_h:
            return None
        text = self.time_text(e.x)
        p = round(self.y2p(e.y))
        if 0 <= p < self.app.keys:
            text += f"     {note_name(p)} ({p})"
        return text
