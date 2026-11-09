"""The side panel of the main window and the drawer (ScrollingSide): scrolls when its rows don't fit, and the line
between it and the piano roll (the drawer's board) drags it wider or narrower (user 2026-11-09; PLAN.md)."""

import tkinter as tk
from tkinter import font as tkfont, ttk

from files import settings
from files.lang import tr
from window import look
from window.widgets import Tooltip

GRIP = 8  # px at 100 %: the strip holding the line between the piano roll and the panel; all of it grabs (user: no
#           pixel hunting), the line drawn in its middle
LINE = 2  # px at 100 %: that line
LEAST = 120  # px at 100 %: the panel never gets narrower than this by dragging (stage C: snaps shut past it)
ROOM_LEFT = 240  # px at 100 %: the piano roll / board keeps at least this much width beside a wide panel
NEED_WAIT_MS = 60  # rows shown: their needed width checked this long after (once for a burst)
NEED_SAMPLE = ("app.new_shape_defaults", "app.velocity", "app.last_note")  # (texts measured for need_mark)
SNAP_SHARE = 1 / 3  # dragged narrower than this share of the no-cut width: the panel snaps shut (user)
STRIP = 20  # px at 100 %: the shut panel's strip, a button all along (user: easy to bring back, no pixel hunting)
STRIP_GAP = 3  # px at 100 %: plain room between the strip and the window's edge (its resize border)
DRAG_START = 4  # px: a press on the strip moving less than this is a click


class ScrollingSide:
    """A window's side panel that scrolls (scrollbar, mouse wheel) when everything in it doesn't fit; otherwise it's
    stretched to the window's height (the main window, the drawer). Its width can be dragged: side_name = the
    settings.ini key it's remembered under ([view] <side_name>_panel_width, in px at 100 %)."""
    SIDE_SKIP = (tk.Listbox, tk.Text)  # (they scroll themselves; value widgets clicked take the wheel: wheel_turns)

    def make_side(self, width, name=None, shuts=False):
        """The panel, side_width pixels wide (wider by the scrollbar while it shows; wider still when the font
        makes texts wider than the usual one, look.WIDE; the width dragged last time when there is one): its frame
        to fill. Make it BEFORE the piano roll / board is packed (the line goes between them). shuts = dragged
        narrow enough it snaps shut to a strip that brings it back (the main window; user 2026-11-09)."""
        s = self.side_scale = getattr(self, "scale", 1) or 1
        self.side_name = name
        self.side_usual = int(width * look.WIDE)
        self.side_fixed = self.saved_side_width()  # (None = never dragged: follows what the rows need)
        self.side_need = self.saved_side_need()  # (the widest the rows have needed: none of their text cut)
        self.side_width = width = self.side_fixed or max(self.side_usual, self.side_need)
        self._need_wait = None
        # (rows that move parts onto a line of their own when there's no room beside: (narrow, fit again) pairs;
        # the double-click measures them narrow, panel_recolour.fit_stir)
        self.side_narrows = []
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
        self.bind("<Map>", self.side_row_shown, add="+")  # (a row shown in the panel may need more width)
        self.side_shuts = shuts
        self._strip_from = None
        if shuts:
            self.make_strip(bg)
            if not self.side_open_var().get():
                self.shut_side(save=False)
        return side

    # ------------------------------------------------------------ shut to a strip (main window only)

    def side_open_var(self):
        """The View menu's "Side panel" tick (made on first ask: the menu is built before the panel)."""
        var = getattr(self, "_side_open", None)
        if var is None:
            saved = settings.get("view", "side_panel_open", "yes") or "yes"
            var = self._side_open = tk.BooleanVar(self, value=saved.strip().casefold() not in ("no", "false", "0"))
        return var

    def make_strip(self, bg):
        s = self.side_scale
        st = self.side_strip = tk.Canvas(self, width=max(12, round(STRIP * s)), highlightthickness=0, bd=0,
                                         bg=look.SIDE_STRIP, cursor="hand2")
        self._strip_hot = False
        st.bind("<Configure>", lambda e: self.draw_strip())
        st.bind("<Enter>", lambda e: self.strip_hot(True))
        st.bind("<Leave>", lambda e: self.strip_hot(False))
        st.bind("<ButtonPress-1>", self.strip_press)
        st.bind("<B1-Motion>", self.strip_drag)
        st.bind("<ButtonRelease-1>", self.strip_release)
        Tooltip(st, tr("app.side_strip_tip"))

    def draw_strip(self):
        st = self.side_strip
        st.delete("all")
        st.configure(bg=look.SIDE_STRIP_HOT if self._strip_hot or self._strip_from else look.SIDE_STRIP)
        w, h = st.winfo_width(), st.winfo_height()
        st.create_text(w / 2, h / 2, text="◀", fill=look.SIDE_STRIP_ARROW, font=look.font(9))

    def strip_hot(self, on):
        self._strip_hot = on
        self.draw_strip()

    def side_is_open(self):
        return not self.side_shuts or self.side_box.winfo_manager() == "pack"

    def shut_side(self, save=True):
        if not self.side_shuts:
            return
        gap = round(STRIP_GAP * self.side_scale)
        if self.side_strip.winfo_manager() != "pack":
            self.side_strip.pack(side="right", fill="y", padx=(gap, gap), pady=(0, gap), before=self.side_box)
        self.side_box.pack_forget()
        self.side_grip.pack_forget()
        self.side_open_var().set(False)
        if save:
            self.save_side_open(False)

    def open_side(self, width=None, save=True):
        if width is not None:
            self.side_width = max(self.side_least(), int(width))
        if self.side_shuts and self.side_box.winfo_manager() != "pack":
            self.side_box.pack(side="right", fill="y", before=self.side_strip)
            self.side_grip.pack(side="right", fill="y", after=self.side_box)
            self.side_strip.pack_forget()
        self.show_side_width()
        if self.side_shuts:
            self.side_open_var().set(True)
            if save:
                self.save_side_open(True)

    def toggle_side(self):
        """The View menu's tick (already flipped)."""
        if self.side_open_var().get():
            self.open_side()
        else:
            self.shut_side()

    def save_side_open(self, on):
        try:
            settings.put_many({("view", "side_panel_open"): "yes" if on else "no"})
        except OSError:
            pass

    def shut_at(self):
        """Dragged narrower than this, the panel snaps shut (user: past ~a third of the no-cut width)."""
        return max(self.side_least(), round(max(self.side_need, self.side_usual) * SNAP_SHARE))

    def strip_x(self):
        """Where the strip's left edge is / would be, in the window (px)."""
        return self.winfo_width() - round((STRIP + 2 * STRIP_GAP) * self.side_scale)

    def strip_press(self, e):
        self._strip_from = (e.x_root, False)
        self.draw_strip()

    def strip_drag(self, e):
        if self._strip_from is None:
            return
        x0, moved = self._strip_from
        if not moved and abs(e.x_root - x0) < DRAG_START:
            return
        self._strip_from = (x0, True)
        if self._grip_ghost is None or not self._grip_ghost.winfo_exists():
            self._grip_ghost = tk.Frame(self, width=max(1, round(LINE * self.side_scale)), bg=look.SIDE_LINE_HOT,
                                        cursor="sb_h_double_arrow")
            self._grip_ghost.lift()
        width = self.winfo_rootx() + self.winfo_width() - e.x_root - round(GRIP * self.side_scale) // 2
        most = self.side_most()
        if most is not None:
            width = min(width, most)
        self._grip_to = width if width >= self.shut_at() else None
        x = self.strip_x() if self._grip_to is None else self.winfo_width() - width - round(GRIP * self.side_scale) // 2
        self._grip_ghost.place(x=x, y=self.side_strip.winfo_y(), height=self.side_strip.winfo_height())

    def strip_release(self, e):
        if self._strip_from is None:
            return
        _, moved = self._strip_from
        self._strip_from = None
        if self._grip_ghost is not None and self._grip_ghost.winfo_exists():
            self._grip_ghost.destroy()
        self.draw_strip()
        if not moved:
            self.open_side()  # (a click: back at the width it had)
        elif self._grip_to is not None:
            self.open_side(self._grip_to)
            self.save_side_width()

    def fit_side(self):
        self.side_row_shown()
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
        self.side_fixed = self.side_width
        if not self.side_name:
            return
        try:
            settings.put_many({("view", f"{self.side_name}_panel_width"): round(self.side_width / self.side_scale)})
        except OSError:
            pass  # (not remembered: the next start opens at the usual width)

    # ------------------------------------------------------------ the width the rows need (no text cut)

    def need_mark(self):
        """What the needed width depends on besides the rows: screen scaling, font, language. A remembered width
        made under another mark isn't used."""
        sample = " ".join(tr(k) for k in NEED_SAMPLE)
        return f"{self.side_scale:.3f}/{look.WIDE:.3f}/{tkfont.Font(font='TkDefaultFont').measure(sample)}"

    def saved_side_need(self):
        if not self.side_name:
            return 0
        text = settings.get("view", f"{self.side_name}_panel_needs", "") or ""
        px, _, mark = text.partition(" @ ")
        try:
            return round(int(px) * self.side_scale) if mark == self.need_mark() and int(px) > 0 else 0
        except ValueError:
            return 0

    def side_row_shown(self, e=None):
        """A row of the panel came into view (or the panel changed size): its needed width checked a moment later
        (once for a burst of rows)."""
        if e is not None and not str(e.widget).startswith(str(self.side)):
            return
        if self._need_wait is None:
            self._need_wait = self.after(NEED_WAIT_MS, self.check_side_need)

    def loosen_wraps(self):
        """Lines that wrap to the room they get (widgets.wrap_to_room) stop asking for their one-line width, so
        they never count as needing room. True when one changed."""
        changed = False
        todo = [self.side]
        while todo:
            w = todo.pop()
            todo.extend(w.winfo_children())
            if getattr(w, "wrap_measure", None) and str(w.cget("width")) != "1":
                w.wrap_measure()  # (its edges measured while it still asks for one line)
                w.configure(width=1)
                changed = True
        return changed

    def side_rows_need(self):
        """The width the panel's rows ask for now (px, inside the panel; wrapping lines not counted)."""
        if self.loosen_wraps():
            self.update_idletasks()
        return self.side.winfo_reqwidth()

    def check_side_need(self):
        self._need_wait = None
        if not self.side.winfo_exists():
            return
        if self.side_fixed is not None:  # (a width of the user's own: nothing grows; and a wide panel's rows
            return  # spread beside each other, which isn't what they NEED)
        need = self.side_rows_need()
        if need <= self.side_need:
            return
        self.side_need = need
        if self.side_name:
            try:
                settings.put_many({("view", f"{self.side_name}_panel_needs"):
                                   f"{round(need / self.side_scale)} @ {self.need_mark()}"})
            except OSError:
                pass
        if self.side_fixed is None and need > self.side_width:  # (never dragged: it grows so nothing is cut)
            self.set_side_width(need)

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
        if self.side_shuts and width < self.shut_at():  # (let go here = shut: the line shows where the strip goes)
            self._grip_to = None
            g = self.side_grip
            self._grip_ghost.place(x=self.strip_x(), y=g.winfo_y(), height=g.winfo_height())
            return
        self._grip_to = max(self.side_least(), width)
        self._ghost_at(self._grip_to)

    def grip_release(self, e):
        if self._grip_from is None:
            return
        self._grip_from = None
        self._grip_ghost.destroy()
        self.draw_grip()
        if self._grip_to is None:
            self.shut_side()  # (its width stays what it was: a click on the strip opens it there)
            return
        self.set_side_width(self._grip_to)
        self.save_side_width()

    def grip_double(self, e):
        """Double-click = the smallest width where nothing SHOWN NOW is cut (user 2026-11-10: "go back to smallest
        visible things that won't get cut off"); rows that wrap measured wrapped."""
        self._grip_from = None
        if self._grip_ghost is not None and self._grip_ghost.winfo_exists():
            self._grip_ghost.destroy()
        for narrow, _ in self.side_narrows:
            narrow()
        self.update_idletasks()
        self.set_side_width(self.side_rows_need())
        self.update_idletasks()
        for _, fit in self.side_narrows:  # (back beside the dropdown where the new width has room)
            fit()
        self.draw_grip()
        self.save_side_width()
