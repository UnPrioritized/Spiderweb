"""What the piano roll's parts (and the velocity pane) share: colours, key names, modifier keys, shape outline
caches."""

import ctypes
import math
import os

import numpy as np

from files.about import ICONS
from files.system import ALT  # (used from here by the piano roll's parts)

from notes.engine import cached_path, cached_strokes  # (used from here by the piano roll's parts)
from window import look

PREVIEW_LIMIT = 200_000  # a custom shape / funnel being drawn with more notes than this previews as its outline only
PICK = 10  # how near (screen pixels) the mouse must be to a shape's line / stroke / funnel part to pick it
LONG_STROKE = 64  # a stroke with more points than this (freehand) shows only some of them until it's picked


def shown_points(n, picked):
    """Point numbers a stroke of n points shows: all when picked or short, else evenly spread ones (at most
    LONG_STROKE, both ends included), so every stroke has some to grab."""
    if picked or n <= LONG_STROKE:
        return range(n)
    step = -(-(n - 1) // (LONG_STROKE - 1))
    return sorted({*range(0, n, step), n - 1})


class _MouseMovePoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("time", ctypes.c_uint32),
                ("extra", ctypes.c_size_t)]


try:
    _get_trail = ctypes.windll.user32.GetMouseMovePointsEx
    _get_trail.argtypes = [ctypes.c_uint, ctypes.POINTER(_MouseMovePoint), ctypes.POINTER(_MouseMovePoint),
                           ctypes.c_int, ctypes.c_uint]
except (AttributeError, OSError):
    _get_trail = None


def mouse_trail(x_root, y_root, since):
    """Where the mouse was on the way to (x_root, y_root) on the screen: [(x, y, time)], oldest first, only moves
    after time `since` (ms). Windows keeps the last 64 positions; while the program is busy (redrawing) it only
    hands over the latest one, so a fast freehand stroke would lose its bends without this. since None: just the
    latest position (to start from). [] if unknown (e.g. the cursor was put there by a program, not moved)."""
    if _get_trail is None:
        return []
    now = _MouseMovePoint(x_root & 0xFFFF, y_root & 0xFFFF, 0, 0)
    buf = (_MouseMovePoint * 64)()
    n = _get_trail(ctypes.sizeof(_MouseMovePoint), ctypes.byref(now), buf, 64, 1)  # 1 = screen pixels
    out = []
    for p in buf[:max(n, 0)]:  # newest first
        if since is not None and not 0 < (p.time - since) & 0xFFFFFFFF < 0x80000000:
            break  # not newer than `since` (the clock wraps after 49 days)
        # a screen left of / above the main one gives coordinates past 32767
        out.append((p.x - 65536 if p.x > 32767 else p.x, p.y - 65536 if p.y > 32767 else p.y, p.time))
        if since is None:
            break
    return out[::-1]


BLACK = {1, 3, 6, 8, 10}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# The 31-EDO view (256 keys + its tick): 31 rows an octave, row 0 = C0. ^ = half sharp, v = half flat
EDO = 31
EDO_NAMES = ["C", "C^", "C#", "Db", "Dv", "D", "D^", "D#", "Eb", "Ev", "E", "E^", "Fv", "F", "F^", "F#", "Gb", "Gv",
             "G", "G^", "G#", "Ab", "Av", "A", "A^", "A#", "Bb", "Bv", "B", "B^", "Cv"]
EDO_WHITE = {0, 5, 10, 13, 18, 23, 28}  # the 7 plain notes
EDO_BLACK = {2, 3, 7, 8, 15, 16, 20, 21, 25, 26}  # sharps and flats (the other 14 rows: half sharps / half flats)
EDO_PIANO = range(23, 249)  # A0 .. C8, a real 88-key piano's range
# Note colours per channel, blue first: the first 15 = channels 1-9, 11-16 (a slot's channel, engine.CHANNELS), so
# a colour always means the same channel; the 16th (CH10) = channel 10's, used only when channel 10 is ("Use
# channel 10" / an image's own tick: slot_colours)
SLOT_COLORS = [("#7ea6f5", "#1f3a93"), ("#f58e8e", "#8f1f1f"), ("#8fd68f", "#1f6f1f"), ("#e6c65c", "#7a5f00"),
               ("#c79bf2", "#5a2a8f"), ("#6fd6d6", "#136b6b"), ("#f2a36b", "#8a4310"), ("#b0b0b0", "#404040"),
               ("#f59ad0", "#8f1f63"), ("#c2e36b", "#4f6b0f"), ("#9aa0f5", "#2a2f8f"), ("#c9a27e", "#5c3a1a"),
               ("#7fd6b0", "#135c3f"), ("#e38ae3", "#7a1f7a"), ("#8fb8d6", "#1f4a6b"), ("#3fae9f", "#0f4f47")]
CH10 = 15  # SLOT_COLORS' place of channel 10's colour
# slot -> SLOT_COLORS place: slot % len(table) looked up (channel 10 skipped / used, engine.slot_track_channel)
COLOURS_15 = np.arange(15, dtype=np.int64)
COLOURS_16 = np.array(list(range(9)) + [CH10] + list(range(9, 15)), np.int64)


def slot_colours(use10):
    """The slot -> colour table (COLOURS_16 while channel 10 is used: slot k = channel k)."""
    return COLOURS_16 if use10 else COLOURS_15


SELECTED_COLOR = ("#ffb65c", "#9a4b00")
DRAFT_COLOR = ("#9be39b", "#1d6b1d")
GHOST_COLOR = look.GHOST_NOTE  # (notes a mask hides: mask.py)
if look.DARK:  # (the dark look's own, lighter: look.NOTES_DARK)
    SLOT_COLORS, SELECTED_COLOR, DRAFT_COLOR = list(look.NOTES_DARK), look.SELECTED_DARK, look.DRAFT_DARK

SHIFT, CTRL = 0x1, 0x4
# the Select tool's mouse pointer: a cross (its middle = the spot pointed at) with a small dotted box
SELECT_CURSOR = "{@" + os.path.join(ICONS, "select.cur").replace("\\", "/") + "}"
GRAB_CURSOR = "{@" + os.path.join(ICONS, "grab.cur").replace("\\", "/") + "}"  # a closed hand
PENCIL_CURSOR = "{@" + os.path.join(ICONS, "pencil.cur").replace("\\", "/") + "}"  # its tip = the spot pointed at


def set_cursor(widget, name):
    """widget's mouse pointer (kept in widget._cursor, set only when it changes); one of our own files that can't be
    read (Linux) = a built-in one instead."""
    if getattr(widget, "_cursor", None) == name:
        return
    widget._cursor = name
    try:
        widget.config(cursor=name)
    except Exception:  # (tk.TclError: the pointer's file can't be read)
        widget.config(cursor={SELECT_CURSOR: "arrow", PENCIL_CURSOR: "pencil"}.get(name, "arrow"))
PIANO_88 = range(21, 109)  # A0 to C8, the keys of a real piano
BOX_STILL = 4  # a Select box moved less than this many pixels from where it started is still a click
BOX_SCROLL_MS = 100  # a Select box dragged past the edge scrolls the view a beat (3 keys up / down) this often
RULER_TOP = 1000.5  # the top of a Select box made along the ruler (every key; no box dragged on a piano roll gets
#                     this high, so the two kinds stay apart: ruler_boxes; both piano rolls)
VEL_TOP = 2000.5  # the same made in the velocity pane: the ruler isn't black under it (user 2026-10-22)
# px per beat: how far the wheel / bars / buttons zoom time (both rolls). Low enough for Fit view on any song (at
# 0.05 a very long one went past it, and "-" then zoomed in)
MIN_SX, MAX_SX = 1e-5, 100000.0


def grid_span(a, b, step, step_b=None):
    """The beats (lo, hi) a Select box covers, pressed at a and dragged to b, on the snap grid. The pressed corner
    is the grid line before a, whichever way the box goes (user: it jumped a step when dragged leftwards); the
    mouse's corner goes out to the grid line after b (right of it) / before b (left of it), so it jumps a whole
    grid step as soon as the mouse crosses a line (the pencil goes to the NEAREST line instead). step 0 / None =
    not snapped. step_b = b's own step (user, Shift pressed or let go while dragging changes only the mouse's
    corner, the first one stays as Shift was at the press)."""
    step_b = step if step_b is None else step_b
    if step:
        a = math.floor(a / step + 1e-9) * step
    least = step_b or step or 0  # (never thinner than a step)
    if b < a:
        return min(math.floor(b / step_b + 1e-9) * step_b if step_b else b, a - least), a
    return a, max(math.ceil(b / step_b - 1e-9) * step_b if step_b else b, a + least)


def grab_while_panning(widget):
    """A closed hand while the middle button is held down on the widget (it scrolls the view), then the pointer
    it had. Call after the widget's own middle button bindings."""
    def press(e):
        widget._before_grab = widget.cget("cursor")
        widget.config(cursor=GRAB_CURSOR)

    def release(e):
        if getattr(widget, "_before_grab", None) is not None:
            widget.config(cursor=widget._before_grab)
            widget._before_grab = None
    widget.bind("<ButtonPress-2>", press, add="+")
    widget.bind("<ButtonRelease-2>", release, add="+")


def bind_double_right(widget, single, double):
    """Right clicks on the widget: single(e), and double(e) for a double right click. Clicks spammed in a row go in
    pairs (user, like many MIDI programs: every 2nd click is a double click; Tk alone makes every click after the
    first one a double click)."""
    widget._right_paired = False

    def press(e):
        widget._right_paired = False
        return single(e)

    def twice(e):
        widget._right_paired = not widget._right_paired
        return double(e) if widget._right_paired else single(e)
    widget.bind("<ButtonPress-3>", press)
    widget.bind("<Double-Button-3>", twice)


def line_touches_box(px, py, x0, y0, x1, y1):
    """A line through the screen points (px, py: arrays) touches the box x0 < x1, y0 < y1 (one point: is in it)."""
    if len(px) == 1:
        return x0 <= px[0] <= x1 and y0 <= py[0] <= y1
    # each piece clipped to the box (Liang-Barsky): something is left = it touches
    ax, ay, dx, dy = px[:-1], py[:-1], np.diff(px), np.diff(py)
    t0, t1, out = np.zeros(len(ax)), np.ones(len(ax)), np.zeros(len(ax), bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
            out |= (p == 0) & (q < 0)
            r = q / p
            t0 = np.where(p < 0, np.maximum(t0, r), t0)
            t1 = np.where(p > 0, np.minimum(t1, r), t1)
    return bool((~out & (t0 <= t1)).any())


def box_upright(area):
    """A Select box's (time, pitch, time, pitch) corners in order: (left, top, right, bottom)."""
    return min(area[0], area[2]), max(area[1], area[3]), max(area[0], area[2]), min(area[1], area[3])


def box_side(rect, x, y, reach):
    """Where (x, y) is on a Select box shown at rect (x0, y0, x1, y1): (sx, sy), sx -1 its left side / 1 its right
    side / 0 neither, sy -1 its top / 1 its bottom / 0 neither ((0, 0) = inside), or None (not on it). reach: how
    close to a side counts (px)."""
    x0, y0, x1, y1 = rect
    if not (x0 - reach <= x <= x1 + reach and y0 - reach <= y <= y1 + reach):
        return None
    sx = 0 if min(abs(x - x0), abs(x - x1)) > reach else -1 if abs(x - x0) < abs(x - x1) else 1
    sy = 0 if min(abs(y - y0), abs(y - y1)) > reach else -1 if abs(y - y0) < abs(y - y1) else 1
    if not sx and not sy and not (x0 <= x <= x1 and y0 <= y <= y1):
        return None
    return sx, sy


def boxes_side(rects, x, y, reach):
    """box_side for several Select boxes kept together (Ctrl+drag adds one): inside any of them = (0, 0); a side
    counts only on the outside of them all (the box around them all), where one of them reaches."""
    if len(rects) == 1:
        return box_side(rects[0], x, y, reach)
    if not any(box_side(r, x, y, reach) for r in rects):
        return None
    side = box_side(boxes_around(rects), x, y, reach)
    if side and side != (0, 0):
        return side
    return (0, 0) if any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects) else None


def boxes_around(rects):
    """The one box (x0, y0, x1, y1) around several (x0 < x1, y0 < y1 each)."""
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def ruler_boxes(boxes):
    """The Select boxes made along the ruler (top RULER_TOP: every key): the ruler is black under them. They and
    boxes dragged on the piano roll are never kept together (user): starting one kind drops the other."""
    return [a for a in boxes or () if a[1] >= RULER_TOP - 0.5]


def black_bands(boxes):
    """The ruler_boxes made along the ruler itself (not in the velocity pane): the ruler is black only under them."""
    return [a for a in ruler_boxes(boxes) if a[1] < VEL_TOP - 0.5]


def box_kind(a):
    """"vel" (made in the velocity pane), "ruler" (along the ruler) or "box" (dragged on the piano roll). Making a
    box of one kind drops the kept ones of the others, Ctrl too (user 2026-10-22)."""
    return "vel" if a[1] >= VEL_TOP - 0.5 else "ruler" if a[1] >= RULER_TOP - 0.5 else "box"


def boxes_upright(areas):
    """Several Select boxes' (time, pitch, time, pitch) corners as box_upright, and the one around them all."""
    boxes = [box_upright(a) for a in areas]
    return boxes, (min(b[0] for b in boxes), max(b[1] for b in boxes), max(b[2] for b in boxes),
                   min(b[3] for b in boxes))


def _cut(lo, hi, spans):
    """The pieces of lo..hi left when the spans [(a, b)] are taken out of it."""
    out = [(lo, hi)]
    for a, b in spans:
        out = [p for c, d in out for p in ((c, min(d, a)), (max(c, b), d)) if p[1] - p[0] > 1e-9]
    return out


def boxes_outline(rects):
    """The outline of several Select boxes on screen as one shape (user: boxes that touch or overlap are joined,
    the lines inside gone): [(x0, y0, x1, y1)] straight pieces."""
    out = []
    for i, (x0, y0, x1, y1) in enumerate(rects):
        others = rects[:i] + rects[i + 1:]
        for y, up in ((y0, True), (y1, False)):  # (a piece goes where another box covers the side just past it)
            spans = [(r[0], r[2]) for r in others if (r[1] < y <= r[3] if up else r[1] <= y < r[3])]
            out += [(a, y, b, y) for a, b in _cut(x0, x1, spans)]
        for x, left in ((x0, True), (x1, False)):
            spans = [(r[1], r[3]) for r in others if (r[0] < x <= r[2] if left else r[0] <= x < r[2])]
            out += [(x, a, x, b) for a, b in _cut(y0, y1, spans)]
    return out


def draw_boxes(canvas, rects, left, top, scale, **kw):
    """The Select boxes' joined outline (boxes_outline), a solid black line centred on the box's edges (user), cut
    off left of x = left / above y = top. 3 px at 100% (user: 2 was hard to see on dense shapes)."""
    width = max(3, round(3 * scale))
    for x0, y0, x1, y1 in boxes_outline(rects):
        if x1 < left or y1 < top:
            continue
        canvas.create_line(max(x0, left), max(y0, top), max(x1, left), max(y1, top), fill=look.SELECT_BOX, width=width,
                           capstyle="projecting", **kw)


# the pointer on a Select box's side / corner / inside (box_side)
BOX_CURSORS = {(-1, 0): "sb_h_double_arrow", (1, 0): "sb_h_double_arrow", (0, -1): "sb_v_double_arrow",
               (0, 1): "sb_v_double_arrow", (-1, -1): "size_nw_se", (1, 1): "size_nw_se", (1, -1): "size_ne_sw",
               (-1, 1): "size_ne_sw", (0, 0): "fleur"}


def fade(color, amount=0.72):
    """color mixed towards the piano roll's background (white in the light look)"""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    to = [int(look.ROLL_BG[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(c + (t - c) * amount) for c, t in zip((r, g, b), to))


def note_name(p, edo=False):
    if edo:  # (Cv sits just under the next octave's C)
        return f"{EDO_NAMES[p % EDO]}{p // EDO + (p % EDO == EDO - 1)}"
    return f"{NOTE_NAMES[p % 12]}{p // 12 - 1}"
