"""The Snap dropdown in the toolbar: bar, note lengths with a little picture of each note (dotted ones with a dot,
triplets with a 3), and Custom… (the "Customised snap" window: a number of notes, dotted or double dotted, divided
by a number). The choices themselves are in files/snap.py; the pictures are drawn here, no image files."""

import base64
import math
import struct
import tkinter as tk
import zlib
from tkinter import ttk

from files.lang import tr
from files.snap import (COUNT_RANGE, DIV_RANGE, DOTS, NOTE_RANGE, SNAP_LIST, custom_parts, custom_snap, snap_text,
                        whole_notes)
from files.mathexpr import fmt
from window.widgets import Tooltip

SIZE = 16  # the pictures' size at 100 % display scaling (drawn in a 16 x 16 box, then scaled)
DIGIT_3 = ["111", "001", "011", "001", "111"]


# ---------------------------------------------------------------- the pictures

def _ellipse(x, y, cx, cy, rx, ry, turn):
    c, s = math.cos(turn), math.sin(turn)
    dx, dy = x - cx, y - cy
    return ((dx * c + dy * s) / rx) ** 2 + ((-dx * s + dy * c) / ry) ** 2 <= 1


def _segment(x, y, ax, ay, bx, by, width):
    vx, vy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy)))
    return math.hypot(x - ax - t * vx, y - ay - t * vy) <= width / 2


def _note_inside(note, dots, triplet):
    """A function (x, y) -> inside the picture of that note (x, y in the 16 x 16 box, y down)."""
    tilt = math.radians(-25)

    def inside(x, y):
        if note == 1:  # a whole note: an open oval, no stem
            return (_ellipse(x, y, 7.5, 9, 4.2, 2.8, 0) and not _ellipse(x, y, 7.5, 9, 2.0, 1.7, math.radians(60)))
        head = _ellipse(x, y, 6.2, 12.6, 3.3, 2.3, tilt)
        if note == 2:  # a half note: open head
            head = head and not _ellipse(x, y, 6.2, 12.6, 2.4, 1.0, tilt)
        if head or _segment(x, y, 9.2, 12.2, 9.2, 1.6, 1.2):
            return True
        flags = {8: 1, 16: 2, 32: 3}.get(note, 0)
        gap = 2.0 if flags == 3 else 2.4
        for k in range(flags):
            y0 = 1.6 + k * gap
            if _segment(x, y, 9.2, y0, 12.6, y0 + 3.2, 1.3):
                return True
        if dots and math.hypot(x - 13.3, y - 12.8) <= 1.2:
            return True
        if triplet:
            col, row = int((x - 12.2) // 1.0), int((y - 10.5) // 1.0)
            if 0 <= row < 5 and 0 <= col < 3 and DIGIT_3[row][col] == "1":
                return True
        return False
    return inside


def _bar_inside(x, y):
    """A bar: two bar lines with the staff between them."""
    return (_segment(x, y, 2.5, 3, 2.5, 13, 1.3) or _segment(x, y, 13.5, 3, 13.5, 13, 1.3) or
            any(_segment(x, y, 2.5, h, 13.5, h, 0.8) for h in (4.5, 8, 11.5)))


def _wrench_inside(x, y):
    """A spanner (settings)."""
    jaw = math.hypot(x - 11.3, y - 4.7) <= 3.3 and not math.hypot(x - 13.2, y - 2.8) <= 2.0
    return jaw or _segment(x, y, 3.2, 12.8, 10.0, 6.0, 2.4)


def _png(inside, size, rgb=(0, 0, 0)):
    """The picture as PNG bytes: black (or rgb), its edges smoothed (4 x 4 samples per pixel), see-through around it."""
    rows = []
    k = SIZE / size
    for py in range(size):
        row = bytearray(b"\0")
        for px in range(size):
            hits = sum(inside((px + (i + 0.5) / 4) * k, (py + (j + 0.5) / 4) * k) for i in range(4) for j in range(4))
            row += bytes((*rgb, round(255 * hits / 16)))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def picture(what, size):
    """A PhotoImage: what = a note (whole-note fraction, dots, triplet), "bar", "custom" or None (empty)."""
    inside = (_bar_inside if what == "bar" else _wrench_inside if what == "custom" else
              (lambda x, y: False) if what is None else _note_inside(*what))
    return tk.PhotoImage(data=base64.b64encode(_png(inside, size)), format="png")


# ---------------------------------------------------------------- the dropdown

class SnapPicker:
    """A button showing the snap (picture + name) that opens the list. var: the app's snap (files/snap.py texts)."""

    def __init__(self, app, parent, var):
        self.app, self.var = app, var
        size = max(SIZE, round(SIZE * app.scale))
        self.pics = {snap: picture("bar" if snap == "bar" else what, size) for snap, what in SNAP_LIST}
        self.custom_pic = picture("custom", size)
        self.pick = tk.StringVar()  # the list's tick: a snap of the list, or "custom"
        self.button = ttk.Menubutton(parent, compound="left", direction="below")
        self.menu = tk.Menu(self.button, tearoff=False)
        for snap, _ in SNAP_LIST:
            self.menu.add_radiobutton(label=snap_text(snap), image=self.pics[snap], compound="left",
                                      variable=self.pick, value=snap, command=lambda s=snap: var.set(s))
        self.menu.add_radiobutton(label=tr("snap.custom"), image=self.custom_pic, compound="left",
                                  variable=self.pick, value="custom", command=self.open_custom)
        self.button.config(menu=self.menu)
        Tooltip(self.button, tr("snap.tip"))
        # (watches the snap only while the button is there: one left behind by a closed Hz bass window
        # configured its gone button, an error on the next snap change / project opened)
        trace = var.trace_add("write", lambda *_: self.show())
        self.button.bind("<Destroy>", lambda e: var.trace_remove("write", trace) if e.widget is self.button else None,
                         add="+")
        self.show()

    def show(self):
        snap = self.var.get()
        custom = custom_parts(snap) is not None
        self.pick.set("custom" if custom else snap)
        self.button.config(text=snap_text(snap), image=self.custom_pic if custom else self.pics.get(snap, ""))

    def open_custom(self):
        self.show()  # (the tick goes back to the snap in use until the window says otherwise)
        CustomSnapWindow(self.app, self.var)


class CustomSnapWindow(tk.Toplevel):
    """Customised snap: [count] [  / . ..] [note] note, divided by [n]. Empty = one plain note (16 = a 16th),
    / = that many notes (3 / 16 = three 16ths), . = a dotted note (1.5 notes), .. = double dotted (1.75); the count
    is greyed out unless it's /."""

    def __init__(self, app, var):
        super().__init__(app)
        self.app, self.var = app, var
        self.title(tr("snap.customised_snap"))
        self.transient(app)
        self.resizable(False, False)
        count, note, div = custom_parts(var.get()) or ("3", 16, 1)
        self.count = tk.StringVar(value=count if count not in DOTS else "3")
        self.kind = tk.StringVar(value=count if count in DOTS else "/")
        self.note, self.div = tk.StringVar(value=str(note)), tk.StringVar(value=str(div))
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        setup = ttk.LabelFrame(box, text=tr("snap.setup"), padding=8)
        setup.pack(fill="x")
        row = ttk.Frame(setup)
        row.pack(anchor="w")
        self.count_box = ttk.Spinbox(row, textvariable=self.count, from_=COUNT_RANGE[0], to=COUNT_RANGE[1], width=6)
        self.count_box.pack(side="left")
        kind = ttk.Combobox(row, textvariable=self.kind, values=list(DOTS), state="readonly", width=3)
        kind.pack(side="left", padx=6)
        kind.bind("<<ComboboxSelected>>", lambda e: self.update_info())
        Tooltip(kind, tr("snap.kind_tip"))
        ttk.Spinbox(row, textvariable=self.note, from_=NOTE_RANGE[0], to=NOTE_RANGE[1], width=6).pack(side="left")
        ttk.Label(row, text=tr("snap.note")).pack(side="left", padx=(6, 0))
        row = ttk.Frame(setup)
        row.pack(anchor="e", pady=(8, 0))
        ttk.Label(row, text=tr("snap.divided_by")).pack(side="left", padx=(0, 6))
        div_box = ttk.Spinbox(row, textvariable=self.div, from_=DIV_RANGE[0], to=DIV_RANGE[1], width=6)
        div_box.pack(side="left")
        Tooltip(div_box, tr("snap.divided_by_tip"))
        self.info = ttk.Label(box, text="", foreground="#777")
        self.info.pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(8, 0))
        ttk.Button(row, text=tr("snap.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("snap.cancel"), command=self.destroy).pack(side="left", padx=(6, 0))
        for v in (self.count, self.note, self.div):
            v.trace_add("write", lambda *_: self.update_info())
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_info()
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 80}+{app.winfo_rooty() + 80}")
        self.grab_set()
        self.count_box.focus_set() if self.kind.get() == "/" else div_box.focus_set()

    def snap(self):
        """The custom snap the boxes make, or None if a number is missing or out of range."""
        try:
            note, div = int(self.note.get()), int(self.div.get())
            count = self.kind.get() if self.kind.get() != "/" else int(self.count.get())
        except ValueError:
            return None
        return custom_snap(count, note, div) if custom_parts(custom_snap(count, note, div)) else None

    def update_info(self):
        self.count_box.config(state="normal" if self.kind.get() == "/" else "disabled")
        snap = self.snap()
        if snap is None:
            self.info.config(text=tr("snap.out_of_range", COUNT=f"{COUNT_RANGE[0]}-{COUNT_RANGE[1]}",
                                     NOTE=f"{NOTE_RANGE[0]}-{NOTE_RANGE[1]}", DIV=f"{DIV_RANGE[0]}-{DIV_RANGE[1]}"),
                             foreground="#d00000")
            return
        beats = whole_notes(snap) * 4
        ppq = self.app.ppq
        self.info.config(text=tr("snap.length", beats=f"{float(beats):.4f}".rstrip("0").rstrip("."),
                                 ticks=fmt(round(float(beats * ppq), 2)),
                                 ppq=ppq), foreground="#777")

    def ok(self):
        snap = self.snap()
        if snap is None:
            self.bell()
            return
        if snap in (custom_snap("", n, 1) for n in (1, 2, 4, 8, 16, 32)):
            snap = "1/" + snap.split("/")[1]  # a plain note that's in the list anyway
        self.var.set(snap)
        self.destroy()
