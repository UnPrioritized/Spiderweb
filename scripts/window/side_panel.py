"""The side panel of the main window and the drawer (ScrollingSide)."""

import tkinter as tk
from tkinter import ttk

from window import look


class ScrollingSide:
    """A window's side panel that scrolls (scrollbar, mouse wheel) when everything in it doesn't fit; otherwise it's
    stretched to the window's height (the main window, the drawer)."""
    SIDE_SKIP = (tk.Listbox, tk.Text)  # (they scroll themselves; value widgets clicked take the wheel: wheel_turns)

    def make_side(self, width):
        """The panel, side_width pixels wide (wider by the scrollbar while it shows; wider still when the font
        makes texts wider than the usual one, look.WIDE): its frame to fill."""
        self.side_width = width = int(width * look.WIDE)
        box = self.side_box = ttk.Frame(self, width=width)
        box.pack(side="right", fill="y")
        box.pack_propagate(False)
        c = self.side_canvas = tk.Canvas(box, highlightthickness=0, bd=0,
                                         bg=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")
        self.side_bar = ttk.Scrollbar(box, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=self.side_bar.set)
        c.pack(side="left", fill="both", expand=True)
        side = self.side = ttk.Frame(c, padding=(6, 0, 6, 6))
        self._side_win = c.create_window(0, 0, window=side, anchor="nw")
        c.bind("<Configure>", lambda e: self.fit_side())
        side.bind("<Configure>", lambda e: self.after_idle(self.fit_side))
        self.bind("<MouseWheel>", self.side_wheel, add="+")
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
                self.side_box.config(width=self.side_width + self.side_bar.winfo_reqwidth())
                self.side_bar.pack(side="right", fill="y", before=c)
        elif self.side_bar.winfo_ismapped():
            self.side_bar.pack_forget()
            self.side_box.config(width=self.side_width)
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
