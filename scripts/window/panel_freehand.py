"""The side panel's freehand setting: how much a freehand stroke is made perfect (smooth.py)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import refit, uv_k
from notes.pen import THICK_MAX, THICK_MIN, clean_thick
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, unchanged, unit_word

TIP = tr("panel_freehand.makes_the_freehand_stroke_perfect_0")
THICK_TIP = tr("panel_freehand.thickness_tip")


class FreehandPanel:
    """Mixed into App."""

    def _build_freehand(self):
        self.free_box = ttk.Frame(self.settings)
        box = ttk.Frame(self.free_box)
        box.pack(side="top", anchor="w")
        lb = ttk.Label(box, text=tr("panel_freehand.straighten"))
        lb.pack(side="left")
        self.free_var = tk.StringVar()
        e = self.free_entry = ttk.Entry(box, textvariable=self.free_var, width=5)
        e.pack(side="left", padx=(5, 3))
        e.bind("<Return>", lambda ev: self.on_free_entry())
        leave_box(self, e, self.free_var, self.on_free_entry)
        Scrub(self, [(e, self.free_var, self.on_free_entry)], (1, 10, 1), 0, 100, label=lb)
        ttk.Label(box, text=tr("panel_freehand.0_as_drawn_100_simplest"), foreground=look.HINT).pack(side="left")
        for w in (lb, e):
            Tooltip(w, TIP)
        # Thickness: a wide pen (notes/pen.py); the stroke itself stays the thin line (user)
        row = ttk.Frame(self.free_box)
        row.pack(side="top", anchor="w", pady=(4, 0))
        lb = ttk.Label(row, text=tr("panel_freehand.thickness"))
        lb.pack(side="left")
        self.thick_var = tk.StringVar()
        e = self.thick_entry = ttk.Entry(row, textvariable=self.thick_var, width=5)
        e.pack(side="left", padx=(5, 3))
        e.bind("<Return>", lambda ev: self.on_thick_entry())
        leave_box(self, e, self.thick_var, self.on_thick_entry)
        Scrub(self, [(e, self.thick_var, self.on_thick_entry)], (1, 5, 0.25), THICK_MIN, THICK_MAX, label=lb)
        unit_word(ttk.Label(row, foreground=look.HINT), self.thick_var, "unit.keys", "unit.key").pack(side="left")
        for w in (lb, e):
            Tooltip(w, THICK_TIP)

    def free_targets(self):
        """What the setting changes: [(the dict holding "smooth", its custom shape or None)] — the selected
        freehand shapes, or the picked freehand stroke of the selected custom shape."""
        shapes = [(self.shapes[i], None) for i in sorted(self.sels) if self.shapes[i]["kind"] == "free"]
        if shapes:
            return shapes
        sh = self.selected()
        k = self.roll.picked_stroke(sh) if sh and len(self.sels) == 1 else None
        return [(sh["strokes"][k], sh)] if k is not None and sh["strokes"][k].get("free") else []

    def sync_freehand(self):
        tgts = self.free_targets()
        # nothing selected with the Freehand tool: the number new strokes get
        self._rows["free"] = bool(tgts) or (not self.sels and self.tool.get() == "free")
        self.layout_rows()
        if not self._rows["free"]:
            return
        self._loading = True
        self.free_var.set(fmt(tgts[0][0].get("smooth", 0) if tgts else self.free_smooth))
        self.free_entry.config(style="TEntry")
        # Thickness: freehand shapes and freehand strokes inside a live shape (user 2026-11-19)
        self.thick_var.set(fmt(clean_thick(tgts[0][0].get("thick", THICK_MIN)) if tgts else self.free_thick))
        self.thick_entry.config(style="TEntry")
        self._loading = False

    def on_thick_entry(self, left=False):
        """The Thickness box (Enter, stepped, or left: widgets.leave_box)."""
        if self._loading or left and unchanged(self.thick_entry):
            return
        try:
            value = calc(self.thick_var.get())
            if not THICK_MIN <= value <= THICK_MAX:
                raise ValueError
        except ValueError:
            bad(self.thick_entry)
            return
        good(self.thick_entry)
        value = clean_thick(value)
        self.free_thick = value  # new strokes get it too
        tgts = [(d, owner) for d, owner in self.free_targets() if clean_thick(d.get("thick", THICK_MIN)) != value]
        if not tgts:
            return self.sync_freehand()
        self.begin_edit(("thick", tuple(sorted(self.sels)), self.stroke))
        k = self.roll.sy / self.roll.sx if self.roll.sx else 0.25  # (round as the roll looks now)
        for d, owner in tgts:
            if value > THICK_MIN:
                d["thick"] = value
                if "k" not in d:  # (one already drawn keeps its own: the pen round as it was drawn)
                    d["k"] = k if owner is None else uv_k(owner["pts"], k)
            else:
                d.pop("thick", None)
        self.shapes_changed()
        self.sync_freehand()
        self.sync_by_snap()  # (Notes by snap / Last note greyed while thick)

    def on_free_entry(self, left=False):
        """The Straighten box (Enter, stepped, or left: widgets.leave_box)."""
        if self._loading or left and unchanged(self.free_entry):
            return
        try:
            value = calc(self.free_var.get())
            if not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            bad(self.free_entry)
            return
        good(self.free_entry)
        value = int(round(value))
        self.free_smooth = value  # new strokes get it too
        tgts = [(d, owner) for d, owner in self.free_targets() if d.get("smooth", 0) != value]
        if not tgts:
            return self.sync_freehand()
        self.begin_edit(("smooth", tuple(sorted(self.sels)), self.stroke))
        k = self.roll.sy / self.roll.sx if self.roll.sx else 0.25  # worked out as the roll looks now
        for d, owner in tgts:
            d["smooth"] = value
            d["k"] = k if owner is None else uv_k(owner["pts"], k)
        for owner in {id(o): o for _, o in tgts if o is not None}.values():
            refit(owner)  # the stroke's new outline may reach further
        self.shapes_changed()
        self.sync_freehand()
