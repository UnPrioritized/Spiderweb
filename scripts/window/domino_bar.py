"""Draw on Domino: the floating bar (window/domino_companion.py's Companion; moved here as a mixin).
The bar's buttons, its message line, where it sits over Domino's grid and its grip drag."""

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from files import settings, system
from files.lang import tr
from window import domino_overlay as dov
from window import look
from window.tool_picker import ToolPicker
from window.widgets import Tooltip

BAR_GAP = 8  # px between the bar and the grid's top right corner at first


def bar_tools():
    from window.domino_companion import LEFT_OUT  # (this file is loaded by that one)
    from window.app import DRAW_TOOLS  # (it imports this file's caller)
    return [t for t in DRAW_TOOLS if t[0] not in LEFT_OUT]


class DominoBar:
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
        for key, run in (("take", self.take), ("send", self.send_all), ("bake", self.bake),
                         ("flip_sideways", lambda: self.flip(True)), ("flip_upside", lambda: self.flip(False)), ("stir", lambda: self.open_tool("stir")),
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
