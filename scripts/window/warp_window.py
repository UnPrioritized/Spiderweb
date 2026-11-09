"""Recolour's Warp… window (side panel Warp… between Stir… and Liquify…, panel_recolour.py; notes/warp.py; user
2026-11-09, PLAN.md: "like Liquify": a small window of its own, the grid dragged on the piano roll). Follows the
selection like Liquify….
The window holds Grid size (3 x 3 / 4 x 4 / 5 x 5 cells, or Custom…: cells across and up apart, 1..20 each in two
number boxes; user 2026-11-09, long thin shapes), Bend (Smooth / Straight cells) and At the edges (Stir's choices),
plus Reset. Each one is the shapes' own (one undo step, only shapes with a Warp change; several picked and different
= blank); a shape with no Warp yet takes the window's picks (settings.ini [warp]) when its first point is dragged.
Grid size on a warped shape RE-FITS its bend onto the new grid (user 2026-11-09: nothing lost); sizes changed one
after another re-fit from the bend before the first of them (_fit), so 4 x 4 -> 12 x 3 -> 4 x 4 gives the bend back.
Mirror: Sideways / Up / down ticks (user 2026-11-09, PLAN C3) = a drawing aid in settings.ini [warp] mirror_side /
mirror_up, read by roll_warp at every drag move."""

import tkinter as tk
from tkinter import ttk

from files import settings
from files.lang import tr
from files.mathexpr import calc, fmt
from notes import warp as wp_
from notes.recolour import KEEP, STIR_EDGES, clean_recolour
from window import look
from window.widgets import Scrub, ShortLabel, Tooltip, good, remember_place

BENDS = (True, False)  # (Smooth, Straight cells: the Bend dropdown by place)
CELLS = ("cols", "rows")  # (the Custom boxes: cells across, cells up)


def put(name, value):
    try:
        settings.put("warp", name, value)
    except OSError:
        pass


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
        self._fit = {}  # id(shape) -> (the Warp a size change left it, the Warp before the first size change)
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ShortLabel(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w", fill="x")
        form = ttk.Frame(box)
        form.pack(anchor="w", fill="x", pady=(8, 0))
        self.boxes = {}
        grids = [tr("warp.grid_n", cols=c, rows=r) for c, r in wp_.GRIDS] + [tr("warp.custom")]
        rows = (("grid", grids, self.on_grid, 0),
                ("bend", [tr("warp.smooth"), tr("warp.straight")], self.on_bend, 2),
                ("edges", [tr(f"warp.edges_{c}") for c in wp_.EDGES], self.on_edges, 3),
                ("folds", [tr(f"warp.folds_{c}") for c in wp_.FOLDS], self.on_folds, 4),
                ("edge", [tr(f"stir.edge_{c}") for c in STIR_EDGES], self.on_edge, 5))
        for name, values, act, r in rows:
            word = ttk.Label(form, text=tr(f"warp.{name}"))
            word.grid(row=r, column=0, sticky="w", pady=2)
            Tooltip(word, tr(f"warp.{name}_tip"))
            b = ttk.Combobox(form, values=values, state="readonly", width=max(len(v) for v in values) + 1)
            b.grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            b.bind("<<ComboboxSelected>>", lambda e, act=act: act())
            Tooltip(b, tr(f"warp.{name}_tip"))
            self.boxes[name] = b
        # (Mirror: a drawing aid (user 2026-11-09, PLAN C3): a dragged point's twins move mirrored across the grid's
        # middle as shown on the piano roll; settings.ini only, never the shapes')
        word = ttk.Label(form, text=tr("warp.mirror"))
        word.grid(row=6, column=0, sticky="w", pady=2)
        Tooltip(word, tr("warp.mirror_tip"))
        ticks = ttk.Frame(form)
        ticks.grid(row=6, column=1, sticky="w", padx=(6, 0), pady=2)
        self.mirror = {}
        for k, name in enumerate(("side", "up")):
            var = tk.BooleanVar(value=settings.get("warp", f"mirror_{name}", "0") == "1")
            t = ttk.Checkbutton(ticks, text=tr(f"warp.mirror_{name}"), variable=var,
                                command=lambda name=name: self.on_mirror(name))
            t.pack(side="left", padx=(0 if not k else 10, 0))
            Tooltip(t, tr("warp.mirror_tip"))
            self.mirror[name] = var
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
        buttons = ttk.Frame(box)
        buttons.pack(anchor="w", fill="x", pady=(10, 0))
        b = ttk.Button(buttons, text=tr("warp.reset"), command=self.on_reset)
        b.pack(side="left")
        Tooltip(b, tr("warp.reset_tip"))
        self.hint = ttk.Label(box, text=tr("warp.hint"), foreground=look.HINT,
                              wraplength=int(280 * app.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(10, 0))
        self.warn = ttk.Label(box, text="", foreground=look.WARN, wraplength=int(280 * app.scale), justify="left")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.sync()
        remember_place(self, "warp")
        self.deiconify()

    def targets(self):
        return self.app.recolour_targets()

    def warps(self):
        """The selected shapes' Warps (clean, every setting filled in), None for a shape without one."""
        out = []
        for t in self.targets():
            got = (clean_recolour(t.get("recolour")) or {}).get("warp")
            out.append(wp_.full(got) if got else None)
        return out

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
        bend before the first of these size changes (a point dragged since = from the bend now)."""
        def set_to(w, sh):
            made, base = self._fit.get(id(sh), (None, None))
            src = base if made is not None and wp_.clean_warp(w) == made else w
            c, r = wp_.size(w)  # (the other way as it is NOW: across set, then up = both kept)
            got = wp_.refit(src, cols or c, rows or r)
            self._fit[id(sh)] = (got, wp_.clean_warp(src))
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
        """(sideways, up / down) mirror ticks, as on the piano roll."""
        return self.mirror["side"].get(), self.mirror["up"].get()

    def on_mirror(self, name):
        put(f"mirror_{name}", "1" if self.mirror[name].get() else "0")
        if not getattr(self.app.roll, "wp", None):
            self.app.roll.draw_warp_grid()

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
        tgts = self.targets()
        self.what.config(text=tr("warp.nothing") if not tgts else
                         tr("warp.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("warp.n_shapes", n=len(tgts)))
        rcs = [clean_recolour(t.get("recolour")) for t in tgts]
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
        sizes = {(w["cols"], w["rows"]) for w in every}
        odd = any(s not in wp_.GRIDS for s in sizes)
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
        if show != self.cells_shown:
            self.cells_shown = show
            if show:
                self.cells_row.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=2)
            else:
                self.cells_row.grid_remove()
        if not getattr(app.roll, "wp", None):  # (the grid on the piano roll: as the shapes are now)
            app.roll.draw_warp_grid()

    def close(self):
        roll = self.app.roll
        if (roll.drag or ("",))[0] in ("warp", "warp_none"):
            roll.warp_release(None)
        self.app.warp_window = None
        self.destroy()
        roll.focus_set()
        roll.draw_warp_grid()


def open_warp(app):
    w = getattr(app, "warp_window", None)
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        WarpWindow(app)
