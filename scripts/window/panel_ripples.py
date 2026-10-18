"""The side panel's rows for Inside fill "Ripples" (notes/ripples.py): Notes Fill / Spam, Steps, Colours, Turn,
Spacing, Grow to + Auto, Drawn shapes are ring edges, Every other one empty, Inside them. Mixed into App (beside
NestedPanel, whose change_nested makes the undo steps)."""

import copy
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.ripples import INSIDES, MOST_COLOURS, MOST_GROW, MOST_STEPS, RIPPLES_DEFAULTS, ripple_rings
from window import look
from window.panel_nested import NOTES_CHOICES, drop_row
from window.widgets import Scrub, Tooltip, bad, good, leave_box, unchanged

# the number boxes: (setting, label, unit, lowest, highest, Scrub steps, setting -> box, box -> setting); two a row
BOXES = [
    [("steps", "ripples_steps", "", 2, MOST_STEPS, (1, 5, 1), int, int),
     ("colours", "ripples_colours", "", 1, MOST_COLOURS, (1, 1, 1), int, int)],
    [("spacing", "ripples_spacing", "", -100, 100, (1, 10, 1), int, lambda x: int(round(x)))],
    [("turn", "ripples_turn", "ripples_turn_unit", -180, 180, (1, 10, 0.1), float, float)],
    [("grow", "ripples_grow", "%", 100, MOST_GROW * 100, (10, 100, 1), lambda v: round(v * 100, 6),
      lambda x: x / 100)],
]
TICKS = [("edge", "ripples_edge"), ("skip", "ripples_skip")]
RIPPLES_UNDO = tr("panel_custom.ripples")


def ripples_on(t):
    """Custom shape (or the settings for new ones) t with a Ripples fill (Nested / Tiles win if it has one too)."""
    return (bool(t.get("ripples")) and t.get("fill") in ("fill", "spam") and not t.get("text") and "notes" not in t
            and not t.get("hz") and not t.get("nested") and not t.get("tiles"))


class RipplesPanel:
    """Mixed into App."""

    def _build_ripples(self, opts):
        """The Ripples rows, put in self.custom_rows (shown by sync_custom while Ripples is the Inside fill)."""
        self.ripples_rows, self.ripples_boxes, self.ripples_ticks = [], {}, {}
        row, self.ripples_notes_box = drop_row(self, opts, "nested_notes", [t for _, t in NOTES_CHOICES])
        self.ripples_rows.append(row)
        self.ripples_notes_box.bind("<<ComboboxSelected>>", lambda e: (
            self.set_ripples_fill(NOTES_CHOICES[e.widget.current()][0]), self.roll.focus_set()))
        for line in BOXES:
            row = ttk.Frame(opts)
            for n, (key, text, unit, lo, hi, steps, _, _) in enumerate(line):
                lb = ttk.Label(row, text=tr("panel_custom." + text))
                lb.grid(row=0, column=3 * n, sticky="w", padx=(12 if n else 0, 0))
                var = tk.StringVar()
                e = ttk.Entry(row, textvariable=var, width=5)
                e.grid(row=0, column=3 * n + 1, sticky="w", padx=4)
                if unit:
                    ttk.Label(row, text=unit if unit == "%" else tr("panel_custom." + unit),
                              foreground=look.HINT).grid(row=0, column=3 * n + 2, sticky="w")
                apply = (lambda left=False, key=key: self.on_ripples_box(key, left))
                e.bind("<Return>", lambda ev, apply=apply: apply())
                leave_box(self, e, var, apply)
                Scrub(self, [(e, var, apply)], steps, lo, hi, label=lb)
                for w in (lb, e):
                    Tooltip(w, tr("panel_custom." + text + "_tip"))
                self.ripples_boxes[key] = (e, var)
            if line[0][0] == "grow":  # Grow to [500] % [x] Auto
                self.ripples_auto_var = tk.BooleanVar(value=True)
                b = ttk.Checkbutton(row, text=tr("panel_custom.ripples_auto"), variable=self.ripples_auto_var,
                                    command=lambda: self.set_ripples("auto", self.ripples_auto_var.get()))
                b.grid(row=0, column=3, columnspan=3, sticky="w", padx=(12, 0))
                Tooltip(b, tr("panel_custom.ripples_auto_tip"))
            self.ripples_rows.append(row)
        self.ripples_table = list(self.ripples_rows)  # (lined up with the Nested / Tiles ones, panel_custom)
        for key, text in TICKS:
            var = tk.BooleanVar(value=False)
            b = ttk.Checkbutton(opts, text=tr("panel_custom." + text), variable=var,
                                command=lambda key=key, var=var: self.set_ripples(key, var.get()))
            Tooltip(b, tr("panel_custom." + text + "_tip"))
            self.ripples_rows.append(b)
            self.ripples_ticks[key] = var
        texts = [tr("panel_custom.ripples_inside_" + v) for v in INSIDES]
        row, self.ripples_inside_box = drop_row(self, opts, "ripples_inside", texts)
        self.ripples_inside_box.bind("<<ComboboxSelected>>", lambda e: (
            self.set_ripples("inside", INSIDES[e.widget.current()]), self.roll.focus_set()))
        self.ripples_rows.append(row)
        self.ripples_table.append(row)
        # nothing drawn inside the outline: no rings (ripples.parts)
        self.ripples_warn = ttk.Label(opts, text=tr("panel_custom.ripples_none"), foreground=look.WARN,
                                      font=look.font(8), wraplength=int(260 * self.scale), justify="left")
        self.ripples_rows.append(self.ripples_warn)
        for row in self.ripples_rows:
            self.custom_rows.append((row, dict(anchor="w", padx=(20, 0), pady=(1, 0))))

    def sync_ripples(self, t):
        """The Ripples rows show t's settings (the first target's)."""
        cfg = t.get("ripples") or RIPPLES_DEFAULTS
        self._loading = True
        self.ripples_notes_box.current(1 if t.get("fill") == "spam" else 0)
        for line in BOXES:
            for key, _, _, _, _, _, shown, _ in line:
                e, var = self.ripples_boxes[key]
                var.set(fmt(shown(cfg.get(key, RIPPLES_DEFAULTS[key]))))
                e.config(style="TEntry")
        auto = cfg.get("auto", True)
        self.ripples_auto_var.set(auto)
        self.ripples_boxes["grow"][0].config(state="disabled" if auto else "normal")
        for key, var in self.ripples_ticks.items():
            var.set(bool(cfg.get(key, RIPPLES_DEFAULTS[key])))
        self.ripples_inside_box.current(INSIDES.index(cfg.get("inside", "rings")))
        self._loading = False

    def ripples_row_states(self, t, on):
        """{row: shown} for sync_custom's show_custom_rows (on: Ripples is the Inside fill shown); the warning only
        while t (a placed shape) has nothing drawn inside its outline."""
        warn = on and t is not self.custom_defaults and not ripple_rings(t, self.ppq)
        return {w: on and (w is not self.ripples_warn or warn) for w in self.ripples_rows}

    def ripples_targets(self):
        return [t for t in self.custom_targets() if ripples_on(t)]

    def pick_ripples(self):
        """Ripples picked as the Inside fill (their own settings kept from before, else the defaults; Notes from
        their Fill / Spam, else Fill). Not text, pasted notes or Hz bass."""
        tgts = [t for t in self.custom_targets() if not t.get("text") and "notes" not in t]
        if any(t.get("hz") for t in tgts):  # (they don't mix: the others change, after a warning)
            tgts = self.skip_hz(tgts)
        tgts = [t for t in tgts if not ripples_on(t)]

        def put(t):
            if t["fill"] not in ("fill", "spam"):
                t["fill"] = "fill"
            t.pop("nested", None)  # (one Inside pattern at a time)
            t.pop("tiles", None)
            if not t.get("ripples"):
                t["ripples"] = copy.deepcopy(RIPPLES_DEFAULTS)

        self.change_nested(tgts, put, RIPPLES_UNDO)
        self.tips.show("fill", wait=True)

    def set_ripples_fill(self, fill):
        """Notes Fill / Spam of the Ripples targets."""
        if self._loading:
            return
        tgts = [t for t in self.ripples_targets() if t["fill"] != fill]
        self.change_nested(tgts, lambda t: t.update(fill=fill), tr("panel_custom.nested_notes"))

    def set_ripples(self, key, value):
        """One Ripples setting of every Ripples target."""
        if self._loading:
            return
        tgts = [t for t in self.ripples_targets() if t["ripples"].get(key, RIPPLES_DEFAULTS.get(key)) != value]

        def put(t):
            t["ripples"] = dict(t["ripples"], **{key: copy.deepcopy(value)})

        self.change_nested(tgts, put, RIPPLES_UNDO)

    def on_ripples_box(self, key, left=False):
        """A Ripples number box entered (left: only left, nothing unless changed)."""
        e, var = self.ripples_boxes[key]
        if self._loading or left and unchanged(e):
            return
        _, _, _, lo, hi, _, _, to_setting = next(b for line in BOXES for b in line if b[0] == key)
        try:
            x = calc(var.get())
            if not lo <= x <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        self.set_ripples(key, to_setting(x))
        good(e)
