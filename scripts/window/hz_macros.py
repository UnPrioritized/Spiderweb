"""The synth window's macros (user 2026-10-08, round 4, as real synths have them): MACROS big knobs in a strip under
the big tabs, shown on the OSC and FX tabs. Dragging a macro's name onto any knob links them: the knob keeps its own
value and the macro adds to it, up to an amount of the knob's turn with the macro all the way (a ring round the knob
shows how far; dragging the ring changes it, down to 0 = unlinked). One macro is picked at a time (its rings shown):
clicking it, or making a link. A macro is one setting for the sound, not changing over time (user: that comes with
the matrix): the knobs' lines get the sum, so it's all in the MIDI. Saved in hz["macro"] (hzbass.clean_macro)."""

import copy
import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.hzbass import MACROS, RACK
from window.hz_knobs import KINDS, KNOBS, PERCENTS, UPDOWN, Dial, knob_of, text_key, value_of
from window.synth_look import ENTRY, HEAD_FONT, MID, PANEL, Box, TEXT, dark_menu
from window.widgets import Scrub, Tooltip

MACRO_COLOURS = ("#f0b43c", "#4fd1c5", "#c084fc", "#f472b6")
LINK_AMOUNT = 0.5  # how far a new link moves its knob (of its turn, the macro all the way)
REST_MS = 500  # how long a dragged macro's name must rest on a knob before a let-go links it (user)


def inside(w, x, y):
    """The screen point x, y is on widget w."""
    wx, wy = w.winfo_rootx(), w.winfo_rooty()
    return wx <= x < wx + w.winfo_width() and wy <= y < wy + w.winfo_height()


def knob_span(key):
    """A knob's whole turn in its own values (0..100, the up / down ones -100..100)."""
    return 200.0 if KNOBS[key][1] in UPDOWN else 100.0


def knob_raw(kind, v):
    """Where a knob would point for a value, past its end too (a time / rate typed further than it turns)."""
    most = KINDS[kind][4]
    return 100 * math.sqrt(max(0.0, v) / most) if most else knob_of(kind, v)


def typed_range(kind, v):
    """A value kept inside what its box takes."""
    lo, hi = KINDS[kind][1:3]
    if kind in PERCENTS:
        lo, hi = lo / 100, hi / 100
    return min(hi, max(lo, v))


def turned(key, base, by):
    """A knob's value with `by` of its whole turn added, as the knob gives it: stopping at the knob's ends, or for a
    value typed past its end, at what its box takes (hunt: the macro made a typed 10 beats 4)."""
    kind = KNOBS[key][1]
    lo = -100.0 if kind in UPDOWN else 0.0
    raw = knob_raw(kind, base)
    top = 100.0 if raw <= 100.0 else knob_raw(kind, KINDS[kind][2])
    return typed_range(kind, value_of(kind, min(top, max(lo, raw + by * knob_span(key)))))


def unturned(key, value, by):
    """The knob's own value that `by` of its whole turn added makes `value` (a line drawn by hand on a linked knob)."""
    kind = KNOBS[key][1]
    lo = -100.0 if kind in UPDOWN else 0.0
    return typed_range(kind, value_of(kind, max(lo, knob_raw(kind, value) - by * knob_span(key))))


class SynthMacros:
    """The macro strip of SynthWindow (needs SynthKnobs: dials, vals, write, keep_vals, fx, commit_fx, extra, and the
    tabs' canvases knobs_canvas / rack_canvas)."""

    def build_macros(self, parent):
        s = self.s
        self.macro_pick = 0  # the macro whose rings show
        self.macro_held = None  # a macro's name being dragged: [macro, the knob under the mouse]
        self.macro_dials, self.macro_vars, self.macro_boxes, self.macro_names, self.macro_counts = [], [], [], [], []
        self.macro_text = [None] * MACROS  # (what each value box was last given to show: different = typed there)
        box = self.macro_box = Box(parent, s, tr("hz.synth_macros"), TEXT, padding=(10, 2, 10, 6))
        Tooltip(box.lamp, tr("hz.synth_tip_macro_lamp"))
        for i in range(MACROS):
            cell = ttk.Frame(box.body, style="Synth.Box.TFrame")
            cell.grid(row=0, column=i, padx=(4, 18), sticky="w")
            name = tk.Label(cell, text=tr("hz.synth_macro", n=i + 1), background=PANEL, foreground=MACRO_COLOURS[i],
                            font=HEAD_FONT, cursor="fleur", padx=4)
            name.grid(row=0, column=0, columnspan=2, sticky="w")
            name.bind("<ButtonPress-1>", lambda e, i=i: self.macro_press(i))
            name.bind("<B1-Motion>", self.macro_motion)
            name.bind("<ButtonRelease-1>", lambda e: self.macro_release())
            name.bind("<ButtonPress-3>", lambda e, i=i: self.macro_menu(i, e))
            Tooltip(name, tr("hz.synth_tip_macro_name"))
            k = Dial(cell, s, lambda v, done, i=i: self.on_macro(i, v, done), MACRO_COLOURS[i], size=40)
            k.grid(row=1, column=0, rowspan=2, padx=(0, 6))
            k.bind("<ButtonPress-1>", lambda e, i=i: self.pick_macro(i), add="+")
            var = tk.StringVar(value="0")
            e = ttk.Entry(cell, textvariable=var, width=5, justify="center", style=ENTRY)
            e.grid(row=1, column=1, sticky="sw")
            e.bind("<Return>", lambda ev, i=i, e=e: (self.on_macro_box(i), self.keyboard_back(e), "break")[2])
            e.bind("<FocusOut>", lambda ev, i=i: self.on_macro_box(i))
            Scrub(self.app, [(e, var, lambda i=i: self.on_macro_box(i))], (1, 10, 0.1), 0.0, 100.0, drag_box=True)
            count = ttk.Label(cell, text="", style="Synth.Box.Dim.TLabel")
            count.grid(row=2, column=1, sticky="nw")
            for w in (k, e):
                Tooltip(w, tr("hz.synth_tip_macro") + "\n" + tr("hz.synth_tip_knob"))
            self.macro_dials.append(k)
            self.macro_vars.append(var)
            self.macro_boxes.append(e)
            self.macro_names.append(name)
            self.macro_counts.append(count)
        ttk.Label(box.body, text=tr("hz.synth_macro_hint"), style="Synth.Box.Dim.TLabel",
                  wraplength=round(260 * s)).grid(row=0, column=MACROS, sticky="w", padx=(6, 0))
        for key, k in self.dials.items():
            k.ring_drag = lambda dy, fine, done, key=key: self.ring_drag(key, dy, fine, done)

    # ------------------------------------------------------------ what the macros do

    def macro(self):
        """hz["macro"] as a copy to change (none yet: every macro at 0, nothing linked)."""
        m = self.extra.get("macro")
        if not m:
            return {"values": [0.0] * MACROS, "links": [], "base": {}}
        return {"values": list(m["values"]), "links": [list(link) for link in m["links"]], "base": dict(m["base"])}

    def linked(self):
        """The knobs a macro moves."""
        return {key for _, key, _ in (self.extra.get("macro") or {}).get("links", ()) if key in KNOBS}

    def bases(self):
        """The knobs a macro moves: their own values (the knob shows that, the lines have the macros added)."""
        m = self.extra.get("macro") or {}
        return {k: v for k, v in m.get("base", {}).items() if k in KNOBS}

    def keep_bases(self):
        """The linked knobs' own values kept in hz["macro"] (before their lines are written with the macros')."""
        m = self.extra.get("macro")
        if m:
            self.set_extra("macro", dict(m, base={k: self.vals[k] for _, k, _ in m["links"] if k in KNOBS}))

    def macro_by(self):
        """{linked knob: how much of its whole turn the macros add to it now}."""
        m = self.extra.get("macro") or {}
        by = {}
        for i, key, amount in m.get("links", ()):
            if key in KNOBS:
                by[key] = by.get(key, 0.0) + m["values"][i] * amount
        return by

    def macro_vals(self, vals=None):
        """The knobs' values (self.vals, or these) as the lines get them: each linked knob turned by its macros
        (value x amount of its whole turn, added up), stopping at its ends."""
        vals = self.vals if vals is None else vals
        by = self.macro_by()
        if not by:
            return vals
        v = dict(vals)
        for key, d in by.items():
            if abs(d) > 1e-12:  # (nothing added: its own value as it is, not through the knob and back)
                v[key] = self.timed(key, turned(key, v[key], d))
        return v

    def macro_moves(self, i):
        """Macro i adds something to a knob right now (at 0, a link made or taken away changes no line)."""
        m = self.extra.get("macro") or {}
        return bool(m and m["values"][i] and any(j == i and a for j, _, a in m["links"]))

    @property
    def pv(self):
        """The knobs' values as the sound has them (the pictures draw these, hunt: not the knobs' own)."""
        return self.macro_vals()

    def bases_read(self, read):
        """The linked knobs' own values for the knobs read from the lines (read = every knob as read): as kept,
        unless the line isn't what that and the macros make (drawn by hand, e.g. on the Draw tab): then the value
        that with the macros makes the line, as an unlinked knob follows the line (hunt)."""
        out = self.bases()
        if not out:
            return out
        want = self.macro_vals(dict(read, **out))
        by = self.macro_by()
        for key, base in out.items():
            r = read[key]
            if (isinstance(r, (int, float)) and abs(r - base) > 1e-6 and abs(r - want[key]) > 1e-6
                    and KNOBS[key][0] in self.box_says and not self.box_says[KNOBS[key][0]].cget("text")):
                out[key] = self.timed(key, unturned(key, r, by.get(key, 0.0)))
        return out

    def macro_write(self, keys):
        """The boxes of these knobs written again (a macro or a link changed), then everything shown."""
        for box in sorted({KNOBS[k][0] for k in keys if k in KNOBS}):
            self.write(box, show=False)
        self.keep_vals()
        self.redraw()
        self.show_knobs()

    def macro_step(self, change, keys, quiet=False):
        """One change of the macros (change(m) edits a copy of hz["macro"]): the knobs' lines again (keys: those
        knobs' boxes; none = no line changes), one undo step (quiet: saved without one)."""
        before = self.fx.state()
        m = self.macro()
        change(m)
        self.set_extra("macro", m)
        self.macro_write(keys)
        if self.fx.now() != before:
            self.macro_commit(before, quiet)

    def macro_commit(self, before, quiet):
        """The change saved in the shape: one undo step, or (quiet: a macro moving nothing, hunt) none."""
        if quiet:
            self.hz.fx.tidy()
            self.hz.commit(tr("hz.step_fx"), copy.deepcopy(self.hz.tones), before, push=False)
        else:
            self.commit_fx(before)

    # ------------------------------------------------------------ the macro knobs

    def pick_macro(self, i):
        if self.macro_pick != i:
            self.macro_pick = i
            self.show_macros()

    def on_macro(self, i, k, done):
        """A macro knob turned (done: let go / one wheel or arrow step = one undo step): every knob linked to it
        turns with it."""
        if self.turning is None:
            self.turning, self.turn_vals = self.fx.state(), dict(self.vals)
        self.macro_pick = i
        m = self.macro()
        m["values"][i] = max(0.0, min(1.0, k / 100))
        self.set_extra("macro", m)
        keys = {key for j, key, _ in m["links"] if j == i}
        self.macro_write(keys)
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.macro_commit(before, not keys)  # (linked to nothing: no undo step, hunt)

    def on_macro_box(self, i):
        """A macro's value typed (or stepped) in the box under it (in %)."""
        e, var = self.macro_boxes[i], self.macro_vars[i]
        e.config(style=ENTRY)
        if var.get() == self.macro_text[i]:
            return
        try:
            v = float(calc(var.get()))
            if not 0 <= v <= 100:
                raise ValueError
        except (ValueError, ZeroDivisionError):  # (back to the last good value, as the other knobs' boxes)
            var.set(self.macro_text[i] or "0")
            return
        self.macro_text[i] = var.get()
        self.macro_pick = i
        keys = {key for j, key, _ in self.macro()["links"] if j == i}
        self.macro_step(lambda m: m["values"].__setitem__(i, v / 100), keys, quiet=not keys)

    # ------------------------------------------------------------ linking

    def knob_at(self, x, y):
        """The knob (its key) at the screen point x, y: one shown on the tab showing, not scrolled out of sight."""
        for key, k in self.dials.items():
            if k.winfo_viewable() and inside(k, x, y):
                page = self.knobs_canvas if str(k).startswith(str(self.knobs_canvas)) else self.rack_canvas
                if inside(page, x, y):
                    return key
        return None

    def macro_press(self, i):
        self.pick_macro(i)
        self.macro_held = [i, None, False]  # (macro, the knob under the mouse, rested on long enough)
        self.macro_wait = None

    def macro_motion(self, e):
        """A macro's name dragged: a knob the mouse rests on for REST_MS is lit in its colour, and only then a let-go
        links it (user: passing over knobs linked them by mistake)."""
        held = self.macro_held
        if not held:
            return
        key = self.knob_at(e.x_root, e.y_root)
        if key != held[1]:
            self.macro_hover(held[1], None)
            held[1], held[2] = key, False
            if self.macro_wait:
                self.after_cancel(self.macro_wait)
            self.macro_wait = self.after(REST_MS, self.macro_arm) if key else None

    def macro_arm(self):
        """The mouse rested on a knob long enough: let go = linked."""
        self.macro_wait = None
        held = self.macro_held
        if held and held[1]:
            held[2] = True
            self.macro_hover(held[1], MACRO_COLOURS[held[0]])

    def macro_hover(self, key, colour):
        if key and self.dials[key].hover != colour:
            self.dials[key].hover = colour
            self.dials[key].draw()

    def macro_drop(self):
        """The name's drag ended or called off (Ctrl+Z): no knob lit any more."""
        held, self.macro_held = self.macro_held, None
        if getattr(self, "macro_wait", None):
            self.after_cancel(self.macro_wait)
            self.macro_wait = None
        if held:
            self.macro_hover(held[1], None)

    def macro_cancel(self):
        """Ctrl+Z / Esc while a macro's name is dragged: the drag ends, nothing linked, no step taken back."""
        self.macro_drop()
        return True

    def macro_release(self):
        """A macro's name let go on a knob it rested on: linked (LINK_AMOUNT), one undo step; already linked: just
        picked."""
        held = self.macro_held
        self.macro_drop()
        if not held or not held[1] or not held[2]:
            return
        held = held[:2]
        i, key = held
        if any(j == i and k == key for j, k, _ in self.macro()["links"]):
            return self.show_macros()
        self.macro_step(lambda m: (m["links"].append([i, key, LINK_AMOUNT]),  # (macro at 0: no line written
                                   m["base"].__setitem__(key, self.vals[key])),  # again, hunt: a drawn one stays)
                        {key} if self.extra.get("macro", {}).get("values", [0.0] * MACROS)[i] else set())

    def link_amount(self, i, key):
        return next((a for j, k, a in self.macro()["links"] if j == i and k == key), None)

    def ring_drag(self, key, dy, fine, done):
        """A knob's ring dragged (dy = pixels up since the press; None at the let-go): how far the picked macro
        moves it; let go at 0 = unlinked. One undo step."""
        i = self.macro_pick
        if self.turning is None:
            self.turning, self.turn_vals = self.fx.state(), dict(self.vals)
            self.ring_from = self.link_amount(i, key) or 0.0
        m = self.macro()
        link = next((link for link in m["links"] if link[0] == i and link[1] == key), None)
        if link is None:  # (unlinked meanwhile, e.g. by Ctrl+Z)
            return
        if dy is not None:
            link[2] = max(-1.0, min(1.0, self.ring_from + dy * (0.001 if fine else 0.005)))
            if abs(link[2]) < 0.01:  # (sticks at 0 on the way past)
                link[2] = 0.0
        elif link[2] == 0.0:
            m["links"].remove(link)
            if key not in {k for _, k, _ in m["links"]}:
                m["base"].pop(key, None)
        self.set_extra("macro", m)
        self.macro_write({key} if m["values"][i] else set())  # (macro at 0: the lines stay as they are)
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

    def macro_menu(self, i, e):
        """A macro's name right-clicked: its links, each to unlink, and Unlink all."""
        self.pick_macro(i)
        menu = tk.Menu(self, tearoff=0)
        dark_menu(menu)
        links = [(k, a) for j, k, a in self.macro()["links"] if j == i]
        for key, a in links:
            menu.add_command(label=tr("hz.synth_macro_unlink", knob=self.knob_label(key), amount=fmt(round(a * 100))),
                             command=lambda key=key: self.unlink(i, {key}))
        if not links:
            menu.add_command(label=tr("hz.synth_macro_none"), state="disabled")
        menu.add_separator()
        menu.add_command(label=tr("hz.synth_macro_unlink_all"), state="normal" if links else "disabled",
                         command=lambda: self.unlink(i, {k for k, _ in links}))
        menu.tk_popup(e.x_root, e.y_root)

    def knob_label(self, key):
        """A knob's box and name, e.g. "Tone: End"."""
        box = KNOBS[key][0]
        return tr("hz.synth_macro_knob", box=tr(f"hz.rack_{box}" if box in RACK else f"hz.synth_{box}"),
                  knob=tr(f"hz.synth_{text_key(key)}"))

    def unlink(self, i, keys):
        """Macro i no longer moves these knobs (they stay at their own values), one undo step."""
        def change(m):
            m["links"] = [link for link in m["links"] if not (link[0] == i and link[1] in keys)]
            still = {k for _, k, _ in m["links"]}
            m["base"] = {k: v for k, v in m["base"].items() if k in still}
        self.macro_step(change, keys if self.macro_moves(i) else set())  # (adding nothing: no line written again)

    # ------------------------------------------------------------ showing them

    def show_macros(self):
        """The macro knobs, their boxes and link counts, the picked one's name lit, and each linked knob's ring."""
        if not hasattr(self, "macro_box"):  # (not built yet)
            return
        m = self.macro()
        for i in range(MACROS):
            k, v = self.macro_dials[i], m["values"][i]
            if not k.drag and abs(k.value - 100 * v) > 0.05:
                k.set(100 * v)
            text = fmt(round(100 * v, 1))
            e, var = self.macro_boxes[i], self.macro_vars[i]
            typing = self.focus_get() is e and var.get() != self.macro_text[i]
            if var.get() != text and not typing:
                var.set(text)
            if not typing:
                self.macro_text[i] = text
            n = sum(1 for j, key, _ in m["links"] if j == i and key in KNOBS)
            says = tr("hz.synth_macro_count_none") if not n else tr("hz.synth_macro_count", n=n)
            if self.macro_counts[i].cget("text") != says:
                self.macro_counts[i].config(text=says)
            back = MID if i == self.macro_pick else PANEL
            if self.macro_names[i].cget("background") != back:
                self.macro_names[i].config(background=back)
        self.macro_box.lamp.light(any(m["values"][i] and a for i, key, a in m["links"] if key in KNOBS))
        now = self.macro_vals()
        held = self.macro_held
        for key, k in self.dials.items():
            if k.hover and not (held and held[1] == key and held[2]):  # (no outline left over from a drag)
                k.hover = None
                k.draw()
            mine = [(j, a) for j, kk, a in m["links"] if kk == key]
            ring = None
            if mine:
                kind = KNOBS[key][1]
                a = next((a for j, a in mine if j == self.macro_pick), None)
                reach = None if a is None else knob_of(kind, turned(key, self.vals[key], a))
                colour = MACRO_COLOURS[self.macro_pick if a is not None else mine[0][0]]
                ring = (reach, colour, knob_of(kind, now[key]))
            if ring != k.ring:
                k.ring = ring
                k.draw()
