"""The Gate trim window (trim.py): each key row's runs of touching notes cut down to a share of their length, shown
live on the piano roll (the shared part: tool_window.py). One number (Keep [n] % of each row, with a slider) and
which end stays."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.trim import TRIM_DEFAULTS, clean_trim
from window import look
from window.tool_window import ToolWindow
from window.widgets import Scrub, Tooltip, bad, good

SLIDER_PX = 220


class TrimWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "trim", "trim_window", "trim_pos", TRIM_DEFAULTS

    def clean(self, cfg):
        return clean_trim(cfg)

    def build(self, box):
        row = ttk.Frame(box)
        row.grid(row=1, column=0, columnspan=2, sticky="w")
        label = ttk.Label(row, text=tr("trim.keep"))
        label.pack(side="left")
        self.var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.var, width=6, justify="center")
        self.entry.pack(side="left", padx=4)
        self.entry.bind("<Return>", lambda e: (self.on_entry(), "break")[1])
        self.entry.bind("<FocusOut>", lambda e: self.on_entry())
        ttk.Label(row, text=tr("trim.of_each_row")).pack(side="left")
        self.scrub = Scrub(self.app, [(self.entry, self.var, lambda: self.on_entry(False))], (1, 10, 0.1), 0, 100,
                           label=label, drag_box=True)
        for w in (label, self.entry):
            Tooltip(w, tr("trim.tip_keep"))
        self.slider_var = tk.DoubleVar(value=100.0)
        self.slider = ttk.Scale(box, from_=0, to=100, variable=self.slider_var,
                                length=round(SLIDER_PX * self.app.scale), command=lambda v: self.on_slider())
        self.slider.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.slider.bind("<ButtonRelease-1>", lambda e: self.on_slider(True), add="+")
        Tooltip(self.slider, tr("trim.tip_keep"))
        self.sliding = False

        side = ttk.Frame(box)
        side.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(side, text=tr("trim.count_from")).pack(anchor="w")
        self.side = tk.StringVar(value="left")
        for value in ("left", "right"):
            b = ttk.Radiobutton(side, text=tr(f"trim.{value}"), variable=self.side, value=value,
                                command=lambda: self.put("side", self.side.get()))
            b.pack(anchor="w", padx=(12, 0))
            Tooltip(b, tr(f"trim.tip_{value}"))
        ttk.Label(box, text=tr("trim.hint"), foreground=look.HINT, wraplength=round(SLIDER_PX * self.app.scale),
                  justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(box)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(buttons, text=tr("trim.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("trim.tip_reset"))
        ttk.Button(buttons, text=tr("trim.accept"), command=self.accept).pack(side="right")

    def on_slider(self, done=False):
        """The slider dragged: whole percents (the box takes finer ones)."""
        if self.sliding:
            return
        v = float(round(self.slider_var.get()))
        if v != self.cfg["keep"]:
            self.put("keep", v, done)
        elif done:
            self.put("keep", v, True)

    def on_entry(self, done=True):
        try:
            v = float(calc(self.var.get()))
            if not 0 <= v <= 100:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            self.entry.good_text = fmt(self.cfg["keep"])
            bad(self.entry)
            return
        good(self.entry)
        if abs(v - self.cfg["keep"]) > 1e-9:
            self.put("keep", v, done)

    def show(self):
        """The window shows self.cfg (only what differs is changed, so dragging stays quick)."""
        c = self.cfg
        text = fmt(c["keep"])
        if self.var.get() != text:
            self.var.set(text)
        if abs(self.slider_var.get() - c["keep"]) > 1e-9:
            self.sliding = True  # (setting it calls its command)
            self.slider_var.set(c["keep"])
            self.sliding = False
        if self.side.get() != c["side"]:
            self.side.set(c["side"])


open_trim = TrimWindow.open
