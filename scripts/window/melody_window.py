"""Fill with tempo jumps (PLAN.md, planned feature #12; Conductor pane right-click): the area = the pane's kept
Select box, the jumps at each melody note start (notes/melody.py) or every snap step. Melody = the highlighted track
of the track list (only its selected notes: tick); All tracks = only Every snap step. While open the notes it follows
light up in the piano roll (roll/roll_melody.py). Stays open and follows the highlight, selection and area like Fill
gaps' window; checked every WATCH_MS (cheap) instead of being told by every place that can change them."""

import copy
import random
import threading
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes.engine import snap_name, snap_name_beats
from notes.loaded import gather_box
from notes.melody import (CATCH_LEAST, CATCH_SHAPES, HOLD, HOWS, MOST_ANCHORS, REST, SHAPES, SHORTEST, anchor_count,
                          fill_clip, heard_points,
                          melody_runs, snap_runs)
from notes.tempo import MAX_BPM, MIN_BPM, drawn_bpm, plans
from window import look
from window.jump_areas import JumpChain
from window.jump_graph import JumpGraph, graph_pts
from window.load_extract import boxes_now
from window.widgets import Scrub, remember_place, unhighlight

WATCH_MS = 250
# the words round the two BPM boxes, by shape (None = one box: the second hidden)
WORDS = {"burst": ("melody.w_from", "melody.w_to", "melody.bpm"), "jump": ("melody.w_jump", None, "melody.bpm"),
         "updown": (None, "melody.w_and", "melody.bpm"), "random": ("melody.w_between", "melody.w_and", "melody.bpm"),
         "drawn": ("melody.w_between", "melody.w_and", "melody.bpm")}  # (its graph: lower box to higher box)
PREVIEW_MOST = 500  # jumps: more = no faint preview line (each change would wait on it)
PREVIEW_CATCH = 200  # the same for the catch-up way (each catch-up worked out, tried up to 3 times)
MOST_LOADED = 5_000_000  # a loaded track's notes read for one area at most (a denser area: refused, said why)


class MelodyWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.seen = None  # what the anchors were worked out for
        self.runs = []  # melody_runs / snap_runs' answer for the area
        self.lit = None  # (starts, ends, keys) in beats of the notes followed, for the piano roll; None = none
        self.reading = None  # a loaded track's notes being read: {"key", "got", "error", "finished"}
        self.loaded_got = None  # (key, starts, ends, keys) of the last read
        app.melody_window = self
        self.title(tr("melody.title"))
        self.transient(app)
        self.resizable(False, False)
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.area = ttk.Label(box, text="")
        self.area.pack(anchor="w")
        ttk.Label(box, text=tr("melody.jump_at")).pack(anchor="w", pady=(10, 2))
        self.at = tk.StringVar(value=getattr(app, "melody_at", "melody"))
        self.at_melody = ttk.Radiobutton(box, text=tr("melody.at_melody"), value="melody", variable=self.at,
                                         command=self.sync)
        self.at_melody.pack(anchor="w", padx=(10, 0))
        self.follow = ttk.Label(box, text="", foreground=look.HINT)
        self.follow.pack(anchor="w", padx=(30, 0))
        self.only_sel = tk.BooleanVar(value=False)
        self.only_tick = ttk.Checkbutton(box, text=tr("melody.only_selected"), variable=self.only_sel,
                                         command=self.sync)
        self.only_tick.pack(anchor="w", padx=(30, 0))
        # every snap step: its own step (user 2026-10-29), a note length or ticks, from the toolbar's Snap to start
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(10, 0), pady=(4, 0))
        self.at_snap = ttk.Radiobutton(row, text=tr("melody.at_every"), value="snap", variable=self.at,
                                       command=self.sync)
        self.at_snap.pack(side="left")
        snap = app.snap.get()
        start = snap if snap_name(snap) else fmt(app.snap_ticks())
        self.step = self.length_box(row, getattr(app, "melody_step", start))
        # Jump areas (group C): each click in the pane an edge, two neighbouring edges an area (window/jump_areas.py)
        self.at_clicks = ttk.Radiobutton(box, text=tr("melody.at_clicks"), value="clicks", variable=self.at,
                                         command=self.sync)
        self.at_clicks.pack(anchor="w", padx=(10, 0), pady=(4, 0))
        self.chain = None  # the click chain being made (JumpChain), None = none
        self.preview_seed = random.randrange(1 << 30)  # (Random's picks: the preview's = what Fill makes)
        self.snap_starts = np.zeros(0)  # the melody's note starts in beats (sorted): edges go onto them
        self.rules = []  # (label, box) of the melody's rules: greyed with Every snap step
        self.shortest = self.length_row(box, "melody.shortest", getattr(app, "melody_shortest", "1/32"))
        self.rest = self.length_row(box, "melody.rest", getattr(app, "melody_rest", "1/4"))
        # the tempo made (group B): shape between jumps, its BPMs (the words round the boxes by the shape), how the
        # timing is kept
        ttk.Label(box, text=tr("melody.between")).pack(anchor="w", pady=(10, 2))
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(10, 0))
        self.shape = self.dropdown(row, SHAPES, "melody.shape_", getattr(app, "melody_shape", "burst"))
        self.shape.pack(side="left")
        normal = self.normal()
        self.lo = tk.StringVar(value=fmt(round(normal * 2, 2)))  # (the first box: Burst's "from", the jump's BPM)
        self.hi = tk.StringVar(value=fmt(round(normal / 2, 2)))
        self.bpm_words = [ttk.Label(row) for _ in range(3)]
        self.bpm_boxes = [ttk.Entry(row, textvariable=var, width=8) for var in (self.lo, self.hi)]
        self.bpm_row = [self.bpm_words[0], self.bpm_boxes[0], self.bpm_words[1], self.bpm_boxes[1], self.bpm_words[2]]
        for w in self.bpm_row:
            w.pack(side="left", padx=(4, 0))
        self.graphs = {}  # the small graph windows open (window/jump_graph.py): {"shape" / "strength": JumpGraph}
        self.shape_graph = ttk.Button(row, text=tr("path.graph"), command=lambda: self.open_graph("shape"))
        self.about = ttk.Label(box, foreground=look.HINT, wraplength=int(420 * app.scale), justify="left")
        self.about.pack(anchor="w", padx=(10, 0), pady=(2, 0))  # (what the shape does: user found them unclear)
        # how far each jump goes, over the area (user 2026-10-29): 0 % = the normal tempo, 100 % = the whole jump
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(10, 0), pady=(6, 0))
        ttk.Label(row, text=tr("melody.strength")).pack(side="left")
        ttk.Button(row, text=tr("path.graph"), command=lambda: self.open_graph("strength")).pack(side="left",
                                                                                               padx=(6, 0))
        self.strength_text = ttk.Label(row, foreground=look.HINT)
        self.strength_text.pack(side="left", padx=(6, 0))
        for var in (self.lo, self.hi):
            var.trace_add("write", lambda *_: self.sync())
        # (the boxes themselves drag sideways, user 2026-10-29: 1 BPM, Shift 10, Ctrl 0.1; no undo step)
        Scrub(app, [(e, var, None) for e, var in zip(self.bpm_boxes, (self.lo, self.hi))], (1, 10, 0.1),
              round(MIN_BPM, 3), MAX_BPM, drag_box=True, no_undo=True)
        ttk.Label(box, text=tr("melody.keep_with")).pack(anchor="w", pady=(10, 2))
        self.how = tk.StringVar(value=getattr(app, "melody_how", "audio"))
        for how in HOWS:
            ttk.Radiobutton(box, text=tr("melody.how_" + how), value=how, variable=self.how,
                            command=self.sync).pack(anchor="w", padx=(10, 0))
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(30, 0), pady=(2, 0))
        self.catch_parts = [ttk.Label(row, text=tr("melody.hold"))]
        self.hold = tk.StringVar(value=getattr(app, "melody_hold", fmt(HOLD)))
        self.catch_parts += [ttk.Entry(row, textvariable=self.hold, width=5), ttk.Label(row, text=tr("melody.hold_unit"))]
        self.hold.trace_add("write", lambda *_: self.sync())
        for w in self.catch_parts:
            w.pack(side="left", padx=(0, 4))
        row = ttk.Frame(box)
        row.pack(anchor="w", padx=(30, 0), pady=(2, 0))
        self.catch_parts.append(ttk.Label(row, text=tr("melody.catch_shape")))
        self.catch_parts[-1].pack(side="left")
        self.catch_shape = self.dropdown(row, CATCH_SHAPES, "tempo.shape_", getattr(app, "melody_catch", "flat"))
        self.catch_shape.pack(side="left", padx=(4, 0))
        self.catch_parts.append(self.catch_shape)
        self.hint =ttk.Label(box, text="", wraplength=int(340 * app.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(10, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text=tr("merge_window.close"), command=self.close).pack(side="right")
        self.go = ttk.Button(row, text=tr("melody.fill"), command=self.fill)
        self.go.pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        sels = [i for i in app.sels if i < len(app.shapes)]
        self.only_sel.set(bool(sels) or bool(app.roll.kept_box()))  # (something selected: only that, to start with)
        self.sync()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "melody")
        self.deiconify()
        self.watch = self.after(WATCH_MS, self.watching)

    def length_row(self, parent, key, start):
        """A rule row: words + a length box (length_box)."""
        row = ttk.Frame(parent)
        row.pack(anchor="w", pady=(6, 0))
        lb = ttk.Label(row, text=tr(key))
        lb.pack(side="left")
        var = self.length_box(row, start)
        self.rules.append((lb, var.entry))
        return var

    def length_box(self, row, start):
        """A box taking ticks or a note length ("1/32", like the Snap list), "ticks" after a number; the box drags
        sideways in ticks (1, Shift 10). Returns its variable (.entry = the box)."""
        var = tk.StringVar(value=start)
        e = var.entry = ttk.Entry(row, textvariable=var, width=7)
        e.pack(side="left", padx=(6, 0))
        unit = ttk.Label(row, foreground=look.HINT)
        unit.pack(side="left", padx=(4, 0))

        def put_unit(*_):  # ("ticks" after a number, nothing after a note length)
            unit.config(text="" if snap_name(var.get()) else tr("melody.ticks"))
            self.sync()
        var.trace_add("write", put_unit)
        unit.config(text="" if snap_name(start) else tr("melody.ticks"))
        scrub = Scrub(self.app, [(e, var, None)], (1, 10, 1), 0, None, drag_box=True, no_undo=True)
        # (dragging / Up / Down from a note length: from its ticks)
        scrub.read = lambda text: snap_name_beats(snap_name(text)) * self.app.ppq if snap_name(text) else calc(text)
        return var

    def dropdown(self, parent, values, key, start):
        """A read-only dropdown of values shown as tr(key + value); .value() = the picked one."""
        names = [tr(key + v) for v in values]
        box = ttk.Combobox(parent, values=names, state="readonly", width=max(len(n) for n in names))
        box.set(names[values.index(start)] if start in values else names[0])
        box.value = lambda: values[max(0, names.index(box.get()) if box.get() in names else 0)]
        box.bind("<<ComboboxSelected>>", lambda e: (unhighlight(box), self.sync()))
        return box

    def normal(self):
        """The BPM arriving at the area (the tempo back between runs), the project's start BPM without an area."""
        pane = self.app.tempo_pane
        if not pane.bands:
            return float(pane.base())
        a = pane.bands_reach()[0]
        return float(drawn_bpm(self.app.tempo, pane.base(), [max(0.0, a - 1e-6)])[0])

    def normal_at(self, a, tempo):
        """The drawn line's BPM at beat a itself: a ramp crossing the area's start stays one ramp there (a point off
        its value by a hair made a jump, and a ramp ending at a jump spreads its steps differently: notes before
        the area moved, hunt 2026-10-29). A point ON a (an earlier fill's first jump, a jump): the BPM arriving there,
        as that point is replaced (user 2026-10-29: filling a filled area again took its old 330 jump as the normal
        tempo, so Strength pulled every jump to 330 and they merged into one flat line)."""
        pane = self.app.tempo_pane
        on = a > 0 and any(abs(p[0] - a) < 1e-9 for p in tempo["points"])
        return float(drawn_bpm(tempo, pane.base(), [max(0.0, a - 1e-6) if on else max(0.0, a)])[0])

    def fill_area(self, box, tempo=None):
        """((first, last beat) the fill covers, moved) for the kept box (first, last): a start inside an earlier
        catch-up's teal area moves to where it ends (hunt 2026-10-29: the fill cut it short, it turned red); None
        when nothing is left. tempo: the line it goes on (the project's)."""
        a, b = box
        moved = False
        pane = self.app.tempo_pane
        tempo = self.app.tempo if tempo is None else tempo
        for _ in range(len(tempo["catch"]) + 1):
            hit = next((st["end"] for st, _ in plans(tempo, pane.base(), self.app.ppq)
                        if st["end"] is not None and st["line"] < a - 1e-9 and a < st["end"] - 1e-9), None)
            if hit is None:
                break
            a, moved = hit, True
        return ((a, b) if b - a > 1e-9 else None), moved

    def step_ticks(self):
        """Every [step]: whole ticks, at least 1 (a note length: its ticks at this PPQ, rounded); None when it can't
        be (hunt 2026-10-29: "0.5" made half-tick jumps, a tiny number an error)."""
        name = snap_name(self.step.get().strip())
        if name:
            t = round(snap_name_beats(name) * self.app.ppq)
            return t if t >= 1 else None
        v = self.number(self.step, 1)
        return int(v) if v is not None and v == int(v) else None

    def shortest_gap(self):
        """The shortest gap between jumps (and to a run's end) in ticks."""
        ppq, least = self.app.ppq, float("inf")
        for xs, end in self.runs:
            t = np.round(np.append(np.asarray(xs, float), end) * ppq)
            if len(t) > 1:
                least = min(least, float(np.diff(t).min()))
        return least

    def number(self, var, lo=None, hi=None):
        """A box's number, None when it can't be read or is outside lo..hi."""
        try:
            v = float(calc(var.get().strip()))
        except (ValueError, ZeroDivisionError, TypeError, OverflowError):
            return None
        if v != v or (lo is not None and v < lo) or (hi is not None and v > hi):
            return None
        return v

    def beats_of(self, var, fallback):
        """A rule box's length in beats (a note length or ticks), None when it can't be read."""
        text = var.get().strip()
        name = snap_name(text)
        if name:
            return snap_name_beats(name)
        if not text:
            return fallback
        try:
            v = float(calc(text))
        except (ValueError, ZeroDivisionError, TypeError, OverflowError):
            return None
        return v / max(1, self.app.ppq) if v >= 0 else None

    # ------------------------------------------------------------ what it follows

    def track_now(self):
        """(highlighted track id or None = All tracks, its shown channel or None, its name text)."""
        tl = getattr(self.app, "track_list", None)
        if tl is None or tl.current is None:
            return None, None, tr("melody.all_tracks")
        row = tl.row_of_id(tl.current)
        if row is None:
            return None, None, tr("melody.all_tracks")
        name = tl.name_of(row)
        ch = tl.shown_ch()
        if ch is not None:
            name = tr("melody.track_ch", name=name, ch=ch + 1)
        return tl.current, ch, name

    def own_notes(self, only):
        """(starts, ends, keys) in beats of Spiderweb's notes the piano roll shows for the highlighted track (only:
        the selected shapes'); faint tracks behind it left out."""
        app = self.app
        d = app.drawn
        if not len(d):
            return np.zeros(0), np.zeros(0), np.zeros(0, np.int64)
        keep = (d[:, 5] >= 0) & (d[:, 5] < len(app.shapes))
        if only:
            keep &= np.isin(d[:, 5], np.array(sorted(app.sels), np.int64))
        d = d[keep]
        ppq = max(1, app.ppq)
        return d[:, 0] / ppq, d[:, 1] / ppq, d[:, 2]

    def loaded_key(self, a, b, only):
        """What a loaded track's read is for: (boxes, track, channels), None = no loaded track highlighted."""
        app, tl = self.app, getattr(self.app, "track_list", None)
        lm = app.loaded
        if lm is None or tl is None or getattr(lm, "changed", False):
            return None
        got = tl.shown_file()
        if got is None:
            return None
        n, chs = got
        scale = lm.ppq / max(1, app.ppq)
        if only:
            boxes = [tuple(x) for x in boxes_now(app)] if app.roll.kept_box() else []
            if not boxes:
                return ()
        else:
            boxes = [(max(0, round(a * app.ppq * scale)), round(b * app.ppq * scale), 0, lm.keys - 1)]
        return (id(lm), lm.hide_ver, tuple(boxes), n, tuple(chs))

    def loaded_notes(self, key):
        """(starts, ends, keys) in beats of the loaded track's notes for key; None = still being read (on a
        thread: the window checks again)."""
        if key == ():
            return np.zeros(0), np.zeros(0), np.zeros(0, np.int64)
        if self.loaded_got is not None and self.loaded_got[0] == key:
            return self.loaded_got[1:]
        job = self.reading
        if job is not None and job["key"] == key:
            if not job["finished"]:
                return None
            self.reading = None
            if job["error"] is not None:
                self.loaded_got = (key, None, isinstance(job["error"], MemoryError), None)
                return self.loaded_got[1:]
            got = job["got"]
            scale = self.app.loaded.ppq
            s = np.concatenate([g["s"] for g in got]) / scale if got else np.zeros(0)
            e = np.concatenate([g["e"] for g in got]) / scale if got else np.zeros(0)
            k = np.concatenate([g["lane"] for g in got]) & 255 if got else np.zeros(0, np.int64)
            self.loaded_got = (key, s, e, k)
            return s, e, k
        if job is not None:
            job["cancel"] = True
        lm = self.app.loaded
        _, _, boxes, n, chs = key
        job = self.reading = {"key": key, "got": None, "error": None, "finished": False, "cancel": False}

        def run():
            try:
                job["got"] = [gather_box(lm, {"t0": t0, "t1": t1, "k0": k0, "k1": k1, "tracks": [n],
                                              **({"chans": list(chs)} if chs else {})},
                                         cancel=lambda: job["cancel"], most=MOST_LOADED)
                              for t0, t1, k0, k1 in boxes]
                if sum(len(g["s"]) for g in job["got"]) > MOST_LOADED:
                    raise MemoryError
            except BaseException as e:  # (cancelled / too many / file gone: said in the window)
                job["error"] = e
            job["finished"] = True
        threading.Thread(target=run, daemon=True, name="melody notes").start()
        return None

    # ------------------------------------------------------------ the window

    def sync(self):
        """Everything worked out again when what it depends on changed: the area, the track, the selection, the
        notes, the boxes."""
        if not hasattr(self, "hint"):  # (a box's trace while building)
            return
        app, pane = self.app, self.app.tempo_pane
        at = self.at.get()
        app.melody_at = at
        app.melody_shortest, app.melody_rest, app.melody_step = self.shortest.get(), self.rest.get(), self.step.get()
        clicks = at == "clicks"
        chain = self.live_chain()
        if clicks:  # (the area = the clicked edges' first .. last)
            edges = chain.sorted() if chain else []
            box = (edges[0], edges[-1]) if len(edges) > 1 else None
            base = chain.base if chain else app.tempo
        else:
            if chain:  # (another choice picked: the chain is done)
                self.finish_chain()
                chain = None
            box = pane.bands_reach() if pane.bands else None
            base = app.tempo
        area, moved = self.fill_area(box, base) if box else (None, False)
        track, ch, name = self.track_now()
        step = self.step_ticks()
        only = self.only_sel.get()
        has_sel = any(i < len(app.shapes) for i in app.sels) or bool(app.roll.kept_box())
        if clicks and track is not None:  # (edges go onto the melody's starts anywhere: the whole track read)
            lm = app.loaded
            lkey = self.loaded_key(0.0, (lm.end / max(1, lm.ppq) + 1) if lm else 1.0, only)
        else:
            lkey = self.loaded_key(area[0], area[1], only) if area and track is not None and at == "melody" else None
        how = self.how.get()
        app.melody_shape, app.melody_how, app.melody_hold = self.shape.value(), how, self.hold.get()
        app.melody_catch = self.catch_shape.value()
        made = (self.shape.get(), self.lo.get(), self.hi.get(), how, self.hold.get(), self.catch_shape.get(),
                str(graph_pts(app, "shape")), str(graph_pts(app, "strength")))
        seen = (box, area, track, ch, at, only, has_sel, step, self.shortest.get(), self.rest.get(),
                tuple(sorted(app.sels)), id(app.drawn), app.ppq, lkey, self.reading and self.reading["finished"],
                None if clicks else getattr(app, "tempo_count", 0), self.preview_seed, made)
        if seen == self.seen:
            return
        if chain is not None and self.seen is not None and self.seen[-1] != made and chain.grab is None:
            self.seen = seen  # (the shape / BPMs / way changed: the chain's areas laid again with them)
            chain.lay()
            self.seen = None
            return self.sync()
        self.seen = seen
        for w in self.catch_parts:
            w.state(["!disabled" if how == "catch" else "disabled"])
        if isinstance(self.catch_shape, ttk.Combobox) and how == "catch":
            self.catch_shape.state(["readonly"])
        self.step.entry.state(["!disabled"] if at == "snap" else ["disabled"])
        shape = self.shape.value()
        words = WORDS[shape]
        for w, key in zip(self.bpm_words, words):
            w.config(text=tr(key) if key else "")
        self.about.config(text=tr("melody.about_" + shape))
        one = words[1] is None  # (one BPM: the second box and its word hidden; My drawn one: + its Graph… button)
        want = (self.bpm_row[:2] + self.bpm_row[4:] if one else self.bpm_row) + [self.shape_graph] * (
            shape == "drawn")
        if [w for w in self.bpm_row + [self.shape_graph] if w.winfo_manager()] != want:
            for w in self.bpm_row + [self.shape_graph]:
                w.pack_forget()
            for w in want:
                w.pack(side="left", padx=(4, 0))
        strength = graph_pts(app, "strength")
        hs = [p[1] for p in strength]
        self.strength_text.config(text=tr("melody.strength_flat", p=fmt(round(hs[0] * 100, 1))) if max(hs) == min(hs)
                                  else tr("melody.strength_range", lo=fmt(round(min(hs) * 100, 1)),
                                          hi=fmt(round(max(hs) * 100, 1))))
        for g in self.graphs.values():  # (the BPMs beside My drawn one's graph follow the boxes)
            g.draw()
        self.area.config(text=tr("melody.area", a=pane.place_text(box[0]), b=pane.place_text(box[1]))
                         if box else tr("melody.no_clicks" if clicks else "melody.no_area"))
        self.follow.config(text=tr("melody.follows", name=name))
        melody_ok = track is not None
        self.at_melody.state(["!disabled"] if melody_ok else ["disabled"])
        if at == "melody" and not melody_ok:  # (All tracks: only the snap steps)
            self.at.set("snap")
            return self.sync()
        on_melody = at == "melody" and melody_ok
        self.only_tick.state(["!disabled"] if (on_melody or clicks and melody_ok) and has_sel else ["disabled"])
        if not has_sel and only:
            self.only_sel.set(False)
            only = False
        for lb, e in self.rules:
            for w in (lb, e):
                w.state(["!disabled"] if on_melody else ["disabled"])
        self.runs, self.lit, problem = [], None, None
        shortest, rest = self.beats_of(self.shortest, SHORTEST), self.beats_of(self.rest, REST)
        if clicks:
            return self.sync_clicks(box, area, moved, track is not None, only, lkey)
        if box is None:
            problem = tr("melody.need_area")
        elif area is None:  # (all of it inside a catch-up's area)
            problem = tr("melody.in_catch_all")
        elif at == "snap":
            if not step:
                problem = tr("melody.bad_step")
            else:
                self.runs = snap_runs(step, area[0], area[1], app.ppq)
        elif not melody_ok:
            problem = tr("melody.need_track")
        elif shortest is None or rest is None:
            problem = tr("melody.bad_length")
        else:
            s, e, k = self.own_notes(only)
            if lkey is not None:
                got = self.loaded_notes(lkey)
                if got is None:
                    problem = tr("melody.reading")
                elif got[0] is None:
                    problem = tr("melody.loaded_dense" if got[1] else "melody.loaded_bad")
                else:
                    s, e, k = np.concatenate([s, got[0]]), np.concatenate([e, got[1]]), np.concatenate([k, got[2]])
            if problem is None:
                self.runs = melody_runs(s, e, area[0], area[1], shortest, rest)
                inside = (s >= area[0]) & (s < area[1]) & (e - s >= shortest - 1e-9)
                self.lit = (s[inside], e[inside], k[inside])
        n = anchor_count(self.runs)
        if problem is None and n > MOST_ANCHORS:
            problem = tr("melody.too_many")
        elif problem is None and not n:
            problem = tr("melody.none")
        if problem is None:
            problem = self.shape_problem(self.runs)
        self.go.state(["disabled"] if problem else ["!disabled"])
        if problem is None:
            text = trn("melody.count", n, n=n)
            if at == "melody" and len(self.runs) > 1:
                text += "\n" + trn("melody.runs", len(self.runs), n=len(self.runs))
            if moved:
                text += "\n" + tr("melody.in_catch", at=pane.place_text(area[0]))
            self.hint.config(text=text, foreground=look.HINT)
        else:
            self.hint.config(text=problem, foreground=look.WARN)
        self.make_preview(None if problem else area)
        app.roll.request_redraw()  # (the notes it follows light up)

    def make_preview(self, area):
        """What Fill would lay, as a faint line in the Conductor pane (user 2026-10-29: Fill had to be pressed to see
        anything): worked out on a copy of the line with the same random as Fill; none past PREVIEW_MOST jumps (the
        line there is a wall of jumps anyway) or with nothing to fill."""
        pane = self.app.tempo_pane
        old = pane.fill_preview
        pane.fill_preview = None
        most = PREVIEW_MOST if self.how.get() == "audio" else PREVIEW_CATCH
        if area is not None and anchor_count(self.runs) <= most and pane.box.mode == "visual":
            a, b = area
            clip, _ = self.make_clip(self.app.tempo, self.runs, a, b, seed=self.preview_seed)
            if clip is not None:
                trial = copy.deepcopy(self.app.tempo)
                pane.lay_copies(trial, clip, a, pane.ed_base(), teal=False)
                pane.fill_preview = {"tempo": trial, "worked": plans(trial, pane.base(), self.app.ppq),
                                     "span": (a, b)}
        if old is not None or pane.fill_preview is not None:
            pane.request_redraw()

    def sync_clicks(self, box, area, moved, melody_ok, only, lkey):
        """sync's "Where I click" part: the notes edges go onto (the melody's starts), what the chain makes. Fill
        greyed: the areas are laid as they're clicked."""
        app, pane = self.app, self.app.tempo_pane
        self.make_preview(None)  # (the chain lays its areas as they're clicked: no preview)
        notes, lines = None, []
        if melody_ok:
            s, e, k = self.own_notes(only)
            if lkey is not None:
                got = self.loaded_notes(lkey)
                if got is None:
                    lines.append(tr("melody.reading"))
                elif got[0] is None:
                    lines.append(tr("melody.loaded_dense" if got[1] else "melody.loaded_bad"))
                else:
                    s, e, k = np.concatenate([s, got[0]]), np.concatenate([e, got[1]]), np.concatenate([k, got[2]])
            notes = (s, e, k)
        self.lit = notes
        self.snap_starts = np.unique(notes[0]) if notes is not None else np.zeros(0)
        self.go.state(["disabled"])
        problem = None
        if box is None:
            lines.insert(0, tr("melody.clicks_how"))
        elif area is None:
            problem = tr("melody.in_catch_all")
        else:
            got = self.chain_runs(self.chain.base, self.chain.sorted())
            self.runs = got[1] if got else []
            problem = self.shape_problem(self.runs) if self.runs else tr("melody.in_catch_all")
            if problem is None:
                n = anchor_count(self.runs)
                lines.insert(0, trn("melody.areas", n, n=n))
                if moved:
                    lines.append(tr("melody.in_catch", at=pane.place_text(area[0])))
        if problem is not None:
            self.hint.config(text=problem, foreground=look.WARN)
        else:
            self.hint.config(text="\n".join(lines), foreground=look.HINT)
        pane.request_redraw()
        app.roll.request_redraw()

    def watching(self):
        self.watch = None
        try:
            self.sync()
        finally:
            if self.winfo_exists():
                self.watch = self.after(WATCH_MS, self.watching)

    def ppq_changed(self):
        self.seen = None
        self.sync()

    def fill(self):
        """Fill: the jumps' tempo laid over the area (what was there replaced, one undo step). The catch-up way:
        gaps whose catch-up can't be done are left at the normal tempo (tried on a copy first), the status line says
        how many."""
        self.seen = None
        self.sync()
        if self.go.instate(["disabled"]) or not self.runs:
            return
        app, pane = self.app, self.app.tempo_pane
        if pane.box.mode != "visual":  # (jumps are the visual tempo's, like a Domino paste)
            pane.box.set_mode("visual")
        (a, b), _ = self.fill_area(pane.bands_reach())
        clip, plain = self.make_clip(app.tempo, self.runs, a, b, seed=self.preview_seed)  # (= the preview)
        if clip is None:  # (no catch-up could be made: nothing changed, no undo step)
            app.status.config(text=tr("melody.catch_none"))
            return
        pane.put_copies(clip, a, tr("melody.step"), teal=False)
        self.preview_seed = random.randrange(1 << 30)  # (each Fill a new random: the preview shows the next one)
        n = anchor_count(self.runs)
        text = trn("melody.filled", n, n=n)
        if plain:
            text += " " + trn("melody.left_plain", len(plain), n=len(plain))
        app.status.config(text=text)
        self.seen = None
        self.sync()

    def make_clip(self, tempo, runs, a, b, seed=None):
        """(the copy Fill lays over a..b of the line tempo, the gaps left plain): the window's shape, BPMs and way.
        The catch-up way: gaps whose catch-up can't be done are left at the normal tempo (tried on a copy first);
        none could = (None, plain). seed: the random's (None = a new one)."""
        app, pane = self.app, self.app.tempo_pane
        how = self.how.get()
        lo, hi = self.number(self.lo), self.number(self.hi)
        hi = lo if hi is None else hi  # (one box only: Jump and come back)
        bpm_snap = pane.box.bpm_snap() or 0.0
        seed = random.randrange(1 << 30) if seed is None else seed  # (the same for the tries below)
        # what was heard over the area: Keep the audio plays it (no note moves, hunt 2026-10-29); the catch-up way
        # comes back to the tempo heard at its start (not the shown one: inside a Keep timing part they differ)
        heard = heard_points(tempo, pane.base(), app.ppq, a, b)
        normal = self.normal_at(a, tempo) if how == "audio" else heard[0][1]

        def clip_for(plain):
            return fill_clip(runs, a, b, self.shape.value(), lo, hi, normal, how,
                             self.number(self.hold) or HOLD, self.catch_shape.value(), random.Random(seed),
                             bpm_snap, plain, heard if how == "audio" else None,
                             graph_pts(app, "shape"), strength)
        strength = graph_pts(app, "strength")
        strength = None if all(p[1] == 1.0 for p in strength) else strength  # (100 % all along: as before)
        plain = set()
        clip = clip_for(plain)
        if how == "catch":
            for _ in range(3):  # (a gap left plain changes its neighbours' catch-ups a little: tried again)
                trial = copy.deepcopy(tempo)
                pane.lay_copies(trial, clip, a, pane.ed_base(), teal=False)
                beats = {round(a + c[0], 9): g for c, g in zip(clip["catch"], clip["gaps"])}
                bad = {beats[round(st["line"], 9)] for st, plan in plans(trial, pane.base(), app.ppq)
                       if round(st["line"], 9) in beats and not plan["ok"]}
                if not bad - plain:
                    break
                plain |= bad
                clip = clip_for(plain)
            if not clip["gaps"] and plain:
                return None, plain
        return clip, plain

    # ------------------------------------------------------------ Jump areas (group C)

    def chain_runs(self, tempo, edges):
        """((a, b), runs) of the clicked edges (sorted) on the line tempo: one run, a jump at every edge but the
        last; a start inside an earlier catch-up's teal area moves to its end (as Fill's). None when nothing's
        left."""
        area, _ = self.fill_area((edges[0], edges[-1]), tempo)
        if area is None:
            return None
        a, b = area
        xs = [x for x in edges[:-1] if x >= a - 1e-9]
        return (a, b), ([(np.array(xs, float), float(b))] if xs else [])

    def chain_clip(self, tempo, edges):
        """The copy the chain lays over tempo (its "at" = where), None when the boxes can't make one."""
        got = self.chain_runs(tempo, edges)
        if got is None or not got[1] or self.shape_problem(got[1]):
            return None
        (a, b), runs = got
        clip, _ = self.make_clip(tempo, runs, a, b, seed=self.chain_seed)
        if clip is not None:
            clip["at"] = a
        return clip

    def shape_problem(self, runs):
        """What stops the shape / BPM boxes / way making the jumps of runs (None = fine)."""
        lo, hi = self.number(self.lo, MIN_BPM, MAX_BPM), self.number(self.hi, MIN_BPM, MAX_BPM)
        if lo is None or (hi is None and WORDS[self.shape.value()][1] is not None):
            return tr("melody.bad_bpm", lo=fmt(round(MIN_BPM, 3)), hi=fmt(MAX_BPM))
        if self.how.get() == "catch" and self.number(self.hold, 1, 99) is None:
            return tr("melody.bad_hold")
        old, self.runs = self.runs, runs
        short = self.how.get() == "catch" and self.shortest_gap() < CATCH_LEAST
        self.runs = old
        return tr("melody.catch_short", n=CATCH_LEAST) if short else None

    def edge_window(self):
        """True while clicks in the Conductor pane make edges ("Where I click" picked)."""
        return self.at.get() == "clicks"

    def live_chain(self):
        """The chain being made, if the tempo line is still as it left it (else it's done)."""
        ch = self.chain
        if ch is not None and not ch.alive():
            self.chain = ch = None
            self.app.tempo_pane.request_redraw()
        return ch

    def edge_press(self, e):
        pane = self.app.tempo_pane
        if pane.box.mode != "visual":
            pane.box.set_mode("visual")
        if self.live_chain() is None:
            self.chain = JumpChain(self)
            self.chain_seed = random.randrange(1 << 30)  # (one random per chain: a click doesn't shuffle it)
        self.chain.press(e)
        self.seen = None
        self.sync()

    def edge_drag(self, e):
        ch = self.live_chain()
        if ch is not None:
            ch.drag(e)
            self.seen = None

    def edge_release(self):
        ch = self.live_chain()
        if ch is not None:
            ch.release()
        self.seen = None
        self.sync()

    def finish_chain(self):
        """Enter / Esc / right-click / another tool: the chain done (its tempo stays, ordinary points). True if
        there was one."""
        ch = self.live_chain()
        self.chain = None
        if ch is None:
            return False
        n = max(0, len(set(ch.edges)) - 1)
        if n:
            self.app.status.config(text=trn("melody.areas_done", n, n=n))
        self.app.tempo_pane.request_redraw()
        self.seen = None
        self.sync()
        return True

    def chain_step(self, redo=False):
        """Ctrl+Z / Ctrl+Y mid-chain: its own steps first (the last edge). True when it took one."""
        ch = self.live_chain()
        if ch is None or ch.grab is not None:
            return False
        if not ch.step(redo):
            return False
        self.seen = None
        self.sync()
        return True

    def open_graph(self, kind):
        """My drawn one's / Strength's small graph window (window/jump_graph.py); open already: brought up."""
        g = self.graphs.get(kind)
        if g is not None and g.winfo_exists():
            return g.lift()
        self.graphs[kind] = JumpGraph(self, kind)

    def graph_bpms(self):
        """(lower, higher) of the two BPM boxes, the bottom and top of My drawn one's graph; (None, None) when one
        can't be read."""
        lo, hi = self.number(self.lo, MIN_BPM, MAX_BPM), self.number(self.hi, MIN_BPM, MAX_BPM)
        return (None, None) if lo is None or hi is None else (min(lo, hi), max(lo, hi))

    def close(self):
        for g in list(self.graphs.values()):
            g.close()
        self.finish_chain()
        self.make_preview(None)
        if self.watch:
            self.after_cancel(self.watch)
            self.watch = None
        if self.reading is not None:
            self.reading["cancel"] = True
        self.app.melody_window = None
        self.lit = None
        self.destroy()
        self.app.roll.request_redraw()
        self.app.roll.focus_set()


def open_melody(app):
    w = getattr(app, "melody_window", None)
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        MelodyWindow(app)
