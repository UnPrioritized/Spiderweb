"""The Fold tracks window (right-click → Fold tracks…; notes/fold.py; BMC tester + user 2026-11-07): the selected
shapes' tracks folded down to fewer, previewed on the piano roll while the window is open. Fold = the selected shapes
become ONE plain-notes shape (drawn shapes too), one undo step; Cancel / Esc / X = everything as it was, no step.
The window holds the keyboard and mouse (grab) while open: nothing else changes under the preview."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes.fold import colour_count, fold_map, folded
from notes.loaded import clean_extract, extract_groups, held_extract
from notes.mask import is_mask
from window import look
from window.join_split import by_owner, without10
from window.widgets import Scrub, Tooltip, remember_place, unit_word

WRAP = 330


def fold_targets(app):
    """The selected shapes Fold tracks works on: not pictures, masks, Hz bass, or notes laid in a track (the
    track's channel wins there)."""
    return [i for i in sorted(app.sels) if i < len(app.shapes) and (sh := app.shapes[i])
            and "picture" not in sh and not is_mask(sh) and not sh.get("hz") and not sh.get("into")]


class FoldWindow(tk.Toplevel):
    def __init__(self, app, got):
        super().__init__(app)
        self.app, self.got = app, got
        self.title(tr("fold.title"))
        self.transient(app)
        self.resizable(False, False)
        self.job = None
        self.shown = None  # (the settings the piano roll shows now)
        app.commit_typing()
        if app.notes_late:
            app.shapes_changed(now=True)
        app.roll.cancel_draft()
        self.originals = list(app.shapes)
        self.state0, self.sel0 = None, app.sel_state()
        self.use10, multi = app.multi_rendered()
        # (the order the shapes take slots in: channel 10 the 16th with "Use channel 10", never with only a
        # picture's, slot order with "Channels 1-16 in order")
        self.seq = ("plain" if app.ch10_order.get() else "last10") if app.use_ch10.get() else "skip10"
        shown = by_owner(multi)
        parts = [app.with_hidden(app.shapes[i], shown(i)) for i in got]
        self.rows = np.concatenate(parts) if parts else np.zeros((0, 6), np.int64)
        self.slots = np.unique(self.rows[:, 4]) if len(self.rows) else np.zeros(0, np.int64)
        self.tracks = len(self.slots)
        self.colours = colour_count(self.slots, self.use10) if self.tracks else 0
        self.slots_of = app.track_slots_in(self.rows)  # (CC passengers: each shape's track -> its slot now)
        self.result = None
        self.how = tk.StringVar(value="row")
        self.cut = tk.BooleanVar(value=True)
        self.even = tk.BooleanVar(value=False)
        self.keep = tk.BooleanVar(value=False)

        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("fold.uses", tracks=trn("n.tracks", self.tracks, n=self.tracks),
                               colours=trn("n.colours", self.colours, n=self.colours)),
                  wraplength=int(WRAP * app.scale), justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        self.n_label = ttk.Label(box, text=tr("fold.into"))
        self.n_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0), padx=(6, 0))
        self.n_var = tk.StringVar(value=fmt(max(1, self.colours)))
        self.n_box = ttk.Entry(row, textvariable=self.n_var, width=6)
        self.n_box.pack(side="left")
        self.n_box.bind("<Return>", lambda e: (self.on_box(), "break")[1])
        self.n_box.bind("<FocusOut>", lambda e: self.on_box())
        unit_word(ttk.Label(row), self.n_var, "fold.tracks").pack(side="left", padx=(4, 0))
        self.each = ttk.Label(row, foreground=look.HINT)
        self.each.pack(side="left", padx=(8, 0))
        Scrub(app, [(self.n_box, self.n_var, self.on_box)], lambda: (1, 10, 1), 1, max(1, self.tracks),
              label=self.n_label, drag_box=True, no_undo=True)
        for w in (self.n_label, self.n_box):
            Tooltip(w, tr("fold.into_tip"))
        self.n = self.read(self.n_var.get()) or 1

        ttk.Label(box, text=tr("fold.how")).grid(row=2, column=0, sticky="nw", pady=(8, 0))
        self.how_text = {}
        for r, (key, name) in enumerate((("row", "fold.in_a_row"), ("loop", "fold.looping"))):
            b = ttk.Radiobutton(box, text=tr(name), variable=self.how, value=key, command=self.changed)
            b.grid(row=2 + r, column=1, sticky="w", pady=(8 if r == 0 else 2, 0), padx=(6, 0))
            Tooltip(b, tr(name + "_tip"))
            self.how_text[key] = ttk.Label(box, foreground=look.HINT)
            self.how_text[key].grid(row=2 + r, column=2, sticky="w", pady=(8 if r == 0 else 2, 0), padx=(8, 0))

        self.ticks = {}
        for r, (var, key) in enumerate(((self.cut, "fold.cut"), (self.even, "fold.even"),
                                        (self.keep, "fold.keep"))):
            t = ttk.Checkbutton(box, text=tr(key), variable=var, command=self.changed)
            t.grid(row=4 + r, column=0, columnspan=3, sticky="w", pady=(8 if r == 0 else 2, 0))
            Tooltip(t, tr(key + "_tip"))
            self.ticks[key] = t
        self.outcome = ttk.Label(box, foreground=look.VALUE, wraplength=int(WRAP * app.scale), justify="left")
        self.outcome.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))
        buttons = ttk.Frame(box)
        buttons.grid(row=8, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.fold_button = ttk.Button(buttons, text=tr("fold.fold"), command=self.fold)
        self.fold_button.pack(side="left")
        ttk.Button(buttons, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.fold())
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.show()
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, "fold")
        self.preview()
        self.focus_set()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # ---- settings

    def least(self):
        """The fewest tracks the box takes: 1, or one per colour with Keep colours."""
        return max(1, self.colours) if self.keep.get() else 1

    def read(self, text):
        try:
            v = calc(text)
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(v):
            return None
        return int(min(max(math.floor(v + 0.5), self.least()), max(1, self.tracks)))

    def on_box(self):
        n = self.read(self.n_var.get())
        if n is None:
            self.n_var.set(fmt(self.n))
            return
        self.n_var.set(fmt(n))
        if n != self.n:
            self.n = n
            self.changed()

    def settings(self):
        return self.n, self.how.get(), self.even.get(), self.keep.get(), self.cut.get()

    def changed(self):
        if self.n < self.least():
            self.n = self.least()
            self.n_var.set(fmt(self.n))
        self.show()
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(60, self.preview)

    # ---- showing

    def show(self):
        n, how, even, keep, _ = self.settings()
        t = self.tracks
        self.each.config(text=tr("fold.each", n=f"{t / max(n, 1):,.0f}") if t else "")
        g = -(-t // max(n, 1)) if t else 1
        self.how_text["row"].config(text=tr("fold.row_example", a=g, b=g + 1, c=2 * g) if g > 1 else "")
        self.how_text["loop"].config(text=tr("fold.loop_example", a=n + 1, b=2 * n + 1) if n < t else "")
        self.ticks["fold.even"].state(["!disabled"] if how == "row" else ["disabled"])

    def preview(self):
        """The folded notes shown on the piano roll (the shapes as they were, the selected ones swapped for one)."""
        self.job = None
        if not self.winfo_exists():
            return
        key = self.settings()
        if key == self.shown or not self.tracks:
            self.say()
            return
        n, how, even, keep, cut = key
        fold = fold_map(self.slots, n, how, even, keep, self.use10, self.seq)
        app = self.app
        app.shapes[:] = self.originals  # (built from the shapes as they were: CC passengers, sustain)
        rows, changed = folded(self.rows, fold, cut)
        slots_of = lambda o: {t: fold.get(s, s) for t, s in self.slots_of(o).items()}
        sh = without10(app.copied(rows, False, True, slots_of), self.use10)
        sh["made"] = "plain"
        sh["name"] = tr("fold.name", name=app.shape_label(self.originals[self.got[0]]))
        if cut:
            sh.pop("held", None)
        else:  # (overlaps kept as they are, like a sustained shape's)
            sh["held"] = True
        ex = clean_extract([g for i in self.got for g in extract_groups(held_extract(self.originals[i]))])
        if ex:
            sh["extract"] = ex
        self.result = sh
        self.made = (len(set(fold.values())), changed)
        app.shapes[:] = self.with_result(sh)
        app.select_many([self.got[0]], self.got[0])
        app.shapes_changed()
        self.shown = key
        self.say()

    def with_result(self, sh):
        gone = set(self.got[1:])
        return [sh if i == self.got[0] else s for i, s in enumerate(self.originals) if i not in gone]

    def say(self):
        if not self.tracks:
            self.outcome.config(text=tr("fold.nothing"))
            self.fold_button.state(["disabled"])
            return
        if self.result is None:
            return
        new, changed = self.made
        text = tr("fold.outcome", a=trn("n.tracks", self.tracks, n=self.tracks), b=trn("n.tracks", new, n=new))
        n, how, even, keep, _ = self.settings()
        if how == "row" and not even and not keep and new < n:  # (full groups can't make n: said why)
            text += tr("fold.short", n=n)
        if changed:
            text += tr("fold.fixed", notes=trn("n.notes", changed, n=changed))
        self.outcome.config(text=text)
        self.fold_button.state(["!disabled"] if new < self.tracks or changed else ["disabled"])

    # ---- closing

    def fold(self):
        self.on_box()
        if self.job:
            self.after_cancel(self.job)
            self.preview()
        if self.result is None or "disabled" in self.fold_button.state():
            return
        app = self.app
        from window.history import undo_state
        app.shapes[:] = self.originals
        before = undo_state(app.shapes)
        app.put_plain({self.got[0]: self.result})  # (an Add between group / sliced pieces: as Turn into plain notes)
        for i in reversed(self.got[1:]):
            del app.shapes[i]
        app.add_undo_step(before, tr("fold.title"), self.sel0)
        app.select_many([self.got[0]], self.got[0])
        app.shapes_changed()
        new = self.made[0]
        app.status.config(text=tr("fold.done", a=trn("n.tracks", self.tracks, n=self.tracks),
                                  b=trn("n.tracks", new, n=new)))
        self.close()

    def cancel(self):
        app = self.app
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        if self.shown is not None:
            app.shapes[:] = self.originals
            app.select_many(self.sel0[0], self.sel0[1])
            app.shapes_changed()
        self.close()

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.app.fold_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_fold(app):
    w = getattr(app, "fold_window", None)
    if w and w.winfo_exists():
        w.lift()
        w.focus_set()
        return
    got = fold_targets(app)
    if got:
        app.fold_window = FoldWindow(app, got)
