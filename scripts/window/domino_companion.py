"""Draw on Domino (Domino companion mode, Windows only): Spiderweb's drawing tools right on Domino's piano roll.

Spiderweb's main window hides; a see-through piano roll of ours (roll/overlay_roll.py) lies exactly over Domino's
note grid (window/domino_overlay.py follows Domino's window, scroll and zoom) and is app.roll while this is on, so
every tool, undo step and shape works as in the main window. A small floating bar over Domino holds the tools:
"Domino" = Domino's own mouse (clicks go through to Domino; Domino's select box is the only selection, user
2026-11-05), a drawing tool = drawing on the overlay, "Spiderweb" = back to the main window.
The wheel always goes to Domino (its scroll and zoom are the view). Reading Domino: files/domino_view.py.
NOTES INTO DOMINO (files/domino_link.py): a shape drawn here stays on the overlay to be changed (curve handles...)
until you're done with it (user 2026-11-06): the next shape drawn, Domino's mouse picked (bar, Esc, double
right-click), Send / Enter, or a key that's Domino's; then it goes into Domino's current track (Domino's own paste
at the shape's start; the shape leaves the overlay), with the project's PPQ set to Domino's song PPQ while this is
on. Ctrl+Z right after takes it out of Domino again (Domino's own undo) and gives the shape back, until Domino is
used (then Domino's own Ctrl+Z). "Take" lifts Domino's selection out (copied, then deleted in Domino) as a shape on
the overlay, for Spiderweb's tools; "Send" puts the shapes on the overlay back in; the flips on a Domino selection
do Take + flip + Send in one click. Shapes of the project made before this started are hidden while it's on.
KEYS: the bar never takes the keyboard; on the overlay only Spiderweb's keys for what's drawn here stay (Esc, Enter,
Ctrl+Z / Y for its own steps, Delete for picked shapes, tool letters, typing), every other key goes on to Domino.
"""

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import numpy as np

from files import domino_link as dl
from files import domino_shapes as dsh
from files import domino_view as dv
from files import settings, system
from files.domino_clip import LONGEST, MOST_NOTES, clip_data, cut_gates
from files.lang import tr, trn
from notes.custom import notes_shape
from notes.engine import SHAPE_DEFAULTS, clean_shape
from roll.overlay_roll import OverlayRoll
from window import domino_overlay as dov
from window import look
from window.big_ask import ask as ask_big
from window.tool_picker import ToolPicker
from window.widgets import Tooltip

NOT_DRAWING = ("select", "slice", "lasso")  # (tools that pick Spiderweb's shapes: Domino's own mouse instead)
LEFT_OUT = ("hz", "picture")  # (they open windows of their own: from the main window)
BAR_GAP = 8  # px between the bar and the grid's top right corner at first
DOMINO_KEYS = 128
MARK = "domino_send"  # (a shape key: drawn / taken here, goes into Domino with Send; dropped when this ends)
REST_S = 0.25  # Domino's song PPQ checked once nothing changed for this long (no drag / typing going on)
SAID_MS = 4000
LOOK_AGAIN_S = 1.5  # the remembered shapes looked at again this often even when Domino's undo count stays (a full list)
KEY_TAG = "DominoCompanionKeys"
ALONE_KEYS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Caps_Lock", "Num_Lock",
              "Win_L", "Win_R", "App", "Super_L", "Super_R"}  # (pressed on their own: nothing to pass on)
CTRL, ALT = 0x4, 0x20000  # (Tk's modifier bits on Windows)


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
        self.pending = []      # the shape drawn last here: changed still, goes into Domino when you're done with it
        self.ready = []        # shapes done with: into Domino as soon as nothing else is going to Domino
        self.landed = []       # (shapes, n, Domino steps) in Domino now: off the overlay once nothing is held there
        self.after_send = []   # (key code, state) of Domino's pressed while sending: pressed there after, in order
        self.job = None        # the Domino a send / Take going on talks to (from start to end, hunt 2026-11-06)
        self.leaving = False   # (Spiderweb pressed: what waits goes in first, then back to the main window)
        self.adopted = []      # (windows of the main window's shown over Domino: given back when this ends)
        self.old_tops = {str(w) for w in app.winfo_children() if isinstance(w, tk.Toplevel)}  # (made before: left be)
        self._ticks = 0
        self.quick = None      # a flip on Domino's selection (Take + flip + Send): its steps leave the history
        self._then = None      # (what a Take does next with the shape it made)
        held = self._before_held = app.undo_stack + app.redo_stack  # (kept: their ids stay theirs)
        self.before = {id(s) for s in held}  # (steps from before this started: never undone from here)
        self.linked = []       # undo / redo steps that changed Domino too (sent, taken): Ctrl+Z there = Domino's
        self.domino_steps = {}  # id(linked step) -> how many Domino undo steps it is (tracks added + the paste)
        self.busy = False      # (a paste / copy going on with Domino)
        self.was_front = None  # (the window with the keyboard before it: given back after, domino_link.give_back)
        self._own = 0.0        # (when Spiderweb last sent something to Domino: Domino in front just then = ours)
        self._gen = (app.shapes_gen, time.monotonic())
        self._said = None
        self.old_ppq = None    # the project's PPQ before Domino's was put in (back when this ends)
        # shapes remembered inside Domino (PLAN K, files/domino_shapes.py): kept on the app, so they outlive this
        self.records = app.__dict__.setdefault("domino_records", [])
        self.sending = None    # (shapes, rows, first track) of the send going on: remembered once it lands
        self.new_records = []  # (shapes, record) landed, not yet off the overlay (take_off links them to the step)
        self.step_records = {}  # id(linked step) -> its record (Spiderweb's Ctrl+Z of a send drops it, Ctrl+Y back)
        self.looked = (None, 0.0)  # (Domino's undo depth, when) at the last look at the records
        self.last_events = []  # (what the last look found: tests)
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
        app.companion = self
        dov.no_keyboard(self.bar)
        self.roll.bindtags((KEY_TAG,) + self.roll.bindtags())
        app.bind_class(KEY_TAG, "<KeyPress>", self.key)
        # the tool used last here (user 2026-11-06: remembered), else the main window's drawing tool (MY PICK: it
        # starts drawing)
        keys = [t[0] for t in bar_tools()]
        saved = settings.get("domino", "tool")
        tool = saved if saved in keys else app.draw_tool if app.draw_tool in keys else "line"
        if app.tool.get() != tool:
            app.tool.set(tool)
        self._trace = app.tool.trace_add("write", lambda *_: self.sync_mode())
        self.sync_mode()
        app.select(None)  # (the project's shapes are hidden while this is on: user 2026-11-06)
        app.track_view()
        self.roll.request_redraw()
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
        tool = self.app.tool.get()
        if tool in LEFT_OUT:  # (a key pressed for one: the main window's tool, not here)
            return self.app.tool.set("line")
        self.picker.tool_changed()  # (the bar's tool button shows the tool picked any way: list, key)
        if self.drawing():
            if settings.get("domino", "tool") != tool:
                settings.put("domino", "tool", tool)
        else:  # Domino's own mouse: what's drawn here is done (it goes in), and Domino gets the keyboard back
            self.done_drawing()
            d = self.overlay.domino
            if d is not None and not self.busy:
                dl.to_domino(d)
        self.overlay.set_drawing(self.drawing())

    # --- keys: Spiderweb's for what's drawn here, the rest Domino's ---
    def own_key(self, e):
        """True when the key pressed on the overlay is Spiderweb's (user 2026-11-06: Space, Ctrl+Z ... must work in
        Domino): typing, Esc / Enter, Ctrl+Z / Y while a step made here is next, Delete with shapes picked, a
        drawing tool's letter. Domino's mouse picked: every key is Domino's."""
        ks, r, app = e.keysym, self.roll, self.app
        if ks in ALONE_KEYS or r.typing:
            return True
        if not self.drawing():
            return False
        ctrl, alt = e.state & CTRL, e.state & ALT
        if ks in ("Escape", "Return", "KP_Enter"):
            return True
        if ks in ("Delete", "BackSpace") and not ctrl:
            return bool(app.sels) or not r.idle()
        if ctrl and ks.lower() in ("z", "y"):
            redo = ks.lower() == "y" or bool(e.state & 0x1)
            stack = app.redo_stack if redo else app.undo_stack
            return not r.idle() or bool(stack) and id(stack[-1]) not in self.before
        return not ctrl and not alt and ks.lower() in {t[2] for t in bar_tools()}

    def key(self, e):
        """A key on the overlay (its own bind tag, first): Spiderweb's goes on as usual; Domino's goes to Domino,
        pressed again there, after what's drawn here went in (pressing a key of Domino's = done drawing)."""
        if self.own_key(e):
            return None
        d = self.overlay.domino
        if d is None:
            return "break"
        k = (e.keycode, e.state)
        if self.pending or self.ready or self.busy:
            self.after_send.append(k)
            self.done_drawing()
        else:
            dl.pass_key(d, *k)
        return "break"

    def pass_waiting_key(self):
        keys, self.after_send = self.after_send, []
        d = self.overlay.domino
        for k in keys if d is not None else ():
            dl.pass_key(d, *k)

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
        self.close_list_elsewhere()
        try:
            self.watch()
        except Exception:  # (a failed look never stops following Domino; logged once a minute at most)
            if time.monotonic() - getattr(self, "_watch_err", -99) > 60:
                self._watch_err = time.monotonic()
                import sys
                from files.errors import write_log
                write_log(*sys.exc_info(), "(Draw on Domino: looking at the remembered shapes)")
        self._ticks += 1
        if self._ticks % 10 == 0:
            self.adopt_windows()
        if self.landed:
            self.take_off()
        self.flush()
        if self.busy or not self.resting():
            return
        if self.app.ppq != (self.domino_ppq() or self.app.ppq):  # (another song opened in Domino)
            self.match_ppq()

    def adopt_windows(self):
        """Windows opened from the overlay (its right-click menu: Claw machine, Tumours, Copy along a path...) belong
        to the main window, hidden while this is on, so they never showed (hunt 2026-11-06): shown over Domino,
        belonging to the bar until back."""
        app = self.app
        mine = {str(w) for w in app.companion_tops} | self.old_tops
        for w in app.winfo_children():
            if not isinstance(w, tk.Toplevel) or str(w) in mine or any(w is a for a in self.adopted):
                continue
            try:  # (a transient of the withdrawn main window is withdrawn with it: only windows made since this
                # started, so ones kept hidden on purpose never pop up)
                if not w.winfo_viewable() and str(w.wm_transient()) == str(app):
                    w.transient(self.bar)
                    w.deiconify()
                    w.lift()
                    self.adopted.append(w)
            except tk.TclError:
                pass

    def close_list_elsewhere(self):
        """The bar's tool list closes when clicked outside it (it never has the keyboard, so it can't tell)."""
        p = self.picker.popup
        if p is None or not dl.mouse_down():
            return
        try:
            x, y = p.winfo_pointerxy()
            for w in (p, self.bar):
                if w.winfo_rootx() <= x < w.winfo_rootx() + w.winfo_width() and \
                        w.winfo_rooty() <= y < w.winfo_rooty() + w.winfo_height():
                    return
        except tk.TclError:
            return
        self.picker.close()

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
        """App.add_shape: a shape drawn here waits, to be changed still; the one drawn before it is done now (user
        2026-11-06: it goes into Domino when you start the next one)."""
        sh[MARK] = 1
        done = [s for s in self.pending if s is not sh]
        self.pending = [sh]
        self.queue(done)

    def done_drawing(self):
        """Done with the shape drawn last (Domino's mouse picked, a key of Domino's): it goes into Domino."""
        done, self.pending = self.pending, []
        self.queue(done)

    def alive(self, shapes):
        """Those still on the overlay (not undone / deleted, not in Domino already)."""
        gone = [s for shs, *_ in self.landed for s in shs]
        return [sh for sh in shapes if any(sh is s for s in self.app.shapes) and not any(sh is s for s in gone)]

    def queue(self, shapes):
        shapes = [sh for sh in self.alive(shapes) if not any(sh is s for s in self.ready)]
        if shapes:
            self.ready += shapes
            self.app.after_idle(self.flush)
        elif self.after_send and not self.busy and not self.ready:
            self.pass_waiting_key()

    def flush(self):
        """The shapes done with go into Domino (one paste at a time; never while Domino is mid-action: the mouse
        held, a menu / window of Domino's open: tried again on the next tick, hunt 2026-11-06)."""
        if self.busy:
            return
        self.ready = self.alive(self.ready)
        d = self.overlay.domino
        if self.ready and d is not None and dl.alive(d) and dl.busy(d):
            return
        shapes, self.ready = self.ready, []
        if shapes:
            self.send(shapes)
        if not self.busy and self.after_send:  # (nothing went: the key of Domino's goes on anyway)
            self.pass_waiting_key()

    def marked(self):
        return [sh for sh in self.app.shapes if sh.get(MARK)]

    def send_all(self):
        """The bar's Send / Enter: every shape drawn / taken here that's still on the overlay goes into Domino."""
        shapes = self.marked()
        self.pending = []
        if shapes:
            self.queue(shapes)
        elif not self.busy:
            self.say(tr("companion.nothing_to_send"))

    def send(self, shapes):
        """shapes (on the overlay) -> pasted into Domino at their place, then off the overlay. Single channel: into
        the current track, overlaps removed with the notes already there too; Multi channel / As drawn: a track per
        channel from the current one down, new tracks added at the bottom when the list runs out (user 2026-11-06)."""
        app, d = self.app, self.overlay.domino
        if d is None or not self.match_ppq():
            return self.say(tr("companion.send_failed_domino"), warn=True)
        rows = self.notes_for(shapes)
        rows = rows[(rows[:, 2] >= 0) & (rows[:, 2] <= 127)]
        self.pending = [s for s in self.pending if not any(s is x for x in shapes)]
        if not len(rows):
            return self.say(tr("companion.no_notes"), warn=True)
        # Copy to Domino's own limits (hunt 2026-11-06): too long / too many refused, a big one asked first
        if int(rows[:, 1].max()) - int(rows[:, 0].min()) > LONGEST:
            return self.say(tr("project.too_long_to_copy_for_domino", longest=LONGEST), warn=True)
        if len(rows) > MOST_NOTES:
            return self.say(tr("project.too_many_to_copy_for_domino", notes=len(rows), most=MOST_NOTES), warn=True)
        got = dv.song_tracks(d)
        if got is not None and got[0] == 0:  # (the Conductor track: tempos there, never notes, hunt 2026-11-06)
            return self.say(tr("companion.conductor_current"), warn=True)
        if not ask_big(app, "domino", len(rows), parent=self.bar):
            return None
        self.was_front = dl.front_window()  # (before the ruler click: Domino may take the keyboard)
        self.busy, self.job = True, d
        self.sending = (shapes, rows.copy(), got[0] if got is not None else None)
        try:
            if app.channel_mode.get() == "single":
                there = []
                dl.Copy(self.bar, d, lambda got, ppq, start: self.safely(
                    self.send_single, shapes, rows, got, ppq, start, there), whole=True, passengers=there)
                return None
            steps = 0
            need = len(np.unique(rows[:, 4]))
            if need > 1 and got is not None and need > len(got[1]) - got[0]:
                steps = dl.add_tracks(d, need - (len(got[1]) - got[0]), app.all16())
                if steps is None:
                    return self.not_sent(tr("companion.send_failed_tracks"))
            self.paste_rows(shapes, rows, int(rows[:, 0].min()), None, steps)
        except Exception:
            self.failed()
        return None

    def safely(self, run, *args):
        """run(*args) for a step of a send / Take coming back later: a failure never leaves it busy for good (no
        send would go any more, hunt 2026-11-06)."""
        try:
            run(*args)
        except Exception:
            self.failed()

    def failed(self):
        import sys
        from files.errors import write_log
        write_log(*sys.exc_info(), "(Draw on Domino)")
        self.busy, self.quick = False, None
        self.say(tr("companion.send_failed_error"), warn=True)
        self.app.after_idle(self.flush)

    def not_sent(self, text):
        """A send stopped before anything was pasted: the shapes stay on the overlay (Send tries again)."""
        self.busy = False
        d = self.job
        if d is not None and self.drawing() and dl.alive(d):
            dl.give_back(d, self.was_front)
        self.say(text, warn=True)
        self.app.after_idle(self.flush)

    def paste_rows(self, shapes, rows, first, end, steps, swap=False, cc=None, since=None):
        """rows (start, end, key, velocity, slot...) pasted at Domino's cursor put on first (or the grid place
        before it); swap: Domino's Substitute Paste over [cursor, end) (the stretch's notes and other events
        replaced). steps = Domino steps made before this send's paste (tracks added). Domino mid-action (the
        mouse held, a menu / window open): tried again a little later (since = when the first try was)."""
        app, d = self.app, self.job
        if not dl.alive(d):
            return self.not_sent(tr("companion.send_failed_domino"))
        since = since or time.monotonic()
        at = dl.put_cursor(d, first)
        if at is None and (dl.busy(d) or dl.mouse_down()) and time.monotonic() - since < 20:
            self.bar.after(60, lambda: self.safely(self.paste_rows, shapes, rows, first, end, steps, swap, cc, since))
            return None
        if at is None:
            return self.not_sent(tr("companion.send_failed_cursor"))
        if swap:  # (the notes from the cursor on: the grid may have put it before first)
            rows = rows[(rows[:, 0] >= at) & (rows[:, 0] < end)]
            cc = {0: [e for e in cc if at <= e[0] < end]} if cc else None
        notes, _ = cut_gates(rows)
        ppq = app.ppq
        length = end - at if swap else max(int(notes[:, 1].max()) - at, 1)
        raw = clip_data(notes, ppq, int(app.beats * ppq), "note", span=(at, length), cc=cc,
                        own=())  # (notes_for: no tracks of yours, Domino's)
        n = len(notes)
        dl.Paste(self.bar, d, raw, lambda ok, why: self.sent(shapes, ok, why, n, steps + 1),
                 cmd=dl.SWAP_PASTE if swap else dl.PASTE)

    def send_single(self, shapes, rows, there, ppq, start, rides):
        """Single channel (user 2026-11-06): the current track's notes read (Select All + Copy), overlaps removed
        over both like Spiderweb's Single channel; Domino's notes that change = the stretch from the first changed
        note on replaced (Substitute Paste, with that stretch's other events as they were): one Domino step."""
        app, d = self.app, self.job
        if not dl.alive(d) or there is None and ppq == "domino":
            return self.not_sent(tr("companion.send_failed_domino"))
        if there is None and ppq == "busy":
            return self.not_sent(tr("companion.send_failed_busy"))
        if there is None and ppq != "nothing":  # (Domino couldn't copy: only the new notes' own overlaps go)
            self.say(tr("companion.track_unread"), warn=True)
        if there is not None and ppq != app.ppq:
            return self.not_sent(tr("companion.send_failed_domino"))
        old = np.zeros((0, 4), np.int64)
        if there is not None:
            old = np.asarray(there, np.int64)[:, :4].copy()
            old[:, 0] += start
        merged, changed, new = dl.merge_single(old, rows[:, :4])
        out = np.zeros((len(merged), 6), np.int64)
        out[:, :4] = merged
        if not len(changed):  # (Domino's notes stay as they are: a plain paste of the new ones)
            if not len(new):
                self.busy = False
                self.remember_safely(shapes, d)
                self.landed.append((shapes, 0, (0, d)))  # (all already there: nothing pasted, nothing to undo there)
                self.take_off()
                return self.app.after_idle(self.flush)
            add = np.zeros((len(new), 6), np.int64)
            add[:, :4] = new
            return self.paste_rows(shapes, add, int(new[:, 0].min()), None, 0)
        moved = np.concatenate([changed[:, 0], new[:, 0]])
        a, b = int(moved.min()), int(moved.max()) + 1
        cc = sorted(((int(t) + start, 0, 0, 0, (tag, body)) for t, _tr, tag, body in rides), key=lambda e: e[0])
        self.paste_rows(shapes, out, a, b, 0, swap=True, cc=cc)

    def notes_for(self, shapes):
        """The notes shapes put into Domino: made for them ALONE (other shapes shown on the overlay never cut them),
        overlaps kept here (Single channel = one track: send_single removes them together with the notes already in
        Domino's track; Multi channel = a track per channel, from Domino's current one down)."""
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

    def sent(self, shapes, ok, why, n, steps=1):
        self.busy, self._own = False, time.monotonic()
        d = self.job
        if dl.alive(d):
            if self.after_send:  # (a key of Domino's pressed meanwhile: Domino gets the keyboard and the key)
                self.pass_waiting_key()
            elif self.drawing():  # (Domino's mouse: Domino keeps the keyboard the paste gave it)
                dl.give_back(d, self.was_front)
        if not ok:  # (they stay on the overlay: Send tries again; tracks added for them stay, empty)
            self.quick = None
            self.say(tr("companion.send_failed_" + why), warn=True)
        else:
            self.remember_safely(shapes, d)
            self.landed.append((shapes, n, (steps, d)))
            self.take_off()
        self.app.after_idle(self.flush)

    # --- shapes remembered inside Domino (PLAN K) ---
    def remember_safely(self, shapes, d):
        """remember(): a failure there never stops the send (the shape just isn't remembered; logged)."""
        try:
            self.remember(shapes, d)
        except Exception:
            import sys
            from files.errors import write_log
            self.sending = None
            write_log(*sys.exc_info(), "(Draw on Domino: remembering a shape)")

    def remember(self, shapes, d):
        """A send landed: a record of the shapes + their notes as Domino holds them now (gates its overlaps cut,
        notes Domino kept only one of left out); other records on those tracks take Spiderweb's own trims."""
        got, self.sending = self.sending, None
        if got is None or not any(s is x for s, x in zip(got[0], shapes)) or got[2] is None:
            return
        _, rows, first = got
        slots = np.unique(rows[:, 4])
        track = first + np.searchsorted(slots, rows[:, 4])  # (a track per channel used, from the current one down)
        tracks = {int(t): dv.track_notes(d, int(t)) for t in np.unique(track)}
        if any(v is None for v in tracks.values()):
            return
        sent = np.column_stack([track, rows[:, 0], rows[:, 1] - rows[:, 0], rows[:, 2], rows[:, 3]])
        import copy as _copy
        rec = dsh.new_record([_copy.deepcopy(s) for s in shapes], dsh.landed(sent, tracks))
        rec["pid"], rec["ppq"] = d.pid, self.app.ppq
        for r in self.records:
            on = {int(t) for t in np.unique(r["notes"][:, 0])}
            if r.get("pid") == d.pid and on & set(tracks):
                need = {t: tracks[t] if t in tracks else dv.track_notes(d, t) for t in on}
                if all(a is not None for a in need.values()):
                    dsh.trimmed(r, need)
        if len(rec["notes"]):
            self.records.append(rec)
            self.new_records.append((shapes, rec))
        self.looked = (dv.undo_depth(d), time.monotonic())

    def watch(self):
        """Each tick: Domino changed (its undo count moved, or a while passed) = the records looked at again."""
        d = self.overlay.domino
        if d is None or self.busy or self.leaving:
            return
        mine = [r for r in self.records if r.get("pid") == d.pid]
        if not mine or dl.mouse_down():
            return
        depth, now = dv.undo_depth(d), time.monotonic()
        if depth == self.looked[0] and now - self.looked[1] < LOOK_AGAIN_S:
            return
        self.looked = (depth, now)
        want = sorted({int(t) for r in mine for t in np.unique(r["notes"][:, 0])})
        tracks = {t: dv.track_notes(d, t) for t in want}
        tracks = {t: a for t, a in tracks.items() if a is not None}  # (a track gone: its records' notes not found)
        events, made = dsh.look(mine, tracks)
        for r in made:
            r["pid"], r["ppq"] = d.pid, mine[0].get("ppq")
        self.records.extend(made)
        self.last_events = events

    def take_off(self, now=False):
        """Shapes now in Domino leave the overlay (one undo step each send) once nothing is held there: a shape
        being changed keeps its place in the list meanwhile."""
        app, r = self.app, self.roll
        if not now and (r.drag or r.draft or r.typing):
            return
        landed, self.landed = self.landed, []
        for shapes, n, steps in landed:
            gone = {i for i, s in enumerate(app.shapes) if any(s is x for x in shapes)}
            if gone:
                keep = [app.shapes[i] for i in sorted(app.sels) if i not in gone]
                main = app.shapes[app.sel] if app.sel is not None and app.sel not in gone else None
                app.push_undo(name=tr("companion.sent_step"))
                for i in sorted(gone, reverse=True):
                    del app.shapes[i]
                at = {id(s): i for i, s in enumerate(app.shapes)}
                if keep:  # (the shape being drawn / changed now stays picked)
                    app.select_many({at[id(s)] for s in keep}, at[id(main)] if main is not None else None)
                else:
                    app.select(None)
                app.shapes_changed()
                self.link(app.undo_stack[-1], steps)
                for got in [g for g in self.new_records if g[0] is shapes]:
                    self.step_records[id(app.undo_stack[-1])] = got[1]
                    self.new_records.remove(got)
                if self.quick and any(s is self.quick.get("shape") for s in shapes):
                    self.quick_done()
            self._gen = (app.shapes_gen, time.monotonic())
            self.say(trn("companion.sent_n", n, n=n))

    def quick_done(self):
        """A flip on Domino's selection is in Domino: its steps (Take, flip, sent) leave Spiderweb's history, it's
        Domino's own (2 Domino steps: its Delete + its Paste) like any change made there."""
        app = self.app
        steps = self.quick["steps"] + app.undo_stack[-1:]  # (+ the step that took it off the overlay)
        self.quick = None
        app.undo_stack[:] = [s for s in app.undo_stack if not any(s is x for x in steps)]
        self.linked = [s for s in self.linked if not any(s is x for x in steps)]
        app.sync_history(force=True)

    # --- Domino's selection out ---
    def take(self, then=None):
        """The bar's Take: Domino's selection copied, deleted there, and on the overlay as a shape at its place.
        then(shape): what's done with that shape next (a flip on Domino's selection)."""
        d = self.overlay.domino
        if d is None or self.busy or not self.match_ppq():
            self.quick = None
            return
        self.done_drawing()  # (what was drawn here first goes in first)
        self.busy, self.was_front, self._then, self.job = True, dl.front_window(), then, d
        dl.Copy(self.bar, d, lambda rows, ppq, start: self.safely(self.took, rows, ppq, start))

    def took(self, rows, ppq, start, tries=0):
        d = self.job
        if rows is not None and dl.alive(d) and dl.busy(d) and tries < 5:
            # (the user acts in Domino right now: the selection may change before the Delete: copied again once
            # Domino is free, so the Delete takes exactly what was copied, hunt 2026-11-06)
            dl.Copy(self.bar, d, lambda r, p, s: self.safely(self.took, r, p, s, tries + 1))
            return
        self.busy, self._own = False, time.monotonic()
        then, self._then = self._then, None
        app, d = self.app, self.job  # (the Domino it was taken from, even if another one is in front now)
        if not dl.alive(d):
            d = None
        if d is not None:
            dl.give_back(d, self.was_front)
        if rows is None or d is None or ppq != app.ppq:
            self.quick = None
            self.app.after_idle(self.flush)
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
        if then:
            then(app.shapes[-1])
        self.app.after_idle(self.flush)

    # --- the flips: on shapes picked here, else on Domino's selection ---
    def flip(self, sideways):
        """↔ / ↕: shapes picked on the overlay = flipped there; none = Domino's selection flipped in one click
        (user 2026-11-06): Take + flip + Send, Domino's own undo for it (2 steps: its Delete + its Paste)."""
        app = self.app
        if app.sels:
            return app.flip(sideways)
        if self.busy or self.overlay.domino is None:
            return
        self.quick = {"steps": [], "shape": None}

        def flipped(sh):
            if self.quick is None:
                return
            self.quick["shape"] = sh
            self.quick["steps"].append(app.undo_stack[-1])  # (the Take)
            app.select(len(app.shapes) - 1)
            app.flip(sideways)
            self.quick["steps"].append(app.undo_stack[-1])  # (the flip)
            self.queue([sh])
        self.take(then=flipped)

    # --- undo: steps that changed Domino too ---
    def link(self, step, steps=1):
        """step changed Domino too: steps = (how many of Domino's own undo steps it made there (tracks added + the
        paste; 0 = nothing pasted), which Domino: undone there even when another one is in front, hunt 11-06)."""
        if not isinstance(steps, tuple):
            steps = (steps, self.job or self.overlay.domino)
        self.linked.append(step)
        self.domino_steps[id(step)] = steps

    def undo_step(self, redo):
        """history.undo_roll: the next step changed Domino too (sent / taken) = undone / redone in Domino as well.
        True when done here."""
        app, d = self.app, self.overlay.domino
        stack = app.redo_stack if redo else app.undo_stack
        if not stack or id(stack[-1]) in self.before:  # (nothing of ours: the project's hidden shapes never change
            # out of sight, user 2026-11-06; Domino's own Undo / Redo instead, the bar's buttons work like its keys)
            if d is not None and not self.busy and dl.alive(d) and not dl.busy(d):
                dl.command(d, dl.REDO if redo else dl.UNDO)
                self._own = time.monotonic()
            return True
        if not any(stack[-1] is s for s in self.linked):
            return False
        if self.busy:
            return True  # (a send / Take going on: nothing undone on one side only)
        old = stack[-1]
        steps, d = self.domino_steps.get(id(old), (1, d))  # (the Domino that step changed, hunt 2026-11-06)
        gone = not dl.alive(d)
        if not gone and steps and dl.busy(d):  # (a window / menu of Domino's open: never sent into it mid-action)
            self.say(tr("companion.undo_busy"), warn=True)
            return True
        app.redo() if redo else app.undo()
        new = (app.undo_stack if redo else app.redo_stack)[-1:]
        if not new or old is new[0]:
            return True
        self.domino_steps.pop(id(old), None)
        rec = self.step_records.pop(id(old), None)  # (a send undone here: its shape isn't in Domino any more)
        if rec is not None:
            if any(r is rec for r in self.records):
                self.records[:] = [r for r in self.records if r is not rec]
            elif not gone:
                self.records.append(rec)
            if not gone:
                self.step_records[id(new[0])] = rec
        if gone:  # (Domino closed / crashed: the shape comes back here, Domino untouched; no longer linked)
            self.linked = [s for s in self.linked if s is not old]
            self.say(tr("companion.undo_gone"), warn=True)
            self._gen = (app.shapes_gen, time.monotonic())
            return True
        was = dl.front_window()
        now = dv.song_tracks(d) if steps > 1 else None
        for _ in range(steps):
            dl.command(d, dl.REDO if redo else dl.UNDO)
        if now is not None:  # (undoing added tracks moves Domino's current track: back to the one it was)
            dl.switch_track(d, now[0])
        dl.give_back(d, was)
        self._own = time.monotonic()
        self.linked = [s for s in self.linked if s is not old] + new
        self.domino_steps[id(new[0])] = (steps, d)
        self.pending = []  # (a shape given back stays on the overlay until Send)
        self._gen = (app.shapes_gen, time.monotonic())
        return True

    def unlink(self):
        """Domino was used: its own Ctrl+Z takes over. The steps that changed Domino leave Spiderweb's history (undoing
        them here would change the shapes without Domino)."""
        app = self.app
        # the steps made here up to the last one that changed Domino go too (hunt 2026-11-06): their shapes are in
        # Domino now, so undoing them changed nothing (a Ctrl+Z eaten) or brought a sent shape back to be sent twice
        last = max((i for i, s in enumerate(app.undo_stack) if any(s is x for x in self.linked)), default=-1)
        app.undo_stack[:] = [s for i, s in enumerate(app.undo_stack)
                             if id(s) in self.before or i > last and not any(s is x for x in self.linked)]
        if self.linked:  # (redoing past a step Domino now owns: nothing to redo here)
            app.redo_stack[:] = [s for s in app.redo_stack if id(s) in self.before]
        self.linked = []
        app.sync_history(force=True)

    def drop_own_steps(self):
        """Back to the main window: every step made here leaves the history (their shapes are in Domino, its own
        undo has them; one undone in the main window brought a sent shape back to be sent twice, hunt 2026-11-06)."""
        app = self.app
        for stack in (app.undo_stack, app.redo_stack):
            stack[:] = [s for s in stack if id(s) in self.before]
        self.linked = []
        app.sync_history(force=True)

    def say(self, text, warn=False):
        """A short line on the bar (the main window's status line is hidden)."""
        try:
            self.said.configure(text=text, foreground=look.WARN if warn else look.HINT, wraplength=self.note_room())
            if not self.said.winfo_manager():
                self.said.pack(anchor="w")
            self.sync_notes()
            if self._said:
                self.bar.after_cancel(self._said)
            self._said = self.bar.after(SAID_MS, self.unsay)
        except tk.TclError:
            pass

    def note_room(self):
        """How wide a message may be: no wider than the buttons (a long one wraps, the bar keeps its width)."""
        return max(120, self.row.winfo_reqwidth() - 12)

    def unsay(self):
        self._said = None
        try:
            self.said.pack_forget()
            self.sync_notes()
        except tk.TclError:
            pass

    def on_state(self, reason):
        if reason is None:
            self.status.configure(text="")
            self.status.pack_forget()
            self.sync_notes()
            self.show_bar()
        elif reason == "hidden" and self.overlay.domino is not None:
            self.bar.withdraw()  # (Domino minimised: the bar goes with it)
        else:
            self.status.configure(text=tr("companion.state_" + reason.replace(" ", "_")), wraplength=self.note_room())
            if not self.status.winfo_manager():
                self.status.pack(anchor="w", before=self.said if self.said.winfo_manager() else None)
            self.sync_notes()
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
        row = self.row = ttk.Frame(edge, padding=(2, 2))
        row.pack(fill="x")
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
        # (as wide as its longest tool name: picking another tool never moves the buttons, user 2026-11-06)
        font = tkfont.nametofont(look.TK)
        widest = max(font.measure(t) for t in self.picker.label.values())
        self.picker.main.configure(width=-(-widest // max(1, font.measure("0"))))
        opened = self.picker.open

        def open_list():  # (the list never takes the keyboard either: Domino keeps its keys)
            opened()
            if self.picker.popup is not None:
                dov.no_keyboard(self.picker.popup)
        self.picker.open = open_list
        self.picker.arrow.configure(command=open_list)
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=3, pady=2)
        ttk.Button(row, text=tr("app.undo"), takefocus=False,
                   command=lambda: app.undo_roll()).pack(side="left", padx=(2, 0))
        ttk.Button(row, text=tr("app.redo"), takefocus=False,
                   command=lambda: app.undo_roll(True)).pack(side="left", padx=(2, 0))
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=3, pady=2)
        for key, run in (("take", self.take), ("send", self.send_all), ("flip_sideways", lambda: self.flip(True)),
                         ("flip_upside", lambda: self.flip(False)), ("stir", lambda: self.open_tool("stir")),
                         ("sustain", lambda: self.open_tool("sustain"))):
            b = ttk.Button(row, text=tr("companion." + key), takefocus=False, command=run)
            b.pack(side="left", padx=(2, 0))
            Tooltip(b, tr("companion." + key + "_tip"))
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=3, pady=2)
        b = ttk.Button(row, text=tr("companion.back"), takefocus=False, command=self.stop)
        b.pack(side="left", padx=(2, 0))
        Tooltip(b, tr("companion.back_tip"))
        # messages on a line of their own under the buttons (user 2026-11-06: a message beside them moved them)
        self.notes = ttk.Frame(edge, padding=(4, 0, 4, 2))
        self.status = ttk.Label(self.notes, text="", foreground=look.WARN)
        self.said = ttk.Label(self.notes, text="", foreground=look.HINT)
        return bar

    def sync_notes(self):
        """The message line shows only while it has something to say."""
        on = bool(self.status.winfo_manager() or self.said.winfo_manager())
        if on != bool(self.notes.winfo_manager()):
            if on:
                self.notes.pack(fill="x")
            else:
                self.notes.pack_forget()

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
            try:  # (the folder's label on the bar too: a second start finds it, the main window is hidden; hunt 11-06)
                import __main__
                label = getattr(__main__, "label_window", None)
                if label:
                    label(self.bar)
            except Exception:
                pass
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
    def stop(self, now=False):
        """The bar's Spiderweb: going back = done with what's drawn here (hunt 2026-11-06, A): every shape waiting
        goes into Domino first and a paste going on finishes, then the main window comes back. now: at once (a
        send that can't go: the shapes become shapes of the project)."""
        if self.leaving and not now:
            return
        self.roll.cancel_draft()
        self.roll.end_typing()
        d = self.overlay.domino
        waiting = self.alive(self.marked()) if d is not None and dl.alive(d) else []
        if now or not (waiting or self.busy or self.ready):
            return self.finish_stop()
        self.leaving = True
        self.pending = []
        self.queue(waiting)
        self.say(tr("companion.leaving"))
        self.bar.after(30, self.leave_when_done)
        return None

    def leave_when_done(self, since=None):
        """Back once nothing goes into Domino any more (a failed send leaves its shapes as the project's)."""
        since = since or time.monotonic()
        if (self.busy or self.ready) and time.monotonic() - since < 60:
            self.bar.after(40, lambda: self.leave_when_done(since))
            return
        self.finish_stop()

    def finish_stop(self):
        app = self.app
        try:
            app.tool.trace_remove("write", self._trace)
        except tk.TclError:
            pass
        self.roll.cancel_draft()
        self.roll.end_typing()
        if self._said:
            self.bar.after_cancel(self._said)
        self.take_off(now=True)  # (in Domino already: never left behind as shapes of the project)
        for sh in app.shapes:  # (not sent: normal shapes of the project now)
            sh.pop(MARK, None)
        self.drop_own_steps()  # (Domino's own Ctrl+Z for what went into it)
        for w in self.adopted:  # (back to the main window they belong to)
            try:
                w.transient(app)
            except tk.TclError:
                pass
        for name in ("stir_window", "sustain_window"):  # (theirs belonged to the bar: closed, hunt 2026-11-06)
            w = getattr(app, name, None)
            if w:
                try:
                    w.close()
                except tk.TclError:
                    setattr(app, name, None)
        app.unbind_class(KEY_TAG, "<KeyPress>")
        app.roll = self.main_roll
        app.companion = None
        app.companion_tops = ()
        app.track_view()  # (the project's shapes shown again)
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
        app.schedule_autosave()  # (none while this was on: it would have kept Domino's PPQ as the project's)


def send_wheel(hwnd, delta, state, x, y):
    """The wheel turned over Domino's piano roll at screen x, y (Shift / Ctrl as held)."""
    if not system.WINDOWS:
        return
    import ctypes
    keys = (0x4 if state & 0x1 else 0) | (0x8 if state & 0x4 else 0)  # MK_SHIFT, MK_CONTROL
    w = ((delta & 0xFFFF) << 16) | keys
    lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x020A, ctypes.c_size_t(w), ctypes.c_ssize_t(lp))
