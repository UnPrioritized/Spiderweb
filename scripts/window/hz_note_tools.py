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
from roll.roll_shared import CTRL, note_name
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
    """Note n's key + cents for the pitch `total` (keys): the nearest row, kept to the rows 0..127 with the rest as
    its tune (±TUNE cents); None when it falls off the rows (past 127 + TUNE / 0 - TUNE)."""
    key = min(HIGHEST, max(LOWEST, int(round(total))))
    cents = round((total - key) * 100.0, 6)
    if abs(cents) > TUNE + 1e-6:
        return None
    return key, max(-TUNE, min(TUNE, cents))


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
        line = [list(p) for p in n.get("bend") or [[0.0, 0.0], [1.0, 0.0]]]
        # (a line starting late / ending early holds its first / last value there, as it sounds on its own: a Fall
        # off must still jump at its note's start, not glide from the note before; hunt 2026-11-06)
        if line[0][0] > 1e-9:
            line.insert(0, [0.0, line[0][1]])
        if line[-1][0] < 1.0 - 1e-9:
            line.append([1.0, line[-1][1]])
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


DETUNE_STEP = 50.0  # cents: the pull snaps to half a key (the plugin's; Ctrl = any whole cent)
DETUNE_MOST = 1200.0  # cents each way at most (my pick: an octave)
VOICES = (2, 9)  # copies a Quick detune makes, fewest / most (the plugin's)
VOICES_START = 3  # ... to start with (wheel / Up / Down while pulling)


def fanned(n, spread, count, ids):
    """Note n fanned into `count` copies spread evenly from `spread` cents below to `spread` above its pitch, each
    on the row nearest its pitch with its own tune (key + cents, as Set Pitch). Copies past the rows left out; of
    the ones kept, the one nearest the note's pitch is n itself (its id + slides: never lost at the edge keys, hunt
    2026-11-06); the others get ids from `ids` (an iterator) and no slides."""
    offs = [spread * (2.0 * k / (count - 1) - 1.0) for k in range(count)]
    kept = [(off, to) for off in offs if (to := moved_to(n, pitch(n) + off / 100.0)) is not None]
    if not kept:
        return []
    mid = min(range(len(kept)), key=lambda k: abs(kept[k][0]))
    out = []
    for k, (off, to) in enumerate(kept):
        m = copy.deepcopy(n)
        m["key"], m["cents"] = to
        if k != mid:
            m["id"], m["to"] = next(ids), []
        out.append(m)
    return out


DETUNE_ROWS = 8  # px: rows shorter than this have no Quick detune strip (zoomed out: the note must still move)
DETUNE_MOVE = 4  # px the mouse moves before a press on the strip decides: up / down = detune, sideways = move


class HzNoteTools:
    def detune_edge(self, x, y):
        """Quick detune (the plugin's): the selected note's index when (x, y) is on the top or bottom strip INSIDE
        its own row (a quarter of the row at most; none on rows under DETUNE_ROWS px) and it's the only one selected
        (Select tool; its bend stays inside its row), with "top" / "bottom"; else None. Whatever else is there wins
        (slide dots, bend handles, the note's ends, other notes, the tune line: hit must find this note's body;
        hunt 2026-11-06)."""
        if self.tool.get() != "select" or len(self.sel) != 1 or self.drag or self.sy < DETUNE_ROWS * self.s:
            return None
        i = next(iter(self.sel))
        if i >= len(self.tones):
            return None
        n = self.tones[i]
        if any(abs(p[1]) >= 0.5 for p in n.get("bend") or ()) or n.get("wobble"):
            return None
        if self.hit(x, y) != ("note", i):
            return None
        y0 = self.y_of(n["key"])
        band = min(3 * self.s, self.sy / 4)
        if y0 <= y < y0 + band:
            return i, "top"
        if y0 + self.sy - band <= y < y0 + self.sy:
            return i, "bottom"
        return None

    def detune_begin(self, e):
        """A note press that started on the strip (on_press marks drag["edge"]): the first DETUNE_MOVE px decide.
        Up / down = the Quick detune takes over; sideways = an ordinary move (the mark goes). A double-click's
        second press never detunes (it deletes the note: hunt 2026-11-06). True when the motion is used here."""
        d = self.drag
        if not d or d["kind"] != "note" or not d.get("edge") or d["moved"] or d.get("double"):
            return False
        dx, dy = abs(e.x - d["x"]), abs(e.y - d["y"])
        if max(dx, dy) < DETUNE_MOVE:
            return True  # (still deciding: nothing moves)
        if dx >= dy:
            d.pop("edge")
            return False
        i = d["i"]
        self.sound(None)
        self.box_kept = None
        self.drag = {"kind": "detune", "i": i, "edge": d["edge"], "y": d["y"], "before": d["before"],
                     "spread": 0.0, "count": VOICES_START, "note": copy.deepcopy(d["before"][i]),
                     "rest": [copy.deepcopy(n) for j, n in enumerate(d["before"]) if j != i]}
        self.detune_drag(e)
        return True

    def detune_drag(self, e):
        d = self.drag
        if not d or d["kind"] != "detune":
            return False
        rows = (d["y"] - e.y if d["edge"] == "top" else e.y - d["y"]) / self.sy
        cents = max(0.0, min(DETUNE_MOST, rows * 100.0))
        d["spread"] = float(round(cents)) if e.state & CTRL else round(cents / DETUNE_STEP) * DETUNE_STEP
        self.detune_show(d)
        self.show_status(e)
        return True

    def detune_count(self, step):
        """Wheel / Up / Down while pulling: one voice more / fewer (VOICES). True when a Quick detune is held."""
        d = self.drag
        if not d or d["kind"] != "detune":
            return False
        d["count"] = max(VOICES[0], min(VOICES[1], d["count"] + step))
        self.detune_show(d)
        self.show_status()
        return True

    def detune_show(self, d):
        """The notes as the pull has them now (the held note's copies in place of it; spread 0 = as it was)."""
        n = d["note"]
        if d["spread"] <= 0:
            self.tones, self.sel = copy.deepcopy(d["before"]), {d["i"]}
        else:
            top = max([m["id"] for m in d["rest"]] + [n["id"]])
            copies = fanned(n, d["spread"], d["count"], iter(range(top + 1, top + 100)))
            self.tones = copy.deepcopy(d["rest"]) + copies
            self.sel = set(range(len(d["rest"]), len(self.tones)))
        self.redraw()

    def detune_release(self, e):
        d = self.drag
        if not d or d["kind"] != "detune":
            return False
        self.drop_drag()
        if d["spread"] <= 0 or self.tones == d["before"]:  # (nothing changed: as at the press, its Select box too)
            sel, kept = self.press_was
            self.tones, self.sel = copy.deepcopy(d["before"]), set(sel)
            self.box_kept = (kept, set(sel)) if kept else None
            self.sel_before = None
            self.redraw()
        else:
            self.commit(tr("hz.step_detune"), d["before"])
        self.point_again()
        return True

    def detune_status(self):
        d = self.drag
        if not d or d["kind"] != "detune":
            return ""
        return "     " + tr("hz.detune_held", n=d["count"], cents=fmt(d["spread"]))

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
            # (stopping only past the rows' tune range, 0 - TUNE .. 127 + TUNE cents: hunt 2026-11-06)
            lo, hi = LOWEST - TUNE / 100.0, HIGHEST + TUNE / 100.0
            to = (got[0], got[1]) if j == i else moved_to(m, min(hi, max(lo, pitch(m) + moved)))
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
        scaled = 0  # (left alone: the Arpeggio's scale would play them differently)
        for j in picked:
            n = self.tones[j]
            if not self.aligns(n):
                continue
            k0 = n["bend"][0][1]
            to = moved_to(n, pitch(n) + k0)
            if to is None:
                continue
            if not self.scale_keeps(n["key"], to[0]):
                scaled += 1
                continue
            shift = to[0] + to[1] / 100.0 - pitch(n)  # (= k0, but exactly what the key + cents make)
            n["key"], n["cents"] = to
            n["bend"] = [[p[0], round(p[1] - shift, 6), *p[2:]] for p in n["bend"]]
            self.tidy_after(n)
        if self.tones != before:
            self.commit(tr("hz.step_align"), before)
        if scaled:
            self.say(tr("hz.align_scale"))
        elif self.tones == before:
            self.say(tr("hz.align_none"))

    def scale_keeps(self, key, to):
        """True when the Arpeggio box (on, with a scale) plays a note moved from row `key` to row `to` (its bend moved
        back as much) at the same pitches: its scale moves each row to the scale's nearest key BEFORE the bend is
        added, so the move must shift every key it plays by exactly as much (hunt 2026-11-06: Align never changes
        the sound). No Arpeggio / no scale / the steps pattern (no scale there) = True."""
        arp = self.line_hz().get("arp")
        if not arp or not arp.get("scale") or arp.get("pattern") == "steps":
            return True
        from notes.hz_arp import in_scale
        from notes.hz_settings import CHORDS
        root = arp.get("root", 0)
        for k in CHORDS[arp["chord"]]:
            for o in range(int(arp["octaves"])):
                off = k + 12 * o
                if in_scale(to + off, arp["scale"], root) - to != in_scale(key + off, arp["scale"], root) - key:
                    return False
        return True

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
