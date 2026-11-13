"""The synth window's Knobs tab pictures (window/hz_knobs.py): each box's small picture, drawn again when its values
change (the envelope with a dot while a key sounds, one wave's hits, the pitch, the wobbles, which keys are loud over
time, the keys' notes, the copies' tones and a glide, the arpeggio's run)."""

import math
from tkinter import font as tkfont

import numpy as np

from files import colours
from files.lang import tr, trnum
from files.mathexpr import fmt
from notes.hzbass import (CRUSH, DETUNE, GROWL, HEIGHT_FROM, OFF_PITCH, OSC2, SOFT, SUB, WAH, WAVES, adsr_line,
                          arpeggiated, blend_gains, clean_arp, clean_mode, clean_voice, copies, filter_line,
                          glide_left, line_at, osc2_shift, wave_hits)
from window.hz_effects import FX_COLOR
from window.synth_look import DIM, GRID, MID, PIC, TEXT, bright, mix


class SynthKnobPics:
    """The Knobs tab's pictures (the draw_* methods) for SynthKnobs."""

    def draw_pics(self):
        """Each box's picture drawn again when its values (or size) changed."""
        for box, knobs in BOXES.items():
            c = self.pics[box]
            key = (tuple(self.pv[k] for k, _, _ in knobs),
                   (self.pv["wave"], self.pv["mode"], self.a_silent()) if box == "wave" else None,
                   self.pv["sweep"] if box == "tone" else None,
                   (self.pv["same"], self.pv["touching"]) if box == "voice" else None,
                   (self.pv["arp_on"], *(self.pv[f"arp_{w}"] for w in ARP_CHOICES), str(self.pv["arp_steps"]),
                    self.pv["arp_count"])
                   if box == "arp" else None,
                   tuple(self.pv[k] for k in ("osc2_on", "osc2_wave", "osc2_mode")) if box == "osc2" else None,
                   self.pic_colour(box) if box in BYPASS else None,
                   c.winfo_width(),
                   c.winfo_height())
            if c.winfo_width() < 50 or self.pic_for.get(box) == key:
                continue
            self.pic_for[box] = key
            c.delete("all")
            getattr(self, "draw_" + box)(c)
            if box == "volume":
                self.dot_at = None

    def adsr_spots(self):
        """The envelope's picture: (its points, sustain point, length, x of a beat, y of a value, the held part's
        width)."""
        v = self.pv
        pts, at, _ = adsr_line(v["attack"], v["decay"], v["sustain"], v["release"])
        every = at + v["release"]
        c = self.pics["volume"]
        w, h, pad = c.winfo_width(), c.winfo_height(), 10 * self.s
        held = every / 3 if every > 0 else 1.0  # (drawn a third as long as the rest)
        sx = (w - 2 * pad) / (every + held)

        def x_of(b, after=False):  # (after the sustain point: past the held part)
            return pad + (b + (held if after else 0.0)) * sx

        def y_of(value):
            return h - pad - value * (h - 2.5 * pad)
        return pts, at, every, x_of, y_of, held * sx

    def draw_volume(self, c):
        """The envelope: its rise and drop, the part while the key is held (Sustain), the fall after the key is let
        go (Release)."""
        s = self.s
        pts, at, every, x_of, y_of, held = self.adsr_spots()
        h = c.winfo_height()
        top = float(line_at(pts, at))
        before, after = np.linspace(0.0, at, 40), np.linspace(at, every, 40)
        xy = [(x_of(b), y_of(v)) for b, v in zip(before, line_at(pts, before))]
        xy += [(x_of(at) + held, y_of(top))]
        xy += [(x_of(b, True), y_of(v)) for b, v in zip(after, line_at(pts, after))]
        colour = self.pic_colour("volume")
        c.create_rectangle(x_of(at), 0, x_of(at) + held, h, fill=mix(colour, PIC, 0.88), outline="")
        font = ("Segoe UI", 7)
        for text, x0, x1 in (("A", x_of(0.0), x_of(self.pv["attack"])), ("D", x_of(self.pv["attack"]), x_of(at)),
                             ("S", x_of(at), x_of(at) + held), ("R", x_of(at) + held, x_of(every, True))):
            if x1 - x0 >= 8 * s:
                c.create_text((x0 + x1) / 2, 2 * s, text=text, anchor="n", fill=DIM, font=font)
        x = x_of(at) + held
        c.create_line(x, 0, x, h, fill=MID, dash=(3, 3))
        text = tr("hz.synth_let_go")  # (left of its line when there's no room right of it)
        right = x + 3 * s + tkfont.Font(font=font).measure(text) <= c.winfo_width()
        c.create_text(x + 3 * s if right else x - 3 * s, h - 2 * s, text=text, anchor="sw" if right else "se", fill=DIM,
                      font=font)
        c.create_line(*[v for p in xy for v in p], fill=colour, width=max(2, round(2 * s)))

    def wave_hits(self, osc=""):
        """Two waves' hits at a note's start as the notes come out (hzbass.wave_hits, as KeyGrid makes them; Growl
        and Bitcrush for an A1): [(place 0..2, how hard 0..1)], the soft ones left out. osc = "osc2_": OSC B's."""
        v = {k[len(osc):]: x for k, x in self.pv.items() if k.startswith(osc)}
        mode = clean_mode({"kind": v["mode"], **{key.split("_", 1)[1]: v[key] for key, _ in MODE_KNOBS.get(v["mode"], ())}})
        number, since = np.arange(2), np.zeros(2)
        wave = v["wave"] != "none"
        shapes = [(WAVES[v["wave"]], np.full(2, v["shape"]), np.ones(2, bool))] if wave else []
        where, mix = wave_hits(shapes, np.full(2, not wave), number, since, mode)
        if not osc:  # (Octave below: every other wave softer)
            mix = mix * np.where(number % 2 == 1, 1.0 - v["octave"], 1.0)[:, None]
        at = number[:, None] + where
        if mode.get("kind") == "growl":
            at = at + GROWL * mode["amount"] * (number % mode["every"] / (mode["every"] - 1))[:, None]
        if mode.get("kind") == "crush" and mode["amount"] > 0:  # (an A1's wave at the Hz bass's BPM)
            bpm = float(((self.hz.target() or {}).get("hz") or {}).get("bpm", 120.0))
            grid = mode["amount"] ** 2 * CRUSH * 55.0 * 60.0 / bpm
            at = np.floor(at / grid + 0.5) * grid
        keep = (mix >= SOFT) & (at < 2.0)
        return [(float(a), float(x)) for a, x in zip(at[keep], mix[keep])]

    def draw_osc2(self, c):
        """OSC B's two waves (as OSC A's), how far it's tuned from the note, or that it's off."""
        v = self.pv
        self.draw_wave(c, "osc2_")
        keys = osc2_shift({k: v[f"osc2_{k}"] for k in OSC2})
        font = ("Segoe UI", 7)
        if not v["osc2_on"]:
            c.create_text(c.winfo_width() / 2, c.winfo_height() / 2, text=tr("hz.synth_osc2_off"), fill=TEXT,
                          font=font)
        elif abs(keys) > 1e-9:
            c.create_text(3 * self.s, 2 * self.s, text=trnum("hz.synth_osc2_tune", "keys", ("+" if keys > 0 else "")
                                                            + fmt(round(keys, 2))), anchor="nw", fill=DIM, font=font)

    def draw_wave(self, c, osc=""):
        """Two waves' notes, one bar each, as tall as it hits (and how many notes a wave takes); osc = "osc2_": OSC
        B's (grey while it's off)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 8 * s
        hits = self.wave_hits(osc)
        bw = (w - 2 * pad) / (2 * SUB)
        wave = self.pv[osc + "wave"]
        colour = bright(FX_COLOR[wave]) if wave in FX_COLOR else DIM
        if (self.box_off("wave") or self.a_silent()) if not osc else not self.pv["osc2_on"]:
            colour = MID
        c.create_line(pad + (w - 2 * pad) / 2, pad, pad + (w - 2 * pad) / 2, h - pad, fill=MID, dash=(3, 3))
        for p, x in hits:
            x0 = pad + p * SUB * bw
            c.create_rectangle(x0 + bw * 0.15, h - pad - x * (h - 3 * pad), x0 + bw * 0.85, h - pad, fill=colour,
                               outline="")
        c.create_line(pad, h - pad, w - pad, h - pad, fill=MID)
        per = len([1 for p, _ in hits if p < 1]), len([1 for p, _ in hits if p >= 1])
        n = sum(per) / 2
        text = tr("hz.synth_wave_note" if n == 1 else "hz.synth_wave_notes", n=fmt(n))
        c.create_text(w - 3 * s, 2 * s, text=text, anchor="ne", fill=DIM, font=("Segoe UI", 7))

    def draw_pitch(self, c):
        """The pitch over the start of a note: from Amount keys off to the tone (the middle line)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 10 * self.s
        v = self.pv
        mid = h / 2
        c.create_line(pad, mid, w - pad, mid, fill=MID)
        font = ("Segoe UI", 7)
        c.create_text(w - 3 * s, mid - 2 * s, text=tr("hz.synth_pitch_tone"), anchor="se", fill=DIM, font=font)
        keys = v["bend_range"]
        for k, y in ((keys, pad), (-keys, h - pad)):
            c.create_text(3 * s, y, text=f"{k:+.0f}", anchor="w", fill=DIM, font=font)
        time = max(v["time"], 1e-9)
        total = time * 1.4  # (a bit of the tone after it)
        pts, _ = pitch_line(v["amount"], v["time"], keys) if v["amount"] else ([[0.0, 0.5]], 0)
        b = np.linspace(0.0, total, 60)
        ys = line_at(pts, b)
        x0 = pad + 14 * s
        xy = [(x0 + u / total * (w - x0 - pad), mid - (y - 0.5) * 2 * (mid - pad)) for u, y in zip(b, ys)]
        c.create_line(*[q for p in xy for q in p], fill=self.pic_colour("pitch"), width=max(2, round(2 * s)))

    def wobble(self, c, values, colour, mid):
        """A picture's line over two beats: values at evenly spread spots (mid: -1..1 around the middle, else 0..1
        of the height)."""
        s = self.s
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        n = len(values)
        top, bottom = pad, h - pad
        if mid:
            y0 = (top + bottom) / 2
            c.create_line(pad, y0, w - pad, y0, fill=MID)
            ys = y0 - values * (y0 - top)
        else:
            ys = bottom - values * (bottom - top)
        c.create_line(w / 2, top, w / 2, bottom, fill=GRID, dash=(3, 3))  # (one beat in)
        xy = [(pad + i / (n - 1) * (w - 2 * pad), y) for i, y in enumerate(ys)]
        c.create_line(*[q for p in xy for q in p], fill=colour, width=max(2, round(2 * s)))

    def draw_vibrato(self, c):
        """The tone going up and down over two beats from a note's start (none for Delay, then coming in over
        Rise)."""
        v = self.pv
        b = np.linspace(0.0, 2.0, 400)
        come = coming_in(b, v["vibrato_wait"], v["vibrato_delay"])
        self.wobble(c, v["vibrato_depth"] * come * np.sin(2 * np.pi * v["vibrato_rate"] * b),
                    self.pic_colour("vibrato"), True)

    def draw_tremolo(self, c):
        """The loudness over two beats: down to 1 - Depth, Rate times a beat (none for Delay, then coming in over
        Rise)."""
        v = self.pv
        b = np.linspace(0.0, 2.0, 400)
        d = (v["tremolo_depth"] if v["tremolo_rate"] > 0 else 0.0) * coming_in(b, v["tremolo_wait"], v["tremolo_rise"])
        self.wobble(c, (1 - d) + d * (1 + np.cos(2 * np.pi * v["tremolo_rate"] * b)) / 2, self.pic_colour("tremolo"),
                    False)

    def draw_tone(self, c):
        """Which of the shape's keys are loud (the brighter, the louder; the highest keys at the top) over a note's
        start: the Sweep moving from Start to End (dashed line: Time), Wah's stripes."""
        v = self.pv
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * self.s
        pts, every = sweep_line(v["sweep_start"], v["sweep_end"], v["sweep_time"])
        total = max(1.0, 1.5 * v["sweep_time"]) if every else 1.0
        cols, rows = 48, 24
        b = (np.arange(cols) + 0.5) / cols * total
        x = (np.arange(rows) + 0.5) / rows
        loud = np.ones((rows, cols))
        if v["sweep"]:
            where = line_at(pts, b)
            loud = 0.08 + 0.92 * np.clip(np.cos(np.pi * (x[:, None] - where[None, :])), 0.0, 1.0) ** 4
        loud = loud * ((1.0 + np.cos(2.0 * np.pi * WAH * v["wah"] * (x - 0.5))) / 2.0)[:, None]
        rgb, dark = colours.rgb(self.pic_colour("tone")), colours.rgb(PIC)
        cw, rh = (w - 2 * pad) / cols, (h - 2 * pad) / rows
        for r in range(rows):
            y = h - pad - (r + 1) * rh
            for i in range(cols):
                f = loud[r, i]
                colour = "#%02x%02x%02x" % tuple(round(d + (q - d) * f * 0.7) for q, d in zip(rgb, dark))
                c.create_rectangle(pad + i * cw, y, pad + (i + 1) * cw + 1, y + rh + 1, fill=colour, outline="")
        if v["sweep"] and every:
            xt = pad + v["sweep_time"] / total * (w - 2 * pad)
            c.create_line(xt, pad, xt, h - pad, fill=DIM, dash=(3, 3))

    def draw_filter(self, c):
        """The Cutoff over a note (bottom = closed, top = open; dashed: the middle, where players start), the held
        part as long as the envelope's rise and fall, then its release."""
        v = self.pv
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * self.s
        pts, at, every = filter_line(**filter_vals(v))
        held =max(0.5, (at or 0.0) * 0.5)
        total = (at or 0.0) + held + (every - at if every else 0.0)
        total = max(total, 1.0)
        b = np.linspace(0.0, total, 200)
        x = np.where(b < (at or 0.0) + held, np.minimum(b, at or 0.0), b - held)  # (the line's own beats)
        y = line_at(pts, x)
        if every is not None:
            y = np.where(b < (at or 0.0) + held, y, line_at(pts, np.minimum(x, every)))
        ym = h - pad - 0.5 * (h - 2 * pad)
        c.create_line(pad, ym, w - pad, ym, fill=GRID, dash=(3, 3))
        xs = pad + b / total * (w - 2 * pad)
        ys = h - pad - y * (h - 2 * pad)
        c.create_line(*np.column_stack([xs, ys]).ravel().tolist(), fill=self.pic_colour("filter"), width=2 * self.s)

    def draw_character(self, c):
        """Eight of the shape's keys (the highest at the top) and their notes over four waves: when each key hits
        (Slant, Groups, Noisy late; Off pitch drifting)."""
        v = self.pv
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * self.s
        keys, waves = 8, 4
        sx, rh = (w - 2 * pad) / waves, (h - 2 * pad) / keys
        colour = self.pic_colour("character")
        groups = int(v["groups"])
        at, tall = HEIGHT_FROM[v["height_from"]], max(v["height"], 1.0 / keys)
        for k in range(1, waves):
            c.create_line(pad + k * sx, pad, pad + k * sx, h - pad, fill=GRID, dash=(3, 3))
        for i in range(keys):
            x = i / keys
            if not 0.0 <= at + ((i + 0.5) / keys - at) / tall <= 1.0:  # (a key the Height doesn't reach: quiet)
                continue
            noise = np.random.default_rng(1000 + i)
            stretch = 1.0 + OFF_PITCH * v["offpitch"] * (x - 0.5)
            y = h - pad - (i + 1) * rh
            for n in range(-1, waves + 1):
                late = v["slant"] * x + math.floor(x * groups) / groups + v["noisy"] * noise.random()
                a, b = (n + late) * stretch, (n + 1 + late) * stretch - 0.12
                a, b = max(0.0, a), min(float(waves), b)
                if b > a:
                    c.create_rectangle(pad + a * sx, y + rh * 0.15, pad + b * sx, y + rh * 0.85,
                                       fill=colour, outline="")

    def draw_voice(self, c):
        """Left: eight of the shape's keys (the highest at the top) and the copies they play, each copy at its tone
        (the middle line = the note's own); right: a note gliding in from a lower one before it (Glide)."""
        s, v = self.s, self.pv
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        colour, font = self.pic_colour("voice"), ("Segoe UI", 7)
        split = w * 0.45
        n = int(v["voices"])
        cents = copies({"voice": clean_voice({"voices": n, "detune": v["detune"]})})
        gains = blend_gains({"voice": clean_voice({"voices": n, "detune": v["detune"], "blend": v["blend"]})})
        keys, rh = 8, (h - 2 * pad) / 8

        def x_of(c_):  # (a copy's tone: the note's own in the middle, half the most Detune each way)
            return pad + (split - 2 * pad) * (0.5 + c_ / DETUNE)
        c.create_line(x_of(0.0), pad, x_of(0.0), h - pad, fill=GRID, dash=(3, 3))
        for c_ in cents:
            c.create_line(x_of(c_), pad, x_of(c_), h - pad, fill=mix(colour, PIC, 0.7))
        r = max(2.0, min(rh * 0.35, 4 * s))
        for i in range(keys):
            y = h - pad - (i + 0.5) * rh
            for j in (range(len(cents)) if v["same"] else [i % len(cents)]):  # (fainter = quieter: Blend)
                c_ = cents[j]
                c.create_rectangle(x_of(c_) - r, y - r, x_of(c_) + r, y + r, outline="",
                                   fill=mix(colour, PIC, 0.85 * (1.0 - gains[j])))
        says = (tr("hz.synth_voice_notes", n=n) if v["same"] else tr("hz.synth_voice_no_extra")) if n > 1 else ""
        c.create_text(split - 3 * s, 1 * s, text=says, anchor="ne", fill=DIM, font=font)
        c.create_line(split, pad, split, h - pad, fill=MID)
        # the glide: a note a beat long, then one 5 keys up, touching
        x0, x1 = split + pad, w - pad
        lo, hi = h - pad - (h - 2 * pad) * 0.2, pad + (h - 2 * pad) * 0.2
        mid = (x0 + x1) / 2
        for a, b, y in ((x0, mid, lo), (mid, x1, hi)):
            c.create_rectangle(a + 1, y - 2 * s, b - 1, y + 2 * s, fill="#2f353e", outline="")
        g = min(v["glide"], 1.0)
        u = np.linspace(0.0, 1.0, 40)
        xy = [(x0, lo), (mid, lo)] + [(mid + q * g * (x1 - mid), hi + (lo - hi) * glide_left(q, v["curve"]))
                                      for q in u]
        xy.append((x1, hi))
        c.create_line(*[q for p in xy for q in p], fill=colour, width=max(2, round(2 * s)))
        c.create_text(x1, 1 * s, text=tr("hz.synth_glide"), anchor="ne", fill=DIM, font=font)

    def draw_arp(self, c):
        """Two beats of what it plays (a small piano roll): for a chord of A1, C2 and E2 placed together, or (with a
        chord shape) one A1; greyed while it's off."""
        s, v = self.s, self.pv
        w, h, pad = c.winfo_width(), c.winfo_height(), 6 * s
        arp = clean_arp({k: v[f"arp_{k}"] for k in ARP_KEYS})
        keys = (33, 36, 40) if arp["chord"] == "placed" else (33,)
        tones = [{"t": 0.0, "len": 2.0, "key": k, "cents": 0.0, "id": i + 1, "to": []} for i, k in enumerate(keys)]
        got = arpeggiated(tones, arp)  # (off: the same run, greyed)
        lo, hi = min((n["key"] for n in got), default=33), max((n["key"] for n in got), default=33)  # (every step a
        # rest: an empty picture, hunt)
        top = 12 * s  # (room for the words at the top)
        rh = (h - pad - top) / max(6, hi - lo + 1)
        colour = COLOURS["arp"] if v["arp_on"] else MID
        c.create_line(w / 2, pad, w / 2, h - pad, fill=GRID, dash=(3, 3))  # (one beat in)
        for n in got:
            x0 = pad + n["t"] / 2 * (w - 2 * pad)
            x1 = max(x0 + 2, pad + (n["t"] + n["len"]) / 2 * (w - 2 * pad) - 1)
            y = h - pad - (n["key"] - lo + 1) * rh
            c.create_rectangle(x0, y + 1, x1, y + rh - 1, fill=mix(colour, PIC, 0.8 * (1 - n.get("level", 1.0))),
                               outline="")  # (a quiet step fainter)
        c.create_text(w - 3 * s, 2 * s, text=trnum("hz.synth_arp_notes", "n", fmt(arp["speed"])), anchor="ne",
                      fill=DIM, font=("Segoe UI", 7))

    def draw_adsr_dot(self):
        """While a key sounds: a dot on the envelope's picture, waiting at the sustain point while it's held, down
        the fall after it's let go."""
        pos = self.live.position() if self.live.active() and self.page.get() == "knobs" else None
        c = self.pics["volume"]
        got = None
        if pos is not None and c.winfo_width() >= 50:
            u, gone = pos
            pts, at, every, x_of, y_of, held = self.adsr_spots()
            b = min(u, at) if gone is None else min(every, at + max(0.0, u - gone))
            got = round(x_of(b, gone is not None)), round(y_of(float(line_at(pts, b))))
        if got == self.dot_at:
            return
        self.dot_at = got
        r = 4 * self.s
        c.delete("dot")
        if got:
            c.create_oval(got[0] - r, got[1] - r, got[0] + r, got[1] + r, fill=COLOURS["volume"], outline=TEXT,
                          width=max(1, round(1.5 * self.s)), tags="dot")


# (after the class: hz_knobs imports this module for the mixin, this one needs hz_knobs' names only when a picture is
# drawn, so either may be imported first)
from window.hz_knobs import (ARP_CHOICES, ARP_KEYS, BOXES, BYPASS, COLOURS, MODE_KNOBS, coming_in, filter_vals,  # noqa: E402
                             pitch_line, sweep_line)
