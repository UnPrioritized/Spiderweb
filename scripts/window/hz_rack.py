"""The synth window's Effects tab (user 2026-10-05: a rack, as many synths have): "+ Add effect" puts a strip in
(Chorus, Echo, Reverb look-alike; each once, in the order added); each strip has its knobs, a picture, On (switched
off = kept but silent) and ✕ to take it out. Only the effects added are shown. They're the Hz bass's own setting
hz["rack"] (hzbass.RACK), not lines; one undo step a change. The knobs are the Knobs tab's (hz_knobs.RACK_KNOBS)."""

import numpy as np
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.hzbass import RACK, SOFT
from window.hz_knobs import RACK_KNOBS
from window.widgets import Tooltip

COLOURS = {"chorus": "#2a9d8f", "echo": "#d07a1e", "reverb": "#6a5acd"}
PICTURE = (240, 64)


class SynthRack:
    """The Effects tab of SynthWindow (needs SynthKnobs: dial_cell, vals, write, fx, commit_fx, extra)."""

    def build_rack(self, page):
        s = self.s
        top = ttk.Frame(page)
        top.pack(fill="x", pady=(0, 8))
        mb = ttk.Menubutton(top, text=tr("hz.rack_add"))
        mb.pack(side="left")
        self.rack_menu = tk.Menu(mb, tearoff=0, postcommand=self.fill_rack_menu)
        mb["menu"] = self.rack_menu
        Tooltip(mb, tr("hz.rack_add_tip"))
        self.rack_hint = ttk.Label(page, text=tr("hz.rack_empty"), foreground="#777", wraplength=round(500 * s))
        self.strips, self.strip_on, self.rack_pics, self.rack_pic_for, self.rack_laid = {}, {}, {}, {}, None
        for kind, knobs in RACK_KNOBS.items():
            strip = self.strips[kind] = ttk.Labelframe(page, text=tr(f"hz.rack_{kind}"), padding=(10, 4, 10, 8))
            col = 0
            for key, k in knobs:
                self.dial_cell(strip, col, key, k, RACK[kind][key.split("_", 1)[1]][2], COLOURS[kind])
                col += 1
            cell = ttk.Frame(strip)
            cell.grid(row=0, column=col, padx=(6, 12), sticky="n")
            var = self.strip_on[kind] = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(cell, text=tr("hz.rack_on"), variable=var, takefocus=False,
                                 command=lambda kind=kind: self.rack_switch(kind))
            cb.pack(anchor="w", pady=(14, 4))
            Tooltip(cb, tr("hz.rack_on_tip"))
            b = ttk.Button(cell, text="✕", width=3, takefocus=False, command=lambda kind=kind: self.rack_remove(kind))
            b.pack(anchor="w")
            Tooltip(b, tr("hz.rack_remove_tip"))
            pic = self.rack_pics[kind] = tk.Canvas(strip, width=round(PICTURE[0] * s), height=round(PICTURE[1] * s),
                                                   background="white", highlightthickness=1, highlightbackground="#ccc")
            pic.grid(row=0, column=col + 1, sticky="n", pady=(4, 0))
            pic.bind("<Configure>", lambda e: self.draw_rack_pics())

    def fill_rack_menu(self):
        m = self.rack_menu
        m.delete(0, "end")
        for kind in RACK:
            m.add_command(label=tr(f"hz.rack_{kind}"), command=lambda kind=kind: self.rack_add(kind),
                          state="disabled" if kind in self.vals["rack"] else "normal")

    # ------------------------------------------------------------ changes (one undo step each)

    def rack_edit(self, order, off):
        before = self.fx.state()
        self.vals["rack"], self.vals["rack_off"] = tuple(order), tuple(off)
        self.write_rack()
        self.redraw()
        self.show_knobs()
        if self.fx.now() != before:
            self.commit_fx(before)

    def rack_add(self, kind):
        if kind not in self.vals["rack"]:
            self.rack_edit(self.vals["rack"] + (kind,), [k for k in self.vals["rack_off"] if k != kind])

    def rack_remove(self, kind):
        self.rack_edit([k for k in self.vals["rack"] if k != kind], [k for k in self.vals["rack_off"] if k != kind])

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
        """The strips of the effects added, in order (laid again only when that changes), their On boxes and
        pictures."""
        order = self.vals["rack"]
        if order != self.rack_laid:
            self.rack_laid = order
            for strip in self.strips.values():
                strip.pack_forget()
            self.rack_hint.pack_forget()
            for kind in order:
                self.strips[kind].pack(fill="x", anchor="w", pady=(0, 8))
            if not order:
                self.rack_hint.pack(anchor="w")
        for kind, var in self.strip_on.items():
            on = kind not in self.vals["rack_off"]
            if var.get() != on:
                var.set(on)
        self.draw_rack_pics()

    def draw_rack_pics(self):
        for kind, c in self.rack_pics.items():
            key = (tuple(self.vals[k] for k, _ in RACK_KNOBS[kind]), kind in self.vals["rack_off"], c.winfo_width(),
                   c.winfo_height())
            if c.winfo_width() < 50 or self.rack_pic_for.get(kind) == key:
                continue
            self.rack_pic_for[kind] = key
            c.delete("all")
            getattr(self, "draw_rack_" + kind)(c, COLOURS[kind] if kind not in self.vals["rack_off"] else "#bbb")

    def rack_text(self, c, text):
        c.create_text(c.winfo_width() - 3 * self.s, 2 * self.s, text=text, anchor="ne", fill="#777",
                      font=("Segoe UI", 7))

    def draw_rack_chorus(self, c, colour):
        """Every other key's tone over two beats: up to Depth cents and back."""
        v = self.vals
        b = np.linspace(0.0, 2.0, 400)
        self.wobble(c, v["chorus_depth"] / 100.0 * (1.0 - np.cos(2.0 * np.pi * v["chorus_rate"] * b)) / 2.0, colour,
                    False)
        self.rack_text(c, tr("hz.rack_no_extra"))

    def draw_rack_echo(self, c, colour):
        """The sound and its repeats: a bar for each, as loud as it is, Time apart."""
        s, v = self.s, self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        n = int(v["echo_repeats"])
        heard = [i for i in range(n + 1) if v["echo_fade"] ** i >= SOFT]
        span = (n + 1) * v["echo_time"]
        bw = max(3.0, (w - 2 * pad) / (n + 1) * 0.6)
        for i in heard:
            x = pad + i * v["echo_time"] / span * (w - 2 * pad)
            top = h - pad - v["echo_fade"] ** i * (h - 3 * pad)
            c.create_rectangle(x, top, x + bw, h - pad, fill=colour if i else "#888", outline="")
        c.create_line(pad, h - pad, w - pad, h - pad, fill="#bbb")
        self.rack_text(c, tr("hz.rack_echo_notes", n=len(heard) - 1))

    def draw_rack_reverb(self, c, colour):
        """A note, then its tone ringing on: fading, its waves more and more scattered."""
        s, v = self.s, self.vals
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        note = (w - 2 * pad) * 0.25
        c.create_rectangle(pad, h / 2 - 3 * s, pad + note, h / 2 + 3 * s, fill="#888", outline="")
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
