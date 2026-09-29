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
from notes.custom import clean_strokes, join_strokes, open_ends, open_paths, stroke_points, strokes_closed
from notes.pattern import has_formula, moved_formulas
from files.about import HERE
from files.safefile import write_text
from roll.roll_shared import mouse_trail
from window.help import open_help
from window.formula_host import DrawerHost, formula_menu
from window.help_texts import BY_ID, DRAWER_TOOL_TOPICS
from window.widgets import Tooltip, symmetry_menu

LIBRARY = os.path.join(HERE, "shapes")
# Always in the library (not files). A saved shape with the same name is used instead; deleting it brings these back.
BUILT_IN = {
    "Circle": [{"kind": "ellipse", "box": [0, 0, 1, 1]}],
    "Square": [{"kind": "poly", "pts": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]}],
    "Triangle": [{"kind": "poly", "pts": [[0, 0], [1, 0], [0.5, 1], [0, 0]]}],
}
GRIDS = ["4", "8", "12", "16", "24", "32", "48", "64"]
TOOLS = [("select", tr("drawer.select"), "v"), ("line", tr("drawer.line"), "l"), ("poly", tr("drawer.polyline"), "p"),
         ("free", tr("drawer.freehand"), "f"), ("curve", tr("drawer.curve"), "c"), ("arc", tr("drawer.arc"), "a"),
         ("square", tr("drawer.square"), "s"), ("circle", tr("drawer.circle"), "o"),
         ("erase", tr("drawer.eraser"), "e")]
SHIFT, CTRL, ALT = 0x1, 0x4, 0x20000
BAD_CHARS = '<>:"/\\|?*'


# ---------------------------------------------------------------- library

def saved_names():
    try:
        return [f[:-5] for f in os.listdir(LIBRARY) if f.lower().endswith(".json")]
    except OSError:
        return []


def built_in_name(name):
    """The built-in shape's name if `name` is one (any upper/lower case), else None."""
    return next((b for b in BUILT_IN if b.lower() == name.lower()), None)


def library_names():
    names = saved_names()
    taken = {n.lower() for n in names}
    names += [b for b in BUILT_IN if b.lower() not in taken]
    return sorted(names, key=str.lower)


def shape_file(name):
    return os.path.join(LIBRARY, name + ".json")


def load_shape(name):
    """The strokes of a library shape, or None if it's missing or broken."""
    try:
        with open(shape_file(name), encoding="utf-8") as f:
            strokes = clean_strokes(json.load(f).get("strokes"))
    except FileNotFoundError:
        b = built_in_name(name)
        return clean_strokes(BUILT_IN[b]) if b else None
    except (OSError, ValueError, AttributeError):
        return None
    return strokes or None


def save_shape(name, strokes):
    os.makedirs(LIBRARY, exist_ok=True)
    write_text(shape_file(name), '{"strokes": [\n  ' + ",\n  ".join(json.dumps(st) for st in strokes) + "\n]}\n")


def clean_name(name):
    return "".join(c for c in name if c not in BAD_CHARS).strip().rstrip(".")


def help_box(parent, text):
    """Grey help text that fills the rest of the panel; a scrollbar shows up when it doesn't fit.
    frame.set_text(text) changes it."""
    frame = ttk.Frame(parent)
    t = tk.Text(frame, wrap="word", font=("Segoe UI", 8), foreground="#666", relief="flat", borderwidth=0,
                highlightthickness=0, height=1, padx=0, pady=0, cursor="arrow", takefocus=0,
                background=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")
    sb = ttk.Scrollbar(frame, orient="vertical", command=t.yview)

    def scrolled(first, last):
        sb.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            sb.pack_forget()
        elif not sb.winfo_ismapped():
            sb.pack(side="right", fill="y", before=t)

    def set_text(new):
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", new)
        t.config(state="disabled")

    t.config(yscrollcommand=scrolled)
    set_text(text)
    t.pack(side="left", fill="both", expand=True)
    frame.set_text = set_text
    return frame


# ---------------------------------------------------------------- window

class Drawer(tk.Toplevel):
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
        self.sel = None        # index of the selected stroke (Select tool)
        self.zoom = 1.0        # 1 = the whole board fits the window
        self.center = [0.5, 0.5]  # the board point in the middle of the window (0.5, 0.5 = the board's middle)
        self._pan = None
        self.tool = tk.StringVar(value="poly")
        self.grid_n = tk.StringVar(value="16")
        self.name = tk.StringVar()
        self._build()
        self.refresh_list()
        self.tool.trace_add("write", lambda *_: (setattr(self, "sel", None), self.cancel_draft(), self.on_tool()))
        self.grid_n.trace_add("write", lambda *_: self.redraw())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.update_side_help()
        self.after(500, lambda: app.tips.show("drawer", parent=self))  # the first time it opens

    def tool_topic(self):
        return DRAWER_TOOL_TOPICS.get(self.tool.get(), "drawer")

    def update_side_help(self):
        """The side panel's help: the current tool's, then the drawer's (everything: Help, F1)."""
        t, d = BY_ID[self.tool_topic()], BY_ID["drawer"]
        self.side_help.set_text(tr("drawer.help_f1_every_tip_searchable", title=t['title'], text=t['text'],
                                   text2=d['text']))

    def on_tool(self):
        self.update_side_help()
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
        ttk.Combobox(bar, textvariable=self.grid_n, values=GRIDS, width=4, state="readonly").pack(side="left")
        ttk.Button(bar, text=tr("drawer.clear"), command=self.clear).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("drawer.reset_view"), command=self.reset_view).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text=tr("drawer.help_f1"), command=self.open_help).pack(side="left", padx=(12, 0))

        side = ttk.Frame(self, padding=(6, 0, 6, 6), width=int(300 * self.scale))
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        box = ttk.LabelFrame(side, text=tr("drawer.shape_library"), padding=6)
        box.pack(fill="x")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.listbox = tk.Listbox(row, height=12, activestyle="none", exportselection=False, font=("Segoe UI", 9))
        sb = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda e: self.open_selected())
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text=tr("drawer.open"), command=self.open_selected).pack(side="left")
        ttk.Button(btns, text=tr("drawer.delete"), command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(btns, text=tr("drawer.new"), command=self.new).pack(side="left")
        name = ttk.Frame(box)
        name.pack(fill="x", pady=(8, 0))
        ttk.Label(name, text=tr("drawer.name")).pack(side="left")
        ttk.Entry(name, textvariable=self.name).pack(side="left", fill="x", expand=True, padx=(5, 0))
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text=tr("drawer.save"), command=self.save).pack(side="left")
        ttk.Button(btns, text=tr("drawer.use_on_the_piano_roll"), command=self.use).pack(side="right")
        self.pos_label = ttk.Label(side, text="", foreground="#555", font=("Segoe UI", 9))
        self.pos_label.pack(fill="x", pady=(8, 0))
        self.state_label = ttk.Label(side, text="", wraplength=int(285 * self.scale), justify="left")
        self.state_label.pack(fill="x", pady=(8, 0))
        self.side_help = help_box(side, "")  # the current tool's help (update_side_help)
        self.side_help.pack(fill="both", expand=True, pady=(8, 0))

        self.canvas = tk.Canvas(self, bg="#f4f4f4", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        c = self.canvas
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Motion>", self.on_motion)
        c.bind("<ButtonPress-3>", self.right_click)
        c.bind("<ButtonPress-2>", self.start_pan)
        c.bind("<B2-Motion>", self.pan_to)
        c.bind("<ButtonRelease-2>", self.on_middle_release)
        c.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Key>", self.on_key)
        self.bind("<F1>", lambda e: self.open_help())
        for k in ("z", "Z"):
            self.bind(f"<Control-{k}>", lambda e: (self.undo(), "break")[1])
        for k in ("y", "Y"):
            self.bind(f"<Control-{k}>", lambda e: (self.redo(), "break")[1])
        for keys, fn in (("c C", self.copy), ("v V", self.paste), ("h H", lambda: self.flip(True)),
                         ("j J", lambda: self.flip(False)), ("Left", lambda: self.turn(False)),
                         ("Right", lambda: self.turn(True))):
            for k in keys.split():
                self.bind(f"<Control-{k}>", self.hotkey(fn))

    def hotkey(self, fn):
        """A drawer shortcut that leaves the name box alone."""
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

    def from_screen(self, x, y):
        k = self.px()
        return (self.center[0] + (x - self.canvas.winfo_width() / 2) / k,
                self.center[1] - (y - self.canvas.winfo_height() / 2) / k)

    def to_xy(self, p):
        return self.to_screen(*p)

    def from_xy(self, x, y):
        return list(self.from_screen(x, y))

    def event_pt(self, e, snap=True):
        u, v = self.from_screen(e.x, e.y)
        if snap and not e.state & SHIFT:
            n = int(self.grid_n.get())
            u, v = round(u * n) / n, round(v * n) / n
        return [round(u, 5), round(v, 5)]

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

    def pan_to(self, e):
        if not self._pan:
            return
        x, y, (cu, cv) = self._pan
        k = self.px()
        self.center = [cu - (e.x - x) / k, cv + (e.y - y) / k]
        self.redraw()

    def on_middle_release(self, e):
        """A middle click (without dragging) near the selected curve: a new anchor there (any tool)."""
        if self._pan and abs(e.x - self._pan[0]) <= 3 and abs(e.y - self._pan[1]) <= 3 and not self.draft:
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
        pt = self.event_pt(e)
        if tool == "select":
            return self.select_press(e)
        j = self.curve_handle_at(e.x, e.y) if self.draft is None else None
        if j is not None:  # the selected curve's anchors and handles work with any tool
            self.push_undo()
            self.drag = ("pen", self.sel, j)
            return
        if tool == "erase":
            i = self.hit_stroke(e.x, e.y)
            if i is not None:
                self.push_undo()
                del self.strokes[i]
                self.changed()
            return
        if tool == "poly":  # click its points, or drag each segment
            if self.draft is None:
                self.draft = {"kind": "poly", "pts": [pt, list(pt)]}
            elif self.poly_point(pt, e):
                return
            self.drag = ("segment", e.x, e.y)
            self.redraw()
            return
        if tool == "free":
            self.draft = {"kind": "poly", "pts": [self.event_pt(e, snap=False)]}
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

    # ------------------------------------------------------------ select tool

    def handles(self):
        """Draggable points: [(stroke index, point index or ellipse corner, u, v)], the selected stroke first.
        Long strokes (freehand) only show their points when selected."""
        order = ([self.sel] if self.sel is not None else []) + [i for i in range(len(self.strokes) - 1, -1, -1)
                                                              if i != self.sel]
        out = []
        for i in order:
            st = self.strokes[i]
            if st["kind"] == "ellipse":
                u0, v0, u1, v1 = st["box"]
                out += [(i, k, u, v) for k, (u, v) in enumerate(((u0, v0), (u1, v0), (u1, v1), (u0, v1)))]
            elif st["kind"] == "curve":  # its anchors and handles only when selected (anchors on top)
                out += [(i, j, *st["pts"][j]) for j, _ in reversed(pen_handles(st["pts"], i == self.sel))]
            elif len(st["pts"]) <= 64 or i == self.sel:
                out += [(i, j, u, v) for j, (u, v) in enumerate(st["pts"])]
        return out

    def is_pen_point(self, i, j):
        """A curve's anchor or handle point that isn't one of its two ends."""
        st = self.strokes[i]
        return st["kind"] == "curve" and 0 < j < len(st["pts"]) - 1

    def curve_handle_at(self, x, y):
        """The point number of the selected curve's anchor / handle point at (x, y) (not its ends), or None."""
        if self.sel is None or self.strokes[self.sel]["kind"] != "curve":
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
        for i, j, u, v in self.handles():
            x, y = self.to_screen(u, v)
            if abs(x - e.x) <= r and abs(y - e.y) <= r:
                self.push_undo()
                self.sel = i
                if self.strokes[i]["kind"] == "ellipse":
                    self.drag = ("corner", i, j, list(self.strokes[i]["box"]))
                elif self.is_pen_point(i, j):
                    self.drag = ("pen", i, j)
                else:
                    # points in the same spot move together (a closed outline's ends, lines that meet)
                    group = [(a, b) for a, st in enumerate(self.strokes) if st["kind"] != "ellipse"
                             for b, p in enumerate(st["pts"])
                             if not self.is_pen_point(a, b) and math.dist(p, (u, v)) < 1e-6]
                    self.drag = ("points", group)
                self.redraw()
                return
        i = self.hit_stroke(e.x, e.y)
        if i is not None:
            self.push_undo()
            self.sel = i
            self.drag = ("stroke", i, self.event_pt(e, snap=False), json.dumps(self.strokes[i]))
        else:
            self.sel = None
            self.start_pan(e)
            self.drag = ("pan",)
        self.redraw()

    def select_drag(self, e):
        kind = self.drag[0]
        if kind == "pan":
            return self.pan_to(e)
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
            _, i, start, orig = self.drag
            cur = self.event_pt(e, snap=False)
            du, dv = cur[0] - start[0], cur[1] - start[1]
            if not e.state & SHIFT:  # move in whole grid squares
                n = int(self.grid_n.get())
                du, dv = round(du * n) / n, round(dv * n) / n
            st = json.loads(orig)
            if st["kind"] == "ellipse":
                u0, v0, u1, v1 = st["box"]
                st["box"] = [round(u0 + du, 5), round(v0 + dv, 5), round(u1 + du, 5), round(v1 + dv, 5)]
            else:
                st["pts"] = [[round(u + du, 5), round(v + dv, 5)] for u, v in st["pts"]]
            self.strokes[i] = st
        self.redraw()

    def select_release(self):
        if self.drag[0] == "pan":
            return
        if self.undo_stack and self.undo_stack[-1] == json.dumps(self.strokes):
            self.undo_stack.pop()  # clicked without moving anything (the undone steps stay redoable)
            self.redo_stack = self.redo_kept
        else:
            self.changed()

    def delete_selected_stroke(self):
        if self.sel is not None and self.tool.get() == "select":
            self.push_undo()
            del self.strokes[self.sel]
            self.sel = None
            self.changed()

    # ------------------------------------------------------------ copy / paste / flip / turn

    def targets(self):
        """What copy / flip / turn work on: the selected stroke, or the whole drawing when nothing is selected."""
        return [self.sel] if self.sel is not None else list(range(len(self.strokes)))

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
        new = [self.map_stroke(st, lambda u, v: (u + d, v - d)) for st in json.loads(self.clipboard)]
        self.push_undo()
        self.strokes += new
        self.sel = len(self.strokes) - 1 if len(new) == 1 else None
        self.changed()

    def map_stroke(self, st, fn):
        """The stroke with every point moved by fn(u, v) -> (u, v)."""
        def pt(u, v):
            return [round(c, 5) for c in fn(u, v)]
        if st["kind"] == "ellipse":
            (a, b), (c, d) = pt(*st["box"][:2]), pt(*st["box"][2:])
            return {"kind": "ellipse", "box": [min(a, c), min(b, d), max(a, c), max(b, d)]}
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
        for i in idx:
            self.strokes[i] = self.map_stroke(self.strokes[i], fn)
            if turned and self.strokes[i]["kind"] == "arc":  # still round (see arc.py)
                self.strokes[i]["k"] = 1 / self.strokes[i].get("k", 1.0)
        self.changed()

    def on_drag(self, e):
        self.show_position(e)
        if not self.drag:
            return
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
        start, pt, tool = self.drag[1], self.event_pt(e), self.tool.get()
        if tool in ("square", "circle") and e.state & CTRL:
            pt = self.perfect(start, pt)
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
        if self.drag and (self.tool.get() == "select" or self.drag[0] in ("points", "pen")):
            self.select_release()
        drag, self.drag = self.drag, None
        if not drag:
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
        cursor = "fleur" if self.draft is None and self.curve_handle_at(e.x, e.y) is not None else "crosshair"
        if str(self.canvas.cget("cursor")) != cursor:
            self.canvas.config(cursor=cursor)
        if self.follow:  # a stroke started with a click follows the mouse
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.follow, self.drag = self.drag, None
        elif self.draft and self.tool.get() in ("poly", "arc"):
            self.draft["pts"][1 if self.arc_bend else -1] = self.event_pt(e)
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
        if self.draft or self.follow:
            if self.draft and self.draft["kind"] == "poly" and self.tool.get() == "poly":
                return self.finish_poly()
            return self.cancel_draft()
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
        i = self.hit_stroke(e.x, e.y)
        if i is None:
            if self.sel is not None:
                self.sel = None
                self.redraw()
            return
        self.sel = i
        self.redraw()
        self.show_menu(e, i)

    def show_menu(self, e, i):
        st = self.strokes[i]
        m = tk.Menu(self, tearoff=0)
        if st["kind"] == "curve":
            m.add_command(label=tr("drawer.add_anchor_here"), command=lambda: self.add_curve_anchor(e))
            # one half follows the other; the half right-clicked keeps its shape
            symmetry_menu(m, st.get("sym"), lambda mode: self.set_curve_symmetry(i, mode, e))
            self._formula_picks = {}  # (kept, so the dots show)
            formula_menu(m, DrawerHost(self), self._formula_picks)
        elif st["kind"] == "poly":
            m.add_command(label=tr("drawer.add_point_here"), command=lambda: self.add_poly_point(i, e))
        m.add_command(label=tr("drawer.delete_stroke"), accelerator=tr("drawer.del"),
                      command=lambda: self.delete_stroke(i))
        m.add_command(label=tr("drawer.copy_stroke"), accelerator=tr("drawer.ctrl_c"), command=self.copy)
        m.add_command(label=tr("drawer.paste"), accelerator=tr("drawer.ctrl_v"), command=self.paste,
                      state="normal" if self.clipboard else "disabled")
        m.add_separator()
        m.add_command(label=tr("drawer.flip_sideways"), accelerator=tr("drawer.ctrl_h"),
                      command=lambda: self.flip(True))
        m.add_command(label=tr("drawer.flip_upside_down"), accelerator=tr("drawer.ctrl_j"),
                      command=lambda: self.flip(False))
        m.add_command(label=tr("drawer.turn_90_left"), accelerator=tr("drawer.ctrl_left"),
                      command=lambda: self.turn(False))
        m.add_command(label=tr("drawer.turn_90_right"), accelerator=tr("drawer.ctrl_right"),
                      command=lambda: self.turn(True))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def add_curve_anchor(self, e, near=None):
        """A new anchor on the selected curve where it's nearest to the mouse, moved to the mouse (snapped unless
        Shift). near: only if the curve is that close (pixels)."""
        if self.sel is None or self.strokes[self.sel]["kind"] != "curve":
            return
        st = self.strokes[self.sel]
        seg, t, d = nearest(st["pts"], self.to_xy, e.x, e.y)
        if near is not None and d > near:
            return
        before = json.dumps(self.strokes)
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

    def delete_stroke(self, i):
        self.push_undo()
        del self.strokes[i]
        self.sel = None
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
            pts = [self.to_screen(u, v) for u, v in stroke_points(self.strokes[i])]
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
        self.strokes.append(st)
        if st["kind"] == "curve":
            self.sel = len(self.strokes) - 1  # selected, so its handles can be bent right away
        self.changed()

    def cancel_draft(self):
        self.draft = None
        self.drag = None
        self.follow = None
        self.arc_bend = False
        self.redraw()

    def push_undo(self, before=None):
        """A step to undo (before: the strokes as JSON, if they were already changed); drops the redo steps."""
        self.undo_stack.append(before or json.dumps(self.strokes))
        del self.undo_stack[:-200]
        self.redo_kept, self.redo_stack = self.redo_stack, []

    def undo(self):
        if self.draft:
            return self.cancel_draft()
        if self.undo_stack:
            self.redo_stack.append(json.dumps(self.strokes))
            self.strokes = json.loads(self.undo_stack.pop())
            self.changed()

    def redo(self):
        if self.draft:
            return self.cancel_draft()
        if self.redo_stack:
            self.undo_stack.append(json.dumps(self.strokes))
            self.strokes = json.loads(self.redo_stack.pop())
            self.changed()

    def clear(self):
        if self.strokes:
            self.push_undo()
            self.strokes = []
            self.changed()

    def changed(self):
        self.dirty = True
        if self.sel is not None and self.sel >= len(self.strokes):
            self.sel = None
        self.redraw()

    # ------------------------------------------------------------ library

    def refresh_list(self, select=None):
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

    def keep_changes(self):
        """True if it's fine to throw away the drawing (nothing unsaved, or the user said so)."""
        return not (self.dirty and self.strokes) or messagebox.askyesno(
            tr("drawer.spiderweb"), tr("drawer.the_current_drawing_isn_t_saved"), parent=self)

    def open_selected(self):
        name = self.picked()
        if not name or not self.keep_changes():
            return
        strokes = load_shape(name)
        if strokes is None:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_read_the_shape", name=name), parent=self)
            return
        self.open_shape(name, strokes)

    def open_shape(self, name, strokes):
        self.strokes, self.undo_stack, self.draft, self.sel = strokes, [], None, None
        self.redo_stack, self.redo_kept = [], []
        self.name.set(name)
        self.saved_name = name or None
        self.dirty = False
        self.redraw()

    def new(self):
        if self.keep_changes():
            self.open_shape("", [])

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
            os.remove(shape_file(name))
        except OSError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_delete", e=e), parent=self)
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
        self.strokes = join_strokes(self.strokes)
        self.sel = None  # joining can change the order
        try:
            save_shape(name, self.strokes)
        except OSError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_save", e=e), parent=self)
            return None
        self.name.set(name)
        self.saved_name = name
        self.dirty = False
        self.refresh_list(select=name)
        self.redraw()
        return name

    def use(self):
        same = self.saved_name and clean_name(self.name.get()) == self.saved_name and not self.dirty
        name = self.saved_name if same else self.save()
        if name:
            self.app.use_custom(name)

    def close(self):
        if self.keep_changes():
            self.app.drawer = None
            self.destroy()

    # ------------------------------------------------------------ drawing

    def redraw(self):
        c = self.canvas
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        (x0, y0), (x1, y1) = self.to_screen(0, 1), self.to_screen(1, 0)
        c.create_rectangle(x0, y0, x1, y1, fill="#ffffff", outline="")  # the board
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
                        color = "#7f8fb0"  # the middle of the board: 0
                    else:
                        color = "#9aa4b4" if i % major == 0 else "#dde3ec"
                    if vertical:
                        x = self.to_screen(i / n, 0)[0]
                        c.create_line(x, 0, x, ch, fill=color)
                    else:
                        y = self.to_screen(0, i / n)[1]
                        c.create_line(0, y, cw, y, fill=color)
                    i += step
        c.create_rectangle(x0, y0, x1, y1, outline="#606060")
        w = max(2, round(2 * self.scale))
        closed = strokes_closed(self.strokes)
        for i, st in enumerate(self.strokes):  # a curve with formulas: the curve as drawn (the origin path), dashed
            if st["kind"] == "curve" and has_formula(st):
                self.draw_stroke(dict(st, shape=None, pattern=None), "#e89a9a" if i == self.sel else "#efc0c0", 1,
                                 dash=(6, 4))
        for i, st in enumerate(self.strokes):
            self.draw_stroke(st, "#ff8c1a" if i == self.sel else "#c0392b", w + (1 if i == self.sel else 0))
        s = self.scale
        r, h = 4 * s, 3.5 * s
        sel = self.strokes[self.sel] if self.sel is not None else None
        curve = sel if sel and sel["kind"] == "curve" else None
        if curve:  # handle lines: blue on a white edge
            for width, color in ((max(3, round(3.5 * s)), "#ffffff"), (max(1, round(1.5 * s)), "#0050d0")):
                for a, b in handle_lines(curve["pts"]):
                    c.create_line(*self.to_screen(*a), *self.to_screen(*b), fill=color, width=width)
        if self.tool.get() == "select":  # the ends of strokes, ellipse corners: squares
            for i, j, u, v in self.handles():
                if not self.is_pen_point(i, j):
                    x, y = self.to_screen(u, v)
                    c.create_rectangle(x - h, y - h, x + h, y + h, fill="#ffffff",
                                       outline="#c05a00" if i == self.sel else "#0050d0")
        if curve:  # its handle dots and anchors work with any tool
            for j, kind in pen_handles(curve["pts"]):
                x, y = self.to_screen(*curve["pts"][j])
                if kind == "ctrl":
                    q = r + 0.5 * s
                    c.create_oval(x - q, y - q, x + q, y + q, fill="#0050d0", outline="#ffffff",
                                  width=max(1, round(s)))
                elif kind == "anchor":
                    q = r + 1.5 * s
                    c.create_oval(x - q, y - q, x + q, y + q, fill="#ffffff", outline="#0050d0",
                                  width=max(2, round(2 * s)))
        for u, v in open_ends(self.strokes):  # open ends: red dots
            x, y = self.to_screen(u, v)
            c.create_oval(x - r, y - r, x + r, y + r, fill="#ff2020", outline="#800000")
        if self.draft:
            self.draw_stroke(self.draft, "#0a8f0a", w)
            self.draw_draft_points(r, h)
        if not self.strokes:
            text = tr("drawer.nothing_drawn_yet")
        elif closed:
            text = tr("drawer.closed_shape_empty_fill_and_spam")
        elif len(open_paths(self.strokes)) == 1:
            text = tr("drawer.one_gap_red_dots_fill_and")
        else:
            text = tr("drawer.open_ends_red_dots_fill_and")
        if self.dirty and self.strokes:
            text += tr("drawer.not_saved_yet")
        self.state_label.config(text=text, foreground="#1d6b1d" if closed else "#9a4b00")

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
                c.create_oval(x - q, y - q, x + q, y + q, fill="#ffffff", outline="#0050d0", width=max(2, round(2 * s)))
            else:
                c.create_rectangle(x - h, y - h, x + h, y + h, fill="#ffffff", outline="#0a8f0a",
                                   width=max(1, round(s)))

    def draw_stroke(self, st, color, width, dash=None):
        coords = [c for u, v in stroke_points(st) for c in self.to_screen(u, v)]
        if len(coords) >= 4:
            self.canvas.create_line(*coords, fill=color, width=width, capstyle="round", joinstyle="round", dash=dash)
        elif coords:
            x, y = coords
            self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color)
