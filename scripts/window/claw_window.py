"""The claw machine window (claw.py): changes the selected shapes' notes, shown live on the piano roll. Accept keeps
the change (one undo step), X / Esc puts the notes back, Reset sets everything back to "does nothing". The main
window can be used while it's open: this window follows the selection, and before anything else changes there, the
claw being tried out is kept as its own undo step (settle)."""

import json
import math
import random
import time
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
    """A round dial from -100 to 100 (0 = straight up, all the way = straight down). Drag up / down (Shift = fine),
    the mouse wheel or the arrow keys turn it; it sticks at 0 for a moment on the way past; the right mouse button
    points it at the mouse; a middle-click puts it back to 0. changed(value, done): done = the end of one turn."""

    TURN = 180  # degrees each way
    STICK = 10  # pixels of dragging that stay at 0

    def __init__(self, parent, scale, changed):
        self.size = size = round(44 * scale)
        super().__init__(parent, width=size, height=size, highlightthickness=0, takefocus=True,
                         background=ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
        self.value, self.changed, self.drag = 0.0, changed, None
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", lambda e: self.release())
        self.bind("<ButtonPress-2>", lambda e: self.turn_to(0, True))
        self.bind("<ButtonPress-3>", self.point)
        self.bind("<B3-Motion>", self.point)
        self.bind("<ButtonRelease-3>", lambda e: self.changed(self.value, True))
        self.bind("<MouseWheel>", lambda e: self.step(5 if e.delta > 0 else -5))
        for key, d in (("Up", 1), ("Right", 1), ("Down", -1), ("Left", -1)):
            self.bind(f"<{key}>", lambda e, d=d: self.step(d * (1 if e.state & 1 else 5)))
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

    def step(self, d):
        v = self.value + d
        self.turn_to(0 if v * self.value < 0 else v, True)  # (stops at 0 on the way past)

    def point(self, e):
        """Right mouse button: the dial points at the mouse."""
        self.focus_set()
        c = self.size / 2
        if (e.x - c) ** 2 + (e.y - c) ** 2 > 4:  # (not right on the middle: no direction there)
            self.turn_to(math.degrees(math.atan2(e.x - c, c - e.y)) / self.TURN * 100)

    def press(self, e):
        self.focus_set()
        self.drag = (e.y, self.value + math.copysign(self.STICK, self.value) if self.value else 0.0)

    def move(self, e):
        if self.drag:  # (the mouse moves a "raw" value that has STICK extra on each side of 0)
            y, r = self.drag
            r = max(-100.0 - self.STICK, min(100.0 + self.STICK, r + (y - e.y) * (0.2 if e.state & 1 else 1)))
            self.drag = (e.y, r)
            self.turn_to(0 if abs(r) <= self.STICK else r - math.copysign(self.STICK, r))

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
        self.title(tr("claw.window_title"))
        self.transient(app)
        self.resizable(False, False)
        if app.claw_pos:
            self.geometry(app.claw_pos)
        self.claw = dict(CLAW_DEFAULTS)
        self.shown_mode = None  # (show)
        self.late, self.took = None, 0.0  # (preview)
        s = app.scale

        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text="", foreground="#777")
        self.what.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
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
        cut = self.cut_box = ttk.Frame(box)  # (every mode but By time)
        cut.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        var = self.ticks["shorten"] = tk.BooleanVar()
        b = ttk.Checkbutton(cut, text=tr("claw.shorten_instead"), variable=var,
                            command=lambda: self.put("shorten", self.ticks["shorten"].get()))
        b.grid(row=0, column=0, columnspan=3, sticky="w")
        Tooltip(b, tr("claw.tip_shorten"))
        cut.columnconfigure(0, minsize=round(80 * s))
        self.number_row(cut, 1, "cut", tr("claw.to"), tr("claw.tip_cut"), (1, 10, 0.1), 1, 99)
        ttk.Label(cut, text=tr("claw.of_their_length"), foreground="#777").grid(row=1, column=2, sticky="w",
                                                                                padx=(5, 0))
        self.update_idletasks()  # (as wide as the widest mode, so the window keeps its width)
        box.columnconfigure(1, minsize=max(f.winfo_reqwidth() for f in (*self.boxes.values(), cut)) - round(80 * s))

        row = ttk.Frame(box)
        row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("claw.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("claw.tip_reset"))
        ttk.Button(row, text=tr("claw.accept"), command=self.accept).pack(side="right")

        self.undo = LocalUndo(self, lambda: json.dumps(self.claw, sort_keys=True), self.put_state)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.bind("<F1>", lambda e: (app.open_help("claw"), "break")[1])
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.accept())
        self.bind("<Configure>", self.remember, add="+")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.retarget()
        self.after_idle(lambda: app.tips.show("claw", parent=self))

    def claws(self):
        return {i: self.app.shapes[i].get("claw") for i in self.targets if i < len(self.app.shapes)}

    def retarget(self):
        """Work on the selected shapes, showing their claw (the first one's that has one)."""
        app = self.app
        self.targets = sorted(app.sels)
        self.saved = json.dumps(app.shapes)  # (for the undo step)
        self.before = self.now = self.claws()  # (before: put back by X / Esc; now: as this window last left them)
        shown = next((c for c in self.before.values() if c), None)
        self.claw = dict(CLAW_DEFAULTS, **json.loads(json.dumps(shown or {})))
        n = len(self.targets)
        self.what.config(text=tr("claw.nothing") if not n else tr("claw.n_shapes", n=n) if n > 1 else
                         tr("claw.shape", shape_label=app.shape_label(app.shapes[self.targets[0]])))
        self.show()
        self.undo.reset()

    def sync(self):
        """The main window changed the selection or the shapes."""
        if sorted(self.app.sels) != self.targets:
            self.settle()
        elif self.claws() == self.now:
            return
        self.retarget()

    def settle(self):
        """Something else is about to change in the main window: the claw tried so far is kept (its own undo step),
        and from now on X / Esc only puts back what changes after this."""
        self.catch_up()
        if self.now != self.before and self.claws() == self.now:
            saved = self.saved
            self.saved, self.before = json.dumps(self.app.shapes), self.now
            self.app.add_undo_step(saved, tr("claw.claw_machine"))

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
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
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

    def put(self, key, value, done=True):
        """A setting changed: show it on the piano roll."""
        self.claw[key] = value
        self.show()
        self.preview(done)
        self.undo.mark(None if done else key)

    def on_knob(self, value, done):
        self.claw["dist"] = value
        self.knob_text.config(text=f"{value:g}")
        self.preview(done)
        self.undo.mark("dist")
        if done:
            self.undo.key = None

    def put_state(self, state):
        self.claw = json.loads(state)
        self.show()
        self.preview()

    def preview(self, now=True):
        """The piano roll shows the claw. While a number is dragged / the dial turned (not now) and that's slow
        (lots of notes), only the window follows the mouse: the notes catch up when the mouse rests."""
        if self.late:
            self.after_cancel(self.late)
            self.late = None
        if not now and self.took > 0.15:
            self.late = self.after(250, self.preview)
            return
        started = time.perf_counter()
        cl = clean_claw(self.claw)
        for i in self.targets:
            sh = self.app.shapes[i]
            if cl:
                sh["claw"] = dict(cl)
            else:
                sh.pop("claw", None)
        self.now = self.claws()
        self.app.shapes_changed(now=True)
        self.app.update_idletasks()  # (the piano roll redrawn now, so the time counts it)
        self.took = time.perf_counter() - started

    def catch_up(self):
        """A preview left for later (preview): now."""
        if self.late:
            self.preview()

    def reset(self):
        self.claw = dict(CLAW_DEFAULTS)
        self.show()
        self.preview()
        self.undo.mark()

    def accept(self):
        self.settle()
        self.close()

    def cancel(self):
        app = self.app
        if self.late:
            self.after_cancel(self.late)
            self.late = None
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
        if self.late:
            self.after_cancel(self.late)
        self.app.claw_window = None
        self.destroy()
        self.app.roll.focus_set()
