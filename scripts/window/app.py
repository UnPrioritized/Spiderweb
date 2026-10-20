"""Main window: toolbar, side panel, shapes.
Its other parts: panel_custom.py / panel_funnel.py / panel_tumour.py (+ tumour_window.py) / panel_text.py /
panel_pattern.py (those panel sections), app_playback.py (playing, MIDI out), app_pictures.py (placed pictures),
history.py (undo and the History panel),
project.py (files, autosave, MIDI export), widgets.py (tooltips), font_dialog.py (picking a font)."""

import copy
import itertools
import math
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np

from files.lang import size as lang_size, tr, trn
from notes.areas import ALL_COLOURS, most_colours
from notes.custom import (ROUND, CUSTOM_DEFAULTS, capped_colours, custom_note_count, has_pattern, set_key_top,
                          tracks_apart)
from window import big_ask, look
from window.help import Tips, open_help
from window.updates import Updates
from window.help_texts import BY_ID, TOOL_TOPICS
from notes.engine import (BEATS_FAR, CLASH, KINDS, NO_NOTES, SHAPE_DEFAULTS, as_made, cached_arrays, from_key, fx_notes, merges_unmade, point_names,
                          render, shape_key)
from notes.funnel import FUNNEL_DEFAULTS, funnel_note_count, inside_out, turned_curve
from notes.fx import flip_shape, flipped as fx_flipped, turn_shape, with_turn as fx_turned, with_velocity
from notes.gateflip import (all_flipped, colours_back, offered as gate_offered,
                            turn_velocities, why_not as gate_why_not)
from notes.glue import added as glue_added, glue_box, to_shares as glue_shares
from notes.hzbass import fit_length
from notes.mask import (MASK_DEFAULTS, as_used, can_mask, is_mask, mask_view, picture_like, pinned_slots, silent,
                        slot_pal, slot_rule, tidy_groups)
from notes.merge import has_colours, merge_parts, velocity_reversed as merge_vrev
from notes.sustain import is_sustained
from notes.pattern import moved_formulas
from notes.paths import KEYS
from notes.polygon import POLYGON_DEFAULTS
from notes.ripples import grows_free
from notes.sliced import completed, fresh_marks, moved_by, steps_kept, turned_piece
from notes.smooth import SMOOTH_DEFAULT
from notes.text import TEXT_DEFAULTS
from files.mathexpr import calc, calc_int, fmt
from window.panel_colours import ColoursPanel
from window.panel_mask import MaskPanel
from window.panel_custom import CustomPanel
from window.panel_nested import NestedPanel
from window.panel_tiles import TilesPanel
from window.panel_ripples import RipplesPanel
from window.panel_freehand import FreehandPanel
from window.panel_funnel import FunnelPanel
from window.panel_pattern import PatternPanel
from window.panel_polygon import PolygonPanel
from window.panel_text import TextPanel
from window.claw_window import open_claw
from window.strum_window import open_strum
from window.chop_window import open_chop, quick_chop
from window.hz_preview import clean_settings as clean_preview
from window.builtin_settings import Overload
from window.panel_tumour import TumourPanel
from notes.joined import all_tumours, is_joined
from window.join_split import JoinSplit
from window.between_window import BetweenGroups
from notes.between import (copied as between_copied, keep_pushes, push_sizes, sync_groups, turns as between_turns,
                           whole_groups)
from window.app_pictures import Pictures
from window.app_playback import Playback
from window.history import CH10_OFF, HistoryPanel
from roll.pianoroll import PianoRoll
from roll.roll_draw import painting_order
from files import errors, speed
from files.about import ICONS, VERSION
from files.playback import BUILTIN, DEFAULT_DEVICE, MidiOut, Player
from files.midi_out import PPQ_WARN, track_slots
from files.domino_clip import DOMINO_STARTS, on_clipboard
from files.clipboard import RAW, copy_count, get_text, put_text
from files.share import LONG_LINE, FIND, ShareError, made_by, read_shapes, shapes_line, unpack
from files.project import AUTOSAVE, OUTPUT_DIR, ProjectFiles
from files.snap import DEFAULT_SNAP, snap_beats
from roll.roll_shared import cached_path
from roll.zoombar import add_zoom_bars
from window.snap_picker import SnapPicker
from window import palettes
from window.settings_window import open_settings
from window.tool_picker import ToolPicker
from window.velocity import VelocityPane
from window.velocity_formula import VelocityFormulaBar
from window.widgets import (REDO_KEYS, UNDO_KEYS, ListTip, Scrub, ScrollingSide, StatusLine, Tooltip, bad, fit_dropdowns, good,
                            grid_shown, label_column, remember_good, watch_bad, ShortLabel, WrapRow)


VEL_KEYS = ("vel0", "vel1")
# Common PPQ values. Above 32767 the MIDI header officially means SMPTE timing
PPQS = [2, 4, 8, 16, 24, 48, 96, 120, 144, 192, 240, 384, 480, 768, 960, 1024, 1440, 1920, 2048, 2880,
        3840, 4096, 5760, 7680, 8192, 11520, 12288, 15360, 16384, 23040, 24576, 30720, 32768, 36864,
        46080, 49152, 65535]
TOOLS = [("select", tr("app.select"), "v"), ("slice", tr("app.slice"), "k")]
# in the drawing tools' list (tool_picker.py), in this order
DRAW_TOOLS = [("line", tr("app.line"), "l"), ("poly", tr("app.polyline"), "p"), ("free", tr("app.freehand"), "f"),
              ("curve", tr("app.curve"), "c"), ("arc", tr("app.arc"), "a"), ("custom", tr("app.custom_shape"), "s"),
              ("circle", tr("app.circle"), "o"), ("polygon", tr("app.polygon"), "q"), ("funnel", tr("app.funnel"), "n"),
              ("text", tr("app.text"), "x"), ("hz", tr("app.hz_bass"), "h"), ("picture", tr("app.picture"), "i")]
MAX_POINT_BOXES = 40  # a curve with more points than this lists none in the panel (a line saying so instead)
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


class Stopped(Exception):
    """Notes being made in the background (App.notes_rested) aren't wanted any more."""


def all_notes(shapes, ppq, keys, mode, split, all16, notes_tracks, quiet=False, rounds=False, order=False,
              keep=False):
    """Every shape's notes together, overlaps / channels worked out: (colours each shape asks for, channel 10 used
    (slot k = channel k, slot_track_channel), rendered, slot count). all16: the project's "Use channel 10" (every
    shape may use it, not only pictures). Touches no Tk: also made in a background thread (App.notes_rested).
    quiet: every mask's own notes left out (what the masks show: App.pattern). rounds: the project's "New channels
    each Colours round" (App.rounds_apart). order: its "Channels 1-16 in order" (App.in_order). keep: Settings →
    General "Overlapping shapes keep their colours" (render keep)."""
    team_laps = []
    teams = between_turns(shapes, team_laps)
    # (each Colours shape's notes made with their rounds, then taken off the tracks: render's rounds; a merged shape's
    # parts' rounds too, merge.part_rows; an Add between step = one channel, its round = its group's: in_rounds)
    rounds = rounds and mode == "auto"
    took = [rounds and has_colours(sh) and team is None for sh, team in zip(shapes, teams)]
    wide = all16 and mode == "auto"  # (a Nested / Tiles / Ripples fill with Colours: its pairs go round 16 colours)
    shapes_as = [dict(sh, _most=ALL_COLOURS) if wide and has_pattern(sh) and sh.get("cycle") else sh for sh in shapes]
    got = [notes_tracks(dict(sh, _rounds=True) if r else sh) for sh, r in zip(shapes_as, took)]
    laps = [np.asarray(t, np.int64) // ROUND if r and t is not None else None for (_, t), r in zip(got, took)]
    got = [(n, t % ROUND) if lap is not None else (n, t) for (n, t), lap in zip(got, laps)]
    if rounds:
        laps = [np.full(len(n), tl, np.int64) if tl is not None else lap
                for (n, _), lap, tl in zip(got, laps, team_laps)]
    # (a mask whose own notes don't sound: none, mask.py)
    off = [silent(sh) or quiet and is_mask(sh) for sh in shapes]
    got = [(n[:0], None if t is None else t[:0]) if o else (n, t) for o, (n, t) in zip(off, got)]
    laps = [lap[:0] if o and lap is not None else lap for o, lap in zip(off, laps)]
    # a shape never has more than 15 colours (user: a MIDI player shows no more either): the extra ones are
    # merged into the last (pasted notes keep their tracks)
    # (a Hz bass layer's notes clashing with another's: another channel, not another colour asked for)
    wanted = [0 if t is None or "notes" in sh else len(np.unique(np.asarray(t) % CLASH)) for sh, (_, t) in zip(shapes, got)]
    most = most_colours(all16)
    got = [(n, capped_colours(t % CLASH, most) + t // CLASH * CLASH if w > most else t) for (n, t), w in zip(got, wanted)]
    # (pictures using channel 10 too: one setting for the project, kept with every picture; an Apply mask copy made
    # while channel 10 was used keeps it in use, hunt)
    # (Turn into plain notes / a path's Turn into plain shapes: "solid", other shapes step round its channels)
    pins = [pinned_slots(sh, mode) and ("solid" if sh.get("made") in ("plain", "path") else True) for sh in shapes]
    rules = [slot_rule(sh) if pin else None for sh, pin in zip(shapes, pins)]
    use10 = any(r is True for r in rules)
    # (pinned tracks made with channel 10 skipped stay on their channels while it's used: mask.as_used)
    got = [(n, as_used(t, r, use10 or all16)) for (n, t), r in zip(got, rules)]
    # (with all16 the shapes use channel 10 too, but only as their 16th: render's "all"; in order: "order")
    # (merged shapes and Nested / Tiles / Ripples fills take channels by colour number, not by which colour starts
    # first: a flipped or moved copy keeps its colours, user)
    rendered, slots = render([n for n, _ in got], mode, split, [t for _, t in got], [tracks_apart(sh) for sh in shapes],
                             pins, ("order" if order else "all") if all16 else use10, teams,
                             [sh.get("paste") if "notes" in sh else None for sh in shapes],
                             [is_sustained(sh) or sh.get("held") is True for sh in shapes],
                             [colours_back(sh) for sh in shapes],
                             [bool(sh.get("merge")) or has_pattern(sh) for sh in shapes], laps, keep,
                             [r and not has_colours(sh, turns=True) for sh, r in zip(shapes, took)])
    return wanted, use10 or all16, rendered, slots


class App(ProjectFiles, CustomPanel, NestedPanel, TilesPanel, RipplesPanel, ColoursPanel, MaskPanel, PolygonPanel, FreehandPanel, FunnelPanel, TumourPanel, PatternPanel, TextPanel, JoinSplit, BetweenGroups,
          Playback, Pictures, HistoryPanel, ScrollingSide, tk.Tk):
    def __init__(self, autosave=AUTOSAVE):
        super().__init__()
        errors.install(self)
        look.apply(self)  # (the dark look, before any widget is made)
        palettes.start()  # (the note colours picked in Settings)
        self.title(tr("app.spiderweb", VERSION=VERSION))
        try:  # title bar + taskbar icon; True = every other window (drawer, Help, ...) gets it too
            self.icons = [tk.PhotoImage(file=os.path.join(ICONS, f"icon-{n}.png")) for n in (16, 24, 32, 48, 64, 256)]
            self.iconphoto(True, *self.icons)
        except tk.TclError:  # icons missing: Tk's own icon
            pass
        s = self.scale = self.winfo_fpixels("1i") / 96  # match Windows display scaling
        self.geometry(f"{int(1400 * s)}x{int(820 * s)}")
        # (never bigger than the screen: a big Size on a small screen put the window's edges off it)
        self.minsize(min(int(1000 * s), int(self.winfo_screenwidth() * 0.9)),
                     min(int(600 * s), int(self.winfo_screenheight() * 0.85)))
        self.autosave_path = autosave

        self.shapes = []
        self.sel = None     # the shape the panel shows (the last one clicked)
        self.sels = set()   # every selected shape
        self.parts = set()  # the selected funnel's highlighted lines / curves (roll_funnel.py)
        self.stroke = None  # the selected custom shape's picked stroke (roll_live.py)
        self.part_main = None  # the highlighted part that was clicked (the others are its linked curves)
        self.clipboard = None
        self.seen_clip = None  # the Windows clipboard's copy count at Spiderweb's last copy (shared_clip)
        self.stroke_clip, self.clip_kind, self.stroke_pastes = None, None, 0  # a copied stroke (roll_live.py)
        self.playhead = 0.0  # beat of the play line
        self.out = MidiOut(self.make_live)
        self.font_failed = False  # (make_live: the soundfont couldn't be read)
        self.player = Player(self.out)
        self.play_voices = 1000  # Built-in BASSMIDI's voice limit (user; with the window settings)
        self.play_guard = tk.BooleanVar(self, value=False)  # lowered by itself while overloaded (user: off to start)
        self.play_limiter = tk.BooleanVar(self, value=False)  # (off to start, as in the driver it copies)
        self.domino256 = tk.BooleanVar(self, value=False)  # Settings → General: keys above 127 to / from Domino (mod)
        self.keep_colours = tk.BooleanVar(self, value=False)  # Settings → General: overlapping shapes move whole to
        # the next row of channels (render keep)
        self.overload, self.overload_text = Overload(), ""  # (red on the status line while it can't keep up)
        self.builtin_window = self.voices_entry = None  # (window/builtin_settings.py)
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
        self.strum_window = None
        self.strum_pos = ""  # (the same for the strum window)
        self.chop_window = None
        self.chop_pos = ""  # (and the chop window)
        self.path_window = None  # Copy along a path (path_window.py)
        self.sustain_window = None  # Sustain… (sustain_window.py)
        self.subtract_window = None  # Gate subtract… (subtract_window.py)
        self.merge_window = None  # Gate sensitive merge… (merge_window.py)
        self.path_pos = ""
        self.hz_window = None  # the Hz bass window (hz_window.py)
        self.image_window = None  # image to notes (image_window.py; opened by the Picture tool)
        self.image_pos = ""  # its size and place
        self.image_last = None  # (picture file, settings) last used there, so it opens with them again
        self.image_rate = 4e-6  # seconds a grid cell takes to make there (measured on each big one: its question)
        self._pictures = {}  # placed pictures' files read: (path, mtime, size) -> picture.Picture (picture_for)
        self.picture_owners, self.picture_pal = np.zeros(0, bool), None  # (shapes_changed: for the piano roll)
        self.picture_use10 = False  # (shapes_changed: pictures use channel 10 too)
        self.hz_clip = None  # notes copied in it (HzWindow.copy_notes)
        self.hz_bend_clip = None  # a note's bend line copied in it (HzBend.copy_bend)
        self.hz_copied = None  # the Windows clipboard's copy count at its last copy (a later Domino copy wins Ctrl+V)
        self.hz_pos = ""  # its size and place ("WxH+x+y", remembered in the autosave)
        self.hz_view = None  # its zoom + scroll when last closed (HzView.view_state, remembered in the autosave)
        self.hz_fx_h = 0  # its effects pane's height in pixels, dragged by its top edge (0 = as it starts)
        self.hz_loud_h = 0  # ... its loudness pane's (hz_loud.py)
        self.hz_start = None  # the beat picked with the Hz bass tool for a new Hz bass (roll_hz.py)
        self.hz_defaults = {"lo": 48, "hi": 58}  # the keys a new Hz bass repeats
        self.hz_preview = clean_preview({})  # the Hz bass preview's settings (hz_preview.py; with the window's)
        self.synth = None  # the built-in synth (files/synth.py), started when the preview or Built-in BASSMIDI is first used
        self.rendered, self.slot_count = NO_NOTES, 0  # (start, end, pitch, velocity, slot, owner) rows
        self.drawn, self.mask_covered = NO_NOTES, set()  # the notes as painted with the masks (mask.mask_view)
        self.pattern = NO_NOTES  # rendered without the masks' own notes: what masks show (= rendered but in Single)
        self.note_counts = []  # notes per shape in rendered
        self.notes_late = False  # the notes are behind the shapes (a drag going on: see shapes_changed)
        self.scrubbing = False  # a number box's label is being dragged (widgets.Scrub): slow notes wait too
        self._merge_time, self._merges_owed = 0.0, False  # (remake_merges' last time; put off while scrubbing)
        self.held_scrub = None  # that Scrub (shortcuts / Delete wait, Ctrl+Z cancels it)
        self._trimmed = None  # the oldest steps the 300 limit dropped for the last step (add_undo_step)
        self.rendered_pts = []  # each shape's first point when rendered was made
        self.ppq, self.beats = 960, 4
        self.undo_stack, self.redo_stack = [], []
        self._redo_kept = None  # (push_undo)
        self._edit_key = None
        self._scrub = None  # the number box being stepped (scrub_step)
        self._loading = False
        self._opening = False  # (load_file filling in the project boxes)
        self._autosave_job = None
        self._autosave_failed = False  # (told once until it works again: ProjectFiles.autosave)
        self._saved_shapes = None  # the shapes as last saved in / opened from a project file (json)
        self.big_skip = set()  # big_ask actions ticked "Don't ask again until Spiderweb is closed"
        self._notes_cache = {}
        self.colours_wanted = []  # per shape, how many colours (tracks) its notes ask for (shapes_changed)
        self.leave_boxes = {}  # side panel box -> what takes its number when it's left (widgets.leave_box)
        self._notes_worked = 0  # shapes whose notes had to be worked out (not remembered)
        self._merge_keys = {}  # merged shapes' parts' settings when last seen (remake_merges)
        self._notes_time = 0.0  # how long that took the last time
        self._mask_time = 0.0  # ... and the masks' preview (mask.mask_view)
        self._late_notes = None  # while dragging: notes left until the mouse rests
        self.shapes_gen = 0  # +1 at every shapes_changed (windows working out something slow from the shapes: same?)
        self._coming = None  # notes being made in the background while a drag rests (notes_rested)
        self._position = None
        self._normal_geometry = None

        self.tool = tk.StringVar(value="select")  # (each tool's tip shows when it's picked, see help.py)
        self.tips = Tips(self)
        self.updates = Updates(self)
        self.help_window = None
        self.live = tk.BooleanVar(value=False)  # drawing tools draw into one custom shape (roll_live.py)
        self.mask = tk.BooleanVar(value=False)  # what's drawn becomes a mask (mask.py)
        self.mask_preview = tk.BooleanVar(value=True)  # masks hide what's outside them (off = the pattern drawn whole)
        self.mask_defaults = dict(MASK_DEFAULTS)  # a new mask's settings: the last ones set in the panel
        self.draw_tool = "line"  # the drawing tool a double right-click goes back to
        self.snap = tk.StringVar(value=DEFAULT_SNAP)
        self.hz_snap = tk.StringVar(value=DEFAULT_SNAP)  # the Hz bass window's own snap
        self.hz_line = tk.BooleanVar(value=True)  # the Hz bass window shows its red line
        self.hz_fx = tk.BooleanVar(value=False)  # the Hz bass window shows its effects pane
        self.hz_layers = tk.BooleanVar(value=False)  # ... and its layers strip (off to start: plain Hz bass users, user)
        self.hz_tools = {}  # ... its tools button: {"shown": the drawing tool on it, "pins": [...], "tool": in use}
        self.hz_loud = tk.BooleanVar(value=False)  # ... and its loudness pane (off to start, user)
        self.show_lines = tk.BooleanVar(value=True)
        self.show_notes = tk.BooleanVar(value=True)
        self.channel_mode = tk.StringVar(value="single")
        self.channel_split = "key"  # what counts as an overlap for Multi channel (engine.SPLITS)
        self.use_ch10 = tk.BooleanVar(value=False)  # Multi channel uses channel 10 too: 16 colours (all16)
        self.ch10_order = tk.BooleanVar(value=False)  # with Use channel 10: channels 1-16 in order (in_order)
        self.new_rounds = tk.BooleanVar(value=False)  # each Colours round on channels of its own (rounds_apart)
        self.pad_rounds = tk.BooleanVar(value=True)  # with it: empty tracks for a round's unused channels (pad_tracks)
        self.channels_folded = tk.BooleanVar(value=False)  # Project's Channels rows folded to one grey line (autosave)
        self._used_tracks = (None, 0)  # (rendered, how many slots have notes: update_status)
        self.keys = 128  # the project's key range: 0 .. keys - 1 (paths.KEYS)
        set_key_top(self.keys)
        self.keys_var = tk.StringVar(value=str(KEYS[0]))
        self.edo31 = tk.BooleanVar(value=False)  # 256 keys shown as 31-EDO (the look only: edo())
        self.edo31_audio = tk.BooleanVar(value=False)  # ... and played that way (Built-in BASSMIDI)
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
        self.project_entries = {}  # BPM, Beats per bar
        self.point_rows = []

        ttk.Style(self).configure("Bad.TEntry", foreground=look.ERROR)
        ttk.Style(self).configure("Bad.TCombobox", foreground=look.ERROR)
        remember_good(self)  # (a wrong value typed in a number box goes back to its last good one)
        self._build()
        self.restore_window()
        self.load_autosave()
        self.sync_panel()
        self.after(800, lambda: self.tips.show("welcome"))  # the first time Spiderweb starts
        self.updates.start()  # (What's new after an update; asks about / looks for updates)
        speed.start()  # (the compiled loops for lots of notes, if Numba is there: loaded in the background)

        for k, v in self.pvar.items():  # (the output file name changes no notes: typing it remade them all, slow)
            v.trace_add("write", lambda *_, k=k: self.schedule_autosave() if k == "output" else self.on_project_change(True))
        for k, v in self.fvars.items():
            v.trace_add("write", lambda *_, k=k: self.on_field(k))
        self.end_dot.trace_add("write", lambda *_: self.on_end_dot())
        self.tool.trace_add("write", lambda *_: self.on_tool_change())
        if self.tool.get() != "select":  # the tool from last time (restore_window)
            self.on_tool_change()
        # (Live shape also shows the selected custom shape's stroke points)
        self.live.trace_add("write", lambda *_: (self.sync_custom(), self.schedule_autosave(),
                                                  self.roll.request_redraw(),
                                                  self.live.get() and self.tips.show("live")))
        self.mask.trace_add("write", lambda *_: (self.schedule_autosave(), self.roll.request_redraw()))
        self.mask_preview.trace_add("write", lambda *_: (self.schedule_autosave(), any(map(is_mask, self.shapes)) and
                                                         self.shapes_changed()))
        self.snap.trace_add("write", lambda *_: self.roll.request_redraw())
        self.hz_snap.trace_add("write", lambda *_: (self.hz_window and self.hz_window.redraw(),
                                                    self.schedule_autosave()))
        # Tk hands Alt (pressed alone, or with a key) and F10 to the Windows window menu, which then eats the next
        # key or beeps (e.g. after an Alt-drag); only Alt+F4 (close) and Alt+Space (window menu) still go there
        for seq in ("<Key-Alt_L>", "<KeyRelease-Alt_L>", "<Key-Alt_R>", "<KeyRelease-Alt_R>",
                    "<Key-F10>", "<KeyRelease-F10>"):
            self.tk.call("bind", "all", seq, "")
        for seq in ("<Alt-Key>", "<Alt-KeyRelease>"):
            self.tk.call("bind", "all", seq, 'if {"%K" in {F4 space}} {tk::WinMenuKey %W %N}')
        self.bind_all("<F1>",lambda e: None if self.in_drawer(e) else self.open_help())
        for keys, fn in ((UNDO_KEYS, lambda e: self.key_undo(e)), (REDO_KEYS, lambda e: self.key_undo(e, True)),
                         (("<Control-s>",), lambda e: self.save_project())):
            for key in keys:
                self.bind_all(key, lambda e, fn=fn: None if self.in_drawer(e) else fn(e))
        self.bind_all("<space>", self.hotkey(self.toggle_play, main_only=True, while_held=True))  # (pop-ups: not the main playback)
        for k in ("<Control-x>", "<Control-X>"):  # (main window only: Ctrl+X in Help / pop-ups cut the shapes behind)
            self.bind_all(k, self.hotkey(self.cut_selected, main_only=True))
        # Delete pressed while a toolbar / panel button has the keyboard (Ctrl+A works there): the piano roll's own
        # Delete, with its mid-drag / velocity line rules (the roll handles its own key itself)
        self.bind_all("<Delete>", lambda e: None if e.widget is self.roll else
                      self.hotkey(lambda: self.roll.on_key(e), main_only=True, while_held=True)(e))
        for keys, fn in (("Control-c Control-C", self.copy_selected),
                         ("Control-Shift-c Control-Shift-C", self.copy_to_domino),
                         ("Control-g Control-G", self.join_selected),
                         ("Control-Shift-g Control-Shift-G", self.split_selected),
                         ("Control-r Control-R", self.turn_into_live),
                         ("Alt-w Alt-W", lambda: open_claw(self)), ("Alt-s Alt-S", lambda: open_strum(self)),
                         ("Alt-u Alt-U", lambda: open_chop(self)), ("Control-u Control-U", lambda: quick_chop(self)),
                         ("Control-v Control-V", self.paste), ("Control-h Control-H", lambda: self.flip(True)),
                         ("Control-j Control-J", lambda: self.flip(False)), ("Control-a Control-A", self.select_all),
                         ("Control-Left", lambda: self.rotate(False)), ("Control-Right", lambda: self.rotate(True))):
            for k in keys.split():
                self.bind_all(f"<{k}>", self.hotkey(fn))
        self._device_was = self.midi_device.get()
        self.midi_device.trace_add("write", lambda *_: self.midi_device_picked())
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
        for key, label, hot in TOOLS:
            b = ttk.Radiobutton(bar, text=f"{label} ({hot.upper()})", value=key, variable=self.tool,
                                style="Toolbutton")
            b.pack(side="left", padx=1)
            Tooltip(b, BY_ID[TOOL_TOPICS[key]]["tip"])
        line = ttk.Separator(bar, orient="vertical")
        line.pack(side="left", fill="y", padx=(3, 3), pady=2)
        # the drawing tool picked last + the list; its pinned tools' buttons go in the bar too (fit_toolbar)
        self.tool_picker = ToolPicker(self, bar, DRAW_TOOLS, inline=True)
        self.tool_picker.frame.pack(side="left", padx=1)
        self._tools_together = ((line, self.tool_picker.frame),)  # (wrapped: the line stays before the tools)
        self._tools_line = line
        live = ttk.Checkbutton(bar, text=tr("app.live_shape_g"), variable=self.live)
        live.pack(side="left", padx=(12, 0))
        Tooltip(live, BY_ID["live"]["tip"])
        b = ttk.Checkbutton(bar, text=tr("mask.tick"), variable=self.mask,
                            command=lambda: self.mask.get() and self.tips.show("masks"))
        b.pack(side="left", padx=(8, 0))
        Tooltip(b, tr("mask.tick_tip"))
        b = ttk.Checkbutton(bar, text=tr("mask.preview"), variable=self.mask_preview)
        b.pack(side="left", padx=(4, 0))
        Tooltip(b, tr("mask.preview_tip"))
        bar = self.option_bar = ttk.Frame(top)
        self._toolbar_rows = self._toolbar_wrap = None
        top.bind("<Configure>", lambda e: self.fit_toolbar())
        snap = ttk.Label(bar, text=tr("app.snap"))
        snap.pack(side="left", padx=(0, 4))
        snap_box = SnapPicker(self, bar, self.snap).button
        snap_box.pack(side="left")
        self._toolbar_together = ((snap, snap_box),)  # (wrapped onto more lines: kept side by side)
        redraw = lambda: (self.roll.request_redraw(), self.schedule_autosave())
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
        ttk.Button(bar, text=tr("app.undo"), command=self.undo_roll).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("app.redo"), command=lambda: self.undo_roll(True)).pack(side="left", padx=(4, 0))
        self.play_btn = ttk.Button(bar, text=tr("app.play_space"), width=14, command=self.toggle_play, takefocus=False)
        self.play_btn.pack(side="left", padx=(12, 0))
        self.voices_var = tk.StringVar(value=str(self.play_voices))
        # Help + Settings: always at the top right corner, whatever row the buttons are on (user)
        corner = self.corner = ttk.Frame(top)
        corner.grid(row=0, column=2, sticky="ne", padx=(8, 0))  # (at the top: the tools may wrap onto more lines)
        help_btn = ttk.Button(corner, text=tr("app.help_f1"), command=self.open_help, takefocus=False)
        help_btn.pack(side="left", padx=(0, 4))
        Tooltip(help_btn, tr("app.every_tip_searchable_opens_at_the"))
        b = self.gear = ttk.Button(corner, text="⚙", width=3, command=lambda: open_settings(self), takefocus=False)
        b.pack(side="left")
        Tooltip(b, tr("settings.button_tip"))

        self.status = StatusLine(self, text="", padding=(6, 2), font=look.font(9))
        self.status.pack(side="bottom", fill="x")
        # (Built-in BASSMIDI's red "Overloaded" while it can't keep up: the status line's right end, placed only
        # then (show_overload), so nothing moves, user 2026-10-18)
        self.overload_label = ttk.Label(self.status, text="", foreground=look.ERROR, font=look.font(9))

        side = self._build_side()
        self._build_project(side)
        self._build_history(side)
        self._build_shape_list(side)
        self._build_shape_settings(side)
        self.settings.bind("<Configure>", lambda e: self.after_idle(self.fit_side), add="+")

        s = self.scale
        self.panes = tk.PanedWindow(self, orient="vertical", sashwidth=int(6 * s), sashrelief="raised",
                                    bd=0, bg=look.SASH, opaqueresize=True)
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
        # (one line: beside the Formula boxes it ends in "…", the whole hint in a tip; hunt 2026-10-19: wrapped,
        # it was a thin column and the graph jumped)
        self.vel_hint = ShortLabel(vbar, least=8, text=tr("app.ctrl_flat_shift_snap_enter_done"),
                                   foreground=look.HINT, font=look.font(8))
        self.vel_hint.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.vel_tool.trace_add("write", lambda *_: self.show_vel_formula())
        self.vel = VelocityPane(self.vel_box, self, s)
        self.vel.pack(fill="both", expand=True)  # the pane itself is added when it's turned on (off at first)

    def show_vel_formula(self):
        if self.vel_tool.get() == "formula":
            self.vel_formula_bar.fill_list()
            self.vel_formula_bar.pack(side="left", before=self.vel_hint)
        else:
            self.vel_formula_bar.pack_forget()

    def fit_toolbar(self):
        """The view settings and buttons: right of the tools when both fit in the window's width, else below them;
        either part wraps onto more lines when even its own row is too narrow (pinned tools, wider fonts)."""
        top = self.toolbar
        room = top.winfo_width() - 12
        if room <= 1:
            return
        if self._toolbar_wrap is None:
            again = lambda: self.winfo_exists() and self.fit_toolbar()  # noqa: E731  (a button grew / shrank)
            tp = self.tool_picker

            def tool_groups():  # (the pinned tools' buttons right after the picker, each wrapping on its own; room
                out = []  # kept for the picker's longest tool name and the closed Circle / Polygon group)
                for g in self._toolbar_wrap[0].groups:
                    out.append((g, tp.label_room() if tp.frame in g else 0))
                    if tp.frame in g:
                        out += tp.parts()
                return out
            self._toolbar_wrap = (WrapRow(self.tool_bar, together=self._tools_together, changed=again,
                                          items=tool_groups, drop=(self._tools_line,)),
                                  WrapRow(self.option_bar, together=self._toolbar_together, changed=again))
        tools, options = self._toolbar_wrap
        corner = self.corner.winfo_reqwidth() + int(8 * self.scale)
        rows = 1 if tools.natural() + options.natural() + corner + int(24 * self.scale) <= room else 2
        tools.fit(10 ** 6 if rows == 1 else room - corner)
        options.fit(10 ** 6 if rows == 1 else room)
        if rows == self._toolbar_rows:
            return
        self._toolbar_rows = rows
        self.tool_bar.grid(row=0, column=0, sticky="w")
        if rows == 1:
            # (columnspan / pady set again: grid keeps the two-row ones, which put it over the gear's column)
            self.option_bar.grid(row=0, column=1, columnspan=1, sticky="e", pady=0)
        else:
            # (all 3 columns: the Help + Settings corner sits on row 0 only, so row 1 gets the whole width)
            self.option_bar.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _build_side(self):
        """The side panel: scrolls (scrollbar, mouse wheel) when everything in it doesn't fit; otherwise the help
        text at the bottom takes the room that's left."""
        side = self.make_side(int(lang_size("size.panel_width", 330) * self.scale))  # (a translation
        self._side_wheel_at = 0.0  # may widen it)
        own = self.bind_class("TCombobox", "<MouseWheel>")  # (a dropdown's own wheel: next / last choice)

        def drop_wheel(e):
            if self.side_gliding(e.widget):
                return self.side_wheel(e, dropdown=True) or "break"
            if own:
                self.tk.eval(own.replace("%W", str(e.widget)).replace("%D", str(e.delta)))
            return None
        self.bind_class("TCombobox", "<MouseWheel>", drop_wheel)
        fit_dropdowns(self)
        return side

    def side_wheel(self, e, dropdown=False):
        """The mouse wheel over the side panel scrolls it (lists scroll themselves; dropdowns and number boxes
        with the keyboard change, unless the panel is already scrolling: side_gliding)."""
        if self.scroll_side(e, dropdown):
            self._side_wheel_at = time.monotonic()

    SIDE_GLIDE = 0.5  # seconds: a wheel turned within this of the last panel scroll keeps scrolling the panel (user)

    def side_gliding(self, widget):
        """The side panel is being scrolled with the wheel: a dropdown / number box the mouse passes over scrolls
        on with it instead of changing (user: like web pages; still the wheel stops = they change again)."""
        return (str(widget).startswith(str(self.side_canvas)) and self.side_bar.winfo_ismapped()
                and time.monotonic() - self._side_wheel_at < self.SIDE_GLIDE)

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
                # a typed PPQ counts on Enter / leaving the box / a pick from the list (user: each key remade the
                # notes and rounded the tool windows' ticks at every half-typed PPQ); a wrong one: back to the last good
                for seq in ("<Return>", "<FocusOut>", "<<ComboboxSelected>>"):
                    self.ppq_box.bind(seq, lambda e: self.on_project_change(True, ppq_done=True) if self.ppq_ok()
                                      else self.pvar["ppq"].set(str(self.ppq)), add="+")
                # Many MIDI programs can't open a file with a PPQ of 32767 or more (above that it's SMPTE timing anyway)
                self.ppq_warning = ttk.Label(row, text=tr("app.many_programs_can_t_open_this"), foreground=look.ERROR,
                                             font=look.font(8))
                Tooltip(self.ppq_warning, tr("app.a_ppq_of_or_more_many", PPQ_WARN=PPQ_WARN))
            else:
                cell = ttk.Frame(box)
                cell.grid(row=r, column=1, sticky="w", padx=5)
                e = self.project_entries[key] = ttk.Entry(cell, textvariable=self.pvar[key], width=6)
                e.pack(fill="both", expand=True)
                watch_bad(e)
                boxes.append(e)
                Scrub(self, [(e, self.pvar[key], None)], (1, 10, 0.1) if key == "bpm" else (1, 1, 1),
                      *((4, 100000) if key == "bpm" else (1, 32)), label=lb)
            r += 1
        ttk.Label(box, text=tr("app.keys")).grid(row=r, column=0, sticky="w", pady=1)
        kf = ttk.Frame(box)
        kf.grid(row=r, column=1, sticky="w", padx=5)
        b = ttk.Combobox(kf, textvariable=self.keys_var, values=[str(k) for k in KEYS], state="readonly",
                         width=7)
        b.pack(side="left")
        b.bind("<<ComboboxSelected>>", lambda e: self.on_project_change())
        Tooltip(b, tr("app.128_the_standard_keys_0_127"))
        self.edo_ticks = ttk.Frame(kf)  # (shown with 256 keys only)
        t = ttk.Checkbutton(self.edo_ticks, text=tr("app.edo31"), variable=self.edo31, command=self.edo_ticked)
        t.pack(side="left", padx=(6, 0))
        Tooltip(t, tr("app.edo31_tip"))
        self.edo_audio_tick = ttk.Checkbutton(self.edo_ticks, text=tr("app.edo31_audio"), variable=self.edo31_audio,
                                              command=self.edo_ticked)
        self.edo_audio_tick.pack(side="left", padx=(4, 0))
        self.edo_audio_tip = Tooltip(self.edo_audio_tick, tr("app.edo31_audio_tip"))
        ttk.Style().configure("Warn.TCheckbutton", foreground=look.WARN)  # (Audio on, another MIDI out: edo_warn)
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
        # (user 2026-10-18: the header folds the rows; folded = one grey line of short words for what's on)
        # (the arrow in a spot of its own: ▶ and ▼ differ in width, the word must not jump)
        head = ttk.Frame(box)
        head.grid(row=r, column=0, sticky="nw", pady=(3, 0))
        self.channels_head = ttk.Label(head, width=2, cursor="hand2")
        self.channels_head.pack(side="left")
        name = ttk.Label(head, text=tr("app.channels"), cursor="hand2")
        name.pack(side="left")
        ch = self.channels_rows = ttk.Frame(box)
        ch.grid(row=r, column=1, sticky="w", padx=5, pady=(3, 0))
        self.channels_short = ttk.Label(box, foreground=look.HINT, cursor="hand2")
        self.channels_short.grid(row=r, column=1, sticky="w", padx=5, pady=(3, 0))
        for w in (self.channels_head, name, self.channels_short):
            Tooltip(w, tr("app.channels_fold_tip"))
            w.bind("<Button-1>", lambda e: self.channels_folded.set(not self.channels_folded.get()))
        # (a long line wraps under itself instead of being cut at the panel's edge)
        # (reqwidth: winfo_width is 1 until the window is drawn)
        box.bind("<Configure>", lambda e: self.channels_short.config(
            wraplength=max(e.width - head.winfo_reqwidth() - int(30 * self.scale), 50)), add="+")
        for var in (self.channel_mode, self.use_ch10, self.ch10_order, self.new_rounds, self.pad_rounds,
                    self.keep_colours, self.channels_folded):
            var.trace_add("write", lambda *a: self.sync_channels_fold())
        for value, text, tip in CHANNEL_CHOICES:
            b = ttk.Radiobutton(ch, text=text, value=value, variable=self.channel_mode,
                                command=self.on_channel_mode)
            b.pack(anchor="w")
            Tooltip(b, tip)
        names = [name for _, name in SPLIT_CHOICES]
        self.split_box = ttk.Combobox(ch, state="readonly", values=names, width=max(map(len, names)))
        # (not packed here: shapes_changed shows it under Multi channel; with no autosave it never runs at start)
        self.split_box.bind("<<ComboboxSelected>>", lambda e: (self.shapes_changed(), self.sync_channels_fold()))
        self.split_box.current(0)
        Tooltip(self.split_box, SPLIT_TIP)
        # (shown under Multi channel too, user: most MIDI players play channel 10 as drums, some people only want
        # the 16th colour)
        self.ch10_tick = ttk.Checkbutton(ch, text=tr("app.use_ch10"), variable=self.use_ch10,
                                         command=self.on_use_ch10)
        Tooltip(self.ch10_tick, tr("app.use_ch10_tip"))
        # (user 2026-10-18: shown only while Use channel 10 is ticked; off = channel 10 last, so ticking that changes
        # no colour)
        self.order_tick = ttk.Checkbutton(ch, text=tr("app.ch10_order"), variable=self.ch10_order,
                                          command=self.on_channel_mode)
        Tooltip(self.order_tick, tr("app.ch10_order_tip"))
        # (user 2026-10-17: a Colours round's channels never used again, so each round can be edited alone elsewhere)
        self.rounds_tick = ttk.Checkbutton(ch, text=tr("app.new_rounds"), variable=self.new_rounds,
                                           command=self.on_channel_mode)
        Tooltip(self.rounds_tick, tr("app.new_rounds_tip"))
        # (user 2026-10-18: shown only while it's ticked; off = only tracks with notes are written, channels kept)
        self.pad_tick = ttk.Checkbutton(ch, text=tr("app.pad_rounds"), variable=self.pad_rounds,
                                        command=self.update_status)
        Tooltip(self.pad_tick, tr("app.pad_rounds_tip"))
        self.sync_channels_fold()
        r += 1
        # (MIDI out: only in Settings → Sound now, user 2026-10-18; "Overloaded" is beside Play)
        names = [name for _, name in DOMINO_STARTS]
        ttk.Label(box, text=tr("app.domino_start")).grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.domino_box = ttk.Combobox(box, state="readonly", values=names)
        self.domino_box.grid(row=r, column=1, sticky="ew", padx=5, pady=(6, 0))
        self.domino_box.current(0)
        self.domino_box.bind("<<ComboboxSelected>>", lambda e: self.schedule_autosave())
        Tooltip(self.domino_box, tr("app.where_copied_and_pasted_notes_start"))
        domino = ttk.Frame(box)
        domino.grid(row=r + 1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(domino, text=tr("app.domino_row")).pack(side="left")
        for text, tip, command, gap in (("app.domino_export", "app.domino_export_tip", self.export_dms, 0),
                                        ("app.domino_copy", "app.ctrl_shift_c_copies_the_selected",
                                         self.copy_to_domino, 4),  # (the same gap as Open… / Save…)
                                        ("app.domino_paste", "app.domino_paste_tip",
                                         lambda: self.paste_from_domino(), 0)):  # (user: missed it)
            b = ttk.Button(domino, text=tr(text), command=command)
            b.pack(side="right", padx=gap)
            Tooltip(b, tr(tip))
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
        self.listbox = tk.Listbox(row, height=8, activestyle="none", exportselection=False, font=look.font(9),
                                  selectmode="extended")
        sb = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_list_select)
        ListTip(self.listbox)
        # hand the keys back to the roll, so Space plays instead of picking a list row
        self.listbox.bind("<ButtonRelease-1>", lambda e: self.roll.focus_set(), add="+")
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text=tr("app.duplicate"), command=self.duplicate).pack(side="left")
        ttk.Button(btns, text=tr("app.delete"), command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(btns, text=tr("app.delete_all"), command=self.delete_all).pack(side="left")

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
            watch_bad(self.fentries[key])
        # dragging "Velocity" moves both ends together
        Scrub(self, [(self.fentries[k], self.fvars[k], None) for k in ("vel0", "vel1")], (1, 10, 1), 1, 127, label=lb)
        ttk.Label(v, text=tr("app.start_end"), foreground=look.HINT).pack(side="left", padx=(5, 0))
        self.env_note = ttk.Label(self.settings, text=tr("app.velocity_drawn_in_the_velocity_pane"), foreground=look.HINT,
                                  font=look.font(8),
                                  wraplength=int(300 * self.scale), justify="left")
        self._build_colours()
        self._build_mask()
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
        # their boxes start at one place, the funnel's too; Colours' rows start under its dropdown, like Inside's.
        # (Not "Last note": 7 px wider, it pushed Tiles' rows past the panel's edge; it never shows with them)
        widest = label_column([lb, self.colours_word, self.shape_word, self.inside_word])
        self.funnel_box.columnconfigure(0, minsize=widest)
        self.cycle_rows_pad = widest + 5  # (panel_colours sync_colours packs the rows there)
        self.cycle_every_row.pack_configure(padx=(self.cycle_rows_pad, 0))
        label_column(self.cycle_words)

    def build_points(self):
        """Tick/pitch boxes for each point of the selected line or curve."""
        for w in self.points_box.winfo_children():
            w.destroy()
        self.point_rows = []
        sh = self.selected()
        names = point_names(sh) if sh and len(self.sels) == 1 else None
        if not names:
            return
        if len(names) > MAX_POINT_BOXES:  # (a box pair per point took ~0.02 s: a fitted curve's hundreds froze it)
            ttk.Label(self.points_box, text=tr("app.too_many_points", n=f"{len(names):,}"), foreground=look.HINT,
                      wraplength=int(300 * self.scale), justify="left").grid(row=0, column=0, sticky="w")
            return
        for col, text in enumerate((tr("app.point"), tr("app.tick"), tr("app.pitch"))):
            ttk.Label(self.points_box, text=text, foreground=look.HINT).grid(row=0, column=col, sticky="w", padx=(0, 5))
        for i, name in enumerate(names):
            tv, pv = tk.StringVar(), tk.StringVar()
            ttk.Label(self.points_box, text=name).grid(row=i + 1, column=0, sticky="w", pady=1, padx=(0, 5))
            te = ttk.Entry(self.points_box, textvariable=tv, width=10)
            te.grid(row=i + 1, column=1, sticky="w", padx=(0, 5))
            pe = ttk.Entry(self.points_box, textvariable=pv, width=7)
            pe.grid(row=i + 1, column=2, sticky="w")
            self.point_rows.append((tv, pv, te, pe))
            watch_bad(te)
            watch_bad(pe)
            # ticks: a snap step (Ctrl = one tick), pitch: a key (Shift = an octave)
            Scrub(self, [(te, tv, None)], lambda: (self.snap_ticks(), 4 * self.snap_ticks(), 1), 0)
            Scrub(self, [(pe, pv, None)], (1, 12, 0.1), 0, KEYS[1] - 1)
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
        part = self.between_part(sh) if sh else None
        if part:
            title += part
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
        self.sync_colours()
        self.sync_mask()
        self.sync_freehand()
        self.sync_tumour()
        self.sync_pattern()
        self.sync_text()
        self.sync_custom()
        self.sync_funnel()
        self.sync_list_selection()
        self.sync_line_fill()
        for w in (self.claw_window, self.strum_window, self.chop_window, self.path_window, self.sustain_window,
                  self.subtract_window, self.merge_window):
            if w:
                w.sync()
        if self.hz_window:
            self.hz_window.sync()
        if self.sel is not None and self.row_of(self.sel) is not None:
            self.listbox.see(self.row_of(self.sel))
        # the first time one is selected: how it's edited
        kinds = {self.shapes[i]["kind"] for i in self.sels}
        for kind, topic in (("custom", "custom_edit"), ("funnel", "funnel_curves"), ("free", "straighten")):
            if kind in kinds:
                self.tips.show(topic)
                break

    def sync_list_selection(self):
        self.listbox.selection_clear(0, "end")
        for r, row in enumerate(self.shape_rows()):
            if self.sels.intersection(row):
                self.listbox.selection_set(r)

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
            bad(self.fentries[key], typing=True)  # (red; back to its good value on Enter / leaving it)
            return
        good(self.fentries[key])
        tgts = self.targets()
        if all(t[key] == value and not t.get("vel_env") for t in tgts):
            return
        if self.sels:
            self.begin_edit(("field", tuple(sorted(self.sels)), key))
        for t in tgts:
            t[key] = value
            t.pop("vel_env", None)
            t.pop("own_vel", None)  # pasted notes: their own velocities are replaced
            if t.get("fx"):  # (and those of its note tool pages, fx.py)
                t["fx"] = with_velocity(t["fx"], [[0.0, t["vel0"]], [1.0, t["vel1"]]])
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
        # (tick 0 on, pitch 0-255 at 128 keys too: user; ticks only as far as a saved file keeps a shape)
        for var, entry, hi in ((tv, te, BEATS_FAR * self.ppq), (pv, pe, KEYS[1] - 1)):
            try:
                value = float(calc(var.get()))
                if not 0 <= value <= hi:
                    raise ValueError
                values.append(value)
                good(entry)
            except ValueError:
                bad(entry, typing=True)
        if len(values) < 2:
            return
        sh = self.selected()
        new = [values[0] / self.ppq, values[1]]
        if sh["pts"][i] != new:
            trial = dict(sh, pts=[new if j == i else p for j, p in enumerate(sh["pts"])])
            if not self.point_ok(sh, trial):
                return bad(te, typing=True)  # (a stray zero: many notes asked first, like the Gate box)
            self.begin_edit(("point", self.sel, i))
            axis = self.roll.held_axis(sh)
            sh["pts"][i] = new
            if self.roll.keep_symmetric(sh, i, axis):
                self.sync_points()  # the other half followed
            self.shapes_changed()

    def point_ok(self, sh, trial):
        """A point typed on a spam custom shape / funnel: past big_ask's 1 M notes = asked first. Counted only when
        the most it could make is past that (counting a funnel 10^9 ticks long took 12 s by itself); far past it
        the most is asked about."""
        if sh["kind"] not in ("custom", "funnel") or sh.get("merge") or trial.get("hz"):
            return True
        beats = [p[0] for p in trial["pts"]]
        keys = [p[1] for p in trial["pts"]]
        gate = min(sh.get("gate", 1), (sh.get("range") or {}).get("to", math.inf))
        most = (int((max(beats) - min(beats)) * self.ppq / max(1, round(gate * self.ppq))) + 3) * (
            int(max(keys) - min(keys)) + 2) * 2
        if most <= big_ask.BIG:
            return True
        if most > 50 * big_ask.BIG:
            return self.ask_notes(most)
        return self.note_count(trial) <= self.note_count(sh) or self.confirm_big([trial])

    def ppq_ok(self):
        try:
            return calc_int(self.pvar["ppq"].get(), 1, 65535) > 0
        except ValueError:
            return False

    def on_project_change(self, typed=False, ppq_done=False):
        """typed: a key in the PPQ / BPM / Beats per bar box. The notes are remade only for a new PPQ or Keys (BPM
        and beats per bar change no notes: Hz bass keeps its own BPM; remaking them made typing lag). A PPQ typed
        counts once done (ppq_done: Enter / left / picked from the list)."""
        if self._opening:  # (a project being opened: done once its boxes are all filled in, load_file)
            return
        before = (self.ppq, self.keys)
        for key, lo, hi in (("ppq", 1, 65535), ("beats", 1, 32)):
            if key == "ppq" and typed and not ppq_done and self.typing_in(self.ppq_box):
                continue  # (still being typed: taken on Enter / leaving it)
            try:
                setattr(self, key, calc_int(self.pvar[key].get(), lo, hi))
                if key in self.project_entries:
                    good(self.project_entries[key])
            except ValueError:
                if key in self.project_entries:  # (red; back to its good value on Enter / leaving it)
                    bad(self.project_entries[key], typing=True)
        if "bpm" in self.project_entries:
            try:
                ok = 4 <= calc(self.pvar["bpm"].get()) <= 100000
            except ValueError:
                ok = False
            (good if ok else lambda e: bad(e, typing=True))(self.project_entries["bpm"])
        self.keys = KEYS[1] if self.keys_var.get() == str(KEYS[1]) else KEYS[0]
        if typed and (self.ppq, self.keys) == before:
            self.play_changed()  # (a new BPM heard while playing)
            self.roll.request_redraw()  # (bar lines)
            self.sync_custom()  # (Hz bass "BPM changed" lines)
            self.sync_funnel()
            if self.hz_window:
                self.hz_window.show_stale()
                self.hz_window.redraw()
            self.schedule_autosave()
            return
        set_key_top(self.keys)
        self.sync_edo()
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
        if self.hz_window:
            self.hz_window.show_stale()  # (a BPM change)
        self.sync_funnel()
        self.sync_tumour()
        self.sync_text()
        for w in (self.claw_window, self.strum_window, self.chop_window, self.path_window, self.sustain_window,
                  self.subtract_window, self.merge_window):
            if w:
                w.ppq_changed()  # (numbers shown in ticks)
        if self.image_window and self.keys != before[1]:
            self.image_window.show_settings()  # (its Keys inside the new key range)

    def all16(self):
        """True while Multi channel uses channel 10 too ("Use channel 10", shown only under Multi channel)."""
        return self.use_ch10.get() and self.channel_mode.get() == "auto"

    def sync_channels_fold(self):
        """Project's Channels header: ▾ = the rows shown, ▸ = folded to a grey line naming what's on."""
        if not hasattr(self, "channels_short"):  # (a trace before the panel is made)
            return
        folded = self.channels_folded.get()
        self.channels_head.config(text="▶" if folded else "▼")
        mode = self.channel_mode.get()
        words = [tr("app.ch_short_" + mode)]
        if mode == "auto":
            words.append(tr("app.ch_short_" + SPLIT_CHOICES[max(self.split_box.current(), 0)][0]))
            if self.all16():
                words.append(tr("app.ch_short_ch10"))
            if self.in_order():
                words.append(tr("app.ch_short_order"))
            if self.rounds_apart():
                words.append(tr("app.ch_short_rounds"))
            if (self.rounds_apart() or self.rows_apart()) and self.pad_rounds.get():
                words.append(tr("app.ch_short_pad"))
        self.channels_short.config(text=", ".join(words))
        grid_shown(self.channels_rows, not folded)
        grid_shown(self.channels_short, folded)

    def in_order(self):
        """True while the shapes take channels 1-16 in order ("Channels 1-16 in order", only with all16)."""
        return self.ch10_order.get() and self.all16()

    def rounds_apart(self):
        """True while each Colours round goes on channels of its own ("New channels each Colours round", Multi
        channel only)."""
        return self.new_rounds.get() and self.channel_mode.get() == "auto"

    def colours_used(self):
        """With "Repeated colours get new channels": how many colours each shape's notes use (channels within one
        repeat; the Shapes list's orange goes by these, not by its channels, hunt), else None."""
        r = self.rendered
        if not self.rounds_apart() or not len(r):
            return None
        step = 16 if self.picture_use10 else 15
        pairs = np.unique(r[:, 5] * step + r[:, 4] % step)
        return np.bincount(pairs // step, minlength=len(self.shapes)).tolist()

    def pad_tracks(self):
        """True if the saved MIDI / .dms gets a track for every channel up to the last one used, empty ones too
        (midi_out.track_slots); False only with the rounds tick (or Settings' "Overlapping shapes keep their
        colours", rows_apart) and "Keep empty tracks" off."""
        return not ((self.rounds_apart() or self.rows_apart()) and not self.pad_rounds.get())

    def rows_apart(self):
        """True while overlapping shapes move to the next row of channels (Settings → General, Multi channel only)."""
        return self.keep_colours.get() and self.channel_mode.get() == "auto"

    def edo(self):
        """True while the piano roll shows 31-EDO (256 keys + its tick)."""
        return self.keys == KEYS[1] and self.edo31.get()

    def edo_ticked(self):
        self.sync_edo()
        self.schedule_autosave()
        if self.edo31.get():
            self.tips.show("edo31", wait=True)  # (the first time: what the ticks do)

    def sync_edo(self):
        """The 31-EDO ticks: shown with 256 keys only, Audio greyed until 31-EDO is ticked."""
        big = self.keys == KEYS[1]
        if big and not self.edo_ticks.winfo_manager():
            self.edo_ticks.pack(side="left")
        elif not big and self.edo_ticks.winfo_manager():
            self.edo_ticks.pack_forget()
        self.edo_audio_tick.state(["!disabled" if self.edo31.get() else "disabled"])
        tuned = self.edo() and self.edo31_audio.get()
        if tuned != self.out.edo:  # (sounding notes end first: their note-offs go where their note-ons went)
            playing = self.player.running
            if playing:
                self.stop_play()
            self.scrub_end()
            self.out.edo = tuned
            if playing:
                self.start_play(keep=True)
        self.edo_warn()
        self.roll.request_redraw()

    def edo_warn(self):
        """The Audio tick orange (+ its tip says why) while it's on but the MIDI out can't play 31-EDO."""
        off = self.edo() and self.edo31_audio.get() and self.midi_device.get() != BUILTIN
        self.edo_audio_tick.config(style="Warn.TCheckbutton" if off else "TCheckbutton")
        self.edo_audio_tip.text = tr("app.edo31_audio_needs_builtin" if off else "app.edo31_audio_tip")

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
        """shape_notes_tracks, remembered. Each note tool page goes on top of the notes remembered without it
        (trying a page's settings doesn't make the notes before it again)."""
        key = (shape_key(sh), self.ppq, self.keys)
        cache = self._notes_cache
        got = cache.pop(key, None)
        if got is None:
            got = fx_notes(sh, self.ppq, self.keys, self.notes_tracks)
            self._notes_worked += 1
        cache[key] = got  # (last = used most recently: trim_notes forgets the oldest first)
        self.trim_notes()
        return got

    def trim_notes(self):
        """The notes remembered for at most 500 shapes, or 3 for each shape in the project: past that, the ones
        used longest ago are forgotten (forgetting them all made every edit with over 500 shapes slow)."""
        cache = self._notes_cache
        most = max(500, 3 * len(self.shapes))
        if len(cache) > most:
            for k in list(itertools.islice(cache, len(cache) - most // 2)):
                del cache[k]

    def shapes_changed(self, now=False, moving=False):
        """Recalculate every note (overlaps depend on all shapes together) and refresh the screen.
        While dragging, if that's slow (lots of notes), only the lines follow the mouse: the notes catch up when the
        mouse rests or is let go (now: catch up). moving: shapes are dragged to another place: the piano roll shows
        their notes going along (roll_draw.paint_carried), and they catch up when the mouse rests (if that's quick)
        or is let go."""
        self.shapes_gen += 1
        if self._late_notes:
            self.after_cancel(self._late_notes)
            self._late_notes = None
        if sync_groups(self.shapes):  # Add between: the steps follow their first / last shape (between.py)
            self.after_idle(self.sync_title)  # (a step changed by hand is a key now)
        if tidy_groups(self.shapes):  # (masks: a group of one = on its own, the first member Add; mask.py)
            self.after_idle(self.sync_mask)
        if not now and self.scrubbing and self._merge_time > 0.03:  # (a big merged shape: made when the mouse rests)
            self._merges_owed = True
        else:
            started = time.perf_counter()
            self.remake_merges()
            self._merge_time, self._merges_owed = time.perf_counter() - started, False
        for sh in self.shapes:  # ("Shape length follows the notes": after any change to its box too, hunt)
            if (sh.get("hz") or {}).get("grow"):
                fit_length(sh)
        busy = self._notes_time + self._mask_time  # (the masks' preview is made after the notes, mask.py)
        slow = busy > 0.15 or self._merges_owed
        # (other drags make the notes AND repaint them all at every step: the repaint counts too)
        slow = slow or not moving and busy + self.roll.paint_time > 0.15
        # (moving: also whenever the piano roll shows its notes as one picture, which it can carry along)
        # (Nested's / Tiles' / Ripples' centre points: the notes are all they show, so they follow in the background,
        # user)
        follow = (self.roll.drag and self.roll.drag[0] == "handle" and isinstance(self.roll.drag[1], tuple)
                  and self.roll.drag[1][0] in ("centre", "ripple"))
        if not now and (self.roll.drag or self.scrubbing) and (
                slow or follow and busy > 0.03 or moving and (busy > 0.03 or self.roll._img is not None)):
            self.notes_late = True
            if follow:  # (one at a time: when it's made, _coming_check shows it and starts the next spot's)
                if not (self._coming and not self._coming["done"]):
                    self._late_notes = self.after_idle(self._make_coming, True, True)
                self.roll.request_redraw()
                return
            # the notes are made in the background once the mouse rests: quick enough to paint = the real notes
            # show then, else they wait for the mouse to be let go (and are ready by then)
            self._late_notes = self.after(120 if moving else 250, self.notes_rested, not (moving and slow))
            self.roll.request_redraw()
            return
        self.notes_late = False
        if not self.roll.drag:
            self.remake_pictures()
        self.rendered_pts = [list(sh["pts"][0]) for sh in self.shapes]  # (where each shape is in these notes)
        started, worked = time.perf_counter(), self._notes_worked
        self.channel_split = SPLIT_CHOICES[max(self.split_box.current(), 0)][0]
        multi = self.channel_mode.get() == "auto"
        self.split_box.config(state="readonly" if multi else "disabled")
        if multi != bool(self.split_box.winfo_manager()):  # (only shown under Multi channel, user)
            if multi:
                self.split_box.pack(anchor="w", padx=(18, 0), pady=(1, 0))
                self.ch10_tick.pack(anchor="w", padx=(18, 0), pady=(2, 0))
                self.rounds_tick.pack(anchor="w", padx=(18, 0), pady=(2, 0))
            else:
                self.split_box.pack_forget()
                self.ch10_tick.pack_forget()
                self.rounds_tick.pack_forget()
        if self.all16() != bool(self.order_tick.winfo_manager()):  # (only shown with Use channel 10, user)
            if self.all16():
                self.order_tick.pack(anchor="w", padx=(36, 0), pady=(2, 0), before=self.rounds_tick)
            else:
                self.order_tick.pack_forget()
        padding = self.rounds_apart() or self.rows_apart()
        if padding != bool(self.pad_tick.winfo_manager()):  # (only shown with the rounds tick / rows, user)
            if padding:
                self.pad_tick.pack(anchor="w", padx=(36, 0), pady=(2, 0), after=self.rounds_tick)
            else:
                self.pad_tick.pack_forget()
        made = self.notes_made(any_spot=now == "follow")
        for sh in self.shapes:  # a piece whose outline was changed is a shape of its own now (sliced.py)
            if "cut" in sh and moved_by(as_made(sh)) is None:
                completed(sh)  # (with the whole's glue / note tool pages)
        if made is None:
            made = all_notes(self.shapes, self.ppq, self.keys, self.channel_mode.get(), self.channel_split,
                             self.all16(), self.notes_tracks, rounds=self.rounds_apart(), order=self.in_order(),
                             keep=self.keep_colours.get())
            if self._notes_worked != worked:
                self._notes_time = time.perf_counter() - started
        wanted, self.picture_use10, self.rendered, self.slot_count = made
        started = time.perf_counter()
        self.pattern = self.rendered
        if self.channel_mode.get() == "single" and any(is_mask(sh) and not silent(sh) for sh in self.shapes):
            # (Single channel: sounding masks' notes cut the pattern's where they start; what a mask shows / Apply
            # makes = the pattern as it sounds without them, user)
            self.pattern = all_notes(self.shapes, self.ppq, self.keys, "single", self.channel_split, self.all16(),
                                     self.notes_tracks, quiet=True)[2]
        if self.mask_preview.get():
            self.drawn, self.mask_covered = mask_view(self.rendered, self.shapes, self.ppq, self.keys, self.notes_of,
                                                      self.pattern)
        else:  # (toolbar "Preview" off: the pattern drawn whole, only the masks' dashed lines; user)
            self.drawn, self.mask_covered = self.rendered, set()
        self._mask_time = time.perf_counter() - started
        if wanted != self.colours_wanted:
            self.colours_wanted = wanted
            self.after_idle(self.sync_custom)  # (the panel's warning)
        self.play_changed()
        # placed pictures: their notes are drawn in the picture's own colours (the first picture's: they share them)
        self.picture_owners = np.array([picture_like(sh) for sh in self.shapes], bool)
        self.picture_pal = self.slots_pal()
        counts = self.note_counts = np.bincount(self.rendered[:, 5], minlength=len(self.shapes)).tolist()
        chans = [1] * len(self.shapes)  # Multi channel: how many channels each shape spreads over
        if self.channel_mode.get() == "auto" and len(self.rendered):
            pairs = np.unique(self.rendered[:, 5] * (self.slot_count + 1) + self.rendered[:, 4])
            chans = np.bincount(pairs // (self.slot_count + 1), minlength=len(self.shapes)).tolist()
        # (an Add between group is one row: between_window.py)
        self.fill_shape_list(counts, chans, most_colours(self.all16()), self.colours_used())
        self.sync_list_selection()
        self.roll.request_redraw()
        self.update_status()
        self.schedule_autosave()
        self.sync_history()

    def slots_pal(self):
        """The picture colours by slot as the notes have them now (the first picture's, else the first copy's)."""
        src = next((sh for sh in self.shapes if "picture" in sh), None) or next(
            (sh for sh in self.shapes if "picture_notes" in sh), None)
        if src is None:
            return None
        pal = src["picture"]["set"].get("pal") if "picture" in src else src["picture_notes"].get("pal")
        return slot_pal(pal, slot_rule(src), self.picture_use10)

    def notes_rested(self, show=True):
        """The mouse rests mid-drag: the notes are made in a background thread (the window keeps answering the mouse;
        moved again = thrown away). show: put on screen once made (else kept for when the mouse is let go)."""
        # (after the mouse moves still waiting to be handled: a slow redraw can make the timer run out first)
        if self._merges_owed:  # (merged shapes put off while a number box label is dragged: made now, hunt)
            self.remake_merges()
            self._merges_owed = False
        self._late_notes = self.after_idle(self._make_coming, show)

    def notes_key(self):
        """Everything the notes are made from (shapes_changed): a background result is used only for exactly this."""
        return (tuple(shape_key(sh) for sh in self.shapes), self.ppq, self.keys,
                self.channel_mode.get(), SPLIT_CHOICES[max(self.split_box.current(), 0)][0], self.all16(),
                self.rounds_apart(), self.in_order(), self.keep_colours.get())

    def _make_coming(self, show, follow=False):
        """follow: shown when made even if the shapes have moved on since (then the next spot's are made)."""
        self._late_notes = None
        show = show or not (self.roll.drag or self.scrubbing)  # (the drag called off: no let go to wait for)
        job, key = self._coming, self.notes_key()
        if job and job["key"] == key and (job["got"] is not None or not job["done"]):  # (made / being made already)
            if not job["done"]:
                job["show"] = job["show"] or show
            elif show:
                self.shapes_changed(now=True)
            return
        if job:  # (made for another spot: it stops at its next shape, these start at once)
            job["stop"] = True
        shapes = [from_key(s) for s in key[0]]  # (the thread's own copy: the drag goes on with the real ones)
        for sh in shapes:
            if "cut" in sh and moved_by(as_made(sh)) is None:
                completed(sh)
        job = self._coming = {"key": key, "show": show, "done": False, "got": None, "own": {}, "stop": False,
                              "follow": follow}
        known = self._notes_cache  # (only read in the thread; its own new notes go to job["own"])
        sels, fast = frozenset(self.sels), speed.loops()

        def notes_tracks(sh):
            if job["stop"]:
                raise Stopped
            k = (shape_key(sh), key[1], key[2])
            got = known.get(k) or job["own"].get(k)
            if got is None:
                got = job["own"][k] = fx_notes(sh, key[1], key[2], notes_tracks)
            return got

        def work():
            started = time.perf_counter()
            try:
                got = all_notes(shapes, *key[1:6], notes_tracks, rounds=key[6], order=key[7], keep=key[8])
                # (the same time as shapes_changed's: making the notes only, not the orders below)
                job["time"] = time.perf_counter() - started
                if job["stop"]:
                    raise Stopped
                # (the piano roll's orders for painting them too: roll_draw.start_order / paint_order)
                rendered, order = got[2], None
                start = np.argsort(np.ascontiguousarray(rendered[:, 0]), kind="stable")
                if fast:
                    pics = np.array([picture_like(sh) for sh in shapes], bool)
                    order = ((sels, pics.tobytes(), len(pics), bool(got[1])),
                             painting_order(fast, rendered, start, sels, pics, key[2], got[1]))
                job["drawn"] = (start, order)
                job["got"] = got
            except Exception:  # (MemoryError too: made again the normal way, which tells about it; Stopped)
                job["got"] = None
            job["done"] = True
        job["thread"] = threading.Thread(target=work, daemon=True, name="notes")
        job["thread"].start()
        self.after(15, self._coming_check, job)

    def _coming_check(self, job):
        if job is not self._coming:  # (the notes were made the normal way meanwhile)
            return
        if not job["done"]:
            self.after(15, self._coming_check, job)
            return
        held = self.roll.drag or self.scrubbing
        if not self.notes_late or not (job["show"] or not held):
            return
        if job["got"] is None:  # (failed: made the normal way, which tells about it; held: done when let go)
            if not held:
                self.shapes_changed(now=True)
        elif self.notes_key() == job["key"]:
            self.shapes_changed(now=True)
        elif job["follow"] and held:  # (the centre point moved on meanwhile: these shown, the new spot's made)
            self.shapes_changed(now="follow")
            self.notes_late = True
            self._make_coming(True, True)

    def notes_made(self, any_spot=False):
        """The notes made in the background for the shapes exactly as they are now (waited for if still being made),
        else None. Called by shapes_changed, which then makes them itself. any_spot: a finished job's notes even if
        the shapes have changed since."""
        job, self._coming = self._coming, None
        if job is None or job["key"] != self.notes_key() and not (any_spot and job["done"]):
            if job:  # (made for another spot: stopped, so it doesn't slow down making these)
                job["stop"] = True
            return None
        job["thread"].join()
        if job["got"] is None:
            return None
        self._notes_cache.update(job["own"])
        self.trim_notes()
        if job["own"]:
            self._notes_time = job["time"]
        self.roll.ready_order(job["got"][2], *job["drawn"])
        return job["got"]

    def catch_up_notes(self):
        """The notes left for later while dragging (shapes_changed): now."""
        if self._late_notes or self.notes_late:
            self.shapes_changed(now=True)

    def shape_edited(self, moving=False):
        """The selected shape was changed with the mouse (moving: just dragged to another place)."""
        self.sync_points()
        self.shapes_changed(moving=moving)

    def add_shape(self, sh, name=None):
        if self.mask.get() and not is_mask(sh) and can_mask(sh):  # (drawn with the Mask tick on: a mask, mask.py)
            sh["mask"] = self.new_mask()
        self.push_undo(name=name or tr("app.draw", shape_label=self.shape_label(sh)))
        self.shapes.append(sh)
        self.select(len(self.shapes) - 1)
        self.shapes_changed()
        if sh["kind"] == "curve":  # the first curve: its anchors and handles
            self.tips.show("curves_pen", wait=True)
        self.tips.show("undo", wait=True)  # the first shape: how to take it back

    def on_use_ch10(self):
        """The "Use channel 10" tick. Turned off: Colours / Add between turns over 16 channels go down to 15 at
        once, as an undo step (user: not kept for when it's ticked again). Drawn colours (drawer areas, a stroke's
        colour) stay: the drawer and panel say so in orange. Ctrl+Z on that step ticks it again (_restore)."""
        if not self.use_ch10.get():
            most = most_colours(False)
            if self.over_15(self.shapes, clamp=False):
                self.push_undo(name=tr("app.use_ch10_off"))
                state, name, sel, hz = self.undo_stack[-1]
                self.undo_stack[-1] = (state, name, tuple(sel) + (CH10_OFF,), hz)  # (the step's mark: _restore)
                self.over_15(self.shapes)
            if (self.defaults.get("cycle") or {}).get("n", 0) > most:
                self.defaults["cycle"] = dict(self.defaults["cycle"], n=most)
        self.on_channel_mode()

    def on_channel_mode(self):
        self.shapes_changed()
        self.sync_custom()
        self.sync_colours()
        self.show_colour_limit()
        if self.hz_window:
            self.hz_window.show_clash(self.hz_window.target())  # (Multi channel only)
        if self.channel_mode.get() == "auto":
            self.tips.show("channels", wait=True)

    def show_colour_limit(self):
        """The open drawer and Image window show the colours allowed now (16 with "Use channel 10")."""
        d, im = getattr(self, "drawer", None), self.image_window
        try:
            if d is not None and d.winfo_exists():
                d.draw_swatches()
                d.show_colours_count()
            if im is not None and im.winfo_exists():
                im.show_settings()
        except tk.TclError:
            pass

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
        self.commit_typing()  # (first: it belongs to the shapes picked until now)
        self._hz_ok = None
        ty = self.roll.typing
        self.sels = set(indices)  # first: ending the typing refreshes the panel, which reads the selection
        self.sel = primary
        if ty and (ty["i"] not in self.sels if ty["i"] is not None else bool(self.sels)):
            self.roll.end_typing()  # another shape picked (or the text deleted): done typing
        self.parts = set()
        self.stroke = self.ripple_pick = None
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
        rows = self.shape_rows()  # (an Add between group is one row: all its shapes)
        cur = set(self.listbox.curselection())
        shown = {r for r, row in enumerate(rows) if self.sels.intersection(row)}
        if cur != shown:
            added = cur - shown
            picked = {i for r in cur if r < len(rows) for i in rows[r]}
            primary = (rows[max(added)][0] if added else self.sel if self.sel in picked else
                       max(picked, default=None))
            self.select_many(picked, primary)

    def delete_selected(self, only=None, name=None):
        """Delete the selected shapes, or `only` these (Delete while dragging them): the rest stay selected."""
        gone = set(self.sels if only is None else only)
        if not gone:
            return
        self.roll.end_typing()  # first: it refreshes the panel, which must still see the old shapes
        self.push_undo(name=name or tr("app.delete"))
        for i in sorted(gone, reverse=True):
            del self.shapes[i]
        rest = [i - sum(j < i for j in gone) for i in sorted(self.sels - gone)]  # (numbers after the gone ones drop)
        self.select_many(rest, max(rest, default=None))
        self.shapes_changed()

    def delete_all(self):
        if not self.shapes:
            return
        if not messagebox.askyesno(tr("app.spiderweb_2"), trn("app.are_you_sure_you_want_to", len(self.shapes), n=len(self.shapes)),
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
        shift = max(self.snap_beats(), 1 / self.ppq) if self.snap_beats() else 1.0  # (a step under a tick: on top)
        self.add_copies([self.shapes[i] for i in sorted(self.sels)], shift, tr("app.duplicate"))

    def add_copies(self, shapes, shift, name=tr("app.paste")):
        """Add copies of shapes moved shift beats later, and select them. Returns how many had 16 colours brought
        down to 15 (copied from a project with "Use channel 10" into one without; also said in the status line)."""
        self.push_undo(name=name)
        first = len(self.shapes)
        for sh in shapes:
            new = copy.deepcopy(sh)
            new["pts"] = [[b + shift, p] for b, p in new["pts"]]
            self.shapes.append(new)
        fresh_marks(self.shapes[first:])
        between_copied(self.shapes, first)  # (a whole Add between group: a new group; parts of one: plain shapes)
        clamped = 0 if self.all16() else len(self.over_15(self.shapes[first:]))
        cut = self.fit_pasted_pictures(first)  # (pictures: the project's one channel-10 setting)
        self.select_many(range(first, len(self.shapes)), len(self.shapes) - 1)
        self.shapes_changed()
        said = [trn("app.pasted_15", clamped, n=clamped)] if clamped else []
        said += [trn("app.pasted_picture_15", cut, n=cut)] if cut else []
        if said:
            self.status.config(text="  ".join(said))
        return clamped

    def over_15(self, shapes, clamp=True):
        """The shapes whose Colours / Add between turns go over 15 channels (only allowed with "Use channel 10"),
        brought down to 15 (clamp). Merged shapes' parts count with them."""
        most = most_colours(False)
        found = []
        for sh in shapes:
            over = []
            for t in [sh] + merge_parts(sh):
                if (t.get("cycle") or {}).get("n", 0) > most:
                    over.append((t["cycle"], "n"))
                b = (t.get("between") or {}).get("set")
                if b and b.get("colours", 0) > most:
                    over.append((b, "colours"))
            if over:
                found.append(sh)
            if clamp:
                for c, k in over:
                    c[k] = most
        return found

    def glue_selected(self):
        """Right-click → Glue notes: touching notes on a key in the selected shapes become one (glue.py), inside the
        kept Select boxes if there are any, else all their notes. The shapes remember it."""
        if not self.note_tool_sels():  # (pictures take no note tools)
            return
        areas = self.roll.kept_box()
        shapes = [self.shapes[i] for i in sorted(self.note_tool_sels())]
        before = sum(len(self.notes_of(sh)) for sh in shapes)
        self.push_undo(name=tr("app.glue"))
        for sh in shapes:
            if not areas:
                sh["glue"] = True
                continue
            box = glue_box(np.concatenate(cached_arrays(sh)))
            for area in areas:
                shares = glue_shares(area, box)
                if shares:
                    sh["glue"] = glue_added(sh.get("glue"), shares)
        after = sum(len(self.notes_of(sh)) for sh in shapes)
        self.shapes_changed()
        self.status.config(text=tr("app.glued", notes=trn("n.notes", before, n=before), after=after))

    def unglue_selected(self):
        """Right-click → Remove glue: the selected shapes' notes as they were before any glue."""
        shapes = [self.shapes[i] for i in sorted(self.sels) if self.shapes[i].get("glue")]
        if not shapes:
            return
        self.push_undo(name=tr("app.remove_glue"))
        for sh in shapes:
            del sh["glue"]
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
            line = shapes_line(self.clipboard)  # (also on the Windows clipboard as text, to share: share.py)
            msg = trn("app.copied_shape_s_ctrl_v_pastes", len(self.clipboard), n=len(self.clipboard))
            if put_text(line):
                self.remember_clip()
                if len(line) > LONG_LINE:
                    msg += tr("app.too_long", chars=len(line))
            else:
                self.remember_clip()
                msg += tr("app.couldn_t_share")
            self.status.config(text=msg)

    def cut_selected(self, whole=False):
        """Ctrl+X: what Ctrl+C and Delete work on (a funnel's highlighted curves, a picked stroke, else the selected
        shapes) copied, then deleted. whole (the menu's Cut): always the shapes."""
        if not whole and self.roll.curve_parts():
            self.roll.copy_curve()
            return self.roll.delete_parts()
        if not whole and self.picked():
            self.roll.copy_stroke(*self.picked())
            return self.roll.delete_stroke(*self.picked())
        if self.sels:
            self.copy_selected(whole=True)
            self.delete_selected(name=tr("app.cut"))

    def remember_clip(self):
        """Spiderweb copied something: what the Windows clipboard holds now is older than that copy."""
        self.seen_clip = copy_count()

    def shared_clip(self, shapes_only=False):
        """The Windows clipboard's text if it holds a shared line put there after Spiderweb's last copy (so it's
        what was copied last; Windows' copy count tells, so the same line copied again counts too), "" if the
        clipboard is busy (another program has it open), else None. shapes_only: only if it's shapes that can
        be pasted."""
        if copy_count() == self.seen_clip:
            return None
        text = get_text()
        if text is None:
            return ""
        if not FIND.search(text):
            return None
        if shapes_only:
            try:
                got = unpack(text)
                if got["kind"] != "shapes":
                    return None
                with merges_unmade():  # (only whether it can be pasted)
                    read_shapes(got)
            except ShareError:
                return None
        return text

    def domino_newer(self):
        """True when the Windows clipboard holds notes copied in Domino, put there after Spiderweb's last copy (so
        they're what was copied last: paste takes them)."""
        return RAW and copy_count() != self.seen_clip and on_clipboard()

    def paste_shared(self, text, at=None):
        """Paste shapes shared as text (share.py), like paste; they become Spiderweb's copied shapes. Shapes left
        out, or a line made by another Spiderweb version: a heads-up after pasting (popup + status line)."""
        try:
            got = unpack(text)
            if got["kind"] == "drawing":
                messagebox.showinfo(tr("app.spiderweb_2"), tr("app.shared_drawing"), parent=self)
                return
            with merges_unmade():  # (merged shapes' notes made only once the count is OK'd: they can be millions)
                shapes, left = read_shapes(got)
        except ShareError as e:
            messagebox.showerror(tr("app.spiderweb_2"), tr("app.shared_newer") if e.why == "newer" else
                                 tr("app.shared_broken"), parent=self)
            return
        except MemoryError:
            messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=self)
            return
        if not big_ask.ask_shapes(self, len(shapes)):
            return
        top = self.last_step()
        try:
            if not self.confirm_big(shapes):
                return
            if any("merge" in sh for sh in shapes):
                try:
                    shapes, left = read_shapes(got)
                except ShareError:
                    messagebox.showerror(tr("app.spiderweb_2"), tr("app.shared_broken"), parent=self)
                    return
            clip = self.clipboard, self.clip_kind, self.seen_clip
            self.remember_clip()
            self.clipboard, self.clip_kind = shapes, "shapes"
            clamped = self.paste(whole=True, at=at)
        except Exception as e:  # (out of memory, or anything else: the shapes never stay half pasted, user)
            if self.last_step() is not top:  # (the paste's step: taken back, not kept for Ctrl+Y)
                kept = self._redo_kept
                self._restore(self.undo_stack, self.redo_stack)
                self.redo_stack.pop()
                if kept:
                    self.redo_stack[:] = kept[0]
                self.sync_history()
            if self.clipboard is shapes:  # (Ctrl+V again tries the line again, not an older copy)
                self.clipboard, self.clip_kind, self.seen_clip = clip
            if not isinstance(e, MemoryError):
                raise
            messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=self)
            return
        made = made_by(got)
        notes = ([trn("app.shared_left_out", left, n=left)] if left else []) + (
            [tr(f"share.made_{made[0]}", version=made[1])] if made else [])
        msg = trn("app.pasted_shared", len(shapes), n=len(shapes))
        if left:
            msg += tr("app.shared_left_out_short", n=left)
        if made:
            msg += tr("app.shared_made_short", version=made[1])
        if clamped:
            msg += " " + trn("app.pasted_15", clamped, n=clamped)
        self.status.config(text=msg)
        if notes:
            messagebox.showwarning(tr("app.spiderweb_2"), "\n\n".join(notes), parent=self)

    def paste(self, whole=False, at=None):
        """Paste the copied shapes so they start at the play line (snapped to the grid), or at beat `at` (a Select
        double-click: the mouse, snapped already). With curves highlighted and a curve copied: its shape onto them.
        Shapes shared as text or notes copied in Domino, copied after Spiderweb's last copy, come first
        (paste_shared, paste_from_domino)."""
        text = self.shared_clip()
        if text == "":  # (busy: what's on it can't be told, so nothing older is pasted in its place)
            self.status.config(text=tr("app.clipboard_busy"))
            return
        if text is not None:
            return self.paste_shared(text, at)
        if self.domino_newer():
            return self.paste_from_domino(at)
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
        return self.add_copies(self.clipboard, at - start)

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
        even = self.notes_middle(shapes, sideways, mid2)
        self.push_undo(name=tr("app.flip_sideways") if sideways else tr("app.flip_upside_down"))
        sizes = push_sizes(self.shapes, whole_groups(self.shapes, self.sels))  # (Add between pushes flip too)
        for sh in shapes:
            steps = steps_kept(sh)  # (a piece of a shape with pages: a flip step too, it stays a piece)
            frm, was = sh["pts"], sh.get("fx")
            fx = fx_flipped(was, "time" if sideways else "keys", steps) if was or steps else was
            # (the true middle for a shape given a flip step: its pages' notes come from its drawing flipped back in
            # its own box (engine.unflipped / turned_back), that gives back the drawing it had; and for a text:
            # rebuilt, it slides onto its anchor by whole ticks, a box moved part of a tick came back a tick off)
            m2 = mid2 if fx is not was or sh.get("text") else even
            if sh.get("merge"):  # (its parts' velocities: each sideways flip turns them round, merge.py)
                sh["merge"]["vrev"] = merge_vrev(sh) != sideways
            if sh.get("unmerge") and sideways:  # (kept as separate shapes: the shape Split gives back, the same)
                sh["unmerge"]["vrev"] = not sh["unmerge"].get("vrev")
            flip_shape(sh, sideways, m2)
            turned_piece(sh, frm, lambda w: flip_shape(w, sideways, m2))  # (a funnel piece stays one)
            if fx:  # (its note tool pages' result is flipped too, fx.py)
                sh["fx"] = fx
            else:
                sh.pop("fx", None)
        keep_pushes(self.shapes, sizes, lambda v: (-v[0], v[1]) if sideways else (v[0], -v[1]))
        self.roll.move_kept_box(lambda b, p: (even - b, p) if sideways else (b, even - p))  # (flips too)
        self.sync_panel()
        self.shapes_changed()

    def notes_middle(self, shapes, sideways, mid2):
        """Twice the middle a flip mirrors shapes around: their drawings' own notes' middle (first start + last end,
        lowest + highest key, as fx.notes_box), so each column / row lands on its partner and the flipped notes are
        the mirror note for note (user 2026-10-18: the drawing's middle between two key rows gave a different
        heart). mid2 (the drawing's) when none has notes of its own (Hz bass, pictures), or with Ripples growing
        free (rings cut off at the piano roll's edges move the notes' middle: flipping twice drifted)."""
        if any(grows_free(sh) for sh in shapes):
            return mid2
        got = [self.notes_of({k: v for k, v in sh.items() if k not in ("fx", "_m", "sustain", "range", "subtract")})
               for sh in shapes if not sh.get("hz") and "picture" not in sh]
        got = [n for n in got if len(n)]
        if not got:
            return mid2
        a = np.concatenate(got)
        if sideways:
            return (int(a[:, 0].min()) + int(a[:, 1].max())) / self.ppq
        return int(a[:, 2].min()) + int(a[:, 2].max())

    def gate_flip_state(self):
        """The right-click "Gate aware flip" for the selected shapes: (shapes it switches, on: ticked, why: None or
        why it's greyed), or None when no selected shape takes it (Hz bass, funnels, images: gateflip.offered)."""
        sel = [self.shapes[i] for i in sorted(self.sels) if i < len(self.shapes) and gate_offered(self.shapes[i])]
        if not sel:
            return None
        on = [sh for sh in sel if all_flipped(sh)]
        if on:  # (switching off always works, even where it does nothing now)
            return on, True, None
        kept = [sh for sh in sel if sh.get("gate_flip")]
        if kept:  # (flipped, then Fill / Empty picked: it does nothing now, but can still be switched off)
            return kept, True, None
        shapes = [sh for sh in sel if not sh.get("gate_flip")]
        why = [gate_why_not(sh, *self.notes_tracks(sh)) for sh in shapes]
        ok = [sh for sh, w in zip(shapes, why) if w is None]
        if ok:
            return ok, False, None
        return [], False, next((w for w in ("one_gate", "no_gates") if w in why), "no_gates")

    def gate_flip(self):
        """Right-click "Gate aware flip": the selected shapes' gates laid from the other side, or back (gateflip.py).
        Their velocities turn round with it, like a flip."""
        got = self.gate_flip_state()
        if not got or not got[0]:
            return
        shapes, on, _ = got
        self.push_undo(name=tr("app.gate_flip"))
        for sh in shapes:
            if on:
                del sh["gate_flip"]
            else:
                sh["gate_flip"] = True
            turn_velocities(sh)
        self.sync_panel()
        self.shapes_changed()
        if not on:
            self.tips.show("gate_flip", wait=True)  # (the first time: what it does)

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
        limit = self.roll.limits(shapes)  # (turned past an edge: pushed back inside, user)
        self.push_undo(name=tr("app.turn_90"))
        sizes = push_sizes(self.shapes, whole_groups(self.shapes, self.sels))  # (Add between pushes turn too)
        frms = [sh["pts"] for sh in shapes]
        for sh in shapes:
            steps = steps_kept(sh)  # (a piece of a shape with pages: a turn step too, it stays a piece)
            if sh.get("merge"):  # (a turn leaves its parts' velocities as they are)
                sh["merge"]["vrev"] = merge_vrev(sh)
            turn_shape(sh, clockwise, r, cb, cp)
            if sh.get("fx") or steps:  # (its note tool pages' result is turned too, fx.py)
                fx = fx_turned(sh.get("fx"), 90 if clockwise else -90, r, steps)
                if fx:
                    sh["fx"] = fx
                else:
                    sh.pop("fx", None)
        keep_pushes(self.shapes, sizes, lambda v: (sign * v[1] * r, -sign * v[0] / r))
        db, dp = self.roll.push_in(self.roll.reach(shapes), limit)
        if db or dp:
            for sh in shapes:
                sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]

        def turned(w):
            turn_shape(w, clockwise, r, cb, cp)
            w["pts"] = [[b + db, p + dp] for b, p in w["pts"]]
        for sh, frm in zip(shapes, frms):  # (a funnel piece stays one)
            turned_piece(sh, frm, turned)
        self.roll.move_kept_box(lambda b, p: (cb + sign * (p - cp) * r + db, cp - sign * (b - cb) / r + dp),
                                turned=True)  # (too)
        self.sync_panel()
        self.shapes_changed()

    @staticmethod
    def stretched(sh, ab, kx, ap, ky):
        """A copy of shape sh stretched kx times sideways from beat ab and ky times up / down from pitch ap (a
        Select box's side dragged), still looking the same stretched: arcs / freehand as round, its tumours,
        formulas and text along. Not a Hz bass's notes (user, like its box's corners)."""
        new = copy.deepcopy(sh)
        fn = lambda b, p: (ab + (b - ab) * kx, ap + (p - ap) * ky)
        new["pts"] = [[round(v, 9) + 0.0 for v in fn(b, p)] for b, p in sh["pts"]]  # (no float noise: roll_custom.tidy)
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
        return new  # (a Hz bass's notes keep their lengths: its box only limits where they sound, user)

    def shape_label(self, sh):
        label = self.plain_label(sh)
        return tr("mask.label", label=label) if is_mask(sh) else label

    def plain_label(self, sh):
        made = sh.get("made") if "notes" in sh else None  # (named by what it is: the same in every language)
        if made == "mask":
            return tr("mask.applied")
        if made == "leftovers":
            return tr("join_split.merge_leftovers")
        if made in ("path", "plain"):  # (Copy along a path → Turn into plain shapes: "Line copy 2", Turn into plain
            # notes: "Custom: Circle (notes)", named when made)
            return sh.get("name") or "?"
        if ("picture" in sh or sh.get("merge") or sh.get("unmerge") or  # (the picture's file name, user; "Merged notes")
                "notes" in sh and sh.get("name") in (tr("join_split.merge_leftovers"),  # (plain notes, user; made
                                                     tr("mask.applied"))):  # before "made")
            return sh.get("name") or "?"
        if "notes" in sh:
            return tr("app.pasted_notes")
        if sh.get("text"):
            return tr("app.text_2", value=sh.get('name') or '?')
        if is_joined(sh):
            pieces = len(sh.get("gaps", [])) + 1
            return (tr("app.joined_pieces", curve=KINDS['curve'], pieces=pieces) if pieces > 1 else
                    tr("app.joined", curve=KINDS['curve']))
        return tr("app.custom", value=sh.get('name') or '?') if sh["kind"] == "custom" else KINDS[sh["kind"]]

    def note_count(self, sh, ppq=None):
        """Notes a shape makes (quick for spam, which can be millions). ppq: another than the project's."""
        ppq = ppq or self.ppq
        n = (None if sh.get("merge") else  # (turned / slanted: made again from its shapes, merge.py)
             custom_note_count(sh, ppq) if sh["kind"] == "custom" else
             funnel_note_count(sh, ppq) if sh["kind"] == "funnel" else None)
        return len(self.notes_of(sh)) if n is None else n

    def notes_about(self, sh, ppq=None):
        """note_count, but a merged shape = what its parts make (quick, never made for it: its own may not be made
        yet, engine.merges_unmade; a little more than its own when rows were left over), for the questions."""
        if sh.get("merge"):
            return sum(self.notes_about(p, ppq) for p in sh["merge"]["parts"])
        return self.note_count(sh, ppq)

    def confirm_big(self, shapes):
        return self.ask_notes(sum(self.notes_about(sh) for sh in shapes))

    def ask_notes(self, n):
        """True = go ahead making n notes (asked past big_ask.BIG)."""
        return big_ask.ask(self, "notes", n)

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
        for tool, _, hot in TOOLS + DRAW_TOOLS:
            if key == hot:
                self.tool.set(tool)

    def toggle_select_tool(self):
        """Double right-click: Select <-> the drawing tool used last."""
        self.tool.set(self.draw_tool if self.tool.get() == "select" else "select")

    def on_tool_change(self):
        if self.tool.get() not in ("select", "slice"):  # (Slice isn't one to go back to, user)
            self.draw_tool = self.tool.get()
        if self.tool.get() != "hz":
            self.hz_start = None  # (a spot waiting for its first note: the Hz bass window stops offering it)
        if self.hz_window:
            self.hz_window.sync()
        self.roll.cancel_draft()
        self.roll.set_cursor(self.roll.custom_cursor(None))  # (the tool's own pointer; remembered: else the next
        #                                                       pointer it had was skipped)
        self.tool_picker.tool_changed()
        self.sync_freehand()
        self.sync_text()
        self.sync_custom()
        self.sync_funnel()
        if self.tool.get() == "picture":
            self.after_idle(self.open_image)
        self.tips.show(TOOL_TOPICS.get(self.tool.get()))

    def remake_merges(self):
        """Merged shapes whose parts' settings were changed in the panel (custom_targets), or made at another PPQ /
        key range than the project's now: made again (merge.refit). A recipe seen for the first time (new, loaded,
        undone) is taken as it is."""
        from notes.merge import refit, settings_key
        seen = {}
        for sh in self.shapes:
            m = sh.get("merge")
            if not m:
                continue
            key = settings_key(m)
            if (m["ppq"], m["keys"]) != (self.ppq, self.keys):  # (its gates in whole ticks of the project's PPQ)
                m["ppq"], m["keys"] = self.ppq, self.keys
                refit(sh)
            elif self._merge_keys.get(id(m), key) != key:
                refit(sh)
            seen[id(m)] = key
        self._merge_keys = seen

    def note_tool_sels(self):
        """The selected shapes that take note tools (claw, strum, chop, glue): not pictures (user)."""
        return {i for i in self.sels if i < len(self.shapes) and "picture" not in self.shapes[i]}

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
        parts.append(tr("app.shapes_notes", shapes=trn("n.shapes", len(self.shapes), n=len(self.shapes)),
                        notes=trn("n.notes", len(self.rendered), n=len(self.rendered))))
        if self.channel_mode.get() == "auto" and self.slot_count:
            if self.pad_tracks():
                tracks = self.slot_count
            else:  # (counted once per notes made: the status line changes with every mouse move)
                if self._used_tracks[0] is not self.rendered:
                    self._used_tracks = (self.rendered, len(track_slots(self.rendered, False)))
                tracks = self._used_tracks[1]
            parts.append(tr("app.one_track") if tracks == 1 else
                         tr("app.tracks_one_channel_each", slot_count=tracks))
        if self.sels:
            shapes = tr("app.shapes_2", n=len(self.sels)) if len(self.sels) > 1 else ""
            n = sum(self.note_counts[i] for i in self.sels if i < len(self.note_counts))
            parts.append(tr("app.selected_notes", shapes=shapes, notes=trn("n.notes", n, n=n)))
        self.status.show("     ".join(parts))

    def in_drawer(self, e):
        """Keys pressed in the drawer window are the drawer's, not the piano roll's."""
        return bool(self.drawer) and str(e.widget).startswith(str(self.drawer))

    def hotkey(self, fn, main_only=False, while_held=False):
        """A window-wide shortcut that leaves typing boxes alone. main_only: only while the main window has the
        keyboard (not in a pop-up). while_held: it works while the mouse holds a shape on the piano roll too (the
        others do nothing then, user: a flip was undone by the next mouse move, a join broken)."""
        def handler(e):
            w = e.widget
            if self.in_drawer(e):
                return None
            if self.hz_window and str(w).startswith(str(self.hz_window)):
                return None  # (the Hz bass window: its own keys only, nothing done to the main piano roll behind)
            if self.image_window and str(w).startswith(str(self.image_window)):
                return None  # (the image window and its pop-ups: Ctrl+C there copies their text, not the shapes)
            if self.app_only(w):
                return None  # (Settings, Help, the update popup: the shapes behind stay as they are)
            if self.grabbed_elsewhere():
                return None  # (a window like Range… is working on the picked shapes: nothing changes behind it)
            if main_only and not (isinstance(w, tk.Misc) and w.winfo_toplevel() is self):
                return None
            if isinstance(w, (tk.Entry, ttk.Entry)) and str(w.cget("state")) != "readonly":
                return None
            if self.box_drawn() or not while_held and (self.roll.holding() or self.vel.edit or self.held_scrub):
                return "break"  # (a velocity line held: its shapes mustn't change under it either)
            fn()
            return "break"
        return handler

    def typing_in(self, box):
        """box has the keyboard (or its dropdown list is open)."""
        try:
            return self.focus_get() is box
        except (KeyError, tk.TclError):  # (a dropdown's list has it)
            return True

    def app_only(self, w):
        """w is in a window about Spiderweb itself (Settings, Help, a tip, the update popup: their class has
        APP_ONLY): keys pressed there never change the shapes behind it."""
        if self.in_settings(w):
            return True
        try:
            return getattr(w.winfo_toplevel(), "APP_ONLY", False)
        except (AttributeError, tk.TclError):  # (Tk's own widgets come as a name: a dropdown's list)
            return False

    def in_settings(self, w):
        """w is in the Settings window (or one of its pop-ups)."""
        sw = getattr(self, "settings_window", None)
        return sw is not None and str(w).startswith(str(sw))

    def grabbed_elsewhere(self):
        """A pop-up holds the mouse and keyboard (grab_set: Range…, formula Custom…, tumour graph, snap picker)."""
        try:
            g = self.grab_current()
        except (KeyError, tk.TclError):  # (a grab on a window tkinter doesn't know, e.g. a message box)
            return True
        return g is not None and g.winfo_toplevel() is not self

    def in_hz(self, widget=None):
        """The Hz bass window (or a window of its own) has the keyboard (widget: the one a key was pressed in)."""
        if not self.hz_window:
            return False
        if widget is None:
            try:
                widget = self.focus_get()
            except (KeyError, tk.TclError):  # (a dropdown's list has it)
                return False
        return widget is not None and str(widget).startswith(str(self.hz_window))

    def box_drawn(self):
        """A Select box being drawn on the main piano roll: shortcuts do nothing until it's let go (user: like many
        MIDI programs)."""
        return bool(self.roll.drag) and self.roll.drag[0] == "box"

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
        if self.drawer and self.drawer.winfo_exists() and not self.drawer.may_quit():
            return
        if self.hz_window and self.hz_window.draft is not None:  # (a path drawn there: made, as when the Hz bass
            self.hz_window.make_draft()  # window itself is closed)
        if not self.close_autosave():
            return
        self.stop_play()
        self.out.close()
        self.destroy()
