"""The soundfonts list (user 2026-10-25, like a MIDI driver's): Settings → Sound (Built-in BASSMIDI) and the Hz bass
settings show the same list, shared by both. Order matters: each soundfont overrides the ones above it (a sound
it doesn't have comes from them), none has it = silent. Add… (several at once, at the bottom), Remove (Delete key too), Up, Down; every change
goes to app.set_soundfonts."""

import os
import tkinter as tk
from tkinter import filedialog, ttk

from files.lang import tr
from files.synth import FONT_TYPES
from window import look
from window.hz_preview import font_list
from window.widgets import ListTip, Tooltip

ROWS = (3, 6)  # the list's height in rows: at least, at most (more scroll)


class FontList(ttk.Frame):
    """Grid it in the row of its label ("Soundfonts"); refresh() shows the list as app.hz_preview has it."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.columnconfigure(0, weight=1)
        self.list = tk.Listbox(self, height=ROWS[0], width=30, activestyle="none", exportselection=False,
                               font=look.font(9))
        self.list.grid(row=0, column=0, sticky="nsew")
        self.list.bind("<<ListboxSelect>>", lambda e: self.sync_buttons())
        self.list.bind("<Delete>", lambda e: self.remove())
        ListTip(self.list)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=1, sticky="n", padx=(6, 0))
        self.buttons = {}
        for key, command in (("add", self.add), ("remove", self.remove), ("up", lambda: self.move(-1)),
                             ("down", lambda: self.move(1))):
            b = self.buttons[key] = ttk.Button(bar, text=tr("ps.font_" + key), command=command, takefocus=False)
            b.pack(fill="x", pady=(0, 2))
            Tooltip(b, tr("ps.font_" + key + "_tip"))
        hint = ttk.Label(self, text=tr("ps.fonts_hint"), foreground=look.HINT, font=look.font(8),
                         wraplength=round(300 * app.scale), justify="left")
        hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.shown = None
        self.refresh()

    def paths(self):
        return font_list(self.app.hz_preview)

    def refresh(self):
        """The list as it's set now (picked elsewhere too: a pick when there was none, the Hz window)."""
        if not self.winfo_exists():
            return
        paths = self.paths()
        if paths == self.shown:
            return
        picked = self.picked()
        name = self.shown[picked] if picked is not None and self.shown and picked < len(self.shown) else None
        self.shown = paths
        self.list.delete(0, "end")
        for p in paths:
            self.list.insert("end", os.path.basename(p))
        if not paths:
            self.list.insert("end", tr("ps.no_font"))
            self.list.itemconfig(0, foreground=look.HINT)
        self.list.config(height=max(ROWS[0], min(ROWS[1], len(paths))))
        if name in paths:
            self.pick(paths.index(name))
        self.sync_buttons()

    def picked(self):
        sel = self.list.curselection()
        return sel[0] if sel and self.shown else None

    def pick(self, i):
        self.list.selection_clear(0, "end")
        self.list.selection_set(i)
        self.list.see(i)

    def sync_buttons(self):
        i, n = self.picked(), len(self.shown or [])
        for key, on in (("remove", i is not None), ("up", i is not None and i > 0),
                        ("down", i is not None and i < n - 1)):
            self.buttons[key].state(["!disabled"] if on else ["disabled"])

    def put(self, paths, picked=None, again=False, added=()):
        self.app.set_soundfonts(paths, again=again, added=added)
        self.refresh()
        if picked is not None and self.shown:
            self.pick(picked)
        self.sync_buttons()

    def add(self):
        paths = self.paths()
        got = filedialog.askopenfilenames(
            parent=self, title=tr("ps.fonts_title"),
            initialdir=os.path.dirname(paths[-1]) if paths else None,
            filetypes=[(tr("hz.preview_fonts"), FONT_TYPES), (tr("hz.preview_all"), "*.*")])
        got = [os.path.normpath(p) for p in (self.tk.splitlist(got) if isinstance(got, str) else got or ())]
        if not got:
            return
        again = any(p in paths for p in got)  # (one already listed = opened again: samples put back are found)
        new = paths + [p for p in dict.fromkeys(got) if p not in paths]
        self.put(new, len(new) - 1, again, [p for p in got if p not in paths])

    def remove(self):
        i = self.picked()
        if i is None:
            return
        paths = self.paths()
        del paths[i]
        self.put(paths, min(i, len(paths) - 1) if paths else None)

    def move(self, way):
        i, paths = self.picked(), self.paths()
        if i is None or not 0 <= i + way < len(paths):
            return
        paths[i], paths[i + way] = paths[i + way], paths[i]
        self.put(paths, i + way)
