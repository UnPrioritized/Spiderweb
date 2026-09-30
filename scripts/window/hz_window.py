"""The Hz bass window (hzbass.py): a small piano roll where the tones of a Hz bass are placed. A click places a
note, dragging a note moves it, dragging either end changes its length, Delete removes the selected ones. The red
line is the tone travelling through the notes: it jumps at the next note unless its dots are dragged (lead out of
one note, lead in of the next), then it slides.

It follows the main window's selection: the selected custom shape's tones, or (Hz bass tool clicked on empty
space) a new Hz bass that's made with the first note and grows with the notes. Every change is an undo step of the
main window, made when the mouse is let go (the notes on the piano roll are made again then, not while dragging)."""

import copy
import math
import re
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import fmt
from notes.custom import BOX_STROKE, SPAM_FILLS, box_frame, custom_settings
from notes.hzbass import HZ_DEFAULTS, clean_tones, fit_length, holds, hz_of, left_edge, tones_span, voices
from roll.roll_shared import CTRL, SELECTED_COLOR, SHIFT, SLOT_COLORS, note_name
from window.widgets import Tooltip

BLACK = (1, 3, 6, 8, 10)
RED = "#e02020"
POS = r"\d+x\d+\+-?\d+\+-?\d+"  # a remembered size and place


def open_hz(app):
    if app.hz_window:
        app.hz_window.lift()
    else:
        app.hz_window = HzWindow(app)
    app.hz_window.sync()


def hz_made(sh):
    """True for a Hz bass made with the Hz bass tool (hz["own"]): a box that is nothing but its placed tones."""
    hz = (sh or {}).get("hz") or {}
    return bool(hz.get("own") and hz.get("tones"))


def shape_length(sh):
    """How long a custom shape's box is, in beats."""
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    bs = (b0, b1, b2, b1 + b2 - b0)
    return max(bs) - min(bs)


class HzWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("hz.window_title"))
        self.transient(app)
        s = self.s = app.scale
        self.geometry(app.hz_pos if re.fullmatch(POS, app.hz_pos or "") else f"{round(820 * s)}x{round(480 * s)}")
        self.minsize(round(420 * s), round(260 * s))
        self.tones, self.sel = [], set()  # the notes shown (hzbass tones) and which are selected
        self.drag = None
        self.last_len = 1.0  # beats: how long a newly placed note is (the last length used)
        self.kb_w, self.ruler_h = round(44 * s), round(18 * s)
        self.sx, self.sy, self.t0, self.top = 80.0 * s, 12.0 * s, -0.25, 64.0
        self.fitted = False

        bar = ttk.Frame(self, padding=(8, 6, 8, 4))
        bar.pack(fill="x")
        self.what = ttk.Label(bar, text="", foreground="#555")
        self.what.pack(side="left")
        self.grow = tk.BooleanVar(value=True)
        self.grow_box = ttk.Checkbutton(bar, text=tr("hz.grow"), variable=self.grow, command=self.on_grow)
        self.grow_box.pack(side="right")
        Tooltip(self.grow_box, tr("hz.grow_tip"))
        self.status = ttk.Label(self, text="", foreground="#555", padding=(8, 2, 8, 4))
        self.status.pack(side="bottom", fill="x")
        c = self.canvas = tk.Canvas(self, background="white", highlightthickness=0, takefocus=True)
        c.pack(fill="both", expand=True)
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-2>", self.pan_start)
        c.bind("<B2-Motion>", self.pan_move)
        c.bind("<Motion>", self.on_motion)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Delete>", lambda e: self.delete_selected() or "break")
        c.bind("<Escape>", lambda e: self.select(()) or "break")
        for k in ("<Control-a>", "<Control-A>"):
            c.bind(k, lambda e: self.select(range(len(self.tones))) or "break")
        self.bind("<Configure>", self.remember)
        self.protocol("WM_DELETE_WINDOW", self.close)
        c.focus_set()

    # ------------------------------------------------------------ what it shows

    def target(self):
        """The shape whose tones are shown: the one selected custom shape (not text or pasted notes), or None."""
        app = self.app
        sh = app.selected()
        if sh and len(app.sels) == 1 and sh["kind"] == "custom" and not sh.get("text") and "notes" not in sh:
            return sh
        return None

    def can_place(self):
        return self.target() is not None or self.app.hz_start is not None

    def sync(self):
        """The main window's selection or shapes changed (undo too): show what's there now."""
        sh = self.target()
        hz = (sh or {}).get("hz") or {}
        tones = clean_tones(hz.get("tones"))
        if tones != self.tones:
            self.tones, self.sel = tones, set()
        if sh is None:
            text = (tr("hz.hint_new", beat=fmt(self.app.hz_start + 1)) if self.app.hz_start is not None
                    else tr("hz.hint_none"))
            self.grow.set(True)
        else:
            text = tr("hz.shape", name=self.app.shape_label(sh))
            self.grow.set(bool(hz.get("grow")) if tones else hz_made(sh))
        self.what.config(text=text)
        self.grow_box.config(state="normal" if sh is not None else "disabled")
        if self.tones and not self.fitted:
            self.fit_view()
        self.redraw()

    def fit_view(self):
        """The view moved so the notes are in sight (the first time there are any)."""
        self.fitted = True
        keys = [n["key"] for n in self.tones]
        rows = max(1.0, (self.canvas.winfo_height() - self.ruler_h) / self.sy)
        self.top = min(127.0, max(rows - 1, (max(keys) + min(keys)) / 2 + rows / 2))

    # ------------------------------------------------------------ view

    def x_of(self, beat):
        return self.kb_w + (beat - self.t0) * self.sx

    def beat_at(self, x):
        return self.t0 + (x - self.kb_w) / self.sx

    def y_of(self, key):
        """The top of a key's row."""
        return self.ruler_h + (self.top - key) * self.sy

    def key_at(self, y):
        return max(0, min(127, math.ceil(self.top - (y - self.ruler_h) / self.sy)))

    def clamp_view(self):
        rows = max(1.0, (self.canvas.winfo_height() - self.ruler_h) / self.sy)
        self.top = min(127.0, max(min(127.0, rows - 1), self.top))
        self.t0 = max(-0.25, self.t0)

    def on_wheel(self, e):
        up = 1 if e.delta > 0 else -1
        if e.state & CTRL:  # zoom both ways, around the mouse
            b, k = self.beat_at(e.x), self.top - (e.y - self.ruler_h) / self.sy
            f = 1.2 ** up
            self.sx = min(2000.0 * self.s, max(4.0 * self.s, self.sx * f))
            self.sy = min(40.0 * self.s, max(4.0 * self.s, self.sy * f))
            self.t0 = b - (e.x - self.kb_w) / self.sx
            self.top = k + (e.y - self.ruler_h) / self.sy
        elif e.state & SHIFT:
            self.t0 -= up * 60 * self.s / self.sx
        else:
            self.top += up * 3
        self.clamp_view()
        self.redraw()

    def pan_start(self, e):
        self.pan = (e.x, e.y, self.t0, self.top)

    def pan_move(self, e):
        x, y, t0, top = self.pan
        self.t0, self.top = t0 - (e.x - x) / self.sx, top + (e.y - y) / self.sy
        self.clamp_view()
        self.redraw()

    # ------------------------------------------------------------ drawing

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        s, kb, rh = self.s, self.kb_w, self.ruler_h
        k_hi, k_lo = self.key_at(rh), self.key_at(h)
        for k in range(k_lo, k_hi + 1):  # rows
            y = self.y_of(k)
            if k % 12 in BLACK:
                c.create_rectangle(kb, y, w, y + self.sy, fill="#eef1f8", outline="")
            c.create_line(kb, y + self.sy, w, y + self.sy, fill="#c9c9c9" if k % 12 == 0 else "#ececec")
        beats, sb = self.app.beats, self.app.snap_beats()
        step = sb if sb and sb * self.sx >= 8 else 1.0
        n = math.floor(max(0.0, self.beat_at(kb)) / step)
        while n * step <= self.beat_at(w):  # columns
            b = n * step
            x = self.x_of(b)
            whole = abs(b - round(b)) < 1e-9
            bar = whole and round(b) % beats == 0
            c.create_line(x, rh, x, h, fill="#707070" if bar else "#bdbdbd" if whole else "#ececec")
            n += 1
        sh = self.target()
        if sh is not None and not self.grow.get():  # the shape ends here: what's after it isn't used
            x = max(kb, self.x_of(shape_length(sh)))
            c.create_rectangle(x, rh, w, h, fill="#d8d8d8", outline="", stipple="gray50")
            c.create_line(x, rh, x, h, fill="#909090", dash=(4, 3))
        for i, n in enumerate(self.tones):  # notes
            x0, x1, y = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            fill, edge = SELECTED_COLOR if i in self.sel else SLOT_COLORS[0]
            c.create_rectangle(x0, y + 1, max(x1, x0 + 2), y + self.sy - 1, fill=fill, outline=edge)
        for line in voices(self.tones):  # the tone's path
            pts = []
            for n, (a, b) in zip(line, holds(line)):
                y = self.y_of(n["key"]) + self.sy / 2
                pts += [self.x_of(a), y, self.x_of(b), y]
            c.create_line(*pts, fill=RED, width=max(2, round(2 * s)))
        for x, y, _, _ in self.dots():
            r = 3.5 * s
            c.create_oval(x - r, y - r, x + r, y + r, fill="white", outline=RED, width=max(1, round(1.5 * s)))
        c.create_rectangle(0, 0, kb, h, fill="#fafafa", outline="")  # keys
        for k in range(k_lo, k_hi + 1):
            y = self.y_of(k)
            if k % 12 in BLACK:
                c.create_rectangle(0, y, kb * 0.6, y + self.sy, fill="#303030", outline="")
            c.create_line(0, y + self.sy, kb, y + self.sy, fill="#d0d0d0")
            if k % 12 == 0 or (self.sy >= 15 * s and k % 12 not in BLACK):
                c.create_text(kb - 3, y + self.sy / 2, text=note_name(k), anchor="e", fill="#222",
                              font=("Segoe UI", 7, "bold" if k % 12 == 0 else "normal"))
        c.create_line(kb, 0, kb, h, fill="#707070")
        c.create_rectangle(0, 0, w, rh, fill="#f3f3f3", outline="")  # bar numbers
        n = max(0, math.floor(self.beat_at(kb) / beats))
        while n * beats <= self.beat_at(w):
            x = self.x_of(n * beats)
            if x >= kb:
                c.create_text(x + 3, rh / 2, text=str(n + 1), anchor="w", fill="#333", font=("Segoe UI", 8))
            n += 1
        c.create_line(0, rh, w, rh, fill="#707070")
        if not self.can_place():
            c.create_text((kb + w) / 2, (rh + h) / 2, text=tr("hz.hint_none"), fill="#777",
                          width=w - kb - 40 * s, justify="center")
        self.show_status()

    def dots(self):
        """[(x, y, tone number, "in" / "out")]: the red line's dots. A dot with no lead sits just outside its note's
        end (so the end itself stays free for changing the note's length)."""
        out = []
        index = {id(n): i for i, n in enumerate(self.tones)}
        off = 6 * self.s
        for line in voices(self.tones):
            hs = holds(line)
            for j, n in enumerate(line):
                y = self.y_of(n["key"]) + self.sy / 2
                a, b = hs[j]
                if j:
                    out.append((self.x_of(a) - (off if a <= n["t"] else 0), y, index[id(n)], "in"))
                if j + 1 < len(line):
                    out.append((self.x_of(b) + (off if b >= n["t"] + n["len"] else 0), y, index[id(n)], "out"))
        return out

    def show_status(self, e=None):
        n = len(self.tones)
        text = tr("hz.one_note") if n == 1 else tr("hz.n_notes", n=n)
        sh = self.target()
        if sh is not None and self.tones:
            text += "     " + tr("hz.repeats", n=f"{self.app.note_count(sh):,}")
        if e is not None and e.x >= self.kb_w and e.y >= self.ruler_h:
            k = self.key_at(e.y)
            cents = ((sh or {}).get("hz") or HZ_DEFAULTS)["cents"]
            text += "     " + tr("hz.position", beat=fmt(max(0.0, self.beat_at(e.x)) + 1), key=note_name(k),
                                 hz=f"{hz_of(k, cents):.2f}")
        self.status.config(text=text)

    # ------------------------------------------------------------ mouse

    def snap(self, beat, e, down=False):
        """beat on the main window's snap grid (Shift = off; down = the line at or before it)."""
        sb = self.app.snap_beats()
        if not sb or e.state & SHIFT:
            sb = 1 / self.app.ppq
        return max(0.0, (math.floor(beat / sb + 1e-9) if down else round(beat / sb)) * sb)

    def shortest(self, e):
        sb = self.app.snap_beats()
        return sb if sb and not e.state & SHIFT else 1 / self.app.ppq

    def hit(self, x, y):
        """What's under the mouse: ("in" / "out", tone) a red dot, ("left" / "right", tone) a note's end,
        ("note", tone), or None."""
        r = 6 * self.s
        for dx, dy, i, which in self.dots():
            if abs(x - dx) <= r and abs(y - dy) <= r:
                return which, i
        if x < self.kb_w or y < self.ruler_h:
            return None
        for i in range(len(self.tones) - 1, -1, -1):
            n = self.tones[i]
            x0, x1, y0 = self.x_of(n["t"]), self.x_of(n["t"] + n["len"]), self.y_of(n["key"])
            if x0 - 1 <= x <= max(x1, x0 + 2) + 1 and y0 <= y < y0 + self.sy:
                edge = min(5 * self.s, (x1 - x0) / 3)
                return ("right" if x >= x1 - edge else "left" if x <= x0 + edge else "note"), i
        return None

    def on_motion(self, e):
        hit = self.hit(e.x, e.y)
        self.canvas.config(cursor={"in": "sb_h_double_arrow", "out": "sb_h_double_arrow", "left": "sb_h_double_arrow",
                                   "right": "sb_h_double_arrow", "note": "fleur"}.get(hit and hit[0], ""))
        self.show_status(e)

    def select(self, indices):
        self.sel = set(indices)
        self.redraw()

    def on_press(self, e):
        self.canvas.focus_set()
        self.drag = None
        hit = self.hit(e.x, e.y)
        before = copy.deepcopy(self.tones)
        if hit is None:
            if e.x < self.kb_w or e.y < self.ruler_h or not self.can_place():
                return
            if e.state & CTRL:
                return self.select(())
            tone = {"t": self.snap(self.beat_at(e.x), e, down=True), "len": self.last_len, "key": self.key_at(e.y),
                    "in": 0.0, "out": 0.0}
            self.tones.append(tone)
            self.sel = {len(self.tones) - 1}
            self.drag = {"kind": "new", "i": len(self.tones) - 1, "before": before, "name": tr("hz.step_place")}
        else:
            kind, i = hit
            if kind == "note" and e.state & CTRL:
                return self.select(self.sel ^ {i})
            if i not in self.sel:
                self.sel = {i}
            self.drag = {"kind": kind, "i": i, "before": before, "beat": self.beat_at(e.x), "key": self.key_at(e.y),
                         "orig": copy.deepcopy(self.tones),
                         "name": {"note": tr("hz.step_move"), "in": tr("hz.step_lead"),
                                  "out": tr("hz.step_lead")}.get(kind, tr("hz.step_length"))}
        self.redraw()

    def on_drag(self, e):
        d = self.drag
        if not d:
            return
        n = self.tones[d["i"]]
        beat, short = self.beat_at(e.x), self.shortest(e)
        if d["kind"] == "new":
            n["len"] = max(self.last_len if beat <= n["t"] + self.last_len else short, self.snap(beat, e) - n["t"])
        elif d["kind"] == "right":
            n["len"] = max(short, self.snap(beat, e) - n["t"])
        elif d["kind"] == "left":
            end = n["t"] + n["len"]
            n["t"] = min(self.snap(beat, e), end - short)
            n["len"] = end - n["t"]
        elif d["kind"] == "out":
            n["out"] = min(max(0.0, n["t"] + n["len"] - self.snap(beat, e)), n["len"] - min(n["in"], n["len"]))
        elif d["kind"] == "in":
            n["in"] = min(max(0.0, self.snap(beat, e) - n["t"]), n["len"] - min(n["out"], n["len"]))
        else:  # move every selected note
            orig = d["orig"]
            dt = self.snap(abs(beat - d["beat"]), e) * (1 if beat >= d["beat"] else -1)
            dk = self.key_at(e.y) - d["key"]
            dt = max(dt, -min(orig[i]["t"] for i in self.sel))
            dk = max(-min(orig[i]["key"] for i in self.sel), min(127 - max(orig[i]["key"] for i in self.sel), dk))
            for i in self.sel:
                self.tones[i]["t"], self.tones[i]["key"] = orig[i]["t"] + dt, orig[i]["key"] + dk
        self.redraw()
        self.show_status(e)

    def on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        if d["kind"] in ("new", "left", "right"):
            self.last_len = self.tones[d["i"]]["len"]
        if self.tones != d["before"]:
            self.commit(d["name"], d["before"])

    def delete_selected(self):
        if self.sel:
            before = copy.deepcopy(self.tones)
            self.tones = [n for i, n in enumerate(self.tones) if i not in self.sel]
            self.sel = set()
            self.commit(tr("hz.step_delete"), before)

    def on_grow(self):
        sh = self.target()
        if sh is None:
            return
        if not self.tones:  # (nothing placed yet: just how it'll be when there is)
            return self.redraw()
        self.commit(tr("hz.grow"), copy.deepcopy(self.tones))

    # ------------------------------------------------------------ into the shape

    def commit(self, name, before):
        """The notes here become the shape's tones: one undo step of the main window. before = the tones to go back
        to if it's called off (too many notes)."""
        app = self.app
        picked = [self.tones[i] for i in self.sel if i < len(self.tones)]
        self.tones.sort(key=lambda n: (n["t"], n["key"]))
        self.sel = {i for i, n in enumerate(self.tones) if any(n is p for p in picked)}
        tones = clean_tones(copy.deepcopy(self.tones))
        sh = self.target()
        bpm = app.current_bpm()
        if sh is None:
            if not tones or app.hz_start is None:
                return self.redraw()
            lo, hi = app.hz_defaults["lo"], app.hz_defaults["hi"]
            new = dict(app.defaults, kind="custom", name=tr("hz.name"), strokes=[copy.deepcopy(BOX_STROKE)],
                       **custom_settings(app.custom_defaults))
            new.update(fill="spam", pts=box_frame(app.hz_start, lo, app.hz_start + tones_span(tones), hi),
                       hz=dict(app.custom_defaults.get("hz") or HZ_DEFAULTS, bpm=float(bpm or 120), tones=tones,
                               grow=True, own=True))
            if not app.confirm_big([new]):
                return self.call_off(before)
            app.hz_start = None
            self.tones = tones  # (so the selection stays when the main window's selection changes to the new shape)
            app.add_shape(new)
        else:
            hz = dict(sh.get("hz") or dict(HZ_DEFAULTS, bpm=float(bpm or 120)))
            hz.pop("tones", None)
            hz.pop("grow", None)
            new = copy.deepcopy(sh)
            if tones:
                new["hz"] = dict(hz, tones=tones, **({"grow": True} if self.grow.get() else {}))
                if new["fill"] not in SPAM_FILLS:
                    new["fill"] = "spam"
                if self.grow.get():
                    fit_length(new)
                if not app.confirm_big([new]):
                    return self.call_off(before)
            app.push_undo(name=name)
            if tones or not hz_made(sh):
                if not tones:  # the last note deleted from a shape of its own: back to its one tone
                    new["hz"] = hz
                sh.clear()
                sh.update(new)
            else:  # ... from a Hz bass made here: it goes, a new one can start at the same spot
                app.hz_start = left_edge(sh)
                del app.shapes[app.sel]
                self.tones = []
                app.select(None)
            app.shapes_changed()
            app.sync_custom()
            app.schedule_autosave()
        self.sync()

    def call_off(self, before):
        self.tones, self.sel = before, set()
        self.redraw()

    def remember(self, e):
        if e.widget is self:
            self.app.hz_pos = self.geometry()

    def close(self):
        self.app.hz_window = None
        self.destroy()
        self.app.roll.focus_set()
