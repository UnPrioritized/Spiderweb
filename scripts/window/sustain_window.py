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
from notes.sustain import MOST_GAP, auto_gap, is_sustained
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
        self.most_on, self.most_var, self.most_box, self.most_tick = self.length_row(box, "most", self.on_most)
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

    def length_row(self, box, key, apply):
        """A row "[ ] <text> [box] ticks" for sh["sustain"][key] (beats, shown in ticks): ticked while any shape has
        it; the box empty when they differ. -> (tick var, box var, box, tick)."""
        have = [t["sustain"][key] for t in self.tgts if key in (t.get("sustain") or {})]
        on = tk.BooleanVar(value=bool(have))
        var = tk.StringVar(value=fmt(self.ticks(have[0])) if have and len(set(have)) == 1 else "")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(2, 0))
        tick = ttk.Checkbutton(row, text=tr("sustain." + key), variable=on, command=lambda: self.tick_length(var))
        tick.pack(side="left")
        e = ttk.Entry(row, textvariable=var, width=8)
        e.pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: (apply(), "break")[1])
        e.bind("<FocusOut>", lambda ev: apply())
        lb = ttk.Label(row, text=tr("sustain.ticks"))
        lb.pack(side="left", padx=(4, 0))
        Scrub(self.app, [(e, var, apply)], (1, 10, 1), 1, MOST_GAP * self.app.ppq, label=lb, no_undo=True)
        for w in (tick, e, lb):
            Tooltip(w, tr("sustain.%s_tip" % key))
        return on, var, e, tick

    def tick_length(self, var):
        """A length row ticked with an empty box: one beat to start with."""
        if self.length(var) is None:
            var.set(fmt(self.app.ppq))
        self.change()

    def length(self, var):
        """A typed length in beats (at least a tick), or None (empty / not a number)."""
        try:
            ticks = calc(var.get())
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(ticks) or ticks < 0.5:
            return None
        return min(math.floor(ticks + 0.5) / self.app.ppq, MOST_GAP)

    def on_length(self, key, on, var, box):
        """A length typed / stepped in a length row: ticks the row."""
        if self.closed or str(box.cget("state")) == "disabled":
            return
        beats = self.length(var)
        if beats is None:  # (a wrong number: back to the last good one, or empty)
            have = [t["sustain"][key] for t in self.tgts if key in (t.get("sustain") or {})]
            return var.set(fmt(self.ticks(have[0])) if have and len(set(have)) == 1 else "")
        var.set(fmt(self.ticks(beats)))
        on.set(True)
        self.store()
        self.hist.mark(key)

    def on_most(self):
        self.on_length("most", self.most_on, self.most_var, self.most_box)

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
        return json.dumps([self.on_var.get(), self.mode.get(), self.gap_var.get(), self.most_on.get(),
                           self.most_var.get()])

    def put_state(self, state):
        on, mode, gap, most_on, most = json.loads(state)
        self.on_var.set(on)
        self.mode.set(mode)
        self.gap_var.set(gap)
        self.most_on.set(most_on)
        self.most_var.set(most)
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
        for t, old in zip(self.tgts, self.old):
            if not on:
                t.pop("sustain", None)
            elif mode == "auto":
                t["sustain"] = {}
            elif mode == "own" and gap is not None:
                t["sustain"] = {"gap": gap}
            else:  # (none picked yet / no number typed: each keeps what it had, Auto if it had none)
                t["sustain"] = {"gap": old["gap"]} if "gap" in (old or {}) else {}
            if on:
                self.put_length(t, old, "most", self.most_on, self.most_var)
        self.seen = self.gap_var.get()  # (the box as last used: typed past it without Enter = OK takes it)
        self.seen_most = self.most_var.get()
        state = "normal" if on else "disabled"
        for w in (self.auto_btn, self.own_btn, self.gap_box, self.most_tick, self.most_box):
            w.config(state=state)
        self.app.shapes_changed()

    def put_length(self, t, old, key, on, var):
        """A length row onto shape t: off = none; on with a number = it; on with an empty box (shapes that differ) =
        each keeps what it had."""
        beats = self.length(var)
        if on.get() and beats is not None:
            t["sustain"][key] = beats
        elif on.get() and key in (old or {}):
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
        if self.most_var.get() != self.seen_most and self.on_var.get():
            self.on_most()
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
