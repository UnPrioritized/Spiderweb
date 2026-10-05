"""The Hz bass synth window (Hz bass window → Synth…), like a synth's own window (user): a Knobs tab (boxes of knobs,
like a synth's; so far the Volume envelope: Attack, Decay, Sustain, Release), a Lines tab (the effects' lines big, for
one note), and a piano keyboard along the bottom to hear the Hz bass live (hz_live.py: the quick sound, NOT the MIDI;
the warning stays at the top).

The knobs write the same lines (the Volume knobs: the Volume line, once per note, its sustain point and fall); a line
drawn by hand that they can't have made shows them about where it is, and turning one makes a new line from them.

The lines are the Hz bass window's own (the same pane, hz_effects.FxPane, on this window's timeline): editing them
here is editing them there, one undo step of the main window each. Effects put on here go with each note (user: as in
a synth): once per note, a repeating shape restarting at each note. The timeline is one note of each key pressed,
from beat 0: held for the longest line counted from each note (so its whole shape shows), then the falls after the
sustain points, all fitted to the window. Lines that play all the way from the shape's start are shown from the
note's start too (a key pressed plays them from there). While a key sounds, a dot runs along every line."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.hzbass import ENVELOPES, FAST, FX, FX_START, LOOP, line_at, sound_span
from roll.roll_shared import note_name
from window.hz_effects import AMOUNT, FX_COLOR, FxPane
from window.tool_window import Knob
from window.widgets import Scrub, Tooltip

BLACK = (1, 3, 6, 8, 10)
KEY_HELD = "#7aa7f0"
WARN = "#c06000"
ADSR = ("attack", "decay", "sustain", "release")
PLAIN = {"attack": 0.0, "decay": 0.0, "sustain": 1.0, "release": 0.0}  # (no Volume line: full all along)
TIME_KNOB = 4.0  # beats an Attack / Decay / Release knob goes to (along a curve: fine near 0)
TIME_MOST = 64.0  # ... and a typed one
STEPS = {"time": (0.05, 0.25, 0.01), "sustain": (1, 10, 0.1)}  # the boxes' steps (Shift, Ctrl)


def adsr_line(attack, decay, sustain, release):
    """The Volume line of an ADSR envelope (beats; sustain 0..1): (points, its sustain point, its length). The rise
    comes late and the drops fast first, as the ready-made envelopes do (the top half sounds about the same)."""
    pts = [[0.0, 0.0, -FAST]] if attack > 0 else []
    if decay > 0:
        pts.append([attack, 1.0, FAST])
    at = attack + decay
    pts.append([at, sustain, FAST] if release > 0 else [at, sustain])
    if release > 0:
        pts.append([at + release, 0.0])
    return pts, at, max(LOOP[0], at + release)


def same_line(a, b):
    """Two lines' points the same (to a millionth)."""
    def same(x, y):
        return x == y if isinstance(x, str) or isinstance(y, str) else abs(x - y) < 1e-6
    return len(a) == len(b) and all(len(p) == len(q) and all(map(same, p, q)) for p, q in zip(a, b))


def adsr_of(win):
    """The Volume line as the knobs show it: ({attack, decay, sustain, release}, True) when they could have made it
    (or there's none: full all along), else (about where it is, False)."""
    pts = win.fxl.get("volume")
    if not pts:
        return dict(PLAIN), True
    every, at = win.loops.get("volume"), win.sustains.get("volume")
    if not every or win.froms.get("volume") != "note" or at is None or "volume" in win.fits:
        return dict(PLAIN), False
    top = max((p for p in pts if p[0] <= at + 1e-9), key=lambda p: p[1], default=pts[0])  # (the first highest)
    got = {"attack": max(0.0, top[0]), "decay": max(0.0, at - top[0]), "sustain": float(line_at(pts, at)),
           "release": max(0.0, every - at)}
    if got["release"] <= LOOP[0] + 1e-9 and pts[-1][0] <= at + 1e-9:  # (no fall)
        got["release"] = 0.0
    return got, same_line(adsr_line(**got)[0], pts)


class Dial(Knob):
    """A synth's knob: 0 (pointing down left) to 100 (down right), turning 270 degrees. As Knob otherwise, without
    sticking anywhere; a middle-click puts it back to `start`."""

    TURN = 270

    def __init__(self, parent, scale, changed, color, size=44, start=0.0):
        super().__init__(parent, scale, changed, color=color, size=size)
        self.start = start
        self.bind("<ButtonPress-2>", lambda e: self.turn_to(self.start, True))

    def draw(self):
        self.delete("all")
        s, m = self.size, max(3, self.size // 9)
        w = max(2, m // 2)
        self.create_arc(m, m, s - m, s - m, start=225, extent=-self.TURN, style="arc", width=w,
                        outline="#888" if self.focus_get() is self else "#ccc")
        if self.value:
            self.create_arc(m, m, s - m, s - m, start=225, extent=-self.value / 100 * self.TURN, style="arc",
                            outline=self.color if self.enabled else "#ccc", width=w)
        c, r = s / 2, s / 2 - m * 1.8
        a = math.radians(225 - self.value / 100 * self.TURN)
        self.create_oval(c - r, c - r, c + r, c + r, fill="#555" if self.enabled else "#aaa", outline="")
        self.create_line(c, c, c + r * math.cos(a), c - r * math.sin(a), fill="white", width=2)

    def step(self, d):
        if self.enabled:
            self.turn_to(self.value + d, True)

    def point(self, e):
        if not self.enabled:
            return
        self.focus_set()
        c = self.size / 2
        if (e.x - c) ** 2 + (e.y - c) ** 2 > 4:
            a = (225 - math.degrees(math.atan2(c - e.y, e.x - c))) % 360  # (from the left end, clockwise)
            self.turn_to(a / self.TURN * 100 if a <= self.TURN else 100 if a < (self.TURN + 360) / 2 else 0)

    def press(self, e):
        if self.enabled:
            self.focus_set()
            self.drag = (e.y, self.value)

    def move(self, e):
        if self.drag:
            y, r = self.drag
            r = max(0.0, min(100.0, r + (y - e.y) * (0.1 if e.state & 1 else 0.5)))
            self.drag = (e.y, r)
            self.turn_to(r)

    def turn_to(self, value, done=False):
        super().turn_to(max(0.0, min(100.0, value)), done)


class SynthPane(FxPane):
    """The effects pane on the synth window's one note (see the top)."""

    def __init__(self, win, hz):
        self.hz = hz
        super().__init__(win)
        self.edge = -1  # (no top edge to drag: the pane fills the window)

    def play_x(self):
        return None  # (no song here: no play line)

    def span(self):
        return self.hz.fx.span()  # (a new line goes over all the real notes, as in the Hz bass window)

    def on_wheel(self, e):
        pass  # (always fitted to the window)

    def new_from(self, kind):
        """(user: a synth's lines go with each note) an envelope once per note, a repeating shape restarting at each
        note."""
        return "note" if kind in ENVELOPES else "restart"

    def put(self, name):
        """An effect put on here: once per note, lasting the note shown (user: a synth's lines go with each note)."""
        win = self.win
        before = self.state()
        every = win.note_len()
        win.fxl[name] = [[u * every, v] for u, v in FX_START[name]]
        win.loops[name], win.froms[name] = every, "note"
        self.active = name
        win.commit_fx(before)

    def live_spot(self, name, u, gone):
        """As FxPane's; lines playing all the way from the shape's start: u beats in (while it's in view)."""
        win = self.win
        if win.froms.get(name) or name.endswith(AMOUNT):
            return super().live_spot(name, u, gone)
        x = win.x_of(u)
        if x > self.canvas.winfo_width():
            return None
        v = line_at(win.fxl[name], u, win.loops.get(name))
        return x, self.y_of(float(v))

    def redraw(self):
        super().redraw()
        c, win, s = self.canvas, self.win, self.s
        if c.winfo_width() < 50:
            return
        h, end = c.winfo_height(), win.note_len()  # where the key is held, and let go
        font = ("Segoe UI", 7)
        c.create_text(win.x_of(0.0) + 3 * s, h - 4 * s, text=tr("hz.synth_held"), anchor="sw", fill="#6a86c8",
                      font=font, )
        c.create_text(win.x_of(end) + 3 * s, h - 4 * s, text=tr("hz.synth_let_go"), anchor="sw", fill="#999",
                      font=font)


class SynthWindow(tk.Toplevel):
    """The window (one per Hz bass window: hz.synth_win). The pane's `win`: its lines are the Hz bass window's."""

    def __init__(self, hz):
        super().__init__(hz)
        self.hz, self.app, self.s = hz, hz.app, hz.s
        s = self.s
        self.title(tr("hz.synth_title"))
        names_h = round(8 * s) + len(FX) * round(15 * s)  # (the pane tall enough for all the effects' names)
        self.geometry(f"{round(900 * s)}x{names_h + round(200 * s)}")  # (the knobs need a bit more)
        self.minsize(round(400 * s), round(260 * s))
        self.kb_w = hz.kb_w
        self.sx, self.t0 = 100.0, 0.0
        self.tool, self.pencil, self.live = hz.tool, hz.pencil, hz.live
        self.held = None  # the key held with the mouse
        self.shown = None  # what the pane was drawn for (refresh)
        self.warn = ttk.Label(self, text=tr("hz.synth_warning"), foreground=WARN, font=("Segoe UI", 9, "bold"),
                              padding=(8, 6, 8, 4))
        self.warn.pack(fill="x")
        self.warn.bind("<Configure>", lambda e: self.warn.config(wraplength=max(100, e.width - round(16 * s))))
        bottom = ttk.Frame(self, padding=(8, 2, 8, 4))
        bottom.pack(side="bottom", fill="x")
        self.says = ttk.Label(bottom, text="", foreground="#555")
        self.says.pack(side="right", padx=(10, 0))
        self.status = ttk.Label(bottom, text="", foreground="#555")
        self.status.pack(side="left", fill="x")
        self.piano = tk.Canvas(self, background="#707070", highlightthickness=0, height=round(70 * s))
        self.piano.pack(side="bottom", fill="x")
        tabs = ttk.Frame(self, padding=(8, 0, 8, 4))
        tabs.pack(fill="x")
        self.page = tk.StringVar(value="knobs")
        for key in ("lines", "knobs"):  # (from the right)
            ttk.Radiobutton(tabs, text=tr(f"hz.synth_{key}"), variable=self.page, value=key, style="Toolbutton",
                            command=self.show_page, takefocus=False).pack(side="right")
        self.knobs = ttk.Frame(self, padding=(10, 4, 10, 8))
        self.turning = None  # while a knob is turned: the lines from before (FxPane.state)
        self.pic_for = None  # what the envelope's picture was drawn for
        self.build_knobs(self.knobs)
        self.fx = SynthPane(self, hz)
        self.canvas = self.fx.canvas
        self.canvas.config(takefocus=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        k = self.piano
        k.bind("<Configure>", lambda e: self.draw_keys())
        k.bind("<ButtonPress-1>", self.on_key_press)
        k.bind("<B1-Motion>", self.on_key_drag)
        k.bind("<ButtonRelease-1>", self.on_key_release)
        k.bind("<Motion>", lambda e: self.show_status())
        c = self.canvas
        c.bind("<Delete>", lambda e: (self.fx.delete_key(), "break")[1])
        for key in ("<Control-c>", "<Control-C>"):
            c.bind(key, lambda e: (self.fx.copy_points() and setattr(self.app, "hz_clip", None), "break")[1])
        for key in ("<Control-v>", "<Control-V>"):
            c.bind(key, lambda e: (self.fx.paste_points(), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_page()

    def show_page(self):
        """The Knobs or the Lines tab shown."""
        knobs = self.page.get() == "knobs"
        (self.canvas if knobs else self.knobs).pack_forget()
        (self.knobs if knobs else self.canvas).pack(fill="both", expand=True)
        self.show_status()
        self.show_knobs()

    # ------------------------------------------------------------ the knobs

    def build_knobs(self, page):
        s = self.s
        box = ttk.Labelframe(page, text=tr("hz.synth_volume"), padding=(10, 4, 10, 8))
        box.pack(side="left", anchor="n")
        self.dials, self.dial_vars, self.dial_boxes = {}, {}, {}
        for c, key in enumerate(ADSR):
            cell = ttk.Frame(box)
            cell.grid(row=0, column=c, padx=6)
            ttk.Label(cell, text=tr(f"hz.synth_{key}")).pack()
            k = self.dials[key] = Dial(cell, s, lambda v, done, key=key: self.on_dial(key, v, done),
                                       FX_COLOR["volume"], size=46, start=100.0 if key == "sustain" else 0.0)
            k.pack()
            row = ttk.Frame(cell)
            row.pack(pady=(2, 0))
            var = self.dial_vars[key] = tk.StringVar()
            e = self.dial_boxes[key] = ttk.Entry(row, textvariable=var, width=5, justify="center")
            e.pack(side="left")
            ttk.Label(row, text=tr("hz.synth_percent" if key == "sustain" else "hz.synth_beats"),
                      foreground="#777").pack(side="left", padx=(2, 0))
            e.bind("<Return>", lambda ev, key=key: (self.on_box(key), "break")[1])
            e.bind("<FocusOut>", lambda ev, key=key: self.on_box(key))
            Scrub(self.app, [(e, var, lambda key=key: self.on_box(key))], STEPS["sustain" if key == "sustain" else
                                                                                 "time"],
                  0, 100 if key == "sustain" else TIME_MOST, drag_box=True)
            for w in (k, e):
                Tooltip(w, tr(f"hz.synth_tip_{key}") + "\n" + tr("hz.synth_tip_knob"))
        self.pic = tk.Canvas(box, width=round(260 * s), height=round(90 * s), background="white",
                             highlightthickness=1, highlightbackground="#ccc")
        self.pic.grid(row=1, column=0, columnspan=len(ADSR), sticky="ew", pady=(8, 0))
        self.pic.bind("<Configure>", lambda e: self.draw_adsr())
        self.adsr_says = ttk.Label(box, text="", foreground=WARN, wraplength=round(260 * s))
        self.adsr_says.grid(row=2, column=0, columnspan=len(ADSR), sticky="w", pady=(6, 0))
        self.adsr = dict(PLAIN)
        self.dot_at = None  # where the picture's moving dot is drawn

    def on_dial(self, key, k, done):
        """A knob turned (done: let go / one step of the wheel or the keys = one undo step)."""
        if self.turning is None:
            self.turning = self.fx.state()
        self.adsr[key] = k / 100 if key == "sustain" else round(TIME_KNOB * (k / 100) ** 2, 3)
        self.write_adsr()
        if done:
            before, self.turning = self.turning, None
            if self.fx.now() != before:
                self.commit_fx(before)

    def on_box(self, key):
        """A value typed (or stepped) in the box under a knob."""
        e, var = self.dial_boxes[key], self.dial_vars[key]
        hi = 100 if key == "sustain" else TIME_MOST
        try:
            v = float(calc(var.get()))
            if not 0 <= v <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        v = v / 100 if key == "sustain" else v
        if abs(v - self.adsr[key]) > 1e-9:
            before = self.fx.state()
            self.adsr[key] = v
            self.write_adsr()
            self.commit_fx(before)

    def write_adsr(self):
        """The Volume line made from the knobs: once per note, with its sustain point."""
        pts, at, every = adsr_line(**self.adsr)
        self.fxl["volume"] = pts
        self.loops["volume"], self.froms["volume"], self.sustains["volume"] = every, "note", at
        if "volume" in self.fits:
            self.fits = [n for n in self.fits if n != "volume"]
        self.redraw()
        self.show_knobs()

    def show_knobs(self):
        """The knobs, their boxes and the picture show the Volume line (not while a knob is turned: it shows what's
        turned)."""
        if self.turning is None:
            self.adsr, made = adsr_of(self)
            says = "" if made else tr("hz.synth_drawn")
            if self.adsr_says.cget("text") != says:
                self.adsr_says.config(text=says)
        for key in ADSR:
            v = self.adsr[key]
            k = 100 * v if key == "sustain" else min(100.0, 100 * math.sqrt(v / TIME_KNOB))
            if not self.dials[key].drag and abs(self.dials[key].value - k) > 0.05:
                self.dials[key].set(k)
            text = fmt(100 * v if key == "sustain" else v)
            e = self.dial_boxes[key]
            if self.dial_vars[key].get() != text and self.focus_get() is not e:
                self.dial_vars[key].set(text)
                e.config(style="TEntry")
        self.draw_adsr()

    def adsr_spots(self):
        """The envelope's picture: (its points, sustain point, length, x of a beat, y of a value, the held part's
        width)."""
        pts, at, _ = adsr_line(**self.adsr)
        every = at + self.adsr["release"]
        w, h, pad = self.pic.winfo_width(), self.pic.winfo_height(), 10 * self.s
        held = every / 3 if every > 0 else 1.0  # (drawn a third as long as the rest)
        sx = (w - 2 * pad) / (every + held)

        def x_of(b, after=False):  # (after the sustain point: past the held part)
            return pad + (b + (held if after else 0.0)) * sx

        def y_of(v):
            return h - pad - v * (h - 2.5 * pad)
        return pts, at, every, x_of, y_of, held * sx

    def draw_adsr(self):
        """The envelope's picture: its rise and drop, the part while the key is held (Sustain), the fall after the
        key is let go (Release)."""
        c, s = self.pic, self.s
        if c.winfo_width() < 50 or (tuple(self.adsr.values()), c.winfo_width(), c.winfo_height()) == self.pic_for:
            return
        self.pic_for = (tuple(self.adsr.values()), c.winfo_width(), c.winfo_height())
        c.delete("all")
        pts, at, every, x_of, y_of, held = self.adsr_spots()
        h = c.winfo_height()
        top = float(line_at(pts, at))
        before, after = np.linspace(0.0, at, 40), np.linspace(at, every, 40)
        xy = [(x_of(b), y_of(v)) for b, v in zip(before, line_at(pts, before))]
        xy += [(x_of(at) + held, y_of(top))]
        xy += [(x_of(b, True), y_of(v)) for b, v in zip(after, line_at(pts, after))]
        c.create_rectangle(x_of(at), 0, x_of(at) + held, h, fill="#eef3fc", outline="")
        font = ("Segoe UI", 7)
        for text, x0, x1 in (("A", x_of(0.0), x_of(self.adsr["attack"])),
                             ("D", x_of(self.adsr["attack"]), x_of(at)),
                             ("S", x_of(at), x_of(at) + held), ("R", x_of(at) + held, x_of(every, True))):
            if x1 - x0 >= 8 * s:
                c.create_text((x0 + x1) / 2, 2 * s, text=text, anchor="n", fill="#999", font=font)
        x = x_of(at) + held
        c.create_line(x, 0, x, h, fill="#bbb", dash=(3, 3))
        c.create_text(x + 3 * s, h - 2 * s, text=tr("hz.synth_let_go"), anchor="sw", fill="#999", font=font)
        c.create_line(*[v for p in xy for v in p], fill=FX_COLOR["volume"], width=max(2, round(2 * s)))
        self.dot_at = None

    def draw_adsr_dot(self):
        """While a key sounds: a dot on the picture, waiting at the sustain point while it's held, down the fall
        after it's let go."""
        pos = self.live.position() if self.live.active() and self.page.get() == "knobs" else None
        got = None
        if pos is not None and self.pic.winfo_width() >= 50:
            u, gone = pos
            pts, at, every, x_of, y_of, held = self.adsr_spots()
            b = min(u, at) if gone is None else min(every, at + max(0.0, u - gone))
            got = round(x_of(b, gone is not None)), round(y_of(float(line_at(pts, b))))
        if got == self.dot_at:
            return
        self.dot_at = got
        c, r = self.pic, 4 * self.s
        c.delete("dot")
        if got:
            c.create_oval(got[0] - r, got[1] - r, got[0] + r, got[1] + r, fill=FX_COLOR["volume"], outline="white",
                          width=max(1, round(1.5 * self.s)), tags="dot")

    # ------------------------------------------------------------ the Hz bass window's lines (the pane works on these)

    fxl = property(lambda self: self.hz.fxl, lambda self, v: setattr(self.hz, "fxl", v))
    loops = property(lambda self: self.hz.loops, lambda self, v: setattr(self.hz, "loops", v))
    froms = property(lambda self: self.hz.froms, lambda self, v: setattr(self.hz, "froms", v))
    fits = property(lambda self: self.hz.fits, lambda self, v: setattr(self.hz, "fits", v))
    sustains = property(lambda self: self.hz.sustains, lambda self, v: setattr(self.hz, "sustains", v))
    off = property(lambda self: self.hz.off, lambda self, v: setattr(self.hz, "off", v))

    def snap(self, beat, e):
        return self.hz.snap(beat, e)

    def snap_beats(self):
        return self.hz.snap_beats()

    def commit_fx(self, before):
        self.hz.commit_fx(before)  # (the Hz bass window redraws, and this one with it: refresh)

    # ------------------------------------------------------------ the timeline: one note

    def note_len(self):
        """How long the note shown is held (beats): the longest line counted from each note, past its sustain point
        by at least a quarter of it (so the wait there shows); a beat without any."""
        every = [self.loops[n] for n in self.froms if n in self.loops]
        most = max(every, default=1.0)
        held = [self.sustains[n] + most / 4 for n in self.sustains if n in self.loops]
        return max([most] + held)

    @property
    def tones(self):
        return [{"t": 0.0, "len": self.note_len(), "key": 60, "cents": 0.0, "id": 1, "to": []}]

    def x_of(self, beat):
        return self.kb_w + (beat - self.t0) * self.sx

    def beat_at(self, x):
        return self.t0 + (x - self.kb_w) / self.sx

    def clamp_view(self):
        pass

    def fit(self):
        """The note and its falls fill the pane (not while a point is dragged: it would move under the mouse)."""
        if self.fx.drag:
            return False
        w = self.canvas.winfo_width()
        end = max(sound_span(self.fx.hz_now()), self.note_len()) * 1.08
        self.sx = max(1.0, (w - self.kb_w - 30 * self.s) / end)
        self.t0 = -12 * self.s / self.sx
        return True

    def redraw(self):
        """The pane drawn again (and the Hz bass window's pane: the same lines, e.g. while a point is dragged)."""
        if not self.winfo_exists():
            return
        self.draw_pane()
        if self.hz.fx.canvas.winfo_ismapped():
            self.hz.fx.redraw()

    def draw_pane(self):
        fitted = self.fit()
        self.fx.redraw()
        self.shown = self.drawn_for() if fitted else None  # (not fitted: fitted at the next refresh)

    def drawn_for(self):
        return repr(self.fx.now()), self.canvas.winfo_width(), self.canvas.winfo_height()

    def refresh(self):
        """The Hz bass window drew itself: this pane too when the lines changed there (an edit, undo)."""
        if not self.winfo_exists():
            return
        if self.drawn_for() != self.shown:
            self.draw_pane()
        self.show_knobs()

    def show_status(self, e=None):
        knobs = self.page.get() == "knobs"
        self.status.config(text=tr("hz.synth_hint_knobs") if knobs else self.fx.says or tr("hz.synth_hint"))

    def draw_live(self):
        """(Every tick of the live keys / the preview) the moving dots and the words at the top right."""
        if not self.winfo_exists():
            return
        self.fx.draw_dots()
        self.draw_adsr_dot()
        says, colour = self.live.says() if self.live.active() else (self.live.ready() or "", "#555")
        if (self.says.cget("text"), str(self.says.cget("foreground"))) != (says, colour):
            self.says.config(text=says, foreground=colour)
        if self.held is None and self.live.key is None and self.piano.find_withtag("lit"):
            self.draw_keys()

    # ------------------------------------------------------------ the keyboard

    def key_spots(self):
        """[(key, x0, x1, black)] of every key, white ones first (black ones are drawn over them)."""
        w = self.piano.winfo_width()
        whites = [k for k in range(128) if k % 12 not in BLACK]
        ww = w / len(whites)
        out, x = [], {}
        for i, k in enumerate(whites):
            out.append((k, i * ww, (i + 1) * ww, False))
            x[k] = (i + 1) * ww
        bw = ww * 0.6
        for k in range(128):
            if k % 12 in BLACK:
                out.append((k, x[k - 1] - bw / 2, x[k - 1] + bw / 2, True))
        return out

    def draw_keys(self):
        c, s = self.piano, self.s
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50:
            return
        lit = self.held if self.held is not None else self.live.key
        bh = h * 0.6
        for k, x0, x1, black in self.key_spots():
            on = k == lit
            fill = KEY_HELD if on else "#202020" if black else "white"
            c.create_rectangle(x0, 0, x1, bh if black else h, fill=fill, outline="#707070",
                               tags="lit" if on else "")
            if not black and k % 12 == 0 and x1 - x0 >= 9 * s:
                c.create_text((x0 + x1) / 2, h - 3 * s, text=note_name(k), anchor="s", fill="#777",
                              font=("Segoe UI", 6))

    def key_at(self, x, y):
        bh = self.piano.winfo_height() * 0.6
        keys = self.key_spots()
        for k, x0, x1, black in reversed(keys):  # (black ones first: they're on top)
            if x0 <= x < x1 and (not black or y < bh):
                return k
        return None

    def on_key_press(self, e):
        k = self.key_at(e.x, e.y)
        if k is None:
            return
        why = self.live.press(k, parent=self)
        if why:
            self.status.config(text=why)
            return
        self.held = k
        self.show_status()
        self.draw_keys()

    def on_key_drag(self, e):
        if self.held is None:
            return
        k = self.key_at(min(max(e.x, 0), self.piano.winfo_width() - 1), min(max(e.y, 0), self.piano.winfo_height() - 1))
        if k is not None and k != self.held:  # (onto another key: that one plays)
            self.live.press(k, parent=self)
            self.held = k
            self.draw_keys()

    def on_key_release(self, e):
        if self.held is None:
            return
        self.held = None
        self.live.release()
        self.draw_keys()

    def close(self):
        if self.held is not None:
            self.held = None
            self.live.release()
        if self.fx.asking:  # (the Repeat every… window)
            self.fx.asking.destroy()
        self.hz.synth_win = None
        self.destroy()


def open_synth(hz):
    """Hz bass window → Synth…: the window opened (or brought up); the preview turned on, as the keys need it."""
    if hz.synth_win is not None and hz.synth_win.winfo_exists():
        hz.synth_win.deiconify()
        hz.synth_win.lift()
        return hz.synth_win
    hz.synth_win = SynthWindow(hz)
    if not hz.preview_on.get():
        hz.preview_on.set(True)
        hz.on_preview()
    hz.synth_win.focus_set()
    return hz.synth_win
