"""The Properties window (PLAN.md "Properties", user 2026-11-11): Properties… = the LAST item of a shape's right-click
menu (roll_menu.properties_item), Alt+Enter, and the Properties… button in every step's window (edit_line.EditLine).
It replaced the Effect order window. One window that follows the selection and stays open beside the piano roll.

It shows the selected shape (several selected: the one picked first, stage D; stage E lists them) in the Settings
window's look (settings_window.Form): its basics at the top (kind / name / "Made by", place and size, gate and
velocity, colours and channel), then ONE ordered list of every step done to its notes (notes/steps.py order_of):
the fixed ones greyed at the top (the notes as drawn; Single channel's own overlap fix) and each movable one a line
with a drag grip, an on / off tick, its name (click = its own window, on that tab), its main number in a box and ×.
Every change is one undo step; typing a step's number = editing that step (the steps after it are hidden until the
user leaves it: PLAN.md stage C, App.colour_edit). Add ▾ opens the existing tool windows."""

import importlib
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from notes import steps as S
from notes.custom import ROUND, SPAM_FILLS, gate_ticks, pack_notes, unpack_notes
from notes.fx import is_page, pages
from notes.recolour import (KEEP, MODES, PAGE, clean_recolour, edited_step, new_seed, ordered, order_of as rc_order,
                            page_removed, shape_order, shape_step_removed, stirs_of, tags_of as rc_tags, turned_map)
from window import look
from window.edit_line import names_line, tag_name
from window.settings_window import ScrollPage
from window.widgets import ShortLabel, Tooltip, bad, good, leave_box, remember_place, unchanged

DRAG_PX = 4  # (a press moving less than this is a click: opens the step's window)
LIST_MOST = 330  # the tallest the step list shows at 100 % size, in pixels (longer lists scroll)
WRAP = 320  # grey lines' width at 100 % size
START_AMOUNT = {"shuffle": 100.0, "scatter": 50.0, "clumps": 20.0}  # (as the side panel's dropdown)
PAGE_WINDOWS = {"claw": ("window.claw_window", "open_claw", "claw_window"),
                "strum": ("window.strum_window", "open_strum", "strum_window"),
                "chop": ("window.chop_window", "open_chop", "chop_window"),
                "trim": ("window.trim_window", "open_trim", "trim_window"),
                "path": ("window.path_window", "open_path", "path_window")}
ADD_TOOLS = ("claw", "strum", "chop", "trim", "path")
# (a step's main number in its box: (unit word key, lowest, highest); None = it has none: the click opens it)
NUMBERS = {"mode": ("props.unit_pct", 0.0, 100.0), "stir": ("props.unit_deg", -360000.0, 360000.0),
           "warp": ("props.unit_pct", 0.0, 400.0), "liquify": ("props.unit_pct", 0.0, 400.0),
           "sustain": ("unit.ticks", 0.0, 10 ** 7), "extend": ("unit.ticks", 0.0, 10 ** 7),
           "subtract": ("unit.ticks", 0.0, 10 ** 7)}


def open_properties(app):
    w = getattr(app, "properties_window", None)
    if w is not None and w.winfo_exists():
        w.sync()
        w.deiconify()
        w.lift()
    else:
        PropertiesWindow(app)


class PropertiesWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.properties_window = self
        self.editing = None  # (the kind of step whose number was typed here last: it is being edited, stage C)
        self.title(tr("props.title"))
        self.transient(app)
        self.resizable(False, False)
        s = app.scale
        box = self.box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ShortLabel(box, least=30, text="")
        self.what.pack(anchor="w", fill="x")
        self.more = ttk.Label(box, text="", foreground=look.HINT, wraplength=int(WRAP * s), justify="left")
        self.basics = ttk.Frame(box)
        self.basics.pack(anchor="w", fill="x", pady=(6, 0))
        self.basics.columnconfigure(1, weight=1)
        self.build_basics()
        head = self.steps_head = ttk.Frame(box)
        head.pack(anchor="w", fill="x", pady=(14, 0))
        ttk.Label(head, text=tr("props.steps")).pack(side="left")
        self.steps_hint = Tooltip(head.winfo_children()[0], tr("props.steps_tip"))
        self.steps_line = ttk.Separator(box)
        self.steps_line.pack(fill="x", pady=(3, 6))
        self.fixed = ttk.Frame(box)
        self.fixed.pack(anchor="w", fill="x")
        self.scroll = ScrollPage(box, padding=(0, 0, 0, 0))
        self.scroll.most = int(LIST_MOST * s)
        self.scroll.pack(anchor="w", fill="x")
        self.list = self.scroll.page
        self.rows = []  # [{"tag", "frame", "grip", "name", "tick", "var", "box", "boxvar", "x"}] in order
        self.pool = []  # (every line made so far, kept: build fills them in)
        self.shown = None  # (what the list shows: rebuilt only when it changes)
        self.drop_line = tk.Frame(self.list, height=2, background=look.HANDLE)
        self.press = None
        self.empty = ttk.Label(self.list, text=tr("props.no_steps"), foreground=look.HINT)
        self.hidden = ttk.Frame(box)  # (stage C's line: the steps hidden while one is edited + Preview all)
        self.hidden_text = ttk.Label(self.hidden, text="", foreground=look.HINT, wraplength=int(WRAP * s),
                                     justify="left")
        self.hidden_text.pack(side="left", fill="x", expand=True)
        b = ttk.Button(self.hidden, text=tr("edit.preview_all"), command=self.preview_all)
        b.pack(side="right", padx=(6, 0))
        Tooltip(b, tr("edit.preview_all_tip"))
        self.status = ttk.Label(box, text="", foreground=look.WARN, wraplength=int(WRAP * s), justify="left")
        row = self.buttons = ttk.Frame(box)
        row.pack(anchor="w", fill="x", pady=(8, 0))
        self.add = ttk.Menubutton(row, text=tr("props.add"))
        self.add.pack(side="left")
        Tooltip(self.add, tr("props.add_tip"))
        self.add_menu = tk.Menu(self.add, tearoff=False, postcommand=self.fill_add)
        self.add["menu"] = self.add_menu
        self.hz_button = ttk.Button(row, text=tr("props.hz_settings"), command=self.open_hz)
        Tooltip(self.hz_button, tr("props.hz_settings_tip"))
        ttk.Button(row, text=tr("merge_window.close"), command=self.close).pack(side="right")
        self.edit_line = self  # (App.sync_edit_lines calls .sync() on it)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<MouseWheel>", self.scroll.wheel)
        self.sync()
        remember_place(self, "properties")
        self.deiconify()

    # ---- the basics (settings_window.Form's look: a heading with a line, "Name:" + its control)

    def build_basics(self):
        f = self.basics
        self.r = 0
        self.parts = {}  # name -> the widgets of its row (shown / hidden by sync)
        self.vars, self.entries = {}, {}

        def head(key):
            lb = ttk.Label(f, text=tr(key))
            lb.grid(row=self.r, column=0, columnspan=2, sticky="w", pady=(14 if self.r else 0, 0))
            sep = ttk.Separator(f)
            sep.grid(row=self.r + 1, column=0, columnspan=2, sticky="ew", pady=(3, 6))
            self.r += 2
            return [lb, sep]

        def row(name, label, widget, tip=None):
            lb = ttk.Label(f, text=tr(label))
            lb.grid(row=self.r, column=0, sticky="w", padx=(0, 8), pady=2)
            widget.grid(row=self.r, column=1, sticky="w", pady=2)
            if tip:
                Tooltip(lb, tr(tip))
                Tooltip(widget, tr(tip))
            self.parts[name] = [lb, widget]
            self.r += 1
            return widget

        def value(name, label, tip=None):
            return row(name, label, ttk.Label(f, text="", wraplength=int(WRAP * self.app.scale), justify="left"), tip)

        def number(name, label, unit, tip, fn, two=None):
            fr = ttk.Frame(f)
            boxes = []
            for k, key in enumerate((name,) + ((two,) if two else ())):
                if k:
                    ttk.Label(fr, text="→").pack(side="left", padx=3)
                var = self.vars[key] = tk.StringVar()
                e = ttk.Entry(fr, textvariable=var, width=8 if not two else 5)
                e.pack(side="left")
                e.bind("<Return>", lambda ev, key=key: fn(key))
                leave_box(self.app, e, var, lambda left=False, key=key, e=e: fn(key, left and e))
                boxes.append(e)
                self.entries[key] = e
            word = ttk.Label(fr, text=tr(unit) if unit else "", foreground=look.HINT)
            word.pack(side="left", padx=(5, 0))
            row(name, label, fr, tip)
            fr.boxes, fr.word = boxes, word
            return fr

        self.heads = {"shape": head("props.head_shape")}
        self.kind = value("kind", "props.kind")
        self.name = value("name", "props.name")
        self.made = value("made", "props.made_by", "props.made_by_tip")
        self.heads["place"] = head("props.head_place")
        self.start = number("start", "props.start", "unit.ticks", "props.start_tip", self.on_place)
        self.length = number("length", "props.length", "unit.ticks", "props.length_tip", self.on_place)
        self.low = number("low", "props.low", None, "props.low_tip", self.on_place)
        self.high = number("high", "props.high", None, "props.high_tip", self.on_place)
        self.heads["gate"] = head("props.head_gate")
        self.inside = value("inside", "props.inside", "props.inside_tip")
        self.gate = number("gate", "props.gate", "unit.ticks", "props.gate_tip", self.on_gate)
        self.vel = number("vel0", "props.velocity", None, "props.velocity_tip", self.on_velocity, two="vel1")
        self.vel_note = ttk.Label(f, text=tr("props.velocity_drawn"), foreground=look.HINT,
                                  wraplength=int(WRAP * self.app.scale), justify="left")
        self.vel_note.grid(row=self.r, column=1, sticky="w")
        self.parts["vel_note"] = [self.vel_note]
        self.r += 1
        self.heads["colours"] = head("props.head_colours")
        self.colours = value("colours", "props.colours", "props.colours_tip")
        self.channel = value("channel", "props.channel", "props.channel_tip")

    def show_part(self, name, on):
        for w in self.parts[name]:
            if on and not w.winfo_manager():
                w.grid()
            elif not on and w.winfo_manager():
                w.grid_remove()

    # ---- what it shows

    def shape(self):
        """(index, shape) shown: the one picked first (app.sel), or (None, None)."""
        app = self.app
        i = app.sel if app.sel is not None and app.sel in app.sels else (min(app.sels) if app.sels else None)
        if i is None or i >= len(app.shapes):
            return None, None
        return i, app.shapes[i]

    def after_notes(self):
        """The notes were made again (App.shapes_changed's take-in): the numbers that come from them."""
        if self.winfo_exists():
            self.sync()

    def sync(self):
        if not self.winfo_exists():
            return
        app = self.app
        i, sh = self.shape()
        n = len(app.sels)
        self.what.config(text=tr("props.nothing") if sh is None else tr("props.shape", n=i + 1,
                                                                          name=app.shape_label(sh)))
        more = tr("props.first_of", n=n) if sh is not None and n > 1 else ""
        if more != self.more.cget("text"):
            self.more.config(text=more)
            if more:
                self.more.pack(anchor="w", fill="x", after=self.what)
            else:
                self.more.pack_forget()
        # (nothing selected: only the line saying so; a frame whose rows all go keeps its size in Tk, so whole parts
        # are taken out)
        prev = self.more if self.more.winfo_manager() else self.what
        for part, kw in ((self.basics, dict(anchor="w", fill="x", pady=(6, 0))),
                         (self.steps_head, dict(anchor="w", fill="x", pady=(14, 0))),
                         (self.steps_line, dict(fill="x", pady=(3, 6))), (self.fixed, dict(anchor="w", fill="x")),
                         (self.scroll, dict(anchor="w", fill="x"))):
            if sh is None:
                part.pack_forget()
            elif not part.winfo_manager():
                part.pack(after=prev, **kw)
            prev = part
        self.sync_basics(sh)
        self.sync_steps(i, sh)
        self.sync_hidden(i, sh)
        hz = bool(sh is not None and sh.get("hz"))
        if hz != bool(self.hz_button.winfo_manager()):
            if hz:
                self.hz_button.pack(side="left", padx=(6, 0))
            else:
                self.hz_button.pack_forget()
        self.add.state(["!disabled" if sh is not None else "disabled"])

    def put(self, key, text):
        """A box's text set by the window (not while it is being typed in: that waits for Enter / leaving)."""
        var, e = self.vars[key], self.entries[key]
        if self.focus_get() is e and var.get() != text:
            return
        self.app._loading = True
        try:
            var.set(text)
        finally:
            self.app._loading = False
        e.config(style="TEntry")
        good(e)

    def place_of(self, sh):
        """(first tick, length in ticks, lowest key, highest key) of sh's notes as made (remembered ones only: never
        made here), or None (none known yet / no notes)."""
        got = self.app.notes_known(sh)
        if got is None:
            return None
        notes = np.asarray(got[0])
        if not len(notes):
            return None
        a, z = int(notes[:, 0].min()), int(notes[:, 1].max())
        return a, z - a, int(notes[:, 2].min()), int(notes[:, 2].max())

    def sync_basics(self, sh):
        app = self.app
        for w in self.basics.winfo_children():
            if sh is None and w.winfo_manager():
                w.grid_remove()
        if sh is None:
            return
        for name in self.heads:
            for w in self.heads[name]:
                w.grid()
        self.kind.config(text=kind_text(sh))
        self.name.config(text=app.shape_label(sh))
        self.show_part("kind", True)
        self.show_part("name", True)
        made = made_text(sh)
        self.made.config(text=made)
        self.show_part("made", bool(made))
        got = self.place_of(sh)
        for key, fr, v in zip(("start", "length", "low", "high"), (self.start, self.length, self.low, self.high),
                              got or ("", "", "", "")):
            self.show_part(key, True)
            self.put(key, fmt(v) if got else "")
            state = "normal" if got and not (key in ("length", "high") and sh.get("hz")) else "disabled"
            fr.boxes[0].config(state=state)
        if got:
            from roll.roll_shared import note_name
            self.low.word.config(text=note_name(got[2]))
            self.high.word.config(text=note_name(got[3]))
        custom = sh.get("kind") == "custom" and "notes" not in sh and "picture" not in sh
        self.show_part("inside", custom)
        if custom:
            self.inside.config(text=fill_text(sh))
        spam = custom and sh.get("fill") in SPAM_FILLS and not sh.get("hz")
        self.show_part("gate", spam)
        if spam:
            self.put("gate", fmt(gate_ticks(sh["gate"], app.ppq)))
        self.show_part("vel0", True)
        self.put("vel0", fmt(sh.get("vel0", 100)))
        self.put("vel1", fmt(sh.get("vel1", 100)))
        self.show_part("vel_note", bool(sh.get("vel_env")))
        self.colours.config(text=colours_text(sh))
        self.show_part("colours", True)
        self.channel.config(text=channel_text(app, sh))
        self.show_part("channel", True)

    # ---- the steps

    def sync_steps(self, i, sh):
        tags = S.order_of(sh) if sh is not None else []
        hidden = set(self.hidden_tags(i, sh))
        names = tuple(tag_name(sh, t) for t in tags) if sh is not None else ()
        offs = tuple(S.is_off(sh, t) for t in tags) if sh is not None else ()
        shown = (id(sh), tuple(tags), names, offs, tuple(sorted(hidden)), self.fixed_lines(sh))
        if shown != self.shown:
            self.shown = shown
            self.build_fixed(sh)
            self.build(sh, tags, names, offs, hidden)
        for r in self.rows:
            if r["box"] is not None:
                self.put_number(r, sh)

    def fixed_lines(self, sh):
        """The fixed steps at the top (they can't move: every step works on them): the notes as drawn, Single
        channel's own overlap fix."""
        if sh is None:
            return ()
        out = ["props.fixed_drawn"]
        if self.app.channel_mode.get() == "single" and not sh.get("hz"):
            out.append("props.fixed_single")
        return tuple(out)

    def build_fixed(self, sh):
        for w in self.fixed.winfo_children():
            w.destroy()
        for key in self.fixed_lines(sh):
            lb = ttk.Label(self.fixed, text=tr(key), foreground=look.LABEL_OFF)
            lb.pack(anchor="w", padx=(20, 0), pady=1)
            Tooltip(lb, tr(key + "_tip"))

    def make_row(self, p):
        """Line p's widgets, made once and kept (made again at every selection, 40 lines took ~0.4 s: speed_props)."""
        fr = ttk.Frame(self.list)
        grip = ttk.Label(fr, text="≡", foreground=look.HINT, cursor="fleur", padding=(0, 0, 4, 0))
        grip.pack(side="left")
        Tooltip(grip, tr("props.drag_tip"))
        var = tk.BooleanVar()
        tick = ttk.Checkbutton(fr, variable=var, command=lambda: self.on_tick(p))
        tick.pack(side="left")
        lb = ttk.Label(fr, text="")
        lb.pack(side="left")
        x = ttk.Label(fr, text="×", foreground=look.CLOSE, cursor="hand2", padding=(8, 0, 2, 0))
        x.pack(side="right")
        x.bind("<Enter>", lambda e: x.config(foreground=look.CLOSE_HOT))
        x.bind("<Leave>", lambda e: x.config(foreground=look.CLOSE))
        x.bind("<ButtonRelease-1>", lambda e: self.on_remove(e, p))
        Tooltip(x, tr("props.remove_tip"))
        word = ttk.Label(fr, text="", foreground=look.HINT, padding=(4, 0, 0, 0),
                         width=self.unit_width())  # (every box in one column, whatever its unit)
        boxvar = tk.StringVar()
        entry = ttk.Entry(fr, textvariable=boxvar, width=7)
        entry.bind("<Return>", lambda e: self.on_number(p))
        leave_box(self.app, entry, boxvar, lambda left=False: self.on_number(p, left and entry))
        for w in (fr, grip, lb):
            w.bind("<ButtonPress-1>", lambda e: self.on_press(e, p))
            w.bind("<B1-Motion>", self.on_motion)
            w.bind("<ButtonRelease-1>", self.on_release)
        return {"tag": None, "frame": fr, "grip": grip, "name": lb, "tick": tick, "var": var, "box": None,
                "entry": entry, "boxvar": boxvar, "word": word, "x": x, "tick_tip": Tooltip(tick, ""),
                "name_tip": Tooltip(lb, ""), "box_tip": Tooltip(entry, "")}

    def build(self, sh, tags, names, offs, hidden):
        """The lines shown: the kept ones filled in again, more made when needed, the rest taken out."""
        self.empty.pack_forget()
        n = len(tags) if sh is not None else 0
        for r in self.pool[n:]:
            if r["frame"].winfo_manager():
                r["frame"].pack_forget()
        self.rows = []
        if sh is None:
            self.scroll.fit()
            return
        if not tags:
            self.empty.pack(anchor="w", padx=(20, 0))
        clumps = clumps_hidden(sh)
        for p, (tag, name, off) in enumerate(zip(tags, names, offs)):
            if p == len(self.pool):
                self.pool.append(self.make_row(p))
            r = self.pool[p]
            if not r["frame"].winfo_manager():
                r["frame"].pack(anchor="w", fill="x", pady=1)
            r["tag"] = tag
            r["var"].set(not off)
            can_off = tag[0] != S.FX or is_page((sh.get("fx") or [])[tag[1]])
            r["tick"].state(["!disabled" if can_off else "disabled"])
            r["tick_tip"].text = tr("props.tick_tip" if can_off else "props.tick_never")
            grey = off or tag in hidden or tag in clumps
            opens = self.opens(sh, tag)
            r["name"].config(text=name, foreground=look.LABEL_OFF if grey else "", cursor="hand2" if opens else "fleur")
            r["name_tip"].text = (tr("props.hidden_tip") if tag in hidden else tr("props.clumps_hidden")
                                  if tag in clumps else tr("props.open_tip") if opens else tr("props.no_window_tip"))
            spec = number_spec(sh, tag)
            word, entry = r["word"], r["entry"]
            if spec is None:
                r["box"] = None
                if entry.winfo_manager():
                    entry.pack_forget()
                    word.pack_forget()
            else:
                r["box"] = entry
                word.config(text=tr(spec[0]))
                r["box_tip"].text = tr("props.number_tip." + tag[0])
                if not entry.winfo_manager():
                    word.pack(side="right")
                    entry.pack(side="right", padx=(10, 0))
            self.rows.append(r)
        self.update_idletasks()
        s = self.app.scale  # (a short screen: the list shorter, so Close stays on it)
        rest = self.box.winfo_reqheight() - self.scroll.winfo_reqheight()
        self.scroll.most = min(int(LIST_MOST * s), max(int(100 * s), self.winfo_screenheight() - rest - int(120 * s)))
        self.scroll.fit()

    def unit_width(self):
        """The unit words' width in letters: the longest one's (any language)."""
        return max(len(tr(k)) for k in {spec[0] for spec in NUMBERS.values()} | {"props.unit_pct"})

    def put_number(self, r, sh):
        v = number_of(sh, r["tag"], self.app.ppq)
        text = "" if v is None else fmt(round(v, 3))
        if self.focus_get() is r["box"] and r["boxvar"].get() != text:
            return
        self.app._loading = True
        try:
            r["boxvar"].set(text)
        finally:
            self.app._loading = False
        good(r["box"])

    def hidden_tags(self, i, sh):
        """The steps of the shown shape hidden while one is edited now (stage C), in order."""
        if sh is None:
            return []
        t = self.app.edit_tag(sh, i)
        return S.hidden_after(sh, t) if t is not None else []

    def sync_hidden(self, i, sh):
        tags = self.hidden_tags(i, sh)
        text = tr("edit.hidden", names=names_line([tag_name(sh, t) for t in tags])) if tags else ""
        if text != self.hidden_text.cget("text"):
            self.hidden_text.config(text=text)
        if bool(text) != bool(self.hidden.winfo_manager()):
            if text:
                self.hidden.pack(anchor="w", fill="x", pady=(6, 0), before=self.buttons)
            else:
                self.hidden.pack_forget()

    def preview_all(self):
        self.app.colour_edit_leave()
        self.sync()

    # ---- drag to reorder, click to open

    def on_press(self, e, p):
        self.press = {"row": p, "y": e.y_root, "moved": False, "to": p}

    def place_at(self, y_root):
        for p, r in enumerate(self.rows):
            f = r["frame"]
            if y_root < f.winfo_rooty() + f.winfo_height() / 2:
                return p
        return len(self.rows)

    def on_motion(self, e):
        pr = self.press
        if pr is None:
            return
        if not pr["moved"] and abs(e.y_root - pr["y"]) < DRAG_PX:
            return
        pr["moved"] = True
        slot = self.place_at(e.y_root)
        pr["to"] = slot - (slot > pr["row"])
        if self.rows:
            ref = self.rows[min(slot, len(self.rows) - 1)]["frame"]
            y = ref.winfo_y() - 1 if slot < len(self.rows) else ref.winfo_y() + ref.winfo_height()
            self.drop_line.place(x=0, y=y, relwidth=1.0)
            self.drop_line.lift()

    def on_release(self, e):
        pr, self.press = self.press, None
        self.drop_line.place_forget()
        if pr is None:
            return
        if pr["moved"]:
            if pr["to"] != pr["row"]:
                self.move(pr["row"], pr["to"])
        else:
            self.open_step(pr["row"])

    def opens(self, sh, tag):
        """Does step tag have a window of its own (a click on its name opens it)?"""
        kind, n = tag
        if kind == S.FX:
            fx = sh.get("fx") or []
            return n < len(fx) and is_page(fx[n]) and fx[n]["tool"] in PAGE_WINDOWS
        return kind in ("mode", "stir", "warp", "liquify", "sustain", "extend", "subtract")

    def open_step(self, p):
        i, sh = self.shape()
        if sh is None or not 0 <= p < len(self.rows):
            return
        kind, n = self.rows[p]["tag"]
        app = self.app
        if kind == S.FX:
            fx = sh.get("fx") or []
            if n < len(fx) and is_page(fx[n]):
                open_page(app, sh, sum(1 for st in fx[:n] if is_page(st)))
        elif kind == "mode":
            app.open_shares()
        elif kind == "stir":
            app.open_stir()
            if app.stir_window:
                app.stir_window.show_tab(n)
        elif kind == "warp":
            app.open_warp()
        elif kind == "liquify":
            app.open_liquify()
        elif kind in ("sustain", "extend", "subtract"):
            mod = importlib.import_module(f"window.{kind}_window")
            getattr(mod, "open_" + kind)(app)

    def begin(self, name):
        """Before a change from this window: typing elsewhere taken, one undo step."""
        app = self.app
        app.commit_typing()
        if app.notes_late:
            app.shapes_changed(now=True)
        app.push_undo(name=name)

    def done(self, step=None):
        """After a change: the notes, the panel and the windows follow (step: the step it edited, stage C)."""
        app = self.app
        app.colour_edit_set(step)
        app.shapes_changed()
        app.sync_recolour()
        app.sync_panel()
        app.schedule_autosave()
        self.sync()

    def move(self, a, b):
        """Step a of the shown shape's order to place b (one undo step); an order it can't keep = a line says why."""
        i, sh = self.shape()
        if sh is None:
            return
        new = S.moved(sh, a, b)
        if new is None:
            return self.say(tr("props.cant_move"))
        self.say("")
        self.editing = None
        self.begin(tr("props.undo_move"))
        sh.clear()
        sh.update(new)
        self.done()

    def say(self, text):
        self.status.config(text=text)
        if bool(text) != bool(self.status.winfo_manager()):
            if text:
                self.status.pack(anchor="w", fill="x", pady=(6, 0), before=self.buttons)
            else:
                self.status.pack_forget()

    def on_tick(self, p):
        i, sh = self.shape()
        if sh is None or not 0 <= p < len(self.rows):
            return
        r = self.rows[p]
        off = not r["var"].get()
        if S.is_off(sh, r["tag"]) == off:
            return
        self.editing = None
        self.begin(tr("props.undo_off" if off else "props.undo_on"))
        S.with_off(sh, r["tag"], off)
        self.done()

    def on_remove(self, e, p):
        if not (0 <= e.x < e.widget.winfo_width() and 0 <= e.y < e.widget.winfo_height()):
            return  # (let go off the ×, like a button)
        i, sh = self.shape()
        if sh is None or not 0 <= p < len(self.rows):
            return
        tag = self.rows[p]["tag"]
        self.editing = None
        self.begin(tr("props.undo_remove", name=tag_name(sh, tag)))
        removed(self.app, sh, tag)
        self.done()

    def on_number(self, p, left=None):
        """A step's main number typed (Enter / the box left): that step is being edited now (stage C)."""
        if self.app._loading or not 0 <= p < len(self.rows):
            return
        r = self.rows[p]
        if left is not None and unchanged(left):
            return
        i, sh = self.shape()
        if sh is None:
            return
        tag = r["tag"]
        spec = number_spec(sh, tag)
        text = r["boxvar"].get().strip().rstrip("%°")
        try:
            v = None if not text and tag[0] == "sustain" else float(calc(text))
            if v is not None and not spec[1] <= v <= spec[2]:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            return bad(r["box"])
        good(r["box"])
        old = number_of(sh, tag, self.app.ppq)
        if old is not None and v is not None and abs(old - v) < 1e-9 or old is None and v is None:
            return
        self.editing = tag[0]
        if tag[0] in S.COLOURS:
            rc = clean_recolour(sh.get("recolour"))
            new = ordered(rc, clean_recolour(with_number(sh, tag, v, self.app.ppq)["recolour"]))
            if new == rc:
                return
            self.begin(tr("props.undo_number", name=tag_name(sh, tag)))
            sh["recolour"] = new
            self.done(edited_step(rc, new) or tag)
        else:
            self.begin(tr("props.undo_number", name=tag_name(sh, tag)))
            sh[tag[0]] = with_number(sh, tag, v, self.app.ppq)[tag[0]]
            self.done(tag)

    # ---- the basics typed

    def number_in(self, key, lo, hi):
        """The number typed in basics box key, or None (wrong: back to the last good one)."""
        var, e = self.vars[key], self.entries[key]
        try:
            v = float(calc(var.get()))
            if not lo <= v <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return None
        good(e)
        return v

    def on_place(self, key, left=None):
        """Start / Length / Lowest key / Highest key typed: the shape moved or sized (one undo step)."""
        app = self.app
        if app._loading or left is not None and unchanged(left):
            return
        i, sh = self.shape()
        got = self.place_of(sh) if sh is not None else None
        if got is None:
            return
        a, n, lo, hi = got
        t_lo, p_lo, p_hi = app.point_limits()
        ppq = app.ppq
        if key == "start":
            v = self.number_in(key, t_lo, 10 ** 9)
            change = None if v is None or round(v) == a else ("move", (round(v) - a) / ppq, 0)
        elif key == "low":
            v = self.number_in(key, p_lo, p_hi)
            change = None if v is None or round(v) == lo or round(v) + hi - lo > p_hi else ("move", 0, round(v) - lo)
        elif key == "length":
            v = self.number_in(key, 1, 10 ** 9)
            change = None if v is None or round(v) == n or not n else ("size", a / ppq, round(v) / n, None, 1.0)
        else:
            v = self.number_in(key, p_lo, p_hi)
            if v is None or round(v) == hi or round(v) < lo or hi == lo:
                change = None
            else:
                change = ("size", 0.0, 1.0, lo - 0.5, (round(v) - lo + 1) / (hi - lo + 1))
        if change is None:
            return self.sync()
        self.begin(tr("props.undo_place"))
        if change[0] == "move":
            db, dp = change[1], change[2]
            sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]
        else:
            _, ab, kx, ap, ky = change
            ap = lo - 0.5 if ap is None else ap
            new = app.stretched(sh, ab, kx, ap, ky)
            sh.clear()
            sh.update(new)
        app.roll.box_kept = None  # (a kept Select box was round the old place)
        self.done()

    def on_gate(self, key, left=None):
        app = self.app
        if app._loading or left is not None and unchanged(left):
            return
        i, sh = self.shape()
        if sh is None or sh.get("kind") != "custom" or sh.get("fill") not in SPAM_FILLS or sh.get("hz"):
            return
        v = self.number_in(key, 0.5, 10 ** 7)
        if v is None:
            return
        beats = int(v + 0.5) / app.ppq
        if abs(sh["gate"] - beats) < 1e-12 and not sh.get("range"):
            return self.sync()
        if not app.confirm_big([dict(sh, gate=beats)]):
            return self.sync()
        self.begin(tr("panel_custom.spam_gate"))
        sh["gate"] = beats
        if sh.get("range"):  # (a gate typed = one flat gate again, kept for the Range window: like the panel)
            sh["range_kept"] = sh.pop("range")
        self.done()

    def on_velocity(self, key, left=None):
        app = self.app
        if app._loading or left is not None and unchanged(left):
            return
        i, sh = self.shape()
        if sh is None:
            return
        v = self.number_in(key, 1, 127)
        if v is None:
            return
        v = int(round(v))
        if sh.get(key) == v and not sh.get("vel_env"):
            return
        from notes.fx import with_velocity
        self.begin(tr("app.velocity"))
        sh[key] = v
        sh.pop("vel_env", None)
        sh.pop("own_vel", None)
        if sh.get("fx"):
            sh["fx"] = with_velocity(sh["fx"], [[0.0, sh["vel0"]], [1.0, sh["vel1"]]])
        self.done()

    # ---- Add ▾ / Hz bass settings…

    def fill_add(self):
        m = self.add_menu
        m.delete(0, "end")
        app = self.app
        i, sh = self.shape()
        if sh is None:
            return
        tools = bool(app.note_tool_sels())
        for tool in ADD_TOOLS:
            mod, fn, _ = PAGE_WINDOWS[tool]
            m.add_command(label=tr("props.add_" + tool), state="normal" if tools else "disabled",
                          command=lambda mod=mod, fn=fn: getattr(importlib.import_module(mod), fn)(app))
        m.add_command(label=tr("roll_menu.glue"), command=app.glue_selected, state="normal" if tools else "disabled")
        m.add_separator()
        for kind in ("sustain", "extend", "subtract"):
            mod = importlib.import_module(f"window.{kind}_window")
            ok = bool(mod.sustain_targets(app) if kind == "sustain" else
                      importlib.import_module("window.subtract_window").subtract_targets(app))
            m.add_command(label=tr(kind + ".menu"), state="normal" if ok else "disabled",
                          command=lambda mod=mod, kind=kind: getattr(mod, "open_" + kind)(app))
        gf = app.gate_flip_state()
        if gf:
            m.add_command(label=tr("app.gate_flip"), command=app.gate_flip,
                          state="normal" if gf[0] and not gf[1] else "disabled")
        if not app.recolour_targets():
            return
        m.add_separator()
        rc = clean_recolour(sh.get("recolour"))
        has = rc_order(rc) if rc else []
        for mode in MODES:  # (one Shuffle / Split a shape: greyed while it has one)
            m.add_command(label=tr("recolour." + mode), command=lambda mode=mode: self.add_mode(mode),
                          state="disabled" if "mode" in has else "normal")
        m.add_command(label=tr("stir.button"), command=self.add_stir)
        m.add_command(label=tr("warp.button"), command=app.open_warp)
        m.add_command(label=tr("liquify.button"), command=app.open_liquify)
        last = rc_tags(shape_order(sh))[-1:]
        twice = bool(last) and last[0][0] == "turned"
        m.add_command(label=tr("order.add_reverse_last" if twice else "join_split.reverse_tracks"),
                      command=app.reverse_tracks,
                      state="normal" if app.reverse_targets() and not twice else "disabled")

    def add_mode(self, mode):
        self.editing = None
        self.app.change_recolour(lambda old, t: old if old and old["mode"] in MODES else
                                 dict(old or {"seed": new_seed()}, mode=mode, amount=START_AMOUNT[mode]),
                                 tr("order.undo_add"))
        self.sync()

    def add_stir(self):
        """A new Stir tab at the end (the Stir window's "+": saved once one of its settings changes)."""
        app = self.app
        app.open_stir()
        w = app.stir_window
        i, sh = self.shape()
        if w and not w.fresh and sh is not None and rc_order(clean_recolour(sh.get("recolour"))).count("stir"):
            w.on_add()

    def open_hz(self):
        from window.hz_window import open_hz
        open_hz(self.app)

    def close(self):
        app = self.app
        app.properties_window = None
        if self.editing is not None and not app.edit_window_open(self.editing):
            app.colour_edit_leave(self.editing)  # (its number was edited here: the later steps come back)
        self.editing = None
        self.destroy()
        app.roll.focus_set()


# ---- the words and numbers it shows

def kind_text(sh):
    if sh.get("hz"):
        return tr("props.kind_hz")
    if "picture" in sh:
        return tr("props.kind_picture")
    if "notes" in sh:
        return tr("props.kind_notes")
    if sh.get("text"):
        return tr("props.kind_text")
    from notes.engine import KINDS
    return KINDS.get(sh.get("kind"), sh.get("kind") or "?")


MADE_BY = ("plain", "fill_gaps", "fold", "line_flip", "split_lines", "mask")


def made_text(sh):
    """"<command>, from <shape>" for a shape a one-time command made (sh["made_by"]), or ""."""
    mb = sh.get("made_by")
    if not (isinstance(mb, list) and len(mb) == 2 and mb[0] in MADE_BY):
        return ""
    return tr("props.made_" + mb[0], name=mb[1])


def fill_text(sh):
    from window.panel_custom import FILL_CHOICES
    from window.panel_nested import nested_on
    from window.panel_ripples import ripples_on
    from window.panel_tiles import tiles_on
    shown = ("nested" if nested_on(sh) else "tiles" if tiles_on(sh) else "ripples" if ripples_on(sh)
             else sh.get("fill"))
    return next((t for v, t, _ in FILL_CHOICES if v == shown), shown or "?")


def colours_text(sh):
    cy = sh.get("cycle")
    out = tr("props.colours_cycle", n=cy.get("n", 1), by=tr("colours." + cy["by"])) if cy and cy.get("by") \
        else tr("colours.off")
    areas = {a[2] for a in sh.get("areas") or () if len(a) > 2 and a[2]}
    if areas:
        out += tr("props.area_colours", n=len(areas))
    return out


def channel_text(app, sh):
    if sh.get("into"):
        return tr("props.channel_into")
    if sh.get("own_slots"):
        return tr("props.channel_own")
    mode = app.channel_mode.get()
    return tr({"auto": "app.multi_channel", "raw": "app.as_drawn"}.get(mode, "app.single_channel"))


def clumps_hidden(sh):
    """The colour steps of sh showing nothing because Split: Clumps runs after them (hunt 11-10 #5: greyed, a tip)."""
    from notes.recolour import hidden_by_clumps
    rc = clean_recolour(sh.get("recolour")) if not sh.get("hz") else None
    if not rc:
        return set()
    o = shape_order(sh)
    tags = rc_tags(o)
    return {tags[p] for p in hidden_by_clumps(rc, o)}


def number_spec(sh, tag):
    """(unit word key, lowest, highest) of step tag's main number box, or None (it has none)."""
    kind = tag[0]
    if kind == "mode":
        rc = clean_recolour(sh.get("recolour")) or {}
        return NUMBERS[kind] if rc.get("mode") in MODES else None
    if kind == "extend" and (sh.get("extend") or {}).get("unit") == "pct":
        return "props.unit_pct", 0.0, 1000.0
    return NUMBERS.get(kind)


def number_of(sh, tag, ppq):
    """Step tag's main number as its box shows it (ticks for lengths), or None (empty)."""
    kind, n = tag
    if kind in S.COLOURS:
        rc = clean_recolour(sh.get("recolour")) or {}
        if kind == "mode":
            return rc.get("amount")
        if kind == "stir":
            st = stirs_of(rc)
            return st[n].get("turn", 0.0) if n < len(st) else None
        if kind in ("warp", "liquify"):
            d = rc.get(kind) or {}
            return float(d.get("strength", 100.0))
        return None
    cfg = sh.get(kind)
    if not isinstance(cfg, dict):
        return None
    if kind == "sustain":
        return cfg["most"] * ppq if cfg.get("most") else None
    if kind == "extend":
        return cfg.get("by", 0.0) if cfg.get("unit") == "pct" else cfg.get("by", 0.0) * ppq
    if kind == "subtract":
        return cfg.get("by", 0.0) * ppq
    return None


def with_number(sh, tag, v, ppq):
    """A copy of sh (only the part step tag lives in) with that step's main number v (as its box shows it)."""
    kind, n = tag
    out = dict(sh)
    if kind in S.COLOURS:
        rc = dict(clean_recolour(sh.get("recolour")) or {})
        if kind == "mode":
            rc["amount"] = round(v, 3)
        elif kind == "stir":
            stack = [dict(st) for st in stirs_of(rc)]
            stack[n]["turn"] = round(v, 3)
            rc.pop("stir_held", None)
            rc["stir"] = stack if len(stack) > 1 else stack[0]
        else:
            rc[kind] = dict(rc.get(kind) or {}, strength=round(v, 3))
        out["recolour"] = rc
        return out
    cfg = dict(sh.get(kind) or {})
    if kind == "sustain":
        if v:
            cfg["most"] = v / ppq
        else:
            cfg.pop("most", None)
    elif kind == "extend":
        cfg["by"] = v if cfg.get("unit") == "pct" else v / ppq
    else:
        cfg["by"] = v / ppq
    out[kind] = cfg
    return out


def removed(app, sh, tag):
    """Step tag taken off shape sh (the ×; the caller made the undo step)."""
    kind, n = tag
    from window.tool_window import TOOL_WINDOWS, ToolWindow
    for attr in TOOL_WINDOWS:  # (a note tool window's try kept first: it follows the pages there are now)
        w = getattr(app, attr, None)
        if w:
            w.settle()
    if kind == S.FX:
        fx = list(sh.get("fx") or [])
        if n >= len(fx):
            return
        if is_page(fx[n]):
            j = sum(1 for st in fx[:n] if is_page(st))
            rc = clean_recolour(sh.get("recolour"))
            if rc:
                got = page_removed(rc, j, len(pages(fx)))
                if got:
                    sh["recolour"] = got
        ToolWindow.put_fx(sh, fx[:n] + fx[n + 1:])
    elif kind == S.GLUE:
        from notes.fx import tidy_glue
        sh.pop("glue", None)
        tidy_glue(sh)
    elif kind == S.GATE_FLIP:
        from notes.gateflip import turn_velocities
        sh.pop("gate_flip", None)
        turn_velocities(sh)
    elif kind in S.LENGTHS:
        sh.pop(kind, None)
    else:
        colour_removed(sh, tag)
    if kind != S.FX and sh.get("off"):  # (its off mark goes; later ones of its kind move up one)
        off = [[k, m - (k == kind and m > n)] for k, m in sh["off"] if [k, m] != [kind, n]]
        if off:
            sh["off"] = off
        else:
            sh.pop("off", None)


def colour_removed(sh, tag):
    """Colour step tag taken off sh. A Reverse on notes whose stored colours it swapped: once no Recolour keeps
    that swap, the notes get their colours back (else the Reverse would stay, hidden in the notes)."""
    o = shape_order(sh)
    tags = rc_tags(o)
    if tuple(tag) not in tags:
        return
    old = clean_recolour(sh.get("recolour"))
    new = shape_step_removed(sh, tags.index(tuple(tag)))
    if old is None:
        return
    tw = old.get("turned")
    if tw and "notes" in sh and not (new or {}).get("turned"):
        rows = unpack_notes(sh["notes"]).copy()
        low = rows[:, 4] % ROUND
        rows[:, 4] = rows[:, 4] - low + turned_map(tw, back=True)(low)
        sh["notes"] = pack_notes(np.asarray(rows, np.int64))
    if new:
        sh["recolour"] = new
    else:
        sh.pop("recolour", None)


def open_page(app, sh, n):
    """Shape sh's n-th note tool page: its tool's window opened on that page's tab."""
    fx = sh.get("fx") or []
    at = [k for k, st in enumerate(fx) if is_page(st)]
    if n >= len(at) or fx[at[n]]["tool"] not in PAGE_WINDOWS:
        return
    mod, fn, attr = PAGE_WINDOWS[fx[at[n]]["tool"]]
    getattr(importlib.import_module(mod), fn)(app)
    w = getattr(app, attr, None)
    if w and w.targets and w.same() and w.app.shapes[w.targets[0]] is sh:
        w.pick_page(at[n])
