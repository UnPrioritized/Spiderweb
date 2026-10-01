"""The Hz bass window (hzbass.py): a small piano roll where the tones of a Hz bass are placed. Pressing the mouse
places a note at once, and it follows the mouse (snapped to the nearest grid line; Shift = not) until the button is let go; a note that's
there is moved the same way, either end changes its length, Ctrl+drag (or a drag with Select) selects with a box, Delete removes the
selected ones, a double click removes the note under it. The key of the note held with the mouse sounds on the MIDI-out device. The window has its own snap.
Under the notes: the effects pane (hz_effects.py), one line for each effect over all the notes.
The red line is the tone travelling through the notes: it jumps at the next note unless its dots are dragged (lead
out of one note, lead in of the next), then it slides.

It follows the main window's selection: the selected custom shape's tones, or (Hz bass tool clicked on empty
space) a new Hz bass that's made with the first note and grows with the notes. Every change is an undo step of the
main window, made when the mouse is let go (the notes on the piano roll are made again then, not while dragging)."""

import copy
import ctypes
import math
import os
import re
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np

from files.about import ICONS
from files.lang import tr
from files.mathexpr import calc, fmt
from files.snap import snap_beats
from notes.engine import slot_track_channel
from notes.custom import BOX_STROKE, SPAM_FILLS, box_frame, custom_settings
from notes.hzbass import (AUTO, AUTO_MOST, FX, HZ_DEFAULTS, TUNE, auto_state, can_slide, clean_fx, clean_loop,
                          clean_off, clean_tones, fit_length, glide, heard, hz_of, left_edge, links, next_id, pitch,
                          tones_span)
from roll.roll_shared import (ALT, BOX_CURSORS, BOX_SCROLL_MS, BOX_STILL, CTRL, SELECT_CURSOR, SELECTED_COLOR, SHIFT,
                              SLOT_COLORS, boxes_side, boxes_upright, draw_boxes, grid_span,
                              note_name)
from roll.zoombar import add_zoom_bars
from window.hz_effects import AMOUNT, FxPane
from window.hz_preview import Preview
from window.preview_settings import open_preview_settings
from window.snap_picker import SnapPicker
from window.widgets import Scrub, Tooltip

BLACK = (1, 3, 6, 8, 10)
RED = "#e02020"
PLAY_LINE = "#0a50e0"  # (the main piano roll's)
GREY = "#8a8a8a"  # over what the preview hasn't made yet
ORANGE = "#c06000"
FAINT = "#f0a0a0"  # behind the red line: each repeat's own pitch
GREEN = "#18a048"  # a note's exact tone (the middle of its row)
# Auto gates: the threshold around a note's tone, (fill, edge) when it gets fixed / mixed gates
BAND_FIXED, BAND_MIXED = ("#8ee0a4", "#18a048"), ("#ffc27a", "#c06000")
GATE_MODES = ("auto", "mixed", "fixed")  # the Gates dropdown's choices, in order
TUNE_ROW = 20  # px: rows at least this tall show the exact tone, and the red line can be dragged up / down
POS = r"\d+x\d+\+-?\d+\+-?\d+"  # a remembered size and place
try:  # how quick a second click has to be to make a double click (Windows' setting)
    DOUBLE_MS = int(ctypes.windll.user32.GetDoubleClickTime())
except (AttributeError, OSError):
    DOUBLE_MS = 500


def open_hz(app):
    if app.hz_window:
        app.hz_window.lift()
    else:
        app.hz_window = HzWindow(app)
    app.hz_window.sync()


def gate_mode(hz):
    """A Hz bass's gates: "mixed", "fixed" or "auto"."""
    hz = hz or {}
    return "fixed" if hz.get("fixed") else "auto" if hz.get("auto") is not None else "mixed"


def auto_box(app, parent, var, apply):
    """The Auto gates threshold box ("within [3] cents"): a frame (not packed) with .entry. apply() on Enter,
    leaving the box, and each step of the number."""
    f = ttk.Frame(parent)
    lb = ttk.Label(f, text=tr("hz.auto_within"))
    lb.pack(side="left")
    f.entry = ttk.Entry(f, textvariable=var, width=4)
    f.entry.pack(side="left", padx=(4, 2))
    ttk.Label(f, text=tr("panel_custom.hz_cents"), foreground="#777").pack(side="left", padx=(0, 4))
    f.entry.bind("<Return>", lambda e: apply())
    f.entry.bind("<FocusOut>", lambda e: apply())
    Scrub(app, [(f.entry, var, apply)], (0.5, 5, 0.1), 0, AUTO_MOST, label=lb)
    for w in (lb, f.entry):
        Tooltip(w, tr("hz.auto_tip", most=f"{AUTO_MOST:g}"))
    return f


def hz_made(sh):
    """True for a Hz bass made with the Hz bass tool (hz["own"]): a box that is nothing but its placed tones."""
    hz = (sh or {}).get("hz") or {}
    return bool(hz.get("own") and hz.get("tones"))


def shape_length(sh):
    """How long a custom shape's box is, in beats."""
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    bs = (b0, b1, b2, b1 + b2 - b0)
    return max(bs) - min(bs)


class HzWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("hz.window_title"))
        self.transient(app)
        s = self.s = app.scale
        self.geometry(app.hz_pos if re.fullmatch(POS, app.hz_pos or "") else f"{round(820 * s)}x{round(700 * s)}")
        self.minsize(round(420 * s), round(260 * s))
        self.tones, self.sel = [], set()  # the notes shown (hzbass tones) and which are selected
        self.drag = None
        self.box_kept = None  # ([box_area, ...], selection) of the last Select boxes, shown after letting go
        self.box_timer = None  # (box_scroll)
        self.pending = None  # (tone id, beat): the first middle click of a slide, waiting for the second
        self.sounding = None  # (channel, keys) heard now: the notes held with the mouse
        self.last_len = 1.0  # beats: how long a newly placed note is (the last length used)
        self.fxl, self.fx_of = {}, None  # the effects' lines (hz["fx"]) and whose they are (the shape, or None)
        self.loops = {}  # the effects that repeat (hz["loop"])
        self.off = []  # the effects switched off (hz["off"])
        self.kb_w, self.ruler_h = round(44 * s), round(18 * s)
        names = tkfont.Font(family="Segoe UI", size=8, weight="bold")  # the keys column: wide enough for the
        self.kb_w = max(self.kb_w, round(20 * s) + max(names.measure(tr("hz.fx_" + n)) for n in FX))  # effects' names
        self.sx, self.sy, self.t0, self.top = 80.0 * s, 12.0 * s, -0.25, 64.0
        self.fitted = False

        # The toolbar: pieces that stay together; when the window is too narrow for one row, they wrap to a second
        # (layout)
        bar = self.bar = ttk.Frame(self, padding=(8, 6, 8, 4))
        bar.pack(fill="x")
        self.rows = [ttk.Frame(bar), ttk.Frame(bar)]
        self.pieces = []  # (frame, side)

        def piece(side="left"):
            f = ttk.Frame(bar)
            self.pieces.append((f, side))
            return f

        self.tool = tk.StringVar(value="pencil")
        f = piece()
        for key in ("select", "pencil"):
            b = ttk.Radiobutton(f, text=tr("hz." + key), value=key, variable=self.tool, style="Toolbutton",
                                takefocus=False)
            b.pack(side="left")
            Tooltip(b, tr(f"hz.{key}_tip"))
        f = piece()
        ttk.Label(f, text=tr("app.snap")).pack(side="left", padx=(10, 0))
        SnapPicker(app, f, app.hz_snap).button.pack(side="left", padx=(4, 10))
        ttk.Button(f, text=tr("app.fit_view"), command=self.fit_notes).pack(side="left", padx=(0, 10))
        f = piece()  # the whole Hz bass's pitch, in cents (moved here from the side panel, user)
        lb = ttk.Label(f, text=tr("hz.pitch"))
        lb.pack(side="left")
        self.pitch_var = tk.StringVar(value="0")
        self.pitch_entry = ttk.Entry(f, textvariable=self.pitch_var, width=5)
        self.pitch_entry.pack(side="left", padx=4)
        ttk.Label(f, text=tr("panel_custom.hz_cents"), foreground="#777").pack(side="left", padx=(0, 10))
        for w in (lb, self.pitch_entry):
            Tooltip(w, tr("panel_custom.hz_cents_tip"))
        self.pitch_entry.bind("<Return>", lambda e: self.on_pitch())
        self.pitch_entry.bind("<FocusOut>", lambda e: self.on_pitch())
        Scrub(app, [(self.pitch_entry, self.pitch_var, self.on_pitch)], (1, 10, 0.1), -1200, 1200, label=lb)
        f = piece()
        ttk.Label(f, text=tr("hz.gates")).pack(side="left")
        self.gates = ttk.Combobox(f, values=[tr("panel_custom.hz_" + m) for m in GATE_MODES],
                                  state="readonly", width=7)
        self.gates.current(GATE_MODES.index("auto"))  # (a new Hz bass: Auto, user)
        self.gates.pack(side="left", padx=(4, 10))
        self.gates.bind("<<ComboboxSelected>>", self.on_gates)
        Tooltip(self.gates, tr("panel_custom.hz_gates_tip"))
        self.auto_var = tk.StringVar(value=fmt(AUTO))
        self.auto_row = auto_box(app, f, self.auto_var, self.on_auto)  # (shown with Auto gates)
        f = piece()
        ttk.Label(f, text=tr("app.ppq")).pack(side="left")  # the project's PPQ: the same box as under Project
        ppq = ttk.Combobox(f, textvariable=app.pvar["ppq"], values=app.ppq_box["values"], width=7,
                           height=12)
        ppq.pack(side="left", padx=(4, 10))
        Tooltip(ppq, tr("hz.ppq_tip"))
        self.ppq_trace = app.pvar["ppq"].trace_add(
            "write", lambda *a: self.after_idle(lambda: self.winfo_exists() and self.redraw()))
        f = piece()
        self.preview_on = tk.BooleanVar(value=False)
        b = ttk.Checkbutton(f, text=tr("hz.preview"), variable=self.preview_on, command=self.on_preview,
                            style="Toolbutton", takefocus=False)
        b.pack(side="left")
        Tooltip(b, tr("hz.preview_tip"))
        b = ttk.Button(f, text=tr("hz.preview_settings"), command=lambda: open_preview_settings(self),
                       takefocus=False)
        b.pack(side="left", padx=(4, 0))
        Tooltip(b, tr("hz.preview_settings_tip"))
        self.preview_says = ttk.Label(f, text="", foreground="#555")
        self.preview_says.pack(side="left", padx=(6, 10))
        self.settings_window = None  # Preview settings… (preview_settings.py)
        f = piece()
        self.what = ttk.Label(f, text="", foreground="#555")
        self.what.pack(side="left", padx=(0, 10))
        f = piece("right")
        fx_box = ttk.Checkbutton(f, text=tr("hz.fx"), variable=app.hz_fx, command=self.on_fx, style="Toolbutton",
                                 takefocus=False)
        fx_box.pack(side="left", padx=(0, 10))
        Tooltip(fx_box, tr("hz.fx_tip"))
        line_box = ttk.Checkbutton(f, text=tr("hz.line"), variable=app.hz_line, command=self.on_line)
        line_box.pack(side="left", padx=(0, 10))
        Tooltip(line_box, tr("hz.line_tip"))
        self.grow = tk.BooleanVar(value=True)
        self.grow_box = ttk.Checkbutton(f, text=tr("hz.grow"), variable=self.grow, command=self.on_grow)
        self.grow_box.pack(side="left")
        Tooltip(self.grow_box, tr("hz.grow_tip"))
        self.laid = None  # which row each piece is in now
        for f, side in self.pieces:
            f.bind("<Configure>", lambda e: self.after_idle(self.layout))
        bar.bind("<Configure>", lambda e: self.after_idle(self.layout))
        self.layout()
        self.status = ttk.Label(self, text="", foreground="#555", padding=(8, 2, 8, 4))
        self.status.pack(side="bottom", fill="x")
        self.fx = FxPane(self)
        self.notes_box = tk.Frame(self)  # the notes with the main piano roll's scrollbars (zoombar.py)
        self.notes_box.pack(fill="both", expand=True)
        c = self.canvas = tk.Canvas(self.notes_box, background="white", highlightthickness=0, takefocus=True)
        self.scale, self.bars = s, ()
        add_zoom_bars(self.notes_box, self, c)
        self.on_fx()
        self.pencil = ("@" + os.path.join(ICONS, "pencil.cur").replace("\\", "/"),)  # its tip is the spot pointed at
        try:
            c.config(cursor=self.pencil)
        except tk.TclError:  # (the file can't be read: the built-in one)
            self.pencil = "pencil"
        c.config(cursor="")
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-2>", self.pan_start)
        c.bind("<B2-Motion>", self.pan_move)
        c.bind("<ButtonRelease-2>", self.on_middle)
        c.bind("<ButtonPress-3>", self.on_menu)
        c.bind("<Double-Button-3>", self.toggle_tool)
        self.menu_wait = None  # a right click on empty space: its menu, waiting to see if it's a double click
        c.bind("<Motion>", self.on_motion)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Delete>", lambda e: (self.fx.delete_key() or self.delete_selected())
               or "break")  # (effect points selected: they go; none, the pane pressed last: the highlighted effect)
        for k in ("<Control-c>", "<Control-C>"):  # (effect points selected: they're copied; else the notes)
            c.bind(k, lambda e: self.copy_notes() or "break")
        for k in ("<Control-v>", "<Control-V>"):  # (what was copied last: effect points, or notes at the play line)
            c.bind(k, lambda e: (self.fx.paste_points() or self.paste_notes(self.play_line_beat()), "break")[1])
        c.bind("<Escape>", lambda e: self.select(()) or "break")
        self.bind("<space>", self.on_space)  # (anywhere in the window: the buttons don't take the keyboard)
        for k in ("<Control-a>", "<Control-A>"):
            c.bind(k, lambda e: self.select(range(len(self.tones))) or "break")
        for k, tool in (("p", "pencil"), ("P", "pencil"), ("v", "select"), ("V", "select")):
            c.bind(f"<KeyPress-{k}>", lambda e, tool=tool: self.tool.set(tool) or self.on_motion(e) or "break")
        self.bind("<Configure>", self.remember)
        self.protocol("WM_DELETE_WINDOW", self.close)
        c.focus_set()
        self.preview = Preview(self)
        if app.hz_preview["on"]:  # (on last time: on again, if its soundfont is still there)
            self.after_idle(self.preview_again)

    # ------------------------------------------------------------ what it shows

    def target(self):
        """The shape whose tones are shown: the one selected custom shape (not text or pasted notes), or None."""
        app = self.app
        sh = app.selected()
        if sh and len(app.sels) == 1 and sh["kind"] == "custom" and not sh.get("text") and "notes" not in sh:
            return sh
        return None

    def can_place(self):
        return self.target() is not None or self.app.hz_start is not None

    def sync(self):
        """The main window's selection or shapes changed (undo too): show what's there now."""
        sh = self.target()
        hz = (sh or {}).get("hz") or {}
        tones = clean_tones(hz.get("tones"))
        if tones != self.tones:
            self.tones, self.sel = tones, set()
            self.drop_drag()
        if sh is not None or self.fx_of is not None:  # (no Hz bass yet: the lines picked stay for the first note)
            self.fxl, self.fx_of = clean_fx(hz.get("fx") or {}), (id(sh) if sh is not None else None)
            self.loops = clean_loop(hz.get("loop"), self.fxl)
            self.fxl.update({k + AMOUNT: v for k, v in clean_fx(hz.get("amount") or {}).items() if k in self.loops})
            self.off = clean_off(hz.get("off"), self.fxl)
        if sh is None:
            text = (tr("hz.hint_new", beat=fmt(self.app.hz_start + 1)) if self.app.hz_start is not None
                    else tr("hz.hint_none"))
            self.grow.set(True)
        else:
            text = tr("hz.shape", name=self.app.shape_label(sh))
            self.grow.set(bool(hz.get("grow")) if tones else hz_made(sh))
        if hz:  # (no Hz bass yet: the dropdown stays as picked, for the one the first note makes)
            self.pitch_var.set(fmt(hz["cents"]))
            self.pitch_entry.config(style="TEntry")
            self.gates.current(GATE_MODES.index(gate_mode(hz)))
            if hz.get("auto") is not None:
                self.auto_var.set(fmt(hz["auto"]))
        self.gates.config(state="readonly" if self.can_place() else "disabled")
        self.pitch_entry.config(state="normal" if self.can_place() else "disabled")
        self.show_auto()
        self.what.config(text=text)
        self.after_idle(self.layout)  # (its width changed)
        self.grow_box.config(state="normal" if sh is not None else "disabled")
        if self.tones and not self.fitted:
            self.fit_view()
        self.redraw()

    def before_restore(self):
        """Undo / redo is about to change the shapes: what's shown now (for after_restore)."""
        app, sh = self.app, self.target()
        if sh is not None and hz_made(sh):
            ps = [p for _, p in sh["pts"]]
            ps.append(ps[1] + ps[2] - ps[0])
            return "shape", left_edge(sh), {"lo": round(min(ps)), "hi": round(max(ps))}
        if sh is None and app.hz_start is not None:
            return "start", len(app.shapes)
        return None

    def after_restore(self, was):
        """Undo / redo changed the shapes. The Hz bass shown was taken back whole: its start spot is back, so notes
        can be placed again. A Hz bass came back on the start spot: it's the one shown again."""
        app = self.app
        if was and was[0] == "shape" and self.target() is None:
            app.hz_start, app.hz_defaults = was[1], was[2]
            app.roll.request_redraw()
        elif was and was[0] == "start" and len(app.shapes) > was[1]:
            last = app.shapes[-1]
            if hz_made(last) and abs(left_edge(last) - app.hz_start) < 1e-9:
                app.hz_start = None
                app.select(len(app.shapes) - 1)
        self.sync()

    def fit_view(self):
        """The view moved so the notes are in sight (the first time there are any)."""
        self.fitted = True
        keys = [n["key"] for n in self.tones]
        rows = max(1.0, (self.canvas.winfo_height() - self.ruler_h) / self.sy)
        self.top = min(127.0, max(rows - 1, (max(keys) + min(keys)) / 2 + rows / 2))

    # ------------------------------------------------------------ view

    def x_of(self, beat):
        return self.kb_w + (beat - self.t0) * self.sx

    def beat_at(self, x):
        return self.t0 + (x - self.kb_w) / self.sx

    def y_of(self, key):
        """The top of a key's row."""
        return self.ruler_h + (self.top - key) * self.sy

    def key_at(self, y):
        return max(0, min(127, math.ceil(self.top - (y - self.ruler_h) / self.sy)))

    def clamp_view(self):
        rows = max(1.0, (self.canvas.winfo_height() - self.ruler_h) / self.sy)
        self.top = min(127.0, max(min(127.0, rows - 1), self.top))
        self.t0 = max(-0.25, self.t0)

    def on_wheel(self, e):
        """The same as on the main piano roll (PianoRoll.on_wheel): wheel = up / down 3 keys, Shift = sideways,
        Ctrl = zoom both ways around the mouse, Ctrl+Shift = time only, Alt = keys only."""
        up = e.delta > 0
        f = 1.25 if up else 0.8
        zoom_time = e.state & CTRL and not e.state & ALT
        zoom_keys = (e.state & CTRL and not e.state & SHIFT) or e.state & ALT
        if zoom_time:
            self.zoom_x(f, e.x)
        if zoom_keys:
            self.zoom_y(f, e.y)
        if not (zoom_time or zoom_keys):
            if e.state & SHIFT:
                self.t0 += (-1 if up else 1) * 120 / self.sx
            else:
                self.top += 3 if up else -3
        self.clamp_view()
        self.redraw()

    def fit_notes(self):
        """The Fit view button: every note in sight (no notes: the first bars, the keys as they are)."""
        w, h = self.canvas.winfo_width() - self.kb_w, self.canvas.winfo_height() - self.ruler_h
        if w < 50 or h < 50:
            return
        lo, hi = (0.0, max(n["t"] + n["len"] for n in self.tones)) if self.tones else (0.0, 4.0 * self.app.beats)
        span = max(hi - lo, 1.0)
        self.sx, self.t0 = w / (span * 1.06), lo - span * 0.03
        if self.tones:
            keys = [n["key"] for n in self.tones]
            rows = max(keys) - min(keys) + 1 + 4  # (two keys of room above and below)
            self.sy = min(12.0 * self.s, max(1.0, h / rows))  # (a few notes: not huge rows)
            self.top = (max(keys) + min(keys)) / 2 + h / self.sy / 2
        self.clamp_view()
        self.redraw()

    def zoom_x(self, f, x):
        """Zoom time by f; the beat at canvas x stays where it is."""
        b = self.beat_at(x)
        self.sx = min(100000.0, max(0.05, self.sx * f))
        self.t0 = b - (x - self.kb_w) / self.sx

    def zoom_y(self, f, y):
        """Zoom the keys by f; the key at canvas y stays where it is."""
        k = self.top - (y - self.ruler_h) / self.sy
        self.sy = min(60.0 * self.s, max(1.0, self.sy * f))
        self.top = k + (y - self.ruler_h) / self.sy

    # ------------------------------------------------------------ scrollbars (zoombar.py, like the main piano roll's)
    # Across they count beats from the view's leftmost spot (-0.25), up and down keys from the top (127).

    def bar_view(self, across):
        """(start, end, total) of what's seen. The time bar reaches 8 bars past the last note (as on the main piano
        roll); scrolled further with the mouse, the total grows to where the view ends."""
        c = self.canvas
        if across:
            a, span = self.t0 + 0.25, max(1, c.winfo_width() - self.kb_w) / self.sx
            end = (max(n["t"] + n["len"] for n in self.tones) if self.tones else 0.0) + 0.25 + 8 * self.app.beats
            return a, a + span, max(end, a + span)
        a, span = 127.0 - self.top, max(1, c.winfo_height() - self.ruler_h) / self.sy
        return a, a + span, max(128.0, a + span)

    def bar_move(self, across, a):
        """A scrollbar was dragged to a (by whole pixels)."""
        if across:
            self.t0 = self.t0 + round((a - 0.25 - self.t0) * self.sx) / self.sx if a > 0 else -0.25
        else:
            self.top = self.top + round((127.0 - a - self.top) * self.sy) / self.sy if a > 0 else 127.0
        self.clamp_view()
        self.redraw()

    def bar_zoom(self, across, a, b, which):
        """A scrollbar's end was dragged (which: "start" / "end"): the view shows a..b, the other end stays."""
        c = self.canvas
        if across:
            px = max(1, c.winfo_width() - self.kb_w)
            self.sx = min(100000.0, max(0.05, px / (b - a)))
            self.t0 = (a if which == "end" else b - px / self.sx) - 0.25
        else:
            px = max(1, c.winfo_height() - self.ruler_h)
            self.sy = min(60.0 * self.s, max(1.0, px / (b - a)))
            self.top = 127.0 - (a if which == "end" else b - px / self.sy)
        self.clamp_view()
        self.redraw()

    def zoom_step(self, across, f):
        """The "+" / "-" buttons: zoom around the middle of the view."""
        c = self.canvas
        if across:
            self.zoom_x(f, (self.kb_w + c.winfo_width()) / 2)
        else:
            self.zoom_y(f, (self.ruler_h + c.winfo_height()) / 2)
        self.clamp_view()
        self.redraw()

    def pan_start(self, e):
        self.pan = (e.x, e.y, self.t0, self.top)

    def pan_move(self, e):
        x, y, t0, top = self.pan
        self.t0, self.top = t0 - (e.x - x) / self.sx, top + (e.y - y) / self.sy
        self.clamp_view()
        self.redraw()

    # ------------------------------------------------------------ drawing

    def redraw(self):
        c = self.canvas
        for bar in self.bars:
            bar.refresh()
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        s, kb, rh = self.s, self.kb_w, self.ruler_h
        k_hi, k_lo = self.key_at(rh), self.key_at(h)
        for k in range(k_lo, k_hi + 1):  # rows
            y = self.y_of(k)
            if k % 12 in BLACK:
                c.create_rectangle(kb, y, w, y + self.sy, fill="#eef1f8", outline="")
            c.create_line(kb, y + self.sy, w, y + self.sy, fill="#c9c9c9" if k % 12 == 0 else "#ececec")
        beats, sb = self.app.beats, self.snap_beats()
        step = sb if sb and sb * self.sx >= 8 else 1.0
        if step * self.sx < 8:  # zoomed far out: bars, then every 2nd, 4th... bar
            step = float(beats)
            while step * self.sx < 8:
                step *= 2
        n = math.floor(max(0.0, self.beat_at(kb)) / step)
        while n * step <= self.beat_at(w):  # columns
            b = n * step
            x = self.x_of(b)
            whole = abs(b - round(b)) < 1e-9
            bar = whole and round(b) % beats == 0
            c.create_line(x, rh, x, h, fill="#707070" if bar else "#bdbdbd" if whole else "#ececec")
            n += 1
        sh = self.target()
        if sh is not None and not self.grow.get():  # the shape ends here: what's after it isn't used
            x = max(kb, self.x_of(shape_length(sh)))
            c.create_rectangle(x, rh, w, h, fill="#d8d8d8", outline="", stipple="gray50")
            c.create_line(x, rh, x, h, fill="#909090", dash=(4, 3))
        for i, n in enumerate(self.tones):  # notes
            x0, x1, y = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            fill, edge = SELECTED_COLOR if i in self.sel else SLOT_COLORS[0]
            c.create_rectangle(x0, y + 1, max(x1, x0 + 2), y + self.sy - 1, fill=fill, outline=edge)
        if self.app.hz_line.get():
            self.draw_line(w)
        for x, y, *_ in self.dots():
            r = 3.5 * s
            c.create_oval(x - r, y - r, x + r, y + r, fill="white", outline=RED, width=max(1, round(1.5 * s)))
        d = self.drag  # the Select box (with the ones kept when Ctrl+drag adds it), or the last ones (kept_box)
        boxes = d["more"] + [b for b in (self.box_area(),) if b] if d and d["kind"] == "box" else self.kept_box() or []
        draw_boxes(c, [self.box_rect(b) for b in boxes], kb, rh, max(2, round(2 * s)))
        c.create_rectangle(0, 0, kb, h, fill="#fafafa", outline="", tags="frame")  # keys (the preview's grey
        # goes under this: draw_preview)
        for k in range(k_lo, k_hi + 1):
            y = self.y_of(k)
            if k % 12 in BLACK:
                c.create_rectangle(0, y, kb * 0.6, y + self.sy, fill="#303030", outline="")
            if self.sy >= 4 * s:
                c.create_line(0, y + self.sy, kb, y + self.sy, fill="#d0d0d0")
        for k in range(k_lo, k_hi + 1):  # (the names after the keys, so small rows don't cover them)
            y = self.y_of(k)
            if k % 12 == 0 or (self.sy >= 15 * s and k % 12 not in BLACK):
                c.create_text(kb - 3, y + self.sy / 2, text=note_name(k), anchor="e", fill="#222",
                              font=("Segoe UI", 7, "bold" if k % 12 == 0 else "normal"))
        c.create_line(kb, 0, kb, h, fill="#707070")
        c.create_rectangle(0, 0, w, rh, fill="#f3f3f3", outline="")  # bar numbers
        every = 1  # (zoomed far out: every 2nd, 4th... bar number, so they don't run into each other)
        while every * beats * self.sx < 40 * s:
            every *= 2
        n = max(0, math.floor(self.beat_at(kb) / beats / every) * every)
        while n * beats <= self.beat_at(w):
            x = self.x_of(n * beats)
            if x >= kb:
                c.create_text(x + 3, rh / 2, text=str(n + 1), anchor="w", fill="#333", font=("Segoe UI", 8))
            n += every
        c.create_line(0, rh, w, rh, fill="#707070")
        if not self.can_place():
            c.create_text((kb + w) / 2, (rh + h) / 2, text=tr("hz.hint_none"), fill="#777",
                          width=w - kb - 40 * s, justify="center")
        self.show_status()
        self.fx.redraw()
        self.preview.shown = None
        self.draw_preview()

    def tune_rows(self):
        """True when the rows are tall enough to see and change a note's tune."""
        return self.sy >= TUNE_ROW * self.s

    def pitch_y(self, key):
        """Where a pitch (in keys, not whole) is: a key's exact tone is the middle of its row."""
        return self.ruler_h + (self.top - key + 0.5) * self.sy

    def draw_line(self, w):
        """The red line: the tone that's really heard. Each repeat lasts a whole number of ticks, so the line sits
        a little off the middle of the row (the exact tone), more at a low PPQ. It stops dead at a note's end; only
        a slide goes on to the next note."""
        c, app, kb = self.canvas, self.app, self.kb_w
        width = max(2, round(2 * self.s))
        for a, b, s in links(self.tones):  # a slide over a gap between two notes: no sound there
            x0, x1 = a["t"] + a["len"], b["t"]
            f0, f1, k0, k1 = glide(a, b, s)
            if x1 - x0 > 1e-9:
                c.create_line(self.x_of(x0), self.pitch_y(k0 + (k1 - k0) * (x0 - f0) / (f1 - f0)),
                              self.x_of(x1), self.pitch_y(k0 + (k1 - k0) * (x1 - f0) / (f1 - f0)),
                              fill=RED, dash=(3, 3))
        sh = self.target()
        bpm = float(app.current_bpm() or 120)
        hz = dict((sh or {}).get("hz") or self.new_hz(bpm), tones=self.tones)
        left = left_edge(sh) if sh is not None else app.hz_start or 0.0
        lo, hi = self.beat_at(kb), self.beat_at(w)
        for n in self.tones:  # Auto gates: the threshold around each note's tone, green = fixed, orange = mixed
            got = auto_state(hz, app.ppq, n)
            if got is None or n["t"] > hi or n["t"] + n["len"] < lo:
                continue
            y, half = self.pitch_y(pitch(n)), max(2.5 * self.s, got[1] / 100.0 * self.sy)  # (always seen)
            fill, edge = BAND_FIXED if got[2] else BAND_MIXED
            c.create_rectangle(self.x_of(n["t"]), y - half, self.x_of(n["t"] + n["len"]), y + half, fill=fill,
                               outline=edge if half >= 3 * self.s else "")
        runs = []
        for a, b, keys, mean in heard(hz, left, app.ppq, bpm):
            see = (b >= lo) & (a <= hi)
            if see.any():
                runs.append((self.x_of(a[see]), self.x_of(b[see]), self.pitch_y(keys[see]), self.pitch_y(mean[see])))
        # faint: every repeat's own pitch (its whole-tick gate); over it, red: the average = the tone heard
        for colour, wide, which in ((FAINT, 1, 2), (RED, width, 3)):
            for run in runs:
                x0, x1, y = run[0], run[1], run[which]
                if (x1[-1] - x0[0]) >= 2 * len(x0):  # every repeat can be seen: steps
                    c.create_line(*np.column_stack([x0, y, x1, y]).ravel().tolist(), fill=colour, width=wide)
                    continue
                # too many to draw each: a band from the lowest to the highest repeat at every pixel
                col = np.floor(x0)
                at = np.flatnonzero(np.concatenate([[True], col[1:] != col[:-1]]))
                top, bottom = np.minimum.reduceat(y, at), np.maximum.reduceat(y, at)
                xs = np.append(col[at], x1[-1])
                top, bottom = np.append(top, top[-1]), np.append(bottom, bottom[-1])
                pts = np.concatenate([np.column_stack([xs, top]), np.column_stack([xs, bottom])[::-1]])
                c.create_polygon(*pts.ravel().tolist(), fill=colour, outline=colour, width=wide)
        if self.pending:  # the first middle click of a slide: where it will start
            n = next((n for n in self.tones if n["id"] == self.pending[0]), None)
            if n is not None:
                x, y, r = self.x_of(self.pending[1]), self.pitch_y(pitch(n)), 3.5 * self.s
                c.create_oval(x - r, y - r, x + r, y + r, fill=RED, outline=RED)
        if self.tune_rows():  # the exact tone of each note, over the red line: a line right on it shows green
            for n in self.tones:
                y = self.pitch_y(n["key"])
                c.create_line(self.x_of(n["t"]), y, self.x_of(n["t"] + n["len"]), y, fill=GREEN)

    def dots(self):
        """[(x, y, tone number, "in" / "out", slide)]: the red line's dots, two for each slide made: "out" on the
        note it leaves, "in" on the note it goes to (tone number = the note the dot is on). A dot with no lead sits
        just outside its note's end (so the end itself stays free for changing the note's length). None while the
        red line is hidden."""
        out = []
        if not self.app.hz_line.get():
            return out
        index = {id(n): i for i, n in enumerate(self.tones)}
        off = 6 * self.s
        for a, b, s in links(self.tones):
            x0, x1, _, _ = glide(a, b, s)
            out.append((self.x_of(x0) + (off if x0 >= a["t"] + a["len"] else 0), self.pitch_y(pitch(a)),
                        index[id(a)], "out", s))
            out.append((self.x_of(x1) - (off if x1 <= b["t"] else 0), self.pitch_y(pitch(b)), index[id(b)], "in", s))
        return out

    def pairs(self):
        """[(a, b)]: the slides that can go between the selected notes. Two notes: the earlier to the later. More:
        the first one to each of the others, or (when they don't all start after its end) each of the others to
        the last one. None when they don't follow each other like that."""
        if len(self.sel) < 2 or max(self.sel) >= len(self.tones):
            return []
        ns = sorted((self.tones[i] for i in self.sel), key=lambda n: (n["t"], n["key"]))
        if all(can_slide(ns[0], n) for n in ns[1:]):
            return [(ns[0], n) for n in ns[1:]]
        last = max(ns, key=lambda n: n["t"])
        if all(can_slide(n, last) for n in ns if n is not last):
            return [(n, last) for n in ns if n is not last]
        return []

    @staticmethod
    def link(a, b):
        """The slide from tone a to tone b, or None."""
        return next((s for s in a["to"] if s["id"] == b["id"]), None)

    def show_status(self, e=None):
        n = len(self.tones)
        text = tr("hz.one_note") if n == 1 else tr("hz.n_notes", n=n)
        sh = self.target()
        if sh is not None and self.tones:
            text += "     " + tr("hz.repeats", n=f"{self.app.note_count(sh):,}")
        if e is not None and e.x >= self.kb_w and e.y >= self.ruler_h:
            k = self.key_at(e.y)
            cents = ((sh or {}).get("hz") or HZ_DEFAULTS)["cents"]
            text += "     " + tr("hz.position", beat=fmt(max(0.0, self.beat_at(e.x)) + 1), key=note_name(k),
                                 hz=f"{hz_of(k, cents):.2f}")
        if self.drag and self.drag["kind"] == "tune":
            text += "     " + tr("hz.tune", cents=f"{self.tones[self.drag['i']]['cents']:+g}")
        hit = self.hit(e.x, e.y) if e is not None and not self.drag else None
        got = (auto_state(sh["hz"], self.app.ppq, self.tones[hit[1]])
               if hit and hit[0] not in ("in", "out") and sh is not None and sh.get("hz") else None)
        if got:  # Auto gates: what this note gets, and why
            text += "     " + tr("hz.auto_fixed" if got[2] else "hz.auto_mixed", off=f"{got[0]:.2f}",
                                 limit=f"{got[1]:g}")
        if self.fx.says:
            text = self.fx.says
        self.status.config(text=text)

    # ------------------------------------------------------------ mouse

    def snap(self, beat, e):
        """beat on the snap grid: the nearest line (Shift = off)."""
        sb = self.snap_beats()
        if not sb or e.state & SHIFT:
            sb = 1 / self.app.ppq
        return max(0.0, round(beat / sb) * sb)

    def snap_beats(self):
        return snap_beats(self.app.hz_snap.get(), self.app.beats)

    def box_area(self, d=None):
        """The Select box being dragged (d: this box drag instead) as (beat, key, beat, key) corners, a key being
        the top of its row (fractions: in between): out to whole snap steps and whole keys (grid_span; Shift = as
        dragged). None while it's still a click."""
        d = d or self.drag
        (x, y), (cx, cy) = d["from"], d["to"]
        if abs(cx - x) < BOX_STILL and abs(cy - y) < BOX_STILL:
            return None
        if d.get("shift"):
            return tuple(v for p in ((x, y), (cx, cy)) for v in (self.beat_at(p[0]),
                                                                    self.top - (p[1] - self.ruler_h) / self.sy))
        b0, b1 = grid_span(self.beat_at(x), self.beat_at(cx), self.snap_beats())
        k0, k1 = sorted((self.key_at(y), self.key_at(cy)))
        return b0, k1, b1, k0 - 1

    def box_rect(self, area):
        """A box_area on screen (x0, y0, x1, y1), x0 < x1, y0 < y1."""
        (x0, x1), (y0, y1) = (sorted((self.x_of(area[0]), self.x_of(area[2]))),
                              sorted((self.y_of(area[1]), self.y_of(area[3]))))
        return x0, y0, x1, y1

    def box_to(self, d):
        """The Select box's corner goes to the mouse (d["mouse"]), kept inside the piano roll, and the box selects
        the notes it touches."""
        x, y, state = d["mouse"]
        d["to"] = (min(max(x, self.kb_w), self.canvas.winfo_width()),
                   min(max(y, self.ruler_h), self.canvas.winfo_height()))
        d["shift"] = bool(state & SHIFT)
        box = self.box_area(d)
        if box is None:  # (still a click)
            self.sel = set(d["base"])
            return self.redraw()
        x0, y0, x1, y1 = self.box_rect(box)
        self.sel = d["base"] | {i for i, n in enumerate(self.tones)
                    if self.x_of(n["t"]) < x1 and self.x_of(n["t"] + n["len"]) > x0
                    and self.y_of(n["key"]) < y1 and self.y_of(n["key"]) + self.sy > y0}
        self.redraw()

    def box_scroll(self):
        """A Select box dragged past the edge: the view goes a beat that way (3 keys up / down) at once and then
        every BOX_SCROLL_MS while the mouse stays out there, as on the main piano roll. The box stays where it is in
        the song (left behind as the view moves) until the mouse moves again."""
        self.box_timer = None
        d = self.drag
        if not d or d["kind"] != "box":
            return
        x, y, _ = d["mouse"]
        dx = (x > self.canvas.winfo_width()) - (x < self.kb_w)
        dy = (y > self.canvas.winfo_height()) - (y < self.ruler_h)
        if not dx and not dy:
            return
        t0, top = self.t0, self.top
        self.t0 += dx
        self.top -= dy * 3
        self.clamp_view()
        for end in ("from", "to"):
            ex, ey = d[end]
            d[end] = (ex - (self.t0 - t0) * self.sx, ey + (self.top - top) * self.sy)
        self.redraw()
        self.box_timer = self.after(BOX_SCROLL_MS, self.box_scroll)

    def stretch_to(self, d, e):
        """The kept Select box's right side dragged: it goes to the mouse (the grid line nearest it, Shift = not
        snapped) and every note it selected gets that much longer / shorter, the same for all (user, like Domino:
        one grid step = one grid step on each note); starts and keys stay."""
        (b0, _, b1, _), orig = d["area"], d["orig"]
        at = max(self.snap(self.beat_at(e.x), e), b0 + self.shortest(e))
        for i in self.sel:
            self.tones[i]["len"] = max(1 / self.app.ppq, orig[i]["len"] + at - b1)
        d["box"] = [(a, t, max(a + 1 / self.app.ppq, z + at - b1), u) for a, t, z, u in d["boxes"]]  # (each one's
        self.box_kept = (d["box"], set(self.sel))  # right side the same amount, like the notes)
        self.redraw()
        self.show_status(e)

    def kept_box(self):
        """The last Select boxes [box_area, ...] (Ctrl+drag adds one), still shown after letting go while what they
        selected is still the selection (a press or any other change of the selection drops them). None = not
        shown."""
        if self.box_kept and self.box_kept[1] == self.sel:
            return self.box_kept[0]
        self.box_kept = None
        return None

    def shortest(self, e):
        sb = self.snap_beats()
        return sb if sb and not e.state & SHIFT else 1 / self.app.ppq

    def hit(self, x, y):
        """What's under the mouse: ("in" / "out", tone, slide) a red dot,("left" / "right", tone) a note's end,
        ("tune", tone) the red line in a note (tall rows only), ("note", tone), or None."""
        r = 6 * self.s
        for dx, dy, i, which, s in self.dots():
            if abs(x - dx) <= r and abs(y - dy) <= r:
                return which, i, s
        if x < self.kb_w or y < self.ruler_h:
            return None
        for i in range(len(self.tones) - 1, -1, -1):
            n = self.tones[i]
            x0, x1, y0 = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            if x0 - 1 <= x <= max(x1, x0 + 2) + 1 and y0 <= y < y0 + self.sy:
                edge = min(5 * self.s, (x1 - x0) / 3)
                on_line = (self.tune_rows() and self.app.hz_line.get()
                           and abs(y - self.pitch_y(pitch(n))) <= 4 * self.s)
                return ("right" if x >= x1 - edge else "left" if x <= x0 + edge else
                        "tune" if on_line else "note"), i
        return None

    def on_kept_box(self, kept, e, hit):
        """Where the mouse is on the kept Select boxes: (1, 0) the right side (its corners too), (0, 0) inside (a
        note there wins), or None. The left side, top and bottom do nothing (user, like Domino); a slide's dot
        wins over it all. With Ctrl only inside counts, notes too (a drag there moves a copy; Select tool only)."""
        if not kept or not self.sel or hit and hit[0] in ("in", "out"):
            return None
        side = boxes_side([self.box_rect(a) for a in kept], e.x, e.y, 5 * self.s)
        if e.state & CTRL:  # (the pencil's Ctrl+drag is its only box: a new one there)
            return (0, 0) if side == (0, 0) and self.tool.get() == "select" else None
        if side and side[0] == 1:
            return 1, 0
        return (0, 0) if side == (0, 0) and not hit else None

    def on_motion(self, e):
        hit = self.hit(e.x, e.y)
        # a pencil where a press places a note (not on the keys or bar numbers, not with Ctrl: that's the box)
        inside = e.x >= self.kb_w and e.y >= self.ruler_h
        empty = (SELECT_CURSOR if inside and self.tool.get() == "select" else
                 self.pencil if inside and self.can_place() and not e.state & CTRL else "")
        on_box = self.on_kept_box(self.kept_box(), e, hit)
        if on_box:
            self.canvas.config(cursor=BOX_CURSORS[on_box])
            return self.show_status(e)
        self.canvas.config(cursor={"in": "sb_h_double_arrow", "out": "sb_h_double_arrow", "left": "sb_h_double_arrow",
                                   "right": "sb_h_double_arrow", "tune": "sb_v_double_arrow",
                                   "note": "fleur"}.get(hit and hit[0], empty))
        self.show_status(e)

    def select(self, indices):
        self.sel = set(indices)
        self.redraw()

    def on_press(self, e):
        self.canvas.focus_set()
        self.fx.pressed = False  # (Delete is for the notes now)
        if self.fx.sel:  # (the effect points selected aren't any more)
            self.fx.sel = set()
            self.fx.redraw()
        kept, self.box_kept, sel0 = self.kept_box(), None, set(self.sel)
        self.drop_drag()
        hit = self.hit(e.x, e.y)
        before = copy.deepcopy(self.tones)
        on_box = self.on_kept_box(kept, e, hit)
        if on_box and on_box != (0, 0):  # the kept Select box's side / corner: its notes stretch
            boxes, area = boxes_upright(kept)
            self.box_kept = (boxes, set(self.sel))
            self.drag = {"kind": "stretch", "side": on_box, "area": area, "boxes": boxes, "box": boxes,
                         "before": before, "orig": copy.deepcopy(self.tones), "name": tr("hz.step_stretch")}
            return self.redraw()
        dup = None
        if on_box and e.state & CTRL:  # Ctrl inside: a drag moves a COPY of all it selected (user); let go without
            dup = {"click": hit[1] if hit and hit[0] in ("note", "tune", "left", "right") else None}  # moving =
        if on_box:  # inside it: all it selected moves, held by the first note                    # a Ctrl+click
            hit = ("note", min(self.sel, key=lambda i: self.tones[i]["t"]))
        if hit is None:
            if e.x >= self.kb_w and e.y < self.ruler_h and self.preview_on.get():  # the bar numbers: the play line
                return self.put_play_line(self.beat_at(e.x))
            if e.x < self.kb_w or e.y < self.ruler_h:
                return
            if e.state & CTRL or self.tool.get() == "select":  # a box that selects the notes it touches
                add = e.state & CTRL and self.tool.get() == "select"  # (Ctrl: added to the selection and the
                base = set(self.sel) if add else set()  # boxes kept)
                self.sel = set(base)
                self.drag = {"kind": "box", "from": (e.x, e.y), "to": (e.x, e.y), "base": base,
                             "more": list(kept or []) if add else []}
                return self.redraw()
            if not self.can_place():
                return
            # a new note, there at once: it follows the mouse until the button is let go
            tone = {"t": self.snap(self.beat_at(e.x), e), "len": self.last_len, "key": self.key_at(e.y),
                    "cents": 0.0, "id": next_id(self.tones), "to": []}
            self.tones.append(tone)
            self.sel = {len(self.tones) - 1}
            self.drag = {"kind": "new", "i": len(self.tones) - 1, "before": before, "name": tr("hz.step_place")}
            self.sound(tone["key"])
        else:
            kind, i = hit[:2]
            if kind == "note" and e.state & CTRL and not dup:
                return self.select(self.sel ^ {i})
            if i not in self.sel:
                self.sel = {i}
            if kind == "note":  # (the next new note is as long as the one clicked)
                self.last_len = self.tones[i]["len"]
                self.sound([self.tones[j]["key"] for j in self.sel])
            self.drag = {"kind": kind, "i": i, "before": before, "beat": self.beat_at(e.x), "key": self.key_at(e.y),
                         "orig": copy.deepcopy(self.tones), "x": e.x, "y": e.y, "moved": False,
                         "slide": hit[2] if len(hit) > 2 else None,
                         "name": {"note": tr("hz.step_move"), "in": tr("hz.step_lead"), "out": tr("hz.step_lead"),
                                  "tune": tr("hz.step_tune")}.get(kind, tr("hz.step_length"))}
            if kind == "note" and kept and i in sel0:  # a note the kept box selected: the box goes along
                boxes = boxes_upright(kept)[0]
                self.drag.update(box=boxes, inside=bool(on_box))
                self.box_kept = (boxes, set(self.sel))
            if dup:
                self.drag.update(dup=dup, name=tr("hz.step_duplicate"))
        self.redraw()

    def on_double(self, e):
        """A double click on a note deletes it, when the button is let go with nothing changed (so a click and then
        a quick drag still moves it). With Select on empty space it pastes the copied notes there (user, like the
        main piano roll). Anywhere else, or with Ctrl, it's a press like any other."""
        hit = self.hit(e.x, e.y)
        if (self.tool.get() == "select" and hit is None and self.app.hz_clip and not e.state & CTRL
                and e.x >= self.kb_w and e.y >= self.ruler_h and not self.on_kept_box(self.kept_box(), e, hit)):
            self.drop_drag()
            return self.paste_notes(self.snap(self.beat_at(e.x), e))
        self.on_press(e)
        if self.drag and hit and hit[0] in ("note", "tune", "left", "right") and not e.state & CTRL:
            self.drag["double"] = True

    def on_drag(self, e):
        d = self.drag
        if not d:
            return
        if d["kind"] == "box":
            d["mouse"] = (e.x, e.y, e.state)
            self.box_to(d)
            if self.box_timer is None:
                self.box_scroll()
            return
        if d["kind"] == "stretch":
            return self.stretch_to(d, e)
        n = self.tones[d["i"]]
        beat, short = self.beat_at(e.x), self.shortest(e)
        if d["kind"] == "new":
            n["t"], n["key"] = self.snap(beat, e), self.key_at(e.y)
            self.sound(n["key"])
        elif d["kind"] == "right":
            n["len"] = max(short, self.snap(beat, e) - n["t"])
            self.keep_leads(d)
        elif d["kind"] == "left":
            end = n["t"] + n["len"]
            n["t"] = min(self.snap(beat, e), end - short)
            n["len"] = end - n["t"]
            self.keep_leads(d)
        elif d["kind"] == "out":
            d["slide"]["out"] = min(max(0.0, n["t"] + n["len"] - self.snap(beat, e)), n["len"])
        elif d["kind"] == "in":
            d["slide"]["in"] = min(max(0.0, self.snap(beat, e) - n["t"]), n["len"])
        elif d["kind"] == "tune":  # the note's own tune: whole cents, and it sticks to the exact tone (Shift = free)
            cents = d["orig"][d["i"]]["cents"] + (d["y"] - e.y) / self.sy * 100
            if e.state & SHIFT:
                cents = round(cents, 1)
            else:
                cents = 0.0 if abs(cents) * self.sy / 100 <= 5 * self.s else float(round(cents))
            n["cents"] = max(-TUNE, min(TUNE, cents))
        else:  # move every selected note: the one held goes to the grid line nearest to where it's dragged
            if not d["moved"] and abs(e.x - d["x"]) < 4 and abs(e.y - d["y"]) < 4:
                return
            d["moved"] = True
            if d.get("dup") and not d.get("copied"):
                self.copy_moved(d)
                n = self.tones[d["i"]]
            orig = d["orig"]
            held = orig[d["i"]]
            dt = 0.0 if abs(e.x - d["x"]) < 4 else self.snap(held["t"] + beat - d["beat"], e) - held["t"]
            dk = self.key_at(e.y) - d["key"]
            dt = max(dt, -min(orig[i]["t"] for i in self.sel))
            dk = max(-min(orig[i]["key"] for i in self.sel), min(127 - max(orig[i]["key"] for i in self.sel), dk))
            for i in self.sel:
                self.tones[i]["t"], self.tones[i]["key"] = orig[i]["t"] + dt, orig[i]["key"] + dk
            if d.get("box"):
                self.box_kept = ([(b0 + dt, top + dk, b1 + dt, bottom + dk) for b0, top, b1, bottom in d["box"]],
                                 set(self.sel))
            self.sound([self.tones[j]["key"] for j in self.sel])
        self.redraw()
        self.show_status(e)

    def copy_moved(self, d):
        """Ctrl+drag inside the kept Select box, the first move: copies of the selected notes are added (new ids;
        a slide between two of them is copied too) and selected, and they're what moves (the notes stay)."""
        old = sorted(self.sel)
        ids, first = {}, len(self.tones)
        for k, i in enumerate(old):
            ids[self.tones[i]["id"]] = next_id(self.tones) + k
        for i in old:
            n = copy.deepcopy(self.tones[i])
            n["id"] = ids[n["id"]]
            n["to"] = [dict(s, id=ids[s["id"]]) for s in n["to"] if s["id"] in ids]
            self.tones.append(n)
        d["i"] = first + old.index(d["i"])
        d["orig"] = copy.deepcopy(self.tones)
        d["copied"] = True
        self.sel = set(range(first, len(self.tones)))

    def copy_notes(self):
        """Ctrl+C: the effect points selected, else the notes selected (app.hz_clip, kept for any Hz bass). The
        last one copied is what Ctrl+V pastes."""
        if self.fx.copy_points():
            self.app.hz_clip = None
        elif self.sel:
            self.app.hz_clip = copy.deepcopy([self.tones[i] for i in sorted(self.sel)])
            self.fx.clip = None

    def play_line_beat(self):
        """Where Ctrl+V pastes notes: the preview's play line (preview on), else the main window's play line;
        on the grid line nearest it."""
        p = self.preview
        if self.preview_on.get() and p.ev is not None:
            beat = p.play_beat()
        else:
            sh = self.target()
            beat = self.app.playhead - (left_edge(sh) if sh is not None else self.app.hz_start or 0.0)
        sb = self.snap_beats()
        return max(0.0, round(beat / sb) * sb if sb else beat)

    def paste_notes(self, at):
        """The copied notes added with the first one starting at beat `at` (keys stay; new ids, a slide between
        two of them comes along) and selected: one undo step. False when there's nothing to paste."""
        clip = self.app.hz_clip
        if not clip or not self.can_place():
            return False
        before = copy.deepcopy(self.tones)
        start, base, first = min(n["t"] for n in clip), next_id(self.tones), len(self.tones)
        ids = {n["id"]: base + k for k, n in enumerate(clip)}
        for n in copy.deepcopy(clip):
            n["id"], n["t"] = ids[n["id"]], n["t"] + at - start
            n["to"] = [dict(s, id=ids[s["id"]]) for s in n["to"] if s["id"] in ids]
            self.tones.append(n)
        self.sel = set(range(first, len(self.tones)))
        self.commit(tr("hz.step_paste"), before)
        return True

    def keep_leads(self, d):
        """A note's end dragged: the dots of its slides stay where they were (as far as the note reaches)."""
        n, was = self.tones[d["i"]], d["orig"][d["i"]]
        for s, s0 in zip(n["to"], was["to"]):  # lead out: counted back from the note's end
            s["out"] = min(max(0.0, s0["out"] + (n["t"] + n["len"]) - (was["t"] + was["len"])), n["len"])
        for m, m0 in zip(self.tones, d["orig"]):  # lead in: counted from the note's start
            for s, s0 in zip(m["to"], m0["to"]):
                if s["id"] == n["id"]:
                    s["in"] = min(max(0.0, s0["in"] - (n["t"] - was["t"])), n["len"])

    def on_release(self, e):
        d = self.drag
        self.drop_drag()
        if not d:
            return
        if d["kind"] == "box":
            if self.box_timer:
                self.after_cancel(self.box_timer)
                self.box_timer = None
            if self.box_area(d) or d["more"] and e.state & CTRL:
                self.box_kept = (d["more"] + [self.box_area(d)] if self.box_area(d) else d["more"], set(self.sel))
            elif (not e.state & CTRL and self.tool.get() == "select"
                    and self.preview_on.get()):  # a click, not a drag: the play line goes there
                self.put_play_line(self.snap(self.beat_at(d["from"][0]), e))
            return self.redraw()
        if d.get("dup") and not d["moved"] and d["dup"]["click"] is not None:  # a Ctrl+click: in / out
            self.sel = set(self.sel) ^ {d["dup"]["click"]}
            return self.redraw()
        if d.get("double") and self.tones == d["before"]:
            self.sel = {d["i"]}
            return self.delete_selected()
        if d["kind"] == "note" and not d["moved"] and len(self.sel) > 1 and not d.get("inside"):
            self.sel = {d["i"]}  # one of several clicked without dragging: just that one
        if d["kind"] in ("left", "right"):
            self.last_len = self.tones[d["i"]]["len"]
        box = (self.box_kept or (None,))[0] if d.get("box") and (d["kind"] == "stretch" or d.get("inside")
                                                                  or d["moved"]) else None
        if self.tones != d["before"]:
            self.commit(d["name"], d["before"])
        if box:  # (the notes were sorted: the same ones, numbered anew)
            self.box_kept = (box, set(self.sel))
        self.redraw()

    def on_middle(self, e):
        """A middle click (not a drag: that moves the view) on a note marks one end of a slide (a full red dot); a
        second one on another note makes the slide between the two spots: it's theirs alone, whatever other notes
        and slides there are. On a dot of a slide: that slide goes. Anywhere else: the mark goes."""
        x, y = self.pan[:2]
        if abs(e.x - x) >= 4 or abs(e.y - y) >= 4 or not self.app.hz_line.get():
            return
        hit = self.hit(e.x, e.y)
        before = copy.deepcopy(self.tones)
        first = next((n for n in self.tones if self.pending and n["id"] == self.pending[0]), None)
        if hit and hit[0] in ("in", "out"):
            for n in self.tones:
                n["to"] = [s for s in n["to"] if s is not hit[2]]
            self.pending = None
        elif hit:
            n = self.tones[hit[1]]
            at = min(max(self.snap(self.beat_at(e.x), e), n["t"]), n["t"] + n["len"])
            (a, xa), (b, xb) = sorted([(first or n, self.pending[1] if first else at), (n, at)],
                                      key=lambda v: v[0]["t"])
            if first is None or first is n or not can_slide(a, b):  # the first spot (or a new first spot)
                self.pending = (n["id"], at)
                return self.redraw()
            s = self.link(a, b)
            if s is None:
                s = {"id": b["id"], "out": 0.0, "in": 0.0}
                a["to"].append(s)
            s["out"] = min(max(0.0, a["t"] + a["len"] - xa), a["len"])
            s["in"] = min(max(0.0, xb - b["t"]), b["len"])
            self.pending = None
        else:
            self.pending = None
        if self.tones != before:
            self.commit(tr("hz.step_lead"), before)
        else:
            self.redraw()

    def toggle_tool(self, e=None):
        """Double right click: Select <-> Pencil."""
        if self.menu_wait:
            self.after_cancel(self.menu_wait)
            self.menu_wait = None
        self.tool.set("pencil" if self.tool.get() == "select" else "select")
        if e is not None:
            self.on_motion(e)

    def on_menu(self, e):
        """Right click: slides between the selected notes (see pairs), or take them away. On a note (its red
        line): its tune, typed. On empty space the menu waits for the double click time first (a double right
        click switches the tool), and there's none when there's nothing to pick."""
        hit = self.hit(e.x, e.y)
        kept = self.kept_box() if len(self.sel) >= 2 and not (hit and hit[0] in ("in", "out")) else None
        # inside the kept Select boxes: the menu for all they selected
        if kept and boxes_side([self.box_rect(a) for a in kept], e.x, e.y, 0) == (0, 0):
            if hit:
                return self.show_box_menu(e)
            if self.menu_wait:  # (empty space: a double right click still switches the tool)
                self.after_cancel(self.menu_wait)
            self.menu_wait = self.after(DOUBLE_MS, lambda: self.show_box_menu(e))
            return
        if not hit:
            if self.menu_wait:
                self.after_cancel(self.menu_wait)
            self.menu_wait = self.after(DOUBLE_MS, lambda: self.show_menu(e, None)) if self.pairs() else None
            return
        self.show_menu(e, hit)

    def show_box_menu(self, e):
        """Right-click inside the kept Select boxes with several notes selected: only what works on all of them at
        once (user asked): tune, own Auto threshold, slides, delete."""
        self.menu_wait = None
        if len(self.sel) < 2:
            return
        first = min(self.sel)
        hz = (self.target() or {}).get("hz") or {}
        pairs = self.pairs()
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("hz.box_selected", n=len(self.sel)), state="disabled")
        menu.add_separator()
        menu.add_command(label=tr("hz.box_tune"), command=lambda: self.type_tune(first))
        if hz.get("auto") is not None:
            menu.add_command(label=tr("hz.box_auto"), command=lambda: self.type_auto(first))
            if any("auto" in self.tones[j] for j in self.sel):
                menu.add_command(label=tr("hz.auto_shared", cents=f"{hz['auto']:g}"),
                                 command=lambda: self.set_auto(first, None))
        if pairs and all(self.link(a, b) for a, b in pairs):
            menu.add_command(label=tr("hz.slide_remove"), command=lambda: self.set_slide(False))
        else:
            menu.add_command(label=tr("hz.slide_add"), command=lambda: self.set_slide(True),
                             state="normal" if pairs else "disabled")
        menu.add_separator()
        menu.add_command(label=tr("hz.box_delete", n=len(self.sel)), accelerator="Del", command=self.delete_selected)
        menu.tk_popup(e.x_root, e.y_root)

    def show_menu(self, e, hit):
        self.menu_wait = None
        pairs = self.pairs()
        menu = tk.Menu(self, tearoff=0)
        if hit and hit[0] not in ("in", "out"):
            menu.add_command(label=tr("hz.tune_type", cents=f"{self.tones[hit[1]]['cents']:+g}"),
                             command=lambda: self.type_tune(hit[1]))
            hz = (self.target() or {}).get("hz") or {}
            if hz.get("auto") is not None:  # Auto gates: the note's own threshold
                n = self.tones[hit[1]]
                menu.add_command(label=tr("hz.auto_own", cents=f"{n.get('auto', hz['auto']):g}"),
                                 command=lambda: self.type_auto(hit[1]))
                picked = self.sel if hit[1] in self.sel else {hit[1]}
                if any("auto" in self.tones[j] for j in picked):
                    menu.add_command(label=tr("hz.auto_shared", cents=f"{hz['auto']:g}"),
                                     command=lambda: self.set_auto(hit[1], None))
            menu.add_separator()
        if pairs and all(self.link(a, b) for a, b in pairs):
            menu.add_command(label=tr("hz.slide_remove"), command=lambda: self.set_slide(False))
        else:
            menu.add_command(label=tr("hz.slide_add"), command=lambda: self.set_slide(True),
                             state="normal" if pairs else "disabled")
        menu.tk_popup(e.x_root, e.y_root)

    def type_tune(self, i):
        """A note's own tune typed in cents (the selected notes get it too when it's one of them)."""
        cents = simpledialog.askfloat(tr("hz.window_title"), tr("hz.tune_ask", most=f"{TUNE:g}"), parent=self,
                                      initialvalue=self.tones[i]["cents"], minvalue=-TUNE, maxvalue=TUNE)
        if cents is None or i >= len(self.tones):
            return
        before = copy.deepcopy(self.tones)
        for j in self.sel if i in self.sel else {i}:
            self.tones[j]["cents"] = float(cents)
        if self.tones != before:
            self.commit(tr("hz.step_tune"), before)

    def type_auto(self, i):
        """A note's own Auto gates threshold typed in cents (the selected notes get it too when it's one of them)."""
        hz = (self.target() or {}).get("hz") or {}
        if hz.get("auto") is None:
            return
        cents = simpledialog.askfloat(tr("hz.window_title"), tr("hz.auto_ask", most=f"{AUTO_MOST:g}"), parent=self,
                                      initialvalue=self.tones[i].get("auto", hz["auto"]), minvalue=0.0,
                                      maxvalue=AUTO_MOST)
        if cents is not None:
            self.set_auto(i, float(cents))

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

    def set_slide(self, on):
        """Slides between the selected notes (see pairs): each a quarter of its two notes long to start with (its
        dots can then be dragged); slides that are there stay as they are. Off = those slides go."""
        before = copy.deepcopy(self.tones)
        for a, b in self.pairs():
            if not on:
                a["to"] = [s for s in a["to"] if s["id"] != b["id"]]
            elif not self.link(a, b):
                a["to"].append({"id": b["id"], "out": a["len"] / 4, "in": b["len"] / 4})
        if self.tones != before:
            self.commit(tr("hz.step_lead"), before)

    def delete_selected(self):
        if self.sel:
            before = copy.deepcopy(self.tones)
            self.tones = [n for i, n in enumerate(self.tones) if i not in self.sel]
            left = {n["id"] for n in self.tones}
            for n in self.tones:  # (the slides to the deleted notes go with them)
                n["to"] = [s for s in n["to"] if s["id"] in left]
            self.sel = set()
            self.commit(tr("hz.step_delete"), before)

    def on_grow(self):
        sh = self.target()
        if sh is None:
            return
        if not self.tones:  # (nothing placed yet: just how it'll be when there is)
            return self.redraw()
        self.commit(tr("hz.grow"), copy.deepcopy(self.tones))

    def auto_limit(self):
        """The threshold box's cents, or None when it doesn't hold a number from 0 to AUTO_MOST (it turns red)."""
        try:
            limit = float(calc(self.auto_var.get()))
            if 0 <= limit <= AUTO_MOST:
                self.auto_row.entry.config(style="TEntry")
                return limit
        except (ValueError, ZeroDivisionError):
            pass
        self.auto_row.entry.config(style="Bad.TEntry")
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
        """The gates dropdown: Mixed, Fixed or Auto for the Hz bass shown (one undo step), or for the one to be
        made."""
        sh = self.target()
        if sh is not None and sh.get("hz"):
            mode = GATE_MODES[self.gates.current()]
            self.app.set_hz_gates(mode, self.fixed().get("auto"))
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

    def pitch(self):
        """The Pitch box in cents, or None (it turns red) when it isn't a number from -1200 to 1200."""
        try:
            cents = float(calc(self.pitch_var.get()))
            if abs(cents) > 1200:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            self.pitch_entry.config(style="Bad.TEntry")
            return None
        self.pitch_entry.config(style="TEntry")
        return cents

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

    def layout(self):
        """The toolbar in one row, or two when the window is too narrow: the pieces keep their order, the ones that
        don't fit go to the second row (the right-hand piece stays at the right)."""
        if not self.winfo_exists():
            return
        room = self.bar.winfo_width() - 16
        left = [f for f, side in self.pieces if side == "left"]
        right = [f for f, side in self.pieces if side == "right"]
        widths = {f: f.winfo_reqwidth() for f, side in self.pieces}
        if room <= 1 or sum(widths.values()) <= room:
            laid = {f: 0 for f in widths}
        else:
            laid, used = {}, 0
            for f in left:
                used += widths[f]
                laid[f] = 0 if used <= room and all(v == 0 for v in laid.values()) else 1
            laid.update({f: 1 for f in right})
        laid = tuple(laid[f] for f, side in self.pieces)
        if laid == self.laid:
            return
        self.laid = laid
        for f, side in self.pieces:
            f.pack_forget()
        for right_first in ("right", "left"):  # (packed first = gets its room first: the hint at the end is the one
            for (f, side), row in zip(self.pieces, laid):  # cut off when even two rows are too narrow)
                if side == right_first:
                    f.pack(in_=self.rows[row], side=side)
        self.rows[0].pack(fill="x")
        if 1 in laid:
            self.rows[1].pack(fill="x", pady=(4, 0))
        else:
            self.rows[1].pack_forget()

    # ------------------------------------------------------------ into the shape

    def commit_fx(self, before):
        """The effects' lines changed: one undo step. before = (lines, repeats, switched off) to go back to if it's
        called off."""
        self.commit(tr("hz.step_fx"), copy.deepcopy(self.tones), before)

    def commit(self, name, before, before_fx=None):
        """The notes (and effects' lines) here become the shape's: one undo step of the main window. before = the
        tones (and before_fx the lines) to go back to if it's called off (too many notes)."""
        app = self.app
        picked = [self.tones[i] for i in self.sel if i < len(self.tones)]
        self.tones.sort(key=lambda n: (n["t"], n["key"]))
        self.sel = {i for i, n in enumerate(self.tones) if any(n is p for p in picked)}
        tones = clean_tones(copy.deepcopy(self.tones))
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
        sh = self.target()
        bpm = app.current_bpm()
        if sh is None:
            if not tones or app.hz_start is None:
                return self.redraw()
            lo, hi = app.hz_defaults["lo"], app.hz_defaults["hi"]
            new = dict(app.defaults, kind="custom", name=tr("hz.name"), strokes=[copy.deepcopy(BOX_STROKE)],
                       **custom_settings(app.custom_defaults))
            new.update(fill="spam", pts=box_frame(app.hz_start, lo, app.hz_start + tones_span(tones), hi),
                       hz=dict(self.new_hz(bpm), tones=copy.deepcopy(tones), grow=True, own=True,
                               **copy.deepcopy(fx)))  # (its own copy)
            if not app.confirm_big([new]):
                return self.call_off(before, before_fx)
            app.hz_start = None
            self.tones = tones  # (so the selection stays when the main window's selection changes to the new shape)
            app.add_shape(new)
        else:
            hz = dict(sh.get("hz") or dict(HZ_DEFAULTS, bpm=float(bpm or 120), **self.fixed()))
            for k in ("tones", "grow", "fx", "loop", "off", "amount"):
                hz.pop(k, None)
            new = copy.deepcopy(sh)
            if tones:
                new["hz"] = dict(hz, tones=tones, **({"grow": True} if self.grow.get() else {}), **copy.deepcopy(fx))
                if new["fill"] not in SPAM_FILLS:
                    new["fill"] = "spam"
                if self.grow.get():
                    fit_length(new)
                if not app.confirm_big([new]):
                    return self.call_off(before, before_fx)
            app.push_undo(name=name)
            if tones or not hz_made(sh):
                if not tones:  # the last note deleted from a shape of its own: back to its one tone
                    new["hz"] = hz
                sh.clear()
                sh.update(new)
            else:  # ... from a Hz bass made here: it goes, a new one can start at the same spot
                app.hz_start = left_edge(sh)
                del app.shapes[app.sel]
                self.tones = []
                app.select(None)
            app.shapes_changed()
            app.sync_custom()
            app.schedule_autosave()
        self.sync()

    def call_off(self, before, before_fx=None):
        self.tones, self.sel = before, set()
        if before_fx is not None:
            self.fxl, self.loops, self.off = before_fx
        self.redraw()

    # ------------------------------------------------------------ hearing the key held

    def sound(self, keys):
        """The keys of the notes held with the mouse (one key, or a list when several selected notes are moved)
        sound on the MIDI-out device; when they change, all start again (None = let go: notes off)."""
        app = self.app
        keys = None if keys is None else tuple(sorted({keys} if isinstance(keys, int) else set(keys)))
        if self.sounding is not None and self.sounding[1] != keys:
            for k in self.sounding[1]:
                app.out.note(self.sounding[0], k, 0)
            self.sounding = None
        if not keys or self.sounding is not None:
            return
        if not app.out.handle and app.out.open(app.midi_device.get()):
            return  # (no device: silent)
        sh, ch, vel = self.target(), 0, app.defaults["vel0"]
        if sh is not None:  # the shape's own channel and velocity
            vel = sh.get("vel0", vel)
            mine = app.rendered[app.rendered[:, 5] == app.sel] if len(app.rendered) else ()
            if len(mine):
                ch = slot_track_channel(int(mine[0, 4]))[1]
        self.sounding = (ch, keys)
        for k in keys:
            app.out.note(ch, k, max(1, min(127, int(vel))))

    def drop_drag(self):
        self.drag = None
        self.sound(None)

    def remember(self, e):
        if e.widget is self:
            self.app.hz_pos = self.geometry()

    # ------------------------------------------------------------ the preview (hz_preview.py)

    def preview_again(self):
        """At opening: the preview was on last time. On again if its soundfont is still there (nothing asked)."""
        if self.winfo_exists() and os.path.isfile(self.app.hz_preview["font"]):
            self.preview_on.set(True)
            self.on_preview()

    def on_preview(self):
        """The Preview toggle. The first time (no soundfont yet, or it's gone) it asks for one."""
        cfg = self.app.hz_preview
        if not self.preview_on.get():
            cfg["on"] = False
            self.preview.stop()
        else:
            if not os.path.isfile(cfg["font"]):
                path = filedialog.askopenfilename(
                    parent=self, title=tr("hz.preview_pick_font"),
                    filetypes=[(tr("hz.preview_fonts"), "*.sf2 *.sf3 *.sfz *.sf2pack"), (tr("hz.preview_all"), "*.*")])
                if not path:
                    self.preview_on.set(False)
                    return
                cfg["font"] = os.path.normpath(path)
            err = self.preview.start()
            if err:
                self.preview_failed(err)
                return
            cfg["on"] = True
        self.app.schedule_autosave()
        self.redraw()
        self.canvas.focus_set()  # (so Space plays)

    def preview_failed(self, err):
        """The synth or the soundfont didn't work: the preview goes off and says why."""
        self.preview_on.set(False)
        self.app.hz_preview["on"] = False
        self.preview.stop()
        messagebox.showerror(tr("hz.window_title"), err, parent=self)

    def on_space(self, e):
        """Space: the preview plays / stops (preview off: nothing; the main piano roll only plays from its own
        window)."""
        if not self.preview_on.get():
            return "break"
        if self.preview.playing():
            self.preview.stop_play()
        else:
            if self.app.player.running:
                self.app.stop_play()
            err = self.preview.play()
            if err:
                messagebox.showerror(tr("hz.window_title"), err, parent=self)
        self.draw_preview()
        return "break"

    def put_play_line(self, beat):
        """A click on the bar numbers (preview on): the play line goes there (playing: plays on from there)."""
        self.preview.put_line(max(0.0, beat))
        self.draw_preview()

    def draw_preview(self):
        """The grey over what isn't made yet, the play line, and the words next to the toggle. The canvas is only
        changed when what it shows changed."""
        c, p = self.canvas, self.preview
        on = self.preview_on.get()
        grey = p.grey() if on else []
        line = p.play_beat() if on and p.ev is not None else None
        if line is not None and p.playing():  # (playing past the right edge: the next page)
            x, w = self.x_of(line), c.winfo_width()
            if x > w - 6 * self.s or x < self.kb_w:
                self.t0 = line
                self.clamp_view()
                return self.redraw()
        shown = (tuple((round(self.x_of(a)), round(self.x_of(b))) for a, b in grey),
                 None if line is None else round(self.x_of(line)))
        if shown != p.shown:
            p.shown = shown
            c.delete("preview")
            w, h, rh = c.winfo_width(), c.winfo_height(), self.ruler_h
            for a, b in shown[0]:
                a, b = max(a, self.kb_w), min(b, w)
                if b > a:
                    c.create_rectangle(a, rh, b, h, fill=GREY, outline="", stipple="gray50", tags="preview")
            if shown[1] is not None and self.kb_w <= shown[1] <= w:
                c.create_line(shown[1], rh, shown[1], h, fill=PLAY_LINE, width=max(1, round(self.s)),
                              tags="preview")
            if c.find_withtag("frame"):
                c.tag_lower("preview", "frame")
        says, colour = "", "#555"
        if on:
            vo = tr("hz.preview_voices", used=f"{p.voices_used:,}", limit=f"{self.app.hz_preview['voices']:,}")
            if p.loading():
                says, colour = tr("hz.preview_loading"), ORANGE
            elif p.just_loaded():
                says, colour = tr("hz.preview_loaded"), GREEN
            elif p.ev is None:
                says = tr("hz.preview_nothing")
            elif grey:
                says = tr("hz.preview_making", speed=f"{p.speed:.1f}" if p.speed else "…", voices=vo)
            else:
                says = tr("hz.preview_ready", voices=vo)
        if (self.preview_says.cget("text"), str(self.preview_says.cget("foreground"))) != (says, colour):
            self.preview_says.config(text=says, foreground=colour)
        if self.settings_window:
            self.settings_window.refresh()

    def close(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        if self.fx.asking:  # (the Repeat every… window)
            self.fx.asking.destroy()
        for job in (self.box_timer, self.menu_wait):
            if job:
                self.after_cancel(job)
        self.preview.stop()
        self.sound(None)
        self.app.pvar["ppq"].trace_remove("write", self.ppq_trace)
        self.app.hz_window = None
        self.destroy()
        self.app.roll.focus_set()
