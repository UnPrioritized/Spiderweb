"""The font window: type a font's name (the list narrows down as you type) or pick one from the list, with a
preview of the text in it."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.fonts import font_families
from window import look
from window.widgets import remember_place

SAMPLE = "AaBbCc 0123"


class FontDialog(tk.Toplevel):
    def __init__(self, app, current, sample, on_pick):
        super().__init__(app)
        self.app, self.on_pick = app, on_pick
        self.title(tr("font_dialog.font"))
        self.transient(app)
        s = app.scale
        self.geometry(f"{int(420 * s)}x{int(460 * s)}")
        self.minsize(int(300 * s), int(300 * s))
        remember_place(self, "font")
        self.families = font_families(self)
        self.shown = []
        self.sample = " ".join(sample.split())[:40] or SAMPLE

        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("font_dialog.type_a_font_s_name_or")).pack(anchor="w")
        self.name = tk.StringVar(value=current)
        self.entry = ttk.Entry(box, textvariable=self.name)
        self.entry.pack(fill="x", pady=(2, 6))
        row = ttk.Frame(box)
        row.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(row, activestyle="none", exportselection=False, font=look.font(10))
        sb = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.preview = tk.Label(box, text=self.sample, anchor="w", height=2, bg=look.WHITE_BOX, relief="solid", bd=1,
                                padx=6)
        self.preview.pack(fill="x", pady=(6, 0))
        self.note = ttk.Label(box, text="", foreground=look.HINT, font=look.font(8))
        self.note.pack(anchor="w")
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text=tr("font_dialog.cancel"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=tr("font_dialog.ok"), command=self.pick).pack(side="right", padx=4)

        self.name.trace_add("write", lambda *_: self.filter())
        self.entry.bind("<Down>", lambda e: self.move(1))
        self.entry.bind("<Up>", lambda e: self.move(-1))
        self.entry.bind("<Next>", lambda e: self.move(10))
        self.entry.bind("<Prior>", lambda e: self.move(-10))
        self.bind("<Return>", lambda e: self.pick())
        self.bind("<Escape>", lambda e: self.destroy())
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.show_preview())
        self.listbox.bind("<Double-Button-1>", lambda e: self.pick())
        self.filter(select=current)
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def filter(self, select=None):
        """The list: fonts whose name has the typed text in it, the ones starting with it first."""
        typed = self.name.get().strip().lower()
        if select is not None or not typed:
            self.shown = list(self.families)
        else:
            self.shown = ([f for f in self.families if f.lower().startswith(typed)] +
                          [f for f in self.families if typed in f.lower() and not f.lower().startswith(typed)])
        self.listbox.delete(0, "end")
        for f in self.shown:
            self.listbox.insert("end", f)
        want = (select or "").lower()
        at = next((i for i, f in enumerate(self.shown) if f.lower() == want), 0 if self.shown else None)
        if at is not None:
            self.listbox.selection_set(at)
            self.listbox.see(at)
        self.show_preview()

    def move(self, step):
        if not self.shown:
            return "break"
        cur = self.listbox.curselection()
        i = min(len(self.shown) - 1, max(0, (cur[0] + step) if cur else 0))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.see(i)
        self.show_preview()
        return "break"

    def chosen(self):
        """The highlighted font (the typed name exactly, if that's a font)."""
        typed = self.name.get().strip().lower()
        exact = next((f for f in self.families if f.lower() == typed), None)
        if exact:
            return exact
        cur = self.listbox.curselection()
        return self.shown[cur[0]] if cur else None

    def show_preview(self):
        f = self.chosen()
        if f:
            self.preview.config(text=self.sample, font=(f, 22))
            self.note.config(text=f)
        else:
            self.preview.config(text=tr("font_dialog.no_font_with_that_name"), font=look.font(11))
            self.note.config(text="")

    def pick(self):
        f = self.chosen()
        if f:
            self.destroy()
            self.on_pick(f)
