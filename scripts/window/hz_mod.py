"""The synth window's MOD tab (user 2026-10-09, round 7: the matrix, as real synths have it): two more LFOs (LFO 3 /
LFO 4: LFO 1 and 2 are the Vibrato and the Tremolo), two more envelopes (ENV 2 / ENV 3: ENV 1 is the Volume box),
and each note's Velocity and Note, which move knobs over time. Their names are also in the macros' strip on the OSC
and FX tabs (where the knobs are): drag one onto a knob, as a macro's, to link them; the knob's ring then shows how
far the source moves it (drag the ring: the amount, down to 0 = unlinked). Only knobs that make an effect's line can
be moved (MOD_KNOBS). Saved in hz["mod"] (hzbass.clean_mod); the engine moves the lines while the notes are made."""

import copy
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.hzbass import (MOD_ENV, MOD_ENVS, MOD_LFO_MODES, MOD_LFO_SHAPES, MOD_LFOS, MOD_SOURCES, TIMINGS, adsr_line,
                          line_at, loop_shape, mod_start)
from window.hz_knobs import KINDS, PERCENTS, Dial, knob_of, note_name, shown, snap_rate, timed_rates, value_of
from window.hz_macros import LINK_AMOUNT, REST_MS, inside
from window.synth_look import ENTRY, GRID, HEAD_FONT, MID, PANEL, PIC, Box, dark_list, dark_menu, mix
from window.widgets import Scrub, Tooltip

# the knobs a source can move: knob -> the effect line it makes (hzbass.MOD_TARGETS; "wave" = the picked waveform's)
MOD_KNOBS = {"shape": "wave", "octave": "octave", "sustain": "volume", "amount": "pitch", "vibrato_depth": "vibrato",
             "tremolo_rate": "tremolo", "sweep_start": "sweep", "sweep_end": "sweep", "wah": "wah", "slant": "slant",
             "groups": "groups", "offpitch": "offpitch", "noisy": "noisy"}
SOURCE_COLOURS = {"lfo3": "#60a5fa", "lfo4": "#34d399", "env2": "#fb923c", "env3": "#facc15", "velocity": "#a78bfa",
                  "note": "#f87171"}
LFO_KNOBS = (("rate", "vib_rate"),)
ENV_KNOBS = (("attack", "time"), ("decay", "time"), ("sustain", "percent"), ("release", "time"))
PIC_SIZE = (230, 70)
MATRIX_COLOUR = "#9aa3ae"


def line_of(key, k):
    """The value a knob's line has with the knob at k (its own 0..100, Pitch Amount -100..100)."""
    if key == "amount":
        return 0.5 + k / 200
    if key == "tremolo_rate":  # (the knob goes along a curve: line = rate / TREMOLO, knob = 100 x its square root)
        return (k / 100) ** 2
    return k / 100


def knob_of_line(key, v):
    """Where a knob points for its line's value v (0..1)."""
    v = min(1.0, max(0.0, v))
    if key == "amount":
        return (v - 0.5) * 200
    if key == "tremolo_rate":
        return 100 * math.sqrt(v)
    return 100 * v


def ring_knob(link):
    """The knob a link's ring shows on: the one it was linked on (two knobs can make one line: Sweep Start / End)."""
    key = link.get("knob")
    return key if MOD_KNOBS.get(key) == link["to"] else next(k for k, to in MOD_KNOBS.items() if to == link["to"])


def source_name(src):
    return tr(f"hz.synth_mod_{src}")


class SynthMod:
    """The MOD tab of SynthWindow and its names in the macros' strip (needs SynthMacros, SynthKnobs: dials, extra,
    set_extra, fx, commit_fx, turning, macro_held / macro_drop / knob_at / knob_label)."""

    def build_mod(self, page):
        s = self.s
        self.mod_pick = None  # the source whose rings show (None: the picked macro's)
        self.mod_dials, self.mod_vars, self.mod_boxes, self.mod_text = {}, {}, {}, {}  # (by (source, knob))
        self.mod_picks, self.mod_pics, self.mod_boxes_of, self.mod_pic_for = {}, {}, {}, {}
        rows = [ttk.Frame(page, style="Synth.TFrame") for _ in range(2)]
        for i, row in enumerate(rows):
            row.pack(fill="x", anchor="w", pady=(0, 8))
        for i, src in enumerate(MOD_LFOS + MOD_ENVS):
            box = self.mod_boxes_of[src] = Box(rows[i // 2], s, source_name(src), SOURCE_COLOURS[src])
            box.pack(side="left", anchor="n", padx=(0, 10))
            Tooltip(box.lamp, tr("hz.synth_tip_mod_lamp"))
            col = 0
            if src in MOD_LFOS:
                for what, ids in (("shape", MOD_LFO_SHAPES), ("mode", MOD_LFO_MODES), ("timing", TIMINGS)):
                    self.mod_choice(box.body, col, src, what, ids)
                    col += 1
                knobs = LFO_KNOBS
            else:
                knobs = ENV_KNOBS
            for key, kind in knobs:
                self.mod_dial_cell(box.body, col, src, key, kind)
                col += 1
            pic = self.mod_pics[src] = tk.Canvas(box.body, width=round(PIC_SIZE[0] * s), height=round(PIC_SIZE[1] * s),
                                                 background=PIC, highlightthickness=1, highlightbackground=GRID)
            pic.grid(row=1, column=0, columnspan=col, sticky="ew", pady=(8, 0))
            pic.bind("<Configure>", lambda e: self.draw_mod_pics())
        ttk.Label(page, text=tr("hz.synth_mod_hint"), style="Synth.Dim.TLabel", wraplength=round(700 * s),
                  justify="left").pack(anchor="w", pady=(2, 6))
        self.build_mod_list(page)

    def build_mod_list(self, page):
        """The list of links (the matrix): one row each (source, knob, amount, both ways, remove), + Add link;
        scrolls when it's taller than the room left."""
        s = self.s
        box = Box(page, s, tr("hz.synth_mod_list"), MATRIX_COLOUR)
        box.pack(fill="both", expand=True)
        Tooltip(box.lamp, tr("hz.synth_tip_mod_lamp"))
        self.mod_list_box = box
        top = ttk.Frame(box.body, style="Synth.Box.TFrame")
        top.pack(fill="x")
        add = ttk.Button(top, text=tr("hz.synth_mod_add"), style="Synth.TButton", command=self.mod_add)
        add.pack(side="left")
        Tooltip(add, tr("hz.synth_tip_mod_add"))
        self.mod_list_empty = ttk.Label(top, text=tr("hz.synth_mod_list_none"), style="Synth.Box.Dim.TLabel")
        self.mod_list_empty.pack(side="left", padx=(10, 0))
        holder = ttk.Frame(box.body, style="Synth.Box.TFrame")
        holder.pack(fill="both", expand=True, pady=(6, 0))
        c = self.mod_list_canvas = tk.Canvas(holder, highlightthickness=0, bd=0, background=PANEL, height=1)
        self.mod_list_bar = ttk.Scrollbar(holder, orient="vertical", command=c.yview, style="Synth.Vertical.TScrollbar")
        c.configure(yscrollcommand=self.mod_list_bar.set)
        c.pack(side="left", fill="both", expand=True)
        self.mod_rows = ttk.Frame(c, style="Synth.Box.TFrame")
        self.mod_rows_win = c.create_window(0, 0, window=self.mod_rows, anchor="nw")
        c.bind("<Configure>", lambda e: self.fit_mod_list())
        self.mod_rows.bind("<Configure>", lambda e: self.after_idle(self.fit_mod_list))
        c.bind("<MouseWheel>", self.mod_list_wheel)
        self.mod_rows_for = None  # (the links the rows were made for)
        self.mod_amount_text = {}  # (each row's amount box: what it was last given to show)

    def fit_mod_list(self):
        c = self.mod_list_canvas
        if not self.winfo_exists() or c.winfo_width() < 20:
            return
        need, have = self.mod_rows.winfo_reqheight(), c.winfo_height()
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(need, have)))
        if need > have + 1:
            if not self.mod_list_bar.winfo_ismapped():
                self.mod_list_bar.pack(side="right", fill="y", before=c)
        elif self.mod_list_bar.winfo_ismapped():
            self.mod_list_bar.pack_forget()
            c.yview_moveto(0)

    def mod_list_wheel(self, e):
        if self.mod_list_bar.winfo_ismapped() and not isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            self.mod_list_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def make_mod_rows(self, links):
        """The list's rows made again for these links."""
        for w in self.mod_rows.winfo_children():
            w.destroy()
        self.mod_amount_vars, self.mod_amount_boxes, self.mod_amount_text = [], [], {}
        knobs = list(dict.fromkeys(MOD_KNOBS))
        knob_names = [self.knob_label(k) for k in knobs]
        src_names = [source_name(src) for src in MOD_SOURCES]
        heads = ("hz.synth_mod_col_source", "hz.synth_mod_col_knob", "hz.synth_mod_col_amount", "", "")
        for col, head in enumerate(heads):
            ttk.Label(self.mod_rows, text=tr(head) if head else "", style="Synth.Box.Dim.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0, 10))
        for i, link in enumerate(links):
            r = i + 1
            var = tk.StringVar(value=source_name(link["from"]))
            cb = ttk.Combobox(self.mod_rows, textvariable=var, values=src_names, state="readonly",
                              width=max(len(n) for n in src_names) + 1, style="Synth.TCombobox")
            dark_list(cb)
            cb.grid(row=r, column=0, sticky="w", padx=(0, 10), pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e, i=i, var=var: (
                self.mod_row_set(i, "from", MOD_SOURCES[src_names.index(var.get())]), self.keyboard_back(e.widget)))
            Tooltip(cb, tr("hz.synth_tip_mod_col_source"))
            var = tk.StringVar(value=self.knob_label(ring_knob(link)))
            cb = ttk.Combobox(self.mod_rows, textvariable=var, values=knob_names, state="readonly",
                              width=max(len(n) for n in knob_names) + 1, style="Synth.TCombobox")
            dark_list(cb)
            cb.grid(row=r, column=1, sticky="w", padx=(0, 10), pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e, i=i, var=var: (
                self.mod_row_set(i, "knob", knobs[knob_names.index(var.get())]), self.keyboard_back(e.widget)))
            Tooltip(cb, tr("hz.synth_tip_mod_col_knob"))
            cell = ttk.Frame(self.mod_rows, style="Synth.Box.TFrame")
            cell.grid(row=r, column=2, sticky="w", padx=(0, 10))
            var = tk.StringVar(value=fmt(round(100 * link["amount"], 1)))
            e = ttk.Entry(cell, textvariable=var, width=6, justify="center", style=ENTRY)
            e.pack(side="left")
            ttk.Label(cell, text=tr("hz.synth_percent"), style="Synth.Box.Dim.TLabel").pack(side="left", padx=(2, 0))
            e.bind("<Return>", lambda ev, i=i, e=e: (self.on_mod_amount(i), self.keyboard_back(e), "break")[2])
            e.bind("<FocusOut>", lambda ev, i=i: self.on_mod_amount(i))
            Scrub(self.app, [(e, var, lambda i=i: self.on_mod_amount(i))], (1, 10, 0.1), -100.0, 100.0, drag_box=True)
            Tooltip(e, tr("hz.synth_tip_mod_col_amount"))
            self.mod_amount_vars.append(var)
            self.mod_amount_boxes.append(e)
            self.mod_amount_text[i] = var.get()
            both = tk.BooleanVar(value=bool(link.get("bipolar")))
            cb = ttk.Checkbutton(self.mod_rows, text=tr("hz.synth_mod_both"), variable=both,
                                 style="Synth.Box.TCheckbutton",
                                 command=lambda i=i, both=both: self.mod_row_set(i, "bipolar", both.get()))
            cb.grid(row=r, column=3, sticky="w", padx=(0, 10))
            Tooltip(cb, tr("hz.synth_tip_mod_both"))
            x = ttk.Button(self.mod_rows, text="✕", width=3, style="Synth.TButton",
                           command=lambda i=i: self.mod_row_remove(i))
            x.grid(row=r, column=4, sticky="w")
            Tooltip(x, tr("hz.synth_tip_mod_remove"))
        for e in self.mod_amount_boxes:  # (a letter that plays a key, typed there: as in the other boxes)
            e.bindtags((f"SynthLetters{id(self)}",) + e.bindtags())
        for w in [self.mod_rows] + list(self.mod_rows.winfo_children()):
            if not isinstance(w, (ttk.Combobox, ttk.Frame)):
                w.bind("<MouseWheel>", self.mod_list_wheel, add="+")

    def mod_row_set(self, i, key, value):
        """A row's source, knob or both-ways changed: one undo step (a source + line linked twice: a ding)."""
        links = self.mod_links()
        if i >= len(links):
            return
        link = dict(links[i])
        if key == "knob":
            link["knob"], link["to"] = value, MOD_KNOBS[value]
        elif key == "bipolar":
            link.pop("bipolar", None)
            if value:
                link["bipolar"] = True
        else:
            link[key] = value
        if any(j != i and (o["from"], o["to"]) == (link["from"], link["to"]) for j, o in enumerate(links)):
            self.bell()
            self.mod_rows_for = None  # (the row shows the link as it is again)
            return self.show_mod()
        self.mod_step(lambda m: m["links"].__setitem__(i, link))

    def on_mod_amount(self, i):
        """A row's amount typed (or stepped), in % (-100 to 100)."""
        if i >= len(self.mod_amount_vars):
            return
        e, var = self.mod_amount_boxes[i], self.mod_amount_vars[i]
        e.config(style=ENTRY)
        if var.get() == self.mod_amount_text.get(i):
            return
        try:
            v = float(calc(var.get()))
            if not -100 <= v <= 100:
                raise ValueError
        except (ValueError, ZeroDivisionError):  # (back to the last good value)
            var.set(self.mod_amount_text.get(i, "0"))
            return
        self.mod_amount_text[i] = var.get()
        links = self.mod_links()
        if i < len(links) and abs(links[i]["amount"] - v / 100) > 1e-9:
            self.mod_step(lambda m: m["links"][i].__setitem__("amount", v / 100))

    def mod_row_remove(self, i):
        self.mod_step(lambda m: m["links"].pop(i) if i < len(m["links"]) else None)

    def mod_add(self):
        """+ Add link: LFO 3 (or the next source) on the first knob it doesn't move yet, at 50 %."""
        links = self.mod_links()
        taken = {(link["from"], link["to"]) for link in links}
        for src in MOD_SOURCES:
            for key, to in MOD_KNOBS.items():
                if (src, to) not in taken:
                    return self.mod_step(lambda m: m["links"].append({"from": src, "to": to, "amount": LINK_AMOUNT,
                                                                      "knob": key}))
        self.bell()

    def build_mod_names(self, strip, col):
        """The sources' names in the macros' strip (column col): drag one onto a knob to link them."""
        s = self.s
        cell = ttk.Frame(strip, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, sticky="w", padx=(round(14 * s), 0))
        ttk.Label(cell, text=tr("hz.synth_mod_names"), style="Synth.Box.Dim.TLabel").grid(row=0, column=0, columnspan=3,
                                                                                          sticky="w")
        self.mod_names = {}
        for i, src in enumerate(MOD_SOURCES):
            name = self.mod_names[src] = tk.Label(cell, text=source_name(src), background=PANEL,
                                                  foreground=SOURCE_COLOURS[src], font=HEAD_FONT, cursor="fleur",
                                                  padx=4)
            name.grid(row=1 + i // 3, column=i % 3, sticky="w", padx=(0, 4))
            name.bind("<ButtonPress-1>", lambda e, src=src: self.mod_press(src))
            name.bind("<B1-Motion>", self.mod_motion)
            name.bind("<ButtonRelease-1>", lambda e: self.mod_release())
            name.bind("<ButtonPress-3>", lambda e, src=src: self.mod_menu(src, e))
            Tooltip(name, tr(f"hz.synth_tip_mod_{src}") + "\n" + tr("hz.synth_tip_mod_name"))

    def mod_choice(self, box, col, src, what, ids):
        """One of an LFO box's dropdowns (Shape, Plays, Timing)."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6, sticky="n")
        label = "hz.synth_timing" if what == "timing" else f"hz.synth_mod_{what}"
        ttk.Label(cell, text=tr(label), style="Synth.Box.TLabel").pack()
        names = [tr(f"hz.synth_timing_{i}" if what == "timing" else f"hz.fx_shape_{i}" if what == "shape"
                    else f"hz.synth_mod_mode_{i}") for i in ids]
        if what == "shape":  # (short: the box stays small)
            names = [n.split(" (")[0] for n in names]
        var = tk.StringVar(value=names[0])
        cb = ttk.Combobox(cell, textvariable=var, values=names, state="readonly",
                          width=max(len(n) for n in names) + 1, style="Synth.TCombobox")
        dark_list(cb)
        cb.pack(pady=(12, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: (self.on_mod_choice(src, what, ids[names.index(var.get())]),
                                                    self.keyboard_back(e.widget)))
        Tooltip(cb, tr("hz.synth_tip_timing" if what == "timing" else f"hz.synth_tip_mod_{what}"))
        self.mod_picks[(src, what)] = (var, names, ids)

    def mod_dial_cell(self, box, col, src, key, kind):
        """A source's knob with its name over it and its value's box under it."""
        cell = ttk.Frame(box, style="Synth.Box.TFrame")
        cell.grid(row=0, column=col, padx=6)
        ttk.Label(cell, text=tr("hz.synth_mod_rate" if key == "rate" else f"hz.synth_{key}"),
                  style="Synth.Box.TLabel").pack()
        start = mod_start()["lfo" if src in MOD_LFOS else "env"][0][key]
        k = Dial(cell, self.s, lambda v, done: self.on_mod_dial(src, key, kind, v, done), SOURCE_COLOURS[src],
                 size=46, start=knob_of(kind, start))
        k.pack()
        row = ttk.Frame(cell, style="Synth.Box.TFrame")
        row.pack(pady=(2, 0))
        var = tk.StringVar()
        e = ttk.Entry(row, textvariable=var, width=5, justify="center", style=ENTRY)
        e.pack(side="left")
        unit, lo, hi, steps, _ = KINDS[kind]
        unit_label = ttk.Label(row, text=tr(unit) if unit else "", style="Synth.Box.Dim.TLabel")
        unit_label.pack(side="left", padx=(2, 0))
        e.bind("<Return>", lambda ev: (self.on_mod_box(src, key, kind), self.keyboard_back(e), "break")[2])
        e.bind("<FocusOut>", lambda ev: self.on_mod_box(src, key, kind))
        Scrub(self.app, [(e, var, lambda: self.on_mod_box(src, key, kind, stepped=True))], steps, lo, hi,
              drag_box=True)
        tip = tr("hz.synth_tip_mod_rate" if key == "rate" else "hz.synth_tip_mod_env")
        for w in (k, e):
            Tooltip(w, tip + "\n" + tr("hz.synth_tip_knob"))
        self.mod_dials[(src, key)], self.mod_vars[(src, key)], self.mod_boxes[(src, key)] = k, var, e
        if key == "rate":
            self.mod_rate_units = getattr(self, "mod_rate_units", {})
            self.mod_rate_units[src] = (unit_label, tr(unit))

    # ------------------------------------------------------------ the sources' settings

    def mod(self):
        """hz["mod"] as a copy to change (as it starts when there's none)."""
        return copy.deepcopy(self.extra.get("mod") or mod_start())

    def source(self, m, src):
        """A source's settings in m (an LFO's or an envelope's)."""
        return m["lfo"][MOD_LFOS.index(src)] if src in MOD_LFOS else m["env"][MOD_ENVS.index(src)]

    def mod_set(self, src, key, v):
        m = self.mod()
        self.source(m, src)[key] = v
        self.set_extra("mod", m)
        self.redraw()
        self.show_mod()

    def mod_rate(self, src, v, step=0):
        """An LFO's Rate at its Timing (as the Vibrato's: the nearest note length; step: at least one that way)."""
        lfo = self.source(self.mod(), src)
        timing, now = lfo["timing"], lfo["rate"]
        hi = KINDS["vib_rate"][2]
        if timing == "free":
            return v
        got = snap_rate(v, timing, hi)
        if step and abs(got - now) < 1e-9:
            rates = timed_rates(timing, hi)
            got = (next((r for r in rates if r > now + 1e-9), now) if step > 0
                   else next((r for r in reversed(rates) if r < now - 1e-9), now))
        return got

    def on_mod_dial(self, src, key, kind, k, done):
        """A source's knob turned (done: let go / one step = one undo step)."""
        if self.turning is None:
            self.turning, self.turn_vals = self.fx.state(), dict(self.vals)
        v = value_of(kind, k)
        if key == "rate":
            v = self.mod_rate(src, v, self.mod_dials[(src, key)].stepping)
        self.mod_set(src, key, v)
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

    def on_mod_box(self, src, key, kind, stepped=False):
        """A source's value typed (or stepped) in the box under its knob."""
        e, var = self.mod_boxes[(src, key)], self.mod_vars[(src, key)]
        e.config(style=ENTRY)
        if var.get() == self.mod_text.get((src, key)):
            return
        lo, hi = KINDS[kind][1:3]
        try:
            v = float(calc(var.get()))
            if not lo <= v <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):  # (back to the last good value, as the other knobs' boxes)
            var.set(self.mod_text.get((src, key), ""))
            return
        self.mod_text[(src, key)] = var.get()
        v = v / 100 if kind in PERCENTS else v
        now = self.source(self.mod(), src)[key]
        if key == "rate":
            v = self.mod_rate(src, v, (v > now) - (v < now) if stepped else 0)
        if abs(v - now) > 1e-9:
            before = self.fx.state()
            self.mod_set(src, key, v)
            if self.fx.now() != before:
                self.commit_fx(before)
        else:
            self.show_mod(force=True)

    def on_mod_choice(self, src, what, value):
        """An LFO's Shape, Plays or Timing picked: one undo step (a Timing snaps its Rate to a note length)."""
        before = self.fx.state()
        m = self.mod()
        lfo = self.source(m, src)
        lfo[what] = value
        if what == "timing":
            lfo["rate"] = snap_rate(lfo["rate"], value, KINDS["vib_rate"][2])
        self.set_extra("mod", m)
        self.redraw()
        self.show_mod()
        if self.fx.now() != before:
            self.commit_fx(before)

    # ------------------------------------------------------------ linking

    def mod_links(self, src=None):
        """The links (of one source, or all)."""
        return [link for link in (self.extra.get("mod") or {}).get("links", ()) if src in (None, link["from"])]

    def mod_press(self, src):
        self.pick_mod(src)
        self.macro_held = [("mod", src), None, False]  # (as a macro's name: Ctrl+Z / Esc calls it off)
        self.macro_wait = None

    def mod_motion(self, e):
        """A source's name dragged: a knob it can move that the mouse rests on for REST_MS is lit in its colour, and
        only then a let-go links it (as the macros)."""
        held = self.macro_held
        if not held:
            return
        key = self.knob_at(e.x_root, e.y_root)
        key = key if key in MOD_KNOBS else None
        if key != held[1]:
            self.macro_hover(held[1], None)
            held[1], held[2] = key, False
            if self.macro_wait:
                self.after_cancel(self.macro_wait)
            self.macro_wait = self.after(REST_MS, self.mod_arm) if key else None

    def mod_arm(self):
        self.macro_wait = None
        held = self.macro_held
        if held and held[1]:
            held[2] = True
            self.macro_hover(held[1], SOURCE_COLOURS[held[0][1]])

    def mod_release(self):
        """A source's name let go on a knob it rested on: linked (LINK_AMOUNT of the line's height), one undo step;
        already linked (that knob, or another making the same line): just picked."""
        held = self.macro_held
        self.macro_drop()
        if not held or not held[1] or not held[2]:
            return
        src, key = held[0][1], held[1]
        if any(link["to"] == MOD_KNOBS[key] for link in self.mod_links(src)):
            return self.show_macros()
        self.mod_step(lambda m: m["links"].append({"from": src, "to": MOD_KNOBS[key], "amount": LINK_AMOUNT,
                                                   "knob": key}))

    def mod_step(self, change):
        """One change of the links (change(m) edits a copy of hz["mod"]): one undo step."""
        before = self.fx.state()
        m = self.mod()
        change(m)
        self.set_extra("mod", m)
        self.redraw()
        self.show_knobs()
        if self.fx.now() != before:
            self.commit_fx(before)

    def pick_mod(self, src):
        if self.mod_pick != src:
            self.mod_pick = src
            self.show_macros()

    def pick_macro(self, i):
        """A macro clicked: its rings show again (no source picked)."""
        if self.mod_pick is not None:
            self.mod_pick = None
            self.macro_pick = i
            return self.show_macros()
        super().pick_macro(i)

    def ring_drag(self, key, dy, fine, done):
        """A knob's ring dragged while a source is picked: how far it moves the knob (let go at 0 = unlinked), one
        undo step; else the picked macro's (SynthMacros)."""
        src = self.mod_pick
        if src is None:
            return super().ring_drag(key, dy, fine, done)
        if self.turning is None:
            self.turning, self.turn_vals = self.fx.state(), dict(self.vals)
            self.ring_from = next((link["amount"] for link in self.mod_links(src) if link["to"] == MOD_KNOBS.get(key)),
                                  0.0)
        m = self.mod()
        link = next((link for link in m["links"] if link["from"] == src and link["to"] == MOD_KNOBS.get(key)), None)
        if link is None:  # (unlinked meanwhile, e.g. by Ctrl+Z)
            return
        if dy is not None:  # (as a macro's ring: the line's whole height = the knob's whole turn)
            link["amount"] = max(-1.0, min(1.0, self.ring_from + dy * (0.001 if fine else 0.005)))
            if abs(link["amount"]) < 0.01:  # (sticks at 0 on the way past)
                link["amount"] = 0.0
        elif link["amount"] == 0.0:
            m["links"].remove(link)
        self.set_extra("mod", m)
        self.redraw()
        self.show_macros()
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

    def mod_menu(self, src, e):
        """A source's name right-clicked: its links, each to unlink, and Unlink all."""
        self.pick_mod(src)
        menu = tk.Menu(self, tearoff=0)
        dark_menu(menu)
        links = self.mod_links(src)
        for link in links:
            menu.add_command(label=tr("hz.synth_macro_unlink", knob=self.mod_target(link),
                                      amount=fmt(round(link["amount"] * 100))),
                             command=lambda to=link["to"]: self.mod_unlink(src, {to}))
        if not links:
            menu.add_command(label=tr("hz.synth_macro_none"), state="disabled")
        menu.add_separator()
        menu.add_command(label=tr("hz.synth_macro_unlink_all"), state="normal" if links else "disabled",
                         command=lambda: self.mod_unlink(src, {link["to"] for link in links}))
        menu.tk_popup(e.x_root, e.y_root)

    def mod_target(self, link):
        """The knob a link moves, as "Box: Knob"."""
        return self.knob_label(ring_knob(link))

    def mod_unlink(self, src, tos):
        self.mod_step(lambda m: m.__setitem__("links", [link for link in m["links"]
                                                        if not (link["from"] == src and link["to"] in tos)]))

    def cancel_turn(self):
        for dial in self.mod_dials.values():
            dial.drag, dial.pointing, dial.ring_held = None, False, None
        return super().cancel_turn()

    # ------------------------------------------------------------ showing them

    def show_knobs(self):
        super().show_knobs()
        self.show_mod()

    def show_macros(self):
        """As SynthMacros', then (a source picked) its rings instead of the macros', its name lit."""
        super().show_macros()
        if not hasattr(self, "mod_names"):
            return
        src = self.mod_pick
        for s, name in self.mod_names.items():
            back = MID if s == src else PANEL
            if name.cget("background") != back:
                name.config(background=back)
        if src is None:
            return
        for i, name in enumerate(self.macro_names):  # (no macro picked meanwhile)
            if name.cget("background") != PANEL:
                name.config(background=PANEL)
        mine = {ring_knob(link): link for link in self.mod_links(src)}
        colour = SOURCE_COLOURS[src]
        for key, k in self.dials.items():
            link = mine.get(key)
            ring = None
            if link is not None:
                v = line_of(key, k.value)
                a = link["amount"]
                reach = knob_of_line(key, v + a)
                low = knob_of_line(key, v - a) if link.get("bipolar") else None
                ring = (reach, colour, k.value, low)
            if ring != k.ring:
                k.ring = ring
                k.draw()

    def show_mod(self, force=False):
        """The MOD tab's knobs, boxes, dropdowns and pictures as hz["mod"] has them; each source's light lit while
        it moves a knob."""
        if not hasattr(self, "mod_dials"):  # (not built yet)
            return
        m = self.mod()
        linked = {link["from"] for link in m["links"]}
        for src, box in self.mod_boxes_of.items():
            box.lamp.light(src in linked)
        for (src, key), k in self.mod_dials.items():
            kind = dict(LFO_KNOBS + ENV_KNOBS)[key]
            v = self.source(m, src)[key]
            if not k.drag and abs(k.value - knob_of(kind, v)) > 0.05:
                k.set(knob_of(kind, v))
            text = fmt(round(shown(kind, v), 2))
            e, var = self.mod_boxes[(src, key)], self.mod_vars[(src, key)]
            typing = self.focus_get() is e and var.get() != self.mod_text.get((src, key)) and not force
            if var.get() != text and not typing:
                var.set(text)
            if not typing:
                self.mod_text[(src, key)] = text
        for src, (label, unit) in getattr(self, "mod_rate_units", {}).items():
            lfo = self.source(m, src)
            says = note_name(lfo["rate"], lfo["timing"]) or unit
            if label.cget("text") != says:
                label.config(text=says)
        for (src, what), (var, names, ids) in self.mod_picks.items():
            name = names[ids.index(self.source(m, src)[what])]
            if var.get() != name:
                var.set(name)
        self.draw_mod_pics()
        self.show_mod_list(m["links"])

    def show_mod_list(self, links):
        """The list as the links are: rows made again when a source / knob / both-ways changed (a moment later: not
        under a dropdown's own event), amounts set in place (not while typed there)."""
        self.mod_list_box.lamp.light(bool(links))
        says = "" if links else tr("hz.synth_mod_list_none")
        if self.mod_list_empty.cget("text") != says:
            self.mod_list_empty.config(text=says)
        shape = [(link["from"], link["to"], ring_knob(link), bool(link.get("bipolar"))) for link in links]
        if shape != self.mod_rows_for:
            if self.mod_rows_for != "waiting":
                self.mod_rows_for = "waiting"
                self.after_idle(self.mod_rows_now)
            return
        for i, link in enumerate(links):
            text = fmt(round(100 * link["amount"], 1))
            e, var = self.mod_amount_boxes[i], self.mod_amount_vars[i]
            typing = self.focus_get() is e and var.get() != self.mod_amount_text.get(i)
            if var.get() != text and not typing:
                var.set(text)
            if not typing:
                self.mod_amount_text[i] = text

    def mod_rows_now(self):
        if not self.winfo_exists():
            return
        links = self.mod_links()
        self.make_mod_rows(links)
        self.mod_rows_for = [(link["from"], link["to"], ring_knob(link), bool(link.get("bipolar"))) for link in links]
        self.show_mod_list(links)

    def draw_mod_pics(self):
        """Each source's picture: an LFO's shape over two waves (Once: one, then its last value), an envelope's
        rise, fall, hold and release."""
        m = self.mod()
        for src, c in self.mod_pics.items():
            w, h = c.winfo_width(), c.winfo_height()
            got = self.source(m, src)
            key = (repr(got), w, h)
            if w < 20 or self.mod_pic_for.get(src) == key:
                continue
            self.mod_pic_for[src] = key
            c.delete("all")
            pad = 6 * self.s
            colour = SOURCE_COLOURS[src]
            if src in MOD_LFOS:
                pts = loop_shape(got["shape"], 1.0, seed=MOD_LFOS.index(src))
                u = np.linspace(0.0, 2.0, 400)
                v = line_at(pts, np.minimum(u, 1.0)) if got["mode"] == "once" else line_at(pts, u, 1.0)
                c.create_line(w / 2, pad, w / 2, h - pad, fill=GRID, dash=(2, 3))
            else:
                pts, at, every = adsr_line(got["attack"], got["decay"], got["sustain"], got["release"])
                held = at + max(0.5, at / 2)  # (held a while past the sustain point, then let go)
                u = np.linspace(0.0, held + every - at, 400)
                v = np.where(u <= held, line_at(pts, np.minimum(u, at)), line_at(pts, at + (u - held)))
                x = pad + (w - 2 * pad) * held / u[-1]
                c.create_line(x, pad, x, h - pad, fill=GRID, dash=(2, 3))
                u = u * 2.0 / u[-1]
            xs = pad + (w - 2 * pad) * u / 2.0
            ys = h - pad - (h - 2 * pad) * np.asarray(v, float)
            flat = np.column_stack([xs, ys]).ravel().tolist()
            c.create_polygon(*flat, xs[-1], h - pad, xs[0], h - pad, fill=mix(colour, PIC, 0.75), outline="")
            c.create_line(*flat, fill=colour, width=2)
