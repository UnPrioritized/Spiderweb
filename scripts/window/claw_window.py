"""The claw machine window (claw.py): changes the selected shapes' notes, shown live on the piano roll (the shared
part: tool_window.py)."""

import random
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.claw import CLAW_DEFAULTS, COUNTS, MAX_COUNT, PERIODS, TRASHES, clean_claw
from window import look
from window.tool_window import Knob, ToolWindow
from window.widgets import Scrub, Tooltip, bad, good, grid_shown

MODES = [("time", tr("claw.by_time")), ("notes", tr("claw.by_notes")), ("keys", tr("claw.by_keys")),
         ("chords", tr("claw.by_chords")), ("random", tr("claw.random"))]
UNITS = {"notes": tr("claw.unit_notes"), "keys": tr("claw.unit_keys"), "chords": tr("claw.unit_chords")}


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
        self.menu_row(box, 1, "mode", tr("claw.mode"), MODES, tr("claw.tip_mode"))
        box.columnconfigure(0, minsize=round(80 * s))

        time = self.boxes["time"] = self.mode_box(box)
        self.menu_row(time, 0, "period", tr("claw.period"), [(p, period_name(p)) for p in PERIODS],
                      tr("claw.tip_period"))
        self.menu_row(time, 1, "trash", tr("claw.trash_every"),
                      [(list(t), trash_name(t)) if t else (None, "-") for t in TRASHES + (None,)] +
                      [(None, trash_name(None))],
                      tr("claw.tip_trash"))
        ttk.Label(time, text=tr("claw.time_dist")).grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
        dial = ttk.Frame(time)
        dial.grid(row=2, column=1, sticky="w", pady=4)
        self.knob = Knob(dial, s, self.on_knob, pressed=self.knob_pressed)
        self.knob.pack(side="left")
        self.knob_text = ttk.Label(dial, text="", width=5, foreground=look.HINT)
        self.knob_text.pack(side="left", padx=(6, 0))
        Tooltip(self.knob, tr("claw.tip_dist"))
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
        lo, hi = {"keep": (1, MAX_COUNT), "skip": (0, MAX_COUNT), "pct": (0, 100), "cut": (1, 99)}[key]
        try:
            v = float(calc(var.get()))
            if not lo <= v <= hi or key in ("keep", "skip") and v != int(v):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        good(e)
        v = int(v) if key in ("keep", "skip") else v
        if v != self.claw[key]:
            self.put(key, v, done)

    def menu_row(self, box, r, key, label, choices, tip):
        ttk.Label(box, text=label).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=2)
        b = ttk.Menubutton(box, width=12)
        b.grid(row=r, column=1, sticky="w", pady=2)
        m = tk.Menu(b, tearoff=0)
        for value, text in choices:
            if text == "-":
                m.add_separator()
            else:
                m.add_command(label=text, command=lambda v=value: self.put(key, v))
        b["menu"] = m
        b.bind("<MouseWheel>", lambda e: self.wheel_menu(key, 1 if e.delta < 0 else -1))
        Tooltip(b, tip)
        self.menus[key] = (b, choices)

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
        if self.knob.value != c["dist"]:
            self.knob.set(c["dist"])
        config(self.knob_text, text=f"{c['dist']:g}")
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
            for u in self.units:
                u.config(text=UNITS.get(c["mode"], ""))
        config(self.entries["cut"], state="normal" if c["shorten"] else "disabled")
        for w in self.cut_row:
            grid_shown(w, c["shorten"])

    def on_knob(self, value, done):
        self.claw["dist"] = value
        self.knob_text.config(text=f"{value:g}")
        self.preview(done)
        self.undo.mark("dist")
        if done:
            self.undo.key = None


open_claw = ClawWindow.open
