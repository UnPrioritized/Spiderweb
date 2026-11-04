"""Draw on Domino (Domino companion mode, Windows only): Spiderweb's drawing tools right on Domino's piano roll.

Spiderweb's main window hides; a see-through piano roll of ours (roll/overlay_roll.py) lies exactly over Domino's
note grid (window/domino_overlay.py follows Domino's window, scroll and zoom) and is app.roll while this is on, so
every tool, undo step and shape works as in the main window. A small floating bar over Domino holds the tools:
"Domino" = Domino's own mouse (clicks go through to Domino; Domino's select box is the only selection, user
2026-11-05), a drawing tool = drawing on the overlay, "Spiderweb" = back to the main window.
The wheel always goes to Domino (its scroll and zoom are the view). Reading Domino: files/domino_view.py.
NOTES INTO DOMINO (files/domino_link.py): a shape drawn here goes into Domino's current track right after it's
finished (Domino's own paste at the shape's start; the shape leaves the overlay), with the project's PPQ set to
Domino's song PPQ while this is on. Ctrl+Z right after takes it out of Domino again (Domino's own undo) and gives
the shape back, until Domino is used (then Domino's own Ctrl+Z). "Take" lifts Domino's selection out (copied, then
deleted in Domino) as a shape on the overlay, for Spiderweb's tools; "Send" puts the shapes on the overlay back in.
"""

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from files import domino_link as dl
from files import domino_view as dv
from files import settings, system
from files.domino_clip import clip_data, cut_gates
from files.lang import tr, trn
from notes.custom import notes_shape
from notes.engine import SHAPE_DEFAULTS, clean_shape
from roll.overlay_roll import OverlayRoll
from window import domino_overlay as dov
from window import look
from window.tool_picker import ToolPicker
from window.widgets import Tooltip

NOT_DRAWING = ("select", "slice", "lasso")  # (tools that pick Spiderweb's shapes: Domino's own mouse instead)
LEFT_OUT = ("hz", "picture")  # (they open windows of their own: from the main window)
BAR_GAP = 8  # px between the bar and the grid's top right corner at first
DOMINO_KEYS = 128
MARK = "domino_send"  # (a shape key: drawn / taken here, goes into Domino with Send; dropped when this ends)
REST_S = 0.25  # a new shape goes into Domino once nothing changed for this long (no drag / typing going on)
SAID_MS = 4000


def bar_tools():
    from window.app import DRAW_TOOLS  # (it imports this file's caller)
    return [t for t in DRAW_TOOLS if t[0] not in LEFT_OUT]


def start(app):
    """The "Draw on Domino" button: follow the front-most Domino (started first when none runs)."""
    if app.companion is not None:
        return app.companion.show_bar()
    if not dov.available():
        return messagebox.showinfo(tr("companion.title"), tr("companion.not_windows"), parent=app)
    if getattr(app, "loaded", None) is not None:
        return messagebox.showinfo(tr("companion.title"), tr("companion.no_loaded"), parent=app)
    d, why = dov.front_domino()
    if d is None and why == "unknown build":
        return unknown_build(app)
    if d is None:
        path = domino_exe(app)
        if not path:
            return None
        if dov.start_domino(path) is None:
            return messagebox.showerror(tr("companion.title"), tr("companion.not_started", path=path), parent=app)
    app.companion = Companion(app)
    return app.companion


def unknown_build(app):
    known = ", ".join(b.name for b in dv.BUILDS.values())
    messagebox.showinfo(tr("companion.title"), tr("companion.unknown_build", known=known), parent=app)


def domino_exe(app):
    """Domino.exe to start: the one used last, else asked (and remembered in settings.ini)."""
    path = settings.get("domino", "exe") or ""
    if os.path.isfile(path) and dv.build_of(path) is not None:
        return path
    path = filedialog.askopenfilename(parent=app, title=tr("companion.pick_exe"),
                                      filetypes=[("Domino.exe", "Domino.exe"), ("*.exe", "*.exe")])
    if not path:
        return None
    path = os.path.normpath(path)
    if dv.build_of(path) is None:
        unknown_build(app)
        return None
    settings.put("domino", "exe", path)
    return path


class Companion:
    def __init__(self, app, pid=None):  # (pid: follow only that Domino; tests give one that doesn't run)
        self.app = app
        self.main_roll = app.roll
        self.main_roll.cancel_draft()
        self.main_roll.end_typing()
        self.bar_at = self.saved_bar_at()
        self._grip = None
        self._placed = None
        self.pending = []      # shapes drawn here, waiting to go into Domino (once nothing changes: REST_S)
        self.linked = []       # undo / redo steps that changed Domino too (sent, taken): Ctrl+Z there = Domino's
        self.busy = False      # (a paste / copy going on with Domino)
        self.was_front = None  # (the window with the keyboard before it: given back after, domino_link.give_back)
        self._own = 0.0        # (when Spiderweb last sent something to Domino: Domino in front just then = ours)
        self._gen = (app.shapes_gen, time.monotonic())
        self._said = None
        self.old_ppq = None    # the project's PPQ before Domino's was put in (back when this ends)
        self.bar = self.make_bar()  # (first: the overlay's first look at Domino places it)
        self.overlay = dov.DominoOverlay(app, pid=pid, on_view=self.on_view, on_state=self.on_state,
                                         on_tick=self.on_tick,
                                         make_canvas=lambda p: OverlayRoll(p, app, app.scale, dov.SEE_THROUGH,
                                                                           wheel=self.wheel))
        self.roll = self.overlay.canvas
        self.roll.fit_view()
        self.overlay.forward_to(self.roll, wheel=self.wheel, pressed=self.pressed)
        self.overlay.above = [self.bar]
        app.companion_tops = (self.overlay.win, self.overlay.catch, self.bar)
        app.roll = self.roll
        if app.tool.get() in NOT_DRAWING or app.tool.get() in LEFT_OUT:  # (MY PICK: it starts drawing)
            app.tool.set(app.draw_tool if app.draw_tool not in LEFT_OUT else "line")
        self._trace = app.tool.trace_add("write", lambda *_: self.sync_mode())
        self.sync_mode()
        app.withdraw()
        self.match_ppq()

    # --- Domino's song PPQ ---
    def domino_ppq(self):
        d = self.overlay.domino
        got = dv.song_place(d) if d is not None else None
        return got[1] if got else None

    def match_ppq(self):
        """The project's PPQ = Domino's song PPQ (notes are made in its ticks; MY PICK: the old one back when this
        ends). True when they match now."""
        ppq = self.domino_ppq()
        if ppq is None:
            return False
        app = self.app
        if ppq != app.ppq:
            if self.old_ppq is None:
                self.old_ppq = app.pvar["ppq"].get()
            app.pvar["ppq"].set(str(ppq))
            app.on_project_change(True, ppq_done=True)
            self.say(tr("companion.ppq_set", ppq=ppq))
        return app.ppq == ppq

    # --- the mode: drawing on the overlay, or Domino's own mouse ---
    def drawing(self):
        return self.app.tool.get() not in NOT_DRAWING

    def sync_mode(self):
        if self.app.tool.get() in LEFT_OUT:  # (a key pressed for one: the main window's tool, not here)
            return self.app.tool.set("line")
        self.overlay.set_drawing(self.drawing())

    def pressed(self):
        """A press on the overlay: Spiderweb takes the keyboard (Esc, Ctrl+Z, Delete... for what's drawn)."""
        try:
            self.roll.focus_force()
        except tk.TclError:
            pass

    def wheel(self, e):
        """The wheel over the overlay: Domino scrolls / zooms as if it were turned over its own piano roll."""
        d = self.overlay.domino
        g = d.grid if d is not None else None
        if g:
            send_wheel(g[0], e.delta, e.state, e.x_root, e.y_root)
        return "break"

    # --- following Domino ---
    def on_view(self, v):
        self.roll.set_domino_view(v, DOMINO_KEYS)
        self.place_bar()

    def on_tick(self):
        v = self.overlay.view
        if v is not None:
            self.roll.set_domino_view(v, DOMINO_KEYS)  # (only redraws when something moved our view)
            if self.bar.winfo_viewable():  # (hidden: placed when shown again, show_bar)
                self.place_bar()
        d = self.overlay.domino
        if d is None:
            return
        if self.linked and dl.in_front(d) and time.monotonic() - self._own > 0.6:  # (the user works in Domino:
            # its own Ctrl+Z from now on)
            self.unlink()
        if self.busy or not self.resting():
            return
        if self.app.ppq != (self.domino_ppq() or self.app.ppq):  # (another song opened in Domino)
            self.match_ppq()
            return
        here = [sh for sh in self.pending if any(sh is s for s in self.app.shapes)]
        self.pending = here
        if here:
            self.send(here)

    def resting(self):
        """Nothing being drawn / dragged / typed, and no shape changed for REST_S."""
        app, r = self.app, self.roll
        if r.drag or r.draft or r.typing or getattr(app, "scrubbing", None):
            self._gen = (app.shapes_gen, time.monotonic())
            return False
        if self._gen[0] != app.shapes_gen:
            self._gen = (app.shapes_gen, time.monotonic())
            return False
        return time.monotonic() - self._gen[1] >= REST_S

    # --- notes into Domino ---
    def drawn(self, sh):
        """App.add_shape: a shape drawn here goes into Domino once it's finished."""
        sh[MARK] = 1
        self.pending.append(sh)

    def marked(self):
        return [sh for sh in self.app.shapes if sh.get(MARK)]

    def send_all(self):
        """The bar's Send: every shape drawn / taken here that's still on the overlay goes into Domino."""
        if not self.busy:
            shapes = self.marked()
            if shapes:
                self.send(shapes)
            else:
                self.say(tr("companion.nothing_to_send"))

    def send(self, shapes):
        """shapes (on the overlay) -> pasted into Domino's current track at their place, then off the overlay."""
        app, d = self.app, self.overlay.domino
        if d is None or not self.match_ppq():
            return self.say(tr("companion.send_failed_domino"), warn=True)
        rows = self.notes_for(shapes)
        rows = rows[(rows[:, 2] >= 0) & (rows[:, 2] <= 127)]
        self.pending = [s for s in self.pending if not any(s is x for x in shapes)]
        if not len(rows):
            return self.say(tr("companion.no_notes"), warn=True)
        first = int(rows[:, 0].min())
        self.was_front = dl.front_window()  # (before the ruler click: Domino may take the keyboard)
        at = dl.put_cursor(d, first)
        if at is None:
            self.pending += shapes
            dl.give_back(d, self.was_front)
            return self.say(tr("companion.send_failed_cursor"), warn=True)
        notes, _ = cut_gates(rows)
        ppq = app.ppq
        raw = clip_data(notes, ppq, int(app.beats * ppq), "note", span=(at, max(int(notes[:, 1].max()) - at, 1)),
                        own=())  # (notes_for: no tracks of yours, Domino's current one)
        self.busy = True
        dl.Paste(self.bar, d, raw, lambda ok, why: self.sent(shapes, ok, why, len(notes)))

    def notes_for(self, shapes):
        """The notes shapes put into Domino: made for them ALONE (other shapes shown on the overlay never cut them),
        overlaps kept like Domino keeps them (Single channel = one track, its notes as drawn: a Take -> Send round trip
        gives Domino's notes back exactly; Multi channel = a track per channel, from Domino's current one down)."""
        from window.app import all_notes  # (app.py imports this file's caller)
        app = self.app
        mode = app.channel_mode.get()
        rows = all_notes(shapes, app.ppq, app.keys, "raw" if mode == "single" else mode, app.channel_split,
                         app.all16(), app.notes_tracks, rounds=app.rounds_apart(), order=app.in_order(),
                         keep=app.keep_colours.get(), busy=app.cc_busy(mode, shapes))[2]
        rows = np.array(rows, np.int64)
        if mode == "single" and len(rows):
            rows[:, 4] = 0
        return rows

    def sent(self, shapes, ok, why, n):
        self.busy, self._own = False, time.monotonic()
        if self.overlay.domino is not None:
            dl.give_back(self.overlay.domino, self.was_front)
        app = self.app
        if not ok:
            self.pending += shapes  # (tried again once something changes; Send too)
            return self.say(tr("companion.send_failed_" + why), warn=True)
        gone = {i for i, s in enumerate(app.shapes) if any(s is x for x in shapes)}
        if gone:
            app.roll.cancel_draft()
            app.push_undo(name=tr("companion.sent_step"))
            for i in sorted(gone, reverse=True):
                del app.shapes[i]
            app.select(None)
            app.shapes_changed()
            self.link(app.undo_stack[-1])
        self._gen = (app.shapes_gen, time.monotonic())
        self.say(trn("companion.sent_n", n, n=n))

    # --- Domino's selection out ---
    def take(self):
        """The bar's Take: Domino's selection copied, deleted there, and on the overlay as a shape at its place."""
        d = self.overlay.domino
        if d is None or self.busy or not self.match_ppq():
            return
        self.busy, self.was_front = True, dl.front_window()
        dl.Copy(self.bar, d, self.took)

    def took(self, rows, ppq, start):
        self.busy, self._own = False, time.monotonic()
        if self.overlay.domino is not None:
            dl.give_back(self.overlay.domino, self.was_front)
        app, d = self.app, self.overlay.domino
        if rows is None:
            return self.say(tr("companion.take_" + ppq), warn=True)
        if d is None or ppq != app.ppq:
            return self.say(tr("companion.send_failed_domino"), warn=True)
        first = int(rows[:, 0].min())
        rows = rows.copy()
        rows[:, 0] -= first
        made = notes_shape(rows, ppq, tr("companion.taken_name"))
        tracks = np.unique(rows[:, 4])
        made["cols"] = (tracks - tracks.min()).tolist()
        sh = clean_shape({**SHAPE_DEFAULTS, **app.defaults, **made})
        sh[MARK] = 1
        dl.command(d, dl.DELETE)
        dl.give_back(d, self.was_front)
        app.roll.cancel_draft()
        app.add_copies([sh], (start + first) / ppq, tr("companion.take_step"))
        self.link(app.undo_stack[-1])
        self._gen = (app.shapes_gen, time.monotonic())
        n = len(rows)
        self.say(trn("companion.took_n", n, n=n))

    # --- undo: steps that changed Domino too ---
    def link(self, step):
        self.linked.append(step)

    def undo_step(self, redo):
        """history.undo_roll: the next step changed Domino too (sent / taken) = undone / redone in Domino as well.
        True when done here."""
        app, d = self.app, self.overlay.domino
        stack = app.redo_stack if redo else app.undo_stack
        if not stack or not any(stack[-1] is s for s in self.linked):
            return False
        if d is None or self.busy:
            return True  # (Domino gone / busy: nothing undone on one side only)
        old = stack[-1]
        app.redo() if redo else app.undo()
        new = (app.undo_stack if redo else app.redo_stack)[-1:]
        if not new or old is new[0]:
            return True
        was = dl.front_window()
        dl.command(d, dl.REDO if redo else dl.UNDO)
        dl.give_back(d, was)
        self._own = time.monotonic()
        self.linked = [s for s in self.linked if s is not old] + new
        self.pending = []  # (a shape given back stays on the overlay until Send)
        self._gen = (app.shapes_gen, time.monotonic())
        return True

    def unlink(self):
        """Domino was used: its own Ctrl+Z takes over. The steps that changed Domino leave Spiderweb's history (undoing
        them here would change the shapes without Domino)."""
        app = self.app
        for stack in (app.undo_stack, app.redo_stack):
            stack[:] = [s for s in stack if not any(s is x for x in self.linked)]
        self.linked = []
        app.sync_history(force=True)

    def say(self, text, warn=False):
        """A short line on the bar (the main window's status line is hidden)."""
        try:
            self.said.configure(text=text, foreground=look.WARN if warn else look.HINT)
            if not self.said.winfo_manager():
                self.said.pack(side="left", padx=(6, 2))
            if self._said:
                self.bar.after_cancel(self._said)
            self._said = self.bar.after(SAID_MS, self.unsay)
        except tk.TclError:
            pass

    def unsay(self):
        self._said = None
        try:
            self.said.pack_forget()
        except tk.TclError:
            pass

    def on_state(self, reason):
        if reason is None:
            self.status.configure(text="")
            self.status.pack_forget()
            self.show_bar()
        elif reason == "hidden" and self.overlay.domino is not None:
            self.bar.withdraw()  # (Domino minimised: the bar goes with it)
        else:
            self.status.configure(text=tr("companion.state_" + reason.replace(" ", "_")))
            if not self.status.winfo_manager():
                self.status.pack(side="left", padx=(6, 2))
            self.show_bar()

    # --- the floating bar ---
    def make_bar(self):
        app = self.app
        bar = tk.Toplevel(app)
        bar.withdraw()
        bar.overrideredirect(True)
        bar.title(tr("companion.title"))
        edge = tk.Frame(bar, bg=look.COMPANION_EDGE, padx=1, pady=1)
        edge.pack(fill="both", expand=True)
        row = ttk.Frame(edge, padding=(2, 2))
        row.pack(fill="both", expand=True)
        grip = self.grip = ttk.Label(row, text="⠿", cursor="fleur", padding=(3, 0))
        grip.pack(side="left")
        Tooltip(grip, tr("companion.grip_tip"))
        grip.bind("<ButtonPress-1>", self.grip_press)
        grip.bind("<B1-Motion>", self.grip_drag)
        grip.bind("<ButtonRelease-1>", self.grip_release)
        b = ttk.Radiobutton(row, text=tr("companion.pointer"), value="select", variable=app.tool,
                            style="Toolbutton", takefocus=False)
        b.pack(side="left", padx=(2, 2))
        Tooltip(b, tr("companion.pointer_tip"))
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=3, pady=2)
        self.picker = ToolPicker(app, row, bar_tools(), owner=bar, fit=lambda: None)
        self.picker.frame.pack(side="left")
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=3, pady=2)
        ttk.Button(row, text=tr("app.undo"), takefocus=False,
                   command=lambda: app.undo_roll()).pack(side="left", padx=(2, 0))
        ttk.Button(row, text=tr("app.redo"), takefocus=False,
                   command=lambda: app.undo_roll(True)).pack(side="left", padx=(2, 0))
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=3, pady=2)
        for key, run in (("take", self.take), ("send", self.send_all), ("flip_sideways", lambda: app.flip(True)),
                         ("flip_upside", lambda: app.flip(False)), ("stir", lambda: self.open_tool("stir")),
                         ("sustain", lambda: self.open_tool("sustain"))):
            b = ttk.Button(row, text=tr("companion." + key), takefocus=False, command=run)
            b.pack(side="left", padx=(2, 0))
            Tooltip(b, tr("companion." + key + "_tip"))
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=3, pady=2)
        b = ttk.Button(row, text=tr("companion.back"), takefocus=False, command=self.stop)
        b.pack(side="left", padx=(2, 0))
        Tooltip(b, tr("companion.back_tip"))
        self.status = ttk.Label(row, text="", foreground=look.WARN)
        self.said = ttk.Label(row, text="", foreground=look.HINT)
        return bar

    def open_tool(self, which):
        """Stir… / Sustain… for the selected shapes (a taken selection), shown over Domino (their window belongs
        to the main one, hidden while this is on)."""
        app = self.app
        if not app.sels:
            return self.say(tr("companion.pick_first"), warn=True)
        if which == "stir":
            from window.stir_window import open_stir
            open_stir(app)
            w = app.stir_window
        else:
            from window.sustain_window import open_sustain
            open_sustain(app)
            w = app.sustain_window
        if w:
            try:
                w.transient(self.bar)
                w.deiconify()
                w.lift()
            except tk.TclError:
                pass

    def show_bar(self):
        if self.bar.winfo_exists() and not self.bar.winfo_viewable():
            self.bar.deiconify()
            self._placed = None
            self.place_bar()
            self.app.tips.show("companion", parent=self.bar, wait=True)  # (the first time only, under the bar)

    def saved_bar_at(self):
        try:
            dx, dy = (int(v) for v in (settings.get("domino", "bar_at") or "0,0").split(","))
            return dx, dy
        except ValueError:
            return 0, 0

    def bar_spot(self):
        """Where the bar goes: the grid's top right corner (+ where it was dragged to), kept on the screen."""
        v = self.overlay.view
        if self.bar.winfo_viewable():  # (its right edge stays put when a longer tool name widens it)
            w, h = self.bar.winfo_width(), self.bar.winfo_height()
        else:
            self.bar.update_idletasks()
            w, h = self.bar.winfo_reqwidth(), self.bar.winfo_reqheight()
        if v is None:  # (nothing to follow yet: near the middle of the screen)
            return (self.bar.winfo_screenwidth() - w) // 2, self.bar.winfo_screenheight() // 3
        g = v.grid
        x = g[2] - w - BAR_GAP + self.bar_at[0]
        y = g[1] + BAR_GAP + self.bar_at[1]
        left, top, right, bottom = system.work_area(self.bar, x + w // 2, y + h // 2)
        return min(max(x, left), right - w), min(max(y, top), bottom - h)

    def place_bar(self):
        if self._grip is not None or not self.bar.winfo_exists():
            return
        spot = self.bar_spot()
        if spot != self._placed:
            self._placed = spot
            self.bar.geometry(f"+{spot[0]}+{spot[1]}")
            # (at once: Tk moves it only when idle, and keeping it above Domino before that made Tk forget the move)
            self.bar.update_idletasks()
        if not self.bar.winfo_viewable() and self.overlay.state is None:
            self.bar.deiconify()

    def grip_press(self, e):
        self._grip = (e.x_root - self.bar.winfo_rootx(), e.y_root - self.bar.winfo_rooty())

    def grip_drag(self, e):
        if self._grip is not None:
            self.bar.geometry(f"+{e.x_root - self._grip[0]}+{e.y_root - self._grip[1]}")

    def grip_release(self, e):
        if self._grip is None:
            return
        self._grip = None
        v = self.overlay.view
        if v is not None:  # (remembered against Domino's grid: it moves along with Domino)
            w = self.bar.winfo_width()
            g = v.grid
            self.bar_at = (self.bar.winfo_rootx() - (g[2] - w - BAR_GAP), self.bar.winfo_rooty() - (g[1] + BAR_GAP))
            settings.put("domino", "bar_at", f"{self.bar_at[0]},{self.bar_at[1]}")
        self._placed = None
        self.place_bar()

    # --- back to the main window ---
    def stop(self):
        app = self.app
        try:
            app.tool.trace_remove("write", self._trace)
        except tk.TclError:
            pass
        self.roll.cancel_draft()
        self.roll.end_typing()
        if self._said:
            self.bar.after_cancel(self._said)
        for sh in app.shapes:  # (not sent: normal shapes of the project now)
            sh.pop(MARK, None)
        self.unlink()  # (Domino's own Ctrl+Z for what went into it)
        app.roll = self.main_roll
        app.companion = None
        app.companion_tops = ()
        if self.old_ppq is not None and app.pvar["ppq"].get() != self.old_ppq:  # (the project's own PPQ back)
            app.pvar["ppq"].set(self.old_ppq)
            app.on_project_change(True, ppq_done=True)
        self.picker.close()
        self.overlay.stop()
        try:
            self.bar.destroy()
        except tk.TclError:
            pass
        app.deiconify()
        self.main_roll.request_redraw()
        self.main_roll.focus_set()


def send_wheel(hwnd, delta, state, x, y):
    """The wheel turned over Domino's piano roll at screen x, y (Shift / Ctrl as held)."""
    if not system.WINDOWS:
        return
    import ctypes
    keys = (0x4 if state & 0x1 else 0) | (0x8 if state & 0x4 else 0)  # MK_SHIFT, MK_CONTROL
    w = ((delta & 0xFFFF) << 16) | keys
    lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x020A, ctypes.c_size_t(w), ctypes.c_ssize_t(lp))
