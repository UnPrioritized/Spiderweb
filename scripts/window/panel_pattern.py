"""The side panel's pattern settings for curves with a formula along them (pattern.py): how many loops, the numbers
in the formula, and on a joined curve whether the loops run on across the pieces. Also what the right-click menu's
Formula items do (roll_menu.py)."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.joined import shown_tumour
from notes.pattern import baked, loop_points, new_pattern, pattern_name
from window.pattern_dialog import PatternDialog, saved_pattern
from window.widgets import Scrub, Tooltip

LOOP_STEPS = (1, 10, 0.1)    # quick changes (widgets.Scrub): step, Shift step, Ctrl step
NUMBER_STEPS = (0.5, 5, 0.1)


class PatternPanel:
    """Mixed into App."""

    def _build_pattern(self):
        box = self.pattern_box = ttk.Frame(self.settings)
        top = ttk.Frame(box)
        top.pack(fill="x")
        ttk.Label(top, text=tr("panel_pattern.pattern")).pack(side="left")
        self.pattern_label = ttk.Label(top, text="", foreground="#777")
        self.pattern_label.pack(side="left", padx=(5, 0))
        self.pattern_numbers = ttk.Frame(box)  # Loops, then a box per name in the formula (rebuilt when they change)
        self.pattern_numbers.pack(fill="x", pady=(2, 0))
        self.pattern_boxes = {}  # "loops" / a name in the formula -> (variable, entry box)
        self._pattern_names = None
        row = self.pattern_each_row = ttk.Frame(box)
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

    def patterned(self):
        return [sh for sh in self.pattern_targets() if sh.get("pattern")]

    # ------------------------------------------------------------ the right-click menu's Formula items
    def set_pattern(self, preset, saved=None):
        """A preset pattern (None: no pattern), or a saved one (pattern_dialog.load_patterns), on every selected
        curve."""
        tgts = self.pattern_targets()
        if not tgts:
            return
        name = saved["name"] if saved else pattern_name({"preset": preset}) if preset else None
        self.push_undo(name=tr("panel_pattern.pattern_step", name=name) if name else tr("panel_pattern.remove_formula"))
        k = self.roll.sy / self.roll.sx if self.roll.sx else 0.25  # sideways worked out as the roll looks now
        for sh in tgts:
            if saved:
                sh["pattern"] = saved_pattern(saved, k, sh.get("pattern"))
            elif preset is None:
                sh.pop("pattern", None)
            else:
                sh["pattern"] = new_pattern(preset, k, sh.get("pattern"))
        self.shapes_changed()
        self.sync_panel()

    def open_pattern_dialog(self):
        if self.pattern_targets():
            PatternDialog(self)

    def plain_curve(self):
        """Turn into plain curve: the patterns become ordinary anchors and handles (tumours stay a setting)."""
        tgts = self.patterned()
        if not tgts:
            return
        self.push_undo(name=tr("panel_pattern.turn_into_plain_curve"))
        for sh in tgts:
            got = baked(sh)
            tm = shown_tumour(sh)
            for key in ("pattern", "sym", "tumours", "splits", "gaps", "sharp"):
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
        tgts = self.patterned()
        self._rows["pattern"] = bool(tgts)
        self.layout_rows()
        if not tgts:
            return
        pat = tgts[0]["pattern"]
        names = ["loops"] + ([] if pat.get("loop") else list(pat["vars"]))  # (a loop edited by hand: just Loops)
        if names != self._pattern_names:
            self._build_pattern_boxes(names)
        self._loading = True
        text = pattern_name(pat)
        if len({pattern_name(sh["pattern"]) for sh in tgts}) > 1:
            text += tr("panel_pattern.and_others")
        self.pattern_label.config(text=text)
        for name, (var, e) in self.pattern_boxes.items():
            var.set(fmt(pat["loops"] if name == "loops" else pat["vars"][name]))
            e.config(style="TEntry")
        self.pattern_each.set(pat["each"])
        self._loading = False
        if any(sh.get("gaps") for sh in tgts):  # only a joined curve with gaps has pieces
            self.pattern_each_row.pack(fill="x", pady=(2, 0))
        else:
            self.pattern_each_row.pack_forget()

    def _build_pattern_boxes(self, names):
        for w in self.pattern_numbers.winfo_children():
            w.destroy()
        self.pattern_boxes = {}
        self._pattern_names = names
        for name in names:
            cell = ttk.Frame(self.pattern_numbers)
            cell.pack(side="left", padx=(0, 8))
            lb = ttk.Label(cell, text=tr("panel_pattern.loops") if name == "loops" else name)
            lb.pack(side="left")
            var = tk.StringVar()
            e = ttk.Entry(cell, textvariable=var, width=5)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, n=name: self.on_pattern_entry(n))
            e.bind("<FocusOut>", lambda ev, n=name: self.on_pattern_entry(n))
            loops = name == "loops"
            Scrub(self, [(e, var, lambda n=name: self.on_pattern_entry(n))], LOOP_STEPS if loops else NUMBER_STEPS,
                  0.01 if loops else None, None, label=lb)
            tip = tr("panel_pattern.loops_tip") if loops else tr("panel_pattern.number_tip", name=name)
            for w in (lb, e):
                Tooltip(w, tip)
            self.pattern_boxes[name] = (var, e)

    def on_pattern_entry(self, name):
        if self._loading or name not in self.pattern_boxes:
            return
        var, e = self.pattern_boxes[name]
        tgts = [sh for sh in self.patterned() if name == "loops" or name in sh["pattern"]["vars"] and
                not sh["pattern"].get("loop")]
        try:
            value = float(calc(var.get()))
            if not math.isfinite(value) or (name == "loops" and value <= 0):
                raise ValueError
            for sh in tgts:  # (a number the formula can't be worked out with, e.g. sqrt of a minus)
                loop_points(dict(sh["pattern"], vars=dict(sh["pattern"]["vars"], **({} if name == "loops" else
                                                                                       {name: value}))))
        except (ValueError, ZeroDivisionError):
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        tgts = [sh for sh in tgts if (sh["pattern"]["loops"] if name == "loops" else sh["pattern"]["vars"][name])
                != value]
        if not tgts:
            return
        self.begin_edit(("pattern", tuple(sorted(self.sels)), name))
        for sh in tgts:
            if name == "loops":
                sh["pattern"]["loops"] = value
            else:
                sh["pattern"]["vars"][name] = value
        self.shapes_changed()

    def set_pattern_each(self, each):
        tgts = [sh for sh in self.patterned() if sh["pattern"]["each"] != each]
        if not tgts:
            return
        self.push_undo(name=tr("panel_pattern.each_piece") if each else tr("panel_pattern.across_all"))
        for sh in tgts:
            sh["pattern"]["each"] = each
        self.shapes_changed()
