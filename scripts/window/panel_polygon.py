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
                           LENGTH_RANGE, MOST_LENGTHS, update_polygon)
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, pack_shown, read_number, unchanged

STYLE_NAMES = [tr("panel_polygon.style_" + s) for s in STYLES]
HOW_NAMES = [tr("panel_polygon.uneven_" + s) for s in UNEVEN_HOWS]
LENGTHS_A_LINE = 5  # Lengths boxes a line in the side panel (after "Lengths [+] [−]")
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
        row_u = self.polygon_row_u = ttk.Frame(box)
        row_u.pack(fill="x", pady=(2, 0))
        # Typed lengths: "Lengths" + a box per side length (type / drag) + [+] [−] (shown in that mode only)
        row_l = self.polygon_len_row = ttk.Frame(box)
        lb = ttk.Label(row_l, text=tr("panel_polygon.lengths"))
        lb.pack(side="left", anchor="n")
        Tooltip(lb, tr("panel_polygon.lengths_tip"))
        # + / − right after the word, anchored (user: buttons moving as boxes came were hard to click again)
        # (no padding: as tall as the number boxes, user: the usual buttons made the row stand out)
        ttk.Style(self).configure("Step.TButton", padding=0)
        self.polygon_len_more = ttk.Button(row_l, text="+", width=2, style="Step.TButton",
                                           command=lambda: self.on_polygon_len_count(1))
        self.polygon_len_less = ttk.Button(row_l, text="−", width=2, style="Step.TButton",
                                           command=lambda: self.on_polygon_len_count(-1))
        self.polygon_len_more.pack(side="left", anchor="n", padx=(4, 0))
        self.polygon_len_less.pack(side="left", anchor="n", padx=(2, 0))
        self.polygon_len_grid = ttk.Frame(row_l)
        self.polygon_len_grid.pack(side="left", anchor="n", padx=(8, 0))
        self.polygon_len_boxes = []  # [(entry, var)], made again when the count changes
        Tooltip(self.polygon_len_more, tr("panel_polygon.lengths_more_tip"))
        Tooltip(self.polygon_len_less, tr("panel_polygon.lengths_less_tip"))
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
            if name == "uneven":  # how: Random (+ New random) / Typed lengths (+ their box)
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
        typed = d["uneven_how"] == "lengths"
        if typed:
            self.sync_polygon_lengths(d["lengths"])
        self._loading = False
        pack_shown(self.polygon_reroll, not typed, side="left", padx=(6, 0))
        pack_shown(self.polygon_len_row, typed, fill="x", pady=(2, 0), after=self.polygon_row_u)
        want = SHOWN_FOR.get(d["style"], ())
        for name in ("inner", "turn", "skip"):
            self.polygon_entries[name].config(state="normal" if name in want else "disabled")
            pack_shown(self.polygon_cells[name], name in want, side="left", padx=(0, 10))
        pack_shown(self.polygon_row2, want, fill="x", pady=(2, 0))
        flat = (d["points"] == 2 and d["style"] != "star" and not (d.get("shape") or d.get("pattern"))
                and not d.get("twist") and not d.get("uneven"))
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

    def sync_polygon_lengths(self, lengths):
        """The Lengths boxes: one per typed side length (made again only when the count changes), LENGTHS_A_LINE a
        line (+ and − stay put before them)."""
        boxes = self.polygon_len_boxes
        if len(boxes) != len(lengths):
            for e, _ in boxes:
                e.destroy()
            boxes.clear()
            for i in range(len(lengths)):
                var = tk.StringVar()
                e = ttk.Entry(self.polygon_len_grid, textvariable=var, width=4)
                e.bind("<Return>", lambda ev, i=i: self.on_polygon_length(i))
                leave_box(self, e, var, lambda left, i=i: self.on_polygon_length(i, left))
                Scrub(self, [(e, var, lambda i=i: self.on_polygon_length(i))], (0.5, 1, 0.1), *LENGTH_RANGE,
                      drag_box=True)
                Tooltip(e, tr("panel_polygon.lengths_tip"))
                e.grid(row=i // LENGTHS_A_LINE, column=i % LENGTHS_A_LINE, padx=(0, 3), pady=(0, 2))
                boxes.append((e, var))
        for (e, var), x in zip(boxes, lengths):
            var.set(fmt(x))
            e.config(style="TEntry")
        self.polygon_len_more.config(state="normal" if len(lengths) < MOST_LENGTHS else "disabled")
        self.polygon_len_less.config(state="normal" if len(lengths) > 1 else "disabled")

    def on_polygon_length(self, i, left=False):
        """One Lengths box (Enter, stepped, or left: widgets.leave_box)."""
        if self._loading or i >= len(self.polygon_len_boxes):
            return
        e, var = self.polygon_len_boxes[i]
        if left and unchanged(e):
            return
        value = read_number(var.get(), *LENGTH_RANGE)
        if value is None:
            bad(e)
            return
        good(e)
        now = list(self.polygon_targets()[0]["lengths"])
        if i < len(now):
            now[i] = value
            self.set_polygon("lengths", now, ("polygon", tuple(sorted(self.sels)), "lengths", i))

    def on_polygon_len_count(self, step):
        """+ = one more length (like the last), − = the last one gone (at least one stays)."""
        now = list(self.polygon_targets()[0]["lengths"])
        if step > 0 and len(now) < MOST_LENGTHS:
            self.set_polygon("lengths", now + [now[-1]])
        elif step < 0 and len(now) > 1:
            self.set_polygon("lengths", now[:-1])

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

