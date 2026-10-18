"""The side panel's rows for Inside fill "Nested" (notes/nested.py): Notes Fill / Spam, Shrink by, Copies of, Morph
into it, Copy angle, Steps, Last size, Spacing, Turn, Colours and Every other one empty. Mixed into App."""

import copy
import tkinter as tk
from tkinter import messagebox, ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.nested import MOST_COLOURS, MOST_STEPS, NESTED_DEFAULTS
from window import look
from window.drawer import library_lists
from window.widgets import Scrub, Tooltip, bad, good, leave_box, name_list, set_names, unchanged

SAME_SHAPE = tr("panel_custom.nested_of_same")

NOTES_CHOICES = [("fill", tr("panel_custom.fill")), ("spam", tr("panel_custom.spam"))]
# the number boxes: (setting, label, unit, lowest, highest, Scrub steps, setting -> box, box -> setting); two a row
BOXES = [
    [("angle", "nested_angle", "nested_angle_unit", -180, 180, (1, 10, 0.1), float, float)],  # (Scaling only)
    [("steps", "nested_steps", "", 2, MOST_STEPS, (1, 5, 1), int, int),
     ("last", "nested_last", "%", 0, 100, (1, 10, 0.1), lambda v: round(v * 100, 6), lambda x: x / 100)],
    [("spacing", "nested_spacing", "", -100, 100, (1, 10, 1), int, lambda x: int(round(x))),
     ("colours", "nested_colours", "", 1, MOST_COLOURS, (1, 1, 1), int, int)],
    [("turn", "nested_turn", "nested_turn_unit", -180, 180, (1, 10, 0.1), float, float)],  # (Scaling only)
    [("mix", "nested_mix", "%", 0, 100, (1, 10, 0.1), lambda v: round(v * 100, 6), lambda x: x / 100)],  # (Blend)
]
WAY_CHOICES = [("scale", tr("panel_custom.nested_way_scale")), ("band", tr("panel_custom.nested_way_band")),
               ("blend", tr("panel_custom.nested_way_blend"))]
ONLY_FOR = {"angle": "scale", "turn": "scale", "mix": "blend"}  # (rows of the boxes only one way uses)
DROP_WIDTH = 14  # every Nested / Tiles dropdown (short choices; the tooltips tell the rest)
NESTED_UNDO = tr("panel_custom.nested")


def nested_on(t):
    """Custom shape (or the settings for new ones) t with a Nested fill (never text, pasted notes or Hz bass: their
    notes don't use it, custom.has_nested)."""
    return (bool(t.get("nested")) and t.get("fill") in ("fill", "spam") and not t.get("text") and "notes" not in t
            and not t.get("hz"))


def drop_row(app, opts, text, values=(), names=None):
    """A row of a label and a dropdown for the Nested / Tiles rows (grid, lined up by widgets.line_up; the dropdown
    spans to the row's end, so every one is as wide as the others). names: a name list (postcommand)."""
    row = ttk.Frame(opts)
    lb = ttk.Label(row, text=tr("panel_custom." + text))
    lb.grid(row=0, column=0, sticky="w")
    if names:
        box = name_list(ttk.Combobox(row, state="readonly", width=DROP_WIDTH, postcommand=names))
    else:
        box = ttk.Combobox(row, values=values, state="readonly", width=DROP_WIDTH)
    box.grid(row=0, column=1, columnspan=5, sticky="w", padx=4)
    for w in (lb, box):
        Tooltip(w, tr("panel_custom." + text + "_tip"))
    return row, box


class NestedPanel:
    """Mixed into App (beside CustomPanel, which places these rows under the Inside choices)."""

    def _build_nested(self, opts):
        """The Nested rows, put in self.custom_rows (shown by sync_custom while Nested is the Inside fill)."""
        self.nested_rows, self.nested_boxes, self.nested_way_rows = [], {}, {}
        self.nested_table = []  # (the rows lined up with the Tiles ones, panel_tiles)
        row, self.nested_notes_box = drop_row(self, opts, "nested_notes", [t for _, t in NOTES_CHOICES])
        self.nested_notes_box.bind("<<ComboboxSelected>>", lambda e: (
            self.set_nested_notes(NOTES_CHOICES[e.widget.current()][0]), self.roll.focus_set()))
        self.nested_rows.append(row)
        # how the copies are made (nested.WAYS)
        row, self.nested_way_box = drop_row(self, opts, "nested_way", [t for _, t in WAY_CHOICES])
        self.nested_way_box.bind("<<ComboboxSelected>>", lambda e: (
            self.set_nested("way", WAY_CHOICES[e.widget.current()][0]), self.roll.focus_set()))
        self.nested_rows.append(row)
        # Scaling: the copies made of another library shape ("of")
        row, self.nested_of_box = drop_row(self, opts, "nested_of", names=self.refresh_nested_names)
        self.nested_of_box.bind("<<ComboboxSelected>>", lambda e: (self.on_nested_of(), self.roll.focus_set()))
        self.nested_rows.append(row)
        self.nested_way_rows[row] = "scale"
        self.nested_table += self.nested_rows
        # Scaling, Copies of another drawing: the steps change into it bit by bit ("morph")
        self.nested_morph_var = tk.BooleanVar(value=False)
        b = self.nested_morph_row = ttk.Checkbutton(
            opts, text=tr("panel_custom.nested_morph"), variable=self.nested_morph_var,
            command=lambda: (self.nested_morph_row.state(["!alternate"]),
                             self.set_nested("morph", self.nested_morph_var.get())))
        Tooltip(b, tr("panel_custom.nested_morph_tip"))
        self.nested_rows.append(b)
        self.nested_way_rows[b] = "scale"
        for line in BOXES:
            row = ttk.Frame(opts)
            if line[0][0] != "mix":  # (its long label stays out of the table)
                self.nested_table.append(row)
            for n, (key, text, unit, lo, hi, steps, _, _) in enumerate(line):
                lb = ttk.Label(row, text=tr("panel_custom." + text))
                lb.grid(row=0, column=3 * n, sticky="w", padx=(12 if n else 0, 0))
                var = tk.StringVar()
                e = ttk.Entry(row, textvariable=var, width=5)
                e.grid(row=0, column=3 * n + 1, sticky="w", padx=4)
                if unit:  # (a lone box's unit may reach past the second column)
                    ttk.Label(row, text=unit if unit == "%" else tr("panel_custom." + unit),
                              foreground=look.HINT).grid(row=0, column=3 * n + 2, sticky="w",
                                                         columnspan=1 if len(line) > 1 else 4)
                apply = (lambda left=False, key=key: self.on_nested_box(key, left))
                e.bind("<Return>", lambda ev, apply=apply: apply())
                leave_box(self, e, var, apply)
                Scrub(self, [(e, var, apply)], steps, lo, hi, label=lb)
                for w in (lb, e):
                    Tooltip(w, tr("panel_custom." + text + "_tip"))
                self.nested_boxes[key] = (e, var)
            self.nested_rows.append(row)
            if line[0][0] in ONLY_FOR:
                self.nested_way_rows[row] = ONLY_FOR[line[0][0]]
        self.nested_skip_var = tk.BooleanVar(value=False)
        b = ttk.Checkbutton(opts, text=tr("panel_custom.nested_skip"), variable=self.nested_skip_var,
                            command=lambda: self.set_nested("skip", self.nested_skip_var.get()))
        Tooltip(b, tr("panel_custom.nested_skip_tip"))
        self.nested_rows.append(b)
        for row in self.nested_rows:
            self.custom_rows.append((row, dict(anchor="w", padx=(20, 0), pady=(1, 0))))

    def sync_nested(self, t):
        """The Nested rows show t's settings (the first target's, like the other Inside rows)."""
        cfg = t.get("nested") or NESTED_DEFAULTS
        self._loading = True
        self.nested_notes_box.current(1 if t.get("fill") == "spam" else 0)
        self.nested_way_box.current([w for w, _ in WAY_CHOICES].index(cfg.get("way", "scale")))
        self.refresh_nested_names()
        self.nested_of_box.set(cfg["of"]["name"] if cfg.get("of") else SAME_SHAPE)
        # Morph into it: the shapes that have another drawing; half-ticked when only some of them morph
        morphs = {bool(x["nested"].get("morph")) for x in self.nested_targets() if x["nested"].get("of")}
        self.nested_morph_var.set(morphs == {True})
        self.nested_morph_row.state(["alternate" if len(morphs) > 1 else "!alternate"])
        for line in BOXES:
            for key, _, _, _, _, _, shown, _ in line:
                e, var = self.nested_boxes[key]
                var.set(fmt(shown(cfg.get(key, NESTED_DEFAULTS[key]))))
                e.config(style="TEntry")
        self.nested_skip_var.set(bool(cfg.get("skip")))
        self._loading = False

    def nested_row_states(self, t, on):
        """{row: shown} for sync_custom's show_custom_rows (on: Nested is the Inside fill shown)."""
        cfg = t.get("nested") or NESTED_DEFAULTS
        way = cfg.get("way", "scale")
        out = {w: on and self.nested_way_rows.get(w, way) == way for w in self.nested_rows}
        # (Morph into it: only with another drawing picked, in any of the shapes)
        out[self.nested_morph_row] &= any((x.get("nested") or {}).get("of") for x in [t] + self.nested_targets())
        return out

    def refresh_nested_names(self):
        """Copies of: "Same as the shape" and the built-in shapes, then the saved ones (newest first)."""
        top, saved = library_lists()
        set_names(self.nested_of_box, [SAME_SHAPE] + list(top), saved)

    def on_nested_of(self):
        """A shape picked in Copies of: the copies are made of it (its own copy of the drawing goes into each
        shape, like a placed shape's), or of the shape itself again."""
        name = self.nested_of_box.get()
        if name == SAME_SHAPE:
            return self.set_nested("of", None)
        tpl = self.custom_template(name)
        if not tpl:
            messagebox.showerror(tr("panel_custom.spiderweb"), tr("panel_custom.couldn_t_read_the_shape", name=name))
            return self.sync_custom()
        self.set_nested("of", {"name": name, "strokes": copy.deepcopy(tpl[0])})

    def nested_targets(self):
        """The custom targets that have a Nested fill (the settings for new ones too)."""
        return [t for t in self.custom_targets() if nested_on(t)]

    def pick_nested(self):
        """Nested picked as the Inside fill: the targets get it (their own settings kept from before, else the
        defaults), Notes from their Fill / Spam (else Fill). Not text, pasted notes or Hz bass."""
        tgts = [t for t in self.custom_targets() if not t.get("text") and "notes" not in t]
        if any(t.get("hz") for t in tgts):  # (they don't mix: the others change, after a warning)
            tgts = self.skip_hz(tgts)
        tgts = [t for t in tgts if not nested_on(t)]
        if not tgts:
            return self.sync_custom()
        placed = [t for t in tgts if t is not self.custom_defaults]
        new = [dict(t, fill=t["fill"] if t["fill"] in ("fill", "spam") else "fill",
                    nested=t.get("nested") or dict(NESTED_DEFAULTS)) for t in placed]
        if not self.confirm_big(new):
            return self.sync_custom()
        if placed:
            self.push_undo(name=NESTED_UNDO)
        for t in tgts:
            if t["fill"] not in ("fill", "spam"):
                t["fill"] = "fill"
            t.pop("tiles", None)  # (one Inside pattern at a time)
            t.pop("ripples", None)
            if not t.get("nested"):
                t["nested"] = dict(NESTED_DEFAULTS)
        self.shapes_changed()
        self.sync_custom()
        self.sync_colours()
        self.tips.show("fill", wait=True)

    def set_nested_notes(self, fill):
        """Notes Fill / Spam of the Nested targets (their fill; the Nested settings stay)."""
        if self._loading:
            return
        tgts = [t for t in self.nested_targets() if t["fill"] != fill]
        self.change_nested(tgts, lambda t: t.update(fill=fill), tr("panel_custom.nested_notes"))

    def set_nested(self, key, value):
        """One Nested setting of every Nested target."""
        if self._loading:
            return
        tgts = [t for t in self.nested_targets() if t["nested"].get(key) != value]

        def put(t):
            t["nested"] = dict(t["nested"], **{key: copy.deepcopy(value)})
            if value is None:  # (off: not kept at all)
                t["nested"].pop(key)

        self.change_nested(tgts, put, NESTED_UNDO)

    def change_nested(self, tgts, fn, name):
        """fn(t) for the targets, as one undo step (asked first when it makes a lot of notes)."""
        if not tgts:
            return self.sync_custom()
        placed = [t for t in tgts if t is not self.custom_defaults]
        tries = [copy.deepcopy(t) for t in placed]
        for t in tries:
            fn(t)
        if not self.confirm_big(tries):
            return self.sync_custom()
        if placed:
            self.push_undo(name=name)
        for t in tgts:
            fn(t)
        self.shapes_changed()
        self.sync_custom()
        self.sync_colours()

    def on_nested_box(self, key, left=False):
        """A Nested number box entered (left: only left, so nothing happens unless its number was changed)."""
        e, var = self.nested_boxes[key]
        if self._loading or left and unchanged(e):
            return
        spec = next(b for line in BOXES for b in line if b[0] == key)
        _, _, _, lo, hi, _, _, to_setting = spec
        try:
            x = calc(var.get())
            if not lo <= x <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        self.set_nested(key, to_setting(x))
        good(e)
