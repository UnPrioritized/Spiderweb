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
SELECT_ALL = 57642     # (the current track's events)
SWAP_PASTE = 33318     # "Substitute Paste": the current track's events in the pasted stretch are replaced, CC too
ADD_TRACK, REMOVE_TRACK = 32902, 32903  # (a new track goes right after the current one, on the lowest free channel)
PREV_TRACK, NEXT_TRACK, TRACK_0 = 32905, 32906, 32907  # (TRACK_0 + n = track n, n up to MENU_TRACKS)
MENU_TRACKS = 33
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


def alive(d):
    """Domino's window is still there (closed / crashed: False)."""
    return bool(system.WINDOWS and d is not None and _u().IsWindow(d.top))


def busy(d):
    """Domino can't be sent anything safely right now (hunt 2026-11-06: a send landing mid-action could select notes
    before the ruler click = Domino closes): a mouse button held (the user drags / clicks), a window of Domino's
    open over it (its main window disabled: a dialog / message box), one of its menus open, or it doesn't answer."""
    if not system.WINDOWS:
        return False
    u = _u()
    if mouse_down() or not u.IsWindowEnabled(d.top):
        return True
    if menu_open(d):
        return True
    res = ctypes.c_size_t()
    return not u.SendMessageTimeoutW(d.top, 0, 0, 0, 0x2, 300, ctypes.byref(res))  # WM_NULL: answers?


def menu_open(d):
    """One of Domino's menus (menu bar / right-click) is open."""
    from files.system import wintypes as W
    u = _u()
    found = []
    cb = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(
        lambda h, _l: (found.append(h) if u.IsWindowVisible(h) and dv._class(h) == "#32768" and dv._pid(h) == d.pid
                       else None) or not found)
    u.EnumWindows(cb, 0)
    return bool(found)


def when_free(widget, d, run, gone, give_up_s=20.0):
    """run() once Domino can be sent things (busy() False); gone(why) when it closed ("domino") or stayed busy past
    give_up_s ("busy"). Waits on widget's Tk loop."""
    end = time.monotonic() + give_up_s

    def check():
        if not alive(d):
            return gone("domino")
        if busy(d):
            if time.monotonic() > end:
                return gone("busy")
            return widget.after(40, check)
        run()
    check()


def ruler_click(d, v, beat):
    """A click on Domino's ruler above beat (ViewState v): Domino's cursor goes there (to its own grid). False =
    not clicked (Domino busy / the user pressed a button meanwhile: tried again later).
    DESELECTS FIRST: a click sent while anything is selected CLOSES DOMINO (measured 2026-11-05 on 1.43, at any
    spot, Select / pen tool; a real click doesn't; after Deselect 36 of 36 clicks were fine: dev/scratch/
    de_crash.py, de_safe.py)."""
    g = d.grid
    if not g or busy(d):
        return False
    if not command(d, DESELECT):
        return False
    if mouse_down():  # (pressed between the two: a select box may be on its way, hunt 2026-11-06)
        return False
    x, y = v.x_of(beat), v.grid[1] - d.build.ruler_h * v.scale / 2
    for msg, keys in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):  # move, left down, left up
        _mouse(g[0], msg, x, y, keys)
    return True


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
        clicked = ruler_click(d, v, beat)
    elif beat >= left:  # (past the right edge: the last place in view, before tick)
        clicked = ruler_click(d, v, max(left, right - 0.001))
    else:
        clicked = ruler_click(d, v, left)
    if not clicked:
        return None
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


def switch_track(d, n):
    """Domino's current track -> track n (its own track commands). True when it is there."""
    got = dv.song_tracks(d)
    if got is None or not 0 <= n < len(got[1]):
        return False
    if n <= MENU_TRACKS:
        command(d, TRACK_0 + n)
    for _ in range(len(got[1]) + 1):
        got = dv.song_tracks(d)
        if got is None:
            return False
        if got[0] == n:
            return True
        before = got[0]
        command(d, NEXT_TRACK if got[0] < n else PREV_TRACK)
        if (dv.song_tracks(d) or (before,))[0] == before:
            return False
    return False


def add_tracks(d, count, use10):
    """count new tracks at the bottom of Domino's track list, made by Domino's own Add Track (the lowest channel no
    track uses; channel 10 skipped when use10 is off: one more is added and that one removed again, still empty).
    The current track stays. -> how many Domino steps (undo steps) that took, or None when it couldn't."""
    got = dv.song_tracks(d)
    if got is None:
        return None
    back, chans = got[0], got[1]
    last = len(chans) - 1
    if not switch_track(d, last):
        return None
    steps, made, drums = 0, 0, 0
    while made < count:
        n = len(chans)
        command(d, ADD_TRACK)
        got = dv.song_tracks(d)
        if got is None or len(got[1]) != n + 1:
            break
        steps += 1
        chans = got[1]
        new = len(chans) - 1  # (made right after the current one = the last: the next one goes after it, in order)
        if not switch_track(d, new):
            break
        if chans[new] == 9 and not use10 and drums < 8:
            drums += 1  # (removed after the others: while it's there, Domino gives the next one another channel;
            # one a port: past port A's 16 channels Domino goes on to port B, its channel 10 too)
        else:
            made += 1
    for at in [i for i in range(len(chans) - 1, last, -1) if chans[i] == 9][:drums] if drums else ():
        if switch_track(d, at):  # (from the bottom up: the ones above keep their place)
            command(d, REMOVE_TRACK)  # (an empty track: Domino asks nothing)
            steps += 1
    switch_track(d, back)
    return steps if made == count else None


def merge_single(there, new, way="stretch"):
    """Single channel against the notes already in the Domino track (user 2026-11-06): there = its notes (tick, gate,
    key, velocity, ...), new = the notes going in (tick, end, key, velocity, ...) -> (rows, changed, added), all
    (tick, end, key, velocity): rows = every note after Spiderweb's Single channel rule (resolve_overlaps over both;
    way "split": split around, the "Split around instead of stretching" tick, hunt 2026-11-06), changed = notes of
    there no longer as they were (cut, or merged into a note on the same tick), added = rows not in Domino as they
    are (what goes in when nothing there changed). Rows sorted by tick, then key."""
    from notes.engine import resolve_overlaps, split_overlaps
    import numpy as np
    there = np.asarray(there, np.int64)
    there = there.reshape(0, 4) if not there.size else there
    new = np.asarray(new, np.int64)
    new = new.reshape(0, 4) if not new.size else new
    old = np.zeros((len(there), 6), np.int64)
    if len(there):
        old[:, 0], old[:, 1], old[:, 2], old[:, 3] = there[:, 0], there[:, 0] + there[:, 1], there[:, 2], there[:, 3]
    mine = np.zeros((len(new), 6), np.int64)
    mine[:, :4] = new[:, :4]
    old[:, 5], mine[:, 5] = 0, 1
    # only Domino's notes that can be touched go through the rule (a dense track: seconds otherwise): those in a run
    # of touching notes on one key (new ones counted) that holds a new note
    near = np.zeros(len(old), bool)
    if len(old) and len(mine):
        on = np.flatnonzero(np.isin(old[:, 2], np.unique(mine[:, 2])))
        both = np.concatenate([old[on], mine])
        src = np.concatenate([on, np.full(len(mine), -1)])
        shift = both[:, 2] << 34  # (keys apart: ticks stay under 2^33)
        s, e = both[:, 0] + shift, both[:, 1] + shift
        order = np.lexsort((s,))
        s, e, src = s[order], e[order], src[order]
        reach = np.maximum.accumulate(e)
        run = np.cumsum(np.concatenate([[1], s[1:] > reach[:-1]]))  # (a new run where a note starts after all before)
        hit = np.isin(run, run[src < 0])
        near[src[hit & (src >= 0)]] = True
    part = old[near]
    fixed = (split_overlaps if way == "split" else resolve_overlaps)(np.concatenate([part, mine]))[:, :4]

    def view(a):
        return np.ascontiguousarray(a[:, :4]).view([("", np.int64)] * 4).ravel()
    changed = part[~np.isin(view(part), view(fixed)), :4] if len(part) else part[:, :4]
    added = fixed[~np.isin(view(fixed), view(part))] if len(part) else fixed
    rows = np.concatenate([old[~near, :4], fixed])
    rows = rows[np.lexsort((rows[:, 2], rows[:, 0]))]
    added = added[np.lexsort((added[:, 2], added[:, 0]))]
    return rows, changed, added


def merge_across(there, new, overlaps):
    """Multi channel's Remove overlaps against the notes already in Domino's tracks (user 2026-11-06, "for
    consistency": like merge_single, a track at a time). there = {k: the k-th target track's notes (tick, gate, key,
    velocity, ...)}, new = the notes going in (tick, end, key, velocity, k), overlaps = App.overlap_rule's. Track k
    = the k-th from the current one down: the earlier one in Domino's list = lower k. -> ({k: (rows, changed,
    added)} as merge_single's, the new notes as they go in (tick, end, key, velocity, k)). Only Domino's notes in a
    run of touching notes on one key (every track counted) that holds a new note go through the rule: Domino's own
    overlaps elsewhere stay."""
    from notes.engine import remove_across
    import numpy as np
    new = np.asarray(new, np.int64).reshape(-1, 5)
    parts, mine = [], np.zeros((len(new), 6), np.int64)
    mine[:, :5], mine[:, 5] = new, -1
    for k, a in there.items():
        a = np.asarray(a, np.int64)
        a = a.reshape(0, 4) if not a.size else a
        o = np.zeros((len(a), 6), np.int64)
        if len(a):
            o[:, 0], o[:, 1], o[:, 2], o[:, 3] = a[:, 0], a[:, 0] + a[:, 1], a[:, 2], a[:, 3]
        o[:, 4], o[:, 5] = k, np.arange(len(a))
        parts.append(o)
    old = np.concatenate(parts) if parts else np.zeros((0, 6), np.int64)
    near = np.zeros(len(old), bool)
    if len(old) and len(mine):
        on = np.flatnonzero(np.isin(old[:, 2], np.unique(mine[:, 2])))
        both = np.concatenate([old[on], mine])
        src = np.concatenate([on, np.full(len(mine), -1)])
        shift = both[:, 2] << 34  # (keys apart: ticks stay under 2^33)
        s, e = both[:, 0] + shift, both[:, 1] + shift
        order = np.lexsort((s,))
        s, e, src = s[order], e[order], src[order]
        reach = np.maximum.accumulate(e)
        run = np.cumsum(np.concatenate([[1], s[1:] > reach[:-1]]))
        hit = np.isin(run, run[src < 0])
        near[src[hit & (src >= 0)]] = True
    part = old[near]
    fixed = remove_across(np.concatenate([part, mine]), np.zeros(len(part) + len(mine), bool), overlaps,
                          lambda n: n[:, 4])

    def view(a):
        return np.ascontiguousarray(a[:, :5]).view([("", np.int64)] * 5).ravel()
    out = {}
    for k in there:
        p, f = part[part[:, 4] == k], fixed[fixed[:, 4] == k]
        changed = p[~np.isin(view(p), view(f)), :4] if len(p) else p[:, :4]
        added = f[~np.isin(view(f), view(p)), :4] if len(p) else f[:, :4]
        rows = np.concatenate([old[~near & (old[:, 4] == k), :4], f[:, :4]])
        rows = rows[np.lexsort((rows[:, 2], rows[:, 0]))]
        out[k] = rows, changed, added[np.lexsort((added[:, 2], added[:, 0]))]
    went = fixed[fixed[:, 5] < 0, :5]
    return out, went[np.lexsort((went[:, 2], went[:, 0]))]


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


def to_domino(d):
    """The keyboard to Domino when Spiderweb has it (never taken from another program): Domino's own keys (Space,
    Ctrl+Z ...) work again, user 2026-11-06. True when Domino has it now."""
    if not system.WINDOWS:
        return False
    if in_front(d):
        return True
    u, k = _u(), ctypes.windll.kernel32
    fg = u.GetForegroundWindow()
    if not fg or dv._pid(fg) != os.getpid():
        return False
    theirs = u.GetWindowThreadProcessId(fg, None)
    mine = k.GetCurrentThreadId()
    attached = theirs and theirs != mine and u.AttachThreadInput(mine, theirs, True)
    try:
        u.SetForegroundWindow(d.top)
    finally:
        if attached:
            u.AttachThreadInput(mine, theirs, False)
    return in_front(d)


EXTENDED = set(range(0x21, 0x29)) | {0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90}  # (arrows, Home, Insert, Delete...)


def pass_key(d, vk, state):
    """A key pressed in Spiderweb that's Domino's (vk = Windows key code, state = Tk's modifier bits): the keyboard
    to Domino, then that key pressed again there, with the Shift / Ctrl / Alt it was pressed with. A modifier still
    held for real is left alone; one let go already is pressed for the key and let go again after it (hunt
    2026-11-06: a key passed on late left Ctrl held down in Windows)."""
    if not system.WINDOWS or not vk or not to_domino(d):
        return False
    u = _u()
    mods = [m for bit, m in ((0x1, 0x10), (0x4, 0x11), (0x20000, 0x12)) if state & bit]
    pressed = [m for m in mods if not u.GetAsyncKeyState(m) & 0x8000]
    for m in pressed:
        u.keybd_event(m, 0, 0, 0)
    ext = 0x1 if vk in EXTENDED else 0
    u.keybd_event(vk, 0, ext, 0)
    u.keybd_event(vk, 0, ext | 0x2, 0)  # KEYEVENTF_KEYUP
    for m in reversed(pressed):
        u.keybd_event(m, 0, 0x2, 0)
    return True


def mouse_down():
    """A mouse button is down right now (anywhere on the screen)."""
    if not system.WINDOWS:
        return False
    u = _u()
    return any(u.GetAsyncKeyState(b) & 0x8000 for b in (0x01, 0x02, 0x04))


class Paste:
    """Put raw (Domino clipboard bytes) on the clipboard and paste it in Domino at its cursor; done(ok, why) when
    over (why: "" / "clipboard" (ours couldn't be used) / "domino" (Domino couldn't open it, twice)). Runs on the
    Tk loop of widget (no waiting in the window)."""

    SETTLE_MS = 120  # (clipboard watchers look at a new copy right away)
    CHECK_MS = 350   # (a "Failed to open clipboard" box shows within this)

    def __init__(self, widget, d, raw, done, cmd=PASTE):
        self.w, self.d, self.done, self.tries, self.cmd = widget, d, done, 0, cmd
        self.started = time.monotonic()
        if not dc.put_on_clipboard(raw):
            widget.after_idle(done, False, "clipboard")
            return
        widget.after(self.SETTLE_MS, self.free)

    def free(self):
        if not alive(self.d):
            return self.done(False, "domino")
        if not clipboard_free() and time.monotonic() - self.started < 1.5:
            return self.w.after(30, self.free)
        if busy(self.d):  # (the user holds the mouse / a menu or window of Domino's is open: wait, hunt 11-06)
            if time.monotonic() - self.started > 20:
                return self.done(False, "busy")
            return self.w.after(40, self.free)
        self.before = boxes(self.d)
        command(self.d, self.cmd, wait=False)
        self.w.after(self.CHECK_MS, self.check)

    def check(self):
        if not alive(self.d):  # (closed / crashed meanwhile: nothing landed, the shapes stay, hunt 2026-11-06)
            return self.done(False, "domino")
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

    def __init__(self, widget, d, done, wait_s=1.5, whole=False, passengers=None):
        """whole: the current track's every event (Select All first, Deselect after); passengers = a list: the
        notes' other events added (read_notes)."""
        self.w, self.d, self.done, self.whole, self.passengers = widget, d, done, whole, passengers
        self.wait_s = wait_s
        when_free(widget, d, self.start, lambda why: done(None, why, None))  # (hunt 2026-11-06: never mid-action)

    def start(self):
        d = self.d
        self.count = clipboard.copy_count()
        self.before = boxes(d)
        self.late = False
        if self.whole:  # (sent, not posted: back when Domino is done copying, so an empty track needs no long wait)
            command(d, SELECT_ALL)
            done = command(d, COPY)
            command(d, DESELECT)  # (at once: Select All held while Domino has the keyboard = a key acts on it all)
            # still copying past the 2 s wait (a dense track): its copy may land late, ON TOP OF what's put on the
            # clipboard next: waited for (long), never taken as "nothing" (hunt 2026-11-06)
            self.late = not done
            self.wait_s = 30.0 if self.late else 0.25
        else:
            command(d, COPY, wait=False)
        self.until = time.monotonic() + self.wait_s
        self.w.after(40, self.check)

    def check(self):
        if not alive(self.d):
            return self.done(None, "domino", None)
        new = boxes(self.d) - self.before
        if self.late and not new and clipboard.copy_count() == self.count and time.monotonic() < self.until:
            res = ctypes.c_size_t()  # (Domino answering again with nothing on the clipboard: the copy is over)
            if _u().SendMessageTimeoutW(self.d.top, 0, 0, 0, 0x2, 100, ctypes.byref(res)):
                self.late, self.until = False, time.monotonic() + 0.25
            return self.w.after(40, self.check)
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
            rows, ppq = dc.read_notes(raw, top=255, passengers=self.passengers) if raw else (None, None)  # (the
            # 256 keys mod's keys too; any other Domino has none above 127)
        except ValueError:
            return self.done(None, "read", None)
        if rows is None or not len(rows) or place is None:
            return self.done(None, "nothing" if place is not None else "read", None)
        # (a whole track's copy runs from the song's start, wherever the cursor is: measured 2026-11-06,
        # dev/scratch/di_real.py)
        self.done(rows, ppq or place[1], 0 if self.whole else place[0])
