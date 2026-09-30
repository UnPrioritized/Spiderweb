"""The claw machine window (claw.py): changes the selected shapes' notes, shown live on the piano roll. Accept keeps
the change (one undo step), X / Esc puts the notes back, Reset sets everything back to "does nothing". It keeps the
piano roll to itself while it's open (only Space, to listen, still works there)."""

import json
import math
import random
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.claw import CLAW_DEFAULTS, COUNTS, MAX_COUNT, PERIODS, TRASHES, clean_claw
from window.widgets import LocalUndo, Scrub, Tooltip

MODES = [("time", tr("claw.by_time")), ("notes", tr("claw.by_notes")), ("keys", tr("claw.by_keys")),
         ("chords", tr("claw.by_chords")), ("random", tr("claw.random"))]
UNITS = {"notes": tr("claw.unit_notes"), "keys": tr("claw.unit_keys"), "chords": tr("claw.unit_chords")}
ORANGE = "#f5a623"


def period_name(beats):
    return tr("claw.beat") if beats == 1 else tr("claw.n_beats", n=beats)


def trash_name(t):
    return tr("claw.none") if not t else f"{t[0]}/{t[1]}"


def open_claw(app):
    if not app.sels:
        return
    if app.claw_window:
        app.claw_window.lift()
    else:
        app.claw_window = ClawWindow(app)
    app.claw_window.focus_set()


class Knob(tk.Canvas):
    """A round dial from -100 to 100 (0 = straight up). Drag up / down (Shift = fine), the mouse wheel or the arrow
    keys turn it; a double-click puts it back to 0. changed(value, done): done = the end of one turn."""

    TURN = 150  # degrees each way

    def __init__(self, parent, scale, changed):
        self.size = size = round(44 * scale)
        super().__init__(parent, width=size, height=size, highlightthickness=0, takefocus=True,
                         background=ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
        self.value, self.changed, self.drag = 0.0, changed, None
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", lambda e: self.release())
        self.bind("<Double-Button-1>", lambda e: self.turn_to(0, True))
        self.bind("<MouseWheel>", lambda e: self.turn_to(self.value + (5 if e.delta > 0 else -5), True))
        for key, d in (("Up", 1), ("Right", 1), ("Down", -1), ("Left", -1)):
            self.bind(f"<{key}>", lambda e, d=d: self.turn_to(self.value + d * (1 if e.state & 1 else 5), True))
        self.bind("<FocusIn>", lambda e: self.draw())
        self.bind("<FocusOut>", lambda e: self.draw())
        self.draw()

    def set(self, value):
        self.value = value
        self.draw()

    def draw(self):
        self.delete("all")
        s, m = self.size, max(3, self.size // 9)
        ring = "#888" if self.focus_get() is self else "#bbb"
        self.create_oval(m, m, s - m, s - m, outline=ring, width=max(2, m // 2))
        if self.value:
            self.create_arc(m, m, s - m, s - m, start=90, extent=-self.value / 100 * self.TURN, style="arc",
                            outline=ORANGE, width=max(2, m // 2))
        c, r = s / 2, s / 2 - m * 1.8
        a = math.radians(90 - self.value / 100 * self.TURN)
        self.create_oval(c - r, c - r, c + r, c + r, fill="#555", outline="")
        self.create_line(c, c, c + r * math.cos(a), c - r * math.sin(a), fill="white", width=2)

    def press(self, e):
        self.focus_set()
        self.drag = (e.y, self.value)

    def move(self, e):
        if self.drag:
            y, v = self.drag
            self.turn_to(v + (y - e.y) * (0.2 if e.state & 1 else 1))

    def release(self):
        if self.drag:
            self.drag = None
            self.changed(self.value, True)

    def turn_to(self, value, done=False):
        value = round(max(-100.0, min(100.0, value)), 1)
        if value != self.value:
            self.set(value)
            self.changed(value, done)
        elif done:
            self.changed(value, True)


class ClawWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("claw.claw_machine"))
        self.transient(app)
        self.resizable(False, False)
        if app.claw_pos:
            self.geometry(app.claw_pos)
        self.targets = sorted(app.sels)
        self.saved = json.dumps(app.shapes)  # (for the undo step)
        self.before = {i: app.shapes[i].get("claw") for i in self.targets}  # (put back by X / Esc)
        shown = next((c for c in self.before.values() if c), None)
        self.claw = dict(CLAW_DEFAULTS, **json.loads(json.dumps(shown or {})))
        s = app.scale

        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        n = len(self.targets)
        ttk.Label(box, text=tr("claw.shape", shape_label=app.shape_label(app.shapes[self.targets[0]]))
                  if n == 1 else tr("claw.n_shapes", n=n), foreground="#777").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
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
        self.knob = Knob(dial, s, self.on_knob)
        self.knob.pack(side="left")
        self.knob_text = ttk.Label(dial, text="", width=5, foreground="#777")
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
            u = ttk.Label(count, text="", foreground="#777")
            u.grid(row=r, column=2, sticky="w", padx=(5, 0))
            self.units.append(u)
        ttk.Label(count, text=tr("claw.then_again"), foreground="#777").grid(row=2, column=1, columnspan=2,
                                                                            sticky="w", pady=(2, 0))

        rand = self.boxes["random"] = self.mode_box(box)
        self.number_row(rand, 0, "pct", tr("claw.keep"), tr("claw.tip_pct"), (1, 10, 0.1), 0, 100)
        ttk.Label(rand, text=tr("unit.percent"), foreground="#777").grid(row=0, column=2, sticky="w", padx=(5, 0))
        again = ttk.Button(rand, text=tr("claw.new_random"),
                           command=lambda: self.put("seed", random.randrange(1, 10 ** 9)))
        again.grid(row=1, column=1, columnspan=2, sticky="w", pady=(4, 0))
        Tooltip(again, tr("claw.tip_new_random"))
        self.update_idletasks()  # (as wide as the widest mode, so the window keeps its width)
        box.columnconfigure(1, minsize=max(f.winfo_reqwidth() for f in self.boxes.values()) - round(80 * s))

        row = ttk.Frame(box)
        row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("claw.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("claw.tip_reset"))
        ttk.Button(row, text=tr("claw.accept"), command=self.accept).pack(side="right")

        self.undo = LocalUndo(self, lambda: json.dumps(self.claw, sort_keys=True), self.put_state)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.bind("<F1>", lambda e: "break")
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.accept())
        self.bind("<Configure>", self.remember, add="+")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.show()
        self.grab_set()

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
        lo, hi = {"keep": (1, MAX_COUNT), "skip": (0, MAX_COUNT), "pct": (0, 100)}[key]
        try:
            v = float(calc(var.get()))
            if not lo <= v <= hi or key != "pct" and v != int(v):
                raise ValueError
        except (ValueError, ZeroDivisionError):
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        v = v if key == "pct" else int(v)
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
        Tooltip(b, tip)
        self.menus[key] = (b, choices)

    def show(self):
        """The window shows self.claw."""
        c = self.claw
        for key, (b, choices) in self.menus.items():
            b.config(text=next((t for v, t in choices if v == c[key] and t != "-"), ""))
        self.knob.set(c["dist"])
        self.knob_text.config(text=f"{c['dist']:g}")
        for key, var in self.ticks.items():
            var.set(c[key])
        for key, var in self.vars.items():
            var.set(fmt(c[key]))
            self.entries[key].config(style="TEntry")
        for mode, f in self.boxes.items():
            if mode != c["mode"]:
                f.grid_remove()
        self.boxes[c["mode"]].grid()
        for u in self.units:
            u.config(text=UNITS.get(c["mode"], ""))

    def put(self, key, value, done=True):
        """A setting changed: show it on the piano roll."""
        self.claw[key] = value
        self.show()
        self.preview()
        self.undo.mark(None if done else key)

    def on_knob(self, value, done):
        self.claw["dist"] = value
        self.knob_text.config(text=f"{value:g}")
        self.preview()
        self.undo.mark("dist")
        if done:
            self.undo.key = None

    def put_state(self, state):
        self.claw = json.loads(state)
        self.show()
        self.preview()

    def preview(self):
        cl = clean_claw(self.claw)
        for i in self.targets:
            sh = self.app.shapes[i]
            if cl:
                sh["claw"] = dict(cl)
            else:
                sh.pop("claw", None)
        self.app.shapes_changed()

    def reset(self):
        self.claw = dict(CLAW_DEFAULTS)
        self.show()
        self.preview()
        self.undo.mark()

    def accept(self):
        app = self.app
        now = {i: app.shapes[i].get("claw") for i in self.targets}
        if now != self.before:
            app.push_undo(self.saved, tr("claw.claw_machine"))
            app.sync_panel()
        self.close()

    def cancel(self):
        app = self.app
        for i, c in self.before.items():
            app.shapes[i].pop("claw", None)
            if c:
                app.shapes[i]["claw"] = c
        app.shapes_changed()
        self.close()

    def remember(self, e):
        if e.widget is self:
            self.app.claw_pos = f"+{self.winfo_x()}+{self.winfo_y()}"

    def close(self):
        self.grab_release()
        self.app.claw_window = None
        self.destroy()
        self.app.roll.focus_set()
