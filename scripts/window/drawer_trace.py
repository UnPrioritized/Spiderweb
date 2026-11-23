"""The drawer's tracing picture (QoL J, user 2026-11-23): a faint picture under the board to draw over. Picture… opens
one, "Picture strength" 0-100 % (0 = invisible, default 30, middle-click back), "Move / size picture" makes board
drags move it (a corner drag sizes it, proportions kept). NEVER saved in the shape; kept while the drawer is open.
PNG / GIF without Pillow, JPG and the rest with it (notes/picture.pixels). Mixed into Drawer (first: its mouse
handlers go before the drawing tools' while Move / size is on)."""
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from files.colours import rgb
from files.lang import tr
from files.speed import Photo
from notes import picture as P
from window import look
from window.widgets import Scrub, Tooltip, good, traced
from window.widgets_tips import greyed_why

STRENGTH = 30  # % the picture shows at first (middle-click on the box: back to it)
MOST_SIDE = 1600  # a bigger picture is averaged down to this many pixels on its longer side (rough is fine)
CORNER_PX = 10  # a press this near a corner of the picture (Move / size on) sizes it
LEAST_SIZE = 0.02  # the picture's least width / height, in boards
CORNER_CURSORS = ("top_left_corner", "top_right_corner", "bottom_left_corner", "bottom_right_corner")


class DrawerTrace:
    """Mixed into Drawer."""
    trace = None  # {"rgb": rows x cols x 3 float32 0..255, "alpha": rows x cols float32 or None, "box": [u0, v0, u1, v1],
    #               "name": file name, "id": changes with the picture / its box}
    _trace_drag = None  # (what, (u, v) at the press, box at the press) while Move / size drags it
    _trace_px = None  # (key, Photo) of the board drawn with the picture (no areas shown)

    def build_trace(self, side, row):
        """The side panel's tracing picture: its button in `row` (beside Deform…) while there's no picture (the
        panel stays as short as before: a whole box made it scroll at the drawer's usual size); Remove, the
        strength box and Move / size show under it once one is open (the panel grows with what's in use)."""
        self.trace_strength = tk.StringVar(value=str(STRENGTH))
        self.trace_adjust = tk.BooleanVar(value=False)
        b = ttk.Button(row, text=tr("trace.open"), command=self.open_trace)
        b.pack(side="left", padx=(4, 0))
        Tooltip(b, tr("trace.open_tip"))
        more = self.trace_more = ttk.Frame(side)
        self.trace_more_after = row  # (shown right under that row)
        row = ttk.Frame(more)
        row.pack(fill="x", pady=(4, 0))
        b = self.trace_remove_btn = ttk.Button(row, text=tr("trace.remove"), command=self.remove_trace)
        b.pack(side="left", padx=(0, 8))
        Tooltip(b, tr("trace.remove_tip"))
        greyed_why(b, tr("trace.why_none"))
        lab = ttk.Label(row, text=tr("trace.strength"))
        lab.pack(side="left")
        e = self.trace_box = ttk.Entry(row, textvariable=self.trace_strength, width=5, justify="center")
        e.pack(side="left", padx=(5, 0))
        ttk.Label(row, text="%").pack(side="left", padx=(3, 0))
        good(e)
        e.bind("<Return>", lambda ev: self.strength_typed())
        e.bind("<FocusOut>", lambda ev: self.strength_typed())
        for w in (lab, e):
            Tooltip(w, tr("trace.strength_tip"))
            greyed_why(w, tr("trace.why_none"))
            w.bind("<ButtonPress-2>", lambda ev: (self.set_strength(STRENGTH), "break")[1])  # (back to its default)
        Scrub(self.app, [(e, self.trace_strength, self.strength_typed)], (1, 10, 1), 0, 100, label=lab,
              no_undo=True)
        self.trace_lab = lab
        t = self.trace_tick = ttk.Checkbutton(more, text=tr("trace.adjust"), variable=self.trace_adjust,
                                              command=lambda: (self.sync_trace(), self.redraw()))
        t.pack(anchor="w", pady=(4, 0))
        Tooltip(t, tr("trace.adjust_tip"))
        greyed_why(t, tr("trace.why_none"))
        # (a drawing tool picked: drawing again, Move / size off)
        traced(self, self.tool, lambda *a: self.trace_adjust.get() and (self.trace_adjust.set(False),
                                                                          self.sync_trace(), self.redraw()))
        self.sync_trace()

    # ------------------------------------------------------------ the picture

    def open_trace(self):
        path = filedialog.askopenfilename(parent=self, title=tr("image.open_title"), filetypes=[
            (tr("image.files"), P.file_patterns()), (tr("image.all_files"), "*.*")])
        if path:
            self.load_trace(path)

    def load_trace(self, path):
        """Opens a picture file as the tracing picture, fitted inside the board (proportions kept). False if it
        couldn't be read (a message says why)."""
        try:
            a, alpha = P.pixels(path, self)
        except (tk.TclError, OSError, ValueError, MemoryError) as e:
            from window.image_window import _kinds
            messagebox.showerror(tr("trace.title"), tr("image.cant_open", e=e, kinds=_kinds()), parent=self)
            return False
        h, w = a.shape[:2]
        k = max(1, math.ceil(max(w, h) / MOST_SIDE))
        if k > 1:
            a = P._shrink(a, k, k)
            alpha = P._shrink(alpha, k, k) if alpha is not None else None
        h, w = a.shape[:2]
        if not w or not h:
            return False
        bw, bh = (1.0, h / w) if w >= h else (w / h, 1.0)
        self.trace = {"rgb": np.asarray(a, np.float32), "alpha": alpha, "name": os.path.basename(path),
                      "box": [(1 - bw) / 2, (1 - bh) / 2, (1 + bw) / 2, (1 + bh) / 2], "id": 0}
        if self.strength() == 0:  # (picked again after hiding it: shown)
            self.set_strength(STRENGTH)
        self.sync_trace()
        self.redraw()
        return True

    def remove_trace(self):
        self.trace, self._trace_px, self._trace_drag = None, None, None
        self.trace_adjust.set(False)
        self.sync_trace()
        self.redraw()

    def strength(self):
        try:
            return min(100, max(0, int(round(float(self.trace_strength.get())))))
        except ValueError:
            return STRENGTH

    def set_strength(self, v):
        self.trace_strength.set(str(v))
        self.strength_typed()

    def strength_typed(self):
        v = self.strength()
        if self.trace_strength.get() != str(v):
            self.trace_strength.set(str(v))
        self.redraw()

    def sync_trace(self):
        """Remove / strength / Move / size greyed with no picture; the file's name shown."""
        on = ["!disabled"] if self.trace else ["disabled"]
        for w in (self.trace_remove_btn, self.trace_box, self.trace_lab, self.trace_tick):
            w.state(on)
        if not self.trace:
            self.trace_adjust.set(False)
        shown = bool(self.trace)
        if shown != bool(self.trace_more.winfo_manager()):  # (only on a change: re-packing flashes)
            if shown:
                self.trace_more.pack(fill="x", after=self.trace_more_after)
            else:
                self.trace_more.pack_forget()
        self.canvas_cursor()

    def canvas_cursor(self):
        c = getattr(self, "canvas", None)
        if c is not None and self.trace_adjust.get():
            c.config(cursor="fleur")
        elif c is not None and str(c.cget("cursor")) == "fleur":
            c.config(cursor="crosshair")

    # ------------------------------------------------------------ drawing it

    def trace_blend(self, img, u, v):
        """img (rows x cols x 3 uint8, the whole canvas) with the picture mixed in at its strength. u / v: the board
        place of each column / row's pixel middle."""
        t, s = self.trace, self.strength() / 100
        if not t or s <= 0:
            return img
        u0, v0, u1, v1 = t["box"]
        ph, pw = t["rgb"].shape[:2]
        cols = np.floor((u - u0) / (u1 - u0) * pw).astype(np.int64)
        rows = np.floor((v1 - v) / (v1 - v0) * ph).astype(np.int64)
        cin, rin = np.flatnonzero((cols >= 0) & (cols < pw)), np.flatnonzero((rows >= 0) & (rows < ph))
        if not len(cin) or not len(rin):
            return img
        c0, c1, r0, r1 = cin[0], cin[-1] + 1, rin[0], rin[-1] + 1
        pic = t["rgb"][rows[r0:r1][:, None], cols[c0:c1][None, :]]
        wt = np.float32(s)
        if t["alpha"] is not None:
            wt = t["alpha"][rows[r0:r1][:, None], cols[c0:c1][None, :]][..., None] * s
        part = img[r0:r1, c0:c1].astype(np.float32)
        img[r0:r1, c0:c1] = (part + (pic - part) * wt + 0.5).astype(np.uint8)
        return img

    def trace_image(self, cw, ch):
        """The board with the picture on it, as a picture of the whole canvas (None: no picture to show). Made again
        only when the view, the strength, the mirror side or the picture changes."""
        t = self.trace
        if not t or self.strength() <= 0:
            return None
        key = (cw, ch, self.zoom, tuple(self.center), self.strength(), self.mirror_mode(), id(t), t["id"])
        if self._trace_px is not None and self._trace_px[0] == key:
            return self._trace_px[1].photo
        k = self.px()
        u = self.center[0] + (np.arange(cw) + 0.5 - cw / 2) / k
        v = self.center[1] - (np.arange(ch) + 0.5 - ch / 2) / k
        board = ((u >= 0) & (u <= 1))[None, :] & ((v >= 0) & (v <= 1))[:, None]
        from window.drawer_areas import BOARD, OFF_BOARD
        img = np.where(board[..., None], np.uint8(rgb(BOARD)), np.uint8(rgb(OFF_BOARD))).astype(np.uint8)
        if self.mirror_mode() != "off":
            img[board & self.mirror_side(u[None, :], v[:, None])] = rgb(look.MIRROR_SIDE)
        img = self.trace_blend(img, u, v)
        pic = self._trace_px[1] if self._trace_px is not None and self._trace_px[1].size == (cw, ch) else None
        pic = pic or Photo(self, cw, ch)
        pic.put(img)
        self._trace_px = (key, pic)
        return pic.photo

    def trace_outline(self, c):
        """While Move / size is on: the picture's edge + corner squares (c = the board's item maker)."""
        if not (self.trace and self.trace_adjust.get()):
            return
        u0, v0, u1, v1 = self.trace["box"]
        (x0, y0), (x1, y1) = self.to_screen(u0, v1), self.to_screen(u1, v0)
        c.create_rectangle(x0, y0, x1, y1, outline=look.TRACE_EDGE, dash=(4, 3))
        r = max(3, round(4 * self.scale))
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            c.create_rectangle(round(x) - r, round(y) - r, round(x) + r, round(y) + r, outline=look.TRACE_EDGE,
                               fill=look.BOARD)

    # ------------------------------------------------------------ Move / size

    def trace_corner(self, x, y):
        """The corner (0..3: left-top, right-top, left-bottom, right-bottom on screen) near (x, y), or None."""
        u0, v0, u1, v1 = self.trace["box"]
        for i, (u, v) in enumerate(((u0, v1), (u1, v1), (u0, v0), (u1, v0))):
            sx, sy = self.to_screen(u, v)
            if abs(sx - x) <= CORNER_PX * self.scale and abs(sy - y) <= CORNER_PX * self.scale:
                return i
        return None

    def adjusting(self):
        return bool(self.trace and self.trace_adjust.get())

    def on_press(self, e):
        if not self.adjusting():
            return super().on_press(e)
        self.canvas.focus_set()
        corner = self.trace_corner(e.x, e.y)
        self._trace_drag = (corner, self.from_screen(e.x, e.y), list(self.trace["box"]))

    def on_drag(self, e):
        if not self.adjusting() or self._trace_drag is None:
            return super().on_drag(e)
        corner, (pu, pv), box = self._trace_drag
        u, v = self.from_screen(e.x, e.y)
        u0, v0, u1, v1 = box
        if corner is None:
            du, dv = u - pu, v - pv
            new = [u0 + du, v0 + dv, u1 + du, v1 + dv]
        else:  # (the opposite corner stays; proportions kept: the longer move wins)
            left, top = corner in (0, 2), corner in (0, 1)
            fu, fv = (u1 if left else u0), (v0 if top else v1)  # the corner that stays
            w0, h0 = u1 - u0, v1 - v0
            k = max(abs(u - fu) / w0, abs(v - fv) / h0, LEAST_SIZE / min(w0, h0))
            w, h = w0 * k, h0 * k
            nu0, nu1 = (fu - w, fu) if left else (fu, fu + w)
            nv0, nv1 = (fv, fv + h) if top else (fv - h, fv)
            new = [nu0, nv0, nu1, nv1]
        self.trace["box"] = new
        self.trace["id"] += 1
        self.redraw()

    def on_release(self, e, second=False):
        if not self.adjusting() or self._trace_drag is None:
            return super().on_release(e, second)
        self._trace_drag = None

    def on_double(self, e):
        if not self.adjusting():
            return super().on_double(e)
        return self.on_press(e)  # (a quick second click: just another grab)

    def right_click(self, e):
        if not self.adjusting():
            return super().right_click(e)

    def on_motion(self, e):
        if not self.adjusting():
            return super().on_motion(e)
        self.show_position(e)
        corner = self.trace_corner(e.x, e.y)
        want = "fleur" if corner is None else CORNER_CURSORS[corner]
        if str(self.canvas.cget("cursor")) != want:
            self.canvas.config(cursor=want)
