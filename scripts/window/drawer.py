"""The custom shape drawer (its own window) and the shape library: built-in shapes plus the user's own in spiderweb/shapes/*.json.
Its other parts: drawer_areas.py (colouring areas by hand), drawer_mouse.py (the mouse on the board, copy / paste /
flip / turn), layers.py (layers)."""

import json
import math
import os
import re
import tkinter as tk
from tkinter import ttk, messagebox

from files.lang import tr
from notes.bezier import handle_lines, pen_handles
import numpy as np

from notes.areas import clean_areas
from notes.custom import (clean_strokes, colour_of, join_strokes, plain_stroke, role_of, settled_areas,
                          stroke_points)
from roll.roll_shared import SLOT_COLORS
from notes.pattern import has_formula
from notes.slice import slice_stroke
from files.about import USER
from files.safefile import write_text
from files.clipboard import get_text, put_text
from files.share import LONG_LINE, ShareError, drawing_line, made_by, read_drawing, unpack
from files.system import double_click_ms
from roll.roll_shared import grab_while_panning
from window import look
from window.help import open_help
from window.drawer_areas import AREA_FRAME, BOARD, OFF_BOARD, DrawerAreas
from window.drawer_mouse import CTRL, MOD_KEYS, SHIFT, STICK_RANK, DrawerMouse
from window.help_texts import BY_ID, DRAWER_TOOL_TOPICS
from window.layers import DrawerLayers
from window.sticky import BEND, REACH, Targets, key_points
from window.widgets import REDO_KEYS, UNDO_KEYS, ListTip, ScrollingSide, Tooltip, remember_place

LIBRARY = os.path.join(USER, "shapes")
# Always in the library (not files). A saved shape with the same name is used instead; deleting it brings these back.
BUILT_IN = {
    "Circle": [{"kind": "ellipse", "box": [0, 0, 1, 1]}],
    "Square": [{"kind": "poly", "pts": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]}],
    "Triangle": [{"kind": "poly", "pts": [[0, 0], [1, 0], [0.5, 1], [0, 0]]}],
}
GRIDS = ["4", "8", "12", "16", "24", "32", "48", "64"]
TOOLS = [("select", tr("drawer.select"), "v"), ("slice", tr("drawer.slice"), "k"), ("erase", tr("drawer.eraser"), "e"), ("line", tr("drawer.line"), "l"),
         ("poly", tr("drawer.polyline"), "p"), ("free", tr("drawer.freehand"), "f"), ("curve", tr("drawer.curve"), "c"),
         ("arc", tr("drawer.arc"), "a"), ("square", tr("drawer.square"), "s"), ("circle", tr("drawer.circle"), "o"),
         ("areas", tr("drawer.areas"), "b")]
STROKE_COLOR = look.STROKE  # (a stroke with an outline colour: that colour's dark shade)
STICK_COLOR = look.STICK
STICK_LINE = look.STICK_LINE
LIST_AWAY = look.LIST_AWAY  # the shape picked in the library list while the keyboard is elsewhere (blue when it's there)
DOUBLE_CLICK_MS = double_click_ms()  # (the system's own setting)
MIRRORS = ("off", "h", "v", "both")  # Mirror: off, left <-> right, top <-> bottom, both (across the board's middle)


def mirror_fns(mode):
    """The ways a new stroke is mirrored: [fn(u, v) -> (u, v)] (none when off)."""
    h, v = (lambda u, w: (1 - u, w)), (lambda u, w: (u, 1 - w))
    return {"h": [h], "v": [v], "both": [h, v, lambda u, w: (1 - u, 1 - w)]}.get(mode, [])


def seg_dist(p, a, b):
    """How far point p is from the piece a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    t = 0 if ll == 0 else min(1, max(0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def same_stroke(a, b):
    """The two strokes lie on each other (a stroke the mirror leaves where it is: no copy)."""
    if a["kind"] != b["kind"]:
        return False
    if a["kind"] == "ellipse":
        return all(abs(p - q) < 1e-6 for p, q in zip(a["box"], b["box"]))
    pa, pb = a["pts"], b["pts"]
    if len(pa) != len(pb):
        return False

    def near(p, q):
        return math.dist(p, q) < 1e-6

    def same(side):
        return all(near(p, q) for p, q in zip(pa, side))
    if same(pb) or same(pb[::-1]):
        return True
    if len(pa) < 3 or not (near(pa[0], pa[-1]) and near(pb[0], pb[-1])):
        return False
    if a["kind"] == "arc":  # a whole circle: the same one from the other end of its line across
        return near(pa[0], pb[1]) and near(pa[1], pb[0])
    step = 3 if a["kind"] == "curve" else 1  # closed: the same outline from any of its points (a curve's anchors)
    for side in (pb, pb[::-1]):
        ring = side[:-1]
        for s in range(step, len(ring), step):
            turned = ring[s:] + ring[:s]
            if same(turned + turned[:1]):
                return True
    return False


BAD_CHARS = '<>:"/\\|?*'
NAME_MAX = 100  # (longer names: the file's full path can get too long for Windows)
DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"{d}{n}" for d in ("COM", "LPT") for n in range(1, 10))}


# ---------------------------------------------------------------- library
# A saved shape's name is inside its file ({"name": ..., on the first line); the file is named after it with the
# characters Windows refuses made "_" (user: names may have any character). Files without a name inside (made
# before 1.5) go by their file name.

_names = {}  # file path -> ((modified time, size), the name inside)


def stamp(path):
    """(modified time, size) of a file, or None if it's gone: tells whether it changed since it was read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _name_in(path, stem):
    """The name a saved shape's file holds (its first line; files made by hand: read whole), else its file name."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            line = f.readline().strip()
            if line.startswith('{"name": ') and line.endswith(","):
                got = json.loads(line[:-1] + "}")
            elif line.startswith('{"strokes"'):  # (made before names were kept inside)
                return stem
            else:
                f.seek(0)
                got = json.load(f)
        name = clean_name(got["name"]) if isinstance(got, dict) and isinstance(got.get("name"), str) else ""
    except (OSError, ValueError):
        return stem
    return name or stem


def _saved():
    """The saved shapes: {name in lower case: (name, file path)}."""
    try:
        files = sorted(f for f in os.listdir(LIBRARY) if f.lower().endswith(".json"))
    except OSError:
        return {}
    out = {}
    for f in files:
        path = os.path.join(LIBRARY, f)
        now = stamp(path)
        if now is None:
            continue
        known = _names.get(path)
        if not known or known[0] != now:
            known = _names[path] = (now, _name_in(path, f[:-5]))
        out.setdefault(known[1].lower(), (known[1], path))
    return out


def saved_names():
    return [n for n, _ in _saved().values()]


def shape_path(name):
    """The file of the saved shape called `name` (any upper/lower case), or None."""
    got = _saved().get(name.lower())
    return got[1] if got else None


def shape_stamp(name):
    """What a library shape is right now: its file and (modified time, size), or the built-in it falls back to.
    Equal stamps = the same drawing (panel_custom keeps the shape read until it changes)."""
    path = shape_path(name)
    return (path, stamp(path)) if path else ("built-in", built_in_name(name))


def built_in_name(name):
    """The built-in shape's name if `name` is one (any upper/lower case), else None."""
    return next((b for b in BUILT_IN if b.lower() == name.lower()), None)


def library_names():
    """The built-in shapes first, in their own order (user: always on top; a saved one by that name takes its
    place), then the saved shapes A-Z."""
    names = saved_names()
    by_lower = {n.lower(): n for n in names}
    top = [by_lower.get(b.lower(), b) for b in BUILT_IN]
    return top + sorted((n for n in names if not built_in_name(n)), key=str.lower)


def library_lists():
    """The side panel's Shape box (user 2026-10-14, like the Hz shape box): (the built-in shapes as in
    library_names, the saved ones NEWEST first: last saved / changed on top)."""
    saved = list(_saved().values())
    by_lower = {n.lower(): n for n, _ in saved}
    top = [by_lower.get(b.lower(), b) for b in BUILT_IN]
    rest = [(n, (stamp(p) or (0, 0))[0]) for n, p in saved if not built_in_name(n)]
    return top, [n for n, _ in sorted(rest, key=lambda r: (-r[1], r[0].lower()))]


def placed_strokes(strokes):
    """A drawing's strokes as a placed shape gets them: hidden ones left out (layers list), lines that meet end to
    end joined (custom.join_strokes), no layer data. None if nothing is left."""
    out = [{k: v for k, v in st.items() if k != "layer"} for st in strokes or []
           if not (st.get("layer") or {}).get("hidden")]
    return join_strokes(out) if out else None


def load_drawing(name, layers=False):
    """A library shape's strokes (None if it's missing or broken) and its areas coloured by hand (areas.py). A
    broken file named like a built-in shape gives the built-in one. layers: as drawn, for the drawer (every stroke,
    with its layer data); else as placed (placed_strokes)."""
    strokes, areas = load_layers(name)
    return (strokes if layers else placed_strokes(strokes)), areas


def load_layers(name):
    """load_drawing as drawn."""
    path, b = shape_path(name), built_in_name(name)
    strokes, areas = None, []
    if path:
        try:
            with open(path, encoding="utf-8-sig") as f:
                got = json.load(f)
            strokes, areas = clean_strokes(got.get("strokes")) or None, clean_areas(got.get("areas"))
        except (OSError, ValueError, AttributeError):
            pass
    if strokes is None and b:
        return clean_strokes(BUILT_IN[b]), []
    return strokes, (areas if strokes else [])


def file_stem(name, what="shape"):
    """A shape's (palette's...) name as a file name Windows takes: the characters it refuses -> "_", no dot / space
    at the end, not one of its device names ("CON" -> "CON (shape)")."""
    stem = "".join("_" if c in BAD_CHARS or c < " " else c for c in name).strip().rstrip(". ")
    base, dot, rest = stem.partition(".")
    if base.strip().upper() in DEVICES:
        stem = f"{base} ({what}){dot}{rest}"
    return stem or what


def free_path(name, keep=None):
    """A file for a shape called `name` that no other shape uses ("x (2).json" if "x.json" is taken). keep: the
    shape's own file now (it may stay that one, e.g. only upper / lower case changed)."""
    stem, k = file_stem(name), 2
    path = os.path.join(LIBRARY, stem + ".json")
    while os.path.exists(path) and os.path.normcase(path) != os.path.normcase(keep or ""):
        path, k = os.path.join(LIBRARY, f"{stem} ({k}).json"), k + 1
    return path


def shape_text(name, strokes, areas=()):
    text = '{"name": ' + json.dumps(name) + ',\n "strokes": [\n  ' + ",\n  ".join(json.dumps(st) for st in strokes)
    text += "\n]"
    if areas:
        text += ',\n "areas": ' + json.dumps([[round(u, 6), round(v, 6), c] for u, v, c in areas])
    return text + "}\n"


def save_shape(name, strokes, areas=()):
    """Saves a library shape (over the one with that name, if any). Returns its file."""
    os.makedirs(LIBRARY, exist_ok=True)
    path = shape_path(name) or free_path(name)
    write_text(path, shape_text(name, strokes, areas))
    return path


def rename_shape(old, new):
    """A saved shape gets a new name (its file too, when it can). Raises OSError; ValueError if it can't be read."""
    path = shape_path(old)
    strokes, areas = load_drawing(old, layers=True)
    if not path or strokes is None:
        raise ValueError(old)
    to = free_path(new, keep=path)
    write_text(to, shape_text(new, strokes, areas))
    if os.path.normcase(to) != os.path.normcase(path):
        os.remove(path)


def clean_name(name):
    """A shape's name: no hidden characters (like Tab or line breaks), no spaces around it, at most NAME_MAX
    letters. Any other character is fine (the file name is made safe by file_stem)."""
    return "".join(c for c in name if c >= " ").strip()[:NAME_MAX].strip()


# ---------------------------------------------------------------- window

class Drawer(DrawerAreas, DrawerMouse, DrawerLayers, ScrollingSide, tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        s = self.scale = app.scale
        self.title(tr("drawer.spiderweb_custom_shape_drawer"))
        self.geometry(f"{int(1000 * s)}x{int(720 * s)}")
        self.minsize(int(700 * s), int(500 * s))
        remember_place(self, "drawer")
        self.strokes = []      # {"kind": "poly" / "curve", "pts": [[u, v], ...]} or {"kind": "ellipse", "box": [...]}
        self.clipboard = None  # copied strokes, and how many times they've been pasted
        self.pastes = 0
        self.undo_stack = []
        self.redo_stack = []   # undone steps, until something new is drawn
        self.redo_kept = []    # the redo steps before the last push_undo (a click that moved nothing gets them back)
        self.draft = None      # stroke being drawn
        self.drag = None
        self.follow = None     # a stroke started with a click: its drag, following the mouse until the next click
        self.arc_bend = False  # an arc dragged start -> end: its middle point follows the mouse until a click
        self.dirty = False     # changed since it was saved or opened
        self.saved_name = None  # the library shape this drawing was opened from / saved as
        self.saved_stamp = None  # its file then (shape_stamp): changed since = saved somewhere else
        self.sel = None        # index of the selected stroke (Select tool; a curve shows its handles)
        self.picks = set()     # the other selected strokes (a select box or Ctrl+click can pick several)
        self.boxes = []        # the select boxes kept after letting go: [[u0, v0, u1, v1]], until a click outside
        self.areas = []        # areas coloured by hand: [[u, v, colour]] (areas.py; colour 0 = empty)
        self.area_pick = 1     # the colour the Areas tool gives (0 = empty)
        self.hover = None      # the area under the mouse (Areas tool)
        self._area_cache = self._area_px = self._area_img = self._gap_cache = None
        self._settled = "[]"   # the strokes as JSON when the areas last matched them (changed)
        self.stuck = None      # where the last point stuck (sticky.py): (kind, (u, v), pixels away), shown as a mark
        self.stuck_stroke = None  # of the strokes being moved, the one that stuck (stick_move)
        self.stuck2 = None     # a circle / square / moved stroke resting on two lines: the second touch's mark
        self.square_sides = [1, 2]  # a square being drawn: its sides lit (stuck_pieces; 1, 2 = at the mouse's corner)
        self.guide = []        # a circle near sticking or stuck: where to point the mouse for it to touch, each
        # ((u, v), [the touched strokes' points moved through it])
        self._stick_cache = self._circle_cache = None
        self.knife = None      # the Slice tool's line being dragged: [start, end] (board points)
        self.drag_xy = None    # where the mouse was at the last drag step (Shift / Ctrl pressed: done again there)
        self.zoom = 1.0       # 1 = the whole board fits the window
        self.center = [0.5, 0.5]  # the board point in the middle of the window (0.5, 0.5 = the board's middle)
        self._pan = None
        self._panned = False  # the middle button moved further than a click's 3 px (pan_to)
        self.tool = tk.StringVar(value="poly")
        self.grid_n = tk.StringVar(value="16")
        self.mirror = tk.StringVar(value=tr("drawer.mirror_off"))  # (its shown name: mirror_mode)
        self.name = tk.StringVar()
        self._build()
        self.refresh_list()
        self.tool.trace_add("write", lambda *_: (self.drop_sel(), self.cancel_draft(), self.on_tool()))
        self.grid_n.trace_add("write", lambda *_: self.redraw())
        self.mirror.trace_add("write", lambda *_: self.redraw())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(500, lambda: app.tips.show("drawer", parent=self))  # the first time it opens

    def tool_topic(self):
        return DRAWER_TOOL_TOPICS.get(self.tool.get(), "drawer")

    def on_tool(self):
        self.hover = None
        if self.tool.get() == "areas":
            if not self.area_bar.winfo_manager():
                self.area_bar.pack(fill="x", pady=(8, 0), before=self.layers_box)
            self.draw_swatches()
            self.show_colours_count()
        elif self.area_bar.winfo_manager():
            self.area_bar.pack_forget()
        self.app.tips.show(self.tool_topic(), parent=self)

    def _build(self):
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(fill="x")
        for key, label, hot in TOOLS:
            b = ttk.Radiobutton(bar, text=f"{label} ({hot.upper()})", value=key, variable=self.tool,
                                style="Toolbutton")
            b.pack(side="left", padx=1)
            Tooltip(b, BY_ID[DRAWER_TOOL_TOPICS[key]]["tip"])
        bar = ttk.Frame(self, padding=(6, 0, 6, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text=tr("drawer.grid")).pack(side="left", padx=(0, 4))
        grid = ttk.Combobox(bar, textvariable=self.grid_n, values=GRIDS, width=4, state="readonly")
        grid.pack(side="left")
        ttk.Label(bar, text=tr("drawer.mirror")).pack(side="left", padx=(12, 4))
        names = [tr("drawer.mirror_" + m) for m in MIRRORS]
        b = self.mirror_box = ttk.Combobox(bar, textvariable=self.mirror, values=names,
                                           width=max(map(len, names)) + 1, state="readonly")
        b.pack(side="left")
        Tooltip(b, tr("drawer.mirror_tip"))
        for box in (grid, b):  # a choice picked: the keys go to the drawing again (Enter, Esc, tool keys)
            box.bind("<<ComboboxSelected>>", lambda e: self.canvas.focus_set(), add="+")
        ttk.Button(bar, text=tr("drawer.clear"), command=self.clear).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("drawer.reset_view"), command=self.reset_view).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("drawer.help_f1"), command=self.open_help).pack(side="left", padx=(12, 0))

        side = self.build_side()
        box = ttk.LabelFrame(side, text=tr("drawer.shape_library"), padding=6)
        box.pack(fill="x")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.listbox = tk.Listbox(row, height=12, activestyle="none", exportselection=False, font=look.font(9))
        sb = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        lb = self.listbox
        ListTip(lb)
        lb.bind("<Double-Button-1>", lambda e: (self.stop_slow_click(), self.open_selected()))
        lb.bind("<ButtonPress-1>", self.list_press)
        # Keys while the list has the keyboard work on the list (the drawing's own keys: click the board). Blue =
        # the list has the keyboard, grey = the board has it (user, like Windows' file lists).
        lb.bind("<Delete>", lambda e: (self.delete_selected(), "break")[1])
        lb.bind("<BackSpace>", lambda e: "break")
        lb.bind("<Return>", lambda e: (self.open_selected(), "break")[1])
        lb.bind("<F2>", lambda e: (self.start_rename(), "break")[1])
        lb.bind("<FocusIn>", lambda e: lb.config(selectbackground=look.LIST_HERE,
                                                 selectforeground=look.LIST_HERE_TEXT))
        lb.bind("<FocusOut>", lambda e: lb.config(selectbackground=LIST_AWAY, selectforeground=look.LIST_AWAY_TEXT))
        lb.config(selectbackground=LIST_AWAY, selectforeground=look.LIST_AWAY_TEXT)
        self.renaming = None  # the box a name is typed into while renaming (start_rename)
        self._slow_click = None
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        for i, (key, cmd) in enumerate((("open", self.open_selected), ("delete", self.delete_selected),
                                        ("rename", self.start_rename), ("new", self.new))):
            b = ttk.Button(btns, text=tr("drawer." + key), command=cmd, width=1)  # (4 equal widths: grid below)
            b.grid(row=0, column=i, sticky="ew", padx=(4 if i else 0, 0))
            btns.columnconfigure(i, weight=1, uniform="lib")
            if key == "rename":
                Tooltip(b, tr("drawer.rename_tip"))
        name = ttk.Frame(box)
        name.pack(fill="x", pady=(8, 0))
        ttk.Label(name, text=tr("drawer.name")).pack(side="left")
        ttk.Entry(name, textvariable=self.name).pack(side="left", fill="x", expand=True, padx=(5, 0))
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text=tr("drawer.save"), command=self.save).pack(side="left")
        ttk.Button(btns, text=tr("drawer.use_on_the_piano_roll"), command=self.use).pack(side="right")
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        b = ttk.Button(btns, text=tr("drawer.export"), command=self.export)
        b.pack(side="left")
        Tooltip(b, tr("drawer.export_tip"))
        b = ttk.Button(btns, text=tr("drawer.import"), command=self.import_shared)
        b.pack(side="left", padx=4)
        Tooltip(b, tr("drawer.import_tip"))
        self.pos_label = ttk.Label(side, text="", foreground=look.INFO, font=look.font(9))
        self.pos_label.pack(fill="x", pady=(8, 0))
        self.state_label = ttk.Label(side, text="", wraplength=int(285 * self.scale), justify="left")
        self.state_label.pack(fill="x", pady=(8, 0))
        # the Areas tool's colours (shown only with that tool): Empty, then 1 .. COLOURS
        self.area_bar = ttk.Frame(side)
        ttk.Label(self.area_bar, text=tr("drawer.area_colour")).pack(anchor="w")
        k = self.swatch = max(14, round(16 * self.scale))
        self.swatches = tk.Canvas(self.area_bar, width=1, height=k + 4,  # (fills the panel's width: draw_swatches
                                  highlightthickness=0, background=ttk.Style().lookup("TFrame", "background")
                                  or "SystemButtonFace", cursor="hand2")  # fits the swatches into it)
        self.swatches.pack(fill="x", pady=(3, 0))
        self.swatches.bind("<Configure>", lambda e: self.draw_swatches())
        self.swatches.bind("<ButtonPress-1>", self.pick_swatch)
        self.swatches.bind("<Motion>", self.swatch_tip)
        self.swatches.bind("<Leave>", lambda e: self.pos_label.config(text=""))
        self.colours_used = ttk.Label(self.area_bar, text="", wraplength=int(285 * self.scale), justify="left")
        self.colours_used.pack(anchor="w", pady=(3, 0))
        ttk.Button(self.area_bar, text=tr("drawer.areas_reset"), command=self.reset_areas).pack(anchor="w",
                                                                                              pady=(6, 0))
        self.build_layers(side)  # (where the tool's help was: that's behind Help now, user)

        self.canvas = tk.Canvas(self, bg=OFF_BOARD, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        c = self.canvas
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Motion>", self.on_motion)
        c.bind("<Leave>", self.on_leave)
        c.bind("<ButtonPress-3>", self.right_click)
        c.bind("<ButtonPress-2>", self.start_pan)
        c.bind("<B2-Motion>", self.pan_to)
        c.bind("<ButtonRelease-2>", self.on_middle_release)
        grab_while_panning(c)
        c.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Key>", self.on_key)
        self.bind("<KeyRelease>", lambda e: e.keysym.lower() in MOD_KEYS and self.mod_changed(e, False))
        self.bind("<F1>", lambda e: self.open_help())
        for keys, fn in ((UNDO_KEYS, self.undo), (REDO_KEYS, self.redo)):
            for k in keys:
                self.bind(k, self.hotkey(fn))
        for keys, fn in (("c C", self.copy), ("x X", self.cut), ("v V", self.paste), ("h H", lambda: self.flip(True)),
                         ("j J", lambda: self.flip(False)), ("Left", lambda: self.turn(False)),
                         ("Right", lambda: self.turn(True))):
            for k in keys.split():
                self.bind(f"<Control-{k}>", self.hotkey(fn))

    def build_side(self):
        """The side panel: the layers list takes the room that's left; when that would make it shorter than its
        least rows (long warning texts, a small window), the panel scrolls instead (scrollbar, mouse wheel)."""
        return self.make_side(int(300 * self.scale))

    SIDE_SKIP = (tk.Listbox, ttk.Treeview, ttk.Combobox)  # (the lists scroll themselves)

    def hotkey(self, fn):
        """A drawer shortcut that leaves text boxes alone (the name box, a layer being renamed)."""
        def handler(e):
            if isinstance(e.widget, (tk.Entry, ttk.Entry)):
                return None
            if (self.knife or self.erasing()) and fn not in (self.undo, self.redo):  # (flip / turn / copy / paste
                return "break"  # wait while a Slice line / eraser box is held; undo / redo drop it)
            fn()
            return "break"
        return handler

    # ------------------------------------------------------------ coordinates

    # The board is u, v = 0..1 (v up); its middle, (0.5, 0.5), is shown to the user as 0, 0.
    # Drawing outside the board is fine: a shape is stretched to its own size when it's placed anyway.

    def px(self):
        """Pixels per board width."""
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return max(10, min(w, h) - 48 * self.scale) * self.zoom

    def to_screen(self, u, v):
        k = self.px()
        return (self.canvas.winfo_width() / 2 + (u - self.center[0]) * k,
                self.canvas.winfo_height() / 2 - (v - self.center[1]) * k)

    def screen_points(self, pts):
        """Many (u, v) points on screen at once: [x0, y0, x1, y1, ...] (to_screen asks the canvas its size each
        time, too slow for big drawings)."""
        k, w, h = self.px(), self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2
        cu, cv = self.center
        return [c for u, v in pts for c in (w + (u - cu) * k, h - (v - cv) * k)]

    def from_screen(self, x, y):
        k = self.px()
        return (self.center[0] + (x - self.canvas.winfo_width() / 2) / k,
                self.center[1] - (y - self.canvas.winfo_height() / 2) / k)

    def to_xy(self, p):
        return self.to_screen(*p)

    def from_xy(self, x, y):
        return list(self.from_screen(x, y))

    def event_pt(self, e, snap=True, stick=True):
        """The board point at the mouse: stuck to a stroke near it (sticky.py; not rounded, so it stays exactly on
        that line; stick=False: grid only), else on the grid; Shift = free."""
        u, v = self.from_screen(e.x, e.y)
        self.stuck = None
        if snap and not e.state & SHIFT:
            self.stuck = self.stick_at(e.x, e.y) if stick else None
            n = int(self.grid_n.get())
            if self.stuck and self.stuck[0] == "line":  # along a mirror line: on the grid too (user)
                kind, (su, sv), far = self.stuck
                if self.mirror_mode() in ("h", "both") and abs(su - 0.5) < 1e-9:
                    self.stuck = kind, (0.5, round(sv * n) / n), far
                elif self.mirror_mode() in ("v", "both") and abs(sv - 0.5) < 1e-9:
                    self.stuck = kind, (round(su * n) / n, 0.5), far
            if self.stuck:
                return list(self.stuck[1])
            u, v = round(u * n) / n, round(v * n) / n
        return [round(u, 5), round(v, 5)]

    def stick_view(self):
        """(k, ox, oy) for sticky.py: on screen x = ox + u * k, y = oy - v * k."""
        k = self.px()
        return (k, self.canvas.winfo_width() / 2 - self.center[0] * k,
                self.canvas.winfo_height() / 2 + self.center[1] * k)

    def stick_targets(self, skip=frozenset(), skip_pts=frozenset()):
        """sticky.Targets of the strokes (but skip / skip_pts, and hidden ones), remembered until they change."""
        skip = skip | frozenset(i for i in range(len(self.strokes)) if self.is_hidden(i))
        # (the skipped strokes left out of the key: strokes being moved change every step, the rest don't)
        key = (json.dumps([st if i not in skip else None for i, st in enumerate(self.strokes)]), skip, skip_pts)
        if self._stick_cache is None or self._stick_cache[0] != key:
            boxes = [np.r_[p.min(0), p.max(0)] for p in (np.asarray(stroke_points(self.strokes[i]), float).reshape(-1, 2)
                     for i in range(len(self.strokes)) if i not in skip) if len(p)]
            self._stick_cache = (key, Targets(self.strokes, skip, skip_pts), np.array(boxes).reshape(-1, 4))
        return self._stick_cache[1]

    def near_others(self, line, reach):
        """Could the line ((N, 2) board points) touch another stroke within reach (board units)? By their boxes
        (stick_targets' strokes; call that first): moving many strokes, the far ones aren't checked (speed)."""
        boxes = self._stick_cache[2]
        if not len(boxes):
            return False
        lo, hi = line.min(0) - reach, line.max(0) + reach
        return bool(((boxes[:, 0] <= hi[0]) & (boxes[:, 2] >= lo[0]) & (boxes[:, 1] <= hi[1])
                     & (boxes[:, 3] >= lo[1])).any())

    def stick_at(self, x, y):
        """Where screen spot (x, y) sticks, or None. Leaves out what's being changed: the dragged points (a
        polyline's: that point and the pieces beside it; other strokes: the whole stroke), and of the stroke being
        drawn all but a polyline's points and pieces before the last one placed / an arc's start (closing it)."""
        skip, skip_pts, extra_pts, extra_lines = set(), set(), [], []
        drag = self.drag
        if drag and drag[0] == "points":
            for a, b in drag[1]:
                st = self.strokes[a]
                if st["kind"] == "poly" and not has_formula(st) and not st.get("smooth"):
                    skip_pts.add((a, b))
                else:
                    skip.add(a)
        elif drag and drag[0] in ("pen", "corner"):
            skip.add(drag[1])
        draft = self.draft
        if draft and draft["kind"] == "poly" and self.tool.get() == "poly":
            extra_pts = draft["pts"][:-2]
            extra_lines = [extra_pts] if len(extra_pts) > 1 else []
        elif draft and draft["kind"] == "arc" and len(draft["pts"]) == 3 and not self.arc_bend:
            extra_pts = draft["pts"][:1]
        extra_lines = extra_lines + self.mirror_lines()
        return self.stick_targets(frozenset(skip), frozenset(skip_pts)).find(
            x, y, self.stick_view(), REACH * self.scale, extra_pts, extra_lines)

    def circle_targets(self):
        """stick_targets for the Circle tool: the mirror lines count too (it rests on them, touching its copy)."""
        base, lines = self.stick_targets(), self.mirror_lines()
        if self._circle_cache is None or self._circle_cache[:2] != (base, lines):
            self._circle_cache = (base, lines, base.with_lines(lines))
        return self._circle_cache[2]

    def guide_at(self, corner, touches, lines=()):
        """A Circle guide: the corner, and each touched stroke's shape [((u, v) touched, stroke)] moved to pass
        through it (a polyline: only the straight stretch touched, up to a corner of more than 20 degrees: one
        piece of a drawn polyline, more of a freehand stroke's tiny steps). Stroke -1, -2...: mirror line lines[0]..."""
        out = []
        for (tu, tv), i in touches:
            pts = lines[-1 - i] if i < 0 else stroke_points(self.strokes[i])
            if i >= 0 and self.strokes[i]["kind"] == "poly" and len(pts) > 2:
                P = np.asarray(pts, float)
                j0 = j1 = min(range(len(P) - 1), key=lambda j: seg_dist((tu, tv), pts[j], pts[j + 1]))
                r = np.diff(P, axis=0)
                ln = np.hypot(r[:, 0], r[:, 1])
                straight = (r[:-1] * r[1:]).sum(1) >= BEND * ln[:-1] * ln[1:]  # (piece j and j + 1 in one stretch)
                while j0 > 0 and straight[j0 - 1]:
                    j0 -= 1
                while j1 < len(straight) and straight[j1]:
                    j1 += 1
                pts = pts[j0:j1 + 2]
            out.append([(u + corner[0] - tu, v + corner[1] - tv) for u, v in pts])
        return corner, out

    def stuck_pieces(self):
        """The parts of what's being drawn / dragged that end at the stuck point (user: shown purple), as lists of
        (u, v): a polyline's piece being drawn, a square's two sides at the mouse's corner (also with Ctrl, where the
        corner may land off the mark: the mark stays at the mouse, user), the pieces beside a dragged polyline
        point; curves, arcs, circles and moved strokes whole."""
        st, drag = self.draft, self.drag
        if st:
            if st["kind"] == "poly" and self.tool.get() == "poly":
                return [st["pts"][-2:]]
            if st["kind"] == "poly" and self.tool.get() == "square":  # (resting: the sides touching / by a corner;
                runs = []  # side i = pts[i:i + 2], ones next to each other as one line)
                for i in self.square_sides:
                    if runs and runs[-1][-1] == i - 1:
                        runs[-1].append(i)
                    else:
                        runs.append([i])
                if len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == 3:  # (round the start)
                    runs[0] = runs.pop() + runs[0]
                return [[st["pts"][i] for i in run] + [st["pts"][run[-1] + 1]] for run in runs]
            return [stroke_points(st)]
        if not drag:
            return []
        if drag[0] == "points":
            out = []
            for a, b in drag[1]:
                s = self.strokes[a]
                plain = s["kind"] == "poly" and not has_formula(s) and not s.get("smooth")
                out.append(s["pts"][max(b - 1, 0):b + 2] if plain else stroke_points(s))
            return out
        if drag[0] in ("pen", "corner"):
            return [stroke_points(self.strokes[drag[1]])]
        if drag[0] == "stroke" and self.stuck_stroke in drag[2]:  # (the one of those moved that touches)
            return [stroke_points(self.strokes[self.stuck_stroke])]
        return []

    def draw_pieces(self, pieces, width):
        for pts in pieces:
            coords = self.screen_points(pts)
            if len(coords) >= 4:
                self.canvas.create_line(*coords, fill=STICK_LINE, width=width, capstyle="round", joinstyle="round")

    def stick_move(self, moving, du, dv):
        """The strokes moving ({index: stroke as it was when grabbed}) moved by du, dv: (du, dv) changed so they
        stick, or None (nothing in reach). Their points stick to the other strokes' points / crossings / lines, and
        their LINES too (user): a stroke's point onto one, or one resting on a stroke's line (sticky.touch_line).
        Several moved together (a select box, user): the nearest touch of any of them wins (stuck_stroke = which)."""
        targets, view, best = self.stick_targets(frozenset(moving)), self.stick_view(), None
        k, ox, oy = view
        two_best = None
        reach = REACH * self.scale / k
        for i, st in moving.items():
            line = stroke_points(st)
            if len(moving) > 1 and not self.near_others(np.asarray(line, float).reshape(-1, 2) + (du, dv), reach):
                continue
            keys = [p for _, p in key_points(st, line)]
            for u, v in keys:
                only = targets.near((u + du, v + dv), reach) if len(keys) > 8 else None  # (many: only what's near,
                if only is not None and not (only[0] or only[1]):  # speed)
                    continue
                got = targets.find(ox + (u + du) * k, oy - (v + dv) * k, view, REACH * self.scale, only=only)
                if got and got[0] == "line" and st["kind"] == "ellipse":
                    continue  # (its left / right / top / bottom on a slanted line = crossing it: its line rests)
                rank = got and (STICK_RANK[got[0]], -got[2])  # (a point first, then a crossing, then a line; nearest)
                if got and (best is None or rank > best[2]):
                    best = got, (got[1][0] - u, got[1][1] - v), rank, i
            moved = [(u + du, v + dv) for u, v in line]
            for kind, at, far, (su, sv) in targets.touch_line(moved, view, REACH * self.scale):
                rank = STICK_RANK[kind], -far
                if best is None or rank > best[2]:
                    best = ("point" if kind == "on" else "line", at, far), (du + su, dv + sv), rank, i
            two = targets.rest_two(moved, view, REACH * self.scale / 2)  # (on two lines: a smaller reach, user)
            if two and (two_best is None or two[1] < two_best[0][1]):
                two_best = two, i
        if two_best and (best is None or (STICK_RANK["two"], -two_best[0][1]) > best[2]):
            ((su, sv), far, (t1, t2)), i = two_best
            self.stuck2 = "line", t2, far
            best = ("line", t1, far), (du + su, dv + sv), None, i
        if best is None:
            return None
        self.stuck, self.stuck_stroke = best[0], best[3]
        return best[1]

    def perfect(self, start, pt):
        """pt moved so the box from start is square."""
        du, dv = pt[0] - start[0], pt[1] - start[1]
        m = max(abs(du), abs(dv))
        return [start[0] + (m if du >= 0 else -m), start[1] + (m if dv >= 0 else -m)]

    def open_help(self):
        open_help(self.app, self.tool_topic())

    def reset_view(self):
        self.zoom, self.center = 1.0, [0.5, 0.5]
        self.redraw()

    def on_wheel(self, e):
        """Zoom in / out around the mouse."""
        before = self.from_screen(e.x, e.y)
        self.zoom = min(64.0, max(0.05, self.zoom * (1.25 if e.delta > 0 else 0.8)))
        after = self.from_screen(e.x, e.y)
        self.center = [self.center[0] + before[0] - after[0], self.center[1] + before[1] - after[1]]
        self.redraw()

    def start_pan(self, e):
        self._pan = (e.x, e.y, list(self.center))
        self._panned = False

    def pan_to(self, e):
        if not self._pan:
            return
        x, y, (cu, cv) = self._pan
        if abs(e.x - x) > 3 or abs(e.y - y) > 3:
            self._panned = True  # (once it went further than 3 px it's a pan, not a click, even if it comes back)
        k = self.px()
        self.center = [cu - (e.x - x) / k, cv + (e.y - y) / k]
        self.redraw()

    def on_middle_release(self, e):
        """A middle click (without dragging) near the selected curve: a new anchor there (any tool)."""
        if (self._pan and not self._panned and abs(e.x - self._pan[0]) <= 3 and abs(e.y - self._pan[1]) <= 3
                and not self.draft):
            self.add_curve_anchor(e, near=12 * self.scale)

    def show_position(self, e):
        n = int(self.grid_n.get())
        u, v = self.from_screen(e.x, e.y)
        self.pos_label.config(text=tr("drawer.mouse_x_y_grid_squares_from", u=(u - 0.5) * n, v=(v - 0.5) * n))

    def on_key(self, e):
        k = e.keysym.lower()
        if isinstance(e.widget, (tk.Entry, ttk.Entry)):
            return
        if k in MOD_KEYS:
            return self.mod_changed(e, True)
        if k == "escape":
            self.cancel_draft()
        elif k == "return":
            self.finish_poly()
        elif k in ("delete", "backspace"):
            self.delete_selected_stroke()
        elif not e.state & CTRL:
            for tool, _, hot in TOOLS:
                if k == hot:
                    self.tool.set(tool)

    # ------------------------------------------------------------ editing

    def commit(self, pts=None):
        st, self.draft = self.draft, None
        if pts is not None:
            st = {"kind": "poly", "pts": pts}
        self.push_undo()
        new = [st] + self.mirrored(st)
        if len(new) > 1:  # (halves meeting on the mirror line: one line, user)
            new = join_strokes(new)
        self.strokes += new
        if st["kind"] == "curve":
            self.deselect()
            self.sel = len(self.strokes) - len(new) + next(k for k, s in enumerate(new) if s is st)  # selected, so
            # its handles can be bent right away
        self.changed()

    def drop_sel(self):
        """Another tool picked: the selected stroke stops showing its handles. For Slice it stays picked (only the
        picked strokes are cut)."""
        if self.sel is not None and self.tool.get() == "slice":
            self.picks.add(self.sel)
        self.sel = None

    def slice_along(self, a, b):
        """The Slice tool's line a-b (and its Mirror copies) cuts the strokes it crosses: the picked ones if any,
        else every one the board can change (never hidden / locked ones). Each cut stroke's pieces take its place
        and layer ("Line 3", "Line 3 (2)"...), not picked afterwards. Nothing crossed: nothing happens."""
        picked = self.movable()
        idx = set(picked or [i for i in range(len(self.strokes)) if self.pickable(i)])
        self.cut_strokes([(ka, kb, idx) for ka, kb in self.knife_copies(a, b)], picked, "drawer.slice_nothing")

    def knife_copies(self, a, b):
        """A cut line and its Mirror copies (none lying on another): [(a, b)]."""
        knives = [(list(a), list(b))]
        for fn in mirror_fns(self.mirror_mode()):
            k = (list(fn(*a)), list(fn(*b)))
            if not any(same_stroke({"kind": "poly", "pts": list(k)}, {"kind": "poly", "pts": list(q)}) for q in knives):
                knives.append(k)
        return knives

    def split_here(self, i, e):
        """Right-click > Split here on open stroke i: cut in two at the spot on it nearest the mouse (a point or a
        crossing in sticking reach: there; Shift = never), by a tiny cut line across it there; with Mirror, its
        mirror copies at the mirrored spot too."""
        x, y = e.x, e.y
        self.event_pt(e)
        if self.stuck and self.stuck[0] in ("point", "cross"):  # (no grid: on a slanted line it's far off, user)
            x, y = self.to_screen(*self.stuck[1])
        self.stuck = None
        pts = self.screen_points(stroke_points(self.strokes[i]))
        pts = list(zip(pts[::2], pts[1::2]))
        k = min(range(len(pts) - 1), key=lambda j: seg_dist((x, y), pts[j], pts[j + 1]))
        (ax, ay), (bx, by) = pts[k], pts[k + 1]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy or 1
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ll))
        p = np.array(self.from_screen(ax + t * dx, ay + t * dy))
        n = np.array([-dy, -dx]) / math.sqrt(ll) * 1e-4  # (across the piece; screen y is down, board v up)
        a, b = list(p - n), list(p + n)
        knives = [(a, b, {i})]
        for fn in mirror_fns(self.mirror_mode()):  # (only the right-clicked stroke's mirror copies, user)
            m = self.map_stroke(self.strokes[i], fn, exact=True)
            copies = {j for j in range(len(self.strokes)) if self.pickable(j) and same_stroke(m, self.strokes[j])}
            if copies:
                knives.append((list(fn(*a)), list(fn(*b)), copies))
        self.cut_strokes(knives, [], "drawer.split_nothing")

    def cut_strokes(self, knives, picked, nothing):
        """Cuts by knives [(a, b, the strokes it may cut)]; picked = the strokes picked before: the uncut ones stay
        picked, the pieces aren't (user: a click then picks ONE piece to delete, and the next cut isn't held to
        them). nothing: the text key said when nothing was cut."""
        before = self.snap()
        self.keep_names()  # (the pieces after a cut stroke shift the numbers: names stay as they are)
        taken = set(self.layer_names())
        slack = 1.5 * self.scale / self.px()  # (board units: a knife end stuck onto a curve's drawn pieces lies a
        # hair off the curve itself)
        out, cut = [], set()
        for i, st in enumerate(self.strokes):
            pieces = [st]
            # (each knife judged on the stroke as it was: a closed one only by a knife all the way through it)
            for ka, kb, idx in [k for k in knives if i in k[2] and slice_stroke(st, k[0], k[1], slack)]:
                pieces = [q for p in pieces for q in (slice_stroke(p, ka, kb, slack) or [p])]
            if len(pieces) > 1:
                lay = self.layer(i)
                base, num = lay["name"], 1
                m = re.fullmatch(r"(.*) \((\d+)\)", base)
                if m:
                    base, num = m.group(1), int(m.group(2))
                for n, p in enumerate(pieces):
                    name = lay["name"]
                    if n:  # (the next free "Line 3 (n)")
                        while f"{base} ({num})" in taken or num < 2:
                            num += 1
                        name = f"{base} ({num})"
                        taken.add(name)
                    p["layer"] = dict(lay, name=name)
                cut.add(i)
            out.append(pieces)
        if not cut:
            self.load_snap(before)
            self.pos_label.config(text=tr(nothing))
            return self.redraw()
        self.push_undo(before)
        self.strokes, new_idx, k = [], set(), 0
        for i, pieces in enumerate(out):
            for p in pieces:
                if i in picked and i not in cut:
                    new_idx.add(k)
                self.strokes.append(p)
                k += 1
        self.sel, self.picks, self.boxes = None, new_idx, []
        self.changed()

    # ------------------------------------------------------------ mirror

    def mirror_mode(self):
        """"off", "h" (left <-> right), "v" (top <-> bottom) or "both"."""
        names = [tr("drawer.mirror_" + m) for m in MIRRORS]
        box = getattr(self, "mirror_box", None)
        picked = box.current() if box is not None else -1  # (the place: two choices translated alike stay apart;
        if not 0 <= picked < len(names) or names[picked] != self.mirror.get():  # the redraw watcher runs before
            picked = names.index(self.mirror.get()) if self.mirror.get() in names else 0  # the box's text follows)
        return MIRRORS[picked]

    def mirrored(self, st):
        """A new stroke's mirrored copies (Mirror; none that would lie on the stroke itself or on another copy).
        Not rounded: a point stuck exactly on a line stays exactly on that line's mirrored copy."""
        out = []
        for fn in mirror_fns(self.mirror_mode()):
            m = self.map_stroke(st, fn, exact=True)
            if not any(same_stroke(m, s) for s in [st] + out):
                out.append(m)
        return out

    def mirror_lines(self):
        """The lines new strokes are mirrored across, as board point lists (points stick to them too)."""
        mode, far = self.mirror_mode(), 100
        return ([[[0.5, -far], [0.5, far]]] if mode in ("h", "both") else []) + \
               ([[[-far, 0.5], [far, 0.5]]] if mode in ("v", "both") else [])

    def mirror_end(self, pt, state):
        """A freehand stroke's first / last point near a mirror line: on it, so its halves join (Shift = free)."""
        if state & SHIFT:
            return pt
        mode, k = self.mirror_mode(), self.px()
        u, v = pt
        if mode in ("h", "both") and abs(u - 0.5) * k < REACH:
            u = 0.5
        if mode in ("v", "both") and abs(v - 0.5) * k < REACH:
            v = 0.5
        return [u, v]

    def mirror_side(self, u, v):
        """Where the board shows the mirrored side (faint grey): [u, v] -> bool (numpy arrays work too)."""
        mode = self.mirror_mode()
        return (mode in ("h", "both")) & (u > 0.5) | (mode in ("v", "both")) & (v < 0.5)

    def cancel_draft(self):
        self.let_go()  # (a stroke held by Esc / a tool key: moved is moved, like letting go)
        if self.knife:  # (a Slice line dropped: nothing cut)
            self.knife = self.drag = None
        self.draft = None
        self.guide = []
        self.follow = None
        self.arc_bend = False
        self.redraw()

    def snap(self):
        """The drawing (strokes and areas) as JSON, for undo."""
        return json.dumps([self.strokes, self.areas])

    def load_snap(self, text):
        self.strokes, self.areas = json.loads(text)

    def push_undo(self, before=None):
        """A step to undo (before: snap() from before, if they were already changed); drops the redo steps. The
        selection is kept with it: undo puts it back with the strokes."""
        self.undo_stack.append((before or self.snap(), self.sel_state()))
        del self.undo_stack[:-200]
        self.redo_kept, self.redo_stack = self.redo_stack, []

    def sel_state(self):
        """The selection as an undo step keeps it: (the selected stroke, the others selected, the select boxes)."""
        return self.sel, sorted(self.picks), json.loads(json.dumps(self.boxes))

    def erasing(self):
        """An eraser box is being dragged."""
        return bool(self.drag) and self.drag[0] == "erasebox"

    def drop_drag(self):
        """Ctrl+Z / Ctrl+Y while the mouse holds something (like the piano roll): the drag ends first, and a press
        that hasn't moved anything yet leaves no step."""
        drag, self.drag = self.drag, None
        if (drag and drag[0] in ("pen", "corner", "points", "stroke") and self.undo_stack
                and self.undo_stack[-1][0] == self.snap()):
            self.undo_stack.pop()
            self.redo_stack = self.redo_kept

    def undo(self):
        if self.erasing():  # (nothing while an eraser box is held)
            return
        if self.draft or self.knife:
            return self.cancel_draft()
        self.drop_drag()
        self.restore(self.undo_stack, self.redo_stack)

    def redo(self):
        if self.erasing():
            return
        if self.draft or self.knife:
            return self.cancel_draft()
        self.drop_drag()
        self.restore(self.redo_stack, self.undo_stack)

    def restore(self, src, dst):
        """Undo / redo: the strokes and the selection as they were."""
        if not src:
            return self.redraw()  # (a dropped select box goes)
        dst.append((self.snap(), self.sel_state()))
        snap, (self.sel, picks, self.boxes) = src.pop()
        self.load_snap(snap)
        self.picks = set(picks)
        self.changed(settle=False)

    def clear(self):
        if self.strokes or self.areas:
            self.push_undo()
            self.strokes, self.areas = [], []
            self.changed()

    def changed(self, settle=True):
        """After any change. settle: lines drawn, erased or changed in one go: coloured areas go where most of each
        went (custom.settled_areas); not after drags (carry_areas did it), undo / redo, or moves that took the
        colours along themselves."""
        bare = self.bare()  # (hidden strokes too: hiding one for a while doesn't merge the areas it splits)
        now = json.dumps(bare)
        if settle and self.areas and self._settled is not None and self._settled != now:
            frame = AREA_FRAME
            self.areas = settled_areas({"strokes": json.loads(self._settled), "pts": frame, "areas": self.areas},
                                       {"strokes": bare, "pts": frame, "areas": self.areas})
        self._settled = now
        self.dirty = True
        if self.sel is not None and self.sel >= len(self.strokes):
            self.sel = None
        self.picks = {i for i in self.picks if i < len(self.strokes)}
        if not self.chosen():
            self.boxes = []
        self.redraw()

    # ------------------------------------------------------------ library

    def refresh_list(self, select=None):
        self.end_rename(False)
        names = library_names()
        self.listbox.delete(0, "end")
        for n in names:
            self.listbox.insert("end", n)
        if select in names:
            i = names.index(select)
            self.listbox.selection_set(i)
            self.listbox.see(i)
        self.app.refresh_custom_names()

    def picked(self):
        cur = self.listbox.curselection()
        return self.listbox.get(cur[0]) if cur else None

    def list_press(self, e):
        """A click on the name already picked, while the list has the keyboard: renamed after the double-click time
        if no second click comes (like Windows' file lists)."""
        self.stop_slow_click()
        lb = self.listbox
        i = lb.nearest(e.y)
        box = lb.bbox(i)
        if (box and box[1] <= e.y < box[1] + box[3] and lb.curselection() == (i,)
                and self.focus_get() is lb):
            self._slow_click = self.after(DOUBLE_CLICK_MS, self.start_rename)

    def stop_slow_click(self):
        if self._slow_click:
            self.after_cancel(self._slow_click)
            self._slow_click = None

    def start_rename(self):
        """A box over the picked name in the list to type its new name: Enter or a click elsewhere = renamed,
        Esc = not."""
        self._slow_click = None
        name = self.picked()
        if not name or self.renaming:
            return
        if not shape_path(name):
            messagebox.showinfo(tr("drawer.spiderweb"), tr("drawer.is_built_in_rename", name=name), parent=self)
            return
        lb = self.listbox
        i = lb.curselection()[0]
        lb.see(i)
        lb.update_idletasks()
        x, y, w, h = lb.bbox(i)
        box = self.renaming = ttk.Entry(lb)
        box.old = name
        box.insert(0, name)
        box.select_range(0, "end")
        box.icursor("end")
        box.place(x=0, y=y - 3, relwidth=1, height=h + 6)
        box.focus_set()
        box.bind("<Return>", lambda e: (self.end_rename(True), lb.focus_set(), "break")[2])
        box.bind("<Escape>", lambda e: (self.end_rename(False), lb.focus_set(), "break")[2])
        box.bind("<FocusOut>", lambda e: self.end_rename(True))

    def end_rename(self, keep):
        """The rename box goes; keep = the name typed is used."""
        box, self.renaming = self.renaming, None
        if not box:
            return
        old, new = box.old, clean_name(box.get())
        box.destroy()
        if not keep or not new or new == old:
            return
        if new.lower() != old.lower() and new.lower() in (n.lower() for n in library_names()):
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.rename_taken", name=new), parent=self)
            return
        here = (self.saved_name or "").lower() == old.lower()  # (the drawing open here)
        fresh = here and not self.changed_elsewhere()
        try:
            rename_shape(old, new)
        except ValueError:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_read_the_shape", name=old), parent=self)
            return
        except OSError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_rename", e=e), parent=self)
            return
        if here:
            self.saved_name = new
            self.saved_stamp = shape_stamp(new) if fresh else None
            if clean_name(self.name.get()).lower() == old.lower():
                self.name.set(new)
        app = self.app
        if app.custom_shape.lower() == old.lower():  # (the Custom shape tool's shape: renamed there too)
            app.custom_shape = new
            app.sync_custom()
            app.schedule_autosave()
        self.refresh_list(select=new)

    def keep_changes(self):
        """True if it's fine to throw away the drawing (nothing unsaved, or the user said so)."""
        return not (self.dirty and self.strokes) or messagebox.askyesno(
            tr("drawer.spiderweb"), tr("drawer.the_current_drawing_isn_t_saved"), parent=self)

    def may_quit(self):
        """Spiderweb is closing: True if the drawing is saved, or the user says to close anyway (the drawer shown
        first, so they see what isn't saved)."""
        if not (self.dirty and self.strokes):
            return True
        self.deiconify()
        self.lift()
        return messagebox.askyesno(tr("drawer.spiderweb"), tr("drawer.unsaved_quit"), icon="warning", default="no",
                                   parent=self)

    def open_selected(self):
        name = self.picked()
        if not name or not self.keep_changes():
            return
        strokes, areas = load_drawing(name, layers=True)  # (as drawn: hidden strokes, names, locks, groups)
        if strokes is None:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_read_the_shape", name=name), parent=self)
            return
        self.open_shape(name, strokes, areas)

    def open_shape(self, name, strokes, areas=()):
        self.strokes, self.undo_stack, self.draft = strokes, [], None
        self.deselect()
        self.areas = [list(a) for a in areas]
        self._settled = json.dumps(self.bare())
        self.redo_stack, self.redo_kept = [], []
        self.name.set(name)
        self.saved_name = name or None
        self.saved_stamp = shape_stamp(name) if name else None
        self.dirty = False
        self.redraw()

    def new(self):
        if self.keep_changes():
            self.open_shape("", [], [])

    def delete_selected(self):
        name = self.picked()
        if not name:
            return
        saved = name.lower() in (n.lower() for n in saved_names())
        if not saved:
            messagebox.showinfo(tr("drawer.spiderweb"), tr("drawer.is_built_in_and_can_t", name=name), parent=self)
            return
        back = tr("drawer.the_built_in_comes_back_in", built_in_name=built_in_name(name)) if built_in_name(name) else ""
        if not messagebox.askyesno(
                tr("drawer.spiderweb"), tr("drawer.delete_from_the_library_shapes_already",
                                           name=name) + back, icon="warning", parent=self):
            return
        try:
            os.remove(shape_path(name))
        except OSError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_delete", e=e), parent=self)
            return self.refresh_list()
        if name.lower() == (self.saved_name or "").lower():  # the drawing open here: not saved any more
            self.saved_name = self.saved_stamp = None
            self.dirty = True
            self.redraw()
        app = self.app
        if app.custom_shape.lower() == name.lower() and not app.custom_template(app.custom_shape):
            app.custom_shape = "Circle"  # (the Custom shape tool used it: back to Circle, user)
            app.sync_custom()
            app.schedule_autosave()
            app.status.config(text=tr("drawer.deleted_in_use", name=name))
        self.refresh_list()

    def save(self):
        """Save to the library under the name in the box. Returns the name, or None if it wasn't saved."""
        name = clean_name(self.name.get())
        if not self.strokes:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.draw_something_first"), parent=self)
            return None
        if not name:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.give_the_shape_a_name_first"), parent=self)
            return None
        taken = name.lower() in (n.lower() for n in library_names())
        if taken and name.lower() != (self.saved_name or "").lower():
            if not messagebox.askyesno(tr("drawer.spiderweb"),
                                       tr("drawer.is_already_in_the_library_replace", name=name), parent=self):
                return None
        elif taken and self.changed_elsewhere():  # (e.g. the piano roll's "Save drawing to the shape library")
            if not messagebox.askyesno(tr("drawer.spiderweb"), tr("drawer.changed_elsewhere", name=name),
                                       icon="warning", parent=self):
                return None
        try:  # (as drawn, layers and all: lines meeting end to end are joined when the shape is placed)
            save_shape(name, self.strokes, self.areas)
        except OSError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_save", e=e), parent=self)
            return None
        self.name.set(name)
        self.saved_name = name
        self.saved_stamp = shape_stamp(name)
        self.dirty = False
        self.refresh_list(select=name)
        self.redraw()
        return name

    def export(self):
        """The whole drawing (with the name box's name) on the clipboard as a shared line (share.py)."""
        name = clean_name(self.name.get())
        if not self.strokes:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.draw_something_first"), parent=self)
            return
        if not name:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.give_the_shape_a_name_first"), parent=self)
            return
        line = drawing_line(name, self.strokes, self.areas)
        if not put_text(line):
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.clipboard_busy"), parent=self)
            return
        self.app.remember_clip()  # (so Ctrl+V on the piano roll doesn't take it for new shapes)
        messagebox.showinfo(tr("drawer.spiderweb"), tr("drawer.exported", name=name) +
                            (tr("drawer.exported_long", chars=len(line)) if len(line) > LONG_LINE else ""),
                            parent=self)

    def import_shared(self):
        """A drawing shared as text -> a new shape in the library (a taken name gets " (2)", " (3)", ...).
        The drawing being drawn isn't touched."""
        text = get_text()
        if text is None:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.clipboard_busy"), parent=self)
            return
        try:
            got = unpack(text)
            if got["kind"] != "drawing":
                messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.import_shapes"), parent=self)
                return
            old, strokes, areas = read_drawing(got)
        except ShareError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr(f"drawer.import_{e.why}"), parent=self)
            return
        old = clean_name(old) or tr("drawer.imported_shape")
        taken = {n.lower() for n in library_names()}
        name, k = old, 2
        while name.lower() in taken:
            name, k = f"{old} ({k})", k + 1
        try:
            save_shape(name, strokes, areas)
        except OSError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_save", e=e), parent=self)
            return
        self.refresh_list(select=name)
        made = made_by(got)  # (made by another Spiderweb version: a heads-up)
        messagebox.showinfo(tr("drawer.spiderweb"), (tr("drawer.imported", name=name) if name == old else
                            tr("drawer.imported_as", name=name, old=old)) +
                            ("\n\n" + tr(f"share.made_{made[0]}", version=made[1]) if made else ""), parent=self)

    def changed_elsewhere(self):
        """The shape open here was saved since it was opened / saved here (its file changed or went)."""
        return bool(self.saved_name) and shape_stamp(self.saved_name) != self.saved_stamp

    def use(self):
        if self.strokes and not self.shown_idx():
            messagebox.showerror(tr("drawer.spiderweb"), tr("layers.all_hidden"), parent=self)
            return
        same =(self.saved_name and clean_name(self.name.get()) == self.saved_name and not self.dirty
                and not self.changed_elsewhere())
        name = self.saved_name if same else self.save()
        if name:
            self.app.use_custom(name)

    def close(self):
        if self.keep_changes():
            self.app.drawer = None
            self.destroy()

    # ------------------------------------------------------------ drawing

    def redraw(self):
        self.show_colours_count()
        c = self.canvas
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        (x0, y0), (x1, y1) = self.to_screen(0, 1), self.to_screen(1, 0)
        img = self.area_image(cw, ch) if cw > 1 and ch > 1 else None
        if img is not None:  # the board with the areas' colours
            c.create_image(0, 0, image=img, anchor="nw")
        else:
            c.create_rectangle(x0, y0, x1, y1, fill=BOARD, outline="")  # the board
            mode = self.mirror_mode()  # Mirror: the side that gets the copy, faint grey
            for on, (u0, v0, u1, v1) in ((mode in ("h", "both"), (0.5, 0, 1, 1)),
                                         (mode in ("v", "both"), (0, 0, 1, 0.5))):
                if on:
                    c.create_rectangle(*self.to_screen(u0, v1), *self.to_screen(u1, v0), fill=look.MIRROR_SIDE,
                                       outline="")
        # Grid lines over the whole window (every line when they're far enough apart, else only the quarters)
        n = int(self.grid_n.get())
        k = self.px() / n  # pixels per grid square
        major = n // 4 if n % 4 == 0 else n
        step = 1 if k >= 5 else major if k * major >= 5 else None
        (u_lo, v_hi), (u_hi, v_lo) = self.from_screen(0, 0), self.from_screen(cw, ch)
        if step:
            for lo, hi, vertical in ((u_lo, u_hi, True), (v_lo, v_hi, False)):
                i = math.ceil(lo * n / step) * step
                while i <= hi * n:
                    if i * 2 == n:
                        color = look.BOARD_MIDDLE  # the middle of the board: 0
                    else:
                        color = look.BOARD_GRID_MAJOR if i % major == 0 else look.BOARD_GRID
                    if vertical:
                        x = self.to_screen(i / n, 0)[0]
                        c.create_line(x, 0, x, ch, fill=color)
                    else:
                        y = self.to_screen(0, i / n)[1]
                        c.create_line(0, y, cw, y, fill=color)
                    i += step
        c.create_rectangle(x0, y0, x1, y1, outline=look.BOARD_EDGE)
        mode, mx, my = self.mirror_mode(), *self.to_screen(0.5, 0.5)  # Mirror: thin lines across the window
        for on, coords in ((mode in ("h", "both"), (mx, 0, mx, ch)), (mode in ("v", "both"), (0, my, cw, my))):
            if on:
                c.create_line(*coords, fill=look.MIRROR_LINE, width=max(2, round(2 * self.scale)))
        w = max(2, round(2 * self.scale))
        gaps = self.gaps()
        shown_idx = self.shown_idx()  # (hidden strokes aren't drawn)
        closed = any(not role_of(self.strokes[i]) for i in shown_idx) and not gaps
        chosen = set(self.chosen())
        for i in shown_idx:  # a stroke with formulas: the stroke as drawn (the origin path), dashed
            st = self.strokes[i]
            if st["kind"] != "ellipse" and has_formula(st):
                self.draw_stroke(plain_stroke(st), look.ORIGIN_PICKED if i in chosen else look.ORIGIN, 1, dash=(6, 4))
        for i in shown_idx:  # outline only: dotted; fill line: thin dashes
            st = self.strokes[i]
            role = role_of(st)
            color = (look.STROKE_PICKED if i in chosen else
                     look.readable(SLOT_COLORS[(colour_of(st) - 1) % len(SLOT_COLORS)][1])
                     if colour_of(st) else STROKE_COLOR)
            if role == "cut":
                self.draw_stroke(st, color, max(1, round(self.scale)), dash=(6, 4))  # (thin: Windows dots thick ones)
            else:
                self.draw_stroke(st, color, w + (1 if i in chosen else 0), dash=(12, 4) if role else None)
        pieces = self.stuck_pieces() if self.stuck else []
        if not self.draft:
            self.draw_pieces(pieces, w + 1)
        s = self.scale
        r, h = 4 * s, 3.5 * s
        sel = self.strokes[self.sel] if self.sel is not None and self.pickable(self.sel) else None
        curve = sel if sel and sel["kind"] == "curve" else None
        if curve:  # handle lines: blue on a white edge
            for width, color in ((max(3, round(3.5 * s)), look.HANDLE_FILL), (max(1, round(1.5 * s)), look.HANDLE)):
                for a, b in handle_lines(curve["pts"]):
                    c.create_line(*self.to_screen(*a), *self.to_screen(*b), fill=color, width=width)
        if self.tool.get() == "select":  # the ends of strokes, ellipse corners: squares
            for i, j, u, v in self.handles():
                if not self.is_pen_point(i, j):
                    x, y = self.to_screen(u, v)
                    c.create_rectangle(x - h, y - h, x + h, y + h, fill=look.HANDLE_FILL,
                                       outline=look.POINT_PICKED if i in chosen else look.HANDLE)
        if curve:  # its handle dots and anchors work with any tool
            for j, kind in pen_handles(curve["pts"]):
                x, y = self.to_screen(*curve["pts"][j])
                if kind == "ctrl":
                    q = r + 0.5 * s
                    c.create_oval(x - q, y - q, x + q, y + q, fill=look.HANDLE, outline=look.HANDLE_FILL,
                                  width=max(1, round(s)))
                elif kind == "anchor":
                    q = r + 1.5 * s
                    c.create_oval(x - q, y - q, x + q, y + q, fill=look.HANDLE_FILL, outline=look.HANDLE,
                                  width=max(2, round(2 * s)))
        for u, v in (end for path in gaps for end in (path[0], path[-1])):  # open ends: red dots
            x, y = self.to_screen(u, v)
            c.create_oval(x - r, y - r, x + r, y + r, fill=look.OPEN_END, outline=look.OPEN_END_EDGE)
        if self.draft:
            for st in [self.draft] + self.mirrored(self.draft):  # (Mirror: its copies follow live)
                self.draw_stroke(st, look.DRAFT_LINE, w)
            self.draw_pieces(pieces, w + 1)
            self.draw_draft_points(r, h)
        if self.knife:  # the Slice line (red dashed, like the piano roll's), and its Mirror copies
            a, b = self.knife
            for p, q in [(a, b)] + [(fn(*a), fn(*b)) for fn in mirror_fns(self.mirror_mode())]:
                c.create_line(*self.to_screen(*p), *self.to_screen(*q), fill=look.CUT, width=max(1, round(s)),
                              dash=(6, 4))
        for spot, lines in self.guide if self.draft else []:  # a dotted ring with a dot inside on the touched
            # strokes' faint dotted copies (thin: Windows draws thick dotted lines solid)
            x, y = self.to_screen(*spot)
            q, p = 7 * s, 2 * s
            for line in lines:
                coords = self.screen_points(line)
                if len(coords) >= 4:
                    c.create_line(*coords, fill=look.STICK_GUIDE, width=max(1, round(s)), dash=(2, 4))
            c.create_oval(x - q, y - q, x + q, y + q, outline=STICK_COLOR, width=max(1, round(s)), dash=(2, 3))
            c.create_oval(x - p, y - p, x + p, y + p, fill=STICK_COLOR, outline="")
        if self.chosen() and self.tool.get() == "select":  # the kept select boxes
            for box in self.screen_boxes():
                c.create_rectangle(*box, outline=look.HANDLE, width=max(1, round(s)), dash=(4, 2))
        if self.drag and self.drag[0] in ("boxsel", "erasebox"):  # a select / eraser box being dragged
            c.create_rectangle(*self.drag[1:5], outline=look.HANDLE if self.drag[0] == "boxsel" else look.ERASE_BOX,
                               width=max(1, round(s)), dash=(4, 2))
        for kind, (u, v), _ in [m for m in (self.stuck, self.stuck and self.stuck2) if m]:  # where the point
            # sticks (a circle on two lines: both): a square on a point, an X on a crossing, a diamond on a line
            x, y = self.to_screen(u, v)
            q, lw = 6 * s, max(2, round(2 * s))
            if kind == "point":
                c.create_rectangle(x - q, y - q, x + q, y + q, outline=STICK_COLOR, width=lw)
            elif kind == "cross":
                c.create_line(x - q, y - q, x + q, y + q, fill=STICK_COLOR, width=lw)
                c.create_line(x - q, y + q, x + q, y - q, fill=STICK_COLOR, width=lw)
            else:
                c.create_polygon(x, y - q, x + q, y, x, y + q, x - q, y, fill="", outline=STICK_COLOR, width=lw)
        hidden = len(self.strokes) - len(shown_idx)
        if not self.strokes:
            text = tr("drawer.nothing_drawn_yet")
        elif not shown_idx:
            text = tr("layers.all_hidden_state")
        elif all(role_of(self.strokes[i]) for i in shown_idx):
            amap = self.area_info()[0] if self.areas else None  # (an area coloured by hand is filled anyway)
            closed = amap is not None and bool((self.area_paint(amap) > 0).any())
            text = tr("drawer.only_coloured_filled" if closed else "drawer.nothing_to_fill")
        elif closed:
            text = tr("drawer.closed_shape_empty_fill_and_spam")
        elif len(gaps) == 1:
            text = tr("drawer.one_gap_red_dots_fill_and")
        else:
            text = tr("drawer.open_ends_red_dots_fill_and")
        if hidden and shown_idx:  # (user: warn that hidden strokes are left out)
            text += "\n" + tr("layers.hidden_warn", n=hidden)
            closed = False
        if self.dirty and self.strokes:
            text += tr("drawer.not_saved_yet")
        self.state_label.config(text=text, foreground=look.GOOD if closed else look.WARN_DARK)
        self.sync_layers()

    def draw_draft_points(self, r, h):
        """The points of the stroke being drawn: its ends, a polyline's / square's corners, an arc's three points
        (the middle one round), a circle's box corners. Not freehand, not a curve's handles (they come once it's
        drawn)."""
        st, c, s = self.draft, self.canvas, self.scale
        if self.drag and self.drag[0] == "free":
            return
        if st["kind"] == "ellipse":
            u0, v0, u1, v1 = st["box"]
            pts = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        else:
            pts = [st["pts"][0], st["pts"][-1]] if st["kind"] == "curve" else st["pts"]
        for j, (u, v) in enumerate(pts):
            x, y = self.to_screen(u, v)
            if st["kind"] == "arc" and j == 1 and len(pts) == 3:
                q = r + 1.5 * s
                c.create_oval(x - q, y - q, x + q, y + q, fill=look.HANDLE_FILL, outline=look.HANDLE, width=max(2, round(2 * s)))
            else:
                c.create_rectangle(x - h, y - h, x + h, y + h, fill=look.HANDLE_FILL, outline=look.DRAFT_LINE,
                                   width=max(1, round(s)))

    def draw_stroke(self, st, color, width, dash=None):
        coords = self.screen_points(stroke_points(st))
        if len(coords) >= 4:
            self.canvas.create_line(*coords, fill=color, width=width, capstyle="round", joinstyle="round", dash=dash)
        elif coords:
            x, y = coords
            self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color)
