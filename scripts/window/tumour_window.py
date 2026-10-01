"""The tumour window: the tumour settings (tumour.py) of the selected lines, polylines, freehand strokes, curves and
arcs. It stays open while you work and follows the selection; the side panel only shows a summary line and a
button that opens it (panel_tumour.py)."""

import json
import random
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.joined import shown_tumour, unify_tumours
from notes.tumour import GRAPH_KEYS, TUMOUR_DEFAULTS, clean_graph
from window.graph_window import GraphWindow
from window.panel_custom import GAP_COLOR
from window.widgets import Scrub, Tooltip

SHAPE_CHOICES = [("triangle", tr("tumour_window.triangle")), ("square", tr("tumour_window.square")),
                 ("circle", tr("tumour_window.circle")), ("parabola", tr("tumour_window.parabola"))]
SIDE_CHOICES = [("alt", tr("tumour_window.alternating")), ("left", tr("tumour_window.left")),
                ("right", tr("tumour_window.right")), ("random", tr("tumour_window.random"))]
WRAP_CHOICES = [("simple", tr("tumour_window.straight")), ("wrap", tr("tumour_window.bent_with_the_line"))]
# the number boxes: (setting, label, unit, quick change steps (step, Shift step, Ctrl step) for widgets.Scrub,
# lowest, highest (as typed))
NUMBERS = [("size", tr("tumour_window.size"), tr("unit.keys"), (0.1, 1, 0.01), 0, 1000),
           ("length", tr("tumour_window.length"), tr("unit.ticks"), (1, 10, 0.1), 0, 10 ** 7),
           ("dist", tr("tumour_window.distance"), tr("unit.ticks"), (1, 10, 0.1), 1, 10 ** 7),
           ("rot", tr("tumour_window.rotation"), tr("unit.degrees"), (1, 15, 0.1), -180, 180),
           ("slant", tr("tumour_window.slant"), "%", (1, 10, 0.1), -100, 100),
           ("ease", tr("tumour_window.lead_in"), tr("unit.ticks"), (1, 10, 0.1), 0, 10 ** 7)]
TICKS = ("length", "dist", "ease")  # stored in beats, shown in ticks
TIPS = {
    "size": tr("tumour_window.how_far_the_bumps_stick_out"),
    "length": tr("tumour_window.how_long_each_bump_is_along"),
    "dist": tr("tumour_window.from_the_start_of_one_bump"),
    "ease": tr("tumour_window.smooth_start_and_end_over_this"),
    "rot": tr("tumour_window.tilts_every_bump_its_two_feet"),
    "slant": tr("tumour_window.square_bumps_only_slants_the_square"),
    "side": tr("tumour_window.which_side_of_the_line_the"),
    "wrap": tr("tumour_window.only_matters_where_the_line_curves"),
    "range": tr("tumour_window.only_this_part_of_the_line"),
    "fit": tr("tumour_window.fit_the_bumps_evenly_the_distance"),
}
NOTHING = tr("tumour_window.select_a_line_polyline_freehand_stroke")


class TumourWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("tumour_window.tumours"))
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
        self.what = ttk.Label(box, text="", foreground="#777", wraplength=int(300 * app.scale), justify="left")
        self.what.grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 4))
        top = ttk.Frame(box)
        top.grid(row=1, column=0, columnspan=5, sticky="w")
        self.on = tk.BooleanVar()
        self.on_box = ttk.Checkbutton(top, text=tr("tumour_window.tumours"), variable=self.on,
                                      command=lambda: self.set("on", True if self.mixed else self.on.get()))
        self.mixed = False  # some of the selected shapes have tumours on, some not: half ticked, a click = all on
        self.on_box.pack(side="left")
        self.combo(top, "shape", SHAPE_CHOICES, 9, tr("tumour_window.shape"))
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
                Tooltip(b, tr("tumour_window.a_graph_this_number_changes_along"))
                self.widgets.append(b)
            u = self.units[key] = ttk.Label(box, text=unit, foreground="#777")
            u.grid(row=r, column=3, sticky="w")
            Tooltip(e, TIPS[key])
            self.widgets.append(e)
            self.entries[key] = e
            if key == "slant":  # (only shown for square bumps, user)
                self.slant_row = box.grid_slaves(row=r)
        row = ttk.Frame(box)
        row.grid(row=8, column=0, columnspan=5, sticky="w", pady=(1, 0))
        self.combo(row, "side", SIDE_CHOICES, 10, tr("tumour_window.side"), pad=0)
        self.combo(row, "wrap", WRAP_CHOICES, 15, "")
        row = ttk.Frame(box)
        row.grid(row=9, column=0, columnspan=5, sticky="w", pady=(1, 0))
        ttk.Label(row, text=tr("tumour_window.range")).pack(side="left")
        for i, key in enumerate(("start", "end")):
            if i:
                ttk.Label(row, text=tr("tumour_window.percent_to")).pack(side="left", padx=(3, 3))
            var = self.vars[key] = tk.StringVar()
            e = ttk.Entry(row, textvariable=var, width=5)
            e.pack(side="left", padx=(5 if not i else 0, 0))
            e.bind("<Return>", lambda ev, key=key: self.on_entry(key))
            e.bind("<FocusOut>", lambda ev, key=key: self.on_entry(key))
            Tooltip(e, TIPS["range"])
            self.widgets.append(e)
            self.entries[key] = e
        ttk.Label(row, text=tr("unit.percent")).pack(side="left", padx=(3, 0))
        self.fit = tk.BooleanVar()
        fit = ttk.Checkbutton(row, text=tr("tumour_window.fit"), variable=self.fit,
                              command=lambda: self.set("fit", self.fit.get()))
        fit.pack(side="left", padx=(8, 0))
        Tooltip(fit, TIPS["fit"])
        self.widgets.append(fit)
        self.reroll = ttk.Button(row, text=tr("tumour_window.new_random"),
                                 command=lambda: self.set("seed", random.randrange(1, 10 ** 9)))
        self.reroll.pack(side="left", padx=(8, 0))
        Tooltip(self.reroll, tr("tumour_window.random_sides_pick_them_again"))
        self.info = ttk.Label(box, text="", foreground="#777", font=("Segoe UI", 8),
                              wraplength=int(300 * app.scale), justify="left")
        self.info.grid(row=10, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        ttk.Button(box, text=tr("tumour_window.close"), command=self.close).grid(row=11, column=0, columnspan=5,
                                                                                 sticky="e", pady=(6, 0))

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
            self.graph_window.ok()
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
            self.what.config(text=tr("tumour_window.shape_2", i=i + 1, shape_label=app.shape_label(tgts[0])))
        else:
            on = sum(bool((shown_tumour(t) or {}).get("on")) for t in tgts)
            self.what.config(text=tr("tumour_window.shapes_they_all_change_together", n=len(tgts))
                             if on in (0, len(tgts)) else
                             tr("tumour_window.shapes_with_tumours_the_settings_change", n=len(tgts), on=on))
        tm = dict(TUMOUR_DEFAULTS, **(app.shown_tumours() or {"on": False}))
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
        ons = [bool((shown_tumour(t) or {}).get("on")) for t in tgts]
        self.mixed = any(ons) and not all(ons)
        self.on_box.state(["alternate"] if self.mixed else ["!alternate"])
        for w in self.widgets:
            w.config(state=("readonly" if isinstance(w, ttk.Combobox) else "normal") if on else "disabled")
        self.reroll.config(state="normal" if on and tm["side"] == "random" else "disabled")
        square = on and tm["shape"] == "square"
        self.entries["slant"].config(state="normal" if square else "disabled")
        self.graph_btns["slant"].config(state="normal" if square else "disabled")
        # Slant only for square bumps, New random only for random sides (greyed while tumours are off, user)
        for w in self.slant_row:
            if tm["shape"] == "square":
                w.grid()
            else:
                w.grid_remove()
        if (tm["side"] == "random") != bool(self.reroll.winfo_manager()):
            if tm["side"] == "random":
                self.reroll.pack(side="left", padx=(8, 0))
            else:
                self.reroll.pack_forget()
        # a number following a graph: blue, "× graph" after its unit
        graphs = tm.get("graphs") or {}
        for key, _, unit, *_ in NUMBERS:
            if key in self.graph_btns:
                self.units[key].config(text=tr("tumour_window.graph", unit=unit) if key in graphs else unit,
                                       foreground="#0a50e0" if key in graphs else "#777")
        if self.graph_window:
            self.graph_window.sync()
        own = bool(tgts) and any(t.get("tumours") for t in tgts)
        self.info.config(text="" if not tgts else
                         tr("tumour_window.the_joined_shapes_kept_their_own") if own else
                         tr("tumour_window.bumps_along_the_line_the_line")
                         if on else tr("tumour_window.tick_tumours_to_put_bumps_along"),
                         foreground=GAP_COLOR if own else "#777")

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
            app.push_undo(name=tr("tumour_window.tumours"))
        k = app.roll.sy / app.roll.sx if app.roll.sx else 0.25
        shown = app.shown_tumours()
        if self.mixed and key == "on" and value:  # (half ticked: the others get the settings shown)
            for t in tgts:
                if not (shown_tumour(t) or {}).get("on"):
                    t.pop("tumours", None)
                    t.pop("splits", None)
                    t["tumour"] = dict(json.loads(json.dumps(shown)), k=k)
        elif self.mixed or not (key == "on" and value):  # (the settings only change shapes with tumours on)
            tgts = [t for t in tgts if (shown_tumour(t) or {}).get("on" if self.mixed else "shape")]
        for t in tgts:
            unify_tumours(t)  # (a joined curve's shapes with their own tumours: these become the whole curve's)
            tm = t.setdefault("tumour", json.loads(json.dumps(shown or TUMOUR_DEFAULTS)))
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
            self.graph_window.ok()
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
        for t in [t for t in tgts if (shown_tumour(t) or {}).get("on" if self.mixed else "shape")]:
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
