"""The Hz bass window (hzbass.py): a small piano roll where the tones of a Hz bass are placed. Pressing the mouse
places a note at once, and it follows the mouse (snapped to the nearest grid line; Shift = not) until the button is let go; a note that's
there is moved the same way, either end changes its length, Ctrl+drag (or a drag with Select) selects with a box, Delete removes the
selected ones, a double click removes the note under it. The key of the note held with the mouse sounds on the MIDI-out device. The window has its own snap.
Under the notes: the effects pane (hz_effects.py), one line for each effect over all the notes.
The red line is the tone travelling through the notes: it jumps at the next note unless its dots are dragged (lead
out of one note, lead in of the next), then it slides.

It follows the main window's selection: the selected custom shape's tones, or (Hz bass tool clicked on empty
space) a new Hz bass that's made with the first note and grows with the notes. Every change is an undo step of the
main window, made when the mouse is let go (the notes on the piano roll are made again then, not while dragging).

This file opens the window, lays it out and follows the selection / undo; the rest is in its parts: hz_view.py
(zoom, scrolling, drawing), hz_mouse.py (mouse and keys on the notes), hz_gates.py (gates, tune, toggles, saving
into the shape), hz_sound.py (the key held, the preview)."""

import os
import re
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from files.about import ICONS
from files.lang import tr
from files.mathexpr import fmt
from notes.hzbass import AUTO, AUTO_MOST, FX, clean_tones, left_edge
from roll.roll_shared import grab_while_panning
from roll.zoombar import add_zoom_bars
from window import look
from window.hz_effects import FxPane
from window.hz_live import LiveKeys
from window.hz_preview import Preview
from window.hz_synth import open_synth
from window.preview_settings import open_preview_settings
from window.snap_picker import SnapPicker
from window.widgets import Scrub, Tooltip, placed
# the window is made of these parts (each a mixin of HzWindow); shape_length / DOUBLE_MS are handed on from here
from window.hz_view import RED, HzView, shape_length  # noqa: F401
from window.hz_sound import HzSound
from window.hz_gates import GATE_MODES, HzGates, gate_mode, hz_keys, hz_made
from window.hz_mouse import DOUBLE_MS, HzMouse  # noqa: F401

POS = r"\d+x\d+\+-?\d+\+-?\d+"  # a remembered size and place


def open_hz(app):
    if app.hz_window:
        app.hz_window.lift()
    else:
        app.hz_window = HzWindow(app)
    app.hz_window.chosen = app.selected()  # (opened for it: its notes can be placed even before it's a Hz bass)
    app.hz_window.sync()
    w = app.hz_window  # it takes the keyboard (user: keys pressed right after went to the piano roll behind)
    w.after_idle(lambda: w.winfo_exists() and w.canvas.focus_force())


def no_spaces(var):
    """A box's spaces taken out (Space types one there, user; numbers have none) when the box is left."""
    if " " in var.get():
        var.set(var.get().replace(" ", ""))


def auto_box(app, parent, var, apply):
    """The Auto gates threshold box ("within [3] cents"): a frame (not packed) with .entry. apply() on Enter,
    leaving the box, and each step of the number."""
    f = ttk.Frame(parent)
    lb = ttk.Label(f, text=tr("hz.auto_within"))
    lb.pack(side="left")
    f.entry = ttk.Entry(f, textvariable=var, width=4)
    f.entry.pack(side="left", padx=(4, 2))
    ttk.Label(f, text=tr("panel_custom.hz_cents"), foreground=look.HINT).pack(side="left", padx=(0, 4))
    f.entry.bind("<Return>", lambda e: apply())
    f.entry.bind("<FocusOut>", lambda e: no_spaces(var) or apply())
    Scrub(app, [(f.entry, var, apply)], (0.5, 5, 0.1), 0, AUTO_MOST, label=lb)
    for w in (lb, f.entry):
        Tooltip(w, tr("hz.auto_tip", most=f"{AUTO_MOST:g}"))
    return f


class HzWindow(HzMouse, HzGates, HzSound, HzView, tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("hz.window_title"))
        self.transient(app)
        s = self.s = app.scale
        self.geometry(placed(self, app.hz_pos) if re.fullmatch(POS, app.hz_pos or "") else f"{round(820 * s)}x{round(700 * s)}")
        self.minsize(round(420 * s), round(260 * s))
        self.tones, self.sel = [], set()  # the notes shown (hzbass tones) and which are selected
        self.drag = None
        self.chosen = None  # the shape the window was opened for (open_hz), until another one is selected
        self.chosen_at = None  # (its number while undo / redo puts the shapes back: before_restore)
        self.press_was = (set(), None)  # (the selection, the kept Select boxes) at the last press
        self.box_kept = None  # ([box_area, ...], selection) of the last Select boxes, shown after letting go
        self.sel_before = None  # (tones before an edit, sel_state() from then): what its undo step keeps (commit)
        self.pushing = None  # (the sel_state() of the undo step being made, commit)
        self.own_step = False  # (an undo step being made here: App.push_undo marks it as this window's whatever has
        # the keyboard; a dropdown's list or a wheel over an unfocused window left it marked "elsewhere", user)
        self.box_timer = None  # (box_scroll)
        self.pending = None  # (tone id, beats from its start): the first middle click of a slide, waiting for the
        # second (it moves with its note, user; mark_beat)
        self.shown = None  # id() of the shape shown (another one: the pending mark goes)
        self.sounding = None  # (channel, what's played) heard now: the notes held with the mouse (see sound)
        self.sound_jobs, self.sound_on = [], set()  # (the notes still to start / stop, the keys on now)
        self.last_len = 1.0  # beats: how long a newly placed note is (the last length used)
        self.placed = None  # the id of the note the last click placed (a double click there places one more)
        self.fxl, self.fx_of = {}, None  # the effects' lines (hz["fx"]) and whose they are (the shape, or None)
        self.loops = {}  # the effects that repeat (hz["loop"])
        self.off = []  # the effects switched off (hz["off"])
        self.froms, self.fits = {}, []  # repeating effects counted from each note (hz["from"]), stretched (hz["fit"])
        self.sustains = {}  # ... their sustain points (hz["sustain"])
        self.lfo = {}  # the synth window's settings for vibrato / tremolo (hz["lfo"])
        self.extra = {}  # ... its own settings, not lines (hzbass.EXTRAS: Voice box, wave mode, rack, arpeggio)
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
        ttk.Label(f, text=tr("panel_custom.hz_cents"), foreground=look.HINT).pack(side="left", padx=(0, 10))
        for w in (lb, self.pitch_entry):
            Tooltip(w, tr("panel_custom.hz_cents_tip"))
        self.pitch_entry.bind("<Return>", lambda e: self.on_pitch())
        self.pitch_entry.bind("<FocusOut>", lambda e: no_spaces(self.pitch_var) or self.on_pitch())
        Scrub(app, [(self.pitch_entry, self.pitch_var, self.on_pitch)], (1, 10, 0.1), -1200, 1200, label=lb)
        f = piece()
        ttk.Label(f, text=tr("hz.gates")).pack(side="left")
        names = [tr("panel_custom.hz_" + m) for m in GATE_MODES]
        self.gates = ttk.Combobox(f, values=names, state="readonly", width=max(map(len, names)))
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
        ppq.bind("<FocusOut>", lambda e: no_spaces(app.pvar["ppq"]))
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
        self.preview_says = ttk.Label(f, text="", foreground=look.INFO)
        self.preview_says.pack(side="left", padx=(6, 10))
        b = ttk.Button(f, text=tr("hz.synth"), command=lambda: open_synth(self), takefocus=False)
        b.pack(side="left", padx=(0, 10))
        Tooltip(b, tr("hz.synth_tip"))
        self.settings_window = None  # Preview settings… (preview_settings.py)
        self.synth_win = None  # the synth window (hz_synth.py)
        f = piece()
        self.what = ttk.Label(f, text="", foreground=look.INFO)
        self.what.pack(side="left", padx=(0, 10))
        # the BPM changed since the Hz bass was made: said in red in the shape's place, with the button (user)
        self.stale = ttk.Label(f, text=tr("hz.stale"), foreground=RED)
        self.stale_btn = ttk.Button(f, text=tr("panel_custom.hz_update"), command=app.update_hz, takefocus=False)
        self.stale_shown = False
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
        self.status = ttk.Label(self, text="", foreground=look.INFO, padding=(8, 2, 8, 4))
        self.status.pack(side="bottom", fill="x")
        self.said_until = 0.0  # (say: a message stays until then)
        self.fx = FxPane(self)
        self.notes_box = tk.Frame(self)  # the notes with the main piano roll's scrollbars (zoombar.py)
        self.notes_box.pack(fill="both", expand=True)
        c = self.canvas = tk.Canvas(self.notes_box, background=look.HZ_BG, highlightthickness=0, takefocus=True)
        self.scale, self.bars = s, ()
        add_zoom_bars(self.notes_box, self, c)
        self.on_fx()
        self.pencil = ("@" + os.path.join(ICONS, "pencil.cur").replace("\\", "/"),)  # its tip is the spot pointed at
        try:
            c.config(cursor=self.pencil)
        except tk.TclError:  # (the file can't be read: the built-in one)
            self.pencil = "pencil"
        c.config(cursor="")
        c.bind("<Configure>", self.on_resize)
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-2>", self.pan_start)
        c.bind("<B2-Motion>", self.pan_move)
        c.bind("<ButtonRelease-2>", self.on_middle)
        grab_while_panning(c)
        c.bind("<ButtonPress-3>", self.on_menu)
        c.bind("<Double-Button-3>", self.toggle_tool)
        self.menu_wait = None  # a right click on empty space: its menu, waiting to see if it's a double click
        c.bind("<Motion>", self.on_motion)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Delete>", lambda e: self.on_delete())
        for k in ("<Control-c>", "<Control-C>"):  # (effect points selected: they're copied; else the notes)
            c.bind(k, lambda e: self.copy_notes() or "break")
        for k in ("<Control-v>", "<Control-V>"):  # (what was copied last: effect points, or notes at the play line;
            c.bind(k, lambda e: (self.drag or self.fx.paste_points()  # nothing while the mouse is held)
                                 or self.paste_notes(self.play_line_beat()), "break")[1])
        c.bind("<Escape>", lambda e: self.on_escape())
        self.bind("<space>", self.on_space)  # (anywhere in the window: the buttons don't take the keyboard)
        for k in ("<Control-a>", "<Control-A>"):
            c.bind(k, lambda e: (self.drag or self.select(range(len(self.tones))), "break")[1])
        for k, tool in (("p", "pencil"), ("P", "pencil"), ("v", "select"), ("V", "select")):
            c.bind(f"<KeyPress-{k}>", lambda e, tool=tool: self.tool.set(tool) or self.on_motion(e) or "break")
        self.bind("<Configure>", self.remember)
        self.protocol("WM_DELETE_WINDOW", self.close)
        c.focus_set()
        self.preview = Preview(self)
        self.live = LiveKeys(self)
        if app.hz_preview["on"]:  # (on last time: on again, if its soundfont is still there)
            self.after_idle(self.preview_again)

    # ------------------------------------------------------------ what it shows

    def target(self):
        """The shape whose tones are shown: the one selected custom shape (not text or pasted notes) when it's a Hz
        bass, or the one the window was opened for (a spam shape's Notes… button: self.chosen), or None. Any other
        custom shape is left alone (user: one stray click turned it into a Hz bass)."""
        app = self.app
        sh = app.selected()
        if (sh and len(app.sels) == 1 and sh["kind"] == "custom" and not sh.get("text") and "notes" not in sh
                and (sh.get("hz") or sh is self.chosen)):
            return sh
        return None

    def can_place(self):
        return self.target() is not None or self.app.hz_start is not None

    def sync(self):
        """The main window's selection or shapes changed (undo too): show what's there now."""
        picked = self.app.selected()
        if self.chosen is not None and picked is not self.chosen:  # another shape selected: no longer opened for it
            # (undo / redo put the same shape back as a new copy: still the one)
            self.chosen = picked if self.chosen_at is not None and self.app.sel == self.chosen_at else None
        sh = self.target()
        hz = (sh or {}).get("hz") or {}
        tones = clean_tones(hz.get("tones"))
        other = (id(sh) if sh is not None else None) != self.shown
        if other:  # another Hz bass: a slide's first mark goes (user)
            self.shown, self.pending = (id(sh) if sh is not None else None), None
        if tones != self.tones:
            self.tones, self.sel = tones, set()
            self.drop_drag()
        if sh is not None or self.fx_of is not None:  # (no Hz bass yet: the lines picked stay for the first note)
            self.set_fx(hz)
            self.fx_of = id(sh) if sh is not None else None
        if sh is None:
            text = (tr("hz.hint_new", beat=fmt(self.app.hz_start + 1)) if self.app.hz_start is not None
                    else tr("hz.hint_none"))
            self.grow.set(True)
        else:
            text = tr("hz.shape", name=self.app.shape_label(sh))
            if tones:
                self.grow.set(bool(hz.get("grow")))
            elif other:  # (no notes yet: ticked by hand stays ticked until the first note, whatever else changes)
                self.grow.set(hz_made(sh))
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
        self.show_stale()
        self.after_idle(self.layout)  # (its width changed)
        self.grow_box.config(state="normal" if sh is not None else "disabled")
        if self.tones and not self.fitted:
            self.fit_view()
        self.redraw()

    def show_stale(self):
        """The red "BPM changed" text and Update button instead of the shape's name, while the shape shown was made
        for another BPM than the project's now (its tone is off: the red line shows it)."""
        hz, bpm = ((self.target() or {}).get("hz") or {}), self.app.current_bpm()
        stale = bool(hz) and bpm is not None and abs(hz["bpm"] - bpm) > 1e-9
        if stale == self.stale_shown:
            return
        self.stale_shown = stale
        for w in (self.what, self.stale, self.stale_btn):
            w.pack_forget()
        if stale:  # (the button packed first: when the bar is too narrow, the text is cut, not the button)
            self.stale_btn.pack(side="right", padx=(0, 10))
            self.stale.pack(side="left", padx=(0, 6))
        else:
            self.what.pack(side="left", padx=(0, 10))
        self.after_idle(self.layout)

    def before_restore(self):
        """Undo / redo is about to change the shapes: what's shown now (for after_restore)."""
        app, sh = self.app, self.target()
        self.chosen_at = app.sel if self.chosen is not None and self.chosen is app.selected() else None
        if sh is not None and hz_made(sh):
            return "shape", left_edge(sh), hz_keys(sh)
        if sh is None and app.hz_start is not None:
            return "start", len(app.shapes)
        return None

    def after_restore(self, was):
        """Undo / redo changed the shapes. The Hz bass shown was taken back whole: its start spot is back, so notes
        can be placed again. A Hz bass came back on the start spot: it's the one shown again. (Gone = no Hz bass
        there any more, not "not selected": the selection is kept by number, so when undo puts a shape back in front
        of it, another shape is selected.)"""
        app = self.app
        if was and was[0] == "shape" and self.target() is None and not any(
                hz_made(s) and abs(left_edge(s) - was[1]) < 1e-9 and hz_keys(s) == was[2] for s in app.shapes):
            app.hz_start, app.hz_defaults = was[1], was[2]
            app.roll.request_redraw()
        elif was and was[0] == "start" and len(app.shapes) > was[1]:
            last = app.shapes[-1]
            if hz_made(last) and abs(left_edge(last) - app.hz_start) < 1e-9:
                app.hz_start = None
                app.select(len(app.shapes) - 1)
        self.sync()
        self.chosen_at = None

    # ------------------------------------------------------------ view

    # ------------------------------------------------------------ scrollbars (zoombar.py, like the main piano roll's)
    # Across they count beats from the view's leftmost spot (-0.25), up and down keys from the top (127).

    # ------------------------------------------------------------ drawing

    # ------------------------------------------------------------ mouse

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

    # ------------------------------------------------------------ hearing the key held

    # ------------------------------------------------------------ the preview (hz_preview.py)

    def close(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        if self.fx.asking:  # (the Repeat every… window)
            self.fx.asking.destroy()
        if self.synth_win:
            self.synth_win.close()
        for job in (self.box_timer, self.menu_wait):
            if job:
                self.after_cancel(job)
        self.preview.stop()
        self.live.stop()
        self.sound(None)
        self.app.pvar["ppq"].trace_remove("write", self.ppq_trace)
        self.app.hz_window = None
        if self.target() is None and self.app.hz_start is not None:  # closed with no notes: the start mark goes
            self.app.hz_start = None  # (no undo step: picking the spot wasn't one either, user)
            self.app.roll.request_redraw()
        self.destroy()
        self.app.roll.focus_set()
