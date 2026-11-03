"""Recolour's Stir… window (side panel Stir… beside New shuffle, panel_recolour.py; notes/recolour.py stirred; a
user's request 2026-11-02, the user picked its own window): Turn, Twist and the Centre point bend the colours the
selected shapes have now, no colour made or thrown away. Works on top of any Recolour mode, also Off (then the shape's
Recolour is mode KEEP: the dropdown still shows Off). Stays open and follows the selection like Shares…; each knob
turn / wheel run / typed number is one undo step; Reset = no stir (one step). The Centre point is also a dot on the
piano roll while this window is open (roll_centre.py)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.recolour import KEEP, MOST_TIGHT, STIR_EDGES, STIR_SETS, clean_recolour, full_stir, new_seed
from window import look
from window.shares_window import AngleKnob, HeldKnobs
from window.tool_window import Knob
from window.widgets import Scrub, Tooltip, bad, good, remember_place, show_mixed, unhighlight

TWIST_KNOB = 5.0  # turns at the Twist knob's ends (the box goes up to MOST_TIGHT)


def stirred_rc(old, stir):
    """A shape's Recolour (clean, or None = Off) with its Stir = stir (full_stir form, or None = none)."""
    rc = {k: v for k, v in (old or {}).items() if k != "stir"}
    if stir:
        rc["stir"] = stir
    if not old:
        rc.update(mode=KEEP, seed=new_seed())
    return clean_recolour(rc)


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
        knobs = row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        for name, unit, lo, hi in (("turn", "°", -360, 360), ("twist", "", -MOST_TIGHT, MOST_TIGHT)):
            cell = ttk.Frame(row, padding=(0, 0, 14, 0))
            cell.pack(side="left", anchor="n")
            word = ttk.Label(cell, text=tr("stir." + name))
            word.pack()
            turned = lambda v, done, name=name: self.on_turned(name, v, done)
            if name == "turn":
                knob = self.turn_knob = AngleKnob(cell, app.scale, turned, self.knob_pressed)
            else:
                knob = self.twist_knob = Knob(cell, app.scale, turned, look.KNOB_GREEN, 40, self.knob_pressed)
            knob.pack()
            if name == "twist":  # (middle-click: back to 0 itself, also from past the knob's end)
                knob.bind("<ButtonPress-2>", lambda e: self.twist_to_zero())
            r = ttk.Frame(cell)
            r.pack()
            self._number_box(r, name, lo, hi, word)
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
        self.squeeze = tk.BooleanVar(value=True)  # (BMC tester 11-03: off = the point only says where it turns)
        self.squeeze_tick = ttk.Checkbutton(box, text=tr("stir.squeeze"), variable=self.squeeze,
                                            command=self.on_squeeze)
        self.squeeze_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.squeeze_tick, tr("stir.squeeze_tip"))
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

    def _number_box(self, parent, name, lo, hi, word):
        var = tk.StringVar()
        e = ttk.Entry(parent, textvariable=var, width=7, justify="right")
        e.pack(side="left", padx=(4, 1))
        self.boxes[name] = {"var": var, "entry": e, "shown": "", "lo": lo, "hi": hi}
        e.bind("<Return>", lambda ev: self.on_box(name))
        e.bind("<FocusOut>", lambda ev: self.on_box(name))
        Scrub(self.app, [(e, var, lambda: self.on_box(name))], (1, 10, 0.1), lo, hi, label=word, drag_box=True)

    # ---- what the selection holds

    def targets(self):
        return self.app.recolour_targets()

    def stirs(self):
        return [full_stir((clean_recolour(t.get("recolour")) or {}).get("stir")) for t in self.targets()]

    def sync(self):
        """Show the selected shapes' Stir (a held knob stays where the mouse put it, its box follows): a box where
        they differ is blank; boxes being typed in keep the typing."""
        app = self.app
        held = self.held_knob()
        tgts = self.targets()
        sts = self.stirs()
        self.what.config(text=tr("stir.nothing") if not tgts else
                         tr("stir.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("stir.n_shapes", n=len(tgts)))
        on = bool(tgts)
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
        squeezes = {s["squeeze"] for s in sts}
        self.squeeze.set(squeezes != {False})
        self.squeeze_tick.state(["alternate" if len(squeezes) > 1 else "!alternate",
                                 "!disabled" if on else "disabled"])
        many = any(len(app.colour_owns(t)) > 1 for t in tgts)
        row = self.picks["sets"]["row"]
        if many != bool(row.winfo_manager()):
            if many:
                row.pack(anchor="w", pady=(6, 0), before=self.hint)
            else:
                row.pack_forget()
        self.reset.state(["!disabled" if any(s != full_stir(None) for s in sts) else "disabled"])
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
        """Each selected shape's Stir key = value (centre0 / centre1: one side of the Centre point); one undo
        step (push False: part of the step taken before)."""
        def changed(old, sh):
            st = full_stir((old or {}).get("stir"))
            if key.startswith("centre"):
                c = list(st["centre"])
                c[int(key[-1])] = value
                st["centre"] = c
            else:
                st[key] = value
            return stirred_rc(old, st)
        return self.app.change_recolour(changed, name or tr("stir.undo"), push)

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

    def on_squeeze(self):
        self.wheel = None
        self.set_stir("squeeze", bool(self.squeeze.get()))
        self.sync()

    def on_reset(self):
        self.wheel = None
        self.app.change_recolour(lambda old, sh: stirred_rc(old, None) if old else old, tr("stir.undo_reset"))
        self.sync()

    def take_typing(self):
        for name, b in self.boxes.items():
            if b["var"].get() != b["shown"]:
                self.on_box(name)

    def close(self):
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
