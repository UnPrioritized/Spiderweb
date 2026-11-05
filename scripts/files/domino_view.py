"""Domino companion: where Domino's piano roll is on the screen and what it shows, read from outside.

Read-only: Spiderweb never writes into Domino. A running Domino is found by its program's path (never by window
title), its program file is checked against BUILDS (an unknown build = no reading, with the reason), then a few
numbers are read out of its memory: how far the piano roll is scrolled, its zoom and row height.

Domino's units (1.43): the horizontal scroll counts 1/24 of a beat (a quarter note); the zoom is pixels per beat;
the vertical scroll counts rows from key 127 down. The note grid sits KEYS_W x RULER_H Domino pixels inside the
grid window (the keyboard on the left, the measure ruler on top). Domino run without "high DPI aware" (Windows'
compatibility setting) is stretched by Windows: its own pixels are then `scale` real pixels.
Windows only: elsewhere every function answers "no Domino".
"""

import ctypes
import hashlib
import os
from dataclasses import dataclass

from files import system
from files.system import wintypes as W


@dataclass(frozen=True)
class Build:
    name: str
    p_slot: int      # fixed spot in Domino.exe -> the piano roll's view object P
    p_vtable: int    # P's first 4 bytes (a check that P really is that object)
    top_row: int     # P + this: rows scrolled from the top (key 127 = row 0)
    row_px: int      # P + this: one row's height in Domino pixels
    beat_px: int     # P + this: Domino pixels per beat (the zoom)
    h_slot: int      # fixed spot -> the horizontal scroll object H
    h_vtable: int
    left: int        # H + this: left edge of the grid, in 1/24 beat
    keys_w: int = 101   # keyboard width (Domino pixels) left of the grid
    ruler_h: int = 17   # measure ruler height above the grid
    c_slot: int = 0     # fixed spot -> the song position object C (0 = not known for this build)
    c_vtable: int = 0
    cursor: int = 0     # C + this: Domino's cursor (where it pastes), in ticks
    song: int = 0       # C + this -> the song object S
    ppq_at: int = 0     # S + this: the song's PPQ
    tracks_at: int = 0  # S + this -> the track list (pointers; an MFC array: + 4 = how many, then its room)
    track_now: int = 0  # S + this: the current track (0 = Conductor)
    track_ch: int = 0   # a track + this: its channel (byte, 0-based)
    events_at: int = 0  # a track + this -> its events (pointers, tick order, every kind; + 4 = how many)
    note_vt: int = 0    # a note event's first 4 bytes (+4 tick, +8 selected byte, +0xC key, +0xD velocity, +0x10 gate)
    undo_at: int = 0    # C + this -> the undo list; + 0x10 there = how many steps it holds


# sha256 of Domino.exe -> Build. Only builds checked by hand: an unknown one is never guessed at.
BUILDS = {
    "ba4d007be174626387a59dd0f127d5fd63d0c67cab2219b71aa087ba6725db9d": Build(
        "Domino 1.43 (with dominohook)", p_slot=0x5B49C8, p_vtable=0x55F490, top_row=0xB8, row_px=0x1CC,
        beat_px=0x1D0, h_slot=0x5B4C8C, h_vtable=0x567F4C, left=0x234,
        c_slot=0x5B49F4, c_vtable=0x55D60C, cursor=0x190, song=0x184, ppq_at=0xC,
        tracks_at=0x14, track_now=0x24, track_ch=0x8, events_at=0x18, note_vt=0x55AB28, undo_at=0x188),
}
UNITS_PER_BEAT = 24


@dataclass(frozen=True)
class ViewState:
    """What Domino's piano roll shows right now. Beats = quarter notes from the song's start; x / y = real
    screen pixels."""
    left_beat: float   # beat at the grid's left edge
    beat_px: float     # real pixels per beat
    top_row: int       # rows hidden above the grid (row 0 = key 127)
    row_px: float      # one row's height in real pixels
    grid: tuple        # (left, top, right, bottom) of the note grid on the screen
    scale: float = 1.0  # real pixels per Domino pixel

    def x_of(self, beat):
        return self.grid[0] + (beat - self.left_beat) * self.beat_px

    def beat_of(self, x):
        return self.left_beat + (x - self.grid[0]) / self.beat_px

    def y_of(self, key):
        """Screen y of the top of a key's row."""
        return self.grid[1] + (127 - key - self.top_row) * self.row_px

    def key_of(self, y):
        """The key whose row holds screen y (outside 0..127 when y is off the keys)."""
        return 127 - self.top_row - int((y - self.grid[1]) // self.row_px)


def fingerprint(path):
    """sha256 of a program file (hex), or None when it can't be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


_known = {}  # (path, size, mtime) -> Build or None


def build_of(path):
    """The Build for a Domino.exe, or None when it's a build we don't know."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(path), st.st_size, st.st_mtime_ns)
    if key not in _known:
        _known[key] = BUILDS.get(fingerprint(path))
    return _known[key]


def view_from(numbers, grid_win, scale):
    """ViewState from the raw numbers (top_row, row_px, beat_px, left units), the grid window's screen rect
    (left, top, right, bottom; its right edge = the scroll bar's left) and the scale, + the build's edges."""
    top_row, row_px, beat_px, left, keys_w, ruler_h = numbers
    x0 = grid_win[0] + keys_w * scale
    y0 = grid_win[1] + ruler_h * scale
    bottom = min(grid_win[3], y0 + (128 - top_row) * row_px * scale)
    return ViewState(left / UNITS_PER_BEAT, beat_px * scale, top_row, row_px * scale,
                     (round(x0), round(y0), grid_win[2], round(bottom)), scale)


def sane(numbers):
    top_row, row_px, beat_px, left = numbers[:4]
    return 0 <= top_row <= 127 and 1 <= row_px <= 200 and 1 <= beat_px <= 10000 and 0 <= left < 2 ** 31


# --- Windows -------------------------------------------------------------------------------------------------

_PROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM) if system.WINDOWS else None


def _api():
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    if not getattr(_api, "done", False):
        k.OpenProcess.restype = W.HANDLE
        k.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
        u.GetWindow.restype = W.HWND
        u.GetParent.restype = W.HWND
        u.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        _api.done = True
    return u, k


def exe_path(pid):
    u, k = _api()
    ph = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not ph:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = W.DWORD(1024)
        return buf.value if k.QueryFullProcessImageNameW(ph, 0, buf, ctypes.byref(n)) else ""
    finally:
        k.CloseHandle(ph)


def _pid(hwnd):
    u, _ = _api()
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _class(hwnd):
    u, _ = _api()
    b = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(hwnd, b, 128)
    return b.value


def _rect(hwnd):
    u, _ = _api()
    r = W.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _children(hwnd):
    u, _ = _api()
    out = []
    c = u.GetWindow(hwnd, 5)  # GW_CHILD
    while c:
        out.append(c)
        c = u.GetWindow(c, 2)  # GW_HWNDNEXT
    return out


@dataclass
class Domino:
    top: int          # its main window
    pid: int
    path: str
    build: object     # Build or None
    _handle: int = 0
    grid: object = None  # (grid window, its scroll bar) last found

    def handle(self):
        if not self._handle:
            _, k = _api()
            self._handle = k.OpenProcess(0x0410, False, self.pid) or 0  # VM_READ | QUERY_INFORMATION
        return self._handle

    def close(self):
        if self._handle:
            _api()[1].CloseHandle(self._handle)
            self._handle = 0


def find_all():
    """Every running Domino with a visible main window (a program named Domino.exe), front-most first."""
    if not system.WINDOWS:
        return []
    u, _ = _api()
    tops = []
    u.EnumWindows(_PROC(lambda h, _l: tops.append(h) or True), 0)
    found = []
    for h in tops:
        if not u.IsWindowVisible(h) or u.GetParent(h) or u.GetWindow(h, 4):  # child / owned windows: skip
            continue
        if u.GetWindowLongW(h, -16) & 0xC00000 != 0xC00000:  # no title bar (Domino's start-up windows)
            continue
        pid = _pid(h)
        path = exe_path(pid)
        if os.path.basename(path).lower() == "domino.exe" and not any(d.pid == pid for d in found):
            found.append(Domino(h, pid, path, build_of(path)))
    return found


def grid_window(top):
    """Domino's note grid window: the biggest visible 'Afx:400000:8:' window with a narrow child (its own
    vertical scroll bar) on its right edge. None when the piano roll isn't shown."""
    u, _ = _api()
    best, area = None, 0
    todo = list(_children(top))
    while todo:
        h = todo.pop()
        if not u.IsWindowVisible(h):
            continue
        kids = _children(h)
        todo.extend(kids)
        if not _class(h).startswith("Afx:400000:8:"):
            continue
        r = _rect(h)
        for c in kids:
            cr = _rect(c)
            if u.IsWindowVisible(c) and cr[2] >= r[2] - 2 and cr[2] - cr[0] < 60 and cr[3] - cr[1] > (r[3] - r[1]) // 2:
                a = (r[2] - r[0]) * (r[3] - r[1])
                if a > area:
                    best, area = (h, c), a
    return best


def scale_of(hwnd):
    """Our pixels per Domino pixel: 1 unless Windows stretches Domino (not high DPI aware, or on a monitor with
    another DPI) differently from us. "Our pixels" = what Windows tells this thread (Spiderweb is system DPI
    aware, the checks in dev/scratch per-monitor aware)."""
    u, _ = _api()
    try:
        own = u.GetDpiForWindow(hwnd) or 96
        ours = u.GetAwarenessFromDpiAwarenessContext(ctypes.c_void_p(u.GetThreadDpiAwarenessContext()))
        if ours == 0:
            return 96 / own
        if ours == 1:
            return u.GetDpiForSystem() / own
        mon = u.MonitorFromWindow(hwnd, 2)
        x, y = ctypes.c_uint(), ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(mon), 0, ctypes.byref(x), ctypes.byref(y)) == 0:
            return x.value / own
    except (OSError, AttributeError):
        pass
    return 1.0


def _i32(ph, addr):
    _, k = _api()
    v = ctypes.c_int32()
    n = ctypes.c_size_t()
    if not k.ReadProcessMemory(ph, ctypes.c_void_p(addr), ctypes.byref(v), 4, ctypes.byref(n)) or n.value != 4:
        return None
    return v.value


def read_numbers(d):
    """(top_row, row_px, beat_px, left units) read from Domino's memory, or None."""
    b, ph = d.build, d.handle()
    if b is None or not ph:
        return None
    p = _i32(ph, b.p_slot)
    h = _i32(ph, b.h_slot)
    if not p or not h:
        return None
    p &= 0xFFFFFFFF
    h &= 0xFFFFFFFF
    if (_i32(ph, p) or 0) & 0xFFFFFFFF != b.p_vtable or (_i32(ph, h) or 0) & 0xFFFFFFFF != b.h_vtable:
        return None
    out = (_i32(ph, p + b.top_row), _i32(ph, p + b.row_px), _i32(ph, p + b.beat_px), _i32(ph, h + b.left))
    return out if None not in out and sane(out) else None


def song_place(d):
    """(cursor tick, PPQ) read from Domino's memory, or None (unknown build / not readable). The cursor is where
    Domino pastes; after a copy it is the copied stretch's start."""
    b, ph = d.build, d.handle()
    if b is None or not b.c_slot or not ph:
        return None
    c = _i32(ph, b.c_slot)
    if not c or (_i32(ph, c & 0xFFFFFFFF) or 0) & 0xFFFFFFFF != b.c_vtable:
        return None
    c &= 0xFFFFFFFF
    s = _i32(ph, c + b.song)
    tick = _i32(ph, c + b.cursor)
    ppq = _i32(ph, (s & 0xFFFFFFFF) + b.ppq_at) if s else None
    if tick is None or ppq is None or not 0 <= tick < 2 ** 31 or not 1 <= ppq <= 65535:
        return None
    return tick, ppq


def _song_obj(d):
    """(process handle, C, S) or None: the song position object and the song, checked."""
    b, ph = d.build, d.handle()
    if b is None or not b.c_slot or not ph:
        return None
    c = _i32(ph, b.c_slot)
    if not c or (_i32(ph, c & 0xFFFFFFFF) or 0) & 0xFFFFFFFF != b.c_vtable:
        return None
    c &= 0xFFFFFFFF
    s = _i32(ph, c + b.song)
    return (ph, c, s & 0xFFFFFFFF) if s else None


def undo_depth(d):
    """How many steps Domino's undo list holds (changes with every edit, undo, redo), or None. Found 2026-11-06
    (dev/scratch/dk_undo.py, dev/data/domino-notes-memory.md)."""
    got = _song_obj(d)
    if got is None or not d.build.undo_at:
        return None
    ph, c, _ = got
    u = _i32(ph, c + d.build.undo_at)
    return _i32(ph, (u & 0xFFFFFFFF) + 0x10) if u else None


def _read(ph, addr, size):
    _, k = _api()
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t()
    if not k.ReadProcessMemory(ph, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return b""
    return buf.raw[:got.value]


def track_notes(d, track):
    """Track's notes as an int64 array (tick, gate, key, velocity, selected 0/1), Domino's order (by tick), read from
    its memory (read-only), or None. 100,000 notes ~0.04 s: the pointer list in one read, then only the memory
    pages the notes sit on (dev/scratch/dk_big.py)."""
    import numpy as np
    got = _song_obj(d)
    b = d.build
    if got is None or not b.events_at:
        return None
    ph, _, s = got
    arr, n = _i32(ph, s + b.tracks_at), _i32(ph, s + b.tracks_at + 4)
    if not arr or n is None or not 0 <= track < n:
        return None
    t = _i32(ph, (arr & 0xFFFFFFFF) + 4 * track)
    if not t:
        return None
    t &= 0xFFFFFFFF
    ev, m = _i32(ph, t + b.events_at), _i32(ph, t + b.events_at + 4)
    if m is None or not 0 <= m <= 50_000_000:
        return None
    if m == 0:
        return np.zeros((0, 5), np.int64)
    raw = _read(ph, ev & 0xFFFFFFFF, 4 * m)
    if len(raw) != 4 * m:
        return None
    ptrs = np.frombuffer(raw, np.uint32).astype(np.int64)
    pages = np.unique(np.concatenate([ptrs >> 12, (ptrs + 0x13) >> 12]))
    blobs = []
    for run in np.split(pages, np.flatnonzero(np.diff(pages) > 1) + 1):
        lo, size = int(run[0]) << 12, len(run) << 12
        data = _read(ph, lo, size)
        if len(data) == size:
            blobs.append((lo, np.frombuffer(data, np.uint8)))
        else:  # (a page that can't be read: page by page, unreadable ones as zeros = not notes)
            for pg in run:
                blobs.append((int(pg) << 12, np.frombuffer(_read(ph, int(pg) << 12, 4096).ljust(4096, b"\0"), np.uint8)))
    los = np.array([lo for lo, _ in blobs], np.int64)
    which = np.searchsorted(los, ptrs, side="right") - 1
    out = np.zeros((m, 6), np.int64)
    for j, (lo, data) in enumerate(blobs):
        sel = np.flatnonzero(which == j)
        off = ptrs[sel] - lo
        off = off[off + 0x14 <= len(data)]
        sel = sel[:len(off)]
        word = lambda o: data[(off + o)[:, None] + np.arange(4)].copy().view(np.uint32).ravel()  # noqa: E731
        out[sel, 0] = word(0)
        out[sel, 1] = word(4).view(np.int32)
        out[sel, 2] = word(0x10).view(np.int32)
        out[sel, 3] = data[off + 0xC]
        out[sel, 4] = data[off + 0xD]
        out[sel, 5] = data[off + 8] != 0
    return out[out[:, 0] == b.note_vt, 1:]


def song_tracks(d):
    """(current track, [each track's channel, 0-based]) read from Domino's memory, or None. Track 0 = Conductor.
    Found 2026-11-06 on 1.43 (dev/scratch/di_probe.py: a MIDI with known channels, Domino's track commands)."""
    b, ph = d.build, d.handle()
    if b is None or not b.tracks_at or not ph:
        return None
    c = _i32(ph, b.c_slot)
    if not c or (_i32(ph, c & 0xFFFFFFFF) or 0) & 0xFFFFFFFF != b.c_vtable:
        return None
    s = _i32(ph, (c & 0xFFFFFFFF) + b.song)
    if not s:
        return None
    s &= 0xFFFFFFFF
    arr, n, now = _i32(ph, s + b.tracks_at), _i32(ph, s + b.tracks_at + 4), _i32(ph, s + b.track_now)
    if not arr or n is None or now is None or not 1 <= n <= 65535 or not 0 <= now < n:
        return None
    _, k = _api()
    ptrs = (ctypes.c_uint32 * n)()
    got = ctypes.c_size_t()
    if not k.ReadProcessMemory(ph, ctypes.c_void_p(arr & 0xFFFFFFFF), ptrs, 4 * n, ctypes.byref(got)) \
            or got.value != 4 * n:
        return None
    chans = []
    one = ctypes.c_uint8()
    for p in ptrs:
        if not p or not k.ReadProcessMemory(ph, ctypes.c_void_p(p + b.track_ch), ctypes.byref(one), 1,
                                            ctypes.byref(got)):
            return None
        chans.append(one.value & 15)
    return now, chans


def view(d, tries=4):
    """(ViewState, None) for a Domino, or (None, reason): "unknown build", "not readable", "hidden"."""
    if not system.WINDOWS:
        return None, "not windows"
    if d.build is None:
        return None, "unknown build"
    u, _ = _api()
    last = None
    for _ in range(tries):  # read until two reads in a row agree (Domino may be mid-update)
        if not u.IsWindow(d.top):
            return None, "closed"
        if u.IsIconic(d.top):
            return None, "hidden"
        g = d.grid
        if not g or not all(u.IsChild(d.top, h) and u.IsWindowVisible(h) for h in g):
            g = d.grid = grid_window(d.top)  # (looked for again only when the old one is gone / hidden)
        if g is None:
            return None, "hidden"
        nums = read_numbers(d)
        if nums is None:
            return None, "not readable"
        win, bar = _rect(g[0]), _rect(g[1])
        now = (nums, (win[0], win[1], bar[0], win[3]), scale_of(d.top))
        if now == last:
            n, rect, scale = now
            return view_from(n + (d.build.keys_w, d.build.ruler_h), rect, scale), None
        last = now
    return None, "busy"
