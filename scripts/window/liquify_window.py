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
from window.edit_line import EditLine
from window.widgets import Scrub, ShortLabel, Tooltip, bad, good, leave_box, open_or_lift, remember_place, \
    same_or_blank, unchanged

# (Pinch + Bulge and Freeze + Thaw = ONE tool each here, Alt held = the other: user 11-07 "too much options"; the
# dab maths keep all of them)
TOOL_ROWS = (("push", "restore", "smooth"), ("twirl", "pinch", "freeze"))
TOOLS = tuple(t for row in TOOL_ROWS for t in row)
# name -> (default, least, most, unit, steps)
BRUSH = {"size": (100.0, 4.0, 1000.0, "px", (5, 50, 1)),
         "density": (50.0, 0.0, 100.0, "%", (1, 10, 0.1)),
         "pressure": (50.0, 1.0, 100.0, "%", (1, 10, 0.1)),
         "rate": (80.0, 0.0, 100.0, "%", (1, 10, 0.1)),
         "smoothing": (0.0, 0.0, 100.0, "%", (1, 10, 0.1))}


def saved(name):
    """A brush setting as saved (settings.ini [liquify]), or its default."""
    d, lo, hi = BRUSH[name][:3]
    try:
        v = float(settings.get("liquify", name, d))
    except (TypeError, ValueError):
        return d
    return min(max(v, lo), hi) if v == v else d


def put(name, value):
    settings.put_quiet("liquify", name, value)


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
        # (Moves: Colours / Shape on top, like Stir's and Warp's (PLAN.md "Deform shapes" stage G); Colours bend too
        # beside it; a shape without a Liquify takes them with its first stroke: settings.ini [liquify])
        self.moves = "shape" if settings.get("liquify", "moves", "colours") == "shape" else "colours"
        self.bend_colours_pick = settings.get("liquify", "bend_colours", "1") != "0"
        self.chop_pick = settings.get("liquify", "chop", "1") != "0"  # (Cut notes where bent, user 2026-11-17)
        word = ttk.Label(form, text=tr("stir.moves"))
        word.grid(row=0, column=0, sticky="nw", pady=(0, 6))
        Tooltip(word, tr("liquify.moves_tip"))
        top = ttk.Frame(form)
        top.grid(row=0, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(0, 6))
        line = ttk.Frame(top)
        line.pack(anchor="w")
        values = [tr("stir.moves_colours"), tr("stir.moves_shape")]
        b = self.moves_box = ttk.Combobox(line, values=values, state="readonly", width=max(len(v) for v in values) + 1)
        b.pack(side="left")
        b.bind("<<ComboboxSelected>>", lambda e: self.on_moves())
        Tooltip(b, tr("liquify.moves_tip"))
        self.bend_colours = tk.BooleanVar(value=True)
        t = self.bend_colours_tick = ttk.Checkbutton(line, text=tr("stir.bend_colours"), variable=self.bend_colours,
                                                     command=self.on_bend_colours)
        t.pack(side="left", padx=(12, 0))
        Tooltip(t, tr("liquify.bend_colours_tip"))
        # (Cut notes where bent under Moves, like the Stir window's; live on drawn shapes set to Shape)
        self.chop = tk.BooleanVar(value=True)
        t = self.chop_tick = ttk.Checkbutton(top, text=tr("stir.chop"), variable=self.chop, command=self.on_chop)
        t.pack(anchor="w", pady=(2, 0))
        Tooltip(t, tr("liquify.chop_tip"))
        ttk.Label(form, text=tr("liquify.tool")).grid(row=1, column=0, sticky="nw", pady=(0, 6))
        tools = ttk.Frame(form)
        tools.grid(row=1, column=1, columnspan=3, sticky="w", pady=(0, 6))
        for r, names in enumerate(TOOL_ROWS):
            for c, t in enumerate(names):
                b = ttk.Radiobutton(tools, text=tr("liquify." + t), value=t, variable=self.tool, command=self.on_tool)
                b.grid(row=r, column=c, sticky="w", padx=(0, 8))
                Tooltip(b, tr(f"liquify.{t}_tip"))
        # (Twirl's way round: easy to find here; Alt held turns it back, like the editors; user 11-07)
        self.twirl_word = ttk.Label(form, text=tr("liquify.twirl_way"))
        self.twirl_word.grid(row=2, column=0, sticky="w", pady=(0, 6))
        Tooltip(self.twirl_word, tr("liquify.twirl_way_tip"))
        ways = ttk.Frame(form)
        ways.grid(row=2, column=1, columnspan=3, sticky="w", pady=(0, 6))
        self.twirl_buttons = []
        for way in ("left", "right"):
            b = ttk.Radiobutton(ways, text=tr("liquify.twirl_" + way), value=way, variable=self.twirl,
                                command=lambda: put("twirl", self.twirl.get()))
            b.pack(side="left", padx=(0, 8))
            Tooltip(b, tr("liquify.twirl_way_tip"))
            self.twirl_buttons.append(b)
        self.boxes = {}
        for row, name in enumerate(BRUSH, start=3):
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
        row = len(BRUSH) + 3
        # (At the edges: Stir's choices, the shapes' own)
        self.edge_word = ttk.Label(form, text=tr("liquify.edge"))
        self.edge_word.grid(row=row, column=0, sticky="w", pady=(6, 2))
        Tooltip(self.edge_word, tr("liquify.edge_tip"))
        names = [tr(f"stir.edge_{c}") for c in STIR_EDGES]
        self.edge_box = ttk.Combobox(form, values=names, state="readonly", width=max(len(n) for n in names) + 1)
        self.edge_box.grid(row=row, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(6, 2))
        self.edge_box.bind("<<ComboboxSelected>>", lambda e: self.on_edge())
        Tooltip(self.edge_box, tr("liquify.edge_tip"))
        # (Strength % + the show tick: the shapes' own (user 2026-11-09: in the window, like Warp's; they were a
        # Recolour row); one undo step each, several picked and different = blank box / half tick, typing sets all)
        row += 1
        word = ttk.Label(form, text=tr("liquify.strength"))
        word.grid(row=row, column=0, sticky="w", pady=2)
        Tooltip(word, tr("liquify.strength_tip"))
        self.strength_var = tk.StringVar()
        e = self.strength_entry = ttk.Entry(form, textvariable=self.strength_var, width=7)
        e.grid(row=row, column=1, sticky="w", padx=(6, 4), pady=2)
        Tooltip(e, tr("liquify.strength_tip"))
        Scrub(app, [(e, self.strength_var, lambda: self.on_shown("strength"))], (1, 10, 0.1), 0, lq.MOST_STRENGTH,
              label=word, drag_box=True)
        ttk.Label(form, text="%").grid(row=row, column=2, sticky="w")
        e.bind("<Return>", lambda ev: self.on_shown("strength"))
        leave_box(app, e, self.strength_var, lambda left: self.on_shown("strength", left and e))
        self.shown = tk.BooleanVar(value=True)
        t = self.shown_tick = ttk.Checkbutton(form, text=tr("liquify.shown"), variable=self.shown,
                                              command=lambda: self.on_shown("on"))
        t.grid(row=row, column=3, sticky="w", padx=(12, 0))
        Tooltip(t, tr("liquify.shown_tip"))
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
        self.edit_line = EditLine(box, app, "liquify", self.hint, 220)  # (later colour steps hidden: PLAN.md C)
        self.protocol("WM_DELETE_WINDOW", self.close)
        # (Esc: a straight stroke's start placed goes first, like on the piano roll; then the window closes)
        self.bind("<Escape>", lambda e: self.on_escape())
        self.sync()
        remember_place(self, "liquify")
        self.deiconify()

    def targets(self):
        return self.app.recolour_targets()

    def kind_picks(self, sh=None):
        """What a shape's first stroke takes from the window: Moves (+ Colours bend too / Cut notes where bent off), as
        saved (a picture: nothing, it stays Colours; hunt 2026-11-17)."""
        from notes.shape_bend import can_bend
        if self.moves != "shape" or (sh is not None and not can_bend(sh)):
            return {}
        return {"moves": "shape", **({} if self.bend_colours_pick else {"bend_colours": False}),
                **({} if self.chop_pick else {"chop": False})}

    def kind_of(self, sh):
        """What shape sh's Liquify bends ("colours" / "shape"; none yet = the window's pick; a picture = Colours)."""
        from notes.shape_bend import can_bend
        if not can_bend(sh):
            return "colours"
        g = (clean_recolour(sh.get("recolour")) or {}).get("liquify")
        return (g.get("moves") if g else self.moves) or "colours"

    def kinds(self):
        """What each selected shape's Liquify bends."""
        return [self.kind_of(t) for t in self.targets()]

    def on_moves(self):
        """Moves: Colours / Shape: the selected shapes' Liquifies bend that (one undo step); the window's pick for
        shapes without one yet."""
        i = self.moves_box.current()
        if i < 0:
            return
        kind = ("colours", "shape")[i]

        from notes.shape_bend import can_bend

        # (a Colours map says where colours come FROM, a Shape one where points GO: one can't be read as the other.
        # Bends of the other kind = asked first, then cleared (user 2026-11-17, PLAN.md Liquify Shape rebuild))
        def bent(sh):
            g = (clean_recolour(sh.get("recolour")) or {}).get("liquify")
            return bool(g and g.get("map") and can_bend(sh) and (g.get("moves") or "colours") != kind)
        if any(bent(sh) for sh in self.targets()):
            from tkinter import messagebox
            if not messagebox.askyesno(tr("liquify.title"), tr("liquify.moves_ask"), parent=self):
                self.sync()  # (the box back to what the shapes have)
                return
        self.moves = kind
        put("moves", self.moves)

        def change(old, sh):
            got = (old or {}).get("liquify")
            if not got or not can_bend(sh):  # (a picture keeps its colour Liquify: hunt 2026-11-17)
                return old
            if (got.get("moves") or "colours") == kind:
                return old
            new = {k: v for k, v in got.items() if k not in ("moves", "bend_colours", "chop", "map", "forward")}
            new = dict(new, forward=True, **self.kind_picks(sh)) if kind == "shape" else new  # (+ the window's ticks)
            return dict(old, liquify=new)
        self.app.change_recolour(change, tr("liquify.undo_moves"))
        self.sync()

    def on_chop(self):
        v = bool(self.chop.get())
        self.chop_tick.state(["!alternate"])
        self.chop_pick = v
        put("chop", "1" if v else "0")

        def change(old, sh):
            got = (old or {}).get("liquify")
            if not got or got.get("moves") != "shape":
                return old
            new = {k: x for k, x in got.items() if k != "chop"}
            return dict(old, liquify=new if v else dict(new, chop=False))
        self.app.change_recolour(change, tr("liquify.undo_chop"))
        self.sync()

    def on_bend_colours(self):
        v = bool(self.bend_colours.get())
        self.bend_colours_tick.state(["!alternate"])
        self.bend_colours_pick = v
        put("bend_colours", "1" if v else "0")

        def change(old, sh):
            got = (old or {}).get("liquify")
            if not got or got.get("moves") != "shape":
                return old
            new = {k: x for k, x in got.items() if k != "bend_colours"}
            return dict(old, liquify=new if v else dict(new, bend_colours=False))
        self.app.change_recolour(change, tr("liquify.undo_bend_colours"))
        self.sync()

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
        """Settings a tool doesn't use greyed (Twirl's way round). Rate never: Push uses it too, on a held straight
        stroke (user 11-07)."""
        tool = self.tool.get()
        on = ["!disabled"] if tool == "twirl" else ["disabled"]
        for b in (*self.twirl_buttons, self.twirl_word):
            b.state(on)

    def on_escape(self):
        if not self.app.roll.liquify_forget():
            self.close()

    def take_typed(self):
        """Numbers typed in the boxes without Enter count from now on (a press on the piano roll: before its first
        dab, like the panel's boxes; their leaving the keyboard comes only after the press)."""
        for name in self.boxes:
            self.on_box(name)

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

    def on_shown(self, what, left=None):
        """The show tick (what = "on": the bends shown / hidden, kept either way) or Strength ("strength"). Only
        shapes with a Liquify change; several picked keep their own strengths until one is typed."""
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
                ok = 0 <= s <= lq.MOST_STRENGTH
            except (ValueError, ZeroDivisionError):
                ok = False
            if not ok:
                return bad(e)
            good(e)
            set_to = {"strength": round(s, 3)}

        def changed(old, _sh=None):
            got = (old or {}).get("liquify")
            if not got:
                return old
            return dict(old, liquify=dict(got, **set_to))
        self.app.change_recolour(changed, tr("liquify.undo_show" if what == "on" else "liquify.undo_strength"))
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
        lqs = [(r or {}).get("liquify") for r in rcs]
        # (Moves: each shape's own (none yet = the window's pick); Shape greys At the edges, never warns about
        # Channels, says in orange when the outline stays as it was)
        kinds = set(self.kinds()) or {self.moves}
        shape = kinds == {"shape"}
        if len(kinds) > 1:
            self.moves_box.set("")
        else:
            self.moves_box.current(("colours", "shape").index(next(iter(kinds))))
        pictures = tgts and all("picture" in t or "picture_notes" in t for t in tgts)  # (pictures: Shape greyed)
        self.moves_box.config(state="readonly" if tgts and not pictures else "disabled")
        # (Cut notes where bent: drawn shapes set to Shape (pasted notes are always cut); Colours bend too only where
        # the outline bends, user 2026-11-17)
        drawn = [g for g, k, t in zip(lqs, self.kinds(), tgts) if k == "shape" and "notes" not in t]
        chops = {g.get("chop", True) if g else self.chop_pick for g in drawn} or {self.chop_pick}
        self.chop.set(chops == {True})
        self.chop_tick.state(["alternate" if len(chops) > 1 else "!alternate",
                              "!disabled" if drawn or not tgts and self.moves == "shape" else "disabled"])
        laid = [g for g in drawn if not (g.get("chop", True) if g else self.chop_pick)]
        bends_ = {g.get("bend_colours", True) if g else self.bend_colours_pick for g in laid} or {
            self.bend_colours_pick}
        self.bend_colours.set(bends_ == {True})
        self.bend_colours_tick.state(["alternate" if len(bends_) > 1 else "!alternate",
                                      "!disabled" if laid or not tgts and self.moves == "shape" and not self.chop_pick
                                      else "disabled"])
        self.edge_box.config(state="disabled" if shape else "readonly")
        if shape:
            from notes.shape_bend import liquify_shape
            from window.stir_window import outline_kept
            lonely = single = False
            same = outline_kept(app, tgts, lambda r, t: liquify_shape(r, t.get("off")) is not None)
        else:
            lonely, single = app.recolour_trouble(tgts, [r or {"mode": KEEP} for r in rcs])
            same = False
        hint = tr("liquify.hint_shape" if shape else "liquify.hint")
        if self.hint.cget("text") != hint:
            self.hint.config(text=hint)
        zero = lqs and all(g and g.get("strength", 100.0) == 0 for g in lqs)  # (Strength 0 %)
        text = (tr("liquify.needs") if lonely else tr("liquify.one") if single else tr("liquify.zero") if zero else
                tr("liquify.shape_same") if same else "")
        if text != self.warn.cget("text"):
            self.warn.config(text=text)
            if text:
                self.warn.pack(anchor="w", fill="x", pady=(6, 0), after=self.hint)
            else:
                self.warn.pack_forget()
        # (At the edges: the shapes' own, a shape without a Liquify yet = the window's pick for its first stroke)
        edges = {((r or {}).get("liquify") or {"edge": self.edge}).get("edge", "escape") for r in rcs} or {self.edge}
        self.edge_box.set(tr(f"stir.edge_{edges.pop()}") if len(edges) == 1 else "")
        # (Strength + tick: greyed while no selected shape has a Liquify yet (a first stroke starts at 100 %, shown))
        have = [dict(lq.DEFAULTS, **g) for g in lqs if g]
        e = self.strength_entry
        try:
            typing = self.focus_get()
        except (KeyError, tk.TclError):
            typing = None
        app._loading = True
        try:
            ons = {g["on"] for g in have} or {True}
            self.shown.set(ons == {True})
            self.shown_tick.state(["alternate" if len(ons) > 1 else "!alternate"])
            if typing is not e or unchanged(e):  # (never under the typing)
                same_or_blank(e, self.strength_var, [fmt(g["strength"]) for g in have] or ["100"])
        finally:
            app._loading = False
        for w in (e, self.shown_tick):
            w.state(["!disabled"] if have else ["disabled"])
        self.sync_tool()
        app.roll.draw_liquify_mask()
        app.roll.draw_liquify_brush()

    def close(self):
        roll = self.app.roll
        if (roll.drag or ("",))[0] == "liquify":
            roll.liquify_release(None)
        # (numbers typed without Enter count, like the Stir window: X / Esc never lose them; hunt 2026-11-09)
        self.take_typed()
        if not unchanged(self.strength_entry):
            self.on_shown("strength", self.strength_entry)
        self.app.liquify_window = None
        self.app.colour_edit_leave("liquify")  # (its later colour steps worked out again, PLAN.md stage C)
        self.app.colour_window_closed()  # (quick strokes' kept work let go: memory use, user 2026-11-10)
        roll.lq_end = None  # (a straight stroke's start: forgotten with the window)
        self.destroy()
        roll.focus_set()
        roll.draw_liquify_mask()
        roll.draw_liquify_brush()


def open_liquify(app):
    def make():
        app.roll.lq_end = None  # (a straight stroke starts from a stroke made since the window opened)
        LiquifyWindow(app)
    if open_or_lift(app, "liquify_window", make):
        app.tips.show("liquify", wait=True)  # (the first time: what it does, Help → Liquify)
