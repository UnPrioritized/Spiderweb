"""The Sustain window (right-click "Sustain..."; sustain.py): the selected shapes' notes held on, for lagging out MIDI
players. "Sustain notes" on / off, how long a gap may be and still be held over (Auto, worked out from each shape's
own notes, or typed), each note's longest length, and where the sustain stops (every N, or points put by hand: the
piano roll shows them as lines while the shape is selected, roll_sustain.py). Lengths are kept in beats (a PPQ
change keeps the look) and shown in ticks or beats (the user's pick, settings.ini).

Opening it puts Sustain on (the menu item is how it's put on). Changes show on the piano roll at once. The piano roll
can be used while it's open: the window follows the selection, and before anything else changes there, the change
being tried out is kept as its own undo step (settle, like the note tool windows). OK keeps it (one undo step),
Cancel / Esc / X puts back what changed since. Ctrl+Z / Ctrl+Y step through the changes made in it (on the piano roll
too while it's open: the stops are dragged there)."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files import settings
from files.lang import tr
from files.mathexpr import calc, fmt
from notes.sustain import (MOST_GAP, MOST_PAST, MOST_PIECES, MOST_POINTS, auto_gap, is_sustained, stop_share, stop_tick,
                           stop_ticks, sustained, too_many_pieces)
from window import look
from window.widgets import LocalUndo, Scrub, Tooltip, remember_place


def target_indices(app):
    """The selected shapes Sustain works on (their numbers): not a Hz bass (holding its repeats would ruin the
    sound), not an image (its notes are its picture, user)."""
    return [i for i in sorted(app.sels)
            if i < len(app.shapes) and not app.shapes[i].get("hz") and "picture" not in app.shapes[i]]


def sustain_targets(app):
    return [app.shapes[i] for i in target_indices(app)]


def bare_notes(app, sh):
    """Shape sh's notes without Sustain (what its stops put by hand are shares of)."""
    return np.asarray(app.notes_of({k: v for k, v in sh.items() if k != "sustain"}))


def own_part(sh):
    """What the window changes on a shape: its Sustain, and an Add between step's mark for a Sustain of its own
    ("-": none)."""
    return [sh.get("sustain"), (sh.get("between") or {}).get("sus", "-")]


def put_own(sh, part):
    sus, mark = part
    if sus is None:
        sh.pop("sustain", None)
    else:
        sh["sustain"] = sus
    b = sh.get("between")
    if isinstance(b, dict):
        if mark == "-":
            b.pop("sus", None)
        else:
            b["sus"] = mark


LENGTHS = ("gap", "most", "every")  # (sh["sustain"] lengths in beats; "from" goes with "every")


class SustainWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        app.sustain_window = self
        self.title(tr("sustain.title"))
        self.transient(app)
        self.resizable(False, False)
        self.closed = False
        self.ppq = app.ppq  # (the PPQ the boxes show ticks of)
        self.idx = []  # the shapes worked on (numbers in app.shapes)
        self.shown_beats = str(settings.get("sustain", "unit", "")).strip().lower() == "beats"
        self.unit = tk.StringVar(value="beats" if self.shown_beats else "ticks")
        self.units, self.scrubs = [], []  # (unit labels beside the boxes; [Scrub, its lowest in ticks])
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text=tr("sustain.nothing"), foreground=look.HINT)
        self.on_var = tk.BooleanVar(value=True)
        self.on_tick = ttk.Checkbutton(box, text=tr("sustain.on"), variable=self.on_var,
                                       command=lambda: self.put_all("on"))
        self.on_tick.pack(anchor="w")
        Tooltip(self.on_tick, tr("sustain.on_tip"))
        ttk.Label(box, text=tr("sustain.hold")).pack(anchor="w", pady=(8, 2))
        self.mode = tk.StringVar(value="")
        self.auto_btn = ttk.Radiobutton(box, text="", value="auto", variable=self.mode, command=self.put_mode)
        self.auto_btn.pack(anchor="w", padx=(int(16 * app.scale), 0))
        Tooltip(self.auto_btn, tr("sustain.auto_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(int(16 * app.scale), 0), pady=(2, 0))
        self.own_btn = ttk.Radiobutton(row, text="", value="own", variable=self.mode, command=self.put_mode)
        self.own_btn.pack(side="left")
        self.gap_var = tk.StringVar(value="")
        e = self.gap_box = ttk.Entry(row, textvariable=self.gap_var, width=8)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: (self.on_gap(), "break")[1])  # (not OK: the window stays)
        e.bind("<FocusOut>", lambda ev: self.on_gap())
        lb = self.gap_unit = self.unit_label(row)
        self.scrub(e, self.gap_var, self.on_gap, lb, 0)
        for w in (self.own_btn, e, lb):
            Tooltip(w, tr("sustain.own_tip"))
        ttk.Separator(box).pack(fill="x", pady=(10, 6))
        self.rows = {}  # key -> [tick var or None, box var, box, tick or None, box as last used]
        self.length_row(self.new_row(box), "most")
        row = self.new_row(box)
        self.length_row(row, "every")
        self.length_row(row, "from", tick=False)
        self.points = []  # (each shape's stops put by hand: shares)
        self.stops_on = tk.BooleanVar(value=False)
        self.past = tk.BooleanVar(value=False)
        row = self.new_row(box)
        self.stops_tick = ttk.Checkbutton(row, text=tr("sustain.stops"), variable=self.stops_on,
                                          command=lambda: self.put_all("stops"))
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
        self.past_tick = ttk.Checkbutton(row, text=tr("sustain.past"), variable=self.past,
                                         command=lambda: self.put_all("past"))
        self.past_tick.pack(side="left", padx=(int(16 * app.scale), 0))
        Tooltip(self.past_tick, tr("sustain.past_tip"))
        self.warn = ttk.Label(box, text="", foreground=look.WARN)  # (shown only while stops can't be put on)
        self.memo = []  # [(notes, what, its value)]: worked out from a shape's notes, kept while they're the same
        ttk.Separator(box).pack(fill="x", pady=(10, 6))
        self.warn_after = box.winfo_children()[-1]
        row = self.new_row(box)
        ttk.Label(row, text=tr("sustain.unit")).pack(side="left")
        for value in ("ticks", "beats"):
            ttk.Radiobutton(row, text=tr("sustain.unit_" + value), value=value, variable=self.unit,
                            command=self.set_unit).pack(side="left", padx=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(10, 0))
        ttk.Button(row, text=tr("range_window.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda ev: self.cancel())
        self.bind("<Return>", lambda ev: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.on, self.vals, self.offs, self.pasts = [], {k: [] for k in LENGTHS + ("from",)}, [], []
        self.hist = LocalUndo(self, self.state, self.put_state)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.retarget(start=True)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, "sustain")
        self.focus_set()

    @property
    def tgts(self):
        return [self.app.shapes[i] for i in self.idx if i < len(self.app.shapes)]

    def new_row(self, box):
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(2, 0))
        return row

    def unit_label(self, row):
        lb = ttk.Label(row, text=tr("sustain." + self.unit.get()))
        lb.pack(side="left", padx=(4, 0))
        self.units.append(lb)
        return lb

    def scrub(self, e, var, apply, lb, lo):
        """The box stepped by dragging it or its unit sideways, Up / Down, the wheel. lo: its lowest, in ticks."""
        s = Scrub(self.app, [(e, var, apply)], self.steps, lo, MOST_GAP, label=lb, drag_box=True, no_undo=True,
                  text=lambda v: self.text_of(v if self.shown_beats else v / self.app.ppq))
        self.scrubs.append((s, lo))
        self.put_limits()

    def steps(self):
        """(step, Shift step, Ctrl step) in the unit shown: ticks 1 / 10 / 1, beats 1/4 / 1 / one tick."""
        return (0.25, 1, 1 / self.app.ppq) if self.shown_beats else (1, 10, 1)

    def put_limits(self):
        ppq = 1 if self.shown_beats else self.app.ppq
        for s, lo in self.scrubs:
            s.lo, s.hi = lo / self.app.ppq * ppq, MOST_GAP * ppq

    def length_row(self, row, key, tick=True):
        """"[ ] <text> [box] ticks" (tick=False: "<text> [box] ticks", a number going with the row's tick) for
        sh["sustain"][key] (beats): ticked while any shape has it; the box empty when they differ."""
        on = tk.BooleanVar(value=False) if tick else None
        var = tk.StringVar(value="")
        if tick:
            w = ttk.Checkbutton(row, text=tr("sustain." + key), variable=on, command=lambda: self.tick_length(key))
        else:
            w = ttk.Label(row, text=tr("sustain." + key))
        w.pack(side="left", padx=(0 if tick else 6, 0))
        e = ttk.Entry(row, textvariable=var, width=8)
        e.pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: (self.on_length(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_length(key))
        lb = self.unit_label(row)
        self.scrub(e, var, lambda: self.on_length(key), lb, 1 if tick else 0)
        for x in (w, e, lb):
            Tooltip(x, tr("sustain.%s_tip" % key))
        # (r[5]: the box, its unit and a word before it: greyed while its tick is off)
        self.rows[key] = [on, var, e, w if tick else None, "", [e, lb] + ([] if tick else [w])]

    # ---- the shapes worked on

    def sus_now(self):
        return json.dumps([t.get("sustain") for t in self.tgts])

    def own_now(self):
        return json.dumps([own_part(t) for t in self.tgts])

    def retarget(self, start=False):
        """Work on the selected shapes (start: opened, which puts Sustain on)."""
        self.idx = target_indices(self.app)
        self.saved_sel = self.app.sel_state()  # (for the undo step)
        self.before = self.sus_now()
        self.kept = self.own_now()  # (put back by Cancel)
        self.load(start)
        if start and self.idx:
            self.store()
        else:
            self.now = self.before  # (as this window last left them)
            self.show()
        self.hist.reset()

    def load(self, start):
        """Each shape's settings from the shapes (start: all sustained), then the boxes and ticks."""
        sus = [t.get("sustain") if is_sustained(t) else None for t in self.tgts]
        self.on = [start or s is not None for s in sus]
        # (each shape's own values, None = none; several shapes keep theirs until that row is changed, hunt)
        self.vals = {k: [(s or {}).get(k) for s in sus] for k in LENGTHS}
        self.vals["from"] = [(s or {}).get("from", 0) if "every" in (s or {}) else None for s in sus]
        self.load_stops(show=False)
        self.show_controls()

    def load_stops(self, show=True):
        stops = [(t.get("sustain") or {}).get("stops") if is_sustained(t) else None for t in self.tgts]
        self.points = [list(s["at"]) if s else [] for s in stops]
        self.offs = [None if not s else bool(s.get("off")) for s in stops]  # (None: no stops of its own)
        self.pasts = [bool(s and s.get("past")) for s in stops]
        if show:
            self.show_controls()

    def show_controls(self):
        """The ticks and boxes as the shapes' settings are: ticked when every shape has it, half-ticked when only
        some do (like the side panel's Mixed); a box shows the number they share, else stays empty."""
        def tick(var, w, flags):
            var.set(bool(flags) and all(flags))
            w.state(["alternate"] if any(flags) and not all(flags) else ["!alternate"])

        def shared(values, empty=""):
            got = {v for v in values if v is not None}
            return self.text_of(got.pop()) if len(got) == 1 and None not in values else empty
        tick(self.on_var, self.on_tick, self.on)
        gaps = self.vals["gap"]
        self.mode.set("auto" if all(g is None for g in gaps) else "own" if None not in gaps else "")
        got = {g for g in gaps if g is not None}
        self.gap_var.set(self.text_of(got.pop()) if len(got) == 1 else "")
        for key, r in self.rows.items():
            vals = self.vals[key]
            if r[0] is not None:
                tick(r[0], r[3], [v is not None for v in vals])
                r[1].set(shared(vals))
            else:
                r[1].set(shared([v for v in vals if v is not None]) if any(v is not None for v in vals) else "0")
        tick(self.stops_on, self.stops_tick, [o is False for o in self.offs])
        tick(self.past, self.past_tick, self.pasts)

    def sync(self):
        """The main window changed the selection or the shapes (sync_panel)."""
        if self.closed:
            return
        if target_indices(self.app) != self.idx:
            self.settle()
            self.retarget()
        elif self.sus_now() != self.now:  # (undo, a file opened)
            self.retarget()
        else:
            self.show_auto()

    def fresh(self):
        """The shapes worked on are still there, as this window last left them."""
        return (not self.closed and bool(self.idx) and self.idx[-1] < len(self.app.shapes)
                and self.sus_now() == self.now)

    def pending(self):
        """Is there a change tried here that settle would keep?"""
        return self.fresh() and self.now != self.before

    def put_back(self, saved):
        """The shapes' Sustain as before the change tried here, on saved (a copy of the shapes)."""
        for i, part in zip(self.idx, json.loads(self.kept)):
            if i < len(saved):
                put_own(saved[i], part)

    def settle(self, also=()):
        """Something else is about to change in the main window: the change tried so far is kept (its own undo
        step), and from now on Cancel only puts back what changes after this. also: other windows kept next
        (App.settle_windows): this step takes their tries back too."""
        if self.fresh():
            self.hist.reset()  # (its steps would undo nothing of what comes next, hunt; kept: the main window's)
        if not self.pending():
            return
        saved = json.loads(json.dumps(self.app.shapes))  # (the step takes back only what was tried here)
        for w in (self, *also):
            w.put_back(saved)
        sel, self.saved_sel, self.before = self.saved_sel, self.app.sel_state(), self.now
        self.kept = self.own_now()
        self.app.add_undo_step(json.dumps(saved), tr("sustain.step"), sel)

    def ppq_changed(self):
        """A project setting changed (App.on_project_change: every key press in its boxes). Only a new PPQ
        matters: the boxes show the same lengths in the new ticks (typed ones too); nothing else is started over
        (hunt: the change tried so far lost its undo step and Cancel)."""
        old = self.ppq
        if self.app.ppq == old:
            return
        self.ppq = self.app.ppq
        texts = [self.gap_var] + [r[1] for r in self.rows.values()]
        got = [self.read(v.get(), 0, old) for v in texts]
        seen = [self.read(t, 0, old) for t in [self.seen] + [r[4] for r in self.rows.values()]]
        for v, b in zip(texts, got):
            if b is not None:
                v.set(self.text_of(b))
        if seen[0] is not None:
            self.seen = self.text_of(seen[0])
        for r, b in zip(self.rows.values(), seen[1:]):
            if b is not None:
                r[4] = self.text_of(b)
        self.put_limits()
        self.show_auto()

    def stops_changed(self, key=None):
        """The piano roll moved / added / removed a stop put by hand (roll_sustain.py): one of this window's steps
        (key: moves of one drag are one step)."""
        self.load_stops()
        self.now = self.sus_now()
        self.show()
        self.hist.mark(key)

    def local_step(self, redo):
        """Ctrl+Z / Ctrl+Y pressed on the piano roll while open (App.key_undo): the stops are dragged there, so this
        window's own steps go first (like Copy along a path's points); none left = False (the main window's undo).
        A stop held is let go."""
        roll = self.app.roll
        if roll.drag and roll.drag[0] == "sustop":
            roll.drag = None
        u = self.hist
        if u.get() != u.states[u.at]:
            u.mark()
        if not (u.at < len(u.states) - 1 if redo else u.at > 0):
            return False
        u.step(1 if redo else -1)
        return True

    # ---- lengths: kept in beats, shown in ticks or beats

    def text_of(self, beats):
        """A length in beats as the boxes show it (whole ticks)."""
        ppq = self.app.ppq
        ticks = int(round(beats * ppq))
        if not self.shown_beats:
            return fmt(ticks)
        return f"{ticks / ppq:.{len(str(ppq))}f}".rstrip("0").rstrip(".")  # (enough places for every tick)

    def read(self, text, lo, ppq=None):
        """Typed text -> beats (whole ticks, at most MOST_GAP), or None (empty, not a number, under lo ticks). ppq: the
        one it was typed for (default the project's)."""
        ppq = ppq or self.app.ppq
        try:
            v = calc(text)
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(v):
            return None
        ticks = v * ppq if self.shown_beats else v
        if ticks < lo:
            return None
        return min(max(math.floor(ticks + 0.5), 0) / ppq, MOST_GAP)

    def set_unit(self):
        """Ticks <-> beats: every box shows its number in the other unit (remembered for next time)."""
        beats = self.unit.get() == "beats"
        if beats == self.shown_beats:
            return
        texts = [self.gap_var] + [r[1] for r in self.rows.values()]
        got = [self.read(v.get(), 0) for v in texts]
        seen = [self.read(t, 0) for t in [self.seen] + [r[4] for r in self.rows.values()]]
        self.shown_beats = beats
        for v, b in zip(texts, got):
            if b is not None:
                v.set(self.text_of(b))
        if seen[0] is not None:
            self.seen = self.text_of(seen[0])
        for r, b in zip(self.rows.values(), seen[1:]):
            if b is not None:
                r[4] = self.text_of(b)
        for lb in self.units:
            lb.config(text=tr("sustain." + self.unit.get()))
        self.put_limits()
        try:
            settings.put("sustain", "unit", self.unit.get())
        except OSError:
            pass

    def put_all(self, what):
        """A tick changed ("on", "stops", "past"): every shape takes it (a half-ticked one: a click = all on)."""
        if not self.idx:
            return
        n = len(self.idx)
        if what == "on":
            self.on = [self.on_var.get()] * n
        elif what == "stops":  # (unticked: the stops kept for ticking again)
            on = self.stops_on.get()
            self.offs = [not on if o is not None or on else None for o in self.offs]
        else:
            self.pasts = [self.past.get()] * n
        self.show_controls()
        self.store()
        self.hist.mark()

    def put_mode(self):
        """Auto / the typed gap picked: for every shape (the typed way with an empty box: the first shape's Auto
        gap)."""
        if not self.idx:
            return
        if self.mode.get() == "auto":
            self.vals["gap"] = [None] * len(self.idx)
        else:
            gap = self.gap()
            if gap is None:
                gap = self.auto_of(self.tgts[0]) / self.app.ppq
            self.vals["gap"] = [gap] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark()

    def tick_length(self, key):
        """A length row ticked / unticked. Ticked: the shapes without it get the box's number (empty: one beat;
        the others keep theirs while they differ)."""
        on, var = self.rows[key][:2]
        if not self.idx:
            return
        vals = self.vals[key]
        if not on.get():
            vals[:] = [None] * len(vals)
            if key == "every":
                self.vals["from"] = [None] * len(vals)
        else:
            beats = self.length(key)
            vals[:] = [v if v is not None and beats is None else beats or 1.0 for v in vals]
            if key == "every":
                self.vals["from"] = [f or 0 for f in self.vals["from"]]
        self.show_controls()
        self.store()
        self.hist.mark()

    def length(self, key):
        """A row's typed length in beats (at least a tick; "from" 0 too), or None (empty / not a number)."""
        return self.read(self.rows[key][1].get(), 0 if self.rows[key][0] is None else 0.5)

    def on_length(self, key):
        """A length typed / stepped in a row: ticks the row (a number without a tick: its row's)."""
        on, var, box = self.rows[key][:3]
        if self.closed or str(box.cget("state")) == "disabled":
            return
        beats = self.length(key)
        if beats is None:  # (a wrong number: back to the last good one, or empty)
            return self.show_controls()
        own = key if on is not None else "every"
        if None in self.vals[own]:  # (a number typed ticks its row for every shape)
            self.vals[own] = [v if v is not None else 1.0 for v in self.vals[own]]
        if own == "every":
            self.vals["from"] = [f or 0 for f in self.vals["from"]]
        self.vals[key] = [beats] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark(key)

    def gap(self):
        """The typed gap in beats, or None (empty / not a number)."""
        return self.read(self.gap_var.get(), 0)

    # ---- stops put by hand

    def add_stop(self):
        """A stop on the next snap line after each shape's last usable stop (none: after its first note's start);
        never past its end unless "Allow past the shape's end" is ticked."""
        snap, added = self.app.snap_ticks(), False
        for t, pts, past in zip(self.tgts, self.points, self.pasts):
            notes = bare_notes(self.app, t)
            if not len(notes) or len(pts) >= MOST_POINTS:
                continue
            hi = int(notes[:, 1].max())
            used = [stop_tick(notes, f) for f in pts if f < 1 or past]
            ref = max(used) if used else int(notes[:, 0].min())
            tick = (ref // snap + 1) * snap
            if tick == hi and past:  # (a stop right on the end does nothing)
                tick += snap
            f = stop_share(notes, tick)
            if f is None or (tick >= hi and not past) or f > MOST_PAST:
                continue
            pts.append(f)
            pts.sort()
            added = True
        if not added:
            return self.app.status.show(tr("sustain.no_room"))
        self.offs = [False] * len(self.offs)  # (ticks "Stop at my points")
        self.show_controls()
        self.store()
        self.hist.mark()

    def clear_stops(self):
        for pts in self.points:
            pts.clear()
        self.store()
        self.hist.mark()

    # ---- showing

    def show_auto(self):
        """"Auto (n ticks)": the gap each shape works out from its own notes (several that differ: said so)."""
        got = {self.auto_of(t) for t in self.tgts}
        n = got.pop() if len(got) == 1 else None
        self.auto_btn.config(text=tr("sustain.auto_each") if n is None else tr("sustain.auto_one") if n == 1 else
                             tr("sustain.auto", n=n))

    def remembered(self, notes, what, make):
        """make() worked out once per notes (the same array while the shape's notes don't change: big shapes took
        0.3 s each time)."""
        for arr, w, value in self.memo:
            if arr is notes and w == what:
                return value
        value = make()
        self.memo.append((notes, what, value))
        del self.memo[:-16]
        return value

    def too_many(self, t):
        """Would shape t's stops cut its notes into more than the engine makes (so it leaves them whole)?"""
        sus = t.get("sustain")
        if not is_sustained(t) or not sus or not ("every" in sus or sus.get("stops")):
            return False
        notes = bare_notes(self.app, t)
        if not len(notes):
            return False
        stops = stop_ticks(notes, sus, self.app.ppq)
        rows = self.remembered(notes, "rows", lambda: len(np.unique(notes[:, 2])))
        if len(notes) + rows * len(stops) <= MOST_PIECES:  # (at most one note per key row and stop)
            return False
        key = ("cut", json.dumps(sus, sort_keys=True), self.app.ppq)

        def count():
            reach = int(stops[-1]) if len(stops) and stops[-1] > notes[:, 1].max() else None
            return too_many_pieces(sustained(notes, sus, self.app.ppq, reach), stops)
        return self.remembered(notes, key, count)

    def auto_of(self, t):
        """Shape t's Auto gap in ticks (from its notes without Sustain)."""
        notes = bare_notes(self.app, t)
        return self.remembered(notes, "auto", lambda: auto_gap(notes))

    def show(self):
        """The window as the settings are: the points counted, what can be used greyed or not."""
        n = sum(len(p) for p in self.points)
        self.count.config(text=tr("sustain.stops_n", n=n) if n != 1 else tr("sustain.stops_one"))
        self.seen = self.gap_var.get()  # (the box as last used: typed past it without Enter = OK takes it)
        for r in self.rows.values():
            r[4] = r[1].get()
        if self.idx:
            self.what.pack_forget()
        elif not self.what.winfo_manager():  # (nothing to work on selected: said so, all greyed)
            self.what.pack(anchor="w", before=self.on_tick, pady=(0, 6))
        self.on_tick.state(["!disabled"] if self.idx else ["disabled"])  # (state(): config(state=) drops a half-tick)
        on = any(self.on) and bool(self.idx)  # (half-ticked = some shapes: their settings can be changed)

        def put(widgets, ok=True):
            for w in widgets:
                if isinstance(w, ttk.Checkbutton):  # (state(): config(state=) drops a half-tick)
                    w.state(["!disabled"] if on and ok else ["disabled"])
                else:
                    w.config(state="normal" if on and ok else "disabled")
                if isinstance(w, ttk.Label):
                    w.config(foreground="" if on and ok else look.LABEL_OFF)
        put([self.auto_btn, self.own_btn, self.stops_tick, self.add_btn, self.clear_btn, self.past_tick] +
            [r[3] for r in self.rows.values() if r[3]])
        # (a box only while its way is picked / its row ticked, user)
        put([self.gap_box, self.gap_unit], any(g is not None for g in self.vals["gap"]))
        for key, r in self.rows.items():  # (some shapes have it: typing sets it on all)
            put(r[5], any(v is not None for v in self.vals[key if r[0] is not None else "every"]))
        self.show_auto()
        stuck = on and any(self.too_many(t) for t in self.tgts)
        if stuck and not self.warn.winfo_manager():
            self.warn.config(text=tr("sustain.too_many"))
            self.warn.pack(anchor="w", before=self.warn_after, pady=(4, 0))
        elif not stuck and self.warn.winfo_manager():
            self.warn.pack_forget()

    def state(self):
        return json.dumps([self.on, self.vals, self.points, self.offs, self.pasts])

    def put_state(self, state):
        on, vals, points, offs, pasts = json.loads(state)
        self.on, self.vals, self.offs, self.pasts = on, vals, offs, pasts
        for pts, was in zip(self.points, points):
            pts[:] = was
        self.show_controls()
        self.store()

    def store(self):
        """Put each shape's settings on it and show them."""
        for i, t in enumerate(self.tgts):
            if not self.on[i]:
                t.pop("sustain", None)
                continue
            sus = {k: self.vals[k][i] for k in LENGTHS if self.vals[k][i] is not None}
            if "every" in sus and self.vals["from"][i]:
                sus["from"] = self.vals["from"][i]
            pts, off = self.points[i], self.offs[i]
            if off is not None or pts:  # (unticked: the stops kept for ticking again)
                sus["stops"] = dict({"at": list(pts)}, **({"past": True} if self.pasts[i] else {}),
                                    **({"off": True} if off else {}))
            t["sustain"] = sus
        self.now = self.sus_now()
        self.show()
        self.app.shapes_changed()

    def on_gap(self):
        """A gap typed / stepped: the typed way for every shape."""
        if self.closed or str(self.gap_box.cget("state")) == "disabled":
            return
        gap = self.gap()
        if gap is None:  # (a wrong number: back to the last good one, or empty)
            return self.show_controls()
        self.vals["gap"] = [gap] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark("gap")

    # ---- closing

    def take_typing(self):
        """Numbers typed without Enter count too."""
        if self.gap_var.get() != self.seen and any(self.on):
            self.on_gap()
        for k, r in self.rows.items():
            if r[1].get() != r[4] and any(self.on):
                self.on_length(k)

    def ok(self):
        self.take_typing()
        self.settle()
        self.close()
        if any(self.on) and self.idx:
            self.app.tips.show("sustain", wait=True)  # (the first time: what it does)

    def cancel(self):
        if self.idx and self.idx[-1] < len(self.app.shapes) and self.own_now() != self.kept:
            # (as it was: an Add between step tried out stays one)
            self.app.roll.cancel_draft()
            for i, part in zip(self.idx, json.loads(self.kept)):
                put_own(self.app.shapes[i], part)
        self.close()
        self.app.shapes_changed()

    def close(self):
        self.closed = True
        self.app.sustain_window = None
        roll = self.app.roll
        if roll.drag and roll.drag[0] == "sustop":  # (a stop held: let go, its moves kept)
            roll.stop_tidy()
            roll.drag = None
        self.destroy()
        roll.focus_set()


def open_sustain(app):
    w = app.sustain_window
    if w:  # (already open: it follows the selection; the menu item puts Sustain on)
        w.sync()
        if w.idx and not all(w.on):
            w.on_var.set(True)
            w.put_all("on")
        w.lift()
        w.focus_set()
    elif target_indices(app):
        SustainWindow(app)


def any_sustained(app):
    return any(is_sustained(app.shapes[i]) for i in app.sels if i < len(app.shapes))
