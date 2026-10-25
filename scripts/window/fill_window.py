"""Fill gaps window (user 2026-10-23: own right-click item + small window): the way (Stretch left / Meet in the middle
/ Stretch right) + which note stays when notes start on the same tick, then Fill (JoinSplit.fill_gaps). Stays open
and follows the selection like Gate sensitive merge's; the picks are kept until Spiderweb closes."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from window import look
from window.widgets import remember_place


class FillWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.seen = None  # what sync worked out the hint for
        app.fill_window = self
        self.title(tr("fill_gaps.title"))
        self.transient(app)
        self.resizable(False, False)
        self.way = tk.StringVar(value=getattr(app, "fill_way", "right"))
        self.same = tk.StringVar(value=getattr(app, "fill_same", "early"))
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        ways = ttk.Frame(box)
        ways.pack(anchor="w")
        for col, way in enumerate(("left", "middle", "right")):
            ttk.Radiobutton(ways, text=tr("fill_gaps." + way), value=way, variable=self.way, style="Toolbutton",
                            command=self.sync).grid(row=0, column=col, padx=2, pady=2, sticky="ew")
        ttk.Label(box, text=tr("fill_gaps.same_start")).pack(anchor="w", pady=(10, 2))
        for same in ("early", "late", "both"):
            ttk.Radiobutton(box, text=tr("fill_gaps.same_" + same), value=same, variable=self.same,
                            command=self.sync).pack(anchor="w", padx=(10, 0))
        self.hint = ttk.Label(box, text="", foreground=look.HINT, wraplength=int(320 * app.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(8, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text=tr("merge_window.close"), command=self.close).pack(side="right")
        self.go = ttk.Button(row, text=tr("fill_gaps.fill"), command=self.fill)
        self.go.pack(side="right", padx=(0, 6))
        self.bind("<Return>", self.enter)
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.sync()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "fill_gaps")
        self.deiconify()
        self.go.focus_set()

    def sync(self):
        """The hint for the picked way, + why it can't fill (Fill greyed) / what's left out."""
        app = self.app
        way, same = self.way.get(), self.same.get()
        app.fill_way, app.fill_same = way, same
        seen = (way, same, tuple(sorted(app.sels)), app.shapes_gen, len(app.shapes))
        if seen == self.seen:  # (worked out already: big shapes, every panel sync)
            return
        self.seen = seen
        hint = tr("fill_gaps.hint_" + way)
        got = app.fill_targets()
        sels = [i for i in app.sels if i < len(app.shapes)]
        problem = app.fill_problem(way, same)
        if got and len(got) < len(sels):
            hint += "\n\n" + tr("fill_gaps.left_out")
        if problem:
            hint += "\n\n" + problem
        self.hint.config(text=hint)
        self.go.state(["disabled"] if problem else ["!disabled"])

    def enter(self, e):
        """Enter: the button that has the keyboard (Tab to Close + Enter = close), else Fill."""
        if isinstance(e.widget, ttk.Button):
            e.widget.invoke()
        else:
            self.fill()

    def ppq_changed(self):
        pass

    def fill(self):
        if self.go.instate(["disabled"]):
            return
        self.app.fill_gaps(self.way.get(), self.same.get())
        self.sync()

    def close(self):
        self.app.fill_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_fill(app):
    w = app.fill_window
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        FillWindow(app)
