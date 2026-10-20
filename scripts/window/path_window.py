"""The Copy along a path window (notes/copypath.py): the selected shapes copied along a path through points A (a corner
of their notes' box), B, C, ..., shown live on the piano roll (the shared part: tool_window.py; the path's points on
the piano roll: roll/roll_path.py). Several shapes travel together as one group: each gets the same page, plus where
the group's box is from its own notes ("grp", page_cfg), so corners, sizes and flips work on the whole group.

Path kind (Straight / Curve / Arc) + a Formula along it (a curve's patterns: presets, saved ones, Custom…), spacing (one copy every [note length] or a number of copies), Speed, Size,
Colours (+ Step), Flip (+ which way), On top, Range (Keep inside + Lowest / Highest key), and where the picked point is as bar / beat / tick / key like the piano
roll's status line, typed or dragged on the piano roll; Add point (it follows the mouse until a click places it)."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from files.snap import SNAP_LIST, snap_beats, snap_text
from notes.copypath import (COLOURS, COUNT_LIMITS, EVERY_LIMITS, FAR, FLIPS, KEY_LIMITS, KINDS, LETTERS, MOST_POINTS,
                            PATH_DEFAULTS, SIZE_LIMITS, SPEEDS, STEP_LIMITS, TOPS, WAYS, all_points, clean_path,
                            peak, shifts, speed_of, track)
from notes.engine import tagged_notes
from notes.pattern import MAX_LOOPS, PATTERN_PRESETS, loop_points, new_pattern, pattern_name
from roll.roll_shared import note_name
from window import big_ask, look
from window.formula_host import FormulaHost
from window.join_split import without10
from window.pattern_dialog import load_patterns, saved_pattern
from window.speed_graph import SpeedGraph, start_curve
from window.tool_window import ToolWindow
from window.widgets import Scrub, Tooltip, bad, good, grid_shown, name_index, name_list, set_names, unit_word

LENGTHS = [s for s, _ in SNAP_LIST if s != "off"]  # "one copy every" choices (the snap list's note lengths)
CHOICES = {"kind": KINDS, "speed": SPEEDS, "colours": COLOURS, "flip": FLIPS, "way": WAYS, "top": TOPS}
PLAIN_ASK = 500  # Turn into plain shapes: asked first past this many new shapes
LOOP_COPIES = 8  # a new formula's Loops: about this many copies a loop
GROUP_ONLY =("grp", "most")  # (page settings that may differ between the shapes of one group)


class PathWindow(ToolWindow):
    KEY, ATTR, POS, DEFAULTS = "path", "path_window", "path_pos", PATH_DEFAULTS
    loose = False  # the last point follows the mouse on the piano roll until a click places it
    adding = False  # Add point pressed: the new point comes with the mouse's first move on the piano roll
    idle = False  # another shape selected meanwhile: no new path until the menu / + asks for one (user)
    taking = False  # (take_typing at work)
    est = 0  # notes the shown settings make, about (asked past big_ask.BIG)
    pick = 0  # the point the bar / beat / tick / key boxes show (0 = B)
    graph_window = None  # Speed: Custom's graph (speed_graph.py), while open
    graph_job = None  # (closing it waits a moment: graph_gone)

    def clean(self, cfg):
        return clean_path(cfg)

    def build(self, box):
        s = self.app.scale
        self.vars, self.entries, self.combos = {}, {}, {}
        box.columnconfigure(0, minsize=round(80 * s))
        kind = self.choice(box, 1, "kind", tr("path.kind"))
        lb = ttk.Label(kind, text=tr("path.formula"), foreground=look.HINT)
        lb.grid(row=0, column=1, sticky="e", padx=(10, 3))
        self.formula_box = name_list(ttk.Combobox(kind, state="readonly", width=14, postcommand=self.formula_list))
        self.formula_box.grid(row=0, column=2, sticky="w")
        self.formula_box.bind("<<ComboboxSelected>>", lambda e: self.pick_formula(name_index(e.widget)))
        for w in (lb, self.formula_box):
            Tooltip(w, tr("path.tip_formula"))
        self.custom_button = ttk.Button(kind, text=tr("roll_menu.custom"), command=self.custom_formula)
        self.custom_button.grid(row=0, column=3, sticky="w", padx=(4, 0))
        Tooltip(self.custom_button, tr("path.tip_custom"))
        self.formula_numbers = ttk.Frame(kind)  # (Loops + a box per other name in the formula, while there is one)
        self.formula_numbers.grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))
        self.pat_boxes, self.pat_names = {}, None
        self.formula_list()

        self.label(box, 2, tr("path.spacing"))
        sp = ttk.Frame(box)
        sp.grid(row=2, column=1, sticky="w", pady=(6, 2))
        self.mode = tk.StringVar(value="every")
        every = ttk.Radiobutton(sp, text=tr("path.every"), variable=self.mode, value="every",
                                command=lambda: self.put("mode", "every"))
        every.grid(row=0, column=0, sticky="w")
        Tooltip(every, tr("path.tip_every"))
        self.length_box = ttk.Combobox(sp, state="readonly", width=8, values=[snap_text(v) for v in LENGTHS])
        self.length_box.grid(row=0, column=1, sticky="w", padx=(4, 4))
        self.length_box.bind("<<ComboboxSelected>>", lambda e: self.put_length(LENGTHS[e.widget.current()]))
        Tooltip(self.length_box, tr("path.tip_every"))
        self.number(sp, 0, 2, "every", tr("path.tip_every_ticks"), (1, 10, 1))
        unit_word(ttk.Label(sp, foreground=look.HINT), self.vars["every"], "unit.ticks", "unit.tick").grid(
            row=0, column=3, sticky="w", padx=(4, 0))
        count = ttk.Radiobutton(sp, text=tr("path.count"), variable=self.mode, value="count",
                                command=lambda: self.put("mode", "count"))
        count.grid(row=1, column=0, sticky="w", pady=(4, 0))
        Tooltip(count, tr("path.tip_count"))
        self.number(sp, 1, 1, "count", tr("path.tip_count"), (1, 10, 1))
        self.ticks = {}
        for r, key in ((2, "anchor"), (3, "half"), (4, "restart")):
            var = tk.BooleanVar(value=True)
            tick = ttk.Checkbutton(sp, text=tr(f"path.{key}"), variable=var,
                                   command=lambda k=key, v=var: self.put(k, v.get()))
            tick.grid(row=r, column=0, columnspan=4, sticky="w", pady=(4, 0))
            Tooltip(tick, tr(f"path.tip_{key}"))
            self.ticks[key] = (tick, var)
        speed = self.choice(box, 3, "speed", tr("path.speed"))
        self.wheeled = False  # (Custom picked with the mouse wheel: the graph doesn't open by itself, hunt)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):  # (before the dropdown's own wheel step)
            self.combos["speed"].bind(seq, lambda e: self.wheel_pick(), add="+")
        self.graph_button = ttk.Button(speed, text=tr("path.graph"), command=self.open_graph)
        self.graph_button.grid(row=0, column=1, sticky="w", padx=(4, 0))  # (Speed: Custom only)
        Tooltip(self.graph_button, tr("path.tip_graph"))

        self.label(box, 4, tr("path.size"))
        size = ttk.Frame(box)
        size.grid(row=4, column=1, sticky="w", pady=(6, 2))
        for c, key in ((0, "size0"), (3, "size1")):
            self.number(size, 0, c, key, tr(f"path.tip_{key}"), (1, 10, 0.1))
            ttk.Label(size, text=tr("unit.percent"), foreground=look.HINT).grid(row=0, column=c + 1, sticky="w",
                                                                              padx=(3, 0))
        ttk.Label(size, text="→", foreground=look.HINT).grid(row=0, column=2, padx=6)

        colours = self.choice(box, 5, "colours", tr("path.colours"))
        lb = ttk.Label(colours, text=tr("path.colour_step"), foreground=look.HINT)
        lb.grid(row=0, column=1, sticky="e", padx=(10, 3))
        self.number(colours, 0, 2, "step", tr("path.tip_step"), (1, 5, 1), label=lb, width=4)
        self.colour_note = ttk.Label(box, text="", foreground=look.WARN, wraplength=round(440 * s))
        self.colour_note.grid(row=6, column=1, sticky="w")
        flip = self.choice(box, 7, "flip", tr("path.flip"))
        self.choice(flip, 0, "way", None, column=1)
        self.choice(box, 8, "top", tr("path.top"))
        same = tuple(v for key in ("kind", "speed", "colours", "flip", "top") for v in self.combos[key].cget("values"))
        for key in ("kind", "speed", "colours", "flip", "top"):  # (one width for the left column's dropdowns, user)
            self.combos[key].fit_also = same

        self.label(box, 9, tr("path.range"))
        rng = ttk.Frame(box)
        rng.grid(row=9, column=1, sticky="w", pady=(6, 2))
        var = tk.BooleanVar(value=False)
        tick = ttk.Checkbutton(rng, text=tr("path.inside"), variable=var, command=lambda: self.put_inside(var.get()))
        tick.grid(row=0, column=0, sticky="w")
        Tooltip(tick, tr("path.tip_inside"))
        self.ticks["inside"] = (tick, var)
        self.range_names = {}
        for c, key in ((1, "low"), (4, "high")):
            lb = ttk.Label(rng, text=tr(f"path.{key}"), foreground=look.HINT)
            lb.grid(row=0, column=c, sticky="e", padx=(10, 3))
            self.number(rng, 0, c + 1, key, tr(f"path.tip_{key}"), (1, 12, 1), label=lb, width=5)
            self.range_names[key] = ttk.Label(rng, text="", foreground=look.HINT, width=5)
            self.range_names[key].grid(row=0, column=c + 2, sticky="w", padx=(4, 0))

        self.point_label = self.label(box, 10, LETTERS[1])
        Tooltip(self.point_label, tr("path.tip_point"))
        end = ttk.Frame(box)
        end.grid(row=10, column=1, sticky="w", pady=(6, 2))
        for c, key, steps in ((0, "bar", (1, 4, 1)), (2, "beat", (1, 4, 1)), (4, "tick", (1, 10, 1)),
                              (6, "key", (1, 12, 1))):
            lb = ttk.Label(end, text=tr(f"path.{key}"), foreground=look.HINT)
            lb.grid(row=0, column=c, sticky="e", padx=(8 if c else 0, 3))
            self.number(end, 0, c + 1, key, tr(f"path.tip_{key}"), steps, label=lb, width=5)
        self.key_name = ttk.Label(end, text="", foreground=look.HINT, width=6)
        self.key_name.grid(row=0, column=8, sticky="w", padx=(4, 0))
        self.add_button = ttk.Button(end, text=tr("path.add_point"), command=self.add_point)
        self.add_button.grid(row=0, column=9, sticky="w", padx=(6, 0))
        Tooltip(self.add_button, tr("path.tip_add_point"))

        self.info = ttk.Label(box, text="", foreground=look.HINT, wraplength=round(440 * s))  # (the window keeps its size)
        self.info.grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(row, text=tr("path.reset"), command=self.reset)
        reset.pack(side="left")
        Tooltip(reset, tr("path.tip_reset"))
        ttk.Button(row, text=tr("path.accept"), command=self.accept).pack(side="right")
        self.plain_button = ttk.Button(row, text=tr("path.plain"), command=self.plain_shapes)
        self.plain_button.pack(side="right", padx=(0, 8))
        Tooltip(self.plain_button, tr("path.tip_plain"))

    def label(self, box, r, text):
        lb = ttk.Label(box, text=text)
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=(6, 2))
        return lb

    def choice(self, box, r, key, text, column=0):
        """A row with a dropdown (CHOICES[key], texts path.<choice>); returns its frame (more can go beside it)."""
        if text is not None:
            self.label(box, r, text)
        f = ttk.Frame(box)
        f.grid(row=r, column=column + 1, sticky="w", pady=(6, 2), padx=(10, 0) if column else 0)
        c = self.combos[key] = ttk.Combobox(f, state="readonly", width=16,
                                            values=[tr(f"path.{v}") for v in CHOICES[key]])
        c.grid(row=0, column=0, sticky="w")
        c.bind("<<ComboboxSelected>>", lambda e: self.put_choice(key, CHOICES[key][e.widget.current()]))
        Tooltip(c, tr(f"path.tip_{key}"))
        return f

    def number(self, parent, r, c, key, tip, steps, label=None, width=6):
        var = self.vars[key] = tk.StringVar()
        e = self.entries[key] = ttk.Entry(parent, textvariable=var, width=width, justify="center")
        e.grid(row=r, column=c, sticky="w", pady=(4 if r else 0, 0))
        e.bind("<Return>", lambda ev: (self.on_entry(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_entry(key))
        Scrub(self.app, [(e, var, lambda: self.on_entry(key, False))], steps, label=label, drag_box=label is None)
        Tooltip(e, tip)

    # ---- the group: its box, each shape's page

    def before_notes(self, i):
        """Shape i's notes before this page (all of them while it's still a new one)."""
        sh = self.app.shapes[i]
        fx = sh.get("fx") or []
        k = self.at.get(i, len(fx)) if self.at else len(fx)
        bare = {key: v for key, v in sh.items() if key != "fx"}
        return self.app.notes_of(dict(bare, fx=fx[:k]) if k else bare)

    def boxes(self):
        """{shape: (first start, last end, lowest key, highest key)} of the selected shapes' notes before this page."""
        out = {}
        for i in self.targets:
            if i < len(self.app.shapes):
                a = self.before_notes(i)
                if len(a):
                    out[i] = (int(a[:, 0].min()), int(a[:, 1].max()), int(a[:, 2].min()), int(a[:, 2].max()))
        return out

    def group(self):
        memo = getattr(self, "group_memo", None)
        if memo:  # (worked out once per preview: every shape's page asked for it, slow with many shapes)
            return memo[0]
        boxes = self.boxes()
        if not boxes:
            return None
        b = list(boxes.values())
        return (min(x[0] for x in b), max(x[1] for x in b), min(x[2] for x in b), max(x[3] for x in b)), boxes

    def frame(self):
        """The group's box as ((beat it starts, lowest key), (beats, keys) it spans), or None (no notes). The points
        are kept from its start, lowest key."""
        g = self.group()
        if g is None:
            return None
        (t0, t1, lo, hi), ppq = g[0], self.app.ppq
        return (t0 / ppq, lo), ((t1 - t0) / ppq, hi - lo)

    def points(self):
        """A, B, C, ... as (beat, key) on the piano roll, or None (B not placed / no notes)."""
        fr, cl = self.frame(), clean_path(self.cfg)
        if fr is None or cl is None:
            return None
        (b0, k0), size = fr
        return [(b0 + b, k0 + p) for b, p in all_points(cl, size)]

    def page_cfg(self, i, cfg):
        """Shape i's page: where the group's box is from its own notes, and how many colours there are."""
        if cfg is None:
            return None
        g = self.group()
        own = g and g[1].get(i)
        ppq = self.app.ppq
        grp = [(g[0][0] - own[0]) / ppq, (g[0][1] - own[1]) / ppq, g[0][2] - own[2], g[0][3] - own[3]] if own else \
            [0.0, 0.0, 0.0, 0.0]
        return dict(cfg, grp=grp, most=16 if self.app.all16() else 15)

    def alike(self, fx):
        return [dict(st, cfg={k: v for k, v in st["cfg"].items() if k not in GROUP_ONLY})
                if st["tool"] == self.KEY and st.get("cfg") else st for st in fx]

    # ---- the points

    def placing(self):
        """Is a point still to be clicked on the piano roll (B, or one added)? (Until then it follows the mouse
        there: follow.)"""
        return bool(self.targets) and not self.idle and (not self.cfg.get("pts") or self.loose or self.adding)

    def lists(self):
        """Copies of the points and the middle points (as many)."""
        pts = [list(p) for p in self.cfg.get("pts") or []]
        mids = list(self.cfg.get("mids") or [])
        return pts, [None if m is None else list(m) for m in (mids + [None] * len(pts))[:len(pts)]]

    def from_box(self, beat, key):
        """(beat, key) on the piano roll -> from the group's start, lowest key; or None (no notes)."""
        fr = self.frame()
        return None if fr is None else [round(beat - fr[0][0], 9), round(key - fr[0][1], 9)]

    def take_typing(self):
        """A number typed in a box without Enter counts before a piano roll click / move acts (user)."""
        if self.taking:
            return
        try:
            f = self.focus_get()
        except (KeyError, tk.TclError):
            return
        for key, e in self.entries.items():
            if e is f:
                self.taking = True
                try:
                    self.on_entry(key)
                finally:
                    self.taking = False
                return
        for name, (var, e) in list(self.pat_boxes.items()):  # (the formula's Loops / numbers too)
            if e is f:
                self.taking = True
                try:
                    self.put_formula_number(name)
                finally:
                    self.taking = False
                return

    def drop_loose(self):
        """A point never clicked (still following the mouse) goes; B too, and then its path page (user)."""
        loose, self.adding = self.loose, False
        if not loose:
            return
        self.loose = False
        pts, mids = self.lists()
        if len(pts) > 1:
            self.cfg.update(pts=pts[:-1], mids=mids[:-1])
            self.pick = min(self.pick, len(pts) - 2)
            self.preview()
        else:
            self.cfg.update(pts=None, mids=None)
            if self.at is not None:
                ToolWindow.remove_page(self)

    def accept(self):
        self.drop_loose()
        g = self.graph_window
        if g and g.job:  # (the last change on the graph counts)
            g.after_cancel(g.job)
            g.store()
        super().accept()

    def plain_shapes(self):
        """Turn into plain shapes: each copy (not the shape itself) becomes a shape of its own notes, right after its
        shape in the list, exactly as shown: the steps after this page (flips, turns, Claw, Strum, Sustain...) already
        done on it, so it has none (user's pick); the page goes (the shape = the first copy, its drawing still
        editable, its other steps kept). The window closes; one undo step after the path's own."""
        self.catch_up()
        self.drop_loose()
        if not self.plain_ok():
            return
        app, ppq = self.app, self.app.ppq
        if app.notes_late:  # (notes still catching up with a drag: made now, so the colours shown are known)
            app.shapes_changed(now=True)
        # (outside Multi channel each copy keeps its Multi channel colours too, like Turn into plain notes: hunt)
        use10, shown = app.multi_rendered()
        made = {}
        for i in self.targets:
            sh = app.shapes[i]
            fx, k = sh.get("fx") or [], self.at.get(i)
            if k is None or k >= len(fx) or fx[k].get("off") or clean_path(fx[k].get("cfg")) is None:
                continue
            notes, tracks = tagged_notes(sh, k, ppq, app.keys)
            if not len(notes) or notes.shape[1] < 5:
                continue
            tag = notes[:, -1]
            slots = self.shown_slots(notes, tag, tracks, i, shown)
            order = np.argsort(tag, kind="stable")  # (each copy's notes cut out once: one scan per copy was slow)
            cuts = np.flatnonzero(np.diff(tag[order])) + 1
            news = []
            for x in np.split(order, cuts):
                if not len(x) or tag[x[0]] == 0:
                    continue
                rows = np.column_stack([notes[x, :4], slots[x], np.full(len(x), i)])
                # (like Apply mask: the colours as they show, user; channel 10 kept in use only if a note is on it,
                # like Turn into plain notes: hunt)
                new = without10(app.copied(rows, False, True), use10)
                new["made"] = "path"
                new["name"] = tr("path.plain_name", name=app.shape_label(sh), n=len(news) + 2)
                news.append(new)
            made[i] = news
        total = sum(len(v) for v in made.values())
        if not total:
            return
        if total > PLAIN_ASK and "plain_path" not in app.big_skip:  # (thousands of shapes make the list slow)
            go, skip = big_ask.dialog(self, tr("big_ask.title"), tr("path.plain_big", n=f"{total:,}") + "\n\n" +
                                      tr("big_ask.go"), False)
            if go and skip:
                app.big_skip.add("plain_path")
            if not go:
                return
        self.tidy()
        self.settle()
        app.roll.cancel_draft()
        app.push_undo(name=tr("path.plain"))
        out, picked = [], []
        for i, sh in enumerate(app.shapes):
            if i not in made:
                out.append(sh)
                continue
            self.put_fx(sh, [st for j, st in enumerate(sh.get("fx") or []) if j != self.at[i]])
            picked += range(len(out), len(out) + 1 + len(made[i]))  # (the shape and its copies, selected)
            out += [sh] + made[i]
        self.close()
        app.shapes[:] = out
        app.select_many(picked, picked[0])
        app.shapes_changed()
        app.status.config(text=trn("path.plain_done", total, n=total))

    def shown_slots(self, notes, tag, tracks, i, r):
        """Each note's channel slot in Multi channel now (r: the rendered notes, join_split.multi_rendered; shape i's
        notes found by start, end and key); one not found there: its copy's most common one, else its colour group."""
        r = r[r[:, 5] == i] if r is not None and len(r) else np.zeros((0, 6), np.int64)
        own = tracks if tracks is not None else np.zeros(len(notes), np.int64)
        both = np.concatenate([r[:, :3], notes[:, :3]]).astype(np.int64)
        if not len(r):
            return np.asarray(own, np.int64).copy()
        ids = np.unique(both, axis=0, return_inverse=True)[1].ravel()
        by_id = np.full(ids.max() + 1, -1, np.int64)
        by_id[ids[:len(r)]] = r[:, 4]
        out = by_id[ids[len(r):]]
        lost = out < 0
        if lost.any():
            found = ~lost  # (each copy's most common slot: counted all at once, not copy by copy)
            pairs, counts = np.unique(np.column_stack([tag[found], out[found]]), axis=0, return_counts=True)
            best = {}
            for (t, s), c in zip(pairs.tolist(), counts.tolist()):
                if c > best.get(t, (0, 0))[0]:
                    best[t] = (c, s)
            out[lost] = [best[t][1] if t in best else int(o) for t, o in zip(tag[lost].tolist(), own[lost].tolist())]
        return out

    def plain_ok(self):
        """Can Turn into plain shapes work: a page with a placed path, switched on."""
        st = self.page_step()
        return (self.at is not None and bool(self.targets) and st is not None and not st.get("off")
                and clean_path(self.cfg) is not None and not self.placing())

    def sync(self):
        if sorted(self.app.note_tool_sels()) != self.targets:  # (another selection: a point not clicked goes)
            self.drop_loose()
        super().sync()

    def follow(self, beat, key, k):
        """The point still to be placed follows the mouse on the piano roll: the copies show there (no step inside)."""
        self.take_typing()
        pt = self.from_box(beat, key)
        if pt is None:
            return
        pts, mids = self.lists()
        if self.loose:
            pts[-1] = pt
        else:
            pts.append(pt)
            mids.append(None)
        self.loose, self.adding = True, False
        self.pick = len(pts) - 1
        self.cfg.update(pts=pts, mids=mids, k=k)
        self.show()
        self.preview(False)

    def place(self, beat, key, k):
        """A click on the piano roll places the point following the mouse (B, or one added)."""
        self.take_typing()
        pt = self.from_box(beat, key)
        if pt is None:
            return
        pts, mids = self.lists()
        if self.loose:
            pts[-1] = pt
        else:
            pts.append(pt)
            mids.append(None)
        self.loose = self.adding = False
        self.pick = len(pts) - 1
        self.cfg.update(pts=pts, mids=mids, k=k)
        self.show()
        self.preview()
        self.undo.mark()

    def add_point(self):
        """Add point: a new point after the last one follows the mouse on the piano roll until a click."""
        if self.cfg.get("pts") and not self.placing() and len(self.cfg["pts"]) < MOST_POINTS:
            self.adding = True
            self.show()

    def stop_adding(self):
        """Right-click while an added point follows the mouse: it's dropped. True if so (B can't be)."""
        if not self.cfg.get("pts") or not (self.loose or self.adding) or (self.loose and len(self.cfg["pts"]) < 2):
            return False
        if self.loose:
            pts, mids = self.lists()
            self.cfg.update(pts=pts[:-1], mids=mids[:-1])
        self.loose = self.adding = False
        self.pick = min(self.pick, len(self.cfg["pts"]) - 1)
        self.show()
        self.preview()
        return True

    def remove_point(self, i):
        """Point i (0 = B) right-clicked: it goes, its two stretches become one straight one. Not the only one."""
        pts, mids = self.lists()
        if len(pts) < 2 or self.placing():
            return
        del pts[i]
        mids = mids[:i] + ([None] if i < len(pts) else []) + mids[i + 2:]
        self.pick = min(self.pick if self.pick < i else max(0, self.pick - 1), len(pts) - 1)
        self.cfg.update(pts=pts, mids=mids)
        self.show()
        self.preview()
        self.undo.mark()

    def calm(self):
        self.loose = self.adding = self.idle = False
        self.pick = 0

    @classmethod
    def open(cls, app):
        """From the right-click menu (the window opened, or brought up): shapes with a path show their last one (user:
        another only with +); none yet = a new path waiting for B."""
        super().open(app)
        w = getattr(app, cls.ATTR)
        if w and w.at is None and w.idle and not w.has_pages():
            w.new_page()

    def retarget(self):
        """Another selection (user: no new path unless asked for in the menu / with +): its last path shows, or
        nothing waits for a click."""
        self.calm()
        super().retarget()
        self.show_last()

    def remove_page(self):
        """A path taken off (its tab's ×): the last one left shows, or nothing waits for a click (user: no new path
        made by moving the mouse)."""
        if self.at is None:
            return
        super().remove_page()
        self.show_last()

    def show_last(self):
        """The selected shapes' last path page shows; none: nothing waits for a click."""
        self.calm()
        last = [k for k, st in enumerate(self.first_fx()) if st["tool"] == self.KEY]
        if last and self.same():
            self.pick_page(last[-1])
        else:
            self.idle = bool(self.targets)
            self.show()

    def pick_page(self, k):
        self.calm()
        super().pick_page(k)
        if self.cfg.get("pts") is None and self.cfg.get("b") is not None:  # (the first days' pages)
            self.cfg = clean_path(self.cfg) or self.cfg
            self.show()

    def new_page(self):
        self.drop_loose()
        self.calm()
        super().new_page()

    def put_state(self, state):
        self.loose = self.adding = False
        super().put_state(state)

    def reset(self):
        self.calm()
        super().reset()

    def set_point(self, which, beat, key, k, done):
        """A point ("pt", i: 0 = B) or a stretch's middle point ("mid", i) put at (beat, key) on the piano roll
        (k = beats per key on screen now). A middle point on a straight path makes it a curve; a point moved takes
        the bends beside it along by half as much."""
        self.take_typing()
        pt = self.from_box(beat, key)
        if pt is None:
            return
        what, i = which
        pts, mids = self.lists()
        if what == "mid":
            if self.cfg["kind"] == "straight":  # (bends hidden by Straight stay straight: only this one bends)
                self.cfg["kind"] = "curve"
                mids = [None] * len(mids)
            mids[i] = pt
        else:
            dx, dy = pt[0] - pts[i][0], pt[1] - pts[i][1]
            for s in (i, i + 1):  # (the bends kept as they were)
                if s < len(mids) and mids[s] is not None:
                    mids[s] = [mids[s][0] + dx / 2, mids[s][1] + dy / 2]
            pts[i] = pt
            self.pick = i
        self.cfg.update(pts=pts, mids=mids, k=k)
        self.show()
        self.preview(done)
        self.undo.mark(which, end=done)

    def local_step(self, redo):
        """Ctrl+Z / Ctrl+Y pressed on the piano roll while open (App.key_undo): the points are dragged there, so this
        window's own steps go first (user); none left = False (the main window's undo). A point held is let go. A
        shape changed on the piano roll after them: that step goes first, and the window's steps before it are kept
        in the step it made then (user: Ctrl+Z took a point away, not the change)."""
        roll = self.app.roll
        if roll.drag and roll.drag[0] == "path":
            roll.drag = None
        u = self.undo
        if u.main_newer():
            u.reset()
            return False
        if u.get() != u.states[u.at]:
            u.mark()
        if not (u.at < len(u.states) - 1 if redo else u.at > 0):
            return False
        self.undo_key(1 if redo else -1)
        return True

    # ---- settings

    def put_choice(self, key, value):
        if key == "speed" and value == "custom":
            wheeled, self.wheeled = self.wheeled, False
            if value != self.cfg["speed"]:
                if not self.cfg.get("curve"):  # (a curve drawn before is kept)
                    self.cfg["curve"] = start_curve(self.cfg["speed"])
                self.put(key, value)
            if not wheeled:  # (picked with the mouse wheel: Graph… opens it)
                self.open_graph()
            return
        self.put(key, value)  # (Straight keeps the bends, hidden: Curve / Arc bring them back; user)

    def wheel_pick(self):
        """The wheel is about to step the Speed dropdown (its own wheel comes after this)."""
        self.wheeled = True
        self.after_idle(lambda: setattr(self, "wheeled", False))

    def graph_gone(self):
        """Speed isn't Custom any more (after a moment, so a page only passing by doesn't count): its graph closes."""
        self.graph_job = None
        if self.cfg.get("speed") != "custom":
            self.drop_graph()

    def open_graph(self):
        """Speed: Custom's curve window (speed_graph.py)."""
        if self.cfg.get("speed") != "custom":
            return
        if not self.graph_window:
            self.graph_window = SpeedGraph(self)
        self.graph_window.lift()
        self.graph_window.focus_set()

    def drop_graph(self):
        """The graph window closes; a change it hasn't put on the page yet is dropped."""
        g = self.graph_window
        if g:
            if g.job:
                g.after_cancel(g.job)
                g.job = None
            g.close()

    # ---- the formula along the path (pattern.py: the same presets / saved ones / Custom… as a curve's)

    def formula_list(self):
        """The Formula dropdown's choices: none, the presets, the saved ones newest first (read again each time it
        opens)."""
        self.saved = load_patterns("pattern")[::-1]
        set_names(self.formula_box, [tr("roll_menu.none")] + [name for _, name, _, _ in PATTERN_PRESETS],
                  [p["name"] for p in self.saved])

    def pick_formula(self, i):
        if i < 0:  # (the divider)
            return
        old = self.cfg.get("pattern")
        if i <= 0:
            new = None
        elif i <= len(PATTERN_PRESETS):
            new = new_pattern(PATTERN_PRESETS[i - 1][0], self.cfg["k"], old)
        else:
            new = saved_pattern(self.saved[i - 1 - len(PATTERN_PRESETS)], self.cfg["k"], old)
        if new and not old and self.start_loops():
            new["loops"] = self.start_loops()
        if new != old:
            self.put("pattern", new)

    def start_loops(self):
        """A new formula's Loops: about LOOP_COPIES copies a loop (fewer can't show the wave), or None (no path)."""
        cl, fr = clean_path(dict(self.cfg, pattern=None)), self.frame()
        if not cl or not fr:
            return None
        return float(max(1, round((len(shifts(cl, self.app.ppq, fr[1])) - 1) / LOOP_COPIES)))

    def custom_formula(self):
        """Custom…: the formula window used for curves, on this path."""
        from window.pattern_dialog import FormulaDialog
        if self.cfg.get("pts"):
            FormulaDialog(PathHost(self), "pattern")

    def put_formula_number(self, name, done=True):
        """A formula number box (Loops first): typed, stepped or dragged."""
        p = self.cfg.get("pattern")
        var, e = self.pat_boxes[name]
        try:
            v = float(calc(var.get()))
            if not math.isfinite(v) or (name == "loops" and not 0 < v <= MAX_LOOPS):
                raise ValueError
            new = dict(p, loops=v) if name == "loops" else dict(p, vars=dict(p["vars"], **{name: v}))
            loop_points(new)  # (a number the formula can't be worked out with, e.g. sqrt of a minus)
        except (ValueError, ZeroDivisionError, TypeError):
            bad(e)
            return
        good(e)
        if new != p:
            self.put("pattern", new, done)

    def show_formula(self):
        p = self.cfg.get("pattern")
        text = pattern_name(p) if p else tr("roll_menu.none")
        if self.formula_box.get() != text:
            self.formula_box.set(text)
        names = (["loops"] + ([] if p.get("loop") else list(p["vars"]))) if p else []
        if names != self.pat_names:
            for w in self.formula_numbers.winfo_children():
                w.destroy()
            self.pat_boxes, self.pat_names = {}, names
            for c, name in enumerate(names):
                lb = ttk.Label(self.formula_numbers, text=tr("panel_pattern.loops") if name == "loops" else name,
                               foreground=look.HINT)
                lb.grid(row=0, column=2 * c, sticky="e", padx=(10 if c else 0, 3))
                var = tk.StringVar()
                e = ttk.Entry(self.formula_numbers, textvariable=var, width=6, justify="center")
                e.grid(row=0, column=2 * c + 1, sticky="w")
                e.bind("<Return>", lambda ev, n=name: (self.put_formula_number(n), "break")[1])
                e.bind("<FocusOut>", lambda ev, n=name: self.put_formula_number(n))
                Scrub(self.app, [(e, var, lambda n=name: self.put_formula_number(n, False))],
                      (1, 10, 0.1) if name == "loops" else (0.5, 5, 0.1), label=lb)
                tip = tr("panel_pattern.loops_tip") if name == "loops" else tr("panel_pattern.number_tip", name=name)
                for w in (lb, e):
                    Tooltip(w, tip)
                self.pat_boxes[name] = (var, e)
            grid_shown(self.formula_numbers, bool(names))
        for name, (var, e) in self.pat_boxes.items():
            text = fmt(round(p["loops"] if name == "loops" else p["vars"][name], 6))
            if var.get() != text:
                var.set(text)
            if str(e.cget("style")) != "TEntry":
                e.config(style="TEntry")

    def put_inside(self, on):
        """Keep inside ticked: from key 0 to the piano roll's top key, so ticking cuts nothing (256 keys too)."""
        if on:
            self.cfg["low"], self.cfg["high"] = 0, self.app.keys - 1
        self.put("inside", on)

    def put_length(self, snap):
        self.cfg["mode"] = "every"
        self.put("every", snap_beats(snap, self.app.beats))

    def on_entry(self, key, done=True):
        e, var = self.entries[key], self.vars[key]
        try:
            v = float(calc(var.get()))
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        ppq = self.app.ppq
        if key == "every":
            v /= ppq
            ok = EVERY_LIMITS[0] <= v <= EVERY_LIMITS[1] and v * ppq >= 1
        elif key == "count":
            ok = COUNT_LIMITS[0] <= v <= COUNT_LIMITS[1] and v == int(v)
            v = int(v)
        elif key in ("size0", "size1"):
            ok = SIZE_LIMITS[0] <= v <= SIZE_LIMITS[1]
        elif key == "step":
            ok = STEP_LIMITS[0] <= v <= STEP_LIMITS[1] and v == int(v)
            v = int(v)
        elif key in ("low", "high"):
            ok = KEY_LIMITS[0] <= v <= KEY_LIMITS[1] and v == int(v)
            v = int(v)
        else:
            ok = self.picked() is not None and v == int(v)
        if not ok:
            bad(e)
            return
        good(e)
        if key in ("size0", "size1"):
            size = list(self.cfg["size"])
            size[key == "size1"] = v
            if size != self.cfg["size"]:
                self.put("size", size, done)
            return
        if key in ("low", "high"):
            if v != self.cfg[key]:
                other = self.cfg["high" if key == "low" else "low"]
                if (key == "low") == (v > other):  # (crossed the other one: they swap)
                    self.cfg["low"], self.cfg["high"] = sorted((v, other))
                    self.put(key, self.cfg[key], done)
                else:
                    self.put(key, v, done)
            return
        if key in ("every", "count", "step"):
            if v != self.cfg[key]:
                if key in ("every", "count"):
                    self.cfg["mode"] = key
                self.put(key, v, done)
            return
        beat, p = self.picked()
        bar, b, t = self.bar_beat_tick(beat)
        bar, b, t, p = {"bar": (v, b, t, p), "beat": (bar, v, t, p), "tick": (bar, b, v, p),
                        "key": (bar, b, t, v)}[key]
        beat = ((bar - 1) * self.app.beats + (b - 1)) + t / ppq
        base = self.frame()[0]
        if abs(beat - base[0]) > FAR or abs(p - base[1]) > FAR:
            bad(e)
            return
        if (beat, p) != self.picked():
            self.set_point(("pt", self.pick), beat, p, self.cfg["k"], done)

    def picked(self):
        """The picked point as (beat, key) on the piano roll, or None (not placed)."""
        pts = self.points()
        return pts[min(self.pick, len(pts) - 2) + 1] if pts else None

    def bar_beat_tick(self, beat):
        """beat -> (bar, beat in it, tick in it) as the status line shows them (bar and beat from 1); before the
        song's start: bar 0 and less."""
        ppq, beats = self.app.ppq, self.app.beats
        ticks = round(beat * ppq)
        bar, rest = divmod(ticks, ppq * beats)
        return bar + 1, rest // ppq + 1, rest % ppq

    def show(self):
        """The window shows self.cfg (only what differs is changed)."""
        c = self.cfg

        def put_text(key, text):
            if self.vars[key].get() != text:
                self.vars[key].set(text)
            if str(self.entries[key].cget("style")) != "TEntry":
                self.entries[key].config(style="TEntry")

        def config(w, **opts):
            if any(str(w.cget(k)) != str(v) for k, v in opts.items()):
                w.config(**opts)

        for key, box in self.combos.items():
            text = tr(f"path.{c[key]}")
            if box.get() != text:
                box.set(text)
        if self.mode.get() != c["mode"]:
            self.mode.set(c["mode"])
        ppq = self.app.ppq
        length = next((snap_text(v) for v in LENGTHS if abs(snap_beats(v, self.app.beats) - c["every"]) < 1e-9), "")
        if self.length_box.get() != length:
            self.length_box.set(length)
        put_text("every", fmt(round(c["every"] * ppq, 6)))
        put_text("count", str(c["count"]))
        put_text("size0", fmt(round(c["size"][0], 6)))
        put_text("size1", fmt(round(c["size"][1], 6)))
        put_text("step", str(c["step"]))
        for key, (tick, var) in self.ticks.items():
            if var.get() != c[key]:
                var.set(c[key])
        even = speed_of(c) == "even"
        config(self.ticks["half"][0], state="normal" if even and c["anchor"] else "disabled")
        config(self.ticks["restart"][0], state="normal" if not even and c["anchor"] else "disabled")
        config(self.entries["step"], state="normal" if c["colours"] != "keep" else "disabled")
        config(self.combos["way"], state="readonly" if c["flip"] == "each" else "disabled")
        grid_shown(self.graph_button, c["speed"] == "custom")
        if self.graph_window:
            if c["speed"] == "custom":
                self.graph_window.sync()
            elif not self.graph_job:  # (only if it stays so: a main window step shows other pages on the way, hunt)
                self.graph_job = self.after_idle(self.graph_gone)
        self.show_formula()
        for key in ("low", "high"):
            put_text(key, str(c[key]))
            config(self.entries[key], state="normal" if c["inside"] else "disabled")
            config(self.range_names[key], text=note_name(c[key], self.app.edo()))
        n = len(c.get("pts") or [])
        self.pick = max(0, min(self.pick, n - 1))
        adding = self.adding or (self.loose and n > 1)
        config(self.point_label, text=LETTERS[(n if self.adding else self.pick) + 1])
        end = self.picked() if not self.adding else None
        bar_beat = self.bar_beat_tick(end[0]) if end else None
        for key, v in zip(("bar", "beat", "tick", "key"), (*bar_beat, end[1]) if end else (None,) * 4):
            put_text(key, "" if v is None else fmt(round(v, 6)))
            config(self.entries[key], state="normal" if end else "disabled")
        name = note_name(round(end[1]), self.app.edo()) if end and 0 <= round(end[1]) < 256 else ""
        config(self.key_name, text=name)
        self.show_plain()
        config(self.add_button, state="normal" if n and not self.placing() and n < MOST_POINTS else "disabled")
        multi = self.app.channel_mode.get() == "auto"
        note = "" if c["colours"] == "keep" or multi else tr("path.needs_multi")
        config(self.colour_note, text=note)
        grid_shown(self.colour_note, bool(note))  # (no empty row while there's nothing to say)
        fr = self.frame()
        if not self.targets or fr is None:
            info = ""
        elif self.idle:  # (shapes whose paths differ: + gives them one together, user)
            info = tr("path.idle_differ") if self.has_pages() else tr("path.idle")
        elif adding:
            info = tr("path.click_point", name=LETTERS[n + 1 if self.adding else n])
        elif self.placing():
            info = tr("path.click_b")
        else:
            cl = clean_path(c)
            copies = len(shifts(cl, ppq, fr[1])) if cl else 0
            info = trn("path.n_copies", copies, n=copies) if cl else ""
            if cl and cl["flip"] == "mirror" and peak(cl, fr[1]) is None:
                info += "\n" + tr("path.no_peak")
        config(self.info, text=info)
        self.app.roll.request_redraw()

    def show_plain(self):
        """Turn into plain shapes: only on a page with a placed path (the page is made at the first preview)."""
        on = self.plain_ok()
        if str(self.plain_button.cget("state")) != ("normal" if on else "disabled"):
            self.plain_button.config(state="normal" if on else "disabled")
        can = bool(self.targets) and not self.idle and bool(self.cfg.get("pts")) and not self.placing()
        if str(self.custom_button.cget("state")) != ("normal" if can else "disabled"):  # (Custom…: once B is placed)
            self.custom_button.config(state="normal" if can else "disabled")

    def on_off(self):
        super().on_off()
        self.show_plain()

    def preview(self, now=True):
        self.group_memo = (self.group(),)
        try:
            if self.big_ok(now):
                super().preview(now)
        finally:
            self.group_memo = None
        self.show_plain()
        self.app.roll.request_redraw()

    def big_ok(self, now):
        """Past big_ask.BIG notes (more than the shapes had so far): asked first, as other actions do (hunt).
        While a point follows the mouse / is dragged (not now) the notes wait for the click / let go. No = the change
        is put back."""
        cl, fr = clean_path(self.cfg), self.frame()
        if not cl or fr is None:
            self.est = 0
            return True
        n = len(shifts(cl, self.app.ppq, fr[1])) * sum(len(self.before_notes(i)) for i in self.targets) * max(
            1.0, max(cl["size"]) / 100)
        if n <= big_ask.BIG or n <= self.est:
            self.est = n
            return True
        if not now:
            return False
        if big_ask.ask(self.app, "notes", int(n), parent=self):
            self.est = n
            return True
        self.loose = self.adding = False
        self.put_state(self.undo.states[self.undo.at])
        return False

    def close(self):
        if self.graph_job:
            self.after_cancel(self.graph_job)
            self.graph_job = None
        self.drop_graph()
        super().close()
        self.app.roll.request_redraw()


class PathHost(FormulaHost):
    """Custom… (pattern_dialog.py) on the path window's page: the holder is its settings (cfg "pattern"). Changes
    show at once; Apply = one step in the window's own undo (Accept makes it the piano roll's)."""
    layers = ("pattern",)

    def __init__(self, win):
        self.win = win
        self.app, self.parent = win.app, win

    def targets(self):
        return [self.win.cfg]

    def fresh(self, holder, layer):
        return {"k": self.win.cfg["k"], "scale": 1.0}

    def start_loops(self):
        return self.win.start_loops()

    def changed(self, final=True):
        self.win.show()
        self.win.preview(final)

    def commit(self, snap, name):
        self.changed()
        self.win.undo.mark()

    def loop_length(self, holder, pat):
        """One loop along the whole path, in keys as the piano roll looked."""
        cl, fr = clean_path(dict(holder, pattern=None)), self.win.frame()
        if cl is None or fr is None or pat.get("loops", 0) <= 0:
            return 0.0
        pts = track(cl, fr[1], 128)[0] / [cl["k"], 1.0]
        return float(np.hypot(*np.diff(pts, axis=0).T).sum()) / pat["loops"]


open_path = PathWindow.open
