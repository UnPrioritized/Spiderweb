"""The side panel's formula settings for curves (pattern.py): the shape of the curve and the pattern along it, each
with the numbers in its formula (a pattern also how many loops, and on a joined curve whether they run on across
the pieces). Also what the right-click menu's Formula items do (roll_menu.py)."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.pattern import FORMULA_KINDS, formula_shape, loop_points
from window.formula_host import SYM_CHOICES, RollHost, layer_name, sym_label
from window.widgets import Scrub, Tooltip

LAYERS = ("shape", "pattern")  # (a curve's shape first: the pattern runs along it)
LOOP_STEPS = (1, 10, 0.1)    # quick changes (widgets.Scrub): step, Shift step, Ctrl step
NUMBER_STEPS = (0.5, 5, 0.1)


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
            sym_row = ttk.Frame(row)  # symmetric halves (only the formula's first half counts)
            sym_row.pack(fill="x", pady=(2, 0))
            lb = ttk.Label(sym_row, text=tr("widgets.symmetric_halves"))
            lb.pack(side="left")
            sym = tk.StringVar()
            names = [tr(key) for _, key in SYM_CHOICES]
            cb = ttk.Combobox(sym_row, textvariable=sym, values=names, state="readonly",
                              width=max(len(n) for n in names))
            cb.pack(side="left", padx=(5, 0))
            cb.bind("<<ComboboxSelected>>", lambda e, l=layer: self.on_formula_sym(l))
            for w in (lb, cb):
                Tooltip(w, tr("pattern_dialog.sym_tip"))
            self.formula_ui[layer] = {"row": row, "label": label, "numbers": numbers, "boxes": {}, "names": None,
                                      "sym": sym}
        row = self.pattern_each_row = ttk.Frame(self.formula_ui["pattern"]["row"])
        self.pattern_each = tk.BooleanVar(value=False)
        for value, text, tip in ((False, tr("panel_pattern.across_all"), tr("panel_pattern.across_all_tip")),
                                 (True, tr("panel_pattern.each_piece"), tr("panel_pattern.each_piece_tip"))):
            b = ttk.Radiobutton(row, text=text, value=value, variable=self.pattern_each,
                                command=lambda: self.set_pattern_each(self.pattern_each.get()))
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, tip)

    def pattern_targets(self):
        """The selected curves, lines and arcs (what the Formula menu changes)."""
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] in FORMULA_KINDS]

    def with_layer(self, layer):
        return [sh for sh in self.pattern_targets() if sh.get(layer)]

    def patterned(self):
        """The selected curves that have a shape or pattern formula."""
        return [sh for sh in self.pattern_targets() if sh.get("shape") or sh.get("pattern")]

    # ------------------------------------------------------------ the right-click menu's Formula items
    # (formula_host.py: the same menu / window works on drawer strokes and funnel curves)
    def set_formula(self, layer, preset, saved=None):
        """A preset (None: none) or a saved one (pattern_dialog.load_patterns) as the shape of / pattern along every
        selected curve."""
        RollHost(self).set_formula(layer, preset, saved)

    def set_pattern(self, preset, saved=None):
        self.set_formula("pattern", preset, saved)

    def set_shape(self, preset, saved=None):
        self.set_formula("shape", preset, saved)

    def remove_formulas(self):
        """Remove formula: the selected curves back to their plain (dotted) path."""
        RollHost(self).remove()

    def open_formula_dialog(self, layer):
        RollHost(self).open_dialog(layer)

    def plain_curve(self):
        """Turn into plain curve: the shapes / patterns become ordinary anchors and handles (tumours stay a
        setting)."""
        RollHost(self).plain()

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
            ui["sym"].set(sym_label(p.get("sym")))
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

    def on_formula_sym(self, layer):
        if self._loading:
            return
        text = self.formula_ui[layer]["sym"].get()
        mode = next(value for value, key in SYM_CHOICES if tr(key) == text) or None
        RollHost(self).set_sym(layer, mode)

    def set_pattern_each(self, each):
        tgts = [sh for sh in self.with_layer("pattern") if sh["pattern"]["each"] != each]
        if not tgts:
            return
        self.push_undo(name=tr("panel_pattern.each_piece") if each else tr("panel_pattern.across_all"))
        for sh in tgts:
            sh["pattern"]["each"] = each
        self.shapes_changed()
