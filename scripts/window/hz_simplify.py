"""Simplify points… (stage E of the Hz bass Sketch extras, 2026-11-13; the plugin's Simplify): a small window with a
Points slider (live preview on the notes), a "Round to semitones" tick for bend lines, OK / Cancel. From the note's
right-click menu (Bend → Simplify points…: its bend line, the other selected notes' too) and from the Loudness
pane's right-click menu (the line shown there: the notes' loudness lines or the layer's). Several lines: each keeps
the same share of its points as the one the slider counts (my pick). One undo step on OK; Cancel = as before.
Maths: notes/hz_simplify.py."""

import copy
import tkinter as tk
from tkinter import ttk

from files.lang import tr, trn
from notes.hz_simplify import least, rank, share_count, simplified, wobble_ends
from window import look
from window.widgets import REDO_KEYS, UNDO_KEYS, remember_place


def ask_points(win, most, lo, notes, bend, apply):
    """The Simplify window: the slider from lo to most points (starts at most = as it is), apply(count, semitones)
    on every change. notes = how many lines change (said under the slider when 2+). bend = the Round to semitones
    tick shows. True on OK, None on Cancel / Escape / closed."""
    top = tk.Toplevel(win)
    top.title(tr("hz.simplify_title"))
    top.transient(win)
    top.resizable(False, False)
    f = ttk.Frame(top, padding=10)
    f.pack(fill="both")
    ttk.Label(f, text=tr("hz.simplify_ask"), justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
    count, semis = tk.IntVar(value=most), tk.BooleanVar(value=False)
    ttk.Label(f, text=tr("hz.simplify_points")).grid(row=1, column=0, sticky="w", pady=(8, 0))
    scale = ttk.Scale(f, from_=lo, to=most, orient="horizontal", length=round(200 * win.s))
    scale.set(most)
    scale.grid(row=1, column=1, sticky="we", pady=(8, 0), padx=(6, 0))
    says = ttk.Label(f, text="", width=max(len(tr("hz.simplify_count", was=most, now=most)), 14))
    says.grid(row=1, column=2, sticky="w", pady=(8, 0), padx=(8, 0))
    row = 2
    if notes > 1:
        ttk.Label(f, text=tr("hz.simplify_many", notes=trn("n.notes", notes, n=notes)), foreground=look.HINT,
                  justify="left").grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))
        row += 1
    if bend:
        ttk.Checkbutton(f, text=tr("hz.simplify_semis"), variable=semis,
                        command=lambda: apply(count.get(), semis.get())).grid(row=row, column=0, columnspan=3,
                                                                              sticky="w", pady=(6, 0))
        row += 1
    got = {"value": None}

    def show():
        says.config(text=tr("hz.simplify_count", was=most, now=count.get()))

    def moved(v=None):
        c = max(lo, min(most, int(round(float(scale.get())))))
        if c != count.get():
            count.set(c)
            show()
            apply(c, semis.get())

    def step(by):
        scale.set(max(lo, min(most, count.get() + by)))
        moved()
        return "break"

    def ok(e=None):
        got["value"] = True
        top.destroy()

    scale.config(command=moved)
    show()
    btns = ttk.Frame(f)
    btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(10, 0))
    ttk.Button(btns, text=tr("hz.live_ok"), command=ok).pack(side="left")
    ttk.Button(btns, text=tr("hz.live_cancel"), command=top.destroy).pack(side="left", padx=(6, 0))
    top.bind("<Return>", ok)
    top.bind("<Escape>", lambda e: top.destroy())
    for k, by in (("<Left>", -1), ("<Down>", -1), ("<Right>", 1), ("<Up>", 1)):  # (one point a key: on the
        scale.bind(k, lambda e, by=by: step(by))  # slider itself, before ttk's own steps)
    top.points = lambda c: (scale.set(c), moved())  # (tests: the slider set to c points)
    top.rounding = lambda v: (semis.set(v), apply(count.get(), v))
    top.ok = ok
    for k in UNDO_KEYS + REDO_KEYS:  # (the main undo would change the shape under the window)
        top.bind(k, lambda e: "break")
    top.geometry(f"+{win.winfo_pointerx() - 40}+{win.winfo_pointery() - 40}")
    remember_place(top, "hz_simplify")
    scale.focus_set()
    top.grab_set()
    win.simplify_top = top  # (tests reach it)
    top.wait_window()
    win.simplify_top = None
    return got["value"]


class HzSimplify:
    simplify_top = None

    def simplifies(self, n):
        """True when note n's bend line has points Simplify can drop."""
        pts = n.get("bend") or ()
        return len(pts) > least(pts, wobble_ends(n))

    def simplify_bends(self, i):
        """Bend → Simplify points…: note i's bend line (the other selected notes' too when it's one of them)."""
        if i >= len(self.tones):
            return
        picked = [j for j in self.picked_or(i) if self.simplifies(self.tones[j])]
        if not picked:
            return
        ref = i if i in picked else picked[0]
        ids = {self.tones[j]["id"]: j for j in picked}
        lines = {k: (self.tones[j]["bend"], wobble_ends(self.tones[j])) for k, j in ids.items()}
        orders = {k: rank(pts, must) for k, (pts, must) in lines.items()}
        pts0, must0 = lines[self.tones[ref]["id"]]
        most, lo, held = len(pts0), least(pts0, must0), self.tones[ref]["id"]
        from window.hz_bend import tidy_bend

        def change(tones, count, semis):
            for n in tones:
                k = n.get("id")
                if k not in lines:
                    continue
                pts, must = lines[k]
                c = count if k == held else share_count(count, most, pts, must)
                n["bend"] = simplified(pts, c, orders[k], semis)
                tidy_bend(n, self.sketching())

        self.simplify_live(most, lo, len(picked), True, change)

    def simplify_loud(self):
        """The Loudness pane's Simplify points…: the line shown (the notes' own lines, the selected ones when some
        are; or the layer's line)."""
        pane = self.loudness
        if pane is None:
            return
        if pane.which.get() == "layer":
            line = copy.deepcopy(self.loud)
            if len(line) <= 2:
                return
            order = rank(line)

            def change(tones, count, semis):
                self.loud = simplified(line, count, order, curves=False)

            return self.simplify_live(len(line), 2, 1, False, change)
        lines = {n["id"]: copy.deepcopy(n["vel"]) for i, n in enumerate(self.tones)
                 if (not self.sel or i in self.sel) and len(n.get("vel") or ()) > 2}
        if not lines:
            return
        orders = {k: rank(pts) for k, pts in lines.items()}
        held = max(lines, key=lambda k: len(lines[k]))  # (the slider counts the line with the most points)
        most = len(lines[held])

        def change(tones, count, semis):
            for n in tones:
                k = n.get("id")
                if k in lines:
                    c = count if k == held else share_count(count, most, lines[k])
                    n["vel"] = simplified(lines[k], c, orders[k], curves=False)

        self.simplify_live(most, 2, len(lines), False, change)

    def simplify_live(self, most, lo, notes, bend, change):
        """The window, each slider move tried on the notes at once (one undo step in all, as the Tune window);
        Cancel, or OK with nothing changed, = as before with no step."""
        before, loud0 = copy.deepcopy(self.tones), copy.deepcopy(self.loud)
        state = {"pushed": False, "at": (most, False)}
        redo = self.app.redo_stack[:]

        def apply(count, semis):
            was, was_loud = copy.deepcopy(self.tones), copy.deepcopy(self.loud)
            tones = copy.deepcopy(before)
            self.loud = copy.deepcopy(loud0)
            state["at"] = (count, bool(semis))
            if state["at"] != (most, False):  # (back at the start: the lines exactly as they were)
                change(tones, count, semis)
            self.tones[:] = tones  # (the same list: the selection keeps its numbers)
            if self.tones != was or self.loud != was_loud:
                top = self.app.last_step()
                self.commit(tr("hz.step_simplify"), copy.deepcopy(before), push=not state["pushed"])
                if self.app.last_step() is not top:
                    state["pushed"] = True
            if self.loudness is not None:
                self.loudness.redraw()

        got = ask_points(self, most, lo, notes, bend, apply)
        same = state["at"] == (most, False) or self.tones == before and self.loud == loud0
        if (got is None or same) and state["pushed"]:
            self.live_drop(before, redo)
        if self.loudness is not None:
            self.loudness.redraw()
