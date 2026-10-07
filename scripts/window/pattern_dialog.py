"""The Custom… window for a curve's formulas (pattern.py), one for its shape and one for the pattern along it: pick a
preset or a saved one, change its numbers (each other name in a formula gets a number box), or type formulas. The
preview shows ONE loop of a pattern (as long as it is on the curve), or the whole shape between the curve's ends A
and B, with anchors and handles: dragging them changes it by hand (every loop along the curve follows). The user's
saved ones are in spiderweb/patterns.json (the funnel's old saved curve formulas, curves.json, are moved in there).
It works on a host (formula_host.py: the piano roll's curves, a drawer stroke, a funnel's curves). Changes show
straight away; Apply keeps them as one undo step, Cancel puts back how it was. Ctrl+Z / Ctrl+Y step through the
changes made in the window (they never reach the piano roll's undo while it's open)."""

import copy
import json
import math
import os
import re
import tkinter as tk
from tkinter import ttk, messagebox

from files.lang import tr
from files.mathexpr import calc, fmt, formula
from files.project import HERE
from files.safefile import write_text
from notes.bezier import (SYM_MODES, add_anchor, can_delete, delete_point, drag_point, fit_symmetric, handle_lines,
                          held_axis, keep_symmetric, nearest, pen_handles, sample)
from notes.pattern import (LOOPS_DEFAULT, MAX_LOOPS, PATTERN_PRESETS, PRESET_ALONG, SHAPE_PRESETS, clean_loop,
                           formula_loop, formula_shape, keep_sym, new_pattern, new_shape, pattern_name, pattern_names,
                           shape_name, shape_names)
from roll.roll_shared import ALT, grab_while_panning
from window.formula_host import SYM_CHOICES, set_loop_sym, sym_label
from window.panel_custom import GAP_COLOR
from window.widgets import LocalUndo, Scrub, Tooltip, bad, good

PATTERNS_FILE = os.path.join(HERE, "patterns.json")
OLD_CURVES_FILE = os.path.join(HERE, "curves.json")  # the funnel's saved curve formulas (before shapes of curves)
LOOP_TOLERANCE = 0.02    # how closely the anchors + handles follow a pattern's formula when first edited, in keys
SHAPE_TOLERANCE = 0.002  # the same for a shape, as a share of the curve's length
GRAB = 7  # how near (pixels) a point has to be to be dragged
FILE_KEYS = {"pattern": "patterns", "shape": "shapes"}


def load_patterns(layer="pattern"):
    """The user's saved patterns [{"name", "formula", "vars", maybe "loop"}] or shapes [{"name", "x", "y", "vars",
    maybe "loop"}]."""
    if layer == "shape" and os.path.exists(OLD_CURVES_FILE):
        move_old_curves()
    try:
        with open(PATTERNS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        out = []
        for p in data.get(FILE_KEYS[layer], []):
            item = {"name": str(p["name"]), "vars": {str(a): float(b) for a, b in dict(p.get("vars", {})).items()}}
            for key in (("x", "y") if layer == "shape" else ("formula", "along")):
                if key != "along" or str(p.get(key, "")).strip():
                    item[key] = str(p.get(key, ""))
            loop = clean_loop(p["loop"]) if isinstance(p.get("loop"), dict) else None
            if loop:
                item["loop"] = loop
            out.append(keep_sym(item, p))
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def save_patterns(items, layer="pattern"):
    data = {key: load_patterns(other) for other, key in FILE_KEYS.items() if other != layer}
    data[FILE_KEYS[layer]] = items
    write_text(PATTERNS_FILE, json.dumps(data, indent=1))


def move_old_curves():
    """The funnel's saved curve formulas (y of x, stretched to go from 0 to 1) become saved shapes; curves.json is
    renamed curves-old.json (kept, just in case)."""
    try:
        with open(OLD_CURVES_FILE, encoding="utf-8") as f:
            old = [(str(c["name"]), str(c["formula"])) for c in json.load(f).get("curves", [])]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        old = []
    try:
        with open(PATTERNS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return  # (a broken patterns.json: leave both alone)
    shapes = data.setdefault(FILE_KEYS["shape"], [])
    names = {str(p.get("name")) for p in shapes if isinstance(p, dict)}
    for name, text in old:
        y = old_curve_y(text)
        if y and name not in names:
            shapes.append({"name": name, "x": "t", "y": y, "vars": {}})
            names.add(name)
    try:
        write_text(PATTERNS_FILE, json.dumps(data, indent=1))
        os.replace(OLD_CURVES_FILE, os.path.splitext(OLD_CURVES_FILE)[0] + "-old.json")
    except OSError:
        pass


def old_curve_y(text):
    """An old funnel curve formula (y of x) as y(t) of a shape, stretched so it goes from 0 to 1 like it did then,
    or None if it doesn't work."""
    y = re.sub(r"\bx\b", "t", text)
    try:
        fn = formula(y, var="t")
        y0, y1 = float(fn(0.0)), float(fn(1.0))
    except (ValueError, ArithmeticError, TypeError):
        return None
    if not (math.isfinite(y0) and math.isfinite(y1)) or abs(y1 - y0) < 1e-12:
        return None
    if abs(y0) < 1e-12 and abs(y1 - 1) < 1e-12:
        return y
    return f"(({y}) - {y0:.12g}) / {y1 - y0:.12g}"


def saved_pattern(item, k, old=None):
    """A saved pattern (load_patterns) as a curve's pattern. k: beats per key on screen; old: the pattern it
    replaces (its loops and how it runs over pieces stay)."""
    old = old or {}
    out = {"preset": "", "name": item["name"], "formula": item["formula"], "vars": dict(item["vars"]),
           "loops": old.get("loops", LOOPS_DEFAULT), "each": old.get("each", False), "k": k, "mirror": False,
           "scale": 1.0}
    if item.get("along"):
        out["along"] = item["along"]
    if item.get("loop"):
        out["loop"] = copy.deepcopy(item["loop"])
    return keep_sym(out, item)


def saved_shape(item, k):
    """A saved shape (load_patterns("shape")) as a curve's shape."""
    out = {"preset": "", "name": item["name"], "x": item["x"], "y": item["y"], "vars": dict(item["vars"]), "k": k,
           "mirror": False}
    if item.get("loop"):
        out["loop"] = copy.deepcopy(item["loop"])
    return keep_sym(out, item)


class FormulaDialog(tk.Toplevel):
    """Custom… for the host's (formula_host.py) curves' shape (layer "shape") or pattern along them (layer
    "pattern")."""

    def __init__(self, host, layer="pattern"):
        parent = host.parent
        super().__init__(parent)
        self.layer = layer
        shape = layer == "shape"
        self.title(tr("pattern_dialog.shape_title") if shape else tr("pattern_dialog.title"))
        self.transient(parent)
        self.resizable(False, False)
        self.host, self.app = host, host.app
        self.targets = host.targets()
        self.snap = host.snapshot()
        self.before = [copy.deepcopy(h) for h in self.targets]
        self.k = host.fresh(self.targets[0], layer)["k"]
        first = self.targets[0].get(layer)
        self.pat = copy.deepcopy(first) if first else new_shape("circle", self.k) if shape else new_pattern("wave",
                                                                                                          self.k)
        self.ok = True      # the formulas work
        self.view = None    # (pixels per unit, left x, middle y, loop length in units) while dragging
        self.own_view = False  # zoomed / moved by hand (kept until Fit view or another pick in the list)
        self.pan = None     # moving the view: (mouse x, mouse y, view then, moved yet)
        self.drag = None    # the point being dragged
        self._loading = False
        s = self.scale = self.app.scale
        self.w, self.h = int(460 * s), int(250 * s) if not shape else int(300 * s)

        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        left = ttk.LabelFrame(body, text=tr("pattern_dialog.shapes") if shape else tr("pattern_dialog.patterns"),
                              padding=6)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, height=16, width=22, activestyle="none", exportselection=False,
                                  font=("Segoe UI", 9))
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_pick)
        ttk.Button(left, text=tr("pattern_dialog.delete"), command=self.delete).pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        right.pack(side="left", fill="both")
        right.columnconfigure(1, weight=1)
        self.name = tk.StringVar(value=self.pat.get("name") or self.preset_name())
        ttk.Label(right, text=tr("pattern_dialog.name")).grid(row=0, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.name, width=30).grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=1)
        # the formula boxes: y = and along = (a pattern) or x(t) = and y(t) = (a shape)
        keys = ("x", "y") if shape else ("formula", "along")
        labels = {"x": tr("pattern_dialog.xt"), "y": tr("pattern_dialog.yt"), "formula": tr("pattern_dialog.y"),
                  "along": tr("pattern_dialog.along")}
        self.texts, self.entries = {}, []
        self.editing = tk.BooleanVar(value=False)
        for r, key in enumerate(keys, start=1):
            lb = ttk.Label(right, text=labels[key])
            lb.grid(row=r, column=0, sticky="w")
            row = ttk.Frame(right)
            row.grid(row=r, column=1, sticky="ew", padx=(5, 0), pady=1)
            var = self.texts[key] = tk.StringVar(value=self.pat.get(key, ""))
            e = ttk.Entry(row, textvariable=var, width=34, font=("Consolas", 10), state="readonly")
            e.pack(side="left", fill="x", expand=True)
            self.entries.append(e)
            if key == "along":
                for w in (lb, e):
                    Tooltip(w, tr("pattern_dialog.along_tip"))
            if r == 1:
                b = ttk.Checkbutton(row, text=tr("pattern_dialog.edit_formula"), variable=self.editing,
                                    command=self.on_edit_mode)
                b.pack(side="left", padx=(6, 0))
                Tooltip(b, tr("pattern_dialog.edit_formula_tip"))
        r = len(keys) + 1
        ttk.Label(right, text=tr("pattern_dialog.shape_help") if shape else tr(host.pattern_help),
                  foreground="#777", font=("Segoe UI", 8), wraplength=int(440 * s),
                  justify="left").grid(row=r, column=0, columnspan=2, sticky="w", pady=(3, 4))
        row = ttk.Frame(right)  # symmetric halves: only the first half of the formula counts
        row.grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(0, 4))
        lb = ttk.Label(row, text=tr("widgets.symmetric_halves"))
        lb.pack(side="left")
        self.sym = tk.StringVar()
        names = [tr(key) for _, key in SYM_CHOICES]
        cb = ttk.Combobox(row, textvariable=self.sym, values=names, state="readonly",
                          width=max(len(n) for n in names))
        cb.pack(side="left", padx=(5, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_sym())
        for w in (lb, cb):
            Tooltip(w, tr("pattern_dialog.sym_tip"))
        r += 1
        self.numbers = ttk.Frame(right)  # (a pattern: Loops, then) a box per name in the formulas
        self.numbers.grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.boxes = {}
        self._names = None
        self.canvas = tk.Canvas(right, width=self.w, height=self.h, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#c0c0c0")
        self.canvas.grid(row=r + 2, column=0, columnspan=2)
        c = self.canvas
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", self.release)
        c.bind("<Button-3>", self.right_click)
        c.bind("<ButtonPress-2>", self.start_pan)
        c.bind("<B2-Motion>", self.move_pan)
        c.bind("<ButtonRelease-2>", self.middle_release)
        grab_while_panning(c)
        c.bind("<MouseWheel>", self.wheel)
        c.bind("<Double-Button-1>", self.add_point)
        info = ttk.Frame(right)
        info.grid(row=r + 3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.info = ttk.Label(info, text="", font=("Segoe UI", 9), wraplength=int(300 * s), justify="left")
        self.info.pack(side="left")
        b = ttk.Button(info, text=tr("pattern_dialog.fit_view"), command=self.fit_again)
        b.pack(side="right")
        Tooltip(b, tr("pattern_dialog.fit_view_tip"))
        self.back = ttk.Button(info, text=tr("pattern_dialog.back_to_formula"), command=self.back_to_formula)
        Tooltip(self.back, tr("pattern_dialog.back_to_formula_tip"))
        btns = ttk.Frame(right)
        btns.grid(row=r + 4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text=tr("pattern_dialog.save"), command=self.save).pack(side="left")
        ttk.Button(btns, text=tr("pattern_dialog.cancel"), command=self.cancel).pack(side="right")
        ttk.Button(btns, text=tr("pattern_dialog.apply"), command=self.apply).pack(side="right", padx=4)

        for key, var in self.texts.items():
            var.trace_add("write", lambda *_, k=key: (self.on_formula(), self.mark(("text", k))))
        self.bind("<Return>", lambda e: self.apply())
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.fill_list()
        self.pick_current()
        self.refresh()
        self.hist = LocalUndo(self, self.state, self.put_state)
        self.update_idletasks()  # over the middle of the window it's for
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()  # the roll can't change under it while it previews
        self.focus_set()

    def presets(self):
        """(id, name, formulas {key: text}, numbers) of the built-in ones."""
        if self.layer == "shape":
            return [(sid, name, {"x": x, "y": y}, values) for sid, name, x, y, values in SHAPE_PRESETS]
        return [(pid, name, {"formula": text, "along": PRESET_ALONG.get(pid, "")}, values)
                for pid, name, text, values in PATTERN_PRESETS]

    def preset_name(self):
        return next((name for pid, name, _, _ in self.presets() if pid == self.pat.get("preset")), "")

    def name_of(self, p):
        return shape_name(p) if self.layer == "shape" else pattern_name(p)

    # ------------------------------------------------------------ undo inside the window
    def state(self):
        return json.dumps({"pat": self.pat, "name": self.name.get(),
                           "texts": {key: var.get() for key, var in self.texts.items()}})

    def put_state(self, state):
        d = json.loads(state)
        self.pat, self.drag, self.pan = d["pat"], None, None
        self._loading = True
        self.name.set(d["name"])
        for key, var in self.texts.items():
            var.set(d["texts"][key])
        self._loading = False
        self.pick_current()
        self.refresh()
        if any(d["texts"][key] != self.pat.get(key, "") for key in self.texts):
            self.on_formula()  # (a formula that was being typed and doesn't work yet)

    def mark(self, key=None):
        """A change done: an undo step (see LocalUndo)."""
        if not self._loading and hasattr(self, "hist"):
            self.hist.mark(key)

    # ------------------------------------------------------------ the list
    def pick_current(self):
        """The one it has now, picked in the list."""
        self.listbox.selection_clear(0, "end")
        for i, (kind, what, name) in enumerate(self.items):
            if not self.pat.get("loop") or self.pat.get("name"):
                if (kind == "preset" and not self.pat.get("name") and what == self.pat.get("preset")
                        or kind == "saved" and name == self.pat.get("name")):
                    self.listbox.selection_set(i)
                    self.listbox.see(i)

    def fill_list(self):
        self.saved = load_patterns(self.layer)
        self.items = [("preset", pid, name) for pid, name, _, _ in self.presets()]
        self.items += [("saved", item, item["name"]) for item in self.saved]
        self.listbox.delete(0, "end")
        for kind, _, name in self.items:
            self.listbox.insert("end", name)
            if kind == "preset":
                self.listbox.itemconfig("end", foreground="#555")

    def on_pick(self, _):
        sel = self.listbox.curselection()
        if not sel:
            return
        kind, what, name = self.items[sel[0]]
        if self.layer == "shape":  # (a preset keeps the symmetric halves picked, a saved one has its own)
            self.pat = new_shape(what, self.k, self.pat) if kind == "preset" else saved_shape(what, self.k)
        else:
            keep = {key: self.pat[key] for key in ("loops", "each")}
            self.pat = new_pattern(what, self.k, self.pat) if kind == "preset" else saved_pattern(what, self.k)
            self.pat.update(keep)
        self._loading = True
        self.name.set(name)
        for key, var in self.texts.items():
            var.set(self.pat.get(key, ""))
        self._loading = False
        self.ok = True
        self.own_view = False
        self.refresh()
        self.mark()

    def save(self):
        name = self.name.get().strip()
        if not self.ok and not self.pat.get("loop"):
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.doesn_t_work_yet"),
                                        parent=self)
        if not name:
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.name_first"),
                                        parent=self)
        if any(p["name"] == name for p in self.saved) and not messagebox.askyesno(
                tr("pattern_dialog.spiderweb"), tr("pattern_dialog.replace", name=name), parent=self):
            return
        item = {"name": name, "vars": dict(self.pat["vars"])}
        item.update({key: self.pat[key] for key in self.texts if key in self.pat})
        if self.pat.get("loop"):
            item["loop"] = copy.deepcopy(self.pat["loop"])
        keep_sym(item, self.pat)
        try:
            save_patterns([p for p in self.saved if p["name"] != name] + [item], self.layer)
        except OSError as e:
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.couldn_t_save", e=e),
                                        parent=self)
        self.pat.update(name=name, preset="")
        self.fill_list()
        i = next(i for i, (kind, _, n) in enumerate(self.items) if kind == "saved" and n == name)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.see(i)
        self.show_on_roll()

    def delete(self):
        sel = self.listbox.curselection()
        if not sel or self.items[sel[0]][0] != "saved":
            return messagebox.showinfo(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.only_saved"), parent=self)
        name = self.items[sel[0]][2]
        if not messagebox.askyesno(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.delete_saved", name=name),
                                   parent=self):
            return
        try:
            save_patterns([p for p in self.saved if p["name"] != name], self.layer)
        except OSError as e:
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.couldn_t_save", e=e),
                                        parent=self)
        self.fill_list()

    # ------------------------------------------------------------ the formulas and their numbers
    def on_edit_mode(self):
        for e in self.entries:
            e.config(state="normal" if self.editing.get() else "readonly")
        if self.editing.get():
            self.entries[0].focus_set()
            self.entries[0].icursor("end")

    def on_formula(self):
        if self._loading:
            return
        texts = {key: var.get() for key, var in self.texts.items()}
        try:
            if self.layer == "shape":
                names = shape_names(texts["x"], texts["y"])
            else:
                names = pattern_names(texts["formula"], texts["along"])
        except ValueError as e:
            self.ok = False
            typed = any(t.strip() for t in texts.values())
            self.set_info(tr("pattern_dialog.can_t_use_it", e=e) if typed else tr("pattern_dialog.type_a_formula"),
                          "#c00000" if typed else "#777")
            return
        old = self.pat["vars"]
        if self.name.get() in [n for _, _, n in self.items]:  # it's not that preset / saved one any more
            self._loading = True
            self.name.set("")
            self._loading = False
        self.pat.update(texts, vars={n: old.get(n, 1.0) for n in names}, preset="")
        if not self.pat.get("along", "x").strip():
            self.pat.pop("along")
        self.pat.pop("name", None)
        self.pat.pop("loop", None)
        self.refresh()

    def works(self, p):
        """ValueError if p's formulas can't be worked out."""
        formula_shape(p) if self.layer == "shape" else formula_loop(p)

    def on_sym(self):
        """Symmetric halves picked: the formula's first half makes the second (one edited by hand too)."""
        if self._loading:
            return
        mode = SYM_CHOICES[[tr(key) for _, key in SYM_CHOICES].index(self.sym.get())][0] or None
        if mode == self.pat.get("sym"):
            return
        if mode:
            self.pat["sym"] = mode
        else:
            self.pat.pop("sym", None)
        if self.pat.get("loop"):
            set_loop_sym(self.pat["loop"], mode)
        self.pat.pop("name", None)  # (not the saved one any more)
        self.pick_current()
        self.own_view = False
        self.refresh()
        self.mark()

    def refresh(self):
        """Number boxes, the preview and the piano roll after a change."""
        names = (["loops"] if self.layer == "pattern" else []) + ([] if self.pat.get("loop") else
                                                                  list(self.pat["vars"]))
        if names != self._names:
            self.build_boxes(names)
        self._loading = True
        for name, (var, e) in self.boxes.items():
            var.set(fmt(self.pat["loops"] if name == "loops" else self.pat["vars"][name]))
            e.config(style="TEntry")
        self.sym.set(sym_label(self.pat.get("sym")))
        self._loading = False
        self.ok = True
        if not self.pat.get("loop"):
            try:
                self.works(self.pat)
            except ValueError as e:
                self.ok = False
                self.set_info(tr("pattern_dialog.can_t_use_it", e=e), "#c00000")
        if self.ok:
            if self.pat.get("loop"):
                self.set_info(tr("pattern_dialog.shape_edited_by_hand") if self.layer == "shape" else
                              tr("pattern_dialog.edited_by_hand"), "#1d6b1d", back=True)
            else:
                self.set_info(tr("pattern_dialog.whole_shape") if self.layer == "shape" else
                              tr("pattern_dialog.one_loop"), "#555")
            if self.layer == "pattern" and self.pat["loops"] >= MAX_LOOPS:  # (only then, user)
                self.set_info(tr("panel_pattern.loops_most", most=fmt(MAX_LOOPS)), GAP_COLOR,
                              back=bool(self.pat.get("loop")))
        self.view = self.view[:3] + (self.loop_len(),) if self.own_view and self.view else None
        self.draw()
        self.show_on_roll()

    def build_boxes(self, names):
        for w in self.numbers.winfo_children():
            w.destroy()
        self.boxes = {}
        self._names = names
        for name in names:
            cell = ttk.Frame(self.numbers)
            cell.pack(side="left", padx=(0, 10))
            lb = ttk.Label(cell, text=tr("panel_pattern.loops") if name == "loops" else name)
            lb.pack(side="left")
            var = tk.StringVar()
            e = ttk.Entry(cell, textvariable=var, width=6)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, n=name: (self.on_number(n), "break")[1])
            e.bind("<FocusOut>", lambda ev, n=name: self.on_number(n))
            loops = name == "loops"
            Scrub(self.host.app, [(e, var, lambda n=name: self.on_number(n))], (1, 10, 0.1) if loops else (0.5, 5, 0.1),
                  0.01 if loops else None, None, label=lb)
            tip = (tr("panel_pattern.loops_tip") if loops else
                   tr("panel_pattern.shape_number_tip" if self.layer == "shape" else self.host.number_tip,
                      name=name))
            for w in (lb, e):
                Tooltip(w, tip)
            self.boxes[name] = (var, e)

    def on_number(self, name):
        if self._loading or name not in self.boxes:
            return
        var, e = self.boxes[name]
        try:
            value = float(calc(var.get()))
            if not math.isfinite(value) or (name == "loops" and value <= 0):
                raise ValueError
            if name != "loops":
                self.works(dict(self.pat, vars=dict(self.pat["vars"], **{name: value})))
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        good(e)
        if name == "loops":
            self.pat["loops"] = min(value, MAX_LOOPS)
        else:
            self.pat["vars"][name] = value
        self.refresh()
        self.mark(("num", name))

    def set_info(self, text, color, back=False):
        self.info.config(text=text, foreground=color)
        if back:
            self.back.pack(side="right")
        else:
            self.back.pack_forget()

    def back_to_formula(self):
        self.pat.pop("loop", None)
        self.refresh()
        self.mark()

    # ------------------------------------------------------------ the piano roll (or the drawer)
    def show_on_roll(self):
        """It on every curve (each keeps its own proportions / mirroring)."""
        if not self.ok and not self.pat.get("loop"):
            return
        for h, before in zip(self.targets, self.before):
            own = h.get(self.layer) or dict(self.host.fresh(h, self.layer), mirror=False)
            new = copy.deepcopy(self.pat)
            new.update(k=own["k"], mirror=own["mirror"])
            if self.layer == "pattern":
                new["scale"] = own.get("scale", 1.0)
            h[self.layer] = new
            self.host.placed(h, self.layer, before)
        self.host.spread()
        self.host.changed(final=False)

    def apply(self):
        if not self.ok and not self.pat.get("loop"):
            return
        self.show_on_roll()
        self.destroy()
        step = "panel_pattern.shape_step" if self.layer == "shape" else "panel_pattern.pattern_step"
        self.host.commit(self.snap, tr(step, name=self.name_of(self.pat)))

    def cancel(self):
        self.destroy()
        self.host.restore(self.snap)

    # ------------------------------------------------------------ the preview
    def loop_len(self):
        """A pattern: one loop's length in keys, on the first selected curve (so the preview looks like the piano
        roll), always as if it had the default Loops (user: the Loops count never changes the preview). A shape: 1
        (its sizes are shares of the curve's length)."""
        if self.layer == "shape":
            return 1.0
        h = self.targets[0]
        own = h.get("pattern") or self.host.fresh(h, "pattern")
        got = self.host.loop_length(h, dict(self.pat, loops=LOOPS_DEFAULT, k=own["k"], scale=own.get("scale", 1.0)))
        return got if got > 1e-6 else 8.0

    def shown_loop(self):
        """The loop / shape as a curve (anchors + handles in along, sideways): the one edited by hand, else fitted
        to the formulas (not kept until something is dragged)."""
        if self.pat.get("loop"):
            return self.pat["loop"]
        if self.layer == "shape":
            u, v = formula_shape(self.pat)
            tol, length = SHAPE_TOLERANCE, 1.0
        else:
            u, v = formula_loop(self.pat)
            tol, length = LOOP_TOLERANCE, self.loop_len()
        corners = []  # (symmetric halves first: then dragging a point moves its partner in the other half too)
        modes = SYM_MODES if self.layer == "shape" else ("mirror", "turn")
        want = self.pat.get("sym")  # (the ones picked are tried first; a closed shape's mirror is "flip")
        want = "flip" if want == "mirror" and self.layer == "shape" and math.dist(
            (u[0], v[0]), (u[-1], v[-1])) < 1e-6 else want
        if want in modes:
            modes = (want,) + tuple(m for m in modes if m != want)
        pts, sym = fit_symmetric([(a * length, b) for a, b in zip(u.tolist(), v.tolist())], tol, corners, modes)
        out = {"pts": [[x / length, y] for x, y in pts]}
        if corners:
            out["sharp"] = corners
        if sym:
            out["sym"] = sym
        if self.layer == "shape" and math.dist(out["pts"][0], out["pts"][-1]) < 1e-6:
            out["pts"][-1] = list(out["pts"][0])  # (a closed shape stays closed)
        return out

    def fit_view(self, loop):
        """Pixels per unit, the left edge, the middle line and the loop's length in units: as it looks on the
        curve, as big as fits."""
        length = self.loop_len()
        pad = 26 * self.scale
        line = sample([tuple(p) for p in loop["pts"]], 32)
        us = [a for a, _ in loop["pts"]] + [a for a, _ in line] + [0.0, 1.0]
        vs = [b for _, b in loop["pts"]] + [b for _, b in line]
        if self.layer == "pattern":  # (the middle line in the middle)
            tall = max([abs(b) for b in vs] + [0.5])
            lo_v, hi_v = -tall, tall
        else:
            lo_v, hi_v = min(vs + [0.0]), max(vs + [0.0])
        lo_u, hi_u = min(us), max(us)
        s = min((self.w - 2 * pad) / max(1e-9, (hi_u - lo_u) * length), (self.h - 2 * pad) / max(1e-9, hi_v - lo_v))
        x0 = self.w / 2 - (lo_u + hi_u) / 2 * length * s
        ym = self.h / 2 + (lo_v + hi_v) / 2 * s
        return s, x0, ym, length

    def to_xy(self, p):
        s, x0, ym, length = self.view
        return x0 + p[0] * length * s, ym - p[1] * s

    def from_xy(self, x, y):
        s, x0, ym, length = self.view
        return [(x - x0) / (length * s), (ym - y) / s]

    def draw(self):
        c = self.canvas
        c.delete("all")
        if not self.ok and not self.pat.get("loop"):
            return
        loop = self.shown_loop()
        if self.view is None:
            self.view = self.fit_view(loop)
        s, x0, ym, length = self.view
        self.draw_grid()
        pts = loop["pts"]
        line = [self.to_xy(p) for p in sample([tuple(p) for p in pts], 32)]
        font = ("Segoe UI", 8)
        if self.layer == "pattern":
            c.create_line(0, ym, self.w, ym, fill="#e0a0a0", dash=(6, 4))  # the curve itself (the origin path)
            width = length * s
            for shift in (-1, 1):  # the loops before and after it, faint
                c.create_line(*[v for x, y in line for v in (x + shift * width, y)], fill="#d8d8e8", width=2)
        else:
            (ax, ay), (bx, by) = self.to_xy((0, 0)), self.to_xy((1, 0))
            c.create_line(ax, ay, bx, by, fill="#e0a0a0", dash=(6, 4))  # the curve from A to B (the origin path)
            c.create_text(ax, ay + 6, text="A", anchor="n", fill="#a05050", font=font)
            c.create_text(bx, by + 6, text="B", anchor="n", fill="#a05050", font=font)
        if self.pat.get("loop"):  # the formula it came from, faint under it
            try:
                u, v = formula_shape(self.pat) if self.layer == "shape" else formula_loop(self.pat)
                c.create_line(*[q for a, b in zip(u.tolist(), v.tolist()) for q in self.to_xy((a, b))],
                              fill="#d0d0d0", width=4)
            except (ValueError, KeyError):
                pass
        c.create_line(*[v for p in line for v in p], fill="#d01010", width=2)
        r = 3.5 * self.scale
        for a, h in handle_lines(pts):
            c.create_line(*self.to_xy(a), *self.to_xy(h), fill="#6080c0")
        for i, kind in pen_handles(pts):
            x, y = self.to_xy(pts[i])
            if kind == "ctrl":
                c.create_oval(x - r + 1, y - r + 1, x + r - 1, y + r - 1, fill="#0050d0", outline="")
            elif kind == "anchor":
                c.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#0050d0")
            else:
                c.create_rectangle(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#c00000")

    def point_at(self, x, y):
        if self.view is None or (not self.ok and not self.pat.get("loop")):
            return None
        pts = self.shown_loop()["pts"]
        best = None
        for i, _ in reversed(pen_handles(pts)):  # (anchors are drawn on top: they win)
            px, py = self.to_xy(pts[i])
            d = math.hypot(px - x, py - y)
            if d <= GRAB * self.scale and (best is None or d < best[0] - 0.5):
                best = (d, i)
        return best[1] if best else None

    def by_hand(self):
        """From now on edited by hand (the formulas' fitted curve to start with). It's no longer the saved one it
        came from (the preset it came from still names it)."""
        if not self.pat.get("loop"):
            self.pat["loop"] = copy.deepcopy(self.shown_loop())
        self.pat.pop("name", None)

    # ------------------------------------------------------------ zooming and moving the view
    def start_pan(self, e):
        self.pan = (e.x, e.y, self.view, False) if self.view else None

    def move_pan(self, e):
        if not self.pan:
            return
        x, y, (s, x0, ym, length), moved = self.pan
        if not moved and math.hypot(e.x - x, e.y - y) < 3 * self.scale:
            return  # (a click, not a drag yet)
        self.pan = (x, y, self.pan[2], True)
        self.own_view = True
        self.view = (s, x0 + e.x - x, ym + e.y - y, length)
        self.draw()

    def middle_release(self, e):
        """Middle-click (not dragged): a new point there, like the piano roll."""
        clicked = self.pan and not self.pan[3]
        self.pan = None
        if clicked:
            self.add_point(e)

    def wheel(self, e):
        """The wheel zooms in and out around the mouse."""
        if not self.view:
            return
        s, x0, ym, length = self.view
        fit_s = self.fit_view(self.shown_loop())[0]
        f = 1.2 ** (e.delta / 120)
        f = max(fit_s / 20, min(fit_s * 200, s * f)) / s
        self.own_view = True
        self.view = (s * f, e.x - (e.x - x0) * f, e.y - (e.y - ym) * f, length)
        self.draw()

    def fit_again(self):
        self.own_view = False
        self.view = None
        self.draw()

    def draw_grid(self):
        """Grid lines at round numbers (x as in the formula, 0 -> 1 along a loop; y in its sizes), a stronger line at
        0 (and at the loop's end), their numbers along the left and bottom edges."""
        c = self.canvas
        s, x0, ym, length = self.view
        font = ("Segoe UI", 7)
        gap = 32 * self.scale  # at least this many pixels between lines
        for axis, per in ((0, length * s), (1, s)):
            raw = gap / max(per, 1e-12)
            step = 10 ** math.floor(math.log10(raw))
            step *= next(m for m in (1, 2, 5, 10) if step * m >= raw)
            lo, hi = sorted([self.from_xy(0, 0)[axis], self.from_xy(self.w, self.h)[axis]])
            for k in range(math.ceil(lo / step), math.floor(hi / step) + 1):
                v = k * step
                strong = abs(v) < step / 2 or (axis == 0 and self.layer == "pattern" and abs(v - 1) < step / 2)
                color = "#d4d4d4" if strong else "#eeeeee"
                if axis == 0:
                    x = self.to_xy((v, 0))[0]
                    c.create_line(x, 0, x, self.h, fill=color)
                    c.create_text(x + 2, self.h - 1, text=fmt(v), anchor="sw", fill="#aaaaaa", font=font)
                else:
                    y = self.to_xy((0, v))[1]
                    c.create_line(0, y, self.w, y, fill=color)
                    c.create_text(2, y - 1, text=fmt(v), anchor="sw", fill="#aaaaaa", font=font)

    def press(self, e):
        self.drag = self.point_at(e.x, e.y)
        if self.drag is None:  # empty space: moves the view
            self.start_pan(e)
        pts = self.shown_loop()["pts"] if self.drag is not None else []
        if self.layer == "shape" and self.drag in (0, len(pts) - 1):
            self.drag = None  # a shape's ends stay on the curve's ends (drag their handles)

    def motion(self, e):
        if self.drag is None:
            return self.move_pan(e)
        self.by_hand()
        c = self.pat["loop"]
        last = len(c["pts"]) - 1
        ends = [list(c["pts"][0]), list(c["pts"][last])]
        axis = held_axis(c, self.to_xy)  # (zooming the preview never changes which way it's mirrored)
        drag_point(c, self.drag, self.from_xy(e.x, e.y), e.state & ALT, self.to_xy, self.from_xy)
        for i, handle, along in ((0, 1, 0.0), (last, last - 1, 1.0)):
            if self.layer == "shape":  # a shape's ends stay where they are
                d = [ends[0 if i == 0 else 1][k] - c["pts"][i][k] for k in (0, 1)]
            else:  # a loop's ends stay at its start and end (only up and down)
                d = [along - c["pts"][i][0], 0.0]
            if d[0] or d[1]:
                for j in (i, handle):
                    c["pts"][j] = [c["pts"][j][0] + d[0], c["pts"][j][1] + d[1]]
        if c.get("sym"):
            if c["sym"] == "mirror" and self.drag in (0, last):  # a mirrored loop's ends stay level
                other, handle = (last, last - 1) if self.drag == 0 else (0, 1)
                dy = c["pts"][self.drag][1] - c["pts"][other][1]
                for j in (other, handle):
                    c["pts"][j] = [c["pts"][j][0], c["pts"][j][1] + dy]
            keep_symmetric(c, self.drag, self.to_xy, axis=axis)
        self.set_info(tr("pattern_dialog.shape_edited_by_hand") if self.layer == "shape" else
                      tr("pattern_dialog.edited_by_hand"), "#1d6b1d", back=True)
        want = ["loops"] if self.layer == "pattern" else []
        if self._names != want:
            self.build_boxes(want)
            if want:
                self.boxes["loops"][0].set(fmt(self.pat["loops"]))
        self.draw()
        self.show_on_roll()

    def release(self, e):
        self.pan = None
        if self.drag is not None:
            self.drag = None
            if not self.own_view:
                self.view = None
            self.draw()
            self.mark()

    def right_click(self, e):
        i = self.point_at(e.x, e.y)
        if i is None:
            return
        self.by_hand()
        if can_delete(self.pat["loop"], i) is None:
            return
        delete_point(self.pat["loop"], i, self.to_xy)
        self.refresh()
        self.mark()

    def add_point(self, e):
        if self.view is None or (not self.ok and not self.pat.get("loop")):
            return
        loop = self.shown_loop()
        seg, t, d = nearest(loop["pts"], self.to_xy, e.x, e.y)
        if d > 3 * GRAB * self.scale:
            return
        self.by_hand()
        if add_anchor(self.pat["loop"], seg, t, self.from_xy(e.x, e.y), self.to_xy):
            self.refresh()
            self.mark()
