"""The side panel's Mask rows (mask.py): a selected shape made a mask or a shape again, and a mask's settings."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc
from notes.mask import MASK_DEFAULTS, MOST_BEATS, MOST_KEYS, is_solid
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, unchanged


class MaskPanel:
    """Mixed into App."""

    def _build_mask(self):
        """[x] Mask; for masks under it: [x] Own notes sound, Thickness [keys] keys [x] Lock [ticks] ticks, [x] Invert.
        Shown only with shapes selected (new shapes: the toolbar's Mask tick)."""
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
        c = ttk.Frame(rows)
        c.pack(anchor="w", pady=(1, 0))
        lb = ttk.Label(c, text=tr("mask.thickness"))
        lb.pack(side="left")
        self.mask_thick_tip = Tooltip(lb, tr("mask.thickness_tip"))
        self.mask_vars = {"keys": tk.StringVar(), "ticks": tk.StringVar()}
        self.mask_entries = {}
        for what, unit in (("keys", "mask.keys"), ("ticks", "mask.ticks")):
            if what == "ticks":
                self.mask_lock = tk.BooleanVar(self, value=True)
                b = self.mask_lock_tick = ttk.Checkbutton(c, text=tr("mask.lock"), variable=self.mask_lock,
                                                          command=lambda: self.on_mask("lock"))
                b.pack(side="left", padx=(6, 2))
                Tooltip(b, tr("mask.lock_tip"))
            e = self.mask_entries[what] = ttk.Entry(c, textvariable=self.mask_vars[what], width=4)
            e.pack(side="left", padx=4)
            steps = (1, 4, 1) if what == "keys" else lambda: (max(1, self.ppq // 32), max(1, self.ppq // 8), 1)
            Scrub(self, [(e, self.mask_vars[what], lambda what=what: self.on_mask(what))], steps, 0,
                  MOST_KEYS if what == "keys" else None, label=lb if what == "keys" else None, drag_box=True)
            ttk.Label(c, text=tr(unit), foreground=look.HINT).pack(side="left")
            e.bind("<Return>", lambda ev, what=what: self.on_mask(what))
            leave_box(self, e, self.mask_vars[what], lambda left, e=e, what=what: self.on_mask(what, left and e))
        self.mask_invert = tk.BooleanVar(self, value=False)
        b = self.mask_invert_tick = ttk.Checkbutton(rows, text=tr("mask.invert"), variable=self.mask_invert,
                                                    command=lambda: self.on_mask("invert"))
        b.pack(anchor="w", pady=(1, 0))
        Tooltip(b, tr("mask.invert_tip"))

    def mask_targets(self):
        return [self.shapes[i] for i in sorted(self.sels) if i < len(self.shapes)]

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
                               ("invert", self.mask_invert, self.mask_invert_tick),
                               ("lock", self.mask_lock, self.mask_lock_tick)):
            values = {bool(m[key]) for m in masks}
            self.mask_mixed[key] = len(values) > 1
            var.set(values == {True})
            tick.state(["alternate"] if len(values) > 1 else ["!alternate"])
        same_or_blank(self.mask_entries["keys"], self.mask_vars["keys"], [m["keys"] for m in masks])
        same_or_blank(self.mask_entries["ticks"], self.mask_vars["ticks"],
                      [int(round(m["beats"] * self.ppq)) for m in masks])
        self._loading = False
        # (Fill / Spam masks show their whole inside: no thickness, user)
        band = [t for t in tgts if t.get("mask") and not is_solid(t)]
        for w in list(self.mask_entries.values()) + [self.mask_lock_tick]:
            w.config(state="normal" if band else "disabled")
        self.mask_thick_tip.text = tr("mask.thickness_tip") + ("" if band else "\n\n" + tr("mask.thickness_solid"))
        if every != bool(self.mask_rows.winfo_manager()):
            if every:
                self.mask_rows.pack(anchor="w", padx=(20, 0), pady=(1, 0))
            else:
                self.mask_rows.pack_forget()

    def on_mask(self, what, left=None):
        """The Mask tick (what = "on"), a tick under it or a thickness box ("keys" / "ticks"). Only what was changed
        goes to each mask (the rest stays its own). Locked: keys and ticks change together, in step (beats per key)."""
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
                    t["mask"] = dict(self.mask_defaults)
                elif not on:
                    t.pop("mask", None)
            return self.mask_done()
        masks = [t["mask"] for t in tgts if t.get("mask")]
        if not masks:
            return
        if what in ("own", "invert", "lock"):
            var = {"own": self.mask_own, "invert": self.mask_invert, "lock": self.mask_lock}[what]
            value = True if self.mask_mixed.get(what) else var.get()  # (half ticked: a click = all on)
            new = []
            for m in masks:
                if what == "lock":
                    # beats per key kept while locked (made from the mask's own numbers, else the usual)
                    v = (m["lock"] or (m["beats"] / m["keys"] if m["keys"] and m["beats"] else MASK_DEFAULTS["lock"])
                         if value else False)
                else:
                    v = value
                new.append(dict(m, **{what: v}))
        else:
            e, var = self.mask_entries[what], self.mask_vars[what]
            if not var.get().strip() and getattr(e, "blank_from", None):  # (Mixed, still empty)
                return
            try:
                v = calc(var.get())
                ok = v == int(v) and 0 <= v <= (MOST_KEYS if what == "keys" else MOST_BEATS * self.ppq)
            except ValueError:
                ok = False
            if not ok:
                return bad(e)
            good(e)
            v = int(v)
            new = []
            for m in masks:
                if what == "keys":
                    keys, beats = v, m["beats"]
                    if m["lock"]:
                        beats = min(MOST_BEATS, keys * m["lock"])
                else:
                    keys, beats = m["keys"], v / self.ppq
                    if m["lock"]:
                        keys = int(min(MOST_KEYS, round(beats / m["lock"])))
                new.append(dict(m, keys=keys, beats=beats))
        if new == masks:
            return self.sync_mask()
        self.push_undo(name=tr("mask.undo"))
        for t, m in zip([t for t in tgts if t.get("mask")], new):
            t["mask"] = m
        self.mask_defaults = dict(new[-1])  # (the next mask drawn starts with these)
        self.mask_done()

    def mask_done(self):
        self.shapes_changed()
        self.sync_title()
        self.sync_mask()
        self.schedule_autosave()
