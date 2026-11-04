"""Recolour's Stir… window (side panel Stir… beside New shuffle, panel_recolour.py; notes/recolour.py stirred; a
user's request 2026-11-02, the user picked its own window): Turn, Twist and the Centre point bend the colours the
selected shapes have now, no colour made or thrown away. Works on top of any Recolour mode, also Off (then the shape's
Recolour is mode KEEP: the dropdown still shows Off). Stays open and follows the selection like Shares…; each knob
turn / wheel run / typed number is one undo step; Reset = no stir (one step). The Centre point is also a dot on the
piano roll while this window is open (roll_centre.py)."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.recolour import (DRAWN, KEEP, LEAST_REACH, LEAST_STRETCH, MOST_REACH, MOST_SHAPE_POINTS, MOST_STIRS,
                            MOST_STRETCH, MOST_TIGHT, MOST_TURN, SPOT_LEAST, SPOT_MOST, STIR_AREAS, STIR_EDGES, STIR_SETS, area_from_box,
                            centre_at, clean_area_shape, clean_recolour, full_stir, new_seed, stir_stack)
from roll.roll_live import STROKE_TOOLS
from window import look
from window.drawer import library_lists, shape_path
from window.shares_window import AngleKnob, HeldKnobs
from window.tool_window import Knob
from window.widgets import (Scrub, Tooltip, bad, good, name_index, name_list, remember_place, set_names, show_mixed,
                            unhighlight)

TWIST_KNOB = 5.0  # turns at the Twist knob's ends (the box goes up to MOST_TIGHT)


def stirred_rc(old, stir):
    """A shape's Recolour (clean, or None = Off) with its Stir = stir (one stir, a list of them: stacked, or None =
    none)."""
    rc = {k: v for k, v in (old or {}).items() if k != "stir"}
    if stir:
        rc["stir"] = stir
    if not old:
        rc.update(mode=KEEP, seed=new_seed())
    return clean_recolour(rc)


def stir_at(rc, i):
    """Stir number i (0 = the first) of a Recolour, every setting filled in (none there yet: the defaults)."""
    stack = stir_stack((rc or {}).get("stir"))
    return stack[i] if i < len(stack) else full_stir(None)


def library_area(app, name):
    """A library drawing as a Stir area's own copy (recolour stir["shape"]): its filled loops (as Fill sees them) in
    its 0..1 box, its proportions as drawn; None when it has no inside (only open lines) or can't be read."""
    from notes.custom import fill_plan
    tpl = app.custom_template(name)
    if not tpl:
        return None
    strokes, drawn = tpl[0], tpl[1] or 1.0
    try:
        polys = fill_plan(app.new_custom(strokes, 0, 0, 1, 1, drawn=drawn))["polys"]
    except (ValueError, MemoryError):
        return None
    loops = [np.asarray(p, np.float64).reshape(-1, 2) for p in polys]
    loops = [p for p in loops if len(p) >= 3]
    total = sum(len(p) for p in loops)
    if total > MOST_SHAPE_POINTS:  # (a very busy drawing: every k-th point, enough for an area's fade)
        k = -(-total // MOST_SHAPE_POINTS)
        loops = [p[::k] for p in loops if len(p[::k]) >= 3]
    return clean_area_shape({"name": name, "loops": [p.tolist() for p in loops], "aspect": float(drawn)})


def with_stir(old, i, change):
    """old's Recolour with stir number i = change(that stir, filled in) (the stack padded with plain stirs up to it;
    change None = that stir taken out of the stack)."""
    stack = stir_stack((old or {}).get("stir"))
    stack += [full_stir(None)] * (i + 1 - len(stack))
    if change is None:
        del stack[i]
    else:
        stack[i] = change(dict(stack[i]))
    return stirred_rc(old, stack or None)


class StirWindow(HeldKnobs, tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.stir_window = self
        self.title(tr("stir.title"))
        self.transient(app)
        self.resizable(False, False)
        self.hold = None
        self.wheel = None
        self.twist_real, self.twist_at, self.zeroing = 0.0, 0.0, False  # (sync: the Twist shown, its knob's place)
        self.boxes = {}  # name -> {var, entry, shown, lo, hi}
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w")
        # STACKED STIRS (BMC tester 11-03): a tab per stir, in order; "+" shows a new one (saved once a setting
        # changes, like the note tool pages), × takes the shown one away
        self.tab, self.fresh, self.tab_count = 0, False, 0
        self.tab_var = tk.IntVar(value=0)
        self.tab_row = ttk.Frame(box)
        self.tab_row.pack(anchor="w", fill="x", pady=(6, 0))
        self.tab_buttons = ttk.Frame(self.tab_row)
        self.tab_buttons.pack(side="left")
        self.tab_add = ttk.Button(self.tab_row, text="+", width=3, command=self.on_add)
        self.tab_add.pack(side="left", padx=(4, 0))
        Tooltip(self.tab_add, tr("stir.add_tip", most=MOST_STIRS))
        self.tab_drop = ttk.Button(self.tab_row, text="×", width=3, command=self.on_drop)
        self.tab_drop.pack(side="right")
        Tooltip(self.tab_drop, tr("stir.drop_tip"))
        # (Area "Draw one…", PLAN C3: shown while an area is being drawn on the piano roll)
        self.drawing = None
        self.draw_bar = ttk.Frame(box)
        self.draw_text = ttk.Label(self.draw_bar, text=tr("stir.draw_hint"), foreground=look.HINT,
                                   wraplength=int(300 * app.scale), justify="left")
        self.draw_text.pack(anchor="w", fill="x")
        self.draw_save = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.draw_bar, text=tr("stir.draw_save"), variable=self.draw_save).pack(anchor="w",
                                                                                               pady=(4, 0))
        row = ttk.Frame(self.draw_bar)
        row.pack(fill="x", pady=(4, 0))
        self.draw_use = ttk.Button(row, text=tr("stir.draw_use"), command=self.on_draw_use)
        self.draw_use.pack(side="left")
        ttk.Button(row, text=tr("stir.draw_cancel"), command=self.end_draw).pack(side="left", padx=(6, 0))
        knobs = row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        for name, unit, lo, hi in (("turn", "°", -MOST_TURN, MOST_TURN), ("twist", "", -MOST_TIGHT, MOST_TIGHT)):
            cell = ttk.Frame(row, padding=(0, 0, 14, 0))
            cell.pack(side="left", anchor="n")
            word = ttk.Label(cell, text=tr("stir." + name))
            word.pack()
            turned = lambda v, done, name=name: self.on_turned(name, v, done)
            if name == "turn":
                knob = self.turn_knob = AngleKnob(cell, app.scale, turned, self.knob_pressed, MOST_TURN)
            else:
                knob = self.twist_knob = Knob(cell, app.scale, turned, look.KNOB_GREEN, 40, self.knob_pressed)
            knob.pack()
            if name == "twist":  # (middle-click: back to 0 itself, also from past the knob's end)
                knob.bind("<ButtonPress-2>", lambda e: self.twist_to_zero())
            r = ttk.Frame(cell)
            r.pack()
            # (Turn goes past +-360 now: room for "-1234.567" whole, hunt 11-04)
            self._number_box(r, name, lo, hi, word, 10 if name == "turn" else 7)
            ttk.Label(r, text=unit or tr("stir.turns")).pack(side="left")
            for w in (word, knob):
                Tooltip(w, tr(f"stir.{name}_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        word = ttk.Label(row, text=tr("stir.centre"))
        word.pack(side="left")
        Tooltip(word, tr("stir.centre_tip"))
        for k, name in enumerate(("across", "up")):
            w = ttk.Label(row, text=tr("shares.centre_" + name))
            w.pack(side="left", padx=(6, 0))
            self._number_box(row, "centre" + str(k), 0, 100, w)
            ttk.Label(row, text="%").pack(side="left")
        self.middle = ttk.Button(row, text=tr("stir.middle"), command=self.on_middle)  # (BMC tester 11-04)
        self.middle.pack(side="left", padx=(6, 0))
        Tooltip(self.middle, tr("stir.middle_tip"))
        self.squeeze =tk.BooleanVar(value=True)  # (BMC tester 11-03: off = the point only says where it turns)
        self.squeeze_tick = ttk.Checkbutton(box, text=tr("stir.squeeze"), variable=self.squeeze,
                                            command=self.on_squeeze)
        self.squeeze_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.squeeze_tick, tr("stir.squeeze_tip"))
        # (Area, BMC tester 11-03 + the user's own idea 11-04: the stir only inside a circle / square / diamond
        # round the Centre point; Size = its width in % of the box; Whole shape = no area)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(4, 0))
        self.reach_last = 50.0  # (the size picking an area brings back: the last one shown)
        self.area_last = {"round": False, "stretch": 100.0, "seen": None, "spot": None, "tilt": 0.0,
                          "skew": 0.0}  # (likewise, hunt 11-04)
        w = ttk.Label(row, text=tr("stir.area"))
        w.pack(side="left")
        Tooltip(w, tr("stir.area_tip"))
        self.areas = ("whole",) + STIR_AREAS
        # (then, below a divider, the shape library's drawings: a drawn area, PLAN C2, user 11-04)
        self.library = []
        names = [tr("stir.area_" + a) for a in self.areas]
        self.area_box = ttk.Combobox(row, values=names, state="readonly", width=max(len(n) for n in names) + 4,
                                     postcommand=self.refresh_areas)
        name_list(self.area_box)
        self.refresh_areas()
        self.area_box.pack(side="left", padx=(5, 0))
        self.area_box.bind("<<ComboboxSelected>>", lambda e: self.on_area())
        self.area_tip = Tooltip(self.area_box, tr("stir.area_tip"))
        w = ttk.Label(row, text=tr("stir.reach_size"))
        w.pack(side="left", padx=(8, 0))
        Tooltip(w, tr("stir.reach_tip"))
        self._number_box(row, "reach", LEAST_REACH, MOST_REACH, w)
        ttk.Label(row, text="%").pack(side="left")
        # (PLAN C1, user 11-04: unticked = the area keeps its own spot, a square on the piano roll; Turn / Twist
        # still go round the Centre point)
        self.tied = tk.BooleanVar(value=True)
        self.tied_tick = ttk.Checkbutton(box, text=tr("stir.tied"), variable=self.tied, command=self.on_tied)
        self.tied_tick.pack(anchor="w", pady=(4, 0))
        Tooltip(self.tied_tick, tr("stir.tied_tip"))
        row = ttk.Frame(box)  # (the own spot as numbers too, user 11-04)
        row.pack(anchor="w", padx=(20, 0), pady=(2, 0))
        word = ttk.Label(row, text=tr("stir.spot"))
        word.pack(side="left")
        Tooltip(word, tr("stir.spot_tip"))
        for k, name in enumerate(("across", "up")):
            w = ttk.Label(row, text=tr("shares.centre_" + name))
            w.pack(side="left", padx=(6, 0))
            self._number_box(row, "spot" + str(k), SPOT_LEAST, SPOT_MOST, w)
            ttk.Label(row, text="%").pack(side="left")
        # (proportion, user's own idea 11-04: the area round as the piano roll shows it when ticked, + Stretch;
        # the spin round as seen too)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(4, 0))
        self.rounds = {}
        for key, parent in (("round", row), ("spin", box)):
            var = tk.BooleanVar(value=False)
            tick = ttk.Checkbutton(parent, text=tr("stir." + key), variable=var,
                                   command=lambda key=key: self.on_round(key))
            Tooltip(tick, tr(f"stir.{key}_tip"))
            self.rounds[key] = (var, tick)
            if key == "round":
                tick.pack(side="left")
                w = ttk.Label(row, text=tr("stir.stretch"))
                w.pack(side="left", padx=(8, 0))
                Tooltip(w, tr("stir.stretch_tip"))
                self._number_box(row, "stretch", LEAST_STRETCH, MOST_STRETCH, w)
                ttk.Label(row, text="%").pack(side="left")
                w = ttk.Label(row, text=tr("stir.angle"))  # (the area box turned, PLAN C2b)
                w.pack(side="left", padx=(8, 0))
                Tooltip(w, tr("stir.angle_tip"))
                self._number_box(row, "tilt", -360, 360, w)
                ttk.Label(row, text="°").pack(side="left")
            else:
                tick.pack(anchor="w", pady=(4, 0))
        # (user 11-04: a button takes the piano roll's look again, for both ticks; zoom alone never does)
        row = ttk.Frame(box)
        row.pack(anchor="w", fill="x", padx=(20, 0), pady=(4, 0))
        self.match = ttk.Button(row, text=tr("stir.match"), command=self.on_match)
        self.match.pack(side="left")
        Tooltip(self.match, tr("stir.match_tip"))
        self.to_shape = ttk.Button(row, text=tr("stir.to_shape"), command=self.on_to_shape)  # (user 11-04)
        self.to_shape.pack(side="right")
        Tooltip(self.to_shape, tr("stir.to_shape_tip"))
        row = ttk.Frame(box)  # (colour flips at this stir's step, BMC tester 11-04: notes stay, colours mirrored)
        row.pack(anchor="w", pady=(4, 0))
        w = ttk.Label(row, text=tr("stir.flip"))
        w.pack(side="left")
        Tooltip(w, tr("stir.flip_tip"))
        self.flips = {}
        for key in ("flip_side", "flip_up"):
            var = tk.BooleanVar(value=False)
            tick = ttk.Checkbutton(row, text=tr("stir." + key), variable=var,
                                   command=lambda key=key: self.on_flip(key))
            tick.pack(side="left", padx=(6, 0))
            Tooltip(tick, tr("stir.flip_tip"))
            self.flips[key] = (var, tick)
        # (a user's feedback 2026-11-02, recolour.stir_from / stirred; Colour sets only while a shape has 2+ sets)
        self.picks = {}  # key -> {row, box, tip, choices}
        for key, choices in (("edge", STIR_EDGES), ("sets", STIR_SETS)):
            if key == "edge":  # (beside the knobs, where the user's picture asked: name over the dropdown)
                row = ttk.Frame(knobs)
                row.pack(side="left", anchor="n")
                side, pad = "top", (4, 0)
            else:
                row = ttk.Frame(box)
                side, pad = "left", (5, 0)
            word = ttk.Label(row, text=tr(f"stir.{key}"))
            word.pack(side=side, anchor="w")
            names = [tr(f"stir.{key}_{c}") for c in choices]
            cb = ttk.Combobox(row, values=names, state="readonly", width=max(len(n) for n in names) + 1)
            if side == "top":
                cb.pack(side=side, anchor="w", pady=pad)
            else:
                cb.pack(side=side, anchor="w", padx=pad)
            cb.bind("<<ComboboxSelected>>", lambda e, key=key: self.on_pick(key))
            Tooltip(word, tr(f"stir.{key}_tip"))
            self.picks[key] = {"row": row, "box": cb, "tip": Tooltip(cb, tr(f"stir.{key}_tip")), "choices": choices}
        self.hint = ttk.Label(box, text=tr("stir.hint"), foreground=look.HINT, wraplength=int(300 * app.scale),
                              justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(8, 0))
        # (why a stir shows nothing: Channels not Multi channel / one colour; shown only then, hunt 2026-11-02)
        self.warn = ttk.Label(box, text="", foreground=look.WARN, wraplength=int(300 * app.scale), justify="left")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text=tr("shares.close"), command=self.close).pack(side="right")
        self.reset = ttk.Button(row, text=tr("stir.reset"), command=self.on_reset)
        self.reset.pack(side="left")
        Tooltip(self.reset, tr("stir.reset_tip"))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.sync()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "stir")
        app.centre_first = "stir"  # (its dots win over Clumps' on one spot: roll_centre.centre_boxes)
        self.bind("<FocusIn>", lambda e: setattr(app, "centre_first", "stir"), add="+")
        self.deiconify()

    def _number_box(self, parent, name, lo, hi, word, width=7):
        var = tk.StringVar()
        e = ttk.Entry(parent, textvariable=var, width=width, justify="right")
        e.pack(side="left", padx=(4, 1))
        self.boxes[name] = {"var": var, "entry": e, "shown": "", "lo": lo, "hi": hi}
        e.bind("<Return>", lambda ev: self.on_box(name))
        e.bind("<FocusOut>", lambda ev: self.on_box(name))
        Scrub(self.app, [(e, var, lambda: self.on_box(name))], (1, 10, 0.1), lo, hi, label=word, drag_box=True)

    # ---- what the selection holds

    def targets(self):
        """The selected shapes the window stirs; while an area is drawn: the ones it stirred when drawing began."""
        d, shapes = self.drawing, self.app.shapes
        if d:  # (by place: Ctrl+Z while drawing makes every shape anew, hunt C 4)
            return [shapes[i] for i in d["idx"] if i < len(shapes)]
        return self.app.recolour_targets()

    def stirs(self):
        """The shown tab's stir of each selected shape (filled in)."""
        return [stir_at(clean_recolour(t.get("recolour")), self.tab) for t in self.targets()]

    def shown_stir(self, rc):
        """The stir of Recolour rc this window shows (its tab's), filled in: the piano roll's dot + circle."""
        return stir_at(rc, self.tab)

    def sync_tabs(self, tgts):
        """The tab row: one tab per stir (the most any selected shape has, at least one; a new tab from "+" shown
        until something else is picked)."""
        count = max([len(stir_stack((clean_recolour(t.get("recolour")) or {}).get("stir"))) for t in tgts] + [1])
        if self.fresh and self.tab == count and count < MOST_STIRS:
            count += 1
        else:
            self.fresh = False
            self.tab = min(self.tab, count - 1)
        if count != self.tab_count:
            for w in self.tab_buttons.winfo_children():
                w.destroy()
            for i in range(count):
                ttk.Radiobutton(self.tab_buttons, text=tr("stir.tab", n=i + 1), variable=self.tab_var, value=i,
                                style="Toolbutton", command=self.on_tab).pack(side="left", padx=(0, 2))
            self.tab_count = count
        self.tab_var.set(self.tab)
        on = bool(tgts) and not self.drawing  # (an area being drawn: the whole window waits, hunt C 6)
        for w in self.tab_buttons.winfo_children():
            w.state(["!disabled" if on else "disabled"])
        self.tab_add.state(["!disabled" if on and count < MOST_STIRS and not self.fresh else "disabled"])
        shown = self.stirs()
        self.tab_drop.state(["!disabled" if on and (count > 1 or any(s != full_stir(None) for s in shown))
                             else "disabled"])

    def on_tab(self):
        self.take_typing()
        if self.fresh and self.tab_var.get() != self.tab:
            self.fresh = False
        self.tab = self.tab_var.get()
        self.wheel = None
        self.sync()

    def on_add(self):
        """A new stir after the others (saved once one of its settings changes)."""
        self.take_typing()
        self.tab, self.fresh = self.tab_count, True
        self.wheel = None
        self.sync()

    def on_drop(self):
        """The shown stir taken away from every selected shape that has it (one undo step)."""
        if self.drawing:  # (the selection is the drawing then, hunt C 6)
            return
        self.take_typing()
        i = self.tab
        if self.fresh:
            self.fresh = False
        else:
            self.wheel = None
            self.app.change_recolour(
                lambda old, sh: with_stir(old, i, None)
                if i < len(stir_stack((old or {}).get("stir"))) else old, tr("stir.undo_drop"))
        self.tab = max(0, i - 1)
        self.sync()

    def sync(self):
        """Show the selected shapes' Stir (a held knob stays where the mouse put it, its box follows): a box where
        they differ is blank; boxes being typed in keep the typing."""
        app = self.app
        held = self.held_knob()
        tgts = self.targets()
        self.sync_tabs(tgts)
        sts = self.stirs()
        self.what.config(text=tr("stir.nothing") if not tgts else
                         tr("stir.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("stir.n_shapes", n=len(tgts)))
        on = bool(tgts) and not self.drawing
        for name, knob, vals in (("turn", self.turn_knob, [s["turn"] for s in sts]),
                                 ("twist", self.twist_knob, [s["twist"] for s in sts]),
                                 ("centre0", None, [s["centre"][0] for s in sts]),
                                 ("centre1", None, [s["centre"][1] for s in sts])):
            if knob is held:  # (the twist's place from the press stays too: on_turned counts from it)
                pass
            elif knob is self.turn_knob:
                knob.set(round(vals[0], 1) if vals else 0.0)
            elif knob:
                self.twist_real = vals[0] if vals else 0.0
                self.twist_at = round(max(-100.0, min(100.0, self.twist_real / TWIST_KNOB * 100)), 1)
                knob.set(self.twist_at)
            if knob and knob is not held:
                knob.on(on)
            b = self.boxes[name]
            text = fmt(vals[0]) if vals and len(set(vals)) == 1 else ""
            if self.focus_get() is not b["entry"] or b["var"].get() == b["shown"]:
                b["var"].set(text)
            b["shown"] = text
            b["entry"].config(state="normal" if on else "disabled")
        for key, p in self.picks.items():
            vals = {s[key] for s in sts}
            if len(vals) == 1:
                p["box"].current(p["choices"].index(next(iter(vals))))
            show_mixed(p["box"], len(vals) > 1, p["tip"], tr(f"stir.{key}_tip"))
            p["box"].config(state="readonly" if on else "disabled")
        reaches = [s["reach"] for s in sts]
        drawn = {id(s["shape"]): s["shape"] for s in sts if s["reach"] is not None and s["area"] == DRAWN}
        # (equal drawings are one object: recolour.clean_area_shape; no writing every one out, hunt C 8)
        kinds = {"whole" if s["reach"] is None else s["area"] if s["area"] != DRAWN else id(s["shape"])
                 for s in sts}
        if len(kinds) == 1:
            kind = next(iter(kinds))
            if kind in self.areas:
                self.area_box.current(self.areas.index(kind))
            else:  # (a drawn area: its name, also when the library has none by it any more)
                self.area_box.set(drawn[kind]["name"] or tr("stir.area_drawn"))
        elif not kinds:
            self.area_box.current(0)
        show_mixed(self.area_box, len(kinds) > 1, self.area_tip, tr("stir.area_tip"))
        self.area_box.config(state="readonly" if on else "disabled")
        sizes = {r for r in reaches if r is not None}
        if len(sizes) == 1:
            self.reach_last = next(iter(sizes))
        b = self.boxes["reach"]
        text = fmt(next(iter(sizes))) if len(sizes) == 1 else "" if sizes else fmt(self.reach_last)
        if self.focus_get() is not b["entry"] or b["var"].get() == b["shown"]:
            b["var"].set(text)
        b["shown"] = text
        b["entry"].config(state="normal" if on and sizes else "disabled")
        areas = [s for s in sts if s["reach"] is not None]  # (Round as seen + Stretch: only with an area)
        looks = {(s["round"], s["stretch"], s["seen"] if s["round"] else None, tuple(s["spot"] or ()), s["tilt"],
                  s["skew"]) for s in areas}
        if len(looks) == 1:  # (what picking an area again brings back)
            r, w, v, p, t, k = next(iter(looks))
            self.area_last = {"round": r, "stretch": w, "seen": v, "spot": list(p) or None, "tilt": t, "skew": k}
        tied = {s["spot"] is None for s in areas}
        self.tied.set(tied != {False})
        self.tied_tick.state(["alternate" if len(tied) > 1 else "!alternate",
                              "!disabled" if on and areas else "disabled"])
        for key, (var, tick) in self.rounds.items():
            vals = {s[key] for s in (areas if key == "round" else sts)}
            var.set(vals == {True})
            live = on and (bool(areas) or key == "spin")
            tick.state(["alternate" if len(vals) > 1 else "!alternate", "!disabled" if live else "disabled"])
        self.match.state(["!disabled" if on and any(s["spin"] or (s["round"] and s["reach"] is not None)
                                                    for s in sts) else "disabled"])
        spots = [s for s in areas if s["spot"]]
        for k in (0, 1):
            b = self.boxes["spot" + str(k)]
            vals = {s["spot"][k] for s in spots}
            text = fmt(next(iter(vals))) if len(vals) == 1 else ""
            if self.focus_get() is not b["entry"] or b["var"].get() == b["shown"]:
                b["var"].set(text)
            b["shown"] = text
            b["entry"].config(state="normal" if on and spots else "disabled")
        self.to_shape.state(["!disabled" if on and areas else "disabled"])
        for key, empty in (("stretch", 100), ("tilt", 0)):
            b = self.boxes[key]
            vals = {s[key] for s in areas}
            text = fmt(next(iter(vals))) if len(vals) == 1 else "" if vals else fmt(empty)
            if self.focus_get() is not b["entry"] or b["var"].get() == b["shown"]:
                b["var"].set(text)
            b["shown"] = text
            b["entry"].config(state="normal" if on and areas else "disabled")
        squeezes = {s["squeeze"] for s in sts}
        self.squeeze.set(squeezes != {False})
        self.squeeze_tick.state(["alternate" if len(squeezes) > 1 else "!alternate",
                                 "!disabled" if on else "disabled"])
        for key, (var, tick) in self.flips.items():
            vals = {s[key] for s in sts}
            var.set(vals == {True})
            tick.state(["alternate" if len(vals) > 1 else "!alternate", "!disabled" if on else "disabled"])
        self.middle.state(["!disabled" if on and any(s["centre"] != [50.0, 50.0] for s in sts) else "disabled"])
        many =any(len(app.colour_owns(t)) > 1 for t in tgts)
        row = self.picks["sets"]["row"]
        if many != bool(row.winfo_manager()):
            if many:
                row.pack(anchor="w", pady=(6, 0), before=self.hint)
            else:
                row.pack_forget()
        self.reset.state(["!disabled" if on and any(s != full_stir(None) for s in sts) else "disabled"])
        d = self.drawing
        if d and d["pick"] and any(i >= len(app.shapes) or i not in d["idx"] for i in app.sels):
            self.after_idle(self.on_picked)  # (a shape clicked while picking: its outline = the area)
        lonely, single = app.recolour_trouble(tgts, [clean_recolour(t.get("recolour")) or {"mode": KEEP}
                                                     for t in tgts])
        text = tr("stir.needs") if lonely else tr("stir.one") if single else ""
        if text != self.warn.cget("text"):
            self.warn.config(text=text)
            if text:
                self.warn.pack(anchor="w", fill="x", pady=(6, 0), after=self.hint)
            else:
                self.warn.pack_forget()
        app.roll.request_redraw()  # (the Centre point dots: roll_centre.py)

    # ---- changes

    def set_stir(self, key, value, name=None, push=True):
        """Each selected shape's shown stir (its tab's) key = value (centre0 / centre1: one side of the Centre
        point); one undo step (push False: part of the step taken before). Size / Stretch only go to stirs with an
        area, Match the screen ("seen") only to stirs with a round tick on (hunt 11-04, like the area line's drag):
        the others are left exactly as they are (no stir padded in either)."""
        def change(st):
            if key == "centre":  # (the Middle button)
                st["centre"] = [50.0, 50.0]
            elif key in ("round", "spin"):  # (ticked: the piano roll's look right now, kept; zoom never changes it;
                # only for stirs that didn't have the tick: a half-ticked box keeps the others' look, hunt 11-04)
                if value and not st[key]:
                    st["seen"] = self.seen_now()
                st[key] = value
            elif key == "area":  # (None = Whole shape; an area on a whole-shape stir starts at the last size, Round
                # as seen / Stretch / look as last shown too, hunt 11-04)
                if value is None:
                    st["reach"] = None
                else:
                    if isinstance(value, dict):  # (a library drawing's own copy)
                        st["area"], st["shape"] = DRAWN, value
                    else:
                        st["area"], st["shape"] = value, None
                    if st["reach"] is None:
                        st["reach"] = self.reach_last
                        last = self.area_last
                        st["stretch"] = last["stretch"]
                        st["spot"] = last["spot"]
                        st["tilt"], st["skew"] = last.get("tilt", 0.0), last.get("skew", 0.0)
                        if last["round"] and last["seen"]:
                            st["round"] = True
                            if not st["spin"]:  # (a look already used by the spin tick stays)
                                st["seen"] = last["seen"]
            elif key == "tied":  # (unticked: the area stays where it is now, on its own spot)
                if value:
                    st["spot"] = None
                elif not st["spot"]:
                    st["spot"] = list(st["centre"])
            elif key.startswith("spot"):
                c = list(st["spot"])
                c[int(key[-1])] = value
                st["spot"] = c
            elif key.startswith("centre"):
                c = list(st["centre"])
                c[int(key[-1])] = value
                st["centre"] = c
            else:
                st[key] = value
            return st
        if self.drawing:  # (an area being drawn: the selection is the drawing, not the stirred shapes)
            return False
        only = {"reach": lambda st: st["reach"] is not None, "stretch": lambda st: st["reach"] is not None,
                "tied": lambda st: st["reach"] is not None, "tilt": lambda st: st["reach"] is not None,
                "spot0": lambda st: st["reach"] is not None and st["spot"],
                "spot1": lambda st: st["reach"] is not None and st["spot"],
                "seen": lambda st: st["spin"] or (st["round"] and st["reach"] is not None)}.get(key)
        self.fresh = False
        i = self.tab
        return self.app.change_recolour(
            lambda old, sh: with_stir(old, i, change) if only is None or only(stir_at(old, i)) else old,
            name or tr("stir.undo"), push)

    def on_turned(self, name, v, done):
        knob = self.turn_knob if name == "turn" else self.twist_knob
        if name == "twist":
            if abs(self.twist_real) > TWIST_KNOB and not self.zeroing:
                # (typed past the knob's end: the knob moves it from there, no jump to its end, hunt 2026-11-02)
                v = self.twist_real + (v - self.twist_at) / 100 * TWIST_KNOB
                v = round(max(-MOST_TIGHT, min(MOST_TIGHT, v)), 3)
            else:
                v = round(v / 100 * TWIST_KNOB, 3)
        self.on_turn(knob, done, lambda: self.set_stir(name, v))

    def twist_to_zero(self):
        self.zeroing = True
        try:
            self.twist_knob.turn_to(0, True)
        finally:
            self.zeroing = False

    def on_box(self, name):
        b = self.boxes[name]
        if b["var"].get() == b["shown"] or str(b["entry"].cget("state")) == "disabled":
            return
        try:
            v = round(calc(b["var"].get().strip().rstrip("%°")), 3)
            ok = b["lo"] <= v <= b["hi"]
        except ValueError:
            ok = False
        if not ok:
            b["var"].set(b["shown"])
            return bad(b["entry"])
        good(b["entry"])
        self.wheel = None
        b["shown"] = b["var"].get()
        self.set_stir(name, v)
        self.sync()

    def on_pick(self, key):
        p = self.picks[key]
        i = p["box"].current()
        unhighlight(p["box"])
        if i >= 0:
            self.wheel = None
            self.set_stir(key, p["choices"][i])
            self.sync()

    def seen_now(self):
        """Beats per key as the piano roll shows them now (a key is as tall as this many beats are wide)."""
        roll = self.app.roll
        return roll.sy / roll.sx if roll.sx and roll.sy else 1.0

    def on_round(self, key):
        self.wheel = None
        self.set_stir(key, bool(self.rounds[key][0].get()), tr("stir.undo_round"))
        self.sync()

    def on_match(self):
        """Match the screen: the round ticks take the piano roll's look right now (one undo step)."""
        self.wheel = None
        self.set_stir("seen", self.seen_now(), tr("stir.undo_match"))
        self.sync()

    def refresh_areas(self):
        """The Area dropdown's choices: Whole shape and the built-in areas, then the shape library's drawings (the
        built-in Circle / Square left out: the areas above are those exactly)."""
        top, saved = library_lists()
        self.library = [n for n in top if shape_path(n) or n.lower() not in ("circle", "square")] + saved
        shown = self.area_box.get()
        set_names(self.area_box, [tr("stir.area_" + a) for a in self.areas] + [tr("stir.area_draw"),
                                                                                tr("stir.area_pick")], self.library)
        self.area_box.set(shown)

    def on_area(self):
        i = name_index(self.area_box)
        unhighlight(self.area_box)
        if i < 0:
            return self.sync()
        self.wheel = None
        if i < len(self.areas):
            self.set_stir("area", self.areas[i] if i else None, tr("stir.undo_area"))
        elif i in (len(self.areas), len(self.areas) + 1):
            return self.on_draw(pick=i > len(self.areas))
        else:
            name = self.library[i - len(self.areas) - 2]
            shape = library_area(self.app, name)
            if shape is None:  # (only open lines, or the file can't be read: nothing to stir inside)
                self.app.status.config(text=tr("stir.area_no_inside", name=name))
                return self.sync()
            self.set_stir("area", shape, tr("stir.undo_area"))
        self.sync()

    def on_tied(self):
        self.wheel = None
        self.set_stir("tied", bool(self.tied.get()), tr("stir.undo_tied"))
        self.sync()

    def on_squeeze(self):
        self.wheel = None
        self.set_stir("squeeze", bool(self.squeeze.get()))
        self.sync()

    def on_flip(self, key):
        self.wheel = None
        self.set_stir(key, bool(self.flips[key][0].get()), tr("stir.undo_flip"))
        self.sync()

    def on_middle(self):
        """The shown stir's Centre point back to the middle (50 / 50), one undo step."""
        self.take_typing()
        self.wheel = None
        self.set_stir("centre", None, tr("stir.undo_centre"))
        self.sync()

    def on_reset(self):
        """The shown stir back to its start (one stir: no stir at all, as before stacking)."""
        if self.drawing:
            return
        self.wheel = None
        i = self.tab
        self.app.change_recolour(
            lambda old, sh: with_stir(old, i, lambda st: full_stir(None))
            if old and i < len(stir_stack(old.get("stir"))) else old, tr("stir.undo_reset"))
        self.sync()

    def take_typing(self):
        for name, b in self.boxes.items():
            if b["var"].get() != b["shown"]:
                self.on_box(name)

    # ---- Area "Draw one…" / "Pick a shape…" (PLAN C3, user 11-04; rebuilt after hunt C)

    def on_draw(self, pick=False):
        """Drawing an area (pick: picking a shape for it): the stirred shapes kept aside by their places, nothing
        selected, the window greyed. Drawing: Live shape OFF (user: confusing; strokes went into old shapes) and a
        drawing tool picked; every shape drawn from now on (it lands after the old ones) makes the area. Picking:
        the Select tool; a clicked shape lends its outline."""
        app = self.app
        tgts = list(self.targets())
        if not tgts or self.drawing:  # (picked again while drawing: nothing, hunt C 5)
            return self.sync()
        self.take_typing()
        idx = [i for i, s in enumerate(app.shapes) if any(s is t for t in tgts)]
        self.drawing = {"idx": idx, "count": len(app.shapes), "tab": self.tab, "live": app.live.get(),
                        "tool": app.tool.get(), "pick": pick}
        app.select(None)
        app.live.set(False)
        if pick:
            app.tool.set("select")
        elif app.tool.get() not in STROKE_TOOLS:
            app.tool.set(app.draw_tool if app.draw_tool in STROKE_TOOLS else "poly")
        self.draw_text.config(text=tr("stir.pick_hint" if pick else "stir.draw_hint"))
        if pick:
            self.draw_use.pack_forget()
        elif not self.draw_use.winfo_manager():
            self.draw_use.pack(side="left", before=self.draw_use.master.winfo_children()[-1])
        self.draw_bar.pack(anchor="w", fill="x", pady=(8, 0), after=self.tab_row)
        app.status.config(text=tr("stir.pick_status" if pick else "stir.draw_status"))
        self.sync()

    def end_draw(self, select=True):
        """Drawing an area over: Live shape and the tool as they were, the stirred shapes selected again (Cancel:
        the strokes stay, Ctrl+Z takes them back)."""
        d = self.drawing
        if not d:
            return
        idx = {i for i in d["idx"] if i < len(self.app.shapes)}
        self.drawing = None
        self.draw_bar.pack_forget()
        app = self.app
        app.live.set(d["live"])
        if app.tool.get() != d["tool"]:  # (the tool from before drawing: Select for the area's box)
            app.tool.set(d["tool"])
        if select and idx:
            app.select_many(idx, min(idx))
        self.sync()

    def on_draw_use(self):
        """Use as the area: every shape drawn since Draw one… (never one that was there before, hunt C 2) becomes
        the area; the strokes go."""
        app, d = self.app, self.drawing
        if not d:
            return
        if len(app.shapes) < d["count"] or len(self.targets()) != len(d["idx"]):
            app.status.config(text=tr("stir.draw_lost"))
            return self.end_draw()
        app.roll.cancel_draft()
        drawn = list(range(d["count"], len(app.shapes)))
        if not drawn:
            app.status.config(text=tr("stir.draw_none"))
            return
        self.area_from([app.shapes[i] for i in drawn], drawn, "stir.draw_none", tr("stir.undo_drawn"))

    def on_picked(self):
        """A shape clicked while picking: its outline becomes the area, where it is; it stays as it is."""
        app, d = self.app, self.drawing
        if not d or not d["pick"]:
            return
        olds = [app.shapes[i] for i in sorted(app.sels) if i < len(app.shapes) and i not in d["idx"]]
        if not olds:
            return
        if (app.roll.drag or ("",))[0] == "move":  # (the click picks; it doesn't move the shape)
            app.roll.drag = None
        self.area_from(olds[:1], (), "stir.pick_none", tr("stir.undo_picked"))

    def area_from(self, olds, drop, why, name):
        """The outline of shapes olds (several = one live shape, like Turn into live shape; open ends closed like
        Fill) becomes the stirred shapes' area where it is: its box turned into each one's middle / Size / Stretch /
        Angle (round as seen); shapes at places drop go (the drawn strokes); one undo step. Saved to the library too
        when ticked. why: the status text when it has no inside."""
        from notes.convert import to_live
        from notes.custom import drawing_in_box, fill_plan
        from notes.engine import cached_strokes
        app, d = self.app, self.drawing
        tgts, roll = self.targets(), app.roll
        try:
            sh = olds[0] if len(olds) == 1 and olds[0]["kind"] == "custom" and olds[0].get("strokes") else to_live(
                olds, [cached_strokes(s) for s in olds], app.defaults, app.custom_defaults, self.seen_now())
            held, _, box = drawing_in_box(sh)  # (the drawing as its shown box holds it, like saving it does)
            loops = [np.asarray(p, np.float64).reshape(-1, 2) for p in
                     fill_plan(app.new_custom(held, 0, 0, 1, 1))["polys"]]
        except (ValueError, KeyError, TypeError, IndexError, MemoryError):
            loops, box = [], None
        loops = [p for p in loops if len(p) >= 3]
        if box is not None:
            (b0, p0), (b1, p1), (b2, p2) = box
            wpx = math.hypot((b1 - b0) * roll.sx, (p1 - p0) * roll.sy)
            hpx = math.hypot((b2 - b0) * roll.sx, (p2 - p0) * roll.sy)
        if not loops or box is None or wpx < 1 or hpx < 1:
            app.status.config(text=tr(why))
            if d["pick"]:
                app.select(None)
            return
        total = sum(len(p) for p in loops)
        if total > MOST_SHAPE_POINTS:
            k = -(-total // MOST_SHAPE_POINTS)
            loops = [p[::k] for p in loops if len(p[::k]) >= 3]
        seen, i = self.seen_now(), d["tab"]
        loops = [p.tolist() for p in loops]
        shape = clean_area_shape({"name": "", "loops": loops, "aspect": wpx / hpx})
        puts = {}
        for t in tgts:
            made = roll.stir_made(t)
            if made is None:
                continue
            st = dict(stir_at(made[0], i), area=DRAWN, shape=shape, round=True, seen=seen)
            st["reach"] = st["reach"] or self.reach_last
            pcts = [centre_at(made[0], made[1], made[2], b * app.ppq, p) for b, p in box]
            got = area_from_box(st, *pcts, roll.area_k(made, st))
            if got is None:
                continue
            mid = got["middle"]
            own = st["spot"] or any(v < 0 or v > 100 for v in mid)  # (drawn past the box: its own spot, hunt C 7)
            lo, hi = (SPOT_LEAST, SPOT_MOST) if own else (0.0, 100.0)
            middle = [round(min(max(v, lo), hi), 3) for v in mid]
            puts[id(t)] = dict(area=DRAWN, round=True, seen=seen,
                               reach=round(min(max(got["reach"], LEAST_REACH), MOST_REACH), 3),
                               stretch=round(min(max(got["stretch"], LEAST_STRETCH), MOST_STRETCH), 3),
                               tilt=round(got["tilt"], 3), skew=round(got["skew"], 4),
                               **({"spot": middle} if own else {"centre": middle}))
        if not puts:  # (no stirred shape has notes: nothing would change, the strokes stay, hunt C 9)
            app.status.config(text=tr("stir.draw_no_notes"))
            return
        label = tr("stir.area_drawn")
        if self.draw_save.get():
            before = sh.get("name")
            app.save_to_library(sh)
            if sh.get("name") != before:
                label = sh["name"]
        elif d["pick"]:
            label = app.shape_label(olds[0])
        shape = clean_area_shape({"name": label, "loops": loops, "aspect": wpx / hpx})
        app.push_undo(name=name)
        app.select(None)  # (before the strokes go: nothing may point at them)
        for j in sorted(drop, reverse=True):
            del app.shapes[j]
        self.end_draw()
        app.change_recolour(lambda old, s: with_stir(old, i, lambda st: dict(st, shape=shape, **puts[id(s)]))
                            if id(s) in puts else old, None, False)
        app.shapes_changed()
        self.sync()

    def on_to_shape(self):
        """Area to a shape: each stirred shape's area (the shown tab's) put on the piano roll as a drawn custom shape
        where the area is (its box = the area's box); one undo step, the new shapes selected."""
        from notes.recolour import area_outlines
        app, roll = self.app, self.app.roll
        if self.drawing:
            return
        self.take_typing()
        new = []
        for t in self.targets():
            made = roll.stir_made(t)
            st = self.shown_stir(made[0]) if made else None
            if not st or st["reach"] is None:
                continue
            box = roll.area_box(made, least=0)
            if not box:
                continue
            frame = box[0]
            if st["area"] == DRAWN and st["shape"]:
                loops, aspect, label = st["shape"]["loops"], st["shape"]["aspect"], st["shape"]["name"]
            else:  # (a built-in area: its outline, in its own box)
                lines = area_outlines(dict(st, tilt=0.0, skew=0.0, stretch=100.0), 72)
                loops = [[[(x + 1) / 2, (y + 1) / 2] for x, y in zip(*ln)] for ln in lines]
                aspect, label = 1.0, tr("stir.area_" + st["area"])
            strokes = [{"kind": "poly", "pts": [list(map(float, p)) for p in lp] + [list(map(float, lp[0]))]}
                       for lp in loops]
            sh = app.new_custom(strokes, 0, 0, 1, 1, drawn=aspect)
            sh["pts"] = [list(map(float, p)) for p in frame]
            sh["name"] = label or tr("stir.area_drawn")
            new.append(sh)
        if not new:
            return
        roll.cancel_draft()
        app.push_undo(name=tr("stir.undo_to_shape"))
        app.shapes.extend(new)
        n = len(app.shapes)
        app.select_many(set(range(n - len(new), n)), n - len(new))
        app.shapes_changed()
        self.sync()

    def close(self):
        if self.drawing:
            self.end_draw()
        if self.hold:
            self.call_off(self.hold)
        self.take_typing()
        self.app.stir_window = None
        roll = self.app.roll
        if (roll.drag or ("",))[0] == "rccentre":  # (a Centre point dot held: let go, what it moved kept)
            roll.drag = None
        self.destroy()
        self.app.roll.focus_set()
        self.app.roll.request_redraw()


def open_stir(app):
    w = app.stir_window
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        StirWindow(app)
