"""Turn by… (piano roll right-click → Turn ▸, user 2026-11-24: a typed angle such as 80° or 22.5°, which dragging
just outside a box's corner can't hit exactly): the selected drawn shapes turned by the angle as seen on screen
(like that corner drag: the zoom decides how many beats a key becomes), together round their middle. Live on the
piano roll while the number changes; OK keeps it as one undo step, Cancel / Esc / X puts it back. The angle typed
last is offered again (until Spiderweb closes). Only shapes with a box turn this way (drawn shapes, text, pasted
notes, pictures), and not with Even steps on: lines can only turn 90°."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr, trn
from files.mathexpr import fmt
from notes.custom import even_on
from notes.fx import copied as fx_copied, with_turn as fx_turned
from notes.sliced import steps_kept
from roll.roll_custom import tidy
from window import look
from window.widgets import Scrub, Tooltip, bad, enter_presses, good, read_number, remember_place

MOST = 360.0
STEPS = (1, 15, 0.1)  # (Up / Down, Shift, Ctrl)


def can_turn(app):
    """Every selected shape has a box that turns freely (the corner drag's shapes; not Even steps)."""
    return bool(app.sels) and app.roll.sx is not None and all(
        app.shapes[i]["kind"] == "custom" and not even_on(app.shapes[i]) for i in app.sels)


class TurnBy(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, roll = app, app.roll
        self.title(tr("turn_by.title"))
        self.transient(app)
        self.resizable(False, False)
        remember_place(self, "turn_by")
        # the shapes as they were: every change works from them again
        self.idx = sorted(app.sels)
        self.was = [(i, [list(q) for q in app.shapes[i]["pts"]], fx_copied(app.shapes[i].get("fx")),
                     steps_kept(app.shapes[i])) for i in self.idx]
        self.fx_was = {i: app.shapes[i].get("fx") for i in self.idx}
        shapes = [app.shapes[i] for i in self.idx]
        corners = [roll.to_su(*q) for sh in shapes for q in roll.custom_corners(sh)]
        xs, ys = [x for x, _ in corners], [y for _, y in corners]
        self.mid = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        self.limit = roll.limits(shapes)  # (turned past an edge: pushed back inside, like the corner drag)
        self.done = False

        s = getattr(app, "scale", 1.0)
        top = ttk.Frame(self, padding=(int(10 * s), int(8 * s)))
        top.pack(fill="both", expand=True)
        row = ttk.Frame(top)
        row.pack(fill="x")
        lab = ttk.Label(row, text=tr("turn_by.angle"))
        lab.pack(side="left")
        start = getattr(app, "turn_by_last", 0.0)
        self.var = tk.StringVar(value=fmt(start))
        self.box = ttk.Entry(row, textvariable=self.var, width=8, justify="center")
        self.box.pack(side="left", padx=(int(6 * s), 0))
        self.box.value = start
        good(self.box)
        Tooltip(lab, tr("turn_by.angle_tip"))
        Tooltip(self.box, tr("turn_by.angle_tip"))
        Scrub(app, [(self.box, self.var, self.typed)], STEPS, -MOST, MOST, label=lab, no_undo=True)
        self.box.bind("<Return>", lambda e: (self.typed(), self.ok(), "break")[2])
        self.box.bind("<KeyRelease>", lambda e: self.typed(quiet=True), add="+")
        n = len(self.idx)
        ttk.Label(top, text=trn("turn_by.what", n, n=n), foreground=look.HINT, justify="left",
                  wraplength=int(260 * s)).pack(fill="x", pady=(int(6 * s), 0))
        btns = ttk.Frame(top)
        btns.pack(fill="x", pady=(int(8 * s), 0))
        ttk.Button(btns, text=tr("turn_by.cancel"), command=self.cancel).pack(side="right")
        self.ok_button = ttk.Button(btns, text=tr("turn_by.ok"), command=self.ok)
        self.ok_button.pack(side="right", padx=(0, int(6 * s)))
        enter_presses(self, self.ok_button)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<F1>", lambda e: (app.open_help("custom_edit"), "break")[1])
        self.grab_set()  # (the selection can't change under it)
        self.box.focus_set()
        self.box.select_range(0, "end")
        if start:
            self.show(start)

    # ------------------------------------------------------------ the angle

    def typed(self, quiet=False):
        v = read_number(self.var.get(), -MOST, MOST)
        if v is None:
            if not quiet:
                self.var.set(fmt(self.box.value))  # (a wrong number: back to the last good one)
                good(self.box)
            elif self.var.get().strip() not in ("", "-", "+", "."):
                bad(self.box, typing=True)
            return
        good(self.box)
        if v != self.box.value:
            self.box.value = v
            self.show(v)

    def turned(self, deg):
        """Each shape's frame (and its note tool pages' result) turned deg degrees, clockwise on screen, round the
        shapes' middle, from how they were (roll_custom.turn_custom's maths, the corner drag's)."""
        app, roll = self.app, self.app.roll
        a = math.radians(deg)
        c, s = math.cos(-a), math.sin(-a)  # (screen y points down, pitch up)
        cx, cy = self.mid
        for i, pts, fx0, steps in self.was:
            sh = app.shapes[i]
            su = [roll.to_su(*q) for q in pts]
            sh["pts"] = tidy([roll.from_su(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c)
                              for x, y in su])
            if fx0 or steps:  # (its note tool pages' result is turned too, fx.py)
                fx = fx_turned(fx_copied(fx0), deg, roll.sy / roll.sx, steps)
                if fx:
                    sh["fx"] = fx
                else:
                    sh.pop("fx", None)
        shapes = [app.shapes[i] for i in self.idx]
        db, dp = roll.push_in(roll.reach(shapes), self.limit)
        if db or dp:
            for sh in shapes:
                sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]

    def put_back(self):
        for i, pts, _, _ in self.was:
            sh = self.app.shapes[i]
            sh["pts"] = [list(q) for q in pts]
            if self.fx_was[i] is None:
                sh.pop("fx", None)
            else:
                sh["fx"] = self.fx_was[i]

    def show(self, deg):
        self.put_back()
        if deg % 360:
            self.turned(deg)
        self.app.shapes_changed()

    # ------------------------------------------------------------ done

    def ok(self):
        if self.done:
            return
        self.typed()
        deg = self.box.value
        self.done = True
        self.put_back()
        if deg % 360:
            self.app.turn_by_last = deg
            self.app.push_undo(name=tr("pianoroll.turn"))
            self.turned(deg)
        self.close()

    def cancel(self):
        if self.done:
            return
        self.done = True
        self.put_back()
        self.close()

    def close(self):
        app = self.app
        self.grab_release()
        self.destroy()
        app.shapes_changed()
        app.sync_panel()
