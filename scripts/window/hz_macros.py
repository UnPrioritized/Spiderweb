"""The synth window's macros (user 2026-10-08, round 4, as real synths have them): MACROS big knobs in a strip under
the big tabs, shown on the OSC and FX tabs. Dragging a macro's name onto any knob links them: the knob keeps its own
value and the macro adds to it, up to an amount of the knob's turn with the macro all the way (a ring round the knob
shows how far; dragging the ring changes it, down to 0 = unlinked). One macro is picked at a time (its rings shown):
clicking it, or making a link. A macro is one setting for the sound, not changing over time (user: that comes with
the matrix): the knobs' lines get the sum, so it's all in the MIDI. Saved in hz["macro"] (hzbass.clean_macro)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.hzbass import MACROS, RACK
from window.hz_knobs import KNOBS, UPDOWN, Dial, knob_of, value_of
from window.synth_look import ENTRY, HEAD_FONT, MID, PANEL, Box, TEXT, dark_menu
from window.widgets import Scrub, Tooltip

MACRO_COLOURS = ("#f0b43c", "#4fd1c5", "#c084fc", "#f472b6")
LINK_AMOUNT = 0.5  # how far a new link moves its knob (of its turn, the macro all the way)


def inside(w, x, y):
    """The screen point x, y is on widget w."""
    wx, wy = w.winfo_rootx(), w.winfo_rooty()
    return wx <= x < wx + w.winfo_width() and wy <= y < wy + w.winfo_height()


def knob_span(key):
    """A knob's whole turn in its own values (0..100, the up / down ones -100..100)."""
    return 200.0 if KNOBS[key][1] in UPDOWN else 100.0


def turned(key, base, by):
    """A knob's value with `by` of its whole turn added (stopping at its ends), as the knob gives it."""
    kind = KNOBS[key][1]
    lo = -100.0 if kind in UPDOWN else 0.0
    return value_of(kind, min(100.0, max(lo, knob_of(kind, base) + by * knob_span(key))))


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

    def macro_vals(self):
        """The knobs' values as the lines get them: each linked knob turned by its macros (value x amount of its
        whole turn, added up), stopping at its ends."""
        m = self.extra.get("macro")
        if not m:
            return self.vals
        by = {}
        for i, key, amount in m["links"]:
            if key in KNOBS:
                by[key] = by.get(key, 0.0) + m["values"][i] * amount
        v = dict(self.vals)
        for key, d in by.items():
            if abs(d) > 1e-12:  # (nothing added: its own value as it is, not through the knob and back)
                v[key] = self.timed(key, turned(key, v[key], d))
        return v

    def macro_write(self, keys):
        """The boxes of these knobs written again (a macro or a link changed), then everything shown."""
        for box in sorted({KNOBS[k][0] for k in keys if k in KNOBS}):
            self.write(box, show=False)
        self.keep_vals()
        self.redraw()
        self.show_knobs()

    def macro_step(self, change, keys):
        """One change of the macros (change(m) edits a copy of hz["macro"]): the knobs' lines again, one undo step."""
        before = self.fx.state()
        m = self.macro()
        change(m)
        self.set_extra("macro", m)
        self.macro_write(keys)
        if self.fx.now() != before:
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
        self.macro_write({key for j, key, _ in m["links"] if j == i})
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

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
        self.macro_step(lambda m: m["values"].__setitem__(i, v / 100),
                        {key for j, key, _ in self.macro()["links"] if j == i})

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
        self.macro_held = [i, None]

    def macro_motion(self, e):
        """A macro's name dragged: the knob under the mouse lit in its colour."""
        if not self.macro_held:
            return
        key = self.knob_at(e.x_root, e.y_root)
        if key != self.macro_held[1]:
            self.macro_hover(None)
            self.macro_held[1] = key
            self.macro_hover(key)

    def macro_hover(self, key):
        if key:
            k = self.dials[key]
            k.hover = MACRO_COLOURS[self.macro_held[0]] if self.macro_held and self.macro_held[1] == key else None
            k.draw()

    def macro_drop(self):
        """The name's drag called off (Ctrl+Z, Esc): nothing linked."""
        held, self.macro_held = self.macro_held, None
        if held and held[1]:
            self.dials[held[1]].hover = None
            self.dials[held[1]].draw()

    def macro_release(self):
        """A macro's name let go on a knob: linked (LINK_AMOUNT), one undo step; already linked: just picked."""
        held = self.macro_held
        self.macro_drop()
        if not held or not held[1]:
            return
        i, key = held
        if any(j == i and k == key for j, k, _ in self.macro()["links"]):
            return self.show_macros()
        self.macro_step(lambda m: (m["links"].append([i, key, LINK_AMOUNT]),
                                   m["base"].__setitem__(key, self.vals[key])), {key})

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
        self.macro_write({key})
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
                  knob=tr(f"hz.synth_{key}"))

    def unlink(self, i, keys):
        """Macro i no longer moves these knobs (they stay at their own values), one undo step."""
        def change(m):
            m["links"] = [link for link in m["links"] if not (link[0] == i and link[1] in keys)]
            still = {k for _, k, _ in m["links"]}
            m["base"] = {k: v for k, v in m["base"].items() if k in still}
        self.macro_step(change, keys)

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
        for key, k in self.dials.items():
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
