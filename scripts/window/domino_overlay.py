"""Domino companion: a see-through window lying exactly over Domino's note grid (Windows only).

It follows Domino: window moves and sizes, scrolling, both zooms; hides when Domino is minimised, closed or its
piano roll isn't shown, and sits right above Domino in the window stack (so Domino's own dialogs and any window
in front of Domino stay in front of it). Clicks go through to Domino unless drawing is on (set_drawing).
Whatever draws on it gets on_view(view) with files.domino_view.ViewState each time the view changes; .canvas
covers the grid only, so (0, 0) = the grid's top-left corner (to_canvas / from the view's numbers). The mouse
while drawing arrives on .mouse (same size and place; see set_drawing).
"""

import os
import subprocess
import tkinter as tk

from files import domino_view as dv
from files import system

SEE_THROUGH = "#ff00fe"  # the colour Windows leaves see-through (never used for drawing)
TICK_MS = 16
FIND_MS = 500  # how often the front-most Domino is looked for again


def available():
    return system.WINDOWS


def start_domino(path, *files):
    """Start Domino (a Domino.exe chosen by the user); returns the process or None."""
    if not available() or not os.path.isfile(path):
        return None
    try:
        return subprocess.Popen([path, *files], cwd=os.path.dirname(path))
    except OSError:
        return None


def front_domino(pid=None):
    """The front-most Domino we can read (or the one with that process id), else (None, reason)."""
    ds = dv.find_all()
    if pid is not None:
        ds = [d for d in ds if d.pid == pid]
    if not ds:
        return None, "not running"
    known = [d for d in ds if d.build is not None and not d.odd]  # (odd: its numbers didn't add up = not known)
    if not known:
        return None, "unknown build"
    return known[0], None


def double_click():
    """(ms, (x px, y px)): how quick and how close a second click must be to make a double click (Windows')."""
    if not system.WINDOWS:
        return 500, (4, 4)
    import ctypes
    u = ctypes.windll.user32
    return u.GetDoubleClickTime(), (max(1, u.GetSystemMetrics(36) // 2), max(1, u.GetSystemMetrics(37) // 2))


def no_keyboard(win):
    """win (a Toplevel of ours) never takes the keyboard when clicked: the window that has it keeps it (the
    floating bar: Domino's own keys go on working, user 2026-11-06). Its buttons still work."""
    if not system.WINDOWS:
        return
    import ctypes
    try:
        win.update_idletasks()
        hwnd = DominoOverlay._own_hwnd(win)
    except tk.TclError:
        return
    u = ctypes.windll.user32
    u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x80 | 0x8000000)  # tool window, never activated


class DominoOverlay:
    """overlay = DominoOverlay(root, on_view=..., on_state=...); overlay.stop() ends it."""

    def __init__(self, root, on_view=None, on_state=None, pid=None, make_canvas=None, on_tick=None):
        self.root = root
        self.on_view = on_view      # (ViewState) when what Domino shows changed
        self.on_state = on_state    # (reason or None) when following starts / stops working
        self.on_tick = on_tick      # () each tick while following (after the windows were placed)
        self.pid = pid              # follow only this Domino (None = the front-most one)
        self.may_switch = None      # (old, new) -> False = keep following old for now (what was drawn on it goes in)
        self.above = []             # more windows of ours kept above Domino (the floating bar), top-most first
        self.domino = None
        self.view = None
        self.state = "starting"
        self.drawing = False
        self.keys_px = 0            # how far both windows reach left over Domino's keyboard (set_keys)
        self._last = None
        self._job = None
        self._found_at = -FIND_MS
        self._clock = 0
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=SEE_THROUGH)
        self.win.attributes("-transparentcolor", SEE_THROUGH)
        # (make_canvas(parent): what draws on it, e.g. roll.overlay_roll; a plain canvas otherwise)
        self.canvas = (make_canvas(self.win) if make_canvas else
                       tk.Canvas(self.win, bg=SEE_THROUGH, highlightthickness=0, bd=0))
        self.canvas.pack(fill="both", expand=True)
        # Windows lets clicks on see-through pixels go through whatever the style says, so while drawing a
        # nearly invisible window (1/255) lies over the overlay and takes the mouse: bind drawing on .mouse
        self.catch = tk.Toplevel(root)
        self.catch.withdraw()
        self.catch.overrideredirect(True)
        self.catch.attributes("-alpha", 1 / 255)
        self.mouse = tk.Canvas(self.catch, bg="#000000", highlightthickness=0, bd=0, cursor="crosshair")
        self.mouse.pack(fill="both", expand=True)
        self.win.update_idletasks()
        self.catch.update_idletasks()
        self.hwnd = self._own_hwnd(self.win)
        self.catch_hwnd = self._own_hwnd(self.catch)
        self._style(self.hwnd, click_through=True)
        self._style(self.catch_hwnd, click_through=False)
        self.forwarded = None
        self._job = root.after(1, self._tick)  # (not at once: the caller gets the overlay first)

    # --- the mouse while drawing: from the catcher to what draws ---
    FORWARD = ("ButtonPress-1", "ButtonPress-2", "ButtonPress-3", "ButtonRelease-1", "ButtonRelease-2",
               "ButtonRelease-3", "Motion")

    def forward_to(self, widget, wheel=None, pressed=None):
        """The catcher's mouse goes on to widget as if it happened there (drags as usual); its pointer copies
        widget's. wheel(e): the wheel; pressed(): before each press. DOUBLE CLICKS made here by hand: Tk never
        sees one in passed-on presses (the catcher's own events sit between them), so a quick second left / right
        press goes to widget.forwarded_double(e) instead (user 2026-11-06: double right-click did nothing)."""
        self.forwarded = widget
        self._click = None
        m = self.mouse

        def send(e, seq):
            if pressed and seq.startswith("ButtonPress"):
                pressed()
            try:
                if seq in ("ButtonPress-1", "ButtonPress-3") and self._doubled(e) and \
                        hasattr(widget, "forwarded_double"):
                    widget.forwarded_double(e)
                    return "break"
                widget.event_generate(f"<{seq}>", x=e.x, y=e.y, state=e.state, when="now")
                cur = widget.cget("cursor")
                if m.cget("cursor") != (cur or "crosshair"):
                    m.configure(cursor=cur or "crosshair")
            except tk.TclError:
                pass
            return "break"

        for seq in self.FORWARD:
            m.bind(f"<{seq}>", lambda e, seq=seq: send(e, seq))
        if wheel:
            m.bind("<MouseWheel>", wheel)

    def _doubled(self, e):
        """This press and the one before: the same button, quick and close enough for Windows' double click (a 3rd
        quick one counts again, like Tk: the piano roll pairs right clicks itself)."""
        ms, near = double_click()
        last, self._click = self._click, (e.num, e.time, e.x_root, e.y_root)
        return bool(last) and last[0] == e.num and 0 <= e.time - last[1] <= ms and \
            abs(e.x_root - last[2]) <= near[0] and abs(e.y_root - last[3]) <= near[1]

    # --- Windows bits ---
    @staticmethod
    def _own_hwnd(win):
        import ctypes
        u = ctypes.windll.user32
        u.GetParent.restype = ctypes.c_void_p
        return u.GetParent(win.winfo_id()) or win.winfo_id()

    @staticmethod
    def _style(hwnd, click_through):
        import ctypes
        u = ctypes.windll.user32
        ex = u.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        ex |= 0x80000 | 0x80 | 0x8000000  # layered, tool window (no taskbar button), never activated
        ex = ex | 0x20 if click_through else ex & ~0x20  # WS_EX_TRANSPARENT = clicks go through
        u.SetWindowLongW(hwnd, -20, ex)

    def _keep_above(self):
        """Right above Domino in the stack: in front of it, behind whatever is in front of it; the mouse
        catcher (while drawing) right above the overlay."""
        import ctypes
        from files.system import wintypes as W
        u = ctypes.windll.user32
        u.GetWindow.restype = W.HWND
        u.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
        extra = [h for h in (self._own_hwnd(w) for w in self.above if w.winfo_exists() and w.winfo_viewable())]
        mine = (self.hwnd, self.catch_hwnd, *extra)
        above = u.GetWindow(self.domino.top, 3)  # GW_HWNDPREV
        while above in mine:
            above = u.GetWindow(above, 3)
        want = [self.hwnd] + ([self.catch_hwnd] if self.drawing else []) + extra[::-1]
        now, h = [], u.GetWindow(self.domino.top, 3)
        while h in mine:
            now.append(h)
            h = u.GetWindow(h, 3)
        if now != want:
            # (GWL_HWNDPARENT owning put it BEHIND Domino in the 11-04 test, so the stack is kept by hand)
            for w in reversed(want):  # top-most first: each goes under the window above Domino
                u.SetWindowPos(w, above or 0, 0, 0, 0, 0, 0x13)  # no move / size / activate
                above = w

    # --- following ---
    def set_drawing(self, on):
        """On = the mouse goes to .mouse (drawing); off = clicks go through to Domino."""
        self.drawing = bool(on)
        if self.drawing and self._last is not None:
            self.catch.deiconify()
            self._style(self.catch_hwnd, click_through=False)
        else:
            self.catch.withdraw()
        self._place()

    def keyboard_px(self):
        """Domino's keyboard width in real pixels (left of the grid), or 0 when not known."""
        d, v = self.domino, self.view
        if d is None or v is None or d.build is None:
            return 0
        return round(d.build.keys_w * v.scale)

    def set_keys(self, on):
        """On = both windows reach left over Domino's keyboard too (picking Glissando keys there, user 2026-11-06);
        the canvas then starts at the keyboard's left edge. Returns the pixels reached over."""
        px = self.keyboard_px() if on else 0
        if px != self.keys_px:
            self.keys_px = px
            self._place()
        return px

    def to_canvas(self, x, y):
        """Screen pixels -> canvas pixels."""
        g = self.view.grid
        return x - g[0] + self.keys_px, y - g[1]

    def _set_state(self, reason):
        if reason != self.state:
            self.state = reason
            if self.on_state:
                self.on_state(reason)

    def _hide(self, reason):
        if self._last is not None:
            self.win.withdraw()
            self.catch.withdraw()
            self._last = None
        self._set_state(reason)

    def _place(self):
        """Both windows exactly over the grid (the catcher only while drawing); + the keyboard while keys_px."""
        if self.view is None or self._last is None:
            return
        g = self.view.grid
        x0 = g[0] - self.keys_px
        spot = f"{max(1, g[2] - x0)}x{max(1, g[3] - g[1])}+{x0}+{g[1]}"
        # (update_idletasks at once: else the z-order SetWindowPos made Tk forget a move, the catcher stayed over
        # Domino's keyboard after picking Glissando keys there; same as the bar's GOTCHA)
        self.win.geometry(spot)
        self.win.update_idletasks()
        if self.drawing:
            self.catch.geometry(spot)
            self.catch.update_idletasks()

    def _tick(self):
        self._job = self.root.after(TICK_MS, self._tick)
        self._clock += TICK_MS
        try:
            self._follow()
        except tk.TclError:
            self.stop()

    def _follow(self):
        if self.domino is None or self._clock - self._found_at >= FIND_MS and self.pid is None:
            self._found_at = self._clock
            d, why = front_domino(self.pid)
            if d is None:
                if self.domino is not None:
                    self.domino.close()
                self.domino = None
                return self._hide(why)
            if self.domino is None or d.pid != self.domino.pid:
                if self.domino is not None and self.may_switch and not self.may_switch(self.domino, d):
                    self._found_at = self._clock - FIND_MS + 100  # (asked again soon)
                else:
                    self.domino = d
        v, why = dv.view(self.domino)
        if v is None:
            if why == "busy":  # Domino mid-change: keep what's shown, the next tick reads again
                return
            if why == "closed":
                self.domino.close()
                self.domino = None
            return self._hide(why)
        self._set_state(None)
        if v != self._last:
            first = self._last is None
            self._last = self.view = v
            self._place()
            if first:
                self.win.deiconify()
                self._style(self.hwnd, click_through=True)
                if self.drawing:
                    self.catch.deiconify()
                    self._style(self.catch_hwnd, click_through=False)
            if self.on_view:
                self.on_view(v)
        self._keep_above()
        if self.on_tick:
            self.on_tick()

    def stop(self):
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self.domino is not None:
            self.domino.close()
            self.domino = None
        for w in (self.win, self.catch):
            try:
                w.destroy()
            except tk.TclError:
                pass
