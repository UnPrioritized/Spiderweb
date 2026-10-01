"""Main window: toolbar, side panel, shapes, playback and undo.
Its other parts: panel_custom.py / panel_funnel.py / panel_tumour.py (+ tumour_window.py) / panel_text.py /
panel_pattern.py (those panel sections),
project.py (files, autosave, MIDI export), widgets.py (tooltips), font_dialog.py (picking a font)."""

import copy
import json
import math
import os
import time
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np

from files.lang import tr
from notes.custom import CUSTOM_DEFAULTS, custom_note_count, tracks_apart
from window.help import Tips, open_help
from window.updates import Updates
from window.help_texts import BY_ID, TOOL_TOPICS
from notes.engine import (KINDS, NO_NOTES, SHAPE_DEFAULTS, point_names, render, shape_notes_tracks, with_claw,
                          slot_track_channel)
from notes.funnel import FUNNEL_DEFAULTS, funnel_note_count, inside_out, turned_curve
from notes.pattern import moved_formulas
from notes.paths import KEYS
from notes.polygon import POLYGON_DEFAULTS
from notes.smooth import SMOOTH_DEFAULT
from notes.text import TEXT_DEFAULTS
from files.mathexpr import calc, calc_int, fmt
from window.panel_custom import GAP_COLOR, CustomPanel
from window.panel_freehand import FreehandPanel
from window.panel_funnel import FunnelPanel
from window.panel_pattern import PatternPanel
from window.panel_polygon import PolygonPanel
from window.panel_text import TextPanel
from window.claw_window import open_claw
from window.hz_preview import clean_settings as clean_preview
from window.panel_tumour import TumourPanel
from notes.joined import all_tumours, is_joined
from window.join_split import JoinSplit
from window.history import HistoryPanel, edit_name
from roll.pianoroll import PianoRoll
from files import errors
from files.about import ICONS, VERSION
from files.playback import DEFAULT_DEVICE, MidiOut, Player, devices
from files.midi_out import PPQ_WARN
from files.domino_clip import DOMINO_STARTS
from files.project import AUTOSAVE, OUTPUT_DIR, ProjectFiles
from files.snap import DEFAULT_SNAP, snap_beats
from roll.roll_shared import cached_path
from roll.zoombar import add_zoom_bars
from window.snap_picker import SnapPicker
from window.velocity import VelocityPane
from window.velocity_formula import VelocityFormulaBar
from window.widgets import Scrub, Tooltip


VEL_KEYS = ("vel0", "vel1")
# Common PPQ values. Above 32767 the MIDI header officially means SMPTE timing
PPQS = [2, 4, 8, 16, 24, 48, 96, 120, 144, 192, 240, 384, 480, 768, 960, 1024, 1440, 1920, 2048, 2880,
        3840, 4096, 5760, 7680, 8192, 11520, 12288, 15360, 16384, 23040, 24576, 30720, 32768, 36864,
        46080, 49152, 65535]
TOOLS = [("select", tr("app.select"), "v"), ("line", tr("app.line"), "l"), ("poly", tr("app.polyline"), "p"),
         ("free", tr("app.freehand"), "f"), ("curve", tr("app.curve"), "c"), ("arc", tr("app.arc"), "a"),
         ("custom", tr("app.custom_shape"), "s"),
         ("funnel", tr("app.funnel"), "n"), ("text", tr("app.text"), "x"), ("hz", tr("app.hz_bass"), "h")]
# shown next to Custom shape while it (or one of them) is the tool; their keys work any time
SHAPE_TOOLS = [("circle", tr("app.circle"), "o"), ("polygon", tr("app.polygon"), "q")]
BIG = 1_000_000  # ask before making a custom shape / funnel with more notes than this
MANY_CHANNELS = 15  # a shape spread over more channels than this is shown orange in the shape list
CHANNEL_CHOICES = [
    ("raw", tr("app.as_drawn"),
     tr("app.keeps_overlaps_every_note_exactly_as")),
    ("single", tr("app.single_channel"),
     tr("app.removes_overlaps_all_on_one_channel")),
    ("auto", tr("app.multi_channel"),
     tr("app.a_channel_per_overlap_shapes_whose")),
]
SPLIT_CHOICES = [
    ("key", tr("app.split_same_key_at_the_same")),
    ("time", tr("app.split_any_notes_at_the_same")),
]
SPLIT_TIP = (
    tr("app.same_key_shapes_only_get_split")
)


class App(ProjectFiles, CustomPanel, PolygonPanel, FreehandPanel, FunnelPanel, TumourPanel, PatternPanel, TextPanel, JoinSplit,
          HistoryPanel, tk.Tk):
    def __init__(self, autosave=AUTOSAVE):
        super().__init__()
        errors.install(self)
        self.title(tr("app.spiderweb", VERSION=VERSION))
        try:  # title bar + taskbar icon; True = every other window (drawer, Help, ...) gets it too
            self.icons = [tk.PhotoImage(file=os.path.join(ICONS, f"icon-{n}.png")) for n in (16, 24, 32, 48, 64, 256)]
            self.iconphoto(True, *self.icons)
        except tk.TclError:  # icons missing: Tk's own icon
            pass
        s = self.scale = self.winfo_fpixels("1i") / 96  # match Windows display scaling
        self.geometry(f"{int(1400 * s)}x{int(820 * s)}")
        self.minsize(int(1000 * s), int(600 * s))
        self.autosave_path = autosave

        self.shapes = []
        self.sel = None     # the shape the panel shows (the last one clicked)
        self.sels = set()   # every selected shape
        self.parts = set()  # the selected funnel's highlighted lines / curves (roll_funnel.py)
        self.stroke = None  # the selected custom shape's picked stroke (roll_live.py)
        self.part_main = None  # the highlighted part that was clicked (the others are its linked curves)
        self.clipboard = None
        self.stroke_clip, self.clip_kind, self.stroke_pastes = None, None, 0  # a copied stroke (roll_live.py)
        self.playhead = 0.0  # beat of the play line
        self.out = MidiOut()
        self.player = Player(self.out)
        self._play_job = None
        self._scrub_held = {}
        self.defaults = dict(SHAPE_DEFAULTS)
        self.custom_defaults = dict(CUSTOM_DEFAULTS)  # inside fill and spam gate for new custom shapes
        self.custom_shape = "Circle"  # the library shape new custom shapes are made of
        self.funnel_defaults = dict(FUNNEL_DEFAULTS)  # settings for new funnels
        self.free_smooth = SMOOTH_DEFAULT  # how much new freehand strokes are made perfect (smooth.py)
        self.text_defaults = dict(TEXT_DEFAULTS)  # settings for new text (the last ones used)
        self.polygon_defaults = dict(POLYGON_DEFAULTS)  # points / kind of new polygons (polygon.py)
        self._rows = {"last": True, "line_fill": False, "free": False, "tumour": False, "pattern": False, "text": False,
                      "custom": False, "funnel": False}  # optional panel parts
        self.drawer = None
        self.claw_window = None
        self.claw_pos = ""  # where the claw machine window was last ("+x+y", remembered in the autosave)
        self.hz_window = None  # the Hz bass window (hz_window.py)
        self.hz_clip = None  # notes copied in it (HzWindow.copy_notes)
        self.hz_pos = ""  # its size and place ("WxH+x+y", remembered in the autosave)
        self.hz_fx_h = 0  # its effects pane's height in pixels, dragged by its top edge (0 = as it starts)
        self.hz_start = None  # the beat picked with the Hz bass tool for a new Hz bass (roll_hz.py)
        self.hz_defaults = {"lo": 48, "hi": 58}  # the keys a new Hz bass repeats
        self.hz_preview = clean_preview({})  # the Hz bass preview's settings (hz_preview.py; with the window's)
        self.synth = None  # the built-in synth (files/synth.py), started when the preview is first turned on
        self.rendered, self.slot_count = NO_NOTES, 0  # (start, end, pitch, velocity, slot, owner) rows
        self.note_counts = []  # notes per shape in rendered
        self.notes_late = False  # the notes are behind the shapes (a drag going on: see shapes_changed)
        self.scrubbing = False  # a number box's label is being dragged (widgets.Scrub): slow notes wait too
        self.rendered_pts = []  # each shape's first point when rendered was made
        self.ppq, self.beats = 960, 4
        self.undo_stack, self.redo_stack = [], []
        self._redo_kept = None  # (push_undo)
        self._edit_key = None
        self._scrub = None  # the number box being stepped (scrub_step)
        self._loading = False
        self._autosave_job = None
        self._notes_cache = {}
        self._notes_worked = 0  # shapes whose notes had to be worked out (not remembered)
        self._notes_time = 0.0  # how long that took the last time
        self._late_notes = None  # while dragging: notes left until the mouse rests
        self._position = None
        self._normal_geometry = None

        self.tool = tk.StringVar(value="select")  # (each tool's tip shows when it's picked, see help.py)
        self.tips = Tips(self)
        self.updates = Updates(self)
        self.help_window = None
        self.live = tk.BooleanVar(value=False)  # drawing tools draw into one custom shape (roll_live.py)
        self.draw_tool = "line"  # the drawing tool a double right-click goes back to
        self.snap = tk.StringVar(value=DEFAULT_SNAP)
        self.hz_snap = tk.StringVar(value=DEFAULT_SNAP)  # the Hz bass window's own snap
        self.hz_line = tk.BooleanVar(value=True)  # the Hz bass window shows its red line
        self.hz_fx = tk.BooleanVar(value=False)  # the Hz bass window shows its effects pane
        self.show_lines = tk.BooleanVar(value=True)
        self.show_notes = tk.BooleanVar(value=True)
        self.channel_mode = tk.StringVar(value="single")
        self.channel_split = "key"  # what counts as an overlap for Multi channel (engine.SPLITS)
        self.keys = 128  # the project's key range: 0 .. keys - 1 (paths.KEYS)
        self.keys_var = tk.StringVar(value=str(KEYS[0]))
        self.show_history = tk.BooleanVar(value=False)  # the History panel (off on a fresh start, like the velocity pane)
        self.show_velocity = tk.BooleanVar(value=False)  # off on a fresh start: turning it on shows its tip
        self.vel_tool = tk.StringVar(value="line")
        self.midi_device = tk.StringVar(value=DEFAULT_DEVICE)
        self._vel_height = int(170 * s)
        self.pvar = {
            "ppq": tk.StringVar(value="960"),
            "bpm": tk.StringVar(value="120"),
            "beats": tk.StringVar(value="4"),
            "output": tk.StringVar(value=os.path.join(OUTPUT_DIR, "spiderweb.mid")),
        }
        self.fvars = {k: tk.StringVar() for k in VEL_KEYS}
        self.end_dot = tk.BooleanVar(value=False)
        self.custom_pick = tk.StringVar()  # the shape box of the custom shape panel
        self.fill_var = tk.StringVar(value="empty")
        self.align_var = tk.StringVar(value="auto")
        self.gate_var = tk.StringVar()
        self.fentries = {}
        self.point_rows = []

        ttk.Style(self).configure("Bad.TEntry", foreground="#d00000")
        ttk.Style(self).configure("Bad.TCombobox", foreground="#d00000")
        self._build()
        self.restore_window()
        self.load_autosave()
        self.sync_panel()
        self.after(800, lambda: self.tips.show("welcome"))  # the first time Spiderweb starts
        self.updates.start()  # (What's new after an update; asks about / looks for updates)

        for v in self.pvar.values():
            v.trace_add("write", lambda *_: self.on_project_change())
        for k, v in self.fvars.items():
            v.trace_add("write", lambda *_, k=k: self.on_field(k))
        self.end_dot.trace_add("write", lambda *_: self.on_end_dot())
        self.tool.trace_add("write", lambda *_: self.on_tool_change())
        # (Live shape also shows the selected custom shape's stroke points)
        self.live.trace_add("write", lambda *_: (self.sync_custom(), self.schedule_autosave(),
                                                  self.roll.request_redraw(),
                                                  self.live.get() and self.tips.show("live")))
        self.snap.trace_add("write", lambda *_: self.roll.request_redraw())
        self.hz_snap.trace_add("write", lambda *_: (self.hz_window and self.hz_window.redraw(),
                                                    self.schedule_autosave()))
        self.bind_all("<F1>", lambda e: None if self.in_drawer(e) else self.open_help())
        for key, fn in (("<Control-z>", self.undo), ("<Control-y>", self.redo), ("<Control-s>", self.save_project)):
            self.bind_all(key, lambda e, fn=fn: None if self.in_drawer(e) else fn())
        self.bind_all("<space>", self.hotkey(self.toggle_play, main_only=True))  # (pop-ups: not the main playback)
        for keys, fn in (("Control-c Control-C", self.copy_selected),
                         ("Control-Shift-c Control-Shift-C", self.copy_to_domino),
                         ("Control-Shift-v Control-Shift-V", self.paste_from_domino),
                         ("Control-g Control-G", self.join_selected),
                         ("Control-Shift-g Control-Shift-G", self.split_selected),
                         ("Control-l Control-L", self.turn_into_live),
                         ("Control-k Control-K", lambda: open_claw(self)),
                         ("Control-v Control-V", self.paste), ("Control-h Control-H", lambda: self.flip(True)),
                         ("Control-j Control-J", lambda: self.flip(False)), ("Control-a Control-A", self.select_all),
                         ("Control-Left", lambda: self.rotate(False)), ("Control-Right", lambda: self.rotate(True))):
            for k in keys.split():
                self.bind_all(f"<{k}>", self.hotkey(fn))
        self.midi_device.trace_add("write", lambda *_: (self.stop_play(), self.out.close(), self.schedule_autosave()))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Configure>", self.remember_geometry, add="+")
        # a click anywhere outside the velocity pane = done with its line / curve
        # (the Formula tool's settings change the line just drawn: clicking them keeps it)
        self.bind("<ButtonPress>", lambda e: e.widget is self.vel or str(e.widget).startswith(str(self.vel_formula_bar))
                  or self.vel.confirm(), add="+")

    # ------------------------------------------------------------ layout

    def _build(self):
        # the toolbar: the tools, then the view settings and buttons on the right (on a second row when the window
        # is too narrow for both, see fit_toolbar)
        top = self.toolbar = ttk.Frame(self, padding=(6, 4))
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        bar = self.tool_bar = ttk.Frame(top)
        # Custom shape stays lit while Circle / Polygon (they make custom shapes) is the tool, so it has
        # its own on/off (set in show_shape_tools) instead of lighting up only for its own value
        self.custom_lit = tk.BooleanVar(value=False)
        for key, label, hot in TOOLS:
            if key == "custom":
                b = ttk.Checkbutton(bar, text=f"{label} ({hot.upper()})", variable=self.custom_lit,
                                    style="Toolbutton", command=lambda: self.tool.set("custom"))
            else:
                b = ttk.Radiobutton(bar, text=f"{label} ({hot.upper()})", value=key, variable=self.tool,
                                    style="Toolbutton")
            b.pack(side="left", padx=1)
            Tooltip(b, BY_ID[TOOL_TOPICS[key]]["tip"])
            if key == "custom":
                self.custom_tool_btn = b
        # Circle / Polygon: only while one of them or Custom shape is the tool
        self.shape_tool_bar = ttk.Frame(bar)
        ttk.Separator(self.shape_tool_bar, orient="vertical").pack(side="left", fill="y", padx=(2, 3), pady=2)
        for key, label, hot in SHAPE_TOOLS:
            b = ttk.Radiobutton(self.shape_tool_bar, text=f"{label} ({hot.upper()})", value=key, variable=self.tool,
                                style="Toolbutton")
            b.pack(side="left", padx=1)
            Tooltip(b, BY_ID[TOOL_TOPICS[key]]["tip"])
        ttk.Separator(self.shape_tool_bar, orient="vertical").pack(side="left", fill="y", padx=(3, 2), pady=2)
        live = ttk.Checkbutton(bar, text=tr("app.live_shape_g"), variable=self.live)
        live.pack(side="left", padx=(12, 0))
        Tooltip(live, BY_ID["live"]["tip"])
        bar = self.option_bar = ttk.Frame(top)
        self._toolbar_rows = None
        top.bind("<Configure>", lambda e: self.fit_toolbar())
        ttk.Label(bar, text=tr("app.snap")).pack(side="left", padx=(0, 4))
        SnapPicker(self, bar, self.snap).button.pack(side="left")
        redraw = lambda: self.roll.request_redraw()
        ttk.Checkbutton(bar, text=tr("app.show_lines"), variable=self.show_lines,
                        command=redraw).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(bar, text=tr("app.show_notes"), variable=self.show_notes,
                        command=redraw).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(bar, text=tr("app.velocity_pane"), variable=self.show_velocity,
                        command=self.toggle_velocity).pack(side="left", padx=(8, 0))
        b = ttk.Checkbutton(bar, text=tr("app.history"), variable=self.show_history, command=self.toggle_history)
        b.pack(side="left", padx=(8, 0))
        Tooltip(b, tr("app.history_tip"))
        ttk.Button(bar, text=tr("app.fit_view"), command=lambda: self.roll.fit_view()).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("app.undo"), command=self.undo).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("app.redo"), command=self.redo).pack(side="left", padx=(4, 0))
        self.play_btn = ttk.Button(bar, text=tr("app.play_space"), width=14, command=self.toggle_play, takefocus=False)
        self.play_btn.pack(side="left", padx=(12, 0))
        help_btn = ttk.Button(bar, text=tr("app.help_f1"), command=self.open_help, takefocus=False)
        help_btn.pack(side="left", padx=(12, 0))
        Tooltip(help_btn, tr("app.every_tip_searchable_opens_at_the"))
        self.show_shape_tools()

        self.status = ttk.Label(self, text="", padding=(6, 2), font=("Segoe UI", 9))
        self.status.pack(side="bottom", fill="x")

        side = self._build_side()
        self._build_project(side)
        self._build_history(side)
        self._build_shape_list(side)
        self._build_shape_settings(side)
        self.settings.bind("<Configure>", lambda e: self.after_idle(self.fit_side), add="+")

        s = self.scale
        self.panes = tk.PanedWindow(self, orient="vertical", sashwidth=int(6 * s), sashrelief="raised",
                                    bd=0, bg="#c8c8c8", opaqueresize=True)
        self.panes.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        roll_box = tk.Frame(self.panes)
        self.roll = PianoRoll(roll_box, self, s)
        add_zoom_bars(roll_box, self.roll)
        self.panes.add(roll_box, stretch="always", minsize=int(120 * s))
        self.vel_box = ttk.Frame(self.panes)
        vbar = ttk.Frame(self.vel_box, padding=(2, 2))
        vbar.pack(fill="x")
        ttk.Label(vbar, text=tr("app.velocity")).pack(side="left", padx=(2, 8))
        for key, label in (("line", tr("app.linear")), ("curve", tr("app.curve")), ("pencil", tr("app.pencil")),
                           ("formula", tr("app.formula"))):
            b = ttk.Radiobutton(vbar, text=label, value=key, variable=self.vel_tool, style="Toolbutton")
            b.pack(side="left", padx=1)
            if key == "formula":
                Tooltip(b, tr("app.formula_tip"))
        self.vel_formula_bar = VelocityFormulaBar(vbar, self)  # (shown while Formula is the tool)
        self.vel_hint = ttk.Label(vbar, text=tr("app.ctrl_flat_shift_snap_enter_done"),
                                  foreground="#777", font=("Segoe UI", 8))
        self.vel_hint.pack(side="left", padx=(10, 0))
        self.vel_tool.trace_add("write", lambda *_: self.show_vel_formula())
        self.vel = VelocityPane(self.vel_box, self, s)
        self.vel.pack(fill="both", expand=True)  # the pane itself is added when it's turned on (off at first)

    def show_vel_formula(self):
        if self.vel_tool.get() == "formula":
            self.vel_formula_bar.fill_list()
            self.vel_formula_bar.pack(side="left", before=self.vel_hint)
        else:
            self.vel_formula_bar.pack_forget()

    def show_shape_tools(self):
        """Circle / Polygon next to Custom shape, only while one of those is the tool."""
        show = self.tool.get() in ("custom",) + tuple(key for key, _, _ in SHAPE_TOOLS)
        if show != bool(self.shape_tool_bar.winfo_manager()):
            if show:
                self.shape_tool_bar.pack(side="left", after=self.custom_tool_btn)
            else:
                self.shape_tool_bar.pack_forget()
        self.custom_lit.set(show)  # (Custom shape stays lit with them: they make custom shapes)
        self.after_idle(self.fit_toolbar)

    def fit_toolbar(self):
        """The view settings and buttons: right of the tools when both fit in the window's width, else below them."""
        top = self.toolbar
        need = self.tool_bar.winfo_reqwidth() + self.option_bar.winfo_reqwidth() + int(24 * self.scale)
        rows = 1 if need <= top.winfo_width() - 12 else 2
        if rows == self._toolbar_rows:
            return
        self._toolbar_rows = rows
        self.tool_bar.grid(row=0, column=0, sticky="w")
        if rows == 1:
            self.option_bar.grid(row=0, column=1, sticky="e")
        else:
            self.option_bar.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_side(self):
        """The side panel: scrolls (scrollbar, mouse wheel) when everything in it doesn't fit; otherwise the help
        text at the bottom takes the room that's left."""
        box = self.side_box = ttk.Frame(self, width=int(330 * self.scale))
        box.pack(side="right", fill="y")
        box.pack_propagate(False)
        c = self.side_canvas = tk.Canvas(box, highlightthickness=0, bd=0,
                                         bg=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")
        self.side_bar = ttk.Scrollbar(box, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=self.side_bar.set)
        c.pack(side="left", fill="both", expand=True)
        side = self.side = ttk.Frame(c, padding=(6, 0, 6, 6))
        self._side_win = c.create_window(0, 0, window=side, anchor="nw")
        c.bind("<Configure>", lambda e: self.fit_side())
        side.bind("<Configure>", lambda e: self.after_idle(self.fit_side))
        self.bind("<MouseWheel>", self.side_wheel, add="+")
        return side

    def fit_side(self):
        c, side = self.side_canvas, self.side
        need, have = side.winfo_reqheight(), c.winfo_height()
        # room to spare: stretched to the panel's height; too tall: its own height (0), so
        # it keeps following what it needs
        c.itemconfigure(self._side_win, width=c.winfo_width(), height=have if need < have else 0)
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(need, have)))
        if need > have + 1:
            if not self.side_bar.winfo_ismapped():  # the panel gets wider by the scrollbar, not narrower inside
                self.side_box.config(width=int(330 * self.scale) + self.side_bar.winfo_reqwidth())
                self.side_bar.pack(side="right", fill="y", before=c)
        elif self.side_bar.winfo_ismapped():
            self.side_bar.pack_forget()
            self.side_box.config(width=int(330 * self.scale))
            c.yview_moveto(0)

    def side_wheel(self, e):
        """The mouse wheel over the side panel scrolls it (lists scroll themselves)."""
        w = str(e.widget)
        if (w.startswith(str(self.side_canvas)) and self.side_bar.winfo_ismapped()
                and not isinstance(e.widget, (tk.Listbox, tk.Text, ttk.Combobox))):
            self.side_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _build_project(self, side):
        box = ttk.LabelFrame(side, text=tr("app.project"), padding=6)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        r = 0
        boxes = []  # BPM and Beats per bar: made as wide as the dropdowns below, so all four line up
        for label, key in ((tr("app.ppq"), "ppq"), (tr("app.bpm"), "bpm"), (tr("app.beats_per_bar"), "beats")):
            lb = ttk.Label(box, text=label)
            lb.grid(row=r, column=0, sticky="w", pady=1)
            if key == "ppq":
                row = ttk.Frame(box)
                row.grid(row=r, column=1, sticky="w", padx=5)
                self.ppq_box = ttk.Combobox(row, textvariable=self.pvar[key], values=PPQS, width=7, height=12)
                self.ppq_box.pack(side="left")
                # Many MIDI programs can't open a file with a PPQ of 32767 or more (above that it's SMPTE timing anyway)
                self.ppq_warning = ttk.Label(row, text=tr("app.many_programs_can_t_open_this"), foreground="#d00000",
                                             font=("Segoe UI", 8))
                Tooltip(self.ppq_warning, tr("app.a_ppq_of_or_more_many", PPQ_WARN=PPQ_WARN))
            else:
                cell = ttk.Frame(box)
                cell.grid(row=r, column=1, sticky="w", padx=5)
                e = ttk.Entry(cell, textvariable=self.pvar[key], width=6)
                e.pack(fill="both", expand=True)
                boxes.append(e)
                Scrub(self, [(e, self.pvar[key], None)], (1, 10, 0.1) if key == "bpm" else (1, 1, 1),
                      *((4, 100000) if key == "bpm" else (1, 32)), label=lb)
            r += 1
        ttk.Label(box, text=tr("app.keys")).grid(row=r, column=0, sticky="w", pady=1)
        b = ttk.Combobox(box, textvariable=self.keys_var, values=[str(k) for k in KEYS], state="readonly",
                         width=7)
        b.grid(row=r, column=1, sticky="w", padx=5)
        b.bind("<<ComboboxSelected>>", lambda e: self.on_project_change())
        Tooltip(b, tr("app.128_the_standard_keys_0_127"))
        box.update_idletasks()
        for e in boxes:
            e.master.config(width=b.winfo_reqwidth(), height=e.winfo_reqheight())
            e.master.pack_propagate(False)
        r += 1
        ttk.Label(box, text=tr("app.output_file")).grid(row=r, column=0, sticky="w", pady=1)
        out = ttk.Frame(box)
        out.grid(row=r, column=1, sticky="ew", padx=5)
        ttk.Entry(out, textvariable=self.pvar["output"]).pack(side="left", fill="x", expand=True)
        ttk.Button(out, text="…", width=3, command=self.browse_output).pack(side="left", padx=(3, 0))
        r += 1
        ttk.Label(box, text=tr("app.channels")).grid(row=r, column=0, sticky="nw", pady=(3, 0))
        ch = ttk.Frame(box)
        ch.grid(row=r, column=1, sticky="w", padx=5, pady=(3, 0))
        for value, text, tip in CHANNEL_CHOICES:
            b = ttk.Radiobutton(ch, text=text, value=value, variable=self.channel_mode,
                                command=self.on_channel_mode)
            b.pack(anchor="w")
            Tooltip(b, tip)
        names = [name for _, name in SPLIT_CHOICES]
        self.split_box = ttk.Combobox(ch, state="readonly", values=names, width=max(map(len, names)))
        self.split_box.pack(anchor="w", padx=(18, 0), pady=(1, 0))
        self.split_box.bind("<<ComboboxSelected>>", lambda e: self.shapes_changed())
        self.split_box.current(0)
        Tooltip(self.split_box, SPLIT_TIP)
        r += 1
        ttk.Label(box, text=tr("app.midi_out")).grid(row=r, column=0, sticky="w", pady=(3, 0))
        dev = ttk.Combobox(box, textvariable=self.midi_device, state="readonly", values=devices())
        dev.configure(postcommand=lambda: dev.configure(values=devices()))  # re-list when it opens
        dev.grid(row=r, column=1, sticky="ew", padx=5, pady=(3, 0))
        r += 1
        names = [name for _, name in DOMINO_STARTS]
        ttk.Label(box, text=tr("app.domino_start")).grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.domino_box = ttk.Combobox(box, state="readonly", values=names)
        self.domino_box.grid(row=r, column=1, sticky="ew", padx=5, pady=(6, 0))
        self.domino_box.current(0)
        self.domino_box.bind("<<ComboboxSelected>>", lambda e: self.schedule_autosave())
        Tooltip(self.domino_box, tr("app.where_copied_and_pasted_notes_start"))
        domino = ttk.Frame(box)
        domino.grid(row=r + 1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        paste = b = ttk.Button(domino, text=tr("app.paste_from_domino"), command=self.paste_from_domino)
        Tooltip(b, tr("app.ctrl_shift_v_the_notes_copied"))
        b = ttk.Button(domino, text=tr("app.copy_to_domino"), command=self.copy_to_domino)
        b.pack(side="right")
        paste.pack(side="right", padx=4)  # (next to it, the same gap as Open… / Save…)
        Tooltip(b, tr("app.ctrl_shift_c_copies_the_selected"))
        btns = ttk.Frame(box)
        btns.grid(row=r + 2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(btns, text=tr("app.open"), command=self.open_project).pack(side="left")
        ttk.Button(btns, text=tr("app.save"), command=self.save_project).pack(side="left", padx=4)
        ttk.Button(btns, text=tr("app.generate_midi"), command=self.generate).pack(side="right")

    def _build_shape_list(self, side):
        box = self.shapes_box = ttk.LabelFrame(side, text=tr("app.shapes"), padding=6)
        box.pack(fill="x", pady=(8, 0))
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.listbox = tk.Listbox(row, height=8, activestyle="none", exportselection=False, font=("Segoe UI", 9),
                                  selectmode="extended")
        sb = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_list_select)
        # hand the keys back to the roll, so Space plays instead of picking a list row
        self.listbox.bind("<ButtonRelease-1>", lambda e: self.roll.focus_set(), add="+")
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text=tr("app.duplicate"), command=self.duplicate).pack(side="left")
        ttk.Button(btns, text=tr("app.delete"), command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(btns, text=tr("app.delete_all"), command=self.delete_all).pack(side="left")
        self._build_join(box)

    def _build_shape_settings(self, side):
        self.settings = ttk.LabelFrame(side, text=tr("app.new_shape_defaults"), padding=6)
        self.settings.pack(fill="x", pady=(8, 0))
        v = ttk.Frame(self.settings)
        v.pack(fill="x")
        lb = ttk.Label(v, text=tr("app.velocity"))
        lb.pack(side="left")
        for i, key in enumerate(("vel0", "vel1")):
            if i:
                ttk.Label(v, text="→").pack(side="left", padx=3)
            self.fentries[key] = ttk.Entry(v, textvariable=self.fvars[key], width=5)
            self.fentries[key].pack(side="left", padx=(5 if not i else 0, 0))
        # dragging "Velocity" moves both ends together
        Scrub(self, [(self.fentries[k], self.fvars[k], None) for k in ("vel0", "vel1")], (1, 10, 1), 1, 127, label=lb)
        ttk.Label(v, text=tr("app.start_end"), foreground="#777").pack(side="left", padx=(5, 0))
        self.env_note = ttk.Label(self.settings, text=tr("app.velocity_drawn_in_the_velocity_pane"), foreground="#777",
                                  font=("Segoe UI", 8),
                                  wraplength=int(300 * self.scale), justify="left")
        last = self.last_row = ttk.Frame(self.settings)
        last.pack(fill="x", pady=(4, 0))
        ttk.Label(last, text=tr("app.last_note")).pack(side="left", anchor="n")
        opts = ttk.Frame(last)
        opts.pack(side="left", padx=(5, 0))
        ttk.Radiobutton(opts, text=tr("app.ends_on_the_last_point"), value=False,
                        variable=self.end_dot).pack(anchor="w")
        ttk.Radiobutton(opts, text=tr("app.starts_exactly_on_the_last_point"), value=True,
                        variable=self.end_dot).pack(anchor="w")
        self._build_line_fill()
        self._build_freehand()
        self._build_tumour()
        self._build_pattern()
        self._build_text()
        self._build_custom()
        self._build_polygon()
        self._build_funnel()
        self.points_box = ttk.Frame(self.settings)
        self.points_box.pack(fill="x", pady=(6, 0))

    def build_points(self):
        """Tick/pitch boxes for each point of the selected line or curve."""
        for w in self.points_box.winfo_children():
            w.destroy()
        self.point_rows = []
        sh = self.selected()
        names = point_names(sh) if sh and len(self.sels) == 1 else None
        if not names:
            return
        for col, text in enumerate((tr("app.point"), tr("app.tick"), tr("app.pitch"))):
            ttk.Label(self.points_box, text=text, foreground="#777").grid(row=0, column=col, sticky="w", padx=(0, 5))
        for i, name in enumerate(names):
            tv, pv = tk.StringVar(), tk.StringVar()
            ttk.Label(self.points_box, text=name).grid(row=i + 1, column=0, sticky="w", pady=1, padx=(0, 5))
            te = ttk.Entry(self.points_box, textvariable=tv, width=10)
            te.grid(row=i + 1, column=1, sticky="w", padx=(0, 5))
            pe = ttk.Entry(self.points_box, textvariable=pv, width=7)
            pe.grid(row=i + 1, column=2, sticky="w")
            self.point_rows.append((tv, pv, te, pe))
            # ticks: a snap step (Ctrl = one tick), pitch: a key (Shift = an octave)
            Scrub(self, [(te, tv, None)], lambda: (self.snap_ticks(), 4 * self.snap_ticks(), 1), 0)
            Scrub(self, [(pe, pv, None)], (1, 12, 0.1), 0, 127)
        self.sync_points()
        for i, (tv, pv, _, _) in enumerate(self.point_rows):
            tv.trace_add("write", lambda *_, i=i: self.on_point(i))
            pv.trace_add("write", lambda *_, i=i: self.on_point(i))

    # ------------------------------------------------------------ panel <-> shapes

    def selected(self):
        return self.shapes[self.sel] if self.sel is not None else None

    def target(self):
        """The panel shows the selected shape, or the defaults for new shapes when nothing is selected."""
        return self.selected() or self.defaults

    def targets(self):
        """What the panel's velocity and last-note settings change: every selected shape, or the defaults."""
        return [self.shapes[i] for i in sorted(self.sels)] or [self.defaults]

    def sync_title(self):
        sh = self.selected()
        title = tr("app.shape", sel=self.sel + 1,
                   shape_label=self.shape_label(sh)) if sh else tr("app.new_shape_defaults")
        if len(self.sels) > 1:
            title += tr("app.more_selected", n=len(self.sels) - 1)
        self.settings.config(text=title)

    def sync_panel(self):
        sh = self.selected()
        self.sync_title()
        self._loading = True
        for key, var in self.fvars.items():
            var.set(fmt(self.target()[key]))
            self.fentries[key].config(style="TEntry")
        self.end_dot.set(bool(self.target().get("end_dot", False)))
        self._loading = False
        if sh and sh.get("vel_env"):
            self.env_note.pack(fill="x", after=self.settings.winfo_children()[0], pady=(2, 0))
        else:
            self.env_note.pack_forget()
        self.build_points()
        self.sync_freehand()
        self.sync_tumour()
        self.sync_pattern()
        self.sync_text()
        self.sync_custom()
        self.sync_funnel()
        self.sync_list_selection()
        self.sync_join()
        self.sync_line_fill()
        if self.claw_window:
            self.claw_window.sync()
        if self.hz_window:
            self.hz_window.sync()
        if self.sel is not None:
            self.listbox.see(self.sel)
        # the first time one is selected: how it's edited
        kinds = {self.shapes[i]["kind"] for i in self.sels}
        for kind, topic in (("custom", "custom_edit"), ("funnel", "funnel_curves"), ("free", "straighten")):
            if kind in kinds:
                self.tips.show(topic)
                break

    def sync_list_selection(self):
        self.listbox.selection_clear(0, "end")
        for i in self.sels:
            self.listbox.selection_set(i)

    def sync_points(self):
        sh = self.selected()
        if not sh:
            return
        self._loading = True
        for (tv, pv, te, pe), (b, p) in zip(self.point_rows, sh["pts"]):
            tv.set(fmt(b * self.ppq))
            pv.set(fmt(p))
            te.config(style="TEntry")
            pe.config(style="TEntry")
        self._loading = False

    def on_field(self, key):
        if self._loading:
            return
        try:
            value = calc_int(self.fvars[key].get(), 1, 127)
        except ValueError:
            self.fentries[key].config(style="Bad.TEntry")
            return
        self.fentries[key].config(style="TEntry")
        tgts = self.targets()
        if all(t[key] == value and not t.get("vel_env") for t in tgts):
            return
        if self.sels:
            self.begin_edit(("field", tuple(sorted(self.sels)), key))
        for t in tgts:
            t[key] = value
            t.pop("vel_env", None)
            t.pop("own_vel", None)  # pasted notes: their own velocities are replaced
        self.env_note.pack_forget()
        self.shapes_changed()

    def on_end_dot(self):
        if self._loading:
            return
        tgts, value = self.targets(), self.end_dot.get()
        if all(t.get("end_dot", False) == value for t in tgts):
            return
        if self.sels:
            self.push_undo(name=tr("app.last_note"))
        for t in tgts:
            t["end_dot"] = value
        self.shapes_changed()

    def on_point(self, i):
        if self._loading or not self.selected():
            return
        tv, pv, te, pe = self.point_rows[i]
        values = []
        for var, entry in ((tv, te), (pv, pe)):
            try:
                values.append(float(calc(var.get())))
                entry.config(style="TEntry")
            except ValueError:
                entry.config(style="Bad.TEntry")
        if len(values) < 2:
            return
        sh = self.selected()
        new = [values[0] / self.ppq, values[1]]
        if sh["pts"][i] != new:
            self.begin_edit(("point", self.sel, i))
            sh["pts"][i] = new
            if self.roll.keep_symmetric(sh, i):
                self.sync_points()  # the other half followed
            self.shapes_changed()

    def on_project_change(self):
        for key, lo, hi in (("ppq", 1, 65535), ("beats", 1, 32)):
            try:
                setattr(self, key, calc_int(self.pvar[key].get(), lo, hi))
            except ValueError:
                pass
        self.keys = KEYS[1] if self.keys_var.get() == str(KEYS[1]) else KEYS[0]
        self.roll.clamp_view()
        warn = self.ppq >= PPQ_WARN
        self.ppq_box.config(style="Bad.TCombobox" if warn else "TCombobox")
        if warn and not self.ppq_warning.winfo_manager():
            self.ppq_warning.pack(side="left", padx=(5, 0))
        elif not warn:
            self.ppq_warning.pack_forget()
        self.sync_points()
        self.shapes_changed()
        self.sync_custom()  # gates are shown in ticks
        self.sync_funnel()
        self.sync_tumour()
        self.sync_text()

    def read_project(self):
        try:
            ppq = calc_int(self.pvar["ppq"].get(), 1, 65535)
        except ValueError:
            raise ValueError(tr("app.ppq_must_be_a_whole_number"))
        try:
            bpm = calc(self.pvar["bpm"].get())
            if not 4 <= bpm <= 100000:
                raise ValueError
        except ValueError:
            raise ValueError(tr("app.bpm_must_be_a_number_at"))
        try:
            beats = calc_int(self.pvar["beats"].get(), 1, 32)
        except ValueError:
            raise ValueError(tr("app.beats_per_bar_must_be_a"))
        return ppq, bpm, beats

    # ------------------------------------------------------------ shapes

    def notes_of(self, sh):
        return self.notes_tracks(sh)[0]

    def notes_tracks(self, sh):
        """shape_notes_tracks, remembered. A claw goes on top of the notes remembered without it (trying claw
        settings doesn't make the shape's notes again)."""
        key = (json.dumps(sh, sort_keys=True), self.ppq, self.keys)
        if key not in self._notes_cache:
            if len(self._notes_cache) > 500:
                self._notes_cache.clear()
            if sh.get("claw"):
                notes, tracks = self.notes_tracks({k: v for k, v in sh.items() if k != "claw"})
                self._notes_cache[key] = with_claw(notes, tracks, sh["claw"], self.ppq)
            else:
                self._notes_cache[key] = shape_notes_tracks(sh, self.ppq, self.keys)
            self._notes_worked += 1
        return self._notes_cache[key]

    def shapes_changed(self, now=False, moving=False):
        """Recalculate every note (overlaps depend on all shapes together) and refresh the screen.
        While dragging, if that's slow (lots of notes), only the lines follow the mouse: the notes catch up when the
        mouse rests or is let go (now: catch up). moving: shapes are dragged to another place: the piano roll shows
        their notes going along (roll_draw.paint_carried), and they catch up when the mouse rests (if that's quick)
        or is let go."""
        if self._late_notes:
            self.after_cancel(self._late_notes)
            self._late_notes = None
        slow = self._notes_time > 0.15
        # (other drags make the notes AND repaint them all at every step: the repaint counts too)
        slow = slow or not moving and self._notes_time + self.roll.paint_time > 0.15
        # (moving: also whenever the piano roll shows its notes as one picture, which it can carry along)
        if not now and (self.roll.drag or self.scrubbing) and (slow or moving and (self._notes_time > 0.03 or self.roll._img is not None)):
            self.notes_late = True
            if not (moving and slow):  # (quick enough to make: the real notes show whenever the mouse rests)
                self._late_notes = self.after(120 if moving else 250, self._notes_rested)
            self.roll.request_redraw()
            return
        self.notes_late = False
        self.rendered_pts = [list(sh["pts"][0]) for sh in self.shapes]  # (where each shape is in these notes)
        started, worked = time.perf_counter(), self._notes_worked
        self.channel_split = SPLIT_CHOICES[max(self.split_box.current(), 0)][0]
        self.split_box.config(state="readonly" if self.channel_mode.get() == "auto" else "disabled")
        got = [self.notes_tracks(sh) for sh in self.shapes]
        self.rendered, self.slot_count = render([n for n, _ in got], self.channel_mode.get(), self.channel_split,
                                                [t for _, t in got], [tracks_apart(sh) for sh in self.shapes])
        if self._notes_worked != worked:
            self._notes_time = time.perf_counter() - started
        counts = self.note_counts = np.bincount(self.rendered[:, 5], minlength=len(self.shapes)).tolist()
        chans = [1] * len(self.shapes)  # Multi channel: how many channels each shape spreads over
        if self.channel_mode.get() == "auto" and len(self.rendered):
            pairs = np.unique(self.rendered[:, 5] * (self.slot_count + 1) + self.rendered[:, 4])
            chans = np.bincount(pairs // (self.slot_count + 1), minlength=len(self.shapes)).tolist()
        self.listbox.delete(0, "end")
        for i, sh in enumerate(self.shapes):
            uses = tr("app.channels_2", chans=chans[i]) if chans[i] > 1 else ""
            self.listbox.insert("end",
                                tr("app.notes", i=i + 1, shape_label=self.shape_label(sh), counts=counts[i], uses=uses))
            if chans[i] > MANY_CHANNELS:  # (past this the note colours and channel numbers repeat)
                self.listbox.itemconfig(i, foreground=GAP_COLOR, selectforeground="#ffd9b0")
        self.sync_list_selection()
        self.sync_join()
        self.roll.request_redraw()
        self.update_status()
        self.schedule_autosave()
        self.sync_history()

    def _notes_rested(self):
        # after the mouse moves still waiting to be handled (a slow redraw can make the timer run out first)
        self._late_notes = self.after_idle(lambda: self.shapes_changed(now=True))

    def catch_up_notes(self):
        """The notes left for later while dragging (shapes_changed): now."""
        if self._late_notes or self.notes_late:
            self.shapes_changed(now=True)

    def shape_edited(self, moving=False):
        """The selected shape was changed with the mouse (moving: just dragged to another place)."""
        self.sync_points()
        self.shapes_changed(moving=moving)

    def add_shape(self, sh, name=None):
        self.push_undo(name=name or tr("app.draw", shape_label=self.shape_label(sh)))
        self.shapes.append(sh)
        self.select(len(self.shapes) - 1)
        self.shapes_changed()
        if sh["kind"] == "curve":  # the first curve: its anchors and handles
            self.tips.show("curves_pen", wait=True)
        self.tips.show("undo", wait=True)  # the first shape: how to take it back

    def on_channel_mode(self):
        self.shapes_changed()
        self.sync_custom()
        if self.channel_mode.get() == "auto":
            self.tips.show("channels", wait=True)

    def select(self, i, toggle=False):
        """Select shape i only (None = nothing), or with toggle add it to / take it out of the selection."""
        if not toggle:
            self.select_many([] if i is None else [i], i)
        elif i in self.sels:
            rest = self.sels - {i}
            self.select_many(rest, self.sel if self.sel in rest else max(rest, default=None))
        else:
            self.select_many(self.sels | {i}, i)

    def select_many(self, indices, primary):
        ty = self.roll.typing
        self.sels = set(indices)  # first: ending the typing refreshes the panel, which reads the selection
        self.sel = primary
        if ty and (ty["i"] not in self.sels if ty["i"] is not None else bool(self.sels)):
            self.roll.end_typing()  # another shape picked (or the text deleted): done typing
        self.parts = set()
        self.stroke = None
        self._edit_key = self._scrub = None
        self.sync_panel()
        self.roll.request_redraw()
        self.update_status()

    def set_parts(self, parts, main=None):
        """Highlight these lines / curves of the selected funnel. `main` = the one clicked (its linked curves get
        another colour); None keeps the last one."""
        if main is not None or not parts:
            self.part_main = main
        if set(parts) != self.parts:
            self.parts = set(parts)
            self.sync_funnel()
        self.roll.request_redraw()

    def set_stroke(self, k):
        """Pick stroke k of the selected custom shape (None = none): Del deletes it, a curve shows its handles."""
        self.stroke = k
        self.sync_custom()
        self.sync_freehand()
        self.roll.request_redraw()

    def select_all(self):
        if self.shapes:
            self.select_many(range(len(self.shapes)), self.sel if self.sel is not None else len(self.shapes) - 1)

    def on_list_select(self, _):
        cur = set(self.listbox.curselection())
        if cur != self.sels:
            added = cur - self.sels
            primary = max(added) if added else self.sel if self.sel in cur else max(cur, default=None)
            self.select_many(cur, primary)

    def delete_selected(self):
        if not self.sels:
            return
        self.roll.end_typing()  # first: it refreshes the panel, which must still see the old shapes
        self.push_undo(name=tr("app.delete"))
        for i in sorted(self.sels, reverse=True):
            del self.shapes[i]
        self.select(None)
        self.shapes_changed()

    def delete_all(self):
        if not self.shapes:
            return
        if not messagebox.askyesno(tr("app.spiderweb_2"), tr("app.are_you_sure_you_want_to", n=len(self.shapes)),
                                   icon="warning", parent=self):
            return
        self.roll.cancel_draft()  # first: it refreshes the panel, which must still see the old shapes
        self.push_undo(name=tr("app.delete_all"))
        self.shapes.clear()
        self.select(None)
        self.shapes_changed()

    def duplicate(self):
        if not self.sels:
            return
        shift = self.snap_beats() or 1.0
        self.add_copies([self.shapes[i] for i in sorted(self.sels)], shift, tr("app.duplicate"))

    def add_copies(self, shapes, shift, name=tr("app.paste")):
        """Add copies of shapes moved shift beats later, and select them."""
        self.push_undo(name=name)
        first = len(self.shapes)
        for sh in shapes:
            new = copy.deepcopy(sh)
            new["pts"] = [[b + shift, p] for b, p in new["pts"]]
            self.shapes.append(new)
        self.select_many(range(first, len(self.shapes)), len(self.shapes) - 1)
        self.shapes_changed()

    def picked(self):
        """(shape, stroke number) of the picked stroke of the selected custom shape, or None."""
        sh = self.selected()
        k = self.roll.picked_stroke(sh) if len(self.sels) == 1 else None
        return None if k is None else (sh, k)

    def copy_selected(self, whole=False):
        """Ctrl+C: the selected shapes (or with curves highlighted: the curve's shape; a stroke picked: it)."""
        if not whole and self.roll.curve_parts():
            return self.roll.copy_curve()
        if not whole and self.picked():
            return self.roll.copy_stroke(*self.picked())
        if self.sels:
            self.clipboard = copy.deepcopy([self.shapes[i] for i in sorted(self.sels)])
            self.clip_kind = "shapes"
            self.status.config(text=tr("app.copied_shape_s_ctrl_v_pastes", n=len(self.clipboard)))

    def paste(self, whole=False, at=None):
        """Paste the copied shapes so they start at the play line (snapped to the grid), or at beat `at` (a Select
        double-click: the mouse, snapped already). With curves highlighted and a curve copied: its shape onto them."""
        if not whole and self.roll.curve_parts() and self.roll.curve_clip:
            return self.roll.paste_curve()
        if not whole and self.clip_kind == "stroke":
            return self.roll.paste_stroke()
        if not self.clipboard:
            return
        start = min(b for sh in self.clipboard for b, _ in cached_path(sh))
        if at is None:
            at, sb = self.playhead, self.snap_beats()
            if sb:
                at = round(at / sb) * sb
        self.add_copies(self.clipboard, at - start)

    def flip(self, sideways, whole=False):
        """Mirror the selected shapes as a group: sideways (time) or upside down (pitch).
        With a funnel's curves highlighted: those curves (end to end / inside out)."""
        if not whole and self.roll.curve_parts():
            return self.roll.set_curves(turned_curve if sideways else inside_out)
        if not whole and self.picked():
            return self.roll.flip_stroke(*self.picked(), sideways)
        if not self.sels:
            return
        shapes = [self.shapes[i] for i in sorted(self.sels)]
        k = 0 if sideways else 1
        vals = [pt[k] for sh in shapes for pt in cached_path(sh)]
        mid2 = min(vals) + max(vals)  # twice the middle
        self.push_undo(name=tr("app.flip_sideways") if sideways else tr("app.flip_upside_down"))
        for sh in shapes:
            sh["pts"] = [[mid2 - b, p] if sideways else [b, mid2 - p] for b, p in sh["pts"]]
            for tm in all_tumours(sh):  # mirrored: the bumps swap sides too
                tm["mirror"] = not tm["mirror"]
            for key in ("pattern", "shape"):  # and a curve's formulas (pattern.py)
                if sh.get(key):
                    sh[key]["mirror"] = not sh[key]["mirror"]
            if sideways:  # the velocities flip with it
                if sh.get("vel_env"):
                    sh["vel_env"] = [[1 - u, v] for u, v in reversed(sh["vel_env"])]
                sh["vel0"], sh["vel1"] = sh["vel1"], sh["vel0"]
        self.sync_panel()
        self.shapes_changed()

    def rotate(self, clockwise, whole=False):
        """Turn the selected shapes 90 degrees as a group, around their middle, as they look on screen
        (so the current zoom decides how many beats one key becomes). A stroke picked: just it."""
        if not whole and self.picked():
            return self.roll.turn_stroke(*self.picked(), clockwise)
        if not self.sels or self.roll.sx is None:
            return
        shapes = [self.shapes[i] for i in sorted(self.sels)]
        path = [pt for sh in shapes for pt in cached_path(sh)]
        bs, ps = [b for b, _ in path], [p for _, p in path]
        cb, cp = (min(bs) + max(bs)) / 2, (min(ps) + max(ps)) / 2
        r = self.roll.sy / self.roll.sx  # beats per key on screen
        sign = 1 if clockwise else -1
        self.push_undo(name=tr("app.turn_90"))
        for sh in shapes:
            sh["pts"] = [[cb + sign * (p - cp) * r, cp - sign * (b - cb) / r] for b, p in sh["pts"]]
            if sh["kind"] == "arc" or sh["kind"] == "free" and "k" in sh:  # still round (arc.py, smooth.py)
                sh["k"] = r * r / sh.get("k", 1.0)
            if sh.get("text"):  # its size / grow are measured the same way (see text.py)
                sh["text"]["k"] = r * r / sh["text"]["k"]
            for tm in all_tumours(sh):  # the bumps turn with it (sizes as they look on screen, see tumour.py)
                tm["size"] *= tm["k"] / r
                tm["length"] *= r / tm["k"]
                tm["dist"] *= r / tm["k"]
                tm["ease"] = tm.get("ease", 0.0) * r / tm["k"]
                tm["k"] = r * r / tm["k"]
            pat = sh.get("pattern")
            if pat:  # a pattern along a curve turns the same way (pattern.py)
                pat["scale"] *= pat["k"] / r
                pat["k"] = r * r / pat["k"]
            if sh.get("shape"):  # (its sizes are shares of the curve's length: only the screen proportions)
                sh["shape"]["k"] = r * r / sh["shape"]["k"]
        self.sync_panel()
        self.shapes_changed()

    @staticmethod
    def stretched(sh, ab, kx, ap, ky):
        """A copy of shape sh stretched kx times sideways from beat ab and ky times up / down from pitch ap (a
        Select box's side dragged), still looking the same stretched: arcs / freehand as round, its tumours,
        formulas and text along, a Hz bass's notes and effects' lines too."""
        new = copy.deepcopy(sh)
        fn = lambda b, p: (ab + (b - ab) * kx, ap + (p - ap) * ky)
        new["pts"] = [list(fn(b, p)) for b, p in sh["pts"]]
        r = abs(kx / ky)  # (k = beats per key where it looks round: see rotate)
        if new["kind"] == "arc" or new["kind"] == "free" and "k" in new:
            new["k"] = new.get("k", 1.0) * r
        if new.get("text"):
            new["text"]["k"] = new["text"].get("k", 1.0) * r
        for tm in all_tumours(new):  # (size in keys, the rest in beats)
            tm["k"] *= r
            tm["size"] *= abs(ky)
            tm["length"] *= abs(kx)
            tm["dist"] *= abs(kx)
            tm["ease"] = tm.get("ease", 0.0) * abs(kx)
        moved_formulas(new, fn)
        hz = new.get("hz")
        if hz:  # its notes count in beats from the shape's left edge
            for n in hz.get("tones") or ():
                n["t"] *= abs(kx)
                n["len"] *= abs(kx)
                for s in n.get("to") or ():
                    s["out"] *= abs(kx)
                    s["in"] *= abs(kx)
            for pts in (hz.get("fx") or {}).values():
                for p in pts:
                    p[0] *= abs(kx)
            for name in hz.get("loop") or {}:
                hz["loop"][name] *= abs(kx)
            for pts in (hz.get("amount") or {}).values():
                for p in pts:
                    p[0] *= abs(kx)
        return new

    def shape_label(self, sh):
        if "notes" in sh:
            return tr("app.pasted_notes")
        if sh.get("text"):
            return tr("app.text_2", value=sh.get('name') or '?')
        if is_joined(sh):
            pieces = len(sh.get("gaps", [])) + 1
            return (tr("app.joined_pieces", curve=KINDS['curve'], pieces=pieces) if pieces > 1 else
                    tr("app.joined", curve=KINDS['curve']))
        return tr("app.custom", value=sh.get('name') or '?') if sh["kind"] == "custom" else KINDS[sh["kind"]]

    def note_count(self, sh):
        """Notes a shape makes (quick for spam, which can be millions)."""
        n = (custom_note_count(sh, self.ppq) if sh["kind"] == "custom" else
             funnel_note_count(sh, self.ppq) if sh["kind"] == "funnel" else None)
        return len(self.notes_of(sh)) if n is None else n

    def confirm_big(self, shapes):
        total = sum(self.note_count(sh) for sh in shapes)
        return total <= BIG or messagebox.askyesno(
            tr("app.spiderweb_2"), tr("app.this_makes_about_notes_which_can", total=total),
            icon="warning", parent=self)

    def layout_rows(self):
        """Show the panel's optional parts, always in the same order above the point boxes."""
        rows = ((self.last_row, "last"), (self.line_fill_row, "line_fill"), (self.free_box, "free"),
                (self.tumour_box, "tumour"), (self.pattern_box, "pattern"),
                (self.text_box, "text"), (self.custom_box, "custom"), (self.funnel_box, "funnel"))
        shown = [key for _, key in rows if self._rows[key]]
        if shown == getattr(self, "_rows_shown", None):
            return  # unpacking and packing them again anyway makes the panel flash
        self._rows_shown = shown
        for w, _ in rows:
            w.pack_forget()
        for w, key in rows:
            if self._rows[key]:
                w.pack(fill="x", pady=(4, 0), before=self.points_box)

    def snap_beats(self):
        return snap_beats(self.snap.get(), self.beats)

    def snap_ticks(self):
        """The snap step in ticks (one tick with snapping off)."""
        sb = self.snap_beats()
        return max(1, round(sb * self.ppq)) if sb else 1

    def tool_hotkey(self, key):
        for tool, _, hot in TOOLS + SHAPE_TOOLS:
            if key == hot:
                self.tool.set(tool)

    def toggle_select_tool(self):
        """Double right-click: Select <-> the drawing tool used last."""
        self.tool.set(self.draw_tool if self.tool.get() == "select" else "select")

    def on_tool_change(self):
        if self.tool.get() != "select":
            self.draw_tool = self.tool.get()
        if self.tool.get() != "hz":
            self.hz_start = None
        elif self.hz_window:
            self.hz_window.sync()
        self.roll.cancel_draft()
        self.roll.config(cursor={"select": "arrow", "text": "xterm"}.get(self.tool.get(), "crosshair"))
        self.show_shape_tools()
        self.sync_freehand()
        self.sync_text()
        self.sync_custom()
        self.sync_funnel()
        self.tips.show(TOOL_TOPICS.get(self.tool.get()))

    def tool_topic(self):
        return TOOL_TOPICS.get(self.tool.get(), "select")

    def open_help(self, topic_id=None):
        open_help(self, topic_id or self.tool_topic())

    def show_position(self, text):
        if text:
            self._position = text
        self.update_status()

    def update_status(self):
        parts = [self._position] if self._position else []
        parts.append(tr("app.shapes_notes", n=len(self.shapes), n2=len(self.rendered)))
        if self.channel_mode.get() == "auto" and self.slot_count:
            parts.append(tr("app.tracks_one_channel_each", slot_count=self.slot_count))
        if self.sels:
            shapes = tr("app.shapes_2", n=len(self.sels)) if len(self.sels) > 1 else ""
            parts.append(tr("app.selected_notes", shapes=shapes,
                            value=sum(self.note_counts[i] for i in self.sels if i < len(self.note_counts))))
        self.status.config(text="     ".join(parts))

    def in_drawer(self, e):
        """Keys pressed in the drawer window are the drawer's, not the piano roll's."""
        return bool(self.drawer) and str(e.widget).startswith(str(self.drawer))

    def hotkey(self, fn, main_only=False):
        """A window-wide shortcut that leaves typing boxes alone. main_only: only while the main window has the
        keyboard (not in a pop-up)."""
        def handler(e):
            w = e.widget
            if self.in_drawer(e):
                return None
            if self.hz_window and str(w).startswith(str(self.hz_window)):
                return None  # (the Hz bass window: its own keys only, nothing done to the main piano roll behind)
            if main_only and not (isinstance(w, tk.Misc) and w.winfo_toplevel() is self):
                return None
            if isinstance(w, (tk.Entry, ttk.Entry)) and str(w.cget("state")) != "readonly":
                return None
            fn()
            return "break"
        return handler

    # ------------------------------------------------------------ playback

    def toggle_play(self):
        if self.player.running:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self):
        self.scrub_end()
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("app.spiderweb_2"), str(e))
            return
        err = self.out.open(self.midi_device.get())
        if err:
            messagebox.showerror(tr("app.spiderweb_2"), err)
            return
        # Stops a quarter bar after the last note ends (or after the play line if already past it), rounded up to
        # the next quarter-bar line
        quarter = beats / 4
        last = (int(self.rendered[:, 1].max()) if len(self.rendered) else 0) / ppq
        stop = math.ceil((max(last, self.playhead) + quarter) / quarter - 1e-9) * quarter
        self.player.start(self.rendered, ppq, bpm, self.playhead, stop)
        self.play_btn.config(text=tr("app.stop_space"))
        self.roll.show_playhead(start=True)
        self._play_job = self.after(15, self._play_tick)

    def _play_tick(self):
        self._play_job = None
        self.playhead = self.player.position()
        if not self.player.running:
            self.stop_play()
            return
        self.roll.show_playhead()
        self._play_job = self.after(15, self._play_tick)

    def stop_play(self):
        if self._play_job:
            self.after_cancel(self._play_job)
            self._play_job = None
        if self.player.running or self.player.thread is not None:
            self.playhead = self.player.position()
            self.player.stop()
            self.roll.show_playhead()
            self.schedule_autosave()
        self.play_btn.config(text=tr("app.play_space"))

    def set_playhead(self, beat):
        self.playhead = max(0.0, beat)
        self.roll.show_playhead()
        self.schedule_autosave()

    def scrub(self, t_from, t_to):
        """Right-drag listening: sound the notes under the mouse (tick t_to) and any it just swept past."""
        if not self.out.handle:
            err = self.out.open(self.midi_device.get())
            if err:
                self.status.config(text=err)
                return False
        lo, hi = min(t_from, t_to), max(t_from, t_to)
        now = {}  # (channel, key) -> (start of the latest note there, velocity)
        near = self.roll.visible_notes(lo, hi)
        s_, e_ = near[:, 0], near[:, 1]
        for s, e, p, v, slot, _ in near[((lo <= s_) & (s_ <= hi)) | ((s_ <= t_to) & (t_to < e_))].tolist():
            k = (slot_track_channel(slot)[1], p)
            if k not in now or s > now[k][0]:
                now[k] = (s, v)
        held = self._scrub_held
        for k in [k for k in held if k not in now]:
            self.out.note(*k, 0)
            del held[k]
        for (ch, p), (s, v) in now.items():
            if held.get((ch, p)) != s:  # a new note on this key (back-to-back spam notes each sound)
                if (ch, p) in held:
                    self.out.note(ch, p, 0)
                self.out.note(ch, p, v)
                held[(ch, p)] = s
        self.set_playhead(t_to / self.ppq)
        return True

    def scrub_end(self):
        for ch, p in self._scrub_held:
            self.out.note(ch, p, 0)
        self._scrub_held.clear()

    # ------------------------------------------------------------ undo

    def push_undo(self, state=None, name=None):
        """Remember the shapes (or `state`, shapes saved earlier as JSON) for Ctrl+Z; name = what the step does
        (the History panel)."""
        if self.claw_window:
            self.claw_window.settle()  # (the claw being tried out is kept first, as its own step)
        sc = self._scrub
        if sc and sc["active"]:  # stepping a number box: only its first step takes an undo step
            if sc["pushed"]:
                return
            sc["pushed"] = True
        else:
            self._scrub = None
        self.add_undo_step(state or json.dumps(self.shapes), name)

    def add_undo_step(self, before, name=None):
        """push_undo without its checks (before: the shapes as JSON)."""
        self.drop_empty_step(before)
        self.undo_stack.append((before, name or tr("app.change")))
        del self.undo_stack[:-300]
        # the undone steps are kept aside until this step turns out to change something (a click on a shape that
        # doesn't drag it mustn't throw them away: drop_empty_step brings them back)
        self._redo_kept = (self.redo_stack[:], len(self.undo_stack)) if self.redo_stack else None
        self.redo_stack.clear()
        self._edit_key = None
        self.sync_history()

    def scrub_step(self, gesture, run):
        """run() steps a number box (widgets.Scrub). The steps of one gesture (a label drag, or arrows / wheel on the
        same box with nothing else in between) share one undo step."""
        if not self._scrub or self._scrub["gesture"] is not gesture:
            self._scrub = {"gesture": gesture, "pushed": False, "active": False}
        sc = self._scrub
        sc["active"] = True
        try:
            run()
        finally:
            sc["active"] = False

    def begin_edit(self, key):
        """Typing in one box counts as a single undo step until you move to another box."""
        if key != self._edit_key:
            self.push_undo(name=edit_name(key))
            self._edit_key = key

    def undo(self):
        if self.roll.draft:  # something half drawn (a funnel waiting for its wall, a polyline): just drop it
            return self.roll.cancel_draft()
        self.drop_empty_step(json.dumps(self.shapes))  # (a click that changed nothing isn't a step)
        self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        self._restore(self.redo_stack, self.undo_stack)

    def _restore(self, src, dst):
        if self.claw_window:
            self.claw_window.settle()  # (so Ctrl+Z here takes back the claw being tried out)
        if not src:
            return
        hz_was = self.hz_window and self.hz_window.before_restore()
        self.roll.cancel_draft()
        state, name = src.pop()
        self._redo_kept = None
        dst.append((json.dumps(self.shapes), name))
        self.shapes = json.loads(state)
        self.sels = {i for i in self.sels if i < len(self.shapes)}
        self.parts = set()
        self.stroke = None
        if self.sel not in self.sels:
            self.sel = max(self.sels, default=None)
        self._edit_key = self._scrub = None
        self.sync_panel()
        self.shapes_changed()
        if self.hz_window:
            self.hz_window.after_restore(hz_was)

    def velocity_height(self):
        if self.show_velocity.get() and self.vel_box.winfo_ismapped() and self.vel_box.winfo_height() > 1:
            self._vel_height = self.vel_box.winfo_height()
        return self._vel_height

    def toggle_velocity(self, tip=True):
        if self.show_velocity.get():
            if str(self.vel_box) not in map(str, self.panes.panes()):
                self.panes.add(self.vel_box, stretch="never", height=self._vel_height, minsize=int(50 * self.scale))
            self.update_idletasks()  # lay it out now, so it's drawn fresh before it shows up
            self.vel.redraw()
            if tip:
                self.tips.show("velocity")
        else:
            if self.vel_box.winfo_ismapped() and self.vel_box.winfo_height() > 1:
                self._vel_height = self.vel_box.winfo_height()
            if str(self.vel_box) in map(str, self.panes.panes()):
                self.panes.forget(self.vel_box)
            self.vel.delete("all")  # hidden, it isn't kept up to date: never show an old picture
        self.schedule_autosave()

    def remember_geometry(self, e):
        if e.widget is self and self.wm_state() == "normal":
            self._normal_geometry = self.wm_geometry()

    def on_close(self):
        self.stop_play()
        self.out.close()
        self.autosave()
        self.destroy()
