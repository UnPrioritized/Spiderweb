"""The Strum window (strum.py): the selected shapes' chords strummed, shown live on the piano roll (the shared part:
tool_window.py). Laid out like the strum tool of a well-known piano roll: a Start panel (Time, Velocity; Preserve
end, Trigger ahead) and an End panel (Time), each with its own on / off box, then Chop chords and Alternate
direction. Every knob has a box under it showing its real value (times in ticks), where a value can be typed too,
even past where the knob goes. Ours on top (tester's wish, Colour cycle): Keys per step (two boxes, at the bottom of
the Start panel) and what happens at the shape's edges (Off / Cut / Wrap, under Alternate direction)."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.strum import EDGES, END_KNOB, KEYS_MAX, LIMITS, STRUM_DEFAULTS, TIME_KNOB, VEL_KNOB, clean_strum
from window import look
from window.tool_window import GREEN, ORANGE, Knob, ToolWindow
from window.widgets import Scrub, Tooltip, bad, good, read_number

TIMES = ("time", "end_time")  # kept in beats, shown in ticks
TENSIONS = ("tension", "vel_tension", "end_tension")
STRENGTH_OF = {"tension": "time", "vel_tension": "vel", "end_tension": "end_time"}
STEPS = {"time": (1, 10, 0.1), "end_time": (1, 10, 0.1), "vel": (1, 10, 0.1)}  # (others: TENSION_STEPS)
TENSION_STEPS = (1, 10, 0.1)


class StrumWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "strum", "strum_window", "strum_pos", STRUM_DEFAULTS

    def clean(self, cfg):
        return clean_strum(cfg)

    def build(self, box):
        self.knobs, self.vars, self.entries, self.ticks, self.checks, self.scrubs = {}, {}, {}, {}, {}, {}
        panels = {}
        for col, (key, rows) in enumerate((("start", (("time", "tension", tr("strum.time")),
                                                      ("vel", "vel_tension", tr("strum.velocity")))),
                                           ("end", (("end_time", "end_tension", tr("strum.time")),)))):
            light = self.check(box, key, tr(f"strum.{key}"))
            panel = panels[key] = ttk.Labelframe(box, labelwidget=light, padding=(8, 4, 8, 8))
            panel.grid(row=1, column=col, rowspan=2 if key == "start" else 1, sticky="new",
                       padx=(0, 10) if key == "start" else 0)
            for c, text in ((1, tr("strum.strength")), (2, tr("strum.tension"))):
                ttk.Label(panel, text=text, foreground=look.HINT, font="TkSmallCaptionFont").grid(row=0, column=c)
            for r, (strength, tension, label) in enumerate(rows, 1):
                ttk.Label(panel, text=label).grid(row=r, column=0, sticky="e", padx=(0, 6))
                self.dial(panel, r, 1, strength)
                self.dial(panel, r, 2, tension)
        for r, key in ((3, "preserve"), (4, "ahead")):
            self.check(panels["start"], key, tr(f"strum.{key}")).grid(row=r, column=0, columnspan=3, sticky="w",
                                                                      pady=(6 if r == 3 else 0, 0))
        self.build_keys(panels["start"])
        more = ttk.Frame(box)
        more.grid(row=2, column=1, sticky="nw", pady=(8, 0))
        for key in ("chop", "alternate"):
            self.check(more, key, tr(f"strum.{key}")).pack(anchor="w")
        edges = ttk.Frame(more)
        edges.pack(anchor="w", pady=(8, 0))
        self.edges_label = ttk.Label(edges, text=tr("strum.edges"))
        self.edges_label.pack(anchor="w")
        names = [tr(f"strum.edges_{v}") for v in EDGES]
        self.edges_box = ttk.Combobox(edges, state="readonly", values=names, width=max(map(len, names)))
        self.edges_box.pack(anchor="w", pady=(2, 0))
        self.edges_box.bind("<<ComboboxSelected>>",
                            lambda e: self.put("edges", EDGES[max(self.edges_box.current(), 0)]))
        for w in (self.edges_label, self.edges_box):
            Tooltip(w, tr("strum.tip_edges"))

        row = ttk.Frame(box)
        row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("strum.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("strum.tip_reset"))
        ttk.Button(row, text=tr("strum.accept"), command=self.accept).pack(side="right")

    def build_keys(self, panel):
        """Keys per step: two whole-number boxes (keys .. keys_to; the same number twice = groups of that many)."""
        row = ttk.Frame(panel)
        row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.keys_label = ttk.Label(row, text=tr("strum.keys"))
        self.keys_label.pack(side="left", padx=(0, 4))
        for key in ("keys", "keys_to"):
            if key == "keys_to":
                self.keys_mid = ttk.Label(row, text=tr("strum.keys_to"))
                self.keys_mid.pack(side="left", padx=4)
            var = self.vars[key] = tk.StringVar()
            e = self.entries[key] = ttk.Entry(row, textvariable=var, width=5, justify="center")
            e.pack(side="left")
            e.bind("<Return>", lambda ev, k=key: (self.on_keys(k), "break")[1])
            e.bind("<FocusOut>", lambda ev, k=key: self.on_keys(k))
            self.scrubs[key] = Scrub(self.app, [(e, var, lambda k=key: self.on_keys(k, False))], (1, 10, 1), 1,
                                     KEYS_MAX, drag_box=True)
            Tooltip(e, tr(f"strum.tip_{key}"))
        Tooltip(self.keys_label, tr("strum.tip_keys"))
        Tooltip(self.keys_mid, tr("strum.tip_keys_to"))

    def on_keys(self, key, done=True):
        """A Keys box typed / scrubbed. The first box moves the second along while they're the same number (one
        number = groups of that many), so typing 4 gives groups of 4, not a 4 .. 1 range."""
        e, var = self.entries[key], self.vars[key]
        v = read_number(var.get(), 1, KEYS_MAX)
        if v is None or v != int(v):
            e.good_text = str(self.cfg[key])
            bad(e)
            return
        good(e)
        v = int(v)
        if v == self.cfg[key]:
            return
        if key == "keys" and self.cfg["keys_to"] == self.cfg["keys"]:
            self.cfg["keys_to"] = v
        self.put(key, v, done)

    def check(self, parent, key, text):
        var = self.ticks[key] = tk.BooleanVar()
        b = self.checks[key] = ttk.Checkbutton(parent, text=text, variable=var,
                                               command=lambda: self.put(key, self.ticks[key].get()))
        Tooltip(b, tr(f"strum.tip_{key}"))
        return b

    def dial(self, parent, r, c, key):
        """A knob with its number box under it."""
        cell = ttk.Frame(parent)
        cell.grid(row=r, column=c, padx=4, pady=2)
        k = self.knobs[key] = Knob(cell, self.app.scale, lambda v, done: self.on_knob(key, v, done),
                                   color=GREEN if key in TENSIONS else ORANGE, size=40, pressed=self.knob_pressed)
        k.pack()
        var = self.vars[key] = tk.StringVar()
        e = self.entries[key] = ttk.Entry(cell, textvariable=var, width=6, justify="center")
        e.pack(pady=(2, 0))
        e.bind("<Return>", lambda ev: (self.on_entry(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_entry(key))
        lo, hi = self.limits(key)
        self.scrubs[key] = Scrub(self.app, [(e, var, lambda: self.on_entry(key, False))],
                                 STEPS.get(key, TENSION_STEPS), lo, hi, drag_box=True)
        for w in (k, e):
            Tooltip(w, tr(f"strum.tip_{key}") + "\n" + tr("strum.tip_knob"))

    # the numbers: cfg holds times in beats; the boxes show ticks

    def shown(self, key):
        return self.cfg[key] * self.app.ppq if key in TIMES else self.cfg[key]

    def limits(self, key):
        lo, hi = LIMITS[key]
        return (lo * self.app.ppq, hi * self.app.ppq) if key in TIMES else (lo, hi)

    def knob_max(self, key):
        """How far a strength knob goes, in shown units (it turns along a curve: fine near 0)."""
        return {"time": TIME_KNOB * self.app.ppq, "end_time": END_KNOB * self.app.ppq, "vel": VEL_KNOB}[key]

    def knob_of(self, key):
        """Where a knob points for the value (-100 .. 100; a typed value past its end: all the way)."""
        v = self.shown(key)
        if key in TENSIONS:
            return v
        return math.copysign(min(100.0, 100 * math.sqrt(abs(v) / self.knob_max(key))), v) if v else 0.0

    def ppq_changed(self):
        """The boxes show ticks at the new PPQ (the times are kept in beats)."""
        for key in TIMES:
            self.scrubs[key].lo, self.scrubs[key].hi = self.limits(key)
        self.show()

    def put_shown(self, key, v):
        self.cfg[key] = v / self.app.ppq if key in TIMES else v

    def on_knob(self, key, k, done):
        v = k if key in TENSIONS else math.copysign(round(self.knob_max(key) * (k / 100) ** 2), k)
        self.put_shown(key, v + 0.0)
        self.vars[key].set(fmt(v))
        self.entries[key].config(style="TEntry")
        self.states()
        self.preview(done)
        self.undo.mark(key)
        if done:
            self.undo.key = None

    def on_entry(self, key, done=True):
        e, var = self.entries[key], self.vars[key]
        lo, hi = self.limits(key)
        v = read_number(var.get(), lo, hi)
        if v is None:
            e.good_text = fmt(self.shown(key))  # (back to what is in use: the knob may have moved since)
            bad(e)
            return
        good(e)
        if abs(v - self.shown(key)) > 1e-9:
            self.put_shown(key, v)
            self.put(key, self.cfg[key], done)

    def show(self):
        """The window shows self.cfg (only what differs is changed, so dragging a number stays quick)."""
        c = self.cfg
        for key, var in self.ticks.items():
            if var.get() != c[key]:
                var.set(c[key])
        for key, knob in self.knobs.items():
            k = self.knob_of(key)
            if not knob.drag and abs(knob.value - k) > 0.05:
                knob.set(k)
            text = fmt(self.shown(key))
            if self.vars[key].get() != text:
                self.vars[key].set(text)
            if str(self.entries[key].cget("style")) != "TEntry":
                self.entries[key].config(style="TEntry")
        for key in ("keys", "keys_to"):
            text = str(c[key])
            if self.vars[key].get() != text:
                self.vars[key].set(text)
            if str(self.entries[key].cget("style")) != "TEntry":
                self.entries[key].config(style="TEntry")
        at = EDGES.index(c["edges"]) if c["edges"] in EDGES else 0
        if self.edges_box.current() != at:
            self.edges_box.current(at)
        self.states()

    def states(self):
        """A panel switched off greys out; so does a tension while its strength is 0 (it would do nothing)."""
        c = self.cfg
        on = {"start": c["start"], "end": c["end"]}
        for key in self.knobs:
            panel = "end" if key.startswith("end") else "start"
            ok = on[panel] and (key not in TENSIONS or self.cfg[STRENGTH_OF[key]] != 0)
            self.knobs[key].on(ok)
            state = "normal" if ok else "disabled"
            if str(self.entries[key].cget("state")) != state:
                self.entries[key].config(state=state)
        for key in ("preserve", "ahead"):
            self.checks[key].state(["!disabled"] if c["start"] else ["disabled"])
        # Keys per step + Edges: greyed while nothing strums (like a tension at strength 0: they'd do nothing)
        moves = c["start"] and (c["time"] or c["vel"]) or c["end"] and c["end_time"]
        state = "normal" if moves else "disabled"
        for key in ("keys", "keys_to"):
            if str(self.entries[key].cget("state")) != state:
                self.entries[key].config(state=state)
        for w in (self.keys_label, self.keys_mid):
            w.state(["!disabled"] if moves else ["disabled"])
        # (Edges: only a Start time moves notes past them; End only shortens notes, Velocity moves none, hunt)
        starts = bool(c["start"] and c["time"])
        self.edges_label.state(["!disabled"] if starts else ["disabled"])
        self.edges_box.state(["!disabled", "readonly"] if starts else ["disabled"])


open_strum = StrumWindow.open
