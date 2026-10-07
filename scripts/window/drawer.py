"""The custom shape drawer (its own window) and the shape library: built-in shapes plus the user's own in spiderweb/shapes/*.json."""

import json
import math
import os
import tkinter as tk
from tkinter import ttk, messagebox
from types import SimpleNamespace

from files.lang import tr
from notes.bezier import (add_anchor, can_delete, delete_point, drag_point, half_at, handle_lines, nearest, pen_handles,
                          set_symmetry)
import numpy as np

from notes.areas import COLOURS, clean_areas
from notes.custom import (DRAWN_FRAME, ROLES, area_paint, areas_filled, carry_areas, carried_spots,
                          clean_strokes, colour_of, filled_spots, join_strokes, open_paths, plain_stroke, role_of, settled_areas,
                          shape_areas, stroke_points, takes_formula)
from roll.roll_shared import SLOT_COLORS
from notes.pattern import has_formula, moved_formulas
from files.about import HERE
from files.safefile import write_text
from files.clipboard import get_text, put_text
from files.share import LONG_LINE, ShareError, drawing_line, made_by, read_drawing, unpack
from files.speed import Photo
from files.system import ALT, double_click_ms
from roll.roll_shared import BOX_STILL, grab_while_panning, line_touches_box, mouse_trail, shown_points
from window import look
from window.help import open_help
from window.formula_host import DrawerHost, formula_menu
from window.help_texts import BY_ID, DRAWER_TOOL_TOPICS
from window.layers import DrawerLayers, pasted
from window.sticky import REACH, Targets, key_points
from window.widgets import Tooltip, symmetry_menu

LIBRARY = os.path.join(HERE, "shapes")
# Always in the library (not files). A saved shape with the same name is used instead; deleting it brings these back.
BUILT_IN = {
    "Circle": [{"kind": "ellipse", "box": [0, 0, 1, 1]}],
    "Square": [{"kind": "poly", "pts": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]}],
    "Triangle": [{"kind": "poly", "pts": [[0, 0], [1, 0], [0.5, 1], [0, 0]]}],
}
GRIDS = ["4", "8", "12", "16", "24", "32", "48", "64"]
TOOLS = [("select", tr("drawer.select"), "v"), ("erase", tr("drawer.eraser"), "e"), ("line", tr("drawer.line"), "l"),
         ("poly", tr("drawer.polyline"), "p"), ("free", tr("drawer.freehand"), "f"), ("curve", tr("drawer.curve"), "c"),
         ("arc", tr("drawer.arc"), "a"), ("square", tr("drawer.square"), "s"), ("circle", tr("drawer.circle"), "o"),
         ("areas", tr("drawer.areas"), "b")]
SHIFT, CTRL = 0x1, 0x4
STROKE_COLOR = look.STROKE  # (a stroke with an outline colour: that colour's dark shade)
# Areas (areas.py) on the board: what Fill / Spam fill as normal, an area emptied by hand, the outside, the board
AREA_NORMAL, AREA_EMPTY, OFF_BOARD, BOARD = look.AREA_NORMAL, look.AREA_EMPTY, look.DRAWER_BG, look.BOARD
WARN_COLOR = look.WARN  # more colours than a shape can have (like the side panel's warning)
AREA_FRAME = DRAWN_FRAME  # (the drawing's box as a custom shape, for finding its areas)
STICK_RANK = {"point": 5, "cross": 4, "two": 3, "on": 2, "rest": 1, "line": 0}  # (moving a stroke: on / two / rest
# = its line)
STICK_COLOR = look.STICK
STICK_LINE = look.STICK_LINE
GUIDE_REACH = 6 * REACH  # pixels: a circle this near to sticking shows where it would touch (dotted)
LIST_AWAY = look.LIST_AWAY  # the shape picked in the library list while the keyboard is elsewhere (blue when it's there)
DOUBLE_CLICK_MS = double_click_ms()  # (the system's own setting)
DRAW_TOOLS = ("line", "poly", "curve", "arc", "square", "circle")  # (the ones whose points stick)
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


def rgb(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def area_color(c):
    """The colour shown for area colour c (1 .. COLOURS): the note colours in order."""
    return SLOT_COLORS[(c - 1) % len(SLOT_COLORS)][0]


def colour_menu(parent, var, pick):
    """A menu of the shape's own colour and colours 1 .. COLOURS (a stroke's outline colour), var = the one on."""
    m = tk.Menu(parent, tearoff=0)
    m.add_radiobutton(label=tr("drawer.colour_own"), value=0, variable=var, command=lambda: pick(0))
    for c in range(1, COLOURS + 1):
        m.add_radiobutton(label=tr("drawer.area_n", n=c), value=c, variable=var, command=lambda c=c: pick(c))
    return m
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


def load_shape(name):
    """The strokes of a library shape, or None if it's missing or broken."""
    return load_drawing(name)[0]


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


def file_stem(name):
    """A shape's name as a file name Windows takes: the characters it refuses -> "_", no dot / space at the end,
    not one of its device names ("CON" -> "CON (shape)")."""
    stem = "".join("_" if c in BAD_CHARS or c < " " else c for c in name).strip().rstrip(". ")
    base, dot, rest = stem.partition(".")
    if base.strip().upper() in DEVICES:
        stem = f"{base} (shape){dot}{rest}"
    return stem or "shape"


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

class Drawer(DrawerLayers, tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        s = self.scale = app.scale
        self.title(tr("drawer.spiderweb_custom_shape_drawer"))
        self.geometry(f"{int(1000 * s)}x{int(720 * s)}")
        self.minsize(int(700 * s), int(500 * s))
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
        self.stuck2 = None     # a circle / square / moved stroke resting on two lines: the second touch's mark
        self.square_sides = [1, 2]  # a square being drawn: its sides lit (stuck_pieces; 1, 2 = at the mouse's corner)
        self.guide = []        # a circle near sticking or stuck: where to point the mouse for it to touch, each
        # ((u, v), [the touched strokes' points moved through it])
        self._stick_cache = None
        self.zoom = 1.0        # 1 = the whole board fits the window
        self.center = [0.5, 0.5]  # the board point in the middle of the window (0.5, 0.5 = the board's middle)
        self._pan = None
        self._panned = False  # the middle button moved further than a click's 3 px (pan_to)
        self.tool = tk.StringVar(value="poly")
        self.grid_n = tk.StringVar(value="16")
        self.mirror = tk.StringVar(value=tr("drawer.mirror_off"))  # (its shown name: mirror_mode)
        self.name = tk.StringVar()
        self._build()
        self.refresh_list()
        self.tool.trace_add("write", lambda *_: (setattr(self, "sel", None), self.cancel_draft(), self.on_tool()))
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

    # ------------------------------------------------------------ areas (coloured by hand: areas.py)

    def draw_swatches(self):
        c, k = self.swatches, self.swatch
        c.delete("all")
        for n in range(COLOURS + 1):
            x, y = 2 + n * (k + 2), 2
            on = n == self.area_pick
            c.create_rectangle(x, y, x + k, y + k, fill=area_color(n) if n else AREA_EMPTY,
                               outline=look.SWATCH_EDGE_ON if on else look.SWATCH_EDGE, width=2 if on else 1)
            if not n:  # Empty: a cross
                c.create_line(x + 3, y + 3, x + k - 3, y + k - 3, fill=look.STROKE)
                c.create_line(x + k - 3, y + 3, x + 3, y + k - 3, fill=look.STROKE)

    def swatch_at(self, x):
        n = int((x - 2) // (self.swatch + 2))
        return n if 0 <= n <= COLOURS else None

    def pick_swatch(self, e):
        n = self.swatch_at(e.x)
        if n is not None:
            self.area_pick = n
            self.draw_swatches()
            self.swatch_tip(e)

    def swatch_tip(self, e):
        n = self.swatch_at(e.x)
        if n is not None:
            self.pos_label.config(text=tr("drawer.area_empty") if n == 0 else tr("drawer.area_n", n=n))

    def area_info(self):
        """(AreaMap of the drawing in board u, v, which areas Fill / Spam fill as normal), remembered."""
        shown = self.shown()  # (hidden strokes are left out of the shape)
        key = json.dumps(shown)
        if self._area_cache and self._area_cache[0] == key:
            return self._area_cache[1:3]
        sh = {"kind": "custom", "strokes": shown, "pts": AREA_FRAME, "fill": "fill"}
        amap = shape_areas(sh) if shown else None
        inside = None
        if amap is not None:
            inside = areas_filled(sh, amap)
        self._area_cache = (key, amap, inside, sh)
        self._area_px = None
        return amap, inside

    def face_info(self):
        """For drawing the areas smooth (AreaMap.exact): (faces, each one's area number, which ones Fill / Spam fill
        as normal), remembered."""
        amap, _ = self.area_info()
        if len(self._area_cache) < 5:
            fc, lab, x, y, g = amap.exact()
            filled = np.zeros(len(lab), bool)
            filled[g] = filled_spots(self._area_cache[3], x, y)
            self._area_cache += ((fc, lab, filled),)
        return self._area_cache[4]

    def gaps(self):
        """The drawing's open outlines (custom.open_paths), remembered until the strokes change."""
        shown = self.shown()
        key = json.dumps(shown)
        if self._gap_cache is None or self._gap_cache[0] != key:
            self._gap_cache = (key, open_paths(shown))
        return self._gap_cache[1]

    def area_at(self, e):
        """The area at the mouse, or None (outside the drawing, or nothing drawn)."""
        return self.area_spot(e.x, e.y)[0]

    def area_spot(self, x, y):
        """(The area at canvas spot x, y, exact on the lines; a spot (u, v) in it to keep its colour at), or (None,
        None) outside the drawing / nothing drawn."""
        amap, _ = self.area_info()
        if amap is None:
            return None, None
        u, v = self.from_screen(x, y)
        fc, lab, _ = self.face_info()
        f = int(fc.area_at([u], [v])[0])
        a = int(lab[f])
        if a < 0 or a == amap.outside():
            return None, None
        if int(amap.at([u], [v])[0]) != a:  # (right by a line, where the cells say the next area: its own spot)
            _, _, sx, sy, g = amap.exact()
            i = int(np.flatnonzero(g == f)[0])
            u, v = float(sx[i]), float(sy[i])
        return a, (u, v)

    def area_paint(self, amap):
        """Each area's colour as given by hand (-1: as normal, 0: empty, k: colour k)."""
        return area_paint({"areas": self.areas}, amap)

    def area_press(self, e):
        """Click = the area under the mouse gets the picked colour; dragging on colours every area it passes
        (one undo step)."""
        self.drag = ["areas", e.x, e.y, False]  # (the last spot, whether the undo step is pushed yet)
        if self.area_at(e) is None:
            self.pos_label.config(text=tr("drawer.area_outside"))
        self.paint_areas([(e.x, e.y)])

    def area_drag(self, e):
        _, lx, ly, _ = self.drag
        n = max(1, int(math.hypot(e.x - lx, e.y - ly) // 3))  # every 3 px along the way (the mouse skips)
        self.paint_areas([(lx + (e.x - lx) * i / n, ly + (e.y - ly) * i / n) for i in range(1, n + 1)])
        self.drag[1:3] = e.x, e.y

    def paint_areas(self, spots):
        """The areas at these canvas spots get the picked colour."""
        picks = {}
        for x, y in spots:
            lab, spot = self.area_spot(x, y)
            if lab is not None and lab not in picks:
                picks[lab] = spot
        if picks and self.set_areas(picks, self.area_pick, undo=not self.drag[3]):
            self.drag[3] = True

    def set_area(self, lab, spot, colour):
        """Area lab gets colour (None: back to normal); spot = where it was clicked (u, v)."""
        self.set_areas({lab: spot}, colour)

    def set_areas(self, picks, colour, undo=True):
        """Areas {lab: (u, v) where it was clicked} get colour (None: back to normal). Returns whether anything
        changed."""
        amap, _ = self.area_info()
        paint = self.area_paint(amap)
        picks = {k: s for k, s in picks.items() if paint[k] != (-1 if colour is None else colour)}
        if not picks:
            return False
        labs = amap.at([a[0] for a in self.areas], [a[1] for a in self.areas]).tolist() if self.areas else []
        keep = [a for a, k in zip(self.areas, labs) if k not in picks]
        new = keep + ([[round(u, 5), round(v, 5), colour] for u, v in picks.values()] if colour is not None else [])
        if undo:
            self.push_undo()
        self.areas = new
        self.changed()
        return True

    def colours_count(self):
        """How many colours the placed shape gets with Fill / Spam: (without "Outline", with it). The shape's own
        colour counts where an area is filled as normal or an outline-only line has no colour of its own (other
        lines are the edge of the fill beside them); "Outline" adds one."""
        amap, inside = self.area_info()
        paint = self.area_paint(amap)[:-1] if amap is not None else np.zeros(0, np.int64)
        if amap is not None:  # (only the areas shown: where many lines meet at a point, the map's cells leave
            # little pockets no note is ever in, which counted the shape's own colour with every area coloured)
            _, lab, _ = self.face_info()
            shown = np.unique(lab[amap.exact()[4]])  # (each shown area's number, by a spot in it)
            paint, inside = paint[shown], inside[shown]
        shown = self.shown()
        used = {int(c) for c in paint if c > 0} | {colour_of(st) for st in shown if colour_of(st)}
        own_fill = amap is not None and bool((inside & (paint < 0)).any())
        own_line = any(not colour_of(st) and role_of(st) == "edge" for st in shown)
        return len(used) + (own_fill or own_line), len(used) + own_fill + 1

    def show_colours_count(self):
        if not self.area_bar.winfo_manager():
            return
        n, with_outline = self.colours_count() if self.shown_idx() else (0, 0)
        text = tr("drawer.colours_used", n=n, most=COLOURS, m=with_outline)
        if with_outline > COLOURS:
            text += " " + tr("drawer.colours_too_many", most=COLOURS)
        if self.colours_used.cget("text") != text:
            self.colours_used.config(text=text, foreground=WARN_COLOR if with_outline > COLOURS else "")

    def reset_areas(self):
        """Every area back to how Fill / Spam fill it as normal (asks first)."""
        if not self.areas:
            self.pos_label.config(text=tr("drawer.areas_none_coloured"))
            return
        if not messagebox.askyesno(tr("drawer.spiderweb"), tr("drawer.areas_reset_ask"), icon="warning", parent=self):
            return
        self.push_undo()
        self.areas = []
        self.changed()

    def area_image(self, cw, ch):
        """The board with the areas' colours, as a picture of the whole canvas (None: no areas to show)."""
        if not (self.areas or self.tool.get() == "areas"):  # (finding the areas is slow on big drawings)
            return None
        amap, _ = self.area_info()
        if amap is None:
            return None
        fc, area, filled = self.face_info()  # (each pixel looked up on the lines themselves: smooth edges)
        view = (cw, ch, self.zoom, tuple(self.center), id(amap))
        if self._area_px is None or self._area_px[0] != view:
            k = self.px()
            u = self.center[0] + (np.arange(cw) + 0.5 - cw / 2) / k
            v = self.center[1] - (np.arange(ch) + 0.5 - ch / 2) / k
            face = fc.area_grid(u, v)
            board = ((u >= 0) & (u <= 1))[None, :] & ((v >= 0) & (v <= 1))[:, None]
            self._area_px = (view, face, board, u, v)
        _, face, board, u, v = self._area_px
        paint = self.area_paint(amap)[area]  # (each face's)
        lut = np.zeros((len(area), 3), np.uint8)
        tint = np.zeros(len(area), bool)
        normal = filled & (paint < 0)
        lut[normal], tint[normal] = rgb(AREA_NORMAL), True
        lut[paint == 0], tint[paint == 0] = rgb(AREA_EMPTY), True
        for c in range(1, COLOURS + 1):
            lut[paint == c], tint[paint == c] = rgb(area_color(c)), True
        if self.hover is not None:  # the area under the mouse: darker
            h = area == self.hover
            lut[h & tint] = (lut[h & tint] * 0.75).astype(np.uint8)
            lut[h & ~tint] = rgb(look.AREA_HOVER)
            tint[h] = True
        img = np.where(board[..., None], np.uint8(rgb(BOARD)), np.uint8(rgb(OFF_BOARD))).astype(np.uint8)
        if self.mirror_mode() != "off":
            side = board & self.mirror_side(u[None, :], v[:, None])
            img[side] = rgb(look.MIRROR_SIDE)
        img = np.where(tint[face][..., None], lut[face], img).astype(np.uint8)
        if self._area_img is None or (self._area_img.width(), self._area_img.height()) != (cw, ch):
            self._area_pic = Photo(self, cw, ch)
            self._area_img = self._area_pic.photo
        self._area_pic.put(img)
        return self._area_img

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
        b = ttk.Combobox(bar, textvariable=self.mirror, values=names, width=max(map(len, names)) + 1,
                         state="readonly")
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
        self.swatches = tk.Canvas(self.area_bar, width=(COLOURS + 1) * (k + 2) + 2, height=k + 4,
                                  highlightthickness=0, background=ttk.Style().lookup("TFrame", "background")
                                  or "SystemButtonFace", cursor="hand2")
        self.swatches.pack(anchor="w", pady=(3, 0))
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
        self.bind("<F1>", lambda e: self.open_help())
        for keys, fn in (("z Z", self.undo), ("y Y", self.redo), ("c C", self.copy), ("v V", self.paste), ("h H", lambda: self.flip(True)),
                         ("j J", lambda: self.flip(False)), ("Left", lambda: self.turn(False)),
                         ("Right", lambda: self.turn(True))):
            for k in keys.split():
                self.bind(f"<Control-{k}>", self.hotkey(fn))

    def build_side(self):
        """The side panel: the layers list takes the room that's left; when that would make it shorter than its
        least rows (long warning texts, a small window), the panel scrolls instead (scrollbar, mouse wheel)."""
        box = self.side_box = ttk.Frame(self, width=int(300 * self.scale))
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
        """Like the main window's side panel (App.fit_side)."""
        c, side = self.side_canvas, self.side
        need, have = side.winfo_reqheight(), c.winfo_height()
        c.itemconfigure(self._side_win, width=c.winfo_width(), height=have if need < have else 0)
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(need, have)))
        if need > have + 1:
            if not self.side_bar.winfo_ismapped():  # the panel gets wider by the scrollbar, not narrower inside
                self.side_box.config(width=int(300 * self.scale) + self.side_bar.winfo_reqwidth())
                self.side_bar.pack(side="right", fill="y", before=c)
        elif self.side_bar.winfo_ismapped():
            self.side_bar.pack_forget()
            self.side_box.config(width=int(300 * self.scale))
            c.yview_moveto(0)

    def side_wheel(self, e):
        """The mouse wheel over the side panel scrolls it (the lists scroll themselves)."""
        if (str(e.widget).startswith(str(self.side_canvas)) and self.side_bar.winfo_ismapped()
                and not isinstance(e.widget, (tk.Listbox, ttk.Treeview, ttk.Combobox))):
            self.side_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def hotkey(self, fn):
        """A drawer shortcut that leaves text boxes alone (the name box, a layer being renamed)."""
        def handler(e):
            if isinstance(e.widget, (tk.Entry, ttk.Entry)):
                return None
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
        key = (json.dumps(self.strokes), skip, skip_pts)
        if self._stick_cache is None or self._stick_cache[0] != key:
            self._stick_cache = (key, Targets(self.strokes, skip, skip_pts))
        return self._stick_cache[1]

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

    def guide_at(self, corner, touches):
        """A Circle guide: the corner, and each touched stroke's shape [((u, v) touched, stroke)] moved to pass
        through it (a polyline: only the straight piece touched)."""
        out = []
        for (tu, tv), i in touches:
            pts = stroke_points(self.strokes[i])
            if self.strokes[i]["kind"] == "poly" and len(pts) > 2:
                pts = min((pts[j:j + 2] for j in range(len(pts) - 1)), key=lambda ab: seg_dist((tu, tv), *ab))
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
        if drag[0] == "stroke" and len(drag[2]) == 1:
            return [stroke_points(self.strokes[i]) for i in drag[2]]
        return []

    def draw_pieces(self, pieces, width):
        for pts in pieces:
            coords = self.screen_points(pts)
            if len(coords) >= 4:
                self.canvas.create_line(*coords, fill=STICK_LINE, width=width, capstyle="round", joinstyle="round")

    def stick_move(self, i, st, du, dv):
        """Stroke i (st = as it was when grabbed) moved by du, dv: (du, dv) changed so it sticks, or None (nothing
        in reach). Its points stick to points / crossings / lines, and its LINE too (user): a stroke's point onto
        it, or resting on a stroke's line (sticky.touch_line)."""
        targets, view, best = self.stick_targets(frozenset([i])), self.stick_view(), None
        k, ox, oy = view
        line = stroke_points(st)
        for _, (u, v) in key_points(st, line):
            got = targets.find(ox + (u + du) * k, oy - (v + dv) * k, view, REACH * self.scale)
            if got and got[0] == "line" and st["kind"] == "ellipse":
                continue  # (its left / right / top / bottom on a slanted line = crossing it: its line rests instead)
            rank = got and (STICK_RANK[got[0]], -got[2])  # (a point first, then a crossing, then a line; nearest)
            if got and (best is None or rank > best[2]):
                best = got, (got[1][0] - u, got[1][1] - v), rank
        moved = [(u + du, v + dv) for u, v in line]
        for kind, at, far, (su, sv) in targets.touch_line(moved, view, REACH * self.scale):
            rank = STICK_RANK[kind], -far
            if best is None or rank > best[2]:
                best = ("point" if kind == "on" else "line", at, far), (du + su, dv + sv), rank
        two = targets.rest_two(moved, view, REACH * self.scale / 2)  # (resting on two lines: a smaller reach, user)
        if two and (best is None or (STICK_RANK["two"], -two[1]) > best[2]):
            (su, sv), far, (t1, t2) = two
            self.stuck2 = "line", t2, far
            best = ("line", t1, far), (du + su, dv + sv), None
        if best is None:
            return None
        self.stuck = best[0]
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

    # ------------------------------------------------------------ mouse

    def on_press(self, e):
        self.canvas.focus_set()
        if self.follow:  # a stroke started with a click: this click finishes it
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.on_release(e, second=True)
            return
        tool = self.tool.get()
        if tool == "select":
            return self.select_press(e)
        if tool == "areas":
            return self.area_press(e)
        j = self.curve_handle_at(e.x, e.y) if self.draft is None else None
        if j is not None:  # the selected curve's anchors and handles work with any tool
            self.push_undo()
            self.drag = ("pen", self.sel, j)
            return
        if tool == "erase":  # a click = the stroke there; a drag = a box, every stroke it touches when let go
            self.drag = ("erasebox", e.x, e.y, e.x, e.y)
            return
        pt = self.event_pt(e) if tool != "free" else None
        if tool == "poly":  # click its points, or drag each segment
            if self.draft is None:
                self.draft = {"kind": "poly", "pts": [pt, list(pt)]}
            elif self.poly_point(pt, e):
                return
            self.drag = ("segment", e.x, e.y)
            self.redraw()
            return
        if tool == "free":
            self.draft = {"kind": "poly", "pts": [self.mirror_end(self.event_pt(e, snap=False), e.state)]}
            trail = mouse_trail(e.x_root, e.y_root, None)
            self.drag = ("free", e.x, e.y, trail[-1][2] if trail else None)
            return
        if tool == "arc":  # three clicks: start, a point it passes through, end (or drag start -> end, then bend)
            if self.draft is None:
                self.draft = {"kind": "arc", "pts": [pt, list(pt)], "k": 1.0}
                self.drag = ("arcdrag", e.x, e.y)
            elif self.arc_bend:  # dragged start -> end: this click sets the point it passes through
                self.draft["pts"][1] = pt
                self.arc_bend = False
                self.commit()
                return
            elif len(self.draft["pts"]) == 2:
                self.draft["pts"][-1] = pt
                self.draft["pts"].append(list(pt))
            elif pt != self.draft["pts"][0] or pt != self.draft["pts"][1]:  # the end on the start: a whole circle
                self.draft["pts"][-1] = pt
                self.commit()
            self.redraw()
            return
        self.drag = ("box", pt, e.x, e.y)

    def erase_release(self, drag):
        _, x0, y0, x1, y1 = drag
        if abs(x1 - x0) >= BOX_STILL or abs(y1 - y0) >= BOX_STILL:
            hit = self.pick_box(x0, y0, x1, y1)
        else:
            hit = [i for i in [self.hit_stroke(x0, y0)] if i is not None]
        if hit:
            self.push_undo()
            self.remove_strokes(hit)
            self.changed()
        else:
            self.redraw()  # (the box goes)

    # ------------------------------------------------------------ select tool

    def chosen(self):
        """The selected strokes' indexes, in order."""
        return sorted(self.picks | ({self.sel} if self.sel is not None else set()))

    def deselect(self):
        self.sel, self.picks, self.boxes = None, set(), []

    def remove_strokes(self, idx):
        """Deletes those strokes; the selection keeps pointing at the same strokes (its boxes go if a selected
        one is deleted)."""
        if set(idx) & set(self.chosen()):
            self.boxes = []
        self.keep_names()  # (the others' names don't count along again)
        keep = [i for i in range(len(self.strokes)) if i not in idx]
        new = {old: k for k, old in enumerate(keep)}
        self.strokes = [self.strokes[i] for i in keep]
        self.sel = new.get(self.sel)
        self.picks = {new[i] for i in self.picks if i in new}

    def screen_boxes(self):
        """The kept select boxes on screen: [(x0, y0, x1, y1)], x0 < x1, y0 < y1."""
        out = []
        for u0, v0, u1, v1 in self.boxes:
            (x0, y0), (x1, y1) = self.to_screen(u0, v1), self.to_screen(u1, v0)
            out.append((x0, y0, x1, y1))
        return out

    def in_boxes(self, x, y):
        """(x, y) on screen is inside a kept select box (Select tool only)."""
        return self.tool.get() == "select" and any(x0 <= x <= x1 and y0 <= y <= y1
                                                   for x0, y0, x1, y1 in self.screen_boxes())

    def pick_box(self, x0, y0, x1, y1):
        """The strokes a box on screen touches."""
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        found = []
        for i, st in enumerate(self.strokes):
            if not self.pickable(i):
                continue
            pts = np.array([self.to_screen(u, v) for u, v in stroke_points(st)], float).reshape(-1, 2)
            if len(pts) and line_touches_box(pts[:, 0], pts[:, 1], x0, y0, x1, y1):
                found.append(i)
        return found

    def handles(self):
        """Draggable points: [(stroke index, point index or ellipse corner, u, v)], the selected stroke first.
        Long strokes (freehand) show only some of their points until selected."""
        order = ([self.sel] if self.sel is not None else []) + [i for i in range(len(self.strokes) - 1, -1, -1)
                                                              if i != self.sel]
        out = []
        for i in order:
            st = self.strokes[i]
            if not self.pickable(i):
                continue
            if st["kind"] == "ellipse":
                u0, v0, u1, v1 = st["box"]
                out += [(i, k, u, v) for k, (u, v) in enumerate(((u0, v0), (u1, v0), (u1, v1), (u0, v1)))]
            elif st["kind"] == "curve":  # its anchors and handles only when selected (anchors on top)
                out += [(i, j, *st["pts"][j]) for j, _ in reversed(pen_handles(st["pts"], i == self.sel))]
            else:
                out += [(i, j, *st["pts"][j]) for j in shown_points(len(st["pts"]), i == self.sel)]
        return out

    def is_pen_point(self, i, j):
        """A curve's anchor or handle point that isn't one of its two ends."""
        st = self.strokes[i]
        return st["kind"] == "curve" and 0 < j < len(st["pts"]) - 1

    def curve_handle_at(self, x, y):
        """The point number of the selected curve's anchor / handle point at (x, y) (not its ends), or None."""
        if self.sel is None or self.strokes[self.sel]["kind"] != "curve" or not self.pickable(self.sel):
            return None
        pts = self.strokes[self.sel]["pts"]
        r = max(7, 8 * self.scale)
        for j, kind in reversed(pen_handles(pts)):
            hx, hy = self.to_screen(*pts[j])
            if kind != "end" and abs(hx - x) <= r and abs(hy - y) <= r:
                return j
        return None

    def select_press(self, e):
        r = max(7, 8 * self.scale)
        ctrl = e.state & CTRL
        for i, j, u, v in self.handles():
            x, y = self.to_screen(u, v)
            if abs(x - e.x) <= r and abs(y - e.y) <= r and not ctrl:
                self.select(i)
                self.push_undo()  # (after: undo leaves the stroke grabbed selected, like the piano roll)
                if self.strokes[i]["kind"] == "ellipse":
                    self.drag = ("corner", i, j, list(self.strokes[i]["box"]))
                elif self.is_pen_point(i, j):
                    self.drag = ("pen", i, j)
                else:
                    # points in the same spot move together (a closed outline's ends, lines that meet)
                    group = [(a, b) for a, st in enumerate(self.strokes) if st["kind"] != "ellipse" and self.pickable(a)
                             for b, p in enumerate(st["pts"])
                             if not self.is_pen_point(a, b) and math.dist(p, (u, v)) < 1e-6]
                    self.drag = ("points", group)
                self.redraw()
                return
        i = self.hit_stroke(e.x, e.y)
        if i is not None and ctrl:  # Ctrl+click: in or out of the selection
            if i in self.chosen():
                rest = [k for k in self.chosen() if k != i]
                self.sel, self.picks = (rest[-1], set(rest[:-1])) if rest else (None, set())
            else:
                self.picks = set(self.chosen())
                self.sel = i
        elif i is not None or (self.chosen() and self.in_boxes(e.x, e.y)):
            # the stroke moves, and the others selected with it (inside the select box: all of them)
            if i is not None:
                self.select(i)
            self.push_undo()  # (the last item: the stroke grabbed, None = the select box's empty space)
            self.drag = ("stroke", self.event_pt(e, snap=False),
                         {k: json.dumps(self.strokes[k]) for k in self.movable()}, json.dumps(self.boxes), i)
        else:  # a select box (Ctrl = adds another to what's selected); a click = deselect
            self.drag = ("boxsel", e.x, e.y, e.x, e.y, bool(ctrl))
        self.redraw()

    def select(self, i):
        """Clicked stroke i: it's selected, with its group (user; Ctrl+click = one stroke); the others selected stay
        so when it's one of them."""
        if i in self.chosen():
            self.picks = set(self.chosen()) - {i}
        else:
            self.picks, self.boxes = set(self.mates(i)) - {i}, []
        self.sel = i

    def select_drag(self, e):
        kind = self.drag[0]
        if kind == "boxsel":
            self.drag = self.drag[:3] + (e.x, e.y) + self.drag[5:]
            return self.redraw()
        old = self.areas and json.dumps(self.shown())  # (coloured areas keep their colours: carry_areas)
        if kind == "points":
            pt = self.event_pt(e)
            for a, b in self.drag[1]:
                if self.strokes[a]["kind"] == "curve":  # a curve's end takes its handle along (Alt: pulls one out)
                    drag_point(self.strokes[a], b, pt, e.state & ALT, self.to_xy, self.from_xy,
                               exact=True)
                else:
                    self.strokes[a]["pts"][b] = list(pt)
        elif kind == "pen":
            _, i, j = self.drag
            drag_point(self.strokes[i], j, self.event_pt(e), e.state & ALT, self.to_xy, self.from_xy, exact=True)
        elif kind == "corner":
            _, i, k, (u0, v0, u1, v1) = self.drag
            corners = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
            opp, pt = corners[(k + 2) % 4], self.event_pt(e)
            if e.state & CTRL:
                pt = self.perfect(opp, pt)
            self.strokes[i]["box"] = [min(opp[0], pt[0]), min(opp[1], pt[1]), max(opp[0], pt[0]), max(opp[1], pt[1])]
        elif kind == "stroke":
            _, start, origs, boxes, _ = self.drag
            cur = self.event_pt(e, snap=False)
            du, dv = cur[0] - start[0], cur[1] - start[1]
            self.stuck = self.stuck2 = None
            if not e.state & SHIFT:  # one stroke: its point nearest to a line sticks to it; else whole grid squares
                got = len(origs) == 1 and self.stick_move(*next((i, json.loads(o)) for i, o in origs.items()), du, dv)
                if got:
                    du, dv = got
                else:
                    n = int(self.grid_n.get())
                    du, dv = round(du * n) / n, round(dv * n) / n
            exact = (lambda c: c) if self.stuck else (lambda c: round(c, 5))  # (stuck: exactly on that line)
            for i, orig in origs.items():
                st = json.loads(orig)
                if st["kind"] == "ellipse":
                    u0, v0, u1, v1 = st["box"]
                    st["box"] = [exact(u0 + du), exact(v0 + dv), exact(u1 + du), exact(v1 + dv)]
                else:
                    st["pts"] = [[exact(u + du), exact(v + dv)] for u, v in st["pts"]]
                self.strokes[i] = st
            self.boxes = [[u0 + du, v0 + dv, u1 + du, v1 + dv] for u0, v0, u1, v1 in json.loads(boxes)]
        if old and old != json.dumps(self.shown()):
            frame = AREA_FRAME
            self.areas = carry_areas({"strokes": json.loads(old), "pts": frame, "areas": self.areas},
                                     {"strokes": self.shown(), "pts": frame, "areas": self.areas})
        self.redraw()

    def select_release(self):
        if self.drag[0] == "boxsel":
            _, x0, y0, x1, y1, adding = self.drag
            self.drag = None
            if abs(x1 - x0) < BOX_STILL and abs(y1 - y0) < BOX_STILL:  # a click
                if not adding:
                    self.deselect()
                return self.redraw()
            got = self.pick_box(x0, y0, x1, y1)
            (u0, v1), (u1, v0) = self.from_screen(min(x0, x1), min(y0, y1)), self.from_screen(max(x0, x1),
                                                                                                max(y0, y1))
            # it stays (until a click outside it), with the boxes before when Ctrl added it
            self.boxes = (self.boxes if adding else []) + ([[u0, v0, u1, v1]] if got else [])
            got = sorted(set(got) | set(self.chosen() if adding else []))
            self.sel = got[-1] if got else None  # (the top one: its handles show if it's a curve)
            self.picks = set(got[:-1])
            return self.redraw()
        if self.undo_stack and self.undo_stack[-1][0] == self.snap():
            self.undo_stack.pop()  # clicked without moving anything (the undone steps stay redoable)
            self.redo_stack = self.redo_kept
        else:
            self.changed(settle=False)  # (the drag took the colours along: carry_areas)

    def holding(self):
        """The mouse holds a stroke, points, a curve handle or an ellipse corner (a drag that changes strokes)."""
        return bool(self.drag) and self.drag[0] in ("stroke", "points", "pen", "corner")

    def let_go(self):
        """A held drag ends as if the mouse was let go there (Esc, a tool key, Delete): what it moved is one
        step (undo, "not saved yet")."""
        if self.holding():
            self.select_release()
        self.drag = self.stuck = self.stuck2 = None

    def delete_dragged(self):
        """Delete while the mouse holds something (like the piano roll): the drag ends where it is, then only the
        stroke grabbed goes (grabbed by the select box's empty space: all it holds); the rest stay selected."""
        kind = self.drag[0]
        if kind == "stroke":
            gone = list(self.drag[2]) if self.drag[4] is None else [self.drag[4]]
        else:
            gone = [self.drag[1] if kind in ("pen", "corner") else self.sel]
        self.let_go()
        self.push_undo()
        self.remove_strokes(gone)
        self.changed()

    def delete_selected_stroke(self):
        if self.holding():
            return self.delete_dragged()
        if self.movable() and self.tool.get() == "select":  # (locked / hidden ones stay: Del in the list)
            self.push_undo()
            self.remove_strokes(self.movable())
            self.changed()

    # ------------------------------------------------------------ copy / paste / flip / turn

    def targets(self):
        """What copy / flip / turn work on: the selected strokes, or the whole drawing when nothing is selected
        (not hidden or locked ones)."""
        return self.movable() if self.chosen() else [i for i in range(len(self.strokes)) if self.pickable(i)]

    def copy(self):
        if self.strokes:
            self.clipboard = json.dumps([self.strokes[i] for i in self.targets()])
            self.pastes = 0

    def paste(self):
        """Paste one grid square further down and right each time, so it doesn't sit exactly on the copy."""
        if not self.clipboard or self.draft:
            return
        self.pastes += 1
        d = self.pastes / int(self.grid_n.get())
        new = [pasted(self.map_stroke(st, lambda u, v: (u + d, v - d))) for st in json.loads(self.clipboard)]
        self.push_undo()
        self.strokes += new
        self.sel = len(self.strokes) - 1  # the pasted strokes are selected, so they can be moved together
        self.picks, self.boxes = set(range(len(self.strokes) - len(new), self.sel)), []
        self.changed()

    def map_stroke(self, st, fn, exact=False):
        """The stroke with every point moved by fn(u, v) -> (u, v) (exact: not rounded to 5 decimals)."""
        def pt(u, v):
            return list(fn(u, v)) if exact else [round(c, 5) for c in fn(u, v)]
        if st["kind"] == "ellipse":
            (a, b), (c, d) = pt(*st["box"][:2]), pt(*st["box"][2:])
            return dict(st, box=[min(a, c), min(b, d), max(a, c), max(b, d)])
        new = dict(st, pts=[pt(u, v) for u, v in st["pts"]])  # a curve keeps its corners and symmetry
        moved_formulas(new, fn)  # (and its formulas look the same)
        return new

    def middle(self, idx):
        """The middle of the strokes' points, in grid squares (a whole or half square if they're all on the grid,
        so flipping and turning keep them on it)."""
        n = int(self.grid_n.get())
        pts = [p for i in idx for p in (self.strokes[i]["pts"] if "pts" in self.strokes[i]
                                         else [self.strokes[i]["box"][:2], self.strokes[i]["box"][2:]])]
        us, vs = [u * n for u, _ in pts], [v * n for _, v in pts]
        cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
        on_grid = all(abs(c - round(c)) < 1e-6 for c in us + vs)
        return cu, cv, on_grid, n

    def flip(self, sideways):
        idx = self.targets()
        if not idx or self.draft:
            return
        cu, cv, _, n = self.middle(idx)
        self.transform(idx, (lambda u, v: (2 * cu / n - u, v)) if sideways else (lambda u, v: (u, 2 * cv / n - v)))

    def turn(self, clockwise):
        """Turn 90° around the middle."""
        idx = self.targets()
        if not idx or self.draft:
            return
        cu, cv, on_grid, n = self.middle(idx)
        if on_grid and (round(2 * cu) + round(2 * cv)) % 2:
            # one middle on a line, the other between lines: turning would land between grid points, so turn
            # around a point half a square off (chosen so turning back, or four times round, ends where it started)
            d = 0.5 if round(2 * cu) % 2 else -0.5
            if clockwise:
                cu += d
            else:
                cv += d
        cu, cv = cu / n, cv / n
        if clockwise:
            self.transform(idx, lambda u, v: (cu + (v - cv), cv - (u - cu)), turned=True)
        else:
            self.transform(idx, lambda u, v: (cu - (v - cv), cv + (u - cu)), turned=True)

    def transform(self, idx, fn, turned=False):
        self.push_undo()
        going = self.carried_areas(idx)
        for i in idx:
            self.strokes[i] = self.map_stroke(self.strokes[i], fn)
            if turned and self.strokes[i]["kind"] == "arc":  # still round (see arc.py)
                self.strokes[i]["k"] = 1 / self.strokes[i].get("k", 1.0)
        self.areas = [[*(round(x, 5) for x in fn(u, v)), c] if go else [u, v, c]
                      for (u, v, c), go in zip(self.areas, going)]
        boxes = []
        for u0, v0, u1, v1 in self.boxes:  # the select boxes go along
            us, vs = zip(*(fn(u, v) for u, v in ((u0, v0), (u1, v1))))
            boxes.append([min(us), min(vs), max(us), max(vs)])
        self.boxes = boxes
        self.changed(settle=False)

    def carried_areas(self, idx):
        """For each coloured area: whether it goes along when strokes idx are flipped / turned (all of them: the whole
        drawing). It does when those strokes close it in more tightly than the others do (a bar turned inside a box:
        the bar's colour, not the box's)."""
        shown = self.shown_idx()  # (as the shape is: hidden strokes left out)
        return carried_spots(self.shown(), [shown.index(i) for i in idx if i in shown], self.areas, AREA_FRAME)

    def on_drag(self, e):
        self.show_position(e)
        if not self.drag:
            return
        if self.drag[0] == "areas":
            return self.area_drag(e)
        if self.drag[0] == "erasebox":
            self.drag = self.drag[:3] + (e.x, e.y)
            return self.redraw()
        if self.tool.get() == "select" or self.drag[0] in ("points", "pen"):
            return self.select_drag(e)
        if self.drag[0] == "free":  # also where Windows skipped the mouse while busy (see mouse_trail)
            _, lx, ly, since = self.drag
            trail = mouse_trail(e.x_root, e.y_root, since)
            ox, oy = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
            spots = [(x - ox, y - oy) for x, y, _ in trail] or [(e.x, e.y)]
            n = len(self.draft["pts"])
            for x, y in spots:
                if math.hypot(x - lx, y - ly) >= 3:
                    self.draft["pts"].append(self.event_pt(SimpleNamespace(x=x, y=y, state=e.state), snap=False))
                    lx, ly = x, y
            self.drag = ("free", lx, ly, trail[-1][2] if trail else since)
            if len(self.draft["pts"]) > n:
                self.redraw()
            return
        if self.drag[0] in ("segment", "arcdrag"):  # a polyline's next point / an arc's end, at the mouse
            self.draft["pts"][-1] = self.event_pt(e)
            self.redraw()
            return
        tool = self.tool.get()
        self.guide, self.stuck2 = [], None
        start, pt = self.drag[1], self.event_pt(e, stick=tool != "circle")
        if tool in ("square", "circle") and e.state & CTRL:
            pt = self.perfect(start, pt)
        self.square_sides = [1, 2]
        if tool == "square" and not e.state & (CTRL | SHIFT):  # its sides rest on lines too, like a moved stroke
            # (user): on one line, or on two at once (a smaller reach); the mouse's corner sticking to a point or a
            # crossing still comes first. Resting by moving one way only, the other way stays on the grid.
            raw = self.from_xy(e.x, e.y)
            best = self.stuck and (STICK_RANK[self.stuck[0]], -self.stuck[2])
            n = int(self.grid_n.get())
            targets, view = self.stick_targets(), self.stick_view()
            for kind, (su, sv), far, touch, sides in targets.rest_box(start, raw, view, REACH * self.scale):
                at = raw
                if kind == "rest" and 0 in (su, sv):  # (again from the grid that other way)
                    at = [raw[0], round(raw[1] * n) / n] if sv == 0 else [round(raw[0] * n) / n, raw[1]]
                    again = [g for g in targets.rest_box(start, at, view, REACH * self.scale) if g[0] == "rest"]
                    if not again or (again[0][1][1] == 0) != (sv == 0):
                        continue
                    _, (su, sv), far, touch, sides = again[0]
                if best is None or (STICK_RANK[kind], -far) > best:
                    best = STICK_RANK[kind], -far
                    pt = [at[0] + su, at[1] + sv]
                    self.stuck, self.stuck2 = ("line", touch[0], far), touch[1:] and ("line", touch[1], far) or None
                    self.square_sides = sides
        if tool == "circle" and not e.state & SHIFT:  # its line sticks, not the dragged corner (user; the pressed
            # corner sticks like any point): the circle to the mouse, grown / shrunk from that corner until it touches
            raw = self.from_xy(e.x, e.y)
            if e.state & CTRL:
                raw = self.perfect(start, raw)
            d = (raw[0] - start[0], raw[1] - start[1])
            targets, view = self.stick_targets(), self.stick_view()
            got = targets.touch_circle(start, d, view, REACH * self.scale)
            # resting on two lines at once (user; not with Ctrl: a perfect circle from that corner rarely can)
            two = None if e.state & CTRL else targets.touch_two(start, d, view, GUIDE_REACH * self.scale)
            if two and two[1] <= REACH * self.scale:
                (t1, _), (t2, _) = two[2]
                self.stuck, self.stuck2 = ("line", t1, two[1]), ("line", t2, two[1])
                pt = list(two[0])
                self.guide = [self.guide_at(two[0], two[2])]
            else:
                if got:
                    self.stuck, s = got[:3], got[3]
                    pt = [start[0] + s * d[0], start[1] + s * d[1]]
                # where to point the mouse for it to touch: a dotted purple point on the touched stroke's shape
                # moved to pass through it (faint, dotted); shown while it's stuck too (user)
                near = got or targets.touch_circle(start, d, view, GUIDE_REACH * self.scale)
                if near:
                    self.guide.append(self.guide_at((start[0] + near[3] * d[0], start[1] + near[3] * d[1]),
                                                    [(near[1], near[4])]))
                if two:
                    self.guide.append(self.guide_at(two[0], two[2]))
        (u0, v0), (u1, v1) = start, pt
        if tool == "line":
            self.draft = {"kind": "poly", "pts": [start, pt]}
        elif tool == "curve":  # a gentle S-curve to start with, like on the piano roll
            mid = round((u0 + u1) / 2, 5)
            self.draft = {"kind": "curve", "pts": [start, [mid, v0], [mid, v1], pt]}
        elif tool == "square":
            self.draft = {"kind": "poly", "pts": [[u0, v0], [u1, v0], [u1, v1], [u0, v1], [u0, v0]]}
        else:
            self.draft = {"kind": "ellipse", "box": [min(u0, u1), min(v0, v1), max(u0, u1), max(v0, v1)]}
        self.redraw()

    def on_release(self, e, second=False):
        """second: the click that finishes a stroke started with a click (see follow)."""
        self.stuck = self.stuck2 = None  # (the mark goes; hovering shows it again)
        self.guide = []
        if self.drag and (self.tool.get() == "select" or self.drag[0] in ("points", "pen")):
            self.select_release()
        drag, self.drag = self.drag, None
        if drag and drag[0] == "erasebox":
            return self.erase_release(drag)
        if not drag or drag[0] == "areas":
            return
        still = drag[0] in ("box", "segment", "arcdrag") and abs(e.x - drag[-2]) < 4 and abs(e.y - drag[-1]) < 4
        if still and drag[0] == "box":
            if second:  # clicked twice in the same spot: nothing
                return self.cancel_draft()
            self.follow = drag  # a click, not a drag: the stroke follows the mouse until the next click
            return
        if drag[0] == "segment" and not still:  # dragged: the point goes where it was let go
            self.poly_point(self.event_pt(e), e)
            return
        if drag[0] == "arcdrag" and not still:  # dragged start -> end: now it bends with the mouse until a click
            (u0, v0), end = self.draft["pts"]
            self.draft["pts"] = [[u0, v0], [round((u0 + end[0]) / 2, 5), round((v0 + end[1]) / 2, 5)], end]
            self.arc_bend = True
            self.redraw()
            return
        if not self.draft or drag[0] in ("segment", "arcdrag"):
            return
        st = self.draft
        if drag[0] == "free":
            pts = st["pts"]
            if len(pts) < 2:
                return self.cancel_draft()
            fx, fy = self.to_screen(*pts[0])
            if len(pts) >= 3 and math.hypot(e.x - fx, e.y - fy) < 12 * self.scale:
                pts.append(list(pts[0]))  # let go near the start: closed
            else:
                pts[-1] = self.mirror_end(pts[-1], e.state)
            return self.commit(pts)
        if st["kind"] == "ellipse":
            u0, v0, u1, v1 = st["box"]
            ok = u1 > u0 and v1 > v0
        else:
            us, vs = [p[0] for p in st["pts"]], [p[1] for p in st["pts"]]
            wide, tall = max(us) > min(us), max(vs) > min(vs)
            ok = (wide or tall) if st["kind"] == "curve" or self.tool.get() == "line" else (wide and tall)
        if ok:
            self.commit()
        else:
            self.cancel_draft()

    def on_double(self, e):
        if self.draft and self.tool.get() == "poly":
            self.finish_poly()
        else:
            self.on_press(e)

    def on_motion(self, e):
        self.show_position(e)
        cursor = "fleur" if self.draft is None and (self.curve_handle_at(e.x, e.y) is not None or (
            self.chosen() and self.in_boxes(e.x, e.y))) else "crosshair"
        if str(self.canvas.cget("cursor")) != cursor:
            self.canvas.config(cursor=cursor)
        if self.follow:  # a stroke started with a click follows the mouse
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.follow, self.drag = self.drag, None
        elif self.draft and self.tool.get() in ("poly", "arc"):
            self.draft["pts"][1 if self.arc_bend else -1] = self.event_pt(e)
            self.redraw()
        elif self.tool.get() in DRAW_TOOLS and self.draft is None:  # where a press would stick: the mark
            was = self.stuck
            self.event_pt(e)
            if self.stuck != was:
                self.redraw()
        elif self.stuck:
            self.stuck = None
            self.redraw()
        if self.tool.get() == "areas":
            lab = self.area_at(e)
            if lab != self.hover:
                self.hover = lab
                self.redraw()
            if lab is not None:
                c = self.area_paint(self.area_info()[0])[lab]
                self.pos_label.config(text=tr("drawer.area_normal") if c < 0 else tr("drawer.area_empty") if c == 0
                                      else tr("drawer.area_n", n=int(c)))

    def on_leave(self, e):
        """The mouse left the board: no sticking mark, no area darkened under it (user: it stayed dark)."""
        stuck, hover = self.stuck and not self.drag, self.hover is not None
        if stuck:
            self.stuck = None
        if hover:
            self.hover = None
            self.pos_label.config(text="")
        if stuck or hover:
            self.redraw()

    def on_key(self, e):
        k = e.keysym.lower()
        if isinstance(e.widget, (tk.Entry, ttk.Entry)):
            return
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

    def right_click(self, e):
        """Finishes a polyline being drawn. Otherwise, like on the piano roll: on the selected curve's anchor =
        remove it, on a handle dot = pull it back in; near a stroke = its menu; empty space = deselect."""
        self.canvas.focus_set()  # (keys go to the drawing now, not the shape list)
        if self.holding():  # (the left button holds a stroke: nothing, its menu would work on it mid-move)
            return
        if self.erasing():  # an eraser box being dragged: dropped, nothing erased (like Esc)
            return self.cancel_draft()
        if self.draft or self.follow:
            if self.draft and self.draft["kind"] == "poly" and self.tool.get() == "poly":
                return self.finish_poly()
            return self.cancel_draft()
        if self.tool.get() == "areas":  # an area back to how Fill / Spam fill it as normal
            lab = self.area_at(e)
            if lab is not None:
                self.set_area(lab, None, None)
            return
        j = self.curve_handle_at(e.x, e.y)
        if j is not None:
            st = self.strokes[self.sel]
            what = can_delete(st, j)
            if what == "middle":
                self.pos_label.config(text=tr("drawer.the_middle_anchor_of_a_symmetric"))
                return
            if what:
                self.push_undo()
                delete_point(st, j, self.to_xy, exact=True)
                return self.changed()
        chosen = self.chosen()
        if chosen and self.in_boxes(e.x, e.y):  # inside the select box: what works on all of them
            return self.show_menu(e, chosen[0]) if len(chosen) == 1 else self.show_group_menu(e)
        self.boxes = []  # (a click outside the box drops it)
        i = self.hit_stroke(e.x, e.y)
        if i is None:
            if chosen:
                self.deselect()
            self.redraw()
            return
        if i in chosen and len(chosen) > 1:  # one of several selected (Ctrl+click)
            self.redraw()
            return self.show_group_menu(e)
        self.select(i)
        self.redraw()
        if len(self.chosen()) > 1:  # (a stroke in a group: the group's menu)
            return self.show_group_menu(e)
        self.show_menu(e, i)

    def show_group_menu(self, e):
        """The menu for several selected strokes: what works on all of them at once (roles, colour, delete: only
        the ones the board can change, like Del; the layer items: all of them)."""
        picked, idx = self.chosen(), self.movable()
        m = tk.Menu(self, tearoff=0)
        roles = {role_of(self.strokes[i]) or "both" for i in idx}
        self._role_var = tk.StringVar(value=roles.pop() if len(roles) == 1 else "")  # (kept, so the dot shows)
        for role in ("both",) + ROLES:
            m.add_radiobutton(label=tr("drawer.role_" + role), value=role, variable=self._role_var,
                              command=lambda r=role: self.set_role(idx, r))
        colours = {self.strokes[i].get("colour", 0) for i in idx if role_of(self.strokes[i]) != "cut"}
        self._colour_var = tk.IntVar(value=min(colours) if len(colours) == 1 else -1)
        m.add_cascade(label=tr("drawer.outline_colour"), menu=colour_menu(
            m, self._colour_var, lambda c: self.set_stroke_colour(idx, c)),
            state="normal" if colours else "disabled")
        m.add_separator()
        m.add_command(label=tr("drawer.delete_strokes", n=len(idx)), accelerator=tr("drawer.del"),
                      command=lambda: self.delete_stroke(idx[0]), state="normal" if idx else "disabled")
        m.add_command(label=tr("drawer.copy_strokes", n=len(idx)), accelerator=tr("drawer.ctrl_c"), command=self.copy,
                      state="normal" if idx else "disabled")
        m.add_command(label=tr("drawer.paste"), accelerator=tr("drawer.ctrl_v"), command=self.paste,
                      state="normal" if self.clipboard else "disabled")
        m.add_separator()
        self.layer_menu_items(m, picked)
        self.add_flip_turn(m)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def add_flip_turn(self, m):
        m.add_separator()
        m.add_command(label=tr("drawer.flip_sideways"), accelerator=tr("drawer.ctrl_h"),
                      command=lambda: self.flip(True))
        m.add_command(label=tr("drawer.flip_upside_down"), accelerator=tr("drawer.ctrl_j"),
                      command=lambda: self.flip(False))
        m.add_command(label=tr("drawer.turn_90_left"), accelerator=tr("drawer.ctrl_left"),
                      command=lambda: self.turn(False))
        m.add_command(label=tr("drawer.turn_90_right"), accelerator=tr("drawer.ctrl_right"),
                      command=lambda: self.turn(True))

    def show_menu(self, e, i):
        st = self.strokes[i]
        m = tk.Menu(self, tearoff=0)
        if st["kind"] == "curve":
            m.add_command(label=tr("drawer.add_anchor_here"), command=lambda: self.add_curve_anchor(e))
            # one half follows the other; the half right-clicked keeps its shape
            symmetry_menu(m, st.get("sym"), lambda mode: self.set_curve_symmetry(i, mode, e))
        elif st["kind"] == "poly":
            m.add_command(label=tr("drawer.add_point_here"), command=lambda: self.add_poly_point(i, e))
        if takes_formula(st):
            self._formula_picks = {}  # (kept, so the dots show)
            formula_menu(m, DrawerHost(self), self._formula_picks)
        self._role_var = tk.StringVar(value=role_of(st) or "both")  # (kept, so the dot shows)
        for role in ("both",) + ROLES:
            m.add_radiobutton(label=tr("drawer.role_" + role), value=role, variable=self._role_var,
                              command=lambda r=role: self.set_role([i], r))
        self._colour_var = tk.IntVar(value=st.get("colour", 0))
        m.add_cascade(label=tr("drawer.outline_colour"), menu=colour_menu(
            m, self._colour_var, lambda c: self.set_stroke_colour([i], c)),
            state="disabled" if role_of(st) == "cut" else "normal")
        m.add_separator()
        m.add_command(label=tr("drawer.delete_stroke"), accelerator=tr("drawer.del"),
                      command=lambda: self.delete_stroke(i))
        m.add_command(label=tr("drawer.copy_stroke"), accelerator=tr("drawer.ctrl_c"), command=self.copy)
        m.add_command(label=tr("drawer.paste"), accelerator=tr("drawer.ctrl_v"), command=self.paste,
                      state="normal" if self.clipboard else "disabled")
        m.add_separator()
        self.layer_menu_items(m, [i])
        self.add_flip_turn(m)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def add_curve_anchor(self, e, near=None):
        """A new anchor on the selected curve where it's nearest to the mouse, moved to the mouse (snapped unless
        Shift). near: only if the curve is that close (pixels)."""
        if self.sel is None or self.strokes[self.sel]["kind"] != "curve" or not self.pickable(self.sel):
            return  # (a locked / hidden curve picked in the layers list stays as it is)
        st = self.strokes[self.sel]
        seg, t, d = nearest(st["pts"], self.to_xy, e.x, e.y)
        if near is not None and d > near:
            return
        before = self.snap()
        if add_anchor(st, seg, t, self.event_pt(e), self.to_xy, exact=True):
            self.push_undo(before)
            self.changed()

    def add_poly_point(self, i, e):
        """A point (snapped unless Shift) put into the line's nearest part."""
        pts = self.strokes[i]["pts"]
        pt = self.event_pt(e)
        if pt in pts or len(pts) < 2:
            return
        screen = [self.to_screen(u, v) for u, v in pts]

        def dist(k):  # from the mouse to the part between points k and k + 1
            (ax, ay), (bx, by) = screen[k], screen[k + 1]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0 if ll == 0 else max(0, min(1, ((e.x - ax) * dx + (e.y - ay) * dy) / ll))
            return math.hypot(e.x - ax - t * dx, e.y - ay - t * dy)
        k = min(range(len(pts) - 1), key=dist)
        self.push_undo()
        pts.insert(k + 1, pt)
        self.changed()

    def set_curve_symmetry(self, i, mode, e):
        st = self.strokes[i]
        if (st.get("sym") or None) == mode:
            return
        self.push_undo()
        set_symmetry(st, mode, half_at(st["pts"], self.to_xy, e.x, e.y), self.to_xy, exact=True)
        self.changed()

    def set_role(self, idx, role):
        """The strokes idx: outline and fill ("both") / outline only ("edge") / fill line ("cut")."""
        sts = [self.strokes[i] for i in idx if (role_of(self.strokes[i]) or "both") != role]
        if not sts:
            return
        self.push_undo()
        for st in sts:
            st.pop("role", None)
            if role != "both":
                st["role"] = role
        self.changed()

    def set_stroke_colour(self, idx, colour):
        """The strokes idx's outline notes in colour (1 .. COLOURS, like the areas') or the shape's own (0). Not
        fill lines (they make no notes)."""
        sts = [self.strokes[i] for i in idx if role_of(self.strokes[i]) != "cut"
               and self.strokes[i].get("colour", 0) != colour]
        if not sts:
            return
        self.push_undo()
        for st in sts:
            st.pop("colour", None)
            if colour:
                st["colour"] = colour
        self.changed()

    def delete_stroke(self, i):
        """Menu > Delete on stroke i: it, or the picked ones it's among (locked / hidden ones stay, like Del)."""
        gone = [k for k in (self.chosen() if i in self.chosen() else [i]) if self.pickable(k)]
        if not gone:
            return
        self.push_undo()
        self.remove_strokes(gone)
        self.changed()

    def poly_point(self, pt, e):
        """The polyline being drawn gets its next point at pt (a new one then follows the mouse). At its first
        point again (on screen, e = the mouse) it's closed and done: True."""
        pts = self.draft["pts"]
        fx, fy = self.to_screen(*pts[0])
        if len(pts) >= 3 and math.hypot(e.x - fx, e.y - fy) < 8 * self.scale:
            pts[-1] = list(pts[0])
            self.commit([p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]])
            return True
        pts[-1] = pt
        pts.append(list(pt))
        self.redraw()
        return False

    def finish_poly(self):
        if not (self.draft and self.draft["kind"] == "poly" and self.tool.get() == "poly"):
            return
        pts = []
        for p in self.draft["pts"][:-1]:  # the last point is the one following the mouse
            if not pts or p != pts[-1]:
                pts.append(p)
        if len(pts) >= 2:
            self.commit(pts)
        else:
            self.cancel_draft()

    def hit_stroke(self, x, y):
        for i in range(len(self.strokes) - 1, -1, -1):
            if not self.pickable(i):
                continue
            xy = self.screen_points(stroke_points(self.strokes[i]))
            pts = list(zip(xy[::2], xy[1::2]))
            if len(pts) == 1 and math.hypot(pts[0][0] - x, pts[0][1] - y) < 8:
                return i
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                dx, dy = bx - ax, by - ay
                ll = dx * dx + dy * dy
                t = 0 if ll == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll))
                if math.hypot(x - ax - t * dx, y - ay - t * dy) < 8:
                    return i
        return None

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

    # ------------------------------------------------------------ mirror

    def mirror_mode(self):
        """"off", "h" (left <-> right), "v" (top <-> bottom) or "both"."""
        names = [tr("drawer.mirror_" + m) for m in MIRRORS]
        return MIRRORS[names.index(self.mirror.get())] if self.mirror.get() in names else "off"

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
        if self.draft:
            return self.cancel_draft()
        self.drop_drag()
        self.restore(self.undo_stack, self.redo_stack)

    def redo(self):
        if self.erasing():
            return
        if self.draft:
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
