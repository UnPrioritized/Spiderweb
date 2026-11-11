"""The side panel's Recolour row, under Colours (notes/recolour.py): which colour each note of a shape has, its notes
kept where they are. Shares… opens window/shares_window.py."""

import tkinter as tk
import weakref
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import ROUND
from notes.engine import pasted_notes
from notes.steps import COLOURS, FX, cut_needed, hidden_after, upto_view
from notes.recolour import BY, KEEP, MODES, SET, can_recolour, clean_recolour, colour_sets, edited_step, enough, \
    is_recoloured, new_seed, order_of, ordered, palettes, step_place, turns_of, wanted, without_recolour
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, show_mixed, unchanged

RECOLOUR_CHOICES = [(None, tr("recolour.off"))] + [(m, tr("recolour." + m)) for m in MODES]
BY_CHOICES = [(b, tr("recolour.by_" + b)) for b in BY]
AMOUNT_WORDS = {"shuffle": ("recolour.amount", "recolour.amount_tip"),
                "scatter": ("recolour.amount", "recolour.amount_tip_scatter"),
                "clumps": ("recolour.rough", "recolour.amount_tip_clumps")}
START_AMOUNT = {"shuffle": 100.0, "scatter": 50.0, "clumps": 20.0}
EDIT_WINDOWS = {"stir": "stir_window", "warp": "warp_window", "liquify": "liquify_window", "mode": "shares_window",
                "sustain": "sustain_window", "extend": "extend_window", "subtract": "subtract_window"}


def edit_windows(kind):
    """The App attributes of the windows editing steps of kind ("fx" = a note tool page: any note tool window)."""
    if kind == FX:
        from window.tool_window import TOOL_WINDOWS
        return TOOL_WINDOWS
    return (EDIT_WINDOWS[kind],) if kind in EDIT_WINDOWS else ()


def all_edit_windows():
    from window.tool_window import TOOL_WINDOWS
    return tuple(EDIT_WINDOWS.values()) + TOOL_WINDOWS + ("properties_window",)


class RecolourPanel:
    """Mixed into App."""

    # ---- one colour step edited: the steps after it skipped (Order of colour settings, PLAN.md stage C)
    # (user 2026-11-10: while an earlier step is edited the later ones are FULLY OFF, so it is as quick as editing
    # the last one; worked out again once the user leaves it: another shape picked, another Stir tab, its window
    # closed, Play, save / export / copy, Preview all. What leaves Spiderweb (App.outgoing) is always whole.)
    colour_edit = None  # ((kind, n) = the step being edited, the selection it started with) or None
    edit_shown = False  # the notes made last (app.rendered) skip steps so (App.shapes_changed)

    def colour_edit_set(self, step):
        """step = ("stir", tab) / ("warp", 0) / ("liquify", 0) / ("mode", 0) of the selected shapes was just
        changed (None: no step alone, e.g. a step added / taken out, a Reverse: the edit ends). Every other step too
        (PLAN.md "Properties" stage C, user 2026-11-11): ("fx", k) = the note tool page at sh["fx"][k], ("sustain",
        0), ("extend", 0), ("subtract", 0). Call before shapes_changed: it makes the notes as shown. Playing: never
        (heard whole). Only while the step's own window is open (Stir / Warp / Liquify / Shares, a note tool window,
        Sustain / Gate extend / Gate subtract: the side panel's Recolour row alone edits nothing for long)."""
        if step is not None and (self.player.running or not self.sels or not self.edit_window_open(step[0])):
            step = None
        new = None if step is None else (tuple(step), frozenset(self.sels))
        if new != self.colour_edit:
            self.colour_edit = new
            self.props_edit_ended()
            self.after_idle(self.sync_edit_lines)

    def props_edit_ended(self):
        """The Properties window's "a step of this kind is edited here" mark goes once that edit ends (else the side
        panel's Recolour row went on hiding later steps long after: it alone never edits)."""
        pw = getattr(self, "properties_window", None)
        e = self.colour_edit
        if pw is not None and pw.editing is not None and (e is None or e[0][0] != pw.editing):
            pw.editing = None

    def edit_window_open(self, kind):
        """Is the window that edits steps of kind open? (The Properties window too, while its own box for a step of
        kind was the last thing changed there: PLAN.md "Properties" stage D.)"""
        pw = getattr(self, "properties_window", None)
        return any(getattr(self, name, None) for name in edit_windows(kind)) or bool(pw and pw.editing == kind)

    def colour_edit_drop(self, kind):
        """The edit of a step of kind ends with no notes made here (the caller makes them next: a Cancel)."""
        e = self.colour_edit
        if e is not None and e[0][0] == kind:
            self.colour_edit = None
            self.props_edit_ended()
            self.sync_edit_lines()

    def colour_edit_start(self, step):
        """A stroke / drag of step starts on the selected shapes (roll_liquify / roll_warp): the steps after it are
        skipped from now on. True = that hid steps not hidden before (the notes were made again here)."""
        self.colour_edit_set(step)
        if self.colour_hidden() and not self.edit_shown:
            self.shapes_changed(now=True)
            return True
        return False

    def colour_edit_leave(self, kind=None, keep=None):
        """The user left the step being edited (kind: only when it is of that kind; keep: not when it is that very
        step, e.g. the Stir tab shown): the steps after it are worked out again."""
        e = self.colour_edit
        if e is None or kind is not None and e[0][0] != kind or keep is not None and e[0] == tuple(keep):
            return
        self.colour_edit = None
        self.props_edit_ended()
        self.sync_edit_lines()
        if self.edit_shown:
            self.shapes_changed(now=True)

    def edit_tag(self, sh, i=None):
        """The step of shape sh (place i in self.shapes) being edited now while steps after it are skipped (its tag
        (kind, n), notes/steps.py), or None (nothing skipped for it)."""
        e = self.colour_edit
        if e is None or self.player.running or frozenset(self.sels) != e[1]:
            return None
        if i is None:
            i = next((j for j, s in enumerate(self.shapes) if s is sh), None)
        if i not in e[1]:
            return None
        if e[0][0] in COLOURS and not is_recoloured(sh):
            return None
        return e[0] if hidden_after(sh, e[0]) else None

    def edit_place(self, sh, i=None):
        """Where the colour step being edited sits in shape sh's Recolour order while the colour steps after it are
        skipped by the Recolour's own cut (rc "_upto"), or None (nothing skipped so, or skipped by the runner:
        edit_view's sh["_upto"])."""
        t = self.edit_tag(sh, i)
        if t is None or t[0] not in COLOURS or cut_needed(sh, t):
            return None
        rc = clean_recolour(sh["recolour"])
        j = step_place(rc, *t)
        return j if j is not None and j < len(order_of(rc)) - 1 else None

    def edit_view(self, sh, i=None):
        """sh as its notes are shown: without the steps after the one being edited (colour effects only after it: rc
        "_upto", recolour.steps_of; anything else: sh["_upto"], made by the runner, steps.expanded)."""
        t = self.edit_tag(sh, i)
        if t is None:
            return sh
        if t[0] in COLOURS and not cut_needed(sh, t):
            j = self.edit_place(sh, i)
            return sh if j is None else dict(sh, recolour=dict(sh["recolour"], _upto=j))
        return upto_view(sh, t)

    def edit_shapes(self):
        """self.shapes as their notes are shown (edit_view each; self.shapes itself when nothing is skipped)."""
        e = self.colour_edit
        if e is None or self.player.running or frozenset(self.sels) != e[1]:
            return self.shapes
        out = [self.edit_view(sh, i) if i in e[1] else sh for i, sh in enumerate(self.shapes)]
        return out if any(a is not b for a, b in zip(out, self.shapes)) else self.shapes

    def colour_hidden(self):
        """Some shape's later colour steps are skipped by the step being edited now (edit_shown: in the notes made
        last)."""
        return self.edit_shapes() is not self.shapes

    def edit_hidden_count(self, kind):
        """How many later colour steps are skipped while a step of kind is edited (the most of any shape), 0."""
        return len(self.edit_hidden_steps(kind))

    def edit_hidden_steps(self, kind):
        """The later steps skipped while a step of kind is edited, as (tag, shape) in order (tag = (kind, n), n = the
        n-th of its kind: "stir" 1 = Stir tab 2; ("fx", k) = sh["fx"][k]), of the shape skipping the most; [] =
        none."""
        e = self.colour_edit
        if e is None or e[0][0] != kind:
            return []
        most = []
        for i in sorted(e[1]):
            if i < len(self.shapes) and self.edit_tag(self.shapes[i], i) is not None:
                got = [(t, self.shapes[i]) for t in hidden_after(self.shapes[i], e[0])]
                if len(got) > len(most):
                    most = got
        return most

    def sync_edit_lines(self):
        """The windows' "later steps hidden" lines (window/edit_line.py)."""
        for name in all_edit_windows():
            w = getattr(self, name, None)
            line = getattr(w, "edit_line", None) if w else None
            if line is not None and w.winfo_exists():
                line.sync()

    def _build_recolour(self):
        """Recolour [Off / Shuffle] [New shuffle]; under it, while on: Amount [n] %, [Per note] [Shares…], and
        Mix across sets while a shape has more than one colour set."""
        box = self.recolour_box = ttk.Frame(self.settings)
        box.pack(fill="x", pady=(4, 0), after=self.colours_box)
        c = ttk.Frame(box)
        c.pack(anchor="w")
        self.recolour_word = ttk.Label(c, text=tr("recolour.recolour"))  # (one column: app._build_shape_settings)
        self.recolour_word.pack(side="left")
        self.recolour_mode = ttk.Combobox(c, values=[t for _, t in RECOLOUR_CHOICES], state="readonly",
                                          width=max(len(t) for _, t in RECOLOUR_CHOICES))
        self.recolour_mode.pack(side="left", padx=(5, 4))
        self.recolour_mode.bind("<<ComboboxSelected>>", lambda ev: (self.on_recolour("mode"), self.roll.focus_set()))
        self.recolour_tip = Tooltip(self.recolour_mode, tr("recolour.tip"))
        self.recolour_again = ttk.Button(c, text=tr("recolour.again"), command=lambda: self.on_recolour("seed"))
        Tooltip(self.recolour_again, tr("recolour.again_tip"))
        # (Stir… goes on a line of its own under the dropdown when the row has no room: "Stir… (on)" beside New
        # shuffle was cut, user 2026-11-03; fit_stir)
        # (Liquify… beside it, user 2026-11-07: the two move together, recolour_tools)
        self.recolour_line, self.recolour_stir_line = c, ttk.Frame(box)
        self.recolour_tools = ttk.Frame(box)
        self.recolour_tools.pack(in_=c, side="left", padx=(4, 0))
        self.recolour_stir = ttk.Button(self.recolour_tools, text=tr("stir.button"), command=self.open_stir)
        self.recolour_stir.pack(side="left")
        Tooltip(self.recolour_stir, tr("stir.button_tip"))
        # (Warp… between them: the order they bend the colours in, user 2026-11-09)
        self.recolour_warp = ttk.Button(self.recolour_tools, text=tr("warp.button"), command=self.open_warp)
        self.recolour_warp.pack(side="left", padx=(4, 0))
        Tooltip(self.recolour_warp, tr("warp.button_tip"))
        self.recolour_liquify = ttk.Button(self.recolour_tools, text=tr("liquify.button"), command=self.open_liquify)
        self.recolour_liquify.pack(side="left", padx=(4, 0))
        Tooltip(self.recolour_liquify, tr("liquify.button_tip"))
        # (Properties… (was Effect order…) is NOT here: it read like one more effect, user 2026-11-11; it sits beside Preview all in the
        # Stir / Warp / Liquify / Shares windows (edit_line.EditLine) + the shape's right-click menu)
        box.bind("<Configure>", lambda e: self.fit_stir(), add="+")
        self.stir_narrow = False  # (held on their own line while the side panel measures: side_narrows)

        def narrow(on):
            self.stir_narrow = on
            self.fit_stir()
        self.side_narrows.append((lambda: narrow(True), lambda: narrow(False)))
        rows = self.recolour_row = ttk.Frame(box)  # (only shown while Recolour is on)
        row = ttk.Frame(rows)
        row.pack(anchor="w")
        lb = ttk.Label(row, text=tr("recolour.amount"))
        lb.pack(side="left")
        self.recolour_var = tk.StringVar()
        e = self.recolour_entry = ttk.Entry(row, textvariable=self.recolour_var, width=7)
        e.pack(side="left", padx=4)
        Scrub(self, [(e, self.recolour_var, lambda: self.on_recolour("amount"))], (1, 10, 0.1), 0, 100, label=lb,
              drag_box=True)
        ttk.Label(row, text="%").pack(side="left")
        self.recolour_amount_word = lb
        self.recolour_amount_tip = Tooltip(lb, tr("recolour.amount_tip"))
        e.bind("<Return>", lambda ev: self.on_recolour("amount"))
        leave_box(self, e, self.recolour_var, lambda left: self.on_recolour("amount", left and e))
        row = ttk.Frame(rows)
        row.pack(anchor="w", pady=(2, 0))
        self.recolour_by = ttk.Combobox(row, values=[t for _, t in BY_CHOICES], state="readonly",
                                        width=max(len(t) for _, t in BY_CHOICES))
        self.recolour_by.pack(side="left")
        self.recolour_by.bind("<<ComboboxSelected>>", lambda ev: (self.on_recolour("by"), self.roll.focus_set()))
        self.recolour_by_tip = Tooltip(self.recolour_by, tr("recolour.by_tip"))
        b = self.recolour_shares = ttk.Button(row, text=tr("recolour.shares"), command=self.open_shares)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("recolour.shares_tip"))
        self.recolour_mix = tk.BooleanVar()
        self.recolour_mix_tick = ttk.Checkbutton(rows, text=tr("recolour.mix"), variable=self.recolour_mix,
                                                 command=lambda: self.on_recolour("mix"))
        self.recolour_mix_tip = Tooltip(self.recolour_mix_tick, tr("recolour.mix_tip", most=SET))
        # (Liquify's show tick + Strength live in the Liquify window, like Warp's: user 2026-11-09)

    def fit_stir(self):
        """Stir… + Liquify… beside the dropdown (and New shuffle) when the row has room for them, else on their own
        line under the dropdown. stir_narrow = on their own line anyway (the side panel's double-click measures the
        smallest width with nothing cut)."""
        narrow = self.stir_narrow
        box, c, line, b = self.recolour_box, self.recolour_line, self.recolour_stir_line, self.recolour_tools
        room = box.winfo_width()
        if room <= 1 and not narrow:
            return
        def wide(w):
            pad = w.pack_info().get("padx", 0)
            return w.winfo_reqwidth() + sum(pad) if isinstance(pad, tuple) else w.winfo_reqwidth() + 2 * int(pad)
        others = sum(wide(w) for w in c.pack_slaves() if w is not b)
        fits = not narrow and others + b.winfo_reqwidth() + 4 <= room
        if fits == (b.winfo_manager() == "pack" and b.pack_info().get("in") == c):
            return
        if fits:
            b.pack(in_=c, side="left", padx=(4, 0))
            line.pack_forget()
        else:
            line.pack(anchor="w", after=c, pady=(2, 0), padx=(wide(self.recolour_word) + 5, 0))
            b.pack(in_=line, side="left", padx=(0, 0))

    def open_shares(self):
        from window.shares_window import open_shares
        open_shares(self)

    def open_stir(self):
        from window.stir_window import open_stir
        open_stir(self)

    def open_liquify(self):
        from window.liquify_window import open_liquify
        open_liquify(self)

    def open_warp(self):
        from window.warp_window import open_warp
        open_warp(self)

    def open_properties(self):
        from window.properties_window import open_properties
        open_properties(self)

    def recolour_targets(self):
        """The selected shapes Recolour works on (not a Hz bass); nothing selected: none (no setting for new
        shapes)."""
        return [self.shapes[i] for i in sorted(self.sels) if can_recolour(self.shapes[i])]

    def set_most(self):
        """How many colours a colour set holds: 15, 16 while Multi channel uses channel 10."""
        return 16 if self.all16() else 15

    def colour_owns(self, sh):
        """Each colour set's own colours of sh's notes before Recolour (the notes as remembered: no new work after a
        change)."""
        notes, tracks = self.notes_tracks(without_recolour(sh))
        if tracks is None:  # (all on one colour)
            return [np.zeros(1, np.int64)] if len(notes) else []
        if not len(tracks):
            return []
        # (worked out once per remembered notes: the Stir window asks at every mouse move, 78 ms a move on 1 M
        # notes, hunt 11-04 T1; a WEAK link to the tracks: tracks the notes cache forgot go, their colours too
        # (it kept every old version's tracks: ~60 MB an edit at 2 M notes); a live link = the id is never another's)
        cache = self.__dict__.setdefault("_owns_cache", {})
        got = cache.get(id(tracks))
        if got is not None and got[0]() is tracks:
            return got[1]
        owns = colour_sets(np.asarray(tracks, np.int64) % ROUND)[1]
        for k in [k for k, v in cache.items() if v[0]() is None]:
            del cache[k]
        if len(cache) >= 64:
            del cache[next(iter(cache))]
        cache[id(tracks)] = (weakref.ref(tracks), owns)
        return owns

    def colour_count(self, sh):
        """How many colours sh's notes have before Recolour (the most in one set)."""
        return max((len(o) for o in self.colour_owns(sh)), default=1)

    def sync_recolour(self):
        tgts = self.recolour_targets()
        act = [clean_recolour(t.get("recolour")) for t in tgts]
        stir_on = any(r and r.get("stir") for r in act)
        on = [r if r and r["mode"] != KEEP else None for r in act]  # (Off with only a Stir: shown as Off)
        modes = {r["mode"] if r else None for r in on}
        live = [r for r in on if r]
        self._loading = True
        mode = next(iter(modes)) if len(modes) == 1 else None
        self.recolour_mode.current([v for v, _ in RECOLOUR_CHOICES].index(mode))
        same_or_blank(self.recolour_entry, self.recolour_var, [fmt(r["amount"]) for r in live] or ["100"])
        bys = {r.get("by", "note") for r in live} or {"note"}
        if len(bys) == 1:
            self.recolour_by.current(BY.index(next(iter(bys))))
        show_mixed(self.recolour_by, len(bys) > 1, self.recolour_by_tip, tr("recolour.by_tip"))
        mixes = {bool(r.get("mix")) for r in live}
        self.recolour_mix.set(mixes == {True})
        self.recolour_mix_tick.state(["alternate" if len(mixes) > 1 else "!alternate"])
        self._loading = False
        # (notes being made in the background after an edit (a big turned paste: ~0.7 s) and these shapes' colours
        # not known yet: what needs them waits for the notes (shapes_changed calls this again then), so the edit
        # doesn't make them here first, freezing the window)
        late = self._bg_notes and any(r and self.notes_known(without_recolour(t)) is None for t, r in zip(tgts, act))
        self._recolour_owed = late
        if late:
            self.recolour_mode.config(state="readonly" if tgts else "disabled")
        else:
            # (Off + a Stir counts too: it says why the stir shows nothing, hunt 2026-11-02)
            lonely, single = self.recolour_trouble(tgts, act)
            owns = [self.colour_owns(t) if r else [] for t, r in zip(tgts, on)]
            self.recolour_mode.config(state="readonly" if tgts else "disabled",
                                      style="Gap.TCombobox" if lonely or single else "TCombobox")
            show_mixed(self.recolour_mode, len(modes) > 1, self.recolour_tip,
                       tr("recolour.tip") + (tr("recolour.needs") if lonely else tr("recolour.one") if single else ""))
        self.recolour_entry.config(state="normal" if live else "disabled", style="TEntry")
        kind = mode if mode in AMOUNT_WORDS else "shuffle"  # (Mixed: the plain Amount)
        self.recolour_amount_word.config(text=tr(AMOUNT_WORDS[kind][0]))
        self.recolour_amount_tip.text = tr(AMOUNT_WORDS[kind][1])
        self.recolour_stir.config(text=tr("stir.button_on" if stir_on else "stir.button"))
        self.recolour_stir.state(["!disabled" if tgts else "disabled"])
        self.recolour_warp.state(["!disabled" if tgts else "disabled"])
        self.recolour_liquify.state(["!disabled" if tgts else "disabled"])
        shown = bool(live)
        if shown != bool(self.recolour_row.winfo_manager()):
            if shown:
                self.recolour_row.pack(anchor="w", padx=(self.cycle_rows_pad, 0), pady=(1, 0))
            else:
                self.recolour_row.pack_forget()
        if shown != bool(self.recolour_again.winfo_manager()):
            if shown:
                self.recolour_again.pack(side="left", after=self.recolour_mode)
            else:
                self.recolour_again.pack_forget()
        self.recolour_box.after_idle(self.fit_stir)  # (New shuffle / "(on)" changed the room the row needs)
        if late:
            return
        sets = shown and any(len(o) > 1 for o in owns)  # (more than one colour set: Mix across sets)
        if sets != bool(self.recolour_mix_tick.winfo_manager()):
            if sets:
                self.recolour_mix_tick.pack(anchor="w", pady=(2, 0))
            else:
                self.recolour_mix_tick.pack_forget()
        self.recolour_mix_tip.text = tr("recolour.mix_tip", most=SET)
        for w in (getattr(self, "shares_window", None), getattr(self, "stir_window", None),
                  getattr(self, "warp_window", None), getattr(self, "liquify_window", None),
                  getattr(self, "properties_window", None)):
            if w:
                w.sync()

    def recolour_trouble(self, tgts, rcs):
        """(lonely, single) for shapes tgts with Recolours rcs (clean; None = Off): lonely = Channels isn't Multi
        channel, so a Recolour on (a Domino paste works in every mode, hunt 2026-11-01) changes nothing; single =
        one has nothing to swap (e.g. one colour, hunt 2026-10-25). Stir window: rcs = what a stir would make."""
        lonely = any(rcs) and self.channel_mode.get() != "auto" and not all(
            pasted_notes(t) for t, r in zip(tgts, rcs) if r)
        single = not lonely and any(
            r and not enough(palettes(self.colour_owns(t), None if r["mode"] == KEEP else wanted(r), self.set_most()),
                             r.get("mix") and r["mode"] != KEEP) for t, r in zip(tgts, rcs))
        return lonely, single

    def on_recolour(self, what, left=None):
        """The Recolour dropdown (what = "mode"), the Amount box ("amount"), New shuffle ("seed"), Per note / chord /
        run ("by") or Mix across sets ("mix"). Only what was changed goes to each shape (several picked keep their
        own amounts). left: the box that was left, nothing unless its number changed (leave_box)."""
        if self._loading or str(self.recolour_mode.cget("state")) == "disabled" or (
                left is not None and unchanged(left)):
            return
        if what == "mode":
            i = self.recolour_mode.current()
            if i < 0:  # (still Mixed: nothing picked)
                return
            mode = RECOLOUR_CHOICES[i][0]
        elif what == "amount":
            e, var = self.recolour_entry, self.recolour_var
            if not var.get().strip() and getattr(e, "blank_from", None):
                return
            try:
                amount = calc(var.get().strip().rstrip("%"))
                ok = 0 <= amount <= 100
            except ValueError:
                ok = False
            if not ok:
                return bad(e)
            good(e)
            amount = round(amount, 3)
        elif what == "by":
            i = self.recolour_by.current()
            if i < 0:
                return
            by = BY[i]
        elif what == "mix":
            self.recolour_mix_tick.state(["!alternate"])
            mix = self.recolour_mix.get()

        def changed(old, _sh=None):
            if what == "mode":
                if mode is None:  # (Off: a Stir / Warp / Liquify / Reverse step stays, on the colours as they are)
                    return dict(old, mode=KEEP) if old and (old.get("stir") or old.get("liquify")
                                                            or old.get("warp") or old.get("stir_held")
                                                            or turns_of(old)) else None
                if old and old["mode"] == mode:
                    return old
                # (another mode: its own Amount to start from, the word means another thing there)
                return dict(old or {"seed": new_seed()}, mode=mode, amount=START_AMOUNT[mode])
            if not old or old["mode"] == KEEP:
                return old
            if what == "amount":
                return dict(old, amount=amount)
            if what == "by":
                return dict({k: v for k, v in old.items() if k != "by"}, **({"by": by} if by != "note" else {}))
            if what == "mix":
                return dict({k: v for k, v in old.items() if k != "mix"}, **({"mix": True} if mix else {}))
            return dict(old, seed=new_seed())

        if self.change_recolour(changed, tr("recolour.undo_again" if what == "seed" else "recolour.undo")) and \
                what == "mode" and mode is not None:
            self.tips.show("recolour", wait=True)  # (the first time: what it does)

    def change_recolour(self, changed, name, push=True):
        """Each selected shape's Recolour = changed(its old one, clean; the shape); one undo step named name (push
        False: part of the step taken before). False when nothing changed."""
        tgts = self.recolour_targets()
        olds = [clean_recolour(t.get("recolour")) for t in tgts]
        new = []
        for o, t in zip(olds, tgts):
            c = changed(o, t)
            new.append(ordered(o, clean_recolour(c)) if c else None)  # (new colour steps at the end of the order)
        if all(o == n for o, n in zip(olds, new)):
            self.sync_recolour()
            return False
        if push:
            self.push_undo(name=name)
        for t, r in zip(tgts, new):
            if r:
                t["recolour"] = r
            else:
                t.pop("recolour", None)
        # (one colour step changed: the ones after it skipped while it is edited, PLAN.md stage C)
        self.colour_edit_set(next((s for s in map(edited_step, olds, new) if s), None))
        self.shapes_changed()
        self.sync_recolour()
        self.schedule_autosave()
        return True
