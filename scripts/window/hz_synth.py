"""The Hz bass synth window (Hz bass window → Synth…), like a synth's own window (user): a Knobs tab (boxes of knobs,
like a synth's, writing the effects' lines: hz_knobs.py), an Effects tab (a rack: chorus, echo, reverb look-alike:
hz_rack.py), a MOD tab (more LFOs and envelopes moving knobs over time: hz_mod.py), a Lines tab (the effects' lines
big, for one note), and a
piano keyboard along the bottom to hear the Hz bass live (hz_live.py: the quick sound, NOT the MIDI; the warning stays
at the top).

The lines are the Hz bass window's own (the same pane, hz_effects.FxPane, on this window's timeline): editing them
here is editing them there, one undo step of the main window each. Effects put on here go with each note (user: as in
a synth): once per note, a repeating shape restarting at each note. The timeline is one note of each key pressed,
from beat 0: held for the longest line counted from each note (so its whole shape shows), then the falls after the
sustain points, all fitted to the window. Lines that play all the way from the shape's start are shown from the
note's start too (a key pressed plays them from there). While a key sounds, a dot runs along every line."""

import json
import math
import sys
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk

import numpy as np

from files.lang import tr
from notes.engine import shape_notes_tracks
from notes.hzbass import ENVELOPES, FX, FX_START, line_at, sound_span
from roll.roll_shared import note_name
from window import look
from window.hz_effects import AMOUNT, FxPane
from window.hz_knobs import SynthKnobs
from window.hz_live import free_sound_later, keep_sound
from notes.hzbass import MACROS
from window.hz_macros import SynthMacros
from window.hz_mod import SynthMod
from window.hz_presets import PresetBar
from window.hz_rack import SynthRack
from window.synth_look import (BG, DIM, EDGE, GRID, MID, PANEL, PIC, TEXT, WARN, BigTab, bright, dark_title, mix,
                               styles)
from window.tool_window import Knob
from window.widgets import Tooltip, remember_place

BLACK = (1, 3, 6, 8, 10)
KEY_HELD = "#7aa7f0"
KEY_WHITE, KEY_BLACK = "#e8eaee", "#101215"
OUT_WHITE, OUT_BLACK = "#9a9fa6", "#30343a"  # keys the letters don't reach now: greyed (user)
METER_KEY, METER_BEATS = 33, 4.0  # the note meter: notes a second while an A1 is held (the first 4 beats)
METER_MS = 250  # ... counted this long after the sound last changed
METER_TOP = 6  # ... its bar goes to 10^6 notes a second (along a log scale)
METER_POLL_MS, METER_SLOW = 50, 3  # ... the count (in the background) looked for this often; "…" after this many looks
METER_JOBS = ThreadPoolExecutor(max_workers=1)


def meter_count(sh, ppq, wanted):
    """(In the background) the notes the meter's shape starts in its first METER_BEATS (0 when no longer wanted)."""
    if not wanted():
        return 0
    notes, _ = shape_notes_tracks(sh, ppq, 128)
    starts = np.asarray(notes)[:, 0] if len(notes) else np.zeros(0)
    return int(np.count_nonzero(starts < METER_BEATS * ppq))
LOOK = {"bg": PIC, "grid": GRID, "outside": "#1b1f24", "notes": "#34507e", "hint": DIM, "repeat": MID,
        "names": "#1f2328", "names_dim": 0.6, "text": DIM, "edge": EDGE, "box": DIM, "point": PIC}
# the computer keyboard's letters playing the keys from C up (like many music programs: the middle row = white keys,
# the row above = black ones); Z / X = an octave down / up
LETTERS = ("a", "w", "s", "e", "d", "f", "t", "g", "y", "h", "u", "j", "k", "o", "l", "p", "semicolon")
NO_LETTERS = 0x4 | (0x20000 if sys.platform == "win32" else 0x8)  # (Ctrl or Alt held: a shortcut, not a key)
WIDE, TALL = 1200, 820  # the window's biggest size when it opens (about a big synth's own; user: it grew past the screen)
LETTER_CHECK_MS = 100  # while a letter is held: how often we ask if it's still down (its let-go can get lost)


def key_down(code):
    """Whether the key with this key code (a key event's keycode) is still held down, asked of the keyboard itself:
    True / False, or None when we can't tell (not Windows). Its let-go never reaches us while the window is being
    moved (Windows keeps it)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(int(code)) & 0x8000)
    except (AttributeError, OSError, ValueError):
        return None


class SynthPane(FxPane):
    """The effects pane on the synth window's one note (see the top), in the dark look."""

    LOOK = LOOK

    def __init__(self, win, hz):
        self.hz = hz
        super().__init__(win)
        self.edge = -1  # (no top edge to drag: the pane fills the window)

    def colour(self, name):
        return bright(super().colour(name))

    def faint(self, colour, by=0.6):
        return mix(colour, PIC, by)

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
        c.create_text(win.x_of(0.0) + 3 * s, h - 4 * s, text=tr("hz.synth_held"), anchor="sw", fill="#7f9ee0",
                      font=font)
        c.create_text(win.x_of(end) + 3 * s, h - 4 * s, text=tr("hz.synth_let_go"), anchor="sw", fill=DIM,
                      font=font)


class SynthWindow(PresetBar, SynthMod, SynthMacros, SynthRack, SynthKnobs, tk.Toplevel):
    """The window (one per Hz bass window: hz.synth_win). The pane's `win`: its lines are the Hz bass window's."""

    def __init__(self, hz):
        super().__init__(hz)
        self.hz, self.app, self.s = hz, hz.app, hz.s
        s = self.s
        self.title(tr("hz.synth_title"))
        names_h = round(8 * s) + len(FX) * round(15 * s)  # (the pane tall enough for all the effects' names)
        self.minsize(round(400 * s), round(260 * s))
        self.kb_w = hz.kb_w
        self.sx, self.t0 = 100.0, 0.0
        self.tool, self.pencil, self.live = hz.tool, hz.pencil, hz.live
        self.holding = {}  # the keys held: "mouse" or a letter (keysym) -> its key (several together, as a synth)
        self.codes = {}  # ... a letter's key code (letter_check)
        self.letter_up = {}  # a letter let go: its key stops a moment later unless it's pressed again (repeating)
        self.kb_base = getattr(self.app, "hz_kb_base", 24)  # the key the letter A plays (C1; Hz bass is low)
        self.shown = None  # what the pane was drawn for (refresh)
        self.meter_job, self.meter_for = None, None  # the note meter: waiting to be counted, counted for
        self.meter_asked, self.meter_poll = None, None  # ... being counted for (in the background), looking for it
        styles(self)
        self.configure(background=BG)
        top = tk.Frame(self, background=BG)  # the big tabs, the preset bar, the note meter
        top.pack(fill="x", padx=round(8 * s), pady=(round(8 * s), 0))
        self.page = tk.StringVar(value="knobs")
        self.tab_buttons = {}
        for key in ("knobs", "effects", "mod", "lines"):
            b = self.tab_buttons[key] = BigTab(top, s, tr(f"hz.synth_{key}"), lambda key=key: self.pick_page(key))
            b.pack(side="left", fill="y")
            Tooltip(b, tr(f"hz.synth_tab_{key}_tip"))
        self.meter = tk.Canvas(top, width=round(180 * s), height=round(50 * s), background=BG, highlightthickness=0)
        self.meter.pack(side="right")
        self.meter.bind("<Configure>", lambda e: self.draw_meter())
        Tooltip(self.meter, tr("hz.synth_meter_tip", key=note_name(METER_KEY)))
        bar = ttk.Frame(top, style="Synth.TFrame")
        bar.pack(side="left", fill="x", expand=True, padx=(round(12 * s), round(12 * s)))
        self.build_presets(bar)
        self.warn = ttk.Label(self, text=tr("hz.synth_warning"), style="Synth.Warn.TLabel", font=("Segoe UI", 8),
                              padding=(10, 4, 10, 2))
        self.warn.pack(fill="x")
        self.warn.bind("<Configure>", lambda e: self.warn.config(wraplength=max(100, e.width - round(16 * s))))
        bottom = ttk.Frame(self, padding=(8, 2, 8, 4), style="Synth.TFrame")
        bottom.pack(side="bottom", fill="x")
        self.says = ttk.Label(bottom, text="", style="Synth.Dim.TLabel")
        self.says.pack(side="right", padx=(10, 0))
        self.status = ttk.Label(bottom, text="", style="Synth.Dim.TLabel")
        self.status.pack(side="left", fill="x")
        self.piano = tk.Canvas(self, background=BG, highlightthickness=0, height=round(70 * s))
        self.piano.pack(side="bottom", fill="x", padx=round(8 * s), pady=(round(4 * s), 0))
        self.knobs_box = ttk.Frame(self, style="Synth.TFrame")  # (the Knobs tab: scrolls when the boxes don't fit,
        kc = self.knobs_canvas = tk.Canvas(self.knobs_box, highlightthickness=0, bd=0, bg=BG)  # e.g. a small screen)
        self.knobs_bar = ttk.Scrollbar(self.knobs_box, orient="vertical", command=kc.yview,
                                       style="Synth.Vertical.TScrollbar")
        kc.configure(yscrollcommand=self.knobs_bar.set)
        kc.pack(side="left", fill="both", expand=True)
        self.knobs = ttk.Frame(kc, padding=(10, 4, 10, 8), style="Synth.TFrame")
        self.knobs_win = kc.create_window(0, 0, window=self.knobs, anchor="nw")
        kc.bind("<Configure>", lambda e: self.fit_knobs())
        self.knobs.bind("<Configure>", lambda e: self.after_idle(self.fit_knobs))
        self.bind("<MouseWheel>", self.knobs_wheel, add="+")
        self.fx = SynthPane(self, hz)
        self.build_knobs(self.knobs)
        self.rack_box = ttk.Frame(self, padding=(10, 4, 10, 8), style="Synth.TFrame")  # (the Effects tab)
        self.build_rack(self.rack_box)
        self.mod_box = ttk.Frame(self, padding=(10, 4, 10, 8), style="Synth.TFrame")  # (the MOD tab)
        self.build_mod(self.mod_box)
        self.build_macros(self)  # (the macro strip: under the warning line on the OSC and FX tabs)
        self.build_mod_names(self.macro_box.body, MACROS + 1)  # (... with the MOD tab's sources, to link them)
        self.canvas = self.fx.canvas
        self.canvas.config(takefocus=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        k = self.piano
        k.bind("<Configure>", lambda e: self.draw_keys())
        k.bind("<ButtonPress-1>", self.on_key_press)
        k.bind("<B1-Motion>", self.on_key_drag)
        k.bind("<ButtonRelease-1>", self.on_key_release)
        k.bind("<Motion>", lambda e: self.show_status())
        self.bind("<KeyPress>", self.on_letter)
        self.bind("<KeyRelease>", self.on_letter_up)
        self.bind("<FocusOut>", lambda e: self.after(1, self.letters_lost))
        c = self.canvas
        c.bind("<Delete>", lambda e: (self.fx.delete_key(), "break")[1])
        for key in ("<Control-c>", "<Control-C>"):
            c.bind(key, lambda e: (self.fx.copy_points() and setattr(self.app, "hz_clip", None), "break")[1])
        for key in ("<Control-v>", "<Control-V>"):
            c.bind(key, lambda e: (self.fx.paste_points(), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_page()
        tag = f"SynthLetters{id(self)}"  # (a letter that plays a key, typed in a number box: the number taken, the
        for e in self.boxes_typed(self):  # key plays; user)
            e.bindtags((tag,) + e.bindtags())
        self.bind_class(tag, "<KeyPress>", self.box_letter)
        self.update_idletasks()  # (a synth's size (user: it grew past the screen): as the knobs need, at most
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()  # WIDE x TALL and the screen less its taskbar;
        w = min(max(round(1000 * s), self.knobs.winfo_reqwidth()), round(WIDE * s), sw - 40)  # the boxes in more
        self.lay_boxes(w - 20)  # rows then, and the Knobs tab scrolls)
        self.update_idletasks()
        tw, th = (max(f(b) for b in self.tab_buttons.values()) for f in (tk.Misc.winfo_reqwidth, tk.Misc.winfo_reqheight))
        for b in self.tab_buttons.values():  # (the big tabs all one size: the biggest one's, user)
            b.config(width=tw, height=th)
            b.pack_propagate(False)
        need = self.winfo_reqheight() - kc.winfo_reqheight() + self.knobs.winfo_reqheight()
        h = min(max(need, names_h + round(160 * s)), round(TALL * s), sh - 120)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h - 80) // 2)}")  # (in the screen's middle)
        remember_place(self, "synth")  # (or where it was last, as big)
        dark_title(self)

    def pick_page(self, key):
        """A big tab clicked."""
        self.page.set(key)
        self.show_page()

    def show_page(self):
        """The Knobs, the Effects, the MOD or the Lines tab shown (its big tab lit)."""
        pages = {"knobs": self.knobs_box, "effects": self.rack_box, "mod": self.mod_box, "lines": self.canvas}
        for key, w in pages.items():
            self.tab_buttons[key].picked(key == self.page.get())
            if key != self.page.get():
                w.pack_forget()
        self.macro_box.pack_forget()
        if self.page.get() in ("knobs", "effects"):  # (the macros' strip: over the OSC and FX tabs, so knobs on both
            # can be linked)
            self.macro_box.pack(fill="x", padx=round(10 * self.s), pady=(round(4 * self.s), 0), after=self.warn)
        pages[self.page.get()].pack(fill="both", expand=True)
        self.show_status()
        self.show_knobs()

    def fit_knobs(self):
        """The Knobs tab: the boxes in rows as wide as the window, a scrollbar when they're taller than it."""
        c = self.knobs_canvas
        if not self.winfo_exists() or c.winfo_width() < 50:
            return
        self.knobs.update_idletasks()  # (a box that just grew, e.g. a Mode's knobs shown: its new width first)
        self.lay_boxes(c.winfo_width() - 20)
        need, have = self.knobs.winfo_reqheight(), c.winfo_height()
        c.itemconfigure(self.knobs_win, width=c.winfo_width(), height=max(need, have))
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(need, have)))
        if need > have + 1:
            if not self.knobs_bar.winfo_ismapped():
                self.knobs_bar.pack(side="right", fill="y", before=c)
        elif self.knobs_bar.winfo_ismapped():
            self.knobs_bar.pack_forget()
            c.yview_moveto(0)

    def knobs_wheel(self, e):
        """The mouse wheel over the Knobs or the Effects tab scrolls it when it's too tall (not over a knob or a box:
        they turn)."""
        for c, bar in ((self.knobs_canvas, self.knobs_bar), (self.rack_canvas, self.rack_bar)):
            if (str(e.widget).startswith(str(c)) and bar.winfo_ismapped()
                    and not isinstance(e.widget, (Knob, tk.Entry, ttk.Entry))):
                c.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # ------------------------------------------------------------ the Hz bass window's lines (the pane works on these)

    fxl = property(lambda self: self.hz.fxl, lambda self, v: setattr(self.hz, "fxl", v))
    loops = property(lambda self: self.hz.loops, lambda self, v: setattr(self.hz, "loops", v))
    froms = property(lambda self: self.hz.froms, lambda self, v: setattr(self.hz, "froms", v))
    fits = property(lambda self: self.hz.fits, lambda self, v: setattr(self.hz, "fits", v))
    sustains = property(lambda self: self.hz.sustains, lambda self, v: setattr(self.hz, "sustains", v))
    lfo = property(lambda self: self.hz.lfo, lambda self, v: setattr(self.hz, "lfo", v))
    extra = property(lambda self: self.hz.extra, lambda self, v: setattr(self.hz, "extra", v))
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
        if getattr(self, "status_until", 0.0) > time.monotonic():  # (a message stays a few seconds)
            return
        knobs = self.page.get() != "lines"  # (the Effects tab has knobs too)
        letters = tr("hz.synth_letters", lo=note_name(self.kb_base), hi=note_name(min(127, self.kb_base + 16)))
        hint = tr("hz.synth_hint_knobs") if knobs else self.fx.says or tr("hz.synth_hint")
        self.status.config(text=hint if self.fx.says and not knobs else f"{hint}  {letters}")

    def draw_live(self):
        """(Every tick of the live keys / the preview) the moving dots and the words at the top right."""
        if not self.winfo_exists():
            return
        self.fx.draw_dots()
        self.draw_adsr_dot()
        self.draw_mod_dots()
        says, colour = self.live.says() if self.live.active() else (self.live.ready() or "", look.INFO)
        colour = DIM if colour == look.INFO else WARN  # (the Hz bass window's colours, for the dark look)
        if (self.says.cget("text"), str(self.says.cget("foreground"))) != (says, colour):
            self.says.config(text=says, foreground=colour)
        if not self.holding and self.live.key is None and self.piano.find_withtag("lit"):
            self.draw_keys()

    # ------------------------------------------------------------ the note meter

    def meter_later(self):
        """The sound may have changed: the note meter counted again once it rests for METER_MS."""
        if self.meter_job:
            self.after_cancel(self.meter_job)
        self.meter_job = self.after(METER_MS, self.count_notes)

    def count_notes(self):
        """The note meter: the notes a second this sound makes while one A1 is held (its first METER_BEATS: the
        engine's own notes, as the live keys make them), counted in the background (a big sound on 128 keys took
        seconds: the window stood still after every knob)."""
        self.meter_job = None
        if not self.winfo_exists():
            return
        try:
            ppq, bpm, _ = self.app.read_project()
        except ValueError:
            return
        sh = self.live.held_shape(METER_KEY, METER_BEATS)
        sig = json.dumps([sh, ppq], sort_keys=True, default=str)
        if sig in ((self.meter_for or (None,))[0], self.meter_asked):
            return
        self.meter_asked = sig
        wanted = lambda: self.meter_asked == sig  # (asked again meanwhile: not counted)
        self.meter_wait(METER_JOBS.submit(meter_count, sh, ppq, wanted), sig, bpm, 0)

    def meter_wait(self, job, sig, bpm, polls):
        """The count looked for every METER_POLL_MS; "…" shown once it takes a while."""
        self.meter_poll = None
        if not self.winfo_exists() or self.meter_asked != sig:
            return
        if not job.done():
            if polls == METER_SLOW and self.meter_for:
                self.meter_for = None
                self.draw_meter()
            self.meter_poll = self.after(METER_POLL_MS, lambda: self.meter_wait(job, sig, bpm, polls + 1))
            return
        self.meter_asked = None
        try:
            n = job.result()
        except Exception:
            from files.errors import write_log
            write_log(*sys.exc_info(), "note meter")
            return
        self.meter_for = (sig, n / (METER_BEATS * 60.0 / bpm))
        self.draw_meter()

    def draw_meter(self):
        """Top right: "Notes a second", the number, and a bar of lights (log scale; green, then orange, then red)."""
        c, s = self.meter, self.s
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50:
            return
        rate = self.meter_for[1] if self.meter_for else None
        pad = 4 * s
        c.create_rectangle(1, 1, w - 1, h - 1, fill=PANEL, outline=EDGE)
        c.create_text(pad + 2 * s, pad + 1 * s, text=tr("hz.synth_meter").upper(), anchor="nw", fill=DIM,
                      font=("Segoe UI Semibold", 7))
        c.create_text(w - pad - 2 * s, pad - 1 * s, text="…" if rate is None else f"{rate:,.0f}", anchor="ne",
                      fill=TEXT, font=("Segoe UI Semibold", 11))
        lights = 24
        lit = 0 if not rate else round(lights * min(1.0, max(0.0, math.log10(max(rate, 1.0)) / METER_TOP)))
        x0, x1, y0, y1 = pad + 2 * s, w - pad - 2 * s, h - pad - 12 * s, h - pad - 2 * s
        lw = (x1 - x0) / lights
        for i in range(lights):
            colour = "#62d36a" if i < lights * 0.7 else "#f0a040" if i < lights * 0.88 else "#f05050"
            c.create_rectangle(x0 + i * lw + 1, y0, x0 + (i + 1) * lw - 1, y1,
                               fill=colour if i < lit else mix(colour, PANEL, 0.82), outline="")

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
        lit = set(self.holding.values()) or set(self.live.held_keys())
        bh = h * 0.6
        for k, x0, x1, black in self.key_spots():
            on = k in lit
            reach = self.kb_base <= k <= self.kb_base + len(LETTERS) - 1
            fill = KEY_HELD if on else (KEY_BLACK if reach else OUT_BLACK) if black else KEY_WHITE if reach else OUT_WHITE
            c.create_rectangle(x0, 0, x1, bh if black else h, fill=fill, outline=BG,
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

    @property
    def held(self):
        """The key held last (None: none)."""
        return next(reversed(self.holding.values()), None)

    def hold(self, by, key):
        """`by` ("mouse" or a letter) presses key, the keys held already sounding on. Returns why it can't, or
        None."""
        why = self.live.press(key, parent=self)
        if why:
            self.status.config(text=why)
            return why
        self.holding[by] = key
        self.draw_keys()
        return None

    def on_key_press(self, e):
        k = self.key_at(e.x, e.y)
        if k is None:
            return
        if "mouse" in self.holding:
            self.let_go("mouse")
        if not self.hold("mouse", k):
            self.show_status()

    def on_key_drag(self, e):
        if "mouse" not in self.holding:
            return
        k = self.key_at(min(max(e.x, 0), self.piano.winfo_width() - 1), min(max(e.y, 0), self.piano.winfo_height() - 1))
        if k is not None and k != self.holding["mouse"]:  # (onto another key: that one plays, the one left lets go)
            self.let_go("mouse")
            self.hold("mouse", k)

    def on_key_release(self, e):
        if "mouse" in self.holding:
            self.let_go("mouse")

    def let_go(self, by=None):
        """`by` lets go of its key (None: every key held); a key still held by another (the mouse and a letter on
        one key) sounds on."""
        for b in [by] if by is not None else list(self.holding):
            key = self.holding.pop(b, None)
            if key is not None and key not in self.holding.values():
                self.live.release(key)
        self.draw_keys()

    # ------------------------------------------------------------ the computer keyboard's letters

    def on_letter(self, e, typed=False):
        """A letter pressed (not in a box, no Ctrl / Alt): its key plays, held until it's let go; Z / X move them.
        typed: pressed in a number box, what was typed there just taken (box_letter)."""
        typing = (not typed and isinstance(e.widget, (tk.Entry, ttk.Entry))
                  and str(e.widget.cget("state")) != "readonly")
        if typing or e.state & NO_LETTERS:  # (a dropdown with the keyboard: they play, user)
            return None
        k = e.keysym.lower()
        if k in ("z", "x"):
            self.kb_base = self.app.hz_kb_base = min(120, max(0, self.kb_base + (12 if k == "x" else -12)))
            self.show_status()
            self.draw_keys()
            return "break"
        if k not in LETTERS:
            return None
        if k in self.letter_up:  # (the key repeating: let go and pressed at once = still held)
            self.after_cancel(self.letter_up.pop(k))
        if k in self.holding:
            return "break"
        key = self.kb_base + LETTERS.index(k)
        if key > 127 or self.hold(k, key):
            return "break"
        self.codes[k] = e.keycode
        self.after(LETTER_CHECK_MS, lambda: self.letter_check(k))
        return "break"

    def boxes_typed(self, w):
        """Every number box in the window (Scrub boxes: knobs', macros', the Arpeggio's Steps)."""
        for c in w.winfo_children():
            if isinstance(c, (tk.Entry, ttk.Entry)) and getattr(c, "_scrub", False):
                yield c
            yield from self.boxes_typed(c)

    def box_letter(self, e):
        """A letter that plays a key (or Z / X) pressed while a number box has the keyboard: what's typed there is
        taken (as Enter does), the keyboard goes back to the window and the key plays (user: type a number, then
        hear it at once)."""
        k = e.keysym.lower()
        if e.state & NO_LETTERS or k not in LETTERS + ("z", "x"):
            return None
        e.widget.event_generate("<Return>")
        if self.winfo_exists():
            self.focus_set()
            return self.on_letter(e, typed=True)
        return "break"

    def letter_check(self, k):
        """A letter still held? Its key let go without telling us (the window was moved meanwhile): let go now."""
        if not self.winfo_exists() or k not in self.holding:
            return
        if key_down(self.codes.get(k)) is False:
            self.let_go(k)
            return
        self.after(LETTER_CHECK_MS, lambda: self.letter_check(k))

    def on_letter_up(self, e):
        k = e.keysym.lower()
        if k in self.holding and k != "mouse" and k not in self.letter_up:
            self.letter_up[k] = self.after(40, lambda: self.letter_gone(k))

    def letter_gone(self, k):
        self.letter_up.pop(k, None)
        if k in self.holding:
            self.let_go(k)

    def letters_lost(self):
        """The window lost the keyboard (another window clicked): the letters held stop (their let-go won't come)."""
        letters = [b for b in self.holding if b != "mouse"]
        if not self.winfo_exists() or not letters:
            return
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            w = None
        if w is None or w.winfo_toplevel() is not self:
            for b in letters:
                self.let_go(b)

    def close(self):
        for job in (self.meter_job, self.meter_poll, *self.letter_up.values()):
            if job:
                self.after_cancel(job)
        self.meter_asked = None
        self.letter_up = {}
        if self.holding:
            self.holding = {}
            self.live.release()
        if self.fx.asking:  # (the Repeat every… window)
            self.fx.asking.destroy()
        self.hz.synth_win = None
        self.destroy()
        free_sound_later(self.app)


def open_synth(hz):
    """Hz bass window → Synth…: the window opened (or brought up); the preview turned on, as the keys need it."""
    if hz.synth_win is not None and hz.synth_win.winfo_exists():
        hz.synth_win.deiconify()
        hz.synth_win.lift()
        return hz.synth_win
    keep_sound(hz.app)
    hz.synth_win = SynthWindow(hz)
    if not hz.preview_on.get():
        hz.preview_on.set(True)
        hz.on_preview()
    hz.synth_win.focus_set()
    return hz.synth_win
