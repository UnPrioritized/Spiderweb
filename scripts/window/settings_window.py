"""The Settings window (the ⚙ button at the end of the toolbar): the app's own settings, saved in user/settings.ini
(files/settings.py), not the project's: no undo. Tabs: Look (light / dark; more look settings and the note colour
palettes later), later Language. Opens by itself once, on the first start that has no "shown" mark (user: Linux
setups vary, so the look is worth a look first)."""

import tkinter as tk
from tkinter import messagebox, ttk

from files import settings
from files.lang import tr
from files.system import WINDOWS
from window import look
from window.widgets import remember_place

FIRST_START = True  # (the saved tests turn it off: every test starts with no settings.ini)


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


def first_start(app):
    """Opens the Settings window on the first start (marked as shown in settings.ini, so only once)."""
    if not FIRST_START or settings.get("settings", "shown"):
        return
    try:
        settings.put("settings", "shown", "yes")
    except OSError:  # (can't be written: opening it every start would be worse than never)
        return
    open_settings(app)


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
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()  # (first time: in the middle of the main window)
        self.geometry("+%d+%d" % (app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2,
                                  app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3))
        remember_place(self, "settings")
        self.focus_set()
