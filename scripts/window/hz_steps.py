"""The Arpeggio box's step editor (window/hz_knobs.py; shown while its Pattern is Steps): the run's own steps
(hzbass.STEPS) as a row of columns, one lane at a time (Note, Octave, Loudness, Length, picked over the grid), like a
synth's arpeggiator steps. Click or drag across the columns to set them, middle-click = that step's value back to
its start; the Steps box beside it = how many there are."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc
from notes.hzbass import STEP, STEPS
from window.synth_look import DIM, EDGE, ENTRY, GRID, MID, PIC, mix
from window.widgets import Scrub, Tooltip

LANES = ("note", "octave", "level", "length")
CELL = 20  # a step's width
GRID_H = 96  # the lanes' height
TOP = 16  # the lane names over it
LEFT = 26  # the row names left of it
TIE = 14  # the Length lane's Tie row at its top


def new_step(i):
    """Step i as it is when the steps grow: notes 1 to 4 round again, the rest as STEP starts."""
    return {"note": 1 + i % 4, "octave": 0, "level": 1.0, "length": 1.0}


class StepEditor:
    """changed(steps, done): the steps set (done = let go / one click: one undo step)."""

    def __init__(self, parent, s, app, colour, changed):
        self.s, self.colour, self.changed = s, colour, changed
        self.steps, self.on, self.lane = [new_step(i) for i in range(8)], True, "note"
        self.drag = None  # (while the mouse paints: the last column it was over)
        self.frame = ttk.Frame(parent, style="Synth.Box.TFrame")
        left = ttk.Frame(self.frame, style="Synth.Box.TFrame")
        left.pack(side="left", anchor="n", padx=(0, round(8 * s)))
        ttk.Label(left, text=tr("hz.synth_steps_count"), style="Synth.Box.TLabel").pack()
        self.count_var = tk.StringVar(value=str(len(self.steps)))
        e = self.count_box = ttk.Entry(left, textvariable=self.count_var, width=4, justify="center", style=ENTRY)
        e.pack(pady=(2, 0))
        e.bind("<Return>", lambda ev: (self.on_count(), self.frame.focus_set(), "break")[2])
        e.bind("<FocusOut>", lambda ev: self.on_count())
        Scrub(app, [(e, self.count_var, self.on_count)], (1, 4, 1), 1, STEPS, drag_box=True)
        Tooltip(e, tr("hz.synth_tip_steps_count"))
        w, h = round((LEFT + CELL * STEPS + 2) * s), round((TOP + GRID_H + 2) * s)
        c = self.canvas = tk.Canvas(self.frame, width=w, height=h, background=PIC, highlightthickness=1,
                                    highlightbackground=EDGE, cursor="hand2")
        c.pack(side="left")
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.move)
        c.bind("<ButtonRelease-1>", self.release)
        c.bind("<ButtonPress-2>", self.reset)
        Tooltip(c, tr("hz.synth_tip_steps"))

    # ------------------------------------------------------------ where things are

    def col_at(self, x):
        """The step column under x (may be outside 0..STEPS - 1)."""
        return int((x / self.s - LEFT) // CELL)

    def value_at(self, y):
        """The picked lane's value at height y on the grid."""
        u = min(1.0, max(0.0, (y / self.s - TOP) / GRID_H))  # (0 = the top)
        if self.lane == "note":
            return 8 - min(8, int(u * 9))
        if self.lane == "octave":
            return 2 - min(4, int(u * 5))
        if self.lane == "level":
            return round(1.0 - u, 2)
        if u * GRID_H < TIE:
            return "tie"
        lo = STEP["length"][0]
        return round(max(lo, 1.0 - (u * GRID_H - TIE) / (GRID_H - TIE)), 2)

    def put(self, steps, col, value):
        """The picked lane's value set on step col."""
        step = dict(steps[col])
        if self.lane == "length":
            step.pop("tie", None)
            if value == "tie":
                step["tie"] = True
            else:
                step["length"] = value
        else:
            step[self.lane] = value
        steps[col] = step

    # ------------------------------------------------------------ the mouse

    def press(self, e):
        y = e.y / self.s
        if y < TOP:  # (a lane's name: that lane shown)
            for lane, (x0, x1) in self.lane_spots.items():
                if x0 <= e.x <= x1 and lane != self.lane:
                    self.lane = lane
                    self.draw()
            return
        col = self.col_at(e.x)
        if 0 <= col < len(self.steps):
            self.drag = col
            self.paint(col, col, e.y, False)

    def move(self, e):
        if self.drag is None:
            return
        col = min(len(self.steps) - 1, max(0, self.col_at(e.x)))
        self.paint(self.drag, col, e.y, False)
        self.drag = col

    def release(self, e):
        if self.drag is not None:
            self.drag = None
            self.changed(self.steps, True)

    def paint(self, a, b, y, done):
        """Every step from column a to b set to the value at y."""
        steps, value = list(self.steps), self.value_at(y)
        for col in range(min(a, b), max(a, b) + 1):
            self.put(steps, col, value)
        if steps != self.steps:
            self.steps = steps
            self.draw()
            self.changed(steps, done)

    def reset(self, e):
        """Middle-click: the step's value in the picked lane back to its start."""
        col = self.col_at(e.x)
        if self.drag is None and 0 <= col < len(self.steps) and e.y / self.s >= TOP:
            steps = list(self.steps)
            self.put(steps, col, STEP[self.lane][2])
            if steps != self.steps:
                self.steps = steps
                self.draw()
                self.changed(steps, True)

    def on_count(self):
        """The Steps box typed / stepped: steps added (notes 1 to 4 round again) or taken off the end."""
        try:
            n = int(round(float(calc(self.count_var.get()))))
            if not 1 <= n <= STEPS:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            n = len(self.steps)
        self.count_var.set(str(n))
        if n != len(self.steps):
            self.steps = self.steps[:n] + [new_step(i) for i in range(len(self.steps), n)]
            self.draw()
            self.changed(self.steps, True)

    # ------------------------------------------------------------ showing them

    def show(self, steps, on):
        """The steps as the sound has them (on: the arpeggio on; off = greyed)."""
        if self.drag is not None or (steps == self.steps and on == self.on and self.canvas.find_all()):
            return
        self.steps, self.on = [dict(s) for s in steps], on
        if self.count_box.focus_get() is not self.count_box:
            self.count_var.set(str(len(steps)))
        self.draw()

    def draw(self):
        c, s = self.canvas, self.s
        c.delete("all")
        colour = self.colour if self.on else MID
        font = ("Segoe UI", 7)
        self.lane_spots, x = {}, LEFT * s
        for lane in LANES:  # (the lane names over the grid: the picked one in the box's colour)
            t = c.create_text(x, 2 * s, text=tr(f"hz.synth_steps_{lane}"), anchor="nw", font=("Segoe UI", 8),
                              fill=self.colour if lane == self.lane else DIM)
            x0, _, x1, _ = c.bbox(t)
            self.lane_spots[lane] = (x0 - 3 * s, x1 + 3 * s)
            x = x1 + 10 * s
        top, h = TOP * s, GRID_H * s
        rows = {"note": 9, "octave": 5}.get(self.lane)
        for i in range(STEPS):  # (columns past the last step: dark; a line every 4 steps)
            x0 = (LEFT + CELL * i) * s
            if i >= len(self.steps):
                c.create_rectangle(x0, top, x0 + CELL * s, top + h, fill=GRID, outline="")
            elif i % 4 == 0:
                c.create_line(x0, top, x0, top + h, fill=MID)
        if rows:  # (row lines and names: 8 .. 1 and a rest / +2 .. -2)
            for r in range(rows):
                y = top + h * r / rows
                if r:
                    c.create_line(LEFT * s, y, (LEFT + CELL * len(self.steps)) * s, y, fill=GRID)
                name = (str(8 - r) if r < 8 else tr("hz.synth_steps_rest")) if rows == 9 else f"{2 - r:+d}"
                c.create_text((LEFT - 3) * s, y + h / rows / 2, text=name.replace("+0", "0"), anchor="e", font=font,
                              fill=DIM)
        elif self.lane == "length":
            c.create_text((LEFT - 3) * s, top + TIE * s / 2, text=tr("hz.synth_steps_tie"), anchor="e", font=font,
                          fill=DIM)
            c.create_line(LEFT * s, top + TIE * s, (LEFT + CELL * len(self.steps)) * s, top + TIE * s, fill=MID,
                          dash=(2, 2))
        else:
            c.create_text((LEFT - 3) * s, top + 4 * s, text="100", anchor="e", font=font, fill=DIM)
        tied = False
        for i, st in enumerate(self.steps):
            x0, x1 = (LEFT + CELL * i + 2) * s, (LEFT + CELL * (i + 1) - 2) * s
            fill = colour if not tied else mix(colour, PIC, 0.6)  # (held on by the step before: faint)
            if self.lane == "note":
                r = 8 - st["note"]
                y0 = top + h * r / 9
                c.create_rectangle(x0, y0 + 1, x1, y0 + h / 9 - 1, fill=fill if st["note"] else MID, outline="")
            elif self.lane == "octave":
                r = 2 - st["octave"]
                y0 = top + h * r / 5
                c.create_rectangle(x0, y0 + 1, x1, y0 + h / 5 - 1, fill=fill, outline="")
            elif self.lane == "level":
                c.create_rectangle(x0, top + h * (1 - st["level"]), x1, top + h, fill=fill, outline="")
            else:
                band = TIE * s
                if st.get("tie"):
                    c.create_rectangle(x0, top + 1, x1 + 4 * s, top + band - 1, fill=fill, outline="")
                    c.create_rectangle(x0, top + band, x1, top + h, fill=fill, outline="")
                else:
                    y = top + band + (h - band) * (1 - st["length"])
                    c.create_rectangle(x0, y, x1, top + h, fill=fill, outline="")
            tied = bool(st.get("tie"))
