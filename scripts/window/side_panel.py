"""The side panel of the main window and the drawer (ScrollingSide): scrolls when its rows don't fit, and the line
between it and the piano roll (the drawer's board) drags it wider or narrower (user 2026-11-09; PLAN.md)."""

import tkinter as tk
from tkinter import ttk

from files import settings
from window import look

GRIP = 8  # px at 100 %: the strip holding the line between the piano roll and the panel; all of it grabs (user: no
#           pixel hunting), the line drawn in its middle
LINE = 2  # px at 100 %: that line
LEAST = 120  # px at 100 %: the panel never gets narrower than this by dragging (stage C: snaps shut past it)
ROOM_LEFT = 240  # px at 100 %: the piano roll / board keeps at least this much width beside a wide panel


class ScrollingSide:
    """A window's side panel that scrolls (scrollbar, mouse wheel) when everything in it doesn't fit; otherwise it's
    stretched to the window's height (the main window, the drawer). Its width can be dragged: side_name = the
    settings.ini key it's remembered under ([view] <side_name>_panel_width, in px at 100 %)."""
    SIDE_SKIP = (tk.Listbox, tk.Text)  # (they scroll themselves; value widgets clicked take the wheel: wheel_turns)

    def make_side(self, width, name=None):
        """The panel, side_width pixels wide (wider by the scrollbar while it shows; wider still when the font
        makes texts wider than the usual one, look.WIDE; the width dragged last time when there is one): its frame
        to fill. Make it BEFORE the piano roll / board is packed (the line goes between them)."""
        s = self.side_scale = getattr(self, "scale", 1) or 1
        self.side_name = name
        self.side_usual = int(width * look.WIDE)
        self.side_width = width = self.saved_side_width() or self.side_usual
        box = self.side_box = ttk.Frame(self, width=width)
        box.pack(side="right", fill="y")
        box.pack_propagate(False)
        bg = ttk.Style().lookup("TFrame", "background") or "SystemButtonFace"
        g = self.side_grip = tk.Canvas(self, width=max(4, round(GRIP * s)), highlightthickness=0, bd=0, bg=bg,
                                       cursor="sb_h_double_arrow")
        g.pack(side="right", fill="y")
        self._grip_hot = False
        self._grip_from = None  # (mouse x on the screen, width) while the line is held
        self._grip_ghost = None
        g.bind("<Configure>", lambda e: self.draw_grip())
        g.bind("<Enter>", lambda e: self.grip_hot(True))
        g.bind("<Leave>", lambda e: self.grip_hot(False))
        g.bind("<ButtonPress-1>", self.grip_press)
        g.bind("<B1-Motion>", self.grip_drag)
        g.bind("<ButtonRelease-1>", self.grip_release)
        g.bind("<Double-Button-1>", self.grip_double)
        c = self.side_canvas = tk.Canvas(box, highlightthickness=0, bd=0, bg=bg)
        self.side_bar = ttk.Scrollbar(box, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=self.side_bar.set)
        c.pack(side="left", fill="both", expand=True)
        side = self.side = ttk.Frame(c, padding=(6, 0, 6, 6))
        self._side_win = c.create_window(0, 0, window=side, anchor="nw")
        c.bind("<Configure>", lambda e: self.fit_side())
        side.bind("<Configure>", lambda e: self.after_idle(self.fit_side))
        self.bind("<MouseWheel>", self.side_wheel, add="+")
        self.bind("<Configure>", self.side_window_sized, add="+")
        return side

    def fit_side(self):
        c, side = self.side_canvas, self.side
        need, have = side.winfo_reqheight(), c.winfo_height()
        # room to spare: stretched to the panel's height; too tall: its own height (0), so
        # it keeps following what it needs
        c.itemconfigure(self._side_win, width=c.winfo_width(), height=have if need < have else 0)
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(need, have)))
        if need > have + 1:
            if not self.side_bar.winfo_ismapped():  # the panel gets wider by the scrollbar, not narrower inside
                self.side_bar.pack(side="right", fill="y", before=c)
                self.show_side_width()
        elif self.side_bar.winfo_ismapped():
            self.side_bar.pack_forget()
            self.show_side_width()
            c.yview_moveto(0)

    def scroll_side(self, e, anyway=False):
        """The wheel over the panel scrolls it (not over SIDE_SKIP widgets unless anyway). True when it did."""
        if (str(e.widget).startswith(str(self.side_canvas)) and self.side_bar.winfo_ismapped()
                and (anyway or not isinstance(e.widget, self.SIDE_SKIP))):
            self.side_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
            return True
        return False

    def side_wheel(self, e):
        self.scroll_side(e)

    # ------------------------------------------------------------ the width

    def side_most(self):
        """The widest the panel may be now: the piano roll / board keeps ROOM_LEFT (None before the window shows)."""
        have = self.winfo_width()
        if have <= 1:
            return None
        return max(self.side_least(), have - round((ROOM_LEFT + GRIP) * self.side_scale))

    def side_least(self):
        return round(LEAST * self.side_scale)

    def show_side_width(self):
        """The panel box at side_width (+ the scrollbar while it shows), never past side_most."""
        width = self.side_width
        most = self.side_most()
        if most is not None:
            width = min(width, most)
        if self.side_bar.winfo_ismapped():
            width += self.side_bar.winfo_reqwidth()
        if int(self.side_box.cget("width")) != width:
            self.side_box.config(width=width)

    def side_window_sized(self, e):
        if e.widget is self:  # (a smaller window: the panel gives way so the piano roll keeps its room)
            self.show_side_width()

    def set_side_width(self, width):
        self.side_width = max(self.side_least(), int(width))
        self.show_side_width()

    def saved_side_width(self):
        """The width dragged last time (px), None when there's none or it doesn't read."""
        if not self.side_name:
            return None
        try:
            px = int(float(settings.get("view", f"{self.side_name}_panel_width", "") or ""))
        except ValueError:
            return None
        return max(self.side_least(), round(px * self.side_scale)) if px > 0 else None

    def save_side_width(self):
        if not self.side_name:
            return
        try:
            settings.put_many({("view", f"{self.side_name}_panel_width"): round(self.side_width / self.side_scale)})
        except OSError:
            pass  # (not remembered: the next start opens at the usual width)

    # ------------------------------------------------------------ the line

    def draw_grip(self):
        g = self.side_grip
        g.delete("all")
        w, h = g.winfo_width(), g.winfo_height()
        line = max(1, round(LINE * self.side_scale))
        x = (w - line) // 2
        hot = self._grip_hot or self._grip_from is not None
        g.create_rectangle(x, 0, x + line, h, width=0, fill=look.SIDE_LINE_HOT if hot else look.SIDE_LINE)

    def grip_hot(self, on):
        self._grip_hot = on
        self.draw_grip()

    # While held, only a thin line follows the mouse; the panel takes its width on let go (moving every control of
    # the panel costs ~130 ms a step, measured 2026-11-10: a live drag stuttered)

    def grip_press(self, e):
        self._grip_from = (e.x_root, self.side_width)
        self._grip_to = self.side_width
        self.draw_grip()
        line = self._grip_ghost = tk.Frame(self, width=max(1, round(LINE * self.side_scale)),
                                           bg=look.SIDE_LINE_HOT, cursor="sb_h_double_arrow")
        self._ghost_at(self.side_width)
        line.lift()

    def _ghost_at(self, width):
        g = self.side_grip
        right = g.winfo_rootx() - self.winfo_rootx() + g.winfo_width() // 2 + (self.side_width - width)
        self._grip_ghost.place(x=right, y=g.winfo_y(), height=g.winfo_height())

    def grip_drag(self, e):
        if self._grip_from is None:
            return
        x0, w0 = self._grip_from
        width = w0 - (e.x_root - x0)  # (the panel is on the right: dragging left makes it wider)
        most = self.side_most()
        if most is not None:
            width = min(width, most)
        self._grip_to = max(self.side_least(), width)
        self._ghost_at(self._grip_to)

    def grip_release(self, e):
        if self._grip_from is None:
            return
        self._grip_from = None
        self._grip_ghost.destroy()
        self.set_side_width(self._grip_to)
        self.draw_grip()
        self.save_side_width()

    def grip_double(self, e):
        """Double-click = back to the usual width (stage B: the smallest width with no cut text)."""
        self._grip_from = None
        if self._grip_ghost is not None and self._grip_ghost.winfo_exists():
            self._grip_ghost.destroy()
        self.set_side_width(self.side_usual)
        self.draw_grip()
        self.save_side_width()
