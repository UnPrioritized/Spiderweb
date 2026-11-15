"""The side panel's polygon settings (polygon.py): how many points, the kind (polygon / star / crossing star), a
star's inner size and a crossing star's skip. They change the selected polygons, and are what the next polygon drawn
gets. The shape of the sides and the pattern along them are formulas (right-click ▸ Formula: formula_host.py's
PolygonHost; their numbers show in the formula part of the panel)."""

import random
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import fmt
from notes.polygon import (MAX_POINTS, MIN_POINTS, MOST_SEED, MOST_TURN, MOST_TWIST, STYLES, UNEVEN_HOWS,
                           update_polygon)
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, pack_shown, read_number, unchanged

STYLE_NAMES = [tr("panel_polygon.style_" + s) for s in STYLES]
HOW_NAMES = [tr("panel_polygon.uneven_" + s) for s in UNEVEN_HOWS]
# name: (lowest, highest, steps (step, Shift step, Ctrl step), whole numbers only)
NUMBERS = {"points": (MIN_POINTS, MAX_POINTS, (1, 5, 1), True), "inner": (0, 1000, (5, 25, 1), False),
           "skip": (1, MAX_POINTS - 1, (1, 5, 1), True), "twist": (-MOST_TWIST, MOST_TWIST, (5, 25, 1), False),
           "turn": (-MOST_TURN, MOST_TURN, (5, 15, 1), False), "uneven": (0, 100, (5, 25, 1), False)}
SHOWN_FOR = {"star": ("inner", "turn"), "cross": ("skip",)}  # (only the boxes the kind uses show, user)


class PolygonPanel:
    """Mixed into App."""

    def _build_polygon(self):
        box = self.polygon_box = ttk.Frame(self.custom_box)  # (packed at the top of the custom part when shown)
        self.polygon_vars, self.polygon_entries, self.polygon_cells = {}, {}, {}
        row1 = ttk.Frame(box)
        row_t = ttk.Frame(box)
        row2 = self.polygon_row2 = ttk.Frame(box)  # (only for a star / crossing star)
        row1.pack(fill="x")
        row_t.pack(fill="x", pady=(2, 0))
        row_u = ttk.Frame(box)
        row_u.pack(fill="x", pady=(2, 0))
        for row, name in ((row1, "points"), (row_t, "twist"), (row_u, "uneven"), (row2, "inner"), (row2, "turn"),
                          (row2, "skip")):
            cell = self.polygon_cells[name] = ttk.Frame(row)
            if row is not row2:
                cell.pack(side="left", padx=(0, 10))
            lb = ttk.Label(cell, text=tr("panel_polygon." + name))
            lb.pack(side="left")
            var = self.polygon_vars[name] = tk.StringVar()
            e = self.polygon_entries[name] = ttk.Entry(cell, textvariable=var, width=5)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, n=name: self.on_polygon_number(n))
            leave_box(self, e, var, lambda left, n=name: self.on_polygon_number(n, left))
            lo, hi, steps, _ = NUMBERS[name]
            Scrub(self, [(e, var, lambda n=name: self.on_polygon_number(n))], steps, lo, hi, label=lb)
            for w in (lb, e):
                Tooltip(w, tr("panel_polygon." + name + "_tip"))
            if name == "points":
                ttk.Label(row1, text=tr("panel_polygon.kind")).pack(side="left")
                self.polygon_style = tk.StringVar()
                cb = self.polygon_style_box = ttk.Combobox(row1, textvariable=self.polygon_style, values=STYLE_NAMES,
                                                           state="readonly", width=max(map(len, STYLE_NAMES)))
                cb.pack(side="left", padx=(4, 0))
                cb.bind("<<ComboboxSelected>>", lambda ev: (self.on_polygon_style(), self.roll.focus_set()))
                Tooltip(cb, tr("panel_polygon.kind_tip"))
            if name == "uneven":  # how: Random (+ New random) / In turns
                self.polygon_how = tk.StringVar()
                cb = self.polygon_how_box = ttk.Combobox(row_u, textvariable=self.polygon_how, values=HOW_NAMES,
                                                         state="readonly", width=max(map(len, HOW_NAMES)))
                cb.pack(side="left")
                cb.bind("<<ComboboxSelected>>", lambda ev: (self.on_polygon_how(), self.roll.focus_set()))
                Tooltip(cb, tr("panel_polygon.uneven_how_tip"))
                b = self.polygon_reroll = ttk.Button(row_u, text=tr("panel_polygon.new_random"),
                                                     command=self.on_polygon_reroll)
                Tooltip(b, tr("panel_polygon.new_random_tip"))
        # 2 points and nothing on the sides: just a flat line (a pattern on the sides changes its shape)
        self.polygon_flat = ttk.Label(box, text=tr("panel_polygon.flat"), foreground=look.WARN, font=look.font(8),
                                      wraplength=int(260 * self.scale), justify="left")

    def polygon_shapes(self):
        """The selected polygons (custom shapes made by the Polygon tool)."""
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i].get("polygon")]

    def polygon_targets(self):
        """What the polygon settings change: the selected polygons' settings, or (Polygon tool, nothing selected)
        the settings for new ones."""
        shapes = self.polygon_shapes()
        if shapes:
            return [sh["polygon"] for sh in shapes]
        return [self.polygon_defaults] if not self.sels and self.tool.get() == "polygon" else []

    def sync_polygon(self):
        tgts = self.polygon_targets()
        if not tgts:
            self.polygon_box.pack_forget()
            return
        if not self.polygon_box.winfo_manager():
            first = next(w for w in self.custom_box.pack_slaves())  # (at the top)
            self.polygon_box.pack(fill="x", pady=(0, 4), before=first)
        d = tgts[0]
        self._loading = True
        for name, var in self.polygon_vars.items():
            var.set(fmt(d[name]))
            self.polygon_entries[name].config(style="TEntry")
        self.polygon_style.set(STYLE_NAMES[STYLES.index(d["style"])])
        self.polygon_how.set(HOW_NAMES[UNEVEN_HOWS.index(d["uneven_how"])])
        self._loading = False
        pack_shown(self.polygon_reroll, d["uneven_how"] == "random", side="left", padx=(6, 0))
        want = SHOWN_FOR.get(d["style"], ())
        for name in ("inner", "turn", "skip"):
            self.polygon_entries[name].config(state="normal" if name in want else "disabled")
            pack_shown(self.polygon_cells[name], name in want, side="left", padx=(0, 10))
        pack_shown(self.polygon_row2, want, fill="x", pady=(2, 0))
        flat = d["points"] == 2 and d["style"] != "star" and not (d.get("shape") or d.get("pattern"))
        pack_shown(self.polygon_flat, flat, fill="x", pady=(2, 0))

    def set_polygon(self, key, value, edit_key=None):
        """A polygon setting changed: on the selected polygons (their strokes made again) and for new ones.
        edit_key: typing / scrubbing in a box (one undo step per box, App.begin_edit), else its own step."""
        shapes = [sh for sh in self.polygon_shapes() if sh["polygon"][key] != value]
        if shapes:
            if edit_key:
                self.begin_edit(edit_key)
            else:
                self.push_undo(name=tr("history.polygon"))
            for sh in shapes:
                sh["polygon"][key] = value
                update_polygon(sh)
        self.polygon_defaults[key] = value
        if shapes:
            self.shapes_changed()
        self.sync_polygon()
        self.schedule_autosave()

    def on_polygon_number(self, name, left=False):
        """A number box (Enter, stepped, or left: widgets.leave_box)."""
        e = self.polygon_entries[name]
        if self._loading or str(e.cget("state")) == "disabled" or left and unchanged(e):
            return
        lo, hi, _, whole = NUMBERS[name]
        value = read_number(self.polygon_vars[name].get(), lo, hi)
        if value is None or whole and value != int(value):
            bad(e)
            return
        good(e)
        self.set_polygon(name, int(value) if whole else value, ("polygon", tuple(sorted(self.sels)), name))

    def on_polygon_style(self):
        if self._loading:
            return
        picked = self.polygon_style_box.current()  # (the place: two choices translated alike stay apart)
        self.set_polygon("style", STYLES[picked if picked >= 0 else STYLE_NAMES.index(self.polygon_style.get())])

    def on_polygon_how(self):
        if self._loading:
            return
        picked = self.polygon_how_box.current()
        self.set_polygon("uneven_how", UNEVEN_HOWS[picked if picked >= 0 else HOW_NAMES.index(self.polygon_how.get())])

    def on_polygon_reroll(self):
        """New random: each selected polygon (and the next one drawn) gets its own new uneven corners, one step."""
        shapes = self.polygon_shapes()
        if shapes:
            self.push_undo(name=tr("history.polygon"))
            for sh in shapes:
                sh["polygon"]["seed"] = random.randint(1, MOST_SEED)
                update_polygon(sh)
        self.polygon_defaults["seed"] = random.randint(1, MOST_SEED)
        if shapes:
            self.shapes_changed()
        self.sync_polygon()
        self.schedule_autosave()

