"""The side panel's tumour settings for lines, polylines, freehand strokes, curves and arcs (tumour.py)."""

import random
import tkinter as tk
from tkinter import ttk

from files.mathexpr import calc, fmt
from notes.tumour import LINE_KINDS, TUMOUR_DEFAULTS
from window.widgets import Scrub, Tooltip

SHAPE_CHOICES = [("triangle", "Triangle"), ("square", "Square"), ("circle", "Circle"), ("parabola", "Parabola")]
SIDE_CHOICES = [("alt", "Alternating"), ("left", "Left"), ("right", "Right"), ("random", "Random")]
WRAP_CHOICES = [("simple", "Straight"), ("wrap", "Bent with the line")]
# the number boxes: (setting, label, unit, quick change steps (step, Shift step, Ctrl step) for widgets.Scrub)
NUMBERS = [("size", "Size", "keys", (0.1, 1, 0.01)), ("length", "Length", "ticks", (1, 10, 0.1)),
           ("dist", "Distance", "ticks", (1, 10, 0.1)), ("ease", "Lead in", "ticks", (1, 10, 0.1))]
TIPS = {
    "size": "How far the bumps stick out, in keys (as the piano roll looks).",
    "length": "How long each bump is along the line, in ticks.\n0 = spikes: every bump is one point pushed sideways,\n"
              "and the line zigzags straight from spike to spike.",
    "dist": "From the start of one bump to the start of the next, in ticks.\n"
            "A bump longer than this is cut where the next one starts.",
    "ease": "Smooth start and end: over this many ticks from each end of the range,\n"
            "the bumps grow from nothing to full size (and shrink back to nothing at the end),\n"
            "so the line leads into them instead of starting with a sudden side.\n0 = off.",
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


class TumourPanel:
    """Mixed into App."""

    def _build_tumour(self):
        box = self.tumour_box = ttk.Frame(self.settings)
        box.columnconfigure(2, weight=1)
        self.tumour_on = tk.BooleanVar()
        self.tumour_vars = {}     # setting -> StringVar of its entry / combobox
        self.tumour_widgets = []  # everything greyed out while tumours are off
        top = ttk.Frame(box)
        top.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(top, text="Tumours", variable=self.tumour_on,
                        command=lambda: self.set_tumour("on", self.tumour_on.get())).pack(side="left")
        self.tumour_combo(top, "shape", SHAPE_CHOICES, 9, "Shape")
        self.tumour_entries = {}  # setting -> its entry box
        for r, (key, label, unit, steps) in enumerate(NUMBERS, start=1):
            lb = ttk.Label(box, text=label)
            lb.grid(row=r, column=0, sticky="w")
            var = self.tumour_vars[key] = tk.StringVar()
            e = ttk.Entry(box, textvariable=var, width=10)
            e.grid(row=r, column=1, sticky="w", padx=(5, 3), pady=1)
            e.bind("<Return>", lambda ev, key=key: self.on_tumour_entry(key))
            e.bind("<FocusOut>", lambda ev, key=key: self.on_tumour_entry(key))
            Scrub(self, [(e, var, lambda key=key: self.on_tumour_entry(key))], steps, 1 if key == "dist" else 0,
                  label=lb)
            ttk.Label(box, text=unit, foreground="#777").grid(row=r, column=2, sticky="w")
            Tooltip(e, TIPS[key])
            self.tumour_widgets.append(e)
            self.tumour_entries[key] = e
        row = ttk.Frame(box)
        row.grid(row=5, column=0, columnspan=4, sticky="w", pady=(1, 0))
        self.tumour_combo(row, "side", SIDE_CHOICES, 10, "Side", pad=0)
        self.tumour_combo(row, "wrap", WRAP_CHOICES, 15, "")
        row = ttk.Frame(box)
        row.grid(row=6, column=0, columnspan=4, sticky="w", pady=(1, 0))
        ttk.Label(row, text="Range").pack(side="left")
        for i, key in enumerate(("start", "end")):
            if i:
                ttk.Label(row, text="% to").pack(side="left", padx=(3, 3))
            var = self.tumour_vars[key] = tk.StringVar()
            e = ttk.Entry(row, textvariable=var, width=5)
            e.pack(side="left", padx=(5 if not i else 0, 0))
            e.bind("<Return>", lambda ev, key=key: self.on_tumour_entry(key))
            e.bind("<FocusOut>", lambda ev, key=key: self.on_tumour_entry(key))
            Tooltip(e, TIPS["range"])
            self.tumour_widgets.append(e)
            self.tumour_entries[key] = e
        ttk.Label(row, text="%").pack(side="left", padx=(3, 0))
        self.tumour_fit = tk.BooleanVar()
        fit = ttk.Checkbutton(row, text="Fit", variable=self.tumour_fit,
                              command=lambda: self.set_tumour("fit", self.tumour_fit.get()))
        fit.pack(side="left", padx=(8, 0))
        Tooltip(fit, TIPS["fit"])
        self.tumour_widgets.append(fit)
        self.reroll = ttk.Button(row, text="New random", command=self.reroll_tumours)
        self.reroll.pack(side="left", padx=(8, 0))
        Tooltip(self.reroll, "Random sides: pick them again.")
        self.tumour_info = ttk.Label(box, text="", foreground="#777", font=("Segoe UI", 8),
                                     wraplength=int(300 * self.scale), justify="left")
        self.tumour_info.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(2, 0))

    def tumour_combo(self, parent, key, choices, width, label, pad=10):
        if label:
            ttk.Label(parent, text=label).pack(side="left", padx=(pad, 0))
        var = self.tumour_vars[key] = tk.StringVar()
        c = ttk.Combobox(parent, textvariable=var, state="readonly", width=width, values=[t for _, t in choices])
        c.pack(side="left", padx=(5, 0))
        c.bind("<<ComboboxSelected>>", lambda e: self.set_tumour(key, next(v for v, t in choices
                                                                            if t == var.get())))
        if key in TIPS:
            Tooltip(c, TIPS[key])
        self.tumour_widgets.append(c)

    def tumour_targets(self):
        """What the tumour settings change: the selected lines / polylines / freehand strokes / curves / arcs."""
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] in LINE_KINDS]

    def sync_tumour(self):
        tgts = self.tumour_targets()
        self._rows["tumour"] = bool(tgts)
        self.layout_rows()
        if not tgts:
            return
        tm = dict(TUMOUR_DEFAULTS, **(tgts[0].get("tumour") or {"on": False}))
        self._loading = True
        self.tumour_on.set(tm["on"])
        self.tumour_fit.set(tm["fit"])
        for key, choices in (("shape", SHAPE_CHOICES), ("side", SIDE_CHOICES), ("wrap", WRAP_CHOICES)):
            self.tumour_vars[key].set(dict(choices)[tm[key]])
        for key, _, _, _ in NUMBERS:
            value = tm[key] * (1 if key == "size" else self.ppq)
            self.tumour_vars[key].set(fmt(round(value, 3)))
        for key in ("start", "end"):
            self.tumour_vars[key].set(fmt(round(tm[key] * 100, 3)))
        for e in self.tumour_entries.values():
            e.config(style="TEntry")
        self._loading = False
        on = tm["on"]
        for w in self.tumour_widgets:
            w.config(state=("readonly" if isinstance(w, ttk.Combobox) else "normal") if on else "disabled")
        self.reroll.config(state="normal" if on and tm["side"] == "random" else "disabled")
        self.tumour_info.config(text="Bumps along the line. The line's points stay draggable. Length 0 = spikes (a zigzag)." if on else
                                "Tick Tumours to put bumps along this line.")

    def set_tumour(self, key, value, group=False):
        """A tumour setting changed (group: one undo step while typing / quick-changing)."""
        if self._loading:
            return
        tgts = self.tumour_targets()
        if not tgts:
            return
        if group:
            self.begin_edit(("tumour", tuple(sorted(self.sels)), key))
        else:
            self.push_undo()
        k = self.roll.sy / self.roll.sx if self.roll.sx else 0.25
        for t in tgts:
            tm = t.setdefault("tumour", dict(TUMOUR_DEFAULTS))
            tm[key] = value
            tm["k"] = k  # sizes as the roll looks now
        self.shapes_changed()
        if not group:
            self.sync_tumour()
        if key == "on" and value:
            self.tips.show("tumours")

    def on_tumour_entry(self, key):
        var, e = self.tumour_vars[key], self.tumour_entries[key]
        if self._loading or str(e.cget("state")) == "disabled":
            return
        try:
            x = calc(var.get())
            if key == "size":
                value = x if 0 <= x <= 1000 else None
            elif key in ("start", "end"):
                value = x / 100 if 0 <= x <= 100 else None
            else:
                value = x / self.ppq if (1 if key == "dist" else 0) <= x <= 10 ** 7 else None
            if value is None:
                raise ValueError
        except ValueError:
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        tgts = self.tumour_targets()
        if tgts and all(abs(t.get("tumour", TUMOUR_DEFAULTS).get(key, 0.0) - value) < 1e-12 for t in tgts):
            return
        self.set_tumour(key, value, group=True)
        self.sync_tumour()

    def reroll_tumours(self):
        self.set_tumour("seed", random.randrange(1, 10 ** 9))
