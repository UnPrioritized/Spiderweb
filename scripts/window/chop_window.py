"""The Chop window (chop.py): the selected shapes' notes cut into a rhythm, shown live on the piano roll (the shared
part: tool_window.py). A rhythm list (our own rhythms + ones saved by the user in spiderweb/rhythms.json), a strip
showing one repeat of the rhythm where pieces can be drawn (drag), deleted (right-click / right-drag) and made louder / quieter
(drag a piece up / down), the rhythm's length in steps, one step's length (picked like the snap), the pieces'
velocities as % of each note or fixed 1..127 and how much they count, and Absolute (the rhythm follows the song's
grid)."""

import json
import os
import tkinter as tk
from tkinter import simpledialog, ttk

from files.about import HERE
from files.lang import tr
from files.mathexpr import calc, fmt
from files.safefile import write_text
from files.snap import snap_beats, snap_text
from notes.chop import (CHOP_DEFAULTS, MAX_STEPS, RHYTHMS, clean_chop, clean_pieces, rhythm, switched, too_many,
                        top)
from window.snap_picker import SnapPicker
from window.tool_window import ORANGE, ToolWindow
from window.widgets import Scrub, Tooltip

RHYTHMS_FILE = os.path.join(HERE, "rhythms.json")
CELLS = (1, 2, 3, 4, 6, 8)  # the strip's grid: cells per step


def load_rhythms():
    """The user's saved rhythms [{"name", "steps", "pieces", "fixed"}]."""
    try:
        with open(RHYTHMS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        out = []
        for r in data.get("rhythms", []):
            steps = max(1, min(MAX_STEPS, int(r["steps"])))
            fixed = r.get("fixed") is True
            pieces = clean_pieces(r["pieces"], steps, fixed)
            if pieces:
                out.append({"name": str(r["name"]), "steps": steps, "pieces": pieces, "fixed": fixed})
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def save_rhythms(items):
    write_text(RHYTHMS_FILE, json.dumps({"rhythms": items}, indent=1))


def piece_beats(snap, app):
    """One step of the rhythm in beats: the snap picked (off = 1 tick)."""
    return snap_beats(snap, app.beats) or 1 / app.ppq


class RhythmStrip(tk.Canvas):
    """One repeat of the rhythm. Press on empty space = a new piece there, as loud as the mouse is high; dragging
    makes it longer (sideways, a grid cell joins as soon as the mouse is in it; pieces it covers are cut back) and
    louder / quieter (up / down). Drag a piece up / down = its velocity (% sticks at 100, Shift = no sticking; fixed
    doesn't stick), its number shown while dragging. Right-click / right-drag = delete the pieces the mouse passes
    over. changed(pieces, done)."""

    def __init__(self, parent, scale, changed):
        self.w, self.h = round(360 * scale), round(90 * scale)
        super().__init__(parent, width=self.w, height=self.h, background="white", highlightthickness=1,
                         highlightbackground="#bbb", cursor="crosshair")
        self.changed, self.steps, self.pieces, self.cells, self.drag = changed, 1, [], 4, None
        self.fixed, self.erased = False, False  # (velocities 1..127 instead of % of the note)
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<ButtonPress-3>", self.erase_press)
        self.bind("<B3-Motion>", self.erase)
        self.bind("<ButtonRelease-3>", self.erase_release)

    def show(self, steps, pieces, fixed):
        self.steps, self.pieces, self.fixed = steps, [list(p) for p in pieces], fixed
        self.draw()

    def x(self, step):
        return step / self.steps * self.w

    def step_at(self, x):
        return max(0.0, min(float(self.steps), x / self.w * self.steps))

    def vel_y(self, v):
        return self.h - 2 - (self.h - 4) * v / top(self.fixed)

    def y_vel(self, e):
        """The velocity at the mouse's height."""
        hi = top(self.fixed)
        v = max(1.0, min(float(hi), round((self.h - 2 - e.y) / (self.h - 4) * hi)))
        if not self.fixed and abs(v - 100) < 6 and not e.state & 1:  # (% sticks at 100; Shift = no sticking)
            v = 100.0
        return v

    def draw(self):
        self.delete("all")
        n = self.steps * self.cells
        for i in range(1, n):
            x = self.x(i / self.cells)
            strong = i % self.cells == 0
            self.create_line(x, 0, x, self.h, fill="#bbb" if strong else "#e8e8e8")
        if not self.fixed:  # (the note's own velocity)
            y = self.vel_y(100)
            self.create_line(0, y, self.w, y, fill="#ddd", dash=(2, 3))
        for s, ln, v in self.pieces:
            self.create_rectangle(self.x(s) + 1, self.vel_y(v), self.x(s + ln) - 1, self.h - 1, fill=ORANGE,
                                  outline="#b06d00")

    def number(self, lo, hi, v):
        """The velocity being dragged, written over its piece (inside it near the top when there's no room)."""
        x, y = (self.x(lo) + self.x(hi)) / 2, self.vel_y(v)
        text = fmt(v) if self.fixed else tr("chop.percent", v=fmt(v))
        room = y > 14
        self.create_text(x, y - 1 if room else y + 2, text=text, anchor="s" if room else "n", fill="#333")

    def piece_at(self, x):
        s = self.step_at(x)
        return next((i for i, (a, ln, _) in enumerate(self.pieces) if a <= s < a + ln), None)

    def snapped(self, x):
        """The grid line before x."""
        return min(float(self.steps), int(self.step_at(x) * self.cells) / self.cells)

    def press(self, e):
        i = self.piece_at(e.x)
        if i is not None:
            self.drag = ("vel", i)
            self.move(e)
        else:
            a = self.snapped(e.x)
            self.drag = ("new", a)
            self.move(e)

    def move(self, e):
        if not self.drag:
            return
        v = self.y_vel(e)
        if self.drag[0] == "vel":
            s, ln, _ = p = self.pieces[self.drag[1]]
            p[2] = v
            self.draw()
            self.number(s, s + ln, v)
            self.changed(self.pieces, False)
            return
        a, cell = self.drag[1], 1 / self.cells  # (a = the start of the cell pressed: it's always in the piece)
        b = self.snapped(e.x)  # (the cell under the mouse joins as soon as the mouse is in it)
        lo, hi = min(a, b), min(float(self.steps), max(a, b) + cell)
        lo = min(lo, hi - cell)
        self.draw()
        self.create_rectangle(self.x(lo) + 1, self.vel_y(v), self.x(hi) - 1, self.h - 1, fill="",
                              outline="#b06d00", dash=(3, 2))
        self.number(lo, hi, v)
        self.drag = ("new", a, lo, hi, v)

    def release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        if d[0] == "new" and len(d) == 5:
            _, _, lo, hi, v0 = d
            kept = []
            for s, ln, v in self.pieces:  # (pieces under the new one are cut back to what's outside it)
                if s < lo:
                    kept.append([s, min(s + ln, lo) - s, v])
                if s + ln > hi:
                    kept.append([max(s, hi), s + ln - max(s, hi), v])
            self.pieces = sorted(kept + [[lo, hi - lo, v0]])
        self.draw()
        self.changed(self.pieces, True)

    def erase_press(self, e):
        self.erased = False
        self.erase(e)

    def erase(self, e):
        i = self.piece_at(e.x)
        if i is not None:
            del self.pieces[i]
            self.erased = True
            self.draw()
            self.changed(self.pieces, False)

    def erase_release(self, e):
        if self.erased:  # (one undo step for the whole drag)
            self.erased = False
            self.changed(self.pieces, True)


class ChopWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "chop", "chop_window", "chop_pos", CHOP_DEFAULTS

    def __init__(self, app):
        super().__init__(app)
        if not any(self.before.values()):  # (opened on unchopped shapes: they show chopped straight away)
            self.preview()

    def clean(self, cfg):
        return clean_chop(cfg)

    def build(self, box):
        s = self.app.scale
        self.long = None  # (the pieces before Steps was made smaller: back when it's made bigger again)
        box.columnconfigure(0, minsize=round(90 * s))
        self.on = tk.BooleanVar()
        b = ttk.Checkbutton(box, text=tr("chop.on"), variable=self.on, command=lambda: self.put("on", self.on.get()))
        b.grid(row=1, column=0, columnspan=2, sticky="w")
        Tooltip(b, tr("chop.tip_on"))

        ttk.Label(box, text=tr("chop.rhythm")).grid(row=2, column=0, sticky="e", padx=(0, 8), pady=(6, 2))
        row = ttk.Frame(box)
        row.grid(row=2, column=1, sticky="w", pady=(6, 2))
        self.rhythm_button = ttk.Menubutton(row, width=14)
        self.rhythm_button.pack(side="left")
        self.rhythm_menu = tk.Menu(self.rhythm_button, tearoff=0, postcommand=self.fill_menu)
        self.rhythm_button["menu"] = self.rhythm_menu
        Tooltip(self.rhythm_button, tr("chop.tip_rhythm"))
        self.save_button = ttk.Button(row, text=tr("chop.save"), command=self.save_rhythm)
        self.save_button.pack(side="left", padx=(6, 0))
        Tooltip(self.save_button, tr("chop.tip_save"))
        self.delete_button = ttk.Button(row, text=tr("chop.delete"), command=self.delete_rhythm)
        self.delete_button.pack(side="left", padx=(6, 0))

        self.strip = RhythmStrip(box, s, self.on_strip)
        self.strip.grid(row=3, column=0, columnspan=2, pady=(4, 2))
        Tooltip(self.strip, tr("chop.tip_strip"))

        ttk.Label(box, text=tr("chop.grid")).grid(row=4, column=0, sticky="e", padx=(0, 8), pady=2)
        self.cells = tk.StringVar(value=str(self.strip.cells))
        grid = ttk.Combobox(box, textvariable=self.cells, values=[str(c) for c in CELLS], state="readonly", width=4)
        grid.grid(row=4, column=1, sticky="w", pady=2)
        grid.bind("<<ComboboxSelected>>", lambda e: self.on_cells())
        Tooltip(grid, tr("chop.tip_grid"))

        lb = ttk.Label(box, text=tr("chop.steps"))
        lb.grid(row=5, column=0, sticky="e", padx=(0, 8), pady=2)
        self.steps_var = tk.StringVar()
        self.steps_entry = ttk.Entry(box, textvariable=self.steps_var, width=6)
        self.steps_entry.grid(row=5, column=1, sticky="w", pady=2)
        self.steps_entry.bind("<Return>", lambda e: (self.on_steps(), "break")[1])
        self.steps_entry.bind("<FocusOut>", lambda e: self.on_steps())
        Scrub(self.app, [(self.steps_entry, self.steps_var, lambda: self.on_steps(False))], (1, 4, 1), 1, MAX_STEPS,
              label=lb)
        Tooltip(self.steps_entry, tr("chop.tip_steps"))

        ttk.Label(box, text=tr("chop.step_length")).grid(row=6, column=0, sticky="e", padx=(0, 8), pady=2)
        row = ttk.Frame(box)
        row.grid(row=6, column=1, sticky="w", pady=2)
        self.snap = tk.StringVar(value=CHOP_DEFAULTS["snap"])
        self.snap_picker = SnapPicker(self.app, row, self.snap)
        self.snap_picker.button.pack(side="left")
        Tooltip(self.snap_picker.button, tr("chop.tip_step_length"))
        self.ticks_text = ttk.Label(row, text="", foreground="#777")
        self.ticks_text.pack(side="left", padx=(6, 0))
        self.snap.trace_add("write", lambda *_: self.on_snap())

        lb = ttk.Label(box, text=tr("chop.velocity"))
        lb.grid(row=7, column=0, sticky="e", padx=(0, 8), pady=2)
        row = ttk.Frame(box)
        row.grid(row=7, column=1, sticky="w", pady=2)
        self.mode_box = ttk.Combobox(row, values=[tr("chop.mode_share"), tr("chop.mode_fixed")], state="readonly",
                                     width=max(len(tr("chop.mode_share")), len(tr("chop.mode_fixed"))))
        self.mode_box.pack(side="left", padx=(0, 8))
        self.mode_box.bind("<<ComboboxSelected>>", lambda e: self.on_mode())
        Tooltip(self.mode_box, tr("chop.tip_mode"))
        ttk.Label(row, text=tr("chop.amount")).pack(side="left", padx=(0, 4))
        self.vel_var = tk.StringVar()
        self.vel_entry = ttk.Entry(row, textvariable=self.vel_var, width=6)
        self.vel_entry.pack(side="left")
        self.vel_entry.bind("<Return>", lambda e: (self.on_vel(), "break")[1])
        self.vel_entry.bind("<FocusOut>", lambda e: self.on_vel())
        Scrub(self.app, [(self.vel_entry, self.vel_var, lambda: self.on_vel(False))], (1, 10, 0.1), 0, 100, label=lb)
        ttk.Label(row, text=tr("unit.percent"), foreground="#777").pack(side="left", padx=(5, 0))
        Tooltip(self.vel_entry, tr("chop.tip_velocity"))

        self.abs = tk.BooleanVar()
        b = ttk.Checkbutton(box, text=tr("chop.absolute"), variable=self.abs,
                            command=lambda: self.put("abs", self.abs.get()))
        b.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))
        Tooltip(b, tr("chop.tip_absolute"))

        self.info = ttk.Label(box, text="", foreground="#777")
        self.info.grid(row=9, column=0, columnspan=2, sticky="w", pady=(6, 0))

        row = ttk.Frame(box)
        row.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("chop.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("chop.tip_reset"))
        ttk.Button(row, text=tr("chop.accept"), command=self.accept).pack(side="right")

    # ---- the rhythm list

    def fill_menu(self):
        m = self.rhythm_menu
        m.delete(0, "end")
        for name in RHYTHMS:
            m.add_command(label=tr(f"chop.rhythm_{name}"), command=lambda n=name: self.pick(n))
        saved = load_rhythms()
        if saved:
            m.add_separator()
            for r in saved:
                m.add_command(label=r["name"], command=lambda r=r: self.pick_saved(r))

    def pick(self, name):
        self.long = None
        steps, pieces = rhythm(name)  # (ours are in %: turned to fixed velocities when that's picked)
        self.cfg.update(name=name, steps=steps, pieces=switched(pieces, True) if self.cfg["fixed"] else pieces)
        self.put("on", True)

    def pick_saved(self, r):
        self.long = None
        self.cfg.update(name=r["name"], steps=r["steps"], pieces=[list(p) for p in r["pieces"]], fixed=r["fixed"])
        self.put("on", True)

    def rhythm_name(self):
        name = self.cfg["name"]
        if name in RHYTHMS:
            return tr(f"chop.rhythm_{name}")
        return name or tr("chop.drawn")

    def save_rhythm(self):
        """Save the rhythm under a name (asked; a built-in rhythm's name is asked again: it couldn't be told apart)."""
        taken = {n.casefold() for n in RHYTHMS} | {tr(f"chop.rhythm_{n}").casefold() for n in RHYTHMS}
        prompt, name = tr("chop.save_prompt"), "" if self.cfg["name"] in RHYTHMS else self.cfg["name"]
        while True:
            name = simpledialog.askstring(tr("chop.save_title"), prompt, parent=self, initialvalue=name)
            if not name or not name.strip():
                return
            name = name.strip()
            if name.casefold() not in taken:
                break
            prompt = tr("chop.name_taken", name=name)
        items = [r for r in load_rhythms() if r["name"] != name]
        items.append({"name": name, "steps": self.cfg["steps"], "pieces": self.cfg["pieces"],
                      "fixed": self.cfg["fixed"]})
        save_rhythms(items)
        self.put("name", name)

    def delete_rhythm(self):
        name = self.cfg["name"]
        save_rhythms([r for r in load_rhythms() if r["name"] != name])
        self.put("name", "")

    # ---- the numbers

    def on_strip(self, pieces, done):
        self.long = None
        self.cfg["pieces"] = [list(p) for p in pieces]
        self.cfg["name"] = ""
        self.put("on", True, done)

    def on_cells(self):
        self.strip.cells = int(self.cells.get())
        self.strip.draw()

    def on_steps(self, done=True):
        try:
            v = int(float(calc(self.steps_var.get())))
            if not 1 <= v <= MAX_STEPS:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            self.steps_entry.config(style="Bad.TEntry")
            return
        self.steps_entry.config(style="TEntry")
        if v != self.cfg["steps"]:
            if self.long is None:  # (pieces past the new end stay in memory while the window is open)
                self.long = [list(p) for p in self.cfg["pieces"]]
            self.cfg["pieces"] = clean_pieces(self.long, v, self.cfg["fixed"])
            self.cfg["name"] = ""
            self.put("steps", v, done)

    def on_snap(self):
        snap = self.snap.get()
        if snap != self.cfg["snap"]:
            self.cfg["len"] = piece_beats(snap, self.app)
            self.put("snap", snap)

    def on_mode(self):
        """% of each note <-> fixed velocities: the pieces are turned over so they stay about as loud."""
        fixed = self.mode_box.current() == 1
        self.mode_box.selection_clear()
        if fixed != self.cfg["fixed"]:
            self.cfg["pieces"] = switched(self.cfg["pieces"], fixed)
            if self.long is not None:
                self.long = switched(self.long, fixed)
            self.put("fixed", fixed)

    def on_vel(self, done=True):
        try:
            v = float(calc(self.vel_var.get()))
            if not 0 <= v <= 100:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            self.vel_entry.config(style="Bad.TEntry")
            return
        self.vel_entry.config(style="TEntry")
        if v != self.cfg["vel"]:
            self.put("vel", v, done)

    def retarget(self):
        self.long = None
        super().retarget()

    def put_state(self, state):
        self.long = None
        super().put_state(state)

    def reset(self):
        self.long = None
        super().reset()

    def show(self):
        c = self.cfg
        if self.on.get() != c["on"]:
            self.on.set(c["on"])
        if self.abs.get() != c["abs"]:
            self.abs.set(c["abs"])
        self.rhythm_button.config(text=self.rhythm_name())
        saved = c["name"] and any(r["name"] == c["name"] for r in load_rhythms())
        self.delete_button.state(["!disabled"] if saved else ["disabled"])
        if self.mode_box.current() != int(c["fixed"]):
            self.mode_box.current(int(c["fixed"]))
        if ((self.strip.steps, self.strip.pieces, self.strip.fixed) != (c["steps"], c["pieces"], c["fixed"])
                and not self.strip.drag):
            self.strip.show(c["steps"], c["pieces"], c["fixed"])
        for var, entry, text in ((self.steps_var, self.steps_entry, str(c["steps"])),
                                 (self.vel_var, self.vel_entry, fmt(c["vel"]))):
            if var.get() != text:
                var.set(text)
            if str(entry.cget("style")) != "TEntry":
                entry.config(style="TEntry")
        if c["snap"] and self.snap.get() != c["snap"]:
            self.snap.set(c["snap"])
        self.ticks_text.config(text=tr("chop.ticks", ticks=fmt(round(max(c["len"] * self.app.ppq, 1), 2))))

    def preview(self, now=True):
        super().preview(now)
        if self.late or not self.winfo_exists():
            return
        app = self.app
        shapes = [app.shapes[i] for i in self.targets if i < len(app.shapes)]
        after = sum(len(app.notes_of(sh)) for sh in shapes)
        plain = [app.notes_of({k: v for k, v in sh.items() if k != "chop"}) for sh in shapes]
        before = sum(len(n) for n in plain)
        cl = self.clean(self.cfg)
        stuck = any(too_many(n, cl, app.ppq) for n in plain)
        empty = self.cfg["on"] and not self.cfg["pieces"]
        self.info.config(text=tr("chop.no_pieces") if empty else tr("chop.too_many") if stuck else
                         tr("chop.count", before=before, after=after), foreground="#d00000" if stuck or empty else "#777")


open_chop = ChopWindow.open


def quick_chop(app):
    """Ctrl+U: the selected shapes' notes cut into even pieces of the snap's length (off = 1 tick), one undo step."""
    if not app.sels:
        return
    snap = app.snap.get()
    chop = clean_chop(dict(CHOP_DEFAULTS, len=piece_beats(snap, app), snap=snap))
    app.push_undo(name=tr("chop.quick_step"))
    for i in app.sels:
        app.shapes[i]["chop"] = dict(chop)
    app.shapes_changed()
    app.status.config(text=tr("chop.quick_done", snap=snap_text(snap)))
