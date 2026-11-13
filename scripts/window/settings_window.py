"""The Settings window (the ⚙ button at the end of the toolbar): the app's own settings, not the project's: no undo.
Tabs: General (update checks, tips), Look (light / dark, Size / Font / Text size, saved in user/settings.ini; the
note colour palettes, palettes.py), Sound (MIDI out + Built-in BASSMIDI's settings), Language. All saved in
user/settings.ini. Never opens by itself (user)."""

import os
import tkinter as tk
from tkinter import colorchooser, font as tkfont, messagebox, ttk

from files import lang, settings
from files.lang import tr
from files.playback import AHEAD, BUILTIN, KESTREL, MAESTRO, devices
from files.system import WINDOWS, open_path, work_area
from window import button_look, look, palettes
from window.builtin_settings import BuiltinPanel
from window.drawer import clean_name
from window.updates import often_box
from window.widgets import ShortLabel, Tooltip, keep_width, letter_jump, name_list, remember_place, set_names


def look_box(parent):
    """Light / Dark / Follow Windows (only on Windows). Saved at once; a different look than the one showing = a
    message to restart (user: it's put on at the next start)."""
    names = {k: tr("look." + k) for k in look.LOOKS if k != "windows" or WINDOWS}
    var = tk.StringVar(value=names.get(look.read_look(), names["light"]))
    box = ttk.Combobox(parent, textvariable=var, values=list(names.values()), state="readonly", width=18)

    def picked(e):
        how = list(names)[box.current()]  # (the place: two translated alike stay apart)
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
    old = parent.grab_current()  # (a modal window asking: it gets its hold back after, hunt 2026-11-13: the LFO
    d = NameDialog(parent, title, prompt, initial, check)  # window's was lost, the windows behind it took clicks)
    d.grab_set()
    d.wait_window()
    if old is not None:
        try:
            if old.winfo_exists():
                old.grab_set()
        except tk.TclError:
            pass
    return d.name


def open_settings(app, tab=None):
    """tab = "look" / "roll" / "sound" / "general" / "language": shown at once (none = the one showing / the first)."""
    w = getattr(app, "settings_window", None)
    if w is not None and w.winfo_exists():
        w.deiconify()
        w.lift()
        w.focus_set()
    else:
        w = app.settings_window = SettingsWindow(app)
        app.tips.show("settings", parent=w, wait=True)  # (the first time: what's in it)
    if tab in w.pages:
        w.tabs.select(w.pages[tab])
    return w


PAGE_MOST = 560  # the tallest a Settings page shows at 100 % size, in pixels (taller ones scroll)
NOTE_WRAP = 380  # a status line's width under its control, at 100 % size


class Form:
    """Every Settings page laid out alike, in Adobe Audition's way (user 2026-10-31; a centred name column "felt
    weird"): everything LEFT-aligned; section headings with a line under them; ticks and buttons from the left edge;
    a short name + its control on one row (column 0 only as wide as the page's names); BRIEF texts, the long
    explanation = hover tip. .r = the next free row."""

    def __init__(self, page, app):
        self.page, self.app, self.r = page, app, 0
        page.columnconfigure(1, weight=1)

    def head(self, text):
        """A section heading, a line under it across the page."""
        lb = ttk.Label(self.page, text=text)
        lb.grid(row=self.r, column=0, columnspan=2, sticky="w", pady=(14 if self.r else 0, 0))
        ttk.Separator(self.page).grid(row=self.r + 1, column=0, columnspan=2, sticky="ew", pady=(3, 6))
        self.r += 2
        return lb

    def row(self, label, widget=None, tip=None, pady=3, widget_tip=True):
        """label (text; None = the widget from the left edge) + widget on one row; tip on both (widget_tip=False:
        the widget's own pieces have theirs). Returns the widget."""
        if label:
            lb = ttk.Label(self.page, text=label)
            lb.grid(row=self.r, column=0, sticky="w", padx=(0, 8), pady=pady)
            if tip:
                Tooltip(lb, tip)
        if widget is not None:
            widget.grid(row=self.r, column=1 if label else 0, columnspan=1 if label else 2, sticky="w", pady=pady)
            if tip and widget_tip:
                Tooltip(widget, tip)
        self.r += 1
        return widget

    def note(self, text="", colour=None, tip=None, under=False):
        """A grey (or coloured) status line; under = beneath a named row's control (column 1), else from the left."""
        lb = ttk.Label(self.page, text=text, foreground=colour or look.HINT,
                       wraplength=round(NOTE_WRAP * self.app.scale), justify="left")
        lb.grid(row=self.r, column=1 if under else 0, columnspan=1 if under else 2, sticky="w", pady=(0, 3))
        if tip:
            Tooltip(lb, tip)
        self.r += 1
        return lb

    def span(self, widget, pady=(4, 0)):
        """A widget across both columns (preview box, built-in panel)."""
        widget.grid(row=self.r, column=0, columnspan=2, sticky="ew", pady=pady)
        self.r += 1
        return widget


class ScrollPage(ttk.Frame):
    """A tab whose page scrolls when the screen is too short for the whole window (user 2026-10-16: on a small
    screen at a big Size, Look's bottom row was under the taskbar). .page = the frame to fill."""
    SKIP = (tk.Listbox, tk.Text)  # (they scroll themselves; a clicked dropdown / box takes the wheel: wheel_ready)

    def __init__(self, tabs, padding=(12, 10, 12, 12)):
        super().__init__(tabs)
        self.most = None  # the tallest the page shows (None = all of it)
        c = self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                    bg=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")
        self.bar = ttk.Scrollbar(self, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=self.bar.set)
        c.pack(side="left", fill="both", expand=True)
        self.page = ttk.Frame(c, padding=padding)
        item = c.create_window(0, 0, window=self.page, anchor="nw")
        self.page.bind("<Configure>", lambda e: self.fit())
        # the page as wide as the tab (the widest page sets it): separators run to the edge on every page
        c.bind("<Configure>", lambda e: c.itemconfigure(item, width=max(e.width, self.page.winfo_reqwidth())))

    def fit(self):
        """As big as the page, or as tall as self.most with a scrollbar."""
        w, h = self.page.winfo_reqwidth(), self.page.winfo_reqheight()
        shown = h if self.most is None else max(1, min(h, self.most))
        self.canvas.configure(width=w, height=shown, scrollregion=(0, 0, w, h))
        if h > shown:
            if not self.bar.winfo_manager():
                self.bar.pack(side="right", fill="y", before=self.canvas)
        elif self.bar.winfo_manager():
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)

    def wheel(self, e):
        if (self.bar.winfo_manager() and str(e.widget).startswith(str(self.canvas))
                and not isinstance(e.widget, self.SKIP)):  # (a clicked box keeps the wheel itself)
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")


class SidePages(ttk.Frame):
    """The category list on the left, the picked page on the right (user 2026-10-31: Adobe Audition's Preferences).
    Notebook-like: add(page, text), select(page / index) or select() = the shown page's name, tab(page / index,
    "text"). Every page sits in one cell, so the window keeps the biggest page's size."""

    def __init__(self, parent):
        super().__init__(parent)
        self.list = tk.Listbox(self, activestyle="none", exportselection=False, width=16, highlightthickness=1,
                               font=look.font(look.USUAL_SIZE))
        self.list.pack(side="left", fill="y", padx=(0, 14))
        self.area = ttk.Frame(self)
        self.area.pack(side="left", fill="both", expand=True)
        self.area.rowconfigure(0, weight=1)
        self.area.columnconfigure(0, weight=1)
        self.shown, self.names, self.on_show = [], [], {}
        self.list.bind("<<ListboxSelect>>", lambda e: self.list.curselection() and self.select(
            self.list.curselection()[0]))

    def add(self, page, text):
        self.shown.append(page)
        self.names.append(text)
        self.list.insert("end", " " + text)
        page.grid(in_=self.area, row=0, column=0, sticky="nsew")  # (all kept in the cell: the biggest sets its size)
        self.select(self._now if self.shown[:-1] else 0)

    _now = 0

    def index(self, which):
        return which if isinstance(which, int) else self.shown.index(which)

    def select(self, which=None):
        if which is None:
            return str(self.shown[self._now]) if self.shown else ""
        k = self._now = self.index(which)
        self.shown[k].tkraise()
        self.list.selection_clear(0, "end")
        self.list.selection_set(k)
        self.list.activate(k)  # (arrow keys go on from the page shown, also one opened from elsewhere: hunt 11-07)
        self.list.see(k)
        run = self.on_show.get(str(self.shown[k]))
        if run:
            run()

    on_show = {}  # page name -> run each time it's shown (set per window in __init__)

    def tab(self, which, option="text"):
        return self.names[self.index(which)]


class SettingsWindow(tk.Toplevel):

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("settings.title"))
        self.transient(app)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=(10, 10, 10, 10))
        body.pack(fill="both", expand=True)
        # (Audition's Preferences, user 2026-10-31: categories on the left, small pages; every change saved at once,
        # so one Close button, no OK / Cancel: user's pick)
        tabs = self.tabs = SidePages(body)
        tabs.pack(fill="both", expand=True)
        bottom = ttk.Frame(body)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text=tr("settings.close"), takefocus=False, command=self.close).pack(side="right")
        self.pages = {}  # page name -> its ScrollPage (.page = what's in it)

        def page(name, text):
            tab = self.pages[name] = ScrollPage(tabs.area, padding=(4, 0, 12, 4))
            tabs.add(tab, text)
            return Form(tab.page, app)
        # General first (user 2026-10-18): updates (how often to look, and looking now: the answer shows under the
        # row), tips, .spwb files
        f = page("general", tr("settings.general_tab"))
        f.head(tr("settings.head_updates"))
        updates = app.updates
        row = f.row(tr("updates.check_for_updates"), ttk.Frame(f.page))
        often_box(row, updates).pack(side="left", padx=(0, 8))
        status = self.update_status = f.note(under=True)
        status.grid_remove()

        def say(msg):
            if status.winfo_exists():
                status.config(text=msg)
                status.grid()
        ttk.Button(row, text=tr("updates.check_now"), takefocus=False, command=lambda: (
            say(tr("updates.checking")), updates.check(report=say))).pack(side="left")
        self.build_tips(f)
        self.build_file_link(f)
        f = page("look", tr("settings.look_tab"))
        f.head(tr("settings.look_tab"))
        self.look = f.row(tr("look.look"), look_box(f.page), tip=tr("settings.look_hint"))
        self.build_size(f)
        # Piano roll after Look (user 2026-11-08): the space past the edges + the notes kept there
        self.build_space(page("roll", tr("settings.roll_tab")))
        f = page("colours", tr("settings.cat_colours"))
        self.build_palettes(f)
        self.build_stacked(f)
        self.build_keep_colours(f)
        self.build_sound(page("sound", tr("settings.sound_tab")))
        f = page("copying", tr("settings.cat_copying"))
        self.build_copying(f)
        self.build_domino(f)
        self.build_indexes(page("loaded", tr("settings.cat_loaded")))
        self.build_language(page("language", tr("settings.language_tab")))
        tabs.select(0)
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<MouseWheel>", lambda e: self.pages and self.nametowidget(self.tabs.select()).wheel(e), add="+")
        self.fit_pages()
        self.update_idletasks()  # (first time: in the middle of the main window)
        self.geometry("+%d+%d" % (app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2,
                                  app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3))
        remember_place(self, "settings")
        self.focus_set()

    def fit_pages(self):
        """The pages no taller than the screen above the taskbar leaves room for (the rest scrolls). Kept for pages
        that grow later (palettes, Built-in BASSMIDI's settings)."""
        app = self.app
        tabs = list(self.pages.values())
        for t in tabs:  # (all of each page first: the window's own height)
            t.most = None
            t.fit()
        self.update_idletasks()
        left, top, right, bottom = work_area(self, app.winfo_rootx() + app.winfo_width() // 2,
                                             app.winfo_rooty() + app.winfo_height() // 2)
        k = getattr(look, "SCREEN_DPI", 96) / 96
        room = bottom - top - round(40 * k)  # (the title bar + edges)
        tallest = max(t.page.winfo_reqheight() for t in tabs)
        most = max(round(150 * app.scale), tallest - (self.winfo_reqheight() - room))
        most = min(most, round(PAGE_MOST * app.scale))  # (user 2026-10-29: grew too long; the rest scrolls)
        for t in tabs:
            t.most = most
            t.fit()

    def close(self):
        """Esc / X: a voice limit / an Opacity typed without Enter is taken first (a wrong one: the last good stays)."""
        panel = self.app.builtin_window
        if panel is not None and panel.winfo_exists():
            panel.take_voices()
        take = getattr(self, "take_opacity", None)
        if take is not None and self.opacity_box.winfo_exists() and not self.opacity_box.instate(["disabled"]):
            take(True)
        self.destroy()

    def tick(self, page, key, var, command, tip=None, **kw):
        c = ttk.Checkbutton(page, text=tr(key), variable=var, takefocus=False, command=command, **kw)
        if tip:
            Tooltip(c, tip)
        return c

    def build_tips(self, f):
        """The same tick and button as at the bottom of the Help window (the tick shares app.tips.on)."""
        tips = self.app.tips
        f.head(tr("settings.head_tips"))
        f.row(None, self.tick(f.page, "help.show_a_tip_the_first_time", tips.on, self.app.schedule_autosave))
        row = f.row(None, ttk.Frame(f.page))
        note = self.tips_note = ttk.Label(row, text="", foreground=look.GOOD)

        def again():
            tips.reset()
            tips.on.set(True)
            note.config(text=tr("help.done_every_tip_shows_again_the"))
        ttk.Button(row, text=tr("help.show_all_tips_again"), takefocus=False, command=again).pack(side="left")
        note.pack(side="left", padx=(6, 0))

    def build_space(self, f):
        """"Space past the keys" / "Space before the start" (user 2026-11-06): how far the main piano roll may scroll
        (and shapes go) past the lowest / highest key and before beat 0. No notes are made there. Read by place."""
        from files.settings import SPACE_BARS, SPACE_KEYS
        app = self.app
        f.head(tr("settings.head_space"))
        self.space_boxes = []
        for var, steps, row, tip, word in ((app.space_keys, SPACE_KEYS, "settings.space_keys",
                                            "settings.space_keys_tip", "settings.space_keys_n"),
                                           (app.space_bars, SPACE_BARS, "settings.space_bars",
                                            "settings.space_bars_tip", "settings.space_bars_n")):
            names = [tr("settings.space_off") if n == 0 else lang.trn(word, n, n=n) for n in steps]
            box = f.row(tr(row), ttk.Combobox(f.page, values=names, state="readonly", width=16), tip=tr(tip))
            box.current(steps.index(var.get()) if var.get() in steps else 0)

            def picked(e, box=box, var=var, steps=steps):
                var.set(steps[box.current()])
                box.selection_clear()
                app.roll.space_changed()
                app.schedule_autosave()
                self.sync_keep()
            box.bind("<<ComboboxSelected>>", picked)
            self.space_boxes.append(box)
        self.build_keep(f)

    def build_keep(self, f):
        """"Keep notes made there" + its Opacity (user 2026-11-08): shapes keep the notes they make in that space,
        drawn see-through, kept only in the project (App.offrange_rows). The tick greyed while both Space settings
        are Off (user), the Opacity box while the tick is off or greyed."""
        from files.settings import OFFRANGE_OPACITY
        from window.widgets import Scrub, bad, good
        app = self.app
        lo, hi, _ = OFFRANGE_OPACITY

        def ticked():
            app.offrange_changed()
            app.schedule_autosave()
            self.sync_keep()
        self.keep_tick = f.row(None, self.tick(f.page, "settings.keep_offrange", app.keep_offrange, ticked))
        self.keep_tip = Tooltip(self.keep_tick, tr("settings.keep_offrange_tip"))
        row = ttk.Frame(f.page)
        self.opacity_var = tk.StringVar(value=str(app.offrange_opacity.get()))
        e = self.opacity_box = ttk.Entry(row, textvariable=self.opacity_var, width=6)
        e.pack(side="left")
        ttk.Label(row, text="%").pack(side="left", padx=(3, 0))
        good(e)

        def took(left=False):
            try:
                v = int(round(float(self.opacity_var.get())))
                if not lo <= v <= hi:
                    raise ValueError
            except ValueError:
                bad(e)
                return
            self.opacity_var.set(str(v))
            good(e)
            if v != app.offrange_opacity.get():
                app.offrange_opacity.set(v)
                app.offrange_changed()
                app.schedule_autosave()
        self.take_opacity = took  # (tests)
        e.bind("<Return>", lambda ev: (took(), "break")[1])
        e.bind("<FocusOut>", lambda ev: took(True))
        f.row(tr("settings.keep_opacity"), row, tip=tr("settings.keep_opacity_tip"))
        self.opacity_label = f.page.grid_slaves(row=f.r - 1, column=0)[0]  # (Form.row's name label)
        Scrub(app, [(e, self.opacity_var, took)], (1, 10, 1), lo, hi, label=self.opacity_label, no_undo=True)

        def wrapped():  # ("Wrap them around", user 2026-11-08: exports, playback and Domino copies; App.outgoing)
            app.offrange_changed()
            app.schedule_autosave()
            self.sync_keep()
        self.wrap_tick = f.row(None, self.tick(f.page, "settings.wrap_offrange", app.wrap_offrange, wrapped))
        Tooltip(self.wrap_tick, tr("settings.wrap_offrange_tip"))
        # "Show wrapped notes" (user 2026-11-08): where they land, for the selected shapes / always / never. Read by place
        from files.settings import WRAP_SHOWN
        names = [tr("settings.show_wrapped_" + k) for k in WRAP_SHOWN]
        box = self.wrapped_box = f.row(tr("settings.show_wrapped"),
                                       ttk.Combobox(f.page, values=names, state="readonly", width=16),
                                       tip=tr("settings.show_wrapped_tip"))
        box.current(WRAP_SHOWN.index(app.show_wrapped.get()) if app.show_wrapped.get() in WRAP_SHOWN else 0)
        self.wrapped_label = f.page.grid_slaves(row=f.r - 1, column=0)[0]  # (Form.row's name label)

        def shown(e=None):
            box.selection_clear()
            app.show_wrapped.set(WRAP_SHOWN[box.current()])
            app.roll.request_redraw()
            app.schedule_autosave()
        box.bind("<<ComboboxSelected>>", shown)
        self.sync_keep()

    def sync_keep(self):
        """The Keep notes tick greyed (tip says why) while there's no space for them; Opacity greyed with it off."""
        if getattr(self, "keep_tick", None) is None or not self.keep_tick.winfo_exists():
            return
        app = self.app
        room = app.space_keys.get() > 0 or app.space_bars.get() > 0
        self.keep_tick.state(["!disabled"] if room else ["disabled"])
        self.keep_tip.set_text(tr("settings.keep_offrange_tip") if room else tr("settings.keep_offrange_off"))
        on = room and app.keep_offrange.get()
        self.opacity_box.state(["!disabled"] if on else ["disabled"])
        self.opacity_label.state(["!disabled"] if on else ["disabled"])
        self.wrap_tick.state(["!disabled"] if on else ["disabled"])
        wraps = on and app.wrap_offrange.get()  # ("Show wrapped notes": greyed while nothing wraps)
        self.wrapped_box.state(["!disabled"] if wraps else ["disabled"])  # (readonly stays)
        self.wrapped_label.state(["!disabled"] if wraps else ["disabled"])

    def build_file_link(self, f):
        """"Open .spwb files with Spiderweb" (14, user 2026-10-29): Windows told that double-clicking a .spwb opens
        this Spiderweb. Pressed again after the folder moved = linked to the new place. Windows only. A short
        status under it, the whole story in its tip."""
        from files.about import HERE
        from files.project import PROJECT_EXT
        from files.system import file_link, link_files
        f.head(tr("settings.head_files"))
        b = f.row(None, ttk.Button(f.page, text=tr("settings.link_button"), takefocus=False))
        note = self.link_note = f.note()
        tip = Tooltip(note, "")

        def show():
            now = file_link(PROJECT_EXT, HERE) or "windows_only"
            note.config(text=tr(f"settings.link_{now}_short"),
                        foreground=look.GOOD if now == "here" else look.HINT)
            tip.set_text(tr(f"settings.link_{now}"))

        def link():
            if link_files(PROJECT_EXT, HERE, tr("project.spiderweb_project")):
                show()
            else:
                note.config(text=tr("settings.link_failed"), foreground=look.ERROR)
                tip.set_text("")
        b.config(command=link)
        if file_link(PROJECT_EXT, HERE) is None:
            b.state(["disabled"])
        show()

    def build_keep_colours(self, f):
        """Multi channel: a shape overlapping an earlier one moves whole to the next row of channels (render keep)."""
        f.head(tr("settings.head_channels"))
        f.row(None, self.tick(f.page, "settings.keep_colours", self.app.keep_colours, self.app.shapes_changed),
              tip=tr("settings.keep_colours_hint"))

    def build_stacked(self, f):
        """"Stacked notes" (user 2026-10-31: ONE setting for every note in the piano roll): which of the notes on top
        of each other shows (roll_draw.stack_rule; a loaded MIDI's painter: roll_loaded.loud_on). Read by place."""
        from roll.roll_draw import STACK_MODES
        f.head(tr("settings.head_stacked"))
        names = [tr("settings.stack_" + k) for k in STACK_MODES]
        var = self.app.stack_order
        box = self.stack_box = f.row(tr("settings.row_on_top"), ttk.Combobox(
            f.page, values=names, state="readonly", width=24), tip=tr("settings.stack_tip"))
        box.current(STACK_MODES.index(var.get()) if var.get() in STACK_MODES else 0)

        def picked(e):
            var.set(STACK_MODES[box.current()])
            box.selection_clear()
            self.app.roll.request_redraw()
            self.app.schedule_autosave()
        box.bind("<<ComboboxSelected>>", picked)
        # (user 2026-10-31: "I don't like it but if that floats their boat": off by default; loaded MIDI always has it)
        f.row(None, self.tick(f.page, "settings.hidden_shade", self.app.hidden_shade,
                              lambda: (self.app.roll.request_redraw(), self.app.schedule_autosave())),
              tip=tr("settings.hidden_shade_hint"))

    def build_indexes(self, f):
        """Load MIDI's saved indexes (files/midi_saved.py): each MIDI's name, the index's size, a Delete button."""
        from files import midi_saved
        from window.load_midi import size_text
        Tooltip(f.head(tr("settings.head_indexes")), tr("settings.indexes_hint"))
        f.note(tr("settings.indexes_short"), tip=tr("settings.indexes_hint"))
        box = ttk.Frame(f.page)
        box.grid(row=f.r, column=0, columnspan=2, sticky="ew", pady=3)
        f.r += 1
        width = round(NOTE_WRAP * self.app.scale)

        def fill():
            for w in box.winfo_children():
                w.destroy()
            got = midi_saved.saved_list()
            if not got:
                ttk.Label(box, text=tr("settings.indexes_none"), foreground=look.HINT).pack(anchor="w")
            for path, f, size in got:
                row = ttk.Frame(box)
                row.pack(anchor="w", fill="x", pady=(2, 0))
                ttk.Button(row, text=tr("settings.index_delete"), takefocus=False,
                           command=lambda f=f: (midi_saved.delete_index(f), fill())).pack(side="right")
                ttk.Label(row, text=size_text(size), foreground=look.HINT).pack(side="right", padx=(6, 6))
                ShortLabel(row, text=os.path.basename(path)).pack(side="left", fill="x", expand=True)
            ttk.Frame(box, width=width, height=0).pack(anchor="w")
        fill()
        # (an index saved while Settings is open: listed when the page is shown again, hunt 2026-11-07)
        self.tabs.on_show[str(self.pages["loaded"])] = fill

    def build_copying(self, f):
        """Copying: what Ctrl+C puts on the clipboard besides Spiderweb's own copy (App.put_clipboard)."""
        f.head(tr("settings.head_ctrl_c"))
        f.row(None, self.tick(f.page, "settings.copy_domino", self.app.copy_domino, self.app.schedule_autosave),
              tip=tr("settings.copy_domino_hint"))
        from files.clipboard import RAW  # (Linux: no such clipboard yet, the tick greyed, user 2026-10-20)
        f.row(None, self.tick(f.page, "settings.copy_notes", self.app.copy_notes, self.app.schedule_autosave,
                              state="normal" if RAW else "disabled"),
              tip=tr("settings.copy_notes_hint") + ("" if RAW else tr("settings.copy_notes_windows")))

    def build_domino(self, f):
        """(Experimental) Domino 256 keys: Copy / Paste / Export to Domino keep keys above 127 (off: left out)."""
        f.head(tr("settings.head_domino"))
        f.row(None, self.tick(f.page, "settings.domino_256_short", self.app.domino256, self.app.schedule_autosave),
              tip=tr("settings.domino_256_hint"))

    # ------------------------------------------------------------ language (lang.languages; next start, like the look)

    def build_language(self, f):
        page = f.page
        f.head(tr("settings.language_tab"))
        found = lang.languages()
        saved = lang.read_language()
        if saved not in dict(found):  # (its file was removed: shown as its code, still the saved pick)
            found.append((saved, saved))
        self.language_codes = codes = [c for c, _ in found]  # (picked by place in the list: names can be alike)
        names = [n for _, n in found]
        var = self.language_var = tk.StringVar(value=names[codes.index(saved)])
        box = self.language_box = f.row(tr("language.pick"), ttk.Combobox(
            page, textvariable=var, values=names, state="readonly", width=max(18, max(len(n) for n in names) + 2)),
            tip=tr("language.tip"))
        letter_jump(box)
        note = self.language_note = f.note(colour=look.WARN, under=True)
        if saved != lang.LANGUAGE:  # (picked before, not put on yet: said for as long as it's true)
            note.config(text=tr("language.restart"))
        else:
            note.grid_remove()
        for file, (why, line) in sorted(lang.problems.items()):  # (warnings stay whole: they say what to fix)
            f.note(tr("language.broken", file=file, line=line) if why == "line" else
                   tr("language.bad_name", file=file) if why == "name" else tr("language.unreadable", file=file),
                   colour=look.WARN)
        if len(found) < 2:
            f.note(tr("language.only_english_short"), tip=tr("language.only_english"))
        f.row(None, ttk.Button(page, text=tr("language.template"), takefocus=False, command=self.make_template),
              tip=tr("language.template_tip"))

        def picked(e):
            code = codes[box.current()]
            try:
                lang.save_language(code)
            except OSError:
                messagebox.showerror(tr("settings.title"), tr("language.not_saved"), parent=self)
                back = lang.read_language()
                box.current(codes.index(back) if back in codes else codes.index(saved))
                return
            note.config(text="" if code == lang.LANGUAGE else tr("language.restart"))
            note.grid_remove() if code == lang.LANGUAGE else note.grid()
        box.bind("<<ComboboxSelected>>", picked)

    def make_template(self):
        """Start a translation: a copy of the English texts beside Spiderweb, and its folder opened. One made before
        is only replaced when the user says so (No = its folder opened as it is)."""
        if os.path.isdir(lang.template_path()) and not messagebox.askyesno(
                tr("settings.title"), tr("language.template_replace"), parent=self):
            open_path(lang.OWN)
            return
        try:
            path = lang.make_template()
        except OSError:
            blocked = os.path.isfile(lang.OWN)  # (a file named lang where the folder goes)
            messagebox.showerror(tr("settings.title"), tr("language.template_blocked" if blocked else
                                                          "language.template_failed"), parent=self)
            return
        open_path(os.path.dirname(path))

    # ------------------------------------------------------------ sound (MIDI out + Built-in BASSMIDI)

    def build_sound(self, f):
        """MIDI out (the same choice as the side panel's) and, while Built-in BASSMIDI is picked, its settings."""
        app, page = self.app, f.page
        f.head(tr("settings.head_midi_out"))
        dev = self.midi_box = f.row(tr("settings.row_midi_out"), ttk.Combobox(page, textvariable=app.midi_device,
                                                                     state="readonly", values=devices(), width=30))
        keep_width(dev)
        letter_jump(dev)
        dev.configure(postcommand=lambda: dev.configure(values=devices()))  # (re-listed when it opens)
        self.builtin_off = f.note(tr("settings.builtin_off"), under=True)
        self.builtin_title = f.head(tr("bs.title"))
        self.sound_sep = page.grid_slaves(row=f.r - 1, column=0)[0]  # (the heading's line: hidden with it)
        panel = app.builtin_window = f.span(BuiltinPanel(page, app))
        panel.bind("<Destroy>", lambda e: e.widget is panel and setattr(app, "builtin_window", None), add="+")
        # (Maestro: its sound settings are in its own window, a profile for Spiderweb; user 2026-11-01)
        self.maestro_title = f.head(tr("maestro.title"))
        self.maestro_sep = page.grid_slaves(row=f.r - 1, column=0)[0]
        self.maestro_btn = f.row(None, ttk.Button(page, text=tr("maestro.open"), command=self.maestro_settings),
                                 tip=tr("maestro.open_tip"))
        self.maestro_note = f.note()
        self.show_builtin()

    def maestro_settings(self):
        """Maestro's window for Spiderweb's profile (it needs an open engine: opened as Play would)."""
        out = self.app.out
        err = out.open(MAESTRO)
        if err is None and not out.handle.show_settings():
            from files import maestro
            err = tr("playback.maestro_failed_why", why=maestro.load()[1].last_error().decode("utf-8", "replace"))
        if err:
            messagebox.showerror(tr("settings.title"), err, parent=self)

    def show_builtin(self):
        """Built-in BASSMIDI's settings while it's the MIDI out, Maestro's button while that is, else a line saying
        they're for them."""
        dev = self.app.midi_device.get()
        on = dev in (BUILTIN, KESTREL, AHEAD)
        for w in (self.sound_sep, self.builtin_title, self.app.builtin_window):
            w.grid() if on else w.grid_remove()
        mo = dev == MAESTRO
        for w in (self.maestro_title, self.maestro_sep, self.maestro_btn, self.maestro_note):
            w.grid() if mo else w.grid_remove()
        if mo:
            from files import maestro
            v = maestro.version()
            self.maestro_note.config(text=tr("maestro.version", v=v, tested=maestro.TESTED) if v
                                     else tr("playback.maestro_failed"))
        self.builtin_off.grid_remove() if on or mo else self.builtin_off.grid()
        if on:  # (Kestrel (GPU), Rendered ahead: their own rows instead, the soundfont list shared)
            which = {KESTREL: "kestrel", AHEAD: "ahead"}.get(dev, "builtin")
            self.builtin_title.config(text=tr({"kestrel": "ks.title", "ahead": "ra.title"}.get(which, "bs.title")))
            self.app.builtin_window.show_for(which)

    # ------------------------------------------------------------ size, font, text size (look.apply_size)

    def build_size(self, f):
        """Size / Font / Text size: saved at once, put on at the next start (an orange line says so while what's
        saved isn't what's showing)."""
        page = f.page
        f.head(tr("settings.head_size"))
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
        self.look_boxes = {}
        for label, var, values, width, tip in rows:
            row = f.row(tr(label), ttk.Frame(page), tip=tr(tip), widget_tip=False)
            box = self.look_boxes[label] = ttk.Combobox(row, textvariable=var, values=values, state="readonly",
                                                        width=width)
            if label == "look.font":
                keep_width(box)  # (font names)
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", lambda e: self.save_size())
            Tooltip(box, tr(tip))
            setattr(self, label.replace(".", "_") + "_box", box)
        self.font_values = list(self.look_font_box.cget("values"))
        self.font_search()
        self.size_reset = ttk.Button(row, text=tr("look.reset"), takefocus=False, command=self.reset_size)
        self.size_reset.pack(side="left", padx=(8, 0))
        Tooltip(self.size_reset, tr("look.reset_tip"))
        self.size_note = f.note(tr("look.restart_size"), colour=look.WARN)
        self.show_size_note()
        self.build_preview(f)

    def build_preview(self, f):
        """Pieces that work but change nothing else (user: tick, open the dropdown, step / type the number), drawn
        with the picked Size / Font / Text size at once, while the rest of the app waits for the restart. Own styles ("Preview.T..."), so the real widgets never change."""
        box = self.preview = f.span(ttk.LabelFrame(f.page, text=tr("look.preview"), padding=(10, 6, 10, 8)),
                                    pady=(8, 0))
        Tooltip(box, tr("look.preview_tip"))
        self.pv_fonts = {k: tkfont.Font(self, family=look.USUAL_FAMILY, size=9) for k in ("text", "heading", "hint")}
        self.pv_fonts["heading"].configure(weight="bold")
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
        # each style's own paddings + arrow sizes, to grow / shrink with the picked Size (the tick box's square has
        # no size setting: it stays)
        self.pv_sizes = {}
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
        kinds = ("TButton", "TCheckbutton", "TCombobox", "TSpinbox")
        if target not in self.pv_sizes:  # (made the first time this style is picked; its own paddings kept)
            if style != "system":
                button_look.dress(st, target, style, look.parts())
            else:
                button_look.system_colours(st, target)
            button_look.put_on(self, st, target, style, look.parts(), kinds, edge)

            def usual(name, option):  # (Tk's own clam values for its styles: the system's theme may be running)
                if style == "system":
                    return st.lookup(target + name, option)
                own = st.configure(target + name, option)
                return own if own not in ("", None) else st.tk.call(
                    "ttk::style", "theme", "settings", "clam", f"ttk::style lookup {name} -{option}")
            self.pv_sizes[target] = {name: (usual(name, "padding"), usual(name, "arrowsize")) for name in kinds}
        for w, name in zip(self.pv_widgets, kinds):
            st.configure(target + name, font=self.pv_fonts["text"])
            w.configure(style=target + name)
        for name, (pad, arrow) in self.pv_sizes[target].items():
            if pad not in ("", None):
                parts = pad if isinstance(pad, (tuple, list)) else self.tk.splitlist(str(pad))  # (Tk 9: a list)
                st.configure(target + name, padding=[round(self.winfo_fpixels(str(p)) * ratio) for p in parts])
            if arrow not in ("", None) and name in ("TCombobox", "TSpinbox"):
                st.configure(target + name, arrowsize=round(self.winfo_fpixels(str(arrow)) * ratio))

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
                if not str(focus).startswith(str(box)):
                    if self.font_var.get() not in self.font_values:
                        back()
                    else:  # (a whole name typed: the whole list back too)
                        box.config(values=self.font_values)
            self.after_idle(check)

        self.font_jump = letter_jump(box)  # (typing while its list is open jumps to the first font with those letters)
        box.bind("<KeyRelease>", typed)
        box.bind("<Return>", enter)
        box.bind("<KP_Enter>", enter)
        box.bind("<Escape>", back)
        box.bind("<FocusOut>", left)
        box.bind("<<ComboboxSelected>>", lambda e: (box.config(values=self.font_values), box.selection_clear(),
                                                     self.save_size()))

    def picked_size(self):
        """(size, font, text) as picked in the three lists (a font half typed: the saved one)."""
        size = self.picked("look.size", self.size_names, self.size_var)
        family = self.font_var.get()
        if family not in self.font_values:
            family = look.read_font() or self.font_default
        family = "" if family == self.font_default else family
        text = self.picked("look.text", self.text_names, self.text_var)
        return size, family, text

    def picked(self, label, names, var):
        """The key of the choice picked in a Look row: by its place in the list (two translated alike stay apart),
        else by its word."""
        i = self.look_boxes[label].current() if label in getattr(self, "look_boxes", {}) else -1
        return list(names)[i] if 0 <= i < len(names) else next(k for k, n in names.items() if n == var.get())

    def picked_button(self):
        return self.picked("look.button", self.button_names, self.button_var)

    def picked_edge(self):
        return next(k for k, n in self.edge_names.items() if n == self.edge_var.get())

    def picked_lines(self):
        return next(k for k, n in self.lines_names.items() if n == self.lines_var.get())

    def save_size(self):
        size, family, text = self.picked_size()
        if self.font_var.get() in self.font_values:  # (not half typed, e.g. after Reset: the whole font list back)
            self.look_font_box.config(values=self.font_values)
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

    def build_palettes(self, f):
        page = f.page
        f.head(tr("settings.cat_colours"))
        self.pal_var = tk.StringVar()
        self.pal_box = f.row(tr("settings.row_palette"), name_list(ttk.Combobox(page, textvariable=self.pal_var,
                                                                         state="readonly", width=24)),
                             tip=tr("palette.tip"))
        self.pal_box.bind("<<ComboboxSelected>>", lambda e: self.pick_palette())
        # the colours, each over its MIDI channel's number (click one to change it)
        s = self.swatch_size = round(22 * self.app.scale)
        self.number_h = round(16 * self.app.scale)
        r = f.r
        ttk.Label(page, text=tr("palette.channel")).grid(row=r, column=0, sticky="se", padx=(0, 8),
                                                         pady=(4, 4))
        self.swatches = tk.Canvas(page, width=palettes.SLOTS * (s + 3), height=s + 2 + self.number_h,
                                  highlightthickness=0, cursor="hand2",
                                  background=look.WINDOW_BG if look.DARK else None)
        self.swatches.grid(row=r, column=1, sticky="w", pady=(4, 4))
        self.swatches.bind("<Button-1>", self.click_swatch)
        Tooltip(self.swatches, tr("palette.swatch_tip"))
        f.r += 1
        for k, (text, command, tip) in enumerate((
                ("palette.new", self.new_palette, "palette.new_tip"),
                ("palette.rename", self.rename_palette, "palette.rename_tip"),
                ("palette.delete", self.delete_palette, "palette.delete_tip"),
                ("palette.random", self.random_palette, "palette.random_tip"),
                ("palette.gradient", self.gradient_palette, "palette.gradient_tip"))):
            if k % 3 == 0:
                row = f.row(None, ttk.Frame(page), pady=(4, 0))
            b = ttk.Button(row, text=tr(text), command=command, takefocus=False)
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, tr(tip))
            setattr(self, text.replace(".", "_"), b)
        self.show_palettes()

    def shown_name(self, name):
        return tr("palette.default") if name == palettes.DEFAULT else name

    def show_palettes(self):
        """The list, the one in use, its colours; Rename / Delete only for the user's own."""
        names = palettes.names_newest()
        self.pal_names = {self.shown_name(n): n for n in names}
        set_names(self.pal_box, list(self.pal_names)[:1], list(self.pal_names)[1:])
        now = palettes.picked()
        self.pal_var.set(self.shown_name(now))
        own = ["!disabled"] if now != palettes.DEFAULT else ["disabled"]
        self.palette_rename.state(own)
        self.palette_delete.state(own)
        c, s = self.swatches, self.swatch_size
        c.delete("all")
        colours = palettes.colours_of(now)
        for ch, k in enumerate(palettes.ORDER):  # (by channel: channel 10's colour is the palette's 16th)
            fill, edge = colours[k]
            x = ch * (s + 3)
            c.create_rectangle(x + 1, 1, x + s, s, fill=fill, outline=edge, width=2)
            c.create_text(x + s / 2, s + 2 + self.number_h / 2, text=str(ch + 1),
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
        ch = int(e.x // (self.swatch_size + 3))
        if not 0 <= ch < palettes.SLOTS:
            return
        k = palettes.ORDER[ch]

        def make(colours):
            got = colorchooser.askcolor(colours[k][0], parent=self,
                                        title=tr("palette.pick_title", channel=ch + 1))
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
            last = colorchooser.askcolor(colours[palettes.ORDER[-1]][0], parent=self,
                                         title=tr("palette.gradient_last"))
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
