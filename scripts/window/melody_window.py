"""Fill with tempo jumps (PLAN.md, planned feature #12; Conductor pane right-click): the area = the pane's kept
Select box, the jumps at each melody note start (notes/melody.py) or every snap step. Melody = the highlighted track
of the track list (only its selected notes: tick); All tracks = only Every snap step. While open the notes it follows
light up in the piano roll (roll/roll_melody.py). Stays open and follows the highlight, selection and area like Fill
gaps' window; checked every WATCH_MS (cheap) instead of being told by every place that can change them."""

import threading
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc
from notes.engine import snap_name, snap_name_beats
from notes.loaded import gather_box
from notes.melody import MOST_ANCHORS, REST, SHORTEST, anchor_count, melody_runs, snap_runs
from window import look
from window.load_extract import boxes_now
from window.widgets import remember_place

WATCH_MS = 250
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
        self.at_snap = ttk.Radiobutton(box, text="", value="snap", variable=self.at, command=self.sync)
        self.at_snap.pack(anchor="w", padx=(10, 0), pady=(4, 0))
        self.rules = []  # (label, box) of the melody's rules: greyed with Every snap step
        self.shortest = self.length_row(box, "melody.shortest", getattr(app, "melody_shortest", "1/32"))
        self.rest = self.length_row(box, "melody.rest", getattr(app, "melody_rest", "1/4"))
        self.hint = ttk.Label(box, text="", wraplength=int(340 * app.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(10, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text=tr("merge_window.close"), command=self.close).pack(side="right")
        self.go = ttk.Button(row, text=tr("melody.fill"), command=self.fill)
        self.go.pack(side="right", padx=(0, 6))
        self.go.state(["disabled"])  # (group B makes the tempo points)
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
        """A rule row: words + a box taking ticks or a note length ("1/32", like the Snap list)."""
        row = ttk.Frame(parent)
        row.pack(anchor="w", pady=(6, 0))
        lb = ttk.Label(row, text=tr(key))
        lb.pack(side="left")
        var = tk.StringVar(value=start)
        e = ttk.Entry(row, textvariable=var, width=7)
        e.pack(side="left", padx=(6, 0))
        unit = ttk.Label(row, foreground=look.HINT)
        unit.pack(side="left", padx=(4, 0))

        def put_unit(*_):  # ("ticks" after a number, nothing after a note length)
            unit.config(text="" if snap_name(var.get()) else tr("melody.ticks"))
            self.sync()
        var.trace_add("write", put_unit)
        unit.config(text="" if snap_name(start) else tr("melody.ticks"))
        self.rules.append((lb, e))
        return var

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
        app.melody_shortest, app.melody_rest = self.shortest.get(), self.rest.get()
        area = pane.bands_reach() if pane.bands else None
        track, ch, name = self.track_now()
        step = app.snap_beats()
        only = self.only_sel.get()
        has_sel = any(i < len(app.shapes) for i in app.sels) or bool(app.roll.kept_box())
        lkey = self.loaded_key(area[0], area[1], only) if area and track is not None and at == "melody" else None
        seen = (area, track, ch, at, only, has_sel, step, self.shortest.get(), self.rest.get(), tuple(sorted(app.sels)),
                id(app.drawn), app.ppq, lkey, self.reading and self.reading["finished"])
        if seen == self.seen:
            return
        self.seen = seen
        snap_text = app.snap.get() if step else None
        self.at_snap.config(text=tr("melody.at_snap", step=snap_text) if snap_text else tr("melody.at_snap_plain"))
        self.at_snap.state(["!disabled"] if step else ["disabled"])
        self.area.config(text=tr("melody.area", a=pane.place_text(area[0]), b=pane.place_text(area[1]))
                         if area else tr("melody.no_area"))
        self.follow.config(text=tr("melody.follows", name=name))
        melody_ok = track is not None
        self.at_melody.state(["!disabled"] if melody_ok else ["disabled"])
        if at == "melody" and not melody_ok and step:  # (All tracks: only the snap steps)
            self.at.set("snap")
            return self.sync()
        on_melody = at == "melody" and melody_ok
        self.only_tick.state(["!disabled"] if on_melody and has_sel else ["disabled"])
        if not has_sel and only:
            self.only_sel.set(False)
            only = False
        for lb, e in self.rules:
            for w in (lb, e):
                w.state(["!disabled"] if on_melody else ["disabled"])
        self.runs, self.lit, problem = [], None, None
        shortest, rest = self.beats_of(self.shortest, SHORTEST), self.beats_of(self.rest, REST)
        if area is None:
            problem = tr("melody.need_area")
        elif at == "snap":
            if not step:
                problem = tr("melody.snap_off")
            else:
                self.runs = snap_runs(step, area[0], area[1])
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
            text = trn("melody.count", n, n=n)
            if at == "melody" and len(self.runs) > 1:
                text += "\n" + trn("melody.runs", len(self.runs), n=len(self.runs))
            self.hint.config(text=text, foreground=look.HINT)
        else:
            self.hint.config(text=problem, foreground=look.WARN)
        app.roll.request_redraw()  # (the notes it follows light up)

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
        pass  # (group B)

    def close(self):
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
