"""Tooltips (moved out of widgets.py; widgets re-exports every name)."""

import tkinter as tk

from files.lang import size as lang_size
from window import look
from window.widgets_place import on_screen

TIP_WIDTH = 560  # tooltips wrap longer lines at this width (at 100 % scaling), so a text needs no line breaks
TALL = 120  # px: a widget taller than this gets its tooltip under the mouse instead of under itself



class Tooltip:
    """Shows a small box of text while the mouse rests on a widget (none while the text is empty). Once the widget
    is used (clicked, dragged, wheel, keys), the box stays away until the mouse leaves it with no button held."""

    BUTTONS = 0x1f00  # (event.state: a mouse button is held)
    # its own bind tag, first on the widget: the widget's own <ButtonPress-1> / wheel bindings (knobs, number box
    # labels, tabs) would otherwise win over a plain <ButtonPress> and the box stayed up while they were used
    TAG = "Tooltip"

    RAW = {}  # Tk's own widgets (a dropdown's list): path -> their tooltips (the event has only the path)

    def __init__(self, widget, text, owner=None):
        self.widget, self.text, self.tip, self.job, self.used = widget, text, None, None, False
        self.owner = owner or widget  # (the box is made in its window)
        if not widget.bind_class(self.TAG, "<Enter>"):  # (once per Tk)
            for seq, name in (("<Enter>", "enter"), ("<Leave>", "leave"), ("<ButtonPress>", "use"),
                              ("<MouseWheel>", "use"), ("<KeyPress>", "use")):
                widget.bind_class(self.TAG, seq, lambda e, n=name: [
                    getattr(t, n)(e) for t in getattr(e.widget, "tooltips", None)
                    or Tooltip.RAW.get(str(e.widget), ())] and None)
        if not hasattr(widget, "tooltips"):
            widget.tooltips = []
            widget.bindtags((self.TAG,) + widget.bindtags())
        widget.tooltips.append(self)

    def enter(self, e):
        if not self.used and not e.state & self.BUTTONS:
            self.schedule()

    def leave(self, e):
        self.hide()
        if not e.state & self.BUTTONS:
            self.used = False

    def use(self, e=None):
        self.used = True
        self.hide()

    def schedule(self):
        self.hide()
        self.job = self.later(400, self.show)

    def later(self, ms, func):
        """after() on the main window, not the widget: a widget closed before it runs would leave Tk calling a
        command that is gone (an error box); func checks alive() itself."""
        return self.owner._root().after(ms, func)

    def alive(self):
        try:
            return bool(self.widget.winfo_exists() and self.owner.winfo_exists())
        except tk.TclError:
            return False

    def show(self):
        self.job = None
        if not self.text or not self.alive():
            return
        w = self.widget
        self.tip = tk.Toplevel(self.owner)
        self.tip.wm_overrideredirect(True)
        if self.owner is not w:  # (over a dropdown's open list, which stays on top)
            self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=self.text, justify="left", background=look.TIP_BG, relief="solid",
                 borderwidth=1, padx=6, pady=4, wraplength=round(lang_size("size.tooltip_width", TIP_WIDTH) * w.winfo_fpixels("1i") / 96)).pack()
        self.tip.update_idletasks()
        # kept inside the window (the panel sits at its right edge), but never past its left edge or the screen's
        # (a narrow window at the screen's left: the text's start stays readable)
        top = self.owner.winfo_toplevel()
        x, y = self.spot()
        x = min(x, top.winfo_rootx() + top.winfo_width() - self.tip.winfo_reqwidth() - 4)
        left = top.winfo_rootx()
        if not on_screen(w, left, y):
            left = w.winfo_pointerx() - 12  # (the mouse is on a screen)
        # no room below (the status line of a maximized window): above the widget instead (hunt 2026-10-19)
        from files.system import work_area
        _, room_top, _, room_bottom = work_area(w, max(x, left), w.winfo_rooty())
        tall = self.tip.winfo_reqheight()
        if y + tall > room_bottom:
            above = (w.winfo_pointery() - 20 if w.winfo_height() > TALL else w.winfo_rooty() - 4) - tall
            y = max(room_top, above)
        self.tip.wm_geometry(f"+{max(x, left)}+{y}")

    def set_text(self, text):
        """New text; a box already up shows it at once (none for empty text)."""
        if text == self.text:
            return
        self.text = text
        if self.tip:
            self.hide()
            self.show()

    def spot(self):
        """Under the widget, or under the mouse for a tall one (a list as tall as the window: not at its bottom)."""
        w = self.widget
        if w.winfo_height() > TALL:
            return w.winfo_pointerx(), w.winfo_pointery() + 20
        return w.winfo_rootx() + 12, w.winfo_rooty() + w.winfo_height() + 4

    def hide(self):
        if self.job:
            self.owner._root().after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ListTip(Tooltip):
    """A list's row whose text is cut off at the list's right edge shows its whole text while the mouse rests on
    it, under that row."""

    WATCH_MS = 250  # while the mouse rests on a row: how often the row is checked for a refill / scroll under it

    def __init__(self, listbox, owner=None):
        super().__init__(listbox, "", owner)
        self.row, self.y, self.watch_job = None, 0, None
        listbox.bind("<Motion>", self.motion, add="+")
        listbox.bind("<ButtonRelease-1>", self.let_go, add="+")

    @classmethod
    def dropdown(cls, cb):
        """The same on a dropdown's open list (Tk makes that list itself: a Python handle on it here)."""
        pop = cb.tk.call("ttk::combobox::PopdownWindow", cb)
        from window.widgets import open_list
        path = open_list(cb)
        if path is None:
            return
        lb = tk.Listbox.__new__(tk.Listbox)
        lb.tk, lb._w, lb.master, lb.children, lb._tclCommands = cb.tk, path, cb, {}, None
        tip = cls(lb, cb)
        Tooltip.RAW[lb._w] = lb.tooltips
        cb.tk.call("bind", pop, "<Unmap>", "+" + cb.register(lambda: (setattr(tip, "row", None), tip.hide())))
        cb.bind("<Destroy>", lambda e: Tooltip.RAW.pop(lb._w, None) if e.widget is cb else None, add="+")
        return tip

    def under(self, y):
        """The row at height y, and its text when it is cut off ("" when it fits, and for a divider row)."""
        lb = self.widget
        i = lb.nearest(y)
        box = lb.bbox(i) if i >= 0 else None
        if not box or not box[1] <= y < box[1] + box[3]:
            return None, ""
        edge = lb.winfo_width() - int(lb.cget("borderwidth")) - int(lb.cget("highlightthickness"))
        from window.widgets import DIVIDER
        text = lb.get(i) if box[0] + box[2] > edge else ""
        return i, "" if text == DIVIDER else text

    def enter(self, e):
        self.motion(e)

    def motion(self, e):
        if e.state & self.BUTTONS:
            return
        self.y = e.y
        row, text = self.under(e.y)
        if row == self.row:
            return
        self.row, self.text, self.used = row, text, False  # (another row: its tip may show again after a click)
        self.hide()
        if text:
            self.schedule()
        if row is not None and self.watch_job is None:
            self.watch_job = self.later(self.WATCH_MS, self.watch)

    def watch(self):
        """The list filled again or scrolled under a resting mouse: the tip goes and the row is forgotten, so the
        next small move shows the row's own text."""
        self.watch_job = None
        if self.row is None or not self.alive():
            return
        if self.under(self.y) != (self.row, self.text):
            self.hide()
            self.row = None
            return
        self.watch_job = self.later(self.WATCH_MS, self.watch)

    def show(self):
        if self.alive() and self.row is not None and self.under(self.y) != (self.row, self.text):
            self.job, self.row = None, None  # (changed in the wait)
            return
        super().show()

    def let_go(self, e):
        """A drag through the rows counts as using the list: the row let go on is taken as the one under the mouse,
        so its tip doesn't pop up on the next nudge."""
        self.y = e.y
        self.row, self.text = self.under(e.y)
        self.used = True
        self.hide()

    def leave(self, e):
        self.row = None
        if self.watch_job:
            self.owner._root().after_cancel(self.watch_job)
            self.watch_job = None
        super().leave(e)

    def spot(self):
        lb = self.widget
        box = lb.bbox(self.row) if self.row is not None else None
        if not box:
            return super().spot()
        return lb.winfo_rootx() + 12, lb.winfo_rooty() + box[1] + box[3] + 2
