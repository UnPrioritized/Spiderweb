"""The Gate subtract window (right-click "Gate subtract..."; subtract.py): the selected shapes' notes end a few ticks
earlier, so a blank space shows between notes that touched. "Subtract from gates" on / off, how many ticks, and
"Only notes that touch the next one". The amount is kept in beats (a PPQ change keeps the look), shown in whole ticks.

Works like the Sustain window: opening it puts Gate subtract on (the menu item is how it's put on), changes show on
the piano roll at once, the window follows the selection, and before anything else changes there the change being
tried out is kept as its own undo step (settle). OK keeps it (one undo step), Cancel / Esc / X puts back what changed
since. Ctrl+Z / Ctrl+Y step through the changes made in it."""

import json
import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.subtract import MOST, is_subtracted
from window import look
from window.widgets import LocalUndo, Scrub, Tooltip, remember_place, unit_text


def target_indices(app):
    """The selected shapes Gate subtract works on (their numbers): not a Hz bass (its own note lengths), not an image
    (its notes are its picture), like Sustain."""
    return [i for i in sorted(app.sels)
            if i < len(app.shapes) and not app.shapes[i].get("hz") and "picture" not in app.shapes[i]]


def subtract_targets(app):
    return [app.shapes[i] for i in target_indices(app)]


def put_own(sh, sub):
    if sub is None:
        sh.pop("subtract", None)
    else:
        sh["subtract"] = sub


class SubtractWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        app.subtract_window = self
        self.title(tr("subtract.title"))
        self.transient(app)
        self.resizable(False, False)
        self.closed = False
        self.ppq = app.ppq  # (the PPQ the box shows ticks of)
        self.idx = []  # the shapes worked on (numbers in app.shapes)
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text=tr("subtract.nothing"), foreground=look.HINT)
        self.on_var = tk.BooleanVar(value=True)
        self.on_tick = ttk.Checkbutton(box, text=tr("subtract.on"), variable=self.on_var,
                                       command=lambda: self.put_all("on"))
        self.on_tick.pack(anchor="w")
        Tooltip(self.on_tick, tr("subtract.on_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        self.by_label = ttk.Label(row, text=tr("subtract.by"))
        self.by_label.pack(side="left")
        self.by_var = tk.StringVar(value="")
        e = self.by_box = ttk.Entry(row, textvariable=self.by_var, width=8)
        e.pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: (self.on_by(), "break")[1])  # (not OK: the window stays)
        e.bind("<FocusOut>", lambda ev: self.on_by())
        self.by_unit = ttk.Label(row)
        self.by_unit.pack(side="left", padx=(4, 0))
        self.by_var.trace_add("write", lambda *_: self.by_unit.config(
            text=unit_text(self.by_var.get(), "subtract.ticks", "subtract.tick")))
        self.scrub = Scrub(app, [(e, self.by_var, self.on_by)], lambda: (1, 10, 1), 1, MOST * app.ppq,
                           label=self.by_unit, drag_box=True, no_undo=True)
        for w in (self.by_label, e, self.by_unit):
            Tooltip(w, tr("subtract.by_tip"))
        self.touch_var = tk.BooleanVar(value=False)
        self.touch_tick = ttk.Checkbutton(box, text=tr("subtract.touch"), variable=self.touch_var,
                                          command=lambda: self.put_all("touch"))
        self.touch_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.touch_tick, tr("subtract.touch_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(10, 0))
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda ev: self.cancel())
        self.bind("<Return>", lambda ev: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.on, self.bys, self.touches = [], [], []
        self.hist = LocalUndo(self, self.state, self.put_state, main=app.last_step)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.retarget(start=True)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, "subtract")
        self.focus_set()

    @property
    def tgts(self):
        return [self.app.shapes[i] for i in self.idx if i < len(self.app.shapes)]

    # ---- the shapes worked on

    def sub_now(self):
        return json.dumps([t.get("subtract") for t in self.tgts])

    def retarget(self, start=False):
        """Work on the selected shapes (start: opened, which puts Gate subtract on)."""
        self.idx = target_indices(self.app)
        self.saved_sel = self.app.sel_state()  # (for the undo step)
        self.before = self.kept = self.sub_now()  # (kept: put back by Cancel)
        self.load(start)
        if start and self.idx:
            self.store()
        else:
            self.now = self.before  # (as this window last left them)
            self.show()
        self.hist.reset()

    def load(self, start):
        """Each shape's settings from the shapes (start: all on, 1 tick where there was none), then the controls."""
        subs = [t.get("subtract") if is_subtracted(t) else None for t in self.tgts]
        self.on = [start or s is not None for s in subs]
        self.bys = [s["by"] if s else None for s in subs]
        if start:
            self.bys = [b if b is not None else 1 / self.app.ppq for b in self.bys]
        self.touches = [bool(s and s.get("touch")) for s in subs]
        self.show_controls()

    def show_controls(self):
        """The ticks and box as the shapes' settings are: ticked when every shape has it, half-ticked when only some
        do; the box shows the number they share, else stays empty."""
        def tick(var, w, flags):
            var.set(bool(flags) and all(flags))
            w.state(["alternate"] if any(flags) and not all(flags) else ["!alternate"])
        tick(self.on_var, self.on_tick, self.on)
        tick(self.touch_var, self.touch_tick, self.touches)
        got = {b for b in self.bys if b is not None}
        self.by_var.set(self.text_of(got.pop()) if len(got) == 1 and None not in self.bys else "")

    def sync(self):
        """The main window changed the selection or the shapes (sync_panel)."""
        if self.closed:
            return
        if target_indices(self.app) != self.idx:
            self.settle()
            self.retarget()
        elif self.sub_now() != self.now:  # (undo, a file opened)
            self.retarget()

    def fresh(self):
        """The shapes worked on are still there, as this window last left them."""
        return (not self.closed and bool(self.idx) and self.idx[-1] < len(self.app.shapes)
                and self.sub_now() == self.now)

    def pending(self):
        """Is there a change tried here that settle would keep?"""
        return self.fresh() and self.now != self.before

    def put_back(self, saved):
        """The shapes' Gate subtract as before the change tried here, on saved (a copy of the shapes)."""
        for i, sub in zip(self.idx, json.loads(self.kept)):
            if i < len(saved):
                put_own(saved[i], sub)

    def settle(self, also=()):
        """Something else is about to change in the main window: the change tried so far is kept (its own undo
        step), and from now on Cancel only puts back what changes after this. also: other windows kept next
        (App.settle_windows): this step takes their tries back too."""
        if self.fresh():
            self.hist.reset()
        if not self.pending():
            return
        saved = json.loads(json.dumps(self.app.shapes))  # (the step takes back only what was tried here)
        for w in (self, *also):
            w.put_back(saved)
        sel, self.saved_sel, self.before = self.saved_sel, self.app.sel_state(), self.now
        self.kept = self.sub_now()
        self.app.add_undo_step(json.dumps(saved), tr("subtract.step"), sel)
        self.hist.seen()

    def ppq_changed(self):
        """A new PPQ: the box shows the same length in the new ticks (App.on_project_change)."""
        old = self.ppq
        if self.app.ppq == old:
            return
        self.ppq = self.app.ppq
        beats = self.read(self.by_var.get(), old)
        if beats is not None:
            self.by_var.set(self.text_of(beats))
        self.seen = self.by_var.get()
        self.scrub.hi = MOST * self.ppq

    # ---- the amount: kept in beats, shown in whole ticks

    def text_of(self, beats):
        return fmt(max(1, int(round(beats * self.app.ppq))))

    def read(self, text, ppq=None):
        """Typed ticks -> beats (whole ticks, at least 1, at most MOST beats), or None (empty, not a number, under
        1). ppq: the one it was typed for (default the project's)."""
        ppq = ppq or self.app.ppq
        try:
            v = calc(text)
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(v) or v < 0.5:
            return None
        return min(math.floor(v + 0.5) / ppq, MOST)

    def put_all(self, what):
        """A tick changed ("on", "touch"): every shape takes it (a half-ticked one: a click = all on)."""
        if not self.idx:
            return
        n = len(self.idx)
        if what == "on":
            self.on = [self.on_var.get()] * n
            if self.on_var.get():  # (shapes with no amount yet: the box's, else 1 tick)
                b = self.read(self.by_var.get()) or 1 / self.app.ppq
                self.bys = [v if v is not None else b for v in self.bys]
        else:
            self.touches = [self.touch_var.get()] * n
        self.show_controls()
        self.store()
        self.hist.mark()

    def on_by(self):
        """An amount typed / stepped: every shape takes it."""
        if self.closed or str(self.by_box.cget("state")) == "disabled":
            return
        beats = self.read(self.by_var.get())
        if beats is None:  # (a wrong number: back to the last good one, or empty)
            return self.show_controls()
        self.bys = [beats] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark("by")

    # ---- showing

    def show(self):
        """The window as the settings are: what can be used greyed or not."""
        self.seen = self.by_var.get()  # (the box as last used: typed past it without Enter = OK takes it)
        if self.idx:
            self.what.pack_forget()
        elif not self.what.winfo_manager():  # (nothing to work on selected: said so, all greyed)
            self.what.pack(anchor="w", before=self.on_tick, pady=(0, 6))
        self.on_tick.state(["!disabled"] if self.idx else ["disabled"])  # (state(): config(state=) drops a half-tick)
        on = any(self.on) and bool(self.idx)
        self.touch_tick.state(["!disabled"] if on else ["disabled"])
        self.by_box.config(state="normal" if on else "disabled")
        for w in (self.by_label, self.by_unit):
            w.config(foreground="" if on else look.LABEL_OFF)

    def state(self):
        return json.dumps([self.on, self.bys, self.touches])

    def put_state(self, state):
        self.on, self.bys, self.touches = json.loads(state)
        self.show_controls()
        self.store()

    def store(self):
        """Put each shape's settings on it and show them."""
        for i, t in enumerate(self.tgts):
            if not self.on[i] or self.bys[i] is None:
                t.pop("subtract", None)
                continue
            t["subtract"] = dict({"by": self.bys[i]}, **({"touch": True} if self.touches[i] else {}))
        self.now = self.sub_now()
        self.show()
        self.app.shapes_changed()

    # ---- closing

    def ok(self):
        if self.by_var.get() != self.seen and any(self.on):  # (a number typed without Enter counts too)
            self.on_by()
        self.settle()
        self.close()

    def cancel(self):
        if self.idx and self.idx[-1] < len(self.app.shapes) and self.sub_now() != self.kept:
            self.app.roll.cancel_draft()
            for i, sub in zip(self.idx, json.loads(self.kept)):
                put_own(self.app.shapes[i], sub)
        self.close()
        self.app.shapes_changed()

    def close(self):
        self.closed = True
        self.app.subtract_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_subtract(app):
    w = app.subtract_window
    if w:  # (already open: it follows the selection; the menu item puts Gate subtract on)
        w.sync()
        if w.idx and not all(w.on):
            w.on_var.set(True)
            w.put_all("on")
        w.lift()
        w.focus_set()
    elif target_indices(app):
        SubtractWindow(app)


def any_subtracted(app):
    return any(is_subtracted(app.shapes[i]) for i in app.sels if i < len(app.shapes))
