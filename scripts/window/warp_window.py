"""Recolour's Warp… window (side panel Warp… between Stir… and Liquify…, panel_recolour.py; notes/warp.py; user
2026-11-09, PLAN.md: "like Liquify": a small window of its own, the grid dragged on the piano roll). Follows the
selection like Liquify….
The window holds Grid size (3 x 3 / 4 x 4 / 5 x 5 cells, or Custom…: cells across and up apart, 1..20 each in two
number boxes; user 2026-11-09, long thin shapes), Preset + its Bend % + Across / Up (notes/warp_presets.py, user
2026-11-10), Cells (Smooth / Straight cells; was "Bend:") and At the edges (Stir's choices),
plus Reset. Each one is the shapes' own (one undo step, only shapes with a Warp change; several picked and different
= blank); a shape with no Warp yet takes the window's picks (settings.ini [warp]) when its first point is dragged.
Grid size on a warped shape RE-FITS its bend onto the new grid (user 2026-11-09: nothing lost); sizes changed one
after another re-fit from the bend before the first of them (_fit), so 4 x 4 -> 12 x 3 -> 4 x 4 gives the bend back.
Mirror: ONE dropdown Off / Sideways / Up / down / 4-way / Half turn (user 2026-11-09, PLAN C3 + C5; like the drawer's)
= a drawing aid in settings.ini [warp] mirror, read by roll_warp at every drag move."""

import json
import tkinter as tk
from tkinter import ttk

from files import settings
from files.lang import tr
from files.mathexpr import calc, fmt
from notes import warp as wp_
from notes import warp_presets as pr
from notes.recolour import KEEP, STIR_EDGES, clean_recolour, new_seed
from window import look
from window.edit_line import EditLine
from window.widgets import Scrub, ShortLabel, Tooltip, bad, good, leave_box, open_or_lift, remember_place, \
    same_or_blank, unchanged

BENDS = (True, False)  # (Smooth, Straight cells: the Bend dropdown by place)
CELLS = ("cols", "rows")  # (the Custom boxes: cells across, cells up)
MIRRORS = ("off", "side", "up", "both", "half")  # (the Mirror dropdown by place; both = 4-way, half = Half turn)
SPLITS = ("across", "down", "both")  # (the Split buttons: a line side to side / top to bottom / both, as seen)


def put(name, value):
    settings.put_quiet("warp", name, value)


def fit_key(w):
    """A Warp as a dict key (_fit): every setting and point."""
    return json.dumps(wp_.clean_warp(w), sort_keys=True)


def _number(text, default):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def saved_cells(name):
    try:
        v = int(settings.get("warp", name, "4"))
    except (TypeError, ValueError):
        return 4
    return v if wp_.cells_ok(v) else 4


class WarpWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.warp_window = self
        self.title(tr("warp.title"))
        self.transient(app)
        self.resizable(False, False)
        self.cols, self.rows = saved_cells("cols"), saved_cells("rows")  # (a shape's first drag takes these)
        self.custom = settings.get("warp", "custom", "0") == "1"  # (Custom… picked: the boxes stay shown)
        self.smooth = settings.get("warp", "smooth", "1") != "0"
        edge = settings.get("warp", "edge", "escape")
        self.edge = edge if edge in STIR_EDGES else "escape"
        edges = settings.get("warp", "edges", wp_.DEFAULTS["edges"])
        self.edges = edges if edges in wp_.EDGES else wp_.DEFAULTS["edges"]
        folds = settings.get("warp", "folds", wp_.DEFAULTS["folds"])
        self.folds = folds if folds in wp_.FOLDS else wp_.DEFAULTS["folds"]
        self._fit = {}  # fit_key(the Warp a size change left) -> the Warp before the first size change
        # (Preset: the last one picked + its Bend % / way, for shapes without one yet; settings.ini [warp])
        p = pr.ok([settings.get("warp", "preset", "arc"), _number(settings.get("warp", "preset_bend", "50"), 50.0),
                   settings.get("warp", "preset_way", "across")]) or ["arc", 50.0, "across"]
        self.preset, self.preset_bend, self.preset_way = p
        if not self.preset_bend:  # (0 % is never the next pick's bend: picks did nothing; hunt 2026-11-10)
            self.preset_bend = 50.0
        self._warps = {}  # id(a shape's recolour) -> (that recolour, its Warp in full): each cleaned once (hunt 11-10)
        self._keys = {}  # id(a Warp from warps()) -> (it, fit_key(it))
        self._seen = {}  # fit_key(a Warp) -> pr.matches (what the Preset row shows for it)
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ShortLabel(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w", fill="x")
        form = ttk.Frame(box)
        form.pack(anchor="w", fill="x", pady=(8, 0))
        self.boxes = {}
        grids = [tr("warp.grid_n", cols=c, rows=r) for c, r in wp_.GRIDS] + [tr("warp.custom")]
        # (rows: Grid size, its Custom boxes, Preset, its Bend % + way, then Cells (the old "Bend:" row, renamed so
        # the preset's Bend % has the editors' name; user 2026-11-10 "cells probably works"), ...)
        rows = (("grid", grids, self.on_grid, 0),
                ("preset", [tr(f"warp.preset_{k}") for k in pr.PRESETS], self.on_preset, 2),
                ("bend", [tr("warp.smooth"), tr("warp.straight")], self.on_bend, 4),
                ("edges", [tr(f"warp.edges_{c}") for c in wp_.EDGES], self.on_edges, 5),
                ("folds", [tr(f"warp.folds_{c}") for c in wp_.FOLDS], self.on_folds, 6),
                ("edge", [tr(f"stir.edge_{c}") for c in STIR_EDGES], self.on_edge, 7))
        for name, values, act, r in rows:
            word = ttk.Label(form, text=tr(f"warp.{name}"))
            word.grid(row=r, column=0, sticky="w", pady=2)
            Tooltip(word, tr(f"warp.{name}_tip"))
            wide = values + ([tr("warp.preset_none"), tr("warp.preset_custom")] if name == "preset" else [])
            b = ttk.Combobox(form, values=values, state="readonly", width=max(len(v) for v in wide) + 1)
            b.grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            b.bind("<<ComboboxSelected>>", lambda e, act=act: act())
            Tooltip(b, tr(f"warp.{name}_tip"))
            self.boxes[name] = b
        # (the preset's Bend % (-100 .. 100, a number box like Strength) + Across / Up (greyed for the round ones);
        # each change lays the preset again: one undo step, a held label drag one step too)
        word = ttk.Label(form, text=tr("warp.amount"))
        word.grid(row=3, column=0, sticky="w", pady=2)
        Tooltip(word, tr("warp.amount_tip"))
        arow = ttk.Frame(form)
        arow.grid(row=3, column=1, sticky="w", padx=(6, 0), pady=2)
        self.amount_var = tk.StringVar()
        e = self.amount_entry = ttk.Entry(arow, textvariable=self.amount_var, width=6)
        e.pack(side="left")
        Tooltip(e, tr("warp.amount_tip"))
        Scrub(app, [(e, self.amount_var, self.on_amount)], (1, 10, 0.1), -pr.MOST_BEND, pr.MOST_BEND, label=word,
              drag_box=True)
        ttk.Label(arow, text="%").pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: self.on_amount())
        leave_box(app, e, self.amount_var, lambda left: self.on_amount(left and e))
        self.way_var = tk.StringVar(value=self.preset_way)
        self.way_buttons = []
        for k, way in enumerate(pr.WAYS):
            b = ttk.Radiobutton(arow, text=tr(f"warp.way_{way}"), value=way, variable=self.way_var,
                                command=self.on_way)
            b.pack(side="left", padx=(12 if not k else 6, 0))
            Tooltip(b, tr("warp.way_tip"))
            self.way_buttons.append(b)
        # (Mirror: a drawing aid (user 2026-11-09, PLAN C3): a dragged point's twins move mirrored across the grid's
        # middle as shown on the piano roll; settings.ini only, never the shapes')
        word = ttk.Label(form, text=tr("warp.mirror"))
        word.grid(row=8, column=0, sticky="w", pady=2)
        Tooltip(word, tr("warp.mirror_tip"))
        values = [tr(f"warp.mirror_{m}") for m in MIRRORS]
        b = ttk.Combobox(form, values=values, state="readonly", width=max(len(v) for v in values) + 1)
        b.grid(row=8, column=1, sticky="w", padx=(6, 0), pady=2)
        b.bind("<<ComboboxSelected>>", lambda e: self.on_mirror())
        Tooltip(b, tr("warp.mirror_tip"))
        self.boxes["mirror"] = b
        mode = settings.get("warp", "mirror", "off")
        self.mirror = mode if mode in MIRRORS else "off"
        b.current(MIRRORS.index(self.mirror))
        # (Custom…: cells across and up, number boxes like the Liquify window's; shown under Grid size)
        self.cells_row = ttk.Frame(form)
        self.cells = {}
        for k, name in enumerate(CELLS):
            word = ttk.Label(self.cells_row, text=tr(f"warp.{name}"))
            word.pack(side="left", padx=(0 if not k else 10, 4))
            Tooltip(word, tr("warp.cells_tip"))
            var = tk.StringVar()
            e = ttk.Entry(self.cells_row, textvariable=var, width=4)
            e.pack(side="left")
            Tooltip(e, tr("warp.cells_tip"))
            apply = lambda name=name: self.on_cells(name)  # noqa: E731
            Scrub(app, [(e, var, apply)], (1, 5, 1), 1, wp_.MOST_CELLS, label=word, drag_box=True)
            e.bind("<Return>", lambda ev, name=name: self.on_cells(name))
            e.bind("<FocusOut>", lambda ev, name=name: self.on_cells(name), add="+")
            self.cells[name] = (e, var)
        self.cells_shown = False
        self.cells_said = {}  # (what sync last put in each box: a box still showing it isn't being typed in)
        # (Strength % + the show tick: the shapes' own (user 2026-11-09: inside this window, not the side panel);
        # one undo step each, several picked and different = blank box / half tick, typing sets all)
        word = ttk.Label(form, text=tr("warp.strength"))
        word.grid(row=9, column=0, sticky="w", pady=2)
        Tooltip(word, tr("warp.strength_tip"))
        srow = ttk.Frame(form)
        srow.grid(row=9, column=1, sticky="w", padx=(6, 0), pady=2)
        self.strength_var = tk.StringVar()
        e = self.strength_entry = ttk.Entry(srow, textvariable=self.strength_var, width=6)
        e.pack(side="left")
        Tooltip(e, tr("warp.strength_tip"))
        Scrub(app, [(e, self.strength_var, lambda: self.on_shown("strength"))], (1, 10, 0.1), 0, wp_.MOST_STRENGTH,
              label=word, drag_box=True)
        ttk.Label(srow, text="%").pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: self.on_shown("strength"))
        leave_box(app, e, self.strength_var, lambda left: self.on_shown("strength", left and e))
        self.shown = tk.BooleanVar(value=True)
        t = self.shown_tick = ttk.Checkbutton(srow, text=tr("warp.shown"), variable=self.shown,
                                              command=lambda: self.on_shown("on"))
        t.pack(side="left", padx=(12, 0))
        Tooltip(t, tr("warp.shown_tip"))
        # (Split, PLAN.md "Warp Split" B: a button picked = the next click on a grid on the piano roll adds the
        # line(s) there; it lets go after one click, a second click on it or Esc lets go too)
        word = ttk.Label(form, text=tr("warp.split"))
        word.grid(row=10, column=0, sticky="w", pady=2)
        Tooltip(word, tr("warp.split_tip"))
        prow = ttk.Frame(form)
        prow.grid(row=10, column=1, sticky="w", padx=(6, 0), pady=2)
        self.split_mode = ""
        self.split_var = tk.StringVar(value="")
        self.split_buttons = {}
        for k, mode in enumerate(SPLITS):
            b = ttk.Radiobutton(prow, text=tr(f"warp.split_{mode}"), value=mode, variable=self.split_var,
                                style="Toolbutton", takefocus=False, command=lambda mode=mode: self.on_split(mode))
            b.pack(side="left", padx=(0 if not k else 4, 0))
            Tooltip(b, tr(f"warp.split_{mode}_tip"))
            self.split_buttons[mode] = b
        buttons = ttk.Frame(box)
        buttons.pack(anchor="w", fill="x", pady=(10, 0))
        b = ttk.Button(buttons, text=tr("warp.reset"), command=self.on_reset)
        b.pack(side="left")
        Tooltip(b, tr("warp.reset_tip"))
        self.hint = ttk.Label(box, text=tr("warp.hint"), foreground=look.HINT,
                              wraplength=int(280 * app.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(10, 0))
        self.warn = ttk.Label(box, text="", foreground=look.WARN, wraplength=int(280 * app.scale), justify="left")
        self.edit_line = EditLine(box, app, "warp", self.hint, 200)  # (later colour steps hidden: PLAN.md stage C)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.set_split("") if self.split_mode else self.close())
        self.sync()
        remember_place(self, "warp")
        self.deiconify()

    def targets(self):
        return self.app.recolour_targets()

    def warps(self):
        """The selected shapes' Warps (clean, every setting filled in), None for a shape without one."""
        return [self.cleaned(t)[1] for t in self.targets()]

    def cleaned(self, t):
        """(clean_recolour of shape t, its Warp in full or None), each recolour cleaned once (a refresh cleaned every
        shape's 3 times: 2 s at 60 shapes with 20 x 20 presets; hunt 2026-11-10). A shape's recolour is replaced on
        every change, never edited in place."""
        rc = t.get("recolour")
        memo = self._warps
        hit = memo.get(id(rc))
        if hit is None or hit[0] is not rc:
            c = clean_recolour(rc)
            got = (c or {}).get("warp")
            hit = memo[id(rc)] = (rc, c, wp_.full(got) if got else None)
            if len(memo) > 1000:
                memo.pop(next(iter(memo)))
        return hit[1:]

    def key_of(self, w):
        """fit_key(w), worked out once for each Warp warps() gave (a refresh asked it 3 times per shape: 2 s at 60
        shapes with 20 x 20 presets; hunt 2026-11-10)."""
        hit = self._keys.get(id(w))
        if hit is None or hit[0] is not w:
            hit = self._keys[id(w)] = (w, fit_key(w))
            if len(self._keys) > 1000:
                self._keys.pop(next(iter(self._keys)))
        return hit[1]

    def _change(self, set_to, name):
        """Every selected shape's Warp changed by set_to(its warp, the shape) -> new warp (None = no Warp); one undo
        step."""
        def change(old, sh):
            got = (old or {}).get("warp")
            if not got:
                return old
            new = set_to(got, sh)
            out = dict(old)
            if new:
                out["warp"] = new
            else:
                out.pop("warp", None)
            return out
        self.app.change_recolour(change, name)
        self.sync()

    def _resize(self, cols=None, rows=None):
        """Every selected warped shape's grid made cols x rows cells (None = that way as it is), re-fitted from its
        bend before the first of these size changes (a point dragged since = from the bend now). The picked point
        goes (its handles show again only after a click)."""
        self.app.roll.warp_drop_pick()

        def set_to(w, sh):
            c, r = wp_.size(w)
            p = self.preset_of(w)
            if p:  # (still a preset: laid again on the new grid, exactly, instead of re-fitted)
                flat = {k: v for k, v in w.items() if k not in ("xs", "ys")}
                return pr.make(dict(flat, cols=cols or c, rows=rows or r), *p)
            # (looked up by the Warp itself, not the shape: undo / redo makes new shape objects; hunt 2026-11-09)
            base = self._fit.get(fit_key(w))
            src = base if base is not None else w
            # (the other way as it is NOW: across set, then up = both kept)
            got = wp_.refit(src, cols or c, rows or r)
            if len(self._fit) > 200:
                self._fit.pop(next(iter(self._fit)))
            self._fit[fit_key(got)] = wp_.clean_warp(src)
            return got
        self._change(set_to, tr("warp.undo_grid"))

    def on_grid(self):
        i = self.boxes["grid"].current()
        if i < 0:
            return
        if i == len(wp_.GRIDS):  # (Custom…: the boxes show, nothing changes till a number is set)
            self.custom = True
            put("custom", "1")
            self.sync()
            self.cells["cols"][0].focus_set()
            return
        c, r = wp_.GRIDS[i]
        self.cols, self.rows, self.custom = c, r, False
        put("cols", c)
        put("rows", r)
        put("custom", "0")
        self._resize(c, r)

    def on_cells(self, name):
        e, var = self.cells[name]
        text = var.get().strip()
        if not text:  # (a blank box for shapes with different numbers: left as they are)
            return
        try:
            v = calc(text)
            ok = wp_.cells_ok(v)
        except (ValueError, ZeroDivisionError):
            ok = False
        if not ok:  # (back to the last good one)
            good(e)
            sizes = {(w or {"cols": self.cols, "rows": self.rows})[name] for w in self.warps()} or {getattr(self, name)}
            var.set(fmt(sizes.pop()) if len(sizes) == 1 else "")
            self.cells_said[name] = var.get()
            return
        good(e)
        v = int(v)
        var.set(fmt(v))
        self.cells_said[name] = var.get()
        setattr(self, name, v)
        put(name, v)
        self._resize(**{name: v})

    def on_bend(self):
        i = self.boxes["bend"].current()
        if i < 0:
            return
        smooth = BENDS[i]
        self.smooth = smooth
        put("smooth", "1" if smooth else "0")
        self._change(lambda w, sh: dict(w, smooth=smooth), tr("warp.undo_bend"))

    # ---- presets (PLAN.md "Warp several points + presets" B, user 2026-11-10: a starting point, the editors' set)

    def preset_of(self, w):
        """[name, bend, way] of the preset a shape's Warp still is, or None (none / edited since = Custom)."""
        if not w:
            return None
        key = self.key_of(w)
        if key not in self._seen:
            if len(self._seen) > 200:
                self._seen.pop(next(iter(self._seen)))
            self._seen[key] = pr.matches(wp_.clean_warp(w))
        return self._seen[key]

    def _lay(self, name, bend, way, undo):
        """The preset laid on every selected shape's grid as it is (a shape without a Warp: a grid with the window's
        picks), its hand edits replaced; one undo step. bend / way None = each shape's own (several picked with
        different ones: only the other number changes)."""
        self.preset = name
        put("preset", name)
        if bend:  # (0 % is never remembered as the next pick's bend: picking then did nothing; hunt 2026-11-10)
            self.preset_bend = bend
            put("preset_bend", fmt(bend))
        if way is not None:
            self.preset_way = way
            put("preset_way", way)
        self.app.roll.warp_drop_pick()

        def change(old, sh):
            got = (old or {}).get("warp")
            base = wp_.full(got) if got else dict(wp_.empty(self.cols, self.rows), smooth=self.smooth,
                                                     edge=self.edge, edges=self.edges, folds=self.folds)
            own = self.preset_of(got) or [name, self.preset_bend, self.preset_way]
            new = pr.make(base, name, own[1] if bend is None else bend, own[2] if way is None else way)
            out = dict(old) if old else {"mode": KEEP, "seed": new_seed()}
            if new:
                new.pop("on", None)  # (a preset laid = the bend shown again, like a drag; hunt 2026-11-10)
                out["warp"] = new
            else:
                out.pop("warp", None)
            return out
        self.app.change_recolour(change, undo)
        self.sync()

    def on_preset(self):
        i = self.boxes["preset"].current()
        if i < 0:
            return
        name = pr.PRESETS[i]
        way = "across" if name in pr.NO_WAY else self.way_var.get() or self.preset_way
        # (a box at 0 % (the shown preset flattened) never lays the new one flat: the last bend picked instead)
        self._lay(name, self.shown_bend() or self.preset_bend, way, tr("warp.undo_preset"))

    def shown_bend(self):
        """The Bend % box's number, else the last one picked (blank / not a number)."""
        try:
            v = calc(self.amount_var.get().strip().rstrip("%"))
            if -pr.MOST_BEND <= v <= pr.MOST_BEND:
                return round(v, 3)
        except (ValueError, ZeroDivisionError):
            pass
        return self.preset_bend

    def shown_preset(self):
        """The one preset every selected shape still is (None: mixed, none or Custom)."""
        got = {tuple(p[:1]) if p else None for p in (self.preset_of(w) for w in self.warps())}
        return next(iter(got))[0] if len(got) == 1 and None not in got else None

    def on_amount(self, left=None):
        """Bend % typed / stepped: the shown preset laid again at it (each shape keeps its own way)."""
        if self.app._loading or (left is not None and unchanged(left)):
            return
        e, var = self.amount_entry, self.amount_var
        if not var.get().strip():
            return
        try:
            v = calc(var.get().strip().rstrip("%"))
            ok = -pr.MOST_BEND <= v <= pr.MOST_BEND
        except (ValueError, ZeroDivisionError):
            ok = False
        if not ok:
            return bad(e)
        good(e)
        name = self.shown_preset()
        if name is None:
            return
        self._lay(name, round(v, 3), None, tr("warp.undo_preset_bend"))

    def on_way(self):
        name = self.shown_preset()
        if name is None or name in pr.NO_WAY:
            return
        self._lay(name, None, self.way_var.get(), tr("warp.undo_preset_way"))

    def on_edges(self):
        i = self.boxes["edges"].current()
        if i < 0:
            return
        edges = wp_.EDGES[i]
        self.edges = edges
        put("edges", edges)
        self._change(lambda w, sh: dict(w, edges=edges), tr("warp.undo_edges"))

    def on_folds(self):
        i = self.boxes["folds"].current()
        if i < 0:
            return
        folds = wp_.FOLDS[i]
        self.folds = folds
        put("folds", folds)
        self._change(lambda w, sh: dict(w, folds=folds), tr("warp.undo_folds"))

    def on_edge(self):
        i = self.boxes["edge"].current()
        if i < 0:
            return
        edge = STIR_EDGES[i]
        self.edge = edge
        put("edge", edge)
        self._change(lambda w, sh: dict(w, edge=edge), tr("warp.undo_edge"))

    def mirrored(self):
        """The Mirror pick (MIRRORS), as on the piano roll."""
        return self.mirror

    def on_mirror(self):
        i = self.boxes["mirror"].current()
        if i < 0:
            return
        self.mirror = MIRRORS[i]
        put("mirror", self.mirror)
        if not getattr(self.app.roll, "wp", None):
            self.app.roll.draw_warp_grid()

    def on_split(self, mode):
        """A Split button clicked: picked (the next click on a grid adds the line), or let go when it was picked."""
        self.set_split("" if self.split_mode == mode else mode)

    def set_split(self, mode):
        """The Split button picked ("" = none): the hint says what a click does, the piano roll's pointer follows."""
        self.split_mode = mode
        self.split_var.set(mode)
        self.hint.config(text=tr("warp.split_hint" if mode else "warp.hint"))
        roll = self.app.roll
        roll.configure(cursor="crosshair" if mode else "")
        if mode:
            roll.focus_set()  # (Esc on the piano roll lets go)

    def on_shown(self, what, left=None):
        """The show tick (what = "on": the bend shown / hidden, the grid kept either way) or Strength ("strength").
        Only shapes with a Warp change; several picked keep their own strengths until one is typed."""
        if self.app._loading or (left is not None and unchanged(left)):
            return
        if what == "on":
            self.shown_tick.state(["!alternate"])
            set_to = {"on": self.shown.get()}
        else:
            e, var = self.strength_entry, self.strength_var
            if not var.get().strip() and getattr(e, "blank_from", None):
                return
            try:
                s = calc(var.get().strip().rstrip("%"))
                ok = 0 <= s <= wp_.MOST_STRENGTH
            except (ValueError, ZeroDivisionError):
                ok = False
            if not ok:
                return bad(e)
            good(e)
            set_to = {"strength": round(s, 3)}
        self._change(lambda w, sh: wp_.clean_warp(dict(w, **set_to)),
                     tr("warp.undo_show" if what == "on" else "warp.undo_strength"))

    def on_reset(self):
        """Every point of the selected shapes back where it started (the Warp gone). The window keeps their grid
        size / bend / edge when they all had the same, so the next drag starts as they were."""
        ws = [w for w in self.warps() if w]
        if not ws:
            return
        for key in ("cols", "rows", "smooth", "edge", "edges", "folds"):
            vals = {w[key] for w in ws}
            if len(vals) == 1:
                v = vals.pop()
                setattr(self, key, v)
                put(key, ("1" if v else "0") if key == "smooth" else v)
        self._change(lambda w, sh: None, tr("warp.undo_reset"))

    def sync(self):
        app = self.app
        app.roll.warp_check_pick()  # (the selection changed since a point was picked: it goes)
        tgts = self.targets()
        self.what.config(text=tr("warp.nothing") if not tgts else
                         tr("warp.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("warp.n_shapes", n=len(tgts)))
        rcs = [self.cleaned(t)[0] for t in tgts]
        lonely, single = app.recolour_trouble(tgts, [r or {"mode": KEEP} for r in rcs])
        ws = self.warps()
        zero = ws and all(w and w["strength"] == 0 for w in ws)
        text = tr("warp.needs") if lonely else tr("warp.one") if single else tr("warp.zero") if zero else ""
        if text != self.warn.cget("text"):
            self.warn.config(text=text)
            if text:
                self.warn.pack(anchor="w", fill="x", pady=(6, 0), after=self.hint)
            else:
                self.warn.pack_forget()
        # (each box: the shapes' own, a shape without a Warp yet = the window's pick for its first drag)
        mine = {"cols": self.cols, "rows": self.rows, "smooth": self.smooth, "edge": self.edge, "edges": self.edges,
                "folds": self.folds}
        every = [w or mine for w in ws] or [mine]
        for name, key, values in (("bend", "smooth", BENDS), ("edges", "edges", wp_.EDGES),
                                  ("folds", "folds", wp_.FOLDS), ("edge", "edge", STIR_EDGES)):
            got = {w[key] for w in every}
            b = self.boxes[name]
            v = got.pop() if len(got) == 1 else None
            if v in values:
                b.current(values.index(v))
            else:
                b.set("")  # (mixed)
        self.sync_preset(ws)
        sizes = {(w["cols"], w["rows"]) for w in every}
        # (a split grid's lines aren't even: Custom with its counts; a ready size evens them out)
        odd = any(s not in wp_.GRIDS for s in sizes) or any(w.get("xs") or w.get("ys") for w in every)
        show = self.custom or odd
        b = self.boxes["grid"]
        if show:
            b.current(len(wp_.GRIDS))
        elif len(sizes) == 1:
            b.current(wp_.GRIDS.index(next(iter(sizes))))
        else:
            b.set("")  # (mixed ready sizes)
        try:
            typing = self.focus_get()
        except (KeyError, tk.TclError):  # (a pop-up's widget Tk can't name)
            typing = None
        for k, name in enumerate(CELLS):
            vals = {s[k] for s in sizes}
            e, var = self.cells[name]
            text = fmt(vals.pop()) if len(vals) == 1 else ""
            if typing is not e or var.get() == self.cells_said.get(name):  # (never under the typing)
                var.set(text)
                self.cells_said[name] = text
        # (Strength + tick: greyed while no selected shape has a Warp yet (a first drag starts at 100 %, shown))
        have = [w for w in ws if w]
        e = self.strength_entry
        app._loading = True
        try:
            ons = {w["on"] for w in have} or {True}
            self.shown.set(ons == {True})
            self.shown_tick.state(["alternate" if len(ons) > 1 else "!alternate"])
            if typing is not e or unchanged(e):  # (never under the typing)
                same_or_blank(e, self.strength_var, [fmt(w["strength"]) for w in have] or ["100"])
        finally:
            app._loading = False
        for w in (e, self.shown_tick):
            w.state(["!disabled"] if have else ["disabled"])
        if show != self.cells_shown:
            self.cells_shown = show
            if show:
                self.cells_row.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=2)
            else:
                self.cells_row.grid_remove()
        if not getattr(app.roll, "wp", None):  # (the grid on the piano roll: as the shapes are now)
            app.roll.draw_warp_grid()

    def sync_preset(self, ws):
        """The Preset row: the one preset the selected shapes still are, "None" (no Warp), "Custom" (edited since a
        preset / dragged by hand) or blank (mixed); Bend % + Across / Up only while one preset shows."""
        ps = [self.preset_of(w) for w in ws]
        kinds = {p[0] if p else ("custom" if w else "none") for p, w in zip(ps, ws)}
        b = self.boxes["preset"]
        one = next(iter(kinds)) if len(kinds) == 1 else None
        if one in pr.PRESETS:
            b.current(pr.PRESETS.index(one))
        else:
            b.set(tr("warp.preset_none") if one == "none" else tr("warp.preset_custom") if one == "custom" else "")
        live = one in pr.PRESETS
        e = self.amount_entry
        try:
            typing = self.focus_get()
        except (KeyError, tk.TclError):
            typing = None
        self.app._loading = True
        try:
            if typing is not e or unchanged(e):  # (never under the typing)
                same_or_blank(e, self.amount_var, [fmt(p[1]) for p in ps if p] if live else [fmt(self.preset_bend)])
            ways = {p[2] for p in ps if p} if live else {self.preset_way}
            self.way_var.set(next(iter(ways)) if len(ways) == 1 else "")
        finally:
            self.app._loading = False
        e.state(["!disabled"] if live else ["disabled"])
        for w in self.way_buttons:
            w.state(["!disabled"] if live and one not in pr.NO_WAY else ["disabled"])

    def close(self):
        roll = self.app.roll
        if (roll.drag or ("",))[0] in ("warp", "warp_none"):
            roll.warp_release(None)
        # (numbers typed without Enter count, like the Stir window: X / Esc never lose them; hunt 2026-11-09)
        for name in CELLS:
            if self.cells[name][1].get() != self.cells_said.get(name):
                self.on_cells(name)
        if not unchanged(self.strength_entry):
            self.on_shown("strength", self.strength_entry)
        if not unchanged(self.amount_entry):  # (Bend % too, like Strength; hunt 2026-11-10)
            self.on_amount(self.amount_entry)
        if self.split_mode:
            self.set_split("")
        roll.warp_drop_pick()  # (opened again: no point picked; Warp Split hunt 2026-11-10)
        self.app.warp_window = None
        self.app.colour_edit_leave("warp")  # (its later colour steps worked out again, PLAN.md stage C)
        self.app.colour_window_closed()  # (quick drags' kept work let go: memory use, user 2026-11-10)
        self.destroy()
        roll.focus_set()
        roll.draw_warp_grid()


def open_warp(app):
    if open_or_lift(app, "warp_window", lambda: WarpWindow(app)):
        app.tips.show("warp", wait=True)  # (the first time: what it does, Help → Warp)
