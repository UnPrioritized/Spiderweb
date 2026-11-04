"""Draw on Domino (Domino companion mode, Windows only): Spiderweb's drawing tools right on Domino's piano roll.

Spiderweb's main window hides; a see-through piano roll of ours (roll/overlay_roll.py) lies exactly over Domino's
note grid (window/domino_overlay.py follows Domino's window, scroll and zoom) and is app.roll while this is on, so
every tool, undo step and shape works as in the main window. A small floating bar over Domino holds the tools:
"Domino" = Domino's own mouse (clicks go through to Domino; Domino's select box is the only selection, user
2026-11-05), a drawing tool = drawing on the overlay, "Spiderweb" = back to the main window.
The wheel always goes to Domino (its scroll and zoom are the view). Reading Domino: files/domino_view.py.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from files import domino_view as dv
from files import settings, system
from files.lang import tr
from roll.overlay_roll import OverlayRoll
from window import domino_overlay as dov
from window import look
from window.tool_picker import ToolPicker
from window.widgets import Tooltip

NOT_DRAWING = ("select", "slice", "lasso")  # (tools that pick Spiderweb's shapes: Domino's own mouse instead)
LEFT_OUT = ("hz", "picture")  # (they open windows of their own: from the main window)
BAR_GAP = 8  # px between the bar and the grid's top right corner at first
DOMINO_KEYS = 128


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
        b = ttk.Button(row, text=tr("companion.back"), takefocus=False, command=self.stop)
        b.pack(side="left", padx=(2, 0))
        Tooltip(b, tr("companion.back_tip"))
        self.status = ttk.Label(row, text="", foreground=look.WARN)
        return bar

    def show_bar(self):
        if self.bar.winfo_exists() and not self.bar.winfo_viewable():
            self.bar.deiconify()
            self._placed = None
            self.place_bar()

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
        app.roll = self.main_roll
        app.companion = None
        app.companion_tops = ()
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
