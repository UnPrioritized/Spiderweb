"""Mixed into App: window-wide shortcuts and which window has the keyboard."""

import tkinter as tk
from tkinter import ttk


class AppKeys:
    def in_drawer(self, e):
        """Keys pressed in the drawer window are the drawer's, not the piano roll's."""
        return bool(self.drawer) and str(e.widget).startswith(str(self.drawer))

    def hotkey(self, fn, main_only=False, while_held=False):
        """A window-wide shortcut that leaves typing boxes alone. main_only: only while the main window has the
        keyboard (not in a pop-up). while_held: it works while the mouse holds a shape on the piano roll too (the
        others do nothing then, user: a flip was undone by the next mouse move, a join broken)."""
        def handler(e):
            w = e.widget
            if self.in_drawer(e):
                return None
            if self.hz_window and str(w).startswith(str(self.hz_window)):
                return None  # (the Hz bass window: its own keys only, nothing done to the main piano roll behind)
            if self.image_window and str(w).startswith(str(self.image_window)):
                return None  # (the image window and its pop-ups: Ctrl+C there copies their text, not the shapes)
            if self.app_only(w):
                return None  # (Settings, Help, the update popup: the shapes behind stay as they are)
            if self.grabbed_elsewhere():
                return None  # (a window like Range… is working on the picked shapes: nothing changes behind it)
            if main_only and not (isinstance(w, tk.Misc) and (w.winfo_toplevel() is self or w.winfo_toplevel()
                                                                in self.companion_tops)):
                return None  # (companion_tops: drawing on Domino, window/domino_companion.py, is the main window)
            if isinstance(w, (tk.Entry, ttk.Entry)) and str(w.cget("state")) != "readonly":
                return None
            if self.tracks_shown() and fn not in (self.toggle_tracks, self.toggle_play, self.generate,
                                                   self.new_project, self.open_project):
                # (the track list shows: shortcuts never change shapes out of sight; the list's own keys work there,
                # group E)
                tl = self.track_list
                own = {self.select_all: tl.select_all, self.copy_selected: tl.key_copy, self.cut_selected: tl.key_cut,
                       self.paste: tl.key_paste, self.copy_to_domino: self.copy_to_domino}.get(fn) or getattr(
                           fn, "in_list", None)
                if own is not None and not self.track_list.busy():
                    own()
                return "break"
            if self.box_drawn() or not while_held and (self.roll.holding() or self.vel.edit or self.held_scrub
                                                       or getattr(self, "tempo_pane", None) is not None
                                                       and self.tempo_pane.drag):
                return "break"  # (a velocity line / something in the Tempo pane held: nothing changes under it)
            fn()
            return "break"
        return handler

    def typing_in(self, box):
        """box has the keyboard (or its dropdown list is open)."""
        try:
            return self.focus_get() is box
        except (KeyError, tk.TclError):  # (a dropdown's list has it)
            return True

    def app_only(self, w):
        """w is in a window about Spiderweb itself (Settings, Help, a tip, the update popup: their class has
        APP_ONLY): keys pressed there never change the shapes behind it."""
        if self.in_settings(w):
            return True
        try:
            return getattr(w.winfo_toplevel(), "APP_ONLY", False)
        except (AttributeError, tk.TclError):  # (Tk's own widgets come as a name: a dropdown's list)
            return False

    def in_settings(self, w):
        """w is in the Settings window (or one of its pop-ups)."""
        sw = getattr(self, "settings_window", None)
        return sw is not None and str(w).startswith(str(sw))

    def grabbed_elsewhere(self):
        """A pop-up holds the mouse and keyboard (grab_set: Range…, formula Custom…, tumour graph, snap picker)."""
        try:
            g = self.grab_current()
        except (KeyError, tk.TclError):  # (a grab on a window tkinter doesn't know, e.g. a message box)
            return True
        return g is not None and g.winfo_toplevel() is not self

    def in_hz(self, widget=None):
        """The Hz bass window (or a window of its own) has the keyboard (widget: the one a key was pressed in)."""
        if not self.hz_window:
            return False
        if widget is None:
            try:
                widget = self.focus_get()
            except (KeyError, tk.TclError):  # (a dropdown's list has it)
                return False
        return widget is not None and str(widget).startswith(str(self.hz_window))
