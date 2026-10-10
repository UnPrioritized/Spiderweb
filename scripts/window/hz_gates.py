"""The Hz bass window's settings for its notes and their shape: gates (Auto / mixed / fixed, each note's own), tune,
Pitch, the Effects / Red line toggles, and every change saved into the shape as one undo step."""

import copy
import json
import math
import tkinter as tk
from tkinter import messagebox, ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import BOX_STROKE, SPAM_FILLS, box_frame, custom_settings
from notes.hzbass import (AUTO, AUTO_MOST, EXTRAS, FULL_LENGTH, HZ_DEFAULTS, LENGTH_BEATS, TUNE, all_tones, clean_fit,
                          clean_from, clean_extra, clean_fx, clean_length, clean_lfo, clean_line, clean_loop, clean_off,
                          clean_sustain, clean_tones, fit_length, held_fixed, left_edge, length_ticks, sound_span)
from window import look
from window.hz_effects import AMOUNT
from window.widgets import Scrub, bad, good, remember_place


GATE_MODES = ("auto", "mixed", "fixed")  # the Gates dropdown's choices, in order
LENGTH_UNITS = ("pct", "ticks")  # the Note length's units, in order (hzbass.clean_length)


def gate_mode(hz):
    """A Hz bass's gates: "mixed", "fixed" or "auto"."""
    hz = hz or {}
    return "fixed" if hz.get("fixed") else "auto" if hz.get("auto") is not None else "mixed"


def ask_live(win, app, prompt, value, lo, hi, steps, on_change):
    """A small window asking for a number (a note's tune / Auto threshold, in cents): its box can be typed in,
    stepped (Up / Down, wheel) or dragged sideways, its label too (user). on_change(value) for every valid value
    on the way, so it can be tried out. Returns the value (OK / Enter) or None (Cancel / Escape / closed)."""
    top = tk.Toplevel(win)
    top.title(tr("hz.window_title"))
    top.transient(win)
    top.resizable(False, False)
    f = ttk.Frame(top, padding=10)
    f.pack(fill="both")
    ttk.Label(f, text=prompt, justify="left").pack(anchor="w")
    row = ttk.Frame(f)
    row.pack(anchor="w", pady=(8, 8))
    var = tk.StringVar(value=fmt(value))
    entry = ttk.Entry(row, textvariable=var, width=9)
    entry.pack(side="left")
    lb = ttk.Label(row, text=tr("panel_custom.hz_cents"), foreground=look.HINT)
    lb.pack(side="left", padx=(4, 0))
    Scrub(app, [(entry, var, None)], steps, lo, hi, label=lb, drag_box=True)
    got = {"value": None}

    def read():
        try:
            v = float(calc(var.get()))
        except (ValueError, ZeroDivisionError):
            return None
        return v if lo <= v <= hi else None

    def changed(*_):
        v = read()
        if v is None:
            bad(entry, typing=True)
        else:
            good(entry)
            on_change(v)

    def ok(e=None):
        v = read()
        if v is None:  # (back to its last good value: tried at once, OK again closes it)
            bad(entry)
            return
        on_change(v)
        got["value"] = v
        top.destroy()

    var.trace_add("write", changed)
    btns = ttk.Frame(f)
    btns.pack(anchor="e")
    ttk.Button(btns, text=tr("hz.live_ok"), command=ok).pack(side="left")
    ttk.Button(btns, text=tr("hz.live_cancel"), command=top.destroy).pack(side="left", padx=(6, 0))
    top.bind("<Return>", ok)
    top.bind("<Escape>", lambda e: top.destroy())
    for k in ("z", "Z", "y", "Y"):  # (the main undo would change the shape under the window)
        top.bind(f"<Control-{k}>", lambda e: "break")
    top.geometry(f"+{win.winfo_pointerx() - 40}+{win.winfo_pointery() - 40}")
    remember_place(top, "hz_number")
    entry.focus_set()
    entry.select_range(0, "end")
    top.grab_set()
    top.wait_window()
    return got["value"]


def hz_made(sh):
    """True for a Hz bass made with the Hz bass tool (hz["own"]): a box that is nothing but its placed tones."""
    hz = (sh or {}).get("hz") or {}
    return bool(hz.get("own") and all_tones(hz))


def hz_keys(sh):
    """The lowest and highest key a custom shape's box covers, as app.hz_defaults ({"lo", "hi"})."""
    ps = [p for _, p in sh["pts"]]
    ps.append(ps[1] + ps[2] - ps[0])
    return {"lo": round(min(ps)), "hi": round(max(ps))}


class HzGates:
    def live_edit(self, i, prompt, get, put, lo, hi, steps, name):
        """A number of note i (the selected notes' too when it's one of them) in a small window whose box can be
        typed in or dragged (user): every valid value is tried out at once (the notes here, the shape, the
        preview), one undo step in all; Cancel puts it back. The others move by as much as note i, each from its
        own value, stopping at the ends (like dragging the tune)."""
        if i >= len(self.tones):
            return
        held = self.tones[i]["id"]
        orig = {self.tones[j]["id"]: get(self.tones[j]) for j in (self.sel if i in self.sel else {i})}
        before = copy.deepcopy(self.tones)
        state = {"pushed": False}
        redo = self.app.redo_stack[:]  # (Cancel leaves no step behind, not even one to redo)

        def apply(v):
            moved = v - orig[held]
            was = copy.deepcopy(self.tones)
            for n in self.tones:
                if n["id"] in orig:
                    put(n, max(lo, min(hi, round(orig[n["id"]] + moved, 6))))
            if self.tones != was:
                self.commit(name, before, push=not state["pushed"])
                state["pushed"] = True

        got = ask_live(self, self.app, prompt, orig[held], lo, hi, steps, apply)
        if got is None and state["pushed"]:  # Cancel: the shape back as it was, its step taken out. Not app.undo:
            app = self.app  # that drops a slide's first mark or a half drawn shape instead (user)
            shapes = json.loads(app.undo_stack.pop()[0])
            sh = self.target()
            if sh is not None and app.sel < len(shapes):
                sh.clear()
                sh.update(shapes[app.sel])
            app.redo_stack[:] = redo
            app._redo_kept = None
            self.tones = copy.deepcopy(before)  # (the same notes as the shape's: the selection stays)
            app.shapes_changed()
            app.sync_hz_panels()
            app.sync_history()
            app.schedule_autosave()
            self.sync()

    def type_tune(self, i):
        """A note's own tune in cents (live_edit)."""
        def put(n, v):
            n["cents"] = float(v)
        self.live_edit(i, tr("hz.tune_ask", most=f"{TUNE:g}"), lambda n: n["cents"], put, -TUNE, TUNE,
                       (1, 10, 0.1), tr("hz.step_tune"))

    def reset_tune(self, i):
        """A double click on note i's red line: its tune back to 0 cents, the selected notes' too when it's one of
        them (as Tune… does). One undo step."""
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        for j in self.sel if i in self.sel else {i}:
            self.tones[j]["cents"] = 0.0
        if self.tones != before:
            self.commit(tr("hz.step_tune"), before)

    def type_auto(self, i):
        """A note's own Auto gates threshold in cents (live_edit)."""
        hz = (self.target() or {}).get("hz") or {}
        if hz.get("auto") is None:
            return
        shared = hz["auto"]

        def put(n, v):
            n["auto"] = float(v)
        self.live_edit(i, tr("hz.auto_ask", most=f"{AUTO_MOST:g}"), lambda n: n.get("auto", shared), put, 0.0,
                       AUTO_MOST, (1, 5, 0.1), tr("hz.step_auto"))

    def set_auto(self, i, cents):
        """Note i's own Auto gates threshold (the selected notes' too when it's one of them); None = back to the
        Hz bass's."""
        if i >= len(self.tones):
            return
        before = copy.deepcopy(self.tones)
        for j in self.sel if i in self.sel else {i}:
            if cents is None:
                self.tones[j].pop("auto", None)
            else:
                self.tones[j]["auto"] = cents
        if self.tones != before:
            self.commit(tr("hz.step_auto"), before)

    def gate_items(self, menu, i):
        """The menu's own-gates items for note i (the selected notes when it's one of them): the other gates than
        the ones they get now; both when they get different ones (either one then goes to all, user); and back to
        the Hz bass's when some have their own."""
        sh = self.target()
        if sh is None or not sh.get("hz") or i >= len(self.tones):
            return
        hz = sh["hz"]
        picked = [self.tones[j] for j in (self.sel if i in self.sel else {i})]
        now = {held_fixed(hz, self.app.ppq, n) for n in picked}
        menu.add_separator()  # (a group of their own, user)
        for fixed in (True, False):
            if now != {fixed}:
                mode = "fixed" if fixed else "mixed"
                menu.add_command(label=tr("hz.gate_" + mode), command=lambda mode=mode: self.set_gate(i, mode))
        if any("gate" in n for n in picked):
            menu.add_command(label=tr("hz.gate_shared", mode=tr("panel_custom.hz_" + gate_mode(hz))),
                             command=lambda: self.set_gate(i, None))
        menu.add_separator()

    def set_gate(self, i, mode):
        """Note i's own gates while held, "fixed" / "mixed" (the selected notes' too when it's one of them); None =
        back to the Hz bass's. With the Hz bass's own Alternating / Fixed the same as picked, a note just follows it."""
        if i >= len(self.tones):
            return
        hz = (self.target() or {}).get("hz") or {}
        same = mode is not None and hz.get("auto") is None and mode == gate_mode(hz)
        before = copy.deepcopy(self.tones)
        for j in self.sel if i in self.sel else {i}:
            if mode is None or same:
                self.tones[j].pop("gate", None)
            else:
                self.tones[j]["gate"] = mode
        if self.tones != before:
            self.commit(tr("hz.step_gate"), before)

    def on_grow(self):
        sh = self.target()
        if sh is None:
            return
        if not all_tones(sh.get("hz") or {}):  # (nothing placed yet in any layer: just how it'll be when there is)
            return self.redraw()
        self.commit(tr("hz.grow"), copy.deepcopy(self.tones))

    def auto_limit(self):
        """The threshold box's cents (a wrong value: back to its last good one), or None when there's none."""
        for again in (False, True):
            try:
                limit = float(calc(self.auto_var.get()))
                if 0 <= limit <= AUTO_MOST:
                    good(self.auto_row.entry)
                    return limit
            except (ValueError, ZeroDivisionError):
                pass
            if not again:
                bad(self.auto_row.entry)
        return None

    def fixed(self):
        """What the dropdown (and threshold box) say, as hz settings ({"fixed": True}, {"auto": cents} or nothing):
        for a Hz bass that's still to be made."""
        mode = GATE_MODES[self.gates.current()]
        if mode == "auto":
            limit = self.auto_limit()
            return {"auto": AUTO if limit is None else limit}
        return {"fixed": True} if mode == "fixed" else {}

    def new_hz(self, bpm):
        """The settings of a Hz bass that isn't there yet, as the first note would make it."""
        hz = dict(self.app.custom_defaults.get("hz") or HZ_DEFAULTS, bpm=float(bpm or 120))
        hz.pop("fixed", None)
        hz.pop("auto", None)
        cents = self.pitch()
        return dict(hz, **self.fixed(), **({} if cents is None else {"cents": cents}))

    def show_auto(self):
        """The threshold box: there only with Auto gates."""
        if GATE_MODES[self.gates.current()] == "auto":
            self.auto_row.pack(side="left", padx=(0, 6))
            self.auto_row.entry.config(state="normal" if self.can_place() else "disabled")
        else:
            self.auto_row.pack_forget()
        self.after_idle(self.layout)

    def on_gates(self, e=None):
        """The gates dropdown: Alternating ("mixed"), Fixed or Auto for the Hz bass shown (one undo step), or for the one to be
        made."""
        sh = self.target()
        if sh is not None and sh.get("hz"):
            mode = GATE_MODES[self.gates.current()]
            self.app.set_hz_gates(mode, self.fixed().get("auto"), every=True)  # (the notes' own gates go: user)
        self.show_auto()
        self.canvas.focus_set()
        self.redraw()

    def on_auto(self):
        """The threshold box typed, stepped or dragged."""
        limit = self.auto_limit()
        if limit is None:
            return
        sh = self.target()
        if sh is not None and sh.get("hz"):
            self.app.set_hz_gates("auto", limit)
        self.redraw()

    # ------------------------------------------------------------ Note length (Settings…)

    def length_of(self, var, row, now):
        """A Note length box as a Hz bass's length ({"pct": whole} / {"beats": whole ticks at this PPQ}; a wrong
        value: back to its last good one), or None. now = the length it has: the same number of ticks gives it back
        (after a PPQ change the ticks shown are rounded: leaving the box mustn't change it)."""
        unit = LENGTH_UNITS[row.unit.current()]
        ppq = self.app.ppq
        hi = 100 if unit == "pct" else math.floor(LENGTH_BEATS * ppq)
        row.scrub.hi = hi
        for again in (False, True):
            try:
                v = int(round(float(calc(var.get()))))
                if 1 <= v <= hi:
                    good(row.entry)
                    if str(v) != var.get():
                        var.set(str(v))
                    if unit == "pct":
                        return {"pct": float(v)}
                    if now and "beats" in now and length_ticks(now, ppq) == v:
                        return now
                    return {"beats": v / ppq}
            except (ValueError, ZeroDivisionError, OverflowError):
                pass
            if not again:
                if unit == "pct":  # (ticks -> %: a number past 100 isn't wrong, just too big)
                    try:
                        if float(calc(var.get())) > 100:
                            var.set("100")
                            continue
                    except (ValueError, ZeroDivisionError, OverflowError):
                        pass
                bad(row.entry)
        return None

    def put_length(self, var, row, length):
        ticks = "beats" in length
        row.unit.current(LENGTH_UNITS.index("ticks" if ticks else "pct"))
        row.scrub.hi = math.floor(LENGTH_BEATS * self.app.ppq) if ticks else 100
        var.set(str(length_ticks(length, self.app.ppq) if ticks else int(length["pct"])))
        good(row.entry)

    def length_target(self):
        """The Hz bass the Note length rows change (any: one without placed notes still makes notes, hunt), or
        None."""
        sh = self.target()
        return sh if sh is not None and sh.get("hz") else None

    def set_shared(self, name, key, value):
        """A setting of the whole Hz bass (every layer's: "length", "stick"), None = left out: one undo step."""
        sh = self.length_target()
        if sh is None or sh["hz"].get(key) == value:
            return
        app = self.app
        app.push_undo(name=name)
        sh["hz"] = dict({k: v for k, v in sh["hz"].items() if k != key}, **({} if value is None else {key: value}))
        app.shapes_changed()
        app.sync_hz_panels()
        self.sync()
        app.schedule_autosave()

    def on_length(self):
        """The Note length box typed, stepped or dragged, or its unit picked."""
        sh = self.length_target()
        got = self.length_of(self.length_var, self.length_row, sh and sh["hz"].get("length"))
        if got is not None:
            got = clean_length(got)
            self.set_shared(tr("hz.step_length"), "length", None if got == FULL_LENGTH else got)
        self.redraw()

    def on_stick(self):
        self.set_shared(tr("hz.step_stick"), "stick", True if self.stick.get() else None)

    def set_own(self, length):
        """The layer picked's own Note length (None: the Hz bass's): one undo step, kept with its sound."""
        sh = self.length_target()
        if sh is None:
            return
        if not all_tones(sh["hz"]):  # (no notes: the window's commit keeps no sound; a preset's own length on a
            return self.set_shared(tr("hz.step_length"), "own_length", length)  # Hz bass without notes, hunt)
        before = self.fx.state()
        self.extra = clean_extra(dict(self.extra, own_length=length))
        if self.fx.now() != before:
            self.fx.tidy()
            self.commit(tr("hz.step_length"), copy.deepcopy(self.tones), before)

    def on_own_pick(self):
        """This layer: Same as the Hz bass / Its own length (starting as the Hz bass's)."""
        sh = self.length_target()
        own = self.own_pick.current() == 1
        if sh is not None:
            self.set_own(clean_length(sh["hz"].get("length") or FULL_LENGTH) if own else None)
        self.canvas.focus_set()
        self.sync()

    def on_own_length(self):
        got = self.length_of(self.own_var, self.own_row, self.extra.get("own_length"))
        if got is not None:
            self.set_own(clean_length(got))
        self.redraw()

    def show_length(self, sh, hz):
        """The Note length rows as the Hz bass shown has them (greyed with no Hz bass); This layer only with layers
        or a length of its own (a preset's on a Hz bass without layers: it wins, so it must show, hunt), its box only
        with a length of its own."""
        ok = sh is not None and bool(hz)
        self.put_length(self.length_var, self.length_row, hz.get("length") or FULL_LENGTH)
        own = (self.extra.get("own_length") or hz.get("own_length")) if ok else None
        self.own_pick.current(1 if own else 0)
        if own:
            self.put_length(self.own_var, self.own_row, own)
            if not self.own_row.winfo_manager():
                self.own_row.pack(side="left")
        elif self.own_row.winfo_manager():
            self.own_row.pack_forget()
        layered = ok and (bool(hz.get("layers")) or bool(own))
        for w in (self.own_label, self.own_frame):
            if bool(w.winfo_manager()) != layered:
                w.grid() if layered else w.grid_remove()
        self.stick.set(bool(hz.get("stick")))
        funnel = sh is not None and sh["kind"] == "funnel"
        self.stick_tip.text = tr("hz.stick_tip") + ("\n" + tr("hz.stick_funnel") if funnel else "")
        state = "normal" if ok else "disabled"
        for row in (self.length_row, self.own_row):
            row.entry.config(state=state)
            row.unit.config(state="readonly" if ok else "disabled")
        self.own_pick.config(state="readonly" if ok else "disabled")
        self.stick_box.config(state="normal" if ok and not funnel else "disabled")

    def pitch(self):
        """The Pitch box in cents (a wrong value, not -1200 to 1200: back to its last good one), or None."""
        for again in (False, True):
            try:
                cents = float(calc(self.pitch_var.get()))
                if abs(cents) <= 1200:
                    good(self.pitch_entry)
                    return cents
            except (ValueError, ZeroDivisionError):
                pass
            if not again:
                bad(self.pitch_entry)
        return None

    def on_pitch(self):
        """The Pitch box typed, stepped or dragged: the Hz bass shown moves by that many cents (one undo step); with
        none yet, the one the first note makes gets it."""
        cents = self.pitch()
        sh = self.target()
        if cents is not None and sh is not None and sh.get("hz"):
            self.app.set_hz_cents(cents)
        self.redraw()

    def on_line(self):
        self.redraw()
        self.app.schedule_autosave()

    def on_fx(self):
        """The Effects button: shows / hides the effects pane (off to start with, user)."""
        if self.app.hz_fx.get():
            self.fx.canvas.pack(side="bottom", fill="x", before=self.notes_box)
        else:
            self.fx.drag = None
            self.fx.canvas.pack_forget()
        self.app.schedule_autosave()

    def on_loud(self):
        """The Loudness button: shows / hides the loudness pane (off to start with, user), under the effects pane."""
        pane = self.loudness
        if self.app.hz_loud.get():
            pane.box.pack(side="bottom", fill="x",
                          before=self.fx.canvas if self.fx.canvas.winfo_manager() else self.notes_box)
            pane.redraw()
        else:
            pane.edit = None
            pane.box.pack_forget()
        self.app.schedule_autosave()

    def commit_fx(self, before):
        """The effects' lines changed: one undo step. before = FxPane.state() to go back to if it's
        called off."""
        self.fx.tidy()
        self.commit(tr("hz.step_fx"), copy.deepcopy(self.tones), before)

    def set_fx(self, hz):
        """The effects' lines here from a Hz bass's settings (fx_settings' own, checked; a preset's too)."""
        self.fxl = clean_fx(hz.get("fx") or {})
        self.loops = clean_loop(hz.get("loop"), self.fxl)
        self.fxl.update({k + AMOUNT: v for k, v in clean_fx(hz.get("amount") or {}).items() if k in self.loops})
        self.off = clean_off(hz.get("off"), self.fxl)
        self.froms = clean_from(hz.get("from"), self.loops)
        self.fits = clean_fit(hz.get("fit"), self.froms)
        self.sustains = clean_sustain(hz.get("sustain"), self.loops, self.froms, self.fits)
        self.lfo = clean_lfo(hz.get("lfo") or {})
        self.extra = clean_extra(hz)

    def fx_settings(self):
        """The effects' lines here as a Hz bass's settings (hz["fx"], "loop", "off", "amount", "from", "fit",
        "sustain"), checked; {} without any."""
        fx = {"fx": clean_fx(self.fxl)} if self.fxl else {}  # (amount lines: below)
        loops = clean_loop(self.loops, fx["fx"]) if fx else {}
        if loops:
            fx["loop"] = loops
        off = clean_off(self.off, fx["fx"]) if fx else []
        if off:
            fx["off"] = off
        amount = clean_fx({k[:-len(AMOUNT)]: v for k, v in self.fxl.items() if k.endswith(AMOUNT)})
        amount = {k: v for k, v in amount.items() if k in loops}
        if amount:
            fx["amount"] = amount
        froms = clean_from(self.froms, loops)
        if froms:
            fx["from"] = froms
        fits = clean_fit(self.fits, froms)
        if fits:
            fx["fit"] = fits
        sustain = clean_sustain(self.sustains, loops, froms, fits)
        if sustain:
            fx["sustain"] = sustain
        lfo = clean_lfo(self.lfo)
        if lfo:
            fx["lfo"] = lfo
        fx.update(clean_extra(self.extra))
        return fx

    def commit(self, name, before, before_fx=None, push=True):
        """The notes (and effects' lines) here become the shape's: one undo step of the main window (push=False:
        part of the step taken already, e.g. the Tune window trying values). before = the tones (and before_fx the
        lines) to go back to if it's called off (too many notes)."""
        app = self.app
        more, self.more_layers = self.more_layers, None  # (notes pasted from Domino for the layers under this one)
        # the undo step keeps the selection from before the edit (the notes are still numbered as in `before`)
        was = self.sel_before[1] if self.sel_before and self.sel_before[0] is before else self.sel_state()
        self.sel_before = None
        picked = {id(self.tones[i]) for i in self.sel if i < len(self.tones)}
        boxed = self.box_kept is not None and self.box_kept[1] == self.sel
        self.tones.sort(key=lambda n: (n["t"], n["key"]))
        self.sel = {i for i, n in enumerate(self.tones) if id(n) in picked}
        if boxed:  # (the same notes, numbered anew: the Select box stays, user)
            self.box_kept = (self.box_kept[0], set(self.sel))
        tones = clean_tones(copy.deepcopy(self.tones))
        fx = self.fx_settings()
        sh = self.target()
        bpm = app.current_bpm()
        if bpm is None and tones and not (sh or {}).get("hz"):  # (the first notes: the tone is worked out for the
            self.bell()                                          # BPM, so it must be a number)
            self.call_off(before, before_fx)
            return self.say(tr("hz.bpm_needed"))
        if sh is None:
            if not tones or app.hz_start is None:
                return self.redraw()
            lo, hi = app.hz_defaults["lo"], app.hz_defaults["hi"]
            new = dict(app.defaults, kind="custom", name=tr("hz.name"), strokes=[copy.deepcopy(BOX_STROKE)],
                       **custom_settings(app.custom_defaults))
            new.update(fill="spam", pts=box_frame(app.hz_start, lo, app.hz_start + sound_span(dict(fx, tones=tones)), hi),
                       hz=dict(self.new_hz(bpm), tones=copy.deepcopy(tones), grow=True, own=True,
                               **copy.deepcopy(fx), **({"loud": copy.deepcopy(self.loud)} if self.loud else {})))
            new.pop("range", None)  # (no gate Range with Hz bass, user)
            if more:
                new["hz"] = self.layers.with_more(new["hz"], more)
                fit_length(new)
            if not app.confirm_big([new]):
                return self.call_off(before, before_fx)
            app.hz_start = None
            self.tones = tones  # (so the selection stays when the main window's selection changes to the new shape)
            self.own_step = True
            try:
                app.add_shape(new)
            finally:
                self.own_step = False
        elif not sh.get("hz") and not tones:  # (a spam shape not a Hz bass yet, no notes: lines drawn wait here for
            self.fx_of = ("waiting", id(sh))  # its first note, nothing changes, no step; sync keeps them)
            return self.redraw()
        else:
            hz = dict(sh.get("hz") or self.new_hz(bpm))  # (none yet: the window's Gates and Pitch boxes)
            others = len(all_tones(hz)) > len(hz.get("tones") or ())  # (other layers' notes: the Hz bass stays)
            for k in ("tones", "grow", "fx", "loop", "off", "amount", "from", "fit", "sustain", "lfo", "loud") + EXTRAS:
                hz.pop(k, None)
            new = copy.deepcopy(sh)
            if tones or others:
                new["hz"] = dict(hz, **({"tones": tones} if tones else {}),
                                 **({"grow": True} if self.grow.get() else {}), **copy.deepcopy(fx),
                                 **({"loud": copy.deepcopy(self.loud)} if self.loud else {}))
                if more:
                    new["hz"] = self.layers.with_more(new["hz"], more)
                if new["kind"] == "custom" and new["fill"] not in SPAM_FILLS:  # (a funnel's own fill doesn't count)
                    new["fill"] = "spam"
                if not sh.get("hz"):  # (a spam shape's first notes: its gate and Range come back if Hz bass is
                    new["before_hz"] = {"gate": sh["gate"]}  # unticked; no Range with Hz bass, user)
                    if new.get("range"):  # (asked first, like the side panel's Hz bass box)
                        if not messagebox.askokcancel(tr("panel_custom.spiderweb"), tr("panel_custom.hz_range_off"),
                                                      icon="warning", parent=self):
                            return self.call_off(before, before_fx)
                        new["range_kept"] = new.pop("range")
                        new["before_hz"]["range"] = True
                if self.grow.get():
                    fit_length(new)
                if not app.confirm_big([new]):
                    return self.call_off(before, before_fx)
            if push:
                self.pushing, self.own_step = was, True
                try:
                    app.push_undo(name=name)
                finally:
                    self.pushing, self.own_step = None, False
            if tones or others or not hz_made(sh):
                if not tones and not others:  # the last note deleted from a shape of its own: back to its one tone
                    new["hz"] = hz
                sh.clear()
                sh.update(new)
            else:  # ... from a Hz bass made here: it goes, a new one can start at the same spot, same keys
                app.hz_start, app.hz_defaults = left_edge(sh), hz_keys(sh)
                del app.shapes[app.sel]
                self.tones = []
                app.select(None)
            app.shapes_changed()
            app.sync_hz_panels()
            app.schedule_autosave()
        self.sync()

    def call_off(self, before, before_fx=None):
        self.tones, self.sel = before, set()
        if self.target() is not None:  # (the layer's loudness line: the shape's, unchanged)
            self.loud = clean_line((self.target().get("hz") or {}).get("loud"))
        if before_fx is not None:
            self.fxl, self.loops, self.off, self.froms, self.fits, self.sustains, self.lfo, self.extra = before_fx
        self.redraw()
