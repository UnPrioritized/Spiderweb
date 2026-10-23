"""The Gate trim window (trim.py): each key row's runs of touching notes cut down to a share of their length, or to
a number of ticks, shown live on the piano roll (the shared part: tool_window.py). One number (Keep [n] [% / ticks
of each row], a slider for the %) and which end stays."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.trim import BY, MAX_LENGTH, TRIM_DEFAULTS, clean_trim, length_ticks, longest_run
from window import look
from window.tool_window import ToolWindow
from window.widgets import Scrub, Tooltip, bad, good

SLIDER_PX = 220


def plain_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


class Hold:
    """The slider while its handle is held, as a knob would be (ToolWindow.knob_pressed / undo_key): Ctrl+Z while
    held puts back how it was at the press, and the slider follows the mouse no more until let go."""

    def __init__(self):
        self.down = self.dead = False

    def held(self):
        return self.down

    def call_off(self):
        self.dead = True


class TrimWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "trim", "trim_window", "trim_pos", TRIM_DEFAULTS

    def clean(self, cfg):
        return clean_trim(cfg)

    def build(self, box):
        self.typed = None  # (value, text typed for it): the box keeps showing it while that value is in use
        row = ttk.Frame(box)
        row.grid(row=1, column=0, columnspan=2, sticky="w")
        label = ttk.Label(row, text=tr("trim.keep"))
        label.pack(side="left")
        self.var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.var, width=8, justify="center")
        self.entry.pack(side="left", padx=4)
        self.entry.bind("<Return>", lambda e: (self.on_entry(enter=True), "break")[1])
        self.entry.bind("<FocusOut>", lambda e: self.on_entry())
        self.unit = ttk.Combobox(row, state="readonly", values=[tr("trim.of_each_row"), tr("trim.ticks_of_each_row")])
        self.unit.pack(side="left")
        self.unit.bind("<<ComboboxSelected>>", lambda e: self.on_unit())
        Tooltip(self.unit, tr("trim.tip_unit"))
        self.scrub = Scrub(self.app, [(self.entry, self.var, lambda: self.on_entry(False))], self.steps, 0, 100,
                           label=label, drag_box=True)
        self.scrub.read = lambda text: self.value() if text == self.shown_text() else calc(text)
        for w in (label, self.entry):
            Tooltip(w, tr("trim.tip_keep"))
        self.slider_var = tk.DoubleVar(value=100.0)
        self.slider = ttk.Scale(box, from_=0, to=100, variable=self.slider_var,
                                length=round(SLIDER_PX * self.app.scale), command=lambda v: self.on_slider())
        self.slider.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for b in (1, 2, 3):  # (Tk's own: the middle / right button jumps there and drags too)
            self.slider.bind(f"<ButtonPress-{b}>", self.slider_press, add="+")
            self.slider.bind(f"<ButtonRelease-{b}>", lambda e: self.slider_release(), add="+")
        Tooltip(self.slider, tr("trim.tip_slider"))
        self.hold = Hold()
        self.pressed_at = None  # where the slider was when pressed
        self.showing = False  # (show() setting the slider: its command fires too)

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

    # ---- the number: keep % (up to 3 decimals) or ticks (whole, kept in beats)

    def ticks(self):
        return self.cfg["by"] == "ticks"

    def steps(self):
        return (10, 100, 1) if self.ticks() else (1, 10, 0.1)

    def value(self):
        """The number in use, as the box shows it (% or ticks)."""
        return length_ticks(self.cfg["length"], self.app.ppq) if self.ticks() else self.cfg["keep"]

    def exact(self):
        """Is the number in use exactly what the box shows? (Not after a PPQ change put a length between ticks.)"""
        return not self.ticks() or abs(self.cfg["length"] * self.app.ppq - self.value()) < 1e-9

    def shown_text(self):
        v = self.value()
        typed = self.typed
        return typed[2] if typed and typed[:2] == (self.cfg["by"], v) else fmt(v)

    def on_entry(self, done=True, enter=False):
        text = self.var.get().strip()
        if text == self.shown_text() and (self.exact() or not enter):  # (left unchanged: the exact value stays,
            # not the box's shortened one; Enter on a length a PPQ change left between ticks makes it whole)
            good(self.entry)
            return
        try:
            v = float(calc(text))
            if self.ticks():
                if not 1 <= v <= MAX_LENGTH * self.app.ppq or v != int(v):
                    raise ValueError
                v = int(v)
            elif not 0 <= v <= 100:
                raise ValueError
            else:
                v = round(v, 3)
        except (ValueError, ZeroDivisionError, OverflowError):
            self.entry.good_text = self.shown_text()
            bad(self.entry)
            return
        good(self.entry)
        if done and fmt(v) != text and not plain_number(text):
            self.typed = (self.cfg["by"], v, text)  # (e.g. "100/3": the box keeps showing it; 12.3456 shows 12.346)
        if v != self.value() or not self.exact():
            if self.ticks():
                self.put("length", v / self.app.ppq, done)
            else:
                self.put("keep", v, done)
        else:
            self.show()

    def on_unit(self):
        """% of each row / ticks of each row picked."""
        by = BY[self.unit.current()]
        if by == self.cfg["by"]:
            return
        if by == "ticks":  # (starts at the longest row: nothing cut until a number is set, like 100 %)
            self.cfg["length"] = max(1, self.longest()) / self.app.ppq
        if self.at is None:  # ("+": no page yet, as the number changes nothing)
            self.cfg["by"] = by
            self.show()
            return
        self.undo.key = None
        self.put("by", by)

    def longest(self):
        """The longest row of touching notes in the selected shapes, in ticks, before the page shown (or all their
        pages, on "+")."""
        most = 0
        for i in self.targets:
            sh = self.app.shapes[i]
            fx = sh.get("fx") or []
            k = len(fx) if self.at is None else self.at[i]
            before = {key: v for key, v in sh.items() if key != "fx"}
            if fx[:k]:
                before["fx"] = fx[:k]
            most = max(most, longest_run(self.app.notes_of(before)))
        return most

    # ---- the slider (whole percents; greyed while counting ticks)

    def slider_press(self, e):
        if self.ticks() or self.hold.down:  # (a second button while one is held: the first press counts)
            return
        self.knob_pressed(self.hold)  # (a step of its own; Ctrl+Z while held: back to the press)
        self.hold.down, self.hold.dead = True, False
        self.pressed_at = self.slider_var.get()

    def slider_release(self):
        held, self.hold.down = self.hold.down, False
        if not held:
            return
        dead, self.hold.dead = self.hold.dead, False
        if dead or abs(self.slider_var.get() - self.pressed_at) < 1e-9:  # (a click that didn't move it: nothing)
            self.show()
            if not dead and self.late:
                self.catch_up()
            return
        self.put("keep", self.slider_now(), True)

    def slider_now(self):
        """The slider's whole percent (halves up, like the trim's ticks)."""
        return float(math.floor(self.slider_var.get() + 0.5))

    def on_slider(self):
        if self.showing or self.ticks():
            return
        if self.hold.down:
            if self.hold.dead:  # (Ctrl+Z while held: it stays where it was put back)
                self.show()
            elif self.slider_now() != self.cfg["keep"]:
                self.put("keep", self.slider_now(), False)
            return
        v = self.slider_now()  # (moved without the left button: a jump, the keys: a step of its own)
        if v != self.cfg["keep"]:
            self.undo.key = None
            self.put("keep", v, True)

    def show(self):
        """The window shows self.cfg (only what differs is changed, so dragging stays quick)."""
        c = self.cfg
        text = self.shown_text()
        if self.var.get() != text:
            self.var.set(text)
        if self.unit.current() != BY.index(c["by"]):
            self.unit.current(BY.index(c["by"]))
        self.scrub.lo, self.scrub.hi = (1, None) if self.ticks() else (0, 100)
        if abs(self.slider_var.get() - c["keep"]) > 1e-9:
            self.showing = True  # (setting it calls its command)
            self.slider_var.set(c["keep"])
            self.showing = False
        self.slider.state(["disabled"] if self.ticks() else ["!disabled"])
        if self.side.get() != c["side"]:
            self.side.set(c["side"])


open_trim = TrimWindow.open
