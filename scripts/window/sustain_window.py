"""The Sustain window (right-click "Sustain..."; sustain.py): the selected shapes' notes held on, for lagging out MIDI
players. "Sustain notes" on / off, and how long a gap may be and still be held over: Auto (worked out from each
shape's own notes) or typed ticks (kept in beats, so a PPQ change keeps the look).

Opening it puts Sustain on (the menu item is how it's put on). Changes show on the piano roll at once; OK keeps
them as one undo step, Cancel / Esc / X puts everything back. Ctrl+Z / Ctrl+Y step through the changes made in it."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.sustain import MOST_GAP, MOST_PAST, MOST_POINTS, auto_gap, is_sustained, stop_share, stop_tick
from window.widgets import LocalUndo, Scrub, Tooltip, remember_place


def sustain_targets(app):
    """The selected shapes Sustain works on: not a Hz bass (holding its repeats would ruin the sound), not an image
    (its notes are its picture, user)."""
    return [app.shapes[i] for i in sorted(app.sels)
            if i < len(app.shapes) and not app.shapes[i].get("hz") and "picture" not in app.shapes[i]]


class SustainWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("sustain.title"))
        self.transient(app)
        self.resizable(False, False)
        self.tgts = sustain_targets(app)
        self.before = json.dumps(app.shapes)
        self.old = [json.loads(json.dumps(t.get("sustain"))) for t in self.tgts]
        self.closed = False
        own = [t["sustain"]["gap"] for t in self.tgts if "gap" in (t.get("sustain") or {})]
        kinds = {"gap" in (t.get("sustain") or {}) for t in self.tgts if t.get("sustain") is not None}
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.on_var = tk.BooleanVar(value=True)
        on = ttk.Checkbutton(box, text=tr("sustain.on"), variable=self.on_var, command=self.change)
        on.pack(anchor="w")
        Tooltip(on, tr("sustain.on_tip"))
        ttk.Label(box, text=tr("sustain.hold")).pack(anchor="w", pady=(8, 2))
        # (several shapes: the way they all share, else none picked until one is; typed ones that differ: empty)
        self.mode = tk.StringVar(value=("own" if kinds == {True} else "auto" if kinds != {True, False} else ""))
        self.auto_btn = ttk.Radiobutton(box, text="", value="auto", variable=self.mode, command=self.change)
        self.auto_btn.pack(anchor="w", padx=(int(16 * app.scale), 0))
        Tooltip(self.auto_btn, tr("sustain.auto_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(int(16 * app.scale), 0), pady=(2, 0))
        self.own_btn = ttk.Radiobutton(row, text="", value="own", variable=self.mode, command=self.change)
        self.own_btn.pack(side="left")
        self.gap_var = tk.StringVar(value=fmt(self.ticks(own[0])) if own and len(set(own)) == 1 else "")
        e = self.gap_box = ttk.Entry(row, textvariable=self.gap_var, width=8)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: (self.on_gap(), "break")[1])  # (not OK: the window stays)
        e.bind("<FocusOut>", lambda ev: self.on_gap())
        lb = ttk.Label(row, text=tr("sustain.ticks"))
        lb.pack(side="left", padx=(4, 0))
        Scrub(app, [(e, self.gap_var, self.on_gap)], (1, 10, 1), 0, MOST_GAP * app.ppq, label=lb, no_undo=True)
        for w in (self.own_btn, e, lb):
            Tooltip(w, tr("sustain.own_tip"))
        ttk.Separator(box).pack(fill="x", pady=(10, 6))
        self.rows = {}  # key -> [tick var or None, box var, box, tick or None, box as last used]
        self.length_row(self.new_row(box), "most")
        row = self.new_row(box)
        self.length_row(row, "every")
        self.length_row(row, "from", tick=False)
        stops = [(t.get("sustain") or {}).get("stops") or {} for t in self.tgts]
        self.points = [list(s.get("at", [])) for s in stops]  # (each shape's stops put by hand: shares)
        self.stops_on = tk.BooleanVar(value=any(s and not s.get("off") for s in stops))
        self.past = tk.BooleanVar(value=any(s.get("past") for s in stops))
        row = self.new_row(box)
        self.stops_tick = ttk.Checkbutton(row, text=tr("sustain.stops"), variable=self.stops_on, command=self.change)
        self.stops_tick.pack(side="left")
        Tooltip(self.stops_tick, tr("sustain.stops_tip"))
        self.add_btn = ttk.Button(row, text=tr("sustain.add_stop"), command=self.add_stop)
        self.add_btn.pack(side="left", padx=(6, 0))
        Tooltip(self.add_btn, tr("sustain.add_stop_tip"))
        self.clear_btn = ttk.Button(row, text=tr("sustain.clear_stops"), command=self.clear_stops)
        self.clear_btn.pack(side="left", padx=(4, 0))
        self.count = ttk.Label(row, text="")
        self.count.pack(side="left", padx=(6, 0))
        row = self.new_row(box)
        self.past_tick = ttk.Checkbutton(row, text=tr("sustain.past"), variable=self.past, command=self.change)
        self.past_tick.pack(side="left", padx=(int(16 * app.scale), 0))
        Tooltip(self.past_tick, tr("sustain.past_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(10, 0))
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda ev: self.cancel())
        self.bind("<Return>", lambda ev: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.show_auto()
        self.store()
        self.hist = LocalUndo(self, self.state, self.put_state)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, "sustain")
        self.grab_set()  # (the shapes it works on stay picked while it's open)
        self.focus_set()

    def new_row(self, box):
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(2, 0))
        return row

    def length_row(self, row, key, tick=True):
        """"[ ] <text> [box] ticks" (tick=False: "<text> [box] ticks", a number going with the row's tick) for
        sh["sustain"][key] (beats, shown in ticks): ticked while any shape has it; the box empty when they differ."""
        have = self.have(key)
        on = tk.BooleanVar(value=bool(have)) if tick else None
        var = tk.StringVar(value=fmt(self.ticks(have[0])) if have and len(set(have)) == 1 else "" if tick else "0")
        if tick:
            w = ttk.Checkbutton(row, text=tr("sustain." + key), variable=on, command=lambda: self.tick_length(key))
        else:
            w = ttk.Label(row, text=tr("sustain." + key))
        w.pack(side="left", padx=(0 if tick else 6, 0))
        e = ttk.Entry(row, textvariable=var, width=8)
        e.pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: (self.on_length(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_length(key))
        lb = ttk.Label(row, text=tr("sustain.ticks"))
        lb.pack(side="left", padx=(4, 0))
        Scrub(self.app, [(e, var, lambda: self.on_length(key))], (1, 10, 1), 1 if tick else 0,
              MOST_GAP * self.app.ppq, label=lb, no_undo=True)
        for x in (w, e, lb):
            Tooltip(x, tr("sustain.%s_tip" % key))
        self.rows[key] = [on, var, e, w if tick else None, var.get()]

    def have(self, key):
        """The shapes' values of sustain[key] (of "from": every shape stopping "every", 0 when it has none)."""
        if key == "from":
            return [t["sustain"].get("from", 0) for t in self.tgts if "every" in (t.get("sustain") or {})]
        return [t["sustain"][key] for t in self.tgts if key in (t.get("sustain") or {})]

    def tick_length(self, key):
        """A length row ticked with an empty box: one beat to start with."""
        var = self.rows[key][1]
        if self.length(key) is None:
            var.set(fmt(self.app.ppq))
        self.change()

    def length(self, key):
        """A row's typed length in beats (at least a tick; "from" 0 too), or None (empty / not a number)."""
        try:
            ticks = calc(self.rows[key][1].get())
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(ticks) or ticks < (0 if self.rows[key][0] is None else 0.5):
            return None
        return min(max(math.floor(ticks + 0.5), 0) / self.app.ppq, MOST_GAP)

    def on_length(self, key):
        """A length typed / stepped in a row: ticks the row (a number without a tick: its row's)."""
        on, var, box = self.rows[key][:3]
        if self.closed or str(box.cget("state")) == "disabled":
            return
        beats = self.length(key)
        if beats is None:  # (a wrong number: back to the last good one, or empty)
            have = self.have(key)
            return var.set(fmt(self.ticks(have[0])) if have and len(set(have)) == 1 else
                           "" if on is not None else "0")
        var.set(fmt(self.ticks(beats)))
        own = next(k for k, r in self.rows.items() if r[2].master is box.master and r[0] is not None)
        if not self.rows[own][0].get():
            self.rows[own][0].set(True)
            if self.length(own) is None:
                self.rows[own][1].set(fmt(self.app.ppq))
        self.store()
        self.hist.mark(key)

    def bare_notes(self, t):
        """Shape t's notes without Sustain (what its stops are shares of)."""
        return np.asarray(self.app.notes_of({k: v for k, v in t.items() if k != "sustain"}))

    def add_stop(self):
        """A stop on the next snap line after each shape's last usable stop (none: after its first note's start);
        never past its end unless "Allow past the shape's end" is ticked."""
        snap, added = self.app.snap_ticks(), False
        for t, pts in zip(self.tgts, self.points):
            notes = self.bare_notes(t)
            if not len(notes) or len(pts) >= MOST_POINTS:
                continue
            hi = int(notes[:, 1].max())
            used = [stop_tick(notes, f) for f in pts if f < 1 or self.past.get()]
            ref = max(used) if used else int(notes[:, 0].min())
            tick = (ref // snap + 1) * snap
            if tick == hi and self.past.get():  # (a stop right on the end does nothing)
                tick += snap
            f = stop_share(notes, tick)
            if f is None or (tick >= hi and not self.past.get()) or f > MOST_PAST:
                continue
            pts.append(f)
            pts.sort()
            added = True
        if not added:
            return self.app.status.show(tr("sustain.no_room"))
        self.stops_on.set(True)
        self.store()
        self.hist.mark()

    def clear_stops(self):
        for pts in self.points:
            pts.clear()
        self.store()
        self.hist.mark()

    def ticks(self, beats):
        return int(round(beats * self.app.ppq))

    def show_auto(self):
        """"Auto (n ticks)": the gap each shape works out from its own notes (several that differ: said so)."""
        got = {self.auto_of(t) for t in self.tgts}
        n = got.pop() if len(got) == 1 else None
        self.auto_btn.config(text=tr("sustain.auto_each") if n is None else tr("sustain.auto_one") if n == 1 else
                             tr("sustain.auto", n=n))

    def auto_of(self, t):
        """Shape t's Auto gap in ticks (from its notes without Sustain)."""
        return auto_gap(np.asarray(self.app.notes_of({k: v for k, v in t.items() if k != "sustain"})))

    def state(self):
        return json.dumps([self.on_var.get(), self.mode.get(), self.gap_var.get(),
                           {k: [r[0] and r[0].get(), r[1].get()] for k, r in self.rows.items()},
                           self.stops_on.get(), self.past.get(), self.points])

    def put_state(self, state):
        on, mode, gap, rows, stops_on, past, points = json.loads(state)
        self.stops_on.set(stops_on)
        self.past.set(past)
        for pts, was in zip(self.points, points):
            pts[:] = was
        self.on_var.set(on)
        self.mode.set(mode)
        self.gap_var.set(gap)
        for k, (ticked, text) in rows.items():
            if self.rows[k][0] is not None:
                self.rows[k][0].set(ticked)
            self.rows[k][1].set(text)
        self.store()

    def gap(self):
        """The typed gap in beats, or None (empty / not a number)."""
        try:
            ticks = calc(self.gap_var.get())
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(ticks) or ticks < 0:
            return None
        return min(math.floor(ticks + 0.5) / self.app.ppq, MOST_GAP)

    def store(self):
        """Put the window's settings on the shapes and show them."""
        on, mode, gap = self.on_var.get(), self.mode.get(), self.gap()
        for t, old, pts in zip(self.tgts, self.old, self.points):
            if not on:
                t.pop("sustain", None)
            elif mode == "auto":
                t["sustain"] = {}
            elif mode == "own" and gap is not None:
                t["sustain"] = {"gap": gap}
            else:  # (none picked yet / no number typed: each keeps what it had, Auto if it had none)
                t["sustain"] = {"gap": old["gap"]} if "gap" in (old or {}) else {}
            if on:
                self.put_length(t, old, "most", "most")
                self.put_length(t, old, "every", "every")
                self.put_length(t, old, "from", "every")
                if not t["sustain"].get("from"):
                    t["sustain"].pop("from", None)
                if self.stops_on.get() or pts:  # (unticked: the stops kept for ticking again)
                    t["sustain"]["stops"] = dict({"at": list(pts)}, **({"past": True} if self.past.get() else {}),
                                                 **({} if self.stops_on.get() else {"off": True}))
        n = sum(len(p) for p in self.points)
        self.count.config(text=tr("sustain.stops_n", n=n) if n != 1 else tr("sustain.stops_one"))
        self.seen = self.gap_var.get()  # (the box as last used: typed past it without Enter = OK takes it)
        for r in self.rows.values():
            r[4] = r[1].get()
        state = "normal" if on else "disabled"
        for w in [self.auto_btn, self.own_btn, self.gap_box, self.stops_tick, self.add_btn, self.clear_btn,
                  self.past_tick] + [w for r in self.rows.values() for w in r[2:4] if w]:
            w.config(state=state)
        self.app.shapes_changed()

    def put_length(self, t, old, key, tick):
        """A row's number onto shape t (tick = the row whose tick it goes with): off = none; on with a number = it;
        on with an empty box (shapes that differ) = each keeps what it had."""
        beats = self.length(key)
        if not self.rows[tick][0].get():
            return
        if beats is not None:
            t["sustain"][key] = beats
        elif key in (old or {}):
            t["sustain"][key] = old[key]

    def change(self):
        if self.mode.get() == "own" and self.gap() is None:  # (picked with an empty box: the first shape's gap)
            self.gap_var.set(fmt(self.auto_of(self.tgts[0])))
        self.store()
        self.hist.mark()

    def on_gap(self):
        """A gap typed / stepped: picks the typed way."""
        if self.closed or str(self.gap_box.cget("state")) == "disabled":
            return
        gap = self.gap()
        if gap is None:  # (a wrong number: back to the last good one, or empty)
            own = [t["sustain"]["gap"] for t in self.tgts if "gap" in (t.get("sustain") or {})]
            return self.gap_var.set(fmt(self.ticks(own[0])) if own and len(set(own)) == 1 else "")
        self.gap_var.set(fmt(self.ticks(gap)))
        self.mode.set("own")
        self.store()
        self.hist.mark("gap")

    def ok(self):
        if self.gap_var.get() != self.seen and self.on_var.get():
            self.on_gap()  # (a number typed without Enter counts too)
        for k, r in self.rows.items():
            if r[1].get() != r[4] and self.on_var.get():
                self.on_length(k)
        self.closed = True
        if json.dumps(self.app.shapes) != self.before:
            self.app.add_undo_step(self.before, tr("sustain.step"))
        self.close()
        if self.on_var.get():
            self.app.tips.show("sustain", wait=True)  # (the first time: what it does)

    def cancel(self):
        self.closed = True
        if json.dumps(self.app.shapes) != self.before:  # (all as it was: an Add between step tried out stays a step)
            self.app.roll.cancel_draft()
            self.app.shapes = json.loads(self.before)
        self.app.shapes_changed()
        self.close()

    def close(self):
        self.grab_release()
        self.destroy()


def open_sustain(app):
    if sustain_targets(app):
        SustainWindow(app)


def any_sustained(app):
    return any(is_sustained(app.shapes[i]) for i in app.sels if i < len(app.shapes))
