"""The claw machine window (claw.py): changes the selected shapes' notes, shown live on the piano roll (the shared
part: tool_window.py)."""

import random
import tkinter as tk
from tkinter import ttk

from files.lang import plural, tr
from files.mathexpr import calc, fmt
from notes.claw import (CLAW_DEFAULTS, COUNTS, MAX_COUNT, MAX_PERIOD, MAX_SLICES, PERIODS, TRASHES, clean_claw,
                        clean_period)
from window import look
from window.tool_window import Knob, ToolWindow
from window.widgets import Scrub, Tooltip, bad, good, grid_shown, unit_word, wheel_ready, wheel_turns

MODES = [("time", tr("claw.by_time")), ("notes", tr("claw.by_notes")), ("keys", tr("claw.by_keys")),
         ("chords", tr("claw.by_chords")), ("random", tr("claw.random"))]
UNITS = {m: (f"claw.unit_{m}_one", f"claw.unit_{m}") for m in ("notes", "keys", "chords")}  # (keys: one, many)


def period_name(beats):
    return tr("claw.beat") if beats == 1 else tr("claw.n_beats", n=beats)


def trash_name(t):
    return tr("claw.none") if not t else f"{t[0]}/{t[1]}"


class ClawWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "claw", "claw_window", "claw_pos", CLAW_DEFAULTS

    @property
    def claw(self):
        return self.cfg

    @claw.setter
    def claw(self, value):
        self.cfg = value

    def clean(self, cfg):
        return clean_claw(cfg)

    def build(self, box):
        s = self.app.scale
        self.shown_mode = None  # (show)
        self.menus, self.boxes, self.vars, self.entries = {}, {}, {}, {}
        self.typed_rows, self.shown_typed = {}, {}  # (rows whose dropdown can turn into typed boxes: show)
        self.menu_row(box, 1, "mode", tr("claw.mode"), MODES, tr("claw.tip_mode"))
        box.columnconfigure(0, minsize=round(80 * s))

        time = self.boxes["time"] = self.mode_box(box)
        self.menu_row(time, 0, "period", tr("claw.period"), [(p, period_name(p)) for p in PERIODS],
                      tr("claw.tip_period"), self.period_box)
        self.menu_row(time, 1, "trash", tr("claw.trash_every"),
                      [(list(t), trash_name(t)) if t else (None, "-") for t in TRASHES + (None,)] +
                      [(None, trash_name(None))],
                      tr("claw.tip_trash"), self.trash_box)
        ttk.Label(time, text=tr("claw.time_dist")).grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
        dial = ttk.Frame(time)
        dial.grid(row=2, column=1, sticky="w", pady=4)
        self.knob = Knob(dial, s, self.on_knob, pressed=self.knob_pressed)
        self.knob.pack(side="left")
        var = self.vars["dist"] = tk.StringVar()  # (a box beside the knob, user)
        e = self.entries["dist"] = ttk.Entry(dial, textvariable=var, width=6, justify="center")
        e.pack(side="left", padx=(10, 0))
        e.bind("<Return>", lambda ev: (self.on_entry("dist"), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_entry("dist"))
        Scrub(self.app, [(e, var, lambda: self.on_entry("dist", False))], (1, 10, 0.1), -100, 100, drag_box=True)
        for w in (self.knob, e):
            Tooltip(w, tr("claw.tip_dist") + "\n" + tr("claw.tip_dist_box"))
        self.ticks = {}
        for r, key, text, tip in ((3, "stretch", tr("claw.stretch_to_compensate"), tr("claw.tip_stretch")),
                                  (4, "short", tr("claw.remove_short_notes"), tr("claw.tip_short"))):
            var = self.ticks[key] = tk.BooleanVar()
            b = ttk.Checkbutton(time, text=text, variable=var, command=lambda key=key: self.put(key,
                                                                                                self.ticks[key].get()))
            b.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4 if r == 3 else 0, 0))
            Tooltip(b, tip)

        count = self.mode_box(box)
        for mode in COUNTS:
            self.boxes[mode] = count
        self.units = []
        for r, key, label, tip in ((0, "keep", tr("claw.keep"), tr("claw.tip_keep")),
                                   (1, "skip", tr("claw.then_trash"), tr("claw.tip_skip"))):
            self.number_row(count, r, key, label, tip, (1, 10, 1), 0 if key == "skip" else 1, MAX_COUNT)
            u = ttk.Label(count, text="", foreground=look.HINT)
            u.grid(row=r, column=2, sticky="w", padx=(5, 0))
            self.units.append(u)
        ttk.Label(count, text=tr("claw.then_again"), foreground=look.HINT).grid(row=2, column=1, columnspan=2,
                                                                            sticky="w", pady=(2, 0))

        rand = self.boxes["random"] = self.mode_box(box)
        self.number_row(rand, 0, "pct", tr("claw.keep"), tr("claw.tip_pct"), (1, 10, 0.1), 0, 100)
        ttk.Label(rand, text=tr("unit.percent"), foreground=look.HINT).grid(row=0, column=2, sticky="w", padx=(5, 0))
        again = ttk.Button(rand, text=tr("claw.new_random"),
                           command=lambda: self.put("seed", random.randrange(1, 10 ** 9)))
        again.grid(row=1, column=1, columnspan=2, sticky="w", pady=(4, 0))
        Tooltip(again, tr("claw.tip_new_random"))
        cut = self.cut_box = ttk.Frame(box)  # (every mode but By time)
        cut.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        var = self.ticks["shorten"] = tk.BooleanVar()
        b = ttk.Checkbutton(cut, text=tr("claw.shorten_instead"), variable=var,
                            command=lambda: self.put("shorten", self.ticks["shorten"].get()))
        b.grid(row=0, column=0, columnspan=3, sticky="w")
        Tooltip(b, tr("claw.tip_shorten"))
        cut.columnconfigure(0, minsize=round(80 * s))
        self.number_row(cut, 1, "cut", tr("claw.to"), tr("claw.tip_cut"), (1, 10, 0.1), 1, 99)
        ttk.Label(cut, text=tr("claw.of_their_length"), foreground=look.HINT).grid(row=1, column=2, sticky="w",
                                                                                padx=(5, 0))
        self.cut_row = cut.grid_slaves(row=1)  # (only shown while "shorten" is ticked, user)
        self.update_idletasks()  # (as wide as the widest mode, so the window keeps its width)
        box.columnconfigure(1, minsize=max(f.winfo_reqwidth() for f in (*self.boxes.values(), cut)) - round(80 * s))

        row = ttk.Frame(box)
        row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("claw.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("claw.tip_reset"))
        ttk.Button(row, text=tr("claw.accept"), command=self.accept).pack(side="right")

    def mode_box(self, box):
        """The settings of one mode (only the picked mode's show)."""
        f = ttk.Frame(box)
        f.grid(row=2, column=0, columnspan=2, sticky="nw")
        f.columnconfigure(0, minsize=round(80 * self.app.scale))
        return f

    def number_row(self, box, r, key, label, tip, steps, lo, hi):
        lb = ttk.Label(box, text=label)
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=2)
        var = self.vars[key] = tk.StringVar()
        e = self.entries[key] = ttk.Entry(box, textvariable=var, width=7)
        e.grid(row=r, column=1, sticky="w", pady=2)
        e.bind("<Return>", lambda ev: (self.on_entry(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_entry(key))
        Scrub(self.app, [(e, var, lambda: self.on_entry(key, False))], steps, lo, hi, label=lb)
        e.bind("<FocusIn>", lambda ev: self.app.tips.show("numbers", parent=self, wait=True))  # (not behind it)
        Tooltip(e, tip)

    def on_entry(self, key, done=True):
        e, var = self.entries[key], self.vars[key]
        lo, hi = {"keep": (1, MAX_COUNT), "skip": (0, MAX_COUNT), "pct": (0, 100), "cut": (1, 99),
                  "dist": (-100, 100), "period": (1 / self.app.ppq, MAX_PERIOD)}[key]
        try:
            v = float(calc(var.get()))
            if not lo - 1e-9 <= v <= hi or key in ("keep", "skip") and v != int(v):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            e.good_text = fmt(self.claw[key])  # (back to what is in use: the knob may have moved since)
            bad(e)
            return
        good(e)
        v = int(v) if key in ("keep", "skip") else clean_period(v) if key == "period" else v
        if v != self.claw[key]:
            self.put(key, v, done)

    def menu_row(self, box, r, key, label, choices, tip, typed=None):
        """A dropdown row. typed(holder, label) (optional): makes the boxes the row turns into when "Custom…" is
        picked (or the value isn't one of the choices); they take the dropdown's place, so the window doesn't grow
        (user 2026-10-22)."""
        lb = ttk.Label(box, text=label)
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=2)
        holder = ttk.Frame(box)
        holder.grid(row=r, column=1, sticky="w", pady=2)
        b = ttk.Menubutton(holder, width=12)
        b.pack(side="left")
        b["menu"] = self.choice_menu(b, key, choices, typed)
        b._wheel = True  # (clicked: the wheel steps it, widgets.wheel_ready)
        b.bind("<ButtonPress-1>", lambda e: b.focus_set(), add="+")
        b.bind("<MouseWheel>", lambda e: (self.wheel_menu(key, 1 if e.delta < 0 else -1) if wheel_turns(b, e)
                                          else "break") if wheel_ready(b) else None)
        Tooltip(b, tip)
        self.menus[key] = (b, choices)
        if typed:
            self.typed_rows[key] = (b, typed(holder, lb))
            self.shown_typed[key] = False

    def choice_menu(self, parent, key, choices, typed):
        """The dropdown's list (+ "Custom…" for a row that can be typed)."""
        m = tk.Menu(parent, tearoff=0)
        for value, text in choices:
            if text == "-":
                m.add_separator()
            else:
                m.add_command(label=text, command=lambda v=value: self.pick(key, v))
        if typed:
            m.add_separator()
            m.add_command(label=tr("claw.custom"), command=lambda: self.go_typed(key))
        return m

    def back_button(self, f, key):
        """The small ▾ beside typed boxes: the list again (a choice picked = the dropdown back)."""
        b = ttk.Menubutton(f, width=0)
        b.pack(side="left", padx=(4, 0))
        b["menu"] = self.choice_menu(b, key, self.menus[key][1], None)
        Tooltip(b, tr("claw.tip_back_to_list"))

    def typed(self):
        return list(self.claw.get("typed") or [])

    def pick(self, key, value):
        """A choice picked from a list: the row is a dropdown (again)."""
        self.claw["typed"] = [k for k in self.typed() if k != key]
        self.put(key, value)

    def go_typed(self, key):
        """"Custom…": the row's dropdown turns into boxes holding the value in use (Trash every with none: 1/4)."""
        self.claw["typed"] = sorted(set(self.typed()) | {key})
        if key == "trash" and not self.claw["trash"]:
            self.put("trash", [1, 4])
        else:
            self.show()
            self.undo.mark()
        e = self.entries["period"] if key == "period" else self.trash_entries["k"]
        e.focus_set()
        e.select_range(0, "end")

    def period_box(self, holder, lb):
        """Period typed: [x] beats ▾. Its label drags it too (1/4 beat a step, Shift 1 beat, Ctrl 1 tick)."""
        f = ttk.Frame(holder)
        self.period_label = lb
        var = self.vars["period"] = tk.StringVar()
        e = self.entries["period"] = ttk.Entry(f, textvariable=var, width=6)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: (self.on_entry("period"), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_entry("period"))
        unit_word(ttk.Label(f, foreground=look.HINT), var, "unit.beats").pack(side="left", padx=(4, 0))
        self.back_button(f, "period")
        sc = self.period_scrub = Scrub(self.app, [(e, var, lambda: self.on_entry("period", False))],
                                       lambda: (0.25, 1, 1 / self.app.ppq), None, MAX_PERIOD,
                                       text=lambda v: f"{v:.9f}".rstrip("0").rstrip("."))
        # (steps from the period in use, not the box's rounded text: 1-tick steps stay whole ticks)
        sc.read = lambda text: self.claw["period"] if text == fmt(self.claw["period"]) else calc(text)
        for ev, fn in (("<ButtonPress-1>", sc.press), ("<B1-Motion>", sc.motion), ("<ButtonRelease-1>", sc.release)):
            lb.bind(ev, lambda e, fn=fn: fn(e) if self.shown_typed.get("period") else None)
        lb.bind("<Destroy>", lambda e: sc.gone(), add="+")
        Tooltip(e, tr("claw.tip_period_typed"))
        return f

    def trash_box(self, holder, lb):
        """Trash every typed: [k] / [n] ▾, each box typed or dragged on its own."""
        f = ttk.Frame(holder)
        self.trash_vars, self.trash_entries = {}, {}
        for which in ("k", "n"):
            if which == "n":
                ttk.Label(f, text="/").pack(side="left", padx=2)
            var = self.trash_vars[which] = tk.StringVar()
            e = self.trash_entries[which] = ttk.Entry(f, textvariable=var, width=4, justify="center")
            e.pack(side="left")
            e.bind("<Return>", lambda ev, w=which: (self.on_trash(w), "break")[1])
            e.bind("<FocusOut>", lambda ev, w=which: self.on_trash(w))
            Scrub(self.app, [(e, var, lambda w=which: self.on_trash(w, False))], (1, 10, 1), 1, MAX_SLICES,
                  drag_box=True)
            Tooltip(e, tr("claw.tip_trash_typed"))
        self.back_button(f, "trash")
        return f

    def on_trash(self, which, done=True):
        """A Trash every box typed / dragged: slice k of n (k never past n)."""
        e, var = self.trash_entries[which], self.trash_vars[which]
        k, n = self.claw["trash"] or [1, 4]
        try:
            v = float(calc(var.get()))
            if not 1 <= v <= MAX_SLICES or v != int(v):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            e.good_text = str(k if which == "k" else n)
            bad(e)
            return
        good(e)
        v = int(v)
        t = [min(v, n), n] if which == "k" else [min(k, v), v]
        if t != self.claw["trash"]:
            self.put("trash", t, done)
        else:
            self.show()  # (k typed past n: back to n)

    def wheel_menu(self, key, d):
        """The mouse wheel over a dropdown picks the next / previous choice (stops at the ends)."""
        values = [v for v, t in self.menus[key][1] if t != "-"]
        i = values.index(self.claw[key]) + d if self.claw[key] in values else 0
        if 0 <= i < len(values) and values[i] != self.claw[key]:
            self.put(key, values[i], False)  # (a run of wheel steps = one Ctrl+Z inside)
        return "break"

    def show(self):
        """The window shows self.claw. Only what differs is changed: each change makes the window lay itself out
        again, which made dragging a number slow."""
        c = self.claw

        def config(w, **opts):
            if any(str(w.cget(k)) != str(v) for k, v in opts.items()):
                w.config(**opts)

        for key, (b, choices) in self.menus.items():
            config(b, text=next((t for v, t in choices if v == c[key] and t != "-"), ""))
        for key, (b, f) in self.typed_rows.items():  # (typed: "Custom…" picked, or a value the list hasn't got)
            typed = key in self.typed() or not any(v == c[key] and t != "-" for v, t in self.menus[key][1])
            if typed != self.shown_typed[key]:
                self.shown_typed[key] = typed
                (b if typed else f).pack_forget()
                (f if typed else b).pack(side="left")
        t = c["trash"] or ["", ""]
        for which, v in zip(("k", "n"), t):
            if self.trash_vars[which].get() != str(v):
                self.trash_vars[which].set(str(v))
            config(self.trash_entries[which], style="TEntry")
        self.period_scrub.lo = 1 / self.app.ppq
        config(self.period_label, cursor="sb_h_double_arrow" if self.shown_typed["period"] else "")
        if not self.knob.drag and abs(self.knob.value - c["dist"]) > 1e-9:
            self.knob.set(c["dist"])
        for key, var in self.ticks.items():
            if var.get() != c[key]:
                var.set(c[key])
        for key, var in self.vars.items():
            if var.get() != fmt(c[key]):
                var.set(fmt(c[key]))
            config(self.entries[key], style="TEntry")
        if c["mode"] != self.shown_mode:
            self.shown_mode = c["mode"]
            for mode, f in self.boxes.items():
                if f is not self.boxes[c["mode"]]:
                    f.grid_remove()
            self.boxes[c["mode"]].grid()
            if c["mode"] == "time":
                self.cut_box.grid_remove()
            else:
                self.cut_box.grid()
        for u, key in zip(self.units, ("keep", "skip")):
            text = plural(c[key], *UNITS[c["mode"]]) if c["mode"] in UNITS else ""
            if u.cget("text") != text:
                u.config(text=text)
        config(self.entries["cut"], state="normal" if c["shorten"] else "disabled")
        for w in self.cut_row:
            grid_shown(w, c["shorten"])

    def on_knob(self, value, done):
        self.claw["dist"] = value
        self.vars["dist"].set(fmt(value))
        self.entries["dist"].config(style="TEntry")
        self.preview(done)
        self.undo.mark("dist")
        if done:
            self.undo.key = None


open_claw = ClawWindow.open
