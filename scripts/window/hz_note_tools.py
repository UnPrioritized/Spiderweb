"""The Hz bass window's note tools from the note's right-click menu (user 2026-11-05, from the plugin's): Set Pitch…
(a key typed, like C4, + cents: the note moves there, its bend keeps its shape; the other selected notes move as
much), Align Note-On (N: the note's row moves to where its bend starts, the sound stays the same) and Join (J: the
selected notes become ONE bending note; gaps hold the pitch, then jump; refused when two overlap in time). Quick
Ramp (R) is in hz_bend.py (quick_ramp)."""

import copy
import re
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.hzbass import NOTE_BEND_MOST, TUNE, pitch
from roll.roll_shared import note_name
from window import look
from window.widgets import REDO_KEYS, UNDO_KEYS, remember_place

LOWEST, HIGHEST = 0, 127  # the keys a Hz bass note can sit on (the Hz window's rows)
LETTERS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def read_key(text):
    """A key typed: a name ("C4" = 60, "C#4", "Db4", "c-1" = 0) or its number. None when it isn't one / out of
    the rows."""
    text = text.strip().replace("♯", "#").replace("♭", "b")
    m = re.fullmatch(r"([A-Ga-g])([#b]?)\s*(-?\d+)", text)
    if m:
        key = LETTERS[m.group(1).lower()] + {"#": 1, "b": -1, "": 0}[m.group(2)] + (int(m.group(3)) + 1) * 12
    else:
        try:
            v = float(calc(text))
        except (ValueError, ZeroDivisionError, TypeError, OverflowError):
            return None
        if v != round(v):
            return None
        key = int(v)
    return key if LOWEST <= key <= HIGHEST else None


def ask_pitch(win, key, cents):
    """A small window asking for a key (name or number) and cents: (key, cents) or None (Cancel / Escape)."""
    top = tk.Toplevel(win)
    top.title(tr("hz.pitch_title"))
    top.transient(win)
    top.resizable(False, False)
    f = ttk.Frame(top, padding=10)
    f.pack(fill="both")
    ttk.Label(f, text=tr("hz.pitch_ask"), justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
    kv, cv = tk.StringVar(value=note_name(key)), tk.StringVar(value=fmt(cents))
    ttk.Label(f, text=tr("hz.pitch_key")).grid(row=1, column=0, sticky="w", pady=(8, 0))
    ke = ttk.Entry(f, textvariable=kv, width=8)
    ke.grid(row=1, column=1, sticky="w", pady=(8, 0), padx=(6, 0))
    ttk.Label(f, text=tr("hz.pitch_cents")).grid(row=2, column=0, sticky="w", pady=(4, 0))
    ce = ttk.Entry(f, textvariable=cv, width=8)
    ce.grid(row=2, column=1, sticky="w", pady=(4, 0), padx=(6, 0))
    ttk.Label(f, text=tr("unit.cents"), foreground=look.HINT).grid(row=2, column=2, sticky="w", padx=(4, 0), pady=(4, 0))
    why = ttk.Label(f, text="", foreground=look.WARN)
    why.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
    got = {"value": None}

    def ok(e=None):
        k = read_key(kv.get())
        try:
            c = float(calc(cv.get()))
        except (ValueError, ZeroDivisionError, TypeError, OverflowError):
            c = None
        if k is None:
            why.config(text=tr("hz.pitch_bad_key", lo=note_name(LOWEST), hi=note_name(HIGHEST)))
            ke.focus_set()
            return
        if c is None or not -TUNE <= c <= TUNE:
            why.config(text=tr("hz.pitch_bad_cents", most=f"{TUNE:g}"))
            ce.focus_set()
            return
        got["value"] = (k, c)
        top.destroy()

    btns = ttk.Frame(f)
    btns.grid(row=4, column=0, columnspan=3, sticky="e", pady=(8, 0))
    ttk.Button(btns, text=tr("hz.live_ok"), command=ok).pack(side="left")
    ttk.Button(btns, text=tr("hz.live_cancel"), command=top.destroy).pack(side="left", padx=(6, 0))
    top.bind("<Return>", ok)
    top.bind("<Escape>", lambda e: top.destroy())
    for k in UNDO_KEYS + REDO_KEYS:  # (the main undo would change the shape under the window)
        top.bind(k, lambda e: "break")
    top.geometry(f"+{win.winfo_pointerx() - 40}+{win.winfo_pointery() - 40}")
    remember_place(top, "hz_pitch")
    ke.focus_set()
    ke.select_range(0, "end")
    top.grab_set()
    top.wait_window()
    return got["value"]


def moved_to(n, total):
    """Note n's key + cents for the pitch `total` (keys): None when it falls off the rows."""
    key = int(round(total))
    if not LOWEST <= key <= HIGHEST:
        return None
    return key, round((total - key) * 100.0, 6)


def joined(notes):
    """The notes (in time order, none overlapping another) as ONE note: the first's own settings (velocity, gates,
    Auto, tune), from its start to the last one's end; its bend line = each note's pitch (and own bend) in turn,
    a gap holds the pitch before it, then it jumps (user 2026-11-05: sounds as before). Wobbles go along. None when
    the line would go further than a note's bend can."""
    a = notes[0]
    t0, t1 = a["t"], max(n["t"] + n["len"] for n in notes)
    length = t1 - t0
    pts, wobs = [], []
    for n in notes:
        d = pitch(n) - pitch(a)
        line = n.get("bend") or [[0.0, 0.0], [1.0, 0.0]]
        at = lambda u, n=n: (n["t"] - t0 + u * n["len"]) / length
        if pts:  # (the gap: held at the last pitch up to this note's start, then the jump)
            pts.append([at(0.0), pts[-1][1]])
        piece = [[at(p[0]), d + p[1], *p[2:]] for p in line]
        del piece[-1][2:]  # (its last point bends nothing after it: the hold / the next note is straight)
        pts += piece
        for w in n.get("wobble") or ():
            w = copy.deepcopy(w)
            w["at"] = [at(w["at"][0]), at(w["at"][1])]
            wobs.append(w)
    if max(abs(p[1]) for p in pts) > NOTE_BEND_MOST:
        return None
    out = copy.deepcopy(a)
    out.update(len=length, bend=[[round(p[0], 9), *p[1:]] for p in pts])
    if a.get("vel"):  # (the first note's loudness line kept where it was in time, its end held after)
        out["vel"] = [[round(u * a["len"] / length, 9), v] for u, v in a["vel"]]
    out.pop("wobble", None)
    if wobs:
        out["wobble"] = wobs
    return out


class HzNoteTools:
    def picked_or(self, i):
        return sorted(self.sel) if i in self.sel else [i]

    def tool_items(self, menu, i):
        """The menu's note tools for note i (hz_mouse.show_menu): Set Pitch…, Align Note-On, Join."""
        picked = self.picked_or(i)
        menu.add_command(label=tr("hz.pitch_set"), command=lambda: self.set_pitch(i))
        menu.add_command(label=tr("hz.align"), accelerator="N", command=lambda: self.align_note_on(picked),
                         state="normal" if any(self.aligns(self.tones[j]) for j in picked) else "disabled")
        if len(picked) >= 2:
            menu.add_command(label=tr("hz.join"), accelerator="J", command=lambda: self.join_notes(picked))

    def set_pitch(self, i):
        """Set Pitch…: note i to the key + cents typed (its bend keeps its shape); the other selected notes when
        it's one of them move as much, each stopping at the lowest / highest key. One undo step."""
        if i >= len(self.tones):
            return
        n = self.tones[i]
        got = ask_pitch(self, n["key"], n.get("cents", 0.0))
        if got is None or not self.winfo_exists() or i >= len(self.tones) or self.tones[i] is not n:
            return
        moved = got[0] + got[1] / 100.0 - pitch(n)
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        for j in self.picked_or(i):
            m = self.tones[j]
            to = (got[0], got[1]) if j == i else moved_to(m, min(HIGHEST, max(LOWEST, pitch(m) + moved)))
            if to:
                m["key"], m["cents"] = to
        if self.tones != before:
            self.commit(tr("hz.step_pitch"), before)

    def aligns(self, n):
        """True when Align Note-On changes note n: its bend starts off its own key."""
        pts = n.get("bend")
        return bool(pts) and abs(pts[0][1]) > 1e-9

    def align_note_on(self, picked=None):
        """Align Note-On (N, the plugin's): each note (the selected ones; from the menu: the clicked one or the
        selected ones with it) moves to the pitch its bend starts at, the bend line moved back as much, so it
        SOUNDS the same and sits on the row it starts on. One undo step; nothing to align = said."""
        if self.drag or self.target() is None:
            return
        picked = sorted(self.sel) if picked is None else picked
        if not picked:
            return self.say(tr("hz.align_none"))
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        for j in picked:
            n = self.tones[j]
            if not self.aligns(n):
                continue
            k0 = n["bend"][0][1]
            to = moved_to(n, pitch(n) + k0)
            if to is None:
                continue
            shift = to[0] + to[1] / 100.0 - pitch(n)  # (= k0, but exactly what the key + cents make)
            n["key"], n["cents"] = to
            n["bend"] = [[p[0], round(p[1] - shift, 6), *p[2:]] for p in n["bend"]]
            self.tidy_after(n)
        if self.tones != before:
            self.commit(tr("hz.step_align"), before)
        else:
            self.say(tr("hz.align_none"))

    def tidy_after(self, n):
        from window.hz_bend import tidy_bend
        tidy_bend(n, self.sketching())

    def join_notes(self, picked=None):
        """Join (J): the selected notes become one bending note (joined). Notes overlapping in time = refused with a
        message (one note can't play two pitches). Slides into the first note and out of the last stay; the ones
        between them go. One undo step, the joined note selected."""
        if self.drag or self.target() is None:
            return
        picked = sorted(self.sel) if picked is None else picked
        if len(picked) < 2:
            return self.say(tr("hz.join_few"))
        notes = sorted((self.tones[j] for j in picked), key=lambda n: (n["t"], n["key"]))
        if any(b["t"] < a["t"] + a["len"] - 1e-9 for a, b in zip(notes, notes[1:])):
            self.bell()
            return self.say(tr("hz.join_overlap"))
        one = joined(notes)
        if one is None:
            self.bell()
            return self.say(tr("hz.join_far"))
        ids = {n["id"] for n in notes}
        last = notes[-1]
        one["to"] = [s for s in last["to"] if s["id"] not in ids]  # (out of the last note: now out of the joined one)
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        keep = [n for n in self.tones if n["id"] not in ids]
        for n in keep:  # (slides into the notes after the first go; into the first one stay, it keeps its id)
            n["to"] = [s for s in n["to"] if s["id"] not in ids or s["id"] == one["id"]]
        self.tidy_after(one)
        self.tones = keep + [one]
        self.sel, self.box_kept = {len(keep)}, None
        self.commit(tr("hz.step_join"), before)
