"""Recolour's Warp… window (side panel Warp… between Stir… and Liquify…, panel_recolour.py; notes/warp.py; user
2026-11-09, PLAN.md: "like Liquify": a small window of its own, the grid dragged on the piano roll). Follows the
selection like Liquify….
The window holds Grid size (3 / 4 / 5 cells a side), Bend (Smooth / Straight cells) and At the edges (Stir's
choices), plus Reset. Each one is the shapes' own (one undo step, only shapes with a Warp change; several picked
and different = blank); a shape with no Warp yet takes the window's picks (settings.ini [warp]) when its first point
is dragged. Grid size on a warped shape RE-FITS its bend onto the new grid (user 2026-11-09: nothing lost)."""

import tkinter as tk
from tkinter import ttk

from files import settings
from files.lang import tr
from notes import warp as wp_
from notes.recolour import KEEP, STIR_EDGES, clean_recolour
from window import look
from window.widgets import ShortLabel, Tooltip, remember_place

BENDS = (True, False)  # (Smooth, Straight cells: the Bend dropdown by place)


def put(name, value):
    try:
        settings.put("warp", name, value)
    except OSError:
        pass


class WarpWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.warp_window = self
        self.title(tr("warp.title"))
        self.transient(app)
        self.resizable(False, False)
        try:
            n = int(settings.get("warp", "grid", "4"))
        except (TypeError, ValueError):
            n = 4
        self.grid_n = n if n in wp_.GRIDS else 4  # (a shape's first drag takes these three)
        self.smooth = settings.get("warp", "smooth", "1") != "0"
        edge = settings.get("warp", "edge", "escape")
        self.edge = edge if edge in STIR_EDGES else "escape"
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ShortLabel(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w", fill="x")
        form = ttk.Frame(box)
        form.pack(anchor="w", fill="x", pady=(8, 0))
        self.boxes = {}
        rows = (("grid", [tr("warp.grid_n", n=n) for n in wp_.GRIDS], self.on_grid),
                ("bend", [tr("warp.smooth"), tr("warp.straight")], self.on_bend),
                ("edge", [tr(f"stir.edge_{c}") for c in STIR_EDGES], self.on_edge))
        for r, (name, values, act) in enumerate(rows):
            word = ttk.Label(form, text=tr(f"warp.{name}"))
            word.grid(row=r, column=0, sticky="w", pady=2)
            Tooltip(word, tr(f"warp.{name}_tip"))
            b = ttk.Combobox(form, values=values, state="readonly", width=max(len(v) for v in values) + 1)
            b.grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            b.bind("<<ComboboxSelected>>", lambda e, act=act: act())
            Tooltip(b, tr(f"warp.{name}_tip"))
            self.boxes[name] = b
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
        """Every selected shape's Warp changed by set_to(its warp) -> new warp (None = no Warp); one undo step."""
        def change(old, sh):
            got = (old or {}).get("warp")
            if not got:
                return old
            new = set_to(got)
            out = dict(old)
            if new:
                out["warp"] = new
            else:
                out.pop("warp", None)
            return out
        self.app.change_recolour(change, name)
        self.sync()

    def on_grid(self):
        i = self.boxes["grid"].current()
        if i < 0:
            return
        n = wp_.GRIDS[i]
        self.grid_n = n
        put("grid", n)
        self._change(lambda w: wp_.refit(w, n), tr("warp.undo_grid"))

    def on_bend(self):
        i = self.boxes["bend"].current()
        if i < 0:
            return
        smooth = BENDS[i]
        self.smooth = smooth
        put("smooth", "1" if smooth else "0")
        self._change(lambda w: dict(w, smooth=smooth), tr("warp.undo_bend"))

    def on_edge(self):
        i = self.boxes["edge"].current()
        if i < 0:
            return
        edge = STIR_EDGES[i]
        self.edge = edge
        put("edge", edge)
        self._change(lambda w: dict(w, edge=edge), tr("warp.undo_edge"))

    def on_reset(self):
        """Every point of the selected shapes back where it started (the Warp gone). The window keeps their grid
        size / bend / edge when they all had the same, so the next drag starts as they were."""
        ws = [w for w in self.warps() if w]
        if not ws:
            return
        for key, attr, saved in (("n", "grid_n", lambda v: v), ("smooth", "smooth", lambda v: "1" if v else "0"),
                                 ("edge", "edge", lambda v: v)):
            vals = {w[key] for w in ws}
            if len(vals) == 1:
                v = vals.pop()
                setattr(self, attr, v)
                put("grid" if key == "n" else key, saved(v))
        self._change(lambda w: None, tr("warp.undo_reset"))

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
        mine = {"n": self.grid_n, "smooth": self.smooth, "edge": self.edge}
        for name, key, values in (("grid", "n", wp_.GRIDS), ("bend", "smooth", BENDS), ("edge", "edge", STIR_EDGES)):
            got = {(w or mine)[key] for w in ws} or {mine[key]}
            b = self.boxes[name]
            v = got.pop() if len(got) == 1 else None
            if v in values:
                b.current(values.index(v))
            else:
                b.set("")  # (mixed, or a file's grid the window doesn't offer)

    def close(self):
        self.app.warp_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_warp(app):
    w = getattr(app, "warp_window", None)
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        WarpWindow(app)
