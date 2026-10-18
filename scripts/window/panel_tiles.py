"""The side panel's rows for Inside fill "Tiles" (notes/tiles.py): Notes Fill / Spam, Tile shape, Height, Length,
gaps, Layout, Flip, Tile colours, Colours and Only whole tiles. Mixed into App (beside NestedPanel, whose
change_nested makes the undo steps)."""

import copy
import math
import tkinter as tk
from tkinter import messagebox, ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import shelve_patterns, unshelve
from notes.tiles import BY, FLIPS, LAYOUTS, MOST_BEATS, MOST_COLOURS, MOST_KEYS, TILES_DEFAULTS, too_many
from window import look
from window.drawer import library_lists
from window.panel_nested import DROP_WIDTH, NOTES_CHOICES, drop_row
from window.widgets import Scrub, Tooltip, bad, good, leave_box, set_names, unchanged, unit_word

# the number boxes: (setting, label, unit, lowest, highest, Scrub steps, in ticks); two a row
BOXES = [
    [("keys", "tiles_keys", "tiles_keys_unit", 1, MOST_KEYS, (1, 5, 1), False),
     ("length", "tiles_length", "tiles_ticks_unit", 1, 10 ** 7, (10, 120, 1), True)],
    [("gap_keys", "tiles_gap_keys", "tiles_keys_unit", 0, MOST_KEYS, (1, 5, 1), False),
     ("gap", "tiles_gap", "tiles_ticks_unit", 0, 10 ** 7, (10, 120, 1), True)],
]
DROPS = [("layout", LAYOUTS), ("flip", FLIPS), ("by", BY)]
TILES_UNDO = tr("panel_custom.tiles")


def tiles_on(t):
    """Custom shape (or the settings for new ones) t with a Tiles fill (Nested wins if it has both)."""
    return (bool(t.get("tiles")) and t.get("fill") in ("fill", "spam") and not t.get("text") and "notes" not in t
            and not t.get("hz") and not t.get("nested"))


class TilesPanel:
    """Mixed into App."""

    def _build_tiles(self, opts):
        """The Tiles rows, put in self.custom_rows (shown by sync_custom while Tiles is the Inside fill)."""
        self.tiles_rows, self.tiles_boxes, self.tiles_drops = [], {}, {}

        def row_of():
            row = ttk.Frame(opts)
            self.tiles_rows.append(row)
            return row

        row, self.tiles_notes_box = drop_row(self, opts, "nested_notes", [t for _, t in NOTES_CHOICES])
        self.tiles_rows.append(row)
        self.tiles_notes_box.bind("<<ComboboxSelected>>", lambda e: (
            self.set_tiles_fill(NOTES_CHOICES[e.widget.current()][0]), self.roll.focus_set()))
        row, self.tiles_of_box = drop_row(self, opts, "tiles_of", names=self.refresh_tiles_names)
        self.tiles_rows.append(row)
        self.tiles_of_box.bind("<<ComboboxSelected>>", lambda e: (self.on_tiles_of(), self.roll.focus_set()))
        for line in BOXES:
            row = row_of()
            for n, (key, text, unit, lo, hi, steps, _) in enumerate(line):
                lb = ttk.Label(row, text=tr("panel_custom." + text))
                lb.grid(row=0, column=3 * n, sticky="w", padx=(8 if n else 0, 0))
                var = tk.StringVar()
                e = ttk.Entry(row, textvariable=var, width=5)
                e.grid(row=0, column=3 * n + 1, sticky="w", padx=4)
                unit_word(ttk.Label(row, foreground=look.HINT), var, "panel_custom." + unit).grid(
                    row=0, column=3 * n + 2, sticky="w")
                apply = (lambda left=False, key=key: self.on_tiles_box(key, left))
                e.bind("<Return>", lambda ev, apply=apply: apply())
                leave_box(self, e, var, apply)
                Scrub(self, [(e, var, apply)], steps, lo, hi, label=lb)
                for w in (lb, e):
                    Tooltip(w, tr("panel_custom." + text + "_tip"))
                self.tiles_boxes[key] = (e, var)
        for key, values in DROPS:
            texts = [tr("panel_custom.tiles_" + key + "_" + v) for v in values]
            if key == "by":  # "Colours [4] by [Diagonals]": how many colours, then which tile gets which
                row = row_of()
                lb = ttk.Label(row, text=tr("panel_custom.nested_colours"))
                lb.grid(row=0, column=0, sticky="w")
                var = tk.StringVar()
                e = ttk.Entry(row, textvariable=var, width=5)
                e.grid(row=0, column=1, sticky="w", padx=4)
                apply = (lambda left=False: self.on_tiles_box("colours", left))
                e.bind("<Return>", lambda ev: apply())
                leave_box(self, e, var, apply)
                Scrub(self, [(e, var, apply)], (1, 1, 1), 1, MOST_COLOURS, label=lb)
                for w in (lb, e):
                    Tooltip(w, tr("panel_custom.tiles_colours_tip"))
                self.tiles_boxes["colours"] = (e, var)
                by = ttk.Label(row, text=tr("panel_custom.tiles_by"))
                by.grid(row=0, column=2, sticky="w")
                box = ttk.Combobox(row, values=texts, state="readonly", width=12)  # (grows to fit: fit_dropdowns)
                box.grid(row=0, column=3, columnspan=3, sticky="w", padx=(8, 0))
                for w in (by, box):
                    Tooltip(w, tr("panel_custom.tiles_by_tip"))
            else:
                row, box = drop_row(self, opts, "tiles_" + key, texts)
                self.tiles_rows.append(row)
            box.bind("<<ComboboxSelected>>", lambda e, key=key, values=values: (
                self.set_tiles(key, values[e.widget.current()]), self.roll.focus_set()))
            self.tiles_drops[key] = box
        self.tiles_table = list(self.tiles_rows)  # (lined up with the Nested ones, panel_custom)
        # too many tiles for the shape: it's left empty (tiles.too_many)
        self.tiles_warn = ttk.Label(opts, text=tr("panel_custom.tiles_too_many"), foreground=look.WARN,
                                    font=look.font(8), wraplength=int(260 * self.scale), justify="left")
        self.tiles_rows.append(self.tiles_warn)
        self.tiles_whole_var = tk.BooleanVar(value=False)
        b = ttk.Checkbutton(opts, text=tr("panel_custom.tiles_whole"), variable=self.tiles_whole_var,
                            command=lambda: self.set_tiles("whole", self.tiles_whole_var.get()))
        Tooltip(b, tr("panel_custom.tiles_whole_tip"))
        self.tiles_rows.append(b)
        for row in self.tiles_rows:
            self.custom_rows.append((row, dict(anchor="w", pady=(1, 0))))

    def shown_tiles(self, key, cfg):
        """A Tiles box's number as shown: keys as they are, lengths in whole ticks."""
        v = cfg.get(key, TILES_DEFAULTS[key])
        return fmt(math.floor(v * self.ppq + 0.5)) if key in ("length", "gap") else fmt(v)

    def sync_tiles(self, t):
        """The Tiles rows show t's settings (the first target's)."""
        cfg = t.get("tiles") or TILES_DEFAULTS
        self._loading = True
        self.tiles_notes_box.current(1 if t.get("fill") == "spam" else 0)
        self.refresh_tiles_names()
        self.tiles_of_box.set(cfg["of"]["name"])
        for key, (e, var) in self.tiles_boxes.items():
            var.set(self.shown_tiles(key, cfg))
            e.config(style="TEntry")
        for key, values in DROPS:
            self.tiles_drops[key].current(values.index(cfg.get(key, TILES_DEFAULTS[key])))
        self.tiles_whole_var.set(bool(cfg.get("whole")))
        self._loading = False

    def tiles_row_states(self, t, on):
        """{row: shown} for sync_custom's show_custom_rows (on: Tiles is the Inside fill shown); the warning only
        while t (a placed shape) has too many tiles."""
        warn = on and t is not self.custom_defaults and too_many(t, self.ppq)
        return {w: on and (w is not self.tiles_warn or warn) for w in self.tiles_rows}

    def refresh_tiles_names(self):
        set_names(self.tiles_of_box, *library_lists())

    def tiles_targets(self):
        return [t for t in self.custom_targets() if tiles_on(t)]

    def pick_tiles(self):
        """Tiles picked as the Inside fill (their own settings kept from before, else the defaults; Notes from their
        Fill / Spam, else Fill). Not text, pasted notes or Hz bass."""
        tgts = [t for t in self.custom_targets() if not t.get("text") and "notes" not in t]
        if any(t.get("hz") for t in tgts):  # (they don't mix: the others change, after a warning)
            tgts = self.skip_hz(tgts)
        tgts = [t for t in tgts if not tiles_on(t)]

        def put(t):
            if t["fill"] not in ("fill", "spam"):
                t["fill"] = "fill"
            shelve_patterns(t, keep="tiles")  # (one Inside pattern at a time: the others' settings kept)
            if not t.get("tiles"):  # (its own settings from before, else the defaults)
                t["tiles"] = unshelve(t, "tiles") or copy.deepcopy(TILES_DEFAULTS)

        self.change_nested(tgts, put, TILES_UNDO)
        self.tips.show("fill", wait=True)

    def set_tiles_fill(self, fill):
        """Notes Fill / Spam of the Tiles targets."""
        if self._loading:
            return
        tgts = [t for t in self.tiles_targets() if t["fill"] != fill]
        self.change_nested(tgts, lambda t: t.update(fill=fill), tr("panel_custom.nested_notes"))

    def set_tiles(self, key, value):
        """One Tiles setting of every Tiles target."""
        if self._loading:
            return
        tgts = [t for t in self.tiles_targets() if t["tiles"].get(key) != value]

        def put(t):
            t["tiles"] = dict(t["tiles"], **{key: copy.deepcopy(value)})

        self.change_nested(tgts, put, TILES_UNDO)

    def on_tiles_of(self):
        """A tile shape picked: its own copy of the drawing goes into each shape."""
        name = self.tiles_of_box.get()
        tpl = self.custom_template(name)
        if not tpl:
            messagebox.showerror(tr("panel_custom.spiderweb"), tr("panel_custom.couldn_t_read_the_shape", name=name))
            return self.sync_custom()
        self.set_tiles("of", {"name": name, "strokes": copy.deepcopy(tpl[0])})

    def on_tiles_box(self, key, left=False):
        """A Tiles number box entered (lengths typed in whole ticks; left: only left, nothing unless changed)."""
        e, var = self.tiles_boxes[key]
        if self._loading or left and unchanged(e):
            return
        spec = next((b for line in BOXES for b in line if b[0] == key), None)
        lo, hi, ticks = (spec[3], spec[4], spec[6]) if spec else (1, MOST_COLOURS, False)
        try:
            x = math.floor(calc(var.get()) + 0.5)
            if not lo <= x <= hi or ticks and x > MOST_BEATS * self.ppq:  # (no more than a saved shape keeps)
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        self.set_tiles(key, x / self.ppq if ticks else int(x))
        good(e)
