"""The Gate extend window (right-click "Gate extend..."; notes/extend.py): the selected shapes' notes end later, so
they run over into the notes after them like a run played by hand. The Gate subtract window's layout and behaviour
(subtract_window.SubtractWindow: live, follows the selection, settle, OK / Cancel, own Ctrl+Z, "Change across the
shape" graph, typed amounts: 120 = ticks, 50% = % of each note's length, 50% + 10 = both) with one side only, 50% to
start with, and "Same amount for every note"."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from notes.extend import clean_extend, is_extended, part_extend
from window.subtract_window import SubtractWindow, side_data, target_indices
from window.widgets import Tooltip


def _bare_notes(app, sh):
    """Shape sh's notes before Gate extend (and Gate subtract, done after it)."""
    return np.asarray(app.notes_of({k: v for k, v in sh.items() if k not in ("extend", "subtract")}),
                      np.int64).reshape(-1, 4)


def carry_extend(app, whole, pieces):
    """Pieces cut off shape whole (Slice, Split here, Split into letters) that took its Gate extend: a graph across the
    shape is cut so each note keeps its amount (extend.part_extend)."""
    ext = whole.get("extend")
    if not isinstance(ext, dict) or not ext.get("vary"):
        return
    pieces = [p for p in pieces if isinstance(p.get("extend"), dict)]
    if not pieces:
        return
    notes = _bare_notes(app, whole)
    for p in pieces:
        p["extend"] = part_extend(p["extend"], notes, _bare_notes(app, p), app.ppq)


class ExtendWindow(SubtractWindow):
    KEY = "extend"
    T = "extend"
    ATTR = "extend_window"
    SIDES = ("end",)
    TOUCH = False

    def side_data(self):
        d = side_data()
        d["sames"] = []  # (per shape: "Same amount for every note"; kept while no %, used only with %)
        return d

    @property
    def pct(self):
        """Some amount shown has a % part ("Same amount for every note" can be used)."""
        if not getattr(self, "sides", None):
            return True
        return any(v[0] is not None for vs in self.shape_values() for v in vs)

    def extra_rows(self, box):
        self.same_var = tk.BooleanVar(value=False)
        self.same_tick = ttk.Checkbutton(box, text=tr("extend.same"), variable=self.same_var, command=self.put_same)
        self.same_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.same_tick, tr("extend.same_tip"))

    def put_same(self):
        """"Same amount for every note" clicked: every shape takes it (a half-ticked one: a click = all on)."""
        if not self.idx:
            return
        self.take_typing()
        self.sides["end"]["sames"] = [self.same_var.get()] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark()

    def show(self):
        super().show()
        on = any(self.ons) and bool(self.idx) and self.pct  # (no %: every note gets the same amount already)
        self.same_tick.state(["!disabled"] if on else ["disabled"])

    # ---- the hooks (subtract_window.SubtractWindow)

    def has_it(self, sh):
        return is_extended(sh)

    def own_part(self, sh):
        """Its Gate extend + an Add between step's mark for one of its own ("-": none), like Gate subtract's."""
        return [sh.get("extend"), (sh.get("between") or {}).get("ext", "-")]

    def put_own(self, sh, part):
        ext, mark = part
        if ext is None:
            sh.pop("extend", None)
        else:
            sh["extend"] = ext
        b = sh.get("between")
        if isinstance(b, dict):
            if mark == "-":
                b.pop("ext", None)
            else:
                b["ext"] = mark

    def first_amount(self):
        return [50.0, 0.0]

    def made_of(self, i):
        side = self.side_of("end", i)
        side = {k: v for k, v in (side or {"by": self.first_amount()}).items() if k not in ("off", "touch", "cuts")}
        side = self.stored(side)
        sames = self.sides["end"].get("sames") or []
        if side.get("unit") == "pct" and i < len(sames) and sames[i]:
            side["same"] = True
        return side

    def clean_saved(self, v):
        return clean_extend(v)

    def load(self, start):
        """As Gate subtract's; "Same amount" per shape (a shape without it yet: the user's default's)."""
        mine = self.saved_default() or {}
        exts = [t.get("extend") if is_extended(t) else mine for t in self.tgts]
        self.sides["end"]["sames"] = [bool(e and e.get("same")) for e in exts]
        super().load(start)

    def show_controls(self):
        super().show_controls()
        if hasattr(self, "same_tick"):
            sames = self.sides["end"].get("sames") or []
            self.same_var.set(bool(sames) and all(sames))
            self.same_tick.state(["alternate"] if any(sames) and not all(sames) else ["!alternate"])


def open_extend(app):
    w = app.extend_window
    if w:  # (already open: it follows the selection; the menu item puts Gate extend on)
        w.sync()
        if w.idx and not all(w.ons):
            w.on_var.set(True)
            w.put_all("on")
        w.lift()
        w.focus_set()
    elif target_indices(app):
        ExtendWindow(app)


def any_extended(app):
    return any(is_extended(app.shapes[i]) for i in app.sels if i < len(app.shapes))
