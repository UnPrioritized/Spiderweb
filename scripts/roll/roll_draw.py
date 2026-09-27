"""Piano roll: painting the grid, notes, shapes, handles, keyboard and ruler."""

import math
import tkinter as tk

import numpy as np

from notes.custom import custom_note_count, gap_line
from notes.engine import cached_arrays, shape_notes
from notes.funnel import funnel_curves, funnel_handle_lines, funnel_lines, funnel_note_count
from roll.roll_shared import (BLACK, DRAFT_COLOR, PIANO_88, PREVIEW_LIMIT, SELECTED_COLOR, SLOT_COLORS,
                              fade, note_name)

HANDLE_COLOR = "#0050d0"
STROKE_POINT_COLOR = "#7a1fe0"  # the points of a custom shape's strokes (purple, like a picked stroke)
PART_COLOR = "#7a1fe0"  # a highlighted funnel line / the curve clicked
TWIN_COLOR = "#00a39a"  # the curves linked to it
# Note colours: number = colour * 32 + velocity // 4 (low velocity = paler fill, the outline stays); colours = the
# slot colours, then selected, then the shape being drawn
SELECTED, DRAFT = len(SLOT_COLORS), len(SLOT_COLORS) + 1
NOTE_COLORS = [(fade(f, 1 - level * 4 / 124), b) for f, b in SLOT_COLORS + [SELECTED_COLOR, DRAFT_COLOR]
               for level in range(32)]
NOTE_RGB = np.array([[[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pair] for pair in NOTE_COLORS], np.uint8)


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

    def redraw(self):
        self._redraw_pending = False
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if self.sx is None or w < 50:
            return
        app = self.app
        rows, cols = self.grid_parts(w, h)
        # the picture below shows exactly this: when nothing here changed (e.g. dragging a shape whose notes catch up
        # later), it's shown again as it is instead of being painted again
        pic = (app.rendered, (frozenset(app.sels), self.sx, self.sy, self.view_t, self.view_top, self.kb_w,
                              self.ruler_h, w, h, tuple(rows), tuple(cols)))
        same = (self.note_img is not None and self._note_pic is not None and not self.draft
                and app.show_notes.get() and self._note_pic[0] is pic[0] and self._note_pic[1] == pic[1])
        rects = None if same or not app.show_notes.get() else self.note_rects(w, h)
        if same:
            self.create_image(int(self.kb_w), int(self.ruler_h), image=self.note_img, anchor="nw")
        elif rects is not None and len(rects[0]) > w * h // 2000:
            # Lots of notes: paint grid + notes as one picture (thousands of canvas items redraw slowly)
            self.paint_image(w, h, rows, cols, rects)
            self._note_pic = None if self.draft else pic
        else:
            self.note_img = self._note_pic = None
            self.draw_grid(w, h, rows, cols)
            for x0, y0, x1, y1, color in zip(*(v.tolist() for v in rects or ())):
                self.create_rectangle(x0, y0, x1, y1, fill=NOTE_COLORS[color][0], outline=NOTE_COLORS[color][1])
        # a line with tumours: the line as drawn, faint and dashed under it
        for i, sh in enumerate(app.shapes):
            if (sh.get("tumour") or {}).get("on") and (i in app.sels or app.show_lines.get()):
                self.draw_path(dict(sh, tumour=None), "#e89a9a" if i in app.sels else "#efc0c0", 1, dash=(6, 4))
        if app.show_lines.get():
            for i, sh in enumerate(app.shapes):
                if i not in app.sels:
                    self.draw_path(sh, "#c0392b", 1)
        for i in app.sels:
            self.draw_path(app.shapes[i], "#ff1f1f", 2)
        sel = app.selected()
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
        self.draw_keyboard(h)
        self.draw_ruler(w)
        self.draw_playhead()
        if self.typing:
            self.show_caret()

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
        return max(0, math.ceil(self.y2p(h) - 0.5)), min(127, math.floor(self.y2p(self.ruler_h) + 0.5))

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
        if self.p2y(127.5) > top:
            rows.append((top, self.row_y(127)[0], "#ececec"))
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
            order = rendered[np.argsort(rendered[:, 0], kind="stable")]
            self._note_index = (rendered, order, int((order[:, 1] - order[:, 0]).max()) if len(order) else 0)
        return self._note_index[1]

    def visible_range(self, t_lo, t_hi):
        """(first, end): the sorted_notes() rows that can overlap ticks t_lo..t_hi."""
        order, longest = self.sorted_notes(), self._note_index[2]
        return (int(np.searchsorted(order[:, 0], t_lo - longest, "left")),
                int(np.searchsorted(order[:, 0], t_hi, "right")))

    def visible_notes(self, t_lo, t_hi):
        """Rendered notes (array rows) that can overlap ticks t_lo..t_hi."""
        lo, hi = self.visible_range(t_lo, t_hi)
        return self.sorted_notes()[lo:hi]

    def note_rects(self, w, h):
        """On-screen notes as whole-pixel rectangles, skipping exact repeats: NumPy arrays (x0, y0, x1, y1, colour),
        colour = a number in NOTE_COLORS. Painted in this order (selected shapes' notes, then the draft, on top)."""
        app, ppq = self.app, self.app.ppq
        kb, top = self.kb_w, self.ruler_h
        ax, bx = self.sx / ppq, kb - self.view_t * self.sx  # x = tick * ax + bx
        ay, by = -self.sy, top + self.view_top * self.sy    # y = pitch * ay + by
        keys = np.arange(128)
        row0, row1 = np.round((keys + 0.5) * ay + by).astype(np.int64), np.round((keys - 0.5) * ay + by).astype(np.int64)
        shown = ~((row1 < top) | (row0 > h))  # each key's row on screen?
        row1 = np.maximum(row1, row0 + 1)

        notes = self.visible_notes(self.x2t(kb) * ppq, self.x2t(w) * ppq)
        color = notes[:, 4] % len(SLOT_COLORS)
        if app.sels:
            mine = np.isin(notes[:, 5], list(app.sels))
            if mine.any():  # selected shapes' notes go on top
                order = np.concatenate([np.nonzero(~mine)[0], np.nonzero(mine)[0]])
                notes, color = notes[order], np.where(mine[order], SELECTED, color[order])
        parts = [(notes, color)]
        big = False
        if self.draft and self.draft["kind"] == "custom":
            big = (custom_note_count(self.draft, ppq) or 0) > PREVIEW_LIMIT
        elif self.draft and self.draft["kind"] == "funnel":
            big = funnel_note_count(self.draft, ppq) > PREVIEW_LIMIT
        if self.draft and not big:
            d = shape_notes(self.draft, ppq)
            parts.append((d, np.full(len(d), DRAFT)))
        s = np.concatenate([p[:, 0] for p, _ in parts])
        e = np.concatenate([p[:, 1] for p, _ in parts])
        key = np.concatenate([p[:, 2] for p, _ in parts])
        vel = np.concatenate([p[:, 3] for p, _ in parts])
        color = np.concatenate([c for _, c in parts])
        # (the same sums as one note at a time, so the same pixels)
        x0, x1 = np.round(s * ax + bx), np.round(e * ax + bx)
        on = ~((x1 < kb) | (x0 > w)) & shown[key]
        left, right = int(kb) - 2, w + 2
        x0 = np.maximum(x0[on].astype(np.int64), left)
        x1 = np.minimum(x1[on].astype(np.int64), right)
        key = key[on]
        # low velocity = paler fill (outline stays); 32 shades is plenty
        color = color[on] * 32 + np.minimum(vel[on], 127) // 4
        # zoomed out, lots of notes land on the very same pixels: keep the first
        packed = ((((x0 - left) << 16) | (x1 - left)) << 7 | key) << 10 | color
        _, first = np.unique(packed, return_index=True)
        if len(first) < len(packed):
            first.sort()
            x0, x1, key, color = x0[first], x1[first], key[first], color[first]
        return x0, row0[key], x1, row1[key], color

    def paint_image(self, w, h, rows, cols, rects):
        """Grid and notes as one picture over the note area."""
        kb, top = int(self.kb_w), int(self.ruler_h)
        iw, ih = w - kb, h - top
        if iw < 1 or ih < 1:
            return
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

        # Notes: which note is on top at every pixel (the last one painted), and whether that pixel is its outline.
        # Same pixels as a canvas rectangle with a 1-pixel outline: x0..x1 and y0..y1 inclusive.
        x0, y0, x1, y1, color = rects
        cx0, cx1 = np.maximum(x0, kb), np.minimum(x1 + 1, w)
        cy0, cy1 = np.maximum(y0, top), np.minimum(y1 + 1, h)
        n, rh = cx1 - cx0, cy1 - cy0
        seen = (n > 0) & (rh > 0)
        idx = np.nonzero(seen)[0]
        x0, y0, x1, y1, cx0, cx1, cy0, cy1, n, rh = (v[seen] for v in (x0, y0, x1, y1, cx0, cx1, cy0, cy1, n, rh))
        solid = (n <= 2) | (y1 - y0 < 2)  # too small to have a fill: all outline
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
        on = top_px >= 0
        k = top_px[on]
        img.reshape(-1, 3)[on] = NOTE_RGB[color[idx][k >> 1], k & 1]

        data = b"P6 %d %d 255\n" % (iw, ih) + img.tobytes()
        if self.note_img is None or (self.note_img.width(), self.note_img.height()) != (iw, ih):
            self.note_img = tk.PhotoImage(master=self, width=iw, height=ih)
        self.note_img.configure(data=data, format="ppm")
        self.create_image(kb, top, image=self.note_img, anchor="nw")

    def draw_path(self, sh, color, width, dash=None):
        ax, bx = self.sx, self.kb_w - self.view_t * self.sx
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        view = (self.kb_w - 20, self.ruler_h - 20, self.winfo_width() + 20, self.winfo_height() + 20)
        for path in cached_arrays(sh):
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
            gap = gap_line(sh)  # the straight line closing the one gap in its outline: faint, dashed
            if gap:
                (b0, p0), (b1, p1) = gap
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
        elif d["kind"] == "custom":  # square / circle / triangle / custom shape: its box's four corners
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
        for p in range(p_lo, p_hi + 1):
            y0, y1 = self.row_y(p)
            n = p % 12
            if p not in PIANO_88:  # outside a real 88-key piano: faintly greyed
                self.create_rectangle(0, y0, kb, y1, fill="#e2e2e2", outline="")
            if n in BLACK:
                self.create_rectangle(0, y0, kb * 0.6, y1, fill="#222222" if p in PIANO_88 else "#6a6a6a", outline="")
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
        self.create_rectangle(0, 0, kb, top, fill="#e4e4e4", outline="")
