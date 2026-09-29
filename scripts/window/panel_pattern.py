"""The side panel's formula settings for curves (pattern.py): the shape of the curve and the pattern along it, each
with the numbers in its formula (a pattern also how many loops, and on a joined curve whether they run on across
the pieces). Also what the right-click menu's Formula items do (roll_menu.py)."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.joined import shown_tumour
from notes.pattern import (baked, formula_shape, loop_points, new_pattern, new_shape, pattern_name, shape_name,
                           SHAPE_NAMES)
from window.pattern_dialog import FormulaDialog, saved_pattern, saved_shape
from window.widgets import Scrub, Tooltip

LAYERS = ("shape", "pattern")  # (a curve's shape first: the pattern runs along it)
LOOP_STEPS = (1, 10, 0.1)    # quick changes (widgets.Scrub): step, Shift step, Ctrl step
NUMBER_STEPS = (0.5, 5, 0.1)


def layer_name(layer, p):
    return shape_name(p) if layer == "shape" else pattern_name(p)


class PatternPanel:
    """Mixed into App."""

    def _build_pattern(self):
        box = self.pattern_box = ttk.Frame(self.settings)
        self.formula_ui = {}
        for layer in LAYERS:
            row = ttk.Frame(box)
            top = ttk.Frame(row)
            top.pack(fill="x")
            ttk.Label(top, text=tr("panel_pattern.shape") if layer == "shape" else
                      tr("panel_pattern.pattern")).pack(side="left")
            label = ttk.Label(top, text="", foreground="#777")
            label.pack(side="left", padx=(5, 0))
            numbers = ttk.Frame(row)  # a box per name in the formula (a pattern: Loops first); rebuilt when they change
            numbers.pack(fill="x", pady=(2, 0))
            self.formula_ui[layer] = {"row": row, "label": label, "numbers": numbers, "boxes": {}, "names": None}
        row = self.pattern_each_row = ttk.Frame(self.formula_ui["pattern"]["row"])
        self.pattern_each = tk.BooleanVar(value=False)
        for value, text, tip in ((False, tr("panel_pattern.across_all"), tr("panel_pattern.across_all_tip")),
                                 (True, tr("panel_pattern.each_piece"), tr("panel_pattern.each_piece_tip"))):
            b = ttk.Radiobutton(row, text=text, value=value, variable=self.pattern_each,
                                command=lambda: self.set_pattern_each(self.pattern_each.get()))
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, tip)

    def pattern_targets(self):
        """The selected curves (what the Formula menu changes)."""
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] == "curve"]

    def with_layer(self, layer):
        return [sh for sh in self.pattern_targets() if sh.get(layer)]

    def patterned(self):
        """The selected curves that have a shape or pattern formula."""
        return [sh for sh in self.pattern_targets() if sh.get("shape") or sh.get("pattern")]

    # ------------------------------------------------------------ the right-click menu's Formula items
    def set_formula(self, layer, preset, saved=None):
        """A preset (None: none) or a saved one (pattern_dialog.load_patterns) as the shape of / pattern along every
        selected curve."""
        tgts = self.pattern_targets()
        if not tgts:
            return
        if saved:
            name = saved["name"]
        elif preset:
            name = SHAPE_NAMES[preset] if layer == "shape" else pattern_name({"preset": preset})
        else:
            name = None
        step = "panel_pattern.shape_step" if layer == "shape" else "panel_pattern.pattern_step"
        self.push_undo(name=tr(step, name=name) if name else tr("panel_pattern.remove_" + layer))
        k = self.roll.sy / self.roll.sx if self.roll.sx else 0.25  # sideways worked out as the roll looks now
        for sh in tgts:
            if preset is None and not saved:
                sh.pop(layer, None)
            elif layer == "shape":
                sh["shape"] = saved_shape(saved, k) if saved else new_shape(preset, k)
            else:
                sh["pattern"] = (saved_pattern(saved, k, sh.get("pattern")) if saved else
                                 new_pattern(preset, k, sh.get("pattern")))
        self.shapes_changed()
        self.sync_panel()

    def set_pattern(self, preset, saved=None):
        self.set_formula("pattern", preset, saved)

    def set_shape(self, preset, saved=None):
        self.set_formula("shape", preset, saved)

    def remove_formulas(self):
        """Remove formula: the selected curves back to their plain (dotted) path."""
        tgts = self.patterned()
        if not tgts:
            return
        self.push_undo(name=tr("panel_pattern.remove_formula"))
        for sh in tgts:
            sh.pop("shape", None)
            sh.pop("pattern", None)
        self.shapes_changed()
        self.sync_panel()

    def open_formula_dialog(self, layer):
        if self.pattern_targets():
            FormulaDialog(self, layer)

    def plain_curve(self):
        """Turn into plain curve: the shapes / patterns become ordinary anchors and handles (tumours stay a
        setting)."""
        tgts = self.patterned()
        if not tgts:
            return
        self.push_undo(name=tr("panel_pattern.turn_into_plain_curve"))
        for sh in tgts:
            got = baked(sh)
            tm = shown_tumour(sh)
            for key in ("shape", "pattern", "sym", "tumours", "splits", "gaps", "sharp"):
                sh.pop(key, None)
            sh["pts"] = got["pts"]
            if got["sharp"]:
                sh["sharp"] = got["sharp"]
            if got["gaps"]:
                sh["gaps"] = got["gaps"]
            if tm:
                sh["tumour"] = tm
        self.shapes_changed()
        self.sync_panel()

    # ------------------------------------------------------------ the panel
    def sync_pattern(self):
        self._rows["pattern"] = bool(self.patterned())
        self.layout_rows()
        if not self._rows["pattern"]:
            return
        for layer in LAYERS:
            ui = self.formula_ui[layer]
            tgts = self.with_layer(layer)
            if not tgts:
                ui["row"].pack_forget()
                continue
            ui["row"].pack(fill="x", pady=(0, 4))
            p = tgts[0][layer]
            names = (["loops"] if layer == "pattern" else []) + ([] if p.get("loop") else list(p["vars"]))
            if names != ui["names"]:
                self._build_formula_boxes(layer, names)
            self._loading = True
            text = layer_name(layer, p)
            if len({layer_name(layer, sh[layer]) for sh in tgts}) > 1:
                text += tr("panel_pattern.and_others")
            ui["label"].config(text=text)
            for name, (var, e) in ui["boxes"].items():
                var.set(fmt(p["loops"] if name == "loops" else p["vars"][name]))
                e.config(style="TEntry")
            self._loading = False
        pats = self.with_layer("pattern")
        if pats:
            self.pattern_each.set(pats[0]["pattern"]["each"])
        if any(sh.get("gaps") for sh in pats):  # only a joined curve with gaps has pieces
            self.pattern_each_row.pack(fill="x", pady=(2, 0))
        else:
            self.pattern_each_row.pack_forget()

    def _build_formula_boxes(self, layer, names):
        ui = self.formula_ui[layer]
        for w in ui["numbers"].winfo_children():
            w.destroy()
        ui["boxes"], ui["names"] = {}, names
        for name in names:
            cell = ttk.Frame(ui["numbers"])
            cell.pack(side="left", padx=(0, 8))
            lb = ttk.Label(cell, text=tr("panel_pattern.loops") if name == "loops" else name)
            lb.pack(side="left")
            var = tk.StringVar()
            e = ttk.Entry(cell, textvariable=var, width=5)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, n=name: self.on_formula_entry(layer, n))
            e.bind("<FocusOut>", lambda ev, n=name: self.on_formula_entry(layer, n))
            loops = name == "loops"
            Scrub(self, [(e, var, lambda n=name: self.on_formula_entry(layer, n))],
                  LOOP_STEPS if loops else NUMBER_STEPS, 0.01 if loops else None, None, label=lb)
            tip = (tr("panel_pattern.loops_tip") if loops else
                   tr("panel_pattern.shape_number_tip" if layer == "shape" else "panel_pattern.number_tip", name=name))
            for w in (lb, e):
                Tooltip(w, tip)
            ui["boxes"][name] = (var, e)

    def on_formula_entry(self, layer, name):
        ui = self.formula_ui[layer]
        if self._loading or name not in ui["boxes"]:
            return
        var, e = ui["boxes"][name]
        tgts = [sh for sh in self.with_layer(layer) if name == "loops" or name in sh[layer]["vars"] and
                not sh[layer].get("loop")]
        try:
            value = float(calc(var.get()))
            if not math.isfinite(value) or (name == "loops" and value <= 0):
                raise ValueError
            for sh in tgts:  # (a number the formula can't be worked out with, e.g. sqrt of a minus)
                p = sh[layer]
                trial = dict(p, vars=dict(p["vars"], **({} if name == "loops" else {name: value})))
                formula_shape(trial) if layer == "shape" else loop_points(trial)
        except (ValueError, ZeroDivisionError):
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        tgts = [sh for sh in tgts if (sh[layer]["loops"] if name == "loops" else sh[layer]["vars"][name]) != value]
        if not tgts:
            return
        self.begin_edit((layer, tuple(sorted(self.sels)), name))
        for sh in tgts:
            if name == "loops":
                sh[layer]["loops"] = value
            else:
                sh[layer]["vars"][name] = value
        self.shapes_changed()

    def set_pattern_each(self, each):
        tgts = [sh for sh in self.with_layer("pattern") if sh["pattern"]["each"] != each]
        if not tgts:
            return
        self.push_undo(name=tr("panel_pattern.each_piece") if each else tr("panel_pattern.across_all"))
        for sh in tgts:
            sh["pattern"]["each"] = each
        self.shapes_changed()
