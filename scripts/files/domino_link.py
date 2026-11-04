"""Domino companion: notes into Domino and Domino's selection out, through Domino's own clipboard commands.

Windows only. Never writes into Domino's memory: Domino's menu commands (Paste, Copy, Delete, Undo...) are
posted to its window, the same as picking them in its menus; its cursor (where it pastes) is set by a click on
its ruler, posted to its piano roll window (the real mouse never moves); the notes travel on the clipboard the
way Copy to Domino puts them. Where the cursor is / the song's PPQ: files/domino_view.py song_place.
A clipboard watcher (clipboard managers) may hold the clipboard for a moment right after it changes: Domino then
says "Failed to open clipboard", so the paste waits until the clipboard is free and that box is answered + the
paste tried once more."""

import ctypes
import os
import time

from files import clipboard, domino_clip as dc, domino_view as dv, system

# Domino 1.43's menu commands (read from its menus)
PASTE, COPY, DELETE, UNDO, REDO, PREV_UNIT, DESELECT = 57637, 57634, 32795, 57643, 57644, 33326, 32797
WM_COMMAND = 0x0111
BOX_CLASS = "#32770"


def _u():
    from files.system import wintypes as W
    u = ctypes.windll.user32
    if not getattr(_u, "done", False):
        u.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT,
                                          ctypes.POINTER(ctypes.c_size_t)]
        u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.ScreenToClient.argtypes = [W.HWND, ctypes.POINTER(W.POINT)]
        u.GetForegroundWindow.restype = W.HWND
        u.OpenClipboard.argtypes = [W.HWND]
        _u.done = True
    return u


def command(d, cmd, wait=True):
    """Domino's menu command cmd. wait: done when this returns (never for one that may show a message box)."""
    if not system.WINDOWS:
        return False
    u = _u()
    if not wait:
        return bool(u.PostMessageW(d.top, WM_COMMAND, cmd, 0))
    res = ctypes.c_size_t()
    return bool(u.SendMessageTimeoutW(d.top, WM_COMMAND, cmd, 0, 0x2, 2000, ctypes.byref(res)))  # SMTO_ABORTIFHUNG


def _mouse(hwnd, msg, x, y, keys):
    from files.system import wintypes as W
    u = _u()
    pt = W.POINT(int(round(x)), int(round(y)))
    u.ScreenToClient(hwnd, ctypes.byref(pt))
    res = ctypes.c_size_t()
    u.SendMessageTimeoutW(hwnd, msg, keys, ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF), 0x2, 1000, ctypes.byref(res))


def ruler_click(d, v, beat):
    """A click on Domino's ruler above beat (ViewState v): Domino's cursor goes there (to its own grid).
    DESELECTS FIRST: a click sent while anything is selected CLOSES DOMINO (measured 2026-11-05 on 1.43, at any
    spot, Select / pen tool; a real click doesn't; after Deselect 36 of 36 clicks were fine: dev/scratch/
    de_crash.py, de_safe.py)."""
    g = d.grid
    if not g:
        return
    if not command(d, DESELECT):
        return
    x, y = v.x_of(beat), v.grid[1] - d.build.ruler_h * v.scale / 2
    for msg, keys in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):  # move, left down, left up
        _mouse(g[0], msg, x, y, keys)


def put_cursor(d, tick):
    """Domino's cursor to tick or the nearest place before it Domino's grid allows -> where it is (ticks), or
    None. Only a click on the ruler when tick is in view (the view stays); stepped back by Domino's own grid unit
    when it landed after."""
    v, _ = dv.view(d)
    got = dv.song_place(d)
    if v is None or got is None:
        return None
    ppq = got[1]
    beat = tick / ppq
    left, right = v.left_beat, v.beat_of(v.grid[2])
    if left <= beat < right:
        ruler_click(d, v, beat)
    elif beat >= left:  # (past the right edge: the last place in view, before tick)
        ruler_click(d, v, max(left, right - 0.001))
    else:
        ruler_click(d, v, left)
    for _ in range(256):
        got = dv.song_place(d)
        if got is None:
            return None
        if got[0] <= tick:
            return got[0]
        before = got[0]
        command(d, PREV_UNIT)
        if (dv.song_place(d) or (before,))[0] >= before:
            return None  # (didn't move: nothing more to step back)
    return None


def boxes(d):
    """Domino's message boxes open now (window handles)."""
    if not system.WINDOWS:
        return set()
    from files.system import wintypes as W
    u = _u()
    found = set()
    cb = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(
        lambda h, _l: (found.add(h) if u.IsWindowVisible(h) and dv._pid(h) == d.pid and dv._class(h) == BOX_CLASS
                       else None) or True)
    u.EnumWindows(cb, 0)
    return found


def answer(box):
    """Press a Domino message box's first button (its OK)."""
    u = _u()
    for c in dv._children(box):
        if dv._class(c) == "Button":
            res = ctypes.c_size_t()
            u.SendMessageTimeoutW(c, 0x00F5, 0, 0, 0x2, 1000, ctypes.byref(res))  # BM_CLICK
            return


def clipboard_free():
    if not system.WINDOWS:
        return True
    u = _u()
    if u.OpenClipboard(None):
        u.CloseClipboard()
        return True
    return False


def in_front(d):
    """True when Domino (or one of its windows) has the keyboard: the user works in Domino."""
    return system.WINDOWS and dv._pid(_u().GetForegroundWindow()) == d.pid


def front_window():
    return _u().GetForegroundWindow() if system.WINDOWS else None


def give_back(d, was):
    """was (front_window() before something was sent to Domino) was ours and Domino took the keyboard since: back
    to was (Ctrl+Z / Esc keep going to Spiderweb). Windows lets a program in front hand it on: done from Domino's
    input queue (AttachThreadInput), no key pressed."""
    if not system.WINDOWS or not was or dv._pid(was) != os.getpid() or not in_front(d):
        return
    u, k = _u(), ctypes.windll.kernel32
    fg = u.GetForegroundWindow()
    theirs = u.GetWindowThreadProcessId(fg, None)
    mine = k.GetCurrentThreadId()
    if theirs and theirs != mine and u.AttachThreadInput(mine, theirs, True):
        try:
            u.SetForegroundWindow(was)
        finally:
            u.AttachThreadInput(mine, theirs, False)


class Paste:
    """Put raw (Domino clipboard bytes) on the clipboard and paste it in Domino at its cursor; done(ok, why) when
    over (why: "" / "clipboard" (ours couldn't be used) / "domino" (Domino couldn't open it, twice)). Runs on the
    Tk loop of widget (no waiting in the window)."""

    SETTLE_MS = 120  # (clipboard watchers look at a new copy right away)
    CHECK_MS = 350   # (a "Failed to open clipboard" box shows within this)

    def __init__(self, widget, d, raw, done):
        self.w, self.d, self.done, self.tries = widget, d, done, 0
        self.started = time.monotonic()
        if not dc.put_on_clipboard(raw):
            widget.after_idle(done, False, "clipboard")
            return
        widget.after(self.SETTLE_MS, self.free)

    def free(self):
        if not clipboard_free() and time.monotonic() - self.started < 1.5:
            return self.w.after(30, self.free)
        self.before = boxes(self.d)
        command(self.d, PASTE, wait=False)
        self.w.after(self.CHECK_MS, self.check)

    def check(self):
        new = boxes(self.d) - self.before
        if not new:
            return self.done(True, "")
        for b in new:
            answer(b)
        self.tries += 1
        if self.tries >= 2:
            return self.done(False, "domino")
        self.started = time.monotonic()
        self.w.after(250, self.free)


class Copy:
    """Domino's Copy of its selection -> done(rows, ppq, start) (read_notes rows, ticks from the copied stretch's
    start; start = that stretch's tick in the song) or done(None, why, None): "nothing" (nothing selected / copied),
    "domino" (Domino couldn't use the clipboard), "read" (not readable)."""

    def __init__(self, widget, d, done, wait_s=1.5):
        self.w, self.d, self.done = widget, d, done
        self.count = clipboard.copy_count()
        self.before = boxes(d)
        self.until = time.monotonic() + wait_s
        command(d, COPY, wait=False)
        widget.after(40, self.check)

    def check(self):
        new = boxes(self.d) - self.before
        if new:
            for b in new:
                answer(b)
            return self.done(None, "domino", None)
        if clipboard.copy_count() == self.count:
            if time.monotonic() < self.until:
                return self.w.after(40, self.check)
            return self.done(None, "nothing", None)
        raw = dc.get_from_clipboard()
        place = dv.song_place(self.d)
        try:
            rows, ppq = dc.read_notes(raw) if raw else (None, None)
        except ValueError:
            return self.done(None, "read", None)
        if rows is None or not len(rows) or place is None:
            return self.done(None, "nothing" if place is not None else "read", None)
        self.done(rows, ppq or place[1], place[0])
