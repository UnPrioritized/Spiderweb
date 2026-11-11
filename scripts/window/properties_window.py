"""The Properties window (PLAN.md "Properties", user 2026-11-11): Properties… = the LAST item of a shape's right-click
menu (roll_menu.properties_item), Alt+Enter, and the Properties… button in every step's window (edit_line.EditLine).
It replaced the Effect order window. One window that follows the selection and stays open beside the piano roll.

It shows the selected shape (several selected: listed on the left like the Settings window's pages, the ones picked
there shown together: differing values empty / "Mixed", steps only some have marked "2 of 3"; stage E) in the Settings
window's look (settings_window.Form): its basics at the top (kind / name / "Made by", place and size, gate and
velocity, colours and channel), then ONE ordered list of every step done to its notes (notes/steps.py order_of):
the fixed ones greyed at the top (the notes as drawn; Single channel's own overlap fix) and each movable one a line
with a drag grip, an on / off tick, its name (click = its own window, on that tab), its main number in a box and ×.
Every change is one undo step; typing a step's number = editing that step (the steps after it are hidden until the
user leaves it: PLAN.md stage C, App.colour_edit). Add ▾ opens the existing tool windows."""

import copy
import importlib
import tkinter as tk
import weakref
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes import steps as S
from notes.custom import ROUND, SPAM_FILLS, gate_ticks, pack_notes, unpack_notes
from notes.fx import is_page, pages
from notes.recolour import (KEEP, MODES, PAGE, clean_recolour, edited_step, new_seed, ordered, order_of as rc_order,
                            page_removed, shape_order, shape_step_removed, stirs_of, tags_of as rc_tags, turned_map)
from window import look
from window.edit_line import names_line, tag_name
from window.settings_window import ScrollPage
from window.widgets import ListTip, ShortLabel, Tooltip, bad, good, leave_box, remember_place, unchanged

LIST_CHARS = 22  # the shape list's width in letters (longer names: cut, the whole name in a tip)
SHORT_CHARS = 32  # a folded basics group's grey line asks this many letters' room (the window a bit wider: user 2026-11-12 OK)
TIP_MOST = 12  # a Mixed value's tip lists this many shapes' values at most
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
NUDGE_SHAPES = 16  # Length / Highest key typed for at most this many shapes: nudged until the notes land (sized)
NUDGE_TRIES = 10  # notes made at most this many times a shape while nudging
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
        self.busy = False  # (a change from here is starting: its own boxes take nothing, begin)
        self.title(tr("props.title"))
        self.transient(app)
        self.resizable(False, False)
        s = app.scale
        self.listed = []  # (the shapes in the list on the left: the selection, stage E)
        self.picked = []  # (the ones shown: picked in that list; all of them at first)
        self.our_sels = None  # (a selection made here for Add ▾: the list stays as it was)
        self.keys_of = {}  # (each picked shape's steps as keys: step_keys)
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        side = self.side = ttk.Frame(outer)  # (several selected: like the Settings window's list, user 2026-11-11)
        head_lb = ttk.Label(side, text=tr("props.shapes_head"))
        head_lb.pack(anchor="w")
        Tooltip(head_lb, tr("props.list_tip"))
        lrow = ttk.Frame(side)
        lrow.pack(fill="both", expand=True, pady=(4, 0))
        self.shape_list = tk.Listbox(lrow, activestyle="none", exportselection=False, selectmode="extended",
                                     width=LIST_CHARS, height=8, highlightthickness=1,
                                     font=look.font(look.USUAL_SIZE))
        sb = ttk.Scrollbar(lrow, orient="vertical", command=self.shape_list.yview)
        self.shape_list.config(yscrollcommand=sb.set)
        self.shape_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.shape_list.bind("<<ListboxSelect>>", self.on_pick)
        ListTip(self.shape_list)
        self.like = ttk.Button(side, text=tr("props.like_this"), command=self.make_like)
        self.like.pack(anchor="w", fill="x", pady=(6, 0))
        self.like_tip = Tooltip(self.like, tr("props.like_this_tip"))
        box = self.box = ttk.Frame(outer)
        box.pack(side="right", fill="both", expand=True)
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
        tips = getattr(app, "tips", None)
        if tips is not None:
            tips.show("properties", parent=self, wait=True)  # (the first time: what it does, Help → Properties)

    # ---- the basics (settings_window.Form's look: a heading with a line, "Name:" + its control)

    def build_basics(self):
        f = self.basics
        self.r = 0
        self.parts = {}  # name -> the widgets of its row (shown / hidden by sync)
        self.vars, self.entries = {}, {}
        self.tips, self.tip_base = {}, {}  # (a value's / box's tip: its own text, + each shape's value when Mixed)

        self.group_of, self.want, self.shorts, self.arrows = {}, {}, {}, {}
        self.group = None

        def head(key, group):
            """A fold-away group's header (user 2026-11-12, the window was too tall): ▶ / ▼ + its name + (folded) a
            grey line of what it holds; a click anywhere on it folds / unfolds (remembered, autosave). Folded at
            first. The arrow in a spot of its own (▶ and ▼ differ in width: the name must not jump); the grey line
            cut with "…" + a tip if long, so it never makes the window wider."""
            hd = ttk.Frame(f, cursor="hand2")
            hd.grid(row=self.r, column=0, columnspan=2, sticky="ew", pady=(6 if self.r else 0, 0))
            arrow = ttk.Label(hd, width=2, cursor="hand2")
            arrow.pack(side="left")
            word = ttk.Label(hd, text=tr(key), cursor="hand2")
            word.pack(side="left")
            short = ShortLabel(hd, least=SHORT_CHARS, foreground=look.HINT, cursor="hand2")
            short.pack(side="left", fill="x", expand=True, padx=(8, 0))
            for w in (hd, arrow, word):
                Tooltip(w, tr("props.fold_tip"))
            for w in (hd, arrow, word, short):
                w.bind("<Button-1>", lambda e, g=group: self.on_fold(g))
            self.arrows[group], self.shorts[group] = arrow, short
            self.group = group
            sep = ttk.Separator(f)
            sep.grid(row=self.r + 1, column=0, columnspan=2, sticky="ew", pady=(3, 4))
            self.parts["sep_" + group] = [sep]
            self.group_of["sep_" + group] = group
            self.r += 2
            return [hd]

        def row(name, label, widget, tip=None):
            lb = ttk.Label(f, text=tr(label))
            lb.grid(row=self.r, column=0, sticky="w", padx=(0, 8), pady=2)
            widget.grid(row=self.r, column=1, sticky="w", pady=2)
            if tip:
                Tooltip(lb, tr(tip))
            if not isinstance(widget, ttk.Frame):
                self.tips[name] = Tooltip(widget, tr(tip) if tip else "")
                self.tip_base[name] = tr(tip) if tip else ""
            self.parts[name] = [lb, widget]
            self.group_of[name] = self.group
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
                self.tips[key] = Tooltip(e, tr(tip))
                self.tip_base[key] = tr(tip)
            word = ttk.Label(fr, text=tr(unit) if unit else "", foreground=look.HINT)
            word.pack(side="left", padx=(5, 0))
            row(name, label, fr, tip)
            fr.boxes, fr.word = boxes, word
            return fr

        self.heads = {"shape": head("props.head_shape", "shape")}
        self.kind = value("kind", "props.kind")
        self.name = value("name", "props.name")
        self.made = value("made", "props.made_by", "props.made_by_tip")
        self.heads["place"] = head("props.head_place", "place")
        self.start = number("start", "props.start", "unit.ticks", "props.start_tip", self.on_place)
        self.length = number("length", "props.length", "unit.ticks", "props.length_tip", self.on_place)
        self.low = number("low", "props.low", None, "props.low_tip", self.on_place)
        self.high = number("high", "props.high", None, "props.high_tip", self.on_place)
        self.heads["gate"] = head("props.head_gate", "gate")
        self.inside = value("inside", "props.inside", "props.inside_tip")
        self.gate = number("gate", "props.gate", "unit.ticks", "props.gate_tip", self.on_gate)
        self.vel = number("vel0", "props.velocity", None, "props.velocity_tip", self.on_velocity, two="vel1")
        self.vel_note = ttk.Label(f, text=tr("props.velocity_drawn"), foreground=look.HINT,
                                  wraplength=int(WRAP * self.app.scale), justify="left")
        self.vel_note.grid(row=self.r, column=1, sticky="w")
        self.parts["vel_note"] = [self.vel_note]
        self.group_of["vel_note"] = self.group
        self.r += 1
        self.heads["colours"] = head("props.head_colours", "colours")
        self.colours = value("colours", "props.colours", "props.colours_tip")
        self.channel = value("channel", "props.channel", "props.channel_tip")
        for name in self.parts:
            self.want[name] = name.startswith("sep_")

    def show_part(self, name, on):
        """Row name wanted or not (what the picked shapes have); shown only while its group is unfolded too."""
        self.want[name] = on
        on = on and self.group_of.get(name) in self.app.props_open
        for w in self.parts[name]:
            if on and not w.winfo_manager():
                w.grid()
            elif not on and w.winfo_manager():
                w.grid_remove()

    def on_fold(self, group):
        """A basics group's header clicked: folded / unfolded (a number typed in another box stays typed: nothing
        here touches the boxes; folding the group a typed box is in takes the number in first, like a new pick)."""
        app = self.app
        if group in app.props_open:
            app.commit_typing()
        app.props_open ^= {group}
        self.sync_folds()

    def sync_folds(self):
        """Arrows, rows and the grey one-line summaries as each group is folded / unfolded now."""
        app = self.app
        for group, arrow in self.arrows.items():
            shown = group in app.props_open
            if arrow.cget("text") != ("▼" if shown else "▶"):
                arrow.config(text="▼" if shown else "▶")
            short = self.shorts[group]
            if shown == bool(short.winfo_manager()):
                if shown:
                    short.pack_forget()
                else:
                    short.pack(side="left", fill="x", expand=True, padx=(8, 0))
        for name in self.parts:
            self.show_part(name, self.want.get(name, False))

    # ---- what it shows

    def shape(self):
        """(index, shape) of the first shape shown, or (None, None)."""
        got = self.picks()
        return got[0] if got else (None, None)

    def picks(self):
        """[(index, shape)] shown together (picked in the list on the left; one selected: it)."""
        shapes = self.app.shapes
        return [(i, shapes[i]) for i in self.picked if i < len(shapes)]

    def follow(self):
        """The list on the left follows the selection (a selection made here for Add ▾ keeps it); what is picked in
        it stays picked while it is still there, else every shape in it is shown."""
        app = self.app
        sels = sorted(i for i in app.sels if i < len(app.shapes))
        if self.our_sels is not None and frozenset(app.sels) == self.our_sels:
            listed = [i for i in self.listed if i < len(app.shapes)]
        else:
            self.our_sels = None
            listed = sels
        if listed != self.listed:
            self.listed = listed
            self.picked = listed[:]  # (my pick: several selected = all of them shown together)
        else:
            there = set(listed)
            keep = [i for i in self.picked if i in there]
            self.picked = keep or listed[:]
        self.sync_list()

    def sync_list(self):
        """The list on the left: shown while 2+ shapes are selected, its names and picks as they are now."""
        app = self.app
        many = len(self.listed) > 1
        if many != bool(self.side.winfo_manager()):
            if many:
                self.side.pack(side="left", fill="y", padx=(0, 14), before=self.box)
            else:
                self.side.pack_forget()
        if not many:
            return
        lb = self.shape_list
        names = tuple(tr("props.list_row", n=i + 1, name=app.shape_label(app.shapes[i])) for i in self.listed)
        if names != getattr(self, "_names", None):
            self._names = names
            lb.delete(0, "end")
            lb.insert("end", *names)
        place = {i: k for k, i in enumerate(self.listed)}
        want = sorted(place[i] for i in self.picked if i in place)
        if list(lb.curselection()) != want:
            lb.selection_clear(0, "end")
            for k in want:
                lb.selection_set(k)
            if want:
                lb.see(want[0])
        one = len(self.picked) == 1
        self.like.state(["!disabled" if one else "disabled"])
        self.like_tip.text = tr("props.like_this_tip" if one else "props.like_this_one")

    def on_pick(self, e=None):
        got = [self.listed[k] for k in self.shape_list.curselection() if k < len(self.listed)]
        if not got or got == self.picked:
            return
        self.app.commit_typing()
        self.picked = got
        self.shown = None
        self.sync(follow=False)

    def after_notes(self):
        """The notes were made again (App.shapes_changed's take-in): the numbers that come from them."""
        if self.winfo_exists():
            self.sync()

    def sync(self, follow=True):
        if not self.winfo_exists():
            return
        app = self.app
        if follow:
            self.follow()
        else:
            self.sync_list()
        picks = self.picks()
        if self.status.winfo_manager() and tuple(id(s) for _, s in picks) != getattr(self, "said_for", None):
            self.say("")  # (other shapes shown: the line was about the ones before)
        i, sh = picks[0] if picks else (None, None)
        n = len(picks)
        # (one shape: no title line, the folded Shape line names it (user 2026-11-12: the window was too tall))
        self.what.config(text=tr("props.nothing") if sh is None else "" if n == 1 else tr("props.shapes", n=n))
        more = tr("props.several", n=n) if n > 1 else ""
        if more != self.more.cget("text"):
            self.more.config(text=more)
        # (nothing selected: only the line saying so; a frame whose rows all go keeps its size in Tk, so whole parts
        # are taken out; each shown part packed before the next shown one, in this order)
        order = ((self.what, n != 1, dict(anchor="w", fill="x")), (self.more, bool(more), dict(anchor="w", fill="x")),
                 (self.basics, sh is not None, dict(anchor="w", fill="x", pady=(6 if n != 1 else 0, 0))),
                 (self.steps_head, sh is not None, dict(anchor="w", fill="x", pady=(10, 0))),
                 (self.steps_line, sh is not None, dict(fill="x", pady=(3, 6))),
                 (self.fixed, sh is not None, dict(anchor="w", fill="x")),
                 (self.scroll, sh is not None, dict(anchor="w", fill="x")))
        for k, (part, on, kw) in enumerate(order):
            if not on:
                if part.winfo_manager():
                    part.pack_forget()
                continue
            if part is self.basics and part.winfo_manager() and getattr(self, "_basics_pad", None) != kw["pady"]:
                part.pack_configure(pady=kw["pady"])
            if part is self.basics:
                self._basics_pad = kw["pady"]
            if part.winfo_manager():
                continue
            after = [p for p, o, _ in order[k + 1:] if o and p.winfo_manager()]
            after += [w for w in (self.hidden, self.status) if w.winfo_manager()]
            part.pack(before=after[0] if after else self.buttons, **kw)
        self.sync_basics(picks)
        self.sync_steps(picks)
        self.sync_hidden(picks)
        hz = any(s.get("hz") for _, s in picks)
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

    def put_all(self, key, pairs):
        """Box key for several shapes, pairs = [(index, its text)]: the text when they're all the same, else empty
        with every shape's value in its tip (the shared Mixed rule, widgets.same_or_blank); typing sets them all."""
        texts = {t for _, t in pairs}
        self.put(key, texts.pop() if len(texts) == 1 else "")
        self.tip_for(key, pairs)

    def show_value(self, name, widget, pairs):
        """A read-only value for several shapes: the text, or "Mixed" with each shape's value in its tip."""
        texts = {t for _, t in pairs}
        widget.config(text=next(iter(texts)) if len(texts) == 1 else tr("widgets.mixed"))
        self.tip_for(name, pairs)

    def tip_for(self, name, pairs):
        tip = self.tips.get(name)
        if tip is None:
            return
        base = self.tip_base.get(name, "")
        tip.text = (mixed_text(pairs) + ("\n\n" + base if base else "")) if len({t for _, t in pairs}) > 1 else base

    def place_of(self, sh, i=None):
        """(first tick, length in ticks, lowest key, highest key) of sh's notes as SHOWN (i: its place in app.shapes;
        while a step is edited = without the later steps, as on the piano roll), or as made whole when those aren't
        known; remembered ones only (never made here), or None (none known yet / no notes)."""
        app = self.app
        view = app.edit_view(sh, i)
        got = app.notes_known(view) if view is not sh else None
        if got is None:
            got = app.notes_known(sh)
        if got is None:
            return None
        notes = got[0]
        if not isinstance(notes, np.ndarray):
            notes = np.asarray(notes)
            return place_in(notes)
        places = self.__dict__.setdefault("_places", {})  # (kept per notes array, weakly: 1,000 shapes picked)
        hit = places.get(id(notes))
        if hit is not None and hit[0]() is notes:
            return hit[1]
        out = place_in(notes)
        if len(places) > 4 * max(64, len(self.picked)):
            for k in [k for k, v in places.items() if v[0]() is None]:
                del places[k]
        places[id(notes)] = (weakref.ref(notes), out)
        return out

    def sync_basics(self, picks):
        app = self.app
        for w in self.basics.winfo_children():
            if not picks and w.winfo_manager():
                w.grid_remove()
        if not picks:
            return
        for name in self.heads:
            for w in self.heads[name]:
                if not w.winfo_manager():
                    w.grid()
        self.show_value("kind", self.kind, [(i, kind_text(sh)) for i, sh in picks])
        self.show_value("name", self.name, [(i, app.shape_label(sh)) for i, sh in picks])
        self.show_part("kind", True)
        self.show_part("name", True)
        made = [(i, made_text(sh)) for i, sh in picks]
        self.show_value("made", self.made, made)
        self.show_part("made", any(t for _, t in made))
        places = [(i, sh, self.place_of(sh, i)) for i, sh in picks]
        known = [(i, sh, p) for i, sh, p in places if p is not None]
        for k, (key, fr) in enumerate(zip(("start", "length", "low", "high"),
                                          (self.start, self.length, self.low, self.high))):
            self.show_part(key, True)
            self.put_all(key, [(i, fmt(p[k])) for i, _, p in known])
            ok = [1 for _, sh, _ in known if not (key in ("length", "high") and sh.get("hz"))]
            fr.boxes[0].config(state="normal" if ok else "disabled")
        from roll.roll_shared import note_name
        for k, fr in ((2, self.low), (3, self.high)):
            names = {note_name(p[k]) for _, _, p in known}
            fr.word.config(text=names.pop() if len(names) == 1 else "")
        custom = [(i, sh) for i, sh in picks if is_custom(sh)]
        self.show_part("inside", bool(custom))
        if custom:
            self.show_value("inside", self.inside, [(i, fill_text(sh)) for i, sh in custom])
        spam = [(i, sh) for i, sh in custom if sh.get("fill") in SPAM_FILLS and not sh.get("hz")]
        self.show_part("gate", bool(spam))
        if spam:
            self.put_all("gate", [(i, fmt(gate_ticks(sh["gate"], app.ppq))) for i, sh in spam])
        self.show_part("vel0", True)
        self.put_all("vel0", [(i, fmt(sh.get("vel0", 100))) for i, sh in picks])
        self.put_all("vel1", [(i, fmt(sh.get("vel1", 100))) for i, sh in picks])
        self.show_part("vel_note", any(sh.get("vel_env") for _, sh in picks))
        cols = [(i, colours_text(sh)) for i, sh in picks]
        chans = [(i, channel_text(app, sh)) for i, sh in picks]
        self.show_value("colours", self.colours, cols)
        self.show_part("colours", True)
        self.show_value("channel", self.channel, chans)
        self.show_part("channel", True)
        # (the folded groups' grey lines: what each holds, "Mixed" (plain) where the picked shapes differ)
        mixed = tr("widgets.mixed")

        def one(pairs):
            texts = {t for _, t in pairs}
            return next(iter(texts)) if len(texts) == 1 else mixed

        names = [(i, app.shape_label(sh)) for i, sh in picks]
        shorts = {"shape": one(names)}
        if known:
            got = [one([(i, fmt(p[k])) for i, _, p in known]) for k in range(2)]
            keys = [one([(i, note_name(p[k])) for i, _, p in known]) for k in (2, 3)]
            shorts["place"] = tr("props.place_short", start=got[0], length=got[1], low=keys[0], high=keys[1])
        else:
            shorts["place"] = ""
        gate = []
        if custom:
            gate.append(one([(i, fill_text(sh)) for i, sh in custom]))
        if spam:
            ticks = {gate_ticks(sh["gate"], app.ppq) for _, sh in spam}
            if len(ticks) == 1:
                t = ticks.pop()
                gate.append(trn("props.gate_short", t, n=fmt(t)))
            else:
                gate.append(mixed)
        gate.append(tr("props.vel_short", a=one([(i, fmt(sh.get("vel0", 100))) for i, sh in picks]),
                       b=one([(i, fmt(sh.get("vel1", 100))) for i, sh in picks])))
        shorts["gate"] = ", ".join(gate)
        shorts["colours"] = ", ".join((one(cols), one(chans)))
        for group, text in shorts.items():
            if self.shorts[group].cget("text") != text:
                self.shorts[group].config(text=text)
        self.sync_folds()

    # ---- the steps

    def step_lines(self, picks):
        """Every step any shown shape has, in order (stage E: shapes with different lists = each step once, a step
        only some have placed after the step it follows in the first shape having it): [(key, [(index, shape,
        tag)])]. Also keeps self.keys_of (each shape's own list as keys)."""
        where, order = {}, []
        self.keys_of = {}
        for i, sh in picks:
            tags = S.order_of(sh)
            keys = step_keys(sh, tags)
            self.keys_of[i] = keys
            if not order:  # (the first: its list as it is)
                order = list(keys)
                for k, t in zip(keys, tags):
                    where[k] = [(i, sh, t)]
                continue
            prev = None
            for k, t in zip(keys, tags):
                if k in where:
                    where[k].append((i, sh, t))
                else:
                    order.insert(order.index(prev) + 1 if prev is not None else 0, k)
                    where[k] = [(i, sh, t)]
                prev = k
        return [(k, where[k]) for k in order]

    def sync_steps(self, picks):
        lines = self.step_lines(picks)
        hidden = self.hidden_keys(picks)
        names = tuple(tag_name(h[0][1], h[0][2]) for _, h in lines)
        offs = tuple(tuple(S.is_off(sh, t) for _, sh, t in h) for _, h in lines)
        shown = (tuple(id(sh) for _, sh in picks), tuple(k for k, _ in lines), tuple(len(h) for _, h in lines), names,
                 offs, tuple(sorted(hidden, key=repr)), self.fixed_lines(picks),
                 tuple(number_spec(h[0][1], h[0][2]) for _, h in lines))  # (a unit switched: its word follows)
        if shown != self.shown:
            self.shown = shown
            self.build_fixed(picks)
            self.build(picks, lines, names, hidden)
        else:
            for r, (k, h) in zip(self.rows, lines):
                r["holders"] = h
                r["tag"] = h[0][2]
        for r in self.rows:
            if r["box"] is not None:
                self.put_number(r)

    def fixed_lines(self, picks):
        """The fixed steps at the top (they can't move: every step works on them): the notes as drawn, Single
        channel's own overlap fix."""
        if not picks:
            return ()
        out = ["props.fixed_drawn"]
        if self.app.channel_mode.get() == "single" and any(not sh.get("hz") for _, sh in picks):
            out.append("props.fixed_single")
        return tuple(out)

    def build_fixed(self, picks):
        for w in self.fixed.winfo_children():
            w.destroy()
        for key in self.fixed_lines(picks):
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
        count = ttk.Label(fr, text="", foreground=look.HINT, padding=(6, 0, 0, 0))  # ("2 of 3": stage E)
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
        for w in (fr, grip, lb, count):
            w.bind("<ButtonPress-1>", lambda e: self.on_press(e, p))
            w.bind("<B1-Motion>", self.on_motion)
            w.bind("<ButtonRelease-1>", self.on_release)
        return {"tag": None, "key": None, "holders": [], "frame": fr, "grip": grip, "name": lb, "count": count,
                "tick": tick, "var": var, "box": None, "entry": entry, "boxvar": boxvar, "word": word, "x": x,
                "tick_tip": Tooltip(tick, ""), "name_tip": Tooltip(lb, ""), "box_tip": Tooltip(entry, ""),
                "count_tip": Tooltip(count, "")}

    def build(self, picks, lines, names, hidden):
        """The lines shown: the kept ones filled in again, more made when needed, the rest taken out."""
        self.empty.pack_forget()
        n = len(lines)
        for r in self.pool[n:]:
            if r["frame"].winfo_manager():
                r["frame"].pack_forget()
        self.rows = []
        if not picks:
            self.scroll.fit()
            return
        if not lines:
            self.empty.pack(anchor="w", padx=(20, 0))
        clumps = {}  # (each shape's steps hidden by Split: Clumps)
        for p, ((key, holders), name) in enumerate(zip(lines, names)):
            if p == len(self.pool):
                self.pool.append(self.make_row(p))
            r = self.pool[p]
            if not r["frame"].winfo_manager():
                r["frame"].pack(anchor="w", fill="x", pady=1)
            _, sh, tag = holders[0]
            r["tag"], r["key"], r["holders"] = tag, key, holders
            offs = [S.is_off(s, t) for _, s, t in holders]
            off = all(offs)
            r["var"].set(not any(offs))  # (half ticked: a click switches them all on)
            can_off = tag[0] != S.FX or is_page((sh.get("fx") or [])[tag[1]])
            r["tick"].state(["!disabled" if can_off else "disabled",
                             "alternate" if any(offs) and not off else "!alternate"])
            r["tick_tip"].text = tr("props.tick_tip" if can_off else "props.tick_never") + (
                "\n\n" + tr("props.tick_mixed") if any(offs) and not off else "")
            some_clumps = any(t in clumps.setdefault(i, clumps_hidden(s)) for i, s, t in holders)
            grey = off or key in hidden or some_clumps
            opens = self.opens(sh, tag)
            r["name"].config(text=name, foreground=look.LABEL_OFF if grey else "", cursor="hand2" if opens else "fleur")
            r["name_tip"].text = (tr("props.hidden_tip") if key in hidden else tr("props.clumps_hidden")
                                  if some_clumps else tr("props.open_tip") if opens else tr("props.no_window_tip"))
            count = r["count"]
            if len(picks) > 1 and len(holders) < len(picks):
                count.config(text=tr("props.count", n=len(holders), of=len(picks)))
                r["count_tip"].text = tr("props.count_tip", names=names_line(
                    [tr("props.list_row", n=i + 1, name=self.app.shape_label(s)) for i, s, _ in holders]))
                if not count.winfo_manager():
                    count.pack(side="left", after=r["name"])
            elif count.winfo_manager():
                count.pack_forget()
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

    def put_number(self, r):
        """Line r's number: the same for every shape having its step, else empty + each one's in the tip."""
        ppq = self.app.ppq
        pairs = []
        for i, sh, tag in r["holders"]:
            v = number_of(sh, tag, ppq)
            pairs.append((i, "" if v is None else fmt(round(v, 3))))
        texts = {t for _, t in pairs}
        text = texts.pop() if len(texts) == 1 else ""
        base = tr("props.number_tip." + r["tag"][0])
        r["box_tip"].text = (mixed_text([(i, t or "–") for i, t in pairs]) + "\n\n" + base
                             if len({t for _, t in pairs}) > 1 else base)
        if self.focus_get() is r["box"] and r["boxvar"].get() != text:
            return
        self.app._loading = True
        try:
            r["boxvar"].set(text)
        finally:
            self.app._loading = False
        good(r["box"])

    def hidden_keys(self, picks):
        """The steps (as keys) of the shown shapes hidden while one is edited now (stage C)."""
        out = set()
        for i, sh in picks:
            t = self.app.edit_tag(sh, i)
            if t is not None:
                keys = step_keys(sh, S.order_of(sh))
                tags = S.order_of(sh)
                gone = set(S.hidden_after(sh, t))
                out.update(k for k, tg in zip(keys, tags) if tg in gone)
        return out

    def sync_hidden(self, picks):
        tags, sh = [], None
        for i, s in picks:  # (the shape hiding the most, as the step windows' line: App.edit_hidden_steps)
            t = self.app.edit_tag(s, i)
            got = S.hidden_after(s, t) if t is not None else []
            if len(got) > len(tags):
                tags, sh = got, s
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
        if not 0 <= p < len(self.rows) or not self.rows[p]["holders"]:
            return
        i, sh, (kind, n) = self.rows[p]["holders"][0]  # (several: as the first shape having it has it)
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
        """Before a change from this window: typing elsewhere taken, one undo step. While it runs, this window's own
        boxes take nothing (busy): the box being typed in IS this change (Enter), or was taken first (take_typed);
        asking it again went round and round (an error, nothing applied)."""
        app = self.app
        self.busy = True
        try:
            app.commit_typing()
        finally:
            self.busy = False
        if app.notes_late:
            app.shapes_changed(now=True)
        app.push_undo(name=name)

    def take_typed(self):
        """A number typed in one of this window's boxes but not entered: it goes in first (its own undo step), before
        a tick / × / drag / Make the others like this works out its own change."""
        try:
            w = self.focus_get()
        except KeyError:
            return
        fn = self.app.leave_boxes.get(w)
        if fn is not None and str(w).startswith(str(self)):
            fn()

    def done(self, step=None, targets=None):
        """After a change: the notes, the panel and the windows follow (step: the step it edited, stage C; targets:
        the shapes it went to: the later steps are hidden only when that is the whole selection, App.colour_edit)."""
        app = self.app
        if step is not None and targets is not None and set(targets) != set(app.sels):
            step = None
        app.colour_edit_set(step)
        app.shapes_changed()
        app.sync_recolour()
        app.sync_panel()
        app.schedule_autosave()
        self.sync()

    def same_list(self, r):
        """The shown shapes whose list of steps is the same as the first one having line r's step (moving / ×
        go to them only, user 2026-11-11), and how many shown shapes are left out."""
        ref = self.keys_of.get(r["holders"][0][0])
        picks = self.picks()
        out = [(i, sh) for i, sh in picks if self.keys_of.get(i) == ref]
        return out, len(picks) - len(out)

    def skipped_line(self, done, left):
        """The line saying how many shapes a move / × went to and why the others didn't follow ("" = all)."""
        return trn("props.done_on", done, n=done) + " " + trn("props.skipped", left, left=left) if left else ""

    def move(self, a, b):
        """Line a to place b (one undo step) on every shown shape with the same list as the first one having it; an
        order it can't keep = a line says why."""
        if not (0 <= a < len(self.rows) and 0 <= b < len(self.rows)):
            return
        self.take_typed()  # (first: the new order is worked out on the shapes with that number in)
        if not (0 <= a < len(self.rows) and 0 <= b < len(self.rows)):
            return
        r = self.rows[a]
        keys = [x["key"] for x in self.rows]
        key = keys.pop(a)
        keys.insert(b, key)
        targets, left = self.same_list(r)
        own = self.keys_of[targets[0][0]]
        mine = set(own)
        ra = own.index(key)
        rb = [k for k in keys if k in mine].index(key)
        if ra == rb:
            return self.say(tr("props.move_nothing") if left else "")
        news = []
        for i, sh in targets:
            new = S.moved(sh, ra, rb)
            if new is None:
                return self.say(tr("props.cant_move"))
            news.append((sh, new))
        self.say(self.skipped_line(len(targets), left))
        self.editing = None
        self.begin(tr("props.undo_move"))
        for sh, new in news:
            sh.clear()
            sh.update(new)
        self.done()

    def say(self, text):
        """The orange line under the list; it goes once other shapes are shown (sync)."""
        self.said_for = tuple(id(sh) for _, sh in self.picks())
        self.status.config(text=text)
        if bool(text) != bool(self.status.winfo_manager()):
            if text:
                self.status.pack(anchor="w", fill="x", pady=(6, 0), before=self.buttons)
            else:
                self.status.pack_forget()

    def on_tick(self, p):
        """On / off: every shown shape having that step (a half tick = some off: a click switches them all on)."""
        if not 0 <= p < len(self.rows):
            return
        off = not self.rows[p]["var"].get()  # (read first: taking a typed number may fill the ticks in again)
        self.take_typed()
        if not 0 <= p < len(self.rows):
            return
        r = self.rows[p]
        change = [(sh, tag) for _, sh, tag in r["holders"] if S.is_off(sh, tag) != off]
        if not change:
            return self.sync(follow=False)
        self.editing = None
        self.begin(tr("props.undo_off" if off else "props.undo_on"))
        for sh, tag in change:
            S.with_off(sh, tag, off)
        self.done()

    def on_remove(self, e, p):
        if not (0 <= e.x < e.widget.winfo_width() and 0 <= e.y < e.widget.winfo_height()):
            return  # (let go off the ×, like a button)
        if not 0 <= p < len(self.rows):
            return
        self.take_typed()
        if not 0 <= p < len(self.rows):
            return
        r = self.rows[p]
        targets, left = self.same_list(r)
        tag = r["holders"][0][2]
        self.say(self.skipped_line(len(targets), left))
        self.editing = None
        self.begin(tr("props.undo_remove", name=tag_name(r["holders"][0][1], tag)))
        for _, sh in targets:
            removed(self.app, sh, tag)
        self.done()

    def on_number(self, p, left=None):
        """A step's main number typed (Enter / the box left): every shown shape having that step (with the same unit)
        gets it, and that step is being edited now (stage C)."""
        if self.app._loading or self.busy or not 0 <= p < len(self.rows):
            return
        r = self.rows[p]
        if left is not None and unchanged(left):
            return
        holders = r["holders"]
        if not holders:
            return
        tag0 = holders[0][2]
        spec = number_spec(holders[0][1], tag0)
        text = r["boxvar"].get().strip().rstrip("%°")
        if not text and len({number_of(sh, t, self.app.ppq) for _, sh, t in holders}) > 1:
            return good(r["box"])  # (a Mixed box left empty: nothing typed; Sustain too: 0 takes "at most" off all)
        try:
            v = None if not text and tag0[0] == "sustain" else float(calc(text))
            if v is not None and not spec[1] <= v <= spec[2]:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            return bad(r["box"])
        good(r["box"])
        ppq = self.app.ppq
        changes, other_unit, step, tags = [], 0, None, set()
        for i, sh, tag in holders:
            if number_spec(sh, tag) != spec:  # (Gate extend in % on one, in ticks on another: the same unit only)
                other_unit += 1
                continue
            old = number_of(sh, tag, ppq)
            if old is not None and v is not None and abs(old - v) < 1e-9 or old is None and v is None:
                continue
            if tag[0] in S.COLOURS:
                rc = clean_recolour(sh.get("recolour"))
                new = ordered(rc, clean_recolour(with_number(sh, tag, v, ppq)["recolour"]))
                if new == rc:
                    continue
                changes.append((i, sh, "recolour", new))
                step = step or edited_step(rc, new) or tag
            else:
                changes.append((i, sh, tag[0], with_number(sh, tag, v, ppq)[tag[0]]))
                step = step or tag
            tags.add(tuple(tag))
        self.say(trn("props.other_unit", other_unit, n=other_unit) if other_unit else "")
        if not changes:
            return
        self.editing = tag0[0]
        self.begin(tr("props.undo_number", name=tag_name(holders[0][1], tag0)))
        for _, sh, k, val in changes:
            sh[k] = val
        self.done(step if len(tags) == 1 else None, [i for i, *_ in changes])

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
        if app._loading or self.busy or left is not None and unchanged(left):
            return
        if self.blank(key):
            return
        t_lo, p_lo, p_hi = app.point_limits()
        lo_hi = {"start": (t_lo, 10 ** 9), "low": (p_lo, p_hi), "length": (1, 10 ** 9)}.get(key, (p_lo, p_hi))
        v = self.number_in(key, *lo_hi)
        if v is None:
            return
        v, ppq = round(v), app.ppq
        changes = []  # (several shown: each one moved / sized the same way, user 2026-11-11: typing sets them all)
        for i, sh in self.picks():
            got = self.place_of(sh, i)
            if got is None or key in ("length", "high") and sh.get("hz"):
                continue
            a, n, lo, hi = got
            if key == "start":
                change = None if v == a else ("move", (v - a) / ppq, 0)
            elif key == "low":
                change = None if v == lo or v + hi - lo > p_hi else ("move", 0, v - lo)
            elif key == "length":
                change = None if v == n or not n else ("size", a / ppq, v / n, lo - 0.5, 1.0)
            else:
                change = (None if v == hi or v < lo or hi == lo else
                          ("size", 0.0, 1.0, lo - 0.5, (v - lo + 1) / (hi - lo + 1)))
            if change is not None:
                changes.append((sh, change))
        if not changes:
            return self.sync(follow=False)
        nudge = len(changes) <= NUDGE_SHAPES
        news = []
        for sh, change in changes:
            if change[0] == "move":
                db, dp = change[1], change[2]
                news.append((sh, dict(sh, pts=[[b + db, p + dp] for b, p in sh["pts"]])))
            else:
                news.append((sh, self.sized(sh, change, key, v) if nudge else app.stretched(sh, *change[1:])))
        self.begin(tr("props.undo_place"))
        for sh, new in news:
            sh.clear()
            sh.update(new)
        app.roll.box_kept = None  # (a kept Select box was round the old place)
        self.done()

    def sized(self, sh, change, key, v):
        """sh sized so its notes' Length / Highest key come out as typed (v), or as near as its notes can (a spam
        gate makes lengths in whole gates; key rows round): the stretch is nudged and the notes made again until they
        land (NUDGE_TRIES at most), the nearest kept (a tie: the smaller stretch)."""
        from notes.engine import shape_notes_tracks
        app = self.app
        _, ab, kx, ap, ky = change
        k0 = kx if key == "length" else ky

        def made(k):
            new = app.stretched(sh, ab, k, ap, 1.0) if key == "length" else app.stretched(sh, ab, 1.0, ap, k)
            try:
                got = place_in(np.asarray(shape_notes_tracks(new, app.ppq, app.keys)[0]).reshape(-1, 4))
            except Exception:
                got = None
            return new, None if got is None else got[1] if key == "length" else got[3]

        best, tried = None, []

        def keep(k):
            nonlocal best
            new, r = made(k)
            tried.append((k, r))
            if r is not None and (best is None or (abs(r - v), abs(k - 1)) < (abs(best[1] - v), abs(best[2] - 1))):
                best = (new, r, k)
            return r

        k = k0
        for _ in range(NUDGE_TRIES):
            r = keep(k)
            if r is None or r == v:
                break
            lo = [t for t in tried if t[1] is not None and t[1] < v]
            hi = [t for t in tried if t[1] is not None and t[1] > v]
            if lo and hi:  # (one under, one over: halfway between the nearest ones)
                k = (max(lo, key=lambda t: t[1])[0] + min(hi, key=lambda t: t[1])[0]) / 2
            else:
                k = k * (v / max(r, 1) if key == "length" else (v + 0.5 - ap) / max(0.5, r + 0.5 - ap))
            if any(abs(k - t[0]) < 1e-9 for t in tried):
                break
        return best[0] if best is not None else made(k0)[0]

    def blank(self, key):
        """Box key left empty while it shows Mixed (several shapes, different values): nothing typed."""
        if self.vars[key].get().strip():
            return False
        good(self.entries[key])
        self.sync(follow=False)
        return True

    def on_gate(self, key, left=None):
        app = self.app
        if app._loading or self.busy or left is not None and unchanged(left):
            return
        if self.blank(key):
            return
        v = self.number_in(key, 0.5, 10 ** 7)
        if v is None:
            return
        beats = int(v + 0.5) / app.ppq
        spam = [sh for _, sh in self.picks() if is_custom(sh) and sh.get("fill") in SPAM_FILLS and not sh.get("hz")
                and not (abs(sh["gate"] - beats) < 1e-12 and not sh.get("range"))]
        if not spam:
            return self.sync(follow=False)
        if not app.confirm_big([dict(sh, gate=beats) for sh in spam]):
            return self.sync(follow=False)
        self.begin(tr("panel_custom.spam_gate"))
        for sh in spam:
            sh["gate"] = beats
            if sh.get("range"):  # (a gate typed = one flat gate again, kept for the Range window: like the panel)
                sh["range_kept"] = sh.pop("range")
        self.done()

    def on_velocity(self, key, left=None):
        app = self.app
        if app._loading or self.busy or left is not None and unchanged(left):
            return
        if self.blank(key):
            return
        v = self.number_in(key, 1, 127)
        if v is None:
            return
        v = int(round(v))
        shapes = [sh for _, sh in self.picks() if sh.get(key, 100) != v or sh.get("vel_env")]
        if not shapes:
            return self.sync(follow=False)
        from notes.fx import with_velocity
        self.begin(tr("app.velocity"))
        for sh in shapes:
            sh[key] = v
            sh.pop("vel_env", None)
            sh.pop("own_vel", None)
            if sh.get("fx"):
                sh["fx"] = with_velocity(sh["fx"], [[0.0, sh.get("vel0", 100)], [1.0, sh.get("vel1", 100)]])
        self.done()

    # ---- Add ▾ / Hz bass settings…

    def fill_add(self):
        m = self.add_menu
        m.delete(0, "end")
        app = self.app
        i, sh = self.shape()
        if sh is None:
            return
        # (Add goes to every shown shape, user 2026-11-11: the tools work on the selection, so picking some in the list
        # on the left selects just those first: to_picked; what is greyed is worked out on the shown ones)
        picks = self.picks()
        was = (set(app.sels), app.sel)
        app.sels, app.sel = {i for i, _ in picks}, picks[0][0]
        try:
            tools = bool(app.note_tool_sels())
            sus = [bool(importlib.import_module("window.sustain_window").sustain_targets(app)),
                   bool(importlib.import_module("window.subtract_window").subtract_targets(app))]
            gf = app.gate_flip_state()
            colours = bool(app.recolour_targets())
            rev = bool(app.reverse_targets())
        finally:
            app.sels, app.sel = was
        go = self.to_picked
        for tool in ADD_TOOLS:
            mod, fn, _ = PAGE_WINDOWS[tool]
            m.add_command(label=tr("props.add_" + tool), state="normal" if tools else "disabled",
                          command=go(lambda mod=mod, fn=fn: getattr(importlib.import_module(mod), fn)(app)))
        m.add_command(label=tr("roll_menu.glue"), command=go(app.glue_selected), state="normal" if tools else "disabled")
        m.add_separator()
        for kind in ("sustain", "extend", "subtract"):
            mod = importlib.import_module(f"window.{kind}_window")
            ok = sus[0] if kind == "sustain" else sus[1]
            m.add_command(label=tr(kind + ".menu"), state="normal" if ok else "disabled",
                          command=go(lambda mod=mod, kind=kind: getattr(mod, "open_" + kind)(app)))
        if gf:
            m.add_command(label=tr("app.gate_flip"), command=go(app.gate_flip),
                          state="normal" if gf[0] and not gf[1] else "disabled")
        if not colours:
            return
        m.add_separator()
        has = set()
        for _, s in picks:
            rc = clean_recolour(s.get("recolour"))
            if rc and "mode" in rc_order(rc):
                has.add("mode")
        for mode in MODES:  # (one Shuffle / Split a shape: greyed while it has one)
            m.add_command(label=tr("recolour." + mode), command=go(lambda mode=mode: self.add_mode(mode)),
                          state="disabled" if "mode" in has else "normal")
        m.add_command(label=tr("stir.button"), command=go(self.add_stir))
        m.add_command(label=tr("warp.button"), command=go(app.open_warp))
        m.add_command(label=tr("liquify.button"), command=go(app.open_liquify))
        last = rc_tags(shape_order(sh))[-1:]
        twice = bool(last) and last[0][0] == "turned"
        m.add_command(label=tr("order.add_reverse_last" if twice else "join_split.reverse_tracks"),
                      command=go(app.reverse_tracks), state="normal" if rev and not twice else "disabled")

    def to_picked(self, fn):
        """fn run on the shown shapes: when only some of the selected ones are picked in the list on the left, they
        are selected first (the list stays as it is: our_sels)."""
        def run():
            app = self.app
            want = {i for i, _ in self.picks()}
            if want and want != set(app.sels):
                listed = list(self.listed)
                self.our_sels = frozenset(want)
                app.select_many(want, self.picks()[0][0])
                self.listed = listed
                self.our_sels = frozenset(app.sels)
            fn()
        return run

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

    def make_like(self):
        """"Make the others like this": the one shape shown gives its whole list of steps (steps, order, numbers, off
        ticks) to every other shape in the list on the left; one undo step (user 2026-11-11). What a shape can't
        have (colour effects on a Hz bass, pages on an image...) is left out of it, and a line says so."""
        app = self.app
        picks = self.picks()
        if len(picks) != 1 or len(self.listed) < 2:
            return
        i, src = picks[0]
        others = [app.shapes[j] for j in self.listed if j != i and j < len(app.shapes)]
        if not others:
            return
        self.editing = None
        app.commit_typing()
        app.colour_edit_leave()
        self.begin(tr("props.undo_like"))
        left_out = 0
        for sh in others:
            left_out += made_like(sh, src)
            settle_steps(app, sh)
        self.say(trn("props.like_left_out", left_out, n=left_out) if left_out else "")
        self.done()

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
        if fx[n]["tool"] == "glue":  # (only this place's boxes go: the glue before the pages stays)
            return glue_part_removed(sh, sum(1 for st in fx[:n + 1] if st["tool"] == "glue"))
        if is_page(fx[n]):
            j = sum(1 for st in fx[:n] if is_page(st))
            rc = clean_recolour(sh.get("recolour"))
            if rc:
                got = page_removed(rc, j, len(pages(fx)))
                if got:
                    sh["recolour"] = got
        ToolWindow.put_fx(sh, fx[:n] + fx[n + 1:])
    elif kind == S.GLUE:  # (the boxes glued before the pages only: the later Glue lines keep theirs)
        glue_part_removed(sh, 0)
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


def glue_part_removed(sh, g):
    """One Glue line's boxes taken off sh (g = 0: the glue before the pages, g = k: the k-th glue place's), the others
    kept where they are. Glue on everything (no boxes) = one Glue line: all of it goes. Changes sh."""
    from notes.fx import glue_groups, regrouped, tidy_glue
    from window.tool_window import ToolWindow
    groups = glue_groups(sh.get("glue"), sh.get("fx"))
    if groups is None or not 0 <= g < len(groups):
        sh.pop("glue", None)
    else:
        groups[g] = []
        old = sh.get("fx") or []
        glue, fx = regrouped(groups, old)
        places = iter(st for st in fx or () if st["tool"] == "glue")
        kept = []  # (the places left keep being the same objects: the step order follows them, steps.after_change)
        for j, st in enumerate(old):
            if st["tool"] != "glue":
                kept.append(st)
            elif groups[sum(1 for s in old[:j + 1] if s["tool"] == "glue")]:
                new = next(places)
                st.pop("from", None)
                st.update(new)
                kept.append(st)
        if glue:
            sh["glue"] = glue
        else:
            sh.pop("glue", None)
        ToolWindow.put_fx(sh, kept)
    tidy_glue(sh)


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


# ---- several shapes (stage E)

def step_keys(sh, tags):
    """Shape sh's steps (tags in order) as keys alike across shapes: the colour effects, Glue, Gate aware flip,
    Sustain, Gate extend / subtract by kind and number; an fx entry by what it is (its tool, a flip's way, a turn's
    angle) and how many of those came before it ("Strum 2" = the 2nd Strum page)."""
    fx = sh.get("fx") or []
    seen, out = {}, []
    for kind, n in tags:
        if kind != S.FX:
            out.append((kind, n))
            continue
        st = fx[n] if n < len(fx) else {}
        what = (st.get("tool"), st.get("axis"), st.get("deg"))
        m = seen.get(what, 0)
        seen[what] = m + 1
        out.append((S.FX,) + what + (m,))
    return out


def mixed_text(pairs):
    """A Mixed value's tip: the shared sentence, then each shape's value ("Shape 3: 120"), at most TIP_MOST."""
    lines = [tr("props.value_of", n=i + 1, value=t) for i, t in pairs[:TIP_MOST]]
    if len(pairs) > TIP_MOST:
        lines.append(trn("props.more_values", len(pairs) - TIP_MOST, n=len(pairs) - TIP_MOST))
    return tr("widgets.mixed_tip") + "\n\n" + "\n".join(lines)


def place_in(notes):
    """(first tick, length in ticks, lowest key, highest key) of notes, or None (no notes)."""
    if not len(notes):
        return None
    a, z = int(notes[:, 0].min()), int(notes[:, 1].max())
    return a, z - a, int(notes[:, 2].min()), int(notes[:, 2].max())


def is_custom(sh):
    """A drawn custom shape with its own inside (not plain notes / an image)."""
    return sh.get("kind") == "custom" and "notes" not in sh and "picture" not in sh


LIKE_KEYS = ("fx", "glue", "sustain", "extend", "subtract")


def made_like(sh, src):
    """Shape sh's steps made like src's: every step, its order, its numbers, its off tick (Make the others like
    this). Steps sh can't have are left out: colour effects on a Hz bass, note tool pages / glue on an image, Sustain
    / Gate extend / subtract on a Hz bass or an image, Gate aware flip on a shape that isn't a flipped custom shape;
    a sliced piece keeps its own flips (its notes come from its whole's pages). Returns how many were left out.
    Changes sh."""
    from notes.fx import tidy_glue
    from notes.gateflip import is_flipped, is_pasted_flipped, turn_velocities
    from notes.mask import is_mask
    from notes.recolour import can_recolour, turns_of
    from notes.sliced import steps_kept
    if is_mask(sh):
        return 1
    left = 0
    have, want = S.own_steps(sh), S.own_steps(src)
    if (S.GATE_FLIP in want) != (S.GATE_FLIP in have):
        can = sh.get("kind") == "custom" and (is_flipped(sh) or is_pasted_flipped(sh))
        if S.GATE_FLIP in want and not can:
            left += 1
        else:  # (switched as Gate aware flip does: the velocities turned round too)
            if S.GATE_FLIP in have:
                del sh["gate_flip"]
            else:
                sh["gate_flip"] = True
            turn_velocities(sh)
    picture, hz = "picture" in sh, bool(sh.get("hz"))
    for key in LIKE_KEYS:
        if (picture and key in ("fx", "glue") or (picture or hz) and key in S.LENGTHS
                or key == "fx" and steps_kept(sh)):
            left += bool(src.get(key))
            continue
        if src.get(key):
            sh[key] = copy.deepcopy(src[key])
        else:
            sh.pop(key, None)
    tidy_glue(sh)
    old = clean_recolour(sh.get("recolour"))
    got = clean_recolour(src.get("recolour"))
    if not can_recolour(sh):
        left += bool(got)
    else:
        new = None
        if got:
            new = copy.deepcopy(got)
            turns = turns_of(got)
            new.pop("turned", None)
            new.pop("turns", None)
            if old and old.get("turned"):  # (its notes hold their own swap: it stays, unswapped at the start)
                new["turned"] = old["turned"]
            if turns:
                new["turns"] = turns
            new = clean_recolour(new)
        tw = (old or {}).get("turned")
        if tw and "notes" in sh and not (new or {}).get("turned"):  # (as colour_removed: the notes' colours back)
            rows = unpack_notes(sh["notes"]).copy()
            low = rows[:, 4] % ROUND
            rows[:, 4] = rows[:, 4] - low + turned_map(tw, back=True)(low)
            sh["notes"] = pack_notes(np.asarray(rows, np.int64))
        if new:
            sh["recolour"] = new
        else:
            sh.pop("recolour", None)
    S.carried(sh, src)  # (its order and off ticks)
    if sh.get("off"):  # (steps it couldn't take: their off ticks go)
        there = set(S.order_of(sh))
        off = [t for t in sh["off"] if tuple(t) in there]
        if off:
            sh["off"] = off
        else:
            sh.pop("off", None)
    return left


def settle_steps(app, sh):
    """sh's steps were set as a whole here: App.steps_noted takes them as they are (it would put steps new to sh at
    the end of its order)."""
    sigs = getattr(app, "_step_sigs", None)
    if sigs is not None:
        sigs[id(sh)] = (sh, S.signature(sh))
