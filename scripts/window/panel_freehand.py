"""The side panel's freehand setting: how much a freehand stroke is made perfect (smooth.py)."""

import tkinter as tk
from tkinter import ttk

from files.mathexpr import calc, fmt
from notes.custom import refit, uv_k
from window.widgets import Scrub, Tooltip

TIP = ("Makes the freehand stroke perfect: 0 = as you drew it. Higher = straighter lines and smoother curves,\n"
       "and a stroke that ends where it started becomes a perfect circle, ellipse, square, rectangle or triangle.\n"
       "The higher, the simpler (a slightly oval loop: an ellipse, then a circle). The stroke as drawn is kept,\n"
       "so you can change this any time. New freehand strokes use the last number picked.")


class FreehandPanel:
    """Mixed into App."""

    def _build_freehand(self):
        box = self.free_box = ttk.Frame(self.settings)
        lb = ttk.Label(box, text="Straighten")
        lb.pack(side="left")
        self.free_var = tk.StringVar()
        e = self.free_entry = ttk.Entry(box, textvariable=self.free_var, width=5)
        e.pack(side="left", padx=(5, 3))
        e.bind("<Return>", lambda ev: self.on_free_entry())
        e.bind("<FocusOut>", lambda ev: self.on_free_entry())
        Scrub(self, [(e, self.free_var, self.on_free_entry)], (1, 10, 1), 0, 100, label=lb)
        ttk.Label(box, text="0 = as drawn, 100 = simplest", foreground="#777").pack(side="left")
        for w in (lb, e):
            Tooltip(w, TIP)

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
        self._loading = False

    def on_free_entry(self):
        if self._loading:
            return
        try:
            value = calc(self.free_var.get())
            if not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            self.free_entry.config(style="Bad.TEntry")
            return
        self.free_entry.config(style="TEntry")
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
