"""The side panel's Colours row (custom.CYCLES): any shape's notes taking turns over channels."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc
from notes.custom import CYCLE_MAX, CYCLES
from window.widgets import Scrub, Tooltip, bad, good

CYCLE_CHOICES = [(None, tr("colours.off"))] + [(v, tr("colours." + v)) for v in CYCLES]
EVERY_MAX = 10 ** 4


class ColoursPanel:
    """Mixed into App."""

    def _build_colours(self):
        """Colours [Off / By step / By key / By time] channels [n]; under it every [n] steps / keys, or for By time
        every [a] / [b] note. Every number box can be dragged itself."""
        box = self.colours_box = ttk.Frame(self.settings)
        box.pack(fill="x", pady=(4, 0))
        c = ttk.Frame(box)
        c.pack(anchor="w")
        ttk.Label(c, text=tr("colours.colours")).pack(side="left")
        self.cycle_box = ttk.Combobox(c, values=[t for _, t in CYCLE_CHOICES], state="readonly", width=8)
        self.cycle_box.pack(side="left", padx=4)
        self.cycle_box.bind("<<ComboboxSelected>>", lambda ev: (self.on_cycle(), self.roll.focus_set()))
        self.cycle_tip = Tooltip(self.cycle_box, tr("colours.tip"))
        self.cycle_n_var = tk.StringVar()
        lb = self.cycle_n_label = ttk.Label(c, text=tr("colours.channels"))
        lb.pack(side="left")
        self.cycle_n_entry = ttk.Entry(c, textvariable=self.cycle_n_var, width=3)
        self.cycle_n_entry.pack(side="left", padx=(4, 0))
        Scrub(self, [(self.cycle_n_entry, self.cycle_n_var, self.on_cycle)], (1, 3, 1), 2, CYCLE_MAX, label=lb,
              drag_box=True)
        c = self.cycle_every_row = ttk.Frame(box)  # (channels + this row: only shown while Colours is on, user)
        c.pack(anchor="w", padx=(20, 0), pady=(1, 0))
        ttk.Label(c, text=tr("colours.every")).pack(side="left")
        self.cycle_vars = [tk.StringVar(), tk.StringVar()]  # steps / keys, or the note length's a / b
        self.cycle_entries = []
        for i, var in enumerate(self.cycle_vars):
            if i:
                self.cycle_slash = ttk.Label(c, text="/")
                self.cycle_slash.pack(side="left")
            e = ttk.Entry(c, textvariable=var, width=4)
            e.pack(side="left", padx=4)
            Scrub(self, [(e, var, self.on_cycle)], (1, 4, 1), 1, EVERY_MAX, drag_box=True)
            self.cycle_entries.append(e)
        self.cycle_unit = ttk.Label(c, text="")
        self.cycle_unit.pack(side="left")
        for e in [self.cycle_n_entry] + self.cycle_entries:
            e.bind("<Return>", lambda ev: self.on_cycle())
            e.bind("<FocusOut>", lambda ev: self.on_cycle())

    def colour_targets(self):
        """What the Colours row changes: the selected shapes (not pasted notes: they keep their tracks), or the
        settings for new shapes."""
        tgts = [t for t in self.targets() if "notes" not in t]
        return tgts if self.sels else [self.defaults]

    def sync_colours(self):
        tgts = self.colour_targets()
        cy = tgts[0].get("cycle") if tgts else None
        by = cy["by"] if cy else None
        every = cy["every"] if cy else None
        self._loading = True
        self.cycle_box.current(1 + CYCLES.index(by) if cy else 0)
        self.cycle_n_var.set(str(cy["n"]) if cy else "4")
        a, b = every if by == "time" else (every or 1, 4)
        self.cycle_vars[0].set(str(a))
        self.cycle_vars[1].set(str(b))
        self._loading = False
        time = by == "time"
        if time != bool(self.cycle_slash.winfo_manager()):  # By time: a second box, "a / b note"
            for w in (self.cycle_slash, self.cycle_entries[1], self.cycle_unit):
                w.pack_forget()
            if time:
                self.cycle_slash.pack(side="left")
                self.cycle_entries[1].pack(side="left", padx=4)
            self.cycle_unit.pack(side="left")
        self.cycle_unit.config(text=tr("colours.unit_" + (by or "step")))
        lonely = bool(cy) and self.channel_mode.get() != "auto"
        self.cycle_box.config(state="readonly" if tgts else "disabled",
                              style="Gap.TCombobox" if lonely else "TCombobox")
        self.cycle_tip.text = tr("colours.tip") + (tr("colours.needs") if lonely else "")
        for e in [self.cycle_n_entry] + self.cycle_entries:
            e.config(state="normal" if cy else "disabled", style="TEntry")
        if bool(cy) != bool(self.cycle_every_row.winfo_manager()):
            if cy:
                self.cycle_n_label.pack(side="left")
                self.cycle_n_entry.pack(side="left", padx=(4, 0))
                self.cycle_every_row.pack(anchor="w", padx=(20, 0), pady=(1, 0))
            else:
                for w in (self.cycle_n_label, self.cycle_n_entry, self.cycle_every_row):
                    w.pack_forget()

    def on_cycle(self):
        """The Colours dropdown, or one of its number boxes."""
        if self._loading or str(self.cycle_box.cget("state")) == "disabled":
            return
        by = CYCLE_CHOICES[max(self.cycle_box.current(), 0)][0]
        new = None
        if by:
            boxes = [(self.cycle_n_entry, self.cycle_n_var, 2, CYCLE_MAX)]
            boxes += [(e, v, 1, EVERY_MAX) for e, v in zip(self.cycle_entries, self.cycle_vars)][:2 if by == "time" else 1]
            got = []
            def number(var, lo, hi):
                try:
                    v = calc(var.get())
                    return int(v) if lo <= v <= hi and v == int(v) else None
                except ValueError:
                    return None
            for e, var, lo, hi in boxes:
                v = number(var, lo, hi)
                if v is None:  # (back to its last good value)
                    bad(e)
                    v = number(var, lo, hi)
                if v is not None:
                    good(e)
                got.append(v)
            if None in got:
                return
            new = {"by": by, "n": got[0], "every": got[1:] if by == "time" else got[1]}
        tgts = self.colour_targets()
        if all(t.get("cycle") == new for t in tgts):
            return self.sync_colours()
        if self.sels:
            self.push_undo(name=tr("colours.undo"))
        for t in tgts:
            if new:
                t["cycle"] = dict(new, every=list(new["every"]) if by == "time" else new["every"])
            else:
                t.pop("cycle", None)
        self.shapes_changed()
        self.sync_colours()
        self.schedule_autosave()
