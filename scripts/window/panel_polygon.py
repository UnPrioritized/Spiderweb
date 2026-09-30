"""The side panel's polygon settings (polygon.py): how many points, the kind (polygon / star / crossing star), a
star's inner size and a crossing star's skip. They change the selected polygons, and are what the next polygon drawn
gets. The shape of the sides and the pattern along them are formulas (right-click ▸ Formula: formula_host.py's
PolygonHost; their numbers show in the formula part of the panel)."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.polygon import MAX_POINTS, STYLES, update_polygon
from window.widgets import Scrub, Tooltip

STYLE_NAMES = [tr("panel_polygon.style_" + s) for s in STYLES]
# name: (lowest, highest, steps (step, Shift step, Ctrl step), whole numbers only)
NUMBERS = {"points": (3, MAX_POINTS, (1, 5, 1), True), "inner": (0, 1000, (5, 25, 1), False),
           "skip": (1, MAX_POINTS - 1, (1, 5, 1), True)}


class PolygonPanel:
    """Mixed into App."""

    def _build_polygon(self):
        box = self.polygon_box = ttk.Frame(self.custom_box)  # (packed at the top of the custom part when shown)
        self.polygon_vars, self.polygon_entries = {}, {}
        row1, row2 = ttk.Frame(box), ttk.Frame(box)
        row1.pack(fill="x")
        row2.pack(fill="x", pady=(2, 0))
        for row, name in ((row1, "points"), (row2, "inner"), (row2, "skip")):
            cell = ttk.Frame(row)
            cell.pack(side="left", padx=(0, 10))
            lb = ttk.Label(cell, text=tr("panel_polygon." + name))
            lb.pack(side="left")
            var = self.polygon_vars[name] = tk.StringVar()
            e = self.polygon_entries[name] = ttk.Entry(cell, textvariable=var, width=5)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, n=name: self.on_polygon_number(n))
            e.bind("<FocusOut>", lambda ev, n=name: self.on_polygon_number(n))
            lo, hi, steps, _ = NUMBERS[name]
            Scrub(self, [(e, var, lambda n=name: self.on_polygon_number(n))], steps, lo, hi, label=lb)
            for w in (lb, e):
                Tooltip(w, tr("panel_polygon." + name + "_tip"))
            if name == "points":
                ttk.Label(row1, text=tr("panel_polygon.kind")).pack(side="left")
                self.polygon_style = tk.StringVar()
                cb = self.polygon_style_box = ttk.Combobox(row1, textvariable=self.polygon_style, values=STYLE_NAMES,
                                                           state="readonly", width=max(map(len, STYLE_NAMES)))
                cb.pack(side="left", padx=(4, 0))
                cb.bind("<<ComboboxSelected>>", lambda ev: (self.on_polygon_style(), self.roll.focus_set()))
                Tooltip(cb, tr("panel_polygon.kind_tip"))

    def polygon_shapes(self):
        """The selected polygons (custom shapes made by the Polygon tool)."""
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i].get("polygon")]

    def polygon_targets(self):
        """What the polygon settings change: the selected polygons' settings, or (Polygon tool, nothing selected)
        the settings for new ones."""
        shapes = self.polygon_shapes()
        if shapes:
            return [sh["polygon"] for sh in shapes]
        return [self.polygon_defaults] if not self.sels and self.tool.get() == "polygon" else []

    def sync_polygon(self):
        tgts = self.polygon_targets()
        if not tgts:
            self.polygon_box.pack_forget()
            return
        if not self.polygon_box.winfo_manager():
            first = next(w for w in self.custom_box.pack_slaves())  # (at the top)
            self.polygon_box.pack(fill="x", pady=(0, 4), before=first)
        d = tgts[0]
        self._loading = True
        for name, var in self.polygon_vars.items():
            var.set(fmt(d[name]))
            self.polygon_entries[name].config(style="TEntry")
        self.polygon_style.set(STYLE_NAMES[STYLES.index(d["style"])])
        self._loading = False
        self.polygon_entries["inner"].config(state="normal" if d["style"] == "star" else "disabled")
        self.polygon_entries["skip"].config(state="normal" if d["style"] == "cross" else "disabled")

    def set_polygon(self, key, value, edit_key=None):
        """A polygon setting changed: on the selected polygons (their strokes made again) and for new ones.
        edit_key: typing / scrubbing in a box (one undo step per box, App.begin_edit), else its own step."""
        shapes = [sh for sh in self.polygon_shapes() if sh["polygon"][key] != value]
        if shapes:
            if edit_key:
                self.begin_edit(edit_key)
            else:
                self.push_undo(name=tr("history.polygon"))
            for sh in shapes:
                sh["polygon"][key] = value
                update_polygon(sh)
        self.polygon_defaults[key] = value
        if shapes:
            self.shapes_changed()
        self.sync_polygon()
        self.schedule_autosave()

    def on_polygon_number(self, name):
        if self._loading or str(self.polygon_entries[name].cget("state")) == "disabled":
            return
        lo, hi, _, whole = NUMBERS[name]
        e = self.polygon_entries[name]
        try:
            value = float(calc(self.polygon_vars[name].get()))
            if not math.isfinite(value) or not lo <= value <= hi or whole and value != int(value):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        self.set_polygon(name, int(value) if whole else value, ("polygon", tuple(sorted(self.sels)), name))

    def on_polygon_style(self):
        if self._loading:
            return
        self.set_polygon("style", STYLES[STYLE_NAMES.index(self.polygon_style.get())])

