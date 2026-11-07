"""Recolour's Liquify… window (side panel Liquify… beside Stir…, panel_recolour.py; notes/liquify.py; user 2026-11-07,
PLAN.md: a small window of its own like Stir's). While it is open, dragging over a selected shape on the piano roll
brushes its colours (roll/roll_liquify.py); the window holds the tool and the brush settings. Follows the selection
like Stir…. The brush settings are the user's own (settings.ini [liquify]), not part of a shape: no undo step."""

import tkinter as tk
from tkinter import ttk

from files import settings
from files.lang import tr
from files.mathexpr import calc, fmt
from notes.recolour import KEEP, clean_recolour
from window import look
from window.widgets import Scrub, ShortLabel, Tooltip, bad, good, remember_place

TOOLS = ("push",)  # (stage C adds the others; notes/liquify.TOOLS knows them all)
# name -> (default, least, most, unit, steps)
BRUSH = {"size": (100.0, 4.0, 1000.0, "px", (5, 50, 1)),
         "density": (50.0, 0.0, 100.0, "%", (1, 10, 0.1)),
         "pressure": (50.0, 1.0, 100.0, "%", (1, 10, 0.1)),
         "smoothing": (0.0, 0.0, 100.0, "%", (1, 10, 0.1))}


def saved(name):
    """A brush setting as saved (settings.ini [liquify]), or its default."""
    d, lo, hi = BRUSH[name][:3]
    try:
        v = float(settings.get("liquify", name, d))
    except (TypeError, ValueError):
        return d
    return min(max(v, lo), hi) if v == v else d


class LiquifyWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.liquify_window = self
        self.title(tr("liquify.title"))
        self.transient(app)
        self.resizable(False, False)
        self.brush = {k: saved(k) for k in BRUSH}
        tool = settings.get("liquify", "tool", "push")
        self.tool = tk.StringVar(value=tool if tool in TOOLS else "push")
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ShortLabel(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w", fill="x")
        form = ttk.Frame(box)
        form.pack(anchor="w", fill="x", pady=(8, 0))
        ttk.Label(form, text=tr("liquify.tool")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        tools = ttk.Frame(form)
        tools.grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, 6))
        for t in TOOLS:
            b = ttk.Radiobutton(tools, text=tr("liquify." + t), value=t, variable=self.tool, command=self.on_tool)
            b.pack(side="left", padx=(0, 8))
            Tooltip(b, tr(f"liquify.{t}_tip"))
        self.boxes = {}
        for row, name in enumerate(BRUSH, start=1):
            d, lo, hi, unit, steps = BRUSH[name]
            word = ttk.Label(form, text=tr(f"liquify.{name}"))
            word.grid(row=row, column=0, sticky="w", pady=2)
            Tooltip(word, tr(f"liquify.{name}_tip"))
            var = tk.StringVar(value=fmt(self.brush[name]))
            e = ttk.Entry(form, textvariable=var, width=7)
            e.grid(row=row, column=1, sticky="w", padx=(6, 4), pady=2)
            ttk.Label(form, text=unit).grid(row=row, column=2, sticky="w")
            apply = lambda name=name: self.on_box(name)  # noqa: E731
            Scrub(app, [(e, var, apply)], steps, lo, hi, label=word, drag_box=True, no_undo=True)
            e.bind("<Return>", lambda ev, name=name: self.on_box(name))
            e.bind("<FocusOut>", lambda ev, name=name: self.on_box(name), add="+")
            self.boxes[name] = (e, var)
        self.hint = ttk.Label(box, text=tr("liquify.hint"), foreground=look.HINT,
                              wraplength=int(300 * app.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(10, 0))
        self.warn = ttk.Label(box, text="", foreground=look.WARN, wraplength=int(300 * app.scale), justify="left")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.sync()
        remember_place(self, "liquify")
        self.deiconify()

    def targets(self):
        return self.app.recolour_targets()

    def size_px(self):
        """The brush's width on screen in real pixels (the window's scale taken in)."""
        return self.brush["size"] * self.app.scale

    def on_tool(self):
        try:
            settings.put("liquify", "tool", self.tool.get())
        except OSError:
            pass

    def on_box(self, name):
        e, var = self.boxes[name]
        d, lo, hi = BRUSH[name][:3]
        try:
            v = calc(var.get().strip().rstrip("%").rstrip("px").strip())
            ok = lo <= v <= hi
        except ValueError:
            ok = False
        if not ok:
            var.set(fmt(self.brush[name]))  # (back to the last good one)
            good(e)
            return
        good(e)
        v = round(v, 3)
        if v == self.brush[name]:
            return
        self.brush[name] = v
        try:
            settings.put("liquify", name, fmt(v))
        except OSError:
            pass
        self.app.roll.draw_liquify_brush()

    def sync(self):
        app = self.app
        tgts = self.targets()
        self.what.config(text=tr("liquify.nothing") if not tgts else
                         tr("liquify.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("liquify.n_shapes", n=len(tgts)))
        lonely, single = app.recolour_trouble(tgts, [clean_recolour(t.get("recolour")) or {"mode": KEEP}
                                                     for t in tgts])
        text = tr("liquify.needs") if lonely else tr("liquify.one") if single else ""
        if text != self.warn.cget("text"):
            self.warn.config(text=text)
            if text:
                self.warn.pack(anchor="w", fill="x", pady=(6, 0), after=self.hint)
            else:
                self.warn.pack_forget()
        app.roll.draw_liquify_brush()

    def close(self):
        roll = self.app.roll
        if (roll.drag or ("",))[0] == "liquify":
            roll.liquify_release(None)
        self.app.liquify_window = None
        self.destroy()
        roll.focus_set()
        roll.draw_liquify_brush()


def open_liquify(app):
    w = getattr(app, "liquify_window", None)
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        LiquifyWindow(app)
