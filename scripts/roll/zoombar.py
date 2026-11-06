"""The piano roll's scrollbars: drag the bar to scroll, drag one of its ends to zoom, plus "-" / "+" buttons."""

import tkinter as tk

from files.lang import tr
from window import look
from window.widgets import Tooltip

TROUGH, THUMB, THUMB_HOT, GRIP = look.TROUGH, look.THUMB, look.THUMB_HOT, look.GRIP


class ZoomBar(tk.Canvas):
    """A scrollbar whose two ends can be dragged: that makes the bar longer or shorter, which zooms.
    view() -> (start, end, total) or (start, end, total, first) of what's seen (the bar's track = first .. total,
    first 0 if not given), or None; move(start) scrolls; zoom(start, end, which) zooms, which = the end being dragged
    ("start" / "end": the other one stays where it is). stops() -> None, or the places a dragged end catches on
    (STOP_PX) and then the bar may also be PUSHED on past its ends (FL's way: the view goes on, the main piano roll's
    Space past the edges, user 2026-11-06)."""
    STOP_PX = 6

    def __init__(self, parent, scale, across, view, move, zoom, stops=None):
        self.thick = int(16 * scale)
        super().__init__(parent, bg=TROUGH, highlightthickness=0, width=self.thick, height=self.thick)
        self.scale, self.across, self.view, self.move, self.zoom = scale, across, view, move, zoom
        self.stops = stops or (lambda: None)
        self.grip, self.least = 8 * scale, 28 * scale  # an end's draggable part; the bar's shortest length
        self._drag = None  # (what, mouse, start, end, total, bar length, first) when the mouse went down
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
        a, b, total = v[:3]
        first = v[3] if len(v) > 3 else 0.0
        if self._drag:
            total, first = max(self._drag[4], b), min(self._drag[6], a)
        track, span = self.track(), b - a
        length = min(track, max(self.least, track * span / (total - first)))
        rest = total - first - span
        pos = (track - length) * min(1.0, max(0.0, (a - first) / rest)) if rest > 1e-9 else 0.0
        return pos, length, a, b, total

    def first(self):
        """Where the bar's track starts (0, or before it while the view is in the Space before the start)."""
        v = self.view()
        return v[3] if v is not None and len(v) > 3 else 0.0

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
        bar = self.bar()
        t, g, s = self.thick, self.grip, self.scale
        color = THUMB_HOT if self._hot or self._drag else THUMB
        # (the piano roll refreshes its bars at every redraw: the same bar is left as it is, redraw hunt 2026-10-31)
        drawn = bar and (bar[:2], color, t, g, s)
        if drawn == self.__dict__.get("_drawn", ()) and self.find_all():
            return
        self._drawn = drawn
        self.delete("all")
        if bar is None:
            return
        pos, length = bar[:2]
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
            self._drag = (what, c, a, b, total, length, self.first())
            self.refresh()

    def on_drag(self, e):
        if not self._drag:
            return
        what, c0, a, b, total, length, first = self._drag
        track, d = self.track(), self.along(e) - c0
        stops = self.stops()
        if what == "move":
            rest = total - first - (b - a)
            if track > length + 1 and rest > 1e-9:
                to = a + d * rest / (track - length)
                # (pushed on past an end: the view goes on, the piano roll keeps it inside its room)
                self.move(to if stops is not None else min(first + rest, max(first, to)))
            elif stops is not None and track > 0:  # (the bar fills the track, e.g. every key shown: dragged = the
                self.move(a + d * (total - first) / track)  # view still goes that way, into the room; user 11-06)
            return
        least, whole = (b - a) * 1e-6, total - first
        if what == "start":
            self.zoom(min(b - least, max(first, self.caught(a + d * whole / track, stops, whole))), b, what)
        else:
            self.zoom(a, min(total, max(a + least, self.caught(b + d * whole / track, stops, whole))), what)

    def caught(self, v, stops, whole):
        """v on a stop when it's within STOP_PX of one (a dragged end catches on the keyboard's ends / beat 0)."""
        for s in stops or ():
            if abs(v - s) * self.track() / whole <= self.STOP_PX * self.scale:
                return s
        return v

    def on_release(self, _):
        self._drag = None
        self.refresh()


def add_zoom_bars(box, roll, widget=None, axes=(True, False), up="keys"):
    """The roll in its box with a scrollbar under it and one right of it, each ending in "-" and "+" buttons.
    roll has scale, bar_view, bar_move, bar_zoom and zoom_step, and gets bars; widget = what's shown (the roll).
    axes: which bars (True = the one under it); up = what the right one's tips name ("keys" / "bpm")."""
    s = roll.scale
    box.rowconfigure(0, weight=1)
    box.columnconfigure(0, weight=1)
    (widget or roll).grid(row=0, column=0, sticky="nsew")
    bars = []
    for across in axes:
        row = tk.Frame(box, bg=TROUGH)
        row.grid(row=1, column=0, sticky="ew") if across else row.grid(row=0, column=1, sticky="ns")
        bar = ZoomBar(row, s, across, lambda across=across: roll.bar_view(across),
                      lambda a, across=across: roll.bar_move(across, a),
                      lambda a, b, which, across=across: roll.bar_zoom(across, a, b, which),
                      (lambda across=across: roll.bar_stops(across)) if hasattr(roll, "bar_stops") else None)
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
            # (held while its window closes: Tk's next repeat ran on the gone button, an error window)
            b.bind("<Destroy>", lambda e: e.widget.tk.eval("catch {after cancel $::tk::Priv(afterId)}"), add="+")
            Tooltip(b, tr(f"zoombar.{tip}_{'time' if across else up}"))
        bar.pack(side="left" if across else "top", fill="both", expand=True)
    if len(axes) == 2:
        tk.Frame(box, bg=TROUGH).grid(row=1, column=1, sticky="nsew")  # the corner
    roll.bars = tuple(bars)
