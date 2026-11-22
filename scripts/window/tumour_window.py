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
from notes.tumour import GRAPH_KEYS, TUMOUR_DEFAULTS, clean_graph, clean_tumour
from window import look
from window.default_pin import DefaultPin
from window.graph_window import GraphSource, GraphWindow
from window.panel_custom import GAP_COLOR
from window.widgets import (LocalUndo, Scrub, Tooltip, bad, fit_screen, good, grid_shown, placed, traced, unchanged,
                            unit_text)

SHAPE_CHOICES = [("triangle", tr("tumour_window.triangle")), ("square", tr("tumour_window.square")),
                 ("circle", tr("tumour_window.circle")), ("parabola", tr("tumour_window.parabola"))]
SIDE_CHOICES = [("alt", tr("tumour_window.alternating")), ("left", tr("tumour_window.left")),
                ("right", tr("tumour_window.right")), ("random", tr("tumour_window.random"))]
WRAP_CHOICES = [("simple", tr("tumour_window.straight")), ("wrap", tr("tumour_window.bent_with_the_line"))]
# the number boxes: (setting, label, unit, quick change steps (step, Shift step, Ctrl step) for widgets.Scrub,
# lowest, highest (as typed))
NUMBERS = [("size", tr("tumour_window.size"), "unit.keys", (0.1, 1, 0.01), 0, 1000),  # (units: text keys)
           ("length", tr("tumour_window.length"), "unit.ticks", (1, 10, 0.1), 0, 10 ** 7),
           ("dist", tr("tumour_window.distance"), "unit.ticks", (1, 10, 0.1), 1, 10 ** 7),
           ("rot", tr("tumour_window.rotation"), "unit.degrees", (1, 15, 0.1), -180, 180),
           ("slant", tr("tumour_window.slant"), "unit.percent", (1, 10, 0.1), -100, 100),
           ("ease", tr("tumour_window.lead_in"), "unit.ticks", (1, 10, 0.1), 0, 10 ** 7)]
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
TUMOUR_KEYS = ("tumour", "tumours", "splits")  # what this window changes on a shape (joined.py: "tumours", "splits")


class TumourGraphs(GraphSource):
    """The graph window on the selected lines' tumours: tm["graphs"][setting] (tumour.py)."""

    def tm(self):
        return dict(self.owner.base(), **(self.owner.shown_tumours() or {}))

    def targets(self):
        return self.owner.tumour_targets()

    def graph(self, key):
        return (self.tm().get("graphs") or {}).get(key)

    def store(self, key, pts):
        self.owner.set_graph(key, pts)

    def box_number(self, key):
        return self.tm()[key] * (self.app.ppq if key in ("length", "dist") else 100 if key == "slant" else 1)

    def band(self):
        tm = self.tm()
        return tm["start"], tm["end"]

    def band_label(self):
        return tr("graph_window.tumour_range")

    def empty_text(self, name):
        return tr("graph_window.select_a_line_with_tumours_to", name=name)

    def snapshot(self, tgts, key):
        return [json.loads(json.dumps(((shown_tumour(t) or {}).get("graphs") or {}).get(key))) for t in tgts]

    def restore(self, tgts, saved, key):
        # (still part of the tumour window's change: its Accept / X decides whether it's kept)
        shapes = self.owner.shape_list()
        if not any(t is s for t in tgts for s in shapes):
            return
        for t, g in zip(tgts, saved):
            tm = t.get("tumour") if any(t is s for s in shapes) else None
            if not tm:
                continue
            graphs = dict(tm.get("graphs") or {})
            graphs.pop(key, None)
            if g:
                graphs[key] = g
            if graphs:
                tm["graphs"] = graphs
            else:
                tm.pop("graphs", None)
        self.owner.changed(("graph", key))

    def closed(self, gw):
        self.owner.graph_window = None


MINE_LEFT_OUT = ("on", "k", "seed")  # (never in a default: on / off, the zoom it was set at, random sides' pick)


class TumourWindow(DefaultPin, tk.Toplevel):
    KEY = "tumour"  # (the shape's key, its section in shape-defaults.ini)
    ATTR = "tumour_window"

    # ---- what it works on (the "your default" window: a stand-in line, tumour_editor)

    def picked(self):
        return sorted(self.app.sels)

    def tumour_targets(self):
        return self.app.tumour_targets()

    def shown_tumours(self):
        return self.app.shown_tumours()

    def shape_list(self):
        return self.app.shapes

    def refresh(self):
        """The shapes changed: notes made again, the panel's summary + this window shown again."""
        self.app.shapes_changed()
        self.app.sync_tumour()

    def resync(self):
        self.app.sync_tumour()

    # ---- the user's own default ("use as default" pin, window/default_pin.py): what new tumours start with (and
    # Reset goes back to); on / off, the zoom and the random pick never in it

    def base(self):
        """What a line without tumours starts with: the user's default, else the program's."""
        return dict(TUMOUR_DEFAULTS, **(self.saved_default() or {}))

    @classmethod
    def program_default(cls):
        return {k: v for k, v in TUMOUR_DEFAULTS.items() if k not in MINE_LEFT_OUT}

    def default_moved(self):
        """(the boxes of lines without tumours show the new default at once: hunt)"""
        self.sync()

    @classmethod
    def clean_saved(cls, v):
        tm = clean_tumour(v)
        return None if tm is None else {k: x for k, x in tm.items() if k not in MINE_LEFT_OUT}

    @classmethod
    def default_title(cls):
        return tr("tumour_window.tumours")

    @classmethod
    def editor_class(cls):
        return tumour_editor(cls)

    def can_save_default(self):
        return bool((self.shown_tumours() or {}).get("on"))

    def default_now(self):
        tm = self.shown_tumours()
        if not (tm or {}).get("on"):
            return None
        return json.loads(json.dumps({k: v for k, v in tm.items() if k not in MINE_LEFT_OUT}))

    @classmethod
    def describe_default(cls, v, ppq):
        v = dict(TUMOUR_DEFAULTS, **v)

        def ticks(beats):
            n = round(beats * ppq, 3)
            return f"{fmt(n)} {unit_text(fmt(n), 'unit.ticks')}"
        words = [dict(SHAPE_CHOICES)[v["shape"]],
                 f"{tr('tumour_window.size')} {fmt(round(v['size'], 3))} {unit_text(fmt(v['size']), 'unit.keys')}",
                 f"{tr('tumour_window.length')} {ticks(v['length'])}",
                 f"{tr('tumour_window.distance')} {ticks(v['dist'])}",
                 dict(SIDE_CHOICES)[v["side"]], dict(WRAP_CHOICES)[v["wrap"]]]
        if v.get("graphs"):
            words.append(tr("defaults.own_graph"))
        return ", ".join(words)

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        setattr(app, self.ATTR, self)
        self.title(tr("tumour_window.tumours"))
        self.transient(app)
        self.resizable(False, False)
        if placed(self, app.tumour_pos):
            self.geometry(placed(self, app.tumour_pos))
        fit_screen(self)  # (once it's made: whole on the screen)
        self.loading = False
        self.vars = {}     # setting -> StringVar of its entry / combobox
        self.entries = {}  # setting -> its entry box
        self.widgets = []  # everything greyed out while tumours are off
        self.graph_btns, self.units = {}, {}  # setting -> its "…" button / unit label
        self.graphed = set()  # (settings following a graph: "× graph" after their unit)
        self.graph_window = None

        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        box.columnconfigure(3, weight=1)
        self.what = ttk.Label(box, text="", foreground=look.HINT, wraplength=int(300 * app.scale), justify="left")
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
            e.bind("<Return>", lambda ev, key=key: (self.on_entry(key), "break")[1])  # (not Accept)
            e.bind("<FocusOut>", lambda ev, key=key: self.on_entry(key, left=True))
            Scrub(app, [(e, var, lambda key=key: self.on_entry(key))], steps, lo, hi, label=lb)
            if key in GRAPH_KEYS:
                b = self.graph_btns[key] = ttk.Button(box, text="…", width=2,
                                                      command=lambda key=key, label=label, unit=unit:
                                                      self.open_graph(key, label, unit))
                b.grid(row=r, column=2, sticky="w", padx=(0, 5))
                Tooltip(b, tr("tumour_window.a_graph_this_number_changes_along"))
                self.widgets.append(b)
            u = self.units[key] = ttk.Label(box, text=tr(unit), foreground=look.HINT)
            u.grid(row=r, column=3, sticky="w")
            traced(self, var, lambda *_, key=key: self.show_unit(key))
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
            e.bind("<Return>", lambda ev, key=key: (self.on_entry(key), "break")[1])  # (not Accept)
            e.bind("<FocusOut>", lambda ev, key=key: self.on_entry(key, left=True))
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
        self.info = ttk.Label(box, text="", foreground=look.HINT, font=look.font(8),
                              wraplength=int(300 * app.scale), justify="left")
        self.info.grid(row=10, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        # like the claw / strum windows (user): changes show at once, Accept keeps them (one undo step), X / Esc puts
        # them back
        row = ttk.Frame(box)
        row.grid(row=11, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        reset = ttk.Button(row, text=tr("tumour_window.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("tumour_window.tip_reset"))
        right = ttk.Frame(row)
        right.pack(side="right")
        self.pin_row(right)
        ttk.Button(right, text=tr("tumour_window.accept"), command=self.accept).pack(side="left")

        self.targets = self.picked()
        self.undo = LocalUndo(self, self.state, self.put_state)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.accept())
        self.bind("<Configure>", self.remember, add="+")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.begin()
        self.sync()

    def combo(self, parent, key, choices, width, label, pad=10):
        if label:
            ttk.Label(parent, text=label).pack(side="left", padx=(pad, 0))
        var = self.vars[key] = tk.StringVar()
        c = ttk.Combobox(parent, textvariable=var, state="readonly", width=width, values=[t for _, t in choices])
        c.pack(side="left", padx=(5, 0))
        c.bind("<<ComboboxSelected>>", lambda e: self.set(key, choices[e.widget.current()][0]))  # (by place)
        if key in TIPS:
            Tooltip(c, TIPS[key])
        self.widgets.append(c)

    def remember(self, e):
        if e.widget is self:
            self.app.tumour_pos = f"+{self.winfo_x()}+{self.winfo_y()}"

    # ---- the change being tried: X / Esc puts the selected shapes back as they were (before), Accept keeps it

    def state(self):
        """The selected shapes' tumour settings as they are now (only these: a shape moved on the piano roll stays
        where it is when they're put back)."""
        shapes = self.shape_list()
        return json.dumps([{k: shapes[i][k] for k in TUMOUR_KEYS if k in shapes[i]}
                           for i in self.targets if i < len(shapes)])

    def begin(self):
        """Start from the selected shapes as they are now."""
        self.targets = self.picked()
        self.saved_sel = self.app.sel_state()  # (for the undo step)
        self.before = self.now = self.state()  # (now: as this window last left them)
        self.undo.reset()

    def put_tumours(self, shapes, state):
        """The tumour settings in state put on the selected ones of shapes."""
        for i, tms in zip(self.targets, json.loads(state)):
            for k in TUMOUR_KEYS:  # (the same dicts: the graph window knows them)
                shapes[i].pop(k, None)
            shapes[i].update(tms)

    def put_state(self, state):
        """The selected shapes' tumours as they were (Ctrl+Z / Ctrl+Y in the window, or X / Esc)."""
        self.put_tumours(self.shape_list(), state)
        self.now = self.state()
        self.refresh()

    def changed(self, mark=None):
        """The window changed the selected shapes (mark: changes with the same key in a row are one Ctrl+Z step)."""
        self.now = self.state()
        self.undo.mark(mark)
        self.refresh()

    def pending(self):
        """Are there tumours tried here that settle would keep?"""
        if self.graph_window and self.graph_window.job:
            self.graph_window.after_cancel(self.graph_window.job)
            self.graph_window.store()
        return self.now != self.before and self.state() == self.now

    def put_back(self, saved):
        self.put_tumours(saved, self.before)

    def settle(self, also=()):
        """Something else is about to change in the main window: the tumours tried so far are kept (their own undo
        step), and from now on X / Esc only puts back what changes after this. also: other windows kept next
        (App.settle_windows): this step takes their tries back too."""
        if self.pending():
            # the step takes back only the tumours tried here (a shape moved on the piano roll meanwhile has its own)
            saved = json.loads(json.dumps(self.app.shapes))
            for w in (self, *also):
                w.put_back(saved)
            sel = self.saved_sel
            self.saved_sel, self.before = self.app.sel_state(), self.now
            self.app.add_undo_step(json.dumps(saved), tr("tumour_window.tumours"), sel)
            self.undo.reset()  # (Ctrl+Z in the window goes back as far as X / Esc does: no further)

    def reset(self):
        """Every setting back to its default (the user's own if saved) and no graphs; tumours stay on or off."""
        app = self.app
        k = app.roll.sy / app.roll.sx if app.roll.sx else 0.25
        tgts = [t for t in self.tumour_targets() if shown_tumour(t)]
        if not tgts:
            return
        start = json.dumps(self.base())
        for t in tgts:
            on = bool(shown_tumour(t).get("on"))
            unify_tumours(t)
            t["tumour"] = dict(json.loads(start), on=on, k=k)
        self.changed()

    def accept(self):
        if self.graph_window:
            self.graph_window.ok()
        self.settle()
        self.close()

    def cancel(self):
        self.drop_graph()
        if self.state() != self.before:
            self.put_state(self.before)
        self.close()

    def drop_graph(self):
        """The graph window closed without storing what it hasn't stored yet."""
        g = self.graph_window
        if g:
            if g.job:
                g.after_cancel(g.job)
                g.job = None
            g.close()

    def close(self):
        self.drop_graph()
        setattr(self.app, self.ATTR, None)
        self.destroy()
        self.app.roll.focus_set()

    def sync(self):
        """Show the first selected line's settings (everything greyed out when nothing fitting is selected)."""
        app = self.app
        if self.picked() != self.targets:  # other shapes: the change tried on the last ones is kept
            self.settle()
            self.begin()
        elif self.state() != self.now:  # changed from outside (the main window's undo)
            self.begin()
        tgts = self.tumour_targets()
        if self.editing_default:
            self.what.grid_remove()  # (no shape to name)
        elif not tgts:
            self.what.config(text=NOTHING)
        elif len(tgts) == 1:
            i = next(i for i in sorted(app.sels) if app.shapes[i] is tgts[0])
            self.what.config(text=tr("tumour_window.shape_2", i=i + 1, shape_label=app.shape_label(tgts[0])))
        else:
            on = sum(bool((shown_tumour(t) or {}).get("on")) for t in tgts)
            self.what.config(text=tr("tumour_window.shapes_they_all_change_together", n=len(tgts))
                             if on in (0, len(tgts)) else
                             tr("tumour_window.shapes_with_tumours_the_settings_change", n=len(tgts), on=on))
        tm = dict(self.base(), **(self.shown_tumours() or {"on": False}))
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
        for key, e in self.entries.items():
            e.config(style="TEntry")
            e.shown_text = self.vars[key].get()  # (left unchanged: nothing happens, see on_entry)
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
            grid_shown(w, tm["shape"] == "square")
        if (tm["side"] == "random") != bool(self.reroll.winfo_manager()):
            if tm["side"] == "random":
                self.reroll.pack(side="left", padx=(8, 0))
            else:
                self.reroll.pack_forget()
        # a number following a graph: blue, "× graph" after its unit
        self.graphed = set(tm.get("graphs") or {})
        for key, *_ in NUMBERS:
            self.show_unit(key)
        if self.graph_window:
            self.graph_window.sync()
        own = bool(tgts) and any(t.get("tumours") for t in tgts)
        self.info.config(text="" if not tgts else
                         tr("tumour_window.the_joined_shapes_kept_their_own") if own else
                         tr("tumour_window.bumps_along_the_line_the_line")
                         if on else tr("tumour_window.tick_tumours_to_put_bumps_along"),
                         foreground=GAP_COLOR if own else look.HINT)

    def set(self, key, value, group=False):
        """A tumour setting changed (group: typing / quick-changing one box is one Ctrl+Z step in the window)."""
        app = self.app
        if self.loading:
            return
        tgts = self.tumour_targets()
        if not tgts:
            return
        k = app.roll.sy / app.roll.sx if app.roll.sx else 0.25
        shown = self.shown_tumours()
        if self.mixed and key == "on" and value:  # (half ticked: the others get the settings shown)
            for t in tgts:
                if not (shown_tumour(t) or {}).get("on"):
                    t.pop("tumours", None)
                    t.pop("splits", None)
                    t["tumour"] = dict(json.loads(json.dumps(shown)), k=k)
        elif self.mixed or not (key == "on" and value):  # (the settings only change shapes with tumours on)
            tgts = [t for t in tgts if (shown_tumour(t) or {}).get("on" if self.mixed else "shape")]
        start = json.dumps(shown or self.base())  # (once: the defaults file read once, not per line)
        for t in tgts:
            unify_tumours(t)  # (a joined curve's shapes with their own tumours: these become the whole curve's)
            tm = t.setdefault("tumour", json.loads(start))
            tm[key] = value
            tm["k"] = k  # sizes as the roll looks now
        self.changed(key if group else None)
        if key == "on" and value and not self.editing_default:
            app.tips.show("tumours")

    def on_entry(self, key, left=False):
        """A number typed (Enter, quick change), or the box left (left: nothing happens while it still shows what
        sync put there; else several shapes all got the first one's number, or a rounded one was written back)."""
        var, e = self.vars[key], self.entries[key]
        if self.loading or str(e.cget("state")) == "disabled" or left and unchanged(e):
            return
        try:
            x = calc(var.get())
            lo, hi = next(((lo, hi) for k, *_, lo, hi in NUMBERS if k == key), (0, 100))
            scale = self.app.ppq if key in TICKS else 100 if key in ("slant", "start", "end") else 1
            value = x / scale if lo <= x <= hi else None
            if value is None:
                raise ValueError
        except ValueError:
            bad(e)
            return
        good(e)
        tgts = self.tumour_targets()
        base = self.base()
        if tgts and all(abs((shown_tumour(t) or base).get(key, 0.0) - value) < 1e-12 and
                        "tumours" not in t for t in tgts):
            return
        self.set(key, value, group=True)
        self.resync()

    def show_unit(self, key):
        """The unit after a box ("1 key" / "2 keys"), blue + "× graph" while it follows a graph."""
        u = self.units.get(key)
        if u is None:
            return
        word = unit_text(self.vars[key].get(), next(unit for k, _, unit, *_ in NUMBERS if k == key))
        graph = key in self.graphed and key in self.graph_btns
        text = tr("tumour_window.graph", unit=word) if graph else word
        if u.cget("text") != text:
            u.config(text=text, foreground=look.VALUE if graph else look.HINT)
        elif str(u.cget("foreground")) != str(look.VALUE if graph else look.HINT):
            u.config(foreground=look.VALUE if graph else look.HINT)

    def open_graph(self, key, label, unit):
        if self.graph_window and self.graph_window.key != key:
            self.graph_window.ok()
        if self.graph_window:
            self.graph_window.lift()
        else:
            self.graph_window = GraphWindow(TumourGraphs(self), key, label, unit)
        self.graph_window.focus_set()

    def set_graph(self, key, pts):
        """The graph window changed a graph (its undo step is already taken)."""
        app = self.app
        tgts = self.tumour_targets()
        if not tgts:
            return
        g = clean_graph(pts)
        k = app.roll.sy / app.roll.sx if app.roll.sx else 0.25
        start = json.dumps(self.base())
        for t in [t for t in tgts if (shown_tumour(t) or {}).get("on" if self.mixed else "shape")]:
            unify_tumours(t)
            tm = t.setdefault("tumour", json.loads(start))
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
        self.changed(("graph", key))


def tumour_editor(cls):
    """The Tumours window changing the user's DEFAULT only (Settings → User set defaults; like
    default_pin.default_editor): a stand-in line no one sees, no shape / selection / undo step touched, the Tumours
    tick greyed; Accept saves, X / Esc changes nothing."""
    class Editor(cls):
        ATTR = cls.ATTR + "_defaults"
        editing_default = True

        def __init__(self, app, done=None):
            self.fake = cls.stand_in()
            self.fake["tumour"] = dict(json.loads(json.dumps(self.base())), on=True)
            self.done = done
            super().__init__(app)
            self.title(tr("defaults.edit_title", name=cls.default_title()))

        def picked(self):
            return [0]

        def tumour_targets(self):
            return [self.fake]

        def shown_tumours(self):
            return shown_tumour(self.fake)

        def shape_list(self):
            return [self.fake]

        def refresh(self):
            self.sync()

        def resync(self):
            self.sync()

        def pin_row(self, row):
            """(no pin here: Accept is what saves)"""

        def settle(self, also=()):
            """(nothing tried on shapes to keep)"""

        def sync(self):
            super().sync()
            self.on_box.state(["disabled"])

        def accept(self):
            if self.graph_window:
                self.graph_window.ok()
            self.save_default()
            self.close()

        def cancel(self):
            self.close()

        def close(self):
            self.drop_graph()
            setattr(self.app, self.ATTR, None)
            self.destroy()
    Editor.__name__ = cls.__name__ + "Default"
    return Editor
