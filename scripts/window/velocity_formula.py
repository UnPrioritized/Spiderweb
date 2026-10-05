"""The velocity pane's Formula tool settings (next to its tool buttons, shown while it's the tool): which pattern
(pattern.py presets and the user's saved ones), how many loops, and the numbers in its formula, in velocity steps.
Changing them changes the formula line just drawn (until Enter / a click elsewhere), like its handles do."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.pattern import PATTERN_PRESETS, PRESET_ALONG, formula_loop, new_pattern
from window.pattern_dialog import load_patterns, saved_pattern
from window.widgets import Scrub, Tooltip, bad, good

VEL_HEIGHT = 20.0  # a preset's height to start with, in velocity steps (on the piano roll it's 4 keys)
VEL_LOOPS = 4.0


def vel_pattern(preset=None, saved=None):
    p = saved_pattern(saved, 1.0) if saved else new_pattern(preset or "wave", 1.0)
    if not saved and "height" in p["vars"]:
        p["vars"]["height"] = VEL_HEIGHT
    p["loops"] = VEL_LOOPS
    return p


class VelocityFormulaBar(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        app.vel_pattern = vel_pattern()
        self.pick = tk.StringVar()
        self.box = ttk.Combobox(self, textvariable=self.pick, state="readonly", width=14)
        self.box.pack(side="left", padx=(6, 6))
        self.box.bind("<<ComboboxSelected>>", lambda e: self.on_pick())
        self.box.bind("<Button-1>", lambda e: self.fill_list(), add="+")  # (saved ones may have changed)
        Tooltip(self.box, tr("velocity_formula.pattern_tip"))
        self.numbers = ttk.Frame(self)
        self.numbers.pack(side="left")
        self.boxes, self._names, self._loading = {}, None, False
        self.fill_list()
        self.pick.set(self.items[0][2])
        self.refresh()

    def fill_list(self):
        # (not ones that move along: velocity can't go back in time)
        self.items = [("preset", pid, name) for pid, name, _, _ in PATTERN_PRESETS if pid not in PRESET_ALONG]
        self.items += [("saved", item, item["name"]) for item in load_patterns("pattern") if not item.get("along")]
        self.box.config(values=[name for _, _, name in self.items])

    def on_pick(self):
        for kind, what, name in self.items:
            if name == self.pick.get():
                old = self.app.vel_pattern
                p = vel_pattern(what, None) if kind == "preset" else vel_pattern(saved=what)
                p["loops"] = old["loops"]
                if kind == "preset":  # (a preset's numbers stay as they were set, like its height)
                    p["vars"].update({n: v for n, v in old["vars"].items() if n in p["vars"]})
                self.app.vel_pattern = p
                break
        self.refresh()
        self.app.vel.formula_changed()

    def refresh(self):
        p = self.app.vel_pattern
        names = ["loops"] + ([] if p.get("loop") else list(p["vars"]))
        if names != self._names:
            for w in self.numbers.winfo_children():
                w.destroy()
            self.boxes, self._names = {}, names
            for name in names:
                cell = ttk.Frame(self.numbers)
                cell.pack(side="left", padx=(0, 6))
                lb = ttk.Label(cell, text=tr("panel_pattern.loops") if name == "loops" else name)
                lb.pack(side="left")
                var = tk.StringVar()
                e = ttk.Entry(cell, textvariable=var, width=5)
                e.pack(side="left", padx=(3, 0))
                e.bind("<Return>", lambda ev, n=name: (self.on_number(n), "break")[1])
                e.bind("<FocusOut>", lambda ev, n=name: self.on_number(n))
                loops = name == "loops"
                Scrub(self.app, [(e, var, lambda n=name: self.on_number(n))], (1, 10, 0.1) if loops else (1, 10, 0.5),
                      0.01 if loops else None, None, label=lb)
                tip = tr("velocity_formula.loops_tip") if loops else tr("velocity_formula.number_tip", name=name)
                for w in (lb, e):
                    Tooltip(w, tip)
                self.boxes[name] = (var, e)
        self._loading = True
        for name, (var, e) in self.boxes.items():
            var.set(fmt(p["loops"] if name == "loops" else p["vars"][name]))
            e.config(style="TEntry")
        self._loading = False

    def on_number(self, name):
        if self._loading or name not in self.boxes:
            return
        var, e = self.boxes[name]
        p = self.app.vel_pattern
        try:
            value = float(calc(var.get()))
            if not math.isfinite(value) or (name == "loops" and value <= 0):
                raise ValueError
            if name != "loops":
                formula_loop(dict(p, vars=dict(p["vars"], **{name: value})))
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        good(e)
        if (p["loops"] if name == "loops" else p["vars"][name]) == value:
            return
        if name == "loops":
            p["loops"] = value
        else:
            p["vars"][name] = value
        self.app.vel.formula_changed()
