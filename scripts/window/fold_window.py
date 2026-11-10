"""The Fold tracks window (right-click → Fold tracks…; notes/fold.py; BMC tester + user 2026-11-07): the selected
shapes' tracks folded down to fewer, previewed on the piano roll while the window is open. Fold = the selected shapes
become ONE plain-notes shape (drawn shapes too), one undo step; Cancel / Esc / X = everything as it was, no step.
The window holds the keyboard and mouse (grab) while open: nothing else changes under the preview."""

import math
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes.fold import block, chunks, colour_count, fold_map, folded, free_rounds
from notes.loaded import clean_extract, extract_groups, held_extract
from notes.mask import is_mask
from window import look
from window.history import load_state, undo_state
from window.join_split import by_owner, without10
from window.widgets import Scrub, Tooltip, remember_place, unit_word

WRAP = 330
POLL_MS = 30
LATE_S = 0.15  # (as App's: notes slower than this to make = the preview made in the background)


def _folded_shape(key, slots, rows, others, use10, seq, slot_maps, got, originals, maker, copied, name, m):
    """The Fold preview's folded shape (FoldWindow._start; also run on Tk's thread by _made_here): m["got"] = (the
    shape, (tracks it makes, notes changed)), or m["error"]."""
    try:
        n, how, even, keep, cut = key
        fold = fold_map(slots, n, how, even, keep, use10, seq)
        rows, changed = folded(rows, fold, cut)
        # (pinned in every Channels mode, like a paste's tracks, hunt 11-07: Single / As drawn put it all on one;
        # moved down by whole rounds of channels past other shapes' tracks sounding with it, so they keep theirs)
        step = free_rounds(rows, others, block(use10))
        if step:
            rows[:, 4] += step
            fold = {s: v + step for s, v in fold.items()}
        # (built from the shapes as they were: CC passengers, sustain)
        slots_of = lambda o: {t: fold.get(s, s) for t, s in slot_maps[o].items()}
        sh = without10(copied(maker, rows, False, True, slots_of), use10)
        sh["own_slots"] = True
        sh["made"] = "plain"
        sh["name"] = name
        if cut:
            sh.pop("held", None)
        else:  # (overlaps kept as they are, like a sustained shape's)
            sh["held"] = True
        ex = clean_extract([g for i in got for g in extract_groups(held_extract(originals[i]))])
        if ex:
            sh["extract"] = ex
        m["got"] = (sh, (len(set(fold.values())), changed))
    except Exception as e:  # noqa: BLE001 (MemoryError too: made again on Tk's thread, which tells)
        m["error"] = e


def fold_targets(app):
    """The selected shapes Fold tracks works on: not pictures, masks, Hz bass, or notes laid in a track (the
    track's channel wins there)."""
    return [i for i in sorted(app.sels) if i < len(app.shapes) and (sh := app.shapes[i])
            and "picture" not in sh and not is_mask(sh) and not sh.get("hz") and not sh.get("into")]


class FoldWindow(tk.Toplevel):
    def __init__(self, app, got):
        super().__init__(app)
        self.app, self.got = app, got
        self.title(tr("fold.title"))
        self.transient(app)
        self.resizable(False, False)
        self.job = None
        self.shown = None  # (the settings the piano roll shows now)
        app.commit_typing()
        app.colour_edit_leave()  # (a colour step being edited: whole first)
        if app.notes_late:
            app.shapes_changed(now=True)
        app.roll.cancel_draft()
        self.originals = list(app.shapes)
        # (the project as it was: Cancel and Fold's undo step start from it, never from the shapes the preview's
        # redraws may have changed, e.g. an Add between group unlinked, hunt 11-07)
        self.state0, self.sel0 = undo_state(app.shapes), app.sel_state()
        tl = app.track_list
        self.highlight = (tl.current, set(tl.behind))  # (put back by Cancel: the preview's tracks moved)
        r = app.rendered
        self.others = r[~np.isin(r[:, 5], got)] if len(r) else r  # (other shapes' notes as heard now)
        if app.channel_mode.get() != "auto":  # (Single / As drawn: the result stays on the channels it was laid on,
            self.others = r[:0]  # no move by whole rounds: moved off a shared channel, Single stopped cutting other
            # shapes' notes against it and they grew back; user 2026-11-08. Multi keeps the move for the colours)
        self.use10, multi = app.multi_rendered()
        # (the order the shapes take slots in: channel 10 the 16th with "Use channel 10", never with only a
        # picture's, slot order with "Channels 1-16 in order")
        self.seq = ("plain" if app.ch10_order.get() else "last10") if app.use_ch10.get() else "skip10"
        shown = by_owner(multi)
        parts = [app.with_hidden(app.shapes[i], shown(i)) for i in got]
        self.rows = np.concatenate(parts) if parts else np.zeros((0, 6), np.int64)
        self.slots = np.unique(self.rows[:, 4]) if len(self.rows) else np.zeros(0, np.int64)
        self.tracks = len(self.slots)
        self.colours = colour_count(self.slots, self.use10) if self.tracks else 0
        slots_of = app.track_slots_in(self.rows)  # (CC passengers: each shape's track -> its slot now)
        # (worked out here once, on Tk's thread: the previews are made in the background from these)
        # (only shapes carrying CC passengers ever ask: passengers.carried)
        self.slot_maps = {o: slots_of(o) for o in (np.unique(self.rows[:, 5]).tolist() if len(self.rows) else [])
                          if "cc" in self.originals[o]}
        self.making = None  # (the preview being made in the background: _start)
        self.result = None
        self.how = tk.StringVar(value="row")
        self.cut = tk.BooleanVar(value=True)
        self.even = tk.BooleanVar(value=False)
        self.keep = tk.BooleanVar(value=False)

        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("fold.uses", tracks=trn("n.tracks", self.tracks, n=self.tracks),
                               colours=trn("n.colours", self.colours, n=self.colours)),
                  wraplength=int(WRAP * app.scale), justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        self.n_label = ttk.Label(box, text=tr("fold.into"))
        self.n_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0), padx=(6, 0))
        self.n_var = tk.StringVar(value=fmt(max(1, self.colours)))
        self.n_box = ttk.Entry(row, textvariable=self.n_var, width=6)
        self.n_box.pack(side="left")
        self.n_box.bind("<Return>", lambda e: (self.on_box(), "break")[1])
        self.n_box.bind("<FocusOut>", lambda e: self.on_box())
        unit_word(ttk.Label(row), self.n_var, "fold.tracks").pack(side="left", padx=(4, 0))
        self.each = ttk.Label(row, foreground=look.HINT)
        self.each.pack(side="left", padx=(8, 0))
        Scrub(app, [(self.n_box, self.n_var, self.on_box)], lambda: (1, 10, 1), 1, max(1, self.tracks),
              label=self.n_label, drag_box=True, no_undo=True)
        for w in (self.n_label, self.n_box):
            Tooltip(w, tr("fold.into_tip"))
        self.n = self.read(self.n_var.get()) or 1

        ttk.Label(box, text=tr("fold.how")).grid(row=2, column=0, sticky="nw", pady=(8, 0))
        self.how_text = {}
        for r, (key, name) in enumerate((("row", "fold.in_a_row"), ("loop", "fold.looping"))):
            b = ttk.Radiobutton(box, text=tr(name), variable=self.how, value=key, command=self.changed)
            b.grid(row=2 + r, column=1, sticky="w", pady=(8 if r == 0 else 2, 0), padx=(6, 0))
            Tooltip(b, tr(name + "_tip"))
            self.how_text[key] = ttk.Label(box, foreground=look.HINT)
            self.how_text[key].grid(row=2 + r, column=2, sticky="w", pady=(8 if r == 0 else 2, 0), padx=(8, 0))

        self.ticks = {}
        for r, (var, key) in enumerate(((self.cut, "fold.cut"), (self.even, "fold.even"),
                                        (self.keep, "fold.keep"))):
            t = ttk.Checkbutton(box, text=tr(key), variable=var, command=self.changed)
            t.grid(row=4 + r, column=0, columnspan=3, sticky="w", pady=(8 if r == 0 else 2, 0))
            Tooltip(t, tr(key + "_tip"))
            self.ticks[key] = t
        self.outcome = ttk.Label(box, foreground=look.VALUE, wraplength=int(WRAP * app.scale), justify="left")
        self.outcome.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))
        # (room for two lines kept from the start: "Calculating…" and the outcome take turns without the buttons
        # moving, user 2026-11-09; never less than the most it has shown)
        self.out_box = box
        # (and as wide as its longest line can be: the window never widens when an outcome comes in)
        ttk.Frame(box, width=int(WRAP * app.scale), height=1).grid(row=7, column=0, columnspan=3, sticky="nw")
        self.outcome.lift()
        self.outcome.config(text="A\nA")
        self.outcome.update_idletasks()
        self.out_h = self.outcome.winfo_reqheight() + 10  # (+ its pady: the row's whole height)
        self.outcome.config(text="")
        box.grid_rowconfigure(7, minsize=self.out_h)
        buttons = ttk.Frame(box)
        buttons.grid(row=8, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.fold_button = ttk.Button(buttons, text=tr("fold.fold"), command=self.fold)
        self.fold_button.pack(side="left")
        ttk.Button(buttons, text=tr("range_window.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.fold())
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.show()
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 160}+{app.winfo_rooty() + 160}")
        remember_place(self, "fold")
        self.preview()
        self.focus_set()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # ---- settings

    def least(self):
        """The fewest tracks the box takes: 1, or one per colour with Keep colours."""
        return max(1, self.colours) if self.keep.get() else 1

    def read(self, text):
        try:
            v = calc(text)
        except (ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(v):
            return None
        return int(min(max(math.floor(v + 0.5), self.least()), max(1, self.tracks)))

    def on_box(self):
        n = self.read(self.n_var.get())
        if n is None:
            self.n_var.set(fmt(self.n))
            return
        self.n_var.set(fmt(n))
        if n != self.n:
            self.n = n
            self.changed()

    def settings(self):
        return self.n, self.how.get(), self.even.get(), self.keep.get(), self.cut.get()

    def changed(self):
        if self.n < self.least():
            self.n = self.least()
            self.n_var.set(fmt(self.n))
        self.show()
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(60, self.preview)

    # ---- showing

    def show(self):
        n, how, even, keep, _ = self.settings()
        t = self.tracks
        self.each.config(text=tr("fold.each", n=f"{t / max(n, 1):,.0f}") if t else "")
        row = loop = ""
        if keep:  # (each colour folds among its own tracks: no one example fits, hunt 11-07)
            row = tr("fold.keep_example")
        elif t and n < t:
            sizes = np.bincount(chunks(t, n, "row", even))  # (the real groups, Even groups too)
            a = int(sizes[0])
            if a > 1:
                row = tr("fold.row_example", a=a, b=a + 1, c=a + int(sizes[1])) if len(sizes) > 1 else ""
            loop = (tr("fold.loop_example", a=n + 1, b=2 * n + 1) if 2 * n < t else  # (never a track past the last)
                    tr("fold.loop_example_two", a=n + 1))
        self.how_text["row"].config(text=row)
        self.how_text["loop"].config(text=loop)
        self.ticks["fold.even"].state(["!disabled"] if how == "row" else ["disabled"])

    def preview(self):
        """The folded notes shown on the piano roll (the shapes as they were, the selected ones swapped for one).
        Made in the background (user 2026-11-09: the window keeps answering; "Calculating…" meanwhile, nothing
        moves): the folded shape on one thread, then its notes on App's notes thread (App.notes_for), then taken
        in here (_take). A newer setting waits for the shape being made, then starts from the newest one."""
        self.job = None
        if not self.winfo_exists():
            return
        key = self.settings()
        if key == self.shown or not self.tracks:  # (back to what's shown: the one being made isn't wanted)
            if self.making is not None and not self.making["thread"].is_alive():
                self._drop()
            elif self.making is not None:
                self.making["next"] = key
            self.say()
            return
        m = self.making
        if m is not None and m["key"] == key:
            return
        if m is not None and m["thread"].is_alive():  # (its shape still being made: the newest settings follow it)
            m["next"] = key
            return
        self._drop()
        app = self.app
        if app._make_time + app._mask_time <= LATE_S:  # (a quick project: made here at once, as always)
            return self._made_here(key)
        self._start(key)

    def _start(self, key):
        app = self.app
        n, how, even, keep, cut = key
        # (everything the thread reads, taken here on Tk's thread)
        maker = SimpleNamespace(shapes=self.originals, defaults=dict(app.defaults), ppq=app.ppq,
                                picture_use10=app.picture_use10)
        name = tr("fold.name", name=app.shape_label(self.originals[self.got[0]]))
        m = {"key": key, "next": None, "got": None, "error": None, "notes": None, "new": None}
        # (the thread gets plain data only, never this window: nothing of Tk's may be let go of on it)
        args = (key, self.slots, self.rows, self.others, self.use10, self.seq, self.slot_maps, self.got,
                self.originals, maker, type(app).copied, name, m)
        m["thread"] = threading.Thread(target=_folded_shape, args=args, daemon=True, name="fold preview")
        self.making = m
        self.say()
        m["thread"].start()
        self.after(POLL_MS, self._poll, m)

    def _poll(self, m):
        if m is not self.making or not self.winfo_exists():
            return
        if m["thread"].is_alive():
            self.after(POLL_MS, self._poll, m)
            return
        if m["next"] is not None and m["next"] != m["key"]:  # (settings changed meanwhile: the newest made instead)
            key, self.making = m["next"], None
            if key == self.shown:  # (back to what's on screen already)
                return self.say()
            return self._start(key)
        if m["got"] is None:  # (failed in the background: made here, which tells about it)
            self.making = None
            return self._made_here(m["key"])
        if m["new"] is None:
            self._lay(m)
            m["notes"] = self.app.notes_for(m["new"], [self.got[0]])
        job = m["notes"]
        if job is not None and not job["done"] and self.app._coming is job:
            self.after(POLL_MS, self._poll, m)
            return
        self._take(m)

    def _lay(self, m):
        """The project's shapes with the folded one in place of the selected ones (not shown yet): m["new"]."""
        app = self.app
        sh = m["got"][0]
        keep = app.shapes
        app.shapes = load_state(self.state0)  # (put_plain works on app.shapes: only during this call)
        try:
            # (shown exactly as Fold keeps it: an Add between group / sliced pieces handled as Turn into plain notes)
            app.put_plain({self.got[0]: sh})
            for i in reversed(self.got[1:]):
                del app.shapes[i]
            m["new"] = app.shapes
        finally:
            app.shapes = keep

    def _take(self, m):
        """The preview made: shown (its notes from the background, if they still fit)."""
        app = self.app
        self.making = None
        app.shapes[:] = m["new"]
        app.select_many([self.got[0]], self.got[0])
        app.shapes_changed(now=True)
        self.result, self.made = m["got"]
        self.shown = m["key"]
        self.say()

    def _made_here(self, key):
        """The whole preview made on Tk's thread (the background one failed, or Fold pressed before it was in)."""
        m = {"key": key, "next": None, "got": None, "error": None, "notes": None, "new": None,
             "thread": threading.Thread(target=lambda: None)}
        app = self.app
        maker = SimpleNamespace(shapes=self.originals, defaults=dict(app.defaults), ppq=app.ppq,
                                picture_use10=app.picture_use10)
        name = tr("fold.name", name=app.shape_label(self.originals[self.got[0]]))
        _folded_shape(key, self.slots, self.rows, self.others, self.use10, self.seq, self.slot_maps, self.got,
                      self.originals, maker, type(app).copied, name, m)
        if m["error"] is not None:
            raise m["error"]
        self._lay(m)
        self._take(m)

    def settle(self):
        """The preview being made: finished here and now (Fold pressed meanwhile: it folds what the settings say)."""
        m = self.making
        if m is None:
            return
        m["thread"].join()
        if m["next"] is not None and m["next"] != m["key"] or m["got"] is None:
            self.making = None
            return self._made_here(m["next"] if m["next"] is not None else m["key"])
        if m["new"] is None:
            self._lay(m)
        self._take(m)  # (shapes_changed(now=True) waits for the notes' thread, if it's these, else makes them)

    def _drop(self):
        """The preview being made: not wanted any more (its notes' thread stopped)."""
        m, self.making = self.making, None
        if m is not None and m["notes"] is not None:
            m["notes"]["stop"] = True
            if self.app._coming is m["notes"]:
                self.app._coming = None

    def say(self):
        if not self.tracks:
            self.outcome.config(text=tr("fold.nothing"))
            self.fold_button.state(["disabled"])
            return
        if self.making is not None:  # ("Calculating…" in the outcome's place: the row keeps its room)
            self.outcome.config(text=tr("fold.calculating"))
            return
        if self.result is None:
            return
        new, changed = self.made
        text = tr("fold.outcome", a=trn("n.tracks", self.tracks, n=self.tracks), b=trn("n.tracks", new, n=new))
        n, how, even, keep, _ = self.settings()
        if how == "row" and not even and new < n:  # (full groups can't make n: said why)
            text += tr("fold.short", n=n)
        if changed:
            text += tr("fold.fixed", notes=trn("n.notes", changed, n=changed))
        self.outcome.config(text=text)
        self.outcome.update_idletasks()
        h = self.outcome.winfo_reqheight() + 10
        if h > self.out_h:  # (a longer outcome than any before: its room kept from now on)
            self.out_h = h
            self.out_box.grid_rowconfigure(7, minsize=h)
        self.fold_button.state(["!disabled"] if new < self.tracks or changed else ["disabled"])

    # ---- closing

    def fold(self):
        self.on_box()
        if self.job:
            self.after_cancel(self.job)
            self.preview()
        self.settle()  # (a preview still being made: finished first, so Fold keeps exactly what the settings say)
        if self.settings() != self.shown and self.tracks:  # (changed since what was shown: made now)
            self._made_here(self.settings())
        if self.result is None or "disabled" in self.fold_button.state():
            return
        app = self.app
        app.add_undo_step(self.state0, tr("fold.title"), self.sel0)  # (the shapes shown now ARE the fold)
        app.select_many([self.got[0]], self.got[0])
        # (their notes were made by the preview just above: not made all again, user 2026-11-07: 1.5 s on 4,000
        # tracks; the autosave waited while the window was open)
        app.schedule_autosave()
        new = self.made[0]
        app.status.config(text=tr("fold.done", a=trn("n.tracks", self.tracks, n=self.tracks),
                                  b=trn("n.tracks", new, n=new)))
        self.close()

    def cancel(self):
        app = self.app
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self._drop()  # (a preview still being made: never shown)
        if self.shown is not None:
            app.shapes[:] = load_state(self.state0)
            app.select_many(self.sel0[0], self.sel0[1])
            app.shapes_changed()
            tl = app.track_list  # (the track highlighted before, hunt 11-07)
            if (tl.current, tl.behind) != self.highlight:
                tl.current, tl.behind = self.highlight[0], set(self.highlight[1])
                tl.shown_changed()
        self.close()

    def close(self):
        self._drop()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.app.fold_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_fold(app):
    w = getattr(app, "fold_window", None)
    if w and w.winfo_exists():
        w.lift()
        w.focus_set()
        return
    got = fold_targets(app)
    if got:
        app.fold_window = FoldWindow(app, got)
