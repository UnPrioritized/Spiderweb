"""Undo / redo (Ctrl+Z / Ctrl+Y, push_undo, _restore) and the History panel: every undo step by name, oldest at the top; click one to go back (or forward) to it. The
toolbar's History box shows / hides it (off on a fresh start). It sits in the side panel between Project and Shapes;
Undock puts it in a window of its own (Dock, or closing that window, puts it back). The steps themselves are
App.undo_stack / redo_stack: (shapes as undo_state, name, (selected shape numbers, main one, kept Select boxes))
steps."""

import json
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.engine import BIG_MARK, BIG_TEXT, with_big
from notes.merge import merge_parts
from notes.tempo import clean_tempo
from window import look
from window.widgets import ListTip, Tooltip, fit_screen, placed

CH10_OFF = "ch10_off"  # (an undo step's mark: made by unticking "Use channel 10", App.on_use_ch10)
BOXES = "boxes"  # (an undo step's mark (BOXES, BPM, project file): before it, App.new_project)


def boxes_mark(step):
    """An undo step's (BOXES, BPM, project file) mark, or None."""
    last = step[2][-1] if step[2] else None
    return last if type(last) is tuple and last[:1] == (BOXES,) else None


def _big_texts(sh):
    """The places a big text (pasted / image notes, many MB) sits in a shape: (dict, key) pairs. In the shape
    itself or in a merge's parts (its recipe, or the shape its notes Split back into), merges of merges too."""
    got = [(sh, k) for k, v in sh.items() if isinstance(v, str) and len(v) > BIG_TEXT]
    for p in merge_parts(sh):
        got += _big_texts(p)
    return got


def undo_state(shapes):
    """The shapes as an undo step keeps them: their JSON, or with big texts (JSON, the texts) where the JSON has
    a mark in each one's place. The texts are the shapes' own strings, shared, never copied (a 4 M-note paste is
    60 MB: every step copying it ran out of memory). Equal states = equal shapes."""
    places = [p for sh in shapes for p in _big_texts(sh)]
    if not places:
        return json.dumps(shapes)
    texts = tuple(d[k] for d, k in places)
    for i, (d, k) in enumerate(places):
        d[k] = BIG_MARK + str(i)
    try:
        return json.dumps(shapes), texts
    finally:
        for (d, k), text in zip(places, texts):
            d[k] = text


def as_state(state, shapes):
    """A state given as plain JSON (json.dumps of the shapes, made by a tool window) -> undo_state's, its big
    texts swapped for the same ones in shapes (shared, not kept twice)."""
    if not isinstance(state, str) or len(state) <= BIG_TEXT:
        return state
    got = undo_state(json.loads(state))
    if isinstance(got, str):
        return got
    same = {d[k]: d[k] for sh in shapes for d, k in _big_texts(sh)}
    return got[0], tuple(same.get(t, t) for t in got[1])


def load_state(state):
    """An undo state -> the shapes (big texts put back as the very strings kept)."""
    if isinstance(state, str):
        return json.loads(state)
    return [with_big(sh, state[1]) for sh in json.loads(state[0])]

START = tr("history.start")  # the first row: the oldest state still kept
FUTURE = look.FUTURE  # steps undone (Ctrl+Y / clicking them brings them back; a new change drops them)
EDIT_NAMES = {"vel0": tr("history.velocity"), "vel1": tr("history.velocity"), "point": tr("history.move_a_point"),
              "smooth": tr("history.straighten"), "pattern": tr("history.pattern"),
              "shape": tr("history.shape"), "polygon": tr("history.polygon")}


def edit_name(key):
    """The History name of a typing / number box edit (App.begin_edit's key)."""
    kind = key[0] if isinstance(key, tuple) else key
    if kind == "field":
        return EDIT_NAMES.get(key[2], key[2])
    if kind == "tumour":
        return tr("history.tumour", key=key[2]) if len(key) > 2 else tr("history.tumours")
    return EDIT_NAMES.get(kind, tr("app.change"))


class HistoryPanel:
    """Mixed into App."""

    def _build_history(self, side):
        self.history_undocked = False
        self.history_pos = ""  # the undocked window's size and place ("WxH+x+y", remembered in the autosave)
        self.history_window = None
        self._history_sig = None
        self._history_jumping = False  # (history_jump)
        box = self.history_box = ttk.LabelFrame(side, text=tr("history.history"), padding=6)  # (packed when shown)
        self.history_frame = self._history_list(box, 6)
        self.history_frame.pack(fill="x")

    def _history_list(self, parent, height):
        """The list and its Undock / Dock button, in parent."""
        frame = ttk.Frame(parent)
        row = ttk.Frame(frame)
        row.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Label(row, text=tr("history.click_a_step_to_go_back"), foreground=look.HINT).pack(side="left")
        b = ttk.Button(row, text=tr("history.dock") if parent is not self.history_box else tr("history.undock"),
                       command=self.toggle_history_dock)
        b.pack(side="right")
        Tooltip(b, tr("history.dock_tip"))
        lst = tk.Listbox(frame, height=height, activestyle="none", exportselection=False, font=look.font(9))
        sb = ttk.Scrollbar(frame, orient="vertical", command=lst.yview)
        lst.config(yscrollcommand=sb.set)
        lst.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        lst.bind("<<ListboxSelect>>", lambda e: self.history_clicked(lst))
        ListTip(lst)
        lst.bind("<ButtonRelease-1>", lambda e: self.roll.focus_set(), add="+")  # (keys back to the piano roll)
        self.history_list = lst
        return frame

    def history_rows(self):
        """(names, current row): Start, the steps done, then the ones undone (still there for redo)."""
        done = [step[1] for step in self.undo_stack]
        undone = [step[1] for step in reversed(self.redo_stack)]
        return [START] + done + undone, len(done)

    def sync_history(self, force=False):
        """Refresh the list if the steps changed (cheap to call often)."""
        self.sync_window_title()  # (the * for unsaved changes follows the steps)
        if not hasattr(self, "history_list") or not self.show_history.get() or self._history_jumping:
            return
        names, now = self.history_rows()
        sig = (names, now)  # (the names themselves: a step replaced by another can sit at the same spot in memory)
        if sig == self._history_sig and not force:
            return
        self._history_sig = sig
        lst = self.history_list
        top = lst.yview()[0]  # (refilling it scrolls to the top: put the scroll back, see() below only moves it if needed)
        lst.delete(0, "end")
        for i, name in enumerate(names):
            lst.insert("end", f"{name}")
            if i > now:
                lst.itemconfig(i, foreground=FUTURE, selectforeground=look.FUTURE_PICKED)
        lst.selection_clear(0, "end")
        lst.selection_set(now)
        lst.yview_moveto(top)
        lst.see(now)

    def history_clicked(self, lst):
        cur = lst.curselection()
        if cur:
            self.history_jump(cur[0])

    def history_jump(self, row):
        """Undo / redo until row is the current step."""
        self.roll.cancel_draft()
        self.settle_windows()  # (a window's try kept as its own step FIRST, then counted: hunt, it landed short)
        self._history_jumping = True  # (no list refresh on the way)
        try:
            n = len(self.undo_stack) - row
            if n > 0:
                self._restore(self.undo_stack, self.redo_stack, n)
            elif n < 0 and self.redo_stack:  # (a step kept just now drops the redo steps: nothing to go forward to)
                self._restore(self.redo_stack, self.undo_stack, min(-n, len(self.redo_stack)))
        finally:
            self._history_jumping = False
        self.sync_history(force=True)

    def drop_empty_step(self, before):
        """The last step changed nothing (e.g. a click on a shape without dragging it): it's taken out, so it
        doesn't show in the History (before: the shapes as JSON / undo_state now, before the next change)."""
        before = as_state(before, self.shapes)
        if (self.undo_stack and self.undo_stack[-1][0] == before and not self.redo_stack
                and self.step_tempo(self.undo_stack[-1]) == json.dumps(self.tempo)
                and not boxes_mark(self.undo_stack[-1])):  # (New with nothing to clear still set the boxes)
            kept = getattr(self, "_redo_kept", None)
            if kept and kept[1] == len(self.undo_stack):
                self.redo_stack[:] = kept[0]  # (the steps undone before it are back)
            self.undo_stack.pop()
            self.undo_stack[:0] = getattr(self, "_trimmed", None) or []  # (the oldest steps the 300 limit dropped)
            self._redo_kept = self._trimmed = None

    def settle_history(self):
        """After a mouse drag: a step that changed nothing goes."""
        if self.undo_stack and not self.redo_stack and not self.roll.drag:
            self.drop_empty_step(undo_state(self.shapes))
            self.sync_history()

    # ------------------------------------------------------------ docked / undocked

    def toggle_history(self, tip=True):
        """The toolbar's History box: show the list (docked or in its window, whichever it was) or hide it."""
        if self.show_history.get():
            if self.history_undocked:
                self._history_window()
            else:
                self.history_box.pack(fill="x", pady=(8, 0), before=self.shapes_box)
            self.sync_history(force=True)
            if tip:
                self.tips.show("history")
        else:
            if self.history_window:
                self.remember_history()
                self.history_window.destroy()
                self.history_window = None
            self.history_box.pack_forget()
        self.schedule_autosave()

    def toggle_history_dock(self):
        if self.history_undocked:
            self.dock_history()
        else:
            self.undock_history()
        self.schedule_autosave()

    def undock_history(self):
        self.history_undocked = True
        self.history_box.pack_forget()
        self._history_window()

    def _history_window(self):
        self.history_frame.destroy()
        win = self.history_window = tk.Toplevel(self)
        win.title(tr("history.history"))
        win.transient(self)
        win.minsize(int(200 * self.scale), int(160 * self.scale))
        win.geometry(placed(win, self.history_pos) or f"{int(260 * self.scale)}x{int(360 * self.scale)}")
        fit_screen(win)
        self.history_frame = self._history_list(win, 16)
        self.history_frame.pack(fill="both", expand=True, padx=6, pady=6)
        win.protocol("WM_DELETE_WINDOW", self.toggle_history_dock)
        win.bind("<Configure>", lambda e: e.widget is win and self.remember_history(), add="+")
        win.bind("<Escape>", lambda e: self.roll.focus_set())
        self.sync_history(force=True)

    def remember_history(self):
        win = self.history_window
        if win and win.winfo_exists():
            self.history_pos = win.wm_geometry()

    def dock_history(self):
        self.history_undocked = False
        if self.history_window:
            self.remember_history()
            self.history_window.destroy()
            self.history_window = None
        self.history_frame.destroy()
        self.history_frame = self._history_list(self.history_box, 6)
        self.history_frame.pack(fill="x")
        self.history_box.pack(fill="x", pady=(8, 0), before=self.shapes_box)
        self.sync_history(force=True)
        self.roll.focus_set()


    # ------------------------------------------------------------ undo / redo

    def settle_windows(self):
        """The windows trying changes out (note tools, tumours, sustain) keep them as their own steps. Several at
        once: the first step takes back what ALL of them tried, the next all but the first's... (each step was
        made from the shapes as they are, which hold every window's tries: undoing them all came back to one)."""
        ws = [w for w in (self.claw_window, self.strum_window, self.chop_window, self.path_window,
                          self.tumour_window, self.sustain_window, self.subtract_window) if w]
        waiting = [w for w in ws if w.pending()]
        for w in ws:
            w.settle(also=waiting[waiting.index(w) + 1:] if w in waiting else ())

    def push_undo(self, state=None, name=None):
        """Remember the shapes (or `state`, shapes saved earlier as JSON) for Ctrl+Z; name = what the step does
        (the History panel)."""
        self.settle_windows()  # (the claw / strum / tumours / sustain being tried out are kept first, as own steps)
        sc = self._scrub
        if sc and sc["active"]:  # stepping a number box: only its first step takes an undo step
            if sc["pushed"]:
                return
            sc["pushed"] = True
        else:
            self._scrub = None
        own = bool(self.hz_window and self.hz_window.own_step)  # (made there, whatever has the keyboard now)
        self.add_undo_step(state or undo_state(self.shapes), name, hz=own or self.in_hz())
        if sc and sc["active"]:
            sc["at"] = len(self.undo_stack)  # (take_back_scrub: still the last step?)

    def take_back_scrub(self, gesture):
        """Ctrl+Z while a number box's label is held (Scrub.cancel): its step goes, not even kept for redo; the
        steps undone before it and the oldest ones the 300 limit dropped come back."""
        sc = self._scrub
        if sc and sc["gesture"] is gesture and sc["pushed"] and sc.get("at") == len(self.undo_stack):
            kept, trimmed = self._redo_kept, self._trimmed
            self._restore(self.undo_stack, self.redo_stack)
            self.redo_stack.pop()
            if kept:
                self.redo_stack[:] = kept[0]
            if trimmed:
                self.undo_stack[:0] = trimmed
            self.sync_history()
        self._scrub = None

    def add_undo_step(self, before, name=None, sel=None, hz=False):
        """push_undo without its checks (before: the shapes as JSON or history.undo_state). The selection is kept with
        it (sel: the one from when `before` was saved, sel_state(); default: now): undo puts it back with the shapes
        (shapes are picked by number, which can point at another shape after undo). hz: made in the Hz bass window
        (key_undo)."""
        before = as_state(before, self.shapes)
        now = self.sel_state()
        if sel and len(sel) > 8:  # (a step saved late, e.g. a tool window's try: the tempo line as it is now, its
            # own edits meanwhile were steps of their own; undoing this one must not take them back)
            sel = tuple(sel[:8]) + tuple(now[8:])
        self.drop_empty_step(before)
        self.undo_stack.append((before, name or tr("app.change"), sel or now, hz))
        self._trimmed = self.undo_stack[:-300]  # (put back by drop_empty_step if this step changes nothing)
        del self.undo_stack[:-300]
        # the undone steps are kept aside until this step turns out to change something (a click on a shape that
        # doesn't drag it mustn't throw them away: drop_empty_step brings them back)
        self._redo_kept = (self.redo_stack[:], len(self.undo_stack)) if self.redo_stack else None
        self.redo_stack.clear()
        self._edit_key = None
        self.sync_history()

    def last_step(self):
        """The newest undo step (None: none), for LocalUndo(main=...)."""
        return self.undo_stack[-1] if self.undo_stack else None

    def sel_state(self):
        """The selection as an undo step keeps it: (selected shape numbers, the main one, the kept Select boxes or
        None, the Hz bass window's HzWindow.sel_state() or None, the picked stroke, the funnel's highlighted parts,
        the one of them clicked, the picked Ripples source, the Tempo pane's line as JSON, what the Tempo pane picked
        as JSON). The tempo line rides here so every step carries it (it isn't a shape): undo / redo put it back with
        the shapes."""
        boxes = self.roll.kept_box()
        pane = getattr(self, "tempo_pane", None)
        return (sorted(self.sels), self.sel, boxes and list(boxes), self.hz_window and self.hz_window.sel_state(),
                self.stroke, list(self.parts), self.part_main, self.ripple_pick, json.dumps(self.tempo),
                pane.pick_state() if pane else "{}")

    def step_tempo(self, step):
        """The tempo line (JSON) an undo step kept; the one now for a step made without it."""
        sel = step[2]
        return sel[8] if len(sel) > 8 and isinstance(sel[8], str) else json.dumps(self.tempo)

    def scrub_step(self, gesture, run):
        """run() steps a number box (widgets.Scrub). The steps of one gesture (a label drag, or arrows / wheel on the
        same box with nothing else in between) share one undo step."""
        if not self._scrub or self._scrub["gesture"] is not gesture:
            self._scrub = {"gesture": gesture, "pushed": False, "active": False}
        sc = self._scrub
        sc["active"] = True
        try:
            run()
        finally:
            sc["active"] = False

    def begin_edit(self, key):
        """Typing in one box counts as a single undo step until you move to another box."""
        if key != self._edit_key:
            self.push_undo(name=edit_name(key))
            self._edit_key = key

    def undo(self):
        if self.roll.drop_hz_keys():  # (a new Hz bass's keys being dragged: just called off)
            return
        if self.roll.draft or self.roll.typing_empty():  # something half drawn (a funnel waiting for its wall, a
            return self.roll.cancel_draft()                # polyline, a text caret with nothing typed): just drop it
        if self.hz_window and self.hz_window.pending:  # a slide started in the Hz bass window: just drop its mark
            self.hz_window.pending = None
            return self.hz_window.redraw()
        self.drop_empty_step(undo_state(self.shapes))  # (a click that changed nothing isn't a step)
        self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        if self.roll.hz_keys_held():  # (a new Hz bass's keys being dragged: nothing)
            return
        self._restore(self.redo_stack, self.undo_stack)

    def undo_roll(self, redo=False):
        """Ctrl+Z / Ctrl+Y on the piano roll and the toolbar's Undo / Redo: the Path / Sustain window's own steps
        first (their points / stops are dragged on the piano roll)."""
        if self.path_window and self.path_window.local_step(redo):
            return "break"
        if self.sustain_window and self.sustain_window.local_step(redo):
            return "break"
        return self.redo() if redo else self.undo()

    def key_undo(self, e, redo=False):
        """Ctrl+Z / Ctrl+Y. In the Hz bass window only its own changes (user: the piano roll behind stays as it
        is): a step made elsewhere is next = a ding and a word in its status line."""
        hz = self.hz_window
        if self.grabbed_elsewhere():
            return None  # (a pop-up without its own undo: the shapes it works on stay as they are)
        if self.vel.edit:  # a velocity line held: Ctrl+Z throws it away (no step), Ctrl+Y does nothing
            return "break" if redo else (self.vel.drop(), "break")[1]
        if self.tempo_pane.end_drag():  # (a tempo point held: let go, the step made at its first move takes it
            return "break"  # back; only a catch-up being added / a press that made no step called off: that's all)
        if self.held_scrub:  # a number box's label held: Ctrl+Z = back to the press (no step), Ctrl+Y nothing
            return "break" if redo else (self.held_scrub.cancel(), "break")[1]
        if self.app_only(e.widget):
            return "break"  # (Settings / Help / the update popup have no undo; the shapes behind stay as they are)
        if self.image_window and str(e.widget).startswith(str(self.image_window)):
            return "break"  # (the image window and its pop-ups: their text boxes only, the piano roll stays, hunt)
        if not self.in_hz(e.widget):
            if self.box_drawn():
                return "break"
            return self.undo_roll(redo)
        if hz.drag:  # the mouse held there: Ctrl+Z only puts back what's being dragged (no step), Ctrl+Y nothing
            return None if redo else hz.cancel_drag()
        held = hz.held_fx()  # (the same for an effect's points and the synth window's knobs)
        if held:
            return None if redo else held()
        if hz.draft is not None and (hz.draft.get("held") is not None or not redo):  # a path being drawn: it
            return None if redo else (hz.drop_draft(), "break")[1]  # goes, nothing made (Ctrl+Y: nothing)
        tw = hz.trace_win  # (the Wave shape window's changes no Hz bass held yet: its own steps)
        if tw and tw.winfo_exists() and str(e.widget).startswith(str(tw)) and tw.local_step(redo):
            return "break"
        if not redo and hz.pending:  # a slide started: just drop its mark
            hz.pending = None
            return hz.redraw()
        if not redo:
            self.drop_empty_step(undo_state(self.shapes))
        src = self.redo_stack if redo else self.undo_stack
        if src and not src[-1][3]:
            hz.bell()
            return hz.status.config(text=tr("hz.redo_elsewhere" if redo else "hz.undo_elsewhere"))
        self._restore(src, self.undo_stack if redo else self.redo_stack)

    def _restore(self, src, dst, count=1):
        """count steps from src to dst (a History jump: the steps on the way only move, the shapes are made once)."""
        self.settle_windows()  # (so Ctrl+Z here takes back the claw / strum / tumours / sustain being tried out)
        if not src:
            if self.roll.holding():  # (nothing to take back: still lets go, or the rest of the drag had no step)
                self.roll.cancel_draft()
            return
        hz_was = self.hz_window and self.hz_window.before_restore()
        self.roll.cancel_draft()
        now, sel_now = undo_state(self.shapes), self.sel_state()
        ch10 = None
        boxes = was_boxes = (self.pvar["bpm"].get(), self.project_file)
        for _ in range(min(count, len(src))):
            state, name, picked, hz = src.pop()
            got = boxes_mark((state, name, picked))
            if got:  # (the boxes swap: this side gets the ones now, the boxes get the step's)
                picked = tuple(picked[:-1])
                dst.append((now, name, tuple(sel_now) + ((BOXES,) + boxes,), hz))
                boxes = got[1:]
                now, sel_now = state, picked
                continue
            # (CH10_OFF last: unticking "Use channel 10" made it: undo ticks it; on the redo side (CH10_OFF, the tick
            # when it was undone): redo puts that back (ticked again by hand after the step = stays ticked))
            last = picked[-1]
            mark = ()
            if last == CH10_OFF or type(last) is tuple and last[:1] == (CH10_OFF,):
                if src is self.undo_stack:
                    ch10, mark = True, ((CH10_OFF, bool(self.use_ch10.get()) if ch10 is None else ch10),)
                else:
                    ch10, mark = (last[1] if type(last) is tuple else False), (CH10_OFF,)
                picked = tuple(picked[:-1])
            dst.append((now, name, tuple(sel_now) + mark, hz))
            now, sel_now = state, picked
        if ch10 is not None:
            self.use_ch10.set(ch10)
        if boxes != was_boxes:
            self.project_file = boxes[1] if len(boxes) > 1 else self.project_file
            self.pvar["bpm"].set(boxes[0])
        state, picked = now, sel_now
        self._redo_kept = self._trimmed = None
        self.shapes = load_state(state)
        was = set(self.sels)
        self.sels, self.sel = set(picked[0]), picked[1]
        self.sels = {i for i in self.sels if i < len(self.shapes)}
        self.parts = set()
        self.stroke = None
        if self.sels != was:  # (the picked Ripples source stays while the same shape is selected)
            self.ripple_pick = None
        if self.sel not in self.sels:
            self.sel = max(self.sels, default=None)
        # the Select boxes, the picked stroke and the funnel's highlighted parts come back as they were (only while
        # they still go with the same selection)
        same = self.sels == set(picked[0])
        self.roll.box_kept = (picked[2], set(self.sels)) if picked[2] and same else None
        if same:
            self.stroke, self.parts, self.part_main = picked[4], set(picked[5]), picked[6]
            if len(picked) > 7:  # (the Ripples source the undone change was made on, hunt)
                self.ripple_pick = picked[7]
        if len(picked) > 8 and isinstance(picked[8], str):
            self.tempo = clean_tempo(json.loads(picked[8]))
            if len(picked) > 9 and isinstance(picked[9], str) and picked[9].startswith("{"):
                self.tempo_pane.restore_pick(picked[9])  # (its Select box and what was picked, user)
            self.tempo_changed(restoring=True)  # (Hz bass warnings: sync_panel / the Hz window's after_restore below)
        self._edit_key = self._scrub = None
        self.sync_panel()
        self.shapes_changed()
        if ch10 is not None:
            self.sync_colours()
            self.show_colour_limit()
        if self.hz_window:
            self.hz_window.after_restore(hz_was)
            if self.sels == set(picked[0]):
                self.hz_window.restore_sel(picked[3])
