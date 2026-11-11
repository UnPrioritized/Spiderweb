"""Draw on Domino: the boxes the user drags in Domino's piano roll, watched (PLAN.md stage B, user 2026-11-12 picked
"watch your drags"; Domino's box rule: files/domino_boxes.py). Kept while Domino's selection is exactly what they
select (DECIDED 2026-11-12 (user): dropped like Domino's selection: a plain click / drag elsewhere, another track,
an edit, the selection changed any other way). Stage D's onion Take reads them (Companion.take_boxes); without
drawn boxes it falls back to groups of the selected notes.

Only while Domino's own mouse is picked on the bar (drawing = the clicks are Spiderweb's): presses / lets go come
from files/mouse_watch.py (place + Ctrl / Shift as they were then), turned into ticks + keys with Domino's view AT
THAT MOMENT (kept for a little while: Domino scrolls during a drag past its edge). A drag is judged once Domino has
answered it (LET_WAIT_S): the box worked out the way Domino does, kept only when the current track's selected
notes are exactly the boxes' notes (a drag that moved notes, a pen stroke, a box Domino saw otherwise = dropped)."""

import collections
import ctypes
import time

from files import domino_boxes as dbx
from files import domino_link as dl
from files import domino_view as dv
from files import mouse_watch, system

LET_WAIT_S = 0.15     # after a let go, Domino has made its selection by then (read once the button is up)
VIEWS_KEPT = 64       # Domino's views kept with their time (a press is read with the view it was made on)
CHECK_S = 0.25        # kept boxes checked against Domino's selection this often (longer on dense tracks)


def _ms():
    return ctypes.windll.kernel32.GetTickCount() if system.WINDOWS else int(time.monotonic() * 1000)


def _drag_px():
    """Windows' drag distance (a move shorter than this is a click)."""
    if not system.WINDOWS:
        return 4, 4
    u = ctypes.windll.user32
    return max(1, u.GetSystemMetrics(68)), max(1, u.GetSystemMetrics(69))  # SM_CXDRAG, SM_CYDRAG


class BoxWatch:
    def __init__(self, comp):
        self.comp = comp
        self.boxes = dbx.Boxes()
        self.watch = mouse_watch.Watch()
        self.views = collections.deque(maxlen=VIEWS_KEPT)  # (GetTickCount ms, ViewState)
        self.press = None      # (ms, x, y, adds, view) of the press in Domino's grid going on
        self.let = None        # (press, (ms, x, y, view), moved, when judged)
        self._check_at = 0.0
        self._every = CHECK_S
        self.last = None       # (tests: the last drag judged: (box, how))

    # --- on / off with Domino's mouse ---
    def sync(self, domino_mouse):
        """Watching only while Domino's own mouse is picked (raw mouse input wakes Python on every move: never while
        drawing, when Spiderweb is busy making notes)."""
        if domino_mouse and not self.watch.on:
            self.watch = mouse_watch.Watch()
            self.watch.start()
        elif not domino_mouse and self.watch.on:
            self.watch.stop()
            self.press = None

    def stop(self):
        self.watch.stop()

    # --- each tick ---
    def tick(self):
        c = self.comp
        d = c.overlay.domino
        v = c.overlay.view
        if v is not None and (not self.views or self.views[-1][1] != v):
            self.views.append((_ms(), v))
        for e in self.watch.take() if self.watch.on else ():
            self.event(d, *e)
        if d is None:
            return
        if self.let is not None:
            if time.monotonic() >= self.let[3] and not dl.mouse_down() and not c.busy:
                let, self.let = self.let, None
                self.judge(d, *let[:3])
        elif self.boxes.list and time.monotonic() - self._check_at >= self._every and not dl.mouse_down():
            self.check(d)

    def view_at(self, ms):
        """Domino's view as it was at GetTickCount ms (the newest one before, else the oldest kept)."""
        best = None
        for t, v in self.views:
            if (ms - t) & 0xFFFFFFFF < 0x80000000:  # (t at or before ms: GetTickCount wraps every 49 days)
                best = v
        return best or (self.views[0][1] if self.views else self.comp.overlay.view)

    def on_domino(self, d, x, y):
        """Is screen x, y on Domino's piano roll grid itself (not the bar, another window over it, drawing)?"""
        if self.comp.drawing() or not system.WINDOWS:
            return False
        u = _user32()
        h = u.WindowFromPoint(system.wintypes.POINT(int(x), int(y)))
        root = u.GetAncestor(h, 2) if h else None  # GA_ROOT
        if not root:
            return False
        o = self.comp.overlay
        if root in (getattr(o, "hwnd", None), getattr(o, "catch_hwnd", None)):
            return True  # (Domino's mouse: the overlay lets it through to Domino)
        return dv._pid(root) == d.pid

    def event(self, d, pressed, x, y, ms, ctrl, shift):
        if pressed:
            self.press = None
            v = self.view_at(ms)
            if d is None or v is None:
                return
            g = v.grid
            if not (g[0] <= x < g[2] and g[1] <= y < g[3]) or not self.on_domino(d, x, y):
                return
            self.press = (ms, x, y, ctrl or shift, v)
        elif self.press is not None:
            p, self.press = self.press, None
            dx, dy = _drag_px()
            moved = abs(x - p[1]) >= dx or abs(y - p[2]) >= dy
            self.let = (p, (ms, x, y, self.view_at(ms)), moved, time.monotonic() + LET_WAIT_S)

    # --- what a drag did ---
    def state(self, d):
        """(notes of the current track, (pid, track, its object), undo count, PPQ) as Domino has them, or None."""
        got = dv.song_tracks(d)
        place = dv.song_place(d)
        if got is None or place is None or got[0] < 1:
            return None
        cur = got[0]
        ids = dv.track_ids(d) or []
        notes = dv.track_notes(d, cur)
        if notes is None:
            return None
        return notes, (d.pid, cur, ids[cur] if cur < len(ids) else None), dv.undo_depth(d), place[1]

    def judge(self, d, p, let, moved):
        """A press + let go in Domino's grid: a box (kept when Domino selected exactly its notes), a click (a plain
        click on an empty spot selects nothing: boxes dropped), or something else (moved notes, a pen stroke)."""
        got = self.state(d)
        if got is None:
            return self.boxes.clear("not readable")
        notes, track, depth, ppq = got
        ms, x0, y0, adds, v0 = p
        v1 = let[3] or v0
        keys = v0.keys
        t0 = v0.beat_of(x0) * ppq
        k0 = min(keys - 1, max(0, v0.key_of(y0)))
        if not moved:
            if adds:  # (Ctrl / Shift + click: Domino's selection stays)
                return self.check(d, got)
            if dbx.under(notes, t0, k0):  # (a note clicked: what Domino selects is checked)
                return self.check(d, got)
            self.last = (None, "click")
            return self.boxes.clear("click")
        if dv.select_tool(d) is False:
            self.last = (None, "not select tool")
            return self.boxes.clear("not the Select tool")
        unit = dv.quantize_item(d)
        grid = dbx.Grid(ppq, dbx.UNITS[unit] if unit is not None and unit < len(dbx.UNITS) else None,
                        dv.song_sigs(d) or ())
        t1 = v1.beat_of(let[1]) * ppq
        k1 = min(keys - 1, max(0, v1.key_of(let[2])))
        b = dbx.box(grid, t0, t1, k0, k1, keys)
        self.boxes.offer(b, adds, notes, track, depth)
        self.last = (b, self.boxes.how)
        self._check_at = time.monotonic()

    def check(self, d, got=None):
        """The kept boxes still Domino's selection? (Read every CHECK_S, longer on dense tracks.)"""
        t = time.perf_counter()
        got = got or self.state(d)
        if got is None:
            return self.boxes.clear("not readable")
        notes, track, depth, _ = got
        self.boxes.still(notes, track, depth)
        self._check_at = time.monotonic()
        self._every = max(CHECK_S, 8 * (time.perf_counter() - t))

    # --- for a Take (stage D) ---
    def for_take(self, d):
        """(boxes, how) for a Take now: the drawn boxes ("drawn") while still Domino's selection, else groups of the
        current track's selected notes ("groups"), or ([], "") with nothing selected / unreadable."""
        got = self.state(d)
        if got is None:
            return [], ""
        notes, track, depth, ppq = got
        if self.boxes.list and self.boxes.still(notes, track, depth):
            return list(self.boxes.list), "drawn"
        sel = notes[notes[:, 4] != 0]
        if not len(sel):
            return [], ""
        unit = dv.quantize_item(d)
        step = dbx.UNITS[unit] if unit is not None and unit < len(dbx.UNITS) else None
        gap = int(ppq * (step if isinstance(step, (int, float)) else 1))
        return dbx.groups(sel, gap), "groups"


_u32 = None


def _user32():
    """An own copy of user32 (its argument types never touch other code's)."""
    global _u32
    if _u32 is None:
        W = system.wintypes
        u = ctypes.WinDLL("user32")
        u.WindowFromPoint.restype = ctypes.c_void_p
        u.WindowFromPoint.argtypes = [W.POINT]
        u.GetAncestor.restype = ctypes.c_void_p
        u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        _u32 = u
    return _u32
