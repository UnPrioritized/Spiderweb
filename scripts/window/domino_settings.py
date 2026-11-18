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
        home.bind("<Escape>", self.settings_escape)
        self.settings_out = True
        app.build_points()  # (the Point / Tick / Pitch table left out while here)

    def put_settings_back(self):
        """Back into the side panel where it was (stop)."""
        if not self.settings_out:
            return
        app = self.app
        home = app.settings_home
        self.settings_out = False
        try:
            home.unbind("<Escape>")
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

    def place_settings(self):
        """Under the bar's left edge the first time, else where it was dragged to (against the bar, so it moves
        along with Domino), kept on the screen."""
        from files import system
        app, home, bar = self.app, self.app.settings_home, self.bar
        try:
            bar.update_idletasks()
            home.update_idletasks()
            at = self.saved_settings_at()
            bx, by = bar.winfo_rootx(), bar.winfo_rooty()
            x, y = (bx + at[0], by + at[1]) if at else (bx, by + bar.winfo_height() + GAP)
            w, h = home.winfo_reqwidth(), home.winfo_reqheight()
            left, top, right, bottom = system.work_area(bar, x + w // 2, y + 20)
            x, y = min(max(x, left), right - w), max(min(y, bottom - h - 30), top)  # (30: its title bar)
            app.tk.call("wm", "geometry", home, f"+{x}+{y}")
            home.update_idletasks()
            self._settings_moved = (x, y)
            self._settings_bar = (bx, by)
        except tk.TclError:
            pass

    def settings_tick(self):
        """Overlay tick: a drag of the window remembered; the bar moved (Domino moved) = the window goes along."""
        if not self.settings_out or not self.settings_on.get():
            return
        self.settings_moved()
        try:
            if self.bar.winfo_viewable() and (self.bar.winfo_rootx(), self.bar.winfo_rooty()) != self._settings_bar:
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
