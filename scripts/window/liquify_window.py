"""Recolour's Liquify… window (side panel Liquify… beside Stir…, panel_recolour.py; notes/liquify.py; user 2026-11-07,
PLAN.md: a small window of its own like Stir's). While it is open, dragging over a selected shape on the piano roll
brushes its colours (roll/roll_liquify.py); the window holds the tool and the brush settings. Follows the selection
like Stir…. The brush settings are the user's own (settings.ini [liquify]), not part of a shape: no undo step.
The tools are the image editors' Liquify ones: Push, Restore (their Reconstruct), Smooth, Twirl (Left / Right here +
Alt turns back, user's pick), Pinch (their Pucker; Alt held = Bulge, their Bloat), Freeze (their mask; Alt held = Thaw), and
the buttons Restore All / Freeze all / Thaw all / Invert (their Restore All, Mask All, None, Invert All). At the edges
(Stir's choices) is the shapes' own (an undo step); for a shape's first stroke the window's last pick."""

import tkinter as tk
from tkinter import ttk

from files import settings
from files.lang import tr
from files.mathexpr import calc, fmt
from notes import liquify as lq
from notes.recolour import KEEP, STIR_EDGES, clean_recolour
from window import look
from window.widgets import Scrub, ShortLabel, Tooltip, good, remember_place

# (Pinch + Bulge and Freeze + Thaw = ONE tool each here, Alt held = the other: user 11-07 "too much options"; the
# dab maths keep all of them)
TOOL_ROWS = (("push", "restore", "smooth"), ("twirl", "pinch", "freeze"))
ONE_OF = {"bulge": "pinch", "thaw": "freeze"}  # (saved before they became one tool)
TOOLS = tuple(t for row in TOOL_ROWS for t in row)
# name -> (default, least, most, unit, steps)
BRUSH = {"size": (100.0, 4.0, 1000.0, "px", (5, 50, 1)),
         "density": (50.0, 0.0, 100.0, "%", (1, 10, 0.1)),
         "pressure": (50.0, 1.0, 100.0, "%", (1, 10, 0.1)),
         "rate": (80.0, 0.0, 100.0, "%", (1, 10, 0.1)),
         "smoothing": (0.0, 0.0, 100.0, "%", (1, 10, 0.1))}
HOLD_TOOLS = ("restore", "smooth", "twirl", "pinch", "freeze")  # (Rate: working while held still)


def saved(name):
    """A brush setting as saved (settings.ini [liquify]), or its default."""
    d, lo, hi = BRUSH[name][:3]
    try:
        v = float(settings.get("liquify", name, d))
    except (TypeError, ValueError):
        return d
    return min(max(v, lo), hi) if v == v else d


def put(name, value):
    try:
        settings.put("liquify", name, value)
    except OSError:
        pass


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
        tool = ONE_OF.get(tool, tool)
        self.tool = tk.StringVar(value=tool if tool in TOOLS else "push")
        way = settings.get("liquify", "twirl", "right")
        self.twirl = tk.StringVar(value=way if way in ("left", "right") else "right")
        edge = settings.get("liquify", "edge", "escape")
        self.edge = edge if edge in STIR_EDGES else "escape"  # (a shape's first stroke takes it)
        self.show_mask = tk.BooleanVar(value=settings.get("liquify", "show_mask", "1") != "0")
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ShortLabel(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w", fill="x")
        form = ttk.Frame(box)
        form.pack(anchor="w", fill="x", pady=(8, 0))
        form.columnconfigure(3, weight=1)  # (the wide rows' room goes there: the units stay by their boxes)
        ttk.Label(form, text=tr("liquify.tool")).grid(row=0, column=0, sticky="nw", pady=(0, 6))
        tools = ttk.Frame(form)
        tools.grid(row=0, column=1, columnspan=3, sticky="w", pady=(0, 6))
        for r, names in enumerate(TOOL_ROWS):
            for c, t in enumerate(names):
                b = ttk.Radiobutton(tools, text=tr("liquify." + t), value=t, variable=self.tool, command=self.on_tool)
                b.grid(row=r, column=c, sticky="w", padx=(0, 8))
                Tooltip(b, tr(f"liquify.{t}_tip"))
        # (Twirl's way round: easy to find here; Alt held turns it back, like the editors; user 11-07)
        self.twirl_word = ttk.Label(form, text=tr("liquify.twirl_way"))
        self.twirl_word.grid(row=1, column=0, sticky="w", pady=(0, 6))
        Tooltip(self.twirl_word, tr("liquify.twirl_way_tip"))
        ways = ttk.Frame(form)
        ways.grid(row=1, column=1, columnspan=3, sticky="w", pady=(0, 6))
        self.twirl_buttons = []
        for way in ("left", "right"):
            b = ttk.Radiobutton(ways, text=tr("liquify.twirl_" + way), value=way, variable=self.twirl,
                                command=lambda: put("twirl", self.twirl.get()))
            b.pack(side="left", padx=(0, 8))
            Tooltip(b, tr("liquify.twirl_way_tip"))
            self.twirl_buttons.append(b)
        self.boxes = {}
        for row, name in enumerate(BRUSH, start=2):
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
            self.boxes[name] = (e, var, word)
        row = len(BRUSH) + 2
        # (At the edges: Stir's choices, the shapes' own)
        self.edge_word = ttk.Label(form, text=tr("liquify.edge"))
        self.edge_word.grid(row=row, column=0, sticky="w", pady=(6, 2))
        Tooltip(self.edge_word, tr("liquify.edge_tip"))
        names = [tr(f"stir.edge_{c}") for c in STIR_EDGES]
        self.edge_box = ttk.Combobox(form, values=names, state="readonly", width=max(len(n) for n in names) + 1)
        self.edge_box.grid(row=row, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(6, 2))
        self.edge_box.bind("<<ComboboxSelected>>", lambda e: self.on_edge())
        Tooltip(self.edge_box, tr("liquify.edge_tip"))
        buttons = ttk.Frame(box)
        buttons.pack(anchor="w", fill="x", pady=(10, 0))
        b = ttk.Button(buttons, text=tr("liquify.restore_all"), command=lambda: self.on_all("restore"))
        b.pack(side="left")
        Tooltip(b, tr("liquify.restore_all_tip"))
        mask = ttk.Frame(box)
        mask.pack(anchor="w", fill="x", pady=(10, 0))
        t = ttk.Checkbutton(mask, text=tr("liquify.show_mask"), variable=self.show_mask, command=self.on_show_mask)
        t.pack(anchor="w")
        Tooltip(t, tr("liquify.show_mask_tip"))
        row2 = ttk.Frame(mask)
        row2.pack(anchor="w", pady=(4, 0))
        for kind in ("freeze", "thaw", "invert"):
            b = ttk.Button(row2, text=tr(f"liquify.{kind}_all"), command=lambda k=kind: self.on_all(k))
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, tr(f"liquify.{kind}_all_tip"))
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

    def turn(self):
        """Twirl's way round as liquify.dab's turn: Right = clockwise on screen (dab's +1 turns the colours
        anticlockwise: each spot fetches from clockwise of it; dev/scratch/lq_twirl_way.py)."""
        return -1 if self.twirl.get() == "right" else 1

    def on_tool(self):
        put("tool", self.tool.get())
        self.sync_tool()

    def sync_tool(self):
        """Settings a tool doesn't use greyed (Twirl's way round, Rate for Push)."""
        tool = self.tool.get()
        on = ["!disabled"] if tool == "twirl" else ["disabled"]
        for b in (*self.twirl_buttons, self.twirl_word):
            b.state(on)
        e, _, word = self.boxes["rate"]
        on = ["!disabled"] if tool in HOLD_TOOLS else ["disabled"]
        e.state(on)
        word.state(on)

    def on_box(self, name):
        e, var, _ = self.boxes[name]
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
        put(name, fmt(v))
        self.app.roll.draw_liquify_brush()

    def on_edge(self):
        i = self.edge_box.current()
        if i < 0:
            return
        edge = STIR_EDGES[i]
        self.edge = edge
        put("edge", edge)

        def change(old, sh):
            got = (old or {}).get("liquify")
            if not got or got.get("edge", "escape") == edge:
                return old
            return dict(old, liquify=dict(got, edge=edge))
        self.app.change_recolour(change, tr("liquify.undo_edge"))
        self.sync()

    def on_all(self, kind):
        if (self.app.roll.drag or ("",))[0] == "liquify":
            return
        self.app.roll.liquify_all(kind)
        self.sync()

    def on_show_mask(self):
        put("show_mask", "1" if self.show_mask.get() else "0")
        self.app.roll.draw_liquify_mask()

    def sync(self):
        app = self.app
        tgts = self.targets()
        self.what.config(text=tr("liquify.nothing") if not tgts else
                         tr("liquify.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("liquify.n_shapes", n=len(tgts)))
        rcs = [clean_recolour(t.get("recolour")) for t in tgts]
        lonely, single = app.recolour_trouble(tgts, [r or {"mode": KEEP} for r in rcs])
        lqs = [(r or {}).get("liquify") for r in rcs]
        zero = lqs and all(g and g.get("strength", 100.0) == 0 for g in lqs)  # (Recolour row's Strength 0 %)
        text = tr("liquify.needs") if lonely else tr("liquify.one") if single else tr("liquify.zero") if zero else ""
        if text != self.warn.cget("text"):
            self.warn.config(text=text)
            if text:
                self.warn.pack(anchor="w", fill="x", pady=(6, 0), after=self.hint)
            else:
                self.warn.pack_forget()
        # (At the edges: the shapes' own, a shape without a Liquify yet = the window's pick for its first stroke)
        edges = {((r or {}).get("liquify") or {"edge": self.edge}).get("edge", "escape") for r in rcs} or {self.edge}
        self.edge_box.set(tr(f"stir.edge_{edges.pop()}") if len(edges) == 1 else "")
        self.sync_tool()
        app.roll.draw_liquify_mask()
        app.roll.draw_liquify_brush()

    def close(self):
        roll = self.app.roll
        if (roll.drag or ("",))[0] == "liquify":
            roll.liquify_release(None)
        self.app.liquify_window = None
        roll.lq_end = None  # (a straight stroke's start: forgotten with the window)
        self.destroy()
        roll.focus_set()
        roll.draw_liquify_mask()
        roll.draw_liquify_brush()


def open_liquify(app):
    w = getattr(app, "liquify_window", None)
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        app.roll.lq_end = None  # (a straight stroke starts from a stroke made since the window opened)
        LiquifyWindow(app)
        app.tips.show("liquify", wait=True)  # (the first time: what it does, Help → Liquify)
