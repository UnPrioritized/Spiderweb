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

import numpy as np

from files.lang import tr, trn
from files.mathexpr import as_written, calc, fmt, formula, split_pair
from files import saved_lists
from files.about import USER
from files.safefile import write_text
from notes.bezier import (SYM_MODES, add_anchor, can_delete, delete_point, drag_point, fit_symmetric, handle_lines,
                          held_axis, keep_symmetric, nearest, pen_handles, sample)
from notes.pattern import (AROUND, LOOPS_DEFAULT, PATTERN_PRESETS, PRESET_ALONG, SHAPE_PRESETS, SLICES_MAX,
                           SLICES_MIN, SOFT_DEFAULT, around_centre, clean_loop, clean_pattern_graphs, formula_loop,
                           formula_shape, formula_slice, formula_slice_way, formula_slice_ways, keep_sym, own_slices,
                           pinned_ends, most_loops, new_pattern, new_shape, pattern_name,
                           pattern_names, polar_of, polar_ok, polar_xy, shape_name, shape_names, shape_points,
                           shape_slices, span_numbers, t_span)
from files.clipboard import put_text
from roll.roll_shared import ALT, grab_while_panning
from window import look
from window.formula_host import put_sym, sym_choices, sym_label
from window.formula_paste import FormulaPaste
from window.panel_custom import GAP_COLOR
from window.pattern_graphs import DialogGraphs, graphed, open_graph
from window.widgets import (ListTip, LocalUndo, Scrub, Tooltip, bad, good, pack_shown, read_number, remember_place,
                            wheel_unit)

PATTERNS_FILE = os.path.join(USER, "patterns.json")
OLD_CURVES_FILE = os.path.join(USER, "curves.json")  # the funnel's saved curve formulas (before shapes of curves)
LOOP_TOLERANCE = 0.02    # how closely the anchors + handles follow a pattern's formula when first edited, in keys
SHAPE_TOLERANCE = 0.002  # the same for a shape, as a share of the curve's length
GRAB = 7  # how near (pixels) a point has to be to be dragged
FILE_KEYS = {"pattern": "patterns", "shape": "shapes"}
# the Symbols… window: (group, its symbols); every one is read by mathexpr.as_written (signs) or is a name
SYMBOLS = [("numbers", "πθ"), ("signs", "×÷−²³√"), ("names", "αβγδελμσφω")]
SYMBOL_TIPS = {"π": "pi", "θ": "theta", "×": "times", "÷": "divide", "−": "minus", "²": "squared", "³": "cubed",
               "√": "root"}


def load_patterns(layer="pattern"):
    """The user's saved patterns [{"name", "formula", "vars", maybe "loop"}] or shapes [{"name", "x", "y", "vars",
    maybe "loop"}]."""
    if layer == "shape" and os.path.exists(OLD_CURVES_FILE):
        move_old_curves()

    def clean(p):
        item = {"name": str(p["name"]), "vars": {str(a): float(b) for a, b in dict(p.get("vars", {})).items()}}
        for key in (("x", "y") if layer == "shape" else ("formula", "along")):
            if key != "along" or str(p.get(key, "")).strip():
                item[key] = str(p.get(key, ""))
        span = t_span(p) if layer == "shape" else None
        if span:
            try:
                span_numbers(span)
                item["t_span"] = span
            except ValueError:
                span = None
        if layer == "shape" and polar_ok(p.get("polar")):  # (typed as one polar line: x / y come from it)
            item["polar"] = p["polar"]
            item["x"], item["y"] = polar_xy(p["polar"], span)
        loop = clean_loop(p["loop"]) if isinstance(p.get("loop"), dict) else None
        if loop:
            item["loop"] = loop
        graphs = clean_pattern_graphs(p.get("graphs"), item["vars"]) if layer == "pattern" else None
        if graphs:
            item["graphs"] = graphs
        return keep_sym(item, p)
    return saved_lists.entries(PATTERNS_FILE, FILE_KEYS[layer], clean)


def save_pattern(name, item, layer="pattern"):
    """The saved pattern / shape `name` replaced by item (None: deleted), every other one kept as saved. Returns
    where a damaged file was kept aside (tell the user), else None. OSError when it can't be written."""
    return saved_lists.change(PATTERNS_FILE, FILE_KEYS[layer], lambda n: n == name, [item] if item else ())


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
    # (its own graphs; one saved without any keeps the Loops graph and fitting ones of the pattern it replaces)
    graphs = clean_pattern_graphs(item.get("graphs") or old.get("graphs"), out["vars"])
    if graphs:
        out["graphs"] = graphs
    return keep_sym(out, item)


def saved_shape(item, k):
    """A saved shape (load_patterns("shape")) as a curve's shape."""
    out = {"preset": "", "name": item["name"], "x": item["x"], "y": item["y"], "vars": dict(item["vars"]), "k": k,
           "mirror": False}
    for key in ("polar", "t_span"):
        if item.get(key) is not None:
            out[key] = copy.deepcopy(item[key])
    if item.get("loop"):
        out["loop"] = copy.deepcopy(item["loop"])
    return keep_sym(out, item)


class FormulaDialog(tk.Toplevel):
    """Custom… for the host's (formula_host.py) curves' shape (layer "shape") or pattern along them (layer
    "pattern")."""

    def __init__(self, host, layer="pattern"):
        parent = host.parent
        super().__init__(parent)
        self.layer = layer  # (the holder's key: "shape", "pattern", or a polygon's "pattern2")
        self.kind = "shape" if layer == "shape" else "pattern"
        shape = layer == "shape"
        self.title(tr("pattern_dialog.shape_title") if shape else tr("pattern_dialog.title"))
        self.transient(parent)
        self.resizable(False, False)
        self.host, self.app = host, host.app
        self.targets = host.targets()
        if layer == "pattern2":  # (a second pattern only on top of a first, like the Formula menu)
            self.targets = [h for h in self.targets if h.get("pattern")]
        self.snap = host.snapshot()
        self.before = [copy.deepcopy(h) for h in self.targets]
        self.k = host.fresh(self.targets[0], layer)["k"]
        first = self.targets[0].get(layer)
        self.pat = copy.deepcopy(first) if first else new_shape("circle", self.k) if shape else new_pattern("wave",
                                                                                                          self.k)
        if not first and not shape and host.start_loops():  # (a path's: as many as its dropdown would start with)
            self.pat["loops"] = host.start_loops()
        self.graph_window = None  # a number's graph, while open (pattern_graphs.py)
        self.graph_marks = {}     # number name -> its "…" button, its "× graph" label
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
                                  font=look.font(9))
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_pick)
        ListTip(self.listbox)
        ttk.Button(left, text=tr("pattern_dialog.delete"), command=self.delete).pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        right.pack(side="left", fill="both")
        right.columnconfigure(1, weight=1)
        self.name = tk.StringVar(value=self.pat.get("name") or self.preset_name())
        ttk.Label(right, text=tr("pattern_dialog.name")).grid(row=0, column=0, sticky="w")
        row = ttk.Frame(right)  # Name [ ] Copy formula, Paste formula… (user: near the formula, not at the bottom)
        row.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=1)
        self.name_box = ttk.Entry(row, textvariable=self.name, width=24)
        self.name_box.pack(side="left", fill="x", expand=True)
        b = ttk.Button(row, text=tr("pattern_dialog.copy_formula"), command=self.copy_formula)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("pattern_dialog.copy_formula_tip"))
        b = ttk.Button(row, text=tr("pattern_dialog.paste_formula"), command=self.open_paste)
        b.pack(side="left", padx=(4, 0))
        Tooltip(b, tr("pattern_dialog.paste_formula_tip"))
        self.name_box.bind("<Return>", lambda e: (self.save(), "break")[1])  # (a name typed: Enter saves, user)
        # the formula boxes: y = and along = (a pattern) or x(t) = and y(t) = (a shape)
        keys = ("x", "y") if shape else ("formula", "along")
        self.labels = {"x": tr("pattern_dialog.xt"), "y": tr("pattern_dialog.yt"), "formula": tr("pattern_dialog.y"),
                       "along": tr("pattern_dialog.along"), "polar": tr("pattern_dialog.rt")}
        self.texts, self.entries, self.rows = {}, [], {}
        self.editing = tk.BooleanVar(value=False)
        self.polar = shape and "polar" in self.pat  # (a shape typed as one polar line r(t), see pattern.polar_xy)
        for r, key in enumerate(keys, start=1):
            lb = ttk.Label(right, text=self.labels[key])
            lb.grid(row=r, column=0, sticky="w")
            row = ttk.Frame(right)
            row.grid(row=r, column=1, sticky="ew", padx=(5, 0), pady=1)
            var = self.texts[key] = tk.StringVar(value=self.pat.get(key, ""))
            e = ttk.Entry(row, textvariable=var, width=34, font=look.mono(10), state="readonly")
            e.pack(side="left", fill="x", expand=True)
            self.entries.append(e)
            self.rows[key] = (lb, row, e)
            if key == "along":
                for w in (lb, e):
                    Tooltip(w, tr("pattern_dialog.along_tip"))
            if r == 1:
                b = self.edit_box = ttk.Checkbutton(row, text=tr("pattern_dialog.edit_formula"),
                                                    variable=self.editing, command=self.on_edit_mode)
                b.pack(side="left", padx=(6, 0))
                Tooltip(b, tr("pattern_dialog.edit_formula_tip"))
                b = ttk.Button(row, text=tr("pattern_dialog.symbols"), command=self.open_symbols)
                b.pack(side="left", padx=(6, 0))
                Tooltip(b, tr("pattern_dialog.symbols_tip"))
            e.bind("<FocusIn>", lambda ev, k=key: setattr(self, "last_box", k), add="+")
        if shape:  # the polar line's box: in x(t)'s place while typed that way (y(t)'s row hidden)
            var = self.texts["polar"] = tk.StringVar(value=self.pat.get("polar", ""))
            e = ttk.Entry(self.rows["x"][1], textvariable=var, width=34, font=look.mono(10), state="readonly")
            self.entries.append(e)
            self.rows["polar"] = (self.rows["x"][0], self.rows["x"][1], e)
            Tooltip(e, tr("pattern_dialog.rt_tip"))
            e.bind("<FocusIn>", lambda ev: setattr(self, "last_box", "polar"), add="+")
        self.last_box = None  # the formula box typed in last (the symbols go there)
        self.symbols = None   # the symbols window, while open
        r = len(keys) + 1
        ttk.Label(right, text=tr("pattern_dialog.shape_help") if shape else tr(host.pattern_help),
                  foreground=look.HINT, font=look.font(8), wraplength=int(440 * s),
                  justify="left").grid(row=r, column=0, columnspan=2, sticky="w", pady=(3, 4))
        row = ttk.Frame(right)  # symmetric halves: only the first half of the formula counts
        row.grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(0, 4))
        lb = ttk.Label(row, text=tr("widgets.symmetric_halves"))
        lb.pack(side="left")
        self.sym = tk.StringVar()
        names = [tr(key) for _, key in sym_choices(layer)]
        cb = ttk.Combobox(row, textvariable=self.sym, values=names, state="readonly",
                          width=max(len(n) for n in names))
        cb.pack(side="left", padx=(5, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_sym(e.widget.current()))
        for w in (lb, cb):
            Tooltip(w, tr("pattern_dialog.sym_tip_shape") if shape else tr("pattern_dialog.sym_tip"))
        self.slices_var = tk.StringVar()  # Around the centre: how many slices (empty = follows Petals / 6)
        self.slices_row = ttk.Frame(row)
        lb = ttk.Label(self.slices_row, text=tr("pattern_dialog.slices"))
        lb.pack(side="left", padx=(8, 0))
        e = self.slices_box = ttk.Entry(self.slices_row, textvariable=self.slices_var, width=4)
        e.pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: (self.on_slices(), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_slices())
        Scrub(self.host.app, [(e, self.slices_var, self.on_slices)], (1, 5, 1), SLICES_MIN, SLICES_MAX, label=lb)
        for w in (lb, e):
            Tooltip(w, tr("pattern_dialog.slices_tip", least=SLICES_MIN, most=SLICES_MAX))
        self.slices_after = cb
        self.type_box = None  # a shape: typed as x(t) and y(t), or as one polar line r(t)
        if shape:
            lb = ttk.Label(row, text=tr("pattern_dialog.type_as"))
            lb.pack(side="left", padx=(12, 0))
            names = [tr("pattern_dialog.type_xy"), tr("pattern_dialog.type_polar")]
            cb = self.type_box = ttk.Combobox(row, values=names, state="readonly", width=max(len(n) for n in names))
            cb.pack(side="left", padx=(5, 0))
            cb.bind("<<ComboboxSelected>>", lambda e: self.on_type(e.widget.current() == 1))
            for w in (lb, cb):
                Tooltip(w, tr("pattern_dialog.type_tip"))
        self.span_vars = {}  # a shape: t goes from [ ] to [ ] (usually 0 to 1)
        for i, key in enumerate(("from", "to") if shape else ()):
            lb = ttk.Label(row, text=tr("pattern_dialog.t_" + key))
            lb.pack(side="left", padx=(12 if not i else 4, 0))
            var = tk.StringVar()
            e = ttk.Entry(row, textvariable=var, width=6)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev: (self.on_span(), "break")[1])
            e.bind("<FocusOut>", lambda ev: self.on_span())
            for w in (lb, e):
                Tooltip(w, tr("pattern_dialog.t_span_tip"))
            self.span_vars[key] = (var, e)
        self.flat_vars = {}  # a pattern's Flatten % + Softness % (the preview shows the loop before pressing)
        for key in (() if shape else ("flatten", "soft")):
            lb = ttk.Label(row, text=tr("panel_pattern." + key))
            lb.pack(side="left", padx=(12 if key == "flatten" else 8, 0))
            var = self.flat_vars[key] = tk.StringVar()
            e = ttk.Entry(row, textvariable=var, width=5)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, k=key: (self.on_flat(k), "break")[1])
            e.bind("<FocusOut>", lambda ev, k=key: self.on_flat(k))
            Scrub(self.host.app, [(e, var, lambda k=key: self.on_flat(k))], (5, 25, 1), 0, 100, label=lb)
            for w in (lb, e):
                Tooltip(w, tr("panel_pattern." + key + "_tip") + "\n" + tr("pattern_dialog.flat_preview_tip"))
            self.flat_vars[key] = (var, e)
        r += 1
        self.numbers = ttk.Frame(right)  # (a pattern: Loops, then) a box per name in the formulas
        self.numbers.grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.boxes = {}
        self._names = None
        views = ttk.Frame(right)  # the preview; Around the centre: Slice | Whole side by side (user 2026-11-19)
        views.grid(row=r + 2, column=0, columnspan=2)
        self.canvas = tk.Canvas(views, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                highlightbackground=look.PAT_BORDER)
        self.canvas.grid(row=1, column=0)
        self.slice_title = ttk.Label(views, text=tr("pattern_dialog.slice_view"), foreground=look.HINT,
                                     font=look.font(8))
        self.whole_title = ttk.Label(views, text=tr("pattern_dialog.whole_view"), foreground=look.HINT,
                                     font=look.font(8))
        self.whole = tk.Canvas(views, width=self.h, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                               highlightbackground=look.PAT_BORDER)  # (shown only with Around the centre)
        Tooltip(self.whole, tr("pattern_dialog.whole_view_tip"))
        self.wdrag = None  # (slice, point number) dragged in the Whole view
        self.wview = None  # the Whole view's (pixels per unit, left x, middle y, centre, step angle)
        self.whole_points = []
        self.whole.bind("<ButtonPress-1>", self.whole_press)
        self.whole.bind("<B1-Motion>", self.whole_motion)
        self.whole.bind("<ButtonRelease-1>", self.whole_release)
        self.whole.bind("<Button-3>", self.whole_right_click)
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
        self.info = ttk.Label(info, text="", font=look.font(9), wraplength=int(300 * s), justify="left")
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
        remember_place(self, "formula")
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
        return json.dumps({"pat": self.pat, "name": self.name.get(), "polar": self.polar,
                           "texts": {key: var.get() for key, var in self.texts.items()}})

    def put_state(self, state):
        d = json.loads(state)
        self.pat, self.drag, self.pan, self.polar = d["pat"], None, None, d["polar"]
        self._loading = True
        self.name.set(d["name"])
        for key, var in self.texts.items():
            var.set(d["texts"][key])
        self._loading = False
        self.pick_current()
        self.refresh()
        if any(d["texts"][key] != self.pat.get(key, "") for key in self.shown_keys()):
            self.on_formula()  # (a formula that was being typed and doesn't work yet; only the boxes shown: the
            # hidden r(t) box left filled took every undo for a retyped formula: name + hand edits gone, hunt)

    def shown_keys(self):
        """The formula boxes shown (as typed): r(t), or x(t) + y(t), or a pattern's y + along."""
        return ("polar",) if self.polar else ("x", "y") if self.layer == "shape" else ("formula", "along")

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
        self.saved = load_patterns(self.kind)
        self.items = [("preset", pid, name) for pid, name, _, _ in self.presets()]
        self.items += [("saved", item, item["name"]) for item in self.saved]
        self.listbox.delete(0, "end")
        for kind, _, name in self.items:
            self.listbox.insert("end", name)
            if kind == "preset":
                self.listbox.itemconfig("end", foreground=look.INFO)

    def on_pick(self, _):
        sel = self.listbox.curselection()
        if not sel:
            return
        kind, what, name = self.items[sel[0]]
        if self.layer == "shape":  # (a preset keeps the symmetric halves picked, a saved one has its own)
            self.pat = new_shape(what, self.k, self.pat) if kind == "preset" else saved_shape(what, self.k)
        else:
            keep = {key: self.pat[key] for key in ("loops", "each")}
            self.pat = new_pattern(what, self.k, self.pat) if kind == "preset" else saved_pattern(what, self.k, self.pat)
            self.pat.update(keep)
        self.polar = "polar" in self.pat  # (x / y, unless it was saved typed as polar)
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
        if self.pat.get("t_span"):
            item["t_span"] = list(self.pat["t_span"])
        if self.kind == "pattern":  # (user: the saved list keeps the graphs)
            self.drop_unused_graphs()
            if self.pat.get("graphs"):
                item["graphs"] = copy.deepcopy(self.pat["graphs"])
        keep_sym(item, self.pat)
        if not self.save_file(name, item):
            return
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
        if self.save_file(name, None):
            self.fill_list()

    def save_file(self, name, item):
        """save_pattern, saying what went wrong. True when saved."""
        try:
            kept = save_pattern(name, item, self.kind)
        except OSError as e:
            messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.couldn_t_save", e=e), parent=self)
            return False
        if kept:
            messagebox.showwarning(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.file_broken", path=kept),
                                   parent=self)
        return True

    # ------------------------------------------------------------ the formulas and their numbers
    def on_edit_mode(self):
        for e in self.entries:
            e.config(state="normal" if self.editing.get() else "readonly")
        if self.editing.get():  # (the typing cursor in the first box shown: r(t) while typed as polar)
            e = self.rows[self.shown_keys()[0]][2]
            e.focus_set()
            e.icursor("end")

    def on_type(self, polar):
        """Type as x(t), y(t) / Polar r(t) picked. They never both count: x / y are written from the polar line; typed
        as x / y the polar line is dropped (one still in r * cos / r * sin form gives its r back)."""
        if polar == self.polar:
            return
        self.polar, r = polar, ""
        if polar:
            r = self.pat.get("polar") or polar_of(self.pat.get("x", ""), self.pat.get("y", ""), t_span(self.pat)) or ""
            if r:
                self.pat["polar"] = r
            self._loading = True
            self.texts["polar"].set(r)
            self._loading = False
        else:
            self.pat.pop("polar", None)
        self.refresh()
        if polar and not r:
            self.on_formula()  # ("Type a formula": nothing to show yet)
        if self.polar and not self.editing.get():  # (typing it is what this is for)
            self.editing.set(True)
            self.on_edit_mode()
        self.mark()

    def on_span(self):
        """t's from / to typed (like 0 and 2pi: formulas from websites; 0 and 1 = the usual, none kept)."""
        if self._loading or not self.span_vars:
            return
        span = [var.get().strip() for var, _ in self.span_vars.values()]
        if span == (t_span(self.pat) or ["0", "1"]):
            return
        try:
            lo, hi = span_numbers(span)
            trial = dict(self.pat)
            if (lo, hi) == (0.0, 1.0):
                trial.pop("t_span", None)
            else:
                trial["t_span"] = span
            if self.polar and self.pat.get("polar"):
                trial["x"], trial["y"] = polar_xy(self.pat["polar"], t_span(trial))
            if not trial.get("loop"):
                self.works(trial)
        except ValueError:
            for _, e in self.span_vars.values():
                bad(e)
            return
        self.pat = trial
        self.pat.pop("name", None)  # (not the saved one any more)
        if self.polar:
            self._loading = True
            self.texts["x"].set(self.pat["x"])
            self.texts["y"].set(self.pat["y"])
            self._loading = False
        self.pick_current()
        self.own_view = False
        self.refresh()
        self.mark("span")

    def open_symbols(self):
        """The small window of math symbols (user 2026-11-16: a tester couldn't find δ on their keyboard): a click
        copies one and types it where the formula was being typed (with Edit formula on)."""
        if self.symbols and self.symbols.winfo_exists():
            self.symbols.lift()
            return
        top = self.symbols = tk.Toplevel(self)  # (inside the formula window: its grab lets clicks through)
        top.title(tr("pattern_dialog.symbols_title"))
        top.transient(self)
        top.resizable(False, False)
        body = ttk.Frame(top, padding=8)
        body.pack(fill="both", expand=True)
        for r, (group, signs) in enumerate(SYMBOLS):
            ttk.Label(body, text=tr("pattern_dialog.symbols_" + group), foreground=look.HINT,
                      font=look.font(8)).grid(row=2 * r, column=0, columnspan=8, sticky="w", pady=(6 if r else 0, 1))
            for c, sign in enumerate(signs):
                b = ttk.Button(body, text=sign, width=3, command=lambda s=sign: self.put_symbol(s))
                b.grid(row=2 * r + 1, column=c, padx=1, pady=1)
                tip = tr("pattern_dialog.symbol_" + SYMBOL_TIPS[sign]) if sign in SYMBOL_TIPS else \
                    tr("pattern_dialog.symbol_name", sign=sign)
                Tooltip(b, tip)
        ttk.Label(body, text=tr("pattern_dialog.symbols_hint"), foreground=look.HINT, font=look.font(8),
                  wraplength=int(260 * self.scale), justify="left").grid(row=2 * len(SYMBOLS), column=0, columnspan=8,
                                                                         sticky="w", pady=(8, 0))
        top.bind("<Escape>", lambda e: top.destroy())
        top.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() + 4
        y = self.winfo_rooty()
        top.geometry(f"+{x}+{y}")

    def put_symbol(self, sign):
        """A symbol clicked: on the clipboard, and typed into the formula box last typed in (Edit formula on)."""
        if put_text(sign):
            self.app.remember_clip()
        if not self.editing.get():
            self.set_info(tr("pattern_dialog.symbol_copied", sign=sign), look.INFO)
            return
        key = self.last_box if self.last_box in self.shown_keys() else self.shown_keys()[0]
        e = self.rows[key][2]
        try:
            if e.selection_present():
                e.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        e.insert("insert", sign)
        e.focus_set()

    def show_type(self):
        """The x(t) / y(t) boxes or the polar line's box, as typed."""
        if not self.type_box:
            return
        self.type_box.current(1 if self.polar else 0)
        lb, row, x_entry = self.rows["x"]
        r_entry = self.rows["polar"][2]
        shown, hidden = (r_entry, x_entry) if self.polar else (x_entry, r_entry)
        if not shown.winfo_ismapped():
            hidden.pack_forget()
            shown.pack(side="left", fill="x", expand=True, before=self.edit_box)
        lb.config(text=self.labels["polar" if self.polar else "x"])
        for w in self.rows["y"][:2]:
            if self.polar:
                w.grid_remove()
            else:
                w.grid()

    def copy_formula(self):
        """The formula as text with the boxes' numbers in place of their names (to share it or keep it)."""
        values = self.pat["vars"]
        by_hand = bool(self.pat.get("loop"))
        if not self.ok and not by_hand:  # (a formula that doesn't work yet, or an empty r(t) box)
            self.set_info(tr("pattern_dialog.copy_nothing"), look.ERROR_DARK)
            return

        def exact(v):  # (in full, hunt: the boxes' 3 places copied depth 0.0004 as 0 = a circle)
            s = repr(float(v))
            s = s[:-2] if s.endswith(".0") else s
            return f"({s})" if v < 0 else s

        var = "t" if self.layer == "shape" else "x"

        def filled(text):  # (names as the formula reader takes them: any letter first, like élan or 深さ; in the
            # form it's READ in, hunt: "2a cos(t)", "\alpha", "x = ..." copied as typed didn't read back)
            text = re.sub(r"\s+", " ", as_written(text, var))
            return re.sub(r"\b[^\W\d]\w*\b", lambda m: exact(values[m.group()]) if m.group() in values else m.group(),
                          text)
        lines = [f"{self.labels[key]} {filled(self.texts[key].get())}" for key in self.shown_keys()
                 if key != "along" or self.texts[key].get().strip()]
        span = t_span(self.pat) if self.layer == "shape" else None
        if span:
            lines.append(tr("pattern_dialog.copy_span", lo=span[0], hi=span[1]))
        if not put_text("\n".join(lines)):
            self.set_info(tr("pattern_dialog.clipboard_busy"), look.ERROR_DARK, back=by_hand)
            return
        self.app.remember_clip()  # (so Ctrl+V on the piano roll doesn't take it for new shapes)
        notes = [tr("pattern_dialog.copied")]
        if by_hand:
            notes.append(tr("pattern_dialog.copied_by_hand"))
        if self.kind == "pattern" and graphed(self.pat):
            notes.append(tr("pattern_dialog.copied_graphs"))
        self.set_info(" ".join(notes), look.GOOD, back=by_hand)

    def open_paste(self):
        """Paste formula…: its own window (formula_paste.py); Use it comes back to use_pasted."""
        self.drop_graph()
        FormulaPaste(self)

    def use_pasted(self, pat):
        """A pasted formula (formula_paste.pasted_pattern) in place of everything typed before (user): one undo step."""
        self.pat, self.polar, self.drag, self.pan = pat, "polar" in pat, None, None
        self._loading = True
        self.name.set("")
        for key, var in self.texts.items():
            var.set(pat.get(key, ""))
        self._loading = False
        self.ok, self.own_view = True, False
        self.pick_current()
        self.refresh()
        self.mark()
        if self.ok:
            self.set_info(tr("pattern_dialog.pasted"), look.GOOD)

    def on_formula(self):
        if self._loading:
            return
        texts = {key: var.get() for key, var in self.texts.items()}
        was, self.box_was = getattr(self, "box_was", {}), dict(texts)
        if self.layer == "shape" and not self.polar:  # a pasted point "(x(t), y(t))" (graphing sites): both boxes
            # (only when it came in at once, 2+ signs more: backspacing "max(t, 1)" passes "(t, 1)" = wiped y, hunt)
            grew = [k for k in ("x", "y") if len(texts[k]) >= len(was.get(k, texts[k])) + 2]
            pair = next((p for p in (split_pair(texts[k]) for k in grew) if p), None)
            if pair:
                texts["x"], texts["y"] = pair
                self._loading = True
                self.texts["x"].set(pair[0])
                self.texts["y"].set(pair[1])
                self._loading = False
                self.box_was = dict(texts)
        if self.polar:  # (x / y written from the polar line)
            texts["x"], texts["y"] = polar_xy(texts["polar"], t_span(self.pat))
        try:
            if self.layer == "shape":
                if self.polar and not texts["polar"].strip():
                    raise ValueError
                if self.polar:  # (on its own first: "1) + (2" only worked inside the brackets around it, hunt)
                    shape_names(texts["polar"], "0")
                names = shape_names(texts["x"], texts["y"])
            else:
                names = pattern_names(texts["formula"], texts["along"])
        except ValueError as e:
            self.ok = False
            typed = any(texts[key].strip() for key in (("polar",) if self.polar else
                                                         [k for k in texts if k != "polar"]))
            self.set_info(tr("pattern_dialog.can_t_use_it", e=e) if typed else tr("pattern_dialog.type_a_formula"),
                          look.ERROR_DARK if typed else look.HINT)
            bare = ["loops"] if self.kind == "pattern" else []
            if not typed and self._names != bare:  # (all wiped: its number boxes go too; values come back if retyped)
                self.build_boxes(bare)
                for var, _ in self.boxes.values():
                    var.set(fmt(self.pat["loops"]))
                self.lay_cells()
            return
        old = self.pat["vars"]
        if self.name.get() in [n for _, _, n in self.items]:  # it's not that preset / saved one any more
            self._loading = True
            self.name.set("")
            self._loading = False
        if self.layer == "shape":
            if self.polar:
                self._loading = True  # (x / y's boxes show what it writes, for when it's typed that way again)
                self.texts["x"].set(texts["x"])
                self.texts["y"].set(texts["y"])
                self._loading = False
            else:
                texts.pop("polar")
                self.pat.pop("polar", None)  # (typed as x / y: no polar line any more)
        self.pat.update(texts, vars={n: old.get(n, 1.0) for n in names}, preset="")
        if not self.pat.get("along", "x").strip():
            self.pat.pop("along")
        self.pat.pop("name", None)
        self.pat.pop("loop", None)
        self.refresh()

    def works(self, p):
        """ValueError if p's formulas can't be worked out."""
        formula_shape(p) if self.layer == "shape" else formula_loop(p)

    def on_sym(self, picked=-1):
        """Symmetric halves picked: the formula's first half makes the second (one edited by hand too). picked = the
        place in the list (two choices translated alike stay apart); -1: found by its word."""
        if self._loading:
            return
        choices = sym_choices(self.layer)
        if picked < 0:
            picked = [tr(key) for _, key in choices].index(self.sym.get())
        mode = choices[picked][0] or None
        if mode == self.pat.get("sym"):
            return
        put_sym(self.pat, mode)
        self.pat.pop("name", None)  # (not the saved one any more)
        self.view = None
        self.pick_current()
        self.own_view = False
        self.refresh()
        self.mark()

    def on_slices(self):
        """Slices typed or stepped (Around the centre): a typed count stays; empty = follows Petals again (else 6)."""
        if self._loading or self.pat.get("sym") != AROUND:
            return
        text = self.slices_var.get().strip()
        if not text:
            value = None
        else:
            value = read_number(text, SLICES_MIN, SLICES_MAX)
            if value is None or value != int(value):
                bad(self.slices_box)
                return
            value = int(value)
        good(self.slices_box)
        if value == self.pat.get("slices") or value is not None and "slices" not in self.pat \
                and value == shape_slices(self.pat):
            if value is None:
                self.slices_var.set(str(shape_slices(self.pat)))
            return
        own = (self.pat.get("loop") or {}).get("own")
        if own:  # (user 2026-11-19: ask first; yes = every slice follows again)
            if not messagebox.askyesno(tr("pattern_dialog.slices_title"), trn("pattern_dialog.slices_unlink_ask",
                                                                                len(own), n=len(own)), parent=self):
                self.slices_var.set(str(shape_slices(self.pat)))
                return
            self.pat["loop"].pop("own")
        if value is None:
            self.pat.pop("slices", None)
        else:
            self.pat["slices"] = value
        self.pat.pop("name", None)  # (not the saved one any more)
        self.view = None
        self.refresh()
        self.mark(("slices",))

    def on_flat(self, key):
        """Flatten % / Softness % typed or stepped (0 Flatten = none)."""
        if self._loading:
            return
        var, e = self.flat_vars[key]
        value = read_number(var.get(), 0, 100)
        if value is None:
            bad(e)
            return
        good(e)
        if self.pat.get(key, SOFT_DEFAULT if key == "soft" else 0.0) == value:
            return
        if key == "flatten" and not value:
            self.pat.pop("flatten", None)
        else:
            self.pat[key] = value
        self.pat.pop("name", None)  # (not the saved one any more)
        self.refresh()
        self.mark(("flat", key))

    def refresh(self):
        """Number boxes, the preview and the piano roll after a change."""
        names = (["loops"] if self.kind == "pattern" else []) + ([] if self.pat.get("loop") else
                                                                  list(self.pat["vars"]))
        if names != self._names:
            self.build_boxes(names)
        self._loading = True
        for name, (var, e) in self.boxes.items():
            var.set(fmt(self.pat["loops"] if name == "loops" else self.pat["vars"][name]))
            e.config(style="TEntry")
        self.sym.set(sym_label(self.pat.get("sym")))
        around = self.around()
        if around:
            self.slices_var.set(str(shape_slices(self.pat)))
            good(self.slices_box)
        pack_shown(self.slices_row, around, side="left", after=self.slices_after)
        for w, place in ((self.slice_title, dict(row=0, column=0, sticky="w")),
                         (self.whole_title, dict(row=0, column=1, sticky="w", padx=(6, 0))),
                         (self.whole, dict(row=1, column=1, padx=(6, 0)))):
            if around and not w.winfo_manager():
                w.grid(**place)
            elif not around and w.winfo_manager():
                w.grid_forget()
        self.show_type()
        self.box_was = {key: var.get() for key, var in self.texts.items()}  # (what a paste is measured against)
        span = t_span(self.pat) or ["0", "1"]
        for (var, e), text in zip(self.span_vars.values(), span):
            var.set(text)
            good(e)  # (a wrong one typed goes back to this, never left red)
        for key, (var, e) in self.flat_vars.items():
            var.set(fmt(self.pat.get(key, SOFT_DEFAULT if key == "soft" else 0.0)))
            e.config(style="TEntry")
        self._loading = False
        for name, (b, mark) in self.graph_marks.items():
            pack_shown(mark, name in graphed(self.pat), side="left", padx=(3, 0), after=b)
        self.lay_cells()
        if self.graph_window:
            self.graph_window.sync()
        self.ok = True
        if not self.pat.get("loop"):
            try:
                self.works(self.pat)
            except ValueError as e:
                self.ok = False
                self.set_info(tr("pattern_dialog.can_t_use_it", e=e), look.ERROR_DARK)
        if self.ok:
            if self.pat.get("loop"):
                self.set_info(tr("pattern_dialog.shape_edited_by_hand") if self.layer == "shape" else
                              tr("pattern_dialog.edited_by_hand"), look.GOOD, back=True)
                if self.around():
                    self.show_follow()
            else:
                self.set_info(tr("pattern_dialog.one_slice") if self.around() else tr("pattern_dialog.whole_shape")
                              if self.layer == "shape" else tr("pattern_dialog.one_loop"), look.INFO)
            if self.kind == "pattern" and self.pat["loops"] >= self.most_loops():  # (only then, user)
                self.set_info(tr("panel_pattern.loops_most", most=fmt(self.most_loops())), GAP_COLOR,
                              back=bool(self.pat.get("loop")))
        self.view = self.view[:3] + (self.loop_len(),) if self.own_view and self.view else None
        self.draw()
        self.show_on_roll()

    def build_boxes(self, names):
        for w in self.numbers.winfo_children():
            w.destroy()
        self.boxes, self.graph_marks, self.cells, self.cell_lines, self.cells_laid = {}, {}, [], [], None
        self._names = names
        for name in names:
            cell = ttk.Frame(self.numbers)
            self.cells.append(cell)
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
            if self.kind == "pattern":  # its graph along the line ("× graph" in blue while it has one)
                b = ttk.Button(cell, text="…", width=2, command=lambda n=name: self.open_graph(n))
                b.pack(side="left", padx=(2, 0))
                Tooltip(b, tr("pattern_graph.loops_button_tip" if loops else "pattern_graph.button_tip"))
                self.graph_marks[name] = (b, ttk.Label(cell, text=tr("panel_pattern.x_graph"), foreground=look.VALUE))
            tip = (tr("panel_pattern.loops_tip") if loops else
                   tr("panel_pattern.shape_number_tip" if self.layer == "shape" else self.host.number_tip,
                      name=name))
            for w in (lb, e):
                Tooltip(w, tip)
            self.boxes[name] = (var, e)

    def lay_cells(self):
        """The number boxes side by side, on as many lines as the preview's width holds (like words: many numbers,
        each with "…" and "× graph", made the window wider than the screen)."""
        cells = getattr(self, "cells", [])
        if not cells:
            return
        self.numbers.update_idletasks()
        room = self.canvas.winfo_reqwidth()
        lines, used = [], None
        for i, c in enumerate(cells):
            w = c.winfo_reqwidth() + 10
            if used is None or used + w > room:
                lines.append([i])
                used = w
            else:
                lines[-1].append(i)
                used += w
        laid = [tuple(line) for line in lines]
        if laid == self.cells_laid:
            return
        self.cells_laid = laid
        for c in cells:
            c.pack_forget()
        for f in self.cell_lines:
            f.destroy()
        self.cell_lines = []
        for line in lines:
            f = ttk.Frame(self.numbers)
            f.pack(anchor="w")
            self.cell_lines.append(f)
            for i in line:
                cells[i].pack(in_=f, side="left", padx=(0, 10))
                cells[i].lift(f)  # (the line frame is made later: it would cover the cell)

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
            self.pat["loops"] = min(value, self.most_loops())
        else:
            self.pat["vars"][name] = value
        self.refresh()
        self.mark(("num", name))

    def most_loops(self):
        """The most Loops: the least any of the curves can take (Each piece: their pieces share it)."""
        return min(most_loops(h, self.pat["each"]) for h in self.targets)

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
            if self.kind == "pattern":
                new["scale"] = own.get("scale", 1.0)
            h[self.layer] = new
            self.host.placed(h, self.layer, before)
        self.host.spread()
        self.host.changed(final=False)

    def open_graph(self, name):
        """The "…" after a number: its graph along the line (pattern_graphs.py)."""
        open_graph(self, DialogGraphs(self, self.graph_marks[name][0]), name)

    def drop_graph(self):
        """The graph window shut (what it has shown so far is in self.pat already)."""
        gw, self.graph_window = self.graph_window, None
        if gw:
            if gw.job:
                gw.after_cancel(gw.job)
                gw.store()
            gw.destroy()

    def apply(self):
        if not self.ok and not self.pat.get("loop"):
            return
        self.drop_graph()
        self.drop_unused_graphs()
        self.show_on_roll()
        self.destroy()
        step = ("panel_pattern.shape_step" if self.layer == "shape" else "panel_pattern.pattern2_step"
                if self.layer == "pattern2" else "panel_pattern.pattern_step")
        self.host.commit(self.snap, tr(step, name=self.name_of(self.pat)))

    def drop_unused_graphs(self):
        """Graphs of numbers a typed formula no longer has go (kept while typing: a name half typed comes back
        with its graph); the Loops graph stays."""
        if self.kind != "pattern":
            return
        gs = clean_pattern_graphs(self.pat.get("graphs"), {} if self.pat.get("loop") else self.pat["vars"])
        if gs:
            self.pat["graphs"] = gs
        else:
            self.pat.pop("graphs", None)

    def cancel(self):
        self.drop_graph()
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
        own = h.get(self.layer) or self.host.fresh(h, self.layer)
        got = self.host.loop_length(h, dict(self.pat, loops=LOOPS_DEFAULT, k=own["k"], scale=own.get("scale", 1.0)),
                                    layer=self.layer)
        return got if got > 1e-6 else 8.0

    def shown_loop(self):
        """The loop / shape as a curve (anchors + handles in along, sideways): the one edited by hand, else fitted
        to the formulas (not kept until something is dragged)."""
        if self.pat.get("loop"):
            return self.pat["loop"]
        around = self.layer == "shape" and self.pat.get("sym") == AROUND
        if around:  # (Around the centre: only its first slice is edited, the rest are copies)
            u, v = formula_slice(self.pat)
            tol, length = SHAPE_TOLERANCE, 1.0
        elif self.layer == "shape":
            u, v = formula_shape(self.pat)
            tol, length = SHAPE_TOLERANCE, 1.0
        else:
            u, v = formula_loop(self.pat)
            tol, length = LOOP_TOLERANCE, self.loop_len()
        corners = []  # (symmetric halves first: then dragging a point moves its partner in the other half too)
        modes = SYM_MODES if self.layer == "shape" else ("mirror", "turn")
        want = None if around else self.pat.get("sym")  # (the ones picked are tried first; a closed shape's mirror is "flip")
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
        if around:
            out["slice"] = True
            out["way"] = formula_slice_way(self.pat)  # (the formula's way round, kept: see pattern._around)
            p = out["pts"]  # (the end handles along the formula's own way at the ends: the fit leaves them a few
            ways = formula_slice_ways(self.pat)  # degrees off, so a smooth join between copies looked like a corner)
            for end, handle, d in ((0, 1, ways[0]), (len(p) - 1, len(p) - 2, ways[1])):
                n, length = math.hypot(*d), math.dist(p[end], p[handle])
                if n > 1e-12 and length > 1e-12:
                    p[handle] = [p[end][0] + d[0] / n * length, p[end][1] + d[1] / n * length]
        elif self.layer == "shape" and math.dist(out["pts"][0], out["pts"][-1]) < 1e-6:
            out["pts"][-1] = list(out["pts"][0])  # (a closed shape stays closed)
        return out

    def around(self):
        """Around the centre picked (a shape): the preview edits ONE slice, the Whole view shows the rest."""
        return self.layer == "shape" and self.pat.get("sym") == AROUND

    def slice_centre(self, loop):
        """(centre, step angle) the slice turns round (pattern.around_centre: its ends make it)."""
        way = loop.get("way") if self.pat.get("loop") else formula_slice_way(self.pat)
        return around_centre(np.asarray(sample([tuple(p) for p in loop["pts"]], 32), float), shape_slices(self.pat),
                             way)

    def fit_view(self, loop):
        """Pixels per unit, the left edge, the middle line and the loop's length in units: as it looks on the
        curve, as big as fits. Around the centre: the slice and its centre (no A to B line)."""
        length = self.loop_len()
        pad = 26 * self.scale
        line = sample([tuple(p) for p in loop["pts"]], 32)
        us = [a for a, _ in loop["pts"]] + [a for a, _ in line]
        vs = [b for _, b in loop["pts"]] + [b for _, b in line]
        if self.around():
            (cu, cv), _ = self.slice_centre(loop)
            us.append(cu)
            vs.append(cv)
        else:
            us += [0.0, 1.0]
        if self.kind == "pattern":  # (the middle line in the middle)
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

    def draw_whole(self):
        """Around the centre: the Whole view = the whole shape as the curve gets it (A to B), the slice being edited
        drawn over it in the handles' colour."""
        w = self.whole
        w.delete("all")
        self.whole_points = []  # (slice, point number, x, y) that can be dragged here
        if not self.around() or (not self.ok and not self.pat.get("loop")):
            return
        try:
            u, v = shape_points(dict(self.pat, mirror=False))
            loop = self.shown_loop()
            centre, step = self.slice_centre(loop)
        except (ValueError, KeyError):
            return
        if self.wdrag is None or self.wview is None:  # (held still while a point here is dragged)
            size, pad = self.h, 18 * self.scale
            lo_u, hi_u = min(u.min(), 0.0), max(u.max(), 1.0)
            lo_v, hi_v = min(v.min(), 0.0), max(v.max(), 0.0)
            s = (size - 2 * pad) / max(hi_u - lo_u, hi_v - lo_v, 1e-9)
            self.wview = (s, size / 2 - (lo_u + hi_u) / 2 * s, size / 2 + (lo_v + hi_v) / 2 * s, centre, step)
        else:
            self.wview = self.wview[:3] + (centre, step)
        xy = self.whole_xy
        font = look.font(8)
        (ax, ay), (bx, by) = xy((0, 0)), xy((1, 0))
        w.create_line(ax, ay, bx, by, fill=look.PAT_ORIGIN, dash=(6, 4))
        w.create_text(ax, ay + 6, text="A", anchor="n", fill=look.PAT_AB, font=font)
        w.create_text(bx, by + 6, text="B", anchor="n", fill=look.PAT_AB, font=font)
        w.create_line(*[q for a, b in zip(u.tolist(), v.tolist()) for q in xy((a, b))], fill=look.PAT_LINE, width=2)
        own = self.own_now()
        r = 2.5 * self.scale
        for k in range(shape_slices(self.pat)):
            pts = pinned_ends(own[k]["pts"], loop["pts"]) if k in own else loop["pts"]
            if k in own or k == 0:  # (an unlinked one in its own colour, the shared one in the handles' blue)
                line = sample([tuple(p) for p in pts], 32)
                w.create_line(*[q for p in line for q in xy(p, k)], fill=look.PAT_OWN if k in own else look.HANDLE,
                              width=3)
            if k in own:
                for a, h in handle_lines(pts):
                    w.create_line(*xy(a, k), *xy(h, k), fill=look.PAT_ARM)
            for i, kind in pen_handles(pts):
                if i in (0, len(pts) - 1) or kind == "ctrl" and k not in own:
                    continue  # (the joins stay put; a following slice shows only its anchors)
                x, y = xy(pts[i], k)
                self.whole_points.append((k, i, x, y))
                if k in own:
                    w.create_oval(x - r, y - r, x + r, y + r, fill=look.PAT_OWN if kind != "ctrl" else look.HANDLE_FILL,
                                  outline=look.PAT_OWN)
                else:
                    w.create_oval(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.PAT_ARM)

    def own_now(self):
        """The unlinked slices that count now: {number: curve} (pattern.own_slices, past the slice count left out)."""
        n = shape_slices(self.pat)
        return {k: c for k, c in own_slices(self.pat).items() if k < n}

    def whole_xy(self, p, k=0):
        """Whole view pixels of a point of slice 0's place, turned on to slice k's."""
        s, x0, ym, centre, step = self.wview
        if k:
            c, si = math.cos(step * k), math.sin(step * k)
            dx, dy = p[0] - centre[0], p[1] - centre[1]
            p = (centre[0] + dx * c - dy * si, centre[1] + dx * si + dy * c)
        return x0 + p[0] * s, ym - p[1] * s

    def whole_from_xy(self, x, y, k=0):
        """whole_xy's way back: a Whole view pixel -> slice 0's place, turned back from slice k's."""
        s, x0, ym, centre, step = self.wview
        p = ((x - x0) / s, (ym - y) / s)
        if k:
            c, si = math.cos(-step * k), math.sin(-step * k)
            dx, dy = p[0] - centre[0], p[1] - centre[1]
            p = (centre[0] + dx * c - dy * si, centre[1] + dx * si + dy * c)
        return [p[0], p[1]]

    def whole_press(self, e):
        best = None
        for k, i, x, y in self.whole_points:
            d = math.hypot(x - e.x, y - e.y)
            if d <= GRAB * self.scale and (best is None or d < best[0] - 0.5):
                best = (d, k, i)
        self.wdrag = best[1:] if best else None

    def whole_motion(self, e):
        """A point of slice k dragged in the Whole view: that slice is unlinked (its own copy of the slice, user
        2026-11-19), the others keep following."""
        if self.wdrag is None:
            return
        k, i = self.wdrag
        self.by_hand()
        loop = self.pat["loop"]
        own = loop.setdefault("own", {})
        if str(k) not in own:
            own[str(k)] = {key: copy.deepcopy(loop[key]) for key in ("pts", "sharp") if key in loop}
        c = own[str(k)]
        drag_point(c, i, self.whole_from_xy(e.x, e.y, k), e.state & ALT, lambda p: self.whole_xy(p, k),
                   lambda x, y: self.whole_from_xy(x, y, k))
        if self._names != []:  # (edited by hand: the formula's numbers don't count any more, like a drag on the left)
            self.build_boxes([])
        self.show_follow()
        self.draw()
        self.show_on_roll()

    def whole_release(self, e):
        if self.wdrag is not None:
            self.wdrag = None
            self.draw()
            self.mark()

    def whole_right_click(self, e):
        """Right-click an unlinked slice in the Whole view: Follow the slice again (its own edit goes)."""
        if self.wview is None:
            return
        loop = self.pat.get("loop") or {}
        best = None
        for k, c in self.own_now().items():
            line = sample([tuple(p) for p in pinned_ends(c["pts"], loop["pts"])], 16)
            d = min(math.hypot(x - e.x, y - e.y) for x, y in (self.whole_xy(p, k) for p in line))
            if d <= 3 * GRAB * self.scale and (best is None or d < best[0]):
                best = (d, k)
        if best is None:
            return
        m = tk.Menu(self, tearoff=0)
        m.add_command(label=tr("pattern_dialog.follow_again"), command=lambda k=best[1]: self.follow_again(k))
        m.tk_popup(e.x_root, e.y_root)

    def follow_again(self, k):
        own = (self.pat.get("loop") or {}).get("own") or {}
        own.pop(str(k), None)
        if not own:
            self.pat["loop"].pop("own", None)
        self.refresh()
        self.mark()

    def show_follow(self):
        """The info line: "4 of 5 slices follow the slice" while some are unlinked."""
        n, own = shape_slices(self.pat), len(self.own_now())
        if own:
            self.set_info(tr("pattern_dialog.slices_follow", n=n - own, total=n), look.GOOD, back=True)

    def draw(self):
        c = self.canvas
        c.delete("all")
        self.draw_whole()
        if not self.ok and not self.pat.get("loop"):
            return
        loop = self.shown_loop()
        if self.view is None:
            self.view = self.fit_view(loop)
        s, x0, ym, length = self.view
        self.draw_grid()
        pts = loop["pts"]
        line = [self.to_xy(p) for p in sample([tuple(p) for p in pts], 32)]
        font = look.font(8)
        if self.kind == "pattern":
            c.create_line(0, ym, self.w, ym, fill=look.PAT_ORIGIN, dash=(6, 4))  # the curve itself (the origin path)
            width = length * s
            for shift in (-1, 1):  # the loops before and after it, faint
                c.create_line(*[v for x, y in line for v in (x + shift * width, y)], fill=look.PAT_NEIGHBOUR, width=2)
        elif self.around():  # the wedge the slice fills: faint lines from the centre to its ends (user)
            centre, _ = self.slice_centre(loop)
            cx, cy = self.to_xy(centre)
            for end in (pts[0], pts[-1]):
                c.create_line(cx, cy, *self.to_xy(end), fill=look.PAT_ORIGIN, dash=(6, 4))
            r = 3 * self.scale
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=look.PAT_AB)
        else:
            (ax, ay), (bx, by) = self.to_xy((0, 0)), self.to_xy((1, 0))
            c.create_line(ax, ay, bx, by, fill=look.PAT_ORIGIN, dash=(6, 4))  # the curve from A to B (the origin path)
            c.create_text(ax, ay + 6, text="A", anchor="n", fill=look.PAT_AB, font=font)
            c.create_text(bx, by + 6, text="B", anchor="n", fill=look.PAT_AB, font=font)
        if self.pat.get("loop"):  # the formula it came from, faint under it
            try:
                u, v = formula_slice(self.pat) if self.around() else formula_shape(self.pat) \
                    if self.layer == "shape" else formula_loop(self.pat)
                c.create_line(*[q for a, b in zip(u.tolist(), v.tolist()) for q in self.to_xy((a, b))],
                              fill=look.PAT_FORMULA, width=4)
            except (ValueError, KeyError):
                pass
        if self.all_unlinked():  # (user: the Slice view fades when no slice follows it any more)
            c.create_line(*[v for p in line for v in p], fill=look.PAT_FORMULA, width=2)
            c.create_text(self.w / 2, 8 * self.scale, text=tr("pattern_dialog.none_follow"), anchor="n",
                          fill=look.PAT_GRID_TEXT, font=font)
            return
        c.create_line(*[v for p in line for v in p], fill=look.PAT_LINE, width=2)
        r = 3.5 * self.scale
        for a, h in handle_lines(pts):
            c.create_line(*self.to_xy(a), *self.to_xy(h), fill=look.PAT_ARM)
        for i, kind in pen_handles(pts):
            x, y = self.to_xy(pts[i])
            if kind == "ctrl":
                c.create_oval(x - r + 1, y - r + 1, x + r - 1, y + r - 1, fill=look.HANDLE, outline="")
            elif kind == "anchor":
                c.create_oval(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.HANDLE)
            else:
                c.create_rectangle(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.HANDLE_FIXED)

    def all_unlinked(self):
        return self.around() and len(self.own_now()) >= shape_slices(self.pat)

    def point_at(self, x, y):
        if self.view is None or (not self.ok and not self.pat.get("loop")) or self.all_unlinked():
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
        f = 1.2 ** (e.delta / wheel_unit())
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
        font = look.font(7)
        gap = 32 * self.scale  # at least this many pixels between lines
        for axis, per in ((0, length * s), (1, s)):
            raw = gap / max(per, 1e-12)
            step = 10 ** math.floor(math.log10(raw))
            step *= next(m for m in (1, 2, 5, 10) if step * m >= raw)
            lo, hi = sorted([self.from_xy(0, 0)[axis], self.from_xy(self.w, self.h)[axis]])
            for k in range(math.ceil(lo / step), math.floor(hi / step) + 1):
                v = k * step
                strong = abs(v) < step / 2 or (axis == 0 and self.kind == "pattern" and abs(v - 1) < step / 2)
                color = look.PAT_GRID_STRONG if strong else look.PAT_GRID
                if axis == 0:
                    x = self.to_xy((v, 0))[0]
                    c.create_line(x, 0, x, self.h, fill=color)
                    c.create_text(x + 2, self.h - 1, text=fmt(v), anchor="sw", fill=look.PAT_GRID_TEXT, font=font)
                else:
                    y = self.to_xy((0, v))[1]
                    c.create_line(0, y, self.w, y, fill=color)
                    c.create_text(2, y - 1, text=fmt(v), anchor="sw", fill=look.PAT_GRID_TEXT, font=font)

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
        join = self.smooth_join(c) if self.around() and not e.state & ALT and self.drag in (1, last - 1) else None
        drag_point(c, self.drag, self.from_xy(e.x, e.y), e.state & ALT, self.to_xy, self.from_xy)
        if join:  # (Around the centre: the handle on the join's other side follows, turned; Alt = a corner)
            self.follow_join(c, join)
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
                      tr("pattern_dialog.edited_by_hand"), look.GOOD, back=True)
        want = ["loops"] if self.kind == "pattern" else []
        if self._names != want:
            self.build_boxes(want)
            if want:
                self.boxes["loops"][0].set(fmt(self.pat["loops"]))
        self.draw()
        self.show_on_roll()

    def smooth_join(self, c):
        """Around the centre, dragging an end's handle: (turn, length of the other end's handle) when the join between
        copies is smooth (the end's handle points straight on from the next copy's start handle), else None (a corner,
        like a sharp petal tip: the handles stay apart)."""
        pts = c["pts"]
        last = len(pts) - 1
        _, step = self.slice_centre(c)
        turn = np.array([[math.cos(step), -math.sin(step)], [math.sin(step), math.cos(step)]])
        a = turn @ (np.asarray(pts[1], float) - pts[0])  # (the next copy's start handle, at this slice's end)
        b = np.asarray(pts[last - 1], float) - pts[last]
        la, lb = np.hypot(*a), np.hypot(*b)
        if la < 1e-12 or lb < 1e-12 or np.hypot(*(a / la + b / lb)) > 0.02:
            return None
        return turn, lb if self.drag == 1 else la

    def follow_join(self, c, join):
        """The handle across the join turned to point straight on from the dragged one, keeping its own length."""
        turn, length = join
        pts = c["pts"]
        last = len(pts) - 1
        if self.drag == 1:  # (start handle dragged: the end's handle = opposite of it, turned on)
            d, at, other = turn @ (np.asarray(pts[1], float) - pts[0]), pts[last], last - 1
        else:  # (end handle dragged: the start's handle = opposite of it, turned back)
            d, at, other = turn.T @ (np.asarray(pts[last - 1], float) - pts[last]), pts[0], 1
        n = np.hypot(*d)
        if n > 1e-12:
            pts[other] = [at[0] - d[0] / n * length, at[1] - d[1] / n * length]

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
        if i is None or can_delete(self.shown_loop(), i) in (None, "middle"):
            return  # (an end / a symmetric middle anchor stays: and it isn't "edited by hand" for nothing)
        self.by_hand()
        delete_point(self.pat["loop"], i, self.to_xy)
        self.refresh()
        self.mark()

    def add_point(self, e):
        if self.view is None or (not self.ok and not self.pat.get("loop")):
            return
        loop = self.shown_loop()
        seg, t, d = nearest(loop["pts"], self.to_xy, e.x, e.y)
        if d > 3 * GRAB * self.scale or t in (0, 1):
            return  # (on an anchor already: nothing added, so not edited by hand either)
        self.by_hand()
        if add_anchor(self.pat["loop"], seg, t, self.from_xy(e.x, e.y), self.to_xy):
            self.refresh()
            self.mark()
