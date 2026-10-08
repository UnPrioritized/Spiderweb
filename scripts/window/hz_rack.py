"""The synth window's Effects tab (user 2026-10-05: a rack, as many synths have). On the left, every effect in a list
(lit = in use; a click puts it in or takes it out, dragging one in use changes the order); on the right, a strip for
each effect in use, in order (Chorus, Flanger, Echo, Reverb look-alike, Compressor): a tag with its name, its knobs,
a picture, and a power
button (off = kept but silent); a scroll bar when they don't fit. They're the Hz bass's own setting hz["rack"]
(hzbass.RACK), not lines; one undo step a change. The knobs are the Knobs tab's (hz_knobs.RACK_KNOBS)."""

import numpy as np
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.hzbass import RACK, SOFT, compress
from window.hz_knobs import RACK_KNOBS
from window.synth_look import BG, DIM, EDGE, HEAD_FONT, LIGHT_OFF, MID, PANEL, PIC, TEXT, bright, mix
from window.widgets import DRAG_PX, Tooltip

COLOURS = {k: bright(v) for k, v in {"chorus": "#2a9d8f", "flanger": "#3a8fd0", "echo": "#d07a1e", "reverb": "#6a5acd",
                                     "compressor": "#c94c4c"}.items()}
PICTURE = (240, 64)
LIST_W, ROW_H = 215, 30  # the list of effects on the left: its width, a row's height


class SynthRack:
    """The Effects tab of SynthWindow (needs SynthKnobs: dial_cell, vals, write, fx, commit_fx, extra)."""

    def build_rack(self, page):
        s = self.s
        lst = self.rack_list = tk.Canvas(page, width=round(LIST_W * s), background=PANEL, highlightthickness=1,
                                         highlightbackground=EDGE, cursor="hand2")
        lst.pack(side="left", fill="y", padx=(0, round(10 * s)))
        lst.bind("<Configure>", lambda e: self.draw_rack_list())
        lst.bind("<ButtonPress-1>", self.list_press)
        lst.bind("<B1-Motion>", self.list_drag)
        lst.bind("<ButtonRelease-1>", self.list_release)
        Tooltip(lst, tr("hz.rack_list_tip"))
        self.list_held = None  # a row pressed: {kind, y, moved, to}
        right = ttk.Frame(page, style="Synth.TFrame")
        right.pack(side="left", fill="both", expand=True)
        rc = self.rack_canvas = tk.Canvas(right, highlightthickness=0, bd=0, background=BG)
        self.rack_bar = ttk.Scrollbar(right, orient="vertical", command=rc.yview, style="Synth.Vertical.TScrollbar")
        rc.configure(yscrollcommand=self.rack_bar.set)
        rc.pack(side="left", fill="both", expand=True)
        self.rack_inner = ttk.Frame(rc, style="Synth.TFrame")
        self.rack_win = rc.create_window(0, 0, window=self.rack_inner, anchor="nw")
        rc.bind("<Configure>", lambda e: self.fit_rack())
        self.rack_inner.bind("<Configure>", lambda e: self.after_idle(self.fit_rack))
        self.rack_hint = ttk.Label(self.rack_inner, text=tr("hz.rack_empty"), style="Synth.Dim.TLabel",
                                   wraplength=round(500 * s))
        self.strips, self.strip_on, self.strip_tags, self.strip_power = {}, {}, {}, {}
        self.rack_pics, self.rack_pic_for, self.rack_laid = {}, {}, None
        wide = max(len(tr(f"hz.rack_{k}")) for k in RACK) + 1  # (the tags as wide as the longest name)
        for kind, knobs in RACK_KNOBS.items():
            strip = self.strips[kind] = tk.Frame(self.rack_inner, background=PANEL, highlightthickness=1,
                                                 highlightbackground=EDGE)
            tag = self.strip_tags[kind] = tk.Label(strip, text=tr(f"hz.rack_{kind}").upper(), font=HEAD_FONT,
                                                   width=wide, anchor="w", padx=round(8 * s), pady=round(4 * s))
            tag.pack(side="left", padx=(round(10 * s), 0))
            power = self.strip_power[kind] = tk.Canvas(strip, width=round(30 * s), height=round(30 * s),
                                                       background=PANEL, highlightthickness=0, cursor="hand2")
            power.pack(side="right", padx=round(10 * s))
            power.bind("<ButtonPress-1>", lambda e, kind=kind: self.power_click(kind))
            Tooltip(power, tr("hz.rack_on_tip"))
            self.strip_on[kind] = tk.BooleanVar(value=True)
            body = ttk.Frame(strip, style="Synth.Box.TFrame", padding=(8, 6, 8, 8))
            body.pack(side="left", fill="y")
            col = 0
            for key, k in knobs:
                self.dial_cell(body, col, key, k, RACK[kind][key.split("_", 1)[1]][2], COLOURS[kind])
                col += 1
            pic = self.rack_pics[kind] = tk.Canvas(body, width=round(PICTURE[0] * s), height=round(PICTURE[1] * s),
                                                   background=PIC, highlightthickness=1, highlightbackground=EDGE)
            pic.grid(row=0, column=col, sticky="n", padx=(12, 0), pady=(4, 0))
            pic.bind("<Configure>", lambda e: self.draw_rack_pics())

    def fit_rack(self):
        """The strips as wide as the tab, a scroll bar when they're taller than it."""
        c = self.rack_canvas
        if not self.winfo_exists() or c.winfo_width() < 50:
            return
        need, have = self.rack_inner.winfo_reqheight(), c.winfo_height()
        c.itemconfigure(self.rack_win, width=c.winfo_width(), height=max(need, have))
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(need, have)))
        if need > have + 1:
            if not self.rack_bar.winfo_ismapped():
                self.rack_bar.pack(side="right", fill="y", before=c)
        elif self.rack_bar.winfo_ismapped():
            self.rack_bar.pack_forget()
            c.yview_moveto(0)

    # ------------------------------------------------------------ the list on the left

    def list_order(self):
        """The list's rows: the effects in use in their order, then the others."""
        order = self.vals["rack"]
        return list(order) + [k for k in RACK if k not in order]

    def list_row(self, y):
        rows = self.list_order()
        i = int(y // (ROW_H * self.s))
        return rows[i] if 0 <= y and i < len(rows) else None

    def list_press(self, e):
        kind = self.list_row(e.y)
        self.list_held = {"kind": kind, "y": e.y, "moved": False, "to": None} if kind else None

    def list_drag(self, e):
        """Dragging an effect in use up / down: a line shows where it goes."""
        h = self.list_held
        if not h or h["kind"] not in self.vals["rack"]:
            return
        if abs(e.y - h["y"]) >= DRAG_PX:
            h["moved"] = True
        if h["moved"]:
            n = len(self.vals["rack"])
            h["to"] = min(n, max(0, round(e.y / (ROW_H * self.s))))
            self.draw_rack_list()

    def list_release(self, e):
        """A click: the effect put in / taken out (let go off its row: nothing, like a menu); a drag: moved there."""
        h, self.list_held = self.list_held, None
        if not h:
            return
        kind = h["kind"]
        if h["moved"]:
            if h["to"] is not None and kind in self.vals["rack"]:
                self.rack_move(kind, h["to"])
            self.draw_rack_list()
        elif not 0 <= e.x < self.rack_list.winfo_width() or self.list_row(e.y) != kind:
            return
        elif kind in self.vals["rack"]:
            self.rack_remove(kind)
        else:
            self.rack_add(kind)

    def cancel_list(self):
        """Ctrl+Z while an effect in the list is held: the drag is called off (its let-go does nothing), no undo
        step."""
        self.list_held = None
        self.draw_rack_list()
        return True

    def draw_rack_list(self):
        c, s = self.rack_list, self.s
        c.delete("all")
        w, rh = c.winfo_width(), ROW_H * s
        order = self.vals["rack"]
        for i, kind in enumerate(self.list_order()):
            y0, used = i * rh, kind in order
            on = used and kind not in self.vals["rack_off"]
            colour = COLOURS[kind]
            c.create_rectangle(0, y0, w, y0 + rh, fill=PANEL if used else mix(PANEL, BG, 0.5), outline=EDGE)
            r = 6 * s
            x, y = 10 * s, y0 + rh / 2
            c.create_rectangle(x, y - r / 1.5, x + 2 * r, y + r / 1.5, fill=colour if on else
                               mix(colour, PANEL, 0.6) if used else PANEL, outline=colour if used else LIGHT_OFF)
            c.create_text(x + 2 * r + 8 * s, y, text=tr(f"hz.rack_{kind}").upper(), anchor="w",
                          fill=TEXT if used else DIM, font=HEAD_FONT)
            if used:
                c.create_text(w - 10 * s, y, text="↕", anchor="e", fill=DIM, font=("Segoe UI", 9))
        h = self.list_held
        if h and h["moved"] and h["to"] is not None:
            y = h["to"] * rh
            c.create_line(4 * s, y, w - 4 * s, y, fill=TEXT, width=max(2, round(2 * s)))

    def power_click(self, kind):
        var = self.strip_on[kind]
        var.set(not var.get())
        self.rack_switch(kind)

    # ------------------------------------------------------------ changes (one undo step each)

    def rack_edit(self, order, off):
        before = self.fx.state()
        self.vals["rack"], self.vals["rack_off"] = tuple(order), tuple(off)
        self.write_rack()
        self.keep_vals()  # (an effect taken out: its knobs kept)
        self.redraw()
        self.show_knobs()
        if self.fx.now() != before:
            self.commit_fx(before)

    def rack_add(self, kind):
        if kind not in self.vals["rack"]:
            self.rack_edit(self.vals["rack"] + (kind,), [k for k in self.vals["rack_off"] if k != kind])

    def rack_remove(self, kind):
        self.rack_edit([k for k in self.vals["rack"] if k != kind], [k for k in self.vals["rack_off"] if k != kind])

    def rack_move(self, kind, to):
        """An effect in use moved to row `to` (counted before it's taken out of its place)."""
        order = list(self.vals["rack"])
        at = order.index(kind)
        order.pop(at)
        order.insert(to - (to > at), kind)
        if tuple(order) != self.vals["rack"]:
            self.rack_edit(order, self.vals["rack_off"])

    def rack_switch(self, kind):
        off = [k for k in self.vals["rack_off"] if k != kind] + ([] if self.strip_on[kind].get() else [kind])
        self.rack_edit(self.vals["rack"], off)

    def write_rack(self):
        """hz["rack"] made from the strips' knobs."""
        v = self.vals
        self.set_extra("rack", [{"kind": k, **{key.split("_", 1)[1]: v[key] for key, _ in RACK_KNOBS[k]},
                                 **({"off": True} if k in v["rack_off"] else {})} for k in v["rack"]])

    # ------------------------------------------------------------ showing it

    def show_rack(self):
        """The strips of the effects in use, in order (laid again only when that changes), their power buttons,
        tags and pictures, and the list."""
        order = self.vals["rack"]
        if order != self.rack_laid:
            self.rack_laid = order
            for strip in self.strips.values():
                strip.pack_forget()
            self.rack_hint.pack_forget()
            for kind in order:
                self.strips[kind].pack(fill="x", anchor="w", pady=(0, 8))
            if not order:
                self.rack_hint.pack(anchor="w", pady=(4, 0))
        for kind, var in self.strip_on.items():
            on = kind not in self.vals["rack_off"]
            if var.get() != on:
                var.set(on)
            self.strip_tags[kind].config(background=COLOURS[kind] if on else MID, foreground=PIC if on else DIM)
            self.draw_power(kind, on)
        self.draw_rack_list()
        self.draw_rack_pics()

    def draw_power(self, kind, on):
        """A strip's power button: a ring with a stroke at the top, in its colour while on."""
        c, s = self.strip_power[kind], self.s
        c.delete("all")
        d = c.winfo_reqwidth()
        colour = COLOURS[kind] if on else LIGHT_OFF
        m, w = 6 * s, max(2, round(2 * s))
        c.create_oval(2 * s, 2 * s, d - 2 * s, d - 2 * s, fill=mix(colour, PANEL, 0.75) if on else PANEL,
                      outline=EDGE)
        c.create_arc(m, m, d - m, d - m, start=120, extent=300, style="arc", outline=colour, width=w)
        c.create_line(d / 2, m - 1 * s, d / 2, d / 2, fill=colour, width=w, capstyle="round")

    def draw_rack_pics(self):
        for kind, c in self.rack_pics.items():
            key = (tuple(self.vals[k] for k, _ in RACK_KNOBS[kind]), kind in self.vals["rack_off"], c.winfo_width(),
                   c.winfo_height())
            if c.winfo_width() < 50 or self.rack_pic_for.get(kind) == key:
                continue
            self.rack_pic_for[kind] = key
            c.delete("all")
            getattr(self, "draw_rack_" + kind)(c, COLOURS[kind] if kind not in self.vals["rack_off"] else MID)

    def rack_text(self, c, text, low=False):
        """A few words in a picture's top right corner (low: bottom right, under a line that ends high)."""
        s = self.s
        c.create_text(c.winfo_width() - 3 * s, c.winfo_height() - 2 * s if low else 2 * s, text=text,
                      anchor="se" if low else "ne", fill=DIM, font=("Segoe UI", 7))

    def draw_rack_chorus(self, c, colour):
        """Every other key's tone over two beats: up to Depth cents and back."""
        v = self.vals
        b = np.linspace(0.0, 2.0, 400)
        self.wobble(c, v["chorus_depth"] / 100.0 * (1.0 - np.cos(2.0 * np.pi * v["chorus_rate"] * b)) / 2.0, colour,
                    False)
        self.rack_text(c, tr("hz.rack_no_extra"))

    def draw_rack_flanger(self, c, colour):
        """How late the moving keys hit over two beats: up to Depth of a wave and back."""
        v = self.vals
        b = np.linspace(0.0, 2.0, 400)
        self.wobble(c, v["flanger_depth"] * (1.0 - np.cos(2.0 * np.pi * v["flanger_rate"] * b)) / 2.0, colour, False)
        self.rack_text(c, tr("hz.rack_no_extra"))

    def draw_rack_compressor(self, c, colour):
        """How loud a sound comes out (up) for how loud it goes in (across), in dB from -48 to 0: straight up to the
        threshold (dashed), flatter past it, all lifted by Gain."""
        s, v = self.s, self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        lo = RACK["compressor"]["threshold"][0]
        comp = {k: v["compressor_" + k] for k in RACK["compressor"]}
        db = np.linspace(lo, 0.0, 200)
        out = 20.0 * np.log10(np.maximum(compress(10.0 ** (db / 20.0), np.full(len(db), 1e9), comp), 1e-12))
        x = lambda d: pad + (d - lo) / -lo * (w - 2 * pad)
        y = lambda d: h - pad - (min(0.0, max(lo, d)) - lo) / -lo * (h - 2 * pad)
        c.create_line(x(lo), y(lo), x(0.0), y(0.0), fill=MID)  # (as it went in)
        xt = x(v["compressor_threshold"])
        c.create_line(xt, pad, xt, h - pad, fill=DIM, dash=(3, 3))
        c.create_line(*[q for d, o in zip(db, out) for q in (x(d), y(o))], fill=colour, width=max(2, round(2 * s)))
        self.rack_text(c, tr("hz.rack_no_extra"), low=True)

    def draw_rack_echo(self, c, colour):
        """The sound and its repeats: a bar for each, as loud as it is, Time apart."""
        s, v = self.s, self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        n = int(v["echo_repeats"])
        heard = [i for i in range(n + 1) if v["echo_fade"] ** i >= SOFT]
        bw = max(3.0, (w - 2 * pad) / (n + 1) * 0.6)
        for i in heard:  # (spread by the repeats only: Time 0 while the knob is turned all the way down)
            x = pad + i / (n + 1) * (w - 2 * pad)
            top = h - pad - v["echo_fade"] ** i * (h - 3 * pad)
            c.create_rectangle(x, top, x + bw, h - pad, fill=colour if i else "#6b737e", outline="")
        c.create_line(pad, h - pad, w - pad, h - pad, fill=MID)
        self.rack_text(c, tr("hz.rack_echo_notes", n=len(heard) - 1))

    def draw_rack_reverb(self, c, colour):
        """A note, then its tone ringing on: fading, its waves more and more scattered."""
        s, v = self.s, self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        note = (w - 2 * pad) * 0.25
        c.create_rectangle(pad, h / 2 - 3 * s, pad + note, h / 2 + 3 * s, fill="#6b737e", outline="")
        rng = np.random.default_rng(7)
        u = np.linspace(0.0, 1.0, 60, endpoint=False)
        loud = v["reverb_level"] * (1.0 - u) ** 1.5
        x = pad + note + (u + v["reverb_scatter"] * 0.5 * u * rng.random(len(u)) / 60) * (w - 2 * pad - note)
        y = h / 2 + (rng.random(len(u)) - 0.5) * v["reverb_scatter"] * u * (h - 2 * pad)
        for xi, yi, li in zip(x, y, loud):
            if li * li >= SOFT:
                r = max(1.0, li * 4 * s)
                c.create_oval(xi - r, yi - r, xi + r, yi + r, fill=colour, outline="")
        self.rack_text(c, tr("hz.rack_reverb_notes"))
