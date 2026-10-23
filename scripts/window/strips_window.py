"""The Channel per strip window (right-click pasted notes, "Channel per strip (Experimental)…"; strips.py): each strip of
a pasted track goes on channels of its own. "Channel per strip" on / off, "Other notes between" (how many notes of
other colours on a key make a colour coming back there its next strip; raised, two colours taking turns stop
splitting), how many strips that makes, and "Show each strip in its own colour" (while the window is open the
strips are painted in colours far apart round the palette, so a wrong guess shows: user 2026-10-23 picked the count +
this over exporting to look).

Works like the Gate subtract window: opening it puts Channel per strip on, changes show at once, the window follows the
selection, and before anything else changes there the change being tried out is kept as its own undo step (settle).
OK keeps it (one undo step), Cancel / Esc / X puts back what changed since. Ctrl+Z / Ctrl+Y step through the changes
made in it."""

import json
import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes.strips import MOST_GAP, can_strip, clean_strips, has_strips, strip_sizes
from window import look
from window.widgets import LocalUndo, Scrub, Tooltip, remember_place, unit_text


def target_indices(app):
    """The selected shapes Channel per strip works on (their numbers): pasted notes (strips.can_strip)."""
    return [i for i in sorted(app.sels) if i < len(app.shapes) and can_strip(app.shapes[i])]


def strips_targets(app):
    return [app.shapes[i] for i in target_indices(app)]


class StripsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        app.strips_window = self
        self.title(tr("strips.title"))
        self.transient(app)
        self.resizable(False, False)
        self.closed = False
        self.idx = []  # the shapes worked on (numbers in app.shapes)
        self.ons, self.gaps = [], []  # (per shape: Channel per strip on, notes between)
        self.painted = []  # (the shapes whose strips were last painted in their own colours)
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text=tr("strips.nothing"), foreground=look.HINT)
        self.on_var = tk.BooleanVar(value=True)
        self.on_tick = ttk.Checkbutton(box, text=tr("strips.on"), variable=self.on_var, command=self.put_on)
        self.on_tick.pack(anchor="w")
        Tooltip(self.on_tick, tr("strips.on_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        self.gap_label = ttk.Label(row, text=tr("strips.gap"))
        self.gap_label.pack(side="left")
        self.gap_var = tk.StringVar(value="")
        self.gap_box = ttk.Entry(row, textvariable=self.gap_var, width=6)
        self.gap_box.pack(side="left", padx=(4, 0))
        self.gap_box.bind("<Return>", lambda ev: (self.on_box(), "break")[1])  # (not OK: the window stays)
        self.gap_box.bind("<FocusOut>", lambda ev: self.on_box())
        self.gap_unit = ttk.Label(row)
        self.gap_unit.pack(side="left", padx=(4, 0))
        self.gap_var.trace_add("write", lambda *_: self.gap_unit.config(
            text=unit_text(self.gap_var.get(), "strips.notes", "strips.note")))
        Scrub(app, [(self.gap_box, self.gap_var, self.on_box)], lambda: (1, 10, 1), 1, MOST_GAP,
              label=self.gap_label, drag_box=True, no_undo=True)
        for w in (self.gap_label, self.gap_box, self.gap_unit):
            Tooltip(w, tr("strips.gap_tip"))
        self.count = ttk.Label(box, text="", foreground=look.VALUE)
        self.count.pack(anchor="w", pady=(8, 0))
        self.warn = ttk.Label(box, text=tr("strips.multi_only"), foreground=look.WARN, wraplength=int(320 * app.scale),
                              justify="left")
        self.view_var = tk.BooleanVar(value=True)
        self.view_tick = ttk.Checkbutton(box, text=tr("strips.view"), variable=self.view_var,
                                         command=self.app.shapes_changed)
        self.view_tick.pack(anchor="w", pady=(8, 0))
        Tooltip(self.view_tick, tr("strips.view_tip"))
        ttk.Label(box, text=tr("strips.hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=int(320 * app.scale)).pack(anchor="w", pady=(8, 0))
        self.buttons = row = ttk.Frame(box)
        row.pack(anchor="e", pady=(10, 0))
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda ev: self.cancel())
        self.bind("<Return>", lambda ev: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.hist = LocalUndo(self, self.state, self.put_state, main=app.last_step)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.retarget(start=True)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, "strips")
        self.focus_set()

    @property
    def tgts(self):
        return [self.app.shapes[i] for i in self.idx if i < len(self.app.shapes)]

    def shown_owners(self):
        """The shapes whose strips are painted each in its own colour now (App.shapes_changed)."""
        if self.closed or not self.view_var.get():
            return ()
        return [i for i, on in zip(self.idx, self.ons) if on and i < len(self.app.shapes)]

    # ---- the shapes worked on

    def now_text(self):
        return json.dumps([t.get("strips") for t in self.tgts])

    def retarget(self, start=False):
        """Work on the selected shapes (start: opened, which puts Channel per strip on)."""
        self.idx = target_indices(self.app)
        self.saved_sel = self.app.sel_state()  # (for the undo step)
        self.before = self.kept = self.now_text()  # (kept: put back by Cancel)
        self.ons = [start or has_strips(t) for t in self.tgts]
        self.gaps = [(clean_strips(t.get("strips")) or {"gap": 1})["gap"] for t in self.tgts]
        self.show_controls()
        if start and self.idx:
            self.store()
        else:
            self.now = self.before
            if list(self.shown_owners()) != self.painted:  # (the strips' colours shown for these shapes now)
                self.app.shapes_changed()
            self.show()
        self.painted = list(self.shown_owners())
        self.hist.reset()

    def show_controls(self):
        """The tick and box as the shapes' settings are: ticked when every shape has it, half-ticked when only some
        do; the box shows the number they share, else stays empty."""
        self.on_var.set(bool(self.ons) and all(self.ons))
        self.on_tick.state(["alternate"] if any(self.ons) and not all(self.ons) else ["!alternate"])
        got = set(self.gaps)
        self.gap_var.set(fmt(got.pop()) if len(got) == 1 else "")
        self.seen = self.gap_var.get()

    def sync(self):
        """The main window changed the selection or the shapes (sync_panel)."""
        if self.closed:
            return
        if target_indices(self.app) != self.idx:
            self.settle()
            self.retarget()
        elif self.now_text() != self.now:  # (undo, a file opened)
            self.retarget()
        else:
            self.show()

    def fresh(self):
        """The shapes worked on are still there, as this window last left them."""
        return (not self.closed and bool(self.idx) and self.idx[-1] < len(self.app.shapes)
                and self.now_text() == self.now)

    def pending(self):
        """Is there a change tried here that settle would keep?"""
        return self.fresh() and self.now != self.before

    def put_back(self, saved):
        """The shapes' Channel per strip as before the change tried here, on saved (a copy of the shapes)."""
        for i, s in zip(self.idx, json.loads(self.kept)):
            if i < len(saved):
                put_own(saved[i], s)

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
        self.kept = self.now
        self.app.add_undo_step(json.dumps(saved), tr("strips.step"), sel)
        self.hist.seen()

    def ppq_changed(self):
        pass  # (counted in notes: nothing to show again)

    # ---- changes

    def read(self, text):
        """Typed notes between -> a whole number 1..MOST_GAP, or None."""
        try:
            v = calc(text)
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(v) or v < 1:
            return None
        return int(min(math.floor(v + 0.5), MOST_GAP))

    def put_on(self):
        """The tick: every shape takes it (a half-ticked one: a click = all on)."""
        if not self.idx:
            return
        self.take_typing()
        self.ons = [self.on_var.get()] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark()

    def on_box(self):
        """Notes between typed / stepped: every shape takes it."""
        if self.closed or str(self.gap_box.cget("state")) == "disabled":
            return
        g = self.read(self.gap_var.get())
        if g is None:  # (a wrong number: back to the last good one, or empty)
            return self.show_controls()
        self.gaps = [g] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark("gap")

    def take_typing(self):
        """A number typed without Enter counts too (OK, or the tick used next)."""
        if any(self.ons) and self.gap_var.get() != self.seen:
            self.on_box()

    # ---- showing

    def counted(self):
        """The line saying how many strips the shapes are cut into now (Multi channel), or ""."""
        if self.app.channel_mode.get() != "auto":
            return ""
        sizes = [strip_sizes(self.app.rendered, i) for i, on in zip(self.idx, self.ons) if on]
        sizes = [s for s in sizes if len(s)]
        if not sizes:
            return ""
        n = sum(len(s) for s in sizes)
        every = [int(v) for s in sizes for v in s.tolist()]
        most = max(set(every), key=every.count) if len(every) <= 100000 else every[0]
        strips = trn("strips.n_strips", n, n=n)
        if len(sizes) > 1:
            return tr("strips.count_shapes", strips=strips, shapes=len(sizes))
        return tr("strips.count", strips=strips, notes=trn("n.notes", most, n=most))

    def show(self):
        """The window as the settings are: greyed when nothing to work on / off, the count and the Multi channel
        warning."""
        if self.idx:
            self.what.pack_forget()
        elif not self.what.winfo_manager():  # (nothing to work on selected: said so, all greyed)
            self.what.pack(anchor="w", before=self.on_tick, pady=(0, 6))
        self.on_tick.state(["!disabled"] if self.idx else ["disabled"])  # (state(): config(state=) drops a half-tick)
        on = bool(self.idx) and any(self.ons)
        self.gap_box.config(state="normal" if on else "disabled")
        for w in (self.gap_label, self.gap_unit):
            w.config(foreground="" if on else look.LABEL_OFF)
        self.view_tick.state(["!disabled"] if on else ["disabled"])
        multi = self.app.channel_mode.get() == "auto"
        if on and not multi and not self.warn.winfo_manager():
            self.warn.pack(anchor="w", pady=(8, 0), after=self.count)
        elif (multi or not on) and self.warn.winfo_manager():
            self.warn.pack_forget()
        self.count.config(text=self.counted() if on else "")

    # ---- undo inside, storing

    def state(self):
        return json.dumps([self.ons, self.gaps])

    def put_state(self, state):
        self.ons, self.gaps = json.loads(state)
        self.show_controls()
        self.store()

    def store(self):
        """Put each shape's settings on it and show them."""
        for t, on, g in zip(self.tgts, self.ons, self.gaps):
            if on:
                t["strips"] = {"gap": g}
            else:
                t.pop("strips", None)
        self.now = self.now_text()
        self.app.shapes_changed()
        self.show()

    # ---- closing

    def ok(self):
        self.take_typing()
        self.settle()
        self.close()

    def cancel(self):
        if self.idx and self.idx[-1] < len(self.app.shapes) and self.now_text() != self.kept:
            for i, s in zip(self.idx, json.loads(self.kept)):
                put_own(self.app.shapes[i], s)
        self.close()

    def close(self):
        self.closed = True
        self.app.strips_window = None
        self.destroy()
        self.app.shapes_changed()  # (the strips' own colours go)
        self.app.roll.focus_set()


def put_own(sh, s):
    if s is None:
        sh.pop("strips", None)
    else:
        sh["strips"] = s


def open_strips(app):
    w = app.strips_window
    if w:  # (already open: it follows the selection; the menu item puts Channel per strip on)
        w.sync()
        if w.idx and not all(w.ons):
            w.on_var.set(True)
            w.put_on()
        w.lift()
        w.focus_set()
    elif target_indices(app):
        StripsWindow(app)


def any_strips(app):
    return any(has_strips(app.shapes[i]) for i in app.sels if i < len(app.shapes))
