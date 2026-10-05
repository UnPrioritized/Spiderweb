"""The side panel's Colours row (custom.CYCLES): any shape's notes taking turns over channels."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc
from notes.custom import CYCLE_MAX, CYCLES
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, show_varies, unchanged, varies_tip

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
        self.cycle_box.bind("<<ComboboxSelected>>", lambda ev: (self.on_cycle("by"), self.roll.focus_set()))
        self.cycle_tip = Tooltip(self.cycle_box, tr("colours.tip"))
        self.cycle_n_var = tk.StringVar()
        lb = self.cycle_n_label = ttk.Label(c, text=tr("colours.channels"))
        lb.pack(side="left")
        self.cycle_n_entry = ttk.Entry(c, textvariable=self.cycle_n_var, width=3)
        self.cycle_n_entry.pack(side="left", padx=(4, 0))
        Scrub(self, [(self.cycle_n_entry, self.cycle_n_var, lambda: self.on_cycle("n"))], (1, 3, 1), 2, CYCLE_MAX,
              label=lb, drag_box=True)
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
            Scrub(self, [(e, var, lambda: self.on_cycle("every"))], (1, 4, 1), 1, EVERY_MAX, drag_box=True)
            self.cycle_entries.append(e)
        self.cycle_unit = ttk.Label(c, text="")
        self.cycle_unit.pack(side="left")
        for e, var, what in ([(self.cycle_n_entry, self.cycle_n_var, "n")] +
                             [(e, v, "every") for e, v in zip(self.cycle_entries, self.cycle_vars)]):
            e.bind("<Return>", lambda ev, what=what: self.on_cycle(what))
            leave_box(self, e, var, lambda left, e=e, what=what: self.on_cycle(what, left and e))

    def colour_targets(self):
        """What the Colours row changes: the selected shapes (not pasted notes: they keep their tracks), or the
        settings for new shapes."""
        tgts = [t for t in self.targets() if "notes" not in t]
        return tgts if self.sels else [self.defaults]

    def cycle_kind(self, tgts):
        """The one kind (By step / key / time) every target with Colours on has, or None (none on, or different)."""
        kinds = {t["cycle"]["by"] for t in tgts if t.get("cycle")}
        return kinds.pop() if len(kinds) == 1 else None

    def sync_colours(self):
        """Several shapes with different settings: the dropdown says Varies, a number box is left empty (shared
        widgets.same_or_blank). The every row only works when all the ones with Colours count the same way."""
        tgts = self.colour_targets()
        on = [t["cycle"] for t in tgts if t.get("cycle")]
        cy = on[0] if on else None
        by = self.cycle_kind(tgts)
        self._loading = True
        self.cycle_box.current(1 + CYCLES.index(cy["by"]) if cy else 0)
        same_or_blank(self.cycle_n_entry, self.cycle_n_var, [c["n"] for c in on] or [4])
        every = [c["every"] for c in on if c["by"] == by] or ([] if on else [1])
        parts = [[x[0] for x in every], [x[1] for x in every]] if by == "time" else [every, [4]]
        for e, var, values in zip(self.cycle_entries, self.cycle_vars, parts):
            same_or_blank(e, var, values)
            if on and not by:
                varies_tip(e).text = tr("colours.every_varies")
        self._loading = False
        time = by == "time"
        if time != bool(self.cycle_slash.winfo_manager()):  # By time: a second box, "a / b note"
            for w in (self.cycle_slash, self.cycle_entries[1], self.cycle_unit):
                w.pack_forget()
            if time:
                self.cycle_slash.pack(side="left")
                self.cycle_entries[1].pack(side="left", padx=4)
            self.cycle_unit.pack(side="left")
        self.cycle_unit.config(text=tr("colours.unit_" + (by or "step")) if by or not on else "")
        lonely = bool(cy) and self.channel_mode.get() != "auto"
        self.cycle_box.config(state="readonly" if tgts else "disabled",
                              style="Gap.TCombobox" if lonely else "TCombobox")
        kinds = {t["cycle"]["by"] if t.get("cycle") else None for t in tgts}  # (Off too)
        show_varies(self.cycle_box, len(kinds) > 1, self.cycle_tip,
                    tr("colours.tip") + (tr("colours.needs") if lonely else ""))
        for e in [self.cycle_n_entry] + self.cycle_entries:
            e.config(state="normal" if cy and (by or e is self.cycle_n_entry) else "disabled", style="TEntry")
        if bool(cy) != bool(self.cycle_every_row.winfo_manager()):
            if cy:
                self.cycle_n_label.pack(side="left")
                self.cycle_n_entry.pack(side="left", padx=(4, 0))
                self.cycle_every_row.pack(anchor="w", padx=(20, 0), pady=(1, 0))
            else:
                for w in (self.cycle_n_label, self.cycle_n_entry, self.cycle_every_row):
                    w.pack_forget()

    def on_cycle(self, what, left=None):
        """The Colours dropdown (what = "by") or one of its number boxes ("n" = channels, "every"). Only what was
        changed goes to each target: several picked shapes keep the rest of their own (user: they all got the first
        one's). A new kind starts at every 1 (By time: 1 / 4 note), not the old kind's number (user). left: the box
        that was left (or the selection is about to change, App.commit_typing): nothing unless its number changed."""
        if (self._loading or str(self.cycle_box.cget("state")) == "disabled"
                or left is not None and unchanged(left)):
            return
        by = CYCLE_CHOICES[max(self.cycle_box.current(), 0)][0]

        def number(e, var, lo, hi):  # (a wrong one: back to its last good value; still empty (Varies): None)
            if not var.get().strip() and getattr(e, "blank_from", None):
                return None
            for _ in range(2):
                try:
                    v = calc(var.get())
                    if lo <= v <= hi and v == int(v):
                        good(e)
                        return int(v)
                except ValueError:
                    pass
                bad(e)
            return None

        tgts = self.colour_targets()
        if what == "n":
            value = number(self.cycle_n_entry, self.cycle_n_var, 2, CYCLE_MAX)
        elif what == "every":  # (only the kind they all have; an empty box (different numbers): each keeps its own)
            kind = self.cycle_kind(tgts)
            got = [number(e, v, 1, EVERY_MAX) for e, v in zip(self.cycle_entries, self.cycle_vars)]
            value = None if not kind else got if kind == "time" and got != [None, None] else got[0]
        else:
            value = by
            on = [t["cycle"]["n"] for t in tgts if t.get("cycle")]
            n = self.cycle_n_var.get() or str(on[0] if on else 4)
        if what != "by" and value is None:
            return

        def changed(old):
            if what == "by":
                if not by:
                    return None
                if old and old["by"] == by:
                    return old
                kept = old["n"] if old else (int(n) if n.isdigit() and 2 <= int(n) <= CYCLE_MAX else 4)
                return {"by": by, "n": kept, "every": [1, 4] if by == "time" else 1}
            if not old:
                return old
            if isinstance(value, list):
                return dict(old, every=[v if v is not None else o for v, o in zip(value, old["every"])])
            return dict(old, **{what: value})

        new = [changed(t.get("cycle")) for t in tgts]
        if all(t.get("cycle") == c for t, c in zip(tgts, new)):
            return self.sync_colours()
        if self.sels:
            self.push_undo(name=tr("colours.undo"))
        for t, c in zip(tgts, new):
            if c:
                t["cycle"] = dict(c, every=list(c["every"]) if c["by"] == "time" else c["every"])
            else:
                t.pop("cycle", None)
        self.shapes_changed()
        self.sync_colours()
        self.schedule_autosave()
