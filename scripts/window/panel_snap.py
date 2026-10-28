"""The side panel's "Notes by snap" row under Last note (notes/paths.py snap_steps): every note of a selected line one
step long, a staircase on the grid. Ticked = the step starts as the Snap box's, then typed in whole ticks."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.engine import BY_SNAP_MOST
from notes.tumour import LINE_KINDS
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, unchanged, unit_word

STEPS = (1, 10, 1)  # quick changes of the step in ticks (widgets.Scrub): step, Shift step, Ctrl step


class SnapPanel:
    """Mixed into App."""

    def _build_by_snap(self, parent):
        """[x] Notes by snap   Step [n] ticks (the box greyed while it's off)."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 0))
        self.by_snap_on = tk.BooleanVar(value=False)
        self.by_snap_tick = ttk.Checkbutton(row, text=tr("app.by_snap"), variable=self.by_snap_on,
                                            command=self.on_by_snap_tick)
        self.by_snap_tick.pack(side="left")
        lb = self.by_snap_word = ttk.Label(row, text=tr("app.by_snap_step"))
        lb.pack(side="left", padx=(8, 0))
        self.by_snap_var = tk.StringVar()
        self.by_snap_entry = ttk.Entry(row, textvariable=self.by_snap_var, width=6)
        self.by_snap_entry.pack(side="left", padx=4)
        unit_word(ttk.Label(row, foreground=look.HINT), self.by_snap_var, "app.by_snap_unit",
                  "app.by_snap_unit_one").pack(side="left")
        for w in (self.by_snap_tick, lb, self.by_snap_entry):
            Tooltip(w, tr("app.by_snap_tip"))
        self.by_snap_entry.bind("<Return>", lambda e: self.on_by_snap())
        leave_box(self, self.by_snap_entry, self.by_snap_var, self.on_by_snap)
        Scrub(self, [(self.by_snap_entry, self.by_snap_var, self.on_by_snap)], STEPS, 1, BY_SNAP_MOST * self.ppq,
              label=lb)

    def by_snap_targets(self):
        """The selected lines (nothing selected = none: new shapes always start without it)."""
        return [self.shapes[i] for i in sorted(self.sels)
                if i < len(self.shapes) and self.shapes[i]["kind"] in LINE_KINDS]

    def sync_by_snap(self):
        tgts = self.by_snap_targets()
        ons = [bool(t.get("by_snap")) for t in tgts]
        steps = {math.floor(t["by_snap"] * self.ppq + 0.5) for t in tgts if t.get("by_snap")}
        self._loading = True
        self.by_snap_on.set(bool(ons) and all(ons))
        self.by_snap_var.set(fmt(steps.pop()) if len(steps) == 1 else "")
        self._loading = False
        self.by_snap_tick.state(["alternate" if any(ons) and not all(ons) else "!alternate"])
        self.by_snap_tick.state(["!disabled" if tgts else "disabled"])
        self.by_snap_entry.state(["!disabled" if any(ons) else "disabled"])
        self.by_snap_entry.config(style="TEntry")
        self.by_snap_word.config(foreground="" if any(ons) else look.FAINT_TEXT)
        # Last note does nothing while every picked line steps (the last note always gets the full step)
        off = bool(ons) and all(ons)
        for w in self.last_parts:
            w.state(["disabled" if off else "!disabled"])

    def on_by_snap_tick(self):
        if self._loading:
            return
        tgts = self.by_snap_targets()
        if not tgts:
            return
        on = self.by_snap_on.get()
        if self.sels:
            self.push_undo(name=tr("app.by_snap"))
        step = self.snap_ticks() / self.ppq  # (turned on: the Snap box's length, whole ticks)
        for t in tgts:
            if not on:
                t.pop("by_snap", None)
            elif not t.get("by_snap"):
                t["by_snap"] = step
        self.shapes_changed()
        self.sync_by_snap()

    def on_by_snap(self, left=False):
        """The step box (whole ticks): every ticked line takes it."""
        e = self.by_snap_entry
        if self._loading or e.instate(["disabled"]) or left and unchanged(e):
            return
        try:
            ticks = calc(self.by_snap_var.get())
            if not 1 <= ticks <= BY_SNAP_MOST * self.ppq:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        good(e)
        step = math.floor(ticks + 0.5) / self.ppq
        tgts = [t for t in self.by_snap_targets() if t.get("by_snap")]
        if all(t["by_snap"] == step for t in tgts):
            return
        self.begin_edit(("by_snap", tuple(sorted(self.sels))))
        for t in tgts:
            t["by_snap"] = step
        self.shapes_changed()
