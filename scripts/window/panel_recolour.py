"""The side panel's Recolour row, under Colours (notes/recolour.py): which colour each note of a shape has, its notes
kept where they are."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import ROUND
from notes.recolour import MODES, can_recolour, clean_recolour, new_seed, without_recolour
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, show_mixed, unchanged

RECOLOUR_CHOICES = [(None, tr("recolour.off"))] + [(m, tr("recolour." + m)) for m in MODES]


class RecolourPanel:
    """Mixed into App."""

    def _build_recolour(self):
        """Recolour [Off / Shuffle] [New shuffle]; under it, while on: Amount [n] %."""
        box = self.recolour_box = ttk.Frame(self.settings)
        box.pack(fill="x", pady=(4, 0), after=self.colours_box)
        c = ttk.Frame(box)
        c.pack(anchor="w")
        self.recolour_word = ttk.Label(c, text=tr("recolour.recolour"))  # (one column: app._build_shape_settings)
        self.recolour_word.pack(side="left")
        self.recolour_mode = ttk.Combobox(c, values=[t for _, t in RECOLOUR_CHOICES], state="readonly",
                                          width=max(len(t) for _, t in RECOLOUR_CHOICES))
        self.recolour_mode.pack(side="left", padx=(5, 4))
        self.recolour_mode.bind("<<ComboboxSelected>>", lambda ev: (self.on_recolour("mode"), self.roll.focus_set()))
        self.recolour_tip = Tooltip(self.recolour_mode, tr("recolour.tip"))
        self.recolour_again = ttk.Button(c, text=tr("recolour.again"), command=lambda: self.on_recolour("seed"))
        Tooltip(self.recolour_again, tr("recolour.again_tip"))
        row = self.recolour_row = ttk.Frame(box)  # (only shown while Recolour is on)
        lb = ttk.Label(row, text=tr("recolour.amount"))
        lb.pack(side="left")
        self.recolour_var = tk.StringVar()
        e = self.recolour_entry = ttk.Entry(row, textvariable=self.recolour_var, width=7)
        e.pack(side="left", padx=4)
        Scrub(self, [(e, self.recolour_var, lambda: self.on_recolour("amount"))], (1, 10, 0.1), 0, 100, label=lb,
              drag_box=True)
        ttk.Label(row, text="%").pack(side="left")
        Tooltip(lb, tr("recolour.amount_tip"))
        e.bind("<Return>", lambda ev: self.on_recolour("amount"))
        leave_box(self, e, self.recolour_var, lambda left: self.on_recolour("amount", left and e))

    def recolour_targets(self):
        """The selected shapes Recolour works on (not a Hz bass); nothing selected: none (no setting for new
        shapes)."""
        return [self.shapes[i] for i in sorted(self.sels) if can_recolour(self.shapes[i])]

    def colour_count(self, sh):
        """How many colours sh's notes have before Recolour (its notes as remembered: no new work after a change)."""
        _, tracks = self.notes_tracks(without_recolour(sh))
        return 1 if tracks is None or not len(tracks) else len(np.unique(np.asarray(tracks, np.int64) % ROUND))

    def sync_recolour(self):
        tgts = self.recolour_targets()
        on = [clean_recolour(t.get("recolour")) for t in tgts]
        modes = {r["mode"] if r else None for r in on}
        live = [r for r in on if r]
        self._loading = True
        mode = next(iter(modes)) if len(modes) == 1 else None
        self.recolour_mode.current([v for v, _ in RECOLOUR_CHOICES].index(mode))
        same_or_blank(self.recolour_entry, self.recolour_var, [fmt(r["amount"]) for r in live] or ["100"])
        self._loading = False
        lonely = bool(live) and self.channel_mode.get() != "auto"
        # (a shape with one colour: Shuffle has nothing to swap, hunt 2026-10-25)
        single = not lonely and any(r and self.colour_count(t) < 2 for t, r in zip(tgts, on))
        self.recolour_mode.config(state="readonly" if tgts else "disabled",
                                  style="Gap.TCombobox" if lonely or single else "TCombobox")
        show_mixed(self.recolour_mode, len(modes) > 1, self.recolour_tip,
                   tr("recolour.tip") + (tr("recolour.needs") if lonely else tr("recolour.one") if single else ""))
        self.recolour_entry.config(state="normal" if live else "disabled", style="TEntry")
        shown = bool(live)
        if shown != bool(self.recolour_row.winfo_manager()):
            if shown:
                self.recolour_row.pack(anchor="w", padx=(self.cycle_rows_pad, 0), pady=(1, 0))
            else:
                self.recolour_row.pack_forget()
        if shown != bool(self.recolour_again.winfo_manager()):
            if shown:
                self.recolour_again.pack(side="left")
            else:
                self.recolour_again.pack_forget()

    def on_recolour(self, what, left=None):
        """The Recolour dropdown (what = "mode"), the Amount box ("amount") or New shuffle ("seed"). Only what was
        changed goes to each shape (several picked keep their own amounts). left: the box that was left, nothing
        unless its number changed (leave_box)."""
        if self._loading or str(self.recolour_mode.cget("state")) == "disabled" or (
                left is not None and unchanged(left)):
            return
        tgts = self.recolour_targets()
        if what == "mode":
            i = self.recolour_mode.current()
            if i < 0:  # (still Mixed: nothing picked)
                return
            mode = RECOLOUR_CHOICES[i][0]
        elif what == "amount":
            e, var = self.recolour_entry, self.recolour_var
            if not var.get().strip() and getattr(e, "blank_from", None):
                return
            try:
                amount = calc(var.get().strip().rstrip("%"))
                ok = 0 <= amount <= 100
            except ValueError:
                ok = False
            if not ok:
                return bad(e)
            good(e)
            amount = round(amount, 3)

        def changed(old):
            if what == "mode":
                if mode is None:
                    return None
                return dict(old, mode=mode) if old else {"mode": mode, "amount": 100.0, "seed": new_seed()}
            if not old:
                return old
            if what == "amount":
                return dict(old, amount=amount)
            return dict(old, seed=new_seed())

        olds = [clean_recolour(t.get("recolour")) for t in tgts]
        new = [changed(o) for o in olds]
        if all(o == n for o, n in zip(olds, new)):
            return self.sync_recolour()
        self.push_undo(name=tr("recolour.undo_again" if what == "seed" else "recolour.undo"))
        for t, r in zip(tgts, new):
            if r:
                t["recolour"] = r
            else:
                t.pop("recolour", None)
        self.shapes_changed()
        self.sync_recolour()
        self.schedule_autosave()
