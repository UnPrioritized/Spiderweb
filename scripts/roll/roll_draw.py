"""Piano roll: painting the grid, notes, shapes, handles, keyboard and ruler."""

import json
import math
import time
import tkinter as tk

import numpy as np

from notes.custom import custom_note_count, edge_inner, gap_lines, role_of
from files.lang import tr
from files.speed import Photo, loops
from notes.engine import cached_arrays, shape_notes_tracks
from notes.joined import all_tumours
from notes.funnel import funnel_curves, funnel_handle_lines, funnel_lines, funnel_note_count, funnel_origins
from notes.paths import KEYS
from notes.sliced import moved_by, moved_mark
from roll.roll_shared import (BLACK, DRAFT_COLOR, PIANO_88, PREVIEW_LIMIT, SELECTED_COLOR, SLOT_COLORS,
                              draw_boxes, fade, note_name)

HANDLE_COLOR = "#0050d0"
STROKE_POINT_COLOR = "#7a1fe0"  # the points of a custom shape's strokes (purple, like a picked stroke)
PART_COLOR = "#7a1fe0"  # a highlighted funnel line / the curve clicked
TWIN_COLOR = "#00a39a"  # the curves linked to it
# Note colours: number = colour * 32 + velocity // 4 (low velocity = paler fill, the outline stays); colours = the
# slot colours, then selected (unused: a selected shape's notes keep their colours, user; a red line goes round
# them, draw_ring), then the shape being drawn
SELECTED, DRAFT = len(SLOT_COLORS), len(SLOT_COLORS) + 1
NOTE_COLORS = [(fade(f, 1 - level * 4 / 124), b) for f, b in SLOT_COLORS + [SELECTED_COLOR, DRAFT_COLOR]
               for level in range(32)]
RING_COLOR = "#b40000"  # (user: #e00000 looked bright, almost pink)
RING_RGB = np.frombuffer(bytes.fromhex(RING_COLOR[1:]), np.uint8)
PREVIEW_COLOR, PREVIEW_HALO = "#ff1f1f", "#ffa8a8"  # the outline gate's preview line (draw_edge_preview)
RING_GAP = 4  # px: notes in a row closer than this count as touching for the ring
RING_MAX = 20000  # more ring pieces on screen than this: none drawn (canvas items are slow)
CUT_MARK = 12  # px each side of a sliced piece's cut end: its stretch of the Slice line (draw_cut_marks)
FAINT_CUT = "#f0c8c8"  # the really faint line from a piece's cut end to the other piece's (user)


def piece_runs(x0, y0, x1, y1):
    """Straight pieces (lists of their ends) joined where one's end is another's: runs of (piece, forward), each
    run one line (a closed loop starts and ends on the same spot)."""
    def spot(x, y):
        return round(x, 3), round(y, 3)
    at = {}
    for i in range(len(x0)):
        at.setdefault(spot(x0[i], y0[i]), []).append(i)
        at.setdefault(spot(x1[i], y1[i]), []).append(i)
    used = [False] * len(x0)

    def follow(i, fwd):
        """From piece i's far end onward (fwd: going from its start to its end)."""
        out = []
        while True:
            end = spot(x1[i], y1[i]) if fwd else spot(x0[i], y0[i])
            nxt = next((j for j in at[end] if not used[j]), None)
            if nxt is None:
                return out
            used[nxt] = True
            fwd = spot(x0[nxt], y0[nxt]) == end
            out.append((nxt, fwd))
            i = nxt
    runs = []
    for i in range(len(x0)):
        if not used[i]:
            used[i] = True
            ahead = follow(i, True)
            back = follow(i, False)
            runs.append([(j, not f) for j, f in reversed(back)] + [(i, True)] + ahead)
    return runs


def ring_parts(notes, gap):
    """
    The outline round the selected shapes' notes (start, end, key), small gaps in a row (< gap ticks) closed:
    (sides, tops). sides = (tick, key0, key1, right): one side line over keys key0..key1. tops = (t0, t1, key, up):
    the top (up = 1) or bottom (0) edge of a key row's notes where the row beside it has none.
    """
    s, e, k = (notes[:, i].astype(np.int64) for i in (0, 1, 2))
    big = int(e.max()) + int(gap) + 2
    order = np.lexsort((s, k))
    s, e = s[order] + k[order] * big, e[order] + k[order] * big
    reach = np.maximum.accumulate(e)
    new = np.ones(len(s), bool)
    new[1:] = s[1:] > reach[:-1] + gap
    first = np.flatnonzero(new)
    last = np.append(first[1:], len(s)) - 1
    rk = s[first] // big
    ra, rb = s[first] - rk * big, reach[last] - rk * big  # runs: key, start, end
    # sides: one per run end, joined over neighbouring keys at the same tick
    sides = []
    for t, right in ((ra, 0), (rb, 1)):
        o = np.lexsort((rk, t))
        tt, kk = t[o], rk[o]
        start = np.ones(len(tt), bool)
        start[1:] = (tt[1:] != tt[:-1]) | (kk[1:] != kk[:-1] + 1)
        f = np.flatnonzero(start)
        l = np.append(f[1:], len(tt)) - 1
        sides.append(np.column_stack([tt[f], kk[f], kk[l], np.full(len(f), right)]))
    # tops / bottoms: a row's runs minus the next row's (events +1 / -1 for this row, +2 / -2 for the other)
    tops = []
    for up, dk in ((1, 1), (0, -1)):
        kk = np.concatenate([rk, rk, rk - dk, rk - dk])
        t = np.concatenate([ra, rb, ra, rb])
        v = np.concatenate([np.ones_like(ra), -np.ones_like(ra), np.full_like(ra, 2), np.full_like(ra, -2)])
        o = np.lexsort((t, kk))
        kk, t, v = kk[o], t[o], np.cumsum(v[o])
        on = np.flatnonzero((v[:-1] == 1) & (t[1:] > t[:-1]) & (kk[1:] == kk[:-1]))
        tops.append(np.column_stack([t[on], t[on + 1], kk[on], np.full(len(on), up)]))
    return np.concatenate(sides), np.concatenate(tops)


def ring_chains(xa, ya, xb, yb, up, extra):
    """Upright (up) / flat pieces from (xa, ya) to (xb, yb), both ends drawn, joined where an end is another
    piece's end or one pixel diagonally beside it (an inner corner of a staircase; a one-pixel diagonal step draws
    no pixel of its own): coordinate lists for create_line, the last point pushed on by extra (a line leaves out
    its last pixel), so the same pixels as one line per piece reaching extra past its end."""
    ends = {}
    for i, (p, q) in enumerate(zip(zip(xa, ya), zip(xb, yb))):
        ends.setdefault(p, []).append(i)
        ends.setdefault(q, []).append(i)
    used = [False] * len(xa)

    def onward(at):  # an unused piece with an end at this point, else at a diagonal neighbour: (piece, its end)
        for dx, dy in ((0, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            p = (at[0] + dx, at[1] + dy)
            for j in ends.get(p, ()):
                if not used[j]:
                    return j, p
        return None, None

    def walk(i, at):  # [(piece, from, to)]: from piece i's end at, on while another piece goes on from where it ends
        out = []
        while True:
            used[i] = True
            p, q = (xa[i], ya[i]), (xb[i], yb[i])
            nxt = q if at == p else p
            out.append((i, at, nxt))
            i, at = onward(nxt)
            if i is None:
                return out

    lines = []
    for i in range(len(xa)):
        if used[i]:
            continue
        p = (xa[i], ya[i])
        ahead = walk(i, p)
        j, at = onward(p)
        back = walk(j, at) if j is not None else []
        pts = []
        for _, a, b in reversed(back):
            pts += (b, a)
        for _, a, b in ahead:
            pts += (a, b)
        last, a, (lx, ly) = ahead[-1]
        # the last piece's direction (a one-pixel piece: along its own kind)
        dx, dy = (lx > a[0]) - (lx < a[0]), (ly > a[1]) - (ly < a[1])
        if (dx, dy) == (0, 0):
            dx, dy = (0, 1) if up[last] else (1, 0)
        pts[-1] = (lx + dx * extra, ly + dy * extra)
        # (points repeated where pieces meet: dropped)
        pts = [pt for n, pt in enumerate(pts) if not n or pt != pts[n - 1]]
        lines.append([v for pt in pts for v in pt])
    return lines


NOTE_RGB = np.array([[[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pair] for pair in NOTE_COLORS], np.uint8)
# placed pictures' notes: 16 more colour groups after these, the picture's own colours (no paler shades: the colour IS
# the picture; the outline a darker shade of it). note_tables() adds them for the colours in use.
PICTURE_GROUP = len(NOTE_COLORS) // 32
_tables = {}


def note_tables(pal):
    """(NOTE_COLORS, NOTE_RGB) with the picture colours pal ("RRGGBB" list or None) added as groups
    PICTURE_GROUP.. (grey where there's none)."""
    key = tuple(pal or ())
    got = _tables.get(key)
    if got is None:
        extra = []
        for k in range(16):
            h = pal[k] if pal and k < len(pal) else "C0C0C0"
            rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
            dark = "#%02x%02x%02x" % tuple(int(c * 0.6) for c in rgb)
            extra += [("#" + h.lower(), dark)] * 32
        colors = NOTE_COLORS + extra
        rgb = np.concatenate([NOTE_RGB, np.array([[[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pair]
                                                  for pair in extra], np.uint8)])
        got = _tables[key] = (colors, rgb)
        if len(_tables) > 20:
            _tables.pop(next(iter(_tables)))
    return got


def screen_lines(path, ax, bx, ay, by, view):
    """
    A (beat, pitch) path as screen coordinate lists for create_line (x = beat * ax + bx, y = pitch * ay + by).
    Only what can show goes to the canvas, which paints tens of thousands of points very slowly (a line with
    tumours a tick apart has that many): parts outside view = (x0, y0, x1, y1) are left out (the line is split
    there), and of the points in one pixel column only the first, lowest, highest and last are kept (a dense
    zigzag looks just the same).
    """
    x0, y0, x1, y1 = view
    pts = np.asarray(path, float).reshape(-1, 2)
    x, y = pts[:, 0] * ax + bx, pts[:, 1] * ay + by
    xa, xb, ya, yb = x[:-1], x[1:], y[:-1], y[1:]
    on = ~(((xa < x0) & (xb < x0)) | ((xa > x1) & (xb > x1)) | ((ya < y0) & (yb < y0)) | ((ya > y1) & (yb > y1)))
    # pieces: runs of on-screen segments (segment k runs from point k to k + 1)
    edge = np.diff(np.concatenate([[False], on, [False]]).astype(np.int8))
    first, last = np.nonzero(edge == 1)[0], np.nonzero(edge == -1)[0]  # points first .. last of each piece
    if not len(first):
        return []
    size = last - first + 1
    idx = np.repeat(first, size) + np.arange(int(size.sum())) - np.repeat(np.cumsum(size) - size, size)
    piece = np.repeat(np.arange(len(first)), size)
    x, y = x[idx], y[idx]
    col = np.trunc(x)
    new = np.ones(len(idx), bool)
    new[1:] = (piece[1:] != piece[:-1]) | (col[1:] != col[:-1])
    at = np.nonzero(new)[0]  # each pixel column's run of points
    group = np.cumsum(new) - 1
    n = np.arange(len(idx))
    keep = np.zeros(len(idx), bool)
    keep[at] = True
    keep[np.append(at[1:], len(idx)) - 1] = True
    for pick in (np.minimum, np.maximum):  # the first lowest and first highest point of each run
        best = pick.reduceat(y, at)
        keep[np.minimum.reduceat(np.where(y == best[group], n, len(idx)), at)] = True
    counts = np.bincount(piece[keep], minlength=len(first)) * 2
    coords = np.column_stack([x, y])[keep].ravel().tolist()
    out, k = [], 0
    for c in counts.tolist():
        if c >= 4:
            out.append(coords[k:k + c])
        k += c
    return out


class RollDrawing:
    """Mixed into PianoRoll."""

    def request_redraw(self, delay=None):
        """Redraw once the program is idle; delay (ms): not sooner than that (while drawing freehand, so the
        mouse keeps being read instead of the whole roll being painted after every move)."""
        if delay:
            if not self._redraw_pending and not self._late_redraw:
                self._late_redraw = self.after(delay, self._redraw_late)
            return
        if self._late_redraw:
            self.after_cancel(self._late_redraw)
            self._late_redraw = None
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self.redraw)
        pane = getattr(self.app, "vel", None)
        if pane:
            pane.request_redraw()  # same notes, same time axis

    def _redraw_late(self):
        self._late_redraw = None
        self.request_redraw()

    def draft_moved(self):
        """The shape being drawn / placed changed with the mouse: redrawn. When making and painting its notes is slow,
        only its lines follow the mouse (the other notes' picture stays): its notes show once the mouse rests."""
        if self._draft_rest:
            self.after_cancel(self._draft_rest)
            self._draft_rest = None
        self.draft_moving = self._draft_time + self.paint_time > 0.15  # (as for other drags: app.shapes_changed)
        if self.draft_moving:
            self._draft_rest = self.after(250, self._draft_rested)
        self.request_redraw()

    def _draft_rested(self):
        self._draft_rest = None
        self.draft_moving = False
        self.request_redraw()
        if self.draft and self.draft["kind"] == "custom":
            self.after_idle(self.show_draft)  # (its note count)

    def draft_notes(self):
        """The notes of the shape being drawn / placed (made once for each spot it's at), or None: none, too many to
        preview (only its lines show), or slow to make while the mouse moves (draft_moved)."""
        d, app = self.draft, self.app
        if not d or self.draft_moving:
            return None
        n = (custom_note_count(d, app.ppq) if d["kind"] == "custom" else
             funnel_note_count(d, app.ppq) if d["kind"] == "funnel" else None)
        if (n or 0) > PREVIEW_LIMIT:
            return None
        key = (json.dumps(d, sort_keys=True), app.ppq, app.keys)  # (app.notes_tracks' key: kept for it, keep_draft)
        if not self._draft_made or self._draft_made[0] != key:
            started = time.perf_counter()
            self._draft_made = (key, shape_notes_tracks(d, app.ppq, app.keys))
            self._draft_time = time.perf_counter() - started
        return self._draft_made[1][0]

    def draft_count(self):
        """How many notes the shape being drawn / placed makes, or None while they're still to be made."""
        d, ppq = self.draft, self.app.ppq
        n = (custom_note_count(d, ppq) if d["kind"] == "custom" else
             funnel_note_count(d, ppq) if d["kind"] == "funnel" else None)
        if n is None:
            notes = self.draft_notes()
            n = None if notes is None else len(notes)
        return n

    def show_draft(self, text=None):
        """The status line while placing: where the mouse is (text; None: as last time) and how many notes the new
        shape makes, once they're known."""
        text = self._draft_text = self._draft_text if text is None else text
        n = self.draft_count() if self.draft else None
        self.app.show_position(text if n is None else tr("pianoroll.new_shape_notes", text=text, note_count=n))

    def keep_draft(self):
        """The shape being drawn / placed is added: the app takes the notes already made for it."""
        app = self.app
        if self.draft and self._draft_made and self._draft_made[0] == (json.dumps(self.draft, sort_keys=True),
                                                                       app.ppq, app.keys):
            app._notes_cache[self._draft_made[0]] = self._draft_made[1]
        self._draft_made = None

    def redraw(self):
        self._redraw_pending = False
        for bar in self.bars:
            bar.refresh()
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if self.sx is None or w < 50:
            return
        app = self.app
        rows, cols = self.grid_parts(w, h)
        # the picture below shows exactly this: when nothing here changed (e.g. dragging a shape whose notes catch up
        # later), it's shown again as it is instead of being painted again
        fixed = (frozenset(app.sels), self.sx, self.sy, self.kb_w, self.ruler_h, w, h)
        pic = (app.rendered, (fixed, self.view_t, self.view_top, tuple(rows), tuple(cols)))
        # (a shape being drawn / placed: its notes are in the picture, unless only its lines show)
        drafted = self.draft_notes() is not None
        old = None if drafted or not app.show_notes.get() or self.note_img is None else self._note_pic
        if self._exact:
            self.after_cancel(self._exact)
            self._exact = None
        moved = self.pan_pixels(old, pic, w, h)
        carried = self.carried()
        if carried is None:
            self._carry = None
        if app.notes_late and not self.drag and not app._late_notes:  # (a drag called off: the notes catch up)
            app._notes_rested()
        if carried is not None and w > self.kb_w and h > self.ruler_h:
            self.paint_carried(w, h, rows, cols, fixed, *carried)
            self._note_pic = None
        elif old is not None and old[0] is pic[0] and old[1] == pic[1]:
            self.create_image(int(self.kb_w), int(self.ruler_h), image=self.note_img, anchor="nw")
        elif moved:
            # only the view moved, by whole pixels: the picture is moved along and just the new edge is painted.
            # (A pixel here and there can round the other way, so it's painted whole once the view rests.)
            self.shift_image(w, h, rows, cols, *moved)
            self._note_pic = pic
            self._exact = self.after(300, self.paint_exact)
        else:
            started = time.perf_counter()
            rects = self.note_rects(w, h) if app.show_notes.get() else None
            if rects is not None and len(rects[0]) > w * h // 2000:
                # Lots of notes: paint grid + notes as one picture (thousands of canvas items redraw slowly)
                self.paint_image(w, h, rows, cols, rects)
                self._note_pic = None if drafted else pic
            else:
                self.note_img = self._note_pic = self._img = None
                self.draw_grid(w, h, rows, cols)
                colors = note_tables(app.picture_pal)[0]
                for x0, y0, x1, y1, color in zip(*(v.tolist() for v in rects or ())):
                    self.create_rectangle(x0, y0, x1, y1, fill=colors[color][0], outline=colors[color][1])
            self.paint_time = time.perf_counter() - started
        if carried is None:  # (while shapes are dragged their notes are stamped along: no ring)
            self.draw_ring(w, h)
            self.draw_edge_preview(w, h)
        # a line with tumours / a curve with a pattern: the line as drawn (the origin path), faint and dashed under it
        for i, sh in enumerate(app.shapes):
            if ((sh.get("pattern") or sh.get("shape") or any(tm["on"] for tm in all_tumours(sh)))
                    and (i in app.sels or app.show_lines.get())):
                self.draw_path(dict(sh, tumour=None, tumours=None, pattern=None, shape=None),
                               "#e89a9a" if i in app.sels else "#efc0c0", 1, dash=(6, 4))
            if sh["kind"] in ("funnel", "custom") and (i in app.sels or app.show_lines.get()):  # its curves' / strokes'
                for path in funnel_origins(sh) if sh["kind"] == "funnel" else (p for _, p in self.origin_strokes(sh)):
                    self.create_line(*[v for b, p in path for v in (self.t2x(b), self.p2y(p))], width=1,
                                     fill="#e89a9a" if i in app.sels else "#efc0c0", dash=(6, 4))
        if app.show_lines.get():
            for i, sh in enumerate(app.shapes):
                if i not in app.sels:
                    self.draw_path(sh, "#c0392b", 1)
        for i in app.sels:
            self.draw_path(app.shapes[i], "#ff1f1f", 2)
        for i in app.sels:
            if i < len(app.shapes) and "picture" in app.shapes[i]:
                self.draw_picture_label(app.shapes[i])
        self.draw_cut_marks()
        sel = self.point_shape()
        if sel and app.parts:
            self.draw_parts()
        if sel and sel["kind"] == "custom":
            self.draw_picked_stroke(sel)
        if sel:
            if sel["kind"] == "custom" and app.tool.get() != "text":
                self.draw_custom_box(sel)
            self.draw_handles(sel)  # on top: a stroke's point wins over the box's squares
        if self.draft:
            self.draw_path(self.draft, "#0a8f0a", 2)
            self.draw_draft_points()
        self.draw_hz_start()
        self.draw_above_marks()
        self.draw_select_box()
        self.draw_slice()
        self.draw_keyboard(h)
        self.draw_ruler(w)
        self.draw_playhead()
        if self.typing:
            self.show_caret()

    def draw_picture_label(self, sh):
        """A selected placed picture: its name, keys and colours above its box's top left corner (only while
        selected, user)."""
        (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
        xs = [self.t2x(b) for b in (b0, b1, b2, b1 + b2 - b0)]
        ys = [self.p2y(p) for p in (p0, p1, p2, p1 + p2 - p0)]
        p = sh["picture"]
        text = tr("image.label", name=sh.get("name") or "?", keys=p["grid"][1], colours=len(p["set"].get("pal", ())))
        x, y = min(xs), min(ys) - 4
        t = self.create_text(x + 5, y - 9, text=text, anchor="w", font=("TkDefaultFont", 9))
        bx = self.bbox(t)
        if bx:
            r = self.create_rectangle(bx[0] - 4, bx[1] - 2, bx[2] + 4, bx[3] + 2, fill="#fffbe6", outline="#c9b26b")
            self.tag_lower(r, t)

    def ring_now(self):
        """The red line round the selected shapes' notes (user: the notes keep their colours, even with short
        gates): ring_parts, worked out once per selection / notes / zoom, or None."""
        app = self.app
        if not app.sels or not app.show_notes.get() or not len(app.rendered):
            return None
        ppq, ax = app.ppq, self.sx / app.ppq
        key = (frozenset(app.sels), self.sx, ppq)
        if self._ring is None or self._ring[0] is not app.rendered or self._ring[1] != key:
            notes = app.rendered[np.isin(app.rendered[:, 5], list(app.sels))]
            self._ring = (app.rendered, key, ring_parts(notes, RING_GAP / ax) if len(notes) else None)
        return self._ring[2]

    def draw_ring(self, w, h):
        """The ring as lines on the canvas, when the notes are canvas rectangles (in the notes' picture it's painted
        with them: ring_pixels)."""
        parts = self.ring_now()
        if parts is None or self.note_img is not None:
            return
        # on the notes' edge pixels, growing inward (user); a wider line as 1 px lines side by side (joined lines
        # are drawn with whole pixels only that way)
        for d in range(max(1, round(self.scale))):  # (user: 2 px was too thick)
            self.draw_ring_lines(parts, (self.kb_w, self.ruler_h, w, h), -d, 1, fill=RING_COLOR, width=1)

    def ring_pixels(self, region):
        """The ring's pixels inside region = (x0, y0, x1, y1) (ends not included): where = row * the region's width
        + column, the same pixels draw_ring's lines cover. None if there's no ring."""
        parts = self.ring_now()
        if parts is None:
            return None
        kb, top, w, h = region
        iw = w - kb
        at = []
        for d in range(max(1, round(self.scale))):
            sx, sy0, sy1, tx0, tx1, ty = self.ring_screen(parts, -d, 0)  # (ends included)
            for x0, x1, y0, y1 in ((sx, sx, np.maximum(sy0, top), np.minimum(sy1, h - 1)),
                                   (np.maximum(tx0, kb), np.minimum(tx1, w - 1), ty, ty)):
                on = (x1 >= x0) & (y1 >= y0) & (x0 >= kb) & (x1 < w) & (y0 >= top) & (y1 < h)
                x0, x1, y0, y1 = (v[on].astype(np.int64) for v in (x0, x1, y0, y1))
                n = (x1 - x0) + (y1 - y0) + 1  # (each piece is upright or flat)
                step = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
                flat = np.repeat(x1 > x0, n)
                x, y = np.repeat(x0, n) + step * flat, np.repeat(y0, n) + step * ~flat
                at.append((y - top) * iw + (x - kb))
        return np.concatenate(at)

    def ring_screen(self, parts, d, extra):
        """ring_parts' pieces on screen, d pixels outside the notes' edge: sides x, y0, y1 and tops x0, x1, y
        (extra: how far past each corner)."""
        sides, tops = parts
        ax = self.sx / self.app.ppq
        bx = self.kb_w - self.view_t * self.sx
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy

        def row(k, lower):  # a key row's top / bottom pixel, as note_rects has it
            y0 = np.round((k + 0.5) * ay + by)
            return np.maximum(np.round((k - 0.5) * ay + by), y0 + 1) - 1 if lower else y0

        sx = np.round(sides[:, 0] * ax + bx) + np.where(sides[:, 3] == 1, d - 1, -d)  # (a note's last pixel: end - 1)
        sy0, sy1 = row(sides[:, 2], False) - d, row(sides[:, 1], True) + d + extra
        tx0, tx1 = np.round(tops[:, 0] * ax + bx) - d, np.round(tops[:, 1] * ax + bx) - 1 + d + extra
        ty = np.where(tops[:, 3] == 1, row(tops[:, 2], False) - d, row(tops[:, 2], True) + d)
        return sx, sy0, sy1, tx0, tx1, ty

    def draw_edge_preview(self, w, h):
        """While the outline gate box is being used (app.edge_preview = its gate in beats): a red line where the
        selected shapes' outline would reach inside, worked out from what's typed (custom.edge_inner): with the
        even band the shape shrunk inward, drawn like a shape's line."""
        app = self.app
        g = getattr(app, "edge_preview", None)
        if not g:
            return
        bx = self.kb_w - self.view_t * self.sx  # (x = beat * sx + bx, y = key * ay + by)
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        for i in app.sels:
            sh = app.shapes[i] if i < len(app.shapes) else None
            if sh and sh["kind"] == "custom" and "notes" not in sh:
                got = edge_inner(dict(sh, edge=g), app.ppq)
                if got is None or not len(got[1]):
                    continue
                # (user: red like a shape's line, a bit thicker than 1 px but never as thick as the selected
                # shape's 2 px: a red pixel with a light red one beside it)
                if got[0] == "rows":
                    parts, view = ring_parts(got[1], 0), (self.kb_w, self.ruler_h, w, h)
                    self.draw_ring_lines(parts, view, -1, 1, fill=PREVIEW_HALO, width=1)
                    self.draw_ring_lines(parts, view, 0, 1, fill=PREVIEW_COLOR, width=1)
                    continue
                segs = got[1]
                x0, y0 = segs[:, 0] * self.sx + bx, segs[:, 1] * ay + by
                x1, y1 = segs[:, 2] * self.sx + bx, segs[:, 3] * ay + by
                on = ~((np.maximum(x0, x1) < self.kb_w) | (np.minimum(x0, x1) > w) |
                       (np.maximum(y0, y1) < self.ruler_h) | (np.minimum(y0, y1) > h))
                ln = np.maximum(np.hypot(x1 - x0, y1 - y0), 1e-9)
                nx, ny = -(y1 - y0) / ln, (x1 - x0) / ln  # (the halo one pixel to the side: the inner one)
                xi, yi = segs[:, 4] * self.sx + bx, segs[:, 5] * ay + by
                flip = np.where(nx * (xi - (x0 + x1) / 2) + ny * (yi - (y0 + y1) / 2) < 0, -1, 1)
                nx, ny = nx * flip, ny * flip
                # (pieces joined end to end into a few long lines: one canvas item per piece made every step of
                # the box slow)
                x0, y0, x1, y1, nx, ny = (v[on].tolist() for v in (x0, y0, x1, y1, nx, ny))
                for run in piece_runs(x0, y0, x1, y1):
                    line, halo = [], []
                    for n, (i, fwd) in enumerate(run):
                        pts = [(x0[i], y0[i]), (x1[i], y1[i])][::1 if fwd else -1]
                        if not n:
                            line += pts[0]
                            halo += (pts[0][0] + nx[i], pts[0][1] + ny[i])
                        # (the halo's corner: the two pieces' sides averaged)
                        j = run[n + 1][0] if n + 1 < len(run) else i
                        hx, hy = nx[i] + nx[j], ny[i] + ny[j]
                        d = math.hypot(hx, hy)
                        hx, hy = (hx / d, hy / d) if d > 1e-6 else (nx[i], ny[i])
                        line += pts[1]
                        halo += (pts[1][0] + hx, pts[1][1] + hy)
                    self.create_line(*halo, fill=PREVIEW_HALO, width=1)
                    self.create_line(*line, fill=PREVIEW_COLOR, width=1, capstyle="round")

    def draw_ring_lines(self, parts, view, d, extra, **kw):
        """ring_parts' (sides, tops) as lines on screen, d pixels outside the notes' edge (extra: how far the
        lines reach past each corner), those in view = (x0, y0, x1, y1)."""
        kb, top, w, h = view
        sx, sy0, sy1, tx0, tx1, ty = self.ring_screen(parts, d, extra)
        on_s = ~((sx < kb) | (sx > w) | (sy1 < top) | (sy0 > h))
        on_t = ~((tx1 < kb) | (tx0 > w) | (ty < top) | (ty > h))
        if on_s.sum() + on_t.sum() > RING_MAX:
            return
        # (pieces meeting at a corner joined into one line: one canvas item per piece made panning slow, ~3000
        # pieces cost 90 ms a step)
        on_t &= tx1 > tx0  # (a note under a pixel wide: nothing, as a line of no length)
        xa = np.concatenate([sx[on_s], tx0[on_t]])
        ya = np.concatenate([sy0[on_s], ty[on_t]])
        up = np.arange(len(xa)) < on_s.sum()
        xb = np.where(up, xa, np.concatenate([sx[on_s], tx1[on_t]]) - extra)
        yb = np.where(up, np.concatenate([sy1[on_s], ty[on_t]]) - extra, ya)
        for line in ring_chains(xa.tolist(), ya.tolist(), xb.tolist(), yb.tolist(), up.tolist(), extra):
            self.create_line(*line, **kw)

    def draw_above_marks(self):
        """A small arrow pointing up at the top of the piano roll for each shape put away above its highest key
        (pianoroll.above_marks; user: one, in the middle of the shape). Selected: red like its line."""
        s, y = self.scale, self.ruler_h + 3 * self.scale
        for i, x in self.above_marks():
            self.create_polygon(x, y, x + 6 * s, y + 9 * s, x - 6 * s, y + 9 * s, outline="#ffffff",
                                fill="#ff1f1f" if i in self.app.sels else "#c0392b")

    def draw_cut_marks(self):
        """A selected piece's cut ends (notes/sliced.py; user, 2026-10-06): a Slice cut = a short stretch of the Slice
        tool's dashed line, the way it was drawn; a Split here cut = small scissors beside it. From each, a really
        faint line to where the other piece's end is now (none while they touch, or once that piece is gone; a copy's
        marks pair only with the pieces copied with it)."""
        app = self.app
        if not app.sels:
            return
        spots = {}  # mark id -> [(shape, mark, x, y)]
        for i, sh in enumerate(app.shapes):
            d = moved_by(sh) if sh.get("cut") else None
            if d is None:
                continue
            for m in sh["cut"]["marks"]:
                m = moved_mark(m, d)
                spots.setdefault(m["id"], []).append((i, m, self.t2x(m["at"][0]), self.p2y(m["at"][1])))
        s = self.scale
        for pair in spots.values():
            for i, m, x, y in pair:
                if i not in app.sels:
                    continue
                for j, _, x2, y2 in pair:
                    if j != i and math.hypot(x2 - x, y2 - y) > 3 * s:
                        self.create_line(x, y, x2, y2, fill=FAINT_CUT, width=1, dash=(2, 4))
                if m.get("segs"):  # (a custom shape's: along its cut edge, over its solid line there)
                    for (b0, k0), (b1, k1) in m["segs"]:
                        xy = self.t2x(b0), self.p2y(k0), self.t2x(b1), self.p2y(k1)
                        self.create_line(*xy, fill="#ffffff", width=max(1, round(2 * s)) + 1)
                        self.create_line(*xy, fill="#d00000", width=max(1, round(2 * s)), dash=(6, 3))
                elif m["kind"] == "slice":
                    dx, dy = m.get("dir", (0, 1))
                    dx, dy = dx * self.sx, -dy * self.sy
                    ln = math.hypot(dx, dy) or 1
                    dx, dy = dx / ln * CUT_MARK * s, dy / ln * CUT_MARK * s
                    self.create_line(x - dx, y - dy, x + dx, y + dy, fill="#d00000", width=max(1, round(2 * s)),
                                     dash=(6, 3))
                else:
                    self.draw_scissors(x + 9 * s, y - 9 * s, s)

    def draw_scissors(self, x, y, s):
        """Small scissors (two rings, two crossed blades) centred on x, y, drawn with lines so every system shows
        them."""
        r, w = 2.5 * s, max(1, round(1.5 * s))
        for side in (-1, 1):
            cx, cy = x + side * 3 * s, y + 4.5 * s
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#d00000", width=w)
            self.create_line(cx - side * 1 * s, cy - r, x - side * 3 * s, y - 6 * s, fill="#d00000", width=w)

    def draw_select_box(self):
        """The box being dragged with Select (with the ones kept when Ctrl+drag adds it), or the last ones
        (kept_box): one outline, boxes that touch or overlap joined."""
        self.delete("selbox")
        if self.drag and self.drag[0] == "box":
            boxes = self.box_more + [b for b in (self.box_area(),) if b]
        else:
            boxes = self.kept_box() or []
        draw_boxes(self, [self.box_rect(b) for b in boxes], self.kb_w, self.ruler_h, self.scale,
                   tags="selbox")

    def draw_slice(self):
        """The Slice tool's line while it's dragged: dashed, a dot at each end."""
        self.delete("slice")
        if not (self.drag and self.drag[0] == "slice"):
            return
        (xa, ya), (xb, yb) = (self.to_xy(p) for p in self.drag[1:3])
        s = self.scale
        self.create_line(xa, ya, xb, yb, fill="#d00000", width=max(1, round(2 * s)), dash=(6, 3), tags="slice")
        r = 3 * s
        for x, y in ((xa, ya), (xb, yb)):
            self.create_oval(x - r, y - r, x + r, y + r, fill="#d00000", outline="", tags="slice")

    def draw_playhead(self):
        self.delete("playhead")
        x = round(self.t2x(self.app.playhead))
        w, h = self.winfo_width(), self.winfo_height()
        if self.kb_w <= x <= w:
            s, top = self.scale, self.ruler_h
            self.create_line(x, top, x, h, fill="#0a50e0", width=max(1, round(s)), tags="playhead")
            self.create_polygon(x - 5 * s, top - 8 * s, x + 5 * s, top - 8 * s, x, top, fill="#0a50e0",
                                tags="playhead")

    def show_playhead(self, start=False):
        """Move the play line; while playing, turn the page when it gets near the right edge
        (start: also jump to it if it's off screen)."""
        if self.sx is None:
            return
        x, w = self.t2x(self.app.playhead), self.winfo_width()
        if self.app.player.running and (x > w - 6 * self.scale or (start and x < self.kb_w)):
            self.view_t = self.app.playhead
            self.clamp_view()
            self.request_redraw()  # the full redraw puts the line back too
            return
        self.draw_playhead()
        pane = getattr(self.app, "vel", None)
        if pane:
            pane.draw_playhead()

    def visible_pitches(self, h):
        return max(0, math.ceil(self.y2p(h) - 0.5)), min(self.app.keys - 1, math.floor(self.y2p(self.ruler_h) + 0.5))

    def grid_parts(self, w, h):
        """
        The grid as rows = [(y0, y1, color)] bands (y1 None = 1-pixel line), painted in order,
        and cols = [(x, color)] vertical lines painted over them.
        All in whole pixels, rounded like the notes: the canvas and the picture (paint_image) then draw the very
        same rows, whichever is used.
        """
        top = self.ruler_h
        rows = [(top, h, "#ffffff")]
        p_lo, p_hi = self.visible_pitches(h)
        for p in range(p_lo, p_hi + 1):
            y0, y1 = self.row_y(p)
            if p % 12 in BLACK:
                rows.append((y0, y1, "#e7eefa"))
            elif self.sy >= 4:
                rows.append((y1, None, "#dfe6f2"))
        for p in range(p_lo, p_hi + 1):
            if p % 12 == 0:
                rows.append((self.row_y(p)[1], None, "#606060"))
        if self.p2y(-0.5) < h:
            rows.append((self.row_y(0)[1], h, "#ececec"))
        if self.p2y(self.app.keys - 0.5) > top:
            rows.append((top, self.row_y(self.app.keys - 1)[0], "#ececec"))
        return rows, self.grid_cols(w)

    def row_y(self, p):
        """Top and bottom of key p's row, in whole pixels (the same rounding as the notes)."""
        return round(self.p2y(p + 0.5)), round(self.p2y(p - 0.5))

    def grid_cols(self, w):
        """Vertical grid lines [(x, color)]: snap lines, beats, bars (the velocity pane uses them too)."""
        cols = []
        b_lo, b_hi = self.x2t(self.kb_w), self.x2t(w)
        sb = self.app.snap_beats()
        if sb and sb < 1 and sb * self.sx >= 8:
            i = math.ceil(b_lo / sb)
            while i * sb <= b_hi:
                b = i * sb
                if abs(b - round(b)) > 1e-9:
                    cols.append((round(self.t2x(b)), "#eef1f6"))
                i += 1
        beats = self.app.beats
        if self.sx >= 5:
            for b in range(max(0, math.ceil(b_lo)), math.floor(b_hi) + 1):
                if b % beats:
                    cols.append((round(self.t2x(b)), "#bcc4d2"))
        step = beats
        while step * self.sx < 6:
            step *= 2
        b = math.floor(b_lo / step) * step
        while b <= b_hi:
            if b >= 0:
                cols.append((round(self.t2x(b)), "#3a3a3a"))
            b += step
        return cols

    def draw_grid(self, w, h, rows, cols):
        x0, top = self.kb_w, self.ruler_h
        for y0, y1, color in rows:
            if y1 is None:
                self.create_line(x0, y0, w, y0, fill=color)
            else:
                self.create_rectangle(x0, y0, w, y1, fill=color, outline="")
        for x, color in cols:
            self.create_line(x, top, x, h, fill=color)

    def sorted_notes(self):
        """The rendered notes sorted by start (kept until the notes change)."""
        rendered = self.app.rendered
        if self._note_index is None or self._note_index[0] is not rendered:
            order = np.take(rendered, np.argsort(np.ascontiguousarray(rendered[:, 0]), kind="stable"), axis=0)
            self._note_index = (rendered, order, int((order[:, 1] - order[:, 0]).max()) if len(order) else 0,
                                np.ascontiguousarray(order[:, 0]))
        return self._note_index[1]

    def visible_range(self, t_lo, t_hi):
        """(first, end): the sorted_notes() rows that can overlap ticks t_lo..t_hi."""
        self.sorted_notes()
        longest, starts = self._note_index[2:]
        return int(np.searchsorted(starts, t_lo - longest, "left")), int(np.searchsorted(starts, t_hi, "right"))

    def visible_notes(self, t_lo, t_hi):
        """Rendered notes (array rows) that can overlap ticks t_lo..t_hi."""
        lo, hi = self.visible_range(t_lo, t_hi)
        return self.sorted_notes()[lo:hi]

    def note_rects(self, w, h, clip=None, area=None, only=None):
        """On-screen notes as whole-pixel rectangles, skipping exact repeats: NumPy arrays (x0, y0, x1, y1, colour),
        colour = a number in NOTE_COLORS. Painted in this order (selected shapes' notes, then the draft, on top).
        clip = (x0, y0, x1, y1): only the notes that touch this part of the screen (the same rectangles).
        area = (x0, y0, x1, y1): as if the note area were there (it can reach past the screen).
        only: True = just the selected shapes' notes, False = all but theirs (no draft either way)."""
        app, ppq = self.app, self.app.ppq
        ax, bx = self.sx / ppq, self.kb_w - self.view_t * self.sx  # x = tick * ax + bx
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy  # y = pitch * ay + by
        kb, top, w, h = area or (self.kb_w, self.ruler_h, w, h)
        keys = np.arange(app.keys)
        row0, row1 = np.round((keys + 0.5) * ay + by).astype(np.int64), np.round((keys - 0.5) * ay + by).astype(np.int64)
        shown = ~((row1 < top) | (row0 > h))  # each key's row on screen?
        row1 = np.maximum(row1, row0 + 1)
        if clip:
            shown &= ~((row1 < clip[1]) | (row0 >= clip[3]))
            notes = self.visible_notes(self.x2t(clip[0] - 2) * ppq, self.x2t(clip[2] + 2) * ppq)
            notes = notes[shown[np.clip(notes[:, 2], 0, app.keys - 1)]]
        else:
            notes = self.visible_notes(self.x2t(kb) * ppq, self.x2t(w) * ppq)
        if only is not None and app.sels:
            notes = notes[np.isin(notes[:, 5], list(app.sels)) == only]
        sels = list(app.sels)

        def screen(notes, color, order):
            """These notes' x0, x1, key and colour number, the ones on screen only. order: put in painting order
            (shapes drawn later go on top, clicks pick that one too; then the selected shapes' notes on top)."""
            key = notes[:, 2]
            # (the same sums as one note at a time, so the same pixels)
            x0, x1 = np.round(notes[:, 0] * ax + bx), np.round(notes[:, 1] * ax + bx)
            on = ~((x1 < kb) | (x0 > w)) & shown[key]
            if clip:
                on &= ~((x1 < clip[0]) | (x0 >= clip[2]))
            on = np.flatnonzero(on)
            if order and len(on):
                owner = notes[on, 5]
                lo, hi = owner.min(), owner.max()
                if lo != hi:
                    on = on[np.argsort(owner.astype(np.uint16) if 0 <= lo and hi < 65536 else owner, kind="stable")]
                    owner = notes[on, 5]
                color = notes[on, 4] % len(SLOT_COLORS)
                pics = getattr(app, "picture_owners", ())
                if len(pics) and pics.any():  # a placed picture's notes: its own colours (PICTURE_GROUP on)
                    mine = pics[np.clip(owner, 0, len(pics) - 1)]
                    color = np.where(mine, PICTURE_GROUP + np.minimum(notes[on, 4], 15), color)
                if sels:
                    mine = np.isin(owner, sels)
                    if mine.any():
                        last = np.concatenate([np.nonzero(~mine)[0], np.nonzero(mine)[0]])
                        on, color = on[last], color[last]
            elif order:
                color = np.zeros(0, np.int64)
            # low velocity = paler fill (outline stays); 32 shades is plenty
            return (x0[on].astype(np.int64), x1[on].astype(np.int64), key[on],
                    color * 32 + np.minimum(notes[on, 3], 127) // 4)

        fast = loops()
        main = self.fast_screen(fast, notes, ax, bx, kb, w, shown, clip) if fast else None
        parts = [screen(notes, None, True) if main is None else main]
        drafted = self.draft_notes() if only is None else None
        if drafted is not None:
            parts.append(screen(drafted, DRAFT, False))
        x0, x1, key, color = (np.concatenate(v) if len(v) > 1 else v[0] for v in zip(*parts))
        left, right = int(kb) - 2, int(w) + 2
        if fast and (not len(key) or (key.min() >= 0 and key.max() < app.keys)):
            return fast.last_on_pixels(*(np.ascontiguousarray(v, np.int64) for v in (x0, x1, key, color)),
                                       left, right, app.keys, row0, row1)
        x0, x1 = np.maximum(x0, left), np.minimum(x1, right)
        # zoomed out, lots of notes land on the very same pixels: only the last of them shows (it's painted over
        # the others), so the others are left out. Found with a table, for notes up to 3 pixels long (the many).
        n = len(x0)
        if n > 1:
            size = x1 - x0
            small = np.flatnonzero((size < 4) & (size >= 0))
            spot = ((x0[small] - left) * app.keys + key[small]) * 4 + size[small]
            last = np.empty((right - left + 1) * app.keys * 4, np.int64)
            last[spot] = small  # (a repeated index keeps the last one written)
            keep = np.ones(n, bool)
            keep[small] = last[spot] == small
            if not keep.all():
                x0, x1, key, color = x0[keep], x1[keep], key[keep], color[keep]
        # the outline goes inside the note (user): its last pixel is the one before the note's end / next row
        return x0, row0[key], np.maximum(x1 - 1, x0), row1[key] - 1, color

    def fast_screen(self, fast, notes, ax, bx, kb, w, shown, clip):
        """note_rects' screen(notes, None, True) by the compiled loop, or None when it can't be used here (keys or
        owners outside the tables: the NumPy way then)."""
        app = self.app
        if len(notes):
            key, owner = notes[:, 2], notes[:, 5]
            if key.min() < 0 or key.max() >= len(shown) or owner.min() < 0:
                return None
            owners = int(owner.max()) + 1
        else:
            owners = 1
        sel = np.zeros(owners, bool)
        sel[[s for s in app.sels if 0 <= s < owners]] = True
        rank = np.empty(owners, np.int64)  # painting order: shapes in their order, the selected ones last
        rank[~sel] = np.arange(owners - int(sel.sum()))
        rank[sel] = np.arange(owners - int(sel.sum()), owners)
        pics = np.asarray(getattr(app, "picture_owners", ()), bool)
        return fast.screen_notes(np.ascontiguousarray(notes, np.int64), float(ax), float(bx), float(kb), float(w),
                                 np.ascontiguousarray(shown, bool), bool(clip),
                                 float(clip[0]) if clip else 0.0, float(clip[2]) if clip else 0.0, rank, owners,
                                 pics if pics.any() else np.zeros(0, bool), len(SLOT_COLORS), PICTURE_GROUP)

    def paint_image(self, w, h, rows, cols, rects):
        """Grid and notes as one picture over the note area."""
        kb, top = int(self.kb_w), int(self.ruler_h)
        if w - kb < 1 or h - top < 1:
            return
        self._img = self.paint_region(rows, cols, rects, (kb, top, w, h))
        self.show_image()

    def show_image(self, moved=None, strips=()):
        """The picture's pixels go on screen. moved = (dx, dy) with strips: the picture shown is the same one moved
        that far, but for these strips (x0, y0, x1, y1 inside the picture), so only they are sent."""
        img = self._img
        ih, iw = img.shape[:2]
        if self.note_img is None or (self.note_img.width(), self.note_img.height()) != (iw, ih):
            self._photo = Photo(self, iw, ih)
            self.note_img = self._photo.photo
            moved = self._shown = None
        name = self.note_img.name
        if moved:
            dx, dy = moved
            self.tk.call(name, "copy", name, "-from", max(-dx, 0), max(-dy, 0), iw + min(-dx, 0), ih + min(-dy, 0),
                         "-to", max(dx, 0), max(dy, 0))
        elif self._shown is not None and self._shown.shape == img.shape:
            # only the part that differs from what's shown is sent (sending pixels is the slow part)
            was = self._shown.reshape(ih, -1)
            rows = np.flatnonzero((was != img.reshape(ih, -1)).any(axis=1))
            strips = []
            if len(rows):
                y0, y1 = int(rows[0]), int(rows[-1]) + 1
                cols = np.flatnonzero((was[y0:y1] != img.reshape(ih, -1)[y0:y1]).any(axis=0)) // 3
                strips = [(int(cols[0]), y0, int(cols[-1]) + 1, y1)]
        else:
            strips = [(0, 0, iw, ih)]
        for x0, y0, x1, y1 in strips:
            self._photo.put(img[y0:y1, x0:x1], x0, y0)
        self._shown = img
        self.create_image(int(self.kb_w), int(self.ruler_h), image=self.note_img, anchor="nw")

    def pan_pixels(self, old, pic, w, h):
        """(dx, dy): how far the picture painted for old has to move to show pic, if the view just moved by whole
        pixels (less than half the picture) and nothing else changed; else None."""
        if old is None or self._img is None or old[0] is not pic[0] or old[1][0] != pic[1][0]:
            return None
        dx, dy = (old[1][1] - pic[1][1]) * self.sx, (pic[1][2] - old[1][2]) * self.sy
        if abs(dx - round(dx)) > 1e-6 or abs(dy - round(dy)) > 1e-6:
            return None
        dx, dy = round(dx), round(dy)
        ih, iw = self._img.shape[:2]
        if (dx, dy) == (0, 0) or abs(dx) > iw // 2 or abs(dy) > ih // 2:
            return None
        return dx, dy

    def shift_image(self, w, h, rows, cols, dx, dy):
        """The picture moved by (dx, dy) pixels; the strips that come into view are painted."""
        kb, top = int(self.kb_w), int(self.ruler_h)
        old = self._img
        ih, iw = old.shape[:2]
        img = np.empty_like(old)
        img[max(dy, 0):ih + min(dy, 0), max(dx, 0):iw + min(dx, 0)] = (
            old[max(-dy, 0):ih + min(-dy, 0), max(-dx, 0):iw + min(-dx, 0)])
        strips = []
        if dx:
            strips.append((kb, top, kb + dx, h) if dx > 0 else (w + dx, top, w, h))
        if dy:
            strips.append((kb, top, w, top + dy) if dy > 0 else (kb, h + dy, w, h))
        for x0, y0, x1, y1 in strips:
            rects = self.note_rects(w, h, clip=(x0, y0, x1, y1))
            img[y0 - top:y1 - top, x0 - kb:x1 - kb] = self.paint_region(rows, cols, rects, (x0, y0, x1, y1))
        self._img = img
        self.show_image((dx, dy), [(x0 - kb, y0 - top, x1 - kb, y1 - top) for x0, y0, x1, y1 in strips])

    def carried(self):
        """While shapes are dragged to another place and their notes are left for later (app.shapes_changed):
        (dx, dy), how far they are from where the notes on screen have them, in pixels. Else None."""
        app = self.app
        if not (self.drag and self.drag[0] == "move" and app.notes_late and app.show_notes.get()) or self.draft:
            return None
        j = next(iter(self.drag[2]), None)
        if j is None or j >= len(app.shapes) or j >= len(app.rendered_pts):
            return None
        (b, p), (b0, p0) = app.shapes[j]["pts"][0], app.rendered_pts[j]
        return round((b - b0) * self.sx), round((p0 - p) * self.sy)

    def paint_carried(self, w, h, rows, cols, fixed, dx, dy):
        """The picture while shapes are dragged with lots of notes about: the other shapes' notes painted once, and
        the dragged shapes' notes as they were, put over them (dx, dy) pixels further every time. (What they really
        turn into at the new place, overlaps and all, is worked out when the mouse is let go.)"""
        app = self.app
        kb, top = int(self.kb_w), int(self.ruler_h)
        iw, ih = w - kb, h - top
        key = (fixed, self.view_t, self.view_top)
        c = self._carry
        if c is None or c["rendered"] is not app.rendered or c["key"] != key:
            rects = self.note_rects(w, h, only=False)
            c = self._carry = {"rendered": app.rendered, "key": key, "area": None,
                               "base": self.paint_region(rows, cols, rects, (kb, top, w, h), ring=False)}
        a = c["area"]  # the dragged notes' pixels are kept for the part of the screen they come from, and around it
        if a is None or a[0] > kb - dx or a[1] > top - dy or a[2] < w - dx or a[3] < h - dy:
            a = c["area"] = (kb - dx - iw // 2, top - dy - ih // 2, w - dx + iw // 2, h - dy + ih // 2)
            at, c["rgb"] = self.note_pixels(self.note_rects(w, h, area=a, only=True), a)
            c["y"], c["x"] = np.divmod(at, a[2] - a[0])
        x, y = c["x"] + (a[0] - kb + dx), c["y"] + (a[1] - top + dy)
        on = (x >= 0) & (x < iw) & (y >= 0) & (y < ih)
        img = c["base"].copy()
        img[y[on], x[on]] = c["rgb"][on]
        self._img = img
        self.show_image()

    def paint_exact(self):
        """The picture painted whole again (after it was moved along with the view)."""
        if self._exact:
            self.after_cancel(self._exact)
        self._exact = None
        self._note_pic = None
        self.request_redraw()

    def paint_region(self, rows, cols, rects, region, ring=True):
        """Grid and notes of region = (x0, y0, x1, y1) of the screen (the ends not included) as an array of pixels
        (rows, columns, RGB), with the selected notes' red line over them (ring; draw_ring's lines were slow
        canvas items: ~1000 of them took 25 ms of every step when panning)."""
        kb, top, w, h = region
        iw, ih = w - kb, h - top
        rgb = {}

        def px(color):
            if color not in rgb:
                rgb[color] = bytes.fromhex(color[1:])
            return rgb[color]

        # Every pixel row of the grid is one of a few patterns: its colour with the vertical lines on top
        row_color = ["#ffffff"] * ih
        for y0, y1, color in rows:
            a = int(y0) - top
            b = a + 1 if y1 is None else int(y1) - top
            for y in range(max(a, 0), min(b, ih)):
                row_color[y] = color
        patterns = {}
        for color in set(row_color):
            line = bytearray(px(color) * iw)
            for x, c in cols:
                x = int(x) - kb
                if 0 <= x < iw:
                    line[x * 3:x * 3 + 3] = px(c)
            patterns[color] = bytes(line)
        img = np.frombuffer(b"".join(patterns[c] for c in row_color), np.uint8).reshape(ih, iw, 3).copy()
        fast = loops()
        if fast:  # (the compiled loops: the colours go straight in, no list of pixels in between)
            pal = getattr(getattr(self, "app", None), "picture_pal", None)
            fast.colour_in(img.reshape(-1, 3), self.top_pixels(fast, rects, region),
                           np.ascontiguousarray(rects[4], np.int64), note_tables(pal)[1])
        else:
            at, colors = self.note_pixels(rects, region)
            img.reshape(-1, 3)[at] = colors
        at = self.ring_pixels(region) if ring else None
        if at is not None:
            img.reshape(-1, 3)[at] = RING_RGB
        return img

    def note_pixels(self, rects, region):
        """The pixels of note_rects' notes inside region = (x0, y0, x1, y1): (where, colours), where = row * the
        region's width + column."""
        kb, top, w, h = region
        iw, ih = w - kb, h - top
        fast = loops()
        if fast:
            top_px = self.top_pixels(fast, rects, region)
            idx = np.arange(len(rects[0]))
        else:
            top_px, idx = self.top_pixels_numpy(rects, region)
        at = np.flatnonzero(top_px >= 0)
        k = top_px[at]
        pal = getattr(getattr(self, "app", None), "picture_pal", None)  # (the Hz window shares this, no pictures)
        return at, note_tables(pal)[1][rects[4][idx][k >> 1], k & 1]

    @staticmethod
    def top_pixels(fast, rects, region):
        """note_pixels' top_px by the compiled loop (every note numbered, also the ones off the region)."""
        kb, top, w, h = (int(v) for v in region)
        top_px = np.full((h - top) * (w - kb), -1, np.int64)
        fast.note_top(top_px, kb, top, w, h, *(np.ascontiguousarray(v, np.int64) for v in rects[:4]))
        return top_px

    def top_pixels_numpy(self, rects, region):
        """(top_px, idx): which note is on top at every pixel of region (the last one painted) as its number in
        idx (the notes that reach the region) * 2 + 1 if that pixel is its outline; -1 = no note."""
        kb, top, w, h = region
        iw, ih = w - kb, h - top
        # Same pixels as a canvas rectangle with a 1-pixel outline: x0..x1 and y0..y1 inclusive.
        x0, y0, x1, y1, color = rects
        cx0, cx1 = np.maximum(x0, kb), np.minimum(x1 + 1, w)
        cy0, cy1 = np.maximum(y0, top), np.minimum(y1 + 1, h)
        n, rh = cx1 - cx0, cy1 - cy0
        seen = (n > 0) & (rh > 0)
        idx = np.nonzero(seen)[0]
        x0, y0, x1, y1, cx0, cx1, cy0, cy1, n, rh = (v[seen] for v in (x0, y0, x1, y1, cx0, cx1, cy0, cy1, n, rh))
        solid = (x1 - x0 < 2) | (y1 - y0 < 2)  # too small to have a fill: all outline
        top_px = np.full(ih * iw, -1, np.int64)  # note number * 2 + (1 = outline), the highest wins
        small = n * rh < 256
        # small notes: pixel by pixel
        i = np.nonzero(small)[0]
        if len(i):
            r = np.repeat(i, rh[i])  # one entry per pixel row of each note
            ry = cy0[r] + np.arange(len(r)) - np.repeat(np.cumsum(rh[i]) - rh[i], rh[i])
            p = np.repeat(np.arange(len(r)), n[r])  # then one per pixel
            ri = r[p]
            x = cx0[ri] + np.arange(len(p)) - np.repeat(np.cumsum(n[r]) - n[r], n[r])
            y = ry[p]
            outline = (solid[ri] | (y == y0[ri]) | (y == y1[ri]) | ((x == x0[ri]) & (cx0[ri] == x0[ri]))
                       | ((x == x1[ri]) & (cx1[ri] == x1[ri] + 1)))
            np.maximum.at(top_px, (y - top) * iw + (x - kb), ri * 2 + outline)
        # big ones: as blocks (fill first, the outline over it: the same note, so the outline wins)
        grid = top_px.reshape(ih, iw)
        big = np.nonzero(~small)[0]
        for k, a, b, c, d, ya, yb, xa, xb, full in zip(big.tolist(), *(v[big].tolist() for v in (
                cy0 - top, cy1 - top, cx0 - kb, cx1 - kb, y0 - top, y1 - top, x0 - kb, x1 - kb, solid))):
            np.maximum(grid[a:b, c:d], k * 2 + full, out=grid[a:b, c:d])
            if not full:
                edge = k * 2 + 1
                for yy in (ya, yb):
                    if a <= yy < b:
                        np.maximum(grid[yy, c:d], edge, out=grid[yy, c:d])
                if c == xa:
                    np.maximum(grid[a:b, c], edge, out=grid[a:b, c])
                if d == xb + 1:
                    np.maximum(grid[a:b, d - 1], edge, out=grid[a:b, d - 1])
        return top_px, idx

    def draw_path(self, sh, color, width, dash=None):
        if "notes" in sh:  # pasted notes: their box, thin and dashed (the notes are the shape)
            width, dash = 1, (4, 3)
        ax, bx = self.sx, self.kb_w - self.view_t * self.sx
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        view = (self.kb_w - 20, self.ruler_h - 20, self.winfo_width() + 20, self.winfo_height() + 20)
        cuts = ([role_of(st) == "cut" for st in sh["strokes"]] if sh["kind"] == "custom" and not sh.get("text")
                else ())
        for k, path in enumerate(cached_arrays(sh)):
            if k < len(cuts) and cuts[k]:  # a fill line (no notes of its own): thin, dashed
                for coords in screen_lines(path, ax, bx, ay, by, view):
                    self.create_line(*coords, fill=color, width=1, dash=(2, 3))
                continue
            for coords in screen_lines(path, ax, bx, ay, by, view):
                if width == 1 or len(coords) < 2000:
                    self.create_line(*coords, fill=color, width=width, dash=dash)
                    continue
                # a long thick line: Windows paints thick lines with thousands of points very slowly (seconds),
                # thin ones quickly, so it's the thin line three times, a pixel apart
                self.create_line(*coords, fill=color, width=1, dash=dash)
                for dx, dy in ((1, 0), (0, 1)):
                    self.create_line(*[v + (dx if n % 2 == 0 else dy) for n, v in enumerate(coords)], fill=color,
                                     width=1, dash=dash)
        if sh["kind"] == "custom" and sh.get("fill") in ("fill", "spam"):
            for (b0, p0), (b1, p1) in gap_lines(sh):  # the straight lines closing gaps in its outline: dashed
                self.create_line(b0 * ax + bx, p0 * ay + by, b1 * ax + bx, p1 * ay + by, fill=color, width=1,
                                 dash=(4, 3))

    def part_colors(self):
        """{(start, wall end): colour} for the highlighted curves: the one clicked purple, the curves linked to it
        teal (so you can tell them apart)."""
        got = self.funnel_parts()
        if not got:
            return {}
        main = self.app.part_main
        main = main[1:] if main and main[0] == "curve" and main[1:] in got[2] else None
        return {c: PART_COLOR if main in (None, c) else TWIN_COLOR for c in got[2]}

    def draw_parts(self):
        """The selected funnel's highlighted lines / curves: thick purple / teal (stands out on the orange selected
        notes), under the handles."""
        got = self.funnel_parts()
        if not got:
            return
        sh, lines, _ = got
        colors = self.part_colors()
        paths = [(funnel_lines(sh)[n], PART_COLOR) for n in lines]
        paths += [(c, colors[k, end]) for k, end, c, _ in funnel_curves(sh) if (k, end) in colors]
        for path, color in paths:
            self.create_line(*[v for b, p in path for v in (self.t2x(b), self.p2y(p))], fill=color,
                             width=max(4, round(4 * self.scale)), capstyle="round", joinstyle="round")

    def draw_handles(self, sh):
        s = self.scale
        # the handle lines of the anchors: blue on a white edge, so they show on notes
        # a highlighted funnel curve's handles take its colour (purple / teal), so you see which ones shape it
        colors = self.part_colors() if sh["kind"] == "funnel" else {}
        lines = ([(a, h, HANDLE_COLOR) for a, h in self.curve_handle_lines(sh)] if sh["kind"] == "curve" else
                 [(a, h, colors.get(c, HANDLE_COLOR)) for a, h, c in funnel_handle_lines(sh)]
                 if sh["kind"] == "funnel" else
                 [(a, h, HANDLE_COLOR) for a, h in self.stroke_handle_lines(sh)] if sh["kind"] == "custom" else [])
        lines.sort(key=lambda line: line[2] != HANDLE_COLOR)  # the coloured ones on top
        for width, edge in ((max(3, round(3.5 * s)), True), (max(1, round(1.5 * s)), False)):
            for a, h, color in lines:
                self.create_line(self.t2x(a[0]), self.p2y(a[1]), self.t2x(h[0]), self.p2y(h[1]),
                                 fill="#ffffff" if edge else color, width=width)
        r = 4 * s
        handles = self.handles(sh)
        if colors:
            handles.sort(key=lambda hd: isinstance(hd[2], tuple) and hd[2][0] != "start" and hd[2][1:3] in colors)
        for b, p, i, free in handles:
            x, y = self.t2x(b), self.p2y(p)
            color = HANDLE_COLOR
            if isinstance(i, tuple):
                kind = i[0]
                if kind == "pt":  # a point of a custom shape's stroke (Select tool): a small square
                    q = r - s
                    self.create_rectangle(x - q, y - q, x + q, y + q, fill="#ffffff", outline=STROKE_POINT_COLOR,
                                          width=2)
                    continue
                if kind != "start":
                    color = colors.get(i[1:3], HANDLE_COLOR)
            elif sh["kind"] == "curve":  # a curve's ends stay squares, like a line's
                kind = "ctrl" if i % 3 else "anchor" if free else None
            elif sh["kind"] == "arc":  # the point an arc passes through: round, like an anchor
                kind = "anchor" if free else None
            else:
                kind = None
            if kind == "anchor":  # an anchor between a curve's ends: round
                q = r + 1.5 * s
                self.create_oval(x - q, y - q, x + q, y + q, fill="#ffffff", outline=color,
                                 width=max(2, round(2 * s)))
                continue
            if kind == "ctrl":  # the end of a handle line: a solid dot with a white edge
                q = r + 0.5 * s
                self.create_oval(x - q, y - q, x + q, y + q, fill=color, outline="#ffffff",
                                 width=max(1, round(s)))
                continue
            if isinstance(i, tuple):  # where a funnel's curves start: a diamond
                d = r + 2
                self.create_polygon(x, y - d, x + d, y, x, y + d, x - d, y, fill="#ffffff", outline="#0050d0",
                                    width=2)
                continue
            self.create_rectangle(x - r, y - r, x + r, y + r, fill="#ffffff",
                                  outline="#0050d0" if free else "#c00000", width=2)

    def draw_draft_points(self):
        """The points of the shape being drawn, like a selected shape's: its ends, a polyline's corners, an arc's
        three points (the middle one round), a box's corners. (A curve's handles come once it's drawn.)"""
        d = self.draft
        kind = d.get("draw") or d["kind"]
        if kind == "free":
            return
        pts = d["pts"]
        if kind == "curve":
            pts = [pts[0], pts[-1]]
        elif d["kind"] == "custom":  # circle / polygon / custom shape: its box's four corners
            (b0, p0), (b1, p1), (b2, p2) = pts
            pts = [pts[0], pts[1], [b1 + b2 - b0, p1 + p2 - p0], pts[2]]
        s = self.scale
        r = 4 * s
        for i, (b, p) in enumerate(pts):
            x, y = self.t2x(b), self.p2y(p)
            if kind == "arc" and i == 1 and len(pts) == 3:
                q = r + 1.5 * s
                self.create_oval(x - q, y - q, x + q, y + q, fill="#ffffff", outline=HANDLE_COLOR,
                                 width=max(2, round(2 * s)))
            else:
                self.create_rectangle(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#c00000", width=2)

    def draw_keyboard(self, h):
        """Piano keys follow the pitch zoom: black keys on their rows, white-key edges between B/C and E/F."""
        kb, top = self.kb_w, self.ruler_h
        self.create_rectangle(0, top, kb, h, fill="#ffffff", outline="")
        p_lo, p_hi = self.visible_pitches(h)
        font_size = max(7, min(11, int(self.sy * 0.6 / self.scale)))
        # faintly greyed: outside a real 88-key piano; with 256 keys, above the standard 128 instead
        usual = PIANO_88 if self.app.keys == KEYS[0] else range(KEYS[0])
        for p in range(p_lo, p_hi + 1):
            y0, y1 = self.row_y(p)
            n = p % 12
            if p not in usual:
                self.create_rectangle(0, y0, kb, y1, fill="#e2e2e2", outline="")
            if n in BLACK:
                self.create_rectangle(0, y0, kb * 0.6, y1, fill="#222222" if p in usual else "#6a6a6a", outline="")
            if n in (0, 5):  # bottom edge of C and F = white key border
                self.create_line(0, y1, kb, y1, fill="#606060" if n == 0 else "#b0b0b0")
            label = note_name(p) if n == 0 and self.sy >= 6 else (
                note_name(p) if n not in BLACK and self.sy >= 16 else None)
            if label:
                self.create_text(kb - 3, (y0 + y1) / 2, text=label, anchor="e", fill="#333",
                                 font=("Segoe UI", font_size, "bold" if n == 0 else "normal"))
        self.create_line(kb, top, kb, h, fill="#808080")

    def draw_ruler(self, w):
        kb, top = self.kb_w, self.ruler_h
        beats = self.app.beats
        self.create_rectangle(0, 0, w, top, fill="#f0f0f0", outline="")
        self.create_line(0, top, w, top, fill="#808080")
        step = beats
        while step * self.sx < 40:
            step *= 2
        b = math.floor(max(0.0, self.x2t(kb)) / step) * step
        while b <= self.x2t(w):
            x = self.t2x(b)
            if x >= kb:
                self.create_line(x, top - 6, x, top, fill="#555")
                self.create_text(x + 3, top / 2, text=str(int(b // beats) + 1), anchor="w", fill="#333",
                                 font=("Segoe UI", 8))
            b += step
        self.create_rectangle(0, 0, kb, top, fill="#f0f0f0", outline="")
