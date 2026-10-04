"""The spam gate Range window (the side panel's Range… button next to the gate; gaterange.py). Everything about the
Range is set here, not in the panel (user: room for more options later): on / off, the first and second gate (From
= the shape's spam gate, To = where the graph reaches the top), across time or keys (low to high or top to bottom),
Fit to the shape's edges, and the graph: across the shape from the first gate to the second. Drag points, click to add one, right-click
one to delete it; presets. The pale steps behind the line = the whole-tick gates the notes really get. Beside it:
how many notes get each gate (counted from the picked shapes' real notes; red = a gate no note gets).

Opening it puts the Range on (the button is how it's put on). Changes show on the piano roll at once; OK keeps them
as one undo step, Cancel / Esc puts everything back. Ctrl+Z / Ctrl+Y step through the changes made in the window."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import SPAM_FILLS, range_gates
from notes.gaterange import DIRS, STRAIGHT, clean_range, gate_steps, steps_of
from window import big_ask
from window.panel_funnel import GATE_STEPS
from window.widgets import LocalUndo, Scrub, Tooltip


def _power(k):
    return [[i / 8, (i / 8) ** k] for i in range(9)]


PRESETS = [("range_window.straight", STRAIGHT), ("range_window.short_longer", _power(2)),
           ("range_window.long_longer", _power(0.5)), ("range_window.hill", [[0, 0], [0.5, 1], [1, 0]]),
           ("range_window.valley", [[0, 1], [0.5, 0], [1, 1]])]
U_SNAP, Y_SNAP = 1 / 40, 1 / 20  # dragging moves points in these steps (Shift = free)
OFF_COLOR = "#b0b0b0"  # the graph while the Range is off
BAR, BAR_HOT, NONE = "#4a90e2", "#1f5fb0", "#e0503c"  # notes per gate: bars, the one pointed at, gates with none
BAR_PX = 3  # bars at least this wide: more gates than fit share a bar


class RangeGraph(tk.Toplevel):
    def __init__(self, app, tgts=None):
        super().__init__(app)
        self.app = app
        self.title(tr("range_window.title"))
        self.transient(app)
        self.resizable(False, False)
        s = self.s = app.scale
        self.w, self.h, self.cw = int(440 * s), int(220 * s), int(300 * s)
        self.ml, self.mr, self.mt, self.mb = int(56 * s), int(12 * s), int(10 * s), int(22 * s)
        self.tgts = tgts or app.custom_targets()
        self.before = json.dumps(app.shapes)
        self.old = [json.loads(json.dumps({k: t.get(k) for k in ("gate", "range", "range_kept")})) for t in self.tgts]
        # each shape's range, kept while it's switched off so switching on brings it back (sh["range_kept"] while
        # it's off; new: 4 times the gate)
        self.memo = [clean_range(json.loads(json.dumps(t.get("range") or t.get("range_kept") or
                                                       {"to": t["gate"] * 4, "graph": STRAIGHT, "dir": "time"})))
                     for t in self.tgts]
        for i, t in enumerate(self.tgts[1:], 1):  # (shapes without a Range on take the one shown: the first's)
            if not t.get("range"):
                self.memo[i] = json.loads(json.dumps(self.memo[0]))
        self.closed = False
        self.drag, self.hover, self.bar_hover, self.counts = None, None, None, None
        self.held, self.drag_from = False, None  # (a drag's graph that would make too many notes: not used yet)
        # notes already said yes to: what the shapes make now (asked again only past that and the usual limit)
        self.big_ok = sum(app.note_count(t) or 0 for t in self.tgts if t is not app.custom_defaults)
        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        self.on_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text=tr("range_window.on"), variable=self.on_var,
                        command=lambda: self.change(None)).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        self.gate_vars, self.gate_boxes = {}, {}
        for key in ("from", "to"):
            lb = ttk.Label(row, text=tr(f"range_window.{key}"))
            lb.pack(side="left", padx=(0 if key == "from" else 8, 0))
            var = self.gate_vars[key] = tk.StringVar()
            e = self.gate_boxes[key] = ttk.Entry(row, textvariable=var, width=7)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda ev, k=key: (self.on_gate(k), "break")[1])  # (not OK: the window stays)
            e.bind("<FocusOut>", lambda ev, k=key: self.on_gate(k))
            Scrub(app, [(e, var, lambda k=key: self.on_gate(k))], GATE_STEPS, 1, 10 ** 7, label=lb)
            for w in (lb, e):
                Tooltip(w, tr(f"range_window.{key}_tip"))
        ttk.Label(row, text=tr("range_window.ticks_unit"), foreground="#777").pack(side="left")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(4, 0))
        ttk.Label(row, text=tr("range_window.across")).pack(side="left")
        self.dir_box = ttk.Combobox(row, values=[tr(f"range_window.{d}") for d in DIRS], state="readonly", width=24)
        self.dir_box.pack(side="left", padx=4)
        self.dir_box.bind("<<ComboboxSelected>>", lambda e: self.change("dir", DIRS[self.dir_box.current()]))
        Tooltip(self.dir_box, tr("range_window.dir_tip"))
        self.fit_var = tk.BooleanVar()
        self.fit_check = ttk.Checkbutton(box, text=tr("range_window.fit"), variable=self.fit_var,
                                         command=lambda: self.change("fit", self.fit_var.get()))
        self.fit_check.pack(anchor="w", pady=(4, 0))
        Tooltip(self.fit_check, tr("range_window.fit_tip"))
        self.info = ttk.Label(box, text="")
        self.info.pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=4)
        cv = self.canvas = tk.Canvas(row, width=self.w, height=self.h, bg="#ffffff", highlightthickness=1,
                                     highlightbackground="#a0a0a0", cursor="crosshair")
        cv.pack(side="left")
        ch = self.chart = tk.Canvas(row, width=self.cw, height=self.h, bg="#ffffff", highlightthickness=1,
                                    highlightbackground="#a0a0a0")
        ch.pack(side="left", padx=(6, 0))
        ch.bind("<Motion>", lambda e: self.on_bar_hover(e.x))
        ch.bind("<Leave>", lambda e: self.on_bar_hover(None))
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.preset_btns = []
        for key, pts in PRESETS:
            b = ttk.Button(row, text=tr(key), command=lambda pts=pts: self.set_points(pts))
            b.pack(side="left", padx=(0, 4))
            self.preset_btns.append(b)
        ttk.Label(box, text=tr("range_window.hint"), foreground="#777", font=("Segoe UI", 8), justify="left",
                  wraplength=self.w + self.cw).pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(6, 0))
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<ButtonPress-3>", self.delete_at)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        if not self.fits():  # (on at once, unless that's too many notes for the user)
            self.on_var.set(False)
        self.store()
        self.hist = LocalUndo(self, self.state, self.put_state)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 120}+{app.winfo_rooty() + 120}")
        self.grab_set()  # (the shapes it works on stay picked while it's open)
        self.focus_set()

    # ---- the shapes

    @property
    def pts(self):
        return self.memo[0]["graph"]

    def gates(self):
        """The first and second gate in ticks (the first target's)."""
        return (max(1, math.floor(self.tgts[0]["gate"] * self.app.ppq + 0.5)),
                max(1, math.floor(self.memo[0]["to"] * self.app.ppq + 0.5)))

    def state(self):
        return json.dumps([self.on_var.get(), self.memo, [t["gate"] for t in self.tgts]])

    def put_state(self, state):
        self.drag, self.held = None, False
        on, self.memo, gates = json.loads(state)
        self.on_var.set(on)
        for t, g in zip(self.tgts, gates):
            t["gate"] = g
        self.store()

    def store(self):
        """Put the window's settings on the shapes and show them."""
        on = self.on_var.get()
        for t, m in zip(self.tgts, self.memo):
            t.pop("range", None)
            t.pop("range_kept", None)
            t["range" if on else "range_kept"] = json.loads(json.dumps(m))
        self.app.shapes_changed()
        self.count = sum(self.app.note_count(t) or 0 for t in self.tgts if t is not self.app.custom_defaults)
        self.counts = self.spread()
        self.show_boxes()
        self.draw()
        self.draw_chart()

    def spread(self):
        """How many notes get each gate from From to To (the first shape's; others' gates outside left out), or None
        while the Range is off. Counted without Strum / Claw (they move notes, not their gates)."""
        if not self.on_var.get():
            return None
        a, b = self.gates()
        n = abs(b - a) + 1
        out = np.zeros(n, np.int64)
        for t in self.tgts:
            if t is self.app.custom_defaults or not t.get("range") or t.get("fill") not in SPAM_FILLS:
                continue
            bare = {k: v for k, v in t.items() if k not in ("strum", "claw")}
            notes = self.app.notes_of(bare)
            if not len(notes):
                continue
            j = (range_gates(bare, notes, self.app.ppq) - a) * (1 if b >= a else -1)
            out += np.bincount(j[(j >= 0) & (j < n)], minlength=n)
        return out

    def fits(self, trial=None, ask=True):
        """The picked shapes (trial: these instead, as they'd be) make few enough notes, or the user says go on
        (the same question as a typed gate; asked again only for more notes than they already said yes to)."""
        if trial is None:
            on = self.on_var.get()
            trial = [dict({k: v for k, v in t.items() if k != "range"}, **({"range": m} if on else {}))
                     for t, m in zip(self.tgts, self.memo) if t is not self.app.custom_defaults]
        total = sum(self.app.note_count(t) for t in trial)
        if total <= self.big_ok or not big_ask.trouble(self.app, "notes", total)[0]:
            return True
        if ask and big_ask.ask(self.app, "notes", total, parent=self):
            self.big_ok = total
            return True
        return False

    def change(self, key, value=None, mark=True):
        """key (to / dir / graph) set on every shape's range (None: only on / off changed). Too many notes: asked
        first (while a point is dragged: when it's let go, the graph alone moves till then)."""
        old = json.loads(json.dumps(self.memo))
        if key:
            for m in self.memo:
                m[key] = json.loads(json.dumps(value))
        if self.drag is not None and not self.fits(ask=False):
            self.held = True
            return self.draw()
        if self.drag is None and not self.fits():
            self.memo = old
            if key is None:
                self.on_var.set(not self.on_var.get())
            self.show_boxes()
            return self.draw()
        self.held = False
        self.store()
        if mark:
            self.hist.mark()

    def set_points(self, pts):
        self.change("graph", [list(p) for p in pts])

    def show_boxes(self):
        on = self.on_var.get()
        a, b = self.gates()
        for key, ticks in (("from", self.tgts[0]["gate"] * self.app.ppq), ("to", self.memo[0]["to"] * self.app.ppq)):
            self.gate_vars[key].set(fmt(round(ticks, 3)))
            self.gate_boxes[key].config(style="TEntry", state="normal" if on else "disabled")
        self.dir_box.current(DIRS.index(self.memo[0]["dir"]))
        self.dir_box.config(state="readonly" if on else "disabled")
        self.fit_var.set(self.memo[0]["fit"])
        self.fit_check.config(state="normal" if on else "disabled")
        for btn in self.preset_btns:
            btn.config(state="normal" if on else "disabled")
        self.info.config(text=tr("range_window.info", a=a, b=b) +
                         (tr("range_window.notes", n=self.count) if self.count else ""))

    def on_gate(self, key):
        """From (the spam gate itself) or To typed / stepped."""
        if self.closed or str(self.gate_boxes[key].cget("state")) == "disabled":
            return
        try:
            ticks = calc(self.gate_vars[key].get())
            if not 1 <= ticks <= 10 ** 7:
                raise ValueError
        except (ValueError, ZeroDivisionError):  # (not a number, or out of range: the box goes back, user)
            now = self.tgts[0]["gate"] if key == "from" else self.memo[0]["to"]
            return self.gate_vars[key].set(fmt(round(now * self.app.ppq, 3)))
        beats = ticks / self.app.ppq
        now = self.tgts[0]["gate"] if key == "from" else self.memo[0]["to"]
        if abs(beats - now) < 1e-12:
            return
        placed = [(t, m) for t, m in zip(self.tgts, self.memo) if t is not self.app.custom_defaults]
        if key == "from":
            trial = [dict(t, gate=beats, range=m) for t, m in placed]
        else:
            trial = [dict(t, range=dict(m, to=beats)) for t, m in placed]
        if not self.fits(trial):
            return self.show_boxes()
        if key == "from":
            for t in self.tgts:
                t["gate"] = beats
            self.store()
            self.hist.mark("from")
        else:
            self.change("to", beats, mark=False)
            self.hist.mark("to")

    def ok(self):
        for key in self.gate_boxes:  # (a number typed without Enter counts too)
            self.on_gate(key)
        self.closed = True
        if json.dumps(self.app.shapes) != self.before:
            self.app.add_undo_step(self.before, tr("range_window.step"))
        self.close()

    def cancel(self):
        self.closed = True
        for t, old in zip(self.tgts, self.old):
            t.update({k: v for k, v in old.items() if v is not None})
            for k in ("range", "range_kept"):
                if old[k] is None:
                    t.pop(k, None)
        self.app.shapes_changed()
        self.close()

    def close(self):
        self.grab_release()
        self.destroy()
        self.app.sync_custom()

    # ---- drawing

    def u2x(self, u):
        return self.ml + u * (self.w - self.ml - self.mr)

    def y2c(self, y):
        return self.mt + (1 - y) * (self.h - self.mt - self.mb)

    def x2u(self, x):
        return min(1.0, max(0.0, (x - self.ml) / (self.w - self.ml - self.mr)))

    def c2y(self, c):
        return min(1.0, max(0.0, 1 - (c - self.mt) / (self.h - self.mt - self.mb)))

    def draw(self):
        cv, s = self.canvas, self.s
        on = self.on_var.get()
        cv.delete("all")
        a, b = self.gates()
        x0, x1, top, bot = self.u2x(0), self.u2x(1), self.y2c(1), self.y2c(0)
        for j in range(1, 4):
            cv.create_line(self.u2x(j / 4), top, self.u2x(j / 4), bot, fill="#d3dff0")
        n = abs(b - a) + 1
        for k in range(5):  # gate labels at 0, 25 ... 100 % of the way
            y = k / 4
            g = a + (1 if b >= a else -1) * min(n - 1, int(y * n))
            cv.create_line(x0, self.y2c(y), x1, self.y2c(y), fill="#d3dff0")
            cv.create_text(x0 - 4 * s, self.y2c(y), text=tr("range_window.ticks", n=g), anchor="e", fill="#333",
                           font=("Segoe UI", 7))
        cv.create_rectangle(x0, top, x1, bot, outline="#808080")
        ends = {"time": ("left", "right"), "keys": ("low", "high"), "keys_down": ("high", "low")}[self.memo[0]["dir"]]
        cv.create_text(x0, bot + 4 * s, text=tr(f"range_window.{ends[0]}"), anchor="nw", fill="#333",
                       font=("Segoe UI", 7))
        cv.create_text(x1, bot + 4 * s, text=tr(f"range_window.{ends[1]}"), anchor="ne", fill="#333",
                       font=("Segoe UI", 7))
        # the whole-tick gates the notes get (pale steps)
        span = (b - a) or 1
        if on and len(steps_of(self.pts, a, b)[0]) <= 2000:
            line = []
            for u0, u1, g in gate_steps(self.pts, a, b):
                y = self.y2c((g - a) / span if b != a else 0)
                line += [self.u2x(u0), y, self.u2x(u1), y]
            if len(line) >= 4:
                cv.create_line(*line, fill="#f0b0b0", width=max(1, round(2 * s)))
        lw = max(1, round(1.5 * s))
        colour = "#d00000" if on else OFF_COLOR
        cv.create_line(*[c for u, y in self.pts for c in (self.u2x(u), self.y2c(y))], fill=colour, width=lw)
        r = 4 * s
        for i, (u, y) in enumerate(self.pts):
            x, yy = self.u2x(u), self.y2c(y)
            shape = cv.create_rectangle if i in (0, len(self.pts) - 1) else cv.create_oval
            shape(x - r, yy - r, x + r, yy + r, fill="#ffffff", outline=colour, width=lw)
        at = self.pts[self.drag] if self.drag is not None else self.hover
        if at is not None and on:
            g = a + (1 if b >= a else -1) * min(n - 1, int(float(at[1]) * n))
            cv.create_text(x1 - 4 * s, top + 4 * s, text=tr("range_window.at", at=fmt(round(at[0] * 100, 1)), n=g),
                           anchor="ne", fill="#0a50e0", font=("Segoe UI", 8))

    def chart_bins(self):
        """(gates per bar, notes in each bar, plot left, plot width)."""
        s = self.s
        left, width = int(40 * s), self.cw - int(40 * s) - int(8 * s)
        per = max(1, math.ceil(len(self.counts) * BAR_PX * s / width))
        bins = np.add.reduceat(self.counts, np.arange(0, len(self.counts), per))
        return per, bins, left, width

    def draw_chart(self):
        """Notes per gate: a bar for each gate from From (left) to To (right); red marks for gates no note gets."""
        ch, s = self.chart, self.s
        ch.delete("all")
        top, bot = int(22 * s), self.h - self.mb
        ch.create_text(int(6 * s), int(4 * s), text=tr("range_window.spread"), anchor="nw", fill="#333",
                       font=("Segoe UI", 8))
        if self.counts is None:
            ch.create_rectangle(int(40 * s), top, self.cw - int(8 * s), bot, outline=OFF_COLOR)
            return
        a, b = self.gates()
        sign = 1 if b >= a else -1
        per, bins, left, width = self.chart_bins()
        most = max(1, int(bins.max()))
        for v in sorted({0, most, most // 2} if most >= 2 else {0, most}):
            y = bot - v / most * (bot - top)
            ch.create_line(left, y, left + width, y, fill="#e4e4e4")
            ch.create_text(left - 4 * s, y, text=str(v), anchor="e", fill="#333", font=("Segoe UI", 7))
        bw = width / len(bins)
        gap = 1 if bw >= 4 else 0
        for i, v in enumerate(bins.tolist()):
            x0 = left + i * bw
            hot = i == self.bar_hover
            if v:
                ch.create_rectangle(x0, bot - v / most * (bot - top), x0 + bw - gap, bot, width=0,
                                    fill=BAR_HOT if hot else BAR)
            else:
                ch.create_rectangle(x0, bot - max(2, round(3 * s)), x0 + bw - gap, bot, width=0,
                                    fill=BAR_HOT if hot else NONE)
        ch.create_line(left, bot, left + width, bot, fill="#808080")
        marks = min(len(bins), 5)  # gate numbers under a few bars, both ends included
        for k in range(marks):
            i = round(k * (len(bins) - 1) / max(1, marks - 1))
            anchor = "nw" if k == 0 and marks > 1 else "ne" if k == marks - 1 and marks > 1 else "n"
            x = left + i * bw + (0 if anchor == "nw" else bw if anchor == "ne" else bw / 2)
            ch.create_text(x, bot + 4 * s, text=str(a + sign * i * per), anchor=anchor, fill="#333",
                           font=("Segoe UI", 7))
        if self.bar_hover is not None and self.bar_hover < len(bins):
            i = self.bar_hover
            g0, g1 = a + sign * i * per, a + sign * min(len(self.counts) - 1, (i + 1) * per - 1)
            text = (tr("range_window.spread_at", g=g0, n=int(bins[i])) if g0 == g1 else
                    tr("range_window.spread_at_many", g0=g0, g1=g1, n=int(bins[i])))
            ch.create_text(self.cw - 6 * s, int(4 * s), text=text, anchor="ne", fill="#0a50e0", font=("Segoe UI", 8))

    def on_bar_hover(self, x):
        self.bar_hover = None
        if x is not None and self.counts is not None:
            per, bins, left, width = self.chart_bins()
            i = math.floor((x - left) / width * len(bins))
            self.bar_hover = i if 0 <= i < len(bins) else None
        self.draw_chart()

    # ---- mouse

    def point_at(self, x, y):
        r = 8 * self.s
        best = None
        for i, (u, v) in enumerate(self.pts):
            d = max(abs(self.u2x(u) - x), abs(self.y2c(v) - y))
            if d <= r and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def snapped(self, e):
        u, y = self.x2u(e.x), self.c2y(e.y)
        if not e.state & 0x1:
            u, y = round(u / U_SNAP) * U_SNAP, round(y / Y_SNAP) * Y_SNAP
        return u, y

    def press(self, e):
        if not self.on_var.get():
            return
        i = self.point_at(e.x, e.y)
        self.drag_from = json.loads(json.dumps(self.memo))  # (put back if the notes it makes are too many)
        if i is None:
            u, y = self.snapped(e)
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            pts = [list(p) for p in self.pts]
            i = next(j for j, p in enumerate(pts) if p[0] > u)
            pts.insert(i, [u, y])
            self.drag = i
            self.change("graph", pts, mark=False)
        self.drag = i
        self.draw()

    def motion(self, e):
        i = self.drag
        if i is None:
            return
        u, y = self.snapped(e)
        pts = [list(p) for p in self.pts]
        if i in (0, len(pts) - 1):
            u = pts[i][0]  # (the ends stay at the shape's edges)
        else:
            u = min(max(u, pts[i - 1][0]), pts[i + 1][0])
        if [u, y] != pts[i]:
            pts[i] = [u, y]
            self.change("graph", pts, mark=False)

    def release(self, e):
        if self.drag is None:
            return
        self.drag = None
        if self.held:  # (the graph moved further than the notes: asked now)
            if not self.fits():
                self.memo = self.drag_from
            self.held = False
            self.store()
        self.draw()
        self.hist.mark()

    def delete_at(self, e):
        i = self.point_at(e.x, e.y)
        if not self.on_var.get() or i is None or len(self.pts) <= 2 or self.drag is not None:  # (not mid-drag)
            return
        pts = [list(p) for p in self.pts]
        del pts[i]
        pts[0][0], pts[-1][0] = 0.0, 1.0
        self.change("graph", pts)

    def on_hover(self, e):
        self.hover = None
        if e is not None and self.drag is None and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            self.hover = [u, float(np.interp(u, [p[0] for p in self.pts], [p[1] for p in self.pts]))]
        self.draw()


def open_range_graph(app):
    tgts = app.skip_hz(app.custom_targets())  # (a Hz bass picked too: left out, after a warning)
    if tgts:
        RangeGraph(app, tgts)
