"""The Gate extend window (right-click "Gate extend..."; notes/extend.py): the selected shapes' notes end later, so
they run over into the notes after them like a run played by hand. The Gate subtract window's layout and behaviour
(subtract_window.SubtractWindow: live, follows the selection, settle, OK / Cancel, own Ctrl+Z, "Change across the
shape" graph) with one side only.

TYPED AMOUNTS (user 2026-11-22, PLAN.md stage B): the % SIGN decides, never the dropdown: 50 = 50 ticks, 50% = % of
each note's length, 50% + 10 = both (files/mathexpr.amount). Each amount in the window = [% or None, beats]
(None = no % typed). "Amount in" follows what's typed (Ticks / % / % + ticks; shapes that differ = Mixed); picking
Ticks or % turns every number into that unit (50% + 10 -> 60 ticks). Text that can't work = the box red + why."""

import math
import re
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import amount, fmt
from notes.extend import MOST, MOST_PCT, is_extended, is_pct, part_extend
from notes.gaterange import y_gate
from notes.subtract import ticks_of
from window import look
from window.subtract_window import SubtractWindow, side_data, target_indices
from window.widgets import Tooltip, show_mixed, unit_text

UNITS = ("pct", "ticks")  # "Amount in:" choices ("% + ticks" = only shown as its text, not in the list: user 2026-11-23)
_LAST_NUMBER = re.compile(r"(\d+(?:\.\d+)?)(\s*[%％]?\s*)$")


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


def value_of(ext, key):
    """A stored Gate extend's "by" / "to" as the window's [% or None, beats] (0 % with ticks = ticks only)."""
    if not is_pct(ext):
        return [None, float(ext[key])]
    p, b = float(ext[key]), float(ext.get("plus" if key == "by" else "plus_to") or 0.0)
    return [None if p == 0 and b > 0 else p, b]


def kind(v):
    return "ticks" if v[0] is None else "pct" if not v[1] else "both"


def step_text(text, delta):
    """A box's text with its LAST number stepped by delta (never under 0): 50% + 10 -> 50% + 11; None when the text
    doesn't end in a number."""
    m = _LAST_NUMBER.search(text)
    if not m:
        return None
    return text[:m.start(1)] + fmt(max(0.0, float(m.group(1)) + delta)) + m.group(2)


class ExtendWindow(SubtractWindow):
    KEY = "extend"
    T = "extend"
    ATTR = "extend_window"
    SIDES = ("end",)
    TOUCH = False
    BOX_WIDTH = 12

    def side_data(self):
        d = side_data()
        d["sames"] = []  # (per shape: "Same amount for every note"; kept while no %, used only with %)
        return d

    @property
    def pct(self):
        """Some amount shown has a % part ("Same amount for every note" can be used)."""
        sides = getattr(self, "sides", None)
        if not sides:
            return True
        return any(v is not None and v[0] is not None for v in self.values())

    def values(self):
        """Every amount the shapes use: their first ones, and the second where they change across."""
        return [v for vs in self.shape_values() for v in vs]

    def shape_values(self):
        """Per shape with an amount: the amounts it uses."""
        d = self.sides["end"]
        out = []
        for i, b in enumerate(d["bys"]):
            if b is None:
                continue
            g = d["g"] if d["g_all"] or not d["owns"][i] else d["owns"][i]
            out.append([b, g["to"]] if d["varies"][i] and g and "to" in g else [b])
        return out

    def shape_kinds(self):
        """Each shape's kind of amount: "ticks", "pct" or "both" (10 ticks changing to 50% = both)."""
        out = set()
        for vs in self.shape_values():
            ks = {kind(v) for v in vs}
            out.add(ks.pop() if len(ks) == 1 else "both")
        return out

    def extra_rows(self, box):
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        self.unit_label = ttk.Label(row, text=tr("extend.unit"))
        self.unit_label.pack(side="left")
        self.unit_box = ttk.Combobox(row, values=[tr("extend.unit_" + u) for u in UNITS], state="readonly", width=24)
        self.unit_box.pack(side="left", padx=4)
        self.unit_box.bind("<<ComboboxSelected>>", lambda e: self.pick_unit(UNITS[self.unit_box.current()]))
        self.unit_tip = Tooltip(self.unit_box, tr("extend.unit_tip"))
        Tooltip(self.unit_label, tr("extend.unit_tip"))
        self.same_var = tk.BooleanVar(value=False)
        self.same_tick = ttk.Checkbutton(box, text=tr("extend.same"), variable=self.same_var, command=self.put_same)
        self.same_tick.pack(anchor="w", pady=(6, 0))
        Tooltip(self.same_tick, tr("extend.same_tip"))

    def put_same(self):
        """"Same amount for every note" clicked: every shape takes it (a half-ticked one: a click = all on)."""
        if not self.idx:
            return
        self.take_typing()
        self.sides["end"]["sames"] = [self.same_var.get()] * len(self.idx)
        self.show_controls()
        self.store()
        self.hist.mark()

    def show(self):
        super().show()
        on = any(self.ons) and bool(self.idx) and self.pct  # (no %: every note gets the same amount already)
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

    def as_shown(self, s):
        return dict(s, by=value_of(s, "by"), **({"to": value_of(s, "to")} if "to" in s else {}))

    def hook_scrub(self, scrub):
        scrub.step_text = step_text

    def first_amount(self):
        return [50.0, 0.0]

    def scaled(self, v, k):
        return [None if v[0] is None else min(v[0] * k, MOST_PCT), min(v[1] * k, MOST)]

    def text_of(self, v):
        p, b = v
        ticks = ticks_of(b, self.app.ppq)
        parts = [] if p is None else [fmt(p) + "%"]
        if ticks or p is None:
            parts.append(fmt(ticks))
        return " + ".join(parts)

    def read(self, text, ppq=None):
        """Typed text -> [% or None, beats] (whole % and whole ticks, each at most its most), or None (self.why = the
        reason to show, "" = none: an empty box)."""
        ppq = ppq or self.app.ppq
        self.why = ""
        if not text.strip():
            return None
        try:
            p, t = amount(text)
        except ValueError as e:
            self.why = str(e)
            return None
        if p < 0 or t < 0:
            self.why = tr("extend.below_zero")
            return None
        p = None if "%" not in text and "％" not in text or p == 0 and t > 0 else min(math.floor(p + 0.5), MOST_PCT)
        return [None if p is None else float(p), min(math.floor(t + 0.5) / ppq, MOST)]

    def refused(self, box, var):
        if not getattr(self, "why", ""):
            return super().refused(box, var)
        box.config(style="Bad.TEntry")  # (left as typed, red, with why under it: user 2026-11-22)
        if not hasattr(self, "why_label"):
            row = self.by_box.master
            self.why_label = ttk.Label(row.master, foreground=look.ERROR, wraplength=int(420 * self.s), justify="left")
        self.why_label.config(text=self.why)
        if not self.why_label.winfo_manager():
            self.why_label.pack(anchor="w", after=self.by_box.master, pady=(2, 0))

    def accepted(self, box):
        box.config(style="TEntry")
        if hasattr(self, "why_label") and self.why_label.winfo_manager():
            self.why_label.pack_forget()

    def gates(self):
        a, b = self.ends()
        if kind(a) == kind(b) == "ticks":
            return ticks_of(a[1], self.app.ppq), ticks_of(b[1], self.app.ppq)
        if kind(a) in ("pct",) and kind(b) == "pct":
            return int(a[0]), int(b[0])
        return None  # (% and ticks both: no pale steps, the labels say it)

    def label_at(self, y):
        a, b = self.ends()
        ppq = self.app.ppq
        parts = []
        if a[0] is not None or b[0] is not None:
            parts.append(tr("extend.pct_n", n=y_gate(int(a[0] or 0), int(b[0] or 0), y)))
        ta, tb = ticks_of(a[1], ppq), ticks_of(b[1], ppq)
        if ta or tb or not parts:
            n = y_gate(ta, tb, y)
            parts.append(trn("n.ticks", n, n=n))
        return " + ".join(parts)

    def widest_label(self):
        return tr("extend.pct_n", n=MOST_PCT) + " + " + trn("n.ticks", 99999, n=99999)

    def amount_text(self, n):
        return trn("n.ticks", n, n=n)

    def put_unit(self):
        """The word after the boxes: "of each note" (only %), ticks (a ticks part in every box shown), or both."""
        texts = [self.by_var.get()] + ([self.to_var.get()] if self.vary_var.get() else [])
        kinds, t = set(), 0
        for text in texts:
            try:
                t = amount(text)[1]
            except ValueError:
                t = 0
            kinds.add("pct" if ("%" in text or "％" in text) and not t else "ticks")
        if kinds == {"pct"}:
            self.by_unit.config(text=tr("extend.pct_word"))
        elif kinds == {"ticks"}:
            self.by_unit.config(text=unit_text(fmt(t), "subtract.ticks", "subtract.tick"))
        else:
            self.by_unit.config(text=tr("extend.both_word"))

    def made_of(self, i):
        side = self.side_of("end", i) or {"by": self.first_amount()}
        side = {k: v for k, v in side.items() if k not in ("off", "touch", "cuts")}
        by, to = side["by"], side.get("to")
        if any(v is not None and v[0] is not None for v in (by, to)):
            side["unit"] = "pct"
            side["by"] = by[0] or 0.0
            if by[1]:
                side["plus"] = by[1]
            if to is not None:
                side["to"] = to[0] or 0.0
                if to[1]:
                    side["plus_to"] = to[1]
            sames = self.sides["end"].get("sames") or []
            if i < len(sames) and sames[i]:
                side["same"] = True
        else:
            side["by"] = by[1]
            if to is not None:
                side["to"] = to[1]
        return side

    def load(self, start):
        """As Gate subtract's; each shape's amounts shown as typed (% and / or ticks)."""
        exts = [t.get("extend") if is_extended(t) else None for t in self.tgts]
        self.sides["end"]["sames"] = [bool(e and e.get("same")) for e in exts]
        super().load(start)

    def show_controls(self):
        super().show_controls()
        if hasattr(self, "unit_box"):
            kinds = self.shape_kinds() or {"pct"}  # (nothing extended yet: the % default)
            if kinds == {"both"}:  # (shown, not a choice)
                self.unit_box.set(tr("extend.unit_both"))
                if tr("extend.unit_both") not in getattr(self.unit_box, "fit_also", ()):
                    self.unit_box.fit_also = (tr("extend.unit_both"), tr("widgets.mixed"))
                show_mixed(self.unit_box, False, self.unit_tip, tr("extend.unit_tip"))
            elif len(kinds) == 1:
                self.unit_box.current(UNITS.index(kinds.pop()))
                show_mixed(self.unit_box, False, self.unit_tip, tr("extend.unit_tip"))
            else:
                show_mixed(self.unit_box, True, self.unit_tip, tr("extend.unit_tip"))
        if hasattr(self, "same_tick"):
            sames = self.sides["end"].get("sames") or []
            self.same_var.set(bool(sames) and all(sames))
            self.same_tick.state(["alternate"] if any(sames) and not all(sames) else ["!alternate"])

    def pick_unit(self, unit):
        """Ticks or % picked: every number shown takes that unit (50% + 10 -> 60 ticks, or 60%)."""
        if not self.idx:
            return self.show_controls()
        self.take_typing()
        d = self.sides["end"]
        ppq = self.app.ppq

        def moved(v):
            if v is None:
                return None
            n = (v[0] or 0) + ticks_of(v[1], ppq)
            return [None, min(n / ppq, MOST)] if unit == "ticks" else [float(min(n, MOST_PCT)), 0.0]
        d["bys"] = [moved(b) for b in d["bys"]]
        for g in [d["g"], *d["owns"]]:
            if g and "to" in g:
                g["to"] = moved(g["to"])
        self.accepted(self.by_box)
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
