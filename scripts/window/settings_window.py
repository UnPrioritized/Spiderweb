"""The Settings window (the ⚙ button at the end of the toolbar): the app's own settings, not the project's: no undo.
Tabs: Look (light / dark, saved in user/settings.ini; the note colour palettes, palettes.py; more later),
Updates (how often to look; kept with the window settings in the autosave), later Language. Never opens by itself
(user)."""

import tkinter as tk
from tkinter import colorchooser, messagebox, simpledialog, ttk

from files.lang import tr
from files.system import WINDOWS
from window import look, palettes
from window.drawer import clean_name
from window.updates import often_box
from window.widgets import Tooltip, remember_place


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
        self.build_palettes(page, 2)
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

    # ------------------------------------------------------------ note colours (palettes.py)

    def build_palettes(self, page, r):
        ttk.Separator(page).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        ttk.Label(page, text=tr("palette.label")).grid(row=r + 1, column=0, sticky="e", padx=(0, 8), pady=3)
        self.pal_var = tk.StringVar()
        self.pal_box = ttk.Combobox(page, textvariable=self.pal_var, state="readonly", width=24)
        self.pal_box.grid(row=r + 1, column=1, sticky="w", pady=3)
        self.pal_box.bind("<<ComboboxSelected>>", lambda e: self.pick_palette())
        Tooltip(self.pal_box, tr("palette.tip"))
        # the colours, each over its MIDI channel's number (click one to change it)
        s = self.swatch_size = round(22 * self.app.scale)
        self.number_h = round(16 * self.app.scale)
        ttk.Label(page, text=tr("palette.channel")).grid(row=r + 2, column=0, sticky="se", padx=(0, 8),
                                                         pady=(4, 4))
        self.swatches = tk.Canvas(page, width=palettes.SLOTS * (s + 3), height=s + 2 + self.number_h,
                                  highlightthickness=0, cursor="hand2",
                                  background=look.WINDOW_BG if look.DARK else None)
        self.swatches.grid(row=r + 2, column=1, sticky="w", pady=(4, 4))
        self.swatches.bind("<Button-1>", self.click_swatch)
        Tooltip(self.swatches, tr("palette.swatch_tip"))
        for k, (text, command, tip) in enumerate((
                ("palette.new", self.new_palette, "palette.new_tip"),
                ("palette.rename", self.rename_palette, "palette.rename_tip"),
                ("palette.delete", self.delete_palette, "palette.delete_tip"),
                ("palette.random", self.random_palette, "palette.random_tip"),
                ("palette.gradient", self.gradient_palette, "palette.gradient_tip"))):
            if k % 3 == 0:
                row = ttk.Frame(page)
                row.grid(row=r + 3 + k // 3, column=1, sticky="w", pady=(4, 0))
            b = ttk.Button(row, text=tr(text), command=command, takefocus=False)
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, tr(tip))
            setattr(self, text.replace(".", "_"), b)
        self.show_palettes()

    def shown_name(self, name):
        return tr("palette.default") if name == palettes.DEFAULT else name

    def show_palettes(self):
        """The list, the one in use, its colours; Rename / Delete only for the user's own."""
        names = palettes.names()
        self.pal_names = {self.shown_name(n): n for n in names}
        self.pal_box.config(values=list(self.pal_names))
        now = palettes.picked()
        self.pal_var.set(self.shown_name(now))
        own = ["!disabled"] if now != palettes.DEFAULT else ["disabled"]
        self.palette_rename.state(own)
        self.palette_delete.state(own)
        c, s = self.swatches, self.swatch_size
        c.delete("all")
        for k, (fill, edge) in enumerate(palettes.colours_of(now)):
            x = k * (s + 3)
            c.create_rectangle(x + 1, 1, x + s, s, fill=fill, outline=edge, width=2)
            c.create_text(x + s / 2, s + 2 + self.number_h / 2, text=str(palettes.CHANNELS[k] + 1),
                          fill=look.LABEL, font=look.font(8))

    # editing: Default itself never changes, so an edit on it first makes a copy (asks its name)

    def own_palette(self):
        """The palette to change: the one in use, or a new copy of Default (None = the name wasn't given)."""
        now = palettes.picked()
        if now != palettes.DEFAULT:
            return now
        name = self.ask_name(tr("palette.copy_title"), "", prompt=tr("palette.copy_ask"))
        if name is None:
            return None
        palettes.save(name, palettes.colours_of(now))
        palettes.use(name, self.app)
        return name

    def change_colours(self, make):
        """make(colours) -> the new colours, None = no change; saved and shown at once."""
        try:
            name = self.own_palette()
            if name is None:
                return
            colours = make(palettes.colours_of(name))
            if colours is not None:
                palettes.save(name, colours, keep=palettes.user_palettes()[name][0])
                palettes.apply(colours, self.app)
        except OSError as e:
            messagebox.showerror(tr("palette.title"), tr("palette.not_saved", e=e), parent=self)
        self.show_palettes()

    def click_swatch(self, e):
        k = int(e.x // (self.swatch_size + 3))
        if not 0 <= k < palettes.SLOTS:
            return

        def make(colours):
            got = colorchooser.askcolor(colours[k][0], parent=self,
                                        title=tr("palette.pick_title", channel=palettes.CHANNELS[k] + 1))
            if not got or not got[1]:
                return None
            colours[k] = palettes.with_edge(got[1])
            return colours
        self.change_colours(make)

    def random_palette(self):
        self.change_colours(lambda colours: palettes.random_colours())

    def gradient_palette(self):
        """From a first colour to a last one across every channel (both asked; the first / last channel's now
        to start with)."""
        def make(colours):
            first = colorchooser.askcolor(colours[0][0], parent=self, title=tr("palette.gradient_first"))
            if not first or not first[1]:
                return None
            last = colorchooser.askcolor(colours[-1][0], parent=self, title=tr("palette.gradient_last"))
            if not last or not last[1]:
                return None
            return palettes.gradient(first[1], last[1])
        self.change_colours(make)

    def pick_palette(self):
        try:
            palettes.use(self.pal_names.get(self.pal_var.get(), palettes.DEFAULT), self.app)
        except OSError as e:
            messagebox.showerror(tr("palette.title"), tr("palette.not_saved", e=e), parent=self)
        self.show_palettes()

    def ask_name(self, title, name, but=None, prompt=None):
        """A palette name typed (asked again while it's taken), None = cancelled."""
        prompt = prompt or tr("palette.name_ask")
        while True:
            name = simpledialog.askstring(title, prompt, parent=self, initialvalue=name)
            name = clean_name(name or "")
            if not name:
                return None
            if not palettes.taken(name, but):
                return name
            prompt = tr("palette.name_taken", name=name)

    def new_palette(self):
        """A new palette: a copy of the one in use (its colours are changed later), picked."""
        now = palettes.picked()
        name = self.ask_name(tr("palette.new_title"), "" if now == palettes.DEFAULT else now + " 2")
        if name is None:
            return
        try:
            palettes.save(name, palettes.colours_of(now))
            palettes.use(name, self.app)
        except OSError as e:
            messagebox.showerror(tr("palette.title"), tr("palette.not_saved", e=e), parent=self)
        self.show_palettes()

    def rename_palette(self):
        now = palettes.picked()
        if now == palettes.DEFAULT:
            return
        name = self.ask_name(tr("palette.rename_title"), now, but=now)
        if name is None or name == now:
            return
        try:
            palettes.rename(now, name)
        except OSError as e:
            messagebox.showerror(tr("palette.title"), tr("palette.not_saved", e=e), parent=self)
        self.show_palettes()

    def delete_palette(self):
        now = palettes.picked()
        if now == palettes.DEFAULT or not messagebox.askyesno(tr("palette.delete_title"),
                                                              tr("palette.delete_ask", name=now), parent=self):
            return
        try:
            palettes.delete(now)
            palettes.apply(palettes.colours_of(palettes.DEFAULT), self.app)
        except OSError as e:
            messagebox.showerror(tr("palette.title"), tr("palette.not_saved", e=e), parent=self)
        self.show_palettes()
