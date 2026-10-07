"""The piano roll's scrollbars: drag the bar to scroll, drag one of its ends to zoom, plus "-" / "+" buttons."""

import tkinter as tk

from files.lang import tr
from window import look
from window.widgets import Tooltip

TROUGH, THUMB, THUMB_HOT, GRIP = look.TROUGH, look.THUMB, look.THUMB_HOT, look.GRIP


class ZoomBar(tk.Canvas):
    """A scrollbar whose two ends can be dragged: that makes the bar longer or shorter, which zooms.
    view() -> (start, end, total) of what's seen, or None; move(start) scrolls; zoom(start, end, which) zooms,
    which = the end being dragged ("start" / "end": the other one stays where it is)."""

    def __init__(self, parent, scale, across, view, move, zoom):
        self.thick = int(16 * scale)
        super().__init__(parent, bg=TROUGH, highlightthickness=0, width=self.thick, height=self.thick)
        self.scale, self.across, self.view, self.move, self.zoom = scale, across, view, move, zoom
        self.grip, self.least = 8 * scale, 28 * scale  # an end's draggable part; the bar's shortest length
        self._drag = None  # (what, mouse, start, end, total, bar length) when the mouse went down
        self._hot = False
        self.bind("<Configure>", lambda e: self.refresh())
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Motion>", self.on_motion)
        self.bind("<Leave>", self.on_leave)

    def track(self):
        return self.winfo_width() if self.across else self.winfo_height()

    def along(self, e):
        return e.x if self.across else e.y

    def bar(self):
        """(where the bar starts, its length, start, end, total), in pixels along the track; None = nothing to show.
        While it's dragged the total stays as it was, so the bar doesn't slide under the mouse."""
        v = self.view()
        if v is None or self.track() < 2:
            return None
        a, b, total = v
        if self._drag:
            total = max(self._drag[4], b)
        track, span = self.track(), b - a
        length = min(track, max(self.least, track * span / total))
        rest = total - span
        pos = (track - length) * min(1.0, max(0.0, a / rest)) if rest > 1e-9 else 0.0
        return pos, length, a, b, total

    def part(self, c):
        """What's at c along the track: "before" / "after" the bar, its "start" / "end" grip, or "move"."""
        bar = self.bar()
        if bar is None:
            return None
        pos, length = bar[:2]
        if c < pos:
            return "before"
        if c > pos + length:
            return "after"
        if c < pos + self.grip:
            return "start"
        return "end" if c > pos + length - self.grip else "move"

    def refresh(self):
        self.delete("all")
        bar = self.bar()
        if bar is None:
            return
        pos, length = bar[:2]
        t, g, s = self.thick, self.grip, self.scale
        color = THUMB_HOT if self._hot or self._drag else THUMB
        rect = self.create_rectangle if self.across else lambda x0, y0, x1, y1, **kw: self.create_rectangle(
            y0, x0, y1, x1, **kw)
        line = self.create_line if self.across else lambda x0, y0, x1, y1, **kw: self.create_line(
            y0, x0, y1, x1, **kw)
        rect(pos, 2 * s, pos + length, t - 2 * s, fill=color, outline="")
        for c in (pos + g * 0.35, pos + g * 0.7, pos + length - g * 0.35, pos + length - g * 0.7):  # the grips
            line(round(c), 5 * s, round(c), t - 5 * s, fill=GRIP)

    def on_motion(self, e):
        what = self.part(self.along(e))
        ends = "sb_h_double_arrow" if self.across else "sb_v_double_arrow"
        self.config(cursor=ends if what in ("start", "end") else "arrow")
        hot = what in ("start", "end", "move")
        if hot != self._hot:
            self._hot = hot
            self.refresh()

    def on_leave(self, _):
        if self._hot and not self._drag:
            self._hot = False
            self.refresh()

    def on_press(self, e):
        c = self.along(e)
        what, bar = self.part(c), self.bar()
        if bar is None:
            return
        pos, length, a, b, total = bar
        if what == "before":  # a click beside the bar: one screen that way
            self.move(a - (b - a))
        elif what == "after":
            self.move(a + (b - a))
        else:
            self._drag = (what, c, a, b, total, length)
            self.refresh()

    def on_drag(self, e):
        if not self._drag:
            return
        what, c0, a, b, total, length = self._drag
        track, d = self.track(), self.along(e) - c0
        if what == "move":
            rest = total - (b - a)
            if track > length and rest > 0:
                self.move(min(rest, max(0.0, a + d * rest / (track - length))))
            return
        least = (b - a) * 1e-6
        if what == "start":
            self.zoom(min(b - least, max(0.0, a + d * total / track)), b, what)
        else:
            self.zoom(a, min(total, max(a + least, b + d * total / track)), what)

    def on_release(self, _):
        self._drag = None
        self.refresh()


def add_zoom_bars(box, roll, widget=None):
    """The roll in its box with a scrollbar under it and one right of it, each ending in "-" and "+" buttons.
    roll has scale, bar_view, bar_move, bar_zoom and zoom_step, and gets bars; widget = what's shown (the roll)."""
    s = roll.scale
    box.rowconfigure(0, weight=1)
    box.columnconfigure(0, weight=1)
    (widget or roll).grid(row=0, column=0, sticky="nsew")
    bars = []
    for across in (True, False):
        row = tk.Frame(box, bg=TROUGH)
        row.grid(row=1, column=0, sticky="ew") if across else row.grid(row=0, column=1, sticky="ns")
        bar = ZoomBar(row, s, across, lambda across=across: roll.bar_view(across),
                      lambda a, across=across: roll.bar_move(across, a),
                      lambda a, b, which, across=across: roll.bar_zoom(across, a, b, which))
        bars.append(bar)
        side = "right" if across else "bottom"
        for text, f, tip in (("+", 1.25, "in"), ("−", 0.8, "out")):  # (packed from the far end: "-" comes first)
            cell = tk.Frame(row, width=bar.thick, height=bar.thick)
            cell.pack_propagate(False)
            cell.pack(side=side)
            b = tk.Button(cell, text=tr("zoombar.plus" if text == "+" else "zoombar.minus"), bd=1, relief="flat",
                          bg=TROUGH, activebackground=THUMB, padx=0, pady=0, takefocus=False,
                          font=look.font(9, "bold"), repeatdelay=350, repeatinterval=90,
                          command=lambda f=f, across=across: roll.zoom_step(across, f))
            b.pack(fill="both", expand=True)
            Tooltip(b, tr(f"zoombar.{tip}_{'time' if across else 'keys'}"))
        bar.pack(side="left" if across else "top", fill="both", expand=True)
    tk.Frame(box, bg=TROUGH).grid(row=1, column=1, sticky="nsew")  # the corner
    roll.bars = tuple(bars)
