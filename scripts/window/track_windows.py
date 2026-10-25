"""The track list's small windows for the tracks added by hand (files/own_tracks.py; user 2026-10-24..26, like
Domino's): Track Properties (name + channel) and Add Tracks (one track per ticked channel; dragging over the ticks
ticks / unticks every one crossed). Both modal with OK / Cancel; their answer is None when cancelled."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.own_tracks import NAME_MOST, clean_name
from window import look


class _Box(tk.Toplevel):
    """A small modal window: body() fills self.body and returns a function giving the answer (None = not good
    yet); OK / Cancel under it. run() shows it and waits: the answer, or None when cancelled."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.parent = parent
        self.answer = None
        self.body = ttk.Frame(self, padding=(14, 12, 14, 6))
        self.body.pack(fill="both", expand=True)
        self.get = self.fill(self.body)
        btns = ttk.Frame(self, padding=(14, 4, 14, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text=tr("tracks.cancel"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=tr("tracks.ok"), command=self.ok).pack(side="right", padx=(0, 6))
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())

    def fill(self, body):
        raise NotImplementedError

    def ok(self):
        got = self.get()
        if got is not None:
            self.answer = got
            self.destroy()

    def run(self):
        self.update_idletasks()
        top = self.parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_reqwidth()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        self.grab_set()
        self.wait_window()
        return self.answer


class PropertiesBox(_Box):
    """Track Properties: (name, channel 0-15), or None."""

    def __init__(self, parent, name, ch):
        self.start = (name, ch)
        super().__init__(parent, tr("tracks.properties_title"))

    def fill(self, f):
        name, ch = self.start
        self.name = tk.StringVar(value=name)
        self.ch = tk.StringVar(value=str(ch + 1))
        ttk.Label(f, text=tr("tracks.properties_name")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        e = self.entry = ttk.Entry(f, textvariable=self.name, width=30)
        e.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 6))
        ttk.Label(f, text=tr("tracks.properties_channel")).grid(row=1, column=0, sticky="w")
        self.box = ttk.Combobox(f, values=[str(c) for c in range(1, 17)], textvariable=self.ch, state="readonly",
                                width=5)
        self.box.grid(row=1, column=1, sticky="w", padx=(8, 0))
        e.focus_set()
        e.selection_range(0, "end")
        return lambda: (clean_name(self.name.get())[:NAME_MOST], int(self.ch.get()) - 1)


class AddTracksBox(_Box):
    """Add Tracks: the channels (0-15, in order) ticked, or None. Every channel ticked at first."""

    def __init__(self, parent):
        super().__init__(parent, tr("tracks.add_many_title"))

    def fill(self, f):
        ttk.Label(f, text=tr("tracks.add_many_hint"), foreground=look.HINT, justify="left",
                  wraplength=round(260 * getattr(self.parent, "scale", 1))).grid(row=0, column=0, columnspan=2,
                                                                                 sticky="w", pady=(0, 6))
        self.vars = [tk.BooleanVar(value=True) for _ in range(16)]
        self.ticks = []
        for c in range(16):
            b = ttk.Checkbutton(f, text=tr("tracks.channel", ch=c + 1), variable=self.vars[c], takefocus=False)
            b.grid(row=1 + c % 8, column=c // 8, sticky="w", padx=(0, 18))
            b.bind("<ButtonPress-1>", lambda e, c=c: self.press(c))
            b.bind("<B1-Motion>", self.drag)
            b.bind("<ButtonRelease-1>", lambda e: (setattr(self, "_sweep", None), "break")[1])
            self.ticks.append(b)
        row = ttk.Frame(f)
        row.grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(row, text=tr("tracks.select_all"), command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(row, text=tr("tracks.deselect_all"), command=lambda: self.set_all(False)).pack(side="left",
                                                                                                padx=(4, 0))
        self.hint = ttk.Label(f, text="", foreground=look.WARN)
        self.hint.grid(row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._sweep = None

        def get():
            chs = [c for c in range(16) if self.vars[c].get()]
            if not chs:  # (says why OK did nothing)
                self.hint.config(text=tr("tracks.add_many_none"))
                return None
            return chs
        return get

    def press(self, c):
        """A tick pressed: it flips, and dragging on gives every tick crossed the same."""
        on = not self.vars[c].get()
        self.vars[c].set(on)
        self._sweep = on
        return "break"  # (the tick's own click would flip it back)

    def tick_at(self, x, y):
        """The tick under the screen point (x, y), or None (worked out from their places: no asking Windows)."""
        for c, b in enumerate(self.ticks):
            bx, by = b.winfo_rootx(), b.winfo_rooty()
            if bx <= x < bx + b.winfo_width() and by <= y < by + b.winfo_height():
                return c
        return None

    def drag(self, e):
        if self._sweep is None:
            return "break"
        c = self.tick_at(e.x_root, e.y_root)
        if c is not None:
            self.vars[c].set(self._sweep)
        return "break"

    def set_all(self, on):
        for v in self.vars:
            v.set(on)
