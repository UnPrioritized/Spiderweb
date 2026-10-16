"""Mixed into Drawer: colouring the drawing's areas by hand (the Areas tool, areas.py), the colour swatches and
the board's area picture."""

import json
import math
from tkinter import messagebox

import numpy as np

from files.lang import tr
from files.speed import Photo
from notes.areas import ALL_COLOURS, most_colours
from notes.custom import (DRAWN_FRAME, area_paint, areas_filled, colour_of, filled_spots, open_paths, role_of,
                          shape_areas)
from roll.roll_shared import SLOT_COLORS
from window import look

# Areas (areas.py) on the board: what Fill / Spam fill as normal, an area emptied by hand, the outside, the board
AREA_NORMAL, AREA_EMPTY, OFF_BOARD, BOARD = look.AREA_NORMAL, look.AREA_EMPTY, look.DRAWER_BG, look.BOARD
WARN_COLOR = look.WARN  # more colours than a shape can have (like the side panel's warning)
AREA_FRAME = DRAWN_FRAME  # (the drawing's box as a custom shape, for finding its areas)


def rgb(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def area_color(c):
    """The colour shown for area colour c (1 .. COLOURS): the note colours in order."""
    return SLOT_COLORS[(c - 1) % len(SLOT_COLORS)][0]


class DrawerAreas:
    """Mixed into Drawer."""

    def most_colours(self):
        """The most colours a shape can have in this project (16 with "Use channel 10")."""
        return most_colours(self.app.all16())

    def swatch_grid(self):
        """(swatch size, swatches per row) fitting the canvas's width: smaller swatches first, then more rows."""
        count, w = self.most_colours() + 1, self.swatches.winfo_width()
        if w <= 1:  # (not shown yet)
            return self.swatch, count
        least = max(10, round(12 * self.scale))
        rows = 1
        while True:
            cols = -(-count // rows)
            k = min(self.swatch, (w - 2) // cols - 2)
            if k >= least or cols == 1:
                return max(k, 4), cols
            rows += 1

    def draw_swatches(self):
        c = self.swatches
        c.delete("all")
        most = self.most_colours()
        self.area_pick = min(self.area_pick, most)  # (the 16th picked, then "Use channel 10" turned off)
        k, cols = self.swatch_grid()
        rows = -(-(most + 1) // cols)
        if int(c.cget("height")) != rows * (k + 2) + 2:
            c.config(height=rows * (k + 2) + 2)
        for n in range(most + 1):
            x, y = 2 + n % cols * (k + 2), 2 + n // cols * (k + 2)
            on = n == self.area_pick
            c.create_rectangle(x, y, x + k, y + k, fill=area_color(n) if n else AREA_EMPTY,
                               outline=look.SWATCH_EDGE_ON if on else look.SWATCH_EDGE, width=2 if on else 1)
            if not n:  # Empty: a cross
                c.create_line(x + 3, y + 3, x + k - 3, y + k - 3, fill=look.STROKE)
                c.create_line(x + k - 3, y + 3, x + 3, y + k - 3, fill=look.STROKE)

    def swatch_at(self, x, y):
        k, cols = self.swatch_grid()
        col, row = int((x - 2) // (k + 2)), int((y - 2) // (k + 2))
        n = row * cols + col
        return n if 0 <= col < cols and row >= 0 and n <= self.most_colours() else None

    def pick_swatch(self, e):
        n = self.swatch_at(e.x, e.y)
        if n is not None:
            self.area_pick = n
            self.draw_swatches()
            self.swatch_tip(e)

    def swatch_tip(self, e):
        n = self.swatch_at(e.x, e.y)
        if n is not None:
            self.pos_label.config(text=tr("drawer.area_empty") if n == 0 else tr("drawer.area_n", n=n))

    def area_info(self):
        """(AreaMap of the drawing in board u, v, which areas Fill / Spam fill as normal), remembered."""
        shown = self.shown()  # (hidden strokes are left out of the shape)
        key = json.dumps(shown)
        if self._area_cache and self._area_cache[0] == key:
            return self._area_cache[1:3]
        sh = {"kind": "custom", "strokes": shown, "pts": AREA_FRAME, "fill": "fill"}
        amap = shape_areas(sh) if shown else None
        inside = None
        if amap is not None:
            inside = areas_filled(sh, amap)
        self._area_cache = (key, amap, inside, sh)
        self._area_px = None
        return amap, inside

    def face_info(self):
        """For drawing the areas smooth (AreaMap.exact): (faces, each one's area number, which ones Fill / Spam fill
        as normal), remembered."""
        amap, _ = self.area_info()
        if len(self._area_cache) < 5:
            fc, lab, x, y, g = amap.exact()
            filled = np.zeros(len(lab), bool)
            filled[g] = filled_spots(self._area_cache[3], x, y)
            self._area_cache += ((fc, lab, filled),)
        return self._area_cache[4]

    def gaps(self):
        """The drawing's open outlines (custom.open_paths), remembered until the strokes change."""
        shown = self.shown()
        key = json.dumps(shown)
        if self._gap_cache is None or self._gap_cache[0] != key:
            self._gap_cache = (key, open_paths(shown))
        return self._gap_cache[1]

    def area_at(self, e):
        """The area at the mouse, or None (outside the drawing, or nothing drawn)."""
        return self.area_spot(e.x, e.y)[0]

    def area_spot(self, x, y):
        """(The area at canvas spot x, y, exact on the lines; a spot (u, v) in it to keep its colour at), or (None,
        None) outside the drawing / nothing drawn."""
        amap, _ = self.area_info()
        if amap is None:
            return None, None
        u, v = self.from_screen(x, y)
        fc, lab, _ = self.face_info()
        f = int(fc.area_at([u], [v])[0])
        a = int(lab[f])
        if a < 0 or a == amap.outside():
            return None, None
        if int(amap.at([u], [v])[0]) != a:  # (right by a line, where the cells say the next area: its own spot)
            _, _, sx, sy, g = amap.exact()
            i = int(np.flatnonzero(g == f)[0])
            u, v = float(sx[i]), float(sy[i])
        return a, (u, v)

    def area_paint(self, amap):
        """Each area's colour as given by hand (-1: as normal, 0: empty, k: colour k)."""
        return area_paint({"areas": self.areas}, amap)

    def area_press(self, e):
        """Click = the area under the mouse gets the picked colour; dragging on colours every area it passes
        (one undo step)."""
        self.drag = ["areas", e.x, e.y, False]  # (the last spot, whether the undo step is pushed yet)
        if self.area_at(e) is None:
            self.pos_label.config(text=tr("drawer.area_outside"))
        self.paint_areas([(e.x, e.y)])

    def area_drag(self, e):
        _, lx, ly, _ = self.drag
        n = max(1, int(math.hypot(e.x - lx, e.y - ly) // 3))  # every 3 px along the way (the mouse skips)
        self.paint_areas([(lx + (e.x - lx) * i / n, ly + (e.y - ly) * i / n) for i in range(1, n + 1)])
        self.drag[1:3] = e.x, e.y

    def paint_areas(self, spots):
        """The areas at these canvas spots get the picked colour."""
        picks = {}
        for x, y in spots:
            lab, spot = self.area_spot(x, y)
            if lab is not None and lab not in picks:
                picks[lab] = spot
        if picks and self.set_areas(picks, self.area_pick, undo=not self.drag[3]):
            self.drag[3] = True

    def set_area(self, lab, spot, colour):
        """Area lab gets colour (None: back to normal); spot = where it was clicked (u, v)."""
        self.set_areas({lab: spot}, colour)

    def set_areas(self, picks, colour, undo=True):
        """Areas {lab: (u, v) where it was clicked} get colour (None: back to normal). Returns whether anything
        changed."""
        amap, _ = self.area_info()
        paint = self.area_paint(amap)
        picks = {k: s for k, s in picks.items() if paint[k] != (-1 if colour is None else colour)}
        if not picks:
            return False
        labs = amap.at([a[0] for a in self.areas], [a[1] for a in self.areas]).tolist() if self.areas else []
        keep = [a for a, k in zip(self.areas, labs) if k not in picks]
        new = keep + ([[round(u, 5), round(v, 5), colour] for u, v in picks.values()] if colour is not None else [])
        if undo:
            self.push_undo()
        self.areas = new
        self.changed()
        return True

    def colours_count(self):
        """How many colours the placed shape gets with Fill / Spam: (without "Outline", with it). The shape's own
        colour counts where an area is filled as normal or an outline-only line has no colour of its own (other
        lines are the edge of the fill beside them); "Outline" adds one."""
        amap, inside = self.area_info()
        paint = self.area_paint(amap)[:-1] if amap is not None else np.zeros(0, np.int64)
        if amap is not None:  # (only the areas shown: where many lines meet at a point, the map's cells leave
            # little pockets no note is ever in, which counted the shape's own colour with every area coloured)
            _, lab, _ = self.face_info()
            shown = np.unique(lab[amap.exact()[4]])  # (each shown area's number, by a spot in it)
            paint, inside = paint[shown], inside[shown]
        shown = self.shown()
        used = {int(c) for c in paint if c > 0} | {colour_of(st) for st in shown if colour_of(st)}
        own_fill = amap is not None and bool((inside & (paint < 0)).any())
        own_line = any(not colour_of(st) and role_of(st) == "edge" for st in shown)
        return len(used) + (own_fill or own_line), len(used) + own_fill + 1

    def show_colours_count(self):
        if not self.area_bar.winfo_manager():
            return
        n, with_outline = self.colours_count() if self.shown_idx() else (0, 0)
        most = self.most_colours()
        text = tr("drawer.colours_used", n=n, most=most, m=with_outline)
        if with_outline > most:
            text += " " + tr("drawer.colours_too_many", most=most)
        if self.colours_used.cget("text") != text:
            self.colours_used.config(text=text, foreground=WARN_COLOR if with_outline > most else "")

    def reset_areas(self):
        """Every area back to how Fill / Spam fill it as normal (asks first)."""
        if not self.areas:
            self.pos_label.config(text=tr("drawer.areas_none_coloured"))
            return
        if not messagebox.askyesno(tr("drawer.spiderweb"), tr("drawer.areas_reset_ask"), icon="warning", parent=self):
            return
        self.push_undo()
        self.areas = []
        self.changed()

    def area_image(self, cw, ch):
        """The board with the areas' colours, as a picture of the whole canvas (None: no areas to show)."""
        if not (self.areas or self.tool.get() == "areas"):  # (finding the areas is slow on big drawings)
            return None
        amap, _ = self.area_info()
        if amap is None:
            return None
        fc, area, filled = self.face_info()  # (each pixel looked up on the lines themselves: smooth edges)
        view = (cw, ch, self.zoom, tuple(self.center), id(amap))
        if self._area_px is None or self._area_px[0] != view:
            k = self.px()
            u = self.center[0] + (np.arange(cw) + 0.5 - cw / 2) / k
            v = self.center[1] - (np.arange(ch) + 0.5 - ch / 2) / k
            face = fc.area_grid(u, v)
            board = ((u >= 0) & (u <= 1))[None, :] & ((v >= 0) & (v <= 1))[:, None]
            self._area_px = (view, face, board, u, v)
        _, face, board, u, v = self._area_px
        paint = self.area_paint(amap)[area]  # (each face's)
        lut = np.zeros((len(area), 3), np.uint8)
        tint = np.zeros(len(area), bool)
        normal = filled & (paint < 0)
        lut[normal], tint[normal] = rgb(AREA_NORMAL), True
        lut[paint == 0], tint[paint == 0] = rgb(AREA_EMPTY), True
        for c in range(1, ALL_COLOURS + 1):
            lut[paint == c], tint[paint == c] = rgb(area_color(c)), True
        if self.hover is not None:  # the area under the mouse: darker
            h = area == self.hover
            lut[h & tint] = (lut[h & tint] * 0.75).astype(np.uint8)
            lut[h & ~tint] = rgb(look.AREA_HOVER)
            tint[h] = True
        img = np.where(board[..., None], np.uint8(rgb(BOARD)), np.uint8(rgb(OFF_BOARD))).astype(np.uint8)
        if self.mirror_mode() != "off":
            side = board & self.mirror_side(u[None, :], v[:, None])
            img[side] = rgb(look.MIRROR_SIDE)
        img = np.where(tint[face][..., None], lut[face], img).astype(np.uint8)
        if self._area_img is None or (self._area_img.width(), self._area_img.height()) != (cw, ch):
            self._area_pic = Photo(self, cw, ch)
            self._area_img = self._area_pic.photo
        self._area_pic.put(img)
        return self._area_img
