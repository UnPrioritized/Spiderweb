"""The Settings window (the ⚙ button at the end of the toolbar): the app's own settings, not the project's: no undo.
Tabs: Look (light / dark, saved in user/settings.ini; more look settings and the note colour palettes later),
Updates (how often to look; kept with the window settings in the autosave), later Language. Never opens by itself
(user)."""

import tkinter as tk
from tkinter import messagebox, ttk

from files.lang import tr
from files.system import WINDOWS
from window import look
from window.updates import often_box
from window.widgets import remember_place


def look_box(parent):
    """Light / Dark / Follow Windows (only on Windows). Saved at once; a different look than the one showing = a
    message to restart (user: it's put on at the next start)."""
    names = {k: tr("look." + k) for k in look.LOOKS if k != "windows" or WINDOWS}
    var = tk.StringVar(value=names.get(look.read_look(), names["light"]))
    box = ttk.Combobox(parent, textvariable=var, values=list(names.values()), state="readonly", width=18)

    def picked(e):
        how = next(k for k, n in names.items() if n == var.get())
        try:
            look.save_look(how)
        except OSError:
            messagebox.showerror(tr("look.title"), tr("look.not_saved"), parent=box.winfo_toplevel())
            var.set(names.get(look.read_look(), names["light"]))  # (back to the pick that's saved)
            return
        if look.is_dark(how) != look.DARK:
            messagebox.showinfo(tr("look.title"), tr("look.restart"), parent=box.winfo_toplevel())
    box.bind("<<ComboboxSelected>>", picked)
    return box


def open_settings(app):
    w = getattr(app, "settings_window", None)
    if w is not None and w.winfo_exists():
        w.deiconify()
        w.lift()
        w.focus_set()
    else:
        app.settings_window = SettingsWindow(app)
    return app.settings_window


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("settings.title"))
        self.transient(app)
        self.resizable(False, False)
        tabs = self.tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=8, pady=8)
        page = ttk.Frame(tabs, padding=(12, 10, 12, 12))
        tabs.add(page, text=tr("settings.look_tab"))
        ttk.Label(page, text=tr("look.look")).grid(row=0, column=0, sticky="e", padx=(0, 8), pady=3)
        self.look = look_box(page)
        self.look.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(page, text=tr("settings.look_hint"), foreground=look.HINT, wraplength=round(300 * app.scale),
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        # updates: how often to look, and looking now (the answer shows under the row)
        page = ttk.Frame(tabs, padding=(12, 10, 12, 12))
        tabs.add(page, text=tr("settings.updates_tab"))
        updates = app.updates
        row = ttk.Frame(page)
        row.pack(anchor="w")
        ttk.Label(row, text=tr("updates.check_for_updates")).pack(side="left")
        often_box(row, updates).pack(side="left", padx=(6, 8))
        status = self.update_status = ttk.Label(page, text="", foreground=look.HINT,
                                                wraplength=round(360 * app.scale), justify="left")
        status.pack(anchor="w", pady=(6, 0))
        ttk.Button(row, text=tr("updates.check_now"), takefocus=False, command=lambda: (
            status.config(text=tr("updates.checking")),
            updates.check(report=lambda msg: status.winfo_exists() and status.config(text=msg)))).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()  # (first time: in the middle of the main window)
        self.geometry("+%d+%d" % (app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2,
                                  app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3))
        remember_place(self, "settings")
        self.focus_set()
