"""Draw on Domino: the shape settings window (window/domino_companion.py's Companion; a mixin like the bar).
The bar's "Settings" toggle lifts the side panel's REAL shape settings box (app.settings_home, a plain frame
holding app.settings) out into a window of its own (Tk's "wm manage"; only plain frames can be lifted, the themed
box can't) and puts it back in the side panel when closed or when drawing on Domino stops: every panel option
shows here without a copy (user 2026-11-18). The Point / Tick / Pitch table stays hidden while it's out (user)."""

import re
import tkinter as tk

from files import settings
from files.lang import tr
from files import domino_link as dl

GAP = 6  # px between the bar and the window the first time
KEY_TAG = "DominoSettingsKeys"
TAG_AGAIN = 20  # overlay ticks between looks for controls made in the window since (they get KEY_TAG too)


def _all_in(w):
    for k in w.winfo_children():
        yield k
        yield from _all_in(k)


class DominoSettings:
    def make_settings_toggle(self, row):
        """The bar's "Settings" toggle (pressed while the window is open)."""
        from tkinter import ttk
        from window.widgets import Tooltip
        self.settings_on = tk.BooleanVar(value=False)
        b = self.settings_button = ttk.Checkbutton(row, text=tr("companion.settings"), variable=self.settings_on,
                                                   style="Toolbutton", takefocus=False,
                                                   command=lambda: self.show_settings(self.settings_on.get()))
        b.pack(side="left", padx=(2, 0))
        Tooltip(b, tr("companion.settings_tip"))
        self.settings_out = False
        self._settings_moved = None  # (the window's spot as last placed / seen)
        self._settings_bar = None    # (the bar's spot it was placed against)
        self._settings_next = None

    # --- lifting the box out and back ---
    def show_settings(self, on, save=True):
        """on: the window opens (the box lifted out of the side panel); off: closed (the box stays out, hidden,
        until stop puts it back: opening it again is then instant)."""
        app = self.app
        home = app.settings_home
        if on:
            if not self.settings_out:
                self.lift_settings()
            try:
                app.tk.call("wm", "deiconify", home)
            except tk.TclError:
                return
            self.window_look()
            self.place_settings()
            home.lift()
        elif self.settings_out:
            try:
                app.tk.call("wm", "withdraw", home)
            except tk.TclError:
                pass
            self.give_keys_back()
        if self.settings_on.get() != bool(on):
            self.settings_on.set(bool(on))
        if save:
            settings.put("domino", "settings_open", "yes" if on else "no")
        # (top-most first: the window ABOVE the bar. It belongs to the bar, so Windows keeps it above the bar
        # anyway; listed under it, the restack pushed the bar under the mouse catcher and clicks on the bar were
        # lost, user 2026-11-18 "closes immediately after toggling")
        self.overlay.above = ([home] if on else []) + [self.bar]

    def lift_settings(self):
        app = self.app
        home = app.settings_home
        # (where it goes back: the side panel row packed right after it, if any)
        rows = home.master.pack_slaves()
        self._settings_next = rows[rows.index(home) + 1] if home in rows and rows.index(home) + 1 < len(rows) else None
        app.tk.call("wm", "manage", home)
        app.tk.call("wm", "withdraw", home)
        # (belongs to the bar: hides with it = Domino minimised. ONCE, while hidden: set again on an open window,
        # Tk on Windows withdrew it after a Send, user 2026-11-18 "settings doesn't show anymore")
        app.tk.call("wm", "transient", home, self.bar)
        app.tk.call("wm", "title", home, tr("companion.settings_title"))
        app.tk.call("wm", "resizable", home, 0, 0)
        app.tk.call("wm", "protocol", home, "WM_DELETE_WINDOW", app.register(lambda: self.show_settings(False)))
        # (keys: a bind tag of our own FIRST on every control in it, before Spiderweb's window-wide shortcuts, hunt
        # 11-18. Not the window's own tag: controls with a tip have their tags fixed, the main window's in them)
        app.bind_class(KEY_TAG, "<Escape>", self.settings_escape)
        app.bind_class(KEY_TAG, "<KeyPress>", self.settings_key)
        self.add_more_button()
        self.tag_keys(True)
        self.settings_out = True
        app.build_points()  # (the Point / Tick / Pitch table left out while here)

    def add_more_button(self):
        """Under the shape settings while lifted out (never in the side panel): Spiderweb's Settings on the Copying
        page, where the Draw on Domino ticks are (user 2026-11-20)."""
        from tkinter import ttk
        from window.widgets import Tooltip
        b = self._more_button = ttk.Button(self.app.settings_home, text=tr("companion.more_settings"),
                                           takefocus=False, command=self.open_more_settings)
        b.pack(anchor="w", padx=6, pady=(4, 6))
        Tooltip(b, tr("companion.more_settings_tip"))

    def open_more_settings(self):
        """Settings opened on its Copying page, shown over Domino (it belongs to the hidden main window otherwise)."""
        from window.settings_window import open_settings
        w = open_settings(self.app, "copying")
        try:
            if str(w.wm_transient()) == str(self.app):
                w.transient(self.bar)
            w.deiconify()
            w.lift()
            w.focus_force()
            if not any(w is a for a in self.adopted):
                self.adopted.append(w)
        except tk.TclError:
            pass

    def tag_keys(self, on):
        """KEY_TAG first on every control in the window (on), or taken off again (back in the side panel)."""
        home = self.app.settings_home
        for w in [home, *_all_in(home)]:
            try:
                tags = w.bindtags()
                if on and tags[:1] != (KEY_TAG,):
                    w.bindtags((KEY_TAG,) + tuple(t for t in tags if t != KEY_TAG))
                elif not on and KEY_TAG in tags:
                    w.bindtags(tuple(t for t in tags if t != KEY_TAG))
            except tk.TclError:
                pass

    def window_look(self):
        """The title bar like every other Spiderweb window's: dark in the dark look, the small two-ring icon (hunt
        2026-11-18: those are put on Toplevel windows when shown; a lifted frame isn't one)."""
        import os
        from types import SimpleNamespace
        from files import system
        from files.about import ICONS
        from window import look
        app, home = self.app, self.app.settings_home
        if look.DARK:
            from window.synth_look import dark_title
            dark_title(home)
        system.small_title_icon(SimpleNamespace(wm_frame=lambda: str(app.tk.call("wm", "frame", home))),
                                os.path.join(ICONS, "icon-small.ico"), os.path.join(ICONS, "icon.ico"))

    def put_settings_back(self):
        """Back into the side panel where it was (stop)."""
        if not self.settings_out:
            return
        app = self.app
        home = app.settings_home
        self.settings_out = False
        try:
            self.tag_keys(False)
            app.unbind_class(KEY_TAG, "<Escape>")
            app.unbind_class(KEY_TAG, "<KeyPress>")
            b = getattr(self, "_more_button", None)
            if b is not None:
                b.destroy()
                self._more_button = None
            app.tk.call("wm", "forget", home)
            nxt = self._settings_next
            if nxt is not None and nxt.winfo_exists() and nxt.winfo_manager() == "pack":
                home.pack(fill="x", pady=(8, 0), before=nxt)
            else:
                home.pack(fill="x", pady=(8, 0))
        except tk.TclError:
            pass
        app.build_points()

    # --- where it sits ---
    def saved_settings_at(self):
        """Where it was dragged to, against the bar's top left (settings.ini [domino] settings_at), or None."""
        try:
            dx, dy = (int(v) for v in (settings.get("domino", "settings_at") or "").split(","))
            return dx, dy
        except ValueError:
            return None

    def bar_room(self):
        """How tall the bar is with ONE message line showing under its buttons, whether one shows now or not (hunt
        2026-11-18, user's B: room kept, nothing jumps; a message hid under the window)."""
        from tkinter import font as tkfont
        from window import look
        bar = self.bar
        h = bar.winfo_height()
        if self.notes.winfo_manager():
            return max(h, bar.winfo_reqheight())
        try:
            line = tkfont.nametofont(look.TK).metrics("linespace") + 2
        except tk.TclError:
            line = 18
        return h + line

    def title_px(self):
        """The window's title bar + top frame height (its inside starts this far under the spot it's put at):
        measured while shown, else Windows' own sizes (hunt 2026-11-18: a fixed 30 px was 38 here)."""
        home = self.app.settings_home
        try:
            if home.winfo_viewable():
                m = re.search(r"[+-](-?\d+)$", str(self.app.tk.call("wm", "geometry", home)))
                got = home.winfo_rooty() - int(m.group(1))
                if 0 < got < 200:
                    self._title_px = got
        except (tk.TclError, ValueError, AttributeError):
            pass
        if getattr(self, "_title_px", None):
            return self._title_px
        from files import system
        if system.WINDOWS:
            import ctypes
            m = ctypes.windll.user32.GetSystemMetrics
            return m(4) + m(33) + m(92)  # SM_CYCAPTION + SM_CYSIZEFRAME + SM_CXPADDEDBORDER
        return 30

    def place_settings(self):
        """Under the bar's left edge the first time (one message line of room left), or above the bar when there's
        no room under it; else where it was dragged to (against the bar, so it moves along with Domino). Always
        whole on the screen, its size as now (hunt 2026-11-18: it covered the bar low on the screen, grew past
        the bottom)."""
        from files import system
        app, home, bar = self.app, self.app.settings_home, self.bar
        try:
            bar.update_idletasks()
            home.update_idletasks()
            at = self.saved_settings_at()
            bx, by = bar.winfo_rootx(), bar.winfo_rooty()
            w, h = home.winfo_reqwidth(), home.winfo_reqheight()
            tall = h + self.title_px()  # (the whole window, title bar included)
            left, top, right, bottom = system.work_area(bar, bx + w // 2, by + 20)
            if at:
                x, y = bx + at[0], by + at[1]
            else:
                x, y = bx, by + self.bar_room() + GAP
                if y + tall > bottom and by - GAP - tall >= top:  # (no room under the bar: above it)
                    y = by - GAP - tall
            x, y = min(max(x, left), right - w), max(min(y, bottom - tall), top)
            app.tk.call("wm", "geometry", home, f"+{x}+{y}")
            home.update_idletasks()
            self._settings_moved = (x, y)
            self._settings_bar = (bx, by)
            self._settings_size = h
        except tk.TclError:
            pass

    def settings_tick(self):
        """Overlay tick: a drag of the window remembered; the bar moved (Domino moved) or the window changed size
        (other rows: another tool / shape) = placed again, whole on the screen."""
        if not self.settings_out or not self.settings_on.get():
            return
        self._tag_count = getattr(self, "_tag_count", 0) + 1
        if self._tag_count % TAG_AGAIN == 0:  # (rows made since: their keys go through KEY_TAG too)
            self.tag_keys(True)
        self.settings_moved()
        try:
            home = self.app.settings_home
            if self.bar.winfo_viewable() and ((self.bar.winfo_rootx(), self.bar.winfo_rooty()) != self._settings_bar
                                              or home.winfo_reqheight() != getattr(self, "_settings_size", None)):
                self.place_settings()
        except tk.TclError:
            pass

    def settings_moved(self):
        """The user dragged the window (it was placed by place_settings before): its spot remembered against the
        bar's top left (overlay tick)."""
        if not self.settings_out or self._settings_moved is None:
            return
        home, bar = self.app.settings_home, self.bar
        try:
            if not home.winfo_viewable() or not bar.winfo_viewable() or self._grip is not None:
                return
            # (the frame's top left, the same way place_settings asked for it)
            m = re.search(r"([+-]-?\d+)([+-]-?\d+)$", str(self.app.tk.call("wm", "geometry", home)))
            fx, fy = int(m.group(1).replace("+", "")), int(m.group(2).replace("+", ""))
        except (tk.TclError, ValueError, AttributeError):
            return
        if (fx, fy) == self._settings_moved:
            return
        self._settings_moved = (fx, fy)
        settings.put("domino", "settings_at", f"{fx - bar.winfo_rootx()},{fy - bar.winfo_rooty()}")

    # --- keyboard ---
    def settings_escape(self, _e=None):
        """Esc in the window: the keyboard back to where it was (the overlay while drawing, else Domino) (user
        2026-11-18). A dropdown's open list takes Esc itself first."""
        self.give_keys_back()
        return "break"

    # (Ctrl+A / C / V / X, word jumps / deletes in a box: its own text editing)
    BOX_KEYS = frozenset(("a", "c", "v", "x", "left", "right", "home", "end", "backspace", "delete"))
    BOXES = ("TEntry", "Entry", "TCombobox", "TSpinbox")

    def settings_key(self, e):
        """A key in the window (its own bind tag, before Spiderweb's window-wide shortcuts): Ctrl / Alt keys are
        Domino's, as on the overlay (hunt 2026-11-18: Ctrl+S saved Spiderweb's project with Domino's PPQ, Ctrl+E
        exported the hidden shapes, Ctrl+N / O replaced the project). Spiderweb keeps typing, Esc (own binding),
        Ctrl+Z / Y (user's pick: Spiderweb's undo) and Ctrl+A / C / V / X inside a box."""
        from window.domino_companion import ALONE_KEYS, ALT, CTRL
        ks = e.keysym
        if ks in ALONE_KEYS or not e.state & (CTRL | ALT):
            return None
        low = ks.lower()
        if e.state & CTRL and not e.state & ALT and low in ("z", "y"):
            return None
        try:
            box = e.widget.winfo_class() in self.BOXES
        except (AttributeError, tk.TclError):
            box = False
        if box and e.state & CTRL and low in self.BOX_KEYS:
            return None
        self.take_typed()  # (a number typed there counts: the shape may go in with this key)
        self.key_to_domino(e)
        return "break"

    def settings_has_keys(self):
        try:
            f = self.app.focus_get()
        except (KeyError, tk.TclError):
            return self.settings_out  # (a dropdown's list has it: one of ours here)
        return f is not None and self.settings_out and str(f).startswith(str(self.app.settings_home))

    def take_typed(self):
        """A number still being typed in the window (boxes taken on Enter / leaving) is taken now, before a shape
        goes into Domino; the keyboard stays where it is (user 2026-11-18)."""
        if self.settings_has_keys():
            self.app.commit_typing()  # (the side panel's own way: widgets.leave_box boxes)

    def give_keys_back(self):
        """The keyboard leaves the window (a number being typed is taken on leaving it)."""
        if not self.settings_has_keys():
            return
        try:
            self.roll.focus_force()
            self.roll.update_idletasks()
        except tk.TclError:
            return
        d = self.overlay.domino
        if not self.drawing() and d is not None and not self.busy:
            dl.to_domino(d)
