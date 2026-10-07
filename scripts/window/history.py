"""The History panel: every undo step by name, oldest at the top; click one to go back (or forward) to it. The
toolbar's History box shows / hides it (off on a fresh start). It sits in the side panel between Project and Shapes;
Undock puts it in a window of its own (Dock, or closing that window, puts it back). The steps themselves are
App.undo_stack / redo_stack: (shapes as JSON, name, (selected shape numbers, main one, kept Select boxes)) steps."""

import json
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from window import look
from window.widgets import Tooltip, placed

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
    return EDIT_NAMES.get(kind, "Change")


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
        if not hasattr(self, "history_list") or not self.show_history.get() or self._history_jumping:
            return
        sig = (len(self.undo_stack), len(self.redo_stack), id(self.undo_stack[-1]) if self.undo_stack else None,
               id(self.redo_stack[-1]) if self.redo_stack else None)
        if sig == self._history_sig and not force:
            return
        self._history_sig = sig
        names, now = self.history_rows()
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
        self._history_jumping = True  # (no list refresh for every step on the way: each would scroll it)
        try:
            while len(self.undo_stack) > row and self.undo_stack:
                self._restore(self.undo_stack, self.redo_stack)
            while len(self.undo_stack) < row and self.redo_stack:
                self._restore(self.redo_stack, self.undo_stack)
        finally:
            self._history_jumping = False
        self.sync_history(force=True)

    def drop_empty_step(self, before):
        """The last step changed nothing (e.g. a click on a shape without dragging it): it's taken out, so it
        doesn't show in the History (before: the shapes as JSON now, before the next change)."""
        if self.undo_stack and self.undo_stack[-1][0] == before and not self.redo_stack:
            kept = getattr(self, "_redo_kept", None)
            if kept and kept[1] == len(self.undo_stack):
                self.redo_stack[:] = kept[0]  # (the steps undone before it are back)
            self.undo_stack.pop()
            self._redo_kept = None

    def settle_history(self):
        """After a mouse drag: a step that changed nothing goes."""
        if self.undo_stack and not self.redo_stack and not self.roll.drag:
            self.drop_empty_step(json.dumps(self.shapes))
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

