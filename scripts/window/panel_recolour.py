"""The side panel's Recolour row, under Colours (notes/recolour.py): which colour each note of a shape has, its notes
kept where they are. Shares… opens window/shares_window.py."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import ROUND
from notes.engine import pasted_notes
from notes.recolour import BY, KEEP, MODES, SET, can_recolour, clean_recolour, colour_sets, enough, new_seed, palettes, \
    wanted, without_recolour
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, show_mixed, unchanged

RECOLOUR_CHOICES = [(None, tr("recolour.off"))] + [(m, tr("recolour." + m)) for m in MODES]
BY_CHOICES = [(b, tr("recolour.by_" + b)) for b in BY]
AMOUNT_WORDS = {"shuffle": ("recolour.amount", "recolour.amount_tip"),
                "scatter": ("recolour.amount", "recolour.amount_tip_scatter"),
                "clumps": ("recolour.rough", "recolour.amount_tip_clumps")}
START_AMOUNT = {"shuffle": 100.0, "scatter": 50.0, "clumps": 20.0}


class RecolourPanel:
    """Mixed into App."""

    def _build_recolour(self):
        """Recolour [Off / Shuffle] [New shuffle]; under it, while on: Amount [n] %, [Per note] [Shares…], and
        Mix across sets while a shape has more than one colour set."""
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
        self.recolour_stir = ttk.Button(c, text=tr("stir.button"), command=self.open_stir)
        self.recolour_stir.pack(side="left", padx=(4, 0))
        Tooltip(self.recolour_stir, tr("stir.button_tip"))
        rows = self.recolour_row = ttk.Frame(box)  # (only shown while Recolour is on)
        row = ttk.Frame(rows)
        row.pack(anchor="w")
        lb = ttk.Label(row, text=tr("recolour.amount"))
        lb.pack(side="left")
        self.recolour_var = tk.StringVar()
        e = self.recolour_entry = ttk.Entry(row, textvariable=self.recolour_var, width=7)
        e.pack(side="left", padx=4)
        Scrub(self, [(e, self.recolour_var, lambda: self.on_recolour("amount"))], (1, 10, 0.1), 0, 100, label=lb,
              drag_box=True)
        ttk.Label(row, text="%").pack(side="left")
        self.recolour_amount_word = lb
        self.recolour_amount_tip = Tooltip(lb, tr("recolour.amount_tip"))
        e.bind("<Return>", lambda ev: self.on_recolour("amount"))
        leave_box(self, e, self.recolour_var, lambda left: self.on_recolour("amount", left and e))
        row = ttk.Frame(rows)
        row.pack(anchor="w", pady=(2, 0))
        self.recolour_by = ttk.Combobox(row, values=[t for _, t in BY_CHOICES], state="readonly",
                                        width=max(len(t) for _, t in BY_CHOICES))
        self.recolour_by.pack(side="left")
        self.recolour_by.bind("<<ComboboxSelected>>", lambda ev: (self.on_recolour("by"), self.roll.focus_set()))
        self.recolour_by_tip = Tooltip(self.recolour_by, tr("recolour.by_tip"))
        b = self.recolour_shares = ttk.Button(row, text=tr("recolour.shares"), command=self.open_shares)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("recolour.shares_tip"))
        self.recolour_mix = tk.BooleanVar()
        self.recolour_mix_tick = ttk.Checkbutton(rows, text=tr("recolour.mix"), variable=self.recolour_mix,
                                                 command=lambda: self.on_recolour("mix"))
        self.recolour_mix_tip = Tooltip(self.recolour_mix_tick, tr("recolour.mix_tip", most=SET))

    def open_shares(self):
        from window.shares_window import open_shares
        open_shares(self)

    def open_stir(self):
        from window.stir_window import open_stir
        open_stir(self)

    def recolour_targets(self):
        """The selected shapes Recolour works on (not a Hz bass); nothing selected: none (no setting for new
        shapes)."""
        return [self.shapes[i] for i in sorted(self.sels) if can_recolour(self.shapes[i])]

    def set_most(self):
        """How many colours a colour set holds: 15, 16 while Multi channel uses channel 10."""
        return 16 if self.all16() else 15

    def colour_owns(self, sh):
        """Each colour set's own colours of sh's notes before Recolour (the notes as remembered: no new work after a
        change)."""
        notes, tracks = self.notes_tracks(without_recolour(sh))
        if tracks is None:  # (all on one colour)
            return [np.zeros(1, np.int64)] if len(notes) else []
        if not len(tracks):
            return []
        return colour_sets(np.asarray(tracks, np.int64) % ROUND)[1]

    def colour_count(self, sh):
        """How many colours sh's notes have before Recolour (the most in one set)."""
        return max((len(o) for o in self.colour_owns(sh)), default=1)

    def sync_recolour(self):
        tgts = self.recolour_targets()
        act = [clean_recolour(t.get("recolour")) for t in tgts]
        stir_on = any(r and r.get("stir") for r in act)
        on = [r if r and r["mode"] != KEEP else None for r in act]  # (Off with only a Stir: shown as Off)
        modes = {r["mode"] if r else None for r in on}
        live = [r for r in on if r]
        self._loading = True
        mode = next(iter(modes)) if len(modes) == 1 else None
        self.recolour_mode.current([v for v, _ in RECOLOUR_CHOICES].index(mode))
        same_or_blank(self.recolour_entry, self.recolour_var, [fmt(r["amount"]) for r in live] or ["100"])
        bys = {r.get("by", "note") for r in live} or {"note"}
        if len(bys) == 1:
            self.recolour_by.current(BY.index(next(iter(bys))))
        show_mixed(self.recolour_by, len(bys) > 1, self.recolour_by_tip, tr("recolour.by_tip"))
        mixes = {bool(r.get("mix")) for r in live}
        self.recolour_mix.set(mixes == {True})
        self.recolour_mix_tick.state(["alternate" if len(mixes) > 1 else "!alternate"])
        self._loading = False
        # (Off + a Stir counts too: it says why the stir shows nothing, hunt 2026-11-02)
        lonely, single = self.recolour_trouble(tgts, act)
        owns = [self.colour_owns(t) if r else [] for t, r in zip(tgts, on)]
        self.recolour_mode.config(state="readonly" if tgts else "disabled",
                                  style="Gap.TCombobox" if lonely or single else "TCombobox")
        show_mixed(self.recolour_mode, len(modes) > 1, self.recolour_tip,
                   tr("recolour.tip") + (tr("recolour.needs") if lonely else tr("recolour.one") if single else ""))
        self.recolour_entry.config(state="normal" if live else "disabled", style="TEntry")
        kind = mode if mode in AMOUNT_WORDS else "shuffle"  # (Mixed: the plain Amount)
        self.recolour_amount_word.config(text=tr(AMOUNT_WORDS[kind][0]))
        self.recolour_amount_tip.text = tr(AMOUNT_WORDS[kind][1])
        self.recolour_stir.config(text=tr("stir.button_on" if stir_on else "stir.button"))
        self.recolour_stir.state(["!disabled" if tgts else "disabled"])
        shown = bool(live)
        if shown != bool(self.recolour_row.winfo_manager()):
            if shown:
                self.recolour_row.pack(anchor="w", padx=(self.cycle_rows_pad, 0), pady=(1, 0))
            else:
                self.recolour_row.pack_forget()
        if shown != bool(self.recolour_again.winfo_manager()):
            if shown:
                self.recolour_again.pack(side="left", before=self.recolour_stir)
            else:
                self.recolour_again.pack_forget()
        sets = shown and any(len(o) > 1 for o in owns)  # (more than one colour set: Mix across sets)
        if sets != bool(self.recolour_mix_tick.winfo_manager()):
            if sets:
                self.recolour_mix_tick.pack(anchor="w", pady=(2, 0))
            else:
                self.recolour_mix_tick.pack_forget()
        self.recolour_mix_tip.text = tr("recolour.mix_tip", most=SET)
        for w in (getattr(self, "shares_window", None), getattr(self, "stir_window", None)):
            if w:
                w.sync()

    def recolour_trouble(self, tgts, rcs):
        """(lonely, single) for shapes tgts with Recolours rcs (clean; None = Off): lonely = Channels isn't Multi
        channel, so a Recolour on (a Domino paste works in every mode, hunt 2026-11-01) changes nothing; single =
        one has nothing to swap (e.g. one colour, hunt 2026-10-25). Stir window: rcs = what a stir would make."""
        lonely = any(rcs) and self.channel_mode.get() != "auto" and not all(
            pasted_notes(t) for t, r in zip(tgts, rcs) if r)
        single = not lonely and any(
            r and not enough(palettes(self.colour_owns(t), None if r["mode"] == KEEP else wanted(r), self.set_most()),
                             r.get("mix") and r["mode"] != KEEP) for t, r in zip(tgts, rcs))
        return lonely, single

    def on_recolour(self, what, left=None):
        """The Recolour dropdown (what = "mode"), the Amount box ("amount"), New shuffle ("seed"), Per note / chord /
        run ("by") or Mix across sets ("mix"). Only what was changed goes to each shape (several picked keep their
        own amounts). left: the box that was left, nothing unless its number changed (leave_box)."""
        if self._loading or str(self.recolour_mode.cget("state")) == "disabled" or (
                left is not None and unchanged(left)):
            return
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
        elif what == "by":
            i = self.recolour_by.current()
            if i < 0:
                return
            by = BY[i]
        elif what == "mix":
            self.recolour_mix_tick.state(["!alternate"])
            mix = self.recolour_mix.get()

        def changed(old, _sh=None):
            if what == "mode":
                if mode is None:  # (Off: a Stir stays, on the colours as they are)
                    return dict(old, mode=KEEP) if old and old.get("stir") else None
                if old and old["mode"] == mode:
                    return old
                # (another mode: its own Amount to start from, the word means another thing there)
                return dict(old or {"seed": new_seed()}, mode=mode, amount=START_AMOUNT[mode])
            if not old or old["mode"] == KEEP:
                return old
            if what == "amount":
                return dict(old, amount=amount)
            if what == "by":
                return dict({k: v for k, v in old.items() if k != "by"}, **({"by": by} if by != "note" else {}))
            if what == "mix":
                return dict({k: v for k, v in old.items() if k != "mix"}, **({"mix": True} if mix else {}))
            return dict(old, seed=new_seed())

        if self.change_recolour(changed, tr("recolour.undo_again" if what == "seed" else "recolour.undo")) and \
                what == "mode" and mode is not None:
            self.tips.show("recolour", wait=True)  # (the first time: what it does)

    def change_recolour(self, changed, name, push=True):
        """Each selected shape's Recolour = changed(its old one, clean; the shape); one undo step named name (push
        False: part of the step taken before). False when nothing changed."""
        tgts = self.recolour_targets()
        olds = [clean_recolour(t.get("recolour")) for t in tgts]
        new = []
        for o, t in zip(olds, tgts):
            c = changed(o, t)
            new.append(clean_recolour(c) if c else None)
        if all(o == n for o, n in zip(olds, new)):
            self.sync_recolour()
            return False
        if push:
            self.push_undo(name=name)
        for t, r in zip(tgts, new):
            if r:
                t["recolour"] = r
            else:
                t.pop("recolour", None)
        self.shapes_changed()
        self.sync_recolour()
        self.schedule_autosave()
        return True
