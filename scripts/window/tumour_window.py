"""The tumour window: the tumour settings (tumour.py) of the selected lines, polylines, freehand strokes, curves and
arcs. It stays open while you work and follows the selection; the side panel only shows a summary line and a
button that opens it (panel_tumour.py)."""

import random
import tkinter as tk
from tkinter import ttk

from files.mathexpr import calc, fmt
from notes.joined import shown_tumour, unify_tumours
from notes.tumour import GRAPH_KEYS, TUMOUR_DEFAULTS, clean_graph
from window.graph_window import GraphWindow
from window.widgets import Scrub, Tooltip

SHAPE_CHOICES = [("triangle", "Triangle"), ("square", "Square"), ("circle", "Circle"), ("parabola", "Parabola")]
SIDE_CHOICES = [("alt", "Alternating"), ("left", "Left"), ("right", "Right"), ("random", "Random")]
WRAP_CHOICES = [("simple", "Straight"), ("wrap", "Bent with the line")]
# the number boxes: (setting, label, unit, quick change steps (step, Shift step, Ctrl step) for widgets.Scrub,
# lowest, highest (as typed))
NUMBERS = [("size", "Size", "keys", (0.1, 1, 0.01), 0, 1000), ("length", "Length", "ticks", (1, 10, 0.1), 0, 10 ** 7),
           ("dist", "Distance", "ticks", (1, 10, 0.1), 1, 10 ** 7),
           ("rot", "Rotation", "degrees", (1, 15, 0.1), -180, 180),
           ("slant", "Slant", "% (square)", (1, 10, 0.1), -100, 100),
           ("ease", "Lead in", "ticks", (1, 10, 0.1), 0, 10 ** 7)]
TICKS = ("length", "dist", "ease")  # stored in beats, shown in ticks
TIPS = {
    "size": "How far the bumps stick out, in keys (as the piano roll looks).",
    "length": "How long each bump is along the line, in ticks.\n0 = spikes: every bump is one point pushed sideways,\n"
              "and the line zigzags straight from spike to spike.",
    "dist": "From the start of one bump to the start of the next, in ticks.\n"
            "A bump longer than this is cut where the next one starts.",
    "ease": "Smooth start and end: over this many ticks from each end of the range,\n"
            "the bumps grow from nothing to full size (and shrink back to nothing at the end),\n"
            "so the line leads into them instead of starting with a sudden side.\n0 = off.",
    "rot": "Tilts every bump, its two feet staying on the line.\n"
           "Plus leans it forward (the way the line runs), minus leans it back.\n"
           "90 lays it flat along the line, 180 turns it over to the other side.",
    "slant": "Square bumps only: slants the square's sides.\n"
             "0 = a square, 100 = the top narrows to a point (like a triangle),\n"
             "minus = the top is wider than the bottom (the sides lean outwards).",
    "side": "Which side of the line the bumps go (left / right as you go along the line from its start).",
    "wrap": "Only matters where the line curves under a bump (long bumps on a curve, arc or circle).\n"
            "Straight: each bump sits on a straight shortcut between its ends (angular).\n"
            "Bent with the line: each bump follows the curve under it (stays round).",
    "range": "Only this part of the line gets bumps (0 % = its start, 100 % = its end).",
    "fit": "Fit the bumps evenly: the distance is changed a little so a whole number of them fits\n"
           "the range exactly, with the last one landing right on its end.\n"
           "On a closed loop (like a full circle) the bumps meet up seamlessly where it starts\n"
           "(alternating sides: an even number, so they keep alternating there too).",
}
NOTHING = "Select a line, polyline, freehand stroke, curve or arc to give it tumours."


class TumourWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Tumours")
        self.transient(app)
        self.resizable(False, False)
        if app.tumour_pos:
            self.geometry(app.tumour_pos)
        self.loading = False
        self.vars = {}     # setting -> StringVar of its entry / combobox
        self.entries = {}  # setting -> its entry box
        self.widgets = []  # everything greyed out while tumours are off
        self.graph_btns, self.units = {}, {}  # setting -> its "…" button / unit label
        self.graph_window = None

        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        box.columnconfigure(3, weight=1)
        self.what = ttk.Label(box, text="", foreground="#777")
        self.what.grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 4))
        top = ttk.Frame(box)
        top.grid(row=1, column=0, columnspan=5, sticky="w")
        self.on = tk.BooleanVar()
        self.on_box = ttk.Checkbutton(top, text="Tumours", variable=self.on,
                                      command=lambda: self.set("on", self.on.get()))
        self.on_box.pack(side="left")
        self.combo(top, "shape", SHAPE_CHOICES, 9, "Shape")
        for r, (key, label, unit, steps, lo, hi) in enumerate(NUMBERS, start=2):
            lb = ttk.Label(box, text=label)
            lb.grid(row=r, column=0, sticky="w")
            var = self.vars[key] = tk.StringVar()
            e = ttk.Entry(box, textvariable=var, width=10)
            e.grid(row=r, column=1, sticky="w", padx=(5, 3), pady=1)
            e.bind("<Return>", lambda ev, key=key: self.on_entry(key))
            e.bind("<FocusOut>", lambda ev, key=key: self.on_entry(key))
            Scrub(app, [(e, var, lambda key=key: self.on_entry(key))], steps, lo, hi, label=lb)
            if key in GRAPH_KEYS:
                b = self.graph_btns[key] = ttk.Button(box, text="…", width=2,
                                                      command=lambda key=key, label=label, unit=unit:
                                                      self.open_graph(key, label, unit))
                b.grid(row=r, column=2, sticky="w", padx=(0, 5))
                Tooltip(b, "A graph: this number changes along the line.")
                self.widgets.append(b)
            u = self.units[key] = ttk.Label(box, text=unit, foreground="#777")
            u.grid(row=r, column=3, sticky="w")
            Tooltip(e, TIPS[key])
            self.widgets.append(e)
            self.entries[key] = e
        row = ttk.Frame(box)
        row.grid(row=8, column=0, columnspan=5, sticky="w", pady=(1, 0))
        self.combo(row, "side", SIDE_CHOICES, 10, "Side", pad=0)
        self.combo(row, "wrap", WRAP_CHOICES, 15, "")
        row = ttk.Frame(box)
        row.grid(row=9, column=0, columnspan=5, sticky="w", pady=(1, 0))
        ttk.Label(row, text="Range").pack(side="left")
        for i, key in enumerate(("start", "end")):
            if i:
                ttk.Label(row, text="% to").pack(side="left", padx=(3, 3))
            var = self.vars[key] = tk.StringVar()
            e = ttk.Entry(row, textvariable=var, width=5)
            e.pack(side="left", padx=(5 if not i else 0, 0))
            e.bind("<Return>", lambda ev, key=key: self.on_entry(key))
            e.bind("<FocusOut>", lambda ev, key=key: self.on_entry(key))
            Tooltip(e, TIPS["range"])
            self.widgets.append(e)
            self.entries[key] = e
        ttk.Label(row, text="%").pack(side="left", padx=(3, 0))
        self.fit = tk.BooleanVar()
        fit = ttk.Checkbutton(row, text="Fit", variable=self.fit, command=lambda: self.set("fit", self.fit.get()))
        fit.pack(side="left", padx=(8, 0))
        Tooltip(fit, TIPS["fit"])
        self.widgets.append(fit)
        self.reroll = ttk.Button(row, text="New random", command=lambda: self.set("seed", random.randrange(1, 10 ** 9)))
        self.reroll.pack(side="left", padx=(8, 0))
        Tooltip(self.reroll, "Random sides: pick them again.")
        self.info = ttk.Label(box, text="", foreground="#777", font=("Segoe UI", 8),
                              wraplength=int(300 * app.scale), justify="left")
        self.info.grid(row=10, column=0, columnspan=5, sticky="ew", pady=(4, 0))

        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Configure>", self.remember, add="+")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.sync()

    def combo(self, parent, key, choices, width, label, pad=10):
        if label:
            ttk.Label(parent, text=label).pack(side="left", padx=(pad, 0))
        var = self.vars[key] = tk.StringVar()
        c = ttk.Combobox(parent, textvariable=var, state="readonly", width=width, values=[t for _, t in choices])
        c.pack(side="left", padx=(5, 0))
        c.bind("<<ComboboxSelected>>", lambda e: self.set(key, next(v for v, t in choices if t == var.get())))
        if key in TIPS:
            Tooltip(c, TIPS[key])
        self.widgets.append(c)

    def remember(self, e):
        if e.widget is self:
            self.app.tumour_pos = f"+{self.winfo_x()}+{self.winfo_y()}"

    def close(self):
        if self.graph_window:
            self.graph_window.close()
        self.app.tumour_window = None
        self.destroy()
        self.app.roll.focus_set()

    def sync(self):
        """Show the first selected line's settings (everything greyed out when nothing fitting is selected)."""
        app = self.app
        tgts = app.tumour_targets()
        if not tgts:
            self.what.config(text=NOTHING)
        elif len(tgts) == 1:
            i = next(i for i in sorted(app.sels) if app.shapes[i] is tgts[0])
            self.what.config(text=f"Shape {i + 1}: {app.shape_label(tgts[0])}")
        else:
            self.what.config(text=f"{len(tgts)} shapes (they all change together)")
        tm = dict(TUMOUR_DEFAULTS, **((shown_tumour(tgts[0]) if tgts else None) or {"on": False}))
        self.loading = True
        self.on.set(tm["on"])
        self.fit.set(tm["fit"])
        for key, choices in (("shape", SHAPE_CHOICES), ("side", SIDE_CHOICES), ("wrap", WRAP_CHOICES)):
            self.vars[key].set(dict(choices)[tm[key]])
        for key, *_ in NUMBERS:
            value = tm[key] * (app.ppq if key in TICKS else 100 if key == "slant" else 1)
            self.vars[key].set(fmt(round(value, 3)))
        for key in ("start", "end"):
            self.vars[key].set(fmt(round(tm[key] * 100, 3)))
        for e in self.entries.values():
            e.config(style="TEntry")
        self.loading = False
        on = tm["on"] and bool(tgts)
        self.on_box.config(state="normal" if tgts else "disabled")
        for w in self.widgets:
            w.config(state=("readonly" if isinstance(w, ttk.Combobox) else "normal") if on else "disabled")
        self.reroll.config(state="normal" if on and tm["side"] == "random" else "disabled")
        square = on and tm["shape"] == "square"
        self.entries["slant"].config(state="normal" if square else "disabled")
        self.graph_btns["slant"].config(state="normal" if square else "disabled")
        # a number following a graph: blue, "× graph" after its unit
        graphs = tm.get("graphs") or {}
        for key, _, unit, *_ in NUMBERS:
            if key in self.graph_btns:
                self.units[key].config(text=f"{unit}  × graph" if key in graphs else unit,
                                       foreground="#0a50e0" if key in graphs else "#777")
        if self.graph_window:
            self.graph_window.sync()
        self.info.config(text="" if not tgts else
                         "The joined shapes kept their own tumours (these are the first one's). Changing anything "
                         "here gives the whole curve these settings." if any(t.get("tumours") for t in tgts) else
                         "Bumps along the line. The line's points stay draggable. Length 0 = spikes (a zigzag)."
                         if on else "Tick Tumours to put bumps along this line.")

    def set(self, key, value, group=False):
        """A tumour setting changed (group: one undo step while typing / quick-changing)."""
        app = self.app
        if self.loading:
            return
        tgts = app.tumour_targets()
        if not tgts:
            return
        if group:
            app.begin_edit(("tumour", tuple(sorted(app.sels)), key))
        else:
            app.push_undo()
        k = app.roll.sy / app.roll.sx if app.roll.sx else 0.25
        for t in tgts:
            unify_tumours(t)  # (a joined curve's shapes with their own tumours: these become the whole curve's)
            tm = t.setdefault("tumour", dict(TUMOUR_DEFAULTS))
            tm[key] = value
            tm["k"] = k  # sizes as the roll looks now
        app.shapes_changed()
        if not group:
            app.sync_tumour()
        else:
            app.sync_tumour_summary()
        if key == "on" and value:
            app.tips.show("tumours")

    def on_entry(self, key):
        var, e = self.vars[key], self.entries[key]
        if self.loading or str(e.cget("state")) == "disabled":
            return
        try:
            x = calc(var.get())
            lo, hi = next(((lo, hi) for k, *_, lo, hi in NUMBERS if k == key), (0, 100))
            scale = self.app.ppq if key in TICKS else 100 if key in ("slant", "start", "end") else 1
            value = x / scale if lo <= x <= hi else None
            if value is None:
                raise ValueError
        except ValueError:
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        tgts = self.app.tumour_targets()
        if tgts and all(abs((shown_tumour(t) or TUMOUR_DEFAULTS).get(key, 0.0) - value) < 1e-12 and
                        "tumours" not in t for t in tgts):
            return
        self.set(key, value, group=True)
        self.app.sync_tumour()

    def open_graph(self, key, label, unit):
        if self.graph_window and self.graph_window.key != key:
            self.graph_window.close()
        if self.graph_window:
            self.graph_window.lift()
        else:
            self.graph_window = GraphWindow(self, key, label, unit)
        self.graph_window.focus_set()

    def set_graph(self, key, pts):
        """The graph window changed a graph (its undo step is already taken)."""
        app = self.app
        tgts = app.tumour_targets()
        if not tgts:
            return
        g = clean_graph(pts)
        k = app.roll.sy / app.roll.sx if app.roll.sx else 0.25
        for t in tgts:
            unify_tumours(t)
            tm = t.setdefault("tumour", dict(TUMOUR_DEFAULTS))
            graphs = dict(tm.get("graphs") or {})
            if g:
                graphs[key] = [list(p) for p in g]
            else:
                graphs.pop(key, None)
            if graphs:
                tm["graphs"] = graphs
            else:
                tm.pop("graphs", None)
            tm["k"] = k  # sizes as the roll looks now
        app.shapes_changed()
        app.sync_tumour()
