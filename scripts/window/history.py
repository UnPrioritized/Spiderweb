"""The History panel: every undo step by name, oldest at the top; click one to go back (or forward) to it. It sits
in the side panel between Project and Shapes; Undock puts it in a window of its own (Dock, or closing that window,
puts it back). The steps themselves are App.undo_stack / redo_stack: (shapes as JSON, name) pairs."""

import json
import tkinter as tk
from tkinter import ttk

START = "Start"  # the first row: the oldest state still kept
FUTURE = "#a0a0a0"  # steps undone (Ctrl+Y / clicking them brings them back; a new change drops them)
EDIT_NAMES = {"vel0": "Velocity", "vel1": "Velocity", "point": "Move a point", "smooth": "Straighten"}


def edit_name(key):
    """The History name of a typing / number box edit (App.begin_edit's key)."""
    kind = key[0] if isinstance(key, tuple) else key
    if kind == "field":
        return EDIT_NAMES.get(key[2], key[2])
    if kind == "tumour":
        return f"Tumour {key[2]}" if len(key) > 2 else "Tumours"
    return EDIT_NAMES.get(kind, "Change")


class HistoryPanel:
    """Mixed into App."""

    def _build_history(self, side):
        self.history_undocked = False
        self.history_pos = ""  # the undocked window's size and place ("WxH+x+y", remembered in the autosave)
        self.history_window = None
        self._history_sig = None
        box = self.history_box = ttk.LabelFrame(side, text="History", padding=6)
        box.pack(fill="x", pady=(8, 0))
        self.history_frame = self._history_list(box, 6)
        self.history_frame.pack(fill="x")

    def _history_list(self, parent, height):
        """The list and its Undock / Dock button, in parent."""
        frame = ttk.Frame(parent)
        row = ttk.Frame(frame)
        row.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Label(row, text="Click a step to go back to it.", foreground="#777").pack(side="left")
        ttk.Button(row, text="Dock" if parent is not self.history_box else "Undock",
                   command=self.toggle_history_dock).pack(side="right")
        lst = tk.Listbox(frame, height=height, activestyle="none", exportselection=False, font=("Segoe UI", 9))
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
        done = [name for _, name in self.undo_stack]
        undone = [name for _, name in reversed(self.redo_stack)]
        return [START] + done + undone, len(done)

    def sync_history(self, force=False):
        """Refresh the list if the steps changed (cheap to call often)."""
        if not hasattr(self, "history_list"):
            return
        sig = (len(self.undo_stack), len(self.redo_stack), id(self.undo_stack[-1]) if self.undo_stack else None,
               id(self.redo_stack[-1]) if self.redo_stack else None)
        if sig == self._history_sig and not force:
            return
        self._history_sig = sig
        names, now = self.history_rows()
        lst = self.history_list
        lst.delete(0, "end")
        for i, name in enumerate(names):
            lst.insert("end", f"{name}")
            if i > now:
                lst.itemconfig(i, foreground=FUTURE, selectforeground="#ffffff")
        lst.selection_clear(0, "end")
        lst.selection_set(now)
        lst.see(now)

    def history_clicked(self, lst):
        cur = lst.curselection()
        if cur:
            self.history_jump(cur[0])

    def history_jump(self, row):
        """Undo / redo until row is the current step."""
        self.roll.cancel_draft()
        while len(self.undo_stack) > row and self.undo_stack:
            self._restore(self.undo_stack, self.redo_stack)
        while len(self.undo_stack) < row and self.redo_stack:
            self._restore(self.redo_stack, self.undo_stack)
        self.sync_history(force=True)

    def drop_empty_step(self, before):
        """The last step changed nothing (e.g. a click on a shape without dragging it): it's taken out, so it
        doesn't show in the History (before: the shapes as JSON now, before the next change)."""
        if self.undo_stack and self.undo_stack[-1][0] == before and not self.redo_stack:
            self.undo_stack.pop()

    def settle_history(self):
        """After a mouse drag: a step that changed nothing goes."""
        if self.undo_stack and not self.redo_stack and not self.roll.drag:
            self.drop_empty_step(json.dumps(self.shapes))
            self.sync_history()

    # ------------------------------------------------------------ docked / undocked

    def toggle_history_dock(self):
        if self.history_undocked:
            self.dock_history()
        else:
            self.undock_history()
        self.schedule_autosave()

    def undock_history(self):
        self.history_undocked = True
        self.history_box.pack_forget()
        self.history_frame.destroy()
        win = self.history_window = tk.Toplevel(self)
        win.title("History")
        win.transient(self)
        win.minsize(int(200 * self.scale), int(160 * self.scale))
        win.geometry(self.history_pos or f"{int(260 * self.scale)}x{int(360 * self.scale)}")
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

