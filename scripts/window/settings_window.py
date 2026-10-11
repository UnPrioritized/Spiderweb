"""The Settings window (the ⚙ button at the end of the toolbar): the app's own settings, not the project's: no undo.
Tabs: Look (light / dark, Size / Font / Text size, saved in user/settings.ini; the note colour palettes,
palettes.py),
Updates (how often to look; kept with the window settings in the autosave), later Language. Never opens by itself
(user)."""

import tkinter as tk
from tkinter import colorchooser, font as tkfont, messagebox, ttk

from files import settings
from files.lang import tr
from files.system import WINDOWS
from window import button_look, look, palettes
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


class NameDialog(tk.Toplevel):
    """Asks a palette's name. OK with a name that won't do (empty, or check(name) gives a reason) keeps it open: the
    question turns into that reason in red for WRONG_MS, then comes back (user: it closed doing nothing).
    .name = the name given, None = cancelled."""
    WRONG_MS = 2000

    def __init__(self, parent, title, prompt, initial="", check=None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.name, self.check, self.prompt, self._back = None, check, prompt, None
        box = ttk.Frame(self, padding=(12, 10, 12, 12))
        box.pack(fill="both", expand=True)
        # the text's room is fixed (user: the window grew and shrank as the text changed): as tall as the longest
        # text it can show, plus a line for a long name in the "taken" one; only ever grows past that
        wrap = round(320 * parent.app.scale)
        self.holder = ttk.Frame(box, width=wrap)
        self.holder.pack(anchor="w", fill="x")
        self.holder.pack_propagate(False)
        self.label = ttk.Label(self.holder, wraplength=wrap, justify="left")
        self.label.pack(anchor="nw")
        tall = 0
        for text in (prompt, tr("palette.name_empty"), tr("palette.name_taken", name="M" * 12)):
            self.label.config(text=text)
            self.label.update_idletasks()
            tall = max(tall, self.label.winfo_reqheight())
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        self.holder.config(height=tall + line)
        self.label.config(text=prompt)
        self.var = tk.StringVar(value=initial)
        self.entry = ttk.Entry(box, textvariable=self.var, width=36)
        self.entry.pack(fill="x", pady=(6, 10))
        self.entry.select_range(0, "end")
        row = ttk.Frame(box)
        row.pack(anchor="e")
        ttk.Button(row, text=tr("palette.ok"), command=self.ok).pack(side="left", padx=(0, 6))
        ttk.Button(row, text=tr("palette.cancel"), command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry("+%d+%d" % (parent.winfo_rootx() + 30, parent.winfo_rooty() + 30))
        self.entry.focus_set()

    def ok(self):
        name = clean_name(self.var.get())
        wrong = tr("palette.name_empty") if not name else self.check(name) if self.check else None
        if wrong:
            self.label.config(text=wrong, foreground=look.ERROR)
            self.label.update_idletasks()
            if self.label.winfo_reqheight() > self.holder.winfo_height():  # (a really long name: grows, never shrinks)
                self.holder.config(height=self.label.winfo_reqheight())
            if self._back:
                self.after_cancel(self._back)
            self._back = self.after(self.WRONG_MS, self.question_back)
            self.entry.focus_set()
            return
        self.name = name
        self.destroy()

    def question_back(self):
        self._back = None
        if self.winfo_exists():
            self.label.config(text=self.prompt, foreground="")


def ask_name(parent, title, prompt, initial="", check=None):
    """The name typed in a NameDialog (waits for it), None = cancelled."""
    d = NameDialog(parent, title, prompt, initial, check)
    d.grab_set()
    d.wait_window()
    return d.name


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
    JUMP_MS = 1000  # (Font list open: letters typed closer together than this make one word)

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
        self.build_size(page, 2)
        self.build_palettes(page, 11)
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

    # ------------------------------------------------------------ size, font, text size (look.apply_size)

    def build_size(self, page, r):
        """Size / Font / Text size: saved at once, put on at the next start (an orange line says so while what's
        saved isn't what's showing)."""
        ttk.Separator(page).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        self.size_names = {k: tr("look.size_system") if k == "system" else f"{k} %" for k in look.SIZES}
        self.font_default = tr("look.font_default", font=look.USUAL_FAMILY)
        self.text_names = {k: tr("look.text_" + k) for k in look.TEXT_STEPS}
        self.size_var = tk.StringVar(value=self.size_names[look.read_size()])
        self.font_var = tk.StringVar(value=look.read_font() or self.font_default)
        self.text_var = tk.StringVar(value=self.text_names[look.read_text()])
        self.button_names = {k: tr("look.button_" + k) for k in button_look.STYLES}
        self.button_var = tk.StringVar(value=self.button_names[button_look.read_style()])
        self.edge_names = {k: f"{k} px" for k in button_look.EDGES}
        self.edge_var = tk.StringVar(value=self.edge_names[button_look.read_edge()])
        self.lines_names = {k: f"{k} px" for k in look.LINE_WIDTHS}
        self.lines_var = tk.StringVar(value=self.lines_names[look.read_lines()])
        families = [f for f in tkfont.families(self) if not f.startswith("@")]  # (@ = sideways fonts)
        rows = (("look.size", self.size_var, list(self.size_names.values()), 18, "look.size_tip"),
                ("look.font", self.font_var, [self.font_default] + sorted(set(families), key=str.lower), 24,
                 "look.font_tip"),
                ("look.text", self.text_var, list(self.text_names.values()), 18, "look.text_tip"),
                ("look.button", self.button_var, list(self.button_names.values()), 18, "look.button_tip"),
                ("look.edge", self.edge_var, list(self.edge_names.values()), 18, "look.edge_tip"),
                ("look.lines", self.lines_var, list(self.lines_names.values()), 18, "look.lines_tip"))
        for k, (label, var, values, width, tip) in enumerate(rows):
            ttk.Label(page, text=tr(label)).grid(row=r + 1 + k, column=0, sticky="e", padx=(0, 8), pady=3)
            row = ttk.Frame(page)
            row.grid(row=r + 1 + k, column=1, sticky="w", pady=3)
            box = ttk.Combobox(row, textvariable=var, values=values, state="readonly", width=width)
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", lambda e: self.save_size())
            Tooltip(box, tr(tip))
            setattr(self, label.replace(".", "_") + "_box", box)
        self.font_values = list(self.look_font_box.cget("values"))
        self.font_search()
        self.size_reset = ttk.Button(row, text=tr("look.reset"), takefocus=False, command=self.reset_size)
        self.size_reset.pack(side="left", padx=(8, 0))
        Tooltip(self.size_reset, tr("look.reset_tip"))
        self.size_note = ttk.Label(page, text=tr("look.restart_size"), foreground=look.WARN,
                                   wraplength=round(300 * self.app.scale), justify="left")
        self.size_note.grid(row=r + 7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.show_size_note()
        self.build_preview(page, r + 8)

    def build_preview(self, page, r):
        """Pieces that work but change nothing else (user: tick, open the dropdown, step / type the number), drawn
        with the picked Size / Font / Text size at once, while the rest of the app waits for the restart. Own styles ("Preview.T..."), so the real widgets never change."""
        box = self.preview = ttk.LabelFrame(page, text=tr("look.preview"), padding=(10, 6, 10, 8))
        box.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        Tooltip(box, tr("look.preview_tip"))
        self.pv_fonts = {k: tkfont.Font(self, family=look.USUAL_FAMILY, size=9) for k in ("text", "heading", "hint")}
        self.pv_fonts["heading"].configure(weight="bold")
        st = ttk.Style(self)
        for name in ("TButton", "TCheckbutton", "TCombobox", "TSpinbox"):
            st.configure("Preview." + name, font=self.pv_fonts["text"])
        self.pv_tick = tk.BooleanVar(self, value=True)
        ttk.Label(box, text=tr("look.preview_heading"), font=self.pv_fonts["heading"]).pack(anchor="w")
        ttk.Label(box, text=tr("look.preview_text"), font=self.pv_fonts["text"]).pack(anchor="w")
        ttk.Label(box, text=tr("look.preview_hint"), font=self.pv_fonts["hint"], foreground=look.HINT).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        self.pv_widgets = (
            ttk.Button(row, text=tr("look.preview_button"), style="Preview.TButton", takefocus=False),
            ttk.Checkbutton(row, text=tr("look.preview_tick"), variable=self.pv_tick, style="Preview.TCheckbutton",
                            takefocus=False),
            ttk.Combobox(row, values=[tr("look.preview_drop"), tr("look.preview_drop2"), tr("look.preview_drop3")],
                         state="readonly", width=12, style="Preview.TCombobox", font=self.pv_fonts["text"],
                         takefocus=False),
            ttk.Spinbox(row, from_=0, to=999, width=5, style="Preview.TSpinbox", font=self.pv_fonts["text"]))
        drop = self.pv_widgets[2]
        drop.set(tr("look.preview_drop"))
        drop.bind("<<ComboboxSelected>>", lambda e: drop.selection_clear())
        try:  # (its open list in the preview's font too)
            self.tk.call(self.tk.call("ttk::combobox::PopdownWindow", drop) + ".f.l", "configure", "-font",
                         self.pv_fonts["text"])
        except tk.TclError:
            pass
        self.pv_widgets[3].set("120")
        for k, w in enumerate(self.pv_widgets):
            w.pack(side="left", padx=(0 if k == 0 else 8, 0))
        # the usual paddings, to grow / shrink with the picked Size
        self.pv_pads = {name: st.lookup(name, "padding") for name in ("TButton", "TCheckbutton", "TCombobox",
                                                                       "TSpinbox")}
        # (the dropdown's / number box's arrows too; the tick box's square has no size setting: it stays)
        self.pv_arrows = {name: st.lookup(name, "arrowsize") for name in ("TCombobox", "TSpinbox")}
        self.show_preview()

    def show_preview(self):
        """The preview pieces at the picked size: fonts in pixels for the picked scaling (Tk's own is fixed
        until the restart), paddings and arrows grown by as much; the button, dropdown and number box in the picked
        Button style (own styles "Pv<Style>.T...", made the first time it's picked)."""
        size, family, text = self.picked_size()
        dpi = look.SCREEN_DPI if size == "system" else 96 * size / 100
        ratio = dpi / self.winfo_fpixels("1i")
        step = look.TEXT_STEPS[text]
        family = family or look.USUAL_FAMILY
        for key, points in (("text", look.USUAL_SIZE), ("heading", 10), ("hint", look.USUAL_SIZE)):
            self.pv_fonts[key].configure(family=family, size=-round(max(6, points + step) * dpi / 72))
        st = ttk.Style(self)
        style = button_look.shown_style(self.picked_button())
        edge = self.picked_edge()
        self.look_edge_box.config(state="disabled" if style == "system" else "readonly")  # (it can't change those)
        target = f"Pv{style.title()}{edge}."
        kinds = ("TButton", "TCombobox", "TSpinbox")
        if style != "system":
            button_look.dress(st, target, style, look.parts())
        button_look.put_on(self, st, target, style, look.parts(), kinds, edge)
        for name in kinds:
            st.configure(target + name, font=self.pv_fonts["text"])
        for w, name in zip((self.pv_widgets[0], self.pv_widgets[2], self.pv_widgets[3]), kinds):
            w.configure(style=target + name)
        for name, pad in self.pv_pads.items():
            if pad:
                try:
                    st.configure((target if name in kinds else "Preview.") + name, padding=[
                        round(self.winfo_fpixels(p) * ratio) for p in self.tk.splitlist(str(pad))])
                except tk.TclError:
                    pass
        for name, value in self.pv_arrows.items():
            if value:
                try:
                    st.configure(target + name, arrowsize=round(self.winfo_fpixels(value) * ratio))
                except tk.TclError:
                    pass

    def font_search(self):
        """The Font box can be typed in (user): the list keeps only the fonts holding what's typed (starting with
        it first), Enter takes the one typed or the first left; leaving the box with a font not in the list (or
        Escape) puts the saved one back."""
        box = self.look_font_box
        box.config(state="normal")

        def hits():
            typed = self.font_var.get().strip().casefold()
            first = [f for f in self.font_values if f.casefold().startswith(typed)]
            return first + [f for f in self.font_values if typed in f.casefold() and f not in first]

        def typed(e):
            if e.keysym not in ("Return", "KP_Enter", "Escape", "Up", "Down", "Tab", "Left", "Right", "Home", "End"):
                box.config(values=hits() or self.font_values)

        def enter(e):
            name = self.font_var.get().strip()
            found = next((f for f in self.font_values if f.casefold() == name.casefold()), None) or next(
                iter(hits()), None)
            if found:
                self.font_var.set(found)
                box.config(values=self.font_values)
                box.icursor("end")
                self.save_size()
            else:
                back()
            return "break"

        def back(e=None):
            self.font_var.set(look.read_font() or self.font_default)
            box.config(values=self.font_values)
            return "break"

        def left(e):  # (focus going into its own open list doesn't count)
            def check():
                focus = self.tk.call("focus")
                if not str(focus).startswith(str(box)) and self.font_var.get() not in self.font_values:
                    back()
            self.after_idle(check)

        # typing while its list is open jumps to the first font starting with the letters typed (user; a pause of
        # JUMP_MS starts a new word)
        jump = {"text": "", "at": 0}
        lb = self.tk.call("ttk::combobox::PopdownWindow", box) + ".f.l"

        def jump_to(char, when):
            if len(char) != 1 or not char.isprintable():
                return
            when = int(when)
            jump["text"] = (jump["text"] if when - jump["at"] < self.JUMP_MS else "") + char.casefold()
            jump["at"] = when
            values = self.tk.splitlist(self.tk.call(lb, "get", 0, "end"))
            k = next((i for i, f in enumerate(values) if f.casefold().startswith(jump["text"])), None)
            if k is None and len(jump["text"]) > 1:  # (the same letter again: the next font with it)
                jump["text"] = char.casefold()
                now = int(self.tk.call(lb, "index", "active"))
                k = next((i for i, f in list(enumerate(values))[now + 1:] + list(enumerate(values))[:now + 1]
                          if f.casefold().startswith(jump["text"])), None)
            if k is not None:
                self.tk.call(lb, "selection", "clear", 0, "end")
                self.tk.call(lb, "selection", "set", k)
                self.tk.call(lb, "activate", k)
                self.tk.call(lb, "see", k)
        self.font_jump = jump_to
        self.tk.call("bind", lb, "<KeyPress>", "+" + self.register(jump_to) + " %A %t")
        box.bind("<KeyRelease>", typed)
        box.bind("<Return>", enter)
        box.bind("<KP_Enter>", enter)
        box.bind("<Escape>", back)
        box.bind("<FocusOut>", left)
        box.bind("<<ComboboxSelected>>", lambda e: (box.config(values=self.font_values), box.selection_clear(),
                                                     self.save_size()))

    def picked_size(self):
        """(size, font, text) as picked in the three lists (a font half typed: the saved one)."""
        size = next(k for k, n in self.size_names.items() if n == self.size_var.get())
        family = self.font_var.get()
        if family not in self.font_values:
            family = look.read_font() or self.font_default
        family = "" if family == self.font_default else family
        text = next(k for k, n in self.text_names.items() if n == self.text_var.get())
        return size, family, text

    def picked_button(self):
        return next(k for k, n in self.button_names.items() if n == self.button_var.get())

    def picked_edge(self):
        return next(k for k, n in self.edge_names.items() if n == self.edge_var.get())

    def picked_lines(self):
        return next(k for k, n in self.lines_names.items() if n == self.lines_var.get())

    def save_size(self):
        size, family, text = self.picked_size()
        try:
            settings.put("look", "size", size)
            settings.put("look", "font", family)
            settings.put("look", "text", text)
            settings.put("look", "button", self.picked_button())
            settings.put("look", "edge", self.picked_edge())
            settings.put("look", "lines", self.picked_lines())
        except OSError:
            messagebox.showerror(tr("look.title"), tr("look.not_saved"), parent=self)
        if look.LINES != self.picked_lines():  # (shown at once in the piano roll: no restart)
            look.LINES = self.picked_lines()
            self.app.roll.request_redraw()
        self.show_size_note()
        self.show_preview()

    def reset_size(self):
        """Size, Font, Text size and Buttons back to the usual ones (the look and note colours stay)."""
        self.size_var.set(self.size_names["system"])
        self.font_var.set(self.font_default)
        self.text_var.set(self.text_names["normal"])
        self.button_var.set(self.button_names["system"])
        self.edge_var.set(self.edge_names[1])
        self.lines_var.set(self.lines_names[1])
        self.save_size()

    def show_size_note(self):
        style = button_look.shown_style(button_look.read_style())
        if ((look.read_size(), look.read_font(), look.read_text()) == look.RUNNING and style == button_look.RUNNING
                and (style == "system" or button_look.read_edge() == button_look.RUNNING_EDGE)):
            self.size_note.grid_remove()
        else:
            self.size_note.grid()

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

    def change_colours(self, make):
        """make(colours) -> the new colours, None = no change; saved and shown at once. On Default the colours are
        picked first, then the copy's name asked (a cancelled colour picker makes no copy)."""
        try:
            now = palettes.picked()
            colours = make(palettes.colours_of(now))
            if colours is None:
                return
            if now == palettes.DEFAULT:
                name = self.ask_name(tr("palette.copy_title"), "", prompt=tr("palette.copy_ask"))
                if name is None:
                    return
                palettes.save(name, colours)
                palettes.use(name, self.app)
            else:
                palettes.save(now, colours, keep=palettes.user_palettes()[now][0])
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
        """A palette name typed (empty / taken: said in red, the dialog stays open), None = cancelled."""
        return ask_name(self, title, prompt or tr("palette.name_ask"), name,
                        check=lambda n: tr("palette.name_taken", name=n) if palettes.taken(n, but) else None)

    def new_palette(self):
        """A new palette: a copy of the one in use (its colours are changed later), picked."""
        now = palettes.picked()
        k = 2
        while now != palettes.DEFAULT and palettes.taken(f"{now} {k}"):  # (the first number not taken)
            k += 1
        name = self.ask_name(tr("palette.new_title"), "" if now == palettes.DEFAULT else f"{now} {k}")
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
