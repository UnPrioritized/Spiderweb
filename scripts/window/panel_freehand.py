"""The side panel's freehand setting: how much a freehand stroke is made perfect (smooth.py)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import refit, uv_k
from notes.sliced import pen_set
from notes.pen import (ENDS, PEN_DEFAULT, TAPER_MAX, THICK_MAX, THICK_MIN, clean_pen, clean_thick, clean_ink,
                       pen_of)
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, unchanged, unit_word

TIP = tr("panel_freehand.makes_the_freehand_stroke_perfect_0")
THICK_TIP = tr("panel_freehand.thickness_tip")


class FreehandPanel:
    """Mixed into App."""

    def _build_freehand(self):
        self.free_box = ttk.Frame(self.settings)
        box = ttk.Frame(self.free_box)
        box.pack(side="top", anchor="w")
        lb = ttk.Label(box, text=tr("panel_freehand.straighten"))
        lb.pack(side="left")
        self.free_var = tk.StringVar()
        e = self.free_entry = ttk.Entry(box, textvariable=self.free_var, width=5)
        e.pack(side="left", padx=(5, 3))
        e.bind("<Return>", lambda ev: self.on_free_entry())
        leave_box(self, e, self.free_var, self.on_free_entry)
        Scrub(self, [(e, self.free_var, self.on_free_entry)], (1, 10, 1), 0, 100, label=lb)
        ttk.Label(box, text=tr("panel_freehand.0_as_drawn_100_simplest"), foreground=look.HINT).pack(side="left")
        for w in (lb, e):
            Tooltip(w, TIP)
        # Thickness: a wide pen (notes/pen.py); the stroke itself stays the thin line (user)
        row = ttk.Frame(self.free_box)
        row.pack(side="top", anchor="w", pady=(4, 0))
        lb = ttk.Label(row, text=tr("panel_freehand.thickness"))
        lb.pack(side="left")
        self.thick_var = tk.StringVar()
        e = self.thick_entry = ttk.Entry(row, textvariable=self.thick_var, width=5)
        e.pack(side="left", padx=(5, 3))
        e.bind("<Return>", lambda ev: self.on_thick_entry())
        leave_box(self, e, self.thick_var, self.on_thick_entry)
        Scrub(self, [(e, self.thick_var, self.on_thick_entry)], (1, 5, 0.25), THICK_MIN, THICK_MAX, label=lb)
        unit_word(ttk.Label(row, foreground=look.HINT), self.thick_var, "unit.keys", "unit.key").pack(side="left")
        for w in (lb, e):
            Tooltip(w, THICK_TIP)
        # the pen's shape (stage D, user): Ends round / flat, Taper %, Width from speed; greyed at Thickness 1
        row = ttk.Frame(self.free_box)
        row.pack(side="top", anchor="w", pady=(4, 0))
        self.pen_ends_word = ttk.Label(row, text=tr("panel_freehand.ends"))
        self.pen_ends_word.pack(side="left")
        self.pen_ends_names = [tr("panel_freehand.ends_" + k) for k in ENDS]
        self.pen_ends_box = ttk.Combobox(row, state="readonly", values=self.pen_ends_names,
                                         width=max(len(n) for n in self.pen_ends_names) + 1)
        self.pen_ends_box.pack(side="left", padx=(5, 10))
        self.pen_ends_box.bind("<<ComboboxSelected>>", lambda ev: (self.on_pen("ends"), self.roll.focus_set()))
        lb = self.pen_taper_word = ttk.Label(row, text=tr("panel_freehand.taper"))
        lb.pack(side="left")
        self.pen_taper_var = tk.StringVar()
        e = self.pen_taper_entry = ttk.Entry(row, textvariable=self.pen_taper_var, width=4)
        e.pack(side="left", padx=(5, 3))
        e.bind("<Return>", lambda ev: self.on_pen("taper"))
        leave_box(self, e, self.pen_taper_var, lambda left=False: self.on_pen("taper", left))
        Scrub(self, [(e, self.pen_taper_var, lambda: self.on_pen("taper"))], (1, 10, 0.25), 0, TAPER_MAX, label=lb)
        self.pen_taper_unit = ttk.Label(row, text="%", foreground=look.HINT)
        self.pen_taper_unit.pack(side="left")
        Tooltip(self.pen_ends_word, tr("panel_freehand.ends_tip"))
        Tooltip(self.pen_ends_box, tr("panel_freehand.ends_tip"))
        for w in (lb, e):
            Tooltip(w, tr("panel_freehand.taper_tip"))
        self.pen_speed_on = tk.BooleanVar()
        self.pen_speed_tick = ttk.Checkbutton(self.free_box, text=tr("panel_freehand.speed"),
                                              variable=self.pen_speed_on, command=lambda: self.on_pen("speed"))
        self.pen_speed_tick.pack(side="top", anchor="w", pady=(4, 0))
        self.pen_speed_tip = Tooltip(self.pen_speed_tick, tr("panel_freehand.speed_tip"))

    def free_targets(self):
        """What the setting changes: [(the dict holding "smooth", its custom shape or None)] — the selected
        freehand shapes, or the picked freehand stroke of the selected custom shape."""
        shapes = [(self.shapes[i], None) for i in sorted(self.sels) if self.shapes[i]["kind"] == "free"]
        if shapes:
            return shapes
        sh = self.selected()
        k = self.roll.picked_stroke(sh) if sh and len(self.sels) == 1 else None
        return [(sh["strokes"][k], sh)] if k is not None and sh["strokes"][k].get("free") else []

    def sync_freehand(self):
        tgts = self.free_targets()
        # nothing selected with the Freehand tool: the number new strokes get
        self._rows["free"] = bool(tgts) or (not self.sels and self.tool.get() == "free")
        self.layout_rows()
        if not self._rows["free"]:
            return
        self._loading = True
        self.free_var.set(fmt(tgts[0][0].get("smooth", 0) if tgts else self.free_smooth))
        self.free_entry.config(style="TEntry")
        # Thickness: freehand shapes and freehand strokes inside a live shape (user 2026-11-19); several with
        # different numbers = an empty box (widgets.same_or_blank, like every panel box: hunt 2026-11-19)
        thicks = [clean_thick(d.get("thick", THICK_MIN)) for d, _ in tgts]
        same_or_blank(self.thick_entry, self.thick_var, [fmt(t) for t in thicks] or [fmt(self.free_thick)])
        self.thick_entry.config(style="TEntry")
        self.sync_pen(tgts, thicks)
        self._loading = False

    def sync_pen(self, tgts, thicks):
        """The pen's shape row: the targets' (or the new strokes'), mixed = an empty box / "Mixed" / a half tick;
        usable when ANY target is wider than 1 (nothing selected: the new-stroke Thickness); Width from speed
        greyed only when no target has drawing-speed widths (no ink: pen.py)."""
        pens = [pen_of(d) for d, _ in tgts] or [dict(PEN_DEFAULT, **self.free_pen)]
        wide = any(t > THICK_MIN for t in thicks) if tgts else self.free_thick > THICK_MIN
        state = ["!disabled" if wide else "disabled"]
        ends = {p["ends"] for p in pens}
        self.pen_ends_box.set(self.pen_ends_names[ENDS.index(ends.pop())] if len(ends) == 1 else tr("widgets.mixed"))
        self.pen_ends_box.state(["readonly"] + state)
        same_or_blank(self.pen_taper_entry, self.pen_taper_var, [fmt(p["taper"]) for p in pens])
        self.pen_taper_entry.state(state)
        self.pen_taper_entry.config(style="TEntry")
        for w in (self.pen_ends_word, self.pen_taper_word, self.pen_taper_unit):
            w.config(foreground="" if wide else look.FAINT_TEXT)
        self.pen_taper_unit.config(foreground=look.HINT if wide else look.FAINT_TEXT)
        timed = not tgts or any(clean_ink(d.get("ink"), len(d.get("pts") or ())) for d, _ in tgts)
        speeds = [p["speed"] for p in pens]
        self.pen_speed_on.set(all(speeds))
        self.pen_speed_tick.state(["alternate" if any(speeds) and not all(speeds) else "!alternate"])
        self.pen_speed_tick.state(["!disabled" if wide and timed else "disabled"])
        self.pen_speed_tip.set_text(tr("panel_freehand.speed_tip") if timed else tr("panel_freehand.speed_no_times"))

    def on_pen(self, key, left=False):
        """Ends / Taper / Width from speed changed: the selected freehand shapes / picked stroke (one undo step)
        and the new strokes' pen."""
        if self._loading:
            return
        if key == "ends":
            if self.pen_ends_box.get() not in self.pen_ends_names:  # ("Mixed" shown: nothing picked)
                return
            value = ENDS[self.pen_ends_names.index(self.pen_ends_box.get())]
        elif key == "taper":
            if left and unchanged(self.pen_taper_entry):
                return
            try:
                value = calc(self.pen_taper_var.get())
                if not 0 <= value <= TAPER_MAX:
                    raise ValueError
            except ValueError:
                bad(self.pen_taper_entry)
                return
            good(self.pen_taper_entry)
            value = round(float(value), 2)
        else:
            value = bool(self.pen_speed_on.get())
        self.free_pen = clean_pen({**PEN_DEFAULT, **self.free_pen, key: value}) or {}  # new strokes too
        tgts = [d for d, _ in self.free_targets() if pen_of(d)[key] != value]
        if not tgts:
            return self.sync_freehand()
        self.begin_edit(("pen", key, tuple(sorted(self.sels)), self.stroke))
        for d in tgts:  # (a sliced piece stays a piece: sliced.pen_set)
            pen_set(d, "pen", clean_pen({**pen_of(d), key: value}))
        self.shapes_changed()
        self.sync_freehand()

    def on_thick_entry(self, left=False):
        """The Thickness box (Enter, stepped, or left: widgets.leave_box)."""
        if self._loading or left and unchanged(self.thick_entry):
            return
        try:
            value = calc(self.thick_var.get())
            if not THICK_MIN <= value <= THICK_MAX:
                raise ValueError
        except ValueError:
            bad(self.thick_entry)
            return
        good(self.thick_entry)
        value = clean_thick(value)
        self.free_thick = value  # new strokes get it too
        tgts = [(d, owner) for d, owner in self.free_targets() if clean_thick(d.get("thick", THICK_MIN)) != value]
        if not tgts:
            return self.sync_freehand()
        self.begin_edit(("thick", tuple(sorted(self.sels)), self.stroke))
        k = self.roll.sy / self.roll.sx if self.roll.sx else 0.25  # (round as the roll looks now)
        for d, owner in tgts:  # (a sliced piece stays a piece: sliced.pen_set)
            if value > THICK_MIN:
                pen_set(d, "thick", value)
                if "k" not in d:  # (one already drawn keeps its own: the pen round as it was drawn)
                    pen_set(d, "k", k if owner is None else uv_k(owner["pts"], k))
                if "pk" not in d:  # (the pen's own: Straighten re-reading "k" never moves it, pen.pen_k)
                    pen_set(d, "pk", d["k"])
            else:
                pen_set(d, "thick")
        self.shapes_changed()
        self.sync_freehand()  # (the pen's shape row greys / ungreys with it)
        self.sync_by_snap()  # (Notes by snap / Last note greyed while thick)

    def on_free_entry(self, left=False):
        """The Straighten box (Enter, stepped, or left: widgets.leave_box)."""
        if self._loading or left and unchanged(self.free_entry):
            return
        try:
            value = calc(self.free_var.get())
            if not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            bad(self.free_entry)
            return
        good(self.free_entry)
        value = int(round(value))
        self.free_smooth = value  # new strokes get it too
        tgts = [(d, owner) for d, owner in self.free_targets() if d.get("smooth", 0) != value]
        if not tgts:
            return self.sync_freehand()
        self.begin_edit(("smooth", tuple(sorted(self.sels)), self.stroke))
        k = self.roll.sy / self.roll.sx if self.roll.sx else 0.25  # worked out as the roll looks now
        for d, owner in tgts:
            d["smooth"] = value
            if (d.get("thick") or 0) > THICK_MIN and "pk" not in d:  # (a wide pen keeps the zoom it's round in:
                d["pk"] = d.get("k", k if owner is None else uv_k(owner["pts"], k))  # pen.pen_k, hunt 2026-11-19)
            d["k"] = k if owner is None else uv_k(owner["pts"], k)
        for owner in {id(o): o for _, o in tgts if o is not None}.values():
            refit(owner)  # the stroke's new outline may reach further
        self.shapes_changed()
        self.sync_freehand()
