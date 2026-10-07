"""The notes' picture on screen as tiles (Tk pictures side by side, roll_draw.show_image). When the view moves by
whole pixels the tiles are just put further along and only the strips coming into view are sent: copying one
picture onto itself took ~13 ms of every scroll step at the user's size, placing the tiles ~1 ms."""

import numpy as np

from files.speed import Photo

TILE = 1024  # px each side, about (the picture's size split evenly)


class Tiles:
    """A picture of size = (width, height) on tiles that wrap round: the picture's pixel (x, y) is the tiles'
    (x + ox, y + oy), so moving the picture moves the origin and no pixel already sent has to be sent again. Each
    tile is used for one spot at a time: there are enough of them for any part of the picture's size. Sending the
    whole picture lines them up with it again (Tk's time goes with the pixels sent: no tile only partly seen)."""

    def __init__(self, master, width, height):
        self.size = (width, height)
        self.ox = self.oy = 0
        nx, ny = (max(1, round(n / TILE)) for n in self.size)
        self.tw, self.th = -(-width // nx), -(-height // ny)
        self.photos = [[Photo(master, self.tw, self.th) for _ in range(nx + 1)] for _ in range(ny + 1)]

    def on_screen(self):
        """(tile, x, y) of every tile the picture shows, (x, y) = its top left corner in the picture."""
        (w, h), tw, th, rows = self.size, self.tw, self.th, self.photos
        for m in range(self.oy // th, (self.oy + h - 1) // th + 1):
            row = rows[m % len(rows)]
            for k in range(self.ox // tw, (self.ox + w - 1) // tw + 1):
                yield row[k % len(row)], k * tw - self.ox, m * th - self.oy

    def move(self, dx, dy):
        """The picture's pixels went (dx, dy) further (the strips coming in are put afterwards)."""
        self.ox -= dx
        self.oy -= dy

    def put(self, img, x0, y0, x1, y1):
        """img's pixels (rows x columns x 3, the whole picture) in x0..x1, y0..y1 (the ends not included) sent to
        the tiles. A tile getting much of its part is sent whole (quicker with Pillow)."""
        h, w = img.shape[:2]
        tw, th = self.tw, self.th
        if (x0, y0, x1, y1) == (0, 0, w, h):
            self.ox = self.oy = 0
        for photo, x, y in self.on_screen():
            a, b, c, d = max(x0, x), min(x1, x + tw), max(y0, y), min(y1, y + th)
            if a >= b or c >= d:
                continue
            if photo.whole_is_quicker((b - a) * (d - c)):
                a, b, c, d = max(x, 0), min(x + tw, w), max(y, 0), min(y + th, h)
                if (b - a, d - c) == (tw, th):
                    photo.put(img[c:d, a:b])
                    continue
                whole = np.zeros((th, tw, 3), np.uint8)  # (past the picture's edge: never seen)
                whole[c - y:d - y, a - x:b - x] = img[c:d, a:b]
                photo.put(whole)
            else:
                photo.put(img[c:d, a:b], a - x, c - y)

    def place(self, canvas, x, y):
        """The tiles as canvas items, the picture's top left corner at (x, y). (Parts past its left / top edge go
        under the keyboard and ruler, drawn over them.)"""
        for photo, a, b in self.on_screen():
            canvas.create_image(x + a, y + b, image=photo.photo, anchor="nw")

    def pixel(self, x, y):
        """The colour (r, g, b) shown at the picture's (x, y)."""
        row = self.photos[(y + self.oy) // self.th % len(self.photos)]
        photo = row[(x + self.ox) // self.tw % len(row)]
        return tuple(photo.photo.get((x + self.ox) % self.tw, (y + self.oy) % self.th))
