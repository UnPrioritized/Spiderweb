"""The side panel's Mask rows (mask.py): a selected shape made a mask or a shape again, and a mask's settings."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.mask import MASK_DEFAULTS, MOST_SIZE, SHOWS, THICKNESS, can_mask, is_solid, loops_of
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, show_mixed, unchanged

SHOW_CHOICES = [(v, tr("mask.show_" + v)) for v in SHOWS]


class MaskPanel:
    """Mixed into App."""

    def _build_mask(self):
        """[x] Mask; for masks under it: [x] Own notes sound, Shows [Inside / Along the line] (closed shapes),
        Thickness [n] keys, [x] Invert. Shown only with shapes selected (new shapes: the toolbar's Mask tick)."""
        box = self.mask_box = ttk.Frame(self.settings)
        self.mask_on = tk.BooleanVar(self, value=False)
        self.mask_mixed = {}  # tick -> some of the picked masks have it, some not (half ticked)
        b = self.mask_tick = ttk.Checkbutton(box, text=tr("mask.panel_tick"), variable=self.mask_on,
                                             command=lambda: self.on_mask("on"))
        b.pack(anchor="w")
        Tooltip(b, tr("mask.panel_tip"))
        rows = self.mask_rows = ttk.Frame(box)
        self.mask_own = tk.BooleanVar(self, value=True)
        b = self.mask_own_tick = ttk.Checkbutton(rows, text=tr("mask.own"), variable=self.mask_own,
                                                 command=lambda: self.on_mask("own"))
        b.pack(anchor="w")
        Tooltip(b, tr("mask.own_tip"))
        c = self.mask_show_row = ttk.Frame(rows)
        c.pack(anchor="w", pady=(1, 0))
        ttk.Label(c, text=tr("mask.show")).pack(side="left")
        self.mask_show_box = ttk.Combobox(c, values=[t for _, t in SHOW_CHOICES], state="readonly",
                                          width=max(len(t) for _, t in SHOW_CHOICES))
        self.mask_show_box.pack(side="left", padx=4)
        self.mask_show_box.bind("<<ComboboxSelected>>", lambda ev: (self.on_mask("show"), self.roll.focus_set()))
        self.mask_show_tip = Tooltip(self.mask_show_box, tr("mask.show_tip"))
        c = ttk.Frame(rows)
        c.pack(anchor="w", pady=(1, 0))
        lb = ttk.Label(c, text=tr("mask.thickness"))
        lb.pack(side="left")
        self.mask_thick_tip = Tooltip(lb, tr("mask.thickness_tip"))
        self.mask_size_var = tk.StringVar()
        e = self.mask_size_entry = ttk.Entry(c, textvariable=self.mask_size_var, width=5)
        e.pack(side="left", padx=4)
        Scrub(self, [(e, self.mask_size_var, lambda: self.on_mask("size"))], (0.5, 4, 0.1), 0, MOST_SIZE, label=lb,
              drag_box=True)
        ttk.Label(c, text=tr("mask.keys"), foreground=look.HINT).pack(side="left")
        e.bind("<Return>", lambda ev: self.on_mask("size"))
        leave_box(self, e, self.mask_size_var, lambda left, e=e: self.on_mask("size", left and e))
        self.mask_invert = tk.BooleanVar(self, value=False)
        b = self.mask_invert_tick = ttk.Checkbutton(rows, text=tr("mask.invert"), variable=self.mask_invert,
                                                    command=lambda: self.on_mask("invert"))
        b.pack(anchor="w", pady=(1, 0))
        Tooltip(b, tr("mask.invert_tip"))

    def mask_targets(self):
        return [self.shapes[i] for i in sorted(self.sels) if i < len(self.shapes) and can_mask(self.shapes[i])]

    def screen_k(self):
        """Beats per key as the piano roll shows them now (a new mask's proportions, mask.py)."""
        roll = self.roll
        return roll.sy / roll.sx if roll.sx and roll.sy else MASK_DEFAULTS["k"]

    def new_mask(self):
        """A new mask's settings: the last thickness set, the piano roll's proportions now."""
        return dict(self.mask_defaults, k=self.screen_k())

    def sync_mask(self):
        """Mask tick half ticked when some picked shapes are masks and some not; the rows under it only while every
        picked shape is a mask (my pick, like Colours' rows)."""
        tgts = self.mask_targets()
        shown = bool(tgts)
        if shown != bool(self.mask_box.winfo_manager()):
            if shown:
                self.mask_box.pack(fill="x", pady=(4, 0), after=self.colours_box)
            else:
                self.mask_box.pack_forget()
        if not shown:
            return
        masks = [t["mask"] for t in tgts if t.get("mask")]
        self._loading = True
        every = len(masks) == len(tgts)
        self.mask_on.set(every)
        self.mask_tick.state(["alternate"] if masks and not every else ["!alternate"])
        self.mask_mixed = {}
        for key, var, tick in (("own", self.mask_own, self.mask_own_tick),
                               ("invert", self.mask_invert, self.mask_invert_tick)):
            values = {bool(m[key]) for m in masks}
            self.mask_mixed[key] = len(values) > 1
            var.set(values == {True})
            tick.state(["alternate"] if len(values) > 1 else ["!alternate"])
        same_or_blank(self.mask_size_entry, self.mask_size_var, [fmt(m["size"]) for m in masks])
        # Shows: closed shapes that aren't Fill / Spam only (open lines are always a band, Fill / Spam their inside)
        closed = [t for t in tgts if t.get("mask") and not is_solid(t) and loops_of(t)]
        shows = {t["mask"].get("show", "inside") for t in closed}
        if len(shows) == 1:
            self.mask_show_box.current(SHOWS.index(next(iter(shows))))
        show_mixed(self.mask_show_box, len(shows) > 1, self.mask_show_tip, tr("mask.show_tip"))
        self._loading = False
        if bool(closed) != bool(self.mask_show_row.winfo_manager()):
            if closed:
                self.mask_show_row.pack(anchor="w", pady=(1, 0), after=self.mask_own_tick)
            else:
                self.mask_show_row.pack_forget()
        # (Fill / Spam masks show their whole inside: no thickness, user)
        band = [t for t in tgts if t.get("mask") and not is_solid(t)]
        self.mask_size_entry.config(state="normal" if band else "disabled")
        self.mask_thick_tip.text = tr("mask.thickness_tip") + ("" if band else "\n\n" + tr("mask.thickness_solid"))
        if every != bool(self.mask_rows.winfo_manager()):
            if every:
                self.mask_rows.pack(anchor="w", padx=(20, 0), pady=(1, 0))
            else:
                self.mask_rows.pack_forget()

    def on_mask(self, what, left=None):
        """The Mask tick (what = "on"), a tick under it, Shows or the Thickness box. Only what was changed goes to each
        mask (the rest stays its own)."""
        if self._loading or left is not None and unchanged(left):
            return
        tgts = self.mask_targets()
        if not tgts:
            return
        if what == "on":
            on = self.mask_on.get() or bool(self.mask_tick.instate(["alternate"]))  # (half ticked: a click = all on)
            if all(bool(t.get("mask")) == on for t in tgts):
                return self.sync_mask()
            self.push_undo(name=tr("mask.undo_on" if on else "mask.undo_off"))
            for t in tgts:
                if on and not t.get("mask"):
                    t["mask"] = self.new_mask()
                elif not on:
                    t.pop("mask", None)
            return self.mask_done()
        masks = [t["mask"] for t in tgts if t.get("mask")]
        if not masks:
            return
        if what in ("own", "invert"):
            var = self.mask_own if what == "own" else self.mask_invert
            value = True if self.mask_mixed.get(what) else var.get()  # (half ticked: a click = all on)
            new = [dict(m, **{what: value}) for m in masks]
        elif what == "show":
            if self.mask_show_box.current() < 0:  # (still Mixed: nothing picked)
                return
            value = SHOWS[self.mask_show_box.current()]
            new = [dict(m, show=value) for m in masks]
        else:
            e, var = self.mask_size_entry, self.mask_size_var
            if not var.get().strip() and getattr(e, "blank_from", None):  # (Mixed, still empty)
                return
            try:
                v = float(calc(var.get()))
                ok = 0 <= v <= MOST_SIZE
            except ValueError:
                ok = False
            if not ok:
                return bad(e)
            good(e)
            new = [dict(m, size=v) for m in masks]
        if new == masks:
            return self.sync_mask()
        self.push_undo(name=tr("mask.undo"))
        for t, m in zip([t for t in tgts if t.get("mask")], new):
            t["mask"] = m
        # (the next mask drawn starts with this thickness; always sounding, not inverted: user)
        self.mask_defaults = dict(MASK_DEFAULTS, **{k: new[-1][k] for k in THICKNESS})
        self.mask_done()

    def mask_done(self):
        self.shapes_changed()
        self.sync_title()
        self.sync_mask()
        self.schedule_autosave()
