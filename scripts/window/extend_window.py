"""The Gate extend window (right-click "Gate extend..."; notes/extend.py): the selected shapes' notes end later, so
they run over into the notes after them like a run played by hand. The Gate subtract window's layout and behaviour
(subtract_window.SubtractWindow: live, follows the selection, settle, OK / Cancel, own Ctrl+Z, "Change across the
shape" graph) with one side only and an "Amount in" choice: Ticks or % of each note's own length (default %, 50)."""

import json
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from notes.extend import MOST, MOST_PCT, is_extended, part_extend
from window.subtract_window import SubtractWindow, side_data, target_indices
from window.widgets import Tooltip, unit_text

UNITS = ("pct", "ticks")  # "Amount in:" choices, % first (the default)


def _bare_notes(app, sh):
    """Shape sh's notes before Gate extend (and Gate subtract, done after it)."""
    return np.asarray(app.notes_of({k: v for k, v in sh.items() if k not in ("extend", "subtract")}),
                      np.int64).reshape(-1, 4)


def carry_extend(app, whole, pieces):
    """Pieces cut off shape whole (Slice, Split here, Split into letters) that took its Gate extend: a graph across the
    shape is cut so each note keeps its amount (extend.part_extend)."""
    ext = whole.get("extend")
    if not isinstance(ext, dict) or not ext.get("vary"):
        return
    pieces = [p for p in pieces if isinstance(p.get("extend"), dict)]
    if not pieces:
        return
    notes = _bare_notes(app, whole)
    for p in pieces:
        p["extend"] = part_extend(p["extend"], notes, _bare_notes(app, p), app.ppq)


class ExtendWindow(SubtractWindow):
    KEY = "extend"
    T = "extend"
    ATTR = "extend_window"
    SIDES = ("end",)
    TOUCH = False

    def side_data(self):
        d = side_data()
        d["unit"] = "pct"
        d["sames"] = []  # (per shape: "Same amount for every note"; kept while in ticks, used only with %)
        return d

    @property
    def pct(self):
        sides = getattr(self, "sides", None)  # (none yet while the window is made: %)
        return not sides or sides["end"].get("unit", "pct") == "pct"

    def extra_rows(self, box):
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        self.unit_label = ttk.Label(row, text=tr("extend.unit"))
        self.unit_label.pack(side="left")
        self.unit_box = ttk.Combobox(row, values=[tr("extend.unit_" + u) for u in UNITS], state="readonly", width=24)
        self.unit_box.pack(side="left", padx=4)
        self.unit_box.bind("<<ComboboxSelected>>", lambda e: self.pick_unit(UNITS[self.unit_box.current()]))
        for w in (self.unit_label, self.unit_box):
            Tooltip(w, tr("extend.unit_tip"))
        self.same_var = tk.BooleanVar(value=False)
        self.same_tick = ttk.Checkbutton(box, text=tr("extend.same"), variable=self.same_var, command=self.put_same)
        self.same_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.same_tick, tr("extend.same_tip"))

    def put_same(self):
        """"Same amount for every note" clicked: every shape takes it (a half-ticked one: a click = all on)."""
        if not self.idx:
            return
        self.adopt()
        self.take_typing()
        self.sides["end"]["sames"] = [self.same_var.get()] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark()

    def show(self):
        super().show()
        on = any(self.ons) and bool(self.idx) and self.pct  # (Ticks: every note gets the same amount already)
        self.same_tick.state(["!disabled"] if on else ["disabled"])

    # ---- the hooks (subtract_window.SubtractWindow)

    def has_it(self, sh):
        return is_extended(sh)

    def own_part(self, sh):
        """Its Gate extend + an Add between step's mark for one of its own ("-": none), like Gate subtract's."""
        return [sh.get("extend"), (sh.get("between") or {}).get("ext", "-")]

    def put_own(self, sh, part):
        ext, mark = part
        if ext is None:
            sh.pop("extend", None)
        else:
            sh["extend"] = ext
        b = sh.get("between")
        if isinstance(b, dict):
            if mark == "-":
                b.pop("ext", None)
            else:
                b["ext"] = mark

    # ---- shapes extended in the other unit than the window's: left exactly as they are until a control is used,
    # then they take the window's unit + amount (self.foreign[i] = their own setting, else None)

    def adopt(self):
        foreign = getattr(self, "foreign", None)
        if not foreign or not any(foreign):
            return
        d = self.sides["end"]
        b = self.read(self.by_var.get())
        b = self.first_amount() if b is None else b
        for i, e in enumerate(foreign):
            if e is not None:
                d["bys"][i] = b
                d["use"][i] = True
        self.foreign = [None] * len(foreign)

    def put_all(self, what):
        self.adopt()
        super().put_all(what)

    def put_graph(self, key, value, mark=True):
        self.adopt()
        super().put_graph(key, value, mark)

    def on_box(self, key):
        self.adopt()
        super().on_box(key)

    def state(self):
        return json.dumps([self.ons, self.sides, getattr(self, "foreign", None)])

    def put_state(self, state):
        ons, sides, foreign = json.loads(state)
        self.foreign = foreign
        super().put_state(json.dumps([ons, sides]))

    def first_amount(self):
        return 50.0 if self.pct else 1 / self.app.ppq

    def uppq(self, ppq=None):
        return 1 if self.pct else ppq or self.app.ppq

    def most(self):
        return MOST_PCT if self.pct else MOST

    def amount_text(self, n):
        return tr("extend.pct_n", n=n) if self.pct else trn("n.ticks", n, n=n)

    def put_unit(self):
        if self.pct:
            self.by_unit.config(text=tr("extend.pct"))
        else:
            self.by_unit.config(text=unit_text(self.to_var.get() if self.vary_var.get() else self.by_var.get(),
                                               "subtract.ticks", "subtract.tick"))

    def made_of(self, i):
        foreign = getattr(self, "foreign", None)
        if foreign and i < len(foreign) and foreign[i] is not None:
            return json.loads(json.dumps(foreign[i]))
        side = self.side_of("end", i) or {"by": self.first_amount()}
        side = {k: v for k, v in side.items() if k not in ("off", "touch", "cuts")}
        if self.pct:
            side["unit"] = "pct"
            sames = self.sides["end"].get("sames") or []
            if i < len(sames) and sames[i]:
                side["same"] = True
        return side

    def load(self, start):
        """As Gate subtract's, the unit being the first extended shape's (default %); shapes in the other unit stay
        exactly as they are (self.foreign) until a control is used, then get the unit + amount shown (their numbers
        mean something else)."""
        exts = [t.get("extend") if is_extended(t) else None for t in self.tgts]
        got = next((e for e in exts if e), None)
        d = self.sides["end"]
        d["unit"] = "pct" if got is None or got.get("unit") == "pct" else "ticks"
        d["sames"] = [bool(e and e.get("same")) for e in exts]
        super().load(start)
        if start:
            self.use_first()
        self.foreign = [None] * len(exts)
        for i, e in enumerate(exts):
            if e and (e.get("unit") == "pct") != self.pct:
                d["bys"][i], d["owns"][i] = None, None
                self.foreign[i] = json.loads(json.dumps(e))
        if d["g"] and all(o is None for o in d["owns"]):
            d["g"] = None
        self.show_controls()

    def show_controls(self):
        super().show_controls()
        if hasattr(self, "unit_box"):
            self.unit_box.current(UNITS.index("pct" if self.pct else "ticks"))
        if hasattr(self, "same_tick"):
            sames = self.sides["end"].get("sames") or []
            self.same_var.set(bool(sames) and all(sames))
            self.same_tick.state(["alternate"] if any(sames) and not all(sames) else ["!alternate"])

    def pick_unit(self, unit):
        """Ticks <-> %: the numbers shown stay (10 ticks -> 10 %), every shape takes the unit."""
        if not self.idx or unit == ("pct" if self.pct else "ticks"):
            return self.show_controls()
        self.adopt()
        self.take_typing()
        d = self.sides["end"]
        ppq = self.app.ppq

        def moved(v):
            if v is None:
                return None
            return float(round(v * ppq)) if unit == "pct" else round(v) / ppq
        d["unit"] = unit
        d["bys"] = [moved(b) for b in d["bys"]]
        for g in [d["g"], *d["owns"]]:
            if g and "to" in g:
                g["to"] = min(moved(g["to"]), self.most())
        d["bys"] = [None if b is None else min(b, self.most()) for b in d["bys"]]
        self.show_controls()
        self.store()
        self.hist.mark()


def open_extend(app):
    w = app.extend_window
    if w:  # (already open: it follows the selection; the menu item puts Gate extend on)
        w.sync()
        if w.idx and not all(w.ons):
            w.on_var.set(True)
            w.put_all("on")
        w.lift()
        w.focus_set()
    elif target_indices(app):
        ExtendWindow(app)


def any_extended(app):
    return any(is_extended(app.shapes[i]) for i in app.sels if i < len(app.shapes))


