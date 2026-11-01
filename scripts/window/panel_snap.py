"""The side panel's "Notes by snap" row under Last note (notes/paths.py snap_steps): every note of a selected line one
step long, a staircase on the grid. Ticked = the step starts as the Snap box's, then typed in whole ticks."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.engine import BY_SNAP_MOST, snap_name, snap_name_beats
from notes.paths import SNAP_ENDS
from notes.tumour import LINE_KINDS
from window import look
from files.snap import DEFAULT_SNAP
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, unchanged, unit_text

STEPS = (1, 10, 1)  # quick changes of the step in ticks (widgets.Scrub): step, Shift step, Ctrl step


class SnapPanel:
    """Mixed into App."""

    def _build_by_snap(self, parent):
        """[x] Notes by snap   Step [n] ticks (the box greyed while it's off)."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 0))
        self.by_snap_rows = [row]  # (hidden for Glissando runs: Last note shows for them, Notes by snap doesn't)
        self.by_snap_on = tk.BooleanVar(value=False)
        self.by_snap_tick = ttk.Checkbutton(row, text=tr("app.by_snap"), variable=self.by_snap_on,
                                            command=self.on_by_snap_tick)
        self.by_snap_tick.pack(side="left")
        lb = self.by_snap_word = ttk.Label(row, text=tr("app.by_snap_step"))
        lb.pack(side="left", padx=(8, 0))
        self.by_snap_var = tk.StringVar()
        self.by_snap_entry = ttk.Entry(row, textvariable=self.by_snap_var, width=6)
        self.by_snap_entry.pack(side="left", padx=4)
        unit = ttk.Label(row, foreground=look.HINT)
        unit.pack(side="left")

        def put_unit(*_):  # ("ticks" after a number, nothing after a note length like 1/16)
            text = self.by_snap_var.get()
            unit.config(text="" if snap_name(text) else unit_text(text, "app.by_snap_unit", "app.by_snap_unit_one"))
        self.by_snap_var.trace_add("write", put_unit)
        put_unit()
        for w in (self.by_snap_tick, lb, self.by_snap_entry):
            Tooltip(w, tr("app.by_snap_tip"))
        self.by_snap_entry.bind("<Return>", lambda e: self.on_by_snap())
        leave_box(self, self.by_snap_entry, self.by_snap_var, self.on_by_snap)
        scrub = Scrub(self, [(self.by_snap_entry, self.by_snap_var, self.on_by_snap)], STEPS, 1,
                      BY_SNAP_MOST * self.ppq, label=lb)
        scrub.read = self.step_ticks  # (dragging / Up / Down from a note length: from its ticks)
        # a line that isn't a whole number of steps long: how its end goes (user 2026-10-28: their choice)
        end = ttk.Frame(parent)
        end.pack(fill="x", pady=(2, 0))
        self.by_snap_rows.append(end)
        self.by_snap_end_word = ttk.Label(end, text=tr("app.by_snap_end"))
        self.by_snap_end_word.pack(side="left", padx=(20, 0))
        self.by_snap_end_box = ttk.Combobox(end, values=[tr("app.by_snap_end_" + h) for h in SNAP_ENDS],
                                            state="readonly", width=max(len(tr("app.by_snap_end_" + h))
                                                                        for h in SNAP_ENDS))
        self.by_snap_end_box.pack(side="left", padx=(5, 0))
        self.by_snap_end_box.bind("<<ComboboxSelected>>", lambda e: (self.on_by_snap_end(), self.roll.focus_set()))
        for w in (self.by_snap_end_word, self.by_snap_end_box):
            Tooltip(w, tr("app.by_snap_end_tip"))

    def step_ticks(self, text):
        """The Step box's text -> ticks: a note length ("1/32", like the Snap list) or a number of ticks."""
        name = snap_name(text)
        return snap_name_beats(name) * self.ppq if name else calc(text)

    def by_snap_targets(self):
        """The selected lines (nothing selected = none: new shapes always start without it)."""
        return [self.shapes[i] for i in sorted(self.sels)
                if i < len(self.shapes) and self.shapes[i]["kind"] in LINE_KINDS]

    def sync_by_snap(self):
        tgts = self.by_snap_targets()
        ons = [bool(t.get("by_snap")) for t in tgts]
        # (shown as typed: a note length's name, else whole ticks)
        steps = [t.get("by_snap_name") or fmt(math.floor(t["by_snap"] * self.ppq + 0.5)) for t in tgts
                 if t.get("by_snap")]
        self._loading = True
        self.by_snap_on.set(bool(ons) and all(ons))
        # (different steps: empty + the shared tip, stepping from the first one's, like other boxes; hunt)
        same_or_blank(self.by_snap_entry, self.by_snap_var, steps)
        self._loading = False
        self.by_snap_tick.state(["alternate" if any(ons) and not all(ons) else "!alternate"])
        self.by_snap_tick.state(["!disabled" if tgts else "disabled"])
        self.by_snap_entry.state(["!disabled" if any(ons) else "disabled"])
        self.by_snap_entry.config(style="TEntry")
        self.by_snap_word.config(foreground="" if any(ons) else look.FAINT_TEXT)
        ends = {t.get("by_snap_end", SNAP_ENDS[0]) for t in tgts if t.get("by_snap")}
        if len(ends) == 1:
            self.by_snap_end_box.current(SNAP_ENDS.index(ends.pop()))
        else:
            self.by_snap_end_box.set(tr("widgets.mixed") if ends else "")
        self.by_snap_end_box.state(["!disabled" if any(ons) else "disabled"])
        self.by_snap_end_word.config(foreground="" if any(ons) else look.FAINT_TEXT)
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
        # turned on: the Snap box's length (a note length of the list keeps its name, else whole ticks); Snap off =
        # 1/16 (hunt 2026-10-28: a note on every tick is rarely meant; 1 typed in Step still gives it)
        name = snap_name(DEFAULT_SNAP if self.snap.get() == "off" else self.snap.get())
        step = snap_name_beats(name) if name else self.snap_ticks() / self.ppq
        if on and not self.confirm_big([dict(t, by_snap=step) if not t.get("by_snap") else t for t in tgts]):
            self.sync_by_snap()  # (too many notes, No: the tick back)
            return
        if self.sels:
            self.push_undo(name=tr("app.by_snap"))
        for t in tgts:
            if not on:
                t.pop("by_snap", None)
                t.pop("by_snap_name", None)
            elif not t.get("by_snap"):
                t["by_snap"] = step
                if name:
                    t["by_snap_name"] = name
        self.shapes_changed()
        self.sync_by_snap()

    def on_by_snap_end(self):
        """Line's end dropdown: every ticked line takes it."""
        i = self.by_snap_end_box.current()
        if self._loading or i < 0:
            return
        how = SNAP_ENDS[i]
        tgts = [t for t in self.by_snap_targets() if t.get("by_snap")]
        if all(t.get("by_snap_end", SNAP_ENDS[0]) == how for t in tgts):
            return
        self.push_undo(name=tr("app.by_snap_end"))
        for t in tgts:
            t.pop("by_snap_end", None)
            if how != SNAP_ENDS[0]:
                t["by_snap_end"] = how
        self.shapes_changed()

    def on_by_snap(self, left=False):
        """The step box (whole ticks, or a note length like 1/32 kept as one): every ticked line takes it."""
        e = self.by_snap_entry
        if self._loading or e.instate(["disabled"]) or left and unchanged(e):
            return
        name = snap_name(self.by_snap_var.get())
        try:
            ticks = self.step_ticks(self.by_snap_var.get())
            if not 1 <= ticks <= BY_SNAP_MOST * self.ppq:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        good(e)
        step = snap_name_beats(name) if name else math.floor(ticks + 0.5) / self.ppq
        tgts = [t for t in self.by_snap_targets() if t.get("by_snap")]
        if all(t["by_snap"] == step and t.get("by_snap_name") == name for t in tgts):
            return
        if not self.confirm_big([dict(t, by_snap=step) for t in tgts]):
            self.sync_by_snap()  # (too many notes, No: the box back)
            return
        self.begin_edit(("by_snap", tuple(sorted(self.sels))))
        for t in tgts:
            t["by_snap"] = step
            t.pop("by_snap_name", None)
            if name:
                t["by_snap_name"] = name
        if name:
            self.by_snap_var.set(name)  # (tidied: " 1 / 32" -> "1/32")
        self.shapes_changed()
