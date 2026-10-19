"""Gate sensitive merge window (user 2026-10-19: picked over an 8-item submenu): the way (Right / Left / Up / Down,
laid out like arrow keys) + "Keep two shapes", then Merge (JoinSplit.gate_merge). Stays open and follows the
selection (says which shape moves, or why it can't: JoinSplit.merge_problem); the picks are kept until Spiderweb
closes. Without the tick (one shape) only two shapes."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from window import look
from window.widgets import remember_place


class MergeWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.merge_window = self
        self.title(tr("join_split.merge"))
        self.transient(app)
        self.resizable(False, False)
        self.way = tk.StringVar(value=getattr(app, "merge_way", "right"))
        self.keep = tk.BooleanVar(value=getattr(app, "merge_keep", False))
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        cross = ttk.Frame(box)
        cross.pack()
        ways = (("up", 0, 1), ("left", 1, 0), ("right", 1, 2), ("down", 2, 1))
        wide = max(len(tr("merge_window." + w)) for w, _, _ in ways) + 2
        for way, row, col in ways:  # (negative width: at least that wide, grows with a wide font, no cut text)
            ttk.Radiobutton(cross, text=tr("merge_window." + way), value=way, variable=self.way, style="Toolbutton",
                            width=-wide, command=self.sync).grid(row=row, column=col, padx=2, pady=2, sticky="ew")
        ttk.Checkbutton(box, text=tr("merge_window.keep"), variable=self.keep,
                        command=self.sync).pack(anchor="w", pady=(10, 0))
        self.hint = ttk.Label(box, text="", foreground=look.HINT, wraplength=int(300 * app.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text=tr("merge_window.close"), command=self.close).pack(side="right")
        self.go = ttk.Button(row, text=tr("merge_window.merge"), command=self.merge)
        self.go.pack(side="right", padx=(0, 6))
        self.bind("<Return>", lambda e: self.merge())
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.sync()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "merge")
        self.deiconify()
        self.go.focus_set()

    def sync(self):
        """The hint for the picked way + which shape moves, or why it can't merge (Merge greyed, hunt)."""
        app = self.app
        way, keep = self.way.get(), self.keep.get()
        app.merge_way, app.merge_keep = way, keep
        problem = app.merge_problem(way, keep)
        hint = tr("merge_window.hint_" + way)
        if problem:
            hint += "\n\n" + problem
        else:  # (both kinds, hunt)
            sels = app.merge_sels() if keep else app.merge_pair()
            k, alt, _ = app.merge_mover(sels, way)
            if not keep:
                alt = None
            name = app.shape_label(app.shapes[sels[k]])
            hint += "\n\n" + (tr("merge_window.moves", name=name) if alt is None else
                              tr("merge_window.moves_or", name=name, other=app.shape_label(app.shapes[sels[alt]])))
        self.hint.config(text=hint)
        self.go.state(["disabled"] if problem else ["!disabled"])

    def ppq_changed(self):
        pass

    def merge(self):
        if self.go.instate(["disabled"]):
            return
        way = self.way.get()
        self.app.gate_merge(way if way in ("up", "down") else way == "right", self.keep.get())
        self.sync()

    def close(self):
        self.app.merge_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_merge(app):
    w = app.merge_window
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        MergeWindow(app)
