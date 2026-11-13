"""The line tools' small window (right-click "Flip each line sideways…" / "Flip each line upside down…" / "Split into
pieces…"; notes/lines.py, JoinSplit.flip_lines / split_lines; user 2026-10-30): "Join notes with gaps up to [n] ticks" (0 = the pictures' rule; kept until
Spiderweb closes), a live "N lines found" under it, then the button doing it (one undo step) or Cancel. Drawn shapes
among the selection are asked about first (they turn into plain notes in the same step)."""

import math
import tkinter as tk
from tkinter import messagebox, ttk

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from window import look
from window.widgets import Scrub, Tooltip, centre_over, remember_place, unit_text

MOST_GAP = 1_000_000  # (ticks)
COUNT_WAIT_MS = 150  # (the count waits for the typing / stepping to rest)


class LinesWindow(tk.Toplevel):
    def __init__(self, app, kind):
        super().__init__(app)
        self.withdraw()
        self.app, self.kind = app, kind
        self.job = None
        self.asked = {id(app.shapes[i]) for i in drawn_of(app, app.line_targets())}  # (asked about on opening)
        app.lines_window = self
        self.title(tr("lines.title_" + kind))
        self.transient(app)
        self.resizable(False, False)
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        row = ttk.Frame(box)
        row.pack(anchor="w")
        label = ttk.Label(row, text=tr("lines.gap"))
        label.pack(side="left")
        self.gap_var = tk.StringVar(value=fmt(getattr(app, "line_gap", 0)))
        self.gap_box = ttk.Entry(row, textvariable=self.gap_var, width=7)
        self.gap_box.pack(side="left", padx=(4, 0))
        unit = ttk.Label(row)
        unit.pack(side="left", padx=(4, 0))
        self.gap_var.trace_add("write", lambda *_: (unit.config(text=unit_text(self.gap_var.get(), "lines.ticks",
                                                                               "lines.tick")), self.count_later()))
        unit.config(text=unit_text(self.gap_var.get(), "lines.ticks", "lines.tick"))
        Scrub(app, [(self.gap_box, self.gap_var, None)], lambda: (1, 10, 1), 0, MOST_GAP, label=label, drag_box=True,
              no_undo=True)
        for w in (label, self.gap_box, unit):
            Tooltip(w, tr("lines.gap_tip"))
        self.count = ttk.Label(box, text="", foreground=look.VALUE)
        self.count.pack(anchor="w", pady=(8, 0))
        if kind != "split":
            self.align_parts(box)
        ttk.Label(box, text=tr("lines.hint_" + kind), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=int(320 * app.scale)).pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(10, 0))
        self.go = ttk.Button(row, text=tr("lines.go_" + kind), command=self.ok)
        self.go.pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.close).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_count()
        centre_over(self, app)
        remember_place(self, "lines")
        self.deiconify()
        self.gap_box.focus_set()
        self.gap_box.select_range(0, "end")

    def align_parts(self, box):
        """The flips' "Line up the lines" tick + Up / Down + "Remove notes that overlap" (a Chinese user's request
        2026-10-31: cut lines bunch up after a flip; ticked before flipping; kept until Spiderweb closes)."""
        app = self.app
        was = getattr(app, "line_align", {"on": False, "way": "up", "remove": False})
        self.align_on = tk.BooleanVar(value=was["on"])
        self.align_way = tk.StringVar(value=was["way"])
        self.align_remove = tk.BooleanVar(value=was["remove"])
        tick = ttk.Checkbutton(box, text=tr("lines.align"), variable=self.align_on, command=self.sync_align)
        tick.pack(anchor="w", pady=(10, 0))
        Tooltip(tick, tr("lines.align_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(20, 0), pady=(2, 0))
        self.align_parts_on = []
        for way in ("up", "down"):
            r = ttk.Radiobutton(row, text=tr("lines.align_" + way), variable=self.align_way, value=way)
            r.pack(side="left", padx=(0, 10))
            Tooltip(r, tr("lines.align_" + way + "_tip"))
            self.align_parts_on.append(r)
        r = ttk.Checkbutton(box, text=tr("lines.align_remove"), variable=self.align_remove)
        r.pack(anchor="w", padx=(20, 0), pady=(2, 0))
        Tooltip(r, tr("lines.align_remove_tip"))
        self.align_parts_on.append(r)
        self.sync_align()

    def sync_align(self):
        for w in self.align_parts_on:
            w.state(["!disabled"] if self.align_on.get() else ["disabled"])

    def gap(self):
        """The typed gap -> whole ticks 0..MOST_GAP, or None."""
        try:
            v = calc(self.gap_var.get())
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(v) or v < 0:
            return None
        return int(min(math.floor(v + 0.5), MOST_GAP))

    def sync(self):
        """The selection or the notes changed (App.sync_panel / shapes_changed; hunt 2026-10-30): counted again."""
        self.count_later()

    def count_later(self):
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(COUNT_WAIT_MS, self.show_count)

    def show_count(self):
        """How many lines the selected shapes have with the typed gap (the button greyed with none)."""
        self.job = None
        g = self.gap()
        if g is None:
            self.count.config(text=tr("lines.bad_gap"), foreground=look.WARN)
            self.go.state(["disabled"])
            return
        n = self.app.line_count(g)
        if n is None:
            self.count.config(text=tr("lines.nothing"), foreground=look.WARN)
        else:
            self.count.config(text=trn("lines.found", n, n=f"{n:,}"), foreground=look.VALUE)
        self.go.state(["!disabled"] if n else ["disabled"])

    def ok(self):
        g = self.gap()
        if g is None or self.go.instate(["disabled"]):
            return
        app = self.app
        new = [i for i in drawn_of(app, app.line_targets()) if id(app.shapes[i]) not in self.asked]
        if new and not ask_drawn(app, self.kind, new, self):  # (drawn shapes selected since it opened, hunt 10-30)
            return
        self.app.line_gap = g
        if self.kind != "split":
            on, way, remove = self.align_on.get(), self.align_way.get(), self.align_remove.get()
            self.app.line_align = {"on": on, "way": way, "remove": remove}
        self.close()
        if self.kind == "split":
            self.app.split_lines(g)
        else:
            self.app.flip_lines(self.kind == "sideways", g, way if on else None, remove)

    def close(self):
        if self.job:
            self.after_cancel(self.job)
        self.app.lines_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_lines(app, kind):
    """Right-click → a line tool: drawn shapes asked about first (they need to be plain notes), then the window."""
    w = app.lines_window
    if w:
        w.close()
    got = app.line_targets()
    if not got:
        return
    drawn = drawn_of(app, got)
    if drawn and not ask_drawn(app, kind, drawn, app):
        return
    LinesWindow(app, kind)


def drawn_of(app, got):
    """The shapes among got that turn into plain notes first: drawn ones, and merged ones (they lose their Split,
    hunt 2026-10-30; Turn into plain notes' rule)."""
    return [i for i in got if "notes" not in app.shapes[i] or app.shapes[i].get("merge")]


def ask_drawn(app, kind, drawn, parent):
    return messagebox.askyesno(tr("lines.title_" + kind), trn("lines.drawn_ask", len(drawn), n=len(drawn)),
                               parent=parent)
